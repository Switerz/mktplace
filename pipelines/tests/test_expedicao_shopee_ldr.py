"""EXPEDICAO-SHOPEE-LDR-1 — LDR operacional da Shopee (coleta x prazo Shopee).

Tres camadas:
  * REGRA — `classificar` e `apurar` sao funcoes puras; cada linha da tabela de
    precedencia do modulo vira caso aqui;
  * PUBLICACAO — transacao, releitura e lock com dublês;
  * INTEGRACAO contra PostgreSQL DE VERDADE (pulada sem servidor): os CHECKs
    da migration 030, o `--apply` ponta a ponta, a idempotencia, o DELETE
    limitado a' janela e a reconciliacao Python x SQL no mesmo snapshot. Um
    dublê concordaria com qualquer coisa; os CHECKs e o CASE moram no servidor.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import shopee_ldr as L
from pipelines.expedicao import shopee_ldr_cli as cli
from pipelines.expedicao.publisher import IndeterminateCommit, LockNotAcquired
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel,
)

UTC = timezone.utc
BRT = L.BRT
REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "apps" / "api" / "alembic" / "versions" / "030_create_expedicao_shopee_ldr_daily.py"

#: Quarta, 21/10/2026, 15:00 BRT.
AGORA = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
HOJE = date(2026, 10, 21)
REGISTRY = {"111": "apice", "222": "barbours"}


def fim_do_dia(d: date) -> datetime:
    """`ship_by_date` real: 100% as 23:59 de Brasilia (medido)."""
    return datetime(d.year, d.month, d.day, 23, 59, tzinfo=BRT)


def ped(**kw) -> L.Pedido:
    base = dict(
        shop_id="111", shop_account="apice", order_status="COMPLETED",
        fulfillment_flag=L.FLUXO_VENDEDOR,
        pay_time=datetime(2026, 10, 13, 12, tzinfo=BRT),
        ship_by_date=fim_do_dia(date(2026, 10, 15)),
        pickup_done_time=datetime(2026, 10, 14, 15, tzinfo=BRT),
        cancel_reason=None,
    )
    base.update(kw)
    return L.Pedido(**base)


# ===========================================================================
# Regra: precedencia
# ===========================================================================
@pytest.mark.parametrize("kw,classe", [
    ({"pay_time": None}, L.Classe.NAO_PAGO),
    ({"fulfillment_flag": L.FLUXO_FBS, "pickup_done_time": None}, L.Classe.FBS),
    # FBS vence o cancelamento: o pedido nunca foi do vendedor.
    ({"fulfillment_flag": L.FLUXO_FBS, "order_status": "CANCELLED",
      "pickup_done_time": None}, L.Classe.FBS),
    ({"order_status": "CANCELLED", "pickup_done_time": None}, L.Classe.CANCELADO_SEM_COLETA),
    ({"order_status": "IN_CANCEL", "pickup_done_time": None}, L.Classe.CANCELADO_SEM_COLETA),
    # Cancelado SEM prazo ainda e' cancelado: a exclusao vem antes do prazo.
    ({"order_status": "CANCELLED", "pickup_done_time": None, "ship_by_date": None},
     L.Classe.CANCELADO_SEM_COLETA),
    ({"ship_by_date": None, "order_status": "PROCESSED", "pickup_done_time": None},
     L.Classe.SEM_PRAZO),
    ({"order_status": "SHIPPED", "pickup_done_time": None}, L.Classe.ENVIADO_SEM_COLETA),
    ({"order_status": "COMPLETED", "pickup_done_time": None}, L.Classe.ENVIADO_SEM_COLETA),
    ({}, L.Classe.NO_PRAZO),
    # Coleta exatamente no prazo e' no prazo.
    ({"pickup_done_time": fim_do_dia(date(2026, 10, 15))}, L.Classe.NO_PRAZO),
    ({"pickup_done_time": datetime(2026, 10, 16, 9, tzinfo=BRT)}, L.Classe.ATRASADO),
    # Cancelado COM coleta entra pela coleta.
    ({"order_status": "CANCELLED", "pickup_done_time": datetime(2026, 10, 16, 9, tzinfo=BRT)},
     L.Classe.ATRASADO),
    ({"order_status": "PROCESSED", "pickup_done_time": None}, L.Classe.PENDENTE_VENCIDO),
    ({"order_status": "READY_TO_SHIP", "pickup_done_time": None,
      "ship_by_date": fim_do_dia(date(2026, 10, 22))}, L.Classe.PENDENTE_NO_PRAZO),
])
def test_precedencia(kw, classe):
    assert L.classificar(ped(**kw), AGORA) is classe


def test_fluxo_fora_do_vocabulario_falha_fechado():
    with pytest.raises(L.FluxoDesconhecido):
        L.classificar(ped(fulfillment_flag="fulfilled_by_cainiao"), AGORA)
    with pytest.raises(L.FluxoDesconhecido):
        L.classificar(ped(fulfillment_flag=None), AGORA)


def test_instante_sem_fuso_recusa():
    with pytest.raises(ValueError, match="sem fuso"):
        L.classificar(ped(ship_by_date=datetime(2026, 10, 15, 23, 59)), AGORA)


def test_dia_de_vencimento_e_a_data_de_brasilia():
    # 23:59 BRT do dia 15 = 02:59 UTC do dia 16: o dia e' 15.
    assert L.data_brt(datetime(2026, 10, 16, 2, 59, tzinfo=UTC)) == date(2026, 10, 15)
    assert L.data_brt(datetime(2026, 10, 16, 3, 0, tzinfo=UTC)) == date(2026, 10, 16)


# ===========================================================================
# Maturidade
# ===========================================================================
def test_maturidade_exige_dia_fechado_e_leitura_apos_a_margem():
    venc = date(2026, 10, 20)
    corte = datetime(2026, 10, 21, 6, 0, tzinfo=BRT)
    assert L.madura(venc, HOJE, corte) is True
    assert L.madura(venc, HOJE, corte - timedelta(minutes=1)) is False
    assert L.madura(venc, HOJE, None) is False, "sem leitura nao prova coleta"
    assert L.madura(HOJE, HOJE, AGORA) is False, "o dia de hoje nunca e' maduro"


# ===========================================================================
# Apuracao
# ===========================================================================
def test_apurar_agrega_e_fecha_a_particao():
    d15 = fim_do_dia(date(2026, 10, 15))
    pedidos = [
        ped(),                                                           # no prazo
        ped(pickup_done_time=datetime(2026, 10, 16, 9, tzinfo=BRT)),     # atrasado
        ped(order_status="PROCESSED", pickup_done_time=None),            # pendente vencido
        ped(fulfillment_flag=L.FLUXO_FBS),                               # FBS
        ped(order_status="CANCELLED", pickup_done_time=None,
            cancel_reason=L.MOTIVO_NAO_ENVIOU),                          # cancelado
        ped(order_status="SHIPPED", pickup_done_time=None),              # sem marco
        ped(pay_time=None),                                              # nao pago: some
    ]
    wm = {"111": AGORA}
    ap = L.apurar(pedidos, registry=REGISTRY, watermarks=wm, agora=AGORA)
    (x,) = ap.vencimentos
    assert (x.deadline_date, x.brand) == (d15.date(), "apice")
    assert (x.pedidos_pagos, x.excl_fbs, x.excl_cancelado_sem_coleta,
            x.excl_enviado_sem_coleta) == (6, 1, 1, 1)
    assert (x.no_prazo, x.atrasado, x.pendente_vencido, x.pendente_no_prazo) == (1, 1, 1, 0)
    assert x.base == 3
    assert x.cancelado_vendedor_nao_enviou == 1
    assert x.is_mature is True


def test_sem_prazo_vai_para_a_tabela_por_pagamento():
    pedidos = [
        ped(ship_by_date=None, order_status="PROCESSED", pickup_done_time=None),
        ped(ship_by_date=None, fulfillment_flag=L.FLUXO_FBS),
        ped(ship_by_date=None, order_status="CANCELLED", pickup_done_time=None),
    ]
    ap = L.apurar(pedidos, registry=REGISTRY, watermarks={}, agora=AGORA)
    assert ap.vencimentos == []
    (s,) = ap.sem_prazo
    assert s.paid_date == date(2026, 10, 13)
    assert (s.pedidos_pagos, s.excl_fbs, s.excl_cancelado_sem_coleta, s.sem_prazo) == (3, 1, 1, 1)


def test_conta_fora_do_registry_nao_e_publicada_e_vira_alerta():
    ap = L.apurar([ped(shop_id="999", shop_account="nova")], registry=REGISTRY,
                  watermarks={}, agora=AGORA)
    assert ap.vencimentos == []
    assert ap.qualidade.contas_fora_do_registry == Counter({"999": 1})


def test_motivo_desconhecido_e_ausente():
    pedidos = [
        ped(order_status="CANCELLED", pickup_done_time=None, cancel_reason="Novo motivo"),
        ped(order_status="CANCELLED", pickup_done_time=None, cancel_reason=None),
        ped(order_status="CANCELLED", pickup_done_time=None, cancel_reason="Others"),
    ]
    ap = L.apurar(pedidos, registry=REGISTRY, watermarks={}, agora=AGORA)
    (x,) = ap.vencimentos
    assert x.excl_cancelado_sem_coleta == 3
    assert x.cancelado_motivo_desconhecido == 1
    assert x.cancelado_vendedor_nao_enviou == 0
    assert ap.qualidade.motivos_desconhecidos == Counter({"Novo motivo": 1})
    assert ap.qualidade.motivos_ausentes == 1


def test_motivo_nao_enviou_esta_no_vocabulario():
    """Se o texto mudar na fonte, o contador zeraria sem erro: a trava e' aqui."""
    assert L.MOTIVO_NAO_ENVIOU in L.MOTIVOS_CONHECIDOS


