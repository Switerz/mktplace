"""Gate PMA-2C5B-R2 — politica de exit do `pma_refresh`.

A decisao que este arquivo trava, em uma frase: `critical=False` governa
CONTINUIDADE entre canais, nao sucesso global.

A regra geral do orquestrador devolve 0 em `DEGRADED`, e isso e' certo para o
`full_daily`: um gap nao-critico ja conhecido nao deve fazer a carga do dia
"falhar" todo dia. Aqui a leitura e' outra — quem le o exit code e' o Task
Scheduler, e para ele 0 significa "a fotografia do dia esta publicada". Duas
publicacoes e uma recusa nao sao isso.

Por isso: SO' tres publicacoes confirmadas devolvem 0. Todo o resto devolve 1,
e o relatorio continua distinguindo POR QUE.
"""
from __future__ import annotations

import itertools

import pytest

from pipelines.ops import orchestrate as orch

CANAIS = ("pma_ml", "pma_shopee", "pma_tiktok")
NAO_PUBLICOU = ("FAILED", "REFUSED", "LOCKED", "INDETERMINATE", "BLOCKED")


def _resultado(ml: str, shopee: str, tiktok: str,
               health: str = "SUCCESS") -> dict[str, str]:
    return {"pma_ml": ml, "pma_shopee": shopee, "pma_tiktok": tiktok,
            "health_check": health}


def _exit(r: dict[str, str]) -> int:
    return orch.exit_code_do_pipeline(
        "pma_refresh", r, orch.compute_overall_status("pma_refresh", r))


# ---------------------------------------------------------------------------
# 1. A tabela do gate, linha por linha
# ---------------------------------------------------------------------------

def test_tres_publicados_e_o_unico_exit_zero():
    r = _resultado("SUCCESS", "SUCCESS", "SUCCESS")
    assert orch.compute_overall_status("pma_refresh", r) == "OK"
    assert _exit(r) == 0


@pytest.mark.parametrize("status", NAO_PUBLICOU)
@pytest.mark.parametrize("posicao", [0, 1, 2])
def test_qualquer_canal_que_nao_publicou_derruba_o_exit(status, posicao):
    """Sucesso parcial, recusa, lock, falha, indeterminado e bloqueio: todos
    devolvem 1, em qualquer das tres posicoes."""
    valores = ["SUCCESS", "SUCCESS", "SUCCESS"]
    valores[posicao] = status
    r = _resultado(*valores)
    assert _exit(r) == 1, f"{status} em {CANAIS[posicao]} saiu 0"


def test_zero_canal_publicado_nunca_e_verde():
    for combo in itertools.product(NAO_PUBLICOU, repeat=3):
        r = _resultado(*combo)
        assert _exit(r) == 1, f"{combo} saiu 0"


def test_um_unico_canal_publicado_tambem_sai_um():
    r = _resultado("SUCCESS", "REFUSED", "LOCKED")
    assert _exit(r) == 1


def test_todos_preflight_blocked_sai_um():
    r = _resultado("BLOCKED", "BLOCKED", "BLOCKED", health="BLOCKED")
    assert orch.compute_overall_status("pma_refresh", r) == "BLOCKED"
    assert _exit(r) == 1


def test_a_matriz_completa_das_tres_posicoes():
    """Exaustivo: 6^3 = 216 combinacoes. So' uma pode sair 0."""
    estados = ("SUCCESS",) + NAO_PUBLICOU
    zeros = [c for c in itertools.product(estados, repeat=3)
             if _exit(_resultado(*c)) == 0]
    assert zeros == [("SUCCESS", "SUCCESS", "SUCCESS")], (
        f"combinacoes indevidamente verdes: {zeros}")


# ---------------------------------------------------------------------------
# 2. INDETERMINATE e' sempre visivel
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("posicao", [0, 1, 2])
def test_indeterminate_aparece_no_agregado_mesmo_com_os_outros_publicando(posicao):
    """Nao pode se dissolver num `DEGRADED` lido como saudavel: e' o unico
    desfecho em que nao se sabe o que foi gravado."""
    valores = ["SUCCESS", "SUCCESS", "SUCCESS"]
    valores[posicao] = "INDETERMINATE"
    r = _resultado(*valores)
    assert orch.compute_overall_status("pma_refresh", r) == "INDETERMINATE"
    assert _exit(r) == 1


