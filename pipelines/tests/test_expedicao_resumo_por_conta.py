"""EXP-3B2-H3 - a chave canonica da conta e o invariante do lote.

O DEFEITO
---------
O resumo era indexado pela MARCA e a fila pelo `seller_id`. `por_conta` ficava
com as chaves `"2227056661"`, `"2532564723"`, ... e `build_account_summaries`
procurava `"kokeshi"`, `"barbours"`, ... Nao achava nada, e as quatro contas
saiam com `backlog_count = 0` enquanto 1.056 pedidos eram publicados na fila.

Nada no caminho percebeu: a fila estava certa, o resumo estava internamente
consistente (zero pedidos, zero em todas as faixas) e o commit foi aceito. Foi
preciso ler as duas tabelas lado a lado para ver a contradicao.

COMO ESTE ARQUIVO PROVA A CORRECAO
-----------------------------------
Primeiro reproduz o defeito: com contas indexadas por marca, o resumo sai
zerado apesar de a fila ter linhas. Depois mostra a mesma fila com a chave
canonica produzindo as contagens reais. Por fim exercita o invariante, que e' a
parte que sobrevive a proxima mudanca de contrato - ele recusa QUALQUER lote em
que fila e resumo nao fechem, seja qual for a causa.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipelines.expedicao import transform
from pipelines.expedicao.coerencia import (
    CAMPOS_DE_PRAZO,
    CAMPOS_TRANSVERSAIS,
    problemas_do_lote,
)
from pipelines.expedicao.contract import Channel, SellerAccount

AGORA = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
LOTE = "lote-h3"
CANAL = Channel.MERCADOLIVRE.value

#: Quatro contas, quatro marcas — o desenho de producao.
REGISTRY = {
    "2227056661": SellerAccount("2227056661", 3, "kokeshi"),
    "2532564723": SellerAccount("2532564723", 2, "barbours"),
    "2579732860": SellerAccount("2579732860", 4, "lescent"),
    "1366932565": SellerAccount("1366932565", 5, "rituaria"),
}

#: Distribuicao MEDIDA no piloto EXP-3B2-P3 (720 linhas). Esta aqui como
#: contraprova historica do comportamento, nao como constante de negocio: o
#: que o teste afirma e' que a soma fecha e que cada conta recebe o seu, nao
#: que a operacao tenha esses volumes.
PILOTO = {
    "2227056661": 433,  # kokeshi
    "2579732860": 147,  # lescent
    "2532564723": 138,  # barbours
    "1366932565": 2,    # rituaria
}
PILOTO_TOTAL = 720


def linha_ml(seller_id, i, *, extracted_at=None):
    """Uma linha crua da fonte, no formato que `build_fila_ml` consome."""
    base = datetime(2026, 9, 21, 12, 0)
    return {
        "seller_id": int(seller_id),
        "brand": REGISTRY[seller_id].brand_key,
        "shipment_id": int(f"9{seller_id[:4]}{i:05d}"),
        "order_id": 5000000 + i,
        "shipment_status": "ready_to_ship",
        "substatus": None,
        "logistic_type": "cross_docking",
        "order_status": "paid",
        "order_created_at": base - timedelta(hours=2),
        "date_created": base,
        "date_ready_to_ship": base,
        "date_shipped": None,
        "date_cancelled": None,
        "tracking_method": "Normal",
        "extracted_at": extracted_at or datetime(2026, 9, 22, 14, 30),
    }


def fila_com(distribuicao):
    linhas = []
    contador = 0
    for seller, quantidade in distribuicao.items():
        for _ in range(quantidade):
            contador += 1
            linhas.append(linha_ml(seller, contador))
    return transform.build_fila_ml(linhas, REGISTRY, AGORA, LOTE)


def contas_canonicas(registry=REGISTRY):
    """Como o `cli` monta: chave canonica -> (chave canonica, marca)."""
    return {ext: (ext, c.brand_key) for ext, c in registry.items()}


def contas_por_marca(registry=REGISTRY):
    """O DEFEITO, preservado como fixture: a conta identificada pela marca."""
    return {ext: (c.brand_key, c.brand_key) for ext, c in registry.items()}


def resumir(fila, accounts):
    return transform.build_account_summaries(
        fila,
        AGORA,
        channel=CANAL,
        refresh_batch_id=LOTE,
        accounts=accounts,
        watermarks={ext: AGORA - timedelta(minutes=10) for ext in REGISTRY},
        source_advanced=False,
    )


# ---------------------------------------------------------------------------
# Contraprova do defeito
# ---------------------------------------------------------------------------
def test_chave_por_marca_reproduz_os_quatro_zeros_do_incidente():
    """Sem a correcao, 720 linhas viram quatro resumos zerados."""
    fila = fila_com(PILOTO)
    assert len(fila) == PILOTO_TOTAL

    resumos = resumir(fila, contas_por_marca())

    assert [r["backlog_count"] for r in resumos] == [0, 0, 0, 0]
    assert sum(r["backlog_count"] for r in resumos) == 0, (
        "e' exatamente este o estado publicado em 22/09: fila cheia, resumo zero"
    )


def test_chave_canonica_produz_as_contagens_reais():
    """Com a correcao, cada conta recebe o seu e a soma fecha com a fila."""
    fila = fila_com(PILOTO)
    resumos = resumir(fila, contas_canonicas())

    obtido = {r["shop_account"]: r["backlog_count"] for r in resumos}
    assert obtido == PILOTO
    assert sum(obtido.values()) == len(fila) == PILOTO_TOTAL


def test_marca_do_resumo_vem_do_registry_e_nao_da_fonte():
    fila = fila_com({"2227056661": 3})
    resumos = resumir(fila, contas_canonicas())
    marcas = {r["shop_account"]: r["brand"] for r in resumos}
    assert marcas["2227056661"] == REGISTRY["2227056661"].brand_key
    assert "2227056661" not in marcas.values(), (
        "seller_id nunca pode ser confundido com nome de marca"
    )


# ---------------------------------------------------------------------------
# Casos obrigatorios do contrato de identidade
# ---------------------------------------------------------------------------
def test_duas_contas_da_mesma_marca_continuam_distintas():
    """Marca NAO e' conta: duas contas da mesma marca tem resumos separados."""
    registry = {
        "111": SellerAccount("111", 3, "kokeshi"),
        "222": SellerAccount("222", 3, "kokeshi"),
    }
    linhas = [linha_ml("2227056661", i) for i in range(5)]
    for i, l in enumerate(linhas):
        l["seller_id"] = 111 if i < 3 else 222
        l["brand"] = "kokeshi"
    fila = transform.build_fila_ml(linhas, registry, AGORA, LOTE)
    resumos = transform.build_account_summaries(
        fila, AGORA, channel=CANAL, refresh_batch_id=LOTE,
        accounts={ext: (ext, c.brand_key) for ext, c in registry.items()},
        watermarks={ext: AGORA for ext in registry}, source_advanced=False,
    )
    obtido = {r["shop_account"]: r["backlog_count"] for r in resumos}
    assert obtido == {"111": 3, "222": 2}
    assert {r["brand"] for r in resumos} == {"kokeshi"}, "a marca continua a mesma"


