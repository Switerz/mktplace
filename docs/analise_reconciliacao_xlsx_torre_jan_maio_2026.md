# Reconciliação XLSX × Torre de Marketplaces — janeiro a maio de 2026

Data da análise: 21/07/2026  
Fonte de verdade definida pelo negócio: `[GoBeaute Marketplaces] Análise de métricas e resultados.xlsx`  
Escopo: Mercado Livre, TikTok Shop e Shopee, por marca, canal e mês, de janeiro a maio de 2026.

> Este documento registra diagnóstico e recomendações para análise futura. Nenhuma regra, carga ou dado de produção foi alterado.

## 1. Conclusão executiva

Os números não deixam de bater por uma única causa:

1. **TikTok:** há uma diferença clara de conceito monetário. O XLSX se comporta como venda de produtos sem o frete pago pelo comprador, enquanto a Torre usa um GMV próximo ao valor total do pedido. No `raw`, `sub_total` é muito mais próximo do XLSX que `total_amount`. Entretanto, a Gold usada pela Torre é uma tabela física e seu `gmv` não coincide exatamente com nenhuma das duas somas atuais do `raw`, portanto também existe uma transformação/snapshot não rastreável no repositório.
2. **Mercado Livre:** março e abril têm cargas parciais graves em Kokeshi e Lescent e, em menor escala, em Barbours. Nos meses com boa cobertura, as diferenças não são explicadas por reembolsos incluídos no GMV. Ocorre o contrário: a Gold aceita somente pedido cujo status atual é `paid`; pedidos reembolsados estão quase todos como `cancelled` e os demais como `partially_refunded`, logo já são excluídos. Persistem diferenças de fotografia temporal, competência/status e, em alguns casos, origem ou conceito do XLSX.
3. **Shopee:** a regra atual soma `subtotal` de todo pedido cujo status não é `Cancelado`. Isso mantém devolvidos/reembolsados dentro do GMV. O XLSX se aproxima de `Vendas - Canceladas - Devolvidas/Reembolsadas`, o que confirma uma divergência semântica na regra da Torre, acrescida de diferenças menores de snapshot/arquivo.
4. **Neon e frontend não são a origem principal dos desvios.** Para TikTok, Neon reproduziu a Gold nas 25 células. Para Mercado Livre, as diferenças Neon × Gold foram pontuais e pequenas diante dos erros XLSX × Data Mart. O site apenas apresenta o fato consolidado do Neon.

## 2. Arquitetura e ponto em que o erro nasce

Fluxo observado:

```text
TikTok/ML APIs → raw no Data Mart → Gold no Data Mart → sincronização → Neon → API → site
Shopee XLSX/exportações → parser local → Neon → API → site
```

O frontend consulta a API pública, que agrega `marts.fact_marketplace_daily_performance` no Neon. A integridade básica dessa fato estava boa na inspeção: não foram encontrados grãos duplicados, chaves nulas ou datas futuras. Assim, para esta reconciliação, o desvio nasce predominantemente **antes da apresentação**:

- TikTok: na definição/origem da Gold;
- Mercado Livre: na extração histórica e na regra `status = 'paid'` sobre o estado atual;
- Shopee: na regra do parser.

## 3. TikTok Shop

### 3.1 O que significam `sub_total` e `total_amount` nos dados atuais

Pela relação empírica nos pedidos de janeiro a maio:

- `sub_total`: valor dos produtos do pedido após os ajustes/descontos aplicáveis, sem o frete final cobrado do comprador;
- `shipping_fee`: frete efetivamente cobrado do comprador;
- `total_amount`: montante total do pedido pago/cobrado do comprador, contendo `sub_total`, frete e pequenos ajustes adicionais.

Nos pedidos não cancelados:

```text
total_amount ≈ sub_total + shipping_fee + pequenos ajustes
```

Exemplos:

| Marca/mês | `sub_total` | `total_amount` | `shipping_fee` | total − subtotal − frete |
|---|---:|---:|---:|---:|
| Barbours/abr | R$ 8.627.777,00 | R$ 9.062.129,20 | R$ 397.506,55 | R$ 36.845,65 |
| Kokeshi/abr | R$ 2.145.126,99 | R$ 2.274.322,89 | R$ 119.914,09 | R$ 9.281,81 |
| Ápice/mai | R$ 797.440,60 | R$ 839.382,90 | R$ 37.438,07 | R$ 4.504,23 |

Portanto, **sim: `sub_total` faz mais sentido para reproduzir o “Total Faturado” do XLSX**, se a definição desejada for faturamento dos produtos sem frete do comprador. Ele não deve ser adotado às cegas antes de definir o tratamento de cancelamentos, devoluções e reembolsos.

### 3.2 Comparação mês a mês

`Δ Torre` e `Δ subtotal` usam o XLSX como denominador. O subtotal é calculado no `raw` atual para pedidos cujo status atual não é `CANCELLED`.

