"""
Testes de pipelines/reconciliation/monitor_bug8_invariants.py: monitor
read-only pos-carga do Bug 8. Valida invariantes (nao snapshots), nunca
referencia o Data Mart, nunca contem DDL/DML, e detecta a assinatura de
regressao ao left merge (canceled_orders do Neon menor que o da fonte).

Usa conexoes falsas — nenhum banco real e' tocado. O FakeCursor levanta
AssertionError para qualquer query que nao comece com SELECT, tornando
"somente leitura" uma propriedade verificada.
"""
import re
from pathlib import Path

import pytest

import pipelines.reconciliation.monitor_bug8_invariants as mon

MODULE_PATH = Path(mon.__file__)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_sql = ""

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append(norm)
        self._last_sql = norm
        if not norm.upper().startswith("SELECT"):
            raise AssertionError(f"query nao-SELECT no monitor (deve ser somente leitura): {norm}")

    def fetchone(self):
        sql = self._last_sql
        for marker, value in self.conn.scalar_by_marker:
            if marker in sql:
                return {"n": value}
        return {"n": 0}

    def fetchall(self):
        return self.conn.agg_rows

    def close(self):
        pass


class FakeConn:
    """scalar_by_marker: lista de (trecho-da-query, valor) na ORDEM em que
    check_db_invariants executa as queries — casada por substring."""

    def __init__(self, dupes=0, nulls=0, negatives=0, incoherent=0, bad_rate=0,
                 cancel_only=7, agg_rows=None):
        self.executed = []
        self.closed = False
        self.agg_rows = agg_rows or []
        self.scalar_by_marker = [
            ("HAVING COUNT(*) > 1", dupes),
            ("IS NULL", nulls),
            ("gmv < 0", negatives),
            ("IS DISTINCT FROM 100", incoherent),
            ("ROUND(canceled_orders::numeric", bad_rate),
            ("completed_orders = 0 AND canceled_orders > 0", cancel_only),
        ]

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Camada 1 — invariantes do mart
# ---------------------------------------------------------------------------

def test_invariantes_limpa_nao_reporta_problema(capsys):
    problems = mon.check_db_invariants(FakeConn())
    assert problems == []
    out = capsys.readouterr().out
    assert "7 linha(s) so-cancelada(s)" in out  # contagem informativa aparece


def test_todas_as_queries_da_camada_1_sao_select():
    conn = FakeConn()
    mon.check_db_invariants(conn)
    assert conn.executed, "nenhuma query executada"
    assert all(q.upper().startswith("SELECT") for q in conn.executed)


def test_duplicatas_sao_detectadas():
    problems = mon.check_db_invariants(FakeConn(dupes=3))
    assert any("duplicada" in p for p in problems)


def test_nulos_sao_detectados():
    problems = mon.check_db_invariants(FakeConn(nulls=2))
    assert any("NULL" in p for p in problems)


def test_metricas_negativas_sao_detectadas():
    problems = mon.check_db_invariants(FakeConn(negatives=1))
    assert any("negativa" in p for p in problems)


def test_linha_so_cancelada_incoerente_e_detectada():
    problems = mon.check_db_invariants(FakeConn(incoherent=1))
    assert any("incoerente" in p for p in problems)


def test_cancel_rate_inconsistente_e_detectado():
    problems = mon.check_db_invariants(FakeConn(bad_rate=5))
    assert any("cancel_rate_pct inconsistente" in p for p in problems)


def test_zero_linhas_so_canceladas_nao_e_falha_dura_da_camada_1():
    """Zero linhas so-canceladas pode ser legitimo (fonte sem esses grupos
    num mes) — quem transforma em falha e' a camada 2 (fonte)."""
    problems = mon.check_db_invariants(FakeConn(cancel_only=0))
    assert problems == []


# ---------------------------------------------------------------------------
# Camada 2 — reconciliacao contra a fonte (funcao pura, sem banco)
# ---------------------------------------------------------------------------

SRC = {("kokeshi", "2026-06-01"): {"gmv": 100.0, "units_sold": 10, "completed_orders": 8, "canceled_orders": 3}}


def test_reconciliacao_identica_passa():
    neon = {("kokeshi", "2026-06-01"): dict(SRC[("kokeshi", "2026-06-01")])}
    assert mon.check_source_reconciliation(neon, SRC) == []


def test_canceled_menor_no_neon_tem_assinatura_de_regressao_bug8():
    neon = {("kokeshi", "2026-06-01"): {**SRC[("kokeshi", "2026-06-01")], "canceled_orders": 1}}
    problems = mon.check_source_reconciliation(neon, SRC)
    assert len(problems) == 1
    assert "canceled_orders diverge" in problems[0]
    assert "regressao ao left merge" in problems[0]


