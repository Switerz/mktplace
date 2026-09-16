"""Gate FULL-SH-1C — contrato da API de desempenho FBS Shopee.

Sem banco: a flag nasce DESLIGADA, e os testes que exercitam agregacao chamam
as funcoes puras do servico com linhas sinteticas. O caminho desligado e'
verificado de ponta a ponta com TestClient -- e ele nao pode tocar o banco.
"""
from __future__ import annotations

import ast
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import shopee_fbs_service as svc

ROTA = "/api/v1/performance/shopee-fbs"
RAIZ = Path(__file__).resolve().parents[1]
SERVICE_SRC = io.open(RAIZ / "app" / "services" / "shopee_fbs_service.py",
                      encoding="utf-8").read()
SCHEMA_SRC = io.open(RAIZ / "app" / "schemas" / "shopee_fbs.py",
                     encoding="utf-8").read()
ROUTER_SRC = io.open(RAIZ / "app" / "routers" / "performance.py",
                     encoding="utf-8").read()


def _codigo(fonte: str) -> str:
    """Codigo sem docstring: as proibicoes sao sobre o que o codigo FAZ."""
    arv = ast.parse(fonte)
    for no in ast.walk(arv):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)):
            b = no.body
            if (b and isinstance(b[0], ast.Expr)
                    and isinstance(b[0].value, ast.Constant)
                    and isinstance(b[0].value.value, str)):
                b[0].value.value = ""
    return ast.unparse(arv)


SERVICE_CODE = _codigo(SERVICE_SRC)


@pytest.fixture()
def client():
    return TestClient(app)


def _linha(classe="fbs", gmv="1000.00", un=10, criados=12, elig=10, canc=2,
           tr=0, tr_gmv="0", up=0, up_gmv="0", h_sum=3600, h_n=5,
           brand="barbours", conta="barbours"):
    return {"fbs_class": classe, "brand": brand, "shop_account": conta,
            "gross_gmv": Decimal(gmv), "gross_units": un,
            "created_orders": criados, "eligible_orders": elig,
            "cancelled_orders": canc, "to_return_orders": tr,
            "to_return_gmv": Decimal(tr_gmv), "unpaid_orders": up,
            "unpaid_gmv": Decimal(up_gmv),
            "handling_seconds_sum": h_sum, "handling_sample_count": h_n,
            "source_watermark_at": datetime(2026, 9, 16, tzinfo=timezone.utc)}


class FakeResult:
    def __init__(self, linhas):
        self._l = linhas

    def mappings(self):
        return self

    def all(self):
        return self._l

    def one(self):
        return self._l[0] if self._l else {}


class FakeDB:
    """Responde por forma de SQL. Registra tudo que foi executado."""

    def __init__(self, classe=None, marca=None, conta=None, diario=None,
                 escopo=None, limites=None, observadas=None):
        self.executado: list[str] = []
        self.classe = classe or []
        self.marca = marca if marca is not None else self.classe
        self.conta = conta if conta is not None else self.classe
        self.diario = diario or []
        self.escopo = escopo or {"effective_date_from": date(2026, 8, 1),
                                 "effective_date_to": date(2026, 8, 31),
                                 "refreshed_at": None,
                                 "source_watermark_at": None, "linhas": 1}
        self.limites = limites or {"source_min_date": date(2026, 1, 1),
                                   "source_max_date": date(2026, 9, 15),
                                   "refreshed_at": datetime(2026, 9, 16, 22, tzinfo=timezone.utc),
                                   "source_watermark_at": datetime(2026, 9, 16, 18, tzinfo=timezone.utc),
                                   "linhas": 1648}
        self.observadas = observadas if observadas is not None else [
            {"shop_account": c, "brand": c} for c in svc.EXPECTED_ACCOUNTS]

    def execute(self, sql, params=None):
        t = str(getattr(sql, "text", sql))
        self.executado.append(t)
        if "MIN(ref_date) AS source_min_date" in t:
            return FakeResult([self.limites])
        if "effective_date_from" in t:
            return FakeResult([self.escopo])
        if "SELECT DISTINCT shop_account" in t:
            return FakeResult(self.observadas)
        if "ref_date, fbs_class" in t:
            return FakeResult(self.diario)
        if "shop_account, brand, fbs_class" in t:
            return FakeResult(self.conta)
        if "brand, fbs_class" in t:
            return FakeResult(self.marca)
        return FakeResult(self.classe)


