"""Gate S3 — contraprovas da troca de fonte de /inteligencia e /brand-detail.

Os dois arquivos de contrato congelado (`test_inteligencia_contract.py` e
`test_brand_detail_contract.py`) provam que o PAYLOAD nao mudou. Este arquivo
prova a outra metade: que as consultas deixaram de tocar o Data Mart e passaram a
ser executadas pela Session do Neon.

A garantia central de `/operacoes` vale aqui do mesmo modo: `_uses_datamart()`
roteia para o `datamart_engine` qualquer SQL que contenha `gold.` ou `raw.`. Com
`marts.*` a funcao devolve `False` e o `_query` usa a Session. A garantia e' tao
forte quanto "nenhum texto de consulta contem gold./raw." — e e' exatamente isso
que os testes por funcao travam.
"""
from __future__ import annotations

import ast
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.routers import performance as rp
from app.services import gold_service as gs

MODULE_PATH = Path(gs.__file__)
FONTE = MODULE_PATH.read_text(encoding="utf-8")
LINHAS = FONTE.splitlines(keepends=True)
ARVORE = ast.parse(FONTE)

MIGRADAS = ("get_inteligencia", "get_brand_detail")

#: As fontes que cada rota passou a usar.
ESPERADAS = {
    "get_inteligencia": {
        "marts.fact_ml_produto_ranking",
        "marts.fact_ml_cross_company_summary",
        "marts.fact_tiktok_product_daily",
    },
    "get_brand_detail": {
        "marts.fact_tiktok_brand_content_daily",
        "marts.fact_tiktok_creator_daily",
        "marts.fact_tiktok_product_daily",
        "marts.fact_tiktok_channel_efficiency_daily",
    },
}


def corpo(nome: str) -> str:
    """Corpo da ULTIMA definicao da funcao (o arquivo tem duplicatas de OUTRAS
    funcoes, nao destas duas; a busca pela ultima e' defensiva)."""
    faixas = [(n.lineno, n.end_lineno) for n in ARVORE.body
              if isinstance(n, ast.FunctionDef) and n.name == nome]
    assert faixas, f"{nome} nao encontrada"
    a, b = faixas[-1]
    return "".join(LINHAS[a - 1:b])


# ===========================================================================
# Zero gold., zero raw., por funcao
# ===========================================================================

@pytest.mark.parametrize("nome", MIGRADAS)
def test_g01_zero_gold_na_funcao(nome):
    assert "gold." not in corpo(nome)


@pytest.mark.parametrize("nome", MIGRADAS)
def test_g02_zero_raw_na_funcao(nome):
    assert "raw." not in corpo(nome)


@pytest.mark.parametrize("nome", MIGRADAS)
def test_g03_as_fontes_sao_exatamente_as_esperadas(nome):
    achadas = set(re.findall(r"\bmarts\.[a-z_0-9]+", corpo(nome)))
    assert achadas == ESPERADAS[nome], f"{nome}: {achadas ^ ESPERADAS[nome]}"


@pytest.mark.parametrize("nome", MIGRADAS)
def test_g04_uses_datamart_e_false_para_todas_as_consultas_da_funcao(nome):
    """Percorre cada relacao citada e confirma que o roteador manda para a
    Session, nao para o `datamart_engine`."""
    for tabela in ESPERADAS[nome]:
        assert gs._uses_datamart(f"SELECT 1 FROM {tabela}") is False


def test_g05_uses_datamart_continua_true_para_gold_e_raw():
    """A funcao nao foi afrouxada: ela ainda roteia gold./raw. para o Data Mart —
    o que mudou foi o SQL das duas rotas."""
    assert gs._uses_datamart("SELECT 1 FROM gold.qualquer") is True
    assert gs._uses_datamart("SELECT 1 FROM raw.qualquer") is True


def test_g06_as_rotas_que_seguem_no_data_mart_nao_foram_tocadas():
    """`/tempo-real` esta fora do S3 por exigir serving intraday: tem de continuar
    lendo a Gold, sem alteracao."""
    assert "gold.tiktok_shop_hourly" in corpo("get_tempo_real")


def test_g07_outras_funcoes_que_usam_tiktok_product_daily_seguem_na_gold():
    """`gold.tiktok_product_daily` aparece em mais de uma funcao. A troca foi
    cirurgica: somente dentro das duas rotas migradas."""
    outras = [n.name for n in ARVORE.body
              if isinstance(n, ast.FunctionDef) and n.name not in MIGRADAS
              and "gold.tiktok_product_daily" in corpo(n.name)]
    assert outras, "esperado que ao menos uma outra funcao siga na Gold"


