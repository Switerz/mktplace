"""
Testes de `execute_shopee_window_restore` / `--restore-shopee-window` —
Gate S3. O backup é tratado como ENTRADA NÃO CONFIÁVEL: hash conferido
antes de qualquer parsing, estrutura validada antes de qualquer conexão,
compare-and-swap contra o estado atual da Gold antes de qualquer DELETE.

Usa arquivos reais em `tmp_path` (o cálculo de SHA-256/leitura do JSON NÃO
é mockado) + conexões/cursores psycopg2 falsos para a parte transacional.
Nenhum banco real é tocado.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines.ingestion.gold_regional import loader


# ---------------------------------------------------------------------------
# Fixtures — registros/linhas consistentes via as MESMAS funções de
# conversão do módulo (nunca redigitadas à mão em dois formatos possivelmente
# divergentes).
# ---------------------------------------------------------------------------

_D_FROM = date(2026, 6, 1)
_D_TO = date(2026, 6, 30)
_ROW_DATE = date(2026, 6, 15)
_ZERO_FINGERPRINT = (0, Decimal("0"), Decimal("0"), 0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), 0, 0, 0, 0)


def _row_tuple(gmv, orders, loja_id=3, uf="SP"):
    return (
        _ROW_DATE, loader.SHOPEE_MARKETPLACE_ID, loja_id, uf,
        Decimal(gmv), orders, 12, 1, 0,
        None, Decimal("20.00"), Decimal("15.00"), None,
        orders, orders, 0, 0,
    )


_BEFORE_ROW = _row_tuple("500.00", 10)
_AFTER_ROW = _row_tuple("520.00", 11)
_BEFORE_RECORD = loader._row_to_backup_record(_BEFORE_ROW)
_AFTER_RECORD = loader._row_to_backup_record(_AFTER_ROW)


def _valid_payload(before_records=None, after_records=None, date_from=_D_FROM, date_to=_D_TO, **overrides):
    before_records = [_BEFORE_RECORD] if before_records is None else before_records
    after_records = [_AFTER_RECORD] if after_records is None else after_records
    payload = {
        "schema_version": loader.WINDOW_BACKUP_SCHEMA_VERSION,
        "created_at_utc": "2026-06-30T12:00:00Z",
        "marketplace_id": loader.SHOPEE_MARKETPLACE_ID,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "grain_key": list(loader._WINDOW_KEY_COLUMNS),
        "business_columns": list(loader._WINDOW_BUSINESS_COLUMNS),
        "before_count": len(before_records),
        "after_count": len(after_records),
        "before_aggregates": {"rows": len(before_records), "gmv": "500.00", "orders": 10},
        "after_aggregates": {"rows": len(after_records), "gmv": "520.00", "orders": 11},
        "before_records": before_records,
        "planned_after_records": after_records,
    }
    payload.update(overrides)
    return payload


def _write_backup(tmp_path, payload_or_text, name="backup.json"):
    path = tmp_path / name
    if isinstance(payload_or_text, str):
        path.write_text(payload_or_text, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload_or_text), encoding="utf-8")
    sha = loader._sha256_file(path)
    return path, sha


_VALID_HEX_64 = "a" * 64


# ---------------------------------------------------------------------------
# Fakes — mesmo padrão de test_gold_regional_window_refresh.py
# ---------------------------------------------------------------------------

class _Seq:
    def __init__(self, *values):
        self.values = values


def _contains(*subs):
    return lambda upper: all(s in upper for s in subs)


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
            return self._resolve_rowcount(self.conn.delete_rowcount, "delete")
        if upper.startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"):
            return self._resolve_rowcount(self.conn.insert_rowcount, "insert")
        return 0

    def _resolve_rowcount(self, value, key):
        if isinstance(value, _Seq):
            idx = self.conn._call_index.get(key, 0)
            self.conn._call_index[key] = idx + 1
            return value.values[min(idx, len(value.values) - 1)]
        return value


class FakeConn:
    def __init__(
        self,
        gold_window_rows=None,
        fingerprint=None,
        delete_rowcount=1,
        insert_rowcount=1,
        lock_acquired=True,
        fail_on_substring=None,
    ):
        self.executed: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.autocommit = None
        self.lock_acquired = lock_acquired
        self.fail_on_substring = fail_on_substring
        self._call_index: dict = {}
        self.delete_rowcount = delete_rowcount
        self.insert_rowcount = insert_rowcount

        if gold_window_rows is None:
            gold_window_rows = _Seq([_AFTER_ROW], [_BEFORE_ROW])  # CAS: atual==planned_after; pós-restore: ==before
        if fingerprint is None:
            fingerprint = _Seq(_ZERO_FINGERPRINT, _ZERO_FINGERPRINT)

        self.responses = [
            (_contains("NOT (MARKETPLACE_ID = %(SHOPEE_MARKETPLACE_ID)S AND DATE BETWEEN"), fingerprint),
            (_contains("ORDER BY DATE, LOJA_ID, UF", "FROM GOLD.MARKETPLACE_REGION_DAILY"), gold_window_rows),
        ]

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
    return FakeConn(**overrides)


# ---------------------------------------------------------------------------
# Hash — obrigatório, 64 hex, recalculado ANTES de qualquer conexão
# ---------------------------------------------------------------------------

def test_restore_hash_invalido_formato_bloqueia_antes_de_ler_arquivo(monkeypatch, tmp_path):
    # Arquivo precisa EXISTIR para passar a checagem de caminho e chegar na
    # checagem de formato do SHA (ordem: caminho -> formato do SHA -> tamanho
    # -> recalcula SHA -> parse -> estrutura).
    existing = tmp_path / "existe.json"
    existing.write_text("{}")

    def boom(*a, **k):
        raise AssertionError("não deveria ler o arquivo com hash malformado")
    monkeypatch.setattr(loader, "_sha256_file", boom)

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", existing, "nao-e-hex")
    assert result.outcome == "blocked"
    assert any("hexadecimal" in p for p in result.problems)


def test_restore_hash_ausente_bloqueia(monkeypatch, tmp_path):
    existing = tmp_path / "existe.json"
    existing.write_text("{}")

    def boom(*a, **k):
        raise AssertionError("não deveria ler o arquivo sem expected_backup_sha256")
    monkeypatch.setattr(loader, "_sha256_file", boom)

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", existing, None)
    assert result.outcome == "blocked"


def test_restore_hash_nao_bate_bloqueia_antes_de_conectar(monkeypatch, tmp_path):
    path, real_sha = _write_backup(tmp_path, _valid_payload())

    def boom(*a, **k):
        raise AssertionError("não deveria conectar com hash divergente")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    wrong_sha = "b" * 64
    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, wrong_sha)
    assert result.outcome == "blocked"
    assert any("SHA-256" in p for p in result.problems)


def test_restore_arquivo_ausente_bloqueia(tmp_path):
    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", tmp_path / "nao_existe.json", _VALID_HEX_64)
    assert result.outcome == "blocked"


# ---------------------------------------------------------------------------
# JSON malformado / estrutura inválida — tudo ANTES de conectar
# ---------------------------------------------------------------------------

def test_restore_json_malformado_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, "{ isso nao e json valido")

    def boom(*a, **k):
        raise AssertionError("não deveria conectar com JSON malformado")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"


def test_restore_topo_nao_e_dict_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, [1, 2, 3])

    def boom(*a, **k):
        raise AssertionError("não deveria conectar com topo que não é objeto")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("objeto JSON" in p for p in result.problems)


def test_restore_schema_version_desconhecido_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(schema_version=999))

    def boom(*a, **k):
        raise AssertionError("não deveria conectar com schema_version desconhecido")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("schema_version" in p for p in result.problems)


def test_restore_chave_de_topo_faltando_bloqueia(monkeypatch, tmp_path):
    payload = _valid_payload()
    del payload["after_count"]
    path, sha = _write_backup(tmp_path, payload)
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("ausente" in p for p in result.problems)


def test_restore_chave_de_topo_extra_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(campo_extra="nao deveria existir"))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("inesperada" in p for p in result.problems)


def test_restore_marketplace_id_nao_shopee_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(marketplace_id=loader.ML_MARKETPLACE_ID))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("Shopee" in p for p in result.problems)


def test_restore_janela_maior_que_180_dias_bloqueia(monkeypatch, tmp_path):
    from datetime import timedelta
    d_to = _D_FROM + timedelta(days=200)
    payload = _valid_payload(date_from=_D_FROM, date_to=d_to, before_records=[], after_records=[])
    path, sha = _write_backup(tmp_path, payload)
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("janela do backup inválida" in p for p in result.problems)


def test_restore_registro_nao_dict_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=["nao e' um objeto"]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("não é um objeto" in p for p in result.problems)


def test_restore_bool_onde_int_esperado_bloqueia(monkeypatch, tmp_path):
    bad_record = dict(_BEFORE_RECORD)
    bad_record["orders"] = True  # bool -- subclasse de int, precisa ser rejeitado explicitamente
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[bad_record]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("orders inválido" in p for p in result.problems)


def test_restore_data_invalida_bloqueia(monkeypatch, tmp_path):
    bad_record = dict(_BEFORE_RECORD)
    bad_record["date"] = "31/06/2026"  # nao ISO
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[bad_record]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("date:" in p for p in result.problems)


def test_restore_uf_invalida_bloqueia(monkeypatch, tmp_path):
    bad_record = dict(_BEFORE_RECORD)
    bad_record["uf"] = "ZZ"
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[bad_record]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("uf inválida" in p for p in result.problems)


def test_restore_chave_duplicada_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[_BEFORE_RECORD, dict(_BEFORE_RECORD)]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("duplicada" in p for p in result.problems)


def test_restore_gmv_nan_bloqueia(monkeypatch, tmp_path):
    bad_record = dict(_BEFORE_RECORD)
    bad_record["gmv"] = "NaN"
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[bad_record]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("finito" in p for p in result.problems)


def test_restore_gmv_infinity_bloqueia(monkeypatch, tmp_path):
    bad_record = dict(_BEFORE_RECORD)
    bad_record["gmv"] = "Infinity"
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[bad_record]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("finito" in p for p in result.problems)


def test_restore_gmv_negativo_bloqueia(monkeypatch, tmp_path):
    bad_record = dict(_BEFORE_RECORD)
    bad_record["gmv"] = "-1.00"
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[bad_record]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("negativo" in p for p in result.problems)


def test_restore_numerador_maior_que_denominador_bloqueia(monkeypatch, tmp_path):
    bad_record = dict(_BEFORE_RECORD)
    bad_record["uf_known_orders"] = 999
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[bad_record]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("uf_known_orders > uf_eligible_orders" in p for p in result.problems)


def test_restore_campo_extra_no_registro_bloqueia(monkeypatch, tmp_path):
    bad_record = dict(_BEFORE_RECORD)
    bad_record["order_id"] = "ABC123"  # PII/identificador nunca deveria aparecer aqui
    path, sha = _write_backup(tmp_path, _valid_payload(before_records=[bad_record]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("inesperada" in p for p in result.problems)


def test_restore_before_e_planned_after_validados_separadamente(monkeypatch, tmp_path):
    """Um erro em planned_after_records (não em before_records) também
    bloqueia -- as duas listas são validadas independentemente."""
    bad_after = dict(_AFTER_RECORD)
    bad_after["gmv"] = "NaN"
    path, sha = _write_backup(tmp_path, _valid_payload(after_records=[bad_after]))
    monkeypatch.setattr(loader.psycopg2, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria conectar")))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("planned_after_records[0]" in p for p in result.problems)


# ---------------------------------------------------------------------------
# Compare-and-swap — estado atual diferente de planned_after aborta SEM DELETE
# ---------------------------------------------------------------------------

def test_restore_cas_bloqueia_quando_gold_atual_diverge_de_planned_after(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    # Gold atual NAO bate com planned_after_records (linha com GMV diferente)
    diverging_row = _row_tuple("999.00", 999)
    fake_conn = FakeConn(gold_window_rows=_Seq([diverging_row]))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "blocked"
    assert any("compare-and-swap" in p for p in result.problems)
    assert not any(s.upper().startswith("DELETE FROM") for s, _ in fake_conn.executed)
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


def test_restore_cas_bloqueia_quando_gold_atual_tem_chave_a_mais(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    extra_row = _row_tuple("10.00", 1, loja_id=4, uf="RJ")
    fake_conn = FakeConn(gold_window_rows=_Seq([_AFTER_ROW, extra_row]))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "blocked"
    assert not any(s.upper().startswith("DELETE FROM") for s, _ in fake_conn.executed)


# ---------------------------------------------------------------------------
# Caminho feliz — restaura e reconcilia
# ---------------------------------------------------------------------------

def test_restore_caminho_feliz_commita(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "committed"
    assert result.rows_deleted == 1
    assert result.rows_inserted == 1
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert fake_conn.closed is True


def test_restore_ordem_lock_cas_delete_insert_reconcile_commit(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    order = [s.upper() for s, _ in fake_conn.executed]

    def idx(pred):
        return next(i for i, s in enumerate(order) if pred(s))

    i_lock = idx(lambda s: "PG_TRY_ADVISORY_LOCK" in s)
    i_table_lock = idx(lambda s: "SHARE ROW EXCLUSIVE MODE" in s)
    i_fp_before = idx(lambda s: "NOT (MARKETPLACE_ID" in s)
    i_select_current = idx(lambda s: "ORDER BY DATE, LOJA_ID, UF" in s)
    i_delete = idx(lambda s: s.startswith("DELETE FROM"))
    i_insert = idx(lambda s: s.startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"))
    i_unlock = idx(lambda s: "PG_ADVISORY_UNLOCK" in s)

    assert i_lock < i_table_lock < i_fp_before < i_select_current < i_delete < i_insert < i_unlock


def test_restore_delete_rowcount_divergente_aborta(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    fake_conn = FakeConn(delete_rowcount=99)
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "failed"
    assert any("DELETE removeu" in p for p in result.problems)
    assert fake_conn.rolled_back is True


def test_restore_insert_rowcount_divergente_aborta(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    fake_conn = FakeConn(insert_rowcount=0)
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "failed"
    assert any("INSERT de restauração" in p for p in result.problems)
    assert fake_conn.rolled_back is True


def test_restore_reconciliacao_pos_restauracao_divergente_aborta(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    # pos-restauracao, a leitura da Gold NAO bate com before_records
    diverging_final_row = _row_tuple("1.00", 1)
    fake_conn = FakeConn(gold_window_rows=_Seq([_AFTER_ROW], [diverging_final_row]))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "failed"
    assert any("reconciliação pós-restauração" in p for p in result.problems)
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


def test_restore_fingerprint_fora_do_escopo_alterado_aborta(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    changed_fp = (1, Decimal("1.00"), 1, 0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), 0, 0, 0, 0)
    fake_conn = FakeConn(fingerprint=_Seq(_ZERO_FINGERPRINT, changed_fp))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "failed"
    assert any("fora do escopo" in p for p in result.problems)
    assert fake_conn.rolled_back is True


# ---------------------------------------------------------------------------
# Nenhuma tentativa automática / rollback em erro genérico
# ---------------------------------------------------------------------------

def test_restore_nao_faz_retry_automatico(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    fake_conn = FakeConn(fail_on_substring="SHARE ROW EXCLUSIVE MODE")
    fake_module = FakePsycopg2Module(fake_conn)
    monkeypatch.setattr(loader, "psycopg2", fake_module)

    loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert fake_module.connect_calls == 1


def test_restore_rollback_em_erro_generico_nunca_expoe_mensagem_nativa(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())

    class FailingCursor(FakeCursor):
        def execute(self, sql, params=None):
            norm = " ".join(sql.split())
            self.conn.executed.append((norm, params))
            if "SHARE ROW EXCLUSIVE MODE" in norm.upper():
                raise RuntimeError(
                    'connection to server at "prod-db.example.rds.amazonaws.com" '
                    '(10.0.0.5), port 5432 failed: FATAL: password authentication failed for user "postgres"'
                )

    class FailingConn(FakeConn):
        def cursor(self):
            return FailingCursor(self)

    fake_conn = FailingConn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "failed"
    combined = " ".join(result.problems)
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined
    assert fake_conn.rolled_back is True


def test_restore_advisory_lock_ocupado_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    fake_conn = FakeConn(lock_acquired=False)
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "blocked"
    assert any("advisory lock" in p for p in result.problems)
    assert fake_conn.closed is True


# =============================================================================
# Gate S3.1 — validação integral do backup (created_at_utc, grain_key,
# business_columns, contagens, agregados recalculados, limites)
# =============================================================================

def _assert_blocks_before_connect(monkeypatch, path, sha):
    def boom(*a, **k):
        raise AssertionError("não deveria conectar")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)
    return loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)


def test_restore_created_at_utc_malformado_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(created_at_utc="não é um timestamp"))
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("created_at_utc" in p for p in result.problems)


def test_restore_created_at_utc_calendario_invalido_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(created_at_utc="2026-13-40T99:99:99Z"))
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("created_at_utc" in p for p in result.problems)


def test_restore_grain_key_incorreto_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(grain_key=["date", "marketplace_id", "uf", "loja_id"]))  # ordem errada
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("grain_key" in p for p in result.problems)


def test_restore_business_columns_fora_de_ordem_bloqueia(monkeypatch, tmp_path):
    reordered = list(loader._WINDOW_BUSINESS_COLUMNS)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    path, sha = _write_backup(tmp_path, _valid_payload(business_columns=reordered))
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("business_columns" in p for p in result.problems)


def test_restore_business_columns_com_coluna_extra_bloqueia(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload(business_columns=list(loader._WINDOW_BUSINESS_COLUMNS) + ["campo_extra"]))
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("business_columns" in p for p in result.problems)


def test_restore_before_count_nao_bate_com_tamanho_real_bloqueia(monkeypatch, tmp_path):
    payload = _valid_payload()
    payload["before_count"] = 999
    path, sha = _write_backup(tmp_path, payload)
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("before_count" in p for p in result.problems)


def test_restore_after_count_nao_bate_com_tamanho_real_bloqueia(monkeypatch, tmp_path):
    payload = _valid_payload()
    payload["after_count"] = 0
    path, sha = _write_backup(tmp_path, payload)
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("after_count" in p for p in result.problems)


def test_restore_before_count_bool_recusado(monkeypatch, tmp_path):
    payload = _valid_payload()
    payload["before_count"] = True  # bool -- subclasse de int, precisa ser rejeitado
    path, sha = _write_backup(tmp_path, payload)
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("before_count" in p for p in result.problems)


def test_restore_before_aggregates_gmv_nao_bate_com_soma_recalculada_bloqueia(monkeypatch, tmp_path):
    payload = _valid_payload()
    payload["before_aggregates"] = {"rows": 1, "gmv": "999999.99", "orders": 10}
    path, sha = _write_backup(tmp_path, payload)
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("before_aggregates.gmv" in p for p in result.problems)


def test_restore_after_aggregates_orders_nao_bate_com_soma_recalculada_bloqueia(monkeypatch, tmp_path):
    payload = _valid_payload()
    payload["after_aggregates"] = {"rows": 1, "gmv": "520.00", "orders": 99999}
    path, sha = _write_backup(tmp_path, payload)
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("after_aggregates.orders" in p for p in result.problems)


def test_restore_aggregates_rows_nao_bate_com_contagem_real_bloqueia(monkeypatch, tmp_path):
    payload = _valid_payload()
    payload["before_aggregates"] = {"rows": 5, "gmv": "500.00", "orders": 10}
    path, sha = _write_backup(tmp_path, payload)
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("before_aggregates.rows" in p for p in result.problems)


def test_restore_aggregates_com_chave_extra_bloqueia(monkeypatch, tmp_path):
    payload = _valid_payload()
    payload["before_aggregates"] = {"rows": 1, "gmv": "500.00", "orders": 10, "extra": 1}
    path, sha = _write_backup(tmp_path, payload)
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("before_aggregates" in p and "inesperada" in p for p in result.problems)


def test_restore_registros_excedem_limite_maximo_bloqueia(monkeypatch, tmp_path):
    """180 dias x 5 lojas x 28 UFs -- backup com mais registros que isso é
    recusado (defesa contra payload adversarial gigante)."""
    huge_before = [_BEFORE_RECORD] * (loader.MAX_WINDOW_BACKUP_RECORDS + 1)
    payload = _valid_payload(before_records=huge_before)
    payload["before_count"] = len(huge_before)
    path, sha = _write_backup(tmp_path, payload)
    result = _assert_blocks_before_connect(monkeypatch, path, sha)
    assert result.outcome == "blocked"
    assert any("excede o limite" in p for p in result.problems)


def test_restore_arquivo_excede_tamanho_maximo_bloqueia_antes_de_ler_conteudo(monkeypatch, tmp_path):
    """Gate S3.2: o teto agora vem de os.fstat no DESCRITOR ABERTO (não de
    Path.stat num caminho separado). Com o teto rebaixado para 16 bytes, um
    arquivo de 1024 bytes é bloqueado ANTES do parse (json.loads boomado
    prova que o conteúdo nunca chega ao parser)."""
    path = tmp_path / "grande.json"
    path.write_bytes(b"0" * 1024)

    monkeypatch.setattr(loader, "MAX_WINDOW_BACKUP_FILE_BYTES", 16)

    def boom_loads(*a, **k):
        raise AssertionError("não deveria parsear JSON de um arquivo grande demais")
    monkeypatch.setattr(loader.json, "loads", boom_loads)

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, "a" * 64)
    assert result.outcome == "blocked"
    assert any("tamanho" in p for p in result.problems)


def test_restore_leitura_limitada_mesmo_se_fstat_mentir(monkeypatch, tmp_path):
    """Defesa contra arquivo crescendo entre fstat e read: fstat declara um
    tamanho dentro do teto, mas a leitura limitada (teto+1) traz um byte
    excedente — bloqueado, nunca lê o arquivo inteiro sem limite."""
    path = tmp_path / "crescendo.json"
    path.write_bytes(b"0" * 40)  # maior que o teto rebaixado abaixo

    monkeypatch.setattr(loader, "MAX_WINDOW_BACKUP_FILE_BYTES", 16)

    real_fstat = loader.os.fstat

    def lying_fstat(fd):
        real = real_fstat(fd)

        class LyingStat:
            st_size = 8  # mente: dentro do teto
            st_mode = real.st_mode

        return LyingStat()

    monkeypatch.setattr(loader.os, "fstat", lying_fstat)

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, "a" * 64)
    assert result.outcome == "blocked"
    assert any("tamanho" in p for p in result.problems)


def test_restore_backup_com_bytes_utf8_invalidos_bloqueia_sem_excecao(monkeypatch, tmp_path):
    """SHA bate (calculado dos mesmos bytes), mas os bytes não são UTF-8
    válido — UnicodeDecodeError capturado, bloqueado, sem exceção."""
    import hashlib as _hashlib
    path = tmp_path / "invalido.json"
    bad_bytes = b"\xff\xfe{ invalido"
    path.write_bytes(bad_bytes)
    sha = _hashlib.sha256(bad_bytes).hexdigest()

    def boom(*a, **k):
        raise AssertionError("não deveria conectar com backup UTF-8 inválido")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)
    assert result.outcome == "blocked"
    assert any("UnicodeDecodeError" in p for p in result.problems)


def test_restore_sha_e_json_calculados_dos_mesmos_bytes_abertos_uma_vez(monkeypatch, tmp_path):
    """Prova da leitura única: Path.read_text e _sha256_file boomados — um
    backup válido AINDA é aceito, porque hash e parse vêm do mesmo buffer
    binário lido uma única vez (nenhuma segunda abertura do arquivo)."""
    path, sha = _write_backup(tmp_path, _valid_payload())

    def boom_read_text(*a, **k):
        raise AssertionError("read_text não deveria ser usado — leitura única binária")
    monkeypatch.setattr(loader.Path, "read_text", boom_read_text)

    def boom_sha_file(*a, **k):
        raise AssertionError("_sha256_file não deveria ser usado — hash vem dos bytes já lidos")
    monkeypatch.setattr(loader, "_sha256_file", boom_sha_file)

    payload, problems = loader._validate_and_load_window_backup(path, sha, tmp_path / "repo")
    assert problems == []
    assert payload["schema_version"] == loader.WINDOW_BACKUP_SCHEMA_VERSION


def test_restore_oserror_em_resolve_bloqueia_sanitizado_sem_secret(monkeypatch, tmp_path, capsys):
    """Finding 4: RuntimeError/OSError em resolve() vira problema sanitizado
    (sem caminho absoluto), nunca traceback; CLI não lê secret nem conecta."""
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")

    def boom_resolve(self):
        raise RuntimeError(f"symlink loop em {self}")
    monkeypatch.setattr(loader.Path, "resolve", boom_resolve)

    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    backup_path = tmp_path / "sub_usuario_secreto" / "b.json"
    backup_path.parent.mkdir()
    backup_path.write_text("{}")

    rc = loader.run_restore_shopee_window_cli(backup_path, "a" * 64)

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False
    captured = capsys.readouterr()
    assert "sub_usuario_secreto" not in captured.out + captured.err


def test_validate_existing_window_audit_path_nunca_levanta_em_oserror(monkeypatch, tmp_path):
    def boom_exists(self):
        raise OSError("ACL negou stat")
    monkeypatch.setattr(loader.Path, "exists", boom_exists)

    problem = loader._validate_existing_window_audit_path(tmp_path / "x.json", tmp_path / "repo")
    assert problem is not None
    assert "OSError" in problem
    assert str(tmp_path) not in problem


def test_run_restore_cli_barreira_se_preflight_levantar(monkeypatch, tmp_path, capsys):
    """Finding 1: preflight levantando na CLI do restore → exit 2, mensagem
    sanitizada, nenhuma execução do restore."""
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    path, sha = _write_backup(tmp_path, _valid_payload())
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
        raise AssertionError("restore nunca deveria executar se o preflight levantou")
    monkeypatch.setattr(loader, "execute_shopee_window_restore", boom_execute)

    rc = loader.run_restore_shopee_window_cli(path, sha)

    assert rc == 2
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined
    assert "Traceback" not in combined


# --- commit() nunca pode resultar em "committed" se levantar -----------------

def test_restore_commit_falha_nunca_resulta_committed(monkeypatch, tmp_path):
    path, sha = _write_backup(tmp_path, _valid_payload())
    fake_conn = _happy_conn()

    def boom_commit():
        raise RuntimeError("connection reset postgresql://u:p@h/db")
    fake_conn.commit = boom_commit
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.execute_shopee_window_restore("postgresql://writer@host/db", path, sha)

    assert result.outcome == "failed"
    assert result.outcome != "committed"
    assert fake_conn.rolled_back is True
    assert "u:p@h" not in " ".join(result.problems)


# --- CLI: valida ANTES de secret/preflight (PoisonConn) ----------------------

class PoisonConnect:
    def __init__(self):
        self.called = False

    def __call__(self, *a, **k):
        self.called = True
        raise RuntimeError("PoisonConnect: não deveria conectar nesta validação")


class PoisonWindowWriteConn:
    def __init__(self):
        self.called = False

    def __call__(self, *a, **k):
        self.called = True
        raise AssertionError("não deveria ler o secret nesta validação")


def test_run_restore_cli_hash_invalido_nao_le_secret_nem_conecta(monkeypatch, tmp_path):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    path, _sha = _write_backup(tmp_path, _valid_payload())
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_restore_shopee_window_cli(path, "hash-invalido")

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


def test_run_restore_cli_json_invalido_nao_le_secret_nem_conecta(monkeypatch, tmp_path):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    path, sha = _write_backup(tmp_path, "{ nao e json valido")
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_restore_shopee_window_cli(path, sha)

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


def test_run_restore_cli_audit_path_relativo_nao_le_secret_nem_conecta(monkeypatch):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_restore_shopee_window_cli(Path("relativo.json"), "a" * 64)

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


def test_run_restore_cli_sem_datamart_database_url_nao_le_secret_nem_conecta(monkeypatch, tmp_path):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "")
    monkeypatch.setattr(loader.settings, "datamart_host", "")
    monkeypatch.setattr(loader.settings, "datamart_db", "")
    path, sha = _write_backup(tmp_path, _valid_payload())
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_restore_shopee_window_cli(path, sha)

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


# --- CLI: nunca imprime caminho absoluto -------------------------------------

def test_run_restore_cli_nunca_imprime_caminho_absoluto_em_erro(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    user_dir = tmp_path / "Users" / "usuario.exemplo" / "backups"
    user_dir.mkdir(parents=True)
    path, sha = _write_backup(user_dir, _valid_payload(), name="backup.json")
    # corrompe o hash esperado para forçar bloqueio ANTES de conectar, mas
    # depois de o caminho já ter sido validado (existe, é .json, absoluto)
    wrong_sha = "b" * 64

    rc = loader.run_restore_shopee_window_cli(path, wrong_sha)

    assert rc == 2
    captured = capsys.readouterr()
    assert "usuario.exemplo" not in captured.out
    assert "usuario.exemplo" not in captured.err
