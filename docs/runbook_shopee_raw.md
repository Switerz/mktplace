# Runbook — Fase Raw Shopee (inventário, contrato e carga real em `raw.shopee_*`)

Status: **Fase 1 (inventário/contrato), Fase 2 (carga real) e endurecimento pós-carga concluídos (2026-07-03 a 2026-07-04).** `raw.shopee_ingestion_file`, `raw.shopee_order_item_export`, `raw.shopee_shop_stats_export` e `raw.shopee_ads_export` existem no Data Mart e contêm o backfill completo de `shopee/` — **384.882 linhas, reconciliação 100% limpa contra a primary**. Staging/gold continuam fora de escopo.

**Endurecimento pós-carga (2026-07-04)** — nenhuma linha alterada, única escrita: um índice novo:
- Índice único adicional null-safe em `raw.shopee_ingestion_file` (seção 6.5) — a constraint original permitia duplicidade quando `sheet_name IS NULL` (caso de todo arquivo `ads`).
- `writer.insert_file` não usa mais `str(exceção)` para erros de INSERT — só tipo, `pgcode` e `constraint_name` (seção 6.7).
- `reconcile.py` usa `execution_options(postgresql_readonly=True)` (proteção real desde a primeira query) em vez de `SET default_transaction_read_only` (que não protegia a transação corrente).
- Reconciliação imediata pós-backfill passou a usar a **primary** (a própria credencial de escrita, em sessão somente-leitura) em vez da réplica — elimina a ambiguidade de lag descrita na seção 7.

## 1. O que foi entregue

- Inventário técnico completo de `shopee/{brand}/...` (arquivos, hashes, headers, schemas) — reexecutável a qualquer momento via `--inventory`.
- Auditoria de PII nos exports de pedidos, com decisão tomada (raw integral, ver seção 5).
- DDL versionado e **executado** para as 4 tabelas em `raw.shopee_*` (`db/sql/raw/shopee_raw_ddl.sql`).
- Loader `pipelines/ingestion/load_shopee_raw.py` com `--inventory`, `--dry-run` e `--apply {--create-schema|--pilot|--backfill}`, todos funcionais.
- Piloto (3 menores arquivos, 1 por `source_type`) validado com sucesso, incluindo teste de idempotência (2ª passada — zero linhas novas).
- Backfill completo: 120 arquivos elegíveis, 117 inseridos + 3 já ingeridos pelo piloto (idempotência), 0 falhas.
- 89 testes cobrindo inventário, PII, hashing/JSON, guardrails do CLI, carregamento seguro do secret, preflight de conexão, execução do DDL e escrita append-only (todos com conexões falsas — nenhum banco real tocado pelos testes).

**Neon e PostgreSQL local nunca foram tocados** nesta fase — nenhum código novo importa `pipelines.common.db` (verificado por teste estrutural).

## 2. Onde a raw mora (achados do preflight)

O `raw` de destino é o **schema `raw` do Data Mart** (o mesmo Postgres que hospeda `raw.tiktok_shop_orders`, `raw.ml_orders` e ~200 outras tabelas de fontes completamente diferentes — Facebook Ads, GA4, Tiny, Bling, Yampi, Shopify, WhatsApp, Intelipost etc.).

Achados confirmados por preflight real (2026-07-03):

- **Correção em relação ao relatório da Fase 1**: a credencial `DATAMART_DATABASE_URL` NÃO é superuser (`rolsuper=false`, verificado via `pg_roles`, não apenas inferido pelo nome de usuário). O relato anterior de "superuser" foi uma inferência a partir do nome `postgres` + privilégio de `CREATE` em `raw`, e estava impreciso.
- **`DATAMART_DATABASE_URL` é uma READ REPLICA** (`pg_is_in_recovery() = true`), não a primary — achado feito durante a reconciliação pós-backfill (ver seção 7).
- A credencial de escrita (`DATAMART_SHOPEE_WRITE_URL`) também não é `rolsuper`, mas é membro do grupo `rds_superuser` (padrão AWS RDS) — risco aceito e documentado explicitamente pelo usuário, não corrigido nesta fase.
- Confirmado via `system_identifier` (`pg_control_system()`) que a credencial de escrita aponta para o **mesmo cluster físico** da credencial de leitura, mesmo os hostnames/IPs resolvendo diferente (rede privada via VPN vs. endpoint direto) — o preflight usa esse fingerprint, não comparação de hostname em texto, para confirmar o alvo.
- A extensão `pgcrypto` **não está instalada** — HMAC de PII precisa ser calculado em Python, não em SQL (não foi necessário nesta fase, ver seção 5).
- Convenção observada em `raw.tiktok_shop_orders`/`raw.ml_orders` (PK serial, `uk_`/`idx_`) foi seguida no DDL novo.

