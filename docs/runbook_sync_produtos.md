# Runbook — Sync Produtos para Neon

**Script:** `pipelines/sync_produtos.py`  
**Destino:** Neon `marts.*` (somente escrita)  
**Fontes (somente leitura):**

| Fonte | Conexão | Requer VPN |
|---|---|---|
| Shopee | `LOCAL_PG_URL` — localhost:5432/mktplace_control | Não (local) |
| ML | `DATAMART_DATABASE_URL` — RDS AWS | Sim |
| TikTok | `DATAMART_DATABASE_URL` — RDS AWS | Sim |

> **Atualizado em 2026-07-01:** o script agora registra cada execução em
> `audit.source_sync_run` (mesmo contrato usado por `daily_performance.py`),
> faz rollback explícito em falha, valida que origem e destino não apontam
> para o mesmo host, lê as brands ativas de `marts.dim_loja` (com fallback
> para a lista hardcoded se o Neon estiver indisponível) e aborta sem commit
> se a fonte retornar menos de 50% das linhas do refresh anterior (ML) ou
> menos de 1.000 linhas num full backfill (TikTok). Ver `pipelines/tests/`
> para os testes dessas guardas.
>
> **Atualizado em 2026-07-03:** os dois bugs de dados conhecidos de
> `fact_shopee_product_monthly` estão **resolvidos em produção** (local e
> Neon): `ref_month` incorreto (Bug 3, dados corrigidos em 2026-07-01) e
> grupos só-cancelados descartados pelo `left` merge (Bug 8, dados
> corrigidos em 2026-07-02 via Gates 1–4B, commit do swap `ccd93fa`). Ver
> `docs/sections/produtos_audit.md`. **Após cada carga Shopee, rodar o
> monitor read-only de invariantes** (seção "Alertas de qualidade" abaixo).

---

## Tabelas, grão e chaves

### `marts.fact_shopee_product_monthly`
- **Grão:** Um produto/variação por SKU por mês por marca
- **Chave única:** `(ref_month, brand, sku_ref_key, product_name)`
- **Deduplica:** Não há duplicatas na fonte; constraint UNIQUE garante idempotência
- **Origem:** Local PG `marts.fact_shopee_product_monthly` (máquina com VPN)

### `marts.fact_ml_produto_ranking`
- **Grão:** Um produto (item_id) por marca — snapshot sem dimensão temporal
- **Chave única:** `(brand, item_id)`
- **Deduplica:** Fonte RDS tem 92 pares `(brand, item_id)` duplicados; mantemos o de maior `gross_revenue` via `DISTINCT ON (brand, item_id) ORDER BY gross_revenue DESC`
- **Origem:** RDS `gold.ml_produto_ranking` — full refresh a cada sync (1.326 linhas)
- **Aviso:** Produtos removidos da fonte permanecem no Neon indefinidamente (sem DELETE). Rodar `--full` não apaga; para limpar stale rows usar a procedure manual abaixo

### `marts.fact_tiktok_product_daily`
- **Grão:** Um produto por dia por marca
- **Chave única:** `(date, product_id)`
- **Deduplica:** Constraint UNIQUE + `ON CONFLICT DO UPDATE` — idempotente
- **Origem:** RDS `gold.tiktok_product_daily` — incremental por `date` com lookback de 7 dias

### `problem_rate` (definição única)
Campo `problem_rate` em `fact_tiktok_product_daily` é copiado verbatim do RDS (pré-calculado).  
No endpoint `/produtos/tiktok`, a agregação mensal usa **média ponderada por `orders`**:
```sql
SUM(problem_rate * orders) FILTER (WHERE problem_rate IS NOT NULL)
/ SUM(orders) FILTER (WHERE problem_rate IS NOT NULL)
```
Retorna `NULL` quando TikTok não fornece a taxa para o período (ex: dados recentes onde `canceled/refunded/returned` são NULL na fonte).

---

## Agendamento recomendado (máquina com VPN)

### Contexto
O sync requer acesso simultâneo a:
- RDS AWS (VPN obrigatória para ML e TikTok — confirmar sempre que a VPN estiver ativa antes do horário agendado)
- Postgres local (Shopee, localhost)
- Neon (internet pública — sem VPN)

A máquina local Windows já tem VPN + Postgres local + Python com psycopg2. Usar **Windows Task Scheduler** é o caminho de menor atrito. Alternativa futura: EC2 na mesma VPC do RDS.

Há **dois pipelines** a agendar, não apenas um — e essa distinção é a causa raiz do atraso observado em 2026-07-01 (Neon ~8-10 dias defasado): `daily_performance.py` alimenta a tabela principal do dashboard (`fact_marketplace_daily_performance`) e nunca foi agendado; `sync_produtos.py` alimenta as tabelas de Produtos e também não estava agendado.

### Frequências sugeridas (confirmar com o time antes de ativar)

| Pipeline | Fonte | Horário | Justificativa |
|---|---|---|---|
| `daily_performance.py` | ML | 06:00 | Fato principal do dashboard; roda primeiro pois Produtos ML depende de RDS estar acessível na mesma janela de VPN |
| `daily_performance.py` | TikTok | 06:10 | Idem |
| `daily_performance.py` | Shopee + shopee-stats + shopee-ads | 06:20 | Arquivos locais, não depende de VPN — pode rodar em paralelo às demais, mas mantido sequencial para simplificar o agendamento inicial |
| `sync_produtos.py` | ML | 06:35 | Depois do daily ML — mesma janela de VPN, evita reconectar |
| `sync_produtos.py` | TikTok | 06:45 | Depois do daily TikTok |
| `sync_produtos.py` | Shopee | 06:55 | Só quando houver arquivos novos em `shopee/{brand}/` — recomendação: rodar sempre (idempotente) e monitorar `rows_extracted=0` como sinal de "sem novidade", em vez de detectar arquivo novo antecipadamente |

Esses horários pressupõem execução sequencial numa única máquina com um único slot de VPN. Se a VPN suportar múltiplas sessões, ML/TikTok/Shopee do mesmo pipeline podem rodar em paralelo — mas **nunca a mesma fonte duas vezes ao mesmo tempo** (ver guarda de concorrência abaixo).

### Guarda de concorrência (lock file)

> **Superseded pela Fase 3A (seção abaixo).** O texto e os comandos que
> estavam aqui (`run_with_lock.ps1` com `Test-Path`/`New-Item` e 6 tarefas
> `schtasks` separadas para `daily_performance`/`sync_produtos`) descreviam
> uma proposta ad-hoc anterior à auditoria formal. Foram **removidos** por
> estarem desatualizados em relação ao `scripts/run_with_lock.ps1` real
> (lock atômico por PID, não por `Test-Path`) e por proporem uma agenda de
> 6 tarefas independentes que a Fase 3A substituiu por 2 tarefas
> orquestradas (ver "Fase 3A — Automação preparada" abaixo, que é a fonte
> de verdade atual). Manter os dois textos em paralelo arriscava alguém
> copiar o snippet errado.

### Reprocessamento manual e observabilidade

- **Reprocessar manualmente**: os mesmos comandos podem ser executados a qualquer momento fora do agendamento; sendo idempotentes (`ON CONFLICT DO UPDATE`), não há risco de duplicar dados.
- **Fonte defasada**: consultar `audit.source_sync_run ORDER BY started_at DESC` — se `MAX(finished_at)` de uma fonte for anterior a `CURRENT_DATE - 1`, a fonte está defasada. Sugestão futura: expor isso como um card de alerta no dashboard (`/health-datasource` já existe como base) ou um script separado que roda após todos os syncs e falha (`exit 1`) se alguma fonte estiver com mais de 48h sem sucesso — para que o Task Scheduler ou um monitor externo (ex.: healthcheck HTTP) sinalize o atraso.

> **Pré-requisito:** A VPN deve estar ativa no momento da execução. Se usar VPN com auto-connect na inicialização do Windows, nenhuma ação extra é necessária. Se for VPN manual, adicionar dependência ou usar o trigger "Ao fazer login".

---

## Comandos de execução

### Sync incremental normal (rodar manualmente ou via Task Scheduler)

```bash
# Todas as fontes
python pipelines/sync_produtos.py --source all

# Por fonte individual
python pipelines/sync_produtos.py --source ml
python pipelines/sync_produtos.py --source tiktok
python pipelines/sync_produtos.py --source shopee

# TikTok com lookback maior (ex: corrigir dados dos últimos 30 dias)
python pipelines/sync_produtos.py --source tiktok --days 30
```

### Full backfill (primeiro uso ou recuperação total)

```bash
# Shopee e TikTok full + ML (sempre full)
python pipelines/sync_produtos.py --source all --full

# Apenas TikTok desde 2025-10-01
python pipelines/sync_produtos.py --source tiktok --full
```

### Variáveis de ambiente necessárias

```
DATABASE_URL=<neon-connection-string>
DATAMART_DATABASE_URL=<rds-connection-string>
LOCAL_PG_URL=postgresql://postgres:postgres@localhost:5432/mktplace_control  # opcional, padrão acima
```

Carregar do `.env` na raiz do projeto (o script usa `load_dotenv` automaticamente).

---

## Recuperação de falhas

### Falha durante sync TikTok ou Shopee
O script usa `ON CONFLICT DO UPDATE` dentro de **uma única transação por fonte** (commit só no final). Se falhar, nenhum dado é gravado para aquela fonte naquele run. Rodar novamente o mesmo comando re-sincroniza de onde parou (via lookback/incremental).

### Falha durante sync ML
Full refresh em transação única. Se falhar, Neon mantém os dados do refresh anterior intactos.

### Neon retorna erro de conexão
Verificar `DATABASE_URL` e conectividade. O Neon tem sleep automático em planos gratuitos — a primeira conexão pode demorar 2-3s.

### RDS inacessível (VPN desconectada)
```
psycopg2.OperationalError: could not connect to server
```
Reconectar VPN e re-rodar. Nenhum dado parcial é gravado.

