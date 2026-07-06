# Contrato da Staging Tipada Shopee — DRAFT (Fase Staging 1)

Status: **projetado e validado em preview read-only; NENHUM objeto criado em
banco, nenhuma escrita executada.** DDL e transformação existem apenas como
draft versionado. Versão original: 2026-07-04. Revisada em 2026-07-06 após
review pré-commit (garantias transacionais, validação semântica, remoção de
`buyer_key`, renomeação da tabela de orders — resumo na seção 11). **Revisada
novamente em 2026-07-06 (2ª revisão) focada em performance e integridade —
resumo na seção 13.**

- Fonte da verdade do mapping: `pipelines/staging/shopee/mapping.py`
- Fonte da verdade das validações: `pipelines/staging/shopee/validations.py`
  (usada tanto pelo preview quanto pela transformação — nunca duas listas)
- Artefatos gerados (NÃO editar à mão): `db/sql/staging/shopee_staging_ddl.sql`
  e `db/sql/staging/shopee_staging_transform.sql`
  (regenerar com `python -m pipelines.staging.shopee.build_sql --write`)
- Preview de reconciliação read-only: `python -m pipelines.staging.shopee.preview`
- Verificação semântica read-only com valores sintéticos (script manual,
  fora do pytest — mesma convenção do resto do repo de nunca tocar banco
  real em testes automáticos): `python -m pipelines.staging.shopee.verify_semantics_live`

## 1. Convenção do Data Mart (confirmada por inspeção read-only, 2026-07-04)

O warehouse tem os schemas `raw` (306 tabelas), `raw_data`, `api` (413 views),
`staging` (69), `silver` (118), `gold` (163+40 matviews). A staging **tipada
de marketplaces** mora no schema **`silver` com prefixo `stg_`**
(`silver.stg_ml_orders`, `silver.stg_ml_order_items`, `silver.stg_tiktok_orders`,
...), como TABELAS físicas com UNIQUE de negócio (`idx_*_pk`), colunas
snake_case, `numeric` para dinheiro e `timestamp` sem timezone. O schema
`staging` é de outra equipe (Shopify/Yampi por brand) e **não** é o padrão
para marketplaces.

## 2. Grãos e chaves

| Tabela | Grão | Chave técnica | Chave única |
|---|---|---|---|
| `silver.stg_shopee_order_item_snapshots` | 1 linha física de SKU de pedido por arquivo/snapshot (igual à Raw) | `raw_id` = `raw.shopee_order_item_export.id` (PK) | `(file_id, source_row_number)` |
| `silver.stg_shopee_shop_stats` | 1 linha física do relatório: um dia **ou** o total do período (`row_type = 'daily' \| 'period_total'`; a linha total **não** é apagada — a Gold decide) | `raw_id` (PK) | `(file_id, source_row_number)` |
| `silver.stg_shopee_ads` | 1 anúncio agregado no período do relatório (**sem** distribuição diária nesta camada) | `raw_id` (PK) | `(file_id, source_row_number)` |

Toda linha staging é rastreável a exatamente UMA linha Raw (`raw_id`,
`file_id`, `source_row_number`, `row_sha256`, `raw_ingested_at`). Nome de
produto **nunca** é chave.

**Revisão 2026-07-06 — nome da tabela de orders.** As tabelas `stg_*`
existentes no warehouse (`silver.stg_ml_orders`, `silver.stg_tiktok_orders`)
são DEDUPLICADAS por chave de negócio (ex.: `UNIQUE(brand, order_id)`).
Não existe hoje uma chave confiável para decidir qual snapshot de um pedido
Shopee é o vigente quando exports se sobrepõem — a Shopee reexporta o
período inteiro a cada novo arquivo, sem número de revisão. Chamar a tabela
de `stg_shopee_order_items` (nome originalmente proposto) sugeriria o mesmo
nível de canonicidade das tabelas ML/TikTok, o que seria enganoso. Por isso
a tabela foi renomeada para **`silver.stg_shopee_order_item_snapshots`**
(sufixo `_snapshots` explícito) e `TableSpec.comment` — que vira
`COMMENT ON TABLE` de verdade no DDL gerado — inclui um aviso em maiúsculas
de que a tabela não é canônica e não deve ser agregada (SUM/COUNT) sem
antes resolver a deduplicação. Essa regra de seleção do snapshot vigente
(candidatos: `raw_ingested_at` mais recente, ou `file_id` mais alto) fica
para uma camada **Gold** futura, não implementada aqui. O comentário
correspondente em `db/sql/raw/shopee_raw_ddl.sql`, que antes atribuía essa
deduplicação à "staging", foi corrigido para citar a Gold.

## 3. Garantias transacionais da transformação

