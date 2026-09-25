"""Reconciliacao antes x depois da referencia de kits (gate KITS-MAP-2).

    python tools/reconcile_kits_map_2.py --out docs/reconciliation/<arquivo>.csv

SOMENTE LEITURA. Abre as duas conexoes com `set_session(readonly=True)`, nao
executa DDL, nao chama publisher e nao liga flag. O unico efeito colateral e'
escrever o CSV de reconciliacao no caminho pedido.

O QUE ELE RECONSTROI DO BANCO, E POR QUE NAO DO CSV
----------------------------------------------------
A ponte e' recomputada de `gold.map_produto_codigo_gobeauty`,
`gold.dim_produto_gobeauty` e `silver.gobeaute_produto_cadastro` — nunca lida
de `kit_map_1_candidatos.csv`. O CSV e' diagnostico e carrega
`CANDIDATE_REVIEW`, que nasce de composicao empirica de NF; usa-lo como
configuracao seria promover evidencia a fato, que e' exatamente o que este gate
proibe. O CSV serve de segunda opiniao: o script compara a sua classificacao
com a dele e reporta onde discordam, em vez de confiar em qualquer um dos dois.

ANTES x DEPOIS
--------------
ANTES  = o que a tela publica hoje: kit nao tem referencia, ponto. Todos os
         `kit_confirmed`/`kit_suspected` saem do elegivel com
         `kit_composition_missing`.
DEPOIS = o que o contrato de `app.services.pma_kit_bom` permitiria publicar,
         se a flag fosse ligada — o que este script NAO faz.

Exit code:
    0  reconciliacao concluida
    2  fonte indisponivel, contrato da BOM recusado, ou env ausente
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.services import pma_kit_bom as kb  # noqa: E402
from app.services.pma_match import (  # noqa: E402
    ReferenceIndex, consumer_ean_or_none, normalize_brand_key, normalize_sku_key,
)

SQL_OFERTAS = """
WITH ult AS (
    SELECT marketplace, max(observed_date) AS d
      FROM marts.fact_channel_offer_observation
     GROUP BY 1
)
SELECT o.marketplace, o.observed_date, o.offer_key, o.brand, o.parent_item_id,
       o.model_id, o.seller_sku, o.gtin, o.product_type, o.product_type_source,
       o.business_scope, o.is_active
  FROM marts.fact_channel_offer_observation o
  JOIN ult u ON u.marketplace = o.marketplace AND u.d = o.observed_date
"""

SQL_REFERENCIA = """
WITH ult AS (
    SELECT snapshot_id
      FROM marts.fact_suggested_price_reference_snapshot
     ORDER BY captured_at DESC
     LIMIT 1
)
SELECT reference_row_id, brand, source_sku, source_gtin, product_name,
       suggested_retail_amount, quality_status, captured_at
  FROM marts.fact_suggested_price_reference_snapshot
 WHERE snapshot_id = (SELECT snapshot_id FROM ult)
"""

SQL_BOM = """
SELECT kit_sku, component_sku, qty_per_kit, valid_from, valid_to, active, source
  FROM raw.protheus_kit_components
"""

#: Identidade do componente. `codigo_protheus` quando existe; senao o proprio
#: `sku`, e so' quando a linha VEIO do Protheus — usar o SKU de um no Bling
#: como se fosse codigo Protheus e' justamente o erro que o gate 1 mediu.
SQL_CATALOGO = """
SELECT upper(trim(coalesce(nullif(codigo_protheus, ''), sku))) AS component_sku,
       marca AS brand, ean, marca_conflitante
  FROM gold.dim_produto_gobeauty
 WHERE coalesce(nullif(codigo_protheus, ''), '') <> ''
    OR fonte_do_sku = 'protheus'
"""

SQL_PONTE_MARCA_CODIGO = """
SELECT m.marca AS brand,
       upper(trim(m.codigo)) AS code,
       upper(trim(coalesce(nullif(d.codigo_protheus, ''), d.sku))) AS protheus_sku
  FROM gold.map_produto_codigo_gobeauty m
  JOIN gold.dim_produto_gobeauty d ON d.produto_sk = m.produto_sk
 WHERE NOT m.ambiguo
   AND (coalesce(nullif(d.codigo_protheus, ''), '') <> '' OR d.fonte_do_sku = 'protheus')
