# Contrato de deduplicação dos exports `Order.all` da Shopee

**Gate SH-AUTO-1B.** Código em
[`pipelines/connectors/shopee/_snapshots.py`](../pipelines/connectors/shopee/_snapshots.py),
integrado em `parse_brand`.

Este gate entregou **somente código, testes e esta documentação**. Nenhuma
execução de pipeline, refresh manual, backfill, escrita em banco, DAG,
Connection ou migration.

---

## 1. O defeito

`parse_brand` lia **todos** os `Order.all*.xlsx` da pasta da marca e empilhava as
linhas num lote único; `_aggregate_daily` agrupava por `order_id` e **somava**
`subtotal` e `qty` linha a linha. Um pedido presente em dois exports com janelas
sobrepostas era contado **duas vezes**.

O dedup que já existia cobria apenas os campos *order-level* (`total_global`,
`commission_net`, `service_fee_net`, `freight_est`), por `max()` — e `max()`
também está errado como regra de escolha, porque **mistura campos de instantes
diferentes**: pega o maior valor de cada export, não o valor de um export.

Pedidos presentes em mais de um arquivo, medidos em 01–24/08/2026:

| marca | pedidos duplicados |
| --- | --- |
| kokeshi | 10.161 |
| barbours | 1.007 |
| lescent | 478 |
| rituária | 369 |
| ápice | 294 |

---

## 2. A unidade é o snapshot, não o arquivo

Um export grande vem partido em `_part_N_of_M`. As partes são pedaços do **mesmo
retrato** e nunca competem entre si: são reagrupadas antes de qualquer escolha.
Tratar `part_3_of_8` como candidato concorrente de `part_4_of_8` descartaria 7/8
do export.

Padrões reais encontrados na pasta de produção (22/09/2026):

| padrão | exemplo | tratamento |
| --- | --- | --- |
| simples | `Order.all.20260805_20260805.xlsx` | 1 snapshot |
| com prefixo novo | `Order.all.order_creation_date.20260901_20260908.xlsx` | 1 snapshot |
| multipart | `..._part_1_of_8.xlsx` … `_part_8_of_8.xlsx` | 1 snapshot, 8 arquivos |
| download duplicado | `...20260805_20260805 (1).xlsx` | snapshot próprio; empata na janela → **recusa** |
| outro relatório | `Order.toship...` | fora do glob, ignorado |

**Cobertura multipart medida: 100% completa** em todas as 5 marcas — nenhum
export com parte faltando, repetida ou com total divergente.

---

## 3. A regra do vencedor

```
chave_de_ordem = (date_to, date_from)   # do NOME do arquivo
```

O snapshot com maior chave vence. Por pedido, mantém-se **todas as linhas do
snapshot vencedor** e descartam-se **integralmente** as do mesmo pedido nos
perdedores.

**Por que `date_to` primeiro:** não se exporta pedido de um dia que ainda não
aconteceu, então um export mais recente tem `date_to` maior. Com o mesmo teto, a
janela que começa depois é a mais estreita e mais recente — caso real
`0805..0810` × `0810..0810`, presente nas cinco marcas.

### 3.1 Por que a regra se sustenta — medido, não suposto

Medição de 22/09/2026 sobre os arquivos reais:

- **Onde há divergência de conteúdo, ela é exclusivamente de status.** Nos três
  pares sobrepostos da ápice em agosto/setembro: 50/50, 56/56 e 108/108 pedidos
  divergentes, **todos** por status; quantidade e número de SKUs idênticos em
  100%. Em todos, o snapshot de `date_to` maior é o de status mais maduro — que
  é o que se quer publicar.
- **O único par em que a ordenação por janela discorda do `mtime`**
  (`20260401..20260501` × `20260501..20260531`, ápice e barbours) tem conteúdo
  **idêntico** nos pedidos em comum: **386/386** e **1.025/1.025**. Os dois
  saíram da mesma carga histórica de 19/06, então a escolha é indiferente e a
  discordância **não tem consequência observável**.

