# Checkpoint de qualidade de dados dos marketplaces — Gate DQ1

**Data:** 05/08/2026
**Corte da fonte:** 2026-08-05 20:00 UTC / 17:00 São Paulo
**Natureza:** auditoria exclusivamente read-only e documental. Zero escrita, zero pipeline, zero deploy, zero commit.
**Rodada:** única (sem DQ1.1/DQ1.2).

---

## 1. Objetivo

Determinar se os dados dos três marketplaces estão completos, atualizados, reconciliados entre fonte, Neon e API de produção, calculados com a semântica correta para cancelamentos/devoluções/reembolsos, e confiáveis para as telas atuais da Torre.

## 2. Cadeia real de dados

A Torre **não** chama as APIs do Mercado Livre, TikTok ou Shopee. As APIs dos canais alimentam o Data Mart corporativo (RDS AWS) a montante, e a Torre consome o Data Mart. Cadeia confirmada por leitura de código e das definições reais no banco:

| Canal | Origem upstream | Tabela consumida pelo pipeline | Transformação | Tabela no Neon | Endpoint | Páginas |
|---|---|---|---|---|---|---|
| **Mercado Livre** | API ML → `raw.ml_orders` (Data Mart, fora deste repo) | `gold.ml_gestao_diaria` (**view**) | `pipelines/transforms/ml_gestao_diaria.py` | `marts.fact_marketplace_daily_performance` (mkt 2) | `/overview`, `/brands`, `/monthly`, `/daily`, `/canais`, `/financeiro`, `/quality` | Gerencial, Canais, Marca, Financeiro, Qualidade, Pedidos |
| **TikTok Shop** | API TikTok → `raw.tiktok_shop_orders` + `gold.tiktok_brand_daily` | `raw.tiktok_shop_orders` (GMV) `LEFT JOIN gold.tiktok_brand_daily` (demais campos) | `pipelines/transforms/tiktok_brand_daily.py` | idem (mkt 1) | idem | idem |
| **Shopee** | **Exports manuais XLSX/CSV locais** (`SHOPEE_DATA_PATH`) — API oficial indisponível | arquivos `Order.all*.xlsx` + shop-stats + ads | `_parser.py` / `_parser_shop_stats.py` / `_parser_ads.py` → 3 transforms | idem (mkt 3) | idem | idem |
| **Regiões** (ML + Shopee) | `raw.ml_orders` + `raw.ml_shipments` / `silver.stg_shopee_order_item_snapshots` | `gold.marketplace_region_daily` | `pipelines/sync_region_daily.py` | `marts.fact_marketplace_region_daily` | `/regioes/*` | Regiões |
| **Produtos** | `gold.ml_produto_ranking` / `gold.tiktok_product_daily` / PG local Shopee | idem | `pipelines/sync_produtos.py` | `fact_ml_produto_ranking`, `fact_tiktok_product_daily`, `fact_shopee_product_monthly` | `/produtos/*` | Produtos |

**Grão e chave.** Fato diário: `(date, loja_id, marketplace_id)` — UNIQUE, 0 duplicidades em toda a tabela. Regional: `(date, marketplace_id, loja_id, uf)`. Produtos Shopee: `(ref_month, brand, sku_ref_key)`.

**Timezone/competência.** ML: `date_created::date` (criação do pedido). TikTok: `created_at::date`. Shopee Daily: data do relatório shop-stats. Nenhuma conversão explícita de timezone é aplicada no pipeline — os timestamps chegam do Data Mart já normalizados a montante. **Dívida:** o timezone efetivo de corte de dia não está documentado em nenhum contrato.

## 3. Fonte de verdade por canal

| Canal | Fonte de verdade do GMV | Observação |
|---|---|---|
| ML | `gold.ml_gestao_diaria` (view sobre `raw.ml_orders`) | Fonte mutável retroativamente |
| TikTok | `raw.tiktok_shop_orders` (não a Gold) | Gold é fonte só dos campos não-GMV |
| Shopee | shop-stats (relatório da própria Shopee), via arquivo local | `PATCH_SHOP_STATS_SQL` roda por último e sobrescreve `gmv` |

## 4. Contrato de GMV por canal

### Mercado Livre — regra confirmada hoje na definição real da view

```sql
SUM(CASE WHEN o.status = 'paid' THEN o.total_amount ELSE 0 END) AS gmv
-- FROM raw.ml_orders, GROUP BY date_created::date, brand
-- nenhum JOIN com pagamentos, claims ou devoluções entra no GMV
```

**Idêntica à decisão documentada no Gate R3** (21/07/2026, `analise_reconciliacao_xlsx_torre_jan_maio_2026.md` §12) — nenhuma deriva de regra desde então. As 4 variantes avaliadas no R3 continuam rejeitadas, e as premissas foram re-verificadas nesta auditoria:

