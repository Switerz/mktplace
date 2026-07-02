"""
Testes das guardas de seguranca de pipelines/reconciliation/reconcile_bug8_canceled_only.py:
sem fallback silencioso de LOCAL_PG_URL, allowlist estrita de host local, e
garantia de que nenhuma credencial aparece em mensagens de erro/log — e de
que o modulo nunca le DATABASE_URL (Neon) nem DATAMART_DATABASE_URL (RDS).

Nao abre nenhuma conexao real de banco — testa apenas as funcoes puras de
validacao/parsing de URL.
"""
import re
from pathlib import Path

import pytest

from pipelines.reconciliation.reconcile_bug8_canceled_only import (
    _get_local_pg_url,
    _sanitize_url,
    UnsafeDatabaseHostError,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "reconciliation" / "reconcile_bug8_canceled_only.py"

SECRET_URL = "postgresql://postgres:S3nh4S3creta@localhost:5432/mktplace_control"
SECRET_URL_REMOTE = "postgresql://admin:OutraSenha123@evil-remote-host.example.com:5432/prod"


def test_local_pg_url_ausente_falha_sem_fallback(monkeypatch):
    monkeypatch.delenv("LOCAL_PG_URL", raising=False)
    with pytest.raises(RuntimeError, match="LOCAL_PG_URL nao definido"):
        _get_local_pg_url()


def test_localhost_e_permitido(monkeypatch):
    monkeypatch.setenv("LOCAL_PG_URL", SECRET_URL)
    assert _get_local_pg_url() == SECRET_URL


def test_127_0_0_1_e_permitido(monkeypatch):
    url = "postgresql://postgres:S3nh4S3creta@127.0.0.1:5432/mktplace_control"
    monkeypatch.setenv("LOCAL_PG_URL", url)
    assert _get_local_pg_url() == url


def test_ipv6_loopback_e_permitido(monkeypatch):
    url = "postgresql://postgres:S3nh4S3creta@[::1]:5432/mktplace_control"
    monkeypatch.setenv("LOCAL_PG_URL", url)
    assert _get_local_pg_url() == url


def test_host_remoto_e_bloqueado(monkeypatch):
    monkeypatch.setenv("LOCAL_PG_URL", SECRET_URL_REMOTE)
    with pytest.raises(UnsafeDatabaseHostError, match="Host nao permitido"):
        _get_local_pg_url()


@pytest.mark.parametrize("host", ["ep-neon-host.aws.neon.tech", "meu-rds.rds.amazonaws.com", "0.0.0.0", "10.0.0.5"])
def test_qualquer_host_fora_da_allowlist_e_bloqueado(monkeypatch, host):
    monkeypatch.setenv("LOCAL_PG_URL", f"postgresql://user:pass@{host}:5432/db")
    with pytest.raises(UnsafeDatabaseHostError):
        _get_local_pg_url()


def test_nenhuma_credencial_aparece_na_mensagem_de_erro_host_bloqueado(monkeypatch):
    monkeypatch.setenv("LOCAL_PG_URL", SECRET_URL_REMOTE)
    with pytest.raises(UnsafeDatabaseHostError) as exc_info:
        _get_local_pg_url()
    message = str(exc_info.value)
    assert "admin" not in message
    assert "OutraSenha123" not in message
    assert "evil-remote-host.example.com:5432/prod" in message  # sanitizado, sem credenciais


def test_sanitize_url_nunca_expoe_usuario_ou_senha():
    sanitized = _sanitize_url(SECRET_URL)
    assert sanitized == "localhost:5432/mktplace_control"
    assert "postgres" not in sanitized  # nem usuario nem parte da senha
    assert "S3nh4S3creta" not in sanitized


def test_modulo_nunca_le_database_url_ou_datamart_database_url():
    """Guarda de regressao: garante que ninguem adicione, no futuro, uma
    leitura de DATABASE_URL (Neon) ou DATAMART_DATABASE_URL (RDS) neste
    modulo. Verifica so' o padrao de LEITURA de variavel de ambiente
    (os.environ[...]/os.environ.get(...)/os.getenv(...)) — comentarios e
    docstrings que mencionam esses nomes em prosa nao disparam o teste."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    read_patterns = [
        r'os\.environ(?:\.get)?\(\s*["\']DATABASE_URL',
        r'os\.getenv\(\s*["\']DATABASE_URL',
        r'os\.environ(?:\.get)?\(\s*["\']DATAMART_DATABASE_URL',
        r'os\.getenv\(\s*["\']DATAMART_DATABASE_URL',
    ]
    for pattern in read_patterns:
        assert not re.search(pattern, source), f"padrao proibido encontrado: {pattern}"
