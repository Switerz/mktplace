"""Gate FULL-SOURCE-3 — tela de Estoque Full (FBS) da Shopee.

O QUE ESTE ARQUIVO PROTEGE
---------------------------
Uma regra acima de todas: **ausencia nunca vira zero**. Um zero na tela diz
"medimos e nao ha estoque" e manda a operacao repor; ausencia diz "nao
sabemos". Os tres caminhos de ausencia (flag desligada, fato inexistente,
fotografia nunca publicada) tem teste proprio, e nenhum deles pode responder
com indicadores zerados.

POR QUE HA TESTE CONTRA POSTGRES REAL
--------------------------------------
Os testes com sessao falsa provam ORQUESTRACAO: que a flag corta antes do
banco, que a ordem dos comandos e' a certa, que `None` nao vira 0. Eles NAO
provam que o SQL existe como linguagem -- um fake devolve o que mandamos,
inclusive para SQL invalido. `ESCAPE`, `to_regclass`, `unnest` de tres arrays
e `NULLS LAST` so' sao provados executando. Sem `TEST_PG_DSN` esses testes sao
PULADOS, nunca falseados.
"""
from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path

import pytest

from app.services import shopee_fbs_stock_service as svc

RAIZ = Path(__file__).resolve().parents[1]
SERVICE_SRC = io.open(
    RAIZ / "app" / "services" / "shopee_fbs_stock_service.py",
    encoding="utf-8").read()
MIGRATION_SRC = io.open(
    RAIZ / "alembic" / "versions" / "021_create_fact_shopee_fbs_stock_daily.py",
    encoding="utf-8").read()

REF = date(2026, 9, 23)
HOJE = date(2026, 9, 24)


# ===========================================================================
# 1. Contrato do codigo — propriedades que nao podem sumir num refactor
# ===========================================================================

def test_dominio_da_classificacao_espelha_a_migration():
    """Se a migration ganhar uma classificacao, o servico tem de saber dela.

    Divergir aqui nao e' cosmetico: `EstoqueFullContractError` transforma
    classificacao desconhecida em 503, entao uma classe nova publicada pela
    fato derrubaria a tela inteira ate' alguem atualizar esta tupla.
    """
    import re
    # A migration guarda os valores com aspas DUPLAS e so' os envolve em
    # aspas simples ao montar o CHECK em tempo de execucao.
    bloco = MIGRATION_SRC.split("CLASSIFICACOES = (")[1].split(")")[0]
    na_migration = set(re.findall(r'"([A-Z_]+)"', bloco))
    assert na_migration == set(svc.CLASSIFICACOES), (
        "dominio do servico divergiu do dominio da migration 021")


def test_colunas_lidas_existem_na_migration():
    """Allowlist do SELECT contra o DDL. Pega drift antes do runtime."""
    projecao = SERVICE_SRC.split("SQL_PRODUTOS = text(")[1].split("FROM")[0]
    for coluna in ("full_stock_saleable", "full_stock_total", "location_count",
                   "seller_stock_total", "reserved_stock",
                   "summary_available_stock", "units_sold_28d",
                   "days_with_sales_28d", "units_sold_28d_legado_com_unpaid",
                   "avg_daily_units_28d", "cobertura_torre_dias",
                   "classificacao_torre", "vinculo_vendas", "item_sku",
                   "item_name", "item_status", "is_kit"):
        assert coluna in projecao, f"{coluna} sumiu da projecao"
        assert coluna in MIGRATION_SRC, f"{coluna} nao existe na migration 021"


def _sqls_do_servico() -> dict:
    """Todas as constantes SQL do modulo, por nome.

    Medir o SQL COMPILADO, e nao o texto-fonte, importa: a docstring do modulo
    fala sobre `SELECT *` em prosa, e um teste que varresse o arquivo inteiro
    acusaria a propria documentacao.
    """
    return {nome: str(getattr(svc, nome))
            for nome in dir(svc) if nome.startswith("SQL_")}


