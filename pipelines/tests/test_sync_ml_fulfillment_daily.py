"""Gate FULL-1A/-1A-R — contrato do sync de marts.fact_ml_fulfillment_daily.

Cobrem as invariantes do gate e as fixtures reconciliadas de agosto/2026.
Nenhum teste toca banco: tudo aqui e' funcao pura sobre `SourceSnapshot`.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipelines import sync_ml_fulfillment_daily as mod
from pipelines.sync_ml_fulfillment_daily import (
    ADVISORY_LOCK_KEY,
    BACKFILL_DAYS_BACK,
    FULL_SOURCE_COMPLETE_FROM_DATE,
    CLASSE_FULL,
    CLASSE_NON_FULL,
    CLASSE_UNKNOWN,
    INCREMENTAL_DAYS_BACK,
    MODE_AUTO,
    MODE_BACKFILL,
    MODE_FULL,
    MODE_INCREMENTAL,
    ROTULO_FULL,
    SENTINELA_LT,
    ConcurrentRunError,
    MLFulfillmentSyncError,
    Row,
    SourceSnapshot,
    SourceUnavailableError,
    Window,
    audit_sources_for_mode,
    resolve_window,
    sanitizar,
    summarize,
    validate_contract,
)

AGORA = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def mk_row(**kw) -> Row:
    """Linha valida por padrao: 9 pagos + 1 cancelado = 10 elegiveis."""
    base = dict(ref_date=date(2026, 8, 1), brand="barbours",
                fulfillment_class=CLASSE_FULL,
                logistic_type_original=ROTULO_FULL,
                eligible_orders=10, paid_orders=9, cancelled_orders=1,
                other_orders=0, paid_gmv=Decimal("1000"), paid_units=9)
    base.update(kw)
    return Row(**base)


PREFLIGHT_OK = {"pedidos": 10, "pedidos_pagos": 9, "envios_duplicados": 0,
                "pedidos_sem_line_item": 0, "pedidos_alocacao_divergente": 0,
                "status_distintos": 2}


def mk_snapshot(rows, preflight=None, window=None, source_alive=True):
    return SourceSnapshot(
        window=window or Window(date(2026, 8, 1), date(2026, 8, 31)),
        rows=list(rows),
        preflight=dict(preflight if preflight is not None else PREFLIGHT_OK),
        source_alive=source_alive,
    )


# ---------------------------------------------------------------------------
# Janela e modos
# ---------------------------------------------------------------------------


def test_janela_incremental_cobre_p99_de_cancelamento():
    """15 dias nao e' numero redondo: e' o p99 medido (14,62 dias)."""
    assert INCREMENTAL_DAYS_BACK == 15
    w = resolve_window(MODE_INCREMENTAL, AGORA)
    assert w.date_to == date(2026, 9, 14)          # D-1, nunca D0
    assert w.days == INCREMENTAL_DAYS_BACK + 1
    assert w.date_from == date(2026, 8, 30)


def test_janela_backfill_cobre_o_maximo_observado():
    """45 dias cobre os 44,12 do pior caso medido."""
    assert BACKFILL_DAYS_BACK == 45
    w = resolve_window(MODE_BACKFILL, AGORA)
    assert (w.date_to - w.date_from).days == 45


def test_janela_nunca_publica_o_dia_corrente():
    for modo in (MODE_INCREMENTAL, MODE_BACKFILL, MODE_FULL):
        assert resolve_window(modo, AGORA).date_to == date(2026, 9, 14)


def test_janela_explicita_recusa_dia_aberto():
    with pytest.raises(ValueError):
        resolve_window(MODE_INCREMENTAL, AGORA,
                       date_from=date(2026, 9, 1), date_to=date(2026, 9, 15))


def test_janela_explicita_exige_os_dois_limites():
    with pytest.raises(MLFulfillmentSyncError):
        resolve_window(MODE_INCREMENTAL, AGORA, date_from=date(2026, 9, 1))


def test_janela_invertida_falha():
    with pytest.raises(MLFulfillmentSyncError):
        Window(date(2026, 8, 31), date(2026, 8, 1))


def test_backfill_e_full_registram_obrigacao_duravel_propria():
    assert len(audit_sources_for_mode(MODE_BACKFILL)) == 2
    assert len(audit_sources_for_mode(MODE_FULL)) == 2
    assert len(audit_sources_for_mode(MODE_INCREMENTAL)) == 1


def test_auditoria_recusa_o_modo_auto():
    """Registrar 'auto' gravaria a intencao, nao o trabalho feito."""
    with pytest.raises(MLFulfillmentSyncError, match="modo efetivo"):
        audit_sources_for_mode(MODE_AUTO)


# ---------------------------------------------------------------------------
# Classificacao: tres estados, cinco rotulos historicos
# ---------------------------------------------------------------------------


def test_envio_ausente_vira_unknown_nunca_non_full():
    s = mk_snapshot([mk_row(fulfillment_class=CLASSE_UNKNOWN,
                            logistic_type_original=SENTINELA_LT,
                            unmatched_orders=10)])
    validate_contract(s)
    assert summarize(s)["unknown_orders"] == 10


def test_unknown_fica_fora_do_denominador_dos_shares():
    """`unknown` nao e' Full nem nao-Full.

    Inclui-lo no denominador faria uma LACUNA DE DADO parecer queda operacional
    do Full -- o share cairia sem que nada tivesse piorado na operacao.
    """
    s = mk_snapshot([
        mk_row(fulfillment_class=CLASSE_FULL, paid_gmv=Decimal("800"),
               paid_orders=8, paid_units=8, eligible_orders=8,
               cancelled_orders=0),
        mk_row(fulfillment_class=CLASSE_NON_FULL,
               logistic_type_original="cross_docking",
               paid_gmv=Decimal("200"), paid_orders=2, paid_units=2,
               eligible_orders=2, cancelled_orders=0),
        mk_row(fulfillment_class=CLASSE_UNKNOWN,
               logistic_type_original=SENTINELA_LT,
               paid_gmv=Decimal("1000"), paid_orders=10, paid_units=10,
               eligible_orders=10, cancelled_orders=0, unmatched_orders=10),
    ])
    r = summarize(s)
    assert r["share_full_gmv"] == 0.8      # 800/1000, nao 800/2000
    assert r["share_full_orders"] == 0.8
    assert r["share_full_units"] == 0.8
    # O total absoluto continua cobrindo as tres classes.
    assert r["paid_gmv_total"] == Decimal("2000")


def test_unknown_com_rotulo_real_e_recusado():
    s = mk_snapshot([mk_row(fulfillment_class=CLASSE_UNKNOWN,
                            logistic_type_original="cross_docking")])
    with pytest.raises(MLFulfillmentSyncError, match="incoerentes"):
        validate_contract(s)


def test_classe_full_com_outro_rotulo_e_recusada():
    s = mk_snapshot([mk_row(logistic_type_original="cross_docking")])
    with pytest.raises(MLFulfillmentSyncError, match="classe full"):
        validate_contract(s)


def test_pedido_sem_envio_nao_pode_ser_classificado_como_full():
    s = mk_snapshot([mk_row(unmatched_orders=3)])
    with pytest.raises(MLFulfillmentSyncError, match="ausencia de envio"):
        validate_contract(s)


@pytest.mark.parametrize("rotulo", ["cross_docking", "xd_drop_off",
                                    "self_service", "drop_off"])
def test_os_cinco_tipos_historicos_permanecem_sem_aviso(rotulo):
    """Serie longa: os quatro extintos sao CONHECIDOS, so' nao sao mais emitidos."""
    s = mk_snapshot([mk_row(fulfillment_class=CLASSE_NON_FULL,
                            logistic_type_original=rotulo)])
    assert validate_contract(s) == []


