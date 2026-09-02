"""Gate UE8-I1 — testes do sync de descontos do pedido TikTok.

Comportamento contra fakes sempre que possivel; inspecao estrutural (AST/texto)
apenas para invariantes que sao AUSENCIAS -- "nao existe coluna de total", "nao
existe retry" -- que nenhum fake consegue exercitar.
"""
from __future__ import annotations

import ast
import io
import json
import pathlib
import re
import tokenize
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import pipelines.sync_tiktok_order_discounts_daily as sync
from pipelines.connectors.tiktok.connector import (
    BRANDS_IN_SCOPE,
    COMMERCIAL_ORDER_STATUSES,
    KNOWN_ORDER_STATUSES,
    NON_COMMERCIAL_ORDER_STATUSES,
)

RAIZ = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = (RAIZ / "apps" / "api" / "alembic" / "versions"
             / "013_create_fact_tiktok_order_discounts_daily.py")
SYNC_PATH = RAIZ / "pipelines" / "sync_tiktok_order_discounts_daily.py"
MIGRATION_SRC = MIGRATION.read_text(encoding="utf-8")
SYNC_SRC = SYNC_PATH.read_text(encoding="utf-8")


def apenas_codigo(fonte: str) -> str:
    """Fonte sem comentarios e sem docstrings.

    Toda invariante de AUSENCIA ("nao existe coluna de total", "nao usa a chave
    de lock da UE2-C") precisa disto: as docstrings deste projeto EXPLICAM o que
    foi deliberadamente omitido, e uma varredura ingenua acusa a explicacao como
    se fosse a violacao.
    """
    sem_comentario = []
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type == tokenize.COMMENT:
            continue
        sem_comentario.append(tok)
    texto = tokenize.untokenize(sem_comentario)

    arvore = ast.parse(texto)
    docstrings = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)):
            corpo = getattr(no, "body", [])
            if (corpo and isinstance(corpo[0], ast.Expr)
                    and isinstance(corpo[0].value, ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                docstrings.add(id(corpo[0]))
    linhas_doc: set[int] = set()
    for no in ast.walk(arvore):
        if id(no) in docstrings:
            linhas_doc.update(range(no.lineno, (no.end_lineno or no.lineno) + 1))
    return "\n".join(
        l for i, l in enumerate(texto.splitlines(), start=1)
        if i not in linhas_doc
    )


MIGRATION_CODE = apenas_codigo(MIGRATION_SRC)
SYNC_CODE = apenas_codigo(SYNC_SRC)

#: 2026-09-02 12:00 BRT -> last_closed_date = 2026-09-01
AGORA = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)


# ===========================================================================
# Fakes
# ===========================================================================


class FakeResult:
    def __init__(self, linhas=None, rowcount=1):
        self._linhas = linhas or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._linhas)

    def one(self):
        return self._linhas[0]

    def scalar(self):
        return self._linhas[0] if self._linhas else None

    def scalar_one(self):
        return self._linhas[0]


class FakeConn:
    """Registra todo SQL executado; devolve respostas roteadas por trecho."""

    def __init__(self, rotas=None, nome="conn"):
        self.nome = nome
        self.sql: list[str] = []
        self.params: list[dict] = []
        self.rotas = rotas or {}
        self._seq = 0

    def execute(self, sql, params=None):
        texto = " ".join(str(sql).split())
        self.sql.append(texto)
        self.params.append(params or {})
        for chave, resposta in self.rotas.items():
            if chave in texto:
                if callable(resposta):
                    return resposta(self._seq, params)
                if isinstance(resposta, list):
                    idx = min(self._seq, len(resposta) - 1)
                    self._seq += 1
                    return resposta[idx]
                return resposta
        return FakeResult()


class FakeSession:
    def __init__(self, conn=None):
        self._conn = conn or FakeConn()
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def connection(self):
        return self._conn

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def linha(ref_date=date(2026, 8, 20), brand="apice", com=10, canc=2,
          upoh=0, unknown=0, sd=Decimal("-100.00"), ps=Decimal("30.00"),
          gmv=Decimal("500.00"), fpv=Decimal("630.00"),
          csd=Decimal("-20.00"), cps=Decimal("5.00"),
          null_com=0, null_can=0, upd=datetime(2026, 8, 21, 3, 0),
          raw_upd=datetime(2026, 8, 21, 5, 0)):
    return sync.SourceRow(
        ref_date=ref_date, brand=brand,
        commercial_orders=com, official_gmv=gmv, full_product_value=fpv,
        seller_discount_signed=sd, platform_subsidy_amount=ps,
        cancelled_orders=canc, cancelled_seller_discount_signed=csd,
        cancelled_platform_subsidy_amount=cps,
        source_max_updated_at=upd,
        raw_max_updated_at=raw_upd,
        total_dedup=com + canc + upoh + unknown,
        unpaid_onhold_orders=upoh, unknown_orders=unknown,
        commercial_null_money=null_com, cancelled_null_money=null_can,
    )


def snap(rows=None, dfrom=date(2026, 8, 20), dto=date(2026, 8, 20)):
    rows = rows if rows is not None else [linha()]
    s = sync.SourceSnapshot(window=sync.Window(dfrom, dto), rows=rows)
    s.missing_keys, s.brands_absent = sync._coverage(rows, s.window)
    return s


# ===========================================================================
# 1-4 — Migration
# ===========================================================================


def test_01_migration_encadeia_no_head_012():
    arvore = ast.parse(MIGRATION_SRC)
    consts = {
        n.targets[0].id: n.value.value
        for n in arvore.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
    }
    assert consts["revision"] == "013"
    assert consts["down_revision"] == "012"


