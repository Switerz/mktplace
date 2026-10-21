"""Gate TORRE-B2B-OCTAPRICE — contrato da aba "B2B / Revendedores".

Sem rede e sem banco: a Torre B2B e' substituida por uma FONTE FALSA que
cumpre o mesmo protocolo do adapter real. O adapter real (SQL) e' verificado
por inspecao — o que ele le, como chama as RPCs, e o que ele NUNCA faz.

O que estes testes existem para impedir:
  1. ausencia de dado virar zero (flag, credencial, fonte, dia sem relatorio);
  2. a Amazon voltar aos KPIs (por omissao de filtro ou allowlist vazia);
  3. dado de cliente (nome, vendedor, CNPJ, e-mail, telefone) sair por aqui;
  4. credencial vazar em resposta ou log.
"""
from __future__ import annotations

import ast
import io
import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.config import Settings, settings
from app.main import app
from app.routers import b2b_revenda as rota
from app.schemas import b2b_revenda as schemas
from app.services import b2b_revenda_fonte as fonte_mod
from app.services import b2b_revenda_service as svc
from app.services.b2b_revenda_fonte import (
    CargaFonte,
    CoberturaFonte,
    FonteComCache,
    FonteIndisponivel,
    MarcaCanalFonte,
    ResumoFonte,
    SeveridadeFonte,
    SkuFonte,
)

BASE = "/api/v1/b2b-revenda"
ROTAS = ["/frescor", "/resumo", "/canais-marcas", "/skus"]
RAIZ = Path(__file__).resolve().parents[1]
FONTE_SRC = io.open(RAIZ / "app" / "services" / "b2b_revenda_fonte.py", encoding="utf-8").read()
ROTA_SRC = io.open(RAIZ / "app" / "routers" / "b2b_revenda.py", encoding="utf-8").read()
SVC_SRC = io.open(RAIZ / "app" / "services" / "b2b_revenda_service.py", encoding="utf-8").read()

HOJE = date(2026, 10, 21)
BRT = timezone(timedelta(hours=-3))
#: 14h BRT: depois do corte das 13h, o relatorio de HOJE ja' e' esperado.
AGORA = datetime(2026, 10, 21, 14, 0, tzinfo=BRT)
LIDO = datetime(2026, 10, 21, 16, 55, tzinfo=timezone.utc)


def _codigo(fonte: str) -> str:
    """Codigo sem docstrings: as proibicoes sao sobre o que o codigo FAZ."""
    arv = ast.parse(fonte)
    for no in ast.walk(arv):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = no.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                b[0].value.value = ""
    return ast.unparse(arv)


