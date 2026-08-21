# Gate UE1 — Contratos e aquisição das fontes de Unit Economics

Data: 2026-08-21 · **Revisão 3** (UE1-C — competência do custo TikTok)
Tipo: auditoria **read-only** de descoberta e desenho. Nenhum código, schema, pipeline ou banco foi alterado.
Base Git: `7b1b4512e54fa7886f9bf098ee25d47509175580` (`origin/main`), worktree `unit-economics-audit`
Antecedentes: `docs/UNIT_ECONOMICS_ATTRIBUTION_AUDIT.md` (commit `8125bb8`), backend `417be72`, frontend `6a5c957`

## Convenção de marcações

- **[FATO]** — verificado por consulta read-only, catálogo ou código versionado. Reproduzível.
- **[INFERÊNCIA]** — apoiado em evidência, não provado. Diz o que falta para virar fato.
- **[RECOMENDAÇÃO]** — juízo de engenharia desta auditoria.
- **[BLOQUEIO]** — impede a entrega; diz o que destrava.
- **[DECISÃO NECESSÁRIA]** — pertence ao proprietário/stakeholder.

## O que mudou na revisão 2

A revisão externa apontou quatro fragilidades. Todas se confirmaram, e **duas mudaram o veredito**.

| # | Revisão 1 dizia | Revisão 2 | Efeito |
|---|---|---|---|
| 1 | `gold.tiktok_brand_daily.total_fees` é reconciliação **independente**, e o fechamento de 0,015% **prova aditividade econômica** | `gold.tiktok_brand_daily` é **tabela**, não view: sua derivação **não é demonstrável**. Silver e Gold são a mesma linhagem. É **consistência aritmética**, não independência | Redação corrigida em todo o documento |
| 2 | Grão de `stg_tiktok_payments_by_order` era "candidato natural" | **CONFIRMADO**: `transaction_id` é único em 2.122.887 linhas, zero nulos (§4.2) | Grão deixa de ser hipótese |
| 3 | Reconciliação demonstrada em jun/2026 | **Estendida a 7 meses × 5 marcas = 35 células. 28 delas divergem mais de 1%** (§4.3). O 0,015% de junho **não era representativo** | UE2-B foi a BLOCKED na revisão 2 e **volta a READY COM RESTRIÇÃO na revisão 3** (§18), por causa diferente da suposta |
| 4 | "Nenhum filtro de `transaction_type` é necessário" | Um único tipo hoje **não autoriza** aceitar tipos novos em silêncio (§4.6) | Contrato passa a exigir allowlist com falha explícita |
| 5 | CMV: "basta aplicar o pipeline existente" ao marketplace | Reduzido a **hipótese forte não provada** — falta prova de join, cardinalidade e universo (§7) | Certeza reduzida |

---

## 1. Resumo executivo

### 1.1 Veredito

**Veredito revisado no UE1-C (§18): o comparador estava errado, não o custo.** A reconciliação estendida encontrou divergência material entre os sete componentes da Silver e o agregado da Gold: **28 de 35 células mês × marca divergem mais de 1%**, com pior caso de −3,64% em mês completo e maior diferença absoluta de R$ 61.138,12.

**O UE1-C (§18) demonstrou por que a comparação é inválida**, não a magnitude da divergência: a Gold é agregado do subsistema de _statements_ e a reconstrução é uma coorte de _pedido_ — universos distintos por construção, com **24,6% das linhas/transações** (não do valor) cruzando a fronteira mensal entre pedido e statement.

A Gold **não é comparador válido**, e a magnitude exata das divergências **permanece não reconciliável** enquanto a semântica de `gold.date` for desconhecida. O custo por coorte de pedido é auditável **contra a própria fonte transacional**, e por isso passa a **READY COM RESTRIÇÃO** — por auditabilidade interna, nunca por reconciliação externa.

### 1.2 As três correções ao UE0 que permanecem válidas

| # | UE0 afirmava | Evidência | Estado na revisão 2 |
|---|---|---|---|
| 1 | Taxa TikTok divergia ~5x, "não reconciliada" | O UE0 comparou `total_fees` contra 1 de 7 componentes. Somando os 7, a ordem de grandeza fecha | **Mantida** — mas o fechamento é aritmético e imperfeito (§4.3), não "reconciliação independente" |
| 2 | Sem CMV histórico; criar `dim_product_cost_history` | Existe cadeia funcional com 8 estratégias, incluindo `protheus_sku_mes` (SKU × mês), cobrindo 99,95% de 10,5 mi de linhas de ecommerce | **Mantida, com certeza reduzida** (§7) |
| 3 | PDF poderia ter as taxas contratuais | Lido: é política de preço mínimo. Zero menção a comissão/taxa/devolução/imposto | **Mantida** — item fechado |

### 1.3 Veredito por pedido

| Pedido | Veredito | Bloqueio real |
|---|---|---|
| **1. Custo de afiliados** | **READY COM RESTRIÇÃO no TikTok** (coorte de pedido — ver §18.6) · **BLOQUEADO em ML e Shopee** | Comparabilidade com a Gold é impossível por construção; auditabilidade interna é suficiente (§18.5) |
| **1. Retorno de afiliados** | **BLOQUEADO nos três canais** | Sem receita atribuída; tabela contratada vazia |
| **2. Share por origem (ML e Shopee)** | **BLOQUEADO nos dois** | Sem campo de origem. `ad_revenue` excede o GMV em 34% dos dias na Shopee |
| **3. Margem por anúncio** | **PARCIAL em ML** · **PARCIAL-FRACO na Shopee** · **BLOQUEADO no TikTok** | CMV de marketplace não enriquecido; taxa do ML sem competência |

### 1.4 Menor próximo gate implementável

**[RECOMENDAÇÃO]** Após o UE1-C, o menor gate implementável é **UE2-B — fato de custo de afiliado por coorte de pedido** (§18.6), com as restrições da §18.5. Não depende de terceiros nem da Gold.

Em paralelo, **UE1-B — aquisição** (§12) pode correr, porque suas respostas vêm de fora.

---

## 2. Escopo e metodologia

**[FATO]** Toda consulta foi read-only. Zero DDL, DML, migration, pipeline, sync ou deploy. Nenhum DSN, token, order id, transaction id, nome de comprador, e-mail, telefone, endereço ou handle de creator foi impresso.

| Caminho | Estado | Cobertura |
|---|---|---|
| Neon (`marts.*`) | Disponível, `transaction_read_only=on` | Camada servida hoje |
| Data Mart via VPN | **INDISPONÍVEL** — timeout, duas tentativas | — |
| Data Mart via proxy governado | Disponível | `api`, `silver`, `gold`. **`raw` não é exposto** |
| Backend publicado | Disponível | Contrato servido |
| PDF local | Lido por decodificação de CMap ToUnicode | — |
| Documentação oficial de API | **Inacessível** (§17) | — |

**[FATO] O proxy governado tem orçamento de custo e rejeita consultas pesadas.** Seis consultas foram rejeitadas com *"Query execution backend is unavailable"*: sete `UNION ALL` sobre 2,1 mi de linhas; doze agregados condicionais com extração `jsonb`; lotes de 4 e de 2 meses em competências de alto volume; e um `count(DISTINCT)` por marca. Cada rejeição foi respondida com redução de escopo, nunca com repetição. Um `SELECT 1` imediatamente após confirmou que se tratava de rejeição por custo, não de indisponibilidade.

⚠️ **[RECOMENDAÇÃO]** O proxy é ferramenta de **auditoria**, não de runtime. Nenhum pipeline futuro deve depender dele (§6).

---

## 3. Fontes consultadas

| Fonte | Tipo | Grão | Linhas | Uso |
|---|---|---|---|---|
| `silver.stg_tiktok_payments_by_order` | **tabela** | transação | 2.122.887 | Custo de afiliado e taxas |
| `api.tiktok_payments_by_order` | **view** sobre a Silver | idem | 2.122.887 | Paridade 1:1 |
| `api.tiktok_affiliate_marketplace_creators` | tabela | marca × creator × data | **0** | Retorno de afiliado — vazia |
| `api.tiktok_shop_settlements` | tabela | repasse | **0** | Vazia |
| `gold.tiktok_brand_daily` | **tabela** | dia × marca | — | Agregado comparado |
| `gold.nf_vendas_unificada_v2` | tabela | item de nota | 15.260.493 | CMV por canal |
| `gold.vendas_consolidada_produto_v2` | tabela | mês × marca × canal × SKU | 20.708 | CMV agregado |
| `silver.bling_produtos` | tabela | produto (snapshot) | 2.319 | Custo atual |
| `silver.stg_ml_orders` | tabela | pedido | 493.981 | Origem de venda ML |
| `silver.stg_shopee_order_item_snapshots` | tabela | item × pedido | 763.600 | Origem, taxas, descontos |
| `silver.stg_shopee_ads` | tabela | anúncio × período | 3.025 | Ads por anúncio |
| `gold.ml_produto_pnl` | tabela | produto (lifetime) | 1.769 | Taxa por item ML |
| `gold.ml_campaign_diaria` | tabela | dia × campanha | 48.749 | Ads por campanha |
| `marts.fact_ml_produto_ranking` | tabela | marca × item | 1.650 | Grão "por anúncio" ML |
| `marts.fact_shopee_product_monthly` | tabela | mês × marca × SKU | 3.631 | Grão de produto Shopee |
| `marts.fact_tiktok_channel_efficiency_daily` | tabela | dia × marca × canal | 4.788 | Taxonomia de canal |
| `docs/octaprice/Regua_Cobranca_...pdf` | — | — | 23.983 chars | §16.3 |

---

## 4. Afiliados — TikTok

### 4.1 Natureza da comparação Silver × Gold — correção da revisão 1

**[FATO]** `gold.tiktok_brand_daily` é uma **tabela** (`relkind = 'r'`), não uma view. Não existe `pg_get_viewdef` a obter, e sua transformação **não está versionada em nenhum repositório nosso** (achado do UE0, mantido). **A derivação de `total_fees` não é demonstrável.**

**[FATO]** `silver.stg_tiktok_payments_by_order` e `gold.tiktok_brand_daily` pertencem à **mesma linhagem** — mesma ingestão, mesmo time, mesmo warehouse. `api.tiktok_payments_by_order` é uma view sobre a Silver, com contagem idêntica (2.122.887).

**Contrato de leitura, corrigido:**

- A comparação entre a soma dos sete componentes e `total_fees` é uma **reconciliação interna entre componentes e agregado da mesma linhagem**.
- Ela mede se a transformação da Gold é **aritmeticamente consistente** com a seleção das sete chaves.
- Ela **não é validação contra fonte externa**.
- Ela **não prova, isoladamente, independência econômica** entre comissão de criador, de parceiro e de Ads de afiliado.
- A duplicidade entre `affiliate_commission_amount` e `affiliate_commission_amount_before_pit` é demonstrada por **outra** evidência: a quase-igualdade linha a linha e o significado *before/after PIT* (§4.5) — não pela reconciliação.
- **Qual conjunto de componentes representa "custo de afiliados" permanece a decisão P2** (§13).

**[RECOMENDAÇÃO]** Não publicar `affiliate_cost_total` antes de P2. Os três componentes permanecem **separados** no futuro fato.

### 4.2 Grão e chave — **CONFIRMADO**

**[FATO]** Medido sobre a tabela inteira:

| Verificação | Resultado |
|---|---|
| Linhas | 2.122.887 |
| `transaction_id` distintos | **2.122.887 → ÚNICO** |
| `order_create_time` | 2025-06-04 a 2026-08-19 |
| Nulos em `transaction_id` | **0** |
| Nulos em `order_create_time` | **0** |
| Nulos em `brand` | **0** |
| Nulos em `fee_breakdown` | **0** |
| Nulos em `order_id` | **0** |
| Nulos em `statement_id` | **0** |
| Moeda diferente de BRL | **0** |
| `order_id` distintos | 2.086.778 |
| Marcas distintas | **8** |

**Grão confirmado: uma linha por `transaction_id`.** É chave única e completa — serve como PK.

