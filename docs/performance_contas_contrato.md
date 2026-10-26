# Contrato: Performance das contas (Shopee, Mercado Livre, TikTok Shop BR)

> T-0012 (orq-5), 26/10/2026. Este contrato consolida três relatórios de capacidade já revisados (Shopee, Mercado
> Livre e TikTok, de 26/10/2026). Esses relatórios são a fonte da verdade deste documento. A evidência é **interna e
> está disponível sob pedido**. Por isso as linhas citam "evidência interna (sob pedido)" ou, quando existe, a
> referência pública: URL ou identificador da doc oficial, ou `arquivo:linha` deste repositório.
>
> Nenhuma API de marketplace foi chamada para escrever este contrato.
>
> Vocabulário alinhado com `docs/kpi_dictionary.md` e `docs/expedicao_contrato.md`.

## 1. Objetivo e escopo

**Objetivo.** Definir o que a Torre pode e o que não pode mostrar sobre a **performance das contas** nos três
marketplaces. Isso inclui a reputação e a saúde da conta pelo critério de cada plataforma, os indicadores
operacionais que a Torre calcula e a forma de separar uns dos outros. Este contrato é a base da tela T-0014.

**Quem usa.**
- Engenheiros e analistas que vão implementar a T-0014 e as ingestões propostas na §7.
- O supervisor, que revisa o contrato.
- O usuário final da tela: o gestor ou operador de marketplace (ver decisões abaixo).

**Decisão que a tela apoia:** *qual loja está perto de ser penalizada e onde agir*.

**Dentro do escopo:**
- métricas oficiais de reputação, score, penalidade e saúde de conta das três plataformas;
- KPIs operacionais da Torre que tratam de cancelamento, atraso de despacho/coleta e fila;
- estados de ausência.

**Fora do escopo:**
- GMV e KPIs comerciais (`docs/kpi_dictionary.md`);
- estoque;
- métricas por anúncio e por reclamação como KPI de tela (ML O9–O12; ver §7);
- alertas no Resumo Executivo.

### 1.1 Decisões assentadas

Decisões do supervisor, já tomadas, que valem para a T-0014:

1. **Persona:** gestor/operador de marketplace.
2. **Prefixo da rota:** `/api/v1/performance-contas`.
3. **`no_series_published` = `sem_fonte`** ("não publicada"). Nunca é `vazio_legitimo`: a série não existe, e isso
   não é o mesmo que uma resposta de sucesso sem registros.
4. **LDR Shopee:** entra como **KPI operacional Torre**. Aparece como indisponível até a fonte/flag estar ativa e
   **jamais** é apresentada como métrica oficial de Health da Shopee.

## 2. Princípios

1. **Oficial e Torre são coisas distintas.**
   - Uma métrica **oficial** é a que a plataforma usa para reputação, penalidade ou score.
   - Um cálculo **Torre** é feito sobre os nossos dados.
   - Um limiar oficial **nunca** aparece ao lado de um número Torre de outra definição, e um número Torre nunca
     recebe a cor de uma faixa oficial.

   Exemplos:
   - **Cancelamento no ML.** O cancelamento Torre (T1) conta todos os pedidos `cancelled`, de qualquer autor. O
     cancelamento de reputação (O6) conta **só os iniciados pelo vendedor**. Os dois divergem de forma relevante
     (medido na descoberta; evidência interna, sob pedido). *Exemplo ilustrativo, números fictícios:* uma marca com
     400 pedidos em 60 dias, 18 cancelados por qualquer autor e 1 pelo vendedor tem T1 = 4,5% e O6 = 0,25%. Pintado
     com os limites MLB de O6 (de Líder ≤0,5% a Vermelho >4%), o T1 deixaria a marca vermelha, enquanto a reputação
     estaria na faixa Líder.
   - **Reclamações no ML.** A proxy por mediação (P2) mistura tipos de claim e não aplica `avoid_reputation`, por
     isso diverge do `claims.rate` oficial (O4) (medido na descoberta; evidência interna, sob pedido). *Exemplo
     ilustrativo, números fictícios:* P2 = 3% contra O4 = 0,7% na mesma conta e janela.
   - **Handling no ML.** O handling médio Torre (T3) mede da criação do pedido até o envio. Não é o atraso de
     despacho oficial (O5).
   - **LDR na Shopee.** A LDR operacional da Shopee é KPI Torre e **não é** a taxa de envio atrasado que a Shopee
     usa para penalizar (`docs/expedicao_contrato.md:1217-1222`). Também não é métrica de Health (§1.1).
   - **Cancelamento no TikTok.** O cancelamento Torre não é o SFCR, e a nota média por produto não é o NRR.

2. **Indisponível, zero e vazio são estados diferentes.** Cada indicador carrega um dos estados da §4. `NULL` e
   ausência nunca viram 0 (`docs/kpi_dictionary.md:191`). Uma lista vazia só é "vazio legítimo" quando a fonte
   respondeu com sucesso.

3. **Frescor e cobertura vêm por indicador, não por tela.** Cada número mostra:
   - o instante da captura ou da publicação;
   - as contas que cobre.

   Uma conta sem fonte aparece como fora da cobertura. Nunca some do total sem aviso.

4. **Nenhum dado demonstrativo em produção.** Valor de exemplo, mock ou preenchimento visual não entra na tela
   publicada. Sem dado, a tela mostra o estado da §4.

## 3. Status por canal

| Canal | Status em uma frase |
|---|---|
| Shopee | **Oficial documentado; autorização NÃO comprovada.** O módulo `AccountHealth` tem 6 endpoints, e o campo `api_permission` inclui Seller In House System, o tipo do nosso app. O único run do probe (21/10) fez **0 chamadas** (`token_do_cofre_sem_folga`), então o resultado é `AH_INCONCLUSIVE`. Não houve negativa e **não é bloqueio tipo AMS**. Nada é ingerido (evidência interna, sob pedido). |
| Mercado Livre | **Oficial via `GET /users/{id}` → `seller_reputation`.** Está documentado e usa a permissão funcional "Usuários (default)". Houve uma leitura manual pontual de uma conta, fora de pipeline; nenhuma ingestão contínua. **NÃO é ingerido**: nenhum DAG, tabela ou migration trata de reputação (evidência interna, sob pedido). |
| TikTok Shop BR | **SPS e Account Health não têm API para lojas BR.** A doc diz "Available only for US-region shops", e AHR e violações existem só na UI. O bloqueio é **documental, não medido**. A LDR oficial agregada não sai por API (só o prazo oficial por pedido). Get Customer Service Performance é **candidato não testado** (evidência interna, sob pedido). |