| Marca | Mês | XLSX | Torre | Δ Torre | `raw.sub_total` | Δ subtotal |
|---|---:|---:|---:|---:|---:|---:|
| Ápice | Jan | R$ 275.052,29 | R$ 295.989,74 | +7,61% | R$ 270.841,11 | -1,53% |
| Ápice | Fev | R$ 218.642,80 | R$ 234.833,13 | +7,40% | R$ 213.564,04 | -2,32% |
| Ápice | Mar | R$ 406.685,38 | R$ 436.414,17 | +7,31% | R$ 395.296,07 | -2,80% |
| Ápice | Abr | R$ 592.620,27 | R$ 637.021,59 | +7,49% | R$ 587.476,96 | -0,87% |
| Ápice | Mai | R$ 825.877,19 | R$ 876.174,24 | +6,09% | R$ 797.440,60 | -3,44% |
| Barbours | Jan | R$ 5.738.344,14 | R$ 5.970.025,88 | +4,04% | R$ 5.701.482,25 | -0,64% |
| Barbours | Fev | R$ 10.078.610,82 | R$ 10.606.317,24 | +5,24% | R$ 9.920.778,37 | -1,57% |
| Barbours | Mar | R$ 11.155.728,69 | R$ 11.830.495,41 | +6,05% | R$ 10.984.939,88 | -1,53% |
| Barbours | Abr | R$ 8.643.111,75 | R$ 9.166.934,11 | +6,06% | R$ 8.627.777,00 | -0,18% |
| Barbours | Mai | R$ 9.144.229,08 | R$ 9.709.786,56 | +6,18% | R$ 9.072.277,92 | -0,79% |
| Kokeshi | Jan | R$ 1.307.811,61 | R$ 1.365.370,44 | +4,40% | R$ 1.300.398,18 | -0,57% |
| Kokeshi | Fev | R$ 1.075.851,49 | R$ 1.141.493,40 | +6,10% | R$ 1.042.429,89 | -3,11% |
| Kokeshi | Mar | R$ 2.170.740,47 | R$ 2.325.091,54 | +7,11% | R$ 2.140.866,55 | -1,38% |
| Kokeshi | Abr | R$ 2.172.291,90 | R$ 2.294.455,55 | +5,62% | R$ 2.145.126,99 | -1,25% |
| Kokeshi | Mai | R$ 2.172.126,16 | R$ 2.316.329,17 | +6,64% | R$ 2.157.352,12 | -0,68% |
| Lescent | Jan | R$ 52.987,54 | R$ 37.839,73 | -28,59% | R$ 52.740,19 | -0,47% |
| Lescent | Fev | R$ 45.934,03 | R$ 48.209,94 | +4,95% | R$ 45.628,93 | -0,66% |
| Lescent | Mar | R$ 246.055,69 | R$ 260.702,30 | +5,95% | R$ 242.492,03 | -1,45% |
| Lescent | Abr | R$ 268.809,10 | R$ 283.203,92 | +5,36% | R$ 267.911,30 | -0,33% |
| Lescent | Mai | R$ 241.156,87 | R$ 253.922,49 | +5,29% | R$ 239.457,18 | -0,70% |
| Rituária | Jan | R$ 96.894,43 | R$ 91.675,16 | -5,39% | R$ 96.101,51 | -0,82% |
| Rituária | Fev | R$ 84.404,32 | R$ 87.035,08 | +3,12% | R$ 76.710,24 | -9,12% |
| Rituária | Mar | R$ 88.771,58 | R$ 92.380,22 | +4,07% | R$ 81.013,63 | -8,74% |
| Rituária | Abr | R$ 149.559,16 | R$ 154.687,63 | +3,43% | R$ 145.850,02 | -2,48% |
| Rituária | Mai | R$ 231.313,50 | R$ 239.773,40 | +3,66% | R$ 232.172,65 | +0,37% |

Leitura:

- `sub_total` aproxima 23 de 25 células para um erro de até 3,5%; as exceções são Rituária/fev e Rituária/mar, ainda 8,7%–9,1% abaixo.
- A regularidade do excesso de aproximadamente 4%–7% da Torre é compatível com frete e ajustes sendo tratados como faturamento.
- Lescent/jan prova que há também problema de snapshot/Gold: o `sub_total` atual quase reproduz o XLSX, mas a Gold usada pela Torre está 28,6% abaixo.

### 3.3 Reembolsos/devoluções no TikTok

Não foi possível provar que o XLSX desconta reembolsos pedido a pedido usando as tabelas disponíveis:

- o `raw.tiktok_shop_orders` atual só apresenta `COMPLETED`, `CANCELLED`, `DELIVERED` e `IN_TRANSIT` no período;
- o log de status não contém transições explícitas para `REFUNDED` ou `RETURNED`;
- `gold.tiktok_brand_daily.refunded`, `.returned` e `.canceled` estão zerados em todas as 25 células analisadas;
- `raw.tiktok_payments_by_order` só apresentou `transaction_type = 'ORDER'` na inspeção de tipos.

