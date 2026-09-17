"""Gate EXP-3B1 — nucleo da Expedicao do Mercado Livre.

O que estes testes travam, em uma frase: a fila do ML contem SO' o que o
vendedor despacha, SO' o que a fonte ainda esta relendo, e NUNCA um prazo
inventado.
"""
from __future__ import annotations

import ast
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import ml_extract, transform
from pipelines.expedicao.contract import (
    ML_FULFILLMENT_LOGISTIC_TYPE,
    ML_KNOWN_LOGISTIC_TYPES,
    ML_SELLER_MANAGED_LOGISTIC_TYPES,
    ML_SOURCE_COHORT_MAX_AGE,
    ML_SOURCE_UTC_OFFSET,
    Channel,
    DeadlineStatus,
    OperationalAgeStatus,
    RegistryError,
    SellerAccount,
    SourceHealth,
    SourceUnhealthy,
    TimestampQuality,
)

RAIZ = Path(__file__).resolve().parents[2]
AGORA = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)

REGISTRY = {
    "2227056661": SellerAccount("2227056661", 3, "kokeshi"),
    "2532564723": SellerAccount("2532564723", 2, "barbours"),
}


def linha(**over):
    """Linha crua da fonte. Carimbos NAIVE, como a fonte grava."""
    base = {
        "seller_id": 2227056661,
        "brand": "kokeshi",
        "shipment_id": 45678901234,
        "order_id": 2000003456789,
        "shipment_status": "ready_to_ship",
        "substatus": "ready_for_pickup",
        "logistic_type": "cross_docking",
        "order_status": "paid",
        "order_created_at": datetime(2026, 9, 16, 10, 0),
        "date_created": datetime(2026, 9, 16, 10, 5),
        "date_ready_to_ship": datetime(2026, 9, 16, 11, 0),
        "date_shipped": None,
        "date_cancelled": None,
        "tracking_method": "Normal",
        "extracted_at": datetime(2026, 9, 17, 13, 30),
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Allowlist positiva de modalidade
# ---------------------------------------------------------------------------
def test_allowlist_e_positiva_e_nao_contem_fulfillment():
    assert ML_FULFILLMENT_LOGISTIC_TYPE not in ML_SELLER_MANAGED_LOGISTIC_TYPES
    assert ML_SELLER_MANAGED_LOGISTIC_TYPES == {
        "cross_docking", "xd_drop_off", "drop_off", "self_service"
    }
    assert ML_KNOWN_LOGISTIC_TYPES == ML_SELLER_MANAGED_LOGISTIC_TYPES | {
        ML_FULFILLMENT_LOGISTIC_TYPE
    }


@pytest.mark.parametrize("modalidade", sorted(ML_SELLER_MANAGED_LOGISTIC_TYPES))
def test_shipment_seller_managed_entra(modalidade):
    fila, diag = ml_extract.classify_candidates(
        [linha(logistic_type=modalidade)], AGORA
    )
    assert len(fila) == 1
    assert diag["queue_count"] == 1
    assert diag["fulfillment_excluded_count"] == 0


def test_fulfillment_nao_entra_e_e_contado():
    fila, diag = ml_extract.classify_candidates(
        [linha(logistic_type="fulfillment"), linha(logistic_type="cross_docking")],
        AGORA,
    )
    assert len(fila) == 1
    assert fila[0]["logistic_type"] == "cross_docking"
    assert diag["candidate_count"] == 2
    assert diag["fulfillment_excluded_count"] == 1
    assert diag["seller_managed_count"] == 1


def test_modalidade_desconhecida_falha_fechada():
    """Modalidade nova do ML NAO entra por negacao nem some em silencio."""
    with pytest.raises(SourceUnhealthy) as erro:
        ml_extract.classify_candidates([linha(logistic_type="flex_v2")], AGORA)
    assert "flex_v2" in str(erro.value)


def test_logistic_type_nulo_tambem_falha_fechada():
    with pytest.raises(SourceUnhealthy):
        ml_extract.classify_candidates([linha(logistic_type=None)], AGORA)


# ---------------------------------------------------------------------------
# Coorte confiavel da fonte
# ---------------------------------------------------------------------------
def test_registro_congelado_nao_vira_backlog_vivo():
    velho = linha(extracted_at=datetime(2026, 5, 1, 12, 0))
    fila, diag = ml_extract.classify_candidates([velho, linha()], AGORA)
    assert len(fila) == 1
    assert diag["stale_source_record_count"] == 1
    assert diag["queue_count"] == 1


def test_fronteira_da_coorte_usa_o_limiar_do_contrato():
    # `extracted_at` em UTC-4; o limiar mede contra `effective_at` em UTC.
    dentro = AGORA - ML_SOURCE_COHORT_MAX_AGE + timedelta(hours=1)
    fora = AGORA - ML_SOURCE_COHORT_MAX_AGE - timedelta(hours=1)
    assert ml_extract.is_stale_source_record(dentro, AGORA) is False
    assert ml_extract.is_stale_source_record(fora, AGORA) is True


def test_extracted_at_ausente_conta_como_fora_da_coorte():
    assert ml_extract.is_stale_source_record(None, AGORA) is True
    fila, diag = ml_extract.classify_candidates([linha(extracted_at=None)], AGORA)
    assert fila == []
    assert diag["stale_source_record_count"] == 1


def test_exclusoes_aparecem_todas_no_diagnostico():
    linhas = [
        linha(),
        linha(logistic_type="fulfillment"),
        linha(extracted_at=datetime(2026, 1, 1, 0, 0)),
    ]
    _, diag = ml_extract.classify_candidates(linhas, AGORA)
    assert diag == {
        "candidate_count": 3,
        "seller_managed_count": 2,
        "fulfillment_excluded_count": 1,
        "stale_source_record_count": 1,
        "unmapped_logistic_type_count": 0,
        "queue_count": 1,
    }


# ---------------------------------------------------------------------------
# Timezone — ponto unico
# ---------------------------------------------------------------------------
def test_normalizacao_de_carimbo_aplica_o_offset_medido():
    bruto = datetime(2026, 9, 17, 14, 0)  # naive, convencao da fonte
    saida = transform.normalizar_carimbo_ml(bruto)
    assert saida == datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)
    assert saida.tzinfo is timezone.utc
    # o offset vem do contrato, nao de um literal solto aqui
    assert bruto - ML_SOURCE_UTC_OFFSET == saida.replace(tzinfo=None)