**Conclusão:** hoje **nenhuma métrica oficial de performance de conta está ingerida em nenhum dos três canais**.
Tudo o que a Torre publica nesse tema é cálculo Torre.

## 4. Estados de exibição

Os nomes são propostos para o contrato da T-0014, servido sob o prefixo `/api/v1/performance-contas` (§1.1). Os
nomes entre parênteses já existem nas APIs atuais e devem ser mapeados para estes estados.

| Estado | Quando | Exemplo neste contrato | Texto mínimo na tela |
|---|---|---|---|
| `sem_fonte` | A Torre não tem fonte. Ou a plataforma não expõe o dado por API para a conta, ou a fonte existe e não é ingerida/publicada | SPS/AHR TikTok BR (`not_available`); reputação ML sem snapshot; cancelamento TikTok em `/quality` (`null`); LDR operacional Shopee enquanto a fonte/flag não está ativa (`disabled`, `table_not_migrated`, `no_series_published`) | dizer qual dos dois casos é e citar a fonte e a data da verificação; para série não publicada, "não publicada" |
| `nao_comprovado` | Existe endpoint oficial, mas a autorização do nosso app não foi provada | Shopee AccountHealth (`AH_INCONCLUSIVE`) | "autorização não comprovada", com a data do último probe |
| `erro` | A fonte respondeu com falha (401, 403 ou outra) ou a publicação falhou | futuro snapshot ML com 401/403 | o desfecho gravado, nunca um valor |
| `vazio_legitimo` | A chamada teve sucesso e não há registro | Shopee sem punição ativa, depois de autorizada | "nenhum registro em {janela}" |
| `dado` | Há valor | LDR operacional TikTok | o valor, o rótulo oficial/Torre, o frescor e a cobertura |

O frescor é um modificador de `dado`, não um estado à parte. Dado velho continua `dado`, mas com aviso. Exemplos:
`snapshot.stale` da LDR Shopee acima de 6 h (`docs/expedicao_contrato.md:1305-1309`) e a reputação ML acima de 36 h
(proposta).

Estados de serving que já existem (`docs/expedicao_contrato.md:1305-1309`):
- `no_series_published` → **`sem_fonte`**, com o texto "não publicada". **Nunca** `vazio_legitimo` (§1.1).
- `disabled`, `table_not_migrated` e `deadline_unavailable` → `sem_fonte` ou `erro`, conforme o caso.

## 5. Matriz consolidada

Convenções:
- **Melhora:** ↑ maior é melhor; ↓ menor é melhor.
- **Status:** *sustentado hoje* (publicado pela Torre), *documentado não ingerido* ou *indisponível*.
- **Limiar:** *oficial* (com o valor, quando documentado), *interno* ou *nenhum*. Um limiar interno nunca é
  apresentado como meta da plataforma.
- **Evidência:** "interna (sob pedido)" remete aos relatórios de capacidade de 26/10/2026; referências públicas
  (doc oficial, `arquivo:linha` do repo) aparecem quando existem.

### 5.1 Shopee

Notas comuns aos endpoints oficiais (evidência interna, sob pedido):
- formato `GET /api/v2/account_health/<rota>`, de loja, com `partner_id`, `shop_id`, `access_token` e `sign`;
- host BR `openplatform.shopee.com.br`;
- a doc não informa `rate_limit` nem a fórmula das taxas;
- o limiar chega na própria resposta (`target.value` + `comparator`), por loja e por métrica.