- `shipping_cost` e `taxes_amount` = **0 em 100%** dos 367.909 pedidos `paid` de jan–ago → `total_amount` não carrega frete nem imposto. Confirma R3.
- `paid_amount` > `total_amount` em **25.612 de 367.909 (7,0%)**, nunca abaixo; soma R$ 31.743.849,00 vs R$ 31.465.266,27 (**+0,89%**). Consistente com juros de parcelamento; corretamente **não** usado no GMV.
- `date_closed` populado em 100%; muda o **dia** em 1.486 pedidos (0,40%) e o **mês** em 129 (0,035%) → escolha de competência é imaterial. Confirma R3.
- **Zero** pedidos `paid` sem registro de pagamento.

### TikTok — regra vigente

```sql
SUM(CASE WHEN order_status IN ('COMPLETED','DELIVERED','IN_TRANSIT') THEN sub_total ELSE 0 END)
-- FROM raw.tiktok_shop_orders, dedup DISTINCT ON (order_id), GROUP BY created_at::date, brand
```

`orders` **não** é a contagem elegível da Raw: o contrato é `COALESCE(gold.tiktok_brand_daily.orders, raw.orders_eligible)`. As duas populações divergem materialmente (jul/2026: 203.506 vs 189.621 = 13.885 pedidos). Reconciliar `orders` contra a contagem crua produz centenas de falsas divergências com GMV em delta R$ 0,00.

### Shopee — três contratos distintos, nunca intercambiáveis

| Contrato | Fórmula | Julho/2026 |
|---|---|---|
| **A. Daily / shop-stats** (autoritativo no fato diário) | `Vendas (BRL) − Vendas Canceladas − Vendas Devolvidas/Reembolsadas` | R$ 7.311.536,51 |
| **B. Gold regional / order-level** | `SUM(order_amount)` com `order_status NOT ILIKE '%cancel%'` — **não** subtrai devolução | R$ 7.617.662,26 |
| **C. Produtos (mensal)** | Só pedidos `Concluído`, agregado por SKU | R$ 5.512.907,44 |

**Por que divergem, legitimamente:** (A) usa o líquido do relatório da Shopee; (B) usa `order_amount` a nível de pedido (inclui frete pago pelo comprador) e mantém devoluções dentro do GMV — R$ 41.182,18 em julho; (C) só conta pedidos concluídos, excluindo tudo que ainda está em trânsito. Diferença A→B em julho: **+R$ 306.125,75 (+4,2%)**. Diferença A→C: **−R$ 1.798.629,07 (−24,6%)**.

Nota técnica: pedidos cancelados na Shopee chegam com `order_amount = 0` em **88.496 de 88.497** casos (soma total R$ 38,56) — o filtro `NOT ILIKE '%cancel%'` do contrato B é, na prática, um no-op sobre valor. O valor cancelado só é recuperável pelo shop-stats.

## 5. Tratamento de status

### Mercado Livre (jan–ago/2026, 4 marcas)

| Status | Entra no GMV | Comportamento |
|---|---|---|
| `paid` | **sim** | única inclusão |
| `cancelled` | não | reembolso total aparece aqui; não existe status `refunded` isolado |
| `partially_refunded` | não | **o pedido inteiro é excluído**, não só a parcela reembolsada |
| `pending_cancel` | não | volume marginal (≤3/mês) |

Impacto das exclusões por mês: **2,58% a 5,45%** do valor bruto.

| Mês | GMV incluído | Cancelado | Parcialmente reembolsado | Pending | % excluído |
|---|---:|---:|---:|---:|---:|
| 01 | 2.573.457,39 | 126.963,85 | 3.129,64 | 79,10 | 4,81% |
| 02 | 3.056.405,46 | 143.927,88 | 5.119,14 | 0,00 | 4,65% |
| 03 | 4.922.252,28 | 278.956,89 | 4.476,36 | 24,40 | 5,45% |
| 04 | 4.396.549,71 | 186.147,62 | 3.724,73 | 204,40 | 4,14% |
| 05 | 5.278.813,03 | 248.568,01 | 1.588,04 | 72,70 | 4,53% |
| 06 | 4.567.893,85 | 177.176,80 | 1.758,86 | 73,88 | 3,77% |
| 07 | 5.778.102,46 | 229.510,48 | 4.761,85 | 0,00 | 3,90% |
| 08 (parcial) | 891.792,09 | 23.475,36 | 183,46 | 0,00 | 2,58% |

Status de pagamento (pedidos `paid`): `approved` 371.338 · `rejected` 5.067 · `cancelled` 2.447 · `charged_back` **165** · `in_mediation` 156. Os 165 pedidos com pagamento `refunded`/`charged_back` **continuam no GMV** (R$ 14.802,99 = 0,047%) — dívida já registrada no Gate R3, reconfirmada.

