"""Gate PMA-1A — contrato canonico da referencia de preco sugerido de revenda.

SO BIBLIOTECA PADRAO, DE PROPOSITO
----------------------------------
Este modulo nao importa openpyxl, psycopg2, sqlalchemy nem pydantic. E' o nucleo
semantico do gate — a parte cujo erro custa uma decisao comercial errada — e
mantendo-o em stdlib ele e' executavel e testavel sem banco, sem rede e sem
ambiente montado. Leitura de planilha e escrita em banco moram em
`pipelines/pma/reference_import.py`.

O QUE A REFERENCIA E' — E O QUE ELA NAO E'
------------------------------------------
`reference_type = 'suggested_retail_pdv'`: o "Preco na Ponta (PDV)" das tabelas
B2B, ou seja o PRECO SUGERIDO DE REVENDA.

NAO e' PMA (preco minimo anunciado). O Gate PMA-0 mediu, por formula na propria
planilha, que o PDV e' markup aritmetico sobre o preco de ATACADO
(`ROUND(atacado * 1,6; 2)` na Rituaria; valor digitado nas outras quatro), com
razao variavel por marca — ~1,50 em Barbours, 1,60 em Yenzah, 1,5997-1,6508 em
Kokeshi, 1,342-1,570 em Apice. Alem disso o proprio documento de politica lista
"congelar a tabela de PMA por SKU e o calendario de reajustes" como pendencia de
Trade/pricing: a tabela de PMA nao existe. Por isso
`policy_status = 'not_applicable_to_own_store_monitoring'` — este MVP e'
observacional, nao aplica politica e nao sustenta sancao.

`validity_status = 'missing'`: nenhuma das cinco planilhas tem vigencia. O unico
campo de data e' a data do PEDIDO. Ausencia de vigencia e' registrada como fato;
nenhuma data e' inventada.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constantes de contrato — literais, versionadas, exigidas pelo gate
# ---------------------------------------------------------------------------

REFERENCE_TYPE = "suggested_retail_pdv"
POLICY_STATUS = "not_applicable_to_own_store_monitoring"
VALIDITY_STATUS = "missing"
COVERAGE_STATUS = "advertised_only"

#: Valor PROIBIDO para `reference_type`. Existe como constante para que o teste
#: que o veta tenha um nome a que se referir, em vez de uma string solta.
FORBIDDEN_REFERENCE_TYPE = "pma"

# ---------------------------------------------------------------------------
# Escopo de marcas — tres conjuntos medidos, nao supostos
# ---------------------------------------------------------------------------

#: Marcas com catalogo ML proprio na Torre (medido 2026-09-02 em
#: `silver.stg_ml_items`: 908 itens, 4 `seller_id`, todos contas da casa).
MONITORED_BRANDS = ("barbours", "kokeshi", "lescent", "rituaria")

#: Marcas com tabela de preco B2B (as cinco planilhas do portal RCA).
REFERENCE_BRANDS = ("apice", "barbours", "kokeshi", "rituaria", "yenzah")

#: Interseccao: monitoradas E com referencia. O unico conjunto comparavel hoje.
COMPARABLE_BRANDS = tuple(b for b in MONITORED_BRANDS if b in REFERENCE_BRANDS)

#: Monitorada, mas sem planilha de referencia -> `no_reference`.
NO_REFERENCE_BRANDS = tuple(b for b in MONITORED_BRANDS if b not in REFERENCE_BRANDS)

#: Tem referencia, mas nao tem catalogo ML -> `out_of_scope_no_ml_catalog`.
OUT_OF_SCOPE_BRANDS = tuple(b for b in REFERENCE_BRANDS if b not in MONITORED_BRANDS)

# ---------------------------------------------------------------------------
# Vocabulario de status — semantica travada
# ---------------------------------------------------------------------------

QUALITY_OK = "ok"
QUALITY_AMBIGUOUS_SKU = "ambiguous_duplicate_sku"
QUALITY_AMBIGUOUS_GTIN = "ambiguous_duplicate_gtin"
QUALITY_AMBIGUOUS_BOTH = "ambiguous_duplicate_both"
QUALITY_MISSING_PRICE = "missing_suggested_price"

QUALITY_STATUSES = (
    QUALITY_OK,
    QUALITY_AMBIGUOUS_SKU,
    QUALITY_AMBIGUOUS_GTIN,
    QUALITY_AMBIGUOUS_BOTH,
    QUALITY_MISSING_PRICE,
)

#: Palavras que NAO podem aparecer em nenhum payload deste produto. O MVP e'
#: observacional: "abaixo da referencia" e' um fato aritmetico, nao um juizo. O
#: PMA-0 registrou o risco concorrencial (Lei 12.529/2011 art. 36 par. 3 IX) de
#: documentar por escrito uma pratica uniforme de preco de revenda; vocabulario
#: de sancao num payload automatizado e' exatamente a prova documental a evitar.
FORBIDDEN_PAYLOAD_TERMS = (
    "infracao", "infração", "infringement",
    "violacao", "violação", "violation",
    "sancao", "sanção", "sanction", "penalidade", "penalty", "multa",
    "denuncia", "denúncia", "descadastr", "notificacao", "notificação",
    "proibido", "obrigacao", "obrigação",
    "pma", "preco minimo", "preço mínimo", "minimum advertised",
)

# ---------------------------------------------------------------------------
# Cabecalho das planilhas — linha 32, medida nos cinco arquivos
# ---------------------------------------------------------------------------

#: Linha do cabecalho da tabela de PRODUTOS na aba "Geral*". Medida identica nos
#: cinco arquivos no PMA-0. As linhas 1-31 sao o cabecalho do PEDIDO e contem o
#: BLOCO CADASTRAL (razao social, CNPJ, I.E., CEP, endereco, bairro, cidade,
#: estado, telefone, e-mail). Esse bloco NAO e' lido: a leitura comeca em 33.
HEADER_ROW = 32
FIRST_DATA_ROW = HEADER_ROW + 1

#: Aba de produtos. Os cinco arquivos usam "Geral", "Geral " ou "Geral Nova".
SHEET_PREFIX = "geral"

#: Cabecalhos aceitos por campo, ja normalizados (sem acento, minusculo, espaco
#: colapsado). Resolvidos por NOME, nunca por posicao: Apice/Barbours/Kokeshi
#: tem 5 colunas extra entre `nome cadastro` e `preco atacado`, e um mapeamento
#: posicional leria "Volumetria" como preco.
COLUMN_ALIASES = {
    "source_sku": ("sku",),
    "source_gtin": ("ean codigo de barras", "ean"),
    # `nome cadastro` e' o nome REGISTRADO DO PRODUTO, nao de pessoa: e' coluna da
    # tabela de produtos, chaveada por SKU/EAN, e reaparece na aba de cadastro de
    # produto ao lado de "nome comercial" e "curva vendas". Ainda assim passa por
    # `assert_no_pii_shaped_value` como defesa em profundidade.
    "product_name": ("nome cadastro", "descricao"),
    "wholesale_amount": ("preco atacado",),
    "suggested_retail_amount": ("preco na ponta pdv", "preco na ponta"),
}

#: Colunas que existem na planilha e sao DELIBERADAMENTE ignoradas.
#:   `preco final`  = atacado * (1 - desconto do pedido) -> preco B2B liquido,
#:                    varia com o desconto digitado em C28 e nao e' referencia
#:                    de vitrine. Medido 0 em C28 nos cinco arquivos publicados,
#:                    o que hoje o torna identico ao atacado.
#:   `quantidade`   = campo de PEDIDO (vazio nos cinco templates).
#:   `valor`        = preco final * quantidade -> total de linha de pedido.
IGNORED_COLUMNS = ("preco final", "quantidade", "valor")

#: Comprimentos de EAN de CONSUMIDOR. Fonte UNICA da verdade (Gate PMA-2R).
#:
#: Estava inline dentro de `classify_gtin` e replicada em
#: `apps/api/app/services/pma_match.CONSUMER_EAN_LENGTHS`. O sync de observacao
#: nao tinha nenhuma copia — e essa ausencia foi o defeito que derrubou a
#: primeira carga (26 digitos chegando ao `varchar(14)`). Agora e' constante
#: nomeada aqui, consumida por `classify_gtin`, espelhada pelo sync e confrontada
#: por teste de equivalencia nos tres lados.
#:
#: 14 digitos NAO entra: e' DUN de caixa, nao unidade de consumo.
CONSUMER_EAN_LENGTHS = (8, 12, 13)

_NON_DIGIT = re.compile(r"\D")
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")

#: Formas de PII que NUNCA podem sair de uma planilha para o snapshot. Nao
#: substitui a regra estrutural (ler apenas da linha 33 em diante) — e' a segunda
#: trava, para o caso de uma planilha futura mudar de layout.
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}\b")
_CEP_RE = re.compile(r"\b\d{5}-\d{3}\b")


class ReferenceContractError(ValueError):
    """Violacao de contrato na referencia. Mensagem sem valor de celula."""


def normalize_header(raw: object) -> str:
    """Cabecalho -> forma canonica: sem acento, minusculo, sem pontuacao."""
    texto = unicodedata.normalize("NFKD", str(raw or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = _WS.sub(" ", texto).strip().lower()
    return _NON_ALNUM.sub("", texto).strip()


# `normalize_brand` foi REMOVIDA no PMA-1A-R (Fase 4, helper morto). Ela
# colapsava espacos, o que a tornava inutil para tokenizar nome de arquivo — e
# esse era seu unico chamador, hoje substituido por
# `reference_import._strip_accents_lower`. A marca do lado do match e' normalizada
# em `pma_match.normalize_brand_key`, que e' quem o endpoint usa.


def normalize_sku(raw: object) -> str | None:
    """SKU -> texto maiusculo sem espaco. `None` quando ausente.

    Numero inteiro vindo do xlsx e' convertido sem `.0`: os SKU do Apice sao
    numericos de 5 digitos e chegariam como `20910.0`, que nunca casaria por
    igualdade exata.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, float):
        if raw != raw:  # NaN
            return None
        texto = str(int(raw)) if raw.is_integer() else str(raw)
    elif isinstance(raw, int):
        texto = str(raw)
    else:
        texto = str(raw)
    texto = texto.strip().upper()
    return texto or None


