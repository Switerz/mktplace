# Estoque Full do ML: runbook de ativação (ML-FULL-PRODUCTION-1)

**Estado em 21/10/2026:** todo o código está pronto e **inerte**. Nenhuma DDL ou migration foi aplicada, nenhum papel foi criado, nenhuma flag foi ligada e nada foi escrito. Cada passo abaixo é um **gate separado**, com autorização explícita do Mário. A promoção para a `main` do airflow **não é mesclada durante o rollout da Expedição TikTok**.

| Peça | Onde | Estado |
|---|---|---|
| Fotografia → `raw.ml_full_*` (DAG `mercadolivre_full_stock_snapshot`) | goca-se/airflow #2057 + #2097 (develop), promoção **#2098** (main) | develop ✅ · main ⏳ aprovação + fim do rollout da Expedição |
| DDL `raw.ml_full_*` | fim de `src/mercadolivre/schema.sql` (airflow) | **não aplicada** |
| Indicadores por inventário (dry-run) | mktplace #89 | mesclado em `main` (`6d991811`) |
| Migration 029 + publicador (três travas) | mktplace #91 | mesclado em `main` (`0eb106b1`); a 029 **não foi aplicada** |
| GRANTs de privilégio mínimo | `db/sql/provisionamento/ml_full_stock_*_grants.sql` | **não executados** |

## 0. Evidência que sustenta a ativação (censo de 21/10/2026)

- Resultado `ML_FULL_CENSUS_COMPLETE` nas 4 marcas, em 2 runs, com a DAG repausada.
- **768 anúncios Full, todos com `inventory_id`**, em **499 inventários** e **91.937 unidades disponíveis**.
- Auditoria do produto/UP: 99 de 100 iguais ao inventário.
- 99,71% das vendas Full associadas a um inventário.
- Cerca de 60 s por marca.

| Marca | Anúncios Full | Inventários | Disponível | `transfer` | Fonte |
|---|---:|---:|---:|---:|---|
| lescent | 137 | 77 | 33.029 | 436 | `ml_census_estado.json` (run 14:35 UTC) |
| rituaria | 135 | 75 | 3.963 | 130 | `ml_census_estado.json` (run 14:35 UTC) |
| barbours + kokeshi | 496 | 347 | 54.945 | — | diferença dos totais informados (o arquivo do 1º run foi sobrescrito) |

**Achados que não bloqueiam a fotografia:**
- `inbound_reception` devolveu HTTP 400 nas 4 contas. É outra frente (remessa Eli → Full).
- A busca por `closed` devolveu 0. Na Rituaria, 138 unidades (2,4%) vieram de anúncios fora do censo, provavelmente encerrados. Elas aparecem como `anuncio_fora_da_ponte` e não são rateadas.

## 1. Promoção do airflow (#2098)

**Pré-condição:** o rollout da Expedição TikTok terminou e não há run dela em execução.

1. Aprovação do time `dados` no #2098, conferindo que o HEAD é `21762bff`.
2. Merge normal, sem `--admin`.
3. Confirmar que o build "Build and Push to ECR" do merge terminou em `success`.

O código servido esperado tem sha256 `e4e7b2aa4448c497086e8d95689457db669ab7adbdebd49a6c4f62fb080e821e`.

## 2. Leitura no EKS (só leitura)

```
powershell -File "<scratchpad>\ml_full_snapshot_disparar.ps1"
```

O esperado:
- DAG presente e **pausada**, sem import error;
- `probe_sha256_confere = true`;
- `flag_escrita` = `"<ausente>"` ou `"false"`;
- nenhuma DAG que renova token ativa.

## 3. Dry-run real, sem escrita (1 marca)

A flag continua desligada. O run coleta e não grava.

```
powershell -File "<scratchpad>\ml_full_snapshot_disparar.ps1" -Disparar -ShaMerge <merge do #2098> -Modo dry_run -Marcas barbours
```

O esperado:
- `matriz.modo = "dry_run"` e `por_marca.barbours.desfecho = "DRY_RUN"`;
- leitura toda `OK`;
- número de inventários na mesma ordem do censo.

Se der certo, repetir com as 4 marcas.

## 4. Aplicar a DDL no Data Mart (gate de DDL)

```bash
# do checkout do airflow em origin/main (depois do #2098)
sed -n '/-- ESTOQUE FULL (ML-FULL-STOCK-INGESTION-1)/,$p' src/mercadolivre/schema.sql > /tmp/ml_full_raw_ddl.sql
psql "$DATAMART_ADMIN_URL" -v ON_ERROR_STOP=1 -1 -f /tmp/ml_full_raw_ddl.sql
psql "$DATAMART_ADMIN_URL" -v ON_ERROR_STOP=1 -1 \
     -v papel_escrita=<papel da Connection data_mart> -v papel_leitura=<papel de leitura do mktplace> \
     -f db/sql/provisionamento/ml_full_stock_datamart_grants.sql   # (mktplace)
```

