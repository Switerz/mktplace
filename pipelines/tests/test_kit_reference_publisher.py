"""Gate KITS-PMA-3 — migration 022 e publisher contra PostgreSQL REAL.

Por que banco de verdade, e nao dublê: o que este gate promete esta em grande
parte NO BANCO — os CHECK que recusam ponte de outra marca, o bicondicional de
`resolved`/`blocked`, a unicidade da PK que torna o republish idempotente, e o
filtro de `reference_snapshot_id` que impede servir referencia calculada sobre
planilha antiga. Um fake que reimplementasse essas regras estaria testando a
propria reimplementacao.

O banco e' DESCARTAVEL: cada execucao cria `kitref_test_<pid>_<n>`, roda o DDL
que a 022 emite, exercita e derruba no fim. Nenhum dado de producao e tocado.

Skip — e NAO falha — quando nao ha Postgres local. O contrato de `pma_kit_bom`
ja' e' coberto sem banco em `apps/api/tests/test_pma_kit_bom.py`; aqui o alvo
e' o que so' o Postgres pode provar.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2.extras import RealDictCursor  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]

#: `kit_reference_publisher` poe `apps/api` no `sys.path` ao ser importado —
#: por isso `app.services` fica alcancavel sem um segundo insert aqui.
from pipelines import kit_reference_publisher as P  # noqa: E402
from app.services import pma_kit_bom as kb  # noqa: E402

HOJE = date(2026, 9, 25)
D0 = date(2026, 9, 25)
SNAPSHOT = "snap-2026-09-23"

ADMIN_DSN = os.environ.get(
    "KIT_TEST_ADMIN_DSN",
    "postgresql://postgres:postgres@localhost:5432/postgres")

MIGRATION = (RAIZ / "apps" / "api" / "alembic" / "versions"
             / "022_create_fact_kit_reference_daily.py")


# --------------------------------------------------------------- infra

class _OpFalso:
    """Coleta o SQL que a migration emite, sem conexao."""

    def __init__(self):
        self.sql: list[str] = []

    def execute(self, texto):
        self.sql.append(str(texto))


def _carregar_migration():
    """Importa a 022 com um `alembic` FALSO, sem tocar banco nenhum.

    O `alembic` verdadeiro nao e' usado — e nao pode ser. `apps/api/alembic/` e'
    um diretorio com `__init__.py`, e basta que qualquer modulo ponha
    `apps/api` no `sys.path` (o que `kit_reference_publisher` e
    `channel_offer_sync` fazem) para `import alembic` passar a resolver para as
    MIGRATIONS em vez do pacote. Quem vence depende da ORDEM em que os testes
    sao coletados: rodando so' este arquivo dava certo, rodando a suite inteira
    dava `ImportError`.

    Injetar o dublê em `sys.modules` durante a carga remove a ordem da equacao:
    `from alembic import op` la' dentro passa a ser resolvido por este modulo,
    sempre. E' tambem o que se quer — a migration NAO deve alcancar um `op` de
    verdade aqui.
    """
    spec = importlib.util.spec_from_file_location("mig022", MIGRATION)
    modulo = importlib.util.module_from_spec(spec)
    falso = types.ModuleType("alembic")
    falso.op = _OpFalso()
    anterior = sys.modules.get("alembic")
    sys.modules["alembic"] = falso
    sys.modules["mig022"] = modulo
    try:
        spec.loader.exec_module(modulo)
    finally:
        if anterior is not None:
            sys.modules["alembic"] = anterior
        else:
            sys.modules.pop("alembic", None)
    return modulo


def _ddl(modulo, funcao_nome: str) -> list[str]:
    """Coleta o SQL que `upgrade`/`downgrade` emitiriam.

    Troca `modulo.op`, e NAO `alembic.op`: a migration faz `from alembic import
    op` no topo, o que copia a referencia para o namespace dela. Mexer em
    `alembic.op` depois do import nao alcanca a copia — e o efeito era um
    coletor vazio, isto e', nenhum DDL executado e a tabela inexistente.
    """
    op_falso = _OpFalso()
    anterior = modulo.op
    modulo.op = op_falso
    try:
        getattr(modulo, funcao_nome)()
    finally:
        modulo.op = anterior
    return op_falso.sql


def _postgres_disponivel() -> bool:
    try:
        c = psycopg2.connect(ADMIN_DSN, connect_timeout=3)
        c.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_disponivel(),
    reason="sem PostgreSQL local descartavel em KIT_TEST_ADMIN_DSN")


#: Tabelas que o publisher LE' e que nao sao criadas pela 022. Recriadas aqui no
#: minimo que as consultas exigem — colunas a mais nao mudariam nada, colunas a
#: menos quebrariam o SELECT, que e' exatamente o que se quer provar.
DDL_APOIO = """
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS silver;

