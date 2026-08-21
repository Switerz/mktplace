# Gate UE0 — Auditoria de atribuição, afiliados e margem por anúncio

Data: 2026-08-19 · **Revisão 2** (correção terminal)
Tipo: auditoria **read-only**. Nenhum código, schema, pipeline ou payload foi alterado.
Branch: `unit-economics-audit` · SHA-base do worktree: `309b6bfa2ae1c031befc7a62b3c0f6be46ba324c`
`origin/main` na revisão 2: `e67594866486e252fe19777aec502d8be4f45bff` (avançou; **sem rebase/merge nesta rodada**)
Escopo: os três pedidos do stakeholder — custo/retorno de afiliados na aba Canais, share de vendas por canal, e margem por anúncio.

## Convenção de rótulos

Toda afirmação material deste documento carrega um destes marcadores:

- **[FATO]** — verificado por consulta read-only, definição de catálogo ou leitura de código versionado. Reproduzível.
- **[INFERÊNCIA]** — apoiado em evidência, mas não provado. Explicita o que falta para virar fato.
- **[RECOMENDAÇÃO]** — juízo de engenharia/produto desta auditoria. Não é propriedade da fonte.
- **[DECISÃO]** — pertence ao proprietário/stakeholder. A auditoria pode recomendar, não resolver.

## Estado de implementação do contrato da §11

| Gate | Commit | O que entregou |
|---|---|---|
| **UE-F1A** (backend) | `417be72` `fix(api): corrige mix de conteudo do tiktok` | Denominador do mix passou a ser `gmv_video+gmv_live+gmv_card`; campos aditivos `tiktok_content_gmv_base` e `tiktok_content_gmv_divergence_pct` em `CanaisKpis`/`CanaisBrandRow` |
| **UE-F1B** (frontend) | `feat(web): corrige mix de conteudo do tiktok` | Aba Canais passou a exibir "Mix do GMV de conteúdo", com faixa de reconciliação separada, formatação pt-BR e sem categoria residual. Versionado em `main`; a publicação da interface é etapa de deploy própria |

**[FATO]** O UE-F1B encontrou **dois defeitos adicionais do mesmo tipo**, que a revisão 2 não havia mapeado porque a auditoria original olhou o backend:

1. `apps/web/app/canais/page.tsx` **recalculava** os percentuais do total no cliente (`tkVidTotal / tkGmvTotal * 100`), com fallback `: 0` que fabricava 0% na ausência de base — o mesmo defeito do backend, duplicado.
2. `apps/web/src/lib/api-client.ts` fazia o mesmo no mock (`tkVid / tkGmv`), e os percentuais das marcas no mock eram literais escritos à mão derivados do GMV comercial.

Ambos corrigidos no UE-F1B: o frontend passou a consumir os campos do backend para dados live, e o mock passou a derivar o contrato por construção via `computeContentMix`.

**[FATO]** `ChannelMixChart` foi auditado e **não** tinha o defeito: trabalha só com valores monetários absolutos empilhados (`gmv_video/live/card`), sem percentual e sem denominador, e serve `/brand-detail`, não Canais. Ficou intacto.

**[FATO]** O risco descrito na §7.1 ("risco ativo, em produção") deixa de existir quando o UE-F1B chegar à interface publicada. O backend já está publicado (abaixo); a tela passa a exibir o contrato correto a partir do deploy do frontend.

### Smoke read-only do backend publicado — 21/08/2026, 17:50 UTC

**[FATO] Resultado: PASS.** `GET /openapi.json` e `GET /api/v1/performance/canais` no backend canônico, somente leitura, com `Cache-Control: no-cache` e parâmetro de cache-busting. `cf-cache-status: DYNAMIC` e `x-render-origin-server: uvicorn` em todas as respostas — leitura viva do origin, sem cópia intermediária.

| Verificação | Resultado |
|---|---|
| `/openapi.json` | HTTP 200 |
| `CanaisKpis` | **27 campos**, os dois novos presentes |
| `CanaisBrandRow` | **30 campos**, os dois novos presentes |
| Payload `kpis` | ambos os campos presentes |
| Payload `brands` | ambos os campos em **5/5** linhas |
| `base == gmv_video+gmv_live+gmv_card` (±R$ 0,01) | OK nas 5 marcas e no total |
| `pct_x == componente/base × 100` (±0,06 p.p.) | OK |
| Soma dos três percentuais | 100,0% por marca · **99,9%** no total — dentro da tolerância de arredondamento, sem ajuste artificial |
| `divergência == (base − gmv)/gmv × 100` (±0,1 p.p.) | OK |
| `kpis.base == Σ bases das marcas` (±R$ 0,01) | OK |
| Categoria residual "Outros" | **inexistente** no payload |
| Janela sem dado (jan/2024, jan/2025) | os dois campos **serializados com `null`**, percentuais `null` — nunca 0 |

Duas medições anteriores (20 e 21/08) encontraram 25/28 e foram classificadas como **BLOCKED BY DEPLOY ORDER**; o build no ar era anterior ao `417be72`. Após o deploy manual do proprietário, o contrato publicado passou a 27/30. **A API não expõe hash de build**, então o commit no ar é comprovado indiretamente, pela contagem de campos e pela conformidade das fórmulas — a mesma limitação já registrada em `docs/PROJECT_STATUS.md` §17.9.

**[FATO] Não observável em produção nesta janela** (coberto pelos 38 testes locais, não pelo smoke): componente individual igual a zero com base válida (percentual `0,0`) e divergência exatamente `0,0`. Nenhuma das 5 marcas apresentou esses casos em jan–jul/2026.

---

## O que mudou na revisão 2

Quatro reclassificações, sendo duas que **fortalecem** conclusões e duas que **corrigem erros meus** da revisão 1:

| # | Revisão 1 dizia | Revisão 2 | Efeito |
|---|---|---|---|
| 1 | `vw_dre_mensal` é TikTok-only, provado por reconciliação (1,0111) | **CONFIRMADO POR LINHAGEM** — `pg_get_viewdef` obtida; as duas tabelas-fonte são `raw.tiktok_*` | Sobe de inferência forte para fato |
| 2 | De-para `bb_varejo_ltda`→barbours é "inferência não confirmada"; exigiria criar `dim_brand_erp_map` | **ERRO MEU. CONFIRMADO NA FONTE**: `silver.bling_produtos.marca_comercial` já faz o de-para | Remove um bloqueio inventado; dispensa tabela nova |
| 3 | "Publicar custos do DRE" era quick win de frontend | **Não é quick win** — 10 pré-requisitos; a view não é consultável em request | Corrige roadmap |
| 4 | `comissao_agencias` "não é afiliado, é gestão de mídia" | **ERRO MEU.** O campo de origem é `affiliate_partner_commission_amount` — é componente do programa de afiliados do TikTok | Mantém a separação, corrige o motivo |

Nenhum número ou evidência válida da revisão 1 foi removido.

---

## 1. Resumo executivo

### 1.1 Veredito por pedido — os três são coisas diferentes

| Pedido do stakeholder | Veredito | Por quê |
|---|---|---|
| **1.** "Colocar o custo e Retorno de afiliados na aba Canais" | **PARCIAL (custo) + BLOQUEADO (retorno)** | Custo existe e é confiável, mas **só TikTok, só mês × marca**. "Retorno" exige receita atribuída a afiliado, que **não existe em nenhum canal** |
| **2.** "Adicionar share de vendas por canal em Shopee e Mercado Livre" | **BLOQUEADO por fonte/semântica** | Nenhum dos dois canais tem partição Ads/live/produto/afiliado. `ad_revenue/GMV` não é composição de 100%. `orgânico = GMV − ad_revenue` é inválido |
| **3.** "Fazer margem por anúncio" | **PARCIAL em ML · PARCIAL-FRACO em Shopee · BLOQUEADO em TikTok** | Só ML tem fórmula fechada e confirmada — e ainda assim é contribuição **pré-CMV**, não margem |

### 1.2 Um defeito adicional, descoberto durante a auditoria — não confundir com o pedido 2

O pedido 2 era sobre **Shopee e Mercado Livre**. Ele está bloqueado.

Independentemente disso, a auditoria encontrou que o **TikTok** já exibe hoje, na aba Canais, vídeo/live/card como se decompusessem o GMV oficial — e **eles somam 106,85%**. **[FATO]**

Isso é um **defeito de verdade da interface já em produção**, não a entrega do pedido 2. Corrigi-lo:

- **não** entrega share de vendas para ML nem para Shopee;
- **não** cria nenhuma fonte nova;
- corrige um número errado que já está na tela hoje.

São duas frentes distintas e este documento não as mistura em nenhum ponto.

### 1.3 A descoberta de fonte

O Gate 1 da Canais (`docs/sections/canais_audit.md` §14.3) concluiu que não existia fonte de comissão de afiliado em canal algum. **Está superado.** Buscando no catálogo completo do Data Mart:

| Fonte | O que tem | Estado |
|---|---|---|
| `raw.tiktok_affiliate_marketplace_creators` | `commission_amount` por `(brand, creator_user_id, reference_date)` | **VAZIA — 0 linhas** **[FATO]** |
| `raw.vw_dre_mensal` | `affiliate_commission_amount_before_pit`, `affiliate_partner_commission_amount`, `affiliate_ads_commission_amount`, comissão de plataforma, desconto, devolução, subsídio, frete real | **POPULADA — 106 linhas**, 8 marcas × 15 meses **[FATO]** |

Existe **custo** de afiliado medido e agora com linhagem confirmada. Não existe **receita** atribuída a afiliado em nenhum grão. Por isso o custo é publicável (com trabalho de dados) e o "retorno" não é.

### 1.4 Quick wins reais

Sem fonte nova e sem decisão pendente, apenas correção de verdade nas superfícies que já existem:

1. Corrigir a apresentação do mix de conteúdo do TikTok (§9.2) — **a única de alto valor e baixo custo**.
2. Corrigir rótulos enganosos (ex.: não chamar atribuição de Ads de "share de vendas").
3. Declarar N/D vs N/A explicitamente onde hoje há ausência silenciosa.
4. Manter indisponível tudo que não tem fonte.

**Publicar os custos do DRE não está nesta lista** — ver §10.1.

---

## 2. Fatos confirmados

### 2.0 Linhagem de `raw.vw_dre_mensal` — CONFIRMADO POR LINHAGEM

**[FATO]** `pg_get_viewdef('raw.vw_dre_mensal')` foi obtida integralmente (3.248 caracteres). `relkind = v`, owner `postgres`. Nenhum `COMMENT` na relation nem em coluna alguma.

**As duas únicas relações-fonte, por `pg_depend`/`pg_rewrite`:**

```
FROM raw.tiktok_payments_by_order p
  LEFT JOIN raw.tiktok_shop_statements s
    ON p.statement_id::text = s.statement_id::text
   AND p.brand::text        = s.brand::text
GROUP BY p.brand, date_trunc('month', p.order_create_time)::date
```

Ambas são `raw.tiktok_*`. **Não há nenhuma tabela de Shopee, Mercado Livre ou de outro canal na definição.** A classificação "TikTok-only" deixa de ser inferência por reconciliação e passa a ser **fato de linhagem**.

**Mapeamento coluna → campo da API do TikTok** (extraído da definição; todos vêm de `jsonb` do registro de pagamento):

| Coluna do DRE | Campo de origem | Bloco |
|---|---|---|
| `gmv_bruto` | `subtotal_before_discount_amount` | `revenue_breakdown` |
| `desconto_vendedor` | `seller_discount_amount` | `revenue_breakdown` |
| `devolucoes` | `refund_subtotal_before_discount_amount` | `revenue_breakdown` |
| `estorno_desconto_devolucao` | `seller_discount_refund_amount` | `revenue_breakdown` |
| `receita_liquida` | soma dos quatro acima (com `COALESCE(...,0)`) | — |
| `frete_pago_cliente` | `customer_paid_shipping_fee_amount` | `shipping_cost_breakdown` |
| `frete_custo_real` | `actual_shipping_fee_amount` | `shipping_cost_breakdown` |
| `subsidio_plataforma` | `platform_discount_amount` | `supplementary_component` |
| `comissao_plataforma` | `platform_commission_amount` | `fee_breakdown` |
| `taxa_sfp` | `sfp_service_fee_amount` | `fee_breakdown` |
| `taxa_por_item` | `fee_per_item_sold_amount` | `fee_breakdown` |
| **`comissao_criadores`** | **`affiliate_commission_amount_before_pit`** | `fee_breakdown` |
| **`comissao_agencias`** | **`affiliate_partner_commission_amount`** | `fee_breakdown` |
| **`comissao_ads_afiliados`** | **`affiliate_ads_commission_amount`** | `fee_breakdown` |
| `gmv_max_ads` | `gmv_max_ad_fee_amount` | `fee_breakdown` |
| `total_marketing` | soma dos **quatro** anteriores | — |
| `ajustes` | `p.adjustment_amount` | coluna escalar |
| `resultado_operacional` | **`sum(p.settlement_amount)`** | coluna escalar |
| `depositado_caixa` | `s.payable_amount` | statements |
| `retido_onhold` | `s.total_reserve_amount` | statements |
| `pedidos` | `count(DISTINCT p.order_id)` | — |

Cinco consequências diretas dessa definição:

1. **[FATO]** `receita_liquida = gmv_bruto − desconto_vendedor − devolucoes + estorno`, exatamente como a §4.1 havia deduzido por álgebra. Agora está confirmado pela definição.
2. **[FATO]** `resultado_operacional` **não é um P&L derivado** — é `sum(settlement_amount)`, um campo independente. Isso **explica** por que ele não fechava com a soma das outras colunas (limitação nº 1 da revisão 1, agora resolvida). Não é defeito; é outra grandeza.
3. **[FATO]** A competência é `order_create_time` — **mês de criação do pedido**, não de repasse. Isso é bom: alinha com a competência que a Torre usa. E como `order_create_time` está na linha de pagamento, múltiplas linhas do mesmo pedido caem no mesmo mês (sem split de competência).
4. **[FATO]** Os três custos de afiliado são, na taxonomia da própria fonte, **três componentes `affiliate_*` do mesmo `fee_breakdown`**. Ver §8 — isto corrige a revisão 1.
5. **[FATO]** A fonte **já define** um agregado (`total_marketing`) que soma os três componentes de afiliado **mais** `gmv_max_ads`. Relevante para a decisão D2.

**Verificações de integridade da view — todas passaram:**

| Teste | Resultado |
|---|---|
| `(statement_id, brand)` é único em `raw.tiktok_shop_statements`? | **Sim** — 3.358 linhas = 3.358 chaves, 0 duplicadas **[FATO]** |
| O `LEFT JOIN` multiplica linhas de pagamento? | **Não** — 2.111.263 antes e 2.111.263 depois **[FATO]** |
| Logo, os `sum(p.*)` estão inflados por fan-out? | **Não.** Risco levantado e **refutado** **[FATO]** |

**[FATO]** `raw.tiktok_payments_by_order` tem 2.111.263 linhas. **Atenção a uma confusão de nomes**: `raw.tiktok_shop_statements` (3.358 linhas, usada pela view) é uma tabela **diferente** de `raw.tiktok_shop_settlements` (0 linhas, vazia, citada no `financeiro_audit.md`).

**[FATO]** Grão: em amostra (rituaria, jun/2026) há 5.494 linhas para 5.459 pedidos = **1,006 linha por pedido**; `transaction_id` é o candidato natural a chave. Na mesma amostra `transaction_type` tem um único valor (`ORDER`).

**[INFERÊNCIA]** A view **não filtra `transaction_type`**. Na amostra isso é inócuo (só existe `ORDER`), mas não verifiquei o domínio de `transaction_type` em todo o histórico e em todas as marcas. Se existirem tipos como ajuste ou reembolso em outras janelas, eles entram nas somas sem rótulo. **Falta para virar fato:** `SELECT DISTINCT transaction_type` no histórico completo (a réplica deu timeout nas varreduras amplas desta tabela).

### 2.1 Afiliados

| # | Fato | Evidência |
|---|---|---|
| F1 | `raw.tiktok_affiliate_marketplace_creators` tem 21 colunas incluindo `commission_amount`, `creator_user_id`, `reference_date`, `total_gmv`, `gmv_video`, `gmv_live` | `information_schema.columns` |
| F2 | Essa tabela está **vazia**: 0 linhas. A cópia em `api.` também: 0 linhas | `SELECT count(*)` nas duas |
| F3 | `raw.tiktok_shop_settlements` também está vazia: 0 linhas | `SELECT count(*)` |
| F4 | `raw.vw_dre_mensal` tem 106 linhas, grão `(brand, mes)` **único** (0 duplicadas), 8 marcas, 15 meses (2025-06 → 2026-08) | agregação + unicidade |
| F5 | **Revisado:** `vw_dre_mensal` é TikTok-only **confirmado por linhagem** (§2.0). A reconciliação `receita_liquida / gold.tiktok_brand_daily.gmv` = **1,0111** (jan–jun/26, 5 marcas) passa a ser *corroboração*, não a prova principal | `pg_get_viewdef` + §4.1 |
| F6 | Custos de atribuição, jan–jun/2026, 5 marcas: `comissao_criadores` R$ 4.356.824 (6,12% da receita líquida); `comissao_ads_afiliados` R$ 535.443 (0,75%); `comissao_agencias` R$ 2.163.873 (3,04%); `comissao_plataforma` R$ 4.268.887 (6,00%) | agregação |
| F7 | O DRE **não tem** coluna de canal, criador, produto, campanha ou dia. Só `brand` e `mes` | schema |
| F8 | Existe grão de criador rico e populado — `gold.tiktok_creator_daily` (203.544), `tiktok_creator_product` (38.189), `tiktok_product_creator_daily` (7.349.712), `tiktok_creator_video_daily` (5.104.645) — e **nenhuma tem coluna de comissão** | schemas + counts |
| F9 | Nenhuma dessas tabelas distingue **afiliado** de **conteúdo próprio da loja**. Sem flag, tipo ou tabela de vínculo | schemas |
| F10 | **Shopee não tem fonte de afiliado.** 74 colunas canônicas do export de pedidos e 67 de `silver.stg_shopee_order_item_snapshots`: sem afiliado, live, vídeo ou fonte-do-pedido | contrato versionado + schema |
| F11 | **Mercado Livre não tem fonte de afiliado.** 74 tabelas `ml_*`/`shopee*` no Data Mart, zero com afiliado | catálogo |
| F12 | `commission_fee` existe em `raw.tiktok_shop_settlements` — é comissão **de marketplace**, não de afiliado, e a tabela está vazia | schema + count |

### 2.2 Shares de venda

| # | Fato | Evidência |
|---|---|---|
| F13 | `gmv_video + gmv_live + gmv_card` (mart, TikTok, jan–jun/26) = **R$ 70.414.835,49**, idêntico ao `gold.tiktok_brand_daily.gmv` | §4.2 |
| F14 | O GMV canônico do mart no mesmo escopo é **R$ 65.898.900,23**. A soma excede em **+6,85%** (R$ 4.515.935) | §4.2 |
| F15 | As três categorias **particionam exatamente** o GMV de linhagem de conteúdo — partição válida, de **outro** numerador | §4.2 |
| F16 | Cobertura integral: 905 linhas dia×marca, todas com `gmv > 0`, **zero** com quebra nula e **zero** com quebra = 0 | agregação |
| F17 | A aba Canais exibe `tiktok_video_pct`/`live_pct`/`card_pct` calculados sobre o GMV canônico (`_pct(tk_vid, tk_gmv)`), em cards, colunas ordenáveis e uma `AttributionBar` | `performance_service.py`, `canais/page.tsx:451-464,706-750` |
| F18 | A página **não tem ressalva** sobre a soma não fechar em 100% | `grep` → 0 ocorrências |
| F19 | `raw.tiktok_shop_orders` (28 col) e `raw.tiktok_shop_line_items` (26 col) **não têm campo de fonte de conteúdo**. A quebra só existe na família *analytics* | schemas |
| F20 | `ad_revenue` **não é subconjunto do GMV**: Shopee tem **318 de 905** linhas dia×marca com `ad_revenue > gmv`; total 72,07% do GMV. ML: 44,73%, 2 linhas > GMV | agregação |
| F21 | TikTok tem `ad_spend = 0` e `ad_revenue = 0` no mart em todo o período — N/A por modelo, não ausência | agregação |
| F22 | `ad_spend`/`ad_revenue` da Shopee **não têm granularidade diária real**: o parser soma o período do CSV e divide pelos dias | `_parser_ads.py` |