# ===========================================================================
# Funcionamento com datamart_engine = None
# ===========================================================================

class SessaoFalsa:
    """Session minima: registra as consultas e devolve resultado vazio."""

    def __init__(self):
        self.consultas: list[str] = []

    def execute(self, clausula):
        self.consultas.append(str(clausula))
        return _ResultadoVazio()


class _ResultadoVazio:
    def mappings(self):
        return []


def test_g08_inteligencia_atravessa_inteira_sem_datamart(monkeypatch):
    monkeypatch.setattr(gs, "datamart_engine", None)
    sessao = SessaoFalsa()
    out = gs.get_inteligencia(sessao)
    assert list(out) == ["signals", "urgent", "scale", "organic", "pareto", "ltv", "tk_products"]
    assert len(sessao.consultas) == 7, sessao.consultas


def test_g09_brand_detail_atravessa_inteira_sem_datamart(monkeypatch):
    monkeypatch.setattr(gs, "datamart_engine", None)
    sessao = SessaoFalsa()
    out = gs.get_brand_detail(sessao, "kokeshi", 2026, 7)
    assert out["brand"] == "kokeshi"
    assert out["channel_funnel"] == [] and out["top_produtos"] == []
    assert len(sessao.consultas) == 5, sessao.consultas


@pytest.mark.parametrize("nome,chamada", [
    ("get_inteligencia", lambda s: gs.get_inteligencia(s)),
    ("get_brand_detail", lambda s: gs.get_brand_detail(s, "kokeshi", 2026, 7)),
])
def test_g10_todas_as_consultas_passam_pela_session(monkeypatch, nome, chamada):
    """Nao basta `datamart_engine=None` nao explodir: cada consulta tem de ter
    ido para a Session."""
    monkeypatch.setattr(gs, "datamart_engine", None)
    sessao = SessaoFalsa()
    chamada(sessao)
    assert sessao.consultas, "nenhuma consulta chegou a Session"
    for sql in sessao.consultas:
        baixo = sql.lower()
        assert "gold." not in baixo
        assert "raw." not in baixo
        assert "marts." in baixo


def test_g11_ltv_degrada_mas_as_outras_seis_consultas_vao_a_session(monkeypatch):
    """`ltv` esta em try/except. Mesmo se a fonte dele falhasse, as outras seis
    continuam na Session — nenhuma cai para o Data Mart."""
    monkeypatch.setattr(gs, "datamart_engine", None)
    sessao = SessaoFalsa()
    original = sessao.execute

    def execute(clausula):
        if "cross_company" in str(clausula):
            raise RuntimeError("fonte de ltv indisponivel")
        return original(clausula)

    sessao.execute = execute
    out = gs.get_inteligencia(sessao)
    assert out["ltv"] == []
    assert len(sessao.consultas) == 6


# ===========================================================================
# Fuso — a unica outra mudanca intencional em /inteligencia
# ===========================================================================

def _sem_comentarios(texto: str) -> str:
    """Remove comentarios de linha. O comentario que documenta a correcao cita
    `date.today()` justamente para explicar que ele saiu — um grep no texto bruto
    casaria com a explicacao, nao com uma violacao."""
    return chr(10).join(l.split("#", 1)[0] for l in texto.splitlines())


def test_g12_janela_de_30_dias_vem_do_dia_operacional_no_fuso_do_brasil():
    codigo = _sem_comentarios(corpo("get_inteligencia"))
    assert "_hoje_operacional()" in codigo
    assert "date.today()" not in codigo
    assert "utcnow()" not in codigo


def test_g13_hoje_operacional_resolve_no_fuso_de_sao_paulo():
    agora = datetime(2026, 8, 19, 0, 5, tzinfo=timezone.utc)
    assert gs._hoje_operacional(agora) == date(2026, 8, 18)


def test_g14_fronteira_utc_brt_desloca_a_janela_de_30_dias(monkeypatch):
    """As 00:05 UTC de 19/08 o dia no Brasil ainda e' 18/08. Com `date.today()`
    a janela comecaria em 20/07; com o fuso correto, em 19/07."""
    agora = datetime(2026, 8, 19, 0, 5, tzinfo=timezone.utc)
    hoje_br = gs._hoje_operacional(agora)
    assert hoje_br == date(2026, 8, 18)
    monkeypatch.setattr(gs, "_hoje_operacional", lambda *a, **k: hoje_br)
    monkeypatch.setattr(gs, "datamart_engine", None)
    sessao = SessaoFalsa()
    gs.get_inteligencia(sessao)
    janela = [s for s in sessao.consultas if "pct_gmv_video" in s][0]
    assert str(hoje_br - timedelta(days=30)) in janela
    assert "2026-07-19" in janela