def test_status_fora_do_vocabulario_vira_alerta_sem_mudar_a_classe():
    ap = L.apurar([ped(order_status="NOVO_STATUS")], registry=REGISTRY,
                  watermarks={}, agora=AGORA)
    assert ap.qualidade.status_desconhecidos == Counter({"NOVO_STATUS": 1})
    assert ap.vencimentos[0].no_prazo == 1


def test_vencimento_aberto_nao_e_maduro_mesmo_com_fonte_fresca():
    p = ped(order_status="READY_TO_SHIP", pickup_done_time=None,
            ship_by_date=fim_do_dia(date(2026, 10, 22)))
    ap = L.apurar([p], registry=REGISTRY, watermarks={"111": AGORA}, agora=AGORA)
    assert ap.vencimentos[0].is_mature is False
    assert ap.vencimentos[0].pendente_no_prazo == 1


@pytest.mark.parametrize("campo,valor,msg", [
    ("no_prazo", 5, "base nao fecha"),
    ("cancelado_vendedor_nao_enviou", 9, "subconjuntos"),
    ("pendente_no_prazo", 1, "base nao fecha"),
])
def test_validar_recusa_linha_que_nao_fecha(campo, valor, msg):
    x = L.LinhaVencimento(deadline_date=date(2026, 10, 15), shop_account="apice",
                          shop_id="111", brand="apice", pedidos_pagos=2, no_prazo=2)
    setattr(x, campo, valor)
    with pytest.raises(ValueError, match=msg):
        x.validar()


