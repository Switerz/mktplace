# OPS-AUTOMATION-2 — recorrência sem depender do notebook

Data: 16/10/2026. Escopo: `full_daily` e `pma_refresh`, os dois pipelines que o
Task Scheduler da estação dispara. A Shopee manual (`shopee_manual_refresh`) e
o regional estão fora deste documento.

## 1. O que falhou e o que foi medido

Entre 13 e 16/10/2026 nenhuma execução agendada completou:

| Data | O que aconteceu | Evidência |
|---|---|---|
| 13/10 08:41 | as duas tarefas dispararam juntas; o `full_daily` morreu com `0xC000013A` | log de 0 byte; a tarefa `Interactive` abre uma janela de PowerShell, e fechá-la mata o processo antes do flush |
| 14 e 15/10 | nenhuma execução | estação suspensa de 13/10 21:19 a 16/10 00:53 (Kernel-Power 42/107); `WakeToRun=False`; `NumberOfMissedRuns=2` |
| 16/10 00:59:13 | as duas dispararam no mesmo segundo ao acordar; o `pma_refresh` pegou o lock e o `full_daily` saiu `BLOCKED` (0x1) | log com `RESUMO pma_refresh`; nenhum log do `full_daily` |
| 16/10 06:00 | nenhuma execução | reinício às 03:44 e standby de 05:01 a 10:11; com logon `Interactive`, a tarefa não roda sem sessão (hipótese, não provada) |

## 2. Inventário dos steps

Todos os steps leem o Data Mart (réplica de leitura, que exige VPN a partir da
estação) e/ou escrevem no Neon. **Nenhum depende de arquivo manual.**

| Step | Lê | Escreve | Lock | Timeout | Duração em 16/10 | Hoje no Airflow? |
|---|---|---|---|---|---|---|
| daily_ml | `gold.ml_gestao_diaria`, `gold.gmv` | `marts.fact_marketplace_daily_performance` | 918130002 (fato diária) | 900s | 11s | não |
| daily_tiktok | `raw.tiktok_shop_orders`, `gold.tiktok_brand_daily` | idem | 918130001 | 900s | 88s | não |
| gold_regional_incremental | `raw.ml_*`, `silver.stg_shopee_order_item_snapshots` (**removida**) | `gold.marketplace_region_daily` no **primário** do Data Mart, com secret de escrita próprio | — | 300s | falha, exit 4 | não; quebrado |
| sync_region_if_needed | `gold.marketplace_region_daily` | `marts.fact_marketplace_region_daily` (+backup) | — | 120s | SKIPPED | não |
| sync_produtos_ml | `gold.ml_produto_ranking` | `marts.fact_ml_produto_ranking` | 911120011 | 600s | <1min | **sim** (`marketplace_tower_ml_refresh`, pausada) |
| sync_produtos_tiktok | `gold.tiktok_product_daily` | `marts.fact_tiktok_product_daily` | 911120011 | 600s | <1min | não |
| serving_ml | `gold.ml_gestao_diaria` (view) | `marts.fact_ml_gestao_diaria` | 906120006 | 600s | ~1–2min | **sim** (ml_refresh) |
| serving_tiktok_brand | `gold.tiktok_brand_daily` | `marts.fact_tiktok_brand_content_daily` | 907120007 | 600s | <1min | não |
| serving_tiktok_creator | `gold.tiktok_creator_daily` | `marts.fact_tiktok_creator_daily` | 908120008 | 1800s | ~2min | não |
| serving_ml_cross_company | `gold.ml_cross_company_summary` | `marts.fact_ml_cross_company_summary` | 909120009 | 300s | 8s | **sim** (ml_refresh) |
| serving_tiktok_channel_efficiency | `gold.v_channel_efficiency` | `marts.fact_tiktok_channel_efficiency_daily` | 910120010 | 600s | 10s | não |
| tiktok_affiliate_cost_order_monthly | `silver.stg_tiktok_payments_by_order` (chega ~21:02 BRT) | `marts.fact_tiktok_affiliate_cost_order_monthly` | 912120012 | 300s | 39s | não |
| tiktok_order_discounts_daily | `raw.tiktok_shop_orders` | `marts.fact_tiktok_order_discounts_daily` | 912130013 | 300s | 44–130s; backfill até 298s | não |
| pma_ml | `silver.stg_ml_item_price_history/items/variations` | `marts.fact_marketplace_listing_price_daily` | 914120014 (`xact`, **espera**) | 900s | <1min | não |
| pma_shopee, pma_tiktok | `silver.stg_shopee_products/models`, `silver.stg_tiktok_inventory`, `gold.*_gobeauty` | `marts.fact_channel_offer_observation` | 917120017 (try, fail-fast) | 900s | <1min | não |
| health_check | `audit.*` e as fatos | — | — | 180s | <1min | — |

**Credenciais usadas hoje:**
- `DATABASE_URL`: Neon, dono das tabelas `marts`.
- `DATAMART_DATABASE_URL`: réplica. O role é superuser, um débito de segurança já registrado.
- Secret de escrita da Gold regional, num arquivo local.
- As flags `PMA_PUBLISH_SHOPEE_ENABLED` e `PMA_PUBLISH_TIKTOK_ENABLED` são **variáveis de ambiente do usuário Windows**, não do `.env`.

**Connections equivalentes no Airflow de produção:**
- `data_mart`: corporativa, usada por cerca de 180 DAGs.
- `neon_marts`: existe em produção, porque a `marketplace_tower_ml_refresh` publicou com ela em 23/09.

## 3. O lock compartilhado