**[FATO] `order_id` NÃO é único**: 2.122.887 − 2.086.778 = **36.109 linhas excedentes**, ou seja ~1,7% dos pedidos têm mais de uma transação. Medido em jun/2026: 1.312 de 190.168 pedidos (0,69%), com **máximo de 4 transações por pedido**.

**[FATO] Sem fan-out de marca nem de competência.** Em jun/2026: **0** pedidos com mais de um `order_create_time`, **0** pedidos atravessando mês, **0** pedidos em mais de uma marca. `order_create_time` é atributo do pedido, constante entre suas transações.

**[FATO] Todas as 28 colunas são nullable** — não há `NOT NULL` na origem. Os zeros acima são medição, não garantia de schema.

**[FATO] Oito marcas, três fora do escopo da Torre:**

| Marca | Linhas | Escopo |
|---|---:|---|
| barbours | 1.326.326 | allowlist |
| kokeshi | 532.315 | allowlist |
| apice | 88.759 | allowlist |
| **gocase** | **78.649** | **fora** |
| lescent | 40.666 | allowlist |
| rituaria | 26.770 | allowlist |
| **azbuy** | **24.662** | **fora** |
| **denavita** | **4.740** | **fora** |

108.051 linhas (5,1%) fora da allowlist. Qualquer fato futuro **deve** aplicar `BRANDS_IN_SCOPE`, como já fazem as migrations 007/008.

### 4.3 Reconciliação por competência — **DIVERGÊNCIA MATERIAL**

**[FATO]** `gold.tiktok_brand_daily` cobre **11 competências** (2025-10 a 2026-08), enquanto a Silver começa em 2025-06. Para 2025-06 a 2025-09 **não há contrapartida na Gold** — quatro competências não reconciliáveis.

**[FATO]** Medi 7 competências × 5 marcas = **35 células**. Quatro competências (mai, jun, jul, ago/2026) não foram medidas por marca por rejeição de custo do proxy.

`silver_7` = soma de `platform_commission_amount` + `sfp_service_fee_amount` + `fee_per_item_sold_amount` + `affiliate_commission_amount_before_pit` + `affiliate_partner_commission_amount` + `affiliate_ads_commission_amount` + `gmv_max_ad_fee_amount`.

| Competência | Marca | `silver_7` | `gold.total_fees` | Δ absoluto | Δ relativo |
|---|---|---:|---:|---:|---:|
| 2025-10 | apice | −27.576,49 | −22.408,19 | −5.168,30 | **+23,06%** |
| 2025-10 | barbours | −624.299,75 | −584.252,46 | −40.047,29 | **+6,85%** |
| 2025-10 | kokeshi | −256.625,28 | −219.795,06 | −36.830,22 | **+16,76%** |
| 2025-10 | lescent | −4.011,38 | −3.454,23 | −557,15 | **+16,13%** |
| 2025-10 | rituaria | −12.929,81 | −11.312,44 | −1.617,37 | **+14,30%** |
| 2025-11 | apice | −59.015,54 | −60.490,45 | +1.474,91 | −2,44% |
| 2025-11 | barbours | −801.569,52 | −813.125,88 | +11.556,36 | −1,42% |
| 2025-11 | kokeshi | −227.787,89 | −230.956,20 | +3.168,31 | −1,37% |
| 2025-11 | lescent | −11.891,09 | −12.238,12 | +347,03 | −2,84% |
| 2025-11 | rituaria | −22.156,24 | −22.697,14 | +540,90 | −2,38% |
| 2025-12 | apice | −73.316,12 | −75.231,37 | +1.915,25 | −2,55% |
| 2025-12 | barbours | −717.340,22 | −725.847,13 | +8.506,91 | −1,17% |
| 2025-12 | kokeshi | −188.797,29 | −192.024,05 | +3.226,76 | −1,68% |
| 2025-12 | lescent | −7.488,41 | −7.771,04 | +282,63 | **−3,64%** |
| 2025-12 | rituaria | −14.914,63 | −15.115,77 | +201,14 | −1,33% |
| 2026-01 | apice | −64.511,67 | −65.320,17 | +808,50 | −1,24% |
| 2026-01 | barbours | −1.644.572,93 | −1.653.384,87 | +8.811,94 | −0,53% |
| 2026-01 | kokeshi | −347.862,28 | −351.388,67 | +3.526,39 | −1,00% |
| 2026-01 | lescent | −10.487,17 | −10.746,75 | +259,58 | −2,41% |
| 2026-01 | rituaria | −19.119,16 | −19.324,74 | +205,58 | −1,06% |
| 2026-02 | apice | −57.077,88 | −58.445,49 | +1.367,61 | −2,34% |
| 2026-02 | barbours | −3.273.605,03 | −3.284.339,64 | +10.734,61 | −0,33% |
| 2026-02 | kokeshi | −324.016,24 | −326.386,10 | +2.369,86 | −0,73% |
| 2026-02 | lescent | −12.954,54 | −13.206,42 | +251,88 | −1,91% |
| 2026-02 | **rituaria** | **−19.582,06** | **−19.582,06** | **0,00** | **0,00%** |
| 2026-03 | apice | −111.188,94 | −114.030,69 | +2.841,75 | −2,49% |
| 2026-03 | barbours | −3.546.184,72 | −3.557.263,86 | +11.079,14 | −0,31% |
| 2026-03 | kokeshi | −713.810,10 | −723.847,82 | +10.037,72 | −1,39% |
| 2026-03 | lescent | −79.759,40 | −80.975,28 | +1.215,88 | −1,50% |
| 2026-03 | **rituaria** | **−20.148,88** | **−20.148,88** | **0,00** | **0,00%** |
| 2026-04 | apice | −165.222,26 | −169.933,36 | +4.711,10 | −2,77% |
| 2026-04 | barbours | −2.782.551,91 | −2.843.690,03 | **+61.138,12** | −2,15% |
| 2026-04 | kokeshi | −701.898,55 | −716.920,49 | +15.021,94 | −2,10% |
| 2026-04 | lescent | −84.576,15 | −86.037,51 | +1.461,36 | −1,70% |
| 2026-04 | **rituaria** | **−38.665,48** | **−38.665,48** | **0,00** | **0,00%** |

**[FATO] Estatísticas das 35 células:**

| Medida | Valor |
|---|---|
| Fecham exatamente | **3** (rituaria em fev, mar, abr/2026) |
| Dentro de 0,1% | **3** (as mesmas) |
| **Acima de 1%** | **28 de 35 (80%)** |
| Pior relativo, geral | 2025-10 apice, **+23,06%** |
| Pior relativo, mês completo | 2025-12 lescent, **−3,64%** |
| Maior absoluto | 2026-04 barbours, **R$ 61.138,12** |

**[FATO] Nenhuma tolerância foi congelada antes de observar os dados.** O limiar de 0,1% sugerido na revisão 1 seria satisfeito por **3 de 35 células** — não serve como critério.

**[FATO] O 0,015% de jun/2026 não era representativo.** Foi medido no **total agregado das 5 marcas**, onde divergências de sinal oposto se compensam, e num mês que não foi reaberto por marca. A revisão 1 generalizou de uma única observação agregada.

### 4.4 Caracterização da divergência

Duas assinaturas distintas:

**[FATO] Assinatura A — out/2025, divergência positiva grande (+6,85% a +23,06%).** A Gold tem **25 a 27 dias** por marca em out/2025, contra 30–31 nos meses seguintes. É o mês de início da Gold, **parcial**. A Silver traz o mês inteiro. **Explicação suficiente**; não é defeito.

**[FATO] Assinatura B — nov/2025 a abr/2026, divergência negativa sistemática (−0,31% a −3,64%).** A Gold reporta **mais** taxa que a soma dos sete componentes, de forma consistente, em todas as marcas e meses completos.

**Estado após o UE1-C:** a *invalidade da comparação* está demonstrada (§18.3); a *magnitude* desta assinatura **permanece não reconciliável** e deixou de ser pré-requisito, porque a Gold não é comparador válido para a métrica de coorte.

**[INFERÊNCIA]** A pista mais forte é que **rituaria — a marca de menor volume — fecha exatamente em três meses consecutivos**, enquanto marcas de volume alto divergem sempre. Um erro de seleção de chave produziria divergência proporcional em todas as marcas, inclusive rituaria. Um erro de **atribuição de data** produziria exatamente o observado: pedidos próximos da fronteira do mês migram entre competências, e o efeito é nulo quando há poucos pedidos na fronteira.

**Hipótese testada no UE1-C:** que a coluna `date` seguisse a data de statement. **REFUTADA** — agrupar por `statement_month` afasta ainda mais (§18.4). A competência da Gold segue **NÃO DEMONSTRÁVEL**, e por isso ela é comparador inválido, não referência a perseguir.

⚠️ **Corrigido no UE1-C (§18).** Esta conclusão confundia *comparabilidade externa* com *auditabilidade interna*. A Assinatura B decorre de a Gold ser um agregado do subsistema de **statements**, enquanto a reconstrução é uma **coorte de pedido** — universos diferentes por construção, com 24,6% das linhas liquidadas em mês distinto do pedido (§18.4). A Gold **não é comparador válido**; isso não impede auditar o custo por coorte contra a própria fonte transacional.

### 4.5 Duplicidade semântica — mantida, com base corrigida

**[FATO]** `fee_breakdown` tem **53 chaves**, das quais **sete** são de afiliado.

**[FATO]** `affiliate_commission_amount` e `affiliate_commission_amount_before_pit` são o **mesmo evento econômico**, demonstrado por dois caminhos independentes da reconciliação:

1. **Quase-igualdade linha a linha:** em 191.525 linhas de jun/2026, **iguais em 191.258 (99,86%)**, diferentes em 267 (0,14%), diferença total de R$ 3.454,06.
2. **Significado dos nomes:** *before PIT* versus o valor sem qualificador — bruto e líquido da retenção de imposto sobre o mesmo pagamento.

**Somar as duas contaria a comissão de criador duas vezes.** O DRE usa `before_pit`; a chave quase-gêmea permanece armadilha ativa para qualquer consumidor futuro.

**[FATO] Valores por componente, jun/2026, 5 marcas da Torre:**

| Componente | Valor |
|---|---:|
| Comissão de criadores (`before_pit`) | −547.976,92 |
| Comissão de parceiro afiliado | −285.908,53 |
| Comissão de Ads de afiliado | −74.762,91 |

**[INFERÊNCIA]** `affiliate_partner_commission_amount` é 31,5% do total. O nome indica *affiliate partner*, que na taxonomia do TikTok designa MCN/parceiro gestor de criadores. **Falta:** confirmação com a plataforma ou com quem contratou.

### 4.6 Sinal, reversões e campos zerados

**[FATO]** Sobre a **história inteira** (2.122.887 linhas):

| Campo | Valores positivos |
|---|---:|
| `affiliate_commission_amount_before_pit` | **0** |
| `affiliate_partner_commission_amount` | **0** |
| `affiliate_ads_commission_amount` | **0** |

**[FATO]** `affiliate_commission_deposit`, `affiliate_commission_release` e `external_affiliate_marketing_fee_amount` somam **zero** em jun/2026. Não foram medidos na história completa — a consulta de perfil por sinal para os sete campos foi rejeitada por custo.

**[RECOMENDAÇÃO] Não propor `CHECK (<= 0)`.** Zero positivos em 14 meses é evidência de que o campo é sempre débito, mas **não prova** que uma reversão futura não chegaria como positivo. A semântica de estorno nestes campos **não está confirmada**: pode ser representada por transação separada, por ajuste em `adjustment_amount`, ou não existir. Um `CHECK` rejeitaria carga legítima.

**[RECOMENDAÇÃO] Contrato de sinal para o futuro fato:**

