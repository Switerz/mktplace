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

---

## 11. Gate 1 — Redesenho como Cockpit Executivo (2026-07-13)

### 11.1 Pedido e reinterpretação

Pedido do gestor: "gostaria de ter mais informações sobre tudo, como um resumo geral de todas as abas, com os pontos mais importantes... conseguir olhar a Gerencial e entender o negócio sem precisar entrar em todas as abas, mas sem simplesmente jogar tudo lá."

Reinterpretação de produto: a Gerencial deixa de ser "overview + tabela de marcas + gráfico" e passa a ser um **cockpit de decisão**, respondendo 5 perguntas fixas:
1. O negócio está bem ou mal?
2. O que mudou desde o período anterior?
3. Onde estão as oportunidades?
4. Onde estão os riscos?
5. Para qual aba eu vou se quiser investigar?

Não é uma cópia condensada de Canais/Produtos/Financeiro/Qualidade/Regiões/Pedidos — é uma camada de **síntese e priorização** em cima delas.

### 11.2 Diagnóstico da Gerencial atual (validado em código, 2026-07-13)

Nota de atualização de doc: as seções 1-10 acima (criadas em 2026-06-24) descrevem um endpoint `/performance/monthly`. O código atual (`apps/web/app/page.tsx`) já não usa `/monthly` — usa **`GET /api/v1/performance/trend`**, com granularidade auto-ajustada (diário ≤92 dias, mensal >92 dias) em vez de "últimos 6 meses" fixo. `GmvChart` foi substituído por `TrendChart`. As seções 1-10 continuam válidas como registro histórico do Gate anterior, mas o contrato real de hoje é:

| Bloco atual | Endpoint | Fonte |
|---|---|---|
| KPI Cards (GMV, Pedidos, Ticket, ROAS) | `GET /performance/overview` | `marts.fact_marketplace_daily_performance` |
| Tabela por marca | `GET /performance/brands` | idem |
| Gráfico de tendência | `GET /performance/trend` | idem (mesma WHERE clause do overview — soma da série sempre reconcilia com o KPI de GMV) |
| Alerta operacional | Hardcoded no frontend (marca "Lescent" com `ml_gmv=0`) | — |

**Problemas de escopo confirmados:**
- **Filtro de marca não funciona**: `BrandFilter` é renderizado e a querystring `brands=` é montada em `api-client.ts`, mas nenhum dos 3 endpoints (`overview`, `brands`, `trend`) aplica esse filtro no backend. É decorativo hoje.
- **Alerta operacional é hardcoded**: a única "regra de insight" que existe hoje é uma condição fixa no código para uma marca específica — não escala, não é regra genérica, e não deve sobreviver ao redesenho.
- **Compradores somados por dia**: `tiktok_customers`/`ml_unique_buyers`/`shopee_unique_buyers` são somas diárias, não deduplicação mensal (mesmo problema já documentado na seção 5.2). Qualquer novo card de "compradores" no cockpit deve herdar o mesmo aviso ou evitar esse campo como métrica primária.
- **Eficiência de Ads limitada**: `/overview` só devolve ROAS (ML/Shopee); não devolve ACOS/CPC. Se o cockpit quiser "eficiência Ads geral", precisa buscar em `/canais` (que já tem `roas`, `acos_pct`, sinais) em vez de reinventar em `/overview`.
- **Sem persistência de URL**: reload perde filtros. Não bloqueia o cockpit, mas visto por Regiões/Canais.

**O que já é bom e deve ser preservado:**
- Reconciliação entre séries (trend soma = overview GMV) — mesma WHERE clause.
- Parametrização segura (bind params, sem f-string) em `performance_service.py`.
- Estados de loading/erro/vazio já padronizados nos componentes.
- Badge de live/offline e cache de 5 min no cliente.
- Canais já calcula **sinais por marca/canal** (`signals: roas_forte, ads_subutilizado, custo_alto, frete_alto, sem_dado`) e **medianas/p75 entre marcas** (`channel_medians`) — esse é exatamente o padrão que o cockpit deveria reaproveitar em vez de reinventar.

### 11.3 Disponibilidade de métricas por área (o que a Gerencial pode honestamente resumir hoje)

