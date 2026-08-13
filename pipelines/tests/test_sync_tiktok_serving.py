"""Gate S2 Task 1/3 — testes do sync das duas fatos TikTok e das migrations 007/008.

NENHUM teste abre conexao real. As conexoes sao falsas e modelam staging e destino
em memoria, o que permite provar as propriedades que importam: DELETE restrito a
janela, remocao retroativa refletida, rollback integral, locks distintos por
tabela e staging temporaria.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import sync_tiktok_serving as s

HOJE = date(2026, 8, 11)
D1 = date(2026, 8, 10)

REPO = Path(s.__file__).resolve().parents[1]
MIG_007 = REPO / "apps" / "api" / "alembic" / "versions" / "007_create_fact_tiktok_brand_content_daily.py"
MIG_008 = REPO / "apps" / "api" / "alembic" / "versions" / "008_create_fact_tiktok_creator_daily.py"
MIG_006 = REPO / "apps" / "api" / "alembic" / "versions" / "006_create_fact_ml_gestao_diaria.py"
GOLD_SERVICE = REPO / "apps" / "api" / "app" / "services" / "gold_service.py"


def code_only(path: Path) -> str:
    """Codigo Python SEM docstrings e SEM comentarios.

    Sem isso, uma proibicao se volta contra a propria documentacao: o comentario
    que EXPLICA por que nao existe `SELECT *`, retry ou Airflow contem o termo
    proibido e reprovaria o arquivo correto. A proibicao vale para CODIGO.
    """
    src = path.read_text(encoding="utf-8")
    saida = []
    anterior = tokenize.INDENT
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and anterior in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL,
            ):
                continue
            if tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                anterior = tok.type
            saida.append(tok.string)
    except tokenize.TokenError:
        return src
    return " ".join(saida)


# ---------------------------------------------------------------------------
# Conexoes falsas: modelam staging e destino em memoria
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = -1
        self._result = None

    # -- helpers internos -------------------------------------------------
    def _agg(self, rows, params):
        d_from, d_to = params["date_from"], params["date_to"]
        dcol = self.conn.spec.date_column
        janela = [r for r in rows if d_from <= r[dcol] <= d_to]
        return s.aggregates_from_rows(self.conn.spec, janela)

    def _business_tuples(self, rows, params):
        d_from, d_to = params["date_from"], params["date_to"]
        dcol = self.conn.spec.date_column
        cols = self.conn.spec.business_columns
        return [tuple(r[c] for c in cols) for r in rows if d_from <= r[dcol] <= d_to]

    # -- API usada pelo modulo -------------------------------------------
    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        low = " ".join(sql.lower().split())

        if "pg_advisory_xact_lock" in low:
            self.conn.locks.append(params[0])
            self._result = {"n": 0}
            return
        if "set local statement_timeout" in low:
            return
        if "create temp table" in low:
            self.conn.staging_created = True
            self.conn.staging_ddl = low
            return
        if low.startswith("select count(*) as count"):
            fonte = self.conn.staging if "pg_temp." in low else self.conn.target
            self._result = self._agg(fonte, params)
            return
        if low.startswith("delete from"):
            if self.conn.falhar_no_delete:
                raise RuntimeError("falha injetada no DELETE")
            dcol = self.conn.spec.date_column
            d_from, d_to = params["date_from"], params["date_to"]
            antes = len(self.conn.target)
            self.conn.target = [r for r in self.conn.target
                                if not (d_from <= r[dcol] <= d_to)]
            self.rowcount = antes - len(self.conn.target)
            return
        if low.startswith("insert into") and "select" in low and "pg_temp." in low:
            self.conn.target.extend(dict(r) for r in self.conn.staging)
            self.rowcount = len(self.conn.staging)
            return
        if " except " in low:
            a_primeiro = low.index("pg_temp.") < low.index("marts.")
            a = self._business_tuples(self.conn.staging if a_primeiro else self.conn.target, params)
            b = self._business_tuples(self.conn.target if a_primeiro else self.conn.staging, params)
            self._result = {"n": len(set(a) - set(b))}
            return
        if "to_regclass" in low:
            self._result = {"existe": True}
            return
        raise AssertionError(f"SQL inesperado no fake: {low[:120]}")

    def fetchone(self):
        return self._result

    def close(self):
        self.conn.cursores_fechados += 1


class FakeConn:
    """Destino falso. `commit`/`rollback` sao observaveis, e o `ON COMMIT DROP`
    e' emulado limpando a staging nos dois casos."""

    def __init__(self, spec, target=None, falhar_no_delete=False):
        self.spec = spec
        self.target = [dict(r) for r in (target or [])]
        self.staging = []
        self.sql = []
        self.locks = []
        self.staging_created = False
        self.staging_ddl = ""
        self.commits = 0
        self.rollbacks = 0
        self.cursores_fechados = 0
        self.falhar_no_delete = falhar_no_delete
        self.readonly = None

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
        self.staging = []

    def rollback(self):
        self.rollbacks += 1
        self.staging = []

    def close(self):
        pass

    def set_session(self, readonly=False, **kw):
        self.readonly = readonly


class FakeSource:
    """Fonte falsa. Registra se foi aberta somente para leitura."""

    def __init__(self, rows):
        self.rows = rows
        self.readonly = None
        self.closed = False
        self.sql = []

    def set_session(self, readonly=False, **kw):
        self.readonly = readonly

    def cursor(self):
        return _SourceCursor(self)

    def close(self):
        self.closed = True


class _SourceCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        self._params = params

    def fetchall(self):
        return [dict(r) for r in self.conn.rows]

    def close(self):
        pass


def _fake_execute_values(cur, sql, batch, page_size=None):
    """Substitui psycopg2.extras.execute_values: converte as tuplas de volta em
    dicts e alimenta a staging em memoria."""
    spec = cur.conn.spec
    cols = spec.business_columns + ["source_run_id"]
    cur.conn.sql.append(sql)
    for tupla in batch:
        cur.conn.staging.append(dict(zip(cols, tupla)))


# ---------------------------------------------------------------------------
# Fixtures de dados
# ---------------------------------------------------------------------------

def brand_row(d: date, marca: str, **over) -> dict:
    r = {c: 0 for c in s.BRAND_SPEC.additive_columns}
    r.update({"date": d, "brand": marca, "gmv": 100, "orders": 2})
    for c in s.BRAND_SPEC.ratio_columns:
        r[c] = None
    r["visitors"] = None
    r["customers"] = None
    r.update(over)
    return r


def creator_row(d: date, marca: str, criador: str, **over) -> dict:
    r = {c: 0 for c in s.CREATOR_SPEC.additive_columns}
    r.update({"date": d, "brand": marca, "creator": criador, "gmv_total": 50})
    r.update(over)
    return r


def janela_brand(dias=3, marcas=("apice", "barbours")) -> list[dict]:
    rows = []
    for i in range(dias):
        d = date(2026, 8, 8) + __import__("datetime").timedelta(days=i)
        for m in marcas:
            rows.append(brand_row(d, m))
    return rows


