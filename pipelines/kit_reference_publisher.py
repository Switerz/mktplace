"""Publisher da referencia de kit derivada dos componentes (gate KITS-PMA-3).

    python -m pipelines.kit_reference_publisher --diagnose
    python -m pipelines.kit_reference_publisher --apply

ONDE A TRANSFORMACAO ACONTECE, E POR QUE AQUI
----------------------------------------------
O Render/API nao alcanca o Data Mart. A BOM (`raw.protheus_kit_components`), o
catalogo (`gold.dim_produto_gobeauty`) e a ponte cadastral
(`gold.map_produto_codigo_gobeauty`, `silver.gobeaute_produto_cadastro`) so'
existem la'. Entao o calculo roda AQUI, no pipeline, e o resultado e'
materializado em `marts.fact_kit_reference_daily` no Neon, com procedencia
explicita dos dois insumos que o determinam: a fotografia da oferta e o
snapshot B2B.

A REGRA NAO E' REIMPLEMENTADA
------------------------------
Toda decisao — contrato da BOM, allowlist da ponte, precedencia EAN ->
`(marca, source_sku)`, faixa por unidades, motivo de cada `NULL` — vem de
`app.services.pma_kit_bom`, o modulo que o gate KITS-MAP-2 travou com 69
testes. Este arquivo NAO tem `if status ==` sobre ponte, nao soma componente e
nao arredonda preco. Ele carrega as fontes, chama o contrato e grava.

O `sys.path` para `apps/api` segue a convencao ja' usada por
`channel_offer_sync` e `sync_serving_snapshots`: a dependencia vai do pipeline
PARA a API, nunca o contrario — `apps/api` e' empacotado e implantado sozinho e
ha teste de regressao provando que ele nao importa `pipelines`.

O CSV DE CANDIDATOS NAO E' CONFIGURACAO
----------------------------------------
`docs/reconciliation/kit_map_1_candidatos_*.csv` e' diagnostico e carrega
`CANDIDATE_REVIEW`, que nasce de composicao empirica reconstruida de nota
fiscal. Este publisher nunca o le'. A ponte e' reconstruida das fontes
cadastrais a cada execucao.

O QUE ENTRA NA TABELA
---------------------
So' ponte `EXACT_DIRECT`/`EXACT_ALIAS` com as duas marcas conhecidas e iguais.
Dessas, as que resolvem todos os componentes entram como `resolved` com valor;
as que param em componente sem referencia B2B entram como `blocked`, com valor
NULO e motivo. `CANDIDATE_REVIEW`, `AMBIGUOUS`, `CROSS_BRAND_CONFLICT` e
`UNMAPPED` nao produzem linha nenhuma — e o CHECK da 022 os recusaria de
qualquer forma.

Exit code:
    0  diagnostico concluido, ou publicacao bem-sucedida
    1  falha de execucao (fonte, contrato da BOM, reconciliacao)
    2  configuracao ausente
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.services import pma_kit_bom as kb  # noqa: E402

TARGET_TABLE = "marts.fact_kit_reference_daily"
OFFER_TABLE = "marts.fact_channel_offer_observation"
REFERENCE_TABLE = "marts.fact_suggested_price_reference_snapshot"

AUDIT_SOURCE_NAME = "kit_reference_daily"
AUDIT_RUNNING, AUDIT_SUCCESS, AUDIT_FAILED = "running", "success", "failed"

#: Chave do advisory lock. Constante propria: esta publicacao nao concorre com
#: a fotografia de ofertas, e compartilhar a chave delas serializaria duas
#: coisas independentes.
LOCK_KEY = 4_2026_0925

ENV_TARGET = "DATABASE_URL"
ENV_SOURCE = "DATAMART_DATABASE_URL"

#: Tamanho da pagina de INSERT. O volume e' de dezenas de linhas; a paginacao
#: existe para que o contrato nao mude quando a cobertura crescer.
PAGE = 500


class KitPublisherError(RuntimeError):
    """Falha que impede a publicacao. NUNCA e' engolida."""


# ---------------------------------------------------------------------------
# Consultas — cada uma declara de qual banco ela vem
# ---------------------------------------------------------------------------

SQL_OFFERS = f"""
SELECT marketplace, observed_date, offer_key, brand, shop_account, seller_sku,
       gtin, product_type, batch_id
  FROM {OFFER_TABLE}
 WHERE marketplace = %(marketplace)s
   AND observed_date = %(observed_date)s
"""

SQL_LATEST_OFFER_DATE = f"""
SELECT max(observed_date) AS observed_date
  FROM {OFFER_TABLE}
 WHERE marketplace = %(marketplace)s
"""

