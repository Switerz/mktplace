"""Gate EXP-3C1 — a API de Expedicao passa a servir dois canais.

O que estes testes travam, em uma frase: a Shopee nao muda, o Mercado Livre so'
sai quando a flag dele estiver ligada, e nenhuma consulta alcanca a fila ou o
resumo sem dizer de qual canal esta' falando.

A `SessaoFake` vem do arquivo da EXP-2A de proposito: um dublê novo, escrito
junto com a mudanca, tenderia a responder exatamente o que a mudanca espera.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import expedicao_service as svc
from tests.test_expedicao_api import (
    AGORA,
    BATCH,
    HORA,
    WM,
    SessaoFake,
    linha_fila,
    resumo,
)

RAIZ_API = Path(__file__).resolve().parents[1]

#: Contas do ML como o pipeline as publica: `shop_account` E' o `seller_id`, e a
#: marca e' atributo vindo do registry.
CONTAS_ML = [
    ("1366932565", "rituaria", 2),
    ("2227056661", "kokeshi", 421),
    ("2532564723", "barbours", 140),
    ("2579732860", "lescent", 151),
]


def resumo_ml(shop_account, brand, backlog, **over):
    """Resumo do ML: todo o backlog cai em `deadline_unavailable`.

    O Mercado Livre nao publica prazo de despacho — ver as notas do canal.
    """
    base = resumo(brand, backlog)
    base.update({
        "shop_account": shop_account, "brand": brand,
        "on_time_count": 0, "deadline_unavailable_count": backlog,
    })
    base.update(over)
    return base


def linha_ml(shop_account="2227056661", brand="kokeshi", ordem="SHIP-1", **over):
    base = linha_fila(brand=brand, ordem=ordem)
    base.update({
        "shop_account": shop_account, "brand": brand,
        "dispatch_deadline": None, "deadline_source": "unavailable",
        "deadline_status": "unavailable", "hours_overdue": None,
        "logistic_type": "cross_docking", "carrier": None,
        "timestamp_quality": "assumed",
    })
    base.update(over)
    return base


def sessao_ml(**over):
    padrao = {
        "resumos": [resumo_ml(sa, b, n) for sa, b, n in CONTAS_ML],
        "fila": [linha_ml()],
        "registry": [
            {"external_seller_id": sa, "brand": b, "account_name": f"ML {b}"}
            for sa, b, _ in CONTAS_ML
        ],
        "auditoria": [{"sync_run_id": 341, "status": "success",
                       "rows_extracted": 714, "rows_loaded": 714}],
    }
    padrao.update(over)
    return SessaoFake(**padrao)


@pytest.fixture
def ligada(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", "", raising=False)


@pytest.fixture
def ml_ligado(ligada, monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_ml_api_enabled", True, raising=False)


def canais_ligados(sessao) -> set:
    """Todo valor ligado ao parametro `:canal` nas consultas da sessao."""
    return {p["canal"] for _sql, p in sessao.consultas if "canal" in p}


# ===========================================================================
# 1. Compatibilidade da Shopee
# ===========================================================================
def test_sem_channel_continua_shopee(ligada):
    """Quem ja' consome a rota nao informa canal nenhum."""
    s = SessaoFake()
    r = svc.get_expedicao(s)
    assert r["channel"] == "shopee"
    assert r["snapshot"]["channel"] == "shopee"
    assert canais_ligados(s) == {"shopee"}


def test_channel_shopee_explicito_produz_o_mesmo_payload(ligada):
    a = svc.get_expedicao(SessaoFake())
    b = svc.get_expedicao(SessaoFake(), channel="shopee")
    # `snapshot_age_hours` e' medido contra `now()` em cada chamada.
    for p in (a, b):
        p["limitations"].pop("snapshot_age_hours")
    assert a == b