def test_tipo_logistico_novo_vira_non_full_e_avisa_sem_bloquear():
    """O dia em que o ML criar uma modalidade nao pode derrubar a carga."""
    s = mk_snapshot([mk_row(fulfillment_class=CLASSE_NON_FULL,
                            logistic_type_original="futuro_desconhecido")])
    assert any("nao catalogada" in a for a in validate_contract(s))
    assert summarize(s)["logistic_types"] == ["futuro_desconhecido"]


def test_classe_invalida_bloqueia():
    with pytest.raises(MLFulfillmentSyncError, match="classe desconhecida"):
        validate_contract(mk_snapshot([mk_row(fulfillment_class="talvez")]))


# ---------------------------------------------------------------------------
# Populacoes: todo status tem casa
# ---------------------------------------------------------------------------


def test_populacoes_fecham_exatamente_nenhum_status_some():
    """`<=` deixaria status novo num limbo silencioso. Aqui a soma e' EXATA."""
    s = mk_snapshot([mk_row(eligible_orders=10, paid_orders=7,
                            cancelled_orders=2, other_orders=1)])
    assert validate_contract(s) == []


def test_status_sem_casa_bloqueia():
    s = mk_snapshot([mk_row(eligible_orders=10, paid_orders=7,
                            cancelled_orders=2, other_orders=0)])
    with pytest.raises(MLFulfillmentSyncError, match="sem casa"):
        validate_contract(s)