### 2.3 Margem e CMV

| # | Fato | Evidência |
|---|---|---|
| F23 | `gold.ml_produto_pnl.estimated_margin` = `gross_revenue − marketplace_fee − ad_spend` **exatamente**: 1.767/1.767 linhas, desvio máximo **0,0000** | teste algébrico |
| F24 | Equivale a **80,38%** da receita bruta (`fee` 16,07%, `ads` 3,55%) — contribuição antes de CMV, frete, desconto, devolução e afiliado | agregação |
| F25 | `(brand, item_id)` **não é chave única** em `ml_produto_pnl`: 117 pares duplicados | unicidade |
| F26 | `ml_produto_pnl` **não tem coluna de data**: lifetime cumulativo | schema |
| F27 | `silver.bling_produtos` tem `preco_custo` para 2.319 produtos em 5 marcas | agregação |
| F28 | **Revisado (join ciente de marca):** cobre **98,6% da receita de ML** (602/609 SKUs) e **97,3% da Shopee** (670/696) | §4.3 |
| F29 | **Não tem vigência temporal** — só `produtos_loaded_at`/`estoque_loaded_at`. Snapshot do cadastro atual | schema |
| F30 | **Revisado:** com a chave `(marca_comercial, código)` o universo tem 1.841 pares em 1.888 linhas (**47** colapsos, não 192). Os 128 códigos que aparecem em >1 marca deixam de ser fan-out: são produtos distintos com chave distinta | §4.3 |
| F31 | Não há coluna de moeda em nenhum candidato de custo | catálogo |
| F32 | **CORRIGIDO — era erro da revisão 1.** O de-para existe **na própria fonte**: `silver.bling_produtos.marca_comercial` mapeia `bb_varejo_ltda → barbours` e `kokeshi_mkt_place → kokeshi`. Não é inferência | §4.4 |
| F33 | `lescent` na Shopee tem só **33,5%** da receita com custo — permanece o pior caso | §4.3 |
| F34 | **TikTok não tem chave de SKU do ERP no grão de produto**: `gold.tiktok_product_daily` expõe `product_id` (id do TikTok) | schema |
| F35 | `gold.vendas_consolidada_produto` (11.460 linhas) tem `custo_unitario_produto` — mas só marca `apice`, canais `atacado`/`ecommerce`. **Não cobre marketplace** | agregação |
| F36 | `gold.nf_vendas_unificada` (3.817.269 linhas) tem `custo_unitario`/`custo_origem` — também só `apice`, canais `atacado`/`ecommerce` | agregação |
| F37 | `raw.tiny_produtos_custo` (4.672 linhas) cobre só contas `ap_cosmetics_*`/`apice_atacado_*` | agregação |
| F38 | Grão de **campanha** sólido para ML: `gold.ml_campaign_diaria`, 47.801 linhas, 473 campanhas, `(ref_date, brand, campaign_id)` único, `ad_revenue = direct + indirect` exato (−0,00) | agregação |
| F39 | Grão de **anúncio** para Shopee: `silver.stg_shopee_ads`, 3.025 linhas, 45 colunas (`ad_name`, `ad_type`, `product_id`, `placement`, `expense`, `gmv`, `direct_revenue`) — em grão de **período**, não diário | schema + count |
| F40 | Shopee tem taxa e desconto no grão item-de-pedido: `silver.stg_shopee_order_item_snapshots`, 763.600 linhas, 5 marcas, 2026-01-01 → 2026-08-17, 702 SKUs, campos 100% não-nulos | agregação |
| F41 | Persiste a ambiguidade de `seller_discount_2` da Shopee | `docs/staging_shopee_contract.md` §12 |
| F42 | **Novo.** `raw.tiktok_payments_by_order` tem `sku_transactions jsonb` e `sku_count` — mas em amostra de 200 linhas com array não-vazio o resultado foi **zero chaves**: o array está vazio. **Não é caminho para custo/taxa por SKU do TikTok hoje** | amostragem de chaves JSON |

---

## 3. Fontes e grãos

### 3.1 Inventário por métrica

Confiabilidade: **Alta** = reconciliado contra referência externa · **Média** = internamente consistente · **Baixa** = semântica ou competência não confirmada · **N/D** = sem dado.

| Campo/métrica | Marketplace | Fonte | Tabela/endpoint | Grão | Chave | Data/competência | Cobertura | Confiab. | Observação |
|---|---|---|---|---|---|---|---|---|---|
| GMV canônico | TK | Raw→mart | `marts.fact_marketplace_daily_performance` | dia×loja | `(date, loja_id, marketplace_id)` | `created_at` do pedido | 2025-12 → hoje | Alta | `SUM(sub_total)`, allowlist de status |
| GMV canônico | ML, SH | idem | idem | dia×loja | idem | idem | ML 2025-12→ · SH 2026-01→ | Alta | Reconciliado com XLSX |
| GMV linhagem conteúdo | TK | Gold | `gold.tiktok_brand_daily.gmv` | dia×marca | `(date, brand)` | idem | 2025-06 → hoje | Média | ≈ `total_amount`; **+6,85%** acima do canônico |
| Receita atribuída a Ads | ML | Gold→mart | `.ad_revenue` | dia×loja | idem | janela do ML | 2026-01→ | Média | 44,73% do GMV — **não é partição** |
| Receita atribuída a Ads | SH | export CSV | `.ad_revenue` | dia×loja (derivado) | idem | período rateado | 2026-01→ | Baixa | 72,07% do GMV; 318/905 linhas > GMV |
| Receita atribuída a Ads | TK | — | — | — | — | — | — | N/A | Canal não opera ads geridos |
| Gasto em Ads | ML | Gold→mart | `.ad_spend` | dia×loja | idem | dia | 2026-01→ | Alta | |
| Gasto em Ads | SH | export CSV | `.ad_spend` | dia×loja (derivado) | idem | período rateado | 2026-01→ | Baixa | Ver F22 |
| Gasto em Ads (campanha) | ML | Gold | `gold.ml_campaign_diaria.spend` | dia×campanha | `(ref_date, brand, campaign_id)` | dia | 2026-01-27→ | Alta | 473 campanhas, chave única |
| Gasto em Ads (anúncio) | SH | Silver | `silver.stg_shopee_ads.expense` | anúncio×período | `(file_id, ad_seq)` | período | 2026 | Média | Sem grão diário |
| GMV vídeo/live/card | TK | analytics | `.gmv_video/.gmv_live/.gmv_card` | dia×marca / dia×SKU | idem | dia | 2025-06→ | Média | Linhagem analytics; **não decompõe o canônico** |
| GMV live/vídeo/card | ML, SH | — | — | — | — | — | — | N/A | Conceito não existe no canal |
| GMV por criador | TK | Gold | `gold.tiktok_creator_daily` | dia×marca×criador | `(date, brand, creator)` | dia | 308 datas | Média | Linhagem conteúdo; **sem comissão** |
| Comissão de afiliado (criador) | TK | Raw | `tiktok_affiliate_marketplace_creators.commission_amount` | dia×marca×criador | `(brand, creator_user_id, reference_date)` | `reference_date` | **nenhuma** | N/D | **Tabela vazia** |
| Comissão de afiliado — criadores | TK | View | `vw_dre_mensal.comissao_criadores` ← `affiliate_commission_amount_before_pit` | **mês×marca** | `(brand, mes)` | `order_create_time` | 2025-06→2026-08 | Média | Linhagem confirmada (§2.0) |
| Comissão de afiliado — ads | TK | View | `vw_dre_mensal.comissao_ads_afiliados` ← `affiliate_ads_commission_amount` | **mês×marca** | idem | idem | idem | Média | 0,75% da receita líquida |
| Comissão de afiliado — parceiro/agência | TK | View | `vw_dre_mensal.comissao_agencias` ← `affiliate_partner_commission_amount` | **mês×marca** | idem | idem | idem | Média | É `affiliate_*` na fonte — ver §8 |
| Comissão de afiliado | ML, SH | — | — | — | — | — | — | N/D | Sem fonte (F10, F11) |
| Taxas de marketplace | SH | export | `.total_fees` | dia×loja | idem | dia | 2026-01→ | Alta | Validado ao centavo |
| Taxas de marketplace | SH (detalhe) | Silver | `commission_fee_net`, `service_fee_net`, `transaction_fee` | item×pedido | `(order_id, sku_ref, …)` | `order_created_at` | 763.600 linhas | Alta | 100% não-nulo |
| Taxas de marketplace | TK | Gold | `.total_fees` | dia×marca | idem | competência de repasse | 2025-12→ | Baixa | Base ~5,5% ≠ GMV comercial |
| Taxas de marketplace | TK (alt.) | View | `comissao_plataforma` + `taxa_sfp` + `taxa_por_item` | mês×marca | idem | `order_create_time` | idem | Média | **Divergem de `total_fees` — §7.4** |
| Taxas de marketplace | ML | mart | `.total_fees` | — | — | — | — | N/D | Sempre `NULL` |
| Taxas de marketplace | ML (alt.) | Gold | `gold.ml_produto_pnl.marketplace_fee` | produto (lifetime) | `(brand, item_id)` não-único | **sem data** | 1.767 linhas | Baixa | Cumulativo; 16,07% da receita |
| Frete seller | ML, SH | mart | `.seller_shipping_cost` | dia×loja | idem | dia | 2026-01→ | Média | |
| Frete custo real | TK | View | `vw_dre_mensal.frete_custo_real` ← `actual_shipping_fee_amount` | mês×marca | idem | idem | 2025-06→ | Média | R$ −26,2M em 15 meses |
| Custo/CMV do produto | ML, SH | Silver | `silver.bling_produtos.preco_custo` | produto (**snapshot**) | `(marca_comercial, produto_codigo)` | **sem vigência** | 98,6%/97,3% da receita | Baixa | Cobertura alta ≠ validade histórica (§9) |
| Custo/CMV do produto | TK | — | — | — | — | — | — | N/D | Sem chave de SKU do ERP (F34); `sku_transactions` vazio (F42) |
| Cancelamentos | SH, ML | mart | `.canceled_orders` | dia×loja | idem | dia | 2026-01→ | Alta | SH 13,8% · ML 4,3% (mai/26) |
| Cancelamentos | TK | mart | `.canceled_orders` | — | — | — | **0 em todos os meses** | N/D | Dado existe na Raw (391.676 pedidos) |
| Devoluções/reembolsos | SH | mart / silver | `.returned_orders` / `returned_quantity` | dia×loja / item | idem | dia | 2026-01→ | Média | |
| Devoluções | TK | View | `vw_dre_mensal.devolucoes` | mês×marca | idem | idem | 2025-06→ | Média | R$ −3,73M jan–jun/26 |
| Descontos | TK | View | `vw_dre_mensal.desconto_vendedor` | mês×marca | idem | idem | idem | Média | R$ −38,87M = **34,55%** do GMV bruto |
| Descontos | SH | Silver | `seller_discount`, `seller_discount_2`, vouchers, `shopee_coins_offset` | item×pedido | idem | dia | 763.600 linhas | Baixa | `seller_discount_2` ambíguo (F41) |
| Descontos | ML | — | — | — | — | — | — | N/D | Sem fonte identificada |
| Subsídio de plataforma | TK | View | `subsidio_plataforma` ← `platform_discount_amount` | mês×marca | idem | idem | 2025-06→ | Média | R$ +11,98M em 15 meses |
| De-para marca ERP→Torre | ML, SH | Silver | `silver.bling_produtos.marca_comercial` | produto | — | snapshot | 5 marcas | Alta | **Confirmado na fonte** (§4.4) |

