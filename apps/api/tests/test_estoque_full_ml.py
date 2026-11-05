"""ML-FULL-STOCK-API-1 — rota `/api/v1/performance/ml-full-estoque`.

O QUE ESTE ARQUIVO PROTEGE
---------------------------
1. **Ausencia nunca vira zero.** Flag desligada, 029 nao aplicada, nada
   publicado, data sem fotografia, inventario SEM_LEITURA e marca sem
   fotografia no dia: cada um tem teste, e nenhum pode chegar como 0.
2. **Estoque contado uma vez por inventario.** Um inventario compartilhado
   por varios anuncios aparece UMA vez, e o total da marca nao multiplica por
   anuncio. Se o agregado de marca divergir da soma por inventario (o que um
   somatorio por anuncio produziria), a rota recusa com 503.
3. **Nenhum identificador do ML na API publica.** `seller_id` e
   `inventory_id` nao saem no JSON (nem derivados); a linha tem so' o
   ordinal `posicao` dentro da resposta.

DUAS CAMADAS DE TESTE
---------------------
- Sessao FALSA: prova orquestracao (flag corta antes do banco, ordem dos
  comandos, `None` preservado, reconciliacao). Roda sempre.
- PostgreSQL REAL, com o DDL da PROPRIA migration 029 (CHECKs inclusive):
  prova que o SQL existe e que os dados ficticios respeitam o contrato.
  Sem `TEST_PG_DSN` sao PULADOS, nunca falseados.

Todos os dados sao FICTICIOS (contas `S-*`, inventarios `INV-*`).
"""
from __future__ import annotations

import importlib.util
import io
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import ml_full_stock_service as svc

RAIZ = Path(__file__).resolve().parents[1]
SERVICE_SRC = io.open(
    RAIZ / "app" / "services" / "ml_full_stock_service.py",
    encoding="utf-8").read()
MIGRATION_PATH = RAIZ / "alembic" / "versions" / "029_create_fact_ml_full_stock_daily.py"
MIGRATION_SRC = io.open(MIGRATION_PATH, encoding="utf-8").read()
MIGRATION_038_PATH = RAIZ / "alembic" / "versions" / "038_ml_full_inventario_anuncio_ancora.py"
MIGRATION_014_PATH = RAIZ / "alembic" / "versions" / "014_create_price_monitoring_serving.py"

ROTA = "/api/v1/performance/ml-full-estoque"
REF = date(2026, 10, 25)
ANTERIOR = date(2026, 10, 23)
HOJE = date(2026, 10, 26)
PUBLICADO = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
AGORA = datetime(2026, 10, 26, 2, 0, tzinfo=timezone.utc)


# ===========================================================================
# Dados ficticios — um so' conjunto, usado pela sessao falsa e pelo Postgres
# ===========================================================================

def _inv(brand, seller, inv, read_status="OK", disp=None, nao=None, tr=None,
         eh_full=True, anuncios=1, ativos=1, full=1, un=0, media="0",
         cob=None, classe="SEM_DEMANDA_MEDIDA", ref=REF, ancora=None,
         variacao=None, extras=None):
    ok = read_status == "OK"
    return {
        "ref_date": ref, "brand": brand, "seller_id": seller,
        "inventory_id": inv, "read_status": read_status,
        "disponivel": disp, "nao_disponivel": nao, "transfer": tr,
        "total_coerente": True if ok else None,
        "detalhe_coerente": True if ok else None,
        "eh_full": eh_full, "anuncios": anuncios, "anuncios_ativos": ativos,
        "anuncios_full": full, "unidades_28d": un,
        "media_diaria_28d": Decimal(media),
        "cobertura_torre_dias": None if cob is None else Decimal(cob),
        "classificacao_torre": classe,
        "vinculo_vendas": "COM_VENDA" if un > 0 else "SEM_VENDA_NA_JANELA",
        "source_run_id": "run-ficticio",
        # Migration 038. `extras=None` = linha publicada antes da 038.
        "anuncio_ancora_item_id": ancora,
        "anuncio_ancora_variation_id": variacao,
        "anuncios_extras": extras,
    }


INVENTARIOS = [
    # 🔑 INV-B1: UM inventario compartilhado por TRES anuncios. Estoque 10,
    # nunca 30.
    # Ancora MLB-B1 tem VARIAS linhas na 014 (datas, contas, outra marca).
    _inv("barbours", "S-B", "INV-B1", disp=10, nao=3, tr=1, anuncios=3,
         ativos=2, full=3, un=56, media="2.00", cob="5.0",
         classe="BAIXO_CANDIDATO", ancora="MLB-B1", extras=2),
    # Ruptura medida: 0 de verdade. Ancora SEM linha na 014.
    _inv("barbours", "S-B", "INV-B2", disp=0, nao=0, tr=0, un=28,
         media="1.00", cob="0.0", classe="RUPTURA_CANDIDATA",
         ancora="MLB-SEM-014", extras=0),
    # SEM_LEITURA: quantidades NULAS, nunca zero.
    _inv("barbours", "S-B", "INV-B3", read_status="NAO_ENCONTRADO",
         anuncios=2, ativos=2, full=2, un=14, media="0.50",
         classe="SEM_LEITURA"),
    # Fora do Full: contexto.
    _inv("barbours", "S-B", "INV-B4", disp=40, nao=0, tr=0, eh_full=False,
         full=0, classe="FORA_DO_FULL"),
    # Ancora e' VARIACAO: o SKU vem do item (ambiguo). 014 sem SKU.
    _inv("lescent", "S-L", "INV-L1", disp=500, nao=0, tr=0, un=28,
         media="1.00", cob="500.0", classe="EXCESSO_CANDIDATO",
         ancora="MLB-L1", variacao="777", extras=0),
    _inv("lescent", "S-L", "INV-L2", disp=60, nao=0, tr=0, anuncios=2,
         ativos=1, full=2, un=28, media="1.00", cob="60.0",
         classe="SUFICIENTE"),
    # Rituaria: unico inventario Full sem leitura -> disponivel da marca NULL.
    _inv("rituaria", "S-R", "INV-R1", read_status="CONTRATO_QUEBRADO",
         classe="SEM_LEITURA"),
    # Kokeshi so' tem fotografia ANTERIOR: no dia exibido, sem fotografia.
    _inv("kokeshi", "S-K", "INV-K1", disp=7, nao=0, tr=0, un=0,
         classe="SEM_GIRO_CANDIDATO", ref=ANTERIOR),
]

#: O ID do inventario NAO sai na resposta (API publica, sem auth). Nos testes,
#: cada linha do fixture e' reconhecida por (marca, classificacao) -- par unico
#: neste fixture, garantido pelo assert abaixo.
CHAVE = {i["inventory_id"]: (i["brand"], i["classificacao_torre"])
         for i in INVENTARIOS}
assert len(set(CHAVE.values())) == len(CHAVE), "fixture ambiguo"
IDS_FICTICIOS = sorted(CHAVE)


def _chave(i):
    return (i.brand, i.classificacao_torre)


def _linha(resposta, inventory_id):
    return next(i for i in resposta.inventarios
                if _chave(i) == CHAVE[inventory_id])

def _preco(brand, item, ref, sku, titulo, seller=101):
    """Linha minima VALIDA da 014 (fact_marketplace_listing_price_daily)."""
    return {
        "ref_date": ref, "marketplace": "ml", "brand": brand,
        "seller_id": seller, "item_id": item, "seller_sku": sku,
        "gtin": None, "listing_title": titulo,
        "permalink": f"https://exemplo.invalido/{item}",
        "advertised_price": Decimal("10.00"), "original_price": None,
        "currency": "BRL", "listing_status": "active",
        "catalog_listing": False,
        "price_captured_at": datetime(2026, 10, 1, 12, 0),
        "listing_metadata_updated_at": None,
        "source_run_id": "run-ficticio",
    }


