"""VENDAS-POR-HORA-1 — o "Hoje" padronizado por hora (sem banco).

O corte e' a menor hora fechada entre os canais do filtro; hoje, ontem e a
semana passada somam ate' o MESMO corte; Kokeshi na Shopee vira limitacao.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services import hoje_por_hora_service as S

BRT = timezone(timedelta(hours=-3))
HOJE = date(2026, 11, 3)
AGORA = datetime(2026, 11, 3, 13, 41, tzinfo=BRT)


def brt(h, m=0, d=HOJE):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=BRT)


class _Resultado:
    def __init__(self, linhas=None, escalar=None):
        self._linhas = linhas or []
        self._escalar = escalar

    def scalar(self):
        return self._escalar

    def mappings(self):
        return self

    def all(self):
        return self._linhas

    def __iter__(self):
        return iter(self._linhas)


class SessaoFalsa:
    def __init__(self, fontes, vendas, existe=True, marcas=()):
        self.fontes, self.vendas, self.existe, self.marcas = fontes, vendas, existe, list(marcas)
        self.params = {}

    def execute(self, sql, params=None):
        sql = str(sql)
        if "to_regclass" in sql:
            return _Resultado(escalar=self.existe)
        if S.FONTES in sql:
            return _Resultado([(m, f) for m, f in self.fontes.items() if m in params["m"]])
        if "-- por_marca" in sql:
            return _Resultado(self.marcas)
        self.params = params
        return _Resultado([v for v in self.vendas if v["marketplace_id"] in params["mkt_ids"]])


def venda(dia, mkt, gmv, pedidos):
    return {"dia": dia, "marketplace_id": mkt, "gmv": gmv, "pedidos": pedidos}


def test_hora_fechada_do_canal():
    assert S.hora_fechada_do_canal(brt(13, 20), HOJE) == 13
    assert S.hora_fechada_do_canal(brt(23, 0, HOJE - timedelta(days=1)), HOJE) == 0
    assert S.hora_fechada_do_canal(None, HOJE) == 0
    utc = datetime(2026, 11, 3, 16, 5, tzinfo=timezone.utc)  # 13:05 BRT
    assert S.hora_fechada_do_canal(utc, HOJE) == 13


def test_corte_e_a_menor_hora_entre_os_canais_do_filtro():
    fontes = {1: brt(13, 35), 2: brt(13, 10), 3: brt(11, 5)}
    assert S.calcular_corte(fontes, [1, 2, 3], AGORA) == 11
    assert S.calcular_corte(fontes, [1, 2], AGORA) == 13
    assert S.calcular_corte({1: brt(13, 35)}, [1, 3], AGORA) == 0  # Shopee sem fonte


def test_corte_nunca_passa_da_hora_atual():
    assert S.calcular_corte({1: brt(23, 59)}, [1], brt(9, 30)) == 9


def test_soma_hoje_ontem_e_semana_passada_no_mesmo_corte():
    db = SessaoFalsa(
        fontes={1: brt(13, 35), 2: brt(13, 10), 3: brt(13, 3)},
        vendas=[venda(HOJE, 1, 1000, 20), venda(HOJE, 2, 500, 5),
                venda(HOJE - timedelta(days=1), 1, 800, 16), venda(HOJE - timedelta(days=7), 3, 90, 3)],
    )
    res = S.get_hoje_por_hora(db, [1, 2, 3], None, agora=AGORA)
    assert res["disponivel"] and res["corte_hora"] == 13
    assert db.params["corte"] == 13
    assert db.params["dias"] == [HOJE, HOJE - timedelta(days=1), HOJE - timedelta(days=7)]
    assert res["hoje"] == {"gmv": 1500.0, "pedidos": 25, "ticket": 60.0}
    assert res["ontem"] == {"gmv": 800.0, "pedidos": 16, "ticket": 50.0}
    assert res["semana_passada"] == {"gmv": 90.0, "pedidos": 3, "ticket": 30.0}
    tiktok = next(c for c in res["por_canal"] if c["channel"] == "tiktok")
    assert tiktok["hoje"]["gmv"] == 1000.0 and tiktok["ontem"]["pedidos"] == 16
    shopee = next(c for c in res["por_canal"] if c["channel"] == "shopee")
    assert shopee["hoje"] == {"gmv": 0.0, "pedidos": 0, "ticket": None}


def test_kokeshi_na_shopee_vira_limitacao():
    db = SessaoFalsa(fontes={3: brt(13, 3)}, vendas=[])
    assert S.get_hoje_por_hora(db, [3], None, agora=AGORA)["limitacoes"] == [S.LIMITACAO_KOKESHI_SHOPEE]
    assert S.get_hoje_por_hora(db, [3], ["kokeshi"], agora=AGORA)["limitacoes"]
    assert S.get_hoje_por_hora(db, [3], ["apice"], agora=AGORA)["limitacoes"] == []
    assert S.get_hoje_por_hora(db, [1, 2], None, agora=AGORA)["limitacoes"] == []


def test_filtro_de_marca_vai_para_o_sql():
    db = SessaoFalsa(fontes={1: brt(13, 35)}, vendas=[])
    S.get_hoje_por_hora(db, [1], ["apice", "barbours"], agora=AGORA)
    assert db.params["brands"] == ["apice", "barbours"]


def test_sem_migration_responde_indisponivel():
    res = S.get_hoje_por_hora(SessaoFalsa({}, [], existe=False), [1, 2, 3], None, agora=AGORA)
    assert res["disponivel"] is False and res["corte_hora"] is None and res["motivo"]


def test_rota_registrada_e_resposta_valida_no_schema():
    from app.routers.performance import router
    from app.schemas.hoje_por_hora import HojePorHoraResponse
    assert any(r.path == "/api/v1/performance/hoje-por-hora" for r in router.routes)
    db = SessaoFalsa(fontes={1: brt(13, 35)}, vendas=[venda(HOJE, 1, 10, 1)])
    HojePorHoraResponse(**S.get_hoje_por_hora(db, [1], None, agora=AGORA))
    HojePorHoraResponse(**S.get_hoje_por_hora(SessaoFalsa({}, [], existe=False), [1], None, agora=AGORA))


def test_projecao_pelo_ritmo_de_ontem():
    # Ontem, ate' 13h saiu 40% do dia: hoje ate' 13h com 500 tende a fechar em 1250.
    assert S.projetar_fechamento(500, 400, 1000) == 1250.0
    assert S.projetar_fechamento(500, 0, 1000) is None   # sem ritmo de ontem, sem palpite
    assert S.projetar_fechamento(0, 400, 1000) == 0.0


def test_tendencia_por_marca_usa_hoje_e_ontem_no_mesmo_corte():
    ontem = HOJE - timedelta(days=1)
    db = SessaoFalsa(
        fontes={1: brt(13, 35), 2: brt(13, 10)}, vendas=[],
        marcas=[{"brand": "apice", "dia": HOJE, "marketplace_id": 2, "gmv_corte": 500, "gmv_dia": 500},
                {"brand": "apice", "dia": ontem, "marketplace_id": 2, "gmv_corte": 400, "gmv_dia": 1000},
                {"brand": "apice", "dia": HOJE, "marketplace_id": 1, "gmv_corte": 300, "gmv_dia": 300},
                {"brand": "apice", "dia": ontem, "marketplace_id": 1, "gmv_corte": 100, "gmv_dia": 200},
                {"brand": "lescent", "dia": HOJE, "marketplace_id": 2, "gmv_corte": 80, "gmv_dia": 80}],
    )
    res = S.get_hoje_por_hora(db, [1, 2], None, agora=AGORA)
    apice, lescent = res["por_marca"]
    # Total: (500+300) x (1000+200) / (400+100) = 1920; por canal, cada um no proprio ritmo.
    assert (apice["hoje"], apice["ontem_mesma_hora"], apice["ontem_dia"], apice["projecao"]) == (800.0, 500.0, 1200.0, 1920.0)
    por_canal = {c["channel"]: c["projecao"] for c in apice["por_canal"]}
    assert por_canal == {"tiktok": 600.0, "ml": 1250.0}
    assert lescent["projecao"] is None  # ontem sem venda: sem projecao


def test_kokeshi_sem_tendencia_quando_a_shopee_esta_no_filtro():
    marcas = [{"brand": "kokeshi", "dia": HOJE, "marketplace_id": 2, "gmv_corte": 500, "gmv_dia": 500},
              {"brand": "kokeshi", "dia": HOJE - timedelta(days=1), "marketplace_id": 2, "gmv_corte": 400, "gmv_dia": 1000}]
    com_shopee = S.get_hoje_por_hora(SessaoFalsa({2: brt(13, 10), 3: brt(13, 3)}, [], marcas=marcas), [2, 3], None, agora=AGORA)
    so_ml = S.get_hoje_por_hora(SessaoFalsa({2: brt(13, 10)}, [], marcas=marcas), [2], None, agora=AGORA)
    assert com_shopee["por_marca"] == []
    assert so_ml["por_marca"][0]["projecao"] == 1250.0
