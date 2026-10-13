# DRE-CONTRATO-0 — Contrato gerencial da DRE: fontes, grão e completude

Data: 2026-10-09
Tipo: contrato + auditoria **read-only**. Nenhum código, schema, migration, publisher, API, frontend ou dado
foi alterado.
Base Git: `f3d90b0` (`origin/main`)
Antecedentes: `docs/margem_real_1_diagnostico.md` (MARGEM-REAL-1), `docs/gold_vs_marts_matrix.md`,
`docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md` (§18.8, §23); roteiro de reuniões em `docs/dre_reunioes_fontes_financeiras.md`

## Convenção de marcações

- **[FATO]** — verificado por consulta read-only reproduzível (§16) ou por código versionado.
- **[INFERÊNCIA]** — apoiado em evidência, não provado. Diz o que falta para virar fato.
- **[RECOMENDAÇÃO]** — juízo de engenharia deste gate.
- **[BLOQUEIO]** — impede a entrega; diz o que destrava.
- **[DECISÃO NECESSÁRIA]** — pertence ao gestor, ao responsável por Fiscal/CMV ou ao responsável por Conciliação/Repasse.

---

## 1. Resumo executivo

1. **[FATO] Nenhuma célula canal × marca × mês pode ser chamada de "margem real" hoje.** Impostos sobre venda
   não estão em nenhuma camada servida, e medimos que pesam **11,6% a 14,6% da receita fiscal** por marketplace
   (ago/2026, §4.10). Qualquer margem sem eles fica superestimada nessa ordem.
2. **[FATO] O CMV do Mercado Livre passou a reconciliar.** Desde o MARGEM-REAL-1 (25/09) a fonte fiscal foi
   reprocessada: a razão receita fiscal ÷ GMV da Torre no ML caiu de 1,69 para **0,998–1,025** (jul–set/2026),
   com todas as marcas dentro de ±4%. **TikTok segue reconciliando** (0,957–1,020 por marca). **A Shopee segue
   sem reconciliar** (0,29–0,45 no total; só a Apice bate).
3. **[FATO] O `total_fees` do TikTok no mart está congelado e imaturo nos meses recentes.** Contra a fonte de
   pagamentos por coorte de pedido, o mart bate em jun e jul (±0,15%) e fica **−20,6% em agosto e −61,3% em
   setembro**. Usar o mart para a DRE de ago/set subestima a taxa TikTok nessas mesmas proporções.
4. **[FATO] Somar afiliados às taxas do TikTok conta o custo duas vezes, e isto está provado no grão da
   transação** (§6): o repasse fecha como `receita + fee_and_tax + frete` em **100% das 758.324 transações**
   de jun–set/2026, e o afiliado está dentro do `fee_and_tax`. Em jul/2026 o erro seria de **9,9% do GMV
   subtraídos duas vezes**.
5. **[FATO] O CMV de setembro é 100% provisório** na fonte fiscal; agosto é parcialmente provisório. A DRE não
   pode tratar mês recente como fechado.
6. **[RECOMENDAÇÃO] MVP:** DRE **mensal**, grão **mês × canal × marca**, só **meses maduros**, com a linha final
   chamada **"Margem conhecida"** e um selo de completude. Primeiro corte: **ML e TikTok** com margem conhecida
   a partir de **jul/2026**; **Shopee** com receita, taxas e Ads, mas **margem indisponível** até o responsável por Fiscal/CMV explicar
   a receita fiscal ausente.

---

## 2. Método, janela e camadas

**[FATO]** Toda consulta rodou em sessão `readonly=True`, com rollback ao final. Zero DDL, DML, publisher,
sync ou deploy. Nenhum DSN, host, credencial, `order_id`, nome de comprador ou dado pessoal aparece neste
documento; todas as consultas da §16 são agregadas.

| Caminho | Variável | Camada lida |
|---|---|---|
| Neon | `DATABASE_URL` | `marts.*` — camada servida pela Torre |
| Data Mart (RDS, réplica) | `DATAMART_DATABASE_URL` | `gold`, `silver`, `raw`, `api` |

**Janela de referência:** jun–set/2026, cinco marcas em escopo (`apice`, `barbours`, `kokeshi`, `lescent`,
`rituaria`). **Mês de demonstração:** jul/2026 — o único mês em que taxa TikTok madura, comissão ML carregada,
CMV não provisório e Ads Shopee completo coexistem.

**[FATO] Declaração de camada.** Todo número diz de que camada veio. GMV = `marts.fact_marketplace_daily_performance.gmv`.
No TikTok esse GMV é `SUM(total_amount)` da população comercial — **inclui frete pago pelo comprador**
(Gate DQ-TK1, `pipelines/connectors/tiktok/connector.py`); no ML e na Shopee, não inclui.

---

## 3. A DRE mínima

### 3.1 Estrutura

```
L1  Receita bruta (GMV oficial da Torre)
L2  (−) Cancelamentos em valor                 [informativo: já fora do L1 nos três canais]
L3  (−) Devoluções e reembolsos em valor
L4  (=) Receita líquida conhecida              = L1 − L3
L5  (−) Taxas do marketplace
L6  (−) Ads / mídia
L7  (−) Afiliados                              [somável SÓ onde não estiver dentro de L5]
L8  (−) Frete pago pelo vendedor
L9  (−) CMV
L10 (=) Margem conhecida                       = L4 − L5 − L6 − L7(somável) − L8 − L9
L11 (−) Impostos sobre venda                   [fora da margem conhecida até a fonte ser ratificada]
L12 (=) Margem após impostos                   [só existe com L11 disponível]
```

### 3.2 Regras que valem para todas as linhas

- **[RECOMENDAÇÃO] Sinal.** Na DRE, receita é positiva e todo custo é guardado como **magnitude ≥ 0**, subtraído
  na fórmula. A normalização acontece **uma vez, no publisher da DRE**, a partir do sinal medido de cada fonte
  (§4) — nunca em cada leitor, que é o defeito registrado no MARGEM-REAL-1 §5.
