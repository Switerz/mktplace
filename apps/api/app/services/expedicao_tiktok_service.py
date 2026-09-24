"""Gate EXP-TK-OPS-1 — serving da serie diaria de atraso do TikTok Shop.

SOMENTE SELECT. Le `marts.expedicao_tiktok_dispatch_daily`, publicada por
`pipelines.expedicao.tiktok_cli`.

MODULO SEPARADO DO `expedicao_service`
---------------------------------------
Aquele serve a fotografia da fila (Shopee, ML) e ja tem 783 linhas. Esta
superficie tem outro grao (coorte, nao pedido), outra forma de resposta (serie
temporal, nao fila paginada) e outro conjunto de estados. Misturar as duas
faria cada mudanca aqui arriscar dois canais que ja estao em producao.

A TABELA PODE NAO EXISTIR
--------------------------
A migration 020 nasce junto com este codigo mas NAO e' aplicada pelo mesmo
gate. Enquanto ela nao rodar, `to_regclass` devolve NULL e a resposta sai como
`unavailable` com motivo explicito — nunca 500, e nunca uma tela vazia que
parece "zero atraso". Um erro de infraestrutura que se disfarca de bom
resultado e' o pior desfecho possivel para esta tela em particular.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

TABELA = "marts.expedicao_tiktok_dispatch_daily"
CANAL = "tiktokshop"

UNAVAILABLE_NOT_MIGRATED = "table_not_migrated"
UNAVAILABLE_NO_DATA = "no_series_published"

#: Os dois eventos que competem pelo nome "postado". Ver
#: `pipelines/expedicao/tiktok_daily.py` para a prova de cada um.
EVENTOS = ("despacho", "coleta")
EVENTO_PADRAO = "coleta"

#: Janela padrao da tela: 7 dias + hoje = 8 coortes.
JANELA_PADRAO_DIAS = 7
JANELA_MAX_DIAS = 90

#: Acima disto o dia entra em destaque na tela. Nao e' a regua do TikTok (que
#: nao temos) — e' um limiar operacional para achar o inicio do incidente.
LIMIAR_DIA_CRITICO = 0.10

#: Marca com menos que isto no periodo nao entra no ranking: 1 atraso em 3
#: pedidos vira 33% e lidera um ranking sem significar nada.
MIN_PEDIDOS_RANKING = 50

#: Fuso da operacao. Os carimbos do TikTok chegam em UTC-3 (medido).
BRT = timedelta(hours=-3)


class EventoInvalido(ValueError):
    """Evento fora da allowlist."""


def resolver_evento(valor: Optional[str]) -> str:
    if valor is None:
        return EVENTO_PADRAO
    v = valor.strip().lower()
    if v not in EVENTOS:
        raise EventoInvalido(v)
    return v


def _hoje_brt(agora: Optional[datetime] = None) -> date:
    return ((agora or datetime.now(timezone.utc)).astimezone(timezone.utc) + BRT).date()


def _tabela_existe(db) -> bool:
    return db.execute(text("SELECT to_regclass(:t) IS NOT NULL AS ok"),
                      {"t": TABELA}).scalar() is True


def _indisponivel(motivo: str) -> dict:
    return {
        "availability": "unavailable",
        "unavailable_reason": motivo,
        "channel": CANAL,
        "deadline_is_reconstructed": True,
        "snapshot": None,
        "window": None,
        "daily": [],
        "brands": [],
        "alerts": [],
    }


SERIE_SQL = f"""
SELECT paid_date, brand, shop_name, deadline_date, is_mature,
       pedidos_pagos, cancelados,
       despacho_no_prazo, despacho_atrasado,
       despacho_pendente_vencido, despacho_pendente_no_prazo,
       coleta_no_prazo, coleta_atrasada,
       coleta_pendente_vencida, coleta_pendente_no_prazo,
       refresh_batch_id, effective_at, source_watermark_at
  FROM {TABELA}
 WHERE channel = :canal
   AND paid_date >= :desde
   AND paid_date <= :ate
 ORDER BY paid_date, brand
