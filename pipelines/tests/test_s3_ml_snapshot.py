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

#: Schema REAL de `marts.fact_ml_produto_ranking`, medido no Neon em 18/08/2026
#: apos a migration 011: (data_type, numeric_precision, numeric_scale).
#:
#: A fonte na Gold e' `NUMERIC` sem escala; o destino declara escala; e a staging
#: e' `LIKE` do destino. Logo e' este schema que define para onde o PostgreSQL
#: arredonda a fotografia no INSERT — e o harness precisa simular isso, senao os
#: testes provariam um comportamento que o banco nao tem.
SCHEMA_DESTINO = {
    "brand": ("character varying", None, None),
    "item_id": ("character varying", None, None),
    "seller_sku": ("character varying", None, None),
    "title": ("text", None, None),
    "gross_revenue": ("numeric", 18, 2),
    "units_sold": ("bigint", 64, 0),
    "unique_buyers": ("bigint", 64, 0),
    "units_per_buyer": ("numeric", 10, 4),
    "cancel_rate_pct": ("numeric", 8, 4),
    "ad_spend": ("numeric", 14, 2),
    "ad_roas": ("numeric", 10, 4),
    "ad_acos_pct": ("numeric", 8, 4),
    "days_advertised": ("bigint", 64, 0),
    "revenue_share_pct": ("numeric", 8, 4),
    "cumulative_revenue_pct": ("numeric", 8, 4),
    "estimated_margin": ("numeric", 18, 2),
    "price_spread_pct": ("numeric", 8, 4),
    "pareto_bucket": ("text", None, None),
    "revenue_velocity": ("text", None, None),
    "ad_efficiency": ("text", None, None),
    "action_signal": ("text", None, None),
    "product_status": ("text", None, None),
    "first_sale": ("date", None, None),
    "last_sale": ("date", None, None),
}


def schema_rows(schema=None):
    """O que `information_schema.columns` devolveria, no formato RealDictCursor."""
    s = SCHEMA_DESTINO if schema is None else schema
    return [{"column_name": c, "data_type": t,
             "numeric_precision": p, "numeric_scale": e}
            for c, (t, p, e) in s.items()]


def escalas_de(schema=None):
    s = SCHEMA_DESTINO if schema is None else schema
    return {c: e for c, (t, p, e) in s.items()
            if t == "numeric" and e is not None and c in sp.ML_NUMERIC_COLUMNS}


def como_postgres(rows, schema=None):
    """Aplica em `rows` o arredondamento que o PostgreSQL aplicaria ao gravar na
    staging tipada. Usado pelo harness para simular o INSERT com fidelidade."""
    return sp.ml_project_to_target(rows, escalas_de(schema))


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
        if "information_schema.columns" in self._ultimo:
            return schema_rows(self.conn.schema)
        if "FROM pg_temp." in self._ultimo:
            return list(self.conn.staged)
        if "FROM marts.fact_ml_produto_ranking" in self._ultimo:
            return list(self.conn.target)
        return []

    def close(self):
        pass


class Conn:
    def __init__(self, target=(), falhar_em=None, schema=None):
        self.executed = []
        self.staged = []
        self.target = [dict(r) for r in target]
        self.falhar_em = falhar_em
        #: `None` = schema real. Um dict substitui o que o information_schema
        #: devolve, para exercitar os caminhos de incompatibilidade.
        self.schema = schema
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
    """Simula o INSERT na staging tipada, INCLUSIVE o arredondamento.

    A staging e' `CREATE TEMP TABLE (LIKE marts.fact_ml_produto_ranking)`, entao
    o PostgreSQL converte cada valor para a escala declarada. Um stub que
    guardasse o Decimal cru esconderia justamente o bug que reprovou a carga de
    18/08 — e faria os testes passarem contra um banco imaginario.
    """
    def _fake(cur, sql, batch, page_size=500):
        cols = sp.ML_BUSINESS_COLUMNS
        cruas = [dict(zip(cols, t[: len(cols)])) for t in batch]
        cur.conn.staged = como_postgres(cruas, cur.conn.schema)
    monkeypatch.setattr(sp, "execute_values", _fake)


