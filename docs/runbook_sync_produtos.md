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

### Conflito de recovery na leitura RDS do ML (Gate C2.4, 2026-07-17)
```
psycopg2 (Operational/DatabaseError): canceling statement due to conflict with recovery
DETAIL:  User query might have needed to see row versions that must be removed.
```
**Causa**: `gold.ml_produto_ranking` é uma VIEW cara (joins/agregações sobre `ml_order_line_items`/`ml_orders`/`ml_ads_items`), lida contra um **read replica** do RDS com `hot_standby_feedback=off` — o Postgres pode cancelar a query quando o replay do WAL no replica precisa remover versões de linha que a query ainda usa. Achado nos Gates C2.2/C2.3: raro (1 falha em 9+ execuções auditadas), mas a condição estrutural pode recorrer a qualquer momento; não é um bug de dado, é comportamento documentado do Postgres para essa classe de conflito.

**Mitigação (Gate C2.4)**: `sync_ml()` agora tem retry **único e estrito**, restrito à leitura da fonte RDS (nunca à escrita no Neon nem à auditoria): se a leitura falhar com essa mensagem específica, espera 8s e tenta mais uma vez (nova conexão); qualquer outro tipo de erro (validação, conexão genérica) sobe imediatamente, sem retry. Se as duas tentativas falharem, o erro original é propagado e nenhuma escrita é feita no Neon (a conexão de destino só abre depois de uma leitura bem-sucedida) — comportamento idêntico ao de antes do Gate C2.4 nesse caso, só que agora dando uma segunda chance à leitura antes de desistir.

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

### Gate C1 (2026-07-16) — separa Shopee (manual) de `full_daily` (recorrente automático)

**Retry do Gate B6.1c encontrou um achado operacional, não um bug**: com a cadeia `run_task.ps1 → run_with_lock.ps1 → orchestrate.py` já corrigida (Gate B6.1d), uma execução real de `full_daily` rodou por ~18min e falhou porque `daily_shopee_orders` estourou seu timeout individual de 900s processando arquivos Shopee grandes (marca "kokeshi", 217.580 linhas de SKU). A causa raiz não era um timeout pequeno demais — era rodar ingestão Shopee (cadência **manual**, o dado só muda quando o usuário faz upload de exports novos) todo dia dentro de um pipeline com cadência **diária automática**. Aumentar o timeout só adiaria o próximo estouro; a correção certa é de desenho.

**Redesenho implementado**: `pipelines/ops/orchestrate.py::PIPELINES` virou **dois pipelines independentes**, cada um com seu próprio lock:

- **`full_daily`** (automático, único candidato ao Task Scheduler): `daily_ml` → `daily_tiktok` → `gold_regional_incremental` → `sync_region_if_needed` → `sync_produtos_ml` → `sync_produtos_tiktok` → `health_check`. Orçamento de timeout caiu de **7200s para 3600s** (~1h).
- **`shopee_manual_refresh`** (novo, MANUAL, nunca agendado): `daily_shopee_orders` → `daily_shopee_stats` → `daily_shopee_ads` → `sync_produtos_shopee` → `monitor_bug8` → `health_check`. Orçamento: 3780s (~1h03). Dentro deste pipeline, os 3 steps Shopee diários são **críticos** (é uma execução manual e deliberada — uma falha real deve aparecer como FAILED, não um DEGRADED silencioso); `sync_produtos_shopee` continua `critical=False` (gap conhecido de `LOCAL_PG_URL`, ortogonal a este redesenho); `monitor_bug8` continua dependendo de `sync_produtos_shopee == SUCCESS`.

**`pipelines/ops/health_check.py`**: as entradas de execução `shopee_daily`/`shopee-stats_daily`/`shopee-ads_daily` em `EXPECTED_SOURCES` viraram `critical=False` — mesmo motivo e mesmo padrão do Gate B4 para `shopee_product_monthly`: como esses 3 steps saem de `full_daily` (roda todo dia) e passam a viver em `shopee_manual_refresh` (roda só sob demanda), sem essa marcação `ok_critical` voltaria a `false` assim que passassem de 48h sem execução — recriando o mesmo alarme-fadiga que os Gates B3/B4 já corrigiram uma vez, agora por outra porta. ML/TikTok (execução e dado), produtos ML/TikTok e Bug 8 continuam 100% críticos, sem mudança.

**`scripts/run_task.ps1`**: nova `TaskKey "shopee_manual_refresh"` em `Get-TaskDefinitions` (mesmo `TimeoutSeconds=9000` de `full_daily`, módulo `pipelines.ops.orchestrate --pipeline shopee_manual_refresh`) — permite ao operador rodar `powershell -File scripts\run_task.ps1 -TaskKey shopee_manual_refresh` reaproveitando o mesmo wrapper de lock/timeout/log da `full_daily`, com lock **separado** (`shopee_manual_refresh.lock`, nunca conflita com `full_daily.lock`). **Nenhuma task nova foi criada no Task Scheduler** — isso continua exigindo um passo manual e separado (fora do escopo deste gate).

**`pipelines/ops/schedule_plan.py`**: comentários de orçamento atualizados (7200s→3600s); `EXTERNAL_LOCK_TIMEOUT_SECONDS`/`TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS` (9000s/9600s) **mantidos inalterados** (margem só aumentou, de ~25% para ~150% sobre o orçamento interno de `full_daily`) — sem motivo forte para reduzir agora. `PROPOSED_SCHEDULE` continua com uma única entrada (`mktplace_full_daily`) — **nenhuma task Shopee é proposta/agendada**, por desenho.

**Resumo Executivo**: comportamento inalterado — o risco `stale_data` de Shopee já era computado direto do `MAX(date)` em `fact_marketplace_daily_performance`, independente de `full_daily` rodar Shopee ou não. Continua aparecendo como risco conhecido/manual sempre que Shopee estiver sem dado recente, agora simplesmente refletindo com mais precisão a realidade operacional (recorrente só para ML/TikTok/regional).

**Scheduler continua Disabled** — este gate não ativa nada; fica para o Gate C3, depois do Gate C2 (rodar `full_daily` sem Shopee via `run_task.ps1`, observado estável) e de execuções manuais de `shopee_manual_refresh` quando houver carga Shopee nova.

**Não executado neste gate** (por instrução explícita): nenhum `full_daily`/`shopee_manual_refresh` real, nenhum sync/incremental real, Task Scheduler não tocado, sem commit/push.

### Retomada operacional (2026-07-23) — handoff Shopee validado

A pausa aberta em 17/07 foi encerrada. O refresh seguro da Gold Shopee por
`file_id`/janela foi implementado, o primeiro lote completo de junho passou
por Raw, Silver e Gold para as cinco marcas, e backup/receipt/reconciliação
foram validados em execução real. Shopee continua manual por desenho e não
volta ao `full_daily`.

**Ciclo encerrado em 24/07/2026.** A ativação do scheduler seguiu esta
ordem, sem nenhum subgate arquitetural novo:

1. **Concluído em 23/07:** Gold regional de junho sincronizada com o Neon;
   backup preservado e paridade confirmada em 37.282 linhas e
   R$ 57.739.424,80;
2. **Concluído em 23/07 (ver Gate C2 abaixo):** rodada manual observada do
   `full_daily` via `run_task.ps1 -TaskKey full_daily` — sete steps
   `SUCCESS`, `STATUS GERAL: OK`, `ok_critical=true`, locks liberados,
   logs preservados, zero steps Shopee;
3. **Concluído em 23/07:** revisão pré-ativação (instalado × código
   versionado) sem incompatibilidade real — único ajuste apontado foi a
   falta de histórico de execução real às 06:00;
4. **Concluído em 23/07:** task `mktplace_full_daily` habilitada via
   `schtasks /Change /TN "\mktplace_full_daily" /ENABLE`, por autorização
   explícita, mantendo horário (06:00), conta e demais parâmetros
   inalterados;
5. **Concluído em 24/07 (ver Gate C3 abaixo):** primeira execução agendada
   real observada às 06:00 — concluiu sozinha, sem intervenção manual,
   `STATUS GERAL: OK`.

`shopee_manual_refresh` permanece sem task agendada. Qualquer dívida
cosmética ou melhoria não necessária a esses critérios vai para backlog.

### Gate C2 (2026-07-23) — rodada manual observada de `full_daily`

Execução real única, via `powershell -NoProfile -NonInteractive -File
scripts\run_task.ps1 -TaskKey full_daily`. Logs preservados em
`logs/full_daily_20260723_184604_stdout.log` e
`logs/full_daily_20260723_184604_stderr.log`.

**Resultado dos sete steps:**

| Step | Status |
|---|---|
| `daily_ml` | SUCCESS |
| `daily_tiktok` | SUCCESS |
| `gold_regional_incremental` | SUCCESS |
| `sync_region_if_needed` | SUCCESS (`NO_OP` — Data Mart e Neon já em paridade, 37.282 linhas) |
| `sync_produtos_ml` | SUCCESS |
| `sync_produtos_tiktok` | SUCCESS |
| `health_check` | SUCCESS |

`STATUS GERAL: OK`. `health_check` reportou `ok_critical=true` e
`ok=false` — o `ok=false` vem exclusivamente dos alertas não-críticos e
manuais já conhecidos de Shopee (`shopee_daily`/`shopee-ads_daily`/
`shopee_product_monthly` com execução defasada, gap aceito desde os Gates
B1/B4/C1), nunca reprovando `ok_critical` nem o `STATUS GERAL`. Confirmado
nos logs: nenhum `[RUN]` relacionado a Shopee em nenhum step — o pipeline
`full_daily` não contém Shopee desde o Gate C1, e este comportamento se
confirmou na execução real. Nenhum `FAILED`/`BLOCKED` funcional em nenhum
step.

**Timeout externo (124) do shell que acompanhava o processo**: a
ferramenta usada para disparar o comando parou de aguardar após ~14s e
reportou exit code 124, mas isso foi só o timeout de acompanhamento da
própria ferramenta — não o exit code do pipeline. O subprocesso Python
continuou normalmente em segundo plano, terminou sozinho (ver os sete
`SUCCESS` e `STATUS GERAL: OK` no stdout) e removeu `full_daily.lock`
como de costume. Não indica falha do pipeline nem exige nova execução.

**Dois avisos não-bloqueantes, classificados como dívida/monitoramento
(não corrigidos neste gate, por instrução explícita):**

1. `UnicodeEncodeError` (2 ocorrências no stderr): o logger, ao rodar sob
   o code page padrão do Windows (cp1252), falha ao tentar imprimir o
   caractere "→" nas mensagens de log de `pipelines/connectors/
   mercadolivre/connector.py` e `pipelines/connectors/tiktok/
   connector.py`. Os dois steps (`daily_ml`, `daily_tiktok`) continuaram e
   terminaram com sucesso — é só a formatação da linha de log que falha,
   nunca a execução. Dívida de encoding, não corrigida nesta rodada.
2. Aviso do TikTok (6.463 pedidos com `order_status` nulo ou fora da
   allowlist conhecida, não incluídos no GMV): comportamento já conhecido
   e as regras vigentes já foram reconciliadas no Gate R2 — não impediu o
   pipeline. Classificado como dívida/monitoramento, sem nova investigação
   nesta rodada.

**Estado do Task Scheduler `mktplace_full_daily`, confirmado nesta rodada
(não presumido)** — consultado via `schtasks /Query /TN
"\mktplace_full_daily" /V` e via `Get-ScheduledTask`: **Disabled**,
nunca disparou de fato (última execução real = nunca), horário 06:00
diário continua como hipótese não confirmada (mesma ressalva desde a
Fase 3A).

