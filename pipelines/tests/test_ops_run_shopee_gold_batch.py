"""
Testes de `pipelines/ops/run_shopee_gold_batch.py` — Gate S5.4b (camada de
evidência operacional sobre o wrapper já auditado do Gate S5.3). Nenhum
banco real é tocado: `refresh_shopee_window_if_needed` é sempre mockado
como caixa-preta (já testado exaustivamente em
`test_ops_refresh_shopee_window_if_needed.py`); só a lógica NOVA deste
módulo (validação de ids/artifacts_dir/probe, nomes determinísticos,
publicação atômica do receipt, git/tempo, exit codes) é exercitada aqui,
usando filesystem real (`tmp_path`) só para o próprio receipt/probe.
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines.ops import refresh_shopee_window_if_needed as refresh_wrapper
from pipelines.ops import run_shopee_gold_batch as rsgb

_WRITE_URL = "postgresql://writer@host/db"
_READ_URL = "postgresql://read@host/db"
_D_FROM = date(2026, 6, 15)
_D_TO = date(2026, 6, 21)


# ---------------------------------------------------------------------------
# Fakes / builders
# ---------------------------------------------------------------------------

def _refresh_result(outcome, reason_code=None, **overrides):
    defaults = dict(outcome=outcome, reason_code=reason_code or outcome)
    defaults.update(overrides)
    return refresh_wrapper.ShopeeWindowRefreshIfNeededResult(**defaults)


def _fake_run_git_ok(commit="abc123def456", dirty_output=""):
    def _fn(args, cwd):
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, returncode=0, stdout=commit + "\n", stderr="")
        if args[0] == "status":
            return subprocess.CompletedProcess(args, returncode=0, stdout=dirty_output, stderr="")
        raise AssertionError(f"comando git inesperado: {args}")
    return _fn


def _fake_run_git_not_a_repo(args, cwd):
    return subprocess.CompletedProcess(args, returncode=128, stdout="", stderr="fatal: not a git repository")


def _monkeypatch_clock(monkeypatch, *values):
    it = iter(values)
    state = {"last": values[-1]}

    def _next():
        try:
            state["last"] = next(it)
        except StopIteration:
            pass
        return state["last"]
    monkeypatch.setattr(rsgb, "_utc_now", _next)


def _boom_refresh(monkeypatch):
    def _fn(*a, **k):
        raise AssertionError("refresh_shopee_window_if_needed nunca deveria ser chamado aqui")
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", _fn)


def _boom_git(monkeypatch):
    def _fn(args, cwd):
        raise AssertionError("Git nunca deveria ser coletado com file_ids/ids inválidos")
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fn)


def _boom_probe(monkeypatch):
    def _fn(*a, **k):
        raise AssertionError("probe/mkstemp nunca deveria rodar com file_ids/ids inválidos")
    monkeypatch.setattr(rsgb.tempfile, "mkstemp", _fn)


# ---------------------------------------------------------------------------
# Gate S5.4b.1 (Correção 1): file_ids validados com a função pública do S5.2
# ANTES de Git/artifacts_dir/probe/secret/conexão -- tanto na função pública
# quanto na CLI.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_file_ids", [
    [],
    [1, 1],
    [0],
    [-1],
    [rsgb.shopee_batch_window._POSTGRES_BIGINT_MAX + 1],
    list(range(1, rsgb.shopee_batch_window.MAX_BATCH_FILE_IDS + 2)),
], ids=["vazia", "duplicados", "zero", "negativo", "acima_do_bigint", "acima_do_limite"])
def test_file_ids_invalido_bloqueia_antes_de_git_probe_e_refresh(tmp_path, monkeypatch, bad_file_ids):
    _boom_refresh(monkeypatch)
    _boom_git(monkeypatch)
    _boom_probe(monkeypatch)

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, bad_file_ids, tmp_path, "batch1", "run1")

    assert result.operation_outcome == "blocked"
    assert result.reason_code == rsgb.REASON_INVALID_INPUT
    assert result.receipt_status == "not_attempted"
    assert result.git_commit is None
    assert result.git_dirty is None


def test_file_ids_validos_usa_lista_ordenada_e_deduplicada(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    captured = {}

    def fake_refresh(write_url, datamart_read_url, file_ids, audit_path, **kwargs):
        captured["file_ids"] = list(file_ids)
        return _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP)
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", fake_refresh)

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [3, 1, 2], tmp_path, "batch1", "run1")

    assert captured["file_ids"] == [1, 2, 3]
    assert result.file_ids == [1, 2, 3]


def test_run_cli_file_ids_invalido_bloqueia_antes_de_ids_git_probe_e_secret(tmp_path, monkeypatch):
    _boom_git(monkeypatch)
    _boom_probe(monkeypatch)

    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com file_ids inválidos")
    monkeypatch.setattr(rsgb.window_write_conn, "load_window_write_secret", boom_secret)

    rc = rsgb.run_cli(["1", "1"], str(tmp_path), "batch1", "run1", as_json=True)

    assert rc == 2


def test_run_cli_file_ids_zero_bloqueia_antes_do_secret(tmp_path, monkeypatch):
    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com file_id zero")
    monkeypatch.setattr(rsgb.window_write_conn, "load_window_write_secret", boom_secret)

    rc = rsgb.run_cli(["0"], str(tmp_path), "batch1", "run1", as_json=True)

    assert rc == 2


# ---------------------------------------------------------------------------
# Validação de batch_id/run_id — allowlist ASCII, antes de qualquer I/O
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", [
    "", "..", "a/b", "a\\b", "a b", "café", "a" * 101, ".comeca_com_ponto", "-comeca_com_hifen",
])
def test_batch_id_invalido_bloqueia_antes_do_refresh(tmp_path, monkeypatch, bad_value):
    _boom_refresh(monkeypatch)

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, bad_value, "run1")

    assert result.operation_outcome == "blocked"
    assert result.reason_code == rsgb.REASON_INVALID_INPUT
    assert result.receipt_status == "not_attempted"


@pytest.mark.parametrize("bad_value", [
    "", "..", "a/b", "a\\b", "a b", "café", "a" * 101,
])
def test_run_id_invalido_bloqueia_antes_do_refresh(tmp_path, monkeypatch, bad_value):
    _boom_refresh(monkeypatch)

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", bad_value)

    assert result.operation_outcome == "blocked"
    assert result.reason_code == rsgb.REASON_INVALID_INPUT
    assert result.receipt_status == "not_attempted"


def test_batch_id_run_id_validos_nao_bloqueiam(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed",
                         lambda *a, **k: _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch.001-2026_07_22", "run.attempt-1")

    assert result.reason_code == refresh_wrapper.REASON_NO_OP


# ---------------------------------------------------------------------------
# Validação de artifacts_dir — estrutural, antes do refresh
# ---------------------------------------------------------------------------

def test_artifacts_dir_relativo_bloqueia(monkeypatch):
    _boom_refresh(monkeypatch)
    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], Path("relativo"), "batch1", "run1")
    assert result.operation_outcome == "blocked"
    assert result.reason_code == rsgb.REASON_ARTIFACTS_DIR_INVALID


def test_artifacts_dir_dentro_do_repo_bloqueia(tmp_path, monkeypatch):
    _boom_refresh(monkeypatch)
    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1", repo_root=tmp_path)
    assert result.reason_code == rsgb.REASON_ARTIFACTS_DIR_INVALID


def test_artifacts_dir_inexistente_bloqueia(tmp_path, monkeypatch):
    _boom_refresh(monkeypatch)
    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path / "nao_existe", "batch1", "run1")
    assert result.reason_code == rsgb.REASON_ARTIFACTS_DIR_INVALID


def test_artifacts_dir_e_arquivo_nao_diretorio_bloqueia(tmp_path, monkeypatch):
    _boom_refresh(monkeypatch)
    a_file = tmp_path / "arquivo.txt"
    a_file.write_text("conteudo", encoding="utf-8")
    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], a_file, "batch1", "run1")
    assert result.reason_code == rsgb.REASON_ARTIFACTS_DIR_INVALID


@pytest.mark.parametrize("which", ["backup", "sha", "receipt"])
def test_artefato_ja_existente_bloqueia_antes_do_banco(tmp_path, monkeypatch, which):
    _boom_refresh(monkeypatch)
    backup_path, sha_path, receipt_path = rsgb._artifact_paths(tmp_path, "batch1", "run1")
    target = {"backup": backup_path, "sha": sha_path, "receipt": receipt_path}[which]
    target.write_text("ja existe", encoding="utf-8")

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.operation_outcome == "blocked"
    assert result.reason_code == rsgb.REASON_ARTIFACTS_DIR_INVALID
    assert result.receipt_status == "not_attempted"


def test_probe_falha_ao_criar_bloqueia(tmp_path, monkeypatch):
    def boom_mkstemp(*a, **k):
        raise OSError("permissao negada simulada")
    monkeypatch.setattr(rsgb.tempfile, "mkstemp", boom_mkstemp)
    _boom_refresh(monkeypatch)

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.reason_code == rsgb.REASON_ARTIFACTS_DIR_NOT_WRITABLE
    assert result.receipt_status == "not_attempted"


def test_probe_falha_ao_remover_bloqueia(tmp_path, monkeypatch):
    real_unlink = Path.unlink

    def fake_unlink(self, *a, **kw):
        if self.name.startswith(".probe_"):
            raise OSError("falha simulada ao remover probe")
        return real_unlink(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", fake_unlink)
    _boom_refresh(monkeypatch)

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.reason_code == rsgb.REASON_ARTIFACTS_DIR_NOT_WRITABLE


def test_probe_unitario_nunca_deixa_residuo_na_falha_de_escrita(tmp_path, monkeypatch):
    def boom_fdopen(fd, mode):
        os.close(fd)
        raise OSError("falha simulada ao escrever probe")
    monkeypatch.setattr(rsgb.os, "fdopen", boom_fdopen)

    problem = rsgb._probe_artifacts_dir_writable(tmp_path)

    assert problem is not None
    assert list(tmp_path.glob(".probe_*")) == []


def test_probe_unitario_sucesso_nao_deixa_residuo(tmp_path):
    problem = rsgb._probe_artifacts_dir_writable(tmp_path)
    assert problem is None
    assert list(tmp_path.glob(".probe_*")) == []


# ---------------------------------------------------------------------------
# Execução — só refresh_shopee_window_if_needed é chamado, exatamente uma vez
# ---------------------------------------------------------------------------

def test_chama_apenas_refresh_shopee_window_if_needed_com_args_corretos(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    calls = {"n": 0, "args": None}

    def fake_refresh(write_url, datamart_read_url, file_ids, audit_path, **kwargs):
        calls["n"] += 1
        calls["args"] = (write_url, datamart_read_url, list(file_ids), audit_path)
        return _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP)
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", fake_refresh)

    rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [3, 1, 2], tmp_path, "batch1", "run1")

    assert calls["n"] == 1
    expected_backup_path = tmp_path / "shopee_window_backup_batch1_run1.json"
    # validate_batch_file_ids ordena/deduplica -- a lista que chega ao S5.3
    # é a JÁ VALIDADA, não a bruta na ordem original.
    assert calls["args"] == (_WRITE_URL, _READ_URL, [1, 2, 3], expected_backup_path)


def test_audit_path_deterministico_para_os_tres_nomes(tmp_path):
    backup_path, sha_path, receipt_path = rsgb._artifact_paths(tmp_path, "b1", "r1")
    assert backup_path == tmp_path / "shopee_window_backup_b1_r1.json"
    assert sha_path == tmp_path / "shopee_window_backup_b1_r1.json.sha256"
    assert receipt_path == tmp_path / "shopee_window_receipt_b1_r1.json"


def test_refresh_chamado_no_maximo_uma_vez_mesmo_com_receipt_falho(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    calls = {"n": 0}

    def fake_refresh(*a, **k):
        calls["n"] += 1
        return _refresh_result("committed", reason_code=refresh_wrapper.REASON_COMMITTED, rows_deleted=1, rows_inserted=1)
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", fake_refresh)
    monkeypatch.setattr(rsgb, "_publish_receipt_atomic", lambda path, payload: ("falha simulada", None))

    rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Matriz de outcomes/exit codes — Gate S5.4b, seção 9
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("outcome,reason_code,receipt_ok,expected_receipt_status,expected_exit", [
    ("no_op", refresh_wrapper.REASON_NO_OP, True, "ok", 0),
    ("committed", refresh_wrapper.REASON_COMMITTED, True, "ok", 0),
    ("blocked", refresh_wrapper.REASON_MISSING_FILE_IDS, True, "ok", 3),
    ("blocked", refresh_wrapper.REASON_MISSING_FILE_IDS, False, "failed", 3),
    ("failed", refresh_wrapper.REASON_REFRESH_FAILED, True, "ok", 4),
    ("failed", refresh_wrapper.REASON_REFRESH_FAILED, False, "failed", 4),
    ("no_op", refresh_wrapper.REASON_NO_OP, False, "failed", 5),
    ("committed", refresh_wrapper.REASON_COMMITTED, False, "failed", 5),
], ids=[
    "no_op_ok_exit0", "committed_ok_exit0", "blocked_ok_exit3", "blocked_falha_exit3",
    "failed_ok_exit4", "failed_falha_exit4", "no_op_falha_exit5", "committed_falha_exit5",
])
def test_matriz_operation_outcome_receipt_status_exit_code(
    tmp_path, monkeypatch, outcome, reason_code, receipt_ok, expected_receipt_status, expected_exit,
):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    refresh_result = _refresh_result(outcome, reason_code=reason_code)
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", lambda *a, **k: refresh_result)
    if not receipt_ok:
        monkeypatch.setattr(rsgb, "_publish_receipt_atomic", lambda path, payload: ("falha simulada de publicação", None))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.operation_outcome == outcome
    assert result.reason_code == reason_code
    assert result.receipt_status == expected_receipt_status
    assert rsgb._exit_code_for(result) == expected_exit


def test_validacao_local_falha_e_sempre_blocked_not_attempted_exit2(tmp_path, monkeypatch):
    _boom_refresh(monkeypatch)
    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "bad batch!", "run1")
    assert result.operation_outcome == "blocked"
    assert result.receipt_status == "not_attempted"
    assert rsgb._exit_code_for(result) == 2


def test_committed_receipt_falha_nunca_vira_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    refresh_result = _refresh_result(
        "committed", reason_code=refresh_wrapper.REASON_COMMITTED,
        rows_deleted=5, rows_inserted=6,
        backup_path=str(tmp_path / "backup.json"), backup_sha256="ab" * 32,
        date_from=_D_FROM, date_to=_D_TO, window_days=7,
    )
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", lambda *a, **k: refresh_result)
    monkeypatch.setattr(rsgb, "_publish_receipt_atomic", lambda path, payload: ("falha simulada de publicação", None))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path, "batch1", "run1")

    assert result.operation_outcome == "committed"
    assert result.reason_code == refresh_wrapper.REASON_COMMITTED
    assert result.receipt_status == "failed"
    assert result.backup_path == str(tmp_path / "backup.json")
    assert result.backup_sha256 == "ab" * 32
    assert result.rows_deleted == 5
    assert result.rows_inserted == 6
    assert rsgb._exit_code_for(result) == 5
    assert any("falha simulada de publicação" in p for p in result.problems)


def test_no_op_receipt_falha_nunca_vira_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    refresh_result = _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP)
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", lambda *a, **k: refresh_result)
    monkeypatch.setattr(rsgb, "_publish_receipt_atomic", lambda path, payload: ("falha simulada de publicação", None))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.operation_outcome == "no_op"
    assert result.receipt_status == "failed"
    assert rsgb._exit_code_for(result) == 5


def test_backup_path_e_hash_preservados_quando_refresh_failed_com_backup(tmp_path, monkeypatch):
    """Backup publicado ANTES do DELETE, mas o refresh falha depois (ex.:
    rowcount divergente) -- backup_path/hash continuam no resultado mesmo
    com operation_outcome=failed."""
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    refresh_result = _refresh_result(
        "failed", reason_code=refresh_wrapper.REASON_REFRESH_FAILED,
        backup_path=str(tmp_path / "backup.json"), backup_sha256="cd" * 32,
        problems=["DELETE removeu 3 linha(s), esperado 4"],
    )
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", lambda *a, **k: refresh_result)

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.operation_outcome == "failed"
    assert result.backup_path == str(tmp_path / "backup.json")
    assert result.backup_sha256 == "cd" * 32
    assert result.receipt_status == "ok"  # publicação real do receipt não foi mockada
    assert rsgb._exit_code_for(result) == 4


# ---------------------------------------------------------------------------
# Receipt — publicação atômica (unitário, sem passar por run_shopee_gold_batch)
# ---------------------------------------------------------------------------

def test_publica_receipt_atomico_com_sucesso_e_releitura(tmp_path):
    receipt_path = tmp_path / "receipt.json"
    payload = {"schema_version": 1, "run_id": "r1", "valor": 42}

    problem, warning = rsgb._publish_receipt_atomic(receipt_path, payload)

    assert problem is None
    assert warning is None
    assert receipt_path.exists()
    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert list(tmp_path.glob("*.tmp")) == []


def test_publica_receipt_nunca_sobrescreve(tmp_path):
    receipt_path = tmp_path / "receipt.json"
    payload1 = {"schema_version": 1, "run_id": "r1", "content": "primeiro"}
    payload2 = {"schema_version": 1, "run_id": "r1", "content": "segundo"}

    problem1, _ = rsgb._publish_receipt_atomic(receipt_path, payload1)
    assert problem1 is None

    problem2, _ = rsgb._publish_receipt_atomic(receipt_path, payload2)
    assert problem2 is not None

    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert on_disk["content"] == "primeiro"


def test_publica_receipt_falha_ao_limpar_temp_nao_mascara_sucesso(tmp_path, monkeypatch):
    receipt_path = tmp_path / "receipt.json"
    payload = {"schema_version": 1, "run_id": "r1"}

    real_unlink = Path.unlink

    def fake_unlink(self, *a, **kw):
        if self.name.startswith("receipt.json."):
            raise OSError("falha simulada ao remover temp")
        return real_unlink(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", fake_unlink)

    problem, warning = rsgb._publish_receipt_atomic(receipt_path, payload)

    assert problem is None
    assert warning is not None and "já publicado com sucesso" in warning
    assert receipt_path.exists()
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["run_id"] == "r1"


def test_publica_receipt_remove_temp_na_falha_de_escrita(tmp_path, monkeypatch):
    def boom_fdopen(fd, mode):
        os.close(fd)
        raise OSError("falha simulada")
    monkeypatch.setattr(rsgb.os, "fdopen", boom_fdopen)

    problem, warning = rsgb._publish_receipt_atomic(tmp_path / "receipt.json", {"schema_version": 1, "run_id": "r1"})

    assert problem is not None
    assert list(tmp_path.glob("*.tmp")) == []


def test_publica_receipt_corrida_no_link_bloqueia_sem_sobrescrever(tmp_path, monkeypatch):
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text('{"original": true}', encoding="utf-8")

    def boom_link(src, dst):
        raise FileExistsError()
    monkeypatch.setattr(rsgb.os, "link", boom_link)

    problem, warning = rsgb._publish_receipt_atomic(receipt_path, {"schema_version": 1, "run_id": "r1"})

    assert problem is not None
    assert "nada sobrescrito" in problem
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == {"original": True}


# --- Gate S5.4b.1 (Correção 3): falha principal + falha de cleanup nunca se mascaram ---

def _fake_unlink_fails_for_temp(real_unlink):
    def fake_unlink(self, *a, **kw):
        if ".tmp" in self.name or self.name.endswith(".tmp"):
            raise OSError("falha simulada ao remover temp")
        return real_unlink(self, *a, **kw)
    return fake_unlink


def test_publica_receipt_falha_escrita_e_falha_cleanup_preserva_ambos(tmp_path, monkeypatch):
    def boom_fdopen(fd, mode):
        os.close(fd)
        raise OSError("falha simulada ao escrever")
    monkeypatch.setattr(rsgb.os, "fdopen", boom_fdopen)

    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", _fake_unlink_fails_for_temp(real_unlink))

    problem, warning = rsgb._publish_receipt_atomic(tmp_path / "receipt.json", {"schema_version": 1, "run_id": "r1"})

    assert problem is not None and "escrever" in problem
    assert warning is not None and "publicação também falhou" in warning


def test_publica_receipt_falha_link_e_falha_cleanup_preserva_ambos(tmp_path, monkeypatch):
    def boom_link(src, dst):
        raise OSError("falha simulada de link")
    monkeypatch.setattr(rsgb.os, "link", boom_link)

    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", _fake_unlink_fails_for_temp(real_unlink))

    problem, warning = rsgb._publish_receipt_atomic(tmp_path / "receipt.json", {"schema_version": 1, "run_id": "r1"})

    assert problem is not None and "link" in problem
    assert warning is not None and "publicação também falhou" in warning


def test_publica_receipt_link_funciona_e_cleanup_falha_causa_principal_none(tmp_path, monkeypatch):
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", _fake_unlink_fails_for_temp(real_unlink))

    payload = {"schema_version": 1, "run_id": "r1", "valor": "ok"}
    problem, warning = rsgb._publish_receipt_atomic(tmp_path / "receipt.json", payload)

    assert problem is None
    assert warning is not None and "já publicado com sucesso" in warning
    assert json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8")) == payload


# --- Gate S5.4b.1 (Correção 4): revalidação integral do payload relido ---

def _corrupting_dumps(field_name, corrupted_value):
    real_dumps = json.dumps

    def _dumps(obj, **kwargs):
        corrupted = dict(obj)
        corrupted[field_name] = corrupted_value
        return real_dumps(corrupted, **kwargs)
    return _dumps


def test_receipt_divergencia_em_operation_outcome_marca_falha_sem_remover(tmp_path, monkeypatch):
    payload = {"schema_version": 1, "run_id": "r1", "operation_outcome": "committed"}
    monkeypatch.setattr(rsgb.json, "dumps", _corrupting_dumps("operation_outcome", "no_op"))

    problem, warning = rsgb._publish_receipt_atomic(tmp_path / "receipt.json", payload)

    assert problem is not None and "não bate integralmente" in problem
    assert (tmp_path / "receipt.json").exists()  # nunca removido automaticamente


def test_receipt_divergencia_em_backup_sha256_marca_falha_sem_remover(tmp_path, monkeypatch):
    payload = {"schema_version": 1, "run_id": "r1", "backup_sha256": "aa" * 32}
    monkeypatch.setattr(rsgb.json, "dumps", _corrupting_dumps("backup_sha256", "bb" * 32))

    problem, warning = rsgb._publish_receipt_atomic(tmp_path / "receipt.json", payload)

    assert problem is not None and "não bate integralmente" in problem
    assert (tmp_path / "receipt.json").exists()


def test_receipt_divergencia_em_file_ids_marca_falha_sem_remover(tmp_path, monkeypatch):
    payload = {"schema_version": 1, "run_id": "r1", "file_ids": [1, 2, 3]}
    monkeypatch.setattr(rsgb.json, "dumps", _corrupting_dumps("file_ids", [1, 2]))

    problem, warning = rsgb._publish_receipt_atomic(tmp_path / "receipt.json", payload)

    assert problem is not None and "não bate integralmente" in problem
    assert (tmp_path / "receipt.json").exists()


def test_receipt_publicado_nunca_contem_conteudo_do_backup(tmp_path, monkeypatch):
    _monkeypatch_clock(monkeypatch, datetime(2026, 7, 22, 14, 30, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    refresh_result = _refresh_result(
        "committed", reason_code=refresh_wrapper.REASON_COMMITTED,
        rows_deleted=1, rows_inserted=1, backup_path=str(tmp_path / "b.json"), backup_sha256="ff" * 32,
        date_from=_D_FROM, date_to=_D_TO, window_days=7,
        gmv_before=Decimal("10.50"), gmv_after=Decimal("20.75"), silver_row_count=100,
    )
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", lambda *a, **k: refresh_result)

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1, 2], tmp_path, "batch1", "run1")

    payload = json.loads(Path(result.receipt_path).read_text(encoding="utf-8"))
    for forbidden_key in ("before_records", "planned_after_records", "grain_key", "business_columns"):
        assert forbidden_key not in payload
    assert payload["backup_path"] == str(tmp_path / "b.json")
    assert payload["backup_sha256"] == "ff" * 32
    assert payload["gmv_before"] == "10.50"
    assert payload["gmv_after"] == "20.75"
    assert payload["batch_id_verified"] is False
    assert payload["schema_version"] == 1


def test_receipt_e_deterministico_para_a_mesma_entrada(tmp_path, monkeypatch):
    fixed_clock = datetime(2026, 7, 22, 14, 30, 0, tzinfo=timezone.utc)
    _monkeypatch_clock(monkeypatch, fixed_clock)
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok(commit="deadbeef"))
    refresh_result = _refresh_result(
        "no_op", reason_code=refresh_wrapper.REASON_NO_OP,
        date_from=_D_FROM, date_to=_D_TO, window_days=7, silver_row_count=10,
    )
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed", lambda *a, **k: refresh_result)

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    result_a = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], dir_a, "batch1", "run1")
    result_b = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], dir_b, "batch1", "run1")

    payload_a = json.loads(Path(result_a.receipt_path).read_text(encoding="utf-8"))
    payload_b = json.loads(Path(result_b.receipt_path).read_text(encoding="utf-8"))
    for key in payload_a:
        if key in ("receipt_path",):
            continue
        assert payload_a[key] == payload_b[key], f"campo {key} divergiu entre execuções idênticas"


# ---------------------------------------------------------------------------
# Git / tempo
# ---------------------------------------------------------------------------

def test_git_commit_disponivel(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok(commit="cafef00d"))
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed",
                         lambda *a, **k: _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.git_commit == "cafef00d"
    assert not any("git_commit" in w for w in result.warnings)


def test_git_ausente_repositorio_vira_warning_nunca_bloqueia(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_not_a_repo)
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed",
                         lambda *a, **k: _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.operation_outcome == "no_op"  # nunca bloqueado por causa disso
    assert result.git_commit is None
    assert any("git_commit indisponível" in w for w in result.warnings)
    assert "fatal: not a git repository" not in " ".join(result.warnings)


def test_git_indisponivel_ou_timeout_vira_warning(tmp_path, monkeypatch):
    def boom_git(args, cwd):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)
    monkeypatch.setattr(rsgb, "_run_git_subprocess", boom_git)
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed",
                         lambda *a, **k: _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.operation_outcome == "no_op"
    assert result.git_commit is None
    assert result.git_dirty is None
    assert any("indisponível" in w for w in result.warnings)


def test_working_tree_dirty_e_so_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok(dirty_output=" M docs/x.md\n?? novo.txt\n"))
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed",
                         lambda *a, **k: _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.operation_outcome == "no_op"  # dirty NUNCA bloqueia
    assert result.git_dirty is True
    assert isinstance(result.git_dirty, bool)
    assert any(
        "working tree possui alterações não commitadas" in w and "git_commit pode não" in w
        for w in result.warnings
    )
    combined = json.dumps(rsgb._result_to_dict(result)) + " ".join(result.warnings)
    assert "docs/x.md" not in combined
    assert "novo.txt" not in combined
    assert "M docs" not in combined  # nunca a saída crua de git status


def test_timestamps_sao_utc_iso(tmp_path, monkeypatch):
    _monkeypatch_clock(monkeypatch, datetime(2026, 7, 22, 14, 30, 0, tzinfo=timezone.utc),
                       datetime(2026, 7, 22, 14, 30, 7, tzinfo=timezone.utc))
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed",
                         lambda *a, **k: _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.started_at_utc == "2026-07-22T14:30:00Z"
    assert result.finished_at_utc == "2026-07-22T14:30:07Z"
    assert result.duration_seconds == pytest.approx(7.0)


def test_duracao_nunca_negativa(tmp_path, monkeypatch):
    same_instant = datetime(2026, 7, 22, 14, 30, 0, tzinfo=timezone.utc)
    _monkeypatch_clock(monkeypatch, same_instant)
    monkeypatch.setattr(rsgb, "_run_git_subprocess", _fake_run_git_ok())
    monkeypatch.setattr(rsgb.refresh_wrapper, "refresh_shopee_window_if_needed",
                         lambda *a, **k: _refresh_result("no_op", reason_code=refresh_wrapper.REASON_NO_OP))

    result = rsgb.run_shopee_gold_batch(_WRITE_URL, _READ_URL, [1], tmp_path, "batch1", "run1")

    assert result.duration_seconds is not None
    assert result.duration_seconds >= 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_secret_setup(monkeypatch):
    monkeypatch.setattr(rsgb.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        rsgb.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": _WRITE_URL, "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(rsgb.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: _WRITE_URL)


def test_run_cli_valida_ids_antes_de_secret(monkeypatch, tmp_path):
    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com batch_id inválido")
    monkeypatch.setattr(rsgb.window_write_conn, "load_window_write_secret", boom_secret)

    rc = rsgb.run_cli(["1"], str(tmp_path), "bad id!", "run1", as_json=True)

    assert rc == 2


def test_run_cli_valida_artifacts_dir_antes_de_secret(monkeypatch):
    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com artifacts_dir inválido")
    monkeypatch.setattr(rsgb.window_write_conn, "load_window_write_secret", boom_secret)

    rc = rsgb.run_cli(["1"], "relativo", "batch1", "run1", as_json=True)

    assert rc == 2


def test_run_cli_sem_datamart_url_nao_le_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(rsgb.settings, "datamart_database_url", "")
    monkeypatch.setattr(rsgb.settings, "datamart_host", "")
    monkeypatch.setattr(rsgb.settings, "datamart_db", "")

    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret sem DATAMART_DATABASE_URL")
    monkeypatch.setattr(rsgb.window_write_conn, "load_window_write_secret", boom_secret)

    rc = rsgb.run_cli(["1"], str(tmp_path), "batch1", "run1", as_json=True)

    assert rc == 2


def test_run_cli_usa_run_shopee_gold_batch(monkeypatch, tmp_path):
    _cli_secret_setup(monkeypatch)
    calls = {}

    def fake_run(write_url, datamart_read_url, file_ids, artifacts_dir, batch_id, run_id, **kwargs):
        calls["args"] = (write_url, datamart_read_url, file_ids, artifacts_dir, batch_id, run_id)
        return rsgb.ShopeeGoldBatchResult(operation_outcome="no_op", reason_code=refresh_wrapper.REASON_NO_OP, receipt_status="ok")
    monkeypatch.setattr(rsgb, "run_shopee_gold_batch", fake_run)

    rc = rsgb.run_cli(["1", "2"], str(tmp_path), "batch1", "run1", as_json=True)

    assert rc == 0
    assert calls["args"] == (_WRITE_URL, _READ_URL, [1, 2], tmp_path, "batch1", "run1")


def test_run_cli_json_e_documento_unico_parseavel(monkeypatch, tmp_path, capsys):
    _cli_secret_setup(monkeypatch)
    monkeypatch.setattr(rsgb, "run_shopee_gold_batch", lambda *a, **k: rsgb.ShopeeGoldBatchResult(
        operation_outcome="committed", reason_code=refresh_wrapper.REASON_COMMITTED, receipt_status="ok",
        batch_id="batch1", run_id="run1", file_ids=[1, 2],
        date_from=_D_FROM, date_to=_D_TO, window_days=7,
        rows_deleted=3, rows_inserted=4, gmv_before=Decimal("1.50"), gmv_after=Decimal("2.75"),
        backup_path=str(tmp_path / "b.json"), backup_sha256="aa" * 32,
        receipt_path=str(tmp_path / "r.json"),
    ))

    rc = rsgb.run_cli(["1", "2"], str(tmp_path), "batch1", "run1", as_json=True)

    assert rc == 0
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["operation_outcome"] == "committed"
    assert doc["date_from"] == "2026-06-15"
    assert doc["gmv_before"] == "1.50"
    assert doc["batch_id_verified"] is False


def test_run_cli_stdout_json_unico_quando_receipt_falha(monkeypatch, tmp_path, capsys):
    _cli_secret_setup(monkeypatch)
    monkeypatch.setattr(rsgb, "run_shopee_gold_batch", lambda *a, **k: rsgb.ShopeeGoldBatchResult(
        operation_outcome="committed", reason_code=refresh_wrapper.REASON_COMMITTED, receipt_status="failed",
        batch_id="batch1", run_id="run1", backup_path=str(tmp_path / "b.json"), backup_sha256="bb" * 32,
        problems=["falha simulada ao publicar o receipt"],
    ))

    rc = rsgb.run_cli(["1"], str(tmp_path), "batch1", "run1", as_json=True)

    assert rc == 5
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["operation_outcome"] == "committed"
    assert doc["receipt_status"] == "failed"
    assert doc["backup_path"] == str(tmp_path / "b.json")


def test_run_cli_json_nunca_contem_pii_ou_infraestrutura(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rsgb.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        rsgb.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer:s3cr3t@prod-db.internal:5432/datamart", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(rsgb.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: "postgresql://writer:s3cr3t@prod-db.internal:5432/datamart")
    monkeypatch.setattr(rsgb, "run_shopee_gold_batch", lambda *a, **k: rsgb.ShopeeGoldBatchResult(
        operation_outcome="blocked", reason_code=refresh_wrapper.REASON_MISSING_FILE_IDS, receipt_status="not_attempted",
        batch_id="batch1", run_id="run1", problems=["1 de 2 file_id(s) ainda não presentes na Silver"],
    ))

    rc = rsgb.run_cli(["1", "999"], str(tmp_path), "batch1", "run1", as_json=True)

    assert rc == 3
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    for forbidden in ("order_id", "cpf", "filename", "s3cr3t", "prod-db.internal", "password"):
        assert forbidden not in combined


def test_run_cli_preflight_bloqueado_stderr_e_exit2(monkeypatch, tmp_path, capsys):
    _cli_secret_setup(monkeypatch)
    monkeypatch.setattr(rsgb, "run_shopee_gold_batch", lambda *a, **k: rsgb.ShopeeGoldBatchResult(
        operation_outcome="blocked", reason_code=refresh_wrapper.REASON_PREFLIGHT_BLOCKED, receipt_status="not_attempted",
        batch_id="batch1", run_id="run1", problems=["preflight bloqueado — refresh NÃO executado."],
        warnings=["aviso sanitizado de exemplo"],
    ))

    rc = rsgb.run_cli(["1"], str(tmp_path), "batch1", "run1", as_json=True)

    assert rc == 2
    captured = capsys.readouterr()
    assert "AVISO: aviso sanitizado de exemplo" in captured.err
    assert "PREFLIGHT: preflight bloqueado" in captured.err
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    json.loads(lines[0])


# ---------------------------------------------------------------------------
# Regressão estática — nenhum SQL/conexão própria, nenhuma chamada direta a
# resolvedor/preflight/refresh/diagnose/restore/sync, nenhuma função privada
# do S5.2/S5.3 importada. Escaneia só o CORPO das funções (nunca o docstring
# do módulo, que legitimamente cita esses nomes em prosa).
# ---------------------------------------------------------------------------

_FORBIDDEN_TOKENS = (
    "import psycopg2",
    "cur.execute(",
    "cursor()",
    "conn.commit(",
    "diagnose_shopee_window(",
    "execute_shopee_window_refresh(",
    "execute_shopee_window_restore(",
    "sync_region_if_needed(",
    "sync_region_daily(",
    "resolve_shopee_batch_window(",
    "run_window_preflight(",
    "shopee_batch_window._parse_cli_file_ids(",
    "_resolve_shopee_batch_window_after_preflight(",
    "time.sleep(",
    "while True",
    "shell=True",
)


def _code_only_source():
    names = (
        "run_shopee_gold_batch", "run_cli", "main", "_result_to_dict", "_print_json",
        "_print_human", "_emit", "_build_receipt_payload", "_publish_receipt_atomic",
        "_blocked_result", "_exit_code_for", "_validate_id_token", "_validate_artifacts_dir",
        "_artifact_paths", "_existing_artifact_problem", "_probe_artifacts_dir_writable",
        "_parse_cli_file_ids", "_collect_git_commit", "_collect_git_dirty", "_run_git_subprocess",
    )
    return "\n".join(inspect.getsource(getattr(rsgb, name)) for name in names)


def test_regressao_estatica_sem_simbolos_proibidos():
    source = _code_only_source()
    for forbidden in _FORBIDDEN_TOKENS:
        assert forbidden not in source, f"símbolo proibido {forbidden!r} encontrado em run_shopee_gold_batch.py"


def test_regressao_estatica_usa_apenas_o_wrapper_publico_s53():
    source = _code_only_source()
    assert "refresh_wrapper.refresh_shopee_window_if_needed(" in source


def test_regressao_estatica_git_subprocess_sem_shell_true_e_com_timeout():
    source = inspect.getsource(rsgb._run_git_subprocess)
    assert "shell=False" in source
    assert "timeout=" in source
