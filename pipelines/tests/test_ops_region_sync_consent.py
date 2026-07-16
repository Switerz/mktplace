"""
Testes de pipelines/ops/region_sync_consent.py: consentimento PERSISTENTE e
gitignored para o sync regional agendado (Gate B6.1b). Nunca cria/toca o
arquivo real do projeto (`.env.region-sync.local`) — sempre usa tmp_path.
Nenhum banco tocado.
"""
import os
from pathlib import Path

import pytest

import pipelines.ops.region_sync_consent as rsc

_KEY = "I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY"


@pytest.fixture(autouse=True)
def _clean_env():
    """A propria funcao sob teste muta os.environ diretamente (nunca via
    monkeypatch, que so' o teste pode chamar) — limpa antes E depois de
    cada teste para nunca vazar entre eles, mesmo se um teste falhar no
    meio."""
    os.environ.pop(_KEY, None)
    yield
    os.environ.pop(_KEY, None)


def test_env_var_ja_definida_vence_sem_ler_arquivo(monkeypatch, tmp_path):
    monkeypatch.setenv(_KEY, "1")
    missing_path = tmp_path / "nao-existe.local"
    assert rsc.ensure_region_sync_consent(env_path=missing_path) is True


def test_sem_env_e_sem_arquivo_retorna_false(tmp_path):
    missing_path = tmp_path / ".env.region-sync.local"
    assert rsc.ensure_region_sync_consent(env_path=missing_path) is False
    assert os.environ.get(_KEY) is None


def test_arquivo_com_valor_1_seta_em_os_environ(tmp_path):
    consent_file = tmp_path / ".env.region-sync.local"
    consent_file.write_text(f"{_KEY}=1\n", encoding="utf-8")
    assert rsc.ensure_region_sync_consent(env_path=consent_file) is True
    assert os.environ.get(_KEY) == "1"


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_arquivo_com_valor_invalido_nao_seta_e_retorna_false(tmp_path, value):
    consent_file = tmp_path / ".env.region-sync.local"
    consent_file.write_text(f"{_KEY}={value}\n", encoding="utf-8")
    assert rsc.ensure_region_sync_consent(env_path=consent_file) is False
    assert os.environ.get(_KEY) is None


def test_arquivo_vazio_retorna_false(tmp_path):
    consent_file = tmp_path / ".env.region-sync.local"
    consent_file.write_text("", encoding="utf-8")
    assert rsc.ensure_region_sync_consent(env_path=consent_file) is False


def test_arquivo_com_chave_ausente_retorna_false(tmp_path):
    consent_file = tmp_path / ".env.region-sync.local"
    consent_file.write_text("OUTRA_CHAVE=qualquer\n", encoding="utf-8")
    assert rsc.ensure_region_sync_consent(env_path=consent_file) is False


def test_arquivo_com_chaves_extras_e_ignorado_sem_falhar(tmp_path):
    """Diferente de write_conn.load_write_secret (que exige EXATAMENTE as
    chaves esperadas, por ser um secret de conexao real): este arquivo so'
    tem um consentimento booleano, sem DSN — chaves extras nao sao um risco
    de seguranca aqui, so' sao ignoradas."""
    consent_file = tmp_path / ".env.region-sync.local"
    consent_file.write_text(f"ALGUMA_OUTRA_CHAVE=xyz\n{_KEY}=1\n", encoding="utf-8")
    assert rsc.ensure_region_sync_consent(env_path=consent_file) is True


def test_default_path_e_usado_quando_env_path_omitido(monkeypatch, tmp_path):
    """env_path=None (padrao) resolve DEFAULT_REGION_SYNC_CONSENT_PATH no
    MOMENTO da chamada -- confirma que basta monkeypatchar a constante do
    modulo, sem precisar passar env_path explicitamente."""
    consent_file = tmp_path / ".env.region-sync.local"
    consent_file.write_text(f"{_KEY}=1\n", encoding="utf-8")
    monkeypatch.setattr(rsc, "DEFAULT_REGION_SYNC_CONSENT_PATH", consent_file)
    assert rsc.ensure_region_sync_consent() is True


def test_nunca_cria_o_arquivo_de_consentimento(tmp_path):
    missing_path = tmp_path / ".env.region-sync.local"
    rsc.ensure_region_sync_consent(env_path=missing_path)
    assert not missing_path.exists(), "ensure_region_sync_consent nunca deve criar o arquivo"


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

MODULE_PATH = Path(rsc.__file__)


def test_modulo_nunca_escreve_em_disco():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in (".write_text(", ".write(", "open(", ".touch("):
        assert forbidden not in source, f"padrao proibido encontrado: {forbidden}"


def test_modulo_nunca_imprime_nada():
    """Modulo puro (sem CLI/print) -- o conteudo do arquivo de consentimento
    nunca deve ser exposto, nem por engano num print futuro."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "print(" not in source


def test_nome_do_arquivo_padrao_e_o_documentado():
    assert rsc.DEFAULT_REGION_SYNC_CONSENT_PATH.name == ".env.region-sync.local"
