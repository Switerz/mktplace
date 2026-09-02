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

**[HISTÓRICO] Estado do desenho no checkpoint do UE1-C:**

- A Gold é **comparador inválido** para esta métrica (§18.3) — reconciliá-la **não é** pré-requisito. **→ Continua valendo.**
- O fato comercial está **READY COM RESTRIÇÃO** (§18.6). **→ Continua valendo.**
- *(À época)* Continua **NÃO IMPLEMENTADO**. **→ Superado: o fato foi materializado no UE2-B (§22) e exposto na UI pelo UE3 (§24), versionado no fechamento de 27/08/2026. O "ainda não publicado" desta linha era o estado daquele checkpoint e foi superado pela publicação e pelo smoke da §24.11 (`PASS WITH ISSUE`).**
- A implementação depende dos guardrails da **§18.8** — snapshot consistente, validação de `transaction_type` e persistência de watermark —, não de reconciliar a Gold. **→ Os três guardrails foram implementados (§19) e exercidos na primeira carga (§22).**

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

### C1 — Custo de afiliado do TikTok — **READY COM RESTRIÇÃO** (§18.6) · **IMPLEMENTADO, VERSIONADO E PUBLICADO — `PASS WITH ISSUE`**

> **Estado em 27/08/2026:** o contrato abaixo foi **implementado, versionado e
> publicado**: fato materializado e reconciliado no UE2-B (§22), API e UI
> entregues e validadas no UE3 (§24), e **smoke pós-publicação `PASS WITH
> ISSUE`** com comportamento compatível com `8760f96` (§24.11). A restrição do
> §18.6 e todas as regras do contrato seguem vigentes. **UE2-C não iniciada** e
> **retorno indisponível.**

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

⚠️ **Distinção que vale para toda esta seção:** *READY para implementar o fato* **≠** *fato implementado* **≠** *API disponível* **≠** *UI disponível*. **Nada do que segue está no ar** — e aqui "**no ar**" significa **publicado em produção**, não ausência de implementação: o custo de afiliados **está implementado localmente** (fato, API e UI), como a linha abaixo registra.

**[RECOMENDAÇÃO]** Um item já publicável e um implementado e versionado, aguardando publicação:

| Item | Estado | Depende de |
|---|---|---|
| **Receita atribuída a Ads ÷ GMV** (C2) | **Publicável hoje** — indicador isolado, sem composição, com ressalvas de janela e rateio | nada |
| **Custo de afiliados por mês do pedido** — três componentes separados (C1) | **IMPLEMENTADO, VERSIONADO E PUBLICADO — smoke `PASS WITH ISSUE`.** Camada por camada: **UE2-B** — migration 012 aplicada, fato materializado e primeira carga reconciliada (§22); **UE3** — API e interface implementadas, **QA integrada `PASS`**, versionamento em 27/08/2026 e **backend publicado no Render + frontend publicado pela Vercel**, com smoke read-only confirmando **comportamento compatível com `8760f96`** (§24.11). Obedece §18.10: os três componentes seguem separados e não somados. **Retorno continua indisponível** (sem receita atribuída no grão) e **UE2-C não foi iniciada**, então o frescor exibido é `manual_snapshot` | UE2-B ✔ → UE3 ✔ (publicado) |

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

### Estado atual do roadmap — 27/08/2026

**Leia esta subseção antes do bloco histórico abaixo.** O planejamento original
foi **superado pelos fatos** e é preservado apenas como registro.

| Gate | Estado atual |
|---|---|
| **UE2-B** | **CONCLUÍDO.** Migration 012 aplicada, fato materializado e primeira carga reconciliada (§22) |
| **UE3** | **CONCLUÍDO, VERSIONADO E PUBLICADO.** API, interface e QA integrada (§24), versionado em 27/08/2026, backend publicado no Render e frontend pela Vercel. **Smoke pós-publicação `PASS WITH ISSUE`** (§24.11) |
| **UE2-C** | **NÃO INICIADA.** Rotina e SLA de atualização da fact; por isso o frescor é `manual_snapshot` (§23.12) |
| **UE2-A** | Não iniciado — segue dependendo de L2 |
| **UE4** | Não iniciado |
| **UE5** | Não iniciado |

---

#### ⏳ Bloco histórico — checkpoint anterior à implementação

> **Este bloco descreve o planejamento de antes da implementação e NÃO é o
> estado atual.** Foi **superado** pela primeira materialização registrada na
> **§22** e pela implementação e QA do UE3 registradas na **§24**. Preservado
> porque documenta o raciocínio das revisões 1, 2 e 3.

##### UE2-B — Fato mensal de custo de afiliado do TikTok — *(à época: próximo gate implementável)*

- *(À época)* **Estado: READY COM RESTRIÇÃO · NÃO INICIADO.** Desenho completo em §18.8; critério de aceite em §18.9. **→ Hoje: concluído, ver §22.**
- Não depende de terceiros nem de reconciliar a Gold. Depende dos requisitos obrigatórios de implementação: snapshot consistente (§18.8.3), guardrail de `transaction_type` (§18.8.6) e persistência de watermark (§18.8.7).
- **Restrições que acompanham o gate:** revisão retroativa exige reafirmação de mês publicado; fuso de `order_create_time` não demonstrável; sem `affiliate_cost_total` (P2). **→ As três continuam valendo.**

##### UE2-A — CMV de marketplace *(depende de L2)*

- **Risco principal:** universo da tabela ≠ universo da Torre (§7.1). Reconciliar receita **antes** de qualquer uso do custo.
- **Critério de saída:** os 8 itens da §7.2.

##### UE3 — API e Canais *(à época: dependente de UE2-B)* **→ Hoje: concluído, ver §24.**

##### UE4 — Unit economics por listing *(depende de UE2-A + P4; começar por ML)*

##### UE5 — QA integrado

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

### 18.8 Contrato do fato — especificação executável canônica

⚠️ **[FATO] Estado desde o UE2-B Task 1/2:** esta especificação foi **implementada localmente**, com migration **não aplicada**. A §18.8 continua sendo a **única** especificação executável — divergência entre código e §18.8 é defeito do código. O estado de implementação, as decisões que a §18.8.7 delegou e o que a validação local **não** cobriu estão na **§19**.

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
| Nenhuma chave tocada | comportamento explícito: o watermark **pode** avançar (nada mudou de fato), mas isso precisa ser decisão declarada, não efeito colateral. **Sem caminho de atalho:** o no-op atravessa o mesmo lock, a mesma fotografia e a mesma reconciliação (§18.8.8) |
| Primeira carga | ausência de watermark **exige backfill integral**; nunca tratar "sem watermark" como "desde o início da janela" |
| Representação da ausência | **ausência de LINHA**, nunca linha com coluna nula. `last_successful_upper_bound` e `source_run_id` são `NOT NULL`: uma linha meio-preenchida seria um terceiro estado, e "sem watermark" e "watermark desconhecido" acabariam tratados como a mesma coisa |
| Avanço observável | o resultado é **medido, não presumido**: `cutoff > atual` → atualiza exatamente uma linha e reporta **avançado**; `cutoff = atual` → **no-op idempotente**, reporta **inalterado** e **não** toca `source_run_id` (o run_id gravado continua sendo o da execução que de fato moveu o watermark); `cutoff < atual` → **FALHA**, nunca publica. `rowcount` diferente de 1 falha. Nenhuma mensagem pode dizer "avançado" quando nada mudou |

**Nenhuma decisão de tabela ou arquitetura é tomada nesta correção documental.**

#### 18.8.8 Serialização da execução — o lock vem antes do watermark

⚠️ **[FATO] Acrescentado na correção terminal do UE2-B.** O desenho anterior lia o watermark numa conexão read-only e só tomava o advisory lock no momento de publicar. Isso é insuficiente, e a afirmação anterior de que "não corromperia o destino" **era falsa**.

**O modo de falha concreto:** duas execuções leem o **mesmo** watermark, abrem fotografias diferentes e publicam em qualquer ordem. A execução com a fotografia **mais antiga** pode publicar por último e sobrescrever o fato com dados velhos, enquanto o `ON CONFLICT` monotônico preserva o watermark **novo**. O estado final — **watermark novo com fato antigo** — é o pior possível: o incremental seguinte nunca releria a janela perdida, e o dado ficaria errado permanentemente, sem nenhum sinal de erro.

⚠️ **[FATO] A primeira correção deste defeito usou `pg_advisory_xact_lock` e criou um segundo defeito. A ordem canônica está na §18.8.10.** O lock de transação amarrava a exclusão mútua a uma transação gravável aberta desde o passo 1, que ficava **ociosa** durante toda a leitura da fonte — e o destino encerra sessões ociosas em transação. O princípio abaixo permanece; o mecanismo mudou.

**Princípio invariante:** o advisory lock é adquirido **antes de qualquer leitura de estado, abertura de snapshot ou acesso a dados**, e o watermark autoritativo é lido **depois** dele. Com isso, duas execuções aplicáveis nunca observam o mesmo watermark, e uma fotografia antiga não pode publicar depois de uma nova: a segunda execução só vê o watermark já avançado, e o avanço falha se o cutoff dela for **menor** que o registrado.

**O `WHERE` monotônico permanece como defesa em profundidade, não como substituto.** Ele protege a coluna do watermark; não protegia — e não protege — o fato.

**Execução sem `--apply`:** **zero** advisory lock, **zero** DDL/DML, **zero** staging. Quando ocorre, a leitura do watermark é read-only e **não é autoritativa** — sem lock ela pode envelhecer no mesmo instante.

⚠️ **[FATO] `full` sem `--apply` NÃO toca o destino, nem para ler.** `full` ignora o watermark por definição, então ler o state era **dependência indevida**: fazia o diagnóstico do backfill inicial depender de uma tabela que só existe **depois** da migration — impossível justamente no primeiro `full`, quando o preflight é mais necessário. Em `full` dry-run, `watermark = None` por construção e a única conexão aberta é a fotografia read-only da fonte.

`incremental` sem `--apply` continua lendo o state em read-only e continua **falhando** quando a linha ou a tabela não existe. Nunca cai silenciosamente para `full`: tratar "sem state" como "lê a história inteira" transformaria erro de configuração em **backfill acidental**.

#### 18.8.9 Ordem canônica — advisory lock de SESSÃO

⚠️ **[FATO] `statement_timeout` é POR STATEMENT e não produz teto acumulado.** `read_source_snapshot` executa **sete** consultas sequenciais (prova de sessão, limites, tipos, população, chaves, recomposição, totais). Reduzir `SOURCE_STATEMENT_TIMEOUT` de 600 s para 180 s **não** limitaria a 180 s o tempo em que a transação gravável do destino ficaria aberta — limitaria cada consulta isoladamente, e sete consultas de até 180 s excedem com folga os 300 s de `idle_in_transaction_session_timeout`. A recomendação anterior de "180 s deixa 120 s de margem" **estava errada** e foi retirada.

A correção é estrutural, não paramétrica: **eliminar a transação ociosa**, em vez de tentar caber nela.

**Ordem obrigatória para execuções com escrita. UMA SÓ conexão com o destino:**

```
 1. abrir a UNICA conexao do destino, em AUTOCOMMIT
 2. adquirir pg_advisory_lock(K) com lock_timeout finito   <- lock de SESSAO
 3. ler o watermark autoritativo sob o lock, SEM transacao aberta
 4. determinar o lower bound
 5. abrir a fotografia read-only REPEATABLE READ na fonte
 6. ler a fonte inteira dentro da fotografia
 7. validar integralmente EM MEMORIA          <- destino ainda intocado
 8. confirmar que ESTA sessao esta viva e ainda detem o lock
 9. desligar o autocommit NA MESMA conexao -> transacao gravavel
10. reler o watermark (FOR UPDATE) e confirmar que NAO mudou
11. staging, publicacao, reconciliacao e watermark na MESMA transacao
12. commit unico
13. devolver a conexao ao autocommit
14. finally: liberar o advisory lock de sessao e fechar a conexao
```

| Propriedade | Como é garantida |
|---|---|
| **Zero transação ociosa** | A conexão começa em `autocommit`; o psycopg2 não abre transação implícita, então a sessão nunca fica em `idle in transaction` durante a leitura da fonte. A transação gravável existe apenas dos passos 9 a 12 |
| **Sessão única** | O advisory lock pertence à **sessão**. Lock e publicação na mesma conexão tornam "o lock está vivo" e "posso escrever" a **mesma condição**. Se a conexão cai, perdem-se os dois juntos, e não existe segunda conexão para onde escapar — `_neon_writable` foi **removida** do módulo, então não há função capaz de abrir outra |
| **Exclusão mútua preservada** | Locks consultivos de **sessão** e de **transação** compartilham o mesmo espaço de chaves e o mesmo gestor de locks — diferem só em *quando* são liberados. Logo `pg_advisory_lock(K)` e `pg_advisory_xact_lock(K)` **conflitam entre si**, e versão antiga e nova não se sobrepõem durante o rollout |
| **Espera finita** | `lock_timeout` de sessão na aquisição (em autocommit não há transação a que prender um `SET LOCAL`) e `SET LOCAL lock_timeout` na transação gravável. Não é `0` e não toca `idle_in_transaction_session_timeout`. Zero loop, retry, sleep ou backoff |
| **Zero DDL/DML se a fonte falhar** | Os passos 7 e 8 rodam antes do 9: dado inválido, ou lock perdido, nunca chegam a abrir transação. Não é rollback — é ausência de transação |
| **Liberação em todos os caminhos** | `finally` chama `pg_advisory_unlock`, que **nunca levanta** (rodaria com exceção em voo e mascararia a causa). Fechar a conexão libera o lock no próprio servidor |

⚠️ **[FATO] Correção factual — o que o `FOR UPDATE` protege, e o que NÃO protege.** Uma versão anterior desta seção afirmava que a releitura do watermark "adquire lock de linha no `sync_state`" e que isso fechava a janela de queda da conexão. **Era falso em duas frentes:**

1. `read_watermark` era um `SELECT` **simples**, sem `FOR UPDATE` — não adquiria lock de linha nenhum.
2. A publicação rodava numa **segunda** conexão, então a queda da conexão do lock liberaria o lock no servidor enquanto a outra seguia perfeitamente capaz de escrever.

Além disso, mesmo **com** `FOR UPDATE` o problema não estaria resolvido: `FOR UPDATE` trava a **linha existente**, e na **primeira carga** a linha do `sync_state` ainda não existe — não há tupla a travar.

O desenho atual não depende disso. `FOR UPDATE` **foi** adicionado à releitura e é defesa complementar sobre linha existente; quem garante a exclusão mútua — inclusive na primeira carga, quando não há linha — é o **advisory lock de sessão mantido na mesma conexão que publica**.

**[FATO] Nenhuma proteção do destino foi desligada.** `idle_in_transaction_session_timeout` não foi alterado e nenhum `SET ... = 0` foi usado.

**[FATO] `SOURCE_STATEMENT_TIMEOUT` permanece em 600 s.** Depois desta correção ele voltou a ser exclusivamente um limite de proteção da **fonte**, sem relação com o timeout do destino. Alterá-lo passaria a exigir justificativa como limite da fonte — e a medição disponível (43,63 s para a leitura integral) não sustenta apertá-lo.

#### 18.8.10 Fonte completamente vazia — e o que NÃO é fonte vazia

⚠️ **[FATO] `MAX(updated_at) IS NULL` NÃO significa fonte vazia.** Pode ser tabela vazia **ou** tabela com linhas e `updated_at` nulo. Tratar os dois como equivalentes seria catastrófico no `full`, que esvazia o destino quando a fonte está vazia: uma coluna de watermark momentaneamente nula apagaria o fato inteiro.

A captura inicial obtém, na **mesma** consulta e fotografia, `COUNT(*)`, `COUNT(updated_at)` e `MAX(updated_at)`, **antes** de qualquer filtro de marca ou de `transaction_type` e antes de qualquer escrita:

| Observação | Decisão |
|---|---|
| `total = 0` | fonte **comprovadamente** vazia (a consulta executou; timeout, permissão ou falha de conexão levantam exceção e nunca chegam aqui) |
| `total > 0` e `count(updated_at) < total` | **FALHA de contrato** — watermark técnico não pode ter nulo |
| `total > 0` e `max(updated_at)` nulo | **FALHA** — isto não é fonte vazia |
| `total > 0`, todos não nulos, `max` válido | segue |

**Semântica por modo, com fonte comprovadamente vazia:**

- **`incremental` → FALHA explícita.** Não se infere hard delete total: "a fonte esvaziou" e "a janela não mudou" são indistinguíveis para o incremental, e apagar o fato com base nessa ambiguidade destruiria a história por causa de um `TRUNCATE` acidental a montante ou de uma réplica apontada para o banco errado. Fato e watermark ficam intactos; a orientação é confirmar operacionalmente e então rodar `full`.
- **`full` com `--apply` → esvazia o fato e REMOVE a linha do state**, na mesma transação e sob o mesmo lock. Nenhum cutoff é fabricado, nenhum `now()` é usado, nenhum watermark é gravado. A próxima execução incremental falha e exige `full`.
- **`full` sem `--apply` → apenas diagnostica** que o `full` esvaziaria o fato.

**Caso distinto:** fonte **não** vazia com zero marca allowlisted. Aqui existe cutoff válido, o `full` esvazia o fato pela via normal de publicação e o watermark **avança** para o cutoff global. Nada é fabricado.

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
12. **Cutoff utilizado** — nenhuma linha da staging tem `source_max_updated_at` **acima** do `current_upper_bound` capturado, e o cutoff usado fica registrado na execução.

    ⚠️ **[FATO] Correção da redação anterior, feita no UE2-B.** Este item exigia igualdade estrita — `max(updated_at)` na staging **igual** ao cutoff. **Igualdade estrita não é invariante**, e a diferença é legítima: o cutoff é o teto do snapshot sobre a tabela **inteira** (§18.8.3 passo b, deliberadamente sem filtro, para que a validação de tipos não fique cega a linhas acima de um teto filtrado), enquanto a staging cobre apenas marcas allowlisted e `transaction_type` allowlisted. Se a linha que detém o `max(updated_at)` global pertencer a uma marca fora de escopo, a staging fica **legitimamente abaixo** do cutoff, e o critério original reprovaria uma execução correta.

    O que **é** invariante — e o que de fato prova a fotografia — é que nada na staging está **acima** do cutoff: estar acima significaria leitura fora do snapshot.
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

⚠️ **[FATO] O ESCOPO da fronteira C depende do modo — correção terminal do UE2-B.** No `incremental` a staging contém **somente as chaves tocadas**, enquanto o destino guarda toda a história. Comparar staging contra o destino **inteiro** faria `destino EXCEPT staging` encontrar, **por construção**, cada linha histórica não tocada: o critério reprovaria toda execução correta e era, na prática, inexecutável.

| Modo | Comparação |
|---|---|
| `full` | staging × **destino inteiro** — a staging cobre todas as chaves, então qualquer linha sobrando é defeito (hard delete não reparado) |
| `incremental` | staging × **projeção do destino restrita às chaves da staging**, via semijoin explícito por `(ref_month, brand)`. Nunca lista de valores interpolada no texto do SQL |

O mesmo escopo se aplica à verificação de `NaN`: no `incremental`, um `NaN` numa linha histórica não tocada não foi introduzido por aquela execução e não pode ser corrigido por ela — verificar o destino inteiro faria toda execução futura falhar por um defeito antigo, sem ação possível. A verificação de **sinal** já é corretamente escopada nos dois modos, porque faz `JOIN` pelas chaves da staging.

**[FATO] Igualdade de chaves não basta: a cardinalidade é obrigatória.** Uma linha **duplicada** no destino para uma chave **tocada** tem a mesma chave, então nenhum `EXCEPT` de chaves a encontraria. Só a contagem (`linhas_no_escopo = linhas_na_staging`) revela.

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

---

## 19. UE2-B Task 1/2 — implementação local do fato

