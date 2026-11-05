"""Atraso de 7 dias por conta no resumo da Expedicao (regra oficial da Shopee).

    taxa = late_7d_count / due_7d_count, janela [hoje-7, hoje-1] em Brasilia

O que se prova aqui:

* janela: os 7 dias FECHADOS antes do dia BRT de `effective_at` (virada de
  dia em Brasilia, nao em UTC);
* Shopee: a base e' a da LDR operacional (`shopee_ldr.classificar`), so'
  vencimentos MADUROS, com as exclusoes da LDR;
* Mercado Livre: o SQL agregado, contra PostgreSQL REAL (sem binarios, SKIP):
  `delayed`, nao coletado vencido, coletado no prazo fora do atraso, cancelado
  antes do prazo fora da base, fusos por coluna e janela "desde o 1o SLA";
* publicador: grava as seis colunas SO' com as seis no destino; sem elas, o
  INSERT e o upsert sao os de antes; o ON CONFLICT atualiza as seis;
* migration 035: cadeia, seis colunas NULLABLE nas duas tabelas de resumo,
  head continua 035 e idempotencia (PG real);
* CLI: o `--apply` grava as contagens e uma falha do calculo nao derruba a fila;
* acima de 48 h e travados (aba "Ultimos 7 dias"): mesma base do atraso,
  duracao = coleta (ou agora) - marco; Shopee pelo baseline da fila, ML sempre
  NULL nos travados e 48 h pelo `substatus_history`/`date_shipped`, Kokeshi
  pelo baseline do export.
"""
from __future__ import annotations

import ast
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import atraso_7d, cli, publisher, shopee_ldr, transform
from pipelines.expedicao.contract import (
    ATRASO_7D_COLUMNS,
    PII_FORBIDDEN_TOKENS,
    RUN_EXPORT_TABLE,
    RUN_TABLE,
    Channel,
    SellerAccount,
    SourceHealth,
)
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_da_sessao,
    postgres_disponivel,
)

BRT = timezone(timedelta(hours=-3))
UTC = timezone.utc

#: 10/11/2026 12:00 em Brasilia. Janela: 03/11 a 09/11.
AGORA = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
DESDE, ATE = date(2026, 11, 3), date(2026, 11, 9)

sem_postgres = pytest.mark.skipif(
    not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES
)

REPO = Path(__file__).resolve().parents[2]
VERSIONS = REPO / "apps" / "api" / "alembic" / "versions"
ARQ_035 = VERSIONS / "035_expedicao_atraso_7d.py"


def prazo(dia: date) -> datetime:
    """`ship_by_date` como a Shopee publica: 23:59 de Brasilia."""
    return datetime.combine(dia, time(23, 59), tzinfo=BRT)


def em(dia: date, hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), tzinfo=BRT)


def ped(dia, *, shop="1", status="PROCESSED", coleta=None,
        fluxo=shopee_ldr.FLUXO_VENDEDOR, pago=True) -> shopee_ldr.Pedido:
    return shopee_ldr.Pedido(
        shop_id=shop, shop_account=f"loja{shop}", order_status=status,
        fulfillment_flag=fluxo,
        pay_time=em(date(2026, 11, 1), 10) if pago else None,
        ship_by_date=prazo(dia) if dia is not None else None,
        pickup_done_time=coleta,
    )


# ===========================================================================
# Janela
# ===========================================================================
def test_janela_e_os_sete_dias_fechados_antes_de_hoje():
    assert atraso_7d.janela(AGORA) == (DESDE, ATE)
    assert (ATE - DESDE).days + 1 == 7


def test_janela_vira_no_dia_de_brasilia_nao_no_utc():
    # 02:59 UTC de 11/11 ainda e' 10/11 em Brasilia.
    assert atraso_7d.janela(datetime(2026, 11, 11, 2, 59, tzinfo=UTC)) == (DESDE, ATE)
    # 03:00 UTC de 11/11 e' meia-noite de 11/11 em Brasilia: a janela anda.
    assert atraso_7d.janela(datetime(2026, 11, 11, 3, 0, tzinfo=UTC)) == (
        date(2026, 11, 4), date(2026, 11, 10))


def test_janela_recusa_instante_sem_fuso():
    with pytest.raises(ValueError):
        atraso_7d.janela(datetime(2026, 11, 10, 12, 0))


def test_modulo_nao_le_relogio():
    arvore = ast.parse(Path(atraso_7d.__file__).read_text(encoding="utf-8"))
    chamadas = {
        n.func.attr for n in ast.walk(arvore)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not {"now", "today", "utcnow"} & chamadas


# ===========================================================================
# Shopee
# ===========================================================================
D = date(2026, 11, 5)


def test_shopee_base_e_atraso_seguem_a_ldr():
    pedidos = [
        ped(D, coleta=em(D, 12)),                                   # no prazo
        ped(D, coleta=em(D + timedelta(days=1), 10)),               # coletado tarde
        ped(D + timedelta(days=1), status="READY_TO_SHIP"),         # pendente vencido
        ped(D, status="CANCELLED", coleta=em(D + timedelta(days=1), 9)),  # cancelado COM coleta tardia
        ped(D, fluxo=shopee_ldr.FLUXO_FBS),                         # FBS: fora
        ped(D, status="CANCELLED"),                                 # cancelado sem coleta: fora
        ped(D, status="SHIPPED"),                                   # pos-envio sem coleta: fora
        ped(D, pago=False),                                         # nao pago: fora
        ped(None),                                                  # sem prazo: fora
        ped(date(2026, 11, 10), status="READY_TO_SHIP"),            # vence hoje: fora
        ped(date(2026, 11, 2), status="READY_TO_SHIP"),             # antes da janela: fora
    ]
    r = atraso_7d.apurar_shopee(pedidos, {"1": AGORA}, AGORA)
    # Pagos em 01/11: os quatro da base passaram de 48 h; sem baseline,
    # travados NULL.
    assert r == {"1": {
        "due_7d_count": 4, "late_7d_count": 3,
        "late_7d_window_start": DESDE, "late_7d_window_end": ATE,
        "over_48h_7d_count": 4, "stalled_7d_count": None,
    }}


def test_shopee_antes_das_6h_ontem_ainda_nao_amadureceu():
    cedo = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)  # 05:00 em Brasilia
    pedidos = [
        ped(date(2026, 11, 9), status="READY_TO_SHIP"),   # ontem: imaturo
        ped(date(2026, 11, 8), coleta=em(date(2026, 11, 8), 15)),
    ]
    r = atraso_7d.apurar_shopee(pedidos, {"1": cedo}, cedo)
    assert r["1"]["due_7d_count"] == 1
    assert r["1"]["late_7d_count"] == 0
    assert r["1"]["late_7d_window_end"] == date(2026, 11, 8)


def test_shopee_conta_com_fonte_parada_encolhe_a_janela():
    # Ultima leitura 06/11 07:00 BRT: so' ate' 05/11 esta' maduro.
    wm = em(date(2026, 11, 6), 7)
    pedidos = [
        ped(date(2026, 11, 7), status="READY_TO_SHIP"),   # imaturo: fora
        ped(date(2026, 11, 4), status="READY_TO_SHIP"),
    ]
    r = atraso_7d.apurar_shopee(pedidos, {"1": wm}, AGORA)
    assert r["1"] == {
        "due_7d_count": 1, "late_7d_count": 1,
        "late_7d_window_start": DESDE, "late_7d_window_end": date(2026, 11, 5),
        "over_48h_7d_count": 1, "stalled_7d_count": None,
    }


def test_shopee_ausencia_e_null_nunca_zero():
    pedidos = [
        ped(D, shop="2", status="READY_TO_SHIP"),             # sem watermark
        ped(D, shop="3", fluxo=shopee_ldr.FLUXO_FBS),         # so' excluidos
        ped(D, shop="4", coleta=em(D, 10)),                   # base sem atraso
    ]
    r = atraso_7d.apurar_shopee(pedidos, {"3": AGORA, "4": AGORA}, AGORA)
    assert set(r) == {"4"}
    assert r["4"]["late_7d_count"] == 0  # zero MEDIDO, com base > 0


def test_shopee_fluxo_desconhecido_falha_fechado():
    with pytest.raises(shopee_ldr.FluxoDesconhecido):
        atraso_7d.apurar_shopee([ped(D, fluxo="outro_fluxo")], {"1": AGORA}, AGORA)


