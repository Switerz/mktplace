"""Gate SH-API-2B — scoped replace, dry-run e a contraprova do UPSERT.

Nenhum teste depende de valor real de GMV nem de ID de pedido real: todos os
ids sao sinteticos ("P1"...). Nenhum teste afirma que os arquivos disputados
sao legitimos — isso e' resultado da arbitragem da Fase 2, nao de teste.

IMPORTANTE: importar o MODULO, nunca os nomes — outro modulo desta suite
chama importlib.reload() no loader (ver test_load_shopee_products_numeric.py).
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from etl import backfill_shopee_products as bf


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeMart:
    """Destino em memoria com as duas semanticas de publicacao, para provar a
    diferenca entre elas. A chave e' a chave real da tabela."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = {self._k(r): dict(r) for r in (rows or [])}
        self.truncated = False

    @staticmethod
    def _k(r):
        return (r["ref_month"], r["brand"], r.get("sku_ref_key", ""), r["product_name"])

    # --- semantica ANTIGA: apenas UPSERT
    def upsert(self, staging: pd.DataFrame) -> None:
        for _, r in staging.iterrows():
            self.rows[self._k(r)] = dict(r)

    # --- semantica NOVA: scoped replace
    def scoped_replace(self, staging: pd.DataFrame, scopes) -> None:
        alvo = set(scopes)
        for k in [k for k in self.rows if (k[1], k[0]) in alvo]:
            del self.rows[k]
        for _, r in staging.iterrows():
            self.rows[self._k(r)] = dict(r)

    def keys(self):
        return set(self.rows)

    def gmv(self):
        return round(sum(float(r["gmv"]) for r in self.rows.values()), 2)


class FakeExecutor:
    """Executor injetavel de apply_scoped_replace. Registra a ordem real das
    operacoes para os testes de rollback."""

    def __init__(self, mart: FakeMart, *, fail_on: str | None = None,
                 fail_rollback: bool = False, count_override: int | None = None) -> None:
        self.mart, self.fail_on = mart, fail_on
        self.fail_rollback, self.count_override = fail_rollback, count_override
        self.calls: list[str] = []
        self.backup_committed = False
        self._snapshot: dict | None = None
        self._staging: pd.DataFrame | None = None
        self._scopes = None

    def _maybe_fail(self, step):
        if self.fail_on == step:
            raise RuntimeError(f"falha injetada em {step}")

    def begin(self):
        self.calls.append("begin"); self._snapshot = dict(self.mart.rows); self._maybe_fail("begin")

    def backup_scope(self, scopes):
        # Gate SH-API-2E1: o backup roda ANTES de begin(), em transacao propria,
        # e so' conta como feito depois de commitado.
        self.calls.append("backup"); self._scopes = scopes; self._maybe_fail("backup")
        self._snapshot = dict(self.mart.rows)
        self.backup_committed = True
        return {"table": "bkp_fake", "rows": len(self.mart.rows), "checksum": "fake"}

    def delete_scope(self, scopes):
        self.calls.append("delete")
        alvo = set(scopes)
        for k in [k for k in self.mart.rows if (k[1], k[0]) in alvo]:
            del self.mart.rows[k]
        self._maybe_fail("delete")

    def insert_rows(self, rows):
        self.calls.append("insert"); self._staging = rows
        self._maybe_fail("insert")
        for _, r in rows.iterrows():
            self.mart.rows[FakeMart._k(r)] = dict(r)

    def count_scope(self, scopes):
        self.calls.append("count")
        if self.count_override is not None:
            return self.count_override
        alvo = set(scopes)
        return sum(1 for k in self.mart.rows if (k[1], k[0]) in alvo)

    def commit(self):
        self.calls.append("commit"); self._maybe_fail("commit")

    def rollback(self):
        self.calls.append("rollback")
        if self.fail_rollback:
            raise RuntimeError("rollback tambem falhou")
        self.mart.rows = dict(self._snapshot or {})


class FakeResult:
    def __init__(self, rows): self._rows = rows
    def mappings(self): return self
    def all(self): return self._rows
    def first(self): return self._rows[0] if self._rows else None
    def scalar(self): return len(self._rows)


DEFAULT_IDENTITY = {"db": "mktplace_control", "tem_produtos": 1, "tem_serving": 0}
NEON_IDENTITY = {"db": "neondb", "tem_produtos": 1, "tem_serving": 1}


class FakeConn:
    """Conexao fake com a MESMA superficie que o codigo produtivo usa:
    exec_driver_sql + execute(text, params) + begin(). Registra SQL e params.

    `execute` distingue o preflight de identidade (devolve a linha de
    identidade) do SELECT de escopo (devolve as linhas de dados)."""

    def __init__(self, engine): self.engine = engine

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def exec_driver_sql(self, sql):
        self.engine.statements.append(sql)

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.engine.statements.append(sql)
        self.engine.params.append(params)
        if "current_database()" in sql:
            return FakeResult([self.engine.identity] if self.engine.identity else [])
        return FakeResult(self.engine.rows)


class FakeEngine:
    def __init__(self, url, readonly, rows=None, identity=None):
        self.url, self.readonly = url, readonly
        self.statements, self.params = [], []
        self.rows = rows or []
        self.identity = DEFAULT_IDENTITY if identity is None else identity

    def connect(self): return FakeConn(self)


LOCAL_ENV = {"BACKFILL_LOCAL_RO_URL": "postgresql://localhost:5432/mkt",
             "BACKFILL_LOCAL_EXPECT_DB": "mktplace_control"}
NEON_ENV = {"BACKFILL_NEON_RO_URL": "postgresql://ep-x.neon.tech/db",
            "BACKFILL_NEON_EXPECT_DB": "neondb"}


def _row(brand="apice", month="2026-05", sku="S1", prod="Produto A",
         gmv=Decimal("100.00"), units=2, completed=1, canceled=0):
    return {"brand": brand, "ref_month": month, "sku_ref": sku, "sku_ref_key": sku,
            "product_name": prod, "variation_name": None, "gmv": gmv,
            "units_sold": units, "completed_orders": completed,
            "canceled_orders": canceled, "cancel_rate_pct": 0.0,
            "unique_buyers": 1, "avg_price": gmv / units if units else None}


def _staging(rows, scopes=None):
    # Gate SH-API-2E1: `apply_scoped_replace` agora exige a allowlist COMPLETA.
    # Testes que nao estao exercitando a allowlist usam o par autorizado.
    return bf.Staging(rows=pd.DataFrame(rows),
                      scopes=list(bf.AUTHORIZED_SCOPES) if scopes is None else scopes)


# ---------------------------------------------------------------------------
# 1-2. A contraprova: UPSERT nao repara, scoped replace repara
# ---------------------------------------------------------------------------

def test_01_upsert_isolado_nao_remove_chave_que_desapareceu():
    """Estado antes: dois SKUs. Depois da deduplicacao, S2 desapareceu
    (o unico pedido dele vinha de um snapshot superado).

    Com UPSERT puro, S2 PERMANECE no mart com o valor antigo — residuo que
    soma no total da tela. Este teste existe para travar essa prova."""
    mart = FakeMart([_row(sku="S1", gmv=100.0), _row(sku="S2", gmv=50.0)])
    nova = pd.DataFrame([_row(sku="S1", gmv=100.0)])

    mart.upsert(nova)

    assert ("2026-05", "apice", "S2", "Produto A") in mart.keys(), (
        "UPSERT deixou a chave obsoleta — e' exatamente o defeito"
    )
    assert mart.gmv() == pytest.approx(150.0)   # 100 corretos + 50 de residuo


