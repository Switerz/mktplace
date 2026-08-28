"""Testes do bloco de afiliados em /canais — UE3 Task 2/3, contrato §23.

Sessoes SQLAlchemy falsas — nenhum banco real e' tocado. O modulo de
classificacao de periodo e' puro e testado sem fake algum.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.schemas.performance import AffiliateCostsBlock, CanaisResponse
from app.services import affiliate_costs_service as acs
from app.services import performance_service as perf_svc

HOJE = date(2026, 8, 27)
TODOS = [perf_svc.TIKTOK_ID, perf_svc.ML_ID, perf_svc.SHOPEE_ID]


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

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Sessao falsa de UMA consulta.

    O bloco emite exatamente um `execute` (valores + cobertura + watermark
    fundidos); `sqls` registra tudo, entao um round-trip a mais apareceria
    aqui e quebraria os testes que contam consultas.
    """

    def __init__(self, scope_rows=None, explode=False):
        self.scope_rows = scope_rows if scope_rows is not None else []
        self.explode = explode
        self.sqls: list[str] = []
        self.params: list = []

    def execute(self, sql, params=None):
        self.sqls.append(str(sql))
        self.params.append(params)
        if self.explode:
            raise OperationalError("SELECT ... FROM marts.fact_...",
                                   {}, Exception("server closed the connection"))
        return FakeResult(self.scope_rows)


def _fact_row(ref_month: date, brand: str, creator=Decimal("-100.50"),
              partner=Decimal("-50.25"), ads=Decimal("-10.00"),
              synced=None, presentes=5, watermark=None) -> dict:
    """Linha COM marca — vira linha monetaria."""
    return {
        "ref_month": ref_month,
        "brand": brand,
        "affiliate_creator_commission": creator,
        "affiliate_partner_commission": partner,
        "affiliate_ads_commission": ads,
        "synced_at": synced or date(2026, 8, 25),
        "brands_present_in_month": presentes,
        "source_watermark": watermark,
    }


def _mes_ausente(ref_month: date, presentes=0, watermark=None) -> dict:
    """Linha SEM marca — o que o LEFT JOIN produz para competencia pedida que
    nao casou registro. E' metainformacao de cobertura, nunca valor."""
    return {
        "ref_month": ref_month,
        "brand": None,
        "affiliate_creator_commission": None,
        "affiliate_partner_commission": None,
        "affiliate_ads_commission": None,
        "synced_at": None,
        "brands_present_in_month": presentes,
        "source_watermark": watermark,
    }


# ===========================================================================
# Contrato de períodos — função PURA, sem banco
# ===========================================================================

def test_mes_completo_passado():
    st, meses = acs.classify_period(date(2026, 3, 1), date(2026, 3, 31), HOJE)
    assert st == "complete_month"
    assert meses == ["2026-03"]


def test_varios_meses_completos_passados():
    st, meses = acs.classify_period(date(2026, 1, 1), date(2026, 3, 31), HOJE)
    assert st == "complete_months"
    assert meses == ["2026-01", "2026-02", "2026-03"]


def test_mes_corrente_e_parcial_sem_competencia():
    st, meses = acs.classify_period(date(2026, 8, 1), date(2026, 8, 31), HOJE)
    assert st == "partial_month"
    assert meses == []


def test_intervalo_parcial_dentro_do_mes():
    st, meses = acs.classify_period(date(2026, 3, 5), date(2026, 3, 20), HOJE)
    assert st == "partial_month"
    assert meses == []


def test_intervalo_desalinhado_entre_meses():
    st, meses = acs.classify_period(date(2026, 3, 5), date(2026, 4, 20), HOJE)
    assert st == "not_month_aligned"
    assert meses == []


def test_intervalo_invertido():
    assert acs.classify_period(date(2026, 4, 1), date(2026, 3, 1), HOJE) == (
        "not_month_aligned", [])


@pytest.mark.parametrize("ano,mes,ultimo", [
    (2026, 2, 28), (2024, 2, 29), (2026, 4, 30), (2026, 12, 31)])