- **Armazenar o valor assinado da origem** (negativo = débito), preservando a semântica contábil.
- **Nunca aplicar `abs()`.** Negativo é débito e pode ser exibido como magnitude positiva via `-valor`; **positivo é crédito/reversão** e `abs()` o faria parecer custo, destruindo o sinal econômico (§18.5.1).
- `NULL` significa **chave ausente no `fee_breakdown`**; `0` significa **medido como zero**. Nunca converter um no outro.
- Sem `CHECK` de sinal. Com `CHECK (<> 'NaN')` explícito nas colunas `numeric`, porque `'NaN'::numeric` passa por comparações de ordem no Postgres (lição das migrations 007/008).
- Monitorar `deposit`, `release` e `external_affiliate_marketing_fee`: se deixarem de ser zero, **falhar o contrato** e exigir revisão, não incluí-los automaticamente.

### 4.7 `transaction_type` e competência

**[FATO]** `transaction_type` tem **um único valor — `ORDER`** — nas 2.122.887 linhas, cobrindo 2025-06-04 a 2026-08-19. **Zero nulos** (a contagem do grupo único iguala o total de linhas).

**[RECOMENDAÇÃO] Contrato futuro — allowlist, não ausência de filtro.** Corrige a revisão 1, que concluía "nenhum filtro é necessário":

- Processar **somente** tipos explicitamente allowlisted.
- `ORDER` é o único tipo conhecido hoje.
- Tipo novo **não pode** entrar silenciosamente na métrica.
- Tipo desconhecido **gera falha de contrato e alerta**, e exige revisão humana — mesmo padrão do `ELIGIBLE_ORDER_STATUSES` do conector TikTok, que já trata status desconhecido como warning fora do GMV.

**[FATO] Competência.** `order_create_time` é `timestamp **without** time zone`. Não há offset armazenado.

**[FATO]** Todas as transações de um mesmo pedido compartilham o mesmo `order_create_time`: em jun/2026, **0** pedidos com mais de um instante e **0** atravessando mês. A competência mensal é estável por pedido.

**[INFERÊNCIA/LIMITAÇÃO] O timezone de origem não é demonstrável.** A coluna não tem offset; o proxy serializa o valor naive com `-03:00`, o que é artefato de renderização, não dado armazenado. Não invento timezone. **Falta:** documentação da ingestão ou confirmação com o time da Raw. Consequência prática: o comportamento na fronteira do mês depende de uma convenção não declarada, e isso é candidato a causa da Assinatura B (§4.4).

### 4.8 Retorno de afiliado — **[BLOQUEIO]**

**[FATO]** `api.tiktok_affiliate_marketplace_creators` tem **0 linhas**, medido em 19/08 e 21/08. É a única fonte contratada com `commission_amount` por creator e GMV atribuído.

**[FATO]** As tabelas de creator populadas não têm flag de afiliado nem comissão. `creator` ≠ `affiliate`.

**[FATO]** `marts.fact_tiktok_channel_efficiency_daily.channel` tem exatamente três valores — `PRODUCT_CARD`, `LIVE`, `VIDEO`, 1.596 linhas cada, 2025-10-05 a 2026-08-20. Taxonomia de **posicionamento de conteúdo**, sem canal "afiliado" nem "orgânico".

**Não aceito como retorno de afiliado:** GMV total de creators; GMV de vídeo/live sem flag; receita de Ads; comissão ÷ GMV total; resíduo por diferença.

### 4.9 ML e Shopee — **[BLOQUEIO]**

**[FATO]** Nenhuma tabela `ml_*` ou `shopee*` acessível tem coluna de afiliado ou comissão de afiliado. O UE0 varreu o catálogo completo (74 tabelas) com o mesmo resultado.

**[INFERÊNCIA]** Ambas as plataformas operam programas de afiliados no Brasil, mas não há evidência de que sejam expostos por API de seller nem de que participemos. **Falta:** documentação oficial (inacessível, §17) e confirmação comercial (P5).

---

## 5. Origem de vendas por marketplace (Pedido 2)

### 5.1 Regra aplicada

Antes de usar *share*: mutuamente exclusivas, exaustivas sobre o denominador, mesmo grão e período, reconciliáveis com o total, coerentes em cancelamento/devolução.

### 5.2 Mercado Livre

**[FATO]** O único campo candidato, `silver.stg_ml_orders.context_channel`, tem **um único valor — `marketplace`** — nos 493.981 pedidos de 2025-04-27 a 2026-08-21. Não discrimina origem: distingue marketplace de loja própria, e só operamos no primeiro.

**[FATO]** Nenhum campo de Ads, afiliado, live, vídeo ou orgânico no pedido ou item.

**[FATO]** `ad_revenue`, jan–jul/2026: **46,97% do GMV**, com 2 de 847 linhas dia×marca excedendo o GMV.

**[FATO]** `stg_ml_ads_campaigns.channel` e `stg_ml_ads_items.channel` são o canal do **anúncio**, não a origem do pedido.

### 5.3 Shopee

**[FATO]** Zero campo de origem nas 67 colunas do snapshot de item-pedido.

**[FATO]** `ad_revenue`, jan–jul/2026: **73,50% do GMV**, com **364 de 1.060 linhas dia×marca excedendo o GMV (34,3%)**. Uma métrica que supera o denominador em um terço dos dias não é composição de vendas.

**[FATO]** `ad_spend`/`ad_revenue` da Shopee não têm granularidade diária real: o parser soma o período do CSV e divide pelos dias (`pipelines/connectors/shopee/_parser_ads.py`).

### 5.4 Tabela obrigatória

| Canal | Categoria | Fonte | Grão | Exclusiva? | Exaustiva? | Reconciliável? | Pode chamar de share? | Estado |
|---|---|---|---|---|---|---|---|---|
| ML | Ads | `marts.fact...ad_revenue` | dia × loja | **Não** | Não | Não | **Não** | Só "receita atribuída a Ads ÷ GMV" |
| ML | live | — | — | — | — | — | Não | **N/A — conceito inexistente** |
| ML | produto | — | — | — | — | — | Não | **N/D** |
| ML | afiliado | — | — | — | — | — | Não | **BLOQUEADO** |
| ML | orgânico/outros | — | — | — | — | — | **Não** | **PROIBIDO derivar por resíduo** |
| Shopee | Ads | `marts.fact...ad_revenue` | dia × loja (rateado) | **Não** (excede o GMV em 34% dos dias) | Não | Não | **Não** | Só com aviso duplo |
| Shopee | live | — | — | — | — | — | Não | **BLOQUEADO** |
| Shopee | produto | `fact_shopee_product_monthly` | mês × SKU | Sim (por SKU) | Não (não é origem) | Sim | **Não** | Mix de produto, não de origem |
| Shopee | afiliado | — | — | — | — | — | Não | **BLOQUEADO** |
| Shopee | orgânico/outros | — | — | — | — | — | **Não** | **PROIBIDO derivar por resíduo** |

**[RECOMENDAÇÃO]** Se houver gate de UI: um indicador isolado por canal — "Receita atribuída a Ads ÷ GMV" — **sem barra de 100%**, com aviso de janela de atribuição (e, na Shopee, de rateio de período), e **N/D explícito** para live, afiliado e orgânico.

**[DECISÃO NECESSÁRIA]** O pedido 2, como formulado, é insatisfazível com as fontes atuais (P3).

---

## 6. Caminho operacional do futuro fato — visão resumida

⚠️ **A especificação executável canônica está na §18.8.** Esta seção é apenas a visão operacional de alto nível e as evidências de watermark que a sustentam. **Nenhum schema, algoritmo ou critério é definido aqui** — se houver qualquer divergência entre esta seção e a §18.8, **a §18.8 prevalece**.

**[FATO]** O proxy governado usado nesta auditoria **não pode ser dependência de runtime**: tem orçamento de custo que rejeitou 6 das minhas consultas, e é um conector de sessão interativa, não um canal de pipeline.

**[RECOMENDAÇÃO] Fluxo coerente com a arquitetura vigente** (mesmo padrão dos Gates S1–S3):

```
Data Mart / silver.stg_tiktok_payments_by_order   (read-only, via VPN no ambiente operacional)
  → snapshot consistente REPEATABLE READ, allowlist BRANDS_IN_SCOPE          (ver 18.8.3)
  → validacao de transaction_type ANTES do filtro comercial                  (ver 18.8.6)
  → recalculo integral das chaves (ref_month, brand) tocadas                 (ver 18.8.3)
  → staging temporaria transacional no Neon
  → marts.fact_tiktok_affiliate_cost_order_monthly   (substituicao atomica por chave)
  → reconciliacao em tres fronteiras                                         (ver 18.9)
  → watermark persistido somente apos sucesso                                (ver 18.8.7)
  → API (campos aditivos)
  → Canais                                                                   (ver 18.10)
```

### 6.1 O fato — referência ao contrato canônico

**O schema canônico está na §18.8.2.** Não é repetido aqui, para não existirem duas especificações. Em resumo:

| Aspecto | Definição canônica (§18.8.2) |
|---|---|
| Destino | **`marts.fact_tiktok_affiliate_cost_order_monthly`** |
| Grão / PK | `(ref_month, brand)` |
| Competência | mês de `order_create_time` **armazenado** |
| Componentes de negócio | exatamente três: `affiliate_creator_commission`, `affiliate_partner_commission`, `affiliate_ads_commission` |
| Contagem | `source_row_count` |
| Watermark | `source_max_updated_at` — **`timestamp` sem timezone** (§18.8.1) |
| Auditoria | `synced_at`, `source_run_id` |
| Proibido | **`affiliate_cost_total` antes de P2** |

⚠️ **[FATO] Campos que NÃO pertencem a este fato.** A análise histórica de sete componentes contra a Gold (§4.3, §18.3) usava `marketplace_platform_commission`, `marketplace_sfp_service_fee`, `marketplace_fee_per_item` e `gmv_max_ad_fee`, além de contagens de `transactions` e `orders`. Esses campos serviram àquela **análise** — e permanecem documentados nela — mas **não fazem parte do produto de dados de custo de afiliados aprovado**. O fato tem três componentes de afiliado, não sete de taxa.

### 6.2 Watermark — evidência

Esta subseção guarda **apenas as evidências**. O algoritmo, os guardrails e a persistência estão na §18.8:

| Tema | Onde |
|---|---|
| Snapshot consistente e incremental | **§18.8.3** |
| Hard delete | **§18.8.4** |
| Guardrail de `transaction_type` | **§18.8.6** |
| Persistência do watermark | **§18.8.7** |

**[FATO]** A tabela tem `fetched_at` e `updated_at`, ambos **sem nulos**, com min/max idênticos entre si (2026-03-12 20:21:21 a 2026-08-21 00:03:27) e **157 dias distintos** de `updated_at`.

**[FATO]** `updated_at` começa em **2026-03-12**, enquanto `order_create_time` começa em **2025-06-04**. O campo foi introduzido ou retroalimentado em março/2026 — **não cobre a história anterior**, e por isso a primeira carga é obrigatoriamente backfill integral.

**[FATO] Revisão retroativa é real e material: 720.981 linhas (34,0%) têm `updated_at` mais de 30 dias após `order_create_time`**, e 16,6% mais de 90 dias. Custo e repasse mudam depois do fechamento da competência — não existe "competência fechada" nesta fonte.

**[FATO]** `updated_at` é **watermark técnico**, nunca competência: o incremental é keyed nele porque uma janela móvel sobre `order_create_time` perderia 34,0% das revisões.

**[FATO] Estado do desenho após o UE1-C:**

- A Gold é **comparador inválido** para esta métrica (§18.3) — reconciliá-la **não é** pré-requisito.
- O fato comercial está **READY COM RESTRIÇÃO** (§18.6).
- Continua **NÃO IMPLEMENTADO**.
- A implementação depende dos guardrails da **§18.8** — snapshot consistente, validação de `transaction_type` e persistência de watermark —, não de reconciliar a Gold.

---

## 7. CMV de marketplace — hipótese forte, não fonte pronta

**Correção da revisão 1**, que afirmava que "basta aplicar o pipeline existente".

**[FATO]** `gold.nf_vendas_unificada_v2`, cobertura de custo por canal:

| Canal | Linhas | Com custo | % |
|---|---:|---:|---:|
| ecommerce | 10.481.051 | 10.475.865 | **99,95%** |
| atacado | 362.852 | 361.848 | **99,72%** |
| **marketplace** | **4.416.590** | **0** | **0,00%** |

