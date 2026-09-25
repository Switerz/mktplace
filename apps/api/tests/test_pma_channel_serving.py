"""Gate PMA-2C4A — serving multicanal de Shopee e TikTok.

O que estes testes travam, em uma frase: o Mercado Livre continua lendo as
tabelas legadas sob teto D-1, os canais novos leem SOMENTE
`marts.fact_channel_offer_observation` sob teto do dia corrente, e nenhum dos
dois cai para o outro quando nao tem resposta.

A `SessaoFake` responde por TRECHO DA CONSULTA e registra tudo o que recebeu.
Nao e' um dublê permissivo: os testes verificam QUAIS tabelas foram tocadas, e
e' assim que "sem fallback" vira prova em vez de intencao.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services import monitoramento_preco_service as mp
from app.services import pma_domain as dom
from app.services import pma_match as pm

HOJE = date(2026, 9, 16)
D0 = date(2026, 9, 16)
D1 = date(2026, 9, 15)
CARGA = datetime(2026, 9, 16, 12, 4, 12, tzinfo=timezone.utc)


class _Result:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return iter(self._linhas)


def oferta(**over) -> dict:
    """Uma linha da fato, com TODAS as colunas que o serving le."""
    base = {
        "observed_date": D0, "marketplace": "shopee", "offer_key": "ITEM-1",
        "parent_item_id": "ITEM-1", "model_id": None, "brand": "barbours",
        "shop_account": "barbours", "seller_sku": "SKU-1", "gtin": None,
        "listing_title": "Shampoo 300ml", "observation_mode": "snapshot_current",
        "observed_at": CARGA, "snapshot_status": dom.SNAPSHOT_CURRENT,
        "account_watermark_at": CARGA, "is_active": True,
        "product_type": dom.PRODUCT_NO_KIT_SIGNAL,
        "product_type_source": dom.SOURCE_CHANNEL_FLAG,
        "observed_price": Decimal("50.00"),
        "observed_price_source": "current_price", "list_price": None,
        "promo_context": "available", "promo_id": None,
        "promo_discount_pct": None, "business_scope": dom.BUSINESS_SCOPE_IN,
        "batch_id": None, "source_run_id": None, "synced_at": CARGA,
    }
    base.update(over)
    return base


def referencia(**over) -> dict:
    base = {
        "brand": "barbours", "reference_row_id": "R1", "source_sku": "SKU-1",
        "source_gtin": None, "product_name": "Shampoo 300ml",
        "wholesale_amount": Decimal("30.00"),
        "suggested_retail_amount": Decimal("60.00"),
        "reference_type": pm.REFERENCE_TYPE, "validity_status": pm.VALIDITY_STATUS,
        "quality_status": "ok", "captured_at": "2026-09-02T12:00:00+00:00",
    }
    base.update(over)
    return base


class SessaoFake:
    """Responde por trecho da consulta e REGISTRA as tabelas tocadas."""

    def __init__(self, ofertas=None, referencias=None, listings_ml=None,
                 datas=None, kit_references=None):
        self.ofertas = list(ofertas or [])
        self.referencias = list(referencias or [])
        self.listings_ml = list(listings_ml or [])
        # Gate KITS-PMA-3 — referencias de kit DERIVADAS, publicadas em
        # `marts.fact_kit_reference_daily`. Vazio por padrao: o comportamento
        # de TODOS os testes anteriores e' o de um banco sem nenhuma linha la'.
        self.kit_references = list(kit_references or [])
        self.datas = list(datas) if datas is not None else (
            sorted({o["observed_date"] for o in self.ofertas}, reverse=True))
        self.executadas: list[tuple[str, dict]] = []

    # -- o que interessa aos testes -----------------------------------------
    @property
    def tabelas(self) -> set:
        alvo = set()
        for texto, _ in self.executadas:
            for t in ("fact_channel_offer_observation",
                      "fact_marketplace_listing_price_daily",
                      "fact_suggested_price_reference_snapshot",
                      "fact_kit_reference_daily"):
                if t in texto:
                    alvo.add(t)
        return alvo

    def execute(self, sql, params=None):
        texto = " ".join(str(sql).lower().split())
        p = params or {}
        self.executadas.append((texto, p))

        if "fact_kit_reference_daily" in texto:
            # Aplica os MESMOS filtros da consulta real, inclusive o do
            # snapshot: e' ele que impede servir referencia calculada sobre uma
            # planilha B2B antiga, e um fake permissivo esconderia isso.
            return _Result([
                r for r in self.kit_references
                if r.get("marketplace", p.get("marketplace")) == p.get("marketplace")
                and r.get("observed_date", p.get("observed_date")) == p.get("observed_date")
                and r.get("reference_snapshot_id") == p.get("snapshot_id")
            ])

        if "fact_channel_offer_observation" in texto:
            if "max(observed_date)" in texto:
                teto = p.get("ceiling")
                elegiveis = [d for d in self.datas if teto is None or d <= teto]
                return _Result([{"observed_date":
                                 max(elegiveis) if elegiveis else None}])
            if "select distinct observed_date" in texto:
                teto = p.get("ceiling")
                return _Result([{"observed_date": d} for d in self.datas
                                if teto is None or d <= teto])
            if "select 1 as existe" in texto:
                return _Result([{"existe": 1}]
                               if p.get("observed_date") in self.datas else [])
            if "group by shop_account" in texto:
                contas: dict = {}
                for o in self.ofertas:
                    if o["observed_date"] != p.get("observed_date"):
                        continue
                    c = contas.setdefault(o["shop_account"], {
                        "shop_account": o["shop_account"],
                        "account_watermark_at": o["account_watermark_at"],
                        "observed_at": o["observed_at"],
                        "refreshed_at": o["synced_at"],
                        "offers": 0, "current_offers": 0, "stale_offers": 0})
                    c["offers"] += 1
                    if o["snapshot_status"] == dom.SNAPSHOT_CURRENT:
                        c["current_offers"] += 1
                    elif o["snapshot_status"] == dom.SNAPSHOT_STALE:
                        c["stale_offers"] += 1
                return _Result(sorted(contas.values(),
                                      key=lambda x: x["shop_account"]))
            if "group by brand" in texto:
                # Gate PMA-2C4D3-H2 — cobertura observada. O fake aplica SO'
                # marketplace + observed_date, como a consulta real: se algum
                # filtro de usuario vazasse para ca', o teste que prova a
                # independencia da cobertura falharia, e e' esse o ponto.
                marcas = sorted({
                    o["brand"] for o in self.ofertas
                    if o["marketplace"] == p.get("marketplace")
                    and o["observed_date"] == p.get("observed_date")})
                return _Result([{"brand": b} for b in marcas])
            # a consulta de ofertas, com os filtros opcionais
            linhas = [o for o in self.ofertas
                      if o["marketplace"] == p.get("marketplace")
                      and o["observed_date"] == p.get("observed_date")]
            if p.get("brand_filter"):
                linhas = [o for o in linhas if o["brand"] in p["brands"]]
            if p.get("account_filter"):
                linhas = [o for o in linhas if o["shop_account"] in p["accounts"]]
            if p.get("product_type_filter"):
                linhas = [o for o in linhas
                          if o["product_type"] in p["product_types"]]
            if p.get("has_query"):
                alvo = p["query_like"].strip("%").lower()
                linhas = [o for o in linhas if alvo in " ".join(
                    str(o.get(c) or "") for c in
                    ("listing_title", "seller_sku", "gtin", "offer_key")).lower()]
            return _Result(sorted(linhas, key=lambda o: (o["brand"],
                                                         o["offer_key"])))

        if "group by snapshot_id" in texto:
            return _Result([{"snapshot_id": "snap-1",
                             "captured_at": "2026-09-02T12:00:00+00:00"}])
        if "fact_suggested_price_reference_snapshot" in texto:
            return _Result(list(self.referencias))
        if "select distinct ref_date" in texto:
            return _Result([{"ref_date": D1}])
        if "select 1 as existe" in texto:
            return _Result([{"existe": 1}] if p.get("ref_date") == D1 else [])
        if "max(ref_date)" in texto:
            return _Result([{"ref_date": D1}])
        if "max(synced_at)" in texto:
            return _Result([{"synced_at": CARGA}])
        if "fact_marketplace_listing_price_daily" in texto:
            if "group by brand" in texto:
                # Cobertura observada do ML, tambem sem filtro de usuario.
                return _Result([{"brand": b} for b in sorted(
                    {l["brand"] for l in self.listings_ml
                     if l.get("ref_date") == p.get("ref_date")}
                    or {l["brand"] for l in self.listings_ml})])
            return _Result(list(self.listings_ml))
        raise AssertionError(f"consulta inesperada: {texto[:120]}")


@pytest.fixture(autouse=True)
def flags_ligadas(monkeypatch):
    """As flags nascem DESLIGADAS; cada teste que precisa delas as liga aqui,
    no processo, jamais na configuracao implantada."""
    monkeypatch.setattr(mp.settings, "pma_shopee_enabled", True)
    monkeypatch.setattr(mp.settings, "pma_tiktok_enabled", True)


def _listing_ml(brand, item_id="ML-1", ref_date=None):
    """Linha minima da fato do ML, no formato que `compare_all` consome."""
    return {"ref_date": ref_date or D1, "marketplace": "ml", "brand": brand,
            "item_id": item_id, "seller_sku": item_id, "gtin": None,
            "title": "Produto", "advertised_price": Decimal("10"),
            "listing_status": "active"}


def servir(sessao, canal="shopee", **kw) -> dict:
    return mp.get_monitoramento_preco(sessao, marketplace=canal, today=HOJE, **kw)


# ---------------------------------------------------------------------------
# 1 a 3 — cada canal na SUA tabela, sem fallback
# ---------------------------------------------------------------------------
def test_ml_continua_nas_tabelas_legadas_e_no_teto_d1():
    s = SessaoFake(listings_ml=[])
    saida = mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE)
    assert "fact_channel_offer_observation" not in s.tabelas
    assert "fact_marketplace_listing_price_daily" in s.tabelas
    assert saida["meta"]["date_policy"] == pm.POLICY_CLOSED_DAY
    assert saida["meta"]["eligible_ref_date"] == D1.isoformat()


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_canal_le_somente_a_fato_multicanal(canal):
    s = SessaoFake(ofertas=[oferta(marketplace=canal,
                                   observation_mode="snapshot_current")])
    servir(s, canal)
    assert "fact_channel_offer_observation" in s.tabelas
    assert "fact_marketplace_listing_price_daily" not in s.tabelas


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_canal_sem_dado_nao_cai_para_o_ml(canal):
    """Vazio HONESTO: nenhuma consulta a tabela do ML, nenhuma linha de la'."""
    s = SessaoFake(ofertas=[], datas=[])
    saida = servir(s, canal)
    assert "fact_marketplace_listing_price_daily" not in s.tabelas
    assert saida["rows"] == [] and saida["total_count"] == 0
    assert saida["meta"]["marketplace"] == canal
    assert saida["meta"]["availability"] == "unavailable"


