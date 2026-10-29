"""METAS-1 — metas mensais de GMV por marca x canal (aba "Metas" da Torre).

O grao e' `mes x brand x channel` (migration 031). Celula sem meta NAO tem
linha: "sem meta" e "meta zero" nao se confundem.

A regra de quais celulas mudam vive em `planejar`, pura e testavel sem banco.
`salvar` so' executa o plano numa transacao, gravando o historico de cada
alteracao — a aba nao tem login, entao o historico e' o que permite desfazer
um erro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Mesmas listas do CHECK da migration 031 (um teste confere).
MARCAS = ("apice", "barbours", "kokeshi", "lescent", "rituaria")
CANAIS = ("tiktok", "ml", "shopee")

#: Teto do CHECK `meta_gmv < 10000000000`.
META_MAXIMA = Decimal("9999999999.99")

#: Faixa de meses aceita. Fora dela e' quase certamente erro de digitacao.
MES_MINIMO = date(2024, 1, 1)
MES_MAXIMO = date(2030, 12, 1)

_RE_MES = re.compile(r"^(\d{4})-(\d{2})$")

Chave = tuple[str, str]


def parse_mes(texto: str) -> date:
    """`AAAA-MM` -> primeiro dia do mes. ValueError com mensagem legivel."""
    m = _RE_MES.match(texto or "")
    if not m:
        raise ValueError("mes deve estar no formato AAAA-MM, ex.: 2026-10")
    ano, mes = int(m.group(1)), int(m.group(2))
    if not 1 <= mes <= 12:
        raise ValueError("mes deve estar entre 01 e 12")
    dia1 = date(ano, mes, 1)
    if not MES_MINIMO <= dia1 <= MES_MAXIMO:
        raise ValueError(
            f"mes deve estar entre {MES_MINIMO:%Y-%m} e {MES_MAXIMO:%Y-%m}"
        )
    return dia1


def _centavos(v: Optional[Decimal]) -> Optional[Decimal]:
    return None if v is None else Decimal(v).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class Alteracao:
    brand: str
    channel: str
    anterior: Optional[Decimal]
    nova: Optional[Decimal]


def planejar(
    atuais: dict[Chave, Decimal], pedidas: dict[Chave, Optional[Decimal]]
) -> list[Alteracao]:
    """Celulas que mudam, na ordem de MARCAS x CANAIS.

    So' as celulas presentes em `pedidas` sao consideradas; as demais ficam
    como estao. `None` remove a meta. Valor igual ao atual (ao centavo) nao e'
    alteracao e nao gera historico.
    """
    plano = []
    for brand in MARCAS:
        for channel in CANAIS:
            chave = (brand, channel)
            if chave not in pedidas:
                continue
            anterior = _centavos(atuais.get(chave))
            nova = _centavos(pedidas[chave])
            if anterior != nova:
                plano.append(Alteracao(brand, channel, anterior, nova))
    return plano


def ler(db: Session, mes: date) -> dict:
    linhas = db.execute(
        text(
            """
            SELECT brand, channel, meta_gmv, updated_at
              FROM marts.meta_gmv_mensal
             WHERE mes = :mes
            """
        ),
        {"mes": mes},
    ).all()
    ordem = {(b, c): i for i, (b, c) in enumerate((b, c) for b in MARCAS for c in CANAIS)}
    celulas = sorted(
        (
            {"brand": r.brand, "channel": r.channel, "meta_gmv": float(r.meta_gmv)}
            for r in linhas
        ),
        key=lambda c: ordem.get((c["brand"], c["channel"]), len(ordem)),
    )
    return {
        "mes": f"{mes:%Y-%m}",
        "marcas": list(MARCAS),
        "canais": list(CANAIS),
        "celulas": celulas,
        "ultima_alteracao": max((r.updated_at for r in linhas), default=None),
    }


def salvar(db: Session, mes: date, pedidas: dict[Chave, Optional[Decimal]]) -> tuple[dict, int]:
    """Aplica `pedidas` ao mes numa unica transacao. Devolve (estado, n alteracoes).

    O lock consultivo por mes serializa dois salvamentos simultaneos do mesmo
    mes: sem ele, os dois leriam o mesmo "anterior" e o historico mentiria.
    """
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": 31_000_000 + mes.year * 100 + mes.month})
        atuais = {
            (r.brand, r.channel): r.meta_gmv
            for r in db.execute(
                text("SELECT brand, channel, meta_gmv FROM marts.meta_gmv_mensal WHERE mes = :mes"),
                {"mes": mes},
            ).all()
        }
        plano = planejar(atuais, pedidas)
        for alt in plano:
            params = {"mes": mes, "brand": alt.brand, "channel": alt.channel}
            if alt.nova is None:
                db.execute(
                    text(
                        "DELETE FROM marts.meta_gmv_mensal"
                        " WHERE mes = :mes AND brand = :brand AND channel = :channel"
                    ),
                    params,
                )
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO marts.meta_gmv_mensal (mes, brand, channel, meta_gmv, updated_at)
                        VALUES (:mes, :brand, :channel, :meta, now())
                        ON CONFLICT (mes, brand, channel)
                        DO UPDATE SET meta_gmv = EXCLUDED.meta_gmv, updated_at = now()
                        """
                    ),
                    {**params, "meta": alt.nova},
                )
            db.execute(
                text(
                    """
                    INSERT INTO marts.meta_gmv_mensal_historico
                        (mes, brand, channel, meta_anterior, meta_nova)
                    VALUES (:mes, :brand, :channel, :anterior, :nova)
                    """
                ),
                {**params, "anterior": alt.anterior, "nova": alt.nova},
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ler(db, mes), len(plano)