class _CursorGravador:
    def __init__(self, conn):
        self.conn = conn
        self.description = [(c,) for c in shopee_ldr.COLUNAS_FONTE]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.chamadas.append((sql, params))

    def fetchall(self):
        return list(self.conn.linhas)


class _ConnGravadora:
    def __init__(self, linhas):
        self.linhas = linhas
        self.chamadas = []

    def cursor(self):
        return _CursorGravador(self)


def test_shopee_le_so_uma_semana_de_vencimentos_sem_pii():
    linha = {
        "shop_id": "1", "shop_account": "loja1", "order_status": "READY_TO_SHIP",
        "fulfillment_flag": shopee_ldr.FLUXO_VENDEDOR, "pay_time": em(D, 9),
        "ship_by_date": prazo(D), "pickup_done_time": None, "cancel_reason": None,
    }
    conn = _ConnGravadora([linha])
    r = atraso_7d.calcular_shopee(conn, AGORA, {"1": AGORA})
    assert r["1"]["due_7d_count"] == r["1"]["late_7d_count"] == 1
    [(sql, params)] = conn.chamadas
    assert sql == shopee_ldr.PEDIDOS_POR_VENCIMENTO_SQL
    assert params == {
        "venc_desde": datetime(2026, 11, 3, 0, 0, tzinfo=BRT),
        "venc_ate_excl": datetime(2026, 11, 10, 0, 0, tzinfo=BRT),
    }
    assert "*" not in sql
    assert not [t for t in PII_FORBIDDEN_TOKENS if t in sql.lower()]


# ===========================================================================
# Mercado Livre — parte Python (fake)
# ===========================================================================
class _CursorML:
    def __init__(self, conn):
        self.conn = conn
        self._r = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.chamadas.append((sql, params))
        if sql == atraso_7d.ML_SLA_INICIO_SQL:
            self._r = [self.conn.inicio]
        elif sql == atraso_7d.ML_ATRASO_7D_SQL:
            self._r = list(self.conn.contas)
        else:
            raise AssertionError(f"SQL inesperado: {sql[:60]!r}")

    def fetchone(self):
        return self._r[0] if self._r else None

    def fetchall(self):
        return list(self._r)


class _ConnML:
    def __init__(self, inicio, contas=()):
        self.inicio = inicio
        self.contas = contas
        self.chamadas = []

    def cursor(self):
        return _CursorML(self)


def _inicio(primeiro, sem_fuso=0, amostra=None):
    return {"primeira_leitura": primeiro, "sem_fuso": sem_fuso,
            "amostra_sem_fuso": amostra}


def _agregado(seller, due, late, over_48h=0, historico_invalido=0):
    """Uma linha do `ML_ATRASO_7D_SQL`, como o cursor de dicionario devolve."""
    return {"seller_id": seller, "due": due, "late": late,
            "over_48h": over_48h, "historico_invalido": historico_invalido}


def test_ml_janela_comeca_no_primeiro_sla_desde_03_11():
    hoje = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # janela nominal 30/10 a 05/11
    conn = _ConnML(_inicio(em(date(2026, 11, 3), 14)),
                   [_agregado(2532564723, 10, 2, 3)])
    r = atraso_7d.calcular_ml(conn, hoje)
    assert r == {"2532564723": {
        "due_7d_count": 10, "late_7d_count": 2,
        "late_7d_window_start": date(2026, 11, 3),
        "late_7d_window_end": date(2026, 11, 5),
        "over_48h_7d_count": 3, "stalled_7d_count": None,
    }}
    params = conn.chamadas[1][1]
    assert params["desde"] == date(2026, 11, 3)
    assert params["ate"] == date(2026, 11, 5)
    assert params["hoje"] == date(2026, 11, 6)
    assert params["sla_atrasado"] == "delayed"
    assert set(params["coletados"]) == {"picked_up", "dropped_off", "in_hub"}
    assert set(params["enviados"]) == {"shipped", "delivered"}
    assert params["agora"] == hoje
    assert params["limite_idade"] == timedelta(hours=48)


def test_ml_hoje_sem_dia_fechado_desde_a_primeira_leitura_fica_nulo():
    """Medido em 03/11/2026: a 1a leitura trouxe envios parados com prazo de
    13/10 a 31/10 (25 de 33 `delayed`). Contados, o ML sairia com 75%. A janela
    comeca no dia da 1a LEITURA: no proprio dia ela esta' vazia -> nulo."""
    hoje = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
    conn = _ConnML(_inicio(datetime(2026, 11, 3, 14, 14, tzinfo=UTC)),
                   [_agregado("1", 28, 21)])
    assert atraso_7d.calcular_ml(conn, hoje) == {}
    assert len(conn.chamadas) == 1, "sem janela, nem consulta o agregado"
    assert "sla_extracted_at" in atraso_7d.ML_SLA_INICIO_SQL


def test_ml_primeiro_sla_antigo_nao_estica_a_janela():
    conn = _ConnML(_inicio(em(date(2026, 10, 1), 14)),
                   [_agregado("1", 3, 0)])
    r = atraso_7d.calcular_ml(conn, AGORA)
    assert r["1"]["late_7d_window_start"] == DESDE


def test_ml_dia_do_primeiro_sla_e_o_de_brasilia():
    # 03/11 01:00 UTC = 02/11 22:00 em Brasilia.
    assert atraso_7d.inicio_ml(
        date(2026, 10, 30), datetime(2026, 11, 3, 1, 0, tzinfo=UTC)
    ) == date(2026, 11, 2)
    assert atraso_7d.inicio_ml(date(2026, 10, 30), None) is None


def test_ml_sem_sla_nao_consulta_contas_e_devolve_vazio():
    conn = _ConnML(_inicio(None))
    assert atraso_7d.calcular_ml(conn, AGORA) == {}
    assert len(conn.chamadas) == 1


def test_ml_sla_que_comeca_hoje_ainda_nao_tem_janela():
    conn = _ConnML(_inicio(em(date(2026, 11, 10), 9)))
    assert atraso_7d.calcular_ml(conn, AGORA) == {}
    assert len(conn.chamadas) == 1


def test_ml_sla_sem_fuso_falha_fechado_pelo_parser_da_fila():
    conn = _ConnML(_inicio(None, sem_fuso=3, amostra="2026-11-05T14:00:00"))
    with pytest.raises(ValueError, match="sem fuso"):
        atraso_7d.calcular_ml(conn, AGORA)


def test_ml_sql_sem_pii_e_sem_select_estrela():
    for sql in (atraso_7d.ML_SLA_INICIO_SQL, atraso_7d.ML_ATRASO_7D_SQL):
        assert "select *" not in sql.lower()
        assert not [t for t in PII_FORBIDDEN_TOKENS if t in sql.lower()]
        # Contagens por conta: nenhum identificador de envio sai do banco.
        assert "shipment_id" not in sql


def test_valores_recusam_base_vazia_e_atraso_acima_da_base():
    with pytest.raises(ValueError):
        atraso_7d.valores(0, 0, DESDE, ATE)
    with pytest.raises(ValueError):
        atraso_7d.valores(2, 3, DESDE, ATE)
    with pytest.raises(ValueError):
        atraso_7d.valores(2, 1, ATE, DESDE)


# ===========================================================================
# Mercado Livre — SQL contra PostgreSQL REAL
# ===========================================================================
DDL_FONTE_ML = (
    "CREATE SCHEMA raw",
    """CREATE TABLE raw.ml_orders (
        brand TEXT, order_id BIGINT, seller_id BIGINT, status TEXT,
        date_created TIMESTAMP)""",
    """CREATE TABLE raw.ml_shipments (
        brand TEXT, shipment_id BIGINT, order_id BIGINT, status TEXT,
        substatus TEXT, logistic_type TEXT, date_created TIMESTAMP,
        date_cancelled TIMESTAMP, extracted_at TIMESTAMP,
        date_ready_to_ship TIMESTAMP, date_shipped TIMESTAMP {hist} {sla})""",
)
#: `sla_extracted_at` faz parte da fonte desde a janela "desde a 1a leitura"
#: (4a3de99): sem ela o 1o SQL nao acha leitura nenhuma e devolve vazio.
SLA_TZ = (", sla_expected_date TIMESTAMPTZ, sla_extracted_at TIMESTAMPTZ,"
          " sla_status TEXT")
SLA_NAIVE = (", sla_expected_date TIMESTAMP, sla_extracted_at TIMESTAMPTZ,"
             " sla_status TEXT")
