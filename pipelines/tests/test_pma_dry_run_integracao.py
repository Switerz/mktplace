"""Gate PMA-2C5C-H1-R/V — o ensaio contra um PostgreSQL de VERDADE.

Os testes de `test_pma_dry_run.py` injetam dublês e provam a fiacao. Eles nao
provam o que so' o servidor decide: que o cursor do driver realmente le, que
`SET TRANSACTION READ ONLY` realmente recusa escrita, que nenhum advisory lock
fica pendurado e que a candidata sai igual quando as linhas vem de um banco em
vez de uma lista literal.

Um dublê concorda com o que o teste mandar. Um `SET TRANSACTION READ ONLY`
dublado provaria apenas que o dublê concorda em ser somente leitura.

O cluster e' descartavel, escuta em 127.0.0.1 numa porta efemera e morre com a
sessao de teste. Nenhuma conexao sai da maquina.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pipelines import channel_offer_publisher as pub
from pipelines import channel_offer_sync as cos
from pipelines.tests.postgres_descartavel import (
    DDL_AUDITORIA, MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel,
)

pytestmark = pytest.mark.skipif(not postgres_disponivel(),
                                reason=MOTIVO_SEM_POSTGRES)

LOCK_CANAIS = 917120017

#: O relogio da conta. `observed_date` sai DAQUI, nunca de `now()`.
WATERMARK = datetime(2026, 9, 22, 9, 2, 6, tzinfo=timezone.utc)
DIA_TIKTOK = date(2026, 9, 22)

#: Miniatura com a MESMA estrutura da fonte real: pai sem variacao vira oferta,
#: pai com variacao e' container (preco proprio NULO, por definicao da fonte) e
#: cada variacao vira oferta. As proporcoes sao pequenas; a aritmetica e' a
#: mesma que produz 325 + 370 = 695 em producao.
PAIS_SIMPLES = 3       # viram oferta
PAIS_CONTAINER = 4     # NAO viram oferta; preco proprio nulo
MODELOS_POR_PAI = 2    # viram oferta
#: Uma das ofertas REAIS nasce sem preco na origem. Ela continua sem preco.
OFERTA_SEM_PRECO = 1

DDL_FONTE = """
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS silver;

CREATE TABLE gold.map_produto_codigo_gobeauty (
    marca text, codigo text, produto_sk bigint, ambiguo boolean);
CREATE TABLE gold.dim_produto_gobeauty (
    produto_sk bigint, is_kit boolean, ean text);
CREATE TABLE gold.bridge_kit_componente_gobeauty (
    kit_sk bigint);

CREATE TABLE silver.stg_shopee_products (
    shop_account text, brand text, item_id bigint, item_sku text,
    gtin_code text, item_name text, item_status text, is_kit boolean,
    has_model boolean, current_price numeric(14,4),
    original_price numeric(14,4), currency text, has_promotion boolean,
    promotion_id text, discount_pct numeric(9,4), ingested_at timestamptz);

CREATE TABLE silver.stg_shopee_product_models (
    shop_account text, brand text, item_id bigint, model_id bigint,
    model_sku text, model_name text, is_active boolean,
    current_price numeric(14,4), original_price numeric(14,4),
    currency text, ingested_at timestamptz);

CREATE TABLE silver.stg_tiktok_inventory (
    brand text, snapshot_date date, sku_id text, product_id text,
    seller_sku text, product_title text, product_status text,
    is_active boolean, sale_price numeric(14,4), currency text,
    fetched_at timestamptz);
