"""F8 — composição real do bloco de afiliados na rota `/canais`.

Os testes de `test_canais_affiliate_costs.py` montam o payload à mão. Estes
invocam **a função da rota**, com as dependências já resolvidas, e provam que a
fiação existe de fato: quem chama quem, com quais argumentos, quantas vezes, e
o que sobra quando o bloco falha.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import OperationalError

from app.deps.filters import ResolvedFilters
from app.deps.period import EffectivePeriod
from app.routers import performance as rota
from app.schemas.performance import CanaisResponse
from app.services import performance_service as perf_svc

SESSAO = object()   # sentinela: só precisa não ser None para `_require_db`


def _filtros(*, channels="tiktok", brands=None,
             start=date(2026, 3, 1), end=date(2026, 3, 31),
             ref_month="2026-03") -> ResolvedFilters:
    return ResolvedFilters(
        channels=channels,
        mkt_ids=perf_svc.parse_marketplace_param(channels),
        brands=brands,
        period=EffectivePeriod(start=start, end=end, ref_month=ref_month),
        compare_period=None,
    )


class Espiao:
    """Registra cada chamada com os argumentos exatos."""

    def __init__(self, retorno=None, erro=None):
        self.retorno = retorno
        self.erro = erro
        self.chamadas: list[tuple[tuple, dict]] = []

    def __call__(self, *a, **kw):
        self.chamadas.append((a, kw))
        if self.erro is not None:
            raise self.erro
        return self.retorno


def _payload_canais() -> dict:
    """Payload REAL de `get_canais`, produzido pelo serviço com uma sessão
    falsa. Um stub à mão não validaria contra `CanaisResponse`, e o teste
    passaria a provar menos do que promete."""
    from tests.test_canais_channel_rows import FakeMappingSession, _row

    linhas = [_row("barbours", perf_svc.TIKTOK_ID, gmv=1000, orders=10,
                   total_fees=-300, total_fees_n=30)]
    return perf_svc.get_canais(FakeMappingSession([linhas]), "tiktok", 2026, 3)


def _bloco(status="available") -> dict:
    return {
        "availability_status": status, "period_status": "complete_month",
        "coverage_status": "complete", "freshness_status": "manual_snapshot",
        "rows": [], "channels": [], "months_included": ["2026-03"],
        "affiliate_refreshed_at": None, "source_watermark": None,
        "return_availability": "unavailable_no_attributed_revenue",
        "return_note": "n", "source_note": "n", "limitation_note": "n",
    }


@pytest.fixture
def espioes(monkeypatch):
    canais = Espiao(retorno=_payload_canais())
    bloco = Espiao(retorno=_bloco())
    monkeypatch.setattr(rota.perf_svc, "get_canais", canais)
    monkeypatch.setattr(rota, "safe_affiliate_costs_block", bloco)
    return canais, bloco


def test_cada_um_chamado_exatamente_uma_vez(espioes):
    canais, bloco = espioes
    rota.canais(filters=_filtros(), db=SESSAO)
    assert len(canais.chamadas) == 1
    assert len(bloco.chamadas) == 1


def test_bloco_recebe_o_mesmo_intervalo_que_o_servico(espioes):
    """Duas resoluções independentes de período divergiriam caladas."""
    canais, bloco = espioes
    rota.canais(filters=_filtros(start=date(2026, 1, 1),
                                 end=date(2026, 2, 28),
                                 ref_month=None), db=SESSAO)

    _, kw_canais = canais.chamadas[0]
    args_bloco, _ = bloco.chamadas[0]
    inicio, fim = args_bloco[2], args_bloco[3]

    assert (inicio, fim) == (kw_canais["period"].start, kw_canais["period"].end)
    assert (inicio, fim) == (date(2026, 1, 1), date(2026, 2, 28))


def test_bloco_recebe_a_mesma_sessao_e_os_mesmos_canais_e_marcas(espioes):
    canais, bloco = espioes
    filtros = _filtros(channels="tiktok,ml", brands=["apice", "barbours"])
    rota.canais(filters=filtros, db=SESSAO)

    args_canais, kw_canais = canais.chamadas[0]
    args_bloco, kw_bloco = bloco.chamadas[0]

    assert args_canais[0] is SESSAO and args_bloco[0] is SESSAO
    assert args_canais[1] == "tiktok,ml"
    assert args_bloco[1] == perf_svc.parse_marketplace_param("tiktok,ml")
    assert kw_canais["brand_keys"] == ["apice", "barbours"]
    assert kw_bloco["brand_keys"] == ["apice", "barbours"]


def test_marcas_none_nao_vira_lista_vazia(espioes):
    """`None` = todas as marcas; `[]` = nenhuma elegível. Trocar um pelo outro
    transformaria "sem filtro" em "sem marca"."""
    _, bloco = espioes
    rota.canais(filters=_filtros(brands=None), db=SESSAO)
    assert bloco.chamadas[0][1]["brand_keys"] is None


def test_bloco_e_realmente_anexado_ao_payload(espioes):
    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    assert "affiliate_costs" in resposta
    assert resposta["affiliate_costs"]["availability_status"] == "available"
    assert CanaisResponse.model_validate(resposta).affiliate_costs is not None


def test_ordem_o_bloco_le_o_payload_ja_produzido(espioes):
    """O bloco é composto SOBRE a resposta, então `get_canais` não pode ver a
    chave — é isso que mantém o contrato histórico intacto.

    As chaves originais são capturadas ANTES da chamada: `_payload_canais()`
    passaria por `get_canais`, que está monkeypatchado, e devolveria o próprio
    dicionário do espião — já mutado — tornando a comparação trivial.
    """
    canais, _ = espioes
    antes = set(canais.retorno)
    assert "affiliate_costs" not in antes

    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    assert set(resposta) - antes == {"affiliate_costs"}
    assert antes <= set(resposta)


def test_falha_sql_do_bloco_nao_remove_kpis_nem_channel_rows(monkeypatch):
    """`safe_...` real (não espião): a falha é engolida lá dentro."""
    # Capturado ANTES do monkeypatch: depois dele, `_payload_canais()` passaria
    # pelo espião e devolveria o mesmo dicionário, sem provar nada.
    esperado = _payload_canais()
    monkeypatch.setattr(rota.perf_svc, "get_canais",
                        Espiao(retorno=_payload_canais()))

    def explode(*a, **kw):
        raise OperationalError("SELECT 1", {}, Exception("boom"))

    monkeypatch.setattr(
        "app.services.affiliate_costs_service.build_affiliate_costs_block",
        explode)

    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    assert resposta["kpis"] == esperado["kpis"]
    assert resposta["channel_rows"] == esperado["channel_rows"]
    assert resposta["channel_rows"]          # e nao esta vazio
    assert resposta["affiliate_costs"]["availability_status"] == "error"
    assert resposta["affiliate_costs"]["rows"] == []
    assert CanaisResponse.model_validate(resposta).channel_rows


def test_bug_nao_sql_do_bloco_continua_propagando(monkeypatch):
    """Um `except Exception` amplo esconderia isto sob "erro de fonte"."""
    monkeypatch.setattr(rota.perf_svc, "get_canais",
                        Espiao(retorno=_payload_canais()))
    monkeypatch.setattr(
        "app.services.affiliate_costs_service.build_affiliate_costs_block",
        Espiao(erro=AttributeError("bug de programacao")))

    with pytest.raises(AttributeError):
        rota.canais(filters=_filtros(), db=SESSAO)


def test_falha_de_get_canais_nao_e_mascarada_pelo_bloco(espioes):
    """Se a consulta principal cair, a rota falha — o bloco não pode dar a
    impressão de que a página respondeu."""
    canais, bloco = espioes
    canais.erro = OperationalError("SELECT 1", {}, Exception("boom"))
    with pytest.raises(OperationalError):
        rota.canais(filters=_filtros(), db=SESSAO)
    assert bloco.chamadas == []       # nem chegou a ser chamado


def test_sem_banco_a_rota_falha_antes_de_qualquer_consulta(espioes):
    from fastapi import HTTPException

    canais, bloco = espioes
    with pytest.raises(HTTPException) as exc:
        rota.canais(filters=_filtros(), db=None)
    assert exc.value.status_code == 503
    assert canais.chamadas == [] and bloco.chamadas == []