### 3.2 O que é proibido como autoridade

| fonte | por que não |
| --- | --- |
| `Path.stat().st_mtime` | não sobrevive a cópia de pasta, backup ou `git clone`; nos arquivos reais é só o instante do **download** — os exports de abril e maio da barbours têm o **mesmo** mtime (17:03), então nem desempata |
| ordem do `glob` / lexicográfica | acidente do sistema de arquivos |
| ordem das linhas | idem |
| `max()` campo a campo | mistura instantes: status novo + valor antigo é um número que não existe em export nenhum |

### 3.3 Por que não há metadado melhor — inventário completo

| fonte | conteúdo |
| --- | --- |
| nome do arquivo | janela, partes, sufixo `(1)` do navegador — **nenhuma data de exportação** |
| `docProps/core.xml` | `dcterms:created` **fixo em `2006-09-16T00:00:00Z`**, hardcoded pela biblioteca `Go Excelize` que a Shopee usa |
| datas dos membros do zip | zeradas (`1980-00-00`) |
| colunas da planilha | 64 colunas, todas do pedido — **nenhuma marca de geração do export** |

A janela do nome é o **único** metadado ordenável e estável entre máquinas.

---

## 4. Recusas fail-closed

| situação | erro | por quê |
| --- | --- | --- |
| parte faltando, repetida ou total divergente | `SnapshotIncompleto` | um export 7/8 publicado como inteiro perde um oitavo do faturamento sem nenhum sinal |
| dois snapshots com a **mesma** janela | `SnapshotAmbiguo` | sem desempate confiável; escolher pela ordem do `glob` faria o número publicado depender do sistema de arquivos |
| nome sem `YYYYMMDD_YYYYMMDD` | `NomeDeExportInvalido` | arquivo inordenável na agregação reintroduz a dupla contagem pela porta dos fundos |

**Como resolver um `SnapshotAmbiguo`:** remova da pasta o arquivo que não deve
valer, ou renomeie-o para fora do padrão `Order.all*.xlsx`. Não existe regra
automática correta — o nome não carrega a informação necessária.

---

## 5. Semântica da escolha

Para cada pedido, o snapshot vencedor fornece **tudo**: status, devolução, datas,
comprador, itens e campos financeiros. Consequências que são intencionais:

- `subtotal` e `qty` vêm **só** do vencedor;
- não existe "status novo + itens antigos";
- se o vencedor tem **menos** SKUs, os SKUs que só existiam no antigo **somem** —
  o vendedor removeu o item do pedido;
- snapshot mais novo com valor **menor** é normal: um cancelamento tira o pedido
  do GMV;
- pedido presente uma única vez sai **exatamente** como antes deste gate.

---

## 6. Relação com `raw.shopee_ingestion_file`

A frente Raw (Data Mart) já resolve o mesmo problema com metadado melhor:
`file_id`, `file_sha256`, `source_modified_at` e a auditoria de monotonicidade
`file_id × raw_ingested_at`. `gold_regional` escolhe o `file_id` vencedor.

Esta deduplicação **não** usa nada disso, e a razão é de arquitetura: o parser do
`shopee_manual_refresh` lê **arquivos do disco local**, sem acesso ao Data Mart, e
o Gate SH-AUTO-1B é estritamente local. Quando a fatia manual for substituída
pela API (SH-AUTO-2 em diante), esta regra sai junto com o parser.

`parse_filename_part` de `pipelines/ingestion/shopee_raw/inventory.py` tem a
mesma semântica de agrupamento, mas **não é importado**: aquele módulo importa
`connectors.shopee.connector`, e importá-lo daqui fecharia um ciclo. A expressão
regular é duplicada de propósito e travada por teste
(`test_contraprova_regex_de_parte_bate_com_a_do_inventario_raw`), para que o
drift apareça no CI e não no número publicado.

---

## 7. Reconciliação medida (22/09/2026, leitura pura)

### 7.1 Parser atual × candidato, sobre os mesmos arquivos

