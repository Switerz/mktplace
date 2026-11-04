# Contrato: Atendimento (CX) dos marketplaces — aba da Performance das contas

> CX-MKT-0, 03/11/2026. Rascunho de contrato da aba **Atendimento** dentro de `/performance-contas`.
> Base: pesquisa da doc oficial de Mercado Livre, TikTok Shop e Shopee feita em 03/11/2026 (§11) e
> medições no Data Mart do mesmo dia. **Nenhuma API de CX foi chamada ainda**: a permissão de cada app
> é a primeira coisa a provar (§8, fase 0). Vocabulário alinhado com `docs/performance_contas_contrato.md`.

## 1. Objetivo

**Decisão que a aba apoia:** *onde o atendimento pós-venda está falhando, por quê, e o que precisa de
resposta nossa agora*.

**Quem usa:** o mesmo gestor/operador de marketplace da Performance das contas. Leitura diária da fila
("o que vence hoje") e leitura semanal dos motivos ("por que estão reclamando").

**Por que dentro da Performance das contas:** reclamação, devolução e cancelamento são justamente o que
derruba a reputação/saúde da conta nas três plataformas. A aba "Saúde da conta" mostra o termômetro; a aba
"Atendimento" mostra os casos que movem o termômetro.

**Fora do escopo:** responder ao cliente pela Torre (a Torre só lê), conteúdo das conversas, dados pessoais
do comprador, NPS e review do site (já cobertos pela base de CX do time de CX).

## 2. Cobertura

| Canal | Contas | Fora | Motivo |
|---|---|---|---|
| Mercado Livre | barbours, kokeshi, lescent, rituaria | apice | Sem conta ML integrada: nenhum pedido em `api.vw_ml_orders` (medido 03/11/2026) e sem token no cofre. Existe um vendedor "APICE COSMÉTICOS" no site do ML, não confirmado como nosso — **pendente de confirmação do negócio** (§10). |
| TikTok Shop | apice, barbours, kokeshi, lescent, rituaria | — | Mesmas 5 marcas da Torre (as lojas `denavita` e `gocase` existem no app, mas estão fora do escopo da Torre). |
| Shopee | apice, barbours, lescent, rituaria | kokeshi | Sem app Shopee (só export manual). |

## 3. Fontes por canal

Status em todas as linhas: **documentado, não ingerido, permissão não comprovada** até o probe da fase 0.

### 3.1 Mercado Livre — permissão funcional "Comunicação pré e pós-venda"

| Dado | Endpoint | Grão | Observação |
|---|---|---|---|
| Reclamações | `GET /post-purchase/v1/claims/search?players.role=respondent&players.user_id={seller}` | reclamação | `type` (mediations, return, cancel_purchase…), `stage` (claim, dispute…), `status` (opened/closed), `reason_id`, `players[].available_actions[]` (ação pendente + `due_date`), `resolution` (`reason`, `benefited`, `closed_by`, data). Página máx. 100, `offset+limit < 10000`. Filtro por `range=last_updated:after:…` para incremental. |
| Motivo em texto | `GET /post-purchase/v1/claims/reasons/{reason_id}` | motivo | `name`, `detail` (texto em espanhol), `settings.rules_engine_triage` (repentant, defective, incomplete, different, not_working). Cachear: muda raramente. |
| Quem agiu e quando | `GET /post-purchase/v1/claims/{id}/actions-history` | evento | Base do "respondida em" (primeira ação do vendedor). |
| Afeta reputação | `GET /post-purchase/v1/claims/{id}/affects-reputation` | reclamação | affected / not_affected / not_applies. Só para abertas e fechadas no período. |
| Devolução | `GET /post-purchase/v2/claims/{id}/returns` | devolução | `status`, `status_money`, `subtype`, datas. Motivo = o da reclamação. |
| Perguntas pré-venda | `GET /questions/search?seller_id=…&api_version=4` | pergunta | `status` (ANSWERED, UNANSWERED…), `date_created`, `answer.date_created`. Não respondidas somem após 7 meses. |
| Tempo de resposta oficial | `GET /users/{id}/questions/response_time` | conta | Média em minutos, janela de 14 dias, atualiza 1×/dia; 404 = sem perguntas. |
| Mensagens pendentes | `GET /messages/unread?role=seller&tag=post_sale` | conta | Só contagem. **Nunca** ler `/messages/packs` sem `mark_as_read=false`. |