def test_shopee_mantem_kokeshi_fora_da_cobertura(ligada):
    r = svc.get_expedicao(SessaoFake())
    assert r["limitations"]["brands_not_covered"] == ["kokeshi"]
    assert r["coverage"]["brands_not_covered"] == ["kokeshi"]


def test_shopee_continua_com_as_quatro_notas_originais(ligada):
    r = svc.get_expedicao(SessaoFake())
    notas = r["limitations"]["notes"]
    assert len(notas) == 4
    assert "raw.shopee_orders" in notas[1]


# ===========================================================================
# 2. Mercado Livre
# ===========================================================================
def test_ml_desligado_por_padrao_nao_serve_dado(ligada):
    """Flag propria: ligar a Expedicao nao expoe o ML sem querer."""
    s = sessao_ml()
    r = svc.get_expedicao(s, channel="mercadolivre")
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == "channel_disabled"
    assert r["channel"] == "mercadolivre"
    assert r["queue"] == [] and r["accounts"] == [] and r["totals"] is None
    assert s.consultas == [], "canal desligado nao pode nem tocar o banco"


def test_ml_desligado_tambem_bloqueia_a_tendencia(ligada):
    s = sessao_ml()
    r = svc.get_tendencia(s, channel="mercadolivre")
    assert r["unavailable_reason"] == "channel_disabled"
    assert s.consultas == []


def test_ml_ligado_serve_o_canal_certo(ml_ligado):
    s = sessao_ml()
    r = svc.get_expedicao(s, channel="mercadolivre")
    assert r["availability"] == "available"
    assert r["channel"] == "mercadolivre"
    assert r["snapshot"]["channel"] == "mercadolivre"
    assert canais_ligados(s) == {"mercadolivre"}


def test_ml_totaliza_o_backlog_das_quatro_contas(ml_ligado):
    r = svc.get_expedicao(sessao_ml(), channel="mercadolivre")
    assert r["totals"]["backlog_count"] == sum(n for _sa, _b, n in CONTAS_ML)
    assert len(r["accounts"]) == 4
    assert {c["shop_account"] for c in r["accounts"]} == {
        sa for sa, _b, _n in CONTAS_ML}


def test_ml_identifica_a_conta_pelo_seller_id_e_a_marca_pelo_registry(ml_ligado):
    r = svc.get_expedicao(sessao_ml(), channel="mercadolivre")
    for c in r["accounts"]:
        assert c["shop_account"].isdigit(), "a conta do ML e' o seller_id"
        assert not c["brand"].isdigit(), "a marca nunca e' o seller_id"


def test_ml_declara_que_nao_ha_prazo(ml_ligado):
    r = svc.get_expedicao(sessao_ml(), channel="mercadolivre")
    assert r["totals"]["deadline_unavailable_count"] == r["totals"]["backlog_count"]
    assert r["totals"]["overdue_count"] == 0
    junto = " ".join(r["limitations"]["notes"])
    assert "nao publica prazo de despacho" in junto
    assert "SHIPMENT" in junto


def test_kokeshi_e_coberta_no_ml(ml_ligado):
    """A lacuna da Shopee nao pode viajar para o payload do ML."""
    r = svc.get_expedicao(sessao_ml(), channel="mercadolivre")
    assert r["limitations"]["brands_not_covered"] == []
    assert r["coverage"]["brands_not_covered"] == []
    assert "kokeshi" in {c["brand"] for c in r["accounts"]}
    assert "kokeshi" not in " ".join(r["limitations"]["notes"])


def test_auditoria_do_ml_usa_a_fonte_do_canal(ml_ligado):
    s = sessao_ml()
    svc.get_expedicao(s, channel="mercadolivre")
    fontes = {p["fonte"] for _sql, p in s.consultas if "fonte" in p}
    assert fontes == {"expedicao_mercadolivre"}


