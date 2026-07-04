import json
import math

from pipelines.ingestion.shopee_raw.hashing import payload_to_json, row_sha256, sha256_file, sha256_text


def test_sha256_file_idempotente(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"conteudo fixo")
    h1 = sha256_file(f)
    h2 = sha256_file(f)
    assert h1 == h2
    assert len(h1) == 64


def test_sha256_file_muda_com_conteudo(tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_bytes(b"conteudo 1")
    f2.write_bytes(b"conteudo 2")
    assert sha256_file(f1) != sha256_file(f2)


def test_payload_to_json_sem_nan_invalido():
    payload = {"valor": float("nan"), "outro": float("inf"), "ok": 1.5}
    text = payload_to_json(payload)
    parsed = json.loads(text)
    assert parsed["valor"] is None
    assert parsed["outro"] is None
    assert parsed["ok"] == 1.5
    # allow_nan=False garante que um NaN literal nunca escaparia sem ser limpo antes
    assert "NaN" not in text
    assert "Infinity" not in text


def test_payload_to_json_datas_viram_iso():
    from datetime import date, datetime

    payload = {"d": date(2026, 1, 1), "dt": datetime(2026, 1, 1, 12, 30)}
    text = payload_to_json(payload)
    parsed = json.loads(text)
    assert parsed["d"] == "2026-01-01"
    assert parsed["dt"] == "2026-01-01T12:30:00"


def test_row_sha256_deterministico_e_sensivel_a_conteudo():
    a = {"x": 1, "y": "valor"}
    b = {"y": "valor", "x": 1}  # ordem diferente, mesmo conteúdo
    c = {"x": 2, "y": "valor"}
    assert row_sha256(a) == row_sha256(b)
    assert row_sha256(a) != row_sha256(c)


def test_sha256_text_basico():
    assert sha256_text("abc") == sha256_text("abc")
    assert sha256_text("abc") != sha256_text("abd")