def test_02_exatamente_15_checks_nos_grupos_certos():
    nomes = re.findall(r"CONSTRAINT (ck_ftodd_\w+)\s+CHECK", MIGRATION_SRC)
    assert len(nomes) == 15, f"esperado 15 CHECKs, encontrado {len(nomes)}: {nomes}"
    assert len([n for n in nomes if n.endswith("_nao_nan")]) == 6
    assert len([n for n in nomes
                if n.endswith("_nao_positivo") or n.endswith("_nao_negativo")]) == 8
    assert "ck_ftodd_brand_nao_vazia" in nomes
    # Nome mais longo cabe no limite de identificador do PostgreSQL.
    assert max(len(n) for n in nomes) <= 63


def test_03_pk_e_indice_declarados():
    assert "PRIMARY KEY (ref_date, brand)" in MIGRATION_SRC
    assert "CREATE INDEX idx_ftodd_brand_ref_date" in MIGRATION_SRC
    assert "(brand, ref_date)" in MIGRATION_SRC
    # Padrao fail-fast de 006-012: colisao tem de falhar alto.
    assert "CREATE TABLE IF NOT EXISTS" not in MIGRATION_SRC


def test_04_downgrade_restrito_ao_que_foi_criado():
    corpo = MIGRATION_SRC.split("def downgrade")[1]
    assert "DROP INDEX IF EXISTS marts.idx_ftodd_brand_ref_date" in corpo
    assert "DROP TABLE IF EXISTS marts.fact_tiktok_order_discounts_daily" in corpo
    for proibido in ("DROP SCHEMA", "CASCADE", "fact_tiktok_brand_content_daily",
                     "fact_marketplace_daily_performance", "audit."):
        assert proibido not in corpo, f"downgrade toca objeto alheio: {proibido}"


# ===========================================================================
# 5-6 — D-1 e relogio
# ===========================================================================


def test_05_janela_explicita_com_d0_e_recusada():
    with pytest.raises(ValueError, match="posterior ao ultimo dia fechado"):
        sync.resolve_window(sync.MODE_INCREMENTAL, AGORA,
                            date(2026, 9, 1), date(2026, 9, 2))


def test_05b_todos_os_modos_terminam_em_d_menos_1():
    for modo in (sync.MODE_INCREMENTAL, sync.MODE_BACKFILL, sync.MODE_FULL):
        w = sync.resolve_window(modo, AGORA)
        assert w.date_to == date(2026, 9, 1), modo


def test_06_relogio_e_america_sao_paulo_nao_do_processo():
    # 2026-09-02 02:00 UTC = 2026-09-01 23:00 BRT -> D-1 = 2026-08-31
    utc_madrugada = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    assert sync.resolve_window(
        sync.MODE_INCREMENTAL, utc_madrugada).date_to == date(2026, 8, 31)


# ===========================================================================
# 7-11 — Modos e decisao auto
# ===========================================================================


def test_07_larguras_das_janelas():
    assert sync.resolve_window(sync.MODE_INCREMENTAL, AGORA).days == 10
    assert sync.resolve_window(sync.MODE_BACKFILL, AGORA).days == 90
    assert sync.resolve_window(sync.MODE_FULL, AGORA).date_from == sync.FULL_MIN_DATE


def test_08_decisao_auto_le_a_auditoria_sob_o_lock():
    conn = FakeConn({"MAX(finished_at)": FakeResult([None])})
    assert sync.decide_effective_mode(conn, AGORA) == sync.MODE_FULL
    assert any("source_sync_run" in s for s in conn.sql)


def test_09_full_e_devido_ate_haver_success_no_mes_corrente():
    respostas = iter([
        FakeResult([datetime(2026, 9, 1, tzinfo=timezone.utc)]),   # _full ok
        FakeResult([datetime(2026, 8, 30, tzinfo=timezone.utc)]),  # _backfill ok
    ])
    conn = FakeConn({"MAX(finished_at)": lambda *_: next(respostas)})
    assert sync.decide_effective_mode(conn, AGORA) == sync.MODE_INCREMENTAL


def test_09b_full_de_mes_anterior_nao_consome_a_obrigacao():
    # A query filtra por `finished_at >= inicio do mes BRT`; sucesso de agosto
    # nao aparece, entao o retorno e' None e o full continua devido.
    conn = FakeConn({"MAX(finished_at)": FakeResult([None])})
    assert sync.decide_effective_mode(conn, AGORA) == sync.MODE_FULL
    desde = conn.params[0]["desde"]
    assert desde.year == 2026 and desde.month == 9 and desde.day == 1


def test_10_backfill_durável_via_nome_proprio():
    respostas = iter([
        FakeResult([datetime(2026, 9, 1, tzinfo=timezone.utc)]),  # _full ok
        FakeResult([None]),                                       # _backfill nao
    ])
    conn = FakeConn({"MAX(finished_at)": lambda *_: next(respostas)})
    assert sync.decide_effective_mode(conn, AGORA) == sync.MODE_BACKFILL
    assert conn.params[1]["fonte"] == sync.BACKFILL_AUDIT_SOURCE


def test_11_incremental_quando_as_duas_obrigacoes_estao_satisfeitas():
    respostas = iter([
        FakeResult([datetime(2026, 9, 1, tzinfo=timezone.utc)]),
        FakeResult([datetime(2026, 8, 31, tzinfo=timezone.utc)]),
    ])
    conn = FakeConn({"MAX(finished_at)": lambda *_: next(respostas)})
    assert sync.decide_effective_mode(conn, AGORA) == sync.MODE_INCREMENTAL


def test_11b_apenas_success_consome_obrigacao():
    assert "status = 'success'" in str(sync._SQL_ULTIMO_SUCESSO)
    for proibido in ("'running'", "'failed'"):
        assert proibido not in str(sync._SQL_ULTIMO_SUCESSO)


# ===========================================================================
# 12-13 — Auditoria
# ===========================================================================


