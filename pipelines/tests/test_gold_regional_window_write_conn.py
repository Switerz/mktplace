"""
Testes de pipelines/ingestion/gold_regional/window_write_conn.py — Gate S3
(secret dedicado + preflight de privilégio mínimo do refresh/restore por
janela Shopee).

Usa conexões psycopg2 falsas e um `_run_git` falso — nenhum banco real e
nenhum repositório git real são tocados. Nenhuma credencial real é usada.
Mesmo padrão de fakes de test_gold_regional_write_conn.py (ScriptedCursor/
ScriptedConn por substring), reimplementado localmente (não importado) —
os dois módulos de teste devem poder ser lidos/auditados isoladamente.
"""
from __future__ import annotations

import subprocess

import pytest

from pipelines.ingestion.gold_regional import window_write_conn as wwc
from pipelines.ingestion.gold_regional import write_conn as wc


# ---------------------------------------------------------------------------
# load_window_write_secret
# ---------------------------------------------------------------------------

def _fake_git_ok(args, cwd):
    """check-ignore retorna 0 (ignorado); ls-files retorna 1 (não rastreado)."""
    if args[0] == "check-ignore":
        return subprocess.CompletedProcess(args, returncode=0)
    return subprocess.CompletedProcess(args, returncode=1)


def test_load_window_write_secret_arquivo_ausente(tmp_path):
    with pytest.raises(wwc.WindowSecretLoadError, match="não encontrado"):
        wwc.load_window_write_secret(tmp_path / "nao_existe.local", tmp_path)


def test_load_window_write_secret_bloqueia_se_nao_gitignored(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-window-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WINDOW_WRITE_URL=x\nI_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW=1\n"
    )

    def fake_run_git(args, cwd):
        return subprocess.CompletedProcess(args, returncode=1)  # nada ignorado

    monkeypatch.setattr(wc, "_run_git", fake_run_git)
    with pytest.raises(wwc.WindowSecretLoadError, match="gitignore"):
        wwc.load_window_write_secret(secret_path, tmp_path)


def test_load_window_write_secret_bloqueia_se_rastreado(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-window-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WINDOW_WRITE_URL=x\nI_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW=1\n"
    )

    def fake_run_git(args, cwd):
        return subprocess.CompletedProcess(args, returncode=0)  # ignorado E "rastreado"

    monkeypatch.setattr(wc, "_run_git", fake_run_git)
    with pytest.raises(wwc.WindowSecretLoadError, match="RASTREADO"):
        wwc.load_window_write_secret(secret_path, tmp_path)


def test_load_window_write_secret_bloqueia_chave_faltando(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-window-write.local"
    secret_path.write_text("DATAMART_GOLD_WINDOW_WRITE_URL=postgresql://writer@host/db\n")
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wwc.WindowSecretLoadError, match="faltando"):
        wwc.load_window_write_secret(secret_path, tmp_path)


