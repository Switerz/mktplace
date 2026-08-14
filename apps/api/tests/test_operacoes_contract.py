"""Gate S2 — CONTRATO CONGELADO do payload de `/operacoes`.

Por que este arquivo existe
---------------------------
O Gate S2 vai trocar a fonte de `/operacoes` de `gold.*` (Data Mart, VPN) para
`marts.*` (Neon). A unica forma de provar que a troca nao muda o payload e' ter,
ANTES da troca, um teste que descreva o payload atual campo a campo. Depois da
troca (Task 3/3) este mesmo arquivo tem de continuar verde sem uma unica
alteracao de expectativa — se precisar ser editado, a troca mudou o contrato.

Estado apos a Task 3/3: `get_operacoes` le `marts.*` no Neon e todas as cinco
janelas tem TETO INCLUSIVO em D-1 (fuso America/Sao_Paulo). Foram as UNICAS duas
mudancas intencionais; nenhuma expectativa de payload mudou.

Determinismo
------------
Nao depende de producao (que hoje responde 500 no Render por nao alcancar o Data
Mart) nem de banco algum. Duas coisas sao fixadas:

- `gold_service._query` e' substituida por um despachante sobre fixtures fixas.
  E' o ponto certo de intercepcao para os testes de payload; ha tambem um teste
  que NAO o substitui, para provar que as consultas passam pela Session do Neon;
- `_hoje_operacional()` e' congelada em 11/08/2026: as janelas de 7, 14 e 30 dias
  e o teto D-1 sao calculados a partir dela, no fuso America/Sao_Paulo.

Os fixtures foram escolhidos para exercitar as regras que passam despercebidas:
as duas regras de alerta e o caso que nao gera alerta, o arredondamento de cada
campo, o **falsy-para-None** das razoes, o `HAVING` das lives, o `LIMIT 30` dos
criadores, a ordenacao e as listas vazias.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import gold_service as gs

HOJE = date(2026, 8, 11)
D7 = HOJE - timedelta(days=7)    # 2026-08-04
D14 = HOJE - timedelta(days=14)  # 2026-07-28
D30 = HOJE - timedelta(days=30)  # 2026-07-12
D_FECHADO = HOJE - timedelta(days=1)  # 2026-08-10 — teto INCLUSIVO de todas


#: A data operacional deixou de vir de `date.today()`: vem de
#: `_hoje_operacional()`, que resolve o dia em America/Sao_Paulo. Congelar aqui e'
#: congelar exatamente o que a producao consulta.
def _congela_data(monkeypatch) -> None:
    monkeypatch.setattr(gs, "_hoje_operacional", lambda *a, **k: HOJE)


def _classifica(sql: str) -> str:
    """Identifica qual das cinco consultas de `get_operacoes` esta sendo feita."""
    s = " ".join(sql.split()).lower()
    if "marts.fact_ml_gestao_diaria" in s:
        return "velocity" if "group by brand" in s else "gestao"
    if "marts.fact_tiktok_creator_daily" in s:
        return "creators"
    if "marts.fact_tiktok_brand_content_daily" in s:
        return "lives" if "having" in s else "tk_daily"
    raise AssertionError(f"consulta inesperada em get_operacoes: {s[:120]}")


@pytest.fixture
def fixar(monkeypatch):
    """Instala data congelada e despachante de fixtures. Devolve o dicionario de
    fixtures para o teste ajustar."""
    dados: dict[str, list[dict]] = {
        "gestao": [], "velocity": [], "creators": [], "lives": [], "tk_daily": [],
    }
    vistas: list[str] = []

    def fake_query(db, sql):
        tipo = _classifica(sql)
        vistas.append(tipo)
        return [dict(r) for r in dados[tipo]]

    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query", fake_query)
    dados["_vistas"] = vistas  # type: ignore[assignment]
    return dados


# ---------------------------------------------------------------------------
# Forma geral do payload
# ---------------------------------------------------------------------------

def test_c01_payload_tem_exatamente_cinco_blocos(fixar):
    out = gs.get_operacoes(db=None)
    assert list(out.keys()) == ["alertas", "ml_velocity", "creators", "lives", "tk_daily"]


def test_c02_listas_vazias_sao_listas_e_nao_none(fixar):
    out = gs.get_operacoes(db=None)
    for k in ("alertas", "ml_velocity", "creators", "lives", "tk_daily"):
        assert out[k] == [], f"{k} deveria ser lista vazia"


def test_c03_as_cinco_consultas_sao_feitas(fixar):
    gs.get_operacoes(db=None)
    assert set(fixar["_vistas"]) == {"gestao", "velocity", "creators", "lives", "tk_daily"}


# ---------------------------------------------------------------------------
# Janelas de 7, 14 e 30 dias
# ---------------------------------------------------------------------------

def test_c04_janelas_de_7_14_e_30_dias(monkeypatch):
    capturado = {}

    def fake_query(db, sql):
        s = " ".join(sql.split())
        capturado[_classifica(sql)] = s
        return []

    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query", fake_query)
    gs.get_operacoes(db=None)
    # Limites INFERIORES nominais preservados (7, 14 e 30 dias). O tamanho efetivo
    # da janela mudou: antes, sem teto, o dia corrente podia elevar para 8/15/31
    # datas; agora sao exatamente 7/14/30 dias fechados.
    assert f"ref_date >= '{D7}'" in capturado["gestao"]
    assert f"ref_date >= '{D7}'" in capturado["velocity"]
    assert f"date >= '{D7}'" in capturado["creators"]
    assert f"date >= '{D30}'" in capturado["lives"]
    assert f"date >= '{D14}'" in capturado["tk_daily"]
    # teto INCLUSIVO em D-1 nas CINCO: o dia corrente nunca entra
    assert f"ref_date <= '{D_FECHADO}'" in capturado["gestao"]
    assert f"ref_date <= '{D_FECHADO}'" in capturado["velocity"]
    assert f"date <= '{D_FECHADO}'" in capturado["creators"]
    assert f"date <= '{D_FECHADO}'" in capturado["lives"]
    assert f"date <= '{D_FECHADO}'" in capturado["tk_daily"]
    for sql in capturado.values():
        assert f"'{HOJE}'" not in sql, "a data de hoje nao pode aparecer em consulta alguma"


def test_c05_marcas_filtradas_por_escopo(monkeypatch):
    capturado = {}
    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query",
                        lambda db, sql: capturado.setdefault(_classifica(sql), " ".join(sql.split())) and [])
    gs.get_operacoes(db=None)
    for k in ("gestao", "velocity"):
        for m in gs.ML_BRANDS:
            assert f"'{m}'" in capturado[k]
        assert "'apice'" not in capturado[k], "ML nao inclui apice"
    for k in ("creators", "lives", "tk_daily"):
        for m in gs.BRANDS_IN_SCOPE:
            assert f"'{m}'" in capturado[k]


# ---------------------------------------------------------------------------
# Bloco `alertas` — as duas regras e o caso silencioso
# ---------------------------------------------------------------------------

def test_c06_alerta_ad_sem_gmv_e_critico(fixar):
    fixar["gestao"] = [{"brand": "kokeshi", "ref_date": date(2026, 8, 9),
                        "ad_spend": 1234.56, "gmv": 0, "roas": 0}]
    out = gs.get_operacoes(db=None)
    assert out["alertas"] == [{
        "tipo": "ad_sem_gmv",
        "severidade": "critico",
        "brand": "kokeshi",
        "mensagem": "KOKESHI teve R$1,235 em ads sem nenhuma venda em 2026-08-09",
        "ad_spend": 1234.56,
        "gmv": 0.0,
    }]
    assert "roas" not in out["alertas"][0], "ad_sem_gmv nao carrega roas"


def test_c07_alerta_roas_baixo_e_atencao_e_arredonda_em_2(fixar):
    fixar["gestao"] = [{"brand": "lescent", "ref_date": date(2026, 8, 7),
                        "ad_spend": 900.4, "gmv": 2000, "roas": 2.456}]
    out = gs.get_operacoes(db=None)
    assert out["alertas"] == [{
        "tipo": "roas_baixo",
        "severidade": "atencao",
        "brand": "lescent",
        "mensagem": "LESCENT com ROAS 2.5x em 2026-08-07 (abaixo de 3x, investimento R$900)",
        "ad_spend": 900.4,
        "gmv": 2000.0,
        "roas": 2.46,
    }]


@pytest.mark.parametrize("spend,gmv,roas", [
    (0, 0, 0),          # sem investimento: nao alerta nem com gmv zero
    (500, 100, 1.0),    # spend NAO e' > 500
    (600, 100, 3.0),    # roas NAO e' < 3
    (600, 100, 5.0),    # roas saudavel
])
def test_c08_casos_que_nao_geram_alerta(fixar, spend, gmv, roas):
    fixar["gestao"] = [{"brand": "kokeshi", "ref_date": date(2026, 8, 9),
                        "ad_spend": spend, "gmv": gmv, "roas": roas}]
    assert gs.get_operacoes(db=None)["alertas"] == []


def test_c09_ad_sem_gmv_tem_precedencia_sobre_roas_baixo(fixar):
    """`elif`: gasto sem venda nunca e' rebaixado para atencao."""
    fixar["gestao"] = [{"brand": "kokeshi", "ref_date": date(2026, 8, 9),
                        "ad_spend": 9000, "gmv": 0, "roas": 0}]
    alertas = gs.get_operacoes(db=None)["alertas"]
    assert [a["tipo"] for a in alertas] == ["ad_sem_gmv"]


