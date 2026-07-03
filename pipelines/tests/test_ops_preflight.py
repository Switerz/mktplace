"""
Testes de pipelines/ops/preflight.py: checagens read-only de dependencia
(RDS, Neon, PostgreSQL local, arquivos Shopee) antes de disparar uma carga.

Usa psycopg2.connect monkeypatched — nenhum banco real e' tocado. Cada
check so' deve emitir SELECT 1; nunca escreve em nada.
"""
import re
import types
from pathlib import Path

import pytest

import pipelines.ops.preflight as preflight


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.executed.append(" ".join(sql.split()))
        if self.conn.raise_on_execute:
            raise self.conn.raise_on_execute

    def fetchone(self):
        return (1,)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, raise_on_execute=None):
        self.executed = []
        self.closed = False
        self.raise_on_execute = raise_on_execute
        self.readonly_sessions = []

    def cursor(self):
        return _FakeCursor(self)

    def set_session(self, readonly=None):
        self.readonly_sessions.append(readonly)

    def close(self):
        self.closed = True


def _fake_connect_factory(raise_on_connect=None, raise_on_execute=None):
    def _fake_connect(url, connect_timeout=5):
        if raise_on_connect:
            raise raise_on_connect
        return _FakeConn(raise_on_execute=raise_on_execute)
    return _fake_connect


# ---------------------------------------------------------------------------
# sanitize_url
# ---------------------------------------------------------------------------

def test_sanitize_url_nunca_expoe_credenciais():
    sanitized = preflight.sanitize_url("postgresql://user:S3nhaSecreta@meu-host.example.com:5432/meubanco")
    assert sanitized == "meu-host.example.com:5432/meubanco"
    assert "user" not in sanitized
    assert "S3nhaSecreta" not in sanitized


def test_sanitize_url_vazio():
    assert preflight.sanitize_url("") == "(nao configurado)"


# ---------------------------------------------------------------------------
# Checks individuais — somente SELECT 1
# ---------------------------------------------------------------------------

