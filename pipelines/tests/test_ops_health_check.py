"""
Testes de pipelines/ops/health_check.py: frescor de EXECUCAO (via
audit.source_sync_run, contra uma lista EXPLICITA de fontes esperadas —
uma fonte sem nenhum historico e' sempre stale, nunca "ausente e' OK") e
frescor de DADO (MAX(date/refreshed_at/ref_month) avaliado contra
threshold, com cadencia manual/mensal tratada separadamente para nao gerar
falso positivo), alem das invariantes do Bug 8 (reaproveitadas de
monitor_bug8_invariants, nao duplicadas).

Usa conexoes falsas — nenhum banco real e' tocado. Nunca depende do Data
Mart (so' consulta o Neon).
"""
import contextlib
import io
import re
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path

import psycopg2
import pytest

import pipelines.ops.health_check as hc

MODULE_PATH = Path(hc.__file__)
NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def by_name(statuses, name):
    return next(s for s in statuses if s.source_name == name)


# ---------------------------------------------------------------------------
# Gate AVH-4C — fixtures da dimensao `avoe_snapshot`
# ---------------------------------------------------------------------------
# As duas consultas da Avoe sao as do SERVICO (`SQL_CANDIDATAS` e
# `SQL_RUNS_AUDITORIA`), traduzidas para psycopg2. Estes construtores produzem
# o MESMO formato de linha que aquele SQL devolve — e' isso que permite ao
# health check usar `_valida_captura`/`_associa_run` do servico sem adaptador.

def avoe_cand(dias_captura=3, metas=7, canais=24, snapshot_id="c8f6be83" + "0" * 24,
              importado=None, **over):
    """Uma candidata a captura, VALIDA por default."""
    captura = NOW - timedelta(days=dias_captura)
    imp = importado if importado is not None else captura + timedelta(days=1)
    linha = {
        "captured_at": captura,
        "targets_count": metas,
        "target_snapshots": 1,
        "target_imports": 1,
        "target_grao": metas,
        "target_imp_min": imp,
        "target_imp_max": imp,
        "target_snapshot_id": snapshot_id,
        "currency_code": "BRL",
        "currency_status": "assumed_unconfirmed",
        "moedas": 1,
        "status_moeda": 1,
        "channel_rows_count": canais,
        "channel_snapshots": 1,
        "channel_imports": 1,
        "channel_grao": canais,
        "channel_imp_min": imp,
        "channel_imp_max": imp,
        "channel_snapshot_id": snapshot_id,
    }
    linha.update(over)
    return linha


def avoe_run(sync_run_id=285, status="success", cand=None, dias_captura=3):
    """Um run de auditoria que COBRE a janela de `imported_at` da candidata."""
    c = cand if cand is not None else avoe_cand(dias_captura=dias_captura)
    return {
        "sync_run_id": sync_run_id,
        "status": status,
        "started_at": c["target_imp_min"] - timedelta(seconds=2),
        "finished_at": c["target_imp_max"] + timedelta(seconds=2),
    }


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_sql = ""
        self._last_params = None

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append(norm)
        self._last_sql = norm
        self._last_params = params
        # Gate AVH-4C: falha de leitura injetada, para exercitar o ramo
        # `error` da dimensao da Avoe sem tocar banco.
        if self.conn.avoe_erro and "proxy_avoe" in norm:
            raise psycopg2.Error("falha simulada na leitura da Avoe")

    def fetchone(self):
        sql = self._last_sql
        params = self._last_params or ()

        if "started_at, finished_at, status, error_message" in sql:
            return self.conn.last_run.get(params[0])
        if "MAX(finished_at)" in sql and "status = 'success'" in sql:
            return {"t": self.conn.last_success.get(params[0])}
        if "fact_tiktok_product_daily" in sql:
            return {"m": self.conn.tiktok_produtos_max}
        if "fact_ml_produto_ranking" in sql:
            return {"m": self.conn.ml_produtos_max}
        # Gate S3: as duas fatos novas. Ramos EXPLICITOS de proposito — ver o
        # fallback estrito no fim deste metodo.
        if "MAX(synced_at) AS m FROM marts.fact_ml_cross_company_summary" in sql:
            return {"m": self.conn.ml_cross_company_synced_at}
        if "MAX(date) AS m FROM marts.fact_tiktok_channel_efficiency_daily" in sql:
            return {"m": self.conn.tiktok_channel_efficiency_max}
        if "ref_month) AS m FROM marts." in sql:
            return {"m": self.conn.shopee_produtos_max}
        # Gate UE2-C: watermark do sync de afiliados. Ramo EXPLICITO — o
        # fallback estrito abaixo so' cobre `AS m`, e sem este ramo a consulta
        # cairia no `{"n": 0}` e o teste ficaria verde sem exercitar nada.
        if "last_successful_upper_bound AS w" in sql:
            return {"w": self.conn.affiliate_watermark}
        # Gate UE8-I4: as duas leituras da cobertura de descontos. Ramos
        # EXPLICITOS pela mesma razao dos de cima — e ambos devolvem DICT,
        # porque a conexao real usa `RealDictCursor`. Foi este fake que pegou o
        # acesso por posicao que teria quebrado em toda execucao de producao.
        if "finished_at, source_max_date" in sql:
            return self.conn.discounts_last_run
        if "MAX(ref_date) AS fact_max_ref_date" in sql:
            return {"fact_max_ref_date": self.conn.discounts_fact_max}
        # Gate AVH-4C: as duas consultas da Avoe usam `fetchall`, nunca
        # `fetchone`. Cair aqui significa consulta nova sem ramo.
        for marker, value in self.conn.bug8_scalars:
            if marker in sql:
                return {"n": value}
        # Fallback ESTRITO para consultas de frescor: qualquer `... AS m` que nao
        # tenha ramo explicito acima e' erro de teste, nao dado ausente. Sem esta
        # guarda, uma consulta nova cairia no `{"n": 0}` e o teste ficaria verde
        # sem exercitar nada.
        if "AS m FROM marts." in sql:
            raise AssertionError(
                f"consulta de frescor sem ramo explicito no FakeCursor: {sql[:120]}")
        return {"n": 0}

    def fetchall(self):
        sql = self._last_sql
        if "marketplace_id, MAX(date)" in sql:
            return self.conn.daily_freshness_rows
        # Gate AVH-4C. Ramos EXPLICITOS, e as duas consultas vem do SERVICO —
        # os marcadores conferem que e' o SQL dele, nao uma copia local.
        if "WITH capturas AS" in sql and "proxy_avoe" in sql:
            return self.conn.avoe_candidatas
        if ("sync_run_id, status, started_at, finished_at" in sql
                and "audit.source_sync_run" in sql):
            return self.conn.avoe_runs
        return []

    def close(self):
        pass


_UNSET = object()


class FakeConn:
    def __init__(self, last_run=None, last_success=None, daily_freshness_rows=None,
                 tiktok_produtos_max=_UNSET, ml_produtos_max=_UNSET, shopee_produtos_max=_UNSET,
                 bug8_scalars=None,
                 ml_cross_company_synced_at=_UNSET, tiktok_channel_efficiency_max=_UNSET,
                 affiliate_watermark=_UNSET,
                 discounts_last_run=_UNSET, discounts_fact_max=_UNSET,
                 avoe_candidatas=_UNSET, avoe_runs=_UNSET, avoe_erro=False):
        self.executed = []
        self.closed = False
        # Gate AVH-4C: estado SAUDAVEL por default — captura valida de 3 dias
        # com um run `success` que cobre o `imported_at`. Mesma convencao das
        # outras dimensoes: o default nao deve reprovar nada, para que cada
        # teste isole UMA divergencia.
        self.avoe_candidatas = (
            [avoe_cand(dias_captura=3)] if avoe_candidatas is _UNSET
            else avoe_candidatas)
        self.avoe_runs = [avoe_run()] if avoe_runs is _UNSET else avoe_runs
        self.avoe_erro = avoe_erro
        self.last_run = last_run or {}
        self.last_success = last_success or {}
        self.daily_freshness_rows = daily_freshness_rows if daily_freshness_rows is not None else [
            {"marketplace_id": 1, "max_date": TODAY},
            {"marketplace_id": 2, "max_date": TODAY},
            {"marketplace_id": 3, "max_date": TODAY},
        ]
        self.tiktok_produtos_max = TODAY if tiktok_produtos_max is _UNSET else tiktok_produtos_max
        self.ml_produtos_max = TODAY if ml_produtos_max is _UNSET else ml_produtos_max
        self.shopee_produtos_max = (TODAY - timedelta(days=40)) if shopee_produtos_max is _UNSET else shopee_produtos_max
        # Gate S3. `fact_ml_cross_company_summary` nao tem data de negocio: o
        # frescor vem de MAX(synced_at), um timestamp. `_evaluate_date_freshness`
        # chama `.date()` nele, entao o default e' um datetime com fuso.
        self.ml_cross_company_synced_at = (
            NOW - timedelta(hours=6) if ml_cross_company_synced_at is _UNSET
            else ml_cross_company_synced_at)
        # `fact_tiktok_channel_efficiency_daily` tem data diaria com teto D-1:
        # ontem e' o estado normal, nao defasagem.
        self.tiktok_channel_efficiency_max = (
            TODAY - timedelta(days=1) if tiktok_channel_efficiency_max is _UNSET
            else tiktok_channel_efficiency_max)
        # Gate UE2-C: `last_successful_upper_bound` e' TIMESTAMP WITHOUT TIME
        # ZONE — naive de proposito. O default e' o lote de ontem, que e' o
        # estado saudavel para uma execucao das 06:00 de hoje.
        self.affiliate_watermark = (
            datetime.combine(TODAY - timedelta(days=1), dt_time(21, 3))
            if affiliate_watermark is _UNSET else affiliate_watermark)
        # Gate UE8-I4: estado SAUDAVEL por default — a ultima execucao publicou
        # ate D-1 e a fato tem competencia ate D-1. `source_max_date` e
        # `ref_date` sao DATE (competencia), nunca timestamp tecnico.
        self.discounts_last_run = (
            {"finished_at": NOW - timedelta(hours=6),
             "source_max_date": TODAY - timedelta(days=1)}
            if discounts_last_run is _UNSET else discounts_last_run)
        self.discounts_fact_max = (
            TODAY - timedelta(days=1)
            if discounts_fact_max is _UNSET else discounts_fact_max)
        self.bug8_scalars = bug8_scalars or [
            ("HAVING COUNT(*) > 1", 0), ("IS NULL", 0), ("gmv < 0", 0),
            ("IS DISTINCT FROM 100", 0), ("ROUND(canceled_orders::numeric", 0),
            ("completed_orders = 0 AND canceled_orders > 0", 0),
        ]

    def _all_sources_fresh(self):
        return {
            s.source_name: {"started_at": NOW - timedelta(hours=1), "finished_at": NOW - timedelta(hours=1), "status": "success", "error_message": None}
            for s in hc.EXPECTED_SOURCES
        }

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def all_fresh_conn(**overrides):
    """FakeConn onde toda fonte esperada tem sucesso ha 1h (nunca stale) —
    usado como base neutra para testes que querem isolar UMA divergencia."""
    fresh_run = {s.source_name: {"started_at": NOW - timedelta(hours=1), "finished_at": NOW - timedelta(hours=1), "status": "success", "error_message": None} for s in hc.EXPECTED_SOURCES}
    fresh_success = {s.source_name: NOW - timedelta(hours=1) for s in hc.EXPECTED_SOURCES}
    kwargs = {"last_run": fresh_run, "last_success": fresh_success}
    kwargs.update(overrides)
    return FakeConn(**kwargs)


# ---------------------------------------------------------------------------
# fetch_source_statuses — lista explicita de fontes esperadas
# ---------------------------------------------------------------------------

def test_todas_as_fontes_esperadas_aparecem_sempre():
    conn = FakeConn(last_run={}, last_success={})
    statuses = hc.fetch_source_statuses(conn, now=NOW)
    assert len(statuses) == len(hc.EXPECTED_SOURCES)
    names = {s.source_name for s in statuses}
    assert names == {s.source_name for s in hc.EXPECTED_SOURCES}


