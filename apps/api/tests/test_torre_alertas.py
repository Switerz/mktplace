"""Alertas criticos do bot (A2 LDR acima do teto, A3 performance da Kokeshi)."""
from __future__ import annotations

import json
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
    assert "1,2%, fora da meta (máximo 1% para MercadoLíder)" in s1.texto
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


def _blocos_em_texto(m) -> str:
    return json.dumps(m.blocos, ensure_ascii=False)


def test_mensagem_criticos_um_grupo_por_canal_com_link_filtrado():
    rit = ta.CelulaLdr("mercadolivre", "rituaria", 0.444, 87, 196, ta.CRITICO, "janela parcial: 2 de 7 dias")
    api = ta.CelulaLdr("tiktokshop", "apice", 0.231, 1844, 7990, ta.CRITICO)
    koke = ta.CelulaLdr("tiktokshop", "kokeshi", 0.116, 2801, 24140, ta.CRITICO)
    sinal = ta.SinalSaude("kokeshi", "mercadolivre", ta.CRITICO, "ml_claims",
                          "KOKESHI no Mercado Livre: Reclamações em 1,2%.", date(2026, 11, 4))
    itens = [Item("ldr:ml", ta.texto_celula(rit), rit), Item("ldr:tk1", ta.texto_celula(api), api),
             Item("ldr:tk2", ta.texto_celula(koke), koke), Item(sinal.chave, sinal.texto, sinal)]
    m = ta.montar_mensagem_criticos(itens, AGORA)
    assert m.texto == (":rotating_light: Torre: LDR acima do teto em 3 contas · "
                       "1 sinal na performance oficial")
    assert "2 sinais" in ta._resumo_criticos([], [], 2)
    assert m.blocos[0] == {"type": "header", "text": {
        "type": "plain_text", "text": ":rotating_light: Torre · alerta crítico", "emoji": True}}
    secoes = [b for b in m.blocos if b["type"] == "section"]
    ml, tk, saude = secoes
    assert "*RITUÁRIA*  44,4%" in ml["text"]["text"] and "_janela parcial: 2 de 7 dias_" in ml["text"]["text"]
    # Uma marca so' no canal: o botao ja' abre filtrado nela.
    assert ml["accessory"]["url"].endswith("/expedicao?channel=mercadolivre&marca=rituaria")
    assert tk["text"]["text"].index("ÁPICE") < tk["text"]["text"].index("KOKESHI")
    assert tk["accessory"]["url"].endswith("/expedicao?channel=tiktokshop")
    assert ":red_circle: Reclamações em 1,2%" in saude["text"]["text"]
    assert saude["accessory"]["url"].endswith("/performance-contas?canal=mercado_livre&brands=kokeshi")


def test_mensagem_criticos_item_sem_dados_ainda_aparece():
    m = ta.montar_mensagem_criticos([Item("ldr:a", "L1"), Item("saude:b", "S1")], AGORA)
    assert "L1" in _blocos_em_texto(m) and "S1" in _blocos_em_texto(m)


# --- A4: vencidos parados no TikTok -----------------------------------------
def _linha(marca, deadline, madura, venc, no_prazo):
    return {"brand": marca, "coleta_deadline": deadline, "coleta_is_mature": madura,
            "coleta_pendente_vencida": venc, "coleta_pendente_no_prazo": no_prazo,
            "effective_at": AGORA}


class _DbTiktok:
    def __init__(self, linhas):
        self.linhas = linhas

    def execute(self, sql, params=None):
        linhas = self.linhas

        class R:
            def scalar(self):
                return True

            def mappings(self):
                class M:
                    def all(self):
                        return linhas
                return M()
        return R()


def test_vencidos_tiktok_usa_os_recortes_da_tela(monkeypatch):
    monkeypatch.setattr(ta.expedicao_service, "_tiktok_fotografia_velha", lambda p, a: False)
    db = _DbTiktok([
        _linha("apice", date(2026, 11, 4), True, 40, 0),
        _linha("apice", date(2026, 10, 20), True, 99, 0),   # vencimento fora da janela
        _linha("apice", date(2026, 11, 6), False, 0, 60),   # coorte aberta: so' no prazo
        _linha("denavita", date(2026, 11, 4), True, 5, 0),  # sem conta no TikTok
    ])
    leitura = ta.ler_vencidos_tiktok(db, AGORA)
    (m,) = leitura.marcas
    assert (m.marca, m.vencidos, m.fila) == ("apice", 40, 100)


def _leitura(*marcas, velha=False):
    return ta.LeituraVencidos("tiktokshop", AGORA, [ta.VencidosMarca(*m) for m in marcas], velha)


def test_vencidos_acima_de_3_porcento_vira_item_por_turno():
    (item,) = ta.itens_vencidos(_leitura(("apice", 1624, 3777), ("rituaria", 1, 400)), AGORA)
    assert item.chave == "vencidos:tiktokshop:2026-11-05:09"  # 14h UTC = 11h BRT
    assert item.texto.startswith("TikTok Shop: 1.625 de 4.177 pedidos parados")
    assert "ÁPICE 1.624 (43,0%)" in item.texto and "RITUÁRIA" not in item.texto


