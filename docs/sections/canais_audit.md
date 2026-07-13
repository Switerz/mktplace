# Auditoria — Aba Canais

Criado: 2026-06-24  
Atualizado: 2026-07-10 (Gate 1 — contrato de métricas comparáveis Ads/Custos/Desconto/Afiliados para redesenho gerencial; ver seção 14)\
Referência: Mai/2026  
Fonte Neon: `ep-lively-frost-a6eg1wh2.us-west-2.aws.neon.tech` (`marts.*`)  
Fonte RDS/Data Mart: `gold.*` — consulta parcial via Metabase; validações complementares pendentes

---

## 1. Objetivo da aba

Entender performance e perfil por marketplace/canal:

- **TikTok Shop:** origem do GMV por vídeo/live/card e conversão de visitantes.
- **Mercado Livre:** perfil de compradores novos vs. recorrentes, GMV por comprador.
- **Shopee:** perfil de compradores, visitantes, conversão e GMV por comprador.
- Comparar canais por marca e identificar oportunidades.

---

## 2. Componentes e endpoints

| Componente | Arquivo |
|---|---|
| Página | `apps/web/app/canais/page.tsx` |
| API client | `apps/web/src/lib/api-client.ts` (`fetchCanais`) |
| Endpoint | `GET /api/v1/performance/canais?marketplace=&ref_month=` |
| Router | `apps/api/app/routers/performance.py` → `perf_svc.get_canais()` |
| Service | `apps/api/app/services/performance_service.py` → `get_canais()` |
| Tabela Neon | `marts.fact_marketplace_daily_performance` + `marts.dim_loja` |

**Filtros disponíveis:** marketplace (all/tiktok/ml/shopee) × period (YYYY-MM).

**Estrutura da resposta:**
```json
{
  "ref_month": "2026-05",
  "marketplace": "all",
  "kpis": { ... },
  "brands": [ ... ]
}
```

---

## 3. Inventário de métricas — mapeamento campo a campo

### 3.1 TikTok

| Campo | Definição de negócio | Fonte Neon | Grain | Status | Risco |
|---|---|---|---|---|---|
| `tiktok_gmv` | GMV total TikTok no mês | `SUM(gmv)` WHERE mkt=1 | diário/loja → mensal/brand | ✅ Igual | Baixo |
| `tiktok_gmv_video` | GMV originado de vídeos | `SUM(gmv_video)` | diário/loja | ✅ Igual | Baixo |
| `tiktok_gmv_live` | GMV originado de lives | `SUM(gmv_live)` | diário/loja | ✅ Igual | Baixo |
| `tiktok_gmv_card` | GMV originado de card/vitrine | `SUM(gmv_card)` | diário/loja | ✅ Igual | Baixo |
| `tiktok_video_pct` | `gmv_video / gmv * 100` | Calculado Python sobre somas mensais | mensal/brand | ✅ Igual | Baixo |
| `tiktok_live_pct` | `gmv_live / gmv * 100` | Calculado Python | mensal/brand | ✅ Igual | Baixo |
| `tiktok_card_pct` | `gmv_card / gmv * 100` | Calculado Python | mensal/brand | ✅ Igual | Baixo |
| `tiktok_visitors` | Visitantes únicos da vitrine/dia | `SUM(visitors)` WHERE mkt=1 | diário/loja → mensal | ⚠️ Proxy | Cobertura parcial: nem todos os dias têm dado. Soma diária pode contar visitante recorrente múltiplas vezes |
| `tiktok_customers` | Compradores únicos | `SUM(unique_buyers)` WHERE mkt=1 | diário/loja → mensal (soma) | ⚠️ Proxy | **Soma diária, não deduplicada mensalmente.** Mai/26: 233.910. Pode ser >100% da base de compradores únicos reais |
| `tiktok_conversion_rate` (kpis) | Taxa de conversão da vitrine | `AVG(NULLIF(conversion_rate,0))` por marca → média entre marcas | média de dias não-zero | ⚠️ Proxy | Cobertura muito parcial: Mai/26 TikTok: 5/155 linhas não-zero. Valor plausível (~3.1%) mas representa poucos dias |
| `tiktok_conversion_rate` (brand) | Idem, por marca | `AVG(NULLIF(conversion_rate,0))` por marca | média dos dias não-zero daquela marca | ⚠️ Proxy | Mesmo risco de cobertura |

**Campos ausentes no Neon (existem no gold):**
- `tiktok_impressions`, `tiktok_page_views`, `tiktok_ctr_pct` — campos do `gold.v_channel_efficiency`. Não existe equivalente no mart.
- `gpm` (GMV per mille views) — requer `total_views`, ausente no mart.

### 3.2 Mercado Livre