def test_frescor_e_cobertura_usam_o_marketplace_do_canal(ml_ligado):
    s_ml = sessao_ml()
    svc.get_expedicao(s_ml, channel="mercadolivre")
    assert {p["mkt"] for _sql, p in s_ml.consultas if "mkt" in p} == {2}

    s_sh = SessaoFake()
    svc.get_expedicao(s_sh)
    assert {p["mkt"] for _sql, p in s_sh.consultas if "mkt" in p} == {3}


def test_registry_de_um_canal_nao_entra_no_payload_do_outro(ml_ligado):
    """O `marketplace_id` da consulta de registry decide de quem sao as contas."""
    s = sessao_ml()
    svc.get_expedicao(s, channel="mercadolivre")
    registry = [p for sql, p in s.consultas if "dim_seller_account" in sql]
    assert registry and all(p["mkt"] == 2 for p in registry)


# ===========================================================================
# 3. Tendencia
# ===========================================================================
def test_tendencia_isolada_por_canal(ml_ligado):
    ponto = {
        "snapshot_hour": HORA, "shop_account": "2227056661", "brand": "kokeshi",
        "refresh_batch_id": BATCH, "observed_at": AGORA, "backlog_count": 421,
        "overdue_count": 0, "due_within_24h_count": 0, "on_time_count": 0,
        "deadline_unavailable_count": 421, "over_48h_count": 169,
        "slow_count": 0, "zombie_count": 0, "stalled_count": 0,
        "source_watermark_at": WM, "source_advanced": False,
        "run_status": "success",
    }
    s = sessao_ml(tendencia=[ponto])
    r = svc.get_tendencia(s, channel="mercadolivre")
    assert r["channel"] == "mercadolivre"
    assert canais_ligados(s) == {"mercadolivre"}
    assert [p["shop_account"] for p in r["points"]] == ["2227056661"]


def test_tendencia_da_shopee_nao_muda(ligada):
    s = SessaoFake(tendencia=[])
    r = svc.get_tendencia(s)
    assert r["channel"] == "shopee"
    assert canais_ligados(s) == {"shopee"}


# ===========================================================================
# 4. Paginacao
# ===========================================================================
def test_paginacao_nao_duplica_nem_omite(ml_ligado):
    """Duas paginas seguidas do MESMO conjunto nao podem repetir uma linha.

    A ordenacao padrao termina em `shop_account, marketplace_order_id`: sem esse
    desempate estavel, duas consultas com OFFSET diferente podem devolver a
    mesma linha em ambas.
    """
    linhas = [linha_ml(ordem=f"SHIP-{i:03d}") for i in range(6)]
    s = sessao_ml(fila=linhas, total=6)
    p1, total = svc._pagina_da_fila(
        s, svc.CANAIS["mercadolivre"], BATCH, brands=None, accounts=None,
        situacoes=None, order_by="criticidade", limit=3, offset=0)
    assert total == 6
    sql_pagina = [sql for sql, _ in s.consultas if "LIMIT :limit" in sql][-1]
    assert "marketplace_order_id ASC" in sql_pagina, "falta o desempate estavel"
    assert "channel = :canal" in sql_pagina


def test_paginacao_do_ml_liga_canal_e_batch(ml_ligado):
    s = sessao_ml()
    svc.get_expedicao(s, channel="mercadolivre", limit=10, offset=0)
    pagina = [(sql, p) for sql, p in s.consultas if "LIMIT :limit" in sql]
    assert pagina
    for sql, p in pagina:
        assert p["canal"] == "mercadolivre"
        assert p["batch"] == BATCH


# ===========================================================================
# 5. Isolamento e falha fechada
# ===========================================================================
@pytest.mark.parametrize("canal", ["shopee", "mercadolivre"])
def test_snapshot_repeatable_read_read_only(ml_ligado, canal):
    s = sessao_ml() if canal == "mercadolivre" else SessaoFake()
    svc.get_expedicao(s, channel=canal)
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in s.sqls()
    assert getattr(s, "rollbacks", 0) >= 1