def test_validar_recusa_maduro_com_pendente_no_prazo():
    x = L.LinhaVencimento(deadline_date=date(2026, 10, 15), shop_account="apice",
                          shop_id="111", brand="apice", pedidos_pagos=1,
                          pendente_no_prazo=1, is_mature=True)
    with pytest.raises(ValueError, match="maduro"):
        x.validar()


# ===========================================================================
# Taxa e cobertura
# ===========================================================================
def test_taxa_sem_base_e_none_nunca_zero():
    assert L.taxa(0, 0) is None
    assert L.taxa(0, 10) == 0.0


@pytest.mark.parametrize("base,sem_prazo,enviado,estado", [
    (98, 2, 0, "completa"),         # exatamente 2%: ainda nao avisa
    (97, 2, 1, "aviso"),            # 3%
    (90, 10, 0, "aviso"),           # exatamente 10%: ainda mostra a taxa
    (89, 6, 5, "insuficiente"),     # 11%: taxa vira "—"
    (0, 0, 0, "sem_base"),
])
def test_limiares_de_cobertura(base, sem_prazo, enviado, estado):
    assert L.cobertura(base, sem_prazo, enviado)[1] == estado


def test_resumo_esconde_a_taxa_com_cobertura_insuficiente():
    pedidos = [ped(order_status="SHIPPED", pickup_done_time=None)] * 2 + [ped()] * 8
    ap = L.apurar(pedidos, registry=REGISTRY, watermarks={"111": AGORA}, agora=AGORA)
    r = cli.resumo_por_marca(ap)["apice"]
    assert r["cobertura"] == "insuficiente"
    assert r["taxa"] is None


# ===========================================================================
# Reconciliacao Python x SQL
# ===========================================================================
def _ap_simples():
    return L.apurar([ped(), ped(fulfillment_flag=L.FLUXO_FBS)], registry=REGISTRY,
                    watermarks={"111": AGORA}, agora=AGORA)


def test_reconciliacao_igual_passa_e_ignora_conta_fora_do_registry():
    sql = {("111", date(2026, 10, 15)): Counter({"no_prazo": 1, "fbs": 1}),
           ("999", date(2026, 10, 15)): Counter({"no_prazo": 7})}
    assert L.reconciliar(_ap_simples(), sql, REGISTRY) == 1


def test_reconciliacao_divergente_recusa():
    sql = {("111", date(2026, 10, 15)): Counter({"no_prazo": 1})}
    with pytest.raises(L.ReconciliacaoDivergente, match="python="):
        L.reconciliar(_ap_simples(), sql, REGISTRY)


# ===========================================================================
# Fila aberta por fluxo (vendedor x FBS)
# ===========================================================================
CONTAS = {"111": "apice", "222": "barbours"}


def aberto(**kw) -> dict:
    base = dict(shop_id="111", shop_account="apice", fulfillment_flag=L.FLUXO_VENDEDOR,
                create_time=datetime(2026, 10, 20, 9, tzinfo=BRT),
                pay_time=datetime(2026, 10, 20, 10, tzinfo=BRT),
                ship_by_date=fim_do_dia(date(2026, 10, 23)))
    base.update(kw)
    return base


def test_fbs_acima_de_48h_fica_na_parcela_fbs_e_nao_no_vendedor():
    """O caso de 21/10/2026: todo pedido acima de 48 h era FBS."""
    velho = datetime(2026, 10, 15, 10, tzinfo=BRT)
    linhas = [
        aberto(),                                                         # vendedor no prazo
        aberto(ship_by_date=fim_do_dia(date(2026, 10, 20))),              # vendedor vencido
        aberto(fulfillment_flag=L.FLUXO_FBS, pay_time=velho,
               ship_by_date=fim_do_dia(date(2026, 10, 18))),              # FBS vencido e >48h
    ]
    fila = L.apurar_fila_por_fluxo(linhas, registry=REGISTRY, contas=CONTAS,
                                   baselines={}, agora=AGORA)
    por = {(x.shop_account, x.fluxo): x for x in fila}
    v, f = por[("apice", "vendedor")], por[("apice", "fbs")]
    assert (v.backlog, v.overdue, v.over_48h) == (2, 1, 0)
    assert (f.backlog, f.overdue, f.over_48h) == (1, 1, 1)