def test_subpopulacao_maior_que_o_total_bloqueia():
    s = mk_snapshot([mk_row(eligible_orders=5, paid_orders=4,
                            cancelled_orders=3, other_orders=0)])
    with pytest.raises(MLFulfillmentSyncError, match="nao fecham"):
        validate_contract(s)


def test_other_orders_e_somado_e_exposto():
    s = mk_snapshot([mk_row(eligible_orders=10, paid_orders=7,
                            cancelled_orders=2, other_orders=1)])
    assert summarize(s)["other_orders"] == 1


def test_unidade_ausente_e_medida_como_ausencia_nao_como_zero():
    """paid_orders > 0 com paid_units = 0 e' line_item faltando, nao venda de
    zero item."""
    with pytest.raises(MLFulfillmentSyncError, match="discordam"):
        validate_contract(mk_snapshot([mk_row(paid_units=0)]))


def test_divisao_por_zero_retorna_none_nunca_zero():
    s = mk_snapshot([mk_row(paid_orders=0, paid_units=0, paid_gmv=Decimal("0"),
                            eligible_orders=1, cancelled_orders=1)])
    r = summarize(s)
    assert r["share_full_gmv"] is None
    assert r["share_full_orders"] is None
    assert r["share_full_units"] is None


# ---------------------------------------------------------------------------
# Coorte temporal: UMA ref_date
# ---------------------------------------------------------------------------


def test_amostra_de_tempo_maior_que_a_coorte_bloqueia():
    """CONTRAPROVA de grao.

    Se `handling_sample_count` exceder os pedidos elegiveis, a medida voltou ao
    grao do ENVIO -- um pack contando varias vezes. Esta e' a guarda que impede
    a regressao que o FULL-1A-R corrigiu.
    """
    s = mk_snapshot([mk_row(eligible_orders=10, handling_sample_count=11,
                            handling_seconds_sum=1000)])
    with pytest.raises(MLFulfillmentSyncError, match="escapou do grao"):
        validate_contract(s)

    s = mk_snapshot([mk_row(eligible_orders=10, delivery_sample_count=99,
                            delivery_seconds_sum=1000)])
    with pytest.raises(MLFulfillmentSyncError, match="escapou do grao"):
        validate_contract(s)


def test_soma_de_tempo_sem_amostra_bloqueia():
    """Sem esta guarda, `sum/count` publicaria tempo medio infinito."""
    s = mk_snapshot([mk_row(handling_sample_count=0, handling_seconds_sum=500)])
    with pytest.raises(MLFulfillmentSyncError, match="soma sem amostra"):
        validate_contract(s)


def test_unknown_nao_pode_ter_amostra_de_tempo():
    """Sem envio nao existe despacho nem entrega."""
    s = mk_snapshot([mk_row(fulfillment_class=CLASSE_UNKNOWN,
                            logistic_type_original=SENTINELA_LT,
                            unmatched_orders=10, handling_sample_count=3,
                            handling_seconds_sum=100)])
    with pytest.raises(MLFulfillmentSyncError, match="sem envio"):
        validate_contract(s)


def test_sql_mede_tempo_a_partir_da_data_do_PEDIDO():
    """CONTRAPROVA estrutural da coorte.

    O SQL tem de subtrair `p.date_created` (pedido), nunca `e.date_created`
    (envio). Esta e' a diferenca entre uma coorte e duas.
    """
    sql = str(mod.SQL_AGREGADO)
    assert "e.date_shipped - p.date_created" in sql
    assert "e.date_delivered - p.date_created" in sql
    assert "e.date_shipped - e.date_created" not in sql
    assert "e.date_delivered - e.date_created" not in sql


def test_nao_existe_mais_consulta_separada_de_tempos():
    """Duas consultas com GROUP BY proprio eram a origem das duas semanticas."""
    assert not hasattr(mod, "SQL_TEMPOS")


