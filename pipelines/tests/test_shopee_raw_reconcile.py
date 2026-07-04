"""
Testes de pipelines/ingestion/shopee_raw/reconcile.py — Fase Raw Shopee 2.

Usa uma "engine" SQLAlchemy falsa que devolve respostas pré-roteirizadas na
mesma ordem das chamadas de run_reconciliation — nenhum banco real é usado.
"""
from __future__ import annotations

from collections import namedtuple

from pipelines.ingestion.shopee_raw import reconcile

ManifestRow = namedtuple("ManifestRow", ["source_type", "brand", "n", "rows"])
FingerprintRow = namedtuple("FingerprintRow", ["source_type", "n"])


class FakeResult:
    def __init__(self, scalar_value=None, rows=None):
        self._scalar = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows


class FakeConnection:
    """Simula Connection.execution_options(postgresql_readonly=True) —
    exige que ele seja chamado ANTES de qualquer execute(), assim como o
    psycopg2 real exige `set_session(readonly=True)` antes de rodar
    queries para a proteção valer para a transação inteira."""

    def __init__(self, queued_results, engine):
        self._queue = list(queued_results)
        self._engine = engine
        self._readonly = False

    def execution_options(self, **kwargs):
        self._engine.execution_options_calls.append(kwargs)
        if kwargs.get("postgresql_readonly"):
            self._readonly = True
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        if not self._readonly:
            raise AssertionError(
                "execute() foi chamado antes de execution_options(postgresql_readonly=True) — "
                "a proteção read-only precisa valer ANTES da primeira query."
            )
        return self._queue.pop(0)


class FakeEngine:
    def __init__(self, queued_results):
        self._queued_results = queued_results
        self.execution_options_calls: list[dict] = []

    def connect(self):
        return FakeConnection(self._queued_results, self)


def _happy_queue():
    return [
        FakeResult(rows=[ManifestRow("orders", "apice", 2, 10)]),  # manifest_counts
        # orders
        FakeResult(scalar_value=10),  # count(*) orders
        FakeResult(scalar_value=0),   # orphans orders
        FakeResult(scalar_value=0),   # dupes orders
        FakeResult(scalar_value=1024),  # size orders
        # shop_stats
        FakeResult(scalar_value=0),
        FakeResult(scalar_value=0),
        FakeResult(scalar_value=0),
        FakeResult(scalar_value=512),
        # ads
        FakeResult(scalar_value=0),
        FakeResult(scalar_value=0),
        FakeResult(scalar_value=0),
        FakeResult(scalar_value=256),
        FakeResult(scalar_value=128),  # manifest table size
        FakeResult(scalar_value=0),  # manifests_without_children
        FakeResult(scalar_value=0),  # duplicate_manifest_key
        FakeResult(rows=[FingerprintRow("orders", 1)]),  # fingerprints
        FakeResult(scalar_value=1),  # pii_present
        FakeResult(scalar_value=1),  # pii_total_orders
    ]


def test_run_reconciliation_happy_path_sem_problemas():
    engine = FakeEngine(_happy_queue())
    report = reconcile.run_reconciliation(engine)

    assert report.problems == []
    assert report.total_manifest_rows == 10
    assert report.total_child_rows == 10
    assert report.row_counts_by_table["shopee_order_item_export"] == 10
    assert report.table_sizes_bytes["shopee_ingestion_file"] == 128
    assert report.pii_headers_present_files == 1
    assert report.pii_headers_absent_files == 0


def test_run_reconciliation_detecta_linhas_orfas():
    queue = _happy_queue()
    queue[2] = FakeResult(scalar_value=3)  # orphans em orders != 0
    engine = FakeEngine(queue)

    report = reconcile.run_reconciliation(engine)

    assert any("órfã" in p for p in report.problems)
    assert report.orphan_children["shopee_order_item_export"] == 3


def test_run_reconciliation_detecta_mismatch_manifesto_vs_filhas():
    queue = _happy_queue()
    queue[0] = FakeResult(rows=[ManifestRow("orders", "apice", 2, 999)])  # manifesto diz 999, filhas dizem 10
    engine = FakeEngine(queue)

    report = reconcile.run_reconciliation(engine)

    assert any("difere" in p for p in report.problems)


def test_run_reconciliation_detecta_duplicidade_de_manifesto():
    queue = _happy_queue()
    queue[15] = FakeResult(scalar_value=2)  # duplicate_manifest_key
    engine = FakeEngine(queue)

    report = reconcile.run_reconciliation(engine)

    assert report.duplicate_manifest_key == 2
    assert any("duplicidade" in p for p in report.problems)


def test_run_reconciliation_ativa_readonly_antes_de_qualquer_query():
    """A garantia read-only precisa ser pedida ANTES da primeira query —
    não `SET default_transaction_read_only` (que só vale para transações
    futuras, não a corrente)."""
    engine = FakeEngine(_happy_queue())
    reconcile.run_reconciliation(engine)

    assert {"postgresql_readonly": True} in engine.execution_options_calls


def test_run_reconciliation_falha_se_readonly_nao_for_ativado(monkeypatch):
    """Se por engano o código voltasse a chamar execute() sem pedir
    postgresql_readonly antes, TODAS as queries devem falhar alto — nunca
    silenciosamente prosseguir sem a proteção."""

    class NeverReadonlyConnection(FakeConnection):
        def execution_options(self, **kwargs):
            self._engine.execution_options_calls.append(kwargs)
            return self  # nunca liga self._readonly, mesmo pedindo

    class NeverReadonlyEngine(FakeEngine):
        def connect(self):
            return NeverReadonlyConnection(self._queued_results, self)

    engine = NeverReadonlyEngine(_happy_queue())
    try:
        reconcile.run_reconciliation(engine)
        assert False, "deveria ter levantado AssertionError na primeira query"
    except AssertionError as exc:
        assert "antes de execution_options" in str(exc)
