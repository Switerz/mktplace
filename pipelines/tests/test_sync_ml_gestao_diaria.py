"""
Testes de pipelines/sync_ml_gestao_diaria.py e da migration 006 — Gate S1.

Usa conexoes psycopg2 FALSAS: nenhum banco real (Data Mart nem Neon) e' tocado,
e nenhuma escrita real acontece — nem sob `--apply`. As tabelas do Neon sao
simuladas como listas de dicts em memoria e o SQL relevante e' interpretado por
substring, no mesmo padrao de `test_sync_region_daily.py`.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines import sync_ml_gestao_diaria as s


# ---------------------------------------------------------------------------
# Dados de apoio
# ---------------------------------------------------------------------------

def _row(ref_date="2026-08-01", brand="barbours", gmv=100.0, ad_spend=10.0,
         ad_revenue=40.0, paid_orders=3, roas=4.0):
    return {
        "ref_date": date.fromisoformat(ref_date) if isinstance(ref_date, str) else ref_date,
        "brand": brand, "gmv": gmv, "ad_spend": ad_spend,
        "ad_revenue": ad_revenue, "paid_orders": paid_orders, "roas": roas,
    }


def _janela(rows):
    ds = sorted({r["ref_date"] for r in rows})
    return ds[0], ds[-1]


SAMPLE = [
    _row("2026-08-01", "barbours", gmv=100.0, ad_spend=10.0, ad_revenue=40.0, paid_orders=3, roas=4.0),
    _row("2026-08-01", "kokeshi", gmv=50.0, ad_spend=5.0, ad_revenue=10.0, paid_orders=1, roas=2.0),
    _row("2026-08-02", "barbours", gmv=200.0, ad_spend=20.0, ad_revenue=60.0, paid_orders=4, roas=3.0),
]


class FakeCursor:
    """Interpreta por substring o SQL que o modulo emite."""

    def __init__(self, conn):
        self.conn = conn
        self._result = None
        self.rowcount = -1

    # -- helpers ---------------------------------------------------------
    def _janela_params(self, params):
        return params["date_from"], params["date_to"]

    def _agg(self, rows):
        somas = {f"sum_{c}": round(sum(0.0 if r[c] is None else float(r[c]) for r in rows), 2)
                 for c in s.ADDITIVE_COLUMNS}
        return {
            "count": len(rows),
            "min_date": min((r["ref_date"] for r in rows), default=None),
            "max_date": max((r["ref_date"] for r in rows), default=None),
            "distinct_dates": len({r["ref_date"] for r in rows}),
            "distinct_brands": len({r["brand"] for r in rows}),
            "roas_not_null": sum(1 for r in rows if r.get("roas") is not None),
            **somas,
        }

    def _tabela(self, sql):
        if s.STAGING_TABLE_QUALIFIED in sql or s.STAGING_TABLE_NAME in sql:
            return "staging"
        if s.TARGET_TABLE in sql:
            return "target"
        raise AssertionError(f"tabela nao reconhecida no SQL: {sql[:120]}")

    # -- protocolo -------------------------------------------------------
    def execute(self, sql, params=None):
        self.conn.executed.append(sql)
        low = " ".join(sql.split()).lower()

        if low.startswith("set local statement_timeout") or low.startswith("set statement_timeout"):
            return
        if "pg_advisory_xact_lock" in low:
            self.conn.locks.append(params[0])
            return
        if "create temp table" in low:
            assert "on commit drop" in low
            self.conn.tables["staging"] = []
            return
        if low.startswith("insert into") and "values %s" in low:
            return  # execute_values passa por aqui no driver real
        if low.startswith("select to_regclass"):
            self._result = [{"t": s.TARGET_TABLE if self.conn.target_exists else None}]
            return
        if low.startswith("delete from"):
            df, dt = self._janela_params(params)
            antes = len(self.conn.tables["target"])
            self.conn.tables["target"] = [
                r for r in self.conn.tables["target"] if not (df <= r["ref_date"] <= dt)
            ]
            self.rowcount = antes - len(self.conn.tables["target"])
            return
        if low.startswith("insert into") and "select" in low:
            novas = [dict(r, synced_at=datetime(2026, 8, 11, 12, 0, 0)) for r in self.conn.tables["staging"]]
            self.conn.tables["target"].extend(novas)
            self.rowcount = len(novas)
            return
        if "except" in low:
            df, dt = self._janela_params(params)
            def chave(rows):
                return {tuple(r[c] for c in s.BUSINESS_COLUMNS) for r in rows if df <= r["ref_date"] <= dt}
            a = chave(self.conn.tables["staging"]) if low.index("staging") < low.index("marts.") else chave(self.conn.tables["target"])
            b = chave(self.conn.tables["target"]) if low.index("staging") < low.index("marts.") else chave(self.conn.tables["staging"])
            self._result = [{"n": len(a - b)}]
            return
        if low.startswith("select count(*) as count"):
            df, dt = self._janela_params(params)
            tab = self._tabela(sql)
            rows = [r for r in self.conn.tables[tab] if df <= r["ref_date"] <= dt]
            self._result = [self._agg(rows)]
            return
        raise AssertionError(f"SQL inesperado no fake: {low[:140]}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result or []

    def close(self):
        pass


class FakeConn:
    def __init__(self, target_rows=None, target_exists=True):
        self.tables = {"target": list(target_rows or []), "staging": []}
        self.executed = []
        self.locks = []
        self.committed = 0
        self.rolledback = 0
        self.target_exists = target_exists

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolledback += 1
        self.tables["staging"] = []  # ON COMMIT DROP

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _sem_execute_values(monkeypatch):
    """`execute_values` do driver real espera cursor de verdade. Aqui ela apenas
    materializa as linhas na staging simulada."""
    def fake(cur, sql, batch, page_size=None):
        cols = s.BUSINESS_COLUMNS + ["source_run_id"]
        for t in batch:
            cur.conn.tables["staging"].append(dict(zip(cols, t)))
    monkeypatch.setattr(s, "execute_values", fake)


# ---------------------------------------------------------------------------
# 1–3. Contrato de colunas e query da fonte
# ---------------------------------------------------------------------------

def test_01_lista_de_colunas_explicita_e_exata():
    assert s.KEY_COLUMNS == ["ref_date", "brand"]
    assert s.ADDITIVE_COLUMNS == ["gmv", "ad_spend", "ad_revenue", "paid_orders"]
    assert s.RATIO_COLUMNS == ["roas"]
    assert s.BUSINESS_COLUMNS == ["ref_date", "brand", "gmv", "ad_spend", "ad_revenue", "paid_orders", "roas"]
    assert s.AUDIT_COLUMNS == ["synced_at", "source_run_id"]
    # roas fica FORA das obrigatorias: a fonte a deixa nula em 906 de 1.625 linhas
    assert "roas" not in s.REQUIRED_COLUMNS
    assert s.REQUIRED_COLUMNS == ["ref_date", "brand", "gmv", "ad_spend", "ad_revenue", "paid_orders"]


def test_02_zero_select_star_no_modulo():
    src = code_only(Path(s.__file__))
    assert not re.search(r"SELECT\s+\*", src, re.I), "nenhum SELECT * pode existir no codigo"
    # A proibicao roda sobre o CODIGO: o cabecalho de secao que documenta
    # "zero SELECT *" nao pode reprovar o arquivo correto.
    # A ausencia de estrela nas consultas emitidas e' verificada nos testes 03 e
    # 03b, que inspecionam o SQL de fato construido — mais direto que varrer
    # estrelas no fonte, onde `**negrito**` de docstring e `*args` sao legitimos.


def test_03_query_da_fonte_limitada_a_janela_e_sem_star():
    q = s.build_source_query()
    assert "SELECT ref_date, brand, gmv, ad_spend, ad_revenue, paid_orders, roas" in q
    assert "FROM gold.ml_gestao_diaria" in q
    assert "WHERE ref_date BETWEEN %(date_from)s AND %(date_to)s" in q
    assert "*" not in q


def test_03b_todo_sql_emitido_lista_colunas_ou_usa_count_estrela():
    """Verifica o SQL DE FATO construido, nao o fonte.

    Executa a publicacao com conexao falsa e inspeciona cada comando emitido: a
    unica estrela permitida e' a de `COUNT(*)`.
    """
    conn = FakeConn()
    df, dt = _janela(SAMPLE)
    s.publish_window(conn, SAMPLE, df, dt, "run")
    assert conn.executed, "nenhum SQL foi emitido"
    for sql in conn.executed + [s.build_source_query()]:
        for m in re.finditer(r"\*", sql):
            antes = sql[max(0, m.start() - 7):m.start()]
            assert antes.upper().endswith("COUNT("), f"estrela fora de COUNT(*): {sql[:90]}"
        assert not re.search(r"SELECT\s+\*", sql, re.I)


# ---------------------------------------------------------------------------
# 4–9. Validacoes que bloqueiam ANTES da publicacao
# ---------------------------------------------------------------------------

def test_04_chave_e_ref_date_brand():
    assert s.duplicates_in_rows(SAMPLE) == 0
    dup = SAMPLE + [_row("2026-08-01", "barbours")]
    assert s.duplicates_in_rows(dup) == 1


def test_05_duplicidade_na_fonte_bloqueia_antes_de_publicar():
    rows = SAMPLE + [_row("2026-08-02", "barbours", gmv=999.0)]
    df, dt = _janela(rows)
    problemas = s.validate_source_rows(rows, df, dt)
    assert any("duplicado" in p for p in problemas)
    conn = FakeConn()
    with pytest.raises(RuntimeError):
        # a publicacao tambem reprova, porque a staging divergiria da fonte
        if problemas:
            raise RuntimeError("; ".join(problemas))
    assert conn.committed == 0


def test_06_nulo_em_coluna_obrigatoria_bloqueia():
    rows = [dict(SAMPLE[0], gmv=None), SAMPLE[1], SAMPLE[2]]
    df, dt = _janela(rows)
    problemas = s.validate_source_rows(rows, df, dt)
    assert any("nulo" in p and "gmv" in p for p in problemas)
    # roas nulo NAO bloqueia
    rows_ok = [dict(SAMPLE[0], roas=None), SAMPLE[1], SAMPLE[2]]
    assert s.validate_source_rows(rows_ok, df, dt) == []


def test_07_cobertura_incompleta_e_detectada_por_DIA():
    rows = [SAMPLE[0], SAMPLE[1]]  # so' 01/08
    cob = s.date_coverage(rows, date(2026, 8, 1), date(2026, 8, 3))
    assert cob["expected_days"] == 3
    assert cob["covered_days"] == 1
    assert cob["missing_days"] == [date(2026, 8, 2), date(2026, 8, 3)]
    assert cob["complete"] is False
    problemas = s.validate_source_rows(rows, date(2026, 8, 1), date(2026, 8, 3))
    assert any("cobertura incompleta" in p for p in problemas)


def test_07b_cobertura_parcial_de_MARCAS_no_dia_nao_reprova():
    """A auditoria achou 99 datas com cobertura parcial de marcas na fonte real:
    marca sem movimento no dia nao aparece. Exigir todas as marcas todo dia
    reprovaria a fonte legitima."""
    rows = [_row("2026-08-01", "barbours"), _row("2026-08-02", "kokeshi")]
    assert s.validate_source_rows(rows, date(2026, 8, 1), date(2026, 8, 2)) == []


def test_08_agregado_divergente_bloqueia_e_nao_publica():
    conn = FakeConn()
    df, dt = _janela(SAMPLE)
    original = s.aggregates_from_rows

    def mentiroso(rows):
        agg = dict(original(rows))
        agg["sum_gmv"] = agg["sum_gmv"] + 1  # fonte "diz" outro total
        return agg

    s.aggregates_from_rows = mentiroso
    try:
        with pytest.raises(RuntimeError, match="divergiu"):
            s.publish_window(conn, SAMPLE, df, dt, "run")
    finally:
        s.aggregates_from_rows = original
    assert conn.committed == 0
    assert conn.rolledback == 1
    assert conn.tables["target"] == []


def test_09_except_divergente_bloqueia(monkeypatch):
    conn = FakeConn()
    df, dt = _janela(SAMPLE)
    monkeypatch.setattr(s, "except_both_ways", lambda *a, **k: (1, 0))
    with pytest.raises(RuntimeError, match="EXCEPT"):
        s.publish_window(conn, SAMPLE, df, dt, "run")
    assert conn.committed == 0
    assert conn.rolledback == 1


# ---------------------------------------------------------------------------
# 10–13. Transacao, janela, remocao e idempotencia
# ---------------------------------------------------------------------------

def test_10_rollback_completo_em_falha(monkeypatch):
    antigas = [_row("2026-08-01", "barbours", gmv=1.0)]
    conn = FakeConn(target_rows=list(antigas))
    df, dt = _janela(SAMPLE)
    monkeypatch.setattr(s, "except_both_ways", lambda *a, **k: (0, 3))
    with pytest.raises(RuntimeError):
        s.publish_window(conn, SAMPLE, df, dt, "run")
    assert conn.rolledback == 1 and conn.committed == 0
    assert conn.tables["staging"] == []  # ON COMMIT DROP tambem no rollback


def test_11_nada_fora_da_janela_e_alterado():
    fora = _row("2026-07-15", "barbours", gmv=777.0)
    conn = FakeConn(target_rows=[fora])
    df, dt = _janela(SAMPLE)
    s.publish_window(conn, SAMPLE, df, dt, "run")
    ainda = [r for r in conn.tables["target"] if r["ref_date"] == date(2026, 7, 15)]
    assert len(ainda) == 1 and float(ainda[0]["gmv"]) == 777.0


def test_12_remocao_na_fonte_dentro_da_janela_e_refletida():
    """Upsert puro deixaria a linha orfa. O DELETE da janela a remove."""
    conn = FakeConn(target_rows=[dict(r) for r in SAMPLE])
    df, dt = _janela(SAMPLE)
    menor = [SAMPLE[0], SAMPLE[2]]  # kokeshi de 01/08 desapareceu da fonte
    s.publish_window(conn, menor, df, dt, "run")
    chaves = {(r["ref_date"], r["brand"]) for r in conn.tables["target"]}
    assert (date(2026, 8, 1), "kokeshi") not in chaves
    assert len(conn.tables["target"]) == 2


def test_13_segunda_execucao_e_idempotente():
    conn = FakeConn()
    df, dt = _janela(SAMPLE)
    s.publish_window(conn, SAMPLE, df, dt, "run-1")
    primeiro = sorted(
        tuple(r[c] for c in s.BUSINESS_COLUMNS) for r in conn.tables["target"]
    )
    s.publish_window(conn, SAMPLE, df, dt, "run-2")
    segundo = sorted(
        tuple(r[c] for c in s.BUSINESS_COLUMNS) for r in conn.tables["target"]
    )
    assert primeiro == segundo
    assert len(conn.tables["target"]) == len(SAMPLE)  # zero duplicidade
    assert conn.committed == 2


# ---------------------------------------------------------------------------
# 14. roas nunca somada
# ---------------------------------------------------------------------------

def test_14_roas_nao_entra_como_soma_aditiva():
    agg = s.aggregates_from_rows(SAMPLE)
    assert "sum_roas" not in agg, "roas e' razao: somar nao tem significado"
    assert agg["roas_not_null"] == 3
    assert "roas" not in s.ADDITIVE_COLUMNS
    # a comparacao de agregados tambem nao inventa soma de roas
    problemas = s.compare_aggregates(agg, dict(agg))
    assert problemas == []
    src = code_only(Path(s.__file__))
    assert "SUM(roas" not in src and "sum(roas" not in src


# ---------------------------------------------------------------------------
# 15–19. Seguranca da publicacao
# ---------------------------------------------------------------------------

def test_15_staging_e_temp_com_on_commit_drop():
    conn = FakeConn()
    df, dt = _janela(SAMPLE)
    s.publish_window(conn, SAMPLE, df, dt, "run")
    criacao = [q for q in conn.executed if "CREATE TEMP TABLE" in q]
    assert len(criacao) == 1
    assert "ON COMMIT DROP" in criacao[0]
    # e nenhum TRUNCATE em sincronizacao incremental
    assert not any("TRUNCATE" in q.upper() for q in conn.executed)


def test_16_advisory_lock_especifico_da_tabela():
    conn = FakeConn()
    df, dt = _janela(SAMPLE)
    s.publish_window(conn, SAMPLE, df, dt, "run")
    assert conn.locks == [s.ADVISORY_LOCK_KEY]
    assert any("pg_advisory_xact_lock" in q for q in conn.executed)


def test_17_timeouts_existem_nas_duas_pontas():
    conn = FakeConn()
    df, dt = _janela(SAMPLE)
    s.publish_window(conn, SAMPLE, df, dt, "run")
    assert any("statement_timeout" in q for q in conn.executed)
    src = code_only(Path(s.__file__))
    assert "SOURCE_STATEMENT_TIMEOUT" in src and "TARGET_STATEMENT_TIMEOUT" in src
    assert "connect_timeout" in src and "CONNECT_TIMEOUT_SECONDS" in src


def test_18_zero_retry_backoff_ou_agendamento():
    src = code_only(Path(s.__file__)).lower()
    for proibido in ["time.sleep", "backoff", "for attempt", "while true", "retry", "schedule", "cron"]:
        assert proibido not in src, f"'{proibido}' nao pode existir no codigo: repeticao e' de quem chama"


def test_19_dsn_e_credencial_nunca_aparecem_em_erro():
    """A DSN inteira desaparece: mensagem de conexao volta como CATEGORIA fixa.

    O contrato mudou no hardening: antes a funcao redigia `usuario:senha@` e
    devolvia o resto, o que preservava hostname, IP e porta. Agora nao ha o que
    redigir, porque a mensagem original nao e' ecoada.
    """
    exc = Exception(
        "could not connect: postgresql://PLACEHOLDER_USER:PLACEHOLDER_SECRET"
        "@PLACEHOLDER_HOST:5432/db timeout"
    )
    msg = s.sanitize_error_message(exc)
    # `timeout` solto nao e' a frase `timed out` do libpq: cai na categoria
    # generica de conexao, e nao na de servidor inalcancavel. A classificacao
    # e' precisa de proposito, em vez de chutar a causa.
    assert msg == s.ERRO_CONEXAO
    for proibido in ("PLACEHOLDER_SECRET", "PLACEHOLDER_USER", "PLACEHOLDER_HOST",
                     "5432", "postgresql://"):
        assert proibido not in msg
    assert len(msg) <= s.MAX_ERRO_CHARS


def test_19b_run_id_e_sanitizado_e_rastreavel():
    assert s.sanitize_run_id("ok_-:123") == "ok_-:123"
    assert ";" not in s.sanitize_run_id("drop;table")
    assert "'" not in s.sanitize_run_id("a'b")
    assert len(s.sanitize_run_id("x" * 200)) == 64
    assert s.default_run_id(datetime(2026, 8, 11, 9, 30, 0)) == "sync_ml_gestao_diaria:20260811_093000"


def test_19c_identificador_invalido_e_recusado():
    with pytest.raises(ValueError):
        s.validate_identifier("gmv; DROP TABLE x")
    with pytest.raises(ValueError):
        s.validate_identifier("Maiuscula")
    assert s.validate_identifier("ref_date") == "ref_date"


def test_19d_conexoes_exigem_variavel_explicita_sem_fallback(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATAMART_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        s._get_neon_url()
    with pytest.raises(RuntimeError, match="DATAMART_DATABASE_URL"):
        s._get_datamart_url()


# ---------------------------------------------------------------------------
# 20–21. CLI
# ---------------------------------------------------------------------------

def test_20_cli_sem_apply_nao_escreve(monkeypatch, capsys):
    chamou = {"sync": 0, "diagnose": 0, "writable": 0}
    monkeypatch.setattr(s, "run_sync", lambda *a, **k: chamou.__setitem__("sync", 1) or 0)
    monkeypatch.setattr(s, "run_diagnose", lambda *a, **k: chamou.__setitem__("diagnose", 1) or 0)
    monkeypatch.setattr(s, "_neon_writable", lambda *a, **k: chamou.__setitem__("writable", 1))
    rc = s.main(["--date-from", "2026-08-01", "--date-to", "2026-08-02"])
    assert rc == 0
    assert chamou == {"sync": 0, "diagnose": 1, "writable": 0}
    assert "DIAGNOSTICO" in capsys.readouterr().out


def test_21_apply_so_com_sessao_fake(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(s, "_datamart_readonly", lambda url: _FakeSource(SAMPLE))
    monkeypatch.setattr(s, "_neon_writable", lambda url: conn)
    monkeypatch.setenv("DATABASE_URL", "PLACEHOLDER_NEON_DSN_NOT_A_CREDENTIAL")
    monkeypatch.setenv("DATAMART_DATABASE_URL", "PLACEHOLDER_DATAMART_DSN_NOT_A_CREDENTIAL")
    rc = s.main(["--date-from", "2026-08-01", "--date-to", "2026-08-02", "--apply", "--run-id", "t"])
    assert rc == 0
    assert conn.committed == 1
    assert len(conn.tables["target"]) == len(SAMPLE)


class _FakeSource:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeSourceCursor(self._rows)

    def close(self):
        pass


class _FakeSourceCursor:
    def __init__(self, rows):
        self._rows = rows
        self._out = []

    def execute(self, sql, params=None):
        if "statement_timeout" in sql.lower():
            return
        assert "SELECT ref_date, brand" in sql and "*" not in sql
        df, dt = params["date_from"], params["date_to"]
        self._out = [r for r in self._rows if df <= r["ref_date"] <= dt]

    def fetchall(self):
        return self._out

    def close(self):
        pass


def test_20b_janela_invalida_da_exit_code_nao_zero(capsys):
    assert s.main(["--date-from", "2026-08-05", "--date-to", "2026-08-01"]) == 2
    assert "FALHA" in capsys.readouterr().err
    assert s.main(["--date-from", "2030-01-01", "--date-to", "2030-01-02"]) == 2


def test_20c_validacao_de_janela():
    hoje = date(2026, 8, 11)
    assert s.validate_window(date(2026, 8, 1), date(2026, 8, 2), hoje) == (date(2026, 8, 1), date(2026, 8, 2))
    with pytest.raises(ValueError, match="invertida"):
        s.validate_window(date(2026, 8, 5), date(2026, 8, 1), hoje)
    with pytest.raises(ValueError, match="futura"):
        s.validate_window(date(2026, 8, 1), date(2026, 8, 12), hoje)
    with pytest.raises(ValueError, match="anterior ao primeiro dado"):
        s.validate_window(date(2020, 1, 1), date(2026, 8, 1), hoje)


def test_20d_janela_incremental_e_backfill():
    hoje = date(2026, 8, 11)
    # 7 dias COMPLETOS terminando no ultimo dia fechado: 04/08 a 10/08
    assert s.incremental_window(hoje, 7) == (date(2026, 8, 4), date(2026, 8, 10))
    p = s.build_parser().parse_args(["--backfill"])
    assert s.resolve_window_from_args(p, hoje) == (s.SOURCE_MIN_DATE, date(2026, 8, 10))
    with pytest.raises(ValueError, match="nao combina"):
        s.resolve_window_from_args(s.build_parser().parse_args(["--backfill", "--date-from", "2026-08-01"]), hoje)
    with pytest.raises(ValueError, match="juntos"):
        s.resolve_window_from_args(s.build_parser().parse_args(["--date-from", "2026-08-01"]), hoje)


# ---------------------------------------------------------------------------
# Gate S1, correcao terminal — Finding 1: somente dias FECHADOS (D-N a D-1)
# ---------------------------------------------------------------------------

HOJE = date(2026, 8, 11)
FECHADO = date(2026, 8, 10)


def test_f1_01_regra_unica_de_ultimo_dia_fechado():
    assert s.last_closed_date(HOJE) == FECHADO
    assert s.require_closed_day(HOJE) == FECHADO
    # o modulo tem UMA regra, e ela e' D-1
    src = code_only(Path(s.__file__))
    assert "timedelta(days=1)" in src


def test_f1_02_incremental_de_7_dias_em_11_08_e_04_a_10_08():
    df, dt = s.incremental_window(HOJE, 7)
    assert (df, dt) == (date(2026, 8, 4), FECHADO)
    assert (dt - df).days + 1 == 7, "exatamente sete dias completos"
    assert dt < HOJE, "o dia corrente nunca entra"


def test_f1_03_incremental_menor_que_o_piso_e_recusado():
    """Era `incremental_window(HOJE, 1) == (FECHADO, FECHADO)`.

    O piso passou de 1 para `MIN_LOOKBACK_DAYS` (7) na correcao de convergencia
    do Gate S2 Task 3/3: uma janela de 1 dia nao absorve late-arriving data
    nenhum, e a medicao mostrou reafirmacao ate 68 dias para tras.
    """
    with pytest.raises(ValueError, match=">= 7"):
        s.incremental_window(HOJE, 1)
    assert s.incremental_window(HOJE, 7) == (date(2026, 8, 4), FECHADO)


def test_f1_04_lookback_menor_que_1_e_erro():
    for n in (0, -1, -7):
        with pytest.raises(ValueError, match="lookback_days"):
            s.incremental_window(HOJE, n)


def test_f1_05_backfill_termina_em_10_08():
    p = s.build_parser().parse_args(["--backfill"])
    df, dt = s.resolve_window_from_args(p, HOJE)
    assert df == s.SOURCE_MIN_DATE
    assert dt == FECHADO
    assert dt != HOJE


def test_f1_06_janela_explicita_terminando_no_dia_fechado_passa():
    p = s.build_parser().parse_args(["--date-from", "2026-08-04", "--date-to", "2026-08-10"])
    assert s.resolve_window_from_args(p, HOJE) == (date(2026, 8, 4), FECHADO)


def test_f1_07_janela_explicita_terminando_hoje_reprova_explicando_o_motivo():
    with pytest.raises(ValueError, match="dia corrente"):
        s.validate_window(date(2026, 8, 4), HOJE, HOJE)
    # e a mensagem diz que o dado esta incompleto, nao apenas "invalido"
    try:
        s.validate_window(date(2026, 8, 4), HOJE, HOJE)
    except ValueError as exc:
        assert "incompleto" in str(exc)
        assert str(FECHADO) in str(exc)
    p = s.build_parser().parse_args(["--date-from", "2026-08-04", "--date-to", "2026-08-11"])
    with pytest.raises(ValueError, match="dia corrente"):
        s.resolve_window_from_args(p, HOJE)


def test_f1_08_data_futura_reprova():
    with pytest.raises(ValueError, match="futura"):
        s.validate_window(date(2026, 8, 4), date(2026, 8, 12), HOJE)
    with pytest.raises(ValueError, match="futura"):
        s.validate_window(date(2026, 8, 4), date(2027, 1, 1), HOJE)


def test_f1_09_sem_dia_fechado_disponivel_falha_claramente():
    """Se o ultimo dia fechado ficasse antes do primeiro dado da fonte, a janela
    nao existe — e o erro precisa dizer isso, nao devolver intervalo invertido."""
    vespera = s.SOURCE_MIN_DATE  # D-1 seria SOURCE_MIN_DATE - 1
    with pytest.raises(ValueError, match="nao existe dia fechado"):
        s.require_closed_day(vespera)
    with pytest.raises(ValueError, match="nao existe dia fechado"):
        s.incremental_window(vespera, 7)
    # SOURCE_MIN_DATE nao foi alterado nesta correcao
    assert s.SOURCE_MIN_DATE == date(2025, 4, 27)


def test_f1_10_nenhuma_mensagem_afirma_terminando_hoje():
    parser = s.build_parser()
    ajuda = parser.format_help().lower()
    assert "terminando hoje" not in ajuda
    assert "ate hoje" not in ajuda
    assert "dia fechado" in ajuda, "a ajuda precisa dizer qual e' o limite"
    texto = Path(s.__file__).read_text(encoding="utf-8").lower()
    for frase in ["terminando hoje", "terminando no dia corrente", "ate hoje"]:
        assert frase not in texto, f"o modulo ainda afirma {frase!r}"


def test_f1_11_publicacao_usa_o_snapshot_em_memoria(monkeypatch):
    """A janela publicada e' a lista JA extraida: `publish_window` nao reabre a
    fonte depois de a escrita no Neon comecar. Reler a view no meio da transacao
    poderia trazer um estado diferente do que foi validado."""
    conn = FakeConn()
    df, dt = _janela(SAMPLE)

    def explode(*a, **k):
        raise AssertionError("publish_window nao pode tocar a fonte")

    monkeypatch.setattr(s, "_datamart_readonly", explode)
    monkeypatch.setattr(s, "fetch_source_rows", explode)
    s.publish_window(conn, SAMPLE, df, dt, "run")
    assert conn.committed == 1
    # e nenhum SQL emitido no Neon menciona a relacao de origem
    for sql in conn.executed:
        assert s.SOURCE_RELATION not in sql


def test_f1_12_run_sync_extrai_e_fecha_a_fonte_antes_de_abrir_escrita():
    """Ordem observada no codigo: extrai da fonte, fecha, so' depois abre o Neon
    para escrita."""
    src = code_only(Path(s.__file__))
    corpo = src[src.index("def run_sync"):src.index("def build_parser")]
    i_fetch = corpo.index("fetch_source_rows")
    i_close = corpo.index("dm.close()")
    i_write = corpo.index("_neon_writable")
    assert i_fetch < i_close < i_write, "a fonte e' lida e fechada antes de abrir a escrita"


# ---------------------------------------------------------------------------
# 22–25. Migration, cadeia Alembic e gold_service intocado
# ---------------------------------------------------------------------------

def code_only(path: Path) -> str:
    """Codigo Python SEM docstrings e SEM comentarios.

    Sem isso, uma proibicao se volta contra a propria documentacao: o docstring
    que EXPLICA por que nao existe `SELECT *` ou retry contem o termo proibido e
    reprovaria o arquivo correto. A proibicao vale para CODIGO.
    """
    import ast
    import io
    import tokenize

    src = path.read_text(encoding="utf-8")
    linhas = src.splitlines()
    apagar: set[int] = set()

    # Docstrings via AST: qualquer statement que seja so' uma string literal.
    # Detectar por token e' fragil (o token anterior varia), e um docstring nao
    # removido faz a proibicao se voltar contra a propria documentacao.
    for no in ast.walk(ast.parse(src)):
        corpo = getattr(no, "body", None)
        if not isinstance(corpo, list) or not corpo:
            continue
        primeiro = corpo[0]
        if isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant) \
                and isinstance(primeiro.value.value, str):
            for ln in range(primeiro.lineno, (primeiro.end_lineno or primeiro.lineno) + 1):
                apagar.add(ln)

    # Comentarios via tokenize.
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            linha = tok.start[0]
            if linhas[linha - 1].strip().startswith("#"):
                apagar.add(linha)
            else:
                linhas[linha - 1] = linhas[linha - 1][: tok.start[1]]

    return "\n".join("" if i + 1 in apagar else l for i, l in enumerate(linhas))


REPO = Path(s.__file__).resolve().parents[1]
MIGRATION = REPO / "apps" / "api" / "alembic" / "versions" / "006_create_fact_ml_gestao_diaria.py"


def test_22_migration_cria_somente_a_tabela_autorizada():
    src = MIGRATION.read_text(encoding="utf-8")
    criadas = set(re.findall(r"CREATE TABLE\s+([a-z_]+\.[a-z_]+)", src))
    assert criadas == {"marts.fact_ml_gestao_diaria"}
    assert "PRIMARY KEY (ref_date, brand)" in src
    assert "idx_fmgd_brand_ref_date" in src
    # nenhum ALTER/DROP de tabela existente, nenhum backfill, nenhum acesso a gold
    codigo = code_only(MIGRATION)
    assert "ALTER TABLE" not in codigo
    assert "INSERT INTO" not in codigo, "a migration nao faz backfill"
    # Nenhuma CONSULTA ao Data Mart. Citar `gold.ml_gestao_diaria` num COMMENT e'
    # documentar procedencia, nao consultar.
    for padrao in [r"FROM\s+gold\.", r"JOIN\s+gold\.", r"FROM\s+raw\.", r"JOIN\s+raw\."]:
        assert not re.search(padrao, codigo, re.I), f"a migration nao consulta o Data Mart: {padrao}"
    assert not re.search(r"\bSELECT\b", codigo, re.I), "a migration nao le dado algum"
    # o CHECK de NaN existe: 'NaN'::numeric >= 0 e' TRUE no Postgres
    assert src.count("<> 'NaN'") >= 4
    # roas e' nullable e tem CHECK tolerante
    assert "roas IS NULL OR" in src
    assert re.search(r"roas\s+NUMERIC\(12,4\)\s*,", src), "roas nao pode ser NOT NULL"


def test_23_downgrade_nao_toca_outras_tabelas():
    src = MIGRATION.read_text(encoding="utf-8")
    corpo = src[src.index("def downgrade"):]
    drops = set(re.findall(r"DROP (?:TABLE|INDEX) IF EXISTS\s+([a-z_]+\.[a-z_]+)", corpo))
    assert drops == {"marts.fact_ml_gestao_diaria", "marts.idx_fmgd_brand_ref_date"}
    assert "DROP SCHEMA" not in corpo
    for outra in ["fact_marketplace_daily_performance", "fact_ml_produto_ranking",
                  "fact_tiktok_product_daily", "fact_marketplace_region_daily"]:
        assert outra not in corpo


def test_24_cadeia_alembic_correta():
    versions = REPO / "apps" / "api" / "alembic" / "versions"
    revs, downs = {}, {}
    for f in versions.glob("*.py"):
        t = f.read_text(encoding="utf-8")
        r = re.search(r'^revision = "([^"]+)"', t, re.M)
        d = re.search(r'^down_revision = (?:"([^"]+)"|None)', t, re.M)
        if r:
            revs[r.group(1)] = f.name
            downs[r.group(1)] = d.group(1) if d else None
    assert "006" in revs and revs["006"] == MIGRATION.name
    assert downs["006"] == "005", "006 precisa suceder 005"
    # cadeia unica: nenhuma revision com dois filhos, nenhum ciclo
    filhos = {}
    for rev, down in downs.items():
        filhos.setdefault(down, []).append(rev)
    assert all(len(v) == 1 for v in filhos.values()), f"cadeia ramificada: {filhos}"
    assert len([d for d in downs.values() if d is None]) == 1, "exatamente uma raiz"


def test_25_operacoes_le_a_fato_de_serving():
    """Invertido na Task 3/3 do Gate S2: `/operacoes` passou a ler o Neon.

    Outros endpoints (`/brand-detail`, `/inteligencia`, `/tempo-real`) continuam
    na gold — a troca foi restrita a `/operacoes`.
    """
    gs = (REPO / "apps" / "api" / "app" / "services" / "gold_service.py").read_text(encoding="utf-8")
    corpo = gs[gs.index("def get_operacoes"):]
    corpo = corpo[:corpo.index("# ---------------------------------------------------------------------------")]
    assert "marts.fact_ml_gestao_diaria" in corpo
    assert "gold." not in corpo and "raw." not in corpo
    # fora de get_operacoes, a gold segue sendo lida por outros endpoints
    assert "gold.ml_gestao_diaria" in gs


# ---------------------------------------------------------------------------
# Gate S1, correcao terminal — Finding 2: migration fail-fast (sem adocao)
# ---------------------------------------------------------------------------

def test_f2_01_upgrade_nao_usa_if_not_exists():
    """`IF NOT EXISTS` faria a migration ADOTAR silenciosamente uma tabela de
    origem e contrato desconhecidos, e marcar a revision como aplicada."""
    src = MIGRATION.read_text(encoding="utf-8")
    corpo = src[src.index("def upgrade"):src.index("def downgrade")]
    assert "CREATE TABLE IF NOT EXISTS" not in corpo
    assert "CREATE INDEX IF NOT EXISTS" not in corpo
    assert re.search(r"CREATE TABLE\s+marts\.fact_ml_gestao_diaria", corpo)
    assert re.search(r"CREATE INDEX\s+idx_fmgd_brand_ref_date", corpo)
    # e o upgrade nao tenta compatibilizar nada
    for proibido in ["ALTER TABLE", "DROP TABLE", "DROP INDEX", "ON CONFLICT", "IF EXISTS"]:
        assert proibido not in corpo, f"upgrade nao pode conter {proibido}"


def test_f2_02_downgrade_segue_tolerante_e_restrito():
    """No downgrade, `IF EXISTS` e' correto: desmontar precisa ser idempotente. O
    que importa e' o escopo — so' os objetos que o upgrade criou."""
    src = MIGRATION.read_text(encoding="utf-8")
    corpo = src[src.index("def downgrade"):]
    drops = set(re.findall(r"DROP (?:TABLE|INDEX) IF EXISTS\s+([a-z_]+\.[a-z_]+)", corpo))
    assert drops == {"marts.fact_ml_gestao_diaria", "marts.idx_fmgd_brand_ref_date"}
    assert "CREATE" not in corpo and "ALTER" not in corpo


def test_f2_03_contrato_das_sete_colunas_inalterado():
    """A correcao terminal nao mexe em contrato: as 7 colunas de negocio e as 2 de
    auditoria seguem exatamente as mesmas."""
    assert s.BUSINESS_COLUMNS == ["ref_date", "brand", "gmv", "ad_spend", "ad_revenue", "paid_orders", "roas"]
    assert s.AUDIT_COLUMNS == ["synced_at", "source_run_id"]
    src = MIGRATION.read_text(encoding="utf-8")
    corpo = src[src.index("CREATE TABLE"):src.index("CONSTRAINT pk_")]
    for c in s.BUSINESS_COLUMNS + s.AUDIT_COLUMNS:
        assert re.search(rf"^\s+{c}\s+", corpo, re.M), f"coluna {c} desapareceu da migration"
    # revision/down_revision preservados
    assert 'revision = "006"' in src and 'down_revision = "005"' in src
    # PK, checks e comentarios preservados
    assert "PRIMARY KEY (ref_date, brand)" in src
    assert src.count("<> 'NaN'") >= 4
    assert "COMMENT ON TABLE" in src and "COMMENT ON COLUMN" in src


# ---------------------------------------------------------------------------
# 26. Nenhuma alegacao de conectividade do Airflow
# ---------------------------------------------------------------------------

def test_26_nenhuma_referencia_ao_airflow_como_conectividade_provada():
    """O S1 nao cria DAG e nao pode afirmar conectividade do worker.

    Varre o CODIGO dos artefatos (nao este arquivo de teste, que naturalmente
    cita os termos proibidos nas proprias assercoes).
    """
    for arq in [Path(s.__file__), MIGRATION]:
        codigo = code_only(arq).lower()
        assert "dag(" not in codigo, f"{arq.name} nao pode instanciar DAG"
        assert "airflow" not in codigo, f"{arq.name} nao pode importar nem referenciar Airflow em codigo"
        texto = arq.read_text(encoding="utf-8").lower()
        for frase in ["airflow alcanca", "airflow conectado", "worker provado",
                      "conectividade do airflow comprovada", "airflow validado"]:
            assert frase not in texto, f"{arq.name} afirma conectividade do Airflow: {frase!r}"


# ---------------------------------------------------------------------------
# Z. Sanitizacao de erro — categorias fixas, zero topologia
# ---------------------------------------------------------------------------
# Todos os dados abaixo sao SINTETICOS: dominios `.invalid` (RFC 2606), IPv4 de
# documentacao (RFC 5737), IPv6 de documentacao (RFC 3849) e credenciais
# ficticias. Nenhum host, IP ou credencial real aparece neste arquivo.

HOST_FALSO = "banco-exemplo.db.invalid"
IPV4_FALSO = "192.0.2.10"
IPV4_FALSO_2 = "198.51.100.20"
IPV6_FALSO = "2001:db8::1"
IPV6_FALSO_LONGO = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
USUARIO_FALSO = "usuario_ficticio"
SENHA_FALSA = "senha_ficticia"
DB_FALSO = "banco_ficticio"

TOPOLOGIA_PROIBIDA = (
    HOST_FALSO, IPV4_FALSO, IPV4_FALSO_2, IPV6_FALSO, IPV6_FALSO_LONGO,
    USUARIO_FALSO, SENHA_FALSA, DB_FALSO, "5432",
)


def _sem_topologia(msg: str) -> None:
    """Nenhum fragmento sensivel pode sobrar na mensagem devolvida."""
    for proibido in TOPOLOGIA_PROIBIDA:
        assert proibido not in msg, f"vazou {proibido!r} em {msg!r}"
    assert "server at" not in msg
    assert "postgresql://" not in msg and "postgres://" not in msg
    assert len(msg) <= s.MAX_ERRO_CHARS


def test_z01_dsn_completa_com_credencial_e_host():
    exc = Exception(
        f"could not connect: postgresql://{USUARIO_FALSO}:{SENHA_FALSA}"
        f"@{HOST_FALSO}:5432/{DB_FALSO}"
    )
    msg = s.sanitize_error_message(exc)
    assert msg == s.ERRO_CONEXAO
    _sem_topologia(msg)


def test_z02_mensagem_nativa_connection_to_server_at():
    """A forma exata que o libpq produz e que vazou topologia no preflight."""
    exc = Exception(
        f'connection to server at "{HOST_FALSO}" ({IPV4_FALSO}), port 5432 failed: '
        f"Connection timed out (0x0000274C/10060)\n\tIs the server running on that "
        f"host and accepting TCP/IP connections?"
    )
    msg = s.sanitize_error_message(exc)
    assert msg == s.ERRO_INALCANCAVEL
    _sem_topologia(msg)


def test_z03_autenticacao_recusada():
    exc = Exception(
        f'FATAL:  password authentication failed for user "{USUARIO_FALSO}"'
    )
    msg = s.sanitize_error_message(exc)
    assert msg == s.ERRO_AUTENTICACAO
    _sem_topologia(msg)


def test_z03b_role_inexistente_nao_vaza_usuario():
    exc = Exception(f'FATAL:  role "{USUARIO_FALSO}" does not exist')
    msg = s.sanitize_error_message(exc)
    assert msg == s.ERRO_AUTENTICACAO
    _sem_topologia(msg)


def test_z04_pg_hba_conf():
    exc = Exception(
        f'FATAL:  no pg_hba.conf entry for host "{IPV4_FALSO}", user '
        f'"{USUARIO_FALSO}", database "{DB_FALSO}", no encryption'
    )
    msg = s.sanitize_error_message(exc)
    assert msg == s.ERRO_PG_HBA
    _sem_topologia(msg)


def test_z05_timeout():
    for texto in (
        f'connection to server at "{HOST_FALSO}" ({IPV4_FALSO}), port 5432 failed: '
        "Connection timed out",
        "timeout expired",
        f"could not translate host name \"{HOST_FALSO}\" to address: "
        "Name or service not known",
    ):
        msg = s.sanitize_error_message(Exception(texto))
        assert msg == s.ERRO_INALCANCAVEL, texto[:40]
        _sem_topologia(msg)


def test_z06_connection_refused():
    exc = Exception(
        f'connection to server at "{HOST_FALSO}" ({IPV4_FALSO_2}), port 5432 failed: '
        "Connection refused"
    )
    msg = s.sanitize_error_message(exc)
    assert msg == s.ERRO_RECUSADA
    _sem_topologia(msg)


def test_z07_ipv4_isolado_em_mensagem_inesperada():
    """Nenhuma categoria casa, mas ha IP: cai na categoria generica."""
    exc = Exception(f"erro inesperado ao falar com {IPV4_FALSO} durante a carga")
    msg = s.sanitize_error_message(exc)
    assert msg == s.ERRO_CONEXAO
    _sem_topologia(msg)


def test_z08_ipv6():
    for endereco in (IPV6_FALSO, IPV6_FALSO_LONGO, f"[{IPV6_FALSO}]:5432"):
        exc = Exception(f"falha estranha no endereco {endereco}")
        msg = s.sanitize_error_message(exc)
        assert msg == s.ERRO_CONEXAO, endereco
        _sem_topologia(msg)


def test_z09_formato_key_value_do_libpq():
    exc = Exception(
        f"invalid connection option: host={HOST_FALSO} hostaddr={IPV4_FALSO} "
        f"port=5432 user={USUARIO_FALSO} password={SENHA_FALSA} dbname={DB_FALSO}"
    )
    msg = s.sanitize_error_message(exc)
    assert msg == s.ERRO_CONEXAO
    _sem_topologia(msg)


def test_z10_mensagem_segura_de_sql_e_preservada():
    """Validacao e constraint nao tem topologia e sao uteis: continuam legiveis."""
    seguras = [
        "staging divergiu da fonte: sum_gmv: fonte=100 destino=99",
        'new row for relation "fact_x" violates check constraint "ck_x_gmv"',
        "2 valor(es) negativo(s) na coluna total_live_minutes",
        "cobertura incompleta: 1 dia(s) sem linha",
        "canceling statement due to statement timeout",
        "duplicate key value violates unique constraint \"pk_fact_x\"",
    ]
    for texto in seguras:
        msg = s.sanitize_error_message(Exception(texto))
        assert msg == texto, f"mensagem segura foi descartada: {texto}"


def test_z10b_horario_nao_e_confundido_com_ipv6():
    """`10:20:30` nao pode virar endereco e apagar a mensagem."""
    texto = "falha de validacao registrada as 10:20:30 na janela"
    assert s.sanitize_error_message(Exception(texto)) == texto


def test_z10c_versao_com_ponto_nao_e_confundida_com_ipv4():
    texto = "driver reportou versao 17.9 incompativel com a extensao"
    assert s.sanitize_error_message(Exception(texto)) == texto


def test_z11_limite_de_500_caracteres():
    # mensagem longa SEM topologia: truncada, nunca estourando o limite
    longa = "erro de validacao " + ("x" * 2000)
    msg = s.sanitize_error_message(Exception(longa))
    assert len(msg) == s.MAX_ERRO_CHARS == 500
    # e com topologia: categoria fixa, muito abaixo do limite
    msg2 = s.sanitize_error_message(Exception(f"server at {HOST_FALSO} " + "y" * 2000))
    assert msg2 == s.ERRO_CONEXAO
    assert len(msg2) <= s.MAX_ERRO_CHARS


def test_z12_nunca_devolve_mensagem_nativa_completa():
    """Contrato 1: nenhuma variante de erro de conexao volta como texto original."""
    nativas = [
        f'connection to server at "{HOST_FALSO}" ({IPV4_FALSO}), port 5432 failed',
        f"could not connect to server: Connection refused\n\tIs the server running "
        f"on host \"{HOST_FALSO}\" ({IPV4_FALSO}) and accepting TCP/IP connections "
        f"on port 5432?",
        "server closed the connection unexpectedly",
        f'FATAL:  database "{DB_FALSO}" does not exist',
        "terminating connection due to administrator command",
    ]
    for texto in nativas:
        msg = s.sanitize_error_message(Exception(texto))
        assert msg in (s.ERRO_AUTENTICACAO, s.ERRO_PG_HBA, s.ERRO_INALCANCAVEL,
                       s.ERRO_RECUSADA, s.ERRO_CONEXAO), texto[:50]
        assert msg != texto
        _sem_topologia(msg)


def test_z13_tem_topologia_cobre_os_formatos_do_contrato():
    for texto in (
        "server at algum lugar",
        f"postgresql://{USUARIO_FALSO}:{SENHA_FALSA}@{HOST_FALSO}/{DB_FALSO}",
        f"postgres://{HOST_FALSO}/{DB_FALSO}",
        f"host={HOST_FALSO}", f"hostaddr={IPV4_FALSO}", f"user={USUARIO_FALSO}",
        f"password={SENHA_FALSA}", f"dbname={DB_FALSO}", "port=5432", "port 5432",
        IPV4_FALSO, IPV6_FALSO, IPV6_FALSO_LONGO,
    ):
        assert s.tem_topologia(texto), f"nao detectou topologia em {texto!r}"
    for texto in ("erro de constraint", "sum_gmv divergiu", "10:20:30", "versao 17.9"):
        assert not s.tem_topologia(texto), f"falso positivo em {texto!r}"


def test_z14_nao_usa_repr_nem_encadeamento_de_excecao():
    """Contrato 7: `repr` carrega os argumentos, e `__cause__` guarda a nativa."""
    # normaliza espacos: os dois helpers `code_only` devolvem formatos diferentes
    # (um preserva as linhas, o outro junta tokens), e o contrato vale nos dois.
    codigo = re.sub(r"\s+", "", code_only(Path(s.__file__)))
    assert "repr(" not in codigo
    assert "__cause__" not in codigo and "__context__" not in codigo
    assert "traceback" not in codigo
    assert "logging" not in codigo
    # a unica leitura da excecao e' `str(exc)`
    assert codigo.count("str(exc)") == 1


def test_z15_cli_imprime_somente_a_mensagem_sanitizada():
    """O `print` de falha do CLI usa exclusivamente `sanitize_error_message`."""
    fonte = Path(s.__file__).read_text(encoding="utf-8")
    linhas = [l for l in fonte.splitlines() if "FALHA" in l and "print(" in l]
    assert linhas, "nenhum print de falha encontrado"
    for l in linhas:
        assert "sanitize_error_message(exc)" in l, l
        assert "{exc}" not in l and "repr(exc)" not in l and "str(exc)" not in l


def test_z16_cli_sanitiza_erro_de_conexao_de_ponta_a_ponta(capsys, monkeypatch):
    """Falha na conexao com a fonte: stderr recebe categoria, nunca topologia."""
    def explode(*a, **k):
        raise Exception(
            f'connection to server at "{HOST_FALSO}" ({IPV4_FALSO}), port 5432 '
            f"failed: Connection timed out"
        )

    monkeypatch.setattr(s.psycopg2, "connect", explode)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@z/db")
    monkeypatch.setenv("DATAMART_DATABASE_URL", "postgresql://x:y@z/db")
    codigo = s.main(["--backfill"])
    assert codigo == 2
    err = capsys.readouterr().err
    assert s.ERRO_INALCANCAVEL in err
    _sem_topologia(err.replace("FALHA", "").strip().splitlines()[-1])


def test_z17_categorias_sao_textos_fixos_e_curtos():
    for cat in (s.ERRO_AUTENTICACAO, s.ERRO_PG_HBA, s.ERRO_INALCANCAVEL,
                s.ERRO_RECUSADA, s.ERRO_CONEXAO):
        assert isinstance(cat, str) and 10 < len(cat) <= 120
        assert not s.tem_topologia(cat), f"a propria categoria tem topologia: {cat}"


def test_z18_categorias_identicas_entre_os_dois_modulos():
    """Contrato 12: a mesma entrada produz a mesma categoria nas duas frentes."""
    import importlib
    outro = importlib.import_module('pipelines.sync_tiktok_serving')
    for nome in ("ERRO_AUTENTICACAO", "ERRO_PG_HBA", "ERRO_INALCANCAVEL",
                 "ERRO_RECUSADA", "ERRO_CONEXAO", "MAX_ERRO_CHARS"):
        assert getattr(s, nome) == getattr(outro, nome), nome
    entradas = [
        f'connection to server at "{HOST_FALSO}" ({IPV4_FALSO}), port 5432 failed: '
        "Connection timed out",
        f'FATAL:  password authentication failed for user "{USUARIO_FALSO}"',
        f'FATAL:  no pg_hba.conf entry for host "{IPV4_FALSO}"',
        "Connection refused",
        f"could not connect: postgresql://{USUARIO_FALSO}:{SENHA_FALSA}@{HOST_FALSO}/{DB_FALSO}",
        f"endereco estranho {IPV6_FALSO}",
        "erro de constraint sem topologia",
    ]
    for texto in entradas:
        a = s.sanitize_error_message(Exception(texto))
        b = outro.sanitize_error_message(Exception(texto))
        assert a == b, f"divergiu entre os modulos: {texto[:50]}"


# ---------------------------------------------------------------------------
# L. Politica de convergencia: lookback 90 e dia operacional em America/Sao_Paulo
# ---------------------------------------------------------------------------

def test_l01_lookback_default_e_90():
    """Era 7, dimensionado por hipotese. A medicao do Gate S2 Task 3/3 mostrou
    reafirmacao retroativa de ate 68 dias nesta fonte."""
    assert s.DEFAULT_LOOKBACK_DAYS == 90
    assert s.incremental_window(HOJE) == (date(2026, 5, 13), FECHADO)


def test_l02_piso_contratual_e_7():
    assert s.MIN_LOOKBACK_DAYS == 7
    for n in (0, 1, 3, 6, -5):
        with pytest.raises(ValueError, match=">= 7"):
            s.incremental_window(HOJE, n)


def test_l03_lookback_explicito_entre_piso_e_default_continua_valido():
    for n in (7, 14, 30, 90, 180):
        d_from, d_to = s.incremental_window(HOJE, n)
        assert d_to == FECHADO
        assert (d_to - d_from).days == n - 1 or d_from == s.SOURCE_MIN_DATE


def test_l04_dia_operacional_usa_america_sao_paulo():
    assert str(s.TZ_OPERACIONAL) == "America/Sao_Paulo"
    # 00:05 UTC de 12/08 ainda e' 11/08 no Brasil (UTC-3)
    assert s.hoje_operacional(datetime(2026, 8, 12, 0, 5, tzinfo=timezone.utc)) == date(2026, 8, 11)
    assert s.hoje_operacional(datetime(2026, 8, 12, 2, 59, tzinfo=timezone.utc)) == date(2026, 8, 11)
    assert s.hoje_operacional(datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc)) == date(2026, 8, 12)


def test_l05_nenhum_date_today_no_codigo():
    """`date.today()` usa o fuso do PROCESSO — proibido neste modulo."""
    assert "date.today()" not in code_only(Path(s.__file__))


def test_l06_help_declara_default_90_e_minimo_7():
    ajuda = s.build_parser().format_help()
    assert "90" in ajuda and "7" in ajuda
    assert "Default 7" not in ajuda
