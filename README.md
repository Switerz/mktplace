# Torre de Controle de Marketplaces â€” GoBeautÃ©

Sistema interno de acompanhamento de performance comercial, operacional e financeira das lojas GoBeauté nos marketplaces TikTok Shop, Mercado Livre e Shopee.

## Status atual

O andamento das frentes, entregas recentes, bloqueios e próximos marcos é mantido em [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md). Esse documento é o ponto de entrada para o status geral; runbooks e análises continuam como fonte dos detalhes técnicos.

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

- `DATABASE_URL`: **Neon** (serverless PostgreSQL) — camada canônica `marts.*` consumida pelo dashboard (overview, brands, monthly, daily, canais, financeiro, quality, pedidos, produtos/ml, produtos/tiktok, produtos/shopee).
- `DATAMART_DATABASE_URL`: **RDS AWS** (Data Mart), somente leitura, fonte de verdade de TikTok/ML via schemas `gold`/`raw`. Requer VPN a partir da máquina local; endpoints que ainda leem `gold.*` diretamente (tempo-real, brand-detail, inteligência, operações) dependem disso estar acessível.
- **PostgreSQL local** (`mktplace_control`, porta 5432): usado apenas como staging da Shopee — `etl/load_shopee_products.py` carrega os exports XLSX/CSV de `shopee/` para lá, e `pipelines/sync_produtos.py` copia (upsert) para o Neon. Não é lido diretamente pela API.
- Os dados chegam ao Neon através de dois pipelines que precisam ser executados/agendados manualmente: `pipelines/ingestion/daily_performance.py` (fato diário principal) e `pipelines/sync_produtos.py` (tabelas de Produtos). Ver `docs/runbook_sync_produtos.md` para o agendamento proposto.

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

- [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — status geral, roadmap e próximas prioridades
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

### Publicacao atual (producao)

- URL canonica (endereco operacional da Torre): `https://mktplace-gobeaute.vercel.app`
- `https://mktplace-blond.vercel.app` e apenas um alias que redireciona (HTTP 307) para o dominio canonico.
- As URLs efemeras de deployment da Vercel (padrao `mktplace-<hash>-...vercel.app`) NAO sao o endereco da Torre: alem de serem descartaveis, ficam atras do SSO da Vercel e nao constam na allowlist de CORS do backend, o que faz a Torre cair em modo demonstracao/offline nelas. Use sempre o dominio canonico.
- `NEXT_PUBLIC_API_URL` deve apontar para o backend publico (FastAPI em `mktplace-api.onrender.com`), nunca para `localhost`/IP local.
- O CORS do backend precisa autorizar o dominio canonico (`Access-Control-Allow-Origin: https://mktplace-gobeaute.vercel.app`); sem isso o frontend publicado nao consegue consumir a API e exibe o fallback offline.