@dataclass(frozen=True)
class GtinClassification:
    """Resultado da classificacao de um codigo de barras de origem.

    `value` so e' preenchido quando o codigo e' EAN DE CONSUMIDOR (8, 12 ou 13
    digitos). Codigo de 14 digitos e' DUN de caixa e devolve `value=None` com
    `kind='dun14'`: tratar DUN como EAN casaria caixa com unidade, e o gate
    proibe fazer isso silenciosamente. `note` carrega o valor bruto para
    auditoria — e' codigo de barras de produto, nao dado pessoal.
    """

    value: str | None
    kind: str
    note: str | None = None


def classify_gtin(raw: object) -> GtinClassification:
    if raw is None:
        return GtinClassification(None, "absent")
    if isinstance(raw, float):
        if raw != raw:
            return GtinClassification(None, "absent")
        bruto = str(int(raw)) if raw.is_integer() else str(raw)
    elif isinstance(raw, int) and not isinstance(raw, bool):
        bruto = str(raw)
    else:
        bruto = str(raw)
    digitos = _NON_DIGIT.sub("", bruto.strip())
    if not digitos:
        return GtinClassification(None, "absent")
    if len(digitos) == 14:
        return GtinClassification(
            None,
            "dun14",
            f"codigo de 14 digitos recusado como EAN de consumidor (DUN de "
            f"caixa): {digitos}. Match primario por GTIN indisponivel; a linha "
            f"permanece elegivel a match secundario por SKU unico na marca.",
        )
    if len(digitos) in CONSUMER_EAN_LENGTHS:
        return GtinClassification(digitos, "consumer_ean")
    return GtinClassification(
        None, "invalid", f"codigo com {len(digitos)} digitos nao e' EAN valido: {digitos}."
    )