def test_12_auditoria_abre_duas_linhas_no_full_e_no_backfill():
    assert sync.audit_sources_for_mode(sync.MODE_INCREMENTAL) == (
        sync.CANONICAL_AUDIT_SOURCE,)
    assert sync.audit_sources_for_mode(sync.MODE_BACKFILL) == (
        sync.CANONICAL_AUDIT_SOURCE, sync.BACKFILL_AUDIT_SOURCE)
    assert sync.audit_sources_for_mode(sync.MODE_FULL) == (
        sync.CANONICAL_AUDIT_SOURCE, sync.FULL_AUDIT_SOURCE)


def test_12b_audit_start_grava_running_antes_de_qualquer_leitura():
    conn = FakeConn({"INSERT INTO audit.source_sync_run": FakeResult([7])})
    ids = sync.audit_start(conn, sync.audit_sources_for_mode(sync.MODE_FULL),
                           sync.Window(date(2026, 8, 1), date(2026, 8, 31)))
    assert set(ids) == {sync.CANONICAL_AUDIT_SOURCE, sync.FULL_AUDIT_SOURCE}
    assert all("'running'" in s for s in conn.sql)


def test_13_success_nao_pode_gravar_error_message():
    conn = FakeConn()
    with pytest.raises(sync.DiscountSyncError, match="data_quality_check"):
        sync.audit_finish(conn, {"a": 1}, "success", 10, 10, "qualquer texto")
    assert conn.sql == []


def test_13b_success_grava_error_message_nulo():
    conn = FakeConn({"UPDATE audit.source_sync_run": FakeResult(rowcount=1)})
    sync.audit_finish(conn, {"a": 1}, "success", 10, 10, None)
    assert conn.params[0]["erro"] is None


def test_13c_update_de_auditoria_exige_rowcount_1():
    conn = FakeConn({"UPDATE audit.source_sync_run": FakeResult(rowcount=0)})
    with pytest.raises(sync.DiscountSyncError, match="afetou 0 linha"):
        sync.audit_finish(conn, {"a": 1}, "success", 1, 1, None)


# ===========================================================================
# 14 — Dry-run
# ===========================================================================


def test_14_dry_run_nao_abre_conexao_gravavel(monkeypatch):
    def explode(*_a, **_k):
        raise AssertionError("dry-run abriu sessao no destino")

    monkeypatch.setattr(sync, "LocalSession", explode)

    class DMSession(FakeSession):
        def connection(self):
            return FakeConn({"WITH raw_dedup": FakeResult([
                dict(ref_date=date(2026, 8, 25), brand="apice",
                     commercial_orders=3, official_gmv=Decimal("10"),
                     full_product_value=Decimal("12"),
                     seller_discount_signed=Decimal("-2"),
                     platform_subsidy_amount=Decimal("0"),
                     cancelled_orders=0,
                     cancelled_seller_discount_signed=Decimal("0"),
                     cancelled_platform_subsidy_amount=Decimal("0"),
                     source_max_updated_at=None, raw_max_updated_at=None,
                     total_dedup=3, unpaid_onhold_orders=0, unknown_orders=0,
                     commercial_null_money=0, cancelled_null_money=0),
            ])})

    r = sync.run_dry(sync.MODE_INCREMENTAL, AGORA, dm_factory=DMSession)
    assert r["applied"] is False and r["rows"] == 1


def test_14b_dry_run_recusa_auto():
    with pytest.raises(sync.DiscountSyncError, match="exige --apply"):
        sync.run_dry(sync.MODE_AUTO, AGORA)


def test_14c_dry_run_nao_escreve_data_quality():
    corpo = SYNC_SRC.split("def run_dry")[1].split("def run_apply")[0]
    assert "write_quality_checks" not in corpo
    assert "audit_start" not in corpo


# ===========================================================================
# 15-19 — Ausencia, zero e cobertura
# ===========================================================================


def test_15_janela_vazia_bloqueia_e_preserva_destino():
    with pytest.raises(sync.DiscountSyncError, match="destino intacto"):
        sync.validate_contract(snap(rows=[]))


def test_16_marca_ausente_nao_bloqueia_e_vira_warning():
    s = snap(rows=[linha(brand="apice")])
    avisos = sync.validate_contract(s)          # nao levanta
    assert len(s.brands_absent) == len(BRANDS_IN_SCOPE) - 1
    assert any("nao bloqueia" in a.lower() for a in avisos)


def test_17_cobertura_incompleta_gera_warn_nunca_fail():
    rows = [linha(ref_date=date(2026, 8, 20), brand="apice"),
            linha(ref_date=date(2026, 8, 21), brand="apice"),
            linha(ref_date=date(2026, 8, 20), brand="kokeshi")]
    s = snap(rows, date(2026, 8, 20), date(2026, 8, 21))
    assert s.missing_keys == [(date(2026, 8, 21), "kokeshi")]
    assert sync.coverage_status(s) == "incomplete_brand_coverage"
    cobertura = next(c for c in sync.build_quality_checks(s, {}, "incremental")
                     if c["nome"] == "ftodd_cobertura_chaves_observadas")
    assert cobertura["status"] == "warn"
    nota = json.loads(cobertura["detalhes"])["note"].lower()
    assert "nao e prova independente de completude da ingestao" in nota


def test_18_zero_comercial_com_atividade_publica_linha_com_zero():
    s = snap(rows=[linha(com=0, canc=4, sd=Decimal("0"), ps=Decimal("0"),
                         gmv=Decimal("0"), fpv=Decimal("0"))])
    sync.validate_contract(s)
    conn = FakeConn({"so_staging": FakeResult(
        [{"so_staging": 0, "so_destino": 0}])})
    assert sync.publish_in_transaction(conn, s, "run") == 1
    inserido = next(p for p, q in zip(conn.params, conn.sql)
                    if "INSERT INTO stg_ftodd" in q)
    assert inserido["commercial_orders"] == 0


