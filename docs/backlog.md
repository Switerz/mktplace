# Backlog â€” Torre de Controle de Marketplaces GoBeautÃ©

Atualizado em: 2026-06-23

---

## Sprint 0 â€” Discovery e setup âœ… CONCLUÃDA

**PerÃ­odo**: 2026-06-16

### EntregÃ¡veis concluÃ­dos
- [x] Inspecionar repositÃ³rio
- [x] Explorar Data Mart via MCP Metabase
- [x] Identificar tabelas TikTok (raw + gold)
- [x] Identificar tabelas Mercado Livre (raw + gold)
- [x] Mapear brands/lojas disponÃ­veis por marketplace
- [x] Levantar contagens, perÃ­odos e schemas
- [x] Criar estrutura de diretÃ³rios do projeto
- [x] Criar `.env.example`
- [x] Criar `docs/architecture.md`
- [x] Criar `docs/source_mapping.md`
- [x] Criar `docs/kpi_dictionary.md`
- [x] Criar `docs/backlog.md`
- [x] Criar `README.md`

### Achados chave
- Data Mart jÃ¡ tem pipeline madura com 3 camadas: raw, api, gold
- TikTok: 1,9M pedidos, 7 brands, gold table com 68 KPIs prÃ©-calculados
- ML: 219K pedidos, 3 brands, gold table com 37 KPIs prÃ©-calculados
- 2 brands no TikTok nÃ£o estÃ£o no XLSX de metas: `azbuy` e `gocase`
- MVP pode usar as gold tables diretamente sem reprocessar raw

### Perguntas em aberto (para Sprint 1)
- [ ] O que sÃ£o `azbuy` e `gocase` no TikTok? Parte do grupo GoBeautÃ©?
- [x] Por que `apice` e `rituaria` nÃ£o estÃ£o no ML? **Respondido em 2026-07-01**: `apice` confirmado sem dados na fonte; `rituaria` tinha dados reais desde 2025-12-28 mas estava excluÃ­da por whitelist hardcoded desatualizada â€” corrigido, ver seÃ§Ã£o "2026-07-01" abaixo e `docs/architecture.md`.
- [ ] Qual a diferenÃ§a entre schema `raw` e `api` no Data Mart?
- [ ] Como serÃ£o importadas as metas do XLSX? Processo manual ou planilha conectada?
- [ ] Credenciais de acesso ao Data Mart (read-only) estÃ£o disponÃ­veis?
- [ ] Existe jÃ¡ algum processo de alertas se o Data Mart parar de atualizar?

---

## Sprint 1 â€” Data contracts e modelo canÃ´nico âœ… CONCLUÃDA

**PerÃ­odo**: 2026-06-16

### EntregÃ¡veis concluÃ­dos
- [x] Criar `docs/data_contracts.md` com entidades canÃ´nicas, ERD Mermaid, mapeamento de status
- [x] Definir entidades: dim_empresa, dim_loja, dim_marketplace, dim_seller_account, dim_calendario, dim_status_pedido, fact_marketplace_daily_performance, fact_goal_monthly, audit.source_sync_run
- [x] Mapear brands no escopo â†’ seed `db/seeds/02_empresas_lojas.sql`
- [x] Mapear todos os status de TikTok (8) e ML (4) â†’ canÃ´nico â†’ seed `db/seeds/03_status_canonico.sql`
- [x] Tabela de disponibilidade de mÃ©tricas por marketplace em `docs/kpi_dictionary.md`
- [x] ERD em Mermaid em `docs/data_contracts.md`
- [x] Definir campos obrigatÃ³rios vs opcionais

### DecisÃµes tomadas
- `azbuy` e `gocase` fora do escopo â€” pipeline deve filtrar via `WHERE brand IN (SELECT brand_key FROM marts.dim_loja)`
- `apice` sem ML (confirmado), `rituaria` pendente de populaÃ§Ã£o no ML (baixa prioridade) **[SUPERADO em 2026-07-01: `rituaria` jÃ¡ tinha dados reais no ML desde 2025-12-28 â€” nÃ£o era "pendente de populaÃ§Ã£o", era gap de whitelist. Incluida no escopo, ver seÃ§Ã£o "2026-07-01" abaixo]**
- Schema `api` ignorado â€” usar apenas `raw` e `gold`
- `fact_marketplace_daily_performance` Ã© a tabela-fato principal do MVP (nÃ£o `fact_order`)
- `fact_order` e `fact_order_item` ficam para fase 2 (Sprint 4+)

---

## Sprint 2 â€” Banco, schemas e migrations âœ… CONCLUÃDA

**PerÃ­odo**: 2026-06-16

