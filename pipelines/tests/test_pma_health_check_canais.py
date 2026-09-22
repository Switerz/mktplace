"""Gate PMA-2C5B — frescor da FOTOGRAFIA de precos no health check.

O que se trava aqui: a defasagem aparece com a CAUSA, e o horario de publicacao
nunca mascara uma `observed_date` velha. Sao seis causas distintas porque pedem
seis acoes distintas do operador — "esta velho" sozinho nao diz se falta VPN,
se o publisher recusou, ou se a fotografia esta publicada e so' o registro
ficou pela metade.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pipelines.ops import health_check as hc

HOJE = date(2026, 9, 22)
AGORA = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)


class CursorFake:
    def __init__(self, conn):
        self.conn = conn
        self._sql = ""
        self._params = ()

    def execute(self, sql, params=None):
        self._sql = " ".join(sql.split())
        self._params = params or ()
        if self.conn.erro_leitura:
            raise RuntimeError("falha simulada de leitura")

    def fetchone(self):
        if "fact_marketplace_listing_price_daily" in self._sql:
            return {"d": self.conn.observadas.get("ml")}
        if "fact_channel_offer_observation" in self._sql:
            return {"d": self.conn.observadas.get(self._params[0])}
        if "audit.source_sync_run" in self._sql:
            return self.conn.execucoes.get(self._params[0])
        raise AssertionError(f"consulta sem ramo no fake: {self._sql[:80]}")

    def close(self):
        pass


class ConnFake:
    def __init__(self, observadas=None, execucoes=None, erro_leitura=False):
        self.observadas = observadas if observadas is not None else {
            "ml": HOJE - timedelta(days=1), "shopee": HOJE, "tiktok": HOJE}
        self.execucoes = execucoes if execucoes is not None else {
            "ml_listing_price_snapshot":
                {"status": "success", "started_at": AGORA, "error_message": None},
            "channel_offer_snapshot":
                {"status": "success", "started_at": AGORA, "error_message": None},
        }
        self.erro_leitura = erro_leitura

    def cursor(self):
        return CursorFake(self)


def _por_canal(conn, hoje=HOJE):
    return {x.channel: x for x in hc.fetch_pma_channel_status(conn, today=hoje)}


# ---------------------------------------------------------------------------
# 1. Fresco
# ---------------------------------------------------------------------------

def test_os_tres_canais_frescos_nao_sao_stale():
    r = _por_canal(ConnFake())
    assert set(r) == {"ml", "shopee", "tiktok"}
    for canal, x in r.items():
        assert x.status == hc.PMA_STATUS_OK, (canal, x.reason)
        assert x.stale is False


def test_o_ml_e_saudavel_em_d1_e_nao_exige_d0():
    """O ML fecha em D-1 por contrato; exigir D0 acusaria atraso todo dia."""
    r = _por_canal(ConnFake(observadas={
        "ml": HOJE - timedelta(days=1), "shopee": HOJE, "tiktok": HOJE}))
    assert r["ml"].stale is False


def test_o_ml_vira_stale_so_quando_anterior_a_d2():
    ontem_retrasado = HOJE - timedelta(days=3)
    r = _por_canal(ConnFake(observadas={
        "ml": ontem_retrasado, "shopee": HOJE, "tiktok": HOJE}))
    assert r["ml"].stale is True
    assert r["ml"].days_since == 3
    assert r["ml"].max_lag_days == 2


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_shopee_e_tiktok_aceitam_d1_e_reprovam_d2(canal):
    """A fonte so' fica pronta de manha: aceitar D-1 evita acusar atraso entre
    a meia-noite e a carga do dia."""
    obs = {"ml": HOJE - timedelta(days=1), "shopee": HOJE, "tiktok": HOJE}
    obs[canal] = HOJE - timedelta(days=1)
    assert _por_canal(ConnFake(observadas=obs))[canal].stale is False
    obs[canal] = HOJE - timedelta(days=2)
    assert _por_canal(ConnFake(observadas=obs))[canal].stale is True


# ---------------------------------------------------------------------------
# 2. As seis causas
# ---------------------------------------------------------------------------

def test_fotografia_ausente_tem_status_proprio():
    r = _por_canal(ConnFake(observadas={"ml": None, "shopee": HOJE, "tiktok": HOJE}))
    assert r["ml"].status == hc.PMA_STATUS_SEM_FOTOGRAFIA
    assert r["ml"].stale is True


def test_publisher_nunca_executado_e_distinguido_de_falha():
    """E' o estado de hoje: os publishers existem e ninguem os orquestra."""
    velha = HOJE - timedelta(days=5)
    r = _por_canal(ConnFake(
        observadas={"ml": velha, "shopee": velha, "tiktok": velha},
        execucoes={}))
    for canal in ("ml", "shopee", "tiktok"):
        assert r[canal].status == hc.PMA_STATUS_PUBLISHER_NAO_EXECUTADO
        assert "falta orquestracao" in r[canal].reason


def test_execucao_recusada_ou_falha_tem_status_proprio():
    velha = HOJE - timedelta(days=5)
    r = _por_canal(ConnFake(
        observadas={"ml": velha, "shopee": velha, "tiktok": velha},
        execucoes={
            "ml_listing_price_snapshot":
                {"status": "failed", "started_at": AGORA, "error_message": "recusa"},
            "channel_offer_snapshot":
                {"status": "failed", "started_at": AGORA, "error_message": "recusa"},
        }))
    assert r["ml"].status == hc.PMA_STATUS_RECUSADO


