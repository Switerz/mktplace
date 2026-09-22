"""Gate PMA-2C5B — preflight por canal, sem tocar banco de verdade.

Lacunas que estes testes fecham (expostas pelas mutacoes M10 e M12 da Fase 6):
o guarda de fonte vazia e o guarda de regressao temporal nao tinham cobertura —
apaga-los mantinha a suite inteira verde, e em producao o primeiro deixaria o
caminho automatizado publicar sobre fonte que nao respondeu, e o segundo
deixaria uma fotografia mais nova ser reescrita com dado mais velho.

A conexao e' dublada no ponto exato em que o modulo a abre. Nenhum teste aqui
alcanca Neon, Data Mart ou qualquer URL configurada.
"""
from __future__ import annotations

from datetime import date

import pytest

from pipelines.ops import preflight as pf

HOJE = date(2026, 9, 22)


class CursorFake:
    def __init__(self, resposta):
        self.resposta = resposta

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())

    def fetchone(self):
        return self.resposta

    def close(self):
        pass


class ConnFake:
    def __init__(self, resposta):
        self.resposta = resposta
        self.readonly = None
        self.fechada = False

    def set_session(self, readonly=None):
        self.readonly = readonly

    def cursor(self):
        return CursorFake(self.resposta)

    def close(self):
        self.fechada = True


@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.setenv("DATAMART_DATABASE_URL", "postgresql://u@h:5432/fonte")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h:5432/destino")


def _responde(monkeypatch, por_url):
    """`por_url` mapeia um pedaco da URL para a linha devolvida."""
    abertas = []

    def connect(url, connect_timeout=None):
        for pedaco, resposta in por_url.items():
            if pedaco in url:
                c = ConnFake(resposta)
                abertas.append(c)
                return c
        raise AssertionError(f"URL inesperada no preflight: {url[:30]}")

    monkeypatch.setattr(pf.psycopg2, "connect", connect)
    return abertas


# ---------------------------------------------------------------------------
# 1. Fonte vazia BLOQUEIA (lacuna da M10)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("canal", ["ml", "shopee", "tiktok"])
def test_fonte_sem_data_maxima_bloqueia(ambiente, monkeypatch, canal):
    _responde(monkeypatch, {"fonte": (None, 0, 0)})
    check = getattr(pf, f"check_pma_{canal}_source")
    r = check()
    assert r.ok is False
    assert "VAZIA" in r.detail
    assert "nunca publica fotografia vazia" in r.detail


@pytest.mark.parametrize("canal", ["ml", "shopee", "tiktok"])
def test_fonte_com_zero_linhas_bloqueia(ambiente, monkeypatch, canal):
    _responde(monkeypatch, {"fonte": (HOJE, 0, 4)})
    r = getattr(pf, f"check_pma_{canal}_source")()
    assert r.ok is False, "data maxima sem linha nenhuma nao e' fotografia"


@pytest.mark.parametrize("canal,linhas,cobertura", [
    ("ml", 10, 4), ("shopee", 10, 4), ("tiktok", 10, 7),
])
def test_cobertura_de_linhas_abaixo_do_piso_bloqueia(ambiente, monkeypatch,
                                                     canal, linhas, cobertura):
    _responde(monkeypatch, {"fonte": (HOJE, linhas, cobertura)})
    r = getattr(pf, f"check_pma_{canal}_source")()
    assert r.ok is False
    assert "abaixo do piso" in r.detail


@pytest.mark.parametrize("canal,linhas,cobertura", [
    ("ml", 6000, 1), ("shopee", 600, 1), ("tiktok", 1200, 3),
])
def test_cobertura_de_marcas_ou_contas_abaixo_do_piso_bloqueia(
        ambiente, monkeypatch, canal, linhas, cobertura):
    """Volume alto com cobertura baixa e' pior que fonte vazia: parece
    saudavel e encolheria a fotografia em silencio."""
    _responde(monkeypatch, {"fonte": (HOJE, linhas, cobertura)})
    r = getattr(pf, f"check_pma_{canal}_source")()
    assert r.ok is False
    assert "abaixo do piso" in r.detail


@pytest.mark.parametrize("canal,linhas,cobertura", [
    ("ml", 6949, 4), ("shopee", 662, 4), ("tiktok", 1215, 7),
])
def test_fonte_saudavel_aprova(ambiente, monkeypatch, canal, linhas, cobertura):
    """Contraprova: os numeros MEDIDOS em 22/09/2026 passam."""
    _responde(monkeypatch, {"fonte": (HOJE, linhas, cobertura)})
    r = getattr(pf, f"check_pma_{canal}_source")()
    assert r.ok is True, r.detail