# ---------------------------------------------------------------------------
# 4 a 7 — politica de data
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_canal_aceita_d0(canal):
    s = SessaoFake(ofertas=[oferta(marketplace=canal)])
    saida = servir(s, canal, observed_date=D0.isoformat())
    assert saida["meta"]["observed_date"] == D0.isoformat()
    assert saida["meta"]["snapshot_mutability"] == mp.SNAPSHOT_MUTABLE
    assert saida["total_count"] == 1


def test_ml_continua_recusando_d0():
    with pytest.raises(mp.MonitoramentoPrecoError):
        mp.normalize_observed_date(D0.isoformat(), HOJE, pm.POLICY_CLOSED_DAY)


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_data_futura_recusada_tambem_no_canal(canal):
    with pytest.raises(mp.MonitoramentoPrecoError):
        mp.normalize_observed_date("2026-09-17", HOJE,
                                   pm.POLICY_SNAPSHOT_CURRENT)


def test_sem_d0_usa_o_maior_disponivel_e_marca_stale():
    """Nao ha fotografia de hoje: serve a de ontem e DIZ que esta atrasada."""
    s = SessaoFake(ofertas=[oferta(observed_date=D1,
                                   snapshot_status=dom.SNAPSHOT_CURRENT)],
                   datas=[D1])
    saida = servir(s)
    assert saida["meta"]["observed_date"] == D1.isoformat()
    assert saida["meta"]["freshness_status"] == pm.FRESHNESS_STALE
    assert saida["meta"]["lag_days"] == 1
    assert saida["meta"]["snapshot_mutability"] == mp.SNAPSHOT_SETTLED


def test_d0_sem_nenhuma_oferta_corrente_nao_e_fresh():
    """Frescor sai do WATERMARK materializado, nao da igualdade de data."""
    s = SessaoFake(ofertas=[oferta(snapshot_status=dom.SNAPSHOT_STALE)])
    saida = servir(s)
    assert saida["meta"]["observed_date"] == D0.isoformat()
    assert saida["meta"]["freshness_status"] == pm.FRESHNESS_STALE
    assert saida["rows"][0]["freshness_status"] == pm.FRESHNESS_STALE


def test_linha_stale_em_d0_nao_reporta_atraso_negativo():
    """Gate PMA-2C4A-R — o finding que a revisao pegou.

    A fotografia e' de HOJE e a oferta carrega `snapshot_status = stale`: nao ha
    atraso de pipeline, foi a OFERTA que nao foi revista nesta carga. O texto
    antigo dizia "-1 dia(s) atras de D-1", que erra tres vezes de uma so' vez —
    numero negativo, teto de outro canal e um diagnostico mandando conferir um
    sync que esta em dia. Na Shopee sao 454 linhas nessa situacao.
    """
    s = SessaoFake(ofertas=[oferta(snapshot_status=dom.SNAPSHOT_STALE,
                                   observed_at=datetime(2026, 8, 28, 16, 40,
                                                        tzinfo=timezone.utc))])
    linha = servir(s)["rows"][0]
    assert linha["freshness_status"] == pm.FRESHNESS_STALE
    texto = " ".join(linha["limitations"])
    assert "-1 dia" not in texto
    assert "dia(s) atras" not in texto
    assert "D-1" not in texto
    assert "NAO foi revista nesta carga" in texto
    assert "nao indica atraso do sync" in texto
    assert "2026-08-28" in texto          # cita a ultima observacao real