def test_fim_de_mes_reconhece_calendario(ano, mes, ultimo):
    """Fevereiro, ano bissexto, mês de 30 e dezembro."""
    st, meses = acs.classify_period(date(ano, mes, 1), date(ano, mes, ultimo),
                                    date(2026 if ano <= 2026 else ano, 8, 27)
                                    if ano != 2026 or mes != 12 else date(2027, 1, 5))
    assert st == "complete_month", (ano, mes)
    assert meses == [f"{ano:04d}-{mes:02d}"]


def test_ultimo_dia_errado_nao_e_completo():
    st, _ = acs.classify_period(date(2026, 3, 1), date(2026, 3, 30), HOJE)
    assert st == "partial_month"


# ===========================================================================
# Montagem do bloco
# ===========================================================================

def test_mes_completo_devolve_linhas_com_sinal_preservado():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice",
                                watermark=date(2026, 8, 25))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["availability_status"] == "available"
    assert b["period_status"] == "complete_month"
    assert len(b["rows"]) == 1
    r = b["rows"][0]
    assert r["creator_commission_signed"] == -100.50
    assert r["partner_commission_signed"] == -50.25
    assert r["affiliate_ads_commission_signed"] == -10.00
    assert r["ref_month"] == "2026-03"
    assert r["channel"] == "tiktok"


def test_varios_meses_uma_linha_por_marca_e_competencia():
    rows = [_fact_row(date(2026, 1, 1), "apice"),
            _fact_row(date(2026, 1, 1), "barbours"),
            _fact_row(date(2026, 2, 1), "apice"),
            _fact_row(date(2026, 2, 1), "barbours")]
    db = FakeSession(rows)
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 1, 1), date(2026, 2, 28), today=HOJE)
    assert b["period_status"] == "complete_months"
    assert len(b["rows"]) == 4
    assert b["months_included"] == ["2026-01", "2026-02"]
    # nenhum agregado multimensal
    assert "total" not in b
    assert not any("total" in k for k in b)


def test_mes_parcial_nao_consulta_valores():
    """`rows=[]` E nenhuma consulta de valores emitida — a regra é não haver
    número, e a prova é o SQL não ter sido montado."""
    db = FakeSession([_fact_row(date(2026, 8, 1), "apice")])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 8, 1), date(2026, 8, 31), today=HOJE)
    assert b["period_status"] == "partial_month"
    assert b["rows"] == []
    assert db.sqls == []                      # zero consulta
    assert b["affiliate_refreshed_at"] is None


def test_intervalo_desalinhado_nao_consulta_valores():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 5), date(2026, 4, 20), today=HOJE)
    assert b["period_status"] == "not_month_aligned"
    assert b["rows"] == []
    assert db.sqls == []


def test_filtro_de_marcas_e_repassado():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31),
        brand_keys=["apice"], today=HOJE)
    assert any("f.brand = ANY(:brands)" in s for s in db.sqls)


def test_lista_de_marcas_vazia_e_no_eligible_brand():
    db = FakeSession([])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31),
        brand_keys=[], today=HOJE)
    assert b["availability_status"] == "no_eligible_brand"
    assert b["rows"] == []
    assert db.sqls == []


def test_tiktok_fora_do_filtro_nao_produz_linha():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    b = acs.build_affiliate_costs_block(
        db, [perf_svc.ML_ID, perf_svc.SHOPEE_ID],
        date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["rows"] == []
    assert db.sqls == []
    canais = {c["channel"]: c["availability_status"] for c in b["channels"]}
    assert canais == {"mercadolivre": "unavailable_no_source",
                      "shopee": "unavailable_no_source"}
    assert "tiktok" not in canais


def test_ml_e_shopee_sempre_unavailable_no_source():
    """Disponibilidade do TikTok jamais os torna disponíveis (§23.6.1)."""
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    canais = {c["channel"]: c["availability_status"] for c in b["channels"]}
    assert canais["tiktok"] == "available"
    assert canais["mercadolivre"] == "unavailable_no_source"
    assert canais["shopee"] == "unavailable_no_source"
    # ordem FIXA, nunca por valor
    assert [c["channel"] for c in b["channels"]] == list(acs.CHANNEL_ORDER)


def test_zero_medido_e_preservado_como_zero():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice",
                                creator=Decimal("0"), partner=Decimal("0"),
                                ads=Decimal("0"))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    r = b["rows"][0]
    assert r["creator_commission_signed"] == 0.0
    assert r["creator_commission_signed"] is not None