def test_vencidos_nao_avisa_dentro_do_limite_velha_ou_base_pequena():
    assert ta.itens_vencidos(_leitura(("apice", 3, 100)), AGORA) == []
    assert ta.itens_vencidos(_leitura(("apice", 50, 100), velha=True), AGORA) == []
    assert ta.itens_vencidos(_leitura(("apice", 10, 20)), AGORA) == []
    assert ta.itens_vencidos(None, AGORA) == []


def test_turno_de_brasilia():
    assert ta.turno(datetime(2026, 11, 5, 11, 59, tzinfo=timezone.utc)) == "00"  # 8h59
    assert ta.turno(datetime(2026, 11, 5, 16, 0, tzinfo=timezone.utc)) == "13"
    assert ta.turno(datetime(2026, 11, 5, 23, 0, tzinfo=timezone.utc)) == "17"


def test_mensagem_criticos_vencidos_com_marcas_e_fila_detalhada():
    v = _leitura(("apice", 716, 2971), ("rituaria", 21, 500))
    m = ta.montar_mensagem_criticos([Item("vencidos:tiktokshop:d:09", "V1", v)], AGORA)
    (sec,) = [b for b in m.blocos if b["type"] == "section"]
    assert "*737* de 3.471 pedidos parados" in sec["text"]["text"]
    assert ":red_circle: *ÁPICE*  24,1%" in sec["text"]["text"]
    assert ":red_circle: *RITUÁRIA*  4,2%" in sec["text"]["text"]
    assert sec["accessory"]["url"].endswith("/expedicao?channel=tiktokshop&visao=detalhe")
    assert "vencidos no TikTok Shop" in m.texto



# --- A3 nos tres canais ---------------------------------------------------------
def _metrica_shopee(mid, valor, meta, comp, unidade, situacao, rotulo):
    return MetricaOficial(codigo=f"shopee_{mid}", rotulo=rotulo, valor=valor, unidade=unidade,
                          meta=meta, comparador=comp, situacao=situacao)


def test_shopee_nota_punicao_pontos_e_metricas():
    conta = _conta(marca="barbours", nivel="Ruim", tom="critico", destaques=[
        Destaque(rotulo="Pontos de penalidade no trimestre", valor="2", tom="atencao"),
        Destaque(rotulo="Punições em andamento", valor="1", tom="critico"),
    ], metricas=[
        _metrica_shopee(1, 9.5, 8.0, "<=", "pct", "fora", "Taxa de envio atrasado"),
        _metrica_shopee(4, 1.8, 2.0, "<=", "dias", "ok", "Tempo de preparação"),
        _metrica_shopee(11, 95.0, 90.0, ">=", "pct", "ok", "Taxa de resposta do chat"),
    ])
    por_codigo = {s.codigo: s for s in ta.sinais_da_conta(conta, "shopee")}
    assert set(por_codigo) == {"nota", "punicoes", "pontos", "shopee_1", "shopee_4"}
    assert por_codigo["nota"].nivel == ta.CRITICO and por_codigo["punicoes"].nivel == ta.CRITICO
    assert por_codigo["pontos"].nivel == ta.ATENCAO
    assert por_codigo["shopee_1"].texto == (
        "BARBOURS na Shopee: Taxa de envio atrasado em 9,5%, fora da meta (máximo 8% da Shopee).")
    assert por_codigo["shopee_4"].nivel == ta.ATENCAO and "1,8 dias, perto do limite de 2 dias" in por_codigo["shopee_4"].texto
    assert por_codigo["shopee_1"].chave == "saude:shopee:barbours:shopee_1:2026-11-04"


def test_shopee_em_ordem_nao_gera_sinal():
    conta = _conta(marca="apice", nivel="Excelente", tom="bom", destaques=[
        Destaque(rotulo="Pontos de penalidade no trimestre", valor="0", tom="bom"),
        Destaque(rotulo="Punições em andamento", valor="0", tom="bom"),
    ])
    assert ta.sinais_da_conta(conta, "shopee") == []


def test_ler_saude_le_os_tres_canais_e_separa_sem_fonte():
    class Resp:
        def __init__(self, estado, contas=()):
            self.estado, self.contas = estado, list(contas)

    respostas = {
        "mercadolivre": Resp("dado", [_conta(), _conta(marca="rituaria", metricas=[
            _metrica("ml_claims", 1.2, 1.0, "fora")])]),
        "shopee": Resp("erro"),
        "tiktokshop": Resp("sem_fonte"),
    }
    chamadas = []

    def montar(canal, brands, agora):
        chamadas.append((canal, brands))
        return respostas[canal]

    leitura = ta.ler_saude(AGORA, montar=montar)
    assert chamadas == [("mercadolivre", None), ("shopee", None), ("tiktokshop", None)]
    assert leitura.em_ordem == [("kokeshi", "mercadolivre")]
    assert [s.marca for s in leitura.sinais] == ["rituaria"]
    assert (leitura.indisponiveis, leitura.sem_fonte) == (["Shopee"], ["TikTok Shop"])