### EntregÃ¡veis concluÃ­dos
- [x] FastAPI app mÃ­nimo com healthcheck (`apps/api/app/main.py`)
- [x] ConfiguraÃ§Ã£o via pydantic-settings (`apps/api/app/config.py`)
- [x] SQLAlchemy + conexÃ£o com check (`apps/api/app/database.py`)
- [x] Alembic configurado (`apps/api/alembic.ini`, `alembic/env.py`)
- [x] Migration 001: cria schemas (raw, staging, marts, app, audit)
- [x] Migration 002: cria dimensÃµes + dim_calendario populada (2024â€“2027)
- [x] Migration 003: cria fact_marketplace_daily_performance, fact_goal_monthly, audit.source_sync_run, audit.data_quality_check + Ã­ndices
- [x] Seeds prontos: 01_marketplaces.sql, 02_empresas_lojas.sql, 03_status_canonico.sql
- [x] Script setup_local.sh (cria banco + roda migrations + seeds)
- [x] Script reset_dev.sh (destrÃ³i e recria do zero)
- [x] pipelines/common: config.py, db.py (conexÃµes Data Mart + local), logging.py

### Como rodar localmente
```bash
# 1. Instalar dependÃªncias
cd apps/api && pip install -e .

# 2. Configurar .env (copiar .env.example e preencher DATABASE_URL)

# 3. Criar banco e rodar migrations
bash db/scripts/setup_local.sh

# 4. Subir API
uvicorn app.main:app --reload --port 8000

# 5. Testar healthcheck
curl http://localhost:8000/health
```

---

## Sprint 3 â€” Profiling aprofundado das bases âœ… CONCLUÃDA

**PerÃ­odo**: 2026-06-16

### EntregÃ¡veis concluÃ­dos
- [x] Queries de profiling executadas (nulos, duplicidades, gaps de visitors)
- [x] Documentado em `docs/data_profiling_tiktok_ml.md`
- [x] Queries de referÃªncia salvas em `db/sql/marts/profiling_queries.sql`
- [x] Cobertura validada por brand e mÃªs (TikTok: out/25â€“jun/26; ML: abr/25â€“jun/26)

### Achados principais
- TikTok: 0 nulos em GMV/pedidos; `visitors` ausente em ~83% dos dias â€” nÃ£o exibir conversÃ£o TikTok
- ML: 0 nulos em todos os campos crÃ­ticos; `lescent` com GMV=0 em jun/2026 â€” monitorar
- Zero duplicatas em ambas as tabelas
- barbours domina TikTok (~78% do GMV total) e ML (~60%)

---

## Sprint 4 â€” IngestÃ£o TikTok + ML â†’ modelo canÃ´nico âœ… CONCLUÃDA

**PerÃ­odo**: 2026-06-16

### EntregÃ¡veis concluÃ­dos
- [x] `pipelines/connectors/tiktok/connector.py` â€” lÃª `gold.tiktok_brand_daily`, filtra brands, modos incremental e backfill
- [x] `pipelines/connectors/mercadolivre/connector.py` â€” lÃª `gold.ml_gestao_diaria`, modos incremental e backfill
- [x] `pipelines/transforms/tiktok_brand_daily.py` â€” mapeia gold â†’ fact_marketplace_daily_performance (NULLIF em visitors)
- [x] `pipelines/transforms/ml_gestao_diaria.py` â€” mapeia gold ML â†’ canonical
- [x] `pipelines/quality/checks.py` â€” 7 checks (GMVâ‰¥0, data vÃ¡lida, chaves obrigatÃ³rias, loja_id, marketplace_id, ordersâ‰¥0, sem duplicatas)
- [x] `pipelines/ingestion/daily_performance.py` â€” orquestraÃ§Ã£o completa: fetch â†’ transform â†’ quality â†’ upsert + audit

### Como usar
```bash
# Sync incremental (Ãºltimos 3 dias)
python -m pipelines.ingestion.daily_performance --source tiktok --mode incremental
python -m pipelines.ingestion.daily_performance --source ml --mode incremental

# Backfill histÃ³rico (Ãºltimos 90 dias)
python -m pipelines.ingestion.daily_performance --source tiktok --mode backfill --days 90
python -m pipelines.ingestion.daily_performance --source ml --mode backfill --days 180
```

### DecisÃµes de design
- Upsert via `ON CONFLICT (date, loja_id, marketplace_id) DO UPDATE` â€” idempotente
- Falhas em checks crÃ­ticos abortam carga mas registram run em `audit.source_sync_run` como 'failed'
- `visitors` TikTok: `NULLIF(visitors, 0)` no conector â†’ nunca zero, sempre NULL quando ausente
- Brand mapping hardcoded no transform (espelha seeds) para evitar join extra a cada linha

---

## Sprints 5â€“16 â€” Ver prompt original

Detalhamento em `docs/backlog_sprints_5_16.md` (a criar conforme avanÃ§amos).

---

## Sprint Neon Migration — Migrar endpoints principais de RDS para Neon ✅ CONCLUÍDA