## 3. Diferença deliberada de `raw.shopee_*` vs `raw.tiktok_shop_orders`/`raw.ml_orders`

As tabelas do TikTok/ML em `raw` têm `UNIQUE(order_id)` — são efetivamente upsert/dedupe por pedido. As tabelas `raw.shopee_*` são **append-only por linha física de arquivo**, sem chave de negócio: o mesmo pedido aparece várias vezes entre exports sobrepostos, e isso é esperado — deduplicação de negócio fica para uma staging futura, fora do escopo desta fase.

## 4. Como rodar

```bash
# Inventário técnico:
uv run --no-project --with openpyxl --with pydantic-settings --with sqlalchemy --with psycopg2-binary \
  python -m pipelines.ingestion.load_shopee_raw --inventory

# Dry-run completo (sem qualquer conexão de escrita):
uv run --no-project --with openpyxl --with pydantic-settings --with sqlalchemy --with psycopg2-binary \
  python -m pipelines.ingestion.load_shopee_raw --dry-run

# Filtros disponíveis em --inventory/--dry-run/--apply --backfill:
  --source orders|shop-stats|ads|all   (default: all)
  --brand apice|barbours|kokeshi|lescent|rituaria
  --file <substring do caminho relativo>   (só inventory/dry-run)
  --data-path <override de SHOPEE_DATA_PATH>

# Fase 2 — requer .env.shopee-write.local na raiz do repo (NUNCA .env, nunca
# variável de ambiente do processo/máquina), com exatamente:
#   DATAMART_SHOPEE_WRITE_URL=postgresql://...
#   I_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1
# Esse arquivo é lido por dotenv_values() para um dict local — nunca toca
# em os.environ, nunca é impresso, é validado como ignorado+não-rastreado
# pelo git antes de ser lido.

uv run --no-project --with pydantic-settings --with sqlalchemy --with psycopg2-binary --with python-dotenv --with openpyxl \
  python -m pipelines.ingestion.load_shopee_raw --apply --create-schema   # uma vez, já feito

uv run --no-project --with pydantic-settings --with sqlalchemy --with psycopg2-binary --with python-dotenv --with openpyxl \
  python -m pipelines.ingestion.load_shopee_raw --apply --pilot           # já feito

uv run --no-project --with pydantic-settings --with sqlalchemy --with psycopg2-binary --with python-dotenv --with openpyxl \
  python -m pipelines.ingestion.load_shopee_raw --apply --backfill        # já feito — idempotente, seguro rodar de novo
```

`--dry-run`/`--inventory` sobre os ~118 MB / 121 arquivos de `shopee/` levam ~4-5 minutos (leem cada arquivo por completo). O backfill real (120 arquivos, 384.882 linhas) levou alguns minutos a mais por causa dos INSERTs.

## 5. PII — decisão tomada e aplicada

Confirmado por inspeção real dos headers: o export `Order.all*.xlsx` tem colunas de PII direta (nome do destinatário, telefone, endereço, CEP; CPF só no template da marca apice) que **não** são usadas hoje pelo agregador de produção (`_parser.py` só lê 11 de ~65 colunas), mas **entraram inteiras** no `raw_payload JSONB`, porque a raw preserva a linha inteira.

**Decisão do usuário (2026-07-03): raw integral, com PII, autorizada explicitamente.** Não foi aplicado HMAC nem remoção de campos — `raw_payload` contém os valores originais completos, exatamente como exportado pela Shopee. Catálogo de classificação em `pipelines/ingestion/shopee_raw/pii.py` (mantido para referência/auditoria futura, mesmo não bloqueando a carga).

