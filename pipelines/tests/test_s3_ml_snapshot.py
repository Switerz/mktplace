"""Gate S3 — testes do snapshot transacional do ranking ML (Parte D).

Antes do S3, `sync_produtos.sync_ml` declarava "full refresh sempre" mas escrevia
com `ON CONFLICT DO UPDATE`: chave que desaparecia da fonte permanecia no destino
para sempre. O refresh era declarado, nao real — e foi essa lacuna que impediu
provar a paridade de `/inteligencia` no fechamento do O1.

Estes testes provam o novo contrato sem tocar banco: staging, `DELETE` integral,
`INSERT`, reconciliacao contra a FOTOGRAFIA capturada, e rollback integral.
Tambem travam o que NAO pode mudar: dedup por `(brand, item_id)` mantendo o maior
`gross_revenue`, allowlist de marcas, guarda `MIN_ROWS_RATIO`, contrato de retorno
e auditoria.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import pipelines.sync_produtos as sp

MODULE_PATH = Path(sp.__file__)
AGORA = datetime(2026, 8, 18, 9, 2, 54, tzinfo=timezone.utc)


def linha(brand="kokeshi", item_id="i1", **kw):
    base = {c: None for c in sp.ML_BUSINESS_COLUMNS}
    base.update({
        "brand": brand, "item_id": item_id, "seller_sku": f"sku-{item_id}",
        "title": "Produto", "gross_revenue": Decimal("100.00"), "units_sold": 5,
        "unique_buyers": 4, "ad_spend": Decimal("10.00"),
        "product_status": "sells+advertised", "pareto_bucket": "A",
    })
    base.update(kw)
    return base


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._ultimo = ""
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._ultimo = " ".join(sql.split())
        self.conn.executed.append(self._ultimo)
        if self.conn.falhar_em and self.conn.falhar_em in self._ultimo:
            raise RuntimeError("falha injetada")
        if self._ultimo.startswith("DELETE FROM marts.fact_ml_produto_ranking"):
            self.rowcount = len(self.conn.target)
            self.conn.target = []
        elif self._ultimo.startswith("INSERT INTO marts.fact_ml_produto_ranking"):
            self.conn.target = list(self.conn.staged)
            self.rowcount = len(self.conn.target)

    def fetchone(self):
        return None

    def fetchall(self):
        if "FROM pg_temp." in self._ultimo:
            return list(self.conn.staged)
        if "FROM marts.fact_ml_produto_ranking" in self._ultimo:
            return list(self.conn.target)
        return []

    def close(self):
        pass


class Conn:
    def __init__(self, target=(), falhar_em=None):
        self.executed = []
        self.staged = []
        self.target = [dict(r) for r in target]
        self.falhar_em = falhar_em
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, cursor_factory=None):
        return Cursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def stub_execute_values(monkeypatch):
    def _fake(cur, sql, batch, page_size=500):
        cols = sp.ML_BUSINESS_COLUMNS
        cur.conn.staged = [dict(zip(cols, t[: len(cols)])) for t in batch]
    monkeypatch.setattr(sp, "execute_values", _fake)


def publicar(rows, target=(), falhar_em=None):
    conn = Conn(target=target, falhar_em=falhar_em)
    res = sp.ml_publish_snapshot(conn, rows, AGORA)
    return res, conn


# ===========================================================================
# Substituicao integral
# ===========================================================================

def test_d01_delete_integral_sem_where():
    _, conn = publicar([linha()])
    deletes = [s for s in conn.executed if s.startswith("DELETE")]
    assert deletes == ["DELETE FROM marts.fact_ml_produto_ranking"]
    assert "WHERE" not in deletes[0]


def test_d02_chave_obsoleta_e_removida_do_destino():
    """O ponto central: o upsert anterior nunca removia nada."""
    antigo = [linha(item_id="i1"), linha(item_id="i2"), linha(item_id="obsoleto")]
    novo = [linha(item_id="i1"), linha(item_id="i2")]
    res, conn = publicar(novo, target=antigo)
    assert res["deleted"] == 3
    assert res["published"] == 2
    assert {r["item_id"] for r in conn.target} == {"i1", "i2"}
    assert "obsoleto" not in {r["item_id"] for r in conn.target}


def test_d03_nenhum_on_conflict_no_caminho_do_ml():
    """`sync_shopee` e `sync_tiktok` seguem usando upsert de proposito; apenas o
    ML virou snapshot."""
    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def sync_ml(")
    j = src.index("def sync_tiktok(")
    assert "ON CONFLICT" not in src[i:j]
    assert src.count("ON CONFLICT") >= 2, "shopee e tiktok devem manter o upsert"


def test_d04_staging_pg_temp_com_on_commit_drop():
    _, conn = publicar([linha()])
    criacao = [s for s in conn.executed if "CREATE TEMP TABLE" in s]
    assert len(criacao) == 1
    assert "ON COMMIT DROP" in criacao[0]


def test_d05_advisory_lock_proprio_e_antes_do_delete():
    _, conn = publicar([linha()])
    assert any(f"pg_advisory_xact_lock" in s for s in conn.executed)
    i_lock = next(i for i, s in enumerate(conn.executed) if "pg_advisory_xact_lock" in s)
    i_del = next(i for i, s in enumerate(conn.executed) if s.startswith("DELETE"))
    assert i_lock < i_del


def test_d06_lock_do_ml_nao_colide_com_os_outros():
    import pipelines.ops.serving_refresh as sr
    import pipelines.sync_serving_snapshots as ss
    outros = ({sr.TARGETS[t].advisory_lock_key for t in sr.TARGET_ORDER}
              | {ss.SPECS[t].advisory_lock_key for t in ss.TARGET_ORDER})
    assert sp.ML_RANKING_ADVISORY_LOCK_KEY == 911_120_011
    assert sp.ML_RANKING_ADVISORY_LOCK_KEY not in outros


def test_d07_commit_so_depois_da_reconciliacao():
    _, conn = publicar([linha()])
    i_insert = max(i for i, s in enumerate(conn.executed)
                   if s.startswith("INSERT INTO marts."))
    i_releitura = max(i for i, s in enumerate(conn.executed)
                      if s.startswith("SELECT") and "FROM marts." in s)
    assert i_releitura > i_insert
    assert conn.committed is True


# ===========================================================================
# Rollback
# ===========================================================================

def test_d08_rollback_em_falha_de_staging(monkeypatch):
    def _vazio(cur, sql, batch, page_size=500):
        cur.conn.staged = []
    monkeypatch.setattr(sp, "execute_values", _vazio)
    with pytest.raises(RuntimeError, match="staging divergiu"):
        publicar([linha()])


def test_d09_rollback_em_falha_no_delete():
    conn = Conn(target=[linha()], falhar_em="DELETE FROM marts.")
    with pytest.raises(RuntimeError, match="falha injetada"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    assert conn.rolled_back is True
    assert conn.committed is False


def test_d10_rollback_em_divergencia_de_reconciliacao(monkeypatch):
    """Staging correta, destino divergente: a reconciliacao final tem de barrar."""
    class CursorMentiroso(Cursor):
        def fetchall(self):
            if "FROM marts.fact_ml_produto_ranking" in self._ultimo:
                return []          # destino "vazio" apos o insert
            return super().fetchall()

    conn = Conn()
    monkeypatch.setattr(Conn, "cursor", lambda self, cursor_factory=None: CursorMentiroso(self))
    with pytest.raises(RuntimeError, match="destino divergiu|EXCEPT bidirecional"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    assert conn.rolled_back is True


def test_d11_nenhuma_tabela_alem_de_fact_ml_produto_ranking_e_tocada():
    _, conn = publicar([linha()])
    for s in conn.executed:
        for proibida in ("fact_tiktok", "fact_shopee", "fact_marketplace",
                         "audit.", "dim_loja", "gold.", "raw."):
            assert proibida not in s, f"{proibida} em {s[:80]}"


# ===========================================================================
# Reconciliacao contra a fotografia, nunca contra releitura
# ===========================================================================

def test_d12_reconciliacao_usa_a_fotografia_mesmo_se_a_fonte_mudasse():
    """A funcao de publicacao recebe `rows` e nunca reabre a Gold: nao existe
    caminho para uma releitura entrar na decisao de commit."""
    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def ml_publish_snapshot(")
    j = src.index("def _ml_read_rows(")
    corpo = src[i:j]
    assert "_rds(" not in corpo, "a publicacao nao pode reabrir a fonte"
    assert "gold." not in corpo


def test_d13_fingerprint_deterministico_e_independe_da_ordem():
    rows = [linha(item_id="i2"), linha(item_id="i1")]
    assert sp.ml_fingerprint(rows) == sp.ml_fingerprint(list(reversed(rows)))


def test_d14_fingerprint_muda_com_o_valor():
    a = [linha(gross_revenue=Decimal("100.00"))]
    b = [linha(gross_revenue=Decimal("100.01"))]
    assert sp.ml_fingerprint(a) != sp.ml_fingerprint(b)


def test_d15_fingerprint_normaliza_decimal():
    a = [linha(gross_revenue=Decimal("100.10"))]
    b = [linha(gross_revenue=Decimal("100.1"))]
    assert sp.ml_fingerprint(a) == sp.ml_fingerprint(b)


def _code_only(path: Path) -> str:
    """Codigo sem docstrings, via AST: o docstring de `ml_fingerprint` DOCUMENTA
    por que `MD5(STRING_AGG(...))` nao e' usado, e um grep no texto bruto casaria
    com a explicacao da regra em vez de com uma violacao."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            c = node.body
            if (c and isinstance(c[0], ast.Expr) and isinstance(c[0].value, ast.Constant)
                    and isinstance(c[0].value.value, str)):
                c.pop(0)
    return ast.unparse(tree)