Sinal já disponível sem API nova: `api.vw_ml_orders.mediations` lista os ids de reclamação por pedido.
Medido em 90 dias (até 03/11/2026): barbours 1.569, kokeshi 3.182, lescent 1.362, rituaria 616 pedidos com
reclamação. Serve de **reconciliação** da ingestão (todo id dali precisa aparecer no `claims/search`), nunca
como número de tela: mistura tipos e não aplica as exclusões de reputação.

### 3.2 TikTok Shop — escopo "Return & Refund Basic" (público)

| Dado | Endpoint | Observação |
|---|---|---|
| Devoluções/reembolsos | `POST /return_refund/202309/returns/search` | `return_type`, `return_status`, `arbitration_status`, `return_reason` + `return_reason_text`, `seller_next_action_response[].action/deadline`, `refund_amount`. Incremental por `update_time_ge`. |
| Cancelamentos | `POST /return_refund/202309/cancellations/search` | `cancel_type`, `cancel_status`, `cancel_reason` + texto, ação pendente, valor. |
| Linha do tempo | `GET /return_refund/202309/returns/{id}/records` | Base do "respondida em". |
| Atendimento (chat) | `GET /customer_service/202407/performance` | `response_percentage` (1ª resposta ≤ 24 h), `response_time_mins`, `satisfaction_percentage`. **Escopo custom**, provavelmente não concedido: entra só se o probe confirmar. |

Motivos: códigos estáveis (`ecom_order_delivered_refund_reason_defective`…) com lista BR publicada na doc
(§11). Avaliação de produto: **não existe API** — fica fora.

### 3.3 Shopee — app "Seller In House System"

| Dado | Endpoint | Observação |
|---|---|---|
| Devoluções/disputas | `v2.returns.get_return_list` (+ `get_return_detail`) | `reason` (enum de ~30 valores), `status`, `negotiation_status`, `seller_proof_status`, `due_date`, `refund_amount`. **Janela máxima de 15 dias por consulta.** |
| Cancelamentos | **Já ingerido.** `silver.stg_shopee_orders` (`shopee_orders_etl`, a cada 6 h) traz `cancel_by`, `cancel_reason` e `buyer_cancel_reason` | Medido em 03/11/2026: 48.846 de 49.265 pedidos `cancelled` com motivo (99%). Nos últimos 30 dias o maior motivo é `system / Unpaid Order` (3.799), que não é atendimento e sai da contagem de casos. Motivos em inglês, com variações de grafia (ex.: "Need to Change Delivery Address" e "Need to change delivery address"): normalizar sem diferenciar maiúsculas. |
| Avaliações | `v2.product.get_comment` | `rating_star`, `comment_reply` (respondida ou não). Por item, sem filtro de data: varredura cara, fica para fase 3. |
| Chat | `v2.sellerchat.get_unread_conversation_count` | Seller In House tem Chat API pela doc de tipos de app; doc técnica fechada por login. Só se o probe confirmar. |

Regra herdada do repo do Airflow: o `refresh_token` da Shopee é de uso único por loja. Toda ingestão nova
usa o mecanismo oficial de token (`auth_service`) e nunca renova por conta própria.

## 4. Modelo canônico: um caso por linha

Todas as fontes viram `cx_caso`, com a mesma forma nos três canais:

| Campo | Definição |
|---|---|
| `canal`, `marca` | chaves da Torre |
| `caso_id` | id da plataforma (reclamação, devolução ou cancelamento) |
| `tipo` | `reclamacao`, `devolucao`, `cancelamento`, `mediacao` (ML `stage=dispute`, TikTok `arbitration_status`, Shopee `SELLER_DISPUTE`/`JUDGING`) |
| `estado` | `aguardando_vendedor`, `aguardando_comprador`, `com_a_plataforma`, `fechado` |
| `prazo_vendedor` | data-limite da ação pendente nossa (ML `available_actions.due_date`, TikTok `seller_next_action_response.deadline`, Shopee `due_date`) |
| `motivo_codigo`, `motivo_texto` | código e texto **da plataforma** (nunca texto livre do comprador) |
| `motivo_grupo` | grupo comum (§5) |
| `aberto_em`, `primeira_resposta_em`, `fechado_em` | datas |
| `resultado` | `a_favor_vendedor`, `a_favor_comprador`, `acordo`, `cancelado`, `sem_resultado` |
| `afeta_reputacao` | só ML (`affects-reputation`); nulo nos demais |
| `valor_reembolso` | quando a plataforma informa |
| `pedido_id` | para cruzar com a Expedição e com o produto |

Perguntas pré-venda (ML) e chat não são casos: vão para o retrato por conta (`cx_conta_dia`).

## 5. Grupos de motivo (proposta; fechar com os códigos reais do probe)

| Grupo | ML | Shopee | TikTok |
|---|---|---|---|
| Não recebido | `PNR*` | NONRECEIPT | códigos `*not_received*` |
| Avariado / defeito | `PDD*` com triagem defective / not_working | ITEM_DAMAGED, PHYSICAL_DMG, FUNCTIONAL_DMG, BROKEN_PRODUCTS, SPILLED_CONTENTS, DAMAGED_PACKAGE, SCRATCHED, DAMAGED_OTHERS, SPOILED_ROTTEN, EXPIRED_PRODUCT | `*defective*`, `*damaged*` |
| Diferente / errado | `PDD*` com triagem different | WRONG_ITEM, SELLER_SENT_WRONG_ITEM, DIFF_DESC, DIFFERENT_DESCRIPTION, LOOK_DEVIATION, SIZE_DEVIATION, ITEM_FAKE, EXPECTATION_FAILED, ITEM_NOT_FIT | `*wrong*`, `*not_as_described*` |
| Incompleto | `PDD*` com triagem incomplete | ITEM_MISSING | `*missing*` |
| Arrependimento | `PDD*` com triagem repentant | CHANGE_MIND, CHANGE_OF_MIND, NO_REASON, MUITAL_AGREE | `*change_of_mind*`, `*no_longer*` |
| Cancelamento | `CS*`, `cancel_purchase`, `cancel_sale` | cancelamentos | cancelamentos |
| Outros | resto | OTHER, USED… | resto |

Código fora do mapa vai para **Outros** e aparece numa lista de "códigos não mapeados" no rodapé da
seção, para o mapa ser corrigido. Nunca é descartado.

## 6. Indicadores da aba

Todos são **cálculo Torre** sobre casos da plataforma, com esse rótulo. Nenhum usa a cor de faixa oficial
de reputação (mesma regra do §2 do contrato da Performance das contas).

| Indicador | Definição | Janela |
|---|---|---|
| Aguardando nossa resposta | casos com `estado = aguardando_vendedor` | agora |
| Vencem em 24 h / vencidos | dos aguardando, `prazo_vendedor` nas próximas 24 h / já passado | agora |
| Casos novos | `aberto_em` no período, por tipo | período |
| Taxa de reclamação | casos novos (exceto cancelamento) / pedidos pagos do período, mesma marca × canal | período |
| A favor do vendedor | fechados com `a_favor_vendedor` / fechados com resultado | período |
| Tempo até a 1ª resposta | mediana de `primeira_resposta_em − aberto_em` | período |
| Tempo até fechar | mediana de `fechado_em − aberto_em` | período |
| Perguntas sem resposta (ML) | `status = UNANSWERED` | agora |
| Tempo de resposta a perguntas (ML) | valor **oficial** do endpoint `response_time` | 14 dias (do ML) |
| Atendimento no chat | TikTok `response_percentage`; Shopee não lidas | se o probe confirmar |

Ausência nunca vira zero: canal sem fonte aparece como `sem_fonte`; conta fora da cobertura aparece na lista
"Fora da cobertura"; permissão não comprovada aparece como `nao_comprovado`.