def test_fotografia_atrasada_do_canal_reporta_atraso_positivo():
    """Quando a fotografia REALMENTE esta atrasada, o atraso e' positivo e
    aponta o publisher — nao o sync do Mercado Livre."""
    s = SessaoFake(ofertas=[oferta(observed_date=date(2026, 9, 14),
                                   snapshot_status=dom.SNAPSHOT_CURRENT)],
                   datas=[date(2026, 9, 14)])
    saida = servir(s)
    assert saida["meta"]["lag_days"] == 2
    # Gate PMA-2C4A-R, segundo finding: a oferta foi carimbada `current` NAQUELA
    # carga, mas a carga e' de dois dias atras. Se a linha se declarasse `fresh`
    # aqui, o mesmo payload traria dois vereditos sobre o mesmo dado.
    assert saida["meta"]["freshness_status"] == pm.FRESHNESS_STALE
    assert saida["rows"][0]["freshness_status"] == pm.FRESHNESS_STALE
    texto = " ".join(saida["rows"][0]["limitations"])
    assert "2 dia(s) atras do dia operacional" in texto
    assert "publisher" in texto
    assert "D-1" not in texto


def test_o_texto_de_atraso_do_ml_permanece_palavra_por_palavra():
    """O ML e' contrato publicado: a frase nao pode ser reescrita de carona."""
    from app.services.pma_match import ReferenceIndex, compare_listing

    linha = compare_listing(
        {"ref_date": date(2026, 9, 13), "advertised_price": Decimal("10"),
         "brand": "kokeshi", "marketplace": "ml", "item_id": "X",
         "listing_status": "active"},
        ReferenceIndex.build([]), HOJE, pm.FRESHNESS_STALE)
    assert any(
        l == ("observacao de 2026-09-13, 2 dia(s) atras de 2026-09-15 "
              "(D-1 do dia operacional 2026-09-16): a comparacao vale para "
              "aquele dia e NAO descreve o preco de hoje. Verifique a ultima "
              "execucao do sync.")
        for l in linha["limitations"]), linha["limitations"]


def test_consulta_retrospectiva_marca_a_linha_como_historical():
    """Dia ESCOLHIDO nao e' atraso: a linha inteira vira `historical`, mesmo
    que o `snapshot_status` gravado seja `current`."""
    s = SessaoFake(ofertas=[oferta(observed_date=date(2026, 9, 14),
                                   snapshot_status=dom.SNAPSHOT_CURRENT)],
                   datas=[date(2026, 9, 14), D0])
    saida = servir(s, observed_date="2026-09-14")
    assert saida["meta"]["freshness_status"] == pm.FRESHNESS_HISTORICAL
    assert saida["rows"][0]["freshness_status"] == pm.FRESHNESS_HISTORICAL


def test_data_valida_sem_observacao_devolve_vazio_tipado():
    s = SessaoFake(ofertas=[oferta()], datas=[D0])
    saida = servir(s, observed_date="2026-09-10")
    assert saida["total_count"] == 0
    assert saida["meta"]["availability"] == "unavailable"
    assert saida["meta"]["unavailable_reason"] == dom.UNAVAILABLE_NO_OBSERVATION
    assert saida["meta"]["requested_observed_date"] == "2026-09-10"
    assert saida["meta"]["observed_ref_date"] is None


# ---------------------------------------------------------------------------
# 9 a 11 — filtros, paginacao e KPIs
# ---------------------------------------------------------------------------
def _muitas(n=7):
    return [oferta(offer_key=f"IT-{i:02d}", seller_sku=f"SKU-{i:02d}",
                   brand="barbours" if i % 2 else "kokeshi",
                   shop_account="barbours" if i % 2 else "kokeshi")
            for i in range(n)]


def test_filtros_por_marca_conta_tipo_e_busca():
    s = SessaoFake(ofertas=_muitas())
    assert servir(s, brand="kokeshi")["total_count"] == 4
    assert servir(s, shop_account="barbours")["total_count"] == 3
    assert servir(s, product_type="no_kit_signal")["total_count"] == 7
    assert servir(s, product_query="SKU-03")["total_count"] == 1


def test_filtro_de_marca_recusa_marca_fora_da_allowlist_do_canal():
    s = SessaoFake(ofertas=_muitas())
    with pytest.raises(mp.MonitoramentoPrecoError):
        servir(s, brand="marca-que-nao-existe")


def test_ml_recusa_filtros_que_nao_se_aplicam_a_ele():
    s = SessaoFake(listings_ml=[])
    for kw in ({"shop_account": "apice"}, {"product_type": "kit_confirmed"}):
        with pytest.raises(mp.MonitoramentoPrecoError):
            mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE, **kw)


def test_paginacao_nao_repete_nem_perde():
    s = SessaoFake(ofertas=_muitas(7))
    vistos = []
    for off in (0, 3, 6):
        pg = servir(s, limit=3, offset=off)
        vistos += [r["offer_key"] for r in pg["rows"]]
        assert pg["total_count"] == 7
    assert len(vistos) == 7 and len(set(vistos)) == 7


def test_kpis_nao_dependem_do_tamanho_da_pagina():
    s = SessaoFake(ofertas=_muitas(7))
    a, b = servir(s, limit=1), servir(s, limit=500)
    assert a["metrics"] == b["metrics"] and a["kpis"] == b["kpis"]
    assert a["returned_count"] == 1 and b["returned_count"] == 7


def test_filtro_de_situacao_altera_a_tabela_e_nao_o_denominador():
    s = SessaoFake(ofertas=[oferta(offer_key="A"),
                            oferta(offer_key="B", is_active=False)])
    cheio, filtrado = servir(s), servir(s, status="inactive_listing")
    assert cheio["total_count"] == 2 and filtrado["total_count"] == 1
    assert filtrado["metrics"] == cheio["metrics"]
    assert filtrado["kpis"] == cheio["kpis"]


# ---------------------------------------------------------------------------
# 12 a 16 — tipo de produto, kits, precos e referencia
# ---------------------------------------------------------------------------
def test_particao_de_product_type_e_exaustiva_sobre_as_ativas():
    s = SessaoFake(ofertas=[
        oferta(offer_key="A", product_type=dom.PRODUCT_KIT_CONFIRMED),
        oferta(offer_key="B", product_type=dom.PRODUCT_KIT_SUSPECTED),
        oferta(offer_key="C", product_type=dom.PRODUCT_NO_KIT_SIGNAL),
        oferta(offer_key="D", product_type=dom.PRODUCT_TYPE_UNKNOWN),
        oferta(offer_key="E", product_type=dom.PRODUCT_NO_KIT_SIGNAL,
               is_active=False),
    ])
    m = servir(s)["metrics"]
    contagem = servir(s)["meta"]["product_type_counts"]
    assert sum(contagem.values()) == m["active_offers"] == 4
    assert m["monitored_offers"] == 5


