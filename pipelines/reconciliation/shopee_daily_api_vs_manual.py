"""Reconciliacao READ ONLY: shadow da API x fato diaria publicada (export manual).

Gate SHOPEE-MANUAL-RETIREMENT-1. Compara, para as quatro contas com aplicacao
Shopee, a familia de pedidos montada a partir da API (mesma funcao do
`sync_shopee_daily_api_shadow`, lida na hora, sem depender da tabela de shadow
existir) com `marts.fact_marketplace_daily_performance` (marketplace_id = 3),
no maior periodo comum disponivel.

Nao escreve em lugar nenhum. A saida e' agregada: contagens e divergencia
percentual. Valor monetario aparece so' como percentual.

COMO LER — O INVARIANTE
-----------------------
O export congela o status do pedido no dia em que foi tirado; a API traz o
status ATUAL. Por isso cancelados e concluidos divergem por MATURACAO quando o
export foi tirado cedo — e "idade do mes hoje" nao diz nada sobre quando o
arquivo foi tirado.

O que nao depende de status e' o total de pedidos CRIADOS no dia:
`orders + canceled_orders`. Mudanca de status move pedido de um lado para o
outro, mas nao cria nem apaga pedido. Se esse invariante fecha em ~0% num mes,
a divergencia restante do mes e' maturacao de status; se nao fecha, ha pedido
faltando ou sobrando de um dos lados — e isso e' defeito, nao maturacao.

USO
---
    python -m pipelines.reconciliation.shopee_daily_api_vs_manual --desde 2026-01-01 --ate 2026-10-12
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import text

from pipelines.common.db import local_engine
from pipelines.sync_shopee_daily_api_shadow import (
    CONTAS_API, LinhaDiaria, ShopeeDailyShadowError, coletar, hoje_brt, validar_marcas,
)

#: Metricas comparaveis: (nome, valor na API, coluna publicada | funcao).
METRICAS = ("orders", "units_sold", "canceled_orders", "delivered_orders", "seller_shipping_cost")
#: Invariante a maturacao de status (ver cabecalho).
INVARIANTE = "pedidos_criados"
#: Tolerancia do invariante, em pontos percentuais, para o mes ser "explicado por maturacao".
TOLERANCIA_INVARIANTE_PCT = 0.10

SQL_PUBLICADO = text("""
    SELECT l.brand_key AS brand, f.date AS d, f.orders, f.units_sold, f.canceled_orders,
           f.delivered_orders, f.seller_shipping_cost
      FROM marts.fact_marketplace_daily_performance f
      JOIN marts.dim_loja l ON l.loja_id = f.loja_id
     WHERE f.marketplace_id = 3 AND f.date BETWEEN :inicio AND :fim AND l.brand_key = ANY(:marcas)
""")


@dataclass(frozen=True)
class Divergencia:
    marca: str
    mes: str
    metrica: str
    api: float
    publicado: float

    @property
    def pct(self) -> float | None:
        """(api − publicado) / publicado × 100. `None` quando o publicado e' zero."""
        if not self.publicado:
            return None
        return (self.api - self.publicado) / self.publicado * 100


def _valor_api(r: LinhaDiaria, metrica: str) -> float:
    if metrica == INVARIANTE:
        return float(r.orders + r.canceled_orders)
    return float(getattr(r, metrica))


def _valor_pub(r: dict, metrica: str) -> float | None:
    if metrica == INVARIANTE:
        if r["orders"] is None or r["canceled_orders"] is None:
            return None
        return float(r["orders"]) + float(r["canceled_orders"])
    v = r[metrica]
    return None if v is None else float(v)