## 7. Tela

Aba nova na Performance das contas: **Saúde da conta | Atendimento**, com estado na URL (`?aba=atendimento`),
os mesmos filtros de canal e marca, mais o período.

1. **Faixa de KPIs:** Aguardando nossa resposta (com "vencem em 24 h" e "vencidos" abaixo), Casos novos,
   Taxa de reclamação, A favor do vendedor, Tempo até a 1ª resposta, Perguntas sem resposta (ML).
2. **Fila "Precisa de ação"** (o bloco principal da leitura diária): casos aguardando o vendedor, ordenados
   pelo prazo, com canal, marca, tipo, motivo, aberto há, prazo (vermelho se vencido) e link para o caso no
   painel do vendedor da plataforma.
3. **Principais motivos:** barras horizontais por grupo de motivo, empilhadas por canal, com % e contagem;
   clicar no grupo abre os motivos detalhados da plataforma.
4. **Evolução semanal:** casos novos × fechados por semana, por canal.
5. **Marca × canal:** tabela com novos, aguardando, taxa, % a favor e tempo até fechar.
6. **Rodapé de fontes:** frescor por canal, cobertura e códigos de motivo não mapeados.

### 7.1 Fatia 1 (CX-MKT-1): o que já existe no Data Mart, sem DAG nova

Implementada antes das ingestões, para a aba nascer útil sem depender de revisão no Airflow.

| Canal | Indicadores | Fonte (lida direto pela API, engine do Data Mart) |
|---|---|---|
| ML | Cancelamentos e taxa de cancelamento (qualquer autor); motivos de cancelamento; tabela por marca | `api.vw_ml_orders` (`cancel_detail`), um pedido por `order_id`, dia = `date_created` |
| Shopee | Cancelamentos e taxa (sem "pedido não pago"), pedidos em devolução agora; motivos de cancelamento; tabela por marca | `silver.stg_shopee_orders` (`cancel_by`, `cancel_reason`, `order_status`), dia = `created_date_brt` |
| TikTok | `sem_fonte`, com motivo | — |

- Rota: `GET /api/v1/cx/atendimento?brands=&dias=7|30|90` (e `GET /api/v1/cx/shopee`, aba CX do menu principal desde 04/11/2026), atrás da flag
  `PERFORMANCE_CONTAS_ATENDIMENTO_API_ENABLED` (API) e `NEXT_PUBLIC_PERFORMANCE_CONTAS_ATENDIMENTO_ENABLED`
  (web). As duas nascem desligadas; desligada, `?aba=atendimento` é ignorado e a rota responde 404.
- A aba é `?aba=atendimento` em `/performance-contas`, por link (sem JS), mantendo canal e marca.
- A fila "Precisa de ação", o motivo das reclamações, perguntas e devoluções com motivo aparecem como
  "Ainda não publicado", com o motivo de cada um.
- **Pré-requisito para ligar:** o usuário do Data Mart da API precisa de `SELECT` em `api.vw_ml_orders` e
  `silver.stg_shopee_orders` (hoje a API só lê `gold.*`). Sem o grant, cada bloco sai como `erro`, nunca zero.
- Medido em 03/11/2026 (30 dias, 04/10 a 02/11): ML 2.343 cancelamentos em 58.666 pedidos (4,0%);
  Shopee 1.704 cancelamentos em 29.290 pedidos (5,8%), fora 3.854 não pagos.
- **Reclamação do ML ficou de fora de propósito.** Contar pedidos com `mediations` dá Kokeshi 2,5% contra
  0,61% na reputação oficial do mesmo período: o campo inclui o cancelamento pedido pelo comprador e não
  aplica as exclusões do ML. A taxa oficial vem do snapshot de reputação (aba Performance, CX-MKT-2).

## 8. Arquitetura e fases

Mesmo caminho da Expedição: o Airflow (`goca-airflow`) chama as APIs e grava no Data Mart; um pipeline deste
repo publica no Neon; a API e a web leem do Neon.