def test_agregado_agrupa_apenas_por_ref_date_do_pedido():
    sql = str(mod.SQL_AGREGADO)
    assert "o.date_created::date AS ref_date" in sql
    assert ("GROUP BY ref_date, brand, fulfillment_class, "
            "logistic_type_original") in sql


# ---------------------------------------------------------------------------
# Contraprovas de join e de unidade
# ---------------------------------------------------------------------------


def test_join_sempre_inclui_brand_na_chave():
    """CONTRAPROVA: 17 shipment_id de agosto aparecem em duas marcas.

    Join so' por `shipment_id` duplicaria essas linhas.
    """
    for sql in (str(mod.SQL_AGREGADO), str(mod.SQL_LISTING)):
        assert "e.brand = p.brand AND e.shipment_id = p.shipping_id" in sql


def test_unidade_nunca_vem_de_shipping_items():
    """CONTRAPROVA: `shipping_items` e' grao de ENVIO e infla 11,2% via packs.

    A unica leitura permitida do campo e' contar envios sem item; ele nunca
    pode alimentar `paid_units`.
    """
    sql = str(mod.SQL_AGREGADO)
    assert "jsonb_array_elements" not in sql          # nunca explodido em linhas
    assert "SUM(un) FILTER (WHERE status = :pago)" in sql
    assert "api.ml_order_line_items" in sql


def test_listing_nunca_rateia_total_amount():
    """GMV por listing vem do item, nunca de `total_amount` do pedido."""
    sql = str(mod.SQL_LISTING)
    assert "SUM(l.quantity * l.unit_price)" in sql
    assert "total_amount" not in sql


def test_alocacao_por_listing_que_deixa_de_fechar_bloqueia():
    pf = dict(PREFLIGHT_OK, pedidos_alocacao_divergente=3)
    with pytest.raises(MLFulfillmentSyncError, match="rateio"):
        validate_contract(mk_snapshot([mk_row()], preflight=pf))


def test_envio_duplicado_na_fonte_avisa_mas_nao_bloqueia():
    pf = dict(PREFLIGHT_OK, envios_duplicados=2)
    assert any("duplicado" in a
               for a in validate_contract(mk_snapshot([mk_row()], preflight=pf)))


# ---------------------------------------------------------------------------
# Fonte vazia x fonte indisponivel
# ---------------------------------------------------------------------------


def test_janela_legitimamente_vazia_avisa_e_nao_bloqueia():
    """Fonte saudavel + zero pedidos = medicao, nao falha."""
    avisos = validate_contract(mk_snapshot([]))
    assert any("sem nenhum pedido" in a for a in avisos)
    assert mk_snapshot([]).janela_vazia is True


def test_snapshot_sem_prova_de_fonte_viva_e_recusado():
    """CONTRAPROVA: fonte indisponivel nao pode chegar perto do DELETE."""
    with pytest.raises(SourceUnavailableError, match="fonte viva"):
        validate_contract(mk_snapshot([mk_row()], source_alive=False))


def test_source_unavailable_e_subclasse_mas_tipo_proprio():
    """O tratamento e' oposto ao de janela vazia; precisa ser distinguivel."""
    assert issubclass(SourceUnavailableError, MLFulfillmentSyncError)
    assert SourceUnavailableError is not MLFulfillmentSyncError


# ---------------------------------------------------------------------------
# Fixtures OFICIAIS de agosto/2026 (grao do PEDIDO PAGO, ratificado)
# ---------------------------------------------------------------------------

AGOSTO_GMV_FULL = Decimal("3649773.48")
AGOSTO_GMV_NON_FULL = Decimal("877705.50")
AGOSTO_GMV_TOTAL = Decimal("4527478.98")

AGOSTO_PEDIDOS_PAGOS_FULL = 48411
AGOSTO_PEDIDOS_PAGOS_NON_FULL = 10712
AGOSTO_UNIDADES_FULL = 49980
AGOSTO_UNIDADES_NON_FULL = 10936

AGOSTO_ELEGIVEIS_FULL = 50492
AGOSTO_ELEGIVEIS_NON_FULL = 11217
AGOSTO_ELEGIVEIS_UNKNOWN = 8
AGOSTO_CANCELADOS_FULL = 2060
AGOSTO_CANCELADOS_NON_FULL = 489
#: `partially_refunded`: fora do GMV por contrato canonico da Torre.
AGOSTO_OUTROS_FULL = 21
AGOSTO_OUTROS_NON_FULL = 16