# ---------------------------------------------------------------------------
# A. Contrato das specs
# ---------------------------------------------------------------------------

def test_a01_duas_specs_fixas_e_nada_dinamico():
    assert set(s.SPECS) == {"brand", "creator"}
    assert s.BRAND_SPEC.source_relation == "gold.tiktok_brand_daily"
    assert s.BRAND_SPEC.target_table == "marts.fact_tiktok_brand_content_daily"
    assert s.CREATOR_SPEC.source_relation == "gold.tiktok_creator_daily"
    assert s.CREATOR_SPEC.target_table == "marts.fact_tiktok_creator_daily"


def test_a02_chaves_reais_da_auditoria():
    assert s.BRAND_SPEC.key_columns == ["date", "brand"]
    assert s.CREATOR_SPEC.key_columns == ["date", "brand", "creator"]


def test_a03_colunas_explicitas_e_sem_duplicata():
    for spec in (s.BRAND_SPEC, s.CREATOR_SPEC):
        cols = spec.business_columns
        assert len(cols) == len(set(cols)), f"{spec.name}: coluna repetida"
        assert not set(cols) & set(s.AUDIT_COLUMNS), "auditoria nao e' coluna de negocio"
    assert len(s.BRAND_SPEC.business_columns) == 37
    assert len(s.CREATOR_SPEC.business_columns) == 9


def test_a04_opcionais_ficam_fora_das_obrigatorias():
    # 14 percentuais 100% nulos + visitors/customers
    assert len(s.BRAND_SPEC.optional_columns) == 16
    assert set(s.BRAND_SPEC.required_columns).isdisjoint(s.BRAND_SPEC.optional_columns)
    assert len(s.BRAND_SPEC.required_columns) == 21
    # creator_daily nao tem coluna opcional: zero nulo em todo o historico
    assert s.CREATOR_SPEC.optional_columns == []
    assert len(s.CREATOR_SPEC.required_columns) == 9


def test_a05_razoes_nunca_entram_nas_aditivas():
    assert set(s.BRAND_SPEC.ratio_columns).isdisjoint(s.BRAND_SPEC.additive_columns)
    assert len(s.BRAND_SPEC.ratio_columns) == 14
    assert s.CREATOR_SPEC.ratio_columns == []


def test_a06_colunas_assinadas_nao_recebem_check_de_nao_negativo():
    assert set(s.BRAND_SPEC.signed_columns) == {"total_fees", "total_live_minutes"}
    rows = [brand_row(D1, "apice", total_fees=-999, total_live_minutes=-29545461)]
    assert s.negatives_in_rows(s.BRAND_SPEC, rows) == {}, "assinadas nao podem reprovar"


def test_a07_locks_distintos_por_tabela():
    assert s.BRAND_SPEC.advisory_lock_key != s.CREATOR_SPEC.advisory_lock_key
    # e distintos do lock do Gate S1, senao as frentes se bloqueariam
    assert 906_120_006 not in (s.BRAND_SPEC.advisory_lock_key, s.CREATOR_SPEC.advisory_lock_key)


# ---------------------------------------------------------------------------
# B. Janela: D-1, incremental movel, dia corrente
# ---------------------------------------------------------------------------

def test_b01_ultimo_dia_fechado_e_d_menos_1():
    assert s.last_closed_date(HOJE) == D1


def test_b02_incremental_de_sete_dias_termina_em_d1():
    for spec in (s.BRAND_SPEC, s.CREATOR_SPEC):
        assert s.incremental_window(spec, HOJE, 7) == (date(2026, 8, 4), D1)


def test_b03_incremental_movel_maior_que_sete():
    d_from, d_to = s.incremental_window(s.BRAND_SPEC, HOJE, 30)
    assert d_to == D1 and (d_to - d_from).days == 29


def test_b04_lookback_menor_que_sete_e_recusado():
    for n in (0, 1, 3, 6, -5):
        with pytest.raises(ValueError, match=">= 7"):
            s.incremental_window(s.BRAND_SPEC, HOJE, n)


def test_b05_backfill_termina_em_d1_e_comeca_no_primeiro_dado():
    assert s.backfill_window(s.BRAND_SPEC, HOJE) == (date(2025, 10, 5), D1)
    assert s.backfill_window(s.CREATOR_SPEC, HOJE) == (date(2025, 10, 7), D1)


def test_b06_dia_corrente_recusado_explicitamente():
    with pytest.raises(ValueError, match="dia corrente recusado"):
        s.validate_window(s.BRAND_SPEC, date(2026, 8, 4), HOJE, HOJE)


def test_b07_janela_futura_recusada():
    with pytest.raises(ValueError, match="janela futura"):
        s.validate_window(s.BRAND_SPEC, date(2026, 8, 4), date(2026, 8, 12), HOJE)


def test_b08_janela_invertida_recusada():
    with pytest.raises(ValueError, match="invertida"):
        s.validate_window(s.BRAND_SPEC, D1, date(2026, 8, 1), HOJE)


def test_b09_antes_do_primeiro_dado_recusado():
    with pytest.raises(ValueError, match="anterior ao primeiro dado"):
        s.validate_window(s.BRAND_SPEC, date(2025, 1, 1), D1, HOJE)


def test_b10_nenhuma_janela_alcanca_o_dia_corrente():
    """Varredura: nenhuma combinacao de modo produz date_to == hoje."""
    for spec in (s.BRAND_SPEC, s.CREATOR_SPEC):
        for n in (7, 14, 30, 90, 400):
            assert s.incremental_window(spec, HOJE, n)[1] == D1
        assert s.backfill_window(spec, HOJE)[1] == D1


def test_b11_deficit_de_13_meses_e_declarado_nao_reprovado():
    """A fonte tem ~10 meses. O piso de 13 vale quando a fonte o possui."""
    assert s.MIN_HISTORY_MONTHS == 13
    assert s.history_deficit_days(s.BRAND_SPEC, HOJE) > 0
    assert s.history_deficit_days(s.CREATOR_SPEC, HOJE) > 0
    # e ainda assim o backfill roda, levando todo o historico disponivel
    assert s.backfill_window(s.BRAND_SPEC, HOJE)[0] == s.BRAND_SPEC.source_min_date


def test_b12_months_before_atravessa_ano_e_fevereiro():
    assert s.months_before(date(2026, 8, 10), 13) == date(2025, 7, 10)
    assert s.months_before(date(2026, 1, 31), 1) == date(2025, 12, 31)
    assert s.months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert s.months_before(date(2024, 3, 31), 1) == date(2024, 2, 29)


# ---------------------------------------------------------------------------
# C. Validacao da fonte: chave, nulos, NaN, negativos, cobertura
# ---------------------------------------------------------------------------