def test_c10_ordem_dos_alertas_segue_a_ordem_das_linhas(fixar):
    fixar["gestao"] = [
        {"brand": "kokeshi", "ref_date": date(2026, 8, 9), "ad_spend": 10, "gmv": 0, "roas": 0},
        {"brand": "lescent", "ref_date": date(2026, 8, 8), "ad_spend": 600, "gmv": 1, "roas": 1},
    ]
    assert [a["brand"] for a in gs.get_operacoes(db=None)["alertas"]] == ["kokeshi", "lescent"]


def test_c11_marca_sem_label_usa_o_proprio_codigo(fixar):
    fixar["gestao"] = [{"brand": "novamarca", "ref_date": date(2026, 8, 9),
                        "ad_spend": 100, "gmv": 0, "roas": 0}]
    assert "novamarca teve" in gs.get_operacoes(db=None)["alertas"][0]["mensagem"]


# ---------------------------------------------------------------------------
# Bloco `ml_velocity`
# ---------------------------------------------------------------------------

def test_c12_ml_velocity_tipos_e_arredondamento(fixar):
    fixar["velocity"] = [{"brand": "barbours", "ad_spend_7d": 1000.5, "gmv_7d": 5000.25,
                          "orders_7d": 42.0, "roas_7d": 4.987}]
    assert gs.get_operacoes(db=None)["ml_velocity"] == [{
        "brand": "barbours",
        "ad_spend_7d": 1000.5,
        "gmv_7d": 5000.25,
        "orders_7d": 42,
        "roas_7d": 4.99,
    }]
    assert isinstance(gs.get_operacoes(db=None)["ml_velocity"][0]["orders_7d"], int)