**Veredito deste gate**: GO apenas para **preparar/revisar** a ativação
do Scheduler (horário, configuração, estado) — não é autorização para
habilitar a task. Se o estado da task não pudesse ter sido confirmado
nesta consulta, isso seria o único bloqueio operacional a resolver antes
da ativação; como foi confirmado (`Disabled`, via duas ferramentas
distintas), não há bloqueio novo.

**Não executado neste gate** (por instrução explícita): nenhum novo
`full_daily`/`health_check`/`shopee_manual_refresh` real, nenhuma correção
de encoding, nenhuma alteração das regras TikTok, nenhuma conexão manual a
banco, nenhum backfill/sync separado/restore/deploy, Task Scheduler não
criado/alterado/habilitado/desabilitado, sem commit/push.

### Gate C3 (2026-07-24) — primeira execução agendada real do `full_daily`

Task `mktplace_full_daily` habilitada em 23/07
(`schtasks /Change /TN "\mktplace_full_daily" /ENABLE`, horário/conta/demais
parâmetros mantidos inalterados). Primeira execução real disparada pelo
próprio Task Scheduler, sem nenhuma intervenção manual: início às
06:00:01, `LastTaskResult=0`. Logs preservados em
`logs/full_daily_20260724_060002_stdout.log` e
`logs/full_daily_20260724_060002_stderr.log`.

**Resultado dos sete steps:**

| Step | Status |
|---|---|
| `daily_ml` | SUCCESS |
| `daily_tiktok` | SUCCESS |
| `gold_regional_incremental` | SUCCESS (30 linha(s) inserida(s) — marketplace `ml`) |
| `sync_region_if_needed` | SUCCESS (sync real desta vez, não `NO_OP`: 37.282 → 37.851 linhas, backup `marts.fact_marketplace_region_daily_backup_20260724_060206`) |
| `sync_produtos_ml` | SUCCESS |
| `sync_produtos_tiktok` | SUCCESS |
| `health_check` | SUCCESS |

`STATUS GERAL: OK`. `ok_critical=true`; `ok=false` só pelos mesmos alertas
não-críticos e manuais de Shopee já aceitos (execução defasada de
`shopee_daily`/`shopee-ads_daily`/`shopee_product_monthly`). Confirmado:
nenhum `[RUN]` Shopee em toda a execução, nenhum `FAILED`/`BLOCKED`.
`sync_region_if_needed` executou um sync real (em vez de `NO_OP`, como no
Gate C2) porque o `gold_regional_incremental` desta execução alterou o
estado de `gold.marketplace_region_daily` o suficiente para gerar
divergência real com o Neon — comportamento esperado e aceito
explicitamente pela autorização desta rodada (`NO_OP` ou sync real são
igualmente válidos, desde que o step termine `SUCCESS`); a mecânica exata
da carga incremental não foi investigada nesta observação, por estar fora
do escopo (somente leitura/monitoramento).

Dois avisos não-bloqueantes, mesma classificação do Gate C2 (dívida/
monitoramento, não corrigidos): `UnicodeEncodeError` do logger (cp1252
vs. "→", 2 ocorrências, mesmas duas linhas de `daily_ml`/`daily_tiktok`) e
o aviso do TikTok sobre pedidos com `order_status` nulo/fora da allowlist
(8.211 desta vez — comportamento já conhecido e reconciliado no Gate R2).

Lock `full_daily.lock` confirmado ausente após a execução (liberado
normalmente). Task Scheduler segue **Habilitado** (`Enabled=true`,
`Estado de tarefa agendada: Habilitado`), próxima execução agendada para
25/07/2026 06:00. Nenhuma alteração de código, task, horário, credenciais,
`.env` ou health check separado nesta rodada — só observação e leitura.

**Veredito**: GO — a automação diária de ML/TikTok/regional está validada
ponta a ponta, incluindo o disparo real pelo Task Scheduler sem qualquer
intervenção manual. Recomenda-se observar mais algumas execuções diárias
antes de considerar o horário 06:00 definitivamente estável (só uma
ocorrência real até aqui).

### Testes desta fase

- `pipelines/tests/test_ops_preflight.py` (64 testes, +17 no Gate B2, +5 no Gate B6.1b) — checks individuais, `LOCAL_PG_URL` sem fallback e com allowlist de host (bloqueia sem tentar conectar), sessão read-only, checks de arquivo Shopee **separados por padrão real** (orders/stats/ads) contra a lista oficial de marcas do conector, bloqueio da fonte inteira quando uma marca oficial falta, `SHOPEE_DATA_PATH` nunca aparece na mensagem, guardas estruturais, **`check_gold_regional_write`** (bloqueia sem secret/guardrails/preflight de escrita, passa com fakes, nunca expõe secret/URL, nunca abre conexão de escrita neste módulo), **`check_sync_region_consent`** (bloqueia sem `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1` ou com valor≠`1`, passa com `1`), `SOURCE_CHECKS`/`run_preflight` das duas novas fontes (`gold_regional_incremental`, `sync_region_daily`); **Gate B6.1b**: `check_sync_region_consent` também passa com consentimento persistente via arquivo (`.env.region-sync.local`), bloqueia com arquivo ausente/inválido, variável de ambiente tem prioridade sobre o arquivo, nunca expõe o conteúdo do arquivo (só o nome).
- `pipelines/tests/test_ops_region_sync_consent.py` (15 testes, novo no Gate B6.1b) — env var já definida vence sem ler o arquivo; sem env e sem arquivo retorna `False`; arquivo com valor `1` seta em `os.environ`; valores inválidos (`0`/`true`/`yes`/vazio) não setam e retornam `False`; arquivo vazio ou sem a chave retorna `False`; chaves extras no arquivo são ignoradas sem falhar; `env_path=None` resolve `DEFAULT_REGION_SYNC_CONSENT_PATH` no momento da chamada (permite monkeypatch sem passar `env_path`); nunca cria o arquivo; guardas estruturais (nunca escreve em disco, nunca imprime nada).
- `pipelines/tests/test_ops_health_check.py` (59 testes, +9 no Gate B1, +8 no Gate B4, +7 no Gate C1) — todas as fontes esperadas aparecem mesmo sem histórico, `execution_stale`/`last_run_failed` **separados** (falha na última execução sempre reprova, mesmo com sucesso recente dentro do threshold), data no futuro em fonte diária e manual/mensal sempre erro de qualidade, regressões de `build_report` para os dois casos, JSON com `reason`, credenciais nunca aparecem, **`ok_critical` separado de `ok`** (Shopee stale nunca reprova `ok_critical` sozinho; ML/TikTok stale reprova os dois; Bug 8 sempre reprova os dois; JSON expõe `critical` por fonte/entrada); **Gate B4**: `shopee_product_monthly` (execução) isolado como `critical=False` — stale só nele (ou combinado com o gap de dado de Shopee) reprova `ok` mas nunca `ok_critical`/exit code; ML/TikTok em execução e Bug 8 continuam reprovando `ok_critical` normalmente; regressão ponta-a-ponta do achado do Gate B3 via `main()`; **Gate C1**: `shopee_daily`/`shopee-stats_daily`/`shopee-ads_daily` (execução) também isolados como `critical=False` — todo o grupo Shopee de execução stale ao mesmo tempo reprova `ok` mas nunca `ok_critical`/exit code (`main()` retorna 0); ML/TikTok stale reprova `ok_critical`/exit 1 mesmo com todo Shopee stale; Bug 8 continua crítico mesmo com todo Shopee de execução stale; regressão do teste pré-existente que assumia "só `shopee_product_monthly` é não-crítico" (atualizado para a lista completa das 4 fontes não-críticas).
- `pipelines/tests/test_ops_orchestrate.py` (65 testes, +11 no Gate B2, +14 no Gate C1) — preflight bloqueado impede o comando real, exit code propagado, **timeout individual por step vira `FAILED` e não trava fontes independentes seguintes** (ML timeout → TikTok normais), orçamento somado dos timeouts de `full_daily` (3600s desde o Gate C1) com margem sobre o timeout externo (9000s), `depends_on`/`always_run`, **health check comprovadamente o último `call` em cenários mistos de sucesso/falha/timeout/bloqueio, para os dois pipelines**, pipelines antigos de 2 tarefas confirmados como removidos, **`compute_overall_status` (Step.critical)**: falha/bloqueio crítico → FAILED; SKIPPED por dependência não-crítica nunca conta como falha; crítico tem prioridade sobre não-crítico quando ambos falham na mesma execução; **Gate B2**: `gold_regional_incremental`/`sync_region_if_needed` na ordem certa, ambos `critical=True`, `sync_region_if_needed` depende de `gold_regional_incremental`, falha/bloqueio de qualquer um dos dois vira `FAILED` (nunca `DEGRADED`), `sync_region_if_needed` pulado (`SKIPPED`) se `gold_regional_incremental` falhar; **Gate C1**: `PIPELINES` tem exatamente `full_daily`/`shopee_manual_refresh`; `full_daily` não contém nenhum step Shopee e nunca fica `DEGRADED` (todos os steps são críticos); `shopee_manual_refresh` contém os 3 steps Shopee + `sync_produtos_shopee` + `monitor_bug8` na ordem correta; `daily_shopee_orders`/`stats`/`ads` são críticos dentro do pipeline manual (falha vira `FAILED`, não `DEGRADED`); `sync_produtos_shopee` continua `critical=False`; `monitor_bug8` depende de `sync_produtos_shopee==SUCCESS`; falha de `daily_shopee_orders` nunca afeta `full_daily` (pipelines isolados); todas as fontes Shopee bloqueadas no preflight não derruba `full_daily` (nem aparecem nele).
- `pipelines/tests/test_ops_sync_region_if_needed.py` (18 testes, novo no Gate B2, +4 no Gate B6.1b) — `no_op` nunca chama sync quando `needs_sync=False`; `needs_sync=True` chama sync exatamente 1 vez com `sync=True`; falha de diagnose aborta antes de qualquer tentativa de sync; falha de sync propaga como `SyncIfNeededError` sanitizado; sem retry automático em nenhum dos dois caminhos; erros nunca vazam credenciais; `main()` com exit 0 (no-op e sync) e exit 1 (erro) sem propagar exceção nativa; guardas estruturais (não reimplementa a checagem de consentimento em código, não usa `subprocess`); **Gate B6.1b**: `main()` chama `ensure_region_sync_consent()` antes de `run()`; `needs_sync=True` com consentimento vindo do arquivo chama sync exatamente 1 vez (e a env var já está presente no processo nesse momento); `needs_sync=False` nunca chama sync mesmo com consentimento disponível; sem consentimento (nem env nem arquivo), o gate original de `sync_region_daily.run_sync` continua recusando antes de qualquer escrita.
- `pipelines/tests/test_ops_schedule_plan.py` (26 testes, +1 assert no Gate B2, +3 no Gate C1) — 1 tarefa (não 2, não 8), XML bem formado com `MultipleInstancesPolicy=IgnoreNew`/`StartWhenAvailable=true`/`ExecutionTimeLimit=PT2H40M` (9600s, maior que os 9000s do lock, com margem para a limpeza pós-timeout) **e `Settings/Enabled=false` sempre** (tarefa nasce desativada; `render_task_scheduler_xml()` não aceita nenhum parâmetro para habilitar), horário 06:00 sinalizado como hipótese não confirmada, aspas dobradas validadas no parser real do PowerShell, `/f` nunca por padrão, nenhuma capacidade de execução importada, **orçamento interno (3600s desde o Gate C1) travado em sincronia com `orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS`, com margem sobre os 9000s do lock**; **Gate C1**: `shopee_manual_refresh` nunca ganha entrada em `PROPOSED_SCHEDULE`; a única tarefa agendada continua sendo `full_daily`, sem nenhuma menção a Shopee no comando renderizado; as notes da tarefa documentam a exclusão do Shopee (cita `shopee_manual_refresh` só para explicar que ficou de fora, nunca implicando que faz parte do conteúdo executado).
- `apps/api/etl/tests/test_load_shopee_products_local_pg_guard.py` (10 testes) — `LOCAL_PG_URL` sem fallback, host remoto bloqueado, hosts locais aceitos, erros nunca expõem credenciais, imports de função pura nunca exigem `LOCAL_PG_URL`.
- `scripts/run_with_lock.tests.ps1` (15 testes Pester) — exit code propagado, `WorkingDirectory` correto por padrão e a partir de `C:\Windows\System32`, lock com dono vivo bloqueia mesmo antigo, lock com dono morto recupera, duas tentativas simultâneas via `Start-Job` (exatamente uma vence), credencial em argumento recusada, timeout mata o processo e retorna 124, **aguarda confirmação real de término do processo morto antes de liberar o lock** (verificado via `Get-CimInstance Win32_Process`, não só inferido), **`LockName` com `..`/`/` rejeitado antes de tocar disco**, stdout/stderr separados.
- `scripts/run_task.tests.ps1` (19 testes Pester, +6 no Gate B6.1d, +6 no Gate C1) — `Resolve-TaskInvocation` resolve as duas `TaskKey`s (`full_daily`, `shopee_manual_refresh`) com lock/timeout/módulo corretos, timeout externo (9000s) maior que o orçamento interno de cada pipeline (3600s/3780s desde o Gate C1), `$null` para chave desconhecida, dot-source não executa nada; **Gate B6.1d**: `Invoke-ResolvedTask` reproduz o bug real e confirma o fix (`--pipeline full_daily` sobrevive intacto via arg-dumper temporário), `-WorkingDirectory` com path contendo espaço continua funcionando, `-WorkingDirectory` simples continua aplicado, lock/timeout continuam funcionando (`BLOCKED`/124 real), `run_task.ps1` nunca passa `-SimulateStopProcessFailure`; **Gate C1**: `shopee_manual_refresh` resolve lock/timeout/módulo corretos e usa o mesmo `LockScript` (run_with_lock.ps1) que `full_daily`; adicionar a nova `TaskKey` não altera em nada a resolução de `full_daily` (locks distintos, sem interferência); `shopee_manual_refresh` não passa nenhuma flag insegura (`--skip-lock`/`--force`); `--pipeline shopee_manual_refresh` também sobrevive intacto via `Invoke-ResolvedTask` (mesmo arg-dumper), com lock próprio que nunca conflita com o de `full_daily`.
- Total: 1.451 testes pytest (`pipelines/tests` + `apps/api/etl/tests` + `apps/api/tests`, todos passando) + 17 testes Pester em `scripts/run_with_lock.tests.ps1` (inalterado) + `scripts/run_task.tests.ps1` (19, +6 no Gate B6.1d, +6 no Gate C1). Todos os testes pytest e Pester relevantes a este gate foram executados nesta revisão (nenhum banco real tocado, nenhum `full_daily`/`shopee_manual_refresh` real).

