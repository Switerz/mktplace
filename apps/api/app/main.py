from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import readiness
from app.routers import expedicao, performance, regioes

app = FastAPI(
    title="Torre de Controle de Marketplaces — GoBeauté",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(performance.router)
app.include_router(regioes.router)
app.include_router(expedicao.router)


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    """LIVENESS: o processo esta de pe. NAO diz nada sobre o banco.

    Mantido exatamente como era, de proposito. Se `/health` passasse a exigir
    banco, uma indisponibilidade transitoria do Neon faria o Render matar e
    recriar a instancia em laco — trocaria uma degradacao por uma queda.
    """
    return {"status": "ok"}


@app.api_route("/ready", methods=["GET", "HEAD"])
def ready(response: Response):
    """READINESS: esta instancia consegue MESMO servir — inclusive o banco.

    E' ESTE o caminho que o Health Check Path do Render precisa apontar.

    Por que existe (INCIDENTE-API-DB-2, 2026-09-24): um deploy subiu com o
    engine do banco nao inicializado. `/health` respondeu 200, o Render
    promoveu a instancia, e a Torre inteira passou a devolver 503 — Shopee e
    Mercado Livre inclusive. Com o health check apontando para `/ready`, aquela
    instancia nunca teria substituido a saudavel.

    O corpo carrega SOMENTE categoria e nome de classe. Nunca URL, host,
    usuario, senha ou mensagem de excecao: o texto do `ArgumentError` do
    SQLAlchemy contem a URL inteira, e este endpoint e' publico.
    """
    pronto, detalhe = readiness()
    if not pronto:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return detalhe