@pytest.mark.parametrize("canal", ["shopee", "mercadolivre"])
def test_batch_inconsistente_falha_fechado_nos_dois_canais(ml_ligado, canal):
    cab = {"linhas": 10, "lotes": 2, "instantes": 1,
           "batch": BATCH, "effective_at": AGORA}
    s = sessao_ml(cabecalho=cab) if canal == "mercadolivre" else SessaoFake(cabecalho=cab)
    r = svc.get_expedicao(s, channel=canal)
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == "inconsistent_batch"
    assert r["channel"] == canal
    assert r["queue"] == []


def test_resumo_de_outro_batch_nao_e_servido(ml_ligado):
    """A fila e o resumo saem sempre do MESMO lote."""
    s = sessao_ml(resumos=[])
    r = svc.get_expedicao(s, channel="mercadolivre")
    assert r["unavailable_reason"] == "inconsistent_batch"


def test_resumo_so_vem_do_batch_corrente(ml_ligado):
    """Nenhuma linha historica entra: a consulta filtra pelo batch da fila."""
    s = sessao_ml()
    svc.get_expedicao(s, channel="mercadolivre")
    resumos = [(sql, p) for sql, p in s.consultas
               if "FROM marts.expedicao_refresh_run" in sql and "snapshot_hour >=" not in sql]
    assert resumos
    for sql, p in resumos:
        assert "refresh_batch_id = :batch" in sql
        assert p["batch"] == BATCH


# ===========================================================================
# 6. Trava estrutural: nenhuma consulta sem canal
# ===========================================================================
def test_toda_consulta_de_fila_ou_resumo_filtra_por_canal():
    """A regra que sustenta tudo o resto, conferida no proprio texto do service.

    Uma consulta a `expedicao_fila_atual` ou `expedicao_refresh_run` sem
    `channel` devolveria os dois marketplaces misturados no payload de um so'.
    O `_pagina_da_fila` monta o WHERE em partes, entao a checagem aceita tanto a
    clausula inline quanto a montada em `onde`.
    """
    fonte = (RAIZ_API / "app/services/expedicao_service.py").read_text(encoding="utf-8")
    trechos = re.findall(r"FROM \{(FILA|RUN)\}(.*?)(?=\"\"\"|\n\n)", fonte, re.S)
    assert trechos, "o scanner nao achou consulta nenhuma - regra virou decoracao"
    sem_canal = [t for _tab, t in trechos
                 if "channel = :canal" not in t and "{filtro}" not in t
                 and '" AND ".join(onde)' not in t]
    assert not sem_canal, f"consulta sem filtro de canal: {sem_canal}"


def test_o_where_montado_sempre_comeca_pelo_canal():
    fonte = (RAIZ_API / "app/services/expedicao_service.py").read_text(encoding="utf-8")
    for bloco in re.findall(r"onde = \[(.*?)\]", fonte, re.S):
        assert "channel = :canal" in bloco, bloco


def test_nenhum_canal_hardcoded_em_parametro_de_consulta():
    """`{"canal": "shopee"}` literal traria a Shopee para dentro do payload ML."""
    fonte = (RAIZ_API / "app/services/expedicao_service.py").read_text(encoding="utf-8")
    assert '"canal": "shopee"' not in fonte
    assert '"canal": CHANNEL' not in fonte
    assert '"mkt": 3' not in fonte


# ===========================================================================
# 7. Allowlist de canal, na borda
# ===========================================================================
def test_canal_invalido_vira_422_sem_eco():
    from fastapi import HTTPException

    from app.routers import expedicao as rt

    veneno = "<script>alert(1)</script>"
    with pytest.raises(HTTPException) as e:
        rt.channel_query(veneno)
    assert e.value.status_code == 422
    assert veneno not in e.value.detail
    assert "Valores aceitos" in e.value.detail