**[FATO]** `custo_origem` tem **8 valores distintos** em ecommerce e atacado (`protheus_nf`, `tiny_congelado`, `fpea_hist`, `protheus_sku_ultimo`, `protheus_sku_mes`, `tiny`, `protheus_sku_mes_outlier`, `nacional`) e é **NULL em 100% das linhas de marketplace**.

**[FATO] Existe mecanismo de custo histórico:** `protheus_sku_mes` é custo por SKU **por mês**; há também `protheus_sku_ultimo`, `tiny_congelado` e `fpea_hist`. O problema de vigência que o UE0 classificou como "fonte ausente" **já está resolvido para ecommerce e atacado**.

**[FATO]** `sku_canonico` está populado em **4.379.092 de 4.416.590 linhas de marketplace (99,15%)**.

**[FATO]** O mesmo padrão no agregado `gold.vendas_consolidada_produto_v2`: canal `marketplace` presente para as 5 marcas, `custo` NULL em 100% das linhas de marketplace.

### 7.1 O que isto demonstra — e o que não demonstra

**Demonstrado:**
- Existe cadeia de enriquecimento de custo funcional para ecommerce e atacado.
- `sku_canonico` tem alta cobertura de **linhas** no marketplace.
- Existe estratégia com vigência mensal.

**NÃO demonstrado:**
- **[BLOQUEIO]** Que o join é correto para marketplace. Não inspecionei o SQL das oito estratégias — ele não está versionado no nosso repositório e as tabelas-base (`protheus_component_cost`, `protheus_sku_mes`) vivem em `raw`/`staging`, inacessíveis pelo proxy.
- **[BLOQUEIO]** Cardinalidade e ausência de fan-out após o join.
- **[BLOQUEIO]** Se a chave inclui marca/empresa. Se for só SKU, **SKU igual entre marcas** produziria atribuição cruzada — risco real, já observado no Bling (128 códigos em mais de uma marca, UE0).
- **[BLOQUEIO]** Prioridade entre as oito estratégias e critério de desempate.
- **[BLOQUEIO]** Tratamento de kits/bundles e variações.
- **[BLOQUEIO]** Cobertura **econômica** — a cobertura medida é de linhas, não ponderada por receita ou GMV.
- **[BLOQUEIO]** Por que o marketplace recebe `custo_origem = NULL`: omissão, decisão ou impedimento técnico. É a pergunta P1.

⚠️ **[FATO] O universo desta tabela não é o da Torre.** A `receita_produto` de `vendas_consolidada_produto_v2` no canal marketplace é de ordem de grandeza muito superior ao GMV de marketplace da Torre (rituaria acumula R$ 135,7 mi desde 2022-10, contra ~R$ 1 mi de GMV de TikTok em jan–jun/2026). Provavelmente inclui todos os marketplaces — inclusive os seis fora da nossa cobertura (`docs/cobertura_canais_avoe.md`) — e outra definição de receita. **Proibido usar para a Torre antes de reconciliar a receita.**

**[FATO]** `silver.bling_produtos` é snapshot puro: 2.319 produtos com **2.319 timestamps distintos** de `produtos_loaded_at`, todos entre 2026-08-17 e 2026-08-21. Sem histórico. 349 produtos (15%) com `preco_custo = 0`. Fallback de custo **atual**, nunca histórico.

### 7.2 Critério de aceite futuro

**[RECOMENDAÇÃO]** "99% das linhas" é insuficiente. O critério deve exigir, cumulativamente:

1. Cobertura ponderada por **receita/GMV**, não só por linhas.
2. **Zero fan-out**: contagem de linhas idêntica antes e depois do join.
3. Reconciliação de **linhas e receita** antes/depois.
4. Custo **não negativo** e **não NaN**.
5. Separação **por marca** na chave, com teste anti-atribuição-cruzada.
6. Cobertura declarada por **mês × marca × canal**.
7. Tratamento **explícito e contado** dos SKUs sem correspondência — nunca zero silencioso.
8. Reconciliação prévia da **receita** da tabela contra o GMV canônico da Torre.

---

## 8. Cobertura, duplicidade, nulls e fan-out

| Verificação | Resultado |
|---|---|
| `stg_tiktok_payments_by_order` — `transaction_id` único | **sim**, 2.122.887 de 2.122.887 |
| Nulos em transaction_id / order_create_time / brand / fee_breakdown / order_id / statement_id | **0** em todos |
| Moeda ≠ BRL | 0 |
| `order_id` único | **não** — 36.109 linhas excedentes (~1,7% dos pedidos) |
| Máximo de transações por pedido (jun/2026) | **4** |
| Pedidos com mais de um `order_create_time` (jun/2026) | **0** |
| Pedidos atravessando mês (jun/2026) | **0** |
| Pedidos em mais de uma marca (jun/2026) | **0** |
| Marcas na tabela | **8** (3 fora da allowlist, 5,1% das linhas) |
| `transaction_type` | 1 valor (`ORDER`), 0 nulos |
| Positivos nos 3 componentes de afiliado (história completa) | **0** |
| `updated_at` / `fetched_at` nulos | **0** |
| Linhas com `updated_at` > 30 d após o pedido | **720.981 (34,0%)** |
| Chaves de afiliado em `fee_breakdown` | 7, das quais 1 é duplicata |
| `affiliate_commission_amount` vs `before_pit` | iguais em 99,86% de 191.525 linhas |
| Chaves de `fee_breakdown` com valor (jun/2026) | 8 de 53 |
| **Células mês×marca acima de 1% de divergência** | **28 de 35** |
| `api.tiktok_affiliate_marketplace_creators` | **0 linhas** |
| `marts.fact_ml_produto_ranking` `(brand,item_id)` | **0 duplicados** em 1.650 |
| `seller_sku` no ML | ausente em 14,8% dos itens, **0,0% da receita** |
| Ads por item no ML | 897 de 1.650 (54,4%) |
| `ad_revenue > gmv` ML / Shopee | 2 de 847 / **364 de 1.060** |
| `custo` em `nf_vendas_unificada_v2` marketplace | **0 de 4.416.590** |
| `sku_canonico` em marketplace | 99,15% |
| `context_channel` no ML | 1 valor em 493.981 pedidos |
| Colunas de afiliado/custo/origem em `marts.*` | **zero** |

---

## 9. Contratos de métrica candidatos

### C1 — Custo de afiliado do TikTok — **READY COM RESTRIÇÃO** (§18.6) · **NÃO IMPLEMENTADO**

```
grão            : mês × marca                    (PK do fato: ref_month, brand)
fonte           : silver.stg_tiktok_payments_by_order, via VPN operacional (NUNCA o proxy)
grão da fonte   : transaction_id (único, confirmado)
allowlist       : BRANDS_IN_SCOPE — 3 das 8 marcas ficam fora
competência     : order_create_time (estável por pedido; timezone NÃO demonstrável)
transaction_type: allowlist ['ORDER']; tipo novo = falha de contrato
componentes     : creator  = SUM(affiliate_commission_amount_before_pit)
                  partner  = SUM(affiliate_partner_commission_amount)
                  ads      = SUM(affiliate_ads_commission_amount)
PROIBIDO        : somar affiliate_commission_amount (duplicata de before_pit)
PROIBIDO        : publicar affiliate_cost_total antes de P2
sinal           : assinado; NUNCA abs() — ver 18.5.1 (debito/zero/credito)
null vs zero    : NULL = chave ausente; 0 = medido
auditabilidade  : interna — a agregação e' GROUP BY puro da fonte, sem join (§18.5)
                  A Gold NAO e' comparador valido (universo de statements)
RESTRICAO       : revisao retroativa (34,0% >30d, 16,6% >90d) exige reafirmacao de mes publicado
```

### C2 — Receita atribuída a Ads ÷ GMV (publicável com ressalva)

```
grão            : mês × marca × canal (ML, Shopee)
fórmula         : SUM(ad_revenue) / SUM(gmv)
nome            : "Receita atribuída a Ads ÷ GMV"
NUNCA           : "share de vendas por Ads"
ressalva ML     : janela de atribuição própria
ressalva Shopee : janela própria + rateio de período; excede o GMV em 34% dos dias
proibido        : barra de 100%; orgânico por resíduo
```

### C3 — Contribuição pré-CMV do ML (parcial, já existe)

```
grão            : marca × item (listing) — (brand,item_id) único no mart
fórmula         : gross_revenue − marketplace_fee − ad_spend
competência     : LIFETIME — acumulado, não mensal
nome            : "contribuição pré-CMV (acumulada)"
NUNCA           : margem, margem real, lucro, resultado
bloqueio        : marketplace_fee ausente do mart; CMV ausente; depende de P4 para ir à UI
```

### C4 — CMV por SKU — **[BLOQUEIO]**, contrato-alvo

```
grão            : mês × marca × canal × sku_canonico
fonte alvo      : gold.nf_vendas_unificada_v2 com custo_origem populado no marketplace
critério aceite : os 8 itens da §7.2, não "99% das linhas"
enquanto ausente: bling_produtos só como "estimativa com custo atual", em exploração
proibido        : usar vendas_consolidada_produto_v2 antes de reconciliar sua receita
```

---

## 10. O que pode ir à UI

⚠️ **Distinção que vale para toda esta seção:** *READY para implementar o fato* **≠** *fato implementado* **≠** *API disponível* **≠** *UI disponível*. Nada do que segue está no ar.

**[RECOMENDAÇÃO]** Um item já publicável e um habilitado após implementação:

| Item | Estado | Depende de |
|---|---|---|
| **Receita atribuída a Ads ÷ GMV** (C2) | **Publicável hoje** — indicador isolado, sem composição, com ressalvas de janela e rateio | nada |
| **Custo de afiliados por mês do pedido** — três componentes separados (C1) | **READY COM RESTRIÇÃO / NÃO IMPLEMENTADO** — habilitado só depois de existirem fato (UE2-B) e API (UE3), obedecendo §18.10 | UE2-B → UE3 |

**[FATO]** Nada mais tem fonte que sustente exibição.

## 11. O que deve permanecer N/D ou bloqueado

| Item | Estado | Motivo |
|---|---|---|
| Custo de afiliado do TikTok no fato | **READY COM RESTRIÇÃO** (§18.6) | Auditável internamente; Gold não é comparador |
| Retorno / ROAS / ROI de afiliado | **BLOQUEADO** | Sem receita atribuída; tabela vazia |
| Afiliado em ML e Shopee | **BLOQUEADO** (P5 pode virar N/A) | Sem fonte |
| Share de vendas por origem (ML, Shopee) | **BLOQUEADO** | Sem campo de origem |
| `ad_revenue / GMV` | **Permitido** como intensidade de atribuição | Nunca share aditivo |
| Orgânico/outros | **PROIBIDO** | Resíduo é inválido |
| Barra de 100% com Ads+live+produto+afiliado | **PROIBIDO** | Sobreposição comprovada |
| "Margem real" | **INDISPONÍVEL** nos 3 canais | Faltam CMV, frete, impostos, descontos, devoluções |
| Contribuição pré-CMV do ML | **Acumulada/lifetime**; depende de P4 | Não é mensal |
| CMV de marketplace | **Candidato forte, não fonte pronta** | §7.1 |
| Taxa de ML por mês | **BLOQUEADO** | Só lifetime, chave não-única |
| Margem por campanha | **PROIBIDO** | Exigiria rateio |
| `estimated_margin` como margem | **PROIBIDO** | É contribuição pré-CMV acumulada |
| `affiliate_cost_total` | **PROIBIDO** antes de P2 | Decisão de escopo pendente |

---

## 12. Aquisição de fontes