### Stale rows no ML (produto removido da fonte)
```sql
-- Listar produtos no Neon que não existem mais na fonte RDS
-- Executar no psql conectado ao Neon:
SELECT n.brand, n.item_id, n.title, n.gross_revenue
FROM marts.fact_ml_produto_ranking n
WHERE NOT EXISTS (
    SELECT 1 FROM dblink('rds_conn', 'SELECT item_id FROM gold.ml_produto_ranking')
    AS r(item_id text) WHERE r.item_id = n.item_id
);
-- Se necessário, DELETE FROM marts.fact_ml_produto_ranking WHERE item_id IN (...);
```
Alternativa prática: truncate + full backfill quando suspeitar de muitos stale rows:
```bash
# 1. Truncar tabela ML no Neon (cuidado: apaga todos os dados)
# psql $DATABASE_URL -c "TRUNCATE marts.fact_ml_produto_ranking RESTART IDENTITY;"
# 2. Re-popular
python pipelines/sync_produtos.py --source ml
```

### Verificação rápida de saúde do Neon

```bash
python - <<'EOF'
import os; from dotenv import load_dotenv; load_dotenv()
import psycopg2
c = psycopg2.connect(os.environ["DATABASE_URL"])
cur = c.cursor()
cur.execute("""
    SELECT 'shopee' AS t, COUNT(*) AS n, MAX(ref_month) AS max_d FROM marts.fact_shopee_product_monthly
    UNION ALL
    SELECT 'ml', COUNT(*), MAX(refreshed_at)::date FROM marts.fact_ml_produto_ranking
    UNION ALL
    SELECT 'tiktok', COUNT(*), MAX(date) FROM marts.fact_tiktok_product_daily
""")
for r in cur.fetchall(): print(r)
EOF
```

Saída esperada (atualizado após a correção do Bug 8 em 2026-07-02 — ver `docs/sections/produtos_audit.md` Bug 3/Bug 5/Bug 8):
```
('shopee', 2471, datetime.date(2026, 5, 1))
('ml',     1486, datetime.date(2026, 7, 1))
('tiktok', 173920, datetime.date(2026, 6, 29))
```
Nota: das 2.471 linhas Shopee, 40 têm `gmv = 0` — são grupos com somente
pedidos cancelados, presença **intencional** desde o fix do Bug 8 (eles
ficam fora do Pareto, que só considera `gmv > 0`).
Se `shopee` aparecer com `max_d` além do mês corrente ou contagem voltando a subir de forma inexplicada, investigar antes de assumir que é frescor normal — já houve um bug de parsing de data que inflava `ref_month` para meses futuros (Bug 3).

---

## Alertas de qualidade

Após cada sync, verificar:

1. **Shopee**: `COUNT` ≥ contagem do mês anterior — redução indica problema na fonte
2. **ML**: `COUNT` entre 1.200 e 1.500 — variação grande indica truncamento ou explosion na fonte
3. **TikTok**: `MAX(date)` ≥ D-2 — atraso indica falha silenciosa no incremental

### Monitor de invariantes do Bug 8 (Shopee — rodar após cada carga)

```bash
# Completo: invariantes do mart + reconciliação contra os XLSX locais
python -m pipelines.reconciliation.monitor_bug8_invariants

# Só invariantes do mart (máquina sem os arquivos-fonte)
python -m pipelines.reconciliation.monitor_bug8_invariants --skip-source
```

Somente leitura (Neon com sessão read-only; nunca toca `DATAMART_DATABASE_URL`).
Valida **invariantes**, não snapshots — funciona para qualquer carga futura:
duplicatas/nulos/negativos na chave do mart, coerência de linhas
só-canceladas (`gmv=0`, `cancel_rate=100`), consistência de
`cancel_rate_pct`, e agregados por marca×mês contra a fonte XLSX
(`canceled_orders` do Neon menor que o da fonte = regressão ao `left`
merge do Bug 8). Exit code ≠ 0 em divergência — adequado para encadear
após o sync no Task Scheduler quando o agendamento for ativado.

**Retenção dos objetos de segurança do Bug 8**: os backups/stagings
`marts.fact_shopee_product_monthly_{backup,staging}_bug8_neon_20260702_232445`
(Neon) e `..._{backup,staging}_bug8_20260702_150840` (PG local) devem ser
preservados até pelo menos **1 carga real posterior do ETL Shopee validada
com sucesso por este monitor + 7 dias de observação**; qualquer remoção
exige autorização explícita.

---

## Fase 3A — Automação preparada, NÃO ativada (2026-07-03, revisada)

Esta seção documenta a auditoria completa das cargas, a decisão sobre o
host, o endurecimento operacional implementado e a agenda proposta. **Nenhuma
tarefa foi criada no Windows Task Scheduler** — só código, testes e esta
documentação. Ativação real é a Fase 3B, que exige autorização explícita
separada.

> **Revisão 1 (2026-07-03)**: a primeira versão desta fase foi reprovada em
> revisão por 10 problemas concretos (preflight guardado mas não amarrado à
> execução real, working directory não garantido, agenda Shopee incompleta,
> aspas inválidas no `schtasks /tr`, health check com fontes silenciosamente
> ausentes, corrida no lock, loader Shopee sem trava de host, credenciais
> hardcoded em `preflight.py`, documentação duplicada).
>
> **Revisão 2 (2026-07-03)**: a versão corrigida da Revisão 1 ainda foi
> reprovada por mais 7 pontos: (1) `last_run_failed` não reprovava o status
> geral sozinho; (2) os 3 checks de arquivo Shopee usavam o mesmo glob
> (não distinguiam orders/stats/ads) e uma whitelist de marcas própria em
> vez da lista oficial do conector; (3) 2 tarefas agendadas em horários
> separados não garantiam que a primeira tivesse terminado antes da
> segunda começar (health check podia rodar antes do fim real da
> primeira); (4) nenhum timeout individual por step — uma fonte travada
> podia consumir o timeout global inteiro; (5) `schtasks /create` simples
> não representa `StartWhenAvailable`/`MultipleInstancesPolicy`/
> `ExecutionTimeLimit`, e o horário 06:00 não tinha justificativa
> verificada; (6) `Stop-Process` no timeout não aguardava confirmação real
> de término antes de liberar o lock, e `LockName` não era validado contra
> path traversal; (7) documentação afirmava garantias (health check
> "sempre por último") que o código de 2 tarefas não sustentava de
> verdade. Esta seção descreve o desenho **corrigido pela segunda vez**
> (pipeline único `full_daily`, timeout por step, XML do Task Scheduler,
> lock com espera pós-kill); a tabela de agenda e a matriz de cargas
> abaixo já refletem esta versão final.

### Matriz das cargas

| Carga | Comando | Interpretador | Diretório de trabalho | Fonte | Destino | VPN? | PG local? | Arquivos locais? | Janela | Idempotente? | Lock | Registra em `audit.source_sync_run`? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| TikTok diário | `python -m pipelines.ingestion.daily_performance --source tiktok --mode incremental` | `apps/api/.venv/Scripts/python.exe` | raiz do repo | RDS `gold.tiktok_brand_daily` | Neon `fact_marketplace_daily_performance` | Sim | Não | Não | incremental 3d (`--days`); backfill usa `--mode backfill --days N` | Sim (`ON CONFLICT (date, loja_id, marketplace_id) DO UPDATE`) | nenhum hoje — `run_with_lock.ps1` proposto | Sim (`{source}_daily`) |
| Mercado Livre diário | `python -m pipelines.ingestion.daily_performance --source ml --mode incremental` | idem | raiz do repo | RDS `gold.ml_gestao_diaria` | Neon `fact_marketplace_daily_performance` | Sim | Não | Não | idem | Sim (idem) | idem | Sim |
| Shopee diário (+ stats + ads) | `python -m pipelines.ingestion.daily_performance --source shopee[,shopee-stats,shopee-ads] --mode incremental` | idem | raiz do repo | XLSX/CSV em `shopee/{brand}/` (`SHOPEE_DATA_PATH`) | Neon `fact_marketplace_daily_performance` (upsert parcial para stats/ads) | **Não** | **Não** | **Sim** | incremental 3d | Sim (idem) | idem | Sim, 3 `source_name` distintos |
| Produtos TikTok | `python -m pipelines.sync_produtos --source tiktok [--days N]` | idem | raiz do repo | RDS `gold.tiktok_product_daily` | Neon `fact_tiktok_product_daily` | Sim | Não | Não | incremental 7d default; `--full` = desde 2025-10-01, aborta se < 1.000 linhas | Sim (`ON CONFLICT (date, product_id)`) | idem | Sim (`tiktok_product_daily`) |
| Produtos ML | `python -m pipelines.sync_produtos --source ml` | idem | raiz do repo | RDS `gold.ml_produto_ranking` | Neon `fact_ml_produto_ranking` | Sim | Não | Não | full refresh sempre (snapshot); aborta se < 50% do count anterior | Sim (`ON CONFLICT (brand, item_id)`) | idem | Sim (`ml_produto_ranking`) |
| **Produtos Shopee** | `python -m pipelines.sync_produtos --source shopee` | idem | raiz do repo | **PostgreSQL local** `marts.fact_shopee_product_monthly` (`LOCAL_PG_URL`) | Neon `fact_shopee_product_monthly` | Não | **Sim** | Não (lê do PG local, não dos XLSX diretamente) | incremental (mês atual + anterior); `--full` = tudo | Sim (`ON CONFLICT (ref_month, brand, sku_ref_key, product_name)`) | idem | Sim (`shopee_product_monthly`) |
| Monitor Bug 8 | `python -m pipelines.reconciliation.monitor_bug8_invariants [--skip-source]` | idem | raiz do repo | Neon (leitura) + opcionalmente XLSX locais | nenhum (só leitura) | Não | Não | Opcional (camada 2) | roda sob demanda, proposto após cada sync Shopee | N/A (não escreve) | proposto | Não (não é uma carga; ver Riscos abaixo) |

**Impacto se a máquina estiver desligada ou a VPN indisponível**: nenhuma carga roda — os dados existentes no Neon permanecem como estão (nenhuma perda), só ficam mais desatualizados. TikTok/ML ficam bloqueados sem VPN; Shopee (diário e Produtos) não depende de VPN, mas Produtos Shopee depende do PostgreSQL local estar no ar.

