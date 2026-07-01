# Auditoria — Aba Financeiro

Criado: 2026-06-26
Atualizado: 2026-07-01 (auditoria de verificacao independente — ver secao 11; corrige a causa do Bug de settlement Shopee e do Q5)
Referencia: Mai/2026
Fonte Neon: `marts.fact_marketplace_daily_performance` + `marts.dim_loja`
Endpoint: `GET /api/v1/performance/financeiro?marketplace=&ref_month=`

---

## 1. Objetivo da aba

A aba Financeiro deve responder, por marketplace e marca:

- TikTok: quanto o GMV gera de receita liquida apos comissoes e taxas de plataforma;
- ML: qual o retorno dos anuncios (ROAS), custo total como % do GMV (ads + frete);
- Shopee: quanto e o settlement, qual a taxa de plataforma e qual o ROAS de ads;
- Identificar marcas com margem financeira comprometida por taxas ou custos de distribuicao.

---

## 2. Componentes e endpoints

| Camada | Arquivo |
|---|---|
| Frontend | `apps/web/app/financeiro/page.tsx` |
| API client | `apps/web/src/lib/api-client.ts` (`fetchFinanceiro`, `FinanceiroKpis`, `FinanceiroBrandRow`) |
| Router | `apps/api/app/routers/performance.py` → `perf_svc.get_financeiro()` |
| Service | `apps/api/app/services/performance_service.py` → `get_financeiro()` |
| Schema | `apps/api/app/schemas/performance.py` (`FinanceiroKpis`, `FinanceiroBrandRow`, `FinanceiroResponse`) |
| Tabela Neon | `marts.fact_marketplace_daily_performance` + `marts.dim_loja` |

Filtros disponíveis: `marketplace` (all/tiktok/ml/shopee) × `ref_month` (YYYY-MM).

Estrutura da resposta:
```json
{
  "ref_month": "2026-05",
  "marketplace": "all",
  "kpis": { ... },
  "brands": [ ... ]
}
```

---

## 3. Inventario de metricas — campo a campo

### 3.1 TikTok

| Campo | Definicao de negocio | Fonte Neon | Grain | Status | Risco |
|---|---|---|---|---|---|
| `tiktok_gmv` | GMV bruto TikTok no mes | `SUM(gmv)` WHERE mkt=1 | diario/loja → mensal/brand | Confiavel | Baixo |
| `tiktok_fees` | Taxas de plataforma (comissao + afiliados) | `ABS(SUM(total_fees))` WHERE mkt=1 | diario/loja | Confiavel | Campo e negativo no BD; backend aplica `abs()` antes de expor |
| `tiktok_settlement` | Receita liquida apos taxas | `SUM(total_settlement)` WHERE mkt=1 | diario/loja | Confiavel | Positivo no BD; semantica correta |
| `tiktok_avg_fee_pct` | Taxa / GMV * 100 | Calculado Python: `abs(fees) / gmv * 100` | mensal/brand | Confiavel | Mai/2026: 25-31% por marca |
| `tiktok_avg_settlement_pct` | Settlement / GMV * 100 | Calculado Python | mensal/brand | Confiavel | Mai/2026: 66-77% por marca |

**Campos ausentes no Neon:**
- Comissao de afiliados discriminada: incluida em `total_fees` sem breakdown
- CPC, impressoes e cliques TikTok: sem equivalente no mart

### 3.2 Mercado Livre