HIST_JSONB = ", substatus_history JSONB"


def _banco(nome: str, sla: str, hist: str = HIST_JSONB) -> str:
    import psycopg2

    with cluster_da_sessao() as base:
        adm = psycopg2.connect(base)
        adm.autocommit = True
        with adm.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)")
            cur.execute(f"CREATE DATABASE {nome}")
        adm.close()
        url = base.rsplit("/", 1)[0] + "/" + nome
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        for stmt in DDL_FONTE_ML:
            cur.execute(stmt.format(sla=sla, hist=hist))
    conn.close()
    return url


_SEQ = iter(range(1, 10_000))


def _envio(cur, *, seller=111, sla=None, sla_status="on_time", status="shipped",
           substatus=None, modalidade="cross_docking", pedido="paid",
           cancelado=None, criado=datetime(2026, 11, 1, 10, 0), pronto=None,
           enviado=None, historico=None, com_historico=True):
    import json

    n = next(_SEQ)
    cur.execute(
        "INSERT INTO raw.ml_orders VALUES ('kokeshi', %s, %s, %s, %s)",
        (n, seller, pedido, criado),
    )
    colunas = ("brand, shipment_id, order_id, status, substatus, logistic_type,"
               " date_created, date_cancelled, extracted_at, date_ready_to_ship,"
               " date_shipped, sla_expected_date, sla_extracted_at, sla_status")
    # A leitura do SLA no proprio horario do prazo: o 1o prazo e' a 1a leitura.
    valores = [n, n, status, substatus, modalidade, criado, cancelado, criado,
               pronto, enviado, sla, sla, sla_status]
    if com_historico:
        colunas += ", substatus_history"
        valores.append(None if historico is None else json.dumps(historico))
    cur.execute(
        f"INSERT INTO raw.ml_shipments ({colunas}) VALUES ('kokeshi', "
        + ", ".join(["%s"] * len(valores)) + ")",
        valores,
    )


def _calcular(url):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    conn.set_session(readonly=True, autocommit=True)
    try:
        return atraso_7d.calcular_ml(conn, AGORA)
    finally:
        conn.close()


def _popular(url, linhas):
    import psycopg2

    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        for kw in linhas:
            _envio(cur, **kw)
    conn.close()


@sem_postgres
def test_ml_sql_regras_contra_postgres_real():
    url = _banco("atraso7d_ml", SLA_TZ)
    d6 = date(2026, 11, 6)
    _popular(url, [
        # 1o SLA da fonte: 05/11 -> a janela comeca em 05/11, nao em 03/11.
        dict(sla=em(date(2026, 11, 5), 14)),                          # base
        # `delayed` do ML: atrasado, mesmo ja' enviado.
        dict(sla=em(d6, 14), sla_status="delayed"),                    # base+atraso
        # ainda ready_to_ship, sem coleta, prazo de dia fechado: atrasado.
        dict(sla=em(date(2026, 11, 7), 14), status="ready_to_ship",
             substatus="ready_for_pickup"),                            # base+atraso
        # ready_to_ship ja' coletado (`picked_up`) e ML `on_time`: no prazo.
        dict(sla=em(date(2026, 11, 7), 14), status="ready_to_ship",
             substatus="picked_up"),                                   # base
        # coleta escaneada depois do horario, ML `on_time`: no prazo.
        dict(sla=em(date(2026, 11, 8), 14)),                           # base
        # cancelado ANTES do prazo (naive -04:00 = 06/11 09:00 BRT): fora.
        dict(sla=em(d6, 14), status="cancelled", sla_status="delayed",
             cancelado=datetime(2026, 11, 6, 8, 0)),
        # cancelado 30 min DEPOIS do prazo: 13:30 em -04:00 = 14:30 em Brasilia.
        # Lido como UTC sairia "antes" e o envio sumiria da base.
        dict(sla=em(d6, 14), status="cancelled", sla_status="delayed",
             cancelado=datetime(2026, 11, 6, 13, 30)),                 # base+atraso
        # 10/11 02:00 UTC = 09/11 23:00 em Brasilia: dentro, dia fechado.
        dict(sla=datetime(2026, 11, 10, 2, 0, tzinfo=UTC), status="ready_to_ship",
             substatus="ready_to_print"),                              # base+atraso
        # Fora: vence hoje (10/11 em Brasilia), Full, pedido nao pago, sem SLA.
        dict(sla=em(date(2026, 11, 10), 10), status="ready_to_ship"),
        dict(sla=em(d6, 14), sla_status="delayed", modalidade="fulfillment"),
        dict(sla=em(d6, 14), sla_status="delayed", pedido="cancelled"),
        dict(sla=None, status="ready_to_ship"),
        # Outra conta, so' com Full: nao aparece (NULL, nunca zero).
        dict(seller=222, sla=em(d6, 14), modalidade="fulfillment"),
    ])
    # Sem `date_ready_to_ship`, coleta nem `date_shipped`: marco = criacao do
    # pedido (01/11) e todos abertos ate' agora -> os sete passam de 48 h.
    assert _calcular(url) == {"111": {
        "due_7d_count": 7, "late_7d_count": 4,
        "late_7d_window_start": date(2026, 11, 5),
        "late_7d_window_end": ATE,
        "over_48h_7d_count": 7, "stalled_7d_count": None,
    }}


@sem_postgres
def test_ml_sql_fonte_sem_sla_devolve_vazio():
    import psycopg2

    url = _banco("atraso7d_ml_sem_sla", "")
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO raw.ml_orders VALUES ('k', 1, 111, 'paid', now())")
        cur.execute(
            "INSERT INTO raw.ml_shipments VALUES ('k', 1, 1, 'ready_to_ship',"
            " NULL, 'cross_docking', '2026-11-05 10:00', NULL, '2026-11-05 10:00')"
        )
    conn.close()
    assert _calcular(url) == {}


@sem_postgres
def test_ml_sql_sla_naive_falha_fechado():
    url = _banco("atraso7d_ml_naive", SLA_NAIVE)
    _popular(url, [dict(sla=datetime(2026, 11, 6, 14, 0))])
    with pytest.raises(ValueError, match="sem fuso"):
        _calcular(url)


# ===========================================================================
# Resumo por conta
# ===========================================================================
def test_resumo_carrega_as_quatro_chaves_por_conta_externa():
    v = atraso_7d.valores(10, 1, DESDE, ATE)
    resumos = transform.build_account_summaries(
        [], AGORA, channel="shopee", refresh_batch_id="l",
        accounts={"1": ("loja1", "apice"), "2": ("loja2", "barbours")},
        watermarks={}, source_advanced=False, atraso_7d={"1": v},
    )
    por_conta = {r["shop_account"]: r for r in resumos}
    assert {c: por_conta["loja1"][c] for c in ATRASO_7D_COLUMNS} == v
    assert all(por_conta["loja2"][c] is None for c in ATRASO_7D_COLUMNS)


def test_resumo_sem_atraso_tem_as_quatro_chaves_null():
    [r] = transform.build_account_summaries(
        [], AGORA, channel="shopee", refresh_batch_id="l",
        accounts={"1": ("loja1", "apice")}, watermarks={}, source_advanced=False,
    )
    assert all(r[c] is None for c in ATRASO_7D_COLUMNS)


# ===========================================================================
# Publicador (fake)
# ===========================================================================
class _CursorPub:
    def __init__(self, conn):
        self.conn = conn
        self._r = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.sqls.append((sql, params))
        self._r = []
        if "information_schema.columns" in sql:
            tabela = f"{params[0]}.{params[1]}"
            self._r = [{"column_name": c}
                       for c in self.conn.colunas.get(tabela, ()) if c in params[2]]

    def fetchall(self):
        return list(self._r)


