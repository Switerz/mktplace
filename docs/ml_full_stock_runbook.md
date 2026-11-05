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

O dry-run e o `--diagnosticar` continuam sem argumentos obrigatórios (nenhum dos dois exige `--destino-esperado`). O `--apply` exige **todas** as travas: `--apply`, `ML_FULL_STOCK_PUBLISH_ENABLED=true`, a 029 aplicada e mais quatro argumentos do chamador.

| Argumento | Regra |
|---|---|
| `--marca <m>` | Repetível. Allowlist: `barbours`, `kokeshi`, `lescent`, `rituaria`. A fonte é **filtrada** para essas marcas; marca com fotografia que não foi pedida sai em `marcas_ignoradas` e nunca é gravada. Marca pedida sem fotografia faz o publicador recusar. |
| `--raw-run <marca>=<run_id>` | Um por marca pedida. Antes de qualquer DELETE/INSERT, toda linha lida (snapshot, ponte e status) da marca/data precisa ter esse `run_id`. Além disso, em `raw.ml_full_stock_runs` o run precisa ter pedido a marca, ter começado na data (fuso de São Paulo), estar `completo`/`parcial` e ter registrado `PUBLICADA` para ela. Se algo divergir, ele recusa. Depois, a **coerência** da fotografia do run, por marca pedida: as invariantes do produtor para marca `PUBLICADA` (tabela abaixo). Falha = `fonte_incoerente: ...` (só contagens), `nao_iniciada`. |
| `--publish-run-id <uuid hex 32>` | Gerado pelo chamador **antes** do apply e gravado como `source_run_id`. Se o id já existir em alguma fato, ele recusa (a checagem roda antes e de novo dentro do lock). |
| `--destino-esperado <arquivo.json>` | O arquivo **aprovado** gerado pelo `-Identificar` do pacote humano, no formato que o `identificar` do helper `ml_full_neon_db.py` grava. O publicador usa só `identidade.alvo_neon`. Sem o argumento, com arquivo ilegível ou **sem identificador forte** (`system_identifier`, ou o par `neon_tenant_id`+`neon_timeline_id`, não nulos), recusa antes de qualquer conexão (`nao_iniciada`; `destino_nao_identificavel`). Metadados genéricos iguais nunca bastam. Dentro da transação de escrita, logo depois do advisory lock e **antes de qualquer DELETE**, lê a impressão digital do banco **com a conexão da própria transação** e compara campo a campo; null só bate com null. As queries e a normalização são as mesmas do helper: `current_database`, `server_version_num`, `em_recuperacao`, `system_identifier`, `neon_tenant_id`, `neon_timeline_id`, `alembic_version_num` e `modo`. Destino vivo sem identificador forte = `destino_nao_identificavel`. Se divergir, recusa com `destino_divergente: alvo_neon.<campo>` (só o nome do campo, sem valores), exit 2 e `revertida_comprovada`. A **mesma conferência** roda nas duas conexões da auditoria, antes do INSERT (`audit_start`) e antes do UPDATE (`audit_finish`): nenhuma escrita, nem de auditoria, vai para destino não aprovado. |


