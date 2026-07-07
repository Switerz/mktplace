"""
Parser do PREÂMBULO dos CSVs de ads da Shopee ("Dados Gerais de Anúncios
Shopee-CPC") — DRAFT da Fase Staging Shopee 2A (Gate 2B). JÁ É USADO por
`pipelines/ingestion/shopee_raw/writer.py` (extração durante a ingestão
real de novos arquivos ads, na mesma transação do arquivo) — mas nenhuma
EXECUÇÃO real ocorreu ainda em nenhum banco nesta fase, porque a coluna
`raw.shopee_ingestion_file.source_metadata` que este parser alimenta ainda
não existe em nenhum ambiente (migration não aplicada — ver
`db/sql/raw/shopee_raw_add_source_metadata.sql`).

Endurecido na revisão de 2026-07-06 (3ª rodada) após review pré-implementação:

- Cada linha do preâmbulo é interpretada com `csv.reader` (não
  `str.partition(",")`), respeitando aspas e vírgulas dentro do valor.
  Para um RÓTULO CONHECIDO, a linha deve produzir EXATAMENTE 2 campos —
  se produzir mais (vírgula solta fora de aspas), o parser FALHA
  explicitamente em vez de remontar o valor silenciosamente unindo os
  campos extras (comportamento antigo, removido nesta revisão: mascarava
  um CSV malformado como se fosse válido). Só uma vírgula corretamente
  entre aspas (que o `csv.reader` já reduz a exatamente 2 campos) é aceita.
- Falha explicitamente (`AdsPreambleError`) se o header tabular `#,...` não
  for encontrado — antes, um arquivo sem header tratava o arquivo inteiro
  como preâmbulo, o que mascarava um formato quebrado em vez de rejeitá-lo.
- Detecta labels conhecidas repetidas no preâmbulo (mesmo valor ou valores
  conflitantes) e falha — um preâmbulo bem formado nunca repete uma chave
  estrutural; repetição é sinal de um formato alterado que não deve ser
  interpretado silenciosamente.
- `ID da Loja` agora é OBRIGATÓRIO (está presente nos 10 arquivos reais
  auditados) e validado como string SOMENTE de dígitos — deixou de ser
  melhor-esforço.
- Nenhuma exceção interna (`ValueError` de `date`/`datetime`, incompatível
  de regex) chega a conter o texto bruto da célula: todo componente é
  validado por regex ANTES de qualquer construção de data, e qualquer
  `except ValueError: raise AdsPreambleError(...) from None` só encapsula
  erros nativos do Python (ex.: "day is out of range for month") que nunca
  ecoam o valor original — nem em `args`, nem em `__cause__`/`__context__`.
- Nenhuma mensagem de erro deste módulo inclui valor de célula, nome de
  loja ou username — só nomes de campo (estruturais, não sensíveis).

Minimização (revisão de 2026-07-06): `shop_username`/`shop_display_name`
foram REMOVIDOS do resultado — não há necessidade concreta identificada que
`brand` (já presente no manifesto) e `shop_id` não atendam. Se uma
necessidade real surgir, os campos podem ser adicionados por uma migration
explícita, nunca reintroduzidos silenciosamente.

Confirmado por inspeção read-only em 2026-07-06: os 10 CSVs locais têm
preâmbulo estruturado e IDÊNTICO em formato (mesmas chaves, na mesma ordem),
incluindo os 2 da kokeshi — cujo `Período` no preâmbulo tem a data completa
(`01/01/2026 - 19/03/2026` e `20/03/2026 - 20/06/2026`), apesar do nome do
arquivo não ter o ano.

Nenhum campo aqui é PII de comprador: são todos metadados operacionais do
RELATÓRIO/LOJA (ID da loja, data de geração do relatório, período coberto)
— não há nome, telefone, endereço ou qualquer dado de cliente/comprador no
preâmbulo.

`report_created_at` é **naive** (sem timezone) — representa o horário
exibido pelo Seller Center no momento da geração do relatório. A Shopee não
informa o fuso horário exato neste export; NÃO deve ser assumido como UTC
nem como o fuso do servidor que roda este parser. Uso deste campo em
comparações entre relatórios de fusos diferentes não é seguro sem
confirmação adicional — ele serve hoje só como metadado informativo de
"quando o relatório foi gerado", não como componente do período coberto
(que é `report_period_start`/`report_period_end`, esses sim em datas puras,
sem componente de hora).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

_LABEL_SHOP_ID = "ID da Loja"
_LABEL_CREATED_AT = "Data de Criação do Relatório"
_LABEL_PERIOD = "Período"

# Só os campos efetivamente usados (minimização) — "Nome de Usuário"/"Nome
# da loja" não são mais reconhecidos: se aparecerem no preâmbulo, caem no
# mesmo tratamento de qualquer rótulo desconhecido (ignorados, sem erro).
_KNOWN_LABELS = {_LABEL_SHOP_ID, _LABEL_CREATED_AT, _LABEL_PERIOD}
# Revisão de 2026-07-06 (3ª rodada): "ID da Loja" passou a ser obrigatório
# — presente nos 10 arquivos reais auditados, deixou de ser melhor-esforço.
_REQUIRED_LABELS = {_LABEL_SHOP_ID, _LABEL_CREATED_AT, _LABEL_PERIOD}

_RE_BR_DATE = re.compile(r"^([0-9]{2})/([0-9]{2})/([0-9]{4})$")
_RE_BR_DATETIME = re.compile(r"^([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2})$")
_RE_DIGITS_ONLY = re.compile(r"^[0-9]+$")


class AdsPreambleError(Exception):
    """Preâmbulo ausente, incompleto ou em formato não reconhecido. Nunca
    carrega valor de célula/loja em `args` — só nomes de campo e motivos
    estruturais, seguro para logar/printar sem risco de vazamento."""


@dataclass(frozen=True)
class AdsPreamble:
    report_period_start: date
    report_period_end: date
    report_created_at: datetime
    shop_id: str

    def to_jsonb_dict(self) -> dict:
        """Forma serializável, minimizada, para `source_metadata jsonb` —
        só ISO 8601 para datas/horas, nunca objetos Python. Chaves estáveis
        (não mudam entre versões do parser sem uma migration de dado
        explícita). `report_created_at` é naive — ver docstring do módulo
        sobre timezone desconhecido."""
        return {
            "period_start": self.report_period_start.isoformat(),
            "period_end": self.report_period_end.isoformat(),
            "report_created_at": self.report_created_at.isoformat(),
            "shop_id": self.shop_id,
        }


def _find_header_index(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.startswith("#,"):
            return i
    raise AdsPreambleError("cabeçalho tabular '#,...' não encontrado no CSV")


def _split_csv_fields(line: str) -> Optional[list[str]]:
    """Interpreta uma linha com `csv.reader` — respeita aspas e vírgulas
    dentro do valor. `None` só se a linha não render nenhum campo (linha
    totalmente vazia). Não decide nada sobre rótulo conhecido aqui."""
    try:
        fields = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return None
    return fields or None


def _parse_kv_lines(preamble_lines: list[str]) -> dict[str, str]:
    """Linhas conhecidas no formato 'Rótulo,Valor'. Rótulos desconhecidos
    são ignorados, INDEPENDENTE de quantos campos CSV a linha tenha
    (permite preâmbulos com campos extras/malformados no futuro, desde que
    não sejam um rótulo que este parser precisa usar).

    Para um rótulo CONHECIDO, a linha deve render EXATAMENTE 2 campos CSV
    — se render mais (vírgula fora de aspas — a única forma de uma vírgula
    legítima dentro do valor não contar como um 3º campo é estar
    corretamente entre aspas, que o `csv.reader` já reduz a 2 campos),
    falha explicitamente em vez de remontar o valor unindo os campos
    extras (comportamento antigo removido: mascarava CSV malformado).

    Rótulo CONHECIDO repetido — mesmo valor ou não — também falha: um
    preâmbulo bem formado nunca repete uma chave estrutural."""
    kv: dict[str, str] = {}
    for line in preamble_lines:
        fields = _split_csv_fields(line)
        if fields is None or len(fields) < 2:
            continue
        label = fields[0].strip()
        if label not in _KNOWN_LABELS:
            continue
        if len(fields) != 2:
            raise AdsPreambleError(
                f"campo '{label}' com formatação CSV inesperada "
                "(mais de 2 colunas — vírgula fora de aspas?)"
            )
        value = fields[1].strip()
        if not value:
            continue
        if label in kv:
            raise AdsPreambleError(f"campo '{label}' aparece mais de uma vez no preâmbulo")
        kv[label] = value
    return kv


def _parse_br_date(text: str) -> date:
    m = _RE_BR_DATE.match(text.strip())
    if not m:
        raise AdsPreambleError("campo de data em formato inesperado (esperado DD/MM/YYYY)")
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        raise AdsPreambleError("data de calendário impossível no campo 'Período'") from None


def _parse_br_datetime(text: str) -> datetime:
    m = _RE_BR_DATETIME.match(text.strip())
    if not m:
        raise AdsPreambleError(
            "campo 'Data de Criação do Relatório' em formato inesperado (esperado DD/MM/YYYY HH:MM)"
        )
    day, month, year, hour, minute = (int(g) for g in m.groups())
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        raise AdsPreambleError(
            "data/hora de calendário impossível no campo 'Data de Criação do Relatório'"
        ) from None


def _parse_period(text: str) -> tuple[date, date]:
    parts = text.split(" - ")
    if len(parts) != 2:
        raise AdsPreambleError(
            "campo 'Período' em formato inesperado (esperado 'DD/MM/YYYY - DD/MM/YYYY')"
        )
    start = _parse_br_date(parts[0])
    end = _parse_br_date(parts[1])
    return start, end


def parse_ads_preamble(lines: list[str]) -> AdsPreamble:
    """`lines` é a lista de linhas de texto do CSV JÁ DECODIFICADO (mesma
    saída de `inventory._decode_ads_csv`), incluindo as linhas de anúncios
    — esta função só olha para ANTES do header `#,...` e nunca acessa as
    linhas de dados.

    Lança `AdsPreambleError` (nunca retorna dado parcial silenciosamente)
    se: o header `#,...` não existir; `Período`, `Data de Criação do
    Relatório` ou `ID da Loja` estiverem ausentes; qualquer um dos dois
    primeiros estiver em formato não reconhecido ou com calendário
    impossível; o período tiver início posterior ao fim; `ID da Loja` não
    for composto só de dígitos; ou qualquer rótulo conhecido aparecer
    duplicado no preâmbulo. `ID da Loja` deixou de ser melhor-esforço
    (revisão de 2026-07-06, 3ª rodada) — está presente nos 10 arquivos
    reais auditados."""
    header_idx = _find_header_index(lines)
    kv = _parse_kv_lines(lines[:header_idx])

    missing = _REQUIRED_LABELS - kv.keys()
    if missing:
        raise AdsPreambleError(f"campo(s) obrigatório(s) ausente(s) no preâmbulo: {sorted(missing)}")

    period_start, period_end = _parse_period(kv[_LABEL_PERIOD])
    if period_start > period_end:
        raise AdsPreambleError("período com início posterior ao fim")
    created_at = _parse_br_datetime(kv[_LABEL_CREATED_AT])

    shop_id = kv[_LABEL_SHOP_ID]
    if not _RE_DIGITS_ONLY.match(shop_id):
        raise AdsPreambleError("campo 'ID da Loja' não é composto só por dígitos")

    return AdsPreamble(
        report_period_start=period_start,
        report_period_end=period_end,
        report_created_at=created_at,
        shop_id=shop_id,
    )