O preview read-only **não é garantia suficiente** — a Raw pode mudar entre
o preview e uma execução real (ex.: novo backfill). A transformação gerada
por `build_sql.render_transform_file()` roda como **uma única transação**,
nesta ordem (versão atual, após as revisões de performance/integridade —
ver §13/§14 para o histórico completo das mudanças):

1. `SET LOCAL lock_timeout` / `SET LOCAL statement_timeout` (nunca vazam da
   transação).
2. `pg_advisory_xact_lock(84772001, 1)` — chave fixa e arbitrária desta
   fase; impede duas execuções concorrentes do mesmo script.
3. `LOCK TABLE` numa ÚNICA instrução, em ordem alfabética fixa, cobrindo as
   4 tabelas Raw/manifesto (`raw.shopee_ingestion_file`,
   `raw.shopee_order_item_export`, `raw.shopee_shop_stats_export`,
   `raw.shopee_ads_export`) **e** as 3 tabelas staging
   (`silver.stg_shopee_order_item_snapshots`, `silver.stg_shopee_shop_stats`,
   `silver.stg_shopee_ads`) `IN SHARE MODE` — compatível com leitura
   concorrente (outro `SELECT`, inclusive um preview rodando ao mesmo
   tempo, continua liberado), mas bloqueia `INSERT`/`UPDATE`/`DELETE` de
   OUTRAS sessões nas 7 tabelas até o fim da transação. Nem a Raw muda sob
   os pés da validação, nem a staging pode ser alterada externamente entre
   o pós-check e o `COMMIT` (ver §13.2).
4. **Validações fail-fast (pré-validação)** — `validations.build_merged_row_query`
   (1 query agregada por fonte via `count(*) FILTER`, a MESMA fonte usada
   pelo preview) mais `validations.build_scan_checks` (duplicidade e schema
   drift, cada uma com scan próprio justificado). ~3 scans de
   PRÉ-VALIDAÇÃO por fonte = 9 no total das 3 fontes — não confundir com o
   total da transformação completa, que soma mais scans nos passos 5/6
   (ver §13.1/§14.2 para a contabilização completa). Um bloco `DO $$ ... $$`
   aborta com `RAISE EXCEPTION` na primeira contagem `> 0`, **antes de
   qualquer INSERT**. A mensagem contém só motivo (string estática) e
   contagem — nunca payload.
5. **INSERTs** das 3 tabelas — 1 leitura da Raw + 1 INSERT na staging por
   fonte, com `ON CONFLICT (raw_id) DO NOTHING` — defesa redundante ao
   anti-join `WHERE NOT EXISTS` para corrida entre a avaliação do anti-join
   e o INSERT em si. Conflitos na UNIQUE `(file_id, source_row_number)` por
   um `raw_id` **diferente** não são alvo deste `ON CONFLICT` (que mira só
   a PK `raw_id`) — continuam abortando a transação com erro de unicidade
   nativo, como deve ser. Sem `ORDER BY` (não traz benefício num INSERT
   idempotente por `raw_id`).
6. **Validações pós-insert** (`validations.post_insert_check`, 1 scan por
   fonte) — toda linha Raw elegível deve existir na staging com os MESMOS
   `raw_id`, `file_id`, `brand`, `source_row_number` **e** `row_sha256` —
   não só o hash (ver §13.4/§14.1): uma linha com `brand`/`file_id`
   alterado manualmente não passaria só por preservar o hash de conteúdo
   do payload.
7. `COMMIT` só é alcançado se todos os passos acima passarem.

- **Arquivo já processado**: nenhum efeito (todas as linhas já têm `raw_id`,
  `ON CONFLICT` e anti-join concordam).
- **Arquivo substituído com hash diferente**: vira novo `file_id` na Raw →
  novas linhas na staging; as antigas (snapshot anterior) permanecem — a
  Gold decide o vigente (ver seção 2).
- **Late-arriving files**: entram naturalmente no próximo run.
- **Correção/reprocessamento**: operação manual documentada — `DELETE ...
  WHERE file_id = <X>` seguido do transform; decisão de DBA, nunca
  automática (mesma política da Raw).

## 4. Qualidade e validação semântica (revisão 2026-07-06)

Mesma filosofia fail-fast do parser de produção: valor NÃO vazio fora do
domínio comprovado nunca vira NULL/0 silencioso. A revisão de 2026-07-06
foi motivada por três achados confirmados por sondagem read-only contra o
Postgres 17.9 real do Data Mart (`SELECT` sobre literais, nenhuma tabela
tocada):