**Período**: 2026-06-24

### Contexto

A API tinha dois backends de dados com nomes contraintuitivos:

| Variável `.env` | Engine no código | Host real | Schemas |
|---|---|---|---|
| `DATABASE_URL` | `engine` / `get_db()` | **Neon** | `marts.*`, `raw.*` |
| `DATAMART_DATABASE_URL` | `datamart_engine` | **RDS** (exige VPN no Render) | `gold.*` |

Os endpoints do dashboard principal dependiam do RDS via `gold_service.py`. A Shopee já estava no Neon. Objetivo: mover os 8 endpoints de dashboard para `marts.*` no Neon.

### Entregáveis concluídos

- [x] Reescrever `apps/api/app/services/performance_service.py` com 8 funções completas para Neon:
  - [x] `get_overview` — KPIs mensais TikTok + ML + Shopee, MoM, ROAS, cancel rate, unique buyers
  - [x] `get_brands` — ranking por marca com GMV por canal, cos_pct, ml_roas, cancel rate, MoM
  - [x] `get_monthly` — série mensal de GMV por marca (todos os canais incluindo Shopee)
  - [x] `get_daily` — série diária por marca e canal (Shopee incluída)
  - [x] `get_canais` — mix TikTok (video/live/card), buyers ML, buyers/funil Shopee
  - [x] `get_financeiro` — settlement, taxas, ads, frete por canal e por marca
  - [x] `get_quality` — cancel%, not-delivered%, avg_delivery_days, retorno Shopee
  - [x] `get_pedidos` — pedidos diários TikTok + ML com breakdown por marca
- [x] Atualizar `apps/api/app/routers/performance.py`: 8 endpoints usam `perf_svc.*` (Neon); restantes permanecem em `svc.*` (RDS)
- [x] `health-datasource` atualizado: `active_source: neon_marts`

### O que permanece em gold_service (RDS)

| Endpoint | Razão |
|---|---|
| `/tempo-real` | `gold.tiktok_shop_hourly` — sem tabela horária no Neon |
| `/brand-detail` | `gold.tiktok_brand_daily` — `total_views`, `active_videos` para GPM |
| `/produtos/ml`, `/produtos/ml/summary` | `gold.ml_produto_ranking` |
| `/produtos/tiktok` | `gold.tiktok_product_daily` |
| `/inteligencia`, `/operacoes` | Lógica específica de `gold.*` |

### Limitações conhecidas

- `gpm` (TikTok) retorna sempre `None` — requer `total_views`, ausente no mart
- `ml_unique_buyers` é soma diária (sobrestima; gold deduplicava via `ml_gestao_mensal`)
- `ml_not_delivered_rate_pct` usa proxy `orders - delivered_orders` (não shipments reais)
- `visitors` TikTok em `/canais` pode ser nulo em ~83% dos dias (confirmado no profiling Sprint 3)

### Decisão de denominador — cancel rate

Todos os cálculos de cancel rate usam `canceled / (paid + canceled)` como denominador (definição padrão e-commerce). Excepção documentada: TikTok em `/pedidos` retorna `None` quando `canceled_orders = 0` no mart (ausência de cobertura, não zero real).

### Validação (2026-06-24)

**Sintaxe:** `py -m py_compile` nos dois ficheiros → OK

**Execução contra Neon real** (`apps/api/.venv/Scripts/python.exe`):

| Endpoint | Schema Pydantic | Nota |
|---|---|---|
| `/overview` | ✅ | gmv=5,810,691 · mom=-4.09% |
| `/brands` | ✅ | top=KOKESHI · labels ÁPICE/RITUÁRIA corretos |
| `/monthly` | ✅ | 6 meses retornados |
| `/daily` | ✅ | 26 dias para barbours |
| `/canais` | ✅ | `tiktok_conversion_rate=None` (corrigido de 108.7%) · `shopee=2.04%` |
| `/financeiro` | ✅ | retorna estrutura correcta |
| `/quality` | ✅ | `tiktok_cancel_rate=None` · `shopee_cancel=13.84%` |
| `/pedidos` | ✅ | retorna estrutura correcta |

**Estado actual do Neon:** apenas dados Shopee (marketplace_id=3) populados no mart para mai/2026. TikTok e ML requerem execução do pipeline com acesso ao RDS via VPN — não é bug de código. Os campos populam-se correctamente quando o pipeline correr.

---

## Sprint Auditoria Financeiro ✅ CONCLUÍDA

**Período**: 2026-06-26

### Contexto

Auditoria da aba Financeiro: leitura de todos os arquivos relevantes, validação das queries no Neon para Mai/2026, identificação de bugs visuais e correção.

### Achados principais

