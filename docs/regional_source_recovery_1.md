# REGIONAL-SOURCE-RECOVERY-1 — fonte Shopee removida da visão regional

Medido em 16/10/2026, somente leitura no Data Mart (réplica). Nenhuma escrita
no Data Mart ou no Neon, nenhum deploy, nenhum export manual novo.

## Veredito

| Canal | Situação | O que este gate faz |
|---|---|---|
| **ML** | Gold parada em **28/09** por efeito colateral, sem defeito na própria fonte | **Recuperável.** O PR tira a Shopee do caminho crítico; o dry-run mostra 1.629 linhas de grão prontas (29/09–15/10) |
| **Shopee** | Gold parada em **24/08** (fim dos exports). A fonte foi arquivada em 29/09 | **Sem substituto equivalente.** Nenhuma fonte atual tem UF, município ou CEP do destinatário |
| **TikTok** | Sem cobertura regional desde sempre | Nada muda |

A tela não está "parada em 28/09" por um único motivo. São duas paradas
diferentes que aparecem juntas:

1. **Shopee, 24/08.** A Gold regional da Shopee sempre dependeu do export
   manual do Seller Center. O último arquivo cobre pedidos até 24/08 23:59
   (carga em 25/08 16:30). O arquivamento só tornou visível uma parada que já
   existia.
2. **ML, 28/09.** Em 29/09 19:37 UTC, `silver.stg_shopee_order_item_snapshots`
   foi movida para `archive.silver__stg_shopee_order_item_snapshots`
   (`archive._archive_log`, id 149, lote 2026-09-29, `destino_final=arquivar`).
   O `--incremental` consulta a fonte Shopee dentro da mesma transação do ML.
   A partir daí, toda execução sai com exit 4:
   `relation "silver.stg_shopee_order_item_snapshots" does not exist`. Os logs
   do runner registram isso em 03/10, 09/10, 13/10 e 16/10. A última execução
   com sucesso foi em 29/09 11:13.

## 1. Contrato atual da visão regional

Fonte: `pipelines/ingestion/gold_regional/loader.py`, serving
`marts.fact_marketplace_region_daily` (Neon), API
`apps/api/app/services/regioes_service.py`.

- **Grão:** `date × marketplace_id × loja_id × uf`. UF é uma das 27 siglas ou
  `XX`, que significa "não identificada" e nunca é NULL. A chave é UNIQUE.
- **Data:** data de criação do pedido. Para a Shopee é
  `order_created_at::date` (naive, já em BRT). Para o ML é
  `date_created::date`. O teto é D-1 em America/Sao_Paulo.
- **Marcas:** apice=1, barbours=2, kokeshi=3, lescent=4, rituaria=5.
- **Canais:** ML (2) e Shopee (3). TikTok (1) nunca tem linha, e a API o lista
  em `channels_sem_cobertura_regional`.

| Métrica | Shopee (export) | ML |
|---|---|---|
| `gmv` | `order_amount` com status `NOT ILIKE '%cancel%'` | `total_amount` com `status='paid'` |
| `orders` | pedidos não cancelados | pedidos `paid` |
| `units_sold` | `SUM(quantity)` dos não cancelados | sempre 0 (ausência, não zero) |
| `canceled_orders` | status `ILIKE '%cancel%'` | `status='cancelled'` |
| `returned_orders` | `return_refund_status IS NOT NULL` | sempre 0 |
| UF | `delivery_state` (nome) → sigla por mapa fixo | `raw.ml_shipments.receiver_state` sem `BR-` |
| `uf_eligible_orders` | **todos** os pedidos, cancelados incluídos | só os `paid` |
| frete | `buyer_paid`, `estimated` e `reverse` (o do vendedor fica NULL) | `sender_cost` resolvido |

A Shopee é deduplicada pelo `file_id` vencedor por `(brand, order_id)`, com
JOIN de volta para trazer todas as linhas de SKU.

## 2. Fontes atuais procuradas