def test_g15_zoneinfo_e_biblioteca_padrao_zero_dependencia_nova():
    assert "from zoneinfo import ZoneInfo" in FONTE
    for externa in ("pytz", "dateutil", "pendulum", "arrow"):
        assert externa not in FONTE


# ===========================================================================
# Nenhum endpoint, campo ou schema mexido
# ===========================================================================

def test_g16_os_tres_endpoints_continuam_registrados():
    caminhos = {getattr(r, "path", "") for r in rp.router.routes}
    for rota in ("/api/v1/performance/inteligencia",
                 "/api/v1/performance/brand-detail",
                 "/api/v1/performance/tempo-real",
                 "/api/v1/performance/operacoes"):
        assert rota in caminhos, rota


def test_g17_response_models_inalterados():
    from app.schemas.performance import BrandDetailResponse, TempoRealResponse
    por_path = {getattr(r, "path", ""): r for r in rp.router.routes}
    assert por_path["/api/v1/performance/brand-detail"].response_model is BrandDetailResponse
    assert por_path["/api/v1/performance/tempo-real"].response_model is TempoRealResponse
    assert getattr(por_path["/api/v1/performance/inteligencia"], "response_model", None) is None


def test_g18_nenhum_campo_novo_nos_schemas():
    from app.schemas.performance import BrandDetailResponse, TempoRealResponse
    assert len(TempoRealResponse.model_fields) == 5
    assert len(BrandDetailResponse.model_fields) == 37
    for campo in BrandDetailResponse.model_fields:
        assert not any(t in campo.lower() for t in
                       ("refreshed", "synced", "source_max", "stale", "coverage")), campo


def test_g19_a_assinatura_das_duas_funcoes_nao_mudou():
    import inspect
    assert list(inspect.signature(gs.get_inteligencia).parameters) == ["db"]
    assert list(inspect.signature(gs.get_brand_detail).parameters) == ["db", "brand", "year", "month"]


# ===========================================================================
# GMV, frete, Shopee e definicoes duplicadas
# ===========================================================================

@pytest.mark.parametrize("nome", MIGRADAS)
def test_g20_nenhuma_redefinicao_de_gmv_ou_frete(nome):
    baixo = corpo(nome).lower()
    for termo in ("sub_total", "frete", "shipping", "freight"):
        assert termo not in baixo, f"{nome}: {termo}"


@pytest.mark.parametrize("nome", MIGRADAS)
def test_g21_zero_shopee(nome):
    assert "shopee" not in corpo(nome).lower()


def test_g22_definicoes_duplicadas_nao_relacionadas_nao_foram_refatoradas():
    """O arquivo tem `get_canais` 3x, `get_quality` 3x etc. Sao armadilha de
    leitura, mas refatorar isso nao e' escopo do S3 — e mexer nelas ampliaria o
    diff sem pedido."""
    nomes = [n.name for n in ARVORE.body if isinstance(n, ast.FunctionDef)]
    duplicadas = {n for n in nomes if nomes.count(n) > 1}
    assert "get_canais" in duplicadas
    assert "get_quality" in duplicadas
    assert not (duplicadas & set(MIGRADAS)), "as duas rotas migradas tem definicao unica"


def test_g23_a_troca_ficou_restrita_as_duas_funcoes():
    """Contagem de `gold.` no arquivo: caiu exatamente pelas 12 consultas
    migradas, e o resto do arquivo permanece intocado."""
    total_marts = len(re.findall(r"\bmarts\.fact_ml_cross_company_summary", FONTE))
    total_canal = len(re.findall(r"\bmarts\.fact_tiktok_channel_efficiency_daily", FONTE))
    assert total_marts == 1, "a nova fato ML aparece so' em /inteligencia"
    assert total_canal == 1, "a nova fato de canal aparece so' em /brand-detail"


def test_g24_zero_frontend_referenciado():
    """A troca e' de backend. Nenhum arquivo web e' tocado por ela."""
    raiz = MODULE_PATH.resolve().parents[4]
    assert (raiz / "apps" / "web").exists()
    for nome in MIGRADAS:
        assert "apps/web" not in corpo(nome)
        assert ".tsx" not in corpo(nome)
