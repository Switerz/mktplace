"""Testes do bloco de descontos do pedido TikTok em /canais — UE8-I3, §28.

Sessoes SQLAlchemy falsas: nenhum banco real e' tocado. A classificacao de
periodo, o calculo de taxa e a classificacao de frescor sao funcoes PURAS e
testadas sem fake algum.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.schemas.performance import CanaisResponse, TikTokOrderDiscountsBlock
from app.services import performance_service as perf_svc
from app.services import tiktok_order_discounts_service as svc

HOJE = date(2026, 9, 8)
AGORA = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)
SO_TIKTOK = [perf_svc.TIKTOK_ID]
TODOS = [perf_svc.TIKTOK_ID, perf_svc.ML_ID, perf_svc.SHOPEE_ID]
SEM_TIKTOK = [perf_svc.ML_ID, perf_svc.SHOPEE_ID]

AGOSTO = (date(2026, 8, 1), date(2026, 8, 31))


def apenas_codigo(fonte: str) -> str:
    """Remove comentarios E docstrings, deixando so' o codigo EXECUTADO.

    Testes de AUSENCIA precisam disto. Filtrar apenas linhas iniciadas por `#`
    ainda varre a prosa das docstrings — e este modulo EXPLICA, em prosa, que
    nao aplica `abs()` e que `total_discount` nao existe. Um teste ingenuo
    falharia justamente por causa da documentacao que prova o contrario.
    """
    toks = [t for t in tokenize.generate_tokens(io.StringIO(fonte).readline)
            if t.type != tokenize.COMMENT]
    texto = tokenize.untokenize(toks)
    arv = ast.parse(texto)
    doc_ids = set()
    for no in ast.walk(arv):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)):
            corpo = getattr(no, "body", [])
            if (corpo and isinstance(corpo[0], ast.Expr)
                    and isinstance(corpo[0].value, ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                doc_ids.add(id(corpo[0]))
    linhas = set()
    for no in ast.walk(arv):
        if id(no) in doc_ids:
            linhas.update(range(no.lineno, (no.end_lineno or no.lineno) + 1))
    return "\n".join(("" if i in linhas else l)
                     for i, l in enumerate(texto.splitlines(), 1))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    """Sessao falsa de UMA consulta.

    O bloco emite exatamente um `execute` (valores + cobertura + metadados
    fundidos); `sqls` registra tudo, entao um round-trip a mais apareceria aqui
    e quebraria `test_uma_unica_consulta_por_request`.
    """

    def __init__(self, rows=None, explode: Exception | None = None):
        self.rows = rows if rows is not None else []
        self.explode = explode
        self.sqls: list[str] = []
        self.params: list = []

    def execute(self, sql, params=None):
        self.sqls.append(str(sql))
        self.params.append(params)
        if self.explode is not None:
            raise self.explode
        return FakeResult(self.rows)


SYNC_RECENTE = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _linha(brand="barbours", *, com=1000, gmv="5000.00", fpv="8000.00",
           sd="-3000.00", ps="500.00", canc=100, csd="-250.00", cps="40.00",
           chaves=31, dias_obs=31, marcas_obs=5, presentes=155,
           marcas_conhecidas=5,
           synced=SYNC_RECENTE, src_upd=datetime(2026, 9, 8, 9, 0),
           src_max=date(2026, 9, 7)) -> dict:
    """Uma linha do resultado: metadados repetidos + agregado da marca.

    `dias_obs` x `marcas_obs` e' a GRADE OBSERVADA; `presentes` sao as chaves
    que existem nela. `marcas_conhecidas` e' outra grandeza — as marcas que a
    fato tem dentro do filtro — e nao participa da cobertura.
    """
    return {
        "dias_observados": dias_obs,
        "marcas_observadas": marcas_obs,
        "chaves_presentes": presentes,
        "discounts_refreshed_at": synced,
        "source_max_updated_at": src_upd,
        "source_max_date": src_max,
        "marcas_conhecidas": marcas_conhecidas,
        "brand": brand,
        "commercial_orders": com,
        "official_gmv": None if gmv is None else Decimal(gmv),
        "full_product_value": None if fpv is None else Decimal(fpv),
        "seller_discount_signed": None if sd is None else Decimal(sd),
        "platform_subsidy_amount": None if ps is None else Decimal(ps),
        "cancelled_orders": canc,
        "cancelled_seller_discount_signed": None if csd is None else Decimal(csd),
        "cancelled_platform_subsidy_amount": None if cps is None else Decimal(cps),
        "chaves_da_marca": chaves,
    }


def _so_metadados(**kw) -> dict:
    """Linha do `LEFT JOIN ... ON TRUE` quando nenhuma marca tem dado."""
    base = _linha(**kw)
    for k in ("brand", "commercial_orders", "official_gmv", "full_product_value",
              "seller_discount_signed", "platform_subsidy_amount",
              "cancelled_orders", "cancelled_seller_discount_signed",
              "cancelled_platform_subsidy_amount", "chaves_da_marca"):
        base[k] = None
    base["dias_observados"] = 0
    base["marcas_observadas"] = 0
    base["chaves_presentes"] = 0
    base["discounts_refreshed_at"] = None
    base["source_max_updated_at"] = None
    base["source_max_date"] = None
    return base


def _bloco(sessao, mkts=SO_TIKTOK, janela=AGOSTO, marcas=None, hoje=HOJE,
           agora=AGORA) -> dict:
    return svc.build_tiktok_order_discounts_block(
        sessao, mkts, janela[0], janela[1],
        brand_keys=marcas, today=hoje, agora=agora)


# ===========================================================================
# 1-6 — contrato do schema
# ===========================================================================

def test_01_bloco_valida_contra_o_schema_pydantic():
    b = _bloco(FakeSession([_linha()]))
    modelo = TikTokOrderDiscountsBlock.model_validate(b)
    assert modelo.availability_status == "available"
    assert len(modelo.rows) == 1


def test_02_todos_os_campos_do_contrato_estao_presentes():
    b = _bloco(FakeSession([_linha()]))
    esperados = {
        "availability_status", "period_status", "coverage_status",
        "coverage_basis", "coverage_expected_keys", "coverage_present_keys",
        "coverage_missing_keys",
        "freshness_status", "rows", "date_from", "date_to", "date_count",
        "discounts_refreshed_at", "source_max_date", "source_max_updated_at",
        "seller_note", "subsidy_note", "limitation_note", "warnings",
    }
    assert set(b) == esperados


def test_03_todos_os_campos_da_linha_estao_presentes():
    b = _bloco(FakeSession([_linha()]))
    esperados = {
        "brand", "commercial_orders", "official_gmv", "full_product_value",
        "seller_discount_signed", "platform_subsidy_amount",
        "cancelled_orders", "cancelled_seller_discount_signed",
        "cancelled_platform_subsidy_amount",
        "seller_discount_rate", "platform_subsidy_rate",
    }
    assert set(b["rows"][0]) == esperados


def test_04_nenhum_campo_de_total_existe_no_contrato():
    """`total_discount` nao existe, e a ausencia e' intencional: os dois
    componentes tem financiadores diferentes."""
    b = _bloco(FakeSession([_linha()]))
    chaves = set(b) | set(b["rows"][0])
    for proibido in ("total_discount", "discount_total", "net_discount",
                     "total", "margin", "margem", "roi", "roas",
                     "receita_liquida", "net_revenue", "cash", "caixa"):
        assert not any(proibido in c for c in chaves), proibido


def test_05_bloco_e_aditivo_em_CanaisResponse():
    campos = CanaisResponse.model_fields
    assert "tiktok_order_discounts" in campos
    assert campos["tiktok_order_discounts"].default is None


def test_06_freshness_e_um_enum_proprio_de_tres_valores():
    from app.schemas.performance import DiscountFreshnessStatus
    from typing import get_args
    assert set(get_args(DiscountFreshnessStatus)) == {
        "recent_load", "stale_load", "unknown"}
    # `current_snapshot` foi DESCARTADO antes de virar contrato publico:
    # sugeria propriedade do dado, quando a medicao e' da carga.
    assert "current_snapshot" not in get_args(DiscountFreshnessStatus)


def test_06b_coverage_basis_declara_a_grade():
    from app.schemas.performance import CoverageBasis
    from typing import get_args
    assert get_args(CoverageBasis) == ("observed_grid",)
    assert svc.COVERAGE_BASIS == "observed_grid"


# ===========================================================================
# 7-10 — uma consulta por request, e nunca a raw
# ===========================================================================

def test_07_uma_unica_consulta_por_request():
    s = FakeSession([_linha()])
    _bloco(s)
    assert len(s.sqls) == 1


def test_08_a_consulta_le_somente_a_fato():
    s = FakeSession([_linha()])
    _bloco(s)
    sql = s.sqls[0]
    assert svc.FACT_TABLE in sql
    for proibida in ("raw.", "gold.", "silver.", "tiktok_shop_orders"):
        assert proibida not in sql, proibida


def test_09_sem_select_estrela_e_sem_coalesce():
    s = FakeSession([_linha()])
    _bloco(s)
    sql = s.sqls[0].upper()
    assert "SELECT *" not in sql
    # COALESCE transformaria ausencia de origem em zero — exatamente o que
    # este bloco nao pode fazer.
    assert "COALESCE" not in sql


def test_10_sem_tiktok_nao_ha_consulta_nenhuma():
    s = FakeSession([_linha()])
    b = _bloco(s, mkts=SEM_TIKTOK)
    assert s.sqls == []
    assert b["availability_status"] == "unavailable_no_source"
    assert b["rows"] == []


# ===========================================================================
# 11-16 — filtros de canal, marca e periodo
# ===========================================================================

def test_11_filtro_de_marca_vai_para_o_sql_e_para_os_params():
    s = FakeSession([_linha()])
    _bloco(s, marcas=["barbours", "apice"])
    assert "brands" in s.params[0]
    assert s.params[0]["brands"] == ["barbours", "apice"]
    assert "ANY(:brands)" in s.sqls[0]


def test_12_sem_filtro_de_marca_o_sql_nao_tem_predicado_de_marca():
    s = FakeSession([_linha()])
    _bloco(s, marcas=None)
    assert "brands" not in s.params[0]
    assert "ANY(:brands)" not in s.sqls[0]


def test_13_marca_vazia_e_no_eligible_brand_sem_consulta():
    """`None` = todas as marcas; `[]` = nenhuma elegivel."""
    s = FakeSession([_linha()])
    b = _bloco(s, marcas=[])
    assert s.sqls == []
    assert b["availability_status"] == "no_eligible_brand"
    assert b["rows"] == []


def test_14_a_janela_vai_para_os_params():
    s = FakeSession([_linha()])
    _bloco(s, janela=(date(2026, 7, 1), date(2026, 7, 31)))
    assert s.params[0]["start"] == date(2026, 7, 1)
    assert s.params[0]["end"] == date(2026, 7, 31)


def test_15_marca_conhecida_zero_vira_no_eligible_brand():
    """O filtro nao intersecta NENHUMA marca da fato."""
    s = FakeSession([_so_metadados(marcas_conhecidas=0)])
    b = _bloco(s, marcas=["inexistente"])
    assert b["availability_status"] == "no_eligible_brand"
    assert b["rows"] == []


def test_15b_marca_conhecida_sem_dado_na_janela_e_available_nao_no_brand():
    """Distincao que a grade observada exige: a marca EXISTE na fonte, so' nao
    vendeu nesta janela. Chamar isso de `no_eligible_brand` esconderia que a
    janela e' que esta vazia."""
    s = FakeSession([_so_metadados(marcas_conhecidas=1)])
    b = _bloco(s, marcas=["barbours"])
    assert b["availability_status"] == "available"
    assert b["rows"] == []
    assert b["coverage_status"] == "unknown"


