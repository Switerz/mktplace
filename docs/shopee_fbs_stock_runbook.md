# Runbook — Estoque Full Shopee diário (`/estoque-full`)

STOCK-DAILY-SHOPEE-TIKTOK-1 (2026-10-21). Publicador: `pipelines/sync_shopee_fbs_stock_daily.py`.
Operação: `pipelines/ops/shopee_fbs_stock_ops.py`.

## O que é

Fotografia diária do estoque físico **vendável** dentro do Full (FBS) da Shopee, para
apice, barbours, lescent e rituaria. Kokeshi **não é coberta**: não está na esteira API
da Shopee, e a ausência dela é falta de fonte, nunca estoque zero.

- Estoque Full = `SUM(stock_info.shopee_stock[].stock) WHERE if_saleable = true`
  (conciliado com o "Total Vendável" do Seller Center em 24/09/2026).
- `seller_stock`, `reserved_stock` e `summary_available_stock` são só contexto.
- Kits saem como `KIT_NAO_CONCILIADO` e fora dos totais operacionais.
- Destino: `marts.fact_shopee_fbs_stock_daily` (produto) e
  `marts.fact_shopee_fbs_stock_location_daily` (CD). Migration 021.
- Publicação: DELETE + INSERT do `ref_date` numa transação, advisory lock
  `916140020`, reconciliação produto × CD antes do commit, auditoria
  `audit.source_sync_run` com `source_name = 'shopee_fbs_stock_daily'`.

## Agendamento

Step `shopee_fbs_stock` da DAG `marketplace_tower_daily_chain` (10:15 BRT), no
repositório `goca-se/airflow`. A fonte é a DAG `shopee_catalog_etl` (06:00 BRT), mas a
ordem entre as duas **não** depende do horário: depende do portão de frescor.

### Portão de frescor (primeira leitura do run)

Corte = meia-noite BRT do `ref_date`. Por conta esperada, recusa se:

| Condição | Por quê |
|---|---|
| zero produto FBS no catálogo | ausência não vira fotografia sem a conta |
| produto FBS sem `stock_info` | sairia da fotografia em silêncio |
| algum produto FBS capturado antes do corte | catálogo de ontem com data de hoje |
| linha da Silver fora da versão da raw | o dbt do catálogo não rodou sobre o retrato |
| última ingestão de pedidos antes do corte | D-1 incompleto infla a cobertura |

O portão e as leituras rodam no **mesmo** snapshot (`REPEATABLE READ`, somente leitura).

### Trava de regime não conciliado

Conta com estoque físico no Full e **zero** unidades com `if_saleable = true` bloqueia a
carga inteira. Motivo medido em 21/10/2026: a Barbours veio com 387/387 entradas
`if_saleable = false` (119.163 unidades nos CDs) enquanto os mesmos itens seguiam
vendendo 114–486 unidades/dia. Publicar mostraria a conta zerada e em ruptura. Não há
flag para pular: resolver é decisão de contrato (conciliar no Seller Center).

## Códigos de saída

| Exit | Significado | Repetir? |
|---|---|---|
| 0 | publicado (ou dry-run concluído) | — |
| 1 | falha de contrato, trava de regime ou banco; nada publicado | não: repete igual |
| 3 | advisory lock ocupado; nada escrito | sim |
| 4 | fonte não pronta (portão); nada escrito, nenhuma auditoria aberta | sim, mais tarde |

## Alertas

- Airflow: task `shopee_fbs_stock` falha (exit 1, ou 3/4 depois das novas tentativas).
- `pipelines.ops.health_check`: fonte `shopee_fbs_stock_daily`, crítica, 30 h.
- Tela: `dias_desde_a_fotografia` no bloco de frescor.

## Comandos

Todos a partir de um checkout de `main` com o `.env` de produção carregado. Na estação
Windows o Python do sistema está quebrado; use `uv`:

```
uv run --python 3.12 --with psycopg2-binary --with python-dotenv --with "sqlalchemy<2.1" \
  --with pydantic-settings --with tzdata --no-project python -m <módulo> <args>
```

| Pergunta | Módulo e argumentos | Escreve? |
|---|---|---|
| Dá para publicar agora? | `pipelines.ops.shopee_fbs_stock_ops preflight` | não |
| Ensaio completo | `pipelines.sync_shopee_fbs_stock_daily` | não |
| Primeira execução controlada | `pipelines.ops.shopee_fbs_stock_ops primeira-execucao --confirmo-escrita-em-producao` | **sim** |
| O publicado bate com a fonte? | `pipelines.ops.shopee_fbs_stock_ops reconciliar [--ref-date AAAA-MM-DD]` | não |
| Desfazer um run | `pipelines.ops.shopee_fbs_stock_ops reverter --ref-date D --run-id R [--apply]` | só com `--apply` |

`primeira-execucao` faz preflight → publica → reconcilia, e imprime o comando exato de
rollback com o `run_id`.

### Reconciliação

SQL independente, direto do JSON da raw (não reusa o publicador). Checagens:

- `produtos_batem_com_a_fonte` (vendável, total e kit por produto);
- `localizacoes_somam_o_produto` e `localizacoes_batem_com_a_fonte`;
- `vendavel_total_bate`;
- `kits_isolados`;
- `classificacao_recalculada_bate` (ruptura, baixo, excesso, sem giro, cobertura);
- `zero_locks` e `zero_auditorias_presas`;
- `um_run_por_fotografia`.

A fonte é recapturada às 06:00 BRT do dia seguinte. Se ela mudou depois da fotografia,
as checagens contra a fonte saem `NAO_APLICAVEL`, nunca `OK`: reconcilie no mesmo dia.

### Rollback

`reverter` apaga só as linhas de (`ref_date`, `source_run_id`), sob o mesmo advisory
lock, e recusa se o dia tiver linha de outro run. Depois dele a tela volta para a
fotografia anterior, com a data dela. Uma fotografia do mesmo dia sobrescrita pelo run
não volta. A linha de auditoria do run fica como estava.

## Flags

Já ligadas em produção (medido em 21/10/2026): `SHOPEE_FBS_STOCK_ENABLED` no Render
(`/api/v1/performance/shopee-fbs-estoque` responde `status: ok`) e
`NEXT_PUBLIC_SHOPEE_FBS_STOCK_ENABLED` na Vercel (`/estoque-full` renderiza). Desligar a
tela: a flag da Vercel é de build, exige rebuild.
