# SHOPEE-API-PRODUTOS-2 — troca de fonte da tela de produtos

**Medição:** 2026-09-25 · **Estado:** `READY_FOR_SHOPEE_PRODUCTS_API_CUTOVER`
**Natureza desta entrega:** código + reconciliação. Nenhuma marca foi ligada.

---

## 1. Procedência

| conta | fonte disponível | modo ao final desta entrega |
|---|---|---|
| apice | API + export | `manual_export` |
| barbours | API + export | `manual_export` |
| lescent | API + export | `manual_export` |
| rituaria | API + export | `manual_export` |
| **kokeshi** | **só export** | `manual_export` (permanente) |

Kokeshi é a única ausente da API, e a causa é **app inexistente**: o app Shopee
é registrado como *Seller In House System* no console da empresa dona de cada
loja, então cada marca tem `partner_id`/`partner_key` próprios. Não é
credencial faltando nem scope insuficiente.

---

## 2. Arquitetura da procedência

`marts.fact_shopee_product_monthly` passa a guardar **as duas fontes lado a
lado**, distinguidas por `source`. Quem decide qual delas a tela lê é
`marts.shopee_product_source_mode`, **marca a marca**.

```
gold.shopee_product_daily (Data Mart)          ← API oficial, 4 contas
        │  gmv_completed / units_completed / orders_completed
        ▼
pipelines/sync_shopee_produtos_api.py          ← agrega dia → competência
        │  source='api', is_partial, source_captured_at
        ▼
marts.fact_shopee_product_monthly (Neon)  ←──  source='manual_export' (export XLSX)
        │
        └── LEFT JOIN marts.shopee_product_source_mode
            WHERE f.source = COALESCE(m.source, 'manual_export')
        ▼
                              /produtos/shopee
```

**Três propriedades que isto compra:**

1. **As duas nunca se somam.** A leitura casa `fact.source = mode.source` por
   marca, então para cada (marca, competência) exatamente uma procedência entra
   no resultado.
2. **Rollback é instantâneo.** Voltar uma marca ao export é um `UPDATE` de uma
   linha. Não há carga, não há `DELETE`, não há deploy — porque as linhas do
   export continuam intactas na tabela.
3. **Fail-closed.** `COALESCE(m.source, 'manual_export')`: marca ausente da
   tabela de modo lê o export, que é o comportamento de hoje. Marca nova nunca
   nasce lendo uma fonte que ninguém ligou.

O `DELETE` do publisher é escopado em `source = 'api'` — nunca toca linha do
export, nem das quatro contas, nem da kokeshi.

---

## 3. Reconciliação

### 3.1 Não há perda de pedido — zero, e está provado

Julho/2026, todo pedido do export procurado na API:

| marca | pedidos no export | ausentes na janela | **ausentes de fato** |
|---|---|---|---|
| apice | 5.100 | 15 (0,294%) | **0** |
| barbours | 14.202 | 70 (0,493%) | **0** |
| lescent | 5.671 | 25 (0,441%) | **0** |
| rituaria | 4.294 | 29 (0,675%) | **0** |

Os **139** pedidos que não caem na janela de julho da API existem todos na API
— com deslocamento de **exatamente 1 dia**, nos 139 casos, sempre na mesma
direção (o export um dia à frente).

**Classificação: fronteira de dia.** É o export medindo em UTC e a API em BRT:
compra às 22h BRT é 01h UTC do dia seguinte. A API está *mais* correta no eixo
do dia. Efeito colateral honesto: na virada do mês, esses pedidos mudam de
competência.

### 3.2 GMV e unidades, por competência

API (recorte `completed`) × Torre (manual, `Concluído`):

| competência | marca | GMV API | GMV Torre | Δ | un. API | un. Torre |
|---|---|---|---|---|---|---|
| julho | apice | 380.538,61 | 260.769,09 | **+45,93%** | 5.217 | 3.636 |
| julho | barbours | 1.062.992,21 | 741.222,83 | **+43,41%** | 12.526 | 8.824 |
| julho | lescent | 272.458,84 | 176.636,52 | **+54,25%** | 4.830 | 3.115 |
| julho | rituaria | 443.895,59 | 321.342,95 | **+38,14%** | 4.238 | 3.095 |
| agosto | apice | 274.758,72 | **0,00** | — | 3.678 | 0 |
| agosto | barbours | 943.449,03 | **0,00** | — | 12.501 | 0 |
| agosto | lescent | 320.435,69 | **0,00** | — | 5.852 | 0 |
| agosto | rituaria | 412.961,42 | **0,00** | — | 4.081 | 0 |
| setembro (D−1) | apice | 170.125,91 | inexistente | — | 2.411 | — |
| setembro (D−1) | barbours | 649.028,81 | inexistente | — | 8.882 | — |
| setembro (D−1) | lescent | 188.251,87 | inexistente | — | 3.328 | — |
| setembro (D−1) | rituaria | 302.824,06 | inexistente | — | 3.048 | — |

