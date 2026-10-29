"""METAS-1 — contrato de /api/v1/metas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.metas_service import META_MAXIMA

Marca = Literal["apice", "barbours", "kokeshi", "lescent", "rituaria"]
Canal = Literal["tiktok", "ml", "shopee"]


class CelulaMeta(BaseModel):
    brand: Marca
    channel: Canal
    meta_gmv: float


class MetasMesResponse(BaseModel):
    mes: str
    marcas: list[str]
    canais: list[str]
    #: So' as celulas COM meta. Celula ausente = sem meta (nunca zero).
    celulas: list[CelulaMeta]
    ultima_alteracao: Optional[datetime]


class SalvarMetasResponse(MetasMesResponse):
    alteracoes: int


class CelulaMetaEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand: Marca
    channel: Canal
    #: `null` remove a meta da celula.
    meta_gmv: Optional[Decimal] = Field(
        default=None, ge=0, le=META_MAXIMA, max_digits=12, decimal_places=2,
        allow_inf_nan=False,
    )


class SalvarMetasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    celulas: list[CelulaMetaEntrada] = Field(min_length=1, max_length=15)

    @model_validator(mode="after")
    def _sem_celula_repetida(self) -> "SalvarMetasRequest":
        vistas = set()
        for c in self.celulas:
            chave = (c.brand, c.channel)
            if chave in vistas:
                raise ValueError(f"celula repetida: {c.brand}/{c.channel}")
            vistas.add(chave)
        return self