### TikTok (jan–ago/2026, 5 marcas)

| Status | Elegível | jul/2026 (pedidos / subtotal) | ago/2026 (pedidos / subtotal) |
|---|---|---|---|
| `COMPLETED` | **sim** | 7.501 / 379.195,96 | 1 / 38,70 |
| `DELIVERED` | **sim** | 173.885 / 8.625.096,83 | 2.330 / 115.345,67 |
| `IN_TRANSIT` | **sim** | 8.235 / 450.172,11 | 16.964 / 908.365,03 |
| `CANCELLED` | não (conhecido) | 45.140 / 2.254.611,45 | 5.403 / 282.427,72 |
| `UNPAID` | não — **fora da allowlist conhecida** | 2.490 / 112.605,85 | 1.414 / 71.538,39 |
| `AWAITING_COLLECTION` | não — **fora da allowlist conhecida** | 250 / 11.782,05 | 8.554 / 440.056,30 |
| `AWAITING_SHIPMENT` | não — **fora da allowlist conhecida** | 6 / 220,48 | 41 / 2.384,30 |
| `ON_HOLD` | não — **fora da allowlist conhecida** | — | 9 / 993,58 |

`orders_unexpected_status` (status nulo ou fora dos 4 conhecidos), por mês:

| Mês | Pedidos | Subtotal excluído | % do subtotal do mês |
|---|---:|---:|---:|
| 01–05 | **0** | 0,00 | 0,00% |
| 06 | 123 | 6.391,27 | 0,06% |
| 07 | 2.746 | 124.608,38 | 1,05% |
| 08 (parcial) | **10.024** | **515.346,17** | **24,3%** |

`sub_total IS NULL` em pedido elegível: **0** em todos os meses (o conector bloquearia a carga se houvesse).

**Mutabilidade comprovada.** `raw.tiktok_shop_order_status_log` (3,6M linhas) mostra que o pedido muda de status depois da primeira captura: 736.125 pedidos com 1 transição, 258.959 com 2, **557.007 com 3**, 148.415 com 4, 7.533 com 5–6. Transições que **retiram** valor do GMV já contado: `IN_TRANSIT → CANCELLED` **12.512 pedidos**. Transições que **acrescentam**: `AWAITING_COLLECTION → IN_TRANSIT` 870.748, `AWAITING_COLLECTION → COMPLETED` 333.265, entre outras. Pedidos de meses fechados seguem sendo atualizados pela plataforma: 50.320 de junho e 50.710 de julho tiveram `updated_at_tiktok` nos últimos 7 dias.

**Maturação dos últimos dias, medida:**

| Dia | GMV elegível | Subtotal total | % maduro | Gold |
|---|---:|---:|---:|---|
| 27–31/07 | — | — | 76,6%–79,2% | 5 marcas |
| 01/08 | 327.442,25 | 408.630,51 | 80,1% | 5 marcas |
| 02/08 | 338.068,02 | 425.131,10 | 79,5% | 5 marcas |
| 03/08 | 242.921,02 | 371.107,89 | 65,5% | 5 marcas |
| 04/08 | 111.443,31 | 380.226,08 | **29,3%** | 5 marcas |
| 05/08 | 3.874,80 | 237.501,99 | **1,6%** | **ausente** |

O platô maduro é ~78–80% (o resto é `CANCELLED`, exclusão permanente). Aplicando 79% como referência, **04/08 está subestimado em ~R$ 189 mil e 05/08 em ~R$ 184 mil** — cerca de **R$ 373 mil** nos dois dias correntes. Em 05/08 a Gold ainda não tem linha, então `orders` cai no fallback da Raw e `units_sold` fica NULL nas 5 marcas.

### Shopee

Order-level (`silver.stg_shopee_order_item_snapshots`): `Concluído` 512.008 · `Cancelado` 88.497 · `Entregue` 22.058 · `Enviado` 16.961 · `A Enviar` 7.503 · `Não pago` 867 · `Pedido Recebido` 64 · `Order Received` 21. **Além disso, ~30 variantes de texto livre** do tipo `"O comprador pode pedir uma devolução até 2026-08-05"` (2.966 pedidos nessa única variante) ocupam o campo `order_status`. `return_refund_status`: `Solicitação aprovada` 6.234 · `Devolução em Andamento` 70 · `Devolução Concluída` 69 · `Contestação resolvida` 54 · `Contestação Pendente` 2.

## 6. Cobertura por mês

Meses fechados (jan–jul) e mês aberto (ago) separados. Contratos: ML 4 marcas (Ápice é `not_applicable` — **zero linhas na fonte**, confirmado), TikTok 5, Shopee 5.

