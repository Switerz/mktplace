# Bot "Torre" no Slack — mapa do que ele avisa

Bot: app Slack **Torre de Marketplaces** (`infra/slack/torre-bot`), sempre no
privado de quem está em `EXPEDICAO_ALERTA_DESTINATARIOS` (e-mails do Slack,
separados por vírgula, no Render).

Tudo roda na API (`apps/api`), chamado por crons do app
`torre-alerta-gatilho` no GoDeploy. O cron só repassa; a regra mora na API.
Nenhuma mensagem se repete: cada uma leva em `metadata` a chave de cada item,
e o envio pula quem já recebeu aquela chave (ver `app/services/slack_dm.py`).

## Visão geral

| # | O quê | Quando chega | Rota | Cron (GoDeploy, UTC) |
|---|---|---|---|---|
| A1 | Vencidos da Shopee acima de 3% da fila | na hora; lembra às 9h, 13h e 17h; avisa quando volta | `POST /api/v1/alertas/expedicao-vencidos` | `*/15 * * * *` |
| A2 | LDR acima do teto (qualquer marca × canal) | na hora, no máximo 1 vez por dia por conta | `POST /api/v1/alertas/criticos` | `*/15 * * * *` |
| A3 | Kokeshi: reputação oficial do Mercado Livre crítica | na hora, 1 vez por fotografia diária | `POST /api/v1/alertas/criticos` | `*/15 * * * *` |
| A4 | TikTok: pedidos vencidos parados acima de 3% dos pendentes | na hora; de novo às 9h, 13h e 17h enquanto continuar acima | `POST /api/v1/alertas/criticos` | `*/15 * * * *` |
| A5 | Vencem hoje: quantos pedidos têm prazo hoje, por canal, etapa e marca | todo dia às 9h | `POST /api/v1/alertas/expedicao-dia?momento=manha` | `0 12 * * *` |
| A6 | Corte da coleta: o que vence hoje e ainda não saiu | todo dia às 15h30 | `POST /api/v1/alertas/expedicao-dia?momento=corte` | `30 18 * * *` |
| R1 | Report diário: GMV de ontem, avanços e problemas | todo dia às 8h (Brasília) | `POST /api/v1/alertas/report-diario` | `0 11 * * *` |

Horário de Brasília = UTC−3 (sem horário de verão).

## A1 — Vencidos da Shopee (já existia)

Regra em `app/services/alerta_vencidos.py`: pedidos vencidos ÷ fila da Shopee,
acima de 3%. Avisa ao entrar, lembra às 9h, 13h e 17h enquanto continuar
acima e avisa uma vez quando volta.

## A2 — LDR acima do teto

A mesma faixa "LDR · últimos 7 dias" do topo da Expedição, com as mesmas fontes
e a mesma meta/teto (confirmados em 04/11/2026):

| Canal | Fonte | Meta | Teto |
|---|---|---|---|
| Shopee | `expedicao_shopee_ldr_service.obter_ldr` (reserva: `shopee_ldr_dm_service`) | 2% | 5% |
| Mercado Livre | `ml_ldr_service.obter_ldr_ml` | 3% | 6% |
| TikTok Shop | `expedicao_tiktok_service.obter_serie` (coleta) | 2% | 4% |

* **Crítico (DM na hora):** taxa **acima do teto** e base de pelo menos 30
  pedidos. Base menor pode dar um percentual alto sem significar nada.
* **Atenção (só no report):** entre a meta e o teto.
* Chave: `ldr:<canal>:<marca>:<dia>`. A DM sai no máximo uma vez por dia por
  conta enquanto ela continuar acima do teto.
* Fonte fora do ar = o canal fica de fora, nunca vira 0%.

## A3 — Kokeshi: performance oficial

Marcas observadas: `MARCAS_SAUDE = ("kokeshi",)` em
`app/services/torre_alertas.py` (para incluir outra marca, basta acrescentar).
Fonte: a fotografia diária da reputação do Mercado Livre
(`performance_oficial_service`, a mesma da seção "Performance").

* **Crítico (DM na hora):**
  * uma métrica oficial passou do limite MercadoLíder (reclamações 1%,
    mediações 0,5%, canceladas 0,5%, envios com atraso 6%);
  * o termômetro saiu do verde (amarelo, laranja ou vermelho);
  * a conta perdeu o MercadoLíder.
