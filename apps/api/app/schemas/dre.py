"""Contrato de `/api/v1/dre` — DRE-MVP-1 (ver `docs/dre_contrato_0.md`).

INDISPONIVEL NAO E' ZERO
-------------------------
`valor=None` sempre vem com um `status` que diz por que. Zero so' aparece quando
a fonte mediu zero. A tela precisa exibir o status, nunca "R$ 0".

O nome do resultado e' "margem de contribuicao conhecida". Nada neste contrato
se chama margem liquida ou lucro.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Classificacao = Literal["COMPLETA", "PARCIAL", "NAO_CALCULAVEL"]
StatusComponente = Literal[
    "disponivel", "parcial", "indisponivel", "snapshot_ausente", "snapshot_vencido", "nao_reconciliado",
    "nao_confirmado", "provisorio", "sinal_inesperado", "nao_aplicavel",
]
NomeComponente = Literal["receita", "devolucoes", "taxas", "afiliados", "ads", "frete", "cmv", "impostos"]


class Componente(BaseModel):
    #: Magnitude (custo >= 0, receita >= 0). `None` = nao observado.
    valor: float | None = None
    pct_receita: float | None = None
    status: StatusComponente
    motivo: str | None = None
    fonte: str | None = None
    #: `False` para receita e para afiliados TikTok (ja dentro de taxas).
    somavel: bool
    #: Decomposicao (so' afiliados TikTok: creator, partner, ads).
    partes: dict[str, float | None] | None = None
    #: So' para insumos publicados (CMV, taxa e frete TikTok): data maxima da
    #: origem e instante da publicacao do lote usado.
    data_as_of: datetime | None = None
    publicado_em: datetime | None = None


class Componentes(BaseModel):
    receita: Componente
    devolucoes: Componente
    taxas: Componente
    afiliados: Componente
    ads: Componente
    frete: Componente
    cmv: Componente
    impostos: Componente


class ComponenteAusente(BaseModel):
    componente: NomeComponente
    status: StatusComponente
    motivo: str | None = None


class Completude(BaseModel):
    usados: int
    aplicaveis: int
    texto: str


class Margem(BaseModel):
    valor: float | None = None
    pct_receita: float | None = None
    rotulo: str
    formula: str | None = None
    componentes_usados: list[str]
    faltam: list[str]


class DataMaxima(BaseModel):
    receita_ultimo_dia: date | None = None
    mart_carregado_em: datetime | None = None
    cmv_ultima_emissao: date | None = None
    repasse_tiktok_atualizado_em: datetime | None = None
    insumo_publicado_em: datetime | None = None


#: ---------------------------------------------------------------------------
#: Cenario ESTIMADO com premissas (DRE-PREMISSAS-1). Complementar e opcional:
#: nunca substitui nem altera `margem_contribuicao_conhecida`.
#: ---------------------------------------------------------------------------

EstadoCenario = Literal["calculavel", "parcial", "nao_calculavel"]
OrigemLinha = Literal["realizado", "premissa", "decomposicao", "indisponivel", "calculado", "misto",
                      "cmp_provisorio"]


class LinhaCenario(BaseModel):
    linha: str
    rotulo: str
    #: `item` entra na soma (se `somavel`); `subtotal` e' calculado; `informativo` nunca soma.
    tipo: Literal["item", "subtotal", "informativo"]
    #: Magnitude. `None` = ausente/indisponivel — nunca zero fabricado.
    valor: float | None = None
    pct_receita: float | None = None
    origem: OrigemLinha
    #: Taxa da matriz aplicada na celula base (0..1). Sempre `None` em agregados:
    #: o agregado soma celulas, nao reaplica taxa.
    percentual: float | None = None
    status: str
    motivo: str | None = None
    somavel: bool


class ComponenteAusenteCenario(BaseModel):
    linha: str
    motivo: str | None = None


class FontePremissas(BaseModel):
    """Procedencia PUBLICA: rotulo, competencia, data da fonte e hash. Nunca o
    nome do arquivo de orcamento nem um caminho local."""
    #: Ex.: "Premissas de outubro/2026" (derivado da competencia).
    rotulo: str
    competencia: str
    data_fonte: date | None = None
    source_sha256: str | None = None
    carregado_em: datetime | None = None


class CmvProvisorio(BaseModel):
    """CMV PROVISORIO por CMP (DRE-CMV-CMP-1). Separado do fiscal; nunca o substitui."""
    status: Literal["calculado", "ausente_na_fonte"]
    data_corte: date | None = None
    receita_fonte: float | None = None
    receita_coberta: float | None = None
    cobertura_pct: float | None = None
    cmv_provisorio: float | None = None
    unidades_sem_cmp: float | None = None
    skus_total: int | None = None
    skus_sem_cmp: int | None = None
    skus_conflito: int | None = None
    #: Receita coberta por metodo de casamento (gtin, volume, codigo_exato_provisorio).
    receita_por_metodo: dict[str, float | None]
    confianca: Literal["alta", "media", "baixa"] | None = None
    regra_temporal: str | None = None
    #: True so' quando o fiscal estava inutilizavel e a cobertura atingiu o minimo.
    usado_na_margem: bool
    celulas_ausentes_na_fonte: int | None = None


class CenarioPremissas(BaseModel):
    competencia_premissas: str
    natureza: Literal["estimada"]
    rotulo: str
    estado: EstadoCenario
    #: Competencia em andamento: o cenario e' uma PREVIA.
    previa: bool
    competencia_madura: bool
    receita_base: float | None = None
    fonte_cmv: Literal["dre_realizada", "cmp_sku", "misto"]
    cmv_provisorio: CmvProvisorio | None = None
    linhas: list[LinhaCenario]
    componentes_ausentes: list[ComponenteAusenteCenario]
    motivos: list[str]
    avisos: list[str]
    margem_contribuicao_estimada: float | None = None
    margem_contribuicao_estimada_pct: float | None = None
    fonte_premissas: FontePremissas
    celulas: int


class CelulaDRE(BaseModel):
    escopo: str | None = None
    mes: str | None = None
    canal: str | None = None
    canal_label: str | None = None
    marca: str | None = None
    marca_label: str | None = None
    celulas: int | None = None
    classificacao: Classificacao
    motivos_classificacao: list[str]
    avisos_qualidade: list[str]
    componentes: Componentes
    componentes_disponiveis: list[NomeComponente]
    componentes_ausentes: list[ComponenteAusente]
    completude: Completude
    margem_contribuicao_conhecida: Margem
    data_maxima: DataMaxima
    #: Opcional e retrocompativel. `None` = competencia sem premissa publicada.
    cenario_premissas: CenarioPremissas | None = None


class Periodo(BaseModel):
    date_from: date
    date_to: date
    meses: list[str]
    #: `padrao_competencia_madura` quando a requisicao nao informou periodo.
    origem: Literal["informado", "padrao_competencia_madura"] = "informado"


class ResumoInsumos(BaseModel):
    estado: Literal["ok", "tabela_ausente", "nao_publicado", "vencido", "parcialmente_vencido"]
    #: Referencia CONSERVADORA: a publicacao mais antiga do conjunto servido.
    publicado_em: datetime | None = None
    publicado_em_mais_antigo: datetime | None = None
    publicado_em_mais_recente: datetime | None = None
    motivo: str | None = None


class ResumoPremissas(BaseModel):
    estado: Literal["ok", "parcial", "tabela_ausente", "nao_publicado", "lote_invalido", "nao_consultado"]
    competencias_publicadas: list[str]
    source_sha256: str | None = None
    rotulo: str | None = None
    data_fonte: date | None = None
    carregado_em: datetime | None = None
    motivo: str | None = None


class Fontes(BaseModel):
    neon: Literal["ok"]
    insumos_dre: ResumoInsumos
    premissas_dre: ResumoPremissas | None = None


class DREResponse(BaseModel):
    periodo: Periodo
    filtros: dict[str, list[str]]
    fontes: Fontes
    regras: dict[str, int | str | list[float]]
    total: CelulaDRE | None = None
    por_canal: list[CelulaDRE]
    por_marca: list[CelulaDRE]
    por_canal_marca: list[CelulaDRE]
    celulas_base: list[CelulaDRE]
