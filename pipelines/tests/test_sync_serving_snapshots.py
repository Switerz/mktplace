"""Gate S3 — testes de pipelines/sync_serving_snapshots.py (Parte C).

Nenhum banco e' tocado e nenhum processo criado: as tres fabricas de conexao sao
injetadas. Os fakes modelam staging, `DELETE` integral, `INSERT` e as releituras
da reconciliacao — um fake permissivo nao provaria nada.

O que estes testes protegem:

- allowlist LITERAL de exatamente dois targets, sem registro dinamico;
- fonte capturada UMA vez, e reconciliacao contra essa fotografia — nunca contra
  uma releitura posterior (as duas fontes sao views, uma delas recalculada a cada
  leitura);
- `DELETE` integral, que e' o que remove chave desaparecida da fonte;
- rollback integral em qualquer falha;
- zero escrita sem `--apply`, e zero conexao de escrita aberta no diagnostico;
- guardas de volume absoluta e proporcional;
- zero retry/backoff/sleep;
- erro sanitizado, sem topologia.
"""
from __future__ import annotations

import ast
import contextlib
import subprocess
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import pipelines.sync_serving_snapshots as ss
import pipelines.sync_tiktok_serving as tk_sync

MODULE_PATH = Path(ss.__file__)
AGORA = datetime(2026, 8, 18, 6, 5, 0, tzinfo=timezone.utc)


def code_only(path: Path) -> str:
    """Codigo sem docstrings, via AST: este repositorio DOCUMENTA as proibicoes
    nos proprios comentarios, e um grep no texto bruto casaria com a documentacao
    da regra em vez de com uma violacao."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            c = node.body
            if (c and isinstance(c[0], ast.Expr) and isinstance(c[0].value, ast.Constant)
                    and isinstance(c[0].value.value, str)):
                c.pop(0)
    return ast.unparse(tree)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def linha_ml(brand="kokeshi", **kw):
    base = {"brand": brand, "total_buyers": 1000, "repeat_buyers": 250,
            "repeat_rate_pct": Decimal("25.04"), "avg_customer_ltv": Decimal("133.34"),
            "vip_buyers": 30, "one_and_done_buyers": 750,
            "at_risk_or_churned": 120, "overall_roas": Decimal("7.12")}
    base.update(kw)
    return base


def linha_canal(date="2026-08-17", brand="kokeshi", channel="VIDEO", **kw):
    base = {"date": date, "brand": brand, "channel": channel,
            "impressions": Decimal("1000"), "page_views": 100,
            "items_sold": 10, "gmv": Decimal("500.50")}
    base.update(kw)
    return base


QUATRO_ML = [linha_ml(b) for b in ("barbours", "kokeshi", "lescent", "rituaria")]
#: 220 datas x 5 marcas x 3 canais = 3.300 linhas, acima do piso de 3.000 do
#: target. O piso e' real (a fonte medida tem ~4,7 mil linhas), entao o fixture
#: precisa ser realista para exercitar o caminho de sucesso.
_DATAS = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(220)]
MUITOS_CANAIS = [
    linha_canal(date=d, brand=b, channel=c)
    for d in _DATAS
    for b in ("apice", "barbours", "kokeshi", "lescent", "rituaria")
    for c in ("VIDEO", "LIVE", "PRODUCT_CARD")
]


class CursorFake:
    def __init__(self, conn):
        self.conn = conn
        self._ultimo = ""
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._ultimo = " ".join(sql.split())
        self.conn.executed.append((self._ultimo, params))
        if self.conn.falhar_em and self.conn.falhar_em in self._ultimo:
            raise RuntimeError("falha injetada no banco")
        if self._ultimo.startswith("DELETE FROM marts."):
            self.rowcount = len(self.conn.target)
            self.conn.target = []
        elif self._ultimo.startswith("INSERT INTO marts."):
            self.conn.target = list(self.conn.staged)
            self.rowcount = len(self.conn.target)

    def fetchone(self):
        if "COUNT(*)" in self._ultimo:
            return (self.conn.contagem_inicial,)
        return None

    def fetchall(self):
        if "FROM pg_temp." in self._ultimo:
            return list(self.conn.staged)
        if "FROM marts." in self._ultimo:
            return list(self.conn.target)
        return list(self.conn.source_rows)

    def close(self):
        pass


class ConnFake:
    def __init__(self, source_rows=(), contagem_inicial=0, falhar_em=None):
        self.source_rows = list(source_rows)
        self.contagem_inicial = contagem_inicial
        self.falhar_em = falhar_em
        self.executed = []
        self.staged = []
        self.target = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.readonly = None

    def cursor(self, cursor_factory=None):
        return CursorFake(self)

    def set_session(self, readonly=None, **kw):
        self.readonly = readonly

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_execute_values(monkeypatch):
    def _fake(cur, sql, batch, page_size=500):
        spec = cur.conn.spec
        cols = list(spec.all_columns)
        cur.conn.staged = [dict(zip(cols, tupla[: len(cols)])) for tupla in batch]
    monkeypatch.setattr(ss, "execute_values", _fake)


def rodar(target, source_rows, *, apply=False, contagem_inicial=0,
          falhar_em=None, run_id=None):
    spec = ss.SPECS[target]
    src = ConnFake(source_rows)
    src.spec = spec
    leitura = ConnFake(contagem_inicial=contagem_inicial)
    leitura.spec = spec
    escrita = ConnFake(contagem_inicial=contagem_inicial, falhar_em=falhar_em)
    escrita.spec = spec
    escrita.target = [dict(r) for r in source_rows] if contagem_inicial else []
    abertas = {"escrita": 0}

    def fabrica_escrita():
        abertas["escrita"] += 1
        return escrita

    codigo = ss.run_target(
        target, apply=apply, run_id=run_id, now=AGORA,
        source_factory=lambda: src,
        neon_read_factory=lambda: leitura,
        neon_write_factory=fabrica_escrita,
        # Desde a correcao da Task 2/3 o caminho `--apply` registra em
        # `audit.source_sync_run`. Sem injetar a conexao de auditoria, este
        # helper cairia na conexao REAL. `AuditConnFake` esta definida mais
        # abaixo no arquivo e e' resolvida em tempo de chamada.
        audit_factory=lambda: AuditConnFake(),
    )
    return codigo, src, leitura, escrita, abertas


# ===========================================================================
# Allowlist literal de dois targets
# ===========================================================================

def test_c01_allowlist_tem_exatamente_dois_targets():
    assert set(ss.SPECS) == {"ml_cross_company", "tiktok_channel_efficiency"}
    assert ss.TARGET_ORDER == ("ml_cross_company", "tiktok_channel_efficiency")


@pytest.mark.parametrize("desconhecido", ["ml", "brand", "creator", "all", "", "shopee"])
def test_c02_target_fora_da_allowlist_falha_sem_conexao(desconhecido):
    with pytest.raises(ValueError, match="target desconhecido"):
        ss.resolve_spec(desconhecido)


def test_c03_cli_nao_aceita_target_fora_da_allowlist():
    for ruim in ("ml", "all", "shopee"):
        with pytest.raises(SystemExit):
            ss.build_parser().parse_args(["--target", ruim])


def test_c04_nao_e_framework_generico():
    """Sem registro dinamico: `SPECS` e' um dict literal de dois itens."""
    codigo = code_only(MODULE_PATH)
    for termo in ("register", "plugin", "importlib", "getattr(ss", "eval(", "exec("):
        assert termo not in codigo, termo


def test_c05_nao_reusa_nem_altera_o_wrapper_do_o1():
    codigo = code_only(MODULE_PATH)
    assert "serving_refresh" not in codigo
    import pipelines.ops.serving_refresh as sr
    assert set(sr.TARGETS) == {"ml", "brand", "creator"}, "o wrapper do O1 nao pode mudar"


# ===========================================================================
# Fonte capturada uma vez; sessao read-only
# ===========================================================================

def test_c06_fonte_lida_uma_unica_vez_por_execucao():
    _, src, _, _, _ = rodar("ml_cross_company", QUATRO_ML)
    selects = [s for s, _ in src.executed if s.startswith("SELECT")]
    assert len(selects) == 1, selects


def test_c07_conexao_da_fonte_e_fechada():
    _, src, _, _, _ = rodar("ml_cross_company", QUATRO_ML)
    assert src.closed is True


def test_c08_datamart_readonly_usa_sessao_somente_leitura(monkeypatch):
    capturado = {}

    def fake_connect(url, connect_timeout=None):
        conn = ConnFake()
        capturado["conn"] = conn
        return conn

    monkeypatch.setattr(ss.psycopg2, "connect", fake_connect)
    ss._datamart_readonly("postgresql://u:p@host/db")
    assert capturado["conn"].readonly is True


def test_c09_query_da_fonte_tem_colunas_explicitas_e_zero_select_estrela():
    for nome, spec in ss.SPECS.items():
        sql = ss.build_source_query(spec)
        assert sql.startswith("SELECT ")
        assert "*" not in sql
        for c in spec.all_columns:
            assert c in sql, f"{nome}: {c} ausente"