@pytest.mark.parametrize("entrada", ["tiktokshop", "SHOPEE", "shopee ", "", "1"])
def test_apenas_os_dois_canais_da_allowlist_passam(entrada):
    from fastapi import HTTPException

    from app.routers import expedicao as rt

    with pytest.raises(HTTPException):
        rt.channel_query(entrada)


def test_canal_omitido_resolve_para_shopee():
    from app.routers import expedicao as rt

    assert rt.channel_query(None) == "shopee"


def test_resolver_canal_nao_inventa_canal():
    assert svc.resolver_canal(None).slug == "shopee"
    assert svc.resolver_canal("mercadolivre").slug == "mercadolivre"
    with pytest.raises(KeyError):
        svc.resolver_canal("tiktokshop")


def test_allowlist_tem_exatamente_os_dois_canais():
    assert set(svc.CANAIS) == {"shopee", "mercadolivre"}


# ===========================================================================
# 8. Higiene do payload
# ===========================================================================
def test_zero_identificador_de_pedido_no_payload_ml(ml_ligado):
    r = svc.get_expedicao(sessao_ml(), channel="mercadolivre")
    for linha in r["queue"]:
        assert "marketplace_order_id" not in linha
        assert "order_sn" not in linha
        assert "shipment_id" not in linha
        assert linha["order_ref"] is None, "sem segredo configurado, nao ha' ref"


def test_zero_pii_nos_campos_servidos_do_ml(ml_ligado):
    r = svc.get_expedicao(sessao_ml(), channel="mercadolivre")
    proibidos = ("buyer", "cpf", "receiver", "phone", "address", "endereco",
                 "email", "zip", "street", "document")
    for linha in r["queue"]:
        for campo in linha:
            assert not any(p in campo.lower() for p in proibidos), campo


def test_openapi_declara_o_parametro_channel_e_os_dois_valores():
    from app.main import app

    doc = app.openapi()
    for rota in ("/api/v1/expedicao", "/api/v1/expedicao/trend"):
        params = doc["paths"][rota]["get"]["parameters"]
        nomes = {p["name"] for p in params}
        assert "channel" in nomes, rota
        desc = next(p for p in params if p["name"] == "channel").get("description", "")
        assert "shopee" in desc and "mercadolivre" in desc


def test_openapi_nao_expoe_identificador_como_CAMPO():
    """Inspeciona NOMES DE PROPRIEDADE, nao a prosa.

    Varrer o texto do OpenAPI acusaria a propria descricao que EXPLICA por que
    `order_sn` nao e' servido — e obrigaria a documentar menos para o teste
    passar. O que importa e' que nenhum SCHEMA declare o campo.
    """
    from app.main import app

    doc = app.openapi()
    proibidos = {"order_sn", "marketplace_order_id", "shipment_id"}
    vazados = []
    for nome, esquema in doc.get("components", {}).get("schemas", {}).items():
        for prop in (esquema.get("properties") or {}):
            if prop in proibidos:
                vazados.append(f"{nome}.{prop}")
    assert not vazados, vazados


def test_openapi_nao_carrega_valor_de_segredo(monkeypatch):
    """Nenhum valor de `expedicao_order_ref_secret` pode chegar ao documento."""
    import json

    segredo = "s" * 64
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", segredo,
                        raising=False)
    from app.main import app

    app.openapi_schema = None
    texto = json.dumps(app.openapi())
    assert segredo not in texto


# ===========================================================================
# 9. A ROTA ligada, ponta a ponta
# ===========================================================================
def _cliente(sessao):
    """TestClient com o `get_db` trocado pelo duble.

    Os testes acima exercitam o service e a borda SEPARADAMENTE. Isso deixa um
    vao: a rota pode simplesmente esquecer de repassar `channel` ao service, e
    as duas metades continuam verdes enquanto o consumidor recebe sempre a
    Shopee. Aqui a fiacao inteira e' percorrida.
    """
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: sessao
    return TestClient(app), app


