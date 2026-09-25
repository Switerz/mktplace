# MARGEM-REAL-1 — Diagnóstico de completude financeira

Data: 2026-09-25
Tipo: auditoria **read-only** de descoberta. Nenhum código, schema, migration, pipeline, métrica ou banco foi alterado.
Base Git: `8ccd1e6` (`origin/main`)
Antecedentes: `docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md` (UE1), `docs/UNIT_ECONOMICS_ATTRIBUTION_AUDIT.md` (UE0), `docs/gold_vs_marts_matrix.md`

## Convenção de marcações

- **[FATO]** — verificado por consulta read-only reproduzível ou por código versionado.
- **[INFERÊNCIA]** — apoiado em evidência, não provado. Diz o que falta para virar fato.
- **[RECOMENDAÇÃO]** — juízo de engenharia desta auditoria.
- **[BLOQUEIO]** — impede a entrega; diz o que destrava.
- **[DECISÃO NECESSÁRIA]** — pertence ao proprietário do indicador.

---

## 1. Método e janela

**[FATO]** Toda consulta foi read-only (`set_session(readonly=True)`). Zero DDL, DML, migration, sync ou deploy.
Nenhum DSN, senha, host, token, `order_id`, nome de comprador, e-mail, telefone, endereço ou handle de creator
aparece neste documento. Os SQLs da §10 são agregados e não retornam linha individual.

| Caminho | Variável de ambiente | Estado | Camada |
|---|---|---|---|
| Neon | `DATABASE_URL` | Disponível, `ssl_in_use=True`, primário | `marts.*` — camada servida hoje |
| Data Mart (RDS) | `DATAMART_DATABASE_URL` | Disponível via VPN, `pg_is_in_recovery=true` | `gold`, `silver`, `api`, `finance`, `gerencial` |

**Janela de reconciliação: julho + agosto de 2026**, meses fechados, cinco marcas em escopo da Torre
(`apice`, `barbours`, `kokeshi`, `lescent`, `rituaria`). A marca `denavita` aparece nas fontes fiscais
mas não é servida pela Torre, e foi excluída de todos os totais comparativos.

**[FATO] Declaração de camada.** Todo número deste documento diz de que camada veio. GMV de `marts` e GMV de
`gold` não são a mesma definição no TikTok (ver `docs/gold_vs_marts_matrix.md` §0.1: +6,85% em jan–jun/2026),
e compará-los sem qualificar a camada produz conclusão errada.

---

## 2. Resumo executivo

**[FATO] O CMV existe e cobre os três marketplaces.** A fonte é `gold.nf_vendas_plataforma_sku_mensal`
(grão: mês × marca × canal × plataforma × SKU), com `custo`, `custo_unitario_produto`, `fonte_custo`,
`custo_provisorio` e `receita_com_custo`. Isto corrige o registro anterior de que não havia CMV por canal.

**[FATO] O bloqueio não é ausência de CMV — é atribuição de canal.** A receita dessa fonte reconcilia com o
GMV servido pela Torre no TikTok e **não reconcilia** em ML nem em Shopee (§4).

**[FATO] Nada de margem ou lucro é publicado hoje.** O frontend não expõe margem em nenhuma tela; há notas
explícitas de indisponibilidade em `apps/web/app/inteligencia/page.tsx`, `apps/web/app/produtos/page.tsx`
e `apps/web/src/components/MercadoLivreProductTable.tsx`. O endpoint `get_financeiro`
(`apps/api/app/services/performance_service.py`) serve GMV, settlement, fees, Ads e frete — nunca uma subtração
que resulte em margem. **Os riscos da §6 são riscos do próximo gate, não defeitos no ar.**

---

## 3. Matriz por componente e canal

**[FATO]** Legenda: ✅ disponível e reconciliado · ⚠️ disponível com restrição · ❌ ausente.

