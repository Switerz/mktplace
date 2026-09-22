"""Gate PMA-2C5B — duas invocacoes concorrentes do `pma_refresh`.

Sao DUAS camadas de exclusao, com propositos distintos, e o teste separa as
duas porque elas falham de jeitos diferentes:

  1. LOCK LOGICO (`run_task.ps1`, `Lock = "full_daily"`) — impede a disputa
     ANTES de qualquer conexao ao banco. E' o que protege o sync do ML, cujo
     `pg_advisory_xact_lock` ESPERA em vez de desistir: sem ele, a segunda
     execucao ficaria pendurada ate o timeout do step.

  2. ADVISORY LOCK do Postgres — garante a exclusao mesmo que a primeira camada
     seja contornada (execucao direta por `python -m`, por exemplo). Nos canais
     e' `pg_try_advisory_lock`, fail-fast: a segunda sai com exit 3 sem ler nem
     escrever nada.

A segunda camada e' comportamento do SERVIDOR e so' se prova contra um
PostgreSQL de verdade — um dublê concordaria com qualquer coisa.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipelines.ops import orchestrate as orch
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TASK = REPO_ROOT / "scripts" / "run_task.ps1"

#: As chaves reais dos dois publishers.
LOCK_CANAIS = 917120017
LOCK_ML = 914_120_014


# ---------------------------------------------------------------------------
# 1. Lock LOGICO — antes do banco
# ---------------------------------------------------------------------------

def test_a_taskkey_pma_refresh_existe_no_wrapper():
    texto = RUN_TASK.read_text(encoding="utf-8", errors="replace")
    assert '"pma_refresh"' in texto


def test_o_pma_compartilha_o_lock_logico_do_full_daily():
    """Nao um lock proprio: os publishers do PMA leem as MESMAS fontes do Data
    Mart que o `full_daily` carrega. Compartilhar torna a sobreposicao manual x
    agendada impossivel por construcao."""
    texto = RUN_TASK.read_text(encoding="utf-8", errors="replace")
    linha = [l for l in texto.splitlines() if '"pma_refresh" = @{' in l]
    assert linha, "TaskKey pma_refresh ausente"
    assert re.search(r'Lock\s*=\s*"full_daily"', linha[0]), (
        "o PMA precisa do lock logico do full_daily, nao de um proprio")


def test_a_taskkey_aponta_para_o_pipeline_certo():
    texto = RUN_TASK.read_text(encoding="utf-8", errors="replace")
    linha = [l for l in texto.splitlines() if '"pma_refresh" = @{' in l][0]
    assert 'Module = "pipelines.ops.orchestrate"' in linha
    assert '"--pipeline", "pma_refresh"' in linha


def test_o_timeout_do_wrapper_cobre_a_soma_dos_steps():
    texto = RUN_TASK.read_text(encoding="utf-8", errors="replace")
    linha = [l for l in texto.splitlines() if '"pma_refresh" = @{' in l][0]
    m = re.search(r"TimeoutSeconds\s*=\s*(\d+)", linha)
    assert m, "TaskKey sem timeout"
    assert int(m.group(1)) >= orch.PMA_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS, (
        "o timeout externo precisa cobrir a soma dos timeouts individuais, "
        "senao o wrapper mata o pipeline no meio de um canal")


def test_nenhuma_tarefa_foi_registrada_no_windows():
    """Este gate implementa a orquestracao e NAO ativa o agendamento."""
    texto = RUN_TASK.read_text(encoding="utf-8", errors="replace")
    for proibido in ("Register-ScheduledTask", "schtasks /create",
                     "Enable-ScheduledTask"):
        assert proibido not in texto, (
            f"{proibido} apareceu no wrapper — o gate e' inerte")


# ---------------------------------------------------------------------------
# 2. Advisory lock — comportamento do servidor
# ---------------------------------------------------------------------------

def test_as_chaves_de_lock_nao_foram_alteradas():
    """Mudar a chave silenciosamente faria a nova execucao nao enxergar a
    antiga, e as duas publicariam em sequencia."""
    from pipelines import channel_offer_publisher as pub
    from pipelines import sync_ml_listing_price_serving as ml
    fonte = Path(pub.__file__).read_text(encoding="utf-8", errors="replace")
    assert str(LOCK_CANAIS) in fonte
    assert ml.ADVISORY_LOCK_KEY == LOCK_ML


def test_as_duas_chaves_sao_diferentes():
    """Canais e ML escrevem em fatos diferentes e podem correr juntos; uma
    chave unica os serializaria sem motivo."""
    assert LOCK_CANAIS != LOCK_ML


def test_o_preflight_de_cada_canal_checa_o_lock_certo():
    from pipelines.ops import preflight as pf
    assert pf.check_pma_ml_lock_free in pf.SOURCE_CHECKS["pma_ml"]
    for canal in ("pma_shopee", "pma_tiktok"):
        assert pf.check_pma_channel_lock_free in pf.SOURCE_CHECKS[canal]
    assert pf._PMA_CHANNEL_ADVISORY_LOCK_KEY == LOCK_CANAIS
    assert pf._PMA_ML_ADVISORY_LOCK_KEY == LOCK_ML


@pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)
def test_segunda_sessao_nao_obtem_o_lock_dos_canais():
    """`pg_try_advisory_lock` e' fail-fast: a segunda desiste em vez de esperar."""
    import psycopg2
    with cluster_da_sessao() as url:
        a = psycopg2.connect(url)
        b = psycopg2.connect(url)
        try:
            with a.cursor() as ca:
                ca.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_CANAIS,))
                assert ca.fetchone()[0] is True
            with b.cursor() as cb:
                cb.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_CANAIS,))
                assert cb.fetchone()[0] is False, (
                    "a segunda execucao NAO pode publicar em cima da primeira")
        finally:
            a.close()
            b.close()


@pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)
def test_o_preflight_ve_o_lock_tomado_e_bloqueia():
    """E' assim que uma espera silenciosa vira um BLOCKED explicito."""
    import os

    import psycopg2
    with cluster_da_sessao() as url:
        segurando = psycopg2.connect(url)
        try:
            with segurando.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_CANAIS,))
                assert cur.fetchone()[0] is True

            from pipelines.ops import preflight as pf
            antes = os.environ.get("DATABASE_URL")
            os.environ["DATABASE_URL"] = url
            try:
                resultado = pf.check_pma_channel_lock_free()
            finally:
                if antes is None:
                    os.environ.pop("DATABASE_URL", None)
                else:
                    os.environ["DATABASE_URL"] = antes
            assert resultado.ok is False
            assert "ja detem o lock" in resultado.detail
        finally:
            segurando.close()


@pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)
def test_lock_livre_nao_bloqueia():
    """Contraprova: sem o lock tomado, o mesmo check aprova."""
    import os

    with cluster_da_sessao() as url:
        from pipelines.ops import preflight as pf
        antes = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        try:
            resultado = pf.check_pma_channel_lock_free()
        finally:
            if antes is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = antes
        assert resultado.ok is True


# ---------------------------------------------------------------------------
# 3. Duas invocacoes do pipeline, sem banco
# ---------------------------------------------------------------------------

def test_duas_invocacoes_concorrentes_nao_publicam_as_duas():
    """Simula o que o advisory lock produz: a primeira publica, a segunda sai
    com `lock_unavailable` (exit 3). O resumo precisa distinguir as duas."""
    tomado = {"canais": False}

    def executor(step):
        if step.name == "pma_ml":
            return 0
        if tomado["canais"]:
            return 3  # EXIT_LOCKED
        tomado["canais"] = True
        return 0

    primeira = orch.run_pipeline("pma_refresh", executor=executor,
                                 preflight_fn=lambda _f: (True, []))
    segunda = orch.run_pipeline("pma_refresh", executor=executor,
                                preflight_fn=lambda _f: (True, []))

    assert primeira["pma_shopee"] == "SUCCESS"
    assert segunda["pma_shopee"] == "LOCKED"
    assert segunda["pma_tiktok"] == "LOCKED"
    assert orch.compute_overall_status("pma_refresh", segunda) == "DEGRADED", (
        "lock ocupado e' desfecho SEGURO: nao derruba o pipeline")


def test_a_segunda_invocacao_nao_e_retentada():
    """ZERO retry: o operador le a mensagem, decide, e roda de novo."""
    chamadas = []

    def executor(step):
        chamadas.append(step.name)
        return 3

    orch.run_pipeline("pma_refresh", executor=executor,
                      preflight_fn=lambda _f: (True, []))
    assert chamadas == ["pma_ml", "pma_shopee", "pma_tiktok", "health_check"], (
        "cada canal e' tentado UMA vez, e o diagnostico roda por ultimo")