def test_indeterminate_tem_precedencia_sobre_degraded():
    r = _resultado("REFUSED", "INDETERMINATE", "LOCKED")
    assert orch.compute_overall_status("pma_refresh", r) == "INDETERMINATE"


def test_indeterminate_nunca_e_lido_como_falha_pre_commit():
    """`FAILED` afirma que a transacao foi desfeita. `INDETERMINATE` afirma que
    nao se sabe. Colapsar os dois seria inventar um fato."""
    r = _resultado("SUCCESS", "SUCCESS", "INDETERMINATE")
    assert orch.compute_overall_status("pma_refresh", r) != "FAILED"
    assert r["pma_tiktok"] != "FAILED"


def test_recusa_nao_e_confundida_com_falha_pre_commit():
    r = _resultado("REFUSED", "SUCCESS", "SUCCESS")
    assert r["pma_ml"] == "REFUSED"
    assert orch.compute_overall_status("pma_refresh", r) == "DEGRADED"


# ---------------------------------------------------------------------------
# 3. O relatorio distingue os cinco desfechos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resultado,agregado", [
    (_resultado("SUCCESS", "SUCCESS", "SUCCESS"), "OK"),
    (_resultado("REFUSED", "SUCCESS", "SUCCESS"), "DEGRADED"),
    (_resultado("BLOCKED", "BLOCKED", "BLOCKED", health="BLOCKED"), "BLOCKED"),
    (_resultado("INDETERMINATE", "SUCCESS", "SUCCESS"), "INDETERMINATE"),
])
def test_o_agregado_distingue_os_desfechos(resultado, agregado):
    assert orch.compute_overall_status("pma_refresh", resultado) == agregado


def test_failed_agregado_existe_para_step_critico():
    """`FAILED` continua reservado a step CRITICO — o `pma_refresh` nao tem
    nenhum, por desenho. O vocabulario existe; o pipeline nao o usa."""
    assert all(not s.critical for s in orch.PIPELINES["pma_refresh"])
    r = {s.name: "FAILED" for s in orch.PIPELINES["full_daily"] if s.critical}
    assert orch.compute_overall_status("full_daily", r) == "FAILED"


# ---------------------------------------------------------------------------
# 4. Continuidade — o exit estrito nao pode virar parada antecipada
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("codigo", [1, 2, 3, 4])
def test_um_canal_ruim_nao_impede_a_TENTATIVA_dos_seguintes(codigo):
    tentados = []

    def executor(step):
        tentados.append(step.name)
        return codigo if step.name == "pma_ml" else 0

    r = orch.run_pipeline("pma_refresh", executor=executor,
                          preflight_fn=lambda _f: (True, []))
    assert tentados == ["pma_ml", "pma_shopee", "pma_tiktok", "health_check"]
    assert r["pma_shopee"] == "SUCCESS" and r["pma_tiktok"] == "SUCCESS"
    assert _exit(r) == 1


def test_o_health_check_e_o_ultimo_step():
    assert orch.PIPELINES["pma_refresh"][-1].name == "health_check"


def test_o_health_check_roda_mesmo_depois_de_tudo_dar_errado():
    tentados = []

    def executor(step):
        tentados.append(step.name)
        return 0 if step.name == "health_check" else 1

    orch.run_pipeline("pma_refresh", executor=executor,
                      preflight_fn=lambda _f: (True, []))
    assert tentados[-1] == "health_check", (
        "e' justamente depois de recusa ou lock que saber o frescor importa")


def test_o_health_check_roda_mesmo_com_todos_os_canais_bloqueados():
    tentados = []

    def executor(step):
        tentados.append(step.name)
        return 0

    def preflight(fonte):
        return (False, [])  # todo canal bloqueado

    orch.run_pipeline("pma_refresh", executor=executor, preflight_fn=preflight)
    assert tentados == ["health_check"], (
        "nenhum canal executa, mas o diagnostico sim")