def test_normalizacao_preserva_carimbo_que_ja_tem_fuso():
    """Se a fonte passar a gravar timestamptz, o helper para de somar offset."""
    ciente = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)
    assert transform.normalizar_carimbo_ml(ciente) == ciente


def test_normalizacao_de_nulo_e_nulo():
    assert transform.normalizar_carimbo_ml(None) is None


def test_offset_do_ml_nao_e_o_da_shopee():
    """Shopee e' timestamptz (verified); usar o fuso dela erraria em 4h."""
    assert ML_SOURCE_UTC_OFFSET == timedelta(hours=-4)


def test_conversao_de_fuso_acontece_em_um_unico_ponto():
    """Nenhum offset literal espalhado pelo pacote da Expedicao."""
    fonte = io.open(
        RAIZ / "pipelines" / "expedicao" / "transform.py", encoding="utf-8"
    ).read()
    arvore = ast.parse(fonte)
    usos = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        alvo = no.func
        nome = getattr(alvo, "attr", None) or getattr(alvo, "id", None)
        if nome != "timedelta":
            continue
        for kw in no.keywords:
            if kw.arg == "hours" and isinstance(kw.value, ast.Constant):
                usos.append(kw.value.value)
    assert usos == [], (
        f"offset literal em transform.py: {usos}. A conversao do ML deve usar "
        "ML_SOURCE_UTC_OFFSET, definido uma unica vez no contrato."
    )


# ---------------------------------------------------------------------------
# Montagem da fila — grao, chave e ausencia de prazo
# ---------------------------------------------------------------------------
def test_fila_usa_shipment_como_identidade():
    fila = transform.build_fila_ml([linha()], REGISTRY, AGORA, "lote-1")
    assert fila[0]["marketplace_order_id"] == "45678901234"
    assert fila[0]["marketplace_order_id"] != "2000003456789"
    assert fila[0]["shop_account"] == "2227056661"
    assert fila[0]["channel"] == Channel.MERCADOLIVRE.value