"""

SQL_PONTE_ALIAS = """
SELECT upper(trim(alias)) AS code,
       upper(trim(coalesce(nullif(codigo_protheus, ''), sku))) AS protheus_sku
  FROM gold.dim_produto_gobeauty d,
       LATERAL (VALUES (d.codigo_bling), (d.codigo_tiny),
                       (d.codigo_shopify), (d.codigo_omie)) AS a(alias)
 WHERE alias IS NOT NULL AND trim(alias) <> ''
   AND (coalesce(nullif(d.codigo_protheus, ''), '') <> '' OR d.fonte_do_sku = 'protheus')
UNION
SELECT upper(trim(sku_antigo)), upper(trim(sku))
  FROM silver.gobeaute_produto_cadastro
 WHERE sku_antigo IS NOT NULL
   AND upper(trim(sku_antigo)) NOT IN ('#N/A', 'N/A', '-', '0', 'NA', '#REF!')
   AND trim(sku_antigo) <> '' AND trim(coalesce(sku, '')) <> ''
"""

SQL_MARCA_DO_KIT = """
SELECT upper(trim(coalesce(nullif(codigo_protheus, ''), sku))) AS protheus_sku,
       marca AS brand
  FROM gold.dim_produto_gobeauty
 WHERE coalesce(nullif(codigo_protheus, ''), '') <> '' OR fonte_do_sku = 'protheus'