SQL_MARKETPLACES = f"SELECT DISTINCT marketplace FROM {OFFER_TABLE}"

SQL_SNAPSHOT = f"""
SELECT snapshot_id, max(captured_at) AS captured_at
  FROM {REFERENCE_TABLE}
 GROUP BY snapshot_id
 ORDER BY max(captured_at) DESC
 LIMIT 1
"""

SQL_REFERENCES = f"""
SELECT reference_row_id, brand, source_sku, source_gtin,
       suggested_retail_amount, quality_status
  FROM {REFERENCE_TABLE}
 WHERE snapshot_id = %(snapshot_id)s
"""

SQL_BOM = """
SELECT kit_sku, component_sku, qty_per_kit, valid_from, valid_to, active
  FROM raw.protheus_kit_components
"""

SQL_CATALOG = """
SELECT upper(trim(coalesce(nullif(codigo_protheus, ''), sku))) AS component_sku,
       marca AS brand, ean, marca_conflitante
  FROM gold.dim_produto_gobeauty
 WHERE coalesce(nullif(codigo_protheus, ''), '') <> ''
    OR fonte_do_sku = 'protheus'
"""

SQL_BRIDGE_BRAND_CODE = """
SELECT m.marca AS brand,
       upper(trim(m.codigo)) AS code,
       upper(trim(coalesce(nullif(d.codigo_protheus, ''), d.sku))) AS protheus_sku
  FROM gold.map_produto_codigo_gobeauty m
  JOIN gold.dim_produto_gobeauty d ON d.produto_sk = m.produto_sk
 WHERE NOT m.ambiguo
   AND (coalesce(nullif(d.codigo_protheus, ''), '') <> ''
        OR d.fonte_do_sku = 'protheus')
"""

SQL_BRIDGE_ALIAS = """
SELECT upper(trim(a.alias)) AS code,
       upper(trim(coalesce(nullif(d.codigo_protheus, ''), d.sku))) AS protheus_sku
  FROM gold.dim_produto_gobeauty d,
       LATERAL (VALUES (d.codigo_bling), (d.codigo_tiny),
                       (d.codigo_shopify), (d.codigo_omie)) AS a(alias)
 WHERE a.alias IS NOT NULL AND trim(a.alias) <> ''
   AND (coalesce(nullif(d.codigo_protheus, ''), '') <> ''
        OR d.fonte_do_sku = 'protheus')
UNION
SELECT upper(trim(sku_antigo)), upper(trim(sku))
  FROM silver.gobeaute_produto_cadastro
 WHERE sku_antigo IS NOT NULL AND trim(sku_antigo) <> ''
   AND upper(trim(sku_antigo)) NOT IN ('#N/A', 'N/A', '-', '0', 'NA', '#REF!')
   AND trim(coalesce(sku, '')) <> ''
"""

SQL_KIT_BRAND = """
SELECT upper(trim(coalesce(nullif(codigo_protheus, ''), sku))) AS protheus_sku,
       marca AS brand
  FROM gold.dim_produto_gobeauty
 WHERE coalesce(nullif(codigo_protheus, ''), '') <> ''
    OR fonte_do_sku = 'protheus'
"""

SQL_DELETE_SCOPE = f"""
DELETE FROM {TARGET_TABLE}
 WHERE marketplace = %(marketplace)s AND observed_date = %(observed_date)s
"""

SQL_COUNT_SCOPE = f"""
SELECT count(*) FROM {TARGET_TABLE}
 WHERE marketplace = %(marketplace)s AND observed_date = %(observed_date)s
"""

COLUNAS = (
    "marketplace", "observed_date", "offer_key", "brand", "shop_account",
    "seller_sku", "kit_protheus_sku", "kit_brand", "bridge_status",
    "bridge_method", "reference_snapshot_id", "reference_captured_at",
    "offer_batch_id", "component_count", "total_units", "components",
    "components_base_amount", "discount_pct", "kit_reference_amount",
    "status", "reason", "source_run_id",
)

SQL_INSERT = (
    f"INSERT INTO {TARGET_TABLE} ({', '.join(COLUNAS)}) VALUES %s"
)

STATUS_RESOLVED = "resolved"
STATUS_BLOCKED = "blocked"

#: Os dois `product_type` que o publisher de ofertas grava para kit. Literais
#: e nao import de `pma_domain` porque aqui eles sao FILTRO de leitura da fato,
#: nao classificacao: o tipo ja' foi decidido por quem gravou a fotografia.
SINAIS_DE_KIT = ("kit_confirmed", "kit_suspected")