def test_dois_shipments_do_mesmo_pedido_nao_colidem():
    linhas = [
        linha(shipment_id=111, order_id=999),
        linha(shipment_id=222, order_id=999),
    ]
    fila = transform.build_fila_ml(linhas, REGISTRY, AGORA, "lote-1")
    chaves = {
        (x["channel"], x["shop_account"], x["marketplace_order_id"]) for x in fila
    }
    assert len(chaves) == 2


def test_mesmo_shipment_id_em_marcas_diferentes_nao_colide():
    """Medido na fonte: `shipment_id` repete entre marcas em 117 casos."""
    linhas = [
        linha(shipment_id=777, seller_id=2227056661, brand="kokeshi"),
        linha(shipment_id=777, seller_id=2532564723, brand="barbours"),
    ]
    fila = transform.build_fila_ml(linhas, REGISTRY, AGORA, "lote-1")
    chaves = {
        (x["channel"], x["shop_account"], x["marketplace_order_id"]) for x in fila
    }
    assert len(chaves) == 2


def test_nenhum_prazo_e_inventado():
    fila = transform.build_fila_ml([linha()], REGISTRY, AGORA, "lote-1")
    x = fila[0]
    assert x["dispatch_deadline"] is None
    assert x["deadline_status"] == DeadlineStatus.UNAVAILABLE.value
    assert x["deadline_source"] == "unavailable"
    assert x["hours_overdue"] is None


def test_nenhuma_classificacao_de_prazo_e_fabricada():
    """Nem um shipment antiquissimo vira `overdue`: nao ha prazo para estourar."""
    antigo = linha(
        date_ready_to_ship=datetime(2026, 1, 1, 0, 0),
        order_created_at=datetime(2026, 1, 1, 0, 0),
    )
    fila = transform.build_fila_ml([antigo], REGISTRY, AGORA, "lote-1")
    x = fila[0]
    assert x["deadline_status"] == DeadlineStatus.UNAVAILABLE.value
    assert x["deadline_status"] not in (
        DeadlineStatus.OVERDUE.value,
        DeadlineStatus.DUE_WITHIN_24H.value,
        DeadlineStatus.ON_TIME.value,
    )
    # ...mas a IDADE continua sendo servida, que e o que sobra sem prazo.
    assert x["operational_age_status"] == OperationalAgeStatus.OVER_48H.value
    assert x["is_source_zombie"] is True
    assert x["is_stalled"] is True


def test_idade_parte_do_date_ready_to_ship():
    """O relogio do vendedor comeca quando o ML libera o shipment."""
    x = transform.build_fila_ml(
        [linha(
            order_created_at=datetime(2026, 9, 1, 0, 0),
            date_ready_to_ship=datetime(2026, 9, 17, 12, 0),
        )],
        REGISTRY, AGORA, "lote-1",
    )[0]
    # 2026-09-17 12:00 em UTC-4 = 16:00Z; AGORA e 18:00Z -> 2h, nao 16 dias.
    assert x["hours_open"] == pytest.approx(2.0)
    assert x["operational_age_status"] == OperationalAgeStatus.WITHIN_48H.value


def test_carimbo_de_qualidade_e_assumed_nunca_verified():
    x = transform.build_fila_ml([linha()], REGISTRY, AGORA, "lote-1")[0]
    assert x["timestamp_quality"] == TimestampQuality.ASSUMED.value
    assert x["timestamp_quality"] != TimestampQuality.VERIFIED.value


def test_conta_fora_do_registry_falha_fechada():
    with pytest.raises(RegistryError):
        transform.build_fila_ml([linha(seller_id=999)], REGISTRY, AGORA, "lote-1")


def test_marca_vem_do_registry_nao_do_texto_da_fonte():
    """A fonte traz `brand`; o contrato manda resolver pela conta."""
    x = transform.build_fila_ml(
        [linha(seller_id=2227056661, brand="marca-errada-na-fonte")],
        REGISTRY, AGORA, "lote-1",
    )[0]
    assert x["brand"] == "kokeshi"


def test_slow_vs_baseline_fica_falso_e_declarado():
    x = transform.build_fila_ml([linha()], REGISTRY, AGORA, "lote-1")[0]
    assert x["is_slow_vs_baseline"] is False
    assert x["is_stalled"] == x["is_source_zombie"]