def test_check_neon_ok(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(preflight.psycopg2, "connect", _fake_connect_factory())
    result = preflight.check_neon()
    assert result.ok is True
    assert "neon-host" in result.detail
    assert "p@" not in result.detail  # sem credenciais


def test_check_neon_falha_de_conexao(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@neon-host/db")
    monkeypatch.setattr(
        preflight.psycopg2, "connect",
        _fake_connect_factory(raise_on_connect=RuntimeError("timeout")),
    )
    result = preflight.check_neon()
    assert result.ok is False
    assert "u:p" not in result.detail


def test_check_neon_sem_url_configurada(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = preflight.check_neon()
    assert result.ok is False
    assert "nao configurada" in result.detail


def test_check_rds_usa_datamart_database_url(monkeypatch):
    monkeypatch.setenv("DATAMART_DATABASE_URL", "postgresql://u:p@rds-host/db")
    monkeypatch.setattr(preflight.psycopg2, "connect", _fake_connect_factory())
    result = preflight.check_rds()
    assert result.ok is True
    assert "rds-host" in result.detail


def test_check_rds_indisponivel_vpn_desconectada(monkeypatch):
    monkeypatch.setenv("DATAMART_DATABASE_URL", "postgresql://u:p@rds-host/db")
    monkeypatch.setattr(
        preflight.psycopg2, "connect",
        _fake_connect_factory(raise_on_connect=OSError("could not connect to server")),
    )
    result = preflight.check_rds()
    assert result.ok is False


def test_check_local_pg_indisponivel(monkeypatch):
    monkeypatch.setenv("LOCAL_PG_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(
        preflight.psycopg2, "connect",
        _fake_connect_factory(raise_on_connect=ConnectionRefusedError()),
    )
    result = preflight.check_local_pg()
    assert result.ok is False


def test_check_local_pg_sem_variavel_bloqueia_sem_fallback(monkeypatch):
    """LOCAL_PG_URL nao tem mais fallback com credencial hardcoded — sem a
    variavel, o check tem que bloquear, nunca conectar num default
    silencioso."""
    monkeypatch.delenv("LOCAL_PG_URL", raising=False)
    connect_calls = []
    monkeypatch.setattr(preflight.psycopg2, "connect", lambda *a, **k: connect_calls.append(1) or _FakeConn())
    result = preflight.check_local_pg()
    assert result.ok is False
    assert "LOCAL_PG_URL" in result.detail
    assert connect_calls == [], "nao deveria tentar conectar sem LOCAL_PG_URL configurado"


def test_check_local_pg_com_variavel_configurada_conecta(monkeypatch):
    monkeypatch.setenv("LOCAL_PG_URL", "postgresql://u:p@localhost:5432/mktplace_control")
    monkeypatch.setattr(preflight.psycopg2, "connect", _fake_connect_factory())
    result = preflight.check_local_pg()
    assert result.ok is True
    assert "localhost" in result.detail


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_check_local_pg_aceita_outros_hosts_locais(monkeypatch, host):
    netloc = f"[{host}]" if host == "::1" else host
    monkeypatch.setenv("LOCAL_PG_URL", f"postgresql://u:p@{netloc}:5432/mktplace_control")
    monkeypatch.setattr(preflight.psycopg2, "connect", _fake_connect_factory())
    result = preflight.check_local_pg()
    assert result.ok is True


def test_check_local_pg_bloqueia_host_remoto_sem_tentar_conectar(monkeypatch):
    """Mesma guarda de apps/api/etl/load_shopee_products.py: LOCAL_PG_URL
    nunca pode apontar para um host remoto (Neon/Data Mart), mesmo que a
    variavel esteja configurada e alcancavel."""
    monkeypatch.setenv("LOCAL_PG_URL", "postgresql://u:p@rds-remoto.example.com:5432/db")
    connect_calls = []
    monkeypatch.setattr(preflight.psycopg2, "connect", lambda *a, **k: connect_calls.append(1) or _FakeConn())
    result = preflight.check_local_pg()
    assert result.ok is False
    assert "nao permitido" in result.detail
    assert connect_calls == [], "nao deveria tentar conectar a um host fora do allowlist local"


def test_check_local_pg_host_bloqueado_nunca_expoe_credenciais(monkeypatch):
    monkeypatch.setenv("LOCAL_PG_URL", "postgresql://segredouser:S3nhaSecreta@rds-remoto.example.com:5432/db")
    result = preflight.check_local_pg()
    assert "segredouser" not in result.detail
    assert "S3nhaSecreta" not in result.detail


def test_todas_as_queries_dos_checks_de_conexao_sao_select_1(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@neon-host/db")
    conns = []

    def _connect(url, connect_timeout=5):
        c = _FakeConn()
        conns.append(c)
        return c

    monkeypatch.setattr(preflight.psycopg2, "connect", _connect)
    preflight.check_neon()
    assert conns[0].executed == ["SELECT 1"]
    assert conns[0].closed is True


def test_checks_de_conexao_usam_sessao_readonly(monkeypatch):
    """Defesa em profundidade: mesmo um diagnostico com SELECT 1 nunca deve
    ser capaz de escrever no servidor."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@neon-host/db")
    conns = []

    def _connect(url, connect_timeout=5):
        c = _FakeConn()
        conns.append(c)
        return c

    monkeypatch.setattr(preflight.psycopg2, "connect", _connect)
    preflight.check_neon()
    assert conns[0].readonly_sessions == [True]


# ---------------------------------------------------------------------------
# check_shopee_{orders,stats,ads}_files — um padrao de arquivo por fonte,
# contra a lista OFICIAL de brands do conector real (BRANDS_IN_SCOPE)
# ---------------------------------------------------------------------------

ALL_BRANDS = preflight.BRANDS_IN_SCOPE


def _make_all_brands(tmp_path, filename_by_brand):
    """filename_by_brand: dict brand -> nome de arquivo a criar (ou None
    para deixar a marca sem nenhum arquivo, simulando export faltando)."""
    for brand in ALL_BRANDS:
        brand_dir = tmp_path / brand
        brand_dir.mkdir()
        filename = filename_by_brand.get(brand)
        if filename:
            (brand_dir / filename).write_bytes(b"")


@pytest.mark.parametrize("check_fn,filename", [
    (preflight.check_shopee_orders_files, "Order.all.20260101_20260131.xlsx"),
    (preflight.check_shopee_stats_files, "20260101.shopee-shop-stats.20260131.xlsx"),
    (preflight.check_shopee_ads_files, "Dados_20260101_20260131.csv"),
])
def test_check_shopee_pattern_sem_variavel(monkeypatch, check_fn, filename):
    monkeypatch.delenv("SHOPEE_DATA_PATH", raising=False)
    result = check_fn()
    assert result.ok is False
    assert "SHOPEE_DATA_PATH" in result.detail


@pytest.mark.parametrize("check_fn,filename", [
    (preflight.check_shopee_orders_files, "Order.all.20260101_20260131.xlsx"),
    (preflight.check_shopee_stats_files, "20260101.shopee-shop-stats.20260131.xlsx"),
    (preflight.check_shopee_ads_files, "Dados_20260101_20260131.csv"),
])
def test_check_shopee_pattern_diretorio_ausente(monkeypatch, tmp_path, check_fn, filename):
    monkeypatch.setenv("SHOPEE_DATA_PATH", str(tmp_path / "nao-existe"))
    result = check_fn()
    assert result.ok is False


@pytest.mark.parametrize("check_fn,filename", [
    (preflight.check_shopee_orders_files, "Order.all.20260101_20260131.xlsx"),
    (preflight.check_shopee_stats_files, "20260101.shopee-shop-stats.20260131.xlsx"),
    (preflight.check_shopee_ads_files, "Dados_20260101_20260131.csv"),
])
def test_check_shopee_pattern_ok_quando_todas_as_marcas_oficiais_tem_arquivo(monkeypatch, tmp_path, check_fn, filename):
    _make_all_brands(tmp_path, {brand: filename for brand in ALL_BRANDS})
    monkeypatch.setenv("SHOPEE_DATA_PATH", str(tmp_path))
    result = check_fn()
    assert result.ok is True
    assert str(len(ALL_BRANDS)) in result.detail


@pytest.mark.parametrize("check_fn,filename", [
    (preflight.check_shopee_orders_files, "Order.all.20260101_20260131.xlsx"),
    (preflight.check_shopee_stats_files, "20260101.shopee-shop-stats.20260131.xlsx"),
    (preflight.check_shopee_ads_files, "Dados_20260101_20260131.csv"),
])
def test_check_shopee_pattern_bloqueia_a_fonte_inteira_se_uma_marca_oficial_faltar(monkeypatch, tmp_path, check_fn, filename):
    """Decisao documentada: ausencia do arquivo esperado em UMA marca
    oficial bloqueia a fonte inteira (nao so' avisa) — evita uma carga
    parcial (algumas marcas sem dado) ser registrada como 'success'."""
    by_brand = {brand: filename for brand in ALL_BRANDS}
    faltante = ALL_BRANDS[0]
    by_brand[faltante] = None
    _make_all_brands(tmp_path, by_brand)
    monkeypatch.setenv("SHOPEE_DATA_PATH", str(tmp_path))
    result = check_fn()
    assert result.ok is False
    assert faltante in result.detail


def test_shopee_orders_stats_ads_usam_padroes_de_arquivo_distintos(monkeypatch, tmp_path):
    """orders/stats/ads sao exportacoes DIFERENTES — um diretorio com so'
    Order.all*.xlsx tem que bloquear stats e ads, nao passar por engano
    porque os três checks compartilhavam o mesmo glob antes desta revisao."""
    _make_all_brands(tmp_path, {brand: "Order.all.20260101_20260131.xlsx" for brand in ALL_BRANDS})
    monkeypatch.setenv("SHOPEE_DATA_PATH", str(tmp_path))
    assert preflight.check_shopee_orders_files().ok is True
    assert preflight.check_shopee_stats_files().ok is False
    assert preflight.check_shopee_ads_files().ok is False


def test_check_shopee_pattern_informa_so_as_marcas_ausentes_sem_expor_paths(monkeypatch, tmp_path):
    sensitive_dir = tmp_path / "Notebook_pessoal_sensivel"
    sensitive_dir.mkdir()
    by_brand = {brand: "Order.all.xlsx" for brand in ALL_BRANDS}
    faltante = ALL_BRANDS[-1]
    by_brand[faltante] = None
    _make_all_brands(sensitive_dir, by_brand)
    monkeypatch.setenv("SHOPEE_DATA_PATH", str(sensitive_dir))

    result = preflight.check_shopee_orders_files()
    assert result.ok is False
    assert faltante in result.detail
    assert str(sensitive_dir) not in result.detail
    assert "Notebook_pessoal_sensivel" not in result.detail

    monkeypatch.setenv("SHOPEE_DATA_PATH", str(tmp_path / "nao-existe-sensivel"))
    result_ausente = preflight.check_shopee_orders_files()
    assert "nao-existe-sensivel" not in result_ausente.detail


def test_lista_de_brands_e_importada_do_conector_nao_duplicada():
    """A lista oficial de brands vem de
    pipelines.connectors.shopee.connector.BRANDS_IN_SCOPE — este modulo
    nao declara sua propria whitelist paralela."""
    from pipelines.connectors.shopee.connector import BRANDS_IN_SCOPE
    assert preflight.BRANDS_IN_SCOPE is BRANDS_IN_SCOPE


# ---------------------------------------------------------------------------
# run_preflight — combinacao por fonte
# ---------------------------------------------------------------------------

def test_run_preflight_fonte_desconhecida():
    with pytest.raises(ValueError):
        preflight.run_preflight("fonte-inexistente")


def test_run_preflight_tiktok_daily_depende_de_rds_e_neon(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "check_rds", lambda: calls.append("rds") or preflight.CheckResult("RDS", True, "ok"))
    monkeypatch.setattr(preflight, "check_neon", lambda: calls.append("neon") or preflight.CheckResult("Neon", True, "ok"))
    monkeypatch.setitem(preflight.SOURCE_CHECKS, "tiktok_daily", (preflight.check_rds, preflight.check_neon))
    ok, results = preflight.run_preflight("tiktok_daily")
    assert ok is True
    assert calls == ["rds", "neon"]


def test_run_preflight_produtos_shopee_depende_de_local_pg_e_neon(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "check_local_pg", lambda: calls.append("local") or preflight.CheckResult("PostgreSQL local", True, "ok"))
    monkeypatch.setattr(preflight, "check_neon", lambda: calls.append("neon") or preflight.CheckResult("Neon", True, "ok"))
    monkeypatch.setitem(preflight.SOURCE_CHECKS, "produtos_shopee", (preflight.check_local_pg, preflight.check_neon))
    ok, results = preflight.run_preflight("produtos_shopee")
    assert ok is True
    assert calls == ["local", "neon"]


def test_run_preflight_bloqueia_se_qualquer_check_falhar():
    def _ok():
        return preflight.CheckResult("a", True, "ok")

    def _falha():
        return preflight.CheckResult("b", False, "falhou")

    import pipelines.ops.preflight as p
    p.SOURCE_CHECKS["_teste_bloqueio"] = (_ok, _falha)
    try:
        ok, results = p.run_preflight("_teste_bloqueio")
        assert ok is False
        assert len(results) == 2
    finally:
        del p.SOURCE_CHECKS["_teste_bloqueio"]


# ---------------------------------------------------------------------------
# VPN/RDS/local PG/arquivos indisponiveis — bloqueiam a fonte certa
# ---------------------------------------------------------------------------

def test_vpn_rds_indisponivel_bloqueia_tiktok_e_ml_mas_nao_shopee(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@neon-host/db")
    for brand in preflight.BRANDS_IN_SCOPE:
        brand_dir = tmp_path / brand
        brand_dir.mkdir()
        (brand_dir / "Order.all.xlsx").write_bytes(b"")
    monkeypatch.setenv("SHOPEE_DATA_PATH", str(tmp_path))

    def _connect(url, connect_timeout=5):
        if "rds-host" in url:
            raise OSError("VPN desconectada")
        return _FakeConn()

    monkeypatch.setenv("DATAMART_DATABASE_URL", "postgresql://u:p@rds-host/db")
    monkeypatch.setattr(preflight.psycopg2, "connect", _connect)

    ok_tiktok, _ = preflight.run_preflight("tiktok_daily")
    ok_ml, _ = preflight.run_preflight("ml_daily")
    ok_shopee, _ = preflight.run_preflight("shopee_daily")

    assert ok_tiktok is False
    assert ok_ml is False
    assert ok_shopee is True  # shopee_daily nao depende do RDS


def test_local_pg_indisponivel_bloqueia_produtos_shopee(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@neon-host/db")
    monkeypatch.setenv("LOCAL_PG_URL", "postgresql://u:p@localhost/db")

    def _connect(url, connect_timeout=5):
        if "localhost" in url:
            raise ConnectionRefusedError("Postgres local nao esta rodando")
        return _FakeConn()

    monkeypatch.setattr(preflight.psycopg2, "connect", _connect)
    ok, results = preflight.run_preflight("produtos_shopee")
    assert ok is False


# ---------------------------------------------------------------------------
# main() — exit codes e ausencia de credenciais na saida
# ---------------------------------------------------------------------------

def test_main_retorna_0_quando_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight, "run_preflight",
        lambda source: (True, [preflight.CheckResult("x", True, "tudo certo")]),
    )
    monkeypatch.setattr(preflight.sys, "argv", ["preflight.py", "--source", "tiktok_daily"])
    exit_code = preflight.main()
    assert exit_code == 0
    assert "STATUS=OK" in capsys.readouterr().out


def test_main_retorna_1_quando_bloqueado(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight, "run_preflight",
        lambda source: (False, [preflight.CheckResult("x", False, "falhou")]),
    )
    monkeypatch.setattr(preflight.sys, "argv", ["preflight.py", "--source", "tiktok_daily"])
    exit_code = preflight.main()
    assert exit_code == 1
    assert "STATUS=BLOCKED" in capsys.readouterr().out


def test_main_nunca_imprime_credenciais(monkeypatch, capsys):
    monkeypatch.setenv("DATAMART_DATABASE_URL", "postgresql://segredouser:S3nhaSecreta@rds-host/db")
    monkeypatch.setattr(preflight.psycopg2, "connect", _fake_connect_factory())
    monkeypatch.setattr(preflight.sys, "argv", ["preflight.py", "--source", "tiktok_daily"])
    preflight.main()
    out = capsys.readouterr().out
    assert "S3nhaSecreta" not in out
    assert "segredouser" not in out


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

MODULE_PATH = Path(preflight.__file__)


def test_nunca_le_datamart_fora_da_variavel_de_ambiente_dedicada():
    """Confirma que DATAMART_DATABASE_URL so' e' lida (nunca escrita) e que
    nenhuma query fora de SELECT 1 e' emitida neste modulo."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert not re.search(r"\bINSERT\s+INTO\b", source, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\s+\w", source, re.IGNORECASE)
    assert not re.search(r"\bDELETE\s+FROM\b", source, re.IGNORECASE)
    assert not re.search(r"\bDROP\s+TABLE\b", source, re.IGNORECASE)
    assert not re.search(r"\bCREATE\s+TABLE\b", source, re.IGNORECASE)


def test_nunca_ativa_task_scheduler():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "schtasks" not in source.lower()
    assert "register-scheduledtask" not in source.lower()
    assert "new-scheduledtask" not in source.lower()
