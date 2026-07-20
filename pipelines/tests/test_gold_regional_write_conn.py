"""
Testes de pipelines/ingestion/gold_regional/write_conn.py — Gate 6A.

Usa conexões psycopg2 falsas e um `_run_git` falso — nenhum banco real e
nenhum repositório git real são tocados. Nenhuma credencial real é usada.
"""
from __future__ import annotations

import subprocess

import pytest

from pipelines.ingestion.gold_regional import write_conn as wc


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
    secret_path = tmp_path / ".env.gold-write.local"
    secret_path.write_text("DATAMART_GOLD_WRITE_URL=x\nI_UNDERSTAND_THIS_WRITES_DATAMART_GOLD=1\n")

    def fake_run_git(args, cwd):
        return subprocess.CompletedProcess(args, returncode=1)  # nada ignorado

    monkeypatch.setattr(wc, "_run_git", fake_run_git)
    with pytest.raises(wc.SecretLoadError, match="gitignore"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_bloqueia_se_rastreado(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-write.local"
    secret_path.write_text("DATAMART_GOLD_WRITE_URL=x\nI_UNDERSTAND_THIS_WRITES_DATAMART_GOLD=1\n")

    def fake_run_git(args, cwd):
        return subprocess.CompletedProcess(args, returncode=0)  # ignorado E "rastreado" (ls-files ok)

    monkeypatch.setattr(wc, "_run_git", fake_run_git)
    with pytest.raises(wc.SecretLoadError, match="RASTREADO"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_bloqueia_chaves_faltando(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-write.local"
    secret_path.write_text("DATAMART_GOLD_WRITE_URL=postgresql://writer@host/db\n")
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wc.SecretLoadError, match="faltando"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_bloqueia_chave_extra(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WRITE_URL=postgresql://writer@host/db\n"
        "I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD=1\n"
        "ALGO_INESPERADO=valor\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wc.SecretLoadError, match="inesperada"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_bloqueia_consentimento_errado(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WRITE_URL=postgresql://writer@host/db\n"
        "I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD=0\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wc.SecretLoadError, match="I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD"):
        wc.load_write_secret(secret_path, tmp_path)


def test_load_write_secret_sucesso(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WRITE_URL=postgresql://writer:S3nh4@host/db\n"
        "I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD=1\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    values = wc.load_write_secret(secret_path, tmp_path)
    assert set(values.keys()) == wc.EXPECTED_SECRET_KEYS
    assert values["DATAMART_GOLD_WRITE_URL"] == "postgresql://writer:S3nh4@host/db"


# --- validate_write_guardrails -------------------------------------------------

def test_validate_write_guardrails_bloqueia_url_vazia():
    with pytest.raises(wc.SecretLoadError, match="vazio"):
        wc.validate_write_guardrails({"DATAMART_GOLD_WRITE_URL": ""}, "postgresql://read@host/db")


def test_validate_write_guardrails_bloqueia_reuso_da_url_de_leitura():
    same = "postgresql://postgres:segredo@host/datamart"
    with pytest.raises(wc.SecretLoadError, match="nunca reutilizar"):
        wc.validate_write_guardrails({"DATAMART_GOLD_WRITE_URL": same}, same)


def test_validate_write_guardrails_ok():
    url = wc.validate_write_guardrails(
        {"DATAMART_GOLD_WRITE_URL": "postgresql://writer@host/db"},
        "postgresql://postgres@host/db",
    )
    assert url == "postgresql://writer@host/db"


# --- sanitize_error_message ----------------------------------------------------

def test_sanitize_error_message_remove_usuario_senha():
    exc = RuntimeError("connection to postgresql://user:S3nh4Secreta@host:5432/db failed")
    msg = wc.sanitize_error_message(exc)
    assert "S3nh4Secreta" not in msg
    assert "<redacted>@host" in msg


def test_sanitize_error_message_sem_credencial_fica_igual():
    exc = RuntimeError("timeout genérico sem segredo nenhum")
    assert wc.sanitize_error_message(exc) == str(exc)


def test_sanitize_error_message_erro_nativo_de_autenticacao_libpq_nao_vaza_host_ip_usuario():
    """Regressao critica (achado real no Gate 6A): a mensagem nativa do
    libpq/psycopg2 para falha de autenticacao NAO segue o formato
    scheme://user:pass@host que a regex de DSN cobre — ela expoe host, IP,
    porta, usuario e database em texto puro. Esta e a mensagem real
    (com host/IP fabricados) que vazou antes desta correcao."""
    exc = RuntimeError(
        'connection to server at "datamart-gogroup.cvfsx8dkoxhw.us-east-1.rds.amazonaws.com" '
        '(172.30.1.57), port 5432 failed: FATAL:  password authentication failed for user "postgres"\n'
        'connection to server at "datamart-gogroup.cvfsx8dkoxhw.us-east-1.rds.amazonaws.com" '
        '(172.30.1.57), port 5432 failed: FATAL:  no pg_hba.conf entry for host "172.30.153.32", '
        'user "postgres", database "datamart", no encryption\n'
    )
    msg = wc.sanitize_error_message(exc)
    assert "datamart-gogroup" not in msg
    assert "172.30.1.57" not in msg
    assert "172.30.153.32" not in msg
    assert "postgres" not in msg
    assert "datamart" not in msg
    assert "autentica" in msg.lower()


def test_sanitize_error_message_erro_nativo_pg_hba_sem_autenticacao_tambem_nao_vaza():
    exc = RuntimeError(
        'connection to server at "10.0.0.5" port 5432 failed: FATAL:  '
        'no pg_hba.conf entry for host "10.0.0.9", user "app_writer", database "prod", no encryption'
    )
    msg = wc.sanitize_error_message(exc)
    assert "10.0.0.5" not in msg
    assert "10.0.0.9" not in msg
    assert "app_writer" not in msg
    assert "prod" not in msg


def test_sanitize_error_message_qualquer_mensagem_com_ip_e_tratada_com_seguranca_mesmo_sem_categoria_conhecida():
    """Fallback defensivo: uma mensagem nativa de conexao ainda nao prevista
    explicitamente (mas que contenha um IP) nunca deve ser ecoada crua."""
    exc = RuntimeError('unexpected libpq message mentioning 203.0.113.42 in the middle')
    msg = wc.sanitize_error_message(exc)
    assert "203.0.113.42" not in msg


# --- categorize_connection_failure / restricted_preflight_summary -------------

def test_categorize_connection_failure_auth_failed():
    assert wc.categorize_connection_failure("falha de autenticação (usuário/senha incorretos)") == "auth_failed"
    assert wc.categorize_connection_failure("authentication failed for user x") == "auth_failed"


def test_categorize_connection_failure_ssl_required_or_failed():
    assert wc.categorize_connection_failure("no encryption, pg_hba.conf entry missing") == "ssl_required_or_failed"
    assert wc.categorize_connection_failure("SSL connection required") == "ssl_required_or_failed"


def test_categorize_connection_failure_network_unreachable():
    assert wc.categorize_connection_failure("timeout expired ao alcançar o servidor") == "network_unreachable"
    assert wc.categorize_connection_failure("connection refused") == "network_unreachable"


def test_categorize_connection_failure_unknown_fallback():
    assert wc.categorize_connection_failure("algo completamente inesperado") == "unknown_connection_error"


def test_categorize_connection_failure_todas_as_categorias_pertencem_ao_vocabulario_fechado():
    for msg in ["autenticação", "ssl", "timeout", "algo aleatorio"]:
        assert wc.categorize_connection_failure(msg) in wc.CONNECTION_FAILURE_CATEGORIES


def test_restricted_preflight_summary_conexao_falhou_so_expoe_categoria():
    report = wc.PreflightReport(
        ok=False,
        blocking_reasons=["falha ao conectar: OperationalError: falha de autenticação (usuário/senha incorretos)"],
    )
    summary = wc.restricted_preflight_summary(report)
    assert summary == {"connected": False, "failure_category": "auth_failed"}


def test_restricted_preflight_summary_conectou_mas_e_replica():
    report = wc.PreflightReport(
        ok=False,
        blocking_reasons=["pg_is_in_recovery()=true — conexao de escrita aponta para uma replica, nao o primary"],
        safe_summary={"pg_is_in_recovery": True},
    )
    summary = wc.restricted_preflight_summary(report)
    assert summary["connected"] is True
    assert summary["failure_category"] == "not_primary"
    assert summary["pg_is_in_recovery"] is True


def test_restricted_preflight_summary_tudo_ok():
    report = wc.PreflightReport(
        ok=True,
        blocking_reasons=[],
        safe_summary={
            "pg_is_in_recovery": False,
            "target_confirmado": True,
            "rolsuper": False,
            "can_create_in_gold": True,
            "can_use_gold": True,
        },
    )
    summary = wc.restricted_preflight_summary(report)
    assert summary == {
        "connected": True,
        "failure_category": None,
        "pg_is_in_recovery": False,
        "cluster_fisico_esperado": True,
        "role_validada": True,
        "schema_tabela_alvo_ok": True,
    }


def test_restricted_preflight_summary_role_invalida_quando_rolsuper_true():
    report = wc.PreflightReport(
        ok=False,
        blocking_reasons=["rolsuper=true"],
        safe_summary={
            "pg_is_in_recovery": False,
            "target_confirmado": True,
            "rolsuper": True,
            "can_create_in_gold": True,
            "can_use_gold": True,
        },
    )
    summary = wc.restricted_preflight_summary(report)
    assert summary["role_validada"] is False
    assert summary["schema_tabela_alvo_ok"] is False  # report.ok e False


def test_restricted_preflight_summary_nunca_expoe_chaves_fora_do_vocabulario_fechado():
    allowed_keys = {
        "connected", "failure_category", "pg_is_in_recovery",
        "cluster_fisico_esperado", "role_validada", "schema_tabela_alvo_ok",
    }
    report = wc.PreflightReport(
        ok=True,
        safe_summary={
            "pg_is_in_recovery": False, "target_confirmado": True,
            "rolsuper": False, "can_create_in_gold": True, "can_use_gold": True,
            "server_version": "16.4", "ssl_in_use": True,  # nao podem vazar para o resumo restrito
        },
    )
    summary = wc.restricted_preflight_summary(report)
    assert set(summary.keys()) <= allowed_keys
    assert "server_version" not in summary
    assert "ssl_in_use" not in summary


def test_advisory_lock_key_e_exclusiva_do_modulo_shopee_raw():
    """Regressao critica: as duas ingestoes de escrita nunca podem
    compartilhar a mesma chave de advisory lock — senao uma bloquearia a
    outra por engano (ou, pior, uma acharia que adquiriu o lock da outra)."""
    from pipelines.ingestion.shopee_raw import write_conn as shopee_wc
    assert wc.ADVISORY_LOCK_KEY != shopee_wc.ADVISORY_LOCK_KEY


def test_expected_secret_keys_nao_colidem_com_shopee_raw():
    from pipelines.ingestion.shopee_raw import write_conn as shopee_wc
    assert wc.EXPECTED_SECRET_KEYS.isdisjoint(shopee_wc.EXPECTED_SECRET_KEYS)


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
    ("PG_IS_IN_RECOVERY", "one", (False,)),
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


def test_run_preflight_bloqueia_replica(monkeypatch):
    """A checagem que primeiro bloqueou o Gate 6A nesta rodada real (a unica
    conexao disponivel era a replica) — precisa ficar explicita e testada,
    nao so descoberta ad-hoc contra o banco real."""
    responses = [r for r in _MAIN_HAPPY_RESPONSES if r[0] != "PG_IS_IN_RECOVERY"]
    responses.insert(0, ("PG_IS_IN_RECOVERY", "one", (True,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=False)
    assert report.ok is False
    assert any("recovery" in r or "replica" in r for r in report.blocking_reasons)


def test_run_preflight_bloqueia_conexao_com_falha_na_identidade(monkeypatch):
    write_url = _install_fake_connect(monkeypatch, raise_on_call=1)
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=False)
    assert report.ok is False
    assert any("falha ao conectar" in r for r in report.blocking_reasons)
    assert "p@h" not in " ".join(report.blocking_reasons)


def test_run_preflight_bloqueia_conexao_com_falha_na_conexao_principal(monkeypatch):
    write_url = _install_fake_connect(monkeypatch, raise_on_call=3)
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=False)
    assert report.ok is False
    assert any("falha ao conectar" in r for r in report.blocking_reasons)


def test_run_preflight_bloqueia_rolsuper_true(monkeypatch):
    responses = [r for r in _MAIN_HAPPY_RESPONSES if r[0] != "FROM PG_ROLES"]
    responses.insert(2, ("FROM PG_ROLES", "one", (True, False, False, False, False)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=False)
    assert report.ok is False
    assert "rolsuper=true" in report.blocking_reasons


def test_run_preflight_bloqueia_cluster_fisico_diferente_do_esperado(monkeypatch):
    write_url = _install_fake_connect(
        monkeypatch,
        write_identity=("datamart", 5432, "sysid-A"),
        read_identity=("datamart", 5432, "sysid-B"),
    )
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=False)
    assert report.ok is False
    assert any("cluster físico" in r for r in report.blocking_reasons)


def test_run_preflight_ok_quando_hosts_diferem_mas_cluster_fisico_e_o_mesmo(monkeypatch):
    write_url = _install_fake_connect(
        monkeypatch,
        write_identity=("datamart", 5432, "sysid-A"),
        read_identity=("datamart", 5432, "sysid-A"),
    )
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=False)
    assert report.ok is True
    assert report.safe_summary["target_confirmado"] is True


def test_run_preflight_bloqueia_se_tabela_ja_existe_antes_do_ddl(monkeypatch):
    responses = [r for r in _MAIN_HAPPY_RESPONSES if r[0] != "INFORMATION_SCHEMA.TABLES"]
    responses.append(("INFORMATION_SCHEMA.TABLES", "all", [("marketplace_region_daily",)]))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=False)
    assert report.ok is False
    assert any("já existem" in r for r in report.blocking_reasons)


def test_run_preflight_bloqueia_se_tabela_nao_existe_antes_da_carga(monkeypatch):
    write_url = _install_fake_connect(monkeypatch)
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=True)
    assert report.ok is False
    assert any("ainda não existem" in r for r in report.blocking_reasons)


def test_run_preflight_ok_quando_tabela_existe_e_carga_esperada(monkeypatch):
    responses = [r for r in _MAIN_HAPPY_RESPONSES if r[0] != "INFORMATION_SCHEMA.TABLES"]
    responses.append(("INFORMATION_SCHEMA.TABLES", "all", [("marketplace_region_daily",)]))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=True)
    assert report.ok is True
    assert report.blocking_reasons == []


def test_run_preflight_nunca_expoe_usuario_ou_senha_no_summary(monkeypatch):
    write_url = _install_fake_connect(monkeypatch)
    report = wc.run_preflight(write_url, _READ_URL, expect_table_exists=False)
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
    wc.run_preflight("postgresql://writer@h/db", "postgresql://read@h/db", expect_table_exists=False)
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


# =============================================================================
# Gate S3.3 — ciclo de conexão dos helpers compartilhados: set_session
# protegido, close best-effort, exceção original nunca mascarada
# =============================================================================

class _LifecycleConn:
    """Fake mínimo para o ciclo connect/set_session/close: permite fazer
    set_session e/ou close levantarem, e conta quantas vezes close rodou."""

    def __init__(self, set_session_error=None, close_error=None, identity_responses=None):
        self.set_session_error = set_session_error
        self.close_error = close_error
        self.set_session_calls = 0
        self.close_calls = 0
        self._responses = identity_responses or _identity_responses()

    def set_session(self, readonly=None, autocommit=None):
        self.set_session_calls += 1
        if self.set_session_error is not None:
            raise self.set_session_error

    def cursor(self):
        return ScriptedCursor(self._responses)

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def _install_lifecycle_conn(monkeypatch, conn):
    monkeypatch.setattr(wc.psycopg2, "connect", lambda url, connect_timeout=15: conn)
    return conn


def test_connect_readonly_set_session_falha_fecha_conexao_e_preserva_excecao(monkeypatch):
    original = RuntimeError("falha simulada no set_session")
    conn = _install_lifecycle_conn(monkeypatch, _LifecycleConn(set_session_error=original))

    with pytest.raises(RuntimeError) as exc_info:
        wc._connect_readonly("postgresql://writer@host/db")

    assert exc_info.value is original  # exceção ORIGINAL, intacta
    assert conn.close_calls == 1


def test_connect_readonly_set_session_e_close_falham_excecao_original_prevalece(monkeypatch):
    original = RuntimeError("falha original do set_session")
    conn = _install_lifecycle_conn(
        monkeypatch,
        _LifecycleConn(set_session_error=original, close_error=OSError("close também falhou")),
    )

    with pytest.raises(RuntimeError) as exc_info:
        wc._connect_readonly("postgresql://writer@host/db")

    assert exc_info.value is original  # a falha do close nunca mascara a original
    assert conn.close_calls == 1


def test_connect_readonly_caminho_feliz_retorna_conexao_aberta(monkeypatch):
    conn = _install_lifecycle_conn(monkeypatch, _LifecycleConn())

    returned = wc._connect_readonly("postgresql://writer@host/db")

    assert returned is conn
    assert conn.set_session_calls == 1
    assert conn.close_calls == 0  # caminho feliz: conexão continua aberta para o chamador


def test_fetch_target_identity_set_session_falha_fecha_conexao(monkeypatch):
    original = RuntimeError("falha simulada no set_session")
    conn = _install_lifecycle_conn(monkeypatch, _LifecycleConn(set_session_error=original))

    with pytest.raises(RuntimeError) as exc_info:
        wc._fetch_target_identity("postgresql://reader@host/db")

    assert exc_info.value is original
    assert conn.close_calls == 1


def test_fetch_target_identity_query_falha_fecha_conexao_e_preserva_excecao(monkeypatch):
    original = RuntimeError("falha simulada na query de identidade")
    responses = [("CURRENT_DATABASE()", "raise", original)]
    conn = _install_lifecycle_conn(monkeypatch, _LifecycleConn(identity_responses=responses))

    with pytest.raises(RuntimeError) as exc_info:
        wc._fetch_target_identity("postgresql://reader@host/db")

    assert exc_info.value is original
    assert conn.close_calls == 1


def test_fetch_target_identity_close_falha_durante_outra_falha_nao_mascara(monkeypatch):
    original = RuntimeError("falha original da query")
    responses = [("CURRENT_DATABASE()", "raise", original)]
    conn = _install_lifecycle_conn(
        monkeypatch,
        _LifecycleConn(identity_responses=responses, close_error=OSError("close falhou")),
    )

    with pytest.raises(RuntimeError) as exc_info:
        wc._fetch_target_identity("postgresql://reader@host/db")

    assert exc_info.value is original  # OSError do close nunca substitui a original
    assert conn.close_calls == 1


def test_fetch_target_identity_caminho_feliz_contrato_inalterado(monkeypatch):
    conn = _install_lifecycle_conn(monkeypatch, _LifecycleConn())

    identity = wc._fetch_target_identity("postgresql://reader@host/db")

    assert identity == {"db": "datamart", "port": 5432, "sysid": "sysid-A"}
    assert conn.close_calls == 1  # identidade sempre fecha a própria conexão


def test_fetch_target_identity_fallback_sysid_none_preservado(monkeypatch):
    """Contrato do fallback do helper GENÉRICO inalterado: pg_control_system
    inacessível -> sysid=None (o preflight de janela é quem recusa isso)."""
    responses = [
        ("CURRENT_DATABASE()", "one", ("datamart", 5432)),
        ("PG_CONTROL_SYSTEM", "raise", RuntimeError("permission denied for function pg_control_system")),
    ]
    conn = _install_lifecycle_conn(monkeypatch, _LifecycleConn(identity_responses=responses))

    identity = wc._fetch_target_identity("postgresql://reader@host/db")

    assert identity == {"db": "datamart", "port": 5432, "sysid": None}
    assert conn.close_calls == 1