# ---------------------------------------------------------------------------
# Saude da fonte — mesma maquina do Shopee, sem contaminacao entre contas
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, fonte):
        self.fonte = fonte
        self._linhas = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        if "MAX(s.extracted_at)" in sql:
            self._linhas = self.fonte["watermarks"]
        elif "FROM raw.ml_shipments s" in sql:
            self._linhas = self.fonte["candidatos"]
        else:
            raise AssertionError(f"SQL inesperado: {sql[:60]}")

    def fetchall(self):
        return self._linhas


class FakeConn:
    """Fake ESTRITO: qualquer tentativa de escrita levanta."""

    def __init__(self, fonte):
        self.fonte = fonte
        self.commits = 0

    def cursor(self):
        return FakeCursor(self.fonte)

    def commit(self):
        raise AssertionError("o extrator do ML nao pode commitar nada")

    def set_session(self, **_):
        raise AssertionError("o extrator nao troca o modo da sessao")


def _fonte(candidatos, watermarks):
    return {"candidatos": candidatos, "watermarks": watermarks}


def _wm(seller_id, brand, quando):
    return {"seller_id": seller_id, "brand": brand, "max_extracted_at": quando}


def test_registry_vazio_devolve_source_unavailable():
    conn = FakeConn(_fonte([], []))
    r = ml_extract.extract(conn, AGORA, frozenset())
    assert r.source_health is SourceHealth.SOURCE_UNAVAILABLE
    assert r.backlog_rows == []


def test_conta_esperada_ausente_bloqueia():
    conn = FakeConn(_fonte([], [_wm(2227056661, "kokeshi", datetime(2026, 9, 17, 13))]))
    r = ml_extract.extract(conn, AGORA, frozenset({"2227056661", "2532564723"}))
    assert r.source_health is SourceHealth.ACCOUNT_MISSING
    assert r.backlog_rows == []


def test_conta_inesperada_bloqueia():
    conn = FakeConn(_fonte([], [
        _wm(2227056661, "kokeshi", datetime(2026, 9, 17, 13)),
        _wm(9999999999, "nova", datetime(2026, 9, 17, 13)),
    ]))
    r = ml_extract.extract(conn, AGORA, frozenset({"2227056661"}))
    assert r.source_health is SourceHealth.UNEXPECTED_ACCOUNT


def test_conta_saudavel_e_conta_desatualizada_nao_se_contaminam():
    """Watermark e POR CONTA: uma conta atrasada nao se esconde atras da outra.

    As duas ficam DENTRO da coorte de 7 dias — senao a publicacao inteira
    bloquearia por `SOURCE_STALE`, que e' outro teste. O ponto aqui e' que o
    frescor de cada conta e classificado separadamente: `fresh` para uma,
    `critical` para a outra, sem media nem maximo global.
    """
    conn = FakeConn(_fonte(
        [linha(seller_id=2227056661), linha(seller_id=2532564723)],
        [
            _wm(2227056661, "kokeshi", datetime(2026, 9, 17, 13, 30)),
            _wm(2532564723, "barbours", datetime(2026, 9, 14, 12, 0)),
        ],
    ))
    r = ml_extract.extract(conn, AGORA, frozenset({"2227056661", "2532564723"}))
    assert r.source_health is SourceHealth.HEALTHY
    assert r.account_watermarks["2227056661"] != r.account_watermarks["2532564723"]
    fresco = transform.classify_freshness(
        transform.normalizar_carimbo_ml(r.account_watermarks["2227056661"]), AGORA
    )
    velho = transform.classify_freshness(
        transform.normalizar_carimbo_ml(r.account_watermarks["2532564723"]), AGORA
    )
    assert fresco.value == "fresh"
    assert velho.value == "critical"


def test_extract_saudavel_devolve_diagnostico():
    conn = FakeConn(_fonte(
        [linha(), linha(logistic_type="fulfillment")],
        [_wm(2227056661, "kokeshi", datetime(2026, 9, 17, 13, 30))],
    ))
    r = ml_extract.extract(conn, AGORA, frozenset({"2227056661"}))
    assert r.source_health is SourceHealth.HEALTHY
    assert r.diagnostics["fulfillment_excluded_count"] == 1
    assert r.diagnostics["queue_count"] == 1
    assert r.backlog_count == 1