- `total_fees` tem sinal diferente por canal: TikTok negativo (backend aplica `abs()`), Shopee positivo (correto sem abs)
- `shopee_fees` positivos no BD implicam que Taxa % e Liq. % sao independentes e podem somar >100% — não são complementares
- ML não tem `total_settlement` nem `total_fees` (ambos NULL) — comissao ML ausente no mart
- kokeshi Shopee: `settlement / gmv = 100,6%` — possível timing de pagamento entre meses

### Correções aplicadas

- [x] Removida coluna `Composição` da tabela TikTok (`SettlementBar` substituída por `Liq. %` numérico)
- [x] Removida coluna `Composição` da tabela Shopee (idem)
- [x] Removido componente `SettlementBar` sem uso
- [x] Legenda do rodapé TikTok corrigida (sem referência à barra removida)
- [x] Legenda do rodapé Shopee corrigida (esclarece que Taxa % e Liq. % são independentes)
- [x] Criado `docs/sections/financeiro_audit.md`

### Pendências de dados

- Trazer comissão ML (`total_fees`) para o mart — campo NULL para mkt=2 em todo o histórico
- Investigar settlement Shopee > 100% (kokeshi mai/2026) — possível problema de competência
- Discriminar fees TikTok (comissão plataforma vs. afiliados)
- Documentar convenção de sinal de `total_fees` no data contract

---

## Sprint Auditoria Produtos ✅ CONCLUÍDA

**Período**: 2026-06-26

### Contexto

Auditoria da aba Produtos: leitura de arquivos, validação de tabelas no Neon, identificação de bugs e correções.

### Achados principais

- `marts.fact_shopee_product_monthly` **não existe no Neon de produção** — tab Shopee retorna 500, UI exibe "API offline" silenciosamente
- SQL injection em `action_signal` (parâmetro sem whitelist, interpolado diretamente no SQL de `gold.ml_produto_ranking`)
- ML Produtos lê `gold.ml_produto_ranking` sem filtro de período — usuário não sabe a qual mês o ranking se refere
- TikTok e ML dependem de RDS via `DATAMART_DATABASE_URL` — falham se VPN/RDS indisponível no Render

### Correções aplicadas

- [x] Adicionado `VALID_ML_ACTION_SIGNALS` com 6 valores permitidos em `apps/api/app/routers/performance.py`
- [x] Validação de `action_signal` antes de chamar `get_produtos_ml` (HTTPException 422 para valor inválido)
- [x] Criado `docs/sections/produtos_audit.md`

### Pendências de dados

- **Migrar `marts.fact_shopee_product_monthly` para o Neon** — tabela existe só no banco local; bloqueia tab Shopee em produção
- Expor data de atualização do ranking ML na UI
- Validar denominador de `problem_rate` TikTok (se `orders` no gold inclui ou exclui cancelados)
- Documentar thresholds de `ad_efficiency` e `revenue_velocity` em `docs/kpi_dictionary.md`

---

## Sprint Regularização Neon (TikTok/ML/Shopee) e correção de dados ✅ CONCLUÍDA

**Período**: 2026-07-01

### Contexto

Diagnóstico e regularização da alimentação do Neon (`fact_marketplace_daily_performance` e tabelas de Produtos), fechamento seguro da migração de Produtos, correção transacional da causa raiz da data futura em `fact_shopee_product_monthly`, e inclusão aprovada de `rituaria` no escopo de ML. Ver `docs/sections/produtos_audit.md` (Bugs 1–5) para o detalhamento completo.

### Diagnóstico confirmado (somente leitura)

- RDS `gold.tiktok_brand_daily`: até 2026-06-29 · `gold.ml_gestao_diaria`: até 2026-07-01 · `gold.ml_produto_ranking`: 1.581 linhas · `gold.tiktok_product_daily`: até 2026-06-29
- Neon `fact_marketplace_daily_performance`: TikTok até 2026-06-21 (890 linhas), ML até 2026-06-23 (539), Shopee até 2026-06-20 (851) — confirma atraso de ~8-10 dias, sem cargas desde 23-24/06
- `audit.source_sync_run`: últimas execuções em 2026-06-23 (Shopee) e 2026-06-24 (TikTok/ML) — pipelines existem mas não estão agendados
- Zero duplicidade, zero nulos em chaves obrigatórias e zero datas futuras em `fact_marketplace_daily_performance` (grain íntegro)
- `fact_shopee_product_monthly`: 2.206 de 5.228 linhas (42%) com `ref_month` em jul–dez/2026 (impossível) — ver Bug 3
- **Novo achado**: `rituaria` tem R$ 8.027.817,35 de GMV real em `gold.ml_gestao_diaria` desde 2025-12-28, mas está excluída de todo o pipeline/API por whitelist hardcoded desatualizada — ver Bug 4

### Causa raiz confirmada — Bug 3 (data futura Shopee)