⚠️ **[FATO] Nada foi aplicado em banco.** Esta task produziu código, uma migration **não aplicada** e validação **local**. Nenhum DDL, DML, backfill, deploy, Scheduler ou API foi executado. A aplicação é escopo da Task 2/2.

**A §18.8 continua sendo a única especificação executável.** Esta seção registra o **estado de implementação** e as decisões que a §18.8.7 deixou explicitamente para o UE2-B tomar. Divergência entre código e §18.8 é defeito do código.

### 19.1 Artefatos

| Artefato | Papel |
|---|---|
| `apps/api/alembic/versions/012_create_fact_tiktok_affiliate_cost_order_monthly.py` | Cria `marts.fact_tiktok_affiliate_cost_order_monthly`, o índice `idx_ftacom_brand_ref_month` e `marts.fact_tiktok_affiliate_cost_order_monthly_sync_state`. Head Alembic único e linear: `012 <- 011` |
| `pipelines/sync_tiktok_affiliate_cost_order_monthly.py` | Sync com modos `full` / `incremental`, `--apply` obrigatório para escrever |
| `pipelines/tests/test_sync_tiktok_affiliate_cost_order_monthly.py` | 134 testes, zero acesso real a banco. Concorrência e transação usam um `Recorder` **compartilhado** entre as conexões falsas, para que a ordem relativa dos eventos das duas conexões seja observável |

### 19.2 Decisões que a §18.8.7 delegou ao UE2-B

| Requisito §18.8.7 | Decisão tomada |
|---|---|
| Armazenamento durável | Tabela `marts.fact_tiktok_affiliate_cost_order_monthly_sync_state`, no **mesmo banco do destino** — o watermark comita na **mesma transação** da publicação, então não pode viver em outro engine |
| Identificação | PK `(source_table, target_table)` — um watermark por par, conforme exigido |
| Tipo do valor | `TIMESTAMP` **sem** timezone. Nenhuma conversão |
| Rastreabilidade | Coluna `source_run_id`, `NOT NULL` |
| Momento do avanço | Dentro da transação de publicação, **após** staging materializada, escrita concluída e fronteiras B e C aprovadas |
| Rollback | `ROLLBACK` integral reverte destino **e** watermark juntos, por estarem na mesma transação |
| Nenhuma chave tocada | **DECLARADO: o watermark pode avançar**, e nada é publicado. Não avançar faria a janela crescer indefinidamente relendo o mesmo intervalo vazio. **Sem atalho:** o no-op atravessa o mesmo lock, a mesma fotografia e a mesma reconciliação |
| Primeira carga | `incremental` sem watermark **falha** com instrução de rodar `full`. Nunca trata ausência como janela móvel |
| Leitura autoritativa | **Sob `--apply`, lida DEPOIS do advisory lock, na mesma transação** (§18.8.8). A leitura read-only do modo diagnóstico não é autoritativa |
| Ausência | **Ausência de LINHA.** `last_successful_upper_bound` e `source_run_id` são `NOT NULL` |

### 19.3 Correção terminal — os seis findings

⚠️ **[FATO] Uma afirmação anterior desta seção estava ERRADA e foi removida.** Dizia que watermark monotônico via `ON CONFLICT` bastava e que a concorrência "não corromperia o destino". É falso: o `ON CONFLICT` protege a **coluna do watermark**, não o **fato**. Duas execuções podiam observar o mesmo watermark e a mais antiga sobrescrever o fato depois da mais nova, deixando **watermark novo com fato antigo** — estado em que o incremental seguinte nunca releria a janela perdida.

| # | Defeito | Correção |
|---|---|---|
| **F1** | Watermark lido fora da transação e lock só na publicação → concorrência podia sobrescrever dado novo com dado velho | Uma transação gravável; **lock primeiro**, watermark depois (§18.8.8). `publish_in_transaction` não toma lock nem comita — a transação é do orquestrador |
| **F2** | Reconciliação incremental comparava staging parcial com destino **inteiro** → `destino EXCEPT staging` reprovava por construção | Escopo por modo: `full` × destino inteiro, `incremental` × **projeção por semijoin** nas chaves da staging. Somada a prova de **cardinalidade**, que é a única que pega duplicata na mesma chave |
| **F3** | `full` com fonte vazia retornava "nada publicado" e deixava dados fantasma | `full` esvazia o fato e **remove a linha** do state, atomicamente, sem fabricar cutoff. `incremental` **falha** — não infere hard delete total |
| **F4** | Schema permitia nulo em campos que só existem após publicação válida | `NOT NULL` em `source_row_count`, `source_max_updated_at`, `source_run_id` (fato) e em `last_successful_upper_bound`, `source_run_id` (state). Só os **três componentes financeiros** seguem anuláveis |
| **F5** | `MAX(updated_at) IS NULL` era tratado como fonte vazia | `capture_source_bounds` obtém `COUNT(*)`, `COUNT(updated_at)` e `MAX(updated_at)` na mesma consulta; `updated_at` nulo em fonte não vazia é **falha de contrato** (§18.8.10) |
| **F6** | `advance_watermark` sempre declarava sucesso | Resultado **medido**: `avancado` / `inalterado` / falha; `rowcount` obrigatoriamente 1; relatório nunca diz "avançado" quando nada mudou |

**[FATO] Decisões que permaneceram, reconfirmadas.** `full` com zero chave allowlisted **e fonte não vazia** esvazia o fato pela via normal de publicação e avança para o cutoff global válido — caso distinto da fonte vazia, porque aqui existe cutoff e nada é fabricado. A validação de `transaction_type` continua **sem** filtro de marca, seguindo a §18.8.6 literalmente: um tipo novo em marca fora de escopo também falha a execução. É o lado seguro, mas é ruído possível — se se mostrar inviável, a correção é **mudança de contrato**, não liberdade de implementação.

### 19.4 O que a validação local cobriu — e o que não cobriu

**Cobriu:** 134 testes focais passando; `pipelines/` completa com **2646 passed, 1 failed**; `apps/api/tests` com **680 passed, 43 failed, 8 skipped**. A única falha de `pipelines/` é **pré-existente e alheia** (`test_sync_tiktok_serving.py::test_j09_...` afirma `sum(0.1 ×10) != 1.0`, que deixou de valer porque o `sum()` do Python ≥3.12 usa somatório compensado — o arquivo tem zero referências a este módulo, e **não foi corrigido nesta task**). As 43 de `apps/api` são testes de integração que tentam `psycopg2.connect()` real, sem banco no ambiente — **baseline idêntico** ao de antes desta correção. `compileall` limpo; head Alembic único em `012`; `git diff --check` limpo; scan de segredos limpo.

⚠️ **NÃO cobriu, por desenho desta task:**

- **A migration não foi aplicada.** Nenhum `CREATE TABLE` foi executado, então nenhum `CHECK`, PK ou índice foi validado pelo Postgres. Um erro de sintaxe SQL só aparecerá na Task 2/2.
- **Nenhum SQL foi executado contra a fonte.** As consultas de agregação, `unnest` de chaves, `EXCEPT` e `SIGN(...) IS DISTINCT FROM` foram verificadas como **texto e forma**, nunca como plano ou resultado. Que a semântica de `SUM` sobre `->>` preserve nulo é comportamento documentado do Postgres, **não medido aqui**.
- **O isolamento `REPEATABLE READ` da réplica não foi verificado.** O módulo o **exige e comprova em tempo de execução** (`assert_snapshot_session`), mas se a réplica ou um pooler rebaixar o nível, isso só será descoberto na primeira execução real.
- **O comportamento real do advisory lock não foi exercitado.** Os testes provam a **ordem** das chamadas e que a segunda execução não observa o watermark antes do lock; um lock real *bloqueia*, e um teste não pode bloquear — a espera é modelada como exceção. Contenção verdadeira só na Task 2/2.
- ⚠️ **A duração da transação gravável aberta não foi medida.** Ver §19.5: é o risco novo que esta correção introduz.
- **Nenhum valor de negócio foi medido.** Não há número de custo de afiliado produzido por esta task.

### 19.5 Dívida técnica registrada

⚠️ **[FATO] RISCO NOVO, introduzido por esta correção — transação gravável aberta durante toda a leitura da fonte.** A serialização exigida pela §18.8.8 obriga o lock a ser adquirido **antes** da leitura do watermark e mantido até o commit. Consequência: a transação do Neon fica aberta — e **ociosa** — durante toda a leitura do Data Mart, cujo `statement_timeout` é de 600 s.

Se o Neon impuser `idle_in_transaction_session_timeout` **menor** que a duração da leitura da fonte, a transação será encerrada pelo servidor e a execução falhará. Nenhuma escrita parcial resulta disso — o rollback é integral —, mas o sync ficaria inoperante.

**Não medi esse parâmetro**, porque esta task não acessa banco, e **não adicionei** um `SET LOCAL idle_in_transaction_session_timeout` especulativo: mascarar um travamento real com uma configuração que não consigo validar é pior que a falha explícita. **Verificação obrigatória na Task 2/2**, antes de qualquer `--apply`: medir `idle_in_transaction_session_timeout` no Neon e o tempo real da leitura integral da fonte. Se houver conflito, a decisão é de contrato — reduzir a janela lida, ou fixar o timeout de sessão explicitamente.

**[FATO]** O módulo importa `_get_neon_url`, `_get_datamart_url`, `sanitize_error_message`, `sanitize_run_id`, `validate_identifier` e `validate_qualified` de `pipelines/sync_tiktok_serving.py`, em vez de duplicá-los — duplicar as regexes de sanitização garantiria que as duas cópias divergissem, e a atrasada vazaria topologia. **Custo:** este módulo focal passa a depender do import daquele sync, que executa `ZoneInfo("America/Sao_Paulo")` no nível de módulo. **Correção sugerida, fora do escopo desta task:** extrair esses helpers para `pipelines/common/` e reapontar os dois módulos.

---

## 20. UE2-B Task 2/2 Fase A — preflight real read-only

⚠️ **[FATO] Veredito: GO COM RESTRIÇÃO. Escrita NÃO autorizada.** Executado em 2026-08-22. Nenhum DDL, DML, migration, backfill, advisory lock real, deploy, API, UI, Scheduler ou Airflow. Alembic permaneceu em `011` e as duas tabelas de destino continuam inexistentes, verificado **antes e depois** do preflight.

### 20.1 A restrição — o risco previsto na §19.5 se confirmou

**[FATO] `idle_in_transaction_session_timeout` do Neon = `5min` (300 s), contra `SOURCE_STATEMENT_TIMEOUT = 600 s`.**

A serialização da §18.8.8 obriga a transação gravável do Neon a ficar aberta — e **ociosa** — durante toda a leitura da fonte. A configuração do Neon **não cobre** o limite teórico que o próprio módulo permite: uma leitura que se aproxime de 600 s teria a transação encerrada pelo servidor, com o advisory lock liberado no meio. Não há escrita parcial (rollback integral), mas o sync ficaria inoperante.

| Grandeza | Valor |
|---|---|
| Duração medida do `full` dry-run | **43,63 s** |
| Janela ociosa do Neon no `apply` ≈ leitura da fonte | ≈ 44 s |
| `idle_in_transaction_session_timeout` (Neon) | 300 s |
| Margem **hoje** | ≈ 256 s (6,9× de folga) |
| Limite **teórico** permitido por `SOURCE_STATEMENT_TIMEOUT` | 600 s |
| Margem no pior caso | **−300 s (negativa)** |

A margem real é confortável; a **configuração** não é segura. O critério de GO exige `idle timeout = 0` ou `idle timeout > pior caso + 60 s`, e nenhum dos dois se verifica.

**[RECOMENDAÇÃO] Menor correção explícita, a decidir antes da Fase B — não implementada aqui:** reduzir `SOURCE_STATEMENT_TIMEOUT` de 600 s para **180 s**, o que limita o pior caso a 180 s e deixa 120 s de margem sob os 300 s do Neon, mantendo 4× de folga sobre os 43,63 s medidos. É a alteração de **uma constante**, sem mudança de arquitetura. A alternativa — lock consultivo de **sessão** (`pg_advisory_lock`) em vez de lock de transação, permitindo abrir a transação gravável só depois da leitura da fonte — resolve o problema de forma estrutural, mas é mudança de desenho e só se justifica se a fonte algum dia precisar de mais de 180 s.

### 20.2 Correção estreita aplicada nesta fase

`_run_diagnostic` deixou de ler o state no Neon em modo `full` (ver §18.8.8). **Prova empírica, não apenas teste com fake:** o dry-run canônico foi executado com `DATABASE_URL` **deliberadamente ausente** do ambiente e concluiu com `exit 0`. Se tivesse tocado o Neon, `_get_neon_url` teria levantado erro. Corrigida também a afirmação factual anterior de "lock como primeira ação": o `SET LOCAL statement_timeout` o precede — a redação correta é que o lock precede **qualquer leitura de estado, abertura de snapshot ou acesso a dados**.

### 20.3 Contrato da fonte — medido

| Fato | Valor |
|---|---|
| `silver.stg_tiktok_payments_by_order` | acessível, `SELECT` concedido |
| Fotografia | `isolation = repeatable read`, `read_only = on` — **confirmado dentro da transação** |
| Tipos de `order_create_time` / `updated_at` | `timestamp without time zone`, precisão 6 — conforme §18.8.1 |
| `COUNT(*)` | 2.129.049 |
| `COUNT(updated_at)` | 2.129.049 — **zero nulos** |
| `MAX(updated_at)` | 2026-08-22 00:04:12.593829 (mín. 2026-03-12 20:21:21) |
| `order_create_time` | 2025-06-04 12:06:03 a 2026-08-20 20:22:50 |
| `transaction_id` único | **sim**, 2.129.049 distintos em 2.129.049 linhas |
| Nulos em `transaction_id`, `order_create_time`, `brand`, `fee_breakdown` | **zero em todos** |
| `transaction_type` | **um único valor: `ORDER`** (2.129.049). Nada fora da allowlist |
| Marcas | 8 distintas; as **5 oficiais todas presentes**; fora de escopo: `gocase` 79.629, `azbuy` 24.662, `denavita` 4.746 |
| Excluídas pela allowlist | 109.037 |
| População do fato | **2.020.012** |
| Chaves de competência | **70** `(ref_month, brand)` |

**[FATO] As três chaves financeiras estão presentes em 100% da população** — e a **chave proibida `affiliate_commission_amount` também**, em 100%. O risco de dupla contagem é real e concreto, não teórico: o guardrail `assert_no_forbidden_component` é o que impede que ela entre no cálculo. Sua presença foi medida; ela **nunca foi somada**.

### 20.4 Sinais e nulos — medidos

| Componente | nulos | zeros | positivos | negativos | NaN | Soma assinada |
|---|---|---|---|---|---|---|
| `affiliate_creator_commission` | 0 | 1.038.581 | **0** | 981.431 | 0 | **−5.504.405,93** |
| `affiliate_partner_commission` | 0 | 1.689.042 | **0** | 330.970 | 0 | **−3.056.314,20** |
| `affiliate_ads_commission` | 0 | 1.728.438 | **0** | 291.574 | 0 | **−722.584,32** |

⚠️ **[FATO] Nenhum nulo e nenhum positivo na população atual.** A distinção nulo × zero está implementada e testada, mas **não é exercitada por estes dados** — a chave JSON está sempre presente. Igualmente, a semântica de crédito/reversão (§18.5.1) não tem nenhuma ocorrência hoje. Ambas seguem corretas por contrato, e ambas permanecem **não observadas na prática**.

**Contraprova independente:** as três somas acima foram medidas por consulta escrita à parte e conferem **exatamente** com a fronteira B do dry-run, incluindo `source_row_count = 2.020.012`.

### 20.5 Plano de consulta

**[FATO] As três consultas mais caras fazem `Seq Scan`; nenhum índice é usado.**

| Consulta | Estratégia | Custo estimado | Linhas est. |
|---|---|---|---|
| Descoberta de chaves | `HashAggregate` ← `Seq Scan` | 512.589 | 2.019.790 |
| Recomposição mensal | `HashAggregate` ← `Hash Semi Join` ← `Seq Scan` + `Function Scan` (70) | 582.693 | 706.927 |
| Totais de detalhe | `Aggregate` ← `Gather` ← `Hash Semi Join` ← **`Parallel Seq Scan`** | 465.121 | 294.553 |

O `Function Scan` de 70 linhas confirma que as chaves entram por `unnest` parametrizado, nunca interpoladas. **Risco de timeout:** baixo hoje (43,63 s contra 600 s = 7,3%), mas o custo é **linear no tamanho da tabela** — sem índice, cada execução relê tudo. Com a fonte a 2,1 M linhas isso é aceitável; convém reavaliar antes de dobrar de tamanho.

**Concorrência:** na fonte, 41 sessões, 1 ativa, 0 em transação, **0 locks na tabela fonte** por outras sessões. No Neon, 5 sessões, nenhuma ativa ou em transação, **0 advisory locks** no servidor.

---

## 21. UE2-B — correção transacional terminal (advisory lock de sessão)

⚠️ **[FATO] A restrição da §20.1 foi eliminada estruturalmente — e a recomendação que eu havia dado para resolvê-la estava errada.** Nenhuma migration, DDL, DML, backfill, advisory lock real, commit, push ou deploy. Alembic permanece em `011`.

### 21.1 O finding — confirmado

Eu havia recomendado reduzir `SOURCE_STATEMENT_TIMEOUT` de 600 s para 180 s, afirmando que isso deixaria "120 s de margem" sob os 300 s de `idle_in_transaction_session_timeout` do Neon. **Está errado.** `statement_timeout` é aplicado **a cada statement**, e `read_source_snapshot` executa **sete** consultas sequenciais. Sete statements de até 180 s não têm teto acumulado de 180 s — têm teto de 1.260 s. A afirmação só valeria se o snapshot fosse um único statement, ou se existisse um deadline acumulado comprovado. Nenhum dos dois existe.

A correção certa não é paramétrica: é **eliminar a transação ociosa**, não tentar caber nela.

### 21.2 Antes e depois

| | Antes (§18.8.8, `pg_advisory_xact_lock`) | Depois (§18.8.9, `pg_advisory_lock`) |
|---|---|---|
| Lock | de **transação** | de **sessão**, em conexão dedicada em `autocommit` |
| Transação gravável | aberta do passo 1 até o commit | aberta só dos passos 8 a 11 |
| Durante a leitura da fonte | transação gravável **ociosa** (exposta aos 300 s) | **nenhuma transação gravável existe** |
| Validação em memória | dentro da publicação | passo 7, **antes** de abrir a transação |
| Se a fonte falhar | rollback de transação já aberta | nenhuma transação chegou a existir |
| Espera pelo lock | indefinida | `lock_timeout` finito, fail-fast |
| Liberação | implícita no fim da transação | `pg_advisory_unlock` em `finally`; queda da conexão libera no servidor |

### 21.3 Semântica do lock e rollout

Locks consultivos de **sessão** e de **transação** compartilham o mesmo espaço de chaves e o mesmo gestor de locks; diferem apenas em *quando* são liberados. Portanto `pg_advisory_lock(K)` e `pg_advisory_xact_lock(K)` **conflitam entre si** — uma execução da versão antiga e uma da nova não podem se sobrepor durante o rollout. A chave permanece a mesma (`ADVISORY_LOCK_KEY`), deliberadamente.

⚠️ **A janela residual descrita aqui na primeira redação NÃO estava fechada, e a explicação dada estava errada.** Ver §21.7.

### 21.7 Terceira correção — sessão única elimina a janela residual

**[FATO] O que estava errado.** Esta seção afirmava que a releitura do watermark adquiria lock de linha no `sync_state` e assim fechava a janela de queda da conexão de lock. Duas falsidades:

1. `read_watermark` era `SELECT` **simples**, sem `FOR UPDATE` — nenhum lock de linha era adquirido.
2. A publicação rodava numa **segunda** conexão. O advisory lock pertence à sessão: a queda da conexão do lock o liberaria no servidor, enquanto a outra conexão continuava capaz de escrever. "Perder o lock numa conexão e seguir escrevendo pela outra" era possível.