# ---------------------------------------------------------------------------
# Plano — puro, sem banco
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    marketplace: str
    observed_date: date


@dataclass
class Plan:
    scope: Scope
    records: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def build_plan(*, scope: Scope, offers, references, bom_rows, catalog_rows,
               bridge_brand_code, bridge_alias, kit_brand_rows,
               snapshot_id: str, reference_captured_at, source_run_id: str,
               today: date) -> Plan:
    """Calcula as linhas publicaveis. Sem I/O, para o teste poder exercitar.

    Levanta `kb.BomContractError` — deliberadamente NAO capturada aqui — quando
    a BOM se contradiz. Publicar "o que deu" a partir de uma BOM corrompida
    produziria kit silenciosamente mais barato, que e' o defeito que o contrato
    existe para impedir.
    """
    from app.services.pma_match import ReferenceIndex

    indice_ref = ReferenceIndex.build(list(references))
    bom = kb.KitBomIndex.build(bom_rows, today=today)
    catalogo = kb.ComponentCatalog.build(catalog_rows)
    ponte = kb.KitBridgeIndex.build(brand_code_rows=bridge_brand_code,
                                    alias_rows=bridge_alias,
                                    kit_brand_rows=kit_brand_rows)

    registros, resumo = [], {
        "offers_in_photo": len(offers),
        "kit_signal": 0,
        "bridge_status": {},
        "published_resolved": 0,
        "published_blocked": 0,
        "skipped_not_promotable": 0,
        "collapsed_duplicates": [f"{k}|{c}" for k, c in bom.collapsed_duplicates],
        "kits_in_bom": len(bom.components_by_kit),
        "reasons": {},
        "amounts": [],
    }

    for oferta in offers:
        tipo = oferta.get("product_type")
        if tipo not in SINAIS_DE_KIT:
            continue
        resumo["kit_signal"] += 1

        ponte_oferta = kb.resolve_bridge(oferta.get("seller_sku"),
                                         oferta.get("brand"),
                                         bom=bom, index=ponte)
        resumo["bridge_status"][ponte_oferta.status] = (
            resumo["bridge_status"].get(ponte_oferta.status, 0) + 1)

        pode, _ = kb.is_promotable(ponte_oferta)
        if not pode:
            # Candidato, ambiguo, conflito e nao mapeado NAO produzem linha.
            resumo["skipped_not_promotable"] += 1
            continue

        r = kb.resolve_kit_reference(ponte_oferta, bom=bom, catalog=catalogo,
                                     index=indice_ref)
        resolvido = r.amount is not None
        status = STATUS_RESOLVED if resolvido else STATUS_BLOCKED
        if resolvido:
            resumo["published_resolved"] += 1
            resumo["amounts"].append(str(r.amount))
        else:
            resumo["published_blocked"] += 1
            resumo["reasons"][r.reason] = resumo["reasons"].get(r.reason, 0) + 1

        componentes = [
            {"component_sku": c.component_sku,
             "qty_per_kit": str(c.qty_per_kit),
             "amount": None if c.amount is None else str(c.amount),
             "method": c.method,
             "reason": c.reason}
            for c in r.components
        ]
        registros.append((
            scope.marketplace, scope.observed_date, oferta["offer_key"],
            oferta["brand"], oferta.get("shop_account"), oferta.get("seller_sku"),
            r.kit_sku, ponte_oferta.kit_brand, ponte_oferta.status,
            ponte_oferta.method, snapshot_id, reference_captured_at,
            oferta.get("batch_id"), r.component_count, r.units,
            json.dumps(componentes, ensure_ascii=False),
            r.components_base, r.discount_pct, r.amount, status, r.reason,
            source_run_id,
        ))

    resumo["amounts"] = sorted(resumo["amounts"])
    return Plan(scope=scope, records=registros, summary=resumo)


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------

def _rows(conn, sql, params=None) -> list:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or {})
        return [dict(r) for r in cur.fetchall()]


def _connect(url: str, *, readonly: bool):
    conn = psycopg2.connect(url)
    if readonly:
        conn.set_session(readonly=True, autocommit=True)
    return conn