Logo, o Data Mart **não possui hoje uma trilha confiável e utilizada pela Gold para descontar devoluções/reembolsos do GMV TikTok**. Isso é um risco adicional, mas não é necessário para explicar o padrão principal: a troca para `sub_total` já remove a maior parte do desvio.

### 3.4 Problema específico da Gold TikTok

`gold.tiktok_brand_daily` é tabela física, não view; sua transformação não está versionada neste repositório. Além disso:

- Neon reproduz a Gold, logo a sincronização não cria o desvio;
- o `gmv` da Gold não coincide com a soma atual nem de `raw.total_amount` nem de `raw.sub_total`;
- `gold.tiktok_orders_daily.net_revenue` coincide com a soma de `raw.total_amount` em vários recortes antigos, mas também diverge nos recortes recentes;
- os campos de problemas estão zerados.

Conclusão: é necessário reconstruir/explicitar a Gold a partir do grão pedido para obter reprodutibilidade. Apenas trocar a coluna apresentada pelo frontend não resolveria todos os meses.

## 4. Mercado Livre

### 4.1 Regra atual comprovada

`gold.ml_gestao_diaria` é uma view. Sua regra comercial é essencialmente:

```sql
SUM(total_amount) FILTER (WHERE status = 'paid') AS gmv
```

Agrupa pela data de criação atual do pedido. Não há join com pagamentos, claims, devoluções ou reembolsos na formação do GMV.

### 4.2 Comparação mês a mês

| Marca | Mês | XLSX | Torre | Diferença | Diagnóstico predominante |
|---|---:|---:|---:|---:|---|
| Barbours | Jan | R$ 880.599,00 | R$ 869.898,28 | -1,22% | sem lacuna estrutural; status/snapshot/conceito |
| Barbours | Fev | R$ 1.191.752,27 | R$ 1.180.177,28 | -0,97% | sem lacuna estrutural; status/snapshot/conceito |
| Barbours | Mar | R$ 2.193.217,84 | R$ 1.855.698,93 | -15,39% | carga parcial relevante |
| Barbours | Abr | R$ 2.157.550,98 | R$ 1.958.196,41 | -9,24% | carga parcial relevante |
| Barbours | Mai | R$ 2.621.563,85 | R$ 2.575.365,28 | -1,76% | cobertura próxima; status/snapshot |
| Kokeshi | Jan | R$ 250.269,21 | R$ 246.794,95 | -1,39% | sem lacuna estrutural; status/snapshot |
| Kokeshi | Fev | R$ 347.994,60 | R$ 349.587,99 | +0,46% | sem lacuna estrutural; status/snapshot |
| Kokeshi | Mar | R$ 453.249,92 | R$ 22.479,87 | -95,04% | carga quase ausente |
| Kokeshi | Abr | R$ 557.474,28 | R$ 166.125,65 | -70,20% | carga fortemente parcial |
| Kokeshi | Mai | R$ 694.595,80 | R$ 789.234,38 | +13,62% | não é reembolso; provável origem/conceito/snapshot |
| Lescent | Jan | R$ 369.711,18 | R$ 370.084,91 | +0,10% | reconciliado materialmente |
| Lescent | Fev | R$ 248.246,40 | R$ 249.812,65 | +0,63% | reconciliado materialmente |
| Lescent | Mar | R$ 463.429,79 | R$ 207.078,42 | -55,32% | carga fortemente parcial |
| Lescent | Abr | R$ 506.449,76 | R$ 100.412,51 | -80,17% | carga quase ausente |
| Lescent | Mai | R$ 552.966,13 | R$ 552.078,14 | -0,16% | reconciliado materialmente |
| Rituária | Jan | R$ 1.161.029,68 | R$ 1.086.580,75 | -6,41% | origem/conceito ou fotografia temporal |
| Rituária | Fev | R$ 1.261.832,83 | R$ 1.276.923,10 | +1,20% | status/snapshot/conceito |
| Rituária | Mar | R$ 1.930.169,31 | R$ 1.913.711,54 | -0,85% | status/snapshot/conceito |
| Rituária | Abr | R$ 1.248.840,28 | R$ 1.250.410,63 | +0,13% | reconciliado materialmente |
| Rituária | Mai | R$ 1.217.681,96 | R$ 1.232.271,86 | +1,20% | status/snapshot/conceito |

Ápice foi excluída do Mercado Livre: o bloco de origem da aba repete dados de Shopee e o resumo do XLSX não possui realizado confiável para ML.

### 4.3 Cargas parciais comprovadas

Não foram encontrados pedidos duplicados no grão `(brand, order_id)`, a conta/seller por marca é estável e existem registros em todos os dias dos meses. O problema é quantidade de pedidos historicamente extraída:

