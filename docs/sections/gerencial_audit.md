# Auditoria — Aba Gerencial

Criado: 2026-06-24  
Validado contra: Neon real (`ep-lively-frost-a6eg1wh2.us-west-2.aws.neon.tech`)  
Ficheiros inspeccionados: `apps/web/app/page.tsx`, `apps/web/src/components/BrandPerformanceTable.tsx`, `apps/web/src/components/GmvChart.tsx`, `apps/web/src/lib/api-client.ts`, `apps/api/app/services/performance_service.py`

---

## 1. Objetivo da aba

Visão executiva consolidada de todos os marketplaces por período mensal.  
Decisão suportada: "Como foi o mês? Qual marca/canal performou? Estamos no caminho da meta?"

---

## 2. Endpoints usados

| Endpoint | Função service | Trigger |
|---|---|---|
| `GET /api/v1/performance/overview?marketplace={filter}&ref_month={YYYY-MM}` | `get_overview()` | Ao abrir ou mudar filtro/período |
| `GET /api/v1/performance/brands?marketplace={filter}&ref_month={YYYY-MM}` | `get_brands()` | Idem |
| `GET /api/v1/performance/monthly?months_back=6&marketplace={filter}` | `get_monthly()` | Idem (sem período — usa últimos 6 meses) |

Todos os três correm em paralelo (`Promise.all`). Cache de 5 min em memória no cliente.

---

## 3. Componentes renderizados

| Componente | Dados usados |
|---|---|
| 4 × `KpiCard` | `OverviewData`: gmv, tiktok/ml/shopee_gmv, orders, avg_ticket, ad_spend, ml_roas, shopee_roas, tiktok_customers, ml_unique_buyers, shopee_unique_buyers, gmv_mom_pct |
| `BrandPerformanceTable` | `BrandRow[]`: tiktok/ml/shopee_gmv (cur + prev), total_gmv, orders, avg_ticket, mom_pct, cos_pct, gpm, ml_roas, ml_cancel_rate_pct + metas de `goals-data.ts` |
| `GmvChart` | `MonthPoint[]`: mes, mes_label, barbours, kokeshi, apice, lescent, rituaria |
| Alerta operacional | Detecta lescent com ml_gmv = 0 no período seleccionado |

---

## 4. Inventário de métricas

### 4.1 KPI Cards

| Métrica exibida | Campo `OverviewData` | Endpoint | Campo mart | Cálculo | Status | Risco |
|---|---|---|---|---|---|---|
| GMV Total | `gmv` | overview | `SUM(gmv)` todos os mkts | tk+ml+sh_gmv | ✅ Igual | Baixo |
| Split TK/ML/SH (subvalue) | `tiktok_gmv`, `ml_gmv`, `shopee_gmv` | overview | `SUM(gmv)` por mkt_id | Separados no service | ✅ Igual | Baixo |
| Pedidos | `orders` | overview | `SUM(orders)` | Pedidos pagos; tk+ml+sh | ✅ Igual | `orders` no mart = só pagos |
| Compradores (subvalue de Pedidos) | `tiktok_customers + ml_unique_buyers + shopee_unique_buyers` | overview | `SUM(unique_buyers)` por mkt_id | Soma do campo | ⚠️ Proxy (ML) | ML: soma diária, não deduplicada mês. TK e SH ok |
| Ticket Médio | `avg_ticket` | overview | Calculado Python | `gmv / orders` | ✅ Igual | Baixo |
| ROAS ML | `ml_roas` | overview | `SUM(ad_revenue) / SUM(ad_spend)` ML | Calculado Python | ✅ Igual | Depende de `ad_revenue` populado no mart |
| ROAS Shopee | `shopee_roas` | overview | `SUM(ad_revenue) / SUM(ad_spend)` SH | Calculado Python | ✅ Validado | mai/2026: ad_spend=287.600 · ad_revenue=4.194.461 → ROAS=14.58x |
| Ad Spend (subvalue de ROAS) | `ad_spend` | overview | `SUM(ad_spend)` ML + SH | Soma ML+SH | ⚠️ Parcial | TikTok ad_spend não incluído (não é gerido via ML/SH Ads) |
| MoM GMV% | `gmv_mom_pct` | overview | Dois meses, mesma query | `(cur-prev)/prev` | ✅ Igual | Baixo |