def test_ausencia_e_distinta_de_zero():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice",
                                creator=None, partner=Decimal("0"), ads=None)])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    r = b["rows"][0]
    assert r["creator_commission_signed"] is None      # ausencia
    assert r["partner_commission_signed"] == 0.0       # zero medido
    assert r["affiliate_ads_commission_signed"] is None


def test_marca_ausente_na_competencia_nao_vira_zero():
    """Só duas marcas na fact → só duas linhas. Nenhuma linha fabricada."""
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice", presentes=2),
                      _fact_row(date(2026, 3, 1), "barbours", presentes=2)])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert len(b["rows"]) == 2
    assert {r["brand"] for r in b["rows"]} == {"apice", "barbours"}


def test_cobertura_incompleta_coexiste_com_available():
    """As quatro dimensões são independentes."""
    db = FakeSession([_fact_row(date(2025, 6, 1), "apice", presentes=1)])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2025, 6, 1), date(2025, 6, 30), today=HOJE)
    assert b["availability_status"] == "available"
    assert b["period_status"] == "complete_month"
    assert b["coverage_status"] == "incomplete_brand_coverage"
    assert b["freshness_status"] == "manual_snapshot"
    assert b["rows"][0]["brands_present_in_month"] == 1


def test_cobertura_geral_e_conservadora():
    """Uma competência incompleta torna o geral incompleto (§23.6.1)."""
    db = FakeSession([_fact_row(date(2026, 1, 1), "apice", presentes=5),
                      _fact_row(date(2026, 2, 1), "apice", presentes=3)])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 1, 1), date(2026, 2, 28), today=HOJE)
    assert b["coverage_status"] == "incomplete_brand_coverage"
    detalhe = {r["ref_month"]: r["coverage_status"] for r in b["rows"]}
    assert detalhe["2026-01"] == "complete"
    assert detalhe["2026-02"] == "incomplete_brand_coverage"


def test_freshness_nunca_e_fresh_ou_stale():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["freshness_status"] == "manual_snapshot"


def test_affiliate_refreshed_at_vem_da_fact():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice",
                                synced=date(2026, 8, 20)),
                      _fact_row(date(2026, 3, 1), "barbours",
                                synced=date(2026, 8, 25))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["affiliate_refreshed_at"] == "2026-08-25"   # MAX(synced_at)


def test_source_watermark_vem_do_sync_state_e_e_distinto():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice",
                                synced=date(2026, 8, 25),
                                watermark=date(2026, 8, 24))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["source_watermark"] == "2026-08-24"
    assert b["affiliate_refreshed_at"] == "2026-08-25"
    assert b["source_watermark"] != b["affiliate_refreshed_at"]
    assert any("last_successful_upper_bound" in s for s in db.sqls)


def test_retorno_e_tipado_sem_campo_numerico():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["return_availability"] == "unavailable_no_attributed_revenue"
    assert "receita atribuída" in b["return_note"]
    for proibido in ("return_amount", "roi", "roas", "attributed_revenue"):
        assert proibido not in b


def test_sem_soma_total_ou_razao_no_payload():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    chaves = set(b) | {k for r in b["rows"] for k in r}
    for proibido in ("total", "sum", "soma", "pct", "ratio", "razao",
                     "affiliate_cost_total", "gmv"):
        assert not any(proibido in k.lower() for k in chaves), proibido


def test_sem_select_estrela_e_colunas_explicitas():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    for s in db.sqls:
        assert not re.search(r"SELECT\s+\*", s, re.I)


def test_modulo_nao_usa_abs():
    """Verificação estrutural: nenhuma chamada a `abs()` no caminho."""
    import ast
    import inspect
    arvore = ast.parse(inspect.getsource(acs))
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            nome = getattr(no.func, "id", "") or getattr(no.func, "attr", "")
            assert nome != "abs"


# ===========================================================================
# Isolamento de falha
# ===========================================================================