def test_19_dia_sem_pedido_algum_nao_ganha_linha_zerada():
    # A grade e' derivada do OBSERVADO: 21/08 nao aparece porque nao ha linha.
    rows = [linha(ref_date=date(2026, 8, 20), brand=b) for b in BRANDS_IN_SCOPE]
    s = snap(rows, date(2026, 8, 20), date(2026, 8, 22))
    assert s.missing_keys == [] and s.brands_absent == []
    assert {r.ref_date for r in s.rows} == {date(2026, 8, 20)}
    conn = FakeConn({"so_staging": FakeResult(
        [{"so_staging": 0, "so_destino": 0}])})
    assert sync.publish_in_transaction(conn, s, "run") == len(BRANDS_IN_SCOPE)


# ===========================================================================
# 20-23 — Bloqueios de contrato
# ===========================================================================


def test_20_status_desconhecido_bloqueia():
    with pytest.raises(sync.DiscountSyncError, match="KNOWN_ORDER_STATUSES"):
        sync.validate_contract(snap(rows=[linha(unknown=1)]))


def test_21_monetario_nulo_bloqueia_e_coalesce_e_proibido():
    with pytest.raises(sync.DiscountSyncError, match="COALESCE"):
        sync.validate_contract(snap(rows=[linha(null_com=1)]))
    with pytest.raises(sync.DiscountSyncError):
        sync.validate_contract(snap(rows=[linha(null_can=3)]))
    # A proibicao vale para o SQL: nenhum agregado pode converter ausencia em
    # zero. A palavra aparece em mensagem de erro, e isso e' documentacao.
    for sql in (sync.SQL_AGREGADO, sync.SQL_STAGING_INSERT,
                sync.SQL_INSERT_DO_STAGING, sync.SQL_DELETE_JANELA,
                sync.SQL_EXCEPT_BIDIRECIONAL):
        assert "COALESCE" not in str(sql).upper()


def test_22_procedencia_anulavel_atravessa_o_caminho():
    s = snap(rows=[linha(upd=None, raw_upd=None)])
    sync.validate_contract(s)
    conn = FakeConn({"so_staging": FakeResult(
        [{"so_staging": 0, "so_destino": 0}])})
    sync.publish_in_transaction(conn, s, "run")
    inserido = next(p for p, q in zip(conn.params, conn.sql)
                    if "INSERT INTO stg_ftodd" in q)
    assert inserido["source_max_updated_at"] is None
    assert inserido["raw_max_updated_at"] is None
    assert "source_max_updated_at             TIMESTAMP," in MIGRATION_SRC
    assert "raw_max_updated_at                TIMESTAMP," in MIGRATION_SRC


def test_23_fechamento_das_quatro_populacoes():
    ok = snap(rows=[linha(com=7, canc=2, upoh=1)])
    assert ok.total_dedup == 10
    sync.validate_contract(ok)

    quebrado = snap(rows=[linha(com=7, canc=2, upoh=1)])
    quebrado.rows[0].total_dedup = 11
    with pytest.raises(sync.DiscountSyncError, match="nao fecha"):
        sync.validate_contract(quebrado)


# ===========================================================================
# 24-27 — Formulas e sinais
# ===========================================================================


def test_24_sinal_invertido_exatamente_uma_vez():
    sql = str(sync.SQL_AGREGADO)
    # Duas ocorrencias: ramo ELSE da populacao comercial e da cancelada. O ramo
    # THEN e' `0::numeric`, entao a inversao continua acontecendo uma unica vez
    # por populacao, dentro do ramo que tem linhas.
    assert sql.count("-SUM(o.seller_discount)") == 2
    assert "-SUM(o.platform_discount)" not in sql
    with pytest.raises(sync.DiscountSyncError, match="inverteu duas vezes"):
        sync.validate_contract(snap(rows=[linha(sd=Decimal("50"))]))


def test_25_platform_subsidy_preservado_positivo():
    sql = str(sync.SQL_AGREGADO)
    assert "SUM(o.platform_discount) FILTER" in " ".join(sql.split())
    with pytest.raises(sync.DiscountSyncError, match="subsidio"):
        sync.validate_contract(snap(rows=[linha(ps=Decimal("-1"))]))


def test_26_full_product_value_e_a_soma_das_tres_parcelas():
    sql = " ".join(str(sync.SQL_AGREGADO).split())
    assert "SUM(o.sub_total + o.seller_discount + o.platform_discount)" in sql


def test_27_nenhum_campo_de_total_dos_descontos():
    for proibido in ("total_discount", "discount_total", "net_discount",
                     "desconto_total"):
        assert proibido not in MIGRATION_CODE.lower(), proibido
        assert proibido not in SYNC_CODE.lower(), proibido
    assert "seller_discount_signed + platform_subsidy_amount" not in SYNC_CODE
    assert set(sync.TARGET_COLUMNS) & {"total_discount", "net_discount"} == set()


# ===========================================================================
# 28-33 — Publicacao, reconciliacao e maquina de estados
# ===========================================================================


def test_28_delete_limitado_a_janela():
    d = " ".join(str(sync.SQL_DELETE_JANELA).split())
    assert "WHERE ref_date >= :date_from AND ref_date <= :date_to" in d
    conn = FakeConn({"so_staging": FakeResult(
        [{"so_staging": 0, "so_destino": 0}])})
    sync.publish_in_transaction(conn, snap(), "run")
    params_delete = next(p for p, q in zip(conn.params, conn.sql)
                         if q.startswith("DELETE FROM"))
    assert params_delete == {"date_from": date(2026, 8, 20),
                             "date_to": date(2026, 8, 20)}