| componente | ML | Shopee | TikTok | fonte de verdade | grão | competência | cobertura | bloqueio |
|---|---:|---:|---:|---|---|---|---:|---|
| GMV bruto | ✅ | ✅ | ✅ | `marts.fact_marketplace_daily_performance.gmv` | dia × loja × marketplace | `date` | 100% | definição TikTok diverge gold×marts |
| Cancelamentos | ⚠️ | ⚠️ | ⚠️ | `.canceled_orders` | idem | `date` | contagem | **é contagem de pedidos, não valor** |
| Devoluções | ❌ | ✅ | ❌ | `.returned_orders` | idem | `date` | só Shopee | NULL em ML e TikTok |
| Reembolsos | ❌ | ❌ | ❌ | `.refunded_orders` | idem | `date` | 0% | NULL nos três canais |
| Comissão/tarifa | ❌ | ✅ | ✅ | SH/TK: `.total_fees` · ML: `api.ml_order_line_items.sale_fee` | SH/TK: dia × marca · ML: item de pedido | SH/TK: `date` · ML: `date_created` | ML 100% na fonte | **ML não chega ao mart** |
| Afiliados | ❌ | ❌ | ✅ | `marts.fact_tiktok_affiliate_cost_order_monthly` | mês × marca | `ref_month` (coorte de pedido) | jun–set/2026 | **dupla contagem com `total_fees`** (§6.1) |
| Mídia/Ads | ✅ | ✅ | ❌ | `.ad_spend`, `.ad_revenue` | dia × marca | `date` | TikTok NULL | sem Ads TikTok no mart |
| CMV | ⚠️ | ⚠️ | ✅ | `gold.nf_vendas_plataforma_sku_mensal.custo` | mês × marca × plataforma × SKU | `data_mes` (emissão) | TK ~99% · SH ~39% · ML indeterminada | atribuição de plataforma (§4) |
| Frete/fulfillment | ✅ | ✅ | ⚠️ | ML/SH: `.seller_shipping_cost` · TK: `gold.tiktok_settlements_summary.total_shipping_cost_amount` | ML/SH: dia × marca · TK: statement | ML/SH: `date` · TK: `statement_month` | TikTok sem quebra por marca | `gerencial.gobeaute_despesa_frete` é % global (§7.3) |
| Impostos | ❌ | ❌ | ❌ | `gold.webgex_faturamento_resumo.impostos_total` | mês × canal × unidade | `mes` | 0% por marketplace | canal não separa marketplace (§7.2) |
| Moeda | BRL | BRL | BRL | — | — | — | — | nenhuma fonte multi-moeda no escopo |

---

## 4. Cobertura do CMV e divergências de atribuição

### 4.1 Cobertura declarada pela própria fonte

**[FATO]** `gold.nf_vendas_plataforma_sku_mensal`, `canal='marketplace'`, jan–set/2026, todas as marcas
(inclui `denavita`). `pct_receita_com_custo` = `receita_com_custo / receita_produto`:

| plataforma | linhas | receita | custo | % receita com custo |
|---|---:|---:|---:|---:|
| tiktokshop | 1.481 | 65.189.877 | 20.860.919 | 99,98% |
| mercadolivre | 1.445 | 43.669.780 | 17.006.942 | 98,70% |
| shopee | 1.427 | 13.682.450 | 3.658.551 | 98,73% |
| loja_propria | 188 | 54.705.319 | 11.351.319 | 100,00% |
| sem_loja | 859 | 12.141.345 | 9.124.998 | 99,66% |
| outro_marketplace | 444 | 2.532.567 | 787.410 | 99,91% |
| nao_mapeada | 406 | 503.681 | 129.788 | 99,93% |
| amazon | 62 | 45.578 | 3.653 | 36,38% |

**[FATO]** Esta coluna mede cobertura de custo **sobre a receita da própria fonte fiscal** — não sobre o GMV
da Torre. A cobertura efetiva sobre o que a Torre serve é o produto das duas, e depende da §4.2.

### 4.2 Reconciliação contra o GMV servido — jul+ago/2026, 5 marcas

