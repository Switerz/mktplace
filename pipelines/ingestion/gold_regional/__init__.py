"""
Gate 6A — aplicação da Gold regional (`gold.marketplace_region_daily`) no
Data Mart primary.

Nada neste pacote escreve em banco a menos que `write_conn.load_write_secret`
tenha validado `.env.gold-write.local` (fora do repositório, nunca lido por
outro módulo) e o preflight de `write_conn.run_preflight` tenha passado.
"""