| Campo | Definicao de negocio | Fonte Neon | Grain | Status | Risco |
|---|---|---|---|---|---|
| `ml_gmv` | GMV bruto ML no mes | `SUM(gmv)` WHERE mkt=2 | diario/loja | Confiavel | Baixo |
| `ml_ad_spend` | Gasto com anuncios ML | `SUM(ad_spend)` WHERE mkt=2 | diario/loja | Confiavel | Mai/2026: 3 marcas com cobertura total |
| `ml_ad_revenue` | GMV atribuido a anuncios | `SUM(ad_revenue)` WHERE mkt=2 | diario/loja | Confiavel | Mai/2026: coverage 100% nas 3 marcas ML |
| `ml_roas` | ad_revenue / ad_spend | Calculado Python | mensal/brand | Confiavel | Mai/2026: 12-15x por marca |
| `ml_acos_pct` | ad_spend / ad_revenue * 100 | Calculado Python | mensal/brand | Confiavel | Inverso do ROAS |
| `ml_cpc` | ad_spend / ad_clicks | Calculado Python | mensal/brand | Confiavel | Depende de `ad_clicks` populado |
| `ml_ctr_pct` | ad_clicks / ad_impressions * 100 | Calculado Python | mensal/brand | Confiavel | Depende de ambos populados |
| `ml_seller_shipping_cost` | Custo de frete pago pelo vendedor ML | `SUM(seller_shipping_cost)` WHERE mkt=2 | diario/loja | Confiavel | Mai/2026: 3 marcas com cobertura total |
| `ml_shipping_pct_of_gmv` | frete / gmv * 100 | Calculado Python | mensal/brand | Confiavel | Mai/2026: 11-14% por marca |
| `ml_total_cost_pct` | (ad_spend + frete) / gmv * 100 | Calculado Python | mensal/brand | Confiavel com ressalva | Nao inclui comissao ML (ausente no mart); subestima custo total real |

**Campos ausentes no Neon:**
- Comissao ML (taxa de plataforma / tarifa de servico): nao esta em `total_fees` para ML (campo NULL para mkt=2)
- Settlement ML: campo NULL para mkt=2

### 3.3 Shopee

| Campo | Definicao de negocio | Fonte Neon | Grain | Status | Risco |
|---|---|---|---|---|---|
| `shopee_gmv` | GMV bruto Shopee no mes | `SUM(gmv)` WHERE mkt=3 | diario/loja | Confiavel | Baixo |
| `shopee_fees` | Taxas de plataforma Shopee | `SUM(total_fees)` WHERE mkt=3 | diario/loja | Confiavel (sinal diferente) | **ATENCAO: campo e POSITIVO no BD Shopee** (diferente de TikTok que e negativo). Nao aplicar abs() — o valor ja esta no sinal correto para exibicao |
| `shopee_settlement` | Receita liquida Shopee | `SUM(total_settlement)` WHERE mkt=3 | diario/loja | Confiavel com ressalva | Mai/2026: kokeshi mostra settlement > GMV (100.6%). Possivel diferenca de corte de datas entre faturamento e pagamento |
| `shopee_avg_fee_pct` | fees / gmv * 100 | Calculado Python (sem abs, fees ja positivo) | mensal/brand | Confiavel | Mai/2026: 24-28% por marca |
| `shopee_avg_settlement_pct` | settlement / gmv * 100 | Calculado Python | mensal/brand | Confiavel com ressalva | Mai/2026: 90-100.6%; valores > 100% indicam timing de pagamento Shopee |
| `shopee_ad_spend` | Gasto com anuncios Shopee | `SUM(ad_spend)` WHERE mkt=3 | diario/loja | Confiavel | Fonte: exports CSV de ads Shopee |
| `shopee_ad_revenue` | GMV atribuido a anuncios Shopee | `SUM(ad_revenue)` WHERE mkt=3 | diario/loja | Confiavel | Mai/2026: ROAS 12-16x por marca |
| `shopee_roas` | ad_revenue / ad_spend | Calculado Python | mensal/brand | Confiavel | Depende de coverage de ads |
| `shopee_shipping_cost` | Custo de frete Shopee | `SUM(seller_shipping_cost)` WHERE mkt=3 | diario/loja | Confiavel | Cobertura validada |

---

## 4. Resultados das queries de validacao — Mai/2026

> Queries executadas contra Neon real. Credenciais nao expostas.

### 4.1 Q1 — Totais financeiros por marketplace/brand

**TikTok (marketplace_id=1) — total_fees negativos no BD:**

| Brand | GMV | Settlement | Fees (BD) | Taxa % | Liq. % |
|---|---:|---:|---:|---:|---:|
| apice | 876.174 | 629.649 | -223.712 | 25,5% | 71,9% |
| barbours | 9.709.787 | 7.150.507 | -2.946.289 | 30,3% | 73,6% |
| kokeshi | 2.316.329 | 1.588.572 | -707.987 | 30,6% | 68,6% |
| lescent | 253.922 | 168.686 | -74.067 | 29,2% | 66,4% |
| rituaria | 239.773 | 185.236 | -61.618 | 25,7% | 77,3% |