**Regras de coerência** (`conferir_coerencia`, por marca pedida, só linhas do `--raw-run`, chave `seller_id`+`inventory_id`). Produtor = goca-se/airflow `src/mercadolivre/ingestion/full_stock/snapshot.py`, blob `cbbc4a8b22a8b4c1736788adad5c57bf0f5ac380` (última alteração: commit `d6d65608`, #2173). A fixture de teste segue o produtor; a regra nunca é afrouxada para caber na fixture.

| Regra | Recusa (`fonte_incoerente: <marca> ...`) | Quem garante |
|---|---|---|
| Snapshot com ≥ 1 linha do run (e, pela igualdade abaixo, a ponte também) | `sem linha do run <run> em snapshot` / `em ponte` | **Publicador**, não o produtor: ele publica marca sem anúncio com 0 linhas (L424 itera conjunto vazio; `publicar_marca` L538 pula listas vazias). Para o publicador, marca pedida sem fotografia é recusa, a mesma de `marca_pedida_sem_fotografia`. |
| Snapshot sem duplicata | `N linha(s) duplicada(s) no snapshot` | L424: `for inv in sorted({...})`, uma linha por `inventory_id` distinto |
| Inventários do snapshot ⊂ ponte | `N inventario(s) do snapshot fora da ponte` | L424: o snapshot nasce só de `inventory_id` da ponte |
| `inventory_id` não nulos da ponte ⊂ snapshot (ponte com `inventory_id` nulo/vazio é legítima e não conta) | `N inventario(s) da ponte fora do snapshot` | L424: `if p["inventory_id"]`; todo inventário não nulo gera uma linha (L431 404, L438 erro, L443 OK) |
| `read_status` ∈ {`OK`, `NAO_ENCONTRADO`} | `N inventario(s) do snapshot com read_status fora de [...]` | L62-65 (`LEITURA_*`); `CONTRATO_QUEBRADO` faz `completa = False` (L438-440) e marca incompleta vira `INCOMPLETA_NAO_PUBLICADA` (L330-331); só chega a `PUBLICADA` (L339) com coleta completa |
| Status ⊂ snapshot | `N inventario(s) do status fora do snapshot` | L448-449: status nasce dentro do laço do snapshot |
| Status só de inventário com `read_status=OK` | `N inventario(s) do status sem leitura OK no snapshot` | L443-449: `por_status` só é lido no ramo OK (404 e erro fazem `continue` antes) |
| Status vazio é legítimo | (aceito) | L448: `por_status` vazio não gera linha |
| Uma linhagem por marca | (via `--raw-run` e `conferir_linhagem`) | `publicar_marca` L524-543: DELETE+INSERT das 3 tabelas numa transação |

```bash
# 1) dry-run: o JSON traz `linhagem_raw` (run_id por marca) para montar o --raw-run
python -m pipelines.publish_ml_full_stock_daily --ref-date 2026-10-28 \
       --marca barbours --raw-run barbours=<run_id>        # mesma conferência, sem escrita

# 2) apply só da Barbours (arquivo = identidade_ml_full_<UTC>.json aprovado do -Identificar)
PID=$(python -c "import uuid; print(uuid.uuid4().hex)")    # guardar ANTES de rodar
ML_FULL_STOCK_PUBLISH_ENABLED=true python -m pipelines.publish_ml_full_stock_daily --apply \
       --ref-date 2026-10-28 --marca barbours --raw-run barbours=<run_id> --publish-run-id "$PID" \
       --destino-esperado <identidade_aprovada.json>

# 3) depois de timeout / JSON perdido / exit 1 com estado indeterminado (só leitura, sem flag)
python -m pipelines.publish_ml_full_stock_daily --diagnosticar --ref-date 2026-10-28 --publish-run-id "$PID"
```

**Saída.** O stdout traz **só o JSON final**. Os logs vão para o stderr: os handlers do `get_logger` que apontam para o stdout são redirecionados no `main()`, e qualquer `print` durante o run também vai para o stderr. Erros, inclusive de argumento, saem como JSON com `erro_classe` e `motivo` sanitizado (sem host, porta, usuário, IP ou DSN).

| Exit | Significado | `estado_transacao` |
|---|---|---|
| 0 | dry-run, diagnóstico ou apply com commit e auditoria ok | `commit_confirmado` (no apply) |
| 2 | recusa: travas, contrato, linhagem, `fonte_incoerente`, marca, `publish-run-id` reutilizado, destino esperado ausente, ilegível, `destino_nao_identificavel` ou `destino_divergente`. Nada gravado nas fatos | `nao_iniciada` (inclui destino divergente já no `audit_start`: zero escrita, nem auditoria) ou `revertida_comprovada` (recusa sob o lock; nesse caso o `audit_finish` também confere e, se divergir, não grava e reporta `auditoria_ok=false`) |
| 5 | **commit confirmado com pendência**: as fatos foram gravadas, mas o `audit_finish` falhou ou recusou o destino (`auditoria_ok=false`, `erro_auditoria` com a causa) | `commit_confirmado` |
| 1 | erro (lock, reconciliação, conexão…) | `nao_iniciada`, `revertida_comprovada` (exceção antes do COMMIT, rollback feito) ou `indeterminado` (falhou o próprio COMMIT: rode `--diagnosticar`) |

**`modo` e o fallback `localhost`.** O publicador não recebe o modo por argumento. Ele vale `teste` quando o host **real** da conexão (`connection.info.host`) é loopback ou socket, e `real` fora disso. O helper só aceita loopback no modo teste e recusa no real. Por isso um arquivo aprovado real nunca confere com uma conexão que caiu no `localhost` padrão do `config.py`: diverge em `modo`, além dos outros campos.

**Auditoria só no destino aprovado (R31).** `audit_start` e `audit_finish` usam conexões próprias, mas cada uma confere a identidade (forte + campo a campo) **antes** do INSERT/UPDATE e do commit. Destino divergente no `audit_start` = recusa `nao_iniciada`, sem nenhuma linha em fatos nem em `audit.source_sync_run`. Se a divergência só aparecer depois (transação das fatos ou `audit_finish`), o UPDATE não é feito e a saída traz `auditoria_ok=false` e a causa em `erro_auditoria`; a linha `running` fica só no banco aprovado.

**Fonte consistente (R31).** Snapshot, status, ponte, vendas e runs são lidos numa **única transação `REPEATABLE READ READ ONLY`** no Data Mart (`SET TRANSACTION` como primeiro comando, conferido por `current_setting`; se não ficar assim, falha antes de ler dado). REPEATABLE READ é permitido em hot standby; SERIALIZABLE não. Uma republicação do airflow durante a leitura não intercala runs: o publicador vê o instante do snapshot.

**Diagnóstico.** `--diagnosticar` abre uma transação `READ ONLY` no Neon, sem lock e sem escrita, e devolve:
- as linhas por fato e por marca com aquele `source_run_id` na data;
- as linhas do id em outras datas;
- o `source_run_id` vigente por marca.

O `veredito` é `commit_confirmado` (há linhas) ou `nenhuma_linha`. `nenhuma_linha` não distingue "não houve commit" de "outra publicação já substituiu o dia/marca": por isso o chamador deve diagnosticar antes de republicar.

Contraprova em PG16 descartável (24/24 com a fixture fiel ao produtor, inclui R31: auditoria no banco errado, identidade fraca no arquivo e no destino vivo, fonte intercalada em READ COMMITTED x REPEATABLE READ e ponte vazia): `.artefacts/deliveries/ml-full/publicacao-contrato/teste/contraprova_pg.py` (não versionado).

**Atenção à cadeia:** se o #78 (028, DRE) for mesclado antes, o `down_revision` da 029 precisa ser reencadeado antes do `upgrade`. O certo é aplicar a cadeia que estiver na `main`, nunca uma revisão solta.

### 7.2 Ordem operacional da 037 (T-0018, identificação por inventário)

A 037 (`037_ml_full_inventario_anuncio_ancora.py`, `down_revision = "036"`; nasceu 034 e foi renumerada porque a `main` já tinha 034, 035 e 036) acrescenta três colunas NULL-áveis, sem default, a `marts.fact_ml_full_stock_inventory_daily`: `anuncio_ancora_item_id`, `anuncio_ancora_variation_id` e `anuncios_extras`. O risco não está nas colunas, e sim no **head** do Neon. Outros publicadores leem esse head: a barreira do PMA (`pipelines.channel_offer_sync` / `assert_apply_authorized`) recusa um head que o grafo da imagem não conhece. Aplicar a 037 antes de os workers a conhecerem repete o apagão da passagem para a 033.

Ordem segura (cada passo só depois do anterior):

- **(a) Merge no mktplace (inerte).** Nada muda em produção: o Neon segue sem a 037, a imagem do airflow segue no pin antigo e a API detecta as colunas a cada request (sem elas, responde como antes).
- **(b) Grafo compatível no runtime.** Bump do pin `MKTPLACE_PIPELINES_SHA` no repo do airflow para o commit do merge (o grafo da imagem passa a conhecer a 037), com rollout e prova em runtime: a barreira do PMA aceita o head e os publicadores que validam o grafo continuam passando. Se o runner Windows do PMA ainda estiver ativo, ele também precisa de um commit com a 037 no grafo. É gate humano do supervisor, coordenado com a frente FRESHNESS/PMA. **Não dispare o modo `publicar` da DAG `mercadolivre_full_stock_neon_publish` entre (b) e (c):** a imagem nova já grava as colunas do anúncio-âncora e, sem a 037, o INSERT falha dentro da transação (`revertida_comprovada`, nada gravado). A DAG tem `schedule=None` e só roda por trigger manual.
- **(c) DDL humana.** Só depois do rollout, `alembic upgrade 037` no Neon (o Neon precisa estar na 036; confira `SELECT version_num FROM alembic_version` antes), fora da janela das cadeias. Antes do rollout, o PMA recusaria a head.
- **(c2) Identidade do destino.** A identidade do ML Full inclui `alembic_version_num` (`identidade_destino` no publicador e a cópia fiel em `neon_publish.py`). Depois da (c), a Variable `ML_FULL_NEON_DESTINO_ESPERADO` deixa de bater e o `publicar` recusa com `destino_divergente` (falha fechada). Rode a DAG `mercadolivre_full_stock_neon_publish` em modo `identificar` (só leitura), confira no JSON logado que **só** `alembic_version_num` mudou (de `036` para `037`) e atualize a Variable com o JSON novo. A edição é humana, na UI do Airflow.
- **(d) Deploy da API (Render)** com o serviço da T-0018. Sem a 037, ou sem a 014 legível, a API não quebra: responde com `sku`/`titulo`/`anuncios_extras` nulos e uma limitação declarada.
- **(e) Republicar** para preencher a âncora nas datas já publicadas (linhas publicadas antes da 037 ficam com as três colunas NULL, ou seja, "sem identificação" na tela).

**Dá para republicar a mesma `(ref_date, marca)`? Sim** (lido no código em 05/11/2026):

- O publicador substitui, não recusa. `publicar_em_transacao` faz `DELETE ... WHERE ref_date = :ref_date AND brand = ANY(:marcas)` nas três fatos e depois o INSERT, tudo na mesma transação (`pipelines/publish_ml_full_stock_daily.py` L636-641; contrato na docstring L61-62: "reexecutar substitui, nunca duplica"). Não existe trava de "escopo já publicado" nem de linhas preexistentes.
- A única recusa de reexecução é o **id**: `publish_run_id` já presente em qualquer fato (`SQL_PUBLISH_RUN_ID_EM_USO`, L192-194) recusa com `publish_run_id_reutilizado`, na pré-checagem (L834-835) e de novo sob o lock (L684-686).
- A DAG gera um id novo a cada run e por marca: `novo_publish_run_id = lambda: uuid.uuid4().hex`, chamado em cada `publicar_marca` (`src/marketplace_tower/ml_full_stock/neon_publish.py` L494 e L534, airflow `origin/main` 9fd71be7).
- Logo, **2026-11-04 pode ser republicado** para preencher a âncora: trigger manual da DAG com `{"modo": "publicar", "ref_date": "2026-11-04", "marcas": {<marca>: <raw run_id>}}`. Condições: (b), (c) e (c2) feitos; o `raw run_id` de cada marca é o que está hoje no RAW para a data (o dry-run da DAG confere `linhagem_raw` == o da conf e a linhagem exige o run de 04/11, terminado e `PUBLICADA` para a marca; se o RAW de 04/11 tiver sido substituído ou apagado, a republicação recusa e nada é gravado). A reconciliação pré-COMMIT garante o mesmo número de linhas e a mesma soma do disponível; só as três colunas novas mudam.

Compatibilidade:

- As colunas são NULL-áveis sem default: um publicador anterior à T-0018 funciona com ou sem a 037, porque não as escreve.
- **Gate, não risco aceito:** o publicador novo não grava antes da DDL da (c). Sem a 037, o INSERT dele falha dentro da transação (`revertida_comprovada`, nada gravado).

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
