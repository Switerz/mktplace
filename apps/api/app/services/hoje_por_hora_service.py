"""VENDAS-POR-HORA-1 — o "Hoje" padronizado: os canais cortados na MESMA hora cheia.

Le' `marts.fact_marketplace_hourly_sales` (vendas por hora, gravadas pela DAG
`marketplace_tower_dia_corrente` via `pipelines.sync_vendas_por_hora`) e soma
de 00:00 ate' o corte, hoje, ontem e no mesmo dia da semana passada.

🔑 O CORTE
----------
`corte_hora = H` significa "vendas de 00:00 ate' H:00" (horas 0..H-1). E' a
MENOR hora fechada entre os canais do filtro: se a Shopee so' chegou ate' 11h,
ninguem passa de 11h. Assim os canais sao comparaveis entre si, e hoje e'
comparavel com ontem.

A KOKESHI NA SHOPEE
-------------------
Chega por arquivo, sem hora de pedido: nao esta' na tabela. Quando o filtro a
inclui, a resposta diz isso em `limitacoes` — ausencia nao vira zero calado.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps.period import APP_TIMEZONE

TABELA = "marts.fact_marketplace_hourly_sales"
FONTES = "marts.fact_marketplace_hourly_sales_fonte"

CANAL_POR_ID = {1: "tiktok", 2: "ml", 3: "shopee"}
SHOPEE_ID = 3

LIMITACAO_KOKESHI_SHOPEE = (
    "Kokeshi na Shopee fica fora do corte por hora: os pedidos dela chegam por "
    "arquivo exportado, sem o horário de cada pedido."
)


def _bloco(gmv, pedidos) -> dict:
    gmv = float(gmv or 0)
    pedidos = int(pedidos or 0)
    return {"gmv": round(gmv, 2), "pedidos": pedidos,
            "ticket": round(gmv / pedidos, 2) if pedidos else None}


def projetar_fechamento(hoje_ate_corte: float, ontem_ate_corte: float, ontem_dia: float) -> Optional[float]:
    """Onde o dia tende a fechar pelo RITMO DE ONTEM: o que ontem ja' tinha
    vendido ate' a mesma hora diz que fracao do dia aquilo era. Sem venda ontem
    ate' o corte nao ha' ritmo para usar: `None`, nunca um palpite."""
    if ontem_ate_corte <= 0 or ontem_dia <= 0:
        return None
    return round(hoje_ate_corte * ontem_dia / ontem_ate_corte, 2)


def hora_fechada_do_canal(fonte_ate: Optional[datetime], hoje: date) -> int:
    """Quantas horas de HOJE o canal ja' fechou (0 a 24)."""
    if fonte_ate is None:
        return 0
    local = fonte_ate.astimezone(APP_TIMEZONE)
    if local.date() < hoje:
        return 0
    if local.date() > hoje:
        return 24
    return local.hour


def calcular_corte(fontes: dict[int, Optional[datetime]], mkt_ids: list[int],
                   agora: datetime) -> int:
    """Menor hora fechada entre os canais do filtro, nunca alem da hora atual."""
    hoje = agora.astimezone(APP_TIMEZONE).date()
    horas = [hora_fechada_do_canal(fontes.get(m), hoje) for m in mkt_ids]
    return min(horas + [agora.astimezone(APP_TIMEZONE).hour])