**Risco encontrado na auditoria original — CORRIGIDO nesta revisão**: `apps/api/etl/load_shopee_products.py` (o passo que popula o PostgreSQL local a partir dos XLSX, **anterior** a `sync_produtos --source shopee`) lia `os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mktplace_control")` — a mesma variável usada em todo o resto do projeto para o **Neon**, com um fallback de credencial hardcoded. Se chamado por um wrapper que já tivesse carregado o `.env`, `DATABASE_URL` apontaria para o Neon e o script escreveria direto lá, fora do controle de `sync_produtos.py`. Corrigido: o loader agora exige `LOCAL_PG_URL` explicitamente (`_get_local_pg_url()`, sem fallback com credencial) e bloqueia qualquer host que não seja `localhost`/`127.0.0.1`/`::1`, levantando `RuntimeError` sanitizado (nunca expõe usuário/senha) se a variável faltar ou apontar para um host remoto. A resolução é *lazy* — só acontece dentro de `main()`, nunca no import do módulo — porque `reconcile_bug8_canceled_only.py`, `monitor_bug8_invariants.py`, `fix_shopee_product_dates.py` e `diagnose_bug8_neon.py` importam só as funções puras (`BRANDS`, `DDL`, `_aggregate`, `_load_brand`) sem precisar de conexão nenhuma; ver `apps/api/etl/tests/test_load_shopee_products_local_pg_guard.py`. Ainda assim, `apps/api/etl/load_shopee_products.py` **continua fora da agenda proposta** — permanece um passo manual, disparado por humano quando novos exports chegam.

### Decisão sobre o host: notebook + VPN

**Aceitável como solução PROVISÓRIA, com limitações que devem ficar visíveis a quem for autorizar a ativação.**

Motivos para aceitar agora:
- É o único host com VPN configurada para o RDS, PostgreSQL local com os dados Shopee, e acesso aos arquivos `shopee/` — não existe hoje alternativa pronta sem reconstruir esses três acessos em outra máquina.
- O impacto de falha é baixo: nenhuma carga escreve dados incorretos silenciosamente sem os testes/guardas já existentes (`_assert_distinct_targets`, `MIN_ROWS_RATIO`, aborto sem commit), e o Neon simplesmente fica desatualizado até a próxima execução manual ou agendada — não há corrupção de dados por a máquina estar desligada.

Limitações que tornam isso **provisório, não definitivo**:
- A máquina precisa estar ligada e sem suspender/hibernar no horário agendado — não há redundância nem re-tentativa automática entre dias.
- A VPN precisa estar conectada manualmente (ou com auto-connect configurado) antes do horário de TikTok/ML — se cair, essas duas cargas falham silenciosamente até alguém notar (por isso o health check, ver abaixo).
- O RDS não é alcançável pelo Render (onde a API roda) — só por esta máquina. Se o notebook for desligado por período longo, TikTok/ML no Neon ficam cada vez mais atrasados sem nenhum outro caminho de atualização.
- Task Scheduler do Windows não tem retry nativo elegante nem alerta externo — depende de alguém rodar o health check manualmente ou de uma automação externa futura consultar o exit code / os logs.
- Credenciais do Windows Task Scheduler: uma tarefa agendada armazena a conta de execução no XML da tarefa, não a senha do banco — mas se a tarefa for exportada/inspecionada, os comandos podem aparecer nos logs do Event Viewer do Windows. Por isso todos os scripts desta automação leem credenciais de variável de ambiente (`.env`), nunca de argumento de linha de comando — `run_with_lock.ps1` agora recusa rodar se detectar uma connection string com usuário:senha embutidos em qualquer argumento.

Caminho futuro mais robusto (não implementado, fora de escopo): mover TikTok/ML para uma instância na mesma VPC do RDS (ex.: EC2 pequena) com acesso direto sem VPN, rodando os mesmos scripts via cron; manter Shopee local (arquivos + PostgreSQL local não têm por que migrar); reportar tudo a um monitor externo (não implementado nesta fase — está fora do escopo de "sem alerta externo" desta etapa).

### Endurecimento implementado