- **[RECOMENDAÇÃO] Ausência ≠ zero.** Componente sem observação é `NULL` com `status = 'ausente'`; zero medido é
  `0` com `status = 'medido'`. Proibido `COALESCE(custo, 0)` em qualquer soma de margem.
- **[RECOMENDAÇÃO] Margem conhecida soma só o que existe** e declara, na mesma linha, a lista nominal do que
  ficou de fora. Não existe "estimativa" silenciosa para componente ausente.
- **[RECOMENDAÇÃO] Competência.** Mês do **pedido** (data de criação) para L1–L8. O CMV vem por **mês de emissão
  da NF** — aproximação aceitável só enquanto a reconciliação da §4.9 passar (decisão D4, §10).
- **[RECOMENDAÇÃO] Maturidade.** Uma célula só sai como "fechada" depois que todas as suas fontes estiverem
  maduras (§7). Antes disso, é "prévia" e não exibe margem.

---

## 4. Contrato por linha

### 4.1 L1 — Receita bruta

| Campo | Contrato |
|---|---|
| Definição | GMV oficial da Torre, já ratificado sem cancelados |
| Fonte | `marts.fact_marketplace_daily_performance.gmv` (Neon) |
| Grão | dia × loja (marca) × marketplace → agregado a mês |
| Chaves | `date`, `loja_id` → `dim_loja.brand_key`, `marketplace_id` |
| Filtros | 5 marcas em escopo; ML sem Apice (não opera no canal) |
| Sinal | positivo |
| Cadência | diária; TikTok e ML com lookback incremental; Shopee por export manual (último dia carregado: 28/09/2026) |
| Cobertura | **[FATO]** 100% dos dias de jun–ago nos três canais; Shopee de setembro só até 28/09 |
| Reconciliação | **[FATO]** ML: mart × `gold.ml_gestao_diaria` diverge +0,33% (jul), +0,63% (ago), +2,41% (set) — a gold mudou depois da carga. TikTok e ML batem com a receita fiscal (§4.9) |
| Limitação | **[FATO]** TikTok inclui frete pago pelo comprador; ML e Shopee não. **[FATO]** Shopee vem do relatório shop-stats, sem contraprova independente nesta auditoria |
| Responsável | Dados |
| Decisão pendente | D3 (frete no TikTok, §10) |

### 4.2 L2 — Cancelamentos em valor

| Campo | Contrato |
|---|---|
| Definição | Valor dos pedidos cancelados antes do faturamento |
| Fonte | **nenhuma em valor no mart**; `canceled_orders` é **contagem** |
| Grão / chaves | dia × marca × canal (contagem) |
| Sinal | n/a |
| Cobertura | **[FATO]** contagem preenchida nos três canais em 2026 (TikTok passou a medir na Raw no DQ-TK1) |
| Reconciliação | n/a |
| Limitação | **[FATO]** O GMV dos três canais **já exclui** cancelados (TikTok: allowlist comercial; ML: `status='paid'`; Shopee: `status != 'Cancelado'`). Logo L2 **não deduz nada** de L1 — é informativo |
| Responsável | Dados |
| Decisão pendente | nenhuma. **[RECOMENDAÇÃO]** Exibir L2 como contagem e taxa, com a nota "já excluído da receita" |

### 4.3 L3 — Devoluções e reembolsos em valor

| Campo | Contrato |
|---|---|
| Definição | Valor devolvido ao comprador após a venda (devolução, reembolso total ou parcial) |
| Fonte | **ausente em valor** em todas as camadas servidas |
| Candidatas | **[FATO]** `silver.protheus_devolucoes` tem valor e custo das devoluções fiscais, mas `canal` só assume `ecommerce`/`atacado`, e a chave `identificador_unico_venda` casou **0 de 29.771** linhas (jul+ago) contra `gold.nf_vendas_unificada_v2`. **[FATO]** `gold.nf_vendas_unificada_v2.item_devolvido` é falso em 100% das linhas de marketplace jun–set. **[FATO]** `marts...returned_orders` é contagem e só existe na Shopee; `refunded_orders` é NULL nos três |
| Sinal | **[FATO]** `protheus_devolucoes.valor_total` é negativo |
| Cobertura | 0% por marketplace |
| Limitação | **[INFERÊNCIA]** O GMV do TikTok (status `COMPLETED/DELIVERED/IN_TRANSIT`) e o da Shopee (todo status ≠ cancelado) **incluem** pedidos devolvidos depois da entrega; o ML exclui `partially_refunded` do GMV. Para virar fato, falta medir a devolução em valor por canal |
| Responsável | Conciliação/Repasse + Fiscal/CMV (devolução fiscal) |
| Decisão pendente | Qual é a fonte de autoridade (perguntas C6, F5) |

### 4.4 L4 — Receita líquida conhecida

`L4 = L1 − L3`. **[FATO]** Com L3 ausente nos três canais, hoje `L4 = L1` e a célula carrega
`devolucoes = 'ausente'`. **[RECOMENDAÇÃO]** Rotular "Receita líquida conhecida (sem devoluções)".

### 4.5 L5 — Taxas do marketplace