def test_d16_fingerprint_em_python_nao_em_sql():
    codigo = _code_only(MODULE_PATH)
    assert "hashlib.md5" in codigo
    assert "STRING_AGG" not in codigo.upper()
    assert "COLLATE" not in codigo.upper()


def test_d17_agregados_em_decimal():
    rows = [linha(item_id=f"i{i}", gross_revenue=Decimal("0.01")) for i in range(1000)]
    agg = sp.ml_aggregates(rows)
    assert isinstance(agg["sum_gross_revenue"], Decimal)
    assert agg["sum_gross_revenue"] == Decimal("10.00")


# ===========================================================================
# Validacao da fotografia
# ===========================================================================

def test_d18_chave_duplicada_na_fotografia_reprova():
    problemas = sp.ml_validate_snapshot([linha(item_id="i1"), linha(item_id="i1")])
    assert any("duplicada" in p for p in problemas)


def test_d19_chave_nula_reprova():
    problemas = sp.ml_validate_snapshot([linha(item_id=None)])
    assert any("chave nula" in p for p in problemas)


def test_d20_nan_reprova():
    problemas = sp.ml_validate_snapshot([linha(gross_revenue=Decimal("NaN"))])
    assert any("NaN" in p for p in problemas)


def test_d21_fotografia_valida_nao_produz_problema():
    assert sp.ml_validate_snapshot([linha(item_id="i1"), linha(item_id="i2")]) == []


