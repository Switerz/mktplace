"""
Testes de pipelines/ops/serving_refresh.py — a ponte temporaria de atualizacao
recorrente do serving (Checkpoint O1, Task 2/2).

Tudo aqui e' comportamental: `conn_factory` e `runner` sao injetados, entao
NENHUM banco e' tocado e NENHUM processo e' criado. Os poucos testes estaticos
existem so' para wiring que nao da' para exercitar (ausencia de `shell=True`,
ausencia de dependencia nova) — nunca no lugar de um comportamento observavel.

O contrato sob teste:

    effective_date_to = min(D-1 em America/Sao_Paulo, source_max)
    date_from         = max(source_min, effective_date_to - (lookback_days - 1))

por tabela, independentemente. As tres propriedades que mais importam, porque
sao as que quebrariam em producao sem ninguem notar:

  - creator em D-2 nunca rebaixa ML nem brand;
  - D0 e' inalcancavel, mesmo quando a Gold ja tem D0 (situacao real do ML);
  - dia ausente permanece ausente — este modulo nunca escreve nada.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

import pipelines.ops.serving_refresh as sr
import pipelines.sync_ml_gestao_diaria as ml_sync
import pipelines.sync_tiktok_serving as tk_sync

MODULE_PATH = Path(sr.__file__)

#: Dia operacional congelado em todos os testes de janela. 17/08/2026 e' o dia
#: real do diagnostico do Checkpoint O1, com watermarks heterogeneos medidos:
#: ML em D0 (17/08), brand em D-1 (16/08), creator em D-2 (15/08).
HOJE = date(2026, 8, 17)
D0 = date(2026, 8, 17)
D1 = date(2026, 8, 16)
D2 = date(2026, 8, 15)


# ---------------------------------------------------------------------------
# Fakes: nenhuma conexao, nenhum processo
# ---------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, source_max, log):
        self._source_max = source_max
        self._log = log
        self._last_was_query = False

    def execute(self, sql, params=None):
        self._log.append((sql, params))
        self._last_was_query = "MAX(" in sql

    def fetchone(self):
        if not self._last_was_query:
            return None
        return (self._source_max,)

    def close(self):
        self._log.append(("__cursor_closed__", None))


class FakeConn:
    """Conexao de leitura fake. Registra tudo que foi executado para que os
    testes possam afirmar que nada de escrita passou por aqui."""

    def __init__(self, source_max):
        self.source_max = source_max
        self.executed = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self.source_max, self.executed)

    def close(self):
        self.closed = True


class RunnerSpy:
    """Substitui o subprocesso. Conta invocacoes — e' o que prova 'exatamente
    uma tentativa, zero retry'."""

    def __init__(self, exit_code=0, raise_timeout=False):
        self.exit_code = exit_code
        self.raise_timeout = raise_timeout
        self.calls = []

    def __call__(self, argv, timeout_seconds):
        self.calls.append({"argv": list(argv), "timeout": timeout_seconds})
        if self.raise_timeout:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout_seconds)
        return self.exit_code

    @property
    def count(self):
        return len(self.calls)

    @property
    def argv(self):
        assert self.count == 1, f"esperava 1 invocacao, houve {self.count}"
        return self.calls[0]["argv"]


def run(target, source_max, *, lookback_days=sr.DEFAULT_LOOKBACK_DAYS, apply=False,
        today=HOJE, exit_code=0, raise_timeout=False, run_id=None):
    """Executa run_target com tudo injetado. Devolve (codigo, runner, conn)."""
    conn = FakeConn(source_max)
    runner = RunnerSpy(exit_code=exit_code, raise_timeout=raise_timeout)
    codigo = sr.run_target(
        target, lookback_days=lookback_days, apply=apply, run_id=run_id,
        today=today, conn_factory=lambda: conn, runner=runner,
    )
    return codigo, runner, conn


def argv_value(argv, flag):
    return argv[argv.index(flag) + 1]