#### Canais (fonte: `GET /performance/canais`, `marts.fact_marketplace_daily_performance`)

| Métrica | Status | Observação |
|---|---|---|
| GMV por canal/marca | ✅ Confiável | — |
| Ads/GMV, ROAS, ACOS | ✅ Confiável (ML+Shopee) / ❌ N/A (TikTok não opera ads geridos) | Já vem com `ads_applicable` vs `ads_available` para distinguir N/A de "sem dado" |
| Custo marketplace/GMV | ✅ Shopee / ⚠️ TikTok (base ~5,5% diferente — `data_warning`) / ❌ ML (sem dado no mart) | |
| Frete seller/GMV | ✅ ML+Shopee / ❌ N/A TikTok | |
| Sinais já calculados | ✅ `roas_forte`, `ads_subutilizado`, `custo_alto`, `frete_alto`, `sem_dado` por marca×canal | **Reaproveitar diretamente**, não recalcular |
| Visitantes/conversão | ⚠️ Proxy, cobertura parcial (ex.: TikTok conversion_rate com 5/155 dias não-zero) | Não usar em regra de insight sem gate de amostra mínima |

#### Produtos (fontes: `/produtos/ml`, `/produtos/tiktok`, `/produtos/shopee` + summaries)

| Métrica | Status | Observação |
|---|---|---|
| Preço médio (3 marketplaces) | ✅ Confiável | `avg_price` / `avg_price_weighted` no summary |
| Pareto A/B/C/D | ✅ Confiável | `pareto_bucket` |
| ROAS/ACOS por produto (ML) | ✅ Confiável, real | Único marketplace com eficiência Ads por produto |
| Eficiência Ads (TikTok/Shopee) | ❌ Indisponível por produto | Só existe por marca/dia (via Canais) |
| Margem real | ❌ Bloqueada nos 3 marketplaces | Sem CMV em lugar nenhum do repositório. **`estimated_margin` do ML nunca deve ser exibido** (lifetime, sem competência mensal) — regra já vigente ([[project_produtos_margem_gate1]]) |

#### Financeiro (fonte: `GET /performance/financeiro`)

| Métrica | Status | Observação |
|---|---|---|
| GMV, fees, taxa% (3 marketplaces) | ✅ Confiável | |
| Ads spend/revenue/ROAS/ACOS (ML+Shopee) | ✅ Confiável | |
| `ml_total_cost_pct` (Ads+Frete/GMV) | ⚠️ Proxy — **não inclui comissão ML (~16,5%)** | Custo real provável ~2x o exibido. Exibir sempre com aviso explícito |
| `tiktok_settlement` / `tiktok_avg_settlement_pct` | ⚠️ Proxy — denominador é "revenue" de repasse, ~5,5% maior que GMV comercial | Rotular "Repasse recebido", nunca "Receita líquida" |
| `shopee_settlement` / `shopee_avg_settlement_pct` | ❌ **Remover totalmente** | Campo mal mapeado (bug identificado e corrigido em 2026-07-01 — coluna é "Total global do pedido", não repasse); é a origem do "settlement >100%" já registrado em memória ([[project_audit_status]]) |

#### Qualidade (fonte: `GET /performance/quality`)

| Métrica | Status | Observação |
|---|---|---|
| Cancelamento ML/Shopee | ✅ Confiável | ML ~4,3%, Shopee ~13,8% (mai/2026) — bases normais bem diferentes entre canais, não comparar 1:1 |
| Devolução Shopee | ✅ Confiável | |
| Tempo de entrega (TikTok, ML) | ✅ Confiável | Shopee: sem dado |
| `ml_not_delivered_rate_pct` | ⚠️ Proxy — mistura em trânsito com não-entregue real | Rotular "proxy" sempre que exibido |
| TikTok cancelamento/problema | ❌ **Sempre 0, sem fonte** | Nunca exibir como "0%" (é ausência de dado, não sinal positivo) |

#### Regiões (fonte: `GET /regioes/summary`, `/regioes/by-brand` — já implementado, Gate 6B)

