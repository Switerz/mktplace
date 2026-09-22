"""Gate PMA-2C5C-H1 — ensaio real e fail-closed do `pma_refresh`.

O PMA-2C5C terminou em `ORCHESTRATOR_DRY_RUN_UNAVAILABLE` por dois motivos, e
este arquivo trava a correcao dos dois:

  1. `--apply` era literal nos tres steps e o orquestrador nao tinha modo
     ensaio — nao havia como exercitar o fluxo sem publicar;
  2. o caminho sem `--apply` dos canais era um `print` e um `return 0`: nao lia
     a fonte, nao montava candidata e nao exercia guarda nenhuma. Um ensaio que
     nao ensaia da' a sensacao de cobertura sem a cobertura.

Nada aqui abre conexao, executa subprocesso ou toca banco: `executor` e
`preflight_fn` sao injetados, e a fonte dos canais e' um dublê.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from pipelines import channel_offer_publisher as pub
from pipelines import channel_offer_sync as cos
from pipelines.ops import orchestrate as orch


# ---------------------------------------------------------------------------
# 1. Capability — declarada, nunca inferida
# ---------------------------------------------------------------------------

def test_todos_os_steps_do_pma_refresh_declaram_o_modo_ensaio():
    for step in orch.PIPELINES["pma_refresh"]:
        assert step.supports_dry_run, f"{step.name} sem `dry_run_args`"


def test_nenhum_dry_run_args_carrega_apply():
    for step in orch.PIPELINES["pma_refresh"]:
        assert "--apply" not in (step.dry_run_args or ()), step.name


def test_os_args_de_producao_continuam_com_apply_nos_tres_canais():
    """O contrato do apply NAO muda: o ensaio e' um modo novo, nao um
    reescrever do antigo."""
    for nome in orch.PMA_CANAIS:
        step = next(s for s in orch.PIPELINES["pma_refresh"] if s.name == nome)
        assert "--apply" in step.args, nome


@pytest.mark.parametrize("nome", ["full_daily", "serving_refresh",
                                  "shopee_manual_refresh"])
def test_os_pipelines_antigos_NAO_declaram_ensaio(nome):
    """Habilitar o que nao foi verificado seria pior que nao habilitar."""
    assert any(not s.supports_dry_run for s in orch.PIPELINES[nome])
    with pytest.raises(orch.DryRunUnsupportedError):
        orch.assert_dry_run_supported(nome)


def test_o_pma_refresh_e_aceito():
    orch.assert_dry_run_supported("pma_refresh")


def test_a_recusa_acontece_ANTES_de_qualquer_subprocesso():
    tocou = []

    def executor(step, **k):
        tocou.append(step.name)
        return 0

    with pytest.raises(orch.DryRunUnsupportedError):
        orch.run_pipeline("full_daily", executor=executor,
                          preflight_fn=lambda _f: (True, []), dry_run=True)
    assert tocou == [], "nenhum step pode ter sido executado"


def test_nao_existe_fallback_para_apply_apos_a_recusa():
    """Recusado e' recusado: nao cai para o modo normal."""
    chamados = []

    def executor(step, **k):
        chamados.append((step.name, k.get("dry_run")))
        return 0

    with pytest.raises(orch.DryRunUnsupportedError):
        orch.run_pipeline("shopee_manual_refresh", executor=executor,
                          preflight_fn=lambda _f: (True, []), dry_run=True)
    assert chamados == []


def test_o_executor_recusa_step_sem_capability_mesmo_se_chamado_direto():
    """Defesa em profundidade: se um caminho novo escapar da checagem de
    pipeline, o executor para em vez de usar os args de producao."""
    step = next(s for s in orch.PIPELINES["full_daily"]
                if not s.supports_dry_run)
    with pytest.raises(orch.DryRunUnsupportedError):
        orch._default_executor(step, dry_run=True)


# ---------------------------------------------------------------------------
# 2. O comando que cada step recebe
# ---------------------------------------------------------------------------

def _captura(dry_run: bool):
    recebidos = []

    def executor(step, **k):
        args = step.dry_run_args if k.get("dry_run") else step.args
        recebidos.append((step.name, tuple(args)))
        return 0

    orch.run_pipeline("pma_refresh", executor=executor,
                      preflight_fn=lambda _f: (True, []), dry_run=dry_run)
    return dict(recebidos), [n for n, _ in recebidos]


def test_no_ensaio_nenhum_step_recebe_apply():
    recebidos, _ = _captura(dry_run=True)
    for nome, args in recebidos.items():
        assert "--apply" not in args, f"{nome} recebeu --apply no ensaio"


def test_no_modo_normal_os_canais_continuam_recebendo_apply():
    recebidos, _ = _captura(dry_run=False)
    for nome in orch.PMA_CANAIS:
        assert "--apply" in recebidos[nome], nome


