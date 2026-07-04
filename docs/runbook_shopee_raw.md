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
- ~~**Achado colateral no parser de produção**: `_parse_float` não remove separador de milhar — qualquer valor ≥ R$ 1.000 seria zerado.~~ **Investigado e corrigido em 2026-07-04 (seção 11) — a hipótese estava certa sobre o defeito de código, mas errada sobre o impacto**: auditoria de 100% dos dados reais mostrou que nenhuma linha usa esse formato, logo nenhuma linha histórica foi afetada. Ver seção 11 para causa raiz, contrato do parser corrigido e quantificação completa.
- **Permissões herdadas do schema `raw`** para 6 roles internas — ver seção 5 (risco aceito, documentado, não corrigido).
- **`shopee/.impeccable/hook.cache.json`**: arquivo não relacionado à Shopee, corretamente classificado como desconhecido pelo inventário, não carregado, não removido.
- **`--dry-run`/`--inventory` leem cada arquivo por completo (~4-5 min)**: aceitável para uma operação manual pontual; otimização de leitura única fica para trabalho futuro se o volume crescer muito.
- Console Windows pode exibir acentos como `�` (mojibake) — é só o codepage do terminal; os dados armazenados (UTF-8/JSONB) estão corretos.

## 9. Rollback operacional

Design é INSERT-only por transação de arquivo (seção 6) — não existe "rollback manual" a executar no dia a dia: um arquivo mal processado nunca deixa uma linha parcial commitada. Remover um arquivo já ingerido corretamente (ex: descoberta de export corrompido semanas depois) exigiria DELETE explícito fora do papel de escrita do loader — decisão de DBA, não deste código.

## 10. Backfill futuro (novos exports)

Rodar `--apply --backfill` novamente processa só os arquivos novos/alterados (idempotência por `file_sha256`) — os 120 arquivos já carregados são pulados automaticamente. Não há agendamento automático desta carga; é operação manual, como o restante da pipeline Shopee hoje.

## 11. Correção do parser numérico e quantificação de impacto (2026-07-04)

Esta seção fecha o achado colateral registrado na seção 8 (separador de milhar). Escopo: apenas código, testes e documentação — **nenhuma escrita em banco, nenhum reprocessamento, nenhum commit foi feito como parte desta fase**.

### 11.1 Causa raiz confirmada

`pipelines/connectors/shopee/_parser.py::_parse_float` (orders) e `pipelines/connectors/shopee/_parser_ads.py::_parse_float` (ads) — duas implementações **divergentes** da mesma ideia — faziam apenas `replace(",", ".")` sem remover separador de milhar. Um valor `"1.234,56"` virava `"1.234.56"`, `float()` levantava `ValueError`, e a função devolvia `0.0` **sem log, sem contagem, sem qualquer rastro** — indistinguível de um valor real igual a zero. Uma terceira implementação (`_parse_brl_float` em `pipelines/ingestion/load_shopee_raw.py`, usada só para diagnóstico de reconciliação, nunca na carga real) já tratava o separador de milhar corretamente, mas retornava `None` em vez de levantar erro para valor inválido — três regras numéricas divergentes coexistiam no repositório para o mesmo tipo de dado.

### 11.2 Formatos efetivamente encontrados nas fontes (auditoria de 100% dos dados, não amostra)

| Fonte | Arquivos | Linhas/registros verificados | Formato numérico encontrado |
|---|---:|---:|---|
| `Order.all*.xlsx` (orders) | 85 | 383.298 | Decimal com ponto, sem milhar (`"1546.30"`). 100% das strings, inclusive valores ≥ R$ 1.000 (`"1098.30"`, `"1019.38"`, etc.). Zero ocorrências de vírgula, `R$`, NBSP, negativo, vazio ou `N/A` nas 6 colunas numéricas usadas pelo agregador. |
| `Dados*.csv` (ads) | 10 | 804 | Idêntico — decimal com ponto, sem milhar (`"1133.83"`, `"38147.83"`). Nota: o CSV usa `,` como delimitador de coluna — um número BR real (vírgula decimal) exigiria o campo entre aspas, o que é a provável razão pela qual o export sempre usa ponto decimal aqui. |
| `*.shopee-shop-stats.*.xlsx` | 25 | 755 linhas diárias úteis (780 linhas físicas na Raw — ver nota abaixo) | Inteiros puros (`"1077"`) para contagens; vírgula decimal só em percentuais sempre < 100 (`"3,84%"`) — tratado por `_parse_pct`, uma função diferente, já correta. Não usa `_parse_float`. |