| Marca/mês | Pedidos no `raw` | Qtde. Vendas no XLSX | Cobertura aproximada |
|---|---:|---:|---:|
| Kokeshi/mar | 443 | 8.416 | 5% |
| Kokeshi/abr | 2.380 | 8.134 | 29% |
| Lescent/mar | 4.111 | 8.835 | 47% |
| Lescent/abr | 1.781 | 8.072 | 22% |
| Barbours/mar | 21.594 | 24.565 | 88% |
| Barbours/abr | 21.367 | 23.014 | 93% |

`Qtde Vendas` do XLSX não é necessariamente número de pedidos — em alguns casos se comporta como unidades —, mas a queda abrupta e localizada no `raw` confirma a extração parcial independentemente dessa ressalva.

A causa técnica mais provável é backfill/paginação incompleta ou janela incremental que não recuperou todo o histórico. Não há evidência de que join, duplicidade ou frontend tenham causado essas perdas.

### 4.4 A hipótese de reembolsos no GMV

A hipótese de que a Torre esteja **incluindo** reembolsos no GMV do Mercado Livre foi rejeitada pelos dados atuais.

O campo `raw.ml_order_payments.transaction_amount_refunded` existe e possui valores. De janeiro a maio foram observados, entre outros:

- pagamentos `refunded / bpp_refunded`: R$ 637.321,26 reembolsados;
- `refunded / bpp_covered`: R$ 52.942,04;
- `refunded / by_admin`: R$ 33.155,68;
- reembolsos parciais também aparecem em pagamentos ainda `approved`.

Ao ligar pagamentos e pedidos:

- quase todos os pedidos com reembolso total estão atualmente `cancelled`;
- os poucos reembolsos parciais estão atualmente `partially_refunded`;
- nenhum desses dois status passa no filtro `status = 'paid'` da Gold.

Exemplos do impacto de subtrair reembolsos outra vez:

| Marca/mês | Gold − XLSX antes | Reembolsos do mês | Efeito de nova subtração |
|---|---:|---:|---|
| Barbours/jan | -R$ 10.700,72 | R$ 65.524,99 | afastaria ainda mais |
| Barbours/mar | -R$ 337.518,91 | R$ 147.543,23 | agravaria carga parcial |
| Lescent/mai | -R$ 887,99 | R$ 14.053,44 | transformaria quase empate em erro relevante |
| Rituária/fev | +R$ 15.090,27 | R$ 33.437,00 | inverteria o sinal e ainda não reconciliaria |
| Kokeshi/mai | +R$ 94.590,08 | R$ 13.856,41 | reduziria só parte pequena do desvio |

Assim, **não se deve simplesmente calcular `GMV atual - transaction_amount_refunded`**. Isso geraria dupla exclusão para os pedidos cujo status já mudou.

### 4.5 Por que ainda há diferença nos meses sem grande lacuna

As causas mais prováveis, em ordem de evidência, são:

1. **Estado atual versus fotografia do fechamento.** A tabela `raw.ml_orders` mantém o status mais recente. Um pedido que estava pago no fechamento e depois foi reembolsado deixa de compor retroativamente o mês na Torre. O XLSX é uma fotografia manual/importada do momento do fechamento.
2. **Métricas independentes no XLSX.** As células são caches de `IMPORTRANGE` vindos de planilhas externas de cada marca. `Total Faturado`, `Qtde Vendas` e `Ticket Médio` são importados de células separadas e nem sempre fecham aritmeticamente entre si. Portanto, não se pode inferir a regra do faturamento apenas dividindo uma métrica pela outra.
3. **Conceito/origem diferente em outliers.** Kokeshi/mai tem cobertura de pedidos normal, mas a Gold fica 13,6% acima. Os reembolsos do mês explicam apenas R$ 13,9 mil dos R$ 94,6 mil e já estão excluídos por status. É necessário rastrear a célula original `📌 Meta X Realizado!G32` da planilha fonte da Kokeshi e compará-la pedido a pedido com a conta/seller do Data Mart.
4. **Competência e timezone.** O Data Mart usa `date_created::date`; o relatório manual pode usar data de pagamento, aprovação ou fechamento e outra conversão de timezone. Isso explica diferenças pequenas e troca de pedidos nas bordas do mês, mas não explica Kokeshi/mai nem as cargas parciais.
5. **Snapshot de sincronização.** Há diferenças pequenas entre a Gold atual e o Neon em algumas células de maio, compatíveis com horários distintos de atualização; elas não explicam os grandes desvios.

## 5. Shopee

### 5.1 Regra atual e erro comprovado

No parser `pipelines/connectors/shopee/_parser.py`, a população ativa é definida como status diferente de `Cancelado`, e o GMV é a soma de `subtotal` dessa população. Pedidos com status devolvido/reembolsado permanecem ativos nessa regra.

O XLSX, por sua vez, fica muito próximo de:

```text
Vendas (BRL) - Vendas Canceladas - Vendas Devolvidas/Reembolsadas
```