def _bloco(db, **kw):
    kw.setdefault("last_closed_date", date(2026, 9, 15))
    kw.setdefault("agora", datetime(2026, 9, 16, 22, 30, tzinfo=timezone.utc))
    return svc.get_shopee_fbs_block(db, date(2026, 8, 1), date(2026, 8, 31), **kw)


# ---------------------------------------------------------------------------
# 1-2. Rota e flag
# ---------------------------------------------------------------------------

def test_1_rota_e_somente_get(client):
    esquema = app.openapi()["paths"][ROTA]
    assert sorted(esquema.keys()) == ["get"], "nenhum metodo mutavel"
    for metodo in ("post", "put", "patch", "delete"):
        r = getattr(client, metodo)(ROTA)
        assert r.status_code == 405, f"{metodo.upper()} deveria ser 405"


def test_2_flag_ausente_desativa_e_nao_consulta_banco(client, monkeypatch):
    """Flag desligada: 200 estruturado, sem sessao de banco."""
    monkeypatch.setattr(settings, "shopee_fbs_enabled", False)

    def _explode(*a, **k):                       # pragma: no cover
        raise AssertionError("a flag desligada NAO pode abrir sessao de banco")

    monkeypatch.setattr(svc, "get_shopee_fbs_block", _explode)
    r = client.get(ROTA)
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "unavailable"
    assert b["marketplace"] == "shopee"
    assert "unavailable_reason" in b
    # Nem sequer aparece o corpo de dados.
    assert "totals" not in b and "by_class" not in b


def test_2b_default_da_flag_e_false():
    campo = type(settings).model_fields["shopee_fbs_enabled"]
    assert campo.default is False, "a flag tem de NASCER desligada"
    assert "shopee_fbs_enabled: bool = Field(default=False)" in io.open(
        RAIZ / "app" / "config.py", encoding="utf-8").read()


@pytest.mark.parametrize("valor,esperado", [
    ("true", True), ("True", True), ("1", True),
    ("false", False), ("0", False), ("sim", None), ("yes", True),
    # String vazia NAO e' booleano valido: levanta, e portanto nao liga.
    ("", None),
])
def test_2c_somente_valor_booleano_reconhecido_ativa(valor, esperado):
    """Valor nao booleano nao pode ligar por acidente."""
    from pydantic import TypeAdapter, ValidationError
    ta = TypeAdapter(bool)
    try:
        obtido = ta.validate_python(valor)
    except ValidationError:
        obtido = None
    assert obtido is esperado


# ---------------------------------------------------------------------------
# 3-7. Validacao de janela e entrada
# ---------------------------------------------------------------------------

@pytest.fixture()
def ligada(monkeypatch):
    monkeypatch.setattr(settings, "shopee_fbs_enabled", True)


def _hoje():
    from app.deps.period import today_brt
    return today_brt()


def test_3_d0_recusado(client, ligada, monkeypatch):
    hoje = _hoje()
    monkeypatch.setattr(svc, "get_shopee_fbs_block",
                        lambda *a, **k: pytest.fail("nao pode consultar"))
    r = client.get(ROTA, params={"date_from": str(hoje - timedelta(days=5)),
                                 "date_to": str(hoje)})
    assert r.status_code == 422
    assert "fechado" in r.json()["detail"].lower()


def test_4_futuro_recusado(client, ligada):
    hoje = _hoje()
    r = client.get(ROTA, params={"date_from": str(hoje),
                                 "date_to": str(hoje + timedelta(days=10))})
    assert r.status_code == 422


def test_5_intervalo_maior_que_366_recusado(client, ligada):
    fim = _hoje() - timedelta(days=1)
    r = client.get(ROTA, params={"date_from": str(fim - timedelta(days=400)),
                                 "date_to": str(fim)})
    assert r.status_code == 422
    assert "366" in r.json()["detail"]


def test_6_date_from_maior_que_date_to_recusado(client, ligada):
    r = client.get(ROTA, params={"date_from": "2026-08-31",
                                 "date_to": "2026-08-01"})
    assert r.status_code == 422