| Métrica | Oficial/Torre | Definição | Numerador/base | Grão/janela | Melhora | Limiar | Fonte/endpoint/tabela | Escopo | Frescor | Cobertura | Status | Evidência |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Nota geral (`overall_performance.rating`) | Oficial | 1 Poor, 2 Improvement Needed, 3 Good, 4 Excellent, mais as contagens de métricas fora da meta por grupo (fulfillment, listing, customer service) | n/d (categórica) | loja; período corrente | ↑ | oficial categórico | `get_shop_performance` | Seller In House System (pela doc) | não documentado (medir) | 4 contas com app (apice, barbours, lescent, rituaria); Kokeshi sem app | documentado não ingerido; autorização não comprovada | evidência interna (sob pedido); doc api_id 2150 |
| Late Shipment Rate (All Channels), `metric_id` 1 | Oficial | taxa de envio atrasado | não documentado | loja; `current_period` × `last_period` | ↓ | oficial na resposta (`target` + `comparator`) | `get_shop_performance`; pedidos em `get_metric_source_detail` (`lsr_order_list`) | idem | não documentado | idem | documentado não ingerido; autorização não comprovada | evidência interna (sob pedido); doc api_id 2150/2323 |
| Non-Fulfilment Rate, id 3 | Oficial | não cumprimento; tipos 1 cancelamento pelo sistema, 2 cancelamento pelo vendedor, 3 devolução/reembolso | não documentado | idem | ↓ | oficial na resposta | idem (`nfr_order_list`) | idem | idem | idem | idem | evidência interna (sob pedido) |
| Preparation Time, id 4 | Oficial | tempo de preparação (dias) | não documentado | idem | ↓ | oficial na resposta | idem (`apt_order_list`) | idem | idem | idem | idem | evidência interna (sob pedido) |
| Fast Handover Rate, id 25 (2001 SLS, 2002 FBS, 2003 3PF) | Oficial | entrega rápida à transportadora | não documentado | idem | ↑ | oficial na resposta | idem (`fhr_order_list`) | idem | idem | idem | idem | evidência interna (sob pedido) |
| Cancellation Rate, id 42; Return-refund Rate, id 43 | Oficial | taxas de cancelamento e de devolução/reembolso | não documentado | idem | ↓ | oficial na resposta | idem | idem | idem | idem | idem | evidência interna (sob pedido) |
| Chat Response Rate, id 11; Response Time, id 21; Shop Rating, id 22 | Oficial | atendimento e nota da loja | não documentado | idem | 11 e 22 ↑; 21 ↓ | oficial na resposta | `get_shop_performance` (sem detalhe de fonte) | idem | idem | idem | idem | evidência interna (sob pedido) |
| Severe/Other Listing Violations, ids 52/53; Pre-order %, id 12 | Oficial | violações de anúncio e % de pré-venda | contagem / % | idem | ↓ | oficial na resposta | idem (`violation_listing_list`) | idem | idem | idem | idem | evidência interna (sob pedido) |
| Pontos de penalidade | Oficial | registros do trimestre corrente (`original_point_num`, `latest_point_num` após recurso, `violation_type`) | soma de pontos | registro; trimestre corrente | ↓ | não documentado na API (punições em Tier 1–5) | `get_penalty_point_history` | idem | idem | idem | idem | evidência interna (sob pedido); doc api_id 2324 |
| Punições ativas/encerradas | Oficial | `punishment_type`, `start_time`/`end_time`, `reason` Tier 1–5 | contagem | registro; trimestre corrente | ↓ | n/a | `get_punishment_history` | idem | idem | idem | idem | evidência interna (sob pedido); doc api_id 2325 |
| Anúncios com problema | Oficial | `item_id` + motivo (proibido, falsificado, spam etc.) | contagem | fotografia | ↓ | n/a | `get_listings_with_issues` | idem | idem | idem | idem | evidência interna (sob pedido); doc api_id 2326 |
| Pedidos atrasados | Oficial | `order_sn`, `shipping_deadline`, `late_by_days` | contagem | fotografia | ↓ | n/a | `get_late_orders` | idem | idem | idem | idem | evidência interna (sob pedido); doc api_id 2327 |
| LDR operacional (coleta × prazo Shopee) | Torre (KPI operacional; **jamais** métrica oficial de Health) | entre os pedidos pagos e despachados pelo próprio vendedor, os que não foram coletados até o `ship_by_date`; não é a Late Shipment Rate oficial | (atrasados + pendentes vencidos) / base madura | conta × dia de vencimento | ↓ | nenhum: sem meta e sem cor de severidade (ver §10) | `marts.expedicao_shopee_ldr_daily` → `GET /api/v1/expedicao/shopee/ldr` | n/a | `snapshot.stale` acima de 6 h | 4 contas | indisponível até a fonte/flag estar ativa: exibido como `sem_fonte` ("não publicada"); rota respondia 404 em 26/10; #2118/#2119 mesclados, migrations não aplicadas | evidência interna (sob pedido); `docs/expedicao_contrato.md:1215-1320` |

### 5.2 Mercado Livre

Contas no registry: barbours, kokeshi, lescent e rituaria. A Apice não tem conta ML.

Os limiares MLB foram copiados da doc oficial de 11/08/2025, e cada valor é o teto da faixa.

Todas as linhas O1–O8 vêm de `GET https://api.mercadolibre.com/users/{USER_ID}`, com a permissão "Usuários
(default)" e o token OAuth do vendedor.

Nenhuma conta tem reputação ingerida. Houve uma leitura manual pontual de uma conta, fora de pipeline; nenhuma
ingestão contínua.