"""


def _atrasados(linha: dict, evento: str) -> int:
    if evento == "despacho":
        return int(linha["despacho_atrasado"]) + int(linha["despacho_pendente_vencido"])
    return int(linha["coleta_atrasada"]) + int(linha["coleta_pendente_vencida"])


def _pendentes_vencidos(linha: dict, evento: str) -> int:
    chave = ("despacho_pendente_vencido" if evento == "despacho"
             else "coleta_pendente_vencida")
    return int(linha[chave])


def _pendentes_no_prazo(linha: dict, evento: str) -> int:
    chave = ("despacho_pendente_no_prazo" if evento == "despacho"
             else "coleta_pendente_no_prazo")
    return int(linha[chave])


def _taxa(atrasados: int, pagos: int) -> Optional[float]:
    """`None` e nao `0.0` quando nao ha base.

    Zero entraria numa media como "dia perfeito". A tela precisa distinguir
    "nao atrasou nada" de "nao havia nada".
    """
    return (atrasados / pagos) if pagos > 0 else None


def obter_serie(
    db,
    *,
    evento: Optional[str] = None,
    dias: int = JANELA_PADRAO_DIAS,
    brands: Optional[list[str]] = None,
    agora: Optional[datetime] = None,
) -> dict:
    """A serie diaria consolidada + abertura por marca + alertas.

    `dias` conta ANTES de hoje: `dias=7` devolve 8 coortes.
    """
    ev = resolver_evento(evento)
    if not _tabela_existe(db):
        return _indisponivel(UNAVAILABLE_NOT_MIGRATED)

    hoje = _hoje_brt(agora)
    desde = hoje - timedelta(days=max(1, min(dias, JANELA_MAX_DIAS)))
    linhas = [
        dict(r) for r in db.execute(
            text(SERIE_SQL), {"canal": CANAL, "desde": desde, "ate": hoje}
        ).mappings()
    ]
    if brands:
        alvo = {b.strip().lower() for b in brands}
        linhas = [x for x in linhas if str(x["brand"]).lower() in alvo]
    if not linhas:
        return _indisponivel(UNAVAILABLE_NO_DATA)

    # --- serie por dia (soma numerador e denominador, NUNCA taxas) ----------
    por_dia: dict[date, dict[str, Any]] = {}
    for x in linhas:
        d = x["paid_date"]
        a = por_dia.setdefault(d, {
            "paid_date": d, "deadline_date": x["deadline_date"],
            "pedidos_pagos": 0, "cancelados": 0, "atrasados": 0,
            "pendentes_vencidos": 0, "pendentes_no_prazo": 0, "is_mature": True,
        })
        a["pedidos_pagos"] += int(x["pedidos_pagos"])
        a["cancelados"] += int(x["cancelados"])
        a["atrasados"] += _atrasados(x, ev)
        a["pendentes_vencidos"] += _pendentes_vencidos(x, ev)
        a["pendentes_no_prazo"] += _pendentes_no_prazo(x, ev)
        # Basta UMA marca imatura para o dia inteiro ainda poder mudar.
        a["is_mature"] = a["is_mature"] and bool(x["is_mature"])
        if x["deadline_date"] > a["deadline_date"]:
            a["deadline_date"] = x["deadline_date"]

    diario = []
    for d in sorted(por_dia):
        a = por_dia[d]
        taxa = _taxa(a["atrasados"], a["pedidos_pagos"])
        diario.append({
            **a,
            "rate": taxa,
            # `is_critical` so' existe para coorte madura: destacar em vermelho
            # um dia que ainda nao venceu acusa a operacao por algo que ela
            # ainda pode cumprir.
            "is_critical": bool(a["is_mature"] and taxa is not None
                                and taxa >= LIMIAR_DIA_CRITICO),
        })

    maduros = [x for x in diario if x["is_mature"]]
    pagos_m = sum(x["pedidos_pagos"] for x in maduros)
    atras_m = sum(x["atrasados"] for x in maduros)
    taxas = [x["rate"] for x in maduros if x["rate"] is not None]

    # --- abertura por marca ------------------------------------------------
    por_marca: dict[str, dict[str, Any]] = {}
    for x in linhas:
        if not bool(x["is_mature"]):
            continue
        b = str(x["brand"])
        a = por_marca.setdefault(b, {
            "brand": b, "shop_name": x["shop_name"],
            "pedidos_pagos": 0, "atrasados": 0,
        })
        a["pedidos_pagos"] += int(x["pedidos_pagos"])
        a["atrasados"] += _atrasados(x, ev)
    marcas = sorted(
        (
            {**a, "rate": _taxa(a["atrasados"], a["pedidos_pagos"])}
            for a in por_marca.values()
            if a["pedidos_pagos"] >= MIN_PEDIDOS_RANKING
        ),
        key=lambda a: (-(a["rate"] or 0.0), -a["pedidos_pagos"], a["brand"]),
    )

    # --- inicio do incidente ----------------------------------------------
    inicio = next((x["paid_date"] for x in diario if x["is_critical"]), None)
    fim = next((x["paid_date"] for x in reversed(diario) if x["is_critical"]), None)

    # --- acionavel agora ---------------------------------------------------
    # Pendentes que JA venceram (perdido, mas ainda precisa sair) e pendentes
    # que ainda estao no prazo (a fila que da' para salvar).
    vencidos_pendentes = sum(x["pendentes_vencidos"] for x in diario)
    no_prazo_pendentes = sum(x["pendentes_no_prazo"] for x in diario)
    em_risco = sum(
        x["pendentes_no_prazo"] for x in diario
        if not x["is_mature"] and (x["deadline_date"] - hoje).days <= 1
    )

    snapshot = linhas[0]
    alertas = _alertas(
        diario=diario, marcas=marcas, vencidos_pendentes=vencidos_pendentes,
        em_risco=em_risco, effective_at=snapshot["effective_at"], agora=agora,
    )

    return {
        "availability": "available",
        "unavailable_reason": None,
        "channel": CANAL,
        "event": ev,
        # A tela PRECISA exibir isto: o prazo nao e' o do TikTok.
        "deadline_is_reconstructed": True,
        "snapshot": {
            "refresh_batch_id": str(snapshot["refresh_batch_id"]),
            "effective_at": snapshot["effective_at"],
            "source_watermark_at": snapshot["source_watermark_at"],
            "today_brt": hoje,
        },
        "window": {
            "days": dias,
            # `from` e' a menor data COM DADO, nao o inicio pedido. Se a
            # publicacao cobre menos dias que a janela escolhida, dizer
            # "de 25/08" enquanto a serie comeca em 03/09 faz o operador achar
            # que houve 9 dias sem atraso nenhum.
            "from": min(x["paid_date"] for x in diario),
            "to": hoje,
            # As duas leituras, sempre as duas. Medido em 2026-09-24: elas
            # divergiram 3,96 pp na mesma janela. Publicar so' uma seria
            # escolher sem prova de qual a plataforma usa.
            "rate_ratio_of_totals": _taxa(atras_m, pagos_m),
            "rate_mean_of_daily": (sum(taxas) / len(taxas)) if taxas else None,
            "mature_cohorts": len(maduros),
            "partial_cohorts": len(diario) - len(maduros),
            "paid_orders": pagos_m,
            "late_orders": atras_m,
            "pending_overdue": vencidos_pendentes,
            "pending_on_time": no_prazo_pendentes,
            "pending_at_risk": em_risco,
            "incident_start": inicio,
            "incident_end": fim,
        },
        "daily": diario,
        "brands": marcas,
        "alerts": alertas,
    }


def _pct(v: Optional[float], casas: int = 1) -> str:
    """Porcentagem em pt-BR. O alerta e' TEXTO PRONTO que vai para a tela.

    Sem isto sai `88.9%` e `2026-09-07` no meio de uma interface em portugues —
    o operador le' como se fosse de outro sistema e desconfia do numero.
    """
    if v is None:
        return "—"
    return f"{v * 100:.{casas}f}".replace(".", ",") + "%"


def _data(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _alertas(*, diario, marcas, vencidos_pendentes, em_risco, effective_at, agora):
    """Alertas DENTRO da Torre. Nao existe canal externo nesta superficie.

    Cada alerta carrega `severity` e um texto ja resolvido: a tela nao deve
    reimplementar o limiar, senao o alerta e o destaque do grafico podem
    discordar.
    """
    out: list[dict] = []
    criticos = [x for x in diario if x["is_critical"]]
    if criticos:
        pior = max(criticos, key=lambda x: x["rate"] or 0.0)
        out.append({
            "severity": "critical",
            "code": "dias_criticos",
            "message": (
                f"{len(criticos)} dia(s) acima de {_pct(LIMIAR_DIA_CRITICO, 0)} na janela. "
                f"Pior: {_data(pior['paid_date'])} com {_pct(pior['rate'])}."
            ),
        })
    if vencidos_pendentes > 0:
        out.append({
            "severity": "critical",
            "code": "pendentes_vencidos",
            # Texto VOLTADO AO OPERADOR: acentuacao correta, ao contrario dos
            # comentarios e docstrings deste repositorio, que sao ASCII.
            "message": (
                f"{vencidos_pendentes} pedido(s) passaram do prazo e ainda não "
                f"foram registrados."
            ),
        })
    if em_risco > 0:
        out.append({
            "severity": "warning",
            "code": "em_risco",
            "message": (
                f"{em_risco} pedido(s) vencem hoje ou amanhã e ainda podem ser "
                f"tratados."
            ),
        })
    piores = [m for m in marcas if (m["rate"] or 0.0) >= LIMIAR_DIA_CRITICO]
    if piores:
        out.append({
            "severity": "warning",
            "code": "marcas_concentram",
            "message": (
                "Concentrado em: "
                + ", ".join(f"{m['brand']} ({_pct(m['rate'])})" for m in piores[:4])
                + "."
            ),
        })
    # Frescor: fotografia velha faz o operador agir sobre o passado sem saber.
    if effective_at is not None:
        ref = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc)
        idade_h = (ref - effective_at).total_seconds() / 3600.0
        if idade_h > 12:
            out.append({
                "severity": "warning",
                "code": "fotografia_velha",
                "message": f"Última atualização há {idade_h:.0f} h.",
            })
    return out