def test_c10_allowlist_de_marca_vai_como_parametro_nunca_interpolada():
    spec = ss.SPECS["tiktok_channel_efficiency"]
    sql = ss.build_source_query(spec)
    assert "brand = ANY(%(brands)s)" in sql
    for marca in tk_sync.ALLOWED_BRANDS:
        assert marca not in sql
    assert ss.source_params(spec)["brands"] == list(tk_sync.ALLOWED_BRANDS)


def test_c11_allowlist_e_a_mesma_tupla_do_conector():
    assert ss.SPECS["tiktok_channel_efficiency"].brand_allowlist is tk_sync.ALLOWED_BRANDS
    assert ss.SPECS["ml_cross_company"].brand_allowlist is None


def test_c12_identificador_malformado_e_rejeitado():
    ruim = ss.SnapshotSpec(
        name="x", source_relation="gold.t; DROP TABLE y", target_table="marts.x",
        staging_name="s", key_columns=("brand",), value_columns=("a",),
        additive_columns=(), advisory_lock_key=1, min_rows=0,
        min_ratio_vs_target=0.0, brand_allowlist=None, preflight_source="p",
        step_name="s", step_timeout_seconds=10, marketplace_id=1,
    )
    with pytest.raises(ValueError, match="falhou na validacao de seguranca"):
        ss.build_source_query(ruim)


# ===========================================================================
# Diagnostico nao escreve
# ===========================================================================

@pytest.mark.parametrize("target,rows", [("ml_cross_company", QUATRO_ML),
                                         ("tiktok_channel_efficiency", MUITOS_CANAIS)])
def test_c13_sem_apply_nenhuma_conexao_de_escrita_e_aberta(target, rows):
    codigo, _, _, escrita, abertas = rodar(target, rows, apply=False)
    assert codigo == 0
    assert abertas["escrita"] == 0, "conexao de escrita aberta no diagnostico"
    assert escrita.executed == []
    assert escrita.committed is False


def test_c14_com_apply_escreve_e_commita():
    codigo, _, _, escrita, abertas = rodar("ml_cross_company", QUATRO_ML, apply=True)
    assert codigo == 0
    assert abertas["escrita"] == 1
    assert escrita.committed is True
    assert escrita.rolled_back is False


# ===========================================================================
# Substituicao integral
# ===========================================================================

def test_c15_delete_e_integral_sem_where():
    _, _, _, escrita, _ = rodar("ml_cross_company", QUATRO_ML, apply=True)
    deletes = [s for s, _ in escrita.executed if s.startswith("DELETE")]
    assert deletes == ["DELETE FROM marts.fact_ml_cross_company_summary"]
    assert "WHERE" not in deletes[0]


def test_c16_chave_que_desapareceu_da_fonte_sai_do_destino():
    """E' o que um upsert nunca faria."""
    tres = QUATRO_ML[:3]
    _, _, _, escrita, _ = rodar("ml_cross_company", tres, apply=True, contagem_inicial=4)
    marcas = {r["brand"] for r in escrita.target}
    assert marcas == {"barbours", "kokeshi", "lescent"}
    assert "rituaria" not in marcas


def test_c17_nenhum_on_conflict_no_codigo():
    codigo = code_only(MODULE_PATH).upper()
    assert "ON CONFLICT" not in codigo


def test_c18_staging_e_pg_temp_com_on_commit_drop():
    _, _, _, escrita, _ = rodar("ml_cross_company", QUATRO_ML, apply=True)
    criacoes = [s for s, _ in escrita.executed if "CREATE TEMP TABLE" in s]
    assert len(criacoes) == 1
    assert "ON COMMIT DROP" in criacoes[0]


def test_c19_advisory_lock_proprio_por_target():
    chaves = {ss.SPECS[t].advisory_lock_key for t in ss.TARGET_ORDER}
    assert chaves == {909_120_009, 910_120_010}
    import pipelines.ops.serving_refresh as sr
    do_o1 = {sr.TARGETS[t].advisory_lock_key for t in sr.TARGET_ORDER}
    assert chaves.isdisjoint(do_o1), "colidiria com os locks do O1"


def test_c20_lock_e_adquirido_antes_do_delete():
    _, _, _, escrita, _ = rodar("ml_cross_company", QUATRO_ML, apply=True)
    ordem = [s for s, _ in escrita.executed]
    i_lock = next(i for i, s in enumerate(ordem) if "pg_advisory_xact_lock" in s)
    i_del = next(i for i, s in enumerate(ordem) if s.startswith("DELETE"))
    assert i_lock < i_del


# ===========================================================================
# Reconciliacao contra a FOTOGRAFIA
# ===========================================================================

def test_c21_fonte_capturada_exatamente_uma_vez(monkeypatch):
    """A fonte e' lida UMA vez e a reconciliacao usa essa lista. Nenhuma releitura
    e' possivel: `capture_source` e' chamada uma unica vez por execucao."""
    chamadas = []
    original = ss.capture_source

    def espia(conn, spec):
        rows = original(conn, spec)
        chamadas.append(len(rows))
        return rows

    monkeypatch.setattr(ss, "capture_source", espia)
    codigo, _, _, escrita, _ = rodar("ml_cross_company", QUATRO_ML, apply=True)
    assert codigo == 0
    assert chamadas == [4], f"capture_source chamada {len(chamadas)}x"
    assert len(escrita.target) == 4


def test_c21b_conexao_da_fonte_e_fechada_antes_de_qualquer_escrita():
    """Prova estrutural de que nao ha releitura: a conexao da Gold ja esta
    fechada quando a transacao de escrita comeca."""
    spec = ss.SPECS["ml_cross_company"]
    src = ConnFake(QUATRO_ML); src.spec = spec
    leitura = ConnFake(); leitura.spec = spec
    escrita = ConnFake(); escrita.spec = spec
    estado = {}

    def fabrica_escrita():
        estado["fonte_fechada_ao_abrir_escrita"] = src.closed
        return escrita

    ss.run_target("ml_cross_company", apply=True, now=AGORA,
                  source_factory=lambda: src,
                  neon_read_factory=lambda: leitura,
                  neon_write_factory=fabrica_escrita,
                  audit_factory=lambda: AuditConnFake())
    assert estado["fonte_fechada_ao_abrir_escrita"] is True


def test_c21c_o_destino_recebe_a_lista_capturada():
    """A fotografia e' o unico insumo da publicacao: o que entra no destino e'
    exatamente o que `capture_source` devolveu."""
    _, _, _, escrita, _ = rodar("ml_cross_company", QUATRO_ML, apply=True)
    assert {r["brand"] for r in escrita.target} == {r["brand"] for r in QUATRO_ML}
    assert len(escrita.target) == len(QUATRO_ML)


def test_c22_fingerprint_e_deterministico_e_independe_da_ordem():
    spec = ss.SPECS["ml_cross_company"]
    a = ss.fingerprint(spec, QUATRO_ML)
    b = ss.fingerprint(spec, list(reversed(QUATRO_ML)))
    assert a == b


def test_c23_fingerprint_muda_se_um_valor_muda():
    spec = ss.SPECS["ml_cross_company"]
    alterado = [dict(r) for r in QUATRO_ML]
    alterado[0]["total_buyers"] = 1001
    assert ss.fingerprint(spec, alterado) != ss.fingerprint(spec, QUATRO_ML)


def test_c24_fingerprint_normaliza_decimal():
    """1.10 e 1.1 tem de gerar o mesmo hash."""
    spec = ss.SPECS["ml_cross_company"]
    a = [linha_ml("kokeshi", overall_roas=Decimal("1.10"))]
    b = [linha_ml("kokeshi", overall_roas=Decimal("1.1"))]
    assert ss.fingerprint(spec, a) == ss.fingerprint(spec, b)


def test_c25_fingerprint_e_calculado_em_python_nao_em_sql():
    """`MD5(STRING_AGG(... ORDER BY texto))` depende de colacao, e as duas pontas
    usam locales diferentes."""
    codigo = code_only(MODULE_PATH)
    assert "hashlib.md5" in codigo, "o hash tem de ser calculado em Python"
    # `MD5(` cru casaria com `hashlib.md5()`; o que nao pode existir e' o MD5 do
    # Postgres combinado com STRING_AGG, que e' o padrao dependente de colacao.
    assert "STRING_AGG" not in codigo.upper()
    assert "COLLATE" not in codigo.upper()


def test_c26_agregados_em_decimal_nunca_float():
    spec = ss.SPECS["tiktok_channel_efficiency"]
    agg = ss.aggregates(spec, MUITOS_CANAIS)
    assert isinstance(agg["sum_gmv"], Decimal)
    esperado = Decimal("500.50") * len(MUITOS_CANAIS)
    assert agg["sum_gmv"] == esperado


def test_c27_divergencia_de_reconciliacao_faz_rollback(monkeypatch):
    """Staging que nao reflete a fotografia aborta antes do DELETE."""
    def _fake(cur, sql, batch, page_size=500):
        cur.conn.staged = []      # staging vazia de proposito
    monkeypatch.setattr(ss, "execute_values", _fake)
    spec = ss.SPECS["ml_cross_company"]
    src = ConnFake(QUATRO_ML); src.spec = spec
    leitura = ConnFake(); leitura.spec = spec
    escrita = ConnFake(); escrita.spec = spec
    with pytest.raises(RuntimeError, match="staging divergiu"):
        ss.run_target("ml_cross_company", apply=True, now=AGORA,
                      source_factory=lambda: src,
                      neon_read_factory=lambda: leitura,
                      neon_write_factory=lambda: escrita,
                      audit_factory=lambda: AuditConnFake())
    assert escrita.rolled_back is True
    assert escrita.committed is False
    assert [s for s, _ in escrita.executed if s.startswith("DELETE")] == []


