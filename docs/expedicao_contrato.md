# Contrato do refresh de expedicao — Gates EXP-1A / R / R2

**Estado: INERTE.** Nenhuma tabela criada, nenhuma migration, nenhum refresh
publicado. `--apply` para com mensagem explicita porque as tabelas nao existem.

| Frente | Estado |
|---|---|
| Migration | **nao criada** — a `016` esta reservada para a frente Full |
| Tabelas em producao | nao existem |
| Refresh publicado | nenhum |
| API / UI / MCP | nao iniciados |
| ML / TikTok / calendario | fora deste gate |
| `schedule_plan.py` / Airflow | nao integrados |

**Head Alembic: `015`.** A worktree `gate-full-1a` tem
`016_create_fact_ml_fulfillment_daily.py` ainda **nao commitada** (cria
`marts.fact_ml_fulfillment_daily` e `marts.fact_ml_fulfillment_listing_daily`).
Expedicao usa a **proxima revisao linear depois que a de Full for integrada** —
nunca a 016.

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

### DML necessario antes do primeiro apply — PLANO, nao executado

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

## Consumo futuro (API/MCP, nao implementados)

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