def test_16_resultado_totalmente_vazio_nao_derruba_o_bloco():
    b = _bloco(FakeSession([]))
    assert b["availability_status"] == "available"
    assert b["rows"] == []
    assert svc.NO_DATA_WARNING in b["warnings"]


# ===========================================================================
# 17-24 — teto D-1 e exclusao de D0
# ===========================================================================

def test_17_teto_nunca_passa_de_d_menos_1():
    s = FakeSession([_linha()])
    _bloco(s, janela=(date(2026, 9, 1), date(2026, 9, 30)))
    assert s.params[0]["end"] == date(2026, 9, 7)


def test_18_d0_excluido_produz_o_warning_literal():
    b = _bloco(FakeSession([_linha()]),
               janela=(date(2026, 9, 1), date(2026, 9, 30)))
    assert svc.D0_WARNING in b["warnings"]
    assert b["date_to"] == date(2026, 9, 7)


def test_19_o_warning_de_d0_e_exatamente_o_texto_do_contrato():
    assert svc.D0_WARNING == (
        "O dia de hoje foi excluído: os pedidos ainda estão entrando e o "
        "número mudaria sozinho."
    )


def test_20_janela_que_termina_em_d_menos_1_nao_dispara_o_warning():
    b = _bloco(FakeSession([_linha()]),
               janela=(date(2026, 9, 1), date(2026, 9, 7)))
    assert svc.D0_WARNING not in b["warnings"]