**ML (marketplace_id=2) — sem settlement/fees; apenas ads e frete:**

| Brand | GMV | Ad Spend | Ad Revenue | ROAS | Frete |
|---|---:|---:|---:|---:|---:|
| barbours | 2.576.681 | 120.543 | 1.456.063 | 12,08x | 307.667 |
| kokeshi | 789.294 | 34.319 | 468.964 | 13,66x | 84.712 |
| lescent | 552.231 | 23.253 | 338.370 | 14,55x | 76.247 |

**Shopee (marketplace_id=3) — total_fees POSITIVOS no BD:**

| Brand | GMV | Settlement | Fees (BD) | Taxa % | Liq. % |
|---|---:|---:|---:|---:|---:|
| apice | 597.930 | 551.792 | 147.692 | 24,7% | 92,3% |
| barbours | 1.774.185 | 1.597.682 | 428.299 | 24,1% | 90,1% |
| kokeshi | 2.877.717 | 2.893.650 | 778.648 | 27,1% | 100,6% |
| lescent | 177.050 | 174.842 | 42.804 | 24,2% | 98,8% |
| rituaria | 383.808 | 371.452 | 107.053 | 27,9% | 96,8% |

### 4.2 Q2 — Cobertura de nulos por marketplace

| Marketplace | Linhas | Settlement | Fees | Ad Spend | Ad Revenue | Shipping |
|---|---:|---:|---:|---:|---:|---:|
| TikTok (1) | 155 | 155 | 155 | 0 | 0 | 0 |
| ML (2) | 93 | 0 | 0 | 93 | 93 | 93 |
| Shopee (3) | 155 | 155 | 155 | 155 | 155 | 155 |

**Conclusao:** TikTok cobre settlement/fees mas nao tem ads. ML nao tem settlement/fees mas cobre ads e frete. Shopee cobre todos os campos financeiros.

### 4.3 Q3 — Sinais dos campos

| Campo | Min | Max | Interpretacao |
|---|---:|---:|---|
| `total_settlement` | 2.808,37 | 656.950,12 | Sempre positivo — correto |
| `total_fees` | -266.374,09 | 38.004,61 | **Misto**: TikTok negativo, Shopee positivo |
| `ad_spend` | 285,40 | 5.557,38 | Sempre positivo — correto |

### 4.4 Q4 — ROAS calculado

| Canal | Brand | ROAS |
|---|---|---:|
| ML | barbours | 12,08x |
| ML | kokeshi | 13,66x |
| ML | lescent | 14,55x |
| Shopee | apice | 15,71x |
| Shopee | barbours | 14,37x |
| Shopee | kokeshi | 14,86x |
| Shopee | lescent | 12,74x |
| Shopee | rituaria | 13,44x |

### 4.5 Q5 — Settlement% e Taxa% Shopee

settlement_pct e taxa_pct **nao sao complementares** — sao dois percentuais independentes sobre o GMV:
- `settlement_pct`: 90,1% a 100,6%
- `taxa_pct`: 24,1% a 27,9%
- Soma: 114–128% do GMV

**Este era o bug principal documentado no Passo 3a.**

**Atualizacao 2026-07-01 (auditoria de verificacao, secao 11):** a causa **nao e** "fees Shopee incluem componentes que podem exceder o GMV nominal" — isso foi uma hipotese nao verificada da rodada anterior. A causa comprovada e que `settlement_pct` usa um campo (`total_settlement` = "Total global" do pedido) que nunca representou repasse liquido; por isso ele fica sempre perto de 90-100%+ independentemente da taxa real. O indicador de "liquidacao" foi removido da tela nesta rodada — ver secao 11.

---

## 5. Bugs e incoerencias encontrados

### Bug 1 — Coluna "Composicao" Shopee com barra enganosa (CORRIGIDO)

**Arquivo:** `apps/web/app/financeiro/page.tsx`