def test_o_health_check_nao_decide_o_exit_code():
    """Ele diagnostica. Tres canais publicados continuam valendo 0 mesmo com o
    health check reportando defasagem conhecida de outra fonte."""
    r = _resultado("SUCCESS", "SUCCESS", "SUCCESS", health="FAILED")
    assert _exit(r) == 0


# ---------------------------------------------------------------------------
# 5. Os demais pipelines NAO mudam de semantica
# ---------------------------------------------------------------------------

def test_so_o_pma_refresh_tem_politica_estrita():
    assert orch.PIPELINES_COM_EXIT_ESTRITO == frozenset({"pma_refresh"})


@pytest.mark.parametrize("nome", ["full_daily", "serving_refresh",
                                  "shopee_manual_refresh"])
def test_degraded_continua_saindo_zero_nos_pipelines_antigos(nome):
    """O contrato deles e' o de antes: gap nao-critico conhecido nao faz a
    carga do dia falhar."""
    r = {s.name: ("FAILED" if not s.critical else "SUCCESS")
         for s in orch.PIPELINES[nome]}
    agregado = orch.compute_overall_status(nome, r)
    if agregado == "DEGRADED":
        assert orch.exit_code_do_pipeline(nome, r, agregado) == 0


@pytest.mark.parametrize("nome", ["full_daily", "serving_refresh",
                                  "shopee_manual_refresh"])
def test_failed_continua_saindo_um_nos_pipelines_antigos(nome):
    r = {s.name: "SUCCESS" for s in orch.PIPELINES[nome]}
    critico = next((s.name for s in orch.PIPELINES[nome] if s.critical), None)
    if critico is None:
        pytest.skip(f"{nome} nao tem step critico")
    r[critico] = "FAILED"
    agregado = orch.compute_overall_status(nome, r)
    assert agregado == "FAILED"
    assert orch.exit_code_do_pipeline(nome, r, agregado) == 1


def test_nenhum_pipeline_antigo_produz_indeterminate():
    """So' steps com `exit_status_map` emitem esse status, e so' o
    `pma_refresh` tem um — entao a regra nova nao os alcanca."""
    for nome, steps in orch.PIPELINES.items():
        if nome == "pma_refresh":
            continue
        for step in steps:
            assert step.exit_status_map is None, f"{nome}/{step.name}"


# ---------------------------------------------------------------------------
# 6. O exit do Python chega ao Task Scheduler
# ---------------------------------------------------------------------------

def test_main_devolve_o_exit_code_da_politica(monkeypatch):
    """`main()` e' o que o wrapper do PowerShell executa; o valor que ele
    devolve e' o `%ERRORLEVEL%` que o Task Scheduler le."""
    import sys

    monkeypatch.setattr(sys, "argv", ["orchestrate", "--pipeline", "pma_refresh"])
    monkeypatch.setattr(orch, "run_pipeline",
                        lambda *a, **k: _resultado("SUCCESS", "REFUSED", "SUCCESS"))
    monkeypatch.setitem(sys.modules, "dotenv",
                        type(sys)("dotenv"))
    sys.modules["dotenv"].load_dotenv = lambda **_k: None
    assert orch.main() == 1


def test_main_devolve_zero_com_os_tres_publicados(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["orchestrate", "--pipeline", "pma_refresh"])
    monkeypatch.setattr(orch, "run_pipeline",
                        lambda *a, **k: _resultado("SUCCESS", "SUCCESS", "SUCCESS"))
    monkeypatch.setitem(sys.modules, "dotenv", type(sys)("dotenv"))
    sys.modules["dotenv"].load_dotenv = lambda **_k: None
    assert orch.main() == 0


def test_o_wrapper_propaga_o_exit_do_python():
    """Contraprova estrutural: `run_task.ps1` precisa repassar o
    `%ERRORLEVEL%`, senao a politica morre no PowerShell."""
    from pathlib import Path
    fonte = Path(__file__).resolve().parents[2] / "scripts" / "run_task.ps1"
    texto = fonte.read_text(encoding="utf-8", errors="replace")
    assert "LASTEXITCODE" in texto, (
        "o wrapper tem de propagar o exit code do Python")