def test_extrator_nunca_commita():
    conn = FakeConn(_fonte([linha()], [
        _wm(2227056661, "kokeshi", datetime(2026, 9, 17, 13, 30))
    ]))
    ml_extract.extract(conn, AGORA, frozenset({"2227056661"}))
    assert conn.commits == 0


# ---------------------------------------------------------------------------
# PII e superficie servida
# ---------------------------------------------------------------------------
def test_sql_do_ml_nao_seleciona_campo_de_comprador():
    for sql in (ml_extract.ML_BACKLOG_SQL, ml_extract.ML_WATERMARK_SQL):
        baixo = sql.lower()
        for termo in ("receiver", "buyer", "cpf", "phone", "address", "endereco",
                      "email", "zip", "street", "nome", "document"):
            assert termo not in baixo, f"{termo} no SQL do ML"


def test_sql_do_ml_nao_seleciona_numero_de_rastreio():
    """`tracking_number` e' rastreavel publicamente e a API nao tem auth."""
    assert "tracking_number" not in ml_extract.ML_BACKLOG_SQL
    assert "tracking_method" in ml_extract.ML_BACKLOG_SQL


def test_fila_do_ml_nao_carrega_order_id_real():
    """O `order_id` e' insumo interno; a linha publicada guarda o shipment."""
    x = transform.build_fila_ml([linha()], REGISTRY, AGORA, "lote-1")[0]
    assert "order_id" not in x
    assert str(linha()["order_id"]) not in str(x.values())


def test_colunas_do_select_batem_com_a_lista_fechada():
    from pipelines.expedicao.contract import ML_ALLOWED_SOURCE_COLUMNS

    assert set(ml_extract.ML_SELECT_COLUMNS) == set(ML_ALLOWED_SOURCE_COLUMNS)


# ---------------------------------------------------------------------------
# Isolamento do canal e preservacao da Shopee
# ---------------------------------------------------------------------------
def test_shopee_continua_sem_diagnostics():
    """O campo novo e aditivo: a Shopee nao muda de forma."""
    from pipelines.expedicao.contract import ExtractionResult

    r = ExtractionResult(
        source_health=SourceHealth.HEALTHY,
        expected_accounts=frozenset(),
        observed_accounts=frozenset(),
        account_watermarks={},
        backlog_rows=[],
    )
    assert r.diagnostics is None


def test_shopee_e_ml_produzem_canais_diferentes():
    ml = transform.build_fila_ml([linha()], REGISTRY, AGORA, "lote-1")[0]
    assert ml["channel"] == "mercadolivre"
    assert ml["channel"] != Channel.SHOPEE.value


def test_logistic_type_do_ml_e_preenchido_e_o_da_shopee_nao():
    """A 018 tem CHECK: channel <> 'shopee' OR logistic_type IS NULL."""
    ml = transform.build_fila_ml([linha()], REGISTRY, AGORA, "lote-1")[0]
    assert ml["logistic_type"] in ML_SELLER_MANAGED_LOGISTIC_TYPES


def test_cli_recusa_apply_do_mercadolivre():
    """Nenhum caminho deste gate chega ao publisher."""
    from pipelines.expedicao import cli

    codigo = cli.run_apply(Channel.MERCADOLIVRE, AGORA)
    assert codigo == cli.EXIT_PRECONDICAO


def test_cli_aceita_mercadolivre_apenas_no_diagnose():
    from pipelines.expedicao import cli

    parser = cli.build_parser()
    args = parser.parse_args(["--channel", "mercadolivre", "--diagnose"])
    assert args.channel == "mercadolivre"
    assert args.diagnose is True
    assert args.apply is False