E `FOR UPDATE`, mesmo se presente, não bastaria: ele trava **linha existente**, e na **primeira carga** a linha do `sync_state` não existe.

**[FATO] A correção.** Uma só conexão com o destino, do início ao fim. Ela adquire o lock, lê o watermark em autocommit, atravessa a leitura da fonte sem transação aberta, confirma que ainda detém o lock (`assert_still_holding_lock`, via `pg_locks` comparado a `pg_backend_pid()`), e só então desliga o autocommit **nela mesma** para publicar.

| Consequência | Por quê |
|---|---|
| Não há como perder o lock e continuar escrevendo | Lock e escrita vivem na mesma sessão: caem juntos |
| A próxima operação após uma queda **falha** | Não existe reconexão, retry ou segunda conexão — `_neon_writable` foi removida do módulo |
| Primeira carga deixa de ser caso especial | A proteção é o advisory lock, não um lock de linha que não existiria |
| A janela ociosa continua inexistente | A transação gravável só nasce depois de a fonte estar lida e validada |

`FOR UPDATE` **foi** adicionado à releitura, e a documentação agora declara com precisão o que ele cobre: linha existente, como defesa complementar — nunca como o mecanismo de exclusão mútua.

**Validação:** **168 testes focais** (antes 160), incluindo prova de conexão única, identidade de sessão entre lock e publicação, `autocommit` desligado só após snapshot e validação, confirmação de posse do lock, queda de conexão sem abertura de segunda, `FOR UPDATE` presente apenas na releitura, e primeira carga sem linha permanecendo serializada. Suítes: **335 passed, 1 failed** (a pré-existente do somatório em Python ≥3.12, fora de escopo).

### 21.4 Comportamento por classe de falha

| Falha | Consequência |
|---|---|
| Aquisição do lock (timeout ou conexão) | Erro sanitizado, exit 2. Zero conexão gravável, zero escrita |
| `incremental` sem watermark | Recusa **sob o lock**, antes de abrir a fotografia. Nunca cai para `full` |
| Leitura da fonte (tipos, nulos, duplicidade, marca, fonte vazia em `incremental`) | Falha no passo 6 ou 7. **A transação gravável nem é aberta** — zero DDL/DML por construção, não por rollback. Lock liberado |
| Watermark divergente no passo 9 | Rollback integral, nada publicado, lock liberado |
| Publicação, reconciliação ou watermark | Rollback integral (fato **e** watermark juntos), lock liberado |
| Sucesso | Commit único, e só **depois** o unlock |

### 21.5 Validação

**160 testes focais passando** (antes 143), com 17 novos cobrindo os requisitos desta correção. Suítes: focais + `test_s3_migrations` + serving = **327 passed, 1 failed** — a falha é a pré-existente de somatório do Python ≥3.12 em `test_sync_tiktok_serving.py`, **classificada separadamente e não corrigida aqui**. `compileall` exit 0; `git diff --check` limpo; scan de DSN, IP, segredos e caminhos pessoais limpo nos arquivos de produção.

⚠️ **O dry-run real de confirmação NÃO pôde ser executado.** A VPN caiu entre a Fase A e esta correção, e o Data Mart ficou inalcançável (o Neon segue acessível). A execução falhou na conexão com a **fonte**, com a mensagem sanitizada de categoria fixa — sem host, IP ou porta. Isso ainda confirma que o `full` dry-run não depende do Neon (a execução passou do ponto em que o código pré-correção teria falhado por `DATABASE_URL` ausente), mas **não** reconfirma população, chaves nem duração. Os números de população (2.020.012), chaves (70) e duração (43,63 s) continuam sendo os da §20, medidos sobre o caminho de diagnóstico, que esta correção não altera em substância — a troca de lock afeta apenas `_run_apply`.

### 21.6 `SOURCE_STATEMENT_TIMEOUT`

**Mantido em 600 s, deliberadamente.** Depois desta correção ele voltou a ser exclusivamente um limite de proteção da **fonte**, sem relação com o timeout do destino. Apertá-lo exigiria justificativa como limite da fonte, e a única medição disponível (43,63 s para a leitura integral de 2,1 M linhas) não a sustenta. Um teste fixa o valor para que qualquer alteração futura seja consciente.

---

## 22. Primeira materialização — 25/08/2026

**[FATO]** A migration `012` foi aplicada e a **primeira carga full** foi executada **uma única vez**. Esta seção registra os números efetivamente publicados.

### 22.1 O que foi executado

| Item | Valor |
|---|---|
| Migration | `012`, aplicada em **25/08/2026**, `011 → 012`, uma única tentativa de upgrade, head único e linear |
| Objetos criados | `marts.fact_tiktok_affiliate_cost_order_monthly`, `..._sync_state`, índice `idx_ftacom_brand_ref_month` — e nada mais; nenhuma tabela preexistente alterada |
| Carga | `--mode full --apply`, **uma única execução**, zero retry |
| `source_run_id` | **`ue2b-first-full-20260825`** |
| Cutoff publicado | **`2026-08-25 00:11:55.377962`** |
| Chaves publicadas | **70** `(ref_month, brand)` |
| Marcas | **5** — as oficiais de `BRANDS_IN_SCOPE` |
| Competências | **2025-06** a **2026-08** |
| `source_row_count` agregado | **2.046.208** |

### 22.2 Os três componentes — valores publicados

| Componente | Valor |
|---|---|
| `affiliate_creator_commission` | **−R$ 5.504.405,93** |
| `affiliate_partner_commission` | **−R$ 3.110.478,68** |
| `affiliate_ads_commission` | **−R$ 738.193,33** |

⚠️ **[FATO] O sinal negativo é o sinal da FONTE, não margem.** Estes valores vêm assinados de `fee_breakdown` e foram publicados **exatamente como vieram** — nenhum `abs()`, nenhuma inversão, nenhuma normalização. Negativo aqui significa débito na perspectiva do repasse; **não** é resultado, não é margem e não é lucro.

**[FATO] Os três componentes continuam separados, e não existe `affiliate_cost_total`** — nem na tabela, nem em nenhuma consulta. Qual subconjunto constitui "custo de afiliado" segue sendo o ponto aberto **P2** (§18.11), e enquanto ele estiver aberto nenhum total agregado pode ser materializado nem apresentado.

**[FATO] Não há retorno de afiliado disponível.** Não existe receita atribuída a afiliado nesta fonte, logo ROAS, ROI ou qualquer razão de retorno são **inderiváveis** — não por falta de implementação, mas por falta de numerador.

### 22.3 Reconciliação

Leitura **pós-commit independente**, com a fonte relida no mesmo cutoff:

| Prova | Resultado |
|---|---|
| Três somas: fonte × fato | **idênticas ao centavo** nas três |
| `source_row_count` × linhas da fonte | 2.046.208 = 2.046.208 |
| Chaves / marcas / competências | 70 = 70; 5 = 5; 2025-06..2026-08 idêntico |
| `EXCEPT` bidirecional | **(0, 0)** |
| PK duplicada / chave nula / `NaN` | 0 / 0 / 0 |
| `ref_month` = primeiro dia do mês | todas |
| Sinais preservados | sim, nos três |
| Watermark em `sync_state` | igual ao cutoff **e** igual a `MAX(source_max_updated_at)` do fato |
| `source_run_id` / `synced_at` | presentes em todas as 70 linhas |
| Advisory lock / sessões `idle in transaction` | liberado / 0 |

### 22.4 Por que 2.157.804 e 2.046.208 são ambos corretos

⚠️ **[FATO] Dois números diferentes aparecem no relatório da carga, e a diferença não é divergência.**

- **2.157.804** — `COUNT(*)` da fonte inteira, e também o total de `transaction_type = ORDER`. Como `ORDER` é o **único** tipo presente na tabela, esses dois números coincidem. É a população **global observada**.
- **2.046.208** — a população **allowlisted**, após filtrar `brand IN BRANDS_IN_SCOPE`. É este o número materializado em `source_row_count`.

A diferença de **111.596 linhas** são marcas fora de escopo (`gocase`, `azbuy`, `denavita`), que a Torre não publica. Qualquer comparação futura entre o fato e a fonte precisa aplicar a allowlist de marcas, ou encontrará essa lacuna e a interpretará como perda de dado.

### 22.5 Correção do relatório do watermark

**[FATO]** A execução real imprimiu `watermark: avancado para None`. Era defeito **exclusivamente de relatório**: `watermark_novo` era gravado em `relatorio["publicacao"]` e `_print_report` o lia do topo de `relatorio`. **O valor persistido estava correto** — confirmado pela leitura pós-commit, e o `NOT NULL` da coluna impediria nulo de qualquer forma. Corrigido em commit separado, sem tocar transação, watermark, cutoff, staging, reconciliação ou SQL. O relatório passou também a **nunca fabricar valor**: se o watermark avançar e o valor não chegar ao relatório, a saída diz isso em vez de imprimir `None`.

### 22.6 Restrições que seguem valendo para o consumo

A §18.10 permanece integralmente em vigor. Antes de expor em API ou em Canais: rótulo com a competência ("custo de afiliados por mês do pedido"), aviso fixo de revisão pós-fechamento, **três componentes sempre separados**, nenhum total agregado enquanto P2 estiver aberto, nenhuma inferência de retorno, e `N/A` para marca sem TikTok distinto de `N/D` para competência sem carga — nunca zero.


---

## 23. UE3 Task 1/3 — contrato de exposição em Canais

⚠️ **[HISTÓRICO] No checkpoint da Task 1/3, nada havia sido implementado.** Esta seção é o desenho read-only daquele momento: zero código, zero escrita em banco, zero migration, zero deploy. **O estado atual da implementação e da QA está na §24** — a Task 2/3 implementou este contrato e a Task 3/3 o validou como `PASS`. As regras de verdade descritas aqui **continuam vigentes**; apenas o "nada foi implementado" deixou de valer.

### 23.1 O que a fact realmente tem — medido em 2026-08-27

| Fato | Valor |
|---|---|
| Grão / PK | `(ref_month, brand)`, **70 linhas, zero duplicidade** |
| Competências | **15 meses**, `2025-06-01` a `2026-08-01` |
| Marcas | 5 — `apice, barbours, kokeshi, lescent, rituaria` |
| `source_row_count` agregado | 2.046.208 |
| `source_run_id` | **um único**: `ue2b-first-full-20260825` |
| `synced_at` | `2026-08-25 19:33:26 UTC` (idêntico em todas as linhas) |
| Watermark (`sync_state`) | `2026-08-25 00:11:55.377962` |
| `NaN` / nulos nos três componentes | **zero em ambos** |

**Sinais medidos — todos os valores são zero ou negativos, nenhum positivo:**

| Componente | zeros | negativos | soma | mín. |
|---|---|---|---|---|
| `affiliate_creator_commission` | 8 | 62 | −5.504.405,93 | −746.997,01 |
| `affiliate_partner_commission` | 10 | 60 | −3.110.478,68 | −417.817,17 |
| `affiliate_ads_commission` | 3 | 67 | −738.193,33 | −121.052,62 |

⚠️ **[FATO] Duas armadilhas de cobertura que o desenho tem de tratar, não esconder:**

1. **Os dois primeiros meses não têm as cinco marcas.** `2025-06` tem **1/5** e `2025-07` tem **4/5**; os outros treze têm 5/5. Não é defeito — as marcas entraram no TikTok em datas diferentes —, mas significa que `2025-06` **não é comparável** com `2026-03`, e uma soma que os misture sem dizer isso é enganosa.
2. **`2026-08` tem `creator_commission` exatamente `0`**, enquanto `partner` e `ads` são não-nulos no mesmo mês. Combinado com a medição do DQ-TK1 (26.196 linhas novas, todas com creator zero), isso indica que **a comissão de criador do mês corrente ainda não está registrada na fonte** — não que ela não exista. Exibir "R$ 0,00" ali, sem marcar o mês como aberto, afirmaria algo que o dado não sustenta.

### 23.2 Atualização automática — NÃO EXISTE

**[FATO] A primeira carga existe; a atualização automática não.** `pipelines/sync_tiktok_affiliate_cost_order_monthly.py` **não é referenciado por nenhum orquestrador**: não aparece em `pipelines/ops/orchestrate.py`, não está em `full_daily`, não está no Scheduler. A única menção fora do próprio módulo e de seu teste é um comentário em `apps/api/tests/test_s3_migrations.py`.

Consequência direta para a UI: `synced_at` é **congelado em 2026-08-25** e vai envelhecer indefinidamente. O bloco **precisa** de estado de frescor visível desde o primeiro dia — não como refinamento futuro, mas porque o dado nasce parado.

**Resolução tomada na Task 2/3.** A alternativa registrada acima era: integrar o incremental ao Scheduler **ou** decidir explicitamente por carga manual. **Optou-se pela segunda.** A implementação segue com `manual_snapshot`, exibe **carimbos próprios** (`affiliate_refreshed_at` e `source_watermark`, grandezas distintas), e **não inventa `fresh`/`stale`** — qualquer limiar seria arbitrário sem rotina nem SLA. Automação e SLA ficam para a **UE2-C**, que **não foi iniciada** (§23.12). Nada foi integrado ao Scheduler, ao `full_daily` ou a qualquer orquestrador.

### 23.3 Sobreposição dos três componentes — NÃO PROVADA

**[FATO] O contrato §18.8.2 prova que os três vêm de chaves JSON **distintas** de `fee_breakdown`, e isso é tudo que ele prova.** Não existe documento que estabeleça que os três são economicamente **disjuntos** — que `partner` não contenha parte de `creator`, por exemplo.

Há evidência de que a fonte **usa representações sobrepostas**: `affiliate_commission_amount` e `affiliate_commission_amount_before_pit` são a MESMA comissão antes e depois de PIT, e somar as duas contaria o mesmo custo duas vezes — é por isso que a primeira é proibida (§18.8.2). Se a fonte sobrepõe nesse eixo, não se pode presumir que não sobreponha em outro.

**Limitação registrada.** Reforça a regra de não somar automaticamente: a soma dos três não é apenas "uma decisão comercial pendente" (P2) — é **aritmeticamente não validada**.

### 23.4 Mapa API → frontend (medido)

| Camada | Artefato |
|---|---|
| Endpoint | `GET /canais` → `apps/api/app/routers/performance.py:321`, `response_model=CanaisResponse` |
| Filtros | `ResolvedFilters` (`channels`, `mkt_ids`, `brands`, `period`, `compare_period`) via `Depends(filters_query)` |
| Período | `EffectivePeriod{start, end, ref_month}` — **`ref_month` só é preenchido quando o período resolve para UM mês calendário** |
| Service | `perf_svc.get_canais(db, channels, year, month, brand_keys, period, compare_period)` |
| Resposta | `ref_month, marketplace, kpis, brands, channel_rows, channel_medians, date_from, date_to, compare_*, filters, refreshed_at` |
| Frontend | `apps/web/app/canais/page.tsx` consome `fetchCanais` de `apps/web/src/lib/api-client.ts` |
| Frescor | `requestKey` (useMemo) × `resolvedKey` (state) → `dataIsFresh`; estado "protegido" (`displayKpis` etc.) vira `null` quando não fresco |
| Estados atuais na página | `"Carregando dados de canais..."`, `"N/D"`, `"Sem dados de canal no período e filtros selecionados."` |

**`EffectivePeriod.ref_month` é o gancho central deste contrato.** Ele já distingue, na arquitetura existente, "um mês calendário" de "intervalo arbitrário" — exatamente a fronteira que a competência mensal exige. Nada novo precisa ser inventado para isso.

#### Guardrails existentes — e por que o caminho aditivo não os viola

Dois testes já proíbem campos de afiliados, e ambos são **estreitos**:

| Teste | Escopo real |
|---|---|
| `apps/api/tests/test_canais_channel_rows.py:359` — `test_nenhum_campo_de_desconto_ou_afiliado_no_payload` | itera **somente** `result["channel_rows"]`. Não alcança `kpis`, `brands` nem o topo |
| `apps/web/tests/canais-channel-metrics.test.ts:49` | valida **somente** as 5 chaves de `CHANNEL_SIGNAL_LABEL` |

**Decisão: bloco NOVO de topo em `CanaisResponse`, e nunca em `channel_rows`.** Três razões convergentes:

1. o guardrail de `channel_rows` é **intencional** e deve continuar valendo;
2. `channel_rows` é a tabela de **comparação entre canais com sinais** — pôr ali um custo que só um canal tem convidaria exatamente ao ranking que este gate proíbe;
3. `kpis` é plano e orientado ao **período**; custo de afiliado é **mensal** e tem disponibilidade **por canal**. Misturar os dois grãos no mesmo objeto perderia a competência.

**Nenhum endpoint novo.** `/canais` já recebe canal, marca e período e já devolve `refreshed_at`; a extensão é aditiva e limpa. Criar `/canais/afiliados` duplicaria resolução de filtros e abriria a porta para os dois divergirem.

### 23.5 Contrato proposto (tipos, sem implementação)

⚠️ **[FATO] Quatro dimensões ORTOGONAIS, não um enum único.** A primeira redação desta seção tinha um `AffiliateDataStatus` que misturava disponibilidade, alinhamento de período, cobertura de marca e frescor num só valor mutuamente exclusivo. Isso é errado: essas condições são **independentes** e coexistem. Um bloco pode ser, ao mesmo tempo, `available` + `complete_month` + `incomplete_brand_coverage` + `manual_snapshot` — e com o enum único era preciso escolher qual verdade contar, escondendo as outras três.

```
# 1. A fonte tem dado para este canal/escopo?
AvailabilityStatus = Literal[
    "available",
    "unavailable_no_source",   # ML/Shopee: fonte equivalente NAO confirmada
    "no_eligible_brand",       # filtro de marca nao intersecta as marcas com dado
    "error",                   # falha ao ler a fact
]

# 2. O periodo pedido fecha competencia mensal?
PeriodStatus = Literal[
    "complete_month",          # exatamente um mes calendario, fechado
    "complete_months",         # varios meses calendario, todos fechados
    "partial_month",           # mes corrente, ou watermark antes do fim do mes
    "not_month_aligned",       # filtro diario ou intervalo que nao fecha mes
]

# 3. A competencia tem as cinco marcas na fact?
CoverageStatus = Literal[
    "complete",
    "incomplete_brand_coverage",
    "unknown",
]

# 4. Quao recente e' a fotografia? (ver 23.5.2 — NAO ha limiar temporal ainda)
FreshnessStatus = Literal[
    "manual_snapshot",         # carga manual, sem rotina: o estado de HOJE
    "fresh",                   # somente apos UE2-C definir SLA
    "stale",                   # somente apos UE2-C definir SLA
    "unknown",
]

# Retorno: indisponibilidade TIPADA, nunca so' texto livre (ver 23.5.3)
ReturnAvailability = Literal["unavailable_no_attributed_revenue"]
```

```
AffiliateCostRow:
    channel: str                                  # "tiktok" | "mercadolivre" | "shopee"
    brand: str
    ref_month: str                                # "YYYY-MM", SEMPRE explicito
    creator_commission_signed: float | None        # lancamento contabil ASSINADO
    partner_commission_signed: float | None        # lancamento contabil ASSINADO
    affiliate_ads_commission_signed: float | None   # lancamento contabil ASSINADO
    coverage_status: CoverageStatus                # cobertura DESTA competencia
    brands_present_in_month: int                   # quantas das 5 tem linha no mes

AffiliateChannelStatus:
    channel: str
    availability_status: AvailabilityStatus
    reason_note: str                               # curta, sem numero fabricado

AffiliateCostsBlock:
    # --- as quatro dimensoes, independentes ---
    availability_status: AvailabilityStatus        # agregado do escopo
    period_status: PeriodStatus
    coverage_status: CoverageStatus                # pior caso entre as competencias
    freshness_status: FreshnessStatus

    # --- dado ---
    rows: list[AffiliateCostRow]                   # UMA linha por (canal, marca, competencia)
    channels: list[AffiliateChannelStatus]         # todos os canais do filtro, inclusive indisponiveis
    months_included: list[str]                     # METADADO de auditoria; ver 23.5.4

    # --- frescor PROPRIO do bloco; ver 23.5.2 ---
    affiliate_refreshed_at: str | None             # MAX(synced_at) da fact NO ESCOPO retornado
    source_watermark: str | None                   # last_successful_upper_bound do sync_state

    # --- retorno: indisponibilidade tipada ---
    return_availability: ReturnAvailability
    return_note: str

    # --- notas ---
    source_note: str
    limitation_note: str

CanaisResponse:
    ...campos atuais, INALTERADOS...
    affiliate_costs: AffiliateCostsBlock | None    # aditivo
```

