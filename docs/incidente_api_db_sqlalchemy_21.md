# INCIDENTE-API-DB — a API ficou sem banco após um deploy (2026-09-24)

Registro de causa raiz. Não é um plano: é o que aconteceu, como foi provado, e
o que ainda depende de decisão.

## Impacto

Todas as rotas da API que usam banco passaram a devolver **503 “Banco de dados
indisponivel”** — Shopee, Mercado Livre, tendência e performance. A Expedição,
que estava viva em produção, ficou fora do ar. `/health` respondeu **200** o
tempo todo.

Recuperado por rollback para o deploy anterior.

## Causa raiz, provada

**O SQLAlchemy 2.1.0 trocou o DBAPI padrão de `postgresql://` de `psycopg2`
para `psycopg` (v3).**

`apps/api/pyproject.toml` declara `sqlalchemy>=2.0` e `psycopg2-binary>=2.9`.
O Render constrói com `pip install -e .`, que **ignora o `uv.lock`** e
reresolve as faixas. O build pegou SQLAlchemy 2.1.0 com apenas `psycopg2`
instalado, e `create_engine("postgresql://…")` passou a levantar
`ModuleNotFoundError: psycopg`.

`_make_engine` engolia essa exceção e devolvia `None`. Sem engine, sem
`SessionLocal`; `get_db` passou a entregar `None`, e o guard de cada rota
virou 503.

### Reprodução (DSN sintético, ambiente descartável)

| pilha | `postgresql://` | driver |
|---|---|---|
| SA 2.0.43 + psycopg2 | OK | `psycopg2` |
| **SA 2.1.0 + psycopg2** | **`ModuleNotFoundError: 'psycopg'`** | — |
| SA 2.1.0 + psycopg2, DSN `postgresql+psycopg2://` | OK | `psycopg2` |
| SA 2.1.0 + psycopg v3 instalado | OK | `psycopg` |

As linhas 3 e 4 são a contraprova: o problema não é a URL nem o `psycopg2` —
é **qual driver o `postgresql://` passou a significar**.

### O que NÃO era

- `DATABASE_URL` ausente, vazia ou malformada. O esquema visível é
  `postgresql://`, que está correto.
- Neon fora do ar, senha errada, rede. Essas falhas acontecem **depois** da
  criação do engine e produzem erro de conexão, não o 503 observado.
- `psycopg2` ausente do artefato. O build o instalou (2.9.13).
- Mudança no código da aplicação. O PR que disparou o deploy não tocou
  `config.py`, `database.py`, `main.py` nem nenhum `pyproject.toml`.

## Os três defeitos que transformaram isso num incidente

**1. Build não reprodutível.** O repositório tem `uv.lock`, mas o Render roda
`pip install -e .`. O lock não participa do build, e toda faixa `>=` é
reresolvida a cada deploy. Um deploy do mesmo commit pode instalar versões
diferentes em dias diferentes — foi exatamente o que aconteceu. A frase
“o `uv.lock` não mudou, então o artefato é igual” é falsa neste serviço.

**2. Falha de inicialização engolida.** `_make_engine` devolvia `None` sem
log. Não havia como saber, olhando o serviço, que o engine nunca nascera.

**3. Health check não olhava o banco.** O *Health Check Path* estava **vazio**,
então o Render usou `HEAD /` — que devolveu **404** — e mesmo assim declarou o
serviço *live*. Uma instância incapaz de servir substituiu a saudável.

## O que o PR #45 corrige

- `_make_engine` registra a falha e a classifica em categoria sanitizada;
- `/ready` valida `SELECT 1` e devolve 503 com a categoria;
- `/health` continua sendo liveness, de propósito;
- teste de regressão que falha se a pilha instalada não conseguir montar um
  engine `postgresql://` — afirma o **comportamento**, sem pinar versão.

## O que o PR #45 NÃO faz, e é decisão de quem opera

Nenhuma destas foi aplicada. Todas resolvem o mesmo problema por caminhos
diferentes, com custos diferentes:

| opção | efeito | custo |
|---|---|---|
| **Health Check Path = `/ready`** | impede promover instância sem banco | só painel; **faça esta de qualquer forma** |
| Build reprodutível (`uv sync --frozen` ou `requirements.txt` com hashes) | acaba com a reresolução silenciosa | muda o Build Command |
| `sqlalchemy>=2.0,<2.1` no `pyproject.toml` | trava a linha conhecida | adia o problema para a migração ao 2.1 |
| `psycopg[binary]` nas dependências | o default do 2.1 passa a existir | acrescenta driver; muda o DBAPI em uso |
| `DATABASE_URL` com `postgresql+psycopg2://` | driver explícito, imune ao default | altera credencial no painel |

A primeira e a segunda atacam a **classe** do problema; as outras três, esta
ocorrência. Pinar a versão sem tornar o build reprodutível deixa a porta
aberta para a próxima faixa `>=`.