# ===========================================================================
# O que NAO pode mudar
# ===========================================================================

def test_d22_dedup_deterministico_preservado():
    """`DISTINCT ON (brand, item_id) ... ORDER BY ..., gross_revenue DESC NULLS LAST`
    e' a regra de negocio: mantem a linha de MAIOR receita. Mudar isso mudaria o
    valor servido."""
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "SELECT DISTINCT ON (brand, item_id)" in src
    assert "ORDER BY brand, item_id, gross_revenue DESC NULLS LAST" in src


def test_d23_guarda_de_volume_preservada():
    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def sync_ml(")
    j = src.index("def sync_tiktok(")
    assert "MIN_ROWS_RATIO" in src[i:j]
    assert "queda suspeita de linhas" in src[i:j]


def test_d24_contrato_de_retorno_preservado():
    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def sync_ml(")
    j = src.index("def sync_tiktok(")
    assert 'return {"source": len(rows), "upserted": len(batch)}' in src[i:j]


def test_d25_auditoria_preservada():
    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def sync_ml(")
    j = src.index("def sync_tiktok(")
    corpo = src[i:j]
    assert "_audit_start(" in corpo
    assert '_audit_finish(\n            audit_conn, run_id, "success"' in corpo
    assert '_audit_finish(audit_conn, run_id, "failed"' in corpo


def test_d26_allowlist_de_marcas_preservada():
    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def sync_ml(")
    j = src.index("def sync_tiktok(")
    assert "_brands_sql(brands)" in src[i:j]
    assert "brands = brands or BRANDS_IN_SCOPE" in src[i:j]


def test_d27_refreshed_at_unico_para_todo_o_snapshot(monkeypatch):
    """Antes do S3 o INSERT usava `now` e o DO UPDATE usava `NOW()`: linhas do
    mesmo refresh carregavam instantes diferentes. Agora o instante e' um so',
    passado como parametro e identico em todas as tuplas."""
    capturado = {}

    def _espia(cur, sql, batch, page_size=500):
        capturado["sql"] = " ".join(sql.split())
        capturado["batch"] = list(batch)
        cols = sp.ML_BUSINESS_COLUMNS
        cur.conn.staged = [dict(zip(cols, tu[: len(cols)])) for tu in batch]

    monkeypatch.setattr(sp, "execute_values", _espia)
    publicar([linha(item_id="i1"), linha(item_id="i2")])

    assert "INSERT INTO pg_temp." in capturado["sql"]
    assert "refreshed_at" in capturado["sql"]
    instantes = {tu[-1] for tu in capturado["batch"]}
    assert instantes == {AGORA}, f"instantes diferentes no mesmo snapshot: {instantes}"

    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def ml_publish_snapshot(")
    j = src.index("def _ml_read_rows(")
    assert "NOW()" not in src[i:j], "instante tem de ser o mesmo para todas as linhas"


def test_d28_24_colunas_de_negocio_na_ordem():
    assert len(sp.ML_BUSINESS_COLUMNS) == 24
    assert sp.ML_BUSINESS_COLUMNS[:2] == ("brand", "item_id")
    assert sp.ML_KEY_COLUMNS == ("brand", "item_id")


def test_d29_zero_retry_no_caminho_de_escrita():
    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def ml_publish_snapshot(")
    j = src.index("def _ml_read_rows(")
    corpo = src[i:j].lower()
    for termo in ("retry", "sleep", "backoff", "while "):
        assert termo not in corpo, termo


def test_d30_o_retry_de_leitura_preexistente_continua_intacto():
    """`_read_rds_with_recovery_retry` e' retry de LEITURA, preexistente e
    documentado (conflito de recovery na replica). O S3 nao o remove."""
    assert callable(sp._read_rds_with_recovery_retry)
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "_read_rds_with_recovery_retry(_read_from_rds)" in src
