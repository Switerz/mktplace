# Torre de Controle de Marketplaces â€” GoBeautÃ©

Sistema interno de acompanhamento de performance comercial, operacional e financeira das lojas GoBeauté nos marketplaces TikTok Shop, Mercado Livre e Shopee.

## Status atual: Sprint Shopee - backfill local executado em 2026-06-23

## Lojas monitoradas

| Brand (interno) | TikTok | ML | Shopee |
|---|---|---|---|
| apice | âœ… | âŒ | âœ… |
| azbuy | âœ… | âŒ | âŒ |
| barbours | âœ… | âœ… | âœ… |
| gocase | âœ… | âŒ | âŒ |
| kokeshi | âœ… | âœ… | âœ… |
| lescent | âœ… | âœ… | âœ… |
| rituaria | âœ… | âŒ | âœ… |

## Fontes de dados

- **TikTok Shop**: Data Mart PostgreSQL â€” schemas `raw`, `api`, `gold` â€” ~1,9M pedidos (jun/2025â€“hoje)
- **Mercado Livre**: Data Mart PostgreSQL â€” schemas `raw`, `api`, `gold` â€” ~219K pedidos (abr/2025â€“hoje)
- **Shopee**: exports locais XLSX/CSV em `shopee/`, ingeridos no PostgreSQL local (`mktplace_control`) em `marts.fact_marketplace_daily_performance`


## Arquitetura de dados atual

- `DATABASE_URL`: PostgreSQL local no PC, usado para escrita e leitura dos dados tratados da Shopee.
- `DATAMART_DATABASE_URL`: Data Mart PostgreSQL remoto, read-only, usado para TikTok/ML via schemas `gold` e `raw`.
- A API foi preparada para consultar ambos: queries `gold/raw` usam o Data Mart; queries `marts` usam o banco local.
- Em 2026-06-23, o Data Mart remoto precisa ter a URL recolocada em `DATAMART_DATABASE_URL`; o valor local foi removido para evitar leitura falsa.

### Postgres local portatil

O Postgres local esta em `.local/postgres16` e os dados em `.local/pgdata`. Para subir/verificar:

```powershell
.\scripts\start_local_postgres.ps1
.\scripts\check_shopee_local.ps1
```

Backfill Shopee executado:

```powershell
uv run --no-project --with openpyxl --with pydantic-settings --with sqlalchemy --with psycopg2-binary python -m pipelines.ingestion.daily_performance --source shopee --mode backfill --days 180
uv run --no-project --with openpyxl --with pydantic-settings --with sqlalchemy --with psycopg2-binary python -m pipelines.ingestion.daily_performance --source shopee-stats --mode backfill --days 180
uv run --no-project --with openpyxl --with pydantic-settings --with sqlalchemy --with psycopg2-binary python -m pipelines.ingestion.daily_performance --source shopee-ads --mode backfill --days 180
```

## Stack

- **Backend**: Python + FastAPI + SQLAlchemy + Alembic + PostgreSQL
- **Pipelines**: Python + SQL (leitura das gold tables existentes)
- **Frontend**: Next.js + TypeScript + Tailwind + shadcn/ui + Recharts

## DocumentaÃ§Ã£o

- [docs/architecture.md](docs/architecture.md) â€” arquitetura e decisÃµes tÃ©cnicas
- [docs/source_mapping.md](docs/source_mapping.md) â€” mapeamento detalhado das fontes
- [docs/kpi_dictionary.md](docs/kpi_dictionary.md) â€” dicionÃ¡rio de KPIs
- [docs/backlog.md](docs/backlog.md) â€” backlog e sprints

## Setup local

```bash
cp .env.example .env
# preencha .env com as credenciais

# Backend
cd apps/api
pip install -e .
uvicorn app.main:app --reload

# Frontend
cd apps/web
npm install
npm run dev
```

## SeguranÃ§a

- Nunca commite `.env`, credenciais ou dumps de dados
- Todos os segredos via variÃ¡veis de ambiente
- Logs nÃ£o devem expor tokens ou CPFs



## Deploy na Vercel

Este repositorio esta preparado para deploy do frontend Next.js na Vercel.

Configuracao recomendada ao importar `Switerz/mktplace`:

- Framework Preset: `Next.js`
- Root Directory: `apps/web`
- Install Command: `npm ci`
- Build Command: `npm run build`
- Output Directory: manter o padrao do Next.js

Variaveis de ambiente necessarias no projeto da Vercel:

- `NEXT_PUBLIC_API_URL`: URL publica do backend FastAPI.

Observacao: o backend FastAPI e os pipelines Python nao ficam hospedados automaticamente na Vercel por esta configuracao. A Vercel ira publicar o app Next.js em `apps/web`; o backend precisa estar disponivel em outro host e informado em `NEXT_PUBLIC_API_URL`.