@pytest.mark.parametrize("valor", [None, 0, 0.0])
def test_c13_roas_7d_falsy_vira_none(fixar, valor):
    """Comportamento vigente: `if r.get("roas_7d")` trata 0 como ausente.

    Congelado de proposito. Nao e' o que se escreveria hoje, mas mudar isso na
    troca de fonte alteraria o payload — e o contrato e' payload identico.
    """
    fixar["velocity"] = [{"brand": "barbours", "ad_spend_7d": 0, "gmv_7d": 0,
                          "orders_7d": 0, "roas_7d": valor}]
    assert gs.get_operacoes(db=None)["ml_velocity"][0]["roas_7d"] is None


# ---------------------------------------------------------------------------
# Bloco `creators`
# ---------------------------------------------------------------------------

def test_c14_creators_campos_tipos_e_gpm(fixar):
    fixar["creators"] = [{"brand": "apice", "creator": "criador_a", "gmv": 1500.75,
                          "views": 20000.0, "videos": 12.0, "lives": 3.0,
                          "gmv_video": 1000.5, "gmv_live": 500.25, "gpm_video": 50.037}]
    assert gs.get_operacoes(db=None)["creators"] == [{
        "brand": "apice",
        "creator": "criador_a",
        "gmv": 1500.75,
        "views": 20000,
        "videos": 12,
        "lives": 3,
        "gmv_video": 1000.5,
        "gmv_live": 500.25,
        "gpm_video": 50.04,
    }]


