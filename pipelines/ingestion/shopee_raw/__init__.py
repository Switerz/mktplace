"""
Fase Raw Shopee 1 — inventário, contrato e leitura read-only dos exports
locais da Shopee (orders, shop-stats, ads).

Nada neste pacote escreve em banco de dados. Ver `pipelines/ingestion/load_shopee_raw.py`
para o CLI que orquestra `--inventory` / `--dry-run` / `--apply` (bloqueado nesta fase).
"""