| marca | arquivos → snapshots | pedidos em >1 snapshot | ΔGMV | Δunidades | Δpedidos | Δcancelados |
| --- | --- | --- | --- | --- | --- | --- |
| ápice | 22 → 18 | 990 | **−3,196%** | −3,249% | −0,148% | +1,195% |
| barbours | 43 → 18 | 3.903 | **−2,621%** | −2,883% | −0,236% | +1,182% |
| lescent | 19 → 18 | 1.241 | **−4,493%** | −4,597% | −0,444% | +2,308% |
| rituária | 18 → 18 | 4.943 | **−15,112%** | −14,944% | −0,245% | +2,064% |

🔑 **As duas causas se separam sozinhas.** A queda de `pedidos` é compensada
**exatamente** pela alta de `canceled_orders` — ápice −47/+47, barbours
−326/+326, lescent −119/+119, rituária −62/+62. Isso é **maturação de status**: o snapshot vencedor traz o
pedido já cancelado. A queda de `gmv` e `units_sold`, muito maior e na mesma
proporção entre si, é a **dupla contagem** sendo removida.

A rituária é a mais afetada porque tem um export `20260707..20260806` que
sobrepõe julho inteiro.

### 7.2 Candidato × API corporativa — a prova independente

Universo comum, mesma regra (`status <> Cancelado`), 258 dias:

| marca | atual vs API | **candidato vs API** |
| --- | --- | --- |
| ápice | +3,768% | **+0,451%** |
| barbours | +2,877% | **+0,181%** |
| lescent | +5,197% | **+0,471%** |
| rituária | +17,980% | **+0,152%** |

Em unidades: ápice +3,684% → **+0,315%** · barbours +3,168% → **+0,194%** ·
lescent +5,199% → **+0,363%** · rituária +17,732% → **+0,138%**.

O parser atual media entre **+2,9% e +18,0%** acima da API. O candidato converge
para **+0,15% a +0,47%** — o resíduo esperado de maturação de status, já que a
API reflete o estado de hoje e os arquivos congelam o estado do dia da
exportação. A fonte independente confirma a magnitude da duplicação e sua
remoção.

### 7.3 Kokeshi — e um defeito histórico que some por tabela

Sem cobertura de API (a marca não está no registry), a verificação é de
consistência interna. O diagnóstico focal (22/09/2026, **offline**, sem rede e
sem banco) encontrou o seguinte:

| item | resultado |
| --- | --- |
| arquivos → snapshots | 124 → 19, desempate OK |
| snapshots que contêm os pedidos afetados | **2** |
| `20260805..20260805` | **perdedor** — contém os valores inválidos |
| `20260805..20260810` | **vencedor** — mesmos 2.617 registros, valores válidos |
| a linha inválida sobrevive à deduplicação? | **não** |
| categoria da diferença entre os dois | **status** (maturação) |