| Métrica | Oficial/Torre | Definição | Numerador/base | Grão/janela | Melhora | Limiar | Fonte/endpoint/tabela | Escopo | Frescor | Cobertura | Status | Evidência |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| O1 Termômetro (`level_id`) | Oficial | nível de reputação em numeração e cor, no formato `<n>_<cor>` (exemplo ilustrativo: `3_yellow`); a lista completa de valores não está na doc | derivado de O4–O6 | conta; janela de O7 | ↑ | oficial: a cor sai das faixas de O4–O6 | `seller_reputation.level_id` | Usuários (default) | fotografia no momento da chamada; cadência de recálculo não documentada | 4 contas (nenhuma ingerida) | documentado não ingerido | evidência interna (sob pedido) |
| O2 Proteção (`real_level`, `protection_end_date`) | Oficial | nível real durante a proteção e a data do fim; dados reais em `excluded` | — | conta | — | — | `seller_reputation.real_level`, `.protection_end_date`, `metrics.*.excluded.real_value` / `real_rate` | idem | idem | 4 contas | documentado não ingerido | evidência interna (sob pedido) |
| O3 MercadoLíder (`power_seller_status`) | Oficial | Silver, Gold ou Platinum; nulo = não é líder | — | conta | ↑ | oficial: coluna "Líderes" da tabela de limites; os demais requisitos não estão na página | `seller_reputation.power_seller_status` | idem | idem | 4 contas | documentado não ingerido | evidência interna (sob pedido) |
| O4 Reclamações (`claims`) | Oficial | vendas com reclamação do comprador; exclui inabilitados/fraudulentos, pagamentos recusados, vendas inválidas e a tag `avoid_reputation` | vendas com reclamação / vendas totais | conta; 60 dias se houve ≥60 vendas em 60 dias (MLB), senão 365 | ↓ | oficial MLB: Líder ≤1%, Verde ≤2%, Amarelo ≤4,5%, Laranja ≤8%, Vermelho >8% | `metrics.claims.{rate,value,period,excluded}` | idem | idem | 4 contas | documentado não ingerido | evidência interna (sob pedido) |
| O5 Atraso de despacho (`delayed_handling_time`) | Oficial | % de vendas com envio atrasado; handling = `ready_to_ship`→`shipped`; só Mercado Envios com ≥10 vendas enviadas | vendas com envio atrasado / vendas enviadas com me2 | conta; 60 ou 365 dias | ↓ | oficial MLB: Líder ≤6%, Verde ≤10%, Amarelo ≤18%, Laranja ≤22%, Vermelho >22% | `metrics.delayed_handling_time.*` | idem | idem | 4 contas | documentado não ingerido | evidência interna (sob pedido) |
| O6 Cancelamentos (`cancellations`) | Oficial | cancelamentos **feitos pelo vendedor** sem reclamação | cancelamentos pelo vendedor / vendas totais | conta; 60 ou 365 dias | ↓ | oficial MLB: Líder ≤0,5%, Verde ≤1,5%, Amarelo ≤3,5%, Laranja ≤4%, Vermelho >4% | `metrics.cancellations.*` | idem | idem | 4 contas | documentado não ingerido | evidência interna (sob pedido) |
| O7 Vendas concluídas (`sales.completed`, `period`) | Oficial | total menos as canceladas; `period` diz a janela usada | contagem | conta; 60 dias (365 se houve menos de 60 vendas, MLB) | ↑ | 60 vendas em 60 dias define a janela MLB | `metrics.sales.{completed,period}` | idem | idem | 4 contas | documentado não ingerido | evidência interna (sob pedido) |
| O8 Transações e qualificações | Oficial | `transactions.{total,completed,canceled}` (histórico) e `ratings.{positive,neutral,negative}` | contagens / % | conta; histórico | ↑ positivo | nenhum | `seller_reputation.transactions` | idem | idem | 4 contas | documentado não ingerido | evidência interna (sob pedido) |
| O9 Experiência de compra por anúncio | Oficial | nível/cor do anúncio por reclamações e cancelamentos | por item | item; janela não explicitada | ↑ | níveis do próprio recurso | `GET /reputation/items/{ITEM_ID}/purchase_experience/integrators` | Publicação e sincronização | não documentado | não testado | documentado não ingerido; não testado | evidência interna (sob pedido) |
| O10 Qualidade do anúncio | Oficial | `level_wording`: Básica, Satisfatória ou Profissional | por item | item | ↑ | 3 níveis | `GET /item/{ITEM_ID}/performance` | Publicação e sincronização | não documentado | não testado | documentado não ingerido; não testado | evidência interna (sob pedido) |
| O11 Moderações/infrações | Oficial | moderação por anúncio e infrações do usuário | contagem | evento | ↓ | nenhum | `GET /moderations/last_moderation/{ref}`; `GET /moderations/infractions/{USER_ID}` | permissão não identificada | não documentado | não testado | documentado não ingerido; não testado | evidência interna (sob pedido) |
| O12 Reclamações individuais | Oficial | cada claim com `type`, `stage`, `status`, `resolution` | por reclamação | evento | ↓ | nenhum (não é a taxa de reputação) | `GET /post-purchase/v1/claims/search` | Comunicação pré e pós-venda; Vendas e envios | sob demanda | não testado; nenhum código chama | documentado não ingerido; não testado | evidência interna (sob pedido) |
| T1 Taxa de cancelamento ML | Torre | todo pedido `status='cancelled'`, de qualquer autor; **não é O6** | `cancelled_orders / total_orders` (gold) ou `canceled / (orders+canceled)` (Neon) | dia × marca, somado no mês | ↓ | interno: alerta se ≥ mediana do canal × 1,5 (mín. 2 marcas); nunca as faixas de O6 | `gold.vw_ml_gestao_daily` → `marts.fact_marketplace_daily_performance` → `/api/v1/performance/{overview,quality,executive-summary}` | — | D-1 (sync diário) | 4 marcas | sustentado hoje | evidência interna (sob pedido) |
| T2 Cancelamento Full × não-Full | Torre | cancelados / elegíveis (todos os status criados no dia) | `cancelled_orders / eligible_orders` | marca × classe × dia (coorte do pedido) | ↓ | nenhum | `marts.fact_ml_fulfillment_daily` → `/api/v1/performance/ml-fulfillment` → `/full-ml` | — | `manual_snapshot` | 4 marcas | sustentado hoje | `docs/kpi_dictionary.md:223-260` |
| T3 Handling médio | Torre | da criação do pedido até `date_shipped`; **não é O5** | soma de segundos / amostra | pedido × classe | ↓ | nenhum | idem T2 | — | `manual_snapshot` | 4 marcas | sustentado hoje | evidência interna (sob pedido) |
| T4 Expedição ML (backlog, idade, >48h, travados) | Torre | shipments seller-managed `ready_to_ship` de pedido `paid`; sem prazo (`deadline_status=unavailable`) | contagens | fotografia atual; shipment | ↓ | interno: 48 h (`OPERATIONAL_AGE_LIMIT`); não é SLA do ML | `raw.ml_shipments` ⋈ `raw.ml_orders` → `/api/v1/expedicao` → `/expedicao` | — | fotografia do run | 4 marcas | sustentado hoje | `docs/expedicao_contrato.md:993-1020` |
| T5 Não entregue | Torre | não entregues / pedidos ML do período, como está no código (`ml_not_del / ml_orders × 100`, `None` se não há pedidos). **Atenção:** a view de origem tem `not_delivered_rate_pct` por shipment; a API recalcula sobre **pedidos**. A tela segue a API e cita "por pedido" | contagem | dia × marca | ↓ | nenhum | `gold.vw_ml_gestao_daily.not_delivered_rate_pct` → Neon `ml_not_delivered_rate_pct` → `GET /api/v1/performance/quality` | — | D-1 | 4 marcas | sustentado hoje | `apps/api/app/services/performance_service.py:1504` |
| P1 Cancelado pelo vendedor (proxy de O6) | Torre (proxy) | `cancel_detail.requested_by='seller'`; é um piso de O6, não o valor | seller / pedidos | 60 dias | ↓ | nenhum | `raw.ml_orders.cancel_detail` | — | `ml_orders_etl` a cada 15 min | 4 marcas | indisponível (medido na descoberta; não existe na Torre) | evidência interna (sob pedido) |
| P2 Pedido com mediação | Torre (proxy ruim) | `jsonb_array_length(mediations)>0`; mistura tipos de claim e não aplica `avoid_reputation` | pedidos com claim / pedidos | 60 dias | ↓ | nenhum | `raw.ml_orders.mediations` | — | `ml_orders_etl` a cada 15 min | 4 marcas | indisponível; **não usar como reputação** | evidência interna (sob pedido) |

### 5.3 TikTok Shop BR

Lojas: apice, barbours, denavita, gocase, kokeshi, lescent e rituaria.

