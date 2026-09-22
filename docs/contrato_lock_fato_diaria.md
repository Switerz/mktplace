# Contrato do advisory lock de `marts.fact_marketplace_daily_performance`

**Gate SH-AUTO-1.** Este documento é a fonte para quem escrever o runner Shopee
no Airflow (gate SH-AUTO-4). O código de referência é
[`pipelines/ingestion/fato_diaria_lock.py`](../pipelines/ingestion/fato_diaria_lock.py).

Este gate entregou **somente código e testes**. Nenhuma execução de pipeline,
nenhum refresh manual, nenhuma escrita em banco produtivo, nenhuma DAG,
nenhuma Connection e nenhum GRANT.

---

## 1. O problema

Cinco caminhos escrevem na mesma tabela e, até este gate, **nenhum deles
adquiria lock**:

| `--source` | SQL | `marketplace_id` | Natureza da escrita |
| --- | --- | --- | --- |
| `shopee` | `PATCH_SHOPEE_ORDERS_SQL` | 3 | **parcial** — pedidos, cancelamento, entrega, financeiro |
| `shopee-stats` | `PATCH_SHOP_STATS_SQL` | 3 | **parcial** — funil e GMV autoritativo |
| `shopee-ads` | `PATCH_ADS_SQL` | 3 | **parcial** — os 8 campos de mídia |
| `ml` | `UPSERT_SQL` | 2 | linha inteira |
| `tiktok` | `UPSERT_SQL` | 1 | linha inteira |

Os três da Shopee são parciais **por desenho** (Gates SD2-C e R2.1): cada um
escreve só as colunas de que é fonte e conta com o que os outros publicaram na
mesma linha `(date, loja_id, marketplace_id)`. Dois deles em paralelo produzem
uma linha montada com metades de instantes diferentes — e **nada fica
vermelho**, porque cada `ON CONFLICT DO UPDATE` isolado é válido.

Hoje isso é contido por acidente: o Task Scheduler é uma esteira só. Deixa de
ser quando o runner Shopee do Airflow entrar e passarem a existir duas.

---

## 2. As chaves

Forma de **um argumento `bigint`**. Derivação: `918130000 + marketplace_id`,
com `marketplace_id` conforme `marts.dim_marketplace`.

| Marketplace | `marketplace_id` | Chave | Quem usa |
| --- | --- | --- | --- |
| TikTok Shop | 1 | **`918130001`** | `--source tiktok` |
| Mercado Livre | 2 | **`918130002`** | `--source ml` |
| Shopee | 3 | **`918130003`** | `--source shopee`, `shopee-stats`, `shopee-ads` **e o futuro runner do Airflow** |

**Os três writers Shopee usam a mesma chave.** Chavear por `source` daria três
locks distintos e nenhuma exclusão — eles disputam a mesma linha.

**Marketplaces diferentes não se excluem, e isso é deliberado.** A chave da
tabela é `(date, loja_id, marketplace_id)` e uma loja pertence a um único
marketplace, então ML e Shopee nunca disputam a mesma linha. Serializar canais
independentes só alongaria o `full_daily`.

### 2.1 Para o runner do Airflow

Use o **literal**, não a fórmula, e não importe o módulo (repositórios e imagens
distintos):

```sql
SELECT pg_try_advisory_lock(918130003);   -- Shopee
```

🔴 **Um argumento, não dois.** `pg_try_advisory_lock(int, int)` ocupa um espaço
de chaves **diferente** no PostgreSQL: `pg_try_advisory_lock(918130, 3)` não se
excluiria com esta chave. Seria um lock que não tranca nada, e passaria em
qualquer teste que não coloque as duas esteiras frente a frente. Há um teste
que coloca: `test_forma_de_dois_argumentos_nao_se_exclui_com_a_nossa`.

O valor está travado por `test_chaves_congeladas`. Mudá-lo de um lado só
produz duas esteiras publicando juntas, sem erro em nenhuma delas.

---

## 3. Semântica