def test_nunca_select_estrela():
    """`SELECT *` traria coluna nova sem revisao -- inclusive de pessoa."""
    for nome, sql in _sqls_do_servico().items():
        # A unica excecao e' `unnest`, onde `*` expande as colunas da propria
        # funcao (os tres arrays), nao as de uma tabela.
        limpo = sql.upper().replace("SELECT * FROM UNNEST", "")
        assert "SELECT *" not in limpo, f"{nome} usa SELECT *"


def test_nenhum_filtro_e_interpolado_no_sql():
    """Valor do usuario so' entra como parametro nomeado.

    As f-strings do modulo interpolam APENAS nomes de tabela e blocos de SQL
    constantes; nenhuma delas pode conter um nome de variavel de entrada.
    """
    for proibido in ("{busca}", "{termo}", "{brands}", "{accounts}",
                     "{classificacoes}", "{marcas}", "{contas_filtro}",
                     "{limite}", "{q}"):
        assert proibido not in SERVICE_SRC, (
            f"{proibido} interpolado no SQL e' injecao")


def test_servico_e_somente_leitura():
    for escrita in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ",
                    "ALTER ", "CREATE ", "GRANT "):
        assert escrita not in SERVICE_SRC.upper(), (
            f"{escrita.strip()} num caminho declarado read-only")
    assert "db.commit()" not in SERVICE_SRC


def test_snapshot_e_o_primeiro_comando_da_transacao():
    """`SET TRANSACTION` so' e' aceito ANTES de qualquer query."""
    corpo = SERVICE_SRC.split("def get_estoque_full_block")[1]
    i_set = corpo.index("SQL_SNAPSHOT_COERENTE")
    # `fato_disponivel` e `_carregar_por_cd` sao helpers: no corpo aparecem
    # pela CHAMADA, nao pelo nome da constante que executam.
    for consulta in ("fato_disponivel(db)", "SQL_ULTIMA_FOTOGRAFIA",
                     "SQL_INDICADORES", "SQL_TOTAL_NO_FILTRO", "SQL_PRODUTOS",
                     "_carregar_por_cd(db", "SQL_FRESCOR",
                     "SQL_CONTAS_OBSERVADAS"):
        assert i_set < corpo.index(consulta), (
            f"{consulta} nao pode ser executada antes de fixar o snapshot")
    # O rollback que garante transacao limpa PRECEDE o SET.
    assert corpo.index("db.rollback()") < i_set
    depois = corpo.split("SQL_SNAPSHOT_COERENTE", 1)[1]
    assert "db.rollback()" not in depois, "rollback no meio parte o snapshot"
    assert "SET SESSION" not in SERVICE_SRC.upper()


def test_limiares_sao_declarados_provisorios():
    assert "PROVISÓRIOS" in svc.LIMITACAO_LIMIARES
    assert str(svc.COBERTURA_BAIXA_DIAS) in svc.LIMITACAO_LIMIARES
    assert str(svc.COBERTURA_EXCESSO_DIAS) in svc.LIMITACAO_LIMIARES


def test_cobertura_e_declarada_como_calculo_nosso():
    """Nao pode ser apresentada como numero oficial da Shopee."""
    assert "NOSSO" in svc.LIMITACAO_COBERTURA_TORRE
    assert "NÃO reproduz nenhuma fórmula da Shopee" in (
        svc.LIMITACAO_COBERTURA_TORRE)


def test_motivos_de_ausencia_negam_estoque_zero_por_escrito():
    """A mensagem que chega ao usuario tem de desfazer a confusao."""
    for motivo in (svc.MOTIVO_FATO_INEXISTENTE, svc.MOTIVO_SEM_FOTOGRAFIA):
        assert "não significa estoque zero" in motivo


def test_kokeshi_declarada_fora_da_cobertura():
    assert "kokeshi" in svc.BRANDS_NOT_COVERED
    assert "kokeshi" not in svc.EXPECTED_BRANDS
    assert "Kokeshi" in svc.LIMITACAO_COBERTURA


# ===========================================================================
# 2. Escape do LIKE — unidade pura
# ===========================================================================

@pytest.mark.parametrize("entrada,esperado", [
    ("ABC", "%ABC%"),
    ("100%", "%100!%%"),
    ("a_b", "%a!_b%"),
    ("!", "%!!%"),
    # O escape tem de ser escapado ANTES dos curingas, senao `!%` digitado
    # pelo usuario viraria um escape valido e `%` voltaria a ser curinga.
    ("!%", "%!!!%%"),
])
def test_escapar_like(entrada, esperado):
    assert svc.escapar_like(entrada) == esperado