def test_c01_duplicidade_de_chave_reprova():
    rows = [brand_row(D1, "apice"), brand_row(D1, "apice")]
    assert s.duplicates_in_rows(s.BRAND_SPEC, rows) == 1
    assert any("duplicada" in p for p in s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1))


def test_c02_chave_de_criador_usa_tres_colunas():
    rows = [creator_row(D1, "apice", "a"), creator_row(D1, "apice", "b")]
    assert s.duplicates_in_rows(s.CREATOR_SPEC, rows) == 0
    rows.append(creator_row(D1, "apice", "a"))
    assert s.duplicates_in_rows(s.CREATOR_SPEC, rows) == 1


def test_c03_nulo_em_obrigatoria_reprova():
    rows = [brand_row(D1, "apice", gmv=None)]
    assert s.missing_required(s.BRAND_SPEC, rows)["gmv"] == 1
    assert any("obrigatoria gmv" in p for p in s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1))


def test_c04_nulo_em_opcional_nao_reprova():
    rows = [brand_row(D1, "apice", visitors=None, viewers_pct_female=None)]
    assert s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1) == []


def test_c05_nan_reprova_mesmo_passando_por_check_de_postgres():
    rows = [brand_row(D1, "apice", gmv=float("nan"))]
    assert s.nan_in_rows(s.BRAND_SPEC, rows) == {"gmv": 1}
    assert any("NaN" in p for p in s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1))


def test_c06_negativo_em_metrica_nao_assinada_reprova():
    rows = [brand_row(D1, "apice", gmv=-1)]
    assert s.negatives_in_rows(s.BRAND_SPEC, rows) == {"gmv": 1}
    assert any("negativo" in p for p in s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1))


def test_c07_cobertura_e_por_dia_nao_por_dia_x_marca():
    rows = [brand_row(date(2026, 8, 9), "apice"), brand_row(D1, "barbours")]
    cob = s.date_coverage(s.BRAND_SPEC, rows, date(2026, 8, 9), D1)
    assert cob["complete"] is True, "marca ausente num dia nao pode reprovar"
    rows = [brand_row(date(2026, 8, 9), "apice")]
    assert s.date_coverage(s.BRAND_SPEC, rows, date(2026, 8, 9), D1)["complete"] is False


def test_c08_linha_fora_da_janela_reprova():
    rows = [brand_row(date(2026, 7, 1), "apice")]
    assert any("fora da janela" in p for p in s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1))


def test_c09_razoes_nao_entram_em_soma():
    rows = [brand_row(D1, "apice", viewers_pct_female=50)]
    agg = s.aggregates_from_rows(s.BRAND_SPEC, rows)
    assert "sum_viewers_pct_female" not in agg
    assert agg["nn_viewers_pct_female"] == 1


# ---------------------------------------------------------------------------
# D. Publicacao: staging, DELETE da janela, remocao retroativa, rollback
# ---------------------------------------------------------------------------

def _publica(monkeypatch, spec, rows, target=None, falhar_no_delete=False):
    monkeypatch.setattr(s, "execute_values", _fake_execute_values)
    conn = FakeConn(spec, target=target, falhar_no_delete=falhar_no_delete)
    dcol = spec.date_column
    d_from = min(r[dcol] for r in rows)
    d_to = max(r[dcol] for r in rows)
    res = s.publish_window(conn, spec, rows, d_from, d_to, "t-run")
    return conn, res


def test_d01_publicacao_usa_lock_da_propria_tabela(monkeypatch):
    conn, _ = _publica(monkeypatch, s.BRAND_SPEC, janela_brand())
    assert conn.locks == [s.BRAND_SPEC.advisory_lock_key]
    conn2, _ = _publica(monkeypatch, s.CREATOR_SPEC,
                        [creator_row(D1, "apice", "c1")])
    assert conn2.locks == [s.CREATOR_SPEC.advisory_lock_key]


def test_d02_staging_e_temporaria_e_cai_no_commit(monkeypatch):
    conn, _ = _publica(monkeypatch, s.BRAND_SPEC, janela_brand())
    assert conn.staging_created
    assert "create temp table" in conn.staging_ddl
    assert "on commit drop" in conn.staging_ddl
    assert conn.staging == [], "ON COMMIT DROP: staging nao sobrevive ao commit"


def test_d03_publicacao_commita_uma_vez_e_nao_faz_rollback(monkeypatch):
    conn, res = _publica(monkeypatch, s.BRAND_SPEC, janela_brand())
    assert (conn.commits, conn.rollbacks) == (1, 0)
    assert res["published"] == 6
    assert res["checks"]["except_both_ways"] == (0, 0)


def test_d04_delete_restrito_a_janela(monkeypatch):
    """Linha ANTERIOR a janela precisa sobreviver intacta."""
    antiga = brand_row(date(2026, 1, 15), "apice", gmv=777)
    rows = janela_brand()
    conn, res = _publica(monkeypatch, s.BRAND_SPEC, rows, target=[antiga])
    assert res["deleted"] == 0
    sobreviventes = [r for r in conn.target if r["date"] == date(2026, 1, 15)]
    assert len(sobreviventes) == 1
    assert sobreviventes[0]["gmv"] == 777, "linha fora da janela foi alterada"
    assert len(conn.target) == 7


def test_d05_delete_apaga_a_janela_antes_de_reinserir(monkeypatch):
    velhas = [brand_row(d, m, gmv=1) for d in (date(2026, 8, 8), date(2026, 8, 9), D1)
              for m in ("apice", "barbours")]
    conn, res = _publica(monkeypatch, s.BRAND_SPEC, janela_brand(), target=velhas)
    assert res["deleted"] == 6
    assert res["published"] == 6
    assert len(conn.target) == 6
    assert all(r["gmv"] == 100 for r in conn.target), "valores antigos persistiram"


def test_d06_remocao_retroativa_e_refletida_no_destino(monkeypatch):
    """A propriedade que `ON CONFLICT DO UPDATE` NAO daria.

    O destino tem 2 marcas no dia; a fonte agora traz 1. Depois da publicacao, o
    destino precisa ter 1 — a marca removida na origem nao pode sobreviver.
    """
    destino = [brand_row(D1, "apice"), brand_row(D1, "barbours")]
    fonte = [brand_row(D1, "apice")]
    conn, res = _publica(monkeypatch, s.BRAND_SPEC, fonte, target=destino)
    assert res["deleted"] == 2 and res["published"] == 1
    assert [r["brand"] for r in conn.target] == ["apice"]


def test_d07_remocao_retroativa_de_criador(monkeypatch):
    destino = [creator_row(D1, "apice", "c1"), creator_row(D1, "apice", "c2")]
    fonte = [creator_row(D1, "apice", "c1")]
    conn, res = _publica(monkeypatch, s.CREATOR_SPEC, fonte, target=destino)
    assert res["deleted"] == 2 and res["published"] == 1
    assert len(conn.target) == 1