**`scripts/run_with_lock.ps1`** (reescrito nesta revisão e endurecido em duas revisões seguintes):
- **Lock atômico**: `[System.IO.File]::Open(..., FileMode.CreateNew, ..., FileShare.None)` — uma única chamada de SO que falha com `IOException` se o lock já existir.
- **O lock é atualizado para o PID do processo FILHO logo após ele ser iniciado** (corrigido na revisão mais recente) — troca atômica via escrever num arquivo temporário e `[System.IO.File]::Replace` (rename atômico ao nível do sistema de arquivos: o caminho do lock nunca fica ausente/vazio/truncado em nenhum instante observável, então nenhuma outra tentativa concorrente pode interpretar esse instante como "lock morto"). Isso importa porque o processo wrapper (`run_with_lock.ps1`) fica vivo durante toda a espera do filho — sem essa troca, o lock sempre pareceria "vivo" (é o próprio wrapper) até ele mesmo terminar, mesmo que o processo filho real tenha ficado travado.
- **Recuperação por PID vivo/morto, não por idade**: vivo → `BLOCKED` sempre, não importa há quanto tempo o lock existe; morto → remove o lock órfão e tenta adquirir de novo uma única vez.
- **`-WorkingDirectory`** (default: raiz do repo) sempre passado a `Start-Process -WorkingDirectory` — garante o diretório certo independentemente de onde o Task Scheduler ou quem chamou o script estava posicionado. Validado com um teste Pester que invoca o script a partir de `C:\Windows\System32` de propósito.
- `-TimeoutSeconds` (default 3600): mata o processo filho se exceder (`Stop-Process -Force`) e **aguarda até 30s a confirmação real de término** (`Get-Process` deixa de encontrar o PID). **O que acontece depois desses 30s foi corrigido na revisão mais recente**: se o filho **realmente terminou** dentro da espera, o lock é removido normalmente; se o filho **continuar vivo** mesmo após os 30s, o lock **NÃO é removido** — fica preservado contendo o PID (real, vivo) do filho, para que a próxima tentativa encontre esse PID vivo via `Test-LockOwnerAlive` e fique corretamente `BLOCKED`, em vez de "recuperar" um lock e rodar uma segunda execução ao lado de um processo zumbi que pode continuar escrevendo em banco/arquivo. Quando esse PID finalmente morrer (fora dessa execução), uma tentativa futura reconhece o PID morto e recupera o lock normalmente. **Nunca afirmar que o lock é sempre liberado após os 30s de espera — essa era exatamente a falha corrigida.**
- **`LockName` validado** contra `^[A-Za-z0-9_-]+$` **antes de qualquer acesso a disco** — corrigido nesta revisão: `LockName` vira diretamente parte de um caminho de arquivo (`logs\<LockName>.lock`); sem essa validação, um valor como `..\..\algo` poderia apontar o lock para fora de `logs\`.
- stdout/stderr em arquivos **separados e datados**: `logs/<lock>_<yyyyMMdd_HHmmss>_stdout.log` / `..._stderr.log`.
- Recusa rodar se qualquer argumento parecer uma connection string com credenciais embutidas (`usuario:senha@`).
- Imprime uma linha final `STATUS=SUCCESS|FAILED|BLOCKED EXITCODE=N WORKDIR=... STDOUT_LOG=... STDERR_LOG=...`.

**`pipelines/ops/orchestrate.py`** (reescrito na revisão seguinte — 2 pipelines → 1): amarra o preflight à execução real e sequencia os passos de um **único pipeline** (`full_daily`) **em processo** — nunca por intervalo de horário do Task Scheduler nem por uma segunda tarefa agendada com folga. Um desenho anterior com 2 pipelines/tarefas separados (`daily_ingestion` @ 06:00 + `produtos_and_monitor` @ 06:35) foi descartado: não havia garantia de que o primeiro tivesse terminado antes do segundo começar (o primeiro podia legitimamente rodar até seu timeout total enquanto o segundo começava por horário) — o health check do segundo podia rodar **antes** do fim real do primeiro. Para cada passo: se houver `preflight_source`, roda o preflight primeiro — aprovado → executa o comando real; bloqueado → o passo vira `BLOCKED` e **o comando real nunca é chamado**. Se o passo declara `depends_on`, só roda se todas as dependências tiverem terminado com `SUCCESS`; caso contrário vira `SKIPPED`. `always_run=True` (usado pelo health check) roda mesmo se algo antes falhou ou foi bloqueado. **Cada `Step` agora tem um `timeout_seconds` individual** (900s para as fontes diárias, 600s para Produtos, 300s para o monitor do Bug 8, 180s para o health check — soma de 6780s): `_default_executor` passa esse valor a `subprocess.run(..., timeout=...)`; se estourar, `subprocess.TimeoutExpired` é capturado, o passo vira `FAILED` e a orquestração **segue para o próximo passo** — uma fonte travada (ex.: ML preso numa query longa) nunca consome o timeout global nem trava as fontes independentes seguintes (TikTok/Shopee continuam normalmente).

Pipeline único `full_daily`: `daily_ml` → `daily_tiktok` → `daily_shopee_orders` → `daily_shopee_stats` → `daily_shopee_ads` → `sync_produtos_ml` → `sync_produtos_tiktok` → `sync_produtos_shopee` → `monitor_bug8` (só se `sync_produtos_shopee` = `SUCCESS`) → `health_check` (sempre, por último — garantido pela posição dele como último item da tupla + `always_run=True`; não existe segunda tarefa/segundo lock depois desta para rodar antes).

```bash
python -m pipelines.ops.orchestrate --pipeline full_daily
```

**`scripts/run_task.ps1`** (atualizado na revisão seguinte — 2 `TaskKey`s → 1): wrapper fino chamado pelo Task Scheduler — recebe só uma `-TaskKey` curta e resolve internamente o lock/timeout/comando reais via `orchestrate.py`, delegando a `run_with_lock.ps1`. Agora só conhece `full_daily`, com `-TimeoutSeconds 9000` (2h30) — **maior que a soma dos timeouts individuais dos steps internos (6780s)**, com margem de ~2220s (~33%) documentada para overhead de spawn de processo Python, imports pandas/sqlalchemy, e latência de rede VPN/Neon entre passos. Se essa margem não existisse, o timeout **externo** mataria o processo pai antes que os timeouts **internos** por step tivessem chance de proteger as fontes independentes.

**`pipelines/ops/preflight.py`** (endurecido nesta e na revisão seguinte): `SELECT 1` read-only contra RDS, Neon ou PostgreSQL local, e checagem de arquivos Shopee — antes de disparar a carga real. `LOCAL_PG_URL` exigida explicitamente, sem fallback com credencial hardcoded, **e agora também restrita ao allowlist de host** (`localhost`/`127.0.0.1`/`::1`), mesma guarda do loader Shopee. Toda conexão de diagnóstico abre a sessão com `set_session(readonly=True)`. Os checks de arquivos Shopee foram **separados por padrão de arquivo real** (corrigido nesta revisão — antes os 3 usavam o mesmo glob, o que deixaria `shopee-stats`/`shopee-ads` passarem mesmo sem os arquivos certos): `check_shopee_orders_files()` procura `Order.all*.xlsx`, `check_shopee_stats_files()` procura `*.shopee-shop-stats.*.xlsx`, `check_shopee_ads_files()` procura `Dados*.csv` — todos contra a lista **oficial** de marcas do conector real (`pipelines.connectors.shopee.connector.BRANDS_IN_SCOPE`, nunca uma whitelist duplicada). **Decisão documentada**: se qualquer marca oficial estiver sem o arquivo esperado, a fonte inteira é **bloqueada** (não só um aviso) — evita que uma carga parcial (algumas marcas sem dado) seja registrada em `audit.source_sync_run` como "success" sem sinalizar a lacuna. As mensagens de bloqueio citam só os nomes das marcas ausentes, nunca o valor de `SHOPEE_DATA_PATH`.

```bash
python -m pipelines.ops.preflight --source tiktok_daily
python -m pipelines.ops.preflight --source produtos_shopee
```

**`pipelines/ops/health_check.py`** (reescrito nesta e na revisão seguinte): duas dimensões de frescor, deliberadamente separadas — (1) frescor de **execução**, via `audit.source_sync_run`, contra uma lista explícita `EXPECTED_SOURCES` (8 fontes) — uma fonte esperada sem nenhum histórico é sempre reportada como atrasada, nunca omitida; (2) frescor de **dado**, via `MAX(date/refreshed_at/ref_month)` avaliado contra um threshold em dias, com `fact_shopee_product_monthly` (`manual_monthly`) isento do threshold por natureza. Duas correções desta revisão: **(a)** cada fonte agora expõe `execution_stale` e `last_run_failed` como campos **separados** — antes, uma falha na última execução só virava atenção geral se também estourasse o threshold de frescor; agora `last_run_failed=True` sozinho já torna `stale=True`, mesmo com um sucesso recente dentro do threshold (um job quebrado não fica mais mascarado de OK); **(b)** uma data **no futuro** em qualquer tabela (`days_since < 0`) é sempre sinalizada como **erro de qualidade** (nunca "fresca"), inclusive para `fact_shopee_product_monthly` — regressão direta do Bug 3 (`ref_month` projetado para meses futuros por bug de parsing). Cada fonte/tabela no JSON traz um campo `reason`. Também roda as invariantes do Bug 8 (reaproveitando `monitor_bug8_invariants.check_db_invariants`, print informativo suprimido).

```bash
python -m pipelines.ops.health_check          # saída legível
python -m pipelines.ops.health_check --json   # saída estruturada para automação
```

**`pipelines/ops/schedule_plan.py`** (reescrito nesta e na revisão seguinte — 8 tarefas → 2 → **1**): declara só os dados da agenda proposta e sabe renderizar, como TEXTO: o comando `schtasks /create` simples **e** a definição XML equivalente do Task Scheduler — **não importa `subprocess`, `os.system` nem qualquer API do Task Scheduler**. A tarefa chama `run_task.ps1 -TaskKey full_daily` com a convenção de aspa dobrada `""..."" ` no `/tr` (validada com o parser real do PowerShell, zero erros). `schtasks /create` simples **não representa com segurança** `MultipleInstancesPolicy`, `StartWhenAvailable` nem `ExecutionTimeLimit` — por isso `render_task_scheduler_xml()` gera a definição XML equivalente (também só texto, nunca aplicada) com essas 3 configurações; ver "Configurações propostas do Task Scheduler" abaixo. `render_schtasks_command(task, allow_overwrite=False)` nunca inclui `/f` por padrão.

### Configurações propostas do Task Scheduler (texto para revisão, Fase 3B)

`schtasks /create` com flags simples não cobre 3 configurações de segurança operacional pedidas nesta revisão — por isso a definição de referência para a Fase 3B é o **XML** gerado por `pipelines.ops.schedule_plan.render_task_scheduler_xml()`, nunca o `schtasks /create` simples:

| Configuração | Valor proposto | Motivo |
|---|---|---|
| `MultipleInstancesPolicy` | `IgnoreNew` | Impede o **próprio Task Scheduler** de iniciar uma nova instância enquanto a anterior ainda roda — camada adicional **em cima** do lock de arquivo (`run_with_lock.ps1`), não no lugar dele. Duas proteções independentes contra concorrência. |
| `StartWhenAvailable` | `true` | Se o notebook estiver desligado/suspenso no horário agendado, a tarefa roda assim que a máquina estiver disponível de novo, em vez de simplesmente pular o dia — relevante dado que o host é um notebook (ver decisão sobre o host acima), não um servidor sempre ligado. |
| `ExecutionTimeLimit` | `PT2H40M` (9600s) | Hard-limit do **próprio Task Scheduler**, independente do `-TimeoutSeconds` (9000s) do `run_with_lock.ps1` — **deliberadamente maior, não igual** (corrigido na revisão mais recente): depois que o wrapper detecta seu próprio timeout de 9000s, ele ainda gasta tempo chamando `Stop-Process`, aguardando até 30s a confirmação real de término do filho, e gravando os logs finais. Se `ExecutionTimeLimit` fosse igual a 9000s, o Task Scheduler poderia matar o **wrapper** no meio dessa limpeza, antes de ele decidir se o lock deve ou não ser removido. 9600s dá 600s de margem para essa limpeza. |
| `Settings/Enabled` | `false` **sempre** | **Corrigido na revisão mais recente**: a tarefa é gerada **desativada** de propósito — o trigger continua totalmente configurado (horário, recorrência diária) para a revisão humana ver quando ela rodaria, mas o Task Scheduler nunca dispara uma tarefa com `Settings/Enabled=false`, mesmo com o trigger habilitado. A ativação (mudar para `Enabled=true`) é um passo **manual e separado** da importação — só depois de importar o XML, consultar a tarefa no Agendador de Tarefas e validar que a definição importada bate com o que foi revisado aqui. `render_task_scheduler_xml()` não aceita nenhum parâmetro para habilitar — não há como chamá-la e obter `Enabled=true` por engano. |

Para ver o XML exato (texto, nada aplicado):
```bash
python -m pipelines.ops.schedule_plan
```

Nenhum `schtasks /create /xml` foi executado — a importação real do XML é a Fase 3B.

### Agenda proposta (não ativada)

**UMA ÚNICA tarefa** no Task Scheduler — a dependência real entre passos (monitor do Bug 8 só depois do sync de Produtos Shopee ter **terminado de verdade**; health check só depois de **tudo**) é resolvida **dentro** da tarefa por `pipelines.ops.orchestrate.PIPELINES["full_daily"]` (sequencial em processo, sob um único lock), não por agendar horários com folga entre tarefas separadas — essa fragilidade (a segunda tarefa podia começar antes da primeira terminar) foi corrigida nesta revisão fundindo as duas tarefas anteriores numa só.

| Horário | Tarefa (`TaskKey`) | Passos internos (em ordem, via `orchestrate.py`, cada um com timeout individual) | Timeout externo (lock) | VPN? |
|---|---|---|---|---|
| 06:00 (**hipótese, não confirmada** — ver nota abaixo) | `mktplace_full_daily` (`full_daily`) | daily_ml(900s) → daily_tiktok(900s) → daily_shopee_orders(900s) → daily_shopee_stats(900s) → daily_shopee_ads(900s) → sync_produtos_ml(600s) → sync_produtos_tiktok(600s) → sync_produtos_shopee(600s) → monitor_bug8(300s, só se shopee=SUCCESS) → health_check(180s, sempre, garantido último) | 9000s | ML/TikTok sim; Shopee/Produtos/monitor/health não |

**O horário 06:00 é uma hipótese herdada da proposta original, não uma confirmação read-only de quando RDS (`gold.*`) e os exports Shopee tipicamente ficam disponíveis.** Antes da Fase 3B: confirmar rodando `python -m pipelines.ops.preflight` manualmente por alguns dias nesse horário, ou revisando os timestamps reais de atualização das fontes. Se RDS/Shopee tipicamente atualizam depois das 06:00, a tarefa vai bloquear no preflight (RDS) ou processar arquivos do dia anterior (Shopee) todo dia, até o horário ser ajustado com base em dado real.

Para ver os comandos `schtasks /create` e o XML exatos (texto, nada executado):
```bash
python -m pipelines.ops.schedule_plan
```

Preflight amarrado à execução real? **Sim, obrigatoriamente** — dentro de cada `Step` de `orchestrate.py`, o preflight roda antes do comando real; se bloqueado, o comando real **nunca é chamado**.

Health check é comprovadamente o último passo global? **Sim** — não há segunda tarefa nem segundo lock depois deste: `health_check` é o último item de `PIPELINES["full_daily"]`, com `always_run=True`, dentro do mesmo processo sequencial. Isso só é verdade porque existe **uma única tarefa**; era exatamente essa garantia que faltava no desenho de 2 tarefas anterior.

### Troubleshooting e recuperação

- **Tarefa perdida (processo morto, máquina reiniciada no meio de uma carga)**: o lock em `logs/<nome>.lock` fica órfão com o PID de um processo que não existe mais. `run_with_lock.ps1` detecta isso e recupera automaticamente na próxima tentativa — nunca por idade do arquivo, só por o dono estar vivo ou morto.
- **Uma fonte trava (timeout individual)**: o step correspondente vira `FAILED` após seu `timeout_seconds` individual estourar; os steps seguintes (fontes independentes) continuam normalmente — só `monitor_bug8` depende especificamente de `sync_produtos_shopee`.
- **Task Scheduler mostra "Last Run Result" ≠ 0**: ver `logs/<nome>_<data>_stderr.log` da execução correspondente.
- **Verificar frescor sem esperar o Task Scheduler**: `python -m pipelines.ops.health_check` a qualquer momento — somente leitura. Uma fonte sem nenhum histórico aparece como atrasada; uma data no futuro em qualquer tabela aparece como erro de qualidade, nunca "fresca".
- **Verificar se uma fonte específica pode rodar agora**: `python -m pipelines.ops.preflight --source <fonte>` — não dispara a carga, só diagnostica. Para Shopee, `shopee_daily`/`shopee-stats_daily`/`shopee-ads_daily` checam padrões de arquivo DIFERENTES.
- **Rodar o pipeline completo manualmente**: `python -m pipelines.ops.orchestrate --pipeline full_daily` — preflight amarrado, mesma lógica usada pelo Task Scheduler quando ativado.
- **Rodar o monitor do Bug 8 manualmente**: `python -m pipelines.reconciliation.monitor_bug8_invariants` (completo) ou `--skip-source`.

### Retenção de logs

Sem limpeza automática nesta fase (`logs/` já é ignorado pelo git). Comando manual/periódico recomendado para reter só os últimos 30 dias:
```powershell
Get-ChildItem "C:\Users\Notebook\Desktop\mktplace\logs" -Filter "*.log" |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
  Remove-Item -Force