### 3.3 A divergência é maturação, e não definição

As duas fontes usam **o mesmo conceito** (`Concluído` ↔ `completed`). O que
difere é o instante da medição. Cruzamento status × status, pedido a pedido
(barbours, julho, 11.595 pedidos): tudo o que não foi cancelado amadureceu para
`completed` — `Entregue` (1.718), "pode pedir devolução" (1.076), `Enviado`
(538), `Pedido Recebido` (24), `A Enviar` (18).

O export congelou julho antes de os pedidos concluírem. Os 3.354 que
concluíram depois valem os ~30% que faltam na Torre.

---

## 4. 🔴 A correção histórica esperada

**Ligar uma marca ELEVA o faturamento histórico dela por SKU em +38% a +54%.**

Isso é **correção de um número velho, não inflação**: os pedidos concluíram de
verdade e estão na fonte oficial da Shopee. Mas é mudança visível de série, e
**precisa ser comunicada antes do cutover** — não pode aparecer como surpresa.

Agosto é o caso extremo: a Torre mostra **R$ 0,00 nas 5 marcas** porque o export
foi tirado em 25/08, um dia depois do fim da janela, quando quase nada tinha
amadurecido (agosto 01–24 no export: R$ 555,83 concluídos na barbours, R$ 0,00
na apice). Ligar a API devolve R$ 274 mil a R$ 943 mil por marca a um mês que
hoje aparece vazio.

---

## 5. Mês corrente

`is_partial = true` marca a competência ainda aberta. Medido em 25/09/2026:

| mês | `completed / is_sale` |
|---|---|
| julho | 100,0% |
| agosto | 100,0% (lescent 99,98%) |
| **setembro** | **70,6% a 79,1%** |

Em mês fechado as duas leituras coincidem. No mês corrente, 21–29% está em
trânsito. Sem a marca de parcialidade, a única leitura possível de um mês
corrente baixo seria "as vendas caíram" — que é falso.

A janela do publisher é de **3 competências recalculadas por inteiro**, nunca só
a corrente: publicar só o mês corrente congelaria cada mês no valor imaturo que
ele tinha ao virar, que é exatamente o defeito do export.

---

## 6. O que a fonte API não entrega

`canceled_orders`, `cancel_rate_pct` e `unique_buyers` ficam **NULL** nas linhas
`api`:

- o gold filtra `is_sale` na origem, então pedido cancelado não chega a ele;
- `buyer_user_id` da API é pseudônimo por loja, não serve como comprador único;
- `cancel_rate_pct` deriva das duas primeiras.

🔴 **NULL, nunca zero.** Zero afirmaria "nenhum pedido cancelado", que não foi
medido. `gold_service` foi corrigido junto: ele convertia NULL em `0` por causa
de um `_float(None) → 0.0`, e isso teria feito a ausência virar um fato.

---

## 7. Cutover — a ordem, e o que bloqueia

| # | passo | estado |
|---|---|---|
| 1 | mesclar `goca-se/airflow#1698` (recorte no gold) | ⏳ aberto |
| 2 | rodar o dbt: `gold.shopee_product_daily` ganha as 3 colunas | ⏳ depende de 1 |
| 3 | aplicar a migration `022` no Neon | ⏳ depende de 2 |
| 4 | `--apply` em shadow (todas as marcas ainda em `manual_export`) | ⏳ |
| 5 | reconciliar publicado × recomputado | ⏳ |
| 6 | ligar **uma** marca: `UPDATE ... SET source='api' WHERE brand='rituaria'` | ⏳ |
| 7 | validar API e tela | ⏳ |
| 8 | ligar as outras três | ⏳ |

🔴 **Bloqueio duro medido em 25/09:** `gold.shopee_product_daily` **ainda não
tem** `gmv_completed`, `units_completed` nem `orders_completed` — o PR #1698 não
foi mesclado e o dbt não rodou. O publisher lê essas colunas, então o passo 4
falha até o passo 2 existir. Nada disso é contornável por código daqui.

**Sugestão de primeira marca: `rituaria`** — é a de menor GMV entre as quatro
(R$ 443 mil em julho), então um erro custa menos, e mesmo assim exercita o
caminho inteiro.

### Rollback

```sql
UPDATE marts.shopee_product_source_mode
   SET source = 'manual_export', updated_at = NOW(),
       note = 'rollback do cutover'
 WHERE brand = '<marca>';
```

Uma linha, efeito imediato na próxima leitura, e as linhas do export continuam
onde estavam. Não há restauração de backup no caminho.