def get_hoje_por_hora(db: Session, mkt_ids: list[int], brand_keys: Optional[list[str]],
                      agora: Optional[datetime] = None) -> dict:
    agora = (agora or datetime.now(APP_TIMEZONE)).astimezone(APP_TIMEZONE)
    hoje = agora.date()
    dias = {"hoje": hoje, "ontem": hoje - timedelta(days=1), "semana_passada": hoje - timedelta(days=7)}

    existe = db.execute(text("SELECT to_regclass(:t) IS NOT NULL AND to_regclass(:f) IS NOT NULL"),
                        {"t": TABELA, "f": FONTES}).scalar()
    base = {"dia": hoje.isoformat(), "corte_hora": None, "canais": [], "limitacoes": []}
    if not existe:
        return {**base, "disponivel": False,
                "motivo": "As vendas por hora ainda não estão publicadas no banco."}

    fontes = {r[0]: r[1] for r in db.execute(
        text(f"SELECT marketplace_id, fonte_ate FROM {FONTES} WHERE marketplace_id = ANY(:m)"),
        {"m": mkt_ids})}
    corte = calcular_corte(fontes, mkt_ids, agora)
    canais = [{"channel": CANAL_POR_ID[m],
               "fonte_ate": fontes[m].isoformat() if fontes.get(m) else None,
               "horas_fechadas_hoje": hora_fechada_do_canal(fontes.get(m), hoje)}
              for m in mkt_ids]
    limitacoes = []
    if SHOPEE_ID in mkt_ids and (brand_keys is None or "kokeshi" in brand_keys):
        limitacoes.append(LIMITACAO_KOKESHI_SHOPEE)

    linhas = db.execute(text(f"""
        SELECT h.dia, h.marketplace_id, SUM(h.gmv) AS gmv, SUM(h.pedidos) AS pedidos
        FROM {TABELA} h
        JOIN marts.dim_loja l ON l.loja_id = h.loja_id
        WHERE h.dia = ANY(:dias)
          AND h.hora < :corte
          AND h.marketplace_id = ANY(:mkt_ids)
          AND (CAST(:brands AS text[]) IS NULL OR l.brand_key = ANY(CAST(:brands AS text[])))
        GROUP BY 1, 2
    """), {"dias": list(dias.values()), "corte": corte, "mkt_ids": mkt_ids,
           "brands": brand_keys}).mappings().all()

    somas = {(r["dia"], r["marketplace_id"]): (r["gmv"], r["pedidos"]) for r in linhas}

    # Tendencia de fechamento por marca: hoje e ontem ate' o corte, e ontem inteiro.
    por_marca_linhas = db.execute(text(f"""
        -- por_marca
        SELECT l.brand_key AS brand, h.dia, h.marketplace_id,
               SUM(h.gmv) FILTER (WHERE h.hora < :corte) AS gmv_corte,
               SUM(h.gmv) AS gmv_dia
        FROM {TABELA} h
        JOIN marts.dim_loja l ON l.loja_id = h.loja_id
        WHERE h.dia = ANY(:dias)
          AND h.marketplace_id = ANY(:mkt_ids)
          AND (CAST(:brands AS text[]) IS NULL OR l.brand_key = ANY(CAST(:brands AS text[])))
        GROUP BY 1, 2, 3
    """), {"dias": [dias["hoje"], dias["ontem"]], "corte": corte, "mkt_ids": mkt_ids,
           "brands": brand_keys}).mappings().all() if corte > 0 else []
    # Por marca E canal: quem mostra a tendencia soma so' os canais do valor
    # ao lado dela (ex.: so' os canais com meta), nunca um conjunto diferente.
    marcas: dict[str, dict[int, dict]] = {}
    for r in por_marca_linhas:
        m = marcas.setdefault(r["brand"], {}).setdefault(
            r["marketplace_id"], {"hoje": 0.0, "ontem_ate": 0.0, "ontem_dia": 0.0})
        if r["dia"] == dias["hoje"]:
            m["hoje"] = float(r["gmv_corte"] or 0)
        else:
            m["ontem_ate"], m["ontem_dia"] = float(r["gmv_corte"] or 0), float(r["gmv_dia"] or 0)

    def _tendencia(partes: list[dict]) -> dict:
        hoje_, ate, dia = (sum(p[k] for p in partes) for k in ("hoje", "ontem_ate", "ontem_dia"))
        return {"hoje": round(hoje_, 2), "ontem_mesma_hora": round(ate, 2), "ontem_dia": round(dia, 2),
                "projecao": projetar_fechamento(hoje_, ate, dia)}

    por_marca = [{
        "brand": b, **_tendencia(list(canais.values())),
        "por_canal": [{"channel": CANAL_POR_ID[mk], **_tendencia([p])} for mk, p in sorted(canais.items())],
    } for b, canais in sorted(marcas.items())
        # Kokeshi na Shopee nao tem hora de pedido: a projecao veria so' o resto
        # dela e ficaria ABAIXO do que ja' vendeu. Sem tendencia, nunca uma errada.
        if not (b == "kokeshi" and SHOPEE_ID in mkt_ids)]

    def total(dia: date, ids: list[int]) -> dict:
        gmv = sum(float(somas.get((dia, m), (0, 0))[0] or 0) for m in ids)
        pedidos = sum(int(somas.get((dia, m), (0, 0))[1] or 0) for m in ids)
        return _bloco(gmv, pedidos)

    return {
        **base,
        "disponivel": True,
        "corte_hora": corte,
        "canais": canais,
        "limitacoes": limitacoes,
        **{nome: total(d, mkt_ids) for nome, d in dias.items()},
        "por_canal": [{"channel": CANAL_POR_ID[m], **{nome: total(d, [m]) for nome, d in dias.items()}}
                      for m in mkt_ids],
        "por_marca": por_marca,
    }
