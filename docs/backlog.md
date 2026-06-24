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
- [ ] Por que `apice` e `rituaria` nÃ£o estÃ£o no ML?
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
- `apice` sem ML (confirmado), `rituaria` pendente de populaÃ§Ã£o no ML (baixa prioridade)
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