#: 014 ficticia. MLB-B1 aparece 5 vezes: duas datas anteriores, a data exibida
#: em DUAS contas (desempate pela menor) e uma data FUTURA que nao pode vencer.
#: A mesma `item_id` em outra marca nao pode casar.
PRECOS = [
    _preco("barbours", "MLB-B1", date(2026, 10, 1), "SKU-VELHO", "Titulo velho"),
    _preco("barbours", "MLB-B1", ANTERIOR, "SKU-ANTERIOR", "Titulo anterior"),
    _preco("barbours", "MLB-B1", REF, "SKU-B1", "Shampoo B1 300ml"),
    _preco("barbours", "MLB-B1", REF, "SKU-OUTRA-CONTA", "Outra conta", seller=202),
    _preco("barbours", "MLB-B1", date(2026, 10, 26), "SKU-FUTURO", "Futuro"),
    # Outra marca = outra conta: a PK da 014 nao inclui `brand`.
    _preco("lescent", "MLB-B1", REF, "SKU-OUTRA-MARCA", "Outra marca", seller=303),
    _preco("lescent", "MLB-L1", ANTERIOR, None, "Perfume L1 50ml"),
]

STATUS = [
    {"ref_date": REF, "brand": "barbours", "seller_id": "S-B",
     "inventory_id": "INV-B1", "status_raw": "transfer", "quantidade": 1,
     "source_run_id": "run-ficticio"},
    {"ref_date": REF, "brand": "barbours", "seller_id": "S-B",
     "inventory_id": "INV-B1", "status_raw": "damaged", "quantidade": 2,
     "source_run_id": "run-ficticio"},
]


def _marca(brand, ref, inventarios, full_lidos, compart, disp, nao, tr,
           alocadas, fora_ponte=0, cob_aloc=None, publicado=PUBLICADO):
    return {
        "ref_date": ref, "brand": brand, "inventarios": inventarios,
        "inventarios_full_lidos": full_lidos,
        "inventarios_compartilhados": compart,
        "disponivel_full": disp, "nao_disponivel_full": nao,
        "transfer_full": tr, "unidades_28d_alocadas": alocadas,
        "unidades_28d_item_varios_inventarios": 0,
        "unidades_28d_sem_inventory_id": 0,
        "unidades_28d_fora_da_ponte": fora_ponte,
        "cobertura_da_alocacao": None if cob_aloc is None else Decimal(cob_aloc),
        "source_run_id": "run-ficticio", "published_at": publicado,
    }


MARCAS = [
    _marca("barbours", REF, 4, 2, 2, 10, 3, 1, 98, fora_ponte=2,
           cob_aloc="0.9800"),
    _marca("lescent", REF, 2, 2, 1, 560, 0, 0, 56, cob_aloc="1.0000"),
    _marca("rituaria", REF, 1, 0, 0, None, None, None, 0),
    _marca("kokeshi", ANTERIOR, 1, 1, 0, 7, 0, 0, 0,
           publicado=datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)),
]


# ===========================================================================
# Sessao falsa: responde por CONSTANTE SQL, como o servico consulta
# ===========================================================================

class _Resultado:
    def __init__(self, linhas=None, escalar=None):
        self._linhas = linhas or []
        self._escalar = escalar

    def mappings(self):
        return self

    def all(self):
        return list(self._linhas)

    def one(self):
        assert len(self._linhas) == 1
        return self._linhas[0]

    def scalar(self):
        return self._escalar


class SessaoFalsa:
    """Emula as fatos em memoria. Ordenacao da listagem NAO e' emulada: ela e'
    provada no Postgres real."""

    def __init__(self, inventarios=INVENTARIOS, marcas=MARCAS, status=STATUS,
                 tabelas=(True, True, True), identificacao=(True, True),
                 precos=PRECOS):
        self.inventarios = inventarios
        self.marcas = marcas
        self.status = status
        self.tabelas = tabelas
        #: (colunas da 038 presentes, 014 legivel)
        self.identificacao = identificacao
        self.precos = precos
        self.comandos: list[str] = []

    def rollback(self):
        self.comandos.append("rollback")

    def _nome(self, stmt):
        for nome in dir(svc):
            if nome.startswith("SQL_") and getattr(svc, nome) is stmt:
                return nome
        raise AssertionError(f"SQL desconhecido: {stmt}")

    def execute(self, stmt, params=None):
        nome = self._nome(stmt)
        self.comandos.append(nome)
        p = params or {}
        marcas = set(p.get("brands") or [])
        dia = p.get("ref_date")
        if nome == "SQL_SNAPSHOT_COERENTE":
            return _Resultado()
        if nome == "SQL_FATOS_EXISTEM":
            a, b, c = self.tabelas
            return _Resultado([{"tem_inventario": a, "tem_status": b,
                                "tem_marca": c}])
        if nome == "SQL_IDENTIFICACAO_DISPONIVEL":
            a, b = self.identificacao
            return _Resultado([{"tem_ancora": a, "tem_precos": b}])
        if nome == "SQL_ULTIMA_FOTOGRAFIA":
            datas = [m["ref_date"] for m in self.marcas if m["brand"] in marcas]
            return _Resultado(escalar=max(datas) if datas else None)
        if nome == "SQL_MARCAS_DO_DIA":
            return _Resultado(sorted(
                (m for m in self.marcas
                 if m["ref_date"] == dia and m["brand"] in marcas),
                key=lambda m: m["brand"]))
        if nome == "SQL_ULTIMA_POR_MARCA":
            ult = {}
            for m in self.marcas:
                if m["brand"] in marcas and m["ref_date"] <= dia:
                    if m["brand"] not in ult or m["ref_date"] > ult[m["brand"]]["ref_date"]:
                        ult[m["brand"]] = m
            return _Resultado(list(ult.values()))
        do_dia = [i for i in self.inventarios
                  if i["ref_date"] == dia and i["brand"] in marcas]
        if nome == "SQL_CONTAGENS":
            grupos: dict = defaultdict(lambda: {
                "inventarios": 0, "disponivel": None,
                "nao_disponivel": None, "transfer": None})
            for i in do_dia:
                k = (i["brand"], i["classificacao_torre"], i["eh_full"],
                     i["read_status"] == "OK", i["anuncios"] >= 2)
                g = grupos[k]
                g["inventarios"] += 1
                for c in ("disponivel", "nao_disponivel", "transfer"):
                    if i[c] is not None:
                        g[c] = (g[c] or 0) + i[c]
            return _Resultado([
                {"brand": k[0], "classificacao_torre": k[1], "eh_full": k[2],
                 "lido": k[3], "compartilhado": k[4], **g}
                for k, g in grupos.items()])
        if nome == "SQL_STATUS":
            validos = {(i["brand"], i["seller_id"], i["inventory_id"])
                       for i in do_dia if i["eh_full"] and i["read_status"] == "OK"}
            agg: dict = defaultdict(lambda: [0, 0])
            for s in self.status:
                if s["ref_date"] == dia and (s["brand"], s["seller_id"], s["inventory_id"]) in validos:
                    agg[(s["brand"], s["status_raw"])][0] += s["quantidade"]
                    agg[(s["brand"], s["status_raw"])][1] += 1
            return _Resultado([
                {"brand": b, "status_raw": st, "quantidade": q, "inventarios": n}
                for (b, st), (q, n) in agg.items()])
        if nome == "SQL_STATUS_POR_MARCA":
            por_marca: dict = defaultdict(int)
            for s in self.status:
                if s["ref_date"] == dia and s["brand"] in marcas:
                    por_marca[s["brand"]] += 1
            return _Resultado([{"brand": b, "linhas": n}
                               for b, n in por_marca.items()])
        listaveis = [i for i in do_dia if p["incluir_fora_do_full"]
                     or i["classificacao_torre"] != "FORA_DO_FULL"]
        if nome == "SQL_TOTAL_NO_FILTRO":
            return _Resultado(escalar=len(listaveis))
        if nome == "SQL_INVENTARIOS":
            # Sem a 038 as colunas novas nem existem: nao chegam ao servico.
            return _Resultado([{k: v for k, v in i.items()
                                if not k.startswith("anuncio")
                                or k in ("anuncios", "anuncios_ativos",
                                         "anuncios_full")}
                               for i in listaveis[: p["limite"]]])
        if nome == "SQL_INVENTARIOS_IDENTIFICADOS":
            # Emula o DISTINCT ON (brand, item_id) da CTE `item_014`.
            item_014 = {}
            for pr in sorted((x for x in self.precos
                              if x["marketplace"] == "ml"
                              and x["brand"] in marcas and x["ref_date"] <= dia),
                             key=lambda x: (x["ref_date"], -x["seller_id"]),
                             reverse=True):
                item_014.setdefault((pr["brand"], pr["item_id"]), pr)
            saida = []
            for i in listaveis[: p["limite"]]:
                casa = item_014.get((i["brand"], i["anuncio_ancora_item_id"]))
                saida.append({**i,
                              "tem_ancora": i["anuncio_ancora_item_id"] is not None,
                              "ancora_e_variacao": i["anuncio_ancora_variation_id"] is not None,
                              "sku": casa["seller_sku"] if casa else None,
                              "titulo": casa["listing_title"] if casa else None})
            return _Resultado(saida)
        raise AssertionError(nome)