| # | Lacuna | Marketplace | Fonte a solicitar | Proprietário provável | Grão | Histórico | Cadência | Bloqueia |
|---|---|---|---|---|---|---|---|---|
| **L0** | **Definição da competência de `gold.tiktok_brand_daily.date`** | TikTok | Definição da transformação (tabela, não view; não versionada) | Dono do datamart | dia × marca | — | — | **UE2-B inteiro** |
| **L2** | **CMV de marketplace** + SQL das 8 estratégias | todos | Enriquecimento aplicado ao `canal='marketplace'` e o SQL versionado | Dono do pipeline de custo/NF | item de nota | existente | igual a ecommerce | **Pedido 3 (todos)** |
| L1 | Receita atribuída a afiliado | TikTok | Popular `tiktok_affiliate_marketplace_creators` | Time da Raw | dia × marca × creator | 12 meses | diária | Pedido 1 (retorno) |
| L3 | Flag afiliado × conteúdo próprio | TikTok | Campo de tipo de atribuição | Time da Raw | pedido ou creator | 12 meses | diária | Pedido 1 (retorno) |
| L4 | Origem exclusiva da venda | ML, Shopee | Relatório/endpoint de atribuição por pedido | Plataforma + Raw | pedido | 6 meses | diária | **Pedido 2 (inteiro)** |
| L5 | Afiliado em ML e Shopee | ML, Shopee | Confirmar programa e export | Comercial + plataforma | mês × marca | 6 meses | mensal | Pedido 1 |
| L6 | Comissão de ML com competência | ML | Taxa por item **com data** | Raw / ML API | item × mês | 12 meses | mensal | Pedido 3 (ML) |
| L7 | Ads por listing na Shopee | Shopee | Export de Ads diário com `product_id` | Plataforma Shopee | anúncio × dia | 6 meses | diária | Pedido 3 (Shopee) |
| L8 | Definição versionada do DRE | TikTok | Versionar `raw.vw_dre_mensal` | Dono do datamart | — | — | — | Confiança em C1 |
| L9 | Semântica de `preco_custo` | — | Contábil vs comercial; frete de entrada; kit/bundle | Dono do ERP/Bling | — | — | — | Fallback de custo |
| L10 | Chave de SKU no produto TikTok | TikTok | `seller_sku` no grão de produto | Time da Raw | produto | 12 meses | diária | Pedido 3 (TikTok) |
| L11 | Timezone de `order_create_time` | TikTok | Convenção da ingestão | Time da Raw | — | — | — | Competência e §4.4 |
| L12 | Acesso a `raw.*` / VPN estável | — | Expor `raw` no proxy ou VPN confiável | Infra | — | — | — | Reauditoria do DRE |

**[FATO]** `transaction_type` saiu da lista: resolvido (§4.7) — mas gerou uma **regra de contrato**, não uma dispensa de filtro.

---

## 13. Perguntas aos stakeholders

**P1 — [DECISÃO NECESSÁRIA] Custo de marketplace.** O enriquecimento cobre 99,95% de 10,5 mi de linhas de ecommerce e **0% de 4,4 mi de linhas de marketplace**, com `sku_canonico` já em 99,15%. É omissão, decisão ou impedimento técnico? E onde está o SQL das oito estratégias? *Destrava:* CMV nos três canais.

**P2 — [DECISÃO NECESSÁRIA] Escopo de "custo de afiliados".** Três componentes com pagadores distintos: comissão de criadores (R$ 547,9 mil em jun/2026, 5 marcas), comissão de **parceiro afiliado** (R$ 285,9 mil) e comissão de afiliados/Ads (R$ 74,8 mil). Deve existir um total agregado — e, se sim, com quais componentes? *Destrava:* `affiliate_cost_total` e qualquer rótulo que apresente os componentes **somados** como custo único. **Não bloqueia** exibir os três separadamente.

**P3 — [DECISÃO NECESSÁRIA] Pedido 2 sem fonte.** Sem campo de origem em ML nem Shopee, e `ad_revenue` excedendo o GMV em 34% dos dias na Shopee. Aceita o indicador isolado com ressalvas, ou aguarda aquisição? *Destrava:* se o Pedido 2 entra em roadmap.

**P4 — [DECISÃO NECESSÁRIA] Competência do ML.** Receita e taxa por listing são **acumuladas (lifetime)**. Aceita contribuição pré-CMV acumulada por anúncio, ou o indicador só serve mensal? *Destrava:* se o ML pode ser o primeiro canal entregável.

**P5 — [DECISÃO NECESSÁRIA] Programa de afiliados em ML e Shopee.** Participamos? Alguém tem acesso ao painel? *Destrava:* se L5 é aquisição ou "não aplicável" definitivo.

---

## 14. Roadmap

**[RECOMENDAÇÃO] Histórico do veredito do UE2-B, para não haver dúvida de estado:**

| Rodada | Estado do UE2-B | Motivo |
|---|---|---|
| Revisão 1 | READY | Reconciliação demonstrada em **um único mês agregado** |
| Revisão 2 | **BLOCKED** | A extensão a 35 células mostrou divergência em 28 delas |
| **UE1-C** | — | Demonstrou que a Gold é **comparador inválido**: universos e competências distintos por construção |
| **Revisão 3 (atual)** | **READY COM RESTRIÇÃO** | Auditabilidade **interna** contra a fonte transacional; reconciliar a Gold deixou de ser pré-requisito |

**UE2-B é agora o próximo gate implementável — e ainda NÃO foi iniciado.** A ordem abaixo reflete isso.

### UE1-C — Competência do custo TikTok — **CONCLUÍDO** (histórico)

- **Executado nesta rodada** (§18). Determinou que a comparação contra a Gold era metodologicamente inválida; a magnitude exata segue não reconciliável e **deixou de ser pré-requisito**.
- **Stop-loss registrado como consumido:** a hipótese era escalar L0 como bloqueio formal caso a divergência não fosse explicável sem a definição da Gold. Não foi necessário bloquear — a invalidez do comparador foi demonstrada por outro caminho, e L0 permanece aberta sem bloquear o custo por coorte.

### UE1-B — Aquisição *(paralelo, sem código)*

- L0, L2, L1 como pedidos formais; P1–P5 respondidas.
- **Critério de saída:** cada lacuna vira DISPONÍVEL com data, ou BLOQUEADO definitivo.

### UE2-B — Fato mensal de custo de afiliado do TikTok — **PRÓXIMO GATE IMPLEMENTÁVEL**

- **Estado: READY COM RESTRIÇÃO · NÃO INICIADO.** Desenho completo em §18.8; critério de aceite em §18.9.
- Não depende de terceiros nem de reconciliar a Gold. Depende dos requisitos obrigatórios de implementação: snapshot consistente (§18.8.3), guardrail de `transaction_type` (§18.8.6) e persistência de watermark (§18.8.7).
- **Restrições que acompanham o gate:** revisão retroativa exige reafirmação de mês publicado; fuso de `order_create_time` não demonstrável; sem `affiliate_cost_total` (P2).

### UE2-A — CMV de marketplace *(depende de L2)*

- **Risco principal:** universo da tabela ≠ universo da Torre (§7.1). Reconciliar receita **antes** de qualquer uso do custo.
- **Critério de saída:** os 8 itens da §7.2.

### UE3 — API e Canais *(depende de UE2-B)*

### UE4 — Unit economics por listing *(depende de UE2-A + P4; começar por ML)*

### UE5 — QA integrado

**[RECOMENDAÇÃO — atualizada na revisão 3]** O custo de afiliado do TikTok por **coorte de pedido** volta a ser o menor gate implementável (§18.6), mas por fundamento diferente do da revisão 1: não por reconciliar com a Gold — que é comparador inválido —, e sim por **auditabilidade interna** contra a fonte transacional. A reconciliação externa que a revisão 1 celebrava nunca foi válida.

---

## 15. Riscos e decisões abertas

| # | Risco | Gravidade | Mitigação |
|---|---|---|---|
| R0 | Tratar a Gold como comparador do custo por coorte | **Alta** | Resolvido no §18: universos diferentes; validação é interna |
| R1 | Somar `affiliate_commission_amount` com `before_pit` | **Alta** | Teste por nome de chave; documentar no DDL |
| R2 | Usar `vendas_consolidada_produto_v2` sem reconciliar receita | **Alta** | Reconciliação obrigatória (§7.1) |
| R3 | Aplicar custo atual do Bling a mês histórico | **Alta** | Rótulo "estimativa com custo atual" |
| R4 | Aceitar `transaction_type` novo em silêncio | **Alta** | Allowlist com falha explícita (§4.7) |
| R5 | Incremental por competência perder revisão retroativa | **Alta** | Watermark em `updated_at` (34,0% das linhas revisadas >30 d) |
| R6 | `CHECK (<= 0)` rejeitar estorno legítimo | Média | Sem check de sinal (§4.6) |
| R7 | `deposit`/`release`/`external_affiliate` virarem não-zero | Média | Falha de contrato, não inclusão automática |
| R8 | Marca fora da allowlist entrar no fato | Média | `BRANDS_IN_SCOPE` obrigatório (5,1% das linhas) |
| R9 | SKU igual entre marcas cruzar custo | Média | Chave com marca; teste anti-cruzamento |
| R10 | Rótulo "retorno de afiliados" sem receita | **Alta** | Bloqueado por contrato |
| R11 | Grão lifetime do ML lido como mensal | Média | Declarar "acumulado" |
| R12 | `raw.*` inacessível esconder mudança de contrato | Média | L12 |
| R13 | Proxy de auditoria virar dependência de runtime | **Alta** | Fluxo da §6 usa VPN operacional |

---

## 16. Evidências e comandos read-only

### 16.1 Consultas executadas

Via proxy governado (`api`/`silver`/`gold`) e psycopg2 read-only (Neon):

1. Tipo de relation (`pg_class.relkind`) de `gold.tiktok_brand_daily`, `silver.stg_tiktok_payments_by_order`, `api.tiktok_payments_by_order`.
2. Colunas e nulabilidade de `silver.stg_tiktok_payments_by_order` (28 colunas).
3. Perfil global: contagens, nulos em 6 campos, distintos de `transaction_id`/`order_id`/`brand`, moeda.
4. Distribuição por marca com janela de datas.
5. Domínio de `transaction_type` sobre a tabela inteira.
6. `gold.tiktok_brand_daily.total_fees` por mês × marca, 5 marcas (55 células).
7–12. `silver_7` por mês × marca, em lotes: 2025-10/11, 2025-12/2026-01, 2026-02, 2026-03, 2026-04.
13. Positivos nos 3 componentes de afiliado, história completa.
14. Fan-out por pedido em jun/2026: transações, instantes, meses, marcas.
15. Watermark: nulos, min/max de `updated_at`/`fetched_at`, dias distintos, linhas revisadas >30 d.
16. Chaves de `fee_breakdown` (`jsonb_object_keys`, amostra de 3.000).
17. Comparação `affiliate_commission_amount` × `before_pit`, jun/2026.
18. Soma de todas as chaves de `fee_breakdown` (`jsonb_each_text`), jun/2026.
19. As 8 chaves com valor não-zero, classificadas.
20. Componentes de taxa e afiliado, jun/2026, 5 marcas.
21. Catálogo: tabelas com afiliado/DRE/payments/statement/settlement.
22. Catálogo: candidatos de custo com dimensão temporal.
23. `vendas_consolidada_produto_v2` por canal × marca.
24. `nf_vendas_unificada_v2` por canal; e por canal × `custo_origem` × `sku_canonico`.
25. `bling_produtos`: cargas distintas, nulos, zeros, marcas.
26. Catálogo de colunas de origem em `stg_ml_*` e `stg_shopee_*`.
27. Domínio de `context_channel` (493.981 pedidos).
28. Neon: colunas de afiliado/custo/origem em `marts.*`; `ad_revenue` vs `gmv`; grão de `fact_ml_produto_ranking`; cobertura de `seller_sku` por receita; domínio de `channel`.

### 16.2 Consultas rejeitadas por custo — seis

Sete `UNION ALL` sobre 2,1 mi de linhas; doze agregados condicionais com `jsonb`; lote de 4 meses; lote de 2 meses em competências de alto volume; `count(DISTINCT order_id)` por marca; lote de 5 meses para 4 marcas. Cada uma foi respondida com redução de escopo. Nenhuma varredura foi repetida.