def test_7_entrada_maliciosa_nao_e_ecoada(client, ligada):
    payloads = ["'; DROP TABLE marts.fact_shopee_fbs_daily; --",
                "<script>alert(1)</script>", "../../etc/passwd",
                "kokeshi", "OR 1=1"]
    for p in payloads:
        r = client.get(ROTA, params={"date_from": "2026-08-01",
                                     "date_to": "2026-08-31", "brands": p})
        assert r.status_code == 422, p
        corpo = r.text
        assert p not in corpo, f"entrada ecoada no corpo: {p}"
        assert "DROP" not in corpo.upper()
        assert "script" not in corpo.lower()


def test_7b_conta_fora_do_dominio_e_422_tipado(client, ligada):
    r = client.get(ROTA, params={"date_from": "2026-08-01",
                                 "date_to": "2026-08-31",
                                 "accounts": "conta_inexistente"})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert "conta_inexistente" not in d
    assert "apice" in d, "a mensagem diz o dominio esperado"


# ---------------------------------------------------------------------------
# 8-11. Dinheiro e agregacao
# ---------------------------------------------------------------------------

def test_8_dinheiro_sem_float_intermediario():
    """Nenhuma conversao a float no caminho do dinheiro."""
    assert "float(r[" not in SERVICE_CODE
    assert "float(gross_gmv" not in SERVICE_CODE
    # `_share` converte apenas no FIM, depois de dividir em Decimal.
    corpo = SERVICE_SRC.split("def _share")[1].split("\ndef ")[0]
    assert "Decimal(den)" in corpo and "Decimal(num)" in corpo
    assert corpo.index("Decimal(num)") < corpo.index("float(n / d)")
    assert "Decimal" in SCHEMA_SRC and "gross_gmv: Decimal" in SCHEMA_SRC


def test_9_shares_calculados_apos_agregacao():
    """Dois dias de pesos muito diferentes: a media dos shares diarios seria
    0,75; o share ponderado e' 0,99. O contrato exige o ponderado."""
    db = FakeDB(classe=[
        _linha("fbs", gmv="99000.00", un=99, elig=99, criados=99, canc=0),
        _linha("seller", gmv="1000.00", un=1, elig=1, criados=1, canc=0),
    ])
    b = _bloco(db)
    esperado = float(Decimal("99000.00") / Decimal("100000.00"))
    assert b["shares"]["share_fbs_gmv"] == pytest.approx(esperado)
    assert b["shares"]["share_fbs_gmv"] == pytest.approx(0.99)


def test_10_handling_ponderado_por_sample_count():
    db = FakeDB(classe=[
        _linha("fbs", h_sum=1000, h_n=1),      # media 1000
        _linha("seller", h_sum=9000, h_n=9),   # media 1000
    ])
    b = _bloco(db)
    # (1000+9000)/(1+9) = 1000 -- e nao a media das medias, que aqui coincide.
    assert b["totals"]["handling"]["seconds_avg"] == pytest.approx(1000.0)
    assert b["totals"]["handling"]["sample_count"] == 10
    assert b["totals"]["handling"]["seconds_sum"] == 10000

    # Agora pesos diferentes: media das medias seria 3000, a ponderada e' 1090.
    db2 = FakeDB(classe=[
        _linha("fbs", h_sum=100, h_n=100),     # media 1
        _linha("seller", h_sum=6000, h_n=1),   # media 6000
    ])
    b2 = _bloco(db2)
    assert b2["totals"]["handling"]["seconds_avg"] == pytest.approx(6100 / 101)
    assert b2["totals"]["handling"]["seconds_avg"] != pytest.approx(3000.5)


def test_11_denominador_zero_retorna_none():
    assert svc._share(Decimal(0), Decimal(0)) is None
    assert svc._share(Decimal(5), 0) is None
    assert svc._share(None, None) is None
    db = FakeDB(classe=[_linha("seller", gmv="0", un=0, elig=0, criados=0,
                               canc=0, h_sum=0, h_n=0)])
    b = _bloco(db)
    assert b["shares"]["share_fbs_gmv"] is None
    assert b["totals"]["handling"]["seconds_avg"] is None


# ---------------------------------------------------------------------------
# 12-14. Zero x ausencia
# ---------------------------------------------------------------------------

def test_12_apice_com_denominador_positivo_da_share_zero():
    """Vendeu e nao teve FBS: 0%, nunca None."""
    db = FakeDB(classe=[_linha("seller", gmv="275234.00", brand="apice",
                               conta="apice")])
    b = _bloco(db)
    assert b["shares"]["share_fbs_gmv"] == 0.0
    assert b["shares"]["share_fbs_gmv"] is not None
    marca = {x["brand"]: x for x in b["by_brand"]}["apice"]
    assert marca["share_fbs_gmv"] == 0.0


