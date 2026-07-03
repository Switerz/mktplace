"""
Guarda de destino do loader Shopee (apps/api/etl/load_shopee_products.py):
so' pode escrever no PostgreSQL LOCAL — exige LOCAL_PG_URL explicitamente
(sem fallback com credencial hardcoded) e bloqueia qualquer host que nao
seja localhost/127.0.0.1/::1. A resolucao e' LAZY (so' dentro de main()),
entao importar as funcoes puras do modulo (BRANDS, DDL, _aggregate,
_load_brand) nunca deve exigir LOCAL_PG_URL nem abrir conexao nenhuma.

Nenhum banco real e' tocado; nenhum XLSX real e' processado; main() nunca
e' chamado nestes testes.
"""
import pytest

from etl import load_shopee_products as loader


def test_get_local_pg_url_sem_variavel_levanta_erro_sem_fallback(monkeypatch):
    monkeypatch.delenv("LOCAL_PG_URL", raising=False)
    with pytest.raises(RuntimeError, match="LOCAL_PG_URL"):
        loader._get_local_pg_url()


def test_get_local_pg_url_bloqueia_host_remoto(monkeypatch):
    monkeypatch.setenv("LOCAL_PG_URL", "postgresql://u:p@neon-host.example.com:5432/db")
    with pytest.raises(RuntimeError, match="nao permitido"):
        loader._get_local_pg_url()


@pytest.mark.parametrize("host,netloc", [("localhost", "localhost"), ("127.0.0.1", "127.0.0.1"), ("::1", "[::1]")])
def test_get_local_pg_url_aceita_hosts_locais(monkeypatch, host, netloc):
    url_in = f"postgresql://u:p@{netloc}:5432/mktplace_control"
    monkeypatch.setenv("LOCAL_PG_URL", url_in)
    assert loader._get_local_pg_url() == url_in


def test_get_local_pg_url_erro_nunca_expoe_credenciais(monkeypatch):
    monkeypatch.setenv("LOCAL_PG_URL", "postgresql://segredouser:S3nhaSecreta@rds-remoto.example.com:5432/db")
    with pytest.raises(RuntimeError) as exc_info:
        loader._get_local_pg_url()
    msg = str(exc_info.value)
    assert "segredouser" not in msg
    assert "S3nhaSecreta" not in msg


def test_sanitize_url_nunca_expoe_credenciais():
    sanitized = loader._sanitize_url("postgresql://user:S3nha@localhost:5432/mktplace_control")
    assert sanitized == "localhost:5432/mktplace_control"
    assert "user" not in sanitized
    assert "S3nha" not in sanitized


def test_sanitize_url_vazio():
    assert loader._sanitize_url("") == "(nao configurado)"


def test_importar_funcoes_puras_nunca_exige_local_pg_url(monkeypatch):
    """Reproduz o uso real de reconcile_bug8_canceled_only.py,
    monitor_bug8_invariants.py, fix_shopee_product_dates.py e
    diagnose_bug8_neon.py: importam so' BRANDS/DDL/_aggregate/_load_brand
    sem nunca precisar de LOCAL_PG_URL nem abrir conexao."""
    monkeypatch.delenv("LOCAL_PG_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import importlib

    reloaded = importlib.reload(loader)
    assert reloaded.BRANDS
    assert "CREATE TABLE" in reloaded.DDL
    assert callable(reloaded._aggregate)
    assert callable(reloaded._load_brand)


def test_modulo_nao_tem_mais_database_url_no_topo():
    assert not hasattr(loader, "DATABASE_URL")
