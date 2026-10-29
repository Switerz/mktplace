"""T-0014 — schema da Performance das contas (`/api/v1/performance-contas`).

Contrato: `docs/performance_contas_contrato.md` (§1.1 decisoes, §4 estados,
§6 tela minima).

OFICIAL x TORRE
---------------
`tipo = "oficial"` e' a metrica que a PLATAFORMA usa para reputacao, penalidade
ou score. `tipo = "torre"` e' um calculo nosso. Um indicador Torre NUNCA carrega
limiar de origem oficial: pintar um numero nosso com a faixa da plataforma faria
a tela afirmar uma penalidade que a plataforma nao aplicou. O validador abaixo
recusa a combinacao na construcao do objeto.

AUSENCIA NUNCA VIRA ZERO
------------------------
So' o estado `dado` carrega `valor`. Em `vazio_legitimo`, `sem_fonte`,
`nao_comprovado` e `erro` o `valor` e' obrigatoriamente `None`. Um zero real
(fonte respondeu e mediu zero) continua `dado` com `valor = 0.0`.

UNIDADE
-------
`pct` e' ponto percentual de 0 a 100 (4.5 = 4,5%). O `limiar.valor` esta na
MESMA unidade do indicador.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, model_validator

CanalPerformance = Literal["mercado_livre", "shopee", "tiktok"]
TipoIndicador = Literal["oficial", "torre"]
EstadoIndicador = Literal[
    "dado", "vazio_legitimo", "sem_fonte", "nao_comprovado", "erro",
]
UnidadeIndicador = Literal["pct", "contagem"]
OrigemLimiar = Literal["oficial", "interno"]

#: Ordem fixa dos canais na resposta.
ORDEM_CANAIS: tuple[str, ...] = ("mercado_livre", "shopee", "tiktok")


class Limiar(BaseModel):
    valor: Optional[float] = None
    origem: OrigemLimiar
    rotulo: str


class Indicador(BaseModel):
    codigo: str
    rotulo: str
    tipo: TipoIndicador
    estado: EstadoIndicador
    valor: Optional[float] = None
    unidade: Optional[UnidadeIndicador] = None
    janela: Optional[str] = None
    limiar: Optional[Limiar] = None
    fonte: str
    atualizado_em: Optional[datetime] = None
    aviso_frescor: Optional[str] = None
    cobertura: Optional[str] = None
    ajuda: str
    motivo_indisponivel: Optional[str] = None

    @model_validator(mode="after")
    def _regras_do_contrato(self) -> "Indicador":
        if self.tipo == "torre" and self.limiar is not None and self.limiar.origem == "oficial":
            raise ValueError("indicador Torre nao pode carregar limiar de origem oficial")
        if self.estado == "dado" and self.valor is None:
            raise ValueError("estado 'dado' exige valor")
        if self.estado != "dado" and self.valor is not None:
            raise ValueError("so' o estado 'dado' carrega valor")
        return self


class MarcaFora(BaseModel):
    marca: str
    motivo: str


class Cobertura(BaseModel):
    marcas: list[str]
    fora: list[MarcaFora]


class CanalResposta(BaseModel):
    canal: CanalPerformance
    cobertura: Cobertura
    indicadores: list[Indicador]


class PerformanceContasResponse(BaseModel):
    gerado_em: datetime
    canais: list[CanalResposta]