def test_fonte_inalcancavel_bloqueia_e_nao_vaza_credencial(ambiente, monkeypatch):
    def connect(url, connect_timeout=None):
        raise OSError("timeout")

    monkeypatch.setattr(pf.psycopg2, "connect", connect)
    r = pf.check_pma_shopee_source()
    assert r.ok is False
    assert "BLOQUEADO em vez de publicar vazio" in r.detail
    assert "postgresql://" not in r.detail
    assert "u@" not in r.detail


def test_o_preflight_nunca_abre_conexao_gravavel(ambiente, monkeypatch):
    abertas = _responde(monkeypatch, {"fonte": (HOJE, 6949, 4)})
    pf.check_pma_ml_source()
    assert abertas, "nenhuma conexao aberta"
    for c in abertas:
        assert c.readonly is True, "o preflight so' le"
        assert c.fechada is True


# ---------------------------------------------------------------------------
# 2. Regressao temporal BLOQUEIA (lacuna da M12)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("canal", ["ml", "shopee", "tiktok"])
def test_fotografia_mais_nova_que_a_fonte_bloqueia(ambiente, monkeypatch, canal):
    """Publicar nesse estado reescreveria uma fotografia mais nova com dado
    mais velho."""
    _responde(monkeypatch, {
        "destino": (date(2026, 9, 22),),
        "fonte": (date(2026, 9, 20),),
    })
    r = getattr(pf, f"check_pma_{canal}_target")()
    assert r.ok is False
    assert "REGRESSAO" in r.detail


@pytest.mark.parametrize("canal", ["ml", "shopee", "tiktok"])
def test_fonte_a_frente_da_fotografia_aprova(ambiente, monkeypatch, canal):
    _responde(monkeypatch, {
        "destino": (date(2026, 9, 17),),
        "fonte": (date(2026, 9, 22),),
    })
    r = getattr(pf, f"check_pma_{canal}_target")()
    assert r.ok is True
    assert "sem regressao" in r.detail


@pytest.mark.parametrize("canal", ["ml", "shopee", "tiktok"])
def test_datas_iguais_aprovam_como_rerun_idempotente(ambiente, monkeypatch, canal):
    _responde(monkeypatch, {
        "destino": (date(2026, 9, 22),),
        "fonte": (date(2026, 9, 22),),
    })
    assert getattr(pf, f"check_pma_{canal}_target")().ok is True


@pytest.mark.parametrize("canal", ["ml", "shopee", "tiktok"])
def test_destino_vazio_aprova(ambiente, monkeypatch, canal):
    """Primeira publicacao de um canal nao e' regressao."""
    _responde(monkeypatch, {
        "destino": (None,),
        "fonte": (date(2026, 9, 22),),
    })
    r = getattr(pf, f"check_pma_{canal}_target")()
    assert r.ok is True
    assert "destino vazio" in r.detail


@pytest.mark.parametrize("canal", ["ml", "shopee", "tiktok"])
def test_fonte_sem_data_maxima_bloqueia_o_destino(ambiente, monkeypatch, canal):
    _responde(monkeypatch, {
        "destino": (date(2026, 9, 17),),
        "fonte": (None,),
    })
    assert getattr(pf, f"check_pma_{canal}_target")().ok is False


# ---------------------------------------------------------------------------
# 3. Locks
# ---------------------------------------------------------------------------

def test_lock_tomado_bloqueia(ambiente, monkeypatch):
    _responde(monkeypatch, {"destino": (1,)})
    for check in (pf.check_pma_channel_lock_free, pf.check_pma_ml_lock_free):
        r = check()
        assert r.ok is False
        assert "ja detem o lock" in r.detail


def test_lock_livre_aprova(ambiente, monkeypatch):
    _responde(monkeypatch, {"destino": (0,)})
    for check in (pf.check_pma_channel_lock_free, pf.check_pma_ml_lock_free):
        assert check().ok is True


def test_cada_canal_registra_os_cinco_checks():
    for fonte in ("pma_ml", "pma_shopee", "pma_tiktok"):
        assert len(pf.SOURCE_CHECKS[fonte]) == 5, (
            f"{fonte} precisa de conectividade (2), fonte, destino e lock")