class _ConnPub:
    def __init__(self, colunas=None):
        self.autocommit = False
        self.colunas = colunas or {}
        self.sqls = []
        self.commits = self.rollbacks = 0

    def cursor(self):
        return _CursorPub(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _ev(cur, sql, linhas):
    cur.conn.sqls.append((sql, linhas))


def _publicar(conn, atraso=None, destino=publisher.DESTINO_PADRAO):
    resumos = transform.build_account_summaries(
        [], AGORA, channel="shopee", refresh_batch_id="l",
        accounts={"1": ("loja1", "apice")}, watermarks={"1": AGORA},
        source_advanced=True, atraso_7d=atraso,
    )
    return publisher.publish_channel(
        conn, Channel.SHOPEE, [], resumos, source_health=SourceHealth.HEALTHY,
        refresh_batch_id="l", expected_accounts=frozenset({"loja1"}),
        execute_values=_ev, destino=destino,
    )


def _insert_resumo(conn):
    [(sql, linhas)] = [(s, p) for s, p in conn.sqls if s.startswith("INSERT INTO")]
    return sql, linhas


V = atraso_7d.valores(40, 3, DESDE, ATE, over_48h=5, stalled=2)


def test_publicador_sem_a_035_escreve_byte_a_byte_como_antes():
    conn = _ConnPub()
    _publicar(conn, {"1": V})
    sql, linhas = _insert_resumo(conn)
    assert sql == (
        f"INSERT INTO {RUN_TABLE} ({', '.join(publisher.SUMMARY_COLUMNS)}) VALUES %s "
        + publisher.SUMMARY_CONFLICT_SQL
    )
    assert all(len(x) == len(publisher.SUMMARY_COLUMNS) for x in linhas)
    assert conn.commits == 1


def test_publicador_com_parte_das_colunas_nao_grava_nenhuma():
    conn = _ConnPub({RUN_TABLE: ATRASO_7D_COLUMNS[:3]})
    _publicar(conn, {"1": V})
    sql, _ = _insert_resumo(conn)
    assert "due_7d_count" not in sql


def test_publicador_so_com_as_quatro_antigas_nao_grava_nenhuma():
    """Tudo ou nada: destino com a 035 antiga (sem as duas novas) publica o
    resumo de antes, em vez de esconder a falta das duas colunas."""
    conn = _ConnPub({RUN_TABLE: ATRASO_7D_COLUMNS[:4]})
    _publicar(conn, {"1": V})
    sql, linhas = _insert_resumo(conn)
    assert "due_7d_count" not in sql and "over_48h_7d_count" not in sql
    assert all(len(x) == len(publisher.SUMMARY_COLUMNS) for x in linhas)


def test_publicador_com_a_035_grava_e_atualiza_as_seis():
    conn = _ConnPub({RUN_TABLE: ATRASO_7D_COLUMNS})
    relatorio: dict = {}
    publisher.publish_channel(
        conn, Channel.SHOPEE, [],
        transform.build_account_summaries(
            [], AGORA, channel="shopee", refresh_batch_id="l",
            accounts={"1": ("loja1", "apice")}, watermarks={"1": AGORA},
            source_advanced=True, atraso_7d={"1": V},
        ),
        source_health=SourceHealth.HEALTHY, refresh_batch_id="l",
        expected_accounts=frozenset({"loja1"}), execute_values=_ev,
        relatorio=relatorio,
    )
    sql, [linha] = _insert_resumo(conn)
    colunas = publisher.SUMMARY_COLUMNS + ATRASO_7D_COLUMNS
    assert f"({', '.join(colunas)})" in sql
    assert dict(zip(colunas, linha))["due_7d_count"] == 40
    assert linha[-6:] == (40, 3, DESDE, ATE, 5, 2)
    assert ATRASO_7D_COLUMNS[-2:] == ("over_48h_7d_count", "stalled_7d_count")
    for c in ATRASO_7D_COLUMNS:
        assert f"{c} = EXCLUDED.{c}," in sql
    assert "WHERE EXCLUDED.observed_at >" in sql
    assert relatorio["atraso_7d_colunas_escritas"] == ATRASO_7D_COLUMNS


def test_publicador_com_a_035_grava_null_quando_nao_calculado():
    conn = _ConnPub({RUN_TABLE: ATRASO_7D_COLUMNS})
    _publicar(conn, None)
    _sql, [linha] = _insert_resumo(conn)
    assert linha[-6:] == (None,) * 6


def test_publicador_detecta_no_resumo_do_destino_e_o_export_nao_tem_as_colunas():
    """A sonda e' no resumo DO DESTINO: sem as colunas no export (banco antes
    da 035 nova), o INSERT do export e' o de antes."""
    conn = _ConnPub({RUN_TABLE: ATRASO_7D_COLUMNS})
    destino = publisher.Destino(
        fila="marts.expedicao_fila_export", resumo=RUN_EXPORT_TABLE,
        contas=("loja1",),
    )
    _publicar(conn, {"1": V}, destino=destino)
    sql, linhas = _insert_resumo(conn)
    assert "due_7d_count" not in sql
    assert all(len(x) == len(publisher.SUMMARY_COLUMNS) for x in linhas)
    consultas = [p for s, p in conn.sqls if "information_schema" in s]
    assert consultas[0][:2] == ("marts", "expedicao_refresh_run_export")


def test_conflito_sem_extras_e_identico_ao_de_sempre():
    assert publisher.summary_conflict_sql("expedicao_refresh_run") == (
        publisher.SUMMARY_CONFLICT_SQL)


@pytest.mark.parametrize("ruim", [
    {"due_7d_count": 5, "late_7d_count": None,
     "late_7d_window_start": DESDE, "late_7d_window_end": ATE},
    {"due_7d_count": 5, "late_7d_count": 6,
     "late_7d_window_start": DESDE, "late_7d_window_end": ATE},
    {"due_7d_count": 0, "late_7d_count": 0,
     "late_7d_window_start": DESDE, "late_7d_window_end": ATE},
    {"due_7d_count": 5, "late_7d_count": 1,
     "late_7d_window_start": ATE, "late_7d_window_end": DESDE},
    {"due_7d_count": True, "late_7d_count": 0,
     "late_7d_window_start": DESDE, "late_7d_window_end": ATE},
    # Acima de 48 h / travados: dentro da base, inteiros, nunca sem a base.
    {"due_7d_count": 5, "late_7d_count": 1,
     "late_7d_window_start": DESDE, "late_7d_window_end": ATE,
     "over_48h_7d_count": 6},
    {"due_7d_count": 5, "late_7d_count": 1,
     "late_7d_window_start": DESDE, "late_7d_window_end": ATE,
     "stalled_7d_count": -1},
    {"due_7d_count": 5, "late_7d_count": 1,
     "late_7d_window_start": DESDE, "late_7d_window_end": ATE,
     "stalled_7d_count": True},
    {"over_48h_7d_count": 3},
])
def test_publicador_recusa_atraso_incoerente_antes_do_delete(ruim):
    conn = _ConnPub({RUN_TABLE: ATRASO_7D_COLUMNS})
    with pytest.raises(ValueError, match="7 dias"):
        _publicar(conn, {"1": ruim})
    assert not [s for s, _ in conn.sqls if s.startswith("DELETE")]


# ===========================================================================
# CLI: `--apply` e diagnostico
# ===========================================================================
def _cenario_shopee(vencimentos, colunas_7d=ATRASO_7D_COLUMNS):
    from pipelines.tests.test_expedicao_apply_orquestracao import Cenario

    c = Cenario()
    c.vencimentos_7d = vencimentos
    c.colunas_etapa = list(colunas_7d)
    return c


def _vencimento(shop_id, conta, dia, **kw):
    linha = {
        "shop_id": shop_id, "shop_account": conta, "order_status": "READY_TO_SHIP",
        "fulfillment_flag": shopee_ldr.FLUXO_VENDEDOR,
        "pay_time": em(dia - timedelta(days=2), 10), "ship_by_date": prazo(dia),
        "pickup_done_time": None, "cancel_reason": None,
    }
    linha.update(kw)
    return linha


def test_apply_shopee_grava_o_atraso_de_7_dias_por_conta():
    from pipelines.tests.test_expedicao_apply_orquestracao import AGORA as AG

    dia = AG.astimezone(BRT).date() - timedelta(days=3)
    c = _cenario_shopee([
        _vencimento("1609671923", "apice", dia),
        _vencimento("1609671923", "apice", dia, pickup_done_time=em(dia, 11)),
    ])
    assert c.rodar() == cli.EXIT_OK
    colunas = publisher.SUMMARY_COLUMNS + ATRASO_7D_COLUMNS
    por_conta = {
        dict(zip(colunas, x))["shop_account"]: dict(zip(colunas, x))
        for x in c.enviadas["insert_resumo"]
    }
    hoje = AG.astimezone(BRT).date()
    # Pagos 2 dias antes do prazo: o aberto e o coletado (49 h) passam de
    # 48 h. O fake devolve baseline vazio: travados NULL.
    assert {k: por_conta["apice"][k] for k in ATRASO_7D_COLUMNS} == {
        "due_7d_count": 2, "late_7d_count": 1,
        "late_7d_window_start": hoje - timedelta(days=7),
        "late_7d_window_end": hoje - timedelta(days=1),
        "over_48h_7d_count": 2, "stalled_7d_count": None,
    }
    # Conta sem vencimento na janela: NULL nas quatro, nunca zero.
    assert all(por_conta["barbours"][k] is None for k in ATRASO_7D_COLUMNS)
    assert not [m for m in c.mensagens if "7 dias" in m]


def test_apply_shopee_falha_do_calculo_nao_derruba_a_fila():
    from pipelines.tests.test_expedicao_apply_orquestracao import AGORA as AG

    dia = AG.astimezone(BRT).date() - timedelta(days=3)
    c = _cenario_shopee([
        _vencimento("1609671923", "apice", dia, fulfillment_flag="fluxo_novo"),
    ])
    assert c.rodar() == cli.EXIT_OK
    assert c.target.commits == 1
    assert any("7 dias" in m and "NAO calculado" in m for m in c.mensagens)
    colunas = publisher.SUMMARY_COLUMNS + ATRASO_7D_COLUMNS
    for x in c.enviadas["insert_resumo"]:
        assert dict(zip(colunas, x))["due_7d_count"] is None


def test_apply_shopee_sem_a_035_publica_o_resumo_de_antes():
    from pipelines.tests.test_expedicao_apply_orquestracao import AGORA as AG

    dia = AG.astimezone(BRT).date() - timedelta(days=3)
    c = _cenario_shopee([_vencimento("1609671923", "apice", dia)], colunas_7d=())
    assert c.rodar() == cli.EXIT_OK
    assert all(len(x) == len(publisher.SUMMARY_COLUMNS)
               for x in c.enviadas["insert_resumo"])


def test_adaptadores_de_shopee_e_ml_calculam_o_atraso():
    assert cli.ADAPTADORES[Channel.SHOPEE].atraso_7d is not None
    assert cli.ADAPTADORES[Channel.MERCADOLIVRE].atraso_7d is not None


def test_diagnostico_mostra_o_atraso_por_conta():
    resultado = {
        "effective_at": AGORA,
        "atraso_7d": {"contas": {"2532564723": atraso_7d.valores(
            200, 7, date(2026, 11, 5), ATE)}},
    }
    texto = "\n".join(cli.format_atraso_7d(resultado))
    assert "ATRASO DE 7 DIAS" in texto
    assert "2026-11-03 a 2026-11-09" in texto       # nominal
    assert "2026-11-05 a 2026-11-09" in texto       # usada
    assert "200" in texto and "3.50%" in texto


def test_diagnostico_diz_quando_nao_calculou():
    texto = "\n".join(cli.format_atraso_7d(
        {"effective_at": AGORA, "atraso_7d": {"erro": "SLA sem fuso"}}))
    assert "NAO CALCULADO" in texto and "SLA sem fuso" in texto
    texto = "\n".join(cli.format_atraso_7d(
        {"effective_at": AGORA, "atraso_7d": {"contas": {}}}))
    assert "publicaria NULL" in texto


def test_format_diagnose_ml_inclui_o_bloco():
    resultado = {
        "effective_at": AGORA,
        "diagnostico": {
            "candidate_count": 0, "seller_managed_count": 0,
            "fulfillment_excluded_count": 0, "stale_source_record_count": 0,
            "unmapped_logistic_type_count": 0, "queue_count": 0,
        },
        "por_conta": {}, "watermarks": {},
        "atraso_7d": {"contas": {"1": atraso_7d.valores(4, 1, DESDE, ATE)}},
    }
    assert "ATRASO DE 7 DIAS" in cli.format_diagnose_ml(resultado)


# ===========================================================================
# Migration 035
# ===========================================================================
def _meta(caminho: Path) -> dict:
    fora = {}
    for no in ast.parse(caminho.read_text(encoding="utf-8")).body:
        if isinstance(no, ast.Assign) and len(no.targets) == 1:
            alvo = no.targets[0]
            if isinstance(alvo, ast.Name) and isinstance(no.value, ast.Constant):
                fora[alvo.id] = no.value.value
    return fora


def test_035_aponta_para_034():
    meta = _meta(ARQ_035)
    assert meta["revision"] == "035"
    assert meta["down_revision"] == "034"
    assert meta["branch_labels"] is None
    assert meta["depends_on"] is None


def test_035_tem_as_colunas_do_contrato_e_e_idempotente_no_texto():
    from pipelines.tests.test_expedicao_etapa_integracao import (
        _constantes_da_migration,
    )

    consts = _constantes_da_migration(ARQ_035)
    assert tuple(n for n, _t in consts["COLUNAS"]) == ATRASO_7D_COLUMNS
    assert dict(consts["COLUNAS"]) == {
        "due_7d_count": "INTEGER", "late_7d_count": "INTEGER",
        "late_7d_window_start": "DATE", "late_7d_window_end": "DATE",
        "over_48h_7d_count": "INTEGER", "stalled_7d_count": "INTEGER",
    }
    # O upgrade itera as DUAS tabelas de resumo (Data Mart e export).
    assert consts["RESUMO_Q"] == RUN_TABLE
    assert consts["RESUMO_EXPORT_Q"] == RUN_EXPORT_TABLE
    fonte = ARQ_035.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS" in fonte
    assert "DROP COLUMN IF EXISTS" in fonte
    assert "DEFAULT" not in fonte.split('"""', 2)[2]  # nada de default no codigo


@pytest.fixture
def banco_034():
    """018 + 034 aplicadas pelo `op` real do alembic (sem a 035)."""
    import psycopg2

    from pipelines.tests.test_expedicao_etapa_integracao import (
        ARQ_018, DDL_BASE, _url_db, rodar_migration,
    )

    with cluster_da_sessao() as base:
        adm = psycopg2.connect(base)
        adm.autocommit = True
        with adm.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS atraso7d_alvo WITH (FORCE)")
            cur.execute("CREATE DATABASE atraso7d_alvo")
        adm.close()
        url = _url_db(base, "atraso7d_alvo")
    conn = psycopg2.connect(url)
    with conn, conn.cursor() as cur:
        for stmt in DDL_BASE:
            cur.execute(stmt)
        cur.execute("INSERT INTO marts.dim_loja VALUES (3, 'apice')")
    conn.close()
    rodar_migration(url, ARQ_018, "upgrade")
    rodar_migration(url, VERSIONS / "034_expedicao_shopee_export.py", "upgrade")
    return url


def _colunas(url, tabela="expedicao_refresh_run"):
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, column_default"
                " FROM information_schema.columns"
                " WHERE table_schema = 'marts' AND table_name = %s",
                (tabela,),
            )
            return {r[0]: r[1:] for r in cur.fetchall()}
    finally:
        conn.close()