def test_o_tipo_de_produto_vem_materializado_e_nao_e_reclassificado():
    """Titulo grita KIT, mas o publisher gravou `no_kit_signal`: vale o gravado."""
    s = SessaoFake(ofertas=[oferta(listing_title="KIT completo 3 pecas",
                                   seller_sku="KIT-999",
                                   product_type=dom.PRODUCT_NO_KIT_SIGNAL)])
    saida = servir(s)
    assert saida["rows"][0]["product_type"] == dom.PRODUCT_NO_KIT_SIGNAL
    assert saida["meta"]["product_type_counts"][dom.PRODUCT_KIT_SUSPECTED] == 0


def test_kits_saem_do_denominador_mas_nao_do_monitoramento():
    s = SessaoFake(ofertas=[
        oferta(offer_key="A", product_type=dom.PRODUCT_KIT_CONFIRMED),
        oferta(offer_key="B", product_type=dom.PRODUCT_NO_KIT_SIGNAL),
    ], referencias=[referencia()])
    m = servir(s)["metrics"]
    assert m["monitored_offers"] == 2 and m["active_offers"] == 2
    assert m["eligible_offers"] == 1
    assert m["kit_confirmed"] == 1


def test_marca_fora_do_escopo_conta_como_monitorada_e_sai_do_denominador():
    s = SessaoFake(ofertas=[
        oferta(offer_key="A", marketplace="tiktok", brand="barbours",
               shop_account="tiktok"),
        oferta(offer_key="B", marketplace="tiktok", brand="gocase",
               shop_account="tiktok",
               business_scope=dom.BUSINESS_SCOPE_OUT),
    ])
    saida = servir(s, "tiktok")
    assert saida["total_count"] == 2
    assert saida["meta"]["out_of_scope_offer_count"] == 1
    assert saida["metrics"]["monitored_offers"] == 2
    assert saida["metrics"]["eligible_offers"] == 1


def test_preco_nulo_e_permitido_e_nunca_vira_zero():
    s = SessaoFake(ofertas=[oferta(observed_price=None, is_active=False)])
    linha = servir(s)["rows"][0]
    assert linha["advertised_price"] is None
    assert linha["observed_effective_amount"] is None
    assert linha["comparison_status"] == pm.STATUS_INACTIVE


def test_preco_nulo_em_oferta_ativa_nao_e_comparado_e_declara_o_motivo():
    s = SessaoFake(ofertas=[oferta(observed_price=None)],
                   referencias=[referencia()])
    saida = servir(s)
    linha = saida["rows"][0]
    assert linha["advertised_price"] is None
    assert linha["difference_amount"] is None and linha["difference_pct"] is None
    assert linha["non_comparable_reason"] == dom.REASON_INVALID_CHANNEL_PRICE
    assert saida["metrics"]["non_comparable_reasons"][
        dom.REASON_INVALID_CHANNEL_PRICE] == 1
    assert saida["metrics"]["comparable_offers"] == 0


@pytest.mark.parametrize("ruim", [Decimal("NaN"), float("inf"), float("-inf")])
def test_preco_nao_finito_e_recusado_e_nunca_comparado(ruim):
    s = SessaoFake(ofertas=[oferta(observed_price=ruim)],
                   referencias=[referencia()])
    linha = servir(s)["rows"][0]
    assert linha["advertised_price"] is None
    assert linha["difference_pct"] is None
    assert linha["non_comparable_reason"] == dom.REASON_INVALID_CHANNEL_PRICE


def test_referencia_ausente_e_referencia_ambigua_sao_distinguidas():
    s = SessaoFake(
        ofertas=[oferta(offer_key="A", seller_sku="SEM-REF"),
                 oferta(offer_key="B", seller_sku="DUP")],
        referencias=[referencia(reference_row_id="R1", source_sku="DUP"),
                     referencia(reference_row_id="R2", source_sku="DUP")],
    )
    por_chave = {r["offer_key"]: r for r in servir(s)["rows"]}
    assert por_chave["A"]["comparison_status"] == pm.STATUS_NO_REFERENCE
    assert por_chave["A"]["non_comparable_reason"] == dom.REASON_REFERENCE_MISSING
    assert por_chave["B"]["comparison_status"] == pm.STATUS_AMBIGUOUS
    assert por_chave["B"]["non_comparable_reason"] == dom.REASON_AMBIGUOUS


def test_comparacao_real_produz_diferenca_e_status():
    s = SessaoFake(ofertas=[oferta(observed_price=Decimal("50.00"))],
                   referencias=[referencia(
                       suggested_retail_amount=Decimal("60.00"))])
    linha = servir(s)["rows"][0]
    assert linha["comparison_status"] == pm.STATUS_BELOW
    assert linha["difference_amount"] == -10.0
    assert linha["suggested_retail_amount"] == 60.0


# ---------------------------------------------------------------------------
# 17 a 22 — flags, PII, schema, erro e SQL
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_flag_desligada_nao_consulta_a_tabela_nova(canal, monkeypatch):
    monkeypatch.setattr(mp.settings, "pma_shopee_enabled", False)
    monkeypatch.setattr(mp.settings, "pma_tiktok_enabled", False)
    s = SessaoFake(ofertas=[oferta(marketplace=canal)])
    saida = servir(s, canal)
    assert s.executadas == []
    assert saida["meta"]["unavailable_reason"] == dom.UNAVAILABLE_CHANNEL_DISABLED


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_flag_ligada_serve_o_dado_real(canal):
    s = SessaoFake(ofertas=[oferta(marketplace=canal)])
    saida = servir(s, canal)
    assert saida["meta"]["availability"] == "available"
    assert saida["total_count"] == 1


def test_nenhuma_coluna_de_pii_e_selecionada():
    proibidos = ("cpf", "cnpj", "telefone", "phone", "email", "endereco",
                 "address", "buyer", "customer", "recipient", "destinatario",
                 "cep")
    for sql in (mp.SQL_CHANNEL_OFFERS, mp.SQL_CHANNEL_ACCOUNT_CLOCKS,
                mp.SQL_CHANNEL_LATEST_OBSERVED_DATE,
                mp.SQL_CHANNEL_AVAILABLE_DATES, mp.SQL_CHANNEL_DATE_EXISTS):
        baixo = sql.lower()
        assert "select *" not in baixo, sql[:120]
        for token in proibidos:
            assert token not in baixo, (token, sql[:120])


def test_resposta_valida_contra_o_schema_publicado():
    from app.schemas.monitoramento_preco import MonitoramentoPrecoResponse

    s = SessaoFake(ofertas=[oferta(offer_key="A"),
                            oferta(offer_key="B", observed_price=None,
                                   is_active=False)],
                   referencias=[referencia()])
    MonitoramentoPrecoResponse(**servir(s))