def test_21_janela_so_em_d0_devolve_vazio_sem_consultar():
    s = FakeSession([_linha()])
    b = _bloco(s, janela=(date(2026, 9, 8), date(2026, 9, 8)))
    assert s.sqls == []
    assert b["rows"] == [] and b["date_count"] == 0
    assert b["date_from"] is None and b["date_to"] is None
    assert svc.D0_WARNING in b["warnings"]
    assert svc.EMPTY_WINDOW_WARNING in b["warnings"]


def test_22_janela_so_no_futuro_tambem_devolve_vazio():
    s = FakeSession([_linha()])
    b = _bloco(s, janela=(date(2026, 9, 20), date(2026, 9, 30)))
    assert s.sqls == [] and b["rows"] == []


def test_23_nenhum_dado_antigo_sobrevive_a_janela_vazia():
    """Nada do recorte anterior pode vazar para a resposta vazia."""
    b = _bloco(FakeSession([_linha()]),
               janela=(date(2026, 9, 8), date(2026, 9, 30)))
    assert b["discounts_refreshed_at"] is None
    assert b["source_max_date"] is None
    assert b["source_max_updated_at"] is None
    assert b["date_count"] == 0


def test_24_janela_vazia_continua_available_e_nao_error():
    """HTTP 200 e um estado honesto — nunca 4xx, nunca `error`."""
    b = _bloco(FakeSession([]), janela=(date(2026, 9, 9), date(2026, 9, 30)))
    assert b["availability_status"] == "available"


# ===========================================================================
# 25-31 — os quatro PeriodStatus
# ===========================================================================

@pytest.mark.parametrize("inicio,fim,esperado", [
    (date(2026, 8, 1), date(2026, 8, 31), "complete_month"),
    (date(2026, 7, 1), date(2026, 8, 31), "complete_months"),
    (date(2026, 8, 5), date(2026, 8, 20), "partial_month"),
    (date(2026, 7, 15), date(2026, 8, 20), "not_month_aligned"),
])
def test_25_os_quatro_period_status(inicio, fim, esperado):
    b = _bloco(FakeSession([_linha()]), janela=(inicio, fim))
    assert b["period_status"] == esperado