Nenhum dos 3 tipos de export contém, hoje, um valor que dispare o bug do separador de milhar.

**Nota — 755 linhas diárias úteis vs. 780 linhas físicas na Raw (diferença de 25, uma por arquivo):** cada arquivo `*.shopee-shop-stats.*.xlsx` tem uma linha de **total do período** (linha física 1 da planilha, ex.: `"01/03/2026-31/03/2026"` com os totais agregados do mês inteiro) além das linhas diárias (a partir da linha física 4). O parser de produção (`_parser_shop_stats.py::_read_xlsx`) começa a ler em `rows[4:]` e **nunca usa essa linha de total** — ela não é um dia, e incluí-la quebraria a granularidade diária das métricas. A camada Raw (`pipelines/ingestion/shopee_raw/inventory.py::read_shop_stats_file`), por design, preserva **toda linha física do arquivo sem filtro de negócio** (mesmo princípio aplicado a orders e ads) — então ela grava essa linha de total como mais um `ParsedRow`, uma por arquivo. 25 arquivos × 1 linha de total cada = as 25 linhas a mais (755 + 25 = 780). Não é uma divergência a corrigir: é a diferença esperada entre "grão de negócio útil" (produção) e "espelho append-only de toda linha física exportada" (Raw) — o mesmo princípio documentado em `docs/data_contracts.md` seção 7 para orders/ads. Confirma também a soma total do backfill: 383.298 (orders) + 780 (shop-stats) + 804 (ads) = 384.882 linhas-filhas.

### 11.3 Impacto histórico quantificado — ZERO linhas afetadas

Comparação linha a linha entre o parser **antigo** (`_parse_float` pré-correção, executado verbatim) e o **novo** (`parse_brl_float`), sobre 100% dos dados reais — não uma amostra:

| Fonte | Linhas/registros comparados | Valores com resultado divergente | Diferença nas somas totais |
|---|---:|---:|---:|
| Orders (6 colunas × 383.298 linhas) | 2.299.788 valores | **0** | R$ 0,00 em todas as 6 colunas (`Quantidade`, `Subtotal do produto`, `Total global`, `Taxa de comissão líquida`, `Taxa de serviço líquida`, `Valor estimado do frete`) |
| Ads (4 colunas × 804 registros) | 3.216 valores | **0** | R$ 0,00 em todas as 4 colunas (`Impressões`, `Cliques`, `Despesas`, `GMV`) |

Somas totais idênticas ao centavo antes/depois (ex.: `Subtotal do produto` = R$ 24.859.859,62 nas duas versões; `GMV` de ads = R$ 16.887.993,55 nas duas versões). Não há distribuição por brand/mês/tipo de arquivo a reportar porque não há nenhuma linha divergente em nenhuma combinação. Não há menor/maior diferença a reportar pelo mesmo motivo (conjunto vazio). Não há valores ambíguos remanescentes: o formato US (vírgula de milhar, rejeitado explicitamente desde o endurecimento de 2026-07-04 — seção 11.8) não foi encontrado em nenhuma linha; se fosse, o parser corrigido levantaria `ShopeeNumericParseError` (fail-fast) em vez de aceitar ou converter incorretamente.

**Conclusão prática**: `fact_marketplace_daily_performance` (via `_parser.py`/`_parser_ads.py`), `marts.fact_shopee_product_monthly` (via `apps/api/etl/load_shopee_products.py` — mesma classe de limitação, endurecida em sessão dedicada, ver 11.5) e `raw.shopee_*` (que preserva os valores originais em texto, sem parsear) **nunca estiveram incorretos por causa deste bug**. O bug era real no código; o impacto medido nos dados é zero.

### 11.4 Contrato do parser corrigido

Documentado em `docs/data_contracts.md` (seção "Contrato do parser numérico") e no docstring de `pipelines/connectors/shopee/_numeric.py::parse_brl_float`. Resumo da decisão de design:

- Vazio/ausente (`None`, `""`, `"-"`, `"N/A"`) → `None` (sem valor, contribui 0 na agregação).
- Valor não vazio inválido, ou não finito (NaN/±Infinity) → `ShopeeNumericParseError`, **nunca `0.0`**. A decisão final (seção 11.8, 2026-07-04) é **fail-fast**: os chamadores (`_parser.py`, `_parser_ads.py`) relançam a exceção com contexto sanitizado (marca/arquivo/linha ou índice do anúncio/campo — nunca o valor bruto, nunca buyer/order_id) e a deixam propagar, interrompendo a leitura da fonte com exit code != 0. Publicar uma métrica financeira construída sobre um valor não interpretável com status de sucesso foi considerado um risco maior do que perder a agregação daquele arquivo — o orquestrador (`pipelines/ops/orchestrate.py`) já marca o step como `FAILED` e segue com as fontes independentes seguintes.
- Formato US (`"1,234.56"`) → rejeitado explicitamente (mesma exceção), nunca convertido para um valor incorreto — ver seção 11.8.
- Três implementações divergentes (`_parser.py::_parse_float`, `_parser_ads.py::_parse_float`, `load_shopee_raw.py::_parse_brl_float`) foram consolidadas em uma única função compartilhada (`pipelines/connectors/shopee/_numeric.py::parse_brl_float`), eliminando a divergência de regras.

### 11.5 Achado relacionado — corrigido em sessão dedicada (2026-07-04)

`apps/api/etl/load_shopee_products.py::_clean_numeric` (usada para `Subtotal do produto` → `marts.fact_shopee_product_monthly.gmv`) tinha a mesma classe de defeito (regex de limpeza baseada em pandas não removia separador de milhar antes de `pd.to_numeric`, e usava `fillna(0.0)` — mesmo silêncio). **Endurecido nesta sessão** com uma implementação LOCAL (`_parse_brl_float`/`ShopeeNumericParseError` no próprio `load_shopee_products.py`), não importada de `pipelines/connectors/shopee/_numeric.py`:

- **Decisão de não importar, confirmada empiricamente**: `apps/api` é empacotado e implantado de forma independente (`pyproject.toml` e `.venv` próprios, sem `pipelines` nas dependências). Testado nesta sessão: `import pipelines` levanta `ModuleNotFoundError` no `.venv` real de `apps/api` quando executado com `cwd=apps/api` (o modo real de execução, documentado no topo do arquivo). Um `sys.path.insert()` seria, além de frágil, a direção oposta do único precedente já existente no repositório (`pipelines/reconciliation/*.py` insere `apps/api` no sys.path para reaproveitar `load_shopee_products.py` — nunca o contrário).
- **Mesmo contrato do parser canônico**: fail-fast para valor não vazio inválido (nunca `0.0` silencioso), exceções nunca encadeadas (`__cause__`/`__context__` sempre `None`, verificado com `traceback.format_exception`), formato US rejeitado explicitamente, `Infinity` rejeitado.
- **Uma divergência deliberada e documentada**: NaN nativo é tratado como **ausência legítima** (não erro) nesta implementação — diferente do parser canônico, que rejeita NaN nativo. Motivo confirmado empiricamente: `pd.read_excel(..., dtype=str)` usa o mesmo sentinela float NaN tanto para célula genuinamente vazia quanto para texto reconhecido como ausência (`"NaN"`, `"NA"`, `"N/A"`, `"null"`) — não há como distinguir os dois casos depois da leitura, e ambos já significam "sem valor" no contrato desta função. `Infinity`/`-Infinity` nativo continua rejeitado (pandas nunca usa Infinity como sentinela de ausência).
- **Impacto histórico confirmado zero** (auditoria anterior não foi reexecutada): a coluna fonte é a mesma (`Subtotal do produto`, 100% ponto-decimal sem milhar, 0 células vazias em 383.298 linhas).
- **`Quantidade` recebeu o mesmo endurecimento** (endurecimento final, mesma sessão): `pd.to_numeric(df["qty"], errors="coerce").fillna(0).astype(int)` tinha o mesmo risco de zerar/truncar silenciosamente. Substituído por `_clean_int`/`_parse_qty_int`, que reaproveita `_parse_brl_float` e adiciona duas validações de contagem: quantidade fracionária (`"1.5"`) é **rejeitada, nunca truncada**, e quantidade negativa é **rejeitada**. Ausência legítima continua virando `0` (mesmo comportamento anterior); resultado final é sempre `int64`.
- **`main()` reestruturado em duas fases** (endurecimento final, mesma sessão): antes, `main()` já criava `engine`/executava o DDL **antes** de chamar `_load_brand` pela primeira marca — contradizendo a garantia fail-fast (uma marca com dado inválido só falhava depois que a conexão e o schema já tinham sido tocados). Agora `_prepare_all_brands()` (Fase A) carrega, valida e agrega **todas** as marcas inteiramente em memória — nenhuma `engine`/conexão/DDL é criada nesta fase; se qualquer marca levantar `ShopeeNumericParseError`, a exceção propaga e `main()` nunca chega a `_get_local_pg_url()`/`create_engine()`. Só o agregado de cada marca (pequeno) é retido — o DataFrame bruto (uma linha por SKU por pedido) é descartado assim que aquela marca é agregada, nunca acumulado para todas as marcas simultaneamente. `_write_prepared_brands()` (Fase B) só é chamada depois que a Fase A termina com sucesso para todas as marcas: abre a única conexão desta execução, executa o DDL uma vez e grava os agregados já validados.
- Testes: `apps/api/etl/tests/test_load_shopee_products_numeric.py` (72 casos) — formatos reais, BR com milhar, ausência, inválido fail-fast, US rejeitado, NaN/Infinity, dtype float64/int64, sanitização de mensagem/traceback/`__cause__`/`__context__`, ausência de referência a `pipelines`/engine/conexão, execução no `.venv` real de `apps/api`, quantidade fracionária/negativa rejeitada, e teste de **fluxo real** (engine/conexão fake, não `inspect.getsource`) provando que `create_engine` nunca é chamado quando qualquer marca falha no parsing, e que DDL sempre executa antes de qualquer `INSERT` quando a Fase A é bem-sucedida.
- Arquivos-legado que importam funções puras deste módulo (`pipelines/reconciliation/fix_shopee_product_dates.py`, `reconcile_bug8_canceled_only.py`, `monitor_bug8_invariants.py`, `diagnose_bug8_neon.py`, `swap_bug8_*.py`) continuam funcionando sem alteração — `BRANDS`/`DDL`/`_aggregate`/`_load_brand` mantêm a mesma assinatura pública.