| Campo | Definição de negócio | Fonte Neon | Grain | Status | Risco |
|---|---|---|---|---|---|
| `ml_unique_buyers` | Compradores únicos ML no mês | `SUM(unique_buyers)` WHERE mkt=2 | diário/loja → soma mensal | ⚠️ Proxy | **Gold deduplica por mês; mart soma por dia.** Mai/26: ~43.841 (soma diária). Overcount significativo vs. real deduplicado |
| `ml_new_buyers` | Novos compradores (sem histórico prévio na marca) | `SUM(new_buyers)` WHERE mkt=2 | diário/loja → soma | ⚠️ Proxy | Soma diária. Um comprador "novo" num dia pode repetir — a deduplicação mensal do gold é mais confiável |
| `ml_repeat_buyers` | Compradores com histórico prévio | `SUM(repeat_buyers)` WHERE mkt=2 | diário/loja → soma | ⚠️ Proxy | Mesmo problema de soma diária |
| `ml_new_buyer_pct` | `new_buyers / unique_buyers * 100` | Calculado (somas diárias) | mensal | ⚠️ Proxy | Numerador e denominador são somas diárias — proporção pode ser ruidosa |
| `ml_repeat_buyer_rate_pct` | `repeat_buyers / unique_buyers * 100` | Calculado | mensal | ⚠️ Proxy | Idem |
| `ml_gmv_per_buyer` | `ml_gmv / ml_unique_buyers` | Calculado | mensal | ⚠️ Proxy | Denominador sobreestimado → GMV/comprador subestimado vs. real |
| `ml_visitors` | Visitantes ML | NULL no mart | — | ❌ Ausente | Confirmado: `visitors=NULL` para ML em todos os períodos |
| `ml_conversion_rate` | Conversão ML | NULL no mart | — | ❌ Ausente | Confirmado: `conversion_rate=NULL` para ML |

### 3.3 Shopee

| Campo | Definição de negócio | Fonte Neon | Grain | Status | Risco |
|---|---|---|---|---|---|
| `shopee_gmv` | GMV Shopee no mês | `SUM(gmv)` WHERE mkt=3 | diário/loja | ✅ Igual | Baixo |
| `shopee_unique_buyers` | Compradores únicos Shopee | `SUM(unique_buyers)` WHERE mkt=3 | diário/loja → soma | ⚠️ Parcial | Mai/26: 98.465. Pode ser soma diária não deduplicada mensalmente |
| `shopee_new_buyers` | Novos compradores Shopee | `SUM(new_buyers)` | diário/loja → soma | ⚠️ Parcial | Soma diária |
| `shopee_repeat_buyers` | Compradores recorrentes Shopee | `SUM(repeat_buyers)` | diário/loja → soma | ⚠️ Parcial | Soma diária |
| `shopee_new_buyer_pct` | `new_buyers / unique_buyers * 100` | Calculado | mensal | ⚠️ Parcial | Proporcional às somas diárias |
| `shopee_repeat_buyer_rate_pct` | `repeat_buyers / unique_buyers * 100` | Calculado | mensal | ⚠️ Parcial | Idem |
| `shopee_gmv_per_buyer` | `shopee_gmv / shopee_unique_buyers` | Calculado | mensal | ⚠️ Parcial | Denominador pode ser sobreestimado |
| `shopee_visitors` | Visitantes do perfil Shopee | `SUM(visitors)` WHERE mkt=3 | diário/loja | ✅ Parcial | Mai/26: cobertura boa (755/851 linhas). Soma diária |
| `shopee_conversion_rate` (kpis) | `shopee_unique_buyers / shopee_visitors * 100` | Calculado sobre somas | mensal | ⚠️ Proxy | Calculado sobre somas mensais, não como média de daily conversion_rates. Diferente da lógica TikTok |
| `shopee_conversion_rate` (brand) | Idem por marca | `_pct(sh_buyers, sh_vis, 2)` | mensal | ⚠️ Proxy | Mesma ressalva |

---

## 4. Queries executadas e resultados Mai/2026

> **Nota:** Validação direta executada contra Neon para o endpoint `/canais` e para `marts.fact_marketplace_daily_performance`. Também houve consulta parcial ao Data Mart via Metabase para `gold.tiktok_brand_daily` e inspeção de `gold.v_channel_efficiency`.

### 4.1 Resumo de cobertura do mart (base gold_vs_marts_matrix)

| Canal | Linhas | Período | conv_non_zero | avg_conv_rate | GMV total |
|---|---|---|---|---|---|
| TikTok (mkt=1) | 890 | dez/25 → jun/26 | 236 | 0.0285 (ratio) | 68.8M |
| ML (mkt=2) | 539 | dez/25 → jun/26 | 0 | NULL | 14.4M |
| Shopee (mkt=3) | 851 | jan/26 → jun/26 | 755 | 3.08 (pct) | 21.2M |

### 4.2 TikTok Mai/2026