* **Atenção (só no report):** métrica em 80% do limite ou mais.
* Chave: `saude:<canal>:<marca>:<sinal>:<dia da fotografia>`.

A LDR da Kokeshi (ML e TikTok) já entra pela A2. A queda de GMV da Kokeshi
entra no report (R1), porque o dia de ontem só fecha de manhã.

## A4 — Vencidos parados no TikTok

O mesmo raciocínio do A1, para o TikTok Shop. Que parte do que está parado já
passou do prazo de coleta? O TikTok não tem fila fotografada como a Shopee, então
a conta sai da série diária (`marts.expedicao_tiktok_dispatch_daily`), com os
mesmos recortes da tela `/expedicao/tiktok`:

* **vencidos:** pendentes de coleta vencidos dos vencimentos maduros dos últimos
  7 dias. É o mesmo número de "N pedido(s) passaram do prazo e ainda não foram
  registrados";
* **parados:** os vencidos mais os pendentes ainda no prazo.

* **Crítico (DM):** vencidos acima de **3%** dos parados (o mesmo limite do A1),
  com base de pelo menos 30 pedidos. A DM lista as marcas acima do limite.
* Publicação velha (a mesma regra `fotografia_velha` da tela) não avisa.
* Chave: `vencidos:tiktokshop:<dia>:<turno>`, com turno `00`, `09`, `13` ou `17`
  (Brasília). Enquanto continuar acima, avisa no máximo 4 vezes por dia.
* O prazo é **reconstruído** pela regra de 2 dias úteis, não é o SLA oficial do
  TikTok. A DM diz isso.
* **Mercado Livre fica de fora** enquanto a trava `expedicao_ml_prazo_enabled`
  estiver desligada. Hoje a fotografia do ML marca como vencido o envio que já
  foi coletado, e o alerta sairia falso.

## A5 e A6 — Vencem hoje e corte da coleta

Regra em `app/services/torre_expedicao_dia.py`. Serve para o time começar o dia
pelo que vence hoje e, no fim da tarde, cobrar a coleta antes do prazo.

* **Shopee e Mercado Livre:** fila do lote servido (`marts.expedicao_fila_atual`),
  com o dia do prazo em Brasília e a etapa publicada. O ML só entra quando a tela
  mostra o prazo dele (`expedicao_service.prazo_publicado`).
* **TikTok Shop:** pendentes de **coleta** com vencimento hoje, da série diária
  (o prazo é reconstruído e não há etapa).
* **A5 (9h):** total que vence hoje por canal, separado em:
  * :red_circle: **ainda não prontos**: a preparar, a faturar, a imprimir, em separação;
  * :truck: **aguardando coleta**.

  Vêm também as marcas e, em nota, os travados, os já vencidos e quantos vencem
  no próximo dia de prazo.
* **A6 (15h30):** o que vence hoje e ainda não saiu: primeiro o que nem está
  pronto, depois o que aguarda coleta, por marca.
* Fotografia com mais de 3 h tira o canal da mensagem, com uma nota dizendo
  qual ficou de fora. Se nada vence hoje, não manda nada.
* Chave: `expedicao-dia:<manha|corte>:<dia>`, uma DM por pessoa por momento.
* O botão abre `/expedicao?channel=<canal>&visao=detalhe` (com `&marca=` quando
  é uma marca só).

## Outras visões da Expedição que valem alerta (ainda não implementadas)

| Visão | Onde está | Por que ajuda | Ideia de alerta |
|---|---|---|---|
| Travados (`is_stalled`) | fila, `stalled_count` | pedido parado sem andar de etapa | nota diária com o total por marca; DM quando subir muito de um dia para o outro |
| Abertos há mais de 48 h (`over_48h`) | fila, `over_48h_count` | atraso que ainda não virou vencido | entrar no A5 como linha própria |
| Zumbis (`is_source_zombie`) | fila, `zombie_count` | pedido que a fonte parou de atualizar | aviso ao time de dados, não à operação |
| Fotografia atrasada | `source_freshness_status`, `effective_at` | a tela mostra número velho | DM quando a fila não atualiza há mais de 2 h em horário comercial |
| Vence amanhã (`due_next_day`) | fila | dá para adiantar hoje | já entra como nota no A5 e no A6 |
| Link filtrado por situação | tela | hoje o link abre só canal, marca e detalhe | a tela passar a ler `?situacao=` da URL, para o botão abrir já em "vence hoje" |

