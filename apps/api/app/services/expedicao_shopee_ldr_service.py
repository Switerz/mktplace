"""EXPEDICAO-SHOPEE-LDR-1 — serving da LDR operacional da Shopee.

SOMENTE SELECT. Le `marts.expedicao_shopee_ldr_daily` e
`marts.expedicao_shopee_ldr_sem_prazo_daily`, publicadas por
`pipelines.expedicao.shopee_ldr_cli`.

NAO E' A METRICA DE PENALIZACAO DA SHOPEE
-----------------------------------------
A resposta SEMPRE traz `official = false` e o aviso pronto (`disclaimer`). Nao
ha' "meta" aqui: a Shopee nao nos entrega a referencia dela para este recorte,
e inventar um limiar faria a tela dizer "dentro da meta" sem base.

QUANDO A TAXA VIRA "—"
-----------------------
  * nenhum vencimento maduro na janela (`rate = None`, nunca 0);
  * cobertura insuficiente: mais de 10% da base elegivel sem prazo ou sem
    marco de coleta. Acima de 2% a taxa aparece com aviso.

ESTADOS SEM 500
---------------
  disabled            flag desligada; nenhuma consulta e' emitida
  table_not_migrated  migration 030 ainda nao aplicada
  no_series_published nada publicado na janela (ou para a marca filtrada)
  `snapshot.stale`    publicacao mais velha que o limite: a tela diz "fonte
                      vencida" em vez de mostrar o numero como atual
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from app.config import settings

TABELA = "marts.expedicao_shopee_ldr_daily"
TABELA_SEM_PRAZO = "marts.expedicao_shopee_ldr_sem_prazo_daily"
CANAL = "shopee"

UNAVAILABLE_DISABLED = "disabled"
UNAVAILABLE_NOT_MIGRATED = "table_not_migrated"
UNAVAILABLE_NO_DATA = "no_series_published"

#: Espelham `pipelines.expedicao.shopee_ldr`. Repetidos aqui porque a API nao
#: importa o pacote de pipelines; teste de contrato trava a igualdade.
NOME_METRICA = "LDR operacional (coleta × prazo Shopee)"
AVISO_NAO_OFICIAL = (
    "Indicador interno da Torre: não é a taxa de envio atrasado que a Shopee "
    "usa para penalização."
)
LIMIAR_AVISO_COBERTURA = 0.02
LIMIAR_OCULTA_TAXA = 0.10

JANELA_PADRAO_DIAS = 7
#: O publisher reescreve 35 dias de vencimento para tras; ler mais que isso
#: misturaria a janela viva com historico que nao e' mais recalculado.
JANELA_MAX_DIAS = 35

#: Publicacao mais velha que isto e' "fonte vencida". Mesmo limite que a fila
#: da Shopee usa na tela para "Desatualizada".
LIMITE_FOTOGRAFIA_HORAS = 6.0

BRT = timedelta(hours=-3)


def habilitado() -> bool:
    """Duas flags: a Expedicao no ar E a LDR Shopee liberada.

    Separadas para que ligar a LDR nao dependa de derrubar ou religar a fila, e
    para que a LDR nasca desligada mesmo com a Expedicao em producao.
    """
    return bool(getattr(settings, "expedicao_api_enabled", False)) and bool(
        getattr(settings, "expedicao_shopee_ldr_api_enabled", False))


def _hoje_brt(agora: Optional[datetime] = None) -> date:
    return ((agora or datetime.now(timezone.utc)).astimezone(timezone.utc) + BRT).date()


def _vazio(motivo: Optional[str], disponivel: bool = False) -> dict:
    return {
        "availability": "available" if disponivel else "unavailable",
        "unavailable_reason": motivo,
        "channel": CANAL,
        "metric_name": NOME_METRICA,
        "official": False,
        "disclaimer": AVISO_NAO_OFICIAL,
        "event": "coleta",
        "thresholds": {"coverage_warning": LIMIAR_AVISO_COBERTURA,
                       "coverage_hide_rate": LIMIAR_OCULTA_TAXA},
        "snapshot": None,
        "ldr": None,
        "exclusions": None,
        "coverage": None,
        "brands": [],
        "alerts": [],
    }


def _tabela_existe(db) -> bool:
    return db.execute(
        text("SELECT to_regclass(:a) IS NOT NULL AND to_regclass(:b) IS NOT NULL AS ok"),
        {"a": TABELA, "b": TABELA_SEM_PRAZO},
    ).scalar() is True


VENCIMENTOS_SQL = f"""
SELECT deadline_date, shop_account, brand, pedidos_pagos, excl_fbs,
       excl_cancelado_sem_coleta, cancelado_vendedor_nao_enviou,
       cancelado_motivo_desconhecido, excl_enviado_sem_coleta,
       base, no_prazo, atrasado, pendente_vencido, pendente_no_prazo, is_mature,
       refresh_batch_id, effective_at, source_watermark_at
  FROM {TABELA}
 WHERE channel = :canal AND deadline_date >= :desde AND deadline_date <= :ate
 ORDER BY deadline_date, brand, shop_account
