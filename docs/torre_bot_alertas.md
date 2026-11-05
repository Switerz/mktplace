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

## Testar sem mandar nada

Todas as rotas aceitam `?simular=true`: avaliam e devolvem a mensagem sem
enviar. Exigem o header `X-Alerta-Token` (o segredo `ALERTA_WEBHOOK_SECRET`).

## O que falta para chegar de verdade

* `SLACK_BOT_TOKEN` (`xoxb-…` do app "Torre de Marketplaces") no Render. Sem
  ele, as rotas respondem 503 "Envio nao configurado.".
* Os crons ligados no GoDeploy (tabela acima).
