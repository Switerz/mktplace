"""
Testes de pipelines/ops/sync_region_if_needed.py: wrapper condicional que
so' dispara pipelines.sync_region_daily.run_sync quando o diagnose (somente
leitura) reporta needs_sync=True. Usa diagnose_fn/sync_fn injetados (fakes)
— nenhum psycopg2/banco real e' tocado.
"""
import pytest

import pipelines.ops.sync_region_if_needed as sut


def _report(needs_sync, n=100, target_n=100):
    return {
        "source_agg": {"n": n, "gmv": 1000.0},
        "target_exists": True,
        "target_agg": {"n": target_n, "gmv": 1000.0} if target_n is not None else None,
        "needs_sync": needs_sync,
    }


def _sync_result(n=100):
    return {"backup_table": "fact_marketplace_region_daily_backup_20260715", "source_agg": {"n": n}, "real_agg_after": {"n": n}}


# ---------------------------------------------------------------------------
# no_op: needs_sync=False nunca chama sync
# ---------------------------------------------------------------------------

def test_no_op_quando_diagnose_diz_que_nao_precisa_sincronizar():
    sync_calls = []
    diagnose_fn = lambda: _report(needs_sync=False)
    sync_fn = lambda args: sync_calls.append(args) or _sync_result()

    result = sut.run(diagnose_fn=diagnose_fn, sync_fn=sync_fn)

    assert result.no_op is True
    assert result.synced is False
    assert sync_calls == [], "sync NUNCA deveria ser chamado quando needs_sync=False"


def test_no_op_reporta_contagem_da_fonte():
    diagnose_fn = lambda: _report(needs_sync=False, n=553)
    result = sut.run(diagnose_fn=diagnose_fn, sync_fn=lambda args: pytest.fail("sync nao deveria ser chamado"))
    assert result.source_rows == 553


# ---------------------------------------------------------------------------
# needs_sync=True chama sync exatamente uma vez, com a flag --sync
# ---------------------------------------------------------------------------

def test_needs_sync_chama_sync_exatamente_uma_vez_com_flag_sync():
    sync_calls = []

    def _sync_fn(args):
        sync_calls.append(args)
        return _sync_result(n=200)

    result = sut.run(diagnose_fn=lambda: _report(needs_sync=True, n=200, target_n=100), sync_fn=_sync_fn)

    assert len(sync_calls) == 1
    assert sync_calls[0].sync is True
    assert result.synced is True
    assert result.no_op is False
    assert result.target_rows_after == 200
    assert result.backup_table == "fact_marketplace_region_daily_backup_20260715"


def test_needs_sync_com_destino_inexistente_ainda_chama_sync():
    """target_agg=None (tabela nao existe ainda) tambem conta como
    needs_sync=True — o wrapper nao deve exigir target_agg presente para
    decidir chamar o sync."""
    sync_calls = []
    diagnose_fn = lambda: _report(needs_sync=True, n=50, target_n=None)
    sync_fn = lambda args: sync_calls.append(args) or _sync_result(n=50)

    result = sut.run(diagnose_fn=diagnose_fn, sync_fn=sync_fn)

    assert len(sync_calls) == 1
    assert result.target_rows_before is None
    assert result.synced is True


# ---------------------------------------------------------------------------
# Falha de diagnose aborta ANTES de qualquer tentativa de sync
# ---------------------------------------------------------------------------

def test_falha_de_diagnose_aborta_antes_de_tentar_sync():
    sync_calls = []

    def _diagnose_fn():
        raise RuntimeError("DATAMART_DATABASE_URL nao definido")

    def _sync_fn(args):
        sync_calls.append(args)
        return _sync_result()

    with pytest.raises(sut.SyncIfNeededError) as exc_info:
        sut.run(diagnose_fn=_diagnose_fn, sync_fn=_sync_fn)

    assert sync_calls == [], "sync nunca deveria ser tentado se o diagnose falhou"
    assert "diagnose falhou" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Falha de sync propaga como SyncIfNeededError (erro sanitizado)
# ---------------------------------------------------------------------------

def test_falha_de_sync_propaga_como_erro_sanitizado():
    def _sync_fn(args):
        raise RuntimeError("Gate 6B requer a variavel de ambiente I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1 explicitamente definida")

    with pytest.raises(sut.SyncIfNeededError) as exc_info:
        sut.run(diagnose_fn=lambda: _report(needs_sync=True), sync_fn=_sync_fn)

    assert "sync falhou" in str(exc_info.value)