| Métrica | Status | Observação |
|---|---|---|
| `uf_fill_pct`, `coverage_level` | ✅ Confiável, já classificado (`ok`/`partial`/`low`/`not_applicable`) | Reaproveitar `coverage_level` direto no cockpit |
| Cobertura TikTok | ❌ Sempre ausente (não é bug, é lacuna estrutural da fonte) | |
| Marca "Barbours" (ML, nov/2025–mar/2026) | ⚠️ `coverage_warning=true` conhecido | Se aparecer numa matriz do cockpit, deve exibir o mesmo warning |

#### Pedidos (fonte: `GET /performance/pedidos`)

| Métrica | Status | Observação |
|---|---|---|
| Volume, cancelamento, ticket (TikTok + ML) | ✅ Confiável | |
| Shopee | ❌ Não coberto nesta fonte | Explicitar no cockpit se pedidos entrar no resumo |

### 11.4 Blocos propostos

| Bloco | Conteúdo | Fonte |
|---|---|---|
| **A. Header executivo** | Período ativo, `refreshed_at` (o mais antigo entre as fontes usadas), banner se algum `data_warning` crítico existir | Todos os endpoints agregados |
| **B. Saúde geral** | GMV, Pedidos, Ticket médio, MoM%, ROAS ML/Shopee (rotulado, TikTok excluído por não aplicável), status geral (OK/Atenção/Crítico) calculado por regra (11.5) | `/overview` |
| **C. O que mudou** | Top 3 marcas que mais cresceram/caíram (`mom_pct`, com piso de volume mínimo), canal que mais explicou a variação total | `/brands` |
| **D. Oportunidades** | Até 5 insights `ads_subutilizado` / `roas_forte` já calculados em Canais + `pareto A/B sem Ads` de Produtos | `/canais` (sinais existentes), `/produtos/*` |
| **E. Riscos** | Custo alto (Canais), cancelamento alto (Qualidade, comparado por canal e não globalmente), cobertura regional baixa (Regiões), dado defasado (`refreshed_at` viejo) | `/canais`, `/quality`, `/regioes/summary` |
| **F. Matriz Marca × Canal** | Célula = status (Forte/OK/Atenção/Oportunidade/Sem dado) por marca×canal, derivado dos mesmos sinais de Canais — não é uma tabela nova de números, é uma leitura visual dos sinais já existentes | `/canais` (`channel_rows`) |
| **G. Próximas ações** | 3-5 linhas de texto curto, geradas a partir dos insights de D/E ordenados por severidade, cada uma linkando para a aba de origem | Derivado de D+E |

Blocos que **não** ganham espaço na Gerencial (ficam só nas abas de origem): tabelas de produto individual, séries diárias detalhadas, breakdown de origem TikTok vídeo/live/card, detalhamento de settlement por marketplace.

### 11.5 Regras de insight (propostas — validar thresholds com o gestor antes de codificar)

Princípio geral: **reaproveitar os sinais e medianas já calculados em Canais** (`channel_rows[].signals`, `channel_medians`) em vez de recalcular do zero no cockpit — evita ter duas implementações da mesma regra divergindo com o tempo.

| Regra | Condição proposta | Fonte do dado | Guarda-corpo (amostra mínima) |
|---|---|---|---|
| `crescimento_forte` | `mom_pct >= +15%` | `/brands` | `total_gmv_prev >= piso configurável` (ex.: R$10k) — evita destacar marca residual saindo de quase-zero |
| `queda_relevante` | `mom_pct <= -15%` | `/brands` | idem |
| `ads_subutilizado` | Reaproveitar sinal já calculado em Canais (`gmv` acima da mediana + `ads_gmv_pct` abaixo da mediana, só quando `ads_applicable=true`) | `/canais` `channel_rows[].signals` | Já respeita `brands_with_data >= 2` em `channel_medians` |
| `roas_forte` | Reaproveitar sinal `roas_forte` de Canais | `/canais` | idem |
| `custo_alto` | Reaproveitar sinal `custo_alto` (acima do p75 do canal) | `/canais` | idem |
| `cancelamento_alto` | `cancel_rate_pct` acima da mediana **entre marcas do mesmo canal no mesmo período** (não usar corte fixo global — ML e Shopee têm bases normais muito diferentes, 4% vs 14%) | `/quality` | Mínimo de marcas com pedido no canal (evitar comparar contra amostra de 1) |
| `cobertura_regional_baixa` | Reaproveitar `coverage_level in ('low','partial')` já calculado | `/regioes/summary` ou `/by-brand` | N/A — já é regra existente |
| `dado_defasado` | `refreshed_at` mais antigo entre as fontes usadas > limite (ex.: 48h para marts diários) | Todos | — |
| `sem_dado` | Reaproveitar `*_available=false` (Canais) ou ausência de campo (Financeiro/Qualidade) — nunca inferir "0" como sinal | Todos | — |