Proteção aplicada: `REVOKE ALL ON <tabela> FROM PUBLIC` nas 4 tabelas (ver DDL). **Importante: isso não é uma garantia absoluta de acesso.** `REVOKE ALL FROM PUBLIC` só remove o acesso implícito do pseudo-role `PUBLIC` — **nunca afeta grants que já existiam para roles NOMEADAS**. **Achado não corrigido, risco aceito explicitamente pelo usuário**: as 4 tabelas herdaram privilégios padrão do schema `raw` configurados por outra equipe antes desta fase (`ALTER DEFAULT PRIVILEGES`, não algo criado por este DDL) para 6 roles internas: `postgrest` (SELECT), `airflow` (SELECT/INSERT/UPDATE/DELETE), `web_user` (SELECT/INSERT/UPDATE/DELETE), `sql_runner`/`datamart_ro`/`datamart_rw` (SELECT). O usuário confirmou que essas 6 roles são internas/controladas e autorizou manter o acesso herdado — nenhuma ação foi tomada (nenhum `REVOKE`/`GRANT`/`ALTER DEFAULT PRIVILEGES` adicional). Isso significa que, tecnicamente, `airflow`/`web_user` podem fazer UPDATE/DELETE nas tabelas `raw.shopee_*` — o append-only é uma garantia do **loader** (código nunca emite essas instruções, verificado por teste), não uma garantia de permissões do banco.

## 6. Idempotência e algoritmo de escrita (implementado e testado com carga real)

Só INSERT, nunca UPDATE/DELETE/DROP/TRUNCATE — confirmado por teste estrutural sobre o texto do DDL e do loader. Mecanismo:

1. Todo o processamento de **um arquivo** roda em **uma transação** (`pipelines/ingestion/shopee_raw/writer.py::insert_file`).
2. Um `file_id` é reservado via `nextval('raw.shopee_ingestion_file_file_id_seq')` no início da transação.
3. As linhas-filhas (`raw.shopee_order_item_export`/`_shop_stats_export`/`_ads_export`) são inseridas usando esse `file_id` — a FK para `raw.shopee_ingestion_file` é `DEFERRABLE INITIALLY DEFERRED`, então a integridade só é checada no COMMIT.
4. `raw.shopee_ingestion_file` (sempre `ingestion_status='success'`, é o único valor permitido pelo CHECK) é o **último INSERT da transação** — funciona como marca de commit.
5. Idempotência técnica: `UNIQUE(file_sha256, sheet_name)` — antes de processar um arquivo, o loader verifica se essa chave já existe (comparação `IS NOT DISTINCT FROM`, já null-safe na consulta da aplicação); se sim, pula (`skipped_idempotent`) sem tocar em nada.
6. SHA-256 é conferido antes e depois da leitura do arquivo — se mudar no meio da carga, a transação é abortada (`FileChangedDuringReadError`) e nada é commitado.
7. Falhas nunca são persistidas: um arquivo que falha (leitura, parse, ou erro de INSERT) faz `ROLLBACK` completo e não deixa nenhum rastro em `raw.shopee_*` — `raw.shopee_ingestion_file` só tem linhas de arquivos 100% bem-sucedidos. Retry é manual e seguro por natureza (idempotência de sucesso não é afetada por tentativas anteriores que falharam). **Mensagens de erro nunca usam `str(exceção)`** (que pode conter DETAIL/statement/valores de linha do servidor) — só tipo da exceção, `pgcode` e `constraint_name` quando existirem, e o caminho relativo do arquivo (`writer._safe_error_summary`).

### 6.5 Endurecimento da chave de idempotência (null-safe, 2026-07-04)

`UNIQUE(file_sha256, sheet_name)` sozinha **não** bloqueia duplicidade quando `sheet_name IS NULL`, porque o Postgres trata cada `NULL` como distinto de qualquer outro `NULL` numa `UNIQUE` constraint. Isso afeta todo arquivo `ads` (CSV, sem sheet — `sheet_name` sempre `NULL`): dois arquivos ads byte-a-byte idênticos poderiam, antes desta correção, ter sido inseridos duas vezes se a checagem da aplicação fosse contornada. Diagnóstico read-only confirmou zero duplicidades na base já carregada (120 manifestos, nenhuma colisão em `(file_sha256, COALESCE(sheet_name, ''))`); em seguida foi criado, como único passo de escrita autorizado, um índice adicional:

```sql
CREATE UNIQUE INDEX uk_shopee_ingestion_file_sha256_sheet_nullsafe
    ON raw.shopee_ingestion_file (file_sha256, (COALESCE(sheet_name, '')));
```

A constraint original **não foi removida** — os dois índices coexistem. Nenhuma linha foi alterada.