def test_fixture_gmv_oficial():
    assert AGOSTO_GMV_FULL + AGOSTO_GMV_NON_FULL == AGOSTO_GMV_TOTAL
    assert round(AGOSTO_GMV_FULL) == Decimal("3649773")
    assert round(AGOSTO_GMV_NON_FULL) == Decimal("877706")


def test_fixture_share_full_gmv_oficial_8061_pct():
    """KPI PRINCIPAL."""
    share = float(AGOSTO_GMV_FULL) / float(AGOSTO_GMV_TOTAL) * 100
    assert round(share, 2) == 80.61


def test_fixture_share_full_orders_oficial_8188_pct():
    """Grao do PEDIDO PAGO -- ratificado como oficial no FULL-1A-R."""
    total = AGOSTO_PEDIDOS_PAGOS_FULL + AGOSTO_PEDIDOS_PAGOS_NON_FULL
    assert total == 59123
    assert round(AGOSTO_PEDIDOS_PAGOS_FULL / total * 100, 2) == 81.88


def test_fixture_share_full_units_oficial_8205_pct():
    total = AGOSTO_UNIDADES_FULL + AGOSTO_UNIDADES_NON_FULL
    assert total == 60916
    assert round(AGOSTO_UNIDADES_FULL / total * 100, 2) == 82.05


def test_fixture_cancelamento_oficial():
    """TRUNCAMENTO, nao arredondamento: 4,079854 -> 4,0798."""
    def trunc4(x):
        return int(x * 10_000) / 10_000
    full = AGOSTO_CANCELADOS_FULL / AGOSTO_ELEGIVEIS_FULL * 100
    nf = AGOSTO_CANCELADOS_NON_FULL / AGOSTO_ELEGIVEIS_NON_FULL * 100
    assert trunc4(full) == 4.0798
    assert trunc4(nf) == 4.3594
    assert round(nf, 2) == 4.36


def test_fixture_cancelamento_nao_fabrica_os_426_da_planilha():
    """A planilha mostra 4,26%; o Data Mart produz 4,36%. Divergencia ABERTA."""
    nf = AGOSTO_CANCELADOS_NON_FULL / AGOSTO_ELEGIVEIS_NON_FULL * 100
    assert round(nf, 2) != 4.26


def test_fixture_populacoes_de_agosto_fecham_exatamente():
    """Os 21 e 16 que faltavam sao `partially_refunded`, agora com coluna."""
    assert (AGOSTO_PEDIDOS_PAGOS_FULL + AGOSTO_CANCELADOS_FULL
            + AGOSTO_OUTROS_FULL == AGOSTO_ELEGIVEIS_FULL)
    assert (AGOSTO_PEDIDOS_PAGOS_NON_FULL + AGOSTO_CANCELADOS_NON_FULL
            + AGOSTO_OUTROS_NON_FULL == AGOSTO_ELEGIVEIS_NON_FULL)
    assert (AGOSTO_ELEGIVEIS_FULL + AGOSTO_ELEGIVEIS_NON_FULL
            + AGOSTO_ELEGIVEIS_UNKNOWN) == 61717


# --- Numeros HISTORICOS do grao do ENVIO. NAO sao KPI, NAO sao comparaveis. --

AGOSTO_ENVIOS_FULL = 47872
AGOSTO_ENVIOS_NON_FULL = 11100
AGOSTO_UNIDADES_FULL_ENVIO = 52170
AGOSTO_UNIDADES_NON_FULL_ENVIO = 11467


def test_numeros_historicos_do_grao_do_envio_nao_sao_kpi():
    """81,18% e 81,98% existem so' como procedencia documentada.

    Sao medidos no grao do ENVIO, populacao diferente da do GMV. Nenhum teste
    exige que o codigo os reproduza -- faze-lo obrigaria o join multiplicador.
    """
    env_ped = AGOSTO_ENVIOS_FULL / (AGOSTO_ENVIOS_FULL + AGOSTO_ENVIOS_NON_FULL)
    env_un = (AGOSTO_UNIDADES_FULL_ENVIO
              / (AGOSTO_UNIDADES_FULL_ENVIO + AGOSTO_UNIDADES_NON_FULL_ENVIO))
    assert round(env_ped * 100, 2) == 81.18
    assert round(env_un * 100, 2) == 81.98
    # E sao DIFERENTES dos oficiais: divergencia de grao, nao erro.
    oficial_ped = AGOSTO_PEDIDOS_PAGOS_FULL / 59123
    assert round(env_ped * 100, 2) != round(oficial_ped * 100, 2)