def publicar(rows, target=(), falhar_em=None, schema=None):
    conn = Conn(target=target, falhar_em=falhar_em, schema=schema)
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


# ===========================================================================
# Projecao canonica para os tipos do destino
#
# A carga de 18/08/2026 reprovou com "staging divergiu da fotografia no
# fingerprint". Nao era dado corrompido: a Gold entrega `NUMERIC` sem escala com
# precisao cheia, o destino declara escala, a staging herda a escala do destino,
# e o codigo comparava o Decimal bruto contra o valor ja arredondado. 1.388 de
# 1.648 linhas divergiam so' em `cumulative_revenue_pct`.
#
# Estes testes travam a projecao e, principalmente, o que ela NAO pode afrouxar.
# ===========================================================================

VALOR_REAL = Decimal("94.838155642022304628")   # medido em gold.ml_produto_ranking


def test_e01_exemplo_real_de_producao_arredonda_para_4_casas():
    escalas = escalas_de()
    assert escalas["cumulative_revenue_pct"] == 4
    [proj] = sp.ml_project_to_target(
        [linha(cumulative_revenue_pct=VALOR_REAL)], escalas)
    assert proj["cumulative_revenue_pct"] == Decimal("94.8382")


def test_e02_empate_positivo_arredonda_para_longe_do_zero():
    """PostgreSQL usa ties away from zero em NUMERIC. O default do `round()` do
    Python e' meio-par, que daria 0.0000 aqui."""
    assert sp._ml_quantiza(Decimal("0.00005"), 4) == Decimal("0.0001")
    assert sp._ml_quantiza(Decimal("0.00015"), 4) == Decimal("0.0002")
    assert round(Decimal("0.00015"), 4) == Decimal("0.0002") or True  # doc: meio-par difere


def test_e03_empate_negativo_arredonda_para_longe_do_zero():
    assert sp._ml_quantiza(Decimal("-0.00005"), 4) == Decimal("-0.0001")
    assert sp._ml_quantiza(Decimal("-0.005"), 2) == Decimal("-0.01")
    assert sp._ml_quantiza(Decimal("-2.5"), 0) == Decimal("-3")


def test_e04_semantica_e_half_up_nao_half_even():
    """Sob meio-par, 0.125 -> 0.12 e 0.135 -> 0.14 (alterna). Sob ties away from
    zero, os dois sobem. Este teste falha se alguem trocar o modo."""
    assert sp._ml_quantiza(Decimal("0.125"), 2) == Decimal("0.13")
    assert sp._ml_quantiza(Decimal("0.135"), 2) == Decimal("0.14")
    codigo = _code_only(MODULE_PATH)
    assert "ROUND_HALF_UP" in codigo
    assert "ROUND_HALF_EVEN" not in codigo


def test_e05_escala_2_e_escala_4_convivem():
    escalas = escalas_de()
    assert escalas["gross_revenue"] == 2 and escalas["ad_spend"] == 2
    assert escalas["cancel_rate_pct"] == 4 and escalas["ad_roas"] == 4
    [p] = sp.ml_project_to_target([linha(
        gross_revenue=Decimal("10.005"), cancel_rate_pct=Decimal("1.00005"))], escalas)
    assert p["gross_revenue"] == Decimal("10.01")
    assert p["cancel_rate_pct"] == Decimal("1.0001")


def test_e06_zero_permanece_zero():
    for escala in (2, 4):
        assert sp._ml_quantiza(Decimal("0"), escala) == Decimal("0")
        assert sp._ml_quantiza(Decimal("0.00000000"), escala) == Decimal("0")


def test_e07_null_permanece_null():
    assert sp._ml_quantiza(None, 4) is None
    [p] = sp.ml_project_to_target([linha(ad_roas=None)], escalas_de())
    assert p["ad_roas"] is None