| Mês | TikTok (dias/marcas) | GMV TikTok | ML (dias/marcas) | GMV ML | Shopee (dias/marcas) | GMV Shopee |
|---|---|---:|---|---:|---|---:|
| 01 | 31/31 · 5 | 7.421.563,24 | 31/31 · 4 | 2.573.358,89 | 31/31 · 5 | 1.784.521,68 |
| 02 | 28/28 · 5 | 11.299.111,47 | 28/28 · 4 | 3.056.373,32 | 28/28 · 5 | 2.721.730,03 |
| 03 | 31/31 · 5 | 13.844.608,16 | 31/31 · 4 | 4.921.882,27 | 31/31 · 5 | 4.495.061,52 |
| 04 | 30/30 · 5 | 11.774.142,27 | 30/30 · 4 | 4.396.332,01 | 30/30 · 5 | 5.458.898,53 |
| 05 | 31/31 · 5 | 12.498.700,47 | 31/31 · 4 | 5.281.736,32 | 31/31 · 5 | 5.612.119,71 |
| 06 | 30/30 · 5 | 9.060.774,62 | 30/30 · 4 | 4.567.893,85 | 30/30 · 5 | 6.363.056,44 |
| 07 | 31/31 · 5 | 9.454.502,44 | 31/31 · 4 | 5.778.258,36 | 31/31 · 5 | 7.311.536,51 |
| **08 (aberto)** | 5 dias · 5 | 1.006.939,69 | 5 dias · 4 | 880.078,11 | **4 dias** · 5 | 677.164,23 |

**Uma única data interna ausente em todos os meses fechados:** ML/barbours em **2026-01-20**. Verificado na fonte: `gold.ml_gestao_diaria` tem 0 linhas e `raw.ml_orders` tem 0 pedidos de barbours nesse dia; barbours tem 30 dos 31 dias de janeiro na própria fonte. **Ausência legítima → `not_applicable`, não defeito de completude.**

**Integridade da chave:** 0 duplicidades, 0 nulos em `date`/`loja_id`/`marketplace_id`/`empresa_id`/`gmv`/`orders`, 0 GMV negativo, 0 `loja_id`/`marketplace_id` fora do domínio. Único nulo: `units_sold` em 5 linhas (TikTok 05/08 — dia sem Gold).

**Atualização (`MAX(ingested_at)` UTC):** TikTok/ML jan–mai em 22/07 (backfill histórico); jun/jul/ago em 05/08. Shopee jan–mai em 22/07; jun em 04/08 18:59; jul em 04/08 21:58; ago em 05/08 14:27.

**Mês aberto — atraso real vs maturação normal:** Shopee para em 04/08 porque a fonte é export manual e o último arquivo cobre 04/08 (`silver.stg_shopee_shop_stats` também para em 04/08) — **maturação da fonte, não atraso do pipeline**. ML e TikTok têm 05/08, mas o dia corrente não está fechado; no TikTok, especificamente, ele está estruturalmente vazio (§5).

## 7. Reconciliação fonte × Neon × API

### Neon × API de produção — **paridade total**

Amostras determinísticas, todos os canais, todas as marcas, julho completo e agosto até o corte, primeiro e último dia de cada série:

- **`/daily`**: 14 combinações canal × marca (5 TikTok + 4 ML + 5 Shopee) × 2 janelas = **28 séries, todas OK** em GMV e pedidos, dia a dia. Zero divergência.
- **`/overview`**: julho GMV R$ 22.544.297,31 e 401.065 pedidos, agosto R$ 2.564.182,03 e 47.915 — **delta R$ 0,00 e 0 pedidos**; `tiktok_gmv`/`ml_gmv`/`shopee_gmv`, `ad_spend` e `avg_ticket` também exatos.
- **`/monthly`**: jan–ago, por marca, nos 3 canais — todos **OK**.
- **`/canais`**: as 14 linhas marca × canal somam exatamente o GMV e os pedidos do Neon por canal (TikTok 9.454.502,44 · ML 5.778.258,36 · Shopee 7.311.536,51).
- **`/quality`**: `ml_cancel_rate_pct` 4,07 e `shopee_cancel_rate_pct` 13,99 / `shopee_return_rate_pct` 1,0 reproduzem o cálculo direto no Neon. `tiktok_cancel_rate` e `tiktok_problem_rate` retornam **`None`** (ver §10, achado 1).
- `health-datasource`: `active_source = neon_marts`, `db_connected = true`.

### Fonte × Neon — meses fechados