**[FATO]** Torre = `marts.fact_marketplace_daily_performance`. Fonte fiscal = `gold.nf_vendas_plataforma_sku_mensal`,
`canal='marketplace'`, `tipo_operacao='normal'` (único valor presente na janela).

| Canal | GMV Torre (marts) | Receita fonte fiscal | Razão | Veredito |
|---|---:|---:|---:|---|
| TikTok | 20.754.924 | 20.479.970 | **0,99** | reconcilia |
| Mercado Livre | 10.329.446 | 17.491.438 | **1,69** | não reconcilia |
| Shopee | 12.537.600 | 4.853.956 | **0,39** | não reconcilia |

**[FATO] O TikTok reconcilia também por marca**, o que o agregado sozinho não provaria:

| marca | Torre (marts) | fonte fiscal | razão |
|---|---:|---:|---:|
| apice | 2.630.278 | 2.660.445 | 1,011 |
| barbours | 6.960.785 | 6.918.748 | 0,994 |
| kokeshi | 8.966.745 | 8.754.760 | 0,976 |
| lescent | 1.237.796 | 1.206.313 | 0,975 |
| rituaria | 959.320 | 939.704 | 0,980 |

**[FATO] ML e Shopee são erráticos por marca** — o desvio não é um fator constante, então não é corrigível
por calibração:

| marca | ML: razão | Shopee: razão |
|---|---:|---:|
| apice | (sem ML na Torre) | 0,997 |
| barbours | 2,501 | 0,135 |
| kokeshi | 1,436 | 0,381 |
| lescent | 1,021 | 0,451 |
| rituaria | 1,431 | 0,536 |

**[FATO]** Na mesma janela há R$ 4.092.375 de receita classificada como `plataforma='sem_loja'`
(kokeshi 1.804.460 · barbours 1.656.072 · lescent 631.843), e o `erp_cd` é NULL em **2.142 de 2.142** linhas
de marketplace na janela — não há emissor para desambiguar.

**[INFERÊNCIA]** Parte do déficit da Shopee está em `sem_loja`. Não está provado: `sem_loja` soma R$ 4,09 mi
e o déficit Shopee é R$ 7,68 mi, e nada no registro associa uma linha `sem_loja` a um canal. **Para virar fato**
é preciso conhecer a regra que deriva `plataforma` em `gold.nf_vendas_unificada_v2` — transformação que,
como registrado no UE1 §4.1 para `gold.tiktok_brand_daily`, não está versionada em repositório nosso.

**[INFERÊNCIA]** O excesso do ML (+69%) sugere receita não-marketplace classificada como `mercadolivre`,
ou contas/CNPJs fora do escopo da Torre. Não está provado. Contra-indício relevante: `lescent` bate a 1,02,
o que um erro sistemático de escopo dificilmente produziria.

### 4.3 CMV medido na janela

**[FATO]** jul+ago/2026, 5 marcas, `canal='marketplace'`:

| plataforma | custo | % sobre a receita da própria fonte |
|---|---:|---:|
| tiktokshop | 6.045.738 | 29,5% |
| mercadolivre | 6.183.825 | 35,4% |
| shopee | 1.285.728 | 26,5% |

**[FATO]** Só o valor do TikTok é utilizável contra o GMV da Torre, pela §4.2.

---

## 5. Convenções de sinal

**[FATO]** `marts.fact_marketplace_daily_performance.total_fees` tem sinal divergente por canal, medido em jul/2026:

| canal | exemplo (kokeshi, jul/2026) | sinal |
|---|---:|---|
| TikTok Shop | −1.297.449 | negativo |
| Shopee | +1.379.652 | positivo |
| Mercado Livre | NULL | ausente |

**[FATO]** `marts.fact_tiktok_affiliate_cost_order_monthly` é **assinado** (negativo), conforme
`COMMENT ON COLUMN` na migration `apps/api/alembic/versions/012_*.py`.

