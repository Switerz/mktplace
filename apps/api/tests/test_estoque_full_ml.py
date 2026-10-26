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
         cob=None, classe="SEM_DEMANDA_MEDIDA", ref=REF):
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
    }


INVENTARIOS = [
    # 🔑 INV-B1: UM inventario compartilhado por TRES anuncios. Estoque 10,
    # nunca 30.
    _inv("barbours", "S-B", "INV-B1", disp=10, nao=3, tr=1, anuncios=3,
         ativos=2, full=3, un=56, media="2.00", cob="5.0",
         classe="BAIXO_CANDIDATO"),
    # Ruptura medida: 0 de verdade.
    _inv("barbours", "S-B", "INV-B2", disp=0, nao=0, tr=0, un=28,
         media="1.00", cob="0.0", classe="RUPTURA_CANDIDATA"),
    # SEM_LEITURA: quantidades NULAS, nunca zero.
    _inv("barbours", "S-B", "INV-B3", read_status="NAO_ENCONTRADO",
         anuncios=2, ativos=2, full=2, un=14, media="0.50",
         classe="SEM_LEITURA"),
    # Fora do Full: contexto.
    _inv("barbours", "S-B", "INV-B4", disp=40, nao=0, tr=0, eh_full=False,
         full=0, classe="FORA_DO_FULL"),
    _inv("lescent", "S-L", "INV-L1", disp=500, nao=0, tr=0, un=28,
         media="1.00", cob="500.0", classe="EXCESSO_CANDIDATO"),
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
                 tabelas=(True, True, True)):
        self.inventarios = inventarios
        self.marcas = marcas
        self.status = status
        self.tabelas = tabelas
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
        listaveis = [i for i in do_dia if p["incluir_fora_do_full"]
                     or i["classificacao_torre"] != "FORA_DO_FULL"]
        if nome == "SQL_TOTAL_NO_FILTRO":
            return _Resultado(escalar=len(listaveis))
        if nome == "SQL_INVENTARIOS":
            return _Resultado(listaveis[: p["limite"]])
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
    b1 = [i for i in r.inventarios if i.inventory_id == "INV-B1"]
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
    b3 = next(i for i in r.inventarios if i.inventory_id == "INV-B3")
    assert b3.classificacao_torre == "SEM_LEITURA"
    assert (b3.disponivel, b3.nao_disponivel, b3.transfer) == (None, None, None)
    assert b3.cobertura_torre_dias is None
    assert r.cobertura.leitura.inventarios_sem_leitura == 2
    assert r.cobertura.leitura.inventarios_lidos == 5
    assert r.indicadores.total_parcial is True
    assert any("sem leitura" in m for m in r.indicadores.motivos_total_parcial)
    # Zero MEDIDO continua zero.
    b2 = next(i for i in r.inventarios if i.inventory_id == "INV-B2")
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
    assert "INV-B4" not in {i.inventory_id for i in r.inventarios}
    assert r.total_no_filtro == 6
    r2 = _bloco_falso(incluir_fora_do_full=True)
    assert "INV-B4" in {i.inventory_id for i in r2.inventarios}
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
    assert [i.inventory_id for i in r.inventarios] == ["INV-K1"]

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


@pytest.fixture
def engine():
    from sqlalchemy import create_engine
    eng = create_engine(DSN, future=True)
    yield eng
    eng.dispose()


def _recriar(engine, com_tabelas=True, inventarios=INVENTARIOS, marcas=MARCAS,
             status=STATUS):
    """Estado COMMITADO: o servico comeca com rollback."""
    from sqlalchemy import text as _t
    upgrade, downgrade = _ddl_029()
    with engine.begin() as conn:
        conn.execute(_t("CREATE SCHEMA IF NOT EXISTS marts"))
        for sql in downgrade:
            conn.execute(_t(sql))
        if not com_tabelas:
            return
        for sql in upgrade:
            conn.execute(_t(sql))
        for tabela, linhas in ((svc.INVENTORY_TABLE, inventarios),
                               (svc.STATUS_TABLE, status),
                               (svc.BRAND_TABLE, marcas)):
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
    assert [i.inventory_id for i in r.inventarios] == [
        "INV-B2", "INV-B1", "INV-B3", "INV-R1", "INV-L1", "INV-L2"]


@pg
def test_pg_compartilhado_conta_uma_vez(engine, sessao):
    _recriar(engine)
    r = _bloco(sessao)
    assert [i.inventory_id for i in r.inventarios].count("INV-B1") == 1
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
    r1 = next(i for i in r.inventarios if i.inventory_id == "INV-R1")
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
    assert r.inventarios[-1].inventory_id == "INV-B4"
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