def test_d08_falha_faz_rollback_integral_e_nao_commita(monkeypatch):
    antiga = brand_row(date(2026, 1, 15), "apice", gmv=777)
    with pytest.raises(RuntimeError, match="falha injetada"):
        _publica(monkeypatch, s.BRAND_SPEC, janela_brand(), target=[antiga],
                 falhar_no_delete=True)


def test_d09_rollback_preserva_o_destino(monkeypatch):
    monkeypatch.setattr(s, "execute_values", _fake_execute_values)
    antiga = brand_row(date(2026, 1, 15), "apice", gmv=777)
    conn = FakeConn(s.BRAND_SPEC, target=[antiga], falhar_no_delete=True)
    with pytest.raises(RuntimeError):
        s.publish_window(conn, s.BRAND_SPEC, janela_brand(), date(2026, 8, 8), D1, "t")
    assert (conn.commits, conn.rollbacks) == (0, 1)
    assert conn.target == [antiga], "destino mudou apesar do rollback"
    assert conn.staging == [], "staging sobreviveu ao rollback"


def test_d10_divergencia_de_agregado_impede_publicacao(monkeypatch):
    """Se a staging nao reproduzir a fonte, nada e' publicado."""
    monkeypatch.setattr(s, "execute_values", _fake_execute_values)

    def corrompe(cur, sql, batch, page_size=None):
        _fake_execute_values(cur, sql, batch, page_size)
        cur.conn.staging.pop()  # perde uma linha

    monkeypatch.setattr(s, "execute_values", corrompe)
    conn = FakeConn(s.BRAND_SPEC)
    with pytest.raises(RuntimeError, match="staging divergiu"):
        s.publish_window(conn, s.BRAND_SPEC, janela_brand(), date(2026, 8, 8), D1, "t")
    assert conn.commits == 0 and conn.rollbacks == 1
    assert conn.target == []


def test_d11_cursor_e_sempre_fechado(monkeypatch):
    conn, _ = _publica(monkeypatch, s.BRAND_SPEC, janela_brand())
    assert conn.cursores_fechados >= 1


def test_d12_insert_e_delete_nomeiam_somente_o_destino(monkeypatch):
    conn, _ = _publica(monkeypatch, s.BRAND_SPEC, janela_brand())
    escritas = [q for q in conn.sql if q.strip().lower().startswith(("delete", "insert"))]
    assert escritas, "nenhuma escrita registrada"
    for q in escritas:
        alvos = set(re.findall(r"\b(marts\.[a-z_]+|gold\.[a-z_]+|raw\.[a-z_]+)", q.lower()))
        assert alvos <= {"marts.fact_tiktok_brand_content_daily"}, alvos


# ---------------------------------------------------------------------------
# E. Fonte read-only, escrita somente com --apply
# ---------------------------------------------------------------------------

def test_e01_fonte_e_aberta_readonly(monkeypatch):
    capturada = {}

    def fake_connect(url, **kw):
        conn = FakeSource(janela_brand())
        capturada["conn"] = conn
        return conn

    monkeypatch.setattr(s.psycopg2, "connect", fake_connect)
    conn = s._datamart_readonly("postgresql://u:p@h/db")
    assert conn.readonly is True


def test_e02_neon_writable_nao_e_readonly(monkeypatch):
    criadas = []
    monkeypatch.setattr(s.psycopg2, "connect",
                        lambda url, **kw: criadas.append(kw) or FakeConn(s.BRAND_SPEC))
    conn = s._neon_writable("postgresql://u:p@h/db")
    assert conn.readonly is None, "_neon_writable nao deve chamar set_session(readonly=True)"


def test_e03_sem_apply_o_default_e_diagnostico(capsys, monkeypatch):
    chamou = {"apply": 0, "diag": 0}
    monkeypatch.setattr(s, "apply_window", lambda *a, **k: chamou.__setitem__("apply", 1) or 0)
    monkeypatch.setattr(s, "diagnose", lambda *a, **k: chamou.__setitem__("diag", 1) or 0)
    assert s.main(["--table", "brand"]) == 0
    assert chamou == {"apply": 0, "diag": 1}
    assert "MODO DIAGNOSTICO" in capsys.readouterr().out


def test_e04_apply_e_necessario_para_escrever(monkeypatch):
    chamou = {"apply": 0}
    monkeypatch.setattr(s, "apply_window", lambda *a, **k: chamou.__setitem__("apply", 1) or 0)
    monkeypatch.setattr(s, "diagnose", lambda *a, **k: 0)
    s.main(["--table", "brand", "--apply", "--run-id", "x"])
    assert chamou["apply"] == 1


def test_e05_apply_le_e_fecha_a_fonte_antes_de_abrir_a_escrita(monkeypatch):
    ordem = []
    fonte = FakeSource(janela_brand())

    def fake_src(url):
        ordem.append("abre_fonte")
        return fonte

    def fake_close():
        ordem.append("fecha_fonte")

    fonte.close = fake_close

    def fake_neon(url):
        ordem.append("abre_neon")
        return FakeConn(s.BRAND_SPEC)

    monkeypatch.setattr(s, "_datamart_readonly", fake_src)
    monkeypatch.setattr(s, "_neon_writable", fake_neon)
    monkeypatch.setattr(s, "_get_datamart_url", lambda: "x")
    monkeypatch.setattr(s, "_get_neon_url", lambda: "y")
    monkeypatch.setattr(s, "execute_values", _fake_execute_values)
    s.apply_window(s.BRAND_SPEC, date(2026, 8, 8), D1, "run")
    assert ordem == ["abre_fonte", "fecha_fonte", "abre_neon"]


def test_e06_fonte_reprovada_nao_abre_conexao_de_escrita(monkeypatch):
    monkeypatch.setattr(s, "_datamart_readonly", lambda url: FakeSource([brand_row(D1, "a", gmv=None)]))
    monkeypatch.setattr(s, "_get_datamart_url", lambda: "x")
    monkeypatch.setattr(s, "_get_neon_url", lambda: "y")

    def nao_deve_abrir(url):
        raise AssertionError("abriu conexao de escrita com fonte reprovada")

    monkeypatch.setattr(s, "_neon_writable", nao_deve_abrir)
    with pytest.raises(RuntimeError, match="nada foi escrito"):
        s.apply_window(s.BRAND_SPEC, D1, D1, "run")


def test_e07_all_seleciona_as_duas_tabelas():
    p = s.build_parser()
    assert s.selected_specs(p.parse_args([])) == [s.BRAND_SPEC, s.CREATOR_SPEC]
    assert s.selected_specs(p.parse_args(["--table", "creator"])) == [s.CREATOR_SPEC]