"""

SINAIS_DE_KIT = ("kit_confirmed", "kit_suspected")

COLUNAS = (
    "canal", "observado_em", "marca_canal", "item_id", "model_id", "seller_sku",
    "sinal_kit", "fonte_do_sinal", "ativo", "escopo",
    "referencia_antes", "motivo_antes",
    "status_ponte", "metodo_ponte", "kit_protheus", "marca_kit",
    "n_componentes", "unidades", "componentes", "quantidades",
    "componentes_resolvidos", "metodos_do_componente",
    "valor_antes_do_desconto", "faixa_desconto", "referencia_depois",
    "motivo_depois", "ganhou_referencia", "bloqueado_para_aprovacao",
)


def _conectar(url: str):
    import psycopg2
    conn = psycopg2.connect(url)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _linhas(conn, sql: str) -> list[dict]:
    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def _carregar_env() -> tuple[str, str]:
    try:
        from dotenv import load_dotenv
        load_dotenv(RAIZ.parent.parent / ".env")
    except ImportError:  # pragma: no cover - ambiente sem dotenv
        pass
    alvo = os.environ.get("DATABASE_URL", "")
    fonte = os.environ.get("DATAMART_DATABASE_URL", "")
    if not alvo or not fonte:
        raise SystemExit(
            "DATABASE_URL e DATAMART_DATABASE_URL sao obrigatorias (somente leitura)."
        )
    return alvo, fonte


def _texto(valor: object) -> str:
    return "" if valor is None else str(valor)


def reconciliar(*, hoje: date, ofertas, referencias, bom_rows, catalogo_rows,
                ponte_marca, ponte_alias, marca_kit) -> tuple[list[dict], dict]:
    indice_ref = ReferenceIndex.build(referencias)
    bom = kb.KitBomIndex.build(bom_rows, today=hoje)
    catalogo = kb.ComponentCatalog.build(catalogo_rows)
    ponte = kb.KitBridgeIndex.build(brand_code_rows=ponte_marca,
                                    alias_rows=ponte_alias,
                                    kit_brand_rows=marca_kit)

    kits = [o for o in ofertas if o["product_type"] in SINAIS_DE_KIT]
    saida: list[dict] = []
    for o in kits:
        b = kb.resolve_bridge(o["seller_sku"], o["brand"], bom=bom, index=ponte)
        r = kb.resolve_kit_reference(b, bom=bom, catalog=catalogo, index=indice_ref)
        comps = r.components
        saida.append({
            "canal": o["marketplace"],
            "observado_em": _texto(o["observed_date"]),
            "marca_canal": _texto(o["brand"]),
            "item_id": _texto(o["parent_item_id"]),
            "model_id": _texto(o["model_id"]),
            "seller_sku": _texto(o["seller_sku"]),
            "sinal_kit": o["product_type"],
            "fonte_do_sinal": _texto(o["product_type_source"]),
            "ativo": o["is_active"],
            "escopo": _texto(o["business_scope"]),
            # ANTES: a tela nao calcula referencia de kit em nenhum caso.
            "referencia_antes": "",
            "motivo_antes": "kit_composition_missing",
            "status_ponte": b.status,
            "metodo_ponte": _texto(b.method),
            "kit_protheus": _texto(b.kit_sku),
            "marca_kit": _texto(b.kit_brand),
            "n_componentes": r.component_count or "",
            "unidades": _texto(r.units),
            "componentes": "|".join(c.component_sku for c in comps),
            "quantidades": "|".join(str(c.qty_per_kit) for c in comps),
            "componentes_resolvidos": sum(1 for c in comps if c.resolved),
            "metodos_do_componente": "|".join(_texto(c.method) for c in comps),
            "valor_antes_do_desconto": _texto(r.components_base),
            "faixa_desconto": _texto(r.discount_pct),
            "referencia_depois": _texto(r.amount),
            "motivo_depois": _texto(r.reason),
            "ganhou_referencia": r.amount is not None,
            "bloqueado_para_aprovacao": b.status in (
                kb.BRIDGE_CANDIDATE_REVIEW, kb.BRIDGE_AMBIGUOUS,
                kb.BRIDGE_CROSS_BRAND_CONFLICT),
        })

    ganhou = [l for l in saida if l["ganhou_referencia"]]
    resumo = {
        "ofertas_com_sinal_de_kit": len(saida),
        "por_canal": dict(Counter(l["canal"] for l in saida)),
        "status_da_ponte": dict(Counter(l["status_ponte"] for l in saida)),
        "ganharam_referencia": len(ganhou),
        "ganharam_por_canal": dict(Counter(l["canal"] for l in ganhou)),
        "kits_distintos_com_referencia": len({l["kit_protheus"] for l in ganhou}),
        "motivos_de_null": dict(Counter(
            l["motivo_depois"] for l in saida if not l["ganhou_referencia"])),
        "bloqueados_para_aprovacao": sum(
            1 for l in saida if l["bloqueado_para_aprovacao"]),
        "duplicatas_colapsadas": [f"{k}|{c}" for k, c in bom.collapsed_duplicates],
        "kits_vigentes_na_bom": len(bom.components_by_kit),
        "valores": sorted({l["referencia_depois"] for l in ganhou}),
    }
    return saida, resumo


def medir_fallback_fora_dos_kits(ofertas, referencias, catalogo_rows,
                                 ponte_marca) -> dict:
    """Impacto do fallback `(marca, source_sku)` em ofertas que NAO sao kit.

    Hoje o caminho interno do matcher vai `produto_sk -> EAN -> referencia`. A
    medicao pergunta: quantas ofertas sem referencia por esse caminho teriam
    referencia se a chave `(marca, codigo_protheus)` tambem fosse tentada?

    Medicao apenas. Nada aqui altera o serving.
    """
    indice = ReferenceIndex.build(referencias)
    identidade = {}
    for row in catalogo_rows:
        sku = normalize_sku_key(row.get("component_sku"))
        if sku and sku not in identidade:
            identidade[sku] = (normalize_brand_key(row.get("brand")),
                               consumer_ean_or_none(row.get("ean")),
                               bool(row.get("marca_conflitante")))
    interno = {}
    for row in ponte_marca:
        marca = normalize_brand_key(row.get("brand"))
        codigo = normalize_sku_key(row.get("code"))
        alvo = normalize_sku_key(row.get("protheus_sku"))
        if marca and codigo and alvo:
            interno.setdefault((marca, codigo), set()).add(alvo)

    so_ean = so_sku = ja_cobertas = 0
    for o in ofertas:
        if o["product_type"] in SINAIS_DE_KIT:
            continue
        marca = normalize_brand_key(o["brand"])
        sku = normalize_sku_key(o["seller_sku"])
        if marca is None or sku is None:
            continue
        # A oferta ja' casa direto? Entao o fallback interno nao muda nada.
        gtin = consumer_ean_or_none(o.get("gtin"))
        if (gtin and len(indice.by_gtin.get((marca, gtin), ())) == 1) or \
           len(indice.by_sku.get((marca, sku), ())) == 1:
            ja_cobertas += 1
            continue
        alvos = interno.get((marca, sku), set())
        if len(alvos) != 1:
            continue
        alvo = next(iter(alvos))
        marca_int, ean_int, conflito = identidade.get(alvo, (None, None, False))
        if conflito or marca_int is None:
            continue
        por_ean = indice.by_gtin.get((marca_int, ean_int), ()) if ean_int else ()
        por_sku = indice.by_sku.get((marca_int, alvo), ())
        if len(por_ean) == 1:
            so_ean += 1
        elif len(por_sku) == 1:
            so_sku += 1
    return {
        "ofertas_nao_kit_ja_cobertas_pelo_matcher": ja_cobertas,
        "recuperaveis_pelo_caminho_interno_por_ean": so_ean,
        "recuperaveis_SO_pelo_fallback_marca_sku": so_sku,
    }


def comparar_com_diagnostico(linhas: list[dict], caminho: Path) -> dict:
    """Segunda opiniao: onde o runtime e o CSV analitico discordam, e por que.

    Discordancia nao e' defeito por si: o resolvedor do runtime so' conhece
    igualdade exata sobre cadastro, enquanto o diagnostico tambem olhou
    composicao empirica de NF. O que importa e' que nenhuma oferta seja
    PROMOVIDA aqui e bloqueada la'.
    """
    if not caminho.exists():
        return {"erro": f"diagnostico ausente em {caminho}"}
    with caminho.open(encoding="utf-8-sig") as fh:
        diag = {(r["canal"], (r["seller_sku"] or "").strip().upper()):
                r["status_ponte"] for r in csv.DictReader(fh)}
    pares = Counter()
    promovida_aqui_bloqueada_la = []
    for l in linhas:
        chave = (l["canal"], l["seller_sku"].strip().upper())
        antes = diag.get(chave, "<ausente>")
        pares[(antes, l["status_ponte"])] += 1
        if l["status_ponte"] in kb.PROMOTABLE_BRIDGE_STATUSES and \
           antes not in kb.PROMOTABLE_BRIDGE_STATUSES:
            promovida_aqui_bloqueada_la.append(chave)
    return {
        "transicoes_diagnostico_para_runtime": {
            f"{a} -> {b}": n for (a, b), n in sorted(pares.items())},
        "promovidas_no_runtime_e_bloqueadas_no_diagnostico":
            sorted(set(promovida_aqui_bloqueada_la)),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", required=True, help="CSV de reconciliacao a escrever")
    p.add_argument("--json", action="store_true", help="resumo em JSON no stdout")
    p.add_argument("--diagnostico", default=None,
                   help="CSV do KITS-MAP-1, usado so' como segunda opiniao")
    args = p.parse_args(argv)

    alvo, fonte = _carregar_env()
    hoje = date.today()

    with _conectar(alvo) as neon:
        ofertas = _linhas(neon, SQL_OFERTAS)
        referencias = _linhas(neon, SQL_REFERENCIA)
    with _conectar(fonte) as dm:
        bom_rows = _linhas(dm, SQL_BOM)
        catalogo_rows = _linhas(dm, SQL_CATALOGO)
        ponte_marca = _linhas(dm, SQL_PONTE_MARCA_CODIGO)
        ponte_alias = _linhas(dm, SQL_PONTE_ALIAS)
        marca_kit = _linhas(dm, SQL_MARCA_DO_KIT)

    for r in referencias:
        v = r.get("suggested_retail_amount")
        r["suggested_retail_amount"] = None if v is None else Decimal(str(v))

    try:
        linhas, resumo = reconciliar(
            hoje=hoje, ofertas=ofertas, referencias=referencias,
            bom_rows=bom_rows, catalogo_rows=catalogo_rows,
            ponte_marca=ponte_marca, ponte_alias=ponte_alias, marca_kit=marca_kit)
    except kb.BomContractError as exc:
        print(f"CONTRATO DA BOM RECUSADO: {exc}", file=sys.stderr)
        return 2

    resumo["fallback_fora_dos_kits"] = medir_fallback_fora_dos_kits(
        ofertas, referencias, catalogo_rows, ponte_marca)
    if args.diagnostico:
        resumo["segunda_opiniao"] = comparar_com_diagnostico(
            linhas, Path(args.diagnostico))
    resumo["fotografia"] = {
        "gerado_em": hoje.isoformat(),
        "ofertas_na_fotografia": len(ofertas),
        "linhas_de_referencia": len(referencias),
        "linhas_de_bom_lidas": len(bom_rows),
    }

    destino = Path(args.out)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUNAS))
        w.writeheader()
        w.writerows(linhas)

    if args.json:
        print(json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"CSV: {destino}  ({len(linhas)} linhas)")
        for chave, valor in resumo.items():
            print(f"  {chave}: {valor}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