O escopo `data.shop_analytics.public.read` está concedido de fato às 7 lojas, porque elas já recebem
`raw.tiktok_analytics_shop_hourly`. A barreira do SPS é a região da loja, não o escopo (evidência interna, sob
pedido).

| Métrica | Oficial/Torre | Definição | Numerador/base | Grão/janela | Melhora | Limiar | Fonte/endpoint/tabela | Escopo | Frescor | Cobertura | Status | Evidência |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPS (Shop Performance Score) | Oficial | nota de 0 a 5 sobre produto, logística/fulfillment e atendimento, comparada com pares da categoria | calculada pela plataforma; ≥30 pedidos entregues em 90 dias | loja; 90 dias | ↑ | oficial na resposta (`sps_tier`); a doc proíbe fixar o limiar no código | `GET /analytics/202606/shop_performances/overview` | `data.shop_analytics.public.read` | `update_time`; sem webhook | só lojas dos EUA: 0 das 7 lojas BR | indisponível para BR (documental, não medido) | evidência interna (sob pedido) |
| NRR (Negative Review Rate) | Oficial (SPS) | taxa de avaliações negativas | pedidos com avaliação negativa / pedidos entregues | loja; 60 dias | ↓ | oficial por categoria (`benchmarks`) | `GET /analytics/202606/shop_performances/metrics` | idem | idem | só EUA | indisponível para BR (documental) | evidência interna (sob pedido) |
| NBFR (Non-Buyer Fault Return & Refund) | Oficial (SPS) | devolução ou reembolso sem culpa do comprador | base não detalhada pela doc | loja; 60 dias | ↓ | oficial (`benchmarks`) | idem | idem | idem | só EUA | indisponível para BR (documental) | evidência interna (sob pedido) |
| SFCR (Seller Fault Cancellation Rate) | Oficial (SPS) | cancelamento por culpa do vendedor | base não detalhada pela doc | loja; 30 dias | ↓ | oficial (`benchmarks`) | idem | idem | idem | só EUA | indisponível para BR (documental) | evidência interna (sob pedido) |
| OTDR (On-Time Delivery Rate) | Oficial (SPS) | entrega no prazo | `on_time_deliver_order_count / delivered_order_count` | loja; 30 dias | ↑ | oficial (`benchmarks`) | idem | idem | idem | só EUA | indisponível para BR (documental) | evidência interna (sob pedido) |
| IM_DSAT | Oficial (SPS) | insatisfação no chat | base não detalhada pela doc | loja; 60 dias | ↓ | oficial (`benchmarks`) | idem | idem | idem | só EUA | indisponível para BR (documental) | evidência interna (sob pedido) |
| AHT (After-Sales Handling Time) | Oficial (SPS) | tempo de tratamento do pós-venda | horas | loja; 60 dias | ↓ | oficial (`benchmarks`) | idem | idem | idem | só EUA | indisponível para BR (documental) | evidência interna (sob pedido) |
| AHR (Shop Health Rating) | Oficial, só UI | pontos ganhos e perdidos por violações | pontuação de 0 a 1000 | loja; 180 dias | ↑ | oficial **EUA**: 0 desativada; 1–50 Poor; 51–199 Fair; 200–1000 Excellent | Seller Center > Account health > Shop health; nenhum endpoint | papel no Seller Center (UI) | diário na UI | doc só dos EUA; BR não comprovado | indisponível (sem API) | evidência interna (sob pedido) |
| Registros de violação | Oficial, só UI | status Enforced, No action ou Pending | contagem | loja; 7 e 60 dias | ↓ | nenhum publicado | Seller Center > Shop Health > Violation Records | UI | UI | EUA (doc); BR não comprovado | indisponível (sem API) | evidência interna (sob pedido) |
| LDR oficial do TikTok | Oficial, só UI (não comprovado) | atraso de postagem pelo critério da plataforma; nenhuma doc oficial para o BR foi encontrada | desconhecido | desconhecido | ↓ | desconhecido (os 4% do repo não têm fonte) | Seller Center (UI); sem endpoint agregado | — | — | — | indisponível (sem API agregada) | evidência interna (sob pedido) |
| Prazo oficial por pedido (insumo, não KPI) | Oficial (campo) | `rts_sla_time` (último envio), `tts_sla_time` (última coleta), `shipping_due_time`/`collection_due_time`, `collection_time`, `cancel_time`, `cancellation_initiator`, `delivery_sla_time` | — | pedido | — | — | `POST /order/202309/orders/search` (já chamado pela ingestão) | Order Information (Public), já em uso | ingestão incremental de pedidos | changelog: todos os mercados; cobertura BR nunca medida | documentado não ingerido (SLA-1 só em `develop` do airflow; Data Mart sem as colunas) | evidência interna (sob pedido) |
| Atendimento (Customer Service Performance) | Oficial (API, fora do SPS) | `response_percentage` (1ª resposta em até 24 h), `response_time_mins`, `satisfaction_percentage` (4–5 estrelas) | sessões iniciadas ou avaliadas no período | loja; período livre | ↑ / ↓ / ↑ | nenhum na API | `GET /customer_service/202407/performance` | `seller.customer_service` + Instant Message; não se sabe se o app tem | sob demanda | doc sem restrição de região; nunca chamado | documentado não ingerido (candidato não testado) | evidência interna (sob pedido) |
| LDR operacional, coleta (TTS), padrão | Torre | atraso de coleta contra um prazo **reconstruído** de 2 dias úteis após o pagamento | (coletados após o prazo + não coletados vencidos) / (pagos − amostras − cancelados antes do vencimento) | coorte (pagamento, marca); taxa pelo vencimento, só coortes maduras | ↓ | referência de 4% sem fonte oficial (`META_TIKTOK_LDR`) + crítico interno de 10%; tratar os dois como internos | `marts.expedicao_tiktok_dispatch_daily` → `GET /api/v1/expedicao/tiktok?event=coleta&days=N` | leitura da Raw do Data Mart | DAG `marketplace_tower_expedicao_refresh` a cada 2 h (`--max-idade-fonte-horas 3`) | 6 marcas (sem `gocase`); tabela no Neon não medida | sustentado hoje | evidência interna (sob pedido) |
| LDR operacional, despacho (RTS) | Torre | atraso de etiqueta contra um prazo **reconstruído** de 1 dia útil | (despachados após o prazo + não despachados vencidos) / mesma base | idem | ↓ | idem | idem, `event=despacho` | idem | idem | idem | sustentado hoje | evidência interna (sob pedido) |
| Fluxo por data de pagamento | Torre (auxiliar) | a mesma partição, agrupada pelo dia do pagamento; **nunca é LDR** | atrasados / pagos no dia | (pagamento, marca) | ↓ | nenhum | `payment_flow` no mesmo payload | idem | idem | idem | sustentado hoje | evidência interna (sob pedido) |
| Taxa de cancelamento TikTok (`/quality`) | Torre | — | sempre `null`: o gold não tem cancelamento do TikTok | marca × mês | ↓ | nenhum | `GET /api/v1/performance/quality` | — | — | 0 marcas | indisponível (nunca exibir 0%) | evidência interna (sob pedido); `docs/gold_vs_marts_matrix.md:111` |
| Nota média por produto | Torre (sobre campo oficial) | média ponderada de `performance.ratings[]` do produto; **não é NRR** | Σ(estrelas × qtd) / Σ qtd | produto × dia | ↑ | nenhum | `raw.tiktok_analytics_products.rating_avg` → `/produtos/tiktok` | `data.shop_analytics.public.read` | diário (`tiktok_analytics_daily`) | 7 marcas | sustentado hoje (grão de produto, não de conta) | evidência interna (sob pedido) |