1. **`to_date`/`to_timestamp` com formato explícito JÁ rejeitam datas de
   calendário impossíveis** neste Postgres (`to_date('31/02/2026',
   'DD/MM/YYYY')` levanta erro nativo) — ao contrário do que se costuma
   supor. Mesmo assim, esse erro só dispara DEPOIS que a linha já está
   sendo processada. `semantics.py` adiciona uma expressão booleana pura
   (nunca lança erro, via `regexp_match` + aritmética de calendário —
   bissexto incluído) para **contar** quantas linhas falhariam ANTES do
   INSERT — mesma fonte usada por `validations.build_row_checks` e pelo
   preview. A expressão de VALOR passou a usar `make_date`/`make_timestamp`
   por componentes (nunca `to_date`/`to_timestamp`).
2. **`numeric` do Postgres aceita nativamente `'NaN'`, `'Infinity'`,
   `'-Infinity'`** (suporte desde PG14) — um `::numeric` "nu" nunca rejeita
   esses valores. `boolean` aceita `'1'/'t'/'yes'/'on'` independente de a
   coluna documentar só Y/N ou Yes/No. `integer` aceita um `+` inicial. As
   funções de valor (`numeric_dot_value`, `numeric_br_value`,
   `pct_flexible_value`, `int_value`, `bool_pair_value`) validam o FORMATO
   por regex antes do cast, rejeitando esses casos.
3. **Bug crítico corrigido**: a primeira versão desta revisão usava um
   sentinela literal (`('__invalid__')::numeric`) no `ELSE` de cada `CASE`
   como "defesa em profundidade". Sondagem confirmou que isso **falha
   incondicionalmente para 100% das linhas** — o Postgres faz *constant
   folding* de literais puros (sem referência a coluna) em tempo de
   PLANEJAMENTO, avaliando o cast antes mesmo de escanear uma linha.
   Corrigido: todo `ELSE` agora concatena um marcador ao PRÓPRIO valor de
   origem (`(v || 'CONTRATO_INVALIDO')::tipo`, via
   `semantics._force_invalid_cast`) — permanece dependente de coluna (nunca
   dobrável em constante) e garante falha mesmo para os literais nativamente
   permissivos do item 2. Nenhuma das colunas afetadas é PII, então o valor
   original aparecer numa mensagem de erro nativa nesse cenário de última
   instância (que só ocorreria se a contagem prévia tivesse um bug) é um
   trade-off aceito e documentado — a defesa primária é a contagem
   sanitizada do passo 4 da seção 3.

Validado com casos sintéticos (datas impossíveis, mês 13, hora 25,
minuto 60, `NaN`/`Infinity`/formato US, flags fora do par documentado,
período com início > fim, arquivo fora do padrão) via
`verify_semantics_live.py` — ver §14.1 para o total consolidado atual (70
casos, 0 falhas) após as revisões seguintes, incluindo a defesa em
profundidade (a expressão de VALOR realmente estoura para as entradas
inválidas testadas, nunca aceita silenciosamente).

## 5. PII — minimização por desenho

Classificação completa por coluna em `mapping.py` (`pii_class`). A staging
analítica **não carrega**: nome do destinatário, telefone, CPF, endereço,
CEP, bairro, observação do comprador, "Nota" (texto livre) e o username do
comprador — tudo isso permanece só na Raw (acesso via roles internas já
aceito). Localização mantida: cidade + UF + país (baixa granularidade).

**Revisão 2026-07-06 — `buyer_key` removida.** A versão original reservava
uma coluna `buyer_key char(64)` para um futuro HMAC de comprador único,
sempre `NULL`. Uma coluna permanentemente `NULL` não entrega valor — foi
**removida** do contrato. Se/quando o algoritmo de pseudonimização for
aprovado (decisões pendentes: HMAC-SHA256 vs. outro algoritmo; segredo
dedicado — pgcrypto não está instalado no Data Mart, cálculo teria que ser
em Python; política de rotação; impacto em `unique_buyers` se o comprador
trocar de username), a coluna pode ser adicionada por uma migration
posterior. **Por ora, compradores únicos vêm de `shop_stats.buyers_count`**
(contagem diária, sem a granularidade de identificar QUAIS compradores).

## 6. Constraints de domínio (novo na revisão de 2026-07-06)

Além dos `NOT NULL` e `UNIQUE(file_id, source_row_number)` já previstos,
o DDL agora inclui:

- `CHECK (coluna >= 0)` inline em toda coluna marcada `non_negative=True`
  no mapping — quantidades, pesos, contagens (orders/shop_stats/ads) e
  percentuais de taxa. **Sem teto de 100%** em `acos_pct`/`direct_acos_pct`
  — ACOS pode legitimamente ultrapassar 100% quando o custo excede a
  receita (pedido explícito da revisão: não inventar limite de negócio sem
  evidência).
- `silver.stg_shopee_shop_stats`: `period_start <= period_end` quando
  ambos preenchidos (além do CHECK de `row_type` já existente).