**[FATO]** `get_financeiro` normaliza com `abs()` em `apps/api/app/services/performance_service.py`
(linha ~1180: `tk_fees = abs(_f(tk["total_fees"]))`). A normalização é local ao endpoint, não à coluna.

**[BLOQUEIO]** Qualquer agregação nova que some `total_fees` cru entre canais subtrai de um e soma no outro.
**Destrava com** uma convenção declarada no contrato do fato (custo sempre negativo, ou sempre positivo)
aplicada na escrita, não em cada leitor.

---

## 6. Riscos de superestimar resultado

### 6.1 Dupla contagem de afiliado no TikTok — material

**[FATO]** `gold.tiktok_settlements_summary` decompõe `total_fee_tax_amount` e a comissão de afiliado está
**dentro** dele:

| statement_month | total_fee_tax | platform_commission | affiliate_commission | service_fees | taxes | shipping_cost |
|---|---:|---:|---:|---:|---:|---:|
| 2026-06 | −3.404.220 | −285.571 | −442.728 | −290.881 | 0 | −121.349 |
| 2026-07 | −3.470.074 | −409.353 | −1.188.770 | −751.830 | 0 | −141.680 |
| 2026-08 | −4.122.556 | −511.799 | −1.496.864 | −913.121 | 0 | −150.991 |
| 2026-09 | −3.331.380 | −393.958 | −1.151.930 | −706.767 | 0 | −134.732 |

**[FATO]** `gold.tiktok_brand_daily.total_fees` — origem do `total_fees` do mart — é da mesma ordem de grandeza
que `total_fee_tax_amount`: jul −3.369.132, ago −4.215.422.

**[FATO]** A soma dos três componentes de `marts.fact_tiktok_affiliate_cost_order_monthly` em jul/2026,
5 marcas, é **−967.764**. Contra o GMV TikTok de julho **no mart** (9.803.249), isso é **9,9%**.
Contra o GMV da **gold** (12.503.147), 7,7%. As duas leituras existem porque as camadas divergem; a relevante
para uma tela servida por `marts` é a primeira.

**[INFERÊNCIA]** Somar afiliado sobre `total_fees` duplicaria aproximadamente esse valor. Não é exato: a
comissão do settlement (−1.188.770) e a da coorte de pedido (−967.764) têm universos distintos — o UE1-C
documenta que 24,6% das transações cruzam a fronteira mensal entre pedido e statement. **A ordem de grandeza
do erro está provada; o valor exato, não.**

**[RECOMENDAÇÃO]** Tratar `total_fees` do TikTok como **já líquido de afiliado**. O fato de afiliado serve
para *decompor* o custo, nunca para somá-lo.

### 6.2 Outros riscos medidos

**[FATO]** GMV TikTok da `gold` está +6,85% sobre `marts` em jan–jun/2026, e ~72% do gap é frete pago pelo
comprador (`docs/gold_vs_marts_matrix.md` §0.1). Uma margem calculada sobre o GMV da gold parte de uma receita
que inclui frete.

**[FATO]** `marts...canceled_orders` é contagem. Não existe, em nenhuma das camadas inspecionadas, o **valor**
de GMV cancelado por dia × marca × canal.

**[INFERÊNCIA]** Usar `gold.nf_vendas_plataforma_sku_mensal` como CMV do ML sem resolver a §4.2 traz 69% de
receita a mais que a Torre. Se o CMV for aplicado como percentual sobre o GMV da Torre, o efeito é diluído;
se for aplicado em valor absoluto, o custo é superestimado na mesma proporção.

---

## 7. Fontes avaliadas e descartadas

**[FATO] §7.1 `silver.margem_diaria` / `api.margem_diaria` não serve à Torre.** Apesar do nome, é Gocase:
tem `device`, `case_type`, `material`, `case_ref`, `de_para_gocase`. Não tem marca gobeaute nem canal de
marketplace. Traz `custo_fab_diario`, `custo_imposto`, `custo_logistica`, `custo_administrativo` — o modelo de
custo completo que falta aqui existe, para outra empresa do grupo.