`apps/api/etl/load_shopee_products.py` usava `pd.to_datetime(order_date, dayfirst=True)` em datas já em formato ISO (`YYYY-MM-DD HH:MM`, confirmado em 85/85 arquivos `.xlsx`). `dayfirst=True` inverte dia/mês mesmo em ISO quando o dia de origem é ≤12, espalhando ~40% dos pedidos de qualquer mês real para os 12 meses do calendário. Corrigido no código (`format="%Y-%m-%d %H:%M"` explícito); dado no PG local/Neon ainda não corrigido — requer re-execução do ETL + `sync_produtos.py --full` (aprovação pendente).

### Correções de código

- [x] `apps/api/etl/load_shopee_products.py` — corrigido parsing de data (causa raiz do Bug 3)
- [x] `pipelines/sync_produtos.py` — auditoria em `audit.source_sync_run` (start/finish por fonte), rollback explícito em falha, validação de origem≠destino, guarda de queda suspeita de linhas (ML/TikTok), brands lidas de `marts.dim_loja` com fallback, exit code não-zero em falha, correção de bug de carregamento de `.env` (constantes de conexão eram lidas antes do `load_dotenv()`)
- [x] `pipelines/reconciliation/check_sources_vs_neon.py` (novo) — reconciliação somente leitura fonte vs Neon + checks de integridade
- [x] `pipelines/reconciliation/fix_shopee_product_dates.py` (novo) — correção transacional com backup/staging/validação cruzada
- [x] `rituaria` incluída no escopo ML em `pipelines/connectors/mercadolivre/connector.py`, `gold_service.py`, `performance_service.py`, `routers/performance.py`, `apps/web/app/produtos/page.tsx`
- [x] `scripts/run_with_lock.ps1` (novo) — guarda de concorrência para o Task Scheduler
- [x] Testes: `pipelines/tests/`, `apps/api/etl/tests/`, `apps/api/tests/` (40 testes, todos passando)

### Correções de dados executadas em 2026-07-01 (aprovadas explicitamente)

- [x] Backfill ML (RDS→Neon, 758 linhas, 2025-12-23→2026-07-01) — inclui `rituaria`
- [x] Incremental TikTok (RDS→Neon, 70 linhas, 2026-06-16→2026-06-29)
- [x] Sync Produtos ML (1.486 linhas, era 1.326) e TikTok (173.920 linhas) — inclui 156 produtos de `rituaria`
- [x] Correção transacional de `fact_shopee_product_monthly` (local + Neon): backup timestamped, staging reprocessada dos 85 arquivos-fonte, validação cruzada contra `fact_marketplace_daily_performance` (diff 0,04%), substituição — GMV de R$ 8.773.954,36 para R$ 21.174.272,80, 0 `ref_month` futuro (era 2.206 linhas/42%)
- [x] Descoberto e corrigido durante a correção acima: colisão de `variation_name` na chave única causava perda silenciosa de até 36,6% do GMV de uma marca (Bug 5) — corrigido somando em vez de sobrescrever
- [x] Nenhuma escrita no Data Mart/RDS em nenhum momento — todas as conexões usadas foram somente leitura contra `DATAMART_DATABASE_URL`

### Pendências remanescentes

- [ ] Consolidar as 4 constantes de whitelist ML duplicadas em uma única fonte de verdade
- [ ] Ativar o Windows Task Scheduler (comandos preparados em `docs/runbook_sync_produtos.md`, não ativados — requer nova autorização)
- [ ] Avaliar adicionar `variation_name` à chave única de `fact_shopee_product_monthly` (Bug 5) se a granularidade por variação for necessária

---

## Backlog tÃ©cnico (nÃ£o priorizado)

- Loader de metas a partir do XLSX
- Alertas de anomalia (queda de GMV, fonte parada)
- Integração Shopee via exports locais: orders, shop-stats e ads em andamento
- Admin de mapeamentos
- Deploy Docker + CI/CD
- Forecast e projeÃ§Ãµes

---

## Riscos identificados

| Risco | Probabilidade | Impacto | MitigaÃ§Ã£o |
|---|---|---|---|
| Data Mart parar de atualizar sem aviso | MÃ©dia | Alto | Monitorar `updated_at`, alertar se > 24h sem refresh |
| Gold tables mudarem de schema sem comunicaÃ§Ã£o | Baixa | Alto | Documentar contratos e criar testes de schema |
| Brands `azbuy`/`gocase` fora do escopo de negÃ³cio | MÃ©dia | MÃ©dio | Confirmar com stakeholder antes de Sprint 1 |
| Metas do XLSX divergirem dos dados reais | Alta | MÃ©dio | Criar reconciliation report desde Sprint 7 |
| Acesso read-only ao Data Mart indisponÃ­vel | Baixa | CrÃ­tico | Garantir credenciais antes da Sprint 4 |

---

## Sprint Shopee — Integração via exports locais 🚧 EM ANDAMENTO

**Período**: 2026-06-23