def test_a_ordem_e_a_mesma_nos_dois_modos():
    _, ordem_ensaio = _captura(dry_run=True)
    _, ordem_normal = _captura(dry_run=False)
    assert ordem_ensaio == ordem_normal == [
        "pma_ml", "pma_shopee", "pma_tiktok", "health_check"]


def test_o_health_check_continua_por_ultimo_no_ensaio():
    _, ordem = _captura(dry_run=True)
    assert ordem[-1] == "health_check"


def test_o_ml_mantem_o_lookback_no_ensaio():
    """Sem lookback, o ensaio mediria uma janela diferente da que o apply
    publicaria — e deixaria de ser ensaio."""
    recebidos, _ = _captura(dry_run=True)
    assert recebidos["pma_ml"] == ("--lookback-days",
                                   str(orch.PMA_ML_LOOKBACK_DAYS))


def test_o_ensaio_nao_expande_o_lookback():
    assert orch.PMA_ML_LOOKBACK_DAYS == 3, (
        "o backfill de 7 dias e' operacao SEPARADA e nao entra no pipeline")


# ---------------------------------------------------------------------------
# 3. Exit code no modo ensaio
# ---------------------------------------------------------------------------

def _resultado(ml, shopee, tiktok, health="SUCCESS"):
    return {"pma_ml": ml, "pma_shopee": shopee, "pma_tiktok": tiktok,
            "health_check": health}


def _exit(r):
    return orch.exit_code_do_pipeline(
        "pma_refresh", r, orch.compute_overall_status("pma_refresh", r))


def test_tres_candidatas_validas_saem_zero():
    assert _exit(_resultado("SUCCESS", "SUCCESS", "SUCCESS")) == 0


@pytest.mark.parametrize("status", ["FAILED", "REFUSED", "LOCKED",
                                    "INDETERMINATE", "BLOCKED"])
@pytest.mark.parametrize("posicao", [0, 1, 2])
def test_qualquer_canal_invalido_sai_diferente_de_zero(status, posicao):
    valores = ["SUCCESS", "SUCCESS", "SUCCESS"]
    valores[posicao] = status
    assert _exit(_resultado(*valores)) == 1


def test_zero_candidata_valida_nunca_sai_zero():
    assert _exit(_resultado("FAILED", "REFUSED", "LOCKED")) == 1


def test_indeterminate_mantem_precedencia_no_ensaio():
    r = _resultado("INDETERMINATE", "SUCCESS", "SUCCESS")
    assert orch.compute_overall_status("pma_refresh", r) == "INDETERMINATE"
    assert _exit(r) == 1


def test_o_health_check_nao_conta_como_publicacao():
    assert _exit(_resultado("SUCCESS", "SUCCESS", "SUCCESS",
                            health="FAILED")) == 0


# ---------------------------------------------------------------------------
# 4. O log diz o modo, e nunca diz "publicado"
# ---------------------------------------------------------------------------

def test_o_log_do_ensaio_identifica_o_modo(capsys):
    orch.run_pipeline("pma_refresh", executor=lambda s, **k: 0,
                      preflight_fn=lambda _f: (True, []), dry_run=True)
    saida = capsys.readouterr().out
    assert "mode=dry_run" in saida


def test_o_log_do_ensaio_nunca_afirma_publicacao(capsys):
    orch.run_pipeline("pma_refresh", executor=lambda s, **k: 0,
                      preflight_fn=lambda _f: (True, []), dry_run=True)
    saida = capsys.readouterr().out.lower()
    for proibido in ("publicado", "publicada", "published"):
        assert proibido not in saida, f"o ensaio afirmou {proibido!r}"


def test_o_modo_normal_nao_e_rotulado_como_ensaio(capsys):
    orch.run_pipeline("pma_refresh", executor=lambda s, **k: 0,
                      preflight_fn=lambda _f: (True, []), dry_run=False)
    assert "mode=dry_run" not in capsys.readouterr().out


def test_main_recusa_ensaio_em_pipeline_incompativel(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv",
                        ["orchestrate", "--pipeline", "full_daily", "--dry-run"])
    monkeypatch.setitem(sys.modules, "dotenv", type(sys)("dotenv"))
    sys.modules["dotenv"].load_dotenv = lambda **_k: None
    assert orch.main() == 1
    assert "RECUSADO" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 5. O diagnostico dos canais monta a MESMA candidata do apply
# ---------------------------------------------------------------------------

AGORA = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)