#### 23.5.1 Nomes dizem que o valor é assinado

Os três campos terminam em `_signed` de propósito. O nome carrega a semântica: quem consumir `creator_commission_signed` sabe que recebe um **lançamento contábil assinado** da fonte, não uma magnitude de custo já normalizada. `abs()` é **proibido** em todo o caminho — API e interface (§18.5.1). Ver §23.9 para a regra completa de sinal.

#### 23.5.2 Frescor é do bloco, não de `/canais`

**[FATO] `affiliate_refreshed_at` e `source_watermark` NÃO reutilizam o `refreshed_at` geral de `/canais`.** São grandezas diferentes:

| Campo | Origem | Significa |
|---|---|---|
| `refreshed_at` (existente) | pipeline diário de `/canais` | quando os KPIs de canais foram atualizados |
| `affiliate_refreshed_at` | **`MAX(synced_at)` da fact, no escopo retornado** | quando esta fotografia de afiliados foi gravada |
| `source_watermark` | `last_successful_upper_bound` do `sync_state` | até que ponto da fonte a fotografia leu |

Reutilizar o `refreshed_at` geral classificaria como recente um dado congelado em 2026-08-25 só porque `/canais` respondeu agora — exatamente o erro que este campo existe para impedir.

⚠️ **`freshness_status` é `manual_snapshot` hoje, e não há limiar de `stale`.** Não existe SLA nem rotina para esta carga (§23.2), então qualquer prazo que eu escolhesse seria inventado. `fresh` e `stale` só passam a ser atribuíveis **depois** da frente **UE2-C** (§23.10.1). Até então a interface mostra a **data da fotografia**, sem adjetivo de qualidade.

#### 23.5.3 Retorno: indisponibilidade tipada, sem campo numérico

`return_availability` é enum de **um único valor** — `unavailable_no_attributed_revenue` — e `return_note` traz a explicação legível. A interface declara a indisponibilidade **sem interpretar texto livre**.

**NÃO existem, e não devem ser criados:** `return_amount`, `roi`, `roas`, `attributed_revenue` (nem nulo), nem qualquer campo numérico de retorno. O enum de um valor é deliberado: cria o lugar para *declarar ausência* sem criar o lugar para *guardar número*.

#### 23.5.4 `months_included` é metadado, não convite a somar

Serve para auditoria — dizer quais competências entraram no escopo. **Não** acompanha nenhum agregado multimensal, porque nenhum é devolvido (§23.7).

### 23.6 Os comportamentos decididos

| # | Situação | Comportamento |
|---|---|---|
| 1 | **Mês completo** | `period_status="complete_month"`, `availability_status="available"`, `rows` com uma linha por `(canal, marca)`, `months_included=["YYYY-MM"]` |
| 2 | **Vários meses completos** | `period_status="complete_months"`. **Uma linha por marca × competência**, cada competência auditável isoladamente. **Nenhum agregado multimensal** (§23.7) |
| 3 | **Mês parcial** | `period_status="partial_month"`, **`rows=[]`**. Nenhuma prévia numérica, nenhum rateio, nenhum zero. Só estado + explicação |
| 4 | **Filtro diário / intervalo não alinhado** | `period_status="not_month_aligned"`, **`rows=[]`**. Idem: sem número, sem rateio |
| 5 | **Ausência de linha × zero medido** | Ausência → a chave **não aparece** em `rows`. Zero medido → linha presente com `0`. Nunca um pelo outro |
| 6 | **TikTok × ML/Shopee** | TikTok `available`; ML e Shopee **`unavailable_no_source`**. É "Dados indisponíveis", **nunca** "Não aplicável" — não há prova de que afiliados não existam nesses canais, só ausência de fonte medida |
| 7 | **Múltiplos canais** | `channels` lista **todos** os canais do filtro com seu `availability_status`. Canal sem dado aparece indisponível, **não desaparece** |
| 8 | **Múltiplas marcas** | Uma linha por `(canal, marca, competência)`. Agregação entre marcas é da UI e declara quais marcas entraram |
| 9 | **Cobertura de marca incompleta** | `coverage_status="incomplete_brand_coverage"` **junto com** `available` e `complete_month` — as dimensões coexistem. `brands_present_in_month` diz quantas das 5 |
| 10 | **Fotografia manual** | `freshness_status="manual_snapshot"`, com `affiliate_refreshed_at` e `source_watermark` preenchidos. Sem adjetivo temporal |
| 11 | **Erro da fonte de afiliados** | `availability_status="error"`, `rows=[]`, `affiliate_refreshed_at=None`. **O restante de `/canais` é preservado** (§23.8) |
| 12 | **Nenhuma marca elegível** | `availability_status="no_eligible_brand"`, `rows=[]`. Distinto de erro e de zero |

**Regra final de período — sem contradição.** Valor numérico existe **apenas** para mês(es) calendário **completo(s)**. Mês parcial e intervalo não alinhado devolvem `rows=[]`: nenhuma prévia, nenhum rateio, nenhum zero de preenchimento.

⚠️ **Isto resolve diretamente a armadilha de `2026-08`.** Enquanto agosto/2026 estiver aberto ou em maturação, ele **não pode** exibir `creator = R$ 0,00` como conclusão — e com `rows=[]` não exibe número algum. A primeira redação desta seção dizia "valor exibido com marca visual distinta", o que contradizia a própria regra 11 do gate; a regra agora é uma só.

**Nenhuma razão sobre GMV** enquanto a Fase C do contrato comercial TikTok estiver aberta (auditoria da fonte BLOCKED, ver `docs/dq_tk1_refresh_runbook.md` §5). O bloco expõe valores absolutos assinados e nada mais.

#### 23.6.1 Duas regras de agregação de estado (para a Task 2/3)

Não redesenham o contrato — fixam como as dimensões se combinam quando o escopo tem mais de uma competência ou mais de um canal.

**1. `coverage_status` geral é CONSERVADOR.** Com várias competências no escopo, o `coverage_status` do bloco é `incomplete_brand_coverage` se **qualquer** competência estiver incompleta. Os metadados por competência — `coverage_status` e `brands_present_in_month` de cada `AffiliateCostRow` — permanecem a **evidência detalhada**, e é neles que a interface diz *quais* competências estão incompletas.

O motivo é assimetria de dano: rotular como completo um escopo que contém `2025-06` (1/5 marcas) esconderia a lacuna; rotular como incompleto um escopo com uma única competência incompleta apenas convida a olhar o detalhe. O erro conservador é recuperável, o otimista não.

**2. Em filtro multicanal, cada entrada de `channels` é AUTORITATIVA.** O `availability_status` agregado do bloco nunca sobrescreve o status por canal, e **disponibilidade do TikTok jamais transforma ML ou Shopee em disponíveis**. A interface lê o status de cada canal na sua própria entrada; o agregado serve apenas para decidir se o bloco tem algo a mostrar.

Sem esta regra, um escopo com TikTok disponível poderia render um bloco `available` que a interface leria como "os três canais têm dado" — precisamente a inferência que o item 6 da §23.6 proíbe.

### 23.7 Nenhuma soma — nem entre componentes, nem entre meses

Três proibições distintas, todas em vigor:

1. **Sem soma dos três componentes.** Nenhum `affiliate_cost_total`, em nenhum nível. Qual subconjunto constitui "custo de afiliado" é o ponto aberto **P2**, e a ausência de sobreposição entre eles **não está provada** (§23.3) — a soma não é só decisão comercial pendente, é aritmeticamente não validada.
2. **Sem agregado multimensal.** Vários meses devolvem linhas separadas por competência. Nenhum `total`, nenhuma média, nenhum campo derivado de somar competências. Cada competência permanece auditável individualmente.
3. **Sem campo derivado.** Nenhum percentual, razão, índice ou composição calculada a partir dos três componentes.

Se a UI algum dia precisar somar, soma explicitamente, declara **quais competências e quais componentes** entraram, e assume a decisão. O contrato de dados não a toma por ela.

### 23.8 Isolamento de falha

**[FATO] Falha na consulta de afiliados NÃO derruba `/canais`.** O bloco é aditivo e opcional: em erro, devolve `availability_status="error"` com `rows=[]`, e `kpis`, `brands`, `channel_rows`, `channel_medians` e `refreshed_at` seguem intactos no payload.

Isso é requisito de contrato, não detalhe de implementação: `/canais` serve KPIs que a operação usa hoje, e um bloco novo — alimentado por uma fact com carga manual — não pode virar ponto único de falha de uma página que funciona.

### 23.9 Sinal contábil — dois níveis, e uma pré-condição

**[FATO] A fact preserva lançamentos assinados, e todos os 210 valores observados são zero ou negativos** (§23.1). Nenhum positivo.

**Nível 1 — API / contrato de dados.** Preserva o valor assinado da fonte, sem exceção. Os nomes (`*_signed`) tornam isso explícito. `abs()` **proibido**.

**Nível 2 — interface.** Não pode chamar silenciosamente um valor negativo de "custo positivo". Até a **confirmação formal da convenção contábil** pelo dono do número:

- o título do bloco é **"Impacto de afiliados no resultado"**, com **sinal preservado** (`-R$ 958.842,36`);
- **não** se usa o título simples "Custo de afiliados" — ele afirmaria uma convenção que ninguém confirmou;
- valor **positivo** futuro é tratado como **reversão ou sinal desconhecido**, rotulado como tal e sinalizado para revisão — **nunca** convertido automaticamente em custo.

⚠️ **Pré-condição registrada.** Usar o título "Custo de afiliados" e exibir magnitude positiva exige **decisão contratual explícita** sobre a convenção de sinal — documentada, com o dono do número, e refletida aqui. A transformação, se aprovada, será uma regra nomeada e testada, **nunca** um `abs()` genérico aplicado no caminho.

### 23.10 Desenho da interface

Bloco novo em `/canais`, reaproveitando a arquitetura existente — **sem novo shell, sem novo modal, sem nova rota**.

**Mês completo (2026-03):**

```
┌─ Impacto de afiliados no resultado ────────── competência: 2026-03 ─────────┐
│  Fotografia de 25/08/2026 19:33 · carga manual, sem rotina automática        │
│  Fonte lida até 25/08/2026 00:11                                            │
│                                                                             │
│  TikTok Shop                                                                │
│    Comissão de criadores          -R$ 958.842,36                             │
│    Comissão de parceiro afiliado  -R$ 472.490,24                             │
│    Comissão de afiliados/Ads      -R$  94.838,13                             │
│                                                                             │
│  Mercado Livre    Dados indisponíveis · fonte equivalente não confirmada     │
│  Shopee           Dados indisponíveis · fonte equivalente não confirmada     │
│                                                                             │
│  Retorno de afiliados: indisponível — não há receita atribuída a afiliado    │
│  no grão necessário, então não existe numerador para calcular retorno.       │
│                                                                             │
│  Valores com o sinal da fonte. Os três são exibidos separadamente e não      │
│  somados: qual subconjunto constitui "custo de afiliado" é decisão aberta,   │
│  e a ausência de sobreposição entre eles não está provada.                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Mês parcial (agosto/2026, em aberto) — nenhum número:**

```
┌─ Impacto de afiliados no resultado ────────── competência: 2026-08 ─────────┐
│  Competência em aberto. Os valores ainda maturam na fonte e não são          │
│  exibidos: um número parcial pareceria comparável a um mês fechado.          │
│  Selecione um mês completo para ver os lançamentos.                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

Regras de apresentação:

- **Competência no cabeçalho**, nunca em nota de pé. Vários meses → uma seção por competência, **sem linha de total**.
- **Três lançamentos sempre separados.** Nenhum total, subtotal, ou barra de composição que sugira partes de um todo.
- **Sinal preservado** conforme §23.9. Título "Impacto de afiliados no resultado" até a convenção ser confirmada.
- **Zero medido × ausência × indisponível**: `R$ 0,00` só para zero medido em competência **completa**; `Dados indisponíveis` para ausência de fonte; nada para período não elegível. Nunca no mesmo pixel.
- **As quatro dimensões podem aparecer juntas**: um mês completo e disponível pode carregar, ao mesmo tempo, aviso de cobertura incompleta (2025-06: 1/5 marcas) e aviso de fotografia manual.
- **Frescor**: exibe a **data da fotografia** (`affiliate_refreshed_at`) e até onde a fonte foi lida (`source_watermark`). **Sem** adjetivo "atualizado"/"desatualizado" antes da UE2-C.
- **Estado protegido**: o bloco lê `displayAffiliateCosts`, `null` quando `resolvedKey !== requestKey`, igual a `displayKpis`. Nunca o estado bruto.
- **Sem ranking entre canais.** Ordem fixa e declarada (TikTok, ML, Shopee), nunca por valor.
- **Drill-down**: reaproveita o mecanismo existente por marca; nenhum shell novo.
- **Vocabulário proibido**: "retorno"/"ROI"/"ROAS" acompanhados de número; "share de vendas"; "margem"; "total de afiliados"; "custo" como rótulo de valor assinado antes da §23.9.

### 23.11 Plano da Task 2/3 — API e frontend, e só isso

1. `apps/api/app/schemas/performance.py` — acrescentar os quatro enums ortogonais, `ReturnAvailability`, `AffiliateCostRow`, `AffiliateChannelStatus`, `AffiliateCostsBlock`; campo aditivo `affiliate_costs` em `CanaisResponse`. **Nenhum campo existente alterado ou removido.**
2. `apps/api/app/services/performance_service.py` — função dedicada que lê `marts.fact_tiktok_affiliate_cost_order_monthly` por marca e pelas competências derivadas do `EffectivePeriod`; devolve o bloco com as quatro dimensões, `affiliate_refreshed_at = MAX(synced_at)` do escopo e `source_watermark` do `sync_state`. Colunas explícitas, nunca `SELECT *`. Envolvida de modo que falha devolva `error` **sem** derrubar o resto (§23.8). `channel_rows` **intocado**.
3. `apps/web/src/lib/api-client.ts` — tipos espelhados e mapeamento; sem novo fetch.
4. `apps/web/app/canais/page.tsx` — bloco novo lendo estado protegido; nenhum componente existente alterado além da inclusão.

⚠️ **A Task 2/3 NÃO toca `full_daily`, `orchestrate.py` nem o Scheduler.** Integração operacional é a frente **UE2-C** (§23.12), separada de propósito: misturar API/frontend com alteração da rotina diária juntaria dois riscos de natureza diferente na mesma mudança.

### 23.12 UE2-C — frente operacional separada (não iniciada)

Escopo próprio, fora do UE3:

1. integrar `pipelines/sync_tiktok_affiliate_cost_order_monthly.py --mode incremental` à rotina;
2. definir **frequência** da carga;
3. definir **SLA** de frescor;
4. definir o **backfill integral periódico** exigido pela §18.8.5 (hard delete não é detectável por watermark);
5. **somente então** habilitar a classificação temporal `fresh`/`stale` no `freshness_status`.

Até a UE2-C concluir, `freshness_status` é `manual_snapshot` e a interface mostra data, não adjetivo.

### 23.13 Aceite da Task 3/3 (testes)

| # | Teste |
|---|---|
| 1 | lançamento negativo aparece com **sinal preservado**; nenhum `abs()` no caminho |
| 2 | **zero medido** em competência completa exibe `R$ 0,00`, distinto de indisponível |
| 3 | `None`/ausência de linha → chave não aparece; **nunca** `0` |
| 4 | ML e Shopee → `unavailable_no_source` e **"Dados indisponíveis"**; nunca "Não aplicável" |
| 5 | **mês parcial → `rows=[]`**, nenhuma prévia numérica, nenhum zero |
| 6 | **intervalo não alinhado → `rows=[]`**, sem rateio |
| 7 | vários meses → uma linha por marca × competência; **nenhum agregado multimensal**; `months_included` presente como metadado |
| 8 | as quatro dimensões são **independentes**: um caso `available` + `complete_month` + `incomplete_brand_coverage` + `manual_snapshot` é representável e exibido por inteiro |
| 9 | `affiliate_refreshed_at` = `MAX(synced_at)` do escopo; **não** é o `refreshed_at` de `/canais` |
| 10 | `source_watermark` vem do `sync_state` e é **distinto** de `affiliate_refreshed_at` |
| 11 | `freshness_status` nunca é `fresh` ou `stale` antes da UE2-C |
| 12 | `return_availability` é enum tipado; **não** existe `return_amount`, `roi`, `roas` nem receita atribuída |
| 13 | **nenhuma soma** dos três componentes em payload ou UI |
| 14 | falha na consulta de afiliados → bloco em `error` e **resto de `/canais` intacto** |
| 15 | `requestKey`/`resolvedKey`: bloco vira `null` quando não fresco; troca de filtro em voo não vaza resultado velho |
| 16 | "retorno", "ROI", "ROAS" nunca aparecem acompanhados de número |
| 17 | título é "Impacto de afiliados no resultado"; **não** "Custo de afiliados" enquanto a convenção não for confirmada |
| 18 | **zero regressão**: `channel_rows`, `channel_medians`, sinais e KPIs byte-equivalentes; `test_nenhum_campo_de_desconto_ou_afiliado_no_payload` continua **verde** |
| 19 | acessibilidade (alvo ≥ 44px, contraste, tipografia ≥ 12px) e responsividade |
| 20 | fonte indisponível → nenhum número fabricado |

### 23.15 Estado da implementação (Task 2/3)

Implementada. O registro factual — arquivos, decisões que este contrato não
fixava, validação executada e o que ficou sem validar — está na **§24**.

**Uma decisão da §23.11 foi revista durante a implementação.** O plano dizia
montar o bloco dentro de `get_canais`; ele é montado **na rota**, sobre a
resposta já produzida. Motivo medido: dentro do serviço, o bloco acrescentava
duas consultas às que `get_canais` já emitia e quebrava 17 testes existentes.
Ver §24.1. A mudança isola o **contrato histórico** e a **falha** — **não** a
latência: a composição é síncrona e `/canais` ganhou trabalho adicional. Nenhuma
outra regra deste contrato mudou.

### 23.14 Riscos e decisões abertas

| # | Item | Estado |
|---|---|---|
| A | **Convenção de sinal não confirmada** | **Aberto.** Pré-condição para o título "Custo de afiliados" e para qualquer magnitude positiva (§23.9) |
| B | **Sem atualização automática** — `synced_at` congelado em 2026-08-25 | **Aberto.** Escopo da **UE2-C** (§23.12), não da Task 2/3 |
| C | **Sobreposição dos três componentes não provada** (§23.3) | **Aberto.** Reforça a proibição de somar; precisa do dono do número |
| D | **P2** — qual subconjunto é "custo de afiliado" | **Aberto.** Bloqueia qualquer total |
| E | **`2026-08` com creator zero** — provável atraso de registro | **Aberto**, mas **neutralizado na UI**: mês em aberto devolve `rows=[]` |
| F | **Fase C do contrato TikTok** (auditoria BLOCKED por host key) | **Aberto.** Bloqueia qualquer razão sobre GMV |
| G | Fonte de afiliados para ML/Shopee | **Não investigada.** Enquanto não houver prova, é "indisponível", nunca "não aplicável" |
| H | Backfill integral periódico (§18.8.5) | **Aberto.** Escopo da UE2-C |