| Campo | ML | Shopee | TikTok |
|---|---|---|---|
| Definição | comissão de venda | comissão líquida + taxa de serviço | `fee_and_tax` do repasse (comissão, taxas de serviço, **afiliados**, etc.) |
| Fonte | `marts...total_fees` ← `api.ml_order_line_items.sale_fee × quantity`, `status='paid'` | `marts...total_fees` ← export de pedidos (`commission_net + service_fee_net`) | **[RECOMENDAÇÃO]** `silver.stg_tiktok_payments_by_order.fee_and_tax_amount`, `transaction_type='ORDER'`, por mês de `order_create_time` — **não** o mart (ver limitação) |
| Grão | dia × marca | dia × marca | transação → mês × marca |
| Chaves | `order_id` (nunca `ml_orders.id`, que é surrogate) | pedido do export | `brand`, mês de `order_create_time` |
| Sinal na fonte | **positivo** | **positivo** | **negativo** |
| Cadência | diária; carga começa nos últimos 5 dias de jun | export manual | diária na silver |
| Cobertura | **[FATO]** jan–mai **NULL**; jun 5 de 30 dias por marca; jul em diante 100% | 100% dos dias carregados | **[FATO]** jun–set completo por coorte |
| Reconciliação | **[FATO]** mart × recomputação direta da fonte: jul **exato**, ago +0,03%, set **+1,26%** (a fonte mudou depois da carga) | **[FATO]** sem contraprova independente | **[FATO]** mart × silver: jun −0,05%, jul +0,15%, **ago −20,6%, set −61,3%** |
| Limitação | ver §7 (deriva pós-carga) | **[INFERÊNCIA]** 21,7–25,9% do GMV é alto para "comissão"; consistente com a pendência de settlement Shopee >100%. Não investigado | **[FATO]** o mart lê `gold.tiktok_brand_daily.total_fees` por passthrough; **[INFERÊNCIA]** com lookback de 10 dias, dias antigos congelam antes de o repasse amadurecer |
| Responsável | Dados | Dados + Conciliação/Repasse | Dados + Conciliação/Repasse |
| Decisão pendente | — | C4 | C2 |

### 4.6 L6 — Ads / mídia

| Campo | ML | Shopee | TikTok |
|---|---|---|---|
| Fonte | `marts...ad_spend` ← `gold.ml_gestao_diaria` | `marts...ad_spend` ← export de Ads | **ausente** |
| Grão | dia × marca | dia × marca | — |
| Sinal | positivo | positivo | — |
| Cobertura | **[FATO]** 100% dos dias jan–set | **[FATO]** jun–jul 100%; **ago 27/31 dias; set 14/28 dias** | **[FATO]** `ad_spend` NULL em 100% dos dias |
| Reconciliação | **[FATO]** mart × gold: −0,9% em jul — mesma linhagem, **não é contraprova independente** | sem contraprova | — |
| Limitação | — | **[FATO]** dias faltantes em ago (4) e set (14) no mart. **[INFERÊNCIA]** são exports com o dia corrente parcial (D0) bloqueados pelo pipeline; não verificado neste gate | **[FATO]** existe `transaction_type = 'GMV_PAYMENT_FOR_TIKTOK_ADS'` (19 transações desde jun) e `raw.vw_dre_mensal.gmv_max_ads`; nenhuma vira custo de Ads no mart |
| Responsável | Dados | Dados | Dados + Marketing |
| Decisão pendente | C8 (Ads descontado do repasse?) | C8 | fonte de Ads TikTok |

### 4.7 L7 — Afiliados

| Campo | TikTok | ML | Shopee |
|---|---|---|---|
| Fonte | `marts.fact_tiktok_affiliate_cost_order_monthly` (3 componentes assinados) | — | — |
| Grão | mês de `order_create_time` × marca | — | — |
| Sinal | negativo | — | — |
| Cobertura | **[FATO]** jun/2025–out/2026 | sem programa medido | **[INFERÊNCIA]** comissão de afiliado Shopee pode estar dentro de `commission_net`; não verificado |
| Reconciliação | **[FATO]** fato × silver: **diferença < R$ 1** em cada marca × componente de jul (consulta arredondada a R$ 1) | — | — |
| Regra | **[FATO] Está DENTRO de L5** (§6). Na DRE, entra como **"dos quais: afiliados"** — linha de decomposição, **não somável** | n/a | ausente |
| Responsável | Dados | — | Conciliação/Repasse |
| Decisão pendente | — | — | C3 |

### 4.8 L8 — Frete pago pelo vendedor

| Campo | ML | Shopee | TikTok |
|---|---|---|---|
| Fonte | `marts...seller_shipping_cost` ← `gold.ml_gestao_diaria` | `marts...seller_shipping_cost` ← "Valor estimado do frete" do export | `silver.stg_tiktok_payments_by_order.shipping_cost_amount` (coorte de pedido) |
| Grão | dia × marca | dia × marca | transação → mês × marca |
| Sinal | positivo | positivo | negativo |
| Cobertura | **[FATO]** 100% | **[FATO]** 100% dos dias carregados | **[FATO]** jun–set; **não chega ao mart** |
| Peso (jul/2026) | 13,9% do GMV | 20,9% do GMV | 1,2% da receita |
| Limitação | — | **[INFERÊNCIA]** "valor estimado do frete" é o frete do pedido, **não necessariamente** custo do vendedor; 20,9% do GMV é implausível como custo. Tratar como **semântica não provada** | **[FATO]** corrige o MARGEM-REAL-1 §3: há quebra por marca na silver e em `gold.tiktok_settlements_summary.brand` |
| Responsável | Dados | Conciliação/Repasse | Dados |
| Decisão pendente | — | C4 | — |

### 4.9 L9 — CMV

| Campo | Contrato |
|---|---|
| Definição | Custo da mercadoria vendida das NFs de venda do marketplace |
| Fonte | `gold.nf_vendas_plataforma_sku_mensal.custo` (Data Mart), `canal='marketplace'` |
| Grão | mês de emissão × marca × canal × plataforma × SKU |
| Chaves | `data_mes`, `marca`, `plataforma` ∈ {`mercadolivre`, `shopee`, `tiktokshop`} |
| Filtros | 5 marcas; `tipo_operacao='normal'` (único valor em 2026) |
| Sinal | positivo |
| Cadência | mensal; última emissão vista 06/10/2026 |
| Cobertura de custo | **[FATO]** ≥ 98,6% da receita fiscal com custo em toda célula marca × plataforma × mês de jun–set, exceto Shopee Apice set (94,9%) |
| Provisório | **[FATO]** set/2026: `custo_provisorio = custo` em **100%** das células; ago/2026: parcial (ex.: Rituaria ML com 42% do custo provisório) |
| Reconciliação | **[FATO]** razão receita fiscal ÷ GMV Torre — ver tabela abaixo |
| Limitação | **[FATO]** A fonte é mutável: entre 25/09 e 09/10 a receita fiscal ML de jul+ago caiu 40% (razão contra a Torre de 1,69 para 1,01) e o `sem_loja` encolheu 98,7%. **[INFERÊNCIA]** reprocessamento da classificação de `plataforma`; a transformação segue não versionada em repositório nosso |
| Responsável | **Fiscal/CMV** |
| Decisão pendente | F1–F7, D4 |