Exemplo forte: Kokeshi/abr.

- XLSX: R$ 2.104.218,36;
- líquido calculado no relatório Shopee: R$ 2.102.888,90, diferença de apenas -R$ 1.329,46;
- Torre: R$ 2.307.942,92, excesso de R$ 203.724,56.

### 5.2 Comparação mês a mês

| Marca | Jan | Fev | Mar | Abr | Mai |
|---|---:|---:|---:|---:|---:|
| Ápice | +4,87% | +4,32% | +4,86% | +13,39% | +9,77% |
| Barbours | +3,35% | +3,89% | +5,73% | +13,46% | +10,81% |
| Kokeshi | +3,12% | +3,26% | +2,42% | +9,68% | +0,67% |
| Lescent | +4,10% | +4,33% | +6,84% | +5,49% | +3,02% |
| Rituária | +3,92% | +2,85% | +3,74% | +12,51% | +4,16% |

Todos os 25 valores da Torre estão acima do XLSX, padrão coerente com não descontar devoluções/reembolsos. Abril concentra os maiores excessos para quatro marcas.

## 6. Fatos, inferências e pontos ainda abertos

### Fatos comprovados

- O XLSX é a fonte de verdade por decisão de negócio.
- O site consome Neon; TikTok no Neon coincide com a Gold nas 25 células analisadas.
- `sub_total` TikTok aproxima muito mais o XLSX que o GMV da Torre.
- No TikTok, a diferença `total_amount - sub_total` é predominantemente frete.
- A Gold TikTok não registra cancelados, reembolsados ou devolvidos no período: os três indicadores estão zerados.
- A Gold ML soma `total_amount` somente de pedidos cujo status atual é `paid`.
- Há cargas históricas parciais de ML, sobretudo Kokeshi e Lescent em março/abril.
- Reembolsos ML não estão sendo somados ao GMV atual; descontá-los novamente é incorreto.
- O parser Shopee mantém devolvidos/reembolsados no GMV quando o status não é literalmente `Cancelado`.

### Inferências fortes

- O XLSX TikTok representa venda de produto sem frete do comprador.
- Pequenas diferenças de ML em meses completos vêm principalmente de fotografia temporal e competência.
- Kokeshi/mai no ML tem uma divergência adicional de origem/conceito e deve ser investigada na planilha-fonte.

### Ainda não comprovado

- A fórmula exata usada para construir `gold.tiktok_brand_daily.gmv`.
- O tratamento exato de reembolsos/devoluções no relatório manual TikTok.
- A data de competência exata usada em cada planilha externa importada pelo XLSX.
- A composição pedido a pedido do valor manual de Kokeshi/mai e Rituária/jan no Mercado Livre.

## 7. Recomendações registradas para a fase de plano de ação

Estas recomendações ficam registradas, mas não foram implementadas.

### TikTok

1. Definir formalmente o KPI-alvo como `subtotal de produtos elegíveis`, sem frete do comprador.
2. Construir uma view auditável no grão `(brand, order_id)` e depois agregar por mês.
3. Usar `sub_total` como base candidata e definir explicitamente quais status entram.
4. Obter uma fonte de refunds/returns; não confiar nos campos zerados da Gold atual.
5. Reconciliar os 25 totais contra o XLSX antes de trocar a fonte da Torre.

### Mercado Livre

1. Fazer backfill completo e paginado de Kokeshi/Lescent/Barbours para março e abril.
2. Manter duas datas/status: fotografia do fechamento e estado atual, evitando reescrever silenciosamente o histórico comercial.
3. Não subtrair `transaction_amount_refunded` de uma população que já exclui `cancelled`/`partially_refunded`.
4. Definir com o negócio se o realizado mensal é “pago no fechamento”, “pago hoje”, “aprovado”, ou “líquido de reembolsos”.
5. Auditar pedido a pedido Kokeshi/mai, Rituária/jan e os meses residuais, usando a planilha externa original e não apenas o cache do XLSX.

### Shopee

1. Trocar a regra genérica `status != Cancelado` por uma classificação explícita de status.
2. Calcular a métrica-alvo como vendas menos canceladas menos devolvidas/reembolsadas, conforme o relatório-fonte.
3. Versionar arquivo, data de extração e competência para evitar diferença de snapshot.
4. Reprocessar janeiro–maio e exigir reconciliação por marca/mês antes da publicação.

### Controles comuns

1. Criar uma tabela de reconciliação mensal com `valor_xlsx`, `valor_calculado`, diferença absoluta, diferença percentual, quantidade de registros e timestamp das duas fontes.
2. Bloquear publicação quando a diferença ultrapassar tolerância acordada; para a meta final “na vírgula”, usar tolerância de R$ 0,01 após estabilizar as fontes.
3. Versionar o contrato de cada KPI: valor base, descontos, frete, cancelamentos, devoluções, status elegíveis, data de competência e timezone.
4. Exibir na Torre a data/hora da última atualização e um aviso de qualidade quando o mês estiver parcial.