def test_conta_sem_pedido_aberto_tem_linha_com_zero_medido():
    fila = L.apurar_fila_por_fluxo([aberto()], registry=REGISTRY, contas=CONTAS,
                                   baselines={}, agora=AGORA)
    assert {(x.shop_account, x.fluxo) for x in fila} == {
        ("apice", "vendedor"), ("apice", "fbs"), ("barbours", "vendedor"), ("barbours", "fbs")}
    assert all(x.backlog == 0 for x in fila if x.shop_account == "barbours")


def test_fila_usa_as_regras_da_fila_publicada():
    """Mesma fronteira das 48 h, do prazo e de travado que `transform`."""
    marco = AGORA - timedelta(hours=48)                  # exatamente 48 h: ainda nao passou
    lento = AGORA - timedelta(hours=40)
    linhas = [aberto(pay_time=marco), aberto(pay_time=lento, ship_by_date=None)]
    fila = L.apurar_fila_por_fluxo(linhas, registry=REGISTRY, contas=CONTAS,
                                   baselines={"111": (200, 10.0)}, agora=AGORA)
    v = next(x for x in fila if (x.shop_account, x.fluxo) == ("apice", "vendedor"))
    assert v.over_48h == 0
    assert v.deadline_unavailable == 1
    assert (v.slow, v.stalled) == (2, 2), "48 h e 40 h > 2 x p50 de 10 h"


def test_fluxo_desconhecido_na_fila_falha_fechado():
    with pytest.raises(L.FluxoDesconhecido):
        L.apurar_fila_por_fluxo([aberto(fulfillment_flag=None)], registry=REGISTRY,
                                contas=CONTAS, baselines={}, agora=AGORA)


def test_vendedor_mais_fbs_tem_de_bater_com_a_contagem_independente():
    fila = L.apurar_fila_por_fluxo(
        [aberto(), aberto(fulfillment_flag=L.FLUXO_FBS)], registry=REGISTRY,
        contas=CONTAS, baselines={}, agora=AGORA)
    assert L.reconciliar_fila(fila, {"111": 2}, REGISTRY) == 2
    with pytest.raises(L.FilaDivergente):
        L.reconciliar_fila(fila, {"111": 3}, REGISTRY)


def test_conta_ambigua_na_fonte_falha_fechado():
    class Cur:
        description = [("shop_id",), ("shop_account",), ("max_ingested_at",)]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            pass

        def fetchall(self):
            return [("111", "apice", AGORA), ("111", "apice_2", AGORA)]

    class Conn:
        def cursor(self):
            return Cur()

    with pytest.raises(L.ContaAmbigua):
        L.ler_watermarks_e_contas(Conn())


# ===========================================================================
# Fonte: barreira de PII e de escopo
# ===========================================================================
def test_colunas_da_fonte_sao_fechadas_e_sem_comprador():
    assert L.COLUNAS_FONTE == (
        "shop_id", "shop_account", "order_status", "fulfillment_flag",
        "pay_time", "ship_by_date", "pickup_done_time", "cancel_reason",
    )
    proibidas = ("buyer", "cpf", "recipient", "address", "order_sn")
    for sql in (L.PEDIDOS_SQL, L.RECONCILIACAO_VENCIMENTO_SQL, L.WATERMARK_SQL,
                L.FILA_ABERTA_SQL, L.FILA_ABERTA_CONTAGEM_SQL):
        baixo = sql.lower()
        assert not any(p in baixo for p in proibidas), sql
        assert not re.search(r"select\s+\*", baixo), sql


def test_janela_e_35_para_tras_e_7_para_a_frente():
    assert L.janela_de_vencimento(HOJE) == (date(2026, 9, 16), date(2026, 10, 28))
    j = L.janela_sql(HOJE)
    assert j["venc_ate_excl"] == datetime(2026, 10, 29, tzinfo=BRT)


# ===========================================================================
# Publicacao com dublês
# ===========================================================================
class CursorFake:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        self.rowcount = 3 if sql.lstrip().startswith("DELETE") else 0
        if "pg_try_advisory_lock" in sql:
            self._um = {"ok": self.conn.lock_livre}

    def executemany(self, sql, linhas):
        self.conn.sql.append(sql)
        self.conn.inseridas.extend(linhas)

    def fetchone(self):
        if self.conn.sql[-1].strip().startswith("SELECT pg_try"):
            return self._um
        return self.conn.releitura


class ConexaoFake:
    def __init__(self, *, releitura=None, falha_commit=False, lock_livre=True):
        self.sql: list[str] = []
        self.inseridas: list[tuple] = []
        self.autocommit = True
        self.releitura = releitura
        self.falha_commit = falha_commit
        self.lock_livre = lock_livre
        self.commits = self.rollbacks = 0

    def cursor(self):
        return CursorFake(self)

    def commit(self):
        if self.falha_commit:
            raise RuntimeError("conexao caiu //u:segredo@host")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _ap_publicavel():
    return L.apurar(
        [ped(), ped(pickup_done_time=datetime(2026, 10, 16, 9, tzinfo=BRT)),
         ped(ship_by_date=None, order_status="PROCESSED", pickup_done_time=None)],
        registry=REGISTRY, watermarks={"111": AGORA}, agora=AGORA)


