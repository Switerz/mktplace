# Handoff — ingerir o SLA de envio do TikTok Shop (`goca-se/airflow`)

> Emitido pelo gate EXP-TK-OPS-2 (Torre `Switerz/mktplace`, 2026-09-24).
> Prompt autocontido para a instância que cuida da ingestão do TikTok.

## Como refazer as medições deste documento

Todo número abaixo foi medido **em 2026-09-24**, contra a fonte real. Os que
descrevem cobertura e grão são reproduzíveis por um comando:

```
python -m pipelines.expedicao.tiktok_cli --diagnose
```

Ele imprime, sob `PROVENIÊNCIA` e `PREMISSAS DA TRANSFORMAÇÃO`, a cobertura de
`IN_TRANSIT`, o grão dos line items e a cobertura do carimbo de cancelamento.

**O comando reproduz a medição, não o valor.** Duas coisas mudam o número:

1. **a janela é móvel** — rodar amanhã lê outros dias;
2. **o tamanho da janela importa** — os valores citados abaixo foram medidos
   sobre pagamentos de **01 a 24/09** (equivalente a `--dias 30`). Com a janela
   padrão de 7 dias os mesmos indicadores dão outro número: a cobertura do
   carimbo de cancelamento, por exemplo, sai 73,1% em vez de 78,4%, porque
   cancelamento recente ainda não foi detectado pelo poller.

Os valores aqui são a fotografia de 2026-09-24 na janela declarada. Servem para
dimensionar o problema, não como constante.

Os números de reconciliação com a planilha da gestão vêm de outro artefato,
esse sim versionado: `docs/reconciliation/` +
`python -m pipelines.reconciliation.tiktok_ldr_planilha`.

## Por que isto é necessário

A Torre passou a publicar a **LDR (Late Dispatch Rate)** do TikTok Shop em
`/expedicao`. A métrica está no ar e reconcilia com a planilha da gestão, mas o
prazo de cada pedido é **reconstruído** de uma política de dias úteis — não é o
SLA que a plataforma usa para penalizar.

Enquanto for assim, a tela é obrigada a exibir o aviso
`deadline_is_reconstructed` e a Torre **não pode afirmar** que mede a
penalização do TikTok. É o único bloqueio real para a métrica virar autoridade.

## O que falta ingerir

### 1. SLA por pedido — **prioridade máxima**

A API `get_order_list` v202309 devolve o prazo de envio por pedido
(`rts_sla_time` e campos irmãos, conforme a versão). O
`ORDER_COLUMN_MAPPING` em `src/tiktok/config/constants.py` **não mapeia nenhum
deles** — verificado por busca direta, zero ocorrências para
`rts_sla|tts_sla|shipping_due|collection_due|delivery_due|sla_time|due_time`.

Pedido: mapear os campos de SLA que a resposta realmente traz, com uma coluna
por campo em `raw.tiktok_shop_orders`.

**Não inventar o nome.** Antes de mapear, registre num probe o conjunto de
chaves que a resposta devolve para uma amostra de pedidos BR, e mapeie só o que
existir. Se nenhum campo de SLA vier na conta BR, isso também é um resultado —
e precisa voltar documentado, porque muda a decisão do lado da Torre.

### 2. Carimbo de cancelamento

Hoje o instante do cancelamento só existe via `raw.tiktok_shop_order_status_log`
(`new_status = 'CANCELLED'`), e a cobertura é **78,4%**: de 4.996 pedidos pagos
e cancelados em 30 dias, 3.918 têm linha no log.

Isso importa porque a regra da LDR é:

- cancelado **antes** do vencimento → sai do denominador;
- cancelado **depois** → permanece (havia obrigação de enviar e não foi
  cumprida).

Com 21,6% sem carimbo, a Torre optou por **mantê-los no denominador** — a
escolha conservadora. O impacto foi medido: a taxa de coleta varia entre 26,16%
e 27,02% conforme a regra (0,86 pp). Um campo de cancelamento na tabela de
pedidos elimina a ambiguidade.

### 3. Evento nativo de coleta (TTS)

O instante de coleta é derivado do log de status (`IN_TRANSIT`), que é o que o
**nosso poller** viu. A cobertura é boa — 99,32%, com o poller detectando a cada
~4 min — mas é uma derivação, não um carimbo da plataforma.

Se a API expõe um `collection_time` (ou equivalente) por pedido ou por pacote,
ingeri-lo tornaria a medição direta. Baixa prioridade: a derivação atual está
medida e funciona.

## O que **não** mudar

- **`rts_time` em `raw.tiktok_shop_line_items` está correto e é essencial.**
  Medido em 175.925 pedidos: preenchido em 100,0% dos pedidos em
  `AWAITING_COLLECTION`/`IN_TRANSIT`/`DELIVERED`/`COMPLETED` e em 0,0% dos que
  estão em `AWAITING_SHIPMENT`/`ON_HOLD`. É um discriminador perfeito do
  despacho, e coincide com a transição para `AWAITING_COLLECTION` (mediana de
  diferença 0,00 h).
- **O log de status continua necessário.** É a única fonte do instante de
  coleta e de cancelamento hoje.
- Nada precisa ser reprocessado historicamente por causa deste handoff.

## Como a Torre consome (para você saber o que não quebrar)

`marts.expedicao_tiktok_dispatch_daily`, grão (data de pagamento, marca),
publicada por `python -m pipelines.expedicao.tiktok_cli --apply` no repo
`Switerz/mktplace`. Ela lê, da Raw:

| tabela | colunas |
|---|---|
| `raw.tiktok_shop_orders` | `order_id`, `brand`, `shop_name`, `paid_at`, `order_status`, `is_sample_order`, `extracted_at` |
| `raw.tiktok_shop_line_items` | `order_id`, `rts_time` |
| `raw.tiktok_shop_order_status_log` | `order_id`, `new_status`, `updated_at_tiktok`, `detected_at` |

Observações que afetam a ingestão:

- **Fuso**: `paid_at`, `created_at`, `updated_at_tiktok` e `rts_time` estão em
  **UTC−3**; `extracted_at` e `detected_at` estão em **UTC**. Medido por
  ancoragem em `now()`: 3,01 h nas quatro primeiras e 0,00 h nas duas últimas.
  Se algum dia isso mudar, a LDR muda junto e ninguém percebe.
- **`order_id` é globalmente único** (`uk_tiktok_orders`), e a Torre depende
  disso para juntar as três tabelas por `order_id` sozinho. Perder essa
  unicidade degrada a consulta de segundos para minutos.
- **O log pula transições**: medido, 238.954 pedidos foram de `UNPAID` direto
  para `AWAITING_COLLECTION`, contra 202.437 que passaram por
  `AWAITING_SHIPMENT`. A Torre já trata isso — não é um defeito a corrigir,
  mas é a razão de `rts_time` ser preferido ao log para o despacho.

## Entregável esperado

PR focal em `goca-se/airflow` com:

1. o probe das chaves realmente devolvidas pela API para pedidos BR (resultado
   documentado, mesmo que negativo);
2. o mapeamento dos campos de SLA que existirem, com DDL correspondente;
3. o carimbo de cancelamento, se a API o expuser;
4. um relatório dizendo o que foi ingerido, o que não existe na conta BR, e a
   cobertura medida de cada campo novo.

Sem backfill histórico e sem alterar o que já funciona.