**Validado com dados reais**: piloto rodou os 3 arquivos duas vezes seguidas — 1ª passada inseriu tudo, 2ª passada pulou os 3 por idempotência (zero linhas novas, zero updates). O backfill reprocessou os mesmos 3 arquivos do piloto e confirmou o mesmo comportamento (`skipped_idempotent`).

## 7. Achado operacional: `DATAMART_DATABASE_URL` é uma read replica

Descoberto ao reconciliar logo após o backfill: a contagem via `DATAMART_DATABASE_URL` ficou abaixo do esperado por alguns minutos (`pg_is_in_recovery() = true` confirma que é uma réplica de leitura, não a primary). Não é perda de dado — é lag de replicação.

**Regra adotada (2026-07-04)**: reconciliação **imediatamente após uma escrita** (dentro de `run_apply_backfill`) usa a **primary** — a própria credencial de escrita (`DATAMART_SHOPEE_WRITE_URL`), aberta com `execution_options(postgresql_readonly=True)` (nunca escreve, mas nunca sofre lag, porque é a mesma instância que acabou de commitar). A réplica (`DATAMART_DATABASE_URL`) continua válida para conferências **posteriores** (o lag eventualmente se resolve), mas nenhum código desta fase trata uma contagem incompleta da réplica como sucesso silencioso: se uma reconciliação contra réplica algum dia for usada de novo para checagem imediata, uma contagem de arquivos abaixo do esperado deve reprovar (`exit code != 0`), nunca ser mascarada por retry silencioso.

Reconciliação final confirmada contra a primary: **384.882 linhas no manifesto = 384.882 linhas-filhas, zero órfãs, zero duplicidades, zero problemas.**

## 8. Limitações conhecidas

- **Ads sem granularidade diária** — cada linha do CSV é um anúncio agregado por período; a raw preserva isso (não distribui/estima).
- **Schema drift confirmado**: `orders` da marca `apice` tem header diferente das outras 4 marcas (tem `CPF do Comprador`, não tem `Domestic Delivered Date`/`Shopee Owned`/`Pedido FBS`). `ads` de `kokeshi` tem uma coluna extra (`Segmentação de Público`). `raw_payload` absorve isso naturalmente (JSONB sem schema fixo); qualquer staging futura deve tratar por nome de coluna, nunca por posição.
- **Header duplicado**: `Cidade` aparece duas vezes no header original de todas as marcas — a segunda ocorrência vira `"Cidade__col<posição>"` em `raw_payload` para não sobrescrever a primeira.
- **Achado colateral no parser de produção (fora do escopo desta fase, não corrigido aqui)**: `pipelines/connectors/shopee/_parser.py::_parse_float` não remove separador de milhar. Um valor como `"1.234,56"` vira `"1.234.56"` → `ValueError` → `0.0`. Qualquer pedido com `Subtotal do produto`/`Total global`/comissão ≥ R$ 1.000 é silenciosamente zerado no agregador de produção atual — recomendo abrir como bug separado.
- **Permissões herdadas do schema `raw`** para 6 roles internas — ver seção 5 (risco aceito, documentado, não corrigido).
- **`shopee/.impeccable/hook.cache.json`**: arquivo não relacionado à Shopee, corretamente classificado como desconhecido pelo inventário, não carregado, não removido.
- **`--dry-run`/`--inventory` leem cada arquivo por completo (~4-5 min)**: aceitável para uma operação manual pontual; otimização de leitura única fica para trabalho futuro se o volume crescer muito.
- Console Windows pode exibir acentos como `�` (mojibake) — é só o codepage do terminal; os dados armazenados (UTF-8/JSONB) estão corretos.

## 9. Rollback operacional

Design é INSERT-only por transação de arquivo (seção 6) — não existe "rollback manual" a executar no dia a dia: um arquivo mal processado nunca deixa uma linha parcial commitada. Remover um arquivo já ingerido corretamente (ex: descoberta de export corrompido semanas depois) exigiria DELETE explícito fora do papel de escrita do loader — decisão de DBA, não deste código.

## 10. Backfill futuro (novos exports)

Rodar `--apply --backfill` novamente processa só os arquivos novos/alterados (idempotência por `file_sha256`) — os 120 arquivos já carregados são pulados automaticamente. Não há agendamento automático desta carga; é operação manual, como o restante da pipeline Shopee hoje.