CREATE TABLE marts.fact_channel_offer_observation (
    marketplace TEXT, observed_date DATE, offer_key TEXT, brand TEXT,
    shop_account TEXT, seller_sku TEXT, gtin TEXT, product_type TEXT,
    batch_id TEXT,
    PRIMARY KEY (observed_date, marketplace, offer_key)
);

CREATE TABLE marts.fact_suggested_price_reference_snapshot (
    snapshot_id VARCHAR(64), reference_row_id TEXT, brand TEXT,
    source_sku TEXT, source_gtin TEXT, suggested_retail_amount NUMERIC(14,2),
    quality_status TEXT, captured_at TIMESTAMPTZ
);

CREATE TABLE audit.source_sync_run (
    sync_run_id SERIAL PRIMARY KEY, source_name TEXT, marketplace_id INT,
    loja_id INT, status TEXT, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
    rows_extracted INT, rows_loaded INT, error_message TEXT
);

CREATE TABLE raw.protheus_kit_components (
    kit_sku TEXT, component_sku TEXT, qty_per_kit NUMERIC,
    valid_from DATE, valid_to DATE, active BOOLEAN, source TEXT
);

CREATE TABLE gold.dim_produto_gobeauty (
    produto_sk TEXT, marca TEXT, sku TEXT, fonte_do_sku TEXT, nome TEXT,
    ean TEXT, marca_conflitante BOOLEAN DEFAULT FALSE,
    codigo_protheus TEXT, codigo_tiny TEXT, codigo_shopify TEXT,
    codigo_bling TEXT, codigo_omie TEXT
);

CREATE TABLE gold.map_produto_codigo_gobeauty (
    marca TEXT, codigo TEXT, produto_sk TEXT, ambiguo BOOLEAN DEFAULT FALSE
);