def test_e08_urls_exigidas_sem_fallback(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATAMART_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        s._get_neon_url()
    with pytest.raises(RuntimeError, match="DATAMART_DATABASE_URL"):
        s._get_datamart_url()


# ---------------------------------------------------------------------------
# F. Seguranca: sanitizacao, identificadores, zero SELECT *
# ---------------------------------------------------------------------------

def test_f01_dsn_e_credencial_nunca_aparecem_em_erro():
    exc = Exception("could not connect: postgresql://PLACEHOLDER_USER:PLACEHOLDER_SECRET"
                    "@PLACEHOLDER_HOST:5432/db timeout")
    msg = s.sanitize_error_message(exc)
    assert "PLACEHOLDER_SECRET" not in msg
    assert "<redacted>" in msg
    assert len(msg) <= 500


def test_f02_run_id_sanitizado_e_limitado():
    assert s.sanitize_run_id("ok_-:123") == "ok_-:123"
    assert ";" not in s.sanitize_run_id("drop;table")
    assert len(s.sanitize_run_id("x" * 200)) == 64
    assert s.default_run_id(s.BRAND_SPEC, datetime(2026, 8, 11, 9, 0, 0)).startswith("sync_tiktok_brand:")


def test_f03_identificadores_invalidos_falham_alto():
    for ruim in ("Brand", "brand;", "gold.x", "", "1x", "x" * 70):
        with pytest.raises(ValueError):
            s.validate_identifier(ruim)
    for ruim in ("marts", "MARTS.x", "marts.x;drop", "x"):
        with pytest.raises(ValueError):
            s.validate_qualified(ruim)


def test_f04_toda_coluna_das_specs_passa_na_validacao():
    for spec in (s.BRAND_SPEC, s.CREATOR_SPEC):
        for c in spec.business_columns + s.AUDIT_COLUMNS:
            assert s.validate_identifier(c) == c
        assert s.validate_qualified(spec.source_relation)
        assert s.validate_qualified(spec.target_table)


def test_f05_zero_select_estrela_no_codigo():
    src = code_only(Path(s.__file__))
    assert not re.search(r"SELECT\s+\*", src, re.I), "nenhum SELECT * pode existir no codigo"


def test_f06_zero_retry_backoff_agendamento_ou_airflow_no_codigo():
    src = code_only(Path(s.__file__)).lower()
    # substring literal: nao ha palavra legitima que os contenha
    for proibido in ("time.sleep", "backoff", "for attempt", "while true", "dag("):
        assert proibido not in src, f"'{proibido}' nao pode existir no codigo"
    # palavra inteira: `cron` casa dentro de "sincronizar" e `schedule` dentro de
    # "scheduled". A proibicao e' do termo, nao de qualquer palavra que o contenha.
    for proibido in ("retry", "backoff", "schedule", "cron", "crontab", "airflow"):
        assert not re.search(rf"\b{proibido}\b", src), \
            f"'{proibido}' nao pode existir no codigo como termo proprio"


def test_f07_query_da_fonte_lista_colunas_e_limita_janela():
    for spec in (s.BRAND_SPEC, s.CREATOR_SPEC):
        q = s.build_source_query(spec)
        assert q.startswith("SELECT " + spec.business_columns[0])
        for c in spec.business_columns:
            assert re.search(rf"\b{c}\b", q)
        assert "BETWEEN %(date_from)s AND %(date_to)s" in q
        assert spec.source_relation in q


def test_f08_except_bidirecional_ignora_auditoria():
    conn = FakeConn(s.BRAND_SPEC)
    s.except_both_ways(conn, s.BRAND_SPEC, s.BRAND_SPEC.staging_qualified,
                       s.BRAND_SPEC.target_table, D1, D1)
    for q in conn.sql:
        if " EXCEPT " in q:
            assert "synced_at" not in q and "source_run_id" not in q


# ---------------------------------------------------------------------------
# G. Migrations 007 e 008
# ---------------------------------------------------------------------------

def _module_level_nodes(path: Path):
    arvore = ast.parse(path.read_text(encoding="utf-8"))
    return arvore.body


def test_g01_cadeia_006_007_008_linear():
    def ler(path):
        txt = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision = "(\d+)"', txt, re.M).group(1)
        down = re.search(r'^down_revision = "(\d+)"', txt, re.M).group(1)
        return rev, down

    assert ler(MIG_006) == ("006", "005")
    assert ler(MIG_007) == ("007", "006")
    assert ler(MIG_008) == ("008", "007")


def test_g02_nenhum_codigo_executado_no_import():
    """No nivel do modulo so pode haver docstring, imports e atribuicoes simples."""
    for path in (MIG_007, MIG_008):
        for node in _module_level_nodes(path):
            assert isinstance(node, (ast.Expr, ast.Import, ast.ImportFrom,
                                     ast.Assign, ast.FunctionDef)), \
                f"{path.name}: {type(node).__name__} executa no import"
            if isinstance(node, ast.Expr):
                assert isinstance(node.value, ast.Constant), \
                    f"{path.name}: expressao executavel no nivel do modulo"


def test_g03_criacao_fail_fast_sem_if_not_exists():
    for path in (MIG_007, MIG_008):
        codigo = code_only(path)
        assert "CREATE TABLE IF NOT EXISTS" not in codigo, path.name
        assert "CREATE INDEX IF NOT EXISTS" not in codigo, path.name


def test_g04_cada_migration_cria_somente_a_propria_tabela():
    esperado = {
        MIG_007: "marts.fact_tiktok_brand_content_daily",
        MIG_008: "marts.fact_tiktok_creator_daily",
    }
    for path, tabela in esperado.items():
        codigo = code_only(path)
        assert set(re.findall(r"CREATE TABLE\s+([a-z_]+\.[a-z_]+)", codigo)) == {tabela}
        assert "ALTER TABLE" not in codigo
        assert "INSERT INTO" not in codigo, "migration nao faz backfill"
        assert not re.search(r"\bSELECT\b", codigo, re.I), "migration nao le dado algum"
        for padrao in (r"FROM\s+gold\.", r"JOIN\s+gold\.", r"FROM\s+raw\."):
            assert not re.search(padrao, codigo, re.I), "migration nao consulta o Data Mart"


def test_g05_downgrade_restrito_aos_objetos_da_propria_revision():
    dono = {
        MIG_007: ("marts.idx_ftbcd_brand_date", "marts.fact_tiktok_brand_content_daily"),
        MIG_008: ("marts.idx_ftcd_brand_date", "marts.fact_tiktok_creator_daily"),
    }
    for path, objetos in dono.items():
        txt = path.read_text(encoding="utf-8")
        corpo = txt[txt.index("def downgrade"):]
        drops = re.findall(r"DROP (?:INDEX|TABLE) IF EXISTS ([a-z_]+\.[a-z_]+)", corpo)
        assert set(drops) == set(objetos), f"{path.name}: {drops}"
        assert "DROP SCHEMA" not in corpo
        # nao pode derrubar a tabela da outra revision nem a do Gate S1
        assert "fact_ml_gestao_diaria" not in corpo
        for outra in ("fact_tiktok_creator_daily", "fact_tiktok_brand_content_daily"):
            if outra not in objetos[1]:
                assert outra not in corpo, f"{path.name} derruba {outra}"


def test_g06_chave_primaria_bate_com_o_grao_auditado():
    t7 = MIG_007.read_text(encoding="utf-8")
    t8 = MIG_008.read_text(encoding="utf-8")
    assert "PRIMARY KEY (date, brand)" in t7
    assert "PRIMARY KEY (date, brand, creator)" in t8


def test_g07_colunas_da_migration_batem_com_a_spec():
    """A DDL e a spec do sync nao podem divergir: e' o mesmo contrato."""
    casos = ((MIG_007, s.BRAND_SPEC), (MIG_008, s.CREATOR_SPEC))
    for path, spec in casos:
        txt = path.read_text(encoding="utf-8")
        corpo = txt[txt.index("CREATE TABLE"):txt.index("CONSTRAINT pk_")]
        # `[a-z_0-9]+`, com digito: viewers_pct_age_18_24 e as outras 9 colunas de
        # faixa etaria nao casariam com `[a-z_]+`.
        declaradas = set(re.findall(r"^\s{12}([a-z_0-9]+)\s+(?:DATE|VARCHAR|NUMERIC|BIGINT|INTEGER|TIMESTAMPTZ)",
                                    corpo, re.M))
        assert declaradas == set(spec.business_columns) | set(s.AUDIT_COLUMNS), \
            f"{path.name}: sobra/falta {declaradas ^ (set(spec.business_columns) | set(s.AUDIT_COLUMNS))}"


def test_g08_check_de_nan_onde_ha_numeric_e_nunca_em_coluna_assinada():
    t7 = MIG_007.read_text(encoding="utf-8")
    assert t7.count("<> 'NaN'") >= 10
    # total_fees: NaN sim, nao-negatividade nunca
    assert "ck_ftbcd_total_fees    CHECK (total_fees <> 'NaN')" in t7
    assert not re.search(r"total_fees\s+>= 0", t7)
    # total_live_minutes: nenhum CHECK de faixa
    assert not re.search(r"total_live_minutes\s+>= 0", t7)


def test_g09_colunas_opcionais_sao_nullable_na_ddl():
    t7 = MIG_007.read_text(encoding="utf-8")
    corpo = t7[t7.index("CREATE TABLE"):t7.index("CONSTRAINT pk_")]
    for c in s.BRAND_SPEC.optional_columns:
        linha = next(l for l in corpo.splitlines() if re.match(rf"\s{{12}}{c}\s", l))
        assert "NOT NULL" not in linha, f"{c} nao pode ser NOT NULL: a fonte a deixa nula"
    for c in s.CREATOR_SPEC.business_columns:
        pass  # creator_daily nao tem opcional (test_a04)


def test_g10_numeric_sem_escala_para_nao_arredondar():
    """Fixar NUMERIC(18,2) arredondaria e quebraria a igualdade de payload."""
    for path in (MIG_007, MIG_008):
        codigo = code_only(path)
        assert "NUMERIC(" not in codigo, f"{path.name}: NUMERIC com escala arredonda a copia"


def test_g11_indice_coerente_com_os_filtros_dos_consumidores():
    assert "idx_ftbcd_brand_date" in MIG_007.read_text(encoding="utf-8")
    assert "(brand, date)" in MIG_007.read_text(encoding="utf-8")
    assert "idx_ftcd_brand_date" in MIG_008.read_text(encoding="utf-8")
    assert "(brand, date)" in MIG_008.read_text(encoding="utf-8")


def test_g12_auditoria_presente_nas_duas():
    for path in (MIG_007, MIG_008):
        txt = path.read_text(encoding="utf-8")
        assert "synced_at" in txt and "DEFAULT NOW()" in txt
        assert "source_run_id" in txt


# ---------------------------------------------------------------------------
# H. Fronteiras do Gate S2 Task 1/3
# ---------------------------------------------------------------------------

def test_h01_gold_service_continua_apontando_para_gold():
    """A troca de fonte e' a Task 3/3. Aqui `/operacoes` precisa seguir na gold."""
    txt = GOLD_SERVICE.read_text(encoding="utf-8")
    corpo = txt[txt.index("def get_operacoes"):]
    corpo = corpo[:corpo.index("# ---------------------------------------------------------------------------")]
    assert "gold.ml_gestao_diaria" in corpo
    assert "gold.tiktok_creator_daily" in corpo
    assert "gold.tiktok_brand_daily" in corpo
    assert "marts.fact_tiktok_brand_content_daily" not in corpo
    assert "marts.fact_tiktok_creator_daily" not in corpo


def test_h02_o_sync_nao_menciona_gold_service_nem_endpoint():
    codigo = code_only(Path(s.__file__))
    assert "gold_service" not in codigo
    assert "get_operacoes" not in codigo


def test_h03_definicao_de_gmv_intocada_pelo_modulo():
    """O modulo copia; nao escolhe entre total_amount e sub_total + shipping_fee."""
    codigo = code_only(Path(s.__file__))
    for termo in ("total_amount", "shipping_fee", "sub_total", "order_status"):
        assert termo not in codigo, f"'{termo}' indica mudanca de definicao de GMV"


def test_h04_nenhuma_escrita_em_gold_raw_ou_silver():
    codigo = code_only(Path(s.__file__)).lower()
    for verbo in ("insert into gold", "update gold", "delete from gold",
                  "insert into raw", "insert into silver", "truncate"):
        assert verbo not in codigo, f"'{verbo}' nao pode existir"


# ---------------------------------------------------------------------------
# I. Finding 1 — allowlist oficial das cinco marcas
# ---------------------------------------------------------------------------

def test_i01_allowlist_e_a_oficial_do_conector():
    """Nao ha terceira lista: e' a MESMA tupla, verificada por identidade."""
    from pipelines.connectors.tiktok.connector import BRANDS_IN_SCOPE as OFICIAL
    assert s.ALLOWED_BRANDS is OFICIAL
    assert tuple(sorted(s.ALLOWED_BRANDS)) == (
        "apice", "barbours", "kokeshi", "lescent", "rituaria"
    )
    assert len(s.ALLOWED_BRANDS) == 5


def test_i02_modulo_nao_declara_lista_propria_de_marcas():
    """Uma segunda lista literal aqui divergiria da oficial no primeiro cadastro."""
    codigo = code_only(Path(s.__file__))
    for marca in s.ALLOWED_BRANDS:
        assert f'"{marca}"' not in codigo and f"'{marca}'" not in codigo, \
            f"marca {marca!r} literal no codigo: use a allowlist oficial"


def test_i03_as_duas_queries_filtram_marca_de_forma_parametrizada():
    for spec in (s.BRAND_SPEC, s.CREATOR_SPEC):
        q = s.build_source_query(spec)
        assert "brand = ANY(%(brands)s)" in q, f"{spec.name}: filtro de marca ausente"
        assert "BETWEEN %(date_from)s AND %(date_to)s" in q, "janela deixou de ser parametrizada"


def test_i04_nenhum_valor_da_allowlist_e_interpolado_no_sql():
    """O SQL carrega o PLACEHOLDER, nunca o valor. Interpolar funcionaria hoje e
    viraria injecao no dia em que a lista vier de fora do codigo."""
    for spec in (s.BRAND_SPEC, s.CREATOR_SPEC):
        q = s.build_source_query(spec)
        for marca in s.ALLOWED_BRANDS:
            assert marca not in q, f"{marca!r} interpolada no texto do SQL"
        assert "IN (" not in q, "lista textual de marcas em vez de parametro"


def test_i05_parametros_levam_exatamente_a_allowlist():
    p = s.source_params(date(2026, 8, 4), D1)
    assert set(p) == {"date_from", "date_to", "brands"}
    assert p["brands"] == list(s.ALLOWED_BRANDS)
    assert len(p["brands"]) == 5
    assert p["date_from"] == date(2026, 8, 4) and p["date_to"] == D1


def test_i06_fetch_source_rows_envia_o_parametro_de_marca():
    fonte = FakeSource(janela_brand())
    cur = fonte.cursor()
    s.fetch_source_rows(fonte, s.BRAND_SPEC, date(2026, 8, 8), D1)
    consulta = [q for q in fonte.sql if "SELECT" in q][0]
    assert "brand = ANY(%(brands)s)" in consulta


def test_i07_as_cinco_marcas_continuam_aceitas():
    rows = [brand_row(D1, m) for m in s.ALLOWED_BRANDS]
    assert s.foreign_brands_in_rows(rows) == set()
    assert s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1) == []
    agg = s.aggregates_from_rows(s.BRAND_SPEC, rows)
    assert agg["distinct_brands"] == 5