## 8. Evidências técnicas consultadas

- Workbook local e fórmulas/caches `IMPORTRANGE`.
- Site oficial e API pública.
- Neon: `marts.fact_marketplace_daily_performance`.
- Data Mart: `raw.tiktok_shop_orders`, `raw.tiktok_shop_order_status_log`, `raw.tiktok_payments_by_order`, `gold.tiktok_brand_daily`, `gold.tiktok_orders_daily`, `raw.ml_orders`, `raw.ml_order_payments`, `raw.ml_order_line_items` e `gold.ml_gestao_diaria`.
- Parser Shopee: `pipelines/connectors/shopee/_parser.py`.

Todas as consultas aos bancos foram somente leitura e retornaram agregados, sem exposição de dados pessoais.

## 9. Impacto quantitativo esperado das correções

Estimativa adicionada em 21/07/2026. A métrica principal é:

```text
erro absoluto acumulado = SUM(ABS(valor_torre - valor_xlsx))
```

O cálculo é feito nas células marca × mês. Essa métrica não permite que um excesso em uma marca esconda uma falta em outra. Como referência secundária, também foi calculada a diferença entre os totais consolidados.

### 9.1 Situação atual e primeira camada corrigida

| Canal | Faturamento XLSX analisado | Erro absoluto atual | Erro atual / XLSX | Erro estimado após correção comprovada | Erro projetado / XLSX | Redução estimada |
|---|---:|---:|---:|---:|---:|---:|
| TikTok | R$ 57.483.610,26 | R$ 3.313.285,94 | 5,76% | R$ 647.202,95 | 1,13% | 80,5% |
| Shopee | R$ 19.920.944,56 | R$ 1.260.905,49 | 6,33% | R$ 184.711,47 | 0,93% | 85,4% |
| Mercado Livre | R$ 20.308.625,07 | R$ 2.314.546,48 | 11,40% | R$ 293.165,70 a R$ 361.623,13 | 1,44% a 1,78% | 84,4% a 87,3% |
| **Total** | **R$ 97.713.179,89** | **R$ 6.888.737,91** | **7,05%** | **R$ 1.125.080,12 a R$ 1.193.537,55** | **1,15% a 1,22%** | **82,7% a 83,7%** |

### 9.2 Como foi feita a projeção

#### TikTok

Substituição simulada do valor atual da Torre pela soma de `raw.sub_total` dos pedidos atualmente não cancelados. É uma simulação direta, não uma extrapolação:

- erro atual: R$ 3.313.285,94;
- erro com `sub_total`: R$ 647.202,95;
- redução: R$ 2.666.082,99, ou 80,5%.

A diferença entre os totais consolidados cairia de R$ 3.272.551,78 acima do XLSX para R$ 645.484,65 abaixo. Isso confirma que `sub_total` corrige o conceito principal, mas ainda exige tratamento de snapshot, status e Rituária/fev-mar.

#### Shopee

Substituição simulada pela métrica dos 25 relatórios mensais `shop-stats`:

```text
sales_brl - cancelled_sales - refunded_sales
```

Resultado:

- erro atual: R$ 1.260.905,49;
- erro com a regra líquida: R$ 184.711,47;
- redução: R$ 1.076.194,02, ou 85,4%.

A diferença entre os totais consolidados cairia de R$ 1.260.905,49 acima do XLSX para R$ 151.386,91 acima. O residual de R$ 184,7 mil por célula indica diferenças de snapshot/arquivo e não invalida a correção semântica.

#### Mercado Livre

Não há como calcular exatamente o resultado pós-backfill sem executar o backfill. Foram usados dois limites:

1. **Cenário otimista:** as seis células com carga parcial — Barbours, Kokeshi e Lescent em março/abril — passam a reproduzir o XLSX; os demais meses ficam como estão. O erro cai para R$ 293.165,70.
2. **Cenário conservador:** as células recuperadas continuam com o erro percentual mediano observado nos meses completos da própria marca. O erro fica em aproximadamente R$ 361.623,13.

O backfill resolveria entre R$ 1,95 milhão e R$ 2,02 milhões do erro atual. O residual permanece concentrado em Kokeshi/mai, Rituária/jan e pequenas diferenças de fotografia/status nos demais meses. Nenhuma redução foi atribuída à subtração de reembolsos, pois essa alteração seria tecnicamente incorreta na população atual.

### 9.3 Interpretação

- A primeira camada de correções resolve **aproximadamente cinco sextos do problema**.
- TikTok e Shopee devem ficar perto de 1% de erro absoluto sobre o faturamento do XLSX, mesmo antes da reconciliação pedido a pedido.
- Mercado Livre deve sair de 11,4% para algo entre 1,4% e 1,8%, desde que o backfill recupere integralmente março e abril.
- Chegar “na vírgula” exige uma segunda camada: congelamento/fotografia mensal, mesma data de competência, mesma classificação de status e exceções reconciliadas pedido a pedido.
- O alvo teórico após essa segunda camada é no máximo R$ 0,01 por célula; ele não foi usado como previsão, pois ainda depende de decisões e fontes não comprovadas.

