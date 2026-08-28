"""Gate UE2-C Task 2/3 — modo `auto`, auditoria, preflight, orquestracao e
frescor.

Nenhum teste aqui toca banco real: tudo por fakes/recorders. A separacao entre
"implementado" e "ativado" e' o ponto do gate, entao varios testes provam
justamente o que AINDA NAO acontece.
"""
from __future__ import annotations

import inspect
import re
import subprocess
import sys
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pipelines.sync_tiktok_affiliate_cost_order_monthly as sync  # noqa: E402
from pipelines.ops import health_check as hc  # noqa: E402
from pipelines.ops import orchestrate as orch  # noqa: E402
from pipelines.ops import preflight as pf  # noqa: E402
from pipelines.ops import schedule_plan as sp  # noqa: E402

BRT = ZoneInfo("America/Sao_Paulo")
FONTE = "tiktok_affiliate_cost_order_monthly"


def _codigo(alvo) -> str:
    """Codigo EXECUTAVEL de uma funcao/modulo: sem docstring e sem comentarios.

    Varrer o texto cru acusaria as proprias proibicoes escritas em prosa — o
    docstring que explica "nao fazemos COUNT integral" contem `COUNT(`. O que
    precisa ser verificado e' o codigo.
    """
    import ast as _ast
    import textwrap
    src = textwrap.dedent(inspect.getsource(alvo))
    arvore = _ast.parse(src)
    for no in _ast.walk(arvore):
        if isinstance(no, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                           _ast.ClassDef, _ast.Module)):
            corpo = getattr(no, "body", [])
            if (corpo and isinstance(corpo[0], _ast.Expr)
                    and isinstance(corpo[0].value, _ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                corpo.pop(0)          # remove o docstring
    return _ast.unparse(arvore)       # comentarios ja se perdem no unparse


# ===========================================================================
# Fakes minimos
# ===========================================================================

class CurDecisao:
    """Cursor que responde a consulta da decisao e registra o SQL."""

    def __init__(self, ultimo_full=None, recorder=None):
        self.ultimo_full = ultimo_full
        self.sqls = recorder if recorder is not None else []
        self._last = None

    def execute(self, sql, params=None):
        self.sqls.append((" ".join(str(sql).split()), params))
        self._last = (sql, params)

    def fetchone(self):
        return (self.ultimo_full,)

    def close(self):
        pass


class ConnAudit:
    """Conexao de auditoria falsa: registra INSERT/UPDATE e commits."""

    def __init__(self, falhar_no_insert=False, falhar_no_update=False):
        self.ops: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.falhar_no_insert = falhar_no_insert
        self.falhar_no_update = falhar_no_update
        self._proximo_id = 500

    def cursor(self):
        return _CurAudit(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    # helpers de leitura
    def inserts(self):
        return [p for k, p in self.ops if k == "insert"]

    def updates(self):
        return [p for k, p in self.ops if k == "update"]


class _CurAudit:
    def __init__(self, conn):
        self.conn = conn
        self._id = None
        # F3: `_audit_finish` exige `rowcount == 1` por UPDATE.
        self.rowcount = 1

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        if "INSERT INTO audit.source_sync_run" in s:
            if self.conn.falhar_no_insert and len(self.conn.inserts()) >= 1:
                raise RuntimeError("insert de auditoria falhou")
            self.conn.ops.append(("insert", params))
            self.conn._proximo_id += 1
            self._id = self.conn._proximo_id
        elif "UPDATE audit.source_sync_run" in s:
            if self.conn.falhar_no_update:
                raise RuntimeError("update de auditoria falhou")
            self.conn.ops.append(("update", params))

    def fetchone(self):
        return (self._id,)

    def close(self):
        pass


def _agora(ano=2026, mes=8, dia=28, hora=9):
    """UTC. 09:00 UTC = 06:00 BRT, o horario do `full_daily`."""
    return datetime(ano, mes, dia, hora, 0, tzinfo=timezone.utc)


# ===========================================================================
# MODO — decisao auto e full mensal duravel
# ===========================================================================

def test_auto_escolhe_full_sem_full_success_no_mes():
    cur = CurDecisao(ultimo_full=None)
    modo, d = sync.decide_effective_mode(cur, sync.MODE_AUTO, _agora())
    assert modo == sync.MODE_FULL
    assert d["full_mensal"] == "devido"
    assert d["mes_operacional"] == "2026-08"


def test_auto_escolhe_incremental_com_full_success_no_mes():
    cur = CurDecisao(ultimo_full=datetime(2026, 8, 3, 9, 5, tzinfo=timezone.utc))
    modo, d = sync.decide_effective_mode(cur, sync.MODE_AUTO, _agora())
    assert modo == sync.MODE_INCREMENTAL
    assert d["full_mensal"] == "atendido"


def test_a_consulta_da_decisao_exige_success_e_o_nome_do_full():
    """`failed` e `running` nao consomem a obrigacao — e a prova e' o SQL:
    ele filtra `status = 'success'` e o nome especifico do full."""
    cur = CurDecisao(ultimo_full=None)
    sync.decide_effective_mode(cur, sync.MODE_AUTO, _agora())
    sql, params = cur.sqls[0]
    assert "status = 'success'" in sql
    assert sync.FULL_AUDIT_SOURCE in params
    assert "started_at" not in sql          # nunca por inicio
    assert "error_message" not in sql       # nunca metadata em campo alheio
    assert "COUNT(" not in sql.upper()      # nunca por contagem de linhas


@pytest.mark.parametrize("estado", ["failed", "running"])
def test_full_nao_success_nao_consome_a_obrigacao(estado):
    """A consulta filtra por `success`; um `failed`/`running` simplesmente nao
    aparece no resultado, e o modo cai em full."""
    cur = CurDecisao(ultimo_full=None)   # o filtro ja excluiu o nao-success
    modo, d = sync.decide_effective_mode(cur, sync.MODE_AUTO, _agora())
    assert modo == sync.MODE_FULL
    assert d["full_mensal"] == "devido"


def test_sucesso_do_mes_anterior_nao_consome_a_obrigacao():
    """O SQL delimita o mes BRT corrente; um sucesso de julho fica fora."""
    cur = CurDecisao(ultimo_full=None)
    sync.decide_effective_mode(cur, sync.MODE_AUTO, _agora(mes=8))
    _, params = cur.sqls[0]
    assert date(2026, 8, 1) in params
    assert date(2026, 9, 1) in params      # limite superior exclusivo


def test_fronteira_do_mes_em_sao_paulo():
    """31/08 22h BRT ainda e' agosto, embora ja seja 01/09 em UTC."""
    instante = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)
    assert instante.astimezone(BRT).date() == date(2026, 8, 31)
    assert sync.operational_month(instante) == (2026, 8)
    cur = CurDecisao(ultimo_full=None)
    sync.decide_effective_mode(cur, sync.MODE_AUTO, instante)
    _, params = cur.sqls[0]
    assert date(2026, 8, 1) in params, "usou o mes de UTC em vez do de BRT"


def test_operational_month_recusa_datetime_ingenuo():
    with pytest.raises(ValueError):
        sync.operational_month(datetime(2026, 8, 28, 9, 0))


def test_modos_explicitos_nao_consultam_a_auditoria():
    for modo in (sync.MODE_FULL, sync.MODE_INCREMENTAL):
        cur = CurDecisao(ultimo_full=None)
        efetivo, d = sync.decide_effective_mode(cur, modo, _agora())
        assert efetivo == modo
        assert cur.sqls == [], "modo explicito nao pode consultar a auditoria"
        assert d["full_mensal"] == "nao avaliado"


def test_decisao_ocorre_depois_do_lock_e_antes_da_fonte():
    """Ordem ESTRUTURAL no corpo de `_run_apply`, nao por prosa.

    Duas execucoes concorrentes so' nao decidem em paralelo porque a decisao
    roda depois de `acquire_advisory_lock` e na mesma conexao que detem o lock.
    """
    src = inspect.getsource(sync._run_apply)
    i_lock = src.index("acquire_advisory_lock(neon)")
    i_dec = src.index("decide_effective_mode(")
    i_audit = src.index("_audit_start(")
    i_fonte = src.index("read_source_snapshot(")
    i_pub = src.index("publish_in_transaction(")
    assert i_lock < i_dec < i_audit < i_fonte < i_pub, (
        "ordem obrigatoria: lock -> decisao -> auditoria -> fonte -> publicacao"
    )


def test_decisao_usa_a_conexao_que_detem_o_lock():
    """Se usasse outra conexao, a decisao nao estaria protegida pelo lock."""
    src = inspect.getsource(sync._run_apply)
    trecho = src[src.index("acquire_advisory_lock(neon)"):
                 src.index("_audit_start(")]
    assert "neon.cursor()" in trecho
    assert "_neon_readonly" not in trecho
    assert "_neon_audit(" not in trecho.split("decide_effective_mode")[0]


def test_run_id_default_reflete_o_modo_efetivo(monkeypatch):
    """Rotular a execucao de "auto" seria falso: o modo efetivo so' existe
    depois da decisao sob o lock."""
    vistos = {}

    def fake_apply(mode, run_id):
        vistos["mode"] = mode
        vistos["run_id"] = run_id
        return {"mode": mode, "run_id": run_id, "applied": True}

    monkeypatch.setattr(sync, "_run_apply", fake_apply)
    sync.main(["--mode", "auto", "--apply"])
    assert vistos["run_id"] is None, (
        "com --mode auto --apply o run_id e' resolvido apos a decisao"
    )
    assert sync.default_run_id(sync.MODE_FULL).count(":full:") == 1
    assert sync.default_run_id(sync.MODE_INCREMENTAL).count(":incremental:") == 1


def test_run_id_explicito_e_preservado_apos_sanitizacao(monkeypatch):
    vistos = {}
    monkeypatch.setattr(sync, "run",
                        lambda mode, run_id, apply: vistos.update(run_id=run_id)
                        or {"mode": mode, "run_id": run_id, "applied": apply})
    sync.main(["--mode", "auto", "--apply", "--run-id", "piloto-ue2c"])
    assert vistos["run_id"] == sync.sanitize_run_id("piloto-ue2c")


# ===========================================================================
# AUDITORIA
# ===========================================================================

def test_incremental_cria_uma_linha_e_full_cria_duas():
    assert sync.audit_source_names(sync.MODE_INCREMENTAL) == (
        sync.CANONICAL_AUDIT_SOURCE,)
    assert sync.audit_source_names(sync.MODE_FULL) == (
        sync.CANONICAL_AUDIT_SOURCE, sync.FULL_AUDIT_SOURCE)


def test_as_duas_linhas_do_full_comecam_juntas_numa_transacao():
    conn = ConnAudit()
    ids = sync._audit_start(conn, sync.audit_source_names(sync.MODE_FULL))
    assert len(ids) == 2 and len(set(ids)) == 2
    assert len(conn.inserts()) == 2
    assert conn.commits == 1, "um unico commit: ou existem as duas, ou nenhuma"
    nomes = [p[0] for p in conn.inserts()]
    assert nomes == [sync.CANONICAL_AUDIT_SOURCE, sync.FULL_AUDIT_SOURCE]
    assert all(p[1] == 1 for p in conn.inserts()), "marketplace_id=1 (tiktok)"


def test_falha_ao_iniciar_faz_rollback_e_nao_deixa_linha_solta():
    conn = ConnAudit(falhar_no_insert=True)
    with pytest.raises(RuntimeError):
        sync._audit_start(conn, sync.audit_source_names(sync.MODE_FULL))
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_falha_ao_iniciar_aborta_antes_da_fonte():
    """`_audit_start` vem ANTES de `read_source_snapshot` no corpo — entao uma
    falha ali nunca chega a abrir a fotografia da fonte."""
    src = inspect.getsource(sync._run_apply)
    assert src.index("_audit_start(") < src.index("_datamart_snapshot(")


def test_as_duas_linhas_finalizam_juntas_com_o_mesmo_resultado():
    conn = ConnAudit()
    sync._audit_finish(conn, [1, 2], "success", extracted=10, loaded=5)
    assert len(conn.updates()) == 2
    assert conn.commits == 1
    assert {u[0] for u in conn.updates()} == {"success"}


def test_falha_do_sync_finaliza_failed_com_mensagem_sanitizada():
    conn = ConnAudit()
    bruta = 'connection to server at "10.0.3.44", port 5432 failed'
    sync._audit_finish(conn, [1], "failed",
                       error=sync.sanitize_error_message(RuntimeError(bruta)))
    (params,) = conn.updates()
    assert params[0] == "failed"
    texto = str(params)
    assert "10.0.3.44" not in texto
    assert "5432" not in texto


def test_watermark_inalterado_continua_sendo_success():
    """Fonte sem avanco NAO e' falha: prova que o job rodou e olhou."""
    conn = ConnAudit()
    sync._audit_finish(conn, [1], "success", extracted=0, loaded=0)
    assert conn.updates()[0][0] == "success"


def test_nada_publicado_nao_fabrica_data():
    class Snap:
        rows = []
    assert sync.published_ref_month_bounds(None, Snap()) == (None, None)
    assert sync.published_ref_month_bounds({"published": 0}, Snap()) == (None, None)


def test_ref_month_publicado_vira_min_max():
    class Snap:
        rows = [{"ref_month": date(2026, 6, 1)}, {"ref_month": date(2026, 8, 1)}]
    assert sync.published_ref_month_bounds({"published": 2}, Snap()) == (
        date(2026, 6, 1), date(2026, 8, 1))


def test_watermark_nao_vai_para_coluna_de_auditoria():
    """Nenhuma coluna com semantica falsa: `_audit_finish` nao aceita nem grava
    watermark."""
    assinatura = inspect.signature(sync._audit_finish)
    assert "watermark" not in assinatura.parameters
    corpo = inspect.getsource(sync._audit_finish)
    assert "last_successful_upper_bound" not in corpo


def test_diagnostico_nao_cria_linha_de_auditoria():
    """Prova estrutural: `_run_diagnostic` nao chama nada de auditoria."""
    src = inspect.getsource(sync._run_diagnostic)
    assert "_audit_start" not in src
    assert "_audit_finish" not in src
    assert "_neon_audit" not in src


def test_zero_retry_em_todo_o_modulo():
    """Codigo executavel, nao prosa: os docstrings do modulo dizem "nao repete"
    e conteriam varias destas palavras."""
    src = _codigo(sync)
    for proibido in ("time.sleep", "backoff", "max_retries", "retry("):
        assert proibido not in src, proibido


def test_falha_pos_commit_nao_finge_rollback():
    """Depois do commit da fact nao existe rollback honesto. O codigo diz isso
    e nao chama rollback na conexao da fact depois do commit."""
    src = inspect.getsource(sync._run_apply)
    depois = src[src.index("# 15 (UE2-C)"):]
    assert "neon.rollback()" not in depois
    assert "auditoria incompleta" in depois


# ===========================================================================
# PREFLIGHT / ORQUESTRACAO
# ===========================================================================

def test_fonte_registrada_no_preflight():
    assert FONTE in pf.SOURCE_CHECKS
    nomes = [f.__name__ for f in pf.SOURCE_CHECKS[FONTE]]
    assert "check_rds" in nomes and "check_neon" in nomes
    assert "check_affiliate_cost_relations" in nomes
    assert "check_affiliate_cost_source_not_empty" in nomes


def test_preflight_nao_faz_count_integral():
    """2,1 milhoes de linhas: um `COUNT(*)` aqui seria scan completo diario."""
    src = _codigo(pf.check_affiliate_cost_source_not_empty)
    assert "COUNT(" not in src.upper()
    assert src.count("LIMIT 1") == 2


def test_preflight_e_somente_leitura():
    for fn in (pf.check_affiliate_cost_relations,
               pf.check_affiliate_cost_source_not_empty):
        src = _codigo(fn)
        assert "readonly=True" in src
        # limite de palavra: `updated_at` contem "UPDATE" e produziria falso
        # positivo numa busca por substring.
        for escrita in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP"):
            assert not re.search(rf"{escrita}", src, re.I), (
                f"{fn.__name__} escreve: {escrita}")


def test_vpn_indisponivel_bloqueia_e_o_comando_nao_executa():
    chamados = []

    def executor(step):
        chamados.append(step.name)
        return 0

    def preflight_fn(source):
        if source == FONTE:
            return False, [pf.CheckResult("RDS", False, "VPN fora")]
        return True, []

    res = orch.run_pipeline("full_daily", executor=executor,
                            preflight_fn=preflight_fn)
    assert res[FONTE] == "BLOCKED"
    assert FONTE not in chamados, "comando nao pode executar sob BLOCKED"


def test_blocked_critico_derruba_o_pipeline_com_exit_1():
    res = {s.name: "SUCCESS" for s in orch.PIPELINES["full_daily"]}
    res[FONTE] = "BLOCKED"
    assert orch.compute_overall_status("full_daily", res) == "FAILED"
    passo = {s.name: s for s in orch.PIPELINES["full_daily"]}[FONTE]
    assert passo.critical is True


def test_step_configurado_conforme_o_contrato():
    passo = {s.name: s for s in orch.PIPELINES["full_daily"]}[FONTE]
    assert passo.module == "pipelines.sync_tiktok_affiliate_cost_order_monthly"
    assert passo.args == ("--mode", "auto", "--apply")
    assert passo.timeout_seconds == 300
    assert passo.preflight_source == FONTE
    assert passo.depends_on == ()
    assert passo.critical is True
    assert passo.always_run is False


def test_step_fica_antes_do_health_check_que_continua_ultimo():
    nomes = [s.name for s in orch.PIPELINES["full_daily"]]
    assert nomes[-1] == "health_check"
    assert nomes.index(FONTE) == len(nomes) - 2


def test_orcamento_e_margem():
    assert orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS == 7800
    assert sp.EXTERNAL_LOCK_TIMEOUT_SECONDS == 9000
    assert sp.TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS == 9600
    margem = sp.EXTERNAL_LOCK_TIMEOUT_SECONDS - orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS
    assert margem == 1200
    assert margem > 0.15 * orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS
    assert round(margem / orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS * 100, 2) == 15.38


def test_outros_pipelines_e_agendamento_intactos():
    assert orch.SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS == 3000
    assert orch.SHOPEE_MANUAL_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS == 3780
    assert [t.task_key for t in sp.PROPOSED_SCHEDULE] == ["full_daily"]
    assert len(sp.PROPOSED_SCHEDULE) == 1, "nenhuma TaskKey nova foi agendada"


def test_nenhum_pipeline_novo():
    assert sorted(orch.PIPELINES) == ["full_daily", "serving_refresh",
                                      "shopee_manual_refresh"]


# ===========================================================================
# FRESCOR
# ===========================================================================

def _fin(dia=28, hora=9):
    """`finished_at` aware; 09:00 UTC = 06:00 BRT."""
    return datetime(2026, 8, dia, hora, 0, tzinfo=timezone.utc)


def _wm(dia, hora=21, minuto=3):
    """Watermark NAIVE, como a coluna."""
    return datetime(2026, 8, dia, hora, minuto)


def test_execucao_recente_e_watermark_esperado_e_fresh():
    v = sync.classify_affiliate_freshness(_fin(28), _wm(27), _fin(28, 12))
    assert v["status"] == "fresh"
    assert v["late_batches"] == 0
    assert v["expected_batch_date"] == date(2026, 8, 27)


def test_execucao_antiga_e_stale():
    v = sync.classify_affiliate_freshness(_fin(25), _wm(24), _fin(28, 12))
    assert v["status"] == "stale"
    assert v["execution_recent"] is False


def test_execucao_recente_com_um_lote_atrasado_ja_e_stale():
    v = sync.classify_affiliate_freshness(_fin(28), _wm(26), _fin(28, 12))
    assert v["status"] == "stale"
    assert v["execution_recent"] is True
    assert v["watermark_current"] is False
    assert v["late_batches"] == 1
    assert v["escalate"] is False, "um lote nao escala o alerta"


def test_dois_lotes_atrasados_escalam_o_alerta():
    v = sync.classify_affiliate_freshness(_fin(28), _wm(25), _fin(28, 12))
    assert v["status"] == "stale"
    assert v["late_batches"] == 2
    assert v["escalate"] is True


def test_watermark_a_frente_e_fresh_com_atraso_zero():
    """Execucao manual pode ultrapassar o lote minimo: `max(0, ...)` impede
    atraso negativo."""
    v = sync.classify_affiliate_freshness(_fin(28), _wm(28), _fin(28, 12))
    assert v["status"] == "fresh"
    assert v["late_batches"] == 0


def test_sem_auditoria_ou_sem_watermark_e_unknown():
    assert sync.classify_affiliate_freshness(None, _wm(27), _fin(28))["status"] == "unknown"
    assert sync.classify_affiliate_freshness(_fin(28), None, _fin(28))["status"] == "unknown"


def test_virada_utc_brt_nao_muda_o_lote_esperado():
    """02:30 UTC de 29/08 e' 28/08 23:30 BRT: D_exec e' 28, nao 29."""
    fin = datetime(2026, 8, 29, 2, 30, tzinfo=timezone.utc)
    assert fin.astimezone(BRT).date() == date(2026, 8, 28)
    v = sync.classify_affiliate_freshness(fin, _wm(27), fin + timedelta(hours=1))
    assert v["expected_batch_date"] == date(2026, 8, 27)
    assert v["status"] == "fresh"


def test_watermark_naive_nunca_recebe_rotulo_brt():
    """A funcao nao pode converter o naive: so' a parte de data entra."""
    src = inspect.getsource(sync.classify_affiliate_freshness)
    trecho = src[src.index("watermark_date"):]
    assert "watermark.astimezone" not in src
    assert "watermark_date = watermark.date()" in src
    # e o resultado nao carrega fuso
    v = sync.classify_affiliate_freshness(_fin(28), _wm(27), _fin(28, 12))
    assert isinstance(v["watermark_date"], date)


def test_finished_at_ingenuo_e_recusado():
    with pytest.raises(ValueError):
        sync.classify_affiliate_freshness(datetime(2026, 8, 28, 9, 0),
                                          _wm(27), _fin(28, 12))


def test_health_check_nao_usa_ref_month_como_frescor_tecnico():
    src = _codigo(hc.fetch_affiliate_watermark_status)
    assert "ref_month" not in src, "frescor tecnico nao pode vir de ref_month"
    assert "last_successful_upper_bound" in src


def test_health_check_registra_a_fonte_canonica_e_nao_a_do_full():
    nomes = [e.source_name for e in hc.EXPECTED_SOURCES]
    assert sync.CANONICAL_AUDIT_SOURCE in nomes
    assert sync.FULL_AUDIT_SOURCE not in nomes, (
        "o marcador do full e' MENSAL; cobrar 30h dele reprovaria todo dia"
    )
    entrada = next(e for e in hc.EXPECTED_SOURCES
                   if e.source_name == sync.CANONICAL_AUDIT_SOURCE)
    assert entrada.exec_threshold_hours == 30
    assert entrada.critical is True


def test_contrato_de_frescor_e_o_mesmo_no_sync_e_na_api():
    """Duas arvores que nao se importam (a API nao importa `pipelines`), um so'
    contrato. Este teste compara as duas implementacoes sobre a MESMA tabela de
    casos — e' o que impede divergencia silenciosa."""
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.services import affiliate_costs_service as api_svc

    casos = [
        (_fin(28), _wm(27)), (_fin(25), _wm(24)), (_fin(28), _wm(26)),
        (_fin(28), _wm(25)), (_fin(28), _wm(28)), (None, _wm(27)),
        (_fin(28), None),
    ]
    agora = _fin(28, 12)
    for fin, wm in casos:
        a = sync.classify_affiliate_freshness(fin, wm, agora)
        b = api_svc.classify_freshness(fin, wm, agora)
        assert a["status"] == b["status"], (fin, wm)
        assert a["late_batches"] == b["late_batches"], (fin, wm)
        assert a["expected_batch_date"] == b["expected_batch_date"], (fin, wm)
    assert (sync.FRESHNESS_MAX_EXECUTION_AGE_HOURS
            == api_svc.FRESHNESS_MAX_EXECUTION_AGE_HOURS)


# ===========================================================================
# SEGURANCA
# ===========================================================================

def test_help_nao_abre_conexao(monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("--help nao pode abrir conexao")

    monkeypatch.setattr(sync.psycopg2, "connect", explode)
    with pytest.raises(SystemExit) as e:
        sync.main(["--help"])
    assert e.value.code == 0


def test_import_nao_abre_conexao():
    """O modulo ja esta importado no topo deste arquivo; se abrisse conexao no
    import, a coleta inteira teria falhado sem rede."""
    assert sync.CANONICAL_AUDIT_SOURCE
    src = inspect.getsource(sync)
    fora_de_funcao = [ln for ln in src.splitlines()
                      if ln.startswith("psycopg2.connect")]
    assert fora_de_funcao == []


def test_mensagens_de_erro_nao_vazam_topologia():
    bruta = RuntimeError(
        'connection to server at "10.0.3.44", port 5432 failed: FATAL: '
        'password authentication failed for user "svc"'
    )
    limpa = sync.sanitize_error_message(bruta)
    for proibido in ("10.0.3.44", "5432", "password", "svc"):
        assert proibido not in limpa


def test_auto_nao_publica_fresh_stale_na_api():
    """Task 2/3 implementa o contrato e NAO o publica."""
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.services import affiliate_costs_service as api_svc

    src = inspect.getsource(api_svc.build_affiliate_costs_block)
    assert '"freshness_status": "manual_snapshot"' in src
    assert "classify_freshness" not in src, (
        "a classificacao existe, mas NAO pode estar ligada ao payload publico"
    )


# ===========================================================================
# F1 — dado commitado NUNCA vira auditoria `failed`
# ===========================================================================

class ConnFactFake:
    """Conexao do destino: registra commit/rollback e pode falhar no commit."""

    def __init__(self, falhar_no_commit=False, falhar_antes_do_commit=False):
        self.autocommit = True
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.falhar_no_commit = falhar_no_commit
        self.falhar_antes_do_commit = falhar_antes_do_commit

    def cursor(self):
        return _CurFact(self)

    def commit(self):
        if self.falhar_no_commit:
            raise RuntimeError("queda de rede durante o commit")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _CurFact:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 1

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        if self.conn.falhar_antes_do_commit and "INSERT INTO marts." in s:
            raise RuntimeError("publicacao falhou antes do commit")

    def fetchone(self):
        return (None,)

    def close(self):
        pass


def _apply_com_estado(monkeypatch, *, falhar_no_commit=False,
                      falhar_antes_do_commit=False, audit_finish_falha=False,
                      source_empty=False):
    """Monta `_run_apply` com fakes minimos e devolve `(fn, audit, fact)`."""
    fact = ConnFactFake(falhar_no_commit=falhar_no_commit,
                        falhar_antes_do_commit=falhar_antes_do_commit)
    audit = ConnAudit()
    finalizacoes = []

    class Snap:
        rows = [] if source_empty else [{"ref_month": date(2026, 7, 1),
                                         "brand": "apice"}]
        boundary_a = {"linhas_lidas": 0 if source_empty else 10}
        detail_totals = {}
        cutoff = datetime(2026, 8, 28, 0, 3)

    Snap.source_empty = source_empty

    monkeypatch.setattr(sync, "_get_neon_url", lambda: "postgresql://x")
    monkeypatch.setattr(sync, "_get_datamart_url", lambda: "postgresql://x")
    monkeypatch.setattr(sync, "_neon_session", lambda url: fact)
    monkeypatch.setattr(sync, "_neon_audit", lambda url: audit)
    monkeypatch.setattr(sync, "acquire_advisory_lock", lambda c: None)
    monkeypatch.setattr(sync, "release_advisory_lock", lambda c: None)
    monkeypatch.setattr(sync, "assert_still_holding_lock", lambda c: None)
    monkeypatch.setattr(sync, "read_watermark", lambda c, **kw: None)
    monkeypatch.setattr(sync, "assert_watermark_unchanged", lambda c, w: None)
    monkeypatch.setattr(sync, "resolve_lower_bound", lambda m, w: None)
    monkeypatch.setattr(sync, "decide_effective_mode",
                        lambda cur, m, agora: (sync.MODE_FULL,
                                               {"effective_mode": "full"}))
    monkeypatch.setattr(sync, "_restore_autocommit", lambda c: None)

    class DM:
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr(sync, "_datamart_snapshot", lambda url: DM())
    monkeypatch.setattr(sync, "read_source_snapshot", lambda dm, lb: Snap())
    monkeypatch.setattr(sync, "validate_snapshot_in_memory",
                        lambda s, m: {"ok": True})
    monkeypatch.setattr(sync, "wipe_fact", lambda cur: {"apagadas": 0})
    def _publish(cur, s, m, r, w):
        if fact.falhar_antes_do_commit:
            raise RuntimeError("publicacao falhou antes do commit")
        return {"published": 1, "deleted": 0, "checks": {},
                "watermark": sync.WATERMARK_ADVANCED}

    monkeypatch.setattr(sync, "publish_in_transaction", _publish)

    original = sync._audit_finish

    def espiao(conn, ids, status, **kw):
        finalizacoes.append((status, kw))
        if audit_finish_falha and status == "success":
            raise RuntimeError("update de auditoria falhou")
        return original(conn, ids, status, **kw)

    monkeypatch.setattr(sync, "_audit_finish", espiao)
    return fact, audit, finalizacoes


def test_f1_falha_da_auditoria_pos_commit_nao_marca_failed(monkeypatch):
    """Fact commitada + `_audit_finish(success)` falha:

    nenhuma segunda finalizacao como `failed`, nenhum rollback posterior na
    fact, linhas permanecem `running`, e o erro sobe sanitizado.
    """
    fact, audit, fins = _apply_com_estado(monkeypatch, audit_finish_falha=True)

    with pytest.raises(sync.AuditoriaIncompleta) as e:
        sync._run_apply(sync.MODE_FULL, "run:1")

    assert fact.commits == 1, "a fact FOI publicada"
    assert fact.rollbacks == 0, "nenhum rollback depois do commit"
    assert [s for s, _ in fins] == ["success"], (
        "nao pode existir uma segunda finalizacao como `failed`"
    )
    assert audit.updates() == [], "as linhas permanecem `running`"
    texto = str(e.value)
    assert "dados publicados, auditoria incompleta" in texto
    for proibido in ("10.0.3.44", "5432", "password", "postgresql://"):
        assert proibido not in texto


def test_f1_cli_termina_com_falha_sanitizada_apos_commit(monkeypatch):
    _apply_com_estado(monkeypatch, audit_finish_falha=True)
    monkeypatch.setattr(sync, "run",
                        lambda mode, run_id, apply: sync._run_apply(mode, run_id))
    import io
    import contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        codigo = sync.main(["--mode", "full", "--apply"])
    assert codigo == 2
    saida = err.getvalue()
    assert "auditoria incompleta" in saida
    assert "postgresql://" not in saida


def test_f1_falha_antes_do_commit_continua_terminando_failed(monkeypatch):
    fact, audit, fins = _apply_com_estado(monkeypatch,
                                          falhar_antes_do_commit=True)
    with pytest.raises(RuntimeError):
        sync._run_apply(sync.MODE_FULL, "run:1")

    assert fact.commits == 0
    assert fact.rollbacks == 1, "rollback confirmado antes da publicacao"
    assert [s for s, _ in fins] == ["failed"]
    assert len(audit.updates()) == 2, "as duas linhas do full terminam failed"


def test_f1_commit_indeterminado_nao_declara_failed_nem_success(monkeypatch):
    """`commit()` levantou: ninguem sabe se o servidor efetivou.

    Nem `failed` (afirmaria que nada foi publicado) nem `success`. As linhas
    ficam em `running`, que e' o unico registro honesto de "nao se sabe".
    """
    fact, audit, fins = _apply_com_estado(monkeypatch, falhar_no_commit=True)
    with pytest.raises(RuntimeError):
        sync._run_apply(sync.MODE_FULL, "run:1")

    assert fact.commits == 0
    assert fact.rollbacks == 0, "rollback apos commit incerto nao esclarece nada"
    assert fins == [], "nenhuma finalizacao de auditoria foi tentada"
    assert audit.updates() == [], "linhas permanecem `running`"


def test_f1_pode_marcar_failed_so_prova_ausencia_de_publicacao():
    assert sync.pode_marcar_failed(sync.PUBLICACAO_NAO_TENTADA) is True
    assert sync.pode_marcar_failed(sync.PUBLICACAO_ROLLBACK_CONFIRMADO) is True
    assert sync.pode_marcar_failed(sync.PUBLICACAO_COMMIT_CONFIRMADO) is False
    assert sync.pode_marcar_failed(sync.PUBLICACAO_INDETERMINADA) is False


# ===========================================================================
# F3 — `_audit_finish` prova atualizacao integral
# ===========================================================================

class ConnRowcount:
    """Auditoria cujo UPDATE atinge N linhas, configuravel por posicao."""

    def __init__(self, rowcounts):
        self.rowcounts = list(rowcounts)
        self.commits = 0
        self.rollbacks = 0
        self.updates = 0

    def cursor(self):
        return _CurRowcount(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _CurRowcount:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.conn.updates += 1
        self.rowcount = self.conn.rowcounts.pop(0)

    def close(self):
        pass


def test_f3_update_que_nao_atinge_uma_linha_derruba_tudo():
    """Dois ids: o primeiro existe, o segundo nao. Zero commit, um rollback —
    a primeira atualizacao tambem nao sobrevive."""
    conn = ConnRowcount([1, 0])
    with pytest.raises(RuntimeError, match="rowcount"):
        sync._audit_finish(conn, [1, 2], "success")
    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert conn.updates == 2


def test_f3_rowcount_maior_que_um_tambem_derruba():
    conn = ConnRowcount([2])
    with pytest.raises(RuntimeError, match="rowcount"):
        sync._audit_finish(conn, [1], "success")
    assert conn.commits == 0 and conn.rollbacks == 1


def test_f3_caminho_feliz_comita_uma_vez():
    conn = ConnRowcount([1, 1])
    sync._audit_finish(conn, [1, 2], "success")
    assert conn.commits == 1 and conn.rollbacks == 0


def test_f3_status_final_invalido_e_recusado():
    conn = ConnRowcount([1])
    for ruim in ("running", "ok", "", "SUCCESS"):
        with pytest.raises(ValueError):
            sync._audit_finish(conn, [1], ruim)
    assert conn.updates == 0, "nem chega a tentar o UPDATE"


# ===========================================================================
# F4 — metricas honestas no full vazio
# ===========================================================================

def test_f4_full_vazio_registra_zero_carregado(monkeypatch):
    """Atravessa `_run_apply`, nao chama `_audit_finish` direto."""
    fact, audit, fins = _apply_com_estado(monkeypatch, source_empty=True)
    rel = sync._run_apply(sync.MODE_FULL, "run:1")

    assert fact.commits == 1
    (status, kw), = fins
    assert status == "success"
    assert kw["extracted"] == 0
    assert kw["loaded"] == 0, "zero medido, NUNCA NULL"
    assert kw["loaded"] is not None
    assert kw["min_ref_month"] is None
    assert kw["max_ref_month"] is None
    assert rel["publicacao_estado"] == sync.PUBLICACAO_COMMIT_CONFIRMADO


def test_f4_as_duas_linhas_recebem_os_mesmos_valores(monkeypatch):
    fact, audit, fins = _apply_com_estado(monkeypatch, source_empty=True)
    sync._run_apply(sync.MODE_FULL, "run:1")
    ups = audit.updates()
    assert len(ups) == 2
    # (status, extracted, loaded, min, max, error, id) — tudo igual menos o id
    assert ups[0][:-1] == ups[1][:-1]
    assert ups[0][2] == 0, "rows_loaded = 0 nas duas"


def test_f4_metricas_nao_fabricam_mes():
    class Snap:
        source_empty = True
        rows = [{"ref_month": date(2026, 7, 1)}]
        boundary_a = {"linhas_lidas": 999}
    assert sync._metricas_da_execucao({}, Snap()) == (0, 0, None, None)


# ===========================================================================
# F2 — health check fail-closed: erro de banco NUNCA vira `unknown`
# ===========================================================================

import psycopg2  # noqa: E402


class ConnHC:
    """Conexao do health check: watermark configuravel, ou erro no SELECT."""

    def __init__(self, ultimo_sucesso=None, watermark=None, linha_ausente=False,
                 erro=None):
        self.ultimo_sucesso = ultimo_sucesso
        self.watermark = watermark
        self.linha_ausente = linha_ausente
        self.erro = erro
        self.rollbacks = 0

    def cursor(self):
        return _CurHC(self)

    def rollback(self):
        self.rollbacks += 1


class _CurHC:
    def __init__(self, conn):
        self.conn = conn
        self._sql = ""

    def execute(self, sql, params=None):
        self._sql = " ".join(str(sql).split())
        if "last_successful_upper_bound" in self._sql and self.conn.erro:
            raise self.conn.erro

    def fetchone(self):
        if "MAX(finished_at)" in self._sql:
            return {"t": self.conn.ultimo_sucesso}
        if "last_successful_upper_bound" in self._sql:
            return None if self.conn.linha_ausente else {"w": self.conn.watermark}
        return {"n": 0}

    def close(self):
        pass


AGORA_HC = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def test_f2_execucao_registrada_com_watermark_ausente_fica_unknown():
    """Cenario FACTUAL: houve execucao bem-sucedida, mas nao ha linha de
    watermark. A dimensao do watermark fica `unknown` e **nao** acrescenta uma
    segunda reprovacao.

    Isto NAO e' o pre-piloto — la nao existe execucao nenhuma, e quem reprova e'
    a dimensao canonica de `EXPECTED_SOURCES`. Ver os testes integrados de
    `build_report` no fim deste arquivo.
    """
    conn = ConnHC(ultimo_sucesso=_fin(28), linha_ausente=True)
    v = hc.fetch_affiliate_watermark_status(conn, now=AGORA_HC)
    assert v.status == "unknown"
    assert v.stale is False, "unknown nao reprova NESTA dimensao"


@pytest.mark.parametrize("erro,rotulo", [
    (psycopg2.ProgrammingError('relation "..." does not exist'), "tabela ausente"),
    (psycopg2.errors.InsufficientPrivilege("permission denied for table"),
     "permissao revogada"),
    (psycopg2.OperationalError("server closed the connection"), "conexao abortada"),
])
def test_f2_erro_de_banco_vira_error_critico(erro, rotulo):
    """Erro tecnico NAO pode virar `unknown`: `unknown` e' nao-critico e
    significa "ainda nao ativado", o oposto de "quebrou"."""
    conn = ConnHC(ultimo_sucesso=_fin(28), erro=erro)
    v = hc.fetch_affiliate_watermark_status(conn, now=AGORA_HC)
    assert v.status == "error", rotulo
    assert v.stale is True, rotulo
    assert v.critical is True, rotulo
    assert conn.rollbacks == 1, "transacao restaurada antes de seguir"


def test_f2_erro_de_banco_nao_vaza_detalhe_sensivel():
    erro = psycopg2.OperationalError(
        'connection to server at "10.0.3.44", port 5432 failed: '
        'password authentication failed for user "svc"'
    )
    conn = ConnHC(ultimo_sucesso=_fin(28), erro=erro)
    v = hc.fetch_affiliate_watermark_status(conn, now=AGORA_HC)
    texto = str(v)
    for proibido in ("10.0.3.44", "5432", "password", "svc",
                     "SELECT", "last_successful_upper_bound"):
        assert proibido not in texto, proibido


def test_f2_bug_python_generico_propaga():
    """Esconder defeito nosso atras de "erro de fonte" e' exatamente o que nao
    se pode fazer."""
    conn = ConnHC(ultimo_sucesso=_fin(28), erro=KeyError("bug de codigo"))
    with pytest.raises(KeyError):
        hc.fetch_affiliate_watermark_status(conn, now=AGORA_HC)


def test_f2_estado_error_derruba_ok_critical():
    conn = ConnHC(ultimo_sucesso=_fin(28),
                  erro=psycopg2.ProgrammingError("nao existe"))
    v = hc.fetch_affiliate_watermark_status(conn, now=AGORA_HC)
    assert v.stale and v.critical
    # e' isso que `build_report` cruza para calcular `ok_critical`
    assert (v.stale and v.critical) is True


def test_f2_watermark_presente_classifica_normalmente():
    conn = ConnHC(ultimo_sucesso=_fin(28), watermark=_wm(27))
    v = hc.fetch_affiliate_watermark_status(conn, now=AGORA_HC)
    assert v.status == "fresh"
    assert v.late_batches == 0
    assert v.stale is False


# ===========================================================================
# H1 — contrato de frescor: igualdade COMPLETA entre as duas implementacoes
# ===========================================================================

def test_h1_as_duas_implementacoes_devolvem_dicionarios_identicos():
    """Comparacao do dicionario INTEIRO, nao de tres campos.

    Duas arvores que nao se importam, um so' contrato: se qualquer chave
    divergir — motivo, escalate, execution_age_hours — este teste falha.
    """
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.services import affiliate_costs_service as api_svc

    agora = _fin(28, 12)
    casos = [
        ("fresh", _fin(28), _wm(27)),
        ("execucao antiga", _fin(25), _wm(24)),
        ("um lote atras", _fin(28), _wm(26)),
        ("dois lotes atras", _fin(28), _wm(25)),
        ("watermark a frente", _fin(28), _wm(28)),
        ("sem auditoria", None, _wm(27)),
        ("sem watermark", _fin(28), None),
        ("virada UTC/BRT", datetime(2026, 8, 29, 2, 30, tzinfo=timezone.utc),
         _wm(27)),
        ("limite exato de 30h", _fin(27, 6), _wm(26)),
    ]
    for rotulo, fin, wm in casos:
        a = sync.classify_affiliate_freshness(fin, wm, agora)
        b = api_svc.classify_freshness(fin, wm, agora)
        assert a == b, f"{rotulo}: {a} != {b}"
        assert set(a) == set(b), rotulo


def test_h1_agora_ingenuo_e_invalido_nas_duas():
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.services import affiliate_costs_service as api_svc

    ingenuo = datetime(2026, 8, 28, 12, 0)
    with pytest.raises(ValueError):
        sync.classify_affiliate_freshness(_fin(28), _wm(27), ingenuo)
    with pytest.raises(ValueError):
        api_svc.classify_freshness(_fin(28), _wm(27), ingenuo)


def test_h1_constantes_do_contrato_sao_iguais():
    sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
    from app.services import affiliate_costs_service as api_svc

    assert (sync.FRESHNESS_MAX_EXECUTION_AGE_HOURS
            == api_svc.FRESHNESS_MAX_EXECUTION_AGE_HOURS == 30)
    assert (sync.FRESHNESS_ESCALATION_LATE_BATCHES
            == api_svc.FRESHNESS_ESCALATION_LATE_BATCHES == 2)


# ===========================================================================
# H2 — tipos
# ===========================================================================

def test_h2_run_id_aceita_none_nas_assinaturas():
    for fn in (sync.run, sync._run_apply, sync._run_diagnostic):
        anot = inspect.signature(fn).parameters["run_id"].annotation
        assert anot == "str | None", f"{fn.__name__}: {anot}"


# ===========================================================================
# Duas dimensoes ORTOGONAIS — integrado por `build_report`
#
# A. execucao   -> `EXPECTED_SOURCES`, decide `ok_critical`
# B. watermark  -> `affiliate_watermark`, nao adiciona 2a reprovacao nem
#                  neutraliza a primeira
# ===========================================================================

from datetime import timedelta as _td  # noqa: E402

import pipelines.tests.test_ops_health_check as hct  # noqa: E402

CANONICA = sync.CANONICAL_AUDIT_SOURCE


def _conn(sem_execucao_afiliados=False, **kw):
    """Base saudavel do harness do health check, com afiliados configuravel."""
    conn = hct.all_fresh_conn(**kw)
    if sem_execucao_afiliados:
        conn.last_run.pop(CANONICA, None)
        conn.last_success.pop(CANONICA, None)
    return conn


def _fonte(report, nome):
    return next(s for s in report["sources"] if s["source_name"] == nome)


def test_pre_piloto_real_watermark_unknown_mas_execucao_reprova():
    """O pre-piloto DE VERDADE: nenhuma auditoria e nenhum watermark.

    As duas dimensoes nao se contradizem — elas dizem coisas diferentes:
    o watermark nao tem o que avaliar (`unknown`, sem reprovar), e a execucao
    nunca aconteceu (reprova). Uma rotina ja declarada critica NAO pode deixar
    o health check verde antes da primeira execucao comprovada.
    """
    conn = _conn(sem_execucao_afiliados=True, affiliate_watermark=None)
    report = hc.build_report(conn, now=hct.NOW)

    aw = report["affiliate_watermark"]
    assert aw["status"] == "unknown"
    assert aw["stale"] is False, "a dimensao do watermark nao reprova sozinha"

    canonica = _fonte(report, CANONICA)
    assert canonica["stale"] is True, "execucao ausente reprova"
    assert canonica["critical"] is True

    assert report["ok_critical"] is False, (
        "unknown no watermark nao pode neutralizar a execucao ausente"
    )


def test_execucao_recente_com_watermark_ausente_nao_gera_stale_extra():
    """Contraprova: execucao saudavel + watermark ausente.

    A dimensao de execucao esta em dia; o watermark fica `unknown`; e nenhuma
    reprovacao NOVA nasce do watermark.
    """
    conn = _conn(affiliate_watermark=None)
    report = hc.build_report(conn, now=hct.NOW)

    aw = report["affiliate_watermark"]
    assert aw["status"] == "unknown"
    assert aw["stale"] is False

    assert _fonte(report, CANONICA)["stale"] is False
    assert report["ok_critical"] is True, (
        "sem execucao ausente nem erro, nada deveria reprovar"
    )


def test_sucesso_recente_com_watermark_atual_deixa_as_duas_saudaveis():
    conn = _conn()      # default do harness: watermark do lote de ontem
    report = hc.build_report(conn, now=hct.NOW)
    aw = report["affiliate_watermark"]
    assert aw["status"] == "fresh"
    assert aw["stale"] is False and aw["late_batches"] == 0
    assert _fonte(report, CANONICA)["stale"] is False
    assert report["ok_critical"] is True


def test_ultima_execucao_failed_com_sucesso_anterior_recente_reprova():
    """`last_run_failed` reprova mesmo com um sucesso recente no historico —
    esconder uma falha porque "ontem deu certo" seria o oposto de observar."""
    conn = _conn()
    conn.last_run[CANONICA] = {
        "started_at": hct.NOW - _td(hours=2),
        "finished_at": hct.NOW - _td(hours=2),
        "status": "failed", "error_message": "falhou",
    }
    conn.last_success[CANONICA] = hct.NOW - _td(hours=26)   # dentro das 30h
    report = hc.build_report(conn, now=hct.NOW)
    assert _fonte(report, CANONICA)["stale"] is True
    assert report["ok_critical"] is False


def test_erro_de_banco_no_watermark_derruba_ok_critical_integrado():
    """Aqui SIM o watermark reprova: erro tecnico e' `error`, nao `unknown`."""
    conn = _conn()
    conn.erro_watermark = psycopg2.ProgrammingError("relation does not exist")

    original = hct.FakeCursor.execute

    def execute(self, sql, params=None):
        if "last_successful_upper_bound" in " ".join(str(sql).split()):
            raise conn.erro_watermark
        return original(self, sql, params)

    import unittest.mock as mock
    with mock.patch.object(hct.FakeCursor, "execute", execute):
        report = hc.build_report(conn, now=hct.NOW)

    aw = report["affiliate_watermark"]
    assert aw["status"] == "error"
    assert aw["stale"] is True and aw["critical"] is True
    assert report["ok_critical"] is False
    texto = str(report)
    for proibido in ("relation does not exist", "SELECT",
                     "last_successful_upper_bound"):
        assert proibido not in texto


def test_excecao_nao_banco_propaga_pelo_build_report():
    conn = _conn()

    original = hct.FakeCursor.execute

    def execute(self, sql, params=None):
        if "last_successful_upper_bound" in " ".join(str(sql).split()):
            raise KeyError("bug de codigo")
        return original(self, sql, params)

    import unittest.mock as mock
    with mock.patch.object(hct.FakeCursor, "execute", execute):
        with pytest.raises(KeyError):
            hc.build_report(conn, now=hct.NOW)


def test_as_duas_dimensoes_sao_independentes_no_relatorio():
    """Prova estrutural do relatorio: `sources` e `affiliate_watermark` sao
    chaves distintas, e nenhuma sobrescreve a outra."""
    report = hc.build_report(_conn(sem_execucao_afiliados=True,
                                   affiliate_watermark=None), now=hct.NOW)
    assert "affiliate_watermark" in report
    assert any(s["source_name"] == CANONICA for s in report["sources"])
    # a fonte canonica NAO some de `sources` so' porque o watermark e' unknown
    assert _fonte(report, CANONICA)["reason"]
