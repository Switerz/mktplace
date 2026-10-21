"""Contrato da aba "B2B / Revendedores" do Monitoramento de Precos.

Gate TORRE-B2B-OCTAPRICE. Fonte: Torre B2B (relatorio diario do OctaPrice),
lida somente leitura pelas RPCs `fn_monit_*`. Contrato completo e campos
consumidos: docs/b2b_revenda_contrato.md.

O PISO DESTA ABA E' O PMA INFORMADO PELO OCTAPRICE. Nao e' o PDV das tabelas
B2B usado na visao "Lojas proprias", e os dois nao se comparam.

SEM DADO DE CLIENTE. Nenhum modelo aqui tem cliente, vendedor, seller, CNPJ,
e-mail ou telefone — so' agregados. Rotas com esses campos dependem da
autenticacao da Torre de Marketplaces, que ainda nao existe (teste de contrato
trava isso).

INDISPONIVEL NAO E' ZERO. Flag desligada, credencial ausente, fonte fora do ar,
contrato quebrado e dia sem relatorio devolvem 200 com `status="unavailable"`
e motivo proprio. Um zero so' aparece quando a fonte respondeu zero.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

MotivoTecnico = Literal[
    "feature_flag_desligada",
    "credencial_ausente",
    "credencial_invalida",
    "configuracao_invalida",
    "fonte_inacessivel",
    "permissao_negada",
    "contrato_da_fonte_quebrado",
    "fonte_lenta",
    "falha_desconhecida",
    "fonte_sem_dados",
    "sem_relatorio_na_data",
]

SituacaoFrescor = Literal["em_dia", "atrasado"]


class Frescor(BaseModel):
    """De QUANDO e' o dado. Sempre descreve a carga MAIS RECENTE da fonte,
    mesmo quando a consulta pede um dia anterior."""

    data_ref_mais_recente: date
    #: Janela da coleta do OctaPrice (o relatorio do dia D coleta na noite de D-1).
    coleta_inicio: Optional[datetime] = None
    coleta_fim: Optional[datetime] = None
    #: Primeira e ultima gravacao da carga na Torre B2B (`created_at`).
    importado_inicio: Optional[datetime] = None
    importado_fim: Optional[datetime] = None
    linhas_na_carga: int
    situacao: SituacaoFrescor
    #: Dia de relatorio que ja' deveria existir a esta hora (BRT).
    data_ref_esperada: date
    dias_de_atraso: int
    #: Quando ESTA API leu a fonte (o cache pode servir por ate' 30 min).
    consultado_em: datetime
    #: Dias com relatorio disponivel na fonte (retencao de ~90 dias la').
    datas_disponiveis: list[date]


class CanalExcluido(BaseModel):
    canal: str
    motivo: str


class Escopo(BaseModel):
    piso: Literal["pma_octaprice"] = "pma_octaprice"
    aviso_piso: str
    canais_nos_kpis: list[str]
    #: Canais presentes na carga mais recente e FORA dos KPIs, com o porque.
    canais_excluidos: list[CanalExcluido]
    #: Canais da allowlist que NAO aparecem na carga mais recente. Grafia
    #: errada na allowlist ("Mercadolivre" x "Mercado Livre") tiraria o canal
    #: dos KPIs em silencio; aqui ela aparece.
    canais_kpi_sem_dados: list[str] = Field(default_factory=list)


class FiltrosAplicados(BaseModel):
    data_ref: date
    marcas: list[str] = Field(default_factory=list)
    canais: list[str] = Field(default_factory=list)
    severidades: list[str] = Field(default_factory=list)
    skus: list[str] = Field(default_factory=list)
    ufs: list[str] = Field(default_factory=list)


class _Base(BaseModel):
    status: Literal["ok"] = "ok"
    #: Dia consultado. Pode ser anterior ao mais recente (consulta historica).
    data_ref: date
    consulta_historica: bool
    frescor: Frescor
    escopo: Escopo
    filtros: FiltrosAplicados
    limitacoes: list[str]


# ---------------------------------------------------------------------------
# /frescor
# ---------------------------------------------------------------------------
class FrescorResponse(BaseModel):
    status: Literal["ok"] = "ok"
    frescor: Frescor
    escopo: Escopo
    limitacoes: list[str]


# ---------------------------------------------------------------------------
# /resumo
# ---------------------------------------------------------------------------
class Severidade(BaseModel):
    """Faixas da fonte sobre o desvio do PMA: leve >= -5%, media de -15% a
    -5%, grave < -15%. Nao recebe o filtro de severidade: e' o breakdown."""

    leve: int
    media: int
    grave: int


