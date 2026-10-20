"""Remessa Eli -> Full do ML (NF do Bling): agregado em DRY-RUN, somente leitura.

Le `silver.bling_nfes` no Data Mart em sessao READ ONLY, classifica cada item
(`pipelines.transforms.bling_remessa_full`) e imprime so' agregados por conta
do Bling x classe x situacao da NF. Documento, nome e SKU do destinatario sao
usados apenas em memoria para classificar e nunca saem.

NAO grava, NAO tem `--apply`, NAO tem tabela de destino (exige migration em
gate proprio) e NAO casa NF com `inventory_id` / `inbound_reception`.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from pipelines.common.db import DataMartSession
from pipelines.transforms import bling_remessa_full as T

OPERATIONAL_TZ = timezone(timedelta(hours=-3))
JANELA_PADRAO_DIAS = 30
CONTAS = ("bb_varejo_ltda", "kokeshi_mkt_place", "lescent", "rituaria")

#: So' as colunas necessarias. Endereco, telefone, e-mail e afins NAO sao lidos.
SQL_ITENS = text("""
SELECT marca AS conta, id AS nf_id, data_emissao::date AS data, situacao, tipo,
       item_cfop AS cfop, cpf AS documento, nome_contato AS nome,
       item_codigo AS sku, item_quantidade AS quantidade
  FROM silver.bling_nfes
 WHERE data_emissao >= :inicio AND data_emissao < :fim
   AND marca = ANY(:contas)
   AND item_cfop IN ('5949', '6949')
""")


def run(fim: date | None = None, dias: int = JANELA_PADRAO_DIAS) -> dict:
    fim = fim or datetime.now(timezone.utc).astimezone(OPERATIONAL_TZ).date()
    inicio = fim - timedelta(days=dias)
    dm = DataMartSession()
    try:
        dm.connection().connection.set_session(readonly=True)
        itens = [dict(r._mapping) for r in dm.connection().execute(
            SQL_ITENS, {"inicio": inicio, "fim": fim, "contas": list(CONTAS)})]
    finally:
        dm.rollback()
        dm.close()
    return {
        "modo": "dry_run", "grava": False,
        "janela": {"inicio_inclusivo": str(inicio), "fim_exclusivo": str(fim)},
        "recorte": "itens com CFOP 5949/6949 (outras saidas); venda fica fora por construcao",
        "documentos_ml_confirmados": len(T.DOCUMENTOS_ML_CONFIRMADOS),
        "por_conta": T.agregar(itens),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Remessa Eli -> Full ML (Bling): DRY-RUN, sem escrita.")
    ap.add_argument("--fim", type=date.fromisoformat, default=None)
    ap.add_argument("--dias", type=int, default=JANELA_PADRAO_DIAS)
    a = ap.parse_args(argv)
    print(json.dumps(run(a.fim, a.dias), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