def test_26_corte_de_d0_torna_o_periodo_parcial():
    """Um mes truncado hoje nao e' mes fechado, qualquer que fosse o pedido."""
    b = _bloco(FakeSession([_linha()]),
               janela=(date(2026, 9, 1), date(2026, 9, 30)))
    assert b["period_status"] == "partial_month"


def test_27_mes_corrente_completo_no_calendario_nao_e_complete_month():
    janela = svc.classify_discount_period(
        date(2026, 9, 1), date(2026, 9, 30), HOJE)
    assert janela.period_status == "partial_month"
    assert janela.end == date(2026, 9, 7)


def test_28_period_status_nao_bloqueia_a_consulta():
    """Diferente do bloco MENSAL de afiliados: aqui o grao e' diario, e um
    recorte parcial e' soma honesta de dias fechados."""
    for inicio, fim in [(date(2026, 8, 5), date(2026, 8, 20)),
                        (date(2026, 7, 15), date(2026, 8, 20))]:
        s = FakeSession([_linha()])
        b = _bloco(s, janela=(inicio, fim))
        assert len(s.sqls) == 1
        assert b["rows"], (inicio, fim)


def test_29_classify_e_puro_e_nao_toca_banco():
    j = svc.classify_discount_period(date(2026, 8, 1), date(2026, 8, 31), HOJE)
    assert (j.start, j.end, j.period_status, j.d0_excluded) == (
        date(2026, 8, 1), date(2026, 8, 31), "complete_month", False)


def test_30_day_count_e_de_calendario_nao_de_dado():
    j = svc.classify_discount_period(date(2026, 8, 1), date(2026, 8, 31), HOJE)
    assert j.day_count == 31
    vazia = svc.classify_discount_period(
        date(2026, 9, 8), date(2026, 9, 30), HOJE)
    assert vazia.day_count == 0


def test_31_dezembro_fecha_o_ano_corretamente():
    j = svc.classify_discount_period(
        date(2025, 12, 1), date(2025, 12, 31), date(2026, 1, 15))
    assert j.period_status == "complete_month"


# ===========================================================================
# 32-36 — os quatro AvailabilityStatus
# ===========================================================================

def test_32_available():
    assert _bloco(FakeSession([_linha()]))["availability_status"] == "available"


def test_33_unavailable_no_source():
    b = _bloco(FakeSession([_linha()]), mkts=SEM_TIKTOK)
    assert b["availability_status"] == "unavailable_no_source"


def test_34_no_eligible_brand():
    b = _bloco(FakeSession([_linha()]), marcas=[])
    assert b["availability_status"] == "no_eligible_brand"


def test_35_error_isolado_por_falha_sql():
    s = FakeSession(explode=OperationalError("SELECT 1", {}, Exception("x")))
    b = svc.safe_tiktok_order_discounts_block(
        s, SO_TIKTOK, *AGOSTO, today=HOJE)
    assert b["availability_status"] == "error"
    assert b["rows"] == []


def test_36_tiktok_junto_com_outros_canais_continua_available():
    b = _bloco(FakeSession([_linha()]), mkts=TODOS)
    assert b["availability_status"] == "available"


# ===========================================================================
# 37-41 — os tres DiscountFreshnessStatus
# ===========================================================================

def test_37_recent_load_quando_a_carga_e_recente():
    b = _bloco(FakeSession([_linha(synced=AGORA - timedelta(hours=3))]))
    assert b["freshness_status"] == "recent_load"


def test_38_stale_load_quando_acima_do_limiar():
    velho = AGORA - timedelta(hours=svc.DISCOUNT_MAX_SNAPSHOT_AGE_HOURS + 1)
    b = _bloco(FakeSession([_linha(synced=velho)]))
    assert b["freshness_status"] == "stale_load"
    assert any("mais de" in w for w in b["warnings"])


def test_39_unknown_sem_carimbo():
    b = _bloco(FakeSession([_linha(synced=None)]))
    assert b["freshness_status"] == "unknown"
    assert svc.UNKNOWN_FRESHNESS_WARNING in b["warnings"]


def test_40_o_limiar_e_exatamente_30_horas():
    assert svc.DISCOUNT_MAX_SNAPSHOT_AGE_HOURS == 30
    na_borda = AGORA - timedelta(hours=30)
    assert svc.classify_discount_freshness(na_borda, AGORA) == "recent_load"
    passou = AGORA - timedelta(hours=30, seconds=1)
    assert svc.classify_discount_freshness(passou, AGORA) == "stale_load"


def test_41_timestamp_naive_nao_e_classificado():
    """Classificar um naive inventaria um fuso."""
    assert svc.classify_discount_freshness(datetime(2026, 9, 8, 12, 0),
                                           AGORA) == "unknown"


def test_41b_synced_at_no_FUTURO_vira_unknown_nunca_recent_load():
    """Idade negativa passaria trivialmente por "recente" e esconderia
    justamente o defeito: relogio inconsistente entre banco e aplicacao."""
    futuro = AGORA + timedelta(hours=2)
    assert svc.classify_discount_freshness(futuro, AGORA) == "unknown"

    b = _bloco(FakeSession([_linha(synced=futuro)]))
    assert b["freshness_status"] == "unknown"
    assert b["freshness_status"] != "recent_load"
    assert svc.UNKNOWN_FRESHNESS_WARNING in b["warnings"]