def test_unidade_por_shipping_items_inflaria_via_packs():
    """CONTRAPROVA: 67.725 pelo caminho errado, 60.916 pelo certo."""
    inflacao = 67725 / (AGOSTO_UNIDADES_FULL + AGOSTO_UNIDADES_NON_FULL) - 1
    assert round(inflacao * 100, 1) == 11.2


# ---------------------------------------------------------------------------
# Cobertura de handling e delivery medida em agosto (coorte do pedido)
# ---------------------------------------------------------------------------

AGOSTO_HANDLING_AMOSTRA = {"full": 49117, "non_full": 10887}
AGOSTO_DELIVERY_AMOSTRA = {"full": 48728, "non_full": 10772}


@pytest.mark.parametrize("classe,elegiveis", [
    ("full", AGOSTO_ELEGIVEIS_FULL), ("non_full", AGOSTO_ELEGIVEIS_NON_FULL)])
def test_amostras_de_agosto_cabem_na_coorte(classe, elegiveis):
    """Prova empirica de que a medida ficou no grao do pedido."""
    assert AGOSTO_HANDLING_AMOSTRA[classe] <= elegiveis
    assert AGOSTO_DELIVERY_AMOSTRA[classe] <= elegiveis
    assert AGOSTO_DELIVERY_AMOSTRA[classe] < AGOSTO_HANDLING_AMOSTRA[classe]


def test_cobertura_das_amostras_de_agosto():
    """Censura, nao ausencia de dado: ~3% ainda nao despacharam/entregaram."""
    cob_h = AGOSTO_HANDLING_AMOSTRA["full"] / AGOSTO_ELEGIVEIS_FULL * 100
    cob_d = AGOSTO_DELIVERY_AMOSTRA["full"] / AGOSTO_ELEGIVEIS_FULL * 100
    assert round(cob_h, 1) == 97.3
    assert round(cob_d, 1) == 96.5


# ---------------------------------------------------------------------------
# Sanitizacao e lock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bruto", [
    "falha em postgresql://user:senha@10.0.0.5:5432/datamart",
    "connection to host=meu-host.rds.amazonaws.com failed",
    "auth failed password=segredo123",
    "timeout ao conectar em 192.168.15.22",
])
def test_sanitizacao_remove_segredo(bruto):
    limpo = sanitizar(bruto)
    for proibido in ("senha", "10.0.0.5", "meu-host", "segredo123",
                     "192.168.15.22", "postgresql://"):
        assert proibido not in limpo
    assert "[REDACTED]" in limpo


def test_sanitizacao_limita_tamanho():
    assert len(sanitizar("x" * 5000)) <= 500


def test_lock_e_de_sessao():
    """O lock precisa sobreviver ao fim de cada statement.

    Um lock TRANSACIONAL (`pg_advisory_xact_lock`) prenderia a transacao do Neon
    durante a leitura do Data Mart -- ate' 600 s de `idle in transaction`.

    Este teste exige a PROPRIEDADE (lock de sessao), nao uma funcao especifica:
    a versao anterior afirmava `"pg_advisory_lock" in SQL_LOCK` e, com isso,
    travava justamente a variante bloqueante que o FULL-1B reprovou.
    """
    sql = str(mod.SQL_TRY_LOCK)
    assert "_xact_" not in sql, "lock transacional prende a transacao do Neon"
    assert "pg_advisory_unlock" in str(mod.SQL_UNLOCK)
    assert ADVISORY_LOCK_KEY == 916140016


def test_lock_e_fail_fast():
    """A aquisicao NAO pode bloquear.

    `pg_advisory_lock` espera indefinidamente: nao ha `lock_timeout` nem na
    conexao de lock, nem no engine, nem no DSN. `pg_try_advisory_lock` devolve
    booleano na hora.
    """
    sql = str(mod.SQL_TRY_LOCK)
    assert "pg_try_advisory_lock" in sql
    assert "AS obtido" in sql, "o retorno precisa ser nomeado para ser lido"