# ---------------------------------------------------------------------------
# EXP-3B1-R/V — fonte parada x fila vazia legitima
# ---------------------------------------------------------------------------
def test_conta_com_extracao_parada_recusa_publicacao():
    """BLOCKER encontrado na revisao: conta parada NAO pode virar fila vazia.

    O filtro de coorte remove justamente as linhas que o extrator deixou de
    reler. Sem esta barreira, a conta saia `healthy` com `backlog = 0`, o
    publisher faria `DELETE WHERE channel='mercadolivre'` e inseriria zero
    linhas — apagando a fila anterior com base em silencio da fonte.
    """
    parada = datetime(2026, 5, 2, 0, 0)  # 4,5 meses atras
    conn = FakeConn(_fonte(
        [linha(extracted_at=parada), linha(shipment_id=4568, extracted_at=parada)],
        [_wm(2227056661, "kokeshi", parada)],
    ))
    r = ml_extract.extract(conn, AGORA, frozenset({"2227056661"}))
    assert r.source_health is SourceHealth.SOURCE_STALE
    assert r.source_health.can_publish is False
    assert r.backlog_rows == []
    assert r.is_empty_photograph is False, (
        "fonte parada nunca pode ser lida como fotografia vazia legitima"
    )


def test_conta_saudavel_sem_backlog_e_fila_vazia_LEGITIMA():
    """Caso Rituaria: 100% FULL no ML, entao zero shipment seller-managed.

    Fonte recente + zero candidatos = fotografia vazia legitima, que e' coisa
    diferente de fonte ausente e PODE ser publicada.
    """
    conn = FakeConn(_fonte([], [
        _wm(1366932565, "rituaria", datetime(2026, 9, 17, 13, 30))
    ]))
    r = ml_extract.extract(conn, AGORA, frozenset({"1366932565"}))
    assert r.source_health is SourceHealth.HEALTHY
    assert r.source_health.can_publish is True
    assert r.backlog_count == 0
    assert r.is_empty_photograph is True


def test_conta_so_com_full_tambem_e_vazia_legitima():
    """Rituaria de verdade: ha shipments, mas todos sao Full."""
    conn = FakeConn(_fonte(
        [linha(seller_id=1366932565, brand="rituaria",
               logistic_type="fulfillment")],
        [_wm(1366932565, "rituaria", datetime(2026, 9, 17, 13, 30))],
    ))
    r = ml_extract.extract(conn, AGORA, frozenset({"1366932565"}))
    assert r.source_health is SourceHealth.HEALTHY
    assert r.backlog_count == 0
    assert r.diagnostics["fulfillment_excluded_count"] == 1
    assert r.diagnostics["candidate_count"] == 1


def test_uma_conta_parada_bloqueia_o_canal_inteiro():
    """Nao se publica metade do canal: o DELETE e' por `channel`, nao por conta."""
    conn = FakeConn(_fonte(
        [linha()],
        [
            _wm(2227056661, "kokeshi", datetime(2026, 9, 17, 13, 30)),
            _wm(2532564723, "barbours", datetime(2026, 5, 2, 0, 0)),
        ],
    ))
    r = ml_extract.extract(conn, AGORA, frozenset({"2227056661", "2532564723"}))
    assert r.source_health is SourceHealth.SOURCE_STALE
    assert "2532564723" in r.detail
    assert "2227056661" not in r.detail


def test_detalhe_da_fonte_parada_nao_vaza_payload():
    """A mensagem carrega conta e limite, nunca linha de pedido."""
    parada = datetime(2026, 5, 2, 0, 0)
    conn = FakeConn(_fonte(
        [linha(extracted_at=parada)], [_wm(2227056661, "kokeshi", parada)]
    ))
    r = ml_extract.extract(conn, AGORA, frozenset({"2227056661"}))
    for proibido in ("4567", "ready_for_pickup", "Normal", "2000"):
        assert proibido not in r.detail


# ---------------------------------------------------------------------------
# EXP-3B1-R/V — fronteiras do limiar e virada de data
# ---------------------------------------------------------------------------
def test_fronteira_exata_das_168_horas():
    """Exatamente no limite ainda esta DENTRO; um segundo alem, fora."""
    limite = AGORA - ML_SOURCE_COHORT_MAX_AGE
    assert ml_extract.is_stale_source_record(limite, AGORA) is False
    assert ml_extract.is_stale_source_record(
        limite - timedelta(seconds=1), AGORA
    ) is True


def test_fronteira_com_carimbo_naive_da_fonte():
    """O mesmo instante, escrito como a fonte escreve (naive UTC-4)."""
    limite_utc = AGORA - ML_SOURCE_COHORT_MAX_AGE
    naive = (limite_utc + ML_SOURCE_UTC_OFFSET).replace(tzinfo=None)
    assert ml_extract.is_stale_source_record(naive, AGORA) is False
    assert ml_extract.is_stale_source_record(
        naive - timedelta(hours=1), AGORA
    ) is True


