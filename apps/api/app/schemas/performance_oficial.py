"""CX-MKT-2 — schema da secao "Performance" da Expedicao.

`GET /api/v1/expedicao/performance?canal=mercadolivre|shopee|tiktokshop&brands=`

So' metrica OFICIAL de cada plataforma (a nota que ela usa para reputacao e
penalidade), lida das fotografias diarias que o Airflow grava:

  ML      raw.ml_seller_reputation_snapshot   (GET /users/{id} -> seller_reputation)
  Shopee  raw.shopee_account_health_snapshot  (v2.account_health.get_shop_performance)
          raw.shopee_account_health_metric
  TikTok  sem fonte: a API de nota da loja (SPS) e' so' para lojas dos EUA pela doc.

A meta tambem e' a oficial (faixas MLB documentadas; `target` que a Shopee
devolve por loja). Nada aqui e' calculo Torre.

AUSENCIA NUNCA VIRA ZERO: tabela ainda nao criada ou conta sem fotografia
aparece como estado explicito; metrica que a plataforma nao devolveu nao
aparece com zero.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.account_performance import Cobertura

CanalOficial = Literal["mercadolivre", "shopee", "tiktokshop"]
EstadoCanal = Literal["dado", "sem_fonte", "erro"]
Tom = Literal["bom", "atencao", "critico", "neutro"]
UnidadeMetrica = Literal["pct", "contagem", "dias", "horas", "segundos", "nota"]
Situacao = Literal["ok", "fora", "sem_meta"]


class MetricaOficial(BaseModel):
    codigo: str
    rotulo: str
    grupo: Optional[str] = None
    valor: Optional[float] = None
    unidade: UnidadeMetrica
    #: Quantidade de vendas afetadas, quando a plataforma informa (ML `value`).
    quantidade: Optional[int] = None
    #: Periodo anterior (Shopee `last_period`).
    anterior: Optional[float] = None
    meta: Optional[float] = None
    comparador: Optional[str] = None
    rotulo_meta: Optional[str] = None
    situacao: Situacao
    ajuda: Optional[str] = None


class Destaque(BaseModel):
    rotulo: str
    valor: str
    tom: Tom = "neutro"


class ContaOficial(BaseModel):
    marca: str
    dia: date
    capturado_em: datetime
    #: "MercadoLíder Platinum", "Verde", "Excelente"...
    nivel: Optional[str] = None
    tom: Tom
    #: Posicao no termometro da plataforma (ML 1-5, Shopee 1-4) e o maximo.
    termometro: Optional[int] = None
    termometro_max: Optional[int] = None
    resumo: Optional[str] = None
    destaques: list[Destaque] = []
    metricas: list[MetricaOficial] = []
    avisos: list[str] = []


class ContaSemFoto(BaseModel):
    marca: str
    motivo: str


class PerformanceOficialResponse(BaseModel):
    gerado_em: datetime
    canal: CanalOficial
    estado: EstadoCanal
    motivo: Optional[str] = None
    fonte: str
    cobertura: Cobertura
    contas: list[ContaOficial] = []
    sem_foto: list[ContaSemFoto] = []