def test_recusa_nao_ecoa_a_entrada():
    s = SessaoFake(ofertas=[oferta()])
    veneno = "<script>alert(1)</script> postgresql://u:p@10.0.0.1/db"
    for kw in ({"brand": veneno}, {"shop_account": veneno},
               {"product_type": veneno}, {"observed_date": veneno}):
        with pytest.raises(mp.MonitoramentoPrecoError) as erro:
            servir(s, **kw)
        assert veneno not in str(erro.value)
        assert "10.0.0.1" not in str(erro.value)


def test_todo_filtro_viaja_por_parametro_nomeado():
    """Nenhum valor de usuario e' interpolado no texto da consulta."""
    s = SessaoFake(ofertas=_muitas())
    servir(s, brand="kokeshi", shop_account="kokeshi",
           product_type="no_kit_signal", product_query="SKU-03")
    for texto, params in s.executadas:
        assert "kokeshi" not in texto, texto[:140]
        assert "SKU-03" not in texto and "sku-03" not in texto, texto[:140]
    alvo = [p for t, p in s.executadas if "fact_channel_offer_observation" in t
            and "brands" in p][0]
    assert alvo["brands"] == ["kokeshi"]
    assert alvo["accounts"] == ["kokeshi"]
    assert alvo["query_like"] == "%SKU-03%"


def test_a_politica_de_data_nao_e_global():
    assert mp.date_policy_for("ml") == pm.POLICY_CLOSED_DAY
    assert mp.date_policy_for("shopee") == pm.POLICY_SNAPSHOT_CURRENT
    assert mp.date_policy_for("tiktok") == pm.POLICY_SNAPSHOT_CURRENT
    assert pm.date_ceiling(HOJE, pm.POLICY_CLOSED_DAY) == D1
    assert pm.date_ceiling(HOJE, pm.POLICY_SNAPSHOT_CURRENT) == D0


# ---------------------------------------------------------------------------
# Gate PMA-2C4D3-H2 — cobertura OBSERVADA por canal e data
#
# `monitored_brands` responde "o que o negocio monitora" e volta igual para
# Shopee e TikTok, com Kokeshi incluida. A Shopee nao observa Kokeshi: montar o
# filtro com aquela lista oferecia a marca e respondia "0", que se le como
# "Kokeshi nao tem anuncios". `observed_brands` responde outra pergunta — o que
# ESTA fotografia contem — e e' a fonte do filtro.
# ---------------------------------------------------------------------------

def _ofertas_shopee_reais():
    """Quatro marcas observadas; Kokeshi ausente, como a fonte real."""
    return [oferta(marketplace="shopee", brand=b, shop_account=b,
                   offer_key="SH-" + b)
            for b in ["apice", "barbours", "lescent", "rituaria"]]


def _ofertas_tiktok_reais():
    """Sete marcas, duas delas fora do escopo comercial."""
    dentro = ["apice", "barbours", "kokeshi", "lescent", "rituaria"]
    fora = ["gocase", "denavita"]
    linhas = [oferta(marketplace="tiktok", brand=b, shop_account="tiktok",
                     observation_mode="daily_series", offer_key="TK-" + b,
                     business_scope=dom.BUSINESS_SCOPE_IN) for b in dentro]
    linhas += [oferta(marketplace="tiktok", brand=b, shop_account="tiktok",
                      observation_mode="daily_series", offer_key="TK-" + b,
                      business_scope=dom.BUSINESS_SCOPE_OUT) for b in fora]
    return linhas


def test_h2_shopee_observa_quatro_marcas_e_kokeshi_fica_de_fora():
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    meta = servir(s, "shopee")["meta"]
    assert meta["observed_brands"] == ["apice", "barbours", "lescent", "rituaria"]
    assert "kokeshi" not in meta["observed_brands"]
    # E a ausencia e' DECLARADA, nao silenciosa.
    assert "kokeshi" in meta["monitored_unobserved_brands"]


def test_h2_tiktok_observa_sete_marcas_inclusive_fora_do_escopo():
    s = SessaoFake(ofertas=_ofertas_tiktok_reais())
    meta = servir(s, "tiktok")["meta"]
    assert len(meta["observed_brands"]) == 7
    for b in ("gocase", "denavita"):
        assert b in meta["observed_brands"], (
            "marca fora do escopo comercial que TEM observacao continua "
            "selecionavel; esconde-la apagaria linhas visiveis da tabela")
    assert meta["monitored_unobserved_brands"] == []


def test_h2_cobertura_nao_encolhe_com_filtro_de_marca():
    """O ponto do contrato: a cobertura e' anterior aos filtros."""
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    meta = servir(s, "shopee", brand="barbours")["meta"]
    assert meta["observed_brands"] == ["apice", "barbours", "lescent", "rituaria"]


def test_h2_cobertura_nao_encolhe_com_conta_tipo_ou_busca():
    esperado = ["apice", "barbours", "lescent", "rituaria"]
    for kw in ({"shop_account": "barbours"},
               {"product_type": dom.PRODUCT_NO_KIT_SIGNAL},
               {"product_query": "Shampoo"}):
        s = SessaoFake(ofertas=_ofertas_shopee_reais())
        assert servir(s, "shopee", **kw)["meta"]["observed_brands"] == esperado, kw


def test_h2_busca_sem_resultado_nao_apaga_a_cobertura():
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    saida = servir(s, "shopee", product_query="zzzz-nao-existe")
    assert saida["total_count"] == 0
    assert saida["meta"]["observed_brands"] == [
        "apice", "barbours", "lescent", "rituaria"], (
        "a tabela pode ficar vazia; a COBERTURA da fotografia nao muda")


def test_h2_paginacao_nao_encolhe_a_cobertura():
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    a = servir(s, "shopee", limit=1, offset=0)["meta"]["observed_brands"]
    s2 = SessaoFake(ofertas=_ofertas_shopee_reais())
    b = servir(s2, "shopee", limit=1, offset=3)["meta"]["observed_brands"]
    assert a == b == ["apice", "barbours", "lescent", "rituaria"]


def test_h2_historico_tem_cobertura_propria_da_data():
    """D-1 nao herda a cobertura de D0, nem o contrario."""
    ontem = [oferta(marketplace="shopee", brand=b, shop_account=b,
                    observed_date=D1, offer_key="OLD-" + b)
             for b in ["barbours", "rituaria"]]
    s = SessaoFake(ofertas=_ofertas_shopee_reais() + ontem, datas=[D0, D1])
    hoje = servir(s, "shopee")["meta"]
    assert hoje["observed_brands"] == ["apice", "barbours", "lescent", "rituaria"]
    s2 = SessaoFake(ofertas=_ofertas_shopee_reais() + ontem, datas=[D0, D1])
    antes = servir(s2, "shopee", observed_date=D1.isoformat())["meta"]
    assert antes["observed_brands"] == ["barbours", "rituaria"]
    assert "apice" in antes["monitored_unobserved_brands"]


def test_h2_canal_sem_linhas_devolve_listas_vazias():
    s = SessaoFake(ofertas=[], datas=[])
    meta = servir(s, "shopee")["meta"]
    assert meta["observed_brands"] == []
    assert meta["monitored_unobserved_brands"] == []