def code_only(path: Path) -> str:
    """Codigo sem docstrings nem comentarios, via AST.

    Necessario porque este repositorio DOCUMENTA as proibicoes nos proprios
    comentarios (ex.: a frase "nunca `shell=True`"). Um grep ingenuo no texto
    bruto casaria com a documentacao da regra e nao com uma violacao dela.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            corpo = node.body
            if (corpo and isinstance(corpo[0], ast.Expr)
                    and isinstance(corpo[0].value, ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                corpo.pop(0)
    return ast.unparse(tree)


# ===========================================================================
# 1. D0 presente na Gold -> effective_date_to = D-1
# ===========================================================================

def test_1_source_max_em_d0_ainda_resulta_em_effective_date_to_d1():
    """Situacao REAL medida em 17/08/2026: gold.ml_gestao_diaria tem 17/08 (D0).
    O teto continua sendo D-1 — publicar D0 significaria servir um dia ainda em
    curso como se estivesse fechado."""
    _, runner, _ = run("ml", source_max=D0)
    assert argv_value(runner.argv, "--date-to") == D1.isoformat()


def test_1_effective_date_to_e_o_minimo_entre_d1_e_source_max():
    tg = sr.TARGETS["ml"]
    assert sr.resolve_effective_date_to(tg, D0, HOJE) == D1   # fonte adiante
    assert sr.resolve_effective_date_to(tg, D1, HOJE) == D1   # fonte em D-1
    assert sr.resolve_effective_date_to(tg, D2, HOJE) == D2   # fonte atrasada


# ===========================================================================
# 2. brand em D-1 e creator em D-2, sem rebaixamento cruzado
# ===========================================================================

def test_2_brand_termina_em_d1_e_creator_em_d2_na_mesma_execucao():
    _, r_brand, _ = run("brand", source_max=D1)
    _, r_creator, _ = run("creator", source_max=D2)

    assert argv_value(r_brand.argv, "--date-to") == D1.isoformat()
    assert argv_value(r_creator.argv, "--date-to") == D2.isoformat()


def test_2_creator_atrasado_nao_rebaixa_brand():
    """A regra explicitamente descartada era reduzir todas as tabelas ao menor
    watermark comum. Se isso voltasse, brand terminaria em 15/08 de graca."""
    _, r_brand, _ = run("brand", source_max=D1)
    _, r_creator, _ = run("creator", source_max=D2)

    assert argv_value(r_brand.argv, "--date-to") != argv_value(r_creator.argv, "--date-to")
    assert argv_value(r_brand.argv, "--date-to") == D1.isoformat()


def test_2_brand_atrasado_nao_rebaixa_creator():
    """Simetria: o inverso tambem nao pode acontecer."""
    _, r_brand, _ = run("brand", source_max=D2)
    _, r_creator, _ = run("creator", source_max=D1)

    assert argv_value(r_brand.argv, "--date-to") == D2.isoformat()
    assert argv_value(r_creator.argv, "--date-to") == D1.isoformat()


# ===========================================================================
# 3. ML e TikTok independentes
# ===========================================================================

def test_3_ml_e_tiktok_tem_watermarks_totalmente_independentes():
    _, r_ml, _ = run("ml", source_max=D0)
    _, r_brand, _ = run("brand", source_max=D2)

    assert argv_value(r_ml.argv, "--date-to") == D1.isoformat()
    assert argv_value(r_brand.argv, "--date-to") == D2.isoformat()


def test_3_cada_target_le_a_propria_fonte():
    relacoes = {t: sr.TARGETS[t].source_relation for t in sr.TARGET_ORDER}
    assert relacoes == {
        "ml": "gold.ml_gestao_diaria",
        "brand": "gold.tiktok_brand_daily",
        "creator": "gold.tiktok_creator_daily",
    }
    assert len(set(relacoes.values())) == 3


def test_3_cada_target_escreve_em_uma_tabela_diferente():
    """Advisory locks distintos por tabela: nenhum target bloqueia outro."""
    chaves = {sr.TARGETS[t].advisory_lock_key for t in sr.TARGET_ORDER}
    assert chaves == {906_120_006, 907_120_007, 908_120_008}


# ===========================================================================
# 4. Janela de 90 datas INCLUSIVAS
# ===========================================================================

@pytest.mark.parametrize("target", ["ml", "brand", "creator"])
def test_4_janela_default_tem_exatamente_90_datas_inclusivas(target):
    _, runner, _ = run(target, source_max=D1)
    dfrom = date.fromisoformat(argv_value(runner.argv, "--date-from"))
    dto = date.fromisoformat(argv_value(runner.argv, "--date-to"))
    assert (dto - dfrom).days + 1 == 90


def test_4_date_from_e_date_to_menos_89_dias():
    tg = sr.TARGETS["brand"]
    dfrom, dto = sr.resolve_window(tg, D1, 90, HOJE)
    assert dto == D1
    assert (dto - dfrom).days == 89


def test_4_janela_nunca_comeca_antes_do_primeiro_dado_da_fonte():
    """Lookback maior que o historico e' truncado em source_min, nunca pede
    data que a fonte nao pode ter."""
    tg = sr.TARGETS["creator"]
    dfrom, dto = sr.resolve_window(tg, D1, 5000, HOJE)
    assert dfrom == tg.source_min_date


# ===========================================================================
# 5 e 6. Lookback minimo e rejeicao ANTES de qualquer subprocesso
# ===========================================================================

def test_5_lookback_minimo_de_7_e_aceito():
    _, runner, _ = run("ml", source_max=D1, lookback_days=7)
    dfrom = date.fromisoformat(argv_value(runner.argv, "--date-from"))
    dto = date.fromisoformat(argv_value(runner.argv, "--date-to"))
    assert (dto - dfrom).days + 1 == 7


def test_5_min_lookback_vem_do_modulo_validado_nao_de_um_numero_local():
    assert sr.MIN_LOOKBACK_DAYS == tk_sync.MIN_LOOKBACK_DAYS == ml_sync.MIN_LOOKBACK_DAYS == 7
    assert sr.DEFAULT_LOOKBACK_DAYS == tk_sync.DEFAULT_LOOKBACK_DAYS == ml_sync.DEFAULT_LOOKBACK_DAYS == 90


@pytest.mark.parametrize("invalido", [6, 1, 0, -1, -90])
def test_6_lookback_invalido_e_rejeitado_sem_conexao_e_sem_subprocesso(invalido):
    """Falha antes de tocar em qualquer recurso: nem a fonte e' consultada."""
    conn = FakeConn(D1)
    runner = RunnerSpy()
    with pytest.raises(ValueError, match=r"lookback_days precisa ser >= 7"):
        sr.run_target("ml", lookback_days=invalido, apply=True, today=HOJE,
                      conn_factory=lambda: conn, runner=runner)
    assert runner.count == 0, "subprocesso foi criado apesar do lookback invalido"
    assert conn.executed == [], "a fonte foi consultada apesar do lookback invalido"