def test_02_scoped_replace_remove_a_chave_obsoleta():
    mart = FakeMart([_row(sku="S1", gmv=100.0), _row(sku="S2", gmv=50.0)])
    nova = pd.DataFrame([_row(sku="S1", gmv=100.0)])

    mart.scoped_replace(nova, [("apice", "2026-05")])

    assert ("2026-05", "apice", "S2", "Produto A") not in mart.keys()
    assert mart.gmv() == pytest.approx(100.0)


def test_03_nenhuma_chave_fora_do_escopo_muda():
    fora1 = _row(brand="barbours", sku="X1", gmv=999.0)
    fora2 = _row(month="2026-06", sku="X2", gmv=777.0)
    mart = FakeMart([_row(sku="S1", gmv=100.0), _row(sku="S2", gmv=50.0), fora1, fora2])
    antes_fora = {k: dict(v) for k, v in mart.rows.items()
                  if not (k[1] == "apice" and k[0] == "2026-05")}

    mart.scoped_replace(pd.DataFrame([_row(sku="S1", gmv=100.0)]),
                        [("apice", "2026-05")])

    depois_fora = {k: dict(v) for k, v in mart.rows.items()
                   if not (k[1] == "apice" and k[0] == "2026-05")}
    assert depois_fora == antes_fora


# ---------------------------------------------------------------------------
# 4. rollback
# ---------------------------------------------------------------------------