def test_falha_de_banco_devolve_error_sanitizado():
    """A prova de sanitizacao e' de IDENTIDADE, nao de substring: todo texto do
    bloco de erro tem de ser uma das constantes fixas do modulo. Assim nenhum
    fragmento da excecao entra, por construcao. `SOURCE_NOTE` nomeia a fact de
    proposito — e' procedencia versionada no §23, nao vazamento."""
    db = FakeSession(explode=True)
    b = acs.safe_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["availability_status"] == "error"
    assert b["rows"] == []
    assert b["affiliate_refreshed_at"] is None
    assert b["source_watermark"] is None

    permitidos = {acs.RETURN_NOTE, acs.SOURCE_NOTE, acs.LIMITATION_NOTE,
                  acs.ERROR_NOTE, acs.NO_SOURCE_NOTE, acs.PARTIAL_NOTE}
    textos = [b["return_note"], b["source_note"], b["limitation_note"]]
    textos += [c["reason_note"] for c in b["channels"]]
    for texto in textos:
        assert texto in permitidos, texto

    # e nenhum artefato de driver, DSN ou credencial no payload inteiro
    plano = " ".join(str(v) for v in b.values())
    for proibido in ("SELECT ", "server closed", "OperationalError",
                     "sqlalchemy", "password", "postgresql://", "sslmode",
                     "Traceback", "5432"):
        assert proibido not in plano, proibido


def test_erro_de_programacao_nao_e_escondido(monkeypatch):
    """`except Exception` amplo esconderia bug do próprio bloco sob "erro de
    fonte", e o defeito viveria em produção parecendo indisponibilidade."""
    def bug(*a, **kw):
        raise KeyError("bug de programacao")

    monkeypatch.setattr(acs, "build_affiliate_costs_block", bug)
    with pytest.raises(KeyError):
        acs.safe_affiliate_costs_block(
            FakeSession(), TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)


def test_bloco_valida_contra_o_schema():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice",
                                watermark=date(2026, 8, 24))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    validado = AffiliateCostsBlock.model_validate(b)
    assert validado.availability_status == "available"
    assert validado.rows[0].creator_commission_signed == -100.50


