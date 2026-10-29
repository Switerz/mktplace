"""ML-FULL-STOCK-INGESTION-1 (mktplace): indicadores do estoque Full do ML, DRY-RUN.

O que este pipeline e' HOJE
---------------------------
Somente leitura e somente dry-run. Le, no Data Mart e em sessao READ ONLY, a
fotografia publicada pela DAG `mercadolivre_full_stock_snapshot` do airflow
(`raw.ml_full_inventory_snapshot`, `_unavailable`, `_listing`) e as vendas
Full pagas dos 28 dias completos anteriores. Calcula os indicadores por
INVENTARIO (`pipelines.transforms.ml_full_stock`) e imprime so' AGREGADOS por
marca.

O que ele NAO faz (de proposito)
---------------------------------
- NAO grava em lugar nenhum e NAO tem `--apply`. A fato de destino no Neon
  exige migration propria, e migration, publicacao e flag sao gates separados.
- NAO assume que as tabelas `raw.ml_full_*` existem. A DDL delas nao foi
  aplicada; ausentes, o resultado e' `fonte_inexistente`, nunca estoque zero.
- NAO soma estoque por anuncio nem rateia venda sem vinculo unico.
- NAO imprime `run_id`: ele e' lido em cada linha (snapshot, status, ponte) so'
  para o publicador conferir a linhagem RAW; o resumo continua so' agregado.
- NAO trata `transfer` como remessa do operador (Eli) ao Full: essa remessa
  vem de NF do Bling e e' outro fato.

Vendas: mesmo contrato do `sync_ml_fulfillment_daily`. Pedido `paid` por
`date_created::date`, envio Full pelo `logistic_type` da versao mais recente
do shipment, unidade de `api.ml_order_line_items`, chaveada por
(marca, item, variacao).
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from pipelines.common.db import DataMartSession
from pipelines.common.logging import get_logger
from pipelines.transforms import ml_full_stock as T

logger = get_logger(__name__)

OPERATIONAL_TZ = timezone(timedelta(hours=-3))
MARCAS = ("barbours", "kokeshi", "lescent", "rituaria")
TABELAS_FONTE = ("raw.ml_full_inventory_snapshot", "raw.ml_full_inventory_unavailable",
                 "raw.ml_full_inventory_listing")

SQL_PRESENCA = text("SELECT " + ", ".join(f"to_regclass('{t}') IS NOT NULL" for t in TABELAS_FONTE))

SQL_SNAPSHOT = text("""
SELECT brand, seller_id, inventory_id, read_status, available_quantity,
       not_available_quantity, total_coerente, run_id
  FROM raw.ml_full_inventory_snapshot
 WHERE ref_date = :ref_date AND brand = ANY(:marcas)
""")

SQL_STATUS = text("""
SELECT brand, seller_id, inventory_id, status_raw, quantity, run_id
  FROM raw.ml_full_inventory_unavailable
 WHERE ref_date = :ref_date AND brand = ANY(:marcas)
""")

SQL_PONTE = text("""
SELECT brand, seller_id, item_id, variation_id, inventory_id, logistic_type, listing_status, run_id
  FROM raw.ml_full_inventory_listing
 WHERE ref_date = :ref_date AND brand = ANY(:marcas)
""")

SQL_VENDAS_FULL = text("""
WITH pedidos AS (
    SELECT o.brand, o.order_id, o.shipping_id
      FROM api.ml_orders o
     WHERE o.status = 'paid'
       AND o.date_created >= :janela_inicio AND o.date_created < :janela_fim
       AND o.brand = ANY(:marcas)
),
envio AS (
    SELECT DISTINCT ON (s.brand, s.shipment_id) s.brand, s.shipment_id, s.logistic_type
      FROM api.ml_shipments s
     WHERE EXISTS (SELECT 1 FROM pedidos p WHERE p.brand = s.brand AND p.shipping_id = s.shipment_id)
     ORDER BY s.brand, s.shipment_id, s.updated_at DESC NULLS LAST
)
SELECT l.brand, l.item_id, COALESCE(l.variation_id::text, '0') AS variation_id,
       SUM(l.quantity)::bigint AS unidades
  FROM pedidos p
  JOIN envio e ON e.brand = p.brand AND e.shipment_id = p.shipping_id
  JOIN api.ml_order_line_items l ON l.brand = p.brand AND l.order_id = p.order_id
 WHERE e.logistic_type = 'fulfillment'
 GROUP BY l.brand, l.item_id, COALESCE(l.variation_id::text, '0')
