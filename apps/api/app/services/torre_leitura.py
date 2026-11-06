"""Leitura do banco da propria Torre (Neon), no formato `Consultar`.

As abas CX e Performance leem o Data Mart por `consultar(sql, params)`. A
Kokeshi, que nao tem app na Shopee, chega pelo bot do Seller Centre e mora no
Neon (migrations 034 e 036). Esta funcao tem a MESMA assinatura, para os
servicos tratarem as duas fontes do mesmo jeito (e os testes injetarem um
dublê). Falha sobe como excecao: quem chama isola e vira estado `erro`.
"""
from __future__ import annotations

from sqlalchemy import text


class TorreIndisponivel(RuntimeError):
    """Engine do Neon nao configurado (o motivo fica em `FALHA_DO_ENGINE`)."""


def consultar(sql: str, params: dict) -> list[dict]:
    from app.database import engine  # import tardio: testes nao precisam de banco

    if engine is None:
        raise TorreIndisponivel("engine local nao inicializado")
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]