def test_6_lookback_nao_inteiro_e_rejeitado():
    with pytest.raises(ValueError, match="inteiro"):
        sr.require_lookback(90.5)
    with pytest.raises(ValueError, match="inteiro"):
        sr.require_lookback(True)


def test_6_cli_rejeita_lookback_invalido_com_exit_nao_zero(monkeypatch, capsys):
    """No CLI, lookback invalido termina em exit != 0 e mensagem sanitizada,
    sem subprocesso."""
    runner = RunnerSpy()
    monkeypatch.setattr(sr, "_default_runner", runner)
    monkeypatch.setattr(sr, "_datamart_readonly", lambda: FakeConn(D1))
    codigo = sr.main(["--target", "ml", "--lookback-days", "3", "--apply"])
    assert codigo == sr.EXIT_WRAPPER_FAILURE
    assert runner.count == 0
    assert "FALHA (ml)" in capsys.readouterr().err


# ===========================================================================
# 7 e 8. source_max ausente ou inconsistente falha ANTES da escrita
# ===========================================================================

def test_7_source_max_none_falha_sem_criar_subprocesso():
    conn = FakeConn(None)
    runner = RunnerSpy()
    with pytest.raises(ValueError, match="veio vazio"):
        sr.run_target("creator", apply=True, today=HOJE,
                      conn_factory=lambda: conn, runner=runner)
    assert runner.count == 0, "subprocesso criado com source_max ausente"


def test_7_source_max_none_no_cli_vira_exit_nao_zero(monkeypatch, capsys):
    monkeypatch.setattr(sr, "_datamart_readonly", lambda: FakeConn(None))
    runner = RunnerSpy()
    monkeypatch.setattr(sr, "_default_runner", runner)
    codigo = sr.main(["--target", "brand", "--apply"])
    assert codigo == sr.EXIT_WRAPPER_FAILURE
    assert runner.count == 0
    assert "FALHA (brand)" in capsys.readouterr().err


def test_8_source_max_anterior_ao_source_min_falha_sem_subprocesso():
    tg = sr.TARGETS["creator"]
    antes = date(2025, 1, 1)
    assert antes < tg.source_min_date
    conn = FakeConn(antes)
    runner = RunnerSpy()
    with pytest.raises(ValueError, match="anterior ao primeiro dado"):
        sr.run_target("creator", apply=True, today=HOJE,
                      conn_factory=lambda: conn, runner=runner)
    assert runner.count == 0