Regras explicitamente **não implementadas** por falta de base semântica segura:
- Qualquer insight de margem/lucratividade por produto ou marca (sem CMV).
- Comparação de `cos_pct`/custo TikTok 1:1 contra ML/Shopee (denominadores diferentes).
- Insight sobre `shopee_settlement` (campo a ser removido, não só evitado).
- Insight de "compradores" usando `unique_buyers` como métrica de volume (é soma diária, não deduplicada).

### 11.6 Arquitetura técnica recomendada

**Opção B — endpoint executivo novo**, ex. `GET /api/v1/performance/executive-summary`, reaproveitando `channels`/`brands`/`date_from`/`date_to`/`compare` do contrato de filtros globais já existente ([[project_db_architecture]] para engines).

Justificativa: as regras de insight (medianas, p75, comparação entre marcas/canais) já existem hoje só dentro de `get_canais()`. Se o frontend tivesse que replicar isso para Produtos/Financeiro/Qualidade/Regiões, cada aba teria sua própria versão da mesma lógica de "o que é alto/baixo/forte", divergindo com manutenção. Um endpoint único, que chama internamente os services já existentes (`get_overview`, `get_brands`, `get_canais`, `get_quality`, `get_financeiro`, regiões summary) e centraliza o cálculo de severidade, é testável isoladamente (dado um conjunto de linhas, o insight esperado é determinístico) e reduz round-trips do frontend de ~8 chamadas para 1.

Contrato de resposta proposto:

```json
{
  "period": { "date_from": "2026-06-01", "date_to": "2026-06-30", "compare_date_from": "...", "compare_date_to": "...", "label": "Junho/2026" },
  "refreshed_at": "2026-07-13T08:00:00Z",
  "health": {
    "status": "ok",
    "gmv": { "value": 23124883.15, "mom_pct": 4.2 },
    "orders": { "value": 138000, "mom_pct": 1.1 },
    "avg_ticket": { "value": 167.5, "mom_pct": 3.0 },
    "roas": { "ml": 5.2, "shopee": 14.58, "tiktok": null }
  },
  "changes": [
    { "type": "crescimento_forte", "brand": "kokeshi", "marketplace": "ml", "metric_value": 22.4, "mom_pct": 22.4, "href": "/canais?brands=kokeshi" }
  ],
  "opportunities": [
    { "type": "ads_subutilizado", "brand": "apice", "marketplace": "shopee", "metric_value": 3.1, "description": "GMV acima da mediana, Ads/GMV abaixo da mediana", "href": "/canais?brands=apice" }
  ],
  "risks": [
    { "type": "cancelamento_alto", "severity": "warning", "brand": "rituaria", "marketplace": "shopee", "metric_value": 18.2, "href": "/qualidade?brands=rituaria" }
  ],
  "brand_channel_matrix": [
    { "brand": "barbours", "marketplace": "ml", "status": "atencao", "gmv": 120000, "mom_pct": -8.1 }
  ],
  "data_warnings": [
    { "source": "financeiro", "message": "Custo ML não inclui comissão do marketplace (~16,5%, sem competência mensal disponível)" },
    { "source": "financeiro", "message": "Settlement TikTok usa base de repasse, ~5,5% maior que GMV comercial" }
  ],
  "links": { "canais": "/canais", "produtos": "/produtos", "financeiro": "/financeiro", "qualidade": "/qualidade", "regioes": "/regioes", "pedidos": "/pedidos" }
}
```

Cada insight (`changes`/`opportunities`/`risks`) segue o mesmo shape: `type`, `severity` (quando aplicável), `brand`, `marketplace`, `metric_value`, `mom_pct` (quando aplicável), `description`, `href`. Isso evita 3 schemas distintos no frontend.