def _rodar_035(url, funcao):
    from pipelines.tests.test_expedicao_etapa_integracao import rodar_migration

    rodar_migration(url, ARQ_035, funcao)


@sem_postgres
def test_035_upgrade_idempotente_cria_seis_colunas_nullable(banco_034):
    assert not set(ATRASO_7D_COLUMNS) & set(_colunas(banco_034))
    _rodar_035(banco_034, "upgrade")
    _rodar_035(banco_034, "upgrade")  # reaplicar nao falha
    cols = _colunas(banco_034)
    assert {c: cols[c] for c in ATRASO_7D_COLUMNS} == {
        "due_7d_count": ("integer", "YES", None),
        "late_7d_count": ("integer", "YES", None),
        "late_7d_window_start": ("date", "YES", None),
        "late_7d_window_end": ("date", "YES", None),
        "over_48h_7d_count": ("integer", "YES", None),
        "stalled_7d_count": ("integer", "YES", None),
    }
    # O resumo do export (034) recebe as MESMAS colunas (Kokeshi com %).
    export = _colunas(banco_034, "expedicao_refresh_run_export")
    assert {c: export[c] for c in ATRASO_7D_COLUMNS} == {c: cols[c] for c in ATRASO_7D_COLUMNS}
    _rodar_035(banco_034, "downgrade")
    _rodar_035(banco_034, "downgrade")  # idem
    assert not set(ATRASO_7D_COLUMNS) & set(_colunas(banco_034))


