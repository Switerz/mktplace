"""T-0015 - migration 033 e publicador da etapa contra PostgreSQL DESCARTAVEL.

Por que servidor real: `ADD COLUMN ... DEFAULT`, `CHECK` e `information_schema`
sao comportamento do servidor. Um duble concordaria com o teste.

DUAS PROVAS, SEPARADAS DE PROPOSITO
-----------------------------------
A 033 aponta para a `032` da Metas (PR #116), que aponta para a `031` da
frente Ads (PR #92); as duas ja' estao na base.

  1. CADEIA: o grafo REAL do alembic (`ScriptDirectory`) e' montado sobre o
     diretorio REAL de `versions/` (somente leitura). Prova `033 -> 032 ->
     031 -> 030 -> ...`, head unico em 033, base unica em 001 e cadeia linear.

  2. DDL: `upgrade()`/`downgrade()` da 033 rodam com o `op` VERDADEIRO do
     alembic (`MigrationContext` + `Operations`) sobre o schema da 018, tambem
     executada com o `op` verdadeiro. A 031 e a 032 nao tocam
     `expedicao_fila_atual`, entao nao ha' o que elas mudem no DDL desta prova.

Nunca toca Neon nem Data Mart: a URL sai de `postgres_descartavel`. Sem
binarios do PostgreSQL o arquivo inteiro e' SKIP.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import publisher
from pipelines.expedicao.contract import Etapa
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_da_sessao,
    postgres_disponivel,
)

REPO = Path(__file__).resolve().parents[2]
VERSIONS = REPO / "apps" / "api" / "alembic" / "versions"
ARQ_018 = VERSIONS / "018_create_expedicao_serving.py"
ARQ_033 = VERSIONS / "033_expedicao_fila_etapa.py"

sem_postgres = pytest.mark.skipif(
    not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES
)

AGORA = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _constantes_da_migration(caminho: Path) -> dict:
    """Constantes da migration SEM importar `alembic` (`op = None`).

    Na suite inteira algum modulo deixa `apps/api/alembic/` (que tem
    `__init__.py`) SOMBREAR o pacote instalado em `sys.modules`; importar a
    migration aqui dentro dependeria da ordem de coleta.
    """
    fonte = caminho.read_text(encoding="utf-8")
    assert fonte.count("from alembic import op") == 1
    ns: dict = {"__name__": f"_const_{caminho.stem}"}
    exec(compile(fonte.replace("from alembic import op", "op = None"),  # noqa: S102
                 str(caminho), "exec"), ns)
    return ns


def _meta(caminho: Path) -> dict:
    """revision/down_revision pela AST, sem importar."""
    fora = {}
    for no in ast.parse(caminho.read_text(encoding="utf-8")).body:
        if isinstance(no, ast.Assign) and len(no.targets) == 1:
            alvo = no.targets[0]
            if isinstance(alvo, ast.Name) and isinstance(no.value, ast.Constant):
                fora[alvo.id] = no.value.value
    return fora


def _python_isolado(roteiro: str, *args: str, cwd: Path) -> str:
    """Roda `roteiro` num processo FILHO com o alembic INSTALADO.

    Por que subprocesso: o `alembic` do processo do pytest pode ser o diretorio
    sombreado de `apps/api` (ver `_constantes_da_migration`). `-P` tira o cwd
    do `sys.path`, `PYTHONPATH` sai vazio e as URLs de banco do ambiente sao
    REMOVIDAS: o filho so' conhece a URL descartavel passada por argumento.
    """
    ambiente = {
        k: v for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "DATAMART_DATABASE_URL", "PYTHONPATH")
    }
    resultado = subprocess.run(
        [sys.executable, "-P", "-c", roteiro, *args],
        cwd=str(cwd), env=ambiente, capture_output=True, text=True, timeout=120,
    )
    if resultado.returncode != 0:
        raise AssertionError(
            "processo filho falhou: " + resultado.stderr[-2000:]
        )
    return resultado.stdout


ROTEIRO_MIGRATION = """
import importlib.util, sys
import alembic
assert 'site-packages' in (alembic.__file__ or ''), alembic.__file__
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
url, caminho, funcao = sys.argv[1:4]
spec = importlib.util.spec_from_file_location('_mig', caminho)
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)
eng = create_engine(url, future=True)
with eng.begin() as conn:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(modulo, funcao)()
eng.dispose()
"""


def rodar_migration(url: str, caminho: Path, funcao: str) -> None:
    """Executa `upgrade`/`downgrade` com o `op` VERDADEIRO do alembic."""
    _python_isolado(ROTEIRO_MIGRATION, url, str(caminho), funcao, cwd=REPO)


def _url_db(base: str, nome: str) -> str:
    return base.rsplit("/", 1)[0] + "/" + nome


DDL_BASE = (
    "CREATE SCHEMA marts",
    # Recorte da 002: so' o que a FK `fk_efa_brand` da 018 exige.
    "CREATE TABLE marts.dim_loja (loja_id INT PRIMARY KEY, "
    "brand_key VARCHAR(50) NOT NULL UNIQUE)",
    "INSERT INTO marts.dim_loja VALUES (1, 'kokeshi'), (2, 'barbours')",
)


@pytest.fixture
def banco_018():
    """Banco novo por teste, com o schema da 018 aplicado pelo `op` real."""
    import psycopg2

    with cluster_da_sessao() as base:
        adm = psycopg2.connect(base)
        adm.autocommit = True
        with adm.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS etapa_alvo WITH (FORCE)")
            cur.execute("CREATE DATABASE etapa_alvo")
        adm.close()
        url = _url_db(base, "etapa_alvo")
        conn = psycopg2.connect(url)
        with conn, conn.cursor() as cur:
            for stmt in DDL_BASE:
                cur.execute(stmt)
        conn.close()
        rodar_migration(url, ARQ_018, "upgrade")
        yield url


def colunas_da_fila(url: str) -> dict[str, dict]:
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = 'marts' AND table_name = 'expedicao_fila_atual'"
            )
            return {
                r[0]: {"tipo": r[1], "largura": r[2], "nulo": r[3], "default": r[4]}
                for r in cur.fetchall()
            }
    finally:
        conn.close()


def linha_ml_minima(pedido: str, **extra) -> dict:
    """Uma linha que satisfaz todos os CHECK da 018 (formato do ML)."""
    linha = {
        "effective_at": AGORA,
        "refresh_batch_id": "lote-t0015",
        "channel": "mercadolivre",
        "shop_account": "2532564723",
        "marketplace_order_id": pedido,
        "brand": "barbours",
        "created_at": AGORA,
        "paid_at": None,
        "dispatch_deadline": None,
        "deadline_source": "unavailable",
        "deadline_status": "unavailable",
        "operational_age_status": "within_48h",
        "is_slow_vs_baseline": False,
        "is_source_zombie": False,
        "is_stalled": False,
        "hours_open": 1.5,
        "hours_overdue": None,
        "logistic_type": "cross_docking",
        "carrier": None,
        "source_ingested_at": AGORA,
        "source_freshness_status": "fresh",
        "timestamp_quality": "assumed",
    }
    linha.update(extra)
    return linha


def inserir(url: str, linha: dict) -> None:
    import psycopg2

    colunas = list(linha)
    conn = psycopg2.connect(url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO marts.expedicao_fila_atual ({', '.join(colunas)}) "
                f"VALUES ({', '.join(['%s'] * len(colunas))})",
                [linha[c] for c in colunas],
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Estatico: revisao e dominio
# ---------------------------------------------------------------------------
def test_033_aponta_para_032_da_metas():
    meta = _meta(ARQ_033)
    assert meta["revision"] == "033"
    assert meta["down_revision"] == "032"
    assert meta["branch_labels"] is None
    assert meta["depends_on"] is None


def test_check_da_033_e_o_dominio_do_contrato():
    """O literal da migration e o enum do contrato sao a MESMA lista."""
    consts = _constantes_da_migration(ARQ_033)
    assert set(consts["ETAPAS"]) == {e.value for e in Etapa}
    assert len(consts["ETAPAS"]) == len(set(consts["ETAPAS"])) == 9
    assert consts["ETAPA_DEFAULT"] == Etapa.NAO_PUBLICADA.value


def test_033_declara_que_nao_e_aplicada_pelo_gate():
    doc = ast.get_docstring(ast.parse(ARQ_033.read_text(encoding="utf-8")))
    assert "NAO E' APLICADA PELO GATE" in doc
    assert "032" in doc


# ---------------------------------------------------------------------------
# Cadeia: grafo REAL do alembic sobre o diretorio REAL de `versions/`
# ---------------------------------------------------------------------------
ROTEIRO_GRAFO = """
import json, sys
from alembic.script import ScriptDirectory
script = ScriptDirectory(sys.argv[1])
revisoes = [r.revision for r in script.walk_revisions('base', 'heads')]
print(json.dumps({
    'heads': list(script.get_heads()),
    'bases': list(script.get_bases()),
    'revisoes': revisoes,
    'pais': {r: script.get_revision(r).down_revision for r in revisoes},
    'filhos': {r: len(script.get_revision(r).nextrev) for r in revisoes},
}))
"""


def _revisoes_por_ast() -> dict[str, str | None]:
    revisoes = {}
    for caminho in VERSIONS.glob("*.py"):
        meta = _meta(caminho)
        if "revision" in meta:
            revisoes[meta["revision"]] = meta.get("down_revision")
    return revisoes


def test_cadeia_real_e_linear_e_termina_na_033(tmp_path):
    """Grafo REAL do alembic sobre `apps/api/alembic/versions/` (so' leitura)."""
    grafo = json.loads(
        _python_isolado(ROTEIRO_GRAFO, str(VERSIONS.parent), cwd=tmp_path)
    )
    assert grafo["heads"] == ["033"]
    assert grafo["bases"] == ["001"]
    assert grafo["revisoes"][:4] == ["033", "032", "031", "030"]
    assert grafo["pais"]["033"] == "032"
    assert grafo["pais"]["032"] == "031"
    assert grafo["pais"]["031"] == "030"
    assert grafo["revisoes"][-1] == "001"
    # Linear: nenhuma revisao com mais de um filho nem mais de um pai (merge).
    assert all(n <= 1 for n in grafo["filhos"].values()), grafo["filhos"]
    assert all(p is None or isinstance(p, str) for p in grafo["pais"].values()), (
        grafo["pais"]
    )
    # Toda revisao do diretorio esta' na caminhada head -> base: nada pendurado.
    assert sorted(grafo["revisoes"]) == sorted(_revisoes_por_ast())


def test_nenhum_pai_ausente_e_a_031_real_aponta_para_030():
    """Com a 031 (Ads) e a 032 (Metas) na base, nenhum `down_revision` fica no ar."""
    revisoes = _revisoes_por_ast()
    ausentes = {
        pai for pai in revisoes.values() if pai is not None and pai not in revisoes
    }
    assert ausentes == set()
    assert revisoes["031"] == "030"
    assert revisoes["032"] == "031"
    assert revisoes["033"] == "032"


# ---------------------------------------------------------------------------
# DDL contra PostgreSQL real
# ---------------------------------------------------------------------------
@sem_postgres
def test_upgrade_cria_as_duas_colunas_com_tipo_default_e_nulidade(banco_018):
    antes = colunas_da_fila(banco_018)
    assert "etapa" not in antes and "etapa_fonte" not in antes

    rodar_migration(banco_018, ARQ_033, "upgrade")

    depois = colunas_da_fila(banco_018)
    assert depois["etapa"]["tipo"] == "character varying"
    assert depois["etapa"]["largura"] == 30
    assert depois["etapa"]["nulo"] == "NO"
    assert depois["etapa"]["default"].startswith("'nao_publicada'")
    assert depois["etapa_fonte"]["tipo"] == "character varying"
    assert depois["etapa_fonte"]["largura"] == 40
    assert depois["etapa_fonte"]["nulo"] == "YES"
    # Nenhuma outra coluna mudou.
    assert set(depois) - set(antes) == {"etapa", "etapa_fonte"}


@sem_postgres
def test_linhas_antigas_recebem_nao_publicada(banco_018):
    import psycopg2

    inserir(banco_018, linha_ml_minima("antiga-1"))
    rodar_migration(banco_018, ARQ_033, "upgrade")
    conn = psycopg2.connect(banco_018)
    with conn.cursor() as cur:
        cur.execute("SELECT etapa, etapa_fonte FROM marts.expedicao_fila_atual")
        assert cur.fetchall() == [("nao_publicada", None)]
    conn.close()


@sem_postgres
def test_check_do_banco_e_exatamente_o_dominio_do_contrato(banco_018):
    import psycopg2

    rodar_migration(banco_018, ARQ_033, "upgrade")
    conn = psycopg2.connect(banco_018)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_efa_dominio_etapa'"
        )
        definicao = cur.fetchone()[0]
    conn.close()
    assert set(re.findall(r"'([a-z_]+)'", definicao)) == {e.value for e in Etapa}

    # Todo valor do dominio passa; um valor fora dele e' recusado.
    for i, etapa in enumerate(Etapa):
        inserir(banco_018, linha_ml_minima(f"ok-{i}", etapa=etapa.value))
    with pytest.raises(psycopg2.errors.CheckViolation):
        inserir(banco_018, linha_ml_minima("ruim", etapa="despachado"))