## 10. Gate R1 — Baseline executável de GMV/Faturamento

Data: 21/07/2026 (R1), corrigido em 21/07/2026 (R1.1 — segundo e último ciclo
deste gate, stop-loss aplicado a partir daqui). Escopo fechado: transformar
esta análise documental em uma baseline versionada e reproduzível (referência
XLSX + snapshot Torre + comparador executável), sem alterar regra de
produção, parser, Gold, Neon, scheduler ou frontend.

### 10.1 Arquivos criados

- `docs/reconciliation/xlsx_gmv_reference_jan_maio_2026.csv` — 70 células de
  referência (`marketplace,brand,month,gmv_reference`).
- `docs/reconciliation/torre_gmv_baseline_20260721.csv` — 70 células da
  fotografia Torre usada nesta análise (`marketplace,brand,month,gmv_actual`).
- `pipelines/reconciliation/reconcile_xlsx_torre_gmv.py` — comparador
  executável (sem conexão a banco).
- `pipelines/tests/test_reconcile_xlsx_torre_gmv.py` — testes focais.

### 10.2 Fonte e hash do snapshot

- Workbook: `[GoBeaute Marketplaces] Análise de métricas e resultados.xlsx`
  (mantido fora do controle de versão, acessado somente leitura).
- SHA-256: `7d594f7374959d3fa18f589f17e929c423ec18726e890bb4b7b7865e1e1cb90`
- Os 70 valores de `gmv_reference` foram lidos diretamente das abas por marca
  (`ÁPICE`, `BARBOURS`, `KOKESHI`, `LESCENT`, `RITUÁRIA`), linha "Total
  Faturado" de cada bloco `Tik Tok` / `Shopee` / `Mercado Livre`, colunas
  jan–mai/2026. Não houve necessidade de fallback por fórmula
  externa/IMPORTRANGE quebrada: os valores em cache (`data_only=True`) leram
  corretamente e reproduzem, célula a célula, as tabelas 3.2, 4.2 e os totais
  da seção 9.1 deste documento.
- Snapshot Torre (`gmv_actual`), fonte única: Neon,
  `marts.fact_marketplace_daily_performance` (join com `marts.dim_marketplace`
  e `marts.dim_loja`), consulta somente leitura, agregada por
  `(brand, mês)`, sem linhas individuais nem PII:
  - TikTok (25 células, `marketplace_id = 1`) e Mercado Livre (20 células,
    `marketplace_id = 2`, Ápice excluída): valores absolutos já publicados nas
    tabelas 3.2 e 4.2 deste documento — mesma fotografia usada na análise.
  - Shopee (25 células, `marketplace_id = 3`): capturado nesta correção
    (R1.1) por consulta read-only direta ao Neon, opção A do plano de
    correção (`DATABASE_URL` disponível pelo mecanismo padrão do projeto —
    `.env`). Consulta executada em 21/07/2026, agregando
    `SUM(gmv)` por `(dim_loja.brand_key, DATE_TRUNC('month', date))` com
    `marketplace_id = 3` e `date` em jan–mai/2026, para as cinco marcas em
    escopo. Todas as 25 células retornaram cobertura completa do mês (28 a 31
    dias, `min(date)`/`max(date)` cobrindo o mês inteiro). Não foi usada
    `gold.marketplace_region_daily` — essa tabela é a Gold **regional** (UF),
    não a fonte da baseline diária da Torre consumida pelo site.
  - A opção B (API pública `/api/v1/performance/brands`) não foi necessária,
    pois a opção A (Neon direto) já entregou os 25 valores exatos.

### 10.3 Comando de execução

```bash
python -m pipelines.reconciliation.reconcile_xlsx_torre_gmv \
    --reference docs/reconciliation/xlsx_gmv_reference_jan_maio_2026.csv \
    --candidate docs/reconciliation/torre_gmv_baseline_20260721.csv

python -m pipelines.reconciliation.reconcile_xlsx_torre_gmv \
    --reference docs/reconciliation/xlsx_gmv_reference_jan_maio_2026.csv \
    --candidate docs/reconciliation/torre_gmv_baseline_20260721.csv --json
```

Testes:

```bash
python -m pytest pipelines/tests/test_reconcile_xlsx_torre_gmv.py
```

### 10.4 Números reproduzidos