def test_i08_marca_externa_reprova_a_fotografia():
    rows = [brand_row(D1, "apice"), brand_row(D1, "marca_sem_consumidor")]
    assert s.foreign_brands_in_rows(rows) == {"marca_sem_consumidor"}
    problemas = s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1)
    assert any("fora da allowlist" in p for p in problemas)


def test_i09_mensagem_de_marca_externa_nao_nomeia_a_marca():
    """Diagnostico precisa da quantidade, nao da identidade das excluidas."""
    rows = [brand_row(D1, "marca_secreta_x"), brand_row(D1, "marca_secreta_y")]
    problemas = s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1)
    texto = " ".join(problemas)
    assert "marca_secreta_x" not in texto and "marca_secreta_y" not in texto
    assert "2 marca(s) fora da allowlist" in texto


def test_i10_marca_externa_nunca_chega_ao_destino(monkeypatch):
    """`apply_window` recusa ANTES de abrir conexao de escrita."""
    monkeypatch.setattr(s, "_datamart_readonly",
                        lambda url: FakeSource([brand_row(D1, "apice"),
                                                brand_row(D1, "externa")]))
    monkeypatch.setattr(s, "_get_datamart_url", lambda: "x")
    monkeypatch.setattr(s, "_get_neon_url", lambda: "y")

    def nao_deve_abrir(url):
        raise AssertionError("marca externa chegou a conexao de escrita")

    monkeypatch.setattr(s, "_neon_writable", nao_deve_abrir)
    with pytest.raises(RuntimeError, match="nada foi escrito"):
        s.apply_window(s.BRAND_SPEC, D1, D1, "run")