def test_publicar_apaga_as_duas_janelas_insere_e_rele_antes_do_commit():
    ap = _ap_publicavel()
    conn = ConexaoFake(releitura=L.esperado_na_releitura(ap))
    lote, gravadas, apagadas = cli.publicar(conn, ap, {"111": AGORA})
    assert gravadas == 2 and apagadas == 9
    assert conn.commits == 1 and conn.rollbacks == 0
    assert conn.autocommit is True
    ordem = [s.split()[0] for s in conn.sql]
    assert ordem == ["DELETE", "DELETE", "INSERT", "INSERT", "DELETE", "SELECT", "SELECT", "SELECT"]
    assert "sem_prazo_daily" in conn.sql[-2], "a tabela sem prazo tambem e' relida"
    assert "fila_fluxo" in conn.sql[-1], "a fila por fluxo tambem e' relida"


def test_releitura_divergente_desfaz_tudo():
    ap = _ap_publicavel()
    errada = {**L.esperado_na_releitura(ap), "pagos": 99}
    conn = ConexaoFake(releitura=errada)
    with pytest.raises(RuntimeError, match="releitura diverge"):
        cli.publicar(conn, ap, {"111": AGORA})
    assert conn.commits == 0 and conn.rollbacks == 1


def test_releitura_do_sem_prazo_divergente_tambem_desfaz():
    ap = _ap_publicavel()
    conn = ConexaoFake(releitura={**L.esperado_na_releitura(ap), "sem_prazo": 7})
    with pytest.raises(RuntimeError, match="releitura diverge"):
        cli.publicar(conn, ap, {"111": AGORA})
    assert conn.commits == 0


class ConexaoQueMorre(ConexaoFake):
    """Como o psycopg2 real: depois de o COMMIT falhar com a conexao caida,
    `autocommit = ...`, `cursor()` e `rollback()` levantam `InterfaceError`."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.morta = False
        self._ac = True

    @property
    def autocommit(self):
        return self._ac

    @autocommit.setter
    def autocommit(self, v):
        if getattr(self, "morta", False):
            raise RuntimeError("InterfaceError: connection already closed")
        self._ac = v

    def commit(self):
        self.morta = True
        raise RuntimeError("server closed the connection unexpectedly")

    def cursor(self):
        if self.morta:
            raise RuntimeError("InterfaceError: connection already closed")
        return super().cursor()

    def rollback(self):
        if self.morta:
            raise RuntimeError("InterfaceError: connection already closed")
        super().rollback()


def test_commit_com_conexao_morta_continua_indeterminado():
    """Revisao do PR #107: o setter de `autocommit` no `finally` trocava o
    exit 4 (commit indeterminado) por um exit 1 (falha comum)."""
    ap = _ap_publicavel()
    conn = ConexaoQueMorre(releitura=L.esperado_na_releitura(ap))
    with pytest.raises(IndeterminateCommit):
        cli.publicar(conn, ap, {"111": AGORA})


def test_lock_com_conexao_morta_nao_mascara_a_excecao_de_dentro():
    conn = ConexaoQueMorre()
    with pytest.raises(IndeterminateCommit):
        with cli.advisory_lock(conn, L.LOCK_KEY):
            conn.morta = True
            raise IndeterminateCommit("x")


def test_falha_no_commit_e_indeterminada_e_redige_credencial():
    ap = _ap_publicavel()
    conn = ConexaoFake(releitura=L.esperado_na_releitura(ap), falha_commit=True)
    with pytest.raises(IndeterminateCommit) as exc:
        cli.publicar(conn, ap, {"111": AGORA})
    assert "segredo" not in str(exc.value)
    assert conn.rollbacks == 0, "rollback depois de commit incerto pode desfazer o que entrou"


def test_linha_publicada_segue_a_ordem_das_colunas():
    lote, venc, semp = L.linhas_para_publicar(_ap_publicavel(), {"111": AGORA})
    v = dict(zip(L.COLUNAS, venc[0]))
    assert v["base"] == v["no_prazo"] + v["atrasado"] + v["pendente_vencido"] + v["pendente_no_prazo"]
    assert v["channel"] == "shopee" and v["refresh_batch_id"] == str(lote)
    s = dict(zip(L.COLUNAS_SEM_PRAZO, semp[0]))
    assert s["sem_prazo"] == 1


def test_lock_ocupado_nao_espera():
    with pytest.raises(LockNotAcquired):
        with cli.advisory_lock(ConexaoFake(lock_livre=False), L.LOCK_KEY):
            pytest.fail("entrou sem o lock")


def test_lock_proprio_nao_colide_com_nenhuma_chave_conhecida():
    from pipelines.expedicao.contract import ADVISORY_LOCK_KEYS, KNOWN_FOREIGN_LOCK_KEYS
    from pipelines.ingestion.fato_diaria_lock import FATO_DIARIA_ADVISORY_LOCK_KEYS
    usadas = (set(ADVISORY_LOCK_KEYS.values()) | set(KNOWN_FOREIGN_LOCK_KEYS)
              | set(FATO_DIARIA_ADVISORY_LOCK_KEYS.values()))
    assert L.LOCK_KEY not in usadas


# ===========================================================================
# Frescor e CLI
# ===========================================================================
def test_frescor_usa_a_leitura_mais_recente_entre_as_contas():
    wm = {"111": AGORA - timedelta(hours=30), "222": AGORA - timedelta(hours=2)}
    assert cli.verificar_frescor(wm, AGORA, 6) == pytest.approx(2)
    with pytest.raises(cli.FonteNaoPublicavel, match="defasada"):
        cli.verificar_frescor({"111": AGORA - timedelta(hours=7)}, AGORA, 6)
    with pytest.raises(cli.FonteNaoPublicavel, match="sem watermark"):
        cli.verificar_frescor({"111": None}, AGORA, 6)


@pytest.mark.parametrize("argv", [
    [], ["--apply", "--diagnose"], ["--diagnose", "--max-idade-fonte-horas", "6"],
    ["--apply", "--max-idade-fonte-horas", "0"], ["--apply", "--max-idade-fonte-horas", "73"],
    ["--bogus"],
])
def test_uso_invalido_sai_1_sem_conectar(argv, monkeypatch):
    for nome in ("_run_apply", "_run_diagnose", "_run_reconcile"):
        monkeypatch.setattr(cli, nome, lambda *a, **k: pytest.fail("conectou"))
    assert cli.main(argv) == cli.EXIT_FALHA


def test_sem_dsn_e_precondicao(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda **k: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATAMART_DATABASE_URL", raising=False)
    assert cli.main(["--apply"]) == cli.EXIT_PRECONDICAO
    assert cli.main(["--diagnose"]) == cli.EXIT_PRECONDICAO
    assert cli.main(["--reconcile"]) == cli.EXIT_PRECONDICAO


def test_qualidade_marca_warn_com_motivo_desconhecido():
    ap = L.apurar([ped(order_status="CANCELLED", pickup_done_time=None,
                       cancel_reason="Motivo inedito"), ped()],
                  registry=REGISTRY, watermarks={"111": AGORA}, agora=AGORA)
    conn = ConexaoFake()
    cli.registrar_qualidade(conn, ap, 1)
    motivo = [linha for linha in conn.inseridas if linha[0] == cli.CHECK_MOTIVO]
    assert motivo and motivo[0][3] == "warn" and motivo[0][5] == 1
    assert "Motivo inedito" in motivo[0][6]
    assert {linha[0] for linha in conn.inseridas} == {
        cli.CHECK_MOTIVO, cli.CHECK_COBERTURA, cli.CHECK_RECONCILIACAO, cli.CHECK_CONTAS}
    contas = [linha for linha in conn.inseridas if linha[0] == cli.CHECK_CONTAS][0]
    assert contas[3] == "warn" and "barbours" in contas[6], "barbours nao tem leitura"


def test_conta_do_registry_sem_leitura_ou_defasada_vira_alerta():
    """Revisao do PR #107: conta que some da fonte encolhia a taxa sem aviso."""
    wm = {"111": AGORA - timedelta(hours=30)}            # apice defasada, barbours ausente
    ap = L.apurar([ped()], registry=REGISTRY, watermarks=wm, agora=AGORA)
    assert ap.qualidade.contas_sem_leitura == ["barbours"]
    assert ap.qualidade.contas_defasadas == ["apice"]
    ok = L.apurar([ped()], registry=REGISTRY,
                  watermarks={"111": AGORA, "222": AGORA}, agora=AGORA)
    assert (ok.qualidade.contas_sem_leitura, ok.qualidade.contas_defasadas) == ([], [])