def test_42_o_enum_fala_de_CARGA_nao_de_dado():
    """`recent_load`/`stale_load` medem a carga. Nenhum valor pode sugerir
    propriedade do DADO — `current_snapshot` foi descartado por isso."""
    from app.schemas.performance import DiscountFreshnessStatus
    from typing import get_args
    valores = get_args(DiscountFreshnessStatus)
    for valor in valores:
        for proibida in ("stable", "mature", "closed", "final", "estavel",
                         "current", "snapshot", "atual"):
            assert proibida not in valor, valor
    assert {"recent_load", "stale_load"} <= set(valores)


# ===========================================================================
# 43-48 — cobertura
# ===========================================================================

def test_43_cobertura_completa_quando_a_grade_observada_fecha():
    # Grade OBSERVADA: 31 dias com atividade x 5 marcas com atividade = 155.
    b = _bloco(FakeSession([_linha(dias_obs=31, marcas_obs=5, presentes=155)]))
    assert b["coverage_status"] == "complete"
    assert b["coverage_expected_keys"] == 155
    assert b["coverage_present_keys"] == 155
    assert b["coverage_missing_keys"] == 0
    assert not any("Cobertura incompleta" in w for w in b["warnings"])


def test_44_cobertura_incompleta_quando_falta_chave():
    b = _bloco(FakeSession([_linha(dias_obs=31, marcas_obs=5, presentes=150)]))
    assert b["coverage_status"] == "incomplete_brand_coverage"
    assert (b["coverage_expected_keys"], b["coverage_present_keys"],
            b["coverage_missing_keys"]) == (155, 150, 5)
    assert "5 de 155" in next(
        w for w in b["warnings"] if "Cobertura incompleta" in w)


def test_44b_a_grade_e_OBSERVADA_nunca_dias_de_calendario():
    """O invariante central deste finding.

    Janela de 31 dias de calendario, mas so' 20 dias tiveram atividade e so'
    3 marcas venderam: a grade e' 20 x 3 = 60, NAO 31 x 5 = 155. Contar dias
    de calendario faria o mesmo `CoverageStatus` significar universos
    diferentes na carga (sync) e na exposicao (serving).
    """
    b = _bloco(FakeSession([_linha(dias_obs=20, marcas_obs=3, presentes=60)]),
               janela=(date(2026, 8, 1), date(2026, 8, 31)))
    assert b["coverage_expected_keys"] == 60
    assert b["coverage_status"] == "complete"


def test_44c_a_definicao_bate_com_a_do_sync_versionado():
    """Compara com `_coverage` do sync sobre a MESMA entrada sintetica."""
    # Raiz do repo derivada DESTE arquivo: `<raiz>/apps/api/tests/<este>.py`.
    # Um caminho absoluto embutido quebraria em qualquer outra maquina e
    # vazaria o diretorio pessoal para o repositorio.
    import pathlib
    import sys
    raiz = pathlib.Path(__file__).resolve().parents[3]
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    from pipelines.sync_tiktok_order_discounts_daily import (
        SourceRow, Window, _coverage,
    )

    def _sr(d, m):
        return SourceRow(
            ref_date=d, brand=m, commercial_orders=1, official_gmv=1,
            full_product_value=1, seller_discount_signed=0,
            platform_subsidy_amount=0, cancelled_orders=0,
            cancelled_seller_discount_signed=0,
            cancelled_platform_subsidy_amount=0, source_max_updated_at=None,
            raw_max_updated_at=None, total_dedup=1, unpaid_onhold_orders=0,
            unknown_orders=0, commercial_null_money=0, cancelled_null_money=0)

    # 3 dias observados x 2 marcas observadas = 6; 5 presentes -> 1 ausente.
    linhas = [_sr(date(2026, 8, d), m)
              for d, m in [(1, "a"), (1, "b"), (2, "a"), (2, "b"), (3, "a")]]
    ausentes_sync, _ = _coverage(
        linhas, Window(date(2026, 8, 1), date(2026, 8, 31)))

    b = _bloco(FakeSession([_linha(dias_obs=3, marcas_obs=2, presentes=5)]))
    assert b["coverage_expected_keys"] == 3 * 2
    assert b["coverage_missing_keys"] == len(ausentes_sync) == 1


def test_45_o_aviso_de_cobertura_declara_as_duas_causas_possiveis():
    b = _bloco(FakeSession([_linha(presentes=150)]))
    aviso = next(w for w in b["warnings"] if "Cobertura incompleta" in w)
    assert "não houve" in aviso and "ingestão" in aviso
    assert "NÃO foram preenchidas com zero" in aviso


def test_45b_complete_nunca_afirma_ingestao_comprovada():
    b = _bloco(FakeSession([_linha(presentes=155)]))
    assert b["coverage_status"] == "complete"
    assert b["coverage_basis"] == "observed_grid"
    texto = " ".join([b["limitation_note"], *b["warnings"]])
    for proibida in ("ingestão completa", "ingestao completa",
                     "ingestão comprovada"):
        assert proibida not in texto