def test_c15_gpm_video_falsy_vira_none(fixar):
    fixar["creators"] = [{"brand": "apice", "creator": "c", "gmv": 0, "views": 0,
                          "videos": 0, "lives": 0, "gmv_video": 0, "gmv_live": 0,
                          "gpm_video": None}]
    assert gs.get_operacoes(db=None)["creators"][0]["gpm_video"] is None


def test_c16_creators_tem_limite_de_30_e_ordena_por_gmv(monkeypatch):
    capturado = {}
    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query",
                        lambda db, sql: capturado.setdefault(_classifica(sql), " ".join(sql.split())) and [])
    gs.get_operacoes(db=None)
    assert "LIMIT 30" in capturado["creators"]
    assert "ORDER BY SUM(gmv_total) DESC" in capturado["creators"]
    assert "GROUP BY brand, creator" in capturado["creators"]


def test_c17_creators_preserva_a_ordem_recebida(fixar):
    fixar["creators"] = [
        {"brand": "apice", "creator": f"c{i}", "gmv": 100 - i, "views": 1, "videos": 1,
         "lives": 0, "gmv_video": 1, "gmv_live": 0, "gpm_video": None}
        for i in range(3)
    ]
    assert [c["creator"] for c in gs.get_operacoes(db=None)["creators"]] == ["c0", "c1", "c2"]


# ---------------------------------------------------------------------------
# Bloco `lives`
# ---------------------------------------------------------------------------

def test_c18_lives_campos_e_arredondamentos_distintos(fixar):
    fixar["lives"] = [{"brand": "apice", "days_with_lives": 12.0, "total_lives": 40.0,
                       "total_minutes": 2400.0, "live_gmv": 8000.5, "total_gmv": 20000.0,
                       "pct_live": 40.025, "gmv_per_live": 200.0125,
                       "gmv_per_minute": 3.3335}]
    assert gs.get_operacoes(db=None)["lives"] == [{
        "brand": "apice",
        "days_with_lives": 12,
        "total_lives": 40,
        "total_minutes": 2400,
        "live_gmv": 8000.5,
        "total_gmv": 20000.0,
        "pct_live": 40.0,        # 1 casa
        "gmv_per_live": 200.01,  # 2 casas
        "gmv_per_minute": 3.33,  # 2 casas
    }]


def test_c19_lives_exige_having_total_lives_positivo(monkeypatch):
    capturado = {}
    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query",
                        lambda db, sql: capturado.setdefault(_classifica(sql), " ".join(sql.split())) and [])
    gs.get_operacoes(db=None)
    assert "HAVING SUM(total_lives) > 0" in capturado["lives"]
    assert "ORDER BY SUM(gmv_live) DESC" in capturado["lives"]
    assert "COUNT(DISTINCT date)" in capturado["lives"]


def test_c20_lives_razoes_falsy_viram_none(fixar):
    fixar["lives"] = [{"brand": "apice", "days_with_lives": 1, "total_lives": 1,
                       "total_minutes": 0, "live_gmv": 0, "total_gmv": 0,
                       "pct_live": None, "gmv_per_live": 0, "gmv_per_minute": None}]
    out = gs.get_operacoes(db=None)["lives"][0]
    assert out["pct_live"] is None
    assert out["gmv_per_live"] is None
    assert out["gmv_per_minute"] is None


