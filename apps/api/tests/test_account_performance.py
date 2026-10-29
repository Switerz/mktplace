"""T-0014 — Performance das contas (`/api/v1/performance-contas`).

O que estes testes existem para impedir:
  1. ausencia, null ou erro de fonte virar 0 na tela;
  2. uma fonte caida derrubar as outras, ou vazar o texto da excecao;
  3. numero Torre sair com limiar de origem oficial (a referencia de 4% do
     TikTok nao tem fonte oficial);
  4. entrada fora da allowlist voltar ecoada;
  5. a ordem dos canais e os tres estados oficiais fixos mudarem sem querer.

Sem banco: a app de teste monta SO' este router e as funcoes de servico sao
substituidas por monkeypatch.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.routers import account_performance as rota
from app.schemas.account_performance import Indicador, Limiar
from app.services import account_performance_service as svc
from app.services import expedicao_service
from app.services import expedicao_shopee_ldr_service
from app.services import expedicao_tiktok_service
from app.services import performance_service

URL = "/api/v1/performance-contas"
SEGREDO = "postgresql://usuario:senha-NAO-PODE-VAZAR@host/db"


class DbFalso:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Respostas falsas das fontes (formato real dos servicos)
# ---------------------------------------------------------------------------
def quality_ok(cancel=4.5, nao_entregue=1.2):
    return {
        "kpis": {"ml_cancel_rate_pct": cancel, "ml_not_delivered_rate_pct": nao_entregue},
        "brands": [{"brand": "barbours", "ml_total_orders": 100},
                   {"brand": "kokeshi", "ml_total_orders": 50}],
        "refreshed_at": (_agora() - timedelta(hours=3)).isoformat(),
    }


def expedicao_ok(freshness="fresh", idade=1.0):
    return {
        "availability": "available",
        "unavailable_reason": None,
        "channel": "mercadolivre",
        "snapshot": {"effective_at": _agora() - timedelta(hours=idade)},
        "totals": {"over_48h_count": 3},
        "accounts": [{"brand": "barbours", "shop_account": "1", "over_48h_count": 2},
                     {"brand": "kokeshi", "shop_account": "2", "over_48h_count": 1}],
        "freshness": [{"brand": "barbours", "freshness": freshness},
                      {"brand": "kokeshi", "freshness": "fresh"}],
        "limitations": {"snapshot_age_hours": idade},
    }


def expedicao_indisponivel(motivo):
    return {"availability": "unavailable", "unavailable_reason": motivo,
            "channel": "mercadolivre", "snapshot": None, "totals": None,
            "accounts": [], "freshness": [], "limitations": {}}


def tiktok_ok(rate=0.031, idade_h=1.0, reconstruido=True):
    return {
        "availability": "available",
        "unavailable_reason": None,
        "deadline_is_reconstructed": reconstruido,
        "targets": {"tiktok_ldr": expedicao_tiktok_service.META_TIKTOK_LDR,
                    "internal_critical": 0.10},
        "snapshot": {"effective_at": _agora() - timedelta(hours=idade_h)},
        "ldr": {"from": date(2026, 10, 22), "to": date(2026, 10, 29),
                "rate": rate, "base": 120 if rate is not None else 0},
        "brands": [],
    }


def tiktok_indisponivel(motivo):
    return {"availability": "unavailable", "unavailable_reason": motivo,
            "deadline_is_reconstructed": True,
            "targets": {"tiktok_ldr": expedicao_tiktok_service.META_TIKTOK_LDR,
                        "internal_critical": 0.10},
            "snapshot": None, "ldr": None}


def shopee_ok(rate=0.05, base=200, stale=False, oculta=None):
    return {
        "availability": "available",
        "unavailable_reason": None,
        "official": False,
        "snapshot": {"effective_at": _agora() - timedelta(hours=1), "stale": stale},
        "ldr": {"from": date(2026, 10, 22), "to": date(2026, 10, 29), "rate": rate,
                "base": base, "rate_hidden_reason": oculta},
        "brands": [{"brand": "barbours"}, {"brand": "lescent"}],
    }


def shopee_indisponivel(motivo):
    return {"availability": "unavailable", "unavailable_reason": motivo,
            "official": False, "snapshot": None, "ldr": None, "brands": []}


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    return DbFalso()


@pytest.fixture()
def chamadas():
    return {}


@pytest.fixture()
def fontes(monkeypatch, chamadas):
    """Instala as quatro fontes com resposta `dado`. Cada teste troca a sua."""
    estado = {
        "quality": lambda: quality_ok(),
        "expedicao": lambda: expedicao_ok(),
        "tiktok": lambda: tiktok_ok(),
        "shopee": lambda: shopee_ok(),
    }

    def _registrar(nome):
        def _fn(*args, **kwargs):
            chamadas.setdefault(nome, []).append((args, kwargs))
            return estado[nome]()
        return _fn

    monkeypatch.setattr(performance_service, "get_quality", _registrar("quality"))
    monkeypatch.setattr(expedicao_service, "get_expedicao", _registrar("expedicao"))
    monkeypatch.setattr(expedicao_tiktok_service, "obter_serie", _registrar("tiktok"))
    monkeypatch.setattr(expedicao_shopee_ldr_service, "obter_ldr", _registrar("shopee"))
    return estado


@pytest.fixture()
def cliente(db):
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _get(cliente, **params):
    r = cliente.get(URL, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ind(corpo, codigo):
    for canal in corpo["canais"]:
        for ind in canal["indicadores"]:
            if ind["codigo"] == codigo:
                return ind
    raise AssertionError(f"indicador ausente: {codigo}")


def _canal(corpo, nome):
    return next(c for c in corpo["canais"] if c["canal"] == nome)


TORRE = ("ml_cancelamento_torre", "ml_nao_entregue_torre", "ml_expedicao_48h_torre",
         "shopee_ldr_operacional_torre", "tiktok_ldr_operacional_torre")


# ---------------------------------------------------------------------------
# Ordem, oficiais fixos e inercia
# ---------------------------------------------------------------------------
def test_ordem_fixa_dos_canais(cliente, fontes):
    corpo = _get(cliente)
    assert [c["canal"] for c in corpo["canais"]] == ["mercado_livre", "shopee", "tiktok"]
    assert corpo["gerado_em"]


def test_tres_oficiais_com_estados_fixos(cliente, fontes):
    corpo = _get(cliente)
    ml = _ind(corpo, "ml_reputacao_oficial")
    tk = _ind(corpo, "tiktok_sps_ahr_oficial")
    sh = _ind(corpo, "shopee_account_health_oficial")
    assert (ml["tipo"], ml["estado"], ml["valor"]) == ("oficial", "sem_fonte", None)
    assert "reputação oficial do mercado livre ainda não capturada pela torre" in (
        ml["motivo_indisponivel"].lower())
    assert (tk["tipo"], tk["estado"], tk["valor"]) == ("oficial", "sem_fonte", None)
    assert "não expõe sps/saúde da conta por api para lojas br" in tk["motivo_indisponivel"].lower()
    assert "26/10/2026" in tk["motivo_indisponivel"]
    assert (sh["tipo"], sh["estado"], sh["valor"]) == ("oficial", "nao_comprovado", None)
    assert "não comprovada" in sh["motivo_indisponivel"]
    assert "sem chamadas" in sh["motivo_indisponivel"]


def test_oficiais_nao_dependem_de_fonte(cliente, fontes):
    """Com TODAS as fontes caindo, os oficiais continuam nos estados fixos."""
    def _cai():
        raise RuntimeError(SEGREDO)
    for nome in fontes:
        fontes[nome] = _cai
    corpo = _get(cliente)
    assert _ind(corpo, "ml_reputacao_oficial")["estado"] == "sem_fonte"
    assert _ind(corpo, "tiktok_sps_ahr_oficial")["estado"] == "sem_fonte"
    assert _ind(corpo, "shopee_account_health_oficial")["estado"] == "nao_comprovado"
    for codigo in TORRE:
        assert _ind(corpo, codigo)["estado"] == "erro"


def test_router_nao_esta_registrado_no_main():
    main = Path(__file__).resolve().parents[1] / "app" / "main.py"
    texto = main.read_text(encoding="utf-8")
    assert "account_performance" not in texto
    assert "performance-contas" not in texto


def test_sem_banco_responde_503(fontes):
    app = FastAPI()
    app.include_router(rota.router)
    app.dependency_overrides[get_db] = lambda: None
    r = TestClient(app).get(URL)
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Estado `dado`
# ---------------------------------------------------------------------------
def test_mapeamento_dado_de_cada_indicador_torre(cliente, fontes):
    corpo = _get(cliente)

    c = _ind(corpo, "ml_cancelamento_torre")
    assert (c["estado"], c["valor"], c["unidade"], c["tipo"]) == ("dado", 4.5, "pct", "torre")
    assert c["limiar"] is None
    assert "qualquer autor" in c["rotulo"] and "não é o cancelamento da reputação ML" in c["rotulo"]
    assert c["atualizado_em"] and c["cobertura"] == "barbours, kokeshi"

    n = _ind(corpo, "ml_nao_entregue_torre")
    assert (n["estado"], n["valor"], n["unidade"]) == ("dado", 1.2, "pct")
    assert "por pedido" in n["rotulo"]

    e = _ind(corpo, "ml_expedicao_48h_torre")
    assert (e["estado"], e["valor"], e["unidade"]) == ("dado", 3.0, "contagem")
    assert e["limiar"] == {"valor": 48.0, "origem": "interno",
                           "rotulo": "48 h é limite interno, não SLA do ML"}
    assert e["aviso_frescor"] is None and e["atualizado_em"]

    t = _ind(corpo, "tiktok_ldr_operacional_torre")
    assert (t["estado"], t["valor"], t["unidade"]) == ("dado", 3.1, "pct")
    assert t["janela"] and "22/10/2026" in t["janela"]
    assert t["atualizado_em"] and t["aviso_frescor"] is None

    s = _ind(corpo, "shopee_ldr_operacional_torre")
    assert (s["estado"], s["valor"], s["tipo"]) == ("dado", 5.0, "torre")
    assert s["limiar"] is None and s["cobertura"] == "barbours, lescent"


def test_zero_medido_continua_dado(cliente, fontes):
    fontes["quality"] = lambda: quality_ok(cancel=0.0)
    corpo = _get(cliente)
    c = _ind(corpo, "ml_cancelamento_torre")
    assert (c["estado"], c["valor"]) == ("dado", 0.0)


def test_ml_quality_usa_janela_fechada_sem_comparacao(cliente, fontes, chamadas):
    _get(cliente)
    args, kwargs = chamadas["quality"][0]
    assert args[1] == "ml"
    assert kwargs["compare_period"] is None
    assert kwargs["brand_keys"] is None
    periodo = kwargs["period"]
    assert periodo.days == svc.JANELA_ML_DIAS
    # Uma chamada so' para os dois indicadores.
    assert len(chamadas["quality"]) == 1
    _, kw_exp = chamadas["expedicao"][0]
    assert kw_exp["channel"] == "mercadolivre" and kw_exp["include_queue"] is False


# ---------------------------------------------------------------------------
# null nunca vira 0
# ---------------------------------------------------------------------------
def test_null_do_ml_vira_vazio_legitimo_e_nunca_zero(cliente, fontes):
    fontes["quality"] = lambda: quality_ok(cancel=None, nao_entregue=None)
    corpo = _get(cliente)
    for codigo in ("ml_cancelamento_torre", "ml_nao_entregue_torre"):
        ind = _ind(corpo, codigo)
        assert ind["estado"] == "vazio_legitimo"
        assert ind["valor"] is None
        assert ind["motivo_indisponivel"]


def test_null_da_ldr_tiktok_vira_vazio_legitimo(cliente, fontes):
    fontes["tiktok"] = lambda: tiktok_ok(rate=None)
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("vazio_legitimo", None)


def test_null_da_ldr_shopee_sem_base_vira_vazio_legitimo(cliente, fontes):
    fontes["shopee"] = lambda: shopee_ok(rate=None, base=0)
    ind = _ind(_get(cliente), "shopee_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("vazio_legitimo", None)


def test_ldr_shopee_oculta_por_cobertura_nao_e_vazio_nem_zero(cliente, fontes):
    fontes["shopee"] = lambda: shopee_ok(rate=None, base=200, oculta="coverage_insufficient")
    ind = _ind(_get(cliente), "shopee_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert "cobertura insuficiente" in ind["motivo_indisponivel"]


def test_contagem_ausente_na_expedicao_vira_erro_nao_zero(cliente, fontes):
    def _sem_contagem():
        r = expedicao_ok()
        r["accounts"][0]["over_48h_count"] = None
        return r
    fontes["expedicao"] = _sem_contagem
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert (ind["estado"], ind["valor"]) == ("erro", None)


def test_nenhum_indicador_fora_de_dado_carrega_valor(cliente, fontes):
    fontes["quality"] = lambda: quality_ok(cancel=None, nao_entregue=None)
    fontes["expedicao"] = lambda: expedicao_indisponivel("channel_disabled")
    fontes["tiktok"] = lambda: tiktok_indisponivel("no_series_published")
    fontes["shopee"] = lambda: shopee_indisponivel("disabled")
    corpo = _get(cliente)
    for canal in corpo["canais"]:
        for ind in canal["indicadores"]:
            assert ind["estado"] != "dado"
            assert ind["valor"] is None


# ---------------------------------------------------------------------------
# Estados de serving -> sem_fonte
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("motivo", [
    "feature_flag_disabled", "channel_disabled", "no_snapshot_published",
    "inconsistent_batch",
])
def test_expedicao_indisponivel_vira_sem_fonte_com_motivo(cliente, fontes, motivo):
    fontes["expedicao"] = lambda: expedicao_indisponivel(motivo)
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert motivo in ind["motivo_indisponivel"]
    assert ind["limiar"]["origem"] == "interno"


def test_expedicao_motivo_desconhecido_nao_e_ecoado(cliente, fontes):
    fontes["expedicao"] = lambda: expedicao_indisponivel("<script>x</script>")
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert ind["estado"] == "sem_fonte"
    assert "<script>" not in ind["motivo_indisponivel"]


@pytest.mark.parametrize("motivo", ["table_not_migrated", "no_series_published"])
def test_tiktok_nao_publicada_vira_sem_fonte(cliente, fontes, motivo):
    fontes["tiktok"] = lambda: tiktok_indisponivel(motivo)
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert "não publicada" in ind["motivo_indisponivel"]


@pytest.mark.parametrize("motivo", ["disabled", "table_not_migrated", "no_series_published"])
def test_shopee_nao_publicada_vira_sem_fonte_e_nunca_oficial(cliente, fontes, motivo):
    fontes["shopee"] = lambda: shopee_indisponivel(motivo)
    ind = _ind(_get(cliente), "shopee_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"], ind["tipo"]) == ("sem_fonte", None, "torre")
    assert "não publicada" in ind["motivo_indisponivel"]
    assert "Não é métrica oficial" in ind["motivo_indisponivel"]


# ---------------------------------------------------------------------------
# Frescor
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("estado", ["stale", "critical", "unknown"])
def test_expedicao_desatualizada_gera_aviso(cliente, fontes, estado):
    fontes["expedicao"] = lambda: expedicao_ok(freshness=estado)
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert ind["estado"] == "dado"
    assert ind["aviso_frescor"] and f"barbours ({estado})" in ind["aviso_frescor"]


def test_expedicao_fotografia_velha_gera_aviso(cliente, fontes):
    fontes["expedicao"] = lambda: expedicao_ok(idade=30.0)
    ind = _ind(_get(cliente), "ml_expedicao_48h_torre")
    assert ind["aviso_frescor"] and "30 h" in ind["aviso_frescor"]


def test_tiktok_publicacao_acima_de_6h_gera_aviso(cliente, fontes):
    fontes["tiktok"] = lambda: tiktok_ok(idade_h=7.0)
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert ind["estado"] == "dado" and ind["aviso_frescor"]


def test_tiktok_publicacao_recente_sem_aviso(cliente, fontes):
    fontes["tiktok"] = lambda: tiktok_ok(idade_h=5.0)
    assert _ind(_get(cliente), "tiktok_ldr_operacional_torre")["aviso_frescor"] is None


def test_shopee_stale_gera_aviso(cliente, fontes):
    fontes["shopee"] = lambda: shopee_ok(stale=True)
    ind = _ind(_get(cliente), "shopee_ldr_operacional_torre")
    assert ind["estado"] == "dado" and ind["aviso_frescor"]


# ---------------------------------------------------------------------------
# Limiar: Torre nunca oficial
# ---------------------------------------------------------------------------
def test_referencia_tiktok_e_interna_nunca_oficial(cliente, fontes):
    for resposta in (tiktok_ok(), tiktok_ok(rate=None),
                     tiktok_indisponivel("no_series_published")):
        fontes["tiktok"] = lambda r=resposta: r
        ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
        assert ind["limiar"]["origem"] == "interno"
        assert ind["limiar"]["rotulo"] == "referência interna, sem fonte oficial"
        # Mesma unidade do indicador: 0,04 vira 4 pontos percentuais.
        assert ind["limiar"]["valor"] == pytest.approx(4.0)


def test_nenhum_indicador_torre_tem_limiar_oficial(cliente, fontes):
    corpo = _get(cliente)
    for canal in corpo["canais"]:
        for ind in canal["indicadores"]:
            if ind["tipo"] == "torre" and ind["limiar"] is not None:
                assert ind["limiar"]["origem"] != "oficial"


def test_schema_recusa_torre_com_limiar_oficial():
    with pytest.raises(ValueError):
        Indicador(codigo="x", rotulo="x", tipo="torre", estado="dado", valor=1.0,
                  fonte="x", ajuda="x",
                  limiar=Limiar(valor=4.0, origem="oficial", rotulo="x"))


def test_schema_recusa_valor_fora_do_estado_dado():
    with pytest.raises(ValueError):
        Indicador(codigo="x", rotulo="x", tipo="torre", estado="sem_fonte",
                  valor=0.0, fonte="x", ajuda="x")
    with pytest.raises(ValueError):
        Indicador(codigo="x", rotulo="x", tipo="torre", estado="dado",
                  valor=None, fonte="x", ajuda="x")


def test_prazo_reconstruido_aparece_na_ajuda(cliente, fontes):
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert "prazo reconstruído (2/1 dias úteis)" in ind["ajuda"]
    fontes["tiktok"] = lambda: tiktok_ok(reconstruido=False)
    ind = _ind(_get(cliente), "tiktok_ldr_operacional_torre")
    assert "prazo oficial TikTok" in ind["ajuda"]
    assert ind["limiar"]["origem"] == "interno"


# ---------------------------------------------------------------------------
# Excecao de uma fonte: so' ela vira erro, sem vazar texto
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fonte,afetados", [
    ("quality", {"ml_cancelamento_torre", "ml_nao_entregue_torre"}),
    ("expedicao", {"ml_expedicao_48h_torre"}),
    ("tiktok", {"tiktok_ldr_operacional_torre"}),
    ("shopee", {"shopee_ldr_operacional_torre"}),
])
def test_excecao_de_uma_fonte_so_afeta_ela(cliente, fontes, db, caplog, fonte, afetados):
    def _cai():
        raise RuntimeError(SEGREDO)
    fontes[fonte] = _cai
    r = cliente.get(URL)
    assert r.status_code == 200
    assert "NAO-PODE-VAZAR" not in r.text
    assert "NAO-PODE-VAZAR" not in caplog.text
    corpo = r.json()
    for codigo in TORRE:
        ind = _ind(corpo, codigo)
        if codigo in afetados:
            assert ind["estado"] == "erro"
            assert ind["valor"] is None
            assert ind["motivo_indisponivel"] == svc.MOTIVO_ERRO
        else:
            assert ind["estado"] == "dado", codigo
    # A transacao abortada e' liberada para a fonte seguinte.
    assert db.rollbacks >= 1


# ---------------------------------------------------------------------------
# Allowlist de marcas e cobertura
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("entrada", [
    "zzz-marca-inexistente", "barbours,<script>alert(1)</script>", " , ", "",
])
def test_brands_fora_da_allowlist_da_422_sem_eco(cliente, fontes, chamadas, entrada):
    r = cliente.get(URL, params={"brands": entrada})
    assert r.status_code == 422
    if entrada.strip(" ,"):
        assert "zzz-marca" not in r.text
        assert "<script>" not in r.text
    assert chamadas == {}


def test_brands_validas_sao_repassadas(cliente, fontes, chamadas):
    _get(cliente, brands="Barbours,kokeshi")
    assert chamadas["quality"][0][1]["brand_keys"] == ["barbours", "kokeshi"]
    assert chamadas["tiktok"][0][1]["brands"] == ["barbours", "kokeshi"]
    # Kokeshi nao tem app Shopee: so' barbours chega a fonte da Shopee.
    assert chamadas["shopee"][0][1]["brands"] == ["barbours"]


def test_brands_repetido_equivale_ao_csv(cliente, fontes, chamadas):
    r = cliente.get(URL, params=[("brands", "kokeshi"), ("brands", "apice,barbours")])
    assert r.status_code == 200, r.text
    assert chamadas["tiktok"][0][1]["brands"] == ["apice", "barbours", "kokeshi"]


def test_brands_repetido_com_valor_invalido_da_422_sem_eco(cliente, fontes, chamadas):
    r = cliente.get(URL, params=[("brands", "barbours"), ("brands", "<b>zzz</b>")])
    assert r.status_code == 422
    assert "<b>" not in r.text and "zzz" not in r.text
    assert chamadas == {}


def test_pct_em_pontos_percentuais_fracao_convertida(cliente, fontes):
    """Contrato da web: `pct` = pontos percentuais em `valor` E `limiar.valor`.

    get_quality ja' devolve pontos; as LDRs (TikTok e Shopee) e o
    `targets.tiktok_ldr` vem em FRACAO e precisam ser multiplicados por 100.
    """
    fontes["quality"] = lambda: quality_ok(cancel=4.5)
    fontes["tiktok"] = lambda: tiktok_ok(rate=0.0425)
    fontes["shopee"] = lambda: shopee_ok(rate=0.012)
    corpo = _get(cliente)
    assert _ind(corpo, "ml_cancelamento_torre")["valor"] == 4.5  # sem dupla conversao
    tk = _ind(corpo, "tiktok_ldr_operacional_torre")
    assert tk["valor"] == pytest.approx(4.25)
    assert tk["limiar"]["valor"] == pytest.approx(4.0)  # 0.04 -> 4.0
    assert _ind(corpo, "shopee_ldr_operacional_torre")["valor"] == pytest.approx(1.2)


def test_cobertura_padrao_por_canal(cliente, fontes):
    corpo = _get(cliente)
    ml = _canal(corpo, "mercado_livre")["cobertura"]
    assert ml["marcas"] == ["barbours", "kokeshi", "lescent", "rituaria"]
    assert [f["marca"] for f in ml["fora"]] == ["apice"]
    sh = _canal(corpo, "shopee")["cobertura"]
    assert "kokeshi" not in sh["marcas"]
    assert [f["marca"] for f in sh["fora"]] == ["kokeshi"]
    tk = _canal(corpo, "tiktok")["cobertura"]
    assert tk["fora"] == []


def test_so_apice_nao_consulta_fontes_ml(cliente, fontes, chamadas):
    corpo = _get(cliente, brands="apice")
    assert "quality" not in chamadas and "expedicao" not in chamadas
    for codigo in ("ml_cancelamento_torre", "ml_nao_entregue_torre", "ml_expedicao_48h_torre"):
        ind = _ind(corpo, codigo)
        assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert _canal(corpo, "mercado_livre")["cobertura"]["fora"][0]["marca"] == "apice"


def test_so_kokeshi_nao_consulta_ldr_shopee(cliente, fontes, chamadas):
    corpo = _get(cliente, brands="kokeshi")
    assert "shopee" not in chamadas
    ind = _ind(corpo, "shopee_ldr_operacional_torre")
    assert (ind["estado"], ind["valor"]) == ("sem_fonte", None)
    assert _ind(corpo, "shopee_account_health_oficial")["estado"] == "nao_comprovado"


def test_expedicao_filtra_contas_pela_marca(cliente, fontes):
    corpo = _get(cliente, brands="kokeshi")
    ind = _ind(corpo, "ml_expedicao_48h_torre")
    assert (ind["estado"], ind["valor"], ind["cobertura"]) == ("dado", 1.0, "kokeshi")