**O que era:** A coluna `Composicao` exibia um componente `SettlementBar` que renderizava uma barra de progresso com dois segmentos — "Receita" (settlement_pct) e "Taxa" (100 - settlement_pct). A legenda visual sugeria que Receita + Taxa = 100% do GMV.

**Por que e falso para Shopee:**
- `settlement_pct` Shopee: 90–100,6% do GMV
- A "Taxa" na barra era calculada como `100 - settlement_pct`, ou seja, 0–10%
- A taxa real (fees / GMV) e de 24–28%
- O usuario via visualmente "menos de 10% de taxa" quando a taxa real era quase 3x maior

**Para TikTok:** A barra tambem existia. Para TikTok `settlement + fees ≈ GMV` (ex: 73,6% + 30,3% = 103,9%), por isso a semantica era mais proxima da realidade — mas ainda tecnicamente incorreta (o complemento de settlement nao e a taxa; pode haver diferenca por descontos, ajustes, etc).

**Correcao aplicada:**
- Removida a coluna `Composicao` de ambas as tabelas (TikTok e Shopee)
- Removido o componente `SettlementBar` (nao mais referenciado)
- Substituida por coluna `Liq. %` (settlement_pct numerico direto, sem barra grafica)
- Legenda do rodape atualizada para esclarecer que Taxa % e Liq. % sao independentes

### Bug 2 — Legenda "Composicao" no rodape TikTok e Shopee (CORRIGIDO)

O rodape de ambas as tabelas exibia icones de cor referenciando "Receita" e "Taxa" da barra removida. Substituido por texto descritivo correto.

### Sem bugs adicionais encontrados (nesta rodada de 2026-06-26)

- `tiktok_fees` exibidos como positivos: correto — backend ja aplica `abs()` antes de retornar
- `ml_total_cost_pct` sem comissao ML: documentado como limitacao conhecida (comissao ausente no mart)
- `shopee_fees` positivos no BD: comportamento esperado — sinal diferente de TikTok, mas backend nao aplica `abs()` para Shopee (correto)

### Bug 3 — `shopee_settlement` nao e um settlement (encontrado e corrigido em 2026-07-01)

**Confirmado por leitura direta do codigo-fonte e re-parse independente dos XLSX originais** (auditoria de verificacao, secao 11). `total_settlement` da Shopee vem da coluna **"Total global"** do export de pedidos (`pipelines/connectors/shopee/_parser.py:191`) — o valor total do pedido, nao um valor liquido de repasse. A hipotese anterior ("possivel diferenca de corte de datas") foi **descartada**: o percentual e estavel (89,8%-100,6%) ao longo de 5 meses, o que e inconsistente com atraso de repasse e consistente com mapeamento de campo incorreto. Correcao de apresentacao aplicada: rotulo "Receita Liquida"/"Liq.%" removido da tela; valor renomeado para "Total Global (pedidos)" onde ainda exibido.

### Bug 4 — Comissao ML ausente e material (quantificado em 2026-07-01)

`gold.ml_produto_pnl.marketplace_fee` existe no Data Mart e nao e usado em nenhum lugar do pipeline. Agregado lifetime por marca: barbours 16,38%, kokeshi 18,18%, lescent 17,86%, rituaria 15,55% (media geral 16,53% da receita bruta) — contra apenas 3,12% de ad_spend lifetime. Ou seja, o "Custo Total ML" atual (ads+frete, ~15,5% em mai/2026) provavelmente subestima o custo real em quase metade. Nao e possivel aplicar esse percentual diretamente a um mes especifico porque `ml_produto_pnl` nao tem coluna de data (e cumulativo por produto/lifetime) — ver pendencia na secao 9.

---

## 6. Metricas confiaveis

**TikTok:**
- GMV, settlement, fees (pos abs), taxa %, settlement %
- ROAS: nao existe — TikTok usa COS% como metrica de custo de plataforma

**Mercado Livre:**
- GMV, ad_spend, ad_revenue, ROAS, ACOS, CPC, CTR
- Custo de frete (seller_shipping_cost)
- Custo total = ads + frete como % do GMV

