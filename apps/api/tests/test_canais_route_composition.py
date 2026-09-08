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


def _bloco_descontos(status="available") -> dict:
    """Bloco UE8-I3. Contrato §28 — estrutura DIFERENTE do de afiliados."""
    return {
        "availability_status": status, "period_status": "complete_month",
        "coverage_status": "complete", "coverage_basis": "observed_grid",
        "coverage_expected_keys": 155, "coverage_present_keys": 155,
        "coverage_missing_keys": 0,
        "freshness_status": "recent_load",
        "rows": [], "date_from": date(2026, 3, 1), "date_to": date(2026, 3, 31),
        "date_count": 31, "discounts_refreshed_at": None,
        "source_max_date": None, "source_max_updated_at": None,
        "seller_note": "s", "subsidy_note": "p", "limitation_note": "n",
        "warnings": [],
    }


@pytest.fixture
def espioes(monkeypatch):
    """Os TRES colaboradores da rota. `descontos` entra junto para que os
    testes antigos continuem provando o que provavam: com o bloco novo real, a
    contagem de chamadas e a invariancia do corpo historico mudariam de causa
    sem que ninguem percebesse."""
    canais = Espiao(retorno=_payload_canais())
    bloco = Espiao(retorno=_bloco())
    descontos = Espiao(retorno=_bloco_descontos())
    monkeypatch.setattr(rota.perf_svc, "get_canais", canais)
    monkeypatch.setattr(rota, "safe_affiliate_costs_block", bloco)
    monkeypatch.setattr(rota, "safe_tiktok_order_discounts_block", descontos)
    return canais, bloco, descontos


def test_cada_um_chamado_exatamente_uma_vez(espioes):
    canais, bloco, descontos = espioes
    rota.canais(filters=_filtros(), db=SESSAO)
    assert len(canais.chamadas) == 1
    assert len(bloco.chamadas) == 1
    assert len(descontos.chamadas) == 1


def test_bloco_recebe_o_mesmo_intervalo_que_o_servico(espioes):
    """Duas resoluções independentes de período divergiriam caladas."""
    canais, bloco, descontos = espioes
    rota.canais(filters=_filtros(start=date(2026, 1, 1),
                                 end=date(2026, 2, 28),
                                 ref_month=None), db=SESSAO)

    _, kw_canais = canais.chamadas[0]
    args_bloco, _ = bloco.chamadas[0]
    inicio, fim = args_bloco[2], args_bloco[3]

    assert (inicio, fim) == (kw_canais["period"].start, kw_canais["period"].end)
    assert (inicio, fim) == (date(2026, 1, 1), date(2026, 2, 28))

    # O bloco novo recebe EXATAMENTE a mesma janela — resolvida uma vez só.
    args_desc, _ = descontos.chamadas[0]
    assert (args_desc[2], args_desc[3]) == (inicio, fim)


def test_bloco_recebe_a_mesma_sessao_e_os_mesmos_canais_e_marcas(espioes):
    canais, bloco, descontos = espioes
    filtros = _filtros(channels="tiktok,ml", brands=["apice", "barbours"])
    rota.canais(filters=filtros, db=SESSAO)

    args_canais, kw_canais = canais.chamadas[0]
    args_bloco, kw_bloco = bloco.chamadas[0]
    args_desc, kw_desc = descontos.chamadas[0]

    assert args_canais[0] is SESSAO and args_bloco[0] is SESSAO
    assert args_desc[0] is SESSAO
    assert args_canais[1] == "tiktok,ml"
    assert args_bloco[1] == perf_svc.parse_marketplace_param("tiktok,ml")
    assert args_desc[1] == perf_svc.parse_marketplace_param("tiktok,ml")
    assert kw_canais["brand_keys"] == ["apice", "barbours"]
    assert kw_bloco["brand_keys"] == ["apice", "barbours"]
    assert kw_desc["brand_keys"] == ["apice", "barbours"]