A DDL é idempotente (`IF NOT EXISTS`). Por último, rodar a verificação comentada no fim do arquivo de GRANTs. Ela foi validada em Postgres descartável, com 22/22 casos.

## 5. Primeira escrita, 1 marca (gate de flag)

1. Ligar a Variable `ML_FULL_STOCK_WRITE_ENABLED = true`, pela UI do Airflow.
2. Disparar:
   ```
   powershell -File "<scratchpad>\ml_full_snapshot_disparar.ps1" -Disparar -ShaMerge <merge do #2098> -Modo escrita -Marcas barbours
   ```
3. Conferir que `por_marca.barbours.desfecho = "PUBLICADA"` e que não há `alerta` no JSON.

**Reconciliação com o censo** (Data Mart, só leitura):

```sql
SELECT s.brand,
       count(*)                                         AS inventarios,
       count(*) FILTER (WHERE s.read_status = 'OK')     AS lidos_ok,
       sum(s.available_quantity)                        AS disponivel,
       (SELECT count(*) FROM raw.ml_full_inventory_listing l
         WHERE l.brand = s.brand AND l.ref_date = s.ref_date
           AND l.logistic_type = 'fulfillment')         AS anuncios_full,
       (SELECT count(*) FROM raw.ml_full_inventory_listing l
         WHERE l.brand = s.brand AND l.ref_date = s.ref_date
           AND l.logistic_type = 'fulfillment' AND l.inventory_id IS NULL) AS full_sem_inventory
  FROM raw.ml_full_inventory_snapshot s
 WHERE s.ref_date = (now() AT TIME ZONE 'America/Sao_Paulo')::date
 GROUP BY s.brand, s.ref_date;
```

**Critérios de aceite.** O estoque muda todo dia, então a comparação é de estrutura, não de valor exato:
- `full_sem_inventory = 0`;
- `lidos_ok / inventarios ≥ 0,99`;
- inventários dentro de ±10% do censo;
- disponível da mesma ordem de grandeza.

Por fim, conferir **3 inventários no Seller Center**.

## 6. Ativar as 4 marcas

Com a flag ligada, repetir o passo 5 com `-Marcas barbours,kokeshi,lescent,rituaria`, ou sem `-Marcas`. A Barbours sai como `JA_PUBLICADA_HOJE`. Depois, reconciliar as 4 marcas com a mesma consulta.

## 7. Neon: migration 029 e publicador (gate de migration)

```bash
cd apps/api && alembic upgrade 029          # papel administrativo; cria só as 3 tabelas
psql "$NEON_ADMIN_URL" -v ON_ERROR_STOP=1 -1 \
     -v papel_publicador=<papel do publicador> -v papel_api=<papel da API> \
     -f db/sql/provisionamento/ml_full_stock_neon_grants.sql
```

### 7.1 Publicador: contrato do CLI (T-0019)

O dry-run continua sem argumentos obrigatórios. O `--apply` exige **todas** as travas: `--apply`, `ML_FULL_STOCK_PUBLISH_ENABLED=true`, a 029 aplicada e mais três argumentos do chamador.

| Argumento | Regra |
|---|---|
| `--marca <m>` | Repetível. Allowlist: `barbours`, `kokeshi`, `lescent`, `rituaria`. A fonte é **filtrada** para essas marcas; marca com fotografia que não foi pedida sai em `marcas_ignoradas` e nunca é gravada. Marca pedida sem fotografia faz o publicador recusar. |
| `--raw-run <marca>=<run_id>` | Um por marca pedida. Antes de qualquer DELETE/INSERT, toda linha lida (snapshot, ponte e status) da marca/data precisa ter esse `run_id`. Além disso, em `raw.ml_full_stock_runs` o run precisa ter pedido a marca, ter começado na data (fuso de São Paulo), estar `completo`/`parcial` e ter registrado `PUBLICADA` para ela. Se algo divergir, ele recusa. |
| `--publish-run-id <uuid hex 32>` | Gerado pelo chamador **antes** do apply e gravado como `source_run_id`. Se o id já existir em alguma fato, ele recusa (a checagem roda antes e de novo dentro do lock). |