def test_46_cobertura_unknown_quando_nao_ha_linha():
    b = _bloco(FakeSession([_so_metadados()]))
    assert b["coverage_status"] == "unknown"
    assert b["coverage_expected_keys"] == 0


def test_47_cobertura_unknown_quando_o_bloco_nao_consulta():
    for kw in ({"mkts": SEM_TIKTOK}, {"marcas": []}):
        b = _bloco(FakeSession([_linha()]), **kw)
        assert b["coverage_status"] == "unknown"
        assert b["coverage_basis"] == "observed_grid"
        assert (b["coverage_expected_keys"], b["coverage_present_keys"],
                b["coverage_missing_keys"]) == (0, 0, 0)


def test_48_chave_ausente_nunca_vira_linha_com_zero():
    """A linha de metadados do LEFT JOIN nao pode virar R$ 0,00."""
    b = _bloco(FakeSession([_so_metadados()]))
    assert b["rows"] == []


# ===========================================================================
# 49-54 — null versus zero
# ===========================================================================

def test_49_null_de_origem_permanece_null():
    b = _bloco(FakeSession([_linha(gmv=None, fpv=None, sd=None, ps=None)]))
    r = b["rows"][0]
    assert r["official_gmv"] is None
    assert r["full_product_value"] is None
    assert r["seller_discount_signed"] is None
    assert r["platform_subsidy_amount"] is None


def test_50_zero_medido_permanece_zero():
    b = _bloco(FakeSession([_linha(gmv="0.00", sd="0.00", ps="0.00")]))
    r = b["rows"][0]
    assert r["official_gmv"] == 0.0
    assert r["seller_discount_signed"] == 0.0
    assert r["platform_subsidy_amount"] == 0.0


def test_51_zero_e_null_nao_se_confundem():
    zero = _bloco(FakeSession([_linha(sd="0.00")]))["rows"][0]
    nulo = _bloco(FakeSession([_linha(sd=None)]))["rows"][0]
    assert zero["seller_discount_signed"] == 0.0
    assert nulo["seller_discount_signed"] is None
    assert zero["seller_discount_signed"] != nulo["seller_discount_signed"]


def test_52_cancelados_nulos_permanecem_nulos():
    b = _bloco(FakeSession([_linha(csd=None, cps=None)]))
    r = b["rows"][0]
    assert r["cancelled_seller_discount_signed"] is None
    assert r["cancelled_platform_subsidy_amount"] is None


def test_53_contagens_nulas_viram_zero_inteiro_deliberadamente():
    """`commercial_orders` e `cancelled_orders` sao `int` no contrato: o SQL
    conta linhas e nao pode devolver `NULL` para uma marca que existe."""
    b = _bloco(FakeSession([_linha(com=0, canc=0)]))
    assert b["rows"][0]["commercial_orders"] == 0
    assert b["rows"][0]["cancelled_orders"] == 0


def test_54_date_count_e_dias_com_dado_nao_tamanho_da_janela():
    """Janela de 31 dias de calendario, 28 dias observados."""
    b = _bloco(FakeSession([_linha(dias_obs=28, marcas_obs=5, presentes=140)]),
               janela=(date(2026, 8, 1), date(2026, 8, 31)))
    assert b["date_count"] == 28
    assert b["date_count"] != 31


# ===========================================================================
# 55-63 — taxas, sinais e denominador
# ===========================================================================

def test_55_taxa_da_marca_e_negativa():
    b = _bloco(FakeSession([_linha(sd="-3000.00", fpv="8000.00")]))
    assert b["rows"][0]["seller_discount_rate"] == pytest.approx(-37.5)


def test_56_taxa_da_plataforma_e_positiva():
    b = _bloco(FakeSession([_linha(ps="500.00", fpv="8000.00")]))
    assert b["rows"][0]["platform_subsidy_rate"] == pytest.approx(6.25)


def test_57_denominador_zero_devolve_null_nao_zero_nem_infinito():
    b = _bloco(FakeSession([_linha(fpv="0.00")]))
    assert b["rows"][0]["seller_discount_rate"] is None
    assert b["rows"][0]["platform_subsidy_rate"] is None


def test_58_denominador_null_devolve_null():
    b = _bloco(FakeSession([_linha(fpv=None)]))
    assert b["rows"][0]["seller_discount_rate"] is None


def test_59_numerador_null_devolve_null():
    b = _bloco(FakeSession([_linha(sd=None, fpv="8000.00")]))
    assert b["rows"][0]["seller_discount_rate"] is None


def test_60_numerador_zero_com_base_valida_devolve_zero_medido():
    b = _bloco(FakeSession([_linha(sd="0.00", fpv="8000.00")]))
    assert b["rows"][0]["seller_discount_rate"] == 0.0


def test_61_o_denominador_e_full_product_value_nunca_o_gmv():
    """Usar `official_gmv` — ja liquido dos descontos — produziria uma taxa
    sem significado."""
    b = _bloco(FakeSession([_linha(sd="-3000.00", fpv="8000.00", gmv="5000.00")]))
    r = b["rows"][0]
    assert r["seller_discount_rate"] == pytest.approx(-37.5)   # 3000/8000
    assert r["seller_discount_rate"] != pytest.approx(-60.0)   # 3000/5000