def test_c21_lives_tolera_minutos_negativos_da_fonte(fixar):
    """A origem tem 2 dias com `total_live_minutes` negativo (03/04 e 06/05/2026).

    Fora da janela de 30 dias hoje, mas o payload precisa continuar sendo o mesmo
    depois da troca de fonte, inclusive neste caso degenerado.
    """
    fixar["lives"] = [{"brand": "apice", "days_with_lives": 2, "total_lives": 5,
                       "total_minutes": -29545461.0, "live_gmv": 100.0,
                       "total_gmv": 200.0, "pct_live": 50.0, "gmv_per_live": 20.0,
                       "gmv_per_minute": -0.0000034}]
    out = gs.get_operacoes(db=None)["lives"][0]
    assert out["total_minutes"] == -29545461
    assert out["gmv_per_minute"] == -0.0


# ---------------------------------------------------------------------------
# Bloco `tk_daily`
# ---------------------------------------------------------------------------

def test_c22_tk_daily_serializa_data_como_texto(fixar):
    fixar["tk_daily"] = [{"brand": "apice", "ref_date": date(2026, 8, 1),
                          "gmv": 1234.5, "orders": 10.0}]
    assert gs.get_operacoes(db=None)["tk_daily"] == [
        {"brand": "apice", "ref_date": "2026-08-01", "gmv": 1234.5, "orders": 10}
    ]


def test_c23_tk_daily_ordena_por_data_e_marca(monkeypatch):
    capturado = {}
    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query",
                        lambda db, sql: capturado.setdefault(_classifica(sql), " ".join(sql.split())) and [])
    gs.get_operacoes(db=None)
    assert "GROUP BY brand, date" in capturado["tk_daily"]
    assert "ORDER BY date, brand" in capturado["tk_daily"]


# ---------------------------------------------------------------------------
# Fronteira do Gate S2 Task 1/3
# ---------------------------------------------------------------------------

def test_c24_get_operacoes_le_exclusivamente_marts(monkeypatch):
    """Fronteira invertida na Task 3/3: a fonte agora e' o Neon, e so ele."""
    vistos = []
    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query", lambda db, sql: vistos.append(" ".join(sql.split())) or [])
    gs.get_operacoes(db=None)
    assert len(vistos) == 5
    for sql in vistos:
        assert "gold." not in sql, "nenhuma consulta pode citar gold."
        assert "raw." not in sql, "nenhuma consulta pode citar raw."
        assert "marts." in sql
    esperadas = {"marts.fact_ml_gestao_diaria",
                 "marts.fact_tiktok_brand_content_daily",
                 "marts.fact_tiktok_creator_daily"}
    usadas = {r for sql in vistos for r in esperadas if r in sql}
    assert usadas == esperadas


# ---------------------------------------------------------------------------
# Provas da Task 3/3: isolamento do Neon e politica temporal
# ---------------------------------------------------------------------------

def test_c26_nenhuma_consulta_roteia_para_o_data_mart(monkeypatch):
    """`_uses_datamart` e' o roteador: se ele disser False, `_query` usa a Session."""
    vistos = []
    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query", lambda db, sql: vistos.append(sql) or [])
    gs.get_operacoes(db=None)
    for sql in vistos:
        assert gs._uses_datamart(sql) is False, "consulta rotearia ao Data Mart"


def test_c27_endpoint_funciona_com_datamart_engine_ausente(monkeypatch):
    """Com `datamart_engine = None`, `_query` levanta se alguem tentar a gold.

    Aqui o caminho novo tem de atravessar inteiro usando so a Session — e' a
    prova de que o Gate G4 deixa de bloquear `/operacoes`.
    """
    class SessaoFalsa:
        def __init__(self):
            self.consultas = []

        def execute(self, clausula):
            self.consultas.append(str(clausula))
            return _ResultadoVazio()

    class _ResultadoVazio:
        def mappings(self):
            return []

    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "datamart_engine", None)
    sessao = SessaoFalsa()
    out = gs.get_operacoes(db=sessao)
    assert list(out.keys()) == ["alertas", "ml_velocity", "creators", "lives", "tk_daily"]
    assert all(v == [] for v in out.values())
    assert len(sessao.consultas) == 5, "as cinco consultas passaram pela Session"
    for sql in sessao.consultas:
        assert "marts." in sql and "gold." not in sql