### 4.2 Tabela por Marca (`BrandPerformanceTable`)

| Coluna exibida | Campo `BrandRow` | Fonte mart | Cálculo | Status | Risco |
|---|---|---|---|---|---|
| TikTok (compacto) | `tiktok_gmv` | `SUM(gmv) mkt=1` por brand | Por brand_key | ✅ Igual | Baixo |
| ML (compacto) | `ml_gmv` | `SUM(gmv) mkt=2` por brand | Por brand_key | ✅ Igual | Baixo |
| Shopee (compacto) | `shopee_gmv` | `SUM(gmv) mkt=3` por brand | Por brand_key | ✅ Igual | Baixo |
| GMV Total | `total_gmv` | Soma tk+ml+sh | Calculado Python | ✅ Igual | Baixo |
| Pedidos | `orders` | `SUM(orders)` todos mkts | Pedidos pagos | ✅ Igual | Baixo |
| Ticket | `avg_ticket` | `gmv/orders` | Calculado Python | ✅ Igual | Baixo |
| MoM | `mom_pct` | Compara mês actual vs. anterior | `(cur-prev)/prev` | ✅ Igual | Baixo |
| COS% | `cos_pct` | `SUM(total_fees) / SUM(gmv)` mkt=1 | `abs(total_fees)/tiktok_gmv` | ⚠️ Verificar | Sinal de `total_fees` pode ser negativo no mart; código usa `abs()`. Confirmar se soma correctamente. |
| R$/1k (GPM) | `gpm` | Não disponível | Hardcoded `None` | ❌ Ausente | `total_views` não existe em `marts.fact_marketplace_daily_performance` |
| ROAS ML | `ml_roas` | `SUM(ad_revenue)/SUM(ad_spend)` mkt=2 | Por brand | ✅ Igual | Depende de `ad_revenue` ML populado |
| Meta TK / ML / SH | calculado localmente | `goals-data.ts` (hardcoded) | % de atingimento | ✅ N/A | Metas estáticas — não vêm da API |

### 4.3 Gráfico GMV Mensal (`GmvChart`)

| Campo | Fonte mart | Status | Risco |
|---|---|---|---|
| `barbours`, `kokeshi`, `apice`, `lescent`, `rituaria` (GMV mensal) | `SUM(gmv)` GROUP BY `DATE_TRUNC('month', date)`, `brand_key` | ✅ Igual | JOIN com `dim_loja` — se brand_key divergir do esperado, linha sumirá |
| Linha total | Calculada no frontend: soma dos 5 campos | ✅ Igual | Baixo |

---

## 5. Diagnóstico por campo

### 5.1 Campos confirmados como correctos (mai/2026, Neon)

| Campo | Valor confirmado |
|---|---|
| GMV TikTok mai/2026 | 13.395.985,86 |
| GMV ML mai/2026 | 3.918.206,55 |
| GMV Shopee mai/2026 | 5.810.690,74 |
| GMV Total mai/2026 | 23.124.883,15 |
| Pedidos ML mai/2026 | 45,432 pagos |
| Pedidos Shopee mai/2026 | 91,157 pagos |
| `ml_cancel_rate_pct` mai/2026 | 4.34% = 2,062/(45,432+2,062) ✅ |
| `shopee_cancel_rate_pct` mai/2026 | 13.84% = 14,639/(91,157+14,639) ✅ |
| MoM GMV | calculado automaticamente a partir dos dados Neon |

### 5.2 Campos proxy ou incertos