def test_13_kokeshi_fora_da_cobertura_nunca_zero():
    db = FakeDB(classe=[_linha()])
    b = _bloco(db)
    assert "kokeshi" in b["meta"]["brands_not_covered"]
    assert "kokeshi" not in b["meta"]["covered_brands"]
    assert all(x["brand"] != "kokeshi" for x in b["by_brand"])
    assert all(x["shop_account"] != "kokeshi" for x in b["by_account"])
    # E o XLSX nao e' consultado para completar.
    assert "order_item_snapshots" not in SERVICE_CODE
    assert "silver." not in SERVICE_CODE


def test_14_conta_ausente_aparece_em_missing_accounts():
    db = FakeDB(classe=[_linha()],
                observadas=[{"shop_account": "barbours", "brand": "barbours"}])
    b = _bloco(db)
    m = b["meta"]
    assert sorted(m["missing_accounts"]) == ["apice", "lescent", "rituaria"]
    assert m["observed_accounts"] == ["barbours"]
    assert m["unexpected_accounts"] == []
    assert any("sem linha na janela" in w for w in m["warnings"])
    # Nao vira 0%: a conta simplesmente nao esta em by_account.
    assert all(x["shop_account"] != "apice" for x in b["by_account"])


def test_14b_conta_inesperada_e_denunciada():
    db = FakeDB(classe=[_linha()],
                observadas=[{"shop_account": "conta_nova", "brand": "x"}])
    b = _bloco(db)
    assert b["meta"]["unexpected_accounts"] == ["conta_nova"]
    assert any("FORA da allowlist" in w for w in b["meta"]["warnings"])


# ---------------------------------------------------------------------------
# 15-19. Classes e semantica financeira
# ---------------------------------------------------------------------------

def test_15_classe_inesperada_falha_fechada():
    db = FakeDB(classe=[_linha(), _linha(classe="cross_docking")])
    with pytest.raises(svc.ShopeeFbsContractError):
        _bloco(db)


def test_15b_dominio_declarado_e_fechado():
    assert svc.VALID_CLASSES == ("fbs", "seller")
    assert "unknown" not in SCHEMA_SRC.split("FbsClass = ")[1].split("\n")[0]


def test_16_fbs_mais_seller_fecha_os_totais():
    db = FakeDB(classe=[
        _linha("fbs", gmv="700.00", un=7, elig=7, criados=8, canc=1),
        _linha("seller", gmv="300.00", un=3, elig=3, criados=4, canc=1),
    ])
    b = _bloco(db)
    bc = {c["fbs_class"]: c for c in b["by_class"]}
    assert Decimal(bc["fbs"]["gross_gmv"]) + Decimal(bc["seller"]["gross_gmv"]) \
        == Decimal(b["totals"]["gross_gmv"])
    assert bc["fbs"]["eligible_orders"] + bc["seller"]["eligible_orders"] \
        == b["totals"]["eligible_orders"]
    assert bc["fbs"]["gross_units"] + bc["seller"]["gross_units"] \
        == b["totals"]["gross_units"]


@pytest.mark.parametrize("campo,gmv_campo", [
    ("to_return_orders", "to_return_gmv"),
    ("unpaid_orders", "unpaid_gmv"),
])
def test_17_18_to_return_e_unpaid_incluidos_e_separados(campo, gmv_campo):
    kw = {"tr": 3, "tr_gmv": "150.00"} if "to_return" in campo \
        else {"up": 2, "up_gmv": "80.00"}
    db = FakeDB(classe=[_linha(gmv="1000.00", **kw)])
    b = _bloco(db)
    t = b["totals"]
    # Publicado em coluna propria...
    assert t[campo] > 0 and Decimal(t[gmv_campo]) > 0
    # ...e AINDA DENTRO do bruto: o bruto nao foi reduzido.
    assert Decimal(t["gross_gmv"]) == Decimal("1000.00")
    # E o contrato diz isso em texto.
    assert "INCLUI to_return e unpaid" in b["meta"]["gmv_definition"]


