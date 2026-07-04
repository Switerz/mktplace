from pipelines.ingestion.shopee_raw.pii import (
    DUVIDA,
    NEGOCIO_NAO_SENSIVEL,
    PII_DIRETA,
    PSEUDONIMIZAVEL,
    classify_header,
    classify_headers,
    has_direct_pii,
)


def test_campos_de_pii_direta_conhecidos():
    for header in ("Nome do destinatário", "Telefone", "CPF do Comprador", "Endereço de entrega", "CEP"):
        assert classify_header(header).classification == PII_DIRETA


def test_username_e_pseudonimizavel_nao_pii_direta():
    rule = classify_header("Nome de usuário (comprador)")
    assert rule.classification == PSEUDONIMIZAVEL


def test_campo_de_negocio_nao_sensivel():
    for header in ("UF", "País", "Cidade", "Número de rastreamento"):
        assert classify_header(header).classification == NEGOCIO_NAO_SENSIVEL


def test_campo_desconhecido_vira_duvida_por_seguranca():
    rule = classify_header("Um Campo Que Nunca Vimos")
    assert rule.classification == DUVIDA


def test_has_direct_pii_true_quando_ha_coluna_direta():
    headers = ["ID do pedido", "Telefone", "Quantidade"]
    assert has_direct_pii(headers) is True


def test_has_direct_pii_false_quando_so_ha_campos_neutros():
    headers = ["ID do pedido", "Quantidade", "UF"]
    assert has_direct_pii(headers) is False


def test_classify_headers_preserva_ordem_e_completa_todos():
    headers = ["ID do pedido", "Telefone", "Bairro"]
    result = classify_headers(headers)
    assert [r["header"] for r in result] == headers
    assert all("classification" in r and "note" in r for r in result)
