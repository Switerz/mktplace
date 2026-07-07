"""
Testes do backfill HISTÓRICO e ATÔMICO de source_metadata (Fase Staging
Shopee 2A, Gate 2B — CLI operacional desde 2026-07-07). Nenhum banco real é
tocado: Fase A e a CLI de dry-run usam uma conexão SQLAlchemy FALSA, Fase B
(e a restauração) usam uma conexão psycopg2 FALSA. `--apply-confirmed`
nunca é chamado contra um banco real neste módulo de teste.
"""
from __future__ import annotations

import copy
import json

import pytest

from pipelines.ingestion.shopee_raw import backfill_ads_metadata as backfill
from pipelines.ingestion.shopee_raw import write_conn
from pipelines.ingestion.shopee_raw.hashing import sha256_file
from pipelines.ingestion.shopee_raw.inventory import SourceReadError

_VALID_CSV = """Relatório de Todos os Anúncios CPC - Shopee Brasil
ID da Loja,999999999
Data de Criação do Relatório,15/07/2026 10:30
Período,01/01/2026 - 31/03/2026

#,Nome do Anúncio,Status
1,Anúncio Exemplo,Em Andamento
"""

_INVALID_CSV = """Relatório de Todos os Anúncios CPC - Shopee Brasil
ID da Loja,999999999

#,Nome do Anúncio,Status
1,Anúncio Exemplo,Em Andamento
"""

_EXPECTED_METADATA = {
    "period_start": "2026-01-01",
    "period_end": "2026-03-31",
    "report_created_at": "2026-07-15T10:30:00",
    "shop_id": "999999999",
}

_BRANDS = ["apice", "barbours", "kokeshi", "lescent", "rituaria"]


def test_expected_brands_vem_da_fonte_canonica():
    from pipelines.connectors.shopee.connector import BRANDS_IN_SCOPE
    assert backfill.EXPECTED_BRANDS == frozenset(BRANDS_IN_SCOPE)
    assert backfill.EXPECTED_BRANDS == frozenset(_BRANDS)
    assert backfill.EXPECTED_TOTAL_PENDING_ADS_MANIFESTS == 10


# =============================================================================
# Validação COMPARTILHADA (fonte única para plan_backfill/apply_backfill_
# atomic/_validate_backup_records) — testada diretamente aqui, sem passar
# por nenhum dos 3 chamadores, para provar que ela sozinha nunca levanta
# exceção e cobre identidade/escopo/metadata.
# =============================================================================

def _valid_record(file_id=1, brand="apice"):
    return {
        "file_id": file_id,
        "source_filename": f"{brand}/Dados+0.csv",
        "file_sha256": f"{file_id:02x}" + "a" * 62,
        "brand": brand,
        "source_type": "ads",
    }


def test_validate_record_identity_aceita_registro_valido():
    assert backfill.validate_record_identity(_valid_record()) == []


@pytest.mark.parametrize("bad_file_id", ["1", [1], {"a": 1}, True, False, 0, -5, None, 1.5])
def test_validate_record_identity_rejeita_file_id_invalido_sem_levantar(bad_file_id):
    r = _valid_record()
    r["file_id"] = bad_file_id
    problems = backfill.validate_record_identity(r)
    assert problems and "file_id" in problems[0]


@pytest.mark.parametrize("bad_hash", [[1, 2, 3], {"a": 1}, None, "curto_demais", 12345, "z" * 64])
def test_validate_record_identity_rejeita_hash_invalido_sem_levantar(bad_hash):
    r = _valid_record()
    r["file_sha256"] = bad_hash
    problems = backfill.validate_record_identity(r)
    assert problems and any("file_sha256" in p for p in problems)


@pytest.mark.parametrize("bad_brand", [[1], {"a": 1}, None, "", 123])
def test_validate_record_identity_rejeita_brand_invalido_sem_levantar(bad_brand):
    r = _valid_record()
    r["brand"] = bad_brand
    problems = backfill.validate_record_identity(r)
    assert problems and any("brand" in p for p in problems)


@pytest.mark.parametrize("bad_filename", ["", None, 123, [], {}])
def test_validate_record_identity_rejeita_source_filename_invalido_sem_levantar(bad_filename):
    r = _valid_record()
    r["source_filename"] = bad_filename
    problems = backfill.validate_record_identity(r)
    assert problems and any("source_filename" in p for p in problems)


@pytest.mark.parametrize("bad_source_type", ["", None, 123, []])
def test_validate_record_identity_rejeita_source_type_invalido_sem_levantar(bad_source_type):
    r = _valid_record()
    r["source_type"] = bad_source_type
    problems = backfill.validate_record_identity(r)
    assert problems and any("source_type" in p for p in problems)


@pytest.mark.parametrize("bad_record", [["nao", "e", "dict"], "string", 123, None, 1.5])
def test_validate_record_identity_rejeita_registro_nao_dict_sem_levantar(bad_record):
    assert backfill.validate_record_identity(bad_record) == ["registro não é um objeto"]


@pytest.mark.parametrize("bad_metadata", [["a"], "nao e um dict", 123, None, True])
def test_validate_applied_metadata_rejeita_tipo_invalido_sem_levantar(bad_metadata):
    assert backfill.validate_applied_metadata(bad_metadata) == ["metadata inválida (esperado objeto)"]


def test_validate_applied_metadata_aceita_valida():
    assert backfill.validate_applied_metadata(_EXPECTED_METADATA) == []


def test_validate_applied_metadata_rejeita_chave_extra():
    m = dict(_EXPECTED_METADATA, chave_extra="x")
    problems = backfill.validate_applied_metadata(m)
    assert any("chave(s) extra(s)" in p for p in problems)


def test_validate_applied_metadata_rejeita_chave_faltante():
    m = dict(_EXPECTED_METADATA)
    del m["shop_id"]
    problems = backfill.validate_applied_metadata(m)
    assert any("chave(s) ausente(s)" in p for p in problems)


def test_validate_manifest_scope_rejeita_lista_com_registro_nao_type_safe_sem_levantar():
    """Regressão-chave: um registro com brand=lista NÃO pode chegar até o
    `set()`/`dict` de agregação -- validate_manifest_scope precisa
    detectar isso no passo de identidade, antes de qualquer agregação."""
    records = [_valid_record(1, "apice"), {**_valid_record(2, "barbours"), "brand": ["nao", "hashable", "como", "chave"]}]
    problems = backfill.validate_manifest_scope(records)
    assert problems  # nunca levanta TypeError por usar lista como chave de dict
    assert any("registro #1" in p for p in problems)


def test_validate_manifest_scope_nao_e_lista_no_topo():
    assert backfill.validate_manifest_scope("nao e uma lista") == ["registros não formam uma lista"]
    assert backfill.validate_manifest_scope({"a": 1}) == ["registros não formam uma lista"]


# --- Fase A: plan_backfill (read-only) ---------------------------------------


class _FakeResultA:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeConnA:
    def __init__(self, manifest_rows):
        self.manifest_rows = manifest_rows

    def execute(self, _stmt, _params=None):
        return _FakeResultA(self.manifest_rows)