---

## 24. UE3 Task 2/3 — implementação em Canais (registro factual)

Implementação do contrato §23. Nenhum endpoint novo, nenhuma migration,
nenhuma dependência nova, nenhum arquivo em `pipelines/`, `db/` ou `alembic`.

### 24.1 A decisão de arquitetura que mudou durante a implementação

O plano da §23.11 previa montar o bloco **dentro de `get_canais`**. Foi
implementado assim primeiro, e a suíte revelou o problema: `get_canais` passou
a emitir duas consultas extras na mesma sessão, e **17 testes existentes**
(`test_canais_channel_rows.py`, `test_canais_content_mix.py`) quebraram com
`IndexError` — os fakes deles entregam resultados por ordem de chamada.

Isso não era problema de fixture: era o sintoma de que o bloco havia sido
enxertado dentro do contrato de um serviço que já funcionava. A composição foi
movida para a **rota**:

```python
resposta = perf_svc.get_canais(sessao, ...)          # intocado
inicio, fim = perf_svc.canais_period_bounds(filters.period, year, month)
resposta["affiliate_costs"] = safe_affiliate_costs_block(sessao, ..., inicio, fim, ...)
return resposta
```

**O que essa mudança isola — e o que NÃO isola.**

| Dimensão | Estado |
|---|---|
| Contrato histórico de `get_canais` | **Isolado.** O serviço não conhece o bloco e emite exatamente uma consulta |
| Falha esperada de banco | **Isolada semanticamente.** `safe_...` devolve o bloco em `error`; `kpis`, `brands` e `channel_rows` permanecem válidos |
| **Latência** | **NÃO isolada.** `safe_affiliate_costs_block` é chamada de forma **síncrona**, antes da resposta HTTP. `/canais` ganhou trabalho adicional e o tempo do bloco soma ao tempo da rota |

Não há cache, thread, fila nem timeout próprio — nenhum deles foi introduzido.
A medição real do custo está na §24.6.

Consequências medidas:

- `get_canais` volta a emitir **exatamente uma** consulta, e não devolve a
  chave `affiliate_costs` (teste
  `test_get_canais_nao_produz_o_bloco_nem_consulta_a_fact`);
- os 17 testes existentes voltaram a passar **sem serem editados** — a
  invariância de `channel_rows` fica provada por construção, não por asserção;
- `canais_period_bounds` foi extraída para que rota e serviço resolvam a janela
  pela **mesma** regra; duas resoluções independentes divergiriam caladas.

`performance_service` não importa mais `affiliate_costs_service`, então o
import circular que existia na primeira versão deixou de existir e os imports
de `TIKTOK_ID`/`ML_ID`/`SHOPEE_ID` subiram para o topo do módulo.

### 24.2 Arquivos

| Arquivo | Natureza |
|---|---|
| `apps/api/app/schemas/performance.py` | 5 aliases `Literal` + 3 modelos; `affiliate_costs: Optional[...] = None` em `CanaisResponse` (aditivo) |
| `apps/api/app/services/affiliate_costs_service.py` | **novo** — `classify_period` (pura), `build_affiliate_costs_block`, `safe_affiliate_costs_block` |
| `apps/api/app/routers/performance.py` | composição do bloco na rota `/canais` |
| `apps/api/app/services/performance_service.py` | `canais_period_bounds` extraída; `get_canais` sem o bloco |
| `apps/web/src/lib/api-client.ts` | 5 tipos + `affiliateCosts` nas **duas** rotas (real e mock) |
| `apps/web/src/lib/canais-affiliate-costs.ts` | **novo** — módulo puro de apresentação |
| `apps/web/src/components/AffiliateCostsPanel.tsx` | **novo** — bloco, reusa `KpiDrilldownDialog` |
| `apps/web/app/canais/page.tsx` | estado + `displayAffiliateCosts` + render |

### 24.3 Decisões de implementação que o contrato não fixava

1. **`formatSignedBrl` não reusa `fmtBrl`.** `fmtBrl` abrevia para `R$ 1.2M`, e
   um custo contábil abreviado não reconcilia com relatório nenhum. O bloco usa
   valor integral, com centavos e com o sinal da fonte.
2. **Cobertura medida sobre a competência, não sobre o recorte.** A CTE conta
   `COUNT(DISTINCT brand)` **sem** o filtro de marca. Com filtro de uma marca, a
   cobertura continua dizendo quantas marcas a competência tem — filtrar
   mostraria "1 de 5" sempre.
3. **`channels` segue autoritativo mesmo em `error`.** Só o TikTok entra em
   `error`; ML e Shopee permanecem `unavailable_no_source`, porque não ficaram
   indisponíveis por causa dessa falha.
4. **`displayAffiliateCosts = dataIsFresh ? affiliateCosts : null`.** O bloco
   obedece à mesma guarda de frescor dos outros estados da página: custo
   contábil do filtro anterior exibido sob o filtro novo é pior que ausência.
5. **`affiliateCosts: raw.affiliate_costs ?? null`.** Bloco ausente na resposta
   (API antiga) é estado distinto de bloco presente em qualquer status. O
   cliente não fabrica bloco vazio.
6. **Rota mock devolve `affiliateCosts: null`.** Um valor inventado no modo
   demonstração poderia ser lido como medição real.
7. **A nota da matriz comparativa foi corrigida.** Ela dizia "Não inclui
   desconto nem comissão de afiliados"; agora aponta que afiliados aparecem em
   bloco próprio, por competência mensal. A matriz de fato continua sem eles.

### 24.4 O que continua não existindo, por decisão

`affiliate_cost_total`; soma dos três componentes em qualquer nível;
`<tfoot>` de total; agregado multimensal; razão sobre GMV; `return_amount`;
ROI; ROAS de afiliado; receita atribuída; `abs()`/`Math.abs()`; rateio de mês
para dia; preenchimento de marca ausente com zero; número em período parcial ou
desalinhado.

### 24.5 Validação executada

| Verificação | Resultado |
|---|---|
| Testes focais backend (`test_canais_affiliate_costs.py`) | **40 passed** |
| Suíte completa `apps/api` | **720 passed, 43 failed** |
| Baseline da suíte em `HEAD` limpo (`git archive`) | **680 passed, 43 failed** |
| Delta de regressão | **zero** — as 43 falhas são idênticas à baseline |
| `compileall app tests` | OK |
| Startup/import da app | OK; `/api/v1/performance/canais` registrada; bloco com 13 campos |
| Nomes de campo proibidos nos 3 modelos | zero |
| Testes focais frontend | **33 passed** |
| `npm test` | **1290 passed, 0 failed** |
| `npm run typecheck` | OK |
| `npm run build` | OK |
| `git diff --check` | limpo |
| Scan de secrets/PII (11 arquivos, 12 padrões) | zero achados |
| `package-lock.json` | sem diff |
| Dependências novas | zero |
| Arquivos em `pipelines/`, `db/`, `alembic` | zero |

**As 43 falhas pré-existentes** não têm relação com esta frente: testes de
router sem banco esperam `503`, mas `psycopg2` estoura `UnicodeDecodeError` ao
decodificar a mensagem de erro de conexão em cp1252 no Windows pt-BR. Falham
igualmente no `HEAD` limpo. Não foram tocadas.

### 24.6 O que NÃO foi validado nesta task

- **QA visual.** Nenhuma tela foi aberta em navegador. Layout, contraste,
  responsividade e comportamento real do diálogo são a Task 3/3.
- **Resposta com banco real.** Todos os testes usam sessão falsa. A consulta
  não foi executada contra o Neon, então plano de execução e latência do bloco
  são desconhecidos.
- **Convenção de sinal** (§23.14-A) segue aberta: a UI exibe o sinal da fonte
  sem afirmar o que ele significa.

### 24.7 Rodada de correção pré-QA (27/08/2026)

Dez achados corrigidos antes do QA visual. Os quatro de maior consequência:

**F1 — a afirmação de isolamento estava larga demais.** O texto anterior dizia
que o bloco não entrava no "caminho crítico". Falso: `safe_affiliate_costs_block`
é chamada **de forma síncrona** antes da resposta HTTP. O que está isolado é o
contrato histórico e a falha — nunca a latência. A consulta da fact e a do
watermark foram **fundidas em uma só** (o watermark virou subconsulta escalar),
caindo de dois round-trips para um. Medição na §24.8.

**F3 — competência inteiramente ausente sumia da análise.** A CTE de cobertura
só conhecia meses **presentes** na fact. Um mês solicitado com zero linhas não
aparecia, e o agregado podia se declarar `complete` escondendo o buraco. A
consulta passou a materializar as competências **pedidas** (`UNNEST` + `LEFT
JOIN`), de modo que um mês ausente retorna com `brand IS NULL` e
`brands_present_in_month = 0`. Essa linha é metainformação: **nunca** vira linha
monetária. O discriminador é `brands_present_in_month == 0` — e não "zero linhas
retornadas" —, o que separa corretamente **mês ausente** de **recorte de marca
vazio**: no segundo caso a competência existe e a cobertura segue dizendo 5.

**F2 — fuso operacional.** `date.today()` lia o relógio do SO. Trocado por
`today_brt()` (helper já existente em `app/deps/period.py`). A fronteira medida
em teste: `2026-09-01T02:30Z` é **31/08 em BRT** e **01/09 em UTC** — pelo
relógio errado, agosto teria "fechado" quase um dia antes da hora.

**F5 — frescor afirmado onde não havia leitura.** O painel dizia "Carga manual
sem registro de data" mesmo quando nenhuma fotografia fora consultada (só
ML/Shopee, consulta falha, período parcial). Agora `freshness_status` só é
`manual_snapshot` no caminho em que a fact foi de fato lida; todo bloco vazio sai
como `unknown`, e `describeFreshness` devolve `null`.

Os demais: **F4** removeu `exc_info=True` do erro esperado (o traceback de
`SQLAlchemyError` carrega o SQL e, conforme o driver, parâmetros de conexão);
**F6** trocou `loading = !dataIsFresh` por quatro fases explícitas
(`resolveBlockPhase`), acabando com o skeleton eterno após erro terminal;
**F7** remonta o painel via `key={requestKey}`, fechando o diálogo na troca de
filtro sem tocar no shell `KpiDrilldownDialog`; **F8** adicionou teste que invoca
a função da rota de verdade; **F9** passou a converter instantes com offset para
BRT e a rotulá-los, mantendo data pura sem deslocamento e recusando carimbar
fuso em timestamp sem offset; **F10** removeu `sm:min-h-0` do botão de detalhe.

### 24.8 Preflight read-only contra o Neon (27/08/2026)

Somente `SELECT`/`EXPLAIN`, sessão em `READ ONLY`, zero escrita.

| Medida | Valor |
|---|---|
| Linhas na fact | **70** |
| Competências distintas | **15** |
| Índices | PK `btree (ref_month, brand)` e `idx_ftacom_brand_ref_month btree (brand, ref_month)` |
| Round-trips do bloco | **1** (era 2 antes da fusão) |
| `Execution Time` — 1 competência | **0,864 ms** |
| `Execution Time` — 12 competências | **0,208 ms** |
| `Execution Time` — 12 competências + filtro de marca | **0,185 ms** |
| Buffers | `shared hit=5` |

**Índice não é usado, e está certo.** O plano faz `Seq Scan`. A tabela inteira
cabe em 2 páginas: para 70 linhas o planejador não tem motivo para percorrer
índice. Isso não é sintoma de problema, e o crescimento é de ~5 linhas por mês.

**A latência medida é rede, não consulta.** Da máquina de desenvolvimento:

| Consulta (mesma conexão, 5 execuções) | min | mediana | máx |
|---|---|---|---|
| `SELECT 1` (RTT puro) | 231,66 ms | **232,29 ms** | 233,05 ms |
| `SELECT count(*)` na fact | 232,04 ms | 232,21 ms | 235,44 ms |
| Consulta do bloco, 12 competências | 232,86 ms | **234,06 ms** | 236,72 ms |

O custo da consulta **acima do RTT** é de **+1,78 ms**, e **99,2%** do tempo
medido é ida-e-volta de rede desta máquina até o Neon.

**Conclusão, limitada à evidência.** O **plano e a execução no Postgres não
apresentam risco material no volume atual**: 0,2 ms de servidor sobre 70 linhas,
`shared hit=5`, sem hazard de escala. O bloco **adiciona uma consulta e um
round-trip síncrono** a `/canais`. **Localmente**, o impacto medido foi de
aproximadamente **um RTT — cerca de 230,79 ms** (§24.9). A **latência total do
endpoint em produção foi verificada na §24.11** — mediana de 423 ms no cenário
com o bloco. O que **continua não isolado** é o **RTT interno Render→Neon**:
medi-lo exigiria instrumentação interna do serviço, e por isso a parcela do bloco
dentro daquele total **não é atribuível causalmente**. Não há indício de problema
de desempenho. Nenhum cache, thread, endpoint ou timeout foi introduzido.

**O que essa medição NÃO diz.** Os ~233 ms são o RTT **desta máquina**, não o de
produção. O RTT real Render→Neon não foi medido e só pode ser medido depois de
publicar. A conclusão defensável é sobre o **marginal**: uma ida-e-volta a mais,
de custo de servidor desprezível.

**Achado colateral, com consequência na UI.** Os dois carimbos têm tipos
**diferentes** no banco: `synced_at` é `timestamp with time zone` (serializa
`...+00:00`) e `last_successful_upper_bound` é `timestamp without time zone`
(sem offset). Por isso o `formatTimestamp` tem três casos: converte e rotula
`BRT` o que tem offset, e se recusa a carimbar fuso no que não tem. As duas
formas reais estão fixadas em teste.

### 24.9 Task 3/3 — QA integrada (27/08/2026)

Executada com backend local lendo o **Neon real** (somente `SELECT`/`EXPLAIN`) e
frontend servido pelo build de produção. Zero escrita, zero deploy, zero commit.

**Uma correção, consolidada em rodada única.** O skeleton do bloco renderizava
uma região `aria-busy="true"` **sem nenhum texto**: um leitor de tela anunciava
"ocupado" sem dizer do quê. Medido por comparação direta — as outras três regiões
de carregamento de `/canais` mantêm o heading durante o load
(`Mix do GMV de conteúdo do TikTok`, `Mercado Livre`, `Shopee`), e só a de
afiliados vinha vazia. O título passou a ser renderizado no skeleton, com a
animação restrita ao invólucro das barras de placeholder. Nenhuma regra de
contrato, fonte ou métrica foi tocada.

**Um candidato descartado com prova.** O detector acusou uma requisição ao abrir
o diálogo. Ela é um **prefetch RSC do Next** para `<Link href="/brand/...">` das
**tabelas de marca** (`A < TD < TR < TBODY < TABLE`), não do painel — que não tem
âncora alguma. Rolar a página **sem abrir o diálogo** dispara cinco prefetches
iguais. Comportamento pré-existente, fora do escopo. O que importava foi
verificado e passou: **zero requisição de dados** ao abrir, e os 15 valores do
diálogo são exatamente os já presentes na tabela.

#### Cenários read-only executados contra dados reais

| # | Cenário | Resultado medido |
|---|---|---|
| A | 2026-07 completo, todos os canais | 5 linhas (1 por marca), 3 lançamentos separados e negativos, `complete`, TikTok `available`, ML/Shopee `unavailable_no_source` |
| B | Só ML+Shopee | `rows=[]`, TikTok ausente da lista, nenhuma disponibilidade herdada, frescor `unknown` |
| C | 2026-08 parcial | `partial_month`, `rows=[]`, `months_included=[]`, zero número |
| D | 2026-06-15..07-14 | `not_month_aligned`, `rows=[]`, nenhum rateio |
| E | 2025-06 | `incomplete_brand_coverage`, **1 marca** (kokeshi), as outras 4 ausentes e **não** preenchidas com zero |
| F | 2025-01 (anterior ao mínimo da fact) | `rows=[]`, competência **listada** em `months_included`, cobertura incompleta, nota declarando ausência ≠ custo zero, nenhum timestamp inventado |
| G | 2025-06..2025-08 | 10 linhas; por competência **1 / 4 / 5** marcas; agregado geral **conservador** = incompleto; nenhum total multimensal |
| + | 2026-07 filtrado por `apice` | 1 linha, `brands_present_in_month=5` — cobertura é da **competência**, não do recorte |
| H | Falha controlada (monkeypatch, sem tocar o banco) | HTTP 200; `kpis`, `brands`, `channel_rows`, `channel_medians` e `refreshed_at` **byte-idênticos** ao baseline; bloco em `error`; nota fixa; zero vazamento de SQL/DSN/host/driver; requisição seguinte volta a `available` |

**Ausência vs. zero, medido.** Nos payloads reais: **15 componentes com valor
exatamente `0`** (zero medido, exibido `R$ 0,00`) e **0 valores `null`**. As três
colunas **são** `nullable` no banco, então o caminho de ausência por coluna é
alcançável — hoje apenas não ocorre. A ausência que de fato aparece nos dados é de
outra natureza e foi verificada: **competência inteira ausente** (cenário F) e
**marca ausente dentro da competência** (E e G) — expressas como **linha
inexistente**, nunca como zero.

#### QA visual — 1440×900, 1024×768 e 390×844

Navegador real (Chromium), **zero falha e zero aviso nos três viewports**.
Verificado em cada um: título exato e posicionado após a matriz comparativa;
três componentes separados com sinal da fonte e duas casas; nenhum `Total`,
`ROI`, `ROAS`, margem ou `<tfoot>`; ML/Shopee como **"Dados indisponíveis"** e
nunca "Não aplicável"; os seis recortes de período; zero overflow horizontal
(tabela com rolagem local — 595px de tabela em 356px de área no mobile); nenhum
texto cortado; todo alvo interativo ≥ 44×44; nenhum glifo < 12px; heading `H2`;
tabela com `<caption>` acessível; zero erro de console, zero *hydration warning*,
nenhum host inesperado, nenhuma requisição duplicada e nenhum segredo no DOM.

Diálogo (desktop e mobile): abre no `KpiDrilldownDialog` existente, foco inicial
em "Fechar detalhes", *focus trap* efetivo após 12 `Tab`, `Escape` fecha, foco
devolvido ao acionador, **zero requisição de dados ao abrir**. Na troca de filtro
com o diálogo aberto: fecha, nenhum valor antigo reaparece, e a reabertura não
mostra estado anterior. Com resposta atrasada em 2,5s, **nenhum dos 15 valores do
filtro anterior piscou** em 10 amostras.

**Limitação honesta do cenário de erro no navegador.** `apiFetch` nunca rejeita —
degrada para o caminho mock —, então o estado que o navegador exercita ao
derrubar a rota é o ramo `bloco === null` ("Dados de afiliado indisponíveis"),
não a fase `unavailable`. Ambos foram verificados quanto ao que o F6 exigia: **sem
skeleton eterno, sem `aria-busy` e sem número antigo**. A fase `unavailable` em
si é coberta por teste unitário e é alcançável apenas quando `fetchCanais`
rejeita.

#### Latência — decomposta, sem extrapolação

| Camada | Medida |
|---|---|
| Execução no Postgres | **0,202 ms** (`Buffers: shared hit=5`, 70 linhas) |
| RTT desta máquina → Neon (`SELECT 1`) | **232,13 ms** (mediana de 10) |
| Consulta do bloco | **233,22 ms** = RTT **+1,10 ms** |
| Endpoint `/canais` **com** consulta do bloco | **1174,90 ms** (mediana de 10) |
| Endpoint `/canais` **sem** consulta do bloco (só ML+Shopee) | **944,12 ms** |
| **Delta atribuível ao bloco** | **+230,79 ms** — praticamente um RTT exato |
| Consultas do bloco por request | **1** (verificado por espião no `_scope_sql`) |