**Consequências declaradas:** perfil de sinal completo dos 7 campos não medido (só os 3 principais, e só positivos); 4 das 11 competências (mai, jun, jul, ago/2026) não medidas por marca.

### 16.3 O PDF — item do UE0, fechado

**[FATO]** `docs/octaprice/Regua_Cobranca_Marketplaces_gobeaute.pdf` lido sem instalar dependência: decodifiquei o CMap `ToUnicode` (102 glifos) e extraí 23.983 caracteres.

**Assunto real:** *Política de Preço Mínimo Anunciado (PMA) — Régua de Cobrança para revenda em marketplaces*. Um crawler compara o preço final exibido (produto + frete, líquido de cupom) contra o PMA vigente do SKU em Amazon, Shopee e Mercado Livre. Trilho **A** para CNPJ cliente direto (notificação em 24 h, perda de 10 pontos de desconto, descadastramento em 48 h); trilho **B** para vendedor não identificado (denúncia por propriedade intelectual). Status: *"Proposta — pendente de validação jurídica"*.

**[FATO] Termos de Unit Economics no texto:** `comissão` 0 · `fee` 0 · `competência` 0 · `cancelamento` 0 · `contestação` 0 · `devolução` 0 · `reembolso` 0 · `tributo` 0 · `imposto` 0 · `repasse` 0 · `settlement` 0 · `afiliado` 0 · `ads` 0 · `CMV` 0. As três ocorrências isoladas são "taxa de aprovação das denúncias", "produto mais frete" e "preço incompatível com o custo de aquisição".

**Conclusão:** sem relação com taxas de marketplace nem Unit Economics. A hipótese do UE0 derivou apenas do nome do arquivo — "cobrança" ali é execução de política de preço, não régua de tarifas.

---

## 17. Limitações da auditoria

1. **[BLOQUEIO] VPN indisponível.** Timeout em duas tentativas; contornado pelo proxy governado.
2. **[BLOQUEIO] Schema `raw` inacessível por qualquer via.** `raw.vw_dre_mensal` não reauditada; `raw.tiktok_affiliate_marketplace_creators` verificada só pelo espelho `api.`; tabelas-base de custo não inspecionadas (existência comprovada indiretamente por `custo_origem`).
3. **[BLOQUEIO] Documentação oficial de API inacessível.** TikTok devolveu conteúdo truncado (app JS); Mercado Livre HTTP 403; Shopee bloqueado para a ferramenta de fetch. **Não afirmo nada sobre o que as APIs oferecem** — tudo aqui é sobre o que nosso pipeline captura. L1, L4–L7 e L10 exigem essa consulta.
4. **Derivação de `gold.tiktok_brand_daily` não demonstrável** — é tabela, não view, e a transformação não é versionada (§4.1).
5. **Quatro competências não reconciliadas por marca** (mai, jun, jul, ago/2026) por rejeição de custo. Junho tem só o total agregado das 5 marcas.
6. **Quatro competências sem contrapartida na Gold** (2025-06 a 2025-09).
7. **Perfil de sinal incompleto:** medi apenas ausência de positivos nos 3 componentes principais. `deposit`/`release`/`external_affiliate` foram medidos como zero só em jun/2026.
8. **Timezone de `order_create_time` não demonstrável** (§4.7). Não inventei convenção.
9. **Unicidade de chave não verificada** em `stg_ml_orders`, `stg_shopee_order_item_snapshots`, `stg_shopee_ads` e `nf_vendas_unificada_v2`.
10. **`affiliate_partner_commission_amount` não confirmado** como MCN/agência — inferência pelo nome.
11. **Semântica de `preco_custo`** não confirmada.
12. **Receita de `vendas_consolidada_produto_v2` não reconciliada** com o GMV da Torre; divergência de ordem de magnitude registrada, causa não investigada.
13. **SQL das oito estratégias de custo não inspecionado** — não versionado no nosso repositório.
14. **Nenhuma validação com o stakeholder.** As cinco perguntas da §13 e todas as recomendações são propostas.

---

## 18. UE1-C — Competência do custo TikTok: custo por pedido × taxa por repasse

Data: 2026-08-21. Auditoria read-only. Fotografia lógica da fonte: `max(updated_at) = 2026-08-21T00:03:27.454607`, 2.122.887 linhas — usado **apenas** como limite superior técnico de leitura, **nunca** como competência.

### 18.1 Correção da interpretação

A revisão 2 concluiu que "não há base auditável" porque Silver e Gold não reconciliavam por `order_create_time`. **Isso confundia duas coisas distintas:**

| Conceito | Significado | Estado |
|---|---|---|
| **Auditabilidade interna** | O agregado reproduz o detalhe da própria fonte transacional | **Demonstrada** (§18.5) |
| **Comparabilidade externa** | O agregado bate com outro objeto (a Gold) | **Impossível por construção** (§18.3) |
| **Competência comercial** | Coorte de pedido — `order_create_time` | **Demonstrada e estável** |
| **Competência financeira** | Mês de repasse — `statement_month` | **Existe e é populada**, mas linhagem externa |

Um fato de custo por coorte de pedido **não precisa** reconciliar com a Gold para ser auditável. Precisa reproduzir a fonte de que deriva.

### 18.2 Matriz de linhagem

| Objeto | Tipo físico | Grão observado | Chave | Competência | Universo | Transformação | Classificação |
|---|---|---|---|---|---|---|---|
| `silver.stg_tiktok_payments_by_order` | **tabela** | transação | `transaction_id` (único) | `order_create_time` | **pedido/transação** | externa (ingestão) | **CONFIRMADO** |
| `api.tiktok_payments_by_order` | **view** sobre a Silver | idem | idem | idem | idem | — | **CONFIRMADO** (1:1) |
| `gold.tiktok_settlements_summary` | tabela | statement | `statement_id` (único) | `statement_date` / `statement_month` | **statement** | **externa, não versionada** | grão CONFIRMADO · transformação DESCONHECIDA |
| `gold.tiktok_brand_daily` | **tabela** | dia × marca | `(date, brand)` | **`date` — semântica DESCONHECIDA** | **statement** (para `total_fees`) | **externa, não versionada** | universo CONFIRMADO · competência **NÃO DEMONSTRÁVEL** |
| `api.tiktok_shop_statements` | tabela | statement | `statement_id` | `create_time` | statement | externa | INFERIDO |
| `api.tiktok_shop_settlements` | tabela | — | — | — | — | — | **VAZIA — revalidado: 0 linhas** |
| `marts.fact_tiktok_brand_content_daily` | tabela | dia × marca | `(date, brand)` | herdada da Gold | herdado | cópia sem transformação (migration 007) | **CONFIRMADO** |

**[FATO] A Gold não é fonte independente.** Pertence à mesma cadeia externa da Silver, sua transformação não é versionada, e `gold.tiktok_brand_daily` é tabela — não há `pg_get_viewdef` a obter.

**[FATO] O universo de `total_fees` já estava documentado no repositório**, em dois lugares que a revisão 2 não incorporou:

1. `pipelines/transforms/tiktok_brand_daily.py:88-97` — comentário em código versionado: *"total_settlement/total_fees continuam passthrough do gold externo (valores absolutos, não tocados, **população de statement e não de pedido**)"*. O conector deixou de selecionar `avg_fee_pct`/`avg_settlement_pct` justamente porque o *"universo do numerador [é] incompatível com o novo GMV — não inventar essa equivalência"*.
2. `docs/sections/financeiro_audit.md` §11.1 — comprovado por SQL no grão statement × marca: `settlement = revenue + fee_tax + shipping + adjustment` fecha exato, mas sobre a *"revenue" do subsistema de repasses* (R$ 14,13 mi em mai/2026), **não sobre o GMV comercial** (R$ 13,40 mi, 5,5% menor). *"universos/denominadores diferentes — isso é fato, verificado linha a linha."*

### 18.3 Por que a comparação das 28 de 35 células era inválida

**[FATO]** Porque a comparação era semanticamente inválida:

- **Lado esquerdo:** sete componentes de `fee_breakdown`, agrupados por mês de `order_create_time` → **coorte de pedido**.
- **Lado direito:** `gold.tiktok_brand_daily.total_fees` → **passthrough de agregado do subsistema de statements**.

São competências e universos diferentes **por construção**. A divergência não é defeito dos componentes transacionais de afiliado; é consequência esperada de comparar coorte de pedido contra agregado de repasse. **A comparação estava errada — os componentes não.**

**[FATO] Quantificado — em LINHAS, não em valor.** Das 2.014.836 linhas/transações de pagamento das cinco marcas oficiais, **494.997 (24,6%)** têm `statement_month` diferente do mês de `order_create_time`.

⚠️ **Este número mede transações que cruzam a fronteira mensal, não a fração do valor monetário que muda de competência.** O valor migrado **não foi calculado** — as transações que cruzam a fronteira não têm, necessariamente, ticket médio igual às que não cruzam.

**O que isto demonstra:** estruturalmente, que **as duas competências não são intercambiáveis**, e portanto que comparar uma reconstrução por coorte de pedido contra um agregado de statements é **metodologicamente inválido**.

**O que isto NÃO demonstra:** a magnitude nem o padrão exatos das divergências observadas contra a Gold. Esses **permanecem não reconciliáveis**, porque a semântica de `gold.tiktok_brand_daily.date` é desconhecida (§18.4) — e ela não corresponde nem ao mês do pedido nem ao `statement_month`.

**[INFERÊNCIA]** Os três fechamentos exatos da rituaria — a marca de menor volume — são **compatíveis** com a hipótese de que, havendo poucos pedidos, é maior a chance de nenhum cruzar a fronteira de repasse. **Não é fato:** não testei essa hipótese, e ela não explicaria por si só o padrão das demais marcas. Continua sendo coincidência plausível, não causa demonstrada.

**Resposta ao quesito:** o UE2-B estava bloqueado porque usava **o comparador e a competência errados**, não porque o custo seja inválido.

### 18.4 A hipótese de statement como competência da Gold — REFUTADA

**[FATO]** Testei agrupar os mesmos sete componentes por `statement_month` (join provado seguro, §18.5) e comparar com `gold.total_fees`:

| Competência de statement | Marca | Sete componentes | `gold.total_fees` | Δ relativo |
|---|---|---:|---:|---:|
| 2026-03 | apice | −100.039,45 | −114.030,69 | −12,27% |
| 2026-03 | barbours | −4.119.821,23 | −3.557.263,86 | **+15,81%** |
| 2026-03 | kokeshi | −681.517,30 | −723.847,82 | −5,85% |
| 2026-03 | lescent | −66.574,45 | −80.975,28 | −17,78% |
| 2026-03 | rituaria | −25.084,15 | −20.148,88 | **+24,49%** |
| 2026-04 | apice | −160.550,96 | −169.933,36 | −5,52% |
| 2026-04 | barbours | −2.746.041,13 | −2.843.690,03 | −3,43% |
| 2026-04 | kokeshi | −647.377,00 | −716.920,49 | −9,70% |
| 2026-04 | lescent | −76.459,44 | −86.037,51 | −11,13% |
| 2026-04 | rituaria | −21.600,23 | −38.665,48 | **−44,14%** |

**[FATO]** A competência de statement **afasta** (−44% a +25%) em vez de aproximar (−0,31% a −3,64% na coorte de pedido). **A coluna `date` da Gold não segue o mês de statement.**

**[FATO] Conclusão:** a competência de `gold.tiktok_brand_daily.date` permanece **NÃO DEMONSTRÁVEL**. Não é mês de pedido nem mês de statement. Como a transformação é externa e não versionada, e a tabela não é view, **não há caminho de auditoria pelo nosso lado**. A Gold é, portanto, **comparador inválido** — não uma referência a perseguir.

**[RECOMENDAÇÃO]** Nenhuma tolerância de reconciliação externa deve ser calculada. Alinhamento semântico é pré-requisito de tolerância, e ele não existe.

### 18.5 Auditabilidade interna da fonte — o que sustenta o veredito

**[FATO] Grão e chave:** `transaction_id` é **único** — 2.122.887 distintos em 2.122.887 linhas. Zero nulos em `transaction_id`, `order_create_time`, `brand`, `fee_breakdown`, `order_id`, `statement_id`. Moeda 100% BRL.

