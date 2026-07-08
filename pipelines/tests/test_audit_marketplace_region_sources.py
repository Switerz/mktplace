"""
Testes das funcoes PURAS de classificacao usadas na auditoria read-only da
Gold regional (Gate 4A/4B) — nao acessam banco real, so fixtures em memoria,
espelhando os casos reais encontrados na auditoria (ver
docs/regional_design_draft.md).
"""
import pytest

from pipelines.reconciliation.audit_marketplace_region_sources import (
    classify_ml_order_coverage,
    classify_shopee_order_snapshots,
    classify_coverage_level,
    coverage_warning,
    NotReadOnlyError,
)


# ---------------------------------------------------------------------------
# classify_shopee_order_snapshots
# ---------------------------------------------------------------------------

def _snapshot(**overrides) -> dict:
    base = {
        "n_linhas": 1, "sku_multiset_sig": "SKU1:V1:2",
        "soma_quantity": 2, "soma_returned_quantity": 0,
        "soma_product_subtotal": 100.0, "order_amount": 110.0, "order_grand_total": 110.0,
        "buyer_paid_shipping_fee": 10.0, "estimated_shipping_fee": 10.0, "reverse_shipping_fee": 0.0,
        "soma_transaction_fee": 1.0, "soma_commission_fee_gross": 5.0, "soma_commission_fee_net": 4.0,
        "soma_service_fee_gross": 2.0, "soma_service_fee_net": 1.5,
        "order_status": "COMPLETED", "return_refund_status": "NONE",
        "delivered_date": "2026-06-10", "cancel_completed_date": None,
        "delivery_city": "Sao Paulo", "delivery_state": "Sao Paulo",
    }
    base.update(overrides)
    return base


def test_classify_shopee_snapshots_identicos_e_exatamente_equivalente():
    snaps = [_snapshot(), _snapshot()]
    assert classify_shopee_order_snapshots(snaps) == "exatamente_equivalente"


def test_classify_shopee_snapshots_com_3_reexports_identicos_ainda_equivalente():
    # Caso real da auditoria: alguns pedidos aparecem em mais de 2 file_id.
    snaps = [_snapshot(), _snapshot(), _snapshot()]
    assert classify_shopee_order_snapshots(snaps) == "exatamente_equivalente"


def test_classify_shopee_snapshots_diferenca_de_itens():
    snaps = [_snapshot(), _snapshot(sku_multiset_sig="SKU2:V1:1", soma_quantity=1)]
    assert classify_shopee_order_snapshots(snaps) == "diferenca_de_itens_sku_qtd"


def test_classify_shopee_snapshots_diferenca_financeira():
    snaps = [_snapshot(), _snapshot(order_amount=999.0)]
    assert classify_shopee_order_snapshots(snaps) == "diferenca_financeira"


def test_classify_shopee_snapshots_diferenca_de_status():
    snaps = [_snapshot(order_status="TO_RETURN"), _snapshot(order_status="COMPLETED")]
    assert classify_shopee_order_snapshots(snaps) == "diferenca_de_status"


def test_classify_shopee_snapshots_diferenca_de_datas():
    snaps = [_snapshot(delivered_date="2026-06-10"), _snapshot(delivered_date="2026-06-12")]
    assert classify_shopee_order_snapshots(snaps) == "diferenca_de_datas"


def test_classify_shopee_snapshots_diferenca_geografica():
    snaps = [_snapshot(delivery_state="Sao Paulo"), _snapshot(delivery_state="Minas Gerais")]
    assert classify_shopee_order_snapshots(snaps) == "diferenca_geografica"


def test_classify_shopee_snapshots_multiplas_diferencas_quando_mais_de_uma_categoria_varia():
    snaps = [_snapshot(), _snapshot(order_amount=999.0, order_status="TO_RETURN")]
    assert classify_shopee_order_snapshots(snaps) == "multiplas_diferencas"


def test_classify_shopee_snapshots_nao_confunde_metadata_pura_com_diferenca_de_negocio():
    # Somente campos fora das listas SHOPEE_*_FIELDS mudam (ex: um campo de
    # metadata hipotetico nao monitorado) -> nao deve contar como diferenca.
    snaps = [_snapshot(campo_nao_monitorado="a"), _snapshot(campo_nao_monitorado="b")]
    assert classify_shopee_order_snapshots(snaps) == "exatamente_equivalente"


# ---------------------------------------------------------------------------
# classify_ml_order_coverage
# ---------------------------------------------------------------------------

def test_classify_ml_coverage_shipping_id_ausente():
    assert classify_ml_order_coverage(None, shipment_found=False, has_uf=False, cost_found=False) == "shipping_id_ausente"


def test_classify_ml_coverage_shipment_ausente():
    assert classify_ml_order_coverage(123, shipment_found=False, has_uf=False, cost_found=False) == "shipment_ausente"


def test_classify_ml_coverage_shipment_sem_uf():
    assert classify_ml_order_coverage(123, shipment_found=True, has_uf=False, cost_found=True) == "shipment_sem_uf"