def test_62_sinal_nunca_e_invertido_nem_absoluto():
    b = _bloco(FakeSession([_linha(sd="-3000.00", csd="-250.00",
                                   ps="500.00", cps="40.00")]))
    r = b["rows"][0]
    assert r["seller_discount_signed"] < 0
    assert r["cancelled_seller_discount_signed"] < 0
    assert r["platform_subsidy_amount"] > 0
    assert r["cancelled_platform_subsidy_amount"] > 0


def test_63_o_codigo_do_servico_nao_usa_abs():
    import inspect
    codigo = apenas_codigo(inspect.getsource(svc))
    assert "abs(" not in codigo


# ===========================================================================
# 64-68 — cancelados separados, e nenhuma soma
# ===========================================================================

def test_64_cancelados_ficam_em_campos_proprios():
    b = _bloco(FakeSession([_linha(com=1000, canc=100)]))
    r = b["rows"][0]
    assert r["commercial_orders"] == 1000
    assert r["cancelled_orders"] == 100


def test_65_cancelado_nao_entra_no_comercial():
    b = _bloco(FakeSession([_linha(sd="-3000.00", csd="-250.00")]))
    r = b["rows"][0]
    assert r["seller_discount_signed"] == -3000.0     # nao -3250
    assert r["cancelled_seller_discount_signed"] == -250.0


def test_66_a_taxa_ignora_os_cancelados():
    b = _bloco(FakeSession([_linha(sd="-3000.00", csd="-5000.00",
                                   fpv="8000.00")]))
    assert b["rows"][0]["seller_discount_rate"] == pytest.approx(-37.5)


def test_67_nenhuma_soma_de_seller_com_platform():
    """A soma dos dois nao aparece em nenhum campo da resposta."""
    b = _bloco(FakeSession([_linha(sd="-3000.00", ps="500.00")]))
    r = b["rows"][0]
    proibidos = {-2500.0, 2500.0, -3500.0, 3500.0}
    numericos = {v for v in r.values() if isinstance(v, float)}
    assert not (numericos & proibidos)


def test_68_a_soma_das_taxas_tambem_nao_aparece():
    b = _bloco(FakeSession([_linha(sd="-3000.00", ps="500.00", fpv="8000.00")]))
    r = b["rows"][0]
    soma = r["seller_discount_rate"] + r["platform_subsidy_rate"]
    assert soma not in {v for v in r.values() if isinstance(v, float)}


# ===========================================================================
# 69-75 — isolamento de falha
# ===========================================================================

def test_69_falha_sql_devolve_bloco_error_sem_levantar():
    s = FakeSession(explode=OperationalError("SELECT 1", {}, Exception("x")))
    b = svc.safe_tiktok_order_discounts_block(s, SO_TIKTOK, *AGOSTO, today=HOJE)
    assert b["availability_status"] == "error"


def test_70_erro_nao_sql_propaga():
    """Um `except Exception` amplo esconderia bug de programacao sob
    "erro de fonte"."""
    s = FakeSession(explode=AttributeError("bug de programacao"))
    with pytest.raises(AttributeError):
        svc.safe_tiktok_order_discounts_block(s, SO_TIKTOK, *AGOSTO, today=HOJE)


def test_71_a_nota_de_erro_e_fixa_e_sanitizada():
    s = FakeSession(explode=OperationalError(
        "SELECT * FROM marts.x WHERE host='prod-db.internal'", {},
        Exception("password=hunter2 at 10.0.0.9")))
    b = svc.safe_tiktok_order_discounts_block(s, SO_TIKTOK, *AGOSTO, today=HOJE)
    texto = " ".join([b["limitation_note"], *b["warnings"]])
    for vazamento in ("SELECT", "prod-db", "hunter2", "10.0.0.9", "marts.x"):
        assert vazamento not in texto, vazamento


def test_72_o_log_de_falha_nao_interpola_nada(caplog):
    s = FakeSession(explode=OperationalError("SELECT segredo", {},
                                             Exception("host=prod-db")))
    with caplog.at_level("WARNING"):
        svc.safe_tiktok_order_discounts_block(s, SO_TIKTOK, *AGOSTO, today=HOJE)
    mensagens = [r.getMessage() for r in caplog.records]
    assert svc.LOG_QUERY_FAILURE in mensagens
    assert not any("segredo" in m or "prod-db" in m for m in mensagens)


def test_73_erro_preserva_a_classificacao_de_periodo():
    s = FakeSession(explode=OperationalError("x", {}, Exception("y")))
    b = svc.safe_tiktok_order_discounts_block(
        s, SO_TIKTOK, date(2026, 9, 1), date(2026, 9, 30), today=HOJE)
    assert b["period_status"] == "partial_month"
    assert svc.D0_WARNING in b["warnings"]


def test_74_erro_valida_contra_o_schema():
    s = FakeSession(explode=OperationalError("x", {}, Exception("y")))
    b = svc.safe_tiktok_order_discounts_block(s, SO_TIKTOK, *AGOSTO, today=HOJE)
    TikTokOrderDiscountsBlock.model_validate(b)


def test_75_erro_nao_afirma_frescor():
    s = FakeSession(explode=OperationalError("x", {}, Exception("y")))
    b = svc.safe_tiktok_order_discounts_block(s, SO_TIKTOK, *AGOSTO, today=HOJE)
    assert b["freshness_status"] == "unknown"
    assert b["discounts_refreshed_at"] is None


