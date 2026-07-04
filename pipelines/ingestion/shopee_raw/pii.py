"""
Catálogo de classificação de PII para os headers reais encontrados nos
exports Shopee (`Order.all*.xlsx`). Baseado na inspeção de headers feita em
2026-07-03 (ver docs/data_contracts.md — contrato raw.shopee_*).

Shop-stats e ads são agregados por período/loja e não contêm PII de
comprador — não precisam de catálogo.

Classificações (conforme especificação da Fase Raw Shopee 1):
  - NEGOCIO_NAO_SENSIVEL: campo operacional, sem risco de reidentificação.
  - PSEUDONIMIZAVEL: identificador que pode/deve ser trocado por HMAC.
  - PII_DIRETA: identifica a pessoa diretamente ou é um quase-identificador
    de alta granularidade (ex: CEP).
  - DUVIDA: não há informação suficiente para classificar com segurança;
    exige decisão humana antes de qualquer ingestão irrestrita.
"""
from __future__ import annotations

from dataclasses import dataclass

NEGOCIO_NAO_SENSIVEL = "negocio_nao_sensivel"
PSEUDONIMIZAVEL = "identificador_pseudonimizavel"
PII_DIRETA = "pii_direta"
DUVIDA = "duvida_decisao"


@dataclass(frozen=True)
class PiiRule:
    classification: str
    note: str


# Header (nome exato exportado pela Shopee, PT-BR) -> regra.
# Confirmado via inspeção real de arquivos em 2026-07-03 (apice, barbours,
# kokeshi, lescent, rituaria — ver inventário).
ORDERS_PII_CATALOG: dict[str, PiiRule] = {
    "Nome de usuário (comprador)": PiiRule(
        PSEUDONIMIZAVEL,
        "Username Shopee do comprador. Não é nome civil, mas reidentifica a "
        "mesma pessoa entre pedidos — recomendado HMAC-SHA256 com segredo, "
        "nunca hash simples.",
    ),
    "Nome do destinatário": PiiRule(
        PII_DIRETA, "Nome completo de quem recebe a entrega."
    ),
    "Telefone": PiiRule(PII_DIRETA, "Telefone de contato do comprador/destinatário."),
    "CPF do Comprador": PiiRule(
        PII_DIRETA,
        "Documento de identificação direta. Presente apenas no template "
        "observado da marca apice — schema drift confirmado no inventário.",
    ),
    "Endereço de entrega": PiiRule(PII_DIRETA, "Endereço completo de entrega."),
    "CEP": PiiRule(
        PII_DIRETA,
        "CEP brasileiro pode isolar poucas residências/uma rua — tratado "
        "como quase-identificador de alta granularidade, não como dado geográfico solto.",
    ),
    "Bairro": PiiRule(
        DUVIDA,
        "Combinado com cidade e pedido pode reduzir bastante o universo de "
        "candidatos em cidades menores. Decisão pendente: manter em texto ou generalizar.",
    ),
    "Observação do comprador": PiiRule(
        DUVIDA,
        "Campo de texto livre digitado pelo comprador — pode conter nome, "
        "telefone ou outro dado pessoal não estruturado.",
    ),
    "Nota": PiiRule(
        DUVIDA,
        "Campo de texto livre; semântica exata (nota do seller vs. do "
        "comprador) não confirmada nos dados amostrados.",
    ),
    "Cidade": PiiRule(
        NEGOCIO_NAO_SENSIVEL,
        "Granularidade de cidade isoladamente tem baixo risco de "
        "reidentificação. Nota: coluna aparece duplicada no header original "
        "(mesmo nome duas vezes) — ver contrato de dados sobre desambiguação por posição.",
    ),
    "UF": PiiRule(NEGOCIO_NAO_SENSIVEL, "Unidade federativa — granularidade baixa."),
    "País": PiiRule(NEGOCIO_NAO_SENSIVEL, "Sempre Brasil nos exports observados."),
    "Número de rastreamento": PiiRule(
        NEGOCIO_NAO_SENSIVEL,
        "Código opaco da transportadora. Risco indireto: rastreamento público "
        "de terceiros pode expor status/endereço parcial a quem já possui o código.",
    ),
}

DEFAULT_RULE = PiiRule(
    DUVIDA, "Header fora do catálogo conhecido — revisar manualmente antes da carga real."
)


def classify_header(header: str) -> PiiRule:
    return ORDERS_PII_CATALOG.get(header, DEFAULT_RULE)


def classify_headers(headers: list[str]) -> list[dict]:
    """Retorna a classificação PII de cada header, na ordem original."""
    result = []
    for h in headers:
        rule = classify_header(h)
        result.append(
            {
                "header": h,
                "classification": rule.classification,
                "note": rule.note,
            }
        )
    return result


def has_direct_pii(headers: list[str]) -> bool:
    return any(classify_header(h).classification == PII_DIRETA for h in headers)
