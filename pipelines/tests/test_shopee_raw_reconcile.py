"""
Testes de pipelines/ingestion/shopee_raw/reconcile.py — Fase Raw Shopee 2.

Usa uma "engine" SQLAlchemy falsa que devolve respostas pré-roteirizadas na
mesma ordem das chamadas de run_reconciliation — nenhum banco real é usado.
"""
from __future__ import annotations

from collections import namedtuple
from pathlib import Path

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
        self._engine.executed.append((stmt, params))
        return self._queue.pop(0)


class FakeEngine:
    def __init__(self, queued_results):
        self._queued_results = queued_results
        self.execution_options_calls: list[dict] = []
        self.executed: list[tuple] = []

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


# ---------------------------------------------------------------------------
# reconcile_batch_file_ids — Gate S5.5.1 (reconciliação escopada ao lote atual,
# nunca comparada contra contagem histórica agregada)
# ---------------------------------------------------------------------------

FileRow = namedtuple("FileRow", ["file_id", "source_type", "source_row_count"])
ChildCountRow = namedtuple("ChildCountRow", ["file_id", "n"])


def test_reconcile_batch_file_ids_happy_path():
    queue = [
        FakeResult(rows=[
            FileRow(1, "orders", 5),
            FileRow(2, "shop_stats", 3),
            FileRow(3, "ads", 2),
        ]),
        FakeResult(rows=[ChildCountRow(1, 5)]),
        FakeResult(rows=[ChildCountRow(2, 3)]),
        FakeResult(rows=[ChildCountRow(3, 2)]),
    ]
    engine = FakeEngine(queue)

    report = reconcile.reconcile_batch_file_ids(engine, [1, 2, 3])

    assert report.reconciled is True
    assert report.problems == []
    assert report.requested_file_count == 3
    assert report.found_file_ids == [1, 2, 3]
    assert report.missing_file_ids == []
    assert report.source_type_by_file_id == {1: "orders", 2: "shop_stats", 3: "ads"}
    assert report.row_count_mismatches == {}


def test_reconcile_batch_file_ids_detecta_ausente_do_manifesto():
    queue = [
        FakeResult(rows=[FileRow(1, "orders", 5)]),
        FakeResult(rows=[ChildCountRow(1, 5)]),
    ]
    engine = FakeEngine(queue)

    report = reconcile.reconcile_batch_file_ids(engine, [1, 2])

    assert report.reconciled is False
    assert report.missing_file_ids == [2]
    assert any("ausente" in p for p in report.problems)


def test_reconcile_batch_file_ids_lote_parcialmente_visivel_bloqueia():
    """Mais de um file_id pedido, só parte aparece no manifesto -- mesmo
    caminho de `missing_file_ids`, testado explicitamente com 3 pedidos e
    só 1 encontrado."""
    queue = [
        FakeResult(rows=[FileRow(2, "orders", 4)]),
        FakeResult(rows=[ChildCountRow(2, 4)]),
    ]
    engine = FakeEngine(queue)

    report = reconcile.reconcile_batch_file_ids(engine, [1, 2, 3])

    assert report.reconciled is False
    assert report.missing_file_ids == [1, 3]
    assert report.found_file_ids == [2]


def test_reconcile_batch_file_ids_detecta_divergencia_linhas_filhas():
    queue = [
        FakeResult(rows=[FileRow(1, "orders", 10)]),
        FakeResult(rows=[ChildCountRow(1, 7)]),
    ]
    engine = FakeEngine(queue)

    report = reconcile.reconcile_batch_file_ids(engine, [1])

    assert report.reconciled is False
    assert report.row_count_mismatches == {1: {"expected": 10, "actual": 7}}
    assert any("diverge" in p for p in report.problems)


def test_reconcile_batch_file_ids_detecta_duplicado_na_lista_solicitada():
    queue = [
        FakeResult(rows=[FileRow(1, "orders", 5)]),
        FakeResult(rows=[ChildCountRow(1, 5)]),
    ]
    engine = FakeEngine(queue)

    report = reconcile.reconcile_batch_file_ids(engine, [1, 1])

    assert report.reconciled is False
    assert any("duplicado" in p for p in report.problems)


def test_reconcile_batch_file_ids_lista_vazia_e_trivialmente_reconciliada():
    engine = FakeEngine([])

    report = reconcile.reconcile_batch_file_ids(engine, [])

    assert report.reconciled is True
    assert report.requested_file_count == 0
    assert engine.execution_options_calls == []


def test_reconcile_batch_file_ids_usa_bind_parameters_nunca_interpola_ids():
    queue = [
        FakeResult(rows=[FileRow(999999, "orders", 5)]),
        FakeResult(rows=[ChildCountRow(999999, 5)]),
    ]
    engine = FakeEngine(queue)

    reconcile.reconcile_batch_file_ids(engine, [999999])

    assert engine.executed  # confirma que consultas de fato rodaram
    for stmt, _params in engine.executed:
        assert "999999" not in str(stmt)
    assert any(
        params and 999999 in params.get("file_ids", []) for _stmt, params in engine.executed
    )


def test_reconcile_batch_file_ids_ativa_readonly_antes_de_qualquer_query():
    queue = [
        FakeResult(rows=[FileRow(1, "orders", 5)]),
        FakeResult(rows=[ChildCountRow(1, 5)]),
    ]
    engine = FakeEngine(queue)

    reconcile.reconcile_batch_file_ids(engine, [1])

    assert {"postgresql_readonly": True} in engine.execution_options_calls


def test_reconcile_batch_file_ids_falha_se_readonly_nao_for_ativado():
    class NeverReadonlyConnection(FakeConnection):
        def execution_options(self, **kwargs):
            self._engine.execution_options_calls.append(kwargs)
            return self  # nunca liga self._readonly, mesmo pedindo

    class NeverReadonlyEngine(FakeEngine):
        def connect(self):
            return NeverReadonlyConnection(self._queued_results, self)

    engine = NeverReadonlyEngine([FakeResult(rows=[FileRow(1, "orders", 5)])])
    try:
        reconcile.reconcile_batch_file_ids(engine, [1])
        assert False, "deveria ter levantado AssertionError na primeira query"
    except AssertionError as exc:
        assert "antes de execution_options" in str(exc)


def test_reconcile_batch_file_ids_nunca_seleciona_raw_payload():
    """Verifica só as linhas de SQL de fato (contendo `SELECT`), nunca o
    docstring da função -- que legitimamente cita `raw_payload` em prosa
    explicando que NUNCA é selecionado (mesma armadilha de falso positivo
    já documentada em outros gates)."""
    import inspect
    source = inspect.getsource(reconcile.reconcile_batch_file_ids)
    sql_lines = [line for line in source.splitlines() if "SELECT" in line.upper()]
    assert sql_lines  # confirma que há de fato SQL nesta função
    for line in sql_lines:
        assert "raw_payload" not in line