def execute_plan(target_conn, plan: Plan) -> tuple[int, int]:
    """DELETE do escopo + INSERT, na MESMA transacao. NAO comita.

    Quem comita e' `publish`, para que commit e desfecho fiquem num lugar so'.
    O DELETE por escopo e' o que torna a publicacao IDEMPOTENTE: reexecutar
    com a mesma entrada apaga e regrava as mesmas linhas.
    """
    escopo = {"marketplace": plan.scope.marketplace,
              "observed_date": plan.scope.observed_date}
    esperado = len(plan.records)

    with target_conn.cursor() as cur:
        cur.execute(SQL_DELETE_SCOPE, escopo)

        inseridas = 0
        for inicio in range(0, esperado, PAGE):
            pagina = plan.records[inicio:inicio + PAGE]
            execute_values(cur, SQL_INSERT, pagina, page_size=len(pagina) or 1)
            inseridas += cur.rowcount

        # PRIMEIRA defesa: o driver confirmou o que o plano tinha.
        if inseridas != esperado:
            raise KitPublisherError(
                f"o INSERT confirmou {inseridas} linhas e o plano tinha {esperado}")

        # SEGUNDA defesa, independente: o que a PROPRIA transacao enxerga no
        # escopo. Cobre linha gravada em escopo alheio e DELETE que varreu
        # alem do proprio escopo — coisas que a contagem do driver nao ve'.
        cur.execute(SQL_COUNT_SCOPE, escopo)
        visto = cur.fetchone()[0]

    if visto != esperado:
        raise KitPublisherError(
            f"reconciliacao pre-commit divergiu: a transacao enxerga {visto} "
            f"linhas no escopo e o plano tinha {esperado}")
    return inseridas, visto


def audit_start(audit_conn, rows_extracted: int) -> int:
    with audit_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit.source_sync_run
                (source_name, marketplace_id, loja_id, status, started_at,
                 rows_extracted)
            VALUES (%s, NULL, NULL, 'running', NOW(), %s)
            RETURNING sync_run_id
            """, (AUDIT_SOURCE_NAME, rows_extracted))
        sync_run_id = cur.fetchone()[0]
    audit_conn.commit()
    return sync_run_id


def audit_finish(audit_conn, sync_run_id: int, status: str,
                 rows_loaded=None, error_message=None) -> None:
    with audit_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE audit.source_sync_run
               SET status = %s, finished_at = NOW(),
                   rows_loaded = %s, error_message = %s
             WHERE sync_run_id = %s
            """, (status, rows_loaded, error_message, sync_run_id))
        if cur.rowcount != 1:
            raise KitPublisherError(
                "UPDATE de auditoria nao afetou exatamente 1 linha")
    audit_conn.commit()


def carregar(source_conn, target_conn, *, marketplace: str,
             observed_date: date | None) -> dict:
    """Le' os dois bancos. O Data Mart e' somente leitura, por conexao."""
    if observed_date is None:
        linhas = _rows(target_conn, SQL_LATEST_OFFER_DATE,
                       {"marketplace": marketplace})
        observed_date = linhas[0]["observed_date"] if linhas else None
    if observed_date is None:
        raise KitPublisherError(
            f"sem fotografia de oferta para {marketplace}: nada a publicar")

    snap = _rows(target_conn, SQL_SNAPSHOT)
    if not snap:
        raise KitPublisherError(
            "sem snapshot de referencia B2B: a referencia de kit nao tem insumo")

    return {
        "observed_date": observed_date,
        "snapshot_id": snap[0]["snapshot_id"],
        "reference_captured_at": snap[0]["captured_at"],
        "offers": _rows(target_conn, SQL_OFFERS, {
            "marketplace": marketplace, "observed_date": observed_date}),
        "references": _rows(target_conn, SQL_REFERENCES,
                            {"snapshot_id": snap[0]["snapshot_id"]}),
        "bom_rows": _rows(source_conn, SQL_BOM),
        "catalog_rows": _rows(source_conn, SQL_CATALOG),
        "bridge_brand_code": _rows(source_conn, SQL_BRIDGE_BRAND_CODE),
        "bridge_alias": _rows(source_conn, SQL_BRIDGE_ALIAS),
        "kit_brand_rows": _rows(source_conn, SQL_KIT_BRAND),
    }