def test_19_cancelled_excluido_do_gmv_mas_no_denominador():
    db = FakeDB(classe=[_linha(gmv="1000.00", criados=100, elig=90, canc=10)])
    b = _bloco(db)
    # GMV vem so' dos elegiveis (o fake ja' entrega o valor agregado).
    assert Decimal(b["totals"]["gross_gmv"]) == Decimal("1000.00")
    # Taxa usa created, nao eligible.
    assert b["rates"]["cancellation_rate"] == pytest.approx(10 / 100)
    assert b["rates"]["cancellation_rate"] != pytest.approx(10 / 90)


def test_19b_gmv_nunca_e_chamado_apenas_de_receita():
    db = FakeDB(classe=[_linha()])
    b = _bloco(db)
    d = b["meta"]["gmv_definition"].lower()
    assert "bruto" in d
    assert "nao e' receita liquida nem realizada" in d
    # Nenhum campo do payload se chama apenas "revenue"/"receita".
    campos = str(list(b["totals"].keys()) + list(b["shares"].keys()))
    assert "revenue" not in campos.lower()


# ---------------------------------------------------------------------------
# 20. PII e seguranca
# ---------------------------------------------------------------------------

def test_20_zero_pii_no_schema_e_no_payload():
    proibidos = ("order_sn", "buyer_username", "buyer_user_id", "buyer_cpf",
                 "cpf", "recipient_address", "dropshipper", "external_seller_id",
                 "raw_payload", "item_sku", "model_sku")
    for p in proibidos:
        assert p not in SCHEMA_SRC, f"{p} nao pode estar no contrato"
        assert p not in SERVICE_CODE, f"{p} nao pode ser lido pelo servico"
    db = FakeDB(classe=[_linha()])
    b = _bloco(db)
    import json
    txt = json.dumps(b, default=str).lower()
    for p in proibidos:
        assert p not in txt


def test_20b_allowlist_de_colunas_sem_select_estrela():
    # No CODIGO executavel: a docstring cita `SELECT *` justamente para
    # proibi-lo, e procurar no texto cru reprovaria a propria proibicao.
    assert "SELECT *" not in SERVICE_CODE.upper()
    # Toda consulta nomeia colunas e filtra pela allowlist de contas.
    for nome in ("SQL_POR_CLASSE", "SQL_POR_MARCA", "SQL_POR_CONTA", "SQL_DIARIO"):
        sql = str(getattr(svc, nome))
        assert "shop_account = ANY(:allowlist)" in sql, nome


def test_20c_filtros_sao_parametrizados():
    for nome in ("SQL_POR_CLASSE", "SQL_POR_MARCA", "SQL_POR_CONTA",
                 "SQL_DIARIO", "SQL_ESCOPO"):
        sql = str(getattr(svc, nome))
        assert "= ANY(:brands)" in sql and "= ANY(:accounts)" in sql, nome
    # Nenhuma f-string injeta valor: so' o nome da tabela e as medidas.
    assert "{brands}" not in SERVICE_SRC and "{accounts}" not in SERVICE_SRC


def test_20d_erro_nao_vaza_sql_nem_credencial():
    assert "ERRO_SHOPEE_FBS_INDISPONIVEL" in ROUTER_SRC
    bloco = ROUTER_SRC.split("except shopee_fbs_svc.ShopeeFbsUnavailable")[1][:220]
    assert "str(exc)" not in bloco and "{exc}" not in bloco


# ---------------------------------------------------------------------------
# 21-22. Frescor
# ---------------------------------------------------------------------------

def test_21_duas_auditorias_da_mesma_execucao_nao_sao_duas_cargas():
    """O frescor sai dos METADADOS DA FATO, nunca de contar linhas de auditoria.

    O pipeline grava DOIS rotulos por execucao (`shopee_fbs_daily` e
    `..._full`, ids 316 e 317, mesmo started_at e finished_at). Contar
    auditorias diria "duas cargas" onde houve uma.
    """
    assert "source_sync_run" not in SERVICE_CODE, (
        "o frescor nao pode ser derivado da tabela de auditoria")
    assert "MAX(ingested_at)" in str(svc.SQL_LIMITES_FATO)
    assert "MAX(source_max_ingested_at)" in str(svc.SQL_LIMITES_FATO)