### 11.6 Testes adicionados

- `pipelines/tests/test_shopee_numeric.py` — contrato completo de `parse_brl_float` (formatos reais, bug histórico, BR com milhar, moeda, negativos, tipos nativos, ausência de valor, valor inválido, formato US rejeitado, NaN/Infinity rejeitados, sanitização da mensagem de erro, amostras anonimizadas dos dados reais).
- `pipelines/tests/test_shopee_parser.py` — agregação de orders ponta a ponta com o parser corrigido (valores reais, BR com milhar, valor inválido interrompe com erro controlado e mensagem sanitizada, valor ausente sem erro, múltiplas linhas por pedido).
- `pipelines/tests/test_shopee_parser_ads.py` — mesma cobertura para o CSV de ads.
- `pipelines/tests/test_shopee_raw_numeric_wrapper.py` — `_reconcile_source`/`_print_dry_run_report` contam `numeric_parse_errors` e reprovam a reconciliação (exit code != 0) quando > 0, nunca declarando "Reconciliação OK" ao ignorar uma célula inválida.

Suíte completa de `pipelines/tests` (463 testes, incluindo os 77 de Shopee numérico) passando; nenhum teste pré-existente quebrou.

### 11.8 Endurecimento final (2026-07-04, antes do commit)

Revisão de segurança pré-commit identificou 5 lacunas na correção da seção 11.1–11.7 e todas foram fechadas — apenas código/testes/documentação, nenhum banco acessado, nenhum dado reprocessado:

1. **Fail-fast em vez de "rejeita e zera".** A decisão original (11.4) de logar e contabilizar `0.0` para valor inválido ainda permitia publicar uma métrica financeira incorreta com status de sucesso. Corrigido: `_to_float` em `_parser.py`/`_parser_ads.py` não captura mais `ShopeeNumericParseError` para continuar — relança com contexto e deixa propagar. Valor ausente legítimo (`None`) continua contribuindo zero, sem exceção.
2. **Mensagens de exceção sanitizadas.** `ShopeeNumericParseError` nunca mais inclui `repr(val)` nem o conteúdo original da célula — só uma descrição genérica ("valor numérico inválido", "valor numérico não finito"). O contexto adicionado pelos chamadores é limitado a marca, nome do arquivo, número da linha física (orders, via novo campo `_source_row`/`_source_file` propagado por `_read_xlsx`) ou índice do anúncio (ads), e nome do campo — nunca buyer, endereço, CPF ou `order_id`.
3. **Formato US rejeitado explicitamente**, não mais "silenciosamente interpretado como BR" (que era o comportamento documentado antes deste endurecimento, herdado de `_parse_brl_float`). Regra: o último separador (`,` ou `.`) decide qual é o decimal; se a vírgula vem depois do ponto (BR, `"1.234,56"`) é aceito, se vem antes (US, `"1,234.56"`) é rejeitado.
4. **Valores não finitos rejeitados.** `"NaN"`, `"Infinity"`, `"-Infinity"` (qualquer capitalização) e os floats nativos `float("nan")`/`float("inf")` agora falham em `math.isfinite()` após a conversão e levantam `ShopeeNumericParseError`, em vez de produzir um valor não finito silenciosamente aceito.
5. **Diagnóstico da Raw não esconde mais erro numérico.** `load_shopee_raw.py::_parse_brl_float_or_none` (que convertia `ShopeeNumericParseError` em `None`, indistinguível de uma célula vazia) foi removido. `_reconcile_source` agora conta `numeric_parse_errors`; `_print_dry_run_report` reprova a reconciliação (`SystemExit(1)`) quando esse total é maior que zero — nunca declara "Reconciliação OK" ignorando uma célula inválida. A carga real (`--apply`) nunca usou esse parser — comportamento inalterado.

**Confirmado nesta revisão**: os formatos reais já auditados (11.2/11.3) continuam produzindo exatamente os mesmos valores — a suíte de testes que cobre amostras reais (`test_amostras_anonimizadas_de_orders_reais`, `test_amostras_anonimizadas_de_ads_reais`, e os testes de agregação com valores reais em `test_shopee_parser.py`/`test_shopee_parser_ads.py`) passa sem alteração de valores esperados. A auditoria completa dos 384.882 registros **não foi re-executada** nesta revisão (não solicitada, e os testes acima já garantem que os valores reais não mudaram) — o impacto histórico medido na seção 11.3 continua válido e é **zero**.

### 11.7 Plano de remediação de dados históricos — **não autorizado, não executado**

Como a quantificação (seção 11.3) encontrou **zero linhas divergentes**, não há dado histórico a corrigir hoje. Este plano fica registrado apenas como procedimento a seguir **se** uma auditoria futura (com novos exports) encontrar divergência real — por exemplo, um arquivo que passe a falhar com `ShopeeNumericParseError` (formato US, valor não finito, ou outro valor não interpretável) e precise ser corrigido na fonte ou ter seu suporte formalmente estendido:

1. **Tabelas potencialmente atingidas**: `marts.fact_marketplace_daily_performance` (Neon, via `_parser.py`/`_parser_ads.py`), `marts.fact_shopee_product_monthly` (local + Neon, via `load_shopee_products.py`, se a mesma consolidação for aplicada lá).
2. **Período atingido**: seria delimitado pela reconciliação por arquivo/brand/mês, nunca assumido como "tudo".
3. **Backup**: snapshot timestamped das tabelas atingidas antes de qualquer escrita (mesmo padrão já usado nos Bugs 3/5/8 — `..._backup_<motivo>_<timestamp>`).
4. **Staging**: reprocessar os arquivos fonte com o parser corrigido em uma tabela `..._staging_<motivo>_<timestamp>`, nunca sobrescrever a tabela real diretamente.
5. **Reconciliação**: comparar staging vs. backup por brand × mês (GMV, contagens, taxas) — só prosseguir se a diferença for exatamente a esperada (mesmo padrão de `EXCEPT` bidirecional 0/0 + agregados idênticos já usado nos Gates 2/3 do Bug 8).
6. **Swap/upsert**: transação única (`LOCK` → `TRUNCATE` só da tabela real → `INSERT` explícito por coluna → validação pós-swap → `COMMIT` só se tudo idêntico), seguindo exatamente o padrão de `pipelines/reconciliation/swap_bug8_canceled_only.py`.
7. **Rollback**: backup preservado (nunca removido) até pelo menos uma carga real subsequente validada + 7 dias de observação — mesma política já adotada para os objetos do Bug 8 (seção 8 de `produtos_audit.md`).
8. **Validação dos endpoints**: smoke test read-only dos endpoints afetados (`/performance/financeiro`, `/produtos/shopee`) comparando totais antes/depois, mesmo padrão do QA final do Bug 8.

**Autorização explícita do usuário é necessária antes de executar qualquer um desses passos** — nada acima foi iniciado.