- `silver.stg_shopee_ads`: `report_period_start`/`report_period_end` ambos
  nulos ou ambos preenchidos, com `start <= end`; `ended_at IS NULL OR
  ended_at >= started_at`.

Essas constraints são um backstop declarativo — a defesa primária continua
sendo a contagem fail-fast do passo 4 da seção 3, que aborta antes de
qualquer INSERT tentar violar um CHECK.

## 7. Mapping (resumo — completo em `mapping.py`)

- **orders**: 71 chaves reais no `raw_payload` → colunas tipadas + 9
  exclusões de PII/texto livre (username do comprador passou a ser
  excluído explicitamente nesta revisão, já que `buyer_key` foi removida).
  Inclui os 2 templates (apice tem `Tipo de pedido`, `Returned quantity`,
  `Desconto de Frete Aproximado`, `CPF do Comprador`; não tem `Domestic
  Delivered Date`/`Pedido FBS`/`Shopee Owned`/`Data da Finalização do
  Cancelamento`). Headers duplicados desambiguados por posição:
  `Cidade__col58/59` (a 1ª "Cidade" é 100% vazia) e `Desconto do
  vendedor__col23/26` (2ª ocorrência → `seller_discount_2`, semântica não
  confirmada — **não alimentar Gold** enquanto não confirmado).
- **shop_stats**: 17 chaves → 100% mapeadas. Dinheiro em formato BR
  ("1.234,56"), percentuais "3,84%" (unidade 0–100, comprovada < 100).
- **ads**: 36 chaves → 100% mapeadas (kokeshi tem `Segmentação de Público`,
  sempre "-"). Dinheiro ponto-decimal; percentuais aceitam ponto OU vírgula
  (a função `pct_flexible` unifica os dois formatos observados).
  `Data de Encerramento` = "Ilimitado" em 803/804 → NULL.

Formatos comprovados por inventário sanitizado sobre 100% da Raw (contagens
por classe de formato, jsonb_typeof, comprimentos — sem valores de PII).

## 8. Gap conhecido: período do relatório de ads

As linhas de metadados do CSV de ads (que contêm "Período") **não foram
persistidas na Raw** (o loader começa no header). O período em
`report_period_start/end` é extraído do **nome do arquivo**
(`...-01_01_2026-31_03_2026.csv`) — funciona para 8 dos 10 arquivos (582 de
804 linhas). Os 2 CSVs da kokeshi (`Dados+Gerais-01-01-19-03.csv`, sem ano)
ficam com período NULL. Opções futuras: (a) renomear os arquivos da kokeshi
no padrão; (b) evoluir a Raw para persistir as linhas de metadados do CSV.
**Nenhuma aplicação real da staging deve ocorrer antes de decidir esse
ponto** (backfill/renomeação) se a Gold precisar do período completo.

## 9. Reconciliação preview (read-only, 2026-07-06, 100% da Raw)

Executada com `DATAMART_DATABASE_URL` (read replica) em sessão
`postgresql_readonly=True`, reaproveitando `validations.build_merged_row_query`
e `validations.build_scan_checks` (a mesma fonte da transformação) + o
SELECT tipado completo sobre as 384.882 linhas:

| Fonte | Raw = manifesto | Aceitas | Rejeitadas | Duplicidades | Órfãs | Chaves fora do contrato |
|---|---|---|---|---|---|---|
| orders | 383.298 (85 arq.) | 383.298 | 0 | 0 | 0 | 0 |
| shop_stats | 780 (25 arq.) | 780 | 0 | 0 | 0 | 0 |
| ads | 804 (10 arq.) | 804 | 0 | 0 | 0 | 0 |

Todas as ~130 checagens individuais (obrigatoriedade, formato/domínio,
não-negatividade, padrão de `order_id`, período do filename de ads,
integridade estrutural Raw/manifesto) retornaram contagem **zero**. Somas
de sanidade idênticas às auditadas na fase Raw: `product_subtotal`
R$ 24.859.859,62; ads `gmv` R$ 16.887.993,55; `quantity` 392.474. Durante a
execução, uma tentativa isolada esbarrou em
`SerializationFailure: canceling statement due to conflict with recovery`
— conflito transitório de replicação na read replica (já documentado no
runbook da Raw), resolvido no retry seguinte; não é um problema do
contrato ou das queries.

**Dois bugs reais foram encontrados e corrigidos durante esta reconciliação**
(não apenas hipotéticos — reproduzidos contra a base real):
1. O sentinela literal no `ELSE` (seção 4, item 3) — fazia a query falhar
   para 100% das linhas de QUALQUER coluna booleana/numérica, mesmo com
   dado 100% válido.