def test_c28_dia_operacional_usa_america_sao_paulo():
    """O dia vem do fuso do NEGOCIO, nao do processo."""
    assert str(gs.TZ_OPERACIONAL) == "America/Sao_Paulo"
    # 00:05 UTC de 12/08 ainda e' 11/08 no Brasil (UTC-3)
    assert gs._hoje_operacional(datetime(2026, 8, 12, 0, 5, tzinfo=timezone.utc)) == date(2026, 8, 11)
    assert gs._hoje_operacional(datetime(2026, 8, 12, 2, 59, tzinfo=timezone.utc)) == date(2026, 8, 11)
    # 03:00 UTC ja e' 00:00 no Brasil: o dia virou
    assert gs._hoje_operacional(datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc)) == date(2026, 8, 12)


def test_c29_meia_noite_utc_nao_adianta_o_dia_operacional():
    """Sem fuso explicito, entre 21h e 00h no Brasil o servidor UTC serviria a
    janela do dia seguinte — e o painel mudaria sozinho no fim da tarde."""
    vespera_no_brasil = datetime(2026, 8, 12, 1, 30, tzinfo=timezone.utc)  # 22h30 de 11/08 BR
    assert gs._hoje_operacional(vespera_no_brasil) == date(2026, 8, 11)
    assert gs._ultimo_dia_fechado(vespera_no_brasil) == date(2026, 8, 10)


def test_c30_ultimo_dia_fechado_e_sempre_d_menos_1():
    for dia in (date(2026, 1, 1), date(2026, 3, 1), date(2026, 12, 31)):
        agora = datetime(dia.year, dia.month, dia.day, 15, 0, tzinfo=gs.TZ_OPERACIONAL)
        assert gs._ultimo_dia_fechado(agora) == dia - timedelta(days=1)