**[FATO] Reconciliação receita fiscal ÷ GMV da Torre (marts), por marca:**

| canal | marca | jul | ago | set |
|---|---|---:|---:|---:|
| TikTok | apice | 1,001 | 1,020 | 0,982 |
| TikTok | barbours | 0,982 | 1,008 | 0,985 |
| TikTok | kokeshi | 0,971 | 0,981 | 0,957 |
| TikTok | lescent | 0,975 | 0,975 | 0,973 |
| TikTok | rituaria | 0,979 | 0,980 | 0,978 |
| **TikTok** | **total** | **0,979** | **0,994** | **0,971** |
| ML | barbours | 1,003 | 1,027 | 1,020 |
| ML | kokeshi | 1,011 | 1,022 | 0,979 |
| ML | lescent | 1,001 | 1,040 | 1,019 |
| ML | rituaria | 1,002 | 1,013 | 0,997 |
| **ML** | **total** | **1,005** | **1,025** | **0,998** |
| Shopee | apice | 1,011 | 0,977 | 0,837* |
| Shopee | barbours | 0,093 | 0,180 | 0,209* |
| Shopee | kokeshi | 0,468 | 0,243 | 0,310* |
| Shopee | lescent | 0,522 | 0,391 | 0,272* |
| Shopee | rituaria | 0,484 | 0,419 | 0,379* |
| **Shopee** | **total** | **0,445** | **0,293** | **0,321*** |

\* Shopee de setembro tem GMV só até 28/09 na Torre.

**[FATO]** A receita fiscal ausente da Shopee **não está** em `sem_loja`, `outro_marketplace` nem `nao_mapeada`
(somados, < 3% da receita fiscal de marketplace em cada mês). **[INFERÊNCIA]** Está emitida em outro `canal` (o canal `ecommerce` concentra
~51% da receita fiscal da Kokeshi em jul) ou em outro emissor. Pergunta F1.

### 4.10 L11 — Impostos sobre venda

| Campo | Contrato |
|---|---|
| Fonte candidata | **[FATO]** `raw.webgex_impostos_gobeaute` — linha de imposto por item de NF, com `chave_nota`, `circuito`, `codigo_imposto`, `debito_credito`, `valor_imposto` |
| Atribuição | **[FATO]** `chave_nota` casa com `gold.nf_vendas_unificada_v2.chave_nfe`, que tem `plataforma`; nenhuma nota casou com mais de uma plataforma (ago/2026) |
| Filtro usado | `circuito='VX'` (venda), `debito_credito='DB'` |
| Peso medido (ago/2026) | **[FATO]** ICMS+PIS+COFINS sobre a receita fiscal da plataforma: **ML 14,6%**, **TikTok 11,8%**, **Shopee 11,6%** |
| Limitação | **[FATO]** PIS+COFINS variam muito por plataforma (ML 1,9% vs TikTok 0,2% da receita). **[INFERÊNCIA]** emissores em regimes tributários diferentes. Cobertura (notas de marketplace com imposto ÷ notas de marketplace) **não medida**. 84.278 notas com imposto não casaram com nenhuma NF de venda das 5 marcas |
| Status | **candidata, não ratificada** — fica fora da margem conhecida |
| Responsável | **Fiscal/CMV** |
| Decisão pendente | F8, D6 |

**[FATO] Corrige o MARGEM-REAL-1 §7.2:** havia fonte de impostos por marketplace, só não tinha sido avaliada.

---

## 5. Matriz por canal

Legenda: ✅ disponível e reconciliado · ⚠️ parcial · ❌ ausente · 🅵 depende de Fiscal/CMV · 🅲 depende de Conciliação/Repasse ·
🅶 decisão do gestor.

| Linha | Mercado Livre | Shopee | TikTok Shop |
|---|---|---|---|
| L1 Receita bruta | ✅ | ⚠️ sem contraprova independente; set até 28/09 | ✅ inclui frete do comprador 🅶 |
| L2 Cancelamentos | ⚠️ contagem; já fora de L1 | ⚠️ contagem; já fora de L1 | ⚠️ contagem; já fora de L1 |
| L3 Devoluções/reembolsos | ❌ 🅲🅵 | ❌ (só contagem) 🅲🅵 | ❌ 🅲🅵 |
| L5 Taxas | ✅ desde jul/2026 (jun parcial) | ⚠️ sem contraprova; peso alto 🅲 | ⚠️ mart imaturo em ago/set → ✅ pela silver |
| L6 Ads | ✅ | ⚠️ ago/set incompletos | ❌ |
| L7 Afiliados | n/a | ❌ 🅲 | ✅ **dentro de L5, não somável** |
| L8 Frete vendedor | ✅ | ⚠️ semântica não provada 🅲 | ⚠️ na silver, fora do mart |
| L9 CMV | ✅ jul–ago; set provisório 🅵 | ❌ não reconcilia 🅵 | ✅ jul–ago; set provisório 🅵 |
| L11 Impostos | ⚠️ candidata 🅵 | ⚠️ candidata 🅵 | ⚠️ candidata 🅵 |
| **Margem conhecida (jul/2026)** | **parcial** | **indisponível** | **parcial** |

---

## 6. Proteção contra dupla contagem de afiliados no TikTok

### 6.1 Prova