def test_c28_falha_no_delete_faz_rollback_integral():
    codigo = None
    with pytest.raises(RuntimeError, match="falha injetada"):
        rodar("ml_cross_company", QUATRO_ML, apply=True, falhar_em="DELETE FROM marts.")
    assert codigo is None


def test_c29_rollback_deixa_o_destino_como_estava():
    spec = ss.SPECS["ml_cross_company"]
    src = ConnFake(QUATRO_ML); src.spec = spec
    leitura = ConnFake(contagem_inicial=4); leitura.spec = spec
    escrita = ConnFake(falhar_em="INSERT INTO marts."); escrita.spec = spec
    escrita.target = [dict(r) for r in QUATRO_ML]
    with pytest.raises(RuntimeError):
        ss.run_target("ml_cross_company", apply=True, now=AGORA,
                      source_factory=lambda: src,
                      neon_read_factory=lambda: leitura,
                      neon_write_factory=lambda: escrita,
                      audit_factory=lambda: AuditConnFake())
    assert escrita.rolled_back is True
    assert escrita.committed is False


def test_c30_commit_so_depois_da_reconciliacao():
    _, _, _, escrita, _ = rodar("tiktok_channel_efficiency", MUITOS_CANAIS, apply=True)
    ordem = [s for s, _ in escrita.executed]
    i_insert = max(i for i, s in enumerate(ordem) if s.startswith("INSERT INTO marts."))
    i_releitura = max(i for i, s in enumerate(ordem)
                      if s.startswith("SELECT") and "FROM marts." in s)
    assert i_releitura > i_insert, "reconciliacao tem de ler o destino APOS o insert"
    assert escrita.committed is True


# ===========================================================================
# Validacao e guardas de volume
# ===========================================================================

def test_c31_fonte_abaixo_do_piso_absoluto_reprova_sem_escrever():
    codigo, _, _, escrita, abertas = rodar("ml_cross_company", QUATRO_ML[:2], apply=True)
    assert codigo == ss.EXIT_FALHA
    assert abertas["escrita"] == 0
    assert escrita.executed == []


def test_c32_fonte_truncada_em_relacao_ao_destino_reprova():
    """Isola a guarda de PROPORCAO da guarda absoluta: 3.100 linhas passam o piso
    de 3.000, mas ficam abaixo dos 90% de um destino com 5.000 — e' a proporcao
    que tem de reprovar, sozinha."""
    poucos = MUITOS_CANAIS[:3100]
    assert len(poucos) >= ss.SPECS["tiktok_channel_efficiency"].min_rows
    codigo, _, _, _, abertas = rodar("tiktok_channel_efficiency", poucos,
                                     apply=True, contagem_inicial=5000)
    assert codigo == ss.EXIT_FALHA
    assert abertas["escrita"] == 0


def test_c32b_mensagem_de_reprovacao_cita_a_proporcao(capsys):
    rodar("tiktok_channel_efficiency", MUITOS_CANAIS[:3100],
          apply=True, contagem_inicial=5000)
    saida = capsys.readouterr().out
    assert "abaixo do minimo" in saida and "90%" in saida


def test_c33_duplicidade_na_fonte_reprova():
    dup = QUATRO_ML + [linha_ml("kokeshi")]
    problemas = ss.validate_source_rows(ss.SPECS["ml_cross_company"], dup)
    assert any("duplicada" in p for p in problemas)


def test_c34_chave_nula_reprova():
    rows = [linha_ml("kokeshi"), linha_ml(None)]
    problemas = ss.validate_source_rows(ss.SPECS["ml_cross_company"], rows)
    assert any("chave nula" in p for p in problemas)


def test_c35_valor_negativo_reprova():
    rows = [linha_ml(b) for b in ("barbours", "kokeshi", "lescent")]
    rows.append(linha_ml("rituaria", total_buyers=-1))
    problemas = ss.validate_source_rows(ss.SPECS["ml_cross_company"], rows)
    assert any("negativo" in p for p in problemas)


def test_c36_nan_reprova():
    rows = [linha_ml(b) for b in ("barbours", "kokeshi", "lescent")]
    rows.append(linha_ml("rituaria", total_buyers=Decimal("NaN")))
    problemas = ss.validate_source_rows(ss.SPECS["ml_cross_company"], rows)
    assert any("NaN" in p for p in problemas)


def test_c37_marca_fora_da_allowlist_na_fonte_reprova():
    rows = MUITOS_CANAIS[:3500] + [linha_canal(brand="marca_estranha")]
    problemas = ss.validate_source_rows(ss.SPECS["tiktok_channel_efficiency"], rows)
    assert any("allowlist" in p for p in problemas)


# ===========================================================================
# Zero retry; erro sanitizado; zero dependencia nova
# ===========================================================================

def test_c38_zero_retry_backoff_sleep_no_codigo():
    codigo = code_only(MODULE_PATH).lower()
    for termo in ("retry", "backoff", "sleep", "tenacity", "max_attempts"):
        assert termo not in codigo, termo


def test_c39_uma_leitura_e_uma_escrita_por_execucao():
    _, src, _, escrita, abertas = rodar("ml_cross_company", QUATRO_ML, apply=True)
    assert len([s for s, _ in src.executed if s.startswith("SELECT")]) == 1
    assert abertas["escrita"] == 1
    assert len([s for s, _ in escrita.executed if s.startswith("INSERT INTO marts.")]) == 1


def test_c40_sanitizadores_sao_os_dos_modulos_validados():
    assert ss.sanitize_error_message is tk_sync.sanitize_error_message
    assert ss.sanitize_run_id is tk_sync.sanitize_run_id
    assert ss.validate_identifier is tk_sync.validate_identifier
    assert ss.validate_qualified is tk_sync.validate_qualified


def test_c41_erro_de_conexao_nao_vaza_topologia(monkeypatch, capsys):
    nativo = ('connection to server at "datamart-interno.exemplo.local" (10.1.2.3), '
              "port 5432 failed: timeout expired")

    def _boom():
        raise RuntimeError(nativo)

    monkeypatch.setattr(ss, "_datamart_readonly", lambda *a, **k: _boom())
    monkeypatch.setattr(ss, "_get_url", lambda env: "postgresql://u:p@h/d")
    codigo = ss.main(["--target", "ml_cross_company"])
    err = capsys.readouterr().err
    assert codigo == ss.EXIT_FALHA
    for topo in ("datamart-interno.exemplo.local", "10.1.2.3", "5432"):
        assert topo not in err


def test_c42_nenhuma_url_e_impressa(capsys):
    rodar("ml_cross_company", QUATRO_ML, apply=True)
    saida = capsys.readouterr().out
    for termo in ("postgres", "@", "DATABASE_URL", "DATAMART_DATABASE_URL"):
        assert termo not in saida, termo


def test_c43_run_id_sanitizado_e_distinto_por_target():
    ids = {ss.default_run_id(ss.SPECS[t], datetime(2026, 8, 18, 6, 5, 0))
           for t in ss.TARGET_ORDER}
    assert len(ids) == 2
    _, _, _, escrita, _ = rodar("ml_cross_company", QUATRO_ML, apply=True,
                                run_id="rodada 1; rm -rf /")
    staged_ids = {s for s, _ in escrita.executed if "INSERT INTO pg_temp" in s}
    rid = ss.sanitize_run_id("rodada 1; rm -rf /")
    for proibido in (" ", ";", "/"):
        assert proibido not in rid


def test_c44_nenhuma_dependencia_nova():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    raizes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raizes.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            raizes.add(node.module.split(".")[0])
    permitidas = {"__future__", "argparse", "hashlib", "os", "sys", "dataclasses",
                  "datetime", "decimal", "pathlib", "psycopg2", "pipelines", "dotenv"}
    assert raizes <= permitidas, raizes - permitidas


def test_c45_zero_shopee_e_zero_airflow():
    codigo = code_only(MODULE_PATH).lower()
    for termo in ("shopee", "airflow", "dag", "schtasks", "scheduledtask"):
        assert termo not in codigo, termo


def test_c46_zero_gmv_oficial_ou_frete_redefinido():
    codigo = code_only(MODULE_PATH).lower()
    assert "sub_total" not in codigo
    assert "frete" not in codigo
    assert "shipping" not in codigo


# ===========================================================================
# Auditoria em audit.source_sync_run (Gate S3, correcao da Task 2/3)
# ===========================================================================
# O health check monitora estas duas fontes pelo audit log. Sem o registro, ele
# reportaria "nenhuma execucao registrada" todo dia e `ok_critical=false`
# permanentemente — ou, pior, um registro de dry-run apareceria como publicacao
# bem-sucedida e mascararia uma tabela que nunca foi carregada.