# ===========================================================================
# 3. Validacao de filtros — fail-closed
# ===========================================================================

def test_marca_desconhecida_e_erro_nao_filtro_vazio():
    """Silenciar faria a tela mostrar zero produtos e parecer 'sem estoque'."""
    with pytest.raises(svc.FiltroInvalido) as exc:
        svc._validar_lista(["kokeshi"], svc.EXPECTED_BRANDS, "brands")
    assert exc.value.recebidos == ("kokeshi",)
    assert "apice" in exc.value.mensagem_segura, (
        "a recusa precisa listar o que e' aceito")


def test_recusa_nao_ecoa_o_valor_recebido():
    """A mensagem que vai para o HTTP e' montada so' com constantes.

    Ecoar a entrada faria o endpoint devolver texto arbitrario do cliente
    dentro da resposta -- e a politica deste router ja' e' mensagem FIXA.
    """
    payload = "<script>alert(1)</script>"
    with pytest.raises(svc.FiltroInvalido) as exc:
        svc._validar_lista([payload], svc.EXPECTED_BRANDS, "brands")
    assert payload not in exc.value.mensagem_segura
    assert payload.lower() not in exc.value.mensagem_segura.lower()
    # E `FiltroInvalido` continua sendo um ValueError, para quem so' captura
    # a excecao padrao.
    assert isinstance(exc.value, ValueError)


def test_endpoint_devolve_422_sem_ecoar_a_entrada(cliente, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "shopee_fbs_stock_enabled", True)

    payload = "<script>alert(1)</script>"
    r = cliente.get("/api/v1/performance/shopee-fbs-estoque",
                    params={"brands": payload})

    assert r.status_code == 422
    assert payload not in r.text
    assert "apice" in r.text


def test_lista_vazia_significa_sem_filtro():
    assert svc._validar_lista([], svc.EXPECTED_BRANDS, "brands") is None
    assert svc._validar_lista(["  "], svc.EXPECTED_BRANDS, "brands") is None
    assert svc._validar_lista(None, svc.EXPECTED_BRANDS, "brands") is None


def test_filtro_normaliza_caixa_e_deduplica_preservando_ordem():
    assert svc._validar_lista(
        ["Rituaria", "APICE", "rituaria"], svc.EXPECTED_BRANDS, "brands"
    ) == ["rituaria", "apice"]


def test_opt_int_preserva_none():
    """🔑 A confusao que este modulo existe para impedir."""
    assert svc._opt_int(None) is None
    assert svc._opt_int(0) == 0
    assert svc._opt_int(7) == 7


# ===========================================================================
# 4. Router — a flag corta ANTES do banco
# ===========================================================================

class _SessaoProibida:
    """Qualquer uso do banco e' falha do teste."""

    def __getattr__(self, nome):
        raise AssertionError(
            f"o endpoint tocou o banco (.{nome}) com a flag desligada")


@pytest.fixture
def cliente():
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: _SessaoProibida()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_flag_desligada_responde_200_sem_tocar_o_banco(cliente, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "shopee_fbs_stock_enabled", False)

    r = cliente.get("/api/v1/performance/shopee-fbs-estoque")

    assert r.status_code == 200, "desligado nao e' erro: a rota existe"
    corpo = r.json()
    assert corpo["status"] == "unavailable"
    assert corpo["motivo_tecnico"] == "feature_flag_desligada"
    # 🔑 Nenhum indicador zerado no corpo de indisponibilidade.
    assert "indicadores" not in corpo
    assert "produtos" not in corpo


def test_flag_de_estoque_e_independente_da_de_desempenho(cliente, monkeypatch):
    """Ligar o desempenho (ja' ligado em producao) nao pode ligar o estoque."""
    from app.config import settings
    monkeypatch.setattr(settings, "shopee_fbs_enabled", True)
    monkeypatch.setattr(settings, "shopee_fbs_stock_enabled", False)

    r = cliente.get("/api/v1/performance/shopee-fbs-estoque")
    assert r.json()["motivo_tecnico"] == "feature_flag_desligada"