## 6. Tela mínima (T-0014)

Persona: gestor/operador de marketplace. Rotas novas da tela ficam sob `/api/v1/performance-contas` (§1.1). As
rotas citadas abaixo são as fontes atuais que alimentam a tela.

### 6.1 O que pode mostrar HOJE

Só entra dado já sustentado. **Nenhum indicador oficial de reputação ou saúde de conta está ingerido em nenhum
canal.** Por isso tudo o que a tela mostra com número hoje é **cálculo Torre** e precisa trazer esse rótulo.

| Canal | Indicador | Rota/tabela | Rótulo obrigatório | Limiar exibível |
|---|---|---|---|---|
| Mercado Livre | T1 Taxa de cancelamento | `/api/v1/performance/{overview,quality,executive-summary}` sobre `marts.fact_marketplace_daily_performance` | "Cálculo Torre · todos os cancelamentos, qualquer autor · não é o cancelamento da reputação ML" | só o interno (mediana × 1,5); **nunca** as faixas MLB |
| Mercado Livre | T2 Cancelamento Full × não-Full; T3 Handling médio | `/api/v1/performance/ml-fulfillment` sobre `marts.fact_ml_fulfillment_daily` | "Cálculo Torre"; T3 com "criação → envio; não é o atraso de despacho do ML" | nenhum |
| Mercado Livre | T4 Expedição (backlog, acima de 48 h, travados) | `/api/v1/expedicao` | "Cálculo Torre · 48 h é limite interno, não SLA do ML" | interno de 48 h |
| Mercado Livre | T5 Não entregue | `GET /api/v1/performance/quality`, campo `ml_not_delivered_rate_pct` (`apps/api/app/services/performance_service.py:1504`) | "Cálculo Torre" | nenhum |
| TikTok | LDR operacional, coleta (padrão) e despacho | `GET /api/v1/expedicao/tiktok?brands=` sobre `marts.expedicao_tiktok_dispatch_daily` | "Cálculo Torre · prazo reconstruído (2/1 dias úteis)". Passa a "prazo oficial TikTok" só quando `deadline_is_reconstructed = false` | os 4% como "referência interna, sem fonte oficial" até a lacuna 7 da §7 ser resolvida; nunca como meta da plataforma |
| Shopee | LDR operacional (KPI Torre) | `GET /api/v1/expedicao/shopee/ldr` sobre `marts.expedicao_shopee_ldr_daily` | Hoje: exibida como **indisponível** (`sem_fonte`, "não publicada") até a fonte/flag estar ativa (§6.2). Quando ativa: "Cálculo Torre · não é a Late Shipment Rate oficial nem métrica de saúde da conta da Shopee" | nenhum (sem meta e sem cor de severidade) |

Frescor e cobertura que cada linha precisa mostrar:
- **T1 e T5:** D-1.
- **T2 e T3:** `manual_snapshot`, que pode estar velho; a data precisa aparecer.
- **T4:** o instante da fotografia.
- **LDR TikTok:** a idade da publicação (o refresh roda a cada 2 h).
- **LDR Shopee (quando ativa):** `snapshot.stale` acima de 6 h.
- **Cobertura:** ML com 4 marcas (Apice fora, sem conta ML), TikTok com 6 marcas (`gocase` fora do escopo da LDR)
  e Shopee com 4 contas com app (Kokeshi fora, sem app).

### 6.2 O que fica como estado indisponível explicado

| Canal | Bloco | Estado (§4) | Texto sugerido |
|---|---|---|---|
| Shopee | Saúde da conta oficial (nota, métricas, pontos, punições) | `nao_comprovado` | "Indisponível: a autorização da API de saúde da conta da Shopee ainda não foi comprovada (probe de 21/10 inconclusivo, sem chamadas)." |
| Shopee | Kokeshi | `sem_fonte` | "Conta sem app Shopee." |
| Shopee | LDR operacional (KPI Torre) | `sem_fonte` (`disabled`, `table_not_migrated` ou `no_series_published`), até a fonte/flag estar ativa | "LDR operacional da Shopee (cálculo Torre) ainda não publicada. Não é métrica oficial de saúde da conta." Nunca `vazio_legitimo` nem 0. |
| Mercado Livre | Reputação oficial (termômetro, MercadoLíder, 3 taxas) | `sem_fonte` (não ingerido) | "A reputação oficial do Mercado Livre ainda não é capturada pela Torre." |
| Mercado Livre | Apice | fora da cobertura | "Sem conta Mercado Livre no cadastro." |
| TikTok | SPS, AHR e violações | `sem_fonte` (`not_available`) | "O TikTok não expõe SPS/saúde da conta por API para lojas BR (doc oficial: só EUA). Verificado em 26/10/2026." Mais o link para o Seller Center (**TODO:** URL). Nunca 0 nem vazio. |
| TikTok | LDR oficial | `sem_fonte` | "A LDR oficial do TikTok não sai por API; a taxa acima é cálculo Torre." |
| TikTok | Taxa de cancelamento | `sem_fonte` | "Cancelamento do TikTok ainda não calculado pela Torre." Nunca 0%. |