def _publicar_real(url, observado, atraso):
    import psycopg2
    from psycopg2.extras import RealDictCursor, execute_values

    resumos = transform.build_account_summaries(
        [], observado, channel="shopee", refresh_batch_id="l",
        accounts={"1": ("loja1", "apice")}, watermarks={"1": observado},
        source_advanced=True, atraso_7d=atraso,
    )
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    try:
        relatorio: dict = {}
        publisher.publish_channel(
            conn, Channel.SHOPEE, [], resumos, source_health=SourceHealth.HEALTHY,
            refresh_batch_id="l", expected_accounts=frozenset({"loja1"}),
            execute_values=execute_values, relatorio=relatorio,
        )
        return relatorio
    finally:
        conn.close()


def _resumo_publicado(url):
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {RUN_TABLE}")
            nomes = [d[0] for d in cur.description]
            return [dict(zip(nomes, r)) for r in cur.fetchall()]
    finally:
        conn.close()


@sem_postgres
def test_publicador_real_sem_e_com_a_035_e_upsert_da_hora(banco_034):
    # Sem a 035: publica como hoje.
    rel = _publicar_real(banco_034, AGORA, {"1": V})
    assert rel["atraso_7d_colunas_escritas"] == ()
    [linha] = _resumo_publicado(banco_034)
    assert "due_7d_count" not in linha

    # Com a 035: a linha antiga fica NULL; a reexecucao da hora atualiza.
    _rodar_035(banco_034, "upgrade")
    [linha] = _resumo_publicado(banco_034)
    assert all(linha[c] is None for c in ATRASO_7D_COLUMNS)

    rel = _publicar_real(banco_034, AGORA + timedelta(minutes=10), {"1": V})
    assert rel["atraso_7d_colunas_escritas"] == ATRASO_7D_COLUMNS
    [linha] = _resumo_publicado(banco_034)
    assert {c: linha[c] for c in ATRASO_7D_COLUMNS} == V

    # Observacao mais nova sem calculo: NULL vence (nao fica o valor velho).
    _publicar_real(banco_034, AGORA + timedelta(minutes=20), None)
    [linha] = _resumo_publicado(banco_034)
    assert all(linha[c] is None for c in ATRASO_7D_COLUMNS)

    # Observacao mais ANTIGA nao sobrescreve.
    _publicar_real(banco_034, AGORA + timedelta(minutes=5), {"1": V})
    [linha] = _resumo_publicado(banco_034)
    assert all(linha[c] is None for c in ATRASO_7D_COLUMNS)


# ---------------------------------------------------------------------------
# Kokeshi pelo export do Seller Centre (mesma regra da Shopee)
# ---------------------------------------------------------------------------
def test_kokeshi_pelo_export_usa_a_regra_da_shopee():
    """03/11/2026: a Kokeshi saia sem % porque so' existe pelo export. O
    historico de pedidos do export tem prazo, coleta, Full e cancelamento: a
    MESMA apuracao da Shopee do Data Mart vale aqui."""
    from pipelines.expedicao import shopee_export_cli as C
    from pipelines.expedicao.contract import SellerAccount

    conta = SellerAccount("kokeshi-sid", 4, "kokeshi")
    agora = datetime(2026, 11, 3, 19, 0, tzinfo=UTC)
    baixado = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
    pago = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)

    def prazo(dia):  # 23:59 de Brasilia do dia
        return datetime(2026, 11 if dia < 10 else 10, dia, 23, 59,
                        tzinfo=timezone(timedelta(hours=-3))).astimezone(UTC)

    def pedido(status, vence, coleta=None, fbs="fulfilled_by_local_seller"):
        return {"shop_id": "kokeshi-sid", "shop_account": "kokeshi",
                "order_status": status, "fulfillment_flag": fbs, "pay_time": pago,
                "ship_by_date": vence, "pickup_done_time": coleta, "cancel_reason": None}

    estado = [
        pedido("COMPLETED", prazo(30), coleta=datetime(2026, 10, 30, 15, 0, tzinfo=UTC)),
        pedido("SHIPPED", prazo(31), coleta=datetime(2026, 10, 31, 20, 0, tzinfo=UTC)),
        pedido("SHIPPED", prazo(30), coleta=datetime(2026, 10, 31, 12, 0, tzinfo=UTC)),  # atrasado
        pedido("READY_TO_SHIP", prazo(1)),                                               # vencido sem coleta
        pedido("COMPLETED", prazo(30), coleta=datetime(2026, 10, 30, 15, 0, tzinfo=UTC),
               fbs="fulfilled_by_shopee"),                                               # Full: fora
        pedido(None, prazo(30), coleta=datetime(2026, 10, 30, 15, 0, tzinfo=UTC)),      # "devolucao ate...": entra pela coleta
        pedido("READY_TO_SHIP", prazo(3)),                                               # vence hoje: fora da janela
    ]
    r = C.atraso_7d_do_export(estado, conta, baixado, agora)
    # Pagos em 28/10: os cinco da base passaram de 48 h. Sem `create_time` o
    # baseline do export nao fecha: travados NULL, e o resto continua.
    assert r == {"kokeshi-sid": {
        "due_7d_count": 5, "late_7d_count": 2,
        "late_7d_window_start": date(2026, 10, 27),
        "late_7d_window_end": date(2026, 11, 2),
        "over_48h_7d_count": 5, "stalled_7d_count": None,
    }}


def test_kokeshi_falha_no_calculo_nao_derruba_a_fila():
    from pipelines.expedicao import shopee_export_cli as C
    from pipelines.expedicao.contract import SellerAccount

    quebrado = [{"order_status": "COMPLETED"}]  # sem nenhuma data
    conta = SellerAccount("k", 4, "kokeshi")
    agora = datetime(2026, 11, 3, 19, 0, tzinfo=UTC)
    assert C.atraso_7d_do_export(quebrado, conta, agora, agora) in ({}, {})


# ===========================================================================
# Acima de 48 h e travados (aba "Ultimos 7 dias") — MESMA base do atraso
# ===========================================================================
#: Pago em 04/11 10:00 BRT; prazo em 05/11 (dentro da janela 03/11 a 09/11).
PAGO = em(date(2026, 11, 4), 10)


def ped_pago(dia, pago, *, coleta=None, status="PROCESSED", shop="1"):
    return shopee_ldr.Pedido(
        shop_id=shop, shop_account=f"loja{shop}", order_status=status,
        fulfillment_flag=shopee_ldr.FLUXO_VENDEDOR, pay_time=pago,
        ship_by_date=prazo(dia), pickup_done_time=coleta,
    )


def _pedidos_de_duracao(shop="1"):
    """Cinco pedidos da base, com duracoes conhecidas."""
    return [
        ped_pago(D, PAGO, coleta=PAGO + timedelta(hours=49), shop=shop),   # 49 h
        ped_pago(D, PAGO, coleta=PAGO + timedelta(hours=47), shop=shop),   # 47 h
        ped_pago(D, PAGO, coleta=PAGO + timedelta(hours=48), shop=shop),   # 48 h exatas
        # Aberto, pago em 04/11: ate' agora (10/11 12:00 BRT) sao 146 h.
        ped_pago(date(2026, 11, 6), PAGO, status="READY_TO_SHIP", shop=shop),
        # Aberto, pago ontem 13:00 BRT: 23 h ate' agora.
        ped_pago(date(2026, 11, 9), em(date(2026, 11, 9), 13),
                 status="READY_TO_SHIP", shop=shop),
    ]


def test_shopee_48h_pela_coleta_e_pelo_relogio_estritamente_acima():
    r = atraso_7d.apurar_shopee(_pedidos_de_duracao(), {"1": AGORA}, AGORA)["1"]
    assert r["due_7d_count"] == 5
    # 49 h coletado e 146 h aberto passam; 47 h e 48 h exatas nao (regra
    # "mais de dois dias", como `transform.classify_age`); 23 h aberto nao.
    assert r["over_48h_7d_count"] == 2


def test_shopee_48h_fora_da_base_nao_conta():
    """FBS, cancelado sem coleta e pos-envio sem coleta ficam fora da base do
    atraso; as 48 h usam a MESMA base."""
    velho = em(date(2026, 10, 25), 10)
    pedidos = [
        ped_pago(D, PAGO, coleta=PAGO + timedelta(hours=10)),
        ped_pago(D, velho, status="CANCELLED"),
        ped_pago(D, velho, status="SHIPPED"),
        shopee_ldr.Pedido("1", "loja1", "PROCESSED", shopee_ldr.FLUXO_FBS, velho,
                          prazo(D), None),
        ped_pago(date(2026, 11, 10), velho, status="READY_TO_SHIP"),  # vence hoje
    ]
    r = atraso_7d.apurar_shopee(pedidos, {"1": AGORA}, AGORA)["1"]
    assert (r["due_7d_count"], r["over_48h_7d_count"]) == (1, 0)