def test_endpoint_de_desempenho_nao_foi_alterado(cliente, monkeypatch):
    """O gate cria superficie nova; nao mexe na que ja' serve producao."""
    from app.config import settings
    monkeypatch.setattr(settings, "shopee_fbs_enabled", False)

    r = cliente.get("/api/v1/performance/shopee-fbs")
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable"


def test_rota_nova_nao_exigiu_editar_main():
    """O PR #45 esta corrigindo `main.py`: nao podemos tocar nele."""
    main_src = io.open(RAIZ / "app" / "main.py", encoding="utf-8").read()
    assert "shopee_fbs_stock" not in main_src
    assert "estoque" not in main_src.lower()


# ===========================================================================
# 5. Integracao contra PostgreSQL real
# ===========================================================================

DSN = os.getenv("TEST_PG_DSN")
pg = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN nao configurado")

DDL = """
CREATE SCHEMA IF NOT EXISTS marts;
DROP TABLE IF EXISTS marts.fact_shopee_fbs_stock_daily;
DROP TABLE IF EXISTS marts.fact_shopee_fbs_stock_location_daily;
CREATE TABLE marts.fact_shopee_fbs_stock_location_daily (
  ref_date date NOT NULL, brand text NOT NULL, shop_account text NOT NULL,
  item_id text NOT NULL, model_id text NOT NULL, location_id text NOT NULL,
  full_stock bigint NOT NULL, is_saleable boolean,
  is_kit boolean NOT NULL, item_status text,
  source_run_id text NOT NULL, source_captured_at timestamptz NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (ref_date, shop_account, item_id, model_id, location_id));
CREATE TABLE marts.fact_shopee_fbs_stock_daily (
  ref_date date NOT NULL, brand text NOT NULL, shop_account text NOT NULL,
  item_id text NOT NULL, model_id text NOT NULL,
  item_name text, item_sku text, item_status text, is_kit boolean NOT NULL,
  full_stock_saleable bigint NOT NULL, full_stock_total bigint NOT NULL,
  location_count integer NOT NULL,
  seller_stock_total bigint, reserved_stock bigint,
  summary_available_stock bigint,
  units_sold_28d bigint NOT NULL, days_with_sales_28d integer NOT NULL,
  units_sold_28d_legado_com_unpaid bigint, avg_daily_units_28d numeric NOT NULL,
  cobertura_torre_dias numeric, classificacao_torre text NOT NULL,
  vinculo_vendas text NOT NULL,
  source_run_id text NOT NULL, source_captured_at timestamptz NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT NOW(),
  PRIMARY KEY (ref_date, shop_account, item_id, model_id));
"""

#: Sete produtos, um por classificacao. Os NULLs de contexto do item 101 sao
#: deliberados: provam que ausencia atravessa a camada sem virar 0.
SEED = """
INSERT INTO marts.fact_shopee_fbs_stock_daily VALUES
 ('2026-09-23','apice','apice','101','0','Capa Alfa','SKU-101','NORMAL',false,
  0,0,0, NULL,NULL,NULL, 28,14,NULL,1.0, 0.0,'RUPTURA_CANDIDATA','COM_VENDA',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00'),
 ('2026-09-23','barbours','barbours','102','0','Capa Beta','SKU-102','NORMAL',false,
  10,12,2, 5,3,11, 56,20,60,2.0, 5.0,'BAIXO_CANDIDATO','COM_VENDA',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00'),
 ('2026-09-23','lescent','lescent','103','0','Capa Gama','PROMO100%','NORMAL',false,
  500,500,3, 0,0,500, 28,5,28,1.0, 500.0,'EXCESSO_CANDIDATO','COM_VENDA',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00'),
 ('2026-09-23','rituaria','rituaria','104','0','Capa Delta','SKU-104','NORMAL',false,
  40,40,1, 0,0,40, 0,0,0,0, NULL,'SEM_GIRO_CANDIDATO','SEM_VENDA_NA_JANELA',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00'),
 ('2026-09-23','apice','apice','105','0','Capa Epsilon','PROMO1005','NORMAL',false,
  60,60,1, 0,0,60, 28,10,28,1.0, 60.0,'SUFICIENTE','COM_VENDA',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00'),
 ('2026-09-23','barbours','barbours','106','0','Capa Zeta','SKU-106','NORMAL',false,
  0,0,0, 0,0,0, 0,0,0,0, NULL,'SEM_DEMANDA_MEDIDA','SEM_VENDA_NA_JANELA',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00'),
 ('2026-09-23','apice','apice','107','0','Kit Omega','KIT040','NORMAL',true,
  25,25,1, 0,0,25, 0,0,0,0, NULL,'KIT_NAO_CONCILIADO','SEM_VENDA_NA_JANELA',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00');
INSERT INTO marts.fact_shopee_fbs_stock_location_daily VALUES
 ('2026-09-23','barbours','barbours','102','0','CD-SP',7,true,false,'NORMAL',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00'),
 ('2026-09-23','barbours','barbours','102','0','CD-RJ',3,true,false,'NORMAL',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00'),
 ('2026-09-23','barbours','barbours','102','0','CD-MG',2,NULL,false,'NORMAL',
  'r1','2026-09-23 08:00:00+00','2026-09-23 09:00:00+00');
"""