**[FATO] Sem fan-out.** O fato proposto é **`GROUP BY` puro da tabela-fonte, sem nenhum join**. Não há como multiplicar linhas ou valores. A reconciliação agregado × detalhe é exata por construção, não por tolerância.

**[FATO] O join para a visão financeira também é seguro** (caso venha a ser usado): `statement_id` é único em `gold.tiktok_settlements_summary` (3.371 de 3.371), zero nulos. Testado sobre as cinco marcas: 2.014.836 linhas entram, **2.014.836 encontram statement (0 unmatched)**, **0 divergências de marca** no join, contagem preservada → **fan-out impossível**.

**[FATO] Isso resolve parcialmente a pendência §11.6.3 do `financeiro_audit.md`**, que registrava a reconciliação pedido↔statement como bloqueada por `raw.tiktok_shop_settlements` estar vazia. A ponte existe por outro caminho: `silver.stg_tiktok_payments_by_order.statement_id` × `gold.tiktok_settlements_summary.statement_date`. A tabela vazia **continua vazia** (revalidado: 0 linhas), mas deixou de ser necessária para essa ligação.

**[FATO] Marcas:** 8 na tabela; 3 fora da allowlist da Torre (gocase, azbuy, denavita — 108.051 linhas, 5,1%). `BRANDS_IN_SCOPE` é obrigatório.

**[FATO] `transaction_type`:** valor único `ORDER`, zero nulos, 2.122.887 linhas em 14 meses. **Contrato:** allowlist explícita; tipo desconhecido é **falha de contrato com alerta**, nunca inclusão silenciosa.

**[FATO] Perfil histórico completo dos sete campos de afiliado** (2.122.887 linhas):

| Campo | Positivos | Chave ausente | Observação |
|---|---:|---:|---|
| `affiliate_commission_amount_before_pit` | **0** | **0** | usado como comissão de criadores |
| `affiliate_partner_commission_amount` | **0** | **0** | **comissão de parceiro afiliado** — significado operacional exato não confirmado |
| `affiliate_ads_commission_amount` | **0** | **0** | Ads de afiliado |
| `affiliate_commission_amount` | — | — | **DUPLICATA de `before_pit` — nunca somar junto** |
| `affiliate_commission_deposit` | — | **0** | **sempre zero em toda a história** |
| `affiliate_commission_release` | — | **0** | **sempre zero em toda a história** |
| `external_affiliate_marketing_fee_amount` | — | **0** | **sempre zero em toda a história** |

**[RECOMENDAÇÃO]** Sem `CHECK (<= 0)`: zero positivos em 14 meses é evidência, não prova de que uma reversão futura não chegue como positivo. Preservar valor **assinado** e **nunca aplicar `abs()`** — a regra completa de débito/zero/crédito está na §18.5.1. `CHECK (<> 'NaN')` sim.

#### 18.5.1 Semântica de débito, zero e crédito — regra final

**[RECOMENDAÇÃO]** As três regras que a revisão anterior propunha eram **incompatíveis entre si**: "armazenar assinado", "não usar `CHECK <= 0` porque reversões positivas podem existir" e "aplicar `abs()` na apresentação". Se um positivo pode existir e significa reversão, `abs()` o transformaria em custo — destruindo exatamente o sinal econômico que a primeira regra preserva.

Regra final, sem contradição:

| Valor na fonte | Significado | Persistência | Apresentação |
|---|---|---|---|
| **Negativo** | débito / custo | assinado, como veio | pode ser exibido como magnitude positiva via **`-valor`** |
| **Zero** | custo **medido** como zero | `0` | exibir `0`, nunca travessão |
| **Positivo** | **crédito / reversão** | assinado, como veio | **NÃO exibir como custo.** Rotular crédito/reversão, ou sinalizar estado que exige revisão |

**[FATO] `abs()` é proibido neste contrato.** Não é escolha estética: `abs()` apaga a distinção entre débito e crédito, e a semântica de reversão nestes campos **não está confirmada**.

**[RECOMENDAÇÃO]** Enquanto essa semântica não for confirmada, qualquer valor positivo deve gerar **warning de contrato** — não falha de carga, porque o valor pode ser legítimo, mas nunca inclusão silenciosa em um total de custo.

**Nunca destruir o sinal econômico.** Sem `CHECK` de sinal (um positivo legítimo seria rejeitado); `CHECK (<> 'NaN')` permanece.

**[FATO] Revisão retroativa — distribuição do atraso `updated_at − order_create_time`:**

| Faixa | Linhas | % |
|---|---:|---:|
| Anterior ao pedido | **0** | 0,0% |
| ≤ 7 dias | 232.776 | 11,0% |
| 7–30 dias | 1.169.130 | 55,1% |
| 30–90 dias | 368.001 | 17,3% |
| **> 90 dias** | **352.980** | **16,6%** |

**[FATO]** 34,0% das linhas são atualizadas mais de 30 dias após o pedido, e 16,6% mais de 90 dias. **Não existe "competência fechada" nesta fonte.** `updated_at` cobre de 2026-03-12 em diante; os pedidos começam em 2025-06-04 — o campo **não cobre** a história anterior.

⚠️ **`updated_at` é watermark técnico. Não é competência financeira nem comercial.** Serve para incremental e para diagnosticar revisão; nunca para agrupar métrica de negócio.

### 18.6 Os quatro vereditos

| # | Produto de dado | Veredito | Fundamento |
|---|---|---|---|
| **1** | **Custo de afiliado por coorte do pedido** (Canais/comercial) | **READY COM RESTRIÇÃO** | Grão único confirmado; agregação sem join nem fan-out; allowlists definidas; componentes separados; duplicata identificada; sinal e nulos perfilados em toda a história. **Restrições:** (a) sujeito a revisão retroativa — mês publicado precisa ser reafirmável; (b) timezone de `order_create_time` não demonstrável, logo a fronteira do mês repousa em convenção não declarada; (c) **não reconcilia com a Gold, e não deve tentar** |
| **2** | **Taxas reconhecidas por statement** (Financeiro/repasse) | **BLOCKED** | `statement_date`/`statement_month` existem, são 100% populadas, e o join é provadamente seguro — isso está demonstrado. **Mas a linhagem de `gold.tiktok_settlements_summary` é externa e não versionada**, e nenhuma referência valida os totais. A lacuna ficou estreita e nomeada, não fechada |
| **3** | **`affiliate_cost_total`** | **BLOCKED** | Decisão P2 pendente: quais componentes agregar. Não implementar coluna de total |
| **4** | **Retorno de afiliado** | **BLOCKED** | `api.tiktok_affiliate_marketplace_creators` revalidada: **0 linhas**. Sem receita atribuída, não há retorno |

### 18.7 Cobertura efetivamente medida

**[FATO]** Coorte de pedido (revisão 2, §4.3): **7 competências × 5 marcas = 35 células** — out/2025 a abr/2026. **Não medidas por marca:** mai, jun, jul e ago/2026 (junho tem apenas o total agregado das cinco marcas). Causa: rejeição por custo do proxy.

**[FATO]** Competência de statement (§18.4): **2 competências × 5 marcas = 10 células** — mar e abr/2026. As demais não foram medidas; a refutação não exigia mais, porque a magnitude do afastamento é inequívoca.

**[FATO]** Perfis de grão, sinal, `transaction_type`, marcas, chaves ausentes e revisão retroativa: **história completa** (2.122.887 linhas).

**[FATO]** Out/2025 permanece separado: a Gold tem 25–27 dias por marca no mês, contra 30–31 nos seguintes — mês parcial de início.

**[FATO] Fotografia estável:** `max(updated_at)` no início e ao fim da auditoria = `2026-08-21T00:03:27.454607`, com 2.122.887 linhas nas duas leituras. Nenhuma consulta desta fase leu além desse limite.

### 18.8 Contrato futuro — desenho, sem implementação

**[RECOMENDAÇÃO]** Dois objetos **separados**, nunca a mesma tabela nem a mesma coluna.

#### 18.8.1 Tipos temporais — verificados por catálogo

**[FATO]** `information_schema.columns`, em `silver.stg_tiktok_payments_by_order` e nos espelhos de `api`:

| Coluna | Tipo PostgreSQL | Precisão | Nulável |
|---|---|---:|---|
| `order_create_time` | **`timestamp without time zone`** | 6 | YES |
| `updated_at` | **`timestamp without time zone`** | 6 | YES |
| `fetched_at` | **`timestamp without time zone`** | 6 | YES |

⚠️ **[RECOMENDAÇÃO] Não converter silenciosamente.** Nenhum dos três carrega offset. O destino deve **preservar a semântica real da origem**:

- armazenar como `timestamp` **sem** timezone, ou
- **bloquear a conversão** até existir decisão explícita sobre o fuso.

**Proibido** assumir `America/Sao_Paulo` ou `UTC` e aplicar `AT TIME ZONE` sem essa decisão — seria inventar dado. A convenção de fuso da ingestão é a lacuna L11.

#### 18.8.2 O fato comercial

```
marts.fact_tiktok_affiliate_cost_order_monthly     -- competencia COMERCIAL

grao / PK    : (ref_month, brand)
competencia  : mes de order_create_time            -- timestamp SEM timezone (18.8.1)
populacao    : coorte de pedido; BRANDS_IN_SCOPE; transaction_type allowlist ['ORDER']
negocio      : affiliate_creator_commission  numeric   -- before_pit, ASSINADO
               affiliate_partner_commission  numeric   -- ASSINADO
               affiliate_ads_commission      numeric   -- ASSINADO
               source_row_count              bigint
auditoria    : source_max_updated_at  timestamp        -- watermark TECNICO, sem tz
               synced_at              timestamptz NOT NULL DEFAULT now()
               source_run_id          varchar(64)
PROIBIDO     : affiliate_cost_total (P2)
PROIBIDO     : somar affiliate_commission_amount junto de before_pit
CHECK        : (<> 'NaN') em cada numeric; SEM check de sinal
```

**[FATO] `updated_at` é exclusivamente watermark técnico.** Não é competência comercial nem financeira, e não deve aparecer em nenhuma agregação de negócio.

**[FATO] `source_row_count` significa:** a quantidade de **transações** que passaram por todos os filtros — competência (`ref_month`), marca allowlisted, `transaction_type` allowlisted — dentro da **fotografia/cutoff** utilizado naquela execução. Não é contagem de pedidos nem de linhas brutas da tabela.

Se e quando a competência financeira for destravada, um objeto **distinto**, com nome explicitamente de repasse — por exemplo `marts.fact_tiktok_settlement_fees_monthly`, com grão `(statement_month, brand)`. Nunca fundir as duas competências na mesma tabela ou coluna.

#### 18.8.3 Algoritmo incremental — fotografia consistente e recálculo por chave

⚠️ **[RECOMENDAÇÃO] Um cutoff por `updated_at` sozinho NÃO produz fotografia consistente sob concorrência.** Este é o defeito do desenho anterior.

**Por que:** entre o passo de *descoberta das chaves tocadas* e o passo de *recálculo integral*, uma linha daquela mesma chave pode receber `updated_at > current_upper_bound`. No recálculo ela é excluída pelo limite superior — e o agregado publicado **perde temporariamente a contribuição dela**. O resultado não é apenas desatualizado: é internamente inconsistente, porque a chave foi recalculada sobre um conjunto que já não corresponde a nenhum instante real da fonte.

**[RECOMENDAÇÃO] Requisito obrigatório de implementação:** todas as leituras da fonte de uma execução devem ocorrer **dentro de uma única transação read-only com isolamento `REPEATABLE READ`** — ou mecanismo equivalente que prove snapshot consistente.

⚠️ **Não afirmo que o Data Mart já esteja configurado assim.** Isolamento é propriedade da transação, não do servidor, e a réplica de leitura não foi verificada quanto a isso. Fica registrado como **requisito a satisfazer no UE2-B**, não como fato observado.