### 3.2 Payloads da API consumidos pelo frontend

**[FATO]** `get_canais()` já lê `gmv, gmv_video, gmv_live, gmv_card, visitors, unique_buyers, new_buyers, repeat_buyers, canceled_orders, orders, conversion_rate, ad_spend, ad_revenue, total_fees, seller_shipping_cost` mais `ad_spend_n`, `total_fees_n`, `seller_shipping_cost_n`. O Gate 2 recomendado pelo Gate 1 **já foi implementado**.

**[FATO]** Não existe em nenhum payload qualquer campo de afiliado, desconto ou CMV. Há teste de regressão que trava a reintrodução — `test_canais_channel_rows.py::test_nenhum_campo_de_desconto_ou_afiliado_no_payload` e `canais-channel-metrics.test.ts`. Qualquer implementação futura de afiliado precisa atualizar esses testes **deliberadamente**.

---

## 4. Reconciliações realizadas

### 4.1 `receita_liquida` × GMV de TikTok — corroboração (a prova agora é a linhagem)

Jan–jun/2026, 5 marcas da Torre:

| Comparação | Valor | Razão vs DRE |
|---|---:|---:|
| `vw_dre_mensal.gmv_bruto` | 112.496.247,57 | — |
| `vw_dre_mensal.receita_liquida` | 71.194.273,64 | — |
| `gold.tiktok_brand_daily.gmv` | 70.414.835,49 | `receita_liquida` / gold TK = **1,0111** |
| mart TikTok (canônico) | 65.898.900,23 | 1,0804 |
| mart ML | 24.797.576,66 | — |
| mart Shopee | 26.435.387,91 | — |
| mart TK+ML+SH | 117.131.864,80 | 0,6078 |

**[FATO]** O total de `gmv_bruto` (112,5M) fica a −3,96% da soma dos 3 canais (117,1M) — o que **parecia** indicar cobertura multicanal. Testado por marca, essa leitura cai: apice +20%, barbours +20%, kokeshi −29%, lescent −58%, rituaria −83%. O acerto no total era compensação de erros opostos. A linhagem (§2.0) fecha a questão.

**[FATO]** `gmv_bruto = subtotal_before_discount_amount` — é bruto **antes do desconto do vendedor** (34,55% do bruto), e `receita_liquida` é o conceito que corresponde ao GMV que a Torre reporta.

⚠️ **[FATO] Ressalva de governança que permanece:** a transformação da view **não está versionada em nenhum repositório nosso**. Obtive a definição por `pg_get_viewdef` num banco de leitura, num instante — ela pode mudar sem aviso e sem revisão. É a mesma condição estrutural que gerou a divergência gold×marts. Consumi-la exige reconciliação contínua, não uma leitura única.

### 4.2 A quebra de canal do TikTok não decompõe o GMV canônico

Mart, `marketplace_id = 1`, jan–jun/2026:

| Mês | GMV canônico | vídeo | live | card | soma | soma/GMV | resíduo |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-01 | 7.421.563,24 | 4.526.634,61 | 1.636.863,26 | 1.597.403,08 | 7.760.900,95 | 1,0457 | −339.337,71 |
| 2026-02 | 11.299.111,47 | 6.556.102,38 | 2.922.026,25 | 2.639.760,16 | 12.117.888,79 | 1,0725 | −818.777,32 |
| 2026-03 | 13.844.608,16 | 7.369.502,05 | 4.067.653,15 | 3.507.928,44 | 14.945.083,64 | 1,0795 | −1.100.475,48 |
| 2026-04 | 11.774.142,27 | 6.467.297,65 | 2.874.137,66 | 3.194.867,49 | 12.536.302,80 | 1,0647 | −762.160,53 |
| 2026-05 | 12.498.700,47 | 6.785.129,27 | 2.893.824,30 | 3.717.032,29 | 13.395.985,86 | 1,0718 | −897.285,39 |
| 2026-06 | 9.060.774,62 | 4.597.213,51 | 1.989.232,61 | 3.072.227,33 | 9.658.673,45 | 1,0660 | −597.898,83 |
| **TOTAL** | **65.898.900,23** | **36.301.879,47** | **16.383.737,23** | **17.729.218,79** | **70.414.835,49** | **1,0685** | **−4.515.935,26** |

**[FATO]** Soma das categorias = R$ 70.414.835,49 = exatamente `gold.tiktok_brand_daily.gmv`. As três categorias **são** uma partição — do GMV de conteúdo, não do canônico.
**[FATO]** Sobre o GMV canônico: vídeo 55,09% + live 24,86% + card 26,90% = **106,85%**.
**[FATO]** Sobre a própria soma: 51,55% + 23,27% + 25,18% = 100,00%.
**[FATO]** As categorias **não se sobrepõem** entre si. O problema é exclusivamente de denominador.
**[FATO]** Cobertura integral (F16). O resíduo não é falta de dado, é diferença de definição de GMV.

### 4.3 Cobertura de custo — recalculada com join ciente de marca

**Chave usada:** `(lower(marca_comercial), upper(btrim(produto_codigo)))`.
**[FATO]** Universo: 1.841 pares distintos em 1.888 linhas → **47 colapsos** (contra 192 no join só por código).

| Canal | SKUs vendidos | Com custo > 0 | % SKUs | Receita | Receita coberta | % receita |
|---|---:|---:|---:|---:|---:|---:|
| ML — barbours | 264 | 264 | **100,0%** | 15.497.069,74 | 15.497.069,74 | **100,0%** |
| ML — kokeshi | 181 | 178 | 98,3% | 7.238.635,24 | 7.176.991,17 | 99,1% |
| ML — lescent | 73 | 69 | 94,5% | 4.924.444,60 | 4.428.282,15 | 89,9% |
| ML — rituaria | 91 | 91 | **100,0%** | 11.604.930,99 | 11.604.930,99 | **100,0%** |
| **ML — TOTAL** | **609** | **602** | **98,9%** | **39.265.080,57** | **38.707.274,05** | **98,6%** |
| SH — apice | 178 | 178 | 100,0% | 2.194.843,88 | 2.194.843,88 | 100,0% |
| SH — barbours | 213 | 213 | **100,0%** | 10.719.477,47 | 10.719.477,47 | **100,0%** |
| SH — kokeshi | 151 | 148 | 98,0% | 17.074.968,26 | 16.889.345,52 | 98,9% |
| SH — lescent | 88 | 65 | 73,9% | 1.058.688,09 | 354.955,28 | **33,5%** |
| SH — rituaria | 66 | 66 | 100,0% | 2.161.009,58 | 2.161.009,58 | 100,0% |
| **SH — TOTAL** | **696** | **670** | **96,3%** | **33.208.987,28** | **32.319.631,73** | **97,3%** |

**Sensibilidade a barbours** (a marca cuja chave de ERP era a dúvida da revisão 1):

| Escopo | ML — % receita coberta | Shopee — % receita coberta |
|---|---:|---:|
| Com barbours | 98,6% | 97,3% |
| **Sem barbours** | **97,7%** | **96,0%** |

**[FATO]** A conclusão de cobertura **não depende de barbours**: sem a marca, a cobertura continua acima de 96% nos dois canais. E com o de-para confirmado (§4.4), barbours passa a 100,0% nos dois — melhor, não pior.

**[FATO]** Os 128 códigos que aparecem em mais de uma `marca_comercial` **deixam de ser risco de fan-out** sob a chave `(marca, código)`: são produtos distintos de marcas distintas, com chaves distintas. O risco de fan-out era artefato do join só por código, que a revisão 1 usou.