**[FATO] P1 — Identidade do repasse.** Em `silver.stg_tiktok_payments_by_order`, `transaction_type='ORDER'`,
5 marcas, pedidos de jun–set/2026:

```
settlement_amount = revenue_amount + fee_and_tax_amount + shipping_cost_amount
```

fecha em **758.324 de 758.324** transações, resíduo **R$ 0,00**. Não existe um quarto termo pelo qual o
afiliado pudesse ser descontado do repasse fora do `fee_and_tax`.

**[FATO] P2 — Inclusão.** Nenhuma transação tem afiliado maior que o `fee_and_tax` (0 casos). Taxa efetiva
sobre a receita:

| mês do pedido | sem afiliado | com afiliado | com afiliado, **retirando** o afiliado | afiliado |
|---|---:|---:|---:|---:|
| jun | 19,58% | 30,70% | **19,90%** | 10,81% |
| jul | 17,73% | 29,02% | **17,69%** | 11,33% |
| ago | 23,26% | 31,58% | **20,50%** | 11,08% |
| set | 20,86% | 29,34% | **18,53%** | 10,81% |

Retirar o afiliado das transações que o têm devolve a taxa ao patamar das que não têm. O afiliado está dentro.

**[FATO] P3 — O mart herda o mesmo total.** `marts...total_fees` do TikTok ≈ `fee_and_tax` por coorte de pedido
nos meses maduros: jun −0,05%; jul +0,15% (mart contra silver).

**[FATO] P4 — O fato de afiliados é a mesma grandeza da fonte.** `marts.fact_tiktok_affiliate_cost_order_monthly`
bate com a soma de `affiliate_*_commission_amount` da silver, com diferença < R$ 1 por marca × componente
(consulta arredondada a R$ 1).

**Conclusão [FATO]:** taxas TikTok (mart ou silver) **já contêm** o afiliado. Em jul/2026, o afiliado equivale a
**34,6% das taxas** e **9,9% do GMV**. Somar as duas linhas subtrairia esses 9,9% do GMV uma segunda vez —
um custo que não existe.

**[INFERÊNCIA]** Comissões de agência e "GMV Max Ads" (`raw.vw_dre_mensal.comissao_agencias`, `gmv_max_ads`)
podem ser cobradas **fora** do repasse e, nesse caso, seriam custos adicionais legítimos. Não medido. Pergunta C3.

### 6.2 Regra do contrato

1. **[RECOMENDAÇÃO]** Afiliado TikTok entra na DRE como **"dos quais: afiliados"**, abaixo de L5, com
   `somavel = false`. A margem é calculada só sobre linhas `somavel = true`.
2. **[RECOMENDAÇÃO]** A regra é **amarrada à fonte de L5**: ela vale enquanto L5 TikTok for `fee_and_tax` do
   repasse. Trocar a fonte de L5 exige reabrir este contrato.
3. **[RECOMENDAÇÃO]** Os três componentes de afiliado não são somados entre si na tela (ponto P2 do contrato
   UE, §23 de `docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md`); na decomposição da DRE aparecem lado a lado.

### 6.3 Testes obrigatórios do próximo gate

| teste | o que prova |
|---|---|
| `margem = receita − Σ(linhas somáveis)` e a linha de afiliado TikTok tem `somavel=false` | afiliado nunca entra na soma |
| `abs(afiliado) <= abs(taxas)` em toda célula TikTok; violação aborta a publicação | afiliado cabe dentro da taxa |
| identidade P1 recalculada a cada carga; resíduo ≠ 0 aborta | a premissa da inclusão continua valendo |
| margem com afiliado somado ≠ margem publicada (teste negativo) | o teste detecta a regressão, não só passa |

---

## 7. Maturação, congelamento e deriva

| Fonte | Comportamento medido | Regra proposta |
|---|---|---|
| Taxa TikTok no mart | **[FATO]** −20,6% (ago), −61,3% (set) vs silver | **[RECOMENDAÇÃO]** ler L5 TikTok da silver por coorte de pedido; ou corrigir a recarga do mart antes |
| Taxa TikTok na silver | **[INFERÊNCIA]** set ainda pode crescer: repasse sai após entrega | **[RECOMENDAÇÃO]** mês maduro = M+2 completo; validar com Conciliação/Repasse (C2) |
| Comissão ML | **[FATO]** mart +1,26% acima da fonte em set: a fonte reclassificou pedidos depois da carga | recarga mensal do mês anterior antes de fechar a DRE |
| GMV ML | **[FATO]** mart acima da gold em +0,33% / +0,63% / +2,41% (jul/ago/set) | mesma recarga |
| CMV | **[FATO]** set 100% provisório; fonte reprocessada entre 25/09 e 09/10 | **[RECOMENDAÇÃO]** DRE grava a versão fiscal usada (data de leitura + soma de controle) |
| Shopee | **[FATO]** export manual; set até 28/09; Ads ago/set incompletos | célula Shopee só fecha com o mês completo |

---

## 8. Critérios de completude

**[RECOMENDAÇÃO]** Cada célula mês × canal × marca recebe exatamente um destes estados:

| Estado | Condição | Rótulo na tela |
|---|---|---|
| **Completa** | L1, L3, L5, L6, L8, L9 **e L11** disponíveis e reconciliados; mês maduro; L7 tratado pela §6 | "Margem após impostos" |
| **Parcial** | L1, L5 e L9 disponíveis e reconciliados; mês maduro; **cada** componente ausente nomeado | "Margem conhecida" + "faltam: …" |
| **Indisponível** | L1 ou L5 ou L9 ausente ou não reconciliado, **ou** mês imaturo | "Margem indisponível" + motivo; nunca um número |

- **[RECOMENDAÇÃO] Reconciliado** = razão receita fiscal ÷ GMV entre 0,95 e 1,05 na célula marca × mês (§4.9).
  Tolerância a ratificar com Fiscal/CMV e Conciliação/Repasse (F7, C7). Com ela, todas as células TikTok e ML de jul–set passam;
  da Shopee, só Apice.