### 6.3 O que a tela não pode fazer

- Colocar um limiar oficial (faixas MLB, `target` Shopee, benchmarks SPS) ao lado de um número Torre, ou colorir
  um número Torre com a faixa oficial.
- Apresentar a LDR operacional da Shopee como métrica oficial de Health.
- Usar P1/P2 (ML) ou a nota média por produto (TikTok) como reputação.
- Converter `null`, ausência, erro, não comprovado ou série não publicada em 0 ou em vazio legítimo.
- Exibir dado demonstrativo.

## 7. Lacunas e próximas tarefas (apenas propostas)

O esforço é uma **estimativa do redator, não medida**: P até 1 dia, M de 2 a 4 dias, G mais de 1 semana. Migration,
deploy e escrita em banco real ficam com um humano, conforme o `CLAUDE.md` do repo. Merge, criação de DAG/tabela e
ligar a flag também, conforme os relatórios internos de capacidade.

| # | Proposta | Fonte | Esforço | Dependências | Exige ação humana |
|---|---|---|---|---|---|
| 1 | ML, fatia 1 (dado). DAG `mercadolivre_seller_reputation_snapshot`: inerte, pausada, `modo=dry_run` e flag `ML_REPUTATION_WRITE_ENABLED`. Faz 1 GET `/users/{seller_id}` por conta por dia e grava em `raw.ml_seller_reputation_snapshot`. Usa o token do cofre via `get_current_token`, **sem renovar** (nunca `refresh_ml_token`). Grava só a subárvore `seller_reputation` + `id`/`site_id` (PII). Confere `id` contra o `seller_id` do registry. 401/403 viram desfecho, nunca zero. | evidência interna (sob pedido) | M | molde `dags/mercadolivre_full_stock_snapshot.py`; janela do token do `ml_orders_etl` | criar DAG e tabela no `goca-se/airflow`, ligar a flag, deploy |
| 2 | ML, fatia 2 (serving). Sync para `marts.ml_seller_reputation_daily` e `GET /api/v1/performance-contas/ml-reputacao?brand=` (prefixo da §1.1). Limiares MLB como constantes com URL e data de origem, também registrados em `docs/kpi_dictionary.md`. | evidência interna (sob pedido) | M | #1 | migration no Neon, deploy |
| 3 | ML, fatia 3 (tela). Seção "Reputação ML" com selo "Oficial ML · capturado em {fetched_at}", aviso acima de 36 h e bloco separado "Indicadores da Torre". | evidência interna (sob pedido) | M | #2 | deploy |
| 4 | TikTok: levar o prazo oficial à produção. PR `develop → main` da SLA-1 (`1d5f2f3d`), probe inerte uma vez, depois ligar `TIKTOK_RTS_SLA_INGEST_ENABLED`. Ampliar o mapeamento para `tts_sla_time`, `collection_time` e `cancellation_initiator`, porque a LDR padrão é de coleta. | evidência interna (sob pedido); `docs/handoff_tiktok_sla_ingestao.md` | M (promoção) + M (ampliação) | ordem já documentada no handoff | merge, deploy, ligar a flag |
| 5 | TikTok: probe de Customer Service Performance. Uma loja, disparo único, parando no primeiro erro de escopo (molde do probe Account Health da Shopee). | evidência interna (sob pedido) | P | — | disparar o probe; se faltar escopo, decidir sobre a reautorização das lojas |
| 6 | TikTok: cancelamento Torre. Definir o denominador (pagos × criados, `docs/backlog.md:285`) e servir como bloco aditivo. Depois da #4, criar a variante "culpa do vendedor" (`cancellation_initiator = 'SELLER'`, base = pagos sem amostra, 30 dias), sempre rotulada como Torre. | evidência interna (sob pedido); `docs/backlog.md:285` | P–M | #4 para a variante do vendedor | decidir o denominador |
| 7 | TikTok: a meta de 4% não tem fonte. Obter a fonte oficial (print do Seller Center BR ou doc da Seller University BR) ou rebaixar o rótulo para "referência interna". | evidência interna (sob pedido) | P | — | fornecer a fonte ou aprovar o rebaixamento |
| 8 | TikTok: captura manual semanal de SPS, notas por métrica, AHR e violações do Seller Center BR para CSV versionado com proveniência. Acompanhar o changelog ("New SPS APIs") e refazer o teste a cada release. | evidência interna (sob pedido) | P (processo) | — | definir quem captura e com que acesso |
| 9 | TikTok: medir `marts.expedicao_tiktok_dispatch_daily` no Neon (contagem e datas), que não foi consultada. | evidência interna (sob pedido) | P | leitor do Neon (só leitura) | nenhuma, se houver leitor só de leitura |
| 10 | Shopee: probe T-0004 (`shopee_account_health_capability_probe`, em main, pausado, sem schedule, só barbours). | evidência interna (sob pedido) | P (um disparo) | token do cofre com folga; prioridade dos estoques | decidir a prioridade e disparar |
| 11 | Shopee: fotografia diária por conta (`metric_id`, `current_period`, `last_period`, `target`, `comparator`, `unit`, pontos do trimestre, punições em andamento), com status explícito. **Só depois de `AH_AVAILABLE_*`.** Reusa `src/shopee/` sem renovar o token fora do mecanismo oficial. | evidência interna (sob pedido) | M | #10 | criar DAG e tabela, migration, deploy |
| 12 | Shopee: definição numérica oficial de cada taxa (Central do Vendedor BR). | evidência interna (sob pedido) | P | — | acesso à Central do Vendedor |
| 13 | Shopee: publicar a LDR operacional (KPI Torre, não Health). Aplicar a migration 030, ligar as flags de API e web e despausar a DAG de publicação. Até lá, a tela mostra `sem_fonte` ("não publicada"). | `docs/expedicao_contrato.md:1269-1318` | P | — | migration, flags no Render/Vercel, despausar a DAG |
| 14 | ML: testar O9–O12 (permissões "Publicação e sincronização" e "Comunicação pré e pós-venda"; moderações sem permissão identificada). | evidência interna (sob pedido) | P–M | — | decidir se entra no escopo |
| 15 | ML: prazo por pedido. Reextrair `shipping_option.estimated_handling_limit` (gate próprio). O5 oficial cobre o KPI agregado, mas não o prazo por pedido. | evidência interna (sob pedido); `docs/expedicao_contrato.md:1017-1020` | G (gate próprio) | — | aprovar o gate |