""")


def janela_demanda(ref_date: date) -> tuple[date, date]:
    """[ref_date - 28, ref_date): dias COMPLETOS, sem o dia corrente."""
    return ref_date - timedelta(days=T.JANELA_VENDAS_DIAS), ref_date


def _hoje_brt(agora: datetime | None = None) -> date:
    return (agora or datetime.now(timezone.utc)).astimezone(OPERATIONAL_TZ).date()


def ler_fonte(conn, ref_date: date, marcas=MARCAS):
    """Le a fonte. Devolve None se alguma tabela `raw.ml_full_*` nao existe."""
    if not all(conn.execute(SQL_PRESENCA).fetchone()):
        return None
    p = {"ref_date": ref_date, "marcas": list(marcas)}
    inicio, fim = janela_demanda(ref_date)
    as_dicts = lambda rs: [dict(r._mapping) for r in rs]
    return {
        "snapshot": as_dicts(conn.execute(SQL_SNAPSHOT, p)),
        "status": as_dicts(conn.execute(SQL_STATUS, p)),
        "ponte": as_dicts(conn.execute(SQL_PONTE, p)),
        "vendas": {(r.brand, r.item_id, r.variation_id): int(r.unidades)
                   for r in conn.execute(SQL_VENDAS_FULL, {"janela_inicio": inicio,
                                                           "janela_fim": fim,
                                                           "marcas": list(marcas)})},
    }


def resumir(fonte, ref_date: date, marcas=MARCAS) -> dict:
    """So' agregados por marca. Nenhum id de inventario, anuncio ou conta sai."""
    inicio, fim = janela_demanda(ref_date)
    base = {
        "ref_date": str(ref_date), "modo": "dry_run", "grava": False,
        "janela_demanda": {"inicio_inclusivo": str(inicio), "fim_exclusivo": str(fim),
                           "dias_completos": T.JANELA_VENDAS_DIAS},
        "limites_PROVISORIOS": {"cobertura_baixa_dias": T.COBERTURA_BAIXA_DIAS_PROVISORIO,
                                "cobertura_excesso_dias": T.COBERTURA_EXCESSO_DIAS_PROVISORIO},
    }
    if fonte is None:
        return dict(base, estado="fonte_inexistente",
                    motivo="tabelas raw.ml_full_* ausentes (DDL nao aplicada): ausencia, nao estoque zero",
                    por_marca={m: {"estado": "sem_fonte"} for m in marcas})
    _linhas, resumo = T.indicadores(fonte["snapshot"], fonte["status"], fonte["ponte"], fonte["vendas"])
    por_marca = {}
    for m in marcas:
        if m in resumo and resumo[m]["inventarios"]:
            por_marca[m] = dict(resumo[m], estado="medido")
        else:
            # Marca sem fotografia no dia: sem dado, nunca zero.
            por_marca[m] = {"estado": "sem_fotografia_no_dia"}
    return dict(base, estado="medido" if any(v["estado"] == "medido" for v in por_marca.values())
                else "sem_fotografia_no_dia", por_marca=por_marca)


def run(ref_date: date | None = None, agora: datetime | None = None) -> dict:
    ref_date = ref_date or _hoje_brt(agora)
    dm = DataMartSession()
    try:
        dm.connection().connection.set_session(readonly=True)
        fonte = ler_fonte(dm.connection(), ref_date)
    finally:
        dm.rollback()
        dm.close()
    return resumir(fonte, ref_date)


def _json(o):
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o).__name__)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Estoque Full ML: indicadores em DRY-RUN (sem escrita).")
    ap.add_argument("--ref-date", type=date.fromisoformat, default=None)
    args = ap.parse_args(argv)
    print(json.dumps(run(args.ref_date), default=_json, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