### 4.4 De-para de marca ERP→Torre — CONFIRMADO NA FONTE

**[FATO]** `silver.bling_produtos` tem duas colunas de marca, e a segunda **é** o de-para:

| `marca` (chave de ERP) | `marca_comercial` | produtos |
|---|---|---:|
| `apice` | `apice` | 232 |
| `bb_varejo_ltda` | **`barbours`** | 300 |
| `kokeshi_mkt_place` | **`kokeshi`** | 383 |
| `lescent` | `lescent` | 140 |
| `rituaria` | `rituaria` | 1.264 |

A correspondência é 1:1, exaustiva sobre as 5 marcas, e vem da própria fonte. **A revisão 1 estava errada** ao classificar isso como inferência e ao propor uma tabela `dim_brand_erp_map` nova: a coluna já existe e resolve. Também cai a limitação de que "a maior marca depende de uma inferência".

⚠️ **[FATO] Armadilha adjacente.** Existe `gold.dim_marca_conta` (colunas `marca_real`, `conta_rede`, `conta_mp`, `obs`), que **parece** um de-para de marca mas **não é**: mapeia marca → **conta adquirente de pagamento**. Nela `kokeshi → conta_rede 'Lescent'` e `rituaria → conta_rede 'Barbours'`. Usá-la como identidade de marca produziria atribuição errada. **Não usar para custo nem para canal.**

**[FATO]** Fora de `silver.bling_produtos`, não há nenhum artefato versionado no repositório que documente esse de-para: `grep` por `bb_varejo|varejo_ltda|kokeshi_mkt_place` em `*.py/*.sql/*.md/*.ts/*.tsx/*.json` retorna **apenas este documento**. `BRANDS_IN_SCOPE = ("apice","barbours","kokeshi","lescent","rituaria")` (em `gold_service.py` e no conector TikTok) é a allowlist canônica e não contém aliases de ERP. **[RECOMENDAÇÃO]** documentar o de-para no contrato de dados quando o CMV for modelado, citando `marca_comercial` como origem — não recriá-lo.

### 4.5 `estimated_margin` do ML — fórmula confirmada

**[FATO]** `estimated_margin = gross_revenue − marketplace_fee − ad_spend`: **1.767 de 1.767 linhas**, desvio máximo **0,0000**.

| Componente | Valor | % da receita bruta |
|---|---:|---:|
| `gross_revenue` | 39.259.063,69 | 100,00% |
| `marketplace_fee` | 6.310.023,77 | 16,07% |
| `ad_spend` | 1.392.267,95 | 3,55% |
| `estimated_margin` | 31.556.771,97 | **80,38%** |

Uma "margem" de 80% da receita bruta é a prova aritmética de que **não é margem**.

### 4.6 Ads como share — reconciliação de denominador

| Canal | GMV | `ad_revenue` | % do GMV | Linhas com `ad_revenue > gmv` |
|---|---:|---:|---:|---:|
| Mercado Livre | 24.797.576,66 | 11.092.001,22 | 44,73% | 2 de 723 |
| Shopee | 26.435.387,91 | 19.051.174,00 | 72,07% | **318 de 905** |
| TikTok | 65.898.900,23 | 0,00 | — | N/A |

**[FATO]** 35% das linhas dia×marca da Shopee têm receita atribuída a Ads **maior que o GMV do dia** — assinatura de janela de atribuição própria somada ao rateio artificial do período. `ad_revenue / GMV` **não é share de vendas**.

**[FATO]** Em `gold.ml_campaign_diaria` (jan–jun/26): `spend` 935.071,35 vs `ad_spend` do mart 947.211,62 (−1,3%); e `direct_revenue + indirect_revenue − ad_revenue = −0,00`. A decomposição direta/indireta **é** exata no grão de campanha.

---

## 5. Matriz de disponibilidade

Classificação: **DISPONÍVEL** · **DERIVÁVEL COM PROVA** · **PARCIAL/PROXY** · **GRÃO INCOMPATÍVEL** · **FONTE AUSENTE** · **NÃO APLICÁVEL** · **DECISÃO DE PRODUTO**

| Solicitação | TikTok | ML | Shopee | Disponível hoje? | Gap | Menor próximo passo |
|---|---|---|---|---|---|---|
| Custo de afiliado | PARCIAL/PROXY — DRE, mês×marca, **linhagem confirmada** | FONTE AUSENTE | FONTE AUSENTE | Não como entrega de tela; o dado existe | Materialização (10 pré-requisitos, §10.1) | UE2 — fato mensal no Neon |
| Receita de afiliado | FONTE AUSENTE — `creator_daily` tem GMV mas é linhagem conteúdo e não separa afiliado de loja | FONTE AUSENTE | FONTE AUSENTE | Não | Sem flag afiliado×próprio (F9); tabela de criador vazia (F2) | UE1 — perguntar ao time da Raw |
| ROAS/ROI de afiliado | **BLOQUEADO** — denominador existe, numerador não | NÃO APLICÁVEL | FONTE AUSENTE | Não | Receita atribuída | Bloquear até UE1 |
| Share Ads | NÃO APLICÁVEL (F21) | PARCIAL/PROXY — atribuição, não partição | PARCIAL/PROXY — pior (F20, F22) | **Não como share** | Não é partição em canal algum | Renomear para "Receita atribuída a Ads ÷ GMV"; nunca somar |
| Share live | DERIVÁVEL COM PROVA — do GMV de **conteúdo** | NÃO APLICÁVEL | NÃO APLICÁVEL | Sim, com o contrato da §9.2 | Denominador errado hoje | **UE-F1** |
| Share product card/produto | idem live | NÃO APLICÁVEL | NÃO APLICÁVEL | idem | idem | idem |
| Share afiliado | FONTE AUSENTE | FONTE AUSENTE | FONTE AUSENTE | Não | Sem receita de afiliado | Bloquear |
| Share orgânico/outros | FONTE AUSENTE | FONTE AUSENTE | FONTE AUSENTE | **Não — proibido derivar** | Categorias não exclusivas nem exaustivas | Bloquear |
| **Pedido 2 do stakeholder (share por origem em ML e Shopee)** | — | **BLOQUEADO** | **BLOQUEADO** | **Não** | Fonte + semântica | UE1 |
| Taxa marketplace | PARCIAL/PROXY — duas fontes divergentes (§7.4) | GRÃO INCOMPATÍVEL — lifetime sem data | DISPONÍVEL — validado, detalhe por item | SH sim; TK proxy; ML não | Competência ML; conciliar TK | Reconciliar `total_fees` × DRE |
| CMV por SKU | FONTE AUSENTE — sem chave (F34, F42) | PARCIAL/PROXY — 98,6% da receita, snapshot sem vigência | PARCIAL/PROXY — 97,3%, um caso a 33,5% | Não para uso histórico | Vigência temporal | `dim_product_cost_history` |
| Margem por listing | **BLOQUEADO** | **PARCIAL** — contribuição pré-CMV confirmada e exata | **PARCIAL-FRACO** — ver §8.2 | Só como contribuição | CMV com vigência; Ads por listing na SH | UE4, começando por ML |
| Margem por campanha/anúncio pago | NÃO APLICÁVEL | DERIVÁVEL COM PROVA no grão campanha×dia (F38) | PARCIAL/PROXY — anúncio×período (F39) | Não como margem | Taxa e CMV não existem nesse grão | Bloqueado — exige rateio (§7.7) |

---

## 6. Definições candidatas

### 6.1 "Retorno de afiliados"

| Definição | Fórmula | Numerador disponível? |
|---|---|---|
| ROAS de afiliado | receita atribuída ÷ comissão paga | **Não** |
| ROI de afiliado | (receita atribuída − comissão) ÷ comissão | **Não** |

**[FATO]** `ROI = ROAS − 1`. São grandezas **diferentes** com a mesma informação; não são intercambiáveis em rótulo.
**[RECOMENDAÇÃO]** Manter os dois como conceitos distintos e **não publicar nenhum dos dois** até existir receita atribuída a afiliado. Quando existir, usar **ROAS de afiliado**, por consistência com o ROAS de Ads já exibido para ML/Shopee.

**[FATO]** O que é calculável hoje é apenas o custo como intensidade (jan–jun/26, 5 marcas):

- Comissão de criadores ÷ Receita líquida = **6,12%**
- Comissão de afiliados/Ads ÷ Receita líquida = **0,75%**
- Comissão de parceiro/agência ÷ Receita líquida = **3,04%**

Isso responde "quanto custa". Não responde "quanto retorna", e não deve ser rotulado como se respondesse.

### 6.2 Margem por anúncio — componentes

Fórmula pedida: `Receita − Custo de Produto − Ads − Afiliado − Taxas Marketplace`.

| Componente | TikTok | ML | Shopee |
|---|---|---|---|
| Receita | ✅ (3 definições concorrentes — D1) | ✅ | ✅ |
| − Custo de Produto | ❌ sem chave | ⚠️ snapshot sem vigência | ⚠️ idem |
| − Ads | ❌ N/A | ✅ campanha×dia | ⚠️ anúncio×período, não alocado por listing |
| − Afiliado | ⚠️ só mês×marca | ❌ | ❌ |
| − Taxas Marketplace | ⚠️ duas fontes divergentes | ⚠️ lifetime sem data | ✅ item×pedido |

**[FATO] Componentes materialmente ausentes da fórmula do stakeholder** (registrados, não alterados):

- **descontos** — 34,55% do GMV bruto no TikTok. O maior componente único da auditoria.
- **frete seller** — R$ 26,2M de custo real no TikTok em 15 meses.
- **devoluções/reembolsos** — R$ 3,73M no TikTok jan–jun/26; 13,8% de cancelamento na Shopee.
- **subsídio de plataforma** — R$ 11,98M **a favor** no TikTok. Omitir subestima a margem.
- **impostos** — sem fonte no escopo da Torre.