| Mês | ML fonte | ML Neon | Δ | TikTok fonte | TikTok Neon | Δ |
|---|---:|---:|---:|---:|---:|---:|
| 01 | 2.573.457,39 | 2.573.358,89 | −98,50 | 7.421.563,24 | 7.421.563,24 | **0,00** |
| 02 | 3.056.405,46 | 3.056.373,32 | −32,14 | 11.299.111,47 | 11.299.111,47 | **0,00** |
| 03 | 4.922.252,28 | 4.921.882,27 | −370,01 | 13.844.608,16 | 13.844.608,16 | **0,00** |
| 04 | 4.396.549,71 | 4.396.332,01 | −217,70 | 11.774.142,27 | 11.774.142,27 | **0,00** |
| 05 | 5.278.813,03 | 5.281.736,32 | +2.923,29 | 12.498.644,11 | 12.498.700,47 | +56,36 |
| 06 | 4.567.893,85 | 4.567.893,85 | **0,00** | 9.060.774,62 | 9.060.774,62 | **0,00** |
| 07 | 5.778.102,46 | 5.778.258,36 | +155,90 | 9.454.464,90 | 9.454.502,44 | +37,54 |

Shopee, contra o shop-stats **deduplicado** no Data Mart: jan, fev, mar, abr, jun, jul e ago em **paridade exata**; mai difere R$ 280,45 (+0,005%).

**Achado estrutural que explica esses resíduos.** Julho de ML e TikTok foi carregado em 05/08 às 18:51 e 18:53 UTC com paridade R$ 0,00 comprovada; **~1 hora depois**, na mesma sessão, a fonte já divergia em R$ 155,90 (ML) e R$ 37,54 (TikTok). A fonte reconstrói o estado *atual* dos pedidos, não uma fotografia do fechamento: um pedido pago em janeiro e cancelado hoje deixa de contar no GMV de janeiro **retroativamente**. Logo, **um mês fechado reconcilia apenas no instante da carga e deriva continuamente depois** — os resíduos de jan–mai são deriva acumulada desde 22/07, não erro de pipeline. O mesmo já estava registrado no Gate R3 §12.2 como viés estrutural; esta auditoria o quantificou pela primeira vez.

### Regional (Regiões) × fato diário — divergência material entre telas

| Mês | ML regional | ML diário | Δ | Cobertura UF ML | Shopee regional | Shopee diário | Δ |
|---|---:|---:|---:|---|---:|---:|---:|
| 01 | 2.573.358,89 | 2.573.358,89 | 0,00 | 69,4% | 1.868.229,14 | 1.784.521,68 | +83.707,46 |
| 02 | 3.056.501,02 | 3.056.373,32 | +127,70 | 64,8% | 2.887.595,61 | 2.721.730,03 | +165.865,58 |
| 03 | 3.998.968,76 | 4.921.882,27 | **−922.913,51** | **52,9%** | 4.702.232,82 | 4.495.061,52 | +207.171,30 |
| 04 | 3.475.145,20 | 4.396.332,01 | **−921.186,81** | **56,5%** | 6.108.282,44 | 5.458.898,53 | +649.383,91 |
| 05 | 5.148.119,64 | 5.281.736,32 | −133.616,68 | 100,0% | 5.769.030,48 | 5.612.119,71 | +156.910,77 |
| 06 | 4.564.305,01 | 4.567.893,85 | −3.588,84 | 100,0% | 6.607.166,51 | 6.363.056,44 | +244.110,07 |
| 07 | 5.057.295,27 | 5.778.258,36 | **−720.963,09** | 100,0% | 7.617.662,26 | 7.311.536,51 | +306.125,75 |

Neon e Data Mart estão em **paridade exata** no regional em todos os meses — a divergência é de contrato, não de sincronização.

`/regioes/summary` para julho retorna GMV R$ 12.674.957,53 (ML 5.057.295,27 + Shopee 7.617.662,26), contra **R$ 22.544.297,31** no fato diário do mesmo período: **−43,8%**. Três causas somadas: (a) **TikTok está integralmente ausente** do regional (nenhuma fonte tem UF a nível de pedido — `not_applicable` por desenho); (b) o regional de ML só considera pedidos com remessa/UF resolvível — em julho 63.852 de 72.769 pedidos pagos, deixando **8.917 (12,25%) fora**; (c) Shopee usa coluna monetária diferente e não subtrai devolução.

## 8. Limitações do mês aberto

1. **Agosto não está fechado.** Retrato de 05/08 20:00 UTC / 17:00 SP. O Data Mart continua recebendo dados após a execução das 06:00.
2. **TikTok subestima os 2 últimos dias por definição da regra** — 04/08 a 29,3% e 05/08 a 1,6% de maturação, ~R$ 373 mil ainda não elegíveis. Não é lacuna nem atraso.
3. **Shopee para em 04/08** por maturação da fonte manual, não por atraso.
4. **Produtos Shopee de agosto são preliminares por definição**: 188 linhas, GMV R$ 0,00, 0 unidades, 0 concluídos, 1.162 cancelamentos — nenhum pedido de 01–04/08 alcançou `Concluído`.
5. Todo mês fechado deriva continuamente após a carga (§7).

## 9. Veredito por canal