def test_fonte_esperada_sem_nenhum_historico_e_sempre_stale():
    """Uma fonte esperada que NUNCA apareceu no audit log tem que ser
    reportada como stale/bloqueada — nunca omitida do relatorio e nunca
    tratada como OK por omissao (bug do desenho anterior, que so' iterava
    DISTINCT source_name)."""
    conn = FakeConn(last_run={}, last_success={})
    status = by_name(hc.fetch_source_statuses(conn, now=NOW), "ml_daily")
    assert status.stale is True
    assert status.last_status is None
    assert "nenhuma execucao registrada" in status.reason


def test_fonte_atualizada_dentro_do_threshold_nao_e_stale():
    success_time = NOW - timedelta(hours=5)
    conn = all_fresh_conn(
        last_run={"ml_daily": {"started_at": success_time, "finished_at": success_time, "status": "success", "error_message": None}},
        last_success={"ml_daily": success_time},
    )
    status = by_name(hc.fetch_source_statuses(conn, now=NOW), "ml_daily")
    assert status.stale is False
    assert status.hours_since_success == 5.0
    assert status.threshold_hours == 30


def test_fonte_atrasada_alem_do_threshold_e_stale():
    success_time = NOW - timedelta(hours=40)  # threshold ml_daily = 30h
    conn = all_fresh_conn(
        last_run={"ml_daily": {"started_at": success_time, "finished_at": success_time, "status": "success", "error_message": None}},
        last_success={"ml_daily": success_time},
    )
    status = by_name(hc.fetch_source_statuses(conn, now=NOW), "ml_daily")
    assert status.stale is True
    assert "acima do limite" in status.reason


def test_shopee_orders_stats_ads_sao_fontes_separadas():
    """Bug corrigido nesta revisao: o schedule antigo so' cobria 'shopee',
    ignorando que daily_performance trata shopee/shopee-stats/shopee-ads
    como fontes distintas."""
    names = {s.source_name for s in hc.EXPECTED_SOURCES}
    assert {"shopee_daily", "shopee-stats_daily", "shopee-ads_daily"} <= names


def test_ultima_execucao_falhou_torna_stale_mesmo_com_sucesso_recente_dentro_do_threshold():
    """Regressao: uma falha na ULTIMA execucao tem que virar atencao
    sempre, mesmo que exista um sucesso anterior ainda dentro do threshold
    de frescor de execucao — senao um job quebrado mas com um sucesso
    "velho" recente o bastante fica mascarado de OK ate o threshold de
    frescor estourar por conta propria (as vezes dias depois)."""
    success_time = NOW - timedelta(hours=2)
    conn = all_fresh_conn(
        last_run={"ml_daily": {"started_at": NOW - timedelta(hours=1), "finished_at": NOW - timedelta(hours=1), "status": "failed", "error_message": "erro pontual"}},
        last_success={"ml_daily": success_time},
    )
    status = by_name(hc.fetch_source_statuses(conn, now=NOW), "ml_daily")
    assert status.execution_stale is False, "o sucesso anterior ainda esta dentro do threshold de frescor"
    assert status.last_run_failed is True
    assert status.stale is True, "last_run_failed sozinho ja tem que tornar 'stale' geral True"
    assert status.last_status == "failed"
    assert "FALHOU" in status.reason


def test_execution_stale_e_last_run_failed_sao_independentes():
    """Uma fonte pode estar execution_stale=False (sucesso recente) e
    last_run_failed=True (a execucao mais recente, ainda que nao seja a de
    sucesso, falhou) ao mesmo tempo — os dois campos precisam refletir
    dimensoes distintas, nao um so' 'stale' opaco."""
    success_time = NOW - timedelta(hours=1, minutes=30)
    conn = all_fresh_conn(
        last_run={"ml_daily": {"started_at": NOW - timedelta(minutes=10), "finished_at": NOW - timedelta(minutes=5), "status": "failed", "error_message": "timeout pontual"}},
        last_success={"ml_daily": success_time},
    )
    status = by_name(hc.fetch_source_statuses(conn, now=NOW), "ml_daily")
    assert status.execution_stale is False
    assert status.last_run_failed is True
    assert status.stale is True


def test_execution_stale_sem_falha_recente():
    """Caso simetrico: sucesso antigo alem do threshold, mas a ULTIMA
    execucao registrada foi a de sucesso (nao falhou) — execution_stale
    True, last_run_failed False."""
    success_time = NOW - timedelta(hours=40)  # alem do threshold de 30h de ml_daily
    conn = all_fresh_conn(
        last_run={"ml_daily": {"started_at": success_time, "finished_at": success_time, "status": "success", "error_message": None}},
        last_success={"ml_daily": success_time},
    )
    status = by_name(hc.fetch_source_statuses(conn, now=NOW), "ml_daily")
    assert status.execution_stale is True
    assert status.last_run_failed is False
    assert status.stale is True


def test_fonte_sem_nenhum_sucesso_registrado_e_sempre_stale():
    conn = all_fresh_conn(
        last_run={"ml_daily": {"started_at": NOW, "finished_at": NOW, "status": "failed", "error_message": "boom"}},
        last_success={"ml_daily": None},
    )
    status = by_name(hc.fetch_source_statuses(conn, now=NOW), "ml_daily")
    assert status.stale is True
    assert status.last_success_at is None
    assert status.last_error == "boom"


# ---------------------------------------------------------------------------
# fetch_data_freshness — frescor de DADO, threshold avaliado de verdade
# ---------------------------------------------------------------------------

def by_label_prefix(results, prefix):
    return next(r for r in results if r.label.startswith(prefix))


def test_dado_fresco_dentro_do_threshold_nao_e_stale():
    conn = FakeConn(daily_freshness_rows=[{"marketplace_id": 2, "max_date": TODAY}])
    results = hc.fetch_data_freshness(conn, today=TODAY)
    ml_row = by_label_prefix(results, "fact_marketplace_daily_performance[ml]")
    assert ml_row.stale is False
    assert ml_row.days_since == 0


def test_dado_atrasado_alem_do_threshold_fica_stale():
    old_date = TODAY - timedelta(days=hc.DAILY_DATA_FRESHNESS_THRESHOLD_DAYS + 1)
    conn = FakeConn(daily_freshness_rows=[{"marketplace_id": 2, "max_date": old_date}])
    results = hc.fetch_data_freshness(conn, today=TODAY)
    ml_row = by_label_prefix(results, "fact_marketplace_daily_performance[ml]")
    assert ml_row.stale is True
    assert "acima do limite" in ml_row.reason


def test_marketplace_ausente_no_resultado_fica_stale_com_motivo():
    conn = FakeConn(daily_freshness_rows=[{"marketplace_id": 2, "max_date": TODAY}])  # so' ml, falta tiktok/shopee
    results = hc.fetch_data_freshness(conn, today=TODAY)
    tiktok_row = by_label_prefix(results, "fact_marketplace_daily_performance[tiktok]")
    assert tiktok_row.stale is True
    assert tiktok_row.max_value is None
    assert "sem nenhuma linha" in tiktok_row.reason


def test_shopee_produtos_manual_mensal_nunca_fica_stale_por_threshold():
    """fact_shopee_product_monthly (ref_month) tem cadencia manual/mensal —
    um MAX(ref_month) de varios meses atras e' esperado e NAO pode, por si
    so', marcar o health check como ATENCAO (falso positivo que o desenho
    anterior cometia ao so' exibir o valor sem classificar a cadencia)."""
    conn = FakeConn(shopee_produtos_max=TODAY - timedelta(days=90))
    results = hc.fetch_data_freshness(conn, today=TODAY)
    shopee_row = by_label_prefix(results, f"marts.{hc.REAL_TABLE}[ref_month]")
    assert shopee_row.cadence == "manual_monthly"
    assert shopee_row.stale is False
    assert shopee_row.threshold_days is None
    assert "90d" in shopee_row.reason


def test_shopee_produtos_sem_nenhum_dado_fica_stale_mesmo_sendo_manual():
    conn = FakeConn(shopee_produtos_max=None)
    results = hc.fetch_data_freshness(conn, today=TODAY)
    shopee_row = by_label_prefix(results, f"marts.{hc.REAL_TABLE}[ref_month]")
    assert shopee_row.stale is True
    assert "sem nenhuma linha" in shopee_row.reason


def test_tiktok_produtos_atrasado_fica_stale():
    conn = FakeConn(tiktok_produtos_max=TODAY - timedelta(days=hc.DAILY_DATA_FRESHNESS_THRESHOLD_DAYS + 1))
    results = hc.fetch_data_freshness(conn, today=TODAY)
    row = by_label_prefix(results, "fact_tiktok_product_daily")
    assert row.stale is True


def test_ml_produtos_refreshed_at_com_timestamp_completo_funciona():
    conn = FakeConn(ml_produtos_max=datetime(2026, 7, 3, 3, 0, tzinfo=timezone.utc))
    results = hc.fetch_data_freshness(conn, today=TODAY)
    row = by_label_prefix(results, "fact_ml_produto_ranking")
    assert row.stale is False
    assert row.max_value == "2026-07-03"


def test_data_futura_em_fonte_diaria_e_erro_de_qualidade_nunca_fresco():
    """MAX(date) no futuro (ex.: bug de parsing/fuso) nunca pode ser
    interpretado como 'dado fresco' so' porque days_since < threshold —
    tem que ser sinalizado como erro de qualidade."""
    future_date = TODAY + timedelta(days=5)
    conn = FakeConn(daily_freshness_rows=[{"marketplace_id": 2, "max_date": future_date}])
    results = hc.fetch_data_freshness(conn, today=TODAY)
    ml_row = by_label_prefix(results, "fact_marketplace_daily_performance[ml]")
    assert ml_row.stale is True
    assert ml_row.days_since == -5
    assert "FUTURO" in ml_row.reason


def test_data_futura_em_fonte_manual_mensal_tambem_e_erro_de_qualidade():
    """Regressao do Bug 3 (ref_month projetado para meses futuros por bug
    de parsing): mesmo sendo cadencia manual/mensal (que normalmente NUNCA
    fica stale so' por estar 'atrasada'), uma data no futuro tem que ser
    sinalizada — 'no futuro' nao e' o mesmo tipo de desvio que 'atrasada'."""
    future_ref_month = TODAY.replace(day=1) + timedelta(days=95)  # alguns meses a frente
    conn = FakeConn(shopee_produtos_max=future_ref_month)
    results = hc.fetch_data_freshness(conn, today=TODAY)
    shopee_row = by_label_prefix(results, f"marts.{hc.REAL_TABLE}[ref_month]")
    assert shopee_row.cadence == "manual_monthly"
    assert shopee_row.stale is True
    assert "FUTURO" in shopee_row.reason


def test_data_no_dia_de_hoje_nao_e_tratada_como_futuro():
    """days_since == 0 e' o caso normal (dado de hoje), nao deve disparar
    o erro de qualidade de data futura."""
    conn = FakeConn(daily_freshness_rows=[{"marketplace_id": 2, "max_date": TODAY}])
    results = hc.fetch_data_freshness(conn, today=TODAY)
    ml_row = by_label_prefix(results, "fact_marketplace_daily_performance[ml]")
    assert ml_row.stale is False
    assert "FUTURO" not in ml_row.reason


# ---------------------------------------------------------------------------
# build_report — combinacao de status geral
# ---------------------------------------------------------------------------

def test_build_report_ok_quando_tudo_fresco_e_bug8_limpo():
    conn = all_fresh_conn()
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is True
    assert report["bug8_invariants"]["ok"] is True
    assert len(report["sources"]) == len(hc.EXPECTED_SOURCES)


def test_build_report_atencao_quando_fonte_de_execucao_stale():
    conn = FakeConn(last_run={}, last_success={})  # nenhuma fonte tem historico
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False