def test_29_insert_com_colunas_explicitas_sem_select_estrela():
    assert "SELECT *" not in SYNC_CODE
    for sql in (sync.SQL_STAGING_INSERT, sync.SQL_INSERT_DO_STAGING):
        texto = " ".join(str(sql).split())
        for col in sync.TARGET_COLUMNS:
            assert col in texto
    assert "INCLUDING DEFAULTS" in str(sync.SQL_STAGING_CREATE)
    assert "ON COMMIT DROP" in str(sync.SQL_STAGING_CREATE)


def test_30_except_bidirecional_derruba_a_transacao():
    conn = FakeConn({"so_staging": FakeResult(
        [{"so_staging": 1, "so_destino": 0}])})
    with pytest.raises(sync.DiscountSyncError, match="Sem tolerancia"):
        sync.publish_in_transaction(conn, snap(), "run")
    texto = " ".join(str(sync.SQL_EXCEPT_BIDIRECIONAL).split())
    assert texto.count("EXCEPT") == 2


def test_31_rollback_integral_antes_do_commit(monkeypatch):
    class DM(FakeSession):
        def connection(self):
            return FakeConn({"WITH raw_dedup": FakeResult([])})   # janela vazia

    pub = FakeSession()
    aud = FakeSession(FakeConn(
        {"INSERT INTO audit.source_sync_run": FakeResult([1]),
         "UPDATE audit.source_sync_run": FakeResult(rowcount=1)}))
    sessoes = iter([pub, aud])
    monkeypatch.setattr(sync, "LocalSession", lambda: next(sessoes))
    with pytest.raises(sync.DiscountSyncError):
        sync.run_apply(sync.MODE_INCREMENTAL, AGORA, dm_factory=DM)
    assert pub.commits == 0 and pub.rollbacks == 1


def test_32_maquina_de_estados_nomeada_e_pos_commit_nunca_marca_failed():
    corpo = SYNC_SRC.split("def run_apply")[1]
    assert "PUBLICACAO_COMMIT_CONFIRMADO" in corpo
    guarda = corpo.split("except Exception as exc:")[1].split("raise")[0]
    assert "PUBLICACAO_NAO_TENTADA" in guarda
    assert "PUBLICACAO_ROLLBACK_CONFIRMADO" in guarda
    # `failed` so' e' alcancavel pelos dois estados que PROVAM nao-publicacao.
    assert '"failed"' in guarda


def test_33_falha_de_auditoria_apos_commit_nao_reclassifica_o_dado():
    assert issubclass(sync.AuditoriaIncompleta, RuntimeError)
    pos = SYNC_SRC.split("# --- pos-commit")[1]
    assert "AuditoriaIncompleta" in pos
    assert '"failed"' not in pos, "pos-commit nao pode marcar failed"
    assert "running" in pos


# ===========================================================================
# 34-35 — Sanitizacao e ausencia de automacao
# ===========================================================================


@pytest.mark.parametrize("bruto,proibido", [
    ("conn to postgresql://u:p@10.0.0.9:5432/db failed", "postgresql://"),
    ("could not connect to host=prod-db.internal port=5432", "prod-db"),
    ("server 192.168.1.44 refused", "192.168.1.44"),
    ("auth failed password=hunter2", "hunter2"),
])
def test_34_sanitizacao_remove_dsn_ip_host_e_senha(bruto, proibido):
    limpo = sync.sanitizar(bruto)
    assert proibido not in limpo and "[REDACTED]" in limpo


def test_34b_sanitizacao_limita_tamanho():
    assert len(sync.sanitizar("x" * 5000)) <= 500


def test_35_sem_retry_sleep_scheduler_airflow():
    arvore = ast.parse(SYNC_SRC)
    chamadas = {
        n.func.attr if isinstance(n.func, ast.Attribute) else
        (n.func.id if isinstance(n.func, ast.Name) else "")
        for n in ast.walk(arvore) if isinstance(n, ast.Call)
    }
    assert "sleep" not in chamadas
    for proibido in ("retry", "airflow", "schedtasks", "schtasks",
                     "full_daily", "orchestrate"):
        assert proibido not in SYNC_CODE.lower(), proibido
    assert not [n for n in ast.walk(arvore) if isinstance(n, ast.While)]


# ===========================================================================
# Reuso do contrato DQ-TK1 — nao pode haver copia divergente
# ===========================================================================


def test_36_constantes_sao_importadas_nao_copiadas():
    assert sync.BRANDS_IN_SCOPE is BRANDS_IN_SCOPE
    assert sync.COMMERCIAL_ORDER_STATUSES is COMMERCIAL_ORDER_STATUSES
    assert sync.NON_COMMERCIAL_ORDER_STATUSES is NON_COMMERCIAL_ORDER_STATUSES
    assert sync.KNOWN_ORDER_STATUSES is KNOWN_ORDER_STATUSES
    for literal in ("'COMPLETED'", "'DELIVERED'", "'IN_TRANSIT'", "'UNPAID'"):
        assert literal not in SYNC_SRC, f"status literal duplicado: {literal}"


def test_37_dedup_reproduz_a_ordem_do_conector():
    conector = (RAIZ / "pipelines" / "connectors" / "tiktok"
                / "connector.py").read_text(encoding="utf-8")
    ordem = "ORDER BY order_id, updated_at DESC NULLS LAST, id DESC"
    assert ordem in conector and ordem in SYNC_SRC


