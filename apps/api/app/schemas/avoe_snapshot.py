"""Gate AVH-4B-S Task 1/2 — schema do serving read-only do snapshot manual Avoe.

O QUE ESTE CONTRATO E', E O QUE ELE NAO E'
-----------------------------------------
E' a leitura de UMA captura manual da Avoe, ja publicada em `marts` pelo
importador do AVH-4A. NAO e' KPI da Torre, nao substitui nem complementa
`fact_marketplace_daily_performance`, e nao entra em `/overview`, `/canais` ou
qualquer outro contrato existente. Por isso `is_official_torre_source` e' um
campo FIXO em `false`: a resposta declara a propria natureza, em vez de
depender de quem consome saber disso.

METAS E CANAIS SAO BLOCOS SEPARADOS
-----------------------------------
`targets` e' meta comercial por marca x competencia. `extra_channels` e'
faturamento INFORMADO pela Avoe em canais que a Torre nao integra. Nao existe
campo de atingimento, realizado, margem ou variacao: cruzar meta com realizado
exige contrato proprio e uma definicao aprovada de realizado, e nenhuma das
duas existe. Somar os dois blocos tambem nao e' contrato — sao grandezas
diferentes.

AUSENCIA NUNCA E' ZERO
----------------------
`reported_amount` e' `Optional[float]` porque a coluna de origem e' nullable:
`None` significa "a Avoe nao informou", e jamais e' substituido por `0.0`. Zero
e' um valor informado e continua chegando como `0.0`. Os dois estados sao
distinguiveis na resposta de proposito.

MOEDA ASSUMIDA
--------------
A origem nao declara moeda. `currency_status = 'assumed_unconfirmed'` viaja em
toda linha, com o aviso correspondente, ate' a Avoe confirmar.

PROXY POR CONSTRUCAO
--------------------
Toda linha de canal tem `is_proxy = true` e `definition_status = 'unconfirmed'`:
a Avoe nao documentou o que o numero mede (bruto? liquido? com frete?). O
`definition_warning` vem do banco, nao e' montado aqui.

INDISPONIBILIDADE E' 200, NAO ERRO
----------------------------------
Sem captura valida, a resposta e' HTTP 200 com `status = 'unavailable'`, arrays
vazios e `unavailable_reason` factual. Nenhuma outra rota e' afetada.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

#: Estado do serving. `available` exige captura completa, unica e conciliada
#: com um run de auditoria `success`; qualquer duvida cai em `unavailable`.
AvoeSnapshotStatus = Literal["available", "unavailable"]

#: Natureza da fonte, fixa. Existe para que a resposta se declare externa e
#: manual sem depender de documentacao externa.
AvoeSourceKind = Literal["external_manual_snapshot"]

#: Como o `sync_run_id` foi associado a captura.
#:
#: `audit_time_window` e' o UNICO metodo disponivel hoje, e e' uma limitacao
#: medida: `audit.source_sync_run` nao tem coluna de ligacao com as tabelas de
#: snapshot (nao ha `captured_at`, `snapshot_id` nem `import_run_id` la'). A
#: associacao usa a janela [started_at, finished_at] do run contra o
#: `imported_at` das linhas, e so' e' aceita quando casa exatamente um run
#: `success`. Ambiguidade ou ausencia derruba para `unavailable`.
AvoeSyncRunLinkMethod = Literal["audit_time_window"]

#: Motivos de indisponibilidade. Fechados de proposito: sao os estados que o
#: servico consegue PROVAR, e nenhum deles ecoa texto de excecao.
AvoeUnavailableReason = Literal[
    "no_snapshot_published",
    "targets_and_channels_capture_mismatch",
    "capture_incomplete",
    "capture_mixes_multiple_imports",
    "duplicate_grain_in_capture",
    "audit_run_not_conclusive",
    "serving_inconsistent",
]

CurrencyStatus = Literal["confirmed", "assumed_unconfirmed"]
DefinitionStatus = Literal["unconfirmed"]
CoverageStatus = Literal["partial_month", "full_month"]


class AvoeSnapshotMeta(BaseModel):
    """Metadados da captura servida (ou da ausencia dela)."""

    status: AvoeSnapshotStatus
    #: Fonte, sempre `avoe_hub`. Nao e' fonte oficial da Torre.
    source: Literal["avoe_hub"]
    source_kind: AvoeSourceKind
    #: FIXO em False. A Avoe e' terceiro, digitada a mao, sem contrato de dados.
    is_official_torre_source: Literal[False]
    #: Instante da captura na origem. `None` quando indisponivel.
    captured_at: Optional[str]
    #: Hash de conteudo dos arquivos do snapshot. Proveniencia, nao infra.
    snapshot_id: Optional[str]
    #: Run de `audit.source_sync_run`. `None` quando nao pode ser associado.
    sync_run_id: Optional[int]
    sync_run_status: Optional[str]
    sync_run_link_method: Optional[AvoeSyncRunLinkMethod]
    #: Contagens da captura servida. Zero quando indisponivel.
    targets_count: int
    channel_rows_count: int
    #: Competencias presentes, em ISO `YYYY-MM-DD` (primeiro dia do mes).
    target_ref_months: list[str]
    channel_ref_months: list[str]
    currency: Optional[str]
    currency_status: Optional[CurrencyStatus]
    #: Instante em que ESTA resposta foi montada. Nao e' frescor do dado: o
    #: dado e' de `captured_at`, e a distancia entre os dois e' a defasagem.
    refreshed_at: str
    #: Idade da captura em dias corridos, para a tela nao ter de calcular.
    captured_age_days: Optional[int]
    unavailable_reason: Optional[AvoeUnavailableReason]
    #: Avisos factuais. Sempre inclui o de fonte externa/manual.
    warnings: list[str]


class AvoeTargetRow(BaseModel):
    """Meta comercial por marca x competencia. Sem realizado, sem atingimento."""

    brand: str
    #: Chave canonica da marca na Torre, quando existe. `None` quando a marca
    #: nao tem correspondencia — e ai' NAO se inventa uma.
    brand_key: Optional[str]
    ref_month: str
    target_amount: float
    currency_code: str
    currency_status: CurrencyStatus
    currency_warning: Optional[str]
    source_recorded_at: Optional[str]


class AvoeExtraChannelRow(BaseModel):
    """Faturamento INFORMADO pela Avoe num canal que a Torre nao integra."""

    brand: str
    brand_key: Optional[str]
    channel: str
    #: Rotulo bruto do canal na origem, preservado para rastreio.
    channel_source_label: str
    ref_month: str
    #: `None` = a Avoe nao informou. `0.0` = a Avoe informou zero. Nunca se
    #: converte um no outro.
    reported_amount: Optional[float]
    is_proxy: bool
    definition_status: DefinitionStatus
    definition_warning: str
    currency_code: str
    currency_status: CurrencyStatus
    #: Cobertura medida do mes: quantos dias uteis a origem trouxe, e quais.
    days_covered: int
    first_business_date: str
    last_business_date: str
    coverage_status: CoverageStatus
    source_recorded_at: Optional[str]


class AvoeSnapshotLimitations(BaseModel):
    """Limitacoes estruturais, expostas no proprio contrato."""

    #: Nao ha conector: a captura e' exportacao humana.
    manual_snapshot: bool
    #: Nao ha agendamento: nada atualiza estas tabelas sozinho.
    automated_refresh: bool
    #: A definicao de `reported_amount` nao foi confirmada pela Avoe.
    channel_amount_definition_confirmed: bool
    #: A moeda nao e' declarada pela origem.
    currency_confirmed: bool
    #: Nao existe realizado nesta fonte, nem cruzamento com o da Torre.
    provides_realized_amount: bool
    #: Nao existe atingimento, margem nem variacao calculada aqui.
    provides_attainment_or_margin: bool
    #: Estes numeros nao substituem nem alimentam KPI canonico da Torre.
    replaces_canonical_torre_kpi: bool
    notes: list[str]


class AvoeSnapshotResponse(BaseModel):
    meta: AvoeSnapshotMeta
    targets: list[AvoeTargetRow]
    extra_channels: list[AvoeExtraChannelRow]
    limitations: AvoeSnapshotLimitations