| Regra | Comportamento |
| --- | --- |
| **Aquisição** | `pg_try_advisory_lock` — nunca a variante bloqueante. Espera silenciosa até o `execution_timeout` do step daria o diagnóstico errado para o que é disputa. |
| **Momento** | Antes de `_start_sync_run` e antes de qualquer leitura da fonte. |
| **Escopo** | Lock de **sessão**, em conexão dedicada em autocommit. Não transacional: o fluxo commita várias vezes antes do UPSERT (a auditoria commita na hora), e um `pg_advisory_xact_lock` morreria no primeiro commit, deixando a extração descoberta. |
| **Ocupado** | `FatoDiariaLockUnavailable`, imediata, antes de ceder o controle. Zero leitura, zero escrita, zero linha de auditoria. |
| **Retry** | **Nenhum.** Lock ocupado significa que o outro escritor está com a fotografia na mão. |
| **Exit code** | `75` (`EX_TEMPFAIL`), distinto do `1` genérico no log do `full_daily`. |
| **Liberação** | `pg_advisory_unlock` no `finally`, e o fechamento da conexão como garantia dura — lock de sessão morre com a sessão, inclusive se o processo cair. **Não existe lock preso a limpar na mão.** |
| **Cleanup** | Nunca substitui a exceção que trouxe o fluxo até ali. Um commit indeterminado que saísse daqui como "erro ao liberar lock" seria lido no runbook como "nada foi publicado" — afirmação falsa. |

### 3.1 O que o lock **não** faz

- **Não torna os writers idempotentes entre si.** Eles continuam parciais; o
  lock só garante que não se sobrepõem no tempo.
- **Não protege outras tabelas.** `marts.fact_marketplace_region_daily`,
  `fact_shopee_fbs_daily` e as demais têm (ou não têm) locks próprios.
- **Não substitui reconciliação.** Publicação serializada ainda pode publicar
  número errado.

---

## 4. Operação

**Sintoma:** step do `full_daily` sai com código 75 e a mensagem nomeia a chave.

**O que significa:** outra esteira estava publicando naquele marketplace. Nada
foi lido nem escrito pelo processo recusado, e nenhuma linha de auditoria foi
aberta — não há `running` órfã para limpar.

**O que fazer:** identificar a outra esteira antes de repetir.

```sql
-- quem detém a chave da Shopee, agora
SELECT a.pid, a.application_name, a.client_addr, a.state,
       a.query_start, left(a.query, 120) AS query
FROM pg_locks l
JOIN pg_stat_activity a USING (pid)
WHERE l.locktype = 'advisory'
  AND ((l.classid::bigint << 32) | l.objid::bigint) = 918130003
  AND l.granted;
```

**Nunca matar a sessão às cegas.** Um lock que persiste significa transação
viva; derrubá-la no meio de uma publicação parcial deixa a linha pela metade,
que é exatamente o que este lock existe para impedir.

Depois que a outra esteira terminar, o step pode ser reexecutado — é o mesmo
comando, e os upserts são idempotentes.

---

## 5. Risco aberto — não corrigido neste gate

🔴 **O parser manual soma de novo itens presentes em arquivos exportados
sobrepostos.**

`pipelines/connectors/shopee/_parser.py` lê **todos** os `Order.all*.xlsx` da
pasta da marca e acumula `subtotal` e `qty` por linha de SKU. O dedup por
`order_id` cobre apenas os campos *order-level* (`total_global`,
`commission_net`, `service_fee_net`, `freight_est`, via `max()`). Um pedido que
apareça em duas janelas exportadas tem as linhas de SKU **somadas duas vezes**.

Medido em 01–24/08/2026, pedidos presentes em mais de um arquivo: **barbours
1.007 · lescent 478 · rituaria 369 · apice 294 · kokeshi 10.161**.

Na fotografia publicada isso **ainda não se materializou**: o incremental só
reescreve 3 dias e os arquivos sobrepostos chegaram depois. A verificação dos
dias cobertos por dois e três arquivos (05/08, 10/08, 15–16/08, 24/08, 01/09,
07–08/09) deu razão ~0,97 contra a API, igual à dos dias de arquivo único.

⚠️ **Enquanto não houver um gate de deduplicação por arquivo vencedor, nenhum
backfill nem refresh manual amplo deve ser executado.** O caminho incremental de
3 dias segue seguro.

---

## 6. Gates seguintes

| Gate | Escopo |
| --- | --- |
| **SH-AUTO-1B** | Deduplicação do parser por arquivo vencedor, antes de qualquer backfill |
| SH-AUTO-2 | `gold.shopee_brand_daily` (dia × marca) no dbt corporativo |
| SH-AUTO-3 | Validação dos GRANTs da Connection `neon_marts` contra o contrato |
| SH-AUTO-4 | Runner Shopee no Airflow, consumindo a chave `918130003` deste documento |