def test_nenhuma_chamada_executavel_ao_lock_bloqueante():
    """CONTRAPROVA estrutural, por AST -- nao por grep.

    O docstring do modulo MENCIONA `pg_advisory_lock` para explicar por que ele
    nao e' usado. Um grep ingenuo confundiria a explicacao com a chamada. Aqui
    so' contam literais de codigo: docstrings de modulo, classe e funcao ficam
    de fora.
    """
    import ast
    import inspect

    fonte = inspect.getsource(mod)
    arvore = ast.parse(fonte)
    docstrings = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)):
            d = ast.get_docstring(no, clean=False)
            if d:
                docstrings.add(d)

    bloqueantes = [
        no.value for no in ast.walk(arvore)
        if isinstance(no, ast.Constant) and isinstance(no.value, str)
        and no.value not in docstrings
        and "pg_advisory_lock(" in no.value
    ]
    assert not bloqueantes, f"lock bloqueante em codigo: {bloqueantes}"
    assert not hasattr(mod, "SQL_LOCK"), \
        "SQL_LOCK bloqueante deveria ter sido removido"


def test_excecao_de_concorrencia_e_tipo_proprio():
    """A acao do operador e' diferente: esperar, nao investigar dado."""
    assert issubclass(ConcurrentRunError, MLFulfillmentSyncError)
    assert ConcurrentRunError is not MLFulfillmentSyncError