```bash
# 1) dry-run: o JSON traz `linhagem_raw` (run_id por marca) para montar o --raw-run
python -m pipelines.publish_ml_full_stock_daily --ref-date 2026-10-28 \
       --marca barbours --raw-run barbours=<run_id>        # mesma conferência, sem escrita

# 2) apply só da Barbours
PID=$(python -c "import uuid; print(uuid.uuid4().hex)")    # guardar ANTES de rodar
ML_FULL_STOCK_PUBLISH_ENABLED=true python -m pipelines.publish_ml_full_stock_daily --apply \
       --ref-date 2026-10-28 --marca barbours --raw-run barbours=<run_id> --publish-run-id "$PID"

# 3) depois de timeout / JSON perdido / exit 1 com estado indeterminado (só leitura, sem flag)
python -m pipelines.publish_ml_full_stock_daily --diagnosticar --ref-date 2026-10-28 --publish-run-id "$PID"
```

**Saída.** O stdout traz **só o JSON final**. Os logs vão para o stderr: os handlers do `get_logger` que apontam para o stdout são redirecionados no `main()`, e qualquer `print` durante o run também vai para o stderr. Erros, inclusive de argumento, saem como JSON com `erro_classe` e `motivo` sanitizado (sem host, porta, usuário, IP ou DSN).

| Exit | Significado | `estado_transacao` |
|---|---|---|
| 0 | dry-run, diagnóstico ou apply com commit e auditoria ok | `commit_confirmado` (no apply) |
| 2 | recusa: travas, contrato, linhagem, marca, `publish-run-id` reutilizado. Nada gravado | `nao_iniciada` ou `revertida_comprovada` (recusa sob o lock) |
| 5 | **commit confirmado com pendência**: as fatos foram gravadas, mas o `audit_finish` falhou (`auditoria_ok=false`, `erro_auditoria`) | `commit_confirmado` |
| 1 | erro (lock, reconciliação, conexão…) | `nao_iniciada`, `revertida_comprovada` (exceção antes do COMMIT, rollback feito) ou `indeterminado` (falhou o próprio COMMIT: rode `--diagnosticar`) |

**Diagnóstico.** `--diagnosticar` abre uma transação `READ ONLY` no Neon, sem lock e sem escrita, e devolve:
- as linhas por fato e por marca com aquele `source_run_id` na data;
- as linhas do id em outras datas;
- o `source_run_id` vigente por marca.

O `veredito` é `commit_confirmado` (há linhas) ou `nenhuma_linha`. `nenhuma_linha` não distingue "não houve commit" de "outra publicação já substituiu o dia/marca": por isso o chamador deve diagnosticar antes de republicar.

Contraprova em PG16 descartável (16/16): `.artefacts/deliveries/ml-full/publicacao-contrato/teste/contraprova_pg.py` (não versionado).

**Atenção à cadeia:** se o #78 (028, DRE) for mesclado antes, o `down_revision` da 029 precisa ser reencadeado antes do `upgrade`. O certo é aplicar a cadeia que estiver na `main`, nunca uma revisão solta.

## 8. Schedule recomendado (gate próprio)

- **DAG `mercadolivre_full_stock_snapshot`:** `20 10,13,16 * * *` (UTC), ou seja, 07:20, 10:20 e 13:20 em São Paulo.
  - O minuto :20 cai depois do ciclo :15 do `ml_orders_etl`.
  - Fica fora da janela 05:30–07:30 UTC do `ml_items_etl`.
  - Três tentativas por dia: a DAG recusa quando a ETL está ativa ou a janela é curta, e marca já publicada no dia sai como `JA_PUBLICADA_HOJE`, sem custo.
- **Publicador do mktplace:** depois da última tentativa (por exemplo, 17:00 UTC), por um mecanismo **fora da cadeia diária**, que não é alterada neste gate.
- **Não ligar os dois schedules no mesmo gate da primeira escrita.**

## 9. Rollback (do mais leve ao mais pesado)

1. **Parar escrita nova:** `ML_FULL_STOCK_WRITE_ENABLED = false` (a DAG volta a dry-run) e/ou pausar a DAG. No mktplace, remover `ML_FULL_STOCK_PUBLISH_ENABLED`.
2. **Remover uma fotografia ruim**, num dia e numa marca específicos, com papel de escrita e em transação:
   ```sql
   BEGIN;
   DELETE FROM raw.ml_full_inventory_unavailable WHERE ref_date = :d AND brand = :b;
   DELETE FROM raw.ml_full_inventory_snapshot    WHERE ref_date = :d AND brand = :b;
   DELETE FROM raw.ml_full_inventory_listing     WHERE ref_date = :d AND brand = :b;
   COMMIT;
   ```
   No Neon, fazer o mesmo nas 3 tabelas `marts.fact_ml_full_stock_*`, por `ref_date` e `brand`.
3. **Desfazer o schema (exige autorização):**
   - Neon: `alembic downgrade 027`, que derruba só as 3 tabelas.
   - Data Mart: `DROP TABLE raw.ml_full_inventory_unavailable, raw.ml_full_inventory_listing, raw.ml_full_inventory_snapshot, raw.ml_full_stock_runs;`.
   - Nenhuma tabela existente é tocada.