O delta bate com um RTT (232,13 ms) e o trabalho de servidor é 0,2 ms: o bloco
custa **exatamente uma ida-e-volta**. O RTT acima é **desta máquina** e **não é
extrapolável** para Render→Neon. A publicação e a latência total do endpoint em
produção foram verificadas na **§24.11**; o **RTT interno Render→Neon continua
não isolado**, porque exigiria instrumentação interna do serviço.

#### Estado que permanece

`manual_snapshot` — a carga da fact segue manual e a **UE2-C não foi iniciada**.
Retorno de afiliados **segue indisponível** (não há receita atribuída no grão).
A **convenção contábil do sinal permanece aberta** (§23.14-A): a UI exibe o sinal
da fonte sem afirmar o que ele significa. **Nenhum deploy foi realizado.**

### 24.10 Versionamento — 27/08/2026

> ⏳ **Estado no checkpoint imediatamente após o versionamento.** Descreve o
> momento do commit, **antes** da publicação, e foi **superado pelo smoke da
> §24.11**. Preservado como registro do que era verdade naquele instante.

O Gate UE3 foi **versionado neste fechamento**, em um único commit com os 14
arquivos da frente. **Nenhum deploy manual foi executado pelo agente.**

*(À época)* O bloco ainda não podia ser considerado disponível em produção: o
backend dependia de publicação manual no Render e o frontend, da publicação
automática da Vercel — que o push pode acionar, mas isso não foi validado
naquela rodada. Smoke de produção e latência ainda estavam pendentes.
**→ Hoje: ambos publicados e o smoke executado (§24.11, `PASS WITH ISSUE`).**

Nada mais mudou de estado: **UE2-C não iniciada**, frescor em `manual_snapshot`,
retorno **indisponível** e convenção contábil do sinal **aberta**.

### 24.11 Smoke pós-publicação em produção — 27/08/2026 · `PASS WITH ISSUE`

Backend publicado **manualmente pelo proprietário** no Render; frontend
publicado pelo **fluxo automático da Vercel**. Smoke estritamente read-only:
somente `GET`. Zero deploy, zero escrita, zero commit pelo agente.

#### Comportamento compatível com `8760f96`

Não há SHA publicado visível em nenhuma das duas plataformas, então a
classificação é **comportamental**: o `openapi.json` de produção publica os
contratos que **só existem** nesse commit — `CanaisResponse.affiliate_costs`, os
modelos `AffiliateCostsBlock` (13 campos), `AffiliateCostRow` e
`AffiliateChannelStatus`, e as **quatro dimensões ortogonais** com os enums
exatos, incluindo `return_availability` como enum de **um único valor de
indisponibilidade**. Nenhum campo agregador em nenhum dos três modelos.

#### Cenários de API executados contra produção

| # | Cenário | Resultado |
|---|---|---|
| B1 | 2026-07 completo, todos os canais | HTTP 200; `available` · `complete_month` · `manual_snapshot`; **5 linhas TikTok**; ML/Shopee `unavailable_no_source`; `channel_rows` com 14 entradas preservadas |
| B2 | Só ML+Shopee | `rows=[]`; TikTok ausente da lista; `freshness=unknown`; nenhum carimbo herdado |
| B3 | Mês corrente parcial | `partial_month`; `rows=[]`; `months_included=[]`; nenhuma ausência virou `R$ 0,00` |
| B4 | 2025-06 | **1 marca real** (kokeshi), `brands_present_in_month=1`, `incomplete_brand_coverage`; as outras 4 **não** fabricadas |
| B5 | 2025-01 | `rows=[]`; competência **listada**; cobertura incompleta; nota declarando ausência ≠ zero; **nenhum carimbo inventado** |

`affiliate_refreshed_at` = `2026-08-25T19:33:26+00:00` e `source_watermark` =
`2026-08-25T00:11:55` — **próprios e distintos entre si**, e distintos do
`refreshed_at` geral da rota (`2026-08-05T18:53:53`). Nenhum vazamento de SQL,
DSN, host, driver ou PII em nenhum payload.

#### Latência TOTAL observada em produção

Após aquecimento descartado, 10 chamadas sequenciais por cenário:

| Cenário | mín | mediana | p95 | máx | payload | erros |
|---|---|---|---|---|---|---|
| B1 — mês completo, todos os canais | 406 ms | **423 ms** | 810 ms | 810 ms | ~17,2 KB | 0 |
| B2 — só ML+Shopee | 389 ms | 410 ms | 464 ms | 464 ms | ~12,4 KB | 0 |
| Mês parcial, todos os canais | 396 ms | 424 ms | 456 ms | 456 ms | ~16,0 KB | 0 |

**Limite da atribuição causal.** Os três cenários têm **workloads diferentes** —
filtros e períodos distintos produzem consultas e volumes distintos em **toda** a
rota, não só no bloco. A diferença entre eles **não é o custo do bloco** e não
foi instrumentada causalmente. O **RTT Render→Neon não foi medido**: não há
acesso à instrumentação interna do serviço. A afirmação sustentada é apenas:
**"produção respondeu com mediana de 423 ms no cenário com o bloco"**.

A única evidência causal disponível continua sendo a **local** (§24.8): execução
de ~0,202 ms no Postgres, **uma** consulta adicional verificada por espião, e
impacto local equivalente a um RTT da máquina de medição.

#### Navegador real — 1440×900 e 390×844