class BuyBox(BaseModel):
    anuncios_com_buy_box: int
    violando_com_buy_box: int


class Kpis(BaseModel):
    anuncios: int
    #: Anuncios com a flag de violacao do OctaPrice (restrita a severidade
    #: filtrada, quando houver).
    anuncios_violando: int
    #: % arredondado pela fonte (1 casa). `None` = sem anuncio no recorte.
    pct_violacao: Optional[Decimal] = None
    sellers_violando: int
    skus_monitorados: int
    #: FRACAO (−0,12 = 12% abaixo do PMA), media simples entre os que violam.
    desvio_medio_pma: Optional[Decimal] = None
    buy_box: BuyBox
    severidade: Severidade


class CoberturaIdentificacao(BaseModel):
    """Quanto do problema ja' tem cliente B2B atribuido na Torre B2B.

    Responde "quais clientes" so' para esta fracao: o resto e' seller sem
    vinculo. Contagens apenas — nenhum nome sai da fonte.
    """

    sellers_violando: int
    sellers_violando_identificados: int
    pct_sellers: Optional[Decimal] = None
    anuncios_violando: int
    anuncios_violando_identificados: int
    pct_anuncios: Optional[Decimal] = None
    #: Os sellers contados aqui batem com o KPI `sellers_violando`?
    consistente: bool


class ResumoResponse(_Base):
    kpis: Kpis
    cobertura_identificacao: CoberturaIdentificacao


# ---------------------------------------------------------------------------
# /canais-marcas
# ---------------------------------------------------------------------------
class LinhaMarcaCanal(BaseModel):
    #: Codigo da fonte (AP, BB, KS) ou `None` = marca nao mapeada na carga.
    marca_id: Optional[str] = None
    marca: str
    canal: Optional[str] = None
    anuncios: int
    anuncios_violando: int
    #: Calculado AQUI (violando / anuncios, 1 casa). `None` sem anuncio.
    pct_violacao: Optional[Decimal] = None
    desvio_medio_pma: Optional[Decimal] = None
    preco_medio: Optional[Decimal] = None


class CanaisMarcasResponse(_Base):
    linhas: list[LinhaMarcaCanal]


# ---------------------------------------------------------------------------
# /skus
# ---------------------------------------------------------------------------
class LinhaSku(BaseModel):
    sku: str
    marca_id: Optional[str] = None
    marca: str
    #: Titulo de UM anuncio do SKU, como veio do OctaPrice (ilustrativo).
    exemplo_anuncio: Optional[str] = None
    anuncios: int
    anuncios_violando: int
    pct_violacao: Optional[Decimal] = None
    desvio_medio_pma: Optional[Decimal] = None


class SkusResponse(_Base):
    linhas: list[LinhaSku]
    #: Ranking por anuncios violando. So' SKUs com ao menos uma violacao.
    limite: int


# ---------------------------------------------------------------------------
# Indisponivel
# ---------------------------------------------------------------------------
class B2BRevendaUnavailableResponse(BaseModel):
    """Indisponivel. NUNCA confundir com zero violacoes."""

    status: Literal["unavailable"] = "unavailable"
    motivo_tecnico: MotivoTecnico
    unavailable_reason: str
    #: Presente quando a fonte respondeu o frescor mas nao o recorte pedido.
    frescor: Optional[Frescor] = None
    limitacoes: list[str] = Field(default_factory=list)