---

## Snapshot vigente dos exports Shopee — regra fail-closed (Gate SH-API-2A-R)

`apps/api/etl/load_shopee_products.py` deduplica os exports XLSX **por pedido**
antes de qualquer filtro comercial. Sem isso, um pedido presente em dois exports
sobrepostos era somado duas vezes em `marts.fact_shopee_product_monthly` e na tela
`/produtos/shopee`.

**Regra canônica**, nesta ordem obrigatória:

1. classificar e reduzir TODA a population de arquivos a snapshots lógicos;
2. agrupar por `brand + ID do pedido`;
3. escolher UM snapshot vencedor por pedido — o de maior `(janela_fim, janela_início)`;
4. manter TODAS as linhas daquele pedido no snapshot vencedor;
5. **só então** aplicar `status == "Concluído"`;
6. **só então** agregar por produto/variação/mês.

A ordem dos passos 3 e 5 não é estética: o mesmo pedido aparece com estados
diferentes em snapshots de idades diferentes, e filtrar antes de escolher
inverte o resultado.

**Snapshot lógico** = `(marca, janela_início, janela_fim)`. As partes
`part_N_of_M` são pedaços complementares do mesmo snapshot, nunca versões
concorrentes: exige-se a série completa de 1 a M, sem número repetido e com M
consistente.

**Formatos ACEITOS** — 212 arquivos, de um total de 214 `.xlsx` com "order" no
nome (213 são `Order.all`; 2 rejeitados; 0 cópias byte-idênticas):

| Padrão | Ocorrências |
|---|---|
| `Order.all.YYYYMMDD_YYYYMMDD.xlsx` | 12 |
| `Order.all.YYYYMMDD_YYYYMMDD_part_N_of_M.xlsx` | 8 |
| `Order.all.order_creation_date.YYYYMMDD_YYYYMMDD.xlsx` | 46 |
| `Order.all.order_creation_date.YYYYMMDD_YYYYMMDD_part_N_of_M.xlsx` | 146 |

**Formatos REJEITADOS — abortam a carga inteira, antes de interpretar qualquer
workbook com pandas/openpyxl e antes de abrir conexão com banco.** A triagem
pode fazer leitura **binária** de candidatos da mesma janela, e apenas deles,
para comparar SHA-256 e decidir se são cópias idênticas:

| Caso | Exemplo real | Motivo |
|---|---|---|
| Instante de export | `Order.all.20260717T155433Z.xlsx` | Carrega o INSTANTE, não a janela — não é ordenável contra um nome com janela |
| Outro tipo de export | `Order.toship.order_creation_date.20260805_20260805.xlsx` | Não é `Order.all`; é um subconjunto (a enviar) |
| Sufixo de cópia | `Order.all.order_creation_date.20260805_20260805 (1).xlsx` | O sufixo não estabelece ordem temporal |
| Parte ausente / N,M inválidos / número repetido / totais divergentes | — | Snapshot incompleto ou ambíguo |
| Dois arquivos inteiros divergentes na mesma janela | — | Só cópia byte a byte é reduzida a uma (`exact_copy`); hash diferente aborta |

**Por que fail-closed.** O formato de instante existiu de verdade no histórico
(`kokeshi`, ingerido em 17/07). Seu nome carrega `20260717`, maior que a
`janela_fim` `20260630` do mensal de junho — mas o mensal é o snapshot
POSTERIOR. Ordenar pelo nome elegeria o snapshot errado e removeria 18.924
pedidos concluídos, R$ 971.946,52 (medido no Gate SH-API-2A), quase 10× a
inflação que a correção remove. Como nada no nome de um arquivo com janela
revela quando ele foi extraído, os dois formatos não têm ordem entre si — então
a carga recusa em vez de adivinhar.

**Nunca usados como ordem:** `mtime`, `ctime`, ordem do `glob`.
**Nunca usados como chave de dedup:** `pedido + SKU` (SKU repetido legitimamente
no mesmo pedido existe), `DISTINCT row_sha256` como regra principal (não resolve
snapshot cujo status amadureceu).

### Estado (não confundir com produção)

- **A duplicação NÃO foi corrigida em produção.** O código está implementado e
  testado; nenhum backfill foi executado, nenhuma escrita em banco foi feita.
  O valor da inflação depende da população declarada — não use um número único:

  | | Valor | População que o produz |
  |---|---|---|
  | **Confirmado** | R$ 104.728,27 | manifesto histórico do Data Mart (208 arquivos), reproduzido ao centavo por consulta |
  | **Condicionado** | R$ 104.304,98 | população do disco hoje (212 aceitos) — não contém o export de instante que gera os R$ 423,29 de kokeshi/2026-06 |
  | **Sob arbitragem, agora resolvido** | R$ 224.617,79 | rituária/2026-07, do export cruzado `20260707_20260806` que existe no disco e não no manifesto — arbitrado como CONFIRMADO legítimo (Gate SH-API-2B) |

  População recomendada após a arbitragem: **212 arquivos aceitos**, delta total
  esperado **R$ 328.922,77** (ver Gate SH-API-2B).
- **Sidecar de manifesto: não implementado.** Foi avaliado no Gate SH-API-2A como
  a alternativa robusta (declarar o instante de extração no depósito dos
  arquivos) e ficou fora do escopo desta rodada. A regra por janela é
  **transitória** enquanto a API Shopee é investigada.
- **`units_sold` de `fact_marketplace_daily_performance` continua pendente**:
  duplicação confirmada em 14.705 unidades (6 datas, 19 linhas marca×dia), pelo
  mesmo mecanismo em `pipelines/connectors/shopee/_parser.py`. Não tocado nesta
  rodada — a coluna não é exposta em nenhuma rota pública e o arquivo divergia de
  `origin/main` em outras branches.
- **GMV, pedidos e ticket oficiais não mudam.** `gmv` de
  `fact_marketplace_daily_performance` é escrito por shop-stats (Gate R2.1); o
  patch de orders não inclui a coluna. `orders` é contagem distinta, imune; e
  `avg_ticket` é derivado de `gmv / orders` em tempo de serving.
- **API Shopee segue em shadow/investigação** e não substitui nenhuma carga
  manual. Cobertura atual: 4 de 5 marcas (`kokeshi` ausente).

### Camadas: o que já está corrigido e o que não está (Gate SH-API-2B)

| Camada | Estado |
|---|---|
| Algoritmo (regra de snapshot, fail-closed) | **implementado e testado** |
| População aprovada | **212 arquivos**, declarados por nome+SHA-256 no dry-run |
| Arquivos inconclusivos | **nenhum** — os 2 rejeitados de `barbours` foram arbitrados e provados irrelevantes para Produtos |
| Dry-run **offline** | **disponível** — `--offline-dry-run`: arquivos e memória apenas, zero conexão. Campos "antes" e deltas saem como **N/D**, nunca zero |
| Dry-run **contra banco** | **implementado, NÃO exercitado** — `--dry-run --target local\|neon`: query parametrizada, sessão `READ ONLY` e **preflight de identidade** (nome do banco declarado em `BACKFILL_*_EXPECT_DB` + impressão digital estrutural do schema `marts`). `DATABASE_URL` genérico é recusado. Em 2026-09-08 as duas execuções retornaram **BLOCKED (exit 5)**: nenhuma credencial `BACKFILL_*_RO_URL` está provisionada |
| Executor de escrita | **adapter implementado** (`ScopedReplaceExecutor`: backup em tabela real e nomeada, `DELETE` escopado, `INSERT`, contagem, commit/rollback), coberto por teste de contrato — **`--apply` BLOQUEADO em `main()`** |
| Backfill | **NOT READY** — nunca executado contra banco algum; o caminho local→Neon está desenhado e testado com fakes, não exercitado |
| Mart **local** | **não corrigido** |
| **Neon** | **não corrigido** |
| Tela de Produtos | **serve os dados antigos** |
| API Shopee | continua em **shadow**, não substitui carga manual |
| `units_sold` da fato diária | **fora deste gate**, duplicação de 14.705 unidades pendente |


### Credencial read-only dedicada — INEXISTENTE (Gate SH-API-2C-R)

Com autorização do proprietário, as conexões configuradas do projeto foram
localizadas e **diagnosticadas** (somente `SELECT`, transação `READ ONLY`,
encerrada com rollback). Nenhum valor foi impresso ou persistido.