def test_lock_ocupado_e_distinguido_por_registro_em_running():
    velha = HOJE - timedelta(days=5)
    r = _por_canal(ConnFake(
        observadas={"ml": velha, "shopee": velha, "tiktok": velha},
        execucoes={
            "ml_listing_price_snapshot":
                {"status": "running", "started_at": AGORA, "error_message": None},
            "channel_offer_snapshot":
                {"status": "running", "started_at": AGORA, "error_message": None},
        }))
    assert r["shopee"].status == hc.PMA_STATUS_LOCK


def test_fonte_atrasada_quando_a_ultima_execucao_teve_sucesso():
    """Publisher rodou e deu certo, mas a fotografia continua velha: quem nao
    avancou foi a fonte."""
    velha = HOJE - timedelta(days=5)
    r = _por_canal(ConnFake(
        observadas={"ml": velha, "shopee": velha, "tiktok": velha}))
    assert r["tiktok"].status == hc.PMA_STATUS_FONTE_ATRASADA


def test_auditoria_incompleta_tem_precedencia_mesmo_com_fotografia_fresca():
    """Os dados podem estar publicados e so' o registro ficou pela metade —
    isso precisa de conserto manual mesmo sem atraso."""
    r = _por_canal(ConnFake(execucoes={
        "ml_listing_price_snapshot":
            {"status": "running", "started_at": AGORA,
             "error_message": "INDETERMINADO: conexao caiu no commit"},
        "channel_offer_snapshot":
            {"status": "success", "started_at": AGORA, "error_message": None},
    }))
    assert r["ml"].status == hc.PMA_STATUS_AUDITORIA_INCOMPLETA
    assert r["ml"].stale is True
    assert r["shopee"].status == hc.PMA_STATUS_OK


def test_leitura_que_falha_nao_e_confundida_com_fotografia_ausente():
    r = _por_canal(ConnFake(erro_leitura=True))
    for canal in ("ml", "shopee", "tiktok"):
        assert r[canal].status == hc.PMA_STATUS_ERRO


# ---------------------------------------------------------------------------
# 3. A regra que nao se negocia
# ---------------------------------------------------------------------------

def test_o_frescor_sai_da_data_OBSERVADA_e_nunca_do_horario_de_publicacao():
    """Uma republicacao de hoje de uma fotografia de 17/09 continua sendo uma
    fotografia de 17/09."""
    velha = HOJE - timedelta(days=5)
    r = _por_canal(ConnFake(
        observadas={"ml": velha, "shopee": velha, "tiktok": velha},
        execucoes={
            "ml_listing_price_snapshot":
                {"status": "success", "started_at": AGORA, "error_message": None},
            "channel_offer_snapshot":
                {"status": "success", "started_at": AGORA, "error_message": None},
        }))
    for canal in ("ml", "shopee", "tiktok"):
        assert r[canal].stale is True, (
            "execucao de hoje nao pode disfarcar observed_date de 5 dias")
        assert r[canal].observed_date == velha.isoformat()


def test_a_consulta_nunca_le_synced_at():
    import inspect
    fonte = inspect.getsource(hc.fetch_pma_channel_status)
    assert "synced_at" not in fonte, (
        "usar o horario de publicacao esconderia a defasagem que esta secao "
        "existe para revelar")


# ---------------------------------------------------------------------------
# 4. Integracao com o relatorio
# ---------------------------------------------------------------------------

def test_o_pma_nao_e_critico_enquanto_o_agendamento_nao_for_ativado():
    for x in hc.fetch_pma_channel_status(ConnFake(), today=HOJE):
        assert x.critical is False, (
            "com o agendamento desligado a defasagem e' gap CONHECIDO e nao "
            "deve derrubar o exit code todo dia")


def test_ok_critical_ignora_o_pma_mas_ok_o_enxerga():
    """Isola a CONTRIBUICAO do PMA: o mesmo fake, com e sem defasagem de
    fotografia. O `FakeConn` padrao ja tem outras dimensoes atrasadas, entao
    comparar contra um absoluto mediria o fixture, nao esta mudanca."""
    from pipelines.tests import test_ops_health_check as base
    velha = base.TODAY - timedelta(days=9)

    sem = hc.build_report(base.FakeConn(), now=base.NOW)
    com = hc.build_report(
        base.FakeConn(pma_observed={"ml": velha, "shopee": velha,
                                    "tiktok": velha}),
        now=base.NOW)

    assert all(not x["stale"] for x in sem["pma_channels"])
    assert all(x["stale"] for x in com["pma_channels"])
    assert com["ok"] is False, "a defasagem precisa aparecer em `ok`"
    assert com["ok_critical"] == sem["ok_critical"], (
        "o PMA nao pode mudar `ok_critical` enquanto for nao critico")
    assert [x["channel"] for x in com["pma_channels"]] == ["ml", "shopee", "tiktok"]


def test_o_pma_derruba_ok_quando_tudo_o_mais_esta_saudavel():
    """Contraprova do teste acima: a dimensao nao e' decorativa."""
    from pipelines.tests import test_ops_health_check as base
    velha = base.TODAY - timedelta(days=9)
    conn = base.FakeConn(pma_observed={"ml": velha, "shopee": velha,
                                       "tiktok": velha})
    rel = hc.build_report(conn, now=base.NOW)
    pma_stale = [x for x in rel["pma_channels"] if x["stale"]]
    assert len(pma_stale) == 3
    assert rel["ok"] is False