- **[RECOMENDAÇÃO] Completude** exibida como contagem de componentes (`6 de 8`), não como percentual de valor:
  não se pondera o que não se mediu.
- **[FATO] Hoje nenhuma célula é "Completa"**, porque L11 não está ratificado em nenhum canal e L3 está ausente
  em todos.

---

## 9. DRE demonstrativa — jul/2026 (NÃO publicar)

Ilustra o contrato com números reais, em % da receita bruta de cada canal; valores absolutos omitidos. Estado: **parcial** (ML, TikTok) e **indisponível** (Shopee).

| Linha | Mercado Livre | TikTok Shop | Shopee |
|---|---:|---:|---:|
| L1 Receita bruta | 100% | 100% | 100% |
| L3 Devoluções | ausente | ausente | ausente |
| L5 Taxas | 14,8% | 28,5% | 25,9% |
| ↳ dos quais afiliados (não somável) | — | 9,9% | ausente |
| L6 Ads | 4,3% | ausente | 5,2% |
| L8 Frete vendedor | 13,9% | 1,2%¹ | 20,9%² |
| L9 CMV | 22,9% | 30,7% | não reconcilia (razão 0,445) |
| **L10 Margem conhecida** | calculável, **não publicada** | calculável, **não publicada** | **indisponível** |
| Faltam | devoluções, impostos | devoluções, Ads, impostos | CMV, devoluções, impostos |
| Impostos medidos em ago (referência) | ~14,6% | ~11,8% | ~11,6% |

¹ silver de pagamentos, não está no mart. ² semântica não provada (§4.8).

**[RECOMENDAÇÃO]** O valor de L10 é deliberadamente omitido. Mesmo como exemplo, um percentual de margem sem
impostos (~11,6–14,6% da receita) e sem devoluções seria lido como resultado. A margem conhecida só deve ser
exibida pelo publisher do próximo gate, com o selo de completude da §8.

---

## 10. Decisões necessárias do gestor

| # | Decisão | Recomendação |
|---|---|---|
| D1 | Nome da linha final enquanto houver componente ausente | "Margem conhecida", com "faltam: …" visível |
| D2 | Publicar margem de **um** canal sem paridade entre os três | Sim: ML e TikTok com margem; Shopee como "indisponível" com motivo |
| D3 | TikTok inclui frete do comprador na receita; ML e Shopee não | Manter (DQ-TK1) e declarar na tela; não ajustar os outros canais |
| D4 | Competência: pedido para receita/custos, emissão de NF para CMV | Aceitar enquanto a razão de §4.9 ficar em 0,95–1,05 |
| D5 | Mês recente: mostrar "prévia" sem margem ou esconder | Prévia com receita/taxas/Ads, sem margem, até M+2 |
| D6 | Impostos: linha "estimada" agora ou só após ratificação | Só após Fiscal/CMV ratificar a fonte (§4.10) |
| D7 | Escopo de marcas e contas | 5 marcas; Denavita e Gocase fora; ML sem Apice |
| D8 | Dono do indicador de margem (dicionário de KPI) | Gestor, com os responsáveis por Fiscal/CMV e por Conciliação/Repasse como fontes |

---

## 11. Perguntas para o responsável por Fiscal/CMV

| # | Pergunta | Por que, com o número |
|---|---|---|
| F1 | Onde estão as NFs da Shopee? Em quais `canal`/emissor/plataforma são classificadas? | Receita fiscal Shopee = 44,5% do GMV em jul e 29,3% em ago; Barbours 9,3% em jul. Apice bate (1,01) |
| F2 | Quando o CMV de um mês fecha, e a versão provisória é revisada? Qual versão é a oficial? | Set/2026 é 100% `custo_provisorio`; ago, parcial |
| F3 | Qual é a hierarquia oficial de `fonte_custo`? O que diferencia `*_vivo` da base consolidada? | 9 fontes em jul–set (bling_cadastro, fpea_base_consolidada[_vivo], protheus_cmp_sp[_vivo], nfs_gobeaute_*); 0,12% da receita sem `fonte_custo` |
| F4 | Como o custo de kits é formado na NF (soma dos componentes)? | Kits são o caso de maior risco de CMV errado (nome de produto divergente já registrado em `dim_produto`) |
| F5 | Qual chave liga uma devolução fiscal à venda original e à plataforma? A devolução estorna o CMV? | `protheus_devolucoes.identificador_unico_venda` casou 0 de 29.771 linhas; a tabela traz `custo` negativo |
| F6 | A remessa ao Full (ML/Shopee) fica fora de `receita_produto` e de `custo`? | `receita_remessa_fulfillment` ML equivale a 68% da receita fiscal ML de jul; se o custo da remessa entrar, o CMV conta duas vezes |
| F7 | A razão 0,95–1,05 entre receita fiscal e GMV é tolerância aceitável para usar o CMV fiscal na DRE? Aplicar CMV em valor absoluto ou como % sobre o GMV? | TikTok Kokeshi set = 0,957; ML Lescent ago = 1,040 |
| F8 | `raw.webgex_impostos_gobeaute` (circuito VX, débito) é a fonte oficial de imposto sobre venda? Por que PIS/COFINS variam tanto por plataforma? Inclui DIFAL/ST? | ICMS+PIS+COFINS = 14,6% (ML), 11,8% (TikTok), 11,6% (Shopee) da receita fiscal em ago |

## 12. Perguntas para o responsável por Conciliação/Repasse