def test_classify_ml_coverage_shipment_cost_ausente():
    assert classify_ml_order_coverage(123, shipment_found=True, has_uf=True, cost_found=False) == "shipment_cost_ausente"


def test_classify_ml_coverage_completo():
    assert classify_ml_order_coverage(123, shipment_found=True, has_uf=True, cost_found=True) == "completo"


def test_classify_ml_coverage_precedencia_shipping_id_ausente_vence_mesmo_com_outros_flags_true():
    # Se shipping_id e None, os outros flags nao deveriam nem ser calculaveis
    # na pratica, mas a funcao precisa ser robusta e determinstica mesmo
    # recebendo flags inconsistentes (defesa em profundidade).
    assert classify_ml_order_coverage(None, shipment_found=True, has_uf=True, cost_found=True) == "shipping_id_ausente"


# ---------------------------------------------------------------------------
# get_readonly_datamart_engine / conexao
# ---------------------------------------------------------------------------

def test_get_readonly_datamart_engine_levanta_erro_sanitizado_quando_nao_configurado(monkeypatch):
    import pipelines.common.db as db_mod
    from pipelines.reconciliation import audit_marketplace_region_sources as audit_mod

    monkeypatch.setattr(db_mod, "_datamart_engine", None)
    with pytest.raises(RuntimeError) as exc:
        audit_mod.get_readonly_datamart_engine()
    # A mensagem de erro nunca deve conter credenciais -- so o nome das vars.
    assert "DATAMART_DATABASE_URL" in str(exc.value) or "DATAMART_" in str(exc.value)
    assert "://" not in str(exc.value)


# ---------------------------------------------------------------------------
# classify_coverage_level / coverage_warning — contrato de API (secao 6 do
# design doc), thresholds sao constantes de codigo, nunca banco.
# ---------------------------------------------------------------------------

def test_classify_coverage_level_alta_a_partir_de_80_pct():
    assert classify_coverage_level(80.0) == "alta"
    assert classify_coverage_level(100.0) == "alta"
    assert classify_coverage_level(95.28) == "alta"  # rituaria, achado real da auditoria


def test_classify_coverage_level_media_entre_50_e_80():
    assert classify_coverage_level(50.0) == "media"
    assert classify_coverage_level(79.9) == "media"
    assert classify_coverage_level(72.09) == "media"  # media nacional ML pre-decisao, achado real


def test_classify_coverage_level_baixa_abaixo_de_50():
    assert classify_coverage_level(49.9) == "baixa"
    assert classify_coverage_level(0.0) == "baixa"
    assert classify_coverage_level(47.26) == "baixa"  # barbours, achado real da auditoria


def test_classify_coverage_level_sem_cobertura_quando_none():
    # None representa canal sem fonte de UF (TikTok) — nao pode ser
    # confundido com 0% (que significa "tem fonte, mas nao cobre nada").
    assert classify_coverage_level(None) == "sem_cobertura"


def test_coverage_warning_e_falso_somente_quando_alta():
    assert coverage_warning("alta") is False
    assert coverage_warning("media") is True
    assert coverage_warning("baixa") is True
    assert coverage_warning("sem_cobertura") is True


def test_coverage_warning_nunca_diverge_de_coverage_level_alta():
    """coverage_warning e sempre o inverso de coverage_level == 'alta' —
    testa a invariante para todos os niveis possiveis, nao so alguns."""
    for pct in (0.0, 25.0, 47.26, 49.9, 50.0, 72.09, 79.9, 80.0, 95.28, 100.0, None):
        level = classify_coverage_level(pct)
        assert coverage_warning(level) == (level != "alta")


class _FakeConn:
    """Conexao falsa minima — simula uma sessao onde o Postgres NAO confirma
    o modo read-only (ex: usuario sem permissao para SET, ou proxy que
    ignora a opcao), para provar que `_readonly_conn` recusa seguir mesmo
    assim, em vez de confiar apenas na intencao `postgresql_readonly=True`."""

    def __init__(self, confirmed_value: str):
        self._confirmed_value = confirmed_value
        self.closed = False

    def execution_options(self, **kwargs):
        return self

    def execute(self, stmt, *args, **kwargs):
        class _Result:
            def __init__(self, value):
                self._value = value

            def scalar(self):
                return self._value

        sql_text = str(stmt)
        if "current_setting" in sql_text:
            return _Result(self._confirmed_value)
        return _Result(None)

    def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self, confirmed_value: str):
        self._confirmed_value = confirmed_value

    def connect(self):
        return _FakeConn(self._confirmed_value)


def test_readonly_conn_recusa_seguir_quando_postgres_nao_confirma_read_only():
    from pipelines.reconciliation.audit_marketplace_region_sources import _readonly_conn

    fake_engine = _FakeEngine(confirmed_value="off")
    with pytest.raises(NotReadOnlyError):
        _readonly_conn(fake_engine)


def test_readonly_conn_segue_quando_postgres_confirma_read_only():
    from pipelines.reconciliation.audit_marketplace_region_sources import _readonly_conn

    fake_engine = _FakeEngine(confirmed_value="on")
    conn = _readonly_conn(fake_engine)
    assert conn is not None