def comparar(api: list[LinhaDiaria], publicado: list[dict]) -> dict:
    """Funcao PURA. Junta por (marca, dia) e agrega por (marca, mes, metrica).

    Dia presente so' de um lado e' contado e reportado — nunca preenchido com zero.
    Valor publicado ausente (NULL) nao entra na soma de nenhum dos dois lados.
    """
    a = {(r.brand, r.date): r for r in api}
    p = {(r["brand"], r["d"]): r for r in publicado}
    comuns = sorted(set(a) & set(p))
    soma: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for k in comuns:
        marca, d = k
        mes = d.strftime("%Y-%m")
        for met in METRICAS + (INVARIANTE,):
            vp = _valor_pub(p[k], met)
            if vp is None:
                continue
            s = soma[(marca, mes, met)]
            s[0] += _valor_api(a[k], met)
            s[1] += vp
    divs = [Divergencia(marca=m, mes=mes, metrica=met, api=v[0], publicado=v[1])
            for (m, mes, met), v in sorted(soma.items())]
    return {
        "dias_comuns": len(comuns),
        "dias_so_api": sorted(k for k in a if k not in p),
        "dias_so_publicado": sorted(k for k in p if k not in a),
        "divergencias": divs,
    }


def classificar_meses(divs: list[Divergencia]) -> dict[tuple[str, str], str]:
    """(marca, mes) -> `exato` | `maturacao` | `defeito`.

    exato     invariante e todas as metricas dentro da tolerancia;
    maturacao invariante fecha, demais metricas divergem (status amadurecendo);
    defeito   o proprio invariante nao fecha (pedido faltando ou sobrando).
    """
    por: dict[tuple[str, str], dict[str, float | None]] = defaultdict(dict)
    for d in divs:
        por[(d.marca, d.mes)][d.metrica] = d.pct
    out = {}
    for k, mets in por.items():
        inv = mets.get(INVARIANTE)
        if inv is None or abs(inv) > TOLERANCIA_INVARIANTE_PCT:
            out[k] = "defeito"
        elif all(v is not None and abs(v) <= TOLERANCIA_INVARIANTE_PCT for v in mets.values()):
            out[k] = "exato"
        else:
            out[k] = "maturacao"
    return out


def run(inicio: date, fim: date, marcas: tuple[str, ...] | None = None,
        agora: datetime | None = None) -> dict:
    agora_utc = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hoje = hoje_brt(agora_utc)
    alvo = validar_marcas(marcas)
    if fim >= hoje:
        raise ShopeeDailyShadowError(f"--ate precisa ser D-1 ou antes (hoje BRT = {hoje})")
    snap = coletar(alvo, inicio, fim, hoje, agora_utc)
    with local_engine().connect() as conn:
        conn.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        pub = [dict(r) for r in conn.execute(
            SQL_PUBLICADO, {"inicio": inicio, "fim": fim, "marcas": list(alvo)}).mappings().all()]
    res = comparar(snap.rows, pub)
    classes = classificar_meses(res["divergencias"])
    return {
        "janela": [str(inicio), str(fim)], "marcas": list(alvo),
        "dias_comuns": res["dias_comuns"],
        "dias_so_api": [f"{b}/{d}" for b, d in res["dias_so_api"]],
        "dias_so_publicado": [f"{b}/{d}" for b, d in res["dias_so_publicado"]],
        "classe_por_marca_mes": {f"{m}/{mes}": c for (m, mes), c in sorted(classes.items())},
        "por_marca_mes_metrica": [
            {"marca": d.marca, "mes": d.mes, "metrica": d.metrica,
             "div_pct": None if d.pct is None else round(d.pct, 2)}
            for d in res["divergencias"]],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reconciliacao read-only: API Shopee x fato diaria publicada")
    p.add_argument("--desde", type=date.fromisoformat, required=True)
    p.add_argument("--ate", type=date.fromisoformat, required=True)
    p.add_argument("--marcas", nargs="+", default=None, help=f"subconjunto de {list(CONTAS_API)}")
    args = p.parse_args(argv)
    try:
        res = run(args.desde, args.ate, tuple(args.marcas) if args.marcas else None)
    except ShopeeDailyShadowError as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