@pytest.fixture
def engine():
    from sqlalchemy import create_engine
    eng = create_engine(DSN, future=True)
    yield eng
    eng.dispose()


def _estado(engine, sql: str):
    """Deixa o banco num estado COMMITADO.

    Tem de ser commitado: o servico comeca com `db.rollback()`, entao setup
    dentro de transacao aberta seria desfeito pelo proprio codigo sob teste.
    """
    from sqlalchemy import text as _t
    with engine.begin() as conn:
        for cmd in filter(None, (c.strip() for c in sql.split(";"))):
            conn.execute(_t(cmd))


@pytest.fixture
def sessao(engine):
    from sqlalchemy.orm import sessionmaker
    s = sessionmaker(bind=engine, future=True)()
    yield s
    s.rollback()
    s.close()


def _bloco(sessao, **kw):
    return svc.get_estoque_full_block(sessao, hoje=HOJE, **kw)


@pg
def test_fato_inexistente_nao_devolve_zeros(engine, sessao):
    """🔑 Migrations 020/021 nao aplicadas -- o caso de PRODUCAO hoje."""
    _estado(engine, """
        DROP TABLE IF EXISTS marts.fact_shopee_fbs_stock_daily;
        DROP TABLE IF EXISTS marts.fact_shopee_fbs_stock_location_daily;
    """)
    with pytest.raises(svc.FatoInexistente) as exc:
        _bloco(sessao)
    assert exc.value.motivo_tecnico == "fato_inexistente"
    assert "não significa estoque zero" in exc.value.mensagem


@pg
def test_falta_so_o_grao_fino_tambem_e_indisponivel(engine, sessao):
    """A tela usa as DUAS tabelas: meia migration nao e' meia tela."""
    _estado(engine, DDL + SEED + """
        DROP TABLE marts.fact_shopee_fbs_stock_location_daily;
    """)
    with pytest.raises(svc.FatoInexistente):
        _bloco(sessao)


@pg
def test_tabela_vazia_e_sem_fotografia_nao_estoque_zero(engine, sessao):
    _estado(engine, DDL)  # cria sem SEED
    with pytest.raises(svc.SemFotografiaPublicada) as exc:
        _bloco(sessao)
    assert exc.value.motivo_tecnico == "sem_fotografia_publicada"
    assert "não significa estoque zero" in exc.value.mensagem


@pg
def test_indicadores_batem_com_a_fotografia(engine, sessao):
    _estado(engine, DDL + SEED)
    r = _bloco(sessao)
    i = r.indicadores

    # Kit NAO entra no operacional: 0+10+500+40+60+0 = 610, kit 25 a' parte.
    assert i.unidades_vendaveis_operacional == 610
    assert i.unidades_vendaveis_kits_contexto == 25
    assert i.unidades_vendaveis_total == 635

    assert (i.produtos_ruptura, i.produtos_baixo, i.produtos_excesso) == (1, 1, 1)
    assert (i.produtos_sem_giro, i.produtos_suficientes) == (1, 1)
    assert (i.produtos_sem_demanda_medida, i.produtos_kit_nao_conciliado) == (1, 1)
    assert i.produtos_total == 7
    assert i.produtos_exigem_acao == 2, "ruptura + baixo"


