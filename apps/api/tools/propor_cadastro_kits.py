"""Proposta de cadastro de kits — fila de trabalho humana (gate KITS-MAP-2).

    python tools/propor_cadastro_kits.py \\
        --diagnostico docs/reconciliation/kit_map_1_candidatos_2026-09-25.csv \\
        --out docs/reconciliation/kits_map_2_cadastro_proposta_2026-09-25.csv

SOMENTE LEITURA no banco. O CSV gerado e' uma FILA DE APROVACAO: nenhum
runtime o consome, nenhuma metrica o le, e toda linha nasce `PENDENTE`. Um
`PENDENTE` nunca influencia numero nenhum — por construcao, porque nada alem
de gente le este arquivo.

DUAS SECOES, DUAS NATUREZAS
---------------------------
`chave_de_canal`  — codigo que o marketplace usa e que precisa ser gravado na
                    linha `tipo='KT'` do cadastro. Quando o KITS-MAP-1 achou
                    composicao empirica, a linha diz quantos componentes e com
                    quantos pedidos de evidencia, para o revisor saber o que
                    esta aceitando. A composicao em si NAO e' pre-aprovada.

`de_para_protheus` — par (codigo antigo, codigo atual) do mesmo produto. Esta
                    secao e' deliberadamente pequena e vem com aviso: o de-para
                    NAO e' derivavel das fontes atuais. O unico sinal estrutural
                    disponivel — mesmo codigo Bling alcancando dois codigos
                    Protheus dentro da MESMA marca — produz 3 candidatos, e um
                    deles ("Caixa com 3 brindes" x "Caixa com 2 brindes",
                    EANs diferentes) e' comprovadamente falso. Sem a marca o
                    sinal produz 83 pares, quase todos lixo, porque o codigo
                    Bling e' por CONTA e colide entre marcas. Por isso as
                    linhas saem como candidatas a conferencia, nao como
                    proposta de gravacao.

Exit code:
    0  proposta gerada
    2  fonte indisponivel ou env ausente
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.services.pma_kit_bom import (  # noqa: E402
    PROMOTABLE_BRIDGE_STATUSES,
)
from app.services.pma_match import (  # noqa: E402
    normalize_brand_key, normalize_sku_key,
)

ESTADO_INICIAL = "PENDENTE"
ESTADOS = ("PENDENTE", "APROVADO", "REJEITADO")

#: Valores que o cadastro usa como "vazio" em `sku_antigo`.
LIXO_DE_ALIAS = {"#N/A", "N/A", "-", "0", "NA", "#REF!"}

#: Minimo de linhas de NF para a nota fiscal ARBITRAR uma marca divergente.
#: `RT01009` tem UMA linha, de abril: um faturamento isolado nao sustenta
#: corrigir cadastro. `KS03046` tem 12.335, de julho a setembro.
MINIMO_DE_NOTAS = 10

SQL_DE_PARA = """
WITH cadastro AS (
    SELECT lower(trim(marca)) AS marca,
           upper(trim(sku_antigo)) AS bling,
           upper(trim(sku))        AS protheus,
           coalesce(descricao, descricao_comercial) AS nome,
           upper(trim(coalesce(gtin_ean, ''))) AS ean
      FROM silver.gobeaute_produto_cadastro
     WHERE sku_antigo IS NOT NULL AND trim(sku_antigo) <> ''
       AND upper(trim(sku_antigo)) <> ALL (%(lixo)s)
       AND trim(coalesce(sku, '')) <> ''
),
dimensao AS (
    SELECT lower(trim(d.marca)) AS marca,
           upper(trim(a.alias)) AS bling,
           upper(trim(coalesce(nullif(d.codigo_protheus, ''), d.sku))) AS protheus,
           d.nome, upper(trim(coalesce(d.ean, ''))) AS ean
      FROM gold.dim_produto_gobeauty d,
           LATERAL (VALUES (d.codigo_bling), (d.codigo_tiny)) AS a(alias)
     WHERE a.alias IS NOT NULL AND trim(a.alias) <> ''
       AND (coalesce(nullif(d.codigo_protheus, ''), '') <> ''
            OR d.fonte_do_sku = 'protheus')
)
SELECT c.marca, c.bling, c.protheus AS antigo, d.protheus AS atual,
       c.nome AS nome_antigo, d.nome AS nome_atual,
       c.ean AS ean_antigo, d.ean AS ean_atual
  FROM cadastro c
  JOIN dimensao d ON d.marca = c.marca AND d.bling = c.bling
 WHERE c.protheus <> d.protheus
