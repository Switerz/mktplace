"""METAS-1 — leitura e gravacao das metas mensais de GMV (aba "Metas").

GET  /api/v1/metas/{AAAA-MM}  -> metas do mes (so' celulas com meta)
PUT  /api/v1/metas/{AAAA-MM}  -> grava as celulas enviadas; `null` remove

Primeira rota de ESCRITA da API. Sem login por decisao do proprietario
(qualquer pessoa com o link edita), entao a protecao e':
  - validacao fechada (marcas, canais, faixa de valor e de mes);
  - limite de gravacoes por minuto, por cliente e global;
  - historico de toda alteracao no banco (migration 031).

Falha de banco vira 503 com mensagem fixa: o texto da excecao do
SQLAlchemy pode carregar o DSN.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.rate_limit import JanelaDeslizante
from app.routers.b2b_revenda import chave_do_cliente
from app.schemas.metas import MetasMesResponse, SalvarMetasRequest, SalvarMetasResponse
from app.services import metas_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/metas", tags=["metas"])

MSG_SEM_BANCO = "Banco de dados indisponivel. Verifique DATABASE_URL."
MSG_FALHA_BANCO = "Nao foi possivel acessar as metas no banco agora."
MSG_LIMITE = "Muitas gravacoes em pouco tempo. Tente de novo em instantes."

#: Limites de gravacao (memoria do processo; aproximados, como os do b2b).
LIMITE_POR_CLIENTE = 30
LIMITE_GLOBAL = 120
_limite_cliente = JanelaDeslizante(LIMITE_POR_CLIENTE, 60.0)
_limite_global = JanelaDeslizante(LIMITE_GLOBAL, 60.0)


def _mes(mes: str):
    try:
        return svc.parse_mes(mes)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _require_db(db: Session) -> Session:
    if db is None:
        raise HTTPException(503, MSG_SEM_BANCO)
    return db


def _limitar_gravacao(request: Request) -> None:
    for limitador, chave in ((_limite_global, "*"), (_limite_cliente, chave_do_cliente(request))):
        r = limitador.verificar(chave)
        if not r.permitido:
            raise HTTPException(429, MSG_LIMITE, headers={"Retry-After": str(r.retry_after_s)})


@router.get("/{mes}", response_model=MetasMesResponse)
def ler_metas(mes: str, db: Session = Depends(get_db)):
    dia1 = _mes(mes)
    db = _require_db(db)
    try:
        return svc.ler(db, dia1)
    except SQLAlchemyError as exc:
        logger.error("metas: leitura falhou (%s)", type(exc).__name__)
        raise HTTPException(503, MSG_FALHA_BANCO)


@router.put("/{mes}", response_model=SalvarMetasResponse)
def salvar_metas(
    mes: str,
    corpo: SalvarMetasRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    dia1 = _mes(mes)
    _limitar_gravacao(request)
    db = _require_db(db)
    pedidas = {(c.brand, c.channel): c.meta_gmv for c in corpo.celulas}
    try:
        estado, n = svc.salvar(db, dia1, pedidas)
    except SQLAlchemyError as exc:
        logger.error("metas: gravacao falhou (%s)", type(exc).__name__)
        raise HTTPException(503, MSG_FALHA_BANCO)
    logger.info("metas: %s gravado com %d alteracao(oes)", estado["mes"], n)
    return {**estado, "alteracoes": n}
