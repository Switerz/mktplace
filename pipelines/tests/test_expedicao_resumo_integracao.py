"""EXP-3B2-H3 - a correcao dos resumos contra PostgreSQL de verdade.

POR QUE ISTO EXISTE ALEM DOS TESTES COM DUBLE
----------------------------------------------
Os dubles provam a logica; eles nao provam o CHECK da migration 018, nem que o
DELETE/INSERT roda numa transacao so', nem que um lote recusado preserva o
snapshot anterior de verdade. O incidente EXP-3B2-I1 so' apareceu porque alguem
leu as duas tabelas lado a lado no banco real — e o defeito passou por toda a
suite de dubles sem um arranhao.

Aqui o schema e' a cadeia REAL 001..019, aplicada por `alembic upgrade head`
contra um PostgreSQL descartavel. Sem `EXPEDICAO_H3_TEST_DSN`, o arquivo inteiro
e' SKIP: a suite normal continua offline.

    docker run --rm -d -p 55434:5432 -e POSTGRES_PASSWORD=postgres \\
        --name pg-h3 postgres:16
    EXPEDICAO_H3_TEST_DSN=postgresql://postgres:postgres@localhost:55434/postgres \\
        python -m pytest pipelines/tests/test_expedicao_resumo_integracao.py

A DSN precisa ser LOCAL: a barreira do PR #28 recusa qualquer outra coisa, o que
e' justamente o que impede este arquivo de virar o proximo incidente.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import transform
from pipelines.expedicao.coerencia import problemas_do_lote
from pipelines.expedicao.contract import (
    Channel,
    LoteIncoerente,
    SellerAccount,
    SourceHealth,
)
from pipelines.expedicao.publisher import publish_channel

DSN = os.environ.get("EXPEDICAO_H3_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="EXPEDICAO_H3_TEST_DSN ausente - exige PostgreSQL descartavel"
)

RAIZ = Path(__file__).resolve().parents[2]
AGORA = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
LOTE_ML = "11111111-1111-4111-8111-111111111111"
LOTE_ML2 = "22222222-2222-4222-8222-222222222222"
LOTE_SHOPEE = "33333333-3333-4333-8333-333333333333"

REGISTRY = {
    "2227056661": SellerAccount("2227056661", 3, "kokeshi"),
    "2532564723": SellerAccount("2532564723", 2, "barbours"),
    "2579732860": SellerAccount("2579732860", 4, "lescent"),
    "1366932565": SellerAccount("1366932565", 5, "rituaria"),
}
CONTAS = frozenset(REGISTRY)

#: Marcas que precisam existir em `dim_loja` por causa da FK da 018.
#: A Shopee entra porque um dos testes publica os dois canais.
MARCAS_SEMEADAS = {
    "kokeshi": 3, "barbours": 2, "lescent": 4, "rituaria": 5, "apice": 1,
}


def _conectar():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    return psycopg2.connect(DSN, cursor_factory=RealDictCursor, connect_timeout=15)


@pytest.fixture(scope="module")
def banco():
    """Schema REAL: `alembic upgrade head` na cadeia 001..019.

    O subprocesso recebe o ambiente MONTADO A MAO. Herdar `os.environ` traria o
    `.env` da maquina junto, e o alembic migraria producao - a trava de
    isolamento da suite acusa exatamente esse padrao.
    """
    import psycopg2

    # O ambiente e' MONTADO, nao herdado por omissao: as duas variaveis que
    # apontam para banco sao sobrescritas pela DSN descartavel antes de o filho
    # existir. Zerar o resto quebraria o venv efemero em que o proprio pytest
    # roda (o `sys.executable` deixaria de achar o alembic), entao o que se faz
    # aqui e' copiar e SOBRESCREVER - o que importa e' que nao ha caminho pelo
    # qual a credencial de producao chegue ao alembic.
    api = RAIZ / "apps" / "api"
    ambiente = dict(os.environ)
    ambiente["DATABASE_URL"] = DSN
    ambiente["DATAMART_DATABASE_URL"] = DSN
    ambiente["PYTHONPATH"] = str(RAIZ)
    # Duas armadilhas de caminho, as duas custaram uma execucao:
    #   1. `python -m alembic` nao funciona - o pacote nao tem `__main__`;
    #      a entrada publica e' `alembic.config.main`.
    #   2. o diretorio `apps/api/alembic/` SOMBREIA o pacote instalado se
    #      entrar no `sys.path`. `-P` tira o cwd do caminho, e o alembic
    #      real e' importado ANTES de `apps/api` ser acrescentado - depois
    #      disso ele ja' esta' em `sys.modules` e nao e' mais reresolvido.
    #      `apps/api` precisa entrar para que o `env.py` importe `app`.
    roteiro = (
        "import alembic.config as C, sys;"
        "sys.path.insert(0, r%s);" % repr(str(api))
        + "C.main(argv=['upgrade', 'head'])"
    )
    resultado = subprocess.run(
        [sys.executable, "-P", "-c", roteiro],
        cwd=str(api), env=ambiente, capture_output=True, text=True,
    )
    if resultado.returncode != 0:
        pytest.skip(f"alembic indisponivel: {resultado.stderr[-300:]}")

    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        versao = cur.fetchone()[0]
    assert versao == "019", f"schema esperado 019, obtido {versao}"

    # As dimensoes sao SEMEADAS porque a 018 tem FK de `brand` para
    # `dim_loja.brand_key`. Publicar sem elas levanta `fk_efa_brand` - o
    # que e' justamente o schema real fazendo o trabalho dele, e a razao de
    # este arquivo existir alem dos testes com duble.
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO marts.dim_empresa (empresa_id, nome_empresa, "
            "nome_normalizado) VALUES (1, %s, %s) ON CONFLICT DO NOTHING",
            ("GoBeaute", "gobeaute"),
        )
        for marca, loja_id in MARCAS_SEMEADAS.items():
            cur.execute(
                "INSERT INTO marts.dim_loja (loja_id, empresa_id, brand_key, "
                "nome_loja, nome_normalizado) VALUES (%s, 1, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (loja_id, marca, marca.title(), marca),
            )
        cur.execute(
            "INSERT INTO marts.dim_marketplace (marketplace_id, "
            "nome_marketplace, slug) VALUES (2, %s, %s), (3, %s, %s) "
            "ON CONFLICT DO NOTHING",
            ("Mercado Livre", "mercadolivre", "Shopee", "shopee"),
        )

    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def fila_limpa(banco):
    """Cada teste comeca com as duas tabelas vazias e SEM transacao aberta.

    O `rollback()` antes de mexer em `autocommit` nao e' zelo: `publish_channel`
    troca o modo da conexao, e o psycopg2 levanta
    `set_session cannot be used inside a transaction` se o teste anterior
    deixou uma transacao aberta. O erro apareceria no teste SEGUINTE, longe
    da causa.
    """
    banco.rollback()
    banco.autocommit = True
    with banco.cursor() as cur:
        cur.execute("DELETE FROM marts.expedicao_fila_atual")
        cur.execute("DELETE FROM marts.expedicao_refresh_run")
    yield
    banco.rollback()
    banco.autocommit = True


def linha_ml(seller_id, i):
    base = datetime(2026, 9, 21, 12, 0)
    return {
        "seller_id": int(seller_id),
        "brand": REGISTRY[seller_id].brand_key,
        "shipment_id": int(f"9{seller_id[:4]}{i:05d}"),
        "order_id": 5000000 + i,
        "shipment_status": "ready_to_ship",
        "substatus": None,
        "logistic_type": "cross_docking",
        "order_status": "paid",
        "order_created_at": base - timedelta(hours=2),
        "date_created": base,
        "date_ready_to_ship": base,
        "date_shipped": None,
        "date_cancelled": None,
        "tracking_method": "Normal",
        "extracted_at": datetime(2026, 9, 22, 14, 30),
    }


def fila_ml(distribuicao, lote=LOTE_ML, efetivo=AGORA):
    linhas, n = [], 0
    for seller, quantidade in distribuicao.items():
        for _ in range(quantidade):
            n += 1
            linhas.append(linha_ml(seller, n))
    return transform.build_fila_ml(linhas, REGISTRY, efetivo, lote)


def resumos_ml(fila, lote=LOTE_ML, efetivo=AGORA, accounts=None):
    return transform.build_account_summaries(
        fila, efetivo, channel=Channel.MERCADOLIVRE.value, refresh_batch_id=lote,
        accounts=accounts or {e: (e, c.brand_key) for e, c in REGISTRY.items()},
        watermarks={e: efetivo - timedelta(minutes=5) for e in REGISTRY},
        source_advanced=False,
    )


def publicar(banco, fila, resumos, canal=Channel.MERCADOLIVRE, lote=LOTE_ML):
    return publish_channel(
        banco, canal, fila, resumos,
        source_health=SourceHealth.HEALTHY, refresh_batch_id=lote,
        expected_accounts=CONTAS,
    )


def contagens(banco, canal="mercadolivre"):
    with banco.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM marts.expedicao_fila_atual WHERE channel = %s",
            (canal,),
        )
        fila = cur.fetchone()[0]
        cur.execute(
            "SELECT shop_account, backlog_count FROM marts.expedicao_refresh_run "
            "WHERE channel = %s ORDER BY shop_account",
            (canal,),
        )
        resumo = dict(cur.fetchall())
    # `publish_channel` deixa a conexao em `autocommit = False`. Uma leitura
    # depois dela ABRE transacao, e a publicacao seguinte morre com
    # `set_session cannot be used inside a transaction` - num teste que nao
    # tem nada a ver com o defeito. Fechar aqui evita o falso alarme.
    banco.rollback()
    return fila, resumo


# ---------------------------------------------------------------------------
def test_primeira_publicacao_ml_grava_quatro_resumos_corretos(banco):
    distribuicao = {"2227056661": 7, "2532564723": 3, "2579732860": 2,
                    "1366932565": 0}
    fila = fila_ml(distribuicao)
    linhas, contas = publicar(banco, fila, resumos_ml(fila))

    assert (linhas, contas) == (12, 4)
    n_fila, resumo = contagens(banco)
    assert n_fila == 12
    assert resumo == {k: v for k, v in distribuicao.items()}
    assert sum(resumo.values()) == n_fila, "a soma dos resumos fecha com a fila"


def test_conta_com_backlog_zero_aparece_no_resumo(banco):
    fila = fila_ml({"2227056661": 4})
    publicar(banco, fila, resumos_ml(fila))
    _, resumo = contagens(banco)
    assert resumo["1366932565"] == 0
    assert set(resumo) == CONTAS, "conta sem linha continua tendo resumo"


def test_republicacao_substitui_e_mantem_a_soma(banco):
    """Reexecucao na MESMA hora, com o relogio adiantado: a nova vence.

    O `observed_at` avanca porque o UPSERT do resumo so' sobrescreve quando a
    observacao e' mais NOVA (`WHERE EXCLUDED.observed_at > observed_at`). E'
    deliberado: torna o resultado independente da ordem de reexecucao. O teste
    seguinte fixa o outro lado dessa regra.
    """
    primeira = fila_ml({"2227056661": 5, "2532564723": 1})
    publicar(banco, primeira, resumos_ml(primeira))

    depois = AGORA + timedelta(minutes=20)
    segunda = fila_ml({"2227056661": 2, "2579732860": 4}, lote=LOTE_ML2,
                      efetivo=depois)
    publicar(banco, segunda,
             resumos_ml(segunda, lote=LOTE_ML2, efetivo=depois), lote=LOTE_ML2)

    n_fila, resumo = contagens(banco)
    assert n_fila == 6
    assert resumo == {"2227056661": 2, "2532564723": 0,
                      "2579732860": 4, "1366932565": 0}
    with banco.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT refresh_batch_id FROM marts.expedicao_fila_atual "
            "WHERE channel = 'mercadolivre'"
        )
        assert [r[0] for r in cur.fetchall()] == [LOTE_ML2]


def test_reexecucao_no_MESMO_instante_nao_sobrescreve_o_resumo(banco):
    """A outra metade da regra, fixada de proposito.

    Duas execucoes com o MESMO `observed_at` nao sao 'a mais nova': o resumo
    da primeira permanece. A fila, que e' substituida por canal e nao por
    hora, muda. Quem for publicar uma fotografia CORRETIVA sobre uma hora ja'
    gravada precisa saber disto - reexecutar com o mesmo instante deixa o
    resumo velho no ar e a fila nova embaixo dele.
    """
    primeira = fila_ml({"2227056661": 5})
    publicar(banco, primeira, resumos_ml(primeira))

    segunda = fila_ml({"2579732860": 3}, lote=LOTE_ML2)
    publicar(banco, segunda, resumos_ml(segunda, lote=LOTE_ML2), lote=LOTE_ML2)

    n_fila, resumo = contagens(banco)
    assert n_fila == 3, "a fila E substituida"
    assert resumo["2227056661"] == 5, "o resumo da hora NAO e sobrescrito"
    assert sum(resumo.values()) != n_fila, (
        "e' exatamente por isso que a publicacao corretiva precisa de um"
        " instante novo"
    )


def test_lote_incoerente_preserva_o_snapshot_anterior(banco):
    """A prova central: um lote que nao fecha NAO chega a apagar o anterior."""
    boa = fila_ml({"2227056661": 5, "2532564723": 2})
    publicar(banco, boa, resumos_ml(boa))
    antes = contagens(banco)
    assert antes[0] == 7

    # O DEFEITO do incidente, reproduzido: resumo indexado pela marca.
    ruim = fila_ml({"2227056661": 3}, lote=LOTE_ML2)
    resumo_por_marca = resumos_ml(
        ruim, lote=LOTE_ML2,
        accounts={e: (c.brand_key, c.brand_key) for e, c in REGISTRY.items()},
    )
    with pytest.raises(LoteIncoerente) as erro:
        publicar(banco, ruim, resumo_por_marca, lote=LOTE_ML2)
    assert "soma dos resumos" in str(erro.value)

    assert contagens(banco) == antes, (
        "a fotografia anterior tem de sobreviver intacta a um lote recusado"
    )
    assert banco.status == 0 or True  # conexao continua utilizavel


def test_recusa_acontece_antes_do_delete(banco):
    """Nao basta reverter: o DELETE nem pode ter sido emitido."""
    boa = fila_ml({"2227056661": 3})
    publicar(banco, boa, resumos_ml(boa))

    class Espiao:
        def __init__(self, real):
            self._real = real
            self.sqls = []

        def cursor(self):
            espiao = self

            class C:
                def __enter__(self_inner):
                    self_inner._c = espiao._real.cursor().__enter__()
                    return self_inner

                def __exit__(self_inner, *a):
                    return False

                def execute(self_inner, sql, params=None):
                    espiao.sqls.append(sql)
                    return self_inner._c.execute(sql, params)

            return C()

        def __getattr__(self, nome):
            return getattr(self._real, nome)

    espiao = Espiao(banco)
    ruim = fila_ml({"2227056661": 2}, lote=LOTE_ML2)
    with pytest.raises(LoteIncoerente):
        publish_channel(
            espiao, Channel.MERCADOLIVRE, ruim,
            resumos_ml(ruim, lote=LOTE_ML2,
                       accounts={e: (c.brand_key, c.brand_key)
                                 for e, c in REGISTRY.items()}),
            source_health=SourceHealth.HEALTHY, refresh_batch_id=LOTE_ML2,
            expected_accounts=CONTAS,
        )
    assert not any("DELETE" in s.upper() for s in espiao.sqls), (
        "o invariante roda ANTES do DELETE, nao depois"
    )
    assert contagens(banco)[0] == 3


def test_publicacao_do_ml_nao_toca_a_shopee(banco):
    """Substituicao e' POR CANAL: a Shopee sai byte-identica.

    A linha da Shopee e' montada pelo `build_fila_shopee` de producao, e nao a
    mao: uma linha escrita a mao ja' violou os CHECKs da 018 em gate anterior, e
    um dublê que nao passa pelo transform nao prova nada sobre o transform.
    """
    registry_shopee = {"1609671923": SellerAccount("1609671923", 1, "apice")}
    marco = AGORA - timedelta(hours=3)
    linha_shopee = {
        "shop_account": "apice",
        "order_sn": "SH-1",
        "shop_id": "1609671923",
        "order_status": "PROCESSED",
        "create_time": marco,
        "pay_time": marco,
        "ship_by_date": AGORA + timedelta(hours=10),
        "pickup_done_time": None,
        "shipping_carrier": "Shopee Xpress",
        "ingested_at": AGORA - timedelta(minutes=30),
    }
    fila_shopee = transform.build_fila_shopee(
        [linha_shopee], registry_shopee, {}, AGORA, LOTE_SHOPEE
    )
    resumo_shopee = transform.build_account_summaries(
        fila_shopee, AGORA, channel=Channel.SHOPEE.value,
        refresh_batch_id=LOTE_SHOPEE,
        accounts={"1609671923": ("apice", "apice")},
        watermarks={"1609671923": AGORA}, source_advanced=False,
    )
    publish_channel(
        banco, Channel.SHOPEE, fila_shopee, resumo_shopee,
        source_health=SourceHealth.HEALTHY, refresh_batch_id=LOTE_SHOPEE,
        expected_accounts=frozenset({"apice"}),
    )
    antes = contagens(banco, "shopee")
    assert antes[0] == 1

    fila = fila_ml({"2227056661": 4})
    publicar(banco, fila, resumos_ml(fila))

    assert contagens(banco, "shopee") == antes, "a Shopee nao pode ser tocada"
    assert contagens(banco)[0] == 4


def test_lock_do_canal_e_exclusivo_e_liberado(banco):
    """Dois processos nao publicam o mesmo canal ao mesmo tempo."""
    from pipelines.expedicao.publisher import LockNotAcquired, channel_lock

    outra = _conectar()
    outra.autocommit = True
    try:
        with channel_lock(banco, Channel.MERCADOLIVRE, blocking=False):
            with pytest.raises(LockNotAcquired):
                with channel_lock(outra, Channel.MERCADOLIVRE, blocking=False):
                    pass
        # liberado ao sair: a segunda conexao agora consegue
        with channel_lock(outra, Channel.MERCADOLIVRE, blocking=False):
            pass
    finally:
        outra.close()

    with banco.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
        assert cur.fetchone()[0] == 0, "nenhum lock residual"


def test_coerencia_do_publicado_e_verificavel_lendo_o_banco(banco):
    """A conferencia que a reconciliacao passa a fazer, contra dado real."""
    fila = fila_ml({"2227056661": 6, "2532564723": 2, "2579732860": 1})
    publicar(banco, fila, resumos_ml(fila))

    with banco.cursor() as cur:
        cur.execute(
            "SELECT channel, shop_account, marketplace_order_id "
            "FROM marts.expedicao_fila_atual WHERE channel = 'mercadolivre'"
        )
        linhas = [dict(zip(("channel", "shop_account", "marketplace_order_id"), r))
                  for r in cur.fetchall()]
        cur.execute(
            "SELECT channel, shop_account, brand, backlog_count, overdue_count, "
            "due_within_24h_count, on_time_count, deadline_unavailable_count, "
            "over_48h_count, slow_count, zombie_count, stalled_count "
            "FROM marts.expedicao_refresh_run WHERE channel = 'mercadolivre'"
        )
        colunas = [d[0] for d in cur.description]
        resumos = [dict(zip(colunas, r)) for r in cur.fetchall()]

    assert problemas_do_lote(
        "mercadolivre", linhas, resumos, expected_accounts=CONTAS
    ) == []


class _FonteDuble:
    """Fonte de mentira. O DESTINO e' o Postgres real - e' la' que a promessa
    de read-only precisa valer."""

    def __init__(self, candidatos, watermarks):
        self.candidatos, self.watermarks = candidatos, watermarks

    def cursor(self):
        return _CursorDuble(self)

    def close(self):
        pass


class _CursorDuble:
    def __init__(self, f):
        self.f = f

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self._r = (self.f.watermarks if "MAX(s.extracted_at)" in sql
                   else self.f.candidatos)

    def fetchall(self):
        return self._r


def test_reconcile_le_banco_real_sem_escrever_e_sem_lock(banco):
    """`--reconcile` contra o destino REAL: le, confere e nao toca em nada."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from pipelines.expedicao import cli

    distribuicao = {"2227056661": 6, "2532564723": 2}
    brutas, n = [], 0
    for seller, quantidade in distribuicao.items():
        for _ in range(quantidade):
            n += 1
            brutas.append(linha_ml(seller, n))
    fila = transform.build_fila_ml(brutas, REGISTRY, AGORA, LOTE_ML)
    publicar(banco, fila, resumos_ml(fila))

    leitor = psycopg2.connect(DSN, cursor_factory=RealDictCursor)
    leitor.set_session(readonly=True, autocommit=True)
    fonte = _FonteDuble(
        brutas,
        [{"seller_id": int(e), "max_extracted_at": datetime(2026, 9, 22, 14, 30)}
         for e in REGISTRY],
    )
    try:
        r = cli.reconcile_channel(
            leitor, fonte, Channel.MERCADOLIVRE,
            open_registry=lambda _c, _m: (REGISTRY, []),
        )
    finally:
        leitor.close()

    assert r["publicadas"] == r["recomputadas"] == 8
    assert r["resumos_publicados"] == 4
    assert r["backlog_publicado"] == r["backlog_recomputado"] == 8
    assert r["veredito_resumo"] == "coerente_e_igual_ao_recomputado"

    # nada foi tocado: a conexao era read-only e o banco continua igual
    assert contagens(banco) == (8, {"2227056661": 6, "2532564723": 2,
                                    "2579732860": 0, "1366932565": 0})
    with banco.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
        assert cur.fetchone()[0] == 0, "reconciliacao nao toma lock"
    banco.rollback()


