"""Alertas criticos do bot (A2 LDR acima do teto, A3 performance da Kokeshi)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.schemas.performance_oficial import ContaOficial, Destaque, MetricaOficial
from app.services import torre_alertas as ta
from app.services.slack_dm import Item

AGORA = datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc)


def _resp(brands, **kw):
    return {"availability": "available", "brands": brands, **kw}


def test_classificar_limite_exato_conta_como_dentro():
    assert ta.classificar(0.06, "mercadolivre") == ta.ATENCAO
    assert ta.classificar(0.0601, "mercadolivre") == ta.CRITICO
    assert ta.classificar(0.02, "tiktokshop") == ta.DENTRO
    assert ta.classificar(0.041, "tiktokshop") == ta.CRITICO
    assert ta.classificar(0.05, "shopee") == ta.ATENCAO


def test_celulas_do_canal_filtra_contas_e_sem_base():
    r = _resp([
        {"brand": "rituaria", "base": 82, "late": 9, "rate": 9 / 82},
        {"brand": "apice", "base": 50, "late": 1, "rate": 0.02},   # sem conta ML
        {"brand": "lescent", "base": 0, "late": 0, "rate": None},  # sem base
    ], mature_days=1, requested_days=7)
    (c,) = ta.celulas_do_canal("mercadolivre", r)
    assert (c.marca, c.faixa, c.parcial) == ("rituaria", ta.CRITICO, "janela parcial: 1 de 7 dias")


def test_canal_indisponivel_vira_none_nunca_zero():
    assert ta.celulas_do_canal("shopee", {"availability": "unavailable"}) is None
    assert ta.celulas_do_canal("shopee", {"availability": "available"}) is None


def test_itens_ldr_so_acima_do_teto_e_com_base_minima():
    leitura = ta.LeituraLdr(celulas=[
        ta.CelulaLdr("mercadolivre", "rituaria", 0.11, 9, 82, ta.CRITICO),
        ta.CelulaLdr("tiktokshop", "kokeshi", 0.03, 371, 12503, ta.ATENCAO),
        ta.CelulaLdr("shopee", "lescent", 0.2, 2, 10, ta.CRITICO),  # base pequena
    ])
    (item,) = ta.itens_ldr_criticos(leitura, date(2026, 11, 5))
    assert item.chave == "ldr:mercadolivre:rituaria:2026-11-05"
    assert "RITUÁRIA no Mercado Livre: LDR 11,0% (9 de 82 pedidos; meta 3,0%, teto 6,0%)" in item.texto


def test_ler_ldr_isola_canal_que_falha(monkeypatch):
    class Db:
        def rollback(self):
            pass

    def quebra(db, agora):
        raise RuntimeError("dsn secreto")

    monkeypatch.setattr(ta, "FONTES_LDR", {
        "shopee": quebra,
        "mercadolivre": lambda db, agora: _resp([{"brand": "kokeshi", "base": 340, "late": 0, "rate": 0.0}]),
        "tiktokshop": lambda db, agora: {"availability": "unavailable"},
    })
    leitura = ta.ler_ldr(Db(), AGORA)
    assert leitura.indisponiveis == ["Shopee", "TikTok Shop"]
    assert [(c.canal, c.marca) for c in leitura.celulas] == [("mercadolivre", "kokeshi")]


def test_shopee_cai_na_reserva_do_datamart_sem_publicacao(monkeypatch):
    monkeypatch.setattr(ta.shopee_ldr, "obter_ldr", lambda db, **kw: {"availability": "unavailable"})
    monkeypatch.setattr(ta.shopee_dm, "obter_ldr_shopee_dm", lambda **kw: _resp([]))
    assert ta._shopee(object(), AGORA) == _resp([])


def _metrica(codigo, valor, meta, situacao):
    return MetricaOficial(codigo=codigo, rotulo=codigo, valor=valor, unidade="pct", meta=meta,
                          comparador="<=", situacao=situacao)


def _conta(**kw):
    base = dict(marca="kokeshi", dia=date(2026, 11, 4), capturado_em=AGORA,
                nivel="MercadoLíder Platinum", tom="bom",
                destaques=[Destaque(rotulo="Termômetro", valor="Verde", tom="bom")], metricas=[])
    base.update(kw)
    return ContaOficial(**base)


def test_kokeshi_em_ordem_nao_gera_sinal():
    assert ta.sinais_da_conta(_conta(metricas=[_metrica("ml_claims", 0.58, 1.0, "ok")])) == []


def test_kokeshi_metrica_fora_e_critica_e_perto_e_atencao():
    conta = _conta(metricas=[_metrica("ml_claims", 1.2, 1.0, "fora"),
                             _metrica("ml_delayed_handling_time", 5.0, 6.0, "ok")])
    s1, s2 = ta.sinais_da_conta(conta)
    assert (s1.nivel, s1.chave) == (ta.CRITICO, "saude:mercadolivre:kokeshi:ml_claims:2026-11-04")
    assert "1,2%, acima do limite de 1%" in s1.texto
    assert s2.nivel == ta.ATENCAO and "5%, perto do limite de 6%" in s2.texto


def test_kokeshi_termometro_fora_do_verde_e_sem_lider_sao_criticos():
    conta = _conta(nivel="Amarelo", tom="atencao",
                   destaques=[Destaque(rotulo="Termômetro", valor="Amarelo", tom="atencao")])
    codigos = {s.codigo for s in ta.sinais_da_conta(conta) if s.nivel == ta.CRITICO}
    assert codigos == {"termometro", "lider"}


def test_ler_saude_fonte_fora_marca_indisponivel():
    def quebra(**kw):
        raise RuntimeError("x")

    assert ta.ler_saude(AGORA, montar=quebra).indisponivel


def test_mensagem_criticos_agrupa_ldr_e_saude():
    texto = ta.montar_mensagem_criticos([Item("ldr:a", "L1"), Item("saude:b", "S1")])
    assert texto.index("LDR acima do teto") < texto.index("L1") < texto.index("Performance oficial")
    assert "S1" in texto and "Abrir a Expedição" in texto