def test_shopee_travados_com_baseline_usam_2x_p50_da_conta():
    # p50 = 23,75 h -> limite 47,5 h: 49 h, 48 h e o aberto de 146 h travam;
    # 47 h e o aberto de 23 h nao.
    r = atraso_7d.apurar_shopee(
        _pedidos_de_duracao(), {"1": AGORA}, AGORA, {"1": (150, 23.75)})["1"]
    assert r["stalled_7d_count"] == 3
    assert r["over_48h_7d_count"] == 2  # independente do baseline


def test_shopee_travados_zero_medido_so_com_baseline_suficiente():
    r = atraso_7d.apurar_shopee(
        _pedidos_de_duracao(), {"1": AGORA}, AGORA, {"1": (100, 500.0)})["1"]
    assert r["stalled_7d_count"] == 0


@pytest.mark.parametrize("baselines", [
    None,                    # CLI sem baseline
    {},                      # conta sem linha de baseline
    {"1": (99, 10.0)},       # amostra abaixo de MIN_BASELINE_SAMPLE
    {"1": (500, None)},      # sem p50
    {"1": (500, 0.0)},       # p50 nao positivo
    {"2": (500, 10.0)},      # baseline de OUTRA conta
])
def test_shopee_travados_sem_baseline_suficiente_e_null(baselines):
    r = atraso_7d.apurar_shopee(_pedidos_de_duracao(), {"1": AGORA}, AGORA, baselines)
    assert r["1"]["stalled_7d_count"] is None
    assert r["1"]["over_48h_7d_count"] == 2


def test_shopee_baseline_por_conta():
    pedidos = _pedidos_de_duracao("1") + _pedidos_de_duracao("2")
    r = atraso_7d.apurar_shopee(pedidos, {"1": AGORA, "2": AGORA}, AGORA,
                                {"1": (150, 23.75), "2": (20, 23.75)})
    assert r["1"]["stalled_7d_count"] == 3
    assert r["2"]["stalled_7d_count"] is None


def test_calcular_shopee_repassa_o_baseline():
    linha = {
        "shop_id": "1", "shop_account": "loja1", "order_status": "READY_TO_SHIP",
        "fulfillment_flag": shopee_ldr.FLUXO_VENDEDOR, "pay_time": em(D, 9),
        "ship_by_date": prazo(D), "pickup_done_time": None, "cancel_reason": None,
    }
    r = atraso_7d.calcular_shopee(_ConnGravadora([linha]), AGORA, {"1": AGORA},
                                  {"1": (200, 5.0)})
    assert r["1"]["over_48h_7d_count"] == 1
    assert r["1"]["stalled_7d_count"] == 1


def test_valores_validam_48h_e_travados():
    assert atraso_7d.valores(3, 1, DESDE, ATE)["over_48h_7d_count"] is None
    assert atraso_7d.valores(3, 1, DESDE, ATE, 3, 0)["stalled_7d_count"] == 0
    with pytest.raises(ValueError):
        atraso_7d.valores(3, 1, DESDE, ATE, over_48h=4)
    with pytest.raises(ValueError):
        atraso_7d.valores(3, 1, DESDE, ATE, stalled=-1)
    with pytest.raises(ValueError):
        atraso_7d.valores(3, 1, DESDE, ATE, over_48h=True)
    assert tuple(atraso_7d.vazio()) == ATRASO_7D_COLUMNS
    assert len(ATRASO_7D_COLUMNS) == 6


# ---------------------------------------------------------------------------
# Mercado Livre — 48 h e travados
# ---------------------------------------------------------------------------
def test_ml_travados_sempre_null_e_48h_do_agregado():
    conn = _ConnML(_inicio(em(date(2026, 11, 1), 14)),
                   [_agregado("1", 10, 2, over_48h=4), _agregado("2", 3, 0)])
    r = atraso_7d.calcular_ml(conn, AGORA)
    assert r["1"]["over_48h_7d_count"] == 4
    assert r["2"]["over_48h_7d_count"] == 0  # zero MEDIDO, base > 0
    assert r["1"]["stalled_7d_count"] is None
    assert r["2"]["stalled_7d_count"] is None


def test_ml_historico_ilegivel_deixa_48h_null_sem_derrubar_o_atraso(caplog):
    conn = _ConnML(_inicio(em(date(2026, 11, 1), 14)),
                   [_agregado("1", 10, 2, over_48h=9, historico_invalido=1)])
    r = atraso_7d.calcular_ml(conn, AGORA)
    assert r["1"]["due_7d_count"] == 10 and r["1"]["late_7d_count"] == 2
    assert r["1"]["over_48h_7d_count"] is None
    assert "substatus_history" in caplog.text


def test_ml_sql_48h_le_coleta_e_marco_com_os_fusos_por_coluna():
    sql = atraso_7d.ML_ATRASO_7D_SQL
    # Historico lido como jsonb, sem exigir a coluna (como o SLA).
    assert "to_jsonb(s) -> 'substatus_history'" in sql
    # Marco: prontidao, senao criacao do PEDIDO, ambos naive -04:00.
    assert "(date_ready_to_ship - %(offset_negocio)s) AT TIME ZONE 'UTC'" in sql
    assert "(pedido_criado - %(offset_negocio)s) AT TIME ZONE 'UTC'" in sql
    assert "o.date_created" in sql
    # Coleta: historico, senao `date_shipped` naive -04:00.
    assert "(date_shipped - %(offset_negocio)s) AT TIME ZONE 'UTC'" in sql
    assert "e ->> 'substatus' = ANY(%(coletados)s)" in sql
    assert "e ->> 'status' = ANY(%(enviados)s)" in sql
    # Nenhum cast de data do historico sem passar no formato com fuso.
    assert "THEN (e ->> 'date')::timestamptz" in sql
    assert "COALESCE(coleta, %(agora)s) - marco > %(limite_idade)s" in sql
    # Base: o mesmo `FROM base` alimenta as quatro contagens.
    assert sql.count("FROM raw.ml_shipments") == 1


@sem_postgres
def test_ml_sql_48h_contra_postgres_real():
    url = _banco("atraso7d_ml_48h", SLA_TZ)
    d6, d9 = date(2026, 11, 6), date(2026, 11, 9)
    pronto = datetime(2026, 11, 4, 10, 0)  # naive -04:00 = 04/11 14:00 UTC

    def hist(*entradas):
        return [{"date": d, "status": st, "substatus": sub} for d, st, sub in entradas]

    _popular(url, [
        # a) coleta pelo historico (1a entrada `picked_up`) em 49 h: conta.
        dict(sla=em(d6, 14), pronto=pronto, historico=hist(
            ("2026-11-05T09:00:00.000-04:00", "ready_to_ship", "ready_for_pickup"),
            ("2026-11-06T11:00:00.000-04:00", "ready_to_ship", "picked_up"),
            ("2026-11-07T08:00:00.000-04:00", "shipped", None))),
        # b) historico em 47 h vence o `date_shipped` (que daria 96 h): nao conta.
        dict(sla=em(d6, 14), pronto=pronto, enviado=datetime(2026, 11, 8, 10, 0),
             historico=hist(("2026-11-06T09:00:00.000-04:00", "ready_to_ship",
                             "picked_up"))),
        # c) `date_ready_to_ship` em -04:00: 12:30 naive = 16:30 UTC; coleta
        #    16:00 UTC de 06/11 = 47,5 h. Lido como UTC dariam 51,5 h.
        dict(sla=em(d6, 14), pronto=datetime(2026, 11, 4, 12, 30),
             historico=hist(("2026-11-06T16:00:00+00:00", "ready_to_ship", "in_hub"))),
        # d) sem historico: `date_shipped` naive -04:00 em 49 h: conta.
        dict(sla=em(d6, 14), pronto=pronto, enviado=datetime(2026, 11, 6, 11, 0)),
        # e) aberto, pronto ha' 27 h: nao conta (mas atrasa: dia fechado).
        dict(sla=em(d9, 14), pronto=datetime(2026, 11, 9, 8, 0),
             status="ready_to_ship", substatus="ready_for_pickup"),
        # f) sem `date_ready_to_ship`: marco = criacao do pedido (01/11), aberto.
        dict(sla=em(d6, 14)),
        # g) status `delivered` no historico em 24 h: coletado, nao conta.
        dict(sla=em(d6, 14), pronto=pronto, historico=hist(
            ("2026-11-05T10:00:00-04:00", "delivered", None))),
        # Conta 222: historico que nao e' array -> 48 h NULL, atraso segue.
        dict(seller=222, sla=em(d6, 14), pronto=pronto, historico="texto"),
        # Conta 333: entrada de coleta com data SEM fuso -> 48 h NULL.
        dict(seller=333, sla=em(d6, 14), pronto=pronto, historico=hist(
            ("2026-11-06T11:00:00", "ready_to_ship", "picked_up"))),
    ])
    janela = {"late_7d_window_start": d6, "late_7d_window_end": ATE}
    assert _calcular(url) == {
        "111": {"due_7d_count": 7, "late_7d_count": 1, **janela,
                "over_48h_7d_count": 3, "stalled_7d_count": None},
        "222": {"due_7d_count": 1, "late_7d_count": 0, **janela,
                "over_48h_7d_count": None, "stalled_7d_count": None},
        "333": {"due_7d_count": 1, "late_7d_count": 0, **janela,
                "over_48h_7d_count": None, "stalled_7d_count": None},
    }