def test_e08_decimal_com_expoentes_diferentes_converge():
    a = sp._ml_quantiza(Decimal("1.5"), 2)
    b = sp._ml_quantiza(Decimal("1.50"), 2)
    c = sp._ml_quantiza(Decimal("15E-1"), 2)
    assert a == b == c == Decimal("1.50")
    assert sp._ml_canonico(a) == sp._ml_canonico(c)


def test_e09_valor_ja_na_escala_permanece_identico():
    v = Decimal("100.00")
    assert sp._ml_quantiza(v, 2) == v
    [p] = sp.ml_project_to_target([linha(gross_revenue=v)], escalas_de())
    assert sp._ml_canonico(p["gross_revenue"]) == sp._ml_canonico(v)


def test_e10_fotografia_original_nao_e_mutada():
    original = linha(cumulative_revenue_pct=VALOR_REAL)
    copia = dict(original)
    rows = [original]
    proj = sp.ml_project_to_target(rows, escalas_de())
    assert original == copia, "ml_project_to_target mutou a fonte"
    assert rows[0] is original
    assert proj[0] is not original
    assert proj[0]["cumulative_revenue_pct"] != original["cumulative_revenue_pct"]


def test_e11_texto_data_e_chave_atravessam_intactos():
    from datetime import date as _date
    original = linha(brand="kokeshi", item_id="MLB-123", seller_sku="  sku com espaco  ",
                     title="Título com acento e 0.5", first_sale=_date(2025, 10, 5),
                     last_sale=_date(2026, 8, 17), pareto_bucket="A",
                     action_signal="manter", units_sold=5, days_advertised=31)
    [p] = sp.ml_project_to_target([original], escalas_de())
    for col in ("brand", "item_id", "seller_sku", "title", "first_sale", "last_sale",
                "pareto_bucket", "action_signal", "units_sold", "days_advertised"):
        assert p[col] == original[col], col
        assert type(p[col]) is type(original[col]), col


def test_e12_apenas_colunas_da_allowlist_sao_tocadas():
    escalas = escalas_de()
    assert set(escalas) == set(sp.ML_NUMERIC_COLUMNS)
    nao_numericas = set(sp.ML_BUSINESS_COLUMNS) - set(sp.ML_NUMERIC_COLUMNS)
    assert nao_numericas & set(escalas) == set()
    assert "units_sold" not in escalas and "brand" not in escalas


# --- incompatibilidade codigo x schema: falha ANTES do DELETE ---------------

def _sem_delete(conn):
    assert not any(s.startswith("DELETE") for s in conn.executed), conn.executed
    assert not any("CREATE TEMP TABLE" in s for s in conn.executed)
    assert not any("pg_advisory_xact_lock" in s for s in conn.executed)
    assert conn.committed is False


