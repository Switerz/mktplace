# Arquitetura â€” Torre de Controle de Marketplaces GoBeautÃ©

## VisÃ£o geral

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                     Fontes de Dados                         â”‚
â”‚  TikTok Shop API â”€â”€â–º Data Mart (raw/api/gold)               â”‚
â”‚  ML API          â”€â”€â–º Data Mart (raw/api/gold)               â”‚
â”‚  Shopee          â”€â”€â–º exports locais CSV/XLSX (API oficial futura)        â”‚
â”‚  XLSX Metas      â”€â”€â–º loader manual / seed                   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                            â”‚ leitura (read-only)
                            â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚              Pipelines (pipelines/)                         â”‚
â”‚  connectors/tiktok      â€” lÃª gold.tiktok_brand_daily, etc.  â”‚
â”‚  connectors/mercadolivre â€” lÃª gold.ml_gestao_diaria, etc.   â”‚
â”‚  transforms/            â€” normaliza para modelo canÃ´nico     â”‚
â”‚  quality/               â€” checks e scoring                  â”‚
â”‚  reconciliation/        â€” compara vs XLSX e fontes raw       â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                            â”‚ escreve
                            â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚           Banco Local (PostgreSQL â€” app prÃ³prio)            â”‚
â”‚  schema: raw       â€” dados brutos ingeridos                 â”‚
â”‚  schema: staging   â€” dados limpos e tipados                 â”‚
â”‚  schema: marts     â€” fatos e dimensÃµes canÃ´nicas            â”‚
â”‚  schema: app       â€” views otimizadas para API              â”‚
â”‚  schema: audit     â€” controle de sync e qualidade           â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                            â”‚
                            â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚   Backend (FastAPI)  â”‚â—„â”€â”€â”‚      Frontend (Next.js)          â”‚
â”‚   apps/api/          â”‚â”€â”€â–ºâ”‚      apps/web/                   â”‚
â”‚   REST API + OpenAPI â”‚   â”‚  /dashboard  /marketplaces       â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚  /lojas      /data-quality       â”‚
                           â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

## Data Mart existente (leitura)

O Data Mart da GoBeautÃ© (PostgreSQL 17.9, banco `Data Mart`, id=43) jÃ¡ possui pipeline de ingestÃ£o prÃ³prio e estrutura em camadas:

| Schema | PropÃ³sito |
|--------|-----------|
| `raw`  | Dados brutos vindos das APIs (TikTok, ML) â€” granularidade de pedido/item |
| `api`  | Espelho/view do raw, com pequenas transformaÃ§Ãµes â€” mesmo schema |
| `gold` | Tabelas analÃ­ticas agregadas (diÃ¡rio, mensal, por brand) |

**Esta torre NÃƒO altera o Data Mart.** Lemos apenas das tabelas `gold` (e ocasionalmente `raw` para reconciliaÃ§Ã£o).

## DecisÃµes tÃ©cnicas registradas

### 2026-06-16 â€” Usar gold tables como fonte primÃ¡ria
As tabelas `gold.tiktok_brand_daily` (68 colunas) e `gold.ml_gestao_diaria` (37 colunas) jÃ¡ computam a maioria dos KPIs necessÃ¡rios. Criar nova pipeline de transformaÃ§Ã£o sobre `raw` seria redundante no MVP. Risco: dependÃªncia da pipeline externa; mitigaÃ§Ã£o: monitorar `updated_at` e alertar se parar de atualizar.

### 2026-06-16 â€” azbuy e gocase fora do escopo
Confirmado pelo stakeholder: `azbuy` e `gocase` existem no Data Mart mas nÃ£o fazem parte do grupo GoBeautÃ© no contexto deste projeto. O pipeline deve sempre filtrar: `WHERE brand IN (SELECT brand_key FROM marts.dim_loja)`. Nunca hardcodar a lista de brands no cÃ³digo â€” usar a dim_loja como fonte de verdade.

### 2026-06-16 â€” RituÃ¡ria pendente no ML [SUPERADO em 2026-07-01 â€” ver decisÃ£o abaixo]
`rituaria` existe no TikTok mas o pipeline de ML ainda nÃ£o foi populado. Cadastrar a loja no seed com `ativo = true`, mas o pipeline simplesmente nÃ£o terÃ¡ dados para ela no ML por ora. Sem tratamento especial necessÃ¡rio â€” `null` nos campos ML.

### 2026-07-01 — Correção: rituaria incluída oficialmente no ML