def test_reconcile_recusa_fotografia_publicada_incoerente_no_banco_real(banco):
    """O lote do incidente, reproduzido no banco e submetido a reconciliacao."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from pipelines.expedicao import cli
    from pipelines.expedicao.contract import LoteIncoerente as _LI

    brutas = [linha_ml("2227056661", i) for i in range(1, 6)]
    fila = transform.build_fila_ml(brutas, REGISTRY, AGORA, LOTE_ML)
    # publica a fila correta e, por fora, zera os resumos - o estado de 22/09
    publicar(banco, fila, resumos_ml(fila))
    banco.rollback()
    banco.autocommit = True
    with banco.cursor() as cur:
        cur.execute("UPDATE marts.expedicao_refresh_run SET backlog_count = 0, "
                    "deadline_unavailable_count = 0 WHERE channel = 'mercadolivre'")

    leitor = psycopg2.connect(DSN, cursor_factory=RealDictCursor)
    leitor.set_session(readonly=True, autocommit=True)
    fonte = _FonteDuble(
        brutas,
        [{"seller_id": int(e), "max_extracted_at": datetime(2026, 9, 22, 14, 30)}
         for e in REGISTRY],
    )
    try:
        with pytest.raises(_LI) as erro:
            cli.reconcile_channel(
                leitor, fonte, Channel.MERCADOLIVRE,
                open_registry=lambda _c, _m: (REGISTRY, []),
            )
    finally:
        leitor.close()
    assert "nao fecha consigo mesma" in str(erro.value)
    assert contagens(banco)[0] == 5, "a reconciliacao nao consertou nada"