| # | Pergunta | Por que, com o número |
|---|---|---|
| C1 | Quais canais, meses e marcas já estão conciliados, e em que grão (pedido, repasse, NF, período)? | Define o que pode sair como "reconciliado" |
| C2 | No TikTok, a taxa oficial é o `fee_and_tax` do repasse por coorte de pedido? Com quantos dias um mês fica final? | Mart −20,6% (ago) e −61,3% (set) vs silver; jun/jul batem |
| C3 | Comissão de agência e "GMV Max Ads" são descontadas no repasse ou cobradas à parte? E na Shopee, a comissão de afiliado está dentro da "taxa de comissão líquida"? | Afiliado TikTok provadamente dentro do repasse (§6); os outros dois não medidos |
| C4 | Na Shopee, qual é a fonte do repasse real? Comissão + taxa de serviço do export é a taxa conciliada? "Valor estimado do frete" é custo do vendedor? | `total_settlement` do mart = 94–101% do GMV no total mensal (é o "Total global" do comprador, não repasse); taxas = 21,7–25,9% do GMV; frete = 20,9% |
| C5 | No ML, `sale_fee × quantity` em pedidos pagos bate com o repasse? Como entram estornos e `partially_refunded`? | Comissão ML = 14,8% do GMV em jul; a fonte mudou +1,26% em set depois da carga |
| C6 | A conciliação tem devolução/reembolso **em valor** por canal e marca? | L3 ausente nos três canais |
| C7 | Qual tolerância e materialidade a conciliação usa para dar uma divergência como aceita? | Para fixar a regra da §8 |
| C8 | Ads do ML e da Shopee são descontados do saldo/repasse ou pagos por fora? | Se saem do repasse e também somamos `ad_spend`, há dupla contagem análoga à do afiliado |

---

## 13. MVP recomendado

**[RECOMENDAÇÃO]**

1. **Escopo:** DRE mensal, mês × canal × marca, meses **jul/2026 em diante** que estejam maduros (M+2).
2. **Linhas:** L1, L2 (informativa), L5, ↳ afiliados (não somável), L6, L8, L9, L10 "Margem conhecida",
   e L3/L11 como linhas visíveis com estado "ausente".
3. **Canais:** ML e TikTok com margem conhecida; Shopee com L1, L5, L6 e "margem indisponível — CMV não
   reconcilia".
4. **Fontes:** L5 TikTok e L8 TikTok pela silver de pagamentos; CMV fiscal por plataforma; o resto do mart.
5. **Camada:** **[FATO]** CMV e repasse TikTok vivem só no Data Mart; a Torre serve do Neon e roteia por string
   de SQL. **[RECOMENDAÇÃO]** Um publisher mensal grava uma fato nova (ex.: `marts.fact_dre_mensal_canal_marca`)
   com um valor e um `status` por componente, mais a versão fiscal lida — leitura simples na API, sem misturar
   bancos no endpoint.
6. **Guardrails:** testes da §6.3; abortar publicação se a razão de §4.9 sair da tolerância; nunca `COALESCE`
   em custo.
7. **Fora do MVP:** margem por produto/anúncio, impostos, devoluções, Ads TikTok, Shopee CMV.

## 14. Bloqueios

| # | Bloqueio | Destrava com |
|---|---|---|
| B1 | **[BLOQUEIO]** Impostos não ratificados → nenhuma célula "completa" | F8 |
| B2 | **[BLOQUEIO]** CMV Shopee não reconcilia → margem Shopee indisponível | F1 |
| B3 | **[BLOQUEIO]** Devoluções em valor ausentes nos três canais | C6, F5 |
| B4 | **[BLOQUEIO]** Taxa TikTok imatura/congelada no mart para ago/set | trocar a fonte para a silver ou corrigir a recarga; C2 para a regra de maturidade |
| B5 | **[BLOQUEIO]** Sem contraprova independente para taxa, Ads e frete da Shopee | C4 |
| B6 | **[BLOQUEIO]** Ads TikTok ausente | fonte de Ads TikTok (Marketing/Dados) |

---

## 15. Contraprovas das somas

| Soma | Contraprova | Diferença (soma ÷ contraprova − 1) | Independente? |
|---|---|---:|---|
| GMV TikTok jul (marts) | receita fiscal TikTok jul | +2,1% | sim (fiscal) |
| GMV ML jul (marts) | receita fiscal ML jul | −0,5% | sim |
| GMV ML jul (marts) | `gold.ml_gestao_diaria` | +0,3% | não (mesma linhagem) |
| Taxa ML jul (marts) | `sale_fee × qty` recalculado da fonte | < R$ 1 (arredondamento) | sim (recomputação) |
| Taxa ML set (marts) | idem | +1,26% | sim |
| Taxa TikTok jul (marts) | `fee_and_tax` silver por coorte | +0,15% | sim |
| Taxa TikTok ago (marts) | idem | −20,6% | sim |
| Afiliado TikTok jul (fato) | soma silver | < R$ 1 por marca × componente | sim |
| CMV ML jul (`plataforma_sku_mensal`) | `nf_vendas_unificada_v2` | 0 | não (tabela-mãe) |
| CMV TikTok jul | `nf_vendas_unificada_v2` | < R$ 1 (arredondamento) | não (tabela-mãe) |
| Receita fiscal Shopee jul | `nf_vendas_unificada_v2` | −0,8% | não — e diverge |
| Taxa/Ads/frete Shopee | — | — | **sem contraprova** (B5) |
| Percentuais dos componentes da §9 | recálculo em script separado | 0 | sim (aritmética) |

**[FATO]** O CMV não tem contraprova independente da gold fiscal; a única checagem externa é a razão contra o
GMV da Torre (§4.9).

---

## 16. Reprodução

Consultas agregadas, sem credenciais nem dado pessoal, sempre em sessão read-only.

### 16.1 Componentes servidos — Neon

```sql
SELECT m.nome_marketplace AS canal, l.brand_key AS marca, date_trunc('month', f.date)::date AS mes,
       round(sum(f.gmv)) AS gmv, round(sum(f.total_fees)) AS fees, count(f.total_fees) AS n_fees,
       round(sum(f.ad_spend)) AS ads, count(f.ad_spend) AS n_ads,
       round(sum(f.seller_shipping_cost)) AS frete, round(sum(f.total_settlement)) AS settl, count(*) AS dias
FROM marts.fact_marketplace_daily_performance f
JOIN marts.dim_marketplace m USING (marketplace_id)
JOIN marts.dim_loja l USING (loja_id)
WHERE f.date BETWEEN '2026-06-01' AND '2026-09-30'
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
```