| Canal | Ref. XLSX | Erro absoluto (comparador) | Erro % | Erro absoluto (número de controle) | Erro % (controle) |
|---|---:|---:|---:|---:|---:|
| TikTok | R$ 57.483.610,26 | R$ 3.313.285,94 | 5,76% | R$ 3.313.285,94 | 5,76% |
| Mercado Livre | R$ 20.308.625,07 | R$ 2.314.546,48 | 11,40% | R$ 2.314.546,48 | 11,40% |
| Shopee | R$ 19.920.944,56 | R$ 1.260.905,49 | 6,33% | R$ 1.260.905,49 | 6,33% |
| **Total** | **R$ 97.713.179,89** | **R$ 6.888.737,91** | **7,05%** | **R$ 6.888.737,91** | **7,05%** |

Todos os canais e o total batem exatamente (diferença R$ 0,00, dentro da
tolerância de R$ 0,01) com os números de controle, incluindo Shopee após a
correção. Nenhum valor foi ajustado manualmente para forçar o fechamento — os
25 valores de Shopee vieram diretamente da consulta ao Neon e reproduziram o
número de controle sem qualquer intervenção manual.

### 10.5 Limitações

1. O comparador não versiona nem interpreta o próprio XLSX (fica fora do
   controle de versão); qualquer nova leitura do workbook deve reconferir o
   SHA-256 acima antes de reutilizar os números.
2. O snapshot Torre é uma fotografia pontual (consulta executada em
   21/07/2026); se o Neon for atualizado depois dessa data (nova carga,
   correção, sync), os valores de `gmv_actual` deixam de refletir o estado
   atual do banco — não há job recorrente associado a este baseline.
3. Nenhuma correção de regra (TikTok `sub_total`, Shopee líquido, backfill ML)
   foi aplicada — este gate só mede o estado atual.
4. A precisão teórica de R$ 0,01 por célula (fotografia/competência/status
   únicos) não faz parte desta primeira camada; permanece como evolução
   posterior condicionada a decisões de negócio ainda não tomadas (ver 10.7).

### 10.6 Achados classificados

| Achado | Classificação |
|---|---|
| Baseline de 70 células (referência + Torre) reproduz os totais por canal e o total geral do documento com tolerância R$ 0,01, nos três canais | necessário — entregue |
| Shopee `gmv_actual` reconstruído por percentual arredondado (achado do ciclo R1) | necessário — **corrigido nesta revisão** (R1.1), substituído por leitura read-only direta do Neon; sem resíduo remanescente |
| Corrigir a regra de GMV do TikTok/Shopee/ML | fora do escopo deste gate |
| Criar tabela de reconciliação em banco | fora do escopo deste gate |

### 10.7 Plano resumido dos próximos gates

Roadmap aprovado, sem novos gates além dos já previstos:

- **Gate R2**: correções semânticas comprovadas, como duas tasks
  independentes dentro do mesmo gate:
  - TikTok: adotar `sub_total` (sem frete do comprador) como base do GMV;
  - Shopee: vendas − canceladas − devolvidas/reembolsadas, substituindo a
    regra `status != Cancelado`.
- **Gate R3**: análise semântica do Mercado Livre, restrita aos meses com
  cobertura adequada, e validação do backfill entregue externamente:
  - meses a usar: Barbours, Kokeshi e Lescent em janeiro, fevereiro e maio;
    Rituária em janeiro a maio. Março/abril de Barbours, Kokeshi e Lescent
    não devem ser usados para concluir se a fórmula de GMV está correta
    enquanto o backfill externo não estiver concluído (carga parcial
    comprovada na seção 4.3);
  - regra atual `status = paid`, estado atual do pedido versus fotografia
    mensal de fechamento, data de criação versus competência comercial,
    tratamento de cancelamentos/devoluções/reembolsos, e confirmação de que
    reembolsos não devem ser descontados novamente da população atual
    (seção 4.4);
  - investigação dos resíduos nominados: Kokeshi/mai (+13,62%) e
    Rituária/jan (-6,41%), e os pequenos resíduos remanescentes nos demais
    meses completos;
  - definição do contrato esperado da métrica de GMV do ML;
  - validação, com o comparador do Gate R1
    (`pipelines/reconciliation/reconcile_xlsx_torre_gmv.py`), do resultado
    entregue pelo engenheiro de dados responsável pelo backfill de
    março/abril.
- **Gate R4**: publicação controlada, sync Neon, reconciliação final e QA da
  Torre.

**Responsabilidade do backfill do Mercado Livre.** A correção das cargas
parciais e o backfill histórico de março/abril (Kokeshi/Lescent/Barbours)
foram repassados a um engenheiro de dados do time e **não são entrega deste
projeto**. Este projeto não implementa nem executa o backfill, não corrige a
paginação da extração e não altera a ingestão histórica do ML — apenas
analisa a semântica do GMV nos meses com cobertura adequada, define o
contrato da métrica e valida o resultado entregue com o comparador do Gate
R1. O backfill do ML é uma **dependência externa** para o fechamento do Gate
R4, não uma entrega deste projeto.

A precisão de R$ 0,01 por célula não é o Gate R4 desta primeira camada; é
evolução posterior, condicionada a snapshot, competência e fontes originais
ainda não decididas com o negócio.