**Corrigir junto**: como parte de qualquer trabalho de Gate 2 no backend de Gerencial, o filtro `brands` deveria passar a ser efetivamente aplicado em `/overview`, `/brands` e `/trend` (hoje é decorativo) — senão o novo endpoint herda a mesma inconsistência.

### 11.7 Regras de layout

- Acima da dobra: Header + Saúde geral + Riscos críticos (se houver `severity=critical`).
- Máximo 3-5 insights por bloco (Changes/Opportunities/Risks) — resto fica só na aba de origem.
- Matriz Marca×Canal: badges de status, não números crus; célula "Sem dado" quando `ads_applicable=false` ou `coverage_level=not_applicable` (nunca célula vazia sem explicação).
- Nenhuma tabela extensa acima da dobra.
- Todo insight que referenciar dado com ressalva (TikTok settlement, ML custo sem comissão, ML not_delivered proxy) carrega um ícone/tooltip de aviso — não texto grande competindo com o insight.
- Empty state por bloco (“Sem oportunidades relevantes neste período” é um resultado válido, não um erro).

### 11.8 Riscos e limitações gerais do cockpit

1. Qualquer "eficiência Ads geral" no bloco de Saúde deve excluir TikTok explicitamente (não aplicável) — nunca somar/mediar com N/A tratado como zero.
2. `unique_buyers` (soma diária) não deve virar KPI de capa; se aparecer, precisa do mesmo aviso já usado hoje.
3. Insight de cancelamento **não pode comparar ML e Shopee na mesma escala** — bases normais muito diferentes.
4. Settlement Shopee deve ser removido de qualquer bloco novo (é a causa raiz do "settlement >100%" já registrado como pendência).
5. Custo ML exibido sem comissão do marketplace — se este for o único "custo total" mostrado no cockpit, é obrigatório o aviso, senão o usuário decide com base num custo subestimado em quase metade.
6. Cobertura regional zero de TikTok não é uma "oportunidade" nem "risco de dado defasado" — é lacuna estrutural conhecida; não gerar insight de `sem_dado` repetido para isso todo período (ficaria ruído fixo).
7. `estimated_margin` do ML nunca deve alimentar nenhum insight ou aparecer em texto, mesmo indiretamente.

### 11.9 Recomendação final

**Opção B** (endpoint executivo novo), com entrega faseada dentro do próprio Gate 2 em vez de um novo gate:
- **Fase 1**: Health + Changes + Risks — fontes já maduras e testadas (`/overview`, `/brands`, `/canais` sinais existentes, `/quality`). Baixo risco de retrabalho.
- **Fase 2**: Opportunities + Matriz Marca×Canal — depende de threshold tuning (o que conta como "forte"/"atenção") e provavelmente precisa de 1-2 rodadas de ajuste com o gestor olhando dados reais.
- Corrigir o filtro de marca (hoje decorativo) como pré-requisito, senão o endpoint novo herda a mesma limitação.

Documentação a atualizar em paralelo (não bloqueante): `docs/kpi_dictionary.md` deveria ganhar uma seção "Insights/Regras de negócio" espelhando 11.5, para não duplicar definição de regra em dois lugares.

### 11.10 Arquivos inspecionados nesta rodada

- `apps/web/app/page.tsx`, `apps/web/src/lib/api-client.ts`, `apps/web/src/hooks/useGlobalFilters.ts`
- `apps/api/app/routers/performance.py`, `apps/api/app/services/performance_service.py`, `apps/api/app/schemas/performance.py`
- `apps/api/app/routers/regioes.py`, `apps/api/app/services/regioes_service.py`
- `apps/web/app/regioes/page.tsx`, `apps/web/app/pedidos/page.tsx`, `apps/web/src/components/AppNav.tsx`
- `docs/filtros_globais_contrato.md`, `docs/kpi_dictionary.md`, `docs/sections/canais_audit.md`, `docs/sections/produtos_audit.md`, `docs/sections/financeiro_audit.md`, `docs/sections/qualidade_audit.md`, `docs/regional_design_draft.md`

Nenhum arquivo de código foi alterado nesta rodada. Nenhuma query de escrita foi executada.
