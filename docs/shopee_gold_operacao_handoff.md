# Handoff operacional — Gold Shopee (encerramento)

Este documento é o procedimento operacional final para quem já executa o scraping e as cargas Raw/Silver da Shopee e passará também a executar a Gold regional (`gold.marketplace_region_daily`, janela Shopee).

Não repete o histórico dos Gates S1–S5 (ver [`docs/shopee_datamart_operacao_completa.md`](shopee_datamart_operacao_completa.md) para o desenho/histórico completo e [`docs/regional_design_draft.md`](regional_design_draft.md) para o design da Gold regional). Este documento ensina **como executar**, não como foi construído.

## 1. Objetivo e responsabilidades

- **Operadora externa** (você): scraping, Raw, Silver e, a partir de agora, Gold Shopee — usando os comandos deste documento.
- **Torre de Controle** (Mário/`mktplace`): sincronização Data Mart → Neon (`sync_region_daily`/`sync_region_if_needed`) e qualquer mudança de endpoint/frontend.
- **Você nunca recebe credencial Neon.** A Gold termina no Data Mart; o Neon é responsabilidade exclusiva da Torre (ver §8).
- **Mercado Livre e TikTok não fazem parte deste procedimento** — os comandos abaixo só tocam `gold.marketplace_region_daily` na janela Shopee (`marketplace_id=Shopee`).

## 2. Pré-requisitos locais

- Repositório `mktplace` atualizado (checkout local no commit esperado).
- Ambiente Python com as dependências do repositório instaladas.
- Acesso de leitura ao Data Mart (`DATAMART_DATABASE_URL` configurado — variável de ambiente ou `.env`).
- Secret Raw já em uso por você (`.env.shopee-write.local`) — sem mudança neste gate.
- `.env.gold-window-write.local` na raiz do repositório, com **exatamente** as duas chaves esperadas pelo código:
  - `DATAMART_GOLD_WINDOW_WRITE_URL`
  - `I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW` (deve valer `"1"`)
- Um diretório **absoluto, persistente e fora do repositório** para guardar backups e receipts (`--artifacts-dir`). Ele precisa já existir e ser gravável.
- **Nunca** commitar `.env*`, o conteúdo do diretório de artefatos, backups ou receipts.

Não há valores reais de DSN/host/usuário/senha neste documento — nunca cole isso em chat, issue ou commit.

## 3. Sequência operacional

### A. Scraping

Execute o scraping normalmente, como já faz hoje.

### B. Loader Raw (com `--json`)

```powershell
python -m pipelines.ingestion.load_shopee_raw --apply --backfill --data-path <caminho-do-lote> --json
```

`--json` só é aceito com `--apply --backfill` — qualquer outra combinação é rejeitada antes de tocar secret/banco. A saída é **um único documento JSON** no stdout, com os campos:

- `batch_id`: identifica **esta invocação** do loader (não é global/histórico).
- `raw_status`: `"all_files_committed"` | `"partially_failed"` | `"failed"` | `"no_files"` | `"blocked"`.
- `raw_reconciled`: `true`/`false` — saúde da reconciliação (global + do lote atual).
- `order_file_ids`: lista de `file_id` de `source_type="orders"` (inseridos + pulados por idempotência) — é o que alimenta a Gold.
- `problems` / `warnings`: listas de texto sanitizado.

Regras obrigatórias:

- **Só continue se o comando terminar com sucesso** (`raw_status` indicando sucesso e `raw_reconciled=true`).
- `order_file_ids` deve ser **capturado exatamente do JSON** — nunca extraído de log humano ou de nome de arquivo.
- Um `file_id` com `skipped_idempotent` continua válido mesmo que tenha sido persistido originalmente em outro `batch_id` — o `batch_id` desta resposta identifica a invocação atual, não necessariamente onde aquele arquivo específico foi gravado da primeira vez.

### C. Silver

Execute aqui o runner Silver já utilizado pela operação. **O comando exato não está versionado neste repositório** — não invente um comando; use o procedimento que você já mantém.

Antes de prosseguir para a Gold, confirme:

- término com sucesso;
- commit da transação Silver;
- reconciliação Raw↔Silver concluída;
- todos os `order_file_ids` do JSON Raw (passo B) estão contemplados na Silver.

### D. Gold (`run_shopee_gold_batch`)

Se `order_file_ids` veio **vazio** no JSON Raw (lote só com `shop_stats`/`ads`, sem pedidos): classifique a Gold regional como **`not_applicable`**, não chame `run_shopee_gold_batch`, e não trate isso como falha.

Caso contrário:

```powershell
$fileIds = @(101, 102, 103)   # order_file_ids do JSON do passo B
$batchId = "<batch_id do JSON do passo B>"
$runId   = "gold-$(<gerador de id novo, ex.: um GUID ou timestamp fornecido por você>)"
$artifactsDir = "C:\caminho\absoluto\fora\do\repo\artefatos-gold"

$fileIdArgs = $fileIds | ForEach-Object { "--file-id", $_ }

python -m pipelines.ops.run_shopee_gold_batch `
  @fileIdArgs `
  --batch-id $batchId `
  --run-id $runId `
  --artifacts-dir $artifactsDir `
  --json
```

Regras do comando:

- Nunca derive a janela a partir de nome de arquivo — só os `order_file_ids` do JSON Raw.
- Nunca chame com a lista de `--file-id` vazia (é rejeitado como entrada inválida, mas não tente — trate como `not_applicable`, ver acima).
- **Gere um `run_id` novo a cada tentativa manual** — reusar um `run_id` cuja tentativa anterior já publicou artefato é sempre rejeitado (nomes determinísticos nunca são sobrescritos).
- `--artifacts-dir` precisa ser absoluto e fora do repositório.
- Preserve o stdout JSON (é a única saída de automação/log deste comando) — nunca misture com print humano.
- Nenhum secret aparece no stdout/stderr em nenhum caminho.

## 4. Interpretação dos resultados

### Loader Raw (`--json`)

| Situação | Exit code |
|---|---|
| Sucesso (todos os arquivos elegíveis, reconciliado) ou nenhum arquivo elegível (`no_files`) | `0` |
| Secret/guardrail de escrita bloqueado | `2` |
| Preflight de escrita bloqueado | `3` |
| Advisory lock em uso por outra execução | `6` |
| Falha total/parcial de arquivo, ou reconciliação (global ou do lote) não fechou | `9` |

### Gold (`run_shopee_gold_batch`)

| Situação | `operation_outcome` | `receipt_status` | Exit code |
|---|---|---|---|
| Gold atualizada, receipt publicado | `committed` | `ok` | `0` |
| Gold já reconciliada, nenhuma escrita | `no_op` | `ok` | `0` |
| Configuração/entrada/preflight inválido (secret, `file_ids`, `artifacts_dir`, `DATAMART_DATABASE_URL` ausente, preflight de escrita) | `blocked` | `not_attempted` | `2` |
| Bloqueio operacional/estrutural (janela não resolvida, `file_id` ausente na Silver, lote vazio, data nula, janela > 180 dias, refresh bloqueado) | `blocked` | `not_attempted` | `3` |
| Falha da operação (contrato do resolvedor violado, refresh falhou, erro inesperado) | `failed` | `not_attempted`/`ok` (se chegou a publicar) | `4` |
| Operação **committed** ou **no_op**, mas o receipt falhou ao publicar/revalidar | `committed`/`no_op` | `failed` | `5` |

**Regra crítica:**

- **Nunca faça retry automático da Gold.** Principalmente exit `5` **não autoriza repetir o refresh** — os dados já foram gravados (ou já estavam corretos); o que falhou foi só a evidência local.
- Qualquer nova tentativa manual deve usar um **`run_id` novo**.
- Antes de qualquer repetição, inspecione o receipt (se existir), o backup e o resultado anterior — nunca decida só pelo exit code isolado.

## 5. Artefatos

Dentro de `--artifacts-dir`, cada execução gera (nomes determinísticos, nunca sobrescritos):

- `shopee_window_backup_{batch_id}_{run_id}.json`
- `shopee_window_backup_{batch_id}_{run_id}.json.sha256`
- `shopee_window_receipt_{batch_id}_{run_id}.json`

Regras:

- Preserve esses arquivos — não existe retenção ou deleção automática.
- **Nunca edite manualmente** backup ou receipt (um receipt divergente do disco não é corrigido automaticamente — fica como evidência para investigação).
- Se algum dos três nomes já existir para o par `batch_id`/`run_id`, a execução é recusada antes de tocar o banco — é sinal de reuso de `run_id`, use um novo.

## 6. Segurança e limites

- A Gold só altera a janela Shopee calculada a partir dos `order_file_ids` informados — nunca ML/TikTok, nunca fora da janela.
- Não há restore automático, nem sync Neon, nem retry automático neste procedimento.
- A credencial Gold (`gold_shopee_window_writer`) é least-privilege: sem `UPDATE`/`TRUNCATE`/`CREATE`, sem acesso a tabelas ML/TikTok.
- Secrets nunca vão para Git, logs, chat ou documentos.
- `order_id`, PII e linhas individuais nunca aparecem em receipts, backups ou chamados — só contagens/agregados/booleans/datas.

## 7. Falhas e acionamento

| Etapa | Sintoma | Parar ou continuar | Evidência a preservar | Responsável |
|---|---|---|---|---|
| Raw | `raw_status` ≠ sucesso ou `raw_reconciled=false` | **Parar** — não prosseguir para Silver/Gold | JSON completo do loader (batch_id, problems, warnings) | Operadora |
| Silver | Runner Silver falhou ou reconciliação Raw↔Silver não fechou | **Parar** — não prosseguir para Gold | Log/saída do runner Silver | Operadora |
| Gold | Exit `2`/`3`/`4` | **Parar** — não repetir sem investigar | JSON de saída do `run_shopee_gold_batch`, receipt (se publicado) | Operadora → reportar à Torre |
| Gold | Exit `5` | **Parar** — dados já gravados, evidência incompleta; nunca repetir o refresh | JSON de saída, backup, receipt parcial/ausente | Operadora → reportar à Torre |

Regra geral: **qualquer falha em Raw/Silver impede a Gold.**

Ao acionar a Torre, envie apenas:

- `batch_id`, `run_id`;
- exit code;
- `operation_outcome`, `reason_code`;
- caminhos dos artefatos (sem conteúdo);
- `problems`/`warnings` (já vêm sanitizados pelo próprio JSON).

Nunca envie DSN, senha, `order_id` ou linhas individuais.

## 8. Neon

- **A operação termina no Data Mart.** Você não sincroniza Neon.
- **Não execute `sync_region_daily` nem `sync_region_if_needed`.**
- A Torre executa e valida a sincronização com o Neon separadamente, depois de conferir o resultado da Gold.

## 9. Checklist diário

1. Rodar scraping normalmente.
2. Rodar loader Raw com `--apply --backfill --json`.
3. Conferir `raw_status` e `raw_reconciled` — só prosseguir se sucesso.
4. Capturar `batch_id` e `order_file_ids` exatamente do JSON.
5. Rodar o runner Silver já em uso.
6. Confirmar commit Silver + reconciliação Raw↔Silver completa.
7. Se `order_file_ids` vazio → marcar Gold como `not_applicable` e parar aqui.
8. Gerar `run_id` novo.
9. Rodar `run_shopee_gold_batch` com `--json`, `--artifacts-dir` absoluto fora do repo.
10. Conferir `operation_outcome`/`receipt_status`/exit code antes de considerar concluído.
11. Preservar backup + receipt no diretório de artefatos.
12. Nunca repetir a Gold sem `run_id` novo e sem investigar o resultado anterior.

## 10. Primeira utilização

Recomenda-se acompanhar a primeira execução com um lote novo (Raw → Silver → Gold ponta a ponta). Isso **não bloqueia** a entrega deste handoff nem exige nova implementação — é só uma recomendação de prudência operacional.
