"""EXPEDICAO-KOKESHI-EXPORT — fila da Shopee a partir do export do Seller Centre.

Os arquivos sao montados aqui com o CABECALHO REAL do export da Kokeshi (66
colunas, enviado pelo operador em 03/11/2026), inclusive as colunas do
comprador, preenchidas com valores ficticios: o teste prova que elas nunca
viram campo.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import openpyxl
import pytest

from pipelines.expedicao import shopee_export as E
from pipelines.expedicao import shopee_export_cli as C
from pipelines.expedicao.coerencia import problemas_do_lote
from pipelines.expedicao.contract import (
    EXPORT_ADVISORY_LOCK_KEY,
    FILA_EXPORT_TABLE,
    FILA_TABLE,
    KNOWN_FOREIGN_LOCK_KEYS,
    ADVISORY_LOCK_KEYS,
    PII_FORBIDDEN_TOKENS,
    RUN_EXPORT_TABLE,
    Channel,
    SellerAccount,
    SourceHealth,
)
from pipelines.expedicao import publisher
from pipelines.expedicao.shopee_extract import assert_no_pii_in_sql

CABECALHO = [
    "ID do pedido", "Status do pedido", "Hot Listing", "Cancelar Motivo",
    "Status da Devolução / Reembolso", "Número de rastreamento", "Opção de envio",
    "Método de envio", "Data de criação do pedido", "Hora do pagamento do pedido",
    "Data prevista de envio", "Tempo de Envio", "Domestic Delivered Date",
    "Hora completa do pedido", "Data da Finalização do Cancelamento", "Pedido FBS",
    "Nº de referência do SKU principal", "Nome do Produto", "Número de referência SKU",
    "Nome da variação", "Shopee Owned", "Preço original", "Preço acordado", "Quantidade",
    "Subtotal do produto", "Desconto do vendedor", "Desconto do vendedor",
    "Incentivo Shopee para ação comercial", "Ajuste por participação em ação comercial",
    "Peso total SKU", "Número de produtos pedidos", "Peso total do pedido",
    "Código do Cupom", "Cupom do vendedor",
    "Coin Cashback Voucher Amount Sponsored by Seller", "Cupom", "Incentivo de cupom",
    "Ajuste por pagamento via PIX", "Indicador da Leve Mais por Menos",
    "Desconto Shopee da Leve Mais por Menos", "Desconto da Leve Mais por Menos do vendedor",
    "Compensar Moedas Shopee", "Total descontado Cartão de Crédito", "Valor Total",
    "Taxa de envio pagas pelo comprador", "Taxa de Serviço Instantâneo pago pelo comprador",
    "Taxa de Envio Reversa", "Taxa de transação", "Taxa de comissão bruta",
    "Taxa de comissão líquida", "Taxa de serviço bruta", "Taxa de serviço líquida",
    "Total global", "Valor estimado do frete", "Nome de usuário (comprador)",
    "Nome do destinatário", "Telefone", "Endereço de entrega", "Cidade", "Bairro",
    "Cidade", "UF", "País", "CEP", "Observação do comprador", "Nota",
]

#: Colunas do comprador no export: a allowlist nunca as toca.
COLUNAS_DO_COMPRADOR = frozenset({
    "Nome de usuário (comprador)", "Nome do destinatário", "Telefone",
    "Endereço de entrega", "Cidade", "Bairro", "UF", "País", "CEP",
    "Observação do comprador",
})

#: 03/11/2026 12:00 BRT.
AGORA = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
CONTA = "kokeshi"
SHOP_ID = "395166866"
SELLER = SellerAccount(external_seller_id=SHOP_ID, loja_id=3, brand_key="kokeshi")
PII = "PII-NUNCA-LIDA"


def pedido(sn, status="A Enviar", rastreio="", criado="2026-11-02 10:00",
           pago="2026-11-02 10:05", prazo="2026-11-04 23:59", envio="", fbs="No",
           motivo="", opcao="Shopee Xpress"):
    linha = {n: None for n in CABECALHO}
    linha.update({
        "ID do pedido": sn, "Status do pedido": status, "Cancelar Motivo": motivo,
        "Número de rastreamento": rastreio, "Opção de envio": opcao,
        "Data de criação do pedido": criado, "Hora do pagamento do pedido": pago,
        "Data prevista de envio": prazo, "Tempo de Envio": envio, "Pedido FBS": fbs,
    })
    for n in COLUNAS_DO_COMPRADOR | {"Nome de usuário (comprador)"}:
        linha[n] = PII
    return linha


def escrever(pasta: Path, nome: str, linhas: list[dict], cabecalho=CABECALHO,
             mtime: datetime = AGORA - timedelta(minutes=10)) -> Path:
    livro = openpyxl.Workbook()
    folha = livro.active
    folha.title = "orders"
    folha.append(cabecalho)
    for linha in linhas:
        folha.append([linha.get(n) for n in cabecalho])
    caminho = pasta / nome
    livro.save(caminho)
    os.utime(caminho, (mtime.timestamp(), mtime.timestamp()))
    return caminho


NOME = "Order.all.order_creation_date.20261004_20261103.xlsx"


def ler(caminhos, agora=AGORA, max_idade=timedelta(hours=3)):
    return E.ler_export(caminhos, shop_account=CONTA, shop_id=SHOP_ID,
                        agora=agora, max_idade=max_idade)


# ===========================================================================
# 1. Leitura: allowlist, status, datas e grao
# ===========================================================================
def test_allowlist_nao_toca_coluna_de_comprador():
    assert not set(E.COLUNAS) & COLUNAS_DO_COMPRADOR
    assert set(E.COLUNAS) <= set(CABECALHO)


def test_nenhum_campo_lido_carrega_dado_do_comprador(tmp_path):
    leitura = ler([escrever(tmp_path, NOME, [pedido("A1")])])
    for p in leitura.pedidos:
        assert PII not in repr(p)


def test_a_enviar_vira_etapa_pelo_rastreio(tmp_path):
    leitura = ler([escrever(tmp_path, NOME, [
        pedido("A1", rastreio="BR123"), pedido("A2", rastreio="")])])
    por = {p["order_sn"]: p for p in leitura.pedidos}
    assert por["A1"]["order_status"] == "PROCESSED"
    assert por["A2"]["order_status"] == "READY_TO_SHIP"
    assert por["A1"]["has_tracking"] is True
    # O numero de rastreamento nunca e' guardado, so' a presenca.
    assert "BR123" not in repr(por["A1"])


@pytest.mark.parametrize("bruto, codigo", [
    ("Não pago", "UNPAID"), ("Enviado", "SHIPPED"), ("Concluído", "COMPLETED"),
    ("Cancelado", "CANCELLED"), ("Entregue", "TO_CONFIRM_RECEIVE"),
    (" a enviar ", "READY_TO_SHIP"),
])
def test_status_do_export(bruto, codigo):
    assert E.classificar_status(bruto, False) == codigo


def test_status_desconhecido_nao_bloqueia_e_e_contado(tmp_path):
    leitura = ler([escrever(tmp_path, NOME, [
        pedido("A1"), pedido("B1", status="Devolvido ao vendedor", envio="2026-11-02 15:00")])])
    assert leitura.status_desconhecidos == {"Devolvido ao vendedor": 1}
    desconhecido = next(p for p in leitura.pedidos if p["order_sn"] == "B1")
    assert desconhecido["order_status"] is None
    assert [r["order_sn"] for r in E.linhas_de_backlog(leitura.pedidos)] == ["A1"]


def test_datas_de_brasilia_viram_utc_e_traco_e_ausencia(tmp_path):
    leitura = ler([escrever(tmp_path, NOME, [pedido("A1", pago="-", envio="")])])
    p = leitura.pedidos[0]
    assert p["create_time"] == datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)
    assert p["ship_by_date"] == datetime(2026, 11, 5, 2, 59, tzinfo=timezone.utc)
    assert p["pay_time"] is None and p["pickup_done_time"] is None


def test_data_fora_do_formato_recusa_o_export(tmp_path):
    with pytest.raises(E.ExportInvalido, match="Data prevista de envio"):
        ler([escrever(tmp_path, NOME, [pedido("A1", prazo="04/11/2026")])])


def test_itens_do_mesmo_pedido_viram_um_pedido(tmp_path):
    leitura = ler([escrever(tmp_path, NOME, [
        pedido("A1", rastreio="", envio=""), pedido("A1", rastreio="BR1")])])
    assert leitura.linhas == 2
    assert len(leitura.pedidos) == 1
    assert leitura.pedidos[0]["order_status"] == "PROCESSED"


def test_status_divergente_entre_itens_recusa(tmp_path):
    with pytest.raises(E.ExportInvalido, match="status diferente"):
        ler([escrever(tmp_path, NOME, [pedido("A1"), pedido("A1", status="Cancelado")])])


def test_fbs_e_motivo_de_cancelamento(tmp_path):
    leitura = ler([escrever(tmp_path, NOME, [
        pedido("A1", fbs="Yes"),
        pedido("C1", status="Cancelado", motivo="Cancelado pelo comprador", fbs="No")])])
    por = {p["order_sn"]: p for p in leitura.pedidos}
    assert por["A1"]["fulfillment_flag"] == "fulfilled_by_shopee"
    assert por["C1"]["fulfillment_flag"] == "fulfilled_by_local_seller"
    assert por["C1"]["cancel_reason"] == "Cancelado pelo comprador"


def test_export_da_aba_a_enviar_sem_cancelar_motivo_e_aceito(tmp_path):
    cab = [c for c in CABECALHO if c != "Cancelar Motivo"]
    nome = "Order.toship.order_creation_date.20261004_20261103.xlsx"
    leitura = ler([escrever(tmp_path, nome, [pedido("A1")], cabecalho=cab)])
    assert leitura.tipo == "toship"
    assert leitura.pedidos[0]["cancel_reason"] is None


def test_coluna_obrigatoria_ausente_recusa(tmp_path):
    cab = [c for c in CABECALHO if c != "Tempo de Envio"]
    with pytest.raises(E.ExportInvalido, match="Tempo de Envio"):
        ler([escrever(tmp_path, NOME, [pedido("A1")], cabecalho=cab)])


# ===========================================================================
# 2. O arquivo serve para a fila de AGORA?
# ===========================================================================
def test_export_que_nao_termina_hoje_e_recusado(tmp_path):
    nome = "Order.all.order_creation_date.20261003_20261102.xlsx"
    with pytest.raises(E.ExportInvalido, match="precisa do dia de hoje"):
        ler([escrever(tmp_path, nome, [pedido("A1")])])


def test_download_velho_e_recusado(tmp_path):
    velho = escrever(tmp_path, NOME, [pedido("A1")], mtime=AGORA - timedelta(hours=5))
    with pytest.raises(E.ExportInvalido, match="baixe o export de novo"):
        ler([velho])


def test_nome_renomeado_e_recusado(tmp_path):
    with pytest.raises(E.ExportInvalido, match="nao renomeie"):
        ler([escrever(tmp_path, "kokeshi_pedidos.xlsx", [pedido("A1")])])


def test_segundo_download_do_navegador_e_aceito(tmp_path):
    nome = "Order.all.order_creation_date.20261004_20261103 (1).xlsx"
    assert len(ler([escrever(tmp_path, nome, [pedido("A1")])]).pedidos) == 1


def test_partes_juntam_e_parte_faltando_recusa(tmp_path):
    p1 = escrever(tmp_path, "Order.all.order_creation_date.20261004_20261103_part_1_of_2.xlsx",
                  [pedido("A1")])
    p2 = escrever(tmp_path, "Order.all.order_creation_date.20261004_20261103_part_2_of_2.xlsx",
                  [pedido("A2")])
    assert len(ler([p2, p1]).pedidos) == 2
    with pytest.raises(E.ExportInvalido, match="incompletas"):
        ler([p1])


def test_janela_curta_avisa_sem_recusar(tmp_path):
    nome = "Order.all.order_creation_date.20261103_20261103.xlsx"
    leitura = ler([escrever(tmp_path, nome, [pedido("A1")])])
    assert any("baseline" in a for a in leitura.avisos)


# ===========================================================================
# 3. Fila e baseline: MESMAS regras do Data Mart
# ===========================================================================
def test_backlog_e_a_enviar_sem_coleta():
    base = {"shop_account": CONTA, "shop_id": SHOP_ID, "create_time": AGORA,
            "pay_time": AGORA, "ship_by_date": AGORA, "shipping_carrier": "X",
            "ingested_at": AGORA}
    pedidos = [
        {**base, "order_sn": "1", "order_status": "READY_TO_SHIP", "pickup_done_time": None},
        {**base, "order_sn": "2", "order_status": "PROCESSED", "pickup_done_time": None},
        {**base, "order_sn": "3", "order_status": "PROCESSED", "pickup_done_time": AGORA},
        {**base, "order_sn": "4", "order_status": "SHIPPED", "pickup_done_time": None},
        {**base, "order_sn": "5", "order_status": None, "pickup_done_time": None},
    ]
    assert [r["order_sn"] for r in E.linhas_de_backlog(pedidos)] == ["1", "2"]


def test_baseline_espelha_o_sql_do_data_mart():
    def p(status, criado_h, coleta_h, pago=True):
        criado = AGORA - timedelta(hours=criado_h)
        return {"order_status": status, "create_time": criado,
                "pay_time": criado if pago else None,
                "pickup_done_time": criado + timedelta(hours=coleta_h)
                if coleta_h is not None else None}
    pedidos = [
        p("COMPLETED", 48, 10), p("SHIPPED", 72, 20), p("PROCESSED", 30, 30),
        p("CANCELLED", 40, 5),             # cancelado fora
        p(None, 40, 5),                    # status desconhecido fora (NULL no SQL)
        p("COMPLETED", 24 * 31, 1),        # fora da janela de 30 dias
        p("COMPLETED", 50, None),          # sem coleta
    ]
    assert E.baseline(pedidos, AGORA) == (3, 20.0)


def _fotografia(tmp_path, linhas):
    leitura = ler([escrever(tmp_path, NOME, linhas)])
    return C.montar_fotografia(leitura, SELLER, CONTA, leitura.pedidos, AGORA,
                               "lote-1", None)


def test_fotografia_classifica_como_as_outras_contas(tmp_path):
    fila, resumos, _ = _fotografia(tmp_path, [
        pedido("V1", prazo="2026-11-02 23:59"),                  # vencido
        pedido("H1", prazo="2026-11-03 23:59", rastreio="BR1"),  # vence hoje
        pedido("M1", prazo="2026-11-04 23:59"),                  # amanha
        pedido("S1", status="Enviado", envio="2026-11-02 15:00"),
    ])
    por = {r["marketplace_order_id"]: r for r in fila}
    assert set(por) == {"V1", "H1", "M1"}
    assert por["V1"]["deadline_status"] == "overdue"
    assert por["H1"]["etapa"] == "aguardando_coleta"
    assert por["M1"]["etapa"] == "a_preparar"
    assert {r["brand"] for r in fila} == {"kokeshi"}
    assert {r["channel"] for r in fila} == {"shopee"}
    assert len(resumos) == 1
    r = resumos[0]
    assert (r["shop_account"], r["backlog_count"], r["overdue_count"]) == (CONTA, 3, 1)
    assert r["source_watermark_at"] == AGORA - timedelta(minutes=10)
    assert not problemas_do_lote("shopee", fila, resumos, expected_accounts={CONTA})


def test_fotografia_vazia_ainda_grava_o_resumo(tmp_path):
    fila, resumos, _ = _fotografia(tmp_path, [
        pedido("S1", status="Concluído", envio="2026-11-02 15:00")])
    assert fila == []
    assert resumos[0]["backlog_count"] == 0


def test_relatorio_nao_cita_pedido(tmp_path):
    leitura = ler([escrever(tmp_path, NOME, [pedido("PEDIDO-SECRETO-1")])])
    fila, resumos, base = C.montar_fotografia(leitura, SELLER, CONTA, leitura.pedidos,
                                              AGORA, "lote-1", None)
    texto = C.relatorio(leitura, fila, resumos, base, AGORA)
    assert "PEDIDO-SECRETO-1" not in texto and PII not in texto
    assert "Fila da Expedicao: 1 pedido(s)" in texto


# ===========================================================================
# 4. Publicacao em tabelas proprias, restrita a conta
# ===========================================================================
class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executados.append((sql, params))

    def fetchall(self):
        return [{"column_name": "etapa"}, {"column_name": "etapa_fonte"}]


class _Conn:
    def __init__(self):
        self.executados = []
        self.autocommit = True
        self.commits = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def _publicar(conn, fila, resumos, destino):
    inseridos = []

    def ev(cur, sql, valores, **_kw):
        inseridos.append((sql, valores))

    publisher.publish_channel(
        conn, Channel.SHOPEE, fila, resumos, source_health=SourceHealth.HEALTHY,
        refresh_batch_id="lote-1", expected_accounts=frozenset({CONTA}),
        execute_values=ev, destino=destino)
    return inseridos


def test_publicacao_do_export_so_apaga_a_conta_nas_tabelas_proprias(tmp_path):
    fila, resumos, _ = _fotografia(tmp_path, [pedido("A1")])
    conn = _Conn()
    destino = publisher.Destino(FILA_EXPORT_TABLE, RUN_EXPORT_TABLE, (CONTA,))
    inseridos = _publicar(conn, fila, resumos, destino)
    deletes = [(s, p) for s, p in conn.executados if s.startswith("DELETE")]
    assert deletes == [(f"DELETE FROM {FILA_EXPORT_TABLE} WHERE channel = %s"
                        " AND shop_account = ANY(%s)", ("shopee", [CONTA]))]
    assert inseridos[0][0].startswith(f"INSERT INTO {FILA_EXPORT_TABLE} (")
    assert inseridos[1][0].startswith(f"INSERT INTO {RUN_EXPORT_TABLE} (")
    assert "expedicao_refresh_run_export.observed_at" in inseridos[1][0]
    # Nenhuma instrucao toca as tabelas do publicador agendado.
    textos = [s for s, _ in conn.executados + inseridos]
    assert not any("expedicao_fila_atual" in s for s in textos)
    assert not any("expedicao_refresh_run " in s or "expedicao_refresh_run(" in s
                   for s in textos)
    assert conn.commits == 1


def test_destino_padrao_continua_o_do_publicador_agendado():
    assert publisher.DESTINO_PADRAO.fila == FILA_TABLE
    assert publisher.DELETE_CANAL_SQL == f"DELETE FROM {FILA_TABLE} WHERE channel = %s"


def test_destino_do_export_sem_conta_e_recusado():
    with pytest.raises(ValueError, match="exige as contas"):
        publisher.Destino(FILA_EXPORT_TABLE, RUN_EXPORT_TABLE, ())


def test_lote_com_conta_fora_do_destino_e_recusado(tmp_path):
    fila, resumos, _ = _fotografia(tmp_path, [pedido("A1")])
    conn = _Conn()
    with pytest.raises(ValueError, match="fora do destino"):
        _publicar(conn, fila, resumos,
                  publisher.Destino(FILA_EXPORT_TABLE, RUN_EXPORT_TABLE, ("outra",)))
    assert not conn.executados


# ===========================================================================
# 5. CLI: previa nao grava; apply publica; nada de PII no SQL
# ===========================================================================
class _Alvo:
    """Neon falso: responde por trecho de SQL e registra tudo que rodou."""

    def __init__(self, conta=True, no_registry_api=False, tem_034=True):
        self.executados = []
        self.autocommit = False
        self.commits = 0
        self.fechada = False
        self._resp = {
            "pg_is_in_recovery": [{"replica": False, "somente_leitura": "off",
                                   "tem_034": tem_034}],
            "FROM marts.expedicao_conta_export": [{
                "shop_account": CONTA, "external_seller_id": SHOP_ID,
                "brand": "kokeshi", "loja_id": 3, "ativo": True,
                "no_registry_api": no_registry_api}] if conta else [],
            "pg_try_advisory_lock": [{"ok": True}],
            "MAX(source_watermark_at)": [{"wm": None}],
            "information_schema.columns": [{"column_name": "etapa"},
                                           {"column_name": "etapa_fonte"}],
        }

    def _resposta(self, sql):
        for trecho, linhas in self._resp.items():
            if trecho in sql:
                return linhas
        return []

    def cursor(self):
        alvo = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                alvo.executados.append(sql)
                self._linhas = alvo._resposta(sql)

            def fetchone(self):
                return self._linhas[0] if self._linhas else None

            def fetchall(self):
                return self._linhas

        return Cur()

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.fechada = True


class _Auditoria(_Alvo):
    def __init__(self):
        super().__init__()
        self._resp = {"RETURNING sync_run_id": [{"sync_run_id": 7}]}


def _rodar(tmp_path, aplicar, alvo=None, linhas=None):
    alvo = alvo or _Alvo()
    auditoria = _Auditoria()
    inseridos = []

    def ev(cur, sql, valores, **_kw):
        alvo.executados.append(sql)
        inseridos.append((sql, valores))

    saidas, logs = [], []
    arquivo = escrever(tmp_path, NOME, linhas or [pedido("A1"), pedido("A2", rastreio="BR")])
    codigo = C.executar([arquivo], CONTA, AGORA, aplicar=aplicar,
                        max_idade=timedelta(hours=3), open_target=lambda: alvo,
                        open_audit=lambda: auditoria, uuid_factory=lambda: "lote-1",
                        execute_values=ev, saida=saidas.append, log=logs.append)
    return codigo, alvo, auditoria, inseridos, "\n".join(saidas), "\n".join(logs)


def test_previa_nao_grava_nada(tmp_path):
    codigo, alvo, auditoria, inseridos, saida, _ = _rodar(tmp_path, aplicar=False)
    assert codigo == 0
    assert not inseridos
    assert not any(s.lstrip().startswith(("INSERT", "DELETE", "UPDATE"))
                   for s in alvo.executados + auditoria.executados)
    assert "PREVIA" in saida and "Fila da Expedicao: 2 pedido(s)" in saida


def test_apply_grava_pedidos_e_publica_a_conta(tmp_path):
    codigo, alvo, auditoria, inseridos, saida, logs = _rodar(tmp_path, aplicar=True)
    assert codigo == 0, logs
    alvos = [s.split(" (")[0] for s, _ in inseridos]
    assert alvos == ["INSERT INTO marts.expedicao_shopee_export_pedido",
                     f"INSERT INTO {FILA_EXPORT_TABLE}", f"INSERT INTO {RUN_EXPORT_TABLE}"]
    assert any("pg_advisory_unlock" in s for s in alvo.executados)
    assert any("INSERT INTO audit.source_sync_run" in s for s in auditoria.executados)
    assert "PUBLICADO: 2 pedido(s)" in saida


def test_conta_que_ja_vem_da_api_e_recusada(tmp_path):
    codigo, *_r, logs = _rodar(tmp_path, aplicar=True, alvo=_Alvo(no_registry_api=True))
    assert codigo == C.EXIT_PRECONDICAO
    assert "dim_seller_account" in logs


def test_sem_migration_034_para_antes_de_ler(tmp_path):
    codigo, *_r, logs = _rodar(tmp_path, aplicar=True, alvo=_Alvo(tem_034=False))
    assert codigo == C.EXIT_PRECONDICAO
    assert "034" in logs


def test_export_recusado_sai_com_codigo_3(tmp_path):
    codigo, *_r, logs = _rodar(tmp_path, aplicar=True,
                               linhas=[pedido("A1", prazo="amanha")])
    assert codigo == C.EXIT_FONTE_NAO_PUBLICAVEL
    assert "EXPORT RECUSADO" in logs


def test_sql_do_cli_sem_pii():
    for sql in (C.CONTA_SQL, C.UPSERT_PEDIDO_SQL, C.PEDIDOS_DO_BASELINE_SQL,
                C.WATERMARK_ANTERIOR_SQL, C.PREFLIGHT_SQL):
        assert_no_pii_in_sql(sql, PII_FORBIDDEN_TOKENS)


def test_lock_do_export_e_proprio():
    assert EXPORT_ADVISORY_LOCK_KEY not in KNOWN_FOREIGN_LOCK_KEYS
    assert EXPORT_ADVISORY_LOCK_KEY not in ADVISORY_LOCK_KEYS.values()
