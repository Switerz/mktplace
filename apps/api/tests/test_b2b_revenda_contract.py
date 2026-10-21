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
import json
import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import get_args

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr, ValidationError

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
            canais=("Amazon", "Mercado Livre", "Shopee"),
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
            MarcaCanalFonte("AP", "Mercado Livre", 500, 100, Decimal("-0.08"), Decimal("59.90")),
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
    monkeypatch.setattr(settings, "b2b_revenda_canais_kpi", "Mercado Livre,Shopee")
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


CHAVE = "chave-ficticia-de-teste-NAO-PODE-VAZAR"


@pytest.mark.parametrize("url,chave", [("", CHAVE), ("https://exemplo.supabase.co", "")])
def test_credencial_ausente_e_indisponivel(url, chave, ligado, singleton_limpo, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_supabase_url", url)
    monkeypatch.setattr(settings, "b2b_revenda_supabase_key", SecretStr(chave))
    r = TestClient(app).get(BASE + "/resumo")
    assert r.status_code == 200
    assert r.json()["motivo_tecnico"] == "credencial_ausente"
    assert fonte_mod._singleton is None, "falha de credencial nao pode ficar guardada"


@pytest.mark.parametrize("url", [
    "http://exemplo.supabase.co",                       # sem TLS
    "https://usuario:senha@exemplo.supabase.co",        # credencial embutida
    "https://exemplo.supabase.co/rest/v1",              # caminho
    "https://exemplo.supabase.co/?apikey=x",            # consulta
    "isto nao e uma url",
])
def test_url_invalida_e_recusada_sem_eco(url, ligado, singleton_limpo, monkeypatch, caplog):
    monkeypatch.setattr(settings, "b2b_revenda_supabase_url", url)
    monkeypatch.setattr(settings, "b2b_revenda_supabase_key", SecretStr(CHAVE))
    caplog.set_level(logging.DEBUG)
    r = TestClient(app).get(BASE + "/resumo")
    assert r.json()["motivo_tecnico"] == "credencial_invalida"
    assert CHAVE not in r.text and CHAVE not in caplog.text


def test_chave_com_quebra_de_linha_e_recusada():
    with pytest.raises(FonteIndisponivel) as info:
        fonte_mod.criar_cliente("https://exemplo.supabase.co", "abc\r\nX-Injetado: 1", timeout_segundos=5)
    assert info.value.motivo == "credencial_invalida"


def test_chave_e_secretstr_e_nao_aparece_em_repr():
    s = Settings(b2b_revenda_supabase_key=CHAVE)
    assert CHAVE not in repr(s) and CHAVE not in str(s.model_dump())
    assert Settings.model_fields["b2b_revenda_supabase_key"].annotation is SecretStr


def test_criar_cliente_nao_conecta_e_so_leva_apikey():
    # Host inexistente: se `criar_cliente` conectasse, isto falharia.
    c = fonte_mod.criar_cliente("https://host-que-nao-existe.invalid", CHAVE, timeout_segundos=5)
    assert c.headers["apikey"] == CHAVE
    assert "authorization" not in {k.lower() for k in c.headers}
    assert c.follow_redirects is False
    assert str(c.base_url).startswith("https://host-que-nao-existe.invalid")


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
        assert f.canais == ("Mercado Livre", "Shopee")
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
    falsa.carga = replace(falsa.carga, canais=("Amazon", "Magalu", "Mercado Livre", "Shopee"))
    escopo = client.get(BASE + "/frescor").json()["escopo"]
    assert escopo["piso"] == "pma_octaprice"
    assert "PDV" in escopo["aviso_piso"]
    assert escopo["canais_nos_kpis"] == ["Mercado Livre", "Shopee"]
    motivos = {c["canal"]: c["motivo"] for c in escopo["canais_excluidos"]}
    assert motivos == {"Amazon": svc.MOTIVO_AMAZON, "Magalu": svc.MOTIVO_FORA_ALLOWLIST}


def test_canais_padrao_usam_a_grafia_da_fonte():
    # Medido na fonte em 21/10/2026: o canal e' "Mercado Livre", com espaco.
    # "Mercadolivre" tirava o ML inteiro dos KPIs sem erro nenhum.
    assert Settings().b2b_revenda_canais_kpi_list == ["Mercado Livre", "Shopee"]


def test_grafia_errada_na_allowlist_aparece_no_escopo(ligado, client, falsa, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_canais_kpi", "Mercadolivre,Shopee")
    escopo = client.get(BASE + "/frescor").json()["escopo"]
    assert escopo["canais_kpi_sem_dados"] == ["Mercadolivre"]
    # E o canal real, fora da allowlist, aparece como excluido.
    assert "Mercado Livre" in {c["canal"] for c in escopo["canais_excluidos"]}


def test_escopo_sem_alerta_quando_allowlist_bate(ligado, client):
    assert client.get(BASE + "/frescor").json()["escopo"]["canais_kpi_sem_dados"] == []


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




def test_allowlist_tem_exatamente_as_seis_rpcs():
    assert fonte_mod.RPCS_PERMITIDAS == {
        "fn_monit_datas", "fn_monit_resumo", "fn_monit_severidade",
        "fn_monit_marca_canal", "fn_monit_top_produtos", "fn_monit_sellers",
    }
    assert set(fonte_mod.COLUNAS) == set(fonte_mod.RPCS_PERMITIDAS)


def test_colunas_lidas_nao_incluem_dado_de_cliente():
    for nome, colunas in fonte_mod.COLUNAS.items():
        for c in colunas:
            assert not _PROIBIDOS.search(c), f"{nome}.{c}"
    # De `fn_monit_sellers`, so' as duas colunas da contagem.
    assert fonte_mod.COLUNAS["fn_monit_sellers"] == ("identificado", "violando")


def test_argumentos_nunca_levam_cliente_nem_vendedor():
    corpo = fonte_mod.argumentos(_f(marcas=("AP",), severidades=("grave",)))
    assert set(corpo) == {"p_data", "p_marcas", "p_canais", "p_skus", "p_ufs", "p_sevs"}
    assert corpo["p_data"] == "2026-10-21" and corpo["p_marcas"] == ["AP"] and corpo["p_skus"] is None
    assert "p_sevs" not in fonte_mod.argumentos(_f(), com_severidade=False)


def test_nenhum_endpoint_generico_nem_nome_de_rpc_da_requisicao():
    # O OpenAPI publicado e' a superficie que de fato chega ao mundo.
    paths = app.openapi()["paths"]
    rotas = {p for p in paths if p.startswith(BASE)}
    assert rotas == {BASE + c for c in ROTAS}
    assert all(set(paths[p]) == {"get"} for p in rotas), "so' leitura"
    codigo = _codigo(FONTE_SRC)
    # O nome da RPC so' entra no caminho a partir de literais da allowlist.
    assert re.findall(r"self\._rpc\((\w+)", codigo) == []
    assert set(re.findall(r"self\._rpc\('(\w+)'", codigo)) == set(fonte_mod.RPCS_PERMITIDAS)


# ---------------------------------------------------------------------------
# 9. Adapter HTTP real (transporte falso): contrato, erros e sigilo da chave
# ---------------------------------------------------------------------------
class Transporte:
    """Responde como o PostgREST; registra cada requisicao."""

    def __init__(self, respostas=None, falha=None):
        self.respostas = respostas or {}
        self.falha = falha
        self.reqs: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.reqs.append(req)
        if self.falha:
            raise self.falha
        nome = req.url.path.rsplit("/", 1)[-1]
        r = self.respostas.get(nome, [])
        if callable(r):
            return r(req)
        if isinstance(r, httpx.Response):
            return r
        return httpx.Response(200, json=r)


def _fonte_http(transporte: Transporte) -> fonte_mod.FonteSupabaseHttp:
    cliente = fonte_mod.criar_cliente("https://exemplo.supabase.co", CHAVE, timeout_segundos=5)
    cliente._transport = httpx.MockTransport(transporte)
    return fonte_mod.FonteSupabaseHttp(cliente)


def test_http_chama_rpc_por_post_com_select_e_corpo_nomeado():
    t = Transporte({"fn_monit_resumo": [{
        "data_ref": "2026-10-21", "total": 7, "violando": 5, "pct": 71.4,
        "sellers_violando": 3, "produtos": 3, "desvio_medio": -0.098}]})
    r = _fonte_http(t).resumo(_f(severidades=("grave",)))
    assert (r.total, r.violando, r.pct, r.desvio_medio) == (7, 5, Decimal("71.4"), Decimal("-0.098"))
    req = t.reqs[0]
    assert req.method == "POST" and req.url.path == "/rest/v1/rpc/fn_monit_resumo"
    assert req.url.params["select"] == "data_ref,total,violando,pct,sellers_violando,produtos,desvio_medio"
    corpo = json.loads(req.content)
    assert corpo["p_canais"] == ["Shopee"] and corpo["p_sevs"] == ["grave"]
    assert "p_vendedores" not in corpo and "p_cnpjs" not in corpo
    assert req.headers["apikey"] == CHAVE
    assert "authorization" not in {k.lower() for k in req.headers}
    assert CHAVE not in str(req.url)


def test_http_cobertura_conta_e_pagina_sem_guardar_linha():
    def sellers(req):
        inicio = int(req.headers["Range"].split("-")[0])
        total = 1500
        n = max(0, min(fonte_mod.PAGINA, total - inicio))
        linhas = [{"identificado": (inicio + i) % 3 == 0, "violando": 2} for i in range(n)]
        return httpx.Response(206 if n else 200, json=linhas)

    t = Transporte({"fn_monit_sellers": sellers})
    cob = _fonte_http(t).cobertura(_f())
    assert cob.sellers_violando == 1500 and cob.anuncios_violando == 3000
    assert cob.sellers_violando_identificados == 500 and cob.anuncios_violando_identificados == 1000
    assert [r.headers["Range"] for r in t.reqs] == ["0-999", "1000-1999"]
    assert t.reqs[0].url.params["select"] == "identificado,violando"


def test_http_carga_vem_de_datas_e_marca_canal_sem_filtro():
    t = Transporte({
        "fn_monit_datas": [{"data_ref": "2026-10-20"}, {"data_ref": "2026-10-21"}],
        "fn_monit_marca_canal": [
            {"marca_id": "AP", "canal": "Mercado Livre", "anuncios": 4, "violando": 3, "desvio_medio": None, "preco_medio": 1},
            {"marca_id": "AP", "canal": "Amazon", "anuncios": 2, "violando": 2, "desvio_medio": None, "preco_medio": 1},
        ],
    })
    carga = _fonte_http(t).carga_mais_recente()
    assert carga.data_ref == date(2026, 10, 21) and carga.linhas == 6
    assert carga.canais == ("Amazon", "Mercado Livre")
    assert carga.coleta_fim is None and carga.importado_fim is None
    corpo_mc = json.loads(t.reqs[1].content)
    assert corpo_mc["p_canais"] is None and corpo_mc["p_data"] == "2026-10-21"


@pytest.mark.parametrize("status,codigo,motivo", [
    (401, None, "credencial_invalida"),
    (403, "42501", "permissao_negada"),
    (401, "42501", "permissao_negada"),
    (404, "PGRST202", "contrato_da_fonte_quebrado"),
    (400, "42703", "contrato_da_fonte_quebrado"),
    (500, "57014", "fonte_lenta"),
    (503, None, "fonte_inacessivel"),
    (429, None, "fonte_inacessivel"),
    (418, None, "falha_desconhecida"),
])
def test_http_erros_viram_motivo_sem_ler_mensagem(status, codigo, motivo, caplog):
    caplog.set_level(logging.DEBUG)
    corpo = {"code": codigo, "message": f"detalhe com {CHAVE}", "hint": "x"}
    t = Transporte({"fn_monit_resumo": httpx.Response(status, json=corpo)})
    with pytest.raises(FonteIndisponivel) as info:
        _fonte_http(t).resumo(_f())
    assert info.value.motivo == motivo
    assert CHAVE not in caplog.text and CHAVE not in str(info.value)


@pytest.mark.parametrize("exc,motivo", [
    (httpx.ConnectTimeout("t"), "fonte_inacessivel"),
    (httpx.ReadTimeout("t"), "fonte_lenta"),
    (httpx.ConnectError("t"), "fonte_inacessivel"),
])
def test_http_falha_de_transporte_sem_encadear(exc, motivo, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(FonteIndisponivel) as info:
        _fonte_http(Transporte(falha=exc)).datas()
    assert info.value.motivo == motivo
    assert info.value.__cause__ is None and info.value.__suppress_context__ is True
    assert CHAVE not in caplog.text


@pytest.mark.parametrize("payload", [
    {"nao": "lista"},
    [1, 2],
    [{"data_ref": "2026-10-21"}, {"data_ref": "x"}],
])
def test_http_payload_inesperado_e_contrato_quebrado(payload):
    with pytest.raises(FonteIndisponivel) as info:
        _fonte_http(Transporte({"fn_monit_datas": payload})).datas()
    assert info.value.motivo == "contrato_da_fonte_quebrado"


def test_http_coluna_faltando_e_contrato_quebrado():
    t = Transporte({"fn_monit_severidade": [{"leve": 1, "media": 2}]})
    with pytest.raises(FonteIndisponivel) as info:
        _fonte_http(t).severidade(_f())
    assert info.value.motivo == "contrato_da_fonte_quebrado"


def test_http_severidade_nao_manda_p_sevs():
    t = Transporte({"fn_monit_severidade": [{"leve": 1, "media": 2, "grave": 3, "com_buybox": 4, "viol_com_buybox": 5}]})
    _fonte_http(t).severidade(_f(severidades=("grave",)))
    assert "p_sevs" not in json.loads(t.reqs[0].content)


def test_rpc_fora_da_allowlist_e_recusada():
    with pytest.raises(ValueError):
        _fonte_http(Transporte())._rpc("fn_monit_identificacao", {})


def test_modulos_nao_usam_mensagem_de_excecao():
    for src in (FONTE_SRC, ROTA_SRC, SVC_SRC):
        codigo = _codigo(src)
        assert "str(exc)" not in codigo and "repr(exc)" not in codigo
        assert ".text" not in codigo, "corpo de resposta da fonte nao e' lido como texto"


def test_nenhum_segredo_ou_chave_de_aplicacao_no_codigo():
    for src in (FONTE_SRC, ROTA_SRC, SVC_SRC):
        assert "service_role" not in src.lower()
        assert not re.search(r"eyJhbGciOi", src)
        assert not re.search(r"sb_(secret|publishable)_[A-Za-z0-9]", src)