def test_8_source_max_de_tipo_errado_falha():
    tg = sr.TARGETS["ml"]
    with pytest.raises(ValueError, match="nao e' uma data"):
        sr.resolve_effective_date_to(tg, datetime(2026, 8, 16, 10, 0), HOJE)
    with pytest.raises(ValueError, match="nao e' uma data"):
        sr.resolve_effective_date_to(tg, "2026-08-16", HOJE)


# ===========================================================================
# 9. NUNCA publica D0
# ===========================================================================

@pytest.mark.parametrize("target", ["ml", "brand", "creator"])
@pytest.mark.parametrize("source_max", [D0, D1, D2, date(2026, 12, 31)])
def test_9_date_to_nunca_alcanca_o_dia_de_hoje(target, source_max):
    """Inclui source_max no FUTURO: mesmo uma fonte com data adiantada nao
    consegue arrastar a janela para D0 ou depois."""
    _, runner, _ = run(target, source_max=source_max)
    dto = date.fromisoformat(argv_value(runner.argv, "--date-to"))
    assert dto < HOJE
    assert dto <= D1


def test_9_o_cli_interno_tambem_rejeitaria_d0_segunda_barreira():
    """Defesa em profundidade: mesmo se o wrapper errasse o teto, os dois CLIs
    validados rejeitam `date_to == today` por conta propria."""
    with pytest.raises(ValueError):
        ml_sync.validate_window(D2, HOJE, HOJE)
    with pytest.raises(ValueError):
        tk_sync.validate_window(tk_sync.BRAND_SPEC, D2, HOJE, HOJE)


def test_9_ultimo_dia_fechado_usa_o_fuso_do_brasil():
    """As 00:05 UTC de 18/08 o dia no Brasil ainda e' 17/08, logo D-1 = 16/08.
    Sem fuso, um servidor UTC viraria o dia as 21:00 locais."""
    from datetime import timezone
    agora = datetime(2026, 8, 18, 0, 5, tzinfo=timezone.utc)
    assert sr.hoje_operacional(agora) == date(2026, 8, 17)
    assert sr.ultimo_dia_fechado(sr.hoje_operacional(agora)) == date(2026, 8, 16)


def test_9_nenhum_date_today_sem_fuso_no_codigo():
    codigo = code_only(MODULE_PATH)
    assert "date.today(" not in codigo
    assert "datetime.today(" not in codigo
    assert "utcnow(" not in codigo


# ===========================================================================
# 10. Ausencia de creator NAO vira zero
# ===========================================================================

def test_10_dia_ausente_fica_fora_da_janela_em_vez_de_virar_zero():
    """Creator em D-2: a janela termina em D-2. O dia D-1 simplesmente nao e'
    pedido — nao existe caminho que o transforme em linha de zeros."""
    _, runner, _ = run("creator", source_max=D2)
    dto = date.fromisoformat(argv_value(runner.argv, "--date-to"))
    assert dto == D2
    assert dto != D1


def test_10_o_wrapper_nunca_executa_escrita():
    """Nenhum verbo de escrita no codigo: o modulo le `MAX(...)` e delega. A
    unica coisa que escreve no banco e' a transacao do CLI."""
    codigo = code_only(MODULE_PATH).upper()
    for verbo in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE", "CREATE TABLE",
                  "DROP ", "ALTER ", "COPY ", "MERGE "):
        assert verbo not in codigo, f"verbo de escrita {verbo!r} presente no wrapper"


def test_10_a_conexao_da_fonte_e_somente_leitura_e_e_fechada():
    _, _, conn = run("creator", source_max=D2)
    sqls = [s for s, _ in conn.executed if s != "__cursor_closed__"]
    assert any("MAX(" in s for s in sqls)
    assert all(s.startswith(("SELECT", "SET statement_timeout")) for s in sqls), sqls
    assert conn.closed is True


def test_10_nenhuma_linha_e_lida_da_fonte_apenas_o_agregado():
    """`source_max` e' um agregado: nao traz linha, nao traz PII, e nao faz um
    scan caro so' para um preflight."""
    for t in sr.TARGET_ORDER:
        sql = sr.build_source_max_query(sr.TARGETS[t])
        assert sql.startswith("SELECT MAX(")
        assert "JOIN" not in sql.upper()
        assert "*" not in sql


# ===========================================================================
# 11 e 12. Default diagnostico; --apply encaminhado exatamente uma vez
# ===========================================================================

@pytest.mark.parametrize("target", ["ml", "brand", "creator"])
def test_11_default_nao_encaminha_apply(target):
    _, runner, _ = run(target, source_max=D1, apply=False)
    assert "--apply" not in runner.argv