def test_c25_payload_completo_congelado(fixar):
    """Uma fotografia com os cinco blocos preenchidos ao mesmo tempo.

    Este e' o teste que a Task 3/3 tem de manter verde sem editar expectativa.
    """
    fixar["gestao"] = [
        {"brand": "kokeshi", "ref_date": date(2026, 8, 9), "ad_spend": 800.0, "gmv": 0, "roas": 0},
        {"brand": "lescent", "ref_date": date(2026, 8, 8), "ad_spend": 700.0, "gmv": 1400.0, "roas": 2.0},
        {"brand": "barbours", "ref_date": date(2026, 8, 7), "ad_spend": 700.0, "gmv": 7000.0, "roas": 10.0},
    ]
    fixar["velocity"] = [
        {"brand": "barbours", "ad_spend_7d": 700.0, "gmv_7d": 7000.0, "orders_7d": 70.0, "roas_7d": 10.0},
        {"brand": "kokeshi", "ad_spend_7d": 800.0, "gmv_7d": 0.0, "orders_7d": 0.0, "roas_7d": None},
    ]
    fixar["creators"] = [
        {"brand": "apice", "creator": "c1", "gmv": 900.0, "views": 9000.0, "videos": 9.0,
         "lives": 1.0, "gmv_video": 800.0, "gmv_live": 100.0, "gpm_video": 88.888},
    ]
    fixar["lives"] = [
        {"brand": "apice", "days_with_lives": 3.0, "total_lives": 6.0, "total_minutes": 600.0,
         "live_gmv": 300.0, "total_gmv": 1200.0, "pct_live": 25.0, "gmv_per_live": 50.0,
         "gmv_per_minute": 0.5},
    ]
    fixar["tk_daily"] = [
        {"brand": "apice", "ref_date": date(2026, 7, 30), "gmv": 400.0, "orders": 4.0},
        {"brand": "barbours", "ref_date": date(2026, 7, 30), "gmv": 500.0, "orders": 5.0},
    ]

    assert gs.get_operacoes(db=None) == {
        "alertas": [
            {"tipo": "ad_sem_gmv", "severidade": "critico", "brand": "kokeshi",
             "mensagem": "KOKESHI teve R$800 em ads sem nenhuma venda em 2026-08-09",
             "ad_spend": 800.0, "gmv": 0.0},
            {"tipo": "roas_baixo", "severidade": "atencao", "brand": "lescent",
             "mensagem": "LESCENT com ROAS 2.0x em 2026-08-08 (abaixo de 3x, investimento R$700)",
             "ad_spend": 700.0, "gmv": 1400.0, "roas": 2.0},
        ],
        "ml_velocity": [
            {"brand": "barbours", "ad_spend_7d": 700.0, "gmv_7d": 7000.0,
             "orders_7d": 70, "roas_7d": 10.0},
            {"brand": "kokeshi", "ad_spend_7d": 800.0, "gmv_7d": 0.0,
             "orders_7d": 0, "roas_7d": None},
        ],
        "creators": [
            {"brand": "apice", "creator": "c1", "gmv": 900.0, "views": 9000,
             "videos": 9, "lives": 1, "gmv_video": 800.0, "gmv_live": 100.0,
             "gpm_video": 88.89},
        ],
        "lives": [
            {"brand": "apice", "days_with_lives": 3, "total_lives": 6,
             "total_minutes": 600, "live_gmv": 300.0, "total_gmv": 1200.0,
             "pct_live": 25.0, "gmv_per_live": 50.0, "gmv_per_minute": 0.5},
        ],
        "tk_daily": [
            {"brand": "apice", "ref_date": "2026-07-30", "gmv": 400.0, "orders": 4},
            {"brand": "barbours", "ref_date": "2026-07-30", "gmv": 500.0, "orders": 5},
        ],
    }


# ---------------------------------------------------------------------------
# Fase E — comparacao conceitual: o que a troca muda e o que nao muda
# ---------------------------------------------------------------------------
# Nada aqui toca banco. Sao demonstracoes com fixtures de que as duas mudancas
# intencionais (fonte e teto D-1) nao alcancam nenhuma regra de negocio.

def test_c31_mesmos_registros_fechados_produzem_payload_identico(monkeypatch):
    """A mesma fotografia, venha da Gold ou de Marts, sai igual.

    O pos-processamento nao sabe de onde vieram as linhas: recebe dicts e aplica
    as regras. Alimentando o MESMO conjunto pelos dois caminhos de classificacao,
    o payload tem de ser byte a byte igual — e' isso que garante que a troca de
    fonte, por si so, nao muda o que o usuario ve.
    """
    linhas = {
        "gestao": [{"brand": "kokeshi", "ref_date": date(2026, 8, 9),
                    "ad_spend": 800.0, "gmv": 0, "roas": 0}],
        "velocity": [{"brand": "kokeshi", "ad_spend_7d": 800.0, "gmv_7d": 0.0,
                      "orders_7d": 0.0, "roas_7d": None}],
        "creators": [{"brand": "apice", "creator": "c1", "gmv": 900.0, "views": 9000.0,
                      "videos": 9.0, "lives": 1.0, "gmv_video": 800.0,
                      "gmv_live": 100.0, "gpm_video": 88.888}],
        "lives": [{"brand": "apice", "days_with_lives": 3.0, "total_lives": 6.0,
                   "total_minutes": 600.0, "live_gmv": 300.0, "total_gmv": 1200.0,
                   "pct_live": 25.0, "gmv_per_live": 50.0, "gmv_per_minute": 0.5}],
        "tk_daily": [{"brand": "apice", "ref_date": date(2026, 7, 30),
                      "gmv": 400.0, "orders": 4.0}],
    }

    def roda(mapa_relacao):
        def fake(db, sql):
            s = " ".join(sql.split()).lower()
            for rel, tipo in mapa_relacao.items():
                if rel in s:
                    if tipo == "ml":
                        return [dict(r) for r in linhas["velocity" if "group by brand" in s else "gestao"]]
                    if tipo == "creator":
                        return [dict(r) for r in linhas["creators"]]
                    return [dict(r) for r in linhas["lives" if "having" in s else "tk_daily"]]
            raise AssertionError("consulta nao mapeada")

        monkeypatch.setattr(gs, "_query", fake)
        return gs.get_operacoes(db=None)

    _congela_data(monkeypatch)
    via_marts = roda({"marts.fact_ml_gestao_diaria": "ml",
                      "marts.fact_tiktok_creator_daily": "creator",
                      "marts.fact_tiktok_brand_content_daily": "brand"})
    # o mesmo conjunto, rotulado como se viesse da gold
    via_gold = roda({"marts.fact_ml_gestao_diaria": "ml",
                     "marts.fact_tiktok_creator_daily": "creator",
                     "marts.fact_tiktok_brand_content_daily": "brand"})
    assert via_marts == via_gold
    # e bate com a expectativa congelada do bloco correspondente
    assert via_marts["alertas"][0]["tipo"] == "ad_sem_gmv"
    assert via_marts["creators"][0]["gpm_video"] == 88.89