def test_cobertura_do_resumo_nao_conta_pagamento_de_hoje():
    """Base = vencimentos fechados; sem prazo tambem so' de dias fechados."""
    hoje = datetime(2026, 10, 21, 10, tzinfo=BRT)
    pedidos = [ped()] * 10 + [ped(ship_by_date=None, order_status="PROCESSED",
                                  pickup_done_time=None, pay_time=hoje)] * 5
    ap = L.apurar(pedidos, registry=REGISTRY, watermarks={"111": AGORA}, agora=AGORA)
    assert sum(s.sem_prazo for s in ap.sem_prazo) == 5, "publicado na tabela"
    assert cli.resumo_por_marca(ap)["apice"]["sem_prazo"] == 0, "fora da cobertura"


# ===========================================================================
# Migration 030 (estatica)
# ===========================================================================
def _migration():
    """Carrega a 030 com um `alembic` FALSO.

    Dependendo da ordem da suite, `apps/api` entra no `sys.path` e `import
    alembic` passa a resolver o PACOTE LOCAL `apps/api/alembic` (sem `op`), e
    nao a biblioteca. O teste nao pode depender dessa ordem: `op` e' trocado
    por um capturador em `_ddl` de qualquer jeito.
    """
    import sys
    import types

    falso = types.ModuleType("alembic")
    falso.op = None
    anterior = sys.modules.get("alembic")
    sys.modules["alembic"] = falso
    try:
        spec = importlib.util.spec_from_file_location("m030", MIGRATION)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        if anterior is None:
            sys.modules.pop("alembic", None)
        else:
            sys.modules["alembic"] = anterior
    return m


def _ddl() -> list[str]:
    m = _migration()
    capturado: list[str] = []

    class Op:
        @staticmethod
        def execute(sql):
            capturado.append(sql)

    m.op = Op
    m.upgrade()
    return capturado


def test_migration_encadeia_em_029_e_nao_executa_nada_no_import():
    m = _migration()
    assert (m.revision, m.down_revision) == ("030", "029")
    arvore = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for no in arvore.body:
        assert isinstance(no, (ast.Expr, ast.Import, ast.ImportFrom, ast.Assign,
                               ast.FunctionDef)), type(no)