2. A checagem de "valor negativo" usava `^-` (qualquer coisa começando com
   hífen), capturando o placeholder `'-'` (ausência documentada em `Add to
   Cart`, `Impressões do Produto`, `Cliques de Produtos`, `CTR do Produto`,
   `Add to Cart Rate`) como falso positivo — 1.632 linhas sinalizadas
   incorretamente. Corrigido para `^-[0-9]` (exige um dígito após o sinal).

## 10. Decisão: view vs materialized view vs tabela física

**Decisão: tabela física incremental em `silver`** (como o restante da
staging de marketplaces do warehouse). Justificativa:

- **Custo de parse repetido**: orders Raw tem ~814 MB de JSONB; uma view
  re-parsearia 71 chaves × 383k linhas a cada consulta — inaceitável para
  consumo recorrente via VPN. Materialized view evitaria o parse, mas
  `REFRESH` é sempre full (re-parse total, lock) e não é incremental.
- **Incrementalidade**: tabela + anti-join por `raw_id` processa só o delta
  dos novos arquivos; matview/view não têm equivalente barato.
- **Rastreabilidade/auditoria**: tabela com `raw_id`/`row_sha256` e
  `staging_built_at` permite auditar quando cada linha foi tipada.
- **Convenção**: silver.* já é 114 tabelas / 4 views — views são exceção.

## 11. Resumo da revisão de 2026-07-06

| Item pedido na revisão | Como foi endereçado |
|---|---|
| Grão de orders confuso com canônico | Tabela renomeada para `..._order_item_snapshots`; comentário de aviso no DDL; comentário da Raw corrigido |
| `buyer_key` reservada sem valor | Removida do contrato |
| Transação sem garantias | Advisory lock + `LOCK TABLE` + validação fail-fast pré-INSERT + validação pós-INSERT, nessa ordem, numa única transação |
| Datas semanticamente inválidas | `semantics.py`: checagem por componentes (bissexto incluído) + `make_date`/`make_timestamp` na construção do valor |
| NaN/Infinity/boolean permissivo | Regras de formato explícitas antes do cast; achado o bug de constant-folding e corrigido |
| CHECKs de domínio | `>= 0` para quantidades/pesos/contagens/percentuais (sem teto em ACOS); ordem de períodos; `ended_at >= started_at` |
| Idempotência concorrente | Advisory lock + `ON CONFLICT (raw_id) DO NOTHING`; `ORDER BY` removido |
| Integridade Raw/manifesto | Checagens de órfã, `source_type`/`brand` incompatível, duplicidade, chave JSONB fora do contrato — abortam a transação |
| Preview e transform com regras divergentes | `validations.py` é a fonte única; `rules_registry.py` idem para as expressões de valor |
| Permissões/FKs | **Corrigido na revisão de performance/integridade (§13.3): FK FÍSICA** `raw_id → raw.<tabela_filha>(id)` e `file_id → raw.shopee_ingestion_file(file_id)` em cada tabela staging — não "sem FK física" como uma versão anterior deste texto dizia. `REVOKE ALL FROM PUBLIC` documentado como não afetando roles nomeadas (mesma nota da Raw) |

## 12. Perguntas de negócio em aberto

1. Semântica da 2ª coluna "Desconto do vendedor" (`seller_discount_2`) —
   confirmar com a Shopee/Seller Center antes de usar em métrica.