def test_04_falha_entre_delete_e_insert_faz_rollback_integral(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    mart = FakeMart([_row(sku="S1", gmv=100.0), _row(sku="S2", gmv=50.0)])
    original = {k: dict(v) for k, v in mart.rows.items()}
    ex = FakeExecutor(mart, fail_on="insert")

    code = bf.apply_scoped_replace(_staging([_row(sku="S1")]),
                                   executor=ex)

    assert code == bf.EXIT_ROLLED_BACK
    assert mart.rows == original
    # Gate SH-API-2E1: o backup e COMMITADO antes de a transacao de mutacao
    # abrir. Por isso "backup" precede "begin" — se caisse dentro da mesma
    # transacao, o rollback levaria o backup junto.
    assert ex.calls == ["backup", "begin", "delete", "insert", "rollback"]
    assert "commit" not in ex.calls


def test_04b_rollback_que_tambem_falha_devolve_indeterminado(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    ex = FakeExecutor(FakeMart([_row()]), fail_on="insert", fail_rollback=True)
    code = bf.apply_scoped_replace(_staging([_row()]), executor=ex)
    assert code == bf.EXIT_INDETERMINATE


def test_04c_commit_que_falha_devolve_indeterminado(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    ex = FakeExecutor(FakeMart(), fail_on="commit")
    code = bf.apply_scoped_replace(_staging([_row()]), executor=ex)
    assert code == bf.EXIT_INDETERMINATE


def test_04d_contagem_pos_insert_divergente_faz_rollback(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    ex = FakeExecutor(FakeMart(), count_override=99)
    code = bf.apply_scoped_replace(_staging([_row()]), executor=ex)
    assert code == bf.EXIT_ROLLED_BACK


def test_04e_apply_sem_variavel_de_consentimento_e_recusado():
    ex = FakeExecutor(FakeMart())
    with pytest.raises(bf.BackfillValidationError):
        bf.apply_scoped_replace(_staging([_row()]), executor=ex)
    assert ex.calls == []


# ---------------------------------------------------------------------------
# 5-7. validacoes e escopo
# ---------------------------------------------------------------------------

def test_05_staging_vazia_com_destino_nao_vazio_bloqueia():
    before = pd.DataFrame([_row(sku="S1"), _row(sku="S2")])
    rec = bf.reconcile(before, _staging([]),
                       compared_target=bf.TARGET_LOCAL)
    with pytest.raises(bf.BackfillValidationError) as ei:
        bf.assert_reconciliation_sane(rec)
    assert "nada foi escrito" in str(ei.value)
    bf.assert_reconciliation_sane(rec, allow_empty_staging=True)  # com flag, passa


def test_05b_nao_da_para_validar_destino_sem_ter_comparado():
    """Guarda central do Gate SH-API-2B-R: sem ler banco, a validacao de
    'staging vazia com destino nao vazio' e' impossivel — e recusa dizer que
    passou."""
    rec = bf.reconcile(None, _staging([]))
    assert rec.compared is False
    with pytest.raises(bf.BackfillValidationError) as ei:
        bf.assert_reconciliation_sane(rec)
    assert "nao comparada com banco" in str(ei.value)


def test_06_escopo_vazio_e_recusado():
    for vazio in (None, []):
        with pytest.raises(bf.BackfillUsageError) as ei:
            bf.parse_scopes(vazio)
        assert "escopo vazio" in str(ei.value)


def test_07_escopo_duplicado_normaliza_deterministicamente():
    a = bf.parse_scopes(["apice:2026-05", "apice:2026-05"])
    b = bf.parse_scopes(["apice:2026-05"])
    assert a == b == [("apice", "2026-05")]
    # ordem de entrada nao altera a saida
    x = bf.parse_scopes(["kokeshi:2026-06", "apice:2026-05"])
    y = bf.parse_scopes(["apice:2026-05", "kokeshi:2026-06"])
    assert x == y == [("apice", "2026-05"), ("kokeshi", "2026-06")]


@pytest.mark.parametrize("ruim", ["apice", "apice:2026", "apice:2026-13",
                                  ":2026-05", "marca_inexistente:2026-05", ""])
def test_07b_escopo_malformado_e_recusado(ruim):
    with pytest.raises(bf.BackfillUsageError):
        bf.parse_scopes([ruim])


def test_08_source_root_inexistente_falha_antes_de_conexao(tmp_path):
    with pytest.raises(bf.BackfillUsageError) as ei:
        bf.resolve_source_root(str(tmp_path / "nao_existe"))
    assert "source-root inexistente" in str(ei.value)
    # o padrao continua compativel
    assert bf.resolve_source_root(str(tmp_path)) == tmp_path


# ---------------------------------------------------------------------------
# 9-10. dry-run
# ---------------------------------------------------------------------------

def test_09_offline_dry_run_nao_abre_conexao_nem_fabrica_estado(tmp_path, monkeypatch, capsys):
    """O modo offline nao toca banco E nao inventa `before=0`: os campos de
    estado anterior e de delta saem como N/D."""
    def proibido(*a, **k):  # pragma: no cover - deve nunca rodar
        raise AssertionError("offline-dry-run tentou resolver alvo/conexao")

    monkeypatch.setattr(bf, "resolve_target_url", proibido)
    monkeypatch.setattr(bf, "read_current_scope", proibido)
    monkeypatch.setattr(bf.loader, "_find_xlsx", lambda d: [], raising=True)
    monkeypatch.setattr(bf.loader, "_plan_brand_snapshots", lambda b, f, **k: {}, raising=True)
    monkeypatch.setattr(bf.loader, "_load_brand", lambda b: pd.DataFrame(
        {"brand": [], "ref_month": pd.to_datetime([]), "sku_ref": [],
         "product_name": [], "variation_name": [], "qty": [], "subtotal": [],
         "status": [], "buyer_username": []}), raising=True)
    (tmp_path / "apice").mkdir()

    code = bf.main(["--scope", "apice:2026-05", "--source-root", str(tmp_path),
                    "--offline-dry-run"])
    out = capsys.readouterr().out
    assert code == bf.EXIT_OK
    assert "OFFLINE-DRY-RUN" in out
    assert "NENHUM BANCO" in out
    assert "antes=" + bf.ND in out          # N/D, nunca 0
    assert "delta=" + bf.ND in out
    assert "APPLY PRODUTIVO BLOQUEADO" in out


def test_09b_dry_run_real_compara_banco_e_candidato():
    """Implementacao PRODUTIVA: query parametrizada, sessao read only e
    comparacao de verdade contra o que voltou do alvo."""
    engines = []
    antes = [{"ref_month": "2026-05", "brand": "apice", "sku_ref": "S1",
              "sku_ref_key": "S1", "product_name": "Produto A",
              "variation_name": None, "gmv": Decimal("100.00"), "units_sold": 2,
              "completed_orders": 1, "canceled_orders": 0, "unique_buyers": 1},
             {"ref_month": "2026-05", "brand": "apice", "sku_ref": "S2",
              "sku_ref_key": "S2", "product_name": "Produto B",
              "variation_name": None, "gmv": Decimal("50.00"), "units_sold": 1,
              "completed_orders": 1, "canceled_orders": 0, "unique_buyers": 1}]

    def factory(url, readonly):
        e = FakeEngine(url, readonly, rows=antes); engines.append(e); return e

    before = bf.read_current_scope(
        [("apice", "2026-05")], target=bf.TARGET_LOCAL, engine_factory=factory,
        env=LOCAL_ENV)
    assert len(before) == 2
    st = _staging([_row(sku="S1", gmv=Decimal("100.00"))])
    rec = bf.reconcile(before, st, compared_target=bf.TARGET_LOCAL)

    assert rec.compared is True and rec.compared_target == "local"
    assert rec.before_rows == 2 and rec.after_rows == 1
    assert (rec.keys_added, rec.keys_updated, rec.keys_removed) == (0, 1, 1)
    assert rec.gmv_before == Decimal("150.00")
    assert rec.gmv_delta == Decimal("-50.00")


def test_09c_sessao_read_only_e_nenhum_comando_de_escrita():
    engines = []

    def factory(url, readonly):
        e = FakeEngine(url, readonly, identity=NEON_IDENTITY)
        engines.append(e); return e

    bf.read_current_scope([("apice", "2026-05")], target=bf.TARGET_NEON,
                          engine_factory=lambda u, readonly: factory(u, readonly),
                          env=NEON_ENV)
    e = engines[0]
    assert e.readonly is True
    assert "SET TRANSACTION READ ONLY" in e.statements
    proibidos = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE", "DROP", "ALTER")
    for s in e.statements:
        assert not any(pp in s.upper() for pp in proibidos)


def test_09d_alvos_local_e_neon_sao_explicitos_e_nao_confundiveis():
    with pytest.raises(bf.BackfillUsageError) as ei:
        bf.resolve_target_url(bf.TARGET_LOCAL,
                              env={"BACKFILL_LOCAL_RO_URL": "postgresql://ep-x.neon.tech/db"})
    assert "host REMOTO" in str(ei.value)

    with pytest.raises(bf.BackfillUsageError) as ei2:
        bf.resolve_target_url(bf.TARGET_NEON,
                              env={"BACKFILL_NEON_RO_URL": "postgresql://localhost/db"})
    assert "LOCALHOST" in str(ei2.value)

    for alvo in bf.TARGETS:
        with pytest.raises(bf.BackfillUsageError) as ei3:
            bf.resolve_target_url(alvo, env={"DATABASE_URL": "postgresql://x/y"})
        assert "explicitamente" in str(ei3.value)

    try:
        bf.resolve_target_url(bf.TARGET_LOCAL,
                              env={"BACKFILL_LOCAL_RO_URL": "postgresql://u:segredo@h/db"})
    except bf.BackfillUsageError as e:
        assert "segredo" not in str(e) and "://" not in str(e)


def test_09e_dry_run_sem_target_e_recusado(tmp_path):
    (tmp_path / "apice").mkdir()
    code = bf.main(["--scope", "apice:2026-05", "--source-root", str(tmp_path), "--dry-run"])
    assert code == bf.EXIT_USAGE


def test_09f_query_parametrizada_para_varios_escopos():
    escopos = [("apice", "2026-05"), ("kokeshi", "2026-06"), ("rituaria", "2026-07")]
    sql, params = bf.build_scope_query(escopos)
    for brand, month in escopos:
        assert brand not in sql and month not in sql
    assert sql.count("(:b") == 3
    assert params == {"b0": "apice", "m0": "2026-05", "b1": "kokeshi",
                      "m1": "2026-06", "b2": "rituaria", "m2": "2026-07"}
    assert "(brand, to_char(ref_month, 'YYYY-MM')) IN" in sql


def test_09g_decimal_preservado_na_leitura():
    rows = [{"ref_month": "2026-05", "brand": "apice", "sku_ref": "S1",
             "sku_ref_key": "S1", "product_name": "P", "variation_name": None,
             "gmv": Decimal("1234.56"), "units_sold": 3, "completed_orders": 2,
             "canceled_orders": 0, "unique_buyers": 2}]
    df = bf.read_current_scope(
        [("apice", "2026-05")], target=bf.TARGET_LOCAL,
        engine_factory=lambda u, readonly: FakeEngine(u, readonly, rows=rows),
        env={**LOCAL_ENV, "BACKFILL_LOCAL_RO_URL": "postgresql://127.0.0.1/db"})
    assert isinstance(df.loc[0, "gmv"], Decimal)
    assert bf._dsum(df, "gmv") == Decimal("1234.56")


def test_09h_linha_fora_do_escopo_na_resposta_e_descartada():
    rows = [{"ref_month": "2026-06", "brand": "apice", "sku_ref": "S1",
             "sku_ref_key": "S1", "product_name": "P", "variation_name": None,
             "gmv": Decimal("1.00"), "units_sold": 1, "completed_orders": 1,
             "canceled_orders": 0, "unique_buyers": 1}]
    with pytest.raises(bf.BackfillValidationError) as ei:
        bf.read_current_scope(
            [("apice", "2026-05")], target=bf.TARGET_LOCAL,
            engine_factory=lambda u, readonly: FakeEngine(u, readonly, rows=rows),
            env={**LOCAL_ENV, "BACKFILL_LOCAL_RO_URL": "postgresql://localhost/db"})
    assert "fora do escopo" in str(ei.value)


def test_10_relatorio_traz_chaves_removidas_sem_expor_produto_nem_pii():
    before = pd.DataFrame([_row(sku="S1", prod="Sabonete Secreto 200ml"),
                           _row(sku="S2", prod="Creme Confidencial")])
    st = _staging([_row(sku="S1", prod="Sabonete Secreto 200ml")],
                  [("apice", "2026-05")])
    rec = bf.reconcile(before, st, compared_target=bf.TARGET_LOCAL)
    rel = bf.format_report(st, rec, mode="dry-run")

    assert rec.keys_removed == 1
    assert "-1" in rel
    assert "Creme Confidencial" not in rel
    assert "ALVO=LOCAL" in rel
    for proibido in ("cpf", "telefone", "endereco", "comprador", "@", "segredo", "://"):
        assert proibido not in rel.lower()


# ---------------------------------------------------------------------------
# 11-12. paridade local/Neon e idempotencia
# ---------------------------------------------------------------------------

def test_11_local_e_neon_recebem_o_mesmo_conjunto_logico():
    """O scoped replace e' aplicado a dois destinos independentes a partir da
    MESMA staging: o conjunto de chaves e o GMV tem de ficar identicos.
    Um UPSERT no Neon (semantica antiga) divergiria."""
    inicial = [_row(sku="S1", gmv=100.0), _row(sku="S2", gmv=50.0)]
    local, neon = FakeMart(inicial), FakeMart(inicial)
    nova = pd.DataFrame([_row(sku="S1", gmv=100.0)])
    escopo = [("apice", "2026-05")]

    local.scoped_replace(nova, escopo)
    neon.scoped_replace(nova, escopo)
    assert local.keys() == neon.keys()
    assert local.gmv() == neon.gmv()

    # contraste: Neon com a semantica antiga divergiria do local
    neon_antigo = FakeMart(inicial)
    neon_antigo.upsert(nova)
    assert neon_antigo.keys() != local.keys()


def test_12_reexecucao_produz_delta_zero():
    mart = FakeMart([_row(sku="S1", gmv=100.0), _row(sku="S2", gmv=50.0)])
    nova = pd.DataFrame([_row(sku="S1", gmv=100.0)])
    escopo = [("apice", "2026-05")]

    mart.scoped_replace(nova, escopo)
    k1, g1 = mart.keys(), mart.gmv()
    mart.scoped_replace(nova, escopo)          # segunda vez
    assert mart.keys() == k1 and mart.gmv() == g1

    rec = bf.reconcile(pd.DataFrame([_row(sku="S1", gmv=100.0)]),
                       _staging([_row(sku="S1", gmv=100.0)], escopo))
    assert rec.keys_added == 0 and rec.keys_removed == 0
    assert rec.gmv_delta == 0.0 and rec.units_delta == 0


# ---------------------------------------------------------------------------
# 13-15. fail-closed, hash e compatibilidade
# ---------------------------------------------------------------------------

def test_13_arquivo_rejeitado_impede_qualquer_escrita(tmp_path, monkeypatch):
    (tmp_path / "apice").mkdir()
    monkeypatch.setattr(bf.loader, "_find_xlsx",
                        lambda d: [type("P", (), {"name": "Order.toship.x.xlsx"})()],
                        raising=True)

    def real_plan(brand, files, **k):
        return bf.loader._plan_brand_snapshots.__wrapped__(brand, files, **k) \
            if hasattr(bf.loader._plan_brand_snapshots, "__wrapped__") else None

    with pytest.raises(bf.loader.ShopeeSnapshotError):
        bf.build_staging([("apice", "2026-05")], tmp_path)


def test_14_hash_alterado_entre_dry_run_e_apply_bloquearia(tmp_path):
    """A staging declara o hash de cada arquivo aceito. Comparar os mapas de
    hash de duas execucoes detecta troca de arquivo entre o dry-run e o apply."""
    a = bf.Staging(rows=pd.DataFrame([_row()]), scopes=list(bf.AUTHORIZED_SCOPES),
                   file_hashes={"apice/Order.all.20260501_20260531.xlsx": "aaa"})
    b = bf.Staging(rows=pd.DataFrame([_row()]), scopes=list(bf.AUTHORIZED_SCOPES),
                   file_hashes={"apice/Order.all.20260501_20260531.xlsx": "bbb"})
    assert a.file_hashes != b.file_hashes
    mudou = [k for k in a.file_hashes if a.file_hashes[k] != b.file_hashes.get(k)]
    assert mudou == ["apice/Order.all.20260501_20260531.xlsx"]


def test_15_modo_e_obrigatorio_e_apply_produtivo_esta_bloqueado(capsys):
    with pytest.raises(SystemExit):
        bf.build_parser().parse_args(["--scope", "apice:2026-05"])

    ns = bf.build_parser().parse_args(["--scope", "apice:2026-05", "--offline-dry-run"])
    assert ns.mode == "offline-dry-run"

    # Gate SH-API-2E1: um unico escopo agora e' recusado pela ALLOWLIST antes
    # de chegar a barreira final — e a mensagem tem de dizer o que falta, nao
    # so' "bloqueado".
    code = bf.main(["--scope", "apice:2026-05", "--apply"])
    assert code == bf.EXIT_VALIDATION_REFUSED
    err = capsys.readouterr().err
    assert "PARCIAL recusada" in err and "barbours:2026-05" in err

    # Gate SH-API-2E3: a barreira incondicional saiu e o caminho real esta
    # ligado. Com a allowlist completa e consentimento, mas SEM --source-root,
    # a recusa passa a ser da porta de origem — nunca um "bloqueado" generico,
    # e continua sem abrir conexao.
    import os as _os
    _os.environ[bf.CONSENT_ENV] = "1"
    _os.environ["BACKFILL_NEON_RW_URL"] = "postgresql://u@remoto.example/db"
    try:
        code2 = bf.main(["--scope", "apice:2026-05", "--scope", "barbours:2026-05",
                         "--apply", "--target", "neon"])
    finally:
        _os.environ.pop(bf.CONSENT_ENV, None)
        _os.environ.pop("BACKFILL_NEON_RW_URL", None)
    assert code2 == bf.EXIT_VALIDATION_REFUSED
    assert "--source-root explicito" in capsys.readouterr().err


def test_15c_apply_somente_no_neon_e_recusado_pelo_contrato():
    bf.assert_not_neon_only(["local", "neon"])
    bf.assert_not_neon_only(["local"])
    with pytest.raises(bf.BackfillValidationError) as ei:
        bf.assert_not_neon_only(["neon"])
    assert "sem o LOCAL" in str(ei.value)


def test_15d_plano_local_neon_nomeia_estado_parcial_e_proibicoes():
    plano = bf.plan_local_then_neon([("apice", "2026-05")])
    assert list(plano["resultados_possiveis"]) == [bf.STATUS_OK, bf.STATUS_PARTIAL,
                                                   bf.STATUS_REFUSED]
    assert "retry SOMENTE" in plano["resultados_possiveis"][bf.STATUS_PARTIAL]
    proib = " ".join(plano["proibido"]).lower()
    assert "somente no neon" in proib and "truncate" in proib and "temp table" in proib
    assert plano["lock"]["compartilhado_com"]
    ordem = " ".join(plano["ordem"]).upper()
    assert ordem.index("LOCAL") < ordem.index("NEON")


class FakeTrans:
    def __init__(self, owner): self.owner = owner; self.state = "open"
    def commit(self): self.owner.events.append("commit"); self.state = "committed"
    def rollback(self): self.owner.events.append("rollback"); self.state = "rolled_back"


class WriteConn:
    """Conexao de escrita fake. `begin()` devolve uma transacao de verdade
    (objeto com commit/rollback) e cada `execute` guarda SQL + params, para
    que o teste possa contar INSERTs e conferir bind parameters."""

    def __init__(self, count=1):
        self.sqls, self.params, self.events = [], [], []
        self._count = count

    def begin(self):
        self.events.append("begin")
        self.trans = FakeTrans(self)
        return self.trans

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sqls.append(sql)
        self.params.append(params)
        if sql.strip().upper().startswith("INSERT") and params is None:
            raise AssertionError("INSERT sem bind parameters")
        outer = self

        class R:
            def scalar(self_inner): return outer._count

            # Gate SH-API-2E1: o backup confere contagem+checksum via
            # .mappings().first(). O fake devolve o MESMO par para origem e
            # copia, entao o backup "confere" — os testes de divergencia usam
            # um fake proprio que devolve pares diferentes.
            def mappings(self_inner): return self_inner

            def first(self_inner):
                return {"n": outer._count, "checksum": "fake-checksum"}
        return R()

    def inserts(self):
        return [s for s in self.sqls if s.strip().upper().startswith("INSERT")]


def test_15e_executor_produtivo_backup_duravel_delete_escopado_nunca_truncate():
    import datetime

    conn = WriteConn()
    ex = bf.ScopedReplaceExecutor(
        conn, target="neon",
        clock=lambda: datetime.datetime(2026, 9, 8, 12, 0, 0))
    escopo = list(bf.AUTHORIZED_SCOPES)
    # Ordem do Gate SH-API-2E1: backup COMMITADO primeiro, depois a transacao
    # de mutacao.
    ex.backup_scope(escopo); ex.begin(); ex.delete_scope(escopo)

    todos = " ".join(conn.sqls).upper()
    assert "CREATE TABLE MARTS.FACT_SHOPEE_PRODUCT_MONTHLY_BKP_NEON_20260908_120000" in todos
    assert "SELECT *" not in todos          # colunas sempre explicitas
    assert "TEMP" not in todos
    assert "TRUNCATE" not in todos
    delete = [s for s in conn.sqls if s.strip().upper().startswith("DELETE")]
    assert len(delete) == 1
    # dois escopos autorizados -> dois pares parametrizados, nunca literais
    assert "IN ((:b0, :m0), (:b1, :m1))" in delete[0]
    # F2: BEGIN nao e' SQL textual — e' conn.begin()
    assert "BEGIN" not in todos
    assert conn.events[0] == "begin"


# ---------------------------------------------------------------------------
# F1 — exatamente UM insert, com bind parameters e quantidade correta
# ---------------------------------------------------------------------------

def test_f1_insert_rows_emite_exatamente_um_insert_com_params():
    conn = WriteConn(count=3)
    ex = bf.ScopedReplaceExecutor(conn, target="neon")
    ex.begin()
    linhas = pd.DataFrame([_row(sku="S1"), _row(sku="S2"), _row(sku="S3")])
    ex.insert_rows(linhas)

    assert len(conn.inserts()) == 1, "F1: o INSERT saia duas vezes"
    params = conn.params[-1]
    assert isinstance(params, list) and len(params) == len(linhas) == 3
    assert all(isinstance(p, dict) for p in params)
    assert {p["sku_ref_key"] for p in params} == {"S1", "S2", "S3"}
    # a trilha registra o SQL sem executa-lo de novo
    assert sum(1 for e in ex.emitted if e.upper().startswith("INSERT")) == 1


def test_f1_staging_vazia_nao_emite_insert():
    conn = WriteConn()
    ex = bf.ScopedReplaceExecutor(conn, target="neon")
    ex.begin()
    ex.insert_rows(pd.DataFrame(columns=["brand", "ref_month", "sku_ref_key",
                                         "product_name", "gmv"]))
    assert conn.inserts() == []


def test_f1_colunas_tecnicas_nao_vao_para_o_insert():
    conn = WriteConn(count=1)
    ex = bf.ScopedReplaceExecutor(conn, target="neon")
    ex.begin()
    linhas = pd.DataFrame([{**_row(sku="S1"), "_snap_end": "20260531"}])
    ex.insert_rows(linhas)
    assert "_snap_end" not in conn.inserts()[0]
    assert "_snap_end" not in conn.params[-1][0]


# ---------------------------------------------------------------------------
# F2 — transacao real, e sinais de encerramento propagam
# ---------------------------------------------------------------------------

def test_f2_commit_e_rollback_usam_a_transacao_e_nao_sql_textual():
    conn = WriteConn()
    ex = bf.ScopedReplaceExecutor(conn, target="neon")
    ex.begin(); ex.commit()
    assert conn.events == ["begin", "commit"]
    assert not any("COMMIT" in s.upper() for s in conn.sqls)

    conn2 = WriteConn()
    ex2 = bf.ScopedReplaceExecutor(conn2, target="neon")
    ex2.begin(); ex2.rollback()
    assert conn2.events == ["begin", "rollback"]
    assert not any("ROLLBACK" in s.upper() for s in conn2.sqls)


def test_f2_commit_sem_transacao_aberta_e_recusado():
    ex = bf.ScopedReplaceExecutor(WriteConn(), target="neon")
    with pytest.raises(bf.BackfillValidationError):
        ex.commit()
    with pytest.raises(bf.BackfillValidationError):
        ex.rollback()


@pytest.mark.parametrize("sinal", [KeyboardInterrupt, SystemExit])
def test_f2_keyboardinterrupt_e_systemexit_propagam(monkeypatch, sinal):
    """Nao viram exit code. Sao ordens de encerramento — o codigo tenta
    desfazer e PROPAGA."""
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    mart = FakeMart([_row()])
    ex = FakeExecutor(mart)

    def boom(rows):
        raise sinal("encerrar")

    ex.insert_rows = boom
    with pytest.raises(sinal):
        bf.apply_scoped_replace(_staging([_row()]), executor=ex)
    assert "rollback" in ex.calls      # tentou desfazer antes de propagar


def test_f2_erro_operacional_continua_virando_exit_code(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    ex = FakeExecutor(FakeMart([_row()]), fail_on="insert")
    code = bf.apply_scoped_replace(_staging([_row()]), executor=ex)
    assert code == bf.EXIT_ROLLED_BACK


# ---------------------------------------------------------------------------
# F3 — plano sem TEMP
# ---------------------------------------------------------------------------

def test_f3_plano_declara_backup_duravel_e_nunca_temp():
    """A palavra TEMP aparece de proposito no COMENTARIO ("nunca TEMP") e na
    lista `never`. O que o teste proibe e' um COMANDO `CREATE TEMP TABLE` — por
    isso compara so' a parte executavel de cada passo, antes do `--`."""
    plano = bf.plan_scoped_replace(_staging([_row()]))

    comandos = [s.split("--")[0].strip().upper() for s in plano["steps"]]
    for c in comandos:
        assert "TEMP TABLE" not in c, f"passo cria TEMP TABLE: {c}"
        assert not c.startswith("TRUNCATE")
        assert not c.startswith("DROP")
        assert not c.startswith("BEGIN")        # F2: nada de BEGIN textual
        assert not c.startswith("COMMIT")
        assert not c.startswith("ROLLBACK")

    criar = [c for c in comandos if c.startswith("CREATE TABLE")]
    assert len(criar) == 1 and bf.BACKUP_PREFIX.upper() in criar[0]

    never = " ".join(plano["never"]).upper()
    assert "TEMP TABLE" in never and "TRUNCATE" in never
    assert "BEGIN/COMMIT/ROLLBACK COMO SQL TEXTUAL" in never


# ---------------------------------------------------------------------------
# F4 — identidade do destino
# ---------------------------------------------------------------------------

def test_f4_sem_expect_db_o_alvo_e_bloqueado():
    conn = FakeConn(FakeEngine("x", True))
    with pytest.raises(bf.BackfillIdentityError) as ei:
        bf.assert_target_identity(conn, bf.TARGET_LOCAL,
                                  env={"BACKFILL_LOCAL_RO_URL": "postgresql://localhost/x"})
    assert "NAO comprovada" in str(ei.value)


def test_f4_banco_com_nome_diferente_do_declarado_e_recusado():
    eng = FakeEngine("x", True, identity={"db": "outro_banco", "tem_produtos": 1,
                                          "tem_serving": 0})
    with pytest.raises(bf.BackfillIdentityError) as ei:
        bf.assert_target_identity(FakeConn(eng), bf.TARGET_LOCAL, env=LOCAL_ENV)
    assert "RECUSADA" in str(ei.value)
    assert "outro_banco" not in str(ei.value)      # nunca vaza o nome
    assert "mktplace_control" not in str(ei.value)


def test_f4_urls_trocadas_sao_recusadas_pela_impressao_digital():
    # URL do 'local' apontando para outro banco -> recusa pelo NOME declarado,
    # nao por presenca de tabela: o local real TEM a tabela de serving (medido
    # no Gate SH-API-2C-R), entao aquela regra foi removida por ser falsa.
    eng = FakeEngine("x", True, identity=NEON_IDENTITY)
    with pytest.raises(bf.BackfillIdentityError) as ei:
        bf.assert_target_identity(FakeConn(eng), bf.TARGET_LOCAL, env=LOCAL_ENV)
    assert "RECUSADA" in str(ei.value)

    # e um local legitimo que TEM a tabela de serving passa, desde que o nome
    # declarado bata — regressao da regra removida
    ok = FakeEngine("x", True, identity={"db": "mktplace_control",
                                         "tem_produtos": 1, "tem_serving": 1})
    r = bf.assert_target_identity(FakeConn(ok), bf.TARGET_LOCAL, env=LOCAL_ENV)
    assert r["identity_proven"] is True

    # URL do 'neon' apontando para um banco sem a tabela de serving -> recusa
    eng2 = FakeEngine("x", True, identity={"db": "neondb", "tem_produtos": 1,
                                           "tem_serving": 0})
    with pytest.raises(bf.BackfillIdentityError) as ei2:
        bf.assert_target_identity(FakeConn(eng2), bf.TARGET_NEON, env=NEON_ENV)
    assert "serving esperado" in str(ei2.value)


def test_f4_banco_remoto_desconhecido_sem_a_tabela_e_recusado():
    eng = FakeEngine("x", True, identity={"db": "neondb", "tem_produtos": 0,
                                          "tem_serving": 0})
    with pytest.raises(bf.BackfillIdentityError) as ei:
        bf.assert_target_identity(FakeConn(eng), bf.TARGET_NEON, env=NEON_ENV)
    assert "fact_shopee_product_monthly" in str(ei.value)


def test_f4_identidade_correta_passa_e_nao_vaza_nada():
    eng = FakeEngine("x", True, identity=NEON_IDENTITY)
    r = bf.assert_target_identity(FakeConn(eng), bf.TARGET_NEON, env=NEON_ENV)
    assert r["identity_proven"] is True
    assert "neondb" not in str(r)


# ---------------------------------------------------------------------------
# F5 — eixos ortogonais coexistindo
# ---------------------------------------------------------------------------

def test_f5_load_stale_e_maturidade_imatura_coexistem():
    """Agosto/2026: presente fisicamente, carga defasada, cobertura analitica
    inadequada e fonte imatura — TUDO ao mesmo tempo."""
    r = bf.classify_scope(
        rows_present=188, eligible_rows=0, daily_gmv=Decimal("1144597.12"),
        source_files=212, load_is_stale=True, month_is_closed=True,
        completed_share=Decimal("0.003"), mature_share_floor=Decimal("0.90"),
        brands_present=4, brands_expected=5)

    assert r["load_status"] == bf.LOAD_STALE
    assert r["maturity_status"] == bf.MATURITY_IMMATURE
    assert r["eligibility_status"] == bf.ELIG_NONE
    assert r["coverage_status"] == bf.COVERAGE_BELOW
    assert r["physically_present"] is True
    assert r["analytically_sufficient"] is False
    # os quatro eixos alertam ao mesmo tempo — nenhum esconde o outro
    assert set(r["alerts"]) >= {"shopee_produtos_carga_defasada",
                                "shopee_produtos_presente_mas_100pct_excluido",
                                "shopee_produtos_fonte_imatura",
                                "shopee_produtos_cobertura_de_marcas"}


def test_f5_nunca_ha_estado_saudavel_com_diaria_material_e_zero_elegiveis():
    for stale in (True, False):
        for share in (None, Decimal("0.99")):
            r = bf.classify_scope(
                rows_present=188, eligible_rows=0, daily_gmv=Decimal("1000.00"),
                source_files=212, load_is_stale=stale, month_is_closed=True,
                completed_share=share, mature_share_floor=Decimal("0.90"),
                brands_present=5, brands_expected=5)
            assert r["analytically_sufficient"] is False
            assert r["alert"] is True


def test_f5_sem_piso_medido_a_maturidade_fica_desconhecida():
    """Nao inventar tolerancia: sem piso MEDIDO, o eixo fica unknown."""
    r = bf.classify_scope(
        rows_present=10, eligible_rows=10, daily_gmv=Decimal("100.00"),
        source_files=212, load_is_stale=False, month_is_closed=True,
        completed_share=Decimal("0.5"), mature_share_floor=None,
        brands_present=5, brands_expected=5)
    assert r["maturity_status"] == bf.MATURITY_UNKNOWN


def test_f5_mes_saudavel_nao_alerta():
    r = bf.classify_scope(
        rows_present=120, eligible_rows=118, daily_gmv=Decimal("573918.72"),
        source_files=212, load_is_stale=False, month_is_closed=True,
        completed_share=Decimal("0.97"), mature_share_floor=Decimal("0.90"),
        brands_present=5, brands_expected=5)
    assert r["alerts"] == [] and r["alert"] is False
    assert r["eligibility_status"] == bf.ELIG_PARTIAL


def test_15f_delete_antes_do_backup_e_recusado():
    class Conn:
        def execute(self, stmt, params=None):
            class R:
                def scalar(self_inner): return 0
            return R()
    ex = bf.ScopedReplaceExecutor(Conn(), target="neon")
    with pytest.raises(bf.BackfillValidationError) as ei:
        ex.delete_scope([("apice", "2026-05")])
    assert "sequencia invalida" in str(ei.value)


def test_15b_plano_do_scoped_replace_nao_contem_comando_truncate():
    """Nenhum PASSO e' um TRUNCATE. A palavra aparece de proposito no
    comentario do DELETE ("-- nunca TRUNCATE") e na lista `never`; o que o
    teste proibe e' um comando."""
    plano = bf.plan_scoped_replace(_staging([_row()]))

    for passo in plano["steps"]:
        comando = passo.split("--")[0].strip().upper()
        assert not comando.startswith("TRUNCATE"), f"passo e' um TRUNCATE: {passo}"
        assert not comando.startswith("DROP")

    texto = " ".join(plano["steps"]).upper()
    assert "DELETE" in texto and "INSERT" in texto
    # o DELETE tem de ser filtrado por escopo, nunca aberto
    delete = [s for s in plano["steps"] if s.split("--")[0].strip().upper().startswith("DELETE")]
    assert len(delete) == 1 and "escopos" in delete[0]
    assert "TRUNCATE" in [x.upper() for x in plano["never"]]


# ---------------------------------------------------------------------------
# Validacoes de staging
# ---------------------------------------------------------------------------

def test_staging_duplicada_na_chave_real_e_recusada():
    st = _staging([_row(sku="S1"), _row(sku="S1")])
    with pytest.raises(bf.BackfillValidationError) as ei:
        bf.assert_staging_unique(st)
    assert "chave real" in str(ei.value)


def test_staging_com_par_fora_do_escopo_e_recusada():
    st = _staging([_row(month="2026-06")])
    with pytest.raises(bf.BackfillValidationError) as ei:
        bf.assert_staging_within_scope(st)
    assert "fora do escopo" in str(ei.value)


def test_reconcile_conta_chaves_adicionadas_atualizadas_removidas():
    before = pd.DataFrame([_row(sku="S1", gmv=10.0), _row(sku="S2", gmv=20.0)])
    st = _staging([_row(sku="S1", gmv=11.0), _row(sku="S3", gmv=30.0)],
                  [("apice", "2026-05")])
    rec = bf.reconcile(before, st)
    assert (rec.keys_added, rec.keys_updated, rec.keys_removed) == (1, 1, 1)
    assert rec.gmv_before == 30.0 and rec.gmv_after == 41.0


# ---------------------------------------------------------------------------
# Fase 5 — estados de frescor / maturacao
# ---------------------------------------------------------------------------

def test_freshness_agosto_real_188_chaves_zero_elegiveis_dispara_alerta():
    """O caso concreto de 2026-08: 188 chaves presentes, 0 elegiveis, e a
    Shopee Daily com GMV material. Nunca 'sem dados', nunca silencioso."""
    r = bf.classify_scope_freshness(
        rows_present=188, eligible_rows=0, daily_gmv=Decimal("1144597.12"),
        source_files=212, load_is_stale=False, month_is_closed=True)

    assert r["state"] == bf.FRESHNESS_MATURATION_PENDING
    assert r["alert"] is True
    assert r["excluded_zero_gmv"] == 188
    assert r["daily_gmv_material"] is True
    assert r["state"] != bf.FRESHNESS_COMPLETE
    assert "NOW()" not in r["refreshed_at_source"]


def test_freshness_mes_corrente_presente_e_nao_elegivel():
    r = bf.classify_scope_freshness(
        rows_present=49, eligible_rows=0, daily_gmv=Decimal("50000.00"),
        source_files=212, load_is_stale=False, month_is_closed=False)
    assert r["state"] == bf.FRESHNESS_PRESENT_NOT_ELIGIBLE
    assert r["alert"] is True


def test_freshness_sem_arquivo_e_carga_atrasada_sao_estados_distintos():
    sem_fonte = bf.classify_scope_freshness(
        rows_present=0, eligible_rows=0, daily_gmv=Decimal("10.00"),
        source_files=0, load_is_stale=False, month_is_closed=True)
    assert sem_fonte["state"] == bf.FRESHNESS_SOURCE_MISSING and sem_fonte["alert"]

    atrasada = bf.classify_scope_freshness(
        rows_present=0, eligible_rows=0, daily_gmv=Decimal("10.00"),
        source_files=212, load_is_stale=False, month_is_closed=True)
    assert atrasada["state"] == bf.FRESHNESS_LOAD_STALE and atrasada["alert"]

    stale = bf.classify_scope_freshness(
        rows_present=100, eligible_rows=90, daily_gmv=Decimal("10.00"),
        source_files=212, load_is_stale=True, month_is_closed=True)
    assert stale["state"] == bf.FRESHNESS_LOAD_STALE and stale["alert"]


def test_freshness_completo_nao_alerta_e_mes_sem_venda_nao_alerta():
    ok = bf.classify_scope_freshness(
        rows_present=120, eligible_rows=118, daily_gmv=Decimal("573918.72"),
        source_files=212, load_is_stale=False, month_is_closed=True)
    assert ok["state"] == bf.FRESHNESS_COMPLETE and ok["alert"] is False

    sem_venda = bf.classify_scope_freshness(
        rows_present=0, eligible_rows=0, daily_gmv=Decimal("0"),
        source_files=212, load_is_stale=False, month_is_closed=True)
    assert sem_venda["state"] == bf.FRESHNESS_COMPLETE and sem_venda["alert"] is False


def test_blueprint_de_alertas_cobre_as_duas_superficies():
    ids = {a["id"] for a in bf.QUALITY_ALERT_BLUEPRINT}
    assert len(ids) == 4
    for a in bf.QUALITY_ALERT_BLUEPRINT:
        assert set(a["onde"]) == {"health_check", "torre_qualidade_dados"}
        assert a["critico_para_exit"] is False   # nao critico, mas nunca silencioso
    assert set(bf.FRESHNESS_STATES) == {
        "source_missing", "load_stale", "present_but_not_eligible",
        "maturation_pending", "complete"}


# ---------------------------------------------------------------------------
# Gate SH-API-2C Fase 1 — lacuna transacional: ValueError entre begin e commit
# ---------------------------------------------------------------------------

def test_2c_valueerror_apos_begin_e_antes_do_commit_faz_rollback_confirmado(monkeypatch):
    """ValueError comum (nem KeyboardInterrupt, nem SystemExit) levantada
    DEPOIS do begin e ANTES do commit.

    Exige: rollback tentado, rollback confirmado, ROLLED_BACK, nenhuma
    alegacao de commit, e NUNCA INDETERMINATE — o rollback deu certo."""
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    mart = FakeMart([_row(sku="S1"), _row(sku="S2")])
    original = {k: dict(v) for k, v in mart.rows.items()}
    ex = FakeExecutor(mart)

    def explode(rows):
        raise ValueError("falha de dominio no meio da transacao")

    ex.insert_rows = explode

    code = bf.apply_scoped_replace(
        _staging([_row(sku="S1")]), executor=ex)

    assert code == bf.EXIT_ROLLED_BACK
    assert code != bf.EXIT_INDETERMINATE
    assert "rollback" in ex.calls                 # rollback explicitamente tentado
    assert "commit" not in ex.calls               # nenhuma alegacao de commit
    assert ex.calls.index("rollback") > ex.calls.index("begin")
    assert mart.rows == original                  # rollback confirmado de fato


@pytest.mark.parametrize("erro", [ValueError, KeyError, RuntimeError, TypeError,
                                  ArithmeticError, bf.BackfillValidationError])
def test_2c_qualquer_excecao_comum_gera_rollback_e_nunca_indeterminate(monkeypatch, erro):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    ex = FakeExecutor(FakeMart([_row()]))

    def explode(scopes):
        raise erro("falha comum")

    ex.delete_scope = explode
    code = bf.apply_scoped_replace(_staging([_row()]), executor=ex)
    assert code == bf.EXIT_ROLLED_BACK
    assert "commit" not in ex.calls


def test_2c_indeterminate_so_quando_o_proprio_rollback_falha(monkeypatch):
    """INDETERMINATE e' reservado: rollback que falhou, ou commit de resultado
    desconhecido. Nunca para um erro comum com rollback bem-sucedido."""
    monkeypatch.setenv("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES", "1")
    ex = FakeExecutor(FakeMart([_row()]), fail_on="insert", fail_rollback=True)
    assert bf.apply_scoped_replace(
        _staging([_row()]), executor=ex) == bf.EXIT_INDETERMINATE


def test_2c_codigo_nao_usa_except_baseexception():
    """Guarda estatica: `except BaseException` engoliria KeyboardInterrupt."""
    import inspect
    src = inspect.getsource(bf)
    assert "except BaseException" not in src
    assert "except (KeyboardInterrupt, SystemExit)" in src


# ---------------------------------------------------------------------------
# Gate SH-API-2C Fase 6 — maturidade: insumos invalidos e ortogonalidade
# ---------------------------------------------------------------------------

def test_2c_denominador_zero_vira_maturity_unknown():
    assert bf.compute_completed_share(Decimal("0"), Decimal("0")) is None
    r = bf.classify_scope(
        rows_present=0, eligible_rows=0, daily_gmv=Decimal("0"),
        source_files=212, load_is_stale=False, month_is_closed=True,
        completed_share=None, mature_share_floor=bf.MATURITY_FLOOR_PROVISIONAL,
        brands_present=5, brands_expected=5)
    assert r["maturity_status"] == bf.MATURITY_UNKNOWN


def test_2c_razao_fora_de_0_1_e_erro_explicito():
    with pytest.raises(bf.MaturityInputError) as ei:
        bf.compute_completed_share(Decimal("150.00"), Decimal("100.00"))
    assert "fora de [0,1]" in str(ei.value)

    with pytest.raises(bf.MaturityInputError):
        bf.classify_scope(
            rows_present=1, eligible_rows=1, daily_gmv=Decimal("1"),
            source_files=1, load_is_stale=False, month_is_closed=True,
            completed_share=Decimal("1.5"),
            mature_share_floor=bf.MATURITY_FLOOR_PROVISIONAL)


@pytest.mark.parametrize("conc,ativo", [(Decimal("-1"), Decimal("10")),
                                        (Decimal("1"), Decimal("-10")),
                                        (Decimal("-1"), Decimal("-10"))])
def test_2c_gmv_negativo_e_erro_e_nao_estado(conc, ativo):
    with pytest.raises(bf.MaturityInputError) as ei:
        bf.compute_completed_share(conc, ativo)
    assert "negativo" in str(ei.value)
    assert "-1" not in str(ei.value)      # valores omitidos da mensagem


def test_2c_julho_2026_permanece_imaturo_com_a_evidencia_atual():
    """Evidencia medida no Data Mart: 5.512.907,44 concluido sobre
    7.526.725,25 nao-cancelado = 0,7324, abaixo do piso provisorio 0,99."""
    share = bf.compute_completed_share(Decimal("5512907.44"), Decimal("7526725.25"))
    assert share == Decimal("0.7324")
    r = bf.classify_scope(
        rows_present=502, eligible_rows=472, daily_gmv=Decimal("7526725.25"),
        source_files=212, load_is_stale=False, month_is_closed=True,
        completed_share=share, mature_share_floor=bf.MATURITY_FLOOR_PROVISIONAL,
        brands_present=5, brands_expected=5)
    assert r["maturity_status"] == bf.MATURITY_IMMATURE
    assert "shopee_produtos_fonte_imatura" in r["alerts"]


def test_2c_agosto_2026_permanece_materialmente_imaturo():
    share = bf.compute_completed_share(Decimal("2955.34"), Decimal("4156389.94"))
    assert share == Decimal("0.0007")
    r = bf.classify_scope(
        rows_present=188, eligible_rows=0, daily_gmv=Decimal("4156389.94"),
        source_files=212, load_is_stale=True, month_is_closed=True,
        completed_share=share, mature_share_floor=bf.MATURITY_FLOOR_PROVISIONAL,
        brands_present=4, brands_expected=5)
    assert r["maturity_status"] == bf.MATURITY_IMMATURE
    assert r["analytically_sufficient"] is False


def test_2c_meses_estaveis_ficam_acima_do_piso_medido():
    """Os seis meses fechados estaveis (jan-jun/2026). O minimo e' 0,9987."""
    medidos = {"2026-01": (Decimal("1837066.60"), Decimal("1837066.60")),
               "2026-02": (Decimal("2805310.91"), Decimal("2805310.91")),
               "2026-03": (Decimal("4670379.38"), Decimal("4670379.38")),
               "2026-04": (Decimal("6058239.46"), Decimal("6058402.42")),
               "2026-05": (Decimal("5698996.99"), Decimal("5706411.28")),
               "2026-06": (Decimal("6521807.04"), Decimal("6526140.66"))}
    shares = {m: bf.compute_completed_share(c, a) for m, (c, a) in medidos.items()}
    assert min(shares.values()) == Decimal("0.9987")
    assert all(s >= bf.MATURITY_FLOOR_PROVISIONAL for s in shares.values())


def test_2c_os_tres_eixos_nunca_se_escondem():
    """load_stale, coverage incompleta e maturity_immature coexistem, e
    nenhum deles some quando os outros estao presentes."""
    r = bf.classify_scope(
        rows_present=188, eligible_rows=0, daily_gmv=Decimal("4156389.94"),
        source_files=212, load_is_stale=True, month_is_closed=True,
        completed_share=Decimal("0.0007"),
        mature_share_floor=bf.MATURITY_FLOOR_PROVISIONAL,
        brands_present=4, brands_expected=5)
    assert r["load_status"] == bf.LOAD_STALE
    assert r["coverage_status"] == bf.COVERAGE_BELOW
    assert r["maturity_status"] == bf.MATURITY_IMMATURE
    assert r["eligibility_status"] == bf.ELIG_NONE
    assert len(set(r["alerts"])) >= 4

    # e cada eixo isolado continua visivel quando os outros estao saudaveis
    so_stale = bf.classify_scope(
        rows_present=10, eligible_rows=10, daily_gmv=Decimal("100"),
        source_files=212, load_is_stale=True, month_is_closed=True,
        completed_share=Decimal("1.0"),
        mature_share_floor=bf.MATURITY_FLOOR_PROVISIONAL,
        brands_present=5, brands_expected=5)
    assert so_stale["load_status"] == bf.LOAD_STALE
    assert so_stale["maturity_status"] == bf.MATURITY_MATURE
    assert so_stale["coverage_status"] == bf.COVERAGE_OK


def test_2c_piso_e_provisorio_e_declarado_como_tal():
    import inspect
    src = inspect.getsource(bf)
    i = src.index("MATURITY_FLOOR_PROVISIONAL")
    contexto = src[max(0, i - 400):i]
    assert "PROVISORIO" in contexto.upper()
    assert "0,9987" in contexto or "0.9987" in contexto   # a evidencia medida
    assert bf.MATURITY_FLOOR_PROVISIONAL == Decimal("0.99")