### Mercado Livre — **TRUSTED WITH LIMITATION**

| Dimensão | Veredito |
|---|---|
| Cobertura | TRUSTED (jan–jul integral; 20/01 de barbours é ausência real na fonte) |
| Atualização | TRUSTED WITH LIMITATION (depende do Task Scheduler local; deriva contínua) |
| GMV | TRUSTED (regra = Gate R3, reconfirmada na definição real da view) |
| Pedidos | TRUSTED |
| Cancelamentos | TRUSTED WITH LIMITATION (`partially_refunded` exclui o pedido inteiro; 165 pedidos com pagamento estornado seguem no GMV) |
| Devoluções/reembolsos | **NOT TRUSTED** (`returned_orders`/`refunded_orders` são NULL — não existem na fonte ML) |
| Dados do mês corrente | TRUSTED WITH LIMITATION (dia corrente parcial) |
| Neon × API | TRUSTED (paridade R$ 0,00) |

### TikTok Shop — **TRUSTED WITH LIMITATION**

| Dimensão | Veredito |
|---|---|
| Cobertura | TRUSTED (jan–jul integral, 5 marcas) |
| Atualização | TRUSTED WITH LIMITATION |
| GMV | TRUSTED WITH LIMITATION (meses fechados reconciliam; 4 status novos fora da allowlist conhecida) |
| Pedidos | TRUSTED WITH LIMITATION (contrato `COALESCE(gold, raw)`; populações incompatíveis) |
| Cancelamentos | **NOT TRUSTED** (`gold.tiktok_brand_daily.canceled` = 0 em 1.080/1.080 linhas vs 436.814 cancelados na Raw) |
| Devoluções/reembolsos | **NOT TRUSTED** (nenhuma coluna de devolução no pedido; `returned`/`refunded` = 0) |
| Dados do mês corrente | **NOT TRUSTED** (últimos 2 dias a 29,3% e 1,6% de maturação) |
| Neon × API | TRUSTED (paridade R$ 0,00) |

### Shopee — **TRUSTED WITH LIMITATION**

| Dimensão | Veredito |
|---|---|
| Cobertura | TRUSTED (jan–jul integral, 5 marcas; ago 4/4 dias disponíveis) |
| Atualização | TRUSTED WITH LIMITATION (export manual; sem API oficial) |
| GMV | TRUSTED (Daily = shop-stats líquido; paridade com a fonte deduplicada) |
| Pedidos | TRUSTED |
| Cancelamentos | TRUSTED (contagem e valor disponíveis nos dois contratos) |
| Devoluções/reembolsos | TRUSTED WITH LIMITATION (`refunded_orders` NULL no fato; contrato B não subtrai devolução) |
| Dados do mês corrente | TRUSTED WITH LIMITATION (4 dias, produtos preliminares) |
| Neon × API | TRUSTED (paridade R$ 0,00) |

### Transversal — Regiões: **NOT TRUSTED como GMV comparável entre canais**

Reconcilia perfeitamente com sua própria fonte, mas mede coisa diferente do fato diário e a API declara `uf_fill_pct: 100.0` / `coverage_level: "ok"` / `coverage_warning: false` sobre um universo já reduzido em 43,8%.

## 10. Achados classificados

### Bloqueadores

1. **TikTok não tem cancelamento nem devolução em lugar nenhum da cadeia servida.** `gold.tiktok_brand_daily.canceled`/`returned`/`refunded` valem **0 em todas as 1.080 linhas** de 2026, enquanto a Raw registra **436.814 pedidos `CANCELLED` em jan–jul** (20,8%–23,4% do subtotal mensal). A API devolve `tiktok_cancel_rate = None` e `tiktok_problem_rate = None` — não exibe um zero falso, o que evita o pior caso, mas a ausência **não é declarada como `not_applicable`**: as telas de Qualidade e os sinais de risco simplesmente não têm o dado e o usuário não é avisado de que o canal com maior volume de cancelamentos é o único sem essa métrica.

2. **Regiões mede 43,8% menos que Gerencial/Canais no mesmo período e se declara "ok".** Julho: R$ 12,67M vs R$ 22,54M. Um leitor que compare as duas telas conclui, sem nenhum aviso, que a Torre é inconsistente. O indicador de cobertura reforça o erro ao reportar 100%.

### Necessários

3. **`silver.stg_shopee_shop_stats` tem chave duplicada em mai (6 linhas) e jun (24 linhas)** — 6 arquivos ingeridos onde os outros meses têm 5, mesma `(brand, stat_date)` carregada duas vezes. Sem impacto no Neon hoje (o fato diário vem dos arquivos locais), mas **inflaria junho em +48% qualquer consumidor da camada silver** — inclusive o Airflow, se ela for adotada como fonte.