def test_rota_repassa_o_canal_ao_service(ml_ligado):
    s = sessao_ml()
    cliente, app = _cliente(s)
    try:
        r = cliente.get("/api/v1/expedicao?channel=mercadolivre&include_queue=false")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["channel"] == "mercadolivre"
    assert canais_ligados(s) == {"mercadolivre"}
    assert corpo["totals"]["backlog_count"] == sum(n for _sa, _b, n in CONTAS_ML)


def test_rota_de_tendencia_repassa_o_canal(ml_ligado):
    s = sessao_ml(tendencia=[])
    cliente, app = _cliente(s)
    try:
        r = cliente.get("/api/v1/expedicao/trend?channel=mercadolivre")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json()["channel"] == "mercadolivre"
    assert canais_ligados(s) == {"mercadolivre"}


def test_rota_sem_channel_continua_shopee(ligada):
    s = SessaoFake()
    cliente, app = _cliente(s)
    try:
        r = cliente.get("/api/v1/expedicao?include_queue=false")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json()["channel"] == "shopee"
    assert canais_ligados(s) == {"shopee"}


def test_rota_recusa_canal_fora_da_allowlist(ligada):
    s = SessaoFake()
    cliente, app = _cliente(s)
    try:
        r = cliente.get("/api/v1/expedicao?channel=tiktokshop")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 422
    assert "tiktokshop" not in r.text
    assert s.consultas == [], "canal invalido nao pode tocar o banco"


def test_rota_do_ml_desligado_devolve_envelope_e_nao_consulta(ligada):
    s = sessao_ml()
    cliente, app = _cliente(s)
    try:
        r = cliente.get("/api/v1/expedicao?channel=mercadolivre")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["availability"] == "unavailable"
    assert corpo["unavailable_reason"] == "channel_disabled"
    assert s.consultas == []


# ===========================================================================
# 10. Identidade canonica da conta na cobertura (EXP-3C1-R/V)
# ===========================================================================
def _cobertura_de(sessao, canal):
    return svc.get_expedicao(sessao, channel=canal, include_queue=False)["coverage"]


def test_esperado_e_observado_sao_o_MESMO_dominio_no_ml(ml_ligado):
    """O defeito que este gate corrigiu.

    Antes, `expected_accounts` trazia MARCAS e `observed_accounts` trazia
    `shop_account`. Na Shopee coincidiam por acidente; no ML sao dominios
    diferentes, e comparar os conjuntos nunca acusaria nada.
    """
    c = _cobertura_de(sessao_ml(), "mercadolivre")
    sellers = {sa for sa, _b, _n in CONTAS_ML}
    assert set(c["expected_accounts"]) == sellers
    assert set(c["observed_accounts"]) == sellers
    assert c["missing_accounts"] == [] and c["unexpected_accounts"] == []


def test_cobertura_do_ml_nao_mistura_marca_com_conta(ml_ligado):
    c = _cobertura_de(sessao_ml(), "mercadolivre")
    marcas = {b for _sa, b, _n in CONTAS_ML}
    for campo in ("expected_accounts", "observed_accounts",
                  "missing_accounts", "unexpected_accounts"):
        assert not (set(c[campo]) & marcas), f"{campo} carrega marca"
        assert all(v.isdigit() for v in c[campo]), f"{campo} nao e' seller_id"


def test_shopee_continua_com_o_nome_da_loja_nos_dois_conjuntos(ligada):
    c = _cobertura_de(SessaoFake(), "shopee")
    assert c["expected_accounts"] == c["observed_accounts"]
    assert set(c["expected_accounts"]) == {"apice", "barbours", "lescent", "rituaria"}


@pytest.mark.parametrize("canal", ["shopee", "mercadolivre"])
def test_os_quatro_conjuntos_vivem_no_dominio_da_conta(ml_ligado, canal):
    """Invariante do contrato, valido nos dois canais."""
    s = sessao_ml() if canal == "mercadolivre" else SessaoFake()
    c = _cobertura_de(s, canal)
    contas = {x["shop_account"] for x in c["accounts"]}
    for campo in ("expected_accounts", "observed_accounts",
                  "missing_accounts", "unexpected_accounts"):
        assert set(c[campo]) <= contas, f"{campo} saiu do dominio da conta"