| Fase | Entrega | Repo | Exige ação humana |
|---|---|---|---|
| 0 | Probes inertes de capacidade: `mercadolivre_cx_capability_probe`, `tiktok_cx_capability_probe`, `shopee_cx_capability_probe` | goca-airflow | merge, deploy e disparo manual de cada probe; se faltar permissão, pedir no painel de desenvolvedor do ML / Partner Center do TikTok |
| 1 | Ingestão incremental: `raw.ml_cx_claims`, `raw.ml_cx_claim_reasons`, `raw.ml_cx_returns`, `raw.ml_cx_questions`, `raw.ml_cx_account_daily`, `raw.tiktok_cx_returns`, `raw.tiktok_cx_cancellations`, `raw.shopee_cx_returns` (cancelamentos da Shopee já vêm de `silver.stg_shopee_orders`) (a cada 2 h, janela móvel por `last_updated`/`update_time`) | goca-airflow | migrations, deploy, ligar a flag |
| 2 | `pipelines/cx/` normaliza para `cx_caso` e publica `marts.cx_casos` + `marts.cx_conta_dia` no Neon | mktplace | migration no Neon |
| 3 | `GET /api/v1/cx/*` + aba CX no menu principal (flag `cx_api_enabled`, ligada) | mktplace | flags no Render/Vercel |
| 4 | Enriquecimentos: afeta reputação (ML), avaliações (Shopee), chat (se liberado) | ambos | — |

## 9. Riscos

- **Permissão:** nenhum dos três apps teve a permissão de CX comprovada. O ML exige "Comunicação pré e
  pós-venda" (sem ela: `403 PA_UNAUTHORIZED_RESULT_FROM_POLICIES`); o chat do TikTok é escopo custom.
- **Token:** o refresh do ML e o da Shopee são de uso único. A ingestão nova nunca renova token fora do
  mecanismo oficial.
- **PII:** os payloads trazem nome, apelido e id do comprador, e texto livre. Nada disso é gravado: só ids da
  plataforma, códigos, datas e valores.
- **Shopee:** janela de 15 dias por consulta; o histórico inicial exige fatiar o backfill.
- **ML:** desde 02/02/2026 a mensageria pós-venda do MLB passa por um agente de IA do ML (`from.user_id`
  3037675074). Isso afeta só a leitura de mensagens, que está fora da fase 1.
- **Motivos em espanhol (ML):** o `detail` do motivo vem em espanhol; a tela usa o grupo (§5) e um rótulo em
  português mantido no código.

## 10. Pendências de negócio

1. Confirmar se existe conta ML da Ápice que deveria estar integrada.
2. Confirmar se a meta interna de "taxa de reclamação" existe (hoje: sem limiar, sem cor).

## 11. Referências oficiais

- ML: <https://developers.mercadolivre.com.br/pt_br/gerenciar-reclamacoes>,
  <https://developers.mercadolivre.com.br/pt_br/gerenciar-devolucoes>,
  <https://developers.mercadolivre.com.br/pt_br/gerenciamento-perguntas-respostas>,
  <https://developers.mercadolivre.com.br/pt_br/mensagens-pendentes>,
  <https://developers.mercadolivre.com.br/pt_br/permissoes-funcionais>
- TikTok: Search Returns <https://partner.tiktokshop.com/docv2/page/650ab69edefece02be70785b>,
  Search Cancellations <https://partner.tiktokshop.com/docv2/page/650ab68bc16ffe02b8f2ea41>,
  Get Return Records <https://partner.tiktokshop.com/docv2/page/650ab676f1fd3102b92e6027>,
  motivos BR <https://partner.tiktokshop.com/docv2/page/67e61d87fcf2dd04c982ef1d>,
  CS Performance <https://partner.tiktokshop.com/docv2/page/66bec10859f0b0030018e518>
- Shopee: `https://open.shopee.com/opservice/api/v1/doc/api/?version=2&api_name=v2.returns.get_return_list`
  (idem `get_return_detail`, `v2.order.get_order_detail`, `v2.product.get_comment`), Data Definition e App
  Types no guia do desenvolvedor.