def assert_no_pii_shaped_value(texto: str, campo: str) -> None:
    """Recusa valor com forma de CNPJ/CPF/e-mail/telefone/CEP.

    Trava de defesa em profundidade. A mensagem NAO ecoa o valor: descrever o
    achado e' suficiente, e ecoar reintroduziria no log o dado que se recusa.
    """
    for regex, nome in (
        (_CNPJ_RE, "CNPJ"), (_CPF_RE, "CPF"), (_EMAIL_RE, "e-mail"),
        (_PHONE_RE, "telefone"), (_CEP_RE, "CEP"),
    ):
        if regex.search(texto):
            raise ReferenceContractError(
                f"valor com forma de {nome} recusado no campo {campo!r}: o "
                f"snapshot de referencia nunca recebe dado cadastral. Verifique "
                f"se o layout da planilha mudou e se a leitura ainda comeca na "
                f"linha {FIRST_DATA_ROW}."
            )


def assert_payload_has_no_forbidden_terms(texto: str, onde: str) -> None:
    """Recusa vocabulario de sancao/politica em texto destinado ao payload."""
    baixo = unicodedata.normalize("NFKD", texto.lower())
    baixo = "".join(c for c in baixo if not unicodedata.combining(c))
    for termo in FORBIDDEN_PAYLOAD_TERMS:
        alvo = unicodedata.normalize("NFKD", termo.lower())
        alvo = "".join(c for c in alvo if not unicodedata.combining(c))
        if alvo and alvo in baixo:
            raise ReferenceContractError(
                f"termo proibido {termo!r} encontrado em {onde}: este MVP e' "
                f"observacional e nao aplica politica de preco."
            )


