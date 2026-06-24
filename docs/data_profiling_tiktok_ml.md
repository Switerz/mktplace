# Data Profiling — TikTok Shop & Mercado Livre

Data: 2026-06-16 | Fonte: Data Mart (`gold.tiktok_brand_daily`, `gold.ml_gestao_diaria`)

---

## TikTok Shop — `gold.tiktok_brand_daily`

### Cobertura por brand

| Brand | Dias | Período | GMV Total | GMV Médio/dia | Pedidos Total |
|---|---|---|---|---|---|
| barbours | 254 | 2025-10-05 → 2026-06-15 | R$ 58.760.480 | R$ 231.340 | 1.182.720 |
| kokeshi | 254 | 2025-10-05 → 2026-06-15 | R$ 13.011.805 | R$ 51.227 | 321.352 |
| apice | 253 | 2025-10-06 → 2026-06-15 | R$ 3.803.119 | R$ 15.032 | 61.416 |
| lescent | 253 | 2025-10-06 → 2026-06-15 | R$ 1.158.503 | R$ 4.579 | 25.066 |
| rituaria | 252 | 2025-10-07 → 2026-06-15 | R$ 1.104.728 | R$ 4.383 | 15.646 |

**Nota:** Os dados gold do TikTok começam em outubro/2025. A tabela raw (`raw.tiktok_shop_orders`) possui dados desde junho/2025 — há ~4 meses de histórico raw sem gold correspondente. A pipeline do Data Mart pode ter sido criada posteriormente. Isso não impede o MVP, mas limita o backfill histórico via gold.

### Qualidade dos campos críticos

| Campo | Nulos | Status |
|---|---|---|
| gmv | 0 | ✅ |
| orders | 0 | ✅ |
| items_sold | 0 | ✅ |
| avg_ticket | 0 | ✅ |
| visitors | **~83% ausentes** | ⚠️ PROBLEMA |
| conversion_rate | depende de visitors | ⚠️ |
| canceled | 0 | ✅ |
| total_settlement | 0 | ✅ |
| ad_spend | não disponível | ❌ |

### ⚠️ Alerta crítico: campo `visitors`

De 254 dias, apenas ~42 (~16%) possuem `visitors > 0`. Os demais têm `visitors = null` ou `= 0`. Consequências:
- `conversion_rate` também estará ausente/incorreta nesses dias
- **NÃO exibir taxa de conversão TikTok como métrica confiável até investigar a fonte**
- Exibir como `null` (não zero) no dashboard para não distorcer médias

### Duplicatas

✅ Zero duplicatas em `(brand, date)` — tabela íntegra, pode ser usada diretamente.

### GMV por mês (últimos 6 meses) — TikTok

| Mês | barbours | kokeshi | apice | lescent | rituaria |
|---|---|---|---|---|---|
| jun/26 (parcial) | 3.524.575 | 1.123.136 | 624.263 | 146.272 | 211.771 |
| mai/26 | 9.709.786 | 2.316.329 | 876.174 | 253.922 | 239.773 |
| abr/26 | 9.166.934 | 2.294.455 | 637.021 | 283.203 | 154.687 |
| mar/26 | 11.830.495 | 2.325.091 | 436.414 | 260.702 | 92.380 |
| fev/26 | 10.606.317 | 1.141.493 | 234.833 | 48.209 | 87.035 |
| jan/26 | 5.970.025 | 1.365.370 | 295.989 | 37.839 | 91.675 |
| dez/25 | 2.590.225 | 735.407 | 323.368 | 38.310 | 71.354 |

**Observações:**
- barbours é dominante e representa ~75–80% do GMV total TikTok
- lescent teve crescimento forte em abr/26 (R$283K) mas caiu em jun/26 (R$146K) — tendência a monitorar
- rituaria cresceu significativamente de dez/25 (R$71K) para mai/26 (R$239K)
- kokeshi teve queda em fev/26 (R$1.1M) vs jan/26 (R$1.4M) — investigar sazonalidade

---

## Mercado Livre — `gold.ml_gestao_diaria`

### Cobertura por brand

