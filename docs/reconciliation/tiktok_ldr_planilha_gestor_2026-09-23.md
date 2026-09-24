# Série de referência — planilha de atraso do TikTok Shop (gestão)

Proveniência e limites do arquivo
[`tiktok_ldr_planilha_gestor_2026-09-23.csv`](tiktok_ldr_planilha_gestor_2026-09-23.csv),
usado para reconciliar a LDR publicada pela Torre.

## De onde veio

**Captura de tela enviada pelo gestor**, recebida em **2026-09-24**. Não é um
export, não é uma consulta e não veio de nenhum sistema ao qual a Torre tenha
acesso: os números foram **transcritos à mão** da imagem.

Isso tem uma consequência que precisa ficar dita: **a referência não é
auditável por reexecução**. Se um número desta série estiver errado, o erro
vive aqui e a reconciliação vai concordar com ele. O que o artefato garante é
que a comparação seja *reproduzível* — não que a referência seja *correta*.

## Data da captura

A imagem **não declara** a data de extração. O que se sabe:

- a última linha é **23/09/2026**;
- a coluna "não postados dentro do prazo" só tem valor em 21, 22 e 23/09
  (12, 34 e 205), o que é o padrão de uma extração feita **em 23/09**: antes
  disso tudo já havia vencido.

Portanto **23/09/2026 é inferido, não declarado**. O nome do arquivo usa essa
data por conveniência de ordenação.

**Diferença temporal para a fotografia da Torre:** a Torre leu a fonte em
**2026-09-24, 15:35 BRT** (18:35 UTC) — cerca de **24 h depois**. Isso explica,
por construção, as divergências nas colunas de *pendente*: um pedido que estava
"não postado" no dia 23 pode ter sido coletado no dia 24 e migra de
`not_shipped_overdue` para `shipped_after_sla` do lado da Torre. Nenhuma
correção foi aplicada para compensar isso — a diferença é registrada, não
escondida.

## População

**Uma única marca: `barbours`.** Isso não estava escrito na imagem; foi
descoberto ao procurar qual população reproduz o total de 23.317 pedidos pagos
(a carteira inteira tem ~173 mil no mesmo período). O `paid_orders` desta série
bate com `barbours` em **21 dos 23 dias**.

## Colunas

| coluna | significado na planilha |
|---|---|
| `paid_date` | data de pagamento do pedido |
| `paid_orders` | total de pedidos pagos no dia |
| `shipped_within_sla` | postados em até 2 dias úteis |
| `shipped_after_sla` | postados após 2 dias úteis |
| `not_shipped_overdue` | ainda não postados e já em atraso |
| `not_shipped_on_time` | ainda não postados, dentro do prazo |
| `late_rate_pct_rounded` | "% de atraso por dia", **como exibido** |

`late_rate_pct_rounded` é o número **arredondado que a planilha mostra** (1%,
6%, 55%, 100%…). A planilha não expõe casas decimais, então a comparação com a
Torre tem resolução de 1 ponto percentual — e é por isso que o erro por dia
nunca poderá ser reportado com precisão maior que isso.

## O que NÃO está aqui

- Nenhum identificador de pedido, cliente, endereço, CPF ou rastreio. A série é
  agregada por dia e só contém contagens.
- Nada foi preenchido por dedução. Todas as 23 linhas estavam legíveis na
  captura; se alguma não estivesse, a linha ficaria de fora e isso estaria
  registrado aqui.
- O rodapé da imagem traz um `TOTAL` (23.317 / 15.258 / 7.668 / 140 / 251) e um
  campo "Média últimos 7 dias + Hoje" de **1,09%**. Nenhum dos dois foi
  transcrito para o CSV: são derivados das linhas, e guardá-los duplicado
  criaria duas fontes para o mesmo número. O script de reconciliação recalcula
  os totais a partir das linhas e confere contra estes valores.

## Como reproduzir a comparação

```
python -m pipelines.reconciliation.tiktok_ldr_planilha
```

Somente leitura, contra o Data Mart. O script recalcula a série da Torre sob as
três regras de prazo candidatas e imprime a diferença de cada uma contra esta
referência. Ver o próprio script para o que cada número significa.