- **visitors:** 215.178 (soma diária — cobertura parcial)
- **unique_buyers:** 233.910 (soma diária — pode contar comprador múltiplas vezes)
- **conversion_rate (avg daily):** ~3.1% (de 5 dias com dados não-zero em ~155 linhas TikTok mai/26)
- **gmv_video / gmv_live / gmv_card:** campos populados ✅
- **gold.tiktok_brand_daily validado via Metabase:** GMV total R$ 13.395.985,86; video 50,7%; live 21,6%; card 27,7%; visitors 215.178; customers 233.910; Barbours 72,5% do GMV TikTok.

### 4.3 ML Mai/2026

- **unique_buyers:** ~43.841 (soma diária, 93 linhas: 3 marcas × 31 dias)
- **visitors:** NULL — não populado
- **conversion_rate:** NULL — não populado

### 4.4 Shopee Mai/2026

- **unique_buyers:** 98.465
- **visitors:** cobertura boa (755/851 linhas não-zero)
- **avg conversion_rate:** ~3.51% (campo no mart como pct >1, tratado corretamente por `_pct_from_source`)

---

## 5. Bugs encontrados

### Bug 1 — Conversão TikTok na linha TOTAL: 108.7% vs. 3.1% no card (CORRIGIDO)

**Arquivo:** `apps/web/app/canais/page.tsx`  
**Linha afetada:** linha de TOTAL da tabela "Atribuição TikTok por Marca" (coluna Conversão)

**Causa raiz:**  
A linha TOTAL calculava `tkConvTotal = SUM(tiktok_customers) / SUM(tiktok_visitors) * 100`.

- `tiktok_customers` = `SUM(unique_buyers)` diário = **233.910** em Mai/26 (soma de compradores únicos por dia)
- `tiktok_visitors` = **215.178** (soma de visitantes por dia)
- `233.910 / 215.178 = 108.7%` — impossível semanticamente

O card superior usa `kpis.tiktok_conversion_rate` (~3.1%), calculado pelo backend como média das daily `conversion_rate` não-zero. Este é o valor correto porque preserva o denominador correto de cada dia.

**Correção aplicada:**  
Substituída a exibição de `tkConvTotal` por `kpis?.tiktok_conversion_rate` na célula TOTAL.

```diff
- {tkConvTotal != null ? `${tkConvTotal.toFixed(1)}%` : "—"}
+ {kpis?.tiktok_conversion_rate != null ? `${kpis.tiktok_conversion_rate.toFixed(1)}%` : "—"}
```

**Resultado esperado Mai/2026:**
- Card TikTok conversion: ~3.1%
- Total da tabela TikTok: ~3.1% (agora consistente com o card)

---

## 6. Métricas confiáveis

| Métrica | Justificativa |
|---|---|
| `tiktok_gmv` e breakdown video/live/card (R$) | Colunas populadas, semântica clara, validada no Neon |
| `tiktok_video_pct / live_pct / card_pct` | Proporções calculadas sobre somas mensais corretas |
| `shopee_gmv` | Populado, validado |
| `tiktok_conversion_rate` (kpis, card) | Valor de `avg_conversion_rate` via SQL — plausível ~3.1% |
| `shopee_visitors` | Boa cobertura diária (755/851 linhas) |
| `shopee_conversion_rate` (por marca) | Cobertura boa; calculado sobre somas mensais |

---

## 7. Métricas proxy (usar com ressalva)

| Métrica | Limitação |
|---|---|
| `tiktok_visitors` | Soma diária — contagem pode incluir visitante recorrente; cobertura parcial de dias |
| `tiktok_customers` | Soma diária de unique_buyers — não deduplicado mensalmente. **Não deve ser usado para calcular conversão total** (foi o bug) |
| `tiktok_conversion_rate` (brand/total) | AVG de dias não-zero — cobertura muito parcial (5/155 linhas em mai/26 TikTok). Valor plausível mas incerto |
| `ml_unique_buyers` | Soma diária vs. deduplicado mensal do gold. Overcount considerável |
| `ml_new_buyers / ml_repeat_buyers` | Somas diárias — proporções aproximadas, não exatas |
| `ml_new_buyer_pct / ml_repeat_buyer_rate_pct` | Calculados sobre somas diárias |
| `ml_gmv_per_buyer` | Denominador sobreestimado → valor subestimado |
| `shopee_unique_buyers / new / repeat` | Mesma questão de soma diária |
| `shopee_conversion_rate` (kpis) | Calculado como `SUM(buyers)/SUM(visitors)` — não como média de daily rates |

---

## 8. Métricas ausentes no Neon

| Campo | Fonte gold | Impacto |
|---|---|---|
| `tiktok_impressions` | `gold.v_channel_efficiency` | Não exibido na aba — campo já `null` no tipo |
| `tiktok_page_views` | `gold.v_channel_efficiency` | Idem |
| `tiktok_ctr_pct` | `gold.v_channel_efficiency` | Idem |
| `gpm` TikTok | `gold.tiktok_brand_daily.total_views` | Campo ausente — `total_views` não existe no mart |
| `ml_visitors` | `gold.ml_gestao_diaria.visitors` | `visitors=NULL` para ML no mart |
| `ml_conversion_rate` | `gold.ml_gestao_mensal` | `conversion_rate=NULL` para ML no mart |

