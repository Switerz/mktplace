"""OPS-AUTOMATION-2 — TaskKey/pipeline `daily_chain`: full_daily e PMA em sequencia.

O defeito medido em 16/10/2026 00:59:13 foi a DISPUTA do lock logico: as duas
tarefas dispararam juntas ao acordar e o full_daily saiu BLOCKED. A cadeia
mantem o lock compartilhado e transforma as duas cargas numa sequencia.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pipelines.ops import orchestrate as o

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TASK = REPO_ROOT / "scripts" / "run_task.ps1"
TASK_XML = REPO_ROOT / "infra" / "windows" / "mktplace_daily_chain.task.xml"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _nomes(pipeline):
    return [s.name for s in o.PIPELINES[pipeline]]


def _taskkey(nome):
    linha = next(l for l in RUN_TASK.read_text(encoding="utf-8", errors="replace").splitlines()
                 if f'"{nome}" = @{{' in l)
    lock = re.search(r'Lock\s*=\s*"([^"]+)"', linha).group(1)
    timeout = int(re.search(r'TimeoutSeconds\s*=\s*(\d+)', linha).group(1))
    return lock, timeout, linha


# --- composicao ---------------------------------------------------------------

def test_cadeia_e_full_daily_sem_health_expedicao_tiktok_canais_pma_e_health():
    fd = [n for n in _nomes("full_daily") if n != "health_check"]
    esperado = fd + ["expedicao_tiktok"] + list(o.PMA_CANAIS) + ["health_check"]
    assert _nomes("daily_chain") == esperado


def test_steps_sao_os_MESMOS_objetos_dos_pipelines_de_origem():
    """Reuso, nao copia: argumentos, timeouts e criticidade nao podem divergir."""
    por_nome = {s.name: s for s in o.PIPELINES["daily_chain"]}
    for s in o.PIPELINES["full_daily"]:
        assert por_nome[s.name] is s
    for s in o.PIPELINES["pma_refresh"]:
        if s.name in o.PMA_CANAIS:
            assert por_nome[s.name] is s


def test_health_check_e_o_ultimo_critico_e_sempre_roda():
    ultimo = o.PIPELINES["daily_chain"][-1]
    assert ultimo.name == "health_check"
    assert ultimo.critical is True and ultimo.always_run is True
    assert _nomes("daily_chain").count("health_check") == 1


def test_canais_pma_continuam_nao_criticos_e_sem_dependencia():
    for s in o.PIPELINES["daily_chain"]:
        if s.name in o.PMA_CANAIS:
            assert s.critical is False
            assert s.depends_on == ()
            assert s.exit_status_map is o.PMA_EXIT_STATUS


def test_nomes_unicos_e_dependencias_resolvem_dentro_da_cadeia():
    nomes = _nomes("daily_chain")
    assert len(nomes) == len(set(nomes))
    for i, s in enumerate(o.PIPELINES["daily_chain"]):
        for dep in s.depends_on:
            assert dep in nomes[:i], f"{s.name} depende de {dep}, que nao roda antes"


def test_pipelines_de_origem_nao_mudaram():
    assert "daily_chain" not in _nomes("full_daily")
    assert _nomes("pma_refresh") == list(o.PMA_CANAIS) + ["health_check"]
    assert o.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS == 8100


# --- lock e timeouts ------------------------------------------------------------

def test_taskkey_daily_chain_usa_o_MESMO_lock_logico():
    lock, _, linha = _taskkey("daily_chain")
    assert lock == "full_daily" == _taskkey("full_daily")[0] == _taskkey("pma_refresh")[0]
    assert '"--pipeline", "daily_chain"' in linha


def test_timeout_externo_cobre_o_orcamento_com_margem_acima_de_15_por_cento():
    _, timeout, _ = _taskkey("daily_chain")
    orcamento = o.DAILY_CHAIN_STEP_TIMEOUT_BUDGET_SECONDS
    assert orcamento == 11700   # 7920 + 900 (expedicao TikTok) + 2700 + 180
    assert timeout - orcamento > 0.15 * orcamento


def test_template_da_tarefa_e_coerente_com_o_wrapper():
    raiz = ET.parse(TASK_XML).getroot()
    g = lambda caminho: raiz.find(caminho, NS).text
    assert g("t:Principals/t:Principal/t:LogonType") == "S4U"
    assert g("t:Settings/t:WakeToRun") == "true"
    assert g("t:Settings/t:StartWhenAvailable") == "true"
    assert g("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert "-TaskKey daily_chain" in g("t:Actions/t:Exec/t:Arguments")
    limite = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", g("t:Settings/t:ExecutionTimeLimit"))
    segundos = int(limite.group(1) or 0) * 3600 + int(limite.group(2) or 0) * 60
    _, timeout, _ = _taskkey("daily_chain")
    assert segundos > timeout, "a tarefa nao pode matar o wrapper antes do lock"


# --- exit ---------------------------------------------------------------------

def _tudo(status="SUCCESS", **excecoes):
    res = {n: status for n in _nomes("daily_chain")}
    res.update(excecoes)
    return res


def test_tudo_sucesso_sai_zero():
    res = _tudo()
    assert o.exit_code_do_pipeline("daily_chain", res, o.compute_overall_status("daily_chain", res)) == 0


def test_health_check_falho_mantem_a_semantica_do_full_daily():
    res = _tudo(health_check="FAILED")
    overall = o.compute_overall_status("daily_chain", res)
    assert overall == "FAILED"
    assert o.exit_code_do_pipeline("daily_chain", res, overall) == 1


@pytest.mark.parametrize("canal", list(o.PMA_CANAIS))
@pytest.mark.parametrize("status", ["REFUSED", "LOCKED", "FAILED", "BLOCKED", "INDETERMINATE"])
def test_canal_pma_nao_publicado_faz_a_cadeia_sair_1(canal, status):
    """OPS-AUTOMATION-3: um step recusado (ou travado, falho, bloqueado,
    indeterminado) faz o resultado da cadeia falhar — exit 0 so' com o dia
    inteiro publicado."""
    res = _tudo(**{canal: status})
    overall = o.compute_overall_status("daily_chain", res)
    assert o.exit_code_do_pipeline("daily_chain", res, overall) == 1


def test_step_critico_do_full_daily_falho_faz_a_cadeia_sair_1_mesmo_com_pma_ok():
    res = _tudo(daily_ml="FAILED")
    overall = o.compute_overall_status("daily_chain", res)
    assert overall == "FAILED"
    assert o.exit_code_do_pipeline("daily_chain", res, overall) == 1


def test_canal_pma_indeterminado_vira_indeterminate_e_exit_1():
    res = _tudo(pma_shopee="INDETERMINATE")
    overall = o.compute_overall_status("daily_chain", res)
    assert overall == "INDETERMINATE"
    assert o.exit_code_do_pipeline("daily_chain", res, overall) == 1


def _roda_main(monkeypatch, capsys, res):
    import sys
    monkeypatch.setattr(sys, "argv", ["orchestrate", "--pipeline", "daily_chain"])
    monkeypatch.setattr(o, "run_pipeline", lambda *a, **k: res)   # nenhuma carga
    monkeypatch.setitem(sys.modules, "dotenv", type(sys)("dotenv"))
    sys.modules["dotenv"].load_dotenv = lambda **_k: None
    codigo = o.main()
    return codigo, capsys.readouterr().out


def test_log_final_traz_os_dois_resumos(monkeypatch, capsys):
    codigo, out = _roda_main(monkeypatch, capsys, _tudo())
    assert codigo == 0
    assert "RESUMO daily_chain" in out
    assert "RESUMO full_daily (dentro de daily_chain): OK" in out
    assert "RESUMO PMA (dentro de daily_chain): OK" in out
    assert "STATUS GERAL: OK" in out
    assert out.index("RESUMO full_daily") < out.index("RESUMO PMA") < out.index("STATUS GERAL")


def test_main_com_canal_recusado_devolve_1_e_mostra_no_resumo_pma(monkeypatch, capsys):
    codigo, out = _roda_main(monkeypatch, capsys, _tudo(pma_tiktok="REFUSED"))
    assert codigo == 1
    assert "RESUMO full_daily (dentro de daily_chain): OK" in out
    assert "RESUMO PMA (dentro de daily_chain): DEGRADED" in out
    assert "'pma_tiktok': 'REFUSED'" in out


def test_pma_refresh_mantem_a_politica_estrita():
    assert "pma_refresh" in o.PIPELINES_COM_EXIT_ESTRITO
    assert "daily_chain" not in o.PIPELINES_COM_EXIT_ESTRITO


def test_dry_run_da_cadeia_e_recusado_como_o_do_full_daily():
    # A funcao de recusa direta, que nao abre subprocesso: o teste nunca pode
    # disparar um step real.
    with pytest.raises(o.DryRunUnsupportedError):
        o.assert_dry_run_supported("daily_chain")


# --- template aceito pelo Agendador -------------------------------------------

def test_template_nao_declara_encoding():
    """O Agendador recebe a definicao como UTF-16 e recusa `encoding="UTF-8"`
    ("unable to switch the encoding", medido em 16/10/2026)."""
    primeira = TASK_XML.read_text(encoding="utf-8").lstrip("\ufeff").splitlines()[0]
    assert primeira.startswith("<?xml")
    assert "encoding" not in primeira


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="API COM do Agendador so' no Windows")
def test_template_e_aceito_pela_api_do_agendador_sem_registrar():
    import subprocess
    script = (
        "$s=New-Object -ComObject Schedule.Service;$s.Connect();$d=$s.NewTask(0);"
        f"$d.XmlText=(Get-Content -Raw '{TASK_XML}');"
        "'{0} {1} {2}' -f $d.Principal.LogonType,$d.Settings.MultipleInstances,$d.Settings.WakeToRun"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    # 2 = TASK_LOGON_S4U; 2 = TASK_INSTANCES_IGNORE_NEW
    assert proc.stdout.split() == ["2", "2", "True"], (proc.stdout, proc.stderr)