| Verificação | Serving (Neon) | PostgreSQL local |
|---|---|---|
| Conecta | sim | sim |
| `transaction_read_only=on` | sim (forçado pela sessão) | sim (forçado) |
| Role superuser | não | **SIM** |
| createdb/createrole/replication/bypassrls | **SIM** | **SIM** |
| INSERT/UPDATE/DELETE/TRUNCATE em `marts.fact_shopee_product_monthly` | **SIM** | **SIM** |
| CREATE no schema `marts` | **SIM** | **SIM** |
| SSL ativo | **não comprovado** | n/a |
| Veredito | **REPROVADO** | **REPROVADO** |

```
BLOCKED — DEDICATED_READONLY_ROLE_MISSING
```

As duas credenciais existentes são **graváveis**. Reatribuí-las a
`BACKFILL_*_RO_URL` seria tratar credencial de escrita como read-only —
exatamente o que o contrato proíbe. As três reconciliações reais seguem
pendentes.

**Proposta para rodada administrativa separada** (não executada aqui; nenhuma
role foi criada, nenhum privilégio concedido):

```sql
-- em CADA destino, executado por quem tem autoridade administrativa
CREATE ROLE shopee_produtos_ro LOGIN PASSWORD '<gerada, fora do repo>';
ALTER  ROLE shopee_produtos_ro NOSUPERUSER NOCREATEDB NOCREATEROLE
                               NOREPLICATION NOBYPASSRLS;
ALTER  ROLE shopee_produtos_ro SET default_transaction_read_only = on;
GRANT  CONNECT ON DATABASE <banco> TO shopee_produtos_ro;
GRANT  USAGE   ON SCHEMA  marts    TO shopee_produtos_ro;
GRANT  SELECT  ON marts.fact_shopee_product_monthly TO shopee_produtos_ro;
-- nenhum INSERT/UPDATE/DELETE/TRUNCATE, nenhum CREATE, nada em outros schemas
```

No destino remoto, exigir SSL na role (`hostssl` no pg_hba ou equivalente do
provedor) e confirmar `pg_stat_ssl.ssl = true` na sessão.

### Drift de schema entre os dois destinos (medido, Gate SH-API-2C-R)

`marts.fact_shopee_product_monthly` tem **15 colunas no serving e 14 no
local** — o serving tem `ingested_at` a mais; nenhum tipo divergente nas 14
comuns. Consequências:

1. qualquer propagação local → Neon precisa ser **explícita em colunas**; um
   `INSERT ... SELECT *` desalinharia. O `ScopedReplaceExecutor` já monta a
   lista de colunas a partir da staging, então está correto — mas a etapa
   local→Neon precisa tratar `ingested_at` deliberadamente;
2. `ingested_at` do serving é candidata natural a alimentar `refreshed_at`,
   hoje nulo na tela de Produtos.

Escala dos schemas (sinal, não gate): serving com 123 schemas e 54 tabelas em
`marts`; local com 9 e 16. **A regra anterior "o local não tem a tabela de
serving" foi REFUTADA** — o local tem — e foi removida do código.

### Reconciliação contra banco — BLOQUEADA (Gate SH-API-2C, 2026-09-08)

As três reconciliações reais (candidato × local, candidato × Neon, local × Neon)
**não foram executadas**: nenhuma das quatro variáveis read-only está
provisionada na sessão.

```
BLOCKED — READONLY_TARGETS_NOT_PROVISIONED
```

Para habilitar, o proprietário define as quatro **na mesma sessão** que executa
o dry-run (valores omitidos aqui de propósito):

```powershell
$env:BACKFILL_LOCAL_RO_URL   = "<DSN read-only do PostgreSQL local>"
$env:BACKFILL_LOCAL_EXPECT_DB= "<nome do banco local esperado>"
$env:BACKFILL_NEON_RO_URL    = "<DSN read-only do Neon serving>"
$env:BACKFILL_NEON_EXPECT_DB = "<nome do banco Neon esperado>"
```

Requisitos da credencial: role **sem** privilégio de escrita nas tabelas de
`marts`, SSL obrigatório no destino remoto, e nenhuma conexão a primary
gravável. `DATABASE_URL` e qualquer credencial gravável **não** são aceitos —
o código recusa.

Depois, com a raiz isolada dos 212 aprovados:

```
python -m etl.backfill_shopee_products --scope apice:2026-05 --scope barbours:2026-05   --scope kokeshi:2026-08 --scope rituaria:2026-07   --source-root <raiz-isolada> --dry-run --target local
```
(idem com `--target neon`.)

### Piso de maturidade — MEDIDO, não arbitrado (Gate SH-API-2B-R2)

Participação do GMV `Concluído` sobre o GMV não-cancelado, por competência,
sobre a população deduplicada do Data Mart:

| Competência | share | leitura |
|---|---|---|
| 2026-01 a 2026-04 | 1,0000 | estável |
| 2026-05 | 0,9987 | estável |
| 2026-06 | 0,9993 | estável |
| **2026-07** | **0,7324** | **parcialmente imatura** |
| **2026-08** | **0,0007** | **materialmente imatura** |

O mínimo observado entre os seis meses historicamente estáveis é **0,9987**.
Qualquer piso de reconciliação deve sair daí — a proposta é **0,99**, e a
decisão do valor final é do proprietário. Sem piso medido, o eixo de maturidade
fica `maturity_unknown`; o código **não arbitra** um número.

Consequência que não estava registrada: **julho também está imaturo** (73,2%).
O valor publicado de Produtos para 2026-07 representa ~73% do GMV não-cancelado
da competência.

### Estados de frescor — quatro eixos ORTOGONAIS (Gate SH-API-2B-R2)

A versão anterior colapsava tudo num único estado mutuamente exclusivo, e
`maturation_pending` **escondia** `load_stale`. Corrigido: `classify_scope`
devolve quatro eixos independentes, porque as condições são independentes.

| Eixo | Valores |
|---|---|
| `source_status` | `source_present` · `source_absent` |
| `load_status` | `load_current` · `load_stale` · `load_absent` |
| `eligibility_status` | `eligible` · `partially_eligible` · `no_eligible_rows` |
| `maturity_status` | `source_mature` · `source_materially_immature` · `maturity_unknown` |
| `coverage_status` | `coverage_ok` · `coverage_below_allowlist` |

**2026-08 registra simultaneamente**, conforme evidência: `load_stale` (destino
defasado ante o candidato mais recente) **e** presença física no Neon (188
chaves) **e** `no_eligible_rows` (cobertura analítica inadequada) **e**
`source_materially_immature` (share 0,0007 contra piso 0,99) **e**
`coverage_below_allowlist` (4 de 5 marcas). Nenhum desses esconde o outro.

Os alertas continuam **blueprint** — `QUALITY_ALERT_BLUEPRINT` é uma estrutura
de dados, **não** está conectada ao health check nem a `torre_qualidade_dados`.

### Estados de frescor (versão anterior, superada — mantida por rastreabilidade)

`classify_scope_freshness` nomeia cada situação de um par (marca, competência).
A fórmula comercial **não mudou** — o que mudou é que a tela vazia deixa de
poder passar por "sem dados" ou por "saudável":

| Estado | Quando | Alerta |
|---|---|---|
| `source_missing` | nenhum arquivo-fonte para o escopo | sim |
| `load_stale` | destino vazio (ou defasado) com Shopee Daily > 0 | sim |
| `present_but_not_eligible` | linhas existem, nenhuma elegível, **mês corrente** | sim, se a diária for material |
| `maturation_pending` | linhas existem, nenhuma elegível, **mês fechado** | sim |
| `complete` | elegíveis > 0 e carga em dia; ou mês genuinamente sem venda | não |

**2026-08 cai em `maturation_pending`** — 188 chaves presentes, 0 elegíveis,
Shopee Daily com GMV material. Nunca `complete`, nunca "sem dados".

`refreshed_at` deve vir de `audit.source_sync_run` — **nunca** de `NOW()` da
requisição, **nunca** da data da competência. Hoje o campo simplesmente não é
preenchido pelos endpoints de Produtos.

Quatro alertas no blueprint (`QUALITY_ALERT_BLUEPRINT`), todos em **health_check
e `torre_qualidade_dados`**, todos `critico_para_exit=False`: fonte manual pode
ser não crítica para o exit code, mas nunca silenciosa para o usuário.

### Arquitetura de escrita local → Neon (desenhada, não executada)

Ordem obrigatória: XLSX → scoped replace no **local** (transação 1, backup
durável) → validar local pós-commit → local → scoped replace dos **mesmos
escopos** no **Neon** (transação 2, backup durável) → validar Neon contra local
→ auditar as duas etapas com `run_id` comum.

**São dois bancos, portanto dois commits.** Não existe transação distribuída
aqui, e o estado parcial é um resultado nomeado:

| Resultado | Significado |
|---|---|
| `OK` | as duas etapas commitaram e validaram |
| `PARTIAL` | local commitado, Neon falhou → retry **somente** da propagação local→Neon, nunca reprocessar XLSX |
| `REFUSED` | validação reprovou antes de qualquer escrita |

Proibido por contrato (com teste): aplicar **somente no Neon** deixando o local
antigo (`assert_not_neon_only`), `TRUNCATE` de tabela inteira, backup em `TEMP
TABLE` (morre com a sessão e não é backup operacional), e UPSERT como único
mecanismo. Concorrência com `sync_produtos_shopee`, `full_daily` e
`shopee_manual_refresh` deve ser impedida por lock compartilhado; quem não
adquirir, **aborta** — nunca espera indefinidamente.

### Incidente de frescor de Produtos Shopee (2026-09-08, read-only)

Sintoma relatado: Shopee diária completa até 31/08, Produtos Shopee com 0 linhas
em 2026-08 e 2026-09, `refreshed_at` nulo, zero warnings.

Classificação formal: **`maturation_pending`** (mês fechado, presença física,
zero elegíveis, diária material). **Não é ausência de dados.** `/produtos/shopee/summary?ref_month=2026-08`
devolve `total_count=188`, `eligible_count=0`, `excluded_zero_gmv_count=188`:
as 188 chaves de agosto **existem** no Neon, todas com `gmv=0`, e o filtro de
elegibilidade `gmv > 0` esvazia a tela.

**Causa raiz (comprovada):** `_aggregate` só soma GMV de `status == "Concluído"`,
e os exports XLSX de agosto disponíveis hoje quase não têm pedidos concluídos —
maturação da fonte manual. O loader corrigido, rodado em memória sobre o disco
atual, produz para 2026-08: apice 75 chaves / GMV 0,00 / 0 concluídos / 408
cancelados; kokeshi 102 / 3.311,08 / 87 / 8.441; lescent 41 / 98,61 / 1 / 838;
rituária 43 / 78,60 / 1 / 450. Ou seja: o mart está **correto para a fonte que
tem** — a fonte é que ainda não amadureceu.

**`refreshed_at` nulo é lacuna de contrato, não falha de carga:**
`get_produtos_shopee_summary` nunca preenche o campo. `_max_refreshed_at` existe,
mas serve outros endpoints e lê `MAX(ingested_at)` da **fato diária**. Deve
futuramente vir da auditoria real da carga/sync — nunca da data da competência e
nunca de `NOW()` da requisição.

**Falha de observabilidade (separada do incidente de dados):** `/operacoes`
devolve `alertas: []` e `/quality` de 2026-08 mostra Shopee normalmente
(cancel_rate 11,94%, 2.784 pedidos de apice) enquanto Produtos/agosto está
vazio. Nada avisa. Três alertas propostos, **não implementados**:

1. último mês fechado com Shopee diária > 0 **e** Produtos Shopee = 0;
2. `MAX(ref_month)` de Produtos Shopee atrás do último mês fechado;
3. cobertura de marcas na competência abaixo da allowlist esperada.

Os três devem aparecer **tanto no health check quanto em
`torre_qualidade_dados`**. Fonte manual pode ser não crítica para o exit code,
mas **nunca silenciosa para o usuário**.

### Ação exigida do operador antes da próxima carga

Hoje a triagem **aborta em `barbours`**: dois arquivos fora do padrão convivem em
`shopee/barbours/`. As outras 4 marcas passam (21, 116, 18 e 17 arquivos
aceitos). Para destravar, retirar da pasta da marca:

- `Order.all.order_creation_date.20260805_20260805 (1).xlsx`
- `Order.toship.order_creation_date.20260805_20260805.xlsx`

Retirar significa mover para fora de `shopee/{marca}/` (nunca apagar: os exports
são evidência). Se a janela 05/08 de `barbours` precisar existir, baixar de novo
o export como `Order.all.order_creation_date.20260805_20260805.xlsx`.

## Contrato de qualidade do escopo — Produtos Shopee (Gate SH-API-2D)

### O problema que este contrato resolve

Até este gate, a tela de Produtos, a API e o MCP apresentavam **qualquer**
competência com o mesmo peso. Três exemplos medidos em 08/09/2026:

| Competência | O que a Torre mostrava | O que era verdade |
|---|---|---|
| 2026-07 | R$ 5.512.907,44, sem ressalva | o mart estava materialmente atrás da diária |
| 2026-08 | tela vazia | **188 linhas carregadas**, todas com GMV = 0 (nenhum pedido concluído) |
| 2026-09 | tela vazia | competência **nunca carregada**; diária já tem até 07/09 |

Nos três casos o número exibido estava aritmeticamente correto. O que faltava
era o **regime**: número certo com regime errado é número errado para quem
decide.

### Os seis eixos (ortogonais, nunca um rótulo único)

Um único estado mutuamente exclusivo esconde o eixo que importa — foi
exatamente o defeito F5 do Gate SH-API-2B-R2, em que `maturation_pending`
encobria a defasagem da carga. O contrato tem seis eixos independentes:

| Eixo | Pergunta exata | Valores | Fonte durável (Neon) |
|---|---|---|---|
| `source_status` | **alguma** execução já carregou esta competência? | `source_ever_loaded` · `source_never_loaded` · `source_history_unknown` | `audit.source_sync_run` |
| `load_status` | há linhas, e existe evidência de que estão atrás da diária? | `load_present` · `load_behind_daily` · `load_absent` | as duas fatos |
| `eligibility_status` | quantas linhas sobrevivem ao filtro de exibição (`gmv > 0`)? | `eligible` · `partially_eligible` · `no_eligible_rows` | `fact_shopee_product_monthly` |
| `maturity_status` | o índice operacional de maturação alcançou o limiar? | `mature` · `materially_immature` · `maturity_unknown` | índice vs. limiar |
| `coverage_status` | todas as marcas que venderam na diária aparecem no mart? | `coverage_ok` · `coverage_below_expected` · `coverage_unknown` | as duas fatos |
| `loaded_at` | quando o **mart** foi publicado neste escopo? | timestamp + `load_age_days` | `MAX(ingested_at)`, fallback no último sync |

`definitive` é um **atalho de renderização** derivado dos eixos, nunca a fonte
da verdade. `definitive = false` não significa "número errado": significa "não
use como definitivo sem ler os eixos".

### Nomenclatura — por que estes nomes (Gate SH-API-2D-R/V)

A primeira versão usava nomes que afirmavam mais do que o contrato media. Três
correções, todas de significado e não de estilo:

**`source_ever_loaded`, não `source_covered`.** O `bool_or(...)` sobre
`audit.source_sync_run` responde "esta competência já foi carregada alguma
vez?". "Coberta" sugere que a fonte está em dia — o eixo não mede isso e não
pode afirmar. Um mês carregado uma única vez em fevereiro responde
`source_ever_loaded` para sempre, mesmo que a Shopee tenha mudado tudo depois.

**`load_present`, não `load_current`.** "Current"/"atual" é uma afirmação
**temporal**. O eixo mede presença de linhas mais ausência de evidência de
atraso — não frescor. O mart publicado em 05/08/2026 aparecia como
`load_current` para julho 34 dias depois: verdadeiro pela definição interna,
enganoso como palavra. A assimetria é deliberada: `load_present` é *ausência
de evidência de atraso*, nunca prova de estar em dia.

**`load_behind_daily`, não `load_stale`.** "Stale" também é temporal e não diz
o que foi medido. O nome novo declara a própria evidência: a diária registrou
dias **desta** competência em datas posteriores à publicação do mart, logo
existe venda conhecida que o mart comprovadamente não viu.

### O índice operacional de maturação

```
maturation_index = SUM(gmv) de marts.fact_shopee_product_monthly
                 ÷ SUM(gmv) de marts.fact_marketplace_daily_performance (Shopee)
```
no mesmo par marca × competência.

**Não é percentual de conclusão, share nem completude.** Numerador e
denominador vêm de **populações diferentes**: o numerador soma subtotais de
item do mart de Produtos; o denominador é o GMV líquido do shop stats na
diária. Por isso o índice **passa de 1,00** em meses fechados — o que seria
absurdo num percentual e é o regime **normal** aqui. Valores > 1 são permitidos
e **nunca truncados**: truncar apagaria justamente o sinal de que as duas
populações não são equivalentes.

O índice serve para uma coisa só: **separar regime maduro de regime imaturo**.
Nunca para afirmar "X% dos pedidos foram concluídos".

Medido em 30 pares marca × competência (2026-01..2026-08, Neon, somente
leitura):

| Regime | Faixa do índice |
|---|---|
| meses fechados e maduros (jan–jun, 5 marcas) | **1,0047 a 1,1234** |
| competência em maturação (julho, 5 marcas) | **0,6643 a 0,7700** |
| competência sem conclusão (agosto, 5 marcas) | **0,0000** |

**O limiar 0,99 é heurístico e configurável**, não meta, SLA nem constante de
negócio. Vive em `Settings.shopee_maturation_threshold`
(`apps/api/app/config.py`), sobrescrevível por `SHOPEE_MATURATION_THRESHOLD`
**sem deploy de código**. Foi escolhido por cair dentro da faixa **vazia**
entre os dois regimes — qualquer valor em (0,7700 ; 1,0047) separa igualmente
bem — e por coincidir com o valor medido de forma independente na `silver` do
Data Mart (0,9987) no Gate SH-API-2C-R3. Deve ser revisto quando houver mais
meses fechados.

**O valor bruto nunca é exibido sozinho.** Toda superfície que mostra o número
carrega junto `maturation_index_note`, que explica que não é percentual. A
faixa da tela nem lê o campo diretamente: o número chega dentro da mensagem já
formada pelo backend, justamente para não poder ser renderizado como "%".

**Entrada impossível é erro, não veredito.** GMV negativo ou `NaN` levanta
`ScopeQualityInputError` em `compute_maturation_index`; o chamador degrada para
`maturity_status = maturity_unknown` com aviso **crítico**
`shopee_produtos_indice_invalido`, e nenhum veredito de maturação é emitido.
Sem essa guarda, `NaN >= limiar` é `False` em Python e o mês viraria "imaturo"
— um veredito inventado a partir de lixo. A degradação é preferida a uma
exceção que derrubaria a página inteira de Produtos por causa de uma linha.

### Os dois relógios

`loaded_at` é o carimbo da **publicação no mart** (`MAX(ingested_at)`, com
fallback no último sync bem-sucedido quando o escopo não tem linha). **Nunca**
é a data de atualização do dado na Shopee. São dois relógios distintos e
nenhuma superfície pode confundi-los:

- Um mart publicado há 34 dias pode conter um mês fechado perfeito.
- O mesmo mart, para o mês corrente, pode não ter visto a venda de ontem — e é
  exatamente isso que `load_behind_daily` detecta.

Por isso a faixa escreve, por extenso: *"Publicado no mart em 05/08/2026, há 34
dias — esta é a data da publicação no mart, não da última atualização do dado
na Shopee."* Nenhum título usa "atual", "atualizado" ou "em dia" — há teste que
reprova a reintrodução dessas palavras.

### Estado atual medido (08/09/2026)

| Competência | fonte | carga | elegibilidade | maturação | cobertura | índice | definitivo |
|---|---|---|---|---|---|---|---|
| 2026-01..06 | `ever_loaded` | `present` | parcial | **madura** | ok | 1,0249–1,1098 | **sim** |
| 2026-07 | `ever_loaded` | `present` | parcial | **imatura** | ok | 0,7540 | não |
| 2026-08 | `ever_loaded` | **`behind_daily`** | **nenhuma elegível** | **imatura** | ok | 0,0000 | não |
| 2026-09 | **`never_loaded`** | **`absent`** | nenhuma elegível | não medida | **abaixo** | N/D | não |

Elegibilidade "parcial" nos meses fechados é esperada e apenas informativa
(9 a 30 linhas com GMV = 0 em ~470–500), não bloqueia `definitive`.

O mart de Produtos foi publicado pela última vez em **05/08/2026** — 34 dias
antes desta medição.

### Onde o selo aparece

| Superfície | Campo |
|---|---|
| `GET /produtos/shopee` | `quality`, `refreshed_at` |
| `GET /produtos/shopee/summary` | `quality`, `refreshed_at` (antes **nunca** preenchido) |
| `GET /quality` | `produtos_shopee_quality` |
| Tela de Produtos, aba Shopee | faixa **antes** dos cards A/B/C/D |
| Tela de Qualidade | bloco próprio, fora do bloco de métricas operacionais |
| `torre_produtos_prioritarios` | `data.scope_quality` + `limitations` + aviso no resumo textual |
| `torre_qualidade_dados` | `data.produtos_shopee_scope` + `limitations` + aviso no resumo textual |

Quatro regras de honestidade valem em todas elas:

- **Ausência de selo não é aprovação.** `null` significa "não medido" (canal
  que ainda não publica o selo; janela que não é uma competência única) e a UI
  não renderiza nada — em vez de renderizar um "ok" que ninguém apurou.
- **Todos os avisos aparecem**, não só o mais grave, senão a carga atrás da
  diária volta a se esconder atrás da maturação.
- **Status desconhecido é recusado no schema**, na API e no MCP. Um estado novo
  quebra o consumidor em vez de ser repassado ao modelo, que trataria
  `provavelmente_ok` como aprovação.
- **O índice nunca viaja sem a nota.** Nem na API, nem no MCP, nem na tela.

As limitações do MCP são **derivadas dos avisos do backend**, nunca de uma
lista local — `src/server/oracle/limitations.ts` proíbe explicitamente duplicar
o que a resposta já informa, para o conector não contradizer o mart quando a
medição mudar.

### Códigos de aviso

| Código | Severidade | Significado |
|---|---|---|
| `shopee_produtos_fonte_nunca_carregada` | critical | nenhuma execução bem-sucedida carregou esta competência |
| `shopee_produtos_historico_de_carga_ausente` | warning | não há execução registrada; histórico indeterminado |
| `shopee_produtos_carga_ausente` | critical | zero linhas no mart |
| `shopee_produtos_carga_atras_da_diaria` | warning | a diária tem dias posteriores à publicação do mart |
| `shopee_produtos_nenhuma_linha_elegivel` | critical | há linhas, todas com GMV = 0 |
| `shopee_produtos_elegibilidade_parcial` | info | parte das linhas fora da exibição por GMV = 0 |
| `shopee_produtos_indice_invalido` | critical | entrada impossível; nenhum veredito emitido |
| `shopee_produtos_maturacao_insuficiente` | critical | índice abaixo do limiar heurístico |
| `shopee_produtos_maturacao_nao_medida` | warning | sem carga, ou sem referência na diária |
| `shopee_produtos_cobertura_de_marcas` | warning | menos marcas no mart que na diária |