def _linhas_shopee():
    return [
        {"is_model": False, "has_model": False, "shop_account": "apice",
         "brand": "apice", "item_id": 1, "seller_sku": "A-1", "gtin": None,
         "listing_title": "Serum", "is_kit": False, "current_price": "10.00",
         "original_price": None, "promotion_id": None, "discount_pct": None,
         "ingested_at": AGORA, "is_active": True},
        # Pai COM variacao: container, nunca vira oferta. Preco proprio NULO.
        {"is_model": False, "has_model": True, "shop_account": "apice",
         "brand": "apice", "item_id": 2, "seller_sku": "A-2", "gtin": None,
         "listing_title": "Kit", "is_kit": True, "current_price": None,
         "original_price": None, "promotion_id": None, "discount_pct": None,
         "ingested_at": AGORA, "is_active": True},
        # A variacao dele, que carrega o preco.
        {"is_model": True, "has_model": True, "shop_account": "apice",
         "brand": "apice", "item_id": 2, "model_id": 20, "seller_sku": "A-2-P",
         "gtin": None, "listing_title": "Kit P", "is_kit": True,
         "current_price": "20.00", "original_price": None, "promotion_id": None,
         "discount_pct": None, "ingested_at": AGORA, "is_active": True},
        # Oferta sem preco na origem: `observed_price` fica NULO, nunca 0.
        {"is_model": False, "has_model": False, "shop_account": "apice",
         "brand": "apice", "item_id": 3, "seller_sku": "A-3", "gtin": None,
         "listing_title": "Sem preco", "is_kit": False, "current_price": None,
         "original_price": None, "promotion_id": None, "discount_pct": None,
         "ingested_at": AGORA, "is_active": True},
    ]


class FonteFake:
    """Dublê de conexao READ-ONLY. Nao tem cursor gravavel nem commit."""

    def __init__(self):
        self.fechada = False

    def close(self):
        self.fechada = True


@pytest.fixture
def fonte(monkeypatch):
    relogios = {"apice": cos.AccountClock(
        marketplace="shopee", account="apice", watermark_at=AGORA, rows_seen=4)}
    monkeypatch.setattr(cos, "load_internal_catalog", lambda c: {})
    monkeypatch.setattr(cos, "load_shopee_account_clocks", lambda c: relogios)
    monkeypatch.setattr(cos, "fetch_shopee_offers",
                        lambda c, cat=None: _linhas_shopee())
    return FonteFake()


def _args(marketplace="shopee"):
    return pub.build_cli().parse_args(["--marketplace", marketplace])


def test_o_diagnostico_monta_a_candidata_de_verdade(fonte):
    rel = pub.run_diagnose(_args(), connect_source=lambda: fonte)
    assert rel["mode"] == "dry_run"
    assert rel["rows"] == 3, "1 pai simples + 1 variacao + 1 sem preco"
    assert rel["observed_dates"] == ["2026-09-22"]
    assert rel["accounts"] == ["apice"]
    assert fonte.fechada, "a conexao de fonte precisa ser fechada"


def test_a_candidata_do_ensaio_e_IGUAL_a_do_apply(fonte, monkeypatch):
    """Prova de equivalencia: as duas saem de `collect_snapshot`.

    Se alguem reimplementar as formulas num dos caminhos, este teste quebra.
    """
    rel = pub.run_diagnose(_args(), connect_source=lambda: fonte)

    gate = pub.SourceGate()
    gate.mark_locked()
    registros, _, _ = pub.collect_snapshot(gate, FonteFake(), "shopee")
    assert pub.candidate_fingerprint(registros) == rel["fingerprint"]
    assert len(registros) == rel["rows"]


def test_o_diagnostico_nao_toma_lock_nem_abre_auditoria(fonte, monkeypatch):
    proibidos = []
    for nome in ("try_acquire_publication_lock", "release_publication_lock"):
        monkeypatch.setattr(cos, nome,
                            lambda *a, _n=nome, **k: proibidos.append(_n))
    for nome in ("audit_start", "audit_finish"):
        monkeypatch.setattr(pub, nome,
                            lambda *a, _n=nome, **k: proibidos.append(_n))
    pub.run_diagnose(_args(), connect_source=lambda: fonte)
    assert proibidos == [], f"o ensaio chamou {proibidos}"


def test_o_diagnostico_nao_monta_plano_nem_publica(fonte, monkeypatch):
    tocou = []
    monkeypatch.setattr(cos, "build_publication_plan",
                        lambda *a, **k: tocou.append("plan"))
    monkeypatch.setattr(pub, "run_apply", lambda *a, **k: tocou.append("apply"))
    pub.run_diagnose(_args(), connect_source=lambda: fonte)
    assert tocou == []


