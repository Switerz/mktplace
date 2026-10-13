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
    "disponivel", "parcial", "indisponivel", "fonte_inacessivel", "nao_reconciliado",
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


class Periodo(BaseModel):
    date_from: date
    date_to: date
    meses: list[str]


class DREResponse(BaseModel):
    periodo: Periodo
    filtros: dict[str, list[str]]
    fontes: dict[str, Literal["ok", "inacessivel"]]
    regras: dict[str, int | str | list[float]]
    total: CelulaDRE | None = None
    por_canal: list[CelulaDRE]
    por_marca: list[CelulaDRE]
    por_canal_marca: list[CelulaDRE]
    celulas_base: list[CelulaDRE]