"""


def _semeia(cur) -> None:
    """Popula a miniatura. Cada linha existe por um motivo declarado."""
    item = 0
    for i in range(PAIS_SIMPLES):
        item += 1
        # A ultima oferta simples nasce SEM preco na origem.
        preco = None if i == PAIS_SIMPLES - 1 else f"{10 + i}.50"
        cur.execute(
            "INSERT INTO silver.stg_shopee_products VALUES "
            "(%s,%s,%s,%s,%s,%s,'NORMAL',false,false,%s,NULL,'BRL',"
            "false,NULL,NULL,%s)",
            ("apice", "apice", item, f"SKU-P{item}", None,
             f"Produto {item}", preco, WATERMARK))
    for i in range(PAIS_CONTAINER):
        item += 1
        # Container: `has_model = true` e `current_price` NULO. O preco vive na
        # variacao — e' este nulo que, em producao, soma 337.
        cur.execute(
            "INSERT INTO silver.stg_shopee_products VALUES "
            "(%s,%s,%s,%s,%s,%s,'NORMAL',false,true,NULL,NULL,'BRL',"
            "false,NULL,NULL,%s)",
            ("apice", "apice", item, f"SKU-C{item}", None,
             f"Container {item}", WATERMARK))
        for m in range(MODELOS_POR_PAI):
            cur.execute(
                "INSERT INTO silver.stg_shopee_product_models VALUES "
                "(%s,%s,%s,%s,%s,%s,true,%s,NULL,'BRL',%s)",
                ("apice", "apice", item, item * 100 + m,
                 f"SKU-C{item}-{m}", f"Variacao {m}",
                 f"{20 + m}.25", WATERMARK - timedelta(seconds=5)))
    for i in range(3):
        cur.execute(
            "INSERT INTO silver.stg_tiktok_inventory VALUES "
            "(%s,%s,%s,%s,%s,%s,'ACTIVATE',true,%s,'BRL',%s)",
            ("apice", DIA_TIKTOK, f"SKU{i}", f"P{i}", f"SELLER-{i}",
             f"Oferta TikTok {i}", None if i == 0 else f"{30 + i}.00",
             WATERMARK))
    # Um dia ANTERIOR, que o ensaio nao pode escolher: a fotografia e' a mais
    # recente, nunca uma data conveniente.
    cur.execute(
        "INSERT INTO silver.stg_tiktok_inventory VALUES "
        "(%s,%s,%s,%s,%s,%s,'ACTIVATE',true,%s,'BRL',%s)",
        ("apice", DIA_TIKTOK - timedelta(days=1), "SKU-VELHO", "P9",
         "SELLER-9", "Oferta velha", "99.00", WATERMARK))


#: Ofertas esperadas na candidata da Shopee.
OFERTAS_SHOPEE = PAIS_SIMPLES + PAIS_CONTAINER * MODELOS_POR_PAI


@pytest.fixture(scope="module")
def fonte_url():
    """Cluster descartavel com a fonte semeada. Uma vez por modulo."""
    import psycopg2

    with cluster_da_sessao() as url:
        conn = psycopg2.connect(url)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS gold CASCADE")
                cur.execute("DROP SCHEMA IF EXISTS silver CASCADE")
                cur.execute(DDL_FONTE)
                cur.execute(DDL_AUDITORIA)
                cur.execute("TRUNCATE audit.source_sync_run")
                _semeia(cur)
        finally:
            conn.close()
        yield url


@pytest.fixture
def espioes(monkeypatch):
    """Registra QUALQUER caminho de escrita, lock ou auditoria alcancado."""
    tocados: list[str] = []

    def marca(nome):
        def _f(*a, **k):
            tocados.append(nome)
        return _f

    monkeypatch.setattr(pub, "_writable", marca("_writable"))
    monkeypatch.setattr(pub, "audit_start", marca("audit_start"))
    monkeypatch.setattr(pub, "audit_finish", marca("audit_finish"))
    monkeypatch.setattr(cos, "try_acquire_publication_lock",
                        marca("try_acquire_publication_lock"))
    monkeypatch.setattr(cos, "build_publication_plan",
                        marca("build_publication_plan"))
    return tocados


def _args(marketplace: str):
    return pub.build_cli().parse_args(["--marketplace", marketplace])


def _fabrica(url):
    """A fabrica REAL do ensaio: `cos._read_only`, contra o cluster local."""
    return lambda: cos._read_only(url)


# ---------------------------------------------------------------------------
# 1. O ensaio le de verdade e monta a candidata certa
# ---------------------------------------------------------------------------

def test_o_ensaio_le_a_fonte_real_e_monta_a_candidata(fonte_url, espioes):
    rel = pub.run_diagnose(_args("shopee"), connect_source=_fabrica(fonte_url))

    assert rel["mode"] == "dry_run"
    assert rel["rows"] == OFERTAS_SHOPEE, (
        "pai simples + variacao; os containers nao entram")
    assert rel["observed_dates"] == ["2026-09-22"]
    assert rel["accounts"] == ["apice"]
    assert espioes == [], f"o ensaio alcancou {espioes}"


def test_o_ensaio_do_tiktok_le_a_fonte_real(fonte_url, espioes):
    rel = pub.run_diagnose(_args("tiktok"), connect_source=_fabrica(fonte_url))
    assert rel["rows"] == 3, "so' o snapshot mais recente"
    assert rel["observed_dates"] == ["2026-09-22"]
    assert espioes == []


def test_a_candidata_do_ensaio_e_IGUAL_a_do_apply_contra_o_banco(fonte_url):
    """Equivalencia com linhas vindas do DRIVER, nao de uma lista literal.

    `collect_snapshot` e' a mesma funcao nos dois caminhos; este teste garante
    que continuara sendo, e que nenhuma coercao de tipo do psycopg2 entra so'
    num deles.
    """
    rel = pub.run_diagnose(_args("shopee"), connect_source=_fabrica(fonte_url))

    gate = pub.SourceGate()
    gate.mark_locked()
    conn = cos._read_only(fonte_url)
    try:
        registros, _, _ = pub.collect_snapshot(gate, conn, "shopee")
    finally:
        conn.close()

    assert pub.candidate_fingerprint(registros) == rel["fingerprint"]
    assert len(registros) == rel["rows"]


class _EspiaoDeConexao:
    """Proxy fino sobre a conexao real. O atributo `cursor` do psycopg2 e'
    somente leitura, entao o espiao precisa envolver a conexao em vez de
    remendar o objeto do driver."""

    def __init__(self, conn):
        self._conn = conn
        self.fabricas = []

    def cursor(self, *a, **k):
        self.fabricas.append(k.get("cursor_factory"))
        return self._conn.cursor(*a, **k)

    def __getattr__(self, nome):
        return getattr(self._conn, nome)


def test_o_cursor_usado_e_o_do_driver(fonte_url):
    """A leitura passa por `conn.cursor(cursor_factory=RealDictCursor)`.

    Nao e' detalhe: um fake que devolvesse tupla onde a producao usa
    `RealDictCursor` faz a suite inteira passar e o runtime falhar sempre.
    """
    from psycopg2.extras import RealDictCursor

    bruta = cos._read_only(fonte_url)
    espiao = _EspiaoDeConexao(bruta)
    try:
        gate = pub.SourceGate.for_diagnose()
        registros, _, _ = pub.collect_snapshot(gate, espiao, "shopee")
    finally:
        bruta.close()

    assert espiao.fabricas, "nenhum cursor foi aberto"
    assert all(f is RealDictCursor for f in espiao.fabricas)
    assert len(registros) == OFERTAS_SHOPEE


# ---------------------------------------------------------------------------
# 2. Somente leitura imposta pelo SERVIDOR
# ---------------------------------------------------------------------------

def test_a_transacao_do_ensaio_recusa_escrita_no_servidor(fonte_url):
    """Nao e' promessa do codigo: e' o PostgreSQL abortando o comando."""
    import psycopg2

    conn = cos._read_only(fonte_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            assert cur.fetchone()[0] == "on"
        with pytest.raises(psycopg2.Error):
            with conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE ensaio_nao_escreve (x int)")
    finally:
        conn.close()


def test_o_ensaio_nao_deixa_advisory_lock_no_servidor(fonte_url, espioes):
    import psycopg2

    pub.run_diagnose(_args("shopee"), connect_source=_fabrica(fonte_url))
    conn = psycopg2.connect(fonte_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND classid = 0 AND objid = %s", (LOCK_CANAIS,))
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_o_ensaio_nao_escreve_auditoria_no_servidor(fonte_url, monkeypatch):
    """Sem espiao: a prova e' a TABELA vazia depois do ensaio.

    Um espiao provaria que a funcao nao foi chamada; a tabela prova que nada
    foi gravado, inclusive por um caminho que o espiao nao conhece.
    """
    import psycopg2

    pub.run_diagnose(_args("shopee"), connect_source=_fabrica(fonte_url))
    pub.run_diagnose(_args("tiktok"), connect_source=_fabrica(fonte_url))
    conn = psycopg2.connect(fonte_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM audit.source_sync_run")
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_o_ensaio_nao_cria_nem_altera_relacao_alguma(fonte_url):
    """Fotografia do catalogo antes e depois: identica."""
    import psycopg2

    def catalogo():
        conn = psycopg2.connect(fonte_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE table_schema IN ('gold','silver','audit','marts') "
                    "ORDER BY 1,2")
                tabelas = cur.fetchall()
                cur.execute("SELECT count(*) FROM silver.stg_shopee_products")
                pais = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM silver.stg_shopee_product_models")
                modelos = cur.fetchone()[0]
            return tabelas, pais, modelos
        finally:
            conn.close()

    antes = catalogo()
    pub.run_diagnose(_args("shopee"), connect_source=_fabrica(fonte_url))
    assert catalogo() == antes


# ---------------------------------------------------------------------------
# 3. Preco ausente x preco zero, contra o banco
# ---------------------------------------------------------------------------

def test_o_nulo_da_fonte_chega_nulo_na_candidata(fonte_url):
    """`numeric` NULO atravessa o driver sem virar `Decimal('0')`."""
    rel = pub.run_diagnose(_args("shopee"), connect_source=_fabrica(fonte_url))
    assert rel["prices_absent"] == OFERTA_SEM_PRECO
    assert rel["prices_zero"] == 0


def test_os_nulos_dos_containers_nao_entram_na_contagem(fonte_url):
    """O nucleo da 'contradicao' dos 337, medido contra o banco.

    A fonte tem `PAIS_CONTAINER` nulos de preco alem do nulo da oferta real.
    Nenhum deles aparece em `prices_absent`, porque container nao e' oferta.
    """
    import psycopg2

    conn = psycopg2.connect(fonte_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM silver.stg_shopee_products "
                        "WHERE current_price IS NULL")
            nulos_na_fonte = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM silver.stg_shopee_products "
                        "WHERE has_model")
            containers = cur.fetchone()[0]
    finally:
        conn.close()

    rel = pub.run_diagnose(_args("shopee"), connect_source=_fabrica(fonte_url))

    # Os dois numeros verdadeiros que parecem se contradizer:
    assert nulos_na_fonte == containers + OFERTA_SEM_PRECO
    assert rel["prices_absent"] == OFERTA_SEM_PRECO
    # ... e a dimensao que os concilia.
    assert nulos_na_fonte - containers == rel["prices_absent"], (
        "'nulos na fonte' conta PAIS; 'ausentes na candidata' conta OFERTAS")


def test_a_aritmetica_do_grao_fecha_contra_o_banco(fonte_url):
    """pais_sem_variacao + modelos = candidata. Sem sobra e sem falta."""
    import psycopg2

    conn = psycopg2.connect(fonte_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM silver.stg_shopee_products")
            pais = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM silver.stg_shopee_products "
                        "WHERE NOT has_model")
            simples = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM silver.stg_shopee_product_models")
            modelos = cur.fetchone()[0]
    finally:
        conn.close()

    rel = pub.run_diagnose(_args("shopee"), connect_source=_fabrica(fonte_url))
    assert pais - simples == PAIS_CONTAINER, "containers = pais - pais simples"
    assert rel["rows"] == simples + modelos


# ---------------------------------------------------------------------------
# 4. A guarda do PR #28 continua valendo para destino remoto
# ---------------------------------------------------------------------------

def test_destino_remoto_e_bloqueado_antes_de_sair_da_maquina(monkeypatch):
    """O ensaio nao tem licenca para alcancar producao.

    Se algum caminho tentar um host que nao seja local, a guarda de
    `conftest.py` recusa — e e' por isso que ela e' default-deny por destino,
    nao uma lista do que e' proibido.
    """
    from pipelines.tests.conftest import ConexaoRealBloqueada

    monkeypatch.setenv(
        "DATAMART_DATABASE_URL",
        "postgresql://u:s@datamart.invalido:5432/db")
    with pytest.raises(ConexaoRealBloqueada):
        pub.run_diagnose(_args("shopee"))


def test_a_cli_sem_apply_nao_alcanca_destino_remoto(monkeypatch):
    """Pelo caminho da CLI, o bloqueio vira exit nao-zero — nunca 0."""
    monkeypatch.setenv(
        "DATAMART_DATABASE_URL",
        "postgresql://u:s@datamart.invalido:5432/db")
    from pipelines.tests.conftest import ConexaoRealBloqueada

    with pytest.raises(ConexaoRealBloqueada):
        # `ConexaoRealBloqueada` herda de BaseException justamente para nao ser
        # engolida por `except Exception` — inclusive o do ensaio.
        pub.main(["--marketplace", "shopee"])


def test_o_cluster_descartavel_e_local(fonte_url):
    """Guarda da propria prova: se o cluster deixasse de ser local, os testes
    acima estariam medindo outra coisa."""
    assert "127.0.0.1" in fonte_url