| Fonte | Endereço? | Casa com pedido Shopee? | Veredito |
|---|---|---|---|
| `raw.shopee_orders.recipient_address` (API, airflow) | 9 chaves (state, city, zipcode, district, town, region, name, phone, full_address) | sim, é o próprio pedido | **100% `****`** nos 289.011 pedidos, nas 4 contas e nos 8 status, inclusive `READY_TO_SHIP` e `PROCESSED`. A máscara vem da plataforma: o repo airflow mediu o mesmo em 28/08 (`helpers.to_txt_desmascarado`) |
| `silver.stg_shopee_orders` / `stg_shopee_order_items` | nenhuma coluna geográfica | — | sem UF |
| `raw.shopee_orders.package_list` | só logística (canal, transportadora, peso) | — | sem UF |
| `raw.shopee_orders.invoice_data.access_key` (NF-e) | só a UF do **emitente** (35/31) | presente em ~85% dos pedidos de set | não traz o destinatário |
| `silver.intelipost_orders` | UF/cidade/CEP | **0** por `order_sn` e **0** por chave de NF-e (01–14/09) | não tem pedido Shopee |
| `gold.fct_gobeaute_prazo_pedidos` | UF/CEP | nenhum `numero_pedido` no formato de `order_sn` | não tem pedido Shopee |
| `raw.tlp_pedidos` | UF/CEP | 0, e só tem Denavita | descartado |
| `gold.tiny_orders_all_brands` | UF/CEP | ERP sem carga desde mai/2026 | descartado |
| `archive.silver__stg_shopee_order_item_snapshots` | `delivery_state`, `delivery_city` | é a fonte antiga | congelada em 24/08, sem atualização possível sem export manual |

## 3. Existe substituto com UF, município ou CEP?

**Não.** Nenhuma tabela do Data Mart liga um pedido Shopee posterior a 24/08 a
um endereço de destino.

## 4. Fonte antiga × candidata (API) em 01/01–24/08

A UF não tem como ser comparada porque a candidata não a possui. Nas métricas
não geográficas, contra a fonte antiga deduplicada:

- **Pedidos válidos** (API `is_sale` × export não cancelado): **exatos** de
  jan a abr nas 4 marcas. Ex.: Barbour's 3.694 / 11.112 / 30.500 / 27.255 e
  Ápice 626 / 1.008 / 2.564 / 5.475. Em mai–jun a diferença vai de 1 a 3
  pedidos. Em jul a diferença maior vem do status congelado no snapshot do
  export (ex.: Ápice 4.312 × 4.341).