class AuditConnFake:
    """Conexao de auditoria fake. Separada da de dados de proposito: o registro
    `failed` tem de sobreviver ao rollback da carga."""

    def __init__(self):
        self.inserts = []          # [(source_name, marketplace_id)]
        self.updates = []          # [(status, extracted, loaded, min_d, max_d, error, run_id)]
        self.commits = 0
        self.closed = False
        self._proximo_id = 0
        self._ultimo = ""

    def cursor(self, cursor_factory=None):
        return _AuditCursorFake(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        raise AssertionError("a conexao de auditoria nunca deve sofrer rollback")

    def close(self):
        self.closed = True

    @property
    def status_final(self):
        return self.updates[-1][0] if self.updates else None

    @property
    def erro_final(self):
        return self.updates[-1][5] if self.updates else None


class _AuditCursorFake:
    def __init__(self, conn):
        self.conn = conn
        self._ultimo = ""

    def execute(self, sql, params=None):
        self._ultimo = " ".join(sql.split())
        if "INSERT INTO audit.source_sync_run" in self._ultimo:
            self.conn.inserts.append(tuple(params))
            self.conn._proximo_id += 1
        elif "UPDATE audit.source_sync_run" in self._ultimo:
            self.conn.updates.append(tuple(params))
        else:
            raise AssertionError(f"consulta inesperada na conexao de auditoria: {self._ultimo[:100]}")

    def fetchone(self):
        if "RETURNING sync_run_id" in self._ultimo:
            return (self.conn._proximo_id,)
        return None

    def close(self):
        pass


def rodar_com_audit(target, source_rows, *, apply=False, contagem_inicial=0,
                    falhar_em=None, run_id=None):
    spec = ss.SPECS[target]
    src = ConnFake(source_rows); src.spec = spec
    leitura = ConnFake(contagem_inicial=contagem_inicial); leitura.spec = spec
    escrita = ConnFake(contagem_inicial=contagem_inicial, falhar_em=falhar_em); escrita.spec = spec
    escrita.target = [dict(r) for r in source_rows] if contagem_inicial else []
    audit = AuditConnFake()
    codigo = ss.run_target(
        target, apply=apply, run_id=run_id, now=AGORA,
        source_factory=lambda: src,
        neon_read_factory=lambda: leitura,
        neon_write_factory=lambda: escrita,
        audit_factory=lambda: audit,
    )
    return codigo, audit, escrita


@pytest.mark.parametrize("target,rows", [("ml_cross_company", QUATRO_ML),
                                         ("tiktok_channel_efficiency", MUITOS_CANAIS)])
def test_c47_apply_registra_success_com_o_nome_do_target(target, rows):
    codigo, audit, _ = rodar_com_audit(target, rows, apply=True)
    assert codigo == 0
    assert len(audit.inserts) == 1
    nome, mkt = audit.inserts[0]
    assert nome == ss.SPECS[target].audit_source_name == target
    assert mkt == ss.SPECS[target].marketplace_id
    assert audit.status_final == "success"
    assert audit.erro_final is None
    assert audit.closed is True


def test_c48_success_registra_linhas_extraidas_e_publicadas():
    _, audit, _ = rodar_com_audit("ml_cross_company", QUATRO_ML, apply=True)
    status, extracted, loaded = audit.updates[-1][:3]
    assert (status, extracted, loaded) == ("success", 4, 4)


def test_c49_success_do_target_com_data_registra_min_e_max():
    _, audit, _ = rodar_com_audit("tiktok_channel_efficiency", MUITOS_CANAIS, apply=True)
    _, _, _, min_d, max_d, _, _ = audit.updates[-1]
    assert min_d == min(r["date"] for r in MUITOS_CANAIS)
    assert max_d == max(r["date"] for r in MUITOS_CANAIS)


def test_c50_snapshot_sem_data_nao_fabrica_min_max():
    """`ml_cross_company` nao tem dimensao temporal: preencher a coluna do audit
    com uma data inventada mentiria sobre o grao."""
    _, audit, _ = rodar_com_audit("ml_cross_company", QUATRO_ML, apply=True)
    _, _, _, min_d, max_d, _, _ = audit.updates[-1]
    assert min_d is None and max_d is None
    assert ss.source_date_bounds(ss.SPECS["ml_cross_company"], QUATRO_ML) == (None, None)


@pytest.mark.parametrize("target,rows", [("ml_cross_company", QUATRO_ML),
                                         ("tiktok_channel_efficiency", MUITOS_CANAIS)])
def test_c51_dry_run_nao_registra_nada(target, rows):
    """Diagnostico nao publica; registrar aqui faria o health check ler a fonte
    como saudavel sem que uma linha tivesse sido escrita."""
    codigo, audit, _ = rodar_com_audit(target, rows, apply=False)
    assert codigo == 0
    assert audit.inserts == []
    assert audit.updates == []
    assert audit.closed is False, "nem a conexao de auditoria deve ser aberta"


def test_c52_falha_registra_failed_e_o_registro_sobrevive_ao_rollback():
    with pytest.raises(RuntimeError, match="falha injetada"):
        rodar_com_audit("ml_cross_company", QUATRO_ML, apply=True,
                        falhar_em="DELETE FROM marts.")


def test_c53_falha_grava_status_failed_com_mensagem_sanitizada(monkeypatch):
    """A mensagem nativa do libpq carrega host e IP. O audit log nunca pode
    receber topologia."""
    nativo = ('connection to server at "datamart-interno.exemplo.local" (10.1.2.3), '
              "port 5432 failed: timeout expired")
    spec = ss.SPECS["ml_cross_company"]
    src = ConnFake(QUATRO_ML); src.spec = spec
    leitura = ConnFake(); leitura.spec = spec
    audit = AuditConnFake()

    def escrita_que_explode():
        raise RuntimeError(nativo)

    with pytest.raises(RuntimeError):
        ss.run_target("ml_cross_company", apply=True, now=AGORA,
                      source_factory=lambda: src,
                      neon_read_factory=lambda: leitura,
                      neon_write_factory=escrita_que_explode,
                      audit_factory=lambda: audit)

    assert audit.status_final == "failed"
    erro = audit.erro_final
    assert erro is not None
    for topologia in ("datamart-interno.exemplo.local", "10.1.2.3", "5432"):
        assert topologia not in erro, erro
    assert len(erro) <= 500
    assert audit.closed is True


def test_c54_falha_registra_zero_linhas_carregadas():
    spec = ss.SPECS["ml_cross_company"]
    src = ConnFake(QUATRO_ML); src.spec = spec
    leitura = ConnFake(); leitura.spec = spec
    audit = AuditConnFake()
    with pytest.raises(RuntimeError):
        ss.run_target("ml_cross_company", apply=True, now=AGORA,
                      source_factory=lambda: src,
                      neon_read_factory=lambda: leitura,
                      neon_write_factory=lambda: (_ for _ in ()).throw(RuntimeError("x")),
                      audit_factory=lambda: audit)
    status, extracted, loaded = audit.updates[-1][:3]
    assert status == "failed"
    assert extracted == 4, "a fotografia foi capturada"
    assert loaded == 0, "nada foi publicado"


@pytest.mark.parametrize("target,rows", [("ml_cross_company", QUATRO_ML),
                                         ("tiktok_channel_efficiency", MUITOS_CANAIS)])
def test_c55_uma_execucao_logica_por_target_sem_duplicidade(target, rows):
    _, audit, _ = rodar_com_audit(target, rows, apply=True)
    assert len(audit.inserts) == 1, "mais de um start registrado"
    assert len(audit.updates) == 1, "mais de um finish registrado"


def test_c56_reprovacao_de_volume_E_AUDITADA_como_failed():
    """Correcao terminal: uma chamada `--apply` bloqueada pelo guardrail continua
    sendo tentativa operacional e precisa ficar auditavel.

    Este teste afirmava o oposto ate a correcao — que a reprovacao NAO deveria
    registrar. Estava errado: sem registro, uma fonte truncada reprovaria todo dia
    e o health check seguiria vendo o sucesso anterior como recente, exatamente o
    cenario que a instrumentacao existe para detectar.
    """
    codigo, audit, escrita = rodar_com_audit("ml_cross_company", QUATRO_ML[:2], apply=True)
    assert codigo == ss.EXIT_FALHA
    assert len(audit.inserts) == 1, "a tentativa tem de aparecer como running"
    assert audit.status_final == "failed"
    status, extracted, loaded = audit.updates[-1][:3]
    assert (extracted, loaded) == (2, 0), "extraidas = capturadas; carregadas = 0"
    assert "volume abaixo do piso" in audit.erro_final
    # a conexao de ESCRITA nunca e' aberta neste caminho
    assert escrita.executed == []
    assert escrita.committed is False
    assert audit.closed is True


def test_c57_a_conexao_de_auditoria_e_separada_da_de_dados():
    """Se fossem a mesma, o rollback da carga apagaria o proprio registro de
    falha — a evidencia desapareceria junto com o erro."""
    _, audit, escrita = rodar_com_audit("ml_cross_company", QUATRO_ML, apply=True)
    assert audit is not escrita
    assert not any("audit.source_sync_run" in s for s, _ in escrita.executed)


def test_c58_o_audit_recebe_apenas_insert_e_update_da_propria_tabela():
    _, audit, _ = rodar_com_audit("ml_cross_company", QUATRO_ML, apply=True)
    assert audit.commits == 2, "um commit no start, um no finish"


def test_c59_nomes_do_audit_batem_com_o_health_check():
    """Se o health check monitorar um nome que ninguem grava, ele reportaria
    'nenhuma execucao registrada' para sempre."""
    import pipelines.ops.health_check as hc
    esperados = {s.source_name for s in hc.EXPECTED_SOURCES}
    for target in ss.TARGET_ORDER:
        assert ss.SPECS[target].audit_source_name in esperados, target


def test_c60_marketplace_id_segue_a_convencao_do_repositorio():
    """1=tiktok, 2=ml, 3=shopee, como em sync_produtos.py."""
    assert ss.SPECS["ml_cross_company"].marketplace_id == 2
    assert ss.SPECS["tiktok_channel_efficiency"].marketplace_id == 1


def test_c61_auditoria_nao_introduziu_retry_nem_dependencia():
    codigo = code_only(MODULE_PATH).lower()
    for termo in ("retry", "backoff", "sleep", "max_attempts"):
        assert termo not in codigo, termo
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    raizes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raizes.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            raizes.add(node.module.split(".")[0])
    permitidas = {"__future__", "argparse", "hashlib", "os", "sys", "dataclasses",
                  "datetime", "decimal", "pathlib", "psycopg2", "pipelines", "dotenv"}
    assert raizes <= permitidas, raizes - permitidas


# ===========================================================================
# Correcao terminal — auditoria desde ANTES da primeira leitura operacional
# ===========================================================================
# Finding: `_audit_start` acontecia depois de ler a fonte, ler o destino e validar,
# e depois do `return EXIT_FALHA` do guardrail. Uma tentativa `--apply` que falhasse
# na VPN, na leitura do destino ou no volume nao deixava rastro, e o health check
# seguiria vendo o sucesso do dia anterior como recente.
#
# Os fakes abaixo registram a ORDEM das operacoes numa lista compartilhada, para que
# os testes provem tanto a sequencia do caminho felizmente completo quanto
# EXATAMENTE onde ela foi interrompida em cada falha.

SEQ_COMPLETA = ["audit_start", "source", "target_read", "validation", "write", "audit_finish"]


class Ordem(list):
    """Registro compartilhado da sequencia. `str()` fica legivel na falha."""

    def __str__(self):
        return " -> ".join(self)


def _fakes_ordenados(target, source_rows, *, contagem_inicial=0,
                     falhar_source_open=False, falhar_capture=False,
                     falhar_target_read=False, falhar_write_open=False,
                     falhar_em=None, falhar_audit_start=False):
    """Monta as quatro conexoes fake compartilhando um registro de ordem."""
    spec = ss.SPECS[target]
    ordem = Ordem()

    class SrcConn(ConnFake):
        def cursor(self, cursor_factory=None):
            ordem.append("source")
            if falhar_capture:
                raise RuntimeError("falha ao materializar a fotografia")
            return CursorFake(self)

    class LeituraConn(ConnFake):
        def cursor(self, cursor_factory=None):
            ordem.append("target_read")
            if falhar_target_read:
                raise RuntimeError("falha ao consultar o destino")
            return CursorFake(self)

    class EscritaConn(ConnFake):
        def cursor(self, cursor_factory=None):
            if "write" not in ordem:
                ordem.append("write")
            return CursorFake(self)

    class AuditConn(AuditConnFake):
        def cursor(self, cursor_factory=None):
            return _AuditCursorOrdenado(self, ordem, falhar_audit_start)

    src = SrcConn(source_rows); src.spec = spec
    leitura = LeituraConn(contagem_inicial=contagem_inicial); leitura.spec = spec
    escrita = EscritaConn(contagem_inicial=contagem_inicial, falhar_em=falhar_em)
    escrita.spec = spec
    escrita.target = [dict(r) for r in source_rows] if contagem_inicial else []
    audit = AuditConn()
    abertas = {"source": 0, "leitura": 0, "escrita": 0, "audit": 0}

    def f_source():
        abertas["source"] += 1
        if falhar_source_open:
            raise RuntimeError("falha ao abrir a fonte")
        return src

    def f_leitura():
        abertas["leitura"] += 1
        return leitura

    def f_escrita():
        abertas["escrita"] += 1
        if falhar_write_open:
            raise RuntimeError("falha ao abrir a conexao de escrita")
        return escrita

    def f_audit():
        abertas["audit"] += 1
        return audit

    return spec, ordem, audit, escrita, abertas, f_source, f_leitura, f_escrita, f_audit


class _AuditCursorOrdenado(_AuditCursorFake):
    def __init__(self, conn, ordem, falhar_start):
        super().__init__(conn)
        self._ordem = ordem
        self._falhar_start = falhar_start

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        if "INSERT INTO audit.source_sync_run" in norm:
            self._ordem.append("audit_start")
            if self._falhar_start:
                raise RuntimeError("falha ao registrar o inicio da auditoria")
        elif "UPDATE audit.source_sync_run" in norm:
            self._ordem.append("audit_finish")
        super().execute(sql, params)


@contextlib.contextmanager
def _espiao_de_validacao(ordem):
    """Registra o passo `validation` na sequencia.

    A validacao e' funcao PURA, nao operacao de conexao — nenhum fake a observaria.
    Espionar a chamada e' o que permite provar a posicao dela na sequencia exigida:
    audit_start -> source -> target_read -> validation -> write -> audit_finish.
    """
    original = ss.validate_source_rows

    def espia(*a, **kw):
        ordem.append("validation")
        return original(*a, **kw)

    ss.validate_source_rows = espia
    try:
        yield
    finally:
        ss.validate_source_rows = original


def rodar_ordenado(target, source_rows, *, apply=True, **kw):
    (spec, ordem, audit, escrita, abertas,
     f_source, f_leitura, f_escrita, f_audit) = _fakes_ordenados(target, source_rows, **kw)
    with _espiao_de_validacao(ordem):
        codigo = ss.run_target(
            target, apply=apply, now=AGORA,
            source_factory=f_source, neon_read_factory=f_leitura,
            neon_write_factory=f_escrita, audit_factory=f_audit,
        )
    return codigo, ordem, audit, escrita, abertas


def _chamar_ordenado(target, source_rows, *, apply=True, **kw):
    """Como `rodar_ordenado`, mas deixa a excecao subir para o teste."""
    (spec, ordem, audit, escrita, abertas,
     f_source, f_leitura, f_escrita, f_audit) = _fakes_ordenados(target, source_rows, **kw)
    ctx = {"ordem": ordem, "audit": audit, "escrita": escrita, "abertas": abertas}

    def chamar():
        with _espiao_de_validacao(ordem):
            return ss.run_target(target, apply=apply, now=AGORA,
                                 source_factory=f_source, neon_read_factory=f_leitura,
                                 neon_write_factory=f_escrita, audit_factory=f_audit)

    return chamar, ctx


def _levanta(target, source_rows, *, apply=True, esperado=RuntimeError, **kw):
    chamar, ctx = _chamar_ordenado(target, source_rows, apply=apply, **kw)
    with pytest.raises(esperado):
        chamar()
    return ctx["ordem"], ctx["audit"], ctx["escrita"], ctx["abertas"]


# --- 1 e 2. diagnostico nunca audita ---------------------------------------

@pytest.mark.parametrize("target,rows", [("ml_cross_company", QUATRO_ML),
                                         ("tiktok_channel_efficiency", MUITOS_CANAIS)])
def test_c62_diagnostico_bem_sucedido_nao_audita(target, rows):
    codigo, ordem, audit, escrita, abertas = rodar_ordenado(target, rows, apply=False)
    assert codigo == 0
    assert "audit_start" not in ordem and "audit_finish" not in ordem
    assert abertas["audit"] == 0, "nem a conexao de auditoria deve ser aberta"
    assert abertas["escrita"] == 0
    assert list(ordem) == ["source", "target_read", "validation"], str(ordem)


def test_c63_diagnostico_reprovado_nao_audita():
    codigo, ordem, audit, _, abertas = rodar_ordenado(
        "ml_cross_company", QUATRO_ML[:2], apply=False)
    assert codigo == ss.EXIT_FALHA
    assert abertas["audit"] == 0
    assert audit.inserts == [] and audit.updates == []


# --- 3. apply bem-sucedido: sequencia completa -----------------------------

@pytest.mark.parametrize("target,rows", [("ml_cross_company", QUATRO_ML),
                                         ("tiktok_channel_efficiency", MUITOS_CANAIS)])
def test_c64_apply_bem_sucedido_segue_a_sequencia_completa(target, rows):
    codigo, ordem, audit, _, abertas = rodar_ordenado(target, rows)
    assert codigo == 0
    assert list(ordem) == SEQ_COMPLETA, str(ordem)
    assert len(audit.inserts) == 1 and len(audit.updates) == 1
    assert audit.status_final == "success"


def test_c65_audit_start_e_a_primeira_operacao_do_apply():
    _, ordem, _, _, _ = rodar_ordenado("ml_cross_company", QUATRO_ML)
    assert ordem[0] == "audit_start", str(ordem)
    assert ordem.index("audit_start") < ordem.index("source")
    assert ordem.index("audit_start") < ordem.index("target_read")
    assert ordem.index("audit_start") < ordem.index("write")


# --- 4/5/6. falhas ANTES da validacao, agora auditadas ---------------------

def test_c66_falha_ao_abrir_a_fonte_registra_running_e_failed():
    ordem, audit, escrita, abertas = _levanta("ml_cross_company", QUATRO_ML,
                                              falhar_source_open=True)
    assert list(ordem) == ["audit_start", "audit_finish"], str(ordem)
    assert audit.status_final == "failed"
    status, extracted, loaded = audit.updates[-1][:3]
    assert (extracted, loaded) == (0, 0), "nada foi capturado"
    assert abertas["escrita"] == 0
    assert audit.closed is True


def test_c67_falha_ao_capturar_a_fotografia_registra_failed():
    ordem, audit, _, abertas = _levanta("ml_cross_company", QUATRO_ML,
                                        falhar_capture=True)
    assert list(ordem) == ["audit_start", "source", "audit_finish"], str(ordem)
    assert audit.status_final == "failed"
    assert audit.updates[-1][1:3] == (0, 0)
    assert abertas["escrita"] == 0


def test_c68_falha_ao_consultar_o_destino_registra_failed():
    ordem, audit, _, abertas = _levanta("ml_cross_company", QUATRO_ML,
                                        falhar_target_read=True)
    assert list(ordem) == ["audit_start", "source", "target_read", "audit_finish"], str(ordem)
    assert "validation" not in ordem, "a validacao nem chegou a rodar"
    assert audit.status_final == "failed"
    assert abertas["escrita"] == 0, "a escrita nunca foi tentada"


# --- 7/8. guardrail: EXIT_FALHA auditado, sem abrir escrita ----------------

def test_c69_volume_abaixo_do_piso_registra_failed_e_nao_abre_escrita():
    codigo, ordem, audit, escrita, abertas = rodar_ordenado(
        "ml_cross_company", QUATRO_ML[:2])
    assert codigo == ss.EXIT_FALHA
    assert list(ordem) == ["audit_start", "source", "target_read", "validation",
                           "audit_finish"], str(ordem)
    assert audit.status_final == "failed"
    assert audit.updates[-1][1:3] == (2, 0), "extraidas=2, carregadas=0"
    assert abertas["escrita"] == 0
    assert escrita.executed == []


def test_c70_duplicidade_registra_failed_sem_publicar():
    dup = QUATRO_ML + [linha_ml("kokeshi")]
    codigo, ordem, audit, escrita, abertas = rodar_ordenado("ml_cross_company", dup)
    assert codigo == ss.EXIT_FALHA
    assert "write" not in ordem, str(ordem)
    assert audit.status_final == "failed"
    assert "duplicada" in audit.erro_final
    assert abertas["escrita"] == 0


def test_c71_chave_nula_registra_failed_sem_publicar():
    rows = [linha_ml(b) for b in ("barbours", "kokeshi", "lescent")] + [linha_ml(None)]
    codigo, ordem, audit, _, abertas = rodar_ordenado("ml_cross_company", rows)
    assert codigo == ss.EXIT_FALHA
    assert audit.status_final == "failed"
    assert "chave nula" in audit.erro_final
    assert abertas["escrita"] == 0


def test_c72_valor_invalido_registra_failed_sem_publicar():
    rows = [linha_ml(b) for b in ("barbours", "kokeshi", "lescent")]
    rows.append(linha_ml("rituaria", total_buyers=Decimal("NaN")))
    codigo, ordem, audit, _, abertas = rodar_ordenado("ml_cross_company", rows)
    assert codigo == ss.EXIT_FALHA
    assert audit.status_final == "failed"
    assert "NaN" in audit.erro_final
    assert abertas["escrita"] == 0


def test_c73_descricao_da_reprovacao_e_limitada_e_sem_payload():
    codigo, _, audit, _, _ = rodar_ordenado("ml_cross_company", QUATRO_ML[:2])
    erro = audit.erro_final
    assert len(erro) <= ss.MAX_AUDIT_ERROR_CHARS
    assert erro.startswith("fonte reprovada pelo guardrail")
    # nenhum valor de linha vaza: a mensagem carrega contagens e nomes de coluna
    for valor in ("1000", "133.34", "barbours"):
        assert valor not in erro, f"{valor!r} vazou na descricao"


# --- 9/10. falhas na publicacao e na reconciliacao ------------------------

def test_c74_falha_ao_abrir_a_conexao_de_escrita_registra_failed():
    ordem, audit, _, abertas = _levanta("ml_cross_company", QUATRO_ML,
                                        falhar_write_open=True)
    assert list(ordem) == ["audit_start", "source", "target_read", "validation",
                           "audit_finish"], str(ordem)
    assert audit.status_final == "failed"
    assert abertas["escrita"] == 1, "a fabrica foi chamada e levantou"


def test_c75_falha_na_publicacao_registra_failed():
    ordem, audit, escrita, _ = _levanta("ml_cross_company", QUATRO_ML,
                                        falhar_em="DELETE FROM marts.")
    assert list(ordem) == SEQ_COMPLETA, str(ordem)
    assert audit.status_final == "failed"
    assert audit.updates[-1][2] == 0, "zero linhas carregadas"
    assert escrita.rolled_back is True
    assert audit.closed is True, "o registro de falha sobrevive ao rollback"


def test_c76_falha_na_reconciliacao_registra_failed(monkeypatch):
    """Staging que nao reflete a fotografia: aborta antes do DELETE, e a tentativa
    fica auditada."""
    def _vazio(cur, sql, batch, page_size=500):
        cur.conn.staged = []
    monkeypatch.setattr(ss, "execute_values", _vazio)
    ordem, audit, escrita, _ = _levanta("ml_cross_company", QUATRO_ML)
    assert audit.status_final == "failed"
    assert "divergiu" in audit.erro_final
    assert escrita.rolled_back is True


# --- 11. sanitizacao da mensagem ------------------------------------------

def test_c77_mensagem_gravada_sanitiza_dsn_senha_host_ip_e_caminho():
    nativo = ('connection to server at "datamart-interno.exemplo.local" (10.1.2.3), '
              "port 5432 failed: FATAL: password authentication failed for user "
              '"segredouser" — postgresql://segredouser:S3nhaSecreta@ep-fake.neon.tech/db '
              r"(config em C:\caminho\do\checkout\.env)")
    spec = ss.SPECS["ml_cross_company"]
    audit = AuditConnFake()

    def fonte_que_explode():
        raise RuntimeError(nativo)

    with pytest.raises(RuntimeError):
        ss.run_target("ml_cross_company", apply=True, now=AGORA,
                      source_factory=fonte_que_explode,
                      neon_read_factory=lambda: ConnFake(),
                      neon_write_factory=lambda: ConnFake(),
                      audit_factory=lambda: audit)

    erro = audit.erro_final
    assert audit.status_final == "failed"
    assert erro is not None
    for sensivel in ("datamart-interno.exemplo.local", "10.1.2.3", "5432",
                     "S3nhaSecreta", "segredouser", "ep-fake.neon.tech",
                     "C:\\caminho\\do\\checkout"):
        assert sensivel not in erro, f"{sensivel!r} vazou: {erro!r}"
    assert len(erro) <= ss.MAX_AUDIT_ERROR_CHARS


# --- 12. fechamento da conexao em todos os caminhos -----------------------

def test_c78_auditoria_fecha_no_sucesso():
    _, _, audit, _, _ = rodar_ordenado("ml_cross_company", QUATRO_ML)
    assert audit.closed is True


def test_c79_auditoria_fecha_na_falha():
    _, audit, _, _ = _levanta("ml_cross_company", QUATRO_ML, falhar_em="DELETE FROM marts.")
    assert audit.closed is True


def test_c80_auditoria_fecha_no_retorno_por_validacao():
    _, _, audit, _, _ = rodar_ordenado("ml_cross_company", QUATRO_ML[:2])
    assert audit.closed is True


# --- 13. exatamente um par por tentativa ---------------------------------

@pytest.mark.parametrize("cenario", [
    {}, {"falhar_source_open": True}, {"falhar_capture": True},
    {"falhar_target_read": True}, {"falhar_write_open": True},
    {"falhar_em": "DELETE FROM marts."},
])
def test_c81_exatamente_um_start_e_um_finish_por_tentativa(cenario):
    if cenario:
        _, audit, _, _ = _levanta("ml_cross_company", QUATRO_ML, **cenario)
    else:
        _, _, audit, _, _ = rodar_ordenado("ml_cross_company", QUATRO_ML)
    assert len(audit.inserts) == 1, f"{len(audit.inserts)} starts"
    assert len(audit.updates) == 1, f"{len(audit.updates)} finishes"


def test_c82_reprovacao_tambem_produz_exatamente_um_par():
    _, _, audit, _, _ = rodar_ordenado("ml_cross_company", QUATRO_ML[:2])
    assert len(audit.inserts) == 1 and len(audit.updates) == 1


# --- 14/15. datas no audit -----------------------------------------------

@pytest.mark.parametrize("cenario", [
    {}, {"falhar_capture": True}, {"falhar_target_read": True},
])
def test_c83_ml_nunca_recebe_min_max_fabricados(cenario):
    if cenario:
        _, audit, _, _ = _levanta("ml_cross_company", QUATRO_ML, **cenario)
    else:
        _, _, audit, _, _ = rodar_ordenado("ml_cross_company", QUATRO_ML)
    _, _, _, dmin, dmax, _, _ = audit.updates[-1]
    assert dmin is None and dmax is None


def test_c84_tiktok_preserva_min_max_quando_capturou():
    _, _, audit, _, _ = rodar_ordenado("tiktok_channel_efficiency", MUITOS_CANAIS)
    _, _, _, dmin, dmax, _, _ = audit.updates[-1]
    assert dmin == min(r["date"] for r in MUITOS_CANAIS)
    assert dmax == max(r["date"] for r in MUITOS_CANAIS)


@pytest.mark.parametrize("cenario", [{"falhar_source_open": True}, {"falhar_capture": True}])
def test_c85_tiktok_nao_fabrica_datas_quando_a_captura_falha(cenario):
    _, audit, _, _ = _levanta("tiktok_channel_efficiency", MUITOS_CANAIS, **cenario)
    _, _, _, dmin, dmax, _, _ = audit.updates[-1]
    assert dmin is None and dmax is None, "sem captura nao ha data a registrar"


def test_c86_tiktok_preserva_min_max_na_reprovacao_por_volume():
    """A captura deu certo; foi o guardrail que reprovou. As datas capturadas sao
    informacao valida e ficam registradas."""
    poucos = MUITOS_CANAIS[:3100]
    codigo, _, audit, _, _ = rodar_ordenado("tiktok_channel_efficiency", poucos,
                                            contagem_inicial=5000)
    assert codigo == ss.EXIT_FALHA
    _, _, _, dmin, dmax, _, _ = audit.updates[-1]
    assert dmin == min(r["date"] for r in poucos)
    assert dmax == max(r["date"] for r in poucos)


# --- 16. falha do proprio _audit_start ------------------------------------

def test_c87_falha_do_audit_start_interrompe_antes_da_fonte_e_nao_tenta_finish():
    """Unico caso inevitavel sem registro: nao ha como registrar a falha do
    proprio mecanismo de registro. Documentado no modulo."""
    ordem, audit, _, abertas = _levanta("ml_cross_company", QUATRO_ML,
                                        falhar_audit_start=True)
    assert list(ordem) == ["audit_start"], str(ordem)
    assert audit.updates == [], "nenhum finish deve ser tentado"
    assert abertas["source"] == 0, "a fonte nunca foi aberta"
    assert abertas["escrita"] == 0
    assert audit.closed is True, "a conexao de auditoria e' fechada mesmo assim"


def test_c88_o_modulo_documenta_o_caso_sem_registro():
    texto = MODULE_PATH.read_text(encoding="utf-8")
    assert "unico caso em que nao havera registro" in texto


def test_c89_nenhum_retry_foi_introduzido_pela_correcao():
    codigo = code_only(MODULE_PATH).lower()
    for termo in ("retry", "backoff", "sleep", "max_attempts", "while true"):
        assert termo not in codigo, termo


def test_c90_a_excecao_original_nunca_e_engolida():
    """O `raise` nu preserva a excecao original; o audit log recebe a versao
    sanitizada, e quem chamou recebe a real, com a mensagem intacta."""
    chamar, ctx = _chamar_ordenado("ml_cross_company", QUATRO_ML, falhar_source_open=True)
    with pytest.raises(RuntimeError, match="falha ao abrir a fonte"):
        chamar()
    assert ctx["audit"].status_final == "failed"


# ===========================================================================
# Patch final pre-Task 3 — exatamente uma TENTATIVA de finish
# ===========================================================================
# A marca `finish_attempted` significa "uma tentativa de finalizacao ja ocorreu" e
# e' escrita ANTES da chamada. Com a semantica anterior ("finalizado com sucesso",
# marcada DEPOIS), uma excecao dentro do proprio `_audit_finish` deixava a marca em
# False, o `except` chamava `_audit_finish` de novo, e a mesma tentativa produzia
# DOIS encerramentos — inclusive reclassificando como `failed` um snapshot ja
# publicado e reconciliado.


class AuditQueFalhaNoFinish(AuditConnFake):
    """Registra `running` normalmente e explode no `UPDATE` de encerramento."""

    def __init__(self):
        super().__init__()
        self.tentativas_finish = 0

    def cursor(self, cursor_factory=None):
        return _CursorQueFalhaNoFinish(self)


class _CursorQueFalhaNoFinish(_AuditCursorFake):
    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        if "UPDATE audit.source_sync_run" in norm:
            self.conn.tentativas_finish += 1
            raise RuntimeError("falha ao gravar o encerramento da auditoria")
        super().execute(sql, params)


def _rodar_com_audit_quebrado(target, source_rows, *, contagem_inicial=0,
                              falhar_em=None, publicacoes=None):
    spec = ss.SPECS[target]
    src = ConnFake(source_rows); src.spec = spec
    leitura = ConnFake(contagem_inicial=contagem_inicial); leitura.spec = spec
    escrita = ConnFake(contagem_inicial=contagem_inicial, falhar_em=falhar_em)
    escrita.spec = spec
    escrita.target = [dict(r) for r in source_rows] if contagem_inicial else []
    audit = AuditQueFalhaNoFinish()
    abertas = {"escrita": 0}

    def f_escrita():
        abertas["escrita"] += 1
        return escrita

    def chamar():
        return ss.run_target(target, apply=True, now=AGORA,
                             source_factory=lambda: src,
                             neon_read_factory=lambda: leitura,
                             neon_write_factory=f_escrita,
                             audit_factory=lambda: audit)

    return chamar, audit, escrita, abertas


# --- 1. finish(success) falha ----------------------------------------------

def test_c91_falha_ao_registrar_success_nao_gera_segunda_tentativa():
    """Publicacao concluida + falha ao registrar `success`: a falha de auditoria
    propaga, e NENHUM segundo encerramento e' tentado."""
    chamar, audit, escrita, abertas = _rodar_com_audit_quebrado(
        "ml_cross_company", QUATRO_ML)
    with pytest.raises(RuntimeError, match="falha ao gravar o encerramento"):
        chamar()
    assert audit.tentativas_finish == 1, "houve mais de uma tentativa de finish"
    assert len(audit.inserts) == 1, "o start continua exatamente um"
    assert audit.updates == [], "nenhum finish chegou a ser gravado"
    assert audit.closed is True


def test_c92_snapshot_publicado_nao_e_reexecutado_nem_reclassificado():
    """O dado ja foi publicado e reconciliado. A falha de auditoria nao republica
    nem desfaz nada, e nao existe um `failed` posterior sobre um snapshot bom."""
    chamar, audit, escrita, abertas = _rodar_com_audit_quebrado(
        "ml_cross_company", QUATRO_ML)
    with pytest.raises(RuntimeError):
        chamar()
    assert abertas["escrita"] == 1, "a publicacao rodou uma unica vez"
    assert escrita.committed is True, "o commit dos dados permanece"
    assert escrita.rolled_back is False, "o snapshot publicado nao foi desfeito"
    inserts_no_alvo = [s for s, _ in escrita.executed if s.startswith("INSERT INTO marts.")]
    assert len(inserts_no_alvo) == 1, "nao houve republicacao"
    assert audit.tentativas_finish == 1


def test_c93_a_excecao_propagada_no_sucesso_e_a_da_auditoria():
    """Nao havia erro de dados: o unico erro real e' o da auditoria, e e' ele que
    quem chama recebe."""
    chamar, _, _, _ = _rodar_com_audit_quebrado("ml_cross_company", QUATRO_ML)
    with pytest.raises(RuntimeError) as info:
        chamar()
    assert "encerramento da auditoria" in str(info.value)
    assert info.value.__cause__ is None, "nao ha erro de carga para ser causa"


# --- 2. falha de dados + falha ao registrar failed -------------------------

def test_c94_falha_de_publicacao_e_de_auditoria_preserva_o_erro_da_carga():
    """Duas falhas, nenhuma terceira operacao. O erro PRINCIPAL continua sendo o
    da carga — e' ele que descreve o que aconteceu com o dado."""
    chamar, audit, escrita, _ = _rodar_com_audit_quebrado(
        "ml_cross_company", QUATRO_ML, falhar_em="DELETE FROM marts.")
    with pytest.raises(RuntimeError) as info:
        chamar()
    assert "falha injetada" in str(info.value), "o erro da carga tem de ser o principal"
    assert audit.tentativas_finish == 1, "uma unica tentativa de finish"
    assert escrita.rolled_back is True


def test_c95_falha_da_auditoria_aparece_como_causa_sanitizada():
    chamar, _, _, _ = _rodar_com_audit_quebrado(
        "ml_cross_company", QUATRO_ML, falhar_em="DELETE FROM marts.")
    with pytest.raises(RuntimeError) as info:
        chamar()
    causa = info.value.__cause__
    assert causa is not None, "a falha da auditoria deve aparecer como causa"
    assert "encerramento da auditoria" in str(causa)
    assert len(str(causa)) <= ss.MAX_AUDIT_ERROR_CHARS + 60


def test_c96_causa_da_auditoria_nao_carrega_segredo():
    """A causa passa pelo mesmo sanitizador: nada de DSN, senha, host ou IP."""
    spec = ss.SPECS["ml_cross_company"]
    nativo = ('connection to server at "interno.exemplo.local" (10.9.8.7), port 5432 '
              "failed: postgresql://u:S3nha@ep-fake.neon.tech/db")

    class AuditComSegredo(AuditConnFake):
        def cursor(self, cursor_factory=None):
            return _CursorComSegredo(self)

    class _CursorComSegredo(_AuditCursorFake):
        def execute(self, sql, params=None):
            if "UPDATE audit.source_sync_run" in " ".join(sql.split()):
                raise RuntimeError(nativo)
            super().execute(sql, params)

    src = ConnFake(QUATRO_ML); src.spec = spec
    leitura = ConnFake(); leitura.spec = spec
    escrita = ConnFake(falhar_em="DELETE FROM marts."); escrita.spec = spec
    audit = AuditComSegredo()
    with pytest.raises(RuntimeError) as info:
        ss.run_target("ml_cross_company", apply=True, now=AGORA,
                      source_factory=lambda: src,
                      neon_read_factory=lambda: leitura,
                      neon_write_factory=lambda: escrita,
                      audit_factory=lambda: audit)
    texto = str(info.value) + "|" + str(info.value.__cause__)
    for sensivel in ("interno.exemplo.local", "10.9.8.7", "5432", "S3nha", "ep-fake.neon.tech"):
        assert sensivel not in texto, f"{sensivel!r} vazou: {texto!r}"


# --- 3. guardrail + falha ao registrar failed ------------------------------

def test_c97_guardrail_com_auditoria_quebrada_propaga_em_vez_de_exit_falha():
    """`EXIT_FALHA` nao pode ser devolvido como se a auditoria tivesse fechado
    normalmente: quem chama precisa saber que a tentativa ficou sem encerramento."""
    chamar, audit, escrita, abertas = _rodar_com_audit_quebrado(
        "ml_cross_company", QUATRO_ML[:2])
    with pytest.raises(RuntimeError, match="falha ao gravar o encerramento"):
        chamar()
    assert audit.tentativas_finish == 1
    assert abertas["escrita"] == 0, "a escrita nunca e' aberta no caminho do guardrail"
    assert escrita.executed == []
    assert audit.closed is True


# --- 4. uma tentativa de finish em todos os caminhos com audit sadio -------

@pytest.mark.parametrize("cenario,levanta", [
    ({}, False),
    ({"falhar_source_open": True}, True),
    ({"falhar_target_read": True}, True),
    ({"falhar_write_open": True}, True),
    ({"falhar_em": "DELETE FROM marts."}, True),
])
def test_c98_um_unico_finish_em_todos_os_caminhos(cenario, levanta):
    if levanta:
        _, audit, _, _ = _levanta("ml_cross_company", QUATRO_ML, **cenario)
    else:
        _, _, audit, _, _ = rodar_ordenado("ml_cross_company", QUATRO_ML, **cenario)
    assert len(audit.inserts) == 1
    assert len(audit.updates) == 1


def test_c99_guardrail_com_audit_sadio_continua_com_um_finish_e_exit_falha():
    codigo, _, audit, _, abertas = rodar_ordenado("ml_cross_company", QUATRO_ML[:2])
    assert codigo == ss.EXIT_FALHA
    assert len(audit.inserts) == 1 and len(audit.updates) == 1
    assert abertas["escrita"] == 0


def test_c100_semantica_da_marca_e_tentativa_nao_sucesso():
    """Guarda estrutural: a marca e' escrita ANTES da chamada. Se voltar a ser
    escrita depois, o bug do finish duplo reaparece."""
    import inspect
    bruto = inspect.getsource(ss.run_target)
    # Sem comentarios: o comentario do modulo EXPLICA que a marca antiga
    # (`finalizado`) foi substituida, e um grep no texto cru casaria com a
    # explicacao em vez de com o codigo.
    src = chr(10).join(l.split("#", 1)[0] for l in bruto.splitlines())
    assert "finalizado" not in src, "a marca antiga voltou ao codigo"
    assert "finish_attempted" in src
    linhas = [l.strip() for l in src.splitlines() if l.strip()]
    for i, l in enumerate(linhas):
        if l.startswith("_audit_finish("):
            anteriores = linhas[max(0, i - 3):i]
            assert any("finish_attempted = True" in a for a in anteriores), (
                f"chamada em {i} sem a marca imediatamente antes: {anteriores}")


def test_c101_dry_run_continua_sem_start_e_sem_finish():
    codigo, ordem, audit, _, abertas = rodar_ordenado(
        "ml_cross_company", QUATRO_ML, apply=False)
    assert codigo == 0
    assert audit.inserts == [] and audit.updates == []
    assert abertas["audit"] == 0


def test_c102_zero_retry_de_auditoria():
    codigo = code_only(MODULE_PATH).lower()
    for termo in ("retry", "backoff", "sleep", "max_attempts"):
        assert termo not in codigo, termo
    # nenhum laco em torno de _audit_finish
    import re
    assert not re.search(r"(for|while)[^\n]*\n[^\n]*_audit_finish", code_only(MODULE_PATH))


# ===========================================================================
# Patch final pre-Task 3 — parse_args antes de load_dotenv
# ===========================================================================

@pytest.fixture
def espioes_cli(monkeypatch):
    """Espia `load_dotenv` e `run_target`, mais as quatro fabricas de conexao, para
    provar que `--help` e argumento invalido nao produzem NENHUM efeito."""
    import dotenv
    chamadas = {"load_dotenv": 0, "run_target": 0, "conexao": 0}

    monkeypatch.setattr(dotenv, "load_dotenv",
                        lambda *a, **k: chamadas.__setitem__("load_dotenv",
                                                             chamadas["load_dotenv"] + 1))
    monkeypatch.setattr(ss, "run_target",
                        lambda *a, **k: chamadas.__setitem__("run_target",
                                                             chamadas["run_target"] + 1) or 0)

    def _proibido(*a, **k):
        chamadas["conexao"] += 1
        raise AssertionError("nenhuma conexao deve ser aberta neste caminho")

    for nome in ("_datamart_readonly", "_neon_writable", "_neon_audit"):
        monkeypatch.setattr(ss, nome, _proibido)
    return chamadas


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_c103_help_sai_zero_sem_ler_env_nem_abrir_conexao(flag, espioes_cli, capsys):
    with pytest.raises(SystemExit) as info:
        ss.main([flag])
    assert info.value.code == 0
    assert espioes_cli == {"load_dotenv": 0, "run_target": 0, "conexao": 0}
    saida = capsys.readouterr().out
    assert "--target" in saida and "--apply" in saida


def test_c104_argumento_invalido_sai_dois_sem_efeito_colateral(espioes_cli):
    with pytest.raises(SystemExit) as info:
        ss.main(["--flag-que-nao-existe"])
    assert info.value.code == 2
    assert espioes_cli == {"load_dotenv": 0, "run_target": 0, "conexao": 0}


def test_c105_target_invalido_sai_dois_sem_efeito_colateral(espioes_cli):
    with pytest.raises(SystemExit) as info:
        ss.main(["--target", "inexistente"])
    assert info.value.code == 2
    assert espioes_cli == {"load_dotenv": 0, "run_target": 0, "conexao": 0}


def test_c106_target_ausente_sai_dois_sem_efeito_colateral(espioes_cli):
    with pytest.raises(SystemExit) as info:
        ss.main([])
    assert info.value.code == 2
    assert espioes_cli["load_dotenv"] == 0


def test_c107_execucao_valida_ainda_carrega_env_e_chama_run_target(espioes_cli, capsys):
    codigo = ss.main(["--target", "ml_cross_company"])
    assert codigo == 0
    assert espioes_cli["load_dotenv"] == 1, "a execucao valida ainda le a configuracao"
    assert espioes_cli["run_target"] == 1
    assert espioes_cli["conexao"] == 0, "as conexoes vem de run_target, aqui espionado"
    assert "MODO DIAGNOSTICO" in capsys.readouterr().out


def test_c108_apply_valido_tambem_preserva_a_ordem(espioes_cli, capsys):
    codigo = ss.main(["--target", "tiktok_channel_efficiency", "--apply"])
    assert codigo == 0
    assert espioes_cli["load_dotenv"] == 1
    assert espioes_cli["run_target"] == 1
    assert "MODO DIAGNOSTICO" not in capsys.readouterr().out


def test_c109_parse_args_vem_antes_de_load_dotenv_no_codigo():
    """Guarda estrutural da ordem, para que uma edicao futura nao a inverta."""
    import inspect
    src = inspect.getsource(ss.main)
    i_parse = src.index("parse_args(")
    i_dotenv = src.index("load_dotenv(")
    assert i_parse < i_dotenv, "load_dotenv voltou a ser chamado antes do parse"


def test_c110_nenhuma_flag_nova_foi_criada():
    acoes = {a.dest for a in ss.build_parser()._actions}
    assert acoes == {"help", "target", "apply", "run_id"}, acoes