def test_erro_de_diagnose_e_de_sync_nunca_vazam_credenciais_na_mensagem():
    def _diagnose_fn():
        raise RuntimeError("connection to server failed: postgresql://segredouser:S3nhaSecreta@rds-host/db")

    with pytest.raises(sut.SyncIfNeededError) as exc_info:
        sut.run(diagnose_fn=_diagnose_fn, sync_fn=lambda args: pytest.fail("nao deveria chamar sync"))

    message = str(exc_info.value)
    assert "S3nhaSecreta" not in message
    assert "segredouser" not in message


# ---------------------------------------------------------------------------
# Sem retry automatico: cada chamada a run() tenta no maximo 1 diagnose + 1 sync
# ---------------------------------------------------------------------------

def test_sem_retry_automatico_em_falha_de_sync():
    sync_calls = []

    def _sync_fn(args):
        sync_calls.append(args)
        raise RuntimeError("falha simulada")

    with pytest.raises(sut.SyncIfNeededError):
        sut.run(diagnose_fn=lambda: _report(needs_sync=True), sync_fn=_sync_fn)

    assert len(sync_calls) == 1, "run() nao deve reter automaticamente apos uma falha de sync"


def test_sem_retry_automatico_em_falha_de_diagnose():
    diagnose_calls = []

    def _diagnose_fn():
        diagnose_calls.append(1)
        raise RuntimeError("falha simulada")

    with pytest.raises(sut.SyncIfNeededError):
        sut.run(diagnose_fn=_diagnose_fn, sync_fn=lambda args: pytest.fail("nao deveria chamar sync"))

    assert len(diagnose_calls) == 1


# ---------------------------------------------------------------------------
# main() — CLI: exit codes e nunca propaga excecao nativa para fora
# ---------------------------------------------------------------------------

def test_main_retorna_0_no_op(monkeypatch, capsys):
    monkeypatch.setattr(sut, "run", lambda: sut.SyncIfNeededResult(no_op=True, needs_sync=False, source_rows=100))
    exit_code = sut.main()
    assert exit_code == 0
    assert "NO_OP" in capsys.readouterr().out


def test_main_retorna_0_quando_sync_e_executado(monkeypatch, capsys):
    monkeypatch.setattr(
        sut, "run",
        lambda: sut.SyncIfNeededResult(
            no_op=False, needs_sync=True, synced=True,
            source_rows=200, target_rows_before=100, target_rows_after=200,
            backup_table="fact_marketplace_region_daily_backup_20260715",
        ),
    )
    exit_code = sut.main()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "SYNC realizado" in out
    assert "fact_marketplace_region_daily_backup_20260715" in out


def test_main_retorna_1_e_nunca_propaga_excecao_quando_run_falha(monkeypatch, capsys):
    def _raise():
        raise sut.SyncIfNeededError("sync falhou: falha de conexao (detalhes omitidos por seguranca)")
    monkeypatch.setattr(sut, "run", _raise)

    exit_code = sut.main()

    assert exit_code == 1
    assert "ERRO" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

def test_nunca_le_a_variavel_de_consentimento_diretamente_neste_modulo():
    """A guarda de consentimento (I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY)
    e' responsabilidade exclusiva de pipelines.sync_region_daily.run_sync —
    este wrapper nao deve reimplementar/duplicar a checagem em codigo (so'
    delegar), mesmo que a mencione na documentacao do modulo."""
    import re
    from pathlib import Path
    source = Path(sut.__file__).read_text(encoding="utf-8")
    assert not re.search(r'os\.environ(?:\.get)?\(\s*["\']I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY', source)


def test_nunca_ativa_task_scheduler_ou_chama_subprocess():
    from pathlib import Path
    source = Path(sut.__file__).read_text(encoding="utf-8")
    assert "schtasks" not in source.lower()
    assert "subprocess" not in source.lower()


# ---------------------------------------------------------------------------
# Gate B6.1b — main() tenta carregar o consentimento PERSISTENTE
# (pipelines.ops.region_sync_consent, arquivo `.env.region-sync.local`)
# antes de run(), para tambem funcionar quando este modulo e' invocado
# standalone (sem o preflight do orquestrador ja ter resolvido o
# consentimento antes) — necessario para a execucao AGENDADA.
# ---------------------------------------------------------------------------

_CONSENT_KEY = "I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY"


@pytest.fixture
def _clean_region_consent_env():
    """region_sync_consent.ensure_region_sync_consent muta os.environ
    diretamente (nao via monkeypatch) quando o consentimento vem de
    arquivo — limpa antes/depois para nunca vazar entre testes."""
    import os
    os.environ.pop(_CONSENT_KEY, None)
    yield
    os.environ.pop(_CONSENT_KEY, None)