def test_load_window_write_secret_bloqueia_chave_extra(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-window-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WINDOW_WRITE_URL=postgresql://writer@host/db\n"
        "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW=1\n"
        "ALGO_INESPERADO=valor\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wwc.WindowSecretLoadError, match="inesperada"):
        wwc.load_window_write_secret(secret_path, tmp_path)


def test_load_window_write_secret_bloqueia_consentimento_diferente_de_1(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-window-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WINDOW_WRITE_URL=postgresql://writer@host/db\n"
        "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW=0\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wwc.WindowSecretLoadError, match="I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW"):
        wwc.load_window_write_secret(secret_path, tmp_path)


def test_load_window_write_secret_rejeita_secret_do_incremental_como_substituto(tmp_path, monkeypatch):
    """Um `.env.gold-write.local` (secret do --incremental, chaves
    DATAMART_GOLD_WRITE_URL/I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD) NUNCA
    deve ser aceito no lugar do secret dedicado do refresh/restore — as
    chaves são diferentes, então a checagem de "exatamente as 2 chaves
    esperadas" já rejeita isso por construção."""
    secret_path = tmp_path / ".env.gold-window-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WRITE_URL=postgresql://writer@host/db\n"
        "I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD=1\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    with pytest.raises(wwc.WindowSecretLoadError, match="faltando"):
        wwc.load_window_write_secret(secret_path, tmp_path)


def test_load_window_write_secret_sucesso(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-window-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WINDOW_WRITE_URL=postgresql://writer:S3nh4@host/db\n"
        "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW=1\n"
    )
    monkeypatch.setattr(wc, "_run_git", _fake_git_ok)
    values = wwc.load_window_write_secret(secret_path, tmp_path)
    assert set(values.keys()) == wwc.EXPECTED_SECRET_KEYS
    assert values["DATAMART_GOLD_WINDOW_WRITE_URL"] == "postgresql://writer:S3nh4@host/db"


def test_expected_secret_keys_nao_colide_com_secret_do_incremental():
    from pipelines.ingestion.gold_regional import write_conn as gold_wc
    assert wwc.EXPECTED_SECRET_KEYS.isdisjoint(gold_wc.EXPECTED_SECRET_KEYS)


# ---------------------------------------------------------------------------
# validate_window_write_guardrails
# ---------------------------------------------------------------------------

def test_validate_window_write_guardrails_bloqueia_url_vazia():
    with pytest.raises(wwc.WindowSecretLoadError, match="vazio"):
        wwc.validate_window_write_guardrails({"DATAMART_GOLD_WINDOW_WRITE_URL": ""}, "postgresql://read@host/db")


def test_validate_window_write_guardrails_bloqueia_reuso_da_url_de_leitura():
    same = "postgresql://postgres:segredo@host/datamart"
    with pytest.raises(wwc.WindowSecretLoadError, match="nunca reutilizar"):
        wwc.validate_window_write_guardrails({"DATAMART_GOLD_WINDOW_WRITE_URL": same}, same)


def test_validate_window_write_guardrails_ok():
    url = wwc.validate_window_write_guardrails(
        {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer@host/db"},
        "postgresql://postgres@host/db",
    )
    assert url == "postgresql://writer@host/db"


# ---------------------------------------------------------------------------
# run_window_preflight (psycopg2 falso)
# ---------------------------------------------------------------------------

class ScriptedCursor:
    """Cursor falso que decide a resposta pelo CONTEÚDO do SQL (substring),
    não pela ordem. Quando dois matchers são ambos substring um do outro
    (ver comentário de `_GOLD_SELECT_INSERT_DELETE_MATCHER` abaixo), a
    ORDEM da lista de respostas resolve o empate — o primeiro que casar
    vence, por isso o mais específico vem sempre antes do mais genérico."""

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


# As duas queries de has_table_privilege sobre a Gold têm texto
# PREFIXO-SOBREPOSTO: a query "só SELECT da fonte silver" é
# "SELECT HAS_TABLE_PRIVILEGE(..., 'SELECT')" (isolada), e a query
# "SELECT/INSERT/DELETE/UPDATE/TRUNCATE da Gold" É EXATAMENTE essa mesma
# string seguida de ", HAS_TABLE_PRIVILEGE(..., 'INSERT'), ...". Por isso o
# matcher da Gold (mais específico, com a vírgula) precisa vir ANTES do
# matcher da silver na lista.
_GOLD_PRIVS_MATCHER = "HAS_TABLE_PRIVILEGE(CURRENT_USER, %S, 'SELECT'), "
_SILVER_SELECT_ONLY_MATCHER = "SELECT HAS_TABLE_PRIVILEGE(CURRENT_USER, %S, 'SELECT')"

# Ordem real de run_window_preflight (Gate S3.1) após a identidade:
# pg_is_in_recovery, current_user, rolsuper+rolcreatedb+rolcreaterole+
# rolreplication+rolbypassrls (5-tupla), tabela existe, sequence existe,
# USAGE silver/gold + CREATE gold + TEMP (4-tupla), SELECT/INSERT/DELETE/
# UPDATE/TRUNCATE na Gold (5-tupla), SELECT na silver, USAGE sequence,
# rds_superuser member, ssl, server_version.
#
# Caminho feliz Gate S3.1: rds_superuser CONFIRMADO false (não True — isso
# agora bloqueia), SSL CONFIRMADO true, sequence existe, UPDATE/TRUNCATE/
# CREATE todos False (privilégio mínimo, nada proibido concedido).
_MAIN_HAPPY_RESPONSES = [
    ("PG_IS_IN_RECOVERY", "one", (False,)),
    ("SELECT CURRENT_USER", "one", ("window_writer_role",)),
    ("FROM PG_ROLES", "one", (False, False, False, False, False)),
    ("INFORMATION_SCHEMA.TABLES", "one", (True,)),
    ("INFORMATION_SCHEMA.SEQUENCES", "one", (True,)),
    ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", "one", (True, True, False, True)),
    (_GOLD_PRIVS_MATCHER, "one", (True, True, True, False, False)),
    (_SILVER_SELECT_ONLY_MATCHER, "one", (True,)),
    ("HAS_SEQUENCE_PRIVILEGE", "one", (True,)),
    ("RDS_SUPERUSER", "one", (False,)),
    ("PG_STAT_SSL", "one", (True,)),
    ("SERVER_VERSION", "one", ("16.4",)),
]


def _override(base, target_prefix, replacement):
    """Substitui, NA MESMA POSIÇÃO, a resposta cujo matcher é exatamente
    `target_prefix` — preserva a ordem relativa das demais (crítico para
    os dois matchers ambíguos de HAS_TABLE_PRIVILEGE acima)."""
    result = []
    replaced = False
    for r in base:
        if r[0] == target_prefix:
            result.append(replacement)
            replaced = True
        else:
            result.append(r)
    assert replaced, f"matcher não encontrado para substituir: {target_prefix!r}"
    return result


def _install_fake_connect(
    monkeypatch,
    main_responses=_MAIN_HAPPY_RESPONSES,
    write_identity=("datamart", 5432, "sysid-A"),
    read_identity=("datamart", 5432, "sysid-A"),
    raise_on_call=None,
):
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


def test_run_window_preflight_ok_caminho_feliz(monkeypatch):
    write_url = _install_fake_connect(monkeypatch)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is True
    assert report.blocking_reasons == []


def test_run_window_preflight_bloqueia_conexao_falha_na_identidade(monkeypatch):
    write_url = _install_fake_connect(monkeypatch, raise_on_call=1)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("falha ao conectar" in r for r in report.blocking_reasons)
    assert "p@h" not in " ".join(report.blocking_reasons)


def test_run_window_preflight_bloqueia_replica(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "PG_IS_IN_RECOVERY", ("PG_IS_IN_RECOVERY", "one", (True,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("recovery" in r or "réplica" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_cluster_fisico_diferente(monkeypatch):
    write_url = _install_fake_connect(
        monkeypatch, write_identity=("datamart", 5432, "sysid-A"), read_identity=("datamart", 5432, "sysid-B"),
    )
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("cluster físico" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_rolsuper_true(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "FROM PG_ROLES", ("FROM PG_ROLES", "one", (True, False, False, False, False)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert "rolsuper=true" in report.blocking_reasons


def test_run_window_preflight_bloqueia_rolcreatedb_true(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "FROM PG_ROLES", ("FROM PG_ROLES", "one", (False, True, False, False, False)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert "rolcreatedb=true" in report.blocking_reasons


def test_run_window_preflight_bloqueia_rolcreaterole_true(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "FROM PG_ROLES", ("FROM PG_ROLES", "one", (False, False, True, False, False)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert "rolcreaterole=true" in report.blocking_reasons


def test_run_window_preflight_bloqueia_rolreplication_true(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "FROM PG_ROLES", ("FROM PG_ROLES", "one", (False, False, False, True, False)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert "rolreplication=true" in report.blocking_reasons


def test_run_window_preflight_bloqueia_rolbypassrls_true(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "FROM PG_ROLES", ("FROM PG_ROLES", "one", (False, False, False, False, True)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert "rolbypassrls=true" in report.blocking_reasons


def test_run_window_preflight_bloqueia_tabela_gold_ausente(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "INFORMATION_SCHEMA.TABLES", ("INFORMATION_SCHEMA.TABLES", "one", (False,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("não existe" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_sequence_ausente(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "INFORMATION_SCHEMA.SEQUENCES", ("INFORMATION_SCHEMA.SEQUENCES", "one", (False,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("sequence" in r and "não existe" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_falta_usage_silver(monkeypatch):
    responses = _override(
        _MAIN_HAPPY_RESPONSES, "HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')",
        ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", "one", (False, True, False, True)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("USAGE no schema silver" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_falta_usage_gold(monkeypatch):
    responses = _override(
        _MAIN_HAPPY_RESPONSES, "HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')",
        ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", "one", (True, False, False, True)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("USAGE no schema gold" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_falta_temp(monkeypatch):
    responses = _override(
        _MAIN_HAPPY_RESPONSES, "HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')",
        ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", "one", (True, True, False, False)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("TEMP no database" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_create_no_schema_gold(monkeypatch):
    """Privilégio PROIBIDO: se a credencial TEM CREATE no schema gold, isso
    é sinal de que não foi escopada como least-privilege -- bloqueia."""
    responses = _override(
        _MAIN_HAPPY_RESPONSES, "HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')",
        ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", "one", (True, True, True, True)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("CREATE" in r and "proibido" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_falta_select_silver_fonte(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, _SILVER_SELECT_ONLY_MATCHER, (_SILVER_SELECT_ONLY_MATCHER, "one", (False,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("SELECT em silver.stg_shopee_order_item_snapshots" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_falta_select_gold(monkeypatch):
    responses = _override(
        _MAIN_HAPPY_RESPONSES, _GOLD_PRIVS_MATCHER,
        (_GOLD_PRIVS_MATCHER, "one", (False, True, True, False, False)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("falta SELECT em gold.marketplace_region_daily" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_falta_insert_gold(monkeypatch):
    responses = _override(
        _MAIN_HAPPY_RESPONSES, _GOLD_PRIVS_MATCHER,
        (_GOLD_PRIVS_MATCHER, "one", (True, False, True, False, False)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("falta INSERT em gold.marketplace_region_daily" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_falta_delete_gold(monkeypatch):
    responses = _override(
        _MAIN_HAPPY_RESPONSES, _GOLD_PRIVS_MATCHER,
        (_GOLD_PRIVS_MATCHER, "one", (True, True, False, False, False)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("falta DELETE em gold.marketplace_region_daily" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_update_concedido_na_gold(monkeypatch):
    """Privilégio PROIBIDO: UPDATE concedido na Gold -- least-privilege
    violado, bloqueia mesmo que SELECT/INSERT/DELETE estejam corretos."""
    responses = _override(
        _MAIN_HAPPY_RESPONSES, _GOLD_PRIVS_MATCHER,
        (_GOLD_PRIVS_MATCHER, "one", (True, True, True, True, False)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("UPDATE" in r and "proibido" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_truncate_concedido_na_gold(monkeypatch):
    responses = _override(
        _MAIN_HAPPY_RESPONSES, _GOLD_PRIVS_MATCHER,
        (_GOLD_PRIVS_MATCHER, "one", (True, True, True, False, True)),
    )
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("TRUNCATE" in r and "proibido" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_falta_usage_sequence(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "HAS_SEQUENCE_PRIVILEGE", ("HAS_SEQUENCE_PRIVILEGE", "one", (False,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("USAGE na sequence" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_se_rds_superuser_member(monkeypatch):
    """Gate S3.1: revertido do comportamento antigo (só aviso) -- para esta
    credencial dedicada (caminho com DELETE), rds_superuser BLOQUEIA."""
    responses = _override(_MAIN_HAPPY_RESPONSES, "RDS_SUPERUSER", ("RDS_SUPERUSER", "one", (True,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("rds_superuser" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_se_rds_superuser_desconhecido(monkeypatch):
    """"Não foi possível confirmar" NUNCA equivale a aprovado: se a consulta
    de rds_superuser falhar por um motivo QUALQUER que não seja "o papel
    não existe", o resultado fica None (desconhecido) -- e isso bloqueia,
    não passa silenciosamente."""
    class RaisingCursor(ScriptedCursor):
        def execute(self, sql, params=None):
            norm = " ".join(sql.split()).upper()
            if "RDS_SUPERUSER" in norm:
                raise RuntimeError("erro de permissão genérico simulado")
            return super().execute(sql, params)

    class RaisingConn(ScriptedConn):
        def cursor(self):
            return RaisingCursor(self.responses)

    def fake_connect(url, connect_timeout=15):
        fake_connect.n += 1
        if fake_connect.n <= 2:
            return ScriptedConn(_identity_responses())
        return RaisingConn(_MAIN_HAPPY_RESPONSES)
    fake_connect.n = 0

    monkeypatch.setattr(wc.psycopg2, "connect", fake_connect)
    report = wwc.run_window_preflight(
        "postgresql://writer@datamart-gogroup.example.rds.amazonaws.com:5432/datamart", _READ_URL,
    )
    assert report.ok is False
    assert any("rds_superuser" in r for r in report.blocking_reasons)


def test_run_window_preflight_aprova_rds_superuser_quando_papel_nao_existe(monkeypatch):
    """Único caso em que a falha da consulta rds_superuser NÃO bloqueia:
    a mensagem nativa confirma que o papel simplesmente não existe neste
    Postgres (não é RDS) -- confirmação válida de não-membro."""
    class RaisingCursor(ScriptedCursor):
        def execute(self, sql, params=None):
            norm = " ".join(sql.split()).upper()
            if "RDS_SUPERUSER" in norm:
                raise RuntimeError('role "rds_superuser" does not exist')
            return super().execute(sql, params)

    class RaisingConn(ScriptedConn):
        def cursor(self):
            return RaisingCursor(self.responses)

    def fake_connect(url, connect_timeout=15):
        fake_connect.n += 1
        if fake_connect.n <= 2:
            return ScriptedConn(_identity_responses())
        return RaisingConn(_MAIN_HAPPY_RESPONSES)
    fake_connect.n = 0

    monkeypatch.setattr(wc.psycopg2, "connect", fake_connect)
    report = wwc.run_window_preflight(
        "postgresql://writer@datamart-gogroup.example.rds.amazonaws.com:5432/datamart", _READ_URL,
    )
    assert report.ok is True


def test_run_window_preflight_bloqueia_se_ssl_nao_confirmado_false(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "PG_STAT_SSL", ("PG_STAT_SSL", "one", (False,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("SSL" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_se_ssl_indisponivel_none(monkeypatch):
    """pg_stat_ssl indisponível (`fetchone()` retorna `None` -- nenhuma
    linha) -> `ssl_in_use` fica `None` -- "não confirmado" bloqueia, não é
    aprovação silenciosa. `ScriptedCursor.fetchone()` devolve `value`
    diretamente quando o matcher registra `None` (não uma tupla), o que
    reproduz exatamente `cur.fetchone()` sem linha nenhuma."""
    responses = _override(_MAIN_HAPPY_RESPONSES, "PG_STAT_SSL", ("PG_STAT_SSL", "one", None))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("SSL" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_se_datamart_database_url_ausente(monkeypatch):
    # expected_read_url="" pula o fetch de read_identity -- só 2 conexões
    # acontecem (write_identity, depois a principal), não 3; por isso este
    # teste usa seu próprio fake_connect em vez de _install_fake_connect
    # (que assume as 3 conexões do caminho com expected_read_url preenchido).
    calls = {"n": 0}

    def fake_connect(url, connect_timeout=15):
        calls["n"] += 1
        if calls["n"] == 1:
            return ScriptedConn(_identity_responses())
        return ScriptedConn(_MAIN_HAPPY_RESPONSES)

    monkeypatch.setattr(wc.psycopg2, "connect", fake_connect)
    report = wwc.run_window_preflight("postgresql://writer@datamart-gogroup.example.rds.amazonaws.com:5432/datamart", "")
    assert report.ok is False
    assert any("DATAMART_DATABASE_URL" in r for r in report.blocking_reasons)


def test_run_window_preflight_bloqueia_se_system_identifier_indisponivel_nunca_cai_para_db_porta(monkeypatch):
    """Gate S3.1: removido o fallback para comparação por database+porta —
    mesmo que db+porta batam exatamente, sysid ausente em qualquer lado
    BLOQUEIA (nunca autoriza um caminho com DELETE por esse substituto mais
    fraco)."""
    write_url = _install_fake_connect(
        monkeypatch,
        write_identity=("datamart", 5432, None),
        read_identity=("datamart", 5432, None),
    )
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("system_identifier" in r for r in report.blocking_reasons)
    assert report.safe_summary.get("target_confirmado") is None


def test_run_window_preflight_nao_exige_privilegio_em_tabelas_ml_tiktok():
    """Nenhuma referência de SQL/privilégio a tabelas ML/TikTok -- checa
    padrões de nome de tabela/coluna reais (não a palavra solta "tiktok",
    que aparece de forma inócua na prosa da docstring)."""
    import inspect
    source = inspect.getsource(wwc.run_window_preflight)
    for forbidden in ("raw.ml_", "gold.tiktok", "gold.ml_", "ml_orders", "ml_shipments", "tiktok_"):
        assert forbidden not in source.lower()


def test_run_window_preflight_nunca_expoe_usuario_ou_host_no_summary(monkeypatch):
    write_url = _install_fake_connect(monkeypatch)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    dumped = str(report.safe_summary) + str(report.warnings) + str(report.blocking_reasons)
    assert "writer" not in dumped
    assert "datamart-gogroup" not in dumped


def test_run_window_preflight_fecha_conexao(monkeypatch):
    write_url = _install_fake_connect(monkeypatch)
    conns = []
    orig_connect = wc.psycopg2.connect

    def tracking_connect(url, connect_timeout=15):
        c = orig_connect(url, connect_timeout=connect_timeout)
        conns.append(c)
        return c

    monkeypatch.setattr(wc.psycopg2, "connect", tracking_connect)
    wwc.run_window_preflight(write_url, _READ_URL)
    assert len(conns) >= 1
    assert all(c.closed_calls == 1 for c in conns)


def test_sanitize_error_message_reexportado_do_write_conn():
    """window_write_conn reaproveita a MESMA `sanitize_error_message` de
    write_conn (nunca duplica a lógica de categorização de erro)."""
    assert wwc.sanitize_error_message is wc.sanitize_error_message


# =============================================================================
# Gate S3.2 — Finding 1: exceção SQL no preflight nunca escapa
# =============================================================================

def _install_fake_connect_with_raising_main(monkeypatch, raise_on_substring, native_message):
    """Conexões de identidade normais; a conexão PRINCIPAL levanta
    `native_message` quando a query contém `raise_on_substring`."""
    class RaisingCursor(ScriptedCursor):
        def execute(self, sql, params=None):
            norm = " ".join(sql.split()).upper()
            if raise_on_substring in norm:
                raise RuntimeError(native_message)
            return super().execute(sql, params)

    class RaisingConn(ScriptedConn):
        def cursor(self):
            return RaisingCursor(self.responses)

    conns = []

    def fake_connect(url, connect_timeout=15):
        fake_connect.n += 1
        if fake_connect.n <= 2:
            c = ScriptedConn(_identity_responses())
        else:
            c = RaisingConn(_MAIN_HAPPY_RESPONSES)
        conns.append(c)
        return c
    fake_connect.n = 0

    monkeypatch.setattr(wc.psycopg2, "connect", fake_connect)
    return conns


_NATIVE_NASTY = (
    'connection to server at "prod-db.example.rds.amazonaws.com" '
    '(10.0.0.5), port 5432 failed while querying pg_roles for user "window_writer" database "proddb"'
)


@pytest.mark.parametrize("raise_on", [
    "PG_IS_IN_RECOVERY",
    "FROM PG_ROLES",
    "INFORMATION_SCHEMA.TABLES",
    "INFORMATION_SCHEMA.SEQUENCES",
    "HAS_SCHEMA_PRIVILEGE",
    "SERVER_VERSION",
])
def test_run_window_preflight_excecao_em_consulta_intermediaria_nao_escapa(monkeypatch, raise_on):
    """Finding 1: falha inesperada em QUALQUER consulta do bloco principal
    nunca escapa como exceção — vira report bloqueado com razão sanitizada
    (sem host/IP/porta/usuário/database), e a conexão é fechada."""
    conns = _install_fake_connect_with_raising_main(monkeypatch, raise_on, _NATIVE_NASTY)

    report = wwc.run_window_preflight(
        "postgresql://writer@datamart-gogroup.example.rds.amazonaws.com:5432/datamart", _READ_URL,
    )

    assert report.ok is False
    assert any("consultas do preflight" in r or "falha ao conectar" in r for r in report.blocking_reasons)
    dumped = " ".join(report.blocking_reasons) + str(report.safe_summary)
    assert "prod-db.example.rds.amazonaws.com" not in dumped
    assert "10.0.0.5" not in dumped
    assert "window_writer" not in dumped
    assert all(c.closed_calls == 1 for c in conns)


def test_run_window_preflight_excecao_em_consulta_nao_deixa_campo_indefinido(monkeypatch):
    """Falha logo na PRIMEIRA consulta: todos os campos do safe_summary
    existem (inicializados como None/inconclusivo), nenhum NameError."""
    _install_fake_connect_with_raising_main(monkeypatch, "PG_IS_IN_RECOVERY", "boom")

    report = wwc.run_window_preflight(
        "postgresql://writer@datamart-gogroup.example.rds.amazonaws.com:5432/datamart", _READ_URL,
    )

    assert report.ok is False
    assert report.safe_summary["pg_is_in_recovery"] is None
    assert report.safe_summary["rolsuper"] is None
    assert report.safe_summary["ssl_in_use"] is None
    assert report.safe_summary["server_version"] is None


# =============================================================================
# Gate S3.2 — Finding 2: None bloqueia individualmente (checagem explícita)
# =============================================================================

@pytest.mark.parametrize("matcher,value,expected_substring", [
    ("PG_IS_IN_RECOVERY", (None,), "pg_is_in_recovery"),
    ("FROM PG_ROLES", (None, False, False, False, False), "rolsuper"),
    ("FROM PG_ROLES", (False, None, False, False, False), "rolcreatedb"),
    ("FROM PG_ROLES", (False, False, None, False, False), "rolcreaterole"),
    ("FROM PG_ROLES", (False, False, False, None, False), "rolreplication"),
    ("FROM PG_ROLES", (False, False, False, False, None), "rolbypassrls"),
    ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", (None, True, False, True), "USAGE no schema silver"),
    ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", (True, None, False, True), "USAGE no schema gold"),
    ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", (True, True, None, True), "CREATE no schema gold não confirmado"),
    ("HAS_SCHEMA_PRIVILEGE(CURRENT_USER, 'SILVER', 'USAGE')", (True, True, False, None), "TEMP no database"),
    (_GOLD_PRIVS_MATCHER, (None, True, True, False, False), "SELECT em gold.marketplace_region_daily"),
    (_GOLD_PRIVS_MATCHER, (True, None, True, False, False), "INSERT em gold.marketplace_region_daily"),
    (_GOLD_PRIVS_MATCHER, (True, True, None, False, False), "DELETE em gold.marketplace_region_daily"),
    (_GOLD_PRIVS_MATCHER, (True, True, True, None, False), "UPDATE em gold.marketplace_region_daily não confirmado"),
    (_GOLD_PRIVS_MATCHER, (True, True, True, False, None), "TRUNCATE em gold.marketplace_region_daily não confirmado"),
    (_SILVER_SELECT_ONLY_MATCHER, (None,), "SELECT em silver.stg_shopee_order_item_snapshots"),
    ("HAS_SEQUENCE_PRIVILEGE", (None,), "USAGE na sequence"),
])
def test_run_window_preflight_none_bloqueia_individualmente(monkeypatch, matcher, value, expected_substring):
    """Finding 2: para cada checagem sensível, o valor None (inconclusivo)
    bloqueia individualmente — nunca aprovado por truthiness/omissão."""
    responses = _override(_MAIN_HAPPY_RESPONSES, matcher, (matcher, "one", value))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any(expected_substring in r for r in report.blocking_reasons), report.blocking_reasons


def test_run_window_preflight_tabela_none_bloqueia(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "INFORMATION_SCHEMA.TABLES", ("INFORMATION_SCHEMA.TABLES", "one", (None,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("não existe ou não confirmada" in r and "tabela" in r for r in report.blocking_reasons)


def test_run_window_preflight_sequence_none_bloqueia(monkeypatch):
    responses = _override(_MAIN_HAPPY_RESPONSES, "INFORMATION_SCHEMA.SEQUENCES", ("INFORMATION_SCHEMA.SEQUENCES", "one", (None,)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert any("sequence" in r and ("não existe" in r or "não confirmada" in r) for r in report.blocking_reasons)


def test_run_window_preflight_set_session_falha_nos_helpers_bloqueia_sanitizado_sem_conexao_aberta(monkeypatch):
    """Gate S3.3 (regressão): se `set_session()` falhar dentro dos helpers
    compartilhados (`_connect_readonly`/`_fetch_target_identity`), o
    preflight bloqueia com razão sanitizada e NENHUMA conexão fica aberta
    — o próprio helper fecha a conexão vazada antes de propagar."""
    class SetSessionFailsConn:
        def __init__(self):
            self.close_calls = 0

        def set_session(self, readonly=None, autocommit=None):
            raise RuntimeError(
                'connection to server at "prod-db.example.rds.amazonaws.com" '
                '(10.0.0.5), port 5432 failed: FATAL: password authentication failed for user "postgres"'
            )

        def close(self):
            self.close_calls += 1

    conns = []

    def fake_connect(url, connect_timeout=15):
        c = SetSessionFailsConn()
        conns.append(c)
        return c

    monkeypatch.setattr(wc.psycopg2, "connect", fake_connect)

    report = wwc.run_window_preflight(
        "postgresql://writer@datamart-gogroup.example.rds.amazonaws.com:5432/datamart", _READ_URL,
    )

    assert report.ok is False
    assert any("falha ao conectar" in r for r in report.blocking_reasons)
    dumped = " ".join(report.blocking_reasons)
    assert "prod-db.example.rds.amazonaws.com" not in dumped
    assert "10.0.0.5" not in dumped
    assert "postgres" not in dumped
    assert len(conns) >= 1
    assert all(c.close_calls == 1 for c in conns)  # nenhuma conexão vazada


def test_run_window_preflight_so_o_valor_esperado_exato_aprova(monkeypatch):
    """Sanidade do Finding 2: valores truthy não-booleanos (1 em vez de
    True, 0 em vez de False) NUNCA aprovam — só o booleano exato."""
    responses = _override(_MAIN_HAPPY_RESPONSES, "FROM PG_ROLES", ("FROM PG_ROLES", "one", (0, 0, 0, 0, 0)))
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False  # 0 não é False literal — inconclusivo bloqueia
    assert any("rolsuper" in r for r in report.blocking_reasons)


# =============================================================================
# Gate S4.3d — compatibilidade do interceptor de DDL do AWS DMS
# (achado real do Gate S4.3b: CREATE TEMP TABLE aciona awsdms_intercept_ddl).
# Nenhum banco real é tocado -- ScriptedConn/ScriptedCursor, mesmo padrão do
# resto deste arquivo.
# =============================================================================

_DMS_EVTFOID = 999999
_DMS_OWNER_OID = 555555
_DMS_SEQ_QUALIFIED = "public.awsdms_ddl_audit_c_key_seq"


def _dms_responses(
    trigger_present=True,
    evtenabled="O",
    function_present=True,
    prosecdef=True,
    table_present=True,
    insert_ok=True,
    delete_ok=True,
    sequence_present=True,
    usage_ok=True,
):
    """Constrói as respostas cientadas do bloco de checagem DMS, na ORDEM
    real das consultas, parando assim que o código de produção pararia de
    consultar (trigger ausente/desabilitado, função ausente, tabela
    ausente, sequence ausente -- cada um interrompe a cadeia real de
    queries em `run_window_preflight`). Colocado ANTES de
    `_MAIN_HAPPY_RESPONSES` pelo chamador para que os matchers específicos
    (com `%S, %S` em vez de `CURRENT_USER`) vençam qualquer matcher
    genérico pré-existente (ex.: `HAS_SEQUENCE_PRIVILEGE` bare)."""
    responses = []
    if not trigger_present:
        return responses  # nenhum matcher -> fetchone() default (None) -> trigger ausente
    responses.append(("PG_EVENT_TRIGGER", "one", (evtenabled, _DMS_EVTFOID)))
    if evtenabled == "D":
        return responses  # desabilitado -> código nunca consulta a função

    if not function_present:
        responses.append(("PROSECDEF, PROOWNER FROM PG_PROC", "one", None))
        return responses
    responses.append(("PROSECDEF, PROOWNER FROM PG_PROC", "one", (prosecdef, _DMS_OWNER_OID)))

    if not table_present:
        responses.append(("PG_CLASS C JOIN PG_NAMESPACE N ON N.OID = C.RELNAMESPACE", "one", (False,)))
        return responses
    responses.append(("PG_CLASS C JOIN PG_NAMESPACE N ON N.OID = C.RELNAMESPACE", "one", (True,)))

    responses.append(("HAS_TABLE_PRIVILEGE(%S, %S, 'INSERT')", "one", (insert_ok, delete_ok)))

    if not sequence_present:
        responses.append(("PG_GET_SERIAL_SEQUENCE", "one", (None,)))
        return responses
    responses.append(("PG_GET_SERIAL_SEQUENCE", "one", (_DMS_SEQ_QUALIFIED,)))
    responses.append(("HAS_SEQUENCE_PRIVILEGE(%S, %S, 'USAGE')", "one", (usage_ok,)))
    return responses


def _install_dms_scenario(monkeypatch, **dms_kwargs):
    responses = _dms_responses(**dms_kwargs) + _MAIN_HAPPY_RESPONSES
    return _install_fake_connect(monkeypatch, main_responses=responses)


def test_dms_trigger_ausente_nao_bloqueia(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, trigger_present=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is True
    assert report.safe_summary["dms_ddl_trigger_present"] is False
    assert report.safe_summary["dms_ddl_trigger_enabled"] is False
    assert report.safe_summary["dms_ddl_interceptor_compatible"] is True


def test_dms_trigger_desabilitado_nao_bloqueia(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, evtenabled="D")
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is True
    assert report.safe_summary["dms_ddl_trigger_present"] is True
    assert report.safe_summary["dms_ddl_trigger_enabled"] is False
    assert report.safe_summary["dms_ddl_interceptor_compatible"] is True


def test_dms_trigger_habilitado_security_invoker_bloqueia(monkeypatch):
    """Reproduz exatamente o achado real do Gate S4.3b: prosecdef=false."""
    write_url = _install_dms_scenario(monkeypatch, prosecdef=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert report.safe_summary["dms_function_security_definer"] is False
    assert report.safe_summary["dms_ddl_interceptor_compatible"] is False
    assert any("Interceptor DDL do AWS DMS incompatível" in r for r in report.blocking_reasons)
    assert any("SECURITY DEFINER" in r for r in report.blocking_reasons)


def test_dms_security_definer_com_privilegios_corretos_aprova(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, prosecdef=True)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is True
    assert report.safe_summary["dms_function_security_definer"] is True
    assert report.safe_summary["dms_ddl_interceptor_compatible"] is True


def test_dms_owner_sem_insert_bloqueia(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, insert_ok=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert report.safe_summary["dms_function_owner_can_insert_audit"] is False
    assert any("INSERT" in r for r in report.blocking_reasons)


def test_dms_owner_sem_delete_bloqueia(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, delete_ok=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert report.safe_summary["dms_function_owner_can_delete_audit"] is False
    assert any("DELETE" in r for r in report.blocking_reasons)


def test_dms_owner_sem_usage_sequence_bloqueia(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, usage_ok=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert report.safe_summary["dms_function_owner_can_use_sequence"] is False
    assert any("USAGE" in r for r in report.blocking_reasons)


def test_dms_tabela_ausente_bloqueia(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, table_present=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert report.safe_summary["dms_audit_table_present"] is False
    assert any("tabela de auditoria" in r for r in report.blocking_reasons)


def test_dms_sequence_ausente_bloqueia(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, sequence_present=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert report.safe_summary["dms_audit_sequence_present"] is False
    assert any("sequence de auditoria" in r for r in report.blocking_reasons)


def test_dms_funcao_ausente_bloqueia(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, function_present=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert report.safe_summary["dms_function_present"] is False
    assert any("função do interceptor" in r for r in report.blocking_reasons)


def test_dms_resultado_inconclusivo_bloqueia(monkeypatch):
    """Trigger habilitado, mas a consulta seguinte (prosecdef/owner) LEVANTA
    uma exceção (simulando uma falha real de consulta) -- os campos ainda
    não coletados permanecem `None` (inconclusivo), e "inconclusivo nunca
    é tratado como aprovado" precisa valer aqui também."""
    responses = [
        ("PG_EVENT_TRIGGER", "one", ("O", _DMS_EVTFOID)),
        ("PROSECDEF, PROOWNER FROM PG_PROC", "raise", RuntimeError("falha simulada na consulta")),
    ] + _MAIN_HAPPY_RESPONSES
    write_url = _install_fake_connect(monkeypatch, main_responses=responses)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    assert report.safe_summary["dms_ddl_trigger_present"] is True
    assert report.safe_summary["dms_ddl_trigger_enabled"] is True
    assert report.safe_summary["dms_function_present"] is None  # nunca confirmado -> inconclusivo
    assert report.safe_summary["dms_ddl_interceptor_compatible"] is False
    assert any("Interceptor DDL do AWS DMS incompatível" in r for r in report.blocking_reasons)


def test_dms_mensagem_nunca_vaza_owner_corpo_ou_infraestrutura(monkeypatch):
    write_url = _install_dms_scenario(monkeypatch, prosecdef=False)
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is False
    combined = " ".join(report.blocking_reasons) + " " + str(report.safe_summary)
    for forbidden in (
        str(_DMS_OWNER_OID), "prosrc", "awsdms-gogroup", "datamart-gogroup.example.rds.amazonaws.com",
        "10.0.0.5", "postgres:", "password",
    ):
        assert forbidden not in combined


def test_dms_nao_afeta_preflight_sem_dms_configurado_no_cluster(monkeypatch):
    """Regressão: sem nenhuma resposta DMS cientada (cluster sem o
    artefato), o preflight segue exatamente como antes do Gate S4.3d."""
    write_url = _install_fake_connect(monkeypatch)  # _MAIN_HAPPY_RESPONSES puro, sem PG_EVENT_TRIGGER
    report = wwc.run_window_preflight(write_url, _READ_URL)
    assert report.ok is True
    assert report.safe_summary["dms_ddl_trigger_present"] is False
    assert report.safe_summary["dms_ddl_interceptor_compatible"] is True