### Por que não houve migration

As três tabelas necessárias já existiam no Neon. Em particular
`audit.source_sync_run` já é escrita por `pipelines/sync_produtos.py`
(`source_name = 'shopee_product_monthly'`, `marketplace_id = 3`) com
`source_min_date`/`source_max_date`, e é ela que responde `source_status`.

Uma armadilha medida durante a implementação: a carga é **incremental**, então
a última execução cobre apenas a janela que ela atualizou (07..08/2026).
Perguntar "a última execução carregou?" reprovava **todos** os meses fechados.
A pergunta correta é histórica — `bool_or(...)` sobre todas as execuções
bem-sucedidas — e é por isso que o eixo se chama `source_ever_loaded`.

`audit.data_quality_check` (viva: 1.029 linhas, 14 checks distintos) é o
destino natural caso se queira **histórico** do selo no futuro; também não
exige migration. Nada foi escrito.

### Autobegin do SQLAlchemy 2.0 — por que o primeiro `--apply` real falhou

Na primeira execução real (Gate SH-API-2E4) o apply no banco local terminou com
**exit 2**, `InvalidRequestError`, **sem criar backup e sem escrever nada**.

Causa: no SQLAlchemy 2.0 o primeiro `conn.execute(...)` faz **autobegin**. As
portas 5–8 do preflight (identidade, primary, SSL, advisory lock) são consultas
na conexão, então ao fim do preflight a conexão já está em transação. O
`ScopedReplaceExecutor.begin()` seguinte então levanta:

```
InvalidRequestError: This connection has already initialized a SQLAlchemy
Transaction() object via begin() or autobegin
```

Correção (Gate SH-API-2E3-H1): `close_preflight_transaction(conn)`, chamado
**depois** de adquirir o advisory lock e **antes** de entregar a conexão ao
executor. Se `conn.in_transaction()` for verdadeiro, faz `conn.rollback()` e
confirma que a transação fechou; se persistir, levanta e o apply para em exit 2
sem criar backup nem mutar.

Duas propriedades sustentam a ordem escolhida:

1. **O advisory lock é de sessão** (`pg_try_advisory_lock`), não de transação.
   Sobrevive ao rollback, então a janela protegida não se abre em momento
   nenhum. Fosse `pg_advisory_xact_lock`, este rollback soltaria o lock.
2. **O preflight só leu.** Não há nada a preservar no rollback.

O executor **não** reaproveita transação implícita: ele continua dono de duas
transações explícitas e separadas (backup e publicação). Há teste que prova que
`ScopedReplaceExecutor.begin()` levanta se encontrar uma transação aberta.

**Por que os 43 testes do Gate SH-API-2E3 não pegaram isso:** o `FakeConn`
tinha `begin()` permissivo — devolvia transação nova sempre, sem reclamar.
Testes verdes, runtime quebrado em 100% das execuções. É a mesma armadilha do
caso `cursor_factory` já registrado neste projeto. O fake agora modela o
SQLAlchemy 2.0: `execute()` faz autobegin e `begin()` com transação ativa
levanta `InvalidRequestError`. Há ainda prova com `Connection` **real** do
SQLAlchemy (engine descartável em SQLite de memória — stdlib, sem dependência
nova e sem abrir conexão gravável a local ou Neon).

Contraprova medida: desligando a chamada do helper, **11 testes falham**,
incluindo todo o caminho feliz.

### Dois defeitos do apply real (Gate SH-API-2E3-H2)

Na primeira execução real que chegou à mutação (Gate SH-API-2E4-R), o apply
local terminou com **exit 3** — rollback confirmado, nada escrito. A
reconciliação encontrou **duas** causas independentes.

#### 1. `NaN` do pandas não equivale a `NULL` no psycopg2

O psycopg2 adapta `float('nan')` para o literal `'NaN'::float`, e o PostgreSQL
**aceita** isso numa coluna `character varying` — gravando a **string `"NaN"`**
em vez de `NULL`. Na staging de maio: **153 de 278** linhas com
`variation_name` ausente e **3** com `avg_price` ausente, todas como `NaN`
float, não `None`.

Se o INSERT tivesse sucedido, o backfill teria introduzido corrupção em 55% das
linhas do escopo — e **nenhuma das dez portas pegaria**, porque contagem,
chaves e GMV fecham perfeitamente enquanto o conteúdo é corrompido. Hoje, nos
dois destinos, essas 153 linhas estão corretamente `NULL` e a string `'NaN'`
não aparece uma única vez na tabela.

Correção: `normalize_records` converte `None`, `NaN` (float e NumPy), `NaT`,
`pd.NA` e `Decimal('NaN')` para `None`, no último ponto antes do driver.
`assert_bind_params_clean` é a rede seguinte: reprova qualquer marcador
remanescente, para o caso de um caminho futuro esquecer a normalização.

Preservados intactos: `0`, `0.0`, `False`, string vazia, a string literal
`"NaN"`, `Decimal` válido, timestamps e inteiros NumPy. **`+inf`/`-inf` são
recusados**, nunca convertidos: não são ausência, são número fora de faixa
(quase sempre divisão por zero rio acima). Virar `NULL` esconderia o defeito;
virar zero inventaria dado.

Nenhum `pd.isna()` indiscriminado: sobre lista/dict ele devolve **vetor**, e
usar isso num `if` levanta *"truth value of an array is ambiguous"*. O código
protege escalares antes de chamar.

#### 2. `INSERT` na tabela não implica `USAGE` na sequence

`marts.fact_shopee_product_monthly.id` tem
`DEFAULT nextval('marts.fact_shopee_product_monthly_id_seq')` nos **dois**
destinos. Um `INSERT` que omite `id` chama `nextval`, e isso exige **`USAGE` na
sequence** — privilégio separado do da tabela. A role temporária tinha
`SELECT/INSERT/DELETE` e o `INSERT` falhou por permissão **depois** do backup
já commitado e do `DELETE` já executado.

Correção: nova **porta 6b**, que descobre a sequence pelo
`pg_get_serial_sequence` do próprio `DEFAULT` (nunca por nome fixo) e exige
`has_sequence_privilege(..., 'USAGE')`. Ela roda **antes** do advisory lock e
do backup, então uma credencial insuficiente nem disputa a chave. Ausência de
`USAGE` → **exit 2 sanitizado**, sem backup e sem mutação.

#### Privilégios mínimos da credencial de escrita

```sql
GRANT CONNECT ON DATABASE <db> TO <role>;
GRANT USAGE ON SCHEMA marts TO <role>;
GRANT SELECT, INSERT, DELETE ON marts.fact_shopee_product_monthly TO <role>;
GRANT CREATE ON SCHEMA marts TO <role>;          -- só para o backup
GRANT USAGE ON SEQUENCE marts.fact_shopee_product_monthly_id_seq TO <role>;
```

**Nunca:** `UPDATE`, `TRUNCATE`, `REFERENCES`, superuser, `createdb`,
`createrole`, `replication`, `bypassrls`, nem membership em outra role.

#### Maturidade: o denominador do local é outro

O índice operacional de maturação divide o GMV de Produtos pelo GMV da diária.
A `fact_marketplace_daily_performance` **local** não tem os mesmos dados de
maio que a do Neon: medido no local, o índice de maio dá **0,9988 (apice)** e
**0,9979 (barbours)**, contra 1,0782 e 1,0758 no Neon.

Consequência operacional: os índices esperados após a correção (1,0361 e
1,0266) foram derivados da **diária do Neon** e só valem lá.

- **No local**, validar: contagens, chaves, delta de GMV, backup e **conteúdo**
  (incluindo `variation_name` como `NULL`, não `"NaN"`).
- **No Neon**, validar tudo isso **mais** o selo `mature` e o índice final.

O selo `mature` final é validado **somente no Neon** — é ele que a Torre lê.

#### Por que os testes não pegaram antes

O `FakeConn` respondia a qualquer parâmetro e a qualquer consulta de
privilégio. Um dublê mais permissivo que o original não é dublê: é um teste que
mente. Agora o fake modela a sequence (`pg_get_serial_sequence` +
`has_sequence_privilege`) e **guarda os parâmetros reais do `INSERT`** — as
asserções de normalização são sobre o que o **driver recebe**, não sobre a
staging. Contraprovas medidas: desligando a normalização, 2 testes falham;
desligando a porta 6b, 3 falham.

### Proposta de backfill — restrita a Ápice/maio e Barbours/maio

**Não executada.** Escopo derivado da deduplicação fail-closed do Gate
SH-API-2A-R e reconciliado no Gate SH-API-2C-R3 (candidato × local × Neon, com
paridade perfeita entre os dois destinos):

| Escopo | Δ GMV | Δ chaves | Efeito no índice |
|---|---|---|---|
| `apice` / 2026-05 | −23.292,43 | 0 | 1,0782 → 1,0361 (segue madura) |
| `barbours` / 2026-05 | −80.987,03 | 0 | 1,0758 → 1,0266 (segue madura) |

Ambos são **reduções**: removem duplicação de snapshot sobreposto, não
acrescentam venda. Nenhuma chave nova, nenhuma chave removida. Os dois seguem
acima de 1,00 depois da correção, então o backfill não muda o veredito de
regime — o que é o resultado esperado para uma remoção de duplicata.

Deliberadamente **fora** desta proposta:

- `kokeshi` / 2026-08 (+3.311,08, +39 chaves) e `rituaria` / 2026-07
  (+45.279,42, +2 chaves): as duas competências estão medidas como
  **materialmente imaturas**. Backfillar um mês que ainda vai mudar sozinho
  troca um número provisório por outro número provisório e consome a janela de
  revisão sem reduzir risco.
- Qualquer competência a partir de 2026-08: `load_behind_daily` ou
  `load_absent`. O passo correto ali é **re-executar a carga**, não corrigir
  retroativamente uma carga que nem chegou.

Pré-condições para executar, todas já implementadas e nenhuma satisfeita hoje:

1. `apps/api/etl/backfill_shopee_products.py --dry-run --target local` e
   `--target neon`, ambos com identidade comprovada e reconciliação idêntica.
2. Os dois XLSX fora do padrão em `shopee/barbours/` retirados da pasta (a
   triagem fail-closed aborta a marca inteira enquanto eles estiverem lá).
3. Credencial de escrita explícita — `--apply` segue **bloqueado** em `main()`,
   retornando `EXIT_VALIDATION_REFUSED` antes de qualquer I/O.

Depois do backfill, o selo é a verificação: os dois escopos devem permanecer
`mature` e `definitive`. Se qualquer um cair abaixo do limiar, o backfill não é
o que se esperava e deve ser revertido pela tabela de backup durável.

## Caminho de escrita do backfill Shopee — habilitado tecnicamente, NÃO executado (Gate SH-API-2E1)

O comando existe, todas as portas estão implementadas e testadas, e `--apply`
**continua bloqueado** na barreira final. Nada foi escrito, nenhuma conexão
gravável foi aberta, nenhum backup real foi criado.

### Comando futuro (exemplo)