def test_h2rv_ml_sem_fotografia_nao_declara_ausencia():
    """Achado da revisao terminal do PR #20.

    O ML nao passa por `_unavailable_envelope`: quando a data pedida nao tem
    fotografia ele segue pelo caminho normal, com `observed_date: null` e
    `availability: available`. A diferenca `monitoradas - observadas` devolvia
    entao as QUATRO marcas, e a tela afirmava "Sem observacao nesta fotografia"
    sobre uma fotografia que nao existe — a mesma afirmacao que o envelope dos
    canais recusa a fazer. Medido em producao antes da correcao com
    `GET ?marketplace=ml&observed_date=2026-01-05`.
    """
    s = SessaoFake(listings_ml=[])
    sem_foto = (D1 - timedelta(days=3)).isoformat()
    meta = mp.get_monitoramento_preco(
        s, marketplace="ml", today=HOJE, observed_date=sem_foto)["meta"]
    assert meta["observed_date"] is None, "pre-condicao: sem fotografia"
    assert meta["observed_brands"] == []
    assert meta["monitored_unobserved_brands"] == [], (
        "sem fotografia nao ha ausencia de observacao a declarar")


def test_h2rv_ml_com_fotografia_continua_declarando_a_ausencia():
    """Contraprova do teste acima: o guarda nao pode calar o caso legitimo."""
    s = SessaoFake(listings_ml=[_listing_ml("barbours")])
    meta = mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE)["meta"]
    assert meta["observed_date"] is not None
    assert meta["observed_brands"] == ["barbours"]
    assert "kokeshi" in meta["monitored_unobserved_brands"], (
        "com fotografia, a marca monitorada e ausente continua sendo declarada")


def test_h2_cobertura_sem_duplicata_e_com_ordem_estavel():
    muitas = []
    for b in ["rituaria", "apice", "barbours", "apice", "rituaria", "lescent"]:
        muitas.append(oferta(marketplace="shopee", brand=b, shop_account=b,
                             offer_key="SH-" + b + "-" + str(len(muitas))))
    s = SessaoFake(ofertas=muitas)
    obs = servir(s, "shopee")["meta"]["observed_brands"]
    assert obs == sorted(set(obs)), "ordenado e sem repeticao"
    s2 = SessaoFake(ofertas=muitas)
    assert servir(s2, "shopee")["meta"]["observed_brands"] == obs


def test_h2_nao_observada_nao_e_zero_nem_carrega_motivo_inventado():
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    meta = servir(s, "shopee")["meta"]
    # A lista e' de NOMES. Nenhuma contagem, nenhum motivo — o servico nao sabe
    # por que a fonte nao devolveu a marca, e inventar seria pior que calar.
    assert isinstance(meta["monitored_unobserved_brands"], list)
    assert all(isinstance(b, str) for b in meta["monitored_unobserved_brands"])


def test_h2_a_consulta_de_cobertura_nao_recebe_filtro_de_usuario():
    """Contraprova estrutural: se a cobertura receber os filtros do usuario,
    ela deixa de ser cobertura."""
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    servir(s, "shopee", brand="barbours", shop_account="barbours",
           product_query="Shampoo")
    cobertura = [(t, p) for t, p in s.executadas if "group by brand" in t]
    assert len(cobertura) == 1, "uma unica consulta de cobertura, sem N+1"
    _, params = cobertura[0]
    assert set(params) == {"marketplace", "observed_date"}, (
        "a cobertura so' pode ser escopada por canal e data; veio "
        + str(set(params)))


def test_h2_uma_consulta_de_cobertura_por_requisicao_sem_loop_por_marca():
    s = SessaoFake(ofertas=_ofertas_tiktok_reais())
    servir(s, "tiktok")
    n = sum(1 for t, _ in s.executadas if "group by brand" in t)
    assert n == 1, "esperava 1 consulta de cobertura, houve " + str(n)


def test_h2rv_o_where_da_cobertura_escopa_por_canal_E_por_data():
    """Achado da revisao terminal do PR #20 — lacuna de contraprova.

    `SessaoFake` despacha pelo TEXTO da consulta e filtra pelos PARAMETROS, de
    modo que a clausula WHERE do SQL nunca e' exercitada: apagar
    `marketplace = :marketplace` da cobertura mantinha a suite inteira verde.
    Em producao isso nao levantaria erro — devolveria as marcas de TODOS os
    canais naquela data, e a Shopee voltaria a oferecer Kokeshi, pela porta dos
    fundos. Este teste le o SQL, nao o fake.
    """
    import re

    def clausula_where(sql: str) -> str:
        m = re.search(r"\bWHERE\b(.*?)\bGROUP BY\b", sql, re.S | re.I)
        assert m, "SQL de cobertura sem WHERE ... GROUP BY: " + sql
        return m.group(1)

    canal = clausula_where(mp.SQL_CHANNEL_OBSERVED_BRANDS)
    assert "marketplace = :marketplace" in canal, (
        "sem o canal no WHERE a cobertura mistura marketplaces")
    assert "observed_date = :observed_date" in canal, (
        "sem a data no WHERE a cobertura mistura fotografias")

    ml = clausula_where(mp.SQL_OBSERVED_BRANDS)
    assert "marketplace = :marketplace" in ml
    assert "ref_date = :ref_date" in ml

    # Nenhum filtro do usuario pode entrar aqui, nem como coluna nem como bind.
    for where in (canal, ml):
        for proibido in ("brand_filter", "brands", "shop_account", "account",
                         "product_type", "query", "limit", "offset", "status"):
            assert proibido not in where.lower(), (
                "filtro de usuario na cobertura: " + proibido)
        # `brand` so' pode aparecer no GROUP BY / ORDER BY, nunca no WHERE.
        assert "brand" not in where.lower()

    for sql in (mp.SQL_CHANNEL_OBSERVED_BRANDS, mp.SQL_OBSERVED_BRANDS):
        assert "GROUP BY brand" in sql and "ORDER BY brand" in sql, (
            "a cobertura precisa sair agrupada e ordenada pelo banco")


def test_h2_monitored_brands_continua_intacto():
    """Compatibilidade: o campo antigo nao mudou de valor nem de semantica."""
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    meta = servir(s, "shopee")["meta"]
    assert meta["monitored_brands"] == list(mp.channel_brand_allowlist("shopee"))
    assert "kokeshi" in meta["monitored_brands"], (
        "o escopo do negocio nao encolheu; quem mudou foi a fonte do FILTRO")


def test_h2_a_cobertura_usa_bind_parameters_e_nao_interpolacao():
    fonte = mp.SQL_CHANNEL_OBSERVED_BRANDS + mp.SQL_OBSERVED_BRANDS
    assert ":marketplace" in fonte and ":observed_date" in fonte
    assert ":ref_date" in mp.SQL_OBSERVED_BRANDS
    for proibido in ("format(", "% (", "' +", '" +'):
        assert proibido not in fonte, proibido