```
Não incluído no Task Scheduler nesta fase (nenhuma nova tarefa foi criada).

### Ativação futura (Fase 3B) e desativação

**Ativação — dois passos manuais e SEPARADOS, nunca um só:**
1. Revisar o XML gerado por `render_task_scheduler_xml()` (recomendado, cobre `MultipleInstancesPolicy`/`StartWhenAvailable`/`ExecutionTimeLimit`) ou o comando `schtasks /create` simples (menos completo), confirmar o horário 06:00 com dado real (ver nota acima), e só então importar/criar a tarefa manualmente. **A tarefa importada nasce com `Settings/Enabled=false`** — registrada no Task Scheduler, mas inerte (nunca dispara, mesmo com o trigger configurado).
2. Só depois de consultar a tarefa importada no Agendador de Tarefas (linha de comando ou UI) e confirmar que a definição bate exatamente com o que foi revisado no passo 1, habilitar manualmente (`Settings/Enabled=true`). Nenhum script desta fase faz qualquer um desses dois passos automaticamente.

**Desativação (rollback operacional — nunca rollback de dados)**: remover a tarefa do Task Scheduler (`schtasks /delete /tn "mktplace_full_daily" /f`) não desfaz nenhuma carga já aplicada — os dados no Neon continuam como estavam.

### Riscos residuais conhecidos, não bloqueantes

- **`Update-LockOwnerPid` (troca do PID do lock de wrapper para filho) pode falhar silenciosamente**: se a chamada lançar exceção, `run_with_lock.ps1` só registra `Write-Warning` e **segue em frente** sem abortar a execução (documentado explicitamente no próprio script, não corrigido nesta revisão — corrigir isso redesenharia a estratégia de lock, fora do escopo pedido). Nesse cenário, o lock permanece com o PID do **wrapper** durante toda a execução; se, além disso, o timeout estourar e o processo filho sobreviver ao `Stop-Process`, o lock preservado conteria o PID do wrapper (que está prestes a morrer), não o do filho real — reabrindo, só nessa combinação rara de duas falhas, a mesma janela de corrida que esta revisão existe para fechar. **Deve ser observado nos logs**: `grep` por `"Nao foi possivel atualizar o dono do lock"` em `logs\*_stderr.log` — nenhuma ocorrência esperada em operação normal; qualquer ocorrência merece investigação manual antes da próxima execução daquele lock.

### Gate B1 (2026-07-15) — política crítico/não-crítico, `ok_critical`

Antes deste gate, `orchestrate.py` tratava qualquer step `FAILED`/`BLOCKED` (inclusive `sync_produtos_shopee` bloqueado por `LOCAL_PG_URL` ausente, um gap manual já conhecido) como falha do pipeline inteiro, e `health_check.py` fazia `ok=false` sempre que qualquer fonte estivesse stale — inclusive `fact_marketplace_daily_performance[shopee]`, que fica defasada até alguém atualizar os exports manuais. Resultado: o pipeline reportava `exit 1` quase todo dia, mesmo com ML/TikTok saudáveis, tornando o alerta inútil (todo mundo aprende a ignorar um alarme que sempre dispara).

**Regra nova:**
- `orchestrate.py::Step` ganhou o campo `critical: bool = True` (default preserva todo comportamento antigo). Hoje só `sync_produtos_shopee` é `critical=False`.
- Status geral do pipeline (`compute_overall_status`): **FAILED** se algum step **crítico** falhar/bloquear; **DEGRADED** se só um **não-crítico** falhar/bloquear (ex.: Shopee produtos); **OK** caso contrário. `SKIPPED` nunca conta como falha, mesmo quando é consequência de um step não-crítico bloqueado (ex.: `monitor_bug8` pulado porque `sync_produtos_shopee` foi bloqueado).
- Exit code do processo: `1` só em `FAILED`. `DEGRADED` e `OK` retornam `0`.
- `health_check.py` ganhou `ok_critical` (só fontes/entradas `critical=True` + Bug 8, que é sempre crítico) separado de `ok` (visão completa, mantida só para visibilidade). `main()` usa `ok_critical` para o exit code. Fontes marcadas `critical=False`: `fact_marketplace_daily_performance[shopee]` e `marts.fact_shopee_product_monthly[ref_month]` (ambas ingestão manual Shopee).
- Saída humana agora imprime `ATRASADA-CRITICO`/`ATRASADO-CRITICO` vs. `ATRASADA-CONHECIDO`/`ATRASADO-CONHECIDO`, e duas linhas de status: "STATUS GERAL (inclui conhecidos/manuais)" e "STATUS CRITICO (decide o exit code)".

**Fora deste gate (fica para o Gate B2)**: nenhum step de Gold regional (`gold_regional_incremental`/`sync_region_daily`) foi adicionado a `orchestrate.py` ainda. Scheduler continua desativado (Fase 3B).

### Gate B2 (2026-07-15) — regional (Gold incremental + sync Neon condicional) integrado a `full_daily`

`gold_regional_incremental` (carrega `gold.marketplace_region_daily` no Data Mart via `pipelines.ingestion.gold_regional.loader --incremental`, já implementado e executado manualmente com sucesso num gate anterior) e `sync_region_if_needed` (novo wrapper) entraram em `PIPELINES["full_daily"]`, nesta ordem exata: depois de `daily_shopee_ads`, antes de `sync_produtos_ml`. `sync_region_if_needed` só roda se `gold_regional_incremental` teve `SUCCESS` na mesma execução (`depends_on`).

**Decisão deliberada, diferente do Gate B1**: os dois novos steps são `critical=True` — não há, aqui, nenhum gap manual conhecido e aceito equivalente ao de `sync_produtos_shopee`. Um `FAILED`/`BLOCKED` real de qualquer um dos dois reprova o pipeline inteiro (`FAILED`, exit 1), nunca só degrada. O período de confiança antes de ativar o Task Scheduler vem de **execuções manuais observadas** destes dois steps (já feitas uma vez cada, com sucesso, em gates anteriores), não de relaxar a criticidade.

**`pipelines/ops/sync_region_if_needed.py` (novo)**: evita o custo de `sync_region_daily --sync` sempre fazer `TRUNCATE`+`INSERT` e criar uma tabela de backup nova (`marts.fact_marketplace_region_daily_backup_<tag>`) mesmo quando não há nada de novo — o que aconteceria todo dia se `full_daily` chamasse `--sync` incondicionalmente. Fluxo: chama `sync_region_daily.run_diagnose()` (somente leitura nos dois lados); se `needs_sync=False`, retorna sem escrever nada (`NO_OP`); se `needs_sync=True`, chama `sync_region_daily.run_sync()` uma única vez (sem retry automático, em nenhum dos dois caminhos). As guardas de escrita (`--sync` + `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1`) continuam sendo responsabilidade exclusiva de `sync_region_daily.run_sync` — o wrapper não as duplica nem as afrouxa. Erros de diagnose ou de sync nunca propagam a mensagem nativa da exceção (passam por `sync_region_daily._sanitize_error_message` antes de qualquer print/log).

**Preflight (`pipelines/ops/preflight.py`)**: duas fontes novas em `SOURCE_CHECKS`, ambas somente leitura:
- `gold_regional_incremental`: `check_gold_regional_write` (delega a `write_conn.load_write_secret`/`validate_write_guardrails`/`run_preflight` — confirma que `.env.gold-write.local` existe e valida, que a write URL não é igual à de leitura, e que o preflight somente-leitura do próprio pacote `gold_regional` aprova o alvo: não é réplica, não é `rolsuper`, mesmo cluster físico da leitura, permissão no schema `gold`, tabela já existe) + `check_rds`.
- `sync_region_daily`: `check_sync_region_consent` (só confere `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1` no ambiente, sem abrir nenhuma conexão) + `check_rds` + `check_neon`. Isso garante `BLOCKED` explícito **antes** de `sync_region_if_needed` sequer chamar `run_diagnose`, em vez de deixar uma falha de consentimento aparecer só no meio da execução.

Nenhuma das duas checagens imprime secret/URL/credencial — só mensagens já saneadas (`SecretLoadError`, `PreflightReport.blocking_reasons`, nenhuma delas ecoa a DSN).

**`schedule_plan.py`**: orçamento somado dos timeouts de `full_daily` sobe de 6780s para 7200s (`gold_regional_incremental=300s` + `sync_region_if_needed=120s`). Margem sobre `EXTERNAL_LOCK_TIMEOUT_SECONDS` (9000s) cai de ~33% para ~25% — ainda folgada, nenhuma mudança nos valores numéricos foi necessária (só nos comentários/teste que documentam a soma).

**Scheduler continua desativado (Fase 3B)** — a integração deste gate não ativa nada; fica para um gate futuro (B3/B4), depois de mais execuções manuais observadas.

**Não executado neste gate** (por instrução explícita): `orchestrate.py --pipeline full_daily` real, `gold_regional.loader --incremental` real, `sync_region_daily --sync` real, ativação do Task Scheduler, commit/push.

### Gate B3 (2026-07-15) — rodada manual observada de `full_daily` com os steps regionais

Execução real única de `full_daily` (13:27:34–13:34:20). ML/TikTok/Shopee daily/`gold_regional_incremental`/`sync_region_if_needed`/`sync_produtos_ml`/`sync_produtos_tiktok` todos `SUCCESS`; `gold_regional_incremental` e `sync_region_if_needed` terminaram em `NO_OP` (Data Mart e Neon já em paridade, 33.896 linhas, nenhuma escrita) — confirma o desenho do Gate B2 funcionando como esperado. `sync_produtos_shopee` `BLOCKED` (gap conhecido de `LOCAL_PG_URL`) e `monitor_bug8` `SKIPPED`, ambos esperados.

**Achado**: mesmo assim, `STATUS GERAL: FAILED` — causado pelo próprio step `health_check`, que retornou exit 1 porque `ok_critical=false`. Causa raiz: a entrada de **execução** `shopee_product_monthly` em `EXPECTED_SOURCES` (rastreio de quando `sync_produtos_shopee` rodou com sucesso) continuava `critical=True` por default — o Gate B1 só tinha marcado `critical=False` nas entradas de **frescor de dado** (`DATA_FRESHNESS`) equivalentes a Shopee, não nessa entrada de execução. Corrigido no Gate B4, abaixo.

### Gate B4 (2026-07-15) — `shopee_product_monthly` (execução) também não-crítico em `health_check.py`

Mudança mínima: `ExpectedSource("shopee_product_monthly", "daily", 48, critical=False)` em `pipelines/ops/health_check.py::EXPECTED_SOURCES` (era `critical=True` por default). Nenhuma outra fonte de execução mudou; `bug8_invariants` continua sempre crítico (não tem — e nunca teve — conceito de não-crítico). Efeito: um Shopee produtos perpetuamente `BLOCKED`/sem execução recente (gap de `LOCAL_PG_URL`) reprova `ok` (visibilidade completa) mas não `ok_critical` — `health_check` (e, por consequência, `full_daily`) volta a terminar `OK`/exit 0 quando só esse gap conhecido está presente, mesmo com ML/TikTok/regional saudáveis. Fecha o achado do Gate B3.

### Gate B5 e B5.2 (2026-07-15) — duas rodadas manuais observadas pós-Gate B4

Duas execuções reais e independentes de `full_daily`, no mesmo dia, para confirmar estabilidade: ML/TikTok/Shopee daily `SUCCESS`; `gold_regional_incremental`/`sync_region_if_needed` `SUCCESS`/`NO_OP` nas duas vezes (Data Mart e Neon já em paridade, 33.896 linhas); produtos ML/TikTok `SUCCESS`; `sync_produtos_shopee` `BLOCKED` (gap conhecido) e `monitor_bug8` `SKIPPED`, ambos esperados. **`STATUS GERAL: DEGRADED`, exit code 0 nas duas rodadas** — confirma que o fix do Gate B4 funciona ponta a ponta, de verdade, não só nos testes unitários. `executive-summary` (produção) refletiu dado ao vivo nas duas vezes, sem risco regional, só o `stale_data` conhecido de Shopee.

### Gate B6.1 (2026-07-15) — revisão final para ativação do Task Scheduler (somente leitura, não ativado)

Auditoria completa, sem nenhuma escrita/execução: task `mktplace_full_daily` instalada e `Disabled`, nunca disparou (`LastTaskResult=267011`/"never ran"). XML instalado bate 100% com `schedule_plan.py` (Action, `MultipleInstancesPolicy=IgnoreNew`, `StartWhenAvailable=true`, `ExecutionTimeLimit=PT2H40M`, `Enabled=false`, trigger 06:00 diário). `run_task.ps1`→`run_with_lock.ps1` confirmados corretos (lock atômico, `WorkingDirectory`, logs separados stdout/stderr, timeout externo 9000s > orçamento interno 7200s).

**Bloqueio real encontrado**: `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY` (consentimento do sync regional) só existia como variável de ambiente definida manualmente por sessão (Gates B2–B5.2). O Task Scheduler dispara um processo novo, sem essa sessão — sem um mecanismo persistente, `check_sync_region_consent()` sempre bloquearia, e como `sync_region_if_needed` é `critical=True`, `full_daily` reportaria `FAILED` todo dia assim que ativado. Corrigido no Gate B6.1b, abaixo. Achados secundários não-bloqueantes: comentário desatualizado em `run_task.ps1` (ainda cita 6780s) e a cadeia real `run_task.ps1`→`run_with_lock.ps1` nunca foi exercida ponta a ponta com `full_daily` de verdade (só via Pester sintético + `orchestrate.py` chamado direto nos gates manuais).

### Gate B6.1b (2026-07-15) — consentimento persistente para o sync regional agendado

Fecha o bloqueio do Gate B6.1 sem rebaixar `sync_region_if_needed` para não-crítico (decisão explícita: o regional não tem gap manual conhecido aceito, diferente de Shopee).

**Novo módulo `pipelines/ops/region_sync_consent.py`** — mesmo padrão de `.env.gold-write.local` (`pipelines/ingestion/gold_regional/write_conn.py`): arquivo dedicado `.env.region-sync.local` na raiz do repo, fora do `.env` principal, coberto pela regra genérica `.env.*` do `.gitignore` (não precisou de regra nova), nunca commitado. Contém **somente**:

```
I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1
```

Isso **não é uma credencial** (nenhum host/usuário/senha aqui) — é um consentimento explícito e persistente de que a escrita automatizada em `marts.fact_marketplace_region_daily` foi autorizada previamente por um humano, para a execução agendada não depender de ninguém logado setando a variável manualmente. `ensure_region_sync_consent()`: variável de ambiente já definida no processo sempre vence (arquivo nem é lido); senão, tenta o arquivo — valor `"1"` seta em `os.environ` (só em memória, deste processo, nunca persiste em `.env`) e retorna `True`; arquivo ausente/valor inválido retorna `False` sem tocar em nada. Chaves extras no arquivo são ignoradas silenciosamente (diferente de `.env.gold-write.local`, que exige exatamente as 2 chaves esperadas por ser um secret de conexão real — aqui não há DSN, só um booleano). **Nunca cria o arquivo automaticamente; nunca imprime o conteúdo, no máximo o nome do arquivo.**

`check_sync_region_consent()` (`pipelines/ops/preflight.py`) passou a chamar `ensure_region_sync_consent()` em vez de checar `os.environ` diretamente — mesmo comportamento de antes quando a env var já está setada, mais o caminho novo via arquivo. `sync_region_if_needed.py::main()` também chama `ensure_region_sync_consent()` antes de `run()`, para funcionar mesmo invocado standalone (sem o preflight do orquestrador já ter resolvido o consentimento antes). O gate **original** de `pipelines/sync_region_daily.py::run_sync()` (flag `--sync` + `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1`) não foi tocado — continua a única barreira real antes de qualquer escrita.

**Como a task agendada encontra o consentimento**: quando `orchestrate.py` roda o preflight de `sync_region_daily` (antes de invocar o step `sync_region_if_needed` como subprocesso), `check_sync_region_consent()` chama `ensure_region_sync_consent()`, que — se o arquivo `.env.region-sync.local` existir e for válido — seta a variável no `os.environ` do próprio processo `orchestrate.py`. Como `subprocess.run()` (em `_default_executor`) herda o ambiente do processo pai por padrão, o subprocesso `sync_region_if_needed` spawnado logo em seguida já nasce com a variável presente — e, como camada extra (defesa em profundidade, cobre também invocação standalone), `sync_region_if_needed.py::main()` tenta o mesmo carregamento de novo, independentemente.

**Pré-condição para ativação (Gate B6.2)**: o arquivo `.env.region-sync.local` precisa existir na raiz do repo, com exatamente `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1`, **antes** de habilitar a task — este gate deliberadamente **não criou** esse arquivo (só implementou o mecanismo de leitura), por instrução explícita de não criar secret/consentimento real sem autorização separada. **Scheduler continua Disabled até o Gate B6.2** (que deve, entre outras coisas, confirmar a criação desse arquivo pelo operador antes de habilitar `Enabled=true`).

### Gate B6.1c (2026-07-16) — validação real da cadeia `run_task.ps1 → run_with_lock.ps1`, com `.env.region-sync.local` já criado

Depois do operador criar `.env.region-sync.local` de verdade (gitignored, confirmado), executou-se pela primeira vez a cadeia real `powershell -File scripts\run_task.ps1 -TaskKey full_daily` (nunca antes exercida ponta a ponta — os Gates B3/B5/B5.2 sempre chamaram `orchestrate.py` direto).

**Achado crítico**: `run_with_lock.ps1` reportou `STATUS=FAILED EXITCODE=2` em ~2s — nenhum step chegou a rodar. `stderr`: `orchestrate.py: error: the following arguments are required: --pipeline`. Diagnosticado (somente leitura, sem re-executar o pipeline real) até a causa raiz: `run_with_lock.ps1` usa atributos `[Parameter(...)]`, o que faz o PowerShell tratá-lo como um "advanced script" com **todos os CommonParameters habilitados implicitamente** (`Verbose`, `Debug`, `PipelineVariable`, `WhatIf`, `Confirm` etc). `--pipeline` é um prefixo AMBÍGUO-LIVRE de `-PipelineVariable` — quando os argumentos chegavam como texto bruto de linha de comando (via `run_task.ps1` spawnando um `powershell -File run_with_lock.ps1 ...` **aninhado**, um novo processo), o parameter binder do PowerShell silenciosamente consumia `--pipeline` **e** o valor seguinte (`full_daily`) como `-PipelineVariable`, nunca deixando-os chegar em `$Cmd`/`Start-Process`. Se a task fosse ativada com esse bug, **toda execução agendada falharia assim, sem nunca rodar um único step**. Corrigido no Gate B6.1d, abaixo.

### Gate B6.1d (2026-07-16) — corrige a perda de `--pipeline` na cadeia `run_task.ps1 → run_with_lock.ps1`

`scripts/run_task.ps1` ganhou `Invoke-ResolvedTask`, que faz **dot-source em processo** de `run_with_lock.ps1` (nunca mais um `powershell -File run_with_lock.ps1 ...` aninhado), com o comando real (`PythonExe` + `ModuleArgs`, incluindo `--pipeline full_daily`) passado como um **array já construído**, ligado explicitamente ao parâmetro `-Cmd` numa única expressão PowerShell avaliada no mesmo processo — nunca re-tokenizado como texto de linha de comando atravessando um novo processo. Isso elimina a classe inteira de colisão com CommonParameters (não só para `--pipeline`, para qualquer futuro flag que coincida com `Verbose`/`Debug`/`WhatIf`/etc), sem exigir nenhuma mudança em `run_with_lock.ps1` (seu uso direto via CLI, para outras `TaskKey`s, continua idêntico e coberto pela suíte Pester existente). `run_with_lock.ps1` continua terminando com `exit $exitCode`, que agora encerra o próprio processo de `run_task.ps1` (dot-sourced) com o código correto — a linha `exit $LASTEXITCODE` no fim de `run_task.ps1` fica como rede de segurança, nunca alcançada no caminho real.

Confirmado empiricamente (arg-dumper temporário no lugar do Python real, nenhum pipeline/banco tocado): `--pipeline full_daily` sobrevive intacto; `-WorkingDirectory` continua aplicado corretamente (inclusive com path contendo espaço); lock/timeout continuam funcionando (`BLOCKED`/124 no timeout real); `run_task.ps1` nunca passa `-SimulateStopProcessFailure` (flag só de teste de `run_with_lock.ps1`).

**Limitação separada e pré-existente, encontrada mas fora do escopo deste gate**: um elemento de `-ModuleArgs` que seja, ele mesmo, um **path com espaço** (ex.: `-File "<dir com espaço>\script.ps1"` como um dos argumentos do comando) quebra em `Start-Process -ArgumentList` (Windows PowerShell 5.1 não coloca aspas automaticamente em elementos do array com espaço) — confirmado que isso é **anterior** a este gate (reproduz identico chamando `run_with_lock.ps1` direto, sem `Invoke-ResolvedTask`) e **não afeta** o `full_daily` real (nenhum path/arg da invocação real tem espaço: `apps\api\.venv\Scripts\python.exe`, `-m pipelines.ops.orchestrate --pipeline full_daily`). Documentado como risco conhecido, não corrigido aqui — corrigir exigiria alterar `Start-Process` dentro de `run_with_lock.ps1`, uma peça de infraestrutura compartilhada por outras `TaskKey`s, fora do escopo específico deste gate (a colisão `--pipeline`/`PipelineVariable`).

**Achado incidental, corrigido**: rodar a suíte pytest completa revelou que 4 testes de `test_ops_preflight.py` (do Gate B2, anteriores ao B6.1b) assumiam implicitamente que `.env.region-sync.local` NUNCA existiria de verdade no disco — premissa que deixou de ser válida assim que o operador criou o arquivo real para este gate. Adicionado `monkeypatch.setattr(preflight.region_sync_consent, "DEFAULT_REGION_SYNC_CONSENT_PATH", tmp_path / "nao-existe.local")` a esses 4 testes (mesmo padrão já usado pelos testes vizinhos, corretamente isolados, do Gate B6.1b) — correção de isolamento de teste, nenhuma mudança de comportamento de produção.

**Não executado neste gate** (por instrução explícita): nenhum `full_daily` real, nenhum sync/incremental real, Task Scheduler continua Disabled, sem commit/push.

### Testes desta fase

- `pipelines/tests/test_ops_preflight.py` (64 testes, +17 no Gate B2, +5 no Gate B6.1b) — checks individuais, `LOCAL_PG_URL` sem fallback e com allowlist de host (bloqueia sem tentar conectar), sessão read-only, checks de arquivo Shopee **separados por padrão real** (orders/stats/ads) contra a lista oficial de marcas do conector, bloqueio da fonte inteira quando uma marca oficial falta, `SHOPEE_DATA_PATH` nunca aparece na mensagem, guardas estruturais, **`check_gold_regional_write`** (bloqueia sem secret/guardrails/preflight de escrita, passa com fakes, nunca expõe secret/URL, nunca abre conexão de escrita neste módulo), **`check_sync_region_consent`** (bloqueia sem `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1` ou com valor≠`1`, passa com `1`), `SOURCE_CHECKS`/`run_preflight` das duas novas fontes (`gold_regional_incremental`, `sync_region_daily`); **Gate B6.1b**: `check_sync_region_consent` também passa com consentimento persistente via arquivo (`.env.region-sync.local`), bloqueia com arquivo ausente/inválido, variável de ambiente tem prioridade sobre o arquivo, nunca expõe o conteúdo do arquivo (só o nome).
- `pipelines/tests/test_ops_region_sync_consent.py` (15 testes, novo no Gate B6.1b) — env var já definida vence sem ler o arquivo; sem env e sem arquivo retorna `False`; arquivo com valor `1` seta em `os.environ`; valores inválidos (`0`/`true`/`yes`/vazio) não setam e retornam `False`; arquivo vazio ou sem a chave retorna `False`; chaves extras no arquivo são ignoradas sem falhar; `env_path=None` resolve `DEFAULT_REGION_SYNC_CONSENT_PATH` no momento da chamada (permite monkeypatch sem passar `env_path`); nunca cria o arquivo; guardas estruturais (nunca escreve em disco, nunca imprime nada).
- `pipelines/tests/test_ops_health_check.py` (52 testes, +9 no Gate B1, +8 no Gate B4) — todas as fontes esperadas aparecem mesmo sem histórico, `execution_stale`/`last_run_failed` **separados** (falha na última execução sempre reprova, mesmo com sucesso recente dentro do threshold), data no futuro em fonte diária e manual/mensal sempre erro de qualidade, regressões de `build_report` para os dois casos, JSON com `reason`, credenciais nunca aparecem, **`ok_critical` separado de `ok`** (Shopee stale nunca reprova `ok_critical` sozinho; ML/TikTok stale reprova os dois; Bug 8 sempre reprova os dois; JSON expõe `critical` por fonte/entrada); **Gate B4**: `shopee_product_monthly` (execução) isolado como `critical=False` — stale só nele (ou combinado com o gap de dado de Shopee) reprova `ok` mas nunca `ok_critical`/exit code; ML/TikTok em execução e Bug 8 continuam reprovando `ok_critical` normalmente; regressão ponta-a-ponta do achado do Gate B3 via `main()`.
- `pipelines/tests/test_ops_orchestrate.py` (51 testes, +11 no Gate B2) — preflight bloqueado impede o comando real, exit code propagado, **timeout individual por step vira `FAILED` e não trava fontes independentes seguintes** (ML timeout → TikTok/Shopee normais), orçamento somado dos timeouts (7200s desde o Gate B2) com margem sobre o timeout externo (9000s), `depends_on`/`always_run`, **health check comprovadamente o último `call` em 5 cenários mistos de sucesso/falha/timeout/bloqueio**, pipelines antigos de 2 tarefas confirmados como removidos, **`compute_overall_status` (Step.critical)**: falha/bloqueio crítico → FAILED; `sync_produtos_shopee` (não-crítico) falho/bloqueado → DEGRADED; SKIPPED por dependência não-crítica nunca conta como falha; crítico tem prioridade sobre não-crítico quando ambos falham na mesma execução; **Gate B2**: `gold_regional_incremental`/`sync_region_if_needed` na ordem certa, ambos `critical=True`, `sync_region_if_needed` depende de `gold_regional_incremental`, falha/bloqueio de qualquer um dos dois vira `FAILED` (nunca `DEGRADED`, mesmo simultâneo com o gap conhecido de Shopee), `sync_region_if_needed` pulado (`SKIPPED`) se `gold_regional_incremental` falhar.
- `pipelines/tests/test_ops_sync_region_if_needed.py` (18 testes, novo no Gate B2, +4 no Gate B6.1b) — `no_op` nunca chama sync quando `needs_sync=False`; `needs_sync=True` chama sync exatamente 1 vez com `sync=True`; falha de diagnose aborta antes de qualquer tentativa de sync; falha de sync propaga como `SyncIfNeededError` sanitizado; sem retry automático em nenhum dos dois caminhos; erros nunca vazam credenciais; `main()` com exit 0 (no-op e sync) e exit 1 (erro) sem propagar exceção nativa; guardas estruturais (não reimplementa a checagem de consentimento em código, não usa `subprocess`); **Gate B6.1b**: `main()` chama `ensure_region_sync_consent()` antes de `run()`; `needs_sync=True` com consentimento vindo do arquivo chama sync exatamente 1 vez (e a env var já está presente no processo nesse momento); `needs_sync=False` nunca chama sync mesmo com consentimento disponível; sem consentimento (nem env nem arquivo), o gate original de `sync_region_daily.run_sync` continua recusando antes de qualquer escrita.
- `pipelines/tests/test_ops_schedule_plan.py` (23 testes, +1 assert no Gate B2) — 1 tarefa (não 2, não 8), XML bem formado com `MultipleInstancesPolicy=IgnoreNew`/`StartWhenAvailable=true`/`ExecutionTimeLimit=PT2H40M` (9600s, maior que os 9000s do lock, com margem para a limpeza pós-timeout) **e `Settings/Enabled=false` sempre** (tarefa nasce desativada; `render_task_scheduler_xml()` não aceita nenhum parâmetro para habilitar), horário 06:00 sinalizado como hipótese não confirmada, aspas dobradas validadas no parser real do PowerShell, `/f` nunca por padrão, nenhuma capacidade de execução importada, **orçamento interno (7200s desde o Gate B2) travado em sincronia com `orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS`, com margem sobre os 9000s do lock**.
- `apps/api/etl/tests/test_load_shopee_products_local_pg_guard.py` (10 testes) — `LOCAL_PG_URL` sem fallback, host remoto bloqueado, hosts locais aceitos, erros nunca expõem credenciais, imports de função pura nunca exigem `LOCAL_PG_URL`.
- `scripts/run_with_lock.tests.ps1` (15 testes Pester) — exit code propagado, `WorkingDirectory` correto por padrão e a partir de `C:\Windows\System32`, lock com dono vivo bloqueia mesmo antigo, lock com dono morto recupera, duas tentativas simultâneas via `Start-Job` (exatamente uma vence), credencial em argumento recusada, timeout mata o processo e retorna 124, **aguarda confirmação real de término do processo morto antes de liberar o lock** (verificado via `Get-CimInstance Win32_Process`, não só inferido), **`LockName` com `..`/`/` rejeitado antes de tocar disco**, stdout/stderr separados.
- `scripts/run_task.tests.ps1` (13 testes Pester, +6 no Gate B6.1d) — `Resolve-TaskInvocation` resolve a `TaskKey` única (`full_daily`) com lock/timeout/módulo corretos, timeout externo (9000s) maior que o orçamento interno (7200s, corrigido de uma referência desatualizada a 6780s), `$null` para chave desconhecida, dot-source não executa nada; **Gate B6.1d**: `Invoke-ResolvedTask` reproduz o bug real e confirma o fix (`--pipeline full_daily` sobrevive intacto via arg-dumper temporário), `-WorkingDirectory` com path contendo espaço continua funcionando, `-WorkingDirectory` simples continua aplicado, lock/timeout continuam funcionando (`BLOCKED`/124 real), `run_task.ps1` nunca passa `-SimulateStopProcessFailure`.
- Total: 1.427 testes pytest (`pipelines/tests` + `apps/api/etl/tests` + `apps/api/tests`) + 30 testes Pester em `scripts/run_with_lock.tests.ps1` (17, inalterado) + `scripts/run_task.tests.ps1` (13, +6 no Gate B6.1d). Todos os testes pytest e Pester relevantes a este gate foram executados nesta revisão (nenhum banco real tocado, nenhum `full_daily` real).

---

## Histórico de decisões

| Data | Decisão |
|---|---|
| 2026-06-26 | Backfill inicial: Shopee=5.228, ML=1.326, TikTok=170.806 linhas |
| 2026-06-26 | `estimated_margin` alterado de `NUMERIC(8,4)` para `NUMERIC(18,2)` (valores até 811k) |
| 2026-06-26 | `problem_rate` definido como média ponderada por orders do campo diário da fonte |
| 2026-06-26 | ML dedup: 1.418 raw → 1.326 pós-dedup (92 pares duplicados na fonte, mantém maior `gross_revenue`) |
| 2026-07-03 | Fase 3A (1ª versão): automação preparada (preflight, health check, `run_with_lock.ps1` com timeout/stale-lock/logs separados, agenda proposta de 8 tarefas) — **não ativada**. Reprovada em revisão. |
| 2026-07-03 | Fase 3A (revisão 1): preflight amarrado à execução real via `orchestrate.py`; agenda reduzida a 2 tarefas orquestradas; lock atômico por PID (sem stale por idade); `WorkingDirectory` garantido inclusive a partir de `C:\Windows\System32`; aspas do `schtasks /tr` corrigidas via `run_task.ps1`; health check com fontes esperadas explícitas e frescor de dado avaliado contra threshold; `preflight.py` e `apps/api/etl/load_shopee_products.py` exigem `LOCAL_PG_URL` sem fallback, com allowlist de host — **ainda não ativada**. Reprovada de novo em revisão. |
| 2026-07-03 | Fase 3A (revisão 2): agenda fundida em **1 tarefa** (`full_daily`) — elimina a corrida entre 2 tarefas em horários separados; timeout **individual por step** em `orchestrate.py` (uma fonte travada não consome o timeout global nem trava fontes independentes); `health_check.py` com `execution_stale`/`last_run_failed` separados (falha na última execução sempre reprova) e data-no-futuro sempre erro de qualidade; `preflight.py` com checks de arquivo Shopee separados por padrão real (orders/stats/ads) contra a lista oficial de marcas do conector, bloqueando a fonte inteira se uma marca oficial faltar, e `check_local_pg` com allowlist de host; `run_with_lock.ps1` aguarda confirmação real de término após `Stop-Process` antes de liberar o lock, e valida `LockName` contra path traversal; `schedule_plan.py` gera a definição XML do Task Scheduler (`MultipleInstancesPolicy=IgnoreNew`, `StartWhenAvailable=true`, `ExecutionTimeLimit=PT2H30M`) como texto, e o horário 06:00 é sinalizado explicitamente como hipótese não confirmada — **ainda não ativada**, aguardando nova revisão |
| 2026-07-15 | Gate B1 (automação recorrente, parte 1): `orchestrate.py::Step` ganha `critical: bool` (default True); `sync_produtos_shopee` marcado `critical=False` (gap manual conhecido de `LOCAL_PG_URL`); status geral do pipeline vira OK/DEGRADED/FAILED (`compute_overall_status`) — só falha/bloqueio **crítico** gera exit 1, não-crítico só degrada (exit 0); `health_check.py` ganha `ok_critical` separado de `ok`, com `fact_marketplace_daily_performance[shopee]` e `fact_shopee_product_monthly[ref_month]` marcadas `critical=False` — Shopee manual defasado para de derrubar o health check (e, por consequência, o pipeline inteiro) sozinho. Scheduler segue **desativado** (Fase 3B); Gold regional **não** entrou no `full_daily` neste gate (fica para o Gate B2). |
| 2026-07-15 | Gate B2 (automação recorrente, parte 2): `gold_regional_incremental` + `sync_region_if_needed` (novo wrapper `pipelines/ops/sync_region_if_needed.py`, diagnose-then-maybe-sync, sem retry) integrados a `full_daily`, na ordem depois de `daily_shopee_ads`/antes de `sync_produtos_ml`, **ambos `critical=True`** (diferente de `sync_produtos_shopee`: sem gap manual conhecido aceito aqui) — uma falha real reprova o pipeline (FAILED), não só degrada. `sync_region_if_needed` evita `TRUNCATE`+`INSERT`/backup diário desnecessário quando Data Mart e Neon já estão em paridade. Novo preflight (`check_gold_regional_write`, `check_sync_region_consent`) bloqueia com `BLOCKED` explícito se o secret de escrita ou o consentimento de sync não estiverem presentes, sem nunca imprimir credencial. Orçamento somado dos timeouts sobe de 6780s para 7200s, ainda com margem folgada sobre os 9000s do lock externo. Scheduler segue **desativado** (Fase 3B, fica para B3/B4). Nenhuma execução real de pipeline/DB/scheduler neste gate. |
| 2026-07-15 | Gate B3 (rodada manual observada de `full_daily`): execução real única — ML/TikTok/Shopee daily/regional (`gold_regional_incremental`+`sync_region_if_needed`, ambos `NO_OP`, Data Mart e Neon já em paridade)/produtos ML/TikTok todos `SUCCESS`; `sync_produtos_shopee` `BLOCKED` (gap conhecido) e `monitor_bug8` `SKIPPED`, ambos esperados. **Achado**: pipeline terminou `FAILED` mesmo assim, por causa do próprio step `health_check` (exit 1, `ok_critical=false`) — a entrada de execução `shopee_product_monthly` em `EXPECTED_SOURCES` não tinha sido marcada `critical=False` no Gate B1 (só as entradas de frescor de dado foram). Corrigido no Gate B4. |
| 2026-07-15 | Gate B4 (fecha o achado do Gate B3): `health_check.py::EXPECTED_SOURCES` — `shopee_product_monthly` marcado `critical=False`. Um Shopee produtos perpetuamente `BLOCKED`/sem execução recente (gap de `LOCAL_PG_URL`) volta a reprovar só `ok` (visibilidade), nunca `ok_critical`/exit code — `full_daily` deixa de terminar `FAILED` todo dia por esse gap já aceito. ML/TikTok (execução e dado), regional e Bug 8 continuam 100% críticos. Nenhuma execução real de pipeline/DB/scheduler neste gate. |
| 2026-07-15 | Gates B5 e B5.2: duas rodadas manuais reais de `full_daily`, mesmo dia — `STATUS GERAL: DEGRADED`, exit code 0 nas duas, confirmando o fix do Gate B4 funcionando ponta a ponta (não só nos testes). Regional (`gold_regional_incremental`+`sync_region_if_needed`) `NO_OP` nas duas vezes (Data Mart/Neon já em paridade). `executive-summary` refletiu dado ao vivo, sem risco regional. |
| 2026-07-15 | Gate B6.1 (revisão somente leitura, nada ativado): task `mktplace_full_daily` confirmada instalada, `Disabled`, nunca disparou, e 100% alinhada com `schedule_plan.py`. **Achado**: consentimento regional (`I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY`) só existe como variável de sessão manual — Task Scheduler não teria como defini-la, e `sync_region_if_needed` (crítico) bloquearia `full_daily` todo dia se ativado agora. Bloqueio real para a Fase 3B, fechado no Gate B6.1b. |
| 2026-07-15 | Gate B6.1b (fecha o achado do Gate B6.1): novo módulo `pipelines/ops/region_sync_consent.py` — consentimento persistente e gitignored via `.env.region-sync.local` (mesmo padrão de `.env.gold-write.local`), consultado por `check_sync_region_consent()` e por `sync_region_if_needed.py::main()` quando a variável de ambiente ainda não está definida no processo. Nunca cria o arquivo automaticamente, nunca imprime seu conteúdo, nunca persiste em `.env`. O gate original de `sync_region_daily.run_sync()` não foi alterado. `sync_region_if_needed` continua `critical=True` (decisão preservada, não rebaixada). Scheduler segue **Disabled** até o Gate B6.2 — que deve confirmar a criação real do arquivo pelo operador antes de habilitar a task. Nenhuma execução real de pipeline/DB/scheduler neste gate. |
| 2026-07-16 | Gate B6.1c (operador cria `.env.region-sync.local` de verdade; primeira execução real da cadeia `run_task.ps1 → run_with_lock.ps1`): **achado crítico** — `full_daily` falhava com `orchestrate.py: error: the following arguments are required: --pipeline`, sem rodar nenhum step. Causa raiz: `--pipeline` colide com o CommonParameter `-PipelineVariable` do PowerShell (`run_with_lock.ps1` é um "advanced script" por usar atributos `[Parameter(...)]`) e é silenciosamente consumido junto com `full_daily` quando os argumentos atravessam um `-File` aninhado. Se ativada hoje, a task falharia assim todo dia. Corrigido no Gate B6.1d. |
| 2026-07-16 | Gate B6.1d (fecha o achado do Gate B6.1c): `scripts/run_task.ps1` ganha `Invoke-ResolvedTask` — dot-source em processo de `run_with_lock.ps1` (nunca mais um `-File` aninhado), comando real passado como array já construído ligado a `-Cmd` numa única expressão PowerShell, eliminando a colisão com CommonParameters. `run_with_lock.ps1` não foi alterado (uso direto via CLI para outras `TaskKey`s continua idêntico). Confirmado com arg-dumper temporário: `--pipeline full_daily` sobrevive, `-WorkingDirectory`/lock/timeout continuam corretos. Limitação separada e pré-existente documentada (path com espaço dentro de um argumento do comando, via `Start-Process -ArgumentList`) — não afeta o `full_daily` real, não corrigida neste gate. Corrigidos incidentalmente 4 testes de `test_ops_preflight.py` que assumiam `.env.region-sync.local` nunca existir de verdade. Scheduler segue **Disabled**; próximo passo é repetir o Gate B6.1c para confirmar a correção ponta a ponta. Nenhuma execução real de `full_daily`/banco/scheduler/commit/deploy neste gate. |