**[FATO] §7.2 `gold.webgex_faturamento_resumo` não tem granularidade de marketplace.** Tem `impostos_total`,
`cmv_total`, `icm`, `pis`, `cofins`, `ipi`, `ii`, `valor_difal` — mas os únicos valores de `canal` em 2026 são
`BB Industria`, `Online`, `Outros`, `Stores`, `Wholesale`. Não separa ML de Shopee de TikTok.
**[BLOQUEIO]** Esta é a única fonte de impostos encontrada. **Destrava com** uma regra de atribuição de
`canal` para marketplace, que hoje não existe na fonte.

**[FATO] §7.3 `gerencial.gobeaute_despesa_frete` é um percentual global.** Três colunas: `data_emissao`,
`pct_frete`, `data_carga`. Sem marca, sem canal, sem valor absoluto. Exemplo: `2026-08-01 → 0,1082`.
Serve para rateio agregado, não para frete por canal.

**[FATO] §7.4 O schema `finance` é inteiro Gocase.** As doze tabelas são `gocase_*`
(`gocase_produtos_cmv_*_webgex`, `gocase_produtos_impostos_depara_webgex`, etc.). Nenhuma cobre gobeaute.

---

## 8. Distinção entre os níveis financeiros

**[FATO]** Estado por nível, jul+ago/2026, com a nomenclatura exigida pelo gate:

| nível | definição | ML | Shopee | TikTok |
|---|---|---|---|---|
| 1. GMV | valor bruto dos pedidos | ✅ publicável | ✅ publicável | ✅ publicável |
| 2. Receita após cancelamentos/reembolsos | GMV − cancelado − devolvido − reembolsado | ⚠️ ver nota | ⚠️ ver nota | ⚠️ ver nota |
| 3. Margem conhecida após marketplace | receita − comissão − afiliados − Ads | ❌ sem comissão no mart | ⚠️ sem afiliados | ✅ **viável** |
| 4. Margem de contribuição | nível 3 − CMV − frete | ❌ CMV não atribuível | ❌ CMV cobre 39% | ⚠️ falta frete por marca |
| 5. Lucro líquido | nível 4 − impostos − demais custos | ❌ | ❌ | ❌ |

**Nota sobre o nível 2. [FATO]** O GMV oficial da Torre já é ratificado **sem frete, sem cancelado e sem
devolvido** — no TikTok pela allowlist de status do conector (`COMPLETED/DELIVERED/IN_TRANSIT`, ver
`docs/gold_vs_marts_matrix.md` §0.1), na Shopee pela seleção de pedidos ativos
(`pipelines/connectors/shopee/_parser.py`, variável `active`).
**[INFERÊNCIA]** Os níveis 1 e 2 portanto colapsam na prática. Não está provado que a exclusão é completa nos
três canais: `refunded_orders` é NULL em todos, e `returned_orders` só existe na Shopee — ou seja, não há como
**medir** o que foi excluído. **Para virar fato**, é preciso o valor (não a contagem) do que é retirado.

**[FATO] Nenhum destes cinco níveis pode ser chamado de "lucro líquido"** enquanto impostos forem 0% por canal
(§7.2). O nome correto para o melhor caso disponível hoje é **"margem conhecida após marketplace"**, e só no TikTok.

---

## 9. Limitações e decisões pendentes

**[FATO] Limitações desta auditoria:**

1. Janela de dois meses (jul+ago/2026). As razões da §4.2 não foram testadas em outros períodos.
2. A derivação de `plataforma` em `gold.nf_vendas_unificada_v2` não foi lida — a transformação não está
   versionada em repositório nosso. Todas as afirmações sobre *por que* ML e Shopee divergem são inferência.
3. `tipo_operacao` tem um único valor (`normal`) na janela. Não se sabe se devoluções aparecem como outro tipo
   em janelas maiores.