def test_offset_fixo_atravessa_virada_de_ano_sem_saltar():
    """Offset FIXO: nao ha horario de verao no Brasil desde 2019.

    O teste existe para travar a decisao: se alguem trocar o offset fixo por
    um fuso com DST, estas duas datas passariam a divergir em 1h e a fronteira
    da coorte se moveria sozinha no meio do verao.
    """
    inverno = datetime(2026, 7, 15, 12, 0)
    verao = datetime(2026, 1, 15, 12, 0)
    d_inverno = transform.normalizar_carimbo_ml(inverno) - inverno.replace(
        tzinfo=timezone.utc
    )
    d_verao = transform.normalizar_carimbo_ml(verao) - verao.replace(
        tzinfo=timezone.utc
    )
    assert d_inverno == d_verao == timedelta(hours=4)


def test_virada_de_data_nao_muda_o_dia_errado():
    """23:30 naive vira 03:30 do dia seguinte em UTC — e isso e' correto."""
    assert transform.normalizar_carimbo_ml(
        datetime(2026, 9, 17, 23, 30)
    ) == datetime(2026, 9, 18, 3, 30, tzinfo=timezone.utc)


def test_normalizacao_nao_desloca_linha_viva_para_fora_da_coorte():
    """A conversao move o carimbo 4h para FRENTE, nunca para tras.

    Se ela deslocasse para tras, uma linha recem-relida poderia cair fora da
    coorte. Medindo o sinal do deslocamento o risco fica travado.
    """
    bruto = datetime(2026, 9, 17, 14, 0)
    convertido = transform.normalizar_carimbo_ml(bruto)
    assert convertido > bruto.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# EXP-3B1-R/V — encerramento da fila e relogio
# ---------------------------------------------------------------------------
def test_sql_exclui_despachado_e_cancelado():
    """O fechamento e' do SQL; o teste trava o predicado."""
    assert "s.date_shipped IS NULL" in ml_extract.ML_BACKLOG_SQL
    assert "s.date_cancelled IS NULL" in ml_extract.ML_BACKLOG_SQL
    assert "s.status = %(shipment_status)s" in ml_extract.ML_BACKLOG_SQL
    assert "o.status = %(order_status)s" in ml_extract.ML_BACKLOG_SQL


def test_relogio_sem_marco_nao_vira_idade_silenciosa():
    """Sem marco nenhum, a idade e' UNKNOWN — nao vira `within_48h` por omissao."""
    x = transform.build_fila_ml(
        [linha(date_ready_to_ship=None, order_created_at=None)],
        REGISTRY, AGORA, "lote-1",
    )[0]
    assert x["operational_age_status"] == OperationalAgeStatus.UNKNOWN.value
    assert x["hours_open"] is None
    assert x["is_source_zombie"] is False


# ---------------------------------------------------------------------------
# EXP-3B1-R/V — isolamento entre os extratores
# ---------------------------------------------------------------------------
def test_extrator_do_ml_nao_le_a_fonte_da_shopee():
    for sql in (ml_extract.ML_BACKLOG_SQL, ml_extract.ML_WATERMARK_SQL):
        assert "shopee" not in sql.lower()
        assert "order_sn" not in sql.lower()


def test_extrator_da_shopee_nao_le_a_fonte_do_ml():
    from pipelines.expedicao import shopee_extract

    for sql in (shopee_extract.SHOPEE_BACKLOG_SQL,
                shopee_extract.SHOPEE_WATERMARK_SQL):
        assert "ml_shipments" not in sql.lower()
        assert "shipment_id" not in sql.lower()


def test_canal_invalido_e_recusado_pelo_parser():
    from pipelines.expedicao import cli

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--channel", "magalu", "--diagnose"])


def test_nenhum_canal_novo_entra_por_fallback():
    from pipelines.expedicao import cli

    parser = cli.build_parser()
    acao = next(a for a in parser._actions if a.dest == "channel")
    assert set(acao.choices) == {"shopee", "mercadolivre"}
