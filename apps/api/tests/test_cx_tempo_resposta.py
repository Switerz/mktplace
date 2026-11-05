"""CX-TEMPO-RESPOSTA (05/11/2026): fila do chat e tempo de resposta da loja."""
from datetime import timedelta

from app.services import cx_atendimento_service as svc
from tests import test_cx_atendimento as base_cx
from tests.test_cx_shopee import AGORA, FonteCx, TorreCx, _montar

FOTO = {"marca": "apice", "unread_count": 3, "captured_at": AGORA, "conversas_30d": 180,
        "aguardando_resposta": 9, "aguardando_ate_1h": 4, "aguardando_1a4h": 2, "aguardando_4a24h": 1,
        "aguardando_mais_24h": 2, "espera_max_s": 3 * 86400}


def _fonte(respostas=True, tempo=None, falhas=()):
    f = FonteCx(nao_lidas=[FOTO, {"marca": "barbours", "unread_count": 1, "captured_at": AGORA}],
                falhas_coleta=falhas)
    f.coleta[svc.SQL_TABELAS_CX] = ("tabelas_cx", [{"returns": True, "chat": True, "respostas": respostas}])
    f.coleta[svc.SQL_TEMPO_RESPOSTA] = ("tempo_resposta", tempo if tempo is not None else [
        {"marca": "apice", "respostas": 40, "mediana_s": 754.4, "p90_s": 7200.0, "ate_1h": 30,
         "desde": AGORA - timedelta(hours=10)},
    ])
    return f


def test_fila_vem_da_foto_por_faixa_de_espera():
    resp, _ = _montar(fonte=_fonte())
    a = {n.marca: n for n in resp.chat.nao_lidas}["apice"]
    assert (a.aguardando_ate_1h, a.aguardando_1a4h, a.aguardando_4a24h, a.aguardando_mais_24h) == (4, 2, 1, 2)
    assert a.espera_max_s == 3 * 86400


def test_tempo_de_resposta_e_a_mediana_das_respostas_do_periodo():
    resp, fonte = _montar(fonte=_fonte(), dias=7)
    nl = {n.marca: n for n in resp.chat.nao_lidas}
    a = nl["apice"]
    assert (a.tempo_resposta_s, a.tempo_resposta_p90_s, a.respostas) == (754, 7200, 40)
    assert a.respostas_ate_1h_pct == 75.0 and a.tempo_resposta_fonte == "mediana"
    assert a.respostas_desde == AGORA - timedelta(hours=10)
    # loja sem resposta medida fica sem tempo (nunca zero)
    assert nl["barbours"].tempo_resposta_s is None and nl["barbours"].tempo_resposta_fonte is None
    params = next(p for n, p in fonte.chamadas if n == "tempo_resposta")
    assert params["desde"] == AGORA - timedelta(days=7) and set(params["marcas"]) <= set(svc.SHOPEE_MARCAS)


def test_sql_tira_respostas_automaticas():
    assert "!~* '(auto|bot)'" in svc.SQL_TEMPO_RESPOSTA
    assert "percentile_cont(0.5)" in svc.SQL_TEMPO_RESPOSTA
    assert "to_regclass('raw.shopee_chat_respostas')" in svc.SQL_TABELAS_CX


def test_sem_tabela_de_respostas_nao_consulta():
    resp, fonte = _montar(fonte=_fonte(respostas=False))
    assert "tempo_resposta" not in [n for n, _ in fonte.chamadas]
    assert all(n.tempo_resposta_s is None for n in resp.chat.nao_lidas)


def test_falha_no_tempo_de_resposta_nao_derruba_as_conversas():
    resp, _ = _montar(fonte=_fonte(falhas={"tempo_resposta"}))
    assert resp.chat.nao_lidas_estado == "dado"
    a = {n.marca: n for n in resp.chat.nao_lidas}["apice"]
    assert a.aguardando_resposta == 9 and a.tempo_resposta_s is None
    assert base_cx.SEGREDO not in resp.model_dump_json()


def test_kokeshi_usa_a_media_que_o_bot_le_no_seller_centre():
    torre = TorreCx(tabelas=(True, True), nao_lidas=[
        {"marca": "kokeshi", "unread_count": 2, "captured_at": AGORA, "tempo_resposta_medio_s": 2100}])
    resp, _ = _montar(fonte=_fonte(), torre=torre, brands=["kokeshi"])
    k = resp.chat.nao_lidas[0]
    assert (k.marca, k.tempo_resposta_s, k.tempo_resposta_fonte) == ("kokeshi", 2100, "media_seller_centre")
    assert "tempo_resposta_medio_s" in svc.SQL_NAO_LIDAS_EXPORT
