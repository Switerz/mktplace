"""Comportamento do refresh de expedicao da Shopee.

Os fakes devolvem MAPPING, nao tupla: a producao usa `RealDictCursor` e um fake
posicional faria a suite inteira passar contra um runtime que quebra sempre.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from pipelines.expedicao import audit as audit_mod
from pipelines.expedicao import publisher, shopee_extract, transform
from pipelines.expedicao.cli import source_advanced
from pipelines.expedicao.contract import (
    Channel,
    DeadlineStatus,
    ExtractionResult,
    FreshnessStatus,
    OperationalAgeStatus,
    RegistryError,
    SellerAccount,
    SourceHealth,
    SourceUnhealthy,
    TimestampQuality,
)

BRT = timezone(timedelta(hours=-3))

#: 20:30 UTC de proposito: os testes de reexecucao na mesma hora precisam de
#: folga para os dois lados (20:10 e 20:50 continuam na hora 20).
AGORA = datetime(2026, 9, 15, 20, 30, 0, tzinfo=timezone.utc)
LOTE = "batch-0001"

#: Contas como o registry as devolve: external_seller_id -> SellerAccount.
REGISTRY = {
    "1609671923": SellerAccount("1609671923", 1, "apice"),
    "1579330222": SellerAccount("1579330222", 2, "barbours"),
    "1593864538": SellerAccount("1593864538", 4, "lescent"),
    "1457734799": SellerAccount("1457734799", 5, "rituaria"),
}
CONTAS = frozenset(REGISTRY)

#: external_seller_id -> (shop_account, brand_key). O `shop_account` vem da
#: FONTE (pk_shopee_orders); a marca vem do REGISTRY.
NOMES = {
    "1609671923": ("apice", "apice"),
    "1579330222": ("barbours", "barbours"),
    "1593864538": ("lescent", "lescent"),
    "1457734799": ("rituaria", "rituaria"),
}
WATERMARKS = {c: AGORA - timedelta(hours=1) for c in CONTAS}
#: Chaves CANONICAS das contas, como aparecem na fila e no resumo. E' o
#: conjunto que `publish_channel` exige para conferir a coerencia do lote.
CONTAS_CANONICAS = frozenset(nome for nome, _marca in NOMES.values())


def linha_fonte(
    *,
    shop_id="1609671923",
    shop_account=None,
    order_sn="X1",
    ship_by=None,
    horas_aberto=1.0,
    ingested_h=1.0,
    status="PROCESSED",
    carrier="Shopee Xpress",
    pay=True,
    base=None,
):
    agora = base or AGORA
    marco = agora - timedelta(hours=horas_aberto)
    return {
        "shop_account": shop_account or NOMES[shop_id][0],
        "order_sn": order_sn,
        "shop_id": shop_id,
        "order_status": status,
        "create_time": marco,
        "pay_time": marco if pay else None,
        "ship_by_date": ship_by,
        "pickup_done_time": None,
        "shipping_carrier": carrier,
        "ingested_at": agora - timedelta(hours=ingested_h),
    }


def fila(linhas, effective_at=AGORA, baselines=None, lote=LOTE):
    return transform.build_fila_shopee(
        linhas, REGISTRY, baselines or {}, effective_at, lote
    )


def resumos(f, effective_at=AGORA, *, lote=LOTE, contas=None, avancou=False):
    return transform.build_account_summaries(
        f,
        effective_at,
        channel=Channel.SHOPEE.value,
        refresh_batch_id=lote,
        accounts=contas if contas is not None else NOMES,
        watermarks=WATERMARKS,
        source_advanced=avancou,
    )


# ---------------------------------------------------------------------------
# deadline_status — quatro estados e fronteiras
# ---------------------------------------------------------------------------
def test_deadline_quatro_estados():
    assert transform.classify_deadline(AGORA - timedelta(hours=1), AGORA) is DeadlineStatus.OVERDUE
    assert transform.classify_deadline(AGORA + timedelta(hours=5), AGORA) is DeadlineStatus.DUE_WITHIN_24H
    assert transform.classify_deadline(AGORA + timedelta(hours=48), AGORA) is DeadlineStatus.ON_TIME
    assert transform.classify_deadline(None, AGORA) is DeadlineStatus.UNAVAILABLE


def test_deadline_fronteiras_exatas():
    assert transform.classify_deadline(AGORA, AGORA) is DeadlineStatus.DUE_WITHIN_24H
    assert transform.classify_deadline(
        AGORA - timedelta(microseconds=1), AGORA
    ) is DeadlineStatus.OVERDUE
    assert transform.classify_deadline(
        AGORA + timedelta(hours=24), AGORA
    ) is DeadlineStatus.ON_TIME
    assert transform.classify_deadline(
        AGORA + timedelta(hours=24) - timedelta(microseconds=1), AGORA
    ) is DeadlineStatus.DUE_WITHIN_24H


def test_prazo_nulo_nunca_vira_overdue():
    for horas in (0.5, 100.0, 24 * 400):
        f = fila([linha_fonte(ship_by=None, horas_aberto=horas)])
        assert f[0]["deadline_status"] == DeadlineStatus.UNAVAILABLE.value
        assert f[0]["hours_overdue"] is None


def test_hours_overdue_so_existe_em_overdue():
    assert transform.hours_overdue(AGORA - timedelta(hours=3), AGORA) == pytest.approx(3.0)
    assert transform.hours_overdue(AGORA + timedelta(hours=3), AGORA) is None
    assert transform.hours_overdue(None, AGORA) is None


def test_prazo_nativo_tem_prioridade_sobre_idade():
    f = fila([linha_fonte(ship_by=AGORA + timedelta(hours=30), horas_aberto=100)])
    assert f[0]["deadline_status"] == DeadlineStatus.ON_TIME.value
    assert f[0]["operational_age_status"] == OperationalAgeStatus.OVER_48H.value


# ---------------------------------------------------------------------------
# operational_age_status — 48h
# ---------------------------------------------------------------------------
def test_idade_tres_estados_e_fronteira_de_48h():
    assert transform.classify_age(AGORA - timedelta(hours=47), AGORA) is OperationalAgeStatus.WITHIN_48H
    assert transform.classify_age(AGORA - timedelta(hours=48), AGORA) is OperationalAgeStatus.WITHIN_48H
    assert transform.classify_age(
        AGORA - timedelta(hours=48, microseconds=1), AGORA
    ) is OperationalAgeStatus.OVER_48H
    assert transform.classify_age(None, AGORA) is OperationalAgeStatus.UNKNOWN


def test_marco_inicial_usa_pagamento_e_cai_para_criacao():
    pago, criado = AGORA - timedelta(hours=10), AGORA - timedelta(hours=50)
    assert transform.marco_inicial(pago, criado) == pago
    assert transform.marco_inicial(None, criado) == criado
    assert transform.marco_inicial(None, None) is None


def test_idade_desconhecida_quando_nao_ha_marco():
    linha = linha_fonte(pay=False)
    linha["create_time"] = None
    f = fila([linha])
    assert f[0]["operational_age_status"] == OperationalAgeStatus.UNKNOWN.value
    assert f[0]["hours_open"] is None
    assert f[0]["is_source_zombie"] is False


# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
def test_brt_e_utc_produzem_o_mesmo_resultado():
    prazo = AGORA + timedelta(hours=5)
    assert transform.classify_deadline(prazo, AGORA) is transform.classify_deadline(
        prazo.astimezone(BRT), AGORA.astimezone(BRT)
    )


def test_naive_e_recusado():
    naive = datetime(2026, 9, 15, 20, 0, 0)
    with pytest.raises(ValueError, match="sem timezone"):
        transform.classify_deadline(naive, AGORA)
    with pytest.raises(ValueError, match="sem timezone"):
        transform.classify_age(AGORA - timedelta(hours=1), naive)


def test_snapshot_hour_trunca_em_utc():
    instante = datetime(2026, 9, 15, 16, 58, 10, tzinfo=BRT)  # 19:58 UTC
    assert transform.snapshot_hour(instante) == datetime(
        2026, 9, 15, 19, 0, 0, tzinfo=timezone.utc
    )


def test_shopee_e_timestamp_verified():
    assert fila([linha_fonte()])[0]["timestamp_quality"] == TimestampQuality.VERIFIED.value


# ---------------------------------------------------------------------------
# Reclassificacao APENAS pela passagem do tempo (EXP-1A-R, preservado)
# ---------------------------------------------------------------------------
def _mesma_fonte(ship_by=None, horas_aberto=1.0):
    """Linha fixa, com instantes absolutos que NAO mudam entre execucoes."""
    marco = AGORA - timedelta(hours=horas_aberto)
    return {
        "shop_account": "apice",
        "order_sn": "FIXO",
        "shop_id": "1609671923",
        "order_status": "PROCESSED",
        "create_time": marco,
        "pay_time": marco,
        "ship_by_date": ship_by,
        "pickup_done_time": None,
        "shipping_carrier": "Shopee Xpress",
        "ingested_at": AGORA - timedelta(hours=1),
    }


@pytest.mark.parametrize(
    "ship_by_offset_h, horas_aberto, avanco_h, campo, de, para",
    [
        (30, 1, 10, "deadline_status", "on_time", "due_within_24h"),
        (5, 1, 10, "deadline_status", "due_within_24h", "overdue"),
        (None, 47, 2, "operational_age_status", "within_48h", "over_48h"),
    ],
)
def test_reclassifica_so_com_o_relogio(
    ship_by_offset_h, horas_aberto, avanco_h, campo, de, para
):
    ship_by = (
        AGORA + timedelta(hours=ship_by_offset_h)
        if ship_by_offset_h is not None else None
    )
    fonte = [_mesma_fonte(ship_by=ship_by, horas_aberto=horas_aberto)]
    depois = AGORA + timedelta(hours=avanco_h)
    assert fila(fonte, AGORA)[0][campo] == de
    assert fila(fonte, depois)[0][campo] == para


def test_slow_vs_baseline_vira_true_so_com_o_relogio():
    fonte = [_mesma_fonte(horas_aberto=15)]
    bl = {"1609671923": (500, 10.0)}  # limiar = 20h
    assert fila(fonte, AGORA, bl)[0]["is_slow_vs_baseline"] is False
    assert fila(fonte, AGORA + timedelta(hours=10), bl)[0]["is_slow_vs_baseline"] is True


def test_source_zombie_vira_true_so_com_o_relogio():
    fonte = [_mesma_fonte(horas_aberto=29 * 24)]
    assert fila(fonte, AGORA)[0]["is_source_zombie"] is False
    assert fila(fonte, AGORA + timedelta(days=2))[0]["is_source_zombie"] is True


def test_is_stalled_vira_true_so_com_o_relogio():
    fonte = [_mesma_fonte(horas_aberto=29 * 24)]
    assert fila(fonte, AGORA)[0]["is_stalled"] is False
    assert fila(fonte, AGORA + timedelta(days=2))[0]["is_stalled"] is True


def test_segunda_execucao_publica_estados_novos():
    """Fonte identica, watermark identico: a segunda execucao AINDA publica."""
    fonte = [_mesma_fonte(ship_by=AGORA + timedelta(hours=5), horas_aberto=47)]
    depois = AGORA + timedelta(hours=10)
    assert source_advanced(WATERMARKS, WATERMARKS) is False

    r1 = {s["shop_account"]: s for s in resumos(fila(fonte, AGORA), AGORA)}["apice"]
    r2 = {s["shop_account"]: s for s in resumos(fila(fonte, depois), depois)}["apice"]

    assert r1["due_within_24h_count"] == 1 and r1["overdue_count"] == 0
    assert r2["overdue_count"] == 1 and r2["due_within_24h_count"] == 0
    assert r1["over_48h_count"] == 0 and r2["over_48h_count"] == 1


# ---------------------------------------------------------------------------
# Anomalias independentes
# ---------------------------------------------------------------------------
def test_slow_exige_amostra_minima():
    assert transform.is_slow_vs_baseline(100.0, 500, 10.0) is True
    assert transform.is_slow_vs_baseline(100.0, 99, 10.0) is False
    assert transform.is_slow_vs_baseline(100.0, 500, None) is False
    assert transform.is_slow_vs_baseline(None, 500, 10.0) is False
    assert transform.is_slow_vs_baseline(100.0, 500, 0.0) is False


def test_slow_usa_dobro_do_p50():
    assert transform.is_slow_vs_baseline(20.0, 500, 10.0) is False
    assert transform.is_slow_vs_baseline(20.1, 500, 10.0) is True


def test_zombie_em_30_dias():
    assert transform.is_source_zombie(30 * 24.0) is False
    assert transform.is_source_zombie(30 * 24.0 + 0.1) is True


def test_slow_e_zombie_simultaneos():
    f = fila(
        [linha_fonte(horas_aberto=40 * 24, ship_by=AGORA + timedelta(days=5))],
        baselines={"1609671923": (500, 10.0)},
    )[0]
    assert f["is_slow_vs_baseline"] is True
    assert f["is_source_zombie"] is True
    assert f["is_stalled"] is True
    assert f["deadline_status"] == DeadlineStatus.ON_TIME.value
    assert f["operational_age_status"] == OperationalAgeStatus.OVER_48H.value


def test_stalled_nao_e_soma_de_slow_e_zombie():
    """Sobreposicao: 1 pedido slow+zombie, 1 so zombie. stalled = 2, nao 3.

    p50 = 400h -> limiar 800h. A (960h) e slow e zombie; B (744h) e so zombie.
    """
    linhas = [
        linha_fonte(order_sn="A", horas_aberto=40 * 24),  # 960h: slow + zombie
        linha_fonte(order_sn="B", horas_aberto=31 * 24),  # 744h: so zombie
    ]
    f = fila(linhas, baselines={"1609671923": (500, 400.0)})
    s = {x["shop_account"]: x for x in resumos(f)}["apice"]
    assert s["slow_count"] == 1
    assert s["zombie_count"] == 2
    assert s["stalled_count"] == 2
    assert s["stalled_count"] != s["slow_count"] + s["zombie_count"]


def test_dimensoes_nao_sao_mutuamente_exclusivas():
    f = fila(
        [linha_fonte(horas_aberto=40 * 24, ship_by=AGORA - timedelta(hours=2))],
        baselines={"1609671923": (500, 10.0)},
    )[0]
    assert f["deadline_status"] == DeadlineStatus.OVERDUE.value
    assert f["operational_age_status"] == OperationalAgeStatus.OVER_48H.value
    assert f["is_stalled"] is True


def test_pedido_ativo_antigo_nao_e_descartado():
    f = fila([linha_fonte(horas_aberto=365 * 24, ship_by=None)])
    assert len(f) == 1
    assert f[0]["is_source_zombie"] is True


# ---------------------------------------------------------------------------
# Chave da fila — EXP-1A-R2
# ---------------------------------------------------------------------------
def test_mesma_order_sn_em_contas_diferentes_nao_colide():
    """A chave e (channel, shop_account, marketplace_order_id)."""
    linhas = [
        linha_fonte(shop_id="1609671923", order_sn="MESMO"),
        linha_fonte(shop_id="1579330222", order_sn="MESMO"),
    ]
    f = fila(linhas)
    chaves = {
        (r["channel"], r["shop_account"], r["marketplace_order_id"]) for r in f
    }
    assert len(f) == 2
    assert len(chaves) == 2
    assert {r["brand"] for r in f} == {"apice", "barbours"}


def test_correcao_da_marca_nao_cria_outro_pedido():
    """Registry corrige a marca da conta; a identidade do pedido nao muda."""
    linha = linha_fonte(shop_id="1609671923", order_sn="P1")
    antes = fila([linha])[0]

    corrigido = dict(REGISTRY)
    corrigido["1609671923"] = SellerAccount("1609671923", 9, "apice_novo")
    depois = transform.build_fila_shopee([linha], corrigido, {}, AGORA, LOTE)[0]

    def chave(r):
        return (r["channel"], r["shop_account"], r["marketplace_order_id"])

    assert chave(antes) == chave(depois)
    assert antes["brand"] != depois["brand"]


def test_conta_nao_publica_pedido_com_marca_de_outra():
    conn = FakeConn()
    f = fila([linha_fonte(shop_id="1609671923", order_sn="P1")])
    f[0]["brand"] = "barbours"  # marca de outra conta
    with pytest.raises(ValueError, match="registry resolve"):
        publisher.publish_channel(
            conn, Channel.SHOPEE, f, resumos([]),
            source_health=SourceHealth.HEALTHY, refresh_batch_id=LOTE,
            expected_accounts=CONTAS_CANONICAS,
            execute_values=fake_execute_values,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_conta_fora_do_registry_falha():
    with pytest.raises(RegistryError, match="999"):
        linha = linha_fonte(shop_id="1609671923")
        linha["shop_id"] = "999"
        transform.build_fila_shopee([linha], REGISTRY, {}, AGORA, LOTE)


def test_marca_vem_do_registry_e_nao_do_texto():
    linha = linha_fonte(shop_id="1579330222")
    linha["brand"] = "marca_errada_do_texto"
    assert fila([linha])[0]["brand"] == "barbours"


class FakeRegistryCursor:
    def __init__(self, linhas):
        self.linhas = linhas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.linhas


class FakeRegistryConn:
    def __init__(self, linhas):
        self.linhas = linhas

    def cursor(self):
        return FakeRegistryCursor(self.linhas)


def test_registry_ignora_conta_inativa():
    conn = FakeRegistryConn([
        {"external_seller_id": "1", "loja_id": 1, "brand_key": "apice", "ativo": True},
        {"external_seller_id": "2", "loja_id": 2, "brand_key": "barbours", "ativo": False},
    ])
    contas, problemas = shopee_extract.load_registry(conn, 3)
    assert set(contas) == {"1"}
    assert problemas == []


def test_registry_detecta_external_id_duplicado():
    conn = FakeRegistryConn([
        {"external_seller_id": "1", "loja_id": 1, "brand_key": "apice", "ativo": True},
        {"external_seller_id": "1", "loja_id": 2, "brand_key": "barbours", "ativo": True},
    ])
    _, problemas = shopee_extract.load_registry(conn, 3)
    assert any("duplicado" in p for p in problemas)


def test_registry_detecta_loja_com_duas_marcas():
    conn = FakeRegistryConn([
        {"external_seller_id": "1", "loja_id": 7, "brand_key": "apice", "ativo": True},
        {"external_seller_id": "2", "loja_id": 7, "brand_key": "barbours", "ativo": True},
    ])
    _, problemas = shopee_extract.load_registry(conn, 3)
    assert any("mais de uma marca" in p for p in problemas)


# ---------------------------------------------------------------------------
# Saude da fonte — igualdade EXATA de conjuntos
# ---------------------------------------------------------------------------
def _extracao(health, backlog, *, observadas=None):
    obs = CONTAS if observadas is None else observadas
    return ExtractionResult(
        source_health=health,
        expected_accounts=CONTAS,
        observed_accounts=obs,
        account_watermarks={c: AGORA - timedelta(hours=1) for c in obs},
        backlog_rows=backlog,
    )


def test_backlog_zero_com_fonte_saudavel_e_fotografia_vazia():
    r = _extracao(SourceHealth.HEALTHY, [])
    assert r.is_empty_photograph is True
    assert r.backlog_count == 0


def test_conta_esperada_ausente_bloqueia():
    r = _extracao(
        SourceHealth.ACCOUNT_MISSING, [], observadas=CONTAS - {"1609671923"}
    )
    assert r.is_empty_photograph is False
    assert r.missing_accounts == frozenset({"1609671923"})


def test_conta_inesperada_bloqueia():
    """Loja nova na API sem cadastro: ignorar esconderia o backlog dela."""
    r = _extracao(
        SourceHealth.UNEXPECTED_ACCOUNT, [], observadas=CONTAS | {"9999999999"}
    )
    assert r.unexpected_accounts == frozenset({"9999999999"})
    assert r.source_health.can_publish is False


def test_igualdade_exata_nao_aceita_subconjunto():
    """Esperado ser subconjunto do observado NAO basta."""
    r = _extracao(SourceHealth.HEALTHY, [], observadas=CONTAS | {"9999999999"})
    # A classificacao correta vem do extract(); aqui provamos que o excedente
    # e visivel e nao pode ser descartado em silencio.
    assert r.unexpected_accounts == frozenset({"9999999999"})


@pytest.mark.parametrize(
    "health",
    [h for h in SourceHealth if h is not SourceHealth.HEALTHY],
)
def test_fonte_doente_nunca_autoriza_publicacao(health):
    assert _extracao(health, []).source_health.can_publish is False


class FakeSourceConn:
    """Fonte falsa para exercitar `extract()` de ponta a ponta."""

    def __init__(self, watermark_rows, backlog_rows):
        self.watermark_rows = watermark_rows
        self.backlog_rows = backlog_rows
        self.ultima = None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.ultima = sql

    def fetchall(self):
        if "MAX(ingested_at)" in self.ultima:
            return self.watermark_rows
        return self.backlog_rows


def _wm(shop_id, shop_account, carimbo=None):
    return {
        "shop_id": shop_id,
        "shop_account": shop_account,
        "max_ingested_at": carimbo if carimbo is not None else AGORA - timedelta(hours=1),
    }


def test_extract_saudavel():
    conn = FakeSourceConn([_wm(k, v[0]) for k, v in NOMES.items()], [linha_fonte()])
    r = shopee_extract.extract(conn, AGORA, CONTAS)
    assert r.source_health is SourceHealth.HEALTHY
    assert r.backlog_count == 1
    assert r.account_shop_names["1609671923"] == "apice"


def test_extract_conta_ausente():
    faltando = [_wm(k, v[0]) for k, v in NOMES.items() if k != "1457734799"]
    r = shopee_extract.extract(FakeSourceConn(faltando, []), AGORA, CONTAS)
    assert r.source_health is SourceHealth.ACCOUNT_MISSING
    assert "1457734799" in r.detail


def test_extract_conta_inesperada():
    extra = [_wm(k, v[0]) for k, v in NOMES.items()] + [_wm("9999999999", "nova")]
    r = shopee_extract.extract(FakeSourceConn(extra, []), AGORA, CONTAS)
    assert r.source_health is SourceHealth.UNEXPECTED_ACCOUNT
    assert "9999999999" in r.detail
    assert r.backlog_rows == []  # nem chegou a ler o backlog


def test_extract_watermark_ausente():
    sem = [_wm(k, v[0]) for k, v in NOMES.items()]
    sem[0]["max_ingested_at"] = None
    r = shopee_extract.extract(FakeSourceConn(sem, []), AGORA, CONTAS)
    assert r.source_health is SourceHealth.WATERMARK_MISSING


def test_extract_registry_vazio():
    r = shopee_extract.extract(FakeSourceConn([], []), AGORA, frozenset())
    assert r.source_health is SourceHealth.SOURCE_UNAVAILABLE


def test_extract_registry_ambiguo():
    r = shopee_extract.extract(
        FakeSourceConn([], []), AGORA, CONTAS,
        registry_problems=["external_seller_id duplicado: 1"],
    )
    assert r.source_health is SourceHealth.REGISTRY_AMBIGUOUS
    assert "duplicado" in r.detail


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------
def test_freshness_quatro_estados():
    assert transform.classify_freshness(AGORA - timedelta(hours=8), AGORA) is FreshnessStatus.FRESH
    assert transform.classify_freshness(AGORA - timedelta(hours=8, minutes=1), AGORA) is FreshnessStatus.STALE
    assert transform.classify_freshness(AGORA - timedelta(hours=24), AGORA) is FreshnessStatus.STALE
    assert transform.classify_freshness(AGORA - timedelta(hours=24, minutes=1), AGORA) is FreshnessStatus.CRITICAL
    assert transform.classify_freshness(None, AGORA) is FreshnessStatus.UNKNOWN


def test_uma_conta_stale_outra_fresh():
    f = fila([
        linha_fonte(shop_id="1609671923", order_sn="A", ingested_h=1),
        linha_fonte(shop_id="1579330222", order_sn="B", ingested_h=30),
    ])
    por_marca = {r["brand"]: r["source_freshness_status"] for r in f}
    assert por_marca["apice"] == FreshnessStatus.FRESH.value
    assert por_marca["barbours"] == FreshnessStatus.CRITICAL.value


def test_freshness_nao_zera_kpi():
    f = fila([linha_fonte(ingested_h=100, ship_by=AGORA - timedelta(hours=1))])[0]
    assert f["source_freshness_status"] == FreshnessStatus.CRITICAL.value
    assert f["deadline_status"] == DeadlineStatus.OVERDUE.value


def test_source_advanced_e_metadado():
    antes = {"a": AGORA - timedelta(hours=1)}
    assert source_advanced({"a": AGORA}, antes) is True
    assert source_advanced(antes, antes) is False
    assert source_advanced({"a": AGORA}, {}) is True
    assert source_advanced({"a": None}, {}) is False


# ---------------------------------------------------------------------------
# Resumo horario por conta
# ---------------------------------------------------------------------------
def test_uma_linha_por_conta_esperada():
    s = resumos(fila([linha_fonte()]))
    assert len(s) == 4
    assert {x["shop_account"] for x in s} == {"apice", "barbours", "lescent", "rituaria"}


def test_contas_reconciliam_com_o_registry():
    s = resumos(fila([linha_fonte()]))
    assert {x["brand"] for x in s} == {c.brand_key for c in REGISTRY.values()}


def test_quatro_categorias_somam_backlog():
    f = fila([
        linha_fonte(order_sn="a", ship_by=AGORA - timedelta(hours=1)),
        linha_fonte(order_sn="b", ship_by=AGORA + timedelta(hours=5)),
        linha_fonte(order_sn="c", ship_by=AGORA + timedelta(days=3)),
        linha_fonte(order_sn="d", ship_by=None),
    ])
    for s in resumos(f):
        soma = (
            s["overdue_count"] + s["due_within_24h_count"]
            + s["on_time_count"] + s["deadline_unavailable_count"]
        )
        assert soma == s["backlog_count"]
    total = {x["shop_account"]: x for x in resumos(f)}["apice"]
    assert total["backlog_count"] == 4


def test_contagens_nunca_negativas():
    for s in resumos([]):
        for k, v in s.items():
            if k.endswith("_count"):
                assert v >= 0


def test_resumo_carrega_metadados_do_lote():
    s = resumos(fila([linha_fonte()]), avancou=True)[0]
    assert s["refresh_batch_id"] == LOTE
    assert s["channel"] == Channel.SHOPEE.value
    assert s["source_advanced"] is True
    assert s["run_status"] == "success"
    assert s["snapshot_hour"] == transform.snapshot_hour(AGORA)


def test_resumo_do_canal_nao_e_persistido_como_quinta_conta():
    """Total do canal e derivado na API; persistir criaria linha sem loja."""
    s = resumos(fila([linha_fonte()]))
    assert all(x["shop_account"] in {v[0] for v in NOMES.values()} for x in s)
    assert len(s) == len(NOMES)


# ---------------------------------------------------------------------------
# Caso de referencia — medicao real de 15/09/2026
# ---------------------------------------------------------------------------
REFERENCIA = [
    ("apice", "1609671923", 281, 81, 95, 67, 38, 130),
    ("barbours", "1579330222", 514, 17, 35, 430, 32, 167),
    ("lescent", "1593864538", 103, 0, 17, 61, 25, 6),
    ("rituaria", "1457734799", 35, 3, 2, 30, 0, 4),
]


def _fila_de_referencia():
    linhas = []
    for marca, shop_id, total, overdue, due24, on_time, unavail, over48 in REFERENCIA:
        assert overdue + due24 + on_time + unavail == total, marca
        prazos = (
            [AGORA - timedelta(hours=2)] * overdue
            + [AGORA + timedelta(hours=5)] * due24
            + [AGORA + timedelta(days=3)] * on_time
            + [None] * unavail
        )
        for i, prazo in enumerate(prazos):
            # `over_48h` e atribuido de forma INDEPENDENTE do bucket de prazo.
            linhas.append(
                linha_fonte(
                    shop_id=shop_id, order_sn=f"{marca}-{i}",
                    ship_by=prazo, horas_aberto=60.0 if i < over48 else 10.0,
                )
            )
    return fila(linhas)


def test_caso_de_referencia_bate_com_a_medicao():
    f = _fila_de_referencia()
    conta = lambda campo, valor: sum(1 for r in f if r[campo] == valor)  # noqa: E731
    assert len(f) == 933
    assert conta("deadline_status", DeadlineStatus.OVERDUE.value) == 101
    assert conta("deadline_status", DeadlineStatus.DUE_WITHIN_24H.value) == 149
    assert conta("deadline_status", DeadlineStatus.ON_TIME.value) == 588
    assert conta("deadline_status", DeadlineStatus.UNAVAILABLE.value) == 95
    assert conta("operational_age_status", OperationalAgeStatus.OVER_48H.value) == 307


def test_referencia_reconcilia_por_conta():
    s = {x["shop_account"]: x for x in resumos(_fila_de_referencia())}
    for marca, _sid, total, overdue, due24, on_time, unavail, over48 in REFERENCIA:
        linha = s[marca]
        assert linha["backlog_count"] == total
        assert linha["overdue_count"] == overdue
        assert linha["due_within_24h_count"] == due24
        assert linha["on_time_count"] == on_time
        assert linha["deadline_unavailable_count"] == unavail
        assert linha["over_48h_count"] == over48
        assert (
            overdue + due24 + on_time + unavail
        ) == total
    assert sum(x["backlog_count"] for x in s.values()) == 933


def test_referencia_over_48h_e_transversal():
    """206 pedidos estao acima de 48h E dentro do prazo."""
    f = _fila_de_referencia()
    assert sum(
        1 for r in f
        if r["operational_age_status"] == OperationalAgeStatus.OVER_48H.value
        and r["deadline_status"] != DeadlineStatus.OVERDUE.value
    ) == 206


# ---------------------------------------------------------------------------
# Fakes de conexao — mapping, como RealDictCursor
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.falhar_em and self.conn.falhar_em in sql:
            raise RuntimeError("falha simulada")

    def fetchone(self):
        return {"pg_try_advisory_lock": self.conn.lock_disponivel}


class FakeConn:
    def __init__(self, lock_disponivel=True, falhar_em=None, falhar_commit=False):
        self.autocommit = False
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.lock_disponivel = lock_disponivel
        self.falhar_em = falhar_em
        self.falhar_commit = falhar_commit

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        if self.falhar_commit:
            raise RuntimeError("commit sem resposta")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def sqls(self):
        return [s for s, _ in self.executed]


def fake_execute_values(cur, sql, rows):
    cur.execute(sql, rows)


@contextmanager
def _lock(conn, blocking=False):
    with publisher.channel_lock(conn, Channel.SHOPEE, blocking=blocking):
        yield


def _publish(conn, f, effective_at=AGORA, health=SourceHealth.HEALTHY, lote=LOTE):
    return publisher.publish_channel(
        conn, Channel.SHOPEE, f, resumos(f, effective_at, lote=lote),
        source_health=health, refresh_batch_id=lote,
        expected_accounts=CONTAS_CANONICAS,
        execute_values=fake_execute_values,
    )


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------
def test_lock_adquire_e_libera():
    conn = FakeConn()
    with _lock(conn):
        pass
    assert any("pg_try_advisory_lock" in s for s in conn.sqls())
    assert any("pg_advisory_unlock" in s for s in conn.sqls())


def test_lock_libera_mesmo_com_falha_na_leitura():
    conn = FakeConn()
    with pytest.raises(RuntimeError, match="erro de leitura"):
        with _lock(conn):
            raise RuntimeError("erro de leitura")
    assert any("pg_advisory_unlock" in s for s in conn.sqls())


def test_lock_ocupado_falha_rapido():
    conn = FakeConn(lock_disponivel=False)
    with pytest.raises(publisher.LockNotAcquired):
        with _lock(conn):
            pass
    assert not any("pg_advisory_unlock" in s for s in conn.sqls())


def test_lock_nao_deixa_transacao_ociosa():
    conn = FakeConn()
    with _lock(conn):
        assert conn.autocommit is True


# ---------------------------------------------------------------------------
# Fotografia vazia
# ---------------------------------------------------------------------------
def test_fotografia_vazia_limpa_shopee_e_grava_quatro_resumos_zero():
    conn = FakeConn()
    linhas, contas = _publish(conn, [])

    deletes = [(s, p) for s, p in conn.executed if "DELETE" in s.upper()]
    inserts_fila = [s for s in conn.sqls() if "expedicao_fila_atual" in s and "INSERT" in s]
    inserts_resumo = [s for s in conn.sqls() if "expedicao_refresh_run" in s]

    assert len(deletes) == 1
    assert deletes[0][1] == ("shopee",)
    assert inserts_fila == []
    assert len(inserts_resumo) == 1
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert linhas == 0
    assert contas == 4  # accounts_recorded == contas esperadas


def test_fotografia_vazia_zera_todas_as_contagens():
    for s in resumos([]):
        assert s["backlog_count"] == 0
        assert s["overdue_count"] == 0
        assert s["over_48h_count"] == 0
        assert s["stalled_count"] == 0


def test_fotografia_vazia_nao_e_rotulada_no_op():
    conn = FakeConn()
    audit_mod.record_observation(
        conn, Channel.SHOPEE, 3, AGORA,
        source_health=SourceHealth.HEALTHY, backlog_count=0,
        expected_accounts=CONTAS, observed_accounts=CONTAS,
        watermarks={}, source_advanced=False, published=True, accounts_recorded=4,
    )
    detalhe = conn.executed[-1][1][-1]
    assert '"empty_photograph": true' in detalhe
    assert '"accounts_recorded": 4' in detalhe
    assert "no_op" not in detalhe.lower()


@pytest.mark.parametrize(
    "health", [h for h in SourceHealth if h is not SourceHealth.HEALTHY]
)
def test_fonte_doente_nao_apaga_a_fila(health):
    conn = FakeConn()
    with pytest.raises(SourceUnhealthy, match="preservada"):
        _publish(conn, [], health=health)
    assert not any("DELETE" in s.upper() for s in conn.sqls())
    assert conn.commits == 0


# ---------------------------------------------------------------------------
# Publicacao e atomicidade
# ---------------------------------------------------------------------------
def test_publicacao_substitui_apenas_o_canal():
    conn = FakeConn()
    _publish(conn, fila([linha_fonte()]))
    deletes = [(s, p) for s, p in conn.executed if "DELETE" in s.upper()]
    assert len(deletes) == 1
    assert "WHERE channel = %s" in deletes[0][0]
    assert deletes[0][1] == ("shopee",)


def test_shopee_nao_apaga_outros_canais():
    conn = FakeConn()
    _publish(conn, [])
    for sql, params in conn.executed:
        if "DELETE" in sql.upper():
            assert params == ("shopee",)
            assert "mercadolivre" not in sql
            assert "tiktokshop" not in sql


def test_publicacao_recusa_linha_de_outro_canal():
    conn = FakeConn()
    f = fila([linha_fonte()])
    f[0]["channel"] = Channel.MERCADOLIVRE.value
    with pytest.raises(ValueError, match="outro canal"):
        _publish(conn, f)


def test_publicacao_exige_resumo():
    """Fila sem resumo deixaria a tendencia sem a hora correspondente."""
    conn = FakeConn()
    with pytest.raises(ValueError, match="sem resumo"):
        publisher.publish_channel(
            conn, Channel.SHOPEE, fila([linha_fonte()]), [],
            source_health=SourceHealth.HEALTHY, refresh_batch_id=LOTE,
            expected_accounts=CONTAS_CANONICAS,
            execute_values=fake_execute_values,
        )
    assert conn.commits == 0


def test_lote_inconsistente_bloqueia():
    """Fila de um lote com resumo de outro indica montagem incoerente."""
    conn = FakeConn()
    f = fila([linha_fonte()], lote="batch-A")
    with pytest.raises(ValueError, match="refresh_batch_id inconsistente"):
        publisher.publish_channel(
            conn, Channel.SHOPEE, f, resumos(f, lote="batch-B"),
            source_health=SourceHealth.HEALTHY, refresh_batch_id="batch-A",
            expected_accounts=CONTAS_CANONICAS,
            execute_values=fake_execute_values,
        )


def test_fila_e_resumo_na_mesma_transacao():
    """Um unico commit cobre os dois; nao ha commit intermediario."""
    conn = FakeConn()
    _publish(conn, fila([linha_fonte()]))
    ordem = [s for s in conn.sqls() if "DELETE" in s.upper() or "INSERT" in s.upper()]
    assert len(ordem) == 3  # delete + insert fila + insert resumo
    assert conn.commits == 1


def test_falha_no_resumo_reverte_a_fila():
    """Nao existe fila nova com resumo antigo."""
    conn = FakeConn(falhar_em="expedicao_refresh_run")
    with pytest.raises(RuntimeError, match="falha simulada"):
        _publish(conn, fila([linha_fonte()]))
    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_falha_na_fila_reverte_o_resumo():
    """Nao existe resumo novo com fila antiga."""
    conn = FakeConn(falhar_em="INSERT INTO marts.expedicao_fila_atual")
    with pytest.raises(RuntimeError, match="falha simulada"):
        _publish(conn, fila([linha_fonte()]))
    assert conn.rollbacks == 1
    assert conn.commits == 0
    assert not any("expedicao_refresh_run" in s for s in conn.sqls())


def test_commit_indeterminado_nao_permite_retry_cego():
    conn = FakeConn(falhar_commit=True)
    with pytest.raises(publisher.IndeterminateCommit, match="reconcilie"):
        _publish(conn, fila([linha_fonte()]))
    assert conn.rollbacks == 0  # rollback cego apagaria evidencia


# ---------------------------------------------------------------------------
# Resumo horario — last-newest-wins
# ---------------------------------------------------------------------------
def _upsert(estado: dict, linhas: list[dict]) -> dict:
    """Aplica em memoria a semantica do `ON CONFLICT ... WHERE EXCLUDED >`."""
    for linha in linhas:
        k = (linha["channel"], linha["shop_account"], linha["snapshot_hour"])
        atual = estado.get(k)
        if atual is None or linha["observed_at"] > atual["observed_at"]:
            estado[k] = linha
    return estado


def test_resumo_observacao_mais_nova_vence():
    depois = AGORA + timedelta(minutes=20)  # mesma hora UTC
    e = _upsert({}, resumos(fila([linha_fonte()]), AGORA))
    e = _upsert(e, resumos(fila([linha_fonte(), linha_fonte(order_sn="B")], depois), depois))
    assert e[("shopee", "apice", transform.snapshot_hour(AGORA))]["backlog_count"] == 2


def test_resumo_antigo_nao_sobrescreve():
    antes = AGORA - timedelta(minutes=20)
    e = _upsert({}, resumos(fila([linha_fonte(), linha_fonte(order_sn="B")]), AGORA))
    e = _upsert(e, resumos(fila([linha_fonte()], antes), antes))
    assert e[("shopee", "apice", transform.snapshot_hour(AGORA))]["backlog_count"] == 2


def test_resumo_ordem_de_reexecucao_e_irrelevante():
    antes = AGORA - timedelta(minutes=20)
    velho = resumos(fila([linha_fonte()], antes), antes)
    novo = resumos(fila([linha_fonte(), linha_fonte(order_sn="B")]), AGORA)
    a = _upsert(_upsert({}, velho), novo)
    b = _upsert(_upsert({}, novo), velho)
    chave = ("shopee", "apice", transform.snapshot_hour(AGORA))
    assert a[chave]["backlog_count"] == b[chave]["backlog_count"] == 2


def test_rerun_idempotente_nao_duplica():
    r = resumos(fila([linha_fonte()]))
    e = _upsert(_upsert({}, r), r)
    assert len(e) == 4  # quatro contas, sem duplicata


def test_resumo_hora_seguinte_cria_linha_nova():
    proxima = AGORA + timedelta(hours=1)
    e = _upsert({}, resumos(fila([linha_fonte()]), AGORA))
    e = _upsert(e, resumos(fila([linha_fonte(base=proxima)], proxima), proxima))
    assert len(e) == 8  # 4 contas x 2 horas


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------
def test_audit_finish_recusa_metadado_em_error_message():
    with pytest.raises(ValueError, match="exclusiva de erro"):
        audit_mod.audit_finish(FakeConn(), 1, "success", 0, 0, error="fotografia vazia")


def test_audit_finish_aceita_erro_sanitizado_em_falha():
    conn = FakeConn()
    audit_mod.audit_finish(conn, 1, "failed", 0, 0, error="timeout")
    assert conn.commits == 1


def test_observacao_de_conta_inesperada_e_fail():
    conn = FakeConn()
    audit_mod.record_observation(
        conn, Channel.SHOPEE, 3, AGORA,
        source_health=SourceHealth.UNEXPECTED_ACCOUNT, backlog_count=0,
        expected_accounts=CONTAS, observed_accounts=CONTAS | {"9999999999"},
        watermarks={}, source_advanced=False, published=False,
    )
    params = conn.executed[-1][1]
    assert params[3] == "fail" and params[4] == "critical"
    assert '"unexpected_accounts": ["9999999999"]' in params[-1]
    assert '"published": false' in params[-1]


def test_observacao_registra_source_advanced_como_metadado():
    conn = FakeConn()
    audit_mod.record_observation(
        conn, Channel.SHOPEE, 3, AGORA,
        source_health=SourceHealth.HEALTHY, backlog_count=933,
        expected_accounts=CONTAS, observed_accounts=CONTAS,
        watermarks={}, source_advanced=False, published=True, accounts_recorded=4,
    )
    detalhe = conn.executed[-1][1][-1]
    assert '"source_advanced": false' in detalhe
    assert '"published": true' in detalhe


def test_freshness_gera_uma_linha_por_marca():
    """Uma linha por marca, e o veredito vem do watermark da FONTE (EXP-1F).

    `open_orders` dimensiona o impacto; nao e criterio. Marca com backlog grande
    e fonte fresca sai `pass` — era o oposto disso que reprovava as quatro
    marcas no primeiro piloto.
    """
    conn = FakeConn()
    audit_mod.record_freshness(
        conn, 3,
        {
            "apice": {
                "freshness": FreshnessStatus.FRESH.value,
                "source_watermark": AGORA,
                "source_age_hours": 0.55,
                "accounts": 1,
                "open_orders": 281,
                "oldest_row_age_hours": 240.0,
            },
            "barbours": {
                "freshness": FreshnessStatus.CRITICAL.value,
                "source_watermark": AGORA - timedelta(hours=72),
                "source_age_hours": 72.0,
                "accounts": 1,
                "open_orders": 514,
                "oldest_row_age_hours": 168.0,
            },
        },
    )
    inserts = [p for s, p in conn.executed if "data_quality_check" in s]
    assert len(inserts) == 2
    por_status = {p[3] for p in inserts}
    assert por_status == {"pass", "fail"}

    apice = next(p for p in inserts if '"brand": "apice"' in p[6])
    assert apice[3] == "pass"
    assert apice[5] == 0, "fonte fresca nao pode contar linhas como falha"
    assert '"oldest_row_age_hours": 240.0' in apice[6], (
        "a idade da linha tem de continuar visivel, so nao decide"
    )
    assert '"measures": "source_watermark_only"' in apice[6]

    barbours = next(p for p in inserts if '"brand": "barbours"' in p[6])
    assert barbours[3] == "fail"
    assert barbours[5] == 514, "fonte parada dimensiona o impacto pelo backlog"


def test_falha_de_auditoria_apos_commit_nao_marca_failed():
    conn = FakeConn(falhar_em="UPDATE audit.source_sync_run")
    with pytest.raises(audit_mod.AuditAfterCommitError, match="COMMITADA"):
        audit_mod.finish_after_commit(conn, 42, 933, 933)


def test_sanitizacao_remove_credencial():
    msg = audit_mod.sanitize_error_message(
        RuntimeError("could not connect to postgresql://user:senha@host:5432/db")
    )
    assert "senha" not in msg
    assert "<redacted>" in msg


def test_source_name_identifica_canal():
    assert audit_mod.source_name_for(Channel.SHOPEE) == "expedicao_shopee"
    assert audit_mod.source_name_for(Channel.MERCADOLIVRE) == "expedicao_mercadolivre"


# ===========================================================================
# EXP-1F-R/V — compatibilidade do payload do alerta
# ===========================================================================
def test_payload_anterior_continua_legivel():
    """Os quatro campos do payload antigo seguem presentes: leitor antigo nao quebra."""
    conn = FakeConn()
    audit_mod.record_freshness(
        conn, 3,
        {"apice": {
            "freshness": "fresh", "source_watermark": AGORA, "source_age_hours": 0.5,
            "accounts": 1, "open_orders": 7, "oldest_row_age_hours": 240.0,
        }},
    )
    import json as _json
    detalhe = _json.loads(
        [p for s, p in conn.executed if "data_quality_check" in s][0][6]
    )
    for campo in ("brand", "freshness", "open_orders", "source_watermark"):
        assert campo in detalhe, f"campo do payload antigo sumiu: {campo}"
    for campo in ("source_age_hours", "accounts", "oldest_row_age_hours", "measures"):
        assert campo in detalhe, f"campo novo ausente: {campo}"