### Entregáveis em andamento
- [x] Estruturar conector Shopee para `Order.all*.xlsx`, shop-stats e ads CSV.
- [x] Incluir brands `apice`, `barbours`, `kokeshi`, `lescent`, `rituaria` no escopo Shopee.
- [x] Mapear Shopee para `marts.fact_marketplace_daily_performance` com `marketplace_id = 3`.
- [x] Declarar `openpyxl` como dependência Python para leitura XLSX.
- [x] Ativar Shopee em `dim_marketplace`.
- [x] Rodar backfill completo em banco local.
- [ ] Validar totais mensais por brand contra os exports originais/Seller Center.
- [x] Expor Shopee em todos os painéis além dos endpoints centrais.
  - [x] `get_canais` → visitantes + conversão Shopee (KPI cards + colunas na tabela)
  - [x] `get_quality` → cancel% + devolução% Shopee (KPI cards + colunas + seção por marca)
  - [x] `get_financeiro` → GMV, settlement, taxas, ROAS, frete Shopee (KPI cards + tabela liquidação)
  - [x] Interfaces TypeScript atualizadas (`FinanceiroKpis/BrandRow`, `QualityKpis/BrandRow`, `CanaisKpis/BrandRow`)
  - [x] `showShopee` adicionado em todas as páginas; filtro "Shopee" isola corretamente cada canal

### Fontes atuais
- Pedidos: `shopee/{brand}/Order.all*.xlsx`.
- Funil: `shopee/{brand}/*.shopee-shop-stats.*.xlsx`.
- Ads: `shopee/{brand}/Dados*.csv`.


### Status executado em 2026-06-23
- PostgreSQL portatil local criado em `.local/postgres16` com dados em `.local/pgdata`.
- Banco `mktplace_control` criado em localhost:5432.
- Migrations/seeds aplicados manualmente via SQLAlchemy/psql.
- Backfill Shopee carregado: orders (755), shop-stats (755), ads (851).
- Scripts adicionados: `scripts/start_local_postgres.ps1` e `scripts/check_shopee_local.ps1`.
- Pendencia: recolocar a URL remota do Data Mart em `DATAMART_DATABASE_URL` para restaurar TikTok/ML na API hibrida.

### Caveats
- Ads Shopee são média diária do período do CSV, não série diária real.
- Kokeshi foi adicionada ao backfill local e deve passar pela mesma reconciliação das demais marcas.
- API oficial Shopee continua como evolução futura; o MVP usa exports manuais.

---

## Roadmap de fases controladas (2026-07-02)

Execução dividida em fases isoladas para não misturar UI, ETL, banco e integrações financeiras na mesma entrega. Cada fase abaixo só é executada mediante autorização explícita e nunca inclui commit/push/deploy/escrita em banco por conta própria.

### Fase 1 — Fechar Produtos ✅ CONCLUÍDA (nesta sessão, sem commit)

**Período**: 2026-07-02

A maior parte do escopo já havia sido implementada e commitada em `062880b` (chave estrita Shopee `(ref_month, brand, sku_ref_key, product_name)`, estados assíncronos por canal, testes de reconciliação). Esta sessão fechou as pendências residuais:

- [x] Corrigido o único comentário remanescente que ainda descrevia a chave Shopee com 5 campos (`apps/api/tests/test_performance_service_produtos.py`)
- [x] `test_cardinalidade_do_join_por_marca_e_mes` (`test_shopee_sku_consolidation.py`) passou a checar também: soma do GMV dos buckets = GMV elegível, e "maior produto sempre no bucket A" — por marca×mês, contra o Neon real
- [x] Confirmado por leitura de código: `variation_name` nunca entra em `JOIN ... USING`, nunca elimina linhas
- [x] Estados assíncronos (loading/vazio/offline/troca de filtro/resposta obsoleta) revisados em `async-channel-state.ts` + `ProductTableShell.tsx` — já corretos, sem necessidade de mudança
- [x] `pytest` (testes de Produtos), `npm test`, `npx tsc --noEmit`, `npm run build`, `compileall`, `git diff --check` — todos passando
- [x] QA visual via Playwright (1280/768/375px, 3 marketplaces) — ver resultado na seção de auditoria
- **Sem escrita em banco, sem commit, sem push** — apenas 2 arquivos de teste + esta documentação alterados

Ver `docs/sections/produtos_audit.md` (entrada C16) para o detalhamento técnico.

### Fase 2 — Bug 8 Shopee (cancelamentos subcontados) — ✅ CONCLUÍDA (2026-07-02/03)

**Bug**: `apps/api/etl/load_shopee_products.py` (`_aggregate`) usava `left` merge a partir de pedidos completados — grupos com *somente* pedidos cancelados eram descartados, subestimando `canceled_orders`/`cancel_rate_pct` em 84 pedidos. GMV/units/completed nunca foram afetados.

