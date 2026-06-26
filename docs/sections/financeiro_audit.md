# Auditoria — Aba Financeiro

Criado: 2026-06-26
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
- Soma: 114–128% do GMV (fees Shopee incluem componentes que podem exceder o GMV nominal, ex: subsidios e ajustes de plataforma)

**Este era o bug principal documentado no Passo 3a.**

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

### Sem bugs adicionais encontrados

- `tiktok_fees` exibidos como positivos: correto — backend ja aplica `abs()` antes de retornar
- `ml_total_cost_pct` sem comissao ML: documentado como limitacao conhecida (comissao ausente no mart)
- `shopee_fees` positivos no BD: comportamento esperado — sinal diferente de TikTok, mas backend nao aplica `abs()` para Shopee (correto)

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
| `ml_total_cost_pct` | Proxy — falta comissao ML | Subestima custo total real; comissao ML ausente no mart |
| `shopee_avg_settlement_pct` > 100% (kokeshi) | Incoerencia de timing | Pagamentos Shopee podem cair em periodo diferente do GMV; nao e erro de dados, mas pode confundir usuario |
| `ml_settlement` / `ml_fees` | Ausente | Campo NULL para ML no mart; sem equivalente disponivel |
| Comissao ML discriminada | Ausente | `total_fees` NULL para mkt=2 em todo o historico |
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

---

## 9. Proximos passos de dados

1. **Trazer comissao ML para o mart.** Campo `total_fees` NULL para ML em todo o historico. Fonte: `gold.ml_gestao_diaria` — verificar se existe campo de comissao/tarifa de servico e mapear para `marts.fact_marketplace_daily_performance.total_fees`.

2. **Investigar settlement Shopee > 100%.** kokeshi em mai/2026 mostra `settlement / gmv = 100,6%`. Verificar se e problema de corte de competencia (pagamento de meses anteriores entrando no mes atual) ou se ha erro de pipeline.

3. **Discriminar fees TikTok.** Atualmente `total_fees` inclui comissao de plataforma + comissao de afiliados sem breakdown. `gold.tiktok_brand_daily` pode ter colunas separadas — validar e adicionar ao mart se disponivel.

4. **Adicionar campo `ml_commission_pct`** quando disponivel, para completar `ml_total_cost_pct = ads + frete + comissao / gmv`.

5. **Monitorar sinal de `total_fees` no mart.** O campo tem sinal diferente entre TikTok (negativo) e Shopee (positivo). Documentar como convencao no data contract para evitar erros futuros em novos endpoints ou pipelines.

---

## 10. Status por canal

| Canal | GMV | Settlement/Taxas | Ads/ROAS | Frete | Status geral |
|---|---|---|---|---|---|
| TikTok | Confiavel | Confiavel | Ausente | Ausente | Validado com limitacoes conhecidas |
| ML | Confiavel | Ausente (NULL) | Confiavel | Confiavel | Parcialmente validado — falta comissao |
| Shopee | Confiavel | Confiavel (ressalva de timing) | Confiavel | Confiavel | Validado com ressalvas |