---

## 9. Oportunidades futuras vindas do Data Mart (gold.*)

> O Data Mart foi consultado parcialmente. `gold.tiktok_brand_daily` confirmou os principais números de TikTok e `gold.v_channel_efficiency` existe com colunas de funil. Ainda falta mapear quais campos devem ser copiados para o mart do Neon e validar cobertura por mês/marca.

### 9.1 TikTok

| Oportunidade | Fonte gold (a confirmar) | Valor para a aba |
|---|---|---|
| `total_views` para GPM | `gold.tiktok_brand_daily.total_views` | GPM = GMV / views × 1000. Métrica de eficiência de conteúdo |
| Impressions / page_views / CTR por canal | `gold.v_channel_efficiency` (`impressions`, `page_views`, `unique_pv`, `ctr_pct`, `conversion_pct`, `gmv_per_1k_impressions`, `gmv_per_page_view`) | Funil completo: impressão → página → compra |
| Dados de creators | `gold.tiktok_creator_daily` | Top creators por marca, GMV por criador, GPM por criador |
| Novos vídeos postados | `gold.tiktok_brand_daily.new_videos_posted` | Volume de produção de conteúdo |
| Lives e minutos ao vivo | `gold.tiktok_brand_daily` | GMV por live, GMV por minuto de live |

**Ranking de oportunidade TikTok sugerido:**
- Marcas com alto `card_pct` + baixa conversão: oportunidade de live/vídeo
- Marcas com alta `card_pct` absoluta podem estar sub-aproveitando conteúdo orgânico

### 9.2 Mercado Livre

| Oportunidade | Fonte gold (a confirmar) | Valor para a aba |
|---|---|---|
| `unique_buyers` deduplicado mensal | `gold.ml_gestao_mensal.unique_buyers` | Substituir proxy de soma diária. Seria dado mais confiável para GMV/buyer, novos%, recompra% |
| Visitantes ML | `gold.ml_gestao_diaria.visitors` | Funil de conversão ML (hoje ausente no mart) |
| Conversion rate ML | `gold.ml_gestao_mensal.conversion_rate` | Completaria o funil comparativo entre canais |
| Novos vs. recorrentes deduplicados | Gold | Perfil de aquisição mais confiável do que soma diária |

### 9.3 Shopee

| Oportunidade | Fonte gold (a confirmar) | Valor para a aba |
|---|---|---|
| Compradores deduplicados mensais | exports locais → verificar gold | Confirmar se soma diária ou mensal é o modelo correto nos exports |
| Visitantes por marca com cobertura validada | exports locais | Verificar se todos os dias têm dado de visitors por marca |

---

## 10. Recomendações de curto prazo

1. **[FEITO]** Corrigir linha TOTAL da tabela TikTok: usar `kpis.tiktok_conversion_rate` em vez de recalcular de somas mensais.

2. **Adicionar nota visual** na tabela TikTok sobre `tiktok_customers` ser soma diária (pode aparecer > visitantes por marca em alguns casos). Já existe nota de rodapé sobre cobertura de visitantes — pode ser expandida.

3. **Adicionar badge "proxy"** ao lado de "Compradores ML" e "Compradores Shopee" para sinalizar que o número é soma diária, não deduplicado mensal. O usuário precisa saber que `43.841 compradores ML` é uma contagem inflada.

4. **ML — ocultar GMV/comprador ML** ou adicionar nota de que é subestimado (denominador inflado). O valor absoluto pode enganar.

5. **Shopee — alinhar lógica de `conversion_rate`:** o kpis usa `SUM(buyers)/SUM(visitors)` enquanto por marca usa `_pct(sh_buyers, sh_vis)`. Ambos são somas — verificar se há divergência visível entre o card e o total da tabela Shopee (como havia no TikTok).

---

## 11. Recomendações de médio prazo

1. **Migrar `ml_unique_buyers` para deduplicado mensal.** Opção A: adicionar coluna `monthly_unique_buyers` no mart (calculado como COUNT(DISTINCT customer_id) por mês/marca). Opção B: manter `gold.ml_gestao_mensal` como fonte canônica via `gold_service`.

2. **Trazer `total_views` TikTok para o mart.** Destravaria GPM no `/brand-detail` e habilitaria métrica de eficiência de conteúdo na aba Canais.

3. **Popularem `visitors` e `conversion_rate` para ML no mart.** Hoje é NULL em todos os períodos — completaria o funil comparativo.

4. **Criar ranking de oportunidades por canal:**
   - Alto visitante + baixa conversão → foco em otimização de vitrine
   - Alto GMV + baixa recompra → foco em fidelização
   - Alto `card_pct` + baixo vídeo → oportunidade de conteúdo orgânico