**Shopee:**
- GMV, settlement, fees (positivos — sinal diferente de TikTok)
- Taxa % (fees / GMV)
- Settlement % (settlement / GMV) — com ressalva de timing de pagamento
- ad_spend, ad_revenue, ROAS

---

## 7. Metricas proxy ou ausentes

| Campo | Status | Risco |
|---|---|---|
| `ml_total_cost_pct` | Proxy — falta comissao ML (quantificada em ~16,5%, ver Bug 4) | Subestima custo total real em ~metade; rotulo "Ads + Frete / GMV" (2026-07-01) deixa isso explicito |
| `shopee_total_settlement` (ex-"Liq.%"/"Receita Liquida") | **Removido da tela em 2026-07-01** — nao e settlement (Bug 3) | Campo mal mapeado desde a origem (coluna "Total global" do pedido); nao usar como indicador de margem/liquidez |
| `ml_settlement` / `ml_fees` | Ausente | Campo NULL para ML no mart; sem equivalente disponivel |
| Comissao ML discriminada | Ausente do indicador mensal | `total_fees` NULL para mkt=2; comissao real existe em `gold.ml_produto_pnl.marketplace_fee` mas sem competencia temporal (Bug 4) |
| `tiktok_avg_settlement_pct` ("Repasse recebido") | Confiavel como numero; **comprovado** que a base ("revenue" dos statements) e diferente do GMV comercial; **inferencia forte, nao comprovada pedido a pedido**, de que tambem ha descasamento de competencia | Nao comparar mes a mes como se fosse margem estavel — ver secao 11.1 |
| `ml_avg_ticket` no Financeiro | Ausente | Nao exposto no endpoint — pode ser calculado de `gmv/orders` se necessario |

---

## 8. Correcoes aplicadas

| # | Arquivo | Mudanca |
|---|---|---|
| C1 | `apps/web/app/financeiro/page.tsx` | Removida coluna `Composicao` da tabela TikTok; substituida por `Liq. %` (settlement_pct numerico) |
| C2 | `apps/web/app/financeiro/page.tsx` | Removida coluna `Composicao` da tabela Shopee; substituida por `Liq. %` (settlement_pct numerico) |
| C3 | `apps/web/app/financeiro/page.tsx` | Removido componente `SettlementBar` (sem referencias apos C1/C2) |
| C4 | `apps/web/app/financeiro/page.tsx` | Legenda rodape TikTok atualizada: removidas referencias de cor da barra; texto correto sobre taxas de afiliados e Liq.% |
| C5 | `apps/web/app/financeiro/page.tsx` | Legenda rodape Shopee atualizada: texto explicativo de que Taxa % e Liq. % sao independentes e podem somar > 100% |
| C6 | `apps/web/app/financeiro/page.tsx` | **(2026-07-01)** Removido card e coluna "Receita Liquida Shopee"/"Liq. %"; valor remanescente renomeado para "Total Global (pedidos)" |
| C7 | `apps/web/app/financeiro/page.tsx` | **(2026-07-01)** "Custo Total ML" renomeado para "Ads + Frete / GMV"; aviso "Nao inclui comissao do Mercado Livre" adicionado ao card e ao rodape |
| C8 | `apps/web/app/financeiro/page.tsx` | **(2026-07-01)** "Receita Liquida TikTok" renomeado para "Repasse recebido TikTok"; "Taxa Media TikTok" para "Taxas e encargos / GMV"; aviso de competencia adicionado |
| C9 | `apps/web/app/financeiro/page.tsx` | **(2026-07-01)** Removida afirmacao nao verificavel "taxas incluem comissao de afiliados" do rodape TikTok |
| C10 | `apps/web/app/financeiro/page.tsx` | **(2026-07-01)** Rodape Shopee corrigido: nao atribui mais a soma >100% a "fees positivos"; explica que o campo antigo nao era settlement |

---

## 9. Proximos passos de dados

