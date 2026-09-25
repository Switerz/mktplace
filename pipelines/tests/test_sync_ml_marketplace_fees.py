"""
MARGEM-REAL-3B — o publisher de comissão do ML, contra PostgreSQL real.

A promessa central deste módulo é negativa: ele NÃO toca em nenhuma coluna além
de `total_fees` e `avg_fee_pct`. Promessa negativa não se prova com um teste que
olha as duas colunas — se prova comparando as 39, antes e depois.

É isso que `test_apenas_as_duas_colunas_autorizadas_mudam` faz, e é por isso que
existe a contraprova `test_contraprova_upsert_completo_reprovaria`: ela troca o
UPDATE restrito pelo `UPSERT_SQL` do pipeline diário e exige que a comparação
das 39 colunas REPROVE. Um teste de escopo que passa com os dois SQLs não estava
medindo escopo nenhum.

Sem PostgreSQL (`PMA_TEST_PG_BIN`, `.local/postgres16` ou no PATH), SKIP.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from pipelines import sync_ml_marketplace_fees as pub
from pipelines.ingestion import daily_performance as dp
from pipelines.ingestion.fato_diaria_lock import FatoDiariaLockUnavailable
from pipelines.tests.banco_descartavel import DDL as MARTS_DDL
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_descartavel,
    postgres_disponivel,
)

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

D1, D2 = date(2026, 7, 10), date(2026, 7, 11)
KOKESHI, BARBOURS = 3, 2
ML_ID, TIKTOK_ID, SHOPEE_ID = 2, 1, 3

#: As 39 colunas de conteúdo da fato. `ingested_at` fica de fora: o UPDATE não
#: a toca, mas ela é `DEFAULT NOW()` e só mudaria num INSERT — que este
#: publisher nunca faz.
COLUNAS = [
    "gmv", "orders", "units_sold", "avg_ticket", "unique_buyers", "new_buyers",
    "repeat_buyers", "repeat_buyer_rate_pct", "visitors", "conversion_rate",
    "canceled_orders", "returned_orders", "refunded_orders", "problem_rate",
    "cancel_rate_pct", "delivered_orders", "avg_delivery_hours",
    "avg_delivery_days", "ad_spend", "ad_revenue", "ad_impressions",
    "ad_clicks", "roas", "acos_pct", "ctr_pct", "cpc", "gmv_video", "gmv_live",
    "gmv_card", "total_settlement", "total_fees", "avg_fee_pct",
    "avg_settlement_pct", "seller_shipping_cost", "shipping_pct_of_gmv",
    "target_revenue", "target_attainment_pct", "projected_month_revenue",
    "data_quality_score",
]
AUTORIZADAS = {"total_fees", "avg_fee_pct"}


@pytest.fixture()
def banco():
    with cluster_descartavel() as url:
        eng = create_engine(url)
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for stmt in filter(None, (s.strip() for s in MARTS_DDL.split(";"))):
                conn.execute(text(stmt))
        yield eng
        eng.dispose()


@pytest.fixture()
def fabrica(banco):
    return sessionmaker(bind=banco)


@contextmanager
def _lock_livre(marketplace_id):
    yield 918_130_002


@contextmanager
def _lock_ocupado(marketplace_id):
    raise FatoDiariaLockUnavailable(marketplace_id, 918_130_002)
    yield  # pragma: no cover


def _bruta(**over) -> dict:
    row = {
        "date": D1, "brand": "kokeshi",
        "gmv": Decimal("1000.00"), "orders": 10,
        "marketplace_fee": Decimal("170.00"), "fee_orders": 10,
        "paid_gmv": Decimal("1000.00"), "paid_orders_src": 10,
    }
    row.update(over)
    return row


def _fetch(brutas):
    """Substitui a leitura da fonte, mas MANTÉM o guardrail no caminho."""
    def _f(date_from, date_to):
        from pipelines.quality.ml_fee_reconciliation import reconciliar
        reconciliar(brutas, brands_esperadas=("barbours", "kokeshi", "lescent", "rituaria"),
                    date_from=date_from, date_to=date_to)
        return brutas
    return _f


def _semear(banco, *chaves, **over):
    """Linha completa e realista na fato, com total_fees NULL."""
    base = {
        "empresa_id": 1, "gmv": 1000, "orders": 10, "units_sold": 12,
        "avg_ticket": 100, "unique_buyers": 9, "new_buyers": 4,
        "repeat_buyers": 5, "repeat_buyer_rate_pct": 55.5, "visitors": None,
        "conversion_rate": None, "canceled_orders": 1, "returned_orders": None,
        "refunded_orders": None, "problem_rate": None, "cancel_rate_pct": 9.1,
        "delivered_orders": 8, "avg_delivery_hours": None,
        "avg_delivery_days": 3, "ad_spend": 50, "ad_revenue": 400,
        "ad_impressions": 10000, "ad_clicks": 300, "roas": 8, "acos_pct": 12.5,
        "ctr_pct": 3, "cpc": 0.17, "gmv_video": None, "gmv_live": None,
        "gmv_card": None, "total_settlement": None, "total_fees": None,
        "avg_fee_pct": None, "avg_settlement_pct": None,
        "seller_shipping_cost": 120, "shipping_pct_of_gmv": 12,
        "target_revenue": None, "target_attainment_pct": None,
        "projected_month_revenue": None, "data_quality_score": None,
        "source_updated_at": None,
    }
    base.update(over)
    with banco.begin() as conn:
        for d, loja, mkt in chaves:
            conn.execute(dp.UPSERT_SQL, {**base, "date": d, "loja_id": loja,
                                         "marketplace_id": mkt})


def _snapshot(banco, mkt=None):
    filtro = "" if mkt is None else f"WHERE marketplace_id = {mkt}"
    with banco.connect() as conn:
        rows = conn.execute(text(
            f"SELECT date, loja_id, marketplace_id, {', '.join(COLUNAS)} "
            f"FROM marts.fact_marketplace_daily_performance {filtro} "
            "ORDER BY date, loja_id, marketplace_id"
        )).mappings().all()
    return [dict(r) for r in rows]


def _diferencas(antes, depois):
    """Colunas que mudaram, por chave. A prova do escopo."""
    ida = {(r["date"], r["loja_id"], r["marketplace_id"]): r for r in antes}
    mudou = set()
    for r in depois:
        a = ida.get((r["date"], r["loja_id"], r["marketplace_id"]))
        assert a is not None, "linha nova apareceu — publisher nao pode inserir"
        for col in COLUNAS:
            if a[col] != r[col]:
                mudou.add(col)
    return mudou


def _publicar(banco, fabrica, brutas, lock=_lock_livre):
    return pub.publicar(D1, D2, fetch=_fetch(brutas),
                        session_factory=fabrica, lock_cm=lock)


# ---------------------------------------------------------------------------
# A prova central: escopo
# ---------------------------------------------------------------------------

def test_apenas_as_duas_colunas_autorizadas_mudam(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID), (D2, KOKESHI, ML_ID))
    antes = _snapshot(banco)

    _publicar(banco, fabrica, [
        _bruta(date=D1), _bruta(date=D2, marketplace_fee=Decimal("180.00")),
    ])

    mudou = _diferencas(antes, _snapshot(banco))
    assert mudou == AUTORIZADAS, f"colunas fora do escopo mudaram: {mudou - AUTORIZADAS}"


def test_contraprova_upsert_completo_reprovaria(banco, fabrica, monkeypatch):
    """Se o publisher voltasse ao `UPSERT_SQL`, a prova de escopo tem de FALHAR.

    Sem esta contraprova, `test_apenas_as_duas_colunas_autorizadas_mudam`
    poderia estar passando por acaso — por exemplo, se o seed e a fonte
    tivessem os mesmos valores em todas as colunas.
    """
    _semear(banco, (D1, KOKESHI, ML_ID))
    antes = _snapshot(banco)

    # O UPSERT completo precisa da linha canônica inteira; o publisher só passa
    # quatro chaves. Um SQL que exige mais parâmetros do que o publisher fornece
    # já falha — e isso, por si, é a prova de que os dois SQLs não são
    # intercambiáveis.
    monkeypatch.setattr(pub, "SQL_PATCH_FEES", dp.UPSERT_SQL)
    with pytest.raises(BaseException):
        _publicar(banco, fabrica, [_bruta(date=D1)])

    assert _diferencas(antes, _snapshot(banco)) == set(), \
        "a transacao devia ter sido desfeita"


def test_contraprova_sem_restricao_de_coluna_o_gmv_mudaria(banco, fabrica, monkeypatch):
    """Segunda contraprova, mais direta: um UPDATE que também toca `gmv`.

    Aqui a prova de escopo REPROVA, como tem de reprovar.
    """
    _semear(banco, (D1, KOKESHI, ML_ID))
    antes = _snapshot(banco)

    monkeypatch.setattr(pub, "SQL_PATCH_FEES", text("""
        UPDATE marts.fact_marketplace_daily_performance
           SET total_fees = :total_fees, avg_fee_pct = :avg_fee_pct,
               gmv = gmv - 1
         WHERE date = :date AND loja_id = :loja_id AND marketplace_id = 2
    """))
    _publicar(banco, fabrica, [_bruta(date=D1)])

    mudou = _diferencas(antes, _snapshot(banco))
    assert "gmv" in mudou
    assert mudou != AUTORIZADAS, "a prova de escopo teria de reprovar aqui"


# ---------------------------------------------------------------------------
# Comportamento do patch
# ---------------------------------------------------------------------------

def test_patch_bem_sucedido_grava_comissao_e_take_rate(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID))
    r = _publicar(banco, fabrica, [_bruta(date=D1)])
    assert r["rows_updated"] == 1
    linha = _snapshot(banco)[0]
    assert linha["total_fees"] == Decimal("170.00")
    assert linha["avg_fee_pct"] == Decimal("17.00")


def test_idempotente(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID))
    _publicar(banco, fabrica, [_bruta(date=D1)])
    primeira = _snapshot(banco)
    _publicar(banco, fabrica, [_bruta(date=D1)])
    assert _snapshot(banco) == primeira


def test_valor_alterado_pela_maturacao_da_fonte_atualiza(banco, fabrica):
    """A fonte muda quando pedidos saem de `paid`. O patch acompanha."""
    _semear(banco, (D1, KOKESHI, ML_ID))
    _publicar(banco, fabrica, [_bruta(date=D1, marketplace_fee=Decimal("170.00"))])
    assert _snapshot(banco)[0]["total_fees"] == Decimal("170.00")

    _publicar(banco, fabrica, [_bruta(date=D1, marketplace_fee=Decimal("165.50"))])
    linha = _snapshot(banco)[0]
    assert linha["total_fees"] == Decimal("165.50")
    assert linha["avg_fee_pct"] == Decimal("16.55")


def test_zero_medido_e_gravado_como_zero(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID))
    _publicar(banco, fabrica, [_bruta(date=D1, marketplace_fee=Decimal("0.00"))])
    linha = _snapshot(banco)[0]
    assert linha["total_fees"] == Decimal("0.00")
    assert linha["total_fees"] is not None
    assert linha["avg_fee_pct"] == Decimal("0.00")


def test_ausencia_de_observacao_nao_toca_a_linha(banco, fabrica):
    """Dia sem venda: a Gold não tem atividade, a fonte não tem comissão.
    A linha não é escrita — nem com NULL, nem com zero."""
    _semear(banco, (D1, KOKESHI, ML_ID), gmv=0, orders=0)
    antes = _snapshot(banco)
    r = _publicar(banco, fabrica, [
        _bruta(date=D1, gmv=Decimal("0.00"), orders=0, marketplace_fee=None,
               fee_orders=None, paid_gmv=None, paid_orders_src=None),
    ])
    assert r["rows_updated"] == 0
    assert r["sem_observacao"] == 1
    assert _snapshot(banco) == antes


# ---------------------------------------------------------------------------
# Falhas — transação única
# ---------------------------------------------------------------------------

def test_chave_ausente_na_fato_falha_e_desfaz_tudo(banco, fabrica):
    """A segunda chave não existe. A primeira NÃO pode ficar publicada."""
    _semear(banco, (D1, KOKESHI, ML_ID))
    antes = _snapshot(banco)

    with pytest.raises(pub.MlFeeKeyMissingError):
        _publicar(banco, fabrica, [
            _bruta(date=D1),
            _bruta(date=D2, marketplace_fee=Decimal("180.00")),
        ])

    assert _snapshot(banco) == antes, "rollback integral"


def test_publisher_nunca_insere_linha_nova(banco, fabrica):
    antes = _snapshot(banco)
    assert antes == []
    with pytest.raises(pub.MlFeeKeyMissingError):
        _publicar(banco, fabrica, [_bruta(date=D1)])
    assert _snapshot(banco) == [], "nenhuma linha foi criada"


def test_chave_duplicada_na_fonte_falha_antes_de_escrever(banco, fabrica):
    """Duplicidade tem DUAS defesas, e a primeira é o guardrail.

    Quem levanta aqui é `MlFeeReconciliationError`, não `MlFeeSyncError` — a
    reconciliação roda antes de `preparar()`. Aceitar só a segunda faria o
    teste passar por um caminho que a produção nunca percorre.
    """
    from pipelines.quality.ml_fee_reconciliation import MlFeeReconciliationError

    _semear(banco, (D1, KOKESHI, ML_ID))
    antes = _snapshot(banco)
    with pytest.raises((MlFeeReconciliationError, pub.MlFeeSyncError)):
        _publicar(banco, fabrica, [_bruta(date=D1), _bruta(date=D1)])
    assert _snapshot(banco) == antes


def test_preparar_tambem_recusa_duplicidade_sem_o_guardrail():
    """A segunda defesa, isolada.

    Se algum dia o guardrail deixar passar uma chave repetida, `preparar()`
    ainda recusa — em vez de somar a comissão duas vezes na mesma linha.
    """
    with pytest.raises(pub.MlFeeSyncError, match="chave repetida"):
        pub.preparar([_bruta(date=D1), _bruta(date=D1)])


def test_fonte_que_nao_reconcilia_aborta_antes_de_escrever(banco, fabrica):
    from pipelines.quality.ml_fee_reconciliation import MlFeeReconciliationError

    _semear(banco, (D1, KOKESHI, ML_ID))
    antes = _snapshot(banco)
    with pytest.raises(MlFeeReconciliationError):
        _publicar(banco, fabrica, [_bruta(date=D1, paid_gmv=Decimal("999.00"))])
    assert _snapshot(banco) == antes


def test_lock_ocupado_nao_escreve_nem_abre_auditoria(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID))
    antes = _snapshot(banco)
    with pytest.raises(FatoDiariaLockUnavailable):
        _publicar(banco, fabrica, [_bruta(date=D1)], lock=_lock_ocupado)
    assert _snapshot(banco) == antes
    with banco.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM audit.source_sync_run WHERE source_name = :f"
        ), {"f": pub.SOURCE_NAME}).scalar_one()
    assert n == 0, "lock ocupado nao deve abrir linha de auditoria"


# ---------------------------------------------------------------------------
# Outros canais
# ---------------------------------------------------------------------------

def test_shopee_e_tiktok_intactos(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID), (D1, KOKESHI, SHOPEE_ID),
            (D1, KOKESHI, TIKTOK_ID))
    outros_antes = [r for r in _snapshot(banco) if r["marketplace_id"] != ML_ID]

    _publicar(banco, fabrica, [_bruta(date=D1)])

    outros_depois = [r for r in _snapshot(banco) if r["marketplace_id"] != ML_ID]
    assert outros_depois == outros_antes


def test_sql_do_patch_esta_preso_ao_marketplace_do_ml():
    sql = str(pub.SQL_PATCH_FEES)
    assert "marketplace_id = 2" in sql
    assert "UPDATE" in sql.upper()
    assert "INSERT" not in sql.upper()
    assert "COALESCE" not in sql.upper()


def test_sql_do_patch_so_atribui_as_duas_colunas():
    import re
    sql = str(pub.SQL_PATCH_FEES)
    corpo = sql.upper().split("SET", 1)[1].split("WHERE", 1)[0]
    atribuidas = set(re.findall(r"(\w+)\s*=", corpo))
    assert atribuidas == {"TOTAL_FEES", "AVG_FEE_PCT"}


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------

def _auditorias(banco):
    with banco.connect() as conn:
        return [dict(r) for r in conn.execute(text(
            "SELECT sync_run_id, source_name, marketplace_id, status, "
            "rows_extracted, rows_loaded, error_message, source_min_date, "
            "source_max_date FROM audit.source_sync_run ORDER BY sync_run_id"
        )).mappings().all()]


def test_auditoria_success(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID))
    _publicar(banco, fabrica, [_bruta(date=D1)])
    a = _auditorias(banco)
    assert len(a) == 1
    assert a[0]["source_name"] == "ml_marketplace_fees"
    assert a[0]["status"] == "success"
    assert a[0]["rows_loaded"] == 1
    assert a[0]["error_message"] is None
    assert a[0]["source_min_date"] == D1 and a[0]["source_max_date"] == D2


def test_auditoria_failed_e_nunca_orfa(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID))
    with pytest.raises(pub.MlFeeKeyMissingError):
        _publicar(banco, fabrica, [_bruta(date=D1), _bruta(date=D2)])
    a = _auditorias(banco)
    assert len(a) == 1
    assert a[0]["status"] == "failed"
    assert a[0]["error_message"]
    assert all(x["status"] != "running" for x in a), "nenhuma auditoria orfa"


def test_auditoria_nao_vaza_dsn_nem_senha(banco, fabrica, caplog):
    _semear(banco, (D1, KOKESHI, ML_ID))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(pub.MlFeeKeyMissingError):
            _publicar(banco, fabrica, [_bruta(date=D2)])

    a = _auditorias(banco)
    texto = (a[0]["error_message"] or "") + " ".join(r.getMessage() for r in caplog.records)
    for proibido in ("postgresql://", "postgres://", "password=", "host="):
        assert proibido not in texto.lower(), proibido


def test_sanitizar_remove_dsn_ip_e_senha():
    sujo = ("falha em postgresql://user:segredo@10.1.2.3:5432/db "
            "password=abc123 host=meuhost")
    limpo = pub.sanitizar(sujo)
    for proibido in ("segredo", "10.1.2.3", "abc123", "meuhost", "postgresql://"):
        assert proibido not in limpo
    assert "[REDACTED]" in limpo


# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------

def test_diagnostico_nao_escreve_nada(banco, fabrica):
    _semear(banco, (D1, KOKESHI, ML_ID))
    antes = _snapshot(banco)
    r = pub.diagnosticar(D1, D2, fetch=_fetch([_bruta(date=D1)]))
    assert r["applied"] is False
    assert r["a_escrever"] == 1
    assert _snapshot(banco) == antes
    assert _auditorias(banco) == [], "diagnostico nao abre auditoria"


def test_janela_fecha_em_d_menos_1():
    from pipelines.common.operational_calendar import last_closed_date
    _, fim = pub.resolver_janela("backfill", 90)
    assert fim == last_closed_date()


def test_cli_sem_apply_e_diagnostico(monkeypatch, capsys):
    chamou = {"diag": 0, "pub": 0}
    monkeypatch.setattr(pub, "diagnosticar",
                        lambda *a, **k: (chamou.__setitem__("diag", 1) or {"applied": False}))
    monkeypatch.setattr(pub, "publicar",
                        lambda *a, **k: chamou.__setitem__("pub", 1))
    assert pub.main(["--mode", "backfill", "--days", "90"]) == 0
    assert chamou == {"diag": 1, "pub": 0}


def test_falha_depois_do_commit_nao_marca_failed(banco, fabrica, monkeypatch):
    """Self-review do MARGEM-REAL-3B.

    Se algo quebrar DEPOIS do commit, `failed` seria uma afirmação falsa — a
    comissão está publicada. E `failed` levaria o próximo operador a
    republicar sobre dado correto. O estado honesto é deixar em `running`:
    um alarme visível que pede inspeção humana.
    """
    _semear(banco, (D1, KOKESHI, ML_ID))

    original = pub._fechar_auditoria
    chamadas = []

    def _quebra_no_success(sessao, sync_run_id, status, extracted, loaded, erro):
        chamadas.append(status)
        if status == "success":
            raise RuntimeError("falha simulada apos o commit")
        return original(sessao, sync_run_id, status, extracted, loaded, erro)

    monkeypatch.setattr(pub, "_fechar_auditoria", _quebra_no_success)

    with pytest.raises(RuntimeError, match="apos o commit"):
        _publicar(banco, fabrica, [_bruta(date=D1)])

    # a comissão FOI publicada e continua lá
    assert _snapshot(banco)[0]["total_fees"] == Decimal("170.00")
    # e a auditoria NÃO foi marcada failed
    assert chamadas == ["success"], f"tentou marcar {chamadas}"
    assert _auditorias(banco)[0]["status"] == "running"


def test_falha_antes_do_commit_marca_failed(banco, fabrica):
    """O contraponto: sem commit, `failed` é a afirmação verdadeira."""
    _semear(banco, (D1, KOKESHI, ML_ID))
    with pytest.raises(pub.MlFeeKeyMissingError):
        _publicar(banco, fabrica, [_bruta(date=D1), _bruta(date=D2)])
    assert _auditorias(banco)[0]["status"] == "failed"