def test_conta_sem_linha_produz_resumo_explicito_com_zero():
    """Backlog zero e' um FATO publicado, nao uma linha ausente."""
    fila = fila_com({"2227056661": 4})
    resumos = resumir(fila, contas_canonicas())
    assert len(resumos) == 4, "uma linha por conta esperada, sempre"
    zeradas = {r["shop_account"] for r in resumos if r["backlog_count"] == 0}
    assert zeradas == set(REGISTRY) - {"2227056661"}


def test_ordem_das_entradas_nao_muda_o_resumo():
    """Permutar a fila nao pode mudar contagem nenhuma."""
    fila = fila_com(PILOTO)
    direto = resumir(fila, contas_canonicas())
    invertido = resumir(list(reversed(fila)), contas_canonicas())

    def chaveado(rs):
        return {r["shop_account"]: r["backlog_count"] for r in rs}

    assert chaveado(direto) == chaveado(invertido)


def test_faixas_de_prazo_somam_o_backlog_de_cada_conta():
    fila = fila_com(PILOTO)
    for r in resumir(fila, contas_canonicas()):
        assert sum(r[c] for c in CAMPOS_DE_PRAZO) == r["backlog_count"]


def test_dimensoes_transversais_nao_sao_somadas():
    """`stalled` e' o OR de lento e zumbi - somar contaria a sobreposicao 2x."""
    fila = fila_com(PILOTO)
    for r in resumir(fila, contas_canonicas()):
        for campo in CAMPOS_TRANSVERSAIS:
            assert 0 <= r[campo] <= r["backlog_count"]


# ---------------------------------------------------------------------------
# Invariante do lote
# ---------------------------------------------------------------------------
def test_invariante_aprova_um_lote_coerente():
    fila = fila_com(PILOTO)
    resumos = resumir(fila, contas_canonicas())
    assert problemas_do_lote(
        CANAL, fila, resumos, expected_accounts=set(REGISTRY)
    ) == []


def test_invariante_reprova_o_lote_do_incidente():
    """A prova central: o lote publicado em 22/09 nao passaria hoje."""
    fila = fila_com(PILOTO)
    resumos = resumir(fila, contas_por_marca())
    problemas = problemas_do_lote(
        CANAL, fila, resumos, expected_accounts=set(REGISTRY)
    )
    assert problemas
    junto = " ".join(problemas)
    assert "soma dos resumos (0)" in junto
    assert f"({PILOTO_TOTAL})" in junto


def test_invariante_reprova_conta_faltando_no_resumo():
    fila = fila_com(PILOTO)
    resumos = [r for r in resumir(fila, contas_canonicas())
               if r["shop_account"] != "1366932565"]
    problemas = problemas_do_lote(
        CANAL, fila, resumos, expected_accounts=set(REGISTRY)
    )
    assert any("faltando=['1366932565']" in p for p in problemas)