@sem_postgres
def test_etapa_fonte_acima_de_40_e_recusada_pelo_banco(banco_018):
    """Por isso o contrato corta o bruto em 40 antes de publicar."""
    import psycopg2

    rodar_migration(banco_018, ARQ_033, "upgrade")
    with pytest.raises(psycopg2.errors.StringDataRightTruncation):
        inserir(banco_018, linha_ml_minima("longo", etapa="outro",
                                           etapa_fonte="x" * 41))


@sem_postgres
def test_downgrade_remove_as_duas_colunas_e_o_check(banco_018):
    import psycopg2

    antes = colunas_da_fila(banco_018)
    rodar_migration(banco_018, ARQ_033, "upgrade")
    inserir(banco_018, linha_ml_minima("p1", etapa="a_imprimir",
                                       etapa_fonte="ready_to_print"))
    rodar_migration(banco_018, ARQ_033, "downgrade")

    assert colunas_da_fila(banco_018) == antes
    conn = psycopg2.connect(banco_018)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_efa_dominio_etapa'"
        )
        assert cur.fetchone()[0] == 0
        # A linha sobrevive ao downgrade; so' as colunas novas saem.
        cur.execute("SELECT marketplace_order_id FROM marts.expedicao_fila_atual")
        assert cur.fetchall() == [("p1",)]
    conn.close()

    # Reversivel: sobe de novo sem erro.
    rodar_migration(banco_018, ARQ_033, "upgrade")
    assert "etapa" in colunas_da_fila(banco_018)