5. **Drilldown temporal por canal:** série mensal de video/live/card para TikTok, e série de novos/recorrentes para ML e Shopee.

---

## 12. Status final por canal

| Canal | Dados de GMV | Atribuição de canal | Compradores | Visitantes/Conversão | Status geral |
|---|---|---|---|---|---|
| TikTok | ✅ Confiável | ✅ Confiável (video/live/card) | ⚠️ Proxy (soma diária) | ⚠️ Proxy (cobertura parcial, bug corrigido) | **Validado com limitações** |
| Mercado Livre | ✅ Confiável | N/A | ⚠️ Proxy (soma diária vs. deduplicado) | ❌ Ausente | **Parcialmente validado** |
| Shopee | ✅ Confiável | N/A | ⚠️ Proxy (soma diária) | ⚠️ Proxy (conversão calculada sobre somas) | **Validado com limitações** |

---

## 13. Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `apps/web/app/canais/page.tsx` | Bug fix: linha TOTAL TikTok — conversão substituída de `tkConvTotal` para `kpis?.tiktok_conversion_rate`; removido cálculo legado não usado; rodapés de ML/Shopee ajustados para não afirmar deduplicação mensal não validada |
| `docs/sections/canais_audit.md` | Criado (este documento) |

---

## 14. Gate 1 — Contrato de métricas comparáveis (Ads, custo de marketplace, frete, desconto, afiliados) — 2026-07-10

### 14.0 Contexto do pedido

O gestor pediu mais comparáveis entre marcas para identificar oportunidades por marca/marketplace: Ads, afiliados, custo de marketplace, desconto médio. Esta seção audita o que existe hoje (sem inferir semântica por nome de coluna), define o que pode e o que não pode ser comparado entre marcas/canais, e propõe o redesenho da tela. **Nenhum código foi alterado nesta rodada** — é o Gate 1 (auditoria + contrato), conforme escopo aprovado.

### 14.1 O que a tela usa hoje (código atual, confirmado em `performance_service.py::get_canais`, 2026-07-10)

A query de `get_canais()` só lê: `gmv, gmv_video, gmv_live, gmv_card, visitors, unique_buyers, new_buyers, repeat_buyers, canceled_orders, orders, conversion_rate`. **Zero métricas de custo, ads, frete ou desconto chegam à aba Canais hoje** — isso é o gap literal que o gestor sentiu. Essas colunas (`ad_spend`, `ad_revenue`, `ad_clicks`, `ad_impressions`, `total_fees`, `seller_shipping_cost`, `total_settlement`) **já existem na mesma tabela** (`marts.fact_marketplace_daily_performance`) e **já são usadas pela aba Financeiro** (`get_financeiro()`) — ou seja, para Ads/Custo/Frete não é preciso nova fonte de dado, é extensão de query já provada. Desconto e afiliados são diferentes: nenhuma dessas duas métricas existe nessa tabela, em nenhum marketplace (detalhe na seção 14.2).

### 14.2 Tabela de disponibilidade de métricas por marketplace

Convenção: **✅ Confiável e comparável** · **⚠️ Existe, mas com ressalva** (ver nota) · **❌ Ausente** (não existe em forma agregada usável).

| Métrica | TikTok | Mercado Livre | Shopee | Nota |
|---|---|---|---|---|
| GMV | ✅ | ✅ | ✅ | Validado nas 3 auditorias (Gerencial/Canais/Financeiro) |
| Pedidos | ✅ | ✅ | ✅ | `orders` no mart = pagos/ativos |
| Ticket médio | ✅ | ✅ | ✅ | `gmv/orders` |
| Ads spend | ❌ N/A | ✅ | ✅ | TikTok não opera ads pagos geridos via Ads Manager — não é "dado ausente", é métrica **não aplicável** ao modelo do canal (usa COS%/comissão como custo de plataforma) |
| Ads revenue | ❌ N/A | ✅ | ✅ | Idem |
| ROAS | ❌ N/A | ✅ (12–15x mai/26) | ✅ (12–16x mai/26) | Idem — não criar coluna "ROAS TikTok = —" sem explicar que é N/A, não zero |
| ACOS | ❌ N/A | ✅ | ⚠️ calculável (1/ROAS) | Shopee não expõe ACOS pronto no mart, mas é trivial derivar de ad_spend/ad_revenue |
| Comissão/afiliados (isolado) | ❌ | ❌ | ❌ | Nenhuma coluna discriminada em nenhum marketplace. Afirmação antiga "TikTok fees incluem afiliados" foi removida da UI do Financeiro por não ser verificável (ver `financeiro_audit.md` C9) |
| Taxas/encargos de marketplace (fee %) | ⚠️ | ❌ | ✅ | TikTok: `total_fees` vem do subsistema de repasses (statements), com base ~5,5% diferente do GMV comercial (comprovado, `financeiro_audit.md` §11.1) — usável como referência, não como número exato mês a mês. ML: `total_fees` é `NULL` no mart; comissão real existe em `gold.ml_produto_pnl.marketplace_fee` mas é **cumulativa por produto, sem coluna de data** — não aplicável a um mês específico. Shopee: validado ao centavo contra XLSX/API (Bug 3/§11.2) |
| Frete seller | ❌ | ✅ | ✅ | TikTok não tem `seller_shipping_cost` agregado; `raw.tiktok_shop_orders` tem `shipping_fee`/`original_shipping_fee` (grão pedido, semântica de frete cobrado do comprador, não confirmada como custo do seller) — não usar sem confirmar |
| Desconto total / médio / Desconto ÷ GMV | ❌ | ❌ | ❌ | Ver seção 14.3 — existe em **camada raw**, não em gold/mart, e com uma ambiguidade não resolvida no caso Shopee |
| Settlement / repasse líquido | ⚠️ (proxy) | ❌ | ❌ (removido da UI) | Nenhum dos 3 marketplaces tem um "repasse líquido" confiável mês a mês hoje — não recriar esse indicador na Canais |