# ===========================================================================
# 76-80 — metadados e notas
# ===========================================================================

def test_75b_source_max_date_vem_do_ESCOPO_nao_da_fato_global():
    """Contraprova do finding: a fato global vai ate 07/09, mas Barbours so'
    ate 06/09. O filtro Barbours tem de reportar 06/09.

    Um maximo global afirmaria que a selecao esta atualizada ate uma data que
    a marca filtrada nao alcancou — exatamente a leitura errada.
    """
    s = FakeSession([_linha(brand="barbours", src_max=date(2026, 9, 6))])
    b = _bloco(s, marcas=["barbours"])
    assert b["source_max_date"] == "2026-09-06"
    assert b["source_max_date"] != "2026-09-07"

    # E o SQL calcula esse maximo sobre `escopo`, nao sobre a fato inteira.
    sql = " ".join(s.sqls[0].split())
    assert "SELECT MAX(e.ref_date) FROM escopo e) AS source_max_date" in sql
    assert "MAX(h.ref_date)" not in sql


def test_75c_sem_filtro_o_maximo_do_escopo_alcanca_a_fato():
    s = FakeSession([_linha(src_max=date(2026, 9, 7))])
    b = _bloco(s, janela=(date(2026, 9, 1), date(2026, 9, 30)), marcas=None)
    assert b["source_max_date"] == "2026-09-07"


def test_76_os_tres_metadados_temporais_sao_grandezas_distintas():
    b = _bloco(FakeSession([_linha(
        synced=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        src_upd=datetime(2026, 9, 8, 9, 0),
        src_max=date(2026, 9, 7))]))
    assert b["discounts_refreshed_at"] == "2026-09-08T12:00:00+00:00"
    assert b["source_max_updated_at"] == "2026-09-08T09:00:00"
    assert b["source_max_date"] == "2026-09-07"


def test_77_source_max_updated_at_sai_sem_rotulo_de_fuso():
    """Timestamp NAIVE da fonte: carimbar BRT afirmaria um fuso nao declarado."""
    b = _bloco(FakeSession([_linha(src_upd=datetime(2026, 9, 8, 9, 0))]))
    assert "+" not in b["source_max_updated_at"]
    assert not b["source_max_updated_at"].endswith("Z")


def test_78_as_notas_nomeiam_o_financiador_de_cada_componente():
    b = _bloco(FakeSession([_linha()]))
    assert "marca" in b["seller_note"] and "reduz a receita" in b["seller_note"]
    assert "TikTok" in b["subsidy_note"]
    assert "não deve ser somado" in b["subsidy_note"]


def test_79_a_nota_de_limitacao_nega_margem_caixa_e_receita():
    b = _bloco(FakeSession([_linha()]))
    n = b["limitation_note"]
    assert "nunca somados" in n
    assert "revisada retroativamente" in n
    assert "Não são receita líquida, caixa nem margem" in n
    assert "cancelados são contexto separado" in n


def test_80_o_servico_nao_calcula_total_margem_nem_retorno():
    """Procura CALCULO, nao a palavra.

    `margem` e `receita liquida` aparecem em `LIMITATION_NOTE` — copy que
    NEGA as duas. Proibir a palavra reprovaria justamente o texto que protege
    o leitor. O que nao pode existir e' identificador: campo, funcao ou
    variavel que produza total, margem ou retorno.
    """
    import inspect
    codigo = apenas_codigo(inspect.getsource(svc))
    for proibido in ("total_discount", "discount_total", "net_discount",
                     "def margin", "def margem", "def roi", "def roas",
                     "net_revenue", "_margin", "_roas", "_roi"):
        assert proibido not in codigo, proibido


# ===========================================================================
# 81-84 — invariancia do corpo historico de /canais
# ===========================================================================

def test_81_o_bloco_nao_altera_nenhum_campo_existente_de_CanaisResponse():
    campos = set(CanaisResponse.model_fields)
    historicos = {
        "ref_month", "marketplace", "kpis", "brands", "channel_rows",
        "channel_medians", "date_from", "date_to", "compare_date_from",
        "compare_date_to", "filters", "refreshed_at", "affiliate_costs",
    }
    assert historicos <= campos
    assert campos - historicos == {"tiktok_order_discounts"}


def test_82_affiliate_costs_continua_opcional_e_independente():
    campos = CanaisResponse.model_fields
    assert campos["affiliate_costs"].default is None


def test_83_o_bloco_ausente_valida_como_none():
    r = CanaisResponse.model_validate({
        "marketplace": "tiktok",
        "kpis": {}, "brands": [],
    })
    assert r.tiktok_order_discounts is None


def test_84_regex_do_sql_nao_tem_interpolacao_de_valor():
    """Os valores entram por bind param, nunca concatenados no SQL."""
    s = FakeSession([_linha()])
    _bloco(s, marcas=["barbours"])
    sql = s.sqls[0]
    assert ":start" in sql and ":end" in sql and ":brands" in sql
    assert not re.search(r"'\d{4}-\d{2}-\d{2}'", sql)
    assert "barbours" not in sql