def test_invariante_reprova_conta_repetida_no_resumo():
    fila = fila_com({"2227056661": 2})
    resumos = resumir(fila, contas_canonicas())
    problemas = problemas_do_lote(
        CANAL, fila, resumos + [dict(resumos[0])],
        expected_accounts=set(REGISTRY),
    )
    assert any("conta repetida" in p for p in problemas)


def test_invariante_reprova_seller_desconhecido_na_fila():
    fila = fila_com({"2227056661": 2})
    intrusa = dict(fila[0])
    intrusa["shop_account"] = "999999999"
    resumos = resumir(fila, contas_canonicas())
    problemas = problemas_do_lote(
        CANAL, fila + [intrusa], resumos, expected_accounts=set(REGISTRY)
    )
    assert any("conta desconhecida" in p for p in problemas)


def test_invariante_reprova_contagem_negativa():
    fila = fila_com({"2227056661": 1})
    resumos = resumir(fila, contas_canonicas())
    alvo = next(r for r in resumos if r["shop_account"] == "1366932565")
    alvo["slow_count"] = -1
    problemas = problemas_do_lote(
        CANAL, fila, resumos, expected_accounts=set(REGISTRY)
    )
    assert any("negativa" in p for p in problemas)


def test_invariante_reprova_faixa_de_prazo_que_nao_fecha():
    fila = fila_com({"2227056661": 3})
    resumos = resumir(fila, contas_canonicas())
    alvo = next(r for r in resumos if r["shop_account"] == "2227056661")
    alvo["deadline_unavailable_count"] -= 1
    problemas = problemas_do_lote(
        CANAL, fila, resumos, expected_accounts=set(REGISTRY)
    )
    assert any("faixas de prazo somam" in p for p in problemas)


def test_invariante_reprova_transversal_maior_que_o_backlog():
    fila = fila_com({"2227056661": 2})
    resumos = resumir(fila, contas_canonicas())
    alvo = next(r for r in resumos if r["shop_account"] == "2227056661")
    alvo["zombie_count"] = alvo["backlog_count"] + 1
    problemas = problemas_do_lote(
        CANAL, fila, resumos, expected_accounts=set(REGISTRY)
    )
    assert any("transversal maior que o backlog" in p for p in problemas)


def test_mensagem_do_invariante_nao_vaza_identificador_de_pedido():
    """Erro que vaza pedido troca um defeito por outro."""
    fila = fila_com(PILOTO)
    resumos = resumir(fila, contas_por_marca())
    junto = " ".join(
        problemas_do_lote(CANAL, fila, resumos, expected_accounts=set(REGISTRY))
    )
    for linha in fila:
        assert str(linha["marketplace_order_id"]) not in junto


@pytest.mark.parametrize("bruto", [None, "", "   ", True, False])
def test_seller_id_invalido_e_recusado_sem_adivinhar(bruto):
    """Nunca derivar a conta pela marca, nem aceitar um seller vazio."""
    from pipelines.expedicao.contract import RegistryError
    from pipelines.expedicao.ml_extract import _seller_id_canonico

    with pytest.raises(RegistryError):
        _seller_id_canonico(bruto)


def test_seller_id_numerico_vira_texto_deterministico():
    from pipelines.expedicao.ml_extract import _seller_id_canonico

    assert _seller_id_canonico(2227056661) == "2227056661"
    assert _seller_id_canonico(" 2227056661 ") == "2227056661"


def test_invariante_reprova_distribuicao_errada_com_soma_certa():
    """O caso que a soma total NAO pega.

    Trocar as contagens entre duas contas mantem o total e mente sobre as duas.
    Sem a conferencia POR CONTA, um resumo assim passaria.
    """
    fila = fila_com({"2227056661": 5, "2532564723": 2})
    resumos = resumir(fila, contas_canonicas())
    a = next(r for r in resumos if r["shop_account"] == "2227056661")
    b = next(r for r in resumos if r["shop_account"] == "2532564723")
    a["backlog_count"], b["backlog_count"] = b["backlog_count"], a["backlog_count"]

    problemas = problemas_do_lote(
        CANAL, fila, resumos, expected_accounts=set(REGISTRY)
    )
    assert sum(r["backlog_count"] for r in resumos) == len(fila), (
        "o total continua fechando - e' justamente esse o disfarce"
    )
    assert any("backlog_count=2 mas a fila tem 5" in p for p in problemas)


def test_watermark_do_ml_e_agrupado_so_por_seller():
    """O grao do watermark decide se duas linhas colidem na mesma chave.

    Com `shop_account = seller_id`, agrupar tambem por marca devolveria duas
    entradas com a MESMA chave para um seller com duas marcas - uma apagaria a
    outra em silencio, e o carimbo publicado seria o da ultima lida, nao o
    maximo da conta. Conferido no texto do SQL porque o agrupamento acontece no
    banco: nenhum duble consegue prova-lo.
    """
    import re

    from pipelines.expedicao.ml_extract import ML_WATERMARK_SQL

    grupo = re.search(r"GROUP BY(.+)", ML_WATERMARK_SQL, re.S).group(1)
    assert "seller_id" in grupo
    assert "brand" not in grupo, (
        "agrupar por marca faz duas linhas competirem pela mesma chave de conta"
    )