@pg
def test_ausencia_de_contexto_chega_como_none_nao_zero(engine, sessao):
    """🔑 O item 101 tem reservado/vendedor/summary NULL na fonte."""
    _estado(engine, DDL + SEED)
    r = _bloco(sessao)
    p101 = next(p for p in r.produtos if p.item_id == "101")

    assert p101.reserved_stock is None
    assert p101.seller_stock_total is None
    assert p101.summary_available_stock is None
    assert p101.units_sold_28d_legado_com_unpaid is None
    # E o estoque medido zero continua sendo ZERO, nao None: sao coisas
    # diferentes e as duas precisam sobreviver a viagem.
    assert p101.full_stock_saleable == 0


@pg
def test_sem_venda_na_janela_mantem_cobertura_nula(engine, sessao):
    """Cobertura infinita nao e' numero grande."""
    _estado(engine, DDL + SEED)
    r = _bloco(sessao)
    p104 = next(p for p in r.produtos if p.item_id == "104")
    assert p104.units_sold_28d == 0
    assert p104.cobertura_torre_dias is None
    assert p104.vinculo_vendas == "SEM_VENDA_NA_JANELA"


@pg
def test_ordem_e_operacional_e_nao_alfabetica(engine, sessao):
    """Ruptura primeiro; depois baixo; 'sem venda' nunca no topo."""
    _estado(engine, DDL + SEED)
    r = _bloco(sessao)
    assert [p.item_id for p in r.produtos][:3] == ["101", "102", "103"]
    assert r.produtos[0].classificacao_torre == "RUPTURA_CANDIDATA"


@pg
def test_distribuicao_por_cd_vem_agrupada_e_preserva_none(engine, sessao):
    _estado(engine, DDL + SEED)
    r = _bloco(sessao)
    p102 = next(p for p in r.produtos if p.item_id == "102")

    assert [c.location_id for c in p102.por_cd] == ["CD-SP", "CD-RJ", "CD-MG"]
    assert sum(c.full_stock for c in p102.por_cd) == 12
    # `is_saleable` ausente na fonte NAO pode virar False: "nao vendavel" e
    # "a API nao disse" sao respostas diferentes.
    assert [c.is_saleable for c in p102.por_cd] == [True, True, None]
    # Item sem linha de grao fino nao quebra e nao inventa CD.
    assert next(p for p in r.produtos if p.item_id == "101").por_cd == []


@pg
def test_filtro_de_acao_nao_mexe_nos_cartoes(engine, sessao):
    """Os cartoes descrevem o ESCOPO; so' a tabela e' refinada.

    Se os cartoes seguissem o filtro, "2 itens exigem acao" viraria "2 de 2" e
    a tela perderia a nocao de proporcao.
    """
    _estado(engine, DDL + SEED)
    r = _bloco(sessao, somente_acao=True)

    assert {p.classificacao_torre for p in r.produtos} == {
        "RUPTURA_CANDIDATA", "BAIXO_CANDIDATO"}
    assert r.total_no_filtro == 2
    assert r.indicadores.produtos_total == 7, "cartao nao segue o filtro"
    assert r.indicadores.unidades_vendaveis_operacional == 610


@pg
def test_filtro_de_marca_reduz_cartoes_e_tabela(engine, sessao):
    """Conta/marca SAO escopo: esses mudam os cartoes."""
    _estado(engine, DDL + SEED)
    r = _bloco(sessao, brands=["apice"])
    assert {p.brand for p in r.produtos} == {"apice"}
    assert r.indicadores.produtos_total == 3
    assert r.indicadores.unidades_vendaveis_operacional == 60
    assert r.indicadores.unidades_vendaveis_kits_contexto == 25
    assert r.contas_cobertas == ["apice"]


@pg
def test_busca_escapa_curinga_do_like(engine, sessao):
    """`100%` tem de casar o literal, nao virar curinga."""
    _estado(engine, DDL + SEED)
    r = _bloco(sessao, busca="100%")
    assert [p.item_id for p in r.produtos] == ["103"], (
        "o % digitado vazou como curinga e trouxe PROMO1005")