def _bloco_falso(sessao=None, **kw):
    return svc.get_ml_full_estoque_block(
        sessao or SessaoFalsa(), hoje=HOJE, agora=AGORA, **kw)


# ===========================================================================
# 1. Contrato do codigo
# ===========================================================================

def test_dominio_da_classificacao_espelha_a_migration():
    bloco = MIGRATION_SRC.split("CLASSIFICACOES = (")[1].split(")")[0]
    import re
    assert set(re.findall(r'"([A-Z_]+)"', bloco)) == set(svc.CLASSIFICACOES)


def test_marcas_espelham_a_migration():
    bloco = MIGRATION_SRC.split("MARCAS = (")[1].split(")")[0]
    import re
    assert set(re.findall(r'"([a-z_]+)"', bloco)) == set(svc.EXPECTED_BRANDS)


def test_le_somente_as_tres_fatos_da_029():
    """Nada de Data Mart, raw, silver ou API do ML no request."""
    sqls = " ".join(str(getattr(svc, n)) for n in dir(svc) if n.startswith("SQL_"))
    for proibido in ("raw.", "silver.", "gold.", "api.", "ml_fulfillment"):
        assert proibido not in sqls, proibido
    for t in (svc.INVENTORY_TABLE, svc.STATUS_TABLE, svc.BRAND_TABLE):
        assert f'"{t}"' in MIGRATION_SRC, f"{t} nao e' tabela da 029"


def test_le_so_as_fatos_da_029_e_a_014_para_identificar():
    import re
    sqls = " ".join(str(getattr(svc, n)) for n in dir(svc) if n.startswith("SQL_"))
    tabelas = set(re.findall(r"\bmarts\.[a-z_0-9]+", sqls))
    assert tabelas == {svc.INVENTORY_TABLE, svc.STATUS_TABLE, svc.BRAND_TABLE,
                       svc.PRICE_TABLE}
    # A 014 entra SO' no SQL identificado (e por parametro no to_regclass).
    assert svc.PRICE_TABLE in str(svc.SQL_INVENTARIOS_IDENTIFICADOS)
    assert 'CREATE TABLE marts.fact_marketplace_listing_price_daily' in io.open(
        MIGRATION_014_PATH, encoding="utf-8").read()


def test_nunca_select_estrela_e_somente_leitura():
    for nome in dir(svc):
        if nome.startswith("SQL_"):
            assert "SELECT *" not in str(getattr(svc, nome)).upper(), nome
    for escrita in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ",
                    "ALTER ", "CREATE ", "GRANT "):
        assert escrita not in SERVICE_SRC.upper(), escrita
    assert "db.commit()" not in SERVICE_SRC


def test_seller_id_nao_sai_no_payload():
    from app.schemas.ml_full_stock import InventarioEstoque, MarcaEstoque
    assert "seller_id" not in InventarioEstoque.model_fields
    assert "seller_id" not in MarcaEstoque.model_fields


def test_inventory_id_nao_sai_no_payload_nem_e_selecionado():
    """API publica: o ID do inventario do ML nao viaja, nem derivado dele."""
    from app.schemas.ml_full_stock import InventarioEstoque
    assert "inventory_id" not in InventarioEstoque.model_fields
    select = str(svc.SQL_INVENTARIOS).split("FROM")[0]
    assert "inventory_id" not in select, "o ID nao pode nem chegar ao servico"
    # Na versao identificada: nem na CTE da 014 nem no SELECT principal.
    cte, principal = str(svc.SQL_INVENTARIOS_IDENTIFICADOS).split("\n    )", 1)
    assert "inventory_id" not in cte
    assert "inventory_id" not in principal.split("FROM")[0]
    # O ID do anuncio tambem nao viaja: so' SKU e titulo.
    assert "anuncio_ancora_item_id" not in InventarioEstoque.model_fields
    assert "anuncio_ancora_variation_id" not in InventarioEstoque.model_fields


def test_join_da_identificacao_e_1_para_1_por_construcao():
    """A CTE tem UMA linha por (marca, item) e o JOIN usa exatamente essa chave:
    o inventario nunca e' multiplicado por linhas/datas da 014."""
    sql = " ".join(str(svc.SQL_INVENTARIOS_IDENTIFICADOS).split())
    assert "SELECT DISTINCT ON (l.brand, l.item_id)" in sql
    assert "ORDER BY l.brand, l.item_id, l.ref_date DESC, l.seller_id" in sql
    assert "l.ref_date <= :ref_date" in sql and "l.marketplace = 'ml'" in sql
    assert ("LEFT JOIN item_014 p ON p.brand = i.brand "
            "AND p.item_id = i.anuncio_ancora_item_id") in sql
    assert sql.count("JOIN") == 1, "nenhum outro JOIN pode multiplicar linhas"
    assert "GROUP BY" not in sql