def test_fila_columns_da_018_nao_mudou():
    """As colunas novas NAO entram em FILA_COLUMNS: elas sao condicionais."""
    assert "etapa" not in publisher.FILA_COLUMNS
    assert "etapa_fonte" not in publisher.FILA_COLUMNS


# ---------------------------------------------------------------------------
# Publicador contra PostgreSQL real, com e sem a 033
# ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402

from pipelines.expedicao import ml_extract, transform  # noqa: E402
from pipelines.expedicao.contract import (  # noqa: E402
    Channel,
    SellerAccount,
    SourceHealth,
)

LOTE = "lote-t0015-pub"
SHOPEE_EXT, SHOPEE_CONTA = "111", "loja_kokeshi"
ML_EXT = "2532564723"
AGORA_NAIVE = AGORA.replace(tzinfo=None)


def _linha_shopee(order_sn, status):
    return {
        "shop_account": SHOPEE_CONTA, "order_sn": order_sn, "shop_id": 111,
        "order_status": status,
        "create_time": AGORA - timedelta(hours=5),
        "pay_time": AGORA - timedelta(hours=4),
        "ship_by_date": AGORA + timedelta(hours=30),
        "pickup_done_time": None, "shipping_carrier": "SPX",
        "ingested_at": AGORA - timedelta(minutes=20),
    }