def test_o_gate_de_diagnostico_recusa_lock_e_publicacao():
    g = pub.SourceGate.for_diagnose()
    with pytest.raises(pub.PublisherError):
        g.mark_locked()
    with pytest.raises(pub.PublisherError):
        g.assert_publishable()
    assert g.read("x", lambda: "ok") == "ok", "leitura e' o que ele PERMITE"


def test_o_gate_normal_continua_exigindo_o_lock_antes_de_ler():
    g = pub.SourceGate()
    with pytest.raises(pub.SourceReadBeforeLockError):
        g.read("x", lambda: "ok")


def test_o_apply_recusa_um_gate_de_diagnostico():
    g = pub.SourceGate.for_diagnose()
    with pytest.raises(pub.PublisherError):
        g.assert_publishable()


# ---------------------------------------------------------------------------
# 6. Preco ausente NUNCA vira zero — o contrato dos 337
# ---------------------------------------------------------------------------

def test_preco_ausente_permanece_nulo_na_candidata(fonte):
    rel = pub.run_diagnose(_args(), connect_source=lambda: fonte)
    assert rel["prices_absent"] == 1, "a oferta sem preco continua sem preco"
    assert rel["prices_zero"] == 0, "nulo NUNCA vira zero"


def test_o_container_nao_entra_na_candidata_e_seu_nulo_nao_e_contado(fonte):
    """O nucleo da inconsistencia do PMA-2C5C.

    `stg_shopee_products` tem pais COM variacao, cujo preco vive na variacao e
    cujo campo proprio e' NULO. Eles sao CONTAINERS: a regra
    `if not row["is_model"] and row.get("has_model"): continue` os exclui, e o
    preco nulo deles nunca chega a ser contado.

    Por isso "337 ausentes na fonte" e "0 ausentes na candidata" nao se
    contradizem: sao POPULACOES diferentes. A fonte conta pais; a candidata
    conta pai-sem-variacao XOR variacao.
    """
    rel = pub.run_diagnose(_args(), connect_source=lambda: fonte)
    # 4 linhas na fonte, 1 delas container -> 3 na candidata.
    assert rel["rows"] == 3
    # O container tinha preco NULO e nao aparece na contagem de ausentes.
    assert rel["prices_absent"] == 1, (
        "so' a oferta REAL sem preco conta; o container nao")


def test_o_fingerprint_distingue_nulo_de_zero():
    """Um fingerprint que igualasse os dois nao detectaria a troca que mais
    importa."""
    base = {"offer_key": "k", "observed_price": None}
    zero = {"offer_key": "k", "observed_price": 0}
    assert (pub.candidate_fingerprint([base])
            != pub.candidate_fingerprint([zero]))


def test_o_resumo_separa_ausente_de_zero_em_campos_distintos(fonte):
    rel = pub.run_diagnose(_args(), connect_source=lambda: fonte)
    assert "prices_absent" in rel and "prices_zero" in rel, (
        "somar um no outro apagaria a distincao que o contrato preserva")


# ---------------------------------------------------------------------------
# 7. Higiene do resumo
# ---------------------------------------------------------------------------

def test_o_resumo_nao_carrega_linha_de_oferta(fonte):
    rel = pub.run_diagnose(_args(), connect_source=lambda: fonte)
    texto = repr(rel)
    for vazamento in ("A-1", "A-2-P", "Serum", "listing_title", "seller_sku"):
        assert vazamento not in texto, f"o resumo vazou {vazamento!r}"


def test_o_diagnostico_roda_as_guardas_de_pre_publicacao(fonte, monkeypatch):
    chamadas = []
    original_pii = cos.assert_no_pii
    original_chaves = cos.assert_offer_keys_unique
    monkeypatch.setattr(cos, "assert_no_pii",
                        lambda r: (chamadas.append("pii"), original_pii(r))[1])
    monkeypatch.setattr(cos, "assert_offer_keys_unique",
                        lambda r: (chamadas.append("chaves"),
                                   original_chaves(r))[1])
    pub.run_diagnose(_args(), connect_source=lambda: fonte)
    assert chamadas == ["pii", "chaves"]


def _explode(exc):
    def _f(*a, **k):
        raise exc
    return _f


def test_fonte_indisponivel_produz_exit_de_recusa(monkeypatch):
    """Fail-closed: um ensaio que nao conseguiu ler NAO pode sair 0."""
    monkeypatch.setattr(pub, "run_diagnose",
                        _explode(cos.ChannelSyncError("fonte indisponivel")))
    assert pub.main(["--marketplace", "shopee"]) == pub.EXIT_REFUSED


def test_erro_de_contrato_no_ensaio_produz_exit_de_falha(monkeypatch):
    monkeypatch.setattr(pub, "run_diagnose", _explode(ValueError("grao roto")))
    assert pub.main(["--marketplace", "shopee"]) == pub.EXIT_FAILED
