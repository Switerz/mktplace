"""
Testes de pipelines/ingestion/shopee_raw/write_conn.py — Fase Raw Shopee 2.

Usa conexões psycopg2 falsas e um `_run_git` falso — nenhum banco real e
nenhum repositório git real são tocados. Nenhuma credencial real é usada.
"""
from __future__ import annotations

import subprocess

import pytest

from pipelines.ingestion.shopee_raw import write_conn as wc


# --- load_write_secret --------------------------------------------------------

def _fake_git_ok(args, cwd):
    """check-ignore retorna 0 (ignorado); ls-files retorna 1 (não rastreado)."""
    if args[0] == "check-ignore":
        return subprocess.CompletedProcess(args, returncode=0)
    return subprocess.CompletedProcess(args, returncode=1)


def test_load_write_secret_arquivo_inexistente(tmp_path):
    with pytest.raises(wc.SecretLoadError, match="não encontrado"):
        wc.load_write_secret(tmp_path / "nao_existe.local", tmp_path)


def test_load_write_secret_bloqueia_se_nao_ignorado(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    secret_path.write_text("DATAMART_SHOPEE_WRITE_URL=x\nI_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1\n")

    def fake_run_git(args, cwd):
        return subprocess.CompletedProcess(args, returncode=1)  # nada ignorado

    monkeypatch.setattr(wc, "_run_git", fake_run_git)
    with pytest.raises(wc.SecretLoadError, match="gitignore"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_bloqueia_se_rastreado(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    secret_path.write_text("DATAMART_SHOPEE_WRITE_URL=x\nI_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1\n")

    def fake_run_git(args, cwd):
        return subprocess.CompletedProcess(args, returncode=0)  # ignorado E "rastreado" (ls-files ok)

    monkeypatch.setattr(wc, "_run_git", fake_run_git)
    with pytest.raises(wc.SecretLoadError, match="RASTREADO"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_bloqueia_chaves_faltando(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    secret_path.write_text("DATAMART_SHOPEE_WRITE_URL=postgresql://writer@host/db\n")
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wc.SecretLoadError, match="faltando"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_bloqueia_chave_extra(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    secret_path.write_text(
        "DATAMART_SHOPEE_WRITE_URL=postgresql://writer@host/db\n"
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1\n"
        "ALGO_INESPERADO=valor\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wc.SecretLoadError, match="inesperada"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_bloqueia_consentimento_errado(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    secret_path.write_text(
        "DATAMART_SHOPEE_WRITE_URL=postgresql://writer@host/db\n"
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW=0\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wc.SecretLoadError, match="I_UNDERSTAND_THIS_WRITES_DATAMART_RAW"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_sucesso(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    secret_path.write_text(
        "DATAMART_SHOPEE_WRITE_URL=postgresql://writer:S3nh4@host/db\n"
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    values = wc.load_write_secret(secret_path, tmp_path)
    assert set(values.keys()) == wc.EXPECTED_SECRET_KEYS
    assert values["DATAMART_SHOPEE_WRITE_URL"] == "postgresql://writer:S3nh4@host/db"


# --- validate_write_guardrails -------------------------------------------------

def test_validate_write_guardrails_bloqueia_url_vazia():
    with pytest.raises(wc.SecretLoadError, match="vazio"):
        wc.validate_write_guardrails({"DATAMART_SHOPEE_WRITE_URL": ""}, "postgresql://read@host/db")


def test_validate_write_guardrails_bloqueia_reuso_da_url_de_leitura():
    same = "postgresql://postgres:segredo@host/datamart"
    with pytest.raises(wc.SecretLoadError, match="nunca reutilizar"):
        wc.validate_write_guardrails({"DATAMART_SHOPEE_WRITE_URL": same}, same)


def test_validate_write_guardrails_ok():
    url = wc.validate_write_guardrails(
        {"DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db"},
        "postgresql://postgres@host/db",
    )
    assert url == "postgresql://writer@host/db"


# --- sanitize_error_message ----------------------------------------------------

def test_sanitize_error_message_remove_usuario_senha():
    exc = RuntimeError("connection to postgresql://user:S3nh4Secreta@host:5432/db failed")
    msg = wc.sanitize_error_message(exc)
    assert "S3nh4Secreta" not in msg
    assert "user" not in msg or "<redacted>" in msg
    assert "<redacted>@host" in msg


def test_sanitize_error_message_sem_credencial_fica_igual():
    exc = RuntimeError("timeout genérico sem segredo nenhum")
    assert wc.sanitize_error_message(exc) == str(exc)


# --- run_preflight (psycopg2 falso) --------------------------------------------

class ScriptedCursor:
    """Cursor falso que decide a resposta pelo CONTEÚDO do SQL (substring),
    não pela ordem — mais robusto a pequenos reordenamentos das queries
    reais em write_conn.py. `responses` é uma lista de
    (substring_maiuscula, "one"|"all"|"raise", valor)."""

    def __init__(self, responses):
        self.responses = responses
        self._pending = ("one", None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split()).upper()
        for substring, kind, value in self.responses:
            if substring in norm:
                self._pending = (kind, value)
                return
        self._pending = ("one", None)

    def fetchone(self):
        kind, value = self._pending
        if kind == "raise":
            raise value
        return value

    def fetchall(self):
        kind, value = self._pending
        if kind == "raise":
            raise value
        return value


class ScriptedConn:
    def __init__(self, responses):
        self.responses = responses
        self.closed_calls = 0

    def set_session(self, readonly=None, autocommit=None):
        pass

    def cursor(self):
        return ScriptedCursor(self.responses)

    def close(self):
        self.closed_calls += 1


def _identity_responses(db="datamart", port=5432, sysid="sysid-A"):
    return [
        ("CURRENT_DATABASE()", "one", (db, port)),
        ("PG_CONTROL_SYSTEM", "one", (sysid,)),
    ]


_MAIN_HAPPY_RESPONSES = [
    ("SELECT CURRENT_USER", "one", ("writer_role",)),
    ("FROM PG_ROLES", "one", (False, False, False, False, False)),
    ("RDS_SUPERUSER", "one", (True,)),
    ("HAS_SCHEMA_PRIVILEGE", "one", (True, True)),
    ("INFORMATION_SCHEMA.TABLES", "all", []),
    ("PG_STAT_SSL", "one", (True,)),
    ("SERVER_VERSION", "one", ("16.4",)),
]


def _install_fake_connect(
    monkeypatch,
    main_responses=_MAIN_HAPPY_RESPONSES,
    write_identity=("datamart", 5432, "sysid-A"),
    read_identity=("datamart", 5432, "sysid-A"),
    raise_on_call=None,
):
    """Simula as 3 conexões de run_preflight, na ordem real: identidade da
    escrita, identidade da leitura, conexão principal (role/permissões)."""
    calls = {"n": 0}

    def fake_connect(url, connect_timeout=15):
        calls["n"] += 1
        if raise_on_call == calls["n"]:
            raise RuntimeError(f"boom postgresql://u:p@h/db (call {calls['n']})")
        if calls["n"] == 1:
            return ScriptedConn(_identity_responses(*write_identity))
        if calls["n"] == 2:
            return ScriptedConn(_identity_responses(*read_identity))
        return ScriptedConn(main_responses)

    monkeypatch.setattr(wc.psycopg2, "connect", fake_connect)
    return "postgresql://writer@datamart-gogroup.example.rds.amazonaws.com:5432/datamart"


_READ_URL = "postgresql://read@datamart-gogroup.example.rds.amazonaws.com:5432/datamart"


def test_run_preflight_bloqueia_conexao_com_falha_na_identidade(monkeypatch):
    write_url = _install_fake_connect(monkeypatch, raise_on_call=1)
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    assert report.ok is False
    assert any("falha ao conectar" in r for r in report.blocking_reasons)
    assert "p@h" not in " ".join(report.blocking_reasons)


def test_run_preflight_bloqueia_conexao_com_falha_na_conexao_principal(monkeypatch):
    write_url = _install_fake_connect(monkeypatch, raise_on_call=3)
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    assert report.ok is False
    assert any("falha ao conectar" in r for r in report.blocking_reasons)


def test_run_preflight_bloqueia_rolsuper_true(monkeypatch):
    responses = [r for r in _MAIN_HAPPY_RESPONSES if r[0] != "FROM PG_ROLES"]
    responses.insert(1, ("FROM PG_ROLES", "one", (True, False, False, False, False)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    assert report.ok is False
    assert "rolsuper=true" in report.blocking_reasons


def test_run_preflight_bloqueia_cluster_fisico_diferente_do_esperado(monkeypatch):
    """system_identifier diferente = servidores fisicamente diferentes,
    mesmo que host/porta/database em texto pareçam iguais."""
    write_url = _install_fake_connect(
        monkeypatch,
        write_identity=("datamart", 5432, "sysid-A"),
        read_identity=("datamart", 5432, "sysid-B"),
    )
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    assert report.ok is False
    assert any("cluster físico" in r for r in report.blocking_reasons)


def test_run_preflight_ok_quando_hosts_diferem_mas_cluster_fisico_e_o_mesmo(monkeypatch):
    """Caso real observado: endpoint privado (VPN) vs endpoint público do
    mesmo RDS resolvem IPs diferentes, mas system_identifier bate — não
    deve bloquear."""
    write_url = _install_fake_connect(
        monkeypatch,
        write_identity=("datamart", 5432, "sysid-A"),
        read_identity=("datamart", 5432, "sysid-A"),
    )
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    assert report.ok is True
    assert report.safe_summary["target_confirmado"] is True


def test_run_preflight_usa_fallback_quando_system_identifier_indisponivel(monkeypatch):
    write_url = _install_fake_connect(
        monkeypatch,
        write_identity=("datamart", 5432, None),
        read_identity=("datamart", 5432, None),
    )
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    assert report.ok is True
    assert "database+porta" in report.safe_summary["target_check_method"]


def test_run_preflight_bloqueia_se_tabelas_ja_existem_antes_do_ddl(monkeypatch):
    responses = [r for r in _MAIN_HAPPY_RESPONSES if r[0] != "INFORMATION_SCHEMA.TABLES"]
    responses.append(("INFORMATION_SCHEMA.TABLES", "all", [("shopee_ingestion_file",)]))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    assert report.ok is False
    assert any("já existem" in r for r in report.blocking_reasons)


def test_run_preflight_bloqueia_se_tabelas_nao_existem_antes_da_carga(monkeypatch):
    write_url = _install_fake_connect(monkeypatch)
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=True)
    assert report.ok is False
    assert any("ainda não existem" in r for r in report.blocking_reasons)


def test_run_preflight_ok_quando_tabelas_existem_e_carga_esperada(monkeypatch):
    responses = [r for r in _MAIN_HAPPY_RESPONSES if r[0] != "INFORMATION_SCHEMA.TABLES"]
    responses.append((
        "INFORMATION_SCHEMA.TABLES", "all",
        [
            ("shopee_ads_export",), ("shopee_ingestion_file",),
            ("shopee_order_item_export",), ("shopee_shop_stats_export",),
        ],
    ))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=True)
    assert report.ok is True
    assert report.blocking_reasons == []


def test_run_preflight_rds_superuser_e_apenas_aviso_nao_bloqueante(monkeypatch):
    write_url = _install_fake_connect(monkeypatch)
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    assert report.ok is True
    assert any("rds_superuser" in w for w in report.warnings)


def test_run_preflight_nunca_expoe_usuario_ou_senha_no_summary(monkeypatch):
    write_url = _install_fake_connect(monkeypatch)
    report = wc.run_preflight(write_url, _READ_URL, expect_tables_exist=False)
    dumped = str(report.safe_summary) + str(report.warnings) + str(report.blocking_reasons)
    assert "writer" not in dumped
    assert "datamart-gogroup" not in dumped


def test_run_preflight_fecha_todas_as_conexoes(monkeypatch):
    conns = []
    calls = {"n": 0}

    def fake_connect(url, connect_timeout=15):
        calls["n"] += 1
        responses = _identity_responses() if calls["n"] <= 2 else _MAIN_HAPPY_RESPONSES
        c = ScriptedConn(responses)
        conns.append(c)
        return c

    monkeypatch.setattr(wc.psycopg2, "connect", fake_connect)
    wc.run_preflight("postgresql://writer@h/db", "postgresql://read@h/db", expect_tables_exist=False)
    assert len(conns) == 3
    assert all(c.closed_calls == 1 for c in conns)


# --- advisory lock --------------------------------------------------------------

def test_try_acquire_advisory_lock_true_false():
    conn_locked = ScriptedConn([("PG_TRY_ADVISORY_LOCK", "one", (True,))])
    assert wc.try_acquire_advisory_lock(conn_locked) is True

    conn_busy = ScriptedConn([("PG_TRY_ADVISORY_LOCK", "one", (False,))])
    assert wc.try_acquire_advisory_lock(conn_busy) is False


def test_release_advisory_lock_nao_levanta():
    conn = ScriptedConn([("PG_ADVISORY_UNLOCK", "one", (True,))])
    wc.release_advisory_lock(conn)  # não deve levantar exceção