CREATE TABLE silver.gobeaute_produto_cadastro (
    marca TEXT, sku TEXT, sku_antigo TEXT, tipo TEXT
);
"""


@pytest.fixture
def banco():
    nome = f"kitref_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    admin.close()

    dsn = ADMIN_DSN.rsplit("/", 1)[0] + "/" + nome
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(DDL_APOIO)
        # O DDL da fato de kit vem da PROPRIA migration, nao redigitado aqui.
        modulo = _carregar_migration()
        for sql in _ddl(modulo, "upgrade"):
            cur.execute(sql)
    conn.autocommit = False
    try:
        yield dsn, conn
    finally:
        conn.close()
        admin = psycopg2.connect(ADMIN_DSN)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        admin.close()


def _semear(conn, *, ofertas=None, kit_bom=None):
    """Popula as fontes com o cenario Kokeshi real, reduzido a dois kits."""
    ofertas = ofertas if ofertas is not None else [
        ("tiktok", D0, "KIT-1", "kokeshi", "kokeshi", "40010",
         "kit_suspected", "lote-1"),
        ("tiktok", D0, "KIT-2", "kokeshi", "kokeshi", "40015",
         "kit_suspected", "lote-1"),
        ("tiktok", D0, "SIMPLES-1", "kokeshi", "kokeshi", "KS03015",
         "no_kit_signal", "lote-1"),
    ]
    kit_bom = kit_bom if kit_bom is not None else [
        # KKS00008 = KS03016 (25,90) + KS03019 (42,90) -> 68,80 -5% = 65,36
        ("KKS00008", "KS03016", 1), ("KKS00008", "KS03019", 1),
        # KKS00027 tem um componente SEM referencia B2B -> blocked
        ("KKS00027", "KS03016", 1), ("KKS00027", "KS03022", 1),
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO marts.fact_channel_offer_observation "
            "(marketplace, observed_date, offer_key, brand, shop_account, "
            " seller_sku, product_type, batch_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            ofertas)
        cur.executemany(
            "INSERT INTO marts.fact_suggested_price_reference_snapshot "
            "(snapshot_id, reference_row_id, brand, source_sku, source_gtin, "
            " suggested_retail_amount, quality_status, captured_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,'ok','2026-09-23T19:34:00+00:00')",
            [(SNAPSHOT, "R1", "kokeshi", "KS03016", "7899459300204", "25.90"),
             (SNAPSHOT, "R2", "kokeshi", "KS03019", "7899459304776", "42.90"),
             (SNAPSHOT, "R3", "kokeshi", "KS03015", "7908790700076", "29.90")])
        cur.executemany(
            "INSERT INTO raw.protheus_kit_components "
            "(kit_sku, component_sku, qty_per_kit, valid_from, valid_to, "
            " active, source) VALUES (%s,%s,%s,NULL,NULL,TRUE,'protheus.sg1010')",
            kit_bom)
        cur.executemany(
            "INSERT INTO gold.dim_produto_gobeauty "
            "(produto_sk, marca, sku, fonte_do_sku, ean, codigo_protheus, "
            " codigo_bling) VALUES (%s,'kokeshi',%s,'protheus',%s,%s,%s)",
            [("sk-1", "KS03016", "7908790700090", "KS03016", None),
             ("sk-2", "KS03019", "7908790700199", "KS03019", None),
             ("sk-3", "KS03015", "7908790700076", "KS03015", None),
             ("sk-4", "KS03022", "7908790700014", "KS03022", None),
             ("sk-5", "KKS00008", None, "KKS00008", "40010"),
             ("sk-6", "KKS00027", None, "KKS00027", "40015")])
    conn.commit()


def _publicar(dsn, **over):
    alvo = psycopg2.connect(dsn)
    fonte = psycopg2.connect(dsn)
    audit = psycopg2.connect(dsn)
    try:
        kwargs = dict(target_conn=alvo, audit_conn=audit, source_conn=fonte,
                      marketplace="tiktok", observed_date=D0, today=HOJE)
        kwargs.update(over)
        return P.publish(**kwargs)
    finally:
        for c in (alvo, fonte, audit):
            c.close()


def _linhas(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM marts.fact_kit_reference_daily "
                    "ORDER BY offer_key")
        return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------- migration

def test_a_migration_encadeia_na_021():
    modulo = _carregar_migration()
    assert modulo.revision == "022"
    assert modulo.down_revision == "021"


def test_o_ddl_e_postgres_valido_e_o_downgrade_derruba(banco):
    dsn, conn = banco
    modulo = _carregar_migration()
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('marts.fact_kit_reference_daily')")
        assert cur.fetchone()[0] is not None
        for sql in _ddl(modulo, "downgrade"):
            cur.execute(sql)
        cur.execute("SELECT to_regclass('marts.fact_kit_reference_daily')")
        assert cur.fetchone()[0] is None
    conn.rollback()


# --------------------------------------------------------------- CHECKs

def _inserir_cru(conn, **over):
    base = dict(
        marketplace="tiktok", observed_date=D0, offer_key="X", brand="kokeshi",
        shop_account="kokeshi", seller_sku="40010", kit_protheus_sku="KKS00008",
        kit_brand="kokeshi", bridge_status="EXACT_ALIAS",
        bridge_method="alias_code_exact", reference_snapshot_id=SNAPSHOT,
        reference_captured_at=None, offer_batch_id=None, component_count=2,
        total_units=Decimal("2"), components='[{"a":1},{"b":2}]',
        components_base_amount=Decimal("68.80"), discount_pct=Decimal("0.05"),
        kit_reference_amount=Decimal("65.36"), status="resolved", reason=None,
        source_run_id="run-1")
    base.update(over)
    colunas = ", ".join(base)
    marcas = ", ".join(["%s"] * len(base))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO marts.fact_kit_reference_daily ({colunas}) "
            f"VALUES ({marcas})", tuple(base.values()))


@pytest.mark.parametrize("over,trecho", [
    ({"bridge_status": "CANDIDATE_REVIEW"}, "ck_fkrd_bridge_status"),
    ({"bridge_status": "AMBIGUOUS"}, "ck_fkrd_bridge_status"),
    ({"bridge_status": "CROSS_BRAND_CONFLICT"}, "ck_fkrd_bridge_status"),
    ({"bridge_status": "UNMAPPED"}, "ck_fkrd_bridge_status"),
    ({"kit_brand": "apice"}, "ck_fkrd_mesma_marca"),
    ({"status": "resolved", "reason": "algum motivo"}, "ck_fkrd_resolvido"),
    ({"status": "blocked"}, "ck_fkrd_resolvido"),
    ({"kit_reference_amount": Decimal("NaN")}, "ck_fkrd_resolvido"),
    ({"total_units": Decimal("0")}, "ck_fkrd_componentes"),
    ({"component_count": 3}, "ck_fkrd_componentes"),
    ({"discount_pct": Decimal("0.07")}, "ck_fkrd_desconto"),
    ({"source_run_id": ""}, "ck_fkrd_textos"),
])
def test_o_banco_recusa_linha_fora_do_contrato(banco, over, trecho):
    dsn, conn = banco
    with pytest.raises(psycopg2.errors.CheckViolation) as exc:
        _inserir_cru(conn, **over)
    assert trecho in str(exc.value)
    conn.rollback()


@pytest.mark.parametrize("status", ["pendente", "PENDENTE", "candidate",
                                   "review", ""])
def test_status_fora_do_dominio_e_recusado(banco, status):
    """"Nenhuma linha PENDENTE entra" e' garantia do BANCO, nao so' do codigo.

    Qual das duas constraints dispara primeiro (`ck_fkrd_status` ou o
    bicondicional `ck_fkrd_resolvido`) depende da ordem de avaliacao do
    Postgres e NAO e' contrato. O que e' contrato e' a recusa.
    """
    dsn, conn = banco
    with pytest.raises(psycopg2.errors.CheckViolation):
        _inserir_cru(conn, status=status)
    conn.rollback()


def test_blocked_com_motivo_e_sem_valor_e_aceito(banco):
    dsn, conn = banco
    _inserir_cru(conn, status="blocked", kit_reference_amount=None,
                 components_base_amount=None,
                 reason=kb.REASON_COMPONENT_REFERENCE_MISSING)
    conn.commit()
    assert _linhas(conn)[0]["status"] == "blocked"


def test_pk_impede_duas_referencias_para_a_mesma_oferta(banco):
    dsn, conn = banco
    _inserir_cru(conn)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        _inserir_cru(conn, kit_reference_amount=Decimal("1.00"))
    conn.rollback()


# --------------------------------------------------------------- publicacao

def test_publica_o_valor_medido_e_bloqueia_o_que_falta_referencia(banco):
    dsn, conn = banco
    _semear(conn)
    saida = _publicar(dsn)
    assert saida["state"] == "published"
    assert saida["rows"] == 2  # os dois kits; o produto simples nao entra

    linhas = _linhas(conn)
    resolvida = next(l for l in linhas if l["offer_key"] == "KIT-1")
    assert resolvida["status"] == "resolved"
    assert resolvida["kit_reference_amount"] == Decimal("65.36")
    assert resolvida["components_base_amount"] == Decimal("68.80")
    assert resolvida["total_units"] == Decimal("2.0000")
    assert resolvida["discount_pct"] == Decimal("0.0500")
    assert resolvida["kit_protheus_sku"] == "KKS00008"
    assert resolvida["reference_snapshot_id"] == SNAPSHOT
    assert resolvida["reason"] is None

    bloqueada = next(l for l in linhas if l["offer_key"] == "KIT-2")
    assert bloqueada["status"] == "blocked"
    assert bloqueada["kit_reference_amount"] is None
    assert bloqueada["reason"] == kb.REASON_COMPONENT_REFERENCE_MISSING


def test_produto_sem_sinal_de_kit_nao_produz_linha(banco):
    dsn, conn = banco
    _semear(conn)
    _publicar(dsn)
    assert all(l["offer_key"] != "SIMPLES-1" for l in _linhas(conn))


def test_publicacao_e_idempotente(banco):
    dsn, conn = banco
    _semear(conn)
    primeira = _publicar(dsn)
    antes = _linhas(conn)
    segunda = _publicar(dsn)
    depois = _linhas(conn)

    assert primeira["rows"] == segunda["rows"]
    assert len(antes) == len(depois)
    comparavel = lambda ls: [  # noqa: E731
        {k: v for k, v in l.items() if k not in ("published_at", "source_run_id")}
        for l in ls]
    assert comparavel(antes) == comparavel(depois)


def test_republish_apaga_o_que_saiu_da_fotografia(banco):
    dsn, conn = banco
    _semear(conn)
    _publicar(dsn)
    assert len(_linhas(conn)) == 2

    with conn.cursor() as cur:
        cur.execute("DELETE FROM marts.fact_channel_offer_observation "
                    "WHERE offer_key = 'KIT-2'")
    conn.commit()
    _publicar(dsn)
    restantes = _linhas(conn)
    assert [l["offer_key"] for l in restantes] == ["KIT-1"]


def test_escopo_de_outro_dia_nao_e_tocado(banco):
    dsn, conn = banco
    _semear(conn)
    _publicar(dsn)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO marts.fact_channel_offer_observation "
            "(marketplace, observed_date, offer_key, brand, shop_account, "
            " seller_sku, product_type) VALUES "
            "('tiktok', DATE '2026-09-24', 'KIT-1', 'kokeshi', 'kokeshi', "
            " '40010', 'kit_suspected')")
    conn.commit()
    _publicar(dsn, observed_date=date(2026, 9, 24))
    with conn.cursor() as cur:
        cur.execute("SELECT observed_date, count(*) "
                    "FROM marts.fact_kit_reference_daily GROUP BY 1 ORDER BY 1")
        assert cur.fetchall() == [(date(2026, 9, 24), 1), (D0, 2)]


def test_rollback_integral_quando_a_transacao_falha(banco, monkeypatch):
    """Falha DEPOIS do INSERT: nada fica, e a auditoria registra `failed`."""
    dsn, conn = banco
    _semear(conn)
    _publicar(dsn)
    antes = _linhas(conn)

    def explode(*a, **k):
        raise P.KitPublisherError("falha injetada apos o INSERT")

    monkeypatch.setattr(P, "execute_plan", explode)
    with pytest.raises(P.KitPublisherError):
        _publicar(dsn)

    assert _linhas(conn) == antes
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM audit.source_sync_run "
                    "ORDER BY sync_run_id DESC LIMIT 1")
        assert cur.fetchone()[0] == "failed"
    conn.rollback()


REGISTRO = ("tiktok", D0, "KIT-1", "kokeshi", "kokeshi", "40010",
            "KKS00008", "kokeshi", "EXACT_ALIAS", "alias_code_exact",
            SNAPSHOT, None, None, 2, Decimal("2"), '[{"a":1},{"b":2}]',
            Decimal("68.80"), Decimal("0.05"), Decimal("65.36"), "resolved",
            None, "run-x")


def test_primeira_defesa_recusa_insercao_parcial(banco, monkeypatch):
    """O driver confirmou menos linhas do que o plano tinha."""
    dsn, conn = banco
    alvo = psycopg2.connect(dsn)
    try:
        monkeypatch.setattr(P, "execute_values",
                            lambda cur, sql, pagina, page_size=None: None)
        plano = P.Plan(scope=P.Scope("tiktok", D0), records=[REGISTRO])
        with pytest.raises(P.KitPublisherError) as exc:
            P.execute_plan(alvo, plano)
        assert "confirmou 0 linhas" in str(exc.value)
        alvo.rollback()
    finally:
        alvo.close()


def test_segunda_defesa_recusa_o_que_a_transacao_nao_enxerga(banco, monkeypatch):
    """Independente da primeira: conta o que a PROPRIA transacao ve' no escopo.

    Cobre o que a contagem do driver nao cobre — linha gravada em escopo alheio,
    DELETE que varreu alem do proprio escopo, regra do banco que redirecionou a
    linha. Simulado aqui apontando a contagem para um escopo diferente daquele
    em que a linha foi de fato gravada.
    """
    dsn, conn = banco
    alvo = psycopg2.connect(dsn)
    try:
        monkeypatch.setattr(P, "SQL_COUNT_SCOPE", (
            "SELECT count(*) FROM marts.fact_kit_reference_daily "
            "WHERE marketplace = %(marketplace)s "
            "  AND observed_date = %(observed_date)s "
            "  AND offer_key = 'NUNCA-EXISTIU'"))
        plano = P.Plan(scope=P.Scope("tiktok", D0), records=[REGISTRO])
        with pytest.raises(P.KitPublisherError) as exc:
            P.execute_plan(alvo, plano)
        assert "reconciliacao pre-commit divergiu" in str(exc.value)
        alvo.rollback()
    finally:
        alvo.close()


def test_as_duas_defesas_passam_no_caminho_feliz(banco):
    dsn, conn = banco
    alvo = psycopg2.connect(dsn)
    try:
        plano = P.Plan(scope=P.Scope("tiktok", D0), records=[REGISTRO])
        inseridas, vistas = P.execute_plan(alvo, plano)
        assert inseridas == vistas == 1
        alvo.rollback()
    finally:
        alvo.close()


def test_auditoria_registra_sucesso_e_o_total(banco):
    dsn, conn = banco
    _semear(conn)
    saida = _publicar(dsn)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM audit.source_sync_run "
                    "WHERE sync_run_id = %s", (saida["sync_run_id"],))
        linha = dict(cur.fetchone())
    assert linha["source_name"] == P.AUDIT_SOURCE_NAME
    assert linha["status"] == "success"
    assert linha["rows_loaded"] == 2


def test_bom_contraditoria_recusa_a_publicacao_inteira(banco):
    dsn, conn = banco
    _semear(conn, kit_bom=[("KKS00008", "KS03016", 1),
                           ("KKS00008", "KS03016", 2)])
    with pytest.raises(kb.BomContractError):
        _publicar(dsn)
    assert _linhas(conn) == []


def test_duplicata_identica_nao_dobra_as_unidades(banco):
    dsn, conn = banco
    _semear(conn, kit_bom=[("KKS00008", "KS03016", 1),
                           ("KKS00008", "KS03016", 1),
                           ("KKS00008", "KS03019", 1)])
    _publicar(dsn)
    linha = next(l for l in _linhas(conn) if l["offer_key"] == "KIT-1")
    assert linha["component_count"] == 2
    assert linha["total_units"] == Decimal("2.0000")
    assert linha["kit_reference_amount"] == Decimal("65.36")


def test_ponte_de_outra_marca_nao_produz_linha(banco):
    dsn, conn = banco
    _semear(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE gold.dim_produto_gobeauty SET marca = 'apice' "
                    "WHERE codigo_protheus = 'KKS00008'")
    conn.commit()
    _publicar(dsn)
    assert all(l["offer_key"] != "KIT-1" for l in _linhas(conn))


def test_marca_do_kit_desconhecida_nao_produz_linha(banco):
    dsn, conn = banco
    _semear(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gold.dim_produto_gobeauty "
                    "WHERE codigo_protheus = 'KKS00008'")
    conn.commit()
    _publicar(dsn)
    assert all(l["offer_key"] != "KIT-1" for l in _linhas(conn))


# --------------------------------------------------------------- serving SQL

def test_o_sql_real_do_serving_carrega_os_tres_filtros():
    """Le' a consulta que a API usa DE VERDADE, nao uma copia.

    O comportamento e' exercitado abaixo contra o banco, mas com SQL escrito no
    teste. Sem esta assercao, alguem poderia remover um filtro do serving e o
    teste de comportamento continuaria verde — ele estaria provando a copia.
    """
    sys.path.insert(0, str(RAIZ / "apps" / "api"))
    from app.services.monitoramento_preco_service import SQL_KIT_REFERENCES

    texto = " ".join(SQL_KIT_REFERENCES.lower().split())
    assert "marts.fact_kit_reference_daily" in texto
    assert "reference_snapshot_id = :snapshot_id" in texto
    assert "status = 'resolved'" in texto
    assert "marketplace = :marketplace" in texto
    assert "observed_date = :observed_date" in texto


def test_o_sql_do_serving_recusa_snapshot_diferente(banco):
    """A recusa esta no SQL, nao so' no dublê dos testes da API."""
    dsn, conn = banco
    _semear(conn)
    _publicar(dsn)
    sql = ("SELECT count(*) FROM marts.fact_kit_reference_daily "
           "WHERE marketplace = %s AND observed_date = %s "
           "AND reference_snapshot_id = %s AND status = 'resolved'")
    with conn.cursor() as cur:
        cur.execute(sql, ("tiktok", D0, SNAPSHOT))
        assert cur.fetchone()[0] == 1
        cur.execute(sql, ("tiktok", D0, "snap-ANTIGO"))
        assert cur.fetchone()[0] == 0
        cur.execute(sql, ("shopee", D0, SNAPSHOT))
        assert cur.fetchone()[0] == 0
    conn.rollback()