| Brand | Dias | Período | GMV Total | GMV Médio/dia | Pedidos Total |
|---|---|---|---|---|---|
| barbours | 336 | 2025-07-11 → 2026-06-16 | R$ 11.495.612 | R$ 34.213 | 123.452 |
| kokeshi | 416 | 2025-04-27 → 2026-06-16 | R$ 3.169.544 | R$ 7.619 | 49.249 |
| lescent | 318 | 2025-07-29 → 2026-06-16 | R$ 2.391.947 | R$ 7.521 | 35.201 |

**Nota:** ML tem cobertura histórica maior que o TikTok gold — kokeshi desde abril/2025. Dados atualizados até hoje (jun/16).

### Qualidade dos campos críticos

| Campo | Nulos | Status |
|---|---|---|
| gmv | 0 | ✅ |
| paid_orders | 0 | ✅ |
| total_units | 0 | ✅ |
| avg_ticket | 0 | ✅ |
| ad_spend | 0 | ✅ |
| roas | 0 | ✅ |
| cancel_rate_pct | 0 | ✅ |
| avg_delivery_days | 0 | ✅ |
| visitors | não disponível | ❌ |
| conversion_rate | não disponível | ❌ |

### Duplicatas

✅ Zero duplicatas em `(brand, ref_date)` — tabela íntegra.

### GMV por mês (últimos 6 meses) — ML

| Mês | barbours | kokeshi | lescent |
|---|---|---|---|
| jun/26 (parcial) | 1.139.707 | 448.290 | **0** ⚠️ |
| mai/26 | 2.578.760 | 789.678 | 510.206 |
| abr/26 | 1.958.271 | 166.125 | 100.412 |
| mar/26 | 1.855.753 | 22.479 | 207.078 |
| fev/26 | 1.180.177 | 349.587 | 249.812 |
| jan/26 | 869.898 | 246.794 | 370.084 |
| dez/25 | 500.932 | 251.829 | 460.652 |

### ⚠️ Alerta: lescent ML = R$0 em junho/2026

`lescent` tem GMV e pedidos = 0 na tabela ML em junho/2026. Pode ser:
1. Pausa operacional proposital
2. Falha de ingestão no Data Mart
3. Dados ainda não processados para o mês corrente

**Ação**: monitorar diariamente. Se persistir por mais de 3 dias úteis, investigar com o time de dados.

### ⚠️ Kokeshi ML — queda abrupta em março/2026

kokeshi caiu de R$349K (fev) para R$22K (mar) e voltou a R$166K (abr). Queda de ~93% em um mês. Não parece sazonalidade — investigar se houve problema de ingestão ou evento operacional.

---

## Granularidade confirmada

| Fonte | Granularidade | Chave | Tipo |
|---|---|---|---|
| `gold.tiktok_brand_daily` | dia × brand | `(date, brand)` | agregado diário |
| `gold.ml_gestao_diaria` | dia × brand | `(ref_date, brand)` | agregado diário |

Ambas as fontes são **agregados diários por loja** — não há granularidade de pedido/item. Para análise de pedido individual, usar `raw.tiktok_shop_orders` e `raw.ml_orders`.

---

## Conclusão: o que pode ser calculado com confiança

### TikTok — confiável ✅
- GMV diário/mensal por loja
- Pedidos, unidades vendidas, ticket médio
- Cancelamentos, devoluções, reembolsos, problem_rate
- GMV por canal (vídeo/live/card)
- Taxa de liquidação e taxas da plataforma
- Tempo médio de entrega
- Dados de criadores e vídeos

### TikTok — não confiável ⚠️
- Visitantes (83% ausentes)
- Taxa de conversão (depende de visitors)
- ROAS/investimento em mídia (não disponível na gold)

### ML — confiável ✅
- GMV diário/mensal por loja
- Pedidos pagos, unidades, ticket médio
- Cancelamentos e taxa de cancelamento
- Ad spend, ROAS, ACOS, CTR, CPC
- Novos compradores, recompra, cohort
- Entrega e frete

### ML — não disponível ❌
- Visitantes / taxa de conversão
- GMV por canal de conteúdo
- Dados de criadores

---

## Queries de referência salvas

Ver `db/sql/marts/profiling_queries.sql`.