```
-- FONTE: uma unica transacao read-only, REPEATABLE READ
BEGIN TRANSACTION READ ONLY ISOLATION LEVEL REPEATABLE READ;

  a. validar tipos          -- 18.8.6, ANTES de qualquer filtro comercial
  b. current_upper_bound := max(updated_at)          -- DENTRO da fotografia
  c. chaves_tocadas := DISTINCT (ref_month, brand)
        WHERE updated_at >= previous_successful_upper_bound
          AND updated_at <= current_upper_bound
          AND brand IN BRANDS_IN_SCOPE
          AND transaction_type IN ('ORDER')          -- so depois de (a)
  d. para cada chave: recalcular INTEGRALMENTE, lendo TODAS as transacoes
     daquela (ref_month, brand) com updated_at <= current_upper_bound
     -- nao apenas as alteradas na janela
  e. materializar a staging e reconciliar (18.9 fronteiras A e B)

COMMIT;   -- somente APOS materializar e reconciliar a staging
```

**Regras que acompanham a fotografia:**

1. **Uma única transação** cobre validação de tipos, captura do cutoff, descoberta de chaves e recálculo integral. Os quatro passos leem o **mesmo** snapshot.
2. `current_upper_bound` é capturado **dentro** da fotografia — nunca antes de abri-la.
3. **Nenhuma consulta posterior em `READ COMMITTED`** pode compor o conjunto. Misturar níveis de isolamento produz resultado híbrido, que é o defeito original com outra roupa.
4. A transação da fonte é encerrada **somente após** a staging estar materializada e reconciliada.
5. A escrita no destino permanece **atômica e separada** da transação de leitura.
6. **Falha em qualquer etapa não avança o watermark** (§18.8.7).
7. Alterações posteriores à fotografia entram na **execução seguinte**, por construção — o limite superior é fechado e persistido.

#### 18.8.4 Hard delete — não detectável por watermark

⚠️ **[FATO] `updated_at` não detecta remoção física na origem.** Uma linha excluída não recebe timestamp novo — simplesmente deixa de existir. Nenhum incremental baseado em watermark, por mais correto que seja, percebe isso.

**[RECOMENDAÇÃO] Consequência obrigatória:** a **reconciliação/backfill histórico integral periódico é obrigatória**, não opcional, e é o único mecanismo que corrige hard delete. Recomendo cadência mensal, cobrindo toda a história.

#### 18.8.5 Política de atualização — consolidada

- **Primeira carga: backfill integral, sempre.** Não depende da cobertura histórica do watermark — e, de fato, `updated_at` só existe a partir de 2026-03-12 enquanto os pedidos começam em 2025-06-04.
- **Incremental por `updated_at`**, com recálculo integral das chaves tocadas (§18.8.3) — nunca janela sobre `order_create_time`, que perderia 34,0% das revisões.
- **Backfill histórico integral periódico** (mensal), obrigatório por causa de hard delete (§18.8.4) e porque 16,6% das linhas mudam após 90 dias.
- **Advisory lock** por competência; staging transacional; rollback por `DELETE` da janela de chaves + reinserção na mesma transação.
- **Runtime pela VPN operacional.** O proxy governado é ferramenta de auditoria e **não pode** ser dependência de pipeline.

#### 18.8.6 Guardrail de `transaction_type` — validar antes de filtrar

⚠️ **[RECOMENDAÇÃO] O desenho anterior tornava o guardrail inoperante.** Ele filtrava `transaction_type IN ('ORDER')` **dentro** da própria descoberta de chaves. Um tipo novo simplesmente não seria selecionado — e passaria despercebido, exatamente o que o guardrail deveria impedir.

Ordem correta, em duas etapas sobre a **mesma fotografia**:

```
1. VALIDAR  -- antes de qualquer filtro comercial
   tipos_na_janela := SELECT DISTINCT transaction_type
                      FROM fonte
                      WHERE updated_at >= previous_successful_upper_bound
                        AND updated_at <= current_upper_bound
   se tipos_na_janela contiver NULL ou qualquer valor fora da allowlist:
        FALHAR a execucao, com erro sanitizado (nome do valor inesperado,
        contagem; nunca identificador individual)
        e NAO avancar o watermark

2. FILTRAR  -- somente depois da validacao passar
   aplicar transaction_type IN ('ORDER') na descoberta de chaves
   e no recalculo integral
```

**[RECOMENDAÇÃO] Onde mais essa validação é obrigatória:**

- **Backfill integral (primeira carga):** validar os tipos sobre **toda a história**, não só uma janela.
- **Reafirmação histórica integral periódica:** repetir a validação sobre todo o período reprocessado.

**[FATO] Esta prova pertence à fronteira fonte × contrato de entrada, não a staging × destino.** O fato mensal proposto **não armazena** `transaction_type` — ele não pode "provar" uma coluna que não tem. E **não se deve adicionar `transaction_type` ao fato apenas para satisfazer um teste**: isso mudaria o grão declarado `(ref_month, brand)`.

#### 18.8.7 Persistência do watermark — contrato mínimo

**[RECOMENDAÇÃO]** O desenho anterior dizia "persistir `current_upper_bound`" sem definir o contrato dessa persistência. Sem escolher tabela nem arquitetura, o UE2-B deverá definir explicitamente:

| Requisito | Detalhe |
|---|---|
| Armazenamento durável | do último watermark **bem-sucedido**; sobrevive a restart do pipeline |
| Identificação | qual fonte / qual pipeline — um watermark por par (fonte, destino) |
| Tipo do valor | `timestamp` **sem** timezone, coerente com a origem (§18.8.1). Não converter |
| Rastreabilidade | `source_run_id` da execução que o avançou |
| Momento do avanço | **somente após** staging materializada, escrita no destino concluída **e** reconciliação aprovada |
| Rollback | execução revertida ou sem publicação **não avança** o watermark |
| Nenhuma chave tocada | comportamento explícito: o watermark **pode** avançar (nada mudou de fato), mas isso precisa ser decisão declarada, não efeito colateral |
| Primeira carga | ausência de watermark **exige backfill integral**; nunca tratar "sem watermark" como "desde o início da janela" |

**Nenhuma decisão de tabela ou arquitetura é tomada nesta correção documental.**

### 18.9 Critério de aceite — três fronteiras

⚠️ **[FATO] O que este critério prova e o que não prova.**

- **Prova:** que a materialização é **fiel à fonte transacional** de que deriva.
- **NÃO prova:** exatidão externa perante TikTok, Seller Center, statements ou repasses. **Não é validação externa.** Nenhuma tolerância percentual contra fonte externa pode ser derivada, porque não existe comparador semanticamente alinhado (§18.4).

#### Fronteira A — fonte × contrato de entrada

Validada **dentro da fotografia** (§18.8.3), antes de qualquer materialização:

1. **Snapshot único** — uma transação `REPEATABLE READ` cobre todas as leituras.
2. **Cutoff** — `current_upper_bound` capturado dentro da fotografia e registrado.
3. **Tipos permitidos** — `DISTINCT transaction_type` da janela contém **somente** valores da allowlist; `NULL` ou valor desconhecido **falha a execução** com erro sanitizado (§18.8.6).
4. **Marcas** — nenhuma marca fora de `BRANDS_IN_SCOPE` entra no conjunto.
5. **Nulls** — campos-chave (`transaction_id`, `order_create_time`, `brand`, `fee_breakdown`) sem nulo.
6. **Chaves** — `transaction_id` único na janela lida.
7. **Nenhuma linha fora da fotografia** — toda linha considerada tem `updated_at <= current_upper_bound`.

#### Fronteira B — fonte detalhada × staging agregada

8. As **três somas** de componentes (criadores, parceiro afiliado, afiliados/Ads) conferem entre detalhe e agregado.
9. **`source_row_count`** igual à contagem de transações filtradas — competência, marca allowlisted, `transaction_type` allowlisted, dentro da fotografia.
10. **Conjunto de chaves** `(ref_month, brand)` idêntico nos dois lados.
11. **Nulls** — nenhum componente nulo onde a fonte tem a chave presente.
12. **Cutoff utilizado** — `max(updated_at)` na staging igual ao `current_upper_bound` capturado.
13. **Reconciliação integral por chave** — cada `(ref_month, brand)` confere por inteiro, não por delta.

#### Fronteira C — staging × destino

Comparadas **no mesmo grão mensal**:

14. **Igualdade monetária exata** por `(ref_month, brand)` — diferença zero, identidade e não tolerância.
15. **`EXCEPT` bidirecional** vazio nos dois sentidos.
16. **Chaves idênticas** entre staging e destino.
17. **Zero `NaN`**.
18. **Sinal preservado** — nenhum valor teve o sinal alterado entre staging e destino (§18.5.1).
19. **`source_run_id` e cutoff** gravados na linha publicada.
20. **Watermark avançado somente após o sucesso** de todas as fronteiras (§18.8.7).

**[FATO] `transaction_type` não é verificável na fronteira C.** O fato mensal proposto não armazena essa coluna — sua prova pertence à **fronteira A**. Staging e destino continuam reconciliados no grão mensal, mas não podem provar uma coluna que não guardam, e **não se deve adicioná-la ao fato só para satisfazer um teste**.

**[RECOMENDAÇÃO]** O fato permanece **READY COM RESTRIÇÃO** justamente porque sua finalidade é **custo por coorte do pedido** — visão comercial —, e não taxa financeira reconhecida no statement. Se algum dia for usado como taxa financeira, o veredito deixa de valer.

### 18.10 Restrições para o consumo futuro na UI

**[RECOMENDAÇÃO]** Quando este fato chegar a uma superfície, o contrato de apresentação exige:

| Regra | Detalhe |
|---|---|
| **Rótulo inequívoco** | "**Custo de afiliados por mês do pedido**" — a competência entra no rótulo, não na nota de pé |
| **Aviso de revisão** | Nota fixa de que os valores **podem ser revisados após o fechamento do mês** (34,0% das transações mudam após 30 dias; 16,6% após 90) |
| **Componentes separados** | **Comissão de criadores**, **Comissão de parceiro afiliado** e **Comissão de afiliados/Ads** — sempre separados. **Proibido** rotular como "agência", "MCN" ou "parceiro gestor de criadores": é inferência, não fato (§18.5) |
| **Sinal** | Armazenado **assinado**. Negativo → exibir magnitude via `-valor`. **Positivo → NÃO exibir como custo**: rotular crédito/reversão e sinalizar revisão. **`abs()` proibido** (§18.5.1) |
| **Sem reconciliação com `total_fees`** | **Proibido** afirmar ou insinuar que o número bate com `total_fees` da Gold — são competências distintas |
| **Sem total agregado** | **Nenhum `affiliate_cost_total`** antes da decisão P2 |
| **Sem retorno** | **Nenhuma** inferência de retorno, ROAS ou ROI de afiliado enquanto não houver receita atribuída |
| **N/D vs N/A** | Marca sem TikTok é **N/A**; competência sem carga é **N/D**. Nunca zero |

### 18.11 Riscos e decisões restantes

| # | Risco / decisão | Estado |
|---|---|---|
| **P1** | Custo de marketplace 0% enriquecido em `nf_vendas_unificada_v2` — omissão, decisão ou impedimento? | **Aberta** — destrava CMV nos três canais |
| **P2** | Quais componentes formam "custo de afiliados" | **Aberta** — bloqueia **`affiliate_cost_total`**, qualquer total agregado e qualquer rótulo que apresente os componentes **somados** como custo único. **Não bloqueia** exibir os três separadamente |
| L0 | Competência de `gold.tiktok_brand_daily.date` | **NÃO DEMONSTRÁVEL** por dentro. Só o dono da transformação externa pode responder. **Deixou de ser bloqueio** do custo por coorte |
| L11 | Timezone de `order_create_time` | Aberta — afeta a fronteira do mês |
| L13 | Linhagem de `gold.tiktok_settlements_summary` | Aberta — bloqueia o produto de dado nº 2 |
| R14 | Publicar mês como definitivo | Mitigado pela política de reafirmação (§18.8) |
| R15 | Alguém somar as duas competências | Mitigado por objetos e nomes separados |