"""

#: Componentes de kit ATIVO em que o cadastro e a dimensao discordam da marca,
#: com a contagem de notas fiscais que arbitra. A NF e' a evidencia mais forte
#: disponivel: ela registra o que foi efetivamente faturado, por marca.
SQL_MARCA_DIVERGENTE = """
WITH componentes AS (
    SELECT DISTINCT upper(trim(component_sku)) AS sku
      FROM raw.protheus_kit_components
     WHERE active
),
cadastro AS (
    SELECT upper(trim(sku)) AS sku, lower(trim(marca)) AS marca_cadastro,
           coalesce(descricao, descricao_comercial) AS descricao
      FROM silver.gobeaute_produto_cadastro
     WHERE trim(coalesce(sku, '')) <> ''
),
dimensao AS (
    SELECT upper(trim(coalesce(nullif(codigo_protheus, ''), sku))) AS sku,
           lower(trim(marca)) AS marca_dim, marca_origem, ean
      FROM gold.dim_produto_gobeauty
     WHERE coalesce(nullif(codigo_protheus, ''), '') <> ''
        OR fonte_do_sku = 'protheus'
),
notas AS (
    SELECT upper(trim(item_codigo)) AS sku, lower(trim(marca)) AS marca_nf,
           count(*) AS linhas, min(data_emissao)::date AS de,
           max(data_emissao)::date AS ate
      FROM gold.bling_all_brands_nfes_gproducts
     WHERE item_codigo IS NOT NULL
     GROUP BY 1, 2
)
SELECT c.sku, c.marca_cadastro, d.marca_dim, d.marca_origem, d.ean,
       c.descricao,
       (SELECT string_agg(n.marca_nf || '=' || n.linhas, ' | '
                          ORDER BY n.linhas DESC)
          FROM notas n WHERE n.sku = c.sku) AS marcas_na_nf,
       (SELECT sum(n.linhas) FROM notas n WHERE n.sku = c.sku) AS linhas_de_nf,
       (SELECT min(n.de)::text || ' .. ' || max(n.ate)::text
          FROM notas n WHERE n.sku = c.sku) AS periodo_da_nf
  FROM cadastro c
  JOIN dimensao d ON d.sku = c.sku
  JOIN componentes k ON k.sku = c.sku
 WHERE c.marca_cadastro IS DISTINCT FROM d.marca_dim
 ORDER BY 1