**Executada em gates com aprovação explícita por etapa** (detalhamento completo em `docs/sections/produtos_audit.md`, Bug 8):

| Gate | Entrega | Commit |
|---|---|---|
| 1 | Fix do merge (`left`→`outer`) + 4 testes de regressão do ETL | `7bd0981` |
| 2 | Backup + staging LOCAIS reconciliados (25 combinações, diff zero em GMV/units/completed, +84 cancelados) | `819ded1` |
| 3 | Swap transacional da tabela real LOCAL | `654153e` |
| 4A.1 | Diagnóstico read-only do Neon (idêntico ao pré-fix) | `7a5b6c3` |
| 4A.2 | Backup + staging criados no NEON com revalidação sob lock | `54780d7` |
| 4B | Swap da tabela real do NEON — **COMMIT em produção** | `ccd93fa` |

**Resultado final (local + Neon)**: 2.471 linhas (+40, todas GMV zero), 53.599 cancelamentos (+84), GMV R$ 21.174.272,80 / unidades / concluídos / Pareto inalterados. QA de encerramento em 2026-07-03: Neon 17/17 checks, API pública 25/25 combinações, reconciliação contra os 85 XLSX com diff zero.

**Monitor pós-carga**: `python -m pipelines.reconciliation.monitor_bug8_invariants` (read-only, invariantes — rodar após cada carga futura do ETL Shopee).

**Retenção**: backups/stagings (local e Neon) preservados até 1 carga real posterior validada + 7 dias; remoção só com autorização explícita.

### Fase 3A — Preparação auditável da operação e frescor — 🔧 REVISADA 2x (2026-07-03), AGUARDANDO NOVA APROVAÇÃO, NÃO ATIVADA

Auditoria completa das 7 cargas (comando, deps, VPN, lock, idempotência — tabela em `docs/runbook_sync_produtos.md`), decisão documentada de que o notebook+VPN é aceitável **como solução provisória** (com limitações explícitas), e endurecimento operacional. Duas rodadas de reprovação em revisão até aqui:

- **Revisão 1** reprovou a 1ª versão por 10 problemas (preflight não amarrado à execução real, working directory não garantido, agenda Shopee incompleta, aspas inválidas no `schtasks /tr`, health check com fontes silenciosamente ausentes, corrida no lock, loader Shopee sem trava de host, credenciais hardcoded em `preflight.py`, documentação duplicada).
- **Revisão 2** reprovou a versão corrigida da Revisão 1 por mais 7 problemas: `last_run_failed` não reprovava o status geral sozinho (só o threshold de frescor); os 3 checks de arquivo Shopee usavam o mesmo glob (não distinguiam orders/stats/ads) com uma whitelist de marcas própria; 2 tarefas em horários separados não garantiam que a primeira tivesse terminado antes da segunda começar; nenhum timeout individual por step (uma fonte travada podia consumir o timeout global inteiro); `schtasks /create` simples não representa `StartWhenAvailable`/`MultipleInstancesPolicy`/`ExecutionTimeLimit`, e o horário 06:00 não tinha justificativa verificada; `Stop-Process` no timeout não aguardava confirmação real de término antes de liberar o lock, e `LockName` não era validado contra path traversal; a documentação afirmava garantias que o código de 2 tarefas não sustentava.

Versão corrigida pela segunda vez:

- `scripts/run_with_lock.ps1` — lock **atômico** por criação exclusiva de arquivo (`FileMode.CreateNew`), recuperação por **PID vivo/morto** (nunca por idade), `-WorkingDirectory` sempre explícito, timeout, logs separados, recusa de credenciais em argumento. **Novo na revisão 2**: após `Stop-Process` por timeout, aguarda até 30s a confirmação real de término (`Get-Process`) antes de liberar o lock; `LockName` validado contra `^[A-Za-z0-9_-]+$` antes de qualquer acesso a disco (path traversal).
- `pipelines/ops/orchestrate.py` — amarra preflight à execução real (bloqueado ⇒ comando nunca roda). **Reescrito na revisão 2**: um único pipeline `full_daily` (não mais 2 pipelines/tarefas separados) e **timeout individual por `Step`** (`subprocess.run(timeout=...)`, `TimeoutExpired` capturado ⇒ `FAILED`, orquestração segue para fontes independentes seguintes — soma dos timeouts internos = 6780s).
- `scripts/run_task.ps1` — wrapper `-TaskKey` que corrige o bug de aspas aninhadas no `schtasks /tr`. **Atualizado na revisão 2**: só conhece `full_daily`, com timeout externo de 9000s (margem de ~33% sobre os 6780s internos).
- `pipelines/ops/preflight.py` — `LOCAL_PG_URL` sem fallback, sessão read-only, `SHOPEE_DATA_PATH` nunca impresso. **Corrigido na revisão 2**: `check_local_pg` agora também bloqueia hosts fora do allowlist local; os checks de arquivo Shopee foram separados por padrão real (`Order.all*.xlsx` para orders, `*.shopee-shop-stats.*.xlsx` para stats, `Dados*.csv` para ads) contra a lista **oficial** de marcas (`pipelines.connectors.shopee.connector.BRANDS_IN_SCOPE`, sem whitelist duplicada), bloqueando a fonte inteira se qualquer marca oficial faltar o arquivo esperado (decisão documentada: evita carga parcial registrada como "success").
- `pipelines/ops/health_check.py` — lista explícita de fontes esperadas, frescor de dado contra threshold, `fact_shopee_product_monthly` sem falso positivo. **Corrigido na revisão 2**: `execution_stale`/`last_run_failed` agora são campos separados (falha na última execução sempre reprova o status geral, mesmo com sucesso recente dentro do threshold); data no futuro em qualquer tabela (inclusive `manual_monthly`) é sempre sinalizada como erro de qualidade, nunca "fresca" (regressão do Bug 3).
- `pipelines/ops/schedule_plan.py` — aspas corrigidas, `/f` nunca por padrão. **Reescrito na revisão 2**: agenda reduzida de 2 para **1 tarefa** (`full_daily`); gera também a definição **XML** do Task Scheduler como texto (`MultipleInstancesPolicy=IgnoreNew`, `StartWhenAvailable=true`, `ExecutionTimeLimit=PT2H30M`) porque `schtasks /create` simples não representa essas 3 configurações com segurança; horário 06:00 sinalizado explicitamente como hipótese não confirmada (sem telemetria de quando RDS/Shopee tipicamente atualizam).
- `apps/api/etl/load_shopee_products.py` — `LOCAL_PG_URL` sem fallback, allowlist de host, resolução lazy.
- 386 testes pytest + 22 testes Pester (lock atômico e concorrência real via `Start-Job`, espera pós-kill verificada via `Get-CimInstance Win32_Process`, `LockName` rejeitado antes de tocar disco, `WorkingDirectory` a partir de `System32`, timeout individual de uma fonte não trava as demais, health check comprovadamente último em 5 cenários mistos, XML bem formado, tokenização do `schtasks /tr` pelo parser real do PowerShell).

**Nenhuma tarefa foi criada no Task Scheduler.** Ativação é a Fase 3B, separada, condicionada a nova aprovação explícita desta revisão.

**Gate B1 (2026-07-15), antes da Fase 3B**: `orchestrate.py`/`health_check.py` ganharam política crítico/não-crítico — `Step.critical` (só `sync_produtos_shopee`=False, gap conhecido de `LOCAL_PG_URL`) e `ok_critical` separado de `ok` (Shopee manual defasado não decide mais o exit code sozinho). Status geral do pipeline passa a ser OK/DEGRADED/FAILED, exit 1 só em FAILED. Detalhe completo, testes (+22) e exemplos em `docs/runbook_sync_produtos.md` (seção "Gate B1"). Scheduler segue desativado; Gold regional ainda fora do `full_daily` (Gate B2).

**Próximo prompt sugerido**: *"Revise a Fase 3A corrigida pela segunda vez (diff, testes, agenda em docs/runbook_sync_produtos.md) e, se aprovada, execute a Fase 3B: confirme o horário com dado real e crie a tarefa real no Task Scheduler (XML ou schtasks /create) manualmente."*

### Fase 4 — Financeiro — 📋 PLANEJADA, NÃO EXECUTADA

Relatório mensal de faturamento ML, comissão ML com competência temporal (`total_fees` NULL para ML no mart hoje), relatório real de renda/repasse Shopee, reconciliação de statements TikTok com pedidos. Nenhuma escrita no Data Mart permitida.

**Próximo prompt sugerido**: *"Execute a Fase 4: planeje e implemente o relatório financeiro (faturamento ML, comissão ML por competência, renda Shopee, reconciliação TikTok), sem escrever no Data Mart."*

### Fase 5 — Seções legadas — 📋 PLANEJADA, NÃO EXECUTADA

Auditoria e refatoração de Tempo Real, Brand Detail, Inteligência, Operações — todas ainda dependentes de `gold_service.py`/RDS.

**Próximo prompt sugerido**: *"Execute a Fase 5: audite Tempo Real, Brand Detail, Inteligência e Operações nos mesmos moldes da auditoria de Produtos, sem migrar dados ainda."*

### Fase 6 — Release estável — 📋 PLANEJADA, NÃO EXECUTADA

Smoke test de todas as abas, filtros, períodos, ordenação, paginação, estados de loading/vazio/erro, frescor de dados, reconciliação com fontes, checklist de release.

**Próximo prompt sugerido**: *"Execute a Fase 6: rode o smoke test completo de todas as abas e monte o checklist de release, sem fazer deploy."*