4. Não foi medido se o `sale_fee` do ML cobre reembolso/estorno de comissão em pedidos cancelados.
5. A reconciliação usa `receita_produto` (exclui `receita_frete`). O GMV da Torre também exclui frete, mas a
   equivalência das duas exclusões não foi provada linha a linha.

**[DECISÃO NECESSÁRIA]:**

1. Qual camada é a oficial para o GMV do TikTok — `gold` ou `marts`. Segue em aberto desde
   `docs/gold_vs_marts_matrix.md` §0.1, e a margem herda a escolha.
2. Se "margem conhecida após marketplace" pode ser publicada para **um** canal só, ou se a Torre exige
   paridade entre os três antes de expor qualquer nível acima de GMV.
3. Convenção de sinal única para colunas de custo no fato (§5).
4. Quem é o proprietário do indicador de margem — o dicionário de KPI exige fórmula, filtros, dimensões, dono,
   cadência e limitações, e nenhum desses campos existe hoje para margem.

---

## 10. Reprodução

Consultas agregadas, sem credenciais e sem dado pessoal. Conexão por variável de ambiente
(`DATABASE_URL` para Neon, `DATAMART_DATABASE_URL` para o Data Mart), sempre em sessão read-only.

### 10.1 Cobertura de CMV por plataforma — Data Mart

```sql
SELECT plataforma, canal, count(*) AS linhas,
       min(data_mes) AS de, max(data_mes) AS ate,
       round(sum(receita_produto)) AS receita,
       round(sum(custo))           AS custo,
       round(100.0 * sum(receita_com_custo) / nullif(sum(receita_produto), 0), 2)
         AS pct_receita_com_custo
FROM gold.nf_vendas_plataforma_sku_mensal
WHERE data_mes >= '2026-01-01'
GROUP BY 1, 2
ORDER BY 6 DESC NULLS LAST;
```

### 10.2 CMV por marca e plataforma na janela — Data Mart

```sql
SELECT marca, plataforma,
       round(sum(receita_produto)) AS receita,
       round(sum(custo))           AS custo,
       round(100.0 * sum(custo) / nullif(sum(receita_produto), 0), 1) AS cmv_pct
FROM gold.nf_vendas_plataforma_sku_mensal
WHERE data_mes BETWEEN '2026-07-01' AND '2026-08-01'
  AND canal = 'marketplace'
  AND plataforma IN ('tiktokshop', 'mercadolivre', 'shopee')
GROUP BY 1, 2
ORDER BY 1, 2;
```

### 10.3 GMV e componentes servidos pela Torre — Neon

```sql
SELECT m.nome_marketplace AS canal, l.brand_key AS marca,
       date_trunc('month', f.date)::date AS mes,
       round(sum(f.gmv))                  AS gmv,
       round(sum(f.total_fees))           AS fees,
       round(sum(f.ad_spend))             AS ads,
       round(sum(f.seller_shipping_cost)) AS frete_seller,
       sum(f.orders)          AS pedidos,
       sum(f.canceled_orders) AS canc,
       sum(f.returned_orders) AS dev,
       sum(f.refunded_orders) AS reemb
FROM marts.fact_marketplace_daily_performance f
JOIN marts.dim_marketplace m USING (marketplace_id)
JOIN marts.dim_loja        l USING (loja_id)
WHERE f.date BETWEEN '2026-07-01' AND '2026-08-31'
GROUP BY 1, 2, 3
ORDER BY 3, 1, 2;
```

### 10.4 Decomposição dos fees do TikTok — Data Mart

Evidencia que `affiliate_commission` está dentro de `total_fee_tax_amount` (§6.1).