## 8. Plano de verificação

Vale para todas as fontes:
- a contagem por conta precisa bater com o registry (Shopee: 4 contas com app; ML: 4; TikTok: 7, ou 6 na LDR);
- cada indicador expõe o frescor;
- falha nunca vira zero.

**ML, reputação oficial (#1–#3):**
- O dry-run retorna 4 respostas, e em cada uma o `id` é igual ao `seller_id` do registry.
- O payload gravado tem só `seller_reputation`, `id` e `site_id`, verificado por **allowlist** de chaves e não por
  lista de chaves proibidas.
- Há 1 linha por conta × dia, e a escrita é idempotente.
- Para 401/403, o desfecho é gravado e nenhuma taxa é preenchida.
- **Reconciliação:** no mesmo dia, comparar `level_id`, `power_seller_status` e as 3 taxas com o termômetro do
  painel do vendedor no Mercado Livre, conta a conta. Conferir o formato da resposta contra a doc oficial de
  `GET /users/{id}`.
- **Frescor:** `fetched_at`, com aviso acima de 36 h. Não prometer "diário oficial", porque a cadência de recálculo
  não é documentada.

**Shopee (#10–#13):**
- O probe precisa terminar com veredito por conta (`AH_AVAILABLE_*` ou negativa explícita). Um novo
  `AH_INCONCLUSIVE` não autoriza a ingestão.
- Depois da ingestão:
  - todo `metric_id` traz `target` e `comparator`;
  - lista vazia de pontos ou punições com resposta de sucesso conta como `vazio_legitimo`;
  - comparar com o Seller Center (hoje só por export manual, `docs/expedicao_contrato.md:1266-1267`);
  - medir a cadência real, que a doc não informa.
- Na LDR operacional, usar o `--reconcile` do publisher (`docs/expedicao_contrato.md:1288-1292`). Antes da
  publicação, conferir que a tela mostra `sem_fonte` ("não publicada") e não `vazio_legitimo` nem 0.

**TikTok (#4–#9):**
- A LDR operacional já foi reconciliada com a planilha do gestor (medido na descoberta; evidência interna, sob
  pedido). Repetir essa reconciliação ao trocar o prazo reconstruído pelo oficial.
- Antes de virar `deadline_is_reconstructed = false`, medir a cobertura de `rts_sla_time` e `tts_sla_time` por
  marca no BR (nunca medida) e comparar a LDR reconstruída com a LDR pelo prazo oficial, coorte a coorte.
- No probe de atendimento, registrar o desfecho (sucesso ou erro de escopo) e não gravar valores no primeiro
  disparo.
- Na captura manual, registrar quem capturou, quando e a evidência (print), e reconciliar na semana seguinte.

**KPIs Torre já publicados:** conferir que o rótulo "Cálculo Torre" e o frescor aparecem em todas as linhas da §6.1
e que nenhum limiar oficial aparece na mesma linha.

## 9. Riscos e assunções

- **[Assunção]** As contas ML não cobertas pela leitura manual pontual têm a mesma permissão porque usam o mesmo
  app (`ml__app`). A chamada nunca foi feita para elas.
- **[Risco]** Os limiares MLB são de 11/08/2025 e podem mudar. Guardar com URL e data.
- **[Risco]** P1 é um piso de O6. Não se sabe se o ML atribui ao vendedor os cancelamentos `meli/shipment`.
- **[Risco]** Sem histórico na API:
  - ML: só o estado atual;
  - Shopee: período corrente e anterior, mais o trimestre corrente.

  A tendência só começa no primeiro snapshot.
- **[Risco]** `/users/{id}` devolve PII da conta. Gravar o payload inteiro seria um vazamento.
- **[Risco]** Renovar o token fora do mecanismo oficial quebra outras ingestões: o refresh_token do ML é de uso único
  e 5 DAGs já renovam.
- **[Risco]** Na Shopee, a autorização depende de uma única chamada, e a doc não traz fórmula, `rate_limit` nem
  frescor.
- **[Assunção]** A semântica de `callable_categories` do TikTok foi inferida do bundle. Não garante dado para loja
  daquela região.
- **[Risco]** AHR e violações no BR: a doc é só dos EUA, e a existência para o BR não foi comprovada.
- **[Risco]** A "referência" de 4% da LDR TikTok não tem fonte oficial no repo.
- **[Risco]** A tabela servida da LDR TikTok no Neon não foi medida.
- **[Assunção]** O esforço da §7 é estimativa do redator.

## 10. Divergências documentais encontradas (não corrigidas aqui)

1. `docs/kpi_dictionary.md:152` marca a "Taxa de cancelamento" do TikTok como ✅ `cancel_rate_pct`. Mas
   `/api/v1/performance/quality` devolve `tiktok_cancel_rate` sempre `null` (`docs/gold_vs_marts_matrix.md:111`).
   **TODO:** alinhar o dicionário.
2. O relatório interno de capacidade da Shopee descreve o limiar da LDR operacional como "interno", enquanto
   `docs/expedicao_contrato.md:1221` diz que não há meta nem cor de severidade. Este contrato segue o contrato da
   Expedição (limiar: nenhum).
3. `docs/kpi_dictionary.md:208-211` lista Cancel% e Devol% da Shopee na tela de Qualidade. O relatório interno da
   Shopee não avaliou esses KPIs, então eles **não** entram na §6.1. **TODO:** confirmar a definição e o frescor
   antes de usá-los na tela de performance, sempre como cálculo Torre.