def test_38_detalhes_do_dq_nao_carregam_identificador_pessoal():
    rows = [linha(ref_date=date(2026, 8, 20 + i), brand="apice")
            for i in range(3)] + [linha(ref_date=date(2026, 8, 20),
                                        brand="kokeshi")]
    s = snap(rows, date(2026, 8, 20), date(2026, 8, 22))
    for c in sync.build_quality_checks(s, {sync.CANONICAL_AUDIT_SOURCE: 9},
                                       "incremental"):
        payload = json.loads(c["detalhes"])
        assert "order_id" not in c["detalhes"]
        assert set(payload) <= {
            "sync_run_id", "mode", "date_from", "date_to", "commercial",
            "cancelled", "unpaid_onhold", "unknown", "total_dedup",
            "unknown_orders", "null_money_rows", "coverage_status",
            "missing_keys_total", "missing_keys_sample", "truncated",
            "brands_absent", "note", "rows_fora_do_contrato",
            "last_closed_date", "teto_conhecido", "brands_expected",
            "brands_observed",
        }, c["nome"]


def test_39_amostra_de_chaves_ausentes_e_truncada():
    dias = [date(2026, 6, 1) + timedelta(days=i) for i in range(60)]
    rows = [linha(ref_date=d, brand="apice") for d in dias]
    rows.append(linha(ref_date=dias[0], brand="kokeshi"))
    s = snap(rows, dias[0], dias[-1])
    cobertura = next(c for c in sync.build_quality_checks(s, {}, "backfill")
                     if c["nome"] == "ftodd_cobertura_chaves_observadas")
    payload = json.loads(cobertura["detalhes"])
    assert payload["missing_keys_total"] == 59
    assert len(payload["missing_keys_sample"]) == sync.MAX_KEYS_IN_DETAILS
    assert payload["truncated"] is True


def test_40_leitura_da_fonte_e_repeatable_read_e_read_only():
    conn = FakeConn({"WITH raw_dedup": FakeResult([])})
    sync.read_source(conn, sync.Window(date(2026, 8, 1), date(2026, 8, 2)))
    assert any("REPEATABLE READ" in s for s in conn.sql)
    assert any("READ ONLY" in s for s in conn.sql)
    assert any("statement_timeout" in s for s in conn.sql)


def test_41_janela_e_aberta_a_direita():
    conn = FakeConn({"WITH raw_dedup": FakeResult([])})
    sync.read_source(conn, sync.Window(date(2026, 8, 1), date(2026, 8, 2)))
    p = next(p for p, q in zip(conn.params, conn.sql) if "raw_dedup" in q)
    assert p["date_to_exclusive"] == datetime(2026, 8, 3, 0, 0)


def test_42_advisory_lock_tem_chave_propria():
    assert sync.ADVISORY_LOCK_KEY == 912130013
    assert "912120012" not in SYNC_CODE   # chave da UE2-C (afiliados)


def test_43_nao_existe_tabela_de_sync_state():
    assert "sync_state" not in MIGRATION_CODE.lower()
    assert "sync_state" not in SYNC_CODE.lower()


# ===========================================================================
# Regressao dos findings da correcao pre-piloto (UE8-I1)
# ===========================================================================


def test_f1_cancelled_nao_entra_em_unpaid_on_hold():
    assert set(sync.UNPAID_ON_HOLD_STATUSES) == (
        set(NON_COMMERCIAL_ORDER_STATUSES) - {"CANCELLED"})
    assert "CANCELLED" not in sync.UNPAID_ON_HOLD_STATUSES
    # Derivada, nao copiada: qualquer status novo em NON_COMMERCIAL aparece aqui.
    assert set(sync.UNPAID_ON_HOLD_STATUSES) < set(NON_COMMERCIAL_ORDER_STATUSES)


def test_f1b_fechamento_fecha_com_cancelados_reais():
    """O bug: usar a tupla inteira contaria CANCELLED duas vezes."""
    s = snap(rows=[linha(com=100, canc=37, upoh=6)])
    sync.validate_contract(s)               # nao levanta
    assert s.commercial + s.cancelled + s.unpaid_onhold + s.unknown == s.total_dedup
    assert s.cancelled == 37 and s.unpaid_onhold == 6

    # Contraprova: se unpaid_onhold reabsorvesse os cancelados, quebraria.
    duplicado = snap(rows=[linha(com=100, canc=37, upoh=6)])
    duplicado.rows[0].unpaid_onhold_orders = 6 + 37
    with pytest.raises(sync.DiscountSyncError, match="nao fecha"):
        sync.validate_contract(duplicado)


def test_f1c_bind_do_sql_recebe_a_tupla_derivada():
    conn = FakeConn({"WITH raw_dedup": FakeResult([])})
    sync.read_source(conn, sync.Window(date(2026, 8, 1), date(2026, 8, 2)))
    p = next(p for p, q in zip(conn.params, conn.sql) if "raw_dedup" in q)
    assert p["unpaid_onhold_statuses"] == sync.UNPAID_ON_HOLD_STATUSES
    assert "non_commercial_statuses" not in p


def test_f2_populacao_vazia_vira_zero_por_case_sem_coalesce():
    sql = " ".join(str(sync.SQL_AGREGADO).split())
    # 4 monetarios comerciais + 2 cancelados = 6 CASE.
    assert sql.count("CASE WHEN COUNT(*) FILTER") == 6
    assert "THEN 0::numeric ELSE" in sql
    assert "COALESCE" not in sql.upper()


def test_f2b_apenas_unpaid_produz_zero_comercial_medido():
    """Chave observada so' com UNPAID: comercial vazia -> zero, nao NULL."""
    s = snap(rows=[linha(com=0, canc=0, upoh=5,
                         gmv=Decimal("0"), fpv=Decimal("0"),
                         sd=Decimal("0"), ps=Decimal("0"),
                         csd=Decimal("0"), cps=Decimal("0"))])
    sync.validate_contract(s)
    conn = FakeConn({"so_staging": FakeResult(
        [{"so_staging": 0, "so_destino": 0}])})
    sync.publish_in_transaction(conn, s, "run")
    ins = next(p for p, q in zip(conn.params, conn.sql)
               if "INSERT INTO stg_ftodd" in q)
    assert ins["commercial_orders"] == 0
    for campo in ("official_gmv", "full_product_value",
                  "seller_discount_signed", "platform_subsidy_amount"):
        assert ins[campo] == Decimal("0"), campo
        assert ins[campo] is not None