### 14.3 Por que Desconto e Afiliados não entram nesta rodada (detalhe)

**Desconto:**
- TikTok: `raw.tiktok_shop_orders` (RDS) tem `platform_discount` e `seller_discount` por pedido — mas **nunca foram agregados** para `gold.tiktok_brand_daily` (a tabela usada como fonte do mart) nem para `marts.fact_marketplace_daily_performance`. Precisaria de ETL novo.
- Shopee: o pipeline de produtos (`pipelines/connectors/shopee/_parser.py`) mapeia "Desconto do vendedor" para `seller_discount`, mas o export tem **essa coluna duplicada** — a 2ª ocorrência é tratada como `seller_discount_2` com semântica **não confirmada**, e a documentação (`docs/staging_shopee_contract.md` §12) é explícita: "não alimentar Gold enquanto não confirmado". Além disso, isso está na camada de staging Shopee (draft, 2 revisões, ainda não fechada — ver memória de projeto), não no mart usado pela Canais.
- Mercado Livre: nenhuma coluna de desconto identificada em `raw.ml_orders` nem nas demais tabelas raw/gold mapeadas em `docs/source_mapping.md`. Sem fonte confirmada.
- **Conclusão:** não existe hoje nenhum caminho para "Desconto médio"/"Desconto ÷ GMV" comparável entre os 3 canais sem (a) nova ETL de agregação raw→gold para TikTok, (b) resolver a ambiguidade `seller_discount_2` da Shopee com o Seller Center, e (c) localizar uma fonte para ML. Mostrar essa métrica agora seria inventar um número ou, pior, mostrar 3 marketplaces com bases de desconto diferentes (ou ausentes) como se fossem comparáveis.

**Afiliados:**
- Nenhuma tabela (raw ou gold) mapeada até hoje tem uma coluna discriminada de comissão de afiliados para nenhum dos 3 marketplaces. A hipótese de que estaria embutida em `tiktok_fees` foi investigada e explicitamente descartada da UI por não ser verificável. Precisaria de descoberta de fonte nova (ex.: export de afiliados/creators do TikTok Shop, relatório de afiliados Shopee) antes de qualquer contrato de métrica — esforço maior que o de Ads/Custo/Frete, que só precisam de extensão de query.

### 14.4 Contrato de métricas comparáveis

| Métrica | Definição | Fórmula | Fonte | Grão | Marketplaces cobertos | Comparável entre marcas/canais? | Limitações |
|---|---|---|---|---|---|---|---|
| GMV | Faturamento bruto | `SUM(gmv)` | `marts.fact_marketplace_daily_performance` | dia/loja → mês/marca | TK, ML, SH | ✅ Sim | — |
| Ads ÷ GMV | Intensidade de investimento em ads | `SUM(ad_spend)/SUM(gmv)` | idem | mês/marca | ML, SH | ✅ Sim entre ML e SH · ❌ N/A TikTok | TikTok não tem ads geridos — exibir "N/A" explícito, nunca "0%" |
| ROAS | Retorno sobre ads | `SUM(ad_revenue)/SUM(ad_spend)` | idem | mês/marca | ML, SH | ✅ Sim entre ML e SH · ❌ N/A TikTok | Requer `ad_spend>0` |
| Custo marketplace ÷ GMV (taxa) | Taxas/comissão de plataforma como % do GMV | `SUM(total_fees)/SUM(gmv)` (TikTok: `abs()`) | idem | mês/marca | SH (confiável) · TK (proxy) · ML (sem dado) | ⚠️ Parcial — comparar SH↔TK só com aviso de base diferente; ML fica de fora com badge "sem dado" | TikTok: base settlement ≠ GMV comercial (~5,5%). ML: campo NULL |
| Frete seller ÷ GMV | Custo de frete pago pelo seller como % do GMV | `SUM(seller_shipping_cost)/SUM(gmv)` | idem | mês/marca | ML, SH | ✅ Sim entre ML e SH · ❌ Ausente TikTok | — |
| Desconto ÷ GMV / Desconto médio | Desconto concedido (plataforma+seller) por pedido/GMV | — | Nenhuma tabela gold/mart | — | Nenhum | ❌ Não implementar | Ver 14.3 — requer ETL novo |
| Afiliados ÷ GMV | Comissão paga a afiliados | — | Nenhuma tabela gold/mart | — | Nenhum | ❌ Não implementar | Ver 14.3 — requer fonte nova |