```bash
# 1. Consentimento e credenciais DEDICADAS de escrita (nunca as read-only,
#    nunca DATABASE_URL). Fora do .env versionado.
export I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES=1
export BACKFILL_LOCAL_RW_URL=...        # host tem de ser localhost
export BACKFILL_NEON_RW_URL=...         # host tem de ser remoto, com SSL
export BACKFILL_LOCAL_EXPECT_DB=...     # nome do banco esperado
export BACKFILL_NEON_EXPECT_DB=...

# 2. LOCAL primeiro, sempre. Os dois --scope são obrigatórios.
python -m etl.backfill_shopee_products --apply --target local \
    --scope apice:2026-05 --scope barbours:2026-05

# 3. Só depois do local commitado e validado, o Neon.
python -m etl.backfill_shopee_products --apply --target neon \
    --scope apice:2026-05 --scope barbours:2026-05
```

Exit codes: `0` ok · `2` recusado na validação (nada escrito) · `3` rollback
confirmado · `4` **indeterminado** (exige inspeção humana) · `5` erro de uso.

### As dez portas

Cada uma é fail-closed e **independente** — nenhuma infere outra. "Passou na
identidade" não implica "é primary"; "é primary" não implica "tem SSL". Um
preflight que deduz uma condição a partir de outra mente quando o ambiente muda.

| # | Porta | Bloqueia quando |
|---|---|---|
| 1 | allowlist exata | falta um `--scope`, sobra um, repete, ou não é o par autorizado |
| 2 | consentimento | `I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES` ≠ `1` |
| 3 | credencial dedicada | `BACKFILL_*_RW_URL` ausente, igual à read-only, ou com classe de host errada |
| 4 | destino confirmado | `--target` ausente — o destino nunca é inferido |
| 5 | identidade do banco | `EXPECT_DB` não bate, ou a tabela não existe |
| 6 | primary gravável | `pg_is_in_recovery()`, `transaction_read_only=on`, ou sem privilégio real |
| 7 | SSL no Neon | `connection.info.ssl_in_use` falso |
| 8 | advisory lock | outra execução em curso — falha na hora, **não espera** |
| 9 | backup durável | não criado, não conferido ou não commitado |
| 10 | expectativa medida | o delta de hoje diverge do medido |

Privilégio é **consultado** (`has_table_privilege`), nunca testado com DML: um
`INSERT` de mentira para "ver se dá" já é a escrita que o preflight existe para
evitar. Há teste que reprova qualquer DML no preflight.

### Allowlist

Autorizado, e só: **`apice:2026-05`** e **`barbours:2026-05`**.

Recusado com o motivo medido junto:

| Escopo | Motivo |
|---|---|
| `rituaria:2026-07` | competência materialmente imatura — trocaria um número provisório por outro |
| `kokeshi:2026-08` | zero pedido concluído e carga atrás da diária — o passo certo é recarregar |
| qualquer outro | fora da allowlist |
| execução sem escopo | recusada |
| execução ampla (marca inteira) | recusada |
| **subconjunto** (só `apice`) | recusado — metade do par não fecha a reconciliação total |

### Expectativa medida (contrato de verificação, não alvo)

| Escopo | Δ GMV esperado |
|---|---|
| `apice` / 2026-05 | −23.292,43 |
| `barbours` / 2026-05 | −80.987,03 |
| **total** | **−104.279,46** |

Mais: **zero** chave adicionada, **zero** removida, e o índice operacional de
maturação continua acima de 0,99 depois da correção (1,0782 → 1,0361 e
1,0758 → 1,0266).

Divergência **bloqueia** — os valores nunca são forçados. Se o delta de hoje
não reproduz o medido, a premissa mudou (arquivo novo, arquivo retirado, regra
de dedup alterada) e ninguém mediu o novo efeito. Tolerância de R$ 0,01, só
para ruído de arredondamento.

### Backup durável

Um backup **por destino e por execução**, com nome determinístico
`marts.fact_shopee_product_monthly_bkp_<destino>_<YYYYMMDD_HHMMSS>`, validado
contra regex antes de entrar na DDL (identificador nunca é interpolado sem
validação; carimbo fora do formato é recusado).

Contrato:

1. **Somente as duas competências autorizadas** — o mesmo predicado
   parametrizado do `DELETE`. O backup nunca cobre menos do que a mutação apaga.
2. **Colunas explícitas**, jamais `SELECT *`. Os dois destinos têm schemas
   diferentes (14 colunas no local, 15 no Neon) e `SELECT *` produziria backups
   de formatos distintos, impossíveis de comparar.
3. **Contagem e checksum** conferidos contra a origem antes de qualquer
   `DELETE`: `md5(string_agg(...))` sobre as colunas explícitas em ordem
   determinística, com separador `chr(31)` para não fundir valores adjacentes.
   Divergência aborta e desfaz.
4. **Commitado em transação própria, antes da mutação.** Antes o backup vivia
   na mesma transação do `DELETE` — se ela caísse, o backup caía junto, e não
   protegia de nada. Agora, se a mutação morrer ou o processo for morto, a
   tabela de backup já está no disco.
5. **Retenção: 90 dias** (`BACKUP_RETENTION_DAYS`). Remoção manual e explícita
   depois disso; nenhuma limpeza automática.

`delete_scope` recusa rodar sem backup **commitado** — não basta ter sido
criado.

### `ingested_at`

Existe **somente no Neon**. Significa instante de **publicação/republicação do
mart** — nunca a data do dado na fonte. Republicar um mês fechado move esse
carimbo sem que nenhuma venda tenha mudado; é exatamente o que a Torre mostra
como "publicado no mart em ...".

- **Não aparece no SQL do local**: a coluna não existe lá, e um SQL que a
  mencionasse falharia no meio da transação, depois do `DELETE`. Há trava de
  regressão (`assert_no_ingested_at_in_local_sql`).
- **No Neon é preenchido pelo banco** (`NOW()`), nunca por valor vindo da
  staging: o carimbo tem de ser o instante real da publicação naquele destino,
  e a staging não sabe disso.

### Estados parciais — sem encenar atomicidade distribuída

Local e Neon são **dois bancos, duas transações independentes**. Não existe
transação distribuída aqui e o módulo não finge que existe.

| Estado | O usuário vê | Ação correta |
|---|---|---|
| **LOCAL_OK_NEON_FALHOU** | a Torre continua servindo o dado **antigo** (a Torre lê o Neon) | repetir **somente** a propagação local → Neon, com novo backup no Neon; nunca reprocessar XLSX |
| **NEON_OK_LOCAL_FALHOU** | a Torre mostraria dado que a fonte local não tem — divergência silenciosa | **proibido por contrato** (a ordem impede). Se ocorrer, é bug de orquestração: restaurar o Neon pelo backup e investigar |
| **COMMIT_INDETERMINADO** | indeterminado até inspeção | **não repetir**. Inspecionar o destino contra o backup (contagem e checksum) e só então decidir |

### Zero retry

Nenhuma etapa tem retry automático. O caso ambíguo (`EXIT_INDETERMINATE`) é
justamente aquele em que não se sabe se a escrita foi aplicada — repetir
poderia duplicar o efeito. A decisão de repetir é humana, depois de inspecionar
destino e backup. Há teste que conta as chamadas e reprova qualquer etapa
executada mais de uma vez.

### Pré-condições operacionais ainda não satisfeitas

1. Os dois XLSX fora do padrão em `shopee/barbours/` precisam sair da pasta —
   a triagem fail-closed aborta a marca inteira enquanto estiverem lá.
2. As credenciais `BACKFILL_*_RW_URL` não existem; precisam ser provisionadas
   como role dedicada de escrita.