1. ~~Trazer comissao ML para o mart.~~ **Atualizado 2026-07-01:** localizada em `gold.ml_produto_pnl.marketplace_fee` (media 16,53% da receita bruta, lifetime), mas essa tabela e cumulativa por produto, **sem coluna de data** — nao da para aplicar a um mes especifico. **Pendencia real:** localizar ou construir uma fonte de comissao ML com granularidade diaria/mensal (pode exigir nova extracao do Data Mart ou da API do Mercado Livre) antes de alterar `ml_total_cost_pct`.

2. ~~Investigar settlement Shopee > 100%.~~ **Resolvido/reclassificado 2026-07-01:** nao e timing — e mapeamento de campo (Bug 3). **Pendencia real:** localizar o relatorio de renda/repasse (income release) da Shopee, que e um export diferente do `Order.all*.xlsx` hoje usado, para obter um valor de settlement genuino. Ate la, o indicador foi removido da tela.

3. **Reconciliar statements TikTok com pedidos por competencia.** Confirmado que `gold.tiktok_settlements_summary` (granularidade = statement/repasse) e `gold.tiktok_brand_daily` (granularidade = dia de pedido) tem bases diferentes (revenue do settlement ~5,5% maior que GMV comercial em mai/2026) e que o settle% varia de 35% a 77% mes a mes — evidencia forte de descasamento de competencia, mas nao foi rastreado pedido a pedido (falta acesso a `raw.tiktok_shop_settlements`, que esta vazia nesta replica do Data Mart).

4. **Discriminar fees TikTok.** Sem coluna de comissao de afiliados em nenhuma tabela `raw`/`gold` do TikTok disponivel — a afirmacao antiga de que "taxas incluem comissao de afiliados" foi removida da UI por nao ser verificavel.

5. **Monitorar sinal de `total_fees` no mart.** O campo tem sinal diferente entre TikTok (negativo) e Shopee (positivo). Ver secao 11.3 para a convencao documentada.

---

## 10. Status por canal

| Canal | GMV | Settlement/Taxas | Ads/ROAS | Frete | Status geral |
|---|---|---|---|---|---|
| TikTok | Confiavel | Repasse confiavel como numero; **nao comparavel 1:1 com GMV do mes** (denominador diferente, comprovado; competencia, inferencia forte — ver 11.1) | Ausente | Ausente | Parcialmente confiavel — nomenclatura corrigida em 2026-07-01 |
| ML | Confiavel | Ausente (NULL); comissao real existe na fonte mas sem competencia temporal | Confiavel | Confiavel | Parcialmente confiavel — "Ads + Frete / GMV" correto para o que mede, incompleto como custo total |
| Shopee | Confiavel | Taxa (comissao+servico) confiavel; **settlement removido da tela** (nao era settlement) | Confiavel | Confiavel | Parcialmente confiavel — pendente fonte real de repasse |

---

## 11. Auditoria de verificacao independente — 2026-07-01

Refeita do zero (leitura de codigo + SQL somente-leitura contra Neon e Data Mart/RDS + re-parse dos XLSX originais da Shopee), tratando a auditoria de 2026-06-26 acima como hipotese, nao como verdade. Duas conclusoes da rodada anterior foram corrigidas (Bug 3 e a causa do Q5).

**Veredito: PARCIALMENTE CONFIAVEL.** O pipeline tecnico esta correto (formula = razao ponderada SUM/SUM, sem bug de media simples) e a reconciliacao de 3 camadas (fonte -> Neon -> API) e exata ao centavo para Shopee e ML, com desvio de 0,03% para TikTok (defasagem de sync). O problema e semantico: dois dos tres canais expunham uma metrica de "% liquido"/"custo total" que nao significava o que o nome sugeria.

### 11.1 TikTok 72,6% + 30,0% = 102,6% — comprovado vs. inferencia

**Comprovado (algebricamente, via SQL somente-leitura contra `gold.tiktok_settlements_summary`, RDS, grao = statement x marca):** `settlement = revenue + fee_tax + shipping + adjustment` bate exato — e uma decomposicao legitima, mas da **"revenue" do subsistema de repasses** (R$14,13M em mai/2026), **nao do GMV comercial** (R$13,40M, 5,5% menor). Ou seja, `settlement_pct` e `fee_pct` sao calculados sobre universos/denominadores diferentes quando divididos pelo GMV do mart — isso e fato, verificado linha a linha.