@pg
def test_busca_casa_sku_ou_nome_sem_caixa(engine, sessao):
    _estado(engine, DDL + SEED)
    assert [p.item_id for p in _bloco(sessao, busca="sku-104").produtos] == ["104"]
    assert [p.item_id for p in _bloco(sessao, busca="zeta").produtos] == ["106"]


@pg
def test_filtro_sem_resultado_e_lista_vazia_com_cartoes_cheios(engine, sessao):
    """Vazio POR FILTRO e' diferente de indisponivel: os cartoes continuam."""
    _estado(engine, DDL + SEED)
    r = _bloco(sessao, brands=["apice"], busca="inexistente-xyz")
    assert r.produtos == []
    assert r.total_no_filtro == 0
    assert r.status == "ok", "filtro sem match nao e' indisponibilidade"
    assert r.indicadores.produtos_total == 3


@pg
def test_truncamento_e_declarado(engine, sessao):
    _estado(engine, DDL + SEED)
    r = _bloco(sessao, limite=2)
    assert len(r.produtos) == 2
    assert r.total_no_filtro == 7
    assert r.truncado is True
    assert any("TRUNCADA" in lim for lim in r.limitacoes)

    inteiro = _bloco(sessao)
    assert inteiro.truncado is False


@pg
def test_classificacao_fora_do_dominio_quebra_o_contrato(engine, sessao):
    _estado(engine, DDL + SEED + """
        UPDATE marts.fact_shopee_fbs_stock_daily
           SET classificacao_torre = 'INVENTADA' WHERE item_id = '105';
    """)
    with pytest.raises(svc.EstoqueFullContractError):
        _bloco(sessao)


@pg
def test_frescor_mede_a_distancia_ate_hoje(engine, sessao):
    _estado(engine, DDL + SEED)
    r = _bloco(sessao)
    assert r.frescor.ref_date == REF
    assert r.frescor.dias_desde_a_fotografia == 1
    assert r.frescor.no_automation is True
    assert r.frescor.source_captured_at is not None


@pg
def test_le_a_fotografia_mais_recente(engine, sessao):
    """Havendo dois dias, a tela mostra o ULTIMO -- e apenas ele."""
    _estado(engine, DDL + SEED + """
        INSERT INTO marts.fact_shopee_fbs_stock_daily
        SELECT '2026-09-22'::date, brand, shop_account, item_id, model_id,
               item_name, item_sku, item_status, is_kit,
               999, 999, location_count,
               seller_stock_total, reserved_stock, summary_available_stock,
               units_sold_28d, days_with_sales_28d,
               units_sold_28d_legado_com_unpaid, avg_daily_units_28d,
               cobertura_torre_dias, classificacao_torre, vinculo_vendas,
               source_run_id, source_captured_at, ingested_at
          FROM marts.fact_shopee_fbs_stock_daily;
    """)
    r = _bloco(sessao)
    assert r.frescor.ref_date == REF
    assert all(p.ref_date == REF for p in r.produtos)
    assert r.indicadores.produtos_total == 7


@pg
def test_limitacoes_viajam_no_payload(engine, sessao):
    _estado(engine, DDL + SEED)
    r = _bloco(sessao)
    texto = " ".join(r.limitacoes)
    assert "Kokeshi" in texto
    assert "MANUAL" in texto
    assert "Kits ficam FORA" in texto
    assert r.marcas_nao_cobertas == ["kokeshi"]
    assert r.limites.provisorio is True


@pg
def test_transacao_e_read_only_de_fato(engine, sessao):
    """READ ONLY nao e' comentario: o banco tem de recusar escrita."""
    from sqlalchemy import text as _t
    _estado(engine, DDL + SEED)
    _bloco(sessao)
    with pytest.raises(Exception) as exc:
        sessao.execute(_t(
            "INSERT INTO marts.fact_shopee_fbs_stock_daily "
            "SELECT * FROM marts.fact_shopee_fbs_stock_daily LIMIT 1"))
    assert "read-only" in str(exc.value).lower()