def test_duas_contas_da_mesma_marca_nao_se_encobrem_no_ml(ml_ligado):
    """O caso que a comparacao por MARCA escondia.

    Duas contas da mesma marca, uma delas ausente da fotografia: comparando
    marcas, o conjunto observado conteria `kokeshi` e nada faltaria. Comparando
    contas, a que sumiu aparece em `missing_accounts` — que e' o ponto inteiro
    de existir um conjunto ESPERADO.
    """
    registry = [
        {"external_seller_id": "111", "brand": "kokeshi", "account_name": "ML A"},
        {"external_seller_id": "222", "brand": "kokeshi", "account_name": "ML B"},
    ]
    s = sessao_ml(registry=registry,
                  resumos=[resumo_ml("111", "kokeshi", 5)])
    c = _cobertura_de(s, "mercadolivre")
    assert c["expected_accounts"] == ["111", "222"]
    assert c["observed_accounts"] == ["111"]
    assert c["missing_accounts"] == ["222"], "a conta ausente tem de ser acusada"
    assert c["unexpected_accounts"] == []


def test_conta_do_ml_fora_do_registry_e_acusada(ml_ligado):
    s = sessao_ml(registry=[{"external_seller_id": sa, "brand": b,
                             "account_name": f"ML {b}"}
                            for sa, b, _n in CONTAS_ML[:3]])
    c = _cobertura_de(s, "mercadolivre")
    assert c["unexpected_accounts"] == ["2579732860"]
    assert c["missing_accounts"] == []


def test_conta_faltando_marca_a_saude_do_snapshot(ml_ligado):
    s = sessao_ml(registry=[{"external_seller_id": sa, "brand": b,
                             "account_name": f"ML {b}"}
                            for sa, b, _n in CONTAS_ML]
                  + [{"external_seller_id": "999", "brand": "nova",
                      "account_name": "ML nova"}])
    r = svc.get_expedicao(s, channel="mercadolivre", include_queue=False)
    assert r["coverage"]["missing_accounts"] == ["999"]
    assert r["snapshot"]["source_health"] == "account_missing"


def test_conta_nao_observada_traz_a_marca_do_registry(ml_ligado):
    s = sessao_ml(registry=[{"external_seller_id": sa, "brand": b,
                             "account_name": f"ML {b}"}
                            for sa, b, _n in CONTAS_ML]
                  + [{"external_seller_id": "999", "brand": "nova",
                      "account_name": "ML nova"}])
    c = _cobertura_de(s, "mercadolivre")
    ausente = next(x for x in c["accounts"] if x["shop_account"] == "999")
    assert ausente["observed"] is False
    assert ausente["brand"] == "nova", "a marca vem do registry, nao da identidade"
    assert ausente["backlog_count"] is None


def test_brands_not_covered_segue_no_campo_de_MARCAS(ml_ligado):
    """A cobertura por marca nao foi removida — mudou de lugar nenhum."""
    sh = _cobertura_de(SessaoFake(), "shopee")
    ml = _cobertura_de(sessao_ml(), "mercadolivre")
    assert sh["brands_not_covered"] == ["kokeshi"]
    assert ml["brands_not_covered"] == []


def test_identidade_declarada_por_canal():
    assert svc.CANAIS["shopee"].identidade_no_registry == "brand"
    assert svc.CANAIS["mercadolivre"].identidade_no_registry == "external_seller_id"
    linha = {"external_seller_id": "2227056661", "brand": "kokeshi"}
    assert svc._identidade_da_conta(svc.CANAIS["shopee"], linha) == "kokeshi"
    assert svc._identidade_da_conta(
        svc.CANAIS["mercadolivre"], linha) == "2227056661"