def test_marcas_none_nao_vira_lista_vazia(espioes):
    """`None` = todas as marcas; `[]` = nenhuma elegível. Trocar um pelo outro
    transformaria "sem filtro" em "sem marca"."""
    _, bloco, descontos = espioes
    rota.canais(filters=_filtros(brands=None), db=SESSAO)
    assert bloco.chamadas[0][1]["brand_keys"] is None
    assert descontos.chamadas[0][1]["brand_keys"] is None


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
    canais, _, _ = espioes
    antes = set(canais.retorno)
    assert "affiliate_costs" not in antes
    assert "tiktok_order_discounts" not in antes

    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    assert set(resposta) - antes == {"affiliate_costs", "tiktok_order_discounts"}
    assert antes <= set(resposta)


def test_falha_sql_do_bloco_nao_remove_kpis_nem_channel_rows(monkeypatch):
    """`safe_...` real (não espião): a falha é engolida lá dentro."""
    # Capturado ANTES do monkeypatch: depois dele, `_payload_canais()` passaria
    # pelo espião e devolveria o mesmo dicionário, sem provar nada.
    esperado = _payload_canais()
    monkeypatch.setattr(rota.perf_svc, "get_canais",
                        Espiao(retorno=_payload_canais()))
    # O bloco UE8-I3 e' espionado: sem isso ele receberia a sessao sentinela e
    # levantaria `AttributeError`, mascarando o que este teste mede.
    monkeypatch.setattr(rota, "safe_tiktok_order_discounts_block",
                        Espiao(retorno=_bloco_descontos()))

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
    monkeypatch.setattr(rota, "safe_tiktok_order_discounts_block",
                        Espiao(retorno=_bloco_descontos()))
    monkeypatch.setattr(
        "app.services.affiliate_costs_service.build_affiliate_costs_block",
        Espiao(erro=AttributeError("bug de programacao")))

    with pytest.raises(AttributeError):
        rota.canais(filters=_filtros(), db=SESSAO)


def test_falha_de_get_canais_nao_e_mascarada_pelo_bloco(espioes):
    """Se a consulta principal cair, a rota falha — o bloco não pode dar a
    impressão de que a página respondeu."""
    canais, bloco, descontos = espioes
    canais.erro = OperationalError("SELECT 1", {}, Exception("boom"))
    with pytest.raises(OperationalError):
        rota.canais(filters=_filtros(), db=SESSAO)
    # Nenhum dos dois auxiliares chegou a ser chamado.
    assert bloco.chamadas == []
    assert descontos.chamadas == []


def test_sem_banco_a_rota_falha_antes_de_qualquer_consulta(espioes):
    from fastapi import HTTPException

    canais, bloco, descontos = espioes
    with pytest.raises(HTTPException) as exc:
        rota.canais(filters=_filtros(), db=None)
    assert exc.value.status_code == 503
    assert canais.chamadas == []
    assert bloco.chamadas == []
    assert descontos.chamadas == []


# ---------------------------------------------------------------------------
# UE8-I3 — o bloco de descontos e' ADITIVO e nao interfere em nada
# ---------------------------------------------------------------------------

def test_descontos_realmente_anexado_ao_payload(espioes):
    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    assert resposta["tiktok_order_discounts"]["availability_status"] == "available"
    validado = CanaisResponse.model_validate(resposta)
    assert validado.tiktok_order_discounts is not None


def test_descontos_nao_substitui_nenhum_campo_existente(espioes):
    """O bloco novo so' ACRESCENTA: nada do corpo histórico muda de valor."""
    canais, _, _ = espioes
    esperado = dict(canais.retorno)
    historicos = {k: v for k, v in esperado.items()
                  if k not in ("affiliate_costs", "tiktok_order_discounts")}

    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    for chave, valor in historicos.items():
        assert resposta[chave] == valor, chave