def _linha_ml(shipment_id, substatus, logistic_type="cross_docking"):
    return {
        "seller_id": int(ML_EXT), "brand": "barbours", "shipment_id": shipment_id,
        "order_id": f"o{shipment_id}", "shipment_status": "ready_to_ship",
        "substatus": substatus, "logistic_type": logistic_type,
        "order_status": "paid",
        "order_created_at": AGORA_NAIVE - timedelta(hours=10),
        "date_created": AGORA_NAIVE - timedelta(hours=10),
        "date_ready_to_ship": AGORA_NAIVE - timedelta(hours=9),
        "date_shipped": None, "date_cancelled": None,
        "tracking_method": "Normal",
        "extracted_at": AGORA_NAIVE - timedelta(minutes=30),
    }


#: Shopee: os dois status medidos + um valor novo da fonte.
SHOPEE_LINHAS = [
    _linha_shopee("S1", "PROCESSED"), _linha_shopee("S2", "PROCESSED"),
    _linha_shopee("S3", "READY_TO_SHIP"), _linha_shopee("S4", "STATUS_NOVO"),
]
#: ML: Full (fica fora), vendedor com todos os substatus medidos e um novo.
ML_CANDIDATOS = [
    _linha_ml("F1", "in_warehouse", "fulfillment"),
    _linha_ml("F2", "in_packing_list", "fulfillment"),
    _linha_ml("F3", "ready_to_pack", "fulfillment"),
    _linha_ml("F4", "packed", "fulfillment"),
    _linha_ml("V1", "in_hub"), _linha_ml("V2", "picked_up", "xd_drop_off"),
    _linha_ml("V3", "ready_for_pickup", "drop_off"),
    _linha_ml("V4", "in_packing_list", "self_service"),
    _linha_ml("V5", "dropped_off"), _linha_ml("V6", "ready_to_print", "xd_drop_off"),
    _linha_ml("V7", "invoice_pending"), _linha_ml("V8", "stale"),
    _linha_ml("V9", "delayed"), _linha_ml("V10", "substatus_novo"),
]


