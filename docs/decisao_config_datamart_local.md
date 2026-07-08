# Decisão pendente: fonte de configuração do Data Mart na API local

Status: **diagnóstico confirmado, correção não implementada** (aguardando decisão de produto/infra). Nenhuma alteração em `.env`.

## Causa confirmada

`apps/api/app/config.py` usa `SettingsConfigDict(env_file=".env")` — caminho resolvido **relativo ao cwd do processo Python**, não ao diretório do arquivo `config.py`.

- O `README.md` documenta iniciar a API via `cd apps/api && uvicorn app.main:app --reload` → cwd = `apps/api` → carrega `apps/api/.env`.
- `apps/api/.env` **não tem** a chave `DATAMART_DATABASE_URL` (nem `DATAMART_HOST`/`DATAMART_DB`/`DATAMART_USER`/`DATAMART_PASSWORD`).
- O `.env` da **raiz do repositório** tem `DATAMART_DATABASE_URL` configurada e funcional (confirmado nesta sessão: `SELECT 1 = 1` e `pg_is_in_recovery()` retornam corretamente quando a Settings é carregada a partir dele).
- `apps/api/app/database.py` cria `datamart_engine = _make_engine(settings.datamart_url)` **uma única vez, no import do módulo** — se `datamart_url` resolve vazio (caso `apps/api/.env`), `datamart_engine` fica `None` para toda a vida do processo.
- `apps/api/app/services/gold_service.py:_query` levanta `RuntimeError("Data Mart indisponivel: configure DATAMART_DATABASE_URL ou DATAMART_*.")` quando `datamart_engine is None` e a query toca `gold.*`/`raw.*`. Não há `exception_handler` para isso em `app/main.py` → FastAPI devolve **500** genérico.

**Consequência**: seguir literalmente o `README.md` (`cd apps/api && uvicorn ...`) quebra qualquer endpoint que dependa do Data Mart (parte de `/quality`, Tempo Real, Inteligência, Operações, Brand Detail) — não por falha de rede/credencial, mas porque o processo nunca viu a variável.

Nota lateral: `pipelines/common/config.py` (usado pelos scripts em `pipelines/`) tem a **mesma estrutura** (`env_file=".env"` relativo ao cwd), mas os scripts em `pipelines/` são documentados para rodar via `python -m pipelines.<modulo>` a partir da **raiz do repo** — por isso, na prática, eles sempre carregam o `.env` da raiz (que tem a credencial) e nunca exibiram este bug. É a mesma classe de problema, só que hoje "funciona por acaso" em um caso e falha no outro.

## Produção

Não investigado se produção é afetada — depende de como o processo é iniciado lá. Se a plataforma de deploy injeta `DATAMART_DATABASE_URL` como variável de ambiente real do processo (comum em PaaS/containers), produção **não é afetada**, porque `pydantic-settings` dá prioridade a variáveis de ambiente reais sobre o `.env` — o `.env` só é consultado para preencher o que não veio do ambiente. Não há Dockerfile/Procfile neste repositório para confirmar isso; verificar na configuração da plataforma de deploy antes de assumir.

## Decisão a tomar (nenhuma implementada nesta rodada)

| Opção | Descrição | Trade-off |
|---|---|---|
| **A. Fonte única no `.env` da raiz** | Apontar `SettingsConfigDict` (API e pipelines) para um caminho absoluto ancorado no repo, ex. `Path(__file__).resolve().parents[N] / ".env"`, eliminando a dependência do cwd | Uma só fonte de verdade; exige decidir se `apps/api/.env` é descontinuado ou passa a ser só para overrides locais |
| **B. `.env` próprio da API, completo** | Copiar/manter `DATAMART_DATABASE_URL` (e os demais `DATAMART_*`) também em `apps/api/.env`, mantendo dois arquivos sincronizados manualmente | Resolve o simples esquecimento, mas mantém dois arquivos que podem voltar a divergir silenciosamente (é exatamente o que já aconteceu) |
| **C. Variáveis de ambiente explícitas no processo** | Não depender de `.env` para `DATAMART_*` — exportar no ambiente do processo (shell, systemd, PaaS) antes de iniciar a API, em qualquer diretório | Mais robusto a mudança de cwd; exige documentar/automatizar a exportação para quem sobe a API localmente |

Nenhuma das três foi aplicada. Recomendação (não vinculante): **A**, porque resolve o problema pela raiz (cwd nunca mais importa) e corrige de brinde o mesmo risco em `pipelines/common/config.py`; mas a escolha entre A/B/C depende de como a equipe já lida com segredos em outros serviços — decisão do Mário.