def _chaves_json(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _chaves_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _chaves_json(v)


def test_nao_reusa_ml_fulfillment():
    """Modalidade de envio NAO e' estoque."""
    assert "ml_fulfillment" not in SERVICE_SRC.replace(
        "`/performance/ml-fulfillment`", "")


def test_motivos_de_ausencia_negam_estoque_zero():
    for motivo in (svc.MOTIVO_FATO_INEXISTENTE, svc.MOTIVO_SEM_FOTOGRAFIA,
                   svc.MOTIVO_SEM_FOTOGRAFIA_NA_DATA):
        assert "não significa estoque zero" in motivo


def test_marca_invalida_e_recusada_sem_ecoar():
    with pytest.raises(svc.FiltroInvalido) as exc:
        svc.validar_marcas(["<script>"])
    assert "<script>" not in exc.value.mensagem_segura
    assert "barbours" in exc.value.mensagem_segura
    assert svc.validar_marcas(None) == list(svc.EXPECTED_BRANDS)
    assert svc.validar_marcas([" Lescent ", "LESCENT", "barbours"]) == [
        "lescent", "barbours"]


# ===========================================================================
# 2. Router — flag e indisponibilidade (sessao proibida / falsa)
# ===========================================================================

class _SessaoProibida:
    def __getattr__(self, nome):
        raise AssertionError(f"o endpoint tocou o banco (.{nome}) com a flag desligada")


@pytest.fixture
def cliente_factory(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.database import get_db
    from app.main import app
    from app.routers import performance as rota

    monkeypatch.setattr(rota, "today_brt", lambda: HOJE)

    def fabricar(sessao, ligada=True):
        monkeypatch.setattr(settings, "ml_full_stock_api_enabled", ligada)
        app.dependency_overrides[get_db] = lambda: sessao
        return TestClient(app)

    yield fabricar
    app.dependency_overrides.pop(get_db, None)


def test_flag_desligada_e_o_padrao():
    from app.config import Settings
    assert Settings.model_fields["ml_full_stock_api_enabled"].default is False


def test_flag_desligada_responde_200_sem_tocar_o_banco(cliente_factory):
    r = cliente_factory(_SessaoProibida(), ligada=False).get(ROTA)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "unavailable"
    assert corpo["marketplace"] == "ml"
    assert corpo["motivo_tecnico"] == "feature_flag_desligada"
    assert "indicadores" not in corpo and "inventarios" not in corpo


def test_flag_e_independente_das_outras(cliente_factory, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "shopee_fbs_stock_enabled", True)
    monkeypatch.setattr(settings, "shopee_fbs_enabled", True)
    r = cliente_factory(_SessaoProibida(), ligada=False).get(ROTA)
    assert r.json()["motivo_tecnico"] == "feature_flag_desligada"


def test_tabelas_ausentes_e_indisponivel_sem_500(cliente_factory):
    sessao = SessaoFalsa(tabelas=(False, False, False))
    r = cliente_factory(sessao).get(ROTA)
    assert r.status_code == 200
    assert r.json()["motivo_tecnico"] == "fato_inexistente"
    assert "não significa estoque zero" in r.json()["unavailable_reason"]
    # Depois de detectar a ausencia, nenhuma consulta as tabelas.
    assert sessao.comandos == ["rollback", "SQL_SNAPSHOT_COERENTE",
                               "SQL_FATOS_EXISTEM"]


def test_meia_migration_tambem_e_indisponivel(cliente_factory):
    r = cliente_factory(SessaoFalsa(tabelas=(True, False, True))).get(ROTA)
    assert r.json()["motivo_tecnico"] == "fato_inexistente"


def test_tabelas_vazias_sao_sem_fotografia(cliente_factory):
    r = cliente_factory(SessaoFalsa(inventarios=[], marcas=[], status=[])).get(ROTA)
    assert r.status_code == 200
    assert r.json()["motivo_tecnico"] == "sem_fotografia_publicada"


def test_data_sem_fotografia_e_indisponivel(cliente_factory):
    r = cliente_factory(SessaoFalsa()).get(ROTA, params={"ref_date": "2026-10-24"})
    assert r.status_code == 200
    assert r.json()["motivo_tecnico"] == "sem_fotografia_na_data"


def test_data_futura_e_422(cliente_factory):
    r = cliente_factory(_SessaoProibida()).get(ROTA, params={"ref_date": "2026-10-27"})
    assert r.status_code == 422


def test_marca_invalida_e_422_sem_ecoar(cliente_factory):
    payload = "<script>alert(1)</script>"
    r = cliente_factory(SessaoFalsa()).get(ROTA, params={"brands": payload})
    assert r.status_code == 422
    assert payload not in r.text
    assert "barbours" in r.text


def test_rota_ml_fulfillment_continua_independente(cliente_factory):
    from app.routers import performance as rota
    src = io.open(RAIZ / "app" / "routers" / "performance.py", encoding="utf-8").read()
    trecho = src.split('"/ml-full-estoque"')[1]
    assert "ml_fulfillment" not in trecho.split("@router")[0].replace(
        "`/ml-fulfillment`", "")
    assert hasattr(rota, "get_ml_fulfillment_block")


def test_rota_nova_nao_exigiu_editar_main():
    main_src = io.open(RAIZ / "app" / "main.py", encoding="utf-8").read()
    assert "ml_full_stock" not in main_src


# ===========================================================================
# 3. Regras de negocio com sessao falsa
# ===========================================================================

def test_snapshot_read_only_e_o_primeiro_comando():
    sessao = SessaoFalsa()
    _bloco_falso(sessao)
    assert sessao.comandos[:3] == ["rollback", "SQL_SNAPSHOT_COERENTE",
                                   "SQL_FATOS_EXISTEM"]
    assert sessao.comandos.count("rollback") == 1


def test_inventario_compartilhado_conta_uma_vez():
    r = _bloco_falso()
    b1 = [i for i in r.inventarios if _chave(i) == CHAVE["INV-B1"]]
    assert len(b1) == 1, "inventario compartilhado repetido por anuncio"
    assert b1[0].anuncios == 3 and b1[0].compartilhado is True
    assert b1[0].disponivel == 10
    barbours = next(m for m in r.marcas if m.brand == "barbours")
    assert barbours.disponivel_full == 10, "10, nunca 10 x 3 anuncios"
    assert r.indicadores.disponivel_full == 570
    assert r.indicadores.inventarios_compartilhados == 3


def test_agregado_que_somou_por_anuncio_e_recusado():
    """Se a fato de marca trouxesse 30 (10 x 3 anuncios), a rota nao escolhe."""
    marcas = [dict(m) for m in MARCAS]
    marcas[0]["disponivel_full"] = 30
    with pytest.raises(svc.MLFullEstoqueContractError):
        _bloco_falso(SessaoFalsa(marcas=marcas))


def test_sem_leitura_fica_nulo_e_declarado():
    r = _bloco_falso()
    b3 = _linha(r, "INV-B3")
    assert b3.classificacao_torre == "SEM_LEITURA"
    assert (b3.disponivel, b3.nao_disponivel, b3.transfer) == (None, None, None)
    assert b3.cobertura_torre_dias is None
    assert r.cobertura.leitura.inventarios_sem_leitura == 2
    assert r.cobertura.leitura.inventarios_lidos == 5
    assert r.indicadores.total_parcial is True
    assert any("sem leitura" in m for m in r.indicadores.motivos_total_parcial)
    # Zero MEDIDO continua zero.
    b2 = _linha(r, "INV-B2")
    assert b2.disponivel == 0


def test_marca_sem_full_lido_tem_disponivel_nulo():
    r = _bloco_falso()
    rit = next(m for m in r.marcas if m.brand == "rituaria")
    assert rit.tem_fotografia_no_dia is True
    assert rit.disponivel_full is None, "nenhum Full lido: ausencia, nao 0"
    assert rit.leitura.inventarios_sem_leitura == 1
    assert any("rituaria" in m for m in r.indicadores.motivos_total_parcial)


def test_marca_sem_fotografia_no_dia_e_ausencia():
    r = _bloco_falso()
    assert r.cobertura.marcas_sem_fotografia == ["kokeshi"]
    assert r.cobertura.marcas_com_fotografia == ["barbours", "lescent", "rituaria"]
    kok = next(m for m in r.marcas if m.brand == "kokeshi")
    assert kok.tem_fotografia_no_dia is False
    assert kok.ultima_fotografia == ANTERIOR
    assert kok.disponivel_full is None and kok.inventarios is None
    # O 7 de kokeshi da fotografia anterior NAO entra no total do dia.
    assert r.indicadores.disponivel_full == 570
    assert any("kokeshi" in m for m in r.indicadores.motivos_total_parcial)


def test_indicadores_do_escopo():
    i = _bloco_falso().indicadores
    assert (i.inventarios, i.inventarios_full, i.inventarios_full_lidos) == (7, 6, 4)
    assert (i.inventarios_fora_do_full, i.inventarios_lidos,
            i.inventarios_sem_leitura) == (1, 5, 2)
    assert (i.inventarios_ruptura, i.inventarios_baixo, i.inventarios_excesso,
            i.inventarios_suficientes) == (1, 1, 1, 1)
    assert i.inventarios_exigem_acao == 2
    assert (i.nao_disponivel_full, i.transfer_full) == (3, 1)


def test_fora_do_full_so_lista_quando_pedido():
    r = _bloco_falso()
    assert CHAVE["INV-B4"] not in {_chave(i) for i in r.inventarios}
    assert r.total_no_filtro == 6
    r2 = _bloco_falso(incluir_fora_do_full=True)
    assert CHAVE["INV-B4"] in {_chave(i) for i in r2.inventarios}
    assert r2.indicadores == r.indicadores, "cartoes nao seguem a listagem"


def test_frescor_e_status():
    r = _bloco_falso()
    assert r.frescor.ref_date == REF
    assert r.frescor.ultima_fotografia_disponivel == REF
    assert r.frescor.dias_desde_a_fotografia == 1
    assert r.frescor.published_at == PUBLICADO
    assert r.frescor.horas_desde_a_publicacao == 6.0
    assert r.frescor.publicacao_agendada is False
    barbours = next(m for m in r.marcas if m.brand == "barbours")
    assert [(s.status_raw, s.quantidade, s.inventarios)
            for s in sorted(barbours.nao_disponivel_por_status,
                            key=lambda s: s.status_raw)] == [
        ("damaged", 2, 1), ("transfer", 1, 1)]


def test_filtro_de_marca_e_data_anterior():
    r = _bloco_falso(brands=["kokeshi"], ref_date=ANTERIOR)
    assert r.frescor.ref_date == ANTERIOR
    assert r.indicadores.disponivel_full == 7
    assert r.indicadores.total_parcial is False
    assert [_chave(i) for i in r.inventarios] == [CHAVE["INV-K1"]]

    r2 = _bloco_falso(brands=["lescent"])
    assert r2.cobertura.marcas_esperadas == ["lescent"]
    assert r2.indicadores.disponivel_full == 560
    assert r2.indicadores.total_parcial is False


def test_truncamento_e_declarado():
    r = _bloco_falso(limite=2)
    assert len(r.inventarios) == 2 and r.total_no_filtro == 6
    assert r.truncado is True
    assert any("TRUNCADA" in lim for lim in r.limitacoes)


def test_classificacao_fora_do_dominio_quebra_o_contrato(cliente_factory):
    inv = [dict(i) for i in INVENTARIOS]
    inv[4]["classificacao_torre"] = "INVENTADA"
    r = cliente_factory(SessaoFalsa(inventarios=inv)).get(ROTA)
    assert r.status_code == 503
    assert "INVENTADA" not in r.text


def test_inventario_sem_agregado_de_marca_quebra_o_contrato():
    marcas = [m for m in MARCAS if m["brand"] != "lescent"]
    with pytest.raises(svc.MLFullEstoqueContractError):
        _bloco_falso(SessaoFalsa(marcas=marcas))


def test_resposta_ok_pelo_endpoint(cliente_factory):
    r = cliente_factory(SessaoFalsa()).get(ROTA)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "ok" and corpo["marketplace"] == "ml"
    assert corpo["indicadores"]["disponivel_full"] == 570
    kok = next(m for m in corpo["marcas"] if m["brand"] == "kokeshi")
    assert kok["disponivel_full"] is None
    assert "seller_id" not in r.text and "S-B" not in r.text


@pytest.mark.parametrize("fora_do_full", [False, True])
def test_resposta_ok_nao_expoe_id_de_inventario(cliente_factory, fora_do_full):
    """Procura no CORPO INTEIRO serializado: nem a chave, nem nenhum ID."""
    r = cliente_factory(SessaoFalsa()).get(
        ROTA, params={"incluir_fora_do_full": str(fora_do_full).lower()})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "ok"
    # Sanidade: as linhas estao la' (senao o teste passaria por vazio).
    assert len(corpo["inventarios"]) == (7 if fora_do_full else 6)
    assert '"inventory_id"' not in r.text
    assert "inventory_id" not in set(_chaves_json(corpo))
    for inv_id in IDS_FICTICIOS:
        assert inv_id not in r.text, f"{inv_id} vazou na resposta publica"
    assert "INV-" not in r.text
    assert [i["posicao"] for i in corpo["inventarios"]] == list(
        range(1, len(corpo["inventarios"]) + 1))


# ===========================================================================
# 3b. T-0051 — "Sem estoque Full" x "saldo disponivel 0" x ausencia
# ===========================================================================
#
# Cenario LIMPO (sem SEM_LEITURA), para que o total parcial so' dependa da
# marca em teste. Cada marca entra num de quatro estados:
#   "medida"      -> 1 inventario Full lido, disponivel 100;
#   "saldo_zero"  -> 2 inventarios Full lidos, disponivel 0, nao disponivel 15;
#   "sem_estoque" -> linha de marca com 0 inventarios, NULLs e linhagem; e
#                    nenhuma linha de inventario (enumeracao completa, zero);
#   "ausente"     -> nenhuma linha (nao coletada / nao publicada / stale).

def _cenario(**estados):
    inventarios, marcas = [], []
    for brand, estado in estados.items():
        seller = f"S-{brand}"
        if estado == "medida":
            inventarios.append(_inv(brand, seller, f"INV-{brand}-1", disp=100,
                                    nao=0, tr=0, un=28, media="1.00",
                                    cob="100.0", classe="EXCESSO_CANDIDATO"))
            marcas.append(_marca(brand, REF, 1, 1, 0, 100, 0, 0, 28,
                                 cob_aloc="1.0000"))
        elif estado == "saldo_zero":
            inventarios.append(_inv(brand, seller, f"INV-{brand}-1", disp=0,
                                    nao=12, tr=4, un=28, media="1.00",
                                    cob="0.0", classe="RUPTURA_CANDIDATA"))
            inventarios.append(_inv(brand, seller, f"INV-{brand}-2", disp=0,
                                    nao=3, tr=0))
            marcas.append(_marca(brand, REF, 2, 2, 0, 0, 15, 4, 28,
                                 cob_aloc="1.0000"))
        elif estado == "sem_estoque":
            marcas.append(_marca(brand, REF, 0, 0, 0, None, None, None, 0))
        else:
            assert estado == "ausente", estado
    return SessaoFalsa(inventarios=inventarios, marcas=marcas, status=[])


def _m(resposta, brand):
    return next(m for m in resposta.marcas if m.brand == brand)


def test_sem_estoque_full_e_estado_proprio_que_conta_zero():
    """Estado 1. CONTRAPROVA: no codigo antigo isto levantava
    `MLFullEstoqueContractError("... agregado sem inventarios no dia")` e a
    tela inteira caia em 503."""
    r = _bloco_falso(_cenario(barbours="medida", kokeshi="sem_estoque",
                              lescent="medida", rituaria="medida"))
    kok = _m(r, "kokeshi")
    assert kok.estado == "sem_estoque_full"
    assert kok.tem_fotografia_no_dia is True
    assert kok.disponivel_full == 0, "enumeracao completa e zero: 0 medido"
    assert (kok.nao_disponivel_full, kok.transfer_full) == (0, 0)
    assert kok.inventarios == 0 and kok.inventarios_full_lidos == 0
    assert kok.leitura.inventarios == 0
    assert "kokeshi" in r.cobertura.marcas_com_fotografia
    assert r.cobertura.marcas_sem_fotografia == []
    assert r.indicadores.total_parcial is False
    assert r.indicadores.motivos_total_parcial == []
    assert r.indicadores.disponivel_full == 300
    assert {m.brand: m.estado for m in r.marcas} == {
        "barbours": "com_inventario", "kokeshi": "sem_estoque_full",
        "lescent": "com_inventario", "rituaria": "com_inventario"}


def test_fixture_sem_estoque_tem_o_formato_do_publicador():
    """Formato confirmado pelo publicador (T-0052): contagens 0, quantidades
    NULL (o CHECK proibe 0 com lidos = 0), linhagem presente, sem captured_at,
    e nenhuma linha de inventario nem de status. O 0 da API vem da REGRA."""
    sessao = _cenario(kokeshi="sem_estoque")
    linha = next(m for m in sessao.marcas if m["brand"] == "kokeshi")
    assert (linha["inventarios"], linha["inventarios_full_lidos"],
            linha["inventarios_compartilhados"]) == (0, 0, 0)
    assert (linha["disponivel_full"], linha["nao_disponivel_full"],
            linha["transfer_full"]) == (None, None, None)
    assert linha["source_run_id"] and linha["published_at"] is not None
    assert "captured_at" not in linha
    assert not [i for i in sessao.inventarios if i["brand"] == "kokeshi"]
    assert not [s for s in sessao.status if s["brand"] == "kokeshi"]
    assert _m(_bloco_falso(sessao), "kokeshi").disponivel_full == 0


def test_saldo_disponivel_zero_e_com_inventario():
    """Estado 2: ha inventario fisico, so' o disponivel e' 0."""
    r = _bloco_falso(_cenario(barbours="medida", kokeshi="saldo_zero",
                              lescent="medida", rituaria="medida"))
    kok = _m(r, "kokeshi")
    assert kok.estado == "com_inventario"
    assert kok.disponivel_full == 0
    assert kok.nao_disponivel_full == 15 and kok.transfer_full == 4
    assert kok.inventarios_full_lidos == 2
    assert r.indicadores.total_parcial is False
    assert r.indicadores.disponivel_full == 300
    assert r.indicadores.nao_disponivel_full == 15


def test_marca_ausente_continua_ausencia_sem_estado():
    """Estado 3: igual a antes -- None, sem fotografia e total parcial."""
    r = _bloco_falso(_cenario(barbours="medida", lescent="medida",
                              rituaria="medida"))
    kok = _m(r, "kokeshi")
    assert kok.estado is None
    assert kok.tem_fotografia_no_dia is False
    assert kok.disponivel_full is None
    assert r.cobertura.marcas_sem_fotografia == ["kokeshi"]
    assert r.indicadores.total_parcial is True
    assert r.indicadores.motivos_total_parcial == [
        "Sem fotografia no dia: kokeshi."]


def test_todas_sem_estoque_full_total_zero_nao_parcial():
    r = _bloco_falso(_cenario(barbours="sem_estoque", kokeshi="sem_estoque",
                              lescent="sem_estoque", rituaria="sem_estoque"))
    assert {m.estado for m in r.marcas} == {"sem_estoque_full"}
    assert r.cobertura.marcas_com_fotografia == list(svc.EXPECTED_BRANDS)
    assert r.cobertura.marcas_sem_fotografia == []
    i = r.indicadores
    assert (i.disponivel_full, i.nao_disponivel_full, i.transfer_full) == (0, 0, 0)
    assert i.total_parcial is False and i.motivos_total_parcial == []
    assert i.inventarios == 0
    assert r.total_no_filtro == 0 and r.inventarios == []


def test_uma_ausente_e_outra_sem_estoque_e_parcial():
    """A marca sem estoque Full NAO entra no motivo do parcial; a ausente sim."""
    r = _bloco_falso(_cenario(kokeshi="sem_estoque", lescent="medida",
                              rituaria="medida"))
    assert _m(r, "kokeshi").estado == "sem_estoque_full"
    assert _m(r, "barbours").estado is None
    assert r.cobertura.marcas_sem_fotografia == ["barbours"]
    assert r.indicadores.total_parcial is True
    assert r.indicadores.motivos_total_parcial == [
        "Sem fotografia no dia: barbours."]
    assert r.indicadores.disponivel_full == 200

    # So' sem estoque + ausente: o total e' o que foi medido, e PARCIAL.
    r2 = _bloco_falso(_cenario(kokeshi="sem_estoque", lescent="sem_estoque"))
    assert r2.indicadores.disponivel_full == 0
    assert r2.indicadores.total_parcial is True
    assert r2.indicadores.motivos_total_parcial == [
        "Sem fotografia no dia: barbours, rituaria."]


def test_filtro_so_da_marca_sem_estoque_full():
    r = _bloco_falso(_cenario(kokeshi="sem_estoque", lescent="medida"),
                     brands=["kokeshi"])
    assert r.indicadores.disponivel_full == 0
    assert r.indicadores.total_parcial is False
    assert r.marcas[0].estado == "sem_estoque_full"


def _sem_estoque_com(**campos):
    marca = _marca("kokeshi", REF, 0, 0, 0, None, None, None, 0)
    marca.update(campos)
    return marca


@pytest.mark.parametrize("caso", [
    "agregado_n_sem_linhas",
    "agregado_zero_com_linhas",
    "zero_com_full_lidos",
    "zero_com_nao_disponivel",
    "zero_com_transfer",
    "zero_com_compartilhados",
    "zero_sem_source_run_id",
    "zero_sem_published_at",
    "zero_com_status",
])
def test_divergencia_marca_x_inventario_continua_503(cliente_factory, caso):
    sessao = _cenario(barbours="medida", lescent="medida", rituaria="medida")
    if caso == "agregado_n_sem_linhas":
        sessao.marcas.append(_marca("kokeshi", REF, 2, 2, 0, 9, 0, 0, 0))
    elif caso == "agregado_zero_com_linhas":
        sessao.marcas.append(_sem_estoque_com())
        sessao.inventarios.append(_inv("kokeshi", "S-K", "INV-K9", disp=5,
                                       nao=0, tr=0))
    elif caso == "zero_com_full_lidos":
        sessao.marcas.append(_sem_estoque_com(inventarios_full_lidos=1,
                                              disponivel_full=5))
    elif caso == "zero_com_nao_disponivel":
        sessao.marcas.append(_sem_estoque_com(nao_disponivel_full=0))
    elif caso == "zero_com_transfer":
        sessao.marcas.append(_sem_estoque_com(transfer_full=0))
    elif caso == "zero_com_compartilhados":
        sessao.marcas.append(_sem_estoque_com(inventarios_compartilhados=1))
    elif caso == "zero_sem_source_run_id":
        sessao.marcas.append(_sem_estoque_com(source_run_id=""))
    elif caso == "zero_sem_published_at":
        sessao.marcas.append(_sem_estoque_com(published_at=None))
    elif caso == "zero_com_status":
        # Status orfao: o JOIN de SQL_STATUS o esconderia; a contagem bruta nao.
        sessao.marcas.append(_sem_estoque_com())
        sessao.status = [{"ref_date": REF, "brand": "kokeshi", "seller_id": "S-K",
                          "inventory_id": "INV-K9", "status_raw": "damaged",
                          "quantidade": 1, "source_run_id": "run-ficticio"}]
    with pytest.raises(svc.MLFullEstoqueContractError):
        _bloco_falso(sessao)
    r = cliente_factory(sessao).get(ROTA)
    assert r.status_code == 503


def test_estado_pelo_endpoint(cliente_factory):
    r = cliente_factory(_cenario(kokeshi="sem_estoque", lescent="medida",
                                 rituaria="saldo_zero")).get(ROTA)
    assert r.status_code == 200
    estados = {m["brand"]: m["estado"] for m in r.json()["marcas"]}
    assert estados == {"barbours": None, "kokeshi": "sem_estoque_full",
                       "lescent": "com_inventario",
                       "rituaria": "com_inventario"}
    assert "source_run_id" not in r.text and "run-ficticio" not in r.text


def test_estado_tem_default_seguro_e_dominio_fechado():
    from pydantic import ValidationError
    from app.schemas.ml_full_stock import MarcaEstoque
    assert MarcaEstoque(brand="kokeshi", tem_fotografia_no_dia=False).estado is None
    with pytest.raises(ValidationError):
        MarcaEstoque(brand="kokeshi", tem_fotografia_no_dia=True,
                     estado="nao_opera_full")


def test_fixture_antigo_marca_sem_full_lido_continua_com_inventario():
    """Inventario presente mas nenhum Full lido NAO e' "sem estoque Full"."""
    r = _bloco_falso()
    assert _m(r, "rituaria").estado == "com_inventario"
    assert _m(r, "rituaria").disponivel_full is None
    assert _m(r, "kokeshi").estado is None


def test_029_admite_a_linha_de_marca_sem_estoque_full_sem_migration():
    """O sinal do estado 1 e' `inventarios = 0` com disponivel NULL: o CHECK
    `(inventarios_full_lidos > 0) = (disponivel_full IS NOT NULL)` fica
    FALSE = FALSE. Nenhuma coluna nova e' necessaria."""
    assert "inventarios                      INTEGER NOT NULL CHECK (inventarios >= 0)" in MIGRATION_SRC
    assert "(inventarios_full_lidos > 0) = (disponivel_full IS NOT NULL)" in MIGRATION_SRC
    assert "nao_disponivel_full              BIGINT  CHECK" in MIGRATION_SRC
    assert "source_run_id                    TEXT    NOT NULL" in MIGRATION_SRC


# ===========================================================================
# 3c. Identificacao (T-0018): SKU, titulo, +N anuncios
# ===========================================================================

def test_identificacao_nao_duplica_inventario_com_varias_linhas_na_014():
    sessao = SessaoFalsa()
    r = _bloco_falso(sessao)
    assert "SQL_INVENTARIOS_IDENTIFICADOS" in sessao.comandos
    assert "SQL_INVENTARIOS" not in sessao.comandos
    assert len(r.inventarios) == r.total_no_filtro == 6
    b1 = [i for i in r.inventarios if _chave(i) == CHAVE["INV-B1"]]
    assert len(b1) == 1, "5 linhas do item na 014 viraram mais de um inventario"
    # Mais recente <= ref_date; data futura nao vence; conta menor desempata;
    # outra marca com o mesmo item_id nao casa.
    assert (b1[0].sku, b1[0].titulo) == ("SKU-B1", "Shampoo B1 300ml")
    assert b1[0].anuncios_extras == 2 and b1[0].ancora_e_variacao is False
    assert b1[0].disponivel == 10
    assert r.indicadores.disponivel_full == 570


def test_identificacao_em_data_anterior_usa_a_014_daquela_data():
    inv = [dict(i) for i in INVENTARIOS] + [
        _inv("barbours", "S-B", "INV-B1", disp=9, nao=0, tr=0, classe="SEM_GIRO_CANDIDATO",
             ref=ANTERIOR, ancora="MLB-B1", extras=2)]
    marcas = MARCAS + [_marca("barbours", ANTERIOR, 1, 1, 0, 9, 0, 0, 0)]
    r = _bloco_falso(SessaoFalsa(inventarios=inv, marcas=marcas),
                     brands=["barbours"], ref_date=ANTERIOR)
    assert [(i.sku, i.titulo) for i in r.inventarios] == [("SKU-ANTERIOR", "Titulo anterior")]


def test_identificacao_ausente_e_null_explicito():
    r = _bloco_falso()
    b2 = _linha(r, "INV-B2")             # ancora sem linha na 014
    assert (b2.sku, b2.titulo, b2.anuncios_extras) == (None, None, 0)
    assert b2.ancora_e_variacao is False
    b3 = _linha(r, "INV-B3")             # publicado antes da 038: sem ancora
    assert (b3.sku, b3.titulo, b3.anuncios_extras, b3.ancora_e_variacao) == (
        None, None, None, None)
    l1 = _linha(r, "INV-L1")             # variacao: SKU ausente, titulo do item
    assert (l1.sku, l1.titulo, l1.ancora_e_variacao) == (None, "Perfume L1 50ml", True)
    assert svc.LIMITACAO_IDENTIFICACAO in r.limitacoes
    corpo = r.model_dump(mode="json")
    linha = next(i for i in corpo["inventarios"] if i["brand"] == "barbours"
                 and i["classificacao_torre"] == "RUPTURA_CANDIDATA")
    assert "sku" in linha and linha["sku"] is None, "null explicito, nao chave ausente"


def test_identificacao_vazia_vira_null():
    precos = [dict(PRECOS[2], seller_sku="   ", listing_title=" ")]
    r = _bloco_falso(SessaoFalsa(precos=precos))
    b1 = _linha(r, "INV-B1")
    assert (b1.sku, b1.titulo) == (None, None)


@pytest.mark.parametrize("identificacao", [(False, True), (True, False), (False, False)])
def test_sem_038_ou_sem_014_lista_como_antes(identificacao):
    sessao = SessaoFalsa(identificacao=identificacao)
    r = _bloco_falso(sessao)
    assert "SQL_INVENTARIOS" in sessao.comandos
    assert "SQL_INVENTARIOS_IDENTIFICADOS" not in sessao.comandos
    assert len(r.inventarios) == 6 and r.indicadores.disponivel_full == 570
    assert all((i.sku, i.titulo, i.anuncios_extras, i.ancora_e_variacao)
               == (None, None, None, None) for i in r.inventarios)
    assert svc.LIMITACAO_SEM_IDENTIFICACAO in r.limitacoes
    assert svc.LIMITACAO_IDENTIFICACAO not in r.limitacoes


def test_checagem_de_identificacao_pede_as_tres_colunas_da_038():
    import re
    texto = io.open(MIGRATION_038_PATH, encoding="utf-8").read()
    for col in svc.COLUNAS_ANCORA:
        assert f"ADD COLUMN {col} " in texto, col
    assert len(svc.COLUNAS_ANCORA) == 3
    sql = str(svc.SQL_IDENTIFICACAO_DISPONIVEL)
    assert "information_schema.columns" in sql and "has_table_privilege" in sql
    assert re.search(r"to_regclass\(:precos\)", sql)


# ===========================================================================
# 4. Integracao contra PostgreSQL real, com o DDL da migration 029
# ===========================================================================

DSN = os.getenv("TEST_PG_DSN")
pg = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN nao configurado")


def _ddl_029():
    """DDL da PROPRIA 029. `apps/api/alembic/` sombreia o pacote alembic, entao
    `from alembic import op` e' satisfeito com um substituto inerte (o mesmo
    recurso de `test_expedicao_migration.py`); so' `_upgrade_sql()` e lido."""
    from unittest import mock
    import alembic
    spec = importlib.util.spec_from_file_location("_mig029", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.object(alembic, "op", object(), create=True):
        spec.loader.exec_module(mod)
    return mod._upgrade_sql(), mod.DOWNGRADE_SQL


def _sql_da_migration(caminho, funcao):
    """Comandos que `upgrade()`/`downgrade()` da migration passariam ao op."""
    from unittest import mock
    import alembic

    class _Op:
        def __init__(self):
            self.sqls = []

        def execute(self, sql):
            self.sqls.append(sql)

    op = _Op()
    spec = importlib.util.spec_from_file_location(f"_mig_{caminho.stem}", caminho)
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.object(alembic, "op", op, create=True):
        spec.loader.exec_module(mod)
        getattr(mod, funcao)()
    return op.sqls


@pytest.fixture
def engine():
    from sqlalchemy import create_engine
    eng = create_engine(DSN, future=True)
    yield eng
    eng.dispose()


def _recriar(engine, com_tabelas=True, inventarios=INVENTARIOS, marcas=MARCAS,
             status=STATUS, com_038=True, precos=PRECOS):
    """Estado COMMITADO: o servico comeca com rollback. DDL das PROPRIAS
    migrations 029, 038 (colunas do ancora) e 014 (SKU/titulo)."""
    from sqlalchemy import text as _t
    upgrade, downgrade = _ddl_029()
    if not com_038:
        inventarios = [{k: v for k, v in i.items()
                        if k not in svc.COLUNAS_ANCORA} for i in inventarios]
    with engine.begin() as conn:
        conn.execute(_t("CREATE SCHEMA IF NOT EXISTS marts"))
        for sql in downgrade + _sql_da_migration(MIGRATION_014_PATH, "downgrade"):
            conn.execute(_t(sql))
        if not com_tabelas:
            return
        for sql in upgrade + _sql_da_migration(MIGRATION_014_PATH, "upgrade"):
            conn.execute(_t(sql))
        if com_038:
            for sql in _sql_da_migration(MIGRATION_038_PATH, "upgrade"):
                conn.execute(_t(sql))
        for tabela, linhas in ((svc.INVENTORY_TABLE, inventarios),
                               (svc.STATUS_TABLE, status),
                               (svc.BRAND_TABLE, marcas),
                               (svc.PRICE_TABLE, precos)):
            for linha in linhas:
                cols = ", ".join(linha)
                vals = ", ".join(f":{c}" for c in linha)
                conn.execute(_t(f"INSERT INTO {tabela} ({cols}) VALUES ({vals})"), linha)


@pytest.fixture
def sessao(engine):
    from sqlalchemy.orm import sessionmaker
    s = sessionmaker(bind=engine, future=True)()
    yield s
    s.rollback()
    s.close()


def _bloco(sessao, **kw):
    return svc.get_ml_full_estoque_block(sessao, hoje=HOJE, agora=AGORA, **kw)


@pg
def test_pg_029_nao_aplicada_e_indisponivel(engine, sessao):
    _recriar(engine, com_tabelas=False)
    with pytest.raises(svc.FatoInexistente):
        _bloco(sessao)


@pg
def test_pg_tabelas_vazias_sao_sem_fotografia(engine, sessao):
    _recriar(engine, inventarios=[], marcas=[], status=[])
    with pytest.raises(svc.SemFotografiaPublicada):
        _bloco(sessao)


@pg
def test_pg_dados_normais_batem_com_a_sessao_falsa(engine, sessao):
    """O mesmo conjunto, pelo SQL real, da' os mesmos numeros."""
    _recriar(engine)
    real = _bloco(sessao)
    falso = _bloco_falso()
    assert real.indicadores == falso.indicadores
    assert real.cobertura == falso.cobertura
    assert real.frescor == falso.frescor
    assert {m.brand: m.disponivel_full for m in real.marcas} == {
        "barbours": 10, "kokeshi": None, "lescent": 560, "rituaria": None}


@pg
def test_pg_ordem_operacional(engine, sessao):
    _recriar(engine)
    r = _bloco(sessao)
    assert [_chave(i) for i in r.inventarios] == [CHAVE[k] for k in (
        "INV-B2", "INV-B1", "INV-B3", "INV-R1", "INV-L1", "INV-L2")]
    # A chave da tela e' o ordinal na resposta, 1..n na ordem operacional.
    assert [i.posicao for i in r.inventarios] == [1, 2, 3, 4, 5, 6]


@pg
def test_pg_compartilhado_conta_uma_vez(engine, sessao):
    _recriar(engine)
    r = _bloco(sessao)
    assert [_chave(i) for i in r.inventarios].count(CHAVE["INV-B1"]) == 1
    assert next(m for m in r.marcas if m.brand == "barbours").disponivel_full == 10
    assert r.indicadores.disponivel_full == 570


@pg
def test_pg_agregado_por_anuncio_e_recusado(engine, sessao):
    marcas = [dict(m) for m in MARCAS]
    marcas[0]["disponivel_full"] = 30
    _recriar(engine, marcas=marcas)
    with pytest.raises(svc.MLFullEstoqueContractError):
        _bloco(sessao)


@pg
def test_pg_sem_leitura_e_marca_sem_fotografia(engine, sessao):
    _recriar(engine)
    r = _bloco(sessao)
    r1 = _linha(r, "INV-R1")
    assert (r1.disponivel, r1.nao_disponivel, r1.transfer) == (None, None, None)
    assert r.cobertura.marcas_sem_fotografia == ["kokeshi"]
    kok = next(m for m in r.marcas if m.brand == "kokeshi")
    assert kok.ultima_fotografia == ANTERIOR and kok.inventarios is None


@pg
def test_pg_data_e_marca(engine, sessao):
    _recriar(engine)
    r = _bloco(sessao, brands=["kokeshi"], ref_date=ANTERIOR)
    assert r.indicadores.disponivel_full == 7
    with pytest.raises(svc.SemFotografiaNaData):
        _bloco(sessao, ref_date=date(2026, 10, 24))


@pg
def test_pg_fora_do_full_e_truncamento(engine, sessao):
    _recriar(engine)
    r = _bloco(sessao, incluir_fora_do_full=True)
    assert _chave(r.inventarios[-1]) == CHAVE["INV-B4"]
    assert r.inventarios[-1].posicao == 7
    assert r.total_no_filtro == 7
    r2 = _bloco(sessao, limite=2)
    assert r2.truncado is True and len(r2.inventarios) == 2


@pg
def test_pg_transacao_e_read_only(engine, sessao):
    from sqlalchemy import text as _t
    _recriar(engine)
    _bloco(sessao)
    with pytest.raises(Exception) as exc:
        sessao.execute(_t(f"DELETE FROM {svc.BRAND_TABLE}"))
    assert "read-only" in str(exc.value).lower()


@pg
def test_pg_029_admite_sem_estoque_full(engine, sessao):
    """A linha de marca com 0 inventarios e NULLs passa nos CHECKs reais."""
    marcas = [dict(m) for m in MARCAS if m["brand"] != "kokeshi"]
    marcas.append(_marca("kokeshi", REF, 0, 0, 0, None, None, None, 0))
    inventarios = [i for i in INVENTARIOS if i["brand"] != "kokeshi"]
    _recriar(engine, inventarios=inventarios, marcas=marcas)
    r = _bloco(sessao)
    kok = next(m for m in r.marcas if m.brand == "kokeshi")
    assert kok.estado == "sem_estoque_full" and kok.disponivel_full == 0
    assert "kokeshi" not in r.cobertura.marcas_sem_fotografia
    assert r.indicadores.disponivel_full == 570


@pg
def test_pg_endpoint_ponta_a_ponta(engine, sessao, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.database import get_db
    from app.main import app
    from app.routers import performance as rota

    _recriar(engine)
    monkeypatch.setattr(settings, "ml_full_stock_api_enabled", True)
    monkeypatch.setattr(rota, "today_brt", lambda: HOJE)
    app.dependency_overrides[get_db] = lambda: sessao
    try:
        r = TestClient(app).get(ROTA, params=[("brands", "barbours"),
                                              ("brands", "rituaria")])
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["indicadores"]["disponivel_full"] == 10
    assert corpo["cobertura"]["marcas_sem_fotografia"] == []
    assert corpo["indicadores"]["total_parcial"] is True
    assert "S-B" not in r.text
    assert corpo["inventarios"], "sem linhas o teste de vazamento e' vazio"
    assert '"inventory_id"' not in r.text
    assert "inventory_id" not in set(_chaves_json(corpo))
    for inv_id in IDS_FICTICIOS:
        assert inv_id not in r.text, f"{inv_id} vazou na resposta publica"


@pg
def test_pg_identificacao_nao_duplica_e_bate_com_a_sessao_falsa(engine, sessao):
    """SQL REAL: 5 linhas do mesmo item na 014 e o inventario aparece uma vez."""
    _recriar(engine)
    real = _bloco(sessao, incluir_fora_do_full=True)
    falso = _bloco_falso(incluir_fora_do_full=True)
    assert len(real.inventarios) == real.total_no_filtro == 7
    assert [_chave(i) for i in real.inventarios].count(CHAVE["INV-B1"]) == 1
    ident = lambda r: {_chave(i): (i.sku, i.titulo, i.anuncios_extras, i.ancora_e_variacao)
                       for i in r.inventarios}
    assert ident(real) == ident(falso)
    assert ident(real)[CHAVE["INV-B1"]] == ("SKU-B1", "Shampoo B1 300ml", 2, False)
    assert real.indicadores.disponivel_full == 570


@pg
def test_pg_sem_038_lista_como_antes(engine, sessao):
    _recriar(engine, com_038=False)
    r = _bloco(sessao)
    assert len(r.inventarios) == 6
    assert all(i.sku is None and i.anuncios_extras is None for i in r.inventarios)
    assert svc.LIMITACAO_SEM_IDENTIFICACAO in r.limitacoes


@pg
def test_pg_038_downgrade_remove_so_as_tres_colunas(engine):
    from sqlalchemy import text as _t
    _recriar(engine)
    cols = lambda conn: {r[0] for r in conn.execute(_t(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'marts' "
        "AND table_name = 'fact_ml_full_stock_inventory_daily'"))}
    with engine.begin() as conn:
        antes = cols(conn)
        n = conn.execute(_t(f"SELECT COUNT(*) FROM {svc.INVENTORY_TABLE}")).scalar()
        for sql in _sql_da_migration(MIGRATION_038_PATH, "downgrade"):
            conn.execute(_t(sql))
        depois = cols(conn)
        assert conn.execute(_t(f"SELECT COUNT(*) FROM {svc.INVENTORY_TABLE}")).scalar() == n
    assert antes - depois == set(svc.COLUNAS_ANCORA)
