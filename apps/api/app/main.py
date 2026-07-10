from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import performance, regioes

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


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}