def test_i11_marca_externa_reprovada_tambem_na_fato_de_criador(monkeypatch):
    rows = [creator_row(D1, "apice", "c1"), creator_row(D1, "externa", "c2")]
    assert any("fora da allowlist"
               in p for p in s.validate_source_rows(s.CREATOR_SPEC, rows, D1, D1))


def test_i12_migrations_e_docstring_falam_de_cinco_marcas():
    for path in (MIG_007, MIG_008, Path(s.__file__)):
        txt = path.read_text(encoding="utf-8")
        assert "7 marcas" not in txt and "sete marcas" not in txt, path.name
        assert "1.944" not in txt and "197.448" not in txt, f"{path.name}: contagem das 7 marcas"


# ---------------------------------------------------------------------------
# J. Finding 2 — reconciliacao exata em Decimal
# ---------------------------------------------------------------------------

def test_j01_dec_preserva_decimal_recebido_do_psycopg2():
    d = Decimal("12345.67891")
    assert s._dec(d) is d


def test_j02_dec_converte_inteiro_exatamente():
    assert s._dec(197448) == Decimal(197448)
    assert isinstance(s._dec(197448), Decimal)


def test_j03_dec_trata_nulo_como_zero_apenas_na_soma():
    assert s._dec(None) == Decimal("0")
    # e o nulo continua sendo nulo para as validacoes que dependem disso
    rows = [brand_row(D1, "apice", gmv=None)]
    assert s.missing_required(s.BRAND_SPEC, rows)["gmv"] == 1


def test_j04_dec_nunca_usa_float_como_intermediario():
    """`Decimal(str(x))` preserva a representacao decimal; `Decimal(float)` nao."""
    assert s._dec(0.1) == Decimal("0.1")
    assert s._dec(0.1) != Decimal(0.1)  # Decimal(float) traz o erro binario


def test_j05_dec_recusa_booleano():
    with pytest.raises(TypeError):
        s._dec(True)


def test_j06_agregados_nao_sao_arredondados():
    rows = [brand_row(D1, "apice", gmv=Decimal("0.123456789"))]
    agg = s.aggregates_from_rows(s.BRAND_SPEC, rows)
    assert agg["sum_gmv"] == Decimal("0.123456789"), "arredondou antes de comparar"


def test_j07_alta_cardinalidade_e_soma_exata():
    """197.448 ocorrencias de 12345.67891 — o volume real da fato de criador."""
    n = 197_448
    valor = Decimal("12345.67891")
    rows = [{"date": D1, "brand": "apice", "creator": f"c{i}",
             "gmv_total": valor, "gmv_video": Decimal("0"), "gmv_live": Decimal("0"),
             "views_video": Decimal("0"), "videos_count": 0, "lives_count": 0}
            for i in range(n)]
    agg = s.aggregates_from_rows(s.CREATOR_SPEC, rows)
    esperado = valor * n
    assert agg["sum_gmv_total"] == esperado
    # literal conferido: 12345.67891 * 197448 = 2437629609.42168, exato em Decimal
    assert agg["sum_gmv_total"] == Decimal("2437629609.42168")


def test_j08_implementacao_antiga_com_float_daria_resultado_diferente():
    """Contraprova adversarial do Finding 2.

    Mesma entrada, duas aritmeticas: `float` acumula erro de representacao e
    divergiria do `SUM(NUMERIC)` exato do Postgres.
    """
    n = 197_448
    valor = Decimal("12345.67891")
    exato = valor * n

    # implementacao antiga: float + round(.., 4)
    antigo = round(sum(float(valor) for _ in range(n)), 4)
    assert Decimal(str(antigo)) != exato, "o caso precisa ser adversarial de fato"

    # e a nova bate exatamente
    novo = sum((s._dec(valor) for _ in range(n)), Decimal("0"))
    assert novo == exato
    # a diferenca do antigo e' real, ainda que pequena: e' exatamente o tipo de
    # desvio que uma tolerancia silenciosa esconderia
    assert abs(Decimal(str(antigo)) - exato) > 0