**Inferencia forte, ainda NAO comprovada pedido a pedido:** a hipotese de que o descasamento tambem tem componente de **competencia temporal** (repasse de um mes incluindo pedidos de outros meses) e sustentada por evidencia indireta — `settle%` varia de 35,3% a 77,3% mes a mes/marca, o que seria incomum para uma margem estavel — mas **nao foi rastreada pedido a pedido**: `raw.tiktok_shop_settlements` (a tabela que permitiria ligar cada settlement ao(s) pedido(s) original(is) por `order_id`) esta vazia nesta replica do Data Mart. Ate essa reconciliacao ser feita, tratar a incompatibilidade de competencia como **hipotese fortemente sustentada, nao como causa definitivamente comprovada**.

Em ambos os casos: nao e erro de sinal (o `abs()` esta correto) nem duplicacao (grao `(date, loja_id, marketplace_id)` sem duplicatas). Pendencia registrada na secao 9/11.6.

### 11.2 Causa comprovada — Shopee 96,2% + 25,9% = 122,1%

Erro de mapeamento de campo (nao e timing). `pipelines/connectors/shopee/_parser.py:191`: `total_settlement = sum(o["total_global"] for o in active)` — "Total global" e o valor total do pedido no export de Orders, nao o repasse liquido (que viria de um relatorio de renda/income release da Shopee, nao usado nesta pipeline). Confirmado por re-parse independente dos 5 arquivos XLSX de maio/2026: bate ao centavo com Neon e com a API em producao, nas 5 marcas — ou seja, o numero e transportado com perfeicao tecnica; o problema nasce na escolha de mapeamento, nao na pipeline. A estabilidade do percentual (89,8%-100,6% em 5 meses, sem a volatilidade vista no TikTok) tambem contradiz a hipotese de timing da rodada anterior.

### 11.3 Convencao de sinal de `total_fees` (documentado formalmente)

| Canal | Sinal na fonte | Tratamento no `performance_service.py` |
|---|---|---|
| TikTok | Negativo (debito) | `abs(total_fees)` antes de expor |
| Shopee | Positivo (custo) | Exposto direto, sem `abs()` |
| ML | Sempre NULL | Campo nao existe no mart para ML |

### 11.4 Comissao ML — quantificada, nao aplicavel a maio/2026

`gold.ml_produto_pnl.marketplace_fee` (RDS) nao e usado no pipeline. Agregado lifetime por marca: barbours 16,38%, kokeshi 18,18%, lescent 17,86%, rituaria 15,55% (media 16,53% da receita bruta, vs. apenas 3,12% de ad_spend lifetime). Plausivel que o custo real de ML seja ~30-32% do GMV (dobro do "Ads + Frete / GMV" atual de 15,5%), mas **essa tabela e cumulativa por produto (sem coluna de data)** — nao deve ser somada a maio/2026 especificamente. Correcao aplicada: renomeacao do indicador + aviso, sem alterar o calculo.

### 11.5 Correcoes de apresentacao aplicadas nesta rodada (sem alterar banco/ETL)

Ver tabela de correcoes C6-C10 na secao 8. Resumo: (a) removido indicador de "liquidacao" da Shopee; (b) renomeado "Custo Total ML" para "Ads + Frete / GMV" com aviso; (c) renomeado "Receita Liquida"/"Taxa Media" do TikTok para "Repasse recebido"/"Taxas e encargos" com aviso de competencia; (d) removida afirmacao nao verificavel sobre comissao de afiliados; (e) corrigido rodape Shopee que atribuia a soma >100% ao sinal das fees.

### 11.6 Pendencias registradas

1. Localizar o relatorio de renda/repasse (income release) da Shopee para obter settlement genuino.
2. Localizar ou construir fonte de comissao ML com granularidade diaria/mensal (hoje so existe lifetime/cumulativa por produto).
3. Reconciliar `gold.tiktok_settlements_summary` (statements) com `raw.tiktok_shop_orders`/pedidos por competencia — bloqueado porque `raw.tiktok_shop_settlements` esta vazia nesta replica do Data Mart.