"""

COLUNAS = (
    "secao", "chave", "canal", "marca", "alvo_proposto",
    "ofertas_afetadas", "composicao_empirica", "componentes", "unidades",
    "pedidos_de_evidencia", "ultima_evidencia", "evidencia", "alerta",
    "responsavel", "estado", "decidido_em", "observacao",
)


def _carregar_env() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(RAIZ.parent.parent / ".env")
    except ImportError:  # pragma: no cover
        pass
    url = os.environ.get("DATAMART_DATABASE_URL", "")
    if not url:
        raise SystemExit("DATAMART_DATABASE_URL e' obrigatoria (somente leitura).")
    return url


def _consultar(url: str) -> tuple[list[dict], list[dict]]:
    """Uma conexao SOMENTE LEITURA, duas consultas."""
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(url)
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(SQL_DE_PARA, {"lixo": list(LIXO_DE_ALIAS)})
            de_para = [dict(r) for r in cur.fetchall()]
            cur.execute(SQL_MARCA_DIVERGENTE)
            marcas = [dict(r) for r in cur.fetchall()]
        return de_para, marcas
    finally:
        conn.close()


def _sem_acento(texto: object) -> str:
    """Compara marca ignorando acento.

    `apice` x `Ápice` e `rituaria` x `Rituária` NAO sao inconsistencia de
    cadastro: sao a mesma marca escrita de dois jeitos. Sem este filtro a fila
    de aprovacao nasceria com dezenas de linhas que nao pedem decisao nenhuma,
    e a unica que pede — `KS03046` — se perderia no meio.
    """
    bruto = unicodedata.normalize("NFKD", str(texto or "").strip().lower())
    return "".join(c for c in bruto if not unicodedata.combining(c))


def marca_divergente_linhas(linhas: list[dict]) -> list[dict]:
    """Discordancia de MARCA entre cadastro e dimensao, arbitrada pela NF.

    O caso que originou esta secao e' `KS03046`: o cadastro diz `By Samia`, o
    `dim_produto` diz `kokeshi` (por `prefixo_protheus`), e as notas fiscais
    registram `kokeshi` em 100% das linhas. A NF e' o que foi FATURADO — nao ha
    fonte mais forte disponivel. A linha do cadastro e' a inconsistencia, nao a
    autoridade.

    A sugestao vai preenchida em `alvo_proposto`, mas o estado continua
    `PENDENTE`: corrigir cadastro e' ato de quem o mantem.
    """
    saida = []
    for r in linhas:
        if _sem_acento(r["marca_cadastro"]) == _sem_acento(r["marca_dim"]):
            continue
        nf = (r.get("marcas_na_nf") or "").strip()
        unica = nf.count("|") == 0 and nf != ""
        marca_nf = nf.split("=")[0] if unica else None
        linhas_nf = r.get("linhas_de_nf") or 0
        # Uma nota so' nao arbitra nada. O limiar existe para que "a NF decidiu"
        # signifique evidencia, nao coincidencia.
        concorda = (unica and _sem_acento(marca_nf) == _sem_acento(r["marca_dim"])
                    and linhas_nf >= MINIMO_DE_NOTAS)
        saida.append({
            "secao": "inconsistencia_de_marca",
            "chave": r["sku"],
            "canal": "",
            "marca": r["marca_cadastro"] or "",
            "alvo_proposto": (r["marca_dim"] or "") if concorda else "",
            "ofertas_afetadas": "",
            "composicao_empirica": "",
            "componentes": "",
            "unidades": "",
            "pedidos_de_evidencia": r.get("linhas_de_nf") or "",
            "ultima_evidencia": r.get("periodo_da_nf") or "",
            "evidencia": (
                f"cadastro='{r['marca_cadastro']}' x dim='{r['marca_dim']}' "
                f"(origem {r['marca_origem']}); NF: {nf or 'sem nota'}; "
                f"'{(r.get('descricao') or '')[:40]}' EAN {r.get('ean') or 'vazio'}"),
            "alerta": ("" if concorda else
                       "as notas nao arbitram sozinhas (marca unica ausente ou "
                       "divergente do dim): decidir com o dono do cadastro"),
            "responsavel": "",
            "estado": ESTADO_INICIAL,
            "decidido_em": "",
            "observacao": ("corrigir a MARCA na linha do cadastro; o dim e a NF "
                           "ja' concordam" if concorda else ""),
        })
    return saida


def chaves_de_canal(diagnostico: list[dict]) -> list[dict]:
    """Uma linha por chave de canal ainda nao cadastrada como kit."""
    por_chave: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for linha in diagnostico:
        sku = normalize_sku_key(linha.get("seller_sku"))
        if not sku:
            continue
        if linha.get("status_ponte") in PROMOTABLE_BRIDGE_STATUSES:
            continue  # ja' tem ponte exata: nao precisa de cadastro novo
        por_chave[(normalize_brand_key(linha.get("marca_canal")) or "", sku)].append(linha)

    saida = []
    for (marca, sku), linhas in sorted(por_chave.items()):
        canais = sorted({l["canal"] for l in linhas})
        com_bom = [l for l in linhas if l.get("fonte_composicao") == "nf_empirica"]
        base = com_bom[0] if com_bom else linhas[0]
        evid = [int(l["pedidos_de_evidencia"]) for l in com_bom
                if (l.get("pedidos_de_evidencia") or "").strip().isdigit()]
        saida.append({
            "secao": "chave_de_canal",
            "chave": sku,
            "canal": "|".join(canais),
            "marca": marca,
            "alvo_proposto": "",       # o SKU Protheus e' decisao do cadastro
            "ofertas_afetadas": len(linhas),
            "composicao_empirica": "sim" if com_bom else "nao",
            "componentes": base.get("n_componentes") or "",
            "unidades": base.get("unidades") or "",
            "pedidos_de_evidencia": min(evid) if evid else "",
            "ultima_evidencia": base.get("ultima_nf_evidencia") or "",
            "evidencia": ("composicao reconstruida de NF (KITS-MAP-1)"
                          if com_bom else "nenhuma"),
            "alerta": ("" if com_bom else
                       "sem composicao: exige a BOM do fornecedor ou do Protheus"),
            "responsavel": "",
            "estado": ESTADO_INICIAL,
            "decidido_em": "",
            "observacao": "",
        })
    return saida


def de_para_linhas(pares: list[dict]) -> list[dict]:
    """Uma linha por `(marca, antigo, atual)`.

    Deduplicar e' requisito de fila de aprovacao, nao arrumacao: o mesmo par
    chega duas vezes quando `codigo_bling` e `codigo_tiny` guardam o mesmo
    valor, e duas linhas iguais permitem duas decisoes conflitantes sobre o
    mesmo par. Os codigos Bling que geraram o par sao acumulados na evidencia.
    """
    agrupado: dict[tuple[str, str, str], dict] = {}
    for p in pares:
        chave = (p["marca"], p["antigo"], p["atual"])
        alvo = agrupado.setdefault(chave, dict(p, blings=set()))
        alvo["blings"].add(p["bling"])

    saida = []
    for p in sorted(agrupado.values(), key=lambda r: (r["marca"], r["antigo"])):
        mesmo_ean = bool(p["ean_antigo"]) and p["ean_antigo"] == p["ean_atual"]
        alerta = ("" if mesmo_ean else
                  f"EAN divergente ou ausente ({p['ean_antigo'] or 'vazio'} x "
                  f"{p['ean_atual'] or 'vazio'}): pode ser produto distinto "
                  "compartilhando codigo Bling. Conferir antes de aprovar.")
        saida.append({
            "secao": "de_para_protheus",
            "chave": p["antigo"],
            "canal": "",
            "marca": p["marca"],
            "alvo_proposto": p["atual"],
            "ofertas_afetadas": "",
            "composicao_empirica": "",
            "componentes": "",
            "unidades": "",
            "pedidos_de_evidencia": "",
            "ultima_evidencia": "",
            "evidencia": (f"mesmo codigo Bling {'/'.join(sorted(p['blings']))} "
                          f"na mesma marca; "
                          f"'{(p['nome_antigo'] or '')[:40]}' (EAN "
                          f"{p['ean_antigo'] or 'vazio'}) x "
                          f"'{(p['nome_atual'] or '')[:40]}' (EAN "
                          f"{p['ean_atual'] or 'vazio'})"),
            "alerta": alerta,
            "responsavel": "",
            "estado": ESTADO_INICIAL,
            "decidido_em": "",
            "observacao": "",
        })
    return saida


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--diagnostico", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    with Path(args.diagnostico).open(encoding="utf-8-sig") as fh:
        diagnostico = list(csv.DictReader(fh))

    de_para, marcas = _consultar(_carregar_env())
    linhas = (chaves_de_canal(diagnostico) + de_para_linhas(de_para)
              + marca_divergente_linhas(marcas))

    destino = Path(args.out)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUNAS))
        w.writeheader()
        w.writerows(linhas)

    resumo = Counter(l["secao"] for l in linhas)
    canal = [l for l in linhas if l["secao"] == "chave_de_canal"]
    com_bom = [l for l in canal if l["composicao_empirica"] == "sim"]
    estados = Counter(l["estado"] for l in linhas)
    print(f"proposta: {destino}  ({len(linhas)} linhas), gerada em {date.today()}")
    # Dois graos, de proposito: o cadastro grava por (marca, codigo), mas o
    # gate conta codigos. Quando os dois numeros divergem e' porque o mesmo
    # codigo e' anunciado por mais de uma marca.
    print(f"  chaves de canal: {len(canal)} linhas (marca, codigo) / "
          f"{len({l['chave'] for l in canal})} codigos distintos")
    print(f"  com composicao empirica: {len(com_bom)} linhas / "
          f"{len({l['chave'] for l in com_bom})} codigos distintos")
    print(f"  de-para Protheus candidatos: {resumo['de_para_protheus']}  "
          f"(com alerta de EAN divergente: "
          f"{sum(1 for l in linhas if l['secao'] == 'de_para_protheus' and l['alerta'])})")
    marcas_lin = [l for l in linhas if l["secao"] == "inconsistencia_de_marca"]
    print(f"  inconsistencias de marca (componente de kit): {len(marcas_lin)}  "
          f"(arbitradas pela NF: "
          f"{sum(1 for l in marcas_lin if l['alvo_proposto'])})")
    print(f"  estados: {dict(estados)}")
    assert set(estados) == {ESTADO_INICIAL}, "toda linha nasce PENDENTE"
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