@sem_postgres
def test_ml_sql_48h_roda_sem_a_coluna_de_historico():
    url = _banco("atraso7d_ml_sem_hist", SLA_TZ, hist="")
    _popular(url, [
        dict(sla=em(date(2026, 11, 6), 14), pronto=datetime(2026, 11, 4, 10, 0),
             enviado=datetime(2026, 11, 6, 11, 0), com_historico=False),
    ])
    r = _calcular(url)
    assert r["111"]["over_48h_7d_count"] == 1
    assert r["111"]["stalled_7d_count"] is None


# ---------------------------------------------------------------------------
# CLI: um baseline so' para a fila e para os travados
# ---------------------------------------------------------------------------
def test_apply_shopee_le_o_baseline_uma_vez_e_grava_travados():
    from pipelines.tests.test_expedicao_apply_orquestracao import AGORA as AG

    dia = AG.astimezone(BRT).date() - timedelta(days=3)
    c = _cenario_shopee([
        _vencimento("1609671923", "apice", dia),
        _vencimento("1609671923", "apice", dia, pickup_done_time=em(dia, 11)),
    ])
    roteador = c.source._roteador

    def com_baseline(conn, sql, params):
        if "percentile_cont" in sql:
            roteador(conn, sql, params)  # registra a etapa no roteiro
            return [{"shop_id": "1609671923", "sample_size": 300, "p50_hours": 1.0}]
        return roteador(conn, sql, params)

    c.source._roteador = com_baseline
    assert c.rodar() == cli.EXIT_OK
    assert c.ordem.count("baseline") == 1
    colunas = publisher.SUMMARY_COLUMNS + ATRASO_7D_COLUMNS
    por_conta = {dict(zip(colunas, x))["shop_account"]: dict(zip(colunas, x))
                 for x in c.enviadas["insert_resumo"]}
    assert por_conta["apice"]["over_48h_7d_count"] == 2
    assert por_conta["apice"]["stalled_7d_count"] == 2   # p50 1 h: ambos > 2 h
    # Conta sem vencimento na janela: tudo NULL.
    assert all(por_conta["barbours"][k] is None for k in ATRASO_7D_COLUMNS)


def test_diagnostico_mostra_48h_e_travados_e_traco_quando_null():
    resultado = {
        "effective_at": AGORA,
        "atraso_7d": {"contas": {
            "1": atraso_7d.valores(200, 7, DESDE, ATE, over_48h=31, stalled=4),
            "2": atraso_7d.valores(50, 1, DESDE, ATE, over_48h=9),
        }},
    }
    texto = "\n".join(cli.format_atraso_7d(resultado))
    assert ">48h" in texto and "travad" in texto
    linha_1 = next(x for x in texto.splitlines() if x.strip().startswith("1 "))
    linha_2 = next(x for x in texto.splitlines() if x.strip().startswith("2 "))
    assert linha_1.split()[-2:] == ["31", "4"]
    assert linha_2.split()[-2:] == ["9", "-"]


# ---------------------------------------------------------------------------
# Kokeshi pelo export: 48 h e travados pelo baseline do export
# ---------------------------------------------------------------------------
def test_kokeshi_48h_e_travados_pelo_export():
    from pipelines.expedicao import shopee_export as E
    from pipelines.expedicao import shopee_export_cli as C

    conta = SellerAccount("kokeshi-sid", 4, "kokeshi")
    agora = datetime(2026, 11, 3, 19, 0, tzinfo=UTC)
    baixado = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)

    def utc(dia, hora, mes=10):
        return datetime(2026, mes, dia, hora, 0, tzinfo=UTC)

    def pedido(status, pago, vence=None, coleta=None):
        return {"shop_id": "kokeshi-sid", "shop_account": "kokeshi",
                "order_status": status, "fulfillment_flag": "fulfilled_by_local_seller",
                "create_time": pago, "pay_time": pago, "ship_by_date": vence,
                "pickup_done_time": coleta, "cancel_reason": None}

    vence_31 = datetime(2026, 10, 31, 23, 59, tzinfo=BRT)
    vence_01 = datetime(2026, 11, 1, 23, 59, tzinfo=BRT)
    historico = [  # 120 pedidos sem prazo (fora da base), coletados em 10 h
        pedido("COMPLETED", utc(20, 10), coleta=utc(20, 20)) for _ in range(120)
    ]
    janela = [
        pedido("COMPLETED", utc(30, 12), vence_31, coleta=utc(31, 13)),  # 25 h: trava
        pedido("COMPLETED", utc(30, 12), vence_31, coleta=utc(30, 20)),  # 8 h
        pedido("READY_TO_SHIP", utc(28, 12), vence_01),                  # aberto: 48h+trava
        pedido("COMPLETED", utc(29, 12), vence_31, coleta=utc(31, 13)),  # 49 h: 48h+trava
    ]
    estado = historico + janela
    amostra, p50 = E.baseline(estado, agora)
    assert amostra >= 100 and p50 == pytest.approx(10.0)

    esperado = {
        "due_7d_count": 4, "late_7d_count": 1,
        "late_7d_window_start": date(2026, 10, 27),
        "late_7d_window_end": date(2026, 11, 2),
        "over_48h_7d_count": 2, "stalled_7d_count": 3,
    }
    # Baseline calculado aqui ou o mesmo da fila (`montar_fotografia`).
    assert C.atraso_7d_do_export(estado, conta, baixado, agora) == {
        "kokeshi-sid": esperado}
    assert C.atraso_7d_do_export(estado, conta, baixado, agora, (amostra, p50)) == {
        "kokeshi-sid": esperado}
    # Baseline insuficiente: travados NULL, o resto igual.
    r = C.atraso_7d_do_export(estado, conta, baixado, agora, (50, 10.0))
    assert r["kokeshi-sid"] == {**esperado, "stalled_7d_count": None}


# ---------------------------------------------------------------------------
# 035: as seis colunas na PROPRIA 035; as revisoes posteriores (036 CX-KOKESHI,
# 037 T-0018) sao de outras frentes e nao tocam as tabelas de resumo
# ---------------------------------------------------------------------------
def test_035_tem_as_seis_colunas_e_nenhuma_posterior_as_toca():
    filhos = [
        p.name for p in VERSIONS.glob("*.py")
        if _meta(p).get("down_revision") == "035"
    ]
    assert filhos == ["036_cx_kokeshi_export.py"]  # linear: um unico filho
    posteriores = [p for p in VERSIONS.glob("*.py") if p.name[:3].isdigit() and p.name[:3] > "035"]
    assert posteriores
    for p in posteriores:
        texto = p.read_text(encoding="utf-8")
        for alvo in (RUN_TABLE, RUN_EXPORT_TABLE, *ATRASO_7D_COLUMNS):
            assert alvo not in texto, f"{p.name} toca {alvo}: o atraso de 7 dias e' so' da 035"
    fonte = ARQ_035.read_text(encoding="utf-8")
    assert "over_48h_7d_count" in fonte.split('"""', 2)[1]  # no docstring
    assert "stalled_7d_count" in fonte.split('"""', 2)[1]