**[RECOMENDAÇÃO] Nome honesto conforme o que entra na conta:**

| Se a conta tem | Nome |
|---|---|
| Receita − taxa − ads | **contribuição pré-CMV** |
| Receita − taxa − desconto (sem Ads nem afiliado) | **contribuição comercial parcial antes de CMV, Ads e afiliados** |
| + CMV com vigência correta | **margem de contribuição** |
| + frete, desconto, devolução, subsídio | **margem de contribuição completa** |
| + impostos e rateio de fixos | **margem real** — hoje **indisponível** nos 3 canais |

**Nunca** chamar `estimated_margin` de margem, lucro ou resultado, e nunca exibi-lo ao gestor (o front já tem `marginUnavailableNote` e teste de regressão — manter).

### 6.3 "Anúncio" — as duas interpretações

| | A — listing/oferta | B — campanha/creative pago |
|---|---|---|
| Grão necessário | produto/SKU × período | campanha (ou anúncio) × período |
| Existe TK | `gold.tiktok_product_daily` (`product_id`, sem SKU do ERP) | não existe |
| Existe ML | `gold.ml_produto_pnl` (lifetime, chave não-única) · `marts.fact_ml_produto_ranking` | `gold.ml_campaign_diaria` — dia×campanha, chave única, 473 campanhas |
| Existe SH | `fact_shopee_product_monthly` (mês×SKU) · `stg_shopee_order_item_snapshots` (item×pedido) | `silver.stg_shopee_ads` — anúncio×**período** |
| Bloqueio | CMV sem vigência | taxa e CMV não existem nesse grão → exigiriam rateio |

**[RECOMENDAÇÃO]** Interpretação **A (listing)**: é o único grão em que receita, taxa e CMV podem coexistir sem rateio arbitrário; a B exigiria ratear taxa de marketplace e CMV por campanha, o que a §7.7 proíbe sem regra aprovada; e "custo de produto" é atributo de listing, não de campanha.

---

## 7. Riscos de atribuição

### 7.1 Denominador trocado (risco ativo, em produção)

**[FATO]** A aba Canais mostra vídeo/live/card como % do GMV canônico. Somam 106,85%. Um gestor que tentar fechar 100% vai concluir que a tela está errada — e estará certo. **Único achado desta auditoria que já afeta o usuário hoje.**

### 7.2 Somar atribuição com partição

`ad_revenue` (janela de atribuição) e `gmv_video` (partição de outro GMV) **não vivem no mesmo espaço**. Na mesma barra de 100% criam um número que não existe.

### 7.3 "Orgânico" como resíduo

`orgânico = GMV − ad_revenue` é inválido nos três canais: categorias não exclusivas (uma venda pode ser vídeo **e** atribuída a Ads), não exaustivas, sem denominador nem janela comuns. Em Shopee daria **negativo** em 318 das 905 linhas. **[FATO]**

### 7.4 Duas fontes de taxa de marketplace do TikTok que divergem ~5x

| Fonte | jan–jun/2026, 5 marcas | % da receita líquida |
|---|---:|---:|
| `gold.tiktok_brand_daily.total_fees` | 21.040.312,56 | 29,55% |
| `vw_dre_mensal.comissao_plataforma` | 4.268.886,53 | 6,00% |

**[INFERÊNCIA]** Somando os três componentes do DRE (`comissao_plataforma` + `taxa_sfp` + `taxa_por_item`) a ordem de grandeza se aproxima, mas **não reconciliei**. **Falta:** a soma dos três contra `total_fees` na mesma janela, e entender a diferença de competência (`order_create_time` vs competência de repasse). **Não publicar taxa de TikTok sem declarar a fonte.**

### 7.5 Competência: custo de hoje aplicado a receita de ontem

**[FATO]** `preco_custo` é o cadastro **atual**, sem vigência. Multiplicá-lo por unidades de janeiro/2026 atribui custo de agosto a venda de janeiro. Em categoria com variação de custo isso não é aproximação, é erro de competência.

### 7.6 Fan-out no join de custo — mitigado, não eliminado

**[FATO]** Sob a chave `(marca_comercial, código)` o join **não** faz fan-out pelos 128 códigos multimarca (§4.3). Restam 47 colapsos de chave dentro do cadastro. **[RECOMENDAÇÃO]** o teste anti-fan-out (contagem de linhas do fato idêntica antes e depois) continua obrigatório — a ausência de fan-out foi medida no snapshot atual, não garantida por constraint.

### 7.7 Rateio arbitrário

Não existe regra aprovada para alocar taxa de marketplace, comissão de afiliado (mês×marca) ou custo de parceiro entre produtos ou campanhas. Qualquer margem por campanha exige esse rateio. **Bloqueado por desenho.**

### 7.8 Linhagem obtida num instante, não versionada

**[FATO]** A definição da view foi lida por `pg_get_viewdef` em 2026-08-19. Ela **não está versionada** em repositório nosso e pode mudar sem revisão. **[RECOMENDAÇÃO]** qualquer consumo exige teste de reconciliação contínuo (`receita_liquida` × GMV de TikTok, limiar em torno de 1,0111) que **falhe alto** quando a relação se mover.

### 7.9 Marca fora de escopo

**[FATO]** O DRE tem 8 marcas (inclui `azbuy`, `gocase`, `denavita`); a Torre opera 5. `denavita` não aparece em `docs/source_mapping.md`. Qualquer sync precisa aplicar `BRANDS_IN_SCOPE`.

### 7.10 `transaction_type` sem filtro

**[INFERÊNCIA]** A view soma todas as linhas de `tiktok_payments_by_order` sem filtrar `transaction_type`. Na amostra (rituaria, jun/26) só existe `ORDER`, então é inócuo ali. Se outros tipos existirem em outras janelas, entram nas somas sem rótulo. **Falta:** domínio completo de `transaction_type`.

---

## 8. Nomenclatura dos custos de afiliado — correção da revisão 1

### 8.1 O que a fonte diz

**[FATO]** Os três custos vêm do **mesmo** `fee_breakdown` e os três são campos `affiliate_*` na taxonomia do TikTok:

| Coluna do DRE | Campo da fonte | Rótulo recomendado |
|---|---|---|
| `comissao_criadores` | `affiliate_commission_amount_before_pit` | **Comissão de criadores** |
| `comissao_ads_afiliados` | `affiliate_ads_commission_amount` | **Comissão de afiliados/Ads** |
| `comissao_agencias` | `affiliate_partner_commission_amount` | **Custo de parceiro/agência** |

**A revisão 1 afirmava que `comissao_agencias` "não é afiliado, é custo de gestão de mídia". Isso era inferência minha a partir do apelido da coluna, e a fonte a contradiz**: o campo é `affiliate_partner_commission_amount` — comissão paga ao **parceiro do programa de afiliados** (tipicamente MCN/agência que gerencia criadores). É um componente do programa de afiliados, com um beneficiário diferente.

**[FATO]** `before_pit` em `affiliate_commission_amount_before_pit` indica valor **antes de retenção de imposto**. Comparar esse componente com os outros dois exige saber se eles também são pré-retenção. **Não verificado.**

### 8.2 Contrato recomendado

**[RECOMENDAÇÃO]**

1. Exibir os **três separados**, sempre, com os rótulos acima. O motivo correto não é "um deles não é afiliado" — é que **são beneficiários diferentes com decisões de gestão diferentes**, e agregá-los apaga a única informação acionável.
2. **Não** agregar automaticamente. Um "Total comercial agregado" só depois de decisão explícita.
3. **[FATO]** Se a decisão for agregar, note que a fonte **já tem** um agregado: `total_marketing = criadores + parceiro/agência + ads_afiliados + gmv_max_ads`. Adotar um total próprio diferente desse cria uma terceira definição concorrente.
4. Nunca rotular o conjunto como "custo de afiliados" sem listar os componentes: **[FATO]** o número varia de 0,75% a 9,91% da receita líquida conforme o que se soma.

A recomendação D2 (§11) permanece, explicitamente marcada como **[RECOMENDAÇÃO] de produto** — não como definição da fonte.

---

## 9. Margem por canal — viabilidade separada

Cada canal tem fórmula, grão e bloqueio próprios. **[RECOMENDAÇÃO]** não forçar os três a uma fórmula única.

### 9.1 Mercado Livre — PARCIAL, o mais avançado

**[FATO]** Confirmado e exato: `gross_revenue − marketplace_fee − ad_spend` (1.767/1.767, desvio 0,0000).

**[RECOMENDAÇÃO]** Nome: **contribuição pré-CMV**. Não chamar de margem, lucro ou resultado.

**[FATO]** Margem real segue bloqueada por: CMV com vigência; frete; descontos/devoluções conforme a definição de receita (D1); afiliado se aplicável; impostos.

**[FATO]** Ressalvas de implementação: `(brand, item_id)` tem 117 duplicados (F25) e a tabela é lifetime sem data (F26) — a contribuição por listing é **acumulada**, não mensal.

### 9.2 Shopee — PARCIAL-FRACO, não é o mesmo caso do ML

**[FATO]** Existem receita, taxa e desconto no grão item-de-pedido (F40), com cobertura boa e campos 100% não-nulos. Mas:

- **[FATO]** Ads **não está reconciliado nem alocado por listing** — `silver.stg_shopee_ads` é anúncio×**período** (F39) e o `ad_spend` do mart é rateio artificial (F22);
- **[FATO]** afiliado **não existe** (F10);
- **[FATO]** CMV é snapshot sem vigência (F29);
- **[FATO]** `seller_discount_2` segue ambíguo (F41).

**Não afirmar que a contribuição pré-CMV do ML já está pronta para Shopee.** No máximo, e só se o produto aceitar o indicador:

> **contribuição comercial parcial antes de CMV, Ads e afiliados**

**[DECISÃO]** aceitar ou não esse indicador pertence ao proprietário.

### 9.3 TikTok — BLOQUEADO para listing