O `pma_refresh` usa o lock lógico `full_daily` (`scripts/run_task.ps1`). O
comentário original justifica isso dizendo que o PMA lê as mesmas fontes que o
`full_daily` carrega. **No código, isso não se confirma:** as tabelas lidas e
escritas pelos dois pipelines são disjuntas (seção 2), e os advisory locks
também. O único ponto em comum é o append em `audit.source_sync_run`.

O lock tem, ainda assim, uma função real:
- impedir duas execuções do **PMA** antes de qualquer conexão;
- o sync de preço do ML usa `pg_advisory_xact_lock`, que espera em vez de desistir, e sem o lock lógico uma segunda execução ficaria pendurada até o timeout;
- o `full_daily` e o PMA disputam a mesma réplica, e concorrência ali aumenta o `conflict with recovery` (medido nesta frente).

**Decisão: o lock não foi separado.** O defeito era a *disputa* por ele, e não
a existência dele. Por isso as duas cargas viraram uma sequência.

## 4. Arquitetura

### Atual

```
Task Scheduler (estação, logon Interactive, sem WakeToRun)
 ├─ 06:00 mktplace_full_daily  ─┐  mesmo lock lógico `full_daily`
 └─ 07:30 mktplace_pma_refresh ─┘  → disputa quando as duas disparam juntas
        └─ run_task.ps1 → run_with_lock.ps1 → orchestrate.py → steps
              lê Data Mart (VPN) ─────────► escreve Neon

Airflow produção: marketplace_tower_ml_refresh (3 steps ML), PAUSADA
```

### Alvo, em três etapas

1. **Já neste PR (Windows):** uma única TaskKey `daily_chain` que roda o `full_daily` e depois o PMA, no mesmo processo e sob o mesmo lock.
   - Tarefa única `mktplace_daily_chain` (template em `infra/windows/mktplace_daily_chain.task.xml`), com logon S4U, `WakeToRun`, `StartWhenAvailable`, `IgnoreNew` e sem janela.
   - Horário às 10:15 BRT, depois dos Ads de D-1 do ML (09:00–09:02) e antes da cobrança de D-1 pelo health check (11:00).
2. **Airflow, só com ação administrativa:**
   - Despausar a `marketplace_tower_ml_refresh` (10:00 BRT).
   - Depois de execuções naturais com sucesso, tirar `serving_ml`, `serving_ml_cross_company` e `sync_produtos_ml` da cadeia Windows.
   - Até isso acontecer, as duas instâncias escrevem as mesmas tabelas sob as mesmas chaves de advisory lock: não corrompe, mas um lado pode esperar até o timeout. **Não despausar a ml_refresh sem ajustar a cadeia.**
3. **Airflow, nova DAG `marketplace_tower_daily`:** TikTok diário, produtos TikTok, servings TikTok, channel efficiency, afiliados, descontos e PMA. Pré-requisitos ainda abertos:
   - a imagem de **produção** (`main` do Airflow) não instala `mktplace-pipelines`;
   - as flags do PMA precisam virar Variables ou configuração da DAG;
   - o backfill semanal e o full mensal dos descontos precisam de DAGs próprias (orçamentos de 298s e 246s+).

   Depois disso, a estação deixa de ser necessária para tudo, exceto a Shopee manual (arquivos locais) e, enquanto estiver quebrado, o regional.

## 5. Instalação da etapa 1 (ação administrativa, não executada)

Pré-requisito: este PR mesclado e o runner avançado por fast-forward.

1. Desabilitar, sem apagar, as tarefas `mktplace_full_daily` e `mktplace_pma_refresh`.
2. Registrar a tarefa nova a partir do template, num PowerShell elevado:
   `Register-ScheduledTask -TaskName mktplace_daily_chain -Xml (Get-Content -Raw infra\windows\mktplace_daily_chain.task.xml)`.
3. Rollback: desabilitar a tarefa nova e reabilitar as duas antigas.

**Riscos a provar na primeira execução real:**
- **Flags do PMA:** uma tarefa S4U pode não carregar o ambiente do usuário. Se não carregar, Shopee e TikTok saem `REFUSED` por flag desligada.
- **VPN:** se ela cair com a estação suspensa, os steps saem `BLOCKED` no preflight, sem escrita.

## 6. Critérios de validação (ao menos uma execução agendada real)

1. A tarefa disparou sozinha, sem login interativo, e `LastTaskResult` está registrado.
2. Existe exatamente um log `full_daily_<data>_*_stdout.log`, não vazio, contendo `RESUMO daily_chain` e `STATUS PMA`.
3. Os steps `daily_*` e os servings estão em `SUCCESS`, com as fatos em D-1 (creator em D-2).
4. Os três canais do PMA estão em `SUCCESS`. Se aparecer `REFUSED` por flag, o risco das flags se confirmou.
5. Os descontos estão em `SUCCESS`, em incremental (ou backfill na semana em que estiver devido).
6. Nenhuma auditoria nova em `running`, zero advisory lock e nenhum arquivo de lock.
7. Nenhum outro log no mesmo dia, ou seja, nenhum segundo disparo.

## 7. Procedimento temporário até a instalação

As tarefas atuais continuam ativas e com os mesmos riscos. Quando o resumo do
dia não mostrar o `full_daily` completo, rode **uma vez**, depois das 10:15 e
pelo wrapper:
`powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts\run_task.ps1 -TaskKey daily_chain`
(depois do merge) ou, antes dele, `-TaskKey full_daily` seguido de
`-TaskKey pma_refresh`, nunca ao mesmo tempo.