def test_canceled_maior_no_neon_diverge_sem_assinatura_de_left_merge():
    neon = {("kokeshi", "2026-06-01"): {**SRC[("kokeshi", "2026-06-01")], "canceled_orders": 9}}
    problems = mon.check_source_reconciliation(neon, SRC)
    assert len(problems) == 1
    assert "regressao ao left merge" not in problems[0]


def test_gmv_divergente_e_detectado():
    neon = {("kokeshi", "2026-06-01"): {**SRC[("kokeshi", "2026-06-01")], "gmv": 99.0}}
    problems = mon.check_source_reconciliation(neon, SRC)
    assert any("GMV diverge" in p for p in problems)


def test_combinacao_ausente_no_neon_e_detectada():
    problems = mon.check_source_reconciliation({}, SRC)
    assert any("nao no Neon" in p for p in problems)


def test_combinacao_extra_no_neon_e_detectada():
    neon = {("kokeshi", "2026-06-01"): dict(SRC[("kokeshi", "2026-06-01")]),
            ("apice", "2026-07-01"): {"gmv": 1.0, "units_sold": 1, "completed_orders": 1, "canceled_orders": 0}}
    problems = mon.check_source_reconciliation(neon, SRC)
    assert any("nao na fonte XLSX" in p for p in problems)


def test_tolerancia_de_arredondamento_de_gmv_nao_falha():
    neon = {("kokeshi", "2026-06-01"): {**SRC[("kokeshi", "2026-06-01")], "gmv": 100.005}}
    assert mon.check_source_reconciliation(neon, SRC) == []


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

def test_nunca_referencia_datamart_database_url():
    source = MODULE_PATH.read_text(encoding="utf-8")
    read_patterns = [
        r'os\.environ(?:\.get)?\(\s*["\']DATAMART_DATABASE_URL',
        r'os\.getenv\(\s*["\']DATAMART_DATABASE_URL',
    ]
    for pattern in read_patterns:
        assert not re.search(pattern, source), f"padrao proibido encontrado: {pattern}"


def test_nenhum_ddl_ou_dml_destrutivo_no_modulo():
    """Checa as FORMAS SQL (INSERT INTO, DELETE FROM, etc.) — 'insert'
    isolado e' legitimo em Python (sys.path.insert)."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    sql_forms = [
        r"\bINSERT\s+INTO\b", r"\bDELETE\s+FROM\b", r"\bDROP\s+TABLE\b",
        r"\bCREATE\s+TABLE\b", r"\bALTER\s+TABLE\b", r"\bUPDATE\s+marts\b",
    ]
    for pattern in sql_forms:
        assert not re.search(pattern, source, re.IGNORECASE), f"forma SQL proibida encontrada no monitor: {pattern}"
    forbidden = "".join(["t", "r", "u", "n", "c", "a", "t", "e"])
    assert forbidden not in source.lower()


def test_nao_ha_numeros_de_snapshot_historico_como_regra():
    """O monitor valida invariantes — os totais do swap (2471/53599) nao
    podem estar hardcoded como criterio, porque cargas futuras mudam."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    # remove docstrings/comentarios? Simples: os numeros nao devem aparecer
    # em nenhuma linha de codigo executavel (fora de aspas de docstring a
    # mencao "2.471"/"53.599" usa pontuacao pt-BR, entao basta checar os
    # literais crus).
    for literal in ("2471", "53599", "21174272"):
        assert literal not in source, f"snapshot historico {literal} hardcoded no monitor"


def test_credenciais_nunca_aparecem_na_saida(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://segredouser:S3nhaSecreta@ep-fake.neon.tech:5432/db")

    fake_conn = FakeConn()
    monkeypatch.setattr(mon, "_neon_readonly", lambda url: fake_conn)
    monkeypatch.setattr(mon.sys, "argv", ["monitor_bug8_invariants.py", "--skip-source"])

    exit_code = mon.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "S3nhaSecreta" not in out
    assert "segredouser" not in out
    assert "ep-fake.neon.tech" in out


def test_exit_code_1_em_divergencia(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")

    fake_conn = FakeConn(dupes=2)
    monkeypatch.setattr(mon, "_neon_readonly", lambda url: fake_conn)
    monkeypatch.setattr(mon.sys, "argv", ["monitor_bug8_invariants.py", "--skip-source"])

    exit_code = mon.main()
    assert exit_code == 1
    assert "DIVERGENCIA" in capsys.readouterr().out
    assert fake_conn.closed is True