- **GMV:** `total_amount` da API fica **0,02% a 11,7% abaixo** do
  `order_amount` do export (Lescent jan −0,02%, Rituaria jan −2,8%, Ápice abr
  −10,0%, Barbour's abr −11,7%). São definições diferentes, não perda. Trocar
  a fonte muda o número de GMV da tela e exige ratificação.
- **Cancelamento:** o silver da API usa status minúsculo (`cancelled`). Um
  filtro `= 'CANCELLED'` devolve zero cancelados e conta todos os pedidos como
  válidos. Barbour's mar: 36.919 = 30.500 + 6.419.
- **Kokeshi:** é a maior marca Shopee da Gold (408.027 pedidos em jan–ago) e
  **não tem conta na API**. Não há app registrado. Qualquer substituto via API
  perde a Kokeshi inteira.

## 5. Qualidade

- **Duplicidade:** 0 chaves duplicadas em `gold.fct_marketplace_region_daily`.
  A API tem 1 linha por `order_sn` nas 4 contas.
- **Endereço ausente:** a fonte antiga tinha 0 pedidos sem UF em jan–ago. Na
  API, 100% estão mascarados.
- **Pedido sem região:** na Shopee antiga, `XX` = 0. No ML, Barbour's tem
  65.609 de 163.942 pedidos em `XX`, um gap conhecido de nov/25–mar/26. A
  janela pendente (29/09–15/10) tem 100% de UF: 36.124 pedidos pagos.
- **Marca sem conta:** Kokeshi na Shopee API.
- **Ausência convertida em zero:** confirmado na API. `refreshed_at` é o
  `MAX(ingested_at)` de todos os canais pedidos, e
  `channels_sem_cobertura_regional` só conhece o TikTok. A Shopee aparece com 0
  em set e out sem aviso. **Depois que este PR rodar, o ML volta a ficar
  fresco e o `refreshed_at` passa a parecer saudável para "todos os canais"
  enquanto a Shopee segue em 0.** Ver o item 7.3.

## 6. O que o PR implementa

O PR não troca a fonte Shopee, porque não há equivalência. Ele faz só isto:

- Antes de ler a fonte Shopee, uma sonda
  `to_regclass('silver.stg_shopee_order_item_snapshots')`. Se a relação não
  existe, a Shopee sai da rodada e vira `source_unavailable`. O diagnose
  imprime `FONTE INDISPONIVEL`, a carga devolve
  `marketplaces_source_unavailable` e a CLI escreve `AVISO` em stderr. Nenhuma
  linha Shopee é inserida.
- O ML continua obrigatório. Ele não tem sonda, e a falta de `raw.ml_orders`
  segue derrubando a carga.
- Os modos exclusivos da Shopee (`--diagnose-shopee-window`, refresh e
  restore) continuam falhando de forma explícita. É o correto: a fonte deles
  não existe.

**Dry-run (`--diagnose`, só leitura, 16/10):**

```
teto superior INCLUSIVO: 2026-10-15
ml: max_date_gold=2026-09-28 max_date_source_eligible=2026-10-15 estimated_new_rows=1629 will_update=True
shopee: FONTE INDISPONIVEL (relacao ausente no banco) — fora da carga; Gold congelada em max_date_gold=2026-08-24
```

Com o código atual de produção, o mesmo comando falha com
`relation "silver.stg_shopee_order_item_snapshots" does not exist`.

## 7. Recuperar a Shopee: o que falta

### 7.1 Campos ausentes

UF, município e CEP do destinatário, por pedido Shopee. Também falta uma
definição de GMV ratificada para a fonte API (`total_amount` ≠
`order_amount`) e a conta Kokeshi.

### 7.2 Endpoint ou fonte necessária, por ordem de custo

1. **Shopee Open API, dados de etiqueta**
   (`v2.logistics.get_shipping_document_data_info`). O endereço do
   destinatário existe na etiqueta de envio. **Hipótese não provada.** Pode vir
   mascarado também, e provavelmente só existe na janela antes do envio, como
   o CPF (185/185 em `PROCESSED`, 0/707 em `COMPLETED`). A prova exige um probe
   pela DAG no repo airflow, disparado pelo Mário, em uma marca.
2. **XML da NF-e no emissor.** O destinatário (UF, município, CEP) é
   obrigatório na NF-e, e ~85% dos pedidos já trazem `access_key`. Falta
   identificar o sistema emissor dos CNPJs SP/MG e ingerir só os campos de
   destino.
3. O export manual do Seller Center está excluído por instrução.

### 7.3 Canais afetados

Só a Shopee, nas 5 marcas. A Kokeshi também fica sem API. O ML é recuperado
por este PR. O TikTok não muda.

### 7.4 Impacto na tela `/regioes`

- O mapa e o ranking por UF ficam sem Shopee depois de 24/08.
- O total "todos os canais" sai subestimado em set e out.
- O GMV Shopee por UF de set/out aparece como 0, sem aviso.
- O `refreshed_at` vai parecer fresco assim que o ML voltar.

### 7.5 Menor implementação para recuperar a visão

1. **Agora (este PR):** fazer o merge e atualizar o runner. O ML volta na
   próxima execução do `full_daily`. Isso é uma ação operacional e não foi
   executada.
2. **Feito no próprio PR #81 (RECOVERY-2):** a cobertura por canal entrou na
   API e na tela. Ver a seção 9.
3. **Recuperar a Shopee:** provar o endpoint de 7.2.1. Se ele entregar a UF,
   gravar só `state`, `city` e `zipcode` na visita em `READY_TO_SHIP`, sem
   nome, telefone ou endereço, com preservação contra sobrescrita por máscara.
   Depois, um transform Gold a partir de `silver.stg_shopee_orders` (`is_sale`)
   e o GMV ratificado.

## 8. REGIONAL-SOURCE-RECOVERY-2: candidata Bling (16/10/2026)

Fonte: `gold.bling_all_brands_nfes_gproducts`, uma VIEW de NF-e com uma linha
por item. A análise foi offline, sobre os pedidos com `numero_pedido_loja` no
formato de `order_sn`: 643.704 linhas, 271.950 NF-e e 270.304 pedidos, de
dez/25 a 15/10. Só agregados foram impressos.

| Verificação | Resultado |
|---|---|
| 1. A chave casa com o pedido Shopee? | **Sim.** A marca do Bling é igual à da API em 100% dos casados, e a chave da NF-e da API (`invoice_data.access_key`) aparece no Bling em **99,98%** de 96.165 pedidos |
| 2. Taxa de casamento (pedidos de venda) | Ápice ~100% até ago, 76,6% em set e 46,9% em out. Rituaria ~100% em jan–abr, 22–43% depois. Lescent 15–86%. Barbour's 0–21% desde abr |
| 3. Multiplicidade | 268.886 pedidos com 1 NF e 1.418 com 2 ou mais. Destes, 1.365 estão em lojas **de atacado** do Bling e só 53 em lojas Shopee, todos com NF autorizada (nota fracionada). A deduplicação é por pedido |
| 4. Destinatário ou emissor? | **Destinatário.** Contra o export (197.942 pedidos sobrepostos), a UF bate em **100%** em 27 UFs distintas, o município em 99,94% e o CEP vem preenchido em 100%. O CFOP 6xxx (interestadual) predomina |
| 5. Cobertura por marca | Cobre as 4 marcas da API e também a **Kokeshi**, que não tem conta na API (10–46% do export). Cobre ainda a Denavita |
| 6. Cancelados e devolvidos | 3.004 de 95.547 cancelados têm NF, e 2.063 de 6.536 devoluções têm NF. A NF não serve para inferir status: o status vem do pedido |
| 7. GMV contra o export | `valor_nota` fica 3,7% a 5,3% abaixo de `order_amount` nos pedidos sobrepostos |
| 8. Separa a Shopee dos demais canais? | O campo `canal` diz só "marketplace". A Shopee é identificada pela `loja_id` (uma por marca) ou pelo formato do número do pedido. Há números no formato Shopee em lojas de **atacado**, que precisam ser excluídas por `loja_id` |

**A causa da cobertura parcial é estrutural.** Cruzando com
`raw.shopee_orders.fulfillment_flag` (pedidos de venda desde jun/26):

| Marca | `fulfilled_by_local_seller` com NF no Bling | `fulfilled_by_shopee` (Full) com NF no Bling | Peso do Full |
|---|---|---|---|
| Ápice | 99,5% | 0,6% | 13% |
| Barbour's | 96,8% | **0,0%** | 88% |
| Lescent | 99,7% | **0,0%** | 65% |
| Rituaria | 100,0% | **0,0%** | 59% |

No Shopee Full, a nota é emitida fora do Bling da marca. O Bling só enxerga o
que sai do CD próprio. O mix de UF dos pedidos com NF e sem NF, no export de
jun–ago, difere no máximo 2,5 p.p. por UF, então a amostra local não parece
geograficamente enviesada. Ainda assim, ela é uma **amostra** e não a
população.

**Decisão: a fonte não vale como substituta.** O endereço é confiável, mas a
fonte não cobre o Full, que é a maior parte do volume em 3 das 4 marcas. Usá-la
como "Shopee regional" mostraria 12% a 41% dos pedidos como se fossem o canal.
Não houve shadow nem cutover. O PR #81 restaura só o ML, com aviso explícito
para a Shopee.

**Opção para decidir depois (não implementada):** carregar a Shopee com a API
como denominador (`uf_eligible_orders`), a UF do Bling no fluxo local e o Full
em `XX`. A `uf_fill_pct` real ficaria assim: Barbour's ~12%, Lescent ~35%,
Rituaria ~41%, Ápice ~87%. O contrato já comporta isso, que é o mesmo
mecanismo do gap da Barbour's no ML. Para seguir, falta decidir:

1. a definição de GMV (`total_amount` da API × `order_amount` do export);
2. a Kokeshi, que tem NF no Bling mas não tem denominador sem a API;
3. se um mapa com 12% de UF na Barbour's tem valor de decisão.

Para o Full, a UF teria de vir da NF emitida pela Shopee. As chaves existem em
`invoice_data`, mas não há fonte do XML.

## 9. Contrato novo da API `/regioes/*` (PR #81)

Os campos novos estão em todas as respostas (summary, by-uf, by-brand e
trend):

| Campo | Significado |
|---|---|
| `cobertura_por_canal[]` | Um item por canal pedido, com `channel`, `marketplace_id`, `status`, `dados_ate`, `esperado_ate`, `motivo` e `motivo_detalhe` |
| `status` | `atualizada` (`dados_ate ≥ esperado_ate`, ou seja, D-1), `atraso_leve` (exatamente 1 dia antes, D-2), `defasada` (2 dias antes ou mais), `sem_carga` ou `sem_cobertura` (TikTok). Não há tolerância: **D-2 nunca é "atualizada"** (decisão RECOVERY-3) |
| `dados_ate` | Última **data** carregada do canal, sem recorte de período. Nunca é o instante de ingestão |
| `esperado_ate` | `min(fim do período, D-1 BRT)` |
| `motivo` | `carga_atrasada`, `carga_defasada`, `sem_carga` ou `sem_fonte_regional`. É `null` quando o canal está atualizado. O texto estrutural do canal (ex.: "fonte descontinuada") só aparece quando o status é `defasada`, `sem_carga` ou `sem_cobertura`, nunca no atraso leve |
| `dados_completos_ate` | Menor `dados_ate` entre os canais com fonte, ou seja, até onde o total cobre todos eles. É `null` se algum canal não tem carga |
| `canais_defasados` | Canais com fonte e status `defasada` ou `sem_carga` (os casos graves) |
| `canais_com_atraso` | Canais com fonte e status `atraso_leve`, `defasada` ou `sem_carga`. Qualquer um deles impede chamar o total de atualizado |
| `trend.data[].canais_ausentes` | Canais com fonte cuja carga para antes do fim do ponto. Ausência, não zero |

`refreshed_at` permanece, mas a tela não o mostra mais sozinho como
"Atualizado em" quando há canal defasado. O cabecalho passa a dizer "Dados
completos até 24/08/2026 · carga mais recente (parcial) em …". A tela também
ganha um aviso por canal antes dos KPIs, o subtítulo do KPI de GMV muda e cada
ponto da tendência recebe o chip "sem Shopee".

**Compatibilidade entre versões.** Todos os campos são aditivos. No front,
eles são opcionais e lidos só por `normalizarCobertura`:

- **API antiga** (sem os campos): a tela mantém o comportamento anterior, com
  "Atualizado em" e sem aviso, e nada quebra;
- **status desconhecido**: conta como atraso, para nunca chamar o total de
  atualizado;
- **sem `canais_com_atraso`**: o atraso é derivado da lista por canal;
- **Oráculo (MCP)**: os contratos zod de `/regioes` não são estritos, então
  campos novos passam.

### Ordem de publicação

1. Merge do PR #81 em `main`.
2. A Vercel publica o front automaticamente. Ele fica compatível com a API
   antiga; nesse intervalo a tela fica como hoje, e o ML continua parado em
   28/09 porque o runner ainda não mudou.
3. Deploy manual da API no Render. Depois dele, `/regioes/summary` já traz
   `cobertura_por_canal`, com o ML `defasada` (dados até 28/09) e a Shopee
   `defasada` (dados até 24/08).
4. Smoke da API e da tela.
5. Atualizar o runner (`runner/producao`) para o merge.
6. O próximo `full_daily` (ou um disparo manual seu) roda o
   `gold_regional_incremental` com SUCCESS e AVISO da Shopee, e em seguida o
   `sync_region_if_needed`.
7. Smoke final: o ML fica `atualizada` (dados até D-1) e a Shopee segue
   `defasada` (dados até 24/08).

Atualizar o runner antes do Render é o único caminho em que o ML volta fresco
com a API antiga e a Shopee aparece com zero sem aviso.

Alternativa descartada: carregar a Shopee API com `uf='XX'`. Os totais por
canal seriam preservados, mas o mapa continuaria vazio, o GMV mudaria de
definição sem ratificação e a `uf_fill_pct` de 0% só trocaria um zero
silencioso por um alerta permanente.