```sql
SELECT statement_month,
       round(sum(total_fee_tax_amount))       AS fee_tax,
       round(sum(platform_commission))        AS plataforma,
       round(sum(transaction_fee))            AS transacao,
       round(sum(affiliate_commission))       AS afiliado,
       round(sum(service_fees))               AS servico,
       round(sum(taxes))                      AS impostos,
       round(sum(total_shipping_cost_amount)) AS frete
FROM gold.tiktok_settlements_summary
WHERE statement_month >= '2026-06-01'
GROUP BY 1
ORDER BY 1;

SELECT date_trunc('month', date)::date AS mes,
       round(sum(total_fees)) AS total_fees,
       round(sum(gmv))        AS gmv
FROM gold.tiktok_brand_daily
WHERE date >= '2026-06-01'
GROUP BY 1
ORDER BY 1;
```

### 10.5 Comissão do ML — Data Mart

**Armadilha de chave.** `api.ml_orders.id` é um surrogate sequencial (1, 2, 3, …), **não** o identificador do
pedido no Mercado Livre. O join correto é por `order_id`. Um join por `id` devolve **zero linha** sem erro —
falso negativo silencioso.

```sql
-- cobertura da coluna
SELECT count(*) AS itens, count(sale_fee) AS com_fee,
       round(100.0 * count(sale_fee) / nullif(count(*), 0), 2) AS pct_preenchido
FROM api.ml_order_line_items;

-- integridade do join
SELECT count(*) AS linhas_line_items, count(o.order_id) AS casadas
FROM api.ml_order_line_items li
LEFT JOIN api.ml_orders o ON li.order_id = o.order_id;

-- comissao e take rate por marca, sem duplicar pedido multi-item
WITH ped AS (
  SELECT brand, order_id, max(total_amount) AS total_amount
  FROM api.ml_orders
  WHERE date_created >= '2026-07-01' AND date_created < '2026-09-01'
  GROUP BY 1, 2
),
fee AS (
  SELECT o.brand, sum(li.sale_fee) AS comissao
  FROM api.ml_order_line_items li
  JOIN ped o ON li.order_id = o.order_id
  GROUP BY 1
)
SELECT p.brand,
       round(sum(p.total_amount)) AS gmv_bruto,
       count(*)                   AS pedidos,
       round(f.comissao)          AS comissao,
       round(100.0 * f.comissao / nullif(sum(p.total_amount), 0), 2) AS take_rate_pct
FROM ped p
JOIN fee f ON f.brand = p.brand
GROUP BY p.brand, f.comissao
ORDER BY 2 DESC;
```

**[FATO]** Resultado em jul+ago/2026: `sale_fee` preenchido em 566.439 de 566.439 itens (100%); join casa
566.439 de 566.439 linhas.

> ### Correção aplicada no MARGEM-REAL-2 (2026-09-25)
>
> Os dois números abaixo estavam **subestimados**, e o contrato de fonte do gate seguinte mediu por quê.
>
> **[FATO] `sale_fee` é por UNIDADE, não por item.** A comissão do item é `sale_fee * quantity`. Medido:
> `sale_fee / unit_price` fica estável em 0,1707–0,1828 para qualquer `quantity`, enquanto
> `sale_fee / (unit_price * quantity)` cai com a quantidade (0,1707 → 0,0894 → 0,0608 → 0,0457). A query
> original somava `sale_fee` cru.
>
> **[FATO] Os +3,9% eram cancelamento, e a causa está provada.** `status = 'paid'` na janela soma
> **10.283.761** — idêntico ao GMV de `gold.ml_gestao_diaria`. O excesso eram 5.934 pedidos `cancelled`
> (R$ 443.208) e 72 `partially_refunded` (R$ 10.032). Sobre a população correta, a reconciliação é exata:
> **248 de 248** células dia × marca com GMV e pedidos idênticos, diferença máxima 0,00.
>
> Valores corrigidos, população `status = 'paid'`, comissão = `SUM(sale_fee * quantity)`:
>
> | marca | GMV (= gold) | pedidos | comissão | take rate |
> |---|---:|---:|---:|---:|
> | kokeshi | 3.432.578 | 53.727 | 579.112 | 16,87% |
> | barbours | 3.131.719 | 31.232 | 461.728 | 14,74% |
> | rituaria | 2.078.989 | 20.801 | 282.641 | 13,60% |
> | lescent | 1.640.476 | 25.870 | 198.231 | 12,08% |
>
> Comissão total da janela: **1.521.713** (a soma das quatro linhas arredondadas dá 1.521.712 — a
> diferença é o arredondamento por marca). A tabela abaixo fica como registro do que foi medido no
> MARGEM-REAL-1, com a ressalva acima.