# ---------------------------------------------------------------------------
# Fonte falsa
# ---------------------------------------------------------------------------
class FonteFalsa:
    """Cumpre `FonteRevenda`. Registra cada chamada e o recorte recebido."""

    def __init__(self, **kw):
        self.carga = kw.get("carga", CargaFonte(
            data_ref=HOJE,
            coleta_inicio=datetime(2026, 10, 20, 19, 3, tzinfo=timezone.utc),
            coleta_fim=datetime(2026, 10, 20, 19, 21, tzinfo=timezone.utc),
            importado_inicio=datetime(2026, 10, 21, 11, 41, tzinfo=timezone.utc),
            importado_fim=datetime(2026, 10, 21, 11, 42, tzinfo=timezone.utc),
            linhas=1530,
            canais=("Amazon", "Mercadolivre", "Shopee"),
        ))
        self._datas = kw.get("datas", [HOJE, HOJE - timedelta(days=1), HOJE - timedelta(days=2)])
        self._resumo = kw.get("resumo", ResumoFonte(
            data_ref=HOJE, total=1437, violando=312, pct=Decimal("21.7"),
            sellers_violando=88, produtos=45, desvio_medio=Decimal("-0.087"),
        ))
        self._sev = kw.get("sev", SeveridadeFonte(leve=150, media=120, grave=42, com_buybox=400, viol_com_buybox=61))
        self._cob = kw.get("cob", CoberturaFonte(
            sellers_violando=88, sellers_violando_identificados=27,
            anuncios_violando=312, anuncios_violando_identificados=140,
        ))
        self._mc = kw.get("mc", [
            MarcaCanalFonte("AP", "Mercadolivre", 500, 100, Decimal("-0.08"), Decimal("59.90")),
            MarcaCanalFonte("BB", "Shopee", 300, 0, None, Decimal("39.90")),
            MarcaCanalFonte(None, "Shopee", 7, 1, Decimal("-0.20"), Decimal("10.00")),
        ])
        self._skus = kw.get("skus", [
            SkuFonte("SKU-1", "KS", "Anuncio exemplo", 40, 25, Decimal("63"), Decimal("-0.12")),
        ])
        self.falhar: dict[str, str] = {}
        self.chamadas: list[tuple] = []

    def _reg(self, nome, *args):
        self.chamadas.append((nome, *args))
        if nome in self.falhar:
            raise FonteIndisponivel(self.falhar[nome])

    def datas(self):
        self._reg("datas")
        return list(self._datas)

    def carga_mais_recente(self):
        self._reg("carga")
        return self.carga

    def resumo(self, f):
        self._reg("resumo", f)
        return self._resumo

    def severidade(self, f):
        self._reg("severidade", f)
        return self._sev

    def cobertura(self, f):
        self._reg("cobertura", f)
        return self._cob

    def marca_canal(self, f):
        self._reg("marca_canal", f)
        return list(self._mc)

    def top_skus(self, f, limite):
        self._reg("top_skus", f, limite)
        return list(self._skus)

    def recortes(self):
        return [c[1] for c in self.chamadas if len(c) > 1]


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def ligado(monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_enabled", True)
    monkeypatch.setattr(settings, "b2b_revenda_canais_kpi", "Mercadolivre,Shopee")
    monkeypatch.setattr(rota, "_agora", lambda: AGORA)


@pytest.fixture()
def falsa():
    return FonteFalsa()


@pytest.fixture()
def client(falsa):
    cache = FonteComCache(falsa, 1200, monotonico=Relogio(), agora=lambda: LIDO)
    app.dependency_overrides[rota.provedor_fonte] = lambda: (lambda: cache)
    yield TestClient(app)
    app.dependency_overrides.pop(rota.provedor_fonte, None)


# ---------------------------------------------------------------------------
# 1. Flag desligada: nada e' tocado
# ---------------------------------------------------------------------------
def test_flag_nasce_desligada():
    assert Settings.model_fields["b2b_revenda_enabled"].default is False


@pytest.mark.parametrize("caminho", ROTAS)
def test_flag_desligada_responde_indisponivel_sem_criar_fonte(caminho, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_enabled", False)

    def fabrica_proibida():
        raise AssertionError("a fonte nao pode ser criada com a flag desligada")

    app.dependency_overrides[rota.provedor_fonte] = lambda: fabrica_proibida
    try:
        r = TestClient(app).get(BASE + caminho)
    finally:
        app.dependency_overrides.pop(rota.provedor_fonte, None)
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"
    assert r.json()["motivo_tecnico"] == "feature_flag_desligada"


# ---------------------------------------------------------------------------
# 2. Credencial: ausente e invalida viram `unavailable`, sem vazar valor
# ---------------------------------------------------------------------------
@pytest.fixture()
def singleton_limpo(monkeypatch):
    monkeypatch.setattr(fonte_mod, "_singleton", None)
    yield
    monkeypatch.setattr(fonte_mod, "_singleton", None)


def test_credencial_ausente_e_indisponivel(ligado, singleton_limpo, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_database_url", "")
    r = TestClient(app).get(BASE + "/resumo")
    assert r.status_code == 200
    assert r.json()["motivo_tecnico"] == "credencial_ausente"
    assert fonte_mod._singleton is None, "falha de credencial nao pode ficar guardada"


@pytest.mark.parametrize("dsn", [
    "mysql://usuario:SENHA-SECRETA-XYZ@host.exemplo/db",
    "isto nao e uma url SENHA-SECRETA-XYZ",
])
def test_credencial_invalida_nao_vaza_dsn(dsn, ligado, singleton_limpo, monkeypatch, caplog):
    monkeypatch.setattr(settings, "b2b_revenda_database_url", dsn)
    caplog.set_level(logging.DEBUG)
    r = TestClient(app).get(BASE + "/resumo")
    assert r.json()["motivo_tecnico"] == "credencial_invalida"
    assert "SENHA-SECRETA-XYZ" not in r.text
    assert "SENHA-SECRETA-XYZ" not in caplog.text


def test_criar_engine_nao_conecta():
    # Host inexistente: se `criar_engine` conectasse, isto falharia/penduraria.
    eng = fonte_mod.criar_engine(
        "postgresql://leitor:x@host-que-nao-existe.invalid:5432/db",
        sslmode="require", connect_timeout=1,
    )
    assert eng.url.drivername == "postgresql+psycopg2"


# ---------------------------------------------------------------------------
# 3. Escopo: Amazon fora dos KPIs, sempre
# ---------------------------------------------------------------------------
def test_canais_padrao_excluem_amazon():
    assert "Amazon" not in Settings().b2b_revenda_canais_kpi_list


@pytest.mark.parametrize("caminho", ["/resumo", "/canais-marcas", "/skus"])
def test_sem_filtro_de_canal_envia_a_allowlist_e_nunca_nulo(caminho, ligado, client, falsa):
    assert client.get(BASE + caminho).status_code == 200
    recortes = falsa.recortes()
    assert recortes, "a fonte deveria ter sido consultada"
    for f in recortes:
        assert f.canais == ("Mercadolivre", "Shopee")
        assert "Amazon" not in f.canais


@pytest.mark.parametrize("canal", ["Amazon", "Magalu", "<script>alert(1)</script>"])
def test_canal_fora_do_escopo_e_422_sem_eco(canal, ligado, client, falsa):
    r = client.get(BASE + "/resumo", params={"canais": canal})
    assert r.status_code == 422
    assert r.json()["detail"] == svc.MSG_CANAL_FORA
    assert canal not in r.text
    assert falsa.chamadas == [], "recorte invalido nao pode chegar a fonte"


def test_allowlist_vazia_nao_libera_amazon(ligado, client, falsa, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_canais_kpi", " , ")
    r = client.get(BASE + "/resumo")
    assert r.json()["status"] == "unavailable"
    assert r.json()["motivo_tecnico"] == "configuracao_invalida"
    assert not any(c[0] == "resumo" for c in falsa.chamadas)


def test_escopo_lista_canais_excluidos_com_motivo(ligado, client, falsa):
    falsa.carga = replace(falsa.carga, canais=("Amazon", "Magalu", "Mercadolivre", "Shopee"))
    escopo = client.get(BASE + "/frescor").json()["escopo"]
    assert escopo["piso"] == "pma_octaprice"
    assert "PDV" in escopo["aviso_piso"]
    assert escopo["canais_nos_kpis"] == ["Mercadolivre", "Shopee"]
    motivos = {c["canal"]: c["motivo"] for c in escopo["canais_excluidos"]}
    assert motivos == {"Amazon": svc.MOTIVO_AMAZON, "Magalu": svc.MOTIVO_FORA_ALLOWLIST}


def test_filtro_de_canal_valido_e_repassado(ligado, client, falsa):
    assert client.get(BASE + "/resumo", params={"canais": "Shopee"}).status_code == 200
    assert {f.canais for f in falsa.recortes()} == {("Shopee",)}


# ---------------------------------------------------------------------------
# 4. Indisponivel nunca vira zero
# ---------------------------------------------------------------------------
def test_fonte_sem_carga_e_indisponivel(ligado, client, falsa):
    falsa.carga = CargaFonte(None, None, None, None, None, 0, ())
    for caminho in ROTAS:
        corpo = client.get(BASE + caminho).json()
        assert corpo["status"] == "unavailable"
        assert corpo["motivo_tecnico"] == "fonte_sem_dados"
    assert not any(c[0] in ("resumo", "marca_canal", "top_skus") for c in falsa.chamadas)


def test_dia_sem_relatorio_nao_consulta_rpc_e_traz_frescor(ligado, client, falsa):
    corpo = client.get(BASE + "/resumo", params={"data": "2026-01-01"}).json()
    assert corpo["status"] == "unavailable"
    assert corpo["motivo_tecnico"] == "sem_relatorio_na_data"
    assert corpo["frescor"]["data_ref_mais_recente"] == "2026-10-21"
    # A RPC devolveria ZERO para um dia sem relatorio. Ela nao pode ser chamada.
    assert not any(c[0] == "resumo" for c in falsa.chamadas)


@pytest.mark.parametrize("motivo", [
    "fonte_inacessivel", "permissao_negada", "contrato_da_fonte_quebrado", "fonte_lenta",
])
def test_falha_da_fonte_e_indisponivel_com_motivo(motivo, ligado, client, falsa):
    falsa.falhar["resumo"] = motivo
    r = client.get(BASE + "/resumo")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"
    assert r.json()["motivo_tecnico"] == motivo
    assert "kpis" not in r.json()


def test_motivos_do_adapter_cabem_no_contrato():
    do_adapter = set(get_args(fonte_mod.MotivoIndisponivel))
    do_schema = set(get_args(schemas.MotivoTecnico))
    assert do_adapter <= do_schema
    assert do_schema <= set(svc.MENSAGENS)


# ---------------------------------------------------------------------------
# 5. Frescor
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("agora,data_ref,situacao,atraso", [
    (datetime(2026, 10, 21, 12, 59, tzinfo=BRT), date(2026, 10, 20), "em_dia", 0),
    (datetime(2026, 10, 21, 13, 0, tzinfo=BRT), date(2026, 10, 20), "atrasado", 1),
    (datetime(2026, 10, 21, 13, 0, tzinfo=BRT), date(2026, 10, 21), "em_dia", 0),
    (datetime(2026, 10, 21, 9, 0, tzinfo=BRT), date(2026, 10, 17), "atrasado", 3),
    # 02h UTC de 22/10 ainda e' 21/10 23h em Sao Paulo.
    (datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc), date(2026, 10, 21), "em_dia", 0),
])
def test_situacao_do_frescor_corte_13h_brt(agora, data_ref, situacao, atraso):
    carga = CargaFonte(data_ref, None, None, None, None, 10, ())
    fr = svc.montar_frescor(carga, [data_ref], agora=agora, consultado_em=LIDO)
    assert (fr.situacao, fr.dias_de_atraso) == (situacao, atraso)


def test_frescor_expoe_coleta_importacao_e_consulta(ligado, client):
    fr = client.get(BASE + "/frescor").json()["frescor"]
    assert fr["data_ref_mais_recente"] == "2026-10-21"
    assert fr["coleta_fim"].startswith("2026-10-20T19:21")
    assert fr["importado_fim"].startswith("2026-10-21T11:42")
    assert fr["consultado_em"].startswith("2026-10-21T16:55")
    assert fr["situacao"] == "em_dia"
    assert fr["datas_disponiveis"][0] == "2026-10-21"


def test_consulta_historica_e_sinalizada(ligado, client):
    corpo = client.get(BASE + "/resumo", params={"data": "2026-10-19"}).json()
    assert corpo["status"] == "ok"
    assert corpo["data_ref"] == "2026-10-19"
    assert corpo["consulta_historica"] is True
    assert corpo["frescor"]["data_ref_mais_recente"] == "2026-10-21"


# ---------------------------------------------------------------------------
# 6. Numeros: repassados da fonte, sem regra nova
# ---------------------------------------------------------------------------
def test_resumo_repassa_a_fonte(ligado, client):
    corpo = client.get(BASE + "/resumo").json()
    k = corpo["kpis"]
    assert (k["anuncios"], k["anuncios_violando"], k["sellers_violando"], k["skus_monitorados"]) == (1437, 312, 88, 45)
    assert Decimal(k["pct_violacao"]) == Decimal("21.7")
    assert Decimal(k["desvio_medio_pma"]) == Decimal("-0.087")
    assert k["buy_box"] == {"anuncios_com_buy_box": 400, "violando_com_buy_box": 61}
    assert k["severidade"] == {"leve": 150, "media": 120, "grave": 42}
    cob = corpo["cobertura_identificacao"]
    assert Decimal(cob["pct_sellers"]) == Decimal("30.7")
    assert Decimal(cob["pct_anuncios"]) == Decimal("44.9")
    assert cob["consistente"] is True


def test_cobertura_divergente_e_marcada(ligado, client, falsa):
    falsa._cob = replace(falsa._cob, sellers_violando=80)
    assert client.get(BASE + "/resumo").json()["cobertura_identificacao"]["consistente"] is False


def test_cobertura_sem_violacao_nao_inventa_percentual(ligado, client, falsa):
    falsa._cob = CoberturaFonte(0, 0, 0, 0)
    falsa._resumo = replace(falsa._resumo, violando=0, sellers_violando=0)
    cob = client.get(BASE + "/resumo").json()["cobertura_identificacao"]
    assert cob["pct_sellers"] is None and cob["pct_anuncios"] is None


def test_canais_marcas_rotula_e_calcula_percentual(ligado, client):
    linhas = client.get(BASE + "/canais-marcas").json()["linhas"]
    assert [l["marca"] for l in linhas] == ["Ápice", "Barbours", svc.ROTULO_SEM_MARCA]
    assert Decimal(linhas[0]["pct_violacao"]) == Decimal("20.0")
    assert Decimal(linhas[1]["pct_violacao"]) == Decimal("0.0")
    assert linhas[1]["desvio_medio_pma"] is None


def test_skus_percentual_com_uma_casa_e_limite(ligado, client, falsa):
    corpo = client.get(BASE + "/skus", params={"limite": 5}).json()
    assert corpo["limite"] == 5
    assert Decimal(corpo["linhas"][0]["pct_violacao"]) == Decimal("62.5")
    assert [c for c in falsa.chamadas if c[0] == "top_skus"][0][2] == 5


@pytest.mark.parametrize("limite", [0, 51])
def test_limite_de_skus_fora_da_faixa_e_422(limite, ligado, client):
    assert client.get(BASE + "/skus", params={"limite": limite}).status_code == 422


@pytest.mark.parametrize("params,msg", [
    ({"severidades": "critica"}, svc.MSG_SEVERIDADE_INVALIDA),
    ({"marcas": "APICE"}, svc.MSG_MARCA_INVALIDA),
    ({"ufs": "XX"}, svc.MSG_UF_INVALIDA),
    ({"skus": "a\x01b"}, svc.MSG_SKU_INVALIDO),
    ({"skus": "x" * 81}, svc.MSG_SKU_INVALIDO),
])
def test_recorte_invalido_e_422_constante(params, msg, ligado, client, falsa):
    r = client.get(BASE + "/resumo", params=params)
    assert r.status_code == 422
    assert r.json()["detail"] == msg
    assert falsa.chamadas == []


def test_lista_de_skus_longa_e_422(ligado, client):
    params = [("skus", f"S{i}") for i in range(svc.MAX_SKUS_FILTRO + 1)]
    assert client.get(BASE + "/resumo", params=params).status_code == 422


def test_recorte_normalizado_vira_mesma_chave(ligado, client, falsa):
    client.get(BASE + "/resumo", params=[("marcas", "bb"), ("marcas", "AP"), ("marcas", "AP")])
    client.get(BASE + "/resumo", params=[("marcas", "AP"), ("marcas", "BB")])
    assert sum(1 for c in falsa.chamadas if c[0] == "resumo") == 1
    assert falsa.recortes()[0].marcas == ("AP", "BB")


# ---------------------------------------------------------------------------
# 7. Cache
# ---------------------------------------------------------------------------
def test_ttl_padrao_e_faixa_15_a_30_min():
    assert Settings.model_fields["b2b_revenda_cache_ttl_seconds"].default == 1200
    for invalido in (899, 1801):
        with pytest.raises(ValidationError):
            Settings(b2b_revenda_cache_ttl_seconds=invalido)


def _f(**kw):
    base = dict(data=HOJE, marcas=(), canais=("Shopee",), severidades=(), skus=(), ufs=())
    base.update(kw)
    return fonte_mod.FiltrosFonte(**base)


def test_cache_serve_dentro_do_ttl_e_rele_depois():
    falsa, rel = FonteFalsa(), Relogio()
    cache = FonteComCache(falsa, 1200, monotonico=rel, agora=lambda: LIDO)
    cache.resumo(_f())
    rel.t += 1199
    cache.resumo(_f())
    assert sum(1 for c in falsa.chamadas if c[0] == "resumo") == 1
    rel.t += 2
    cache.resumo(_f())
    assert sum(1 for c in falsa.chamadas if c[0] == "resumo") == 2


def test_cache_separa_recortes():
    falsa = FonteFalsa()
    cache = FonteComCache(falsa, 1200, monotonico=Relogio(), agora=lambda: LIDO)
    cache.resumo(_f())
    cache.resumo(_f(marcas=("AP",)))
    assert sum(1 for c in falsa.chamadas if c[0] == "resumo") == 2


def test_falha_nao_e_cacheada():
    falsa = FonteFalsa()
    cache = FonteComCache(falsa, 1200, monotonico=Relogio(), agora=lambda: LIDO)
    falsa.falhar["resumo"] = "fonte_inacessivel"
    with pytest.raises(FonteIndisponivel):
        cache.resumo(_f())
    falsa.falhar.clear()
    valor, _ = cache.resumo(_f())
    assert valor.total == 1437


def test_cache_tem_teto_de_entradas():
    falsa, rel = FonteFalsa(), Relogio()
    cache = FonteComCache(falsa, 1200, max_entradas=3, monotonico=rel, agora=lambda: LIDO)
    for i in range(5):
        rel.t += 1
        cache.resumo(_f(skus=(f"S{i}",)))
    assert len(cache._itens) == 3


def test_consultado_em_e_o_da_leitura_mais_antiga(ligado, falsa):
    rel, instantes = Relogio(), iter([
        datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc),   # carga
        datetime(2026, 10, 21, 16, 1, tzinfo=timezone.utc),   # datas
        datetime(2026, 10, 21, 16, 30, tzinfo=timezone.utc),  # resumo
        datetime(2026, 10, 21, 16, 30, tzinfo=timezone.utc),  # severidade
        datetime(2026, 10, 21, 16, 30, tzinfo=timezone.utc),  # cobertura
    ])
    cache = FonteComCache(falsa, 1200, monotonico=rel, agora=lambda: next(instantes))
    app.dependency_overrides[rota.provedor_fonte] = lambda: (lambda: cache)
    try:
        corpo = TestClient(app).get(BASE + "/resumo").json()
    finally:
        app.dependency_overrides.pop(rota.provedor_fonte, None)
    assert corpo["frescor"]["consultado_em"].startswith("2026-10-21T16:00")


# ---------------------------------------------------------------------------
# 8. Sem dado de cliente
# ---------------------------------------------------------------------------
_PROIBIDOS = re.compile(r"cliente|vendedor|cnpj|email|telefone|phone|razao|seller_n", re.I)


def _campos(modelo: type[BaseModel], vistos=None) -> set[str]:
    vistos = vistos or set()
    if modelo in vistos:
        return set()
    vistos.add(modelo)
    nomes = set()
    for nome, campo in modelo.model_fields.items():
        nomes.add(nome)
        for tipo in [campo.annotation, *get_args(campo.annotation)]:
            for t in [tipo, *get_args(tipo)]:
                if isinstance(t, type) and issubclass(t, BaseModel):
                    nomes |= _campos(t, vistos)
    return nomes


@pytest.mark.parametrize("modelo", [
    schemas.FrescorResponse, schemas.ResumoResponse, schemas.CanaisMarcasResponse,
    schemas.SkusResponse, schemas.B2BRevendaUnavailableResponse,
])
def test_contrato_nao_tem_campo_de_cliente(modelo):
    proibidos = {n for n in _campos(modelo) if _PROIBIDOS.search(n)}
    assert proibidos == set()


def test_rotas_nao_aceitam_filtro_de_cliente_ou_vendedor():
    params = set(re.findall(r"^\s+(\w+): Optional\[list\[str\]\]", ROTA_SRC, re.M))
    assert params == {"marcas", "canais", "severidades", "skus", "ufs"}


def test_sql_nao_le_dado_de_cliente_nem_escreve():
    sqls = {n: v for n, v in vars(fonte_mod).items() if n.startswith("SQL_")}
    assert set(sqls) == {
        "SQL_DATAS", "SQL_CARGA", "SQL_RESUMO", "SQL_SEVERIDADE",
        "SQL_COBERTURA", "SQL_MARCA_CANAL", "SQL_TOP_SKUS",
    }
    for nome, sql in sqls.items():
        baixo = sql.lower()
        for proibido in ("email", "telefone", "cliente_nome", "razao_social", "vendedor",
                         "cnpj", "monitor_seller_cliente", "p_vendedores", "p_cnpjs"):
            assert proibido not in baixo, f"{nome} menciona {proibido}"
        assert not re.search(r"\b(insert|update|delete|create|alter|drop|grant|truncate)\b", baixo), nome


def test_rpcs_chamadas_por_nome_de_parametro():
    for nome in ("SQL_RESUMO", "SQL_SEVERIDADE", "SQL_COBERTURA", "SQL_MARCA_CANAL", "SQL_TOP_SKUS"):
        sql = getattr(fonte_mod, nome)
        assert "p_data => " in sql and "p_marcas => " in sql and "p_canais => " in sql, nome


def test_cobertura_agrega_no_banco_e_nao_traz_nome_de_seller():
    sql = fonte_mod.SQL_COBERTURA.lower()
    assert "seller_norm" not in sql and "seller_nome" not in sql
    assert sql.strip().startswith("select count(*)")


# ---------------------------------------------------------------------------
# 9. Adapter real: transacao read-only e falha sanitizada
# ---------------------------------------------------------------------------
class _Orig(Exception):
    def __init__(self, pgcode):
        super().__init__("detalhe com SENHA-SECRETA-XYZ")
        self.pgcode = pgcode


def _dbapi(pgcode):
    from sqlalchemy.exc import ProgrammingError
    return ProgrammingError("SELECT ...", {}, _Orig(pgcode))


@pytest.mark.parametrize("pgcode,motivo", [
    ("42501", "permissao_negada"),
    ("57014", "fonte_lenta"),
    ("42883", "contrato_da_fonte_quebrado"),
    ("42703", "contrato_da_fonte_quebrado"),
    ("XX000", "falha_desconhecida"),
])
def test_classificacao_por_sqlstate(pgcode, motivo):
    assert fonte_mod.classificar_falha(_dbapi(pgcode)) == motivo


def test_operational_error_e_fonte_inacessivel():
    from sqlalchemy.exc import OperationalError
    exc = OperationalError("connect", {}, Exception("could not connect SENHA-SECRETA-XYZ"))
    assert fonte_mod.classificar_falha(exc) == "fonte_inacessivel"


class _ConnQuebrada:
    def __init__(self, exc):
        self.exc = exc

    def connect(self):
        raise self.exc


def test_falha_do_driver_nao_vaza_mensagem_nem_encadeia(caplog):
    caplog.set_level(logging.DEBUG)
    from sqlalchemy.exc import OperationalError
    exc = OperationalError("connect", {}, Exception("password=SENHA-SECRETA-XYZ host=x"))
    fonte = fonte_mod.FontePostgres(_ConnQuebrada(exc), 10000)
    with pytest.raises(FonteIndisponivel) as info:
        fonte.resumo(_f())
    assert info.value.motivo == "fonte_inacessivel"
    assert info.value.__cause__ is None and info.value.__suppress_context__ is True
    assert "SENHA-SECRETA-XYZ" not in caplog.text
    assert "SENHA-SECRETA-XYZ" not in str(info.value)


class _Resultado:
    def __init__(self, linhas):
        self.linhas = linhas

    def __iter__(self):
        class R:
            def __init__(self, d):
                self._mapping = d
        return iter(R(d) for d in self.linhas)


class _ConnGravadora:
    def __init__(self, linhas):
        self.sqls, self.linhas = [], linhas

    def connect(self):
        return self

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sqls.append((str(sql), params))
        return _Resultado(self.linhas)


def test_cada_leitura_e_read_only_com_timeout_local():
    conn = _ConnGravadora([{"data_ref": HOJE}])
    fonte_mod.FontePostgres(conn, 7000).datas()
    assert conn.sqls[0][0] == "SET TRANSACTION READ ONLY"
    assert "set_config('statement_timeout', :t, true)" in conn.sqls[1][0]
    assert conn.sqls[1][1] == {"t": "7000"}


def test_listas_vazias_viram_nulo_e_severidade_nao_vai_ao_breakdown():
    conn = _ConnGravadora([{"leve": 1, "media": 2, "grave": 3, "com_buybox": 4, "viol_com_buybox": 5}])
    fonte_mod.FontePostgres(conn, 7000).severidade(_f(severidades=("grave",)))
    params = conn.sqls[2][1]
    assert "sevs" not in params
    assert params["marcas"] is None and params["canais"] == ["Shopee"]


def test_modulos_nao_usam_mensagem_de_excecao():
    for src in (FONTE_SRC, ROTA_SRC, SVC_SRC):
        codigo = _codigo(src)
        assert "str(exc)" not in codigo and "repr(exc)" not in codigo
        assert not re.search(r"logger\.\w+\([^)]*exc\b(?!\)\.__name__)", codigo.replace("type(exc).__name__", ""))


def test_nenhum_segredo_ou_chave_de_aplicacao_no_codigo():
    for src in (FONTE_SRC, ROTA_SRC, SVC_SRC):
        assert "service_role" not in src.lower()
        assert not re.search(r"eyJhbGciOi", src)
        assert not re.search(r"sb_(secret|publishable)_", src)