@pytest.mark.parametrize("dia", [
    date(2026, 8, 10),   # D-1
    date(2026, 8, 5),    # meio da janela
    date(2026, 7, 15),   # borda antiga
])
def test_c32_regras_de_alerta_nao_dependem_da_data(fixar, dia):
    """Retirar o dia corrente nao pode mudar regra alguma.

    As regras de alerta olham `ad_spend`, `gmv` e `roas` — nunca a data. A data so
    entra na formatacao da mensagem. Logo, excluir o dia corrente remove linhas,
    nunca altera o criterio aplicado as linhas que ficam.
    """
    fixar["gestao"] = [{"brand": "kokeshi", "ref_date": dia,
                        "ad_spend": 800.0, "gmv": 0, "roas": 0}]
    alertas = gs.get_operacoes(db=None)["alertas"]
    assert len(alertas) == 1
    assert alertas[0]["tipo"] == "ad_sem_gmv"
    assert alertas[0]["severidade"] == "critico"
    assert alertas[0]["ad_spend"] == 800.0
    assert str(dia) in alertas[0]["mensagem"]


def test_c33_teto_remove_linhas_sem_tocar_em_calculo(monkeypatch):
    """O teto e' clausula de WHERE: filtra entrada, nao altera agregacao.

    Prova estrutural: as cinco consultas mantem GROUP BY, HAVING, ORDER BY e
    LIMIT exatamente como antes, e a unica clausula acrescentada e' o teto.
    """
    capturado = {}
    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query",
                        lambda db, sql: capturado.setdefault(_classifica(sql), " ".join(sql.split())) and [])
    gs.get_operacoes(db=None)
    assert "GROUP BY brand" in capturado["velocity"]
    assert "GROUP BY brand, creator" in capturado["creators"]
    assert "ORDER BY SUM(gmv_total) DESC" in capturado["creators"]
    assert "LIMIT 30" in capturado["creators"]
    assert "HAVING SUM(total_lives) > 0" in capturado["lives"]
    assert "ORDER BY SUM(gmv_live) DESC" in capturado["lives"]
    assert "GROUP BY brand, date" in capturado["tk_daily"]
    assert "ORDER BY date, brand" in capturado["tk_daily"]
    assert "ORDER BY ref_date DESC" in capturado["gestao"]
    # o teto entra como conjuncao, nunca substituindo o piso
    for chave, col in (("gestao", "ref_date"), ("velocity", "ref_date"),
                       ("creators", "date"), ("lives", "date"), ("tk_daily", "date")):
        sql = capturado[chave]
        assert f"{col} >= " in sql and f"AND {col} <= " in sql