**[FATO]** Motivos cumulativos:

- sem chave de SKU adequada no grão de produto (F34);
- `sku_transactions` existe mas está vazio na amostra (F42) — não é caminho hoje;
- custos de afiliado e frete só em mês×marca (F7);
- taxa de marketplace com duas fontes divergentes (§7.4);
- CMV não reconciliável no grão atual.

---

## 10. CMV do Bling — registro correto

**[FATO]** Cobertura financeira alta (98,6% ML / 97,3% Shopee) **não equivale a validade histórica**. São coisas diferentes:

- **[FATO]** `preco_custo` é snapshot do cadastro **atual**, sem `valid_from`/`valid_to` (F29);
- **[FATO]** 349 dos 2.319 produtos têm `preco_custo = 0` — zero não é custo, é ausência;
- **[FATO]** 128 códigos aparecem em mais de uma marca; sob a chave `(marca_comercial, código)` isso deixa de causar fan-out (§4.3), mas o join direto **só por código** produziria fan-out;
- **[FATO]** não há coluna de moeda (F31);
- **[FATO]** semântica não confirmada: custo contábil ou comercial, com ou sem frete de entrada, e tratamento de kit/bundle e variação;
- **[FATO]** TikTok não tem chave suficiente (F34, F42);
- **[FATO]** meses históricos **não podem** usar silenciosamente o custo atual.

**[RECOMENDAÇÃO]**