### 14.5 Proposta de novo desenho da aba Canais

**KPIs de topo** (cards, mesmo padrão visual do `KpiCard` já usado):
1. GMV total do período (com MoM, como hoje).
2. "Melhor eficiência de Ads" — marca/canal com maior ROAS entre ML e Shopee (TikTok fica de fora deste card por N/A, não por "pior").
3. "Maior oportunidade de Ads" — marca com GMV alto e Ads/GMV abaixo da mediana do canal (regra `ads_subutilizado`, 14.6).
4. "Maior pressão de custo" — marca com maior (Ads+Fees+Frete)/GMV disponível, com badge indicando quais componentes entraram na soma (mesmo padrão já usado no Financeiro para "Ads + Frete / GMV" do ML).
5. Card fixo "Cobertura de dados" — texto direto: "Desconto e comissão de afiliados: sem dado nesta rodada" + link para esta seção. Não é um KPI de negócio, é um alerta de transparência — sempre visível, não escondido em rodapé.

**Matriz principal marca × marketplace** (tabela, uma seção por marketplace como já é hoje, mantendo os blocos TikTok/ML/Shopee):
- Marca, GMV, Pedidos, Share da marca no canal (%), Ads ÷ GMV, ROAS, Custo marketplace ÷ GMV (com badge de confiabilidade), Frete ÷ GMV, Sinal/Oportunidade.
- Colunas de Desconto e Afiliados **não entram na tabela agora** — ficariam vazias/enganosas. Se o gestor quiser sinalizar a ausência na própria matriz, a alternativa aceitável é uma coluna única "Cobertura" com um ícone/tooltip por marca, não uma coluna numérica fantasma.
- TikTok: sem colunas de Ads/ROAS/Frete (N/A) — manter as colunas de atribuição de canal (vídeo/live/card) que já são o diferencial confiável desse canal.
- ML: sem coluna de Custo marketplace ÷ GMV (badge "sem dado" em vez de omitir a linha).

**Seção "Oportunidades"** (lista/cards abaixo da matriz, agrupados por regra — 14.6):
- Ads subutilizado
- ROAS forte
- Custo alto
- Frete alto (mesma lógica de percentil que custo, quando o dado existir)
- Dado ausente/incomparável (Desconto, Afiliados, Custo ML) — sempre presente como categoria própria, não como ausência silenciosa

### 14.6 Regras de oportunidade (simples, testáveis, sem "regras mágicas")

Todas as regras comparam a marca **contra a mediana das marcas do mesmo canal no mesmo período** — nunca um limiar absoluto hardcoded, para se manter válido conforme o negócio cresce/encolhe.

| Regra | Condição | Aplica-se a |
|---|---|---|
| `ads_subutilizado` | GMV da marca ≥ mediana do canal **E** `ads_gmv_pct` da marca < mediana do canal **E** (ROAS da marca ≥ mediana do canal **OU** `ad_spend = 0`) | ML, Shopee |
| `roas_forte` | ROAS da marca ≥ mediana do canal | ML, Shopee |
| `custo_alto` | `custo_marketplace_gmv_pct` da marca ≥ percentil 75 do canal | Shopee sempre · TikTok com badge de base mista · ML nunca (sem dado) |
| `frete_alto` | `frete_gmv_pct` da marca ≥ percentil 75 do canal | ML, Shopee |
| `desconto_alto` | — | Não implementar (sem dado) |
| `afiliado_relevante` | — | Não implementar (sem dado) |
| `sem_dado` | Métrica não disponível/não aplicável para o marketplace | Sempre exibida como sinal explícito, nunca omitida |

Cada card de oportunidade deve exibir a fórmula e o valor comparado (ex.: "Ads ÷ GMV: 4,2% vs. mediana do canal 7,8%") para ser auditável na própria UI, sem número mágico escondido.

### 14.7 Recomendação de implementação

**Caminho recomendado: B — implementar parcialmente com avisos de cobertura, em duas frentes de esforço distinto:**