# ---------------------------------------------------------------------------
# Gate PMA-2C4D3-H3 — a COBERTURA autoriza o filtro de marca.
#
# Antes, quem autorizava era `channel_brand_allowlist`, que responde "o que o
# negocio monitora". `gocase` e `denavita` sao observadas no TikTok, a tela as
# oferecia (o filtro sai de `observed_brands`) e a API as recusava com 422.
# ---------------------------------------------------------------------------
def _tiktok_com_marcas_fora_da_allowlist():
    """Fotografia do TikTok como a producao: sete marcas, duas fora do escopo
    de monitoramento."""
    linhas = []
    for marca in ("apice", "barbours", "denavita", "gocase", "kokeshi",
                  "lescent", "rituaria"):
        for i in range(2):
            linhas.append(oferta(marketplace="tiktok", brand=marca,
                                 shop_account=marca,
                                 offer_key=f"TK-{marca}-{i}"))
    return linhas


def test_h3_marca_observada_fora_da_allowlist_e_filtravel():
    for marca in ("gocase", "denavita"):
        s = SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist())
        saida = servir(s, "tiktok", brand=marca)
        assert saida["meta"]["observed_brands"].count(marca) == 1
        assert saida["total_count"] == 2, (marca, saida["total_count"])
        assert {r["brand"] for r in saida["rows"]} == {marca}


def test_h3_contagem_filtrada_bate_com_a_fotografia():
    """O filtro devolve o que a fato tem daquela marca, nao um subconjunto."""
    s = SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist())
    total = servir(s, "tiktok")["total_count"]
    soma = 0
    for marca in servir(SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist()),
                        "tiktok")["meta"]["observed_brands"]:
        s2 = SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist())
        soma += servir(s2, "tiktok", brand=marca)["total_count"]
    assert soma == total, (soma, total)


def test_h3_marca_sintetica_e_aceita_sem_editar_allowlist():
    """Prova que a autoridade e' a fotografia: uma marca que NAO existe em
    nenhuma constante do codigo passa a ser filtravel por estar observada."""
    inventada = "marca-que-nao-existe-no-codigo"
    assert inventada not in mp.channel_brand_allowlist("tiktok")
    assert inventada not in pm.MONITORED_BRANDS
    linhas = [oferta(marketplace="tiktok", brand=inventada,
                     shop_account="x", offer_key="TK-INV-1")]
    s = SessaoFake(ofertas=linhas)
    saida = servir(s, "tiktok", brand=inventada)
    assert saida["total_count"] == 1
    assert inventada in saida["meta"]["observed_brands"]


def test_h3_marca_monitorada_e_nao_observada_e_recusada_sem_eco():
    """Kokeshi na Shopee: recusa FIXA, nunca 200 com zero.

    Devolver zero se le como "Kokeshi nao tem anuncio". Era o defeito de
    origem desta familia de gates.
    """
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    erro = None
    try:
        servir(s, "shopee", brand="kokeshi")
    except mp.MonitoramentoPrecoError as exc:
        erro = str(exc)
    assert erro == mp.ERRO_BRAND_NAO_OBSERVADA
    assert "kokeshi" not in erro.lower()
    meta = servir(SessaoFake(ofertas=_ofertas_shopee_reais()), "shopee")["meta"]
    assert "kokeshi" in meta["monitored_unobserved_brands"]
    assert "kokeshi" not in meta["observed_brands"]


def test_h3_marca_inexistente_e_recusada_sem_eco():
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    erro = None
    try:
        servir(s, "shopee", brand="marcaquenaoexiste")
    except mp.MonitoramentoPrecoError as exc:
        erro = str(exc)
    assert erro == mp.ERRO_BRAND_NAO_OBSERVADA
    assert "marcaquenaoexiste" not in erro


def test_h3_payload_hostil_e_recusado_antes_de_qualquer_consulta_filtrada():
    """A validacao SINTATICA roda antes de o banco ser tocado pelo filtro."""
    # `BARBOURS ` NAO entra aqui: caixa e espaco normalizam para `barbours`,
    # que e' observada de verdade na Shopee. Normalizar nao e' contornar — o
    # contorno esta coberto por `test_h3_caixa_e_espaco_nao_contornam_a_
    # cobertura`, onde a marca normalizada continua NAO observada.
    hostis = ("<script>alert(1)</script>", "a' OR 1=1 --", "DROP TABLE x",
              "barbours; DELETE", "../../etc/passwd", "rituária",
              "\x00", "a" * 200, "marca\nnova")
    for payload in hostis:
        s = SessaoFake(ofertas=_ofertas_shopee_reais())
        erro = None
        try:
            servir(s, "shopee", brand=payload)
        except mp.MonitoramentoPrecoError as exc:
            erro = str(exc)
        assert erro in (mp.ERRO_BRAND_INVALIDA, mp.ERRO_BRAND_TAMANHO,
                        mp.ERRO_BRAND_NAO_OBSERVADA), (payload[:30], erro)
        for pedaco in ("script", "DROP", "DELETE", "passwd", "OR 1=1"):
            assert pedaco not in erro, payload[:30]


def test_h3_normalizacao_de_caixa_e_espaco_continua_valendo():
    """Caixa e espaco NORMALIZAM, como em `normalize_accounts`: a marca
    autorizada segue autorizada escrita de qualquer jeito."""
    for variante in ("BARBOURS", " barbours ", "Barbours"):
        s = SessaoFake(ofertas=_ofertas_shopee_reais())
        direto = servir(SessaoFake(ofertas=_ofertas_shopee_reais()),
                        "shopee", brand="barbours")["total_count"]
        assert servir(s, "shopee", brand=variante)["total_count"] == direto


def test_h3_caixa_e_espaco_nao_contornam_a_cobertura():
    """`GOCASE` normaliza para `gocase`; na Shopee segue nao observada."""
    for variante in ("GOCASE", " gocase ", "GoCase"):
        s = SessaoFake(ofertas=_ofertas_shopee_reais())
        erro = None
        try:
            servir(s, "shopee", brand=variante)
        except mp.MonitoramentoPrecoError as exc:
            erro = str(exc)
        assert erro == mp.ERRO_BRAND_NAO_OBSERVADA, variante


def test_h3_data_sem_fotografia_nao_fabrica_cobertura():
    s = SessaoFake(ofertas=[], datas=[])
    erro = None
    try:
        servir(s, "shopee", brand="barbours", observed_date="2026-01-05")
    except mp.MonitoramentoPrecoError as exc:
        erro = str(exc)
    assert erro == mp.ERRO_BRAND_NAO_OBSERVADA
    # E sem marca, o envelope honesto continua sendo servido.
    vazio = servir(SessaoFake(ofertas=[], datas=[]), "shopee",
                   observed_date="2026-01-05")
    assert vazio["meta"]["observed_brands"] == []
    assert vazio["meta"]["monitored_unobserved_brands"] == []


def test_h3_marca_nao_vaza_entre_canais():
    """`gocase` e' observada no TikTok e NAO na Shopee: o canal decide."""
    s = SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist())
    assert servir(s, "tiktok", brand="gocase")["total_count"] == 2
    s2 = SessaoFake(ofertas=_ofertas_shopee_reais())
    try:
        servir(s2, "shopee", brand="gocase")
        assert False, "gocase nao pode ser aceita na Shopee"
    except mp.MonitoramentoPrecoError as exc:
        assert str(exc) == mp.ERRO_BRAND_NAO_OBSERVADA