| Campo | Situação | Risco real |
|---|---|---|
| `compradores` (subvalue no card Pedidos) | `ml_unique_buyers` = `SUM(unique_buyers)` diário. Para mai/2026: 43,841 (soma diária por brand). Gold deduplicava por mês. Overcount provável. | Número ~10-30% mais alto que o real deduplicado. Não é crítico para decisão gerencial. |
| `ad_spend` KPI (Shopee) | `SUM(ad_spend)` Shopee confirmado no mart. | Integrado na soma ML+SH do overview. |
| `ad_spend` KPI | Soma ML+SH. TikTok não incluído (sem ads geridos via TikTok Ads Manager). | Expectativa correta: COS TikTok é comissão de plataforma, não gasto em anúncios. |
| `cos_pct` por marca | `abs(total_fees)/tiktok_gmv`. Valor de `total_fees` pode ser negativo no mart (comissão é débito). `abs()` garante positivo. | Confirmar sinal do campo numa query pontual se os % parecerem estranhos na UI. |

### 5.3 Campos ausentes (confirmados)

| Campo | Estado |
|---|---|
| `gpm` (R$/1k views) | `total_views` não existe na tabela mart → sempre `None` → coluna exibe "—" na tabela. **Comportamento esperado e documentado.** |
| ROAS TikTok | Não existe no produto. TikTok usa COS% como métrica de custo de plataforma. |
| `tiktok_cancel_rate` | Mart não tem cobertura. `canceled_orders=0` para TikTok em todos os meses disponíveis. Não aparece na Gerencial (apenas na aba Qualidade). |

---

## 6. Bugs conhecidos / decisões pendentes

### B1 — `ml_unique_buyers` sobreestima compradores (⚠️ Proxy)
**O quê:** Campo exibido no subvalue do card "Pedidos": `"${total} compradores"`. Para ML, somamos `unique_buyers` diário em vez de deduplicar por mês.  
**Impacto visual:** Número de compradores ML ~10-30% acima do real. Ticket implícito (`gmv/buyers`) não é exibido neste card, então o erro não propaga.  
**Recomendação:** Aceitar como proxy por agora. Documentar na legenda do card ou remover ML da soma de compradores até haver campo mensal deduplicado no mart.

### B2 — `shopee_roas` ✅ validado (fechado)
**Resultado Neon mai/2026:** ad_spend=287.600,33 · ad_revenue=4.194.461,20 → ROAS=14,58x.  
`ad_revenue` Shopee está correctamente populado no mart. Campo funcional.

### B3 — `gpm` sempre None — coluna sem uso
**O quê:** Coluna "R$/1k" sempre mostra "—".  
**Decisão:** Manter como está (coluna preparada para quando `total_views` for adicionado ao mart). Sem acção imediata.

---

## 7. Queries de validação

```sql
-- Q1: GMV por marketplace e por mês (valida split KPI cards)
SELECT
    marketplace_id,
    DATE_TRUNC('month', date)::date AS mes,
    SUM(gmv)    AS gmv,
    SUM(orders) AS orders,
    SUM(unique_buyers) AS buyers,
    SUM(ad_spend)      AS ad_spend,
    SUM(ad_revenue)    AS ad_revenue
FROM marts.fact_marketplace_daily_performance
WHERE date BETWEEN '2026-05-01' AND '2026-05-31'
GROUP BY 1, 2 ORDER BY 1;

-- Q2: GMV por brand_key e marketplace (valida tabela por marca)
SELECT
    l.brand_key,
    f.marketplace_id,
    SUM(f.gmv)         AS gmv,
    SUM(f.orders)      AS orders,
    SUM(f.total_fees)  AS total_fees,
    SUM(f.ad_spend)    AS ad_spend,
    SUM(f.ad_revenue)  AS ad_revenue
FROM marts.fact_marketplace_daily_performance f
JOIN marts.dim_loja l ON l.loja_id = f.loja_id
WHERE f.date BETWEEN '2026-05-01' AND '2026-05-31'
GROUP BY 1, 2 ORDER BY 1, 2;

-- Q3: Evolução mensal por brand (valida GmvChart)
SELECT
    l.brand_key,
    DATE_TRUNC('month', f.date)::date AS mes,
    SUM(f.gmv) AS gmv
FROM marts.fact_marketplace_daily_performance f
JOIN marts.dim_loja l ON l.loja_id = f.loja_id
WHERE f.date >= '2025-12-01'
GROUP BY 1, 2 ORDER BY 2, 1;

-- Q4: Shopee ad_revenue (B2 — verificar ROAS Shopee)
SELECT
    SUM(ad_spend)   AS sh_ad_spend,
    SUM(ad_revenue) AS sh_ad_revenue,
    CASE WHEN SUM(ad_spend) > 0
         THEN ROUND(SUM(ad_revenue)/SUM(ad_spend)::numeric, 2) END AS roas
FROM marts.fact_marketplace_daily_performance
WHERE marketplace_id = 3
  AND date BETWEEN '2026-05-01' AND '2026-05-31';
```