def test_main_needs_sync_com_consentimento_do_arquivo_chama_sync_uma_vez(monkeypatch, tmp_path, capsys, _clean_region_consent_env):
    import os

    monkeypatch.delenv(_CONSENT_KEY, raising=False)
    consent_file = tmp_path / ".env.region-sync.local"
    consent_file.write_text(f"{_CONSENT_KEY}=1\n", encoding="utf-8")
    monkeypatch.setattr(sut.region_sync_consent, "DEFAULT_REGION_SYNC_CONSENT_PATH", consent_file)

    sync_calls = []

    def _sync_fn(args):
        # confirma que, no momento em que o sync de verdade seria chamado,
        # a env var ja esta presente no processo (carregada do arquivo)
        assert os.environ.get(_CONSENT_KEY) == "1"
        sync_calls.append(args)
        return _sync_result(n=10)

    monkeypatch.setattr(sut.srd, "run_diagnose", lambda: _report(needs_sync=True, n=10, target_n=5))
    monkeypatch.setattr(sut.srd, "run_sync", _sync_fn)
    monkeypatch.setattr(sut.sys, "argv", ["sync_region_if_needed.py"])

    exit_code = sut.main()

    assert exit_code == 0
    assert len(sync_calls) == 1
    assert "SYNC realizado" in capsys.readouterr().out


def test_main_needs_sync_false_nao_chama_sync_mesmo_com_consentimento_no_arquivo(monkeypatch, tmp_path, capsys, _clean_region_consent_env):
    """needs_sync=False nunca deve exigir/chamar escrita real, mesmo quando
    ha' consentimento persistente disponivel — o diagnose ainda decide."""
    monkeypatch.delenv(_CONSENT_KEY, raising=False)
    consent_file = tmp_path / ".env.region-sync.local"
    consent_file.write_text(f"{_CONSENT_KEY}=1\n", encoding="utf-8")
    monkeypatch.setattr(sut.region_sync_consent, "DEFAULT_REGION_SYNC_CONSENT_PATH", consent_file)

    sync_calls = []
    monkeypatch.setattr(sut.srd, "run_diagnose", lambda: _report(needs_sync=False))
    monkeypatch.setattr(sut.srd, "run_sync", lambda args: sync_calls.append(args) or _sync_result())
    monkeypatch.setattr(sut.sys, "argv", ["sync_region_if_needed.py"])

    exit_code = sut.main()

    assert exit_code == 0
    assert sync_calls == [], "needs_sync=False nunca deveria chamar sync, mesmo com consentimento disponivel"
    assert "NO_OP" in capsys.readouterr().out


def test_main_sem_consentimento_bloqueia_antes_de_qualquer_escrita(monkeypatch, tmp_path, capsys, _clean_region_consent_env):
    """Sem env var e sem arquivo (persistente ou de sessao): o gate
    ORIGINAL de sync_region_daily.run_sync (nao tocado neste Gate) continua
    recusando antes de qualquer tentativa de escrita real — confirma que a
    ausencia de consentimento persistente nao abre uma porta lateral."""
    monkeypatch.delenv(_CONSENT_KEY, raising=False)
    monkeypatch.setattr(sut.region_sync_consent, "DEFAULT_REGION_SYNC_CONSENT_PATH", tmp_path / "nao-existe.local")

    def _fake_sync_fn_espelha_o_gate_original(args):
        # Espelha o gate ORIGINAL de sync_region_daily.run_sync sem
        # duplicar a implementacao real (que tem sua propria suite de
        # testes) — so' confirma que o wrapper propaga a recusa.
        import os as _os
        if _os.environ.get(_CONSENT_KEY) != "1":
            raise RuntimeError(
                "Gate 6B requer a variavel de ambiente "
                "I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1 explicitamente definida"
            )
        pytest.fail("nao deveria alcancar o corpo do sync sem consentimento")

    monkeypatch.setattr(sut.srd, "run_diagnose", lambda: _report(needs_sync=True))
    monkeypatch.setattr(sut.srd, "run_sync", _fake_sync_fn_espelha_o_gate_original)
    monkeypatch.setattr(sut.sys, "argv", ["sync_region_if_needed.py"])

    exit_code = sut.main()

    assert exit_code == 1
    assert "ERRO" in capsys.readouterr().err


def test_main_chama_ensure_region_sync_consent(monkeypatch):
    """Confirma que main() de fato tenta carregar o consentimento
    persistente (nao so' documentacao) — sem essa chamada, a execucao
    agendada standalone nunca encontraria o arquivo."""
    calls = []
    monkeypatch.setattr(sut.region_sync_consent, "ensure_region_sync_consent", lambda: calls.append(1) or False)
    monkeypatch.setattr(sut, "run", lambda: sut.SyncIfNeededResult(no_op=True, needs_sync=False, source_rows=0))
    sut.main()
    assert calls == [1]