4. **Cobertura de UF do regional de ML é parcial em jan–abr** (52,9%–69,4%) e, mesmo com 100% de "fill" em mai–ago, a elegibilidade já descarta 12,25% dos pedidos pagos de julho. O denominador de `uf_fill_pct` é o universo elegível, não o universo real.

5. **Quatro status TikTok fora da allowlist conhecida** (`UNPAID`, `AWAITING_COLLECTION`, `AWAITING_SHIPMENT`, `ON_HOLD`) apareceram a partir de jun/2026 e já representam **24,3% do subtotal de agosto** (10.024 pedidos, R$ 515.346,17). Precisam ser classificados formalmente — decidir se são pré-GMV (entram quando amadurecem) ou exclusão permanente. **Nenhuma inclusão automática foi feita.**

6. **Sinal `custo_alto` dispara em TikTok sem nenhum dado de mídia.** As 5 linhas TikTek de `/canais` em julho retornam `roas = None` (o fato não tem `ad_spend`/`ad_revenue` para o canal) e duas delas ainda recebem `custo_alto`. É o mesmo falso positivo corrigido no Gate G1 para a Gerencial, que deliberadamente **não** tocou os sinais de Canais.

### Dívidas

7. `partially_refunded` no ML exclui 100% do valor do pedido em vez da parcela reembolsada (R$ 1.588–5.119/mês, ~0,1%).
8. 165 pedidos ML `paid` com pagamento `refunded`/`charged_back` permanecem no GMV (R$ 14.802,99 = 0,047%). Já registrado no Gate R3.
9. `order_status` da Shopee carrega texto livre com data (`"O comprador pode pedir uma devolução até YYYY-MM-DD"`, ~30 variantes) — qualquer regra baseada em status é frágil por construção.
10. Deriva retroativa dos meses fechados não é registrada em nenhum lugar: não há coluna de "fotografia" nem `as_of`, então não é possível distinguir deriva legítima de erro de carga sem re-medir a fonte.
11. `marts.fact_ml_produto_ranking` não tem coluna de data — é snapshot, não permite corte temporal.
12. Timezone de corte de dia não documentado em nenhum contrato de dados.
13. `audit.source_sync_run` mantém 3 execuções órfãs (#52 `shopee_daily` e #64 `ml_produto_ranking` de 16/07, #90 `tiktok_daily` de 26/07).
14. 9 tabelas `*_backup_*` acumuladas em `marts` no Neon.

### Fora do escopo

15. Reprocessar jan–mai para eliminar a deriva (não autorizado; e voltaria a derivar).
16. Alterar qualquer regra de GMV, allowlist de status ou KPI.
17. Implementar a automação no Airflow.
18. Investigação pedido a pedido (proibida por desenho deste gate).

## 11. Correções realmente necessárias

Em ordem de prioridade, **nenhuma executada nesta rodada**:

1. Expor cancelamento/devolução de TikTok — ou corrigir `gold.tiktok_brand_daily` a montante (a Raw tem o dado), ou passar a calcular `canceled_orders` da Raw no conector, como já é feito para o GMV. Enquanto não houver dado, declarar `not_applicable` explicitamente na Qualidade em vez de deixar em branco.
2. Rotular Regiões como métrica de escopo próprio na UI (canais cobertos, pedidos elegíveis, GMV não comparável ao de Canais) e corrigir `uf_fill_pct` para usar o universo real como denominador.
3. Deduplicar `silver.stg_shopee_shop_stats` (mai e jun) e adicionar UNIQUE em `(brand, stat_date)` antes de qualquer consumidor novo.
4. Classificar formalmente os 4 status TikTok novos e documentar a decisão.
5. Aplicar o guardrail de `high_cost` do Gate G1 aos sinais de Canais.
6. Registrar `as_of`/fotografia na carga para tornar a deriva auditável.

## 11.1. Gate DQ2 — verdade da interface (05/08/2026)

Rodada única de correção **apenas de representação**: nenhuma regra de GMV, allowlist de status, threshold de negócio, endpoint, cálculo de cancelamento ou dado foi alterado. Zero escrita, zero pipeline, zero deploy.

**Bloqueador 1 (TikTok sem cancelamento/devolução) — representação corrigida, limitação estrutural mantida.** A API já devolvia `None` corretamente; o problema era a tela de Qualidade, que simplesmente **não exibia** as duas métricas para o canal, de modo que a ausência nunca era declarada. Agora, com TikTok no escopo, a tela mostra dois cards explícitos (`Cancelamento TK` e `Devolução TK`) com valor **`N/D`** — deliberadamente distinto de `0%` e de `—` — legenda "Não disponível nesta fonte", accent neutro (nunca verde de "saudável"), uma nota de limitação que afirma textualmente que *ausência de dado não significa taxa zero*, e o `aria-live` passa a comunicar a indisponibilidade em vez de só "dados carregados". **A limitação de fonte permanece aberta**: continua não existindo cancelamento/devolução de TikTok na cadeia servida, e nada foi calculado da Raw neste gate (segue como correção necessária nº 1).

**Bloqueador 2 (Regiões parece cobertura integral) — representação corrigida.** O contrato já trazia as três dimensões, então nada foi adicionado à API. A tela passa a declará-las separadamente: (a) **cobertura de canal** — "Escopo regional: Mercado Livre e Shopee. TikTok Shop fora do escopo"; (b) **elegibilidade** — os totais cobrem apenas pedidos elegíveis ao fato regional; (c) **preenchimento de UF** — o KPI virou "UF preenchida (elegíveis)" com a legenda "dentro dos pedidos elegíveis", e quando o preenchimento é 100% com canal fora do escopo há ressalva explícita de que isso "não é cobertura de 100% do GMV do período". O total virou **"GMV com cobertura regional"** (antes "GMV Regional"), com o escopo de canais no próprio card. Nenhum percentual geral de cobertura foi fabricado — o denominador real não existe neste endpoint. Seleção só-TikTok agora rende `not_applicable` explícito, nunca zero.

**Achado 6 (`custo_alto` no TikTok) — diagnóstico do DQ1 corrigido e guarda estrutural aplicada.** A verificação contra o dado real de julho mostrou que o achado, como formulado, **conflacionou duas métricas distintas**: `roas = None` é ausência de dado de **mídia/ads**, enquanto `custo_alto` depende do **fee de marketplace**, que o TikTok **tem** (settlements: 29,3% · 25,1% · 24,9% · 24,6% · 20,5% do GMV; mediana 24,9%, p75 25,1%). Os dois sinais de julho (kokeshi e barbours) são portanto **legítimos pela regra vigente** e foram preservados — com o aviso de base (`~5,5%` de desvio vs GMV comercial) já exibido no drill-down. O risco real e comprovado é outro: o sinal é **relativo** e degenera quando a distribuição não tem dispersão (custo 0 em todas as marcas ⇒ `p75 = 0` e `0 >= 0` marcaria 100% das marcas com "0,0%"). A guarda já aprovada no Gate G1 para a camada executiva passou a valer no **produtor** do sinal (`performance_service._build_channel_rows`): `custo > 0` **e** `> mediana do canal` **e** `>= p75`. Nenhum threshold comercial novo; ML e Shopee sem regressão.

**Permanece pendente e inalterado:** a duplicidade de `silver.stg_shopee_shop_stats` em mai (6 linhas) e jun (24 linhas) — **deve ser resolvida antes de a Silver Shopee ser adotada como fonte no Airflow** (achado 3 / correção nº 3). As demais correções necessárias (nº 1, 2, 4, 6) e todas as dívidas de §10 seguem abertas.

## 12. Checkpoint do Airflow

A automação futura será feita no **repositório corporativo do Airflow**, substituindo o Windows Task Scheduler local. **O Gate DQ2 foi concluído e os requisitos de verdade da interface estão atendidos** (§11.1), de modo que a migração está **pronta para retomada futura** no que dependia deste checkpoint — mas **ainda não foi iniciada**: nada de Airflow foi criado, configurado ou executado nesta rodada nem no DQ2. Os requisitos técnicos abaixo continuam valendo integralmente, em especial a deduplicação de `silver.stg_shopee_shop_stats` antes de adotá-la como fonte.

Requisitos que esta auditoria estabelece para essa migração:

1. **Janela fechada e idempotente**, não lookback de 3 dias. A chave `(date, loja_id, marketplace_id)` é upsert, então reprocessar é seguro.
2. **Health check por cobertura**, não por `MAX(data)` — a incompletude de julho passou despercebida por semanas porque a última data estava sempre presente.
3. **Reprocessamento periódico dos meses fechados**, já que a fonte deriva retroativamente (§7). Sem isso, a Torre se afasta da fonte de forma monotônica.
4. **Não adotar `silver.stg_shopee_shop_stats` como fonte** antes da deduplicação do achado 3.
5. **Não tratar o dia corrente do TikTok como fechado** — a maturação leva 2–3 dias.
6. Retry com alerta em falha de VPN/rede: as execuções bloqueadas de 01/08 e 03/08 nunca foram recuperadas.
7. Preservar as regras de GMV exatamente como estão; qualquer mudança exige gate próprio.

---

**Confirmação de escopo:** zero escrita no Data Mart ou Neon; zero pipeline, backfill ou sync executado; zero alteração de código, SQL, `.env`, segredo, VPN ou credencial; zero mudança no Scheduler; zero Airflow; zero deploy; zero commit/push. Nenhum `order_id`, CPF, nome, endereço, telefone, e-mail, nome de arquivo original ou credencial aparece neste documento.