def test_j09_fracionarios_pequenos_nao_perdem_precisao():
    n = 10_000
    rows = [{"date": D1, "brand": "apice", "creator": f"c{i}",
             "gmv_total": Decimal("0.01"), "gmv_video": Decimal("0.001"),
             "gmv_live": Decimal("0.0001"), "views_video": Decimal("0"),
             "videos_count": 0, "lives_count": 0} for i in range(n)]
    agg = s.aggregates_from_rows(s.CREATOR_SPEC, rows)
    assert agg["sum_gmv_total"] == Decimal("100.00")
    assert agg["sum_gmv_video"] == Decimal("10.000")
    assert agg["sum_gmv_live"] == Decimal("1.0000")
    # o mesmo em float erraria
    assert sum(0.1 for _ in range(10)) != 1.0


def test_j10_mistura_de_decimal_inteiro_e_nulo():
    rows = [
        brand_row(D1, "apice", gmv=Decimal("1.05"), visitors=None, customers=3),
        brand_row(date(2026, 8, 9), "barbours", gmv=2, visitors=7, customers=None),
    ]
    agg = s.aggregates_from_rows(s.BRAND_SPEC, rows)
    assert agg["sum_gmv"] == Decimal("3.05")
    assert agg["sum_visitors"] == Decimal("7")
    assert agg["sum_customers"] == Decimal("3")


def test_j11_agregados_das_duas_origens_sao_do_mesmo_tipo(monkeypatch):
    """`aggregates_from_rows` e `aggregates_from_table` precisam ser comparaveis."""
    monkeypatch.setattr(s, "execute_values", _fake_execute_values)
    rows = janela_brand()
    conn = FakeConn(s.BRAND_SPEC, target=rows)
    do_banco = s.aggregates_from_table(conn, s.BRAND_SPEC, s.BRAND_SPEC.target_table,
                                       date(2026, 8, 8), D1)
    da_memoria = s.aggregates_from_rows(s.BRAND_SPEC, rows)
    for c in s.BRAND_SPEC.additive_columns:
        k = f"sum_{c}"
        assert isinstance(do_banco[k], Decimal), f"{k} do banco nao e' Decimal"
        assert isinstance(da_memoria[k], Decimal), f"{k} da memoria nao e' Decimal"
    assert s.compare_aggregates(s.BRAND_SPEC, da_memoria, do_banco) == []


def test_j12_compare_aggregates_pega_diferenca_de_um_centesimo():
    """Sem tolerancia monetaria silenciosa."""
    a = {"count": 1, "min_date": D1, "max_date": D1, "distinct_dates": 1,
         "distinct_brands": 1}
    b = dict(a)
    for c in s.CREATOR_SPEC.additive_columns:
        a[f"sum_{c}"] = Decimal("100.00")
        b[f"sum_{c}"] = Decimal("100.00")
    b["sum_gmv_total"] = Decimal("100.01")
    problemas = s.compare_aggregates(s.CREATOR_SPEC, a, b)
    assert len(problemas) == 1
    assert "sum_gmv_total" in problemas[0]


def test_j13_escala_diferente_com_mesmo_valor_nao_e_divergencia():
    """`Decimal("100") == Decimal("100.00")` — comparacao e' de VALOR."""
    a = {"count": 0, "min_date": None, "max_date": None, "distinct_dates": 0,
         "distinct_brands": 0}
    b = dict(a)
    for c in s.CREATOR_SPEC.additive_columns:
        a[f"sum_{c}"] = Decimal("100")
        b[f"sum_{c}"] = Decimal("100.0000")
    assert s.compare_aggregates(s.CREATOR_SPEC, a, b) == []


def test_j14_print_agg_nao_altera_o_valor_validado(capsys):
    rows = [brand_row(D1, "apice", gmv=Decimal("0.123456789"))]
    agg = s.aggregates_from_rows(s.BRAND_SPEC, rows)
    antes = agg["sum_gmv"]
    s._print_agg("teste:", s.BRAND_SPEC, agg)
    capsys.readouterr()
    assert agg["sum_gmv"] == antes
    assert agg["sum_gmv"] is antes


def test_j15_print_agg_nao_usa_notacao_cientifica(capsys):
    rows = [brand_row(D1, "apice", gmv=Decimal("1E+9"))]
    s._print_agg("teste:", s.BRAND_SPEC, s.aggregates_from_rows(s.BRAND_SPEC, rows))
    saida = capsys.readouterr().out
    assert "sum_gmv = 1000000000" in saida
    assert "E+" not in saida


def test_j16_nan_continua_recusado_sem_passar_por_float():
    for valor in (Decimal("NaN"), float("nan")):
        rows = [brand_row(D1, "apice", gmv=valor)]
        assert s.nan_in_rows(s.BRAND_SPEC, rows) == {"gmv": 1}
        assert any("NaN" in p for p in s.validate_source_rows(s.BRAND_SPEC, rows, D1, D1))


def test_j17_razoes_continuam_fora_da_soma_e_contadas():
    rows = [brand_row(D1, "apice", viewers_pct_female=Decimal("50.5")),
            brand_row(date(2026, 8, 9), "apice", viewers_pct_female=None)]
    agg = s.aggregates_from_rows(s.BRAND_SPEC, rows)
    assert "sum_viewers_pct_female" not in agg
    assert agg["nn_viewers_pct_female"] == 1


def test_j18_colunas_assinadas_somam_negativo_exato():
    """`total_live_minutes` negativo da origem e' copiado e somado como esta."""
    rows = [brand_row(D1, "apice", total_live_minutes=-29_545_461,
                      total_fees=Decimal("-266342.00"))]
    agg = s.aggregates_from_rows(s.BRAND_SPEC, rows)
    assert agg["sum_total_live_minutes"] == Decimal("-29545461")
    assert agg["sum_total_fees"] == Decimal("-266342.00")
    assert s.negatives_in_rows(s.BRAND_SPEC, rows) == {}


def test_j19_zero_float_nas_metricas_do_codigo():
    codigo = code_only(Path(s.__file__))
    assert "float(" not in codigo, "nenhuma conversao para float no codigo"
    assert "_num(" not in codigo, "_num foi substituida por _dec"


def test_h05_modulo_do_gate_s1_nao_foi_alterado():
    """Reuso de padrao, nao de codigo: a 006 e o sync do S1 seguem intactos."""
    s1 = REPO / "pipelines" / "sync_ml_gestao_diaria.py"
    assert s1.exists()
    codigo = code_only(s1)
    assert "fact_ml_gestao_diaria" in codigo
    assert "tiktok" not in codigo.lower(), "o modulo do S1 nao deve saber do S2"