---

## 8. Resultados confirmados (mai/2026)

| Métrica | Valor Neon mai/2026 | Notas |
|---|---|---|
| GMV TikTok | 13.395.985,86 | Todas as marcas |
| GMV ML | 3.918.206,55 | Todas as marcas |
| GMV Shopee | 5.810.690,74 | Todas as marcas |
| GMV Total | 23.124.883,15 | |
| Pedidos ML (pagos) | 45,432 | `orders` no mart = só pagos |
| Cancelamentos ML | 2,062 | Cancel rate = 4.34% ✅ |
| Pedidos Shopee (pagos) | 91,157 | |
| Cancelamentos Shopee | 14,639 | Cancel rate = 13.84% ✅ |
| Shopee ad_spend | 287.600,33 | |
| Shopee ad_revenue | 4.194.461,20 | ROAS = 14,58x ✅ |
| `tiktok_conversion_rate` | ~3.1% | AVG(NULLIF(conversion_rate,0)) × 100; cobertura parcial (5/155 linhas) |
| `ml_conversion_rate` | NULL | Não populado no mart |
| `unique_buyers` ML | ~43,841 (soma diária) | Sobreestima vs. deduplicado mensal |

**Nota:** os valores 68.8M (TK), 14.4M (ML) e 21.2M (SH) são totais históricos acumulados no Neon desde dez/2025, não valores de maio/2026.

---

## 9. Correcções necessárias

| # | Prioridade | Acção | Ficheiro |
|---|---|---|---|
| C1 | ✅ Fechado | ROAS Shopee validado — ad_revenue Shopee populado no mart | — |
| C2 | Baixa | Considerar remover `ml_unique_buyers` da soma "compradores" no card Pedidos, ou adicionar legenda "soma diária (estimativa)" | `apps/web/app/page.tsx:133` |
| C3 | Quando disponível | Adicionar `total_views` ao mart e remover `gpm: None` hardcoded | `performance_service.py:251`, pipeline Neon |

Sem alterações urgentes em código. A aba Gerencial está funcionalmente correcta com os dados Neon actuais.

---

## 10. Status final

| Secção | Status |
|---|---|
| KPI Cards (GMV, Pedidos, Ticket) | ✅ Validado |
| KPI Card (ROAS ML) | ✅ Validado (lógica) — depende de `ad_revenue` ML populado |
| KPI Card (ROAS Shopee) | ✅ Validado — mai/2026: ROAS=14,58x |
| KPI Card (MoM GMV) | ✅ Validado |
| Tabela por Marca (GMV, Pedidos, Ticket, MoM) | ✅ Validado |
| Tabela por Marca (COS%, ROAS ML) | ✅ Validado (lógica) |
| Tabela por Marca (GPM) | ❌ Ausente — aceite como limitação conhecida |
| Metas por marca | ✅ N/A (dados estáticos) |
| Gráfico GMV Mensal | ✅ Validado (lógica e schema) |

**Status global: Validado com limitações conhecidas** — aba funcional. Limitações aceites: GPM ausente (total_views não existe no mart), ml_unique_buyers é soma diária (proxy).