def test_f2c_comercial_sem_cancelados_produz_zero_cancelado():
    s = snap(rows=[linha(com=8, canc=0, csd=Decimal("0"), cps=Decimal("0"))])
    sync.validate_contract(s)
    conn = FakeConn({"so_staging": FakeResult(
        [{"so_staging": 0, "so_destino": 0}])})
    sync.publish_in_transaction(conn, s, "run")
    ins = next(p for p, q in zip(conn.params, conn.sql)
               if "INSERT INTO stg_ftodd" in q)
    assert ins["cancelled_orders"] == 0
    assert ins["cancelled_seller_discount_signed"] == Decimal("0")
    assert ins["cancelled_platform_subsidy_amount"] == Decimal("0")


def test_f2d_nulo_dentro_da_populacao_continua_bloqueando():
    """Zero por populacao vazia NAO pode virar desculpa para nulo de origem."""
    with pytest.raises(sync.DiscountSyncError, match="COALESCE"):
        sync.validate_contract(snap(rows=[linha(com=5, null_com=1)]))
    with pytest.raises(sync.DiscountSyncError, match="COALESCE"):
        sync.validate_contract(snap(rows=[linha(canc=5, null_can=1)]))


class CommitQueLevanta(FakeSession):
    def commit(self):
        self.commits += 1
        raise RuntimeError("connection lost at postgresql://u:p@10.0.0.9/db")


def test_f3_commit_indeterminado_nao_vira_failed_nem_rollback(monkeypatch, caplog):
    class DM(FakeSession):
        def connection(self):
            return FakeConn({"WITH raw_dedup": FakeResult([
                dict(ref_date=date(2026, 8, 25), brand="apice",
                     commercial_orders=1, official_gmv=Decimal("10"),
                     full_product_value=Decimal("12"),
                     seller_discount_signed=Decimal("-2"),
                     platform_subsidy_amount=Decimal("0"),
                     cancelled_orders=0,
                     cancelled_seller_discount_signed=Decimal("0"),
                     cancelled_platform_subsidy_amount=Decimal("0"),
                     source_max_updated_at=None, raw_max_updated_at=None,
                     total_dedup=1, unpaid_onhold_orders=0, unknown_orders=0,
                     commercial_null_money=0, cancelled_null_money=0),
            ])})

    pub = CommitQueLevanta(FakeConn(
        {"so_staging": FakeResult([{"so_staging": 0, "so_destino": 0}])}))
    aud = FakeSession(FakeConn(
        {"INSERT INTO audit.source_sync_run": FakeResult([1]),
         "UPDATE audit.source_sync_run": FakeResult(rowcount=1)}))
    sessoes = iter([pub, aud])
    monkeypatch.setattr(sync, "LocalSession", lambda: next(sessoes))

    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError):
            sync.run_apply(sync.MODE_INCREMENTAL, AGORA, dm_factory=DM)

    # 1. nenhum rollback usado como prova
    assert pub.rollbacks == 0
    # 2. nenhuma auditoria marcada failed nem success
    updates = [p for p, q in zip(aud._conn.params, aud._conn.sql)
               if "UPDATE audit.source_sync_run" in q]
    assert updates == []
    # 3. mensagem sanitizada declara indeterminacao
    texto = " ".join(r.getMessage() for r in caplog.records)
    assert "INDETERMINADO" in texto
    assert "postgresql://" not in texto and "10.0.0.9" not in texto
    # 4. nenhuma segunda tentativa
    assert pub.commits == 1


def test_f3b_ordem_das_atribuicoes_no_codigo():
    corpo = SYNC_CODE.split("def run_apply")[1]
    i_ind = corpo.index("PUBLICACAO_INDETERMINADA")
    i_commit = corpo.index("sessao_pub.commit()")
    i_conf = corpo.index("PUBLICACAO_COMMIT_CONFIRMADO")
    assert i_ind < i_commit < i_conf


def test_f4_lock_e_transacional_sem_unlock_manual():
    assert "pg_advisory_xact_lock" in str(sync.SQL_LOCK)
    assert "pg_advisory_lock(" not in SYNC_CODE
    assert "pg_advisory_unlock" not in SYNC_CODE
    assert not hasattr(sync, "SQL_UNLOCK")


def test_f4b_lock_ocorre_antes_da_decisao_de_modo():
    corpo = SYNC_CODE.split("def run_apply")[1]
    assert corpo.index("SQL_LOCK") < corpo.index("decide_effective_mode")


def test_f4c_falha_pre_commit_faz_rollback_que_libera_o_lock(monkeypatch):
    class DM(FakeSession):
        def connection(self):
            return FakeConn({"WITH raw_dedup": FakeResult([])})   # janela vazia

    pub = FakeSession()
    aud = FakeSession(FakeConn(
        {"INSERT INTO audit.source_sync_run": FakeResult([1]),
         "UPDATE audit.source_sync_run": FakeResult(rowcount=1)}))
    sessoes = iter([pub, aud])
    monkeypatch.setattr(sync, "LocalSession", lambda: next(sessoes))
    with pytest.raises(sync.DiscountSyncError):
        sync.run_apply(sync.MODE_INCREMENTAL, AGORA, dm_factory=DM)
    assert pub.rollbacks == 1 and pub.commits == 0
    assert not any("advisory_unlock" in s for s in pub._conn.sql)


@pytest.mark.parametrize("modo", [sync.MODE_INCREMENTAL, sync.MODE_BACKFILL,
                                  sync.MODE_FULL, sync.MODE_AUTO])