2. Unidade de "Compensar Moedas Shopee" (moedas vs centavos).
3. Algoritmo/segredo/rotação de pseudonimização de comprador único (seção 5).
4. Período dos ads da kokeshi (seção 8): renomear arquivos ou evoluir Raw.
5. "Status do pedido" tem valores-frase ("O comprador pode pedir uma
   devolução até YYYY-MM-DD", 28 linhas) — definir bucket canônico na Gold.
6. Raw de orders cobre até 2026-05-31; o Neon tem dados até ~2026-06-20
   (carregados direto dos arquivos em jun/2026). Se existirem exports de
   junho em `shopee/`, rodar o backfill da Raw antes da primeira carga real
   da staging.
7. Regra de seleção do snapshot vigente para `stg_shopee_order_item_snapshots`
   (seção 2) — a definir na Gold.

## 13. Revisão de performance e integridade (2026-07-06, 2ª revisão)

### 13.1 Scans eliminados

A 1ª revisão gerava um `SELECT count(*)` INDEPENDENTE por condição de
validação (~55-60 por fonte, ~130-150 no total, incluindo checagens
estruturais) — cada um um scan completo de `raw.shopee_*_export`.
Redesenhado (`validations.py`):

- **1 query agregada por fonte** (`build_merged_row_query`), usando
  `count(*) FILTER (WHERE <condição>)` para TODAS as condições de linha
  (obrigatoriedade, formato/domínio, não-negatividade, padrão de
  `order_id`, período de ads, órfã de manifesto,
  `brand`/`source_type` incompatível — esta última passou a usar `LEFT
  JOIN` em vez de `INNER JOIN` exatamente para caber na mesma query).
- **2 checagens estruturais por fonte** (`build_scan_checks`), cada uma
  com scan próprio mas apoiada em algo já existente:
  - duplicidade de `(file_id, source_row_number)` — reaproveita a UNIQUE
    constraint que a própria Raw já tem;
  - schema drift — reconstruída a partir de
    `raw.shopee_ingestion_file.headers_json` (1 linha por ARQUIVO) em vez
    de `raw_payload` por LINHA; validado por sondagem read-only que produz
    exatamente o mesmo conjunto de chaves (71/17/36), a uma fração do
    custo.

**Benchmark real (read-only, 2026-07-06)**, mesmas condições, execução
separada vs. agregada:

| Fonte | Scans antes | Scans depois | Tempo antes | Tempo depois | Redução |
|---|---:|---:|---:|---:|---:|
| shop_stats (780 linhas) | 35 | 1 | 3,75s | 0,16s | 23,8x |
| ads (804 linhas) | 55 | 1 | 5,99s | 0,44s | 13,5x |
| orders (383.298 linhas) | 59 | 1 | ~441s (estimado por amostra de 10) | 271s (medido) | ~1,6x |
| orders — schema drift | 1 (raw_payload/linha) | 1 (headers_json/arquivo) | 5,52s | 0,14s | 38,1x |

**Scans de PRÉ-VALIDAÇÃO**: **~130-150 → 9** (3 por fonte × 3 fontes) —
esse "9" é só o passo 4 (validação fail-fast), não a execução completa da
transformação. Contabilização completa por etapa (ver também docstring de
`build_sql.py`):

| Etapa | Scans | Unidade |
|---|---:|---|
| Passo 4 — pré-validação | **9** (3×3) | 1 query agregada + 2 estruturais, por fonte |
| Passo 5 — INSERTs | 3 (1×3) | 1 leitura da Raw + 1 INSERT na staging, por fonte |
| Passo 6 — pós-insert | 3 (1×3) | 1 comparação Raw × staging, por fonte |
| **Total da transformação (4+5+6)** | **15** | — |

O `preview.py` roda só o equivalente ao passo 4 (9 scans de pré-validação) mais consultas
adicionais que não fazem parte da transformação real (contagem do
manifesto, SELECT tipado completo, contagem por brand/mês/arquivo) — por
isso a frase "9 scans" nunca deve ser lida como "execução completa": é
especificamente a pré-validação.

Nota honesta sobre `orders`: o ganho de E/S (menos scans/menos
round-trips) é real, mas o tempo absoluto da query agregada (271s) é
dominado por CUSTO DE CPU — cada condição de data/timestamp chama
`regexp_match` até 5 vezes (uma por componente ano/mês/dia/hora/minuto),
e existem ~8-10 colunas desse tipo em orders. Reduzir essa redundância
(computar o array de match uma vez por coluna, via subquery, e reutilizar)
é uma otimização válida e NÃO implementada nesta revisão — exigiria alterar
`semantics.py` (já validado com casos sintéticos — total atual em §14.1) com risco de
regressão, fora do escopo explícito desta revisão (eliminar SCANS
separados, não minimizar CPU por linha). Como job de carga em lote
(não um caminho quente), um lock de alguns minutos durante uma execução
periódica é operacionalmente aceitável — mas fica registrado aqui como
característica conhecida, não escondida.

### 13.2 Lock também nas tabelas staging

`LOCK TABLE` agora cobre as 4 tabelas Raw/manifesto **e** as 3 tabelas
staging, numa ÚNICA instrução (nunca instruções separadas — evita uma
janela em que só parte dos locks foi adquirida), com a lista de tabelas em
**ordem alfabética fixa** (evita deadlock entre execuções concorrentes:
toda execução deste mesmo script sempre pede os locks na mesma ordem).
`SHARE MODE` permite `SELECT` concorrente (inclusive um preview rodando ao
mesmo tempo) mas bloqueia `INSERT`/`UPDATE`/`DELETE` de OUTRAS sessões nas
7 tabelas até o fim da transação — a Raw não muda sob os pés da validação,
e a staging não pode ser alterada externamente entre o pós-check e o
`COMMIT`. O `INSERT` desta mesma transação (passo 5) não é bloqueado pelo
próprio `SHARE` lock — uma transação nunca bloqueia a si mesma.

### 13.3 FK física — decisão corrigida com evidência

**Correção de um erro do relatório da revisão anterior**: foi dito que a
Raw usa "FK lógica" — falso. `raw.shopee_order_item_export` (e
`shop_stats_export`, `ads_export`) têm `FOREIGN KEY (file_id) REFERENCES
raw.shopee_ingestion_file(file_id) DEFERRABLE INITIALLY DEFERRED` — uma FK
FÍSICA real, confirmada via `pg_constraint`.

Evidência levantada (read-only, `pg_constraint` + `has_table_privilege`):

| Item | Achado |
|---|---|
| `raw.shopee_*` | FK física real (`file_id → raw.shopee_ingestion_file`) |
| `silver.stg_ml_*` / `silver.stg_tiktok_*` | **0 de 51** tabelas `silver.stg_*` têm qualquer FK física |
| Owner de `raw.shopee_*` | `postgres` |
| Owner de `silver.stg_ml_orders` | `dbt` (ferramenta/pipeline diferente) |
| `REFERENCES` em `raw.shopee_ingestion_file` | Só o owner (`postgres`) tem; `postgrest`, `airflow`, `web_user`, `sql_runner`, `datamart_ro`, `datamart_rw` — **nenhuma tem** |

**Decisão: FK física** — `raw_id → raw.<tabela_filha>(id)` e
`file_id → raw.shopee_ingestion_file(file_id)` em cada tabela staging (já
no DDL gerado). Justificativa: a Raw já usa FK física para sua própria
lineage interna, e é append-only + a staging depende diretamente dela —
o cenário onde FK física é mais defensável. As tabelas `silver.stg_*`
existentes não terem FK física não é um contra-argumento definitivo: elas
são mantidas por uma ferramenta/dono diferente (`dbt`), sem relação direta
de dependência transacional com a Raw como esta staging tem.

**Privilégio REFERENCES — dois cenários, esclarecido nesta revisão**:

1. **Aplicação manual com a credencial de escrita atual da Raw**
   (`.env.shopee-write.local` → `DATAMART_SHOPEE_WRITE_URL`) — essa
   credencial conecta como a role **`postgres`**, a MESMA que é dona de
   `raw.shopee_ingestion_file`/`raw.shopee_order_item_export`/etc. (owner
   confirmado via `pg_tables`). Um owner tem implicitamente TODOS os
   privilégios sobre seus próprios objetos, `REFERENCES` incluso — **não
   precisa de nenhum `GRANT` adicional**. Se este DDL for aplicado
   manualmente com essa credencial, o FK físico funciona de imediato.
2. **Uma futura role de automação dedicada à staging** (diferente de
   `postgres`, ainda não criada — o padrão já usado para a Raw foi ter uma
   credencial de escrita própria e segregada) precisaria de um
   `GRANT REFERENCES ON raw.shopee_ingestion_file,
   raw.shopee_order_item_export, raw.shopee_shop_stats_export,
   raw.shopee_ads_export TO <nova_role>` do owner da Raw (`postgres`) ANTES
   de aplicar este DDL com essa nova credencial — nenhuma role nomeada
   conhecida hoje (`postgrest`, `airflow`, `web_user`, `sql_runner`,
   `datamart_ro`, `datamart_rw`) tem esse privilégio.

Nenhuma role foi criada e nenhum `GRANT` foi executado nesta fase — os dois
cenários ficam documentados para quando a forma de aplicação for decidida.

### 13.4 Post-insert fortalecido

Antes: comparava só `raw_id` + `row_sha256`. Agora compara também
`file_id`, `brand` e `source_row_number` — uma linha da staging alterada
manualmente (ex.: `brand` trocado) não passa mais só por preservar o hash
do conteúdo do payload, já que esses 3 campos não entram no hash.

### 13.5 NaN/Infinity em `numeric(p,s)` e `CHECK` — achado real

Sondagem read-only confirmou:

- `numeric(p,s)` **explícito** (precisão/escala definidas — todas as
  colunas deste contrato usam, ex.: `numeric(14,2)`) já REJEITA
  ±Infinity nativamente (`'Infinity'::numeric(14,2)` → "numeric field
  overflow"). Infinity está coberto pelo próprio tipo, sem necessidade de
  CHECK adicional.
- **NaN não é rejeitado** por `numeric(p,s)` nem pelo `CHECK (col >= 0)`
  que a revisão anterior já tinha: `'NaN'::numeric(14,2) >= 0` avalia
  **TRUE** (Postgres trata NaN como "maior que qualquer valor não-NaN" na
  ordenação/comparação). Um CHECK de não-negatividade sozinho deixaria
  NaN passar silenciosamente.

**Corrigido**: toda coluna `numeric(...)` — não só as marcadas
`non_negative=True` — ganhou `CHECK (col <> 'NaN')` explícito (combinado
com `>= 0` quando aplicável). Validado com 7 casos sintéticos adicionais
em `verify_semantics_live.py` (coluna com e sem `non_negative`, valor
válido/negativo-permitido/NaN) — ver §14.1 para o total consolidado atual
(70 casos, 0 falhas).

### 13.6 Incrementalidade após mudança de mapping

O anti-join por `raw_id` (`WHERE NOT EXISTS (... s.raw_id = r.id)`) só
detecta linhas AINDA NÃO staged — não reaplica a transformação a linhas já
carregadas. Se uma regra de `mapping.py`/`semantics.py` mudar (ex.:
corrigir um formato, adicionar uma coluna), as linhas já staged **não são
recalculadas automaticamente** por uma nova execução do transform: elas
permanecerão com o resultado da regra ANTIGA até um rebuild explícito.
**Nenhum framework de migração foi implementado** (fora de escopo,
explicitamente não pedido) — uma mudança de mapping que precise
retroagir sobre dados já staged exigirá, no futuro, uma decisão manual e
controlada (ex.: `TRUNCATE` + reprocessamento completo, ou uma coluna de
versão de transformação) — documentado aqui como uma limitação conhecida,
não resolvida.

### 13.7 Decisões preservadas desta revisão

Confirmado que a revisão de performance/integridade NÃO alterou: o nome
`stg_shopee_order_item_snapshots`; a remoção de `buyer_key`; a ausência de
Gold/deduplicação de negócio nesta camada; os gaps documentados de junho
(Raw), ads da kokeshi (período), `seller_discount_2` e unidade de
"Compensar Moedas Shopee".

## 14. Fechamento técnico (2026-07-06, 3ª revisão)

### 14.1 Contagem exata de linhas rejeitadas

O preview somava as contagens por motivo (`total_rejectable += n`) —
supercontava uma linha que viola mais de uma condição ao mesmo tempo (ex.:
`order_id` vazio E `quantity` negativo na mesma linha somava 2, mas é 1
linha). Corrigido: a MESMA query agregada (`build_merged_row_query`) agora
também calcula `rejected_any_expr` —
`count(*) FILTER (WHERE (c0) OR (c1) OR ...) AS rejected_any` — contando
LINHAS DISTINTAS que violam qualquer condição, numa única passada, sem
scan adicional. `linhas_rejeitaveis`/`linhas_aceitas` do preview agora vêm
exclusivamente de `rejected_any` (contagem exata); as contagens por motivo
continuam existindo só para diagnóstico (qual regra falhou), nunca somadas.

Prova simbólica em `test_shopee_staging_validations.py` (linha sintética
violando 2 motivos reais do contrato → 2 ocorrências individuais, 1
rejeitada) + prova via SQL literal em `verify_semantics_live.py`
(4 linhas sintéticas via `VALUES`: motivo1=2, motivo2=2, soma=4,
`rejected_any`=3 — a linha que viola os dois é contada 1 vez, não 2). Com
essa prova, `verify_semantics_live.py` acumula, no total, **70 casos
sintéticos, 0 falhas** (52 de formato/domínio semântico + 10 de defesa em
profundidade + 7 de CHECK anti-NaN no DDL + 1 desta prova de
`rejected_any`) — este é o total corrente citado por todo o resto deste
documento; qualquer número menor mencionado em seções anteriores (§4, §13)
é um snapshot histórico de quando aquele trecho foi escrito, não o total
atual.

Os contadores estruturais (duplicidade, schema drift) permanecem à parte
de `linhas_rejeitaveis`/`linhas_aceitas`: são unidades diferentes de "linha
rejeitada" (duplicidade conta linhas participantes de um par; schema drift
conta CHAVES de JSONB, não linhas) — reportados como
`problemas_estruturais`, nunca somados ao total de linhas.

### 14.2 Nomenclatura de scans corrigida

"9 scans por execução completa" era impreciso — renomeado para "9 scans de
pré-validação" em todo o código (`build_sql.py`, `preview.py`) e na
documentação (§13.1, tabela de contabilização por etapa: pré-validação 9,
INSERTs 3, pós-insert 3, total da transformação 15). `preview.py` também
passou a expor `scans_prevalidacao` (antes `scans_executados`, nome
ambíguo) no relatório.

### 14.3 Documentação de FK corrigida

A tabela da seção 11 ainda dizia "Sem FK física nova" — contradição com a
decisão real (§13.3) e com o DDL gerado. Corrigida para refletir a decisão
final: FK física `raw_id → raw.<tabela_filha>(id)` e
`file_id → raw.shopee_ingestion_file(file_id)`. §13.3 também foi expandida
com o esclarecimento operacional que faltava: a credencial de escrita
ATUAL (`.env.shopee-write.local`, role `postgres`) já É a dona das tabelas
Raw, então uma aplicação manual deste DDL com essa mesma credencial **não
precisa de nenhum GRANT REFERENCES adicional** — o bloqueio de privilégio
só existe para uma FUTURA role de automação dedicada e diferente. Nenhuma
role foi criada nem GRANT executado.