def test_bloco_de_erro_valida_contra_o_schema():
    b = acs.safe_affiliate_costs_block(
        FakeSession(explode=True), TODOS,
        date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert AffiliateCostsBlock.model_validate(b).availability_status == "error"


# ===========================================================================
# Composicao em /canais: o corpo historico nao muda
# ===========================================================================

def test_get_canais_nao_produz_o_bloco_nem_consulta_a_fact():
    """O bloco e' composto na ROTA, nao em `get_canais`. Prova ESTRUTURAL: o
    servico nao devolve a chave e emite exatamente UMA consulta — a mesma de
    antes. Isso e' o que mantem o corpo historico de /canais inalterado."""
    from tests.test_canais_channel_rows import FakeMappingSession, _row

    db = FakeMappingSession([[_row("barbours", perf_svc.TIKTOK_ID, gmv=1000)]])
    result = perf_svc.get_canais(db, "tiktok", 2026, 5)

    assert "affiliate_costs" not in result
    assert len(db.captured_params) == 1          # nenhuma consulta extra
    assert not any(k.startswith("affiliate")
                   for k in (db.captured_params[0] or {}))


def test_channel_rows_identico_com_e_sem_o_bloco():
    """Invariancia ESTRUTURAL: compara `channel_rows` do HEAD do servico contra
    o payload da rota composta. Se o bloco tocasse a matriz, divergiria."""
    from tests.test_canais_channel_rows import FakeMappingSession, _row

    rows = [_row("barbours", perf_svc.TIKTOK_ID, gmv=1000, orders=10,
                 total_fees=-300, total_fees_n=30)]
    sozinho = perf_svc.get_canais(FakeMappingSession([rows]), "tiktok", 2026, 5)

    composto = dict(sozinho)
    composto["affiliate_costs"] = acs.safe_affiliate_costs_block(
        FakeSession([_fact_row(date(2026, 5, 1), "barbours")]),
        [perf_svc.TIKTOK_ID], date(2026, 5, 1), date(2026, 5, 31), today=HOJE)

    assert composto["channel_rows"] == sozinho["channel_rows"]
    assert composto["channel_medians"] == sozinho["channel_medians"]
    assert composto["kpis"] == sozinho["kpis"]
    assert composto["brands"] == sozinho["brands"]
    # e o bloco realmente entrou
    assert composto["affiliate_costs"]["availability_status"] == "available"


def test_canais_permanece_valido_quando_o_bloco_falha():
    """A pagina inteira continua servivel com o bloco em `error`."""
    from tests.test_canais_channel_rows import FakeMappingSession, _row

    rows = [_row("barbours", perf_svc.TIKTOK_ID, gmv=1000, orders=10)]
    payload = perf_svc.get_canais(FakeMappingSession([rows]), "tiktok", 2026, 5)
    payload["affiliate_costs"] = acs.safe_affiliate_costs_block(
        FakeSession(explode=True), [perf_svc.TIKTOK_ID],
        date(2026, 5, 1), date(2026, 5, 31), today=HOJE)

    validado = CanaisResponse.model_validate(payload)
    assert validado.channel_rows                       # matriz intacta
    assert validado.affiliate_costs.availability_status == "error"
    assert validado.affiliate_costs.rows == []
    # ML/Shopee nao ficaram indisponiveis por causa desta falha
    canais = {c.channel: c.availability_status
              for c in validado.affiliate_costs.channels}
    assert canais["tiktok"] == "error"


def test_bloco_ausente_ainda_valida_o_schema():
    """`affiliate_costs` e' opcional: um payload sem ele segue valido."""
    from tests.test_canais_channel_rows import FakeMappingSession, _row

    rows = [_row("barbours", perf_svc.TIKTOK_ID, gmv=1000, orders=10)]
    payload = perf_svc.get_canais(FakeMappingSession([rows]), "tiktok", 2026, 5)
    assert CanaisResponse.model_validate(payload).affiliate_costs is None


def test_rota_resolve_a_mesma_janela_que_get_canais():
    """Duas resolucoes independentes de periodo divergiriam calada."""
    assert perf_svc.canais_period_bounds(None, 2026, 5) == (
        date(2026, 5, 1), date(2026, 5, 31))


# ===========================================================================
# F2 — fuso operacional America/Sao_Paulo
# ===========================================================================

def test_fronteira_utc_brt_no_primeiro_dia_do_mes():
    """A fronteira que importa: entre 21h e 00h BRT, o UTC ja virou o dia.

    No dia 1, isso decidiria que o mes anterior "ainda esta em aberto" — ou o
    contrario — um dia inteiro fora de hora, escondendo competencia fechada.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    SP = ZoneInfo("America/Sao_Paulo")

    # 2026-08-31 23:30 BRT == 2026-09-01 02:30 UTC: os dois discordam do DIA.
    instante = datetime(2026, 9, 1, 2, 30, tzinfo=timezone.utc)
    assert instante.date() == date(2026, 9, 1)                    # UTC
    assert instante.astimezone(SP).date() == date(2026, 8, 31)    # BRT

    # Pelo relogio BRT, agosto AINDA nao fechou nesse instante...
    assert acs.classify_period(date(2026, 8, 1), date(2026, 8, 31),
                               instante.astimezone(SP).date()) == \
        ("partial_month", [])
    # ...e pelo UTC teria fechado cedo demais.
    assert acs.classify_period(date(2026, 8, 1), date(2026, 8, 31),
                               instante.date()) == \
        ("complete_month", ["2026-08"])


def test_modulo_nao_chama_date_today():
    """Verificacao ESTRUTURAL na AST, nao na prosa."""
    import ast
    import inspect

    arvore = ast.parse(inspect.getsource(acs))
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute):
            alvo = getattr(no.func.value, "id", "")
            assert not (alvo == "date" and no.func.attr == "today"), \
                "use today_brt(), nunca date.today()"


def test_today_brt_vem_do_helper_existente_e_resolve_em_sao_paulo():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    assert acs.today_brt.__module__ == "app.deps.period"
    esperado = datetime.now(timezone.utc).astimezone(
        ZoneInfo("America/Sao_Paulo")).date()
    assert acs.today_brt() == esperado


# ===========================================================================
# F3 — competencia inteiramente ausente
# ===========================================================================

def test_dois_meses_pedidos_segundo_totalmente_ausente():
    """Fevereiro sumiu da fonte. Nao pode desaparecer da analise."""
    db = FakeSession(
        [_fact_row(date(2026, 1, 1), f"m{i}", presentes=5) for i in range(5)]
        + [_mes_ausente(date(2026, 2, 1))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 1, 1), date(2026, 2, 28), today=HOJE)

    assert b["months_included"] == ["2026-01", "2026-02"]   # integral
    assert b["coverage_status"] == "incomplete_brand_coverage"
    assert len(b["rows"]) == 5
    assert {r["ref_month"] for r in b["rows"]} == {"2026-01"}
    assert not any(r["ref_month"] == "2026-02" for r in b["rows"])


def test_primeiro_completo_e_segundo_ausente_nao_vira_complete():
    """Contraprova direta do bug: sem a correcao so' janeiro seria avaliado e
    a cobertura geral sairia `complete`."""
    db = FakeSession(
        [_fact_row(date(2026, 1, 1), f"m{i}", presentes=5) for i in range(5)]
        + [_mes_ausente(date(2026, 2, 1))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 1, 1), date(2026, 2, 28), today=HOJE)
    assert b["coverage_status"] != "complete"
    # o detalhe de janeiro segue `complete` — as duas leituras convivem, e e'
    # por isso que o agregado precisa ser conservador
    assert all(r["coverage_status"] == "complete" for r in b["rows"])


def test_todos_os_meses_ausentes_nao_e_erro_nem_zero():
    db = FakeSession([_mes_ausente(date(2026, 1, 1)),
                      _mes_ausente(date(2026, 2, 1))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 1, 1), date(2026, 2, 28), today=HOJE)
    assert b["availability_status"] == "available"     # a fonte foi lida
    assert b["rows"] == []                             # e nao tinha nada
    assert b["coverage_status"] == "incomplete_brand_coverage"
    assert b["months_included"] == ["2026-01", "2026-02"]
    assert "zero" in b["limitation_note"].lower()
    assert b["freshness_status"] == "unknown"
    assert b["affiliate_refreshed_at"] is None


def test_filtro_de_marca_com_ausencia_nao_confunde_recorte_com_competencia():
    """A competencia TEM 5 marcas; o filtro pediu uma que nao esta la.

    Recorte vazio nao e' mes ausente: a cobertura continua dizendo 5.
    """
    db = FakeSession([_mes_ausente(date(2026, 3, 1), presentes=5)])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31),
        brand_keys=["inexistente"], today=HOJE)
    assert b["availability_status"] == "no_eligible_brand"
    assert b["coverage_status"] == "complete"
    assert b["rows"] == []


def test_zero_medido_continua_diferente_de_mes_ausente():
    db = FakeSession([
        _fact_row(date(2026, 1, 1), "apice", creator=Decimal("0"),
                  partner=Decimal("0"), ads=Decimal("0"), presentes=5),
        _mes_ausente(date(2026, 2, 1)),
    ])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 1, 1), date(2026, 2, 28), today=HOJE)

    assert len(b["rows"]) == 1
    r = b["rows"][0]
    assert r["ref_month"] == "2026-01"
    assert r["creator_commission_signed"] == 0.0        # medido zero
    assert r["creator_commission_signed"] is not None
    assert "2026-02" in b["months_included"]            # ausente, mas listado
    assert not any(x["ref_month"] == "2026-02" for x in b["rows"])


# ===========================================================================
# F1 — uma consulta so'
# ===========================================================================

def test_uma_unica_consulta_para_valores_cobertura_e_watermark():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice",
                                watermark=date(2026, 8, 24))])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert len(db.sqls) == 1
    assert "last_successful_upper_bound" in db.sqls[0]
    assert b["source_watermark"] == "2026-08-24"


def test_cobertura_conta_sem_o_filtro_de_marca():
    """Estrutural: a CTE de cobertura nao pode carregar o filtro de marca."""
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31),
        brand_keys=["apice"], today=HOJE)
    sql = db.sqls[0]
    cobertura = sql[sql.index("cobertura AS"):sql.index("recorte AS")]
    assert ":brands" not in cobertura
    assert ":brands" in sql          # mas o recorte usa


# ===========================================================================
# F4 — log sanitizado
# ===========================================================================

def test_log_de_falha_nao_tem_traceback_nem_dado_sensivel(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger=acs.__name__):
        acs.safe_affiliate_costs_block(
            FakeSession(explode=True), TODOS,
            date(2026, 3, 1), date(2026, 3, 31), today=HOJE)

    assert len(caplog.records) == 1
    registro = caplog.records[0]
    assert registro.getMessage() == acs.LOG_QUERY_FAILURE
    assert registro.exc_info is None          # sem traceback
    assert registro.exc_text is None
    for proibido in ("SELECT", "server closed", "OperationalError",
                     "sqlalchemy.exc", "Traceback", "password", "5432",
                     "postgresql://"):
        assert proibido not in caplog.text, proibido


def test_bug_de_programacao_ainda_sobe(monkeypatch):
    def bug(*a, **kw):
        raise KeyError("bug")

    monkeypatch.setattr(acs, "build_affiliate_costs_block", bug)
    with pytest.raises(KeyError):
        acs.safe_affiliate_costs_block(
            FakeSession(), TODOS, date(2026, 3, 1), date(2026, 3, 31),
            today=HOJE)


# ===========================================================================
# F5 — frescor so' quando ha fotografia consultada
# ===========================================================================

def test_tiktok_fora_do_filtro_nao_afirma_frescor():
    b = acs.build_affiliate_costs_block(
        FakeSession([]), [perf_svc.ML_ID, perf_svc.SHOPEE_ID],
        date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["freshness_status"] == "unknown"
    assert b["affiliate_refreshed_at"] is None
    assert b["source_watermark"] is None


def test_marcas_vazias_nao_afirmam_frescor():
    b = acs.build_affiliate_costs_block(
        FakeSession([]), TODOS, date(2026, 3, 1), date(2026, 3, 31),
        brand_keys=[], today=HOJE)
    assert b["freshness_status"] == "unknown"


def test_periodo_parcial_nao_afirma_frescor():
    b = acs.build_affiliate_costs_block(
        FakeSession([]), TODOS, date(2026, 8, 1), date(2026, 8, 31),
        today=HOJE)
    assert b["freshness_status"] == "unknown"


def test_erro_nao_afirma_frescor():
    b = acs.safe_affiliate_costs_block(
        FakeSession(explode=True), TODOS,
        date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["freshness_status"] == "unknown"


def test_manual_snapshot_somente_com_fotografia_lida():
    b = acs.build_affiliate_costs_block(
        FakeSession([_fact_row(date(2026, 3, 1), "apice")]),
        TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["freshness_status"] == "manual_snapshot"
    assert b["affiliate_refreshed_at"] is not None


# ===========================================================================
# UE2-C — frescor IMPLEMENTADO, ainda NAO PUBLICADO (Task 2/3)
# ===========================================================================

def _aware(dia, hora=9):
    """`finished_at` da auditoria: TIMESTAMPTZ. 09:00 UTC = 06:00 BRT."""
    from datetime import datetime, timezone
    return datetime(2026, 8, dia, hora, 0, tzinfo=timezone.utc)


def _naive(dia, hora=21, minuto=3):
    """Watermark: TIMESTAMP WITHOUT TIME ZONE."""
    from datetime import datetime
    return datetime(2026, 8, dia, hora, minuto)


def test_frescor_execucao_recente_e_lote_esperado_e_fresh():
    v = acs.classify_freshness(_aware(28), _naive(27), _aware(28, 12))
    assert v["status"] == "fresh"
    assert v["late_batches"] == 0


def test_frescor_um_lote_atrasado_ja_e_stale():
    v = acs.classify_freshness(_aware(28), _naive(26), _aware(28, 12))
    assert v["status"] == "stale"
    assert v["execution_recent"] is True      # o job rodou...
    assert v["watermark_current"] is False    # ...mas a fonte nao avancou
    assert v["late_batches"] == 1
    assert v["escalate"] is False


def test_frescor_dois_lotes_escalam_o_alerta():
    v = acs.classify_freshness(_aware(28), _naive(25), _aware(28, 12))
    assert v["late_batches"] == 2 and v["escalate"] is True


def test_frescor_execucao_antiga_e_stale():
    assert acs.classify_freshness(_aware(25), _naive(24),
                                  _aware(28, 12))["status"] == "stale"


def test_frescor_watermark_a_frente_nunca_produz_atraso_negativo():
    v = acs.classify_freshness(_aware(28), _naive(28), _aware(28, 12))
    assert v["status"] == "fresh"
    assert v["late_batches"] == 0


def test_frescor_sem_auditoria_ou_watermark_e_unknown():
    assert acs.classify_freshness(None, _naive(27), _aware(28))["status"] == "unknown"
    assert acs.classify_freshness(_aware(28), None, _aware(28))["status"] == "unknown"


def test_frescor_recusa_finished_at_ingenuo():
    from datetime import datetime
    with pytest.raises(ValueError):
        acs.classify_freshness(datetime(2026, 8, 28, 9, 0), _naive(27),
                               _aware(28, 12))


def test_frescor_nao_converte_o_watermark_naive():
    """O watermark e' TIMESTAMP WITHOUT TIME ZONE: so' a parte de data entra,
    sem conversao e sem rotulo de fuso."""
    import inspect
    src = inspect.getsource(acs.classify_freshness)
    assert "watermark.astimezone" not in src
    assert "watermark_date = watermark.date()" in src


# --- a parte que prova o que AINDA NAO acontece ---------------------------

def test_bloco_publico_continua_em_manual_snapshot():
    db = FakeSession([_fact_row(date(2026, 3, 1), "apice")])
    b = acs.build_affiliate_costs_block(
        db, TODOS, date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert b["freshness_status"] == "manual_snapshot"


def test_nenhum_caminho_publico_devolve_fresh_ou_stale():
    """Varre TODOS os estados do bloco: nenhum pode expor `fresh`/`stale`
    enquanto a Task 3/3 nao comprovar a rotina."""
    cenarios = [
        ("mes completo", FakeSession([_fact_row(date(2026, 3, 1), "apice")]),
         TODOS, date(2026, 3, 1), date(2026, 3, 31), {}),
        ("mes parcial", FakeSession([]), TODOS,
         date(2026, 8, 1), date(2026, 8, 31), {}),
        ("desalinhado", FakeSession([]), TODOS,
         date(2026, 6, 15), date(2026, 7, 14), {}),
        ("sem tiktok", FakeSession([]), [perf_svc.ML_ID, perf_svc.SHOPEE_ID],
         date(2026, 3, 1), date(2026, 3, 31), {}),
        ("marcas vazias", FakeSession([]), TODOS,
         date(2026, 3, 1), date(2026, 3, 31), {"brand_keys": []}),
        ("mes ausente", FakeSession([_mes_ausente(date(2026, 3, 1))]), TODOS,
         date(2026, 3, 1), date(2026, 3, 31), {}),
    ]
    for nome, db, mkts, ini, fim, kw in cenarios:
        b = acs.build_affiliate_costs_block(db, mkts, ini, fim, today=HOJE, **kw)
        assert b["freshness_status"] in ("manual_snapshot", "unknown"), nome
        assert b["freshness_status"] not in ("fresh", "stale"), nome

    erro = acs.safe_affiliate_costs_block(
        FakeSession(explode=True), TODOS,
        date(2026, 3, 1), date(2026, 3, 31), today=HOJE)
    assert erro["freshness_status"] == "unknown"


def test_a_classificacao_nao_esta_ligada_ao_payload():
    """Prova ESTRUTURAL: `build_affiliate_costs_block` nao chama a funcao."""
    import inspect
    src = inspect.getsource(acs.build_affiliate_costs_block)
    assert "classify_freshness" not in src
    assert '"freshness_status": "manual_snapshot"' in src


def test_schema_publico_nao_ganhou_campo_novo():
    from app.schemas.performance import AffiliateCostsBlock
    assert len(AffiliateCostsBlock.model_fields) == 13


def test_falha_da_fonte_de_afiliados_nao_derruba_o_resto_de_canais():
    from tests.test_canais_channel_rows import FakeMappingSession, _row

    linhas = [_row("barbours", perf_svc.TIKTOK_ID, gmv=1000, orders=10)]
    payload = perf_svc.get_canais(FakeMappingSession([linhas]), "tiktok", 2026, 5)
    payload["affiliate_costs"] = acs.safe_affiliate_costs_block(
        FakeSession(explode=True), [perf_svc.TIKTOK_ID],
        date(2026, 5, 1), date(2026, 5, 31), today=HOJE)

    validado = CanaisResponse.model_validate(payload)
    assert validado.channel_rows
    assert validado.affiliate_costs.availability_status == "error"
    assert validado.affiliate_costs.freshness_status == "unknown"