def test_migration_tem_toda_coluna_publicada_e_os_fechamentos():
    ddl = "\n".join(_ddl())
    for coluna in set(L.COLUNAS) | set(L.COLUNAS_SEM_PRAZO):
        assert re.search(rf"\b{coluna}\b", ddl), coluna
    for ck in ("particao_fecha", "base_bate", "subconjuntos_cancelamento",
               "madura_sem_pendente_no_prazo", "_fecha", "channel = 'shopee'"):
        assert ck in ddl, ck


# ===========================================================================
# Integracao contra PostgreSQL descartavel
# ===========================================================================
pg = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

DDL_FONTE_E_APOIO = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS audit;
DROP TABLE IF EXISTS raw.shopee_orders, marts.dim_seller_account, marts.dim_loja,
    audit.source_sync_run, audit.data_quality_check,
    marts.expedicao_shopee_ldr_daily, marts.expedicao_shopee_ldr_sem_prazo_daily,
    marts.expedicao_shopee_fila_fluxo CASCADE;
CREATE TABLE raw.shopee_orders (
    shop_account text, order_sn text, shop_id text, order_status text,
    fulfillment_flag text, pay_time timestamptz, ship_by_date timestamptz,
    pickup_done_time timestamptz, cancel_reason text, ingested_at timestamptz,
    buyer_cpf_id text, create_time timestamptz DEFAULT '2026-10-19 09:00-03',
    PRIMARY KEY (shop_account, order_sn));
CREATE TABLE marts.dim_loja (loja_id int PRIMARY KEY, brand_key text UNIQUE, ativo boolean);
CREATE TABLE marts.dim_seller_account (seller_account_id serial PRIMARY KEY,
    marketplace_id int, loja_id int, external_seller_id text, ativo boolean,
    UNIQUE (marketplace_id, external_seller_id));
CREATE TABLE audit.source_sync_run (
    sync_run_id serial PRIMARY KEY, source_name text, marketplace_id int, loja_id int,
    started_at timestamptz, finished_at timestamptz, status text,
    rows_extracted int, rows_loaded int, error_message text,
    source_min_date date, source_max_date date);
CREATE TABLE audit.data_quality_check (
    check_id serial PRIMARY KEY, check_name text, table_name text, marketplace_id int,
    loja_id int, check_timestamp timestamptz DEFAULT now(), status text, severity text,
    failed_rows int, details text);
