"""T-0014 — rota read-only da Performance das contas.

INERTE POR FLAG: registrado em `app/main.py`, mas com
`settings.performance_contas_api_enabled` desligada (padrao) toda requisicao
responde 404 -- o mesmo corpo de rota inexistente. A checagem e' dependencia
do ROUTER, e por isso roda ANTES de `get_db` e de `brands_query`: desligada,
nenhuma sessao e' aberta e nenhuma fonte e' chamada (fail-closed).

SOMENTE GET. A entrada do cliente e' validada na BORDA contra a allowlist de
marcas do servico; o que nao passa vira 422 com mensagem FIXA, sem ecoar a
entrada.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.schemas.account_performance import PerformanceContasResponse
from app.services import account_performance_service as svc


def exigir_flag() -> None:
    """Flag desligada = 404 igual ao de rota inexistente, sem tocar em nada.

    Lida a cada requisicao (e nao no import) para que a flag valha sem
    reconstruir o app e para que os testes possam alterna-la.
    """
    if not settings.performance_contas_api_enabled:
        raise HTTPException(404, "Not Found")


router = APIRouter(
    prefix="/api/v1/performance-contas",
    tags=["performance-contas"],
    dependencies=[Depends(exigir_flag)],
)

_MSG_BRANDS = "Marca invalida. Valores aceitos: " + ", ".join(svc.MARCAS_PERMITIDAS)
_MAX_ITENS = 20


def _require_db(db: Session) -> Session:
    if db is None:
        raise HTTPException(503, "Banco de dados indisponivel. Verifique DATABASE_URL.")
    return db


def brands_query(
    brands: Optional[list[str]] = Query(
        None,
        description="Marcas separadas por virgula (?brands=a,b) ou repetidas "
                    "(?brands=a&brands=b). Omitido = todas."),
) -> Optional[list[str]]:
    """CSV e/ou repeticao -> lista canonica. Fora da allowlist = 422, sem eco."""
    if brands is None:
        return None
    itens = [x.strip().lower() for valor in brands for x in valor.split(",")]
    itens = [x for x in itens if x]
    if (not itens or len(itens) > _MAX_ITENS
            or any(x not in svc.MARCAS_PERMITIDAS for x in itens)):
        raise HTTPException(422, _MSG_BRANDS)
    return sorted(set(itens))


@router.get("", response_model=PerformanceContasResponse)
@router.get("/", response_model=PerformanceContasResponse, include_in_schema=False)
def performance_contas(
    brands: Optional[list[str]] = Depends(brands_query),
    db: Session = Depends(get_db),
):
    """Indicadores oficiais e Torre por canal, cada um com estado explicito."""
    return svc.montar_performance(_require_db(db), brands=brands)