def test_build_report_atencao_quando_dado_stale():
    old_date = TODAY - timedelta(days=hc.DAILY_DATA_FRESHNESS_THRESHOLD_DAYS + 5)
    conn = all_fresh_conn(daily_freshness_rows=[{"marketplace_id": 1, "max_date": old_date}, {"marketplace_id": 2, "max_date": TODAY}, {"marketplace_id": 3, "max_date": TODAY}])
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False


def test_build_report_nao_falha_so_por_shopee_produtos_manual_estar_defasado():
    conn = all_fresh_conn(shopee_produtos_max=TODAY - timedelta(days=90))
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is True


def test_build_report_atencao_quando_bug8_tem_divergencia():
    conn = all_fresh_conn(bug8_scalars=[("HAVING COUNT(*) > 1", 3)])
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["bug8_invariants"]["ok"] is False


def test_build_report_atencao_quando_ultima_execucao_falhou_mesmo_com_sucesso_recente():
    """Regressao ponta-a-ponta do bug corrigido nesta revisao: antes,
    build_report() so' olhava para o campo agregado 'stale', que nao
    virava True quando a ultima execucao falhava mas um sucesso anterior
    ainda estava dentro do threshold — o status geral ficava OK
    incorretamente."""
    success_time = NOW - timedelta(hours=1)
    conn = all_fresh_conn(
        last_run={"ml_daily": {"started_at": NOW, "finished_at": NOW, "status": "failed", "error_message": "falha pontual"}},
        last_success={"ml_daily": success_time},
    )
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    ml_status = next(s for s in report["sources"] if s["source_name"] == "ml_daily")
    assert ml_status["execution_stale"] is False
    assert ml_status["last_run_failed"] is True
    assert ml_status["stale"] is True


def test_build_report_atencao_quando_dado_no_futuro():
    """Regressao: uma data no futuro em qualquer tabela de dado tem que
    reprovar o status geral, mesmo com todas as fontes de execucao
    frescas e o Bug 8 limpo."""
    future_date = TODAY + timedelta(days=10)
    conn = all_fresh_conn(daily_freshness_rows=[
        {"marketplace_id": 1, "max_date": TODAY}, {"marketplace_id": 2, "max_date": future_date}, {"marketplace_id": 3, "max_date": TODAY},
    ])
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    ml_row = next(d for d in report["data_freshness"] if d["label"].startswith("fact_marketplace_daily_performance[ml]"))
    assert ml_row["stale"] is True
    assert "FUTURO" in ml_row["reason"]


# ---------------------------------------------------------------------------
# run_bug8_check — nao vaza o print informativo de check_db_invariants
# ---------------------------------------------------------------------------

def test_run_bug8_check_suprime_o_print_informativo():
    import io
    from contextlib import redirect_stdout

    conn = all_fresh_conn()
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = hc.run_bug8_check(conn)
    assert buf.getvalue() == "", "o print informativo de check_db_invariants vazou para stdout"
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# main() — exit codes, JSON, ausencia de credenciais, nunca acessa Data Mart
# ---------------------------------------------------------------------------

def test_main_retorna_0_quando_report_ok(monkeypatch, capsys):
    """main() nao aceita `now` (e' o entrypoint real, sempre usa o relogio
    de producao) — para o teste ficar deterministico sem depender do dia em
    que a suite roda, fixa-se o relogio via monkeypatch de hc._now (a
    pequena funcao de relogio isolada), nunca datetime.now() global."""
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn())
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "STATUS GERAL (inclui conhecidos/manuais): OK" in out
    assert "STATUS CRITICO (decide o exit code): OK" in out


def test_main_retorna_1_quando_bug8_diverge(monkeypatch, capsys):
    """Relogio fixado (ver test_main_retorna_0_quando_report_ok) para que a
    reprovacao venha exclusivamente da divergencia do Bug 8 sendo testada
    aqui, nunca de frescor "acidentalmente" tambem estourado pelo relogio
    real no momento em que a suite roda."""
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn(bug8_scalars=[("HAVING COUNT(*) > 1", 5)]))
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 1
    assert "ATENCAO" in capsys.readouterr().out


def test_main_json_e_valido_e_tem_reason(monkeypatch, capsys):
    import json
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn())
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py", "--json"])
    hc.main()
    parsed = json.loads(capsys.readouterr().out)
    assert "ok" in parsed
    assert "sources" in parsed
    assert "data_freshness" in parsed
    assert "bug8_invariants" in parsed
    assert all("reason" in s for s in parsed["sources"])
    assert all("reason" in d for d in parsed["data_freshness"])


def test_main_nunca_imprime_credenciais(monkeypatch, capsys):
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://segredouser:S3nhaSecreta@ep-fake.neon.tech/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn())
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    hc.main()
    out = capsys.readouterr().out
    assert "S3nhaSecreta" not in out
    assert "segredouser" not in out
    assert "ep-fake.neon.tech" in out


# =============================================================================
# Gate B1 — ok_critical separado de ok (Shopee manual = nao-critico)
# =============================================================================

def test_expected_source_critical_default_true():
    """Gate B4: shopee_product_monthly virou critical=False (rastreio de
    execucao de sync_produtos_shopee, bloqueado pelo gap conhecido de
    LOCAL_PG_URL). Gate C1: shopee_daily/shopee-stats_daily/shopee-ads_daily
    tambem viraram critical=False (steps correspondentes saem de
    full_daily e passam a viver em shopee_manual_refresh, manual) -- todas
    as demais fontes continuam critical=True."""
    non_critical_since_gates_b4_c1 = {"shopee_product_monthly", "shopee_daily", "shopee-stats_daily", "shopee-ads_daily"}
    for s in hc.EXPECTED_SOURCES:
        if s.source_name in non_critical_since_gates_b4_c1:
            assert s.critical is False
        else:
            assert s.critical is True, f"{s.source_name} deveria continuar critical=True (default, Gate B1)"


def test_build_report_stale_em_fonte_critica_reprova_ok_e_ok_critical():
    old_date = TODAY - timedelta(days=hc.DAILY_DATA_FRESHNESS_THRESHOLD_DAYS + 5)
    # marketplace_id=2 (ml) stale -- fonte critica
    conn = all_fresh_conn(daily_freshness_rows=[
        {"marketplace_id": 1, "max_date": TODAY},
        {"marketplace_id": 2, "max_date": old_date},
        {"marketplace_id": 3, "max_date": TODAY},
    ])
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is False
    ml_entry = next(d for d in report["data_freshness"] if d["label"] == "fact_marketplace_daily_performance[ml]")
    assert ml_entry["critical"] is True
    assert ml_entry["stale"] is True
    assert "reason" in ml_entry and ml_entry["reason"]


def test_build_report_stale_apenas_em_shopee_reprova_ok_mas_nao_ok_critical():
    """Cenario central do Gate B1: Shopee (ingestao manual) defasado nunca
    faz ok_critical virar False sozinho, mesmo que `ok` (visao completa)
    continue reprovando para visibilidade."""
    old_date = TODAY - timedelta(days=hc.DAILY_DATA_FRESHNESS_THRESHOLD_DAYS + 5)
    conn = all_fresh_conn(daily_freshness_rows=[
        {"marketplace_id": 1, "max_date": TODAY},
        {"marketplace_id": 2, "max_date": TODAY},
        {"marketplace_id": 3, "max_date": old_date},
    ])
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is True
    shopee_entry = next(d for d in report["data_freshness"] if d["label"] == "fact_marketplace_daily_performance[shopee]")
    assert shopee_entry["critical"] is False
    assert shopee_entry["stale"] is True
    assert "reason" in shopee_entry and shopee_entry["reason"]


def test_build_report_ok_critical_true_quando_so_shopee_produtos_manual_defasado():
    """marts.fact_shopee_product_monthly[ref_month] ja nunca vira stale por
    threshold_days=None, mas confirma tambem marcado critical=False."""
    conn = all_fresh_conn(shopee_produtos_max=TODAY - timedelta(days=90))
    report = hc.build_report(conn, now=NOW)
    assert report["ok_critical"] is True
    entry = next(d for d in report["data_freshness"] if "ref_month" in d["label"])
    assert entry["critical"] is False
    assert entry["stale"] is False  # cadencia manual_monthly, nunca stale por si so'


def test_build_report_bug8_divergencia_reprova_ok_critical_mesmo_sem_nenhuma_fonte_stale():
    """Bug 8 (reconciliacao Shopee) nao tem conceito de 'critico/nao-critico'
    — uma divergencia sempre reprova ok_critical tambem, nunca so' `ok`."""
    conn = all_fresh_conn(bug8_scalars=[("HAVING COUNT(*) > 1", 3)])
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is False


def test_build_report_todas_as_entradas_de_data_freshness_tem_campo_critical():
    conn = all_fresh_conn()
    report = hc.build_report(conn, now=NOW)
    assert all("critical" in d for d in report["data_freshness"])
    assert all("critical" in s for s in report["sources"])


def test_main_retorna_1_quando_apenas_fonte_critica_stale(monkeypatch, capsys):
    old_date = TODAY - timedelta(days=hc.DAILY_DATA_FRESHNESS_THRESHOLD_DAYS + 5)
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn(daily_freshness_rows=[
        {"marketplace_id": 1, "max_date": TODAY},
        {"marketplace_id": 2, "max_date": old_date},
        {"marketplace_id": 3, "max_date": TODAY},
    ]))
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "ATRASADO-CRITICO" in out


def test_main_retorna_0_quando_apenas_shopee_stale(monkeypatch, capsys):
    """Regressao central do Gate B1: antes, isso retornava exit 1 todo dia
    so' por causa do gap manual conhecido de Shopee."""
    old_date = TODAY - timedelta(days=hc.DAILY_DATA_FRESHNESS_THRESHOLD_DAYS + 5)
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn(daily_freshness_rows=[
        {"marketplace_id": 1, "max_date": TODAY},
        {"marketplace_id": 2, "max_date": TODAY},
        {"marketplace_id": 3, "max_date": old_date},
    ]))
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "ATRASADO-CONHECIDO" in out
    assert "STATUS CRITICO (decide o exit code): OK" in out


def test_main_json_tem_ok_critical_e_campo_critical_por_entrada(monkeypatch, capsys):
    import json
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn())
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py", "--json"])
    hc.main()
    parsed = json.loads(capsys.readouterr().out)
    assert "ok" in parsed
    assert "ok_critical" in parsed
    assert all("critical" in s for s in parsed["sources"])
    assert all("critical" in d for d in parsed["data_freshness"])
    assert all("reason" in d for d in parsed["data_freshness"])


def test_main_fecha_a_conexao(monkeypatch):
    fake_conn = all_fresh_conn()
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: fake_conn)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    hc.main()
    assert fake_conn.closed is True


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

def test_nunca_referencia_datamart_database_url():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for pattern in (r'os\.environ(?:\.get)?\(\s*["\']DATAMART_DATABASE_URL', r'os\.getenv\(\s*["\']DATAMART_DATABASE_URL'):
        assert not re.search(pattern, source), f"padrao proibido encontrado: {pattern}"