def test_h3_cobertura_continua_invariante_com_o_filtro_novo():
    base = servir(SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist()),
                  "tiktok")["meta"]["observed_brands"]
    for kw in ({"brand": "gocase"}, {"brand": "denavita"},
               {"shop_account": "gocase"}, {"product_query": "zzz"},
               {"limit": 1, "offset": 0}, {"limit": 1, "offset": 10}):
        s = SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist())
        assert servir(s, "tiktok", **kw)["meta"]["observed_brands"] == base, kw


def test_h3_uma_consulta_de_cobertura_mesmo_com_filtro_de_marca():
    """A validacao REAPROVEITA a consulta de cobertura: sem N+1, sem consulta
    por marca."""
    s = SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist())
    servir(s, "tiktok", brand="gocase")
    n = sum(1 for t, _ in s.executadas if "group by brand" in t)
    assert n == 1, f"esperava 1 consulta de cobertura, houve {n}"


def test_h3_a_cobertura_e_o_filtro_continuam_por_bind_parameter():
    s = SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist())
    servir(s, "tiktok", brand="gocase")
    for texto, params in s.executadas:
        assert "gocase" not in texto, "valor de marca interpolado no SQL"
    filtradas = [(t, p) for t, p in s.executadas if p and "brands" in p]
    assert filtradas, "nenhuma consulta recebeu o filtro"
    assert any("gocase" in (p.get("brands") or []) for _, p in filtradas)


def test_h3_flag_desligada_continua_fail_closed_mesmo_com_marca(monkeypatch):
    """Canal desligado recusa ANTES de tocar o banco — inclusive com `brand`."""
    monkeypatch.setattr(mp.settings, "pma_shopee_enabled", False)
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    saida = servir(s, "shopee", brand="gocase")
    assert saida["meta"]["unavailable_reason"] == dom.UNAVAILABLE_CHANNEL_DISABLED
    assert s.executadas == [], "flag desligada nao pode emitir consulta"


def test_h3_ml_preserva_a_razao_publicada_de_fora_de_escopo():
    """`apice` e `yenzah` tem razao PROPRIA no ML, mais informativa que
    "nao observada". Trocar por ela seria perder contrato publicado."""
    for marca in ("apice", "yenzah"):
        s = SessaoFake(listings_ml=[_listing_ml("barbours")])
        erro = None
        try:
            mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE,
                                       brand=marca)
        except mp.MonitoramentoPrecoError as exc:
            erro = str(exc)
        assert erro == mp.ERRO_BRAND_INVALIDA, marca
        assert pm.BRAND_SCOPE_OUT_OF_SCOPE in erro


def test_h3_ml_marca_monitorada_ausente_da_fotografia_e_recusada():
    s = SessaoFake(listings_ml=[_listing_ml("barbours")])
    erro = None
    try:
        mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE,
                                   brand="kokeshi")
    except mp.MonitoramentoPrecoError as exc:
        erro = str(exc)
    assert erro == mp.ERRO_BRAND_NAO_OBSERVADA


def test_h3_sem_filtro_nada_muda():
    """Compatibilidade: a resposta sem `brand` e' identica a de antes."""
    a = servir(SessaoFake(ofertas=_ofertas_shopee_reais()), "shopee")
    b = servir(SessaoFake(ofertas=_ofertas_shopee_reais()), "shopee",
               brand="all")
    assert a["total_count"] == b["total_count"]
    assert a["meta"]["observed_brands"] == b["meta"]["observed_brands"]
    assert a["meta"]["monitored_brands"] == b["meta"]["monitored_brands"]


def test_h3_a_cobertura_e_pedida_SEMPRE_para_o_canal_servido():
    """Lacuna exposta pela mutacao M9 da Fase 6.

    `SessaoFake` despacha pelo TEXTO da consulta e, no ramo do ML, filtra
    `listings_ml` so' por `ref_date`. Trocar `canal` por um literal na consulta
    de cobertura do ML mantinha a suite inteira verde. Em producao isso serviria
    a cobertura de OUTRO canal — e a autoridade do filtro de marca passaria a
    ser a fotografia errada. Aqui se afirma o PARAMETRO emitido, nao o retorno
    do fake.
    """
    for canal, kw in (("shopee", {}), ("tiktok", {})):
        s = SessaoFake(ofertas=_tiktok_com_marcas_fora_da_allowlist()
                       if canal == "tiktok" else _ofertas_shopee_reais())
        servir(s, canal, **kw)
        cobertura = [(t, pr) for t, pr in s.executadas if "group by brand" in t]
        assert cobertura, f"{canal}: nenhuma consulta de cobertura"
        for _, pr in cobertura:
            assert pr.get("marketplace") == canal, (canal, pr)

    s = SessaoFake(listings_ml=[_listing_ml("barbours")])
    mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE)
    cobertura = [(t, pr) for t, pr in s.executadas if "group by brand" in t]
    assert cobertura, "ml: nenhuma consulta de cobertura"
    for _, pr in cobertura:
        assert pr.get("marketplace") == "ml", pr


def test_h3_marca_malformada_e_recusada_SEM_emitir_consulta():
    """Lacuna exposta pela mutacao M10 da Fase 6.

    Sem a checagem de formato, o payload hostil chegava ate a validacao de
    cobertura e era recusado la' — a suite ficava verde e o contrato "rejeitado
    ANTES da consulta filtrada" deixava de valer. O que prova a ordem nao e' a
    mensagem: e' o banco nao ter sido tocado.
    """
    malformados = ("<script>alert(1)</script>", "a' OR 1=1 --", "DROP TABLE x",
                   "../../etc/passwd", "rituária", "marca\nnova", "\x00",
                   "barbours; DELETE")
    for payload in malformados:
        s = SessaoFake(ofertas=_ofertas_shopee_reais())
        erro = None
        try:
            servir(s, "shopee", brand=payload)
        except mp.MonitoramentoPrecoError as exc:
            erro = str(exc)
        assert erro == mp.ERRO_BRAND_INVALIDA, (payload[:24], erro)
        assert s.executadas == [], (
            f"{payload[:24]!r} chegou ao banco: {len(s.executadas)} consulta(s)")


def test_h3_marca_bem_formada_mas_nao_observada_falha_DEPOIS_da_cobertura():
    """Contraprova do teste acima: a recusa de pertencimento e' outra coisa.

    Aqui a consulta de cobertura DEVE ter sido emitida — e' ela que decide.
    """
    s = SessaoFake(ofertas=_ofertas_shopee_reais())
    erro = None
    try:
        servir(s, "shopee", brand="kokeshi")
    except mp.MonitoramentoPrecoError as exc:
        erro = str(exc)
    assert erro == mp.ERRO_BRAND_NAO_OBSERVADA
    assert any("group by brand" in t for t, _ in s.executadas), (
        "a cobertura precisa ter sido consultada para decidir")
    assert not any(pr.get("brand_filter") for _, pr in s.executadas if pr), (
        "nenhuma consulta FILTRADA pode ter sido emitida antes da recusa")