def _write_valid_ads_csv(path):
    """Grava um CSV de ads válido cujo CONTEÚDO é único por caminho (nunca
    dois arquivos byte-a-byte idênticos) -- necessário porque a validação
    de escopo exige 10 file_sha256 distintos, e vários testes criam
    múltiplos arquivos com o mesmo texto-base."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _VALID_CSV.replace("Anúncio Exemplo", f"Anúncio Exemplo {path.as_posix()}")
    path.write_text(content, encoding="utf-8-sig", newline="")


def _exact_scope_manifests(tmp_path, per_brand=2, brands=None):
    """Monta exatamente `len(brands) * per_brand` manifestos válidos,
    2 arquivos por marca por padrão (o escopo histórico esperado)."""
    brands = brands if brands is not None else _BRANDS
    rows = []
    file_id = 1
    for brand in brands:
        for i in range(per_brand):
            rel = f"{brand}/Dados+{i}.csv"
            f = tmp_path / rel
            _write_valid_ads_csv(f)
            rows.append({
                "file_id": file_id, "source_filename": rel,
                "file_sha256": sha256_file(f), "brand": brand, "source_type": "ads",
            })
            file_id += 1
    return rows


def test_plan_backfill_escopo_exato_10_5_2_e_aceito(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    assert len(manifests) == 10
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is True
    assert plan.problems == []
    assert len(plan.items) == 10
    assert {i.brand for i in plan.items} == set(_BRANDS)
    assert all(i.metadata == _EXPECTED_METADATA for i in plan.items)
    assert all(i.source_type == "ads" for i in plan.items)


def test_plan_backfill_9_manifestos_aborta_por_escopo(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)[:9]
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert plan.items == []
    assert any("exatamente 10" in p for p in plan.problems)


def test_plan_backfill_11_manifestos_aborta_por_escopo(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    extra = dict(manifests[0])
    extra["file_id"] = 999
    extra["source_filename"] = "apice/Dados+extra.csv"
    _write_valid_ads_csv(tmp_path / extra["source_filename"])
    extra["file_sha256"] = sha256_file(tmp_path / extra["source_filename"])
    manifests = manifests + [extra]
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("exatamente 10" in p for p in plan.problems)


def test_plan_backfill_distribuicao_errada_aborta_por_escopo(tmp_path):
    """10 manifestos, mas 3 para uma marca e 1 para outra (em vez de 2/2)."""
    manifests = _exact_scope_manifests(tmp_path)
    manifests = [m for m in manifests if not (m["brand"] == "kokeshi" and m["file_id"] == manifests[4]["file_id"])]
    extra = dict(manifests[0])
    extra["file_id"] = 998
    extra["source_filename"] = "apice/Dados+3.csv"
    _write_valid_ads_csv(tmp_path / extra["source_filename"])
    extra["file_sha256"] = sha256_file(tmp_path / extra["source_filename"])
    manifests.append(extra)
    assert len(manifests) == 10
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("quantidade de arquivos" in p for p in plan.problems)


def test_plan_backfill_marca_nao_oficial_aborta_por_escopo(tmp_path):
    """10 manifestos, 5 marcas, 2 por marca -- mas uma delas ('sexta') não
    é uma marca oficial (substitui 'rituaria')."""
    brands = ["apice", "barbours", "kokeshi", "lescent", "sexta"]
    rows = []
    file_id = 1
    for brand in brands:
        for i in range(2):
            rel = f"{brand}/Dados+{i}.csv"
            f = tmp_path / rel
            _write_valid_ads_csv(f)
            rows.append({
                "file_id": file_id, "source_filename": rel,
                "file_sha256": sha256_file(f), "brand": brand, "source_type": "ads",
            })
            file_id += 1
    conn = _FakeConnA(rows)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("conjunto de marcas não bate com o oficial" in p for p in plan.problems)


def test_plan_backfill_6_marcas_aborta_por_escopo(tmp_path):
    """10 manifestos mas espalhados por 6 marcas (não 5) -- ex.: 2,2,2,2,1,1."""
    rows = []
    file_id = 1
    dist = {"apice": 2, "barbours": 2, "kokeshi": 2, "lescent": 2, "rituaria": 1, "sexta": 1}
    for brand, n in dist.items():
        for i in range(n):
            rel = f"{brand}/Dados+{i}.csv"
            f = tmp_path / rel
            _write_valid_ads_csv(f)
            rows.append({
                "file_id": file_id, "source_filename": rel,
                "file_sha256": sha256_file(f), "brand": brand, "source_type": "ads",
            })
            file_id += 1
    assert len(rows) == 10
    conn = _FakeConnA(rows)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("conjunto de marcas não bate com o oficial" in p for p in plan.problems)


def test_plan_backfill_file_id_duplicado_aborta_por_escopo(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    manifests[1]["file_id"] = manifests[0]["file_id"]
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("file_id duplicado" in p for p in plan.problems)


def test_plan_backfill_hash_duplicado_aborta_por_escopo(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    manifests[1]["file_sha256"] = manifests[0]["file_sha256"]
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("file_sha256 duplicado" in p for p in plan.problems)


def test_plan_backfill_source_type_diferente_aborta_por_escopo(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    manifests[0]["source_type"] = "orders"
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("source_type != 'ads'" in p for p in plan.problems)


def test_plan_backfill_nao_le_nenhum_arquivo_quando_escopo_ja_esta_errado(tmp_path, monkeypatch):
    manifests = _exact_scope_manifests(tmp_path)[:9]
    conn = _FakeConnA(manifests)

    def _boom(*a, **kw):
        raise AssertionError("não deveria tentar ler arquivo com escopo já inválido")

    monkeypatch.setattr(backfill, "sha256_file", _boom)
    plan = backfill.plan_backfill(conn, tmp_path)
    assert plan.ready is False


def test_plan_backfill_um_arquivo_invalido_com_escopo_correto_derruba_o_plano(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    bad_path = tmp_path / manifests[0]["source_filename"]
    bad_path.write_text(_INVALID_CSV, encoding="utf-8-sig", newline="")
    manifests[0]["file_sha256"] = sha256_file(bad_path)
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert plan.items == []
    assert any(f"file_id={manifests[0]['file_id']}" in p for p in plan.problems)


def test_plan_backfill_arquivo_local_ausente_derruba_plano(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    missing_path = tmp_path / manifests[3]["source_filename"]
    missing_path.unlink()
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("arquivo local não encontrado" in p for p in plan.problems)


def test_plan_backfill_nunca_usa_filename_sozinho_como_chave(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    manifests[1]["source_filename"] = manifests[0]["source_filename"]
    manifests[1]["file_sha256"] = "f" * 64
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any(f"file_id={manifests[1]['file_id']}" in p and "hash" in p for p in plan.problems)


def test_plan_backfill_falha_de_preambulo_invalido_e_sanitizada(tmp_path, monkeypatch):
    manifests = _exact_scope_manifests(tmp_path)

    def _raise_ads_preamble_error(*a, **kw):
        raise backfill.AdsPreambleError("campo 'Período' ausente no preâmbulo")

    monkeypatch.setattr(backfill, "parse_ads_preamble", _raise_ads_preamble_error)
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("AdsPreambleError" in p for p in plan.problems)
    assert not any("ausente no preâmbulo" in p for p in plan.problems)


def test_plan_backfill_falha_de_leitura_source_read_error_e_capturada(tmp_path, monkeypatch):
    manifests = _exact_scope_manifests(tmp_path)

    def _raise_source_read_error(*a, **kw):
        raise SourceReadError("não foi possível decodificar o CSV")

    monkeypatch.setattr(backfill, "_decode_ads_csv", _raise_source_read_error)
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("SourceReadError" in p for p in plan.problems)


def test_plan_backfill_falha_de_leitura_oserror_e_capturada(tmp_path, monkeypatch):
    manifests = _exact_scope_manifests(tmp_path)

    def _raise_oserror(*a, **kw):
        raise PermissionError("acesso negado")

    monkeypatch.setattr(backfill, "_decode_ads_csv", _raise_oserror)
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("PermissionError" in p for p in plan.problems)


def test_plan_backfill_rejeita_metadata_invalida_via_validacao_compartilhada(tmp_path, monkeypatch):
    """Mesmo que ads_metadata.parse_ads_preamble já garanta formato válido
    por construção, plan_backfill NUNCA confia cegamente -- roda
    validate_applied_metadata sobre o resultado antes de aceitar o item."""
    manifests = _exact_scope_manifests(tmp_path)

    class _FakePreamble:
        def to_jsonb_dict(self):
            return {"period_start": "2026-01-01", "period_end": "2026-03-31",
                     "report_created_at": "2026-07-15T10:30:00"}  # falta shop_id

    monkeypatch.setattr(backfill, "parse_ads_preamble", lambda lines: _FakePreamble())
    conn = _FakeConnA(manifests)

    plan = backfill.plan_backfill(conn, tmp_path)

    assert plan.ready is False
    assert any("chave(s) ausente(s)" in p for p in plan.problems)


# --- Fase B: apply_backfill_atomic --------------------------------------------


class _PoisonConn:
    """Prova que Fase B nunca toca o banco quando aborta antes da transação."""

    def cursor(self):
        raise AssertionError("apply_backfill_atomic não deveria abrir cursor neste cenário")

    def commit(self):
        raise AssertionError("apply_backfill_atomic não deveria commitar neste cenário")

    def rollback(self):
        raise AssertionError("apply_backfill_atomic não deveria dar rollback neste cenário")


class _FakeCursorB:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._last_rows = []

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append((norm, params))
        upper = norm.upper()
        if "PG_ADVISORY_XACT_LOCK" in upper or upper.startswith("LOCK TABLE") or upper.startswith("SET LOCAL"):
            self._last_rows = []
        elif "FILE_SHA256, BRAND, SOURCE_TYPE, SOURCE_METADATA" in upper and "WHERE FILE_ID = ANY" not in upper:
            # apply_backfill_atomic: revalidação GLOBAL (sem filtro de file_id)
            self._last_rows = list(self.conn.revalidation_rows)
        elif "FILE_SHA256, BRAND, SOURCE_TYPE, SOURCE_METADATA" in upper and "WHERE FILE_ID = ANY" in upper:
            # restore_from_backup_atomic: revalidação por file_id
            self._last_rows = list(self.conn.current_rows)
        elif upper.startswith("UPDATE RAW.SHOPEE_INGESTION_FILE"):
            file_id = params[1]
            self.rowcount = self.conn.update_rowcounts.get(file_id, 1)
            self.conn.updates.append((file_id, params[0].adapted if hasattr(params[0], "adapted") else params[0]))
        elif upper.startswith("SELECT FILE_ID, SOURCE_METADATA FROM"):
            self._last_rows = list(self.conn.final_rows)
        else:
            self._last_rows = []

    def fetchall(self):
        return self._last_rows

    def close(self):
        pass


class _FakeConnB:
    def __init__(self, revalidation_rows=None, final_rows=None, update_rowcounts=None, current_rows=None):
        self.executed: list[tuple[str, object]] = []
        self.updates: list[tuple[int, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.revalidation_rows = revalidation_rows or []
        self.final_rows = final_rows or []
        self.update_rowcounts = update_rowcounts or {}
        self.current_rows = current_rows or []

    def cursor(self):
        return _FakeCursorB(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _valid_metadata_for(file_id):
    return {
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "report_created_at": "2026-07-15T10:30:00",
        "shop_id": "999999999",
    }


def _ten_pending_items():
    """10 PendingItems realistas: 5 marcas oficiais × 2 arquivos, file_id/
    hash únicos, metadata válida -- o plano REAL que passaria por
    plan_backfill em produção."""
    items = []
    file_id = 1
    for brand in _BRANDS:
        for i in range(2):
            items.append(backfill.PendingItem(
                file_id, f"{brand}/Dados+{i}.csv", f"{file_id:02x}" + "a" * 62,
                brand, "ads", _valid_metadata_for(file_id),
            ))
            file_id += 1
    return items


def _plan_with_ten_items():
    return backfill.BackfillPlan(ready=True, items=_ten_pending_items())


def _plan_with_two_items():
    """Plano DELIBERADAMENTE fora do escopo (2 itens) -- só para provar que
    `apply_backfill_atomic` reprova por conta própria, nunca para simular
    uma aplicação realista (ver `_plan_with_ten_items`)."""
    return backfill.BackfillPlan(
        ready=True,
        items=[
            backfill.PendingItem(1, "loja/A.csv", "a" * 64, "apice", "ads",
                                  {"period_start": "2026-01-01", "period_end": "2026-03-31",
                                   "report_created_at": "2026-07-15T10:30:00", "shop_id": "999999999"}),
            backfill.PendingItem(2, "loja/B.csv", "b" * 64, "barbours", "ads",
                                  {"period_start": "2026-04-01", "period_end": "2026-06-30",
                                   "report_created_at": "2026-07-15T10:30:00", "shop_id": "999999999"}),
        ],
    )


def _revalidation_rows_for(items):
    """Linhas 'sob o lock' que batem 1:1 com os items do plano --
    source_metadata=None (ainda não aplicado)."""
    return [(i.file_id, i.source_filename, i.file_sha256, i.brand, i.source_type, None) for i in items]


def _final_rows_for(items):
    """Linhas pós-UPDATE -- source_metadata == o valor aplicado."""
    return [(i.file_id, i.metadata) for i in items]


def _replace_row(rows, index, **overrides):
    """Substitui campos nomeados de UMA linha (tupla) de
    `_revalidation_rows_for`/`current_rows`, mantendo as demais intactas."""
    fields = ["file_id", "source_filename", "file_sha256", "brand", "source_type", "source_metadata"]
    row = list(rows[index])
    for k, v in overrides.items():
        row[fields.index(k)] = v
    rows = list(rows)
    rows[index] = tuple(row)
    return rows


def test_apply_exige_audit_path_e_repo_root_por_assinatura():
    plan = _plan_with_ten_items()
    with pytest.raises(TypeError):
        backfill.apply_backfill_atomic(_PoisonConn(), plan, confirm_flag=True, confirm_secret_value="1")


def test_apply_aborta_se_plano_nao_pronto_sem_tocar_conexao(tmp_path):
    plan = backfill.BackfillPlan(ready=False, items=[], problems=["esperado exatamente 10 manifestos ads pendentes, encontrado 9"])
    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path,
    )
    assert result.outcome == "aborted_plan_not_ready"
    assert result.problems == plan.problems


@pytest.mark.parametrize("confirm_flag,secret", [(False, "1"), (True, None), (True, "0"), (False, None)])
def test_apply_aborta_se_confirmacao_dupla_ausente_sem_tocar_conexao(tmp_path, confirm_flag, secret):
    plan = _plan_with_ten_items()
    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan, confirm_flag=confirm_flag, confirm_secret_value=secret,
        audit_path=tmp_path / "backup.json", repo_root=tmp_path,
    )
    assert result.outcome == "aborted_confirmation_missing"


def test_apply_aborta_se_nada_pendente_sem_tocar_conexao(tmp_path):
    plan = backfill.BackfillPlan(ready=True, items=[])
    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path,
    )
    assert result.outcome == "aborted_nothing_pending"


def test_apply_nao_confia_em_plan_ready_plano_manual_com_2_itens_e_recusado(tmp_path):
    """O TESTE explicitamente pedido: um BackfillPlan montado à mão com
    `ready=True` mas só 2 itens (fora do escopo histórico de 10) precisa
    ser recusado por `apply_backfill_atomic` SEM abrir cursor -- a função
    nunca pode confiar cegamente em `plan.ready`."""
    plan = _plan_with_two_items()
    assert plan.ready is True  # a premissa do teste: o plano AFIRMA estar pronto

    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path,
    )

    assert result.outcome == "aborted_items_invalid"
    assert any("exatamente 10" in p for p in result.problems)
    assert not (tmp_path / "backup.json").exists()


def test_apply_nao_confia_em_plan_ready_marca_nao_oficial_e_recusada(tmp_path):
    """Mesma ideia, mas com 10 itens (contagem certa) e uma marca não
    oficial -- prova que a checagem NÃO é só "conta os itens", é a MESMA
    validate_manifest_scope completa."""
    items = _ten_pending_items()
    items[0] = backfill.PendingItem(
        items[0].file_id, "marca_fantasma/Dados+0.csv", items[0].file_sha256,
        "marca_fantasma", "ads", items[0].metadata,
    )
    plan = backfill.BackfillPlan(ready=True, items=items)

    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path,
    )

    assert result.outcome == "aborted_items_invalid"
    assert any("conjunto de marcas não bate com o oficial" in p for p in result.problems)


def test_apply_nao_confia_em_plan_ready_metadata_invalida_e_recusada(tmp_path):
    items = _ten_pending_items()
    bad_metadata = dict(items[0].metadata)
    del bad_metadata["shop_id"]
    items[0] = backfill.PendingItem(
        items[0].file_id, items[0].source_filename, items[0].file_sha256,
        items[0].brand, items[0].source_type, bad_metadata,
    )
    plan = backfill.BackfillPlan(ready=True, items=items)

    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path,
    )

    assert result.outcome == "aborted_items_invalid"
    assert any(f"file_id={items[0].file_id}" in p and "chave(s) ausente(s)" in p for p in result.problems)


def test_apply_aborta_se_audit_path_ja_existe_sem_tocar_conexao(tmp_path):
    plan = _plan_with_ten_items()
    audit_path = tmp_path / "backup.json"
    audit_path.write_text("{}", encoding="utf-8")

    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=audit_path, repo_root=tmp_path,
    )
    assert result.outcome == "aborted_audit_path_invalid"
    assert any("já existe" in p for p in result.problems)


def test_apply_aborta_se_audit_path_dentro_do_repo_e_nao_ignorado(tmp_path):
    plan = _plan_with_ten_items()
    inner_repo = tmp_path / "repo"
    inner_repo.mkdir()
    audit_path = inner_repo / "backup.json"

    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=audit_path, repo_root=inner_repo,
    )
    assert result.outcome == "aborted_audit_path_invalid"
    assert any("dentro do repositório" in p or "NÃO coberto" in p for p in result.problems)


def test_apply_aceita_audit_path_fora_do_repo_sem_precisar_de_git(tmp_path):
    plan = _plan_with_ten_items()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    audit_path = scratch / "backup.json"

    conn = _FakeConnB(
        revalidation_rows=_revalidation_rows_for(plan.items),
        final_rows=_final_rows_for(plan.items),
    )
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=audit_path, repo_root=repo,
    )
    assert result.outcome == "committed"
    assert audit_path.exists()


def test_apply_sucesso_completo_commit_atualiza_todos_e_grava_backup(tmp_path):
    plan = _plan_with_ten_items()
    audit_path = tmp_path / "backup.json"
    conn = _FakeConnB(
        revalidation_rows=_revalidation_rows_for(plan.items),
        final_rows=_final_rows_for(plan.items),
    )

    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=audit_path, repo_root=tmp_path / "nao_e_o_repo",
    )

    assert result.outcome == "committed"
    assert conn.committed is True
    assert conn.rolled_back is False
    assert sorted(result.updated_file_ids) == list(range(1, 11))
    assert len(result.backup) == 10
    assert result.backup_sha256 is not None
    assert result.backup_sha256 == sha256_file(audit_path)
    assert len(conn.updates) == 10

    assert audit_path.exists()
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert len(payload) == 10
    assert {p["file_id"] for p in payload} == set(range(1, 11))
    assert all(p["source_metadata_before"] is None for p in payload)
    assert {p["brand"] for p in payload} == set(_BRANDS)
    assert all(p["source_metadata_applied"] is not None for p in payload)
    # nenhum arquivo .tmp deixado para trás
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_apply_conflito_11o_manifesto_surgiu_entre_planejamento_e_lock(tmp_path):
    """Regressão-chave: um manifesto ads pendente ADICIONAL (não estava no
    plano) aparece na revalidação GLOBAL sob o lock -- deve abortar tudo
    ANTES do backup e dos UPDATEs, mesmo que os 10 planejados continuem
    intactos."""
    plan = _plan_with_ten_items()
    rows = _revalidation_rows_for(plan.items) + [(11, "kokeshi/Novo.csv", "c" * 64, "kokeshi", "ads", None)]
    conn = _FakeConnB(revalidation_rows=rows)
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert conn.committed is False
    assert conn.updates == []
    assert not (tmp_path / "backup.json").exists()
    assert any("ADICIONAL" in p and "file_id=[11]" in p for p in result.problems)


def test_apply_conflito_brand_mudou_sob_lock_aborta_tudo(tmp_path):
    plan = _plan_with_ten_items()
    rows = _replace_row(_revalidation_rows_for(plan.items), 0, brand="MARCA_ERRADA")
    conn = _FakeConnB(revalidation_rows=rows)
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert conn.updates == []
    assert any("brand mudou" in p for p in result.problems)
    assert not (tmp_path / "backup.json").exists()


def test_apply_conflito_source_type_mudou_sob_lock_aborta_tudo(tmp_path):
    plan = _plan_with_ten_items()
    rows = _replace_row(_revalidation_rows_for(plan.items), 0, source_type="orders")
    conn = _FakeConnB(revalidation_rows=rows)
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert any("source_type mudou" in p for p in result.problems)


def test_apply_conflito_source_filename_mudou_sob_lock_aborta_tudo_de_verdade(tmp_path):
    plan = _plan_with_ten_items()
    rows = _replace_row(_revalidation_rows_for(plan.items), 0, source_filename="loja/MUDOU.csv")
    conn = _FakeConnB(revalidation_rows=rows)
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert any("source_filename mudou" in p for p in result.problems)


def test_apply_conflito_hash_mudou_sob_lock_aborta_tudo(tmp_path):
    plan = _plan_with_ten_items()
    rows = _replace_row(_revalidation_rows_for(plan.items), 0, file_sha256="MUDOU" + "a" * 59)
    conn = _FakeConnB(revalidation_rows=rows)
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert conn.committed is False
    assert conn.updates == []
    assert any("hash mudou sob o lock" in p for p in result.problems)


def test_apply_conflito_source_metadata_deixou_de_ser_null_aborta_tudo(tmp_path):
    plan = _plan_with_ten_items()
    rows = _replace_row(_revalidation_rows_for(plan.items), 0, source_metadata={"period_start": "2020-01-01"})
    conn = _FakeConnB(revalidation_rows=rows)
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert conn.updates == []
    assert any("deixou de ser NULL" in p for p in result.problems)


def test_apply_conflito_manifesto_sumiu_sob_lock_aborta_tudo(tmp_path):
    plan = _plan_with_ten_items()
    rows = _revalidation_rows_for(plan.items)[:-1]  # o último "sumiu"
    conn = _FakeConnB(revalidation_rows=rows)
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert any("sumiram sob o lock" in p for p in result.problems)


def test_apply_rowcount_zero_e_tratado_como_conflito_nao_como_skip(tmp_path):
    plan = _plan_with_ten_items()
    conn = _FakeConnB(
        revalidation_rows=_revalidation_rows_for(plan.items),
        update_rowcounts={5: 0},
    )
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert conn.committed is False
    assert "skipped" not in result.outcome
    assert any("conflito concorrente" in p for p in result.problems)
    assert len([u for u in conn.updates if u[0] == 5]) == 1
    assert (tmp_path / "backup.json").exists()


def test_apply_rowcount_maior_que_um_tambem_e_conflito(tmp_path):
    plan = _plan_with_ten_items()
    conn = _FakeConnB(
        revalidation_rows=_revalidation_rows_for(plan.items),
        update_rowcounts={1: 2},
    )
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True


def test_apply_falha_na_reconciliacao_pos_update_aborta_mesmo_com_rowcount_1(tmp_path):
    plan = _plan_with_ten_items()
    final_rows = _final_rows_for(plan.items)
    final_rows[3] = (final_rows[3][0], {"period_start": "VALOR-ERRADO"})
    conn = _FakeConnB(revalidation_rows=_revalidation_rows_for(plan.items), final_rows=final_rows)
    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert conn.committed is False
    assert any("não bate com o esperado" in p for p in result.problems)


def test_apply_usa_set_local_timeouts(tmp_path):
    plan = _plan_with_ten_items()
    conn = _FakeConnB(
        revalidation_rows=_revalidation_rows_for(plan.items),
        final_rows=_final_rows_for(plan.items),
    )
    backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    sqls = [s for s, _ in conn.executed]
    assert any("SET LOCAL lock_timeout" in s for s in sqls)
    assert any("SET LOCAL statement_timeout" in s for s in sqls)


def test_advisory_lock_key_reaproveitada_de_write_conn(tmp_path):
    plan = _plan_with_ten_items()
    conn = _FakeConnB(
        revalidation_rows=_revalidation_rows_for(plan.items),
        final_rows=_final_rows_for(plan.items),
    )
    backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path / "nao_e_o_repo",
    )
    lock_calls = [p for sql, p in conn.executed if "PG_ADVISORY_XACT_LOCK" in sql.upper()]
    assert lock_calls == [(write_conn.ADVISORY_LOCK_KEY,)]


def test_apply_e_idempotente_segunda_chamada_com_plano_vazio_e_no_op(tmp_path):
    plan_2a_rodada = backfill.BackfillPlan(ready=True, items=[])
    result = backfill.apply_backfill_atomic(
        _PoisonConn(), plan_2a_rodada, confirm_flag=True, confirm_secret_value="1",
        audit_path=tmp_path / "backup.json", repo_root=tmp_path,
    )
    assert result.outcome == "aborted_nothing_pending"


# =============================================================================
# CLI: _check_migration_state / _dry_run_with_conn (núcleo testável do
# --dry-run) -- conexão SQLAlchemy FALSA que despacha por conteúdo do SQL,
# mesmo estilo de _FakeCursorB (Fase B) para nunca depender de banco real.
# =============================================================================


class _FakeResult:
    def __init__(self, scalar_value=None, fetchone_value=None, mapping_rows=None):
        self._scalar_value = scalar_value
        self._fetchone_value = fetchone_value
        self._rows = mapping_rows or []

    def scalar(self):
        return self._scalar_value

    def fetchone(self):
        return self._fetchone_value

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeDryRunConn:
    def __init__(
        self, *, in_recovery=False, column_type="jsonb", constraint_row=(True,),
        total_manifests=120, not_null_count=0, pending_manifests=None,
    ):
        self.in_recovery = in_recovery
        self.column_type = column_type
        self.constraint_row = constraint_row
        self.total_manifests = total_manifests
        self.not_null_count = not_null_count
        self.pending_manifests = pending_manifests or []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        upper = sql.upper()
        if "PG_IS_IN_RECOVERY" in upper:
            return _FakeResult(scalar_value=self.in_recovery)
        if "INFORMATION_SCHEMA.COLUMNS" in upper:
            return _FakeResult(fetchone_value=(self.column_type,) if self.column_type else None)
        if "PG_CONSTRAINT" in upper:
            return _FakeResult(fetchone_value=self.constraint_row)
        if "IS NOT NULL" in upper:
            return _FakeResult(scalar_value=self.not_null_count)
        if upper.strip().startswith("SELECT COUNT(*) FROM RAW.SHOPEE_INGESTION_FILE"):
            return _FakeResult(scalar_value=self.total_manifests)
        if "SOURCE_TYPE = 'ADS'" in upper:
            return _FakeResult(mapping_rows=self.pending_manifests)
        raise AssertionError(f"SQL inesperado na conexão falsa: {sql}")


def _make_fake_engine(conn):
    class _Ctx:
        def __enter__(self):
            return conn

        def __exit__(self, *a):
            return False

    class _ConnectResult:
        def execution_options(self, **kw):
            return _Ctx()

    class _Engine:
        def connect(self):
            return _ConnectResult()

    return _Engine()


def test_check_migration_state_ok_quando_tudo_confere():
    check = backfill._check_migration_state(_FakeDryRunConn())
    assert check.ok is True
    assert check.problems == []


def test_check_migration_state_replica_detectada_aborta_antes_de_checar_coluna():
    check = backfill._check_migration_state(_FakeDryRunConn(in_recovery=True))
    assert check.ok is False
    assert any("pg_is_in_recovery" in p for p in check.problems)
    assert check.column_exists is False  # nunca chegou a checar


def test_check_migration_state_coluna_ausente():
    check = backfill._check_migration_state(_FakeDryRunConn(column_type=None))
    assert check.ok is False
    assert any("coluna source_metadata não existe" in p for p in check.problems)


def test_check_migration_state_constraint_ausente():
    check = backfill._check_migration_state(_FakeDryRunConn(constraint_row=None))
    assert check.ok is False
    assert any("constraint" in p and "não existe" in p for p in check.problems)


def test_check_migration_state_constraint_nao_validada():
    check = backfill._check_migration_state(_FakeDryRunConn(constraint_row=(False,)))
    assert check.ok is False
    assert any("não está validada" in p for p in check.problems)


def test_check_migration_state_drift_no_total_de_manifestos():
    check = backfill._check_migration_state(_FakeDryRunConn(total_manifests=121))
    assert check.ok is False
    assert any("total de manifestos mudou" in p for p in check.problems)


def test_check_migration_state_metadata_ja_preenchida_e_drift():
    check = backfill._check_migration_state(_FakeDryRunConn(not_null_count=3))
    assert check.ok is False
    assert any("já têm source_metadata" in p for p in check.problems)


def test_dry_run_com_conn_plano_10_10_pronto_retorna_exit_0(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    report = backfill._dry_run_with_conn(_FakeDryRunConn(pending_manifests=manifests), tmp_path)
    assert report.ready is True
    assert report.exit_code == 0
    assert report.total_pending_ads == 10
    assert set(report.count_by_brand) == set(_BRANDS)
    assert len(report.periods) == 10
    assert report.problems == []


def test_dry_run_com_conn_9_manifestos_retorna_nao_zero(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)[:9]
    report = backfill._dry_run_with_conn(_FakeDryRunConn(pending_manifests=manifests), tmp_path)
    assert report.ready is False
    assert report.exit_code != 0
    assert report.total_pending_ads == 9
    assert any("exatamente 10" in p for p in report.problems)


def test_dry_run_com_conn_11_manifestos_retorna_nao_zero(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    extra = dict(manifests[0])
    extra["file_id"] = 999
    extra["source_filename"] = "apice/Dados+extra.csv"
    _write_valid_ads_csv(tmp_path / extra["source_filename"])
    extra["file_sha256"] = sha256_file(tmp_path / extra["source_filename"])
    manifests = manifests + [extra]
    report = backfill._dry_run_with_conn(_FakeDryRunConn(pending_manifests=manifests), tmp_path)
    assert report.ready is False
    assert report.exit_code != 0
    assert report.total_pending_ads == 11
    assert any("exatamente 10" in p for p in report.problems)


def test_dry_run_com_conn_hash_divergente_retorna_nao_zero(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    manifests[0] = dict(manifests[0], file_sha256="f" * 64)
    report = backfill._dry_run_with_conn(_FakeDryRunConn(pending_manifests=manifests), tmp_path)
    assert report.ready is False
    assert report.exit_code != 0
    assert any("hash" in p for p in report.problems)


def test_dry_run_com_conn_preambulo_invalido_retorna_nao_zero(tmp_path):
    manifests = _exact_scope_manifests(tmp_path)
    bad_path = tmp_path / manifests[0]["source_filename"]
    bad_path.write_text(_INVALID_CSV, encoding="utf-8-sig", newline="")
    manifests[0] = dict(manifests[0], file_sha256=sha256_file(bad_path))
    report = backfill._dry_run_with_conn(_FakeDryRunConn(pending_manifests=manifests), tmp_path)
    assert report.ready is False
    assert report.exit_code != 0
    assert any(f"file_id={manifests[0]['file_id']}" in p for p in report.problems)


def test_dry_run_com_conn_coluna_ausente_nao_chega_a_buscar_manifestos(tmp_path, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("não deveria buscar manifestos com migration não confirmada")

    monkeypatch.setattr(backfill, "_pending_ads_manifests", _boom)
    report = backfill._dry_run_with_conn(_FakeDryRunConn(column_type=None), tmp_path)
    assert report.ready is False
    assert report.exit_code == 3
    assert any("coluna source_metadata não existe" in p for p in report.problems)


def test_dry_run_com_conn_replica_detectada_nao_chega_a_buscar_manifestos(tmp_path, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("não deveria buscar manifestos numa réplica")

    monkeypatch.setattr(backfill, "_pending_ads_manifests", _boom)
    report = backfill._dry_run_with_conn(_FakeDryRunConn(in_recovery=True), tmp_path)
    assert report.ready is False
    assert report.exit_code == 3


def test_dry_run_report_nunca_expoe_shop_id_url_ou_filename_completo(tmp_path, capsys):
    manifests = _exact_scope_manifests(tmp_path)
    report = backfill._dry_run_with_conn(_FakeDryRunConn(pending_manifests=manifests), tmp_path)
    assert report.ready is True

    backfill._print_dry_run_report(report)
    out = capsys.readouterr().out

    assert "999999999" not in out  # shop_id usado em _VALID_CSV
    for m in manifests:
        assert m["source_filename"] not in out  # filename completo nunca aparece
    assert "postgresql://" not in out
    assert "@" not in out  # forma comum de credencial embutida em URL


def test_run_dry_run_bloqueado_por_secret_nunca_abre_engine(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(backfill, "create_engine", lambda *a, **k: called.append(1))
    report = backfill.run_dry_run(secret_path=tmp_path / "nao_existe.local", repo_root=tmp_path)
    assert report.ready is False
    assert report.exit_code == 2
    assert called == []


def test_main_dry_run_usa_run_dry_run_e_propaga_exit_code_pronto(monkeypatch, capsys):
    fake_report = backfill.DryRunReport(
        ready=True, exit_code=0,
        migration_state=backfill.MigrationStateCheck(ok=True, total_manifests=120, manifests_with_metadata_not_null=0),
        total_pending_ads=10, count_by_brand={"apice": 2},
        periods=[{"brand": "apice", "period_start": "2026-01-01", "period_end": "2026-03-31"}],
    )
    monkeypatch.setattr(backfill, "run_dry_run", lambda **kw: fake_report)
    exit_code = backfill.main(["--dry-run"])
    assert exit_code == 0
    assert "ready=True" in capsys.readouterr().out


def test_main_dry_run_retorna_exit_code_nao_zero_quando_nao_pronto(monkeypatch):
    fake_report = backfill.DryRunReport(
        ready=False, exit_code=5,
        migration_state=backfill.MigrationStateCheck(ok=True, total_manifests=120, manifests_with_metadata_not_null=0),
        problems=["esperado exatamente 10 manifestos, encontrado 9"],
    )
    monkeypatch.setattr(backfill, "run_dry_run", lambda **kw: fake_report)
    assert backfill.main(["--dry-run"]) == 5


# --- CLI: --apply-confirmed (guardrails -- NUNCA chamado contra banco real) --


def test_apply_confirmed_sem_audit_path_nao_chama_run_apply_confirmed(monkeypatch):
    called = []
    monkeypatch.setattr(backfill, "run_apply_confirmed", lambda *a, **k: called.append(1))
    exit_code = backfill.main(["--apply-confirmed"])
    assert exit_code != 0
    assert called == []


def test_apply_confirmed_bloqueado_por_secret_ausente_nao_abre_conexao_de_escrita(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(write_conn, "open_write_connection", lambda *a, **k: called.append(1))
    exit_code = backfill.run_apply_confirmed(
        tmp_path / "backup.json", secret_path=tmp_path / "nao_existe.local", repo_root=tmp_path,
    )
    assert exit_code == 2
    assert called == []


def test_apply_confirmed_bloqueado_por_i_understand_diferente_de_1_nao_abre_conexao(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.shopee-write.local"
    secret_path.write_text(
        "DATAMART_SHOPEE_WRITE_URL=postgresql://writer@host/db\nI_UNDERSTAND_THIS_WRITES_DATAMART_RAW=0\n"
    )
    called = []
    monkeypatch.setattr(write_conn, "open_write_connection", lambda *a, **k: called.append(1))
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", secret_path=secret_path, repo_root=tmp_path)
    assert exit_code == 2
    assert called == []


def test_apply_confirmed_bloqueado_por_preflight_nao_abre_conexao_de_escrita(monkeypatch, tmp_path):
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    blocked_report = write_conn.PreflightReport(ok=False, blocking_reasons=["rolsuper=true"])
    monkeypatch.setattr(backfill.write_conn, "run_preflight", lambda *a, **k: blocked_report)
    called = []
    monkeypatch.setattr(backfill.write_conn, "open_write_connection", lambda *a, **k: called.append(1))
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path)
    assert exit_code == 3
    assert called == []


def test_apply_confirmed_bloqueado_por_replica_nao_abre_conexao_de_escrita(monkeypatch, tmp_path):
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    ok_report = write_conn.PreflightReport(ok=True, safe_summary={"rolsuper": False})
    monkeypatch.setattr(backfill.write_conn, "run_preflight", lambda *a, **k: ok_report)
    monkeypatch.setattr(backfill, "create_engine", lambda *a, **k: _make_fake_engine(_FakeDryRunConn(in_recovery=True)))
    called = []
    monkeypatch.setattr(backfill.write_conn, "open_write_connection", lambda *a, **k: called.append(1))
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 4
    assert called == []


def test_apply_confirmed_bloqueado_por_plano_nao_pronto_nao_abre_conexao_de_escrita(monkeypatch, tmp_path):
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    ok_report = write_conn.PreflightReport(ok=True, safe_summary={"rolsuper": False})
    monkeypatch.setattr(backfill.write_conn, "run_preflight", lambda *a, **k: ok_report)
    manifests = _exact_scope_manifests(tmp_path)[:9]  # escopo errado -> plano nunca fica pronto
    monkeypatch.setattr(
        backfill, "create_engine", lambda *a, **k: _make_fake_engine(_FakeDryRunConn(pending_manifests=manifests))
    )
    called = []
    monkeypatch.setattr(backfill.write_conn, "open_write_connection", lambda *a, **k: called.append(1))
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 5
    assert called == []


def test_apply_confirmed_sucesso_chama_exclusivamente_apply_backfill_atomic(monkeypatch, tmp_path):
    """Prova que, quando todas as guardas passam, a ÚNICA operação de
    escrita é a chamada a apply_backfill_atomic (nunca um INSERT/UPDATE
    solto no meio de run_apply_confirmed)."""
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    ok_report = write_conn.PreflightReport(ok=True, safe_summary={"rolsuper": False})
    monkeypatch.setattr(backfill.write_conn, "run_preflight", lambda *a, **k: ok_report)
    manifests = _exact_scope_manifests(tmp_path)
    monkeypatch.setattr(
        backfill, "create_engine", lambda *a, **k: _make_fake_engine(_FakeDryRunConn(pending_manifests=manifests))
    )

    class _FakeWriteConn:
        def close(self):
            pass

    fake_write_conn = _FakeWriteConn()
    monkeypatch.setattr(backfill.write_conn, "open_write_connection", lambda *a, **k: fake_write_conn)
    monkeypatch.setattr(backfill.write_conn, "try_acquire_advisory_lock", lambda conn: True)
    monkeypatch.setattr(backfill.write_conn, "release_advisory_lock", lambda conn: None)

    calls = []

    def _fake_apply(conn, plan, **kw):
        calls.append((conn, plan, kw))
        return backfill.BackfillResult(outcome="committed", updated_file_ids=list(range(1, 11)))

    monkeypatch.setattr(backfill, "apply_backfill_atomic", _fake_apply)

    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)

    assert exit_code == 0
    assert len(calls) == 1
    conn, plan, kw = calls[0]
    assert conn is fake_write_conn
    assert plan.ready is True
    assert kw["confirm_flag"] is True
    assert kw["confirm_secret_value"] == "1"


# =============================================================================
# CLI: sanitização adversarial de TODAS as falhas do caminho de escrita --
# exceções simuladas em cada um dos 9 pontos de risco de run_apply_confirmed
# carregam uma DSN fictícia com senha, um filename fictício, um shop_id
# fictício e um CPF fictício, para provar que NADA disso aparece em
# stdout/stderr, mesmo numa exceção nunca antes vista. --apply-confirmed
# nunca é chamado contra um banco real nestes testes.
# =============================================================================

_FAKE_SECRET_MSG = (
    "dsn=postgresql://shopee_writer:S3nh4Sup3rSecreta@db.internal.acme.com:5432/datamart "
    "arquivo=apice/Dados+Gerais-01-01-2026.csv shop_id=999999999 cpf=123.456.789-00"
)
_FAKE_SECRET_TOKENS = (
    "S3nh4Sup3rSecreta",
    "shopee_writer:S3nh4Sup3rSecreta@",
    "Dados+Gerais-01-01-2026.csv",
    "999999999",
    "123.456.789-00",
)


def _assert_no_secrets_leaked(text):
    for token in _FAKE_SECRET_TOKENS:
        assert token not in text, f"vazou em stdout/stderr: {token!r}\n--- saída completa ---\n{text}"


def _setup_apply_confirmed_happy_guards(monkeypatch, tmp_path, pending_manifests=None):
    """Deixa passar secret/preflight/migration/plano até o ponto de abrir a
    conexão de escrita -- usado pelos testes que simulam falha DEPOIS
    desse ponto (open_write_connection em diante)."""
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    ok_report = write_conn.PreflightReport(ok=True, safe_summary={"rolsuper": False})
    monkeypatch.setattr(backfill.write_conn, "run_preflight", lambda *a, **k: ok_report)
    manifests = pending_manifests if pending_manifests is not None else _exact_scope_manifests(tmp_path)
    monkeypatch.setattr(
        backfill, "create_engine", lambda *a, **k: _make_fake_engine(_FakeDryRunConn(pending_manifests=manifests))
    )


def test_apply_confirmed_secret_excecao_inesperada_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    def _boom(p, r):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill.write_conn, "load_write_secret", _boom)
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 2
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_run_preflight_excecao_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })

    def _boom(*a, **k):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill.write_conn, "run_preflight", _boom)
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 3
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_create_engine_excecao_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    ok_report = write_conn.PreflightReport(ok=True, safe_summary={"rolsuper": False})
    monkeypatch.setattr(backfill.write_conn, "run_preflight", lambda *a, **k: ok_report)

    def _boom(*a, **k):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill, "create_engine", _boom)
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 4
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_check_migration_state_excecao_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    ok_report = write_conn.PreflightReport(ok=True, safe_summary={"rolsuper": False})
    monkeypatch.setattr(backfill.write_conn, "run_preflight", lambda *a, **k: ok_report)

    class _FakeConnRaisingOnExecute:
        def execute(self, *a, **k):
            raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill, "create_engine", lambda *a, **k: _make_fake_engine(_FakeConnRaisingOnExecute()))
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 4
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_plan_backfill_excecao_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backfill.write_conn, "load_write_secret", lambda p, r: {
        "DATAMART_SHOPEE_WRITE_URL": "postgresql://writer@host/db",
        "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW": "1",
    })
    ok_report = write_conn.PreflightReport(ok=True, safe_summary={"rolsuper": False})
    monkeypatch.setattr(backfill.write_conn, "run_preflight", lambda *a, **k: ok_report)
    monkeypatch.setattr(backfill, "create_engine", lambda *a, **k: _make_fake_engine(_FakeDryRunConn()))

    def _boom(*a, **k):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill, "_pending_ads_manifests", _boom)
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 4
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_open_write_connection_excecao_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    _setup_apply_confirmed_happy_guards(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill.write_conn, "open_write_connection", _boom)
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 6
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_try_acquire_advisory_lock_excecao_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    _setup_apply_confirmed_happy_guards(monkeypatch, tmp_path)

    class _FakeWriteConn:
        def close(self):
            pass

    monkeypatch.setattr(backfill.write_conn, "open_write_connection", lambda *a, **k: _FakeWriteConn())

    def _boom(conn):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill.write_conn, "try_acquire_advisory_lock", _boom)
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 6
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_apply_backfill_atomic_excecao_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    _setup_apply_confirmed_happy_guards(monkeypatch, tmp_path)

    class _FakeWriteConn:
        def close(self):
            pass

    monkeypatch.setattr(backfill.write_conn, "open_write_connection", lambda *a, **k: _FakeWriteConn())
    monkeypatch.setattr(backfill.write_conn, "try_acquire_advisory_lock", lambda conn: True)
    monkeypatch.setattr(backfill.write_conn, "release_advisory_lock", lambda conn: None)

    def _boom(*a, **k):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill, "apply_backfill_atomic", _boom)
    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)
    assert exit_code == 9
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_commit_ocorreu_mas_release_lock_falha_reporta_committed(tmp_path, monkeypatch, capsys):
    """O cenário crítico pedido: apply_backfill_atomic já retornou
    'committed' -- uma falha DEPOIS disso (release_advisory_lock) NUNCA
    pode virar 'o backfill falhou'. A conexão ainda é fechada (libera o
    lock de sessão de qualquer forma), o resultado reportado continua
    'committed', o exit code continua 0, e o aviso nunca sugere retry."""
    _setup_apply_confirmed_happy_guards(monkeypatch, tmp_path)

    closed = []

    class _FakeWriteConn:
        def close(self):
            closed.append(1)

    monkeypatch.setattr(backfill.write_conn, "open_write_connection", lambda *a, **k: _FakeWriteConn())
    monkeypatch.setattr(backfill.write_conn, "try_acquire_advisory_lock", lambda conn: True)

    def _boom_release(conn):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill.write_conn, "release_advisory_lock", _boom_release)
    committed_result = backfill.BackfillResult(
        outcome="committed", updated_file_ids=list(range(1, 11)), backup_sha256="a" * 64,
    )
    monkeypatch.setattr(backfill, "apply_backfill_atomic", lambda *a, **k: committed_result)

    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)

    assert exit_code == 0  # NUNCA reportado como falho por causa do release do lock
    assert closed == [1]  # conexão fechada mesmo com a falha -- libera o lock de sessão de qualquer forma
    captured = capsys.readouterr()
    assert "Resultado: committed" in captured.out
    assert "AVISO" in captured.err
    # o aviso pode EXPLICAR que não há retry automático (é a garantia
    # pedida), mas nunca pode SUGERIR uma ação de repetir a operação
    assert "tente novamente" not in captured.err.lower()
    assert "tentar novamente" not in captured.err.lower()
    _assert_no_secrets_leaked(captured.out + captured.err)


def test_apply_confirmed_conn_close_excecao_nunca_vaza_segredos(tmp_path, monkeypatch, capsys):
    _setup_apply_confirmed_happy_guards(monkeypatch, tmp_path)

    class _FakeWriteConn:
        def close(self):
            raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill.write_conn, "open_write_connection", lambda *a, **k: _FakeWriteConn())
    monkeypatch.setattr(backfill.write_conn, "try_acquire_advisory_lock", lambda conn: True)
    monkeypatch.setattr(backfill.write_conn, "release_advisory_lock", lambda conn: None)
    committed_result = backfill.BackfillResult(outcome="committed", updated_file_ids=list(range(1, 11)))
    monkeypatch.setattr(backfill, "apply_backfill_atomic", lambda *a, **k: committed_result)

    exit_code = backfill.run_apply_confirmed(tmp_path / "backup.json", repo_root=tmp_path, data_path=tmp_path)

    assert exit_code == 0  # falha ao fechar a conexão não muda o resultado já obtido
    captured = capsys.readouterr()
    assert "Resultado: committed" in captured.out
    _assert_no_secrets_leaked(captured.out + captured.err)


# --- main(): última barreira ------------------------------------------------


def test_main_ultima_barreira_cobre_dry_run_e_nunca_vaza_traceback_cru(monkeypatch, capsys):
    def _boom(**kw):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill, "run_dry_run", _boom)
    exit_code = backfill.main(["--dry-run"])
    assert exit_code == 10
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_main_ultima_barreira_cobre_apply_confirmed_e_nunca_vaza_traceback_cru(monkeypatch, tmp_path, capsys):
    def _boom(*a, **k):
        raise RuntimeError(_FAKE_SECRET_MSG)

    monkeypatch.setattr(backfill, "run_apply_confirmed", _boom)
    exit_code = backfill.main(["--apply-confirmed", "--audit-path", str(tmp_path / "backup.json")])
    assert exit_code == 10
    captured = capsys.readouterr()
    _assert_no_secrets_leaked(captured.out + captured.err)
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_backfill_cli_nenhuma_dependencia_nova():
    import ast
    from pathlib import Path

    src = Path(backfill.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_modules.add(node.module.split(".")[0])
    allowed = {
        "__future__", "argparse", "json", "os", "re", "sys", "tempfile",
        "dataclasses", "datetime", "pathlib", "typing",
        "psycopg2", "sqlalchemy", "pipelines",
    }
    assert top_level_modules <= allowed, top_level_modules - allowed


# --- backup sem possibilidade de sobrescrita (corrida TOCTOU) ---------------


def test_write_audit_atomic_aborta_se_destino_surge_entre_validacao_e_publicacao(tmp_path, monkeypatch):
    """Simula a corrida: `_validate_audit_path` passou (arquivo não existia
    na checagem), mas outro processo criou o destino antes de `os.link`
    publicar. `os.link` deve detectar e abortar SEM sobrescrever."""
    plan = _plan_with_ten_items()
    audit_path = tmp_path / "backup.json"
    conn = _FakeConnB(revalidation_rows=_revalidation_rows_for(plan.items))

    import pipelines.ingestion.shopee_raw.backfill_ads_metadata as mod

    real_link = mod.os.link

    def _link_after_race(src, dst):
        # simula outro processo criando o destino bem antes do link real
        with open(dst, "w", encoding="utf-8") as f:
            f.write("conteudo de outro processo -- nunca deve ser sobrescrito")
        return real_link(src, dst)

    monkeypatch.setattr(mod.os, "link", _link_after_race)

    result = backfill.apply_backfill_atomic(
        conn, plan, confirm_flag=True, confirm_secret_value="1",
        audit_path=audit_path, repo_root=tmp_path / "nao_e_o_repo",
    )

    assert result.outcome == "aborted_backup_failed"
    assert conn.rolled_back is True
    assert conn.committed is False
    assert conn.updates == []
    # o conteúdo do "outro processo" nunca foi sobrescrito
    assert audit_path.read_text(encoding="utf-8") == "conteudo de outro processo -- nunca deve ser sobrescrito"
    # nenhum .tmp deixado para trás
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_write_audit_atomic_nunca_usa_os_replace(tmp_path):
    """os.replace SUBSTITUI o destino silenciosamente -- este módulo nunca
    pode CHAMÁ-lo para publicar o backup (só os.link, que falha se o
    destino já existir). A prosa da docstring pode CITAR 'os.replace' para
    explicar a diferença -- o que não pode existir é a CHAMADA."""
    import inspect
    src = inspect.getsource(backfill._write_audit_file_atomic)
    assert "os.replace(" not in src
    assert "os.link(" in src


def test_write_audit_atomic_usa_temporario_exclusivo_e_fsync(tmp_path):
    import inspect
    src = inspect.getsource(backfill._write_audit_file_atomic)
    assert "tempfile.mkstemp" in src
    assert "os.fsync" in src


# --- restore_from_backup_atomic (nunca executado contra banco real) --------


def _valid_backup_records():
    """10 registros válidos, 2 por marca oficial, cada um com file_sha256
    hexadecimal único (64 chars) e source_metadata_applied completo."""
    records = []
    file_id = 1
    for brand in _BRANDS:
        for i in range(2):
            records.append({
                "file_id": file_id,
                "source_filename": f"{brand}/Dados+{i}.csv",
                "file_sha256": f"{file_id:02x}" + "a" * 62,
                "brand": brand,
                "source_type": "ads",
                "source_metadata_before": None,
                "source_metadata_applied": {
                    "period_start": "2026-01-01",
                    "period_end": "2026-03-31",
                    "report_created_at": "2026-07-15T10:30:00",
                    "shop_id": "999999999",
                },
            })
            file_id += 1
    return records


def _write_backup_and_hash(path, records):
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return sha256_file(path)


def _current_rows_matching(records):
    """Linhas 'atuais' do banco que batem 1:1 com os registros do backup —
    usadas para simular o estado ANTES da restauração (== o estado
    aplicado pelo backfill)."""
    return [
        (r["file_id"], r["source_filename"], r["file_sha256"], r["brand"], r["source_type"], r["source_metadata_applied"])
        for r in records
    ]


def _final_rows_after_restore(records):
    return [(r["file_id"], r["source_metadata_before"]) for r in records]


def test_restore_aborta_se_confirmacao_dupla_ausente(tmp_path):
    audit_path = tmp_path / "backup.json"
    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=False, confirm_secret_value="1",
        expected_backup_sha256="0" * 64,
    )
    assert result.outcome == "aborted_confirmation_missing"


@pytest.mark.parametrize("bad_expected", ["", "z" * 64, "curto", 12345, None, "a" * 63])
def test_restore_expected_backup_sha256_invalido_e_recusado_sem_tocar_arquivo(tmp_path, bad_expected):
    """expected_backup_sha256 é tratado como parâmetro potencialmente
    inválido -- checado ANTES de ler o arquivo, e nunca propaga exceção."""
    records = _valid_backup_records()
    audit_path = tmp_path / "backup.json"
    _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=bad_expected,
    )
    assert result.outcome == "aborted_backup_invalid"


def test_restore_aborta_se_sha_nao_bate(tmp_path):
    records = _valid_backup_records()
    audit_path = tmp_path / "backup.json"
    _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256="f" * 64,  # deliberadamente errado (mas bem formado)
    )
    assert result.outcome == "aborted_backup_sha_mismatch"


def test_restore_aborta_se_backup_ilegivel(tmp_path):
    audit_path = tmp_path / "backup.json"
    audit_path.write_text("{ nao e json valido", encoding="utf-8")
    expected_sha = sha256_file(audit_path)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"


def test_restore_aborta_se_top_level_nao_e_lista(tmp_path):
    audit_path = tmp_path / "backup.json"
    audit_path.write_text(json.dumps({"nao": "e uma lista"}), encoding="utf-8")
    expected_sha = sha256_file(audit_path)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any("lista" in p for p in result.problems)


def test_restore_aborta_se_backup_vazio(tmp_path):
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, [])
    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any("esperado exatamente 10" in p for p in result.problems)


@pytest.mark.parametrize("n", [9, 11])
def test_restore_aborta_se_9_ou_11_registros(tmp_path, n):
    records = _valid_backup_records()
    if n == 9:
        records = records[:9]
    else:
        extra = copy.deepcopy(records[0])
        extra["file_id"] = 999
        extra["file_sha256"] = "f" * 64
        records = records + [extra]
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any(f"encontrado {len(records)}" in p for p in result.problems)


def test_restore_aborta_se_file_id_duplicado(tmp_path):
    records = _valid_backup_records()
    records[1]["file_id"] = records[0]["file_id"]
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any("file_id duplicado" in p for p in result.problems)


def test_restore_aborta_se_marca_inesperada(tmp_path):
    records = _valid_backup_records()
    records[0]["brand"] = "marca_desconhecida"
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any("conjunto de marcas não bate com o oficial" in p for p in result.problems)


def test_restore_aborta_se_distribuicao_diferente_de_2_por_marca(tmp_path):
    records = _valid_backup_records()
    # move 1 registro de rituaria para apice -> apice=3, rituaria=1
    for r in records:
        if r["brand"] == "rituaria":
            r["brand"] = "apice"
            break
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any("quantidade de arquivos != 2" in p for p in result.problems)


def test_restore_aborta_se_source_type_diferente(tmp_path):
    records = _valid_backup_records()
    records[0]["source_type"] = "orders"
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any("source_type != 'ads'" in p for p in result.problems)


def test_restore_aborta_se_metadata_com_chave_extra(tmp_path):
    records = _valid_backup_records()
    records[0]["source_metadata_applied"]["chave_extra"] = "valor"
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any("chave(s) extra(s)" in p for p in result.problems)


@pytest.mark.parametrize("field,value", [
    ("period_start", "01-01-2026"),
    ("period_end", "2026-13-40"),
    ("report_created_at", "2026-01-01 10:00:00"),
    ("shop_id", "ABC123"),
])
def test_restore_aborta_se_metadata_aplicada_invalida(tmp_path, field, value):
    records = _valid_backup_records()
    records[0]["source_metadata_applied"][field] = value
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"


def test_restore_aborta_se_period_start_posterior_a_period_end(tmp_path):
    records = _valid_backup_records()
    records[0]["source_metadata_applied"]["period_start"] = "2026-12-31"
    records[0]["source_metadata_applied"]["period_end"] = "2026-01-01"
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"
    assert any("posterior a period_end" in p for p in result.problems)


# --- type-safety adversarial: JSON arbitrário nunca levanta exceção --------


@pytest.mark.parametrize("bad_file_id", ["1", [1], {"a": 1}, True, False, 0, -5])
def test_restore_type_safety_file_id_invalido_nunca_propaga_excecao(tmp_path, bad_file_id):
    records = _valid_backup_records()
    records[0]["file_id"] = bad_file_id
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"


@pytest.mark.parametrize("bad_hash", [[1, 2, 3], {"a": 1}, None])
def test_restore_type_safety_hash_invalido_nunca_propaga_excecao(tmp_path, bad_hash):
    records = _valid_backup_records()
    records[0]["file_sha256"] = bad_hash
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"


@pytest.mark.parametrize("bad_brand", [[1], {"a": 1}, None])
def test_restore_type_safety_brand_invalido_nunca_propaga_excecao(tmp_path, bad_brand):
    records = _valid_backup_records()
    records[0]["brand"] = bad_brand
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"


@pytest.mark.parametrize("bad_filename", ["", 123, None, []])
def test_restore_type_safety_source_filename_invalido_nunca_propaga_excecao(tmp_path, bad_filename):
    records = _valid_backup_records()
    records[0]["source_filename"] = bad_filename
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"


@pytest.mark.parametrize("bad_metadata", [["a"], "nao e um dict"])
def test_restore_type_safety_metadata_aplicada_invalida_nunca_propaga_excecao(tmp_path, bad_metadata):
    records = _valid_backup_records()
    records[0]["source_metadata_applied"] = bad_metadata
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"


def test_restore_type_safety_registro_nao_dict_nunca_propaga_excecao(tmp_path):
    records = _valid_backup_records()
    records[0] = ["nao", "e", "um", "dict"]
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    result = backfill.restore_from_backup_atomic(
        _PoisonConn(), audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )
    assert result.outcome == "aborted_backup_invalid"


# --- restore: sucesso, compare-and-swap e reconciliação --------------------


def test_restore_sucesso_completo_reverte_para_null(tmp_path):
    records = _valid_backup_records()
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    conn = _FakeConnB(
        current_rows=_current_rows_matching(records),
        final_rows=_final_rows_after_restore(records),
    )

    result = backfill.restore_from_backup_atomic(
        conn, audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )

    assert result.outcome == "committed"
    assert conn.committed is True
    assert sorted(result.restored_file_ids) == list(range(1, 11))
    assert len(conn.updates) == 10
    assert all(v is None for _, v in conn.updates)


def test_restore_usa_compare_and_swap_com_source_metadata_atual_no_where(tmp_path):
    records = _valid_backup_records()
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)
    conn = _FakeConnB(
        current_rows=_current_rows_matching(records),
        final_rows=_final_rows_after_restore(records),
    )

    backfill.restore_from_backup_atomic(
        conn, audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )

    update_sqls = [sql for sql, _ in conn.executed if sql.upper().startswith("UPDATE RAW.SHOPEE_INGESTION_FILE")]
    assert update_sqls
    assert all("SOURCE_METADATA = %S" in sql.upper() for sql in update_sqls)  # WHERE inclui source_metadata


def test_restore_aborta_se_metadata_atual_nao_bate_com_aplicada(tmp_path):
    records = _valid_backup_records()
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    current = _current_rows_matching(records)
    current[0] = (current[0][0], current[0][1], current[0][2], current[0][3], current[0][4], {"period_start": "OUTRO-VALOR"})
    conn = _FakeConnB(current_rows=current)

    result = backfill.restore_from_backup_atomic(
        conn, audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )

    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert conn.updates == []
    assert any("não bate com o valor" in p for p in result.problems)


def test_restore_aborta_se_hash_mudou_desde_o_backfill(tmp_path):
    records = _valid_backup_records()
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    current = _current_rows_matching(records)
    current[0] = (current[0][0], current[0][1], "MUDOU" + "a" * 59, current[0][3], current[0][4], current[0][5])
    conn = _FakeConnB(current_rows=current)

    result = backfill.restore_from_backup_atomic(
        conn, audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )

    assert result.outcome == "aborted_reconciliation_conflict"
    assert any("hash mudou" in p for p in result.problems)


def test_restore_aborta_se_arquivo_adulterado_depois_do_backfill_mudanca_de_filename(tmp_path):
    """'Arquivo adulterado' no sentido do MANIFESTO Raw ter mudado
    (source_filename diferente do registrado no backup) -- detectado na
    revalidação sob o lock, mesmo com o backup íntegro (hash bate)."""
    records = _valid_backup_records()
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    current = _current_rows_matching(records)
    current[0] = (current[0][0], "outro/caminho.csv", current[0][2], current[0][3], current[0][4], current[0][5])
    conn = _FakeConnB(current_rows=current)

    result = backfill.restore_from_backup_atomic(
        conn, audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )

    assert result.outcome == "aborted_reconciliation_conflict"
    assert any("source_filename mudou" in p for p in result.problems)


def test_restore_falha_na_reconciliacao_pos_restore_aborta_mesmo_com_rowcount_1(tmp_path):
    """Defesa em profundidade: mesmo que o UPDATE reporte rowcount=1 para
    todos, uma releitura pós-UPDATE que não bate com source_metadata_before
    ainda aborta tudo."""
    records = _valid_backup_records()
    audit_path = tmp_path / "backup.json"
    expected_sha = _write_backup_and_hash(audit_path, records)

    final_rows = _final_rows_after_restore(records)
    final_rows[0] = (final_rows[0][0], {"ainda": "nao voltou pra null"})
    conn = _FakeConnB(current_rows=_current_rows_matching(records), final_rows=final_rows)

    result = backfill.restore_from_backup_atomic(
        conn, audit_path, confirm_flag=True, confirm_secret_value="1",
        expected_backup_sha256=expected_sha,
    )

    assert result.outcome == "aborted_reconciliation_conflict"
    assert conn.rolled_back is True
    assert conn.committed is False
    assert any("pós-restauração não bate" in p for p in result.problems)