def publish(*, target_conn, audit_conn, source_conn, marketplace: str,
            observed_date: date | None, today: date,
            source_run_id: str | None = None) -> dict:
    """Publica UM escopo `(marketplace, observed_date)`, atomicamente."""
    run_id = source_run_id or f"kitref-{uuid.uuid4().hex[:12]}"

    with target_conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        if not cur.fetchone()[0]:
            raise KitPublisherError(
                "advisory lock indisponivel: outra publicacao esta em curso")

    try:
        dados = carregar(source_conn, target_conn, marketplace=marketplace,
                         observed_date=observed_date)
        scope = Scope(marketplace, dados["observed_date"])
        plano = build_plan(
            scope=scope, offers=dados["offers"], references=dados["references"],
            bom_rows=dados["bom_rows"], catalog_rows=dados["catalog_rows"],
            bridge_brand_code=dados["bridge_brand_code"],
            bridge_alias=dados["bridge_alias"],
            kit_brand_rows=dados["kit_brand_rows"],
            snapshot_id=dados["snapshot_id"],
            reference_captured_at=dados["reference_captured_at"],
            source_run_id=run_id, today=today)

        sync_run_id = audit_start(audit_conn, len(plano.records))
        try:
            inseridas, _ = execute_plan(target_conn, plano)
            target_conn.commit()
        except BaseException as exc:
            target_conn.rollback()
            # A auditoria NAO pode mascarar a causa. Se o banco de auditoria
            # tambem estiver ruim, `audit_finish` levantaria aqui e o erro que
            # o operador veria seria o da auditoria, nao o da publicacao — e o
            # registro ficaria `running` para sempre, o que e' pior de
            # diagnosticar do que um `failed` perdido.
            try:
                audit_finish(audit_conn, sync_run_id, AUDIT_FAILED, 0,
                             str(exc)[:500])
            except Exception:  # pragma: no cover - defesa de borda
                pass
            raise
        audit_finish(audit_conn, sync_run_id, AUDIT_SUCCESS, inseridas)

        return {"state": "published", "source_run_id": run_id,
                "sync_run_id": sync_run_id, "rows": inseridas,
                "scope": {"marketplace": marketplace,
                          "observed_date": str(scope.observed_date)},
                "summary": plano.summary}
    finally:
        with target_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        target_conn.commit()


def diagnose(*, target_conn, source_conn, marketplace: str,
             observed_date: date | None, today: date) -> dict:
    """Calcula e relata SEM lock, SEM transacao de destino e SEM escrever."""
    dados = carregar(source_conn, target_conn, marketplace=marketplace,
                     observed_date=observed_date)
    plano = build_plan(
        scope=Scope(marketplace, dados["observed_date"]),
        offers=dados["offers"], references=dados["references"],
        bom_rows=dados["bom_rows"], catalog_rows=dados["catalog_rows"],
        bridge_brand_code=dados["bridge_brand_code"],
        bridge_alias=dados["bridge_alias"],
        kit_brand_rows=dados["kit_brand_rows"],
        snapshot_id=dados["snapshot_id"],
        reference_captured_at=dados["reference_captured_at"],
        source_run_id="diagnose", today=today)
    return {"state": "diagnosed", "rows_that_would_publish": len(plano.records),
            "scope": {"marketplace": marketplace,
                      "observed_date": str(dados["observed_date"])},
            "snapshot_id": dados["snapshot_id"],
            "summary": plano.summary}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _data(texto: str) -> date:
    return date.fromisoformat(texto)


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--marketplace", action="append", default=None,
                   help="canal a publicar; repetivel. Padrao: todos os da fato")
    p.add_argument("--observed-date", type=_data, default=None,
                   help="fotografia a usar. Padrao: a mais recente do canal")
    p.add_argument("--apply", action="store_true",
                   help="PUBLICA. Sem esta flag o comando so' diagnostica")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_cli().parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:  # pragma: no cover
        pass

    alvo_url = os.environ.get(ENV_TARGET, "")
    fonte_url = os.environ.get(ENV_SOURCE, "")
    if not alvo_url or not fonte_url:
        print(f"{ENV_TARGET} e {ENV_SOURCE} sao obrigatorias.", file=sys.stderr)
        return 2

    hoje = date.today()
    resultados = []
    source_conn = _connect(fonte_url, readonly=True)
    target_conn = _connect(alvo_url, readonly=False)
    audit_conn = _connect(alvo_url, readonly=False) if args.apply else None
    try:
        canais = args.marketplace or [
            r["marketplace"] for r in _rows(target_conn, SQL_MARKETPLACES)]
        for canal in canais:
            if args.apply:
                resultados.append(publish(
                    target_conn=target_conn, audit_conn=audit_conn,
                    source_conn=source_conn, marketplace=canal,
                    observed_date=args.observed_date, today=hoje))
            else:
                resultados.append(diagnose(
                    target_conn=target_conn, source_conn=source_conn,
                    marketplace=canal, observed_date=args.observed_date,
                    today=hoje))
    except (KitPublisherError, kb.BomContractError) as exc:
        print(f"FALHA: {exc}", file=sys.stderr)
        return 1
    finally:
        for c in (source_conn, target_conn, audit_conn):
            if c is not None:
                c.close()

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2, default=str))
    else:
        for r in resultados:
            print(json.dumps(r, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