def _lote(canal):
    if canal is Channel.SHOPEE:
        fila = transform.build_fila_shopee(
            SHOPEE_LINHAS, {SHOPEE_EXT: SellerAccount(SHOPEE_EXT, 1, "kokeshi")},
            {}, AGORA, LOTE,
        )
        contas = {SHOPEE_EXT: (SHOPEE_CONTA, "kokeshi")}
    else:
        vendedor, _diag = ml_extract.classify_candidates(ML_CANDIDATOS, AGORA)
        fila = transform.build_fila_ml(
            vendedor, {ML_EXT: SellerAccount(ML_EXT, 2, "barbours")}, AGORA, LOTE
        )
        contas = {ML_EXT: (ML_EXT, "barbours")}
    resumo = transform.build_account_summaries(
        fila, AGORA, channel=canal.value, refresh_batch_id=LOTE, accounts=contas,
        watermarks={ext: AGORA for ext in contas}, source_advanced=True,
    )
    return fila, resumo, frozenset(c for c, _ in contas.values())


def _publicar(url, canal):
    import psycopg2
    from psycopg2.extras import RealDictCursor, execute_values

    fila, resumo, esperadas = _lote(canal)
    relatorio: dict = {}
    # RealDictCursor, como o `open_target_default` de producao.
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    try:
        linhas, _ = publisher.publish_channel(
            conn, canal, fila, resumo, source_health=SourceHealth.HEALTHY,
            refresh_batch_id=LOTE, expected_accounts=esperadas,
            execute_values=execute_values, relatorio=relatorio,
        )
    finally:
        conn.close()
    return linhas, relatorio


def _consultar(url, sql):
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
    finally:
        conn.close()