def test_11_cli_sem_apply_anuncia_modo_diagnostico(monkeypatch, capsys):
    monkeypatch.setattr(sr, "_datamart_readonly", lambda: FakeConn(D1))
    runner = RunnerSpy()
    monkeypatch.setattr(sr, "_default_runner", runner)
    sr.main(["--target", "ml"])
    saida = capsys.readouterr().out
    assert "MODO DIAGNOSTICO" in saida
    assert "--apply" not in runner.argv


@pytest.mark.parametrize("target", ["ml", "brand", "creator"])
def test_12_apply_e_encaminhado_exatamente_uma_vez(target):
    _, runner, _ = run(target, source_max=D1, apply=True)
    assert runner.argv.count("--apply") == 1


def test_12_backfill_nunca_e_encaminhado():
    """`--backfill` reprocessaria todo o historico a cada execucao diaria."""
    for t in sr.TARGET_ORDER:
        _, runner, _ = run(t, source_max=D1, apply=True)
        assert "--backfill" not in runner.argv


def test_12_lookback_days_nao_e_encaminhado_ao_cli_interno():
    """O CLI ignora --lookback-days quando ha datas explicitas; encaminhar daria
    a impressao falsa de que ele recalcularia a janela."""
    _, runner, _ = run("ml", source_max=D1, apply=True, lookback_days=30)
    assert "--lookback-days" not in runner.argv


# ===========================================================================
# 13 e 14. Um subprocesso por target, zero retry, shell=False
# ===========================================================================

@pytest.mark.parametrize("exit_code", [0, 1, 2, 124])
def test_13_exatamente_uma_invocacao_qualquer_que_seja_o_exit_code(exit_code):
    codigo, runner, _ = run("brand", source_max=D1, apply=True, exit_code=exit_code)
    assert runner.count == 1, "houve retry"
    assert codigo == exit_code, "exit code do filho nao foi propagado sem traducao"


def test_13_timeout_do_filho_nao_gera_nova_tentativa():
    codigo, runner, _ = run("creator", source_max=D2, apply=True, raise_timeout=True)
    assert runner.count == 1
    assert codigo == sr.EXIT_TIMEOUT == 124


def test_13_nenhum_laco_de_retry_no_codigo():
    codigo = code_only(MODULE_PATH).lower()
    for termo in ("retry", "backoff", "tenacity", "max_attempts", "time.sleep", "sleep("):
        assert termo not in codigo, f"indicio de retry no codigo: {termo!r}"


def test_13_child_timeout_e_menor_que_o_timeout_do_step():
    """O filho tem que morrer ANTES do orquestrador matar o wrapper — senao o
    CLI ficaria orfao escrevendo no Neon depois que o step foi encerrado."""
    for t in sr.TARGET_ORDER:
        tg = sr.TARGETS[t]
        assert tg.child_timeout_seconds < tg.step_timeout_seconds, t
        assert tg.step_timeout_seconds - tg.child_timeout_seconds >= 30, t


def test_13_o_timeout_passado_ao_runner_e_o_do_target():
    for t in sr.TARGET_ORDER:
        _, runner, _ = run(t, source_max=D2, apply=True)
        assert runner.calls[0]["timeout"] == sr.TARGETS[t].child_timeout_seconds


def test_14_argv_e_lista_e_nunca_string_de_shell():
    _, runner, _ = run("ml", source_max=D1, apply=True)
    argv = runner.argv
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    assert argv[0] == sys.executable
    assert argv[1] == "-m"
    # nenhum metacaractere de shell concatenado em um unico argumento
    assert not any(c in a for a in argv for c in ("|", "&&", ";", ">", "<", "`"))


def test_14_shell_true_nao_existe_no_codigo():
    codigo = code_only(MODULE_PATH)
    assert "shell=True" not in codigo
    assert "os.system" not in codigo
    assert "subprocess.call" not in codigo
    assert "Popen" not in codigo