def test_affiliate_costs_permanece_identico_com_o_bloco_novo(espioes):
    """O bloco de afiliados não pode mudar por causa do vizinho."""
    _, bloco, _ = espioes
    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    assert resposta["affiliate_costs"] == bloco.retorno


def test_afiliados_e_descontos_recebem_a_mesma_janela_e_os_mesmos_ids(espioes):
    """A janela e os ids são resolvidos UMA vez e reusados: duas resoluções
    independentes divergiriam caladas."""
    _, bloco, descontos = espioes
    rota.canais(filters=_filtros(channels="tiktok,shopee",
                                 brands=["kokeshi"]), db=SESSAO)
    a, _ = bloco.chamadas[0]
    d, _ = descontos.chamadas[0]
    assert a[1] == d[1]                      # mesmos marketplace ids
    assert (a[2], a[3]) == (d[2], d[3])      # mesma janela


def test_falha_sql_dos_descontos_preserva_o_resto_da_pagina(monkeypatch):
    """`safe_...` real (não espião): a falha é engolida lá dentro."""
    esperado = _payload_canais()
    monkeypatch.setattr(rota.perf_svc, "get_canais",
                        Espiao(retorno=_payload_canais()))
    monkeypatch.setattr(rota, "safe_affiliate_costs_block",
                        Espiao(retorno=_bloco()))

    def explode(*a, **kw):
        raise OperationalError("SELECT 1", {}, Exception("boom"))

    monkeypatch.setattr(
        "app.services.tiktok_order_discounts_service"
        ".build_tiktok_order_discounts_block", explode)

    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    assert resposta["kpis"] == esperado["kpis"]
    assert resposta["channel_rows"] == esperado["channel_rows"]
    assert resposta["channel_rows"]
    assert resposta["affiliate_costs"]["availability_status"] == "available"
    assert resposta["tiktok_order_discounts"]["availability_status"] == "error"
    assert resposta["tiktok_order_discounts"]["rows"] == []
    assert CanaisResponse.model_validate(resposta).channel_rows


def test_bug_nao_sql_dos_descontos_continua_propagando(monkeypatch):
    """Um `except Exception` amplo esconderia isto sob "erro de fonte"."""
    monkeypatch.setattr(rota.perf_svc, "get_canais",
                        Espiao(retorno=_payload_canais()))
    monkeypatch.setattr(rota, "safe_affiliate_costs_block",
                        Espiao(retorno=_bloco()))
    monkeypatch.setattr(
        "app.services.tiktok_order_discounts_service"
        ".build_tiktok_order_discounts_block",
        Espiao(erro=AttributeError("bug de programacao")))

    with pytest.raises(AttributeError):
        rota.canais(filters=_filtros(), db=SESSAO)


def test_falha_de_um_bloco_nao_derruba_o_outro(monkeypatch):
    """Os dois blocos são independentes: a queda de um não pode levar o
    vizinho junto."""
    monkeypatch.setattr(rota.perf_svc, "get_canais",
                        Espiao(retorno=_payload_canais()))

    def explode(*a, **kw):
        raise OperationalError("SELECT 1", {}, Exception("boom"))

    monkeypatch.setattr(
        "app.services.affiliate_costs_service.build_affiliate_costs_block",
        explode)
    monkeypatch.setattr(rota, "safe_tiktok_order_discounts_block",
                        Espiao(retorno=_bloco_descontos()))

    resposta = rota.canais(filters=_filtros(), db=SESSAO)
    assert resposta["affiliate_costs"]["availability_status"] == "error"
    assert resposta["tiktok_order_discounts"]["availability_status"] == "available"


def test_nenhum_endpoint_novo_foi_criado():
    """O bloco é ADITIVO em `/canais` — não existe rota própria."""
    caminhos = {r.path for r in rota.router.routes}
    assert not any("discount" in c or "desconto" in c for c in caminhos)