def test_22_source_max_date_antiga_nao_e_mascarada_por_loaded_at_recente():
    """Republicar o mesmo periodo move `refreshed_at` sem mover a serie."""
    agora = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    limites = {"source_min_date": date(2026, 1, 1),
               "source_max_date": date(2026, 9, 10),      # serie ATRASADA
               "refreshed_at": agora - timedelta(minutes=5),   # publicacao NOVA
               "source_watermark_at": agora - timedelta(minutes=10),
               "linhas": 1648}
    f = svc._freshness(limites, agora, date(2026, 9, 19))
    assert f["freshness_status"] == "stale", (
        "publicacao recente nao pode mascarar serie atrasada")
    assert f["closed_days_behind"] == 9
    assert f["snapshot_age_hours"] < 1
    assert f["source_max_date"] == date(2026, 9, 10)


def test_22b_frescor_expoe_os_tres_relogios():
    db = FakeDB(classe=[_linha()])
    f = _bloco(db)["meta"]["freshness"]
    for campo in ("source_watermark_at", "refreshed_at", "source_max_date",
                  "source_age_hours", "snapshot_age_hours",
                  "closed_days_behind", "load_mode", "no_automation"):
        assert campo in f
    assert f["load_mode"] == "manual_snapshot"
    assert f["no_automation"] is True


def test_22c_tabela_vazia_e_never_loaded_nao_fresh():
    f = svc._freshness({"linhas": 0}, datetime.now(timezone.utc), date(2026, 9, 15))
    assert f["freshness_status"] == "never_loaded"
    assert f["refreshed_at"] is None


# ---------------------------------------------------------------------------
# 23. Tendencia diaria
# ---------------------------------------------------------------------------

def test_23_tendencia_diaria_nao_publica_nem_promedia_share():
    db = FakeDB(
        classe=[_linha("fbs", gmv="700.00"), _linha("seller", gmv="300.00")],
        diario=[{"ref_date": date(2026, 8, 1), "fbs_class": "fbs",
                 "gross_gmv": Decimal("700.00"), "gross_units": 7,
                 "eligible_orders": 7, "created_orders": 8,
                 "cancelled_orders": 1},
                {"ref_date": date(2026, 8, 1), "fbs_class": "seller",
                 "gross_gmv": Decimal("300.00"), "gross_units": 3,
                 "eligible_orders": 3, "created_orders": 4,
                 "cancelled_orders": 1}])
    b = _bloco(db)
    for ponto in b["daily"]:
        assert not any("share" in k for k in ponto), (
            "o daily publica apenas medidas ADITIVAS")
        assert not any("rate" in k for k in ponto)
    # O schema tambem nao admite share por dia.
    bloco_daily = SCHEMA_SRC.split("class DailyPoint")[1].split("class ")[0]
    assert "share" not in bloco_daily.split('"""')[2]


# ---------------------------------------------------------------------------
# 24. Compatibilidade do Full ML
# ---------------------------------------------------------------------------

def test_24_endpoint_full_ml_intacto():
    paths = app.openapi()["paths"]
    assert "/api/v1/performance/ml-fulfillment" in paths
    assert sorted(paths["/api/v1/performance/ml-fulfillment"].keys()) == ["get"]
    sch = app.openapi()["components"]["schemas"]
    assert "MLFulfillmentResponse" in sch
    # 16 campos do contrato ML seguem intactos.
    assert len(sch["MLFulfillmentResponse"]["properties"]) == 16


def test_24b_nenhum_schema_existente_foi_alterado():
    """O bloco e' ADITIVO: os schemas do ML nao sao tocados."""
    ml = io.open(RAIZ / "app" / "schemas" / "ml_fulfillment.py",
                 encoding="utf-8").read()
    assert "shopee" not in ml.lower()
    assert "fbs" not in ml.lower()


# ---------------------------------------------------------------------------
# Politica de data
# ---------------------------------------------------------------------------

def test_contrato_de_data_declarado_no_payload():
    db = FakeDB(classe=[_linha()])
    m = _bloco(db)["meta"]
    assert m["date_policy"] == "closed_day"
    assert m["d0_materialized"] is False
    assert m["last_closed_date"] == date(2026, 9, 15)
    assert m["requested_date_from"] == date(2026, 8, 1)
    assert m["effective_date_from"] == date(2026, 8, 1)
    assert m["scope_label"] == svc.SCOPE_LABEL
    assert "total" not in m["scope_label"].lower()


def test_servico_le_apenas_a_fato_publicada():
    assert svc.FACT_TABLE == "marts.fact_shopee_fbs_daily"
    for proibida in ("silver.", "raw.", "gold.", "stg_shopee"):
        assert proibida not in SERVICE_CODE, proibida