def test_nenhuma_escrita_no_modulo():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for pattern in (r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w", r"\bDELETE\s+FROM\b", r"\bDROP\s+TABLE\b", r"\bCREATE\s+TABLE\b"):
        assert not re.search(pattern, source, re.IGNORECASE), f"forma SQL proibida: {pattern}"


def test_nunca_ativa_task_scheduler():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "schtasks" not in source.lower()
    assert "register-scheduledtask" not in source.lower()


# =============================================================================
# Gate B4 (2026-07-15) — shopee_product_monthly (EXECUCAO) tambem nao-critico
# =============================================================================

def _fresh_run_override(**by_source_name):
    """Baseline 100% fresco (todas as EXPECTED_SOURCES) com sobrescrita
    APENAS das fontes passadas -- ao contrario de repassar last_run/
    last_success diretamente para all_fresh_conn(), que SUBSTITUI o dict
    inteiro (colapsando as demais fontes para 'sem historico')."""
    fresh = {s.source_name: {"started_at": NOW - timedelta(hours=1), "finished_at": NOW - timedelta(hours=1), "status": "success", "error_message": None} for s in hc.EXPECTED_SOURCES}
    fresh.update(by_source_name)
    return fresh


def _fresh_success_override(**by_source_name):
    fresh = {s.source_name: NOW - timedelta(hours=1) for s in hc.EXPECTED_SOURCES}
    fresh.update(by_source_name)
    return fresh


def test_expected_source_shopee_product_monthly_e_nao_critico():
    entry = next(s for s in hc.EXPECTED_SOURCES if s.source_name == "shopee_product_monthly")
    assert entry.critical is False


def test_build_report_stale_apenas_em_shopee_product_monthly_execucao_reprova_ok_mas_nao_ok_critical():
    """Regressao do achado do Gate B3: sync_produtos_shopee fica BLOCKED por
    LOCAL_PG_URL ausente ha dias -- a entrada de EXECUCAO
    shopee_product_monthly fica sem sucesso recente por causa disso, mas
    isso sozinho nao pode reprovar ok_critical (senao full_daily reporta
    FAILED todo dia so' por esse gap ja conhecido, mesmo com ML/TikTok/
    regional saudaveis)."""
    conn = all_fresh_conn(
        last_run=_fresh_run_override(shopee_product_monthly={"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}),
        last_success=_fresh_success_override(shopee_product_monthly=NOW - timedelta(hours=300)),
    )
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is True
    entry = next(s for s in report["sources"] if s["source_name"] == "shopee_product_monthly")
    assert entry["critical"] is False
    assert entry["stale"] is True
    # nenhuma outra fonte deveria ter sido afetada por esse isolamento
    others = [s for s in report["sources"] if s["source_name"] != "shopee_product_monthly"]
    assert all(not s["stale"] for s in others), "isolar shopee_product_monthly nao pode tornar outras fontes stale"


def test_build_report_stale_em_shopee_product_monthly_e_shopee_daily_juntos_ainda_ok_critical():
    """Os dois gaps Shopee conhecidos (execucao shopee_product_monthly +
    dado fact_marketplace_daily_performance[shopee]) podem coexistir sem
    derrubar ok_critical — ambos sao nao-criticos, por motivos distintos mas
    relacionados (ingestao manual Shopee)."""
    old_date = TODAY - timedelta(days=hc.DAILY_DATA_FRESHNESS_THRESHOLD_DAYS + 5)
    conn = all_fresh_conn(
        last_run=_fresh_run_override(shopee_product_monthly={"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}),
        last_success=_fresh_success_override(shopee_product_monthly=NOW - timedelta(hours=300)),
        daily_freshness_rows=[
            {"marketplace_id": 1, "max_date": TODAY},
            {"marketplace_id": 2, "max_date": TODAY},
            {"marketplace_id": 3, "max_date": old_date},
        ],
    )
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is True
    exec_entry = next(s for s in report["sources"] if s["source_name"] == "shopee_product_monthly")
    data_entry = next(d for d in report["data_freshness"] if d["label"] == "fact_marketplace_daily_performance[shopee]")
    assert exec_entry["stale"] is True and exec_entry["critical"] is False
    assert data_entry["stale"] is True and data_entry["critical"] is False


def test_build_report_stale_em_ml_ou_tiktok_execucao_ainda_reprova_ok_critical():
    """Fontes criticas de EXECUCAO continuam derrubando ok_critical — o Gate
    B4 so' afeta shopee_product_monthly, nunca ml_daily/tiktok_daily."""
    conn = all_fresh_conn(
        last_run=_fresh_run_override(ml_daily={"started_at": NOW - timedelta(hours=40), "finished_at": NOW - timedelta(hours=40), "status": "success", "error_message": None}),
        last_success=_fresh_success_override(ml_daily=NOW - timedelta(hours=40)),  # threshold ml_daily = 30h
    )
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is False


def test_build_report_bug8_diverge_reprova_ok_critical_mesmo_com_shopee_produtos_stale():
    """bug8_invariants continua sempre critico — nao existe conceito de
    nao-critico para ele, mesmo depois do Gate B4."""
    conn = all_fresh_conn(
        last_run=_fresh_run_override(shopee_product_monthly={"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}),
        last_success=_fresh_success_override(shopee_product_monthly=NOW - timedelta(hours=300)),
        bug8_scalars=[("HAVING COUNT(*) > 1", 3)],
    )
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is False


def test_main_retorna_0_quando_apenas_shopee_product_monthly_execucao_stale(monkeypatch, capsys):
    """Regressao ponta-a-ponta do achado do Gate B3: `full_daily` nao deve
    mais reportar FAILED so' por esse gap ja conhecido de Shopee (sync_produtos_shopee
    BLOCKED por LOCAL_PG_URL ausente)."""
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn(
        last_run=_fresh_run_override(shopee_product_monthly={"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}),
        last_success=_fresh_success_override(shopee_product_monthly=NOW - timedelta(hours=300)),
    ))
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "ATRASADA-CONHECIDO" in out
    assert "STATUS CRITICO (decide o exit code): OK" in out


def test_main_retorna_1_quando_ml_execucao_stale_mesmo_com_shopee_produtos_ok(monkeypatch, capsys):
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn(
        last_run=_fresh_run_override(ml_daily={"started_at": NOW - timedelta(hours=40), "finished_at": NOW - timedelta(hours=40), "status": "success", "error_message": None}),
        last_success=_fresh_success_override(ml_daily=NOW - timedelta(hours=40)),
    ))
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 1
    assert "ATRASADA-CRITICO" in capsys.readouterr().out


def test_main_retorna_1_quando_bug8_diverge_mesmo_com_shopee_produtos_stale(monkeypatch, capsys):
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn(
        last_run=_fresh_run_override(shopee_product_monthly={"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}),
        last_success=_fresh_success_override(shopee_product_monthly=NOW - timedelta(hours=300)),
        bug8_scalars=[("HAVING COUNT(*) > 1", 7)],
    ))
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 1


# =============================================================================
# Gate C1 (2026-07-16) — shopee_daily/shopee-stats_daily/shopee-ads_daily
# (EXECUCAO) tambem nao-criticas: os steps correspondentes saem de
# full_daily (automatico) e passam a viver em shopee_manual_refresh
# (manual), entao ficar sem execucao recente por mais de 48h e' esperado,
# nao uma quebra de pipeline.
# =============================================================================

_ALL_SHOPEE_EXEC_SOURCES = ("shopee_daily", "shopee-stats_daily", "shopee-ads_daily", "shopee_product_monthly")


@pytest.mark.parametrize("source_name", ["shopee_daily", "shopee-stats_daily", "shopee-ads_daily"])
def test_expected_source_shopee_daily_stats_ads_sao_nao_criticas(source_name):
    entry = next(s for s in hc.EXPECTED_SOURCES if s.source_name == source_name)
    assert entry.critical is False


def test_build_report_stale_em_todas_as_fontes_shopee_execucao_reprova_ok_mas_nao_ok_critical():
    """Cenario central do Gate C1: com daily_shopee_orders/stats/ads
    vivendo em shopee_manual_refresh (nao mais em full_daily), essas 3
    fontes de EXECUCAO (+ shopee_product_monthly, ja nao-critica desde o
    Gate B4) podem ficar todas stale ao mesmo tempo sem derrubar
    ok_critical."""
    stale_overrides = {
        name: {"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}
        for name in _ALL_SHOPEE_EXEC_SOURCES
    }
    success_overrides = {name: NOW - timedelta(hours=300) for name in _ALL_SHOPEE_EXEC_SOURCES}
    conn = all_fresh_conn(
        last_run=_fresh_run_override(**stale_overrides),
        last_success=_fresh_success_override(**success_overrides),
    )
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is True
    for name in _ALL_SHOPEE_EXEC_SOURCES:
        entry = next(s for s in report["sources"] if s["source_name"] == name)
        assert entry["critical"] is False
        assert entry["stale"] is True
    others = [s for s in report["sources"] if s["source_name"] not in _ALL_SHOPEE_EXEC_SOURCES]
    assert all(not s["stale"] for s in others), "isolar as fontes Shopee nao pode tornar ML/TikTok/produtos stale"


def test_main_retorna_0_quando_apenas_fontes_shopee_execucao_stale(monkeypatch, capsys):
    """Regressao ponta-a-ponta do Gate C1: full_daily (que so' roda health_check
    depois de ml/tiktok/regional/produtos ml/tiktok) nao deve reportar
    FAILED so' porque Shopee (agora um pipeline manual separado) esta'
    sem execucao recente ha' dias."""
    stale_overrides = {
        name: {"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}
        for name in _ALL_SHOPEE_EXEC_SOURCES
    }
    success_overrides = {name: NOW - timedelta(hours=300) for name in _ALL_SHOPEE_EXEC_SOURCES}
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn(
        last_run=_fresh_run_override(**stale_overrides),
        last_success=_fresh_success_override(**success_overrides),
    ))
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "ATRASADA-CONHECIDO" in out
    assert "STATUS CRITICO (decide o exit code): OK" in out


def test_main_retorna_1_quando_ml_ou_tiktok_execucao_stale_mesmo_com_todo_shopee_stale(monkeypatch, capsys):
    """ML/TikTok (execucao) continuam criticos e reprovam ok_critical/exit
    code, mesmo com TODAS as fontes Shopee (execucao) tambem stale ao
    mesmo tempo — o Gate C1 nao afeta a criticidade de ML/TikTok."""
    stale_overrides = {
        name: {"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}
        for name in _ALL_SHOPEE_EXEC_SOURCES
    }
    stale_overrides["ml_daily"] = {"started_at": NOW - timedelta(hours=40), "finished_at": NOW - timedelta(hours=40), "status": "success", "error_message": None}
    success_overrides = {name: NOW - timedelta(hours=300) for name in _ALL_SHOPEE_EXEC_SOURCES}
    success_overrides["ml_daily"] = NOW - timedelta(hours=40)  # threshold ml_daily = 30h
    monkeypatch.setattr(hc, "_get_neon_url", lambda: "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(hc, "_neon_readonly", lambda url: all_fresh_conn(
        last_run=_fresh_run_override(**stale_overrides),
        last_success=_fresh_success_override(**success_overrides),
    ))
    monkeypatch.setattr(hc, "_now", lambda: NOW)
    monkeypatch.setattr(hc.sys, "argv", ["health_check.py"])
    exit_code = hc.main()
    assert exit_code == 1
    assert "ATRASADA-CRITICO" in capsys.readouterr().out


def test_build_report_bug8_continua_critico_mesmo_com_todo_shopee_execucao_stale():
    """bug8_invariants continua sempre critico — Gate C1 nao muda isso."""
    stale_overrides = {
        name: {"started_at": NOW - timedelta(hours=300), "finished_at": NOW - timedelta(hours=300), "status": "success", "error_message": None}
        for name in _ALL_SHOPEE_EXEC_SOURCES
    }
    success_overrides = {name: NOW - timedelta(hours=300) for name in _ALL_SHOPEE_EXEC_SOURCES}
    conn = all_fresh_conn(
        last_run=_fresh_run_override(**stale_overrides),
        last_success=_fresh_success_override(**success_overrides),
        bug8_scalars=[("HAVING COUNT(*) > 1", 3)],
    )
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is False


# =============================================================================
# Gate S3 (2026-08-18) — as duas fontes de snapshot no contrato critico
# =============================================================================
# Finding corrigido: os steps `serving_ml_cross_company` e
# `serving_tiktok_channel_efficiency` entraram no `full_daily` como criticos, mas
# o health check nao os monitorava. Nesse estado, um sync podia falhar por dias e
# `ok_critical` continuaria `true`, deixando `/inteligencia` e `/brand-detail`
# defasados em silencio.
#
# EXECUCAO e COBERTURA sao sinais distintos e os dois sao testados aqui: um sync
# pode rodar com sucesso todo dia e ainda servir dado que parou de avancar.

S3_FONTES = ("ml_cross_company", "tiktok_channel_efficiency")
S3_LABEL_ML = "fact_ml_cross_company_summary[synced_at]"
S3_LABEL_TK = "fact_tiktok_channel_efficiency_daily"


def _freshness(report, label):
    return next(d for d in report["data_freshness"] if d["label"] == label)


# --- 1. classificacao -------------------------------------------------------

@pytest.mark.parametrize("fonte", S3_FONTES)
def test_s3_fonte_esta_em_expected_sources_e_e_critica(fonte):
    esperada = next((s for s in hc.EXPECTED_SOURCES if s.source_name == fonte), None)
    assert esperada is not None, f"{fonte} ausente de EXPECTED_SOURCES"
    assert esperada.critical is True
    assert esperada.cadence == "daily"
    assert esperada.exec_threshold_hours == 30, "mesmo contrato das outras diarias criticas"


def test_s3_nomes_sao_os_mesmos_dos_targets_do_sync():
    """Uma unica string por target entre CLI, audit log e health check. Nomes
    divergentes fariam o health check monitorar uma fonte que ninguem grava."""
    import pipelines.sync_serving_snapshots as ss
    nomes_hc = {s.source_name for s in hc.EXPECTED_SOURCES}
    for target in ss.TARGET_ORDER:
        spec = ss.SPECS[target]
        assert spec.audit_source_name == spec.name == target
        assert target in nomes_hc, f"{target} nao esta em EXPECTED_SOURCES"


# --- 2. execucao saudavel ---------------------------------------------------

@pytest.mark.parametrize("fonte", S3_FONTES)
def test_s3_execucao_recente_com_sucesso_mantem_a_fonte_saudavel(fonte):
    conn = all_fresh_conn()
    statuses = hc.fetch_source_statuses(conn, now=NOW)
    s = by_name(statuses, fonte)
    assert s.stale is False
    assert s.execution_stale is False
    assert s.last_run_failed is False
    assert s.critical is True


def test_s3_execucao_e_dado_frescos_mantem_ok_critical_true():
    report = hc.build_report(all_fresh_conn(), now=NOW)
    assert report["ok_critical"] is True
    assert _freshness(report, S3_LABEL_ML)["stale"] is False
    assert _freshness(report, S3_LABEL_TK)["stale"] is False


# --- 3/4/5. execucao ausente, falha e antiga --------------------------------

@pytest.mark.parametrize("fonte", S3_FONTES)
def test_s3_execucao_ausente_reprova_ok_critical(fonte):
    """Fonte sem nenhuma linha no audit log e' sempre stale — nunca
    "ausente e' OK"."""
    conn = all_fresh_conn()
    conn.last_run.pop(fonte)
    conn.last_success.pop(fonte)
    report = hc.build_report(conn, now=NOW)
    assert report["ok_critical"] is False
    s = next(x for x in report["sources"] if x["source_name"] == fonte)
    assert s["stale"] is True
    assert s["critical"] is True
    # A fonte e' identificada pelo campo `source_name` do registro e pela saida
    # humana, que imprime nome + razao. A string `reason` omite o nome de
    # proposito em TODAS as fontes (quem imprime o fornece), entao exigir o nome
    # dentro dela mudaria a mensagem de todas as fontes existentes — fora do
    # escopo deste finding.
    assert s["source_name"] == fonte
    assert "nenhuma execucao registrada" in s["reason"]


@pytest.mark.parametrize("fonte", S3_FONTES)
def test_s3_saida_humana_identifica_a_fonte_afetada(fonte, capsys):
    conn = all_fresh_conn()
    conn.last_run.pop(fonte)
    conn.last_success.pop(fonte)
    hc._print_human(hc.build_report(conn, now=NOW))
    saida = capsys.readouterr().out
    linha = next(l for l in saida.splitlines() if fonte in l)
    assert "ATRASADA-CRITICO" in linha, linha
    assert "nenhuma execucao registrada" in linha


@pytest.mark.parametrize("fonte", S3_FONTES)
def test_s3_ultima_execucao_failed_reprova_ok_critical(fonte):
    conn = all_fresh_conn()
    conn.last_run[fonte] = {
        "started_at": NOW - timedelta(hours=1), "finished_at": NOW - timedelta(hours=1),
        "status": "failed", "error_message": "fonte reprovada, nada foi escrito",
    }
    report = hc.build_report(conn, now=NOW)
    assert report["ok_critical"] is False
    s = next(x for x in report["sources"] if x["source_name"] == fonte)
    assert s["last_run_failed"] is True
    assert s["stale"] is True


@pytest.mark.parametrize("fonte", S3_FONTES)
def test_s3_execucao_antiga_reprova_ok_critical(fonte):
    """31h > 30h de threshold."""
    conn = all_fresh_conn()
    antigo = NOW - timedelta(hours=31)
    conn.last_run[fonte] = {"started_at": antigo, "finished_at": antigo,
                            "status": "success", "error_message": None}
    conn.last_success[fonte] = antigo
    report = hc.build_report(conn, now=NOW)
    assert report["ok_critical"] is False
    s = next(x for x in report["sources"] if x["source_name"] == fonte)
    assert s["execution_stale"] is True
    assert s["hours_since_success"] > 30


@pytest.mark.parametrize("fonte", S3_FONTES)
def test_s3_execucao_dentro_do_limite_de_30h_nao_reprova(fonte):
    conn = all_fresh_conn()
    quase = NOW - timedelta(hours=29)
    conn.last_run[fonte] = {"started_at": quase, "finished_at": quase,
                            "status": "success", "error_message": None}
    conn.last_success[fonte] = quase
    report = hc.build_report(conn, now=NOW)
    assert report["ok_critical"] is True


# --- 6/7. frescor do snapshot ML (sem data de negocio) ----------------------

def test_s3_ml_cross_company_tabela_vazia_fica_stale():
    """`MAX(synced_at)` NULL cobre os dois casos — tabela vazia e coluna nula."""
    conn = all_fresh_conn(ml_cross_company_synced_at=None)
    report = hc.build_report(conn, now=NOW)
    entry = _freshness(report, S3_LABEL_ML)
    assert entry["stale"] is True
    assert entry["critical"] is True
    assert "sem nenhuma linha" in entry["reason"]
    assert report["ok_critical"] is False


def test_s3_ml_cross_company_snapshot_antigo_fica_stale():
    conn = all_fresh_conn(ml_cross_company_synced_at=NOW - timedelta(days=5))
    report = hc.build_report(conn, now=NOW)
    entry = _freshness(report, S3_LABEL_ML)
    assert entry["stale"] is True
    assert entry["days_since"] == 5
    assert report["ok_critical"] is False


def test_s3_ml_cross_company_usa_synced_at_e_nao_fabrica_data_de_negocio():
    """A fonte e' snapshot sem dimensao temporal: inventar uma data de negocio
    mentiria sobre o grao. O sinal e' o campo de auditoria da fotografia."""
    conn = all_fresh_conn()
    hc.fetch_data_freshness(conn, today=TODAY)
    consultas = [s for s in conn.executed if "fact_ml_cross_company_summary" in s]
    assert consultas == ["SELECT MAX(synced_at) AS m FROM marts.fact_ml_cross_company_summary"]


def test_s3_ml_cross_company_dentro_do_limite_nao_fica_stale():
    conn = all_fresh_conn(ml_cross_company_synced_at=NOW - timedelta(days=2))
    report = hc.build_report(conn, now=NOW)
    assert _freshness(report, S3_LABEL_ML)["stale"] is False
    assert report["ok_critical"] is True


# --- 8/9. frescor da fato de canal (data diaria, teto D-1) ------------------

def test_s3_channel_efficiency_tabela_vazia_fica_stale():
    conn = all_fresh_conn(tiktok_channel_efficiency_max=None)
    report = hc.build_report(conn, now=NOW)
    entry = _freshness(report, S3_LABEL_TK)
    assert entry["stale"] is True
    assert entry["critical"] is True
    assert "sem nenhuma linha" in entry["reason"]
    assert report["ok_critical"] is False


def test_s3_channel_efficiency_data_antiga_fica_stale():
    conn = all_fresh_conn(tiktok_channel_efficiency_max=TODAY - timedelta(days=4))
    report = hc.build_report(conn, now=NOW)
    entry = _freshness(report, S3_LABEL_TK)
    assert entry["stale"] is True
    assert entry["days_since"] == 4
    assert report["ok_critical"] is False


def test_s3_channel_efficiency_em_d_menos_1_e_o_estado_normal():
    """O serving nunca publica D0: um dia de defasagem e' o teto correto, nao
    atraso."""
    conn = all_fresh_conn(tiktok_channel_efficiency_max=TODAY - timedelta(days=1))
    report = hc.build_report(conn, now=NOW)
    entry = _freshness(report, S3_LABEL_TK)
    assert entry["stale"] is False
    assert entry["days_since"] == 1
    assert report["ok_critical"] is True


def test_s3_channel_efficiency_data_no_futuro_nunca_e_fresca():
    conn = all_fresh_conn(tiktok_channel_efficiency_max=TODAY + timedelta(days=1))
    report = hc.build_report(conn, now=NOW)
    entry = _freshness(report, S3_LABEL_TK)
    assert entry["stale"] is True
    assert "FUTURO" in entry["reason"]


# --- 10/11. execucao x cobertura sao sinais independentes -------------------

def test_s3_execucao_ok_mas_dado_parado_ainda_reprova():
    """O caso que justifica os DOIS sinais: o sync roda todo dia com sucesso e
    ainda assim serve dado que parou de avancar."""
    conn = all_fresh_conn(tiktok_channel_efficiency_max=TODAY - timedelta(days=10))
    statuses = hc.fetch_source_statuses(conn, now=NOW)
    assert by_name(statuses, "tiktok_channel_efficiency").stale is False
    report = hc.build_report(conn, now=NOW)
    assert _freshness(report, S3_LABEL_TK)["stale"] is True
    assert report["ok_critical"] is False


def test_s3_dado_ok_mas_execucao_parada_ainda_reprova():
    """O inverso: a tabela tem dado recente de uma carga anterior, mas o sync
    parou de executar."""
    conn = all_fresh_conn()
    antigo = NOW - timedelta(hours=40)
    for fonte in S3_FONTES:
        conn.last_run[fonte] = {"started_at": antigo, "finished_at": antigo,
                                "status": "success", "error_message": None}
        conn.last_success[fonte] = antigo
    report = hc.build_report(conn, now=NOW)
    assert _freshness(report, S3_LABEL_ML)["stale"] is False
    assert _freshness(report, S3_LABEL_TK)["stale"] is False
    assert report["ok_critical"] is False


@pytest.mark.parametrize("fonte", S3_FONTES)
def test_s3_falha_nao_e_rebaixada_a_alerta_nao_critico(fonte):
    """Nada de tratamento tipo Shopee: estas duas alimentam telas em producao."""
    conn = all_fresh_conn()
    conn.last_run.pop(fonte)
    conn.last_success.pop(fonte)
    report = hc.build_report(conn, now=NOW)
    s = next(x for x in report["sources"] if x["source_name"] == fonte)
    assert s["critical"] is True
    assert report["ok"] is False
    assert report["ok_critical"] is False, "nao pode degradar para alerta informativo"


# --- 12. Shopee segue nao critica ------------------------------------------

@pytest.mark.parametrize("fonte", ["shopee_daily", "shopee-stats_daily",
                                   "shopee-ads_daily", "shopee_product_monthly"])
def test_s3_fontes_shopee_continuam_nao_criticas(fonte):
    esperada = next(s for s in hc.EXPECTED_SOURCES if s.source_name == fonte)
    assert esperada.critical is False


def test_s3_todo_shopee_stale_nao_reprova_ok_critical():
    """Regra preexistente preservada: o gap manual do Shopee nao derruba o
    critico, e as duas fontes novas nao mudaram isso."""
    conn = all_fresh_conn()
    antigo = NOW - timedelta(hours=200)
    for fonte in ("shopee_daily", "shopee-stats_daily", "shopee-ads_daily",
                  "shopee_product_monthly"):
        conn.last_run[fonte] = {"started_at": antigo, "finished_at": antigo,
                                "status": "failed", "error_message": "gap manual"}
        conn.last_success[fonte] = antigo
    report = hc.build_report(conn, now=NOW)
    assert report["ok"] is False
    assert report["ok_critical"] is True


def test_s3_as_regras_das_fontes_antigas_nao_mudaram():
    esperado = {
        "ml_daily": (30, True), "tiktok_daily": (30, True),
        "shopee_daily": (48, False), "shopee-stats_daily": (48, False),
        "shopee-ads_daily": (48, False), "tiktok_product_daily": (30, True),
        "ml_produto_ranking": (30, True), "shopee_product_monthly": (48, False),
    }
    por_nome = {s.source_name: (s.exec_threshold_hours, s.critical) for s in hc.EXPECTED_SOURCES}
    for nome, alvo in esperado.items():
        assert por_nome[nome] == alvo, nome
    # +2 do Gate S3 (ml_cross_company, tiktok_channel_efficiency), +1 do Gate
    # UE2-C (custo de afiliado do TikTok) e +1 do Gate UE8-I4 (descontos do
    # pedido TikTok). O objetivo do teste continua sendo o mesmo: provar que
    # nenhuma REGRA das fontes antigas mudou ao acrescentar fontes novas.
    assert len(hc.EXPECTED_SOURCES) == len(esperado) + 4


# --- 13. o fake responde explicitamente, sem fallback generico --------------

def test_s3_fake_falha_alto_em_consulta_de_frescor_sem_ramo_explicito():
    """Guarda do proprio harness: se alguem adicionar uma consulta de frescor e
    esquecer o ramo no fake, o teste tem de FALHAR em vez de passar pelo
    `{"n": 0}` generico."""
    conn = all_fresh_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) AS m FROM marts.fact_inexistente_qualquer")
    with pytest.raises(AssertionError, match="sem ramo explicito"):
        cur.fetchone()


def test_s3_as_duas_consultas_novas_sao_executadas_de_fato():
    conn = all_fresh_conn()
    hc.fetch_data_freshness(conn, today=TODAY)
    assert "SELECT MAX(synced_at) AS m FROM marts.fact_ml_cross_company_summary" in conn.executed
    assert "SELECT MAX(date) AS m FROM marts.fact_tiktok_channel_efficiency_daily" in conn.executed


def test_s3_health_check_nao_le_o_data_mart_nem_faz_count_integral():
    conn = all_fresh_conn()
    hc.build_report(conn, now=NOW)
    for sql in conn.executed:
        assert " gold." not in sql and "from gold." not in sql.lower()
        assert " raw." not in sql and "from raw." not in sql.lower()
    novas = [s for s in conn.executed
             if "fact_ml_cross_company_summary" in s or "fact_tiktok_channel_efficiency_daily" in s]
    assert novas, "as consultas novas deveriam ter sido executadas"
    for sql in novas:
        assert "COUNT(*)" not in sql, "frescor nao precisa de COUNT integral"
        assert "JOIN" not in sql.upper()


def test_s3_as_duas_fontes_aparecem_no_relatorio_final():
    report = hc.build_report(all_fresh_conn(), now=NOW)
    nomes = {s["source_name"] for s in report["sources"]}
    labels = {d["label"] for d in report["data_freshness"]}
    for fonte in S3_FONTES:
        assert fonte in nomes
    assert S3_LABEL_ML in labels
    assert S3_LABEL_TK in labels


# ===========================================================================
# Gate UE8-I4 Task 1/2 — descontos TikTok: EXECUCAO e COBERTURA, separadas
# ===========================================================================

import pipelines.sync_tiktok_order_discounts_daily as _sync_desc

NOME_DESC = _sync_desc.CANONICAL_AUDIT_SOURCE
D1 = TODAY - timedelta(days=1)


def _conn_desc(**kw):
    """Conn com TODAS as fontes frescas, exceto o que o teste sobrescrever."""
    conn = FakeConn(**kw)
    for fonte in (s.source_name for s in hc.EXPECTED_SOURCES):
        conn.last_success.setdefault(fonte, NOW - timedelta(hours=2))
        conn.last_run.setdefault(fonte, {
            "started_at": NOW - timedelta(hours=3),
            "finished_at": NOW - timedelta(hours=2),
            "status": "success", "error_message": None,
        })
    return conn


# --- dimensao 1: EXECUCAO ---------------------------------------------------

def test_i4_a_fonte_canonica_esta_em_expected_sources():
    por_nome = {s.source_name: s for s in hc.EXPECTED_SOURCES}
    assert NOME_DESC in por_nome
    e = por_nome[NOME_DESC]
    assert e.exec_threshold_hours == 30
    assert e.critical is True
    assert e.cadence == "daily"


def test_i4_os_nomes_derivados_NAO_entram_em_expected_sources():
    """`_full` e `_backfill` marcam ciclos MENSAL e SEMANAL; cobrar 30h deles
    reprovaria o pipeline todo dia."""
    nomes = {s.source_name for s in hc.EXPECTED_SOURCES}
    assert _sync_desc.FULL_AUDIT_SOURCE not in nomes
    assert _sync_desc.BACKFILL_AUDIT_SOURCE not in nomes


def test_i4_execucao_antiga_reprova_ok_critical():
    conn = _conn_desc()
    conn.last_success[NOME_DESC] = NOW - timedelta(hours=31)
    conn.last_run[NOME_DESC] = {
        "started_at": NOW - timedelta(hours=32),
        "finished_at": NOW - timedelta(hours=31),
        "status": "success", "error_message": None,
    }
    rel = hc.build_report(conn, now=NOW)
    assert rel["ok_critical"] is False
    assert by_name(hc.fetch_source_statuses(conn, now=NOW), NOME_DESC).stale


def test_i4_execucao_dentro_de_30h_nao_reprova():
    conn = _conn_desc()
    conn.last_success[NOME_DESC] = NOW - timedelta(hours=29)
    assert not by_name(hc.fetch_source_statuses(conn, now=NOW), NOME_DESC).stale


def test_i4_sem_execucao_nenhuma_a_dimensao_de_EXECUCAO_reprova():
    """Pre-piloto: a rotina e' critica, entao o verde nao pode vir antes da
    primeira execucao comprovada."""
    conn = _conn_desc()
    conn.last_success[NOME_DESC] = None
    conn.last_run[NOME_DESC] = None
    rel = hc.build_report(conn, now=NOW)
    assert rel["ok_critical"] is False


# --- dimensao 2: COBERTURA OPERACIONAL -------------------------------------

def test_i4_cobertura_ok_quando_job_e_fonte_estao_em_d_menos_1():
    conn = _conn_desc()
    c = hc.fetch_discounts_coverage_status(conn, now=NOW)
    assert c.status == "ok"
    assert c.stale is False
    assert c.source_max_date == D1.isoformat()
    assert c.fact_max_ref_date == D1.isoformat()
    assert c.job_lag_days == 0


def test_i4_execucao_atrasada_e_diferente_de_competencia_ausente():
    """Os dois continuam distintos — o que mudou foi o NOME do segundo, que
    afirmava uma causa ("fonte parada") que a evidencia nao sustenta."""
    parado = _conn_desc(discounts_last_run={
        "finished_at": NOW - timedelta(days=5),
        "source_max_date": TODAY - timedelta(days=6),
    })
    c = hc.fetch_discounts_coverage_status(parado, now=NOW)
    assert c.status == "execucao_atrasada"
    assert c.stale is True
    assert "a ultima execucao publicou ate" in c.reason

    ausente = _conn_desc(discounts_fact_max=TODAY - timedelta(days=5))
    c2 = hc.fetch_discounts_coverage_status(ausente, now=NOW)
    assert c2.status == "competencia_ausente"
    assert c2.stale is True
    assert c2.status != c.status


def test_i4_competencia_ausente_NAO_afirma_causa():
    """Finding B: as fontes atuais nao distinguem ausencia de vendas de lacuna
    de ingestao, entao nem o nome do estado nem o texto podem escolher uma."""
    c = hc.fetch_discounts_coverage_status(
        _conn_desc(discounts_fact_max=TODAY - timedelta(days=5)), now=NOW)
    assert "fonte parou" not in c.reason.lower()
    assert "fonte_atrasada" != c.status
    assert "nao distinguem ausencia de vendas de lacuna de ingestao" in c.reason
    assert hc.DISCOUNTS_COVERAGE_MISSING_NOTE in c.reason


def test_i4_fato_vazia_e_competencia_ausente_nao_ok():
    conn = _conn_desc(discounts_fact_max=None)
    c = hc.fetch_discounts_coverage_status(conn, now=NOW)
    assert c.status == "competencia_ausente"
    assert c.stale is True
    assert hc.DISCOUNTS_COVERAGE_MISSING_NOTE in c.reason


def test_i4_pre_piloto_sem_auditoria_e_unknown_e_NAO_reprova_esta_dimensao():
    conn = _conn_desc(discounts_last_run=None)
    c = hc.fetch_discounts_coverage_status(conn, now=NOW)
    assert c.status == "unknown"
    assert c.stale is False, "unknown nao reprova NESTA dimensao"


def test_i4_pre_piloto_nao_esconde_a_falha_da_dimensao_de_EXECUCAO():
    """`unknown` na cobertura + sem execucao = `ok_critical` False, vindo da
    dimensao de EXECUCAO. Uma rotina critica nao fica verde antes do piloto."""
    conn = _conn_desc(discounts_last_run=None)
    conn.last_success[NOME_DESC] = None
    conn.last_run[NOME_DESC] = None
    rel = hc.build_report(conn, now=NOW)
    assert rel["discounts_coverage"]["status"] == "unknown"
    assert rel["discounts_coverage"]["stale"] is False
    assert rel["ok_critical"] is False


def test_i4_cobertura_atrasada_reprova_ok_critical():
    conn = _conn_desc(discounts_fact_max=TODAY - timedelta(days=9))
    rel = hc.build_report(conn, now=NOW)
    assert rel["discounts_coverage"]["stale"] is True
    assert rel["ok_critical"] is False


def test_i4_um_dia_de_folga_e_o_tolerado():
    assert hc.DISCOUNTS_COVERAGE_MAX_LAG_DAYS == 1
    # exatamente no limite: nao reprova
    conn = _conn_desc(discounts_fact_max=TODAY - timedelta(days=2))
    assert hc.fetch_discounts_coverage_status(conn, now=NOW).stale is False
    # um dia alem: reprova
    conn2 = _conn_desc(discounts_fact_max=TODAY - timedelta(days=3))
    assert hc.fetch_discounts_coverage_status(conn2, now=NOW).stale is True


def test_i4_as_duas_dimensoes_sao_independentes():
    """Execucao OK com cobertura atrasada, e o inverso — nenhuma mascara a
    outra."""
    exec_ok_cob_ruim = _conn_desc(discounts_fact_max=TODAY - timedelta(days=9))
    r1 = hc.build_report(exec_ok_cob_ruim, now=NOW)
    assert not by_name(hc.fetch_source_statuses(exec_ok_cob_ruim, now=NOW),
                       NOME_DESC).stale
    assert r1["discounts_coverage"]["stale"] is True
    assert r1["ok_critical"] is False

    cob_ok_exec_ruim = _conn_desc()
    cob_ok_exec_ruim.last_success[NOME_DESC] = NOW - timedelta(hours=40)
    r2 = hc.build_report(cob_ok_exec_ruim, now=NOW)
    assert r2["discounts_coverage"]["stale"] is False
    assert r2["ok_critical"] is False


# --- o que a cobertura NAO usa ---------------------------------------------

def test_i4_nao_usa_carimbo_tecnico_como_competencia():
    """`source_max_updated_at`/`raw_max_updated_at` dizem QUANDO a linha foi
    tocada, nao ate quando ha venda medida."""
    corpo = MODULE_PATH.read_text(encoding="utf-8")
    trecho = corpo.split("def fetch_discounts_coverage_status")[1] \
        .split("\ndef ")[0]
    assert "source_max_updated_at" not in trecho
    assert "raw_max_updated_at" not in trecho
    assert "observed_grid" not in trecho
    assert "coverage_status" not in trecho


def test_i4_a_cobertura_usa_o_calendario_do_proprio_sync():
    """Dois calendarios divergiriam na fronteira da meia-noite BRT."""
    trecho = MODULE_PATH.read_text(encoding="utf-8") \
        .split("def fetch_discounts_coverage_status")[1].split("\ndef ")[0]
    assert "sync_descontos.last_closed_date(now)" in trecho


def test_i4_a_cobertura_nao_le_o_data_mart_nem_faz_count_integral():
    conn = _conn_desc()
    hc.fetch_discounts_coverage_status(conn, now=NOW)
    sql = " ".join(conn.executed)
    assert "raw." not in sql and "silver." not in sql and "gold." not in sql
    assert "COUNT(*)" not in sql


def test_i4_agregado_tem_alias_explicito():
    """`RealDictCursor` em producao: `MAX(ref_date)` sem `AS` viria na chave
    'max', e o acesso por nome levantaria KeyError em toda execucao real."""
    conn = _conn_desc()
    hc.fetch_discounts_coverage_status(conn, now=NOW)
    assert any("MAX(ref_date) AS fact_max_ref_date" in q for q in conn.executed)


def test_i4_a_cobertura_aparece_no_relatorio_final():
    conn = _conn_desc()
    rel = hc.build_report(conn, now=NOW)
    assert "discounts_coverage" in rel
    d = rel["discounts_coverage"]
    assert d["source_name"] == NOME_DESC
    for campo in ("status", "reason", "source_max_date", "fact_max_ref_date",
                  "last_closed_date", "job_lag_days", "source_lag_days",
                  "stale", "critical"):
        assert campo in d, campo


def test_i4_a_saida_humana_mostra_a_cobertura(capsys):
    conn = _conn_desc(discounts_fact_max=TODAY - timedelta(days=9))
    hc._print_human(hc.build_report(conn, now=NOW))
    saida = capsys.readouterr().out
    assert "Cobertura operacional dos descontos TikTok" in saida
    assert "COMPETENCIA-AUSENTE" in saida


class _ConnQueExplode(FakeConn):
    """Falha SOMENTE na leitura da cobertura — o resto do relatorio segue."""

    def cursor(self):
        cur = super().cursor()
        original = cur.execute

        def explode(sql, params=None):
            if "source_max_date" in sql:
                raise hc.psycopg2.Error(
                    "FATAL: senha S3nha para host prod-db.internal")
            return original(sql, params)

        cur.execute = explode
        return cur


# --- Finding A: erro de banco FALHA FECHADO --------------------------------

def test_i4a_erro_de_banco_vira_status_error_e_stale():
    """Antes devolvia `unknown` com `stale=False`: o Neon indisponivel deixava
    a cobertura VERDE. "Nao consegui verificar" nao e' evidencia de saude."""
    c = hc.fetch_discounts_coverage_status(_ConnQueExplode(), now=NOW)
    assert c.status == "error"
    assert c.stale is True
    assert c.critical is True


def test_i4a_a_mensagem_de_erro_e_fixa_e_sanitizada():
    c = hc.fetch_discounts_coverage_status(_ConnQueExplode(), now=NOW)
    assert c.reason == hc.DISCOUNTS_COVERAGE_ERROR_NOTE
    for vazamento in ("S3nha", "prod-db", "FATAL", "SELECT", "source_max_date"):
        assert vazamento not in c.reason, vazamento


def test_i4a_erro_reprova_ok_critical_VIA_build_report():
    """Integrado, nao so' na funcao isolada: e' `build_report` que decide o
    exit code do step `health_check`."""
    rel = hc.build_report(_ConnQueExplode(), now=NOW)
    assert rel["discounts_coverage"]["status"] == "error"
    assert rel["discounts_coverage"]["stale"] is True
    assert rel["ok_critical"] is False
    assert rel["ok"] is False


def test_i4a_erro_na_cobertura_nao_apaga_o_resto_do_relatorio():
    """Falha fechado NAO significa relatorio truncado: as outras dimensoes
    continuam sendo reportadas."""
    rel = hc.build_report(_ConnQueExplode(), now=NOW)
    assert rel["sources"], "as fontes sumiram do relatorio"
    assert "data_freshness" in rel and "bug8_invariants" in rel
    assert "affiliate_watermark" in rel


def test_i4a_saida_humana_marca_a_leitura_falha(capsys):
    hc._print_human(hc.build_report(_ConnQueExplode(), now=NOW))
    saida = capsys.readouterr().out
    assert "LEITURA-FALHOU" in saida
    assert "S3nha" not in saida and "prod-db" not in saida


def test_i4a_erro_e_DIFERENTE_de_unknown():
    """Ausencia de execucao e' fato conhecido (`unknown`, nao reprova aqui);
    falha de leitura e' cegueira (`error`, reprova)."""
    pre_piloto = hc.fetch_discounts_coverage_status(
        _conn_desc(discounts_last_run=None), now=NOW)
    cego = hc.fetch_discounts_coverage_status(_ConnQueExplode(), now=NOW)
    assert pre_piloto.status == "unknown" and pre_piloto.stale is False
    assert cego.status == "error" and cego.stale is True


def test_i4a_bug_de_programacao_continua_propagando():
    """`except psycopg2.Error` e' estreito de proposito: um `TypeError` nosso
    nao pode virar "erro de fonte"."""
    class ConnBug(FakeConn):
        def cursor(self):
            cur = super().cursor()

            def explode(sql, params=None):
                raise TypeError("bug de programacao")

            cur.execute = explode
            return cur

    with pytest.raises(TypeError):
        hc.fetch_discounts_coverage_status(ConnBug(), now=NOW)


# ===========================================================================
# Gate AVH-4C — obsolescencia do snapshot manual da Avoe
#
# A dimensao existe para que uma captura esquecida apareca como esquecida. Ela
# NUNCA pode reprovar `ok_critical` — e' o que decide o exit code e, por
# consequencia, se o step `health_check` derruba o `full_daily`.
# ===========================================================================

def _avoe(conn=None, **kw):
    c = conn if conn is not None else all_fresh_conn(**kw)
    return hc.fetch_avoe_snapshot_status(c, now=NOW)


# --- estados por idade -----------------------------------------------------

def test_avoe_captura_recente_e_disponivel():
    s = _avoe(avoe_candidatas=[avoe_cand(dias_captura=3)])
    assert s.status == "available_manual_snapshot"
    assert s.stale is False
    assert s.critical is False
    assert s.serving_available is True
    assert s.capture_age_days == 3
    assert s.targets_count == 7 and s.channel_rows_count == 24
    assert s.sync_run_id == 285
    assert s.sync_run_link_method == "audit_time_window"
    assert s.unavailable_reason is None
    assert s.source_name == "avoe_manual_snapshot"
    assert "nao e' SLA da fonte" in s.reason
    assert "depende de acao humana" in s.reason


def test_avoe_quinze_dias_envelhecendo():
    cand = avoe_cand(dias_captura=15)
    s = _avoe(avoe_candidatas=[cand], avoe_runs=[avoe_run(cand=cand)])
    assert s.status == "aging_manual_snapshot"
    assert s.capture_age_days == 15
    # Warning: aparece, mas nao e' critico.
    assert s.stale is True
    assert s.critical is False
    assert s.serving_available is True, "a captura continua valida; so' envelheceu"
    assert f"limiar operacional de {hc.AVOE_AGING_DAYS}" in s.reason


def test_avoe_trinta_e_um_dias_obsoleto():
    cand = avoe_cand(dias_captura=31)
    s = _avoe(avoe_candidatas=[cand], avoe_runs=[avoe_run(cand=cand)])
    assert s.status == "stale_manual_snapshot"
    assert s.capture_age_days == 31
    assert s.stale is True
    assert s.critical is False
    assert f"limiar operacional de {hc.AVOE_STALE_DAYS}" in s.reason


def test_avoe_fronteiras_exatas_dos_limiares():
    """13/14 e 29/30 — o limiar e' inclusivo, e a virada e' onde se espera."""
    for dias, esperado in ((13, "available_manual_snapshot"),
                           (14, "aging_manual_snapshot"),
                           (29, "aging_manual_snapshot"),
                           (30, "stale_manual_snapshot")):
        cand = avoe_cand(dias_captura=dias)
        s = _avoe(avoe_candidatas=[cand], avoe_runs=[avoe_run(cand=cand)])
        assert s.status == esperado, f"{dias} dias -> {s.status}"


def test_avoe_limiares_viajam_na_resposta_como_operacionais():
    s = _avoe()
    assert s.aging_threshold_days == hc.AVOE_AGING_DAYS == 14
    assert s.stale_threshold_days == hc.AVOE_STALE_DAYS == 30
    # E o codigo declara que nao sao SLA.
    fonte = MODULE_PATH.read_text(encoding="utf-8")
    assert "NAO SLA" in fonte or "nao SLA" in fonte
    assert "nunca acordou cadencia" in fonte


def test_avoe_limiar_de_obsoleto_bate_com_o_da_tela():
    """30 dias aqui e 30 dias no selo "captura antiga" da tela. Um so' numero."""
    contrato = (MODULE_PATH.parents[2] / "apps" / "web" / "src" / "lib"
                / "avoe-snapshot-contract.ts")
    texto = contrato.read_text(encoding="utf-8")
    achado = re.search(r"DIAS_PARA_CAPTURA_ANTIGA\s*=\s*(\d+)", texto)
    assert achado, "constante da tela nao encontrada"
    assert int(achado.group(1)) == hc.AVOE_STALE_DAYS


# --- ausencia e invalidez --------------------------------------------------

def test_avoe_sem_nenhuma_captura():
    s = _avoe(avoe_candidatas=[], avoe_runs=[])
    assert s.status == "unavailable"
    assert s.unavailable_reason == "no_snapshot_published"
    assert s.stale is True and s.critical is False
    assert s.serving_available is False
    # Nada inventado: sem contagem, sem idade, sem captura.
    assert s.captured_at is None
    assert s.capture_age_days is None
    assert s.targets_count is None and s.channel_rows_count is None
    assert s.sync_run_id is None
    assert "exportacao manual" in s.reason


def test_avoe_captura_mais_nova_invalida_usa_a_anterior_valida():
    """Publicacao parcial na captura nova nao derruba a anterior que se sustenta."""
    nova = avoe_cand(dias_captura=1, canais=0, snapshot_id="ff" + "0" * 30)
    velha = avoe_cand(dias_captura=5)
    s = _avoe(avoe_candidatas=[nova, velha],
              avoe_runs=[avoe_run(cand=velha)])
    assert s.status == "available_manual_snapshot"
    assert s.capture_age_days == 5, "escolheu a captura VALIDA, nao a mais nova"
    assert s.serving_available is True


def test_avoe_max_captured_at_sozinho_nao_e_saude():
    """Existe MAX(captured_at), mas a captura nao passa na validacao."""
    for quebra in ({"channel_rows_count": 0},          # publicacao parcial
                   {"target_snapshots": 2},            # mistura de imports
                   {"channel_snapshot_id": "zz" + "0" * 30},
                   {"target_grao": 6},                 # grao duplicado
                   {"moedas": 2}):                     # moeda nao unica
        cand = avoe_cand(dias_captura=2, **quebra)
        s = _avoe(avoe_candidatas=[cand], avoe_runs=[avoe_run(cand=cand)])
        assert s.status == "unavailable", quebra
        assert s.serving_available is False
        assert s.unavailable_reason is not None
        assert "nao basta" in s.reason


def test_avoe_metas_e_canais_precisam_ser_da_mesma_captura():
    """Metas numa captura e canais em outra: nenhuma das duas e' servida."""
    so_metas = avoe_cand(dias_captura=2, canais=0, channel_grao=0)
    so_canais = avoe_cand(dias_captura=4, metas=0, target_grao=0)
    s = _avoe(avoe_candidatas=[so_metas, so_canais],
              avoe_runs=[avoe_run(cand=so_metas), avoe_run(sync_run_id=286, cand=so_canais)])
    assert s.status == "unavailable"
    assert s.unavailable_reason == "targets_and_channels_capture_mismatch"


# --- auditoria nao conclusiva ---------------------------------------------

def test_avoe_auditoria_running_nao_e_servida():
    """`finished_at` nulo nao casa com nada, de proposito."""
    cand = avoe_cand(dias_captura=2)
    run = avoe_run(cand=cand, status="running")
    run["finished_at"] = None
    s = _avoe(avoe_candidatas=[cand], avoe_runs=[run])
    assert s.status == "unavailable"
    assert s.unavailable_reason == "audit_run_not_conclusive"
    assert s.stale is True and s.critical is False
    # A ultima execucao `success` nao existe, e isso e' dito sem inventar.
    assert s.last_success_at is None


def test_avoe_auditoria_failed_nao_e_servida():
    cand = avoe_cand(dias_captura=2)
    s = _avoe(avoe_candidatas=[cand],
              avoe_runs=[avoe_run(cand=cand, status="failed")])
    assert s.status == "unavailable"
    assert s.unavailable_reason == "audit_run_not_conclusive"


def test_avoe_auditoria_ambigua_nao_e_servida():
    """Dois runs `success` cobrindo a mesma janela: fail-closed."""
    cand = avoe_cand(dias_captura=2)
    s = _avoe(avoe_candidatas=[cand],
              avoe_runs=[avoe_run(sync_run_id=285, cand=cand),
                         avoe_run(sync_run_id=286, cand=cand)])
    assert s.status == "unavailable"
    assert s.unavailable_reason == "audit_run_not_conclusive"


def test_avoe_ultima_execucao_success_e_medida_a_parte_da_captura():
    """Existe execucao `success`, mas nenhuma captura servivel."""
    cand = avoe_cand(dias_captura=2, target_snapshots=2)   # invalida
    run = avoe_run(cand=cand)
    s = _avoe(avoe_candidatas=[cand], avoe_runs=[run])
    assert s.status == "unavailable"
    assert s.last_success_at == run["finished_at"].isoformat(), \
        "a execucao existiu e e' reportada, mesmo sem captura servivel"


# --- erro de leitura ------------------------------------------------------

def test_avoe_erro_de_leitura_falha_fechado_mas_nao_critico():
    s = _avoe(avoe_erro=True)
    assert s.status == "error"
    assert s.stale is True, "cegueira nao e' evidencia de saude"
    assert s.critical is False, "nem cegueira sobre a Avoe derruba o dia"
    assert s.serving_available is False
    assert s.captured_at is None and s.capture_age_days is None


def test_avoe_mensagem_de_erro_e_sanitizada():
    s = _avoe(avoe_erro=True)
    baixo = s.reason.lower()
    for proibido in ("select", "insert", "postgres://", "psycopg2", "password",
                     "senha", "host=", "traceback", "proxy_avoe",
                     "falha simulada"):
        assert proibido not in baixo, proibido
    assert "nao consegui verificar" in baixo


def test_avoe_defeito_de_codigo_continua_propagando():
    """`psycopg2.Error` e' tratado; bug nosso, nao — esconder seria pior."""
    class ConnQuebrada(FakeConn):
        def cursor(self, cursor_factory=None):
            raise TypeError("defeito de programacao")

    with pytest.raises(TypeError):
        hc.fetch_avoe_snapshot_status(ConnQuebrada(), now=NOW)


# --- integracao no relatorio ---------------------------------------------

def test_avoe_entra_no_relatorio_como_dimensao_propria():
    report = hc.build_report(all_fresh_conn(), now=NOW)
    assert "avoe_snapshot" in report
    a = report["avoe_snapshot"]
    assert a["source_name"] == "avoe_manual_snapshot"
    assert a["status"] == "available_manual_snapshot"
    assert a["critical"] is False
    # E nao contaminou nenhuma das dimensoes existentes.
    nomes_exec = {s["source_name"] for s in report["sources"]}
    assert "avoe_manual_snapshot" not in nomes_exec
    assert not any("avoe" in d["reason"].lower() for d in report["data_freshness"])


def test_avoe_obsoleta_derruba_ok_mas_nunca_ok_critical():
    cand = avoe_cand(dias_captura=45)
    report = hc.build_report(
        all_fresh_conn(avoe_candidatas=[cand], avoe_runs=[avoe_run(cand=cand)]),
        now=NOW)
    assert report["avoe_snapshot"]["status"] == "stale_manual_snapshot"
    assert report["ok"] is False, "aparece como ATENCAO no status geral"
    assert report["ok_critical"] is True, "nunca reprova o status critico"


def test_avoe_indisponivel_nunca_reprova_ok_critical():
    report = hc.build_report(
        all_fresh_conn(avoe_candidatas=[], avoe_runs=[]), now=NOW)
    assert report["avoe_snapshot"]["status"] == "unavailable"
    assert report["ok"] is False
    assert report["ok_critical"] is True


def test_avoe_erro_nunca_reprova_ok_critical():
    report = hc.build_report(all_fresh_conn(avoe_erro=True), now=NOW)
    assert report["avoe_snapshot"]["status"] == "error"
    assert report["ok"] is False
    assert report["ok_critical"] is True


def test_full_daily_nao_falha_exclusivamente_por_avoe():
    """O exit code do processo — que o step do full_daily consome — segue 0."""
    for kw in ({"avoe_candidatas": [], "avoe_runs": []},
               {"avoe_erro": True},
               {"avoe_candidatas": [avoe_cand(dias_captura=99)],
                "avoe_runs": [avoe_run(cand=avoe_cand(dias_captura=99))]}):
        report = hc.build_report(all_fresh_conn(**kw), now=NOW)
        assert report["ok_critical"] is True, kw
        # `main()` decide o exit code por `ok_critical`.
        assert (0 if report["ok_critical"] else 1) == 0, kw


def test_fontes_criticas_existentes_permanecem_intactas():
    """Nenhuma fonte esperada foi adicionada, removida ou tornada nao critica."""
    nomes = [s.source_name for s in hc.EXPECTED_SOURCES]
    assert "avoe_manual_snapshot" not in nomes, \
        "a Avoe e' dimensao propria, nao entra em EXPECTED_SOURCES"
    nao_criticas = {s.source_name for s in hc.EXPECTED_SOURCES if not s.critical}
    assert nao_criticas == {
        "shopee_daily", "shopee-stats_daily", "shopee-ads_daily",
        "shopee_product_monthly",
    }, nao_criticas
    # E as criticas continuam criticas.
    for nome in ("ml_daily", "tiktok_daily", "tiktok_product_daily",
                 "ml_produto_ranking", "ml_cross_company",
                 "tiktok_channel_efficiency"):
        assert by_name(hc.EXPECTED_SOURCES, nome).critical is True


def test_avoe_nao_altera_o_veredito_das_outras_dimensoes():
    """Mesma conexao, com e sem a Avoe saudavel: o resto do relatorio e' igual."""
    saudavel = hc.build_report(all_fresh_conn(), now=NOW)
    quebrada = hc.build_report(all_fresh_conn(avoe_erro=True), now=NOW)
    for chave in ("sources", "data_freshness", "bug8_invariants",
                  "affiliate_watermark", "discounts_coverage"):
        assert saudavel[chave] == quebrada[chave], chave


def test_saida_humana_declara_o_estado_e_a_natureza_da_fonte():
    report = hc.build_report(all_fresh_conn(), now=NOW)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hc._print_human(report)
    texto = buf.getvalue()
    assert "Obsolescencia do snapshot manual da Avoe (nao critico)" in texto
    assert "limiares OPERACIONAIS (nao SLA da fonte)" in texto
    assert "nunca reprova o status critico nem o full_daily" in texto
    assert "avoe_manual_snapshot" in texto


def test_saida_humana_marca_cada_estado_como_conhecido():
    for kw, marca in (
        ({"avoe_candidatas": [avoe_cand(dias_captura=20)],
          "avoe_runs": [avoe_run(cand=avoe_cand(dias_captura=20))]},
         "ENVELHECENDO-CONHECIDO"),
        ({"avoe_candidatas": [avoe_cand(dias_captura=40)],
          "avoe_runs": [avoe_run(cand=avoe_cand(dias_captura=40))]},
         "OBSOLETO-CONHECIDO"),
        ({"avoe_candidatas": [], "avoe_runs": []}, "INDISPONIVEL-CONHECIDO"),
        ({"avoe_erro": True}, "LEITURA-FALHOU-CONHECIDO"),
    ):
        report = hc.build_report(all_fresh_conn(**kw), now=NOW)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            hc._print_human(report)
        assert marca in buf.getvalue(), kw


# --- reuso das regras do serving ------------------------------------------

def test_as_regras_de_validacao_sao_as_do_servico():
    """Nao ha validacao reimplementada aqui: sao as funcoes do endpoint."""
    from app.services import avoe_snapshot_service as svc
    assert hc.avoe_svc is svc
    fonte = MODULE_PATH.read_text(encoding="utf-8")
    assert "avoe_svc._valida_captura" in fonte
    assert "avoe_svc._associa_run" in fonte
    assert "avoe_svc.SQL_CANDIDATAS" in fonte
    assert "avoe_svc.SQL_RUNS_AUDITORIA" in fonte
    # E os nomes de fonte tambem vem de la, nunca digitados de novo.
    assert 'source_name="avoe_manual_snapshot"' not in fonte
    assert "avoe_svc.AUDIT_SOURCE_NAME" in fonte
    assert "avoe_svc.SOURCE" in fonte


def test_a_consulta_executada_e_a_do_servico():
    conn = all_fresh_conn()
    hc.fetch_avoe_snapshot_status(conn, now=NOW)
    from app.services import avoe_snapshot_service as svc
    esperadas = [" ".join(hc._sql_para_psycopg2(s).split())
                 for s in (svc.SQL_CANDIDATAS, svc.SQL_RUNS_AUDITORIA)]
    for sql in esperadas:
        assert sql in conn.executed, sql[:80]


def test_reported_amount_nao_entra_na_avaliacao_de_saude():
    conn = all_fresh_conn()
    hc.fetch_avoe_snapshot_status(conn, now=NOW)
    for sql in conn.executed:
        if "proxy_avoe" in sql or "audit.source_sync_run" in sql:
            assert "reported_amount" not in sql, sql[:100]
    # E a dimensao nao tem campo de valor.
    campos = set(hc.AvoeSnapshotStatus.__dataclass_fields__)
    for proibido in ("reported_amount", "target_amount", "valor", "amount"):
        assert not any(proibido in c for c in campos), proibido


def test_traducao_de_sql_para_psycopg2():
    assert hc._sql_para_psycopg2("SELECT :a, :b_c FROM t") == \
        "SELECT %(a)s, %(b_c)s FROM t"
    # Fail-closed: `%` e `::` tornariam a traducao insegura.
    with pytest.raises(ValueError):
        hc._sql_para_psycopg2("SELECT x FROM t WHERE y LIKE '%a'")
    with pytest.raises(ValueError):
        hc._sql_para_psycopg2("SELECT x::text FROM t WHERE a = :a")


def test_dimensao_nao_escreve_nada():
    conn = all_fresh_conn()
    hc.fetch_avoe_snapshot_status(conn, now=NOW)
    for sql in conn.executed:
        baixo = sql.lower()
        for proibido in ("insert into", "update ", "delete from", "truncate",
                         "create ", "alter ", "drop "):
            assert proibido not in baixo, proibido


def test_dimensao_nao_expoe_infraestrutura_nem_segredo():
    for kw in ({}, {"avoe_candidatas": [], "avoe_runs": []}, {"avoe_erro": True}):
        s = _avoe(**kw)
        texto = " ".join(str(v) for v in s.__dict__.values()).lower()
        for proibido in ("postgres://", "postgresql://", "password", "senha",
                         "apikey", "host=", "user=", "dbname=", "onrender",
                         "neon.tech", "amazonaws"):
            assert proibido not in texto, f"{kw}: {proibido}"