def test_14_default_runner_chama_subprocess_run_sem_shell(monkeypatch):
    capturado = {}

    class _Proc:
        returncode = 0

    def _fake_run(argv, **kwargs):
        capturado["argv"] = argv
        capturado["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(sr.subprocess, "run", _fake_run)
    assert sr._default_runner(["python", "-m", "x"], 42) == 0
    assert "shell" not in capturado["kwargs"], "shell foi passado explicitamente"
    assert capturado["kwargs"]["timeout"] == 42
    assert capturado["kwargs"]["cwd"] == str(sr.REPO_ROOT)
    assert isinstance(capturado["argv"], list)


# ===========================================================================
# 15. Nenhum uso de --table all
# ===========================================================================

def test_15_nenhum_target_usa_table_all():
    for t in sr.TARGET_ORDER:
        args = sr.TARGETS[t].module_args
        assert "all" not in args, t


def test_15_targets_tiktok_sempre_passam_table_explicito():
    assert sr.TARGETS["brand"].module_args == ("--table", "brand")
    assert sr.TARGETS["creator"].module_args == ("--table", "creator")
    assert sr.TARGETS["ml"].module_args == ()


def test_15_argv_final_nunca_contem_all():
    for t in sr.TARGET_ORDER:
        _, runner, _ = run(t, source_max=D2, apply=True)
        assert "all" not in runner.argv


def test_15_uma_invocacao_cobre_exatamente_um_target():
    """Cada chamada resolve UM target. Nao existe caminho que resolva dois."""
    _, runner, _ = run("brand", source_max=D1, apply=True)
    assert runner.count == 1
    assert "brand" in runner.argv
    assert "creator" not in runner.argv


# ===========================================================================
# Allowlist fechada de targets
# ===========================================================================

def test_allowlist_de_targets_e_exatamente_ml_brand_creator():
    assert set(sr.TARGETS) == {"ml", "brand", "creator"}
    assert sr.TARGET_ORDER == ("ml", "brand", "creator")


@pytest.mark.parametrize("desconhecido", ["all", "shopee", "tiktok", "ML", "", "../ml"])
def test_target_fora_da_allowlist_falha_sem_conexao(desconhecido):
    conn = FakeConn(D1)
    runner = RunnerSpy()
    with pytest.raises(ValueError, match="target desconhecido"):
        sr.run_target(desconhecido, apply=True, today=HOJE,
                      conn_factory=lambda: conn, runner=runner)
    assert runner.count == 0
    assert conn.executed == []


def test_cli_nao_aceita_target_fora_da_allowlist():
    with pytest.raises(SystemExit):
        sr.build_parser().parse_args(["--target", "all"])
    with pytest.raises(SystemExit):
        sr.build_parser().parse_args(["--target", "shopee"])


def test_nenhum_target_de_shopee_em_lugar_algum():
    codigo = code_only(MODULE_PATH).lower()
    assert "shopee" not in codigo
    for t in sr.TARGET_ORDER:
        assert "shopee" not in sr.TARGETS[t].source_relation.lower()


# ---------------------------------------------------------------------------
# source_max: allowlist de marca como PARAMETRO, identificadores validados
# ---------------------------------------------------------------------------

def test_allowlist_de_marca_vem_do_modulo_de_sync_nao_de_uma_copia():
    """Identidade por `is`: nao e' uma quarta lista de marcas no repositorio."""
    assert sr.TARGETS["brand"].brand_allowlist is tk_sync.ALLOWED_BRANDS
    assert sr.TARGETS["creator"].brand_allowlist is tk_sync.ALLOWED_BRANDS
    assert tk_sync.ALLOWED_BRANDS is tk_sync.BRANDS_IN_SCOPE


def test_source_max_do_tiktok_filtra_pela_mesma_allowlist_da_leitura_real():
    """Sem esse filtro o numero divergiria: uma marca FORA da allowlist com data
    mais recente produziria um source_max que o CLI nao consegue cobrir, e a
    cobertura por dia reprovaria a janela."""
    for t in ("brand", "creator"):
        sql = sr.build_source_max_query(sr.TARGETS[t])
        assert "brand = ANY(%(brands)s)" in sql
        assert sr.source_max_params(sr.TARGETS[t])["brands"] == list(tk_sync.ALLOWED_BRANDS)


def test_source_max_do_ml_nao_filtra_marca_porque_a_leitura_real_tambem_nao():
    sql = sr.build_source_max_query(sr.TARGETS["ml"])
    assert "brand" not in sql
    assert sr.source_max_params(sr.TARGETS["ml"]) == {}
    assert "brand = ANY" not in ml_sync.build_source_query()


def test_nenhum_valor_da_allowlist_e_interpolado_no_texto_do_sql():
    for t in ("brand", "creator"):
        sql = sr.build_source_max_query(sr.TARGETS[t])
        for marca in tk_sync.ALLOWED_BRANDS:
            assert marca not in sql


def test_identificadores_do_sql_passam_por_validacao():
    """Um identificador malformado tem que falhar alto, nao virar injecao."""
    ruim = sr.Target(
        name="x", module="m", module_args=(), source_relation="gold.t; DROP TABLE y",
        date_column="date", source_min_date=date(2025, 1, 1), advisory_lock_key=1,
        brand_allowlist=None, preflight_source="p", step_name="s",
        step_timeout_seconds=10, child_timeout_seconds=5,
    )
    with pytest.raises(ValueError, match="falhou na validacao de seguranca"):
        sr.build_source_max_query(ruim)


def test_coluna_de_data_malformada_tambem_e_rejeitada():
    ruim = sr.Target(
        name="x", module="m", module_args=(), source_relation="gold.t",
        date_column="date) FROM x --", source_min_date=date(2025, 1, 1),
        advisory_lock_key=1, brand_allowlist=None, preflight_source="p",
        step_name="s", step_timeout_seconds=10, child_timeout_seconds=5,
    )
    with pytest.raises(ValueError, match="falhou na validacao de seguranca"):
        sr.build_source_max_query(ruim)


# ---------------------------------------------------------------------------
# 25. Run IDs distintos, sanitizados, sem colisao obvia
# ---------------------------------------------------------------------------

def test_25_run_ids_dos_tres_targets_sao_distintos_no_mesmo_instante():
    agora = datetime(2026, 8, 17, 6, 30, 0)
    ids = {sr.default_run_id(sr.TARGETS[t], agora) for t in sr.TARGET_ORDER}
    assert len(ids) == 3, ids


def test_25_run_id_identifica_target_e_instante():
    rid = sr.default_run_id(sr.TARGETS["creator"], datetime(2026, 8, 17, 6, 30, 5))
    assert rid == "serving_creator_20260817_063005"


def test_25_run_id_e_encaminhado_ao_cli():
    _, runner, _ = run("ml", source_max=D1, apply=True)
    rid = argv_value(runner.argv, "--run-id")
    assert rid.startswith("serving_ml_")


def test_25_run_id_explicito_do_operador_e_sanitizado():
    _, runner, _ = run("ml", source_max=D1, apply=True,
                       run_id="rodada 1; rm -rf / && echo $HOME")
    rid = argv_value(runner.argv, "--run-id")
    for proibido in (" ", ";", "&", "$", "/", "*"):
        assert proibido not in rid, rid


def test_25_sanitizador_de_run_id_e_o_mesmo_dos_modulos_validados():
    assert sr.sanitize_run_id is tk_sync.sanitize_run_id
    bruto = "a b;c/d"
    assert sr.sanitize_run_id(bruto) == tk_sync.sanitize_run_id(bruto)
    assert sr.sanitize_run_id(bruto) == ml_sync.sanitize_run_id(bruto)


# ---------------------------------------------------------------------------
# 26. Sanitizacao de erros: zero topologia, zero credencial
# ---------------------------------------------------------------------------

def test_26_sanitizador_de_erro_e_o_do_modulo_validado():
    assert sr.sanitize_error_message is tk_sync.sanitize_error_message


def test_26_erro_de_conexao_nao_vaza_host_ip_nem_porta(monkeypatch, capsys):
    nativo = (
        'connection to server at "datamart-interno.exemplo.local" (10.1.2.3), '
        "port 5432 failed: timeout expired"
    )

    def _boom():
        raise RuntimeError(nativo)

    monkeypatch.setattr(sr, "_datamart_readonly", _boom)
    codigo = sr.main(["--target", "ml", "--apply"])
    err = capsys.readouterr().err
    assert codigo == sr.EXIT_WRAPPER_FAILURE
    for topologia in ("datamart-interno.exemplo.local", "10.1.2.3", "5432"):
        assert topologia not in err, err


def test_26_credencial_em_mensagem_de_erro_e_redigida(monkeypatch, capsys):
    def _boom():
        raise RuntimeError("falha ao validar postgresql://usuario:senha@host/db")

    monkeypatch.setattr(sr, "_datamart_readonly", _boom)
    sr.main(["--target", "brand", "--apply"])
    err = capsys.readouterr().err
    assert "senha" not in err
    assert "usuario:senha" not in err


def test_26_nenhuma_url_e_impressa_nem_sanitizada(capsys):
    """O wrapper nao imprime topologia em NENHUM caminho, nem no de sucesso."""
    run("creator", source_max=D2, apply=True)
    saida = capsys.readouterr().out
    for termo in ("postgres", "postgresql://", "@", "5432", "DATAMART_DATABASE_URL", "DATABASE_URL"):
        assert termo not in saida, f"{termo!r} apareceu no stdout"


def test_26_variaveis_de_ambiente_nunca_sao_impressas():
    codigo = code_only(MODULE_PATH)
    assert "print(os.environ" not in codigo
    assert "print(url" not in codigo
    assert "sanitize_url" not in codigo, "nem a URL sanitizada deve ser impressa aqui"


# ---------------------------------------------------------------------------
# 11 (log): o wrapper registra o que o operador precisa auditar
# ---------------------------------------------------------------------------

def test_log_registra_todos_os_campos_exigidos(capsys):
    run("creator", source_max=D2, apply=True, exit_code=0)
    saida = capsys.readouterr().out
    for campo in ("modo=APPLY", "source_max", "D-1 (Sao Paulo)", "effective_date_to",
                  "date_from", "lookback_days", "run_id", "advisory lock", "exit code"):
        assert campo in saida, f"campo ausente no log: {campo}"
    assert "2026-08-15" in saida   # source_max e effective_date_to
    assert "2026-08-16" in saida   # D-1, para o operador ver a diferenca


def test_log_diz_quando_a_janela_foi_limitada_pela_fonte(capsys):
    run("creator", source_max=D2, apply=True)
    assert "limitado pela fonte" in capsys.readouterr().out


def test_log_diz_quando_a_janela_alcancou_d1(capsys):
    run("brand", source_max=D1, apply=True)
    assert "igual a D-1" in capsys.readouterr().out


def test_log_do_advisory_lock_e_apenas_informativo():
    """O wrapper nunca adquire lock: quem serializa e' a transacao do CLI.
    Adquirir aqui criaria auto-bloqueio contra o proprio filho."""
    codigo = code_only(MODULE_PATH)
    assert "pg_advisory" not in codigo
    assert "advisory_xact_lock" not in codigo


# ---------------------------------------------------------------------------
# 29 e 30. CLIs validados intactos; nenhuma dependencia nova
# ---------------------------------------------------------------------------

def test_29_os_clis_validados_continuam_aceitando_o_contrato_que_o_wrapper_usa():
    """Comportamental: se um dos CLIs perdesse --date-from/--date-to/--run-id, a
    ponte inteira deixaria de funcionar silenciosamente."""
    a = ml_sync.build_parser().parse_args(
        ["--date-from", "2026-05-19", "--date-to", "2026-08-16", "--run-id", "x", "--apply"])
    assert (a.date_from, a.date_to, a.run_id, a.apply) == ("2026-05-19", "2026-08-16", "x", True)

    b = tk_sync.build_parser().parse_args(
        ["--table", "creator", "--date-from", "2026-05-18", "--date-to", "2026-08-15",
         "--run-id", "y", "--apply"])
    assert (b.table, b.date_from, b.date_to, b.run_id, b.apply) == (
        "creator", "2026-05-18", "2026-08-15", "y", True)


def test_29_os_clis_validados_continuam_com_o_default_de_diagnostico():
    assert ml_sync.build_parser().parse_args([]).apply is False
    assert tk_sync.build_parser().parse_args([]).apply is False


def test_29_o_wrapper_nao_redefine_regra_de_negocio_dos_clis():
    """Reuso por referencia, nao reimplementacao: fuso, sanitizadores e limites
    de lookback sao os MESMOS objetos dos modulos validados."""
    assert sr.TZ_OPERACIONAL is tk_sync.TZ_OPERACIONAL
    assert sr.validate_identifier is tk_sync.validate_identifier
    assert sr.validate_qualified is tk_sync.validate_qualified


def test_30_nenhuma_dependencia_nova():
    """Imports do wrapper restritos a stdlib + psycopg2 + os dois modulos de
    sync (todos ja presentes no projeto antes desta task)."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    raizes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raizes.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            raizes.add(node.module.split(".")[0])
    permitidas = {
        "__future__", "argparse", "os", "subprocess", "sys", "dataclasses",
        "datetime", "pathlib", "psycopg2", "pipelines", "dotenv",
    }
    assert raizes <= permitidas, f"import inesperado: {raizes - permitidas}"


def test_30_nenhuma_biblioteca_de_agendamento_ou_airflow():
    codigo = code_only(MODULE_PATH).lower()
    for termo in ("airflow", "dag", "schtasks", "register-scheduledtask",
                  "scheduledtask", "crontab", "apscheduler", "celery"):
        assert termo not in codigo, f"{termo!r} presente no wrapper"