1. **Ads, ROAS, Custo marketplace÷GMV, Frete÷GMV — fazer agora.** Dado já existe no mesmo mart já usado pela Canais; é extensão de `get_canais()` com os mesmos campos que `get_financeiro()` já usa (`ad_spend`, `ad_revenue`, `ad_clicks`, `ad_impressions`, `total_fees`, `seller_shipping_cost`). Sem risco de dado novo — risco é só de UX (não confundir N/A com zero, não comparar TikTok fee% com Shopee fee% sem aviso).
2. **Desconto e Afiliados — bloquear nesta rodada (caminho C para essas duas métricas específicas).** Exigem trabalho de dados que este Gate não cobre: ETL raw→gold para desconto TikTok, resolução da ambiguidade `seller_discount_2` da Shopee com o Seller Center, e descoberta de fonte para ML e para afiliados em qualquer canal. Registrar como pendência de dados (não como bug da tela).

Não é A puro (dado 100% pronto) nem D (não precisa de endpoint novo — é extensão do endpoint existente) nem C total (parte do pedido é implementável hoje).

### 14.8 Arquivos que mudariam na próxima rodada (se aprovado)

| Arquivo | Mudança prevista |
|---|---|
| `apps/api/app/services/performance_service.py::get_canais` | Adicionar `ad_spend, ad_revenue, ad_clicks, ad_impressions, total_fees, seller_shipping_cost` à query e aos cálculos por marca (mesmo padrão de `get_financeiro`) |
| `apps/api/app/schemas/performance.py::CanaisKpis/CanaisBrandRow` | Novos campos opcionais: `*_ads_gmv_pct`, `*_roas`, `*_custo_marketplace_gmv_pct`, `*_frete_gmv_pct` + flags de cobertura |
| `apps/web/app/canais/page.tsx` | Novas colunas na matriz por canal, card de "Cobertura de dados", seção "Oportunidades" |
| `apps/web/src/lib/api-client.ts` | Atualizar tipos `CanaisKpis`/`CanaisBrandRow` |
| `docs/sections/canais_audit.md` | Esta seção (14) — Gate 1 concluído |

---

## 15. Gate 2 — QA de produção (2026-07-13): granularidade dos Ads Shopee

Implementação do Gate 2 (`get_canais()` estendido com `channel_rows`/`channel_medians`) validada em produção (Render + Vercel) após redeploy. Todos os itens do contrato da seção 14 confirmados corretos: TikTok em N/A para Ads/ROAS/ACOS/Frete, ML em "Sem dado" para custo marketplace, Shopee com valores reais quando disponível, sinais de oportunidade coerentes, sem erros de console.

Uma checagem de QA comparou ROAS/ACOS da Shopee entre mai/2026 e jun/2026 e encontrou os dois praticamente iguais por marca, apesar de `ad_spend`/`ad_revenue` absolutos serem diferentes entre os meses. Investigação direta contra a API (comparação numérica, sem alterar cálculo) confirmou a causa raiz — **não é bug do Gate 2**, é uma característica já documentada da fonte de dados:

- O export de Ads da Shopee (`Dados*.csv`, ver `docs/source_mapping.md` seção Shopee) **não tem granularidade diária** — vem como total do período.
- O pipeline distribui esse total como média diária ao longo dos dias cobertos pelo arquivo (limitação operacional já registrada em `docs/source_mapping.md`).
- Como `ad_spend` e `ad_revenue` diários são ambos fatias proporcionais do mesmo total fixo, a razão entre eles (ROAS/ACOS) tende a ficar estável para **qualquer recorte de data dentro da mesma janela de distribuição** — mesmo que os valores absolutos mudem corretamente com o período (confirmado: `ad_spend`/`ad_revenue` de mai/2026 ≠ jun/2026 em todas as 5 marcas testadas).
- Adicionalmente, **pedidos/GMV e Ads da Shopee têm coberturas temporais diferentes**: a sincronização de pedidos (`shopee_daily`) cobre até 31/05/2026, enquanto Ads (`shopee-ads_daily`) cobre até 20/06/2026. Por isso jun/2026 mostra `gmv=0`/`orders=0` com `ad_spend`/`ad_revenue` reais — um gap de cobertura pré-existente, não introduzido por este Gate.
- Quando `gmv=0` e há dado de Ads no período, `ads_gmv_pct` fica `None` (denominador zero) e a UI mostra `—` corretamente — nunca "0%". Esse terceiro estado (distinto de N/A e de Sem dado) foi adicionado à legenda do rodapé da tabela em `apps/web/app/canais/page.tsx` nesta rodada.

**Nenhuma alteração de cálculo foi feita** — `roas`/`acos_pct`/`ads_gmv_pct` continuam `ad_revenue/ad_spend`, `ad_spend/ad_revenue*100` e `ad_spend/gmv*100` respectivamente, exatamente como no Gate 2. O ajuste desta rodada foi só de clareza textual (legenda) e desta nota de documentação.

**Pendência de dados registrada (fora do escopo desta rodada):** se a granularidade diária real dos Ads Shopee ou a reconciliação de cobertura pedidos×ads vier a ser necessária para decisões operacionais, requer nova fonte (relatório com granularidade diária da Shopee Ads) — mesma categoria de pendência de dados já registrada na seção 14.3.