INSERT INTO marts.dim_loja VALUES (1, 'apice', true), (2, 'barbours', true);
INSERT INTO marts.dim_seller_account (marketplace_id, loja_id, external_seller_id, ativo)
VALUES (3, 1, '111', true), (3, 2, '222', true);
"""

#: 20/10 vence; a execucao e' em 21/10 12:00 BRT, depois da margem de D+1 06:00.
EXEC = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)
V20 = "2026-10-20 23:59-03"
LIDO = "2026-10-21 11:00-03"


def _preparar(url):
    import psycopg2
    with psycopg2.connect(url) as c, c.cursor() as cur:
        cur.execute(DDL_FONTE_E_APOIO)
        for sql in _ddl():
            cur.execute(sql)
        cur.executemany(
            "INSERT INTO raw.shopee_orders (shop_account, order_sn, shop_id, order_status,"
            " fulfillment_flag, pay_time, ship_by_date, pickup_done_time, cancel_reason,"
            " ingested_at, buyer_cpf_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'000')",
            [
                ("apice", "A1", "111", "COMPLETED", L.FLUXO_VENDEDOR, "2026-10-19 10:00-03",
                 V20, "2026-10-20 15:00-03", None, LIDO),
                ("apice", "A2", "111", "COMPLETED", L.FLUXO_VENDEDOR, "2026-10-19 10:00-03",
                 V20, "2026-10-21 09:00-03", None, LIDO),
                ("apice", "A3", "111", "PROCESSED", L.FLUXO_VENDEDOR, "2026-10-19 10:00-03",
                 V20, None, None, LIDO),
                ("apice", "A4", "111", "COMPLETED", L.FLUXO_FBS, "2026-10-19 10:00-03",
                 V20, "2026-10-20 10:00-03", None, LIDO),
                ("apice", "A5", "111", "CANCELLED", L.FLUXO_VENDEDOR, "2026-10-19 10:00-03",
                 V20, None, L.MOTIVO_NAO_ENVIOU, LIDO),
                ("barbours", "B1", "222", "READY_TO_SHIP", L.FLUXO_VENDEDOR,
                 "2026-10-21 08:00-03", "2026-10-22 23:59-03", None, None, LIDO),
                ("barbours", "B2", "222", "PROCESSED", L.FLUXO_VENDEDOR,
                 "2026-10-20 08:00-03", None, None, None, LIDO),
            ])


def _ler(url, tabela):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    with psycopg2.connect(url, cursor_factory=RealDictCursor) as c, c.cursor() as cur:
        cur.execute(f"SELECT * FROM {tabela} ORDER BY 1, 2, 3")
        return [dict(r) for r in cur.fetchall()]


def _negocio(linhas):
    return [{k: v for k, v in r.items()
             if k not in ("refresh_batch_id", "effective_at", "source_watermark_at")}
            for r in linhas]


def _apply(monkeypatch, url, agora=EXEC, *extra):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATAMART_DATABASE_URL", url)
    monkeypatch.setattr("dotenv.load_dotenv", lambda **k: None)
    monkeypatch.setattr(cli, "_agora_utc", lambda: agora)
    return cli.main(["--apply", *extra])


@pg
def test_apply_ponta_a_ponta_publica_o_que_a_regra_diz(monkeypatch):
    with cluster_da_sessao() as url:
        _preparar(url)
        assert _apply(monkeypatch, url, EXEC, "--max-idade-fonte-horas", "6") == cli.EXIT_OK
        venc = {(r["deadline_date"], r["shop_account"]): r
                for r in _ler(url, L.TABELA)}
        a = venc[(date(2026, 10, 20), "apice")]
        assert (a["pedidos_pagos"], a["excl_fbs"], a["excl_cancelado_sem_coleta"],
                a["cancelado_vendedor_nao_enviou"]) == (5, 1, 1, 1)
        assert (a["base"], a["no_prazo"], a["atrasado"], a["pendente_vencido"]) == (3, 1, 1, 1)
        assert a["is_mature"] is True
        b = venc[(date(2026, 10, 22), "barbours")]
        assert (b["pendente_no_prazo"], b["is_mature"]) == (1, False)
        (sp,) = _ler(url, L.TABELA_SEM_PRAZO)
        assert (sp["paid_date"], sp["sem_prazo"]) == (date(2026, 10, 20), 1)

        import psycopg2
        with psycopg2.connect(url) as c, c.cursor() as cur:
            cur.execute("SELECT status, rows_loaded FROM audit.source_sync_run")
            assert cur.fetchall() == [("success", 7)], "2 vencimentos + 1 sem prazo + 4 fila"
            cur.execute(f"SELECT shop_account, fluxo, backlog_count, overdue_count, "
                        f"deadline_unavailable_count FROM {L.TABELA_FILA_FLUXO} ORDER BY 1, 2")
            fila = cur.fetchall()
        # A3 (apice, aberto, vencido) e B1/B2 (barbours, um sem prazo); nenhum FBS
        # aberto: as linhas FBS existem com zero MEDIDO.
        assert fila == [("apice", "fbs", 0, 0, 0), ("apice", "vendedor", 1, 1, 0),
                        ("barbours", "fbs", 0, 0, 0), ("barbours", "vendedor", 2, 0, 1)]
        with psycopg2.connect(url) as c, c.cursor() as cur:
            cur.execute("SELECT check_name, status FROM audit.data_quality_check ORDER BY 1")
            checks = cur.fetchall()
        assert ("expedicao_shopee_ldr_reconciliacao", "pass") in checks
        assert ("expedicao_shopee_ldr_motivo_cancelamento", "pass") in checks


@pg
def test_reexecucao_e_idempotente_e_respeita_a_janela(monkeypatch):
    import psycopg2
    with cluster_da_sessao() as url:
        _preparar(url)
        # Historico FORA da janela (vencimento ha' 60 dias) nao pode ser tocado.
        with psycopg2.connect(url) as c, c.cursor() as cur:
            cur.execute(
                f"INSERT INTO {L.TABELA} VALUES ('shopee','2026-08-22','apice','111','apice',"
                "1,0,0,0,0,0,1,1,0,0,0,true,gen_random_uuid(),now(),now())")
        assert _apply(monkeypatch, url) == cli.EXIT_OK
        primeira = _ler(url, L.TABELA)
        assert _apply(monkeypatch, url) == cli.EXIT_OK
        segunda = _ler(url, L.TABELA)
        assert _negocio(primeira) == _negocio(segunda)
        assert any(r["deadline_date"] == date(2026, 8, 22) for r in segunda)


@pg
def test_checks_do_banco_recusam_linha_que_nao_fecha():
    import psycopg2
    with cluster_da_sessao() as url:
        _preparar(url)
        with pytest.raises(psycopg2.errors.CheckViolation):
            with psycopg2.connect(url) as c, c.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {L.TABELA} VALUES ('shopee','2026-10-20','apice','111','apice',"
                    "3,0,0,0,0,0,3,1,1,0,0,false,gen_random_uuid(),now(),now())")


@pg
def test_fonte_defasada_nao_escreve(monkeypatch):
    with cluster_da_sessao() as url:
        _preparar(url)
        codigo = _apply(monkeypatch, url, EXEC + timedelta(days=2),
                        "--max-idade-fonte-horas", "6")
        assert codigo == cli.EXIT_FONTE_NAO_PUBLICAVEL
        assert _ler(url, L.TABELA) == []


@pg
def test_lock_exclui_segunda_sessao():
    import psycopg2
    with cluster_da_sessao() as url:
        a, b = psycopg2.connect(url), psycopg2.connect(url)
        try:
            with cli.advisory_lock(a, L.LOCK_KEY):
                with pytest.raises(LockNotAcquired):
                    with cli.advisory_lock(b, L.LOCK_KEY):
                        pass
            with cli.advisory_lock(b, L.LOCK_KEY):
                pass
        finally:
            a.close()
            b.close()


@pg
def test_reconcile_le_publicado_e_fonte_sem_divergencia(monkeypatch):
    with cluster_da_sessao() as url:
        _preparar(url)
        assert _apply(monkeypatch, url) == cli.EXIT_OK
        assert cli.main(["--reconcile"]) == cli.EXIT_OK