## R1 — Report diário das 8h

Uma mensagem por dia (chave `report:<dia>`), sobre **ontem** (dia fechado,
Brasília):

1. **Linha de abertura:** "Ontem (qui, 05/11): R$ X de GMV nas marcas,
   N avanços, M problemas."
2. **GMV por marca:** total dos canais, % da meta do dia e variação contra o
   mesmo dia da semana anterior.
3. **Mês até ontem:** realizado ÷ meta acumulada até ontem (ritmo) e % da
   meta do mês.
4. **Avanços:**
   * marca que bateu a meta do dia (≥ 100%);
   * marca que cresceu mais de 15% contra o mesmo dia da semana anterior
     (piso de R$ 10 mil na base);
   * mês no ritmo da meta (≥ 100%);
   * LDR: contas na meta (N de M);
   * Kokeshi com reputação no verde e tudo dentro do limite.
5. **Problemas:**
   * 🔴 LDR acima do teto; ⚠️ LDR entre a meta e o teto;
   * 🔴/⚠️ sinais da Kokeshi (A3);
   * 🔴 marca abaixo de 70% da meta do dia; ⚠️ entre 70% e 80%;
   * ⚠️ queda de mais de 20% contra o mesmo dia da semana anterior (piso de
     R$ 10 mil);
   * ⚠️ mês abaixo de 90% do ritmo da meta;
   * ⚠️ canal sem GMV ontem (carga atrasada).

Meta do dia = meta mensal da aba Metas × peso do dia na curva do canal.
A curva é a mesma de `apps/web/src/lib/metas-curva.ts`: nov/26 para ML e
Shopee; sem curva, todos os dias pesam igual. Só os canais com meta entram no
"% da meta". O GMV vem de `marts.fact_marketplace_daily_performance`, a mesma
tabela da Gerencial.

## Formato das mensagens

As DMs usam blocos do Slack (Block Kit, montados em `app/services/slack_blocks.py`):

* **título** curto e uma linha de resumo. O campo `text` da mensagem é esse
  resumo, que é o que aparece na notificação;
* **um grupo por canal**, com 🔴 por marca e números curtos (`%` e
  "atrasados de total");
* **um botão em cada grupo** que abre a Torre já filtrada:

| Grupo | Link |
|---|---|
| LDR | `/expedicao?channel=<canal>` (+ `&marca=<marca>` quando é uma só) |
| Vencidos | `/expedicao?channel=<canal>&visao=detalhe` |
| Performance oficial | `/performance-contas?canal=<shopee\|mercado_livre\|tiktok>&brands=<marca>` |
| Report | `/metas`; atalhos Metas, Expedição e Performance no fim |

* **notas** curtas em `context` (prazo reconstruído, definição da LDR, canais
  com meta);
* **report:** números do dia em duas colunas e uma bola por marca, conforme o
  % da meta do dia: 🟢 ≥100%, 🟡 ≥80%, 🟠 ≥70%, 🔴 <70%.

`?simular=true` devolve `mensagem` (o resumo) e `blocos` (o JSON). Para ver o
desenho, cole os blocos no Block Kit Builder (app.slack.com/block-kit-builder).

## Testar sem mandar nada

Todas as rotas aceitam `?simular=true`: avaliam e devolvem a mensagem sem
enviar. Exigem o header `X-Alerta-Token` (o segredo `ALERTA_WEBHOOK_SECRET`).

## No ar (05/11/2026)

* `SLACK_BOT_TOKEN` (bot "torre", app `A0CDDSL102W`) está no Render. Sem ele, as
  rotas respondem 503 "Envio nao configurado.".
* Os crons estão ligados no GoDeploy `04cc2f20` (tabela acima). Para diagnosticar,
  use os logs do app: a linha `api=<status> enviados=… ja_tinham=…`.
