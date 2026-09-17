# Contrato da Expedicao — Gates EXP-1A ... 1F-P / 2A / 2B / 2C0 / 2C1

**Estado: ATIVA EM PRODUCAO desde 17/09/2026 (EXP-2C1-P).** As duas flags estao
LIGADAS: a API serve `availability=available` e `/expedicao` responde 200 com a
tela real. Migration `018` aplicada, quatro contas cadastradas.

**A carga continua MANUAL.** Nenhum agendamento existe: `load_mode =
manual_snapshot` e `no_automation = true` na propria resposta da API. A
fotografia so' avanca quando alguem executa o `--apply` — o batch vigente depende
de execucao manual, e entre duas execucoes a tela envelhece. A Expedicao **nao**
e' tempo real e **nao** e' automatizada.

O EXP-1F corrigiu a semantica do alerta `expedicao_source_freshness`, que nascia
permanentemente `fail`/`high`. Ver a secao propria mais abaixo.

| Frente | Estado |
|---|---|
| Migration `018` | **aplicada** no Neon em 16/09/2026 (`016` = Full ML, `017` = PMA) |
| Tabelas em producao | criadas e populadas; `expedicao_refresh_run` acumula 4 linhas por hora |
| Registry (`marts.dim_seller_account`) | **4 contas ativas** (Kokeshi fora: nao existe na fonte) |
| Orquestracao do `--apply` | implementada (EXP-1D-H1) e **executada em producao** (EXP-1E, 1F-P, 2C1-P) |
| Refresh publicado | 3, todos **manuais** (ultimo: batch `494c2441-52b3-48bf-afea-dbea850effe7`, auditoria #319 `success`, 821 pedidos) |
| API read-only | **implementada e ATIVA** (EXP-2A), `EXPEDICAO_API_ENABLED=true` no Render |
| Tela (frontend) | **implementada e ATIVA** (EXP-2B), `NEXT_PUBLIC_EXPEDICAO_ENABLED=true` na Vercel |
| Autenticacao | **inexistente** — API publica por decisao temporaria (EXP-2C0/2C1-P); divida registrada |
| `order_ref` | **desativado**: `expedicao_order_ref_secret` vazio, identificador do pedido nao e' servido |
| MCP | nao iniciado |
| ML / TikTok / calendario | fora deste gate |
| `schedule_plan.py` / Airflow | **nao integrados** — automacao por DAG segue PENDENTE |

A `018` e' a proxima revisao linear depois da `017` (PMA). Cadeia: `001 -> ... ->
016 -> 017 -> 018`, raiz unica, head unico.

---

## Registry — autoridade e estado real

**Autoridade: Neon.** O Data Mart nao tem schema `marts` nem
`dim_seller_account`. `gold.dim_marca_conta` e `raw.mp_seller_brand_map` existem
la, mas nenhuma mapeia `shop_id` da Shopee.

Estrutura confirmada por leitura **read-only**:

```
marts.dim_seller_account(seller_account_id PK, marketplace_id, loja_id,
    external_seller_id, account_name, ativo NOT NULL, created_at, updated_at)
  UNIQUE (marketplace_id, external_seller_id)

marts.dim_loja(loja_id PK, empresa_id, brand_key UNIQUE, nome_loja,
    nome_normalizado, ativo NOT NULL, ...)

marts.dim_marketplace: 1 TikTok Shop | 2 Mercado Livre | 3 Shopee (ativo)
                       4 Magalu (inativo) | 5 Amazon (inativo)
```

| Item | Resultado |
|---|---|
| `dim_marketplace` | 5 linhas — Shopee = **3**, confirmando a convencao usada |
| `dim_loja` | **5 linhas, todas ativas**: apice(1), barbours(2), kokeshi(3), lescent(4), rituaria(5) |
| `dim_seller_account` | **0 linhas — VAZIA** |
| Unicidade | `UNIQUE (marketplace_id, external_seller_id)` garante conta unica no marketplace |
| 1:1 loja→marca | `brand_key` e UNIQUE em `dim_loja`; o codigo revalida e bloqueia se houver loja com duas marcas |
| Semantica de atividade | **existe** — `ativo` nas DUAS dimensoes |

**Como uma conta sai do conjunto esperado:** `UPDATE marts.dim_seller_account
SET ativo = false` (ou desativar a loja). **Nao se apaga a linha**, para nao
perder o historico da chave. `REGISTRY_SQL` exige `sa.ativo AND l.ativo`.

**Os `shop_id` nao estao no codigo** — ha teste que falha se alguem hardcodar.

### DML de cadastro das contas — EXECUTADO em 16/09/2026 (EXP-1E)

```sql
-- Kokeshi (loja_id 3) NAO entra: nao ha ingestao Shopee para ela.
INSERT INTO marts.dim_seller_account
    (marketplace_id, loja_id, external_seller_id, account_name, ativo)
VALUES
    (3, 1, '1609671923', 'Shopee Apice',    true),
    (3, 2, '1579330222', 'Shopee Barbours', true),
    (3, 4, '1593864538', 'Shopee Lescent',  true),
    (3, 5, '1457734799', 'Shopee Rituaria', true);
```

Enquanto a tabela estiver vazia, `extract()` devolve `SOURCE_UNAVAILABLE` e nada
e publicado — falha fechada, nao fila apagada.

---

## Saude da fonte — IGUALDADE de conjuntos

| Fonte | Contas | Watermarks | Backlog | `source_health` | Acao |
|---|---|---|---|---|---|
| ok | conjuntos iguais | validos | > 0 | `healthy` | publica fotografia normal |
| ok | conjuntos iguais | validos | == 0 | `healthy` | **publica fotografia vazia** |
| ok | **esperada ausente** | — | — | `account_missing` | falha, preserva fila |
| ok | **observada sem cadastro** | — | — | `unexpected_account` | falha, preserva fila |
| ok | iguais | **ausente** | — | `watermark_missing` | falha, preserva fila |
| — | registry duplicado/ambiguo | — | — | `registry_ambiguous` | falha, preserva fila |
| — | registry vazio | — | — | `source_unavailable` | falha, preserva fila |
| erro de consulta | — | — | — | excecao | falha, preserva fila |

**Subconjunto nao basta.** Uma loja nova aberta na Shopee apareceria na API sem
cadastro; ignora-la deixaria o backlog dela invisivel, e dar marca por texto e
proibido. Os dois desfechos sao inaceitaveis, entao a publicacao inteira bloqueia
ate a conta ser cadastrada.

`expected_accounts` vem do REGISTRY, nunca da fonte: se viesse da fonte, uma
conta que sumisse apareceria como "nao esperada" em vez de "faltando".

---

## Chave da fila

```
PK: (channel, shop_account, marketplace_order_id)
```

Espelha `pk_shopee_orders (shop_account, order_sn)`, que e UNIQUE na origem.
**`brand` e ATRIBUTO, nao identidade** — corrigir a marca de uma conta no
registry nao pode criar um pedido novo.

Provado em teste: mesma `order_sn` em duas contas gera duas linhas distintas;
trocar a marca no registry mantem a chave; uma conta nao pode publicar pedido
com a marca de outra (o publisher bloqueia).

Sem deduplicacao por "versao mais recente": medimos 29.369 linhas para 29.369
chaves e 29.369 `order_sn` distintos em 30 dias.

---

## Persistencia

| Tabela | Grao / PK | Papel |
|---|---|---|
| `marts.expedicao_fila_atual` | `(channel, shop_account, marketplace_order_id)` | estado atual, substituido por canal |
| `marts.expedicao_refresh_run` | `(channel, shop_account, snapshot_hour)` | resumo horario por conta |

Campos do resumo: `refresh_batch_id, channel, shop_account, brand,
snapshot_hour, observed_at, source_watermark_at, source_advanced, backlog_count,
overdue_count, due_within_24h_count, on_time_count, deadline_unavailable_count,
over_48h_count, slow_count, zombie_count, stalled_count, run_status,
ingested_at`.

Regras:

- uma execucao saudavel grava **uma linha por conta esperada**, inclusive com
  `backlog_count = 0`;
- as quatro categorias de prazo somam `backlog_count`;
- `over_48h`, `slow`, `zombie` e `stalled` sao **transversais** e nao entram
  nessa soma;
- **`stalled_count` nunca e `slow_count + zombie_count`**: as duas anomalias
  podem coincidir no mesmo pedido e a soma contaria a sobreposicao duas vezes;
- `snapshot_hour = date_trunc('hour', effective_at)` em UTC;
- reexecucao na mesma hora: `ON CONFLICT ... DO UPDATE ... WHERE
  EXCLUDED.observed_at > tabela.observed_at`. `DO NOTHING` congelaria a primeira
  leitura da hora;
- o resumo do CANAL e derivado na API. Persistir um total como quinta conta
  criaria linha sem loja, contada em dobro por qualquer `GROUP BY shop_account`.

### Historico integral por pedido — REMOVIDO

A tabela `expedicao_fila_historico` (pedido x hora) saiu do MVP: ~1.000
pedidos/hora dariam ~8,8 milhoes de linhas/ano copiando o mesmo estado. A tela
precisa do estado atual; a tendencia e respondida pelo resumo horario.

**Evolucao futura (nao implementada):** `marts.expedicao_alert_event` — uma
linha quando um pedido **entra ou sai** de `overdue`, `over_48h`, `slow`,
`zombie` ou `stalled`. Transicao relevante, nao fotografia repetida.

---

## Atomicidade

Fila e resumo vao na **mesma transacao**, com o mesmo `refresh_batch_id`. Se
fossem transacoes separadas, uma falha no meio deixaria fila nova com resumo
antigo (ou o contrario) e a tela mostraria um backlog que a tendencia nao
explica.

Garantias testadas: falha no resumo reverte a fila; falha na fila impede o
resumo; lote inconsistente bloqueia; publicar fila sem resumo e recusado; commit
indeterminado vira `IndeterminateCommit` sem rollback cego e sem retry; falha de
auditoria depois do commit nunca marca `failed`.

---

## Consumo (tela e API ATIVAS; MCP nao implementado)

| Superficie | Le | Mostra |
|---|---|---|
| Tela operacional | `expedicao_fila_atual` | vencidos, proximos do prazo, acima de 48h, stalled; filtro por marca e conta |
| Tendencia | `expedicao_refresh_run` | evolucao horaria do backlog e alertas por marca |
| MCP read-only | ambas | mesmas metricas; **sem PII**; alerta quando `overdue_count` ou `over_48h_count` cresce |

O MCP consome os mesmos endpoints da tela: sem SQL proprio, sem recalculo.

---

## Auditoria

`run_mode` **nao e necessaria** (proposta retirada no EXP-1A-R): sem NO_OP,
`rows_loaded = 0` com `status='success'` significa apenas fotografia vazia
publicada. `error_message` permanece exclusiva de erro sanitizado.

`accounts_recorded`, `backlog_count`, `expected/observed/unexpected_accounts`,
`source_health`, `source_advanced` e `empty_photograph` vao para
`audit.data_quality_check.details`.

---

## Runbook do `--apply` (EXP-1D-H1)

### Precondicoes EXTERNAS

O comando **nao** cria nada por conta propria. Antes do primeiro `--apply`:

1. aplicar a migration `018` no Neon (`alembic upgrade head`);
2. cadastrar as quatro contas com o DML da secao *Registry*;
3. exportar `DATABASE_URL` (Neon, gravavel) e `DATAMART_DATABASE_URL` (Data
   Mart, hot standby — **exige VPN**).

Faltando qualquer uma delas o comando **para antes de tocar na fila**. Nao ha
fallback: sem a variavel configurada ele falha, nao adivinha um banco.

### Comandos

```bash
# Leitura. Nao abre conexao gravavel, nao registra em audit.source_sync_run.
python -m pipelines.expedicao.cli --channel shopee --diagnose

# Publicacao. A flag E a confirmacao: nao existe segunda flag nem variavel de
# desbloqueio.
python -m pipelines.expedicao.cli --channel shopee --apply
```

Rode `--diagnose` antes do primeiro `--apply` e confira os agregados por conta
contra o caso de referencia abaixo.

### Ordem de execucao

`config -> target -> preflight -> lock -> registry -> batch_id -> auditoria ->
fonte -> extracao -> validacao -> fila/resumos -> publicacao -> auditoria final
-> release`.

O **lock vem antes da leitura** da fonte: travar depois de ler abriria uma janela
em que outro processo publica no intervalo, e a fotografia descreveria um estado
que ja' mudou. A **auditoria comeca antes da fonte**, em conexao independente,
para que falha de leitura deixe rastro que o rollback da publicacao nao apaga.

### Exit codes

| Codigo | Significado | O que fazer |
|---|---|---|
| `0` | publicado (inclusive fotografia vazia) | nada |
| `1` | falha generica **anterior ao commit** | fila anterior intacta; investigar e reexecutar |
| `2` | advisory lock ocupado | outro refresh do canal esta rodando; **nao** forcar, aguardar |
| `3` | fonte nao publicavel / registry ambiguo | fila anterior **preservada**; corrigir a fonte ou o registry |
| `4` | **commit indeterminado** | ver procedimento proprio abaixo |
| `5` | publicado, auditoria incompleta | o dado **esta** no ar; reconciliar so' a auditoria |
| `6` | precondicao ausente (env, schema `018`, replica, registry vazio) | providenciar a precondicao |

### Fila vazia nao e' fonte doente

Sao dois desfechos **diferentes** e nao podem ser lidos como um so':

* **fonte saudavel, backlog zero** -> a fotografia vazia **e publicada**: a fila
  do canal e' limpa, nenhum pedido e' inserido e os quatro resumos por conta vao
  com `backlog_count = 0`. Exit `0`. Sem isso, um dia realmente zerado manteria a
  fila de ontem no ar e a torre mostraria pendencia ja' expedida.
* **fonte nao saudavel** (`account_missing`, `unexpected_account`,
  `registry_ambiguous`, `watermark_missing`, `source_unavailable`) -> **zero
  DELETE e zero INSERT**, fila anterior preservada. Exit `3`.

Na auditoria, `empty_photograph` em `audit.data_quality_check.details` separa os
dois sem ambiguidade.

### Commit indeterminado (exit 4)

O `COMMIT` nao respondeu conclusivamente. O estado da publicacao e'
**desconhecido**: pode ter sido aplicada.

* **Nao** houve rollback e **nao** deve haver retry cego — repetir duplica ou
  apaga trabalho as cegas.
* A execucao em `audit.source_sync_run` fica em `running`, que e' o estado
  honesto de "nao se sabe como terminou". Marca-la `success` ou `failed` seria
  afirmar o que nao se sabe.
* Procedimento: consultar `marts.expedicao_fila_atual` e
  `marts.expedicao_refresh_run` pelo `refresh_batch_id` da execucao (ele aparece
  na mensagem) e so' entao decidir entre reexecutar ou apenas fechar a
  auditoria.

### Auditoria incompleta (exit 5)

O dado **esta publicado**. A mensagem diz explicitamente que **nenhuma reversao
ocorreu**. Reconciliar apenas `audit.source_sync_run` e
`audit.data_quality_check`; nao mexer na fila.

### Lock ocupado (exit 2)

Advisory lock de **sessao** (`pg_try_advisory_lock`), chave por canal, adquirido
em autocommit e liberado no `finally`. Exit `2` significa que outro refresh do
mesmo canal esta em andamento: nao houve leitura da fonte, nem auditoria, nem
DELETE. Aguardar. Se ninguem estiver rodando, procurar sessao orfa em
`pg_locks`; nao existe flag para ignorar o lock.

### Sequencia do piloto — EXECUTADA em 16/09/2026 (EXP-1E)

1. `alembic upgrade head` no Neon (aplica a `018`);
2. DML das quatro contas;
3. `--diagnose` e conferencia contra o caso de referencia;
4. **um unico** `--apply`;
5. leitura de `marts.expedicao_fila_atual`, `marts.expedicao_refresh_run` e
   `audit.source_sync_run` para conferir o lote publicado.

Executada integralmente. Os refreshes seguintes (EXP-1F-P e EXP-2C1-P) repetiram
apenas os passos 4 e 5 — e continuam sendo **execucoes manuais**, uma a uma.

---

## Alerta `expedicao_source_freshness` — definicao operacional (EXP-1F)

### Tres idades que nao podem ser a mesma coisa

| Conceito | Timestamp | Onde vive | Responde |
|---|---|---|---|
| **Fonte desatualizada** | `source_watermark_at` (= `MAX(ingested_at)` da conta) | `expedicao_refresh_run` e este alerta | a fonte foi lida recentemente? |
| **Pedido antigo no backlog** | `source_ingested_at` da linha | `expedicao_fila_atual.source_freshness_status` | ha quanto tempo ESTA linha nao e relida? |
| **Atraso operacional** | `created_at` / `paid_at` / `dispatch_deadline` | `deadline_status`, `operational_age_status`, `hours_open` | o pedido esta atrasado para o cliente? |

O instante do snapshot e `effective_at`, lido uma unica vez na CLI e injetado em
tudo — nenhuma dessas medidas chama relogio por conta propria.

### Definicao

`expedicao_source_freshness` mede **so o primeiro conceito**: o frescor da
FONTE. Ele responde "o Data Mart parou de atualizar esta conta?" e nada mais.

### Calculo

1. para cada conta esperada, `classify_freshness(source_watermark_at, effective_at)`;
   carimbo **no futuro** (`watermark > effective_at`) nao passa por aqui: vira
   `unknown` direto — ver abaixo;
2. agrupa as contas por marca;
3. o veredito da marca e o **pior** estado entre suas contas;
4. o carimbo reportado e o **mais antigo** entre elas (`None` vence);
5. `freshness -> status/severity`: `fresh -> pass/low`, `stale -> warn/medium`,
   `critical -> fail/high`, `unknown -> warn/medium`.

### Granularidade

Uma linha por **marca**, por execucao. Nunca um agregado global: um unico numero
para o canal esconderia uma conta parada atras de outra atualizada — foi assim
que a planilha antiga ficou 43 dias defasada sem alarme. Uma marca com varias
contas agrega pelo pior, nunca pela media.

### Threshold

Os do contrato, **inalterados**: `FRESHNESS_FRESH_LIMIT = 8h`,
`FRESHNESS_STALE_LIMIT = 24h`, ambos inclusivos (`<=`). O EXP-1F nao criou
threshold novo; mudou apenas **qual timestamp** e medido.

### O que o registro carrega

```json
{
  "brand": "rituaria",
  "freshness": "fresh",
  "source_watermark": "2026-09-16T18:07:49Z",
  "source_age_hours": 0.55,
  "accounts": 1,
  "open_orders": 75,
  "oldest_row_age_hours": 240.0,
  "measures": "source_watermark_only"
}
```

`oldest_row_age_hours` fica visivel de proposito, mas e **contexto**: nao entra
no veredito. `measures` existe para que quem ler o registro saiba, sem abrir
codigo, que o julgamento veio do watermark.

`failed_rows` recebe `open_orders` apenas quando o status nao e `pass`, para
dimensionar o impacto de uma fonte parada. Com a fonte fresca ele e zero,
mesmo havendo backlog.

### Por que mudou (defeito do EXP-1E)

A versao anterior agregava o pior `source_freshness_status` das **linhas** da
fila. Esse campo mede a idade da linha ingerida, e um backlog legitimo sempre
contem pedido cuja linha nao e relida ha mais de 24h — medimos **240h** na
rituaria e **168h** na barbours com o watermark das contas a **0,55h**. As
quatro marcas nasceram `fail`/`high` no primeiro piloto real, com a fonte
saudavel e `source_health = healthy`.

Alerta permanentemente vermelho e alerta ignorado. `FreshnessStatus` ja dizia no
contrato "idade do DADO, nao do pedido, medida por conta": a implementacao e que
divergia do proprio contrato.

### Carimbo no futuro (EXP-1F-R/V)

`classify_freshness` compara `effective_at - watermark <= 8h`, e idade
**negativa** satisfaz essa condicao: sem tratamento, um watermark adiantado
sairia `fresh`. Seria o mesmo defeito que esta secao corrige — sinal quebrado se
apresentando como saudavel. O relogio do Data Mart e o de quem roda a CLI sao
maquinas diferentes, e `ingested_at` e escrito pelo carregador.

Comportamento: `watermark > effective_at` vira **`unknown`**, e o carimbo
reportado vira nulo. A partir de um instante impossivel nao da para afirmar que
a fonte esta fresca nem que esta velha — `critical` seria tao inventado quanto
`fresh`. `source_age_hours` fica negativo de proposito: e a evidencia de por que
o estado e `unknown`.

A comparacao e **estrita**, sem tolerancia: qualquer margem seria um threshold
novo, e o contrato so define 8h e 24h. Se skew de poucos segundos comecar a
gerar `warn` na operacao, definir a tolerancia e decisao de contrato, nao de
implementacao.

### Limitacoes conhecidas

* O watermark e `MAX(ingested_at)` da conta na fonte. Se o carregador reescrever
  linhas antigas sem trazer novidade, o watermark avanca e o alerta fica verde —
  ele mede **leitura**, nao chegada de pedido novo.
* Marca sem nenhuma conta esperada nao gera linha. Conta esperada ausente da
  fonte nao chega aqui: `extract` ja bloqueia com `account_missing` e a
  publicacao inteira e recusada (exit 3).
* O alerta nao diz nada sobre atraso operacional. Backlog vencido se acompanha
  por `overdue_count` e `over_48h_count` em `expedicao_refresh_run`.
* A coluna `source_freshness_status` POR PEDIDO continua usando
  `classify_freshness` sem o tratamento de carimbo futuro. Ela e contexto, nao
  veredito, e mexer nela mudaria dado ja publicado; se um dia `ingested_at`
  aparecer adiantado nas linhas, vira gate proprio.

---

## API read-only da Expedicao (EXP-2A)

**Estado: implementada e ATIVA em producao (EXP-2C1-P).**
`EXPEDICAO_API_ENABLED=true` no Render; a API devolve `availability=available`.
A flag continua nascendo `false` no codigo: com ela off o servico devolve
`availability=unavailable` sem emitir uma unica consulta, e esse continua sendo
o caminho de rollback.

**A API e' publica e nao tem autenticacao** — decisao temporaria aceita pelo
responsavel no EXP-2C1-P. Por isso `order_ref` segue desativado.

### Endpoints

| Rota | Serve |
|---|---|
| `GET /api/v1/expedicao` | resumo do canal + por conta, frescor, cobertura e uma pagina da fila — tudo do MESMO batch |
| `GET /api/v1/expedicao/trend` | serie horaria por conta, para tendencia |

Somente `GET`. Nao existe `POST`, `PATCH` nem `DELETE`, e o servico so' emite
`SELECT`.

### Parametros

| Parametro | Onde | Regra |
|---|---|---|
| `brands` | ambos | CSV, ate 50 itens de ate 64 caracteres |
| `accounts` | ambos | CSV, mesma regra |
| `situacao` | principal | `overdue`, `due_within_24h`, `on_time`, `deadline_unavailable`, `over_48h`, `stalled`, `slow`, `zombie`. Varias combinam com E logico |
| `order_by` | principal | `criticidade` (padrao), `deadline`, `oldest` |
| `limit` / `offset` | principal | 1..500 / >= 0 |
| `include_queue` | principal | `false` devolve so' o resumo |
| `window_hours` | tendencia | 1..336, default 48 |

Filtros e ordenacao passam por **allowlist**: o cliente escolhe uma CHAVE, nunca
escreve a clausula. Valor invalido vira 422 com mensagem FIXA — a entrada nao e'
ecoada, para que um payload com script nao volte renderizado.

### Grao

| Bloco | Grao |
|---|---|
| `totals` | canal, no batch vigente (soma das contas — NAO e' linha materializada) |
| `accounts` | `(shop_account)` no batch vigente |
| `queue` | um pedido |
| `freshness` | `(brand)`, da observacao mais recente |
| `trend.points` | `(shop_account, snapshot_hour)` |

`trend` **nunca soma horas diferentes**: o mesmo pedido continua no backlog de
uma hora para a outra, e somar dois pontos o contaria duas vezes.

### Frescor — de onde vem o veredito

`freshness` sai da observacao **mais recente** de `expedicao_source_freshness`,
por marca, via `DISTINCT ON (brand) ... ORDER BY check_timestamp DESC, check_id
DESC`. O `check_id` desempata as quatro linhas de um mesmo lote, que compartilham
o timestamp do commit.

E' **proibido** agregar o pior valor historico. O historico guarda as quatro
linhas `fail`/`high` de 16/09 18:41, gravadas com a semantica antiga (pior pedido
do backlog); servi-las como estado atual ressuscitaria o defeito do EXP-1E.

Linha de auditoria **sem** `measures="source_watermark_only"` e' antiga: o
servico ignora o veredito dela e deriva o estado do watermark do batch servido,
com os limites do contrato (8h / 24h, inclusivos; futuro e nulo -> `unknown`).

| Campo | Mede |
|---|---|
| `freshness`, `source_age_hours` | a FONTE foi lida recentemente (watermark da conta) |
| `oldest_row_age_hours` | ha quanto tempo a linha mais velha do backlog nao e relida — **contexto**, nunca veredito |
| `deadline_status`, `over_48h_count` | atraso do PEDIDO — situacao operacional, nao frescor |

### `load_mode = manual_snapshot`

**Nao existe agendamento.** Toda fotografia veio de alguem executando o refresh
a mao. `limitations.snapshot_age_hours` diz ha quanto tempo, e
`limitations.no_automation` e' sempre `true`. O consumidor deve exibir isso: um
painel que nao mostra a idade do dado convida a decidir sobre estado vencido.

`source_advanced=false` e' METADADO: significa "a fonte nao avancou desde a
leitura anterior", nao falha. O estado e' recomputado por relogio a cada
execucao justamente porque a fonte pode ficar parada.

### Cobertura e Kokeshi

`coverage` compara conjuntos, nao continencia: `expected_accounts` vem do
registry, `observed_accounts` da fotografia, e as diferencas viram
`missing_accounts` / `unexpected_accounts`. Conta que some da fonte aparece como
**faltando**, nao como silencio.

`brands_not_covered` lista **Kokeshi**: ela existe em `marts.dim_loja` e **nao
existe em `raw.shopee_orders`** (medido no EXP-1E). Nao e' conta faltando — e'
marca fora do alcance desta fonte, e por isso nunca entra em
`missing_accounts`.

### Politica de identificadores

**`marketplace_order_id` (o `order_sn` da Shopee) NAO e' servido.**

Esta API **nao tem autenticacao**: nenhum router declara dependencia de auth, o
CORS e' aberto conforme configuracao e a instancia do Render e' publica. Um
identificador que permite localizar o pedido no painel do marketplace nao vai
numa rota assim.

Um hash **sem chave** tambem nao resolveria: o espaco de `order_sn` e' curto e
enumeravel, e uma tabela arco-iris o reverte. Por isso `order_ref` e'
**HMAC-SHA256 truncado**, com chave em `expedicao_order_ref_secret`:

* segredo **vazio** (default) -> `order_ref = null` e
  `limitations.order_identifier_withheld = true`;
* segredo configurado -> valor opaco, estavel entre execucoes, inutil sem a chave.

Servir o identificador real e' decisao de **controle de acesso**, nao de
serving: exige autenticacao na API, e isso e' outro gate.

### Consistencia do batch

Antes de montar a resposta o servico exige: um unico `refresh_batch_id` na fila,
um unico `effective_at`, resumos do MESMO lote, um unico `snapshot_hour` e
`observed_at == effective_at`. Qualquer divergencia falha FECHADA com
`availability=unavailable` e `unavailable_reason=inconsistent_batch`. Combinar
fila nova com resumo velho mostraria um backlog que a tendencia nao explica.

### Estados de erro

| Situacao | Resposta |
|---|---|
| flag desligada | 200 · `unavailable` · `feature_flag_disabled` |
| nenhuma fotografia publicada | 200 · `unavailable` · `no_snapshot_published` |
| batch inconsistente | 200 · `unavailable` · `inconsistent_batch` |
| filtro/ordenacao/janela invalidos | 422 com mensagem fixa |
| banco indisponivel | 503 |

`unavailable` responde **200**, nao 500: o cliente perguntou algo valido e a
resposta e' "existe, mas nao ha o que servir". Nenhuma colecao some do payload e
nenhuma medida ausente vira zero — totais ficam `null`.

### Indices usados

`pk_expedicao_fila_atual (channel, shop_account, marketplace_order_id)` cobre o
recorte por canal e o desempate da paginacao; `idx_efa_acao (channel,
deadline_status, brand)` atende o filtro por situacao de prazo com marca;
`idx_efa_over_48h` e `idx_efa_stalled` sao parciais e atendem esses dois
filtros; `idx_err_tendencia (channel, brand, snapshot_hour DESC)` serve a
tendencia.

### Limitacoes

* Fotografia manual: envelhece ate alguem rodar o refresh.
* O watermark mede **leitura**, nao chegada de pedido novo: um carregador que
  reescreve linhas antigas mantem o alerta verde.
* Sem identificador de pedido enquanto a API nao tiver autenticacao.
* Somente Shopee. ML, TikTok e o calendario de dias uteis estao fora.
* Sem UI, sem MCP, sem alerta externo — a API nao notifica ninguem.

---

## Tela da Expedicao (EXP-2B)

**Estado: implementada e ATIVA em producao (EXP-2C1-P).**
`NEXT_PUBLIC_EXPEDICAO_ENABLED=true` na Vercel, com rebuild feito — a variavel e'
inlinada em tempo de build. `/expedicao` responde **200** e o item de menu
aparece.

O fail-closed continua valendo no codigo: a variavel nasce ausente, ausente
significa `false`, e nesse estado a rota volta a **404** com o menu sem o item.
Qualquer valor diferente da string exata `"true"` resolve `false`.

A tela declara **um unico landmark `main`** — o do `AppShell`. Ela abria um
segundo, corrigido no EXP-2C1-H1; `apps/web/tests/landmark-main.test.ts` trava a
invariante.

### Rota e superficie

`/expedicao` consome exclusivamente `GET /api/v1/expedicao` e
`GET /api/v1/expedicao/trend`. Nao ha outra chamada, nao ha escrita e nao ha
calculo operacional refeito no cliente: toda classificacao ja vem do pipeline, e
refaze-la faria dois consumidores verem coisas diferentes da mesma fotografia.

Blocos: cabecalho com os relogios e avisos, cartoes de KPI, frescor e cobertura
por marca, backlog por conta, evolucao horaria por conta e fila paginada com
filtros.

### Os tres relogios

| Campo | Mede | Onde aparece |
|---|---|---|
| `snapshot_age_hours` | ha quanto tempo a FOTOGRAFIA foi publicada | cabecalho |
| `source_age_hours` | ha quanto tempo a FONTE foi lida | tabela de frescor |
| `oldest_row_age_hours` | idade do pedido mais antigo do backlog | tabela de frescor, como CONTEXTO |

Sao colunas separadas com rotulos distintos de proposito. Pedido antigo **nao**
significa fonte desatualizada — confundir os dois fez as quatro marcas nascerem
vermelhas no primeiro piloto (EXP-1E).

`source_advanced` aparece rotulado como **metadado** e nao altera veredito
nenhum.

### Limiar de 48h

O cartao se chama **"Acima de 48h (limiar interno da Torre)"** e carrega a
ressalva no proprio cartao: e' limiar operacional **interno**, nao SLA do
marketplace nem promessa ao cliente. O prazo contratual aparece em "Vencidos" e
"Vence em 24h", que vem do `ship_by_date` da Shopee. Sao dimensoes ortogonais —
medimos 206 pedidos com mais de 48h ainda dentro do prazo nativo.

Nao existe cartao "entre 24h e 48h": essa interseccao nao existe no contrato, e
calcula-la na tela seria agregacao que a API nao fez.

### Cobertura e Kokeshi

Conta esperada ausente vira aviso de **conta faltando**; conta observada sem
cadastro vira **conta inesperada**. Sao avisos diferentes.

**Kokeshi** aparece so' como marca **fora do escopo**, com o texto explicito de
que e' ausencia de cobertura e nao backlog zero. Ela nao entra em denominador,
nao aparece na tabela de contas e nunca e' exibida como zero.

### Carga manual

`load_mode = manual_snapshot` e o aviso **"Sem automacao: a fotografia so' avanca
quando alguem executa o refresh manualmente"** ficam sempre visiveis. Acima de
6h a tela acrescenta um aviso de fotografia antiga, sem alterar numero nenhum.

### Identificadores

A tela **nao recebe e nao exibe** `order_sn`. `order_ref` e' opaco e opcional:

* quando a API o omite (default), a coluna de referencia simplesmente **nao
  aparece** — nada de coluna vazia nem de mensagem sugerindo erro;
* quando vem, e' texto monoespacado e **nunca vira link** para o marketplace;
* a tela nao tenta reconstruir nem inferir o identificador real.

### Estados cobertos

carregando · fotografia vazia · fila vazia por filtro · 422 · 5xx/rede · backend
desligado (`feature_flag_disabled`) · sem fotografia publicada · batch
inconsistente · fotografia antiga · frescor desconhecido · cobertura parcial ·
serie de tendencia vazia, parcial e com erro.

Batch inconsistente **nao mostra numero parcial**: os KPIs e a fila so'
renderizam nos estados `ok` e `fila vazia por filtro`.

### Flags coordenadas

| Flag | Onde | Default no codigo | Estado em producao |
|---|---|---|---|
| `NEXT_PUBLIC_EXPEDICAO_ENABLED` | Vercel (frontend) | ausente = `false` | **`true`** (EXP-2C1-P) |
| `EXPEDICAO_API_ENABLED` | Render (API) | `False` | **`true`** (EXP-2C1-P) |
| `expedicao_order_ref_secret` | Render (API) | `""` | **vazia** — `order_ref` desativado |

As duas precisam ser ligadas **em conjunto e nessa ordem**: primeiro a API,
depois a tela. Ligar so' a tela produz `unavailable` em toda requisicao; ligar
so' a API expoe a rota HTTP sem que ninguem a use — e a API **nao tem
autenticacao**.

Fail-closed em duas camadas: a rota chama `notFound()` no SERVIDOR antes de
qualquer render, e o item de menu so' entra com a flag ligada. Esconder apenas o
menu deixaria a URL direta acessivel.

### Risco ACEITO: API sem autenticacao

Mesmo sem PII e sem `order_sn`, o payload carrega backlog, atrasos, contas,
marcas, transportadora e ritmo da operacao. O EXP-2C0 mediu que a Torre **nao
tem autenticacao** fora de `/api/mcp` (Auth0).

**O responsavel aceitou explicitamente a exposicao publica temporaria desses
dados no EXP-2C1-P.** Autenticacao ficou como **divida futura** e nao bloqueia a
operacao atual; enquanto nao existir, `order_ref` permanece desativado e o
identificador do pedido nao e' servido.

### Ativacao e rollback

Ativacao EXECUTADA em 17/09/2026 (EXP-2C1-P), nesta ordem:
1. republicacao da fotografia pelo `--apply` manual;
2. `EXPEDICAO_API_ENABLED=true` no Render + deploy manual do backend, conferindo
   `GET /api/v1/expedicao`;
3. `NEXT_PUBLIC_EXPEDICAO_ENABLED=true` na Vercel e **rebuild** (a variavel e'
   inlinada em tempo de build);
4. conferencia da tela contra o payload do mesmo `refresh_batch_id`.

O EXP-2C1-S validou o resultado em producao (129 asserções, 3 viewports) e
registrou um unico achado — o landmark `main` duplicado, corrigido no
EXP-2C1-H1.

Rollback: remover `NEXT_PUBLIC_EXPEDICAO_ENABLED` e refazer o build — a rota
volta a 404 e o menu some. Depois desligar `expedicao_api_enabled`. Nenhum dado
e' apagado; a fotografia publicada segue no banco.

### Limitacoes

* Somente Shopee. ML, TikTok e calendario de dias uteis estao fora.
* A tela nao notifica ninguem: nao ha alerta externo, e-mail nem MCP.
* **Sem automacao: a fotografia envelhece ate alguem rodar o refresh.** Nao ha
  DAG nem Scheduler; a automacao segue pendente.
* A tendencia depende de quantas fotografias existirem.
* Kokeshi continua **fora da cobertura** — declarada em `brands_not_covered`,
  nunca como zero.
* `order_ref` desativado enquanto a API nao tiver autenticacao.

---

## Caso de referencia

Medicao de 15/09/2026, fixada em teste:

| | aguardando | overdue | due_24h | on_time | s/prazo | >48h |
|---|---|---|---|---|---|---|
| apice | 281 | 81 | 95 | 67 | 38 | 130 |
| barbours | 514 | 17 | 35 | 430 | 32 | 167 |
| lescent | 103 | 0 | 17 | 61 | 25 | 6 |
| rituaria | 35 | 3 | 2 | 30 | 0 | 4 |
| **total** | **933** | **101** | **149** | **588** | **95** | **307** |

`>48h` sobe entre execucoes (307 -> 316 -> 320 em ~2h) porque pedidos cruzam a
fronteira com a fonte parada: e a prova de que recomputar a cada execucao
importa.

Baseline exclui `CANCELLED` com pickup preenchido: 46 de 23.755 (0,19%).
