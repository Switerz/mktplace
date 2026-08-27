"""Testes de pipelines/ops/refresh_tiktok_daily_contract.py — Gate DQ-TK1.

Conexões psycopg2 falsas e sistema de arquivos temporário real. NENHUM banco é
tocado: `_neon_session` e `read_source_snapshot` são sempre substituídos. O
backup é exercitado contra disco de verdade, porque é ali que a integridade
(link atômico, SHA-256, releitura) precisa valer.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pipelines.connectors.tiktok import connector as tk
from pipelines.ops import refresh_tiktok_daily_contract as rf

TZ = ZoneInfo("America/Sao_Paulo")
D_FROM = date(2026, 8, 1)
D_TO = date(2026, 8, 2)
AGORA = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)   # => cutoff 2026-08-02
MARCAS = list(tk.BRANDS_IN_SCOPE)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.connection = conn
        self._pend = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        if isinstance(sql, bytes):
            sql = sql.decode("utf-8")
        self.conn.log.append(("sql", sql, params))
        if self.conn.explode_on and re.search(self.conn.explode_on, sql, re.I | re.S):
            raise RuntimeError("falha injetada pelo teste")
        self._pend = None
        for pat, res in self.conn.rules:
            if re.search(pat, sql, re.I | re.S):
                self._pend = res
                break
        self.rowcount = 0
        for pat, n in self.conn.rowcounts:
            if re.search(pat, sql, re.I | re.S):
                self.rowcount = n
                break

    def mogrify(self, template, args=None):
        txt = template.decode("utf-8") if isinstance(template, bytes) else template
        if args is None:
            return txt.encode("utf-8")
        return (txt % tuple("NULL" if a is None else f"'{a}'"
                            for a in args)).encode("utf-8")

    def fetchone(self):
        r = self._pend
        return (r[0] if r else None) if isinstance(r, list) else r

    def fetchall(self):
        r = self._pend
        return [] if r is None else (r if isinstance(r, list) else [r])

    def close(self):
        self.conn.log.append(("close_cursor", None, None))


class FakeConn:
    def __init__(self, rules=(), rowcounts=(), explode_on=None):
        self.log = []
        self.rules = list(rules)
        self.rowcounts = list(rowcounts)
        self.explode_on = explode_on
        self.encoding = "UTF8"
        self._autocommit = False

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, v):
        self._autocommit = v
        self.log.append(("autocommit", v, None))

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.log.append(("commit", None, None))

    def rollback(self):
        self.log.append(("rollback", None, None))

    def close(self):
        self.log.append(("close", None, None))

    def sqls(self):
        return [s for k, s, _ in self.log if k == "sql"]

    def kinds(self):
        return [k for k, _, _ in self.log]


def _linha_fonte(d: date, brand: str, **over) -> dict:
    base = {
        "date": d, "brand": brand,
        "gmv": Decimal("100.00"), "orders": 4, "avg_ticket": Decimal("25.00"),
        "orders_unknown_status": 0, "orders_commercial_null_amount": 0,
        "content_orders": 6, "units_sold": 5, "unique_buyers": 3,
        "visitors": None, "conversion_rate": None,
        "canceled_orders": 1, "cancel_rate_pct": Decimal("20.00"),
        "returned_orders": None, "refunded_orders": None, "problem_rate": None,
        "delivered_orders": 2, "avg_delivery_hours": Decimal("30.00"),
        "gmv_video": Decimal("10.00"), "gmv_live": Decimal("5.00"),
        "gmv_card": Decimal("1.00"),
        "total_settlement": Decimal("90.00"), "total_fees": Decimal("10.00"),
    }
    base.update(over)
    return base


def _fonte_completa(date_from=D_FROM, date_to=D_TO, **over) -> list[dict]:
    return [_linha_fonte(d, b, **over)
            for d in rf.dias_da_janela(date_from, date_to) for b in MARCAS]


def _snapshot(raw=None, date_from=D_FROM, date_to=D_TO) -> rf.SourceSnapshot:
    raw = _fonte_completa(date_from, date_to) if raw is None else raw
    from pipelines.transforms.tiktok_brand_daily import transform_batch
    return rf.SourceSnapshot(date_from, date_to, raw, transform_batch(raw),
                             {"isolamento": "repeatable read", "read_only": "on"})


def _fact_row(d: date, loja: int, **over) -> dict:
    r = {c: None for c in rf.WRITE_COLUMNS}
    r.update({"date": d, "loja_id": loja, "marketplace_id": 1, "empresa_id": 1,
              "gmv": Decimal("100.00"), "orders": 4, "canceled_orders": 1,
              "units_sold": 5, "gmv_video": Decimal("10.00"),
              "gmv_live": Decimal("5.00"), "gmv_card": Decimal("1.00")})
    r.update(over)
    return r


INGESTED = datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc)


def _backup_row(d: date, loja: int, ident: int, **over) -> dict:
    """Linha de BACKUP: as 46 colunas, com `id` e `ingested_at`."""
    r = _fact_row(d, loja)
    r["id"] = ident
    r["ingested_at"] = INGESTED
    r.update(over)
    return r


def _neon_ok(destino_apos, deleted=10, explode_on=None, destino_backup=None):
    """Conexão falsa. `SELECT id, date...` (46 col) devolve `destino_backup`;
    `SELECT date, loja_id...` (44 col) devolve `destino_apos`."""
    if destino_backup is None:
        destino_backup = []
    return FakeConn(
        rules=[(r"SELECT id, date", destino_backup),
               (r"SELECT date, loja_id", destino_apos),
               (r"pg_advisory_unlock", {"liberado": True}),
               (r"pg_advisory_lock", {"pg_advisory_lock": ""}),
               (r"COUNT\(\*\) AS n", {"n": 0, "fora_mkt": 0, "fora_janela": 0,
                                      "sem_id": 0, "chaves": 0})],
        rowcounts=[(r"^\s*DELETE", deleted), (r"INSERT INTO marts", 10)],
        explode_on=explode_on,
    )


def _wire(monkeypatch, neon, snapshot=None, readonly=None):
    monkeypatch.setattr(rf, "_get_neon_url", lambda: "postgresql://neon")
    monkeypatch.setattr(rf, "_neon_session", lambda url: neon)
    monkeypatch.setattr(rf, "_neon_readonly",
                        lambda url: readonly if readonly is not None else neon)
    if snapshot is not None:
        monkeypatch.setattr(rf, "read_source_snapshot",
                            lambda df, dt: snapshot)


# ===========================================================================
# 21-22. Import e isolamento do pipeline diário
# ===========================================================================

def test_nada_e_executado_no_import():
    """Reimportar o módulo não pode abrir conexão nem escrever."""
    import importlib
    fonte = Path(rf.__file__).read_text(encoding="utf-8")
    arvore = __import__("ast").parse(fonte)
    import ast
    for no in arvore.body:
        assert isinstance(no, (ast.Import, ast.ImportFrom, ast.Assign,
                               ast.AnnAssign, ast.FunctionDef, ast.ClassDef,
                               ast.Expr, ast.If)), type(no).__name__
        if isinstance(no, ast.If):   # somente o guard __main__
            assert "__main__" in ast.unparse(no.test)
    importlib.reload(rf)


def _sqls_gerados() -> list[str]:
    """SQL que o módulo realmente emite, coletado de fakes — não texto-fonte.

    Verificação estrutural em vez de busca textual: a docstring do módulo
    explica o UPSERT do pipeline diário e o `SELECT *` proibido, e um scan de
    texto acusaria a própria explicação.
    """
    conn = _neon_ok([])
    cur = conn.cursor()
    rf._delete_window(cur, D_FROM, D_TO)
    rf.read_target_window(cur, D_FROM, D_TO)
    rf._insert_rows(cur, [_fact_row(D_FROM, 1)])
    return conn.sqls()


def test_nao_aciona_o_pipeline_diario():
    """O refresh é operação separada: não importa o fluxo diário e não upserta."""
    import ast
    arvore = ast.parse(Path(rf.__file__).read_text(encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module)
        elif isinstance(no, ast.Import):
            importados.update(a.name for a in no.names)
    assert not any("daily_performance" in m for m in importados)
    for s in _sqls_gerados():
        assert "ON CONFLICT" not in s.upper()   # apaga e insere, nunca upserta


def test_nunca_usa_select_estrela():
    for s in _sqls_gerados():
        assert not re.search(r"SELECT\s+\*", s, re.I)


# ===========================================================================
# 3. Janela e cutoff
# ===========================================================================

def test_cutoff_e_d_menos_1_em_sao_paulo():
    assert rf.operational_cutoff(AGORA) == date(2026, 8, 2)


def test_cutoff_exige_timezone():
    with pytest.raises(rf.RefreshError, match="fuso explicito"):
        rf.operational_cutoff(datetime(2026, 8, 3, 10, 0))


@pytest.mark.parametrize("delta", [0, 1, 30])
def test_date_to_no_dia_corrente_ou_futuro_e_recusado(delta):
    cutoff = rf.operational_cutoff(AGORA)
    with pytest.raises(rf.RefreshError, match="posterior a D-1"):
        rf.resolve_window(D_FROM, cutoff + timedelta(days=1 + delta), cutoff)


def test_janela_default_nao_expande_o_historico():
    cutoff = date(2026, 8, 2)
    assert rf.resolve_window(None, None, cutoff) == (rf.DEFAULT_DATE_FROM, cutoff)


def test_date_from_anterior_ao_historico_existente_e_recusado():
    """A extensão 2025-10-05..2025-12-25 é decisão separada, não efeito
    colateral de um refresh."""
    with pytest.raises(rf.RefreshError, match="decisao separada"):
        rf.resolve_window(rf.CANDIDATE_EXTENSION[0], date(2026, 1, 1),
                          date(2026, 8, 2))


def test_janela_invertida_e_recusada():
    with pytest.raises(rf.RefreshError, match="janela vazia"):
        rf.resolve_window(date(2026, 2, 1), date(2026, 1, 1), date(2026, 8, 2))


# ===========================================================================
# 4. marketplace_id impossível de desviar
# ===========================================================================

def test_marketplace_id_e_constante_e_nao_flag_do_cli():
    assert rf.TIKTOK_MARKETPLACE_ID == 1
    acoes = {a.dest for a in rf.build_parser()._actions}
    assert "marketplace_id" not in acoes and "marketplace" not in acoes


def test_delete_e_insert_sempre_fixam_marketplace_1():
    conn = _neon_ok([])
    cur = conn.cursor()
    rf._delete_window(cur, D_FROM, D_TO)
    sql, params = [(s, p) for k, s, p in conn.log if k == "sql"][-1]
    assert "marketplace_id = %(mkt)s" in sql
    assert params["mkt"] == 1


def test_linha_canonica_fora_do_tiktok_bloqueia():
    snap = _snapshot()
    ruim = [dict(r) for r in snap.canonical_rows]
    ruim[0]["marketplace_id"] = 3
    snap2 = rf.SourceSnapshot(D_FROM, D_TO, snap.raw_rows, ruim, {})
    with pytest.raises(rf.RefreshError, match="marketplace_id fora do TikTok"):
        rf.assert_source_contract(snap2)


# ===========================================================================
# 5-8. Pré-condições da fonte
# ===========================================================================

def test_fonte_vazia_nao_apaga_o_destino(monkeypatch):
    neon = _neon_ok([])
    _wire(monkeypatch, neon, _snapshot(raw=[]))
    with pytest.raises(rf.RefreshError, match="fonte VAZIA"):
        rf.run_refresh(D_FROM, D_TO, True, "run:1", Path("."), agora=AGORA)
    assert not any("DELETE" in s.upper() for s in neon.sqls())
    assert "commit" not in neon.kinds()


def test_marca_ausente_bloqueia():
    raw = [r for r in _fonte_completa() if r["brand"] != "rituaria"]
    with pytest.raises(rf.RefreshError, match="marcas ausentes"):
        rf.assert_source_contract(_snapshot(raw=raw))


def test_dia_ausente_bloqueia():
    raw = [r for r in _fonte_completa() if r["date"] != D_TO]
    with pytest.raises(rf.RefreshError, match="sem NENHUMA linha"):
        rf.assert_source_contract(_snapshot(raw=raw))


def test_chave_duplicada_na_fonte_bloqueia():
    raw = _fonte_completa()
    raw.append(dict(raw[0]))
    with pytest.raises(rf.RefreshError, match="duplicada"):
        rf.assert_source_contract(_snapshot(raw=raw))


def test_status_desconhecido_bloqueia():
    raw = _fonte_completa()
    raw[0] = dict(raw[0], orders_unknown_status=7)
    with pytest.raises(rf.RefreshError, match="status nulo ou fora"):
        rf.assert_source_contract(_snapshot(raw=raw))


def test_total_amount_nulo_em_pedido_comercial_bloqueia():
    raw = _fonte_completa()
    raw[0] = dict(raw[0], orders_commercial_null_amount=3)
    with pytest.raises(rf.RefreshError, match="total_amount NULO"):
        rf.assert_source_contract(_snapshot(raw=raw))


def test_fonte_limpa_aprova_e_conta():
    provas = rf.assert_source_contract(_snapshot())
    assert provas["linhas"] == 10
    assert provas["marcas"] == sorted(MARCAS)
    assert provas["dias"] == provas["dias_esperados"] == 2


# ===========================================================================
# 1-2. Dry-run e exigências do apply
# ===========================================================================

def test_dry_run_nao_abre_conexao_gravavel(monkeypatch):
    """O dry-run LÊ o destino — em conexão read-only. O que ele não pode é
    abrir conexão gravável, tomar lock ou escrever."""
    def nunca(url):
        raise AssertionError("dry-run nao pode abrir conexao gravavel")

    ro = _neon_ok([])
    monkeypatch.setattr(rf, "_get_neon_url", lambda: "postgresql://neon")
    monkeypatch.setattr(rf, "_neon_session", nunca)
    monkeypatch.setattr(rf, "_neon_readonly", lambda url: ro)
    monkeypatch.setattr(rf, "read_source_snapshot", lambda df, dt: _snapshot())
    rel = rf.run_refresh(D_FROM, D_TO, False, None, None, agora=AGORA)
    assert rel["applied"] is False
    assert rel["resultado"] == "dry-run; nada escrito"
    assert rel["pre_condicoes"]["linhas"] == 10
    assert rel["impacto"]["chaves_inseridas"] == 10   # destino vazio


def test_apply_sem_run_id_e_rejeitado(monkeypatch):
    monkeypatch.setattr(rf, "read_source_snapshot", lambda df, dt: _snapshot())
    with pytest.raises(rf.RefreshError, match="--run-id"):
        rf.run_refresh(D_FROM, D_TO, True, None, Path("."), agora=AGORA)


def test_apply_sem_backup_dir_e_rejeitado(monkeypatch):
    monkeypatch.setattr(rf, "read_source_snapshot", lambda df, dt: _snapshot())
    with pytest.raises(rf.RefreshError, match="--backup-dir"):
        rf.run_refresh(D_FROM, D_TO, True, "run:1", None, agora=AGORA)


def test_cli_dry_run_e_o_default():
    assert rf.build_parser().parse_args([]).apply is False
    assert rf.build_parser().parse_args(["--apply"]).apply is True


# ===========================================================================
# 9-11. Órfãs, escopo do DELETE, isolamento de ML/Shopee
# ===========================================================================

def test_refresh_remove_chaves_orfas(monkeypatch, tmp_path):
    """O UPSERT do diário nunca remove; o refresh apaga a janela antes de
    inserir, então a chave que saiu da fonte desaparece do destino."""
    snap = _snapshot()
    destino_apos = [dict(r) for r in snap.canonical_rows]
    neon = _neon_ok(destino_apos)
    _wire(monkeypatch, neon, snap)
    rel = rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    assert rel["resultado"] == "publicado"
    deletes = [s for s in neon.sqls() if s.strip().upper().startswith("DELETE")]
    assert len(deletes) == 1
    assert "commit" in neon.kinds()


def test_reconciliacao_reprova_chave_orfa_remanescente():
    snap = _snapshot()
    esperado = [dict(r) for r in snap.canonical_rows]
    orfa = _fact_row(D_FROM, 99)
    with pytest.raises(rf.RefreshError, match="orfa"):
        rf.reconcile(esperado + [orfa], esperado, D_FROM, D_TO)


def test_delete_restrito_a_janela(monkeypatch, tmp_path):
    snap = _snapshot()
    destino = [dict(r) for r in snap.canonical_rows]
    neon = _neon_ok(destino)
    _wire(monkeypatch, neon, snap)
    rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    d = next((s, p) for k, s, p in neon.log
             if k == "sql" and s.strip().upper().startswith("DELETE"))
    assert "date BETWEEN %(df)s AND %(dt)s" in d[0]
    assert d[1]["df"] == D_FROM and d[1]["dt"] == D_TO


def test_ml_e_shopee_nunca_sao_alcancados(monkeypatch, tmp_path):
    """Nenhum SQL de escrita pode existir sem o filtro de marketplace."""
    snap = _snapshot()
    destino = [dict(r) for r in snap.canonical_rows]
    neon = _neon_ok(destino)
    _wire(monkeypatch, neon, snap)
    rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    for k, s, p in neon.log:
        if k != "sql":
            continue
        if s.strip().upper().startswith("DELETE"):
            assert p and p.get("mkt") == 1
    # e nenhuma linha inserida fora do TikTok
    assert all(r["marketplace_id"] == 1 for r in snap.canonical_rows)


def test_linha_fora_da_janela_no_destino_reprova():
    esperado = [_fact_row(D_FROM, 1)]
    fora = _fact_row(D_FROM - timedelta(days=1), 1)
    with pytest.raises(rf.RefreshError, match="fora da janela"):
        rf.reconcile(esperado + [fora], esperado, D_FROM, D_TO)


# ===========================================================================
# 12-14. Rollback
# ===========================================================================

def test_falha_depois_do_delete_faz_rollback(monkeypatch, tmp_path):
    snap = _snapshot()
    neon = _neon_ok([], explode_on=r"INSERT INTO marts")
    _wire(monkeypatch, neon, snap)
    with pytest.raises(RuntimeError, match="falha injetada"):
        rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    assert "rollback" in neon.kinds()
    assert "commit" not in neon.kinds()


def test_falha_no_delete_faz_rollback(monkeypatch, tmp_path):
    snap = _snapshot()
    neon = _neon_ok([], explode_on=r"^\s*DELETE")
    _wire(monkeypatch, neon, snap)
    with pytest.raises(RuntimeError, match="falha injetada"):
        rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    assert "rollback" in neon.kinds()
    assert "commit" not in neon.kinds()


def test_divergencia_na_reconciliacao_faz_rollback(monkeypatch, tmp_path):
    """Destino publicado com valor diferente do esperado: rollback integral."""
    snap = _snapshot()
    destino = [dict(r) for r in snap.canonical_rows]
    destino[0] = dict(destino[0], gmv=Decimal("999.99"))
    neon = _neon_ok(destino)
    _wire(monkeypatch, neon, snap)
    with pytest.raises(rf.RefreshError, match="reconciliacao reprovou"):
        rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    assert "rollback" in neon.kinds()
    assert "commit" not in neon.kinds()


def test_reconciliacao_confere_agregados_decimal():
    esperado = [_fact_row(D_FROM, 1, gmv=Decimal("10.10")),
                _fact_row(D_FROM, 2, gmv=Decimal("0.90"))]
    prova = rf.reconcile(list(esperado), esperado, D_FROM, D_TO)
    assert prova["agregados"]["gmv"] == "11.00"
    assert prova["except_bidirecional"] == (0, 0)


def test_reconciliacao_nao_confunde_representacao_decimal():
    """`Decimal("1.10")` e `Decimal("1.1")` são o mesmo valor; comparar por
    string acusaria divergência falsa."""
    a = [_fact_row(D_FROM, 1, gmv=Decimal("1.10"))]
    b = [_fact_row(D_FROM, 1, gmv=Decimal("1.1"))]
    rf.reconcile(a, b, D_FROM, D_TO)


# ===========================================================================
# 15. Advisory lock
# ===========================================================================

def test_advisory_lock_e_adquirido_e_liberado(monkeypatch, tmp_path):
    snap = _snapshot()
    destino = [dict(r) for r in snap.canonical_rows]
    neon = _neon_ok(destino)
    _wire(monkeypatch, neon, snap)
    rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    sqls = neon.sqls()
    locks = [s for s in sqls if "pg_advisory_lock(" in s]
    unlocks = [s for s in sqls if "pg_advisory_unlock" in s]
    assert len(locks) == 1 and len(unlocks) == 1
    assert sqls.index(locks[0]) < sqls.index(unlocks[0])
    assert any("lock_timeout" in s for s in sqls)


def test_lock_liberado_mesmo_em_falha(monkeypatch, tmp_path):
    snap = _snapshot()
    neon = _neon_ok([], explode_on=r"INSERT INTO marts")
    _wire(monkeypatch, neon, snap)
    with pytest.raises(RuntimeError):
        rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    assert any("pg_advisory_unlock" in s for s in neon.sqls())


def test_release_advisory_lock_nunca_levanta():
    class Morta:
        def cursor(self):
            raise RuntimeError("conexao morta")

    assert rf.release_advisory_lock(Morta()) is False


# ===========================================================================
# 16-18. Backup e restore
# ===========================================================================

def _backup_rows():
    """Linhas de backup: 46 colunas, com id e ingested_at."""
    return [_backup_row(D_FROM, 1, 101, gmv=Decimal("10.50")),
            _backup_row(D_FROM, 2, 102, gmv=None, orders=None)]


def test_backup_grava_csv_manifesto_e_sha(tmp_path):
    prova = rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    for nome in (rf.BACKUP_CSV_NAME, rf.BACKUP_MANIFEST_NAME, rf.BACKUP_SUMS_NAME):
        assert (tmp_path / nome).is_file()
    assert prova["row_count"] == 2
    man = json.loads((tmp_path / rf.BACKUP_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert man["marketplace_id"] == 1
    assert man["columns"] == list(rf.BACKUP_COLUMNS)
    assert man["aggregates"]["gmv"] == "10.50"


def test_backup_nao_sobrescreve(tmp_path):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    with pytest.raises(rf.BackupIntegrityError, match="ja existe"):
        rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:2")


def test_backup_em_diretorio_inexistente_bloqueia(tmp_path):
    with pytest.raises(rf.BackupIntegrityError, match="nao existe"):
        rf.write_backup(tmp_path / "nao_existe", D_FROM, D_TO, _backup_rows(), "r")


def test_backup_ausente_ou_incompleto_bloqueia(tmp_path):
    with pytest.raises(rf.BackupIntegrityError, match="ausente"):
        rf.load_backup(tmp_path)
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    (tmp_path / rf.BACKUP_SUMS_NAME).unlink()
    with pytest.raises(rf.BackupIntegrityError, match="incompleto"):
        rf.load_backup(tmp_path)


def test_csv_adulterado_bloqueia_restore(tmp_path):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    csv_path = tmp_path / rf.BACKUP_CSV_NAME
    texto = csv_path.read_text(encoding="utf-8").replace("10.50", "99.50")
    csv_path.write_text(texto, encoding="utf-8")
    with pytest.raises(rf.BackupIntegrityError, match="SHA-256"):
        rf.load_backup(tmp_path)


def test_manifesto_adulterado_bloqueia_restore(tmp_path):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    man_path = tmp_path / rf.BACKUP_MANIFEST_NAME
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man["row_count"] = 99
    man_path.write_text(json.dumps(man), encoding="utf-8")
    with pytest.raises(rf.BackupIntegrityError):
        rf.load_backup(tmp_path)


def test_sha_companion_adulterado_bloqueia_restore(tmp_path):
    """Companion com os dois nomes certos mas digests falsos: reprova no CSV."""
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    (tmp_path / rf.BACKUP_SUMS_NAME).write_text(
        "0" * 64 + f"  {rf.BACKUP_CSV_NAME}\n"
        + "0" * 64 + f"  {rf.BACKUP_MANIFEST_NAME}\n", encoding="utf-8")
    with pytest.raises(rf.BackupIntegrityError, match="divergiu"):
        rf.load_backup(tmp_path)


def test_backup_recupera_exatamente_o_snapshot(tmp_path):
    """Ida e volta byte-a-valor: Decimal volta Decimal, None volta None."""
    original = _backup_rows()
    rf.write_backup(tmp_path, D_FROM, D_TO, original, "run:1")
    volta = rf.load_backup(tmp_path)["records"]
    assert len(volta) == len(original)
    for o, v in zip(original, volta):
        for c in rf.WRITE_COLUMNS:
            assert v[c] == o[c], c
            assert (v[c] is None) == (o[c] is None), c


def test_null_permanece_null_nunca_zero(tmp_path):
    linha = _backup_row(D_FROM, 1, 501, gmv=None, orders=None, canceled_orders=None)
    rf.write_backup(tmp_path, D_FROM, D_TO, [linha], "run:1")
    volta = rf.load_backup(tmp_path)["records"][0]
    assert volta["gmv"] is None and volta["orders"] is None
    assert volta["canceled_orders"] is None
    man = json.loads((tmp_path / rf.BACKUP_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert man["aggregates"]["gmv"] is None      # ausencia nao virou 0


def test_restore_dry_run_valida_e_nao_escreve(tmp_path, monkeypatch):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")

    def nunca(url):
        raise AssertionError("restore dry-run nao pode abrir conexao")

    monkeypatch.setattr(rf, "_neon_session", nunca)
    rel = rf.run_restore(tmp_path, False, None)
    assert rel["applied"] is False
    assert "nada escrito" in rel["resultado"]
    assert rel["row_count"] == 2


def test_restore_apply_sem_run_id_e_rejeitado(tmp_path):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    with pytest.raises(rf.RefreshError, match="--run-id"):
        rf.run_restore(tmp_path, True, None)


def test_restore_usa_staging_temporaria_e_valida_antes_do_fato(tmp_path, monkeypatch):
    rows = _backup_rows()
    rf.write_backup(tmp_path, D_FROM, D_TO, rows, "run:1")
    backup = rf.load_backup(tmp_path)
    neon = FakeConn(
        # o restore relê as 46 colunas (`SELECT id, date...`), não as 44
        rules=[(r"SELECT id, date", [dict(r) for r in rows]),
               (r"COUNT\(\*\) AS n", {"n": 2, "fora_mkt": 0, "fora_janela": 0,
                                      "sem_id": 0, "chaves": 2}),
               (r"pg_advisory_unlock", {"liberado": True}),
               (r"pg_advisory_lock", {"pg_advisory_lock": ""})],
        rowcounts=[(r"^\s*DELETE", 2), (r"INSERT INTO marts", 2)],
    )
    cur = neon.cursor()
    prova = rf.restore_in_transaction(cur, backup)
    sqls = neon.sqls()
    criacao = next(s for s in sqls if "CREATE TEMP TABLE" in s)
    assert "ON COMMIT DROP" in criacao
    i_valida = next(i for i, s in enumerate(sqls) if "COUNT(*) AS n" in s)
    i_delete = next(i for i, s in enumerate(sqls)
                    if s.strip().upper().startswith("DELETE"))
    assert i_valida < i_delete           # valida a staging ANTES de tocar o fato
    assert prova["reconciliacao"]["linhas"] == 2


def test_restore_reprova_staging_com_chave_duplicada(tmp_path):
    rows = _backup_rows()
    rf.write_backup(tmp_path, D_FROM, D_TO, rows, "run:1")
    backup = rf.load_backup(tmp_path)
    neon = FakeConn(rules=[(r"COUNT\(\*\) AS n",
                            {"n": 2, "fora_mkt": 0, "fora_janela": 0, "sem_id": 0, "chaves": 1})])
    with pytest.raises(rf.BackupIntegrityError, match="chave duplicada"):
        rf.restore_in_transaction(neon.cursor(), backup)


def test_restore_reprova_staging_com_outro_marketplace(tmp_path):
    rows = _backup_rows()
    rf.write_backup(tmp_path, D_FROM, D_TO, rows, "run:1")
    backup = rf.load_backup(tmp_path)
    neon = FakeConn(rules=[(r"COUNT\(\*\) AS n",
                            {"n": 2, "fora_mkt": 1, "fora_janela": 0, "sem_id": 0, "chaves": 2})])
    with pytest.raises(rf.BackupIntegrityError, match="outro marketplace"):
        rf.restore_in_transaction(neon.cursor(), backup)


def test_backup_nao_contem_pii(tmp_path):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    texto = (tmp_path / rf.BACKUP_CSV_NAME).read_text(encoding="utf-8")
    texto += (tmp_path / rf.BACKUP_MANIFEST_NAME).read_text(encoding="utf-8")
    # `unique_buyers`/`new_buyers`/`repeat_buyers` são NOMES DE COLUNA agregada,
    # não dado pessoal — por isso "buyer" não entra na lista de proibidos.
    for proibido in ("order_id", "cpf", "email", "telefone", "endereco",
                     "postgresql://", "password", "token"):
        assert proibido not in texto.lower()


# ===========================================================================
# 20. Erros sanitizados
# ===========================================================================

def test_erro_do_cli_nao_expoe_topologia(monkeypatch, capsys):
    def explode(*a, **kw):
        raise RuntimeError(
            'connection to server at "10.7.7.7", port 5432 failed: timeout')

    monkeypatch.setattr(rf, "run_refresh", explode)
    assert rf.main(["--date-from", "2026-08-01"]) == 2
    err = capsys.readouterr().err
    for proibido in ("10.7.7.7", "5432", "postgresql://"):
        assert proibido not in err


def test_modulo_nao_imprime_dsn_nem_env():
    fonte = Path(rf.__file__).read_text(encoding="utf-8")
    assert not re.search(r"print\([^)]*url", fonte, re.I)
    assert not re.search(r"print\([^)]*DATABASE_URL", fonte)


def test_modulo_nao_tem_retry_sleep_nem_backoff():
    import ast
    arvore = ast.parse(Path(rf.__file__).read_text(encoding="utf-8"))
    chamados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            f = no.func
            chamados.add(f.attr if isinstance(f, ast.Attribute)
                         else getattr(f, "id", ""))
    for nome in chamados:
        baixo = nome.lower()
        assert "sleep" not in baixo and "retry" not in baixo, nome
    assert not [n for n in ast.walk(arvore) if isinstance(n, ast.While)]


# ===========================================================================
# Contrato reutilizado, não redefinido
# ===========================================================================

def test_contrato_comercial_vem_do_connector():
    fonte = Path(rf.__file__).read_text(encoding="utf-8")
    assert "tk.QUERY" in fonte
    assert "tk.COMMERCIAL_ORDER_STATUSES" in fonte
    assert "tk.KNOWN_ORDER_STATUSES" in fonte
    assert "tk.BRANDS_IN_SCOPE" in fonte
    # Nenhuma segunda definição de GMV: o módulo não emite agregação própria
    # sobre a fonte. Verificado no SQL EMITIDO, não no texto — a docstring
    # descreve `SUM(total_amount)` para explicar de onde o número vem.
    for s in _sqls_gerados():
        assert not re.search(r"\bSUM\s*\(", s, re.I)
        assert "total_amount" not in s.lower()


def test_colunas_canonicas_batem_com_o_pipeline_diario():
    """Se o diário passar a escrever outra coluna, este teste quebra — é o
    ponto: as duas listas não podem divergir em silêncio."""
    diario = Path(rf.__file__).parent.parent / "ingestion" / "daily_performance.py"
    texto = diario.read_text(encoding="utf-8")
    bloco = texto.split("INSERT INTO marts.fact_marketplace_daily_performance (")[1]
    bloco = bloco.split(") VALUES")[0]
    colunas = [c.strip() for c in bloco.replace("\n", " ").split(",") if c.strip()]
    assert colunas == list(rf.WRITE_COLUMNS)


# ===========================================================================
# FINDING 1 — backup restaura o estado EXATO (46 colunas)
# ===========================================================================

def _reassinar(tmp_path):
    """Recalcula o companion a partir dos arquivos atuais.

    Serve para isolar a camada semântica da camada de hash: com o companion
    recalculado, os hashes deixam de acusar, e o que sobra é a recomputação.
    Não modela um adversário — apenas remove a proteção de integridade para que o
    teste meça a validação semântica isoladamente.
    """
    import hashlib

    def h(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    (tmp_path / rf.BACKUP_SUMS_NAME).unlink()
    (tmp_path / rf.BACKUP_SUMS_NAME).write_text(
        f"{h(tmp_path / rf.BACKUP_CSV_NAME)}  {rf.BACKUP_CSV_NAME}\n"
        f"{h(tmp_path / rf.BACKUP_MANIFEST_NAME)}  {rf.BACKUP_MANIFEST_NAME}\n",
        encoding="utf-8")


def test_write_columns_sao_44_e_backup_columns_sao_46():
    assert len(rf.WRITE_COLUMNS) == 44
    assert len(rf.BACKUP_COLUMNS) == 46
    assert rf.BACKUP_COLUMNS[0] == "id"
    assert rf.BACKUP_COLUMNS[-1] == "ingested_at"
    assert set(rf.BACKUP_COLUMNS) - set(rf.WRITE_COLUMNS) == {"id", "ingested_at"}


def test_refresh_insere_somente_as_44_canonicas():
    """`id` continua vindo da sequence e `ingested_at` do DEFAULT, igual ao
    pipeline diário — o refresh não os fabrica."""
    conn = _neon_ok([])
    rf._insert_rows(conn.cursor(), [_fact_row(D_FROM, 1)], rf.WRITE_COLUMNS)
    sql = conn.sqls()[0]
    lista = [c.strip() for c in sql.split("(")[1].split(")")[0].split(",")]
    assert "id" not in lista
    assert "ingested_at" not in lista
    assert len(lista) == 44


def test_backup_le_as_46_colunas_do_destino():
    conn = _neon_ok([], destino_backup=[])
    rf.read_target_window(conn.cursor(), D_FROM, D_TO, rf.BACKUP_COLUMNS)
    sql = conn.sqls()[0]
    assert sql.startswith("SELECT id, date")
    assert "ingested_at" in sql
    assert not re.search(r"SELECT\s+\*", sql, re.I)


def test_backup_ida_e_volta_preserva_tudo(tmp_path):
    """id, ingested_at com fuso, source_updated_at, Decimal, inteiros e nulls."""
    src = datetime(2026, 8, 2, 23, 30, tzinfo=timezone.utc)
    original = [
        _backup_row(D_FROM, 1, 101, gmv=Decimal("10.50"), orders=7,
                    source_updated_at=src, avg_ticket=Decimal("1.50")),
        _backup_row(D_TO, 2, 202, gmv=None, orders=None, source_updated_at=None),
    ]
    rf.write_backup(tmp_path, D_FROM, D_TO, original, "run:1")
    volta = rf.load_backup(tmp_path, D_FROM, D_TO)["records"]
    assert [r["id"] for r in volta] == [101, 202]
    for o, v in zip(original, volta):
        for c in rf.BACKUP_COLUMNS:
            assert v[c] == o[c], c
            assert (v[c] is None) == (o[c] is None), c
            assert type(v[c]) is type(o[c]), c
    assert volta[0]["ingested_at"].tzinfo is not None
    assert volta[0]["ingested_at"] == INGESTED
    assert volta[0]["source_updated_at"] == src


def test_restore_insere_id_e_ingested_at_explicitamente(tmp_path):
    rows = _backup_rows()
    rf.write_backup(tmp_path, D_FROM, D_TO, rows, "run:1")
    backup = rf.load_backup(tmp_path)
    neon = FakeConn(
        rules=[(r"SELECT id, date", [dict(r) for r in rows]),
               (r"COUNT\(\*\) AS n", {"n": 2, "fora_mkt": 0, "fora_janela": 0,
                                      "sem_id": 0, "chaves": 2})],
        rowcounts=[(r"^\s*DELETE", 2), (r"INSERT INTO marts", 2)],
    )
    rf.restore_in_transaction(neon.cursor(), backup)
    ins = [s for s in neon.sqls() if s.startswith("INSERT INTO marts")]
    assert ins and "id" in ins[0] and "ingested_at" in ins[0]


def test_restore_nao_executa_setval(tmp_path):
    """A sequence já avançou; fazê-la retroceder arriscaria colisão futura.

    Verificado no SQL EMITIDO — a docstring do módulo menciona `setval` para
    explicar a ausência, e um scan de texto acusaria a própria explicação."""
    rows = _backup_rows()
    rf.write_backup(tmp_path, D_FROM, D_TO, rows, "run:1")
    backup = rf.load_backup(tmp_path)
    neon = FakeConn(
        rules=[(r"SELECT id, date", [dict(r) for r in rows]),
               (r"COUNT\(\*\) AS n", {"n": 2, "fora_mkt": 0, "fora_janela": 0,
                                      "sem_id": 0, "chaves": 2})],
        rowcounts=[(r"^\s*DELETE", 2), (r"INSERT INTO marts", 2)],
    )
    rf.restore_in_transaction(neon.cursor(), backup)
    for s in neon.sqls():
        assert "setval" not in s.lower()
        assert "nextval" not in s.lower()


def test_restore_reprova_se_id_nao_voltar(tmp_path):
    rows = _backup_rows()
    rf.write_backup(tmp_path, D_FROM, D_TO, rows, "run:1")
    backup = rf.load_backup(tmp_path)
    adulterado = [dict(r) for r in rows]
    adulterado[0]["id"] = 999
    neon = FakeConn(
        rules=[(r"SELECT id, date", adulterado),
               (r"COUNT\(\*\) AS n", {"n": 2, "fora_mkt": 0, "fora_janela": 0,
                                      "sem_id": 0, "chaves": 2})],
        rowcounts=[(r"^\s*DELETE", 2), (r"INSERT INTO marts", 2)],
    )
    with pytest.raises(rf.RefreshError, match="nao preservou id/ingested_at"):
        rf.restore_in_transaction(neon.cursor(), backup)


def test_csv_sem_id_reprova(tmp_path):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    csv_path = tmp_path / rf.BACKUP_CSV_NAME
    linhas = csv_path.read_text(encoding="utf-8").splitlines()
    linhas[1] = "," + linhas[1].split(",", 1)[1]
    csv_path.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    man_path = tmp_path / rf.BACKUP_MANIFEST_NAME
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man["csv_sha256"] = __import__("hashlib").sha256(
        csv_path.read_bytes()).hexdigest()
    man_path.write_text(json.dumps(man), encoding="utf-8")
    _reassinar(tmp_path)
    with pytest.raises(rf.BackupIntegrityError, match="sem id"):
        rf.load_backup(tmp_path)


# ===========================================================================
# FINDING 2 — SHA256SUMS cobre os dois arquivos; janela vem do CLI
# ===========================================================================

def test_sums_cobre_csv_e_manifesto(tmp_path):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    linhas = (tmp_path / rf.BACKUP_SUMS_NAME).read_text(
        encoding="utf-8").splitlines()
    assert {l.split()[1] for l in linhas if l.strip()} == {
        rf.BACKUP_CSV_NAME, rf.BACKUP_MANIFEST_NAME}


@pytest.mark.parametrize("campo,valor", [
    ("date_from", "2025-12-26"),
    ("date_to", "2026-12-31"),
    ("row_count", 99),
])
def test_manifesto_adulterado_reprova_com_csv_intacto(tmp_path, campo, valor):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    man_path = tmp_path / rf.BACKUP_MANIFEST_NAME
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man[campo] = valor
    man_path.write_text(json.dumps(man), encoding="utf-8")
    with pytest.raises(rf.BackupIntegrityError, match="manifesto adulterado"):
        rf.load_backup(tmp_path)


def test_agregado_adulterado_reprova_mesmo_com_hashes_recalculados(tmp_path):
    """Prova ESTREITA, e vale declarar o limite: alterar o agregado do manifesto
    e recalcular os hashes continua sendo detectado, porque o agregado é
    RECOMPUTADO a partir do CSV em vez de aceito do manifesto.

    NÃO prova resistência a um adversário que reescreva todos os artefatos de
    forma coerente — quem editasse o CSV, recalculasse os agregados no manifesto
    e refizesse o `SHA256SUMS` passaria por esta camada. `SHA256SUMS` dá
    integridade, não autenticidade (sem assinatura, sem HMAC).
    """
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    man_path = tmp_path / rf.BACKUP_MANIFEST_NAME
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man["aggregates"]["gmv"] = "999.99"
    man_path.write_text(json.dumps(man), encoding="utf-8")
    _reassinar(tmp_path)
    with pytest.raises(rf.BackupIntegrityError, match="agregado gmv"):
        rf.load_backup(tmp_path)


@pytest.mark.parametrize("conteudo,erro", [
    ("", "exatamente"),
    ("deadbeef  x\n", "malformado"),
    ("{h}  tk_daily_backup.csv\n", "exatamente"),
    ("{h}  tk_daily_backup.csv\n{h}  tk_daily_backup.csv\n", "duplicada"),
    ("{h}  tk_daily_backup.csv\n{h}  tk_daily_backup_manifest.json\n"
     "{h}  extra.txt\n", "exatamente"),
])
def test_companion_incompleto_extra_ou_duplicado_reprova(tmp_path, conteudo, erro):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    (tmp_path / rf.BACKUP_SUMS_NAME).unlink()
    (tmp_path / rf.BACKUP_SUMS_NAME).write_text(
        conteudo.replace("{h}", "0" * 64), encoding="utf-8")
    with pytest.raises(rf.BackupIntegrityError, match=erro):
        rf.load_backup(tmp_path)


def test_restore_apply_exige_janela_explicita(tmp_path):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    with pytest.raises(rf.RefreshError, match="exige --date-from e --date-to"):
        rf.run_restore(tmp_path, True, "run:1")
    with pytest.raises(rf.RefreshError, match="exige --date-from e --date-to"):
        rf.run_restore(tmp_path, True, "run:1", date_from=D_FROM)


@pytest.mark.parametrize("df,dt", [
    (date(2026, 7, 1), D_TO),
    (D_FROM, date(2026, 9, 1)),
])
def test_janela_do_cli_divergente_do_manifesto_reprova(tmp_path, df, dt, monkeypatch):
    rf.write_backup(tmp_path, D_FROM, D_TO, _backup_rows(), "run:1")
    monkeypatch.setattr(rf, "_neon_session",
                        lambda url: pytest.fail("nao pode conectar"))
    with pytest.raises(rf.BackupIntegrityError, match="nao coincide com o manifesto"):
        rf.run_restore(tmp_path, True, "run:1", date_from=df, date_to=dt)


def test_cli_repassa_janela_no_modo_restore(monkeypatch, tmp_path):
    visto = {}

    def fake(d, a, r, df=None, dt=None):
        visto.update({"df": df, "dt": dt})
        return {"ok": True}

    monkeypatch.setattr(rf, "run_restore", fake)
    rf.main(["--mode", "restore", "--backup-dir", str(tmp_path),
             "--date-from", "2026-08-01", "--date-to", "2026-08-02"])
    assert visto == {"df": D_FROM, "dt": D_TO}


# ===========================================================================
# FINDING 3 — backup sob transação + lock de tabela, antes do primeiro DML
# ===========================================================================

def _classificar(sql: str):
    if "pg_advisory_unlock" in sql:
        return "unlock"
    if "pg_advisory_lock(" in sql:
        return "advisory"
    if sql.strip().upper().startswith("LOCK TABLE"):
        return "lock_tabela"
    if sql.startswith("SELECT id, date"):
        return "le_estado_anterior"
    if sql.strip().upper().startswith("DELETE"):
        return "delete"
    if sql.startswith("INSERT INTO marts"):
        return "insert"
    if sql.startswith("SELECT date, loja_id"):
        return "reconcilia"
    return None


def _ordem(neon):
    out = []
    for k, s, _ in neon.log:
        if k == "sql":
            n = _classificar(s)
            if n:
                out.append(n)
        elif k == "autocommit":
            out.append("begin" if s is False else "autocommit_on")
        elif k in ("commit", "rollback"):
            out.append(k)
    return out


def test_ordem_transacional_do_apply(monkeypatch, tmp_path):
    snap = _snapshot()
    neon = _neon_ok([dict(r) for r in snap.canonical_rows],
                    destino_backup=[_backup_row(D_FROM, 1, 1)])
    _wire(monkeypatch, neon, snap)
    rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    o = _ordem(neon)
    esperada = ["advisory", "begin", "lock_tabela", "le_estado_anterior",
                "delete", "insert", "reconcilia", "commit", "unlock"]
    pos = [o.index(e) for e in esperada]
    assert pos == sorted(pos), o
    assert (tmp_path / rf.BACKUP_CSV_NAME).is_file()
    assert o.index("le_estado_anterior") < o.index("delete")


def test_lock_de_tabela_e_share_row_exclusive(monkeypatch, tmp_path):
    """Menor nível que bloqueia INSERT/UPDATE/DELETE e preserva SELECT."""
    assert rf.TABLE_LOCK_MODE == "SHARE ROW EXCLUSIVE"
    snap = _snapshot()
    neon = _neon_ok([dict(r) for r in snap.canonical_rows],
                    destino_backup=[_backup_row(D_FROM, 1, 1)])
    _wire(monkeypatch, neon, snap)
    rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    lock = next(s for s in neon.sqls() if s.strip().upper().startswith("LOCK TABLE"))
    assert "SHARE ROW EXCLUSIVE MODE" in lock
    assert "ACCESS EXCLUSIVE" not in lock


def test_falha_do_backup_nao_produz_nenhum_dml(monkeypatch, tmp_path):
    """Diretório de backup inexistente: zero DELETE, zero INSERT, rollback."""
    snap = _snapshot()
    neon = _neon_ok([], destino_backup=[_backup_row(D_FROM, 1, 1)])
    _wire(monkeypatch, neon, snap)
    with pytest.raises(rf.BackupIntegrityError):
        rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path / "nao_existe",
                       agora=AGORA)
    o = _ordem(neon)
    assert "delete" not in o and "insert" not in o
    assert "rollback" in o and "commit" not in o
    assert "unlock" in o


def test_restore_tambem_adquire_lock_de_tabela(monkeypatch, tmp_path):
    rows = _backup_rows()
    rf.write_backup(tmp_path, D_FROM, D_TO, rows, "run:1")
    neon = FakeConn(
        rules=[(r"SELECT id, date", [dict(r) for r in rows]),
               (r"COUNT\(\*\) AS n", {"n": 2, "fora_mkt": 0, "fora_janela": 0,
                                      "sem_id": 0, "chaves": 2}),
               (r"pg_advisory_unlock", {"liberado": True}),
               (r"pg_advisory_lock", {"pg_advisory_lock": ""})],
        rowcounts=[(r"^\s*DELETE", 2), (r"INSERT INTO marts", 2)],
    )
    _wire(monkeypatch, neon)
    rf.run_restore(tmp_path, True, "run:1", D_FROM, D_TO)
    o = _ordem(neon)
    assert o.index("advisory") < o.index("begin") < o.index("lock_tabela")
    assert o.index("lock_tabela") < o.index("delete")


def test_nao_abre_segunda_conexao_gravavel(monkeypatch, tmp_path):
    snap = _snapshot()
    neon = _neon_ok([dict(r) for r in snap.canonical_rows],
                    destino_backup=[_backup_row(D_FROM, 1, 1)])
    aberturas = {"n": 0}

    def contar(url):
        aberturas["n"] += 1
        return neon

    monkeypatch.setattr(rf, "_get_neon_url", lambda: "postgresql://neon")
    monkeypatch.setattr(rf, "_neon_session", contar)
    monkeypatch.setattr(rf, "_neon_readonly",
                        lambda url: pytest.fail("apply nao usa conexao read-only"))
    monkeypatch.setattr(rf, "read_source_snapshot", lambda df, dt: snap)
    rf.run_refresh(D_FROM, D_TO, True, "run:1", tmp_path, agora=AGORA)
    assert aberturas["n"] == 1


# ===========================================================================
# FINDING 4 — dry-run lê fonte E destino e mostra o impacto
# ===========================================================================

def test_dry_run_le_o_destino_em_conexao_read_only(monkeypatch):
    snap = _snapshot()
    ro = _neon_ok([_fact_row(D_FROM, 1)])
    monkeypatch.setattr(rf, "_get_neon_url", lambda: "postgresql://neon")
    monkeypatch.setattr(rf, "_neon_readonly", lambda url: ro)
    monkeypatch.setattr(rf, "_neon_session",
                        lambda url: pytest.fail("dry-run nao abre conexao gravavel"))
    monkeypatch.setattr(rf, "read_source_snapshot", lambda df, dt: snap)
    rel = rf.run_refresh(D_FROM, D_TO, False, None, None, agora=AGORA)
    assert rel["resultado"] == "dry-run; nada escrito"
    assert "impacto" in rel
    assert any(s.startswith("SELECT date, loja_id") for s in ro.sqls())
    for s in ro.sqls():
        assert "pg_advisory" not in s
        assert not s.strip().upper().startswith(
            ("LOCK TABLE", "DELETE", "INSERT", "UPDATE", "CREATE"))
    assert "commit" not in ro.kinds()


def test_dry_run_nao_grava_backup(monkeypatch, tmp_path):
    snap = _snapshot()
    ro = _neon_ok([])
    monkeypatch.setattr(rf, "_get_neon_url", lambda: "postgresql://neon")
    monkeypatch.setattr(rf, "_neon_readonly", lambda url: ro)
    monkeypatch.setattr(rf, "_neon_session", lambda url: pytest.fail("gravavel"))
    monkeypatch.setattr(rf, "read_source_snapshot", lambda df, dt: snap)
    rf.run_refresh(D_FROM, D_TO, False, None, tmp_path, agora=AGORA)
    assert list(tmp_path.iterdir()) == []


def test_dry_run_fecha_as_quatro_classes_de_chave():
    """inseridas + removidas + alteradas + inalteradas == união das chaves."""
    atual = [_fact_row(D_FROM, 1),
             _fact_row(D_FROM, 2, gmv=Decimal("1.00")),
             _fact_row(D_FROM, 3)]
    novo = [_fact_row(D_FROM, 1),
            _fact_row(D_FROM, 2, gmv=Decimal("2.00")),
            _fact_row(D_FROM, 4)]
    imp = rf.compute_impact(atual, novo, D_FROM, D_TO)
    assert imp["chaves_inseridas"] == 1
    assert imp["chaves_removidas_orfas"] == 1
    assert imp["chaves_alteradas"] == 1
    assert imp["chaves_inalteradas"] == 1
    assert imp["soma_das_quatro_classes"] == imp["uniao_das_chaves"] == 4
    assert imp["linhas_atuais"] == 3 and imp["linhas_novas"] == 3


def test_dry_run_reporta_deltas_e_quebras():
    atual = [_fact_row(D_FROM, 1, gmv=Decimal("100.00"), orders=4)]
    novo = [_fact_row(D_FROM, 1, gmv=Decimal("150.00"), orders=5)]
    imp = rf.compute_impact(atual, novo, D_FROM, D_TO)
    assert imp["agregados_antigos"]["gmv"] == "100.00"
    assert imp["agregados_novos"]["gmv"] == "150.00"
    assert imp["deltas"]["gmv"] == "50.00"
    assert imp["deltas"]["orders"] == 1
    assert imp["por_mes_novo"]["2026-08"]["avg_ticket"] == "30.00"
    assert imp["por_marca_novo"]["apice"]["gmv"] == "150.00"
    assert imp["cobertura_atual"]["min_date"] == D_FROM.isoformat()


def test_dry_run_mantem_null_distinto_de_zero():
    atual = [_fact_row(D_FROM, 1, gmv=None)]
    novo = [_fact_row(D_FROM, 1, gmv=None)]
    imp = rf.compute_impact(atual, novo, D_FROM, D_TO)
    assert imp["agregados_antigos"]["gmv"] is None
    assert imp["agregados_novos"]["gmv"] is None
    assert imp["deltas"]["gmv"] is None
    assert imp["chaves_alteradas"] == 0


def test_dry_run_conta_combinacoes_ausentes_sem_fabricar_linha():
    """Ausência de marca×dia é reportada, nunca preenchida com linha zero."""
    imp = rf.compute_impact([], [_fact_row(D_FROM, 1)], D_FROM, D_TO)
    assert imp["combinacoes_marca_dia_ausentes"] == 9   # 5 marcas x 2 dias - 1
    assert imp["linhas_novas"] == 1
    assert "NAO e' preenchida com linha zero" in imp["nota_combinacoes"]


def test_precondicoes_nao_exigem_produto_cartesiano():
    """Uma marca sem venda num dia não bloqueia: pode ser ausência legítima."""
    raw = [r for r in _fonte_completa()
           if not (r["date"] == D_TO and r["brand"] == "rituaria")]
    provas = rf.assert_source_contract(_snapshot(raw=raw))
    assert provas["combinacoes_marca_dia_ausentes"] == 1
    assert provas["dias"] == provas["dias_esperados"] == 2
