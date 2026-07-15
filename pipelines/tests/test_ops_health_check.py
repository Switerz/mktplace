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
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pipelines.ops.health_check as hc

MODULE_PATH = Path(hc.__file__)
NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def by_name(statuses, name):
    return next(s for s in statuses if s.source_name == name)


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
        if "ref_month) AS m FROM marts." in sql:
            return {"m": self.conn.shopee_produtos_max}
        for marker, value in self.conn.bug8_scalars:
            if marker in sql:
                return {"n": value}
        return {"n": 0}

    def fetchall(self):
        sql = self._last_sql
        if "marketplace_id, MAX(date)" in sql:
            return self.conn.daily_freshness_rows
        return []

    def close(self):
        pass


_UNSET = object()


class FakeConn:
    def __init__(self, last_run=None, last_success=None, daily_freshness_rows=None,
                 tiktok_produtos_max=_UNSET, ml_produtos_max=_UNSET, shopee_produtos_max=_UNSET,
                 bug8_scalars=None):
        self.executed = []
        self.closed = False
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
    for s in hc.EXPECTED_SOURCES:
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