def _codigo_sem_prosa() -> str:
    """Fonte do modulo sem comentarios nem strings.

    As docstrings EXPLICAM que nao ha retry ("sem espera, sem retry"), entao um
    grep pela palavra acusaria a propria explicacao. Aqui os tokens de
    comentario e de string sao descartados: sobra so' o que executa.
    """
    import inspect
    import io as _io
    import tokenize

    fonte = inspect.getsource(mod)
    pedacos = []
    for tok in tokenize.generate_tokens(_io.StringIO(fonte).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        pedacos.append(tok.string)
    return " ".join(pedacos)


def test_nenhum_sleep_ou_espera_no_codigo():
    """CONTRAPROVA: fail-fast nao pode virar espera disfarcada."""
    codigo = _codigo_sem_prosa()
    for proibido in ("sleep", "wait", "while True"):
        assert proibido not in codigo, f"encontrado {proibido!r} em codigo"


def test_nenhum_retry_no_codigo():
    """CONTRAPROVA: lock negado nao pode ser repetido automaticamente."""
    codigo = _codigo_sem_prosa()
    for proibido in ("retry", "retries", "tentativa", "backoff"):
        assert proibido not in codigo, f"encontrado {proibido!r} em codigo"


def test_o_helper_de_prosa_realmente_remove_strings():
    """Guarda do proprio guarda.

    Se `_codigo_sem_prosa` parasse de remover strings, os dois testes acima
    passariam a acusar a documentacao e alguem os removeria por "flaky".
    """
    codigo = _codigo_sem_prosa()
    assert "pg_try_advisory_lock" not in codigo   # vive dentro de uma string
    assert "SQL_TRY_LOCK" in codigo               # o identificador permanece


def test_conexao_de_lock_usa_autocommit():
    """Abrir transacao so' para segurar o lock recriaria o problema."""
    fonte = inspect.getsource(mod._default_lock_connection)
    assert 'isolation_level="AUTOCOMMIT"' in fonte


# ---------------------------------------------------------------------------
# Piso historico do modo full — Gate FULL-1C-H1
# ---------------------------------------------------------------------------
#
# O `full` comeca em 01/08/2025 porque a FONTE so' tem cobertura integral de
# line items a partir dai. Medido em 15/09/2026 sobre 550.239 pedidos: 1.330
# pedidos PAGOS sem item em mai-jul/2025, zero nos 14 meses seguintes.

#: Ultimo dia medido com pedido pago sem line item. O piso tem de ficar DEPOIS.
ULTIMO_DIA_COM_FALHA_NA_FONTE = date(2025, 7, 27)

#: Pedidos pagos sem item por mes, medidos na fonte.
PAGOS_SEM_ITEM_POR_MES = {"2025-05": 338, "2025-06": 190, "2025-07": 802}


def test_full_comeca_exatamente_em_2025_08_01():
    assert FULL_SOURCE_COMPLETE_FROM_DATE == date(2025, 8, 1)
    assert resolve_window(MODE_FULL, AGORA).date_from == date(2025, 8, 1)


def test_piso_fica_depois_da_ultima_falha_medida_na_fonte():
    """CONTRAPROVA do item 12: reverter para 2025-05-01 reprova aqui.

    O piso nao e' um numero escolhido: e' posterior ao ultimo dia em que a fonte
    falhou. Baixa-lo sem reparar a fonte faz o `full` voltar a quebrar na
    primeira execucao -- exatamente o que o FULL-1C encontrou.
    """
    assert FULL_SOURCE_COMPLETE_FROM_DATE > ULTIMO_DIA_COM_FALHA_NA_FONTE
    # E o valor antigo NAO satisfaz a invariante.
    assert not date(2025, 5, 1) > ULTIMO_DIA_COM_FALHA_NA_FONTE


def test_2025_07_31_fica_fora_da_janela_full():
    w = resolve_window(MODE_FULL, AGORA)
    assert date(2025, 7, 31) < w.date_from


def test_2025_08_01_fica_dentro_da_janela_full():
    w = resolve_window(MODE_FULL, AGORA)
    assert w.date_from <= date(2025, 8, 1) <= w.date_to


def test_meses_incompletos_ficam_fora_da_populacao_publicada():
    """Mai-jul/2025 sao INDISPONIVEIS, nao meses com zero."""
    w = resolve_window(MODE_FULL, AGORA)
    for mes in ("2025-05", "2025-06", "2025-07"):
        ano, m = (int(x) for x in mes.split("-"))
        # Nenhum dia desses meses cabe na janela publicada.
        assert date(ano, m, 1) < w.date_from
        assert PAGOS_SEM_ITEM_POR_MES[mes] > 0   # e a razao esta medida


def test_pedido_pago_sem_unidade_ANTES_do_cutoff_nao_chega_a_validacao():
    """Item 4: o dado ruim nao e' tolerado -- ele nao entra na janela.

    A guarda continua bloqueante; o que muda e' que a linha de mai-jul/2025
    nunca e' lida, porque a janela do `full` comeca depois.
    """
    w = resolve_window(MODE_FULL, AGORA)
    assert date(2025, 5, 5) < w.date_from      # o caso real que quebrou o full


def test_pedido_pago_sem_unidade_NO_cutoff_ou_depois_continua_bloqueando():
    """Item 5: a regra NAO foi rebaixada para warning."""
    for dia in (date(2025, 8, 1), date(2026, 8, 1)):
        s = mk_snapshot([mk_row(ref_date=dia, paid_orders=9, paid_units=0)])
        with pytest.raises(MLFulfillmentSyncError, match="discordam"):
            validate_contract(s)


def test_ausencia_de_unidade_nunca_vira_zero():
    """Item 11: nada no modulo converte unidade ausente em zero de venda."""
    s = mk_snapshot([mk_row(paid_orders=1, paid_units=0)])
    with pytest.raises(MLFulfillmentSyncError):
        validate_contract(s)
    # E o caminho inverso tambem e' recusado: unidade sem pedido pago.
    s = mk_snapshot([mk_row(paid_orders=0, paid_units=5, eligible_orders=1,
                            cancelled_orders=1, other_orders=0)])
    with pytest.raises(MLFulfillmentSyncError, match="discordam"):
        validate_contract(s)


def test_incremental_e_backfill_nao_mudaram():
    """Item 6: o hotfix toca SO' o piso do full."""
    assert INCREMENTAL_DAYS_BACK == 15
    assert BACKFILL_DAYS_BACK == 45
    assert resolve_window(MODE_INCREMENTAL, AGORA).date_from == date(2026, 8, 30)
    assert resolve_window(MODE_BACKFILL, AGORA).date_from == date(2026, 7, 31)


def test_teto_d_menos_1_preservado_em_todos_os_modos():
    for modo in (MODE_INCREMENTAL, MODE_BACKFILL, MODE_FULL):
        assert resolve_window(modo, AGORA).date_to == date(2026, 9, 14)


def test_obrigacao_mensal_continua_exigindo_full():
    """Item 8: backfill e full seguem com auditoria propria."""
    assert audit_sources_for_mode(MODE_FULL) == (
        "ml_fulfillment_daily", "ml_fulfillment_daily_full")
    assert audit_sources_for_mode(MODE_BACKFILL) == (
        "ml_fulfillment_daily", "ml_fulfillment_daily_backfill")
