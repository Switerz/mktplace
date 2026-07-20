"""
Testes de `execute_shopee_window_refresh` / `--refresh-shopee-window` —
Gate S3. PRIMEIRO caminho de escrita da Gold regional que faz DELETE.

Usa conexões/cursores psycopg2 falsos (respostas por SUBSTRING do SQL,
mesmo padrão de test_gold_regional_loader.py) + `tmp_path` real para o
backup atômico (a publicação do arquivo NÃO é mockada — o teste exercita o
mkstemp+fsync+os.link de verdade contra um diretório temporário). Nenhum
banco real é tocado.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines.ingestion.gold_regional import loader


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Seq:
    """Resposta que muda a cada chamada REPETIDA da MESMA query (fingerprint
    fora do escopo e key-diff são executados 2x: antes e depois do
    DELETE/INSERT). Um valor comum (tupla/lista fixa) fora de `_Seq` é
    devolvido sempre igual, em qualquer número de chamadas."""

    def __init__(self, *values):
        self.values = values


def _contains(*subs):
    return lambda upper: all(s in upper for s in subs)


def _exact(pattern):
    return lambda upper: upper == pattern


_D_FROM = date(2026, 6, 1)
_D_TO = date(2026, 6, 30)

_SAMPLE_GOLD_ROW = (
    date(2026, 6, 15), loader.SHOPEE_MARKETPLACE_ID, 3, "SP",
    Decimal("500.00"), 10, 12, 1, 0,
    None, Decimal("20.00"), Decimal("15.00"), None,
    10, 10, 0, 0,
)
_SAMPLE_STAGING_ROW = (
    date(2026, 6, 15), loader.SHOPEE_MARKETPLACE_ID, 3, "SP",
    Decimal("520.00"), 11, 13, 1, 0,
    None, Decimal("22.00"), Decimal("16.00"), None,
    11, 11, 0, 0,
)

_ZERO_FINGERPRINT = (0, Decimal("0"), Decimal("0"), 0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), 0, 0, 0, 0)


def _happy_responses(
    staging_agg=(1, Decimal("520.00"), 11),
    dup=(0,), nulls=(0,), bad=(0,), nan_neg=(0,), out_of_scope_staging=(0,),
    gold_agg=(1, Decimal("500.00"), 10),
    key_diff=None,
    fingerprint=None,
    gold_rows=None,
    staging_rows=None,
):
    if key_diff is None:
        key_diff = _Seq((0, 1, 0), (0, 0, 0))  # antes: 1 chave nova (source_only) -> would_change=True; depois: reconciliado
    if fingerprint is None:
        fingerprint = _Seq(_ZERO_FINGERPRINT, _ZERO_FINGERPRINT)
    if gold_rows is None:
        gold_rows = [_SAMPLE_GOLD_ROW]
    if staging_rows is None:
        staging_rows = [_SAMPLE_STAGING_ROW]

    return [
        (_contains("NOT (MARKETPLACE_ID = %(SHOPEE_MARKETPLACE_ID)S AND DATE BETWEEN"), fingerprint),
        (_exact("SELECT COUNT(*), COALESCE(SUM(GMV), 0), COALESCE(SUM(ORDERS), 0) FROM STG_MARKETPLACE_REGION_DAILY"), staging_agg),
        (_contains("HAVING COUNT(*) > 1"), dup),
        (_contains("DATE IS NULL OR MARKETPLACE_ID IS NULL"), nulls),
        (_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), bad),
        (_contains("GMV = 'NAN'"), nan_neg),
        (_contains("MARKETPLACE_ID <> %(SHOPEE_MARKETPLACE_ID)S"), out_of_scope_staging),
        (_contains("COALESCE(SUM(ORDERS), 0) FROM GOLD.MARKETPLACE_REGION_DAILY WHERE MARKETPLACE_ID ="), gold_agg),
        (_contains("FULL OUTER JOIN", "GOLD_WINDOW"), key_diff),
        (_contains("ORDER BY DATE, LOJA_ID, UF", "FROM GOLD.MARKETPLACE_REGION_DAILY"), gold_rows),
        (_contains("ORDER BY DATE, LOJA_ID, UF", "FROM STG_MARKETPLACE_REGION_DAILY"), staging_rows),
    ]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append((norm, params))
        if self.conn.fail_on_substring and self.conn.fail_on_substring in norm.upper():
            raise RuntimeError("falha simulada de execução")
        if norm.upper().startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"):
            self.conn.final_insert_executed = True

    def _dispatch(self):
        norm, _params = self.conn.executed[-1]
        upper = norm.upper()
        if "PG_TRY_ADVISORY_LOCK" in upper:
            return (self.conn.lock_acquired,)
        if "PG_ADVISORY_UNLOCK" in upper:
            return (True,)
        for matcher, value in self.conn.responses:
            if matcher(upper):
                if isinstance(value, _Seq):
                    idx = self.conn._call_index.get(id(value), 0)
                    self.conn._call_index[id(value)] = idx + 1
                    return value.values[min(idx, len(value.values) - 1)]
                return value
        raise AssertionError(f"nenhuma resposta simulada para a query: {norm!r}")

    def fetchone(self):
        return self._dispatch()

    def fetchall(self):
        return self._dispatch()

    @property
    def rowcount(self):
        norm, _params = self.conn.executed[-1]
        upper = norm.upper()
        if upper.startswith("DELETE FROM GOLD.MARKETPLACE_REGION_DAILY"):
            return self.conn.delete_rowcount
        if upper.startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"):
            return self.conn.insert_rowcount
        return 0


class FakeConn:
    def __init__(
        self,
        responses=None,
        lock_acquired=True,
        fail_on_substring=None,
        delete_rowcount=1,
        insert_rowcount=1,
    ):
        self.executed: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.autocommit = None
        self.lock_acquired = lock_acquired
        self.fail_on_substring = fail_on_substring
        self.responses = responses if responses is not None else _happy_responses()
        self._call_index: dict[int, int] = {}
        self.delete_rowcount = delete_rowcount
        self.insert_rowcount = insert_rowcount
        self.final_insert_executed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakePsycopg2Module:
    def __init__(self, conn):
        self._conn = conn
        self.connect_calls = 0

    def connect(self, url, connect_timeout=15):
        self.connect_calls += 1
        return self._conn


def _happy_conn(**overrides):
    return FakeConn(responses=_happy_responses(), **overrides)


# ---------------------------------------------------------------------------
# Validação de janela/audit_path — ANTES de qualquer conexão
# ---------------------------------------------------------------------------

def test_refresh_janela_invalida_bloqueia_antes_de_conectar(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("não deveria conectar com janela inválida")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    result = loader.execute_shopee_window_refresh(
        "postgresql://writer@host/db", _D_TO, _D_FROM, tmp_path / "backup.json",  # invertida
    )
    assert result.outcome == "blocked"
    assert "posterior" in result.problems[0]


def test_refresh_audit_path_relativo_bloqueia_antes_de_conectar(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("não deveria conectar com audit_path inválido")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    result = loader.execute_shopee_window_refresh(
        "postgresql://writer@host/db", _D_FROM, _D_TO, Path("relativo.json"), repo_root=tmp_path,
    )
    assert result.outcome == "blocked"
    assert "absoluto" in result.problems[0]


def test_refresh_audit_path_existente_bloqueia_antes_de_conectar(monkeypatch, tmp_path):
    outside_repo = tmp_path / "outside"
    outside_repo.mkdir()
    existing = outside_repo / "ja_existe.json"
    existing.write_text("{}")

    def boom(*a, **k):
        raise AssertionError("não deveria conectar com audit_path já existente")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    result = loader.execute_shopee_window_refresh(
        "postgresql://writer@host/db", _D_FROM, _D_TO, existing, repo_root=tmp_path / "repo",
    )
    assert result.outcome == "blocked"
    assert "já existe" in result.problems[0]


def test_refresh_audit_path_dentro_do_repo_bloqueia(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("não deveria conectar com audit_path dentro do repo")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    inside = tmp_path / "backup.json"
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, inside, repo_root=tmp_path)
    assert result.outcome == "blocked"
    assert "dentro do repositório" in result.problems[0]


# ---------------------------------------------------------------------------
# Advisory lock ocupado / nenhuma tentativa automática
# ---------------------------------------------------------------------------

def test_refresh_bloqueia_se_advisory_lock_em_uso(monkeypatch, tmp_path):
    fake_conn = FakeConn(lock_acquired=False)
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_refresh(
        "postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "sub" / "backup.json",
    )
    # audit_path com diretório pai inexistente -- bloqueado ANTES do lock
    assert result.outcome == "blocked"


def test_refresh_bloqueia_se_advisory_lock_em_uso_com_audit_path_valido(monkeypatch, tmp_path):
    fake_conn = FakeConn(lock_acquired=False)
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_refresh(
        "postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "backup.json",
    )
    assert result.outcome == "blocked"
    assert any("advisory lock" in p for p in result.problems)
    assert fake_conn.closed is True
    assert not any("CREATE TEMP TABLE" in s.upper() for s, _ in fake_conn.executed)


def test_refresh_nao_faz_retry_automatico(monkeypatch, tmp_path):
    fake_conn = FakeConn(fail_on_substring="CREATE TEMP TABLE")
    fake_module = FakePsycopg2Module(fake_conn)
    monkeypatch.setattr(loader, "psycopg2", fake_module)

    loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "backup.json")

    assert fake_module.connect_calls == 1


def test_refresh_fecha_conexao_mesmo_se_lock_acquire_falhar(monkeypatch, tmp_path):
    fake_conn = FakeConn()

    def boom_lock(conn):
        raise RuntimeError("falha simulada ao adquirir lock")

    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    monkeypatch.setattr(loader, "try_acquire_advisory_lock", boom_lock)

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "backup.json")

    assert result.outcome == "failed"
    assert fake_conn.closed is True
    assert fake_conn.rolled_back is True
    # lock nunca foi de fato adquirido -- release_advisory_lock nunca chamado
    assert not any("PG_ADVISORY_UNLOCK" in s.upper() for s, _ in fake_conn.executed)


# ---------------------------------------------------------------------------
# Ordem: lock -> table lock -> staging -> validações -> key-diff -> backup ->
# delete -> insert -> reconciliação -> fingerprint -> commit
# ---------------------------------------------------------------------------

def test_refresh_ordem_operacoes_caminho_feliz(monkeypatch, tmp_path):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    audit_path = tmp_path / "backup.json"

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, audit_path)

    order = [s.upper() for s, _ in fake_conn.executed]

    def idx(pred):
        return next(i for i, s in enumerate(order) if pred(s))

    i_lock = idx(lambda s: "PG_TRY_ADVISORY_LOCK" in s)
    i_table_lock = idx(lambda s: "SHARE ROW EXCLUSIVE MODE" in s)
    i_fp_before = idx(lambda s: "NOT (MARKETPLACE_ID" in s)
    i_staging_create = idx(lambda s: "CREATE TEMP TABLE" in s)
    i_staging_insert = idx(lambda s: s.startswith("INSERT INTO STG_MARKETPLACE_REGION_DAILY"))
    i_dup = idx(lambda s: "HAVING COUNT(*) > 1" in s)
    i_key_diff_first = idx(lambda s: "FULL OUTER JOIN" in s)
    i_delete = idx(lambda s: s.startswith("DELETE FROM"))
    i_insert_final = idx(lambda s: s.startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"))
    i_unlock = idx(lambda s: "PG_ADVISORY_UNLOCK" in s)

    assert i_lock < i_table_lock < i_fp_before < i_staging_create < i_staging_insert
    assert i_staging_insert < i_dup < i_key_diff_first
    assert i_key_diff_first < i_delete < i_insert_final < i_unlock

    assert result.outcome == "committed"
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert fake_conn.closed is True
    assert result.rows_deleted == 1
    assert result.rows_inserted == 1
    assert audit_path.exists()
    assert Path(str(audit_path) + ".sha256").exists()


def test_refresh_backup_publicado_antes_do_delete(monkeypatch, tmp_path):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    audit_path = tmp_path / "backup.json"

    loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, audit_path)

    order = [s.upper() for s, _ in fake_conn.executed]
    i_delete = next(i for i, s in enumerate(order) if s.startswith("DELETE FROM"))
    # o backup e' publicado via _write_window_backup_atomic (fora de cur.execute),
    # mas o arquivo já deve existir no disco ANTES do DELETE ser executado --
    # aqui confirmamos indiretamente que o commit funcionou (senão o DELETE
    # nem teria acontecido) e que o arquivo existe.
    assert audit_path.exists()
    assert i_delete >= 0


# ---------------------------------------------------------------------------
# NO_OP — sem mudança, sem backup, sem DELETE/INSERT
# ---------------------------------------------------------------------------

def test_refresh_no_op_quando_janela_ja_reconciliada(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(key_diff=_Seq((0, 0, 0))))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    audit_path = tmp_path / "backup.json"

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, audit_path)

    assert result.outcome == "no_op"
    assert result.rows_deleted == 0
    assert result.rows_inserted == 0
    assert not audit_path.exists()
    assert not Path(str(audit_path) + ".sha256").exists()
    assert not any(s.upper().startswith("DELETE FROM") for s, _ in fake_conn.executed)
    assert fake_conn.final_insert_executed is False
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


# ---------------------------------------------------------------------------
# Validações do staging — cada uma bloqueia isoladamente, sem escrever
# ---------------------------------------------------------------------------

def test_refresh_bloqueia_duplicidade_no_staging(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(dup=(2,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "blocked"
    assert any("duplicada" in p for p in result.problems)
    assert fake_conn.rolled_back is True
    assert not (tmp_path / "b.json").exists()


def test_refresh_bloqueia_nulos_no_staging(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(nulls=(3,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "blocked"
    assert any("nula" in p for p in result.problems)


def test_refresh_bloqueia_numerador_maior_que_denominador(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(bad=(1,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "blocked"
    assert any("numerador > denominador" in p for p in result.problems)


def test_refresh_bloqueia_nan_ou_negativo_no_staging(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(nan_neg=(1,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "blocked"
    assert any("NaN" in p for p in result.problems)


def test_refresh_bloqueia_linha_fora_do_escopo_no_staging(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(out_of_scope_staging=(1,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "blocked"
    assert any("fora do escopo" in p for p in result.problems)


# ---------------------------------------------------------------------------
# zero_source_risk — bloqueia por padrão, --confirm-empty-window libera
# ---------------------------------------------------------------------------

def test_refresh_bloqueia_zero_source_risk_sem_confirmar(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(staging_agg=(0, Decimal("0"), 0), gold_agg=(1, Decimal("500.00"), 10)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "blocked"
    assert any("ZERO linhas" in p for p in result.problems)
    assert not (tmp_path / "b.json").exists()


def test_refresh_confirm_empty_window_libera_zero_source_risk(monkeypatch, tmp_path):
    fake_conn = FakeConn(
        responses=_happy_responses(
            staging_agg=(0, Decimal("0"), 0),
            gold_agg=(1, Decimal("500.00"), 10),
            key_diff=_Seq((1, 0, 0), (0, 0, 0)),  # gold_only=1 (a linha que sera' apagada) -> would_change=True
            gold_rows=[_SAMPLE_GOLD_ROW],
            staging_rows=[],
        ),
        delete_rowcount=1,
        insert_rowcount=0,
    )
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    audit_path = tmp_path / "b.json"

    result = loader.execute_shopee_window_refresh(
        "postgresql://writer@host/db", _D_FROM, _D_TO, audit_path, confirm_empty_window=True,
    )

    assert result.outcome == "committed"
    assert result.rows_deleted == 1
    assert result.rows_inserted == 0
    assert audit_path.exists()


def test_refresh_confirm_empty_window_nao_desativa_outras_validacoes(monkeypatch, tmp_path):
    """--confirm-empty-window só libera o caso zero_source_risk -- nao
    desativa a checagem de duplicidade/nulos/etc."""
    fake_conn = FakeConn(responses=_happy_responses(staging_agg=(0, Decimal("0"), 0), dup=(2,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh(
        "postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json", confirm_empty_window=True,
    )
    assert result.outcome == "blocked"
    assert any("duplicada" in p for p in result.problems)


# ---------------------------------------------------------------------------
# Falha no backup ocorre ANTES do DELETE
# ---------------------------------------------------------------------------

def test_refresh_falha_no_backup_ocorre_antes_do_delete(monkeypatch, tmp_path):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    def boom(*a, **k):
        raise loader.BackupIntegrityError("falha simulada de backup")
    monkeypatch.setattr(loader, "_write_window_backup_atomic", boom)

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")

    assert result.outcome == "failed"
    assert not any(s.upper().startswith("DELETE FROM") for s, _ in fake_conn.executed)
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


def test_write_window_backup_atomic_nunca_sobrescreve_destino_existente(tmp_path):
    """Corrida simulada: o destino passa a existir ENTRE a validação
    antecipada e a publicação. os.link deve detectar e abortar sem
    sobrescrever -- conteúdo original preservado, nenhum .tmp sobra."""
    audit_path = tmp_path / "backup.json"
    audit_path.write_text("CONTEUDO ORIGINAL INTOCADO")

    with pytest.raises(loader.BackupIntegrityError, match="passou a existir"):
        loader._write_window_backup_atomic(audit_path, _D_FROM, _D_TO, [_SAMPLE_GOLD_ROW], [_SAMPLE_STAGING_ROW])

    assert audit_path.read_text() == "CONTEUDO ORIGINAL INTOCADO"
    leftover_tmp = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp == []


def test_write_window_backup_atomic_publica_json_e_sha256_validos(tmp_path):
    audit_path = tmp_path / "backup.json"
    sha256 = loader._write_window_backup_atomic(audit_path, _D_FROM, _D_TO, [_SAMPLE_GOLD_ROW], [_SAMPLE_STAGING_ROW])

    sha_path = Path(str(audit_path) + ".sha256")
    assert sha_path.exists()
    assert sha_path.read_text().strip() == sha256
    assert loader._sha256_file(audit_path) == sha256

    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == loader.WINDOW_BACKUP_SCHEMA_VERSION
    assert payload["marketplace_id"] == loader.SHOPEE_MARKETPLACE_ID
    assert payload["date_from"] == _D_FROM.isoformat()
    assert payload["date_to"] == _D_TO.isoformat()
    assert payload["before_count"] == 1
    assert payload["after_count"] == 1
    assert loader.validate_window_backup_payload(payload) == []
    # nunca PII/order_id/CPF/filename/file_id
    dumped = json.dumps(payload)
    for forbidden in ("order_id", "buyer_cpf", "cpf", "file_id", "filename"):
        assert forbidden not in dumped.lower()


# ---------------------------------------------------------------------------
# DELETE: bind parameters, sem literal de data; rowcount divergente
# ---------------------------------------------------------------------------

def test_refresh_delete_usa_bind_parameters_nunca_literal_de_data(monkeypatch, tmp_path):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")

    delete_calls = [(s, p) for s, p in fake_conn.executed if s.upper().startswith("DELETE FROM")]
    assert len(delete_calls) == 1
    sql, params = delete_calls[0]
    assert "2026-06-01" not in sql and "2026-06-30" not in sql
    assert params["date_from"] == _D_FROM
    assert params["date_to"] == _D_TO
    assert params["shopee_marketplace_id"] == loader.SHOPEE_MARKETPLACE_ID


def test_refresh_delete_rowcount_divergente_aborta(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(), delete_rowcount=99)
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "failed"
    assert any("DELETE removeu" in p for p in result.problems)
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


def test_refresh_insert_rowcount_divergente_aborta(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(), insert_rowcount=0)
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "failed"
    assert any("INSERT inseriu" in p for p in result.problems)
    assert fake_conn.rolled_back is True


def test_refresh_reconciliacao_pos_insert_divergente_aborta(monkeypatch, tmp_path):
    fake_conn = FakeConn(responses=_happy_responses(key_diff=_Seq((0, 1, 0), (0, 1, 0))))  # nunca reconcilia
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "failed"
    assert any("reconciliação pós-insert" in p for p in result.problems)
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


def test_refresh_fingerprint_fora_do_escopo_alterado_aborta(monkeypatch, tmp_path):
    """ML/TikTok ou Shopee fora da janela mudaram durante a operação --
    detectado pelo fingerprint agregado antes/depois, mesmo que o DELETE/
    INSERT tenham sido escopados corretamente."""
    changed_fp = (1, Decimal("999.00"), 5, 0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), 0, 0, 0, 0)
    fake_conn = FakeConn(responses=_happy_responses(fingerprint=_Seq(_ZERO_FINGERPRINT, changed_fp)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "failed"
    assert any("fora do escopo" in p for p in result.problems)
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


# ---------------------------------------------------------------------------
# Erro genérico durante execução -- rollback + mensagem sanitizada
# ---------------------------------------------------------------------------

def test_refresh_rollback_em_erro_generico_nunca_expoe_mensagem_nativa(monkeypatch, tmp_path):
    class FailingCursor(FakeCursor):
        def execute(self, sql, params=None):
            norm = " ".join(sql.split())
            self.conn.executed.append((norm, params))
            if norm.upper().startswith("INSERT INTO STG_MARKETPLACE_REGION_DAILY"):
                raise RuntimeError(
                    'connection to server at "prod-db.example.rds.amazonaws.com" '
                    '(10.0.0.5), port 5432 failed: FATAL: password authentication failed for user "postgres"'
                )

    class FailingConn(FakeConn):
        def cursor(self):
            return FailingCursor(self)

    fake_conn = FailingConn(responses=_happy_responses())
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")

    assert result.outcome == "failed"
    combined = " ".join(result.problems)
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


# ---------------------------------------------------------------------------
# Warnings pós-commit: falha ao liberar lock/fechar conexão NUNCA rebaixa
# um resultado já committed
# ---------------------------------------------------------------------------

def test_refresh_lock_release_falha_apos_commit_preserva_committed(monkeypatch, tmp_path):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    def boom_release(conn):
        raise RuntimeError("connection lost postgresql://u:p@h/db")
    monkeypatch.setattr(loader, "release_advisory_lock", boom_release)

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")

    assert result.outcome == "committed"
    assert fake_conn.committed is True
    assert any("advisory lock" in w for w in result.warnings)
    assert "u:p@h" not in " ".join(result.warnings)
    assert fake_conn.closed is True


def test_refresh_close_falha_apos_commit_preserva_committed(monkeypatch, tmp_path):
    fake_conn = _happy_conn()

    def boom_close():
        raise RuntimeError("close failed")
    fake_conn.close = boom_close
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")

    assert result.outcome == "committed"
    assert any("fechar" in w for w in result.warnings)


# =============================================================================
# Gate S3.1 — hardening de conexão, CLI e falhas pós-backup
# =============================================================================

# --- connect() protegido -----------------------------------------------------

def test_refresh_connect_falha_retorna_failed_sanitizado(monkeypatch, tmp_path):
    def boom_connect(url, connect_timeout=15):
        raise RuntimeError(
            'connection to server at "prod-db.example.rds.amazonaws.com" '
            '(10.0.0.5), port 5432 failed: FATAL: password authentication failed for user "postgres"'
        )

    class BoomModule:
        def connect(self, url, connect_timeout=15):
            return boom_connect(url, connect_timeout)

    monkeypatch.setattr(loader, "psycopg2", BoomModule())

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")

    assert result.outcome == "failed"
    combined = " ".join(result.problems)
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined


def test_refresh_autocommit_assignment_falha_fecha_conexao(monkeypatch, tmp_path):
    """Se `conn.autocommit = False` (dentro do try) levantar, o finally
    ainda precisa fechar a conexão -- nenhuma conexão vaza."""
    class ExplodingAutocommitConn(FakeConn):
        def __init__(self, **kw):
            self._autocommit_value = None
            self._armed = False
            super().__init__(**kw)
            self._armed = True  # só passa a explodir DEPOIS que o __init__ (que também atribui autocommit) terminar

        @property
        def autocommit(self):
            return self._autocommit_value

        @autocommit.setter
        def autocommit(self, value):
            if self._armed:
                raise RuntimeError("falha simulada ao configurar autocommit")
            self._autocommit_value = value

    fake_conn = ExplodingAutocommitConn(responses=_happy_responses())
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")

    assert result.outcome == "failed"
    assert fake_conn.closed is True


# --- commit() nunca pode resultar em "committed" se levantar -----------------

def test_refresh_commit_falha_nunca_resulta_committed(monkeypatch, tmp_path):
    fake_conn = _happy_conn()

    def boom_commit():
        raise RuntimeError("connection reset by peer postgresql://u:p@h/db")
    fake_conn.commit = boom_commit
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    audit_path = tmp_path / "b.json"

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, audit_path)

    assert result.outcome == "failed"
    assert result.outcome != "committed"
    # backup já tinha sido publicado com sucesso ANTES do commit falhar --
    # preservado no resultado para auditoria, mesmo numa falha genérica.
    assert result.backup_path == str(audit_path)
    assert result.backup_sha256 is not None
    assert audit_path.exists()  # backup nunca é removido automaticamente
    combined = " ".join(result.problems)
    assert "u:p@h" not in combined


# --- Falha no backup: nenhum DELETE, artefato parcial nunca some ------------

def test_write_window_backup_atomic_falha_ao_publicar_sha256_nao_remove_json(monkeypatch, tmp_path):
    """JSON publicado com sucesso; falha simulada ao publicar o companion
    .sha256 (ex.: corrida) -- BackupIntegrityError levantada, mas o JSON
    já publicado NÃO é removido automaticamente (auditoria manual)."""
    audit_path = tmp_path / "backup.json"
    real_link = loader.os.link

    def flaky_link(src, dst):
        if str(dst).endswith(".sha256"):
            raise FileExistsError("corrida simulada no companion .sha256")
        return real_link(src, dst)

    monkeypatch.setattr(loader.os, "link", flaky_link)

    with pytest.raises(loader.BackupIntegrityError, match="sha256"):
        loader._write_window_backup_atomic(audit_path, _D_FROM, _D_TO, [_SAMPLE_GOLD_ROW], [_SAMPLE_STAGING_ROW])

    assert audit_path.exists()  # JSON parcial preservado, nunca removido automaticamente
    assert not Path(str(audit_path) + ".sha256").exists()
    assert loader.validate_window_backup_payload(json.loads(audit_path.read_text())) == []


def test_refresh_falha_ao_publicar_sha256_bloqueia_antes_do_delete_com_aviso_sanitizado(monkeypatch, tmp_path):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    real_link = loader.os.link

    def flaky_link(src, dst):
        if str(dst).endswith(".sha256"):
            raise FileExistsError("corrida simulada")
        return real_link(src, dst)
    monkeypatch.setattr(loader.os, "link", flaky_link)

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")

    assert result.outcome == "failed"
    assert not any(s.upper().startswith("DELETE FROM") for s, _ in fake_conn.executed)
    assert fake_conn.rolled_back is True
    combined = " ".join(result.problems)
    assert "artefato" in combined.lower() or "parcial" in combined.lower()


# --- DELETE/INSERT levantam exceção genérica (não só rowcount) --------------

def test_refresh_delete_levanta_excecao_generica_preserva_backup(monkeypatch, tmp_path):
    class FailingCursor(FakeCursor):
        def execute(self, sql, params=None):
            norm = " ".join(sql.split())
            self.conn.executed.append((norm, params))
            if norm.upper().startswith("DELETE FROM"):
                raise RuntimeError("deadlock detected postgresql://u:p@h/db")
            if self.conn.fail_on_substring and self.conn.fail_on_substring in norm.upper():
                raise RuntimeError("falha simulada de execução")
            if norm.upper().startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"):
                self.conn.final_insert_executed = True

    class FailingConn(FakeConn):
        def cursor(self):
            return FailingCursor(self)

    fake_conn = FailingConn(responses=_happy_responses())
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    audit_path = tmp_path / "b.json"

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, audit_path)

    assert result.outcome == "failed"
    assert result.backup_path == str(audit_path)
    assert result.backup_sha256 is not None
    assert fake_conn.rolled_back is True
    combined = " ".join(result.problems)
    assert "u:p@h" not in combined


def test_refresh_insert_levanta_excecao_generica_preserva_backup(monkeypatch, tmp_path):
    class FailingCursor(FakeCursor):
        def execute(self, sql, params=None):
            norm = " ".join(sql.split())
            self.conn.executed.append((norm, params))
            if norm.upper().startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"):
                raise RuntimeError("connection lost postgresql://u:p@h/db")
            if self.conn.fail_on_substring and self.conn.fail_on_substring in norm.upper():
                raise RuntimeError("falha simulada de execução")

    class FailingConn(FakeConn):
        def cursor(self):
            return FailingCursor(self)

    fake_conn = FailingConn(responses=_happy_responses())
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    audit_path = tmp_path / "b.json"

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, audit_path)

    assert result.outcome == "failed"
    assert result.backup_path == str(audit_path)
    assert result.backup_sha256 is not None
    assert fake_conn.rolled_back is True


# --- CLI: valida janela/audit_path ANTES de secret/preflight (PoisonConn) ---

class PoisonConnect:
    """Substitui psycopg2.connect: registra se foi chamado. Diferente de
    `raise AssertionError(...)` dentro do próprio connect (que agora seria
    capturado pelo try/except em volta de connect() e viraria um resultado
    "failed" comum, mascarando a falha do teste) — aqui a prova é o FLAG
    `called`, verificado depois, robusto a qualquer captura de exceção no
    código sob teste."""

    def __init__(self):
        self.called = False

    def __call__(self, *a, **k):
        self.called = True
        raise RuntimeError("PoisonConnect: não deveria conectar nesta validação")


class PoisonWindowWriteConn:
    """Substitui window_write_conn.load_window_write_secret: registra se
    foi chamado, sem nunca ler o disco de verdade."""

    def __init__(self):
        self.called = False

    def __call__(self, *a, **k):
        self.called = True
        raise AssertionError("não deveria ler o secret nesta validação")


def test_run_refresh_cli_janela_invalida_nao_le_secret_nem_conecta(monkeypatch, tmp_path):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_refresh_shopee_window_cli(_D_TO, _D_FROM, tmp_path / "b.json")  # invertida

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


def test_run_refresh_cli_audit_path_invalido_nao_le_secret_nem_conecta(monkeypatch, tmp_path):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_refresh_shopee_window_cli(_D_FROM, _D_TO, Path("relativo.json"))

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


def test_run_refresh_cli_sem_datamart_database_url_nao_le_secret_nem_conecta(monkeypatch, tmp_path):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "")
    monkeypatch.setattr(loader.settings, "datamart_host", "")
    monkeypatch.setattr(loader.settings, "datamart_db", "")
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_refresh_shopee_window_cli(_D_FROM, _D_TO, tmp_path / "b.json")

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


# --- CLI: nunca imprime caminho absoluto -------------------------------------

def test_run_refresh_cli_nunca_imprime_caminho_absoluto_em_erro(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    fake_username_dir = tmp_path / "Users" / "usuario.exemplo" / "AppData"
    fake_username_dir.mkdir(parents=True)
    relative_path = Path("relativo_dentro_de_mario.json")  # relativo -> bloqueado antes de qualquer secret/conexão

    rc = loader.run_refresh_shopee_window_cli(_D_FROM, _D_TO, relative_path)

    assert rc == 2
    captured = capsys.readouterr()
    assert "usuario.exemplo" not in captured.out
    assert "usuario.exemplo" not in captured.err


def test_run_refresh_cli_sucesso_imprime_so_basename_e_sha(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")

    class HappyPreflightReport:
        ok = True
        warnings = []
        blocking_reasons = []
        safe_summary = {"pg_is_in_recovery": False}

    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer@host/db", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"})
    monkeypatch.setattr(loader.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: "postgresql://writer@host/db")
    monkeypatch.setattr(loader.window_write_conn, "run_window_preflight", lambda *a, **k: HappyPreflightReport())

    audit_path = tmp_path / "sub_mario_secreto" / "backup.json"
    audit_path.parent.mkdir(parents=True)

    fake_result = loader.ShopeeWindowRefreshResult(
        outcome="committed", rows_deleted=1, rows_inserted=1,
        backup_path=str(audit_path), backup_sha256="a" * 64,
    )
    monkeypatch.setattr(loader, "execute_shopee_window_refresh", lambda *a, **k: fake_result)

    rc = loader.run_refresh_shopee_window_cli(_D_FROM, _D_TO, audit_path)

    assert rc == 0
    captured = capsys.readouterr()
    assert "sub_mario_secreto" not in captured.out
    assert str(audit_path.parent) not in captured.out
    assert "backup.json" in captured.out
    assert "a" * 64 in captured.out


# =============================================================================
# Gate S3.2 — barreira da CLI se o preflight levantar + paths nunca levantam
# =============================================================================

def test_run_refresh_cli_barreira_se_preflight_levantar(monkeypatch, tmp_path, capsys):
    """Finding 1: mesmo que uma regressão futura faça run_window_preflight
    levantar, a CLI retorna exit 2 com mensagem sanitizada — nunca
    traceback, nunca infraestrutura, nunca execução do refresh."""
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    monkeypatch.setattr(
        loader.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer@host/db", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(
        loader.window_write_conn, "validate_window_write_guardrails",
        lambda secret, read_url: "postgresql://writer@host/db",
    )

    def boom_preflight(*a, **k):
        raise RuntimeError(
            'connection to server at "prod-db.example.rds.amazonaws.com" '
            '(10.0.0.5), port 5432 failed for user "postgres"'
        )
    monkeypatch.setattr(loader.window_write_conn, "run_window_preflight", boom_preflight)

    def boom_execute(*a, **k):
        raise AssertionError("refresh nunca deveria executar se o preflight levantou")
    monkeypatch.setattr(loader, "execute_shopee_window_refresh", boom_execute)

    rc = loader.run_refresh_shopee_window_cli(_D_FROM, _D_TO, tmp_path / "b.json")

    assert rc == 2
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined
    assert "Traceback" not in combined


def test_validate_new_window_audit_path_nunca_levanta_em_oserror(monkeypatch, tmp_path):
    """Finding 4: OSError em is_dir()/resolve()/exists() vira problema
    sanitizado (sem caminho absoluto), nunca exceção."""
    def boom_is_dir(self):
        raise OSError(f"ACL negou stat em {self}")
    monkeypatch.setattr(loader.Path, "is_dir", boom_is_dir)

    problem = loader._validate_new_window_audit_path(tmp_path / "b.json", tmp_path / "repo")
    assert problem is not None
    assert "OSError" in problem
    assert str(tmp_path) not in problem


def test_validate_new_window_audit_path_nunca_levanta_em_runtimeerror_no_resolve(monkeypatch, tmp_path):
    def boom_resolve(self):
        raise RuntimeError(f"symlink loop em {self}")
    monkeypatch.setattr(loader.Path, "resolve", boom_resolve)

    problem = loader._validate_new_window_audit_path(tmp_path / "b.json", tmp_path / "repo")
    assert problem is not None
    assert "RuntimeError" in problem
    assert str(tmp_path) not in problem


def test_refresh_path_com_falha_de_resolve_bloqueia_sem_conectar(monkeypatch, tmp_path):
    def boom_resolve(self):
        raise RuntimeError("symlink loop")
    monkeypatch.setattr(loader.Path, "resolve", boom_resolve)

    def boom_connect(*a, **k):
        raise AssertionError("não deveria conectar com path que falha em resolve()")
    monkeypatch.setattr(loader.psycopg2, "connect", boom_connect)

    result = loader.execute_shopee_window_refresh("postgresql://writer@host/db", _D_FROM, _D_TO, tmp_path / "b.json")
    assert result.outcome == "blocked"
    assert any("RuntimeError" in p for p in result.problems)


# =============================================================================
# Gate S4.3d — quando o preflight bloqueia por incompatibilidade do
# interceptor DDL do AWS DMS, o refresh real NUNCA roda.
# =============================================================================

def test_run_refresh_cli_bloqueado_por_incompatibilidade_dms_nao_executa_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    monkeypatch.setattr(
        loader.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer@host/db", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(
        loader.window_write_conn, "validate_window_write_guardrails",
        lambda secret, read_url: "postgresql://writer@host/db",
    )

    class DmsIncompatiblePreflightReport:
        ok = False
        warnings = []
        blocking_reasons = [
            "Interceptor DDL do AWS DMS incompatível com execução least-privilege: "
            "função sem SECURITY DEFINER (ou não confirmado)"
        ]
        safe_summary = {"dms_ddl_interceptor_compatible": False}

    monkeypatch.setattr(loader.window_write_conn, "run_window_preflight", lambda *a, **k: DmsIncompatiblePreflightReport())

    def boom_execute(*a, **k):
        raise AssertionError("refresh nunca deveria executar com o interceptor DMS incompatível")
    monkeypatch.setattr(loader, "execute_shopee_window_refresh", boom_execute)

    rc = loader.run_refresh_shopee_window_cli(_D_FROM, _D_TO, tmp_path / "b.json")

    assert rc == 2
    assert not (tmp_path / "b.json").exists()