| marca | GMV bruto (fonte) | pedidos | comissão | take rate |
|---|---:|---:|---:|---:|
| kokeshi | 3.575.546 | 55.998 | 593.153 | 16,59% |
| barbours | 3.285.921 | 32.743 | 475.930 | 14,48% |
| rituaria | 2.139.518 | 21.419 | 276.783 | 12,94% |
| lescent | 1.736.016 | 27.476 | 204.027 | 11,75% |

**[FATO]** O GMV somado dessa fonte (10.737.001) fica **+3,9%** do GMV ML servido pela Torre (10.329.446)
na mesma janela — a menor divergência entre todas as fontes de custo avaliadas neste gate. O MARGEM-REAL-2
mediu a causa desses +3,9% (ver correção acima): são os pedidos cancelados e parcialmente reembolsados, que
a consulta original não filtrava.

### 10.6 Origem dos fees da Shopee — código versionado

`pipelines/connectors/shopee/_parser.py`:

```python
total_fees = sum(o["commission_net"] + o["service_fee_net"] for o in active)
```

**[FATO]** O `total_fees` da Shopee é comissão + taxa de serviço. Não inclui frete.
**[INFERÊNCIA]** Em jul/2026 isso representa 26,5% do GMV de kokeshi, acima do esperado para comissão pura —
consistente com a pendência já registrada de settlement Shopee acima de 100%. Não investigado neste gate.

---

## 11. Próximo gate recomendado

**[RECOMENDAÇÃO]** Sequência, nesta ordem. Cada passo é verificável antes do seguinte.

### 1. Levar `api.ml_order_line_items.sale_fee` ao mart

O menor incremento útil e o de menor risco. A coluna está 100% preenchida, o join é íntegro (566.439/566.439)
e o take rate resultante (11,75%–16,59%) é plausível por marca. Fecha a linha "comissão" nos três canais sem
depender de terceiros nem da fonte fiscal.

Cuidados que este gate já identificou:

- Definir a convenção de sinal **antes** de escrever (§5), não no leitor.
- A competência da fonte é `date_created`; o fato usa `date`. Declarar qual vale.
- Verificar se `sale_fee` estorna em pedido cancelado — não medido aqui (§9.4).
- Acrescentar coluna a um fato existente altera o INSERT mesmo sob feature flag quando o gerador deriva as
  colunas do primeiro registro. Gerar o SQL nos dois estados antes de prometer que o ALTER é folgado.

### 2. Reconciliar comissão e GMV do ML

Com a comissão no mart, comparar contra o GMV ML servido e contra o take rate por marca. A base de partida
é +3,9% (§10.5) — bom o bastante para reconciliar, e ruim o bastante para exigir explicação antes de publicar.

### 3. Só então avaliar a publicação da "margem conhecida após marketplace" por canal

Não antes. E com três condições explícitas:

- o nome é **"margem conhecida após marketplace"**, nunca "margem" nem "lucro" (§8);
- `total_fees` do TikTok é tratado como já líquido de afiliado (§6.1);
- a tela declara a camada do GMV e o que **não** está no cálculo — CMV, frete e impostos.

**[RECOMENDAÇÃO]** Fora desta sequência, e independente dela: resolver a derivação de `plataforma` em
`gold.nf_vendas_unificada_v2` é o que destrava CMV para ML e Shopee (§4.2). É investigação de fonte, não de
pipeline, e pode correr em paralelo porque suas respostas vêm de fora do repositório.