"""

SEM_PRAZO_SQL = f"""
SELECT paid_date, brand, sem_prazo
  FROM {TABELA_SEM_PRAZO}
 WHERE channel = :canal AND paid_date >= :desde AND paid_date <= :ate
"""


def taxa(atraso: int, base: int) -> Optional[float]:
    """`None` e nao `0.0` quando nao ha base."""
    return (atraso / base) if base > 0 else None


def cobertura(base: int, sem_prazo: int, enviado_sem_coleta: int) -> dict:
    ausentes = sem_prazo + enviado_sem_coleta
    elegivel = base + ausentes
    if elegivel == 0:
        return {"state": "sem_base", "missing_share": None, "missing": 0, "eligible": 0,
                "without_deadline": sem_prazo, "shipped_without_pickup": enviado_sem_coleta}
    f = ausentes / elegivel
    estado = ("insuficiente" if f > LIMIAR_OCULTA_TAXA
              else "aviso" if f > LIMIAR_AVISO_COBERTURA else "completa")
    return {"state": estado, "missing_share": f, "missing": ausentes, "eligible": elegivel,
            "without_deadline": sem_prazo, "shipped_without_pickup": enviado_sem_coleta}


def _somar(linhas: list[dict]) -> dict:
    s = {k: 0 for k in ("pedidos_pagos", "excl_fbs", "excl_cancelado_sem_coleta",
                        "cancelado_vendedor_nao_enviou", "cancelado_motivo_desconhecido",
                        "excl_enviado_sem_coleta", "base", "no_prazo", "atrasado",
                        "pendente_vencido", "pendente_no_prazo")}
    for x in linhas:
        for k in s:
            s[k] += int(x[k])
    return s


def obter_ldr(
    db,
    *,
    dias: int = JANELA_PADRAO_DIAS,
    brands: Optional[list[str]] = None,
    agora: Optional[datetime] = None,
) -> dict:
    """LDR por vencimento na janela `[hoje - dias, hoje]` + exclusoes + cobertura."""
    if not habilitado():
        return _vazio(UNAVAILABLE_DISABLED)
    if not _tabela_existe(db):
        return _vazio(UNAVAILABLE_NOT_MIGRATED)

    ref = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hoje = _hoje_brt(ref)
    dias = max(1, min(dias, JANELA_MAX_DIAS))
    desde = hoje - timedelta(days=dias)
    # Le ate' amanha: o pedido que vence amanha esta' fora da janela historica
    # e e' justamente o que ainda da' para salvar (`pending_at_risk`).
    ate_leitura = hoje + timedelta(days=1)

    alvo = {b.strip().lower() for b in brands} if brands else None
    linhas = [dict(r) for r in db.execute(
        text(VENCIMENTOS_SQL), {"canal": CANAL, "desde": desde, "ate": ate_leitura},
    ).mappings()]
    # Sem prazo: so' pagamentos de dias FECHADOS, a mesma faixa dos vencimentos
    # que entram na taxa. Contar hoje inflaria a ausencia com pedido que a
    # Shopee ainda nem terminou de publicar.
    sem_prazo = [dict(r) for r in db.execute(
        text(SEM_PRAZO_SQL),
        {"canal": CANAL, "desde": desde, "ate": hoje - timedelta(days=1)},
    ).mappings()]
    if alvo is not None:
        linhas = [x for x in linhas if str(x["brand"]).lower() in alvo]
        sem_prazo = [x for x in sem_prazo if str(x["brand"]).lower() in alvo]
    if not linhas:
        return _vazio(UNAVAILABLE_NO_DATA)

    na_janela = [x for x in linhas if x["deadline_date"] <= hoje]
    maduras = [x for x in na_janela if x["is_mature"]]
    abertas = [x for x in linhas if not x["is_mature"]]

    # ----- serie diaria por vencimento -----------------------------------
    por_dia: dict[date, list[dict]] = {}
    for x in na_janela:
        por_dia.setdefault(x["deadline_date"], []).append(x)
    diario = []
    for d in sorted(por_dia):
        s = _somar(por_dia[d])
        madura = all(x["is_mature"] for x in por_dia[d])
        atraso = s["atrasado"] + s["pendente_vencido"]
        diario.append({
            "due_date": d, "base": s["base"], "late": atraso,
            "shipped_on_time": s["no_prazo"], "shipped_late": s["atrasado"],
            "pending_overdue": s["pendente_vencido"], "pending_on_time": s["pendente_no_prazo"],
            "excluded_fbs": s["excl_fbs"],
            "excluded_cancelled": s["excl_cancelado_sem_coleta"],
            "shipped_without_pickup": s["excl_enviado_sem_coleta"],
            "rate": taxa(atraso, s["base"]) if madura else None,
            "is_mature": madura,
        })

    # ----- taxa da janela: so' vencimentos maduros ------------------------
    sm = _somar(maduras)
    sem_prazo_total = sum(int(x["sem_prazo"]) for x in sem_prazo)
    cob = cobertura(sm["base"], sem_prazo_total, sm["excl_enviado_sem_coleta"])
    atraso_m = sm["atrasado"] + sm["pendente_vencido"]
    taxa_bruta = taxa(atraso_m, sm["base"])
    taxa_exibida = None if cob["state"] == "insuficiente" else taxa_bruta

    # Pendente "no prazo" so' se o prazo ainda nao passou: um vencimento de
    # ontem publicado antes da virada do dia ainda carrega `pendente_no_prazo`,
    # e chama-lo de "ainda pode ser coletado" seria falso.
    abertas_futuras = [x for x in abertas if x["deadline_date"] >= hoje]
    risco = sum(int(x["pendente_no_prazo"]) for x in abertas_futuras
                if (x["deadline_date"] - hoje).days <= 1)
    # Dia "fechado" so' quando TODAS as contas dele amadureceram.
    dias_maduros = {d["due_date"] for d in diario if d["is_mature"]}
    dias_na_janela = {d["due_date"] for d in diario}

    ldr = {
        "from": desde, "to": hoje, "days": dias,
        "rate": taxa_exibida,
        "rate_hidden_reason": ("coverage_insufficient"
                               if cob["state"] == "insuficiente" and taxa_bruta is not None
                               else None),
        "base": sm["base"], "late": atraso_m,
        "shipped_on_time": sm["no_prazo"], "shipped_late": sm["atrasado"],
        "pending_overdue": sm["pendente_vencido"],
        "pending_on_time": sum(int(x["pendente_no_prazo"]) for x in abertas_futuras),
        "pending_at_risk": risco,
        "mature_due_days": len(dias_maduros),
        "partial_due_days": len(dias_na_janela - dias_maduros),
        "daily": diario,
    }
    exclusoes = {
        "fbs": sm["excl_fbs"],
        "cancelled_without_pickup": sm["excl_cancelado_sem_coleta"],
        "cancelled_seller_did_not_ship": sm["cancelado_vendedor_nao_enviou"],
        "cancelled_unknown_reason": sm["cancelado_motivo_desconhecido"],
        "shipped_without_pickup": sm["excl_enviado_sem_coleta"],
        "without_deadline": sem_prazo_total,
    }

    # ----- por marca (mesma regra de cobertura) ---------------------------
    marcas = []
    for marca in sorted({str(x["brand"]) for x in linhas}):
        mx = [x for x in maduras if str(x["brand"]) == marca]
        s = _somar(mx)
        sp = sum(int(x["sem_prazo"]) for x in sem_prazo if str(x["brand"]) == marca)
        c = cobertura(s["base"], sp, s["excl_enviado_sem_coleta"])
        at = s["atrasado"] + s["pendente_vencido"]
        wms = [x["source_watermark_at"] for x in linhas
               if str(x["brand"]) == marca and x["source_watermark_at"] is not None]
        marcas.append({
            "brand": marca,
            "base": s["base"], "late": at,
            "rate": None if c["state"] == "insuficiente" else taxa(at, s["base"]),
            "coverage_state": c["state"],
            "excluded_fbs": s["excl_fbs"],
            "excluded_cancelled": s["excl_cancelado_sem_coleta"],
            "source_watermark_at": max(wms) if wms else None,
        })

    # ----- frescor ---------------------------------------------------------
    efetivo = max(x["effective_at"] for x in linhas)
    lote = next(x["refresh_batch_id"] for x in linhas if x["effective_at"] == efetivo)
    idade = (ref - efetivo).total_seconds() / 3600.0
    wm_todos = [x["source_watermark_at"] for x in linhas if x["source_watermark_at"]]
    # "Fonte vencida" tem dois sintomas: publicacao velha (o agendador parou)
    # OU publicacao nova sobre fonte parada (`effective_at` recente, leitura
    # mais recente da fonte velha). Sem leitura nenhuma tambem e' vencida.
    idade_fonte = ((ref - max(wm_todos)).total_seconds() / 3600.0) if wm_todos else None
    snapshot = {
        "refresh_batch_id": str(lote),
        "effective_at": efetivo,
        "source_watermark_at": min(wm_todos) if wm_todos else None,
        "today_brt": hoje,
        "age_hours": round(idade, 2),
        "source_age_hours": None if idade_fonte is None else round(idade_fonte, 2),
        "stale": (idade > LIMITE_FOTOGRAFIA_HORAS or idade_fonte is None
                  or idade_fonte > LIMITE_FOTOGRAFIA_HORAS),
    }

    resposta = _vazio(None, disponivel=True)
    resposta.update({
        "snapshot": snapshot,
        "ldr": ldr,
        "exclusions": exclusoes,
        "coverage": cob,
        "brands": marcas,
        "alerts": _alertas(ldr, exclusoes, cob, snapshot),
    })
    return resposta


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}".replace(".", ",") + "%"


def _alertas(ldr: dict, exc: dict, cob: dict, snap: dict) -> list[dict]:
    """Texto pronto para a tela; a tela nao reimplementa limiar."""
    out: list[dict[str, Any]] = []
    if snap["stale"]:
        if snap["age_hours"] > LIMITE_FOTOGRAFIA_HORAS:
            causa = f"última publicação há {snap['age_hours']:.0f} h"
        elif snap["source_age_hours"] is None:
            causa = "a publicação não registra leitura da fonte"
        else:
            causa = f"a fonte não recebe dado novo há {snap['source_age_hours']:.0f} h"
        out.append({"severity": "critical", "code": "fonte_vencida",
                    "message": f"Fonte vencida: {causa}. Os números abaixo não são atuais."})
    if cob["state"] == "insuficiente":
        out.append({"severity": "critical", "code": "cobertura_insuficiente",
                    "message": (f"Taxa não exibida: {_pct(cob['missing_share'])} da base "
                                f"elegível sem prazo ou sem marco de coleta (limite "
                                f"{_pct(LIMIAR_OCULTA_TAXA)}).")})
    elif cob["state"] == "aviso":
        out.append({"severity": "warning", "code": "cobertura_incompleta",
                    "message": (f"Cobertura incompleta: {_pct(cob['missing_share'])} da "
                                f"base elegível sem prazo ou sem marco de coleta.")})
    if exc["cancelled_unknown_reason"] > 0:
        out.append({"severity": "warning", "code": "motivo_cancelamento_desconhecido",
                    "message": (f"{exc['cancelled_unknown_reason']} cancelamento(s) com "
                                f"motivo fora do vocabulário conhecido. Qualidade de dado "
                                f"a revisar.")})
    if ldr["pending_overdue"] > 0:
        out.append({"severity": "critical", "code": "pendentes_vencidos",
                    "message": (f"{ldr['pending_overdue']} pedido(s) passaram do prazo "
                                f"e ainda não foram coletados.")})
    if ldr["pending_at_risk"] > 0:
        out.append({"severity": "warning", "code": "em_risco",
                    "message": (f"{ldr['pending_at_risk']} pedido(s) vencem hoje ou "
                                f"amanhã e ainda podem ser coletados.")})
    return out
