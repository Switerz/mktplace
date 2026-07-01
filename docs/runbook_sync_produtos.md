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
> Há também um bug de dados conhecido em `fact_shopee_product_monthly`
> (`ref_month` incorreto em ~42% das linhas, causa raiz corrigida no código
> em `apps/api/etl/load_shopee_products.py` mas dado ainda não corrigido) —
> ver `docs/sections/produtos_audit.md` (Bug 3) antes de rodar `--full` para
> Shopee em produção.

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

Nem `daily_performance.py` nem `sync_produtos.py` implementam lock próprio hoje. Para Windows Task Scheduler, a forma mais simples de evitar duas execuções simultâneas da mesma fonte (ex.: um `--full` manual rodando ao mesmo tempo que o incremental agendado) é envolver cada chamada num wrapper PowerShell com lock file:

```powershell
# scripts/run_with_lock.ps1 — uso: run_with_lock.ps1 <nome-lock> <comando...>
param([string]$LockName, [Parameter(ValueFromRemainingArguments)]$Cmd)
$lockFile = "C:\Users\Notebook\Desktop\mktplace\logs\$LockName.lock"
if (Test-Path $lockFile) {
    Write-Error "Lock '$LockName' já existe — outra execução pode estar em andamento. Abortando."
    exit 1
}
New-Item -ItemType File -Path $lockFile -Force | Out-Null
try {
    & $Cmd[0] $Cmd[1..($Cmd.Length-1)]
    $exitCode = $LASTEXITCODE
} finally {
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}
exit $exitCode
```

O exit code do script Python (0 = sucesso, 1 = falha — `sync_produtos.py` já retorna isso via `sys.exit(main())`) propaga para o Task Scheduler, que marca "Last Run Result" ≠ 0 e permite configurar notificação/alerta externo.

### Criar as tarefas no Windows Task Scheduler

```powershell
# Salvar como setup_tasks.ps1 — executar como Administrador
# Pré-requisito: scripts/run_with_lock.ps1 (guarda de concorrência acima)

$python = "C:\Users\Notebook\Desktop\mktplace\apps\api\.venv\Scripts\python.exe"
$lock   = "C:\Users\Notebook\Desktop\mktplace\scripts\run_with_lock.ps1"
$logdir = "C:\Users\Notebook\Desktop\mktplace\logs"
New-Item -ItemType Directory -Force -Path $logdir | Out-Null

# --- daily_performance.py (fato principal) ---
schtasks /create /tn "mktplace_daily_ml"     /tr "powershell -File `"$lock`" daily_ml     $python -m pipelines.ingestion.daily_performance --source ml     --mode incremental >> `"$logdir\daily_ml.log`" 2>&1"     /sc daily /st 06:00 /f
schtasks /create /tn "mktplace_daily_tiktok" /tr "powershell -File `"$lock`" daily_tiktok $python -m pipelines.ingestion.daily_performance --source tiktok --mode incremental >> `"$logdir\daily_tiktok.log`" 2>&1" /sc daily /st 06:10 /f
schtasks /create /tn "mktplace_daily_shopee" /tr "powershell -File `"$lock`" daily_shopee $python -m pipelines.ingestion.daily_performance --source shopee --mode incremental >> `"$logdir\daily_shopee.log`" 2>&1" /sc daily /st 06:20 /f

# --- sync_produtos.py (tabelas de Produtos) — depois do daily correspondente ---
schtasks /create /tn "mktplace_sync_ml"     /tr "powershell -File `"$lock`" sync_ml     $python `"C:\Users\Notebook\Desktop\mktplace\pipelines\sync_produtos.py`" --source ml     >> `"$logdir\sync_ml.log`" 2>&1"     /sc daily /st 06:35 /f
schtasks /create /tn "mktplace_sync_tiktok" /tr "powershell -File `"$lock`" sync_tiktok $python `"C:\Users\Notebook\Desktop\mktplace\pipelines\sync_produtos.py`" --source tiktok >> `"$logdir\sync_tiktok.log`" 2>&1" /sc daily /st 06:45 /f
schtasks /create /tn "mktplace_sync_shopee" /tr "powershell -File `"$lock`" sync_shopee $python `"C:\Users\Notebook\Desktop\mktplace\pipelines\sync_produtos.py`" --source shopee >> `"$logdir\sync_shopee.log`" 2>&1" /sc daily /st 06:55 /f
```

**Nenhuma dessas tarefas foi criada no Task Scheduler** — os comandos acima são a proposta para revisão e ativação futura mediante nova autorização explícita. `scripts/run_with_lock.ps1` já foi criado e testado (lock cria/remove corretamente, exit code propagado — testado com `cmd /c exit 0` e `cmd /c exit 7`), mas `schtasks /create` não foi executado.

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

Saída esperada (atualizado após a correção de 2026-07-01 — ver `docs/sections/produtos_audit.md` Bug 3/Bug 5):
```
('shopee', 2431, datetime.date(2026, 5, 1))
('ml',     1486, datetime.date(2026, 7, 1))
('tiktok', 173920, datetime.date(2026, 6, 29))
```
Se `shopee` aparecer com `max_d` além do mês corrente ou contagem voltando a subir de forma inexplicada, investigar antes de assumir que é frescor normal — já houve um bug de parsing de data que inflava `ref_month` para meses futuros (Bug 3).

---

## Alertas de qualidade

Após cada sync, verificar:

1. **Shopee**: `COUNT` ≥ contagem do mês anterior — redução indica problema na fonte
2. **ML**: `COUNT` entre 1.200 e 1.500 — variação grande indica truncamento ou explosion na fonte
3. **TikTok**: `MAX(date)` ≥ D-2 — atraso indica falha silenciosa no incremental

---

## Histórico de decisões

| Data | Decisão |
|---|---|
| 2026-06-26 | Backfill inicial: Shopee=5.228, ML=1.326, TikTok=170.806 linhas |
| 2026-06-26 | `estimated_margin` alterado de `NUMERIC(8,4)` para `NUMERIC(18,2)` (valores até 811k) |
| 2026-06-26 | `problem_rate` definido como média ponderada por orders do campo diário da fonte |
| 2026-06-26 | ML dedup: 1.418 raw → 1.326 pós-dedup (92 pares duplicados na fonte, mantém maior `gross_revenue`) |