A decisão de 2026-06-16 acima estava desatualizada. Diagnóstico confirmou que `gold.ml_gestao_diaria` (RDS) tem dados reais de `rituaria` desde 2025-12-28 (~R$8M GMV histórico) — antes até da data daquela decisão. `rituaria` foi adicionada a `BRANDS_IN_SCOPE`/`ML_BRANDS`/`_ML_BRANDS`/`VALID_ML_BRANDS` (connector, `gold_service.py`, `performance_service.py`, router) e ao filtro de marca ML em `apps/web/app/produtos/page.tsx`, seguida de backfill via `daily_performance.py --source ml --mode backfill` e sync de produtos ML. `apice` permanece fora do ML — confirmado sem nenhuma linha na fonte. Ver `docs/backlog.md` e `docs/sections/produtos_audit.md` (Bug 4).

### 2026-06-16 â€” Schema api ignorado
O schema `api` no Data Mart Ã© uma exposiÃ§Ã£o via postgres das mesmas tabelas do `raw`. NÃ£o usar â€” consumir sempre `raw` (dados brutos) e `gold` (agregados).

### 2026-06-16 â€” fact_marketplace_daily_performance como fato central do MVP
Em vez de construir `fact_order` (granularidade de pedido) do zero, o MVP usa as gold tables jÃ¡ existentes para popular `fact_marketplace_daily_performance` (granularidade dia Ã— loja Ã— marketplace). Isso reduz o tempo para ter dados visÃ­veis no frontend de semanas para dias. `fact_order` fica para Sprint 4+.

### 2026-06-16 â€” Brand como chave de loja
Tanto TikTok quanto ML usam `brand` (varchar) como identificador de loja. Os valores sÃ£o em minÃºsculas sem acento (ex: `apice`, `barbours`). Mapeamento para nome de exibiÃ§Ã£o e empresa serÃ¡ feito via seed/dim_loja.

### 2026-06-16 â€” Cobertura de brands por marketplace [PARCIALMENTE SUPERADO em 2026-07-01 â€” ver decisÃ£o acima sobre rituaria]
TikTok: 7 brands (apice, azbuy, barbours, gocase, kokeshi, lescent, rituaria)
ML (na Ã©poca): 3 brands (barbours, kokeshi, lescent) â€” hoje 4 brands, incluindo `rituaria` (ver 2026-07-01)
As brands `azbuy` e `gocase` nÃ£o estÃ£o no XLSX de metas â€” investigar.
~~As brands `apice` e `rituaria` nÃ£o tÃªm dados no ML â€” esperado ou gap?~~ Respondido em 2026-07-01: `rituaria` tem dados reais (gap de whitelist, corrigido); `apice` confirmado sem dados na fonte.

### 2026-06-23 — Shopee integrada via exports locais
A Shopee passa a entrar no escopo ativo por exports manuais locais em `shopee/{brand}`. Orders, shop-stats e ads são normalizados para `marts.fact_marketplace_daily_performance`. A API oficial Shopee Open Platform continua como evolução futura.


### 2026-06-23 — Migração do banco Shopee para Neon.tech
O banco de destino das transformações Shopee migrou de PostgreSQL local (`.local/postgres16`) para **Neon.tech** (serverless PostgreSQL, IPv4, free tier). TikTok e Mercado Livre continuam como fontes read-only no Data Mart remoto (`DATAMART_DATABASE_URL`).

- Projeto Neon: `mktplace-gobeaute` (região `us-west-2`)
- Host: `ep-lively-frost-a6eg1wh2.us-west-2.aws.neon.tech` / banco `neondb`
- `DATABASE_URL` no `.env` aponta para Neon com `sslmode=require`
- `DATAMART_DATABASE_URL` permanece inalterado no AWS RDS

Status após migração:
- Migrations 001/002/003 aplicadas no Neon via SQLAlchemy.
- Seeds (marketplaces, lojas, status canônico) carregados.
- Backfill Shopee completo no Neon — ver totais em `docs/source_mapping.md`.

Supabase foi avaliado como alternativa mas descartado: no plano free (Nano shared) o host direto é IPv6-only e o Supavisor retornou "tenant not found" a partir de IPv4. Neon aceita IPv4 nativamente sem configuração adicional.

## Estrutura de repositÃ³rio

```
marketplace-control-tower/
  apps/
    api/          â€” FastAPI backend
    web/          â€” Next.js frontend
  pipelines/
    common/       â€” config, logging, db utils
    connectors/
      tiktok/     â€” lÃª Data Mart gold tables TikTok
      mercadolivre/ â€” lÃª Data Mart gold tables ML
      shopee/     — conector de exports orders/shop-stats/ads
    ingestion/    â€” orquestraÃ§Ã£o de carga
    transforms/   â€” normalizaÃ§Ã£o para modelo canÃ´nico
    quality/      â€” checks de qualidade
    reconciliation/ â€” comparaÃ§Ã£o vs XLSX e fontes
  db/
    migrations/   â€” Alembic
    seeds/        â€” dados iniciais (lojas, marketplaces, status)
    sql/          â€” queries de referÃªncia por schema
  docs/           â€” documentaÃ§Ã£o
```


