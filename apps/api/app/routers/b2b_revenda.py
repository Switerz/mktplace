"""`/api/v1/b2b-revenda` — revendedores B2B x PMA do OctaPrice (Gate TORRE-B2B-OCTAPRICE).

Fonte externa, somente leitura: Torre B2B, pelas RPCs `fn_monit_*`.
Contrato: docs/b2b_revenda_contrato.md.

QUATRO ROTAS, TODAS AGREGADAS. Nenhuma devolve cliente, vendedor, seller,
CNPJ, e-mail ou telefone, e nenhuma aceita filtro por cliente ou vendedor:
esta API nao tem autenticacao, e rota com dado de cliente so' nasce depois que
a autenticacao da Torre de Marketplaces for definida.

ORDEM FIXA EM TODA ROTA:
  0. AUTENTICACAO (dependencia do ROUTER, vale para toda rota daqui):
     sem configuracao -> 503; sem token/token invalido -> 401; sem a
     permission `b2b_revenda:read` -> 403. Nada abaixo roda antes disso
     (app/auth.py, Gate OCTAPRICE-2);
  1. flag desligada -> `unavailable` sem criar cliente nem abrir conexao;
  2. recorte invalido -> 422 com mensagem constante (o valor nao volta);
  3. credencial ausente/invalida, fonte fora, contrato quebrado, dia sem
     relatorio -> 200 com `unavailable` e motivo. Nunca zero, nunca 500.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import exigir_escopo
from app.config import settings
from app.schemas.b2b_revenda import (
    B2BRevendaUnavailableResponse,
    CanaisMarcasResponse,
    FrescorResponse,
    ResumoResponse,
    SkusResponse,
)
from app.services import b2b_revenda_service as svc
from app.services.b2b_revenda_fonte import FonteComCache, FonteIndisponivel, obter_fonte

router = APIRouter(
    prefix="/api/v1/b2b-revenda",
    tags=["b2b-revenda"],
    dependencies=[Depends(exigir_escopo(lambda: settings.b2b_revenda_required_scope))],
)


def _agora() -> datetime:
    """Unico ponto de leitura do relogio nesta rota (substituivel em teste)."""
    return datetime.now(timezone.utc)


def provedor_fonte() -> Callable[[], FonteComCache]:
    """Devolve a FABRICA, nao a fonte: com a flag desligada a fabrica nunca e'
    chamada, entao nenhum engine nasce. Testes trocam isto por uma fonte falsa."""
    return obter_fonte


def _indisponivel(motivo: str, frescor=None) -> B2BRevendaUnavailableResponse:
    return B2BRevendaUnavailableResponse(
        motivo_tecnico=motivo,
        unavailable_reason=svc.MENSAGENS.get(motivo, svc.MENSAGENS["falha_desconhecida"]),
        frescor=frescor,
        limitacoes=list(svc.LIMITACOES),
    )


class _Params:
    """Recorte comum as rotas de dado. Parametros repetidos (`?marcas=AP&marcas=BB`)."""

    def __init__(
        self,
        data: Optional[date] = Query(None, description="Dia do relatorio (YYYY-MM-DD). Omitido = mais recente."),
        marcas: Optional[list[str]] = Query(None, description="Codigo da marca na fonte (AP, BB, KS)."),
        canais: Optional[list[str]] = Query(None, description="Canal com a grafia da fonte. So' os do escopo dos KPIs."),
        severidades: Optional[list[str]] = Query(None, description="grave, media, leve."),
        skus: Optional[list[str]] = Query(None, description="SKU do OctaPrice."),
        ufs: Optional[list[str]] = Query(None, description="UF do seller."),
    ):
        self.data, self.marcas, self.canais = data, marcas, canais
        self.severidades, self.skus, self.ufs = severidades, skus, ufs


def _executar(params: Optional[_Params], fabrica: Callable[[], FonteComCache], caso):
    if not settings.b2b_revenda_enabled:
        return _indisponivel("feature_flag_desligada")
    canais_kpi = settings.b2b_revenda_canais_kpi_list
    recorte = None
    if params is not None:
        try:
            recorte = svc.validar_recorte(
                data=params.data, marcas=params.marcas, canais=params.canais,
                severidades=params.severidades, skus=params.skus, ufs=params.ufs,
                canais_kpi=canais_kpi,
            )
        except svc.FiltroInvalido as invalido:
            raise HTTPException(422, invalido.mensagem_segura)
    try:
        fonte = fabrica()
        return caso(fonte, recorte, canais_kpi)
    except FonteIndisponivel as exc:
        return _indisponivel(exc.motivo)
    except svc.B2BIndisponivel as exc:
        return _indisponivel(exc.motivo, exc.frescor)


_Resposta = B2BRevendaUnavailableResponse


@router.get("/frescor", response_model=Union[FrescorResponse, _Resposta])
def frescor(fabrica: Callable[[], FonteComCache] = Depends(provedor_fonte)):
    """De quando e' o dado: dia do relatorio, janela da coleta, importacao na
    Torre B2B, situacao (em dia / atrasado), dias disponiveis e canais fora
    dos KPIs. Nao traz numero de anuncio nem de violacao."""
    return _executar(
        None, fabrica,
        lambda fonte, _r, canais: svc.get_frescor(fonte, agora=_agora(), canais_kpi=canais),
    )


@router.get("/resumo", response_model=Union[ResumoResponse, _Resposta])
def resumo(params: _Params = Depends(), fabrica: Callable[[], FonteComCache] = Depends(provedor_fonte)):
    """KPIs do dia: anuncios, violacao do PMA, sellers violando, desvio medio,
    buy box, severidade e cobertura de identificacao (so' contagens)."""
    return _executar(
        params, fabrica,
        lambda fonte, r, canais: svc.get_resumo(fonte, r, agora=_agora(), canais_kpi=canais),
    )


@router.get("/canais-marcas", response_model=Union[CanaisMarcasResponse, _Resposta])
def canais_marcas(params: _Params = Depends(), fabrica: Callable[[], FonteComCache] = Depends(provedor_fonte)):
    """Anuncios e violacoes por marca x canal, no escopo dos KPIs."""
    return _executar(
        params, fabrica,
        lambda fonte, r, canais: svc.get_canais_marcas(fonte, r, agora=_agora(), canais_kpi=canais),
    )


@router.get("/skus", response_model=Union[SkusResponse, _Resposta])
def skus(
    params: _Params = Depends(),
    limite: int = Query(svc.LIMITE_SKUS_PADRAO, ge=1, le=svc.LIMITE_SKUS_MAX),
    fabrica: Callable[[], FonteComCache] = Depends(provedor_fonte),
):
    """Ranking de SKUs por anuncios violando (so' SKUs com violacao)."""
    return _executar(
        params, fabrica,
        lambda fonte, r, canais: svc.get_skus(fonte, r, limite=limite, agora=_agora(), canais_kpi=canais),
    )