- Criar `dim_product_cost_history` com chave canônica **marca + SKU** (marca via `marca_comercial`), `valid_from`/`valid_to`, teste anti-fan-out, e cobertura/*unmatched* explicitados em cada carga.
- Enquanto ela não existir, qualquer uso do custo atual deve ser rotulado **"estimativa com custo atual"**, restrito a cenário exploratório, e **nunca** apresentado como margem histórica real.

### 10.1 Por que publicar os custos do DRE **não** é quick win

**Correção da revisão 1**, que classificou isso como quick win de frontend. Não é. Exige, no mínimo:

1. confirmação de linhagem da view — **feita** (§2.0), mas a view segue não versionada (§7.8);
2. confirmação do de-para de marcas — **feita** (§4.4);
3. snapshot/materialização no Neon (a Torre não lê o Data Mart em request);
4. contrato de grão **mês × marca** declarado no nome da tabela;
5. testes contra fan-out;
6. reconciliação contínua com limiar que falhe alto;
7. `freshness` / `source_run_id` / competência;
8. API aditiva;
9. estados N/D vs N/A nas superfícies;
10. QA.

**[FATO]** Além disso, a view **não é consultável em tempo de request**: uma agregação simples sobre ela passou de 2 minutos, e agora se sabe por quê — 2.111.263 linhas de origem com extração de `jsonb` por campo, sem índice útil na janela. Consultá-la pelo backend hospedado não é opção.

**[RECOMENDAÇÃO]** Quick win verdadeiro, sem fonte nova: corrigir a apresentação do mix do TikTok; corrigir rótulos enganosos; declarar N/D vs N/A nas superfícies existentes; manter indisponível o que não tem fonte.

---

## 11. Contrato do mix de conteúdo do TikTok

**Classificação: CORREÇÃO DE VERDADE DA INTERFACE — independente de novas fontes.**

Isto **não** é a entrega do pedido 2 (share em ML e Shopee), que segue bloqueado. É o reparo de um número errado já exibido.

### 11.1 O contrato

**[RECOMENDAÇÃO]**

- **Rótulo:** "**Mix do GMV de conteúdo**" (ou equivalente que carregue "conteúdo"). Nunca "share das vendas", "share de vendas totais" ou "atribuição de vendas".
- **Denominador:** `base_conteudo = gmv_video + gmv_live + gmv_card`.
- As três categorias somam **100% dessa base**, por construção.
- **Exibir o valor monetário da base** (R$ 70,41 mi no período medido), não só percentuais.
- **Declarar linhagem distinta** do GMV canônico, explicitamente.
- **Não** dizer que decompõe o GMV oficial.
- **Não** esconder a divergência de 6,85%.
- **Não** somar Ads, afiliado ou orgânico à mesma barra.

```
base_conteudo = gmv_video + gmv_live + gmv_card
share_video   = gmv_video / NULLIF(base_conteudo, 0)
share_live    = gmv_live  / NULLIF(base_conteudo, 0)
share_card    = gmv_card  / NULLIF(base_conteudo, 0)
-- soma = 100,00% por construção

-- Diagnóstico de reconciliação/qualidade — NUNCA share, NUNCA "cobertura":
divergencia_linhagem = base_conteudo / NULLIF(gmv_canonico, 0)   -- 1,0685 no período medido
```

**[RECOMENDAÇÃO]** `divergencia_linhagem` só pode aparecer como **diagnóstico de reconciliação/qualidade**. Quando exceder 100%, é uma divergência de linhagem — não é "cobertura", que sugeriria um subconjunto. Não exibir como share em nenhuma circunstância.

### 11.2 Texto de evidência sugerido

> Mix medido sobre R$ 70,41 mi de GMV de conteúdo — 6,85% acima do GMV oficial de R$ 65,90 mi. As duas contas vêm de fontes diferentes: a quebra por canal de conteúdo **não** decompõe o GMV oficial.

### 11.3 Impedir comparação enganosa

**[RECOMENDAÇÃO]** O bloco fica contido na seção TikTok, **sem** coluna equivalente em ML/Shopee — nesses canais o conceito é **N/A**, e uma coluna vazia sugeriria "zero live" em vez de "conceito inexistente".

---

## 12. Proposta de UI/drill-down

Padrão da Torre: **insight → explicação → evidência → próximo destino**. Nada implementado.

### 12.1 Regra de N/D vs N/A — a distinção mais importante

**[RECOMENDAÇÃO]** estender o contrato que já existe no backend (`_ADS_APPLICABLE`, `_COST_APPLICABLE`, `_SHIPPING_APPLICABLE`), não recriar:

| Situação | Exibir | Exemplo |
|---|---|---|
| Canal não opera isso | **N/A** + motivo | Ads no TikTok; live no ML |
| Canal opera, sem fonte | **N/D** + link para esta auditoria | Afiliado em ML/Shopee |
| Canal opera, fonte existe, período sem dado | **—** + cobertura | `canceled_orders` do TikTok |
| Fonte existe com ressalva | valor + **badge** | Taxa do TikTok (§7.4) |

Nunca preencher nenhum dos quatro com zero.

### 12.2 Bloco de custo de aquisição (quando UE2 existir)

**Decisão que suporta:** *onde estou pagando para vender, e esse custo está proporcional ao retorno?*

| Card | Canais | Aviso obrigatório |
|---|---|---|
| Ads ÷ GMV | ML, SH | TikTok **N/A**, nunca 0% |
| ROAS de Ads | ML, SH | Shopee: "receita atribuída pode exceder o GMV do dia — janela própria" |
| Comissão de criadores ÷ Receita líquida | **TikTok só** | "Grão mês×marca. Fonte de DRE, linhagem não versionada" |
| Comissão de afiliados/Ads ÷ Receita líquida | **TikTok só** | idem |
| Custo de parceiro/agência ÷ Receita líquida | **TikTok só** | "Componente `affiliate_partner` — beneficiário distinto" |
| Cobertura de dados | todos | "Retorno de afiliados: sem receita atribuída. ML e Shopee: sem fonte de afiliado." |

### 12.3 Drill-downs

| Origem | Abre | Grão | Estado |
|---|---|---|---|
| Ads ÷ GMV (ML) | campanhas da marca | `ref_date × campaign_id` | Viável hoje (F38) |
| Ads ÷ GMV (SH) | anúncios da marca | anúncio × período | Viável, com aviso de grão |
| Custo de criadores | série mensal da marca | `mes × brand` | Viável; **sem** drill para criador |
| Mix de conteúdo | criadores da marca | `date × creator` | Viável, **só GMV** — rotular "receita de conteúdo, sem custo atribuído" |

### 12.4 Aviso de confiança por canal

| Canal | Aviso |
|---|---|
| TikTok | "Sem Ads geridos (N/A). Taxa com duas fontes divergentes. Custo de afiliado só em grão mensal. Mix de conteúdo de linhagem distinta do GMV oficial." |
| Mercado Livre | "Comissão sem competência mensal. Sem fonte de afiliado. Ads confiável, inclusive por campanha." |
| Shopee | "Ads sem granularidade diária real. Receita atribuída pode exceder o GMV do dia. Taxas detalhadas por item. Sem fonte de afiliado." |

### 12.5 O que **não** entra na tela

Coluna de "share afiliado"; coluna de "orgânico"; qualquer "margem real"; `estimated_margin`; ROAS/ROI de afiliado; e qualquer barra de 100% somando Ads + live + produto + afiliado.

---

## 13. Decisões

### 13.1 Podem ser adotadas como default recomendado

**[RECOMENDAÇÃO]** — não exigem o proprietário; são consequência da evidência:

| # | Default |
|---|---|
| **D3** | Grão de anúncio = **listing**, não campanha (§6.3) |
| **D5** | Manter **ROAS e ROI como conceitos diferentes**; usar ROAS de afiliado **somente** quando houver receita atribuída |
| **D6** | Aceitar **"contribuição pré-CMV"** como indicador separado, nunca como margem |
| — | **Não somar parceiro/agência dentro de "afiliados"** sem listar componentes (§8) |
| — | **Não usar custo atual silenciosamente em histórico** — rotular "estimativa com custo atual" |
| — | Corrigir o mix do TikTok conforme §11 |

### 13.2 Exigem decisão explícita do proprietário/stakeholder

**[DECISÃO]**

| # | Decisão | Opções | Observação |
|---|---|---|---|
| **D1** | Definição de **receita por canal** | GMV canônico · GMV de conteúdo · receita líquida do DRE | **Deve ser channel-specific.** Ver 13.3 |
| **D2** | "Custo de afiliados" inclui criadores? | só ads_afiliados (0,75%) · + criadores (6,87%) · + parceiro/agência (9,91%) | A fonte já tem `total_marketing` como precedente (§8.2) |
| **D4** | Permitir estimativa com custo atual em histórico? | sim, rotulada · não · obter histórico do ERP | Bloqueia qualquer CMV histórico |
| **D7** | Aceitar a **contribuição parcial da Shopee**? | sim · esperar Ads por listing e CMV | §9.2 |
| **D8** | **Política de TikTok com frete** no GMV | incluir · manter `sub_total` | Frente separada (`docs/tiktok_gmv_com_frete_decisao.md`) |

### 13.3 As definições devem ser channel-specific antes de qualquer consolidação

**[RECOMENDAÇÃO]** A decisão de frete do TikTok (D8) **não deve bloquear uma entrega exclusivamente de ML**.

Razão: o frete do TikTok altera o GMV **do TikTok**. A contribuição pré-CMV do ML depende de `gross_revenue` de `gold.ml_produto_pnl`, que não tem relação alguma com essa decisão. Esperar D8 para avançar em ML seria acoplamento artificial.

Portanto: fixar a definição de receita **por canal**, avançar onde a definição já está estável (ML), e consolidar cross-channel só quando todas as definições estiverem fechadas. Qualquer indicador consolidado antes disso soma grandezas de definição diferente.

---

## 14. Roadmap corrigido

### UE-F1 — Verdade da atribuição do TikTok
**Sem novas fontes. Não depende de decisão alguma.** Corrigir o denominador; renomear para "Mix do GMV de conteúdo"; expor a base monetária e a divergência de linhagem; aplicar N/D vs N/A. É correção de defeito ativo — não é a entrega do pedido 2.

### UE1 — Contratos e aquisição de fontes
Confirmar a **estabilidade** da linhagem do DRE e obter a definição versionada (a linhagem em si está confirmada — §2.0); entender por que `tiktok_affiliate_marketplace_creators` está vazia; localizar relatórios de afiliados em ML/Shopee; definir a fonte de CMV histórico; resolver os mappings restantes (`transaction_type`, semântica de `preco_custo`); leitura manual de `Regua_Cobranca_Marketplaces_gobeaute.pdf`.

### UE2 — Serving / marts
Fato TikTok **mensal por marca** (nome declarando o grão); `dim_product_cost_history`; reconciliação contínua com limiar; D−1 / competência / freshness / `source_run_id`.

### UE3 — API e Canais
Custos **separados** (nunca pré-agregados); N/D vs N/A; nenhuma receita de afiliado inventada; nenhum share aditivo falso; atualização deliberada dos testes que travam campos de afiliado.

### UE4 — Unit economics por listing
**Começar pelo canal cuja fórmula e grão estiverem completos.** **ML pode avançar antes dos demais** se D1(ML) e D4 estiverem resolvidos. Nunca forçar os três canais a uma fórmula única prematuramente.

### UE5 — QA integrado
Fonte → mart → API → UI; reconciliação monetária; fan-out; null vs zero; filtros; drill-down; estados de cobertura.

### Separação exigida

**1. Quick wins confiáveis** (sem fonte nova, sem decisão) — todo o UE-F1; rótulos honestos; N/D vs N/A.

**2. Exigem nova fonte** — receita atribuída a afiliado; afiliado em ML/Shopee; CMV com vigência; comissão de ML com competência mensal; taxa de TikTok reconciliada; Ads por listing na Shopee.

**3. Decisões de produto** — §13.2.

**4. Permanecem bloqueados** — share orgânico por resíduo; barra de 100% somando Ads+live+produto+afiliado; `estimated_margin` como margem; margem por campanha (exige rateio); CMV estimado ou preço de venda como custo; comissão lifetime de ML aplicada a um mês; ROAS/ROI de afiliado.

---

## 15. Limitações da auditoria

**Verificado.** Conectividade read-only aos dois bancos (`transaction_read_only = on`; Data Mart é réplica, `pg_is_in_recovery() = true`); busca no catálogo completo (87 colunas de afiliado/criador/comissão, 262 de custo); schemas e contagens de 19 tabelas; **`pg_get_viewdef` de `raw.vw_dre_mensal` obtida integralmente**, com dependências por `pg_rewrite`/`pg_depend`; testes de fan-out da view (passaram); de-para de marca confirmado na fonte; as reconciliações da §4; leitura das migrations 004/007/008/011, do parser de ads da Shopee, do contrato de staging e de `get_canais`.

**Não verificado — e o que limita:**

1. **Estabilidade da definição da view.** A linhagem está confirmada, mas a definição não é versionada e pode mudar sem revisão (§7.8). Limita: consumir sem reconciliação contínua.
2. **Domínio de `transaction_type`** em todo o histórico (§7.10). A réplica deu timeout nas varreduras amplas de `tiktok_payments_by_order` (2,1M linhas). Limita: garantir que as somas do DRE contenham só transações de pedido.
3. **Se `affiliate_ads` e `affiliate_partner` também são pré-retenção**, como o `before_pit` de `affiliate_commission` (§8.1). Limita: somar os três componentes.
4. **Divergência de taxa de TikTok (§7.4)** não reconciliada. Limita: publicar taxa de TikTok.
5. **`Regua_Cobranca_Marketplaces_gobeaute.pdf` não lido** — texto CID/fonte embutida, sem biblioteca de PDF no ambiente (instalar exigiria aprovação). Extraí só o título e um heading ("1. Triagem: dois trilhos"). Pelo nome trata de régua de cobrança de marketplaces e **poderia conter as taxas contratuais**. Nada dele foi usado como evidência. **Requer leitura manual.**
6. **Semântica de `preco_custo`** — contábil ou comercial, com/sem frete de entrada, tratamento de kit/bundle e variação (67 registros `eh_variacao`). Limita: interpretar o CMV.
7. **`seller_discount_2` da Shopee** permanece ambíguo (F41).
8. **Cobertura temporal desigual.** Reconciliações concentradas em jan–jun/2026. Julho e agosto/2026 não reconciliados; no TikTok vale a maturação de status conhecida.
9. **Grão real de `tiktok_affiliate_marketplace_creators`** não testável (tabela vazia). Os nomes `total_*` com `reference_date` sugerem **snapshot cumulativo**. Se for populada, **testar cumulatividade antes de somar**.
10. **`sku_transactions`** verificado apenas em amostra de 200 linhas de uma marca em 2 dias (F42). Não descarta que esteja populado em outras janelas — mas não é caminho hoje.
11. **Sem validação com o stakeholder.** Todas as recomendações e as decisões da §13 são propostas.

### Classificação dos documentos externos ao worktree

| Documento | Classificação | Uso feito |
|---|---|---|
| `docs/tiktok_marts_grain_extension_handoff.md` | **Relevante** | §7 (colunas de conteúdo não somam o GMV de pedidos) e §5 sustentam §4.2 e o bloqueio de CMV do TikTok |
| `docs/gold_vs_marts_matrix.md` | **Relevante** | §0.1 (gap de +6,85%) é a base da §4.2 |
| `docs/tiktok_gmv_com_frete_decisao.md` | **Relevante** | Decisão pendente de frete → D8; **não** bloqueia entrega de ML (§13.3) |
| `docs/cobertura_canais_avoe.md` | **Parcialmente relevante** | Cobertura de canais (Torre = 97,75% do Avoe). Contextualiza denominador; não usado como evidência de atribuição |
| `docs/supabase-schema.md` | **Outro projeto/domínio** | Projeto `torre_de_performance_b2b` (Supabase). **Não usado.** Sem ligação comprovada com a Torre |
| `docs/octaprice/Regua_Cobranca_Marketplaces_gobeaute.pdf` | **Potencialmente relevante — NÃO VERIFICADO** | Não lido (limitação 5). Nada dele foi usado |

Nenhum desses arquivos foi copiado, editado, movido ou versionado.

---

## 16. Referências de código e evidência

| Item | Onde |
|---|---|
| Query da aba Canais | `apps/api/app/services/performance_service.py::get_canais` |
| Contrato aplicável/disponível por canal | `performance_service.py` (`_ADS_APPLICABLE`, `_COST_APPLICABLE`, `_SHIPPING_APPLICABLE`) |
| Shares de conteúdo exibidas | `apps/web/app/canais/page.tsx` (cards ~451-464; colunas ~706-750; `AttributionBar` ~736) |
| Testes que travam campos de afiliado | `apps/api/tests/test_canais_channel_rows.py`, `apps/web/tests/canais-channel-metrics.test.ts` |
| Nota de margem indisponível | `apps/web/src/lib/produtos-tab-transition.ts::marginUnavailableNote` |
| Allowlist canônica de marcas | `apps/api/app/services/gold_service.py::BRANDS_IN_SCOPE`; `pipelines/connectors/tiktok/connector.py` |
| Rateio de período do Ads da Shopee | `pipelines/connectors/shopee/_parser_ads.py` |
| Contrato de colunas da Shopee | `pipelines/tests/test_shopee_staging_contract.py` |
| Regra canônica de GMV do TikTok | `pipelines/connectors/tiktok/connector.py` |
| Linhagem de conteúdo declarada em DDL | `apps/api/alembic/versions/007_…py`, `008_…py` |
| `estimated_margin` na origem | `alembic/versions/004_create_product_tables.py`; `docs/sections/produtos_audit.md` §647-650 |
| Gate 1 da Canais (conclusão superada quanto a afiliados) | `docs/sections/canais_audit.md` §14 |
| Semântica de `total_fees`/`total_settlement` | `docs/data_contracts.md`; `docs/sections/financeiro_audit.md` §11 |
| Linhagem do DRE (fontes) | `raw.tiktok_payments_by_order`, `raw.tiktok_shop_statements` — via `pg_get_viewdef`/`pg_depend` |
| De-para de marca ERP→Torre | `silver.bling_produtos.marca_comercial` (**não** `gold.dim_marca_conta`, que é conta de pagamento) |