def reference_row_id(brand: str, source_sku: str, gtin_raw: str,
                     source_row_number: int) -> str:
    """Identidade DETERMINISTICA da linha de referencia.

    Inclui `source_row_number` de proposito: a Rituaria tem o SKU `RT01016` em
    DUAS linhas com EAN diferentes, e um EAN (`7901128300047`) em DUAS linhas com
    PDV divergente (R$ 109,90 e R$ 109,01). Sem a linha de origem na identidade,
    duas linhas legitimamente distintas colidiriam e uma sumiria do snapshot —
    apagando justamente a ambiguidade que precisa ficar visivel.

    Deterministico permite reimportar o mesmo arquivo e obter as mesmas
    identidades, o que e' o que torna dois snapshots comparaveis.
    """
    material = f"{brand}|{source_sku}|{gtin_raw}|{int(source_row_number)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def snapshot_id_for(captured_at: datetime) -> str:
    """Identidade do snapshot, derivada do instante de captura em UTC."""
    if captured_at.tzinfo is None:
        raise ReferenceContractError("captured_at precisa ser timezone-aware.")
    return "pma-ref:" + captured_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class ReferenceRow:
    """Uma linha de referencia sanitizada. Somente produto e preco."""

    brand: str
    reference_row_id: str
    source_sku: str
    source_gtin: str | None
    product_name: str
    wholesale_amount: object | None
    suggested_retail_amount: object | None
    source_row_number: int
    quality_status: str = QUALITY_OK
    quality_notes: list[str] = field(default_factory=list)

    @property
    def gtin_match_allowed(self) -> bool:
        return (
            self.source_gtin is not None
            and self.quality_status not in (
                QUALITY_AMBIGUOUS_GTIN, QUALITY_AMBIGUOUS_BOTH, QUALITY_MISSING_PRICE,
            )
        )

    @property
    def sku_match_allowed(self) -> bool:
        return self.quality_status not in (
            QUALITY_AMBIGUOUS_SKU, QUALITY_AMBIGUOUS_BOTH, QUALITY_MISSING_PRICE,
        )


def classify_quality(rows: list[ReferenceRow]) -> list[ReferenceRow]:
    """Diagnostica ambiguidade DENTRO DE CADA MARCA e anota cada linha.

    Ambiguidade e' propriedade do conjunto, nao da linha: um SKU so e' ambiguo
    porque OUTRA linha da mesma marca o repete. Por isso a classificacao roda
    sobre a lista inteira, depois da leitura de todos os arquivos.

    Escopo por marca e' obrigatorio: os SKU do Apice sao numericos de 5 digitos, e
    Barbours/Kokeshi/Lescent tem 100/188/94 itens ML com SELLER_SKU no MESMO
    formato. Contar duplicidade globalmente inventaria ambiguidade onde nao ha.
    """
    por_marca_sku: dict[tuple[str, str], int] = {}
    por_marca_gtin: dict[tuple[str, str], int] = {}
    for r in rows:
        por_marca_sku[(r.brand, r.source_sku)] = por_marca_sku.get((r.brand, r.source_sku), 0) + 1
        if r.source_gtin:
            chave = (r.brand, r.source_gtin)
            por_marca_gtin[chave] = por_marca_gtin.get(chave, 0) + 1

    for r in rows:
        sku_dup = por_marca_sku.get((r.brand, r.source_sku), 0) > 1
        gtin_dup = bool(r.source_gtin) and por_marca_gtin.get((r.brand, r.source_gtin), 0) > 1

        # Preco ausente domina: sem referencia numerica a linha e' inutilizavel,
        # e reportar ambiguidade nela seria descrever o problema errado.
        if r.suggested_retail_amount is None:
            r.quality_status = QUALITY_MISSING_PRICE
            r.quality_notes.append(
                "preco sugerido de revenda ausente na planilha: linha inutilizavel "
                "como referencia. Ausencia NAO e' zero."
            )
            continue

        if sku_dup and gtin_dup:
            r.quality_status = QUALITY_AMBIGUOUS_BOTH
        elif sku_dup:
            r.quality_status = QUALITY_AMBIGUOUS_SKU
        elif gtin_dup:
            r.quality_status = QUALITY_AMBIGUOUS_GTIN
        else:
            r.quality_status = QUALITY_OK

        # Redacao deliberadamente TECNICA: "indisponivel", nunca "proibido".
        # A nota vai para o snapshot e chega ao payload da tela; vocabulario de
        # politica num texto automatizado e' exatamente o que o PMA-0 mandou
        # evitar, e `assert_payload_has_no_forbidden_terms` o reprovaria.
        if sku_dup:
            r.quality_notes.append(
                f"SKU repetido dentro da marca {r.brand}: match secundario por SKU "
                f"indisponivel. Ambiguidade MARCADA, nao resolvida por adivinhacao."
            )
        if gtin_dup:
            r.quality_notes.append(
                f"EAN repetido dentro da marca {r.brand}: match primario por GTIN "
                f"indisponivel. Ambiguidade MARCADA, nao resolvida por adivinhacao."
            )
    return rows


def notes_to_text(notas: list[str]) -> str | None:
    """Lista de notas -> texto unico, ou `None` quando vazia. Nunca string vazia."""
    if not notas:
        return None
    return " | ".join(notas)