3. `--apply` continua bloqueado no código: habilitar é decisão de outro gate.

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
| 2026-07-23 | Gate C2 (rodada manual observada de `full_daily` via `run_task.ps1`, pós-sync regional): sete steps `SUCCESS` (`daily_ml`, `daily_tiktok`, `gold_regional_incremental`, `sync_region_if_needed` `NO_OP`/paridade 37.282 linhas, `sync_produtos_ml`, `sync_produtos_tiktok`, `health_check`), `STATUS GERAL: OK`, `ok_critical=true` (`ok=false` só pelos alertas Shopee não-críticos já conhecidos), zero steps Shopee, lock liberado, logs preservados. Timeout externo (124) da ferramenta de acompanhamento não é o exit code do pipeline — o subprocesso terminou sozinho e liberou o lock normalmente. Dois avisos não-bloqueantes registrados como dívida: `UnicodeEncodeError` do logger (cp1252 vs. "→") em `daily_ml`/`daily_tiktok`, e o aviso já conhecido do TikTok sobre pedidos com `order_status` nulo/fora da allowlist (regras já reconciliadas no Gate R2). Task Scheduler `mktplace_full_daily` reconfirmado **Disabled** nesta rodada via `schtasks`/`Get-ScheduledTask` (não presumido); horário 06:00 segue como hipótese não confirmada. GO apenas para revisar/preparar a ativação do Scheduler — não autoriza habilitar a task. Nenhuma execução real de `full_daily`/`health_check`/`shopee_manual_refresh`, correção de código, alteração do Scheduler ou commit/push neste gate. |
| 2026-07-24 | Gate C3 (primeira execução agendada real do `full_daily`, task habilitada no Gate anterior): disparo automático pelo Task Scheduler às 06:00:01, `LastTaskResult=0`, sem nenhuma intervenção manual. Sete steps `SUCCESS` (`daily_ml`, `daily_tiktok`, `gold_regional_incremental`, `sync_region_if_needed` — desta vez com sync real 37.282→37.851 linhas e backup, não `NO_OP` como no Gate C2 —, `sync_produtos_ml`, `sync_produtos_tiktok`, `health_check`), `STATUS GERAL: OK`, `ok_critical=true`, zero steps Shopee, lock liberado, logs preservados. Mesmos dois avisos não-bloqueantes do Gate C2 (`UnicodeEncodeError` do logger, aviso TikTok de status nulo/fora da allowlist), sem nova investigação. Task Scheduler segue **Habilitado**, próxima execução 25/07 06:00. Ciclo de fechamento operacional e automação **encerrado** com este gate. Nenhuma alteração de código/task/horário/credenciais/`.env` nesta rodada — só observação read-only. |
| 2026-07-16 | Gate C1 (retry do Gate B6.1c revela achado operacional, não bug): execução real de `full_daily` (~18min) falhou por `daily_shopee_orders` estourar seu timeout de 900s num arquivo Shopee grande — causa raiz era rodar ingestão **manual** (Shopee só muda com upload de export novo) todo dia dentro de um pipeline de cadência **automática diária**, não um timeout pequeno demais. `orchestrate.py::PIPELINES` separado em dois pipelines independentes: `full_daily` (ml/tiktok/regional/produtos ml-tiktok/health_check, orçamento 7200s→3600s) e `shopee_manual_refresh` (novo, manual, nunca agendado: Shopee orders/stats/ads críticos + produtos Shopee + Bug 8 + health_check, orçamento 3780s). `health_check.py::EXPECTED_SOURCES` — `shopee_daily`/`shopee-stats_daily`/`shopee-ads_daily` (execução) marcados `critical=False`, mesmo padrão do Gate B4 para `shopee_product_monthly`, evitando que `ok_critical` reprove só por Shopee ter saído da cadência diária. `run_task.ps1` ganha `TaskKey "shopee_manual_refresh"` reaproveitando o mesmo `Invoke-ResolvedTask`/lock/timeout/log, com lock separado. `schedule_plan.py` só atualiza comentários de orçamento (7200s→3600s); `EXTERNAL_LOCK_TIMEOUT_SECONDS`/`TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS` (9000s/9600s) e a lógica de criação/ativação **não foram alterados**; `PROPOSED_SCHEDULE` continua com 1 única tarefa (`full_daily`), nenhuma tarefa Shopee é proposta. Scheduler segue **Disabled** até o Gate C3 (depois do Gate C2 — rodar `full_daily` sem Shopee via `run_task.ps1`, observado estável). 1.427→1.451 testes pytest (+24) e 13→19 testes Pester em `run_task.tests.ps1` (+6), todos passando. Nenhuma execução real de `full_daily`/`shopee_manual_refresh`/banco/scheduler/commit/deploy neste gate. |
| 2026-09-08 | Gate SH-API-2A-R (implementação fail-closed da deduplicação de Produtos Shopee): `apps/api/etl/load_shopee_products.py` ganha `ID do pedido` no `COL_MAP` (obrigatório), `_classify_order_file`/`_plan_brand_snapshots`/`_select_current_snapshot` e a regra de snapshot vigente por pedido aplicada ANTES do filtro `status == "Concluído"`. Quatro formatos de nome aceitos; instante de export, `Order.toship`, sufixo `(1)`, parte ausente/duplicada e janela ambígua **abortam a carga inteira** antes de qualquer leitura de arquivo ou conexão. Ordenação `(janela_fim, janela_início)` DESC, validada contra `max(file_id)` do Data Mart em 13.720/13.720 pedidos sobrepostos. mtime/ctime/ordem do glob nunca usados. 38 testes novos (incl. contraprova dos R$ 971.946,52 que a ordenação por nome removeria); suíte `etl/tests` 135→174 passando, zero falhas; `apps/api/tests` com as MESMAS 45 falhas pré-existentes por node ID (zero novas). Deduplicação **não aplicada em produção**: nenhum backfill, nenhuma escrita em banco, nenhuma migration, nenhum commit/push. Sidecar fora de escopo; `units_sold` da fato diária pendente. Triagem aborta hoje em `barbours` por 2 arquivos fora do padrão — ação do operador documentada na seção nova. |
| 2026-09-08 | Gate SH-API-2D (contrato de qualidade de escopo dos Produtos Shopee): seis eixos ortogonais (`source_status`, `load_status`, `eligibility_status`, `maturity_status`, `coverage_status`, `loaded_at`) derivados de tres tabelas que **ja existiam** no Neon — `marts.fact_shopee_product_monthly`, `marts.fact_marketplace_daily_performance` e `audit.source_sync_run`. **Sem migration, sem escrita em banco, sem backfill.** Piso de maturidade MEDIDO em 30 pares marca x competencia (maduros 1,0047–1,1234; imaturos 0,6643–0,7700; sem conclusao 0,0000) e exposto como parametro `Settings.shopee_maturity_floor` (default 0,99), nunca literal em API/tela/MCP — ha teste que reprova a reintroducao de `0.99`/`2026-07`/`2026-08`. Propagado a 5 superficies: `/produtos/shopee`, `/produtos/shopee/summary` (que **nunca** preenchia `refreshed_at`), `/quality`, tela de Produtos (faixa antes dos cards A/B/C/D), tela de Qualidade, `torre_produtos_prioritarios` e `torre_qualidade_dados` (campo estruturado + limitacoes derivadas do backend + aviso no resumo textual). Tres achados corrigidos pela validacao contra o Neon real: cobertura da fonte e HISTORICA (a ultima execucao cobre so' 07..08/2026 e reprovava todos os meses fechados); sem carga a maturidade e' `maturity_unknown`, nunca `materially_immature`; `source_unknown` nao bloqueia um mes com maturidade medida. Validacao: 38 testes pytest novos + 19 `node --test` novos; `apps/api/tests` **1095 passed / 0 failed** com `.env` carregado (as 43 falhas do worktree sao ausencia de `.env`, node IDs identicos a baseline em 9a81cc1); `etl/tests` 257 passed; web 1478/1478; `tsc --noEmit` com **zero** erro novo. Backfill proposto e NAO executado, restrito a `apice`/2026-05 (−23.292,43) e `barbours`/2026-05 (−80.987,03); `kokeshi`/2026-08 e `rituaria`/2026-07 excluidos por imaturidade medida. |
| 2026-09-08 | Gate SH-API-2D-R/V (correcao semantica, QA e integracao linear): a primeira versao usava nomes que afirmavam mais do que o contrato media. `source_covered` -> **`source_ever_loaded`** (o eixo responde "ja foi carregada alguma vez?", nunca "a fonte esta em dia"); `load_current` -> **`load_present`** ("current" e afirmacao temporal, e o mart publicado em 05/08 aparecia como `load_current` para julho 34 dias depois); `load_stale` -> **`load_behind_daily`** (declara a evidencia medida em vez de um adjetivo temporal). `completed_share` -> **`maturation_index`** e `maturity_floor` -> **`maturation_threshold`**: a razao NAO e percentual de conclusao, share nem completude — numerador (subtotal de item do mart) e denominador (GMV liquido do shop stats) sao populacoes diferentes, valores > 1 sao o regime normal de mes fechado e NUNCA sao truncados. Novo campo obrigatorio `maturation_index_note` acompanha o numero em toda superficie; a faixa da tela nem le o indice diretamente, para nao poder exibi-lo como "%". Entrada impossivel (GMV negativo, NaN) levanta `ScopeQualityInputError` e degrada para `maturity_unknown` com aviso critico `shopee_produtos_indice_invalido` — sem a guarda, `NaN >= limiar` e False e o mes viraria "imaturo", um veredito inventado a partir de lixo. `loaded_at` explicitado como publicacao NO MART, com os dois relogios nomeados por extenso na tela; nenhum titulo usa "atual"/"atualizado"/"em dia" (ha teste que reprova). Os tres `text-[11px]` novos viraram `text-xs` (piso de 12px). Integracao LINEAR: worktree limpa sobre origin/main 6b9bb94 + cherry-pick de a18cea5 sem conflito (22/22 blobs identicos), correcoes em commit separado — sem merge no branch antigo. Zero backfill, zero escrita em banco, zero migration. |
| 2026-09-09 | Gate SH-API-2E3-H2 (fecha os dois defeitos do apply real; NADA executado): (1) o `NaN` do pandas NAO vira NULL — o psycopg2 o adapta para `'NaN'::float` e o Postgres ACEITA num varchar, gravando a STRING "NaN". Na staging de maio eram 153 de 278 linhas em `variation_name` e 3 em `avg_price`; teria corrompido 55% do escopo sem nenhuma das dez portas perceber, porque contagem, chaves e GMV fecham. Novo `normalize_records` (None/NaN float e NumPy/NaT/pd.NA/Decimal NaN -> None) no ultimo ponto antes do driver, mais `assert_bind_params_clean` como rede. Preservados 0, 0.0, False, string vazia, a string literal "NaN", Decimal valido, timestamps e int NumPy; +inf/-inf RECUSADOS (nao sao ausencia, sao numero fora de faixa). Zero `pd.isna()` indiscriminado — sobre container ele devolve vetor e quebra num `if`. (2) INSERT na tabela nao implica USAGE na SEQUENCE: `id` tem `DEFAULT nextval(...)` nos dois destinos, e o INSERT falhou por permissao DEPOIS do backup commitado e do DELETE. Nova porta 6b descobre a sequence pelo `pg_get_serial_sequence` do proprio DEFAULT (nunca por nome fixo) e exige `has_sequence_privilege('USAGE')` ANTES do lock e do backup -> exit 2 sanitizado, sem backup e sem mutacao. Privilegios minimos documentados no modulo, incluindo `GRANT USAGE ON SEQUENCE`. Registrado tambem que a diaria LOCAL tem denominador diferente (indice de maio 0,9988/0,9979 no local contra 1,0782/1,0758 no Neon): o local valida contagens/chaves/delta/backup/CONTEUDO e o selo `mature` final e validado SOMENTE no Neon. Fakes deixaram de mascarar: modelam a sequence e guardam os parametros REAIS do INSERT. Contraprovas: sem a normalizacao 2 testes falham; sem a porta 6b, 3 falham. 26 testes novos; etl/tests 392 -> 418. Backup local de 20260909 preservado e intocado. Zero --apply, zero conexao gravavel, zero role, zero backup novo, zero escrita, zero migration, zero dependencia. |
| 2026-09-09 | Gate SH-API-2E3-H1 (corrige o defeito que impedia o --apply real; NADA executado): no SQLAlchemy 2.0 o primeiro `conn.execute()` faz AUTOBEGIN, entao os SELECTs das portas 5-8 deixavam a conexao em transacao e o `begin()` explicito do executor levantava `InvalidRequestError` — o apply real do Gate SH-API-2E4 morreu com exit 2 sem criar backup e sem escrever nada (fail-closed funcionou). Novo helper `close_preflight_transaction(conn)`, chamado DEPOIS do advisory lock e ANTES de entregar a conexao ao executor: faz rollback se houver transacao implicita e confirma que fechou; se persistir, levanta `PreflightTransactionError` -> exit 2 sanitizado, sem backup e sem mutacao, com lock/conexao/engine liberados no finally. A ordem e deliberada: o advisory lock e de SESSAO e sobrevive ao rollback. O executor NAO reaproveita transacao implicita — segue dono de duas transacoes explicitas (backup e publicacao). Causa da falha escapar aos 43 testes do gate anterior: o `FakeConn` tinha `begin()` permissivo (mesma armadilha do `cursor_factory`); agora modela autobegin e levanta em begin aninhado, com prova adicional numa `Connection` REAL do SQLAlchemy (SQLite de memoria, zero dependencia). Contraprova: desligando o helper, 11 testes falham. 15 testes novos; etl/tests 377 -> 392. Formulas, SQL, allowlist, escopos, deltas, exit codes e maturidade INALTERADOS. Zero --apply, zero conexao gravavel, zero role, zero backup, zero escrita, zero migration, zero dependencia. |
| 2026-09-08 | Gate SH-API-2E1 (prepara o backfill maduro de maio; NADA executado): `--apply` habilitado TECNICAMENTE e ainda BLOQUEADO na barreira final. Dez portas fail-closed e independentes (allowlist exata, consentimento, credencial dedicada de escrita, destino confirmado, identidade, primary gravavel, SSL no Neon, advisory lock de sessao sem espera, backup commitado, expectativa medida). Allowlist EXATA `apice:2026-05` + `barbours:2026-05` — subconjunto tambem e' recusado, porque metade do par nao fecha a reconciliacao total; `rituaria:2026-07` e `kokeshi:2026-08` recusados COM o motivo medido na mensagem. Backup redesenhado: transacao PROPRIA commitada ANTES da mutacao (antes caia junto com o rollback e nao protegia de nada), colunas explicitas por destino (nunca `SELECT *`, os schemas tem 14 x 15 colunas), nome deterministico validado por regex antes de entrar na DDL, contagem + checksum md5 conferidos contra a origem, retencao de 90 dias. `ingested_at` tratado explicitamente: so' existe no Neon, e' instante de PUBLICACAO (nunca data da fonte), preenchido por NOW() do banco e com trava de regressao que reprova sua presenca em SQL do local. Tres estados parciais nomeados com acao definida, sem encenar atomicidade distribuida; ZERO retry, com teste que conta chamadas. Expectativa medida vira TRAVA: -23.292,43 + -80.987,03 = -104.279,46, zero chave adicionada/removida, maturacao acima de 0,99 — divergencia bloqueia, valores nunca sao forcados. 77 testes novos; etl/tests 257 -> 334. Zero escrita, zero conexao gravavel, zero backup real, zero migration, zero deploy. |