def test_e13_coluna_numerica_esperada_ausente_falha_antes_do_delete():
    schema = {c: v for c, v in SCHEMA_DESTINO.items() if c != "ad_roas"}
    conn = Conn(target=[linha()], schema=schema)
    with pytest.raises(RuntimeError, match="ad_roas.*ausente|ausente.*ad_roas"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    _sem_delete(conn)


def test_e14_scale_nula_falha_antes_do_delete():
    schema = dict(SCHEMA_DESTINO)
    schema["cumulative_revenue_pct"] = ("numeric", 8, None)
    conn = Conn(target=[linha()], schema=schema)
    with pytest.raises(RuntimeError, match="numeric_scale invalida"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    _sem_delete(conn)


def test_e15_scale_negativa_falha():
    schema = dict(SCHEMA_DESTINO)
    schema["ad_spend"] = ("numeric", 14, -2)
    conn = Conn(schema=schema)
    with pytest.raises(RuntimeError, match="numeric_scale invalida"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    _sem_delete(conn)


def test_e16_tipo_trocado_no_schema_falha():
    schema = dict(SCHEMA_DESTINO)
    schema["gross_revenue"] = ("double precision", 53, None)
    conn = Conn(schema=schema)
    with pytest.raises(RuntimeError, match="esperado numeric"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    _sem_delete(conn)


def test_e17_coluna_que_virou_numeric_fora_da_allowlist_falha():
    """O inverso do e13: se o schema passar a declarar escala numa coluna que o
    codigo trata como intacta, ela seria comparada crua contra staging
    arredondada. Tem de reprovar em vez de passar calado."""
    schema = dict(SCHEMA_DESTINO)
    schema["units_per_buyer"] = ("numeric", 10, 4)      # segue na allowlist
    schema["problem_extra"] = ("numeric", 8, 4)         # irrelevante: nao e' de negocio
    schema["days_advertised"] = ("numeric", 10, 2)      # ESTA e' de negocio
    conn = Conn(schema=schema)
    with pytest.raises(RuntimeError, match="fora de ML_NUMERIC_COLUMNS"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    _sem_delete(conn)


def test_e18_coluna_de_negocio_ausente_no_destino_falha():
    schema = {c: v for c, v in SCHEMA_DESTINO.items() if c != "title"}
    conn = Conn(schema=schema)
    with pytest.raises(RuntimeError, match="title.*ausente|ausente.*title"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    _sem_delete(conn)


def test_e19_leitura_do_schema_e_a_primeira_query():
    _, conn = publicar([linha()])
    assert "information_schema.columns" in conn.executed[0]
    i_schema = 0
    i_lock = next(i for i, s in enumerate(conn.executed) if "pg_advisory_xact_lock" in s)
    i_del = next(i for i, s in enumerate(conn.executed) if s.startswith("DELETE"))
    assert i_schema < i_lock < i_del


def test_e20_escalas_vem_do_information_schema_nao_do_codigo():
    codigo = _code_only(MODULE_PATH)
    i = codigo.index("def ml_target_numeric_scales")
    j = codigo.index("def _ml_quantiza")
    corpo = codigo[i:j]
    assert "information_schema.columns" in corpo
    assert "numeric_scale" in corpo
    # nenhuma escala literal amarrada a uma coluna
    for literal in ("'cumulative_revenue_pct': 4", '"cumulative_revenue_pct": 4',
                    "scale = 4", "escala = 4", "escala=4"):
        assert literal not in corpo, literal


# --- o guardrail continua guardando -----------------------------------------

def test_e21_staging_arredondada_igual_a_fotografia_normalizada_passa():
    """O caso que reprovava antes da correcao: valor de precisao cheia na fonte,
    staging arredondada pelo tipo. Agora tem de PASSAR e publicar."""
    rows = [linha(item_id="i1", cumulative_revenue_pct=VALOR_REAL),
            linha(item_id="i2", cumulative_revenue_pct=Decimal("5.123456789"))]
    res, conn = publicar(rows)
    assert conn.committed is True
    assert res["published"] == 2
    assert res["checks"]["except_both_ways"] == (0, 0)
    assert {r["cumulative_revenue_pct"] for r in conn.target} == {
        Decimal("94.8382"), Decimal("5.1235")}


def test_e22_fingerprint_bruto_pode_diferir_mas_o_tipado_confere():
    rows = [linha(cumulative_revenue_pct=VALOR_REAL)]
    res, _ = publicar(rows)
    bruto = res["checks"]["fingerprint_raw"]
    tipado = res["checks"]["fingerprint"]
    assert bruto != tipado, "o exemplo precisa exercitar arredondamento real"
    assert bruto == sp.ml_fingerprint(rows)
    assert tipado == sp.ml_fingerprint(sp.ml_project_to_target(rows, escalas_de()))


def test_e23_diferenca_alem_da_escala_continua_reprovando(monkeypatch):
    """Staging com valor que NAO e' o arredondamento da fotografia: reprova."""
    def _corrompe(cur, sql, batch, page_size=500):
        cols = sp.ML_BUSINESS_COLUMNS
        cruas = [dict(zip(cols, t[: len(cols)])) for t in batch]
        staged = como_postgres(cruas, cur.conn.schema)
        staged[0]["cumulative_revenue_pct"] = Decimal("94.9999")   # != 94.8382
        cur.conn.staged = staged
    monkeypatch.setattr(sp, "execute_values", _corrompe)
    conn = Conn()
    with pytest.raises(RuntimeError, match="staging divergiu"):
        sp.ml_publish_snapshot(conn, [linha(cumulative_revenue_pct=VALOR_REAL)], AGORA)
    assert conn.rolled_back is True
    assert conn.committed is False


def test_e24_diferenca_em_texto_continua_reprovando(monkeypatch):
    def _corrompe(cur, sql, batch, page_size=500):
        cols = sp.ML_BUSINESS_COLUMNS
        cruas = [dict(zip(cols, t[: len(cols)])) for t in batch]
        staged = como_postgres(cruas, cur.conn.schema)
        staged[0]["title"] = "Outro titulo"
        cur.conn.staged = staged
    monkeypatch.setattr(sp, "execute_values", _corrompe)
    conn = Conn()
    with pytest.raises(RuntimeError, match="staging divergiu"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    assert conn.rolled_back is True


def test_e25_null_virando_zero_continua_reprovando(monkeypatch):
    """Zero fabricado no lugar de NULL e' perda de informacao, nao arredondamento."""
    def _corrompe(cur, sql, batch, page_size=500):
        cols = sp.ML_BUSINESS_COLUMNS
        cruas = [dict(zip(cols, t[: len(cols)])) for t in batch]
        staged = como_postgres(cruas, cur.conn.schema)
        staged[0]["ad_roas"] = Decimal("0.0000")
        cur.conn.staged = staged
    monkeypatch.setattr(sp, "execute_values", _corrompe)
    conn = Conn()
    with pytest.raises(RuntimeError, match="staging divergiu"):
        sp.ml_publish_snapshot(conn, [linha(ad_roas=None)], AGORA)
    assert conn.rolled_back is True


def test_e26_chave_diferente_na_staging_continua_reprovando(monkeypatch):
    def _corrompe(cur, sql, batch, page_size=500):
        cols = sp.ML_BUSINESS_COLUMNS
        cruas = [dict(zip(cols, t[: len(cols)])) for t in batch]
        staged = como_postgres(cruas, cur.conn.schema)
        staged[0]["item_id"] = "outro"
        cur.conn.staged = staged
    monkeypatch.setattr(sp, "execute_values", _corrompe)
    conn = Conn()
    with pytest.raises(RuntimeError, match="staging divergiu"):
        sp.ml_publish_snapshot(conn, [linha()], AGORA)
    assert conn.rolled_back is True


def test_e27_destino_igual_a_staging_passa():
    res, conn = publicar([linha(item_id="i1", cumulative_revenue_pct=VALOR_REAL),
                          linha(item_id="i2")])
    assert conn.committed is True
    assert res["published"] == 2
    assert sp.ml_fingerprint(conn.target) == res["checks"]["fingerprint"]


def test_e28_destino_divergente_continua_causando_rollback(monkeypatch):
    class CursorMentiroso(Cursor):
        def fetchall(self):
            if "FROM marts.fact_ml_produto_ranking" in self._ultimo:
                falso = como_postgres([linha(cumulative_revenue_pct=VALOR_REAL)],
                                      self.conn.schema)
                falso[0]["gross_revenue"] = Decimal("999.99")
                return falso
            return super().fetchall()
    conn = Conn()
    monkeypatch.setattr(Conn, "cursor",
                        lambda self, cursor_factory=None: CursorMentiroso(self))
    with pytest.raises(RuntimeError, match="destino divergiu|fingerprint do destino"):
        sp.ml_publish_snapshot(conn, [linha(cumulative_revenue_pct=VALOR_REAL)], AGORA)
    assert conn.rolled_back is True


def test_e29_agregados_usam_a_fotografia_normalizada():
    rows = [linha(item_id=f"i{i}", gross_revenue=Decimal("0.005")) for i in range(4)]
    res, _ = publicar(rows)
    # 0.005 -> 0.01 em NUMERIC(18,2); 4 linhas -> 0.04, nao 0.020
    assert res["checks"]["aggregates"]["sum_gross_revenue"] == "0.04"
    assert sp.ml_aggregates(rows)["sum_gross_revenue"] == Decimal("0.020")


def test_e30_staging_recebe_o_valor_original_nao_o_projetado(monkeypatch):
    """Quem arredonda e' o PostgreSQL. Se o codigo inserisse o valor ja
    projetado, a reconciliacao viraria tautologia."""
    capturado = {}

    def _espia(cur, sql, batch, page_size=500):
        capturado["batch"] = list(batch)
        cols = sp.ML_BUSINESS_COLUMNS
        cruas = [dict(zip(cols, t[: len(cols)])) for t in batch]
        cur.conn.staged = como_postgres(cruas, cur.conn.schema)

    monkeypatch.setattr(sp, "execute_values", _espia)
    publicar([linha(cumulative_revenue_pct=VALOR_REAL)])
    i = sp.ML_BUSINESS_COLUMNS.index("cumulative_revenue_pct")
    assert capturado["batch"][0][i] == VALOR_REAL, "o INSERT nao deve pre-arredondar"


# --- proibicoes ------------------------------------------------------------

def test_e31_sem_tolerancia_epsilon_e_sem_float():
    codigo = _code_only(MODULE_PATH)
    i = codigo.index("def ml_target_numeric_scales")
    j = codigo.index("def ml_publish_snapshot")
    corpo = codigo[i:j]
    for proibido in ("epsilon", "tolerancia", "tolerance", "float(", "1e-", "math."):
        assert proibido not in corpo, proibido
    assert "abs(" not in corpo


def test_e32_float_em_coluna_numerica_e_erro_explicito():
    with pytest.raises(RuntimeError, match="float"):
        sp._ml_quantiza(1.5, 2)


def test_e33_nenhuma_mudanca_de_schema_no_caminho_de_escrita():
    codigo = _code_only(MODULE_PATH)
    i = codigo.index("def ml_target_numeric_scales")
    j = codigo.index("def _ml_read_rows")
    corpo = codigo[i:j].upper()
    for ddl in ("ALTER TABLE", "ALTER COLUMN", "DROP ", "TRUNCATE"):
        assert ddl not in corpo, ddl
    assert "CREATE TEMP TABLE" in corpo    # a staging segue sendo a unica criacao


def test_e34_delete_continua_depois_da_validacao_da_staging():
    _, conn = publicar([linha(cumulative_revenue_pct=VALOR_REAL)])
    # A LEITURA da staging, nao qualquer mencao a pg_temp: o `INSERT INTO marts.
    # ... SELECT ... FROM pg_temp` tambem cita a staging e ocorre depois do DELETE.
    i_leitura = next(i for i, s in enumerate(conn.executed)
                     if s.startswith("SELECT") and "FROM pg_temp." in s)
    i_del = next(i for i, s in enumerate(conn.executed) if s.startswith("DELETE"))
    i_insert = next(i for i, s in enumerate(conn.executed)
                    if s.startswith("INSERT INTO marts."))
    assert i_leitura < i_del < i_insert


def test_e35_contrato_e_retorno_de_sync_ml_preservados():
    src = MODULE_PATH.read_text(encoding="utf-8")
    i = src.index("def sync_ml(")
    j = src.index("def sync_tiktok(")
    corpo = src[i:j]
    assert 'return {"source": len(rows), "upserted": len(batch)}' in corpo
    assert "MIN_ROWS_RATIO" in corpo
    assert "ml_validate_snapshot(rows)" in corpo
    assert "_brands_sql(brands)" in corpo
    # o fingerprint REPORTADO passa a ser o tipado
    assert "res['checks']['fingerprint']" in corpo


def test_e36_projecao_nao_virou_framework_generico():
    """Helper local a sync_produtos.py, sem modulo novo nem abstracao exportada."""
    for nome in ("ml_target_numeric_scales", "_ml_quantiza", "ml_project_to_target"):
        assert hasattr(sp, nome), nome
    import pipelines.sync_serving_snapshots as ss
    for nome in ("ml_project_to_target", "_ml_quantiza"):
        assert not hasattr(ss, nome), f"{nome} nao deve ter vazado para o snapshot generico"