@sem_postgres
def test_publicador_sem_a_033_escreve_como_hoje(banco_018):
    for canal in (Channel.SHOPEE, Channel.MERCADOLIVRE):
        linhas, relatorio = _publicar(banco_018, canal)
        assert linhas > 0
        assert relatorio["etapa_colunas_escritas"] == ()
        # Mesmo sem a coluna, a contagem fecha com o que foi publicado.
        assert sum(relatorio["etapa_por_etapa"].values()) == linhas
    assert "etapa" not in colunas_da_fila(banco_018)
    por_canal = dict(_consultar(
        banco_018,
        "SELECT channel, count(*) FROM marts.expedicao_fila_atual GROUP BY channel",
    ))
    assert por_canal == {"shopee": 4, "mercadolivre": 10}


@sem_postgres
def test_publicador_com_a_033_escreve_etapa_e_reconcilia_por_canal(banco_018, caplog):
    import logging

    # Linha ANTIGA, de antes da 033: recebe `nao_publicada` e e' substituida.
    inserir(banco_018, linha_ml_minima("antiga"))
    rodar_migration(banco_018, ARQ_033, "upgrade")

    publicadas, relatorios = {}, {}
    with caplog.at_level(logging.WARNING, logger=publisher.__name__):
        for canal in (Channel.SHOPEE, Channel.MERCADOLIVRE):
            publicadas[canal.value], relatorios[canal.value] = _publicar(
                banco_018, canal
            )

    for canal in ("shopee", "mercadolivre"):
        assert relatorios[canal]["etapa_colunas_escritas"] == ("etapa", "etapa_fonte")

    # RECONCILIACAO: soma por etapa no BANCO == linhas publicadas por canal ==
    # contagem do relatorio.
    no_banco: dict[str, dict[str, int]] = {}
    for canal, etapa, n in _consultar(
        banco_018,
        "SELECT channel, etapa, count(*) FROM marts.expedicao_fila_atual "
        "GROUP BY channel, etapa",
    ):
        no_banco.setdefault(canal, {})[etapa] = n
    for canal, linhas in publicadas.items():
        assert sum(no_banco[canal].values()) == linhas
        assert no_banco[canal] == relatorios[canal]["etapa_por_etapa"]

    assert no_banco["shopee"] == {"aguardando_coleta": 2, "a_preparar": 1, "outro": 1}
    assert no_banco["mercadolivre"] == {
        "entregue_transportadora": 3, "aguardando_coleta": 1, "em_separacao": 1,
        "a_imprimir": 1, "a_faturar": 1, "sinalizado_ml": 2, "outro": 1,
    }
    # A linha antiga saiu com o DELETE do canal; nada ficou `nao_publicada`.
    assert all("nao_publicada" not in e for e in no_banco.values())

    # Full do ML continua fora.
    ids_ml = {r[0] for r in _consultar(
        banco_018,
        "SELECT marketplace_order_id FROM marts.expedicao_fila_atual "
        "WHERE channel = 'mercadolivre'",
    )}
    assert ids_ml == {f"V{i}" for i in range(1, 11)}
    assert _consultar(
        banco_018,
        "SELECT count(*) FROM marts.expedicao_fila_atual "
        "WHERE logistic_type = 'fulfillment' OR etapa_fonte = 'in_warehouse'",
    ) == [(0,)]

    # `outro` preserva o bruto, e' contado e avisado - sem bloquear.
    assert sorted(_consultar(
        banco_018,
        "SELECT channel, etapa_fonte FROM marts.expedicao_fila_atual "
        "WHERE etapa = 'outro'",
    )) == [("mercadolivre", "substatus_novo"), ("shopee", "STATUS_NOVO")]
    assert relatorios["shopee"]["etapa_outro_count"] == 1
    assert relatorios["shopee"]["etapa_outro_fontes"] == ["STATUS_NOVO"]
    assert relatorios["mercadolivre"]["etapa_outro_count"] == 1
    avisos = [r.getMessage() for r in caplog.records]
    assert any("STATUS_NOVO" in m for m in avisos)
    assert any("substatus_novo" in m for m in avisos)