def test_f5_janela_explicita_bloqueia_apply_antes_de_tudo(modo, monkeypatch):
    def nunca(*_a, **_k):
        raise AssertionError("abriu conexao apesar da janela explicita")

    monkeypatch.setattr(sync, "LocalSession", nunca)
    monkeypatch.setattr(sync, "DataMartSession", nunca)
    with pytest.raises(sync.DiscountSyncError, match="obrigacao durável"):
        sync.run_apply(modo, AGORA,
                       date(2026, 8, 1), date(2026, 8, 2))


def test_f5b_janela_explicita_continua_permitida_em_dry_run():
    class DM(FakeSession):
        def connection(self):
            return FakeConn({"WITH raw_dedup": FakeResult([
                dict(ref_date=date(2026, 8, 1), brand="apice",
                     commercial_orders=1, official_gmv=Decimal("1"),
                     full_product_value=Decimal("1"),
                     seller_discount_signed=Decimal("0"),
                     platform_subsidy_amount=Decimal("0"),
                     cancelled_orders=0,
                     cancelled_seller_discount_signed=Decimal("0"),
                     cancelled_platform_subsidy_amount=Decimal("0"),
                     source_max_updated_at=None, raw_max_updated_at=None,
                     total_dedup=1, unpaid_onhold_orders=0, unknown_orders=0,
                     commercial_null_money=0, cancelled_null_money=0),
            ])})

    r = sync.run_dry(sync.MODE_INCREMENTAL, AGORA,
                     date(2026, 8, 1), date(2026, 8, 2), dm_factory=DM)
    assert r["date_from"] == date(2026, 8, 1)
    assert r["applied"] is False


def test_f6_tres_relogios_permanecem_distintos():
    sql = " ".join(str(sync.SQL_AGREGADO).split())
    assert "MAX(o.updated_at_tiktok) AS source_max_updated_at" in sql
    assert "MAX(o.updated_at) AS raw_max_updated_at" in sql
    assert "source_max_updated_at" in sync.TARGET_COLUMNS
    assert "raw_max_updated_at" in sync.TARGET_COLUMNS
    # synced_at e' do destino, com DEFAULT now(): nunca vem da fonte.
    assert "synced_at" not in sync.TARGET_COLUMNS
    assert "synced_at                         TIMESTAMPTZ NOT NULL DEFAULT now()" \
        in MIGRATION_SRC


def test_f6b_migration_nao_afirma_mais_a_data_errada():
    assert "2026-03-12" not in MIGRATION_SRC
    assert "2026-06-12" in MIGRATION_SRC     # medicao real desta tabela
    assert "2025-06-06" in MIGRATION_SRC


def test_f6c_contagem_de_checks_continua_15_sem_check_em_timestamp():
    nomes = re.findall(r"CONSTRAINT (ck_ftodd_\w+)\s+CHECK", MIGRATION_SRC)
    assert len(nomes) == 15
    for proibido in ("source_max_updated_at", "raw_max_updated_at", "synced_at"):
        assert not any(proibido in n for n in nomes)


def test_f7_marca_ausente_gera_contagem_honesta_em_check_proprio():
    s = snap(rows=[linha(brand="apice")])          # 4 marcas ausentes
    checks = {c["nome"]: c for c in sync.build_quality_checks(s, {}, "incremental")}

    chaves = checks["ftodd_cobertura_chaves_observadas"]
    assert chaves["status"] == "pass" and chaves["failed"] == 0

    marcas = checks["ftodd_cobertura_marcas_do_escopo"]
    assert marcas["status"] == "warn"
    # A contagem honesta e' 4 marcas, nao zero e nao um cartesiano inventado.
    assert marcas["failed"] == len(BRANDS_IN_SCOPE) - 1
    payload = json.loads(marcas["detalhes"])
    assert payload["brands_observed"] == 1
    assert payload["brands_expected"] == len(BRANDS_IN_SCOPE)


def test_f7b_os_dois_checks_de_cobertura_nunca_sao_fail():
    rows = [linha(ref_date=date(2026, 8, 20), brand="apice"),
            linha(ref_date=date(2026, 8, 21), brand="apice"),
            linha(ref_date=date(2026, 8, 20), brand="kokeshi")]
    s = snap(rows, date(2026, 8, 20), date(2026, 8, 21))
    for c in sync.build_quality_checks(s, {}, "incremental"):
        if c["nome"].startswith("ftodd_cobertura"):
            assert c["status"] in ("pass", "warn")


def test_f8_check_de_d_menos_1_usa_o_relogio_da_janela():
    # 02:30 UTC de 02/09 ainda e' 01/09 em America/Sao_Paulo -> D-1 = 31/08.
    madrugada = datetime(2026, 9, 2, 2, 30, tzinfo=timezone.utc)
    teto = sync.last_closed_date(madrugada)
    assert teto == date(2026, 8, 31)

    s = snap(rows=[linha()], dfrom=date(2026, 8, 22), dto=teto)
    check = next(c for c in sync.build_quality_checks(s, {}, "incremental", teto)
                 if c["nome"] == "ftodd_teto_d_menos_1")
    payload = json.loads(check["detalhes"])
    assert payload["last_closed_date"] == teto.isoformat()
    assert payload["teto_conhecido"] is True


def test_f8b_build_quality_checks_nao_consulta_o_relogio_sozinho():
    corpo = SYNC_CODE.split("def build_quality_checks")[1].split("\ndef ")[0]
    assert "last_closed_date()" not in corpo


def test_f8c_dry_run_propaga_o_mesmo_teto():
    corpo = SYNC_CODE.split("def run_dry")[1].split("def run_apply")[0]
    assert "teto = last_closed_date(instante)" in corpo
    assert "build_quality_checks(snapshot, {}, mode, teto)" in corpo