Bloco presente com título exato; três componentes separados; **sinal da fonte
preservado**; duas casas decimais; nenhum total, margem, ROI, ROAS ou retorno
numérico; TikTok com dado e ML/Shopee como **"Dados indisponíveis"** (nunca "Não
aplicável"); `manual_snapshot` apresentado como *"Carga manual gravada em… Sem
atualização automática"*, **sem jamais dizer "fresco" ou "atualizado agora"**;
avisos de retorno indisponível e de **sobreposição não provada** visíveis;
nenhuma ausência exibida como `R$ 0,00`. Tabela com rolagem local no mobile,
zero overflow horizontal, tipografia ≥ 12px, alvos ≥ 44×44, zero *hydration
warning*, zero exceção de página e **nenhum host além dos dois canônicos**.

Diálogo: abre no `KpiDrilldownDialog`, foco inicial em "Fechar detalhes", focus
trap efetivo, `Escape` fecha, foco devolvido ao acionador, **zero fetch de dados
ao abrir**; a troca de filtro fecha o diálogo e o conteúdo antigo não reaparece.

Estados verificados com **dado real de produção**, sem fixture: cobertura
incompleta, competência ausente, período parcial e ML+Shopee sem TikTok.
**Interceptação foi usada em um único ponto**, declarado: o estado transitório de
`loading` (atraso de 3 s na resposta), que não se reproduz deterministicamente —
ali confirmou-se `aria-busy`, heading acessível e ausência de número.

#### Coerência API × interface

Os **15 valores** de B1 foram comparados um a um entre o JSON e a tela: todos
**idênticos**. A interface **não recalcula, não inverte sinal, não aplica
`abs()`, não soma componentes, não completa marca ausente e não cria retorno** —
a soma dos três componentes foi buscada no texto e **não aparece**.

> Nota de método: a primeira comparação acusou divergência nos 15 valores. Era
> defeito do harness — `Intl.NumberFormat("pt-BR")` separa `R$` dos dígitos com
> **U+00A0** (espaço não separável), tipografia correta que impede o símbolo de
> quebrar linha longe do número, enquanto a string esperada usava espaço comum.
> Normalizado, a identidade é exata.

#### A única ressalva — por que `PASS WITH ISSUE`

Em **uma** das execuções apareceu no console do desktop um
`Failed to load resource: 404`, **sem URL capturada**. Não reproduziu em **seis
tentativas** posteriores — quatro cargas limpas, um diagnóstico dedicado e um
*replay* da sequência completa de desktop —, todas com **zero respostas ≥ 400**.
Nenhuma delas era de `/canais` nem do bloco, e o bloco renderizou corretamente,
com os 15 valores, em **todas** as execuções. Classificação: **observação
intermitente, não reproduzível e não atribuível a esta frente**. Não bloqueia,
mas fica registrada em vez de ser omitida.

#### O que continua aberto

**UE2-C não iniciada** — por isso `manual_snapshot`. **Retorno indisponível.**
**Convenção contábil do sinal aberta** (§23.14-A). **RTT Render→Neon nunca
medido.**

---

## 25. UE2-C Task 1/3 — blueprint da atualização automática

⚠️ **[DESENHO] Nada foi implementado.** Esta seção é auditoria read-only e
blueprint. Zero escrita em banco, zero `--apply`, zero alteração de Scheduler,
zero código. **UE2-C não está implementada.**

### 25.1 O achado que muda o desenho: a fonte é EXTERNA e roda em lote único

**[FATO — medido em 2026-08-28]** `silver.stg_tiktok_payments_by_order`
**não é escrita por nenhum pipeline deste repositório**. Uma busca por todas as
referências encontra exatamente duas: o próprio sync (que a lê) e este documento.
A tabela pertence à mesma linhagem externa de `gold.tiktok_brand_daily`, cuja
transformação já era conhecida por **não estar versionada em repositório nosso**
(§4).

Consequência direta e não óbvia: **não existe step do `full_daily` depois do qual
a fonte fique madura.** `daily_tiktok` alimenta `raw.tiktok_shop_orders`, não a
Silver de pagamentos. Declarar `depends_on=("daily_tiktok",)` criaria um
**contrato falso** — pareceria haver garantia de ordem onde não há relação causal
alguma.

**[FATO] A fonte avança em UM lote diário, em janela apertadíssima.** Distribuição
de `updated_at` por hora BRT nos últimos 14 dias:

| Hora BRT | Linhas |
|---|---|
| 21h | **104.922** |
| 01h | 1 |

Dez dias consecutivos confirmam a regularidade:

| Dia (BRT) | Linhas | Primeiro | Último |
|---|---|---|---|
| 2026-08-18 | 8.693 | 21:02:30 | 21:03:01 |
| 2026-08-19 | 4.669 | 21:02:27 | 21:03:05 |
| 2026-08-20 | 6.955 | 21:03:00 | 21:03:27 |
| 2026-08-21 | 6.162 | 21:03:41 | 21:04:12 |
| 2026-08-22 | 7.761 | 21:02:29 | 21:03:11 |
| 2026-08-23 | 9.022 | 21:02:42 | 21:03:34 |
| 2026-08-24 | 11.972 | 21:02:35 | 21:11:55 |
| 2026-08-25 | 6.787 | 21:02:14 | 21:02:51 |
| 2026-08-26 | 3.972 | 21:03:04 | 21:03:14 |
| 2026-08-27 | 3.649 | 21:03:26 | 21:03:51 |

O lote inteiro cabe em **~30 a 90 segundos**, sempre entre **21:02 e 21:12 BRT**.
É esse relógio — e não uma preferência nossa — que define a cadência viável.

### 25.2 Estado real medido (read-only, 2026-08-28)

| Grandeza | Valor |
|---|---|
| Linhas na fonte | 2.172.212 |
| `MAX(updated_at)` da fonte | **2026-08-28 00:03:51** (= 27/08 21:03 BRT) |
| `MIN(updated_at)` da fonte | 2026-03-12 20:21:21 |
| `updated_at` nulos | **0** |
| Watermark persistido | **2026-08-25 00:11:55** (= 24/08 21:11 BRT) |
| `MAX(synced_at)` da fact | 2026-08-25 19:33:26 |
| Linhas da fonte além do watermark | **14.408** |
| Chaves que o incremental recalcularia | **12** (número do próprio módulo) |
| `transaction_type` distintos | **só `ORDER`** — 2.172.212, zero tipo desconhecido |
| Marcas na fonte | 8 — as 5 da allowlist + `gocase`, `azbuy`, `denavita` fora dela |
| Competências na fact | 15 (2025-06 a 2026-08), 70 chaves, **um único `source_run_id`** |
| Cobertura incompleta | 2025-06 (1/5) e 2025-07 (4/5) |

**Reconciliação fonte × fact no cutoff do watermark: ZERO divergências em 70
chaves.** A verificação reusou `_component_sql()` e `_filtro_populacao()` **do
próprio módulo**, para que a auditoria não medisse uma coisa e o pipeline outra.
A fact reproduz exatamente a fonte no ponto em que parou — a defasagem é de
atualização, não de corretude.

**Deriva acumulada em 3 lotes não aplicados** (simulação read-only do que o
incremental faria agora, cutoff `2026-08-28 00:03:51`): **13 valores mudariam**,
em duas competências.

| Componente | Soma dos deltas |
|---|---|
| `creator` | **+0,00** |
| `partner` | **−19.368,01** |
| `ads` | **−5.915,20** |

Confirmado de forma independente pelo agregado do próprio módulo em modo
diagnóstico: `partner` sai de −3.110.478,68 para **−3.129.846,69** e `ads` de
−738.193,33 para **−744.108,53** — exatamente os deltas acima. `creator`
permanece **−5.504.405,93**.

**Dois fatos com consequência de desenho:**

1. **`creator` nunca revisa.** Ele lê
   `affiliate_commission_amount_before_pit` — valor *antes* de PIT, que por
   definição não muda depois. `partner` e `ads` revisam.
2. **Mês fechado NÃO congela.** Julho/2026 — competência encerrada — teve
   `partner` alterado em três marcas (apice −12,64; barbours −6,38;
   kokeshi −15,45). A revisão retroativa é real e alcança competências passadas.

**Maturação medida** (atraso `updated_at − order_create_time`, últimos 90 dias):
**p50 = 8,24 dias · p90 = 12,45 · p99 = 107,21 · máximo = 248,25**. A cauda é
longa: revisão continua chegando meses depois.

### 25.3 O mecanismo existente, auditado

| Aspecto | Comportamento |
|---|---|
| Modos | `full` (história inteira, reconstrói o destino, **repara hard delete**) e `incremental` (usa watermark, recalcula por inteiro cada chave tocada) |
| Fonte → destino | `silver.stg_tiktok_payments_by_order` (Data Mart, VPN) → `marts.fact_tiktok_affiliate_cost_order_monthly` (Neon) |
| Watermark | `last_successful_upper_bound` em tabela própria de `sync_state`; `incremental` **sem** watermark **falha** em vez de virar backfill acidental |
| Janela tocada | chaves `(ref_month, brand)` com `updated_at > watermark`, cada uma **recalculada integralmente** |
| Lock | `pg_advisory_lock` de **sessão**, chave `912120012`, `lock_timeout=30s`; releitura do watermark `FOR UPDATE` + `assert_watermark_unchanged` |
| Transação | UMA conexão de destino do início ao fim; transação gravável **curta e no fim**; fato e watermark commitados juntos |
| Fonte vazia | `incremental` **recusa** (não infere hard delete total); só `full` age |
| Hard delete | reparado **apenas** pelo `full`; o incremental é estruturalmente cego |
| Reconciliação | três fronteiras — snapshot, detalhe×agregado, `EXCEPT` bidirecional + chaves + sinais + anti-NaN |
| Códigos de saída | `0` sucesso, `2` falha; mensagem sanitizada em `stderr` |
| Sanitização | `sanitize_error_message`; sem SQL, DSN, host ou credencial |
| Duração **medida** | diagnóstico `incremental` **20–21 s**; diagnóstico `full` **101 s** |
| VPN | obrigatória para a fonte; o Neon é público |
| Concorrência | protegida pelo advisory lock de sessão + watermark sob `FOR UPDATE` |
| Retry | **nenhum, por design** — "não agenda, não dorme, não repete e não tenta de novo" |
| `audit.source_sync_run` | **não registra** — zero ocorrências no módulo |

### 25.4 Ponto de integração — contratos reais, lidos no código

**1. Depois de qual step a fonte está madura? De nenhum.** Confirmado por busca
exaustiva: `silver.stg_tiktok_payments_by_order` tem exatamente duas referências
no repositório — o sync que a lê e este documento. Nenhum step a alimenta.

**`depends_on=()` significa ausência de dependência LÓGICA de outro step, não
execução paralela.** O orquestrador roda os steps em sequência, na ordem da
tupla; `depends_on` só condiciona a execução ao sucesso de um step anterior.
Declarar `depends_on=("daily_tiktok",)` criaria **vínculo falso**: `daily_tiktok`
escreve `raw`/`gold`, nunca a Silver de pagamentos.

**Posição:** após a ingestão e **antes do `health_check`** — que é sempre o
último, por posição na tupla mais `always_run=True`.

**2. `critical=True`**, mesma razão dos steps de serving: `/canais` já lê esta
fact em produção.

**3. BLOCKED do step × FAILED do pipeline — a distinção que faltava.**

A redação anterior dizia "VPN fora → BLOCKED, não FAILED". **Isso estava
enganoso.** O contrato real, lido em `compute_overall_status()`:

```python
has_critical_failure = any(
    critical_by_name.get(step_name, True) and status in ("FAILED", "BLOCKED")
    for step_name, status in results.items()
)
if has_critical_failure:
    return "FAILED"
```

Três camadas que **não** podem ser confundidas:

| Camada | Valor com VPN fora |
|---|---|
| 1. Causa/status do **step** | **`BLOCKED`** — o preflight impede a execução; o comando não roda; zero escrita e zero tentativa do sync são consumidas |
| 2. Resultado operacional do **pipeline** | **`STATUS GERAL: FAILED`** e **exit code 1**, porque o step é `critical=True` e `BLOCKED` crítico entra no mesmo ramo que `FAILED` |
| 3. Estado dos **dados** | **Snapshot anterior preservado e disponível** — nada foi escrito, e `/canais` continua servindo a fotografia publicada |

**Não é verdade que "VPN fora não reprova o `full_daily`".** Reprova. O que
`BLOCKED` preserva é a *causa* (infraestrutura, não defeito do sync) e a
*integridade dos dados*, nunca o resultado verde do pipeline.

**4. Orçamento — e um conflito aritmético que precisa de decisão.**

O invariante está **versionado** em `pipelines/tests/test_ops_s3_wiring.py`:

```python
def test_f24_orcamento_interno_cabe_no_timeout_externo():
    EXTERNO = 9000
    assert orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS == 7500
    margem = EXTERNO - orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS
    assert margem > 0.15 * orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS
```

**DECISÃO FECHADA: `timeout_seconds=300`.**

| Grandeza | Valor decidido |
|---|---|
| `timeout_seconds` do step | **300 s** |
| Orçamento interno do `full_daily` | 7.500 → **7.800 s** |
| Timeout externo do lock | **9.000 s** (preservado) |
| `ExecutionTimeLimit` do Task Scheduler | **9.600 s** (preservado) |
| Margem interna | **1.200 s** |
| Margem relativa | **1.200 / 7.800 = 15,38 %** → satisfaz o invariante estrito `> 15 %` |

Como a aritmética se comporta em torno da decisão:

| `timeout` do step | Orçamento | Margem | Mínimo exigido | Invariante |
|---|---|---|---|---|
| 0 (hoje) | 7.500 | 1.500 | 1.125 | OK |
| 240 | 7.740 | 1.260 | 1.161 | OK |
| **300 (decidido)** | **7.800** | **1.200** | **1.170** | **OK — 15,38 %** |
| 326 (máximo) | 7.826 | 1.174 | 1.173,9 | OK (limite) |
| 400 | 7.900 | 1.100 | 1.185 | QUEBRA |

**Por que 300 s:** o diagnóstico incremental mede 20–21 s e o `full`, 101 s — o
envelope dá **~14× o incremental** e **~3× o full**. Ampliar os timeouts externos
seria alteração operacional maior e desnecessária, tocando o contrato do Task
Scheduler para resolver um problema que 300 s já resolvem.

**`SOURCE_STATEMENT_TIMEOUT = 600 s` permanece inalterado** e não se confunde com
isto: é proteção **por statement da fonte** (§18.8.x). Os 300 s são o **envelope
externo do step no orquestrador**. Se o processo exceder 300 s, o step **falha
explicitamente** e o **snapshot anterior permanece preservado** — não há
publicação parcial, porque a transação de publicação é curta e no fim.

**A Task 2/3 deverá sincronizar** as constantes e comentários hoje presos em
**7.500 s** (`test_f24`, `test_f25`) e em **6.600 s** (comentário defasado de
`schedule_plan.py`, que já não corresponde ao orçamento atual).

**5–6. Colisões e cobertura do lock.**

| Risco | Avaliação |
|---|---|
| Ingestão TikTok | **Nenhuma** — escreve `raw`/`gold`, não a Silver nem esta fact |
| Serving | **Nenhuma** — outros destinos |
| Operação manual | Coberta pelo advisory lock de sessão `912120012` |
| Backfill (`full`) | Coberta pelo mesmo lock — `full` e `incremental` disputam a mesma chave |
| Scheduler | Coberto pelo lock externo da TaskKey `full_daily` |
| Lote upstream das ~21:03 | **Não coberto por lock nosso** — nem precisa: o snapshot `REPEATABLE READ` garante leitura consistente ainda que coincidisse |

**7. Checkout do Scheduler.** O módulo já existe lá. A integração exigirá
atualizar o checkout por causa da edição de `orchestrate.py` — e essa atualização
é **Task 3/3**, não 2/3.

### 25.5 Cadência — 06:00 é escolha operacional, não SLA

**A concentração em 21:02–21:04 BRT foi observada por dez dias. Isso é
evidência, não contrato.** Não existe SLA do time que mantém a fonte, e este
documento **não afirma garantia de upstream que não existe**.

- **06:00 BRT é escolha operacional baseada na evidência atual**: é o ponto mais
  distante das duas bordas observadas (~9 h depois do lote, ~15 h antes do
  próximo). Se o horário da fonte mudar, **isso aparece via watermark e frescor**
  — o contrato de §25.6 detecta, sem depender de aviso do upstream.
- **Fonte não avançou:** `cutoff == watermark` → `WATERMARK_UNCHANGED`, zero linha
  publicada, **`success`**. Um dia sem revisão é normal e precisa ser registrado
  como execução bem-sucedida (§25.7).
- **VPN fora:** step `BLOCKED`, pipeline `FAILED`, snapshot preservado (§25.4-3).
- **Contingência manual:** TaskKey reutilizando o **lock lógico `full_daily`**,
  como `serving_refresh` faz.
- **Zero retry automático** — convenção do módulo, mantida.

### 25.6 Full mensal — obrigação DURÁVEL, não gatilho de calendário

`today.day == 1` **não serve**: se notebook, VPN ou Scheduler falharem no dia 1, o
full sumiria até o mês seguinte. Contrato durável:

1. O `full` é **devido** enquanto não existir **evidência persistida** de full
   bem-sucedido para o **mês operacional corrente** (mês BRT).
2. **Falha ou `BLOCKED` não consomem a obrigação.** Só `success` consome.
3. **Somente full concluído e reconciliado** marca o mês como atendido.
4. Atendido o mês, as execuções seguintes voltam ao **incremental**.
5. O mês seguinte cria **nova obrigação**.
6. O `full` continua sendo **história integral** — é a única defesa contra hard
   delete, e por isso não pode ser trocado por lookback maior.

**Persistência escolhida — sem migration.** O schema real de
`audit.source_sync_run` (migration 003) é:

```
sync_run_id SERIAL PK · source_name VARCHAR(100) NOT NULL · marketplace_id INT
loja_id INT · started_at TIMESTAMPTZ · finished_at TIMESTAMPTZ
status VARCHAR(20) CHECK IN ('running','success','failed')
rows_extracted INT · rows_loaded INT · error_message TEXT
source_min_date DATE · source_max_date DATE
```

`source_name` é **texto livre, sem FK e sem enum** — logo **dois nomes distintos
são compatíveis com o schema atual, sem migration alguma**.

**Desenho proposto — dois nomes, ambos escritos:**

| `source_name` | Quando a linha é CRIADA | Para que serve |
|---|---|---|
| `tiktok_affiliate_cost_order_monthly` | em **toda** execução real, qualquer modo | entrada em `EXPECTED_SOURCES` do health check (30 h), prova que **o job rodou** |
| `tiktok_affiliate_cost_order_monthly_full` | em **toda** execução `full`, **antes** da escrita | auditoria do **ciclo completo** do full — inclusive das tentativas que falharem |

**A linha `_full` é criada no início, não no sucesso.** Escrevê-la só ao terminar
bem serviria de marcador de sucesso, mas **esconderia tentativas de full que
falharam** — e uma tentativa fracassada é exatamente o que se precisa enxergar.

| Modo | Linhas de auditoria | Ciclo |
|---|---|---|
| `incremental` | **uma**, canônica | `running` → `success` \| `failed` |
| `full` | **duas**, canônica + `_full` | ambas `running` → ambas com **o mesmo resultado factual**, `success` \| `failed` |

Regras de atomicidade das duas linhas do `full`:

- **Iniciadas na MESMA transação de auditoria**, para que nunca exista só uma.
- **Finalizadas na MESMA transação de auditoria**, pelo mesmo motivo.
- **Falha ao iniciar qualquer uma aborta antes do sync** — não se publica carga
  que não pode ser observada.
- **Falha ao finalizar depois do commit** pode deixar ambas em `running`, e isso
  deve ser reportado honestamente como **"dados possivelmente publicados,
  auditoria incompleta"** — nunca encenado como rollback.

**A obrigação mensal é consumida SOMENTE se existir**, cumulativamente:

1. linha com `source_name = "tiktok_affiliate_cost_order_monthly_full"`;
2. `status = "success"`;
3. `finished_at` dentro do **mês operacional corrente em `America/Sao_Paulo`**.

`failed`, `running`, ausência da linha ou preflight `BLOCKED` **não consomem** a
obrigação. Nenhuma coluna é usada com significado falso.

**Por que não a variante do enunciado** (o health check considerar "o mais
recente entre os dois nomes"): ela exigiria generalizar `EXPECTED_SOURCES` de
`source_name: str` para um conjunto de nomes, mudando uma estrutura compartilhada
por **dez** fontes existentes. O desenho acima obtém o mesmo resultado
**sem tocar** no health check — o nome canônico já é escrito em todo modo, então
a frescura de execução continua sendo uma consulta a um único nome. Se a
generalização for preferida na Task 2/3, ela é possível; apenas não é necessária.

**Proibições respeitadas:** o modo **não** vai em `error_message`; o full **não**
é inferido por contagem de linhas; a evidência é **persistida em banco**, não em
memória ou arquivo temporário; **nenhuma migration** é proposta.

**Stop-loss registrado:** se, na Task 2/3, a decisão de full mensal exigir
qualquer coluna nova, **pare e reporte** — não invente persistência.

### 25.7 Contrato de `audit.source_sync_run`

Segue o padrão já estabelecido em `sync_serving_snapshots.py`, `sync_produtos.py`
e `daily_performance.py`: `_audit_start` insere `running` e comita; `_audit_finish`
atualiza e comita; **a conexão de auditoria é separada da de dados**.

| Situação | Registro |
|---|---|
| Preflight `BLOCKED` | **Nenhuma linha** — o sync não chegou a iniciar |
| Dry-run / diagnóstico (sem `--apply`) | **Nenhuma linha** |
| Execução real iniciada | `running`, `started_at = NOW()` |
| Sucesso com watermark **avançado** | `success` |
| Sucesso com watermark **inalterado** | **`success` também** — prova que o job rodou e encontrou a fonte sem avanço |
| Falha após o início | `failed`, `error_message` **sanitizada** |
| Retry | **nenhum** |

**Identificação:** `marketplace_id = 1` (TikTok, pela convenção
`MARKETPLACE_LABELS` do health check); `source_name` conforme a tabela da §25.6.

**Semântica das colunas — sem significado falso:**

| Coluna | Significado adotado |
|---|---|
| `rows_extracted` | linhas lidas da fonte no snapshot (`linhas_lidas`) |
| `rows_loaded` | linhas efetivamente publicadas na fact (`published`) |
| `source_min_date` / `source_max_date` | **MIN/MAX de `ref_month`** das linhas publicadas; **`(None, None)`** quando nada foi publicado — mesmo princípio de `source_date_bounds`, que devolve `(None, None)` em vez de fabricar data |
| `error_message` | somente mensagem sanitizada de falha — **nunca** o modo |
| watermark | **não vai para nenhuma coluna daqui** — já tem tabela própria |

**Duas transações distintas, deliberadamente:**

1. **Publicação do fato e do watermark** — uma única conexão gravável, transação
   curta no fim, fato e watermark commitados **juntos**. Preservada intacta: a
   conexão de auditoria **não participa** do DML da fact.
2. **Auditoria operacional** — conexão independente, commit próprio. É o que
   permite a auditoria **sobreviver ao rollback do fato** e registrar `failed`.

**Dois modos de falha, tratados honestamente:**

- **Falha ao INICIAR a auditoria → aborta antes de escrever.** Não se publica uma
  carga que não pode ser observada.
- **Falha ao FINALIZAR a auditoria depois do commit** → a linha permanece
  `running` com `finished_at` nulo. Estado real: **"dados publicados, auditoria
  incompleta"**. Deve ser dito assim, e **nunca** encenado como rollback: o
  commit do fato já aconteceu e desfazê-lo seria mentira. O resíduo `running` é
  observável pelo health check, que compara o último status com o último
  `success`.

### 25.8 Contrato de frescor — quatro estados EXAUSTIVOS

O desenho anterior deixava um buraco: exigia dois lotes parados para declarar
`stale`, então **um lote de atraso ficava sem classificação**. Corrigido:

| Estado | Condição |
|---|---|
| `unknown` | auditoria insuficiente **ou** watermark ausente |
| `manual_snapshot` | **estado atual**, preservado até a Task 3/3 comprovar a rotina |
| `fresh` | **as duas** condições: (1) última execução `success` há **≤ 30 h**; (2) `watermark_date >= expected_batch_date` |
| `stale` | **qualquer uma** das duas condições de `fresh` falha |

Os quatro cobrem todo o espaço: sem dado → `unknown`; em transição →
`manual_snapshot`; com dado, ou as duas condições valem (`fresh`) ou não valem
(`stale`). **Um lote de atraso já é `stale`.**

**Lote esperado — e uma distinção de tipo que não pode ser borrada.**

⚠️ **O watermark é `TIMESTAMP WITHOUT TIME ZONE`** (migration 012):
`last_successful_upper_bound` é **naive**. Ele **não carrega fuso**, e por isso
este contrato **não o converte nem o rotula como BRT**. `finished_at` da
auditoria, ao contrário, é `TIMESTAMPTZ` — esse **sim** é timezone-aware e **deve**
ser convertido para `America/Sao_Paulo`.

- `D_exec` = **data BRT** do `finished_at` da última execução **canônica**
  `success` (conversão legítima: a coluna é aware).
- `watermark_date = DATE(last_successful_upper_bound)` — apenas a **parte de data
  já armazenada**, sem conversão e sem rótulo de fuso.
- `expected_batch_date = D_exec − 1 dia`.
- Comparação: **`watermark_date >= expected_batch_date`** — sempre `>=`, **nunca
  igualdade**. Uma execução manual mais tarde no dia D pode alcançar o lote de D e
  continua válida.

**Por que comparar datas de tipos diferentes é defensável aqui, e onde está o
limite:** o lote observado fecha perto das 21 h, **longe da fronteira de
meia-noite** em qualquer das duas leituras, então a parte de data do watermark é
operacionalmente estável. Isso é **evidência empírica** (§25.1), **não** timezone
embutido na coluna. Se o horário do lote migrar para perto da virada do dia, esta
premissa deixa de valer e o contrato precisa ser revisto — e é o próprio frescor
que dará o sinal.

Conferindo com o dado real: `last_successful_upper_bound = 2026-08-25 00:11:55`
(naive) → `watermark_date = 2026-08-25`. Uma execução canônica em 25/08 às 06:00
BRT dá `D_exec = 2026-08-25` e `expected_batch_date = 2026-08-24`;
`2026-08-25 >= 2026-08-24` → **satisfeito**.

**Contando "dois lotes sem avanço"** — nem idade do processo, nem idade da fact:

```
atraso_em_lotes = max(0, expected_batch_date − watermark_date)
```

O **`max(0, …)` é obrigatório**: uma execução manual pode avançar o watermark
**além** do mínimo esperado, e isso jamais pode produzir atraso negativo.

| `atraso_em_lotes` | Leitura |
|---|---|
| 0 | em dia → `fresh` (se a execução também estiver ≤ 30 h) |
| ≥ 1 | **`stale`** |
| ≥ 2 | `stale` **+ ALERTA/ESCALONAMENTO** |

Dois lotes é limiar de **escalonamento**, jamais condição para começar a chamar
o dado de `stale`.

**Execução recente com fonte parada NÃO é fresh** — é exatamente o caso
`atraso_em_lotes ≥ 1` com execução dentro de 30 h: a condição (1) passa, a (2)
falha, e o resultado é `stale`. É por isso que as duas condições existem.

**Falha não derruba `/canais`:** `safe_affiliate_costs_block` já garante (§24).
**A UI preserva a última fotografia e avisa que está desatualizada** — `stale`
mostra os valores com aviso explícito, em vez de esconder o dado.

### 25.9 Observabilidade

- **Step** `sync_afiliados_tiktok` no `full_daily`, `critical=True`,
  `depends_on=()`, `preflight_source` próprio para a VPN, timeout conforme
  §25.4-4.
- **Health check:** uma entrada em `EXPECTED_SOURCES` com o **nome canônico**,
  `exec_threshold_hours=30`, `critical=True`. Mais um check de cobertura medindo
  `atraso_em_lotes`.
- **Métricas mínimas por execução:** início/fim; modo; cutoff; watermark
  anterior/novo; chaves tocadas; linhas publicadas; resultado do `EXCEPT`;
  duração; status.
- **Alertas:** falha do step; **`atraso_em_lotes >= 2`**; **cobertura incompleta
  nova** (competência que era 5/5 e deixou de ser).
- **Distinção obrigatória:** *fonte sem avanço* (job `success`, watermark igual)
  × *job quebrado* (`failed`/exit 2). Hoje quem olha só `synced_at` não separa os
  dois.

### 25.10 Plano — Tasks 2/3 e 3/3

**Task 2/3 — implementação SEM execução operacional.**

Integrar o step ao `full_daily`; implementar a decisão incremental/full mensal
durável (§25.6); adicionar preflight; registrar auditoria (§25.7); integrar ao
health check; implementar a classificação de frescor **em código e testes**
(§25.8). **A resposta pública permanece `manual_snapshot`.**

Restrições: **zero `--apply`**, **zero atualização do checkout operacional**,
**zero execução do Scheduler**, **zero ativação de `fresh`/`stale`** na resposta.

Arquivos prováveis: `pipelines/ops/orchestrate.py`, `pipelines/ops/preflight.py`,
`pipelines/ops/health_check.py`, `pipelines/ops/schedule_plan.py` (comentário
defasado), `pipelines/sync_tiktok_affiliate_cost_order_monthly.py`,
`apps/api/app/services/affiliate_costs_service.py`,
`apps/web/src/lib/canais-affiliate-costs.ts`, mais testes em `pipelines/tests/`
(incluindo a atualização de `test_f24`/`test_f25`) e `apps/api/tests/`.

**Task 3/3 — ativação controlada**, nesta ordem: (1) preflight real; (2) **um
único** piloto autorizado com `--apply`; (3) reconciliação completa; (4)
comprovação da auditoria; (5) atualização coordenada do checkout operacional;
(6) observação de execução **agendada** real; (7) comprovação do comportamento
com **fonte sem avanço**; (8) comprovação controlada de **falha/`BLOCKED`**; (9)
**somente após evidência suficiente**, trocar `manual_snapshot` por
`fresh`/`stale`; (10) QA focal da API e da interface.

**Falhas devem ser simuladas por injeção/fakes** sempre que possível — nada de
escritas artificiais só para testar.

### 25.11 Stop-loss

**Pare e reporte, sem implementar**, se qualquer solução exigir: mudança no
cálculo dos três componentes; soma dos componentes; `abs()`; alteração de grão;
mudança da **migration 012 já aplicada**; alteração do significado do watermark;
nova dependência; retry/backoff; Airflow; novo Scheduler; migration nova não
prevista; alteração da interface **além** do estado de frescor; leitura de fonte
diferente; ou **vínculo falso com `daily_tiktok`**.

### 25.12 Riscos e bloqueios

| # | Item | Estado |
|---|---|---|
| A | **Fonte externa sem SLA contratual** — outro time, transformação não versionada; 21:02–21:04 é evidência de 10 dias, não garantia | **Aberto** |
| B | **Hard delete só o `full` repara** | Mitigado pelo full mensal durável (§25.6) |
| C | **Cauda de revisão de 248 dias** | Aceito: o incremental recalcula por inteiro cada chave tocada |
| D | **Módulo não registra em `audit.source_sync_run`** | **Pré-requisito** da Task 2/3 (§25.7) |
| E | Dimensionamento do step × invariante `test_f24` | **Decidido:** `timeout=300`, orçamento 7.800 s, margem 15,38 % (§25.4-4). A Task 2/3 sincroniza as constantes de 7.500 e o comentário de 6.600 |
| F | Comentário defasado em `schedule_plan.py` ("6600s") | Sincronizar na Task 2/3 |
| G | Dependência de VPN; `full_daily` exige notebook ligado e usuário logado | Dívida operacional preexistente, fora desta frente |
| H | Convenção contábil do sinal e sobreposição dos componentes | **Seguem abertas**, fora do escopo da UE2-C; retorno de afiliados **continua indisponível** |

---

## 26. UE2-C Task 2/3 — automação implementada, NÃO executada

⚠️ **UE2-C não está concluída.** Esta seção registra código e testes prontos
para revisão. **Nada foi executado:** zero `--apply`, zero escrita em banco,
zero alteração do Task Scheduler, zero atualização do checkout operacional, zero
deploy. **A Task 3/3 não foi iniciada.**

### 26.1 Modo `auto` e a obrigação mensal durável

O CLI passou a aceitar `--mode auto`, e é ele que o `full_daily` usa.
`full` e `incremental` explícitos preservam o comportamento anterior.

**A decisão acontece SOB o advisory lock**, nesta ordem exata dentro de
`_run_apply`:

```
1. abre a única conexão do destino (autocommit)
2. adquire pg_advisory_lock(912120012)          <- lock de sessão
3. DECIDE o modo efetivo, na MESMA conexão      <- UE2-C
4. abre a auditoria e insere as linhas running  <- UE2-C
5. lê o watermark autoritativo
6. abre a fotografia REPEATABLE READ da fonte
7. lê e valida em memória
8. transação gravável curta: staging, publicação, watermark
9. commit único
10. finaliza a auditoria
```

Um wrapper que decidisse **antes** do lock abriria corrida: duas execuções
leriam a mesma ausência de full no mês, ambas escolheriam `full`, e a segunda
reconstruiria o destino em cima da primeira. Um teste estrutural (AST sobre o
corpo da função) trava as posições relativas de lock, decisão, auditoria, fonte
e publicação.

**A obrigação mensal só é consumida** por uma linha
`tiktok_affiliate_cost_order_monthly_full` com `status='success'` e
`finished_at` dentro do mês operacional **BRT**. `failed`, `running`, ausência
ou sucesso de mês anterior **não consomem** — e a prova está no próprio SQL, que
filtra `status = 'success'`, delimita o mês por `AT TIME ZONE
'America/Sao_Paulo'` e não toca `started_at`, `error_message` nem contagem.
Nada de `today.day == 1`, arquivo temporário ou memória de processo.

O `run_id` default reflete o **modo efetivo**, nunca "auto" — rotular a execução
com o modo pedido seria falso. `--run-id` explícito é preservado após
sanitização.

### 26.2 Auditoria — duas linhas no `full`, criadas no início

| Modo | Linhas | Ciclo |
|---|---|---|
| `incremental` | uma, canônica | `running` → `success` \| `failed` |
| `full` | **duas** (canônica + `_full`) | ambas `running` → ambas com o mesmo resultado |

As duas são inseridas **numa única transação de auditoria** e finalizadas
**noutra única transação** — se a segunda falhasse depois de a primeira comitar,
existiria um `full` sem marcador auditável, e o mês seguinte poderia se declarar
atendido por uma execução que ninguém consegue auditar.

**A conexão de auditoria é independente da conexão da fact.** É isso que permite
registrar `failed` sobrevivendo ao rollback da publicação. Um teste estrutural
prova que `_audit_start`/`_audit_finish` só referenciam `audit.source_sync_run`
— jamais a fact, a staging, o `sync_state` ou o advisory lock.

**Semântica das colunas, sem significado falso:** `rows_extracted` = linhas
lidas da fonte; `rows_loaded` = linhas publicadas; `source_min_date`/
`source_max_date` = MIN/MAX de `ref_month` **publicado**, e `(None, None)`
quando nada foi publicado; `error_message` só recebe erro sanitizado. O
watermark **não vai para coluna nenhuma** daqui — `_audit_finish` sequer aceita
o parâmetro, e um teste trava isso.

**Full sobre fonte vazia** não passa por `publish_in_transaction`, então
`rows_loaded` iria a `NULL` — que significa "não se sabe" — quando o fato
conhecido é **zero**. Registra-se `rows_extracted=0` e **`rows_loaded=0`**, com
`source_min_date`/`source_max_date` nulos, e as **duas** linhas recebem
exatamente os mesmos valores. Nem mês nem watermark são fabricados.

**Máquina de estados da publicação.** A auditoria só pode dizer `failed`
quando se **prova** que nada foi publicado. O estado é rastreado explicitamente:

| Estado | Quando | Auditoria |
|---|---|---|
| `nao_tentada` | falha antes de a transação gravável chegar ao commit | **`failed`** — nada publicado |
| `rollback_confirmado` | falha na transação, rollback efetuado | **`failed`** — nada publicado |
| `commit_confirmado` | `commit()` retornou | **nunca `failed`** |
| `indeterminada` | `commit()` levantou — ninguém sabe se o servidor efetivou | **nem `failed` nem `success`** |

**Falha ao FINALIZAR a auditoria depois do commit** não marca `failed`: isso
afirmaria que os dados não foram publicados — falso, e no `full` faria a
obrigação mensal parecer não atendida, provocando **outra reconstrução**. As
linhas ficam em `running`, que é o resíduo observável correto, e o erro sobe
como `AuditoriaIncompleta` com mensagem sanitizada. **Nenhum rollback é
encenado** — o commit já aconteceu.

**Commit indeterminado** também não recebe rollback: depois de um `commit()` que
levantou, o rollback não esclarece nada e pode levantar por cima da exceção
original. `running` é o único registro honesto de "não se sabe".

**Ao INICIAR** → aborta antes da fonte e antes de qualquer escrita. Não se
publica carga que não pode ser observada.

**Atualização integral.** Cada `UPDATE` da auditoria exige `rowcount == 1`;
`0` (id inexistente) ou `>1` derrubam a transação inteira, para que as duas
linhas de um `full` nunca terminem pela metade. `status` só aceita
`success`/`failed`.

Diagnóstico sem `--apply` cria **zero** linha. Preflight `BLOCKED` também —
o processo nem chega a abrir. **Zero retry**, como no resto do módulo.

### 26.3 Preflight

Fonte `tiktok_affiliate_cost_order_monthly` registrada, com quatro checks
estritamente read-only: Data Mart, Neon, existência das relações das migrations
012 e 003, e prova **barata** de que a fonte não está vazia — duas consultas com
`LIMIT 1`, **nenhum `COUNT(*)` integral** sobre 2,1 milhões de linhas. As
validações profundas continuam dentro do sync.

**Fonte vazia BLOQUEIA de propósito.** No caminho automatizado, um `full` sobre
fonte vazia esvaziaria a fact. Se a fonte realmente deve estar vazia, isso é
decisão operacional explícita, com `--mode full --apply` manual.

### 26.4 `full_daily` — 13 steps

Um único step novo, `tiktok_affiliate_cost_order_monthly`, **entre os snapshots
e o `health_check`**, que continua comprovadamente o último (posição na tupla +
`always_run=True`, travado por teste).

| Campo | Valor |
|---|---|
| `module` | `pipelines.sync_tiktok_affiliate_cost_order_monthly` |
| `args` | `("--mode", "auto", "--apply")` |
| `timeout_seconds` | **300** |
| `preflight_source` | `tiktok_affiliate_cost_order_monthly` |
| `depends_on` | `()` — ausência de dependência **lógica**, não paralelismo |
| `critical` | `True` |
| `always_run` | `False` |

**Consequência assumida e testada:** VPN fora deixa o step em `BLOCKED`; como ele
é crítico, `compute_overall_status` devolve **`FAILED`** e o `full_daily` sai com
**exit 1**. O que fica preservado é o snapshot publicado, não o resultado verde.

Nenhum pipeline novo, nenhuma TaskKey nova, nenhuma entrada nova no Scheduler,
nenhum Airflow, nenhum retry, nenhum segundo agendamento.

### 26.5 Orçamento

| Grandeza | Antes | Agora |
|---|---|---|
| Orçamento interno do `full_daily` | 7.500 s | **7.800 s** |
| Timeout externo do lock | 9.000 s | 9.000 s |
| `ExecutionTimeLimit` | 9.600 s | 9.600 s |
| Margem | 1.500 s (20,0 %) | **1.200 s (15,38 %)** |

Acima do invariante estrito de 15 %. Sincronizados: o comentário de
`orchestrate.py`, o comentário e a constante de `schedule_plan.py` (que ainda
diziam **6.600 s**), e os testes que fixavam **7.500** em
`test_ops_orchestrate.py`, `test_ops_s3_wiring.py` e `test_ops_schedule_plan.py`.
`SOURCE_STATEMENT_TIMEOUT = 600 s` **não mudou**: é proteção por statement da
fonte, não o envelope do step.

### 26.6 Health check

Uma entrada em `EXPECTED_SOURCES` com o nome **canônico**, 30 h, `critical=True`.
O nome `_full` **não** entra: ele marca um ciclo mensal, e cobrar 30 h dele
reprovaria o pipeline todo dia.

Mais uma verificação nova, `affiliate_watermark`, que separa as duas dimensões:
execução (última canônica `success`) e avanço da fonte (`sync_state`). O
relatório distingue *job não executou/falhou*, *job executou mas a fonte não
avançou*, *ambos saudáveis* e *desconhecido*.

**Fail-closed, e a distinção importa:**

| Situação | Estado | Reprova **nesta dimensão**? |
|---|---|---|
| Consulta OK, **sem linha** de watermark | `unknown` | **Não** — não há o que avaliar |
| **Erro de banco** ao ler o watermark | **`error`** | **Sim** — `stale=True`, `critical=True` |
| Erro que **não é** de banco | propaga | bug de código não vira "erro de fonte" |

⚠️ **`unknown` não reprova NESTA dimensão — e só nesta.** A frase "unknown não
reprova antes do piloto", usada numa redação anterior, era **enganosa**: ela
sugeria que o health check ficaria verde antes da primeira execução, e não fica.

As duas dimensões são **ortogonais** e nenhuma sobrescreve a outra:

| Dimensão | Onde | O que mede | Decide `ok_critical`? |
|---|---|---|---|
| **Execução** | entrada canônica em `EXPECTED_SOURCES` | houve execução bem-sucedida nas últimas 30 h? | **Sim** |
| **Watermark** | `affiliate_watermark` | a fonte avançou até o lote esperado? | Só quando é `error` |

**No pré-piloto real** — nenhuma execução registrada, nenhuma linha de
watermark — o resultado correto é:

- `affiliate_watermark.status == "unknown"` e `stale == false`;
- a entrada canônica em `sources` com `stale=true` e `critical=true`;
- **`ok_critical == false`**.

Isso é intencional. Uma rotina já declarada `critical=True` **não pode** deixar o
health check verde antes da sua primeira execução comprovada. O `unknown` do
watermark não acrescenta uma segunda reprovação, mas também **não neutraliza** a
da execução. Quatro testes integrados de `build_report` fixam exatamente esse
comportamento, incluindo a contraprova de execução saudável com watermark
ausente.

Converter erro técnico em `unknown` mascararia permissão revogada, relação
ausente, schema incompatível e conexão abortada — justamente atrás do estado
não-crítico que significa "ainda não ativado". A mensagem do estado `error` é
**categoria fixa**: nada de SQL, DSN, host, usuário ou texto bruto do driver. A
transação é restaurada antes de o relatório seguir.

O contrato de frescor vive no módulo que **owns** o watermark, e o health check
o **importa** em vez de reimplementar.

### 26.7 Frescor — implementado, ainda não publicado

```
D_exec              = data BRT do finished_at (TIMESTAMPTZ) da última execução canônica success
watermark_date      = DATE(last_successful_upper_bound)     -- naive, sem conversão
expected_batch_date = D_exec − 1 dia
fresh   ⇔ idade ≤ 30 h  E  watermark_date >= expected_batch_date
stale   ⇔ qualquer uma falha
unknown ⇔ falta auditoria ou watermark
atraso_em_lotes = max(0, expected_batch_date − watermark_date)
```

Um lote de atraso **já é** `stale`; dois escalam o **alerta**. `max(0, …)`
impede atraso negativo quando uma execução manual ultrapassa o lote mínimo.

**A resposta pública continua em `manual_snapshot`.**
`build_affiliate_costs_block` **não chama** a função de classificação — há teste
estrutural provando isso, e outro varrendo os seis estados do bloco mais o
caminho de erro para garantir que **nenhum** devolve `fresh`/`stale`. Schema
público intocado (13 campos), frontend intocado, zero mudança visual.

Existem duas implementações — uma em `pipelines`, outra em `apps/api` — porque
as duas árvores não se importam. **Um teste compara as duas sobre a mesma tabela
de casos**, para que não possam divergir em silêncio.

### 26.8 Validação executada

| Verificação | Resultado |
|---|---|
| Testes focais novos (`test_ue2c_automacao_afiliados.py`) | **52 passed** |
| Focais da API de afiliados | **70 passed** |
| Suíte `pipelines/` | **2.854 passed, 1 failed** |
| Baseline `pipelines/` | 2.800 passed, 1 failed |
| **Comparação por node ID** | **idêntica — novas: [], sumidas: []** |
| Suíte `apps/api/tests` | **760 passed, 43 failed, 8 skipped** |
| Baseline `apps/api` | 747 passed, 43 failed |
| **Comparação por node ID** | **idêntica — novas: [], sumidas: []** |
| `compileall` dos módulos alterados | OK |
| Startup/import da API | OK; `/canais` registrada; bloco com 13 campos |

A única falha de `pipelines/` é `test_j09_fracionarios_pequenos_nao_perdem_precisao`,
**pré-existente** (soma de fracionários no Python 3.14) e fora desta frente. As
43 de `apps/api` são as falhas ambientais de sempre.

### 26.9 O que NÃO foi feito, e continua pendente

- **Nenhuma execução.** Zero `--apply`, zero escrita, zero Scheduler, zero
  deploy, zero atualização do checkout operacional.
- **`manual_snapshot` ainda é o estado público.**
- **O full mensal nunca foi observado** — a decisão `auto` foi provada por teste,
  nunca contra o Postgres real.
- **A auditoria nunca foi comprovada em Postgres real** — os INSERT/UPDATE em
  `audit.source_sync_run` rodaram apenas contra fakes.
- **A latência real do step automatizado não foi medida.**
- **Task 3/3 não iniciada.**

### 26.10 Ajustes de contrato feitos em testes existentes

Três guardrails legítimos mudaram, e nenhum foi afrouxado sem substituto:

1. **`test_f1_nao_existe_segunda_fabrica_de_conexao_gravavel`** proibia qualquer
   segunda fábrica de conexão. `_neon_audit` **é** gravável, e precisa ser. O
   teste passou a fixar a lista exata das três fábricas, e ganhou um **teste
   novo** provando que a auditoria só toca `audit.source_sync_run` — mais
   específico do que o anterior, não menos.
2. **Allowlist de imports** passou a aceitar `zoneinfo`, **biblioteca padrão**
   desde o Python 3.9. Não é dependência nova; o intento do teste (nenhuma
   dependência de terceiros) continua intacto.
3. **Listas de ordem dos steps** (12 → 13) e **contagem de `EXPECTED_SOURCES`**
   (10 → 11) atualizadas, preservando as asserções de que as regras das fontes
   antigas não mudaram.

---

## 27. UE8-I1 — descontos do pedido TikTok: implementado, NADA executado

### 27.1 Estado

**IMPLEMENTADO, VALIDADO LOCALMENTE E VERSIONADO NESTE COMMIT.**

- migration `013` **escrita, NÃO aplicada**. A tabela
  `marts.fact_tiktok_order_discounts_daily` **não existe no Neon**;
- sync escrito, **nunca executado com `--apply`**;
- **zero carga**, zero escrita em Data Mart ou Neon;
- **UE8-I2 não começou**: piloto, backfill e medição de duração são daquele gate;
- **nenhum timeout de step e nenhuma alteração de Scheduler** foram definidos;
- API, frontend e `full_daily` intocados.

### 27.2 O que a tabela é, e o que não é

Grão `(ref_date, brand)`, competência **comercial** (`created_at::date` do
pedido). Dois descontos com **financiadores diferentes**, deliberadamente sem
total:

| Coluna | Fórmula | Financiador |
|---|---|---|
| `seller_discount_signed` | `-SUM(seller_discount)` | **marca** — reduz receita |
| `platform_subsidy_amount` | `SUM(platform_discount)` | **TikTok** — não reduz receita |

**Não é** receita econômica (depende de refunds e settlement), **não é** caixa
(pertence à competência do statement), **não é** margem (faltam CMV e Shop Ads) e
**não é** maturidade financeira. As três continuam separadas e bloqueadas.

### 27.3 Limites que precisam sobreviver à leitura

1. **A fonte é snapshot mutável.** `raw.tiktok_shop_orders` tem
   `uk_tiktok_orders UNIQUE (order_id)`: uma linha por pedido, atualizada em
   lugar, sem histórico de versões. A tabela **nunca fica madura** — um dia
   fechado pode mudar retroativamente. Medido: julho/2026 perdeu 8 pedidos e
   R$ 660,25 entre 28/08 e 01/09.
2. **Cobertura observada não é prova de ingestão completa.**
   `coverage_status = "complete"` significa apenas que a grade **observada**
   (data × marca com ao menos um pedido) está completa. Não existe manifesto por
   data × marca; `audit.source_sync_run` guarda janela, não grade.
3. **Marca ausente na janela não bloqueia sozinha.** Ausência de vendas e buraco
   de ingestão são indistinguíveis com as fontes atuais: o sync registra
   `warn` em `audit.data_quality_check` e segue.
4. **`full` e `backfill` ainda não foram medidos.** O `full` lerá ~2,7 milhões de
   pedidos numa réplica que já cancelou consultas por `conflict with recovery`.
   Nenhuma afirmação sobre duração foi feita.

### 27.4 Correção pré-piloto — oito findings

A primeira versão foi **segurada antes do piloto**. Dois findings fariam a
carga inicial falhar; os demais produziriam auditoria incorreta ou lock
persistente.

| # | Defeito | Correção |
|---|---|---|
| F1 | `NON_COMMERCIAL_ORDER_STATUSES` inclui `CANCELLED`, que tem população própria → **dupla contagem** no fechamento | `UNPAID_ON_HOLD_STATUSES` **derivada** (nunca copiada) da constante do conector |
| F2 | `SUM(...) FILTER` de população vazia devolve **NULL** contra colunas `NOT NULL` | `CASE WHEN COUNT(*) FILTER (...) = 0 THEN 0::numeric ELSE ... END` nos 6 monetários. **Sem `COALESCE`** — ele não distinguiria população vazia de nulo de origem, e o segundo **tem** de bloquear |
| F3 | `PUBLICACAO_INDETERMINADA` existia mas nunca era atribuída | marcada **antes** de `commit()`. Commit que levanta: zero rollback, zero `failed`, auditorias em `running`, log sanitizado, nenhuma nova tentativa |
| F4 | advisory lock **de sessão** podia vazar ao devolver a conexão ao pool | trocado por **`pg_advisory_xact_lock`**; `SQL_UNLOCK` e o `finally` de unlock removidos. Liberado pelo commit ou rollback do PostgreSQL |
| F5 | `--mode full --date-from ... --apply` gravaria `_full` + `success` **sem reconstruir histórico** | janela explícita **bloqueada em `--apply`**, antes do lock, da auditoria e de qualquer conexão. Continua permitida em dry-run |
| F6 | um só campo de procedência para **dois relógios distintos** | `source_max_updated_at = MAX(updated_at_tiktok)` (TikTok) e `raw_max_updated_at = MAX(updated_at)` (nossa ingestão). `synced_at` é o terceiro |
| F7 | `failed_rows` ficava **zero** quando uma marca inteira sumia | dois checks separados: `ftodd_cobertura_chaves_observadas` e `ftodd_cobertura_marcas_do_escopo`, ambos `warn`, com contagem honesta |
| F8 | `build_quality_checks` chamava `last_closed_date()` de novo | recebe o **mesmo teto** que resolveu a janela; na fronteira da meia-noite BRT não diverge |

**Correção factual da migration:** a afirmação de que `updated_at` só existe
desde 2026-03-12 foi **removida** — é verdade para
`raw.tiktok_payments_by_order`, não para `raw.tiktok_shop_orders`. O preflight
desta tabela mediu, sobre 2.692.671 pedidos deduplicados: `updated_at` com
mínimo **2026-06-12** e `updated_at_tiktok` com mínimo **2025-06-06**, **zero
nulos nos dois**. A medição não se estende a outras tabelas.

**CHECKs de sinal mantidos.** Uma mudança de convenção de sinal na fonte
**bloqueará a carga** e exigirá nova arbitragem. Não relaxar automaticamente.

### 27.5 Dry-run real, read-only

Uma execução do CLI sem `--apply`, contra a fonte real, com `DATABASE_URL`
apontado para um sentinela inválido — qualquer tentativa de abrir sessão
gravável falharia em vez de escrever.

| Métrica | Valor |
|---|---|
| Janela | 2026-08-22 → **2026-08-31** (D−1 respeitado) |
| Linhas produzidas | **50** = 10 dias × 5 marcas |
| Pedidos comerciais | 66.078 |
| Cancelados | **17.774** |
| `UNPAID`/`ON_HOLD` | 1.024 |
| Status desconhecido | **0** |
| `coverage_status` | `complete`, 0 chaves ausentes, 0 marcas ausentes |
| Checks de qualidade | **7/7 `pass`** |
| Escrita | **zero** — Neon, `source_sync_run` e `data_quality_check` intocados |

O fechamento `comercial + cancelado + unpaid/on_hold + desconhecido =
total_dedup` passou **com 17.774 cancelados reais** — é a prova de campo do F1:
com a tupla antiga, o fechamento teria quebrado e a execução abortado.

**O dry-run encontrou um bug que os fakes não pegariam:** `updated_at_tiktok`
estava no `SELECT` externo mas **não na projeção da CTE `raw_dedup`**. Corrigido
antes da segunda execução, que passou.

### 27.6 Arquivos

| Arquivo | Estado |
|---|---|
| `apps/api/alembic/versions/013_create_fact_tiktok_order_discounts_daily.py` | novo — **não aplicado** |
| `pipelines/sync_tiktok_order_discounts_daily.py` | novo — **nunca executado com `--apply`** |
| `pipelines/tests/test_sync_tiktok_order_discounts_daily.py` | novo, **80 testes** |
| `apps/api/tests/test_s3_migrations.py` | pino do head 012 → 013 |
| `docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md` | esta seção |

**UE8-I2 não iniciado.** A tabela não existe no Neon; nenhuma carga foi feita;
nenhum timeout de step ou alteração de Scheduler foi definido.