@sem_postgres
def test_deteccao_e_por_execucao(banco_018):
    """Mesmo processo: publica sem a 033, a 033 chega, a proxima publicacao a usa."""
    _, antes = _publicar(banco_018, Channel.SHOPEE)
    assert antes["etapa_colunas_escritas"] == ()
    assert _consultar(
        banco_018, "SELECT count(*) FROM marts.expedicao_fila_atual"
    ) == [(4,)]

    rodar_migration(banco_018, ARQ_033, "upgrade")
    # As linhas ja' publicadas viram `nao_publicada` (o default)...
    assert _consultar(
        banco_018, "SELECT DISTINCT etapa FROM marts.expedicao_fila_atual"
    ) == [("nao_publicada",)]

    _, depois = _publicar(banco_018, Channel.SHOPEE)
    assert depois["etapa_colunas_escritas"] == ("etapa", "etapa_fonte")
    # ...e somem na proxima publicacao do canal.
    assert _consultar(
        banco_018,
        "SELECT count(*) FROM marts.expedicao_fila_atual "
        "WHERE etapa = 'nao_publicada'",
    ) == [(0,)]

    # Downgrade: o publicador volta sozinho ao INSERT antigo.
    rodar_migration(banco_018, ARQ_033, "downgrade")
    _, sem = _publicar(banco_018, Channel.SHOPEE)
    assert sem["etapa_colunas_escritas"] == ()


# ---------------------------------------------------------------------------
# --reconcile contra PostgreSQL real, com e sem a 033 (T-0015 fase A)
# ---------------------------------------------------------------------------
class _FonteMl:
    """Fonte de mentira do ML. O DESTINO e' o Postgres descartavel."""

    def __init__(self, candidatos):
        self.candidatos = candidatos

    def cursor(self):
        fonte = self

        class _C:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, sql, params=None):
                self._r = (
                    [{"seller_id": int(ML_EXT),
                      "max_extracted_at": AGORA_NAIVE - timedelta(minutes=30)}]
                    if "MAX(s.extracted_at)" in sql else fonte.candidatos
                )

            def fetchall(self):
                return self._r

        return _C()

    def close(self):
        pass


def _reconciliar_ml(url):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from pipelines.expedicao import cli

    leitor = psycopg2.connect(url, cursor_factory=RealDictCursor)
    leitor.set_session(readonly=True, autocommit=True)
    try:
        return cli.reconcile_channel(
            leitor, _FonteMl(ML_CANDIDATOS), Channel.MERCADOLIVRE,
            open_registry=lambda _c, _m: (
                {ML_EXT: SellerAccount(ML_EXT, 2, "barbours")}, []
            ),
        )
    finally:
        leitor.close()


def _executar(url, sql):
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


@sem_postgres
def test_reconcile_sem_a_033_nao_toca_na_etapa(banco_018):
    _publicar(banco_018, Channel.MERCADOLIVRE)
    r = _reconciliar_ml(banco_018)
    assert r["publicadas"] == r["recomputadas"] == r["comparaveis"] == 10
    assert "etapa_reconciliada" not in r


@sem_postgres
def test_reconcile_com_a_033_compara_etapa_e_pega_drift(banco_018):
    from pipelines.expedicao.contract import SourceUnhealthy

    rodar_migration(banco_018, ARQ_033, "upgrade")
    _publicar(banco_018, Channel.MERCADOLIVRE)

    r = _reconciliar_ml(banco_018)
    assert r["comparaveis"] == 10
    assert r["etapa_reconciliada"] is True
    assert r["etapa_nao_publicada"] == 0

    # Etapa adulterada no banco, mesma entrada: drift fechado.
    _executar(banco_018,
              "UPDATE marts.expedicao_fila_atual SET etapa = 'a_faturar' "
              "WHERE marketplace_order_id = 'V6'")
    with pytest.raises(SourceUnhealthy) as erro:
        _reconciliar_ml(banco_018)
    assert "etapa: publicado='a_faturar' recomputado='a_imprimir'" in str(erro.value)

    # `nao_publicada` (default da coluna) nao e' drift: contado a' parte.
    _executar(banco_018,
              "UPDATE marts.expedicao_fila_atual SET etapa = 'nao_publicada'")
    r = _reconciliar_ml(banco_018)
    assert r["etapa_nao_publicada"] == 10
    assert r["veredito"] == "deterministico_no_subconjunto_comparavel"