Os valores inválidos são 2.494 pedidos com quatro campos financeiros
(`total_global`, `commission_net`, `service_fee_net`, `freight_est`) em **formato
US** (`1,234.56`), que `_numeric.py` rejeita deliberadamente. Esse é o defeito
que derrubou o `shopee_daily` em **28/08 (run #226)** e **15/09 (run #297)** — o
mesmo arquivo, a mesma marca.

🔑 **A deduplicação elimina esse defeito incidentalmente, e isso não é uma
correção genérica de valores inválidos.** Ela funciona aqui apenas porque a
seleção do snapshot acontece **antes** da conversão numérica: `_read_xlsx`
devolve células cruas, `deduplicar_por_pedido` escolhe, e só então
`_aggregate_daily` chama `_to_float`. O snapshot perdedor é descartado inteiro,
com os valores ruins dentro.

🔴 **O fail-fast continua valendo.** Se o valor inválido estiver no snapshot
**vencedor** — ou no único snapshot do pedido — o parser levanta como sempre.
Quatro testes fixam essa fronteira, incluindo o caso fino em que o snapshot
perde para um pedido e vence para outro (a escolha é **por pedido**, não por
snapshot inteiro).

O contrato do campo `total_global` para exports em formato US **não é escopo
deste gate** e continua aberto.

#### Reconciliação local completa (22/09/2026, offline)

| item | resultado |
| --- | --- |
| arquivos → snapshots | 124 → **19** |
| linhas de SKU lidas | 599.168 |
| linhas após deduplicação | **570.075** (−29.093, −4,86%) |
| pedidos presentes em >1 snapshot | **22.046** |
| janela | 2026-01-01 .. 2026-09-15 (258 dias) |
| determinismo em duas ordens de arquivos | **idêntico** |
| campos inválidos sobreviventes | **0** |

Agregados do resultado deduplicado: `orders` 471.762 · `gmv` 24.539.521,29 ·
`units_sold` 510.335 · `canceled_orders` 68.425 · `returned_orders` 3.229 ·
`delivered_orders` 336.235 · `total_fees` 6.162.198,13 · `total_settlement`
23.460.332,94 · `seller_shipping_cost` 5.486.316,16 · `unique_buyers` 450.023.

Invariantes: nenhum agregado negativo · 0 dias com GMV negativo · 0 dias com
`orders ≤ 0` · `units_sold ≥ orders` · `total_fees / gmv` 25,11% ·
`total_settlement / gmv` 95,60% · `seller_shipping / gmv` 22,36%.

🔑 **Não há ΔGMV a reportar para a Kokeshi, e a razão importa:** o parser
**atual** não produz número nenhum nessa marca — ele levanta
`ShopeeNumericParseError` ao agregar, que é o defeito dos runs #226 e #297. A
regra nova é a primeira a agregar a Kokeshi inteira com sucesso. A comparação
"antes × depois" existe para as outras quatro marcas (§7.1 e §7.2); aqui o
"antes" é uma exceção, não um valor.

### 7.4 Caminho incremental

`fetch_incremental(days_back=3)` lê os mesmos arquivos e passa pela mesma
deduplicação. Dias cobertos por um único snapshot saem **idênticos** — é o que
`test_parse_brand_snapshot_unico_inalterado` fixa.

---

## 8. O que continua bloqueado

Esta correção **não autoriza backfill**. Ela torna o backfill *possível* sem
inflação, mas o impacto agregado sobre o histórico já publicado precisa ser
revisado e aprovado antes de qualquer reprocessamento amplo — a projeção está na
seção de reconciliação do PR.

O caminho incremental de 3 dias segue seguro, como já era.

Também continuam abertos, e **fora** deste gate:

- o **contrato do `total_global` em formato US** (§7.3) — hoje rejeitado por
  desenho; a deduplicação só evita encostar nele quando o snapshot ruim perde;
- a **comparação numérica "antes × depois" da Kokeshi**, impossível por
  construção: o parser atual levanta nessa marca em vez de produzir um valor
  (§7.3). A leitura completa pela regra nova foi feita e está reconciliada
  internamente;
- a **validação da Kokeshi contra fonte externa**, que só existirá quando a
  marca entrar na API (gate SH-AUTO-7).

---

## 9. Modo de validação usado

A validação de 22/09/2026 foi feita em **modo offline**, por decisão operacional
tomada durante o incidente `EXP-3B2-I1`:

- todas as variáveis de conexão e segredo removidas do ambiente antes de
  qualquer import (67 nomes);
- `socket.socket.connect`, `socket.create_connection`, `socket.getaddrinfo`,
  `psycopg2.connect` e `sqlalchemy.create_engine` substituídos por sentinelas que
  **levantam**, com a sentinela verificada antes do diagnóstico começar;
- dependências resolvidas do cache local (`uv --offline`), sem instalação remota;
- **zero socket aberto, zero conexão, zero consulta** a Neon, Data Mart, API ou
  Airflow;
- nenhum `order_id`, comprador, CPF ou valor bruto de célula impresso.

A reconciliação contra a API das §7.2 é **anterior** a essa restrição e não foi
refeita.