### 16.2 CMV e reconciliação — Data Mart

```sql
SELECT plataforma, marca, data_mes,
       round(sum(receita_produto)) AS receita, round(sum(custo)) AS custo,
       round(100.0 * sum(receita_com_custo) / nullif(sum(receita_produto), 0), 2) AS pct_rec_com_custo,
       round(sum(custo_provisorio)) AS custo_prov
FROM gold.nf_vendas_plataforma_sku_mensal
WHERE data_mes BETWEEN '2026-06-01' AND '2026-09-01' AND canal = 'marketplace'
  AND plataforma IN ('tiktokshop', 'mercadolivre', 'shopee')
  AND marca IN ('apice', 'barbours', 'kokeshi', 'lescent', 'rituaria')
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
```

### 16.3 Prova de inclusão do afiliado — Data Mart

```sql
WITH t AS (
  SELECT date_trunc('month', order_create_time)::date AS mes,
         revenue_amount AS rev, fee_and_tax_amount AS ft,
         shipping_cost_amount AS sh, settlement_amount AS st,
         coalesce(affiliate_commission_amount, 0) + coalesce(affiliate_partner_commission_amount, 0)
           + coalesce(affiliate_ads_commission_amount, 0) AS af
  FROM silver.stg_tiktok_payments_by_order
  WHERE transaction_type = 'ORDER'
    AND order_create_time >= '2026-06-01' AND order_create_time < '2026-10-01'
    AND brand IN ('apice', 'barbours', 'kokeshi', 'lescent', 'rituaria')
)
SELECT mes, (af <> 0) AS tem_afiliado, count(*) AS tx,
       count(*) FILTER (WHERE abs(st - (rev + ft + sh)) > 0.01) AS tx_repasse_nao_fecha,
       count(*) FILTER (WHERE af <> 0 AND af < ft - 0.01)      AS tx_afiliado_maior_que_taxa,
       round(100.0 * sum(-ft) / nullif(sum(rev), 0), 2)        AS taxa_pct,
       round(100.0 * sum(-(ft - af)) / nullif(sum(rev), 0), 2) AS taxa_sem_afiliado_pct
FROM t GROUP BY 1, 2 ORDER BY 1, 2;
```

### 16.4 Taxa TikTok por coorte — contraprova do mart — Data Mart

```sql
SELECT brand, date_trunc('month', order_create_time)::date AS mes,
       round(sum(fee_and_tax_amount)) AS fee_and_tax,
       round(sum(shipping_cost_amount)) AS frete,
       round(sum(coalesce(affiliate_commission_amount, 0)))         AS af_creator,
       round(sum(coalesce(affiliate_partner_commission_amount, 0))) AS af_partner,
       round(sum(coalesce(affiliate_ads_commission_amount, 0)))     AS af_ads
FROM silver.stg_tiktok_payments_by_order
WHERE transaction_type = 'ORDER'
  AND order_create_time >= '2026-06-01' AND order_create_time < '2026-10-01'
  AND brand IN ('apice', 'barbours', 'kokeshi', 'lescent', 'rituaria')
GROUP BY 1, 2 ORDER BY 2, 1;
```

### 16.5 Comissão ML recalculada — Data Mart

```sql
SELECT o.brand, date_trunc('month', o.date_created)::date AS mes,
       round(sum(li.sale_fee * li.quantity)) AS comissao
FROM api.ml_orders o
JOIN api.ml_order_line_items li ON li.order_id = o.order_id   -- nunca por ml_orders.id
WHERE o.status = 'paid'
  AND o.date_created >= '2026-07-01' AND o.date_created < '2026-10-01'
  AND o.brand IN ('barbours', 'kokeshi', 'lescent', 'rituaria')
GROUP BY 1, 2 ORDER BY 2, 1;
```

### 16.6 Impostos por plataforma — Data Mart

```sql
WITH nota AS (
  SELECT chave_nfe, min(plataforma) AS plataforma, count(DISTINCT plataforma) AS n_plat
  FROM gold.nf_vendas_unificada_v2
  WHERE data_mes = '2026-08-01' AND chave_nfe IS NOT NULL
    AND marca IN ('apice', 'barbours', 'kokeshi', 'lescent', 'rituaria')
  GROUP BY 1
), imp AS (
  SELECT chave_nota, codigo_imposto, sum(valor_imposto) AS imposto
  FROM raw.webgex_impostos_gobeaute
  WHERE data >= '2026-08-01' AND data < '2026-09-01'
    AND circuito = 'VX' AND debito_credito = 'DB'
  GROUP BY 1, 2
)
SELECT coalesce(n.plataforma, '(sem casamento)') AS plataforma, i.codigo_imposto,
       count(*) AS notas, round(sum(i.imposto)) AS imposto,
       count(*) FILTER (WHERE n.n_plat > 1) AS notas_multi_plataforma
FROM imp i LEFT JOIN nota n ON n.chave_nfe = i.chave_nota
GROUP BY 1, 2 ORDER BY 1, 2;
```

---

## 17. Limitações desta auditoria

1. **[FATO]** `raw.vw_dre_mensal` (DRE TikTok-only já existente) estourou o tempo limite de 180 s e não foi
   avaliada; é referência útil para C3.
2. **[FATO]** A cobertura de impostos por nota de marketplace não foi medida, só o casamento em ago/2026.
3. **[FATO]** A Shopee não tem contraprova independente para nenhum componente além da receita fiscal.
4. **[INFERÊNCIA]** A causa do congelamento da taxa TikTok no mart (lookback de 10 dias sobre fonte que
   amadurece depois) não foi provada; o efeito foi.
5. **[FATO]** A fonte fiscal mudou entre o MARGEM-REAL-1 e este gate; os números da §4.9 valem para a leitura de
   09/10/2026.
