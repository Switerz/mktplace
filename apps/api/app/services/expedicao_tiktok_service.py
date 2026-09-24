"""Gate EXP-TK-OPS-1/2 — serving da LDR de expedicao do TikTok Shop.

SOMENTE SELECT. Le `marts.expedicao_tiktok_dispatch_daily`, publicada por
`pipelines.expedicao.tiktok_cli`.

DUAS LEITURAS, NOMEADAS, COM DENOMINADORES DIFERENTES
------------------------------------------------------
  `ldr`          LDR OPERACIONAL — VENCIMENTOS NA JANELA. A metrica oficial.
                 denominador = pedidos cujo envio VENCE no periodo.
  `payment_flow` FLUXO POR DATA DE PAGAMENTO. A visao que a gestao usa.
                 denominador = pedidos PAGOS no dia.

As duas nunca se confundem no payload. Um feriado empurra tres datas de
pagamento para o mesmo vencimento: pelo pagamento isso vira tres linhas de
~100%, pelo vencimento vira um unico dia ruim com o volume somado. Usar a
coorte de pagamento como se fosse a LDR inflaria qualquer media.

DOIS LIMIARES COM NOMES DIFERENTES
-----------------------------------
  META_TIKTOK_LDR (4%)        referencia operacional do TikTok. O numero e da
                              plataforma; a medicao e nossa e usa prazo
                              reconstruido — nao e a conta de penalizacao dela.
  LIMIAR_CRITICO_INTERNO (10%) severidade nossa, para achar o dia do incidente.

Confundi-los faria a tela dizer "dentro da meta" num dia de 9%.

MODULO SEPARADO DO `expedicao_service`
---------------------------------------
Aquele serve a fotografia da fila (Shopee, ML) e ja tem 783 linhas. Esta
superficie tem outro grao, outra forma de resposta e outro conjunto de estados.
Misturar faria cada mudanca aqui arriscar dois canais em producao.

A TABELA PODE NAO EXISTIR
--------------------------
A migration 020 nasce junto com este codigo mas NAO e' aplicada pelo mesmo
gate. Enquanto ela nao rodar, `to_regclass` devolve NULL e a resposta sai como
`unavailable` com motivo explicito — nunca 500, e nunca uma tela vazia que
parece "zero atraso".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

TABELA = "marts.expedicao_tiktok_dispatch_daily"
CANAL = "tiktokshop"

UNAVAILABLE_NOT_MIGRATED = "table_not_migrated"
UNAVAILABLE_NO_DATA = "no_series_published"

#: Os dois marcos da politica, com prazos DIFERENTES. Espelham
#: `pipelines.expedicao.tiktok_daily.PRAZO_DIAS_UTEIS`.
EVENTOS = ("coleta", "despacho")
EVENTO_PADRAO = "coleta"
SLA_DIAS_UTEIS = {"despacho": 1, "coleta": 2}

#: Referencia operacional do TikTok para a LDR.
#:
#: O NUMERO e' da plataforma; a MEDICAO com que o comparamos e' nossa e usa
#: prazo reconstruido, porque o SLA por pedido nao e' ingerido. Cruzar esta
#: linha significa "acima da referencia pela nossa conta", e NAO "penalizado
#: pelo TikTok" — a plataforma calcula com o proprio relogio e o proprio
#: denominador, aos quais nao temos acesso. Todo texto derivado daqui carrega
#: essa ressalva.
META_TIKTOK_LDR = 0.04

#: Limiar INTERNO de severidade. Nao e' conformidade.
LIMIAR_CRITICO_INTERNO = 0.10

JANELA_PADRAO_DIAS = 7
JANELA_MAX_DIAS = 90

#: Marca com menos que isto no periodo nao entra no ranking: 1 atraso em 3
#: pedidos vira 33% e lidera sem significar nada.
MIN_PEDIDOS_RANKING = 50

#: Fuso da operacao. Os carimbos do TikTok chegam em UTC-3 (medido).
BRT = timedelta(hours=-3)

#: Folga de datas de pagamento a ler para cobrir a janela de vencimento: entre
#: uma sexta e o vencimento de terca ha um fim de semana, e com feriado o vao
#: cresce. Ler so' a janela de vencimento perderia as coortes empurradas por
#: feriado, que sao exatamente as que produzem os dias ruins.
FOLGA_PAGAMENTO_DIAS = 9


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
        "sla_business_days": SLA_DIAS_UTEIS,
        "targets": {
            "tiktok_ldr": META_TIKTOK_LDR,
            "internal_critical": LIMIAR_CRITICO_INTERNO,
        },
        "snapshot": None,
        "ldr": None,
        "payment_flow": [],
        "brands": [],
        "alerts": [],
    }


SERIE_SQL = f"""
SELECT paid_date, brand, shop_name,
       pedidos_pagos_brutos, amostras_excluidas,
       despacho_deadline, despacho_base, despacho_cancelado_antes_sla,
       despacho_no_prazo, despacho_atrasado,
       despacho_pendente_vencido, despacho_pendente_no_prazo, despacho_is_mature,
       coleta_deadline, coleta_base, coleta_cancelado_antes_sla,
       coleta_no_prazo, coleta_atrasada,
       coleta_pendente_vencida, coleta_pendente_no_prazo, coleta_is_mature,
       refresh_batch_id, effective_at, source_watermark_at
  FROM {TABELA}
 WHERE channel = :canal
   AND paid_date >= :desde
   AND paid_date <= :ate
 ORDER BY paid_date, brand
"""


# ---------------------------------------------------------------------------
# Acesso por evento
# ---------------------------------------------------------------------------
def _p(evento: str, sufixo: str) -> str:
    """Nome da coluna do evento. `coleta_atrasada` x `despacho_atrasado`."""
    if evento == "despacho":
        return f"despacho_{sufixo}"
    return f"coleta_{sufixo}"


def _deadline(linha: dict, evento: str) -> date:
    return linha["despacho_deadline"] if evento == "despacho" else linha["coleta_deadline"]


def _base(linha: dict, evento: str) -> int:
    return int(linha["despacho_base"] if evento == "despacho" else linha["coleta_base"])


def _madura(linha: dict, evento: str) -> bool:
    return bool(
        linha["despacho_is_mature"] if evento == "despacho" else linha["coleta_is_mature"]
    )


def _atrasados(linha: dict, evento: str) -> int:
    if evento == "despacho":
        return int(linha["despacho_atrasado"]) + int(linha["despacho_pendente_vencido"])
    return int(linha["coleta_atrasada"]) + int(linha["coleta_pendente_vencida"])


def _enviados_no_prazo(linha: dict, evento: str) -> int:
    return int(linha["despacho_no_prazo"] if evento == "despacho"
               else linha["coleta_no_prazo"])


def _enviados_atrasados(linha: dict, evento: str) -> int:
    return int(linha["despacho_atrasado"] if evento == "despacho"
               else linha["coleta_atrasada"])


def _pend_vencidos(linha: dict, evento: str) -> int:
    return int(linha["despacho_pendente_vencido"] if evento == "despacho"
               else linha["coleta_pendente_vencida"])


def _pend_no_prazo(linha: dict, evento: str) -> int:
    return int(linha["despacho_pendente_no_prazo"] if evento == "despacho"
               else linha["coleta_pendente_no_prazo"])


def _taxa(atrasados: int, base: int) -> Optional[float]:
    """`None` e nao `0.0` quando nao ha base.

    Zero entraria numa media como "dia perfeito". A tela precisa distinguir
    "nao atrasou nada" de "nao havia nada".
    """
    return (atrasados / base) if base > 0 else None


# ---------------------------------------------------------------------------
# Leitura principal
# ---------------------------------------------------------------------------
def obter_serie(
    db,
    *,
    evento: Optional[str] = None,
    dias: int = JANELA_PADRAO_DIAS,
    brands: Optional[list[str]] = None,
    agora: Optional[datetime] = None,
) -> dict:
    """LDR por vencimento + fluxo por pagamento + marcas + alertas.

    `dias` conta ANTES de hoje: `dias=7` cobre 8 datas de vencimento.
    """
    ev = resolver_evento(evento)
    if not _tabela_existe(db):
        return _indisponivel(UNAVAILABLE_NOT_MIGRATED)

    hoje = _hoje_brt(agora)
    dias = max(1, min(dias, JANELA_MAX_DIAS))
    venc_desde, venc_ate = hoje - timedelta(days=dias), hoje
    # Le mais datas de PAGAMENTO do que a janela de vencimento, senao as
    # coortes empurradas por feriado ficam de fora justamente no dia ruim.
    pag_desde = venc_desde - timedelta(days=FOLGA_PAGAMENTO_DIAS)

    linhas = [
        dict(r) for r in db.execute(
            text(SERIE_SQL), {"canal": CANAL, "desde": pag_desde, "ate": venc_ate}
        ).mappings()
    ]
    if brands:
        alvo = {b.strip().lower() for b in brands}
        linhas = [x for x in linhas if str(x["brand"]).lower() in alvo]
    if not linhas:
        return _indisponivel(UNAVAILABLE_NO_DATA)

    # ----- LDR OPERACIONAL: agrega por VENCIMENTO --------------------------
    na_janela = [x for x in linhas if venc_desde <= _deadline(x, ev) <= venc_ate]
    por_venc: dict[date, dict[str, Any]] = {}
    for x in na_janela:
        d = _deadline(x, ev)
        a = por_venc.setdefault(d, {
            "due_date": d, "base": 0, "late": 0,
            "pending_overdue": 0, "pending_on_time": 0, "is_mature": True,
        })
        a["base"] += _base(x, ev)
        a["late"] += _atrasados(x, ev)
        a["pending_overdue"] += _pend_vencidos(x, ev)
        a["pending_on_time"] += _pend_no_prazo(x, ev)
        # Basta UMA marca imatura para o vencimento inteiro ainda poder mudar.
        a["is_mature"] = a["is_mature"] and _madura(x, ev)

    ldr_diario = []
    for d in sorted(por_venc):
        a = por_venc[d]
        taxa = _taxa(a["late"], a["base"])
        ldr_diario.append({
            **a,
            "rate": taxa,
            # Os dois selos existem separados de proposito: um diz
            # conformidade com a plataforma, o outro diz severidade interna.
            "above_target": bool(a["is_mature"] and taxa is not None
                                 and taxa > META_TIKTOK_LDR),
            "is_critical": bool(a["is_mature"] and taxa is not None
                                and taxa >= LIMIAR_CRITICO_INTERNO),
        })

    maduros = [x for x in ldr_diario if x["is_mature"]]
    base_m = sum(x["base"] for x in maduros)
    late_m = sum(x["late"] for x in maduros)
    taxa_ldr = _taxa(late_m, base_m)

    # O acionavel NAO se limita a janela de vencimento. A janela mede o
    # historico, que termina hoje; o pedido que vence AMANHA esta fora dela e
    # e' justamente o que ainda da' para salvar. Restringi-lo a janela zerava o
    # unico numero da tela sobre o qual a operacao pode agir agora.
    pendentes_abertos = [x for x in linhas if not _madura(x, ev)]
    pend_no_prazo_total = sum(_pend_no_prazo(x, ev) for x in pendentes_abertos)
    em_risco = sum(
        _pend_no_prazo(x, ev) for x in pendentes_abertos
        if (_deadline(x, ev) - hoje).days <= 1
    )
    criticos = [x for x in ldr_diario if x["is_critical"]]
    inicio = criticos[0]["due_date"] if criticos else None
    fim = criticos[-1]["due_date"] if criticos else None

    ldr = {
        "from": ldr_diario[0]["due_date"] if ldr_diario else venc_desde,
        "to": venc_ate,
        "days": dias,
        "rate": taxa_ldr,
        "base": base_m,
        "late": late_m,
        "pending_overdue": sum(x["pending_overdue"] for x in maduros),
        "pending_on_time": pend_no_prazo_total,
        "pending_at_risk": em_risco,
        "mature_due_days": len(maduros),
        "partial_due_days": len(ldr_diario) - len(maduros),
        "above_target": bool(taxa_ldr is not None and taxa_ldr > META_TIKTOK_LDR),
        "internal_critical": bool(taxa_ldr is not None
                                  and taxa_ldr >= LIMIAR_CRITICO_INTERNO),
        "incident_start": inicio,
        "incident_end": fim,
        "daily": ldr_diario,
    }

    # ----- FLUXO POR DATA DE PAGAMENTO: as colunas da planilha da gestao ----
    por_pag: dict[date, dict[str, Any]] = {}
    for x in linhas:
        d = x["paid_date"]
        if d < venc_desde - timedelta(days=FOLGA_PAGAMENTO_DIAS):
            continue
        a = por_pag.setdefault(d, {
            "paid_date": d, "deadline_date": _deadline(x, ev),
            "paid_orders": 0, "excluded_samples": 0, "excluded_cancelled": 0,
            "base": 0, "shipped_on_time": 0, "shipped_late": 0,
            "pending_overdue": 0, "pending_on_time": 0, "is_mature": True,
        })
        a["paid_orders"] += int(x["pedidos_pagos_brutos"])
        a["excluded_samples"] += int(x["amostras_excluidas"])
        a["excluded_cancelled"] += int(x[_p(ev, "cancelado_antes_sla")])
        a["base"] += _base(x, ev)
        a["shipped_on_time"] += _enviados_no_prazo(x, ev)
        a["shipped_late"] += _enviados_atrasados(x, ev)
        a["pending_overdue"] += _pend_vencidos(x, ev)
        a["pending_on_time"] += _pend_no_prazo(x, ev)
        a["is_mature"] = a["is_mature"] and _madura(x, ev)
        a["deadline_date"] = max(a["deadline_date"], _deadline(x, ev))

    fluxo = []
    for d in sorted(por_pag):
        a = por_pag[d]
        atras = a["shipped_late"] + a["pending_overdue"]
        fluxo.append({**a, "late": atras, "rate": _taxa(atras, a["base"])})

    # ----- marcas ----------------------------------------------------------
    por_marca: dict[str, dict[str, Any]] = {}
    for x in na_janela:
        if not _madura(x, ev):
            continue
        b = str(x["brand"])
        a = por_marca.setdefault(b, {
            "brand": b, "shop_name": x["shop_name"], "base": 0, "late": 0,
        })
        a["base"] += _base(x, ev)
        a["late"] += _atrasados(x, ev)
    marcas = sorted(
        (
            {**a, "rate": _taxa(a["late"], a["base"]),
             "above_target": (_taxa(a["late"], a["base"]) or 0.0) > META_TIKTOK_LDR}
            for a in por_marca.values()
            if a["base"] >= MIN_PEDIDOS_RANKING
        ),
        key=lambda a: (-(a["rate"] or 0.0), -a["base"], a["brand"]),
    )

    snapshot = linhas[0]
    return {
        "availability": "available",
        "unavailable_reason": None,
        "channel": CANAL,
        "event": ev,
        # A tela PRECISA exibir isto: o prazo nao e' o do TikTok.
        "deadline_is_reconstructed": True,
        "sla_business_days": SLA_DIAS_UTEIS,
        "targets": {
            "tiktok_ldr": META_TIKTOK_LDR,
            "internal_critical": LIMIAR_CRITICO_INTERNO,
        },
        "snapshot": {
            "refresh_batch_id": str(snapshot["refresh_batch_id"]),
            "effective_at": snapshot["effective_at"],
            "source_watermark_at": snapshot["source_watermark_at"],
            "today_brt": hoje,
        },
        "ldr": ldr,
        "payment_flow": fluxo,
        "brands": marcas,
        "alerts": _alertas(
            ldr=ldr, marcas=marcas, evento=ev,
            effective_at=snapshot["effective_at"], agora=agora,
        ),
    }


# ---------------------------------------------------------------------------
# Alertas — dentro da Torre, sem canal externo
# ---------------------------------------------------------------------------
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


def _alertas(*, ldr, marcas, evento, effective_at, agora):
    """Os alertas usam a LDR de VENCIMENTOS, nunca a media das taxas diarias.

    Cada alerta carrega `severity` e um texto ja resolvido: a tela nao deve
    reimplementar o limiar, senao o alerta e o destaque do grafico discordam.
    """
    out: list[dict] = []

    # 1. Conformidade com a plataforma. Este e' o alerta que importa.
    if ldr["above_target"]:
        out.append({
            "severity": "critical",
            "code": "fora_da_meta_tiktok",
            # "Acima da meta — medição interna", nunca "fora da meta". Quem
            # declara penalização é o TikTok, com o próprio relógio e o próprio
            # denominador; aqui o prazo é reconstruído.
            "message": (
                f"Acima da meta — medição interna: LDR de {_pct(ldr['rate'], 2)} "
                f"nos vencimentos da janela, contra a referência de "
                f"{_pct(META_TIKTOK_LDR, 0)} do TikTok. Base: {ldr['base']} pedidos "
                f"com envio vencendo no período. O prazo é reconstruído da "
                f"política de dias úteis e ainda não é a medição oficial de "
                f"penalização da plataforma."
            ),
        })
    elif ldr["rate"] is not None:
        out.append({
            "severity": "info",
            "code": "dentro_da_meta_tiktok",
            "message": (
                f"Dentro da meta — medição interna: LDR de {_pct(ldr['rate'], 2)}, "
                f"abaixo da referência de {_pct(META_TIKTOK_LDR, 0)} do TikTok. "
                f"Prazo reconstruído; não é a medição oficial da plataforma."
            ),
        })

    # 2. Severidade INTERNA — nome e limiar diferentes do de cima.
    criticos = [x for x in ldr["daily"] if x["is_critical"]]
    if criticos:
        pior = max(criticos, key=lambda x: x["rate"] or 0.0)
        out.append({
            "severity": "critical",
            "code": "dias_criticos_internos",
            "message": (
                f"{len(criticos)} vencimento(s) acima do limiar interno de "
                f"{_pct(LIMIAR_CRITICO_INTERNO, 0)}. Pior: "
                f"{_data(pior['due_date'])} com {_pct(pior['rate'])}."
            ),
        })

    if ldr["pending_overdue"] > 0:
        out.append({
            "severity": "critical",
            "code": "pendentes_vencidos",
            "message": (
                f"{ldr['pending_overdue']} pedido(s) passaram do prazo e ainda não "
                f"foram registrados."
            ),
        })
    if ldr["pending_at_risk"] > 0:
        out.append({
            "severity": "warning",
            "code": "em_risco",
            "message": (
                f"{ldr['pending_at_risk']} pedido(s) vencem hoje ou amanhã e ainda "
                f"podem ser tratados."
            ),
        })
    piores = [m for m in marcas if m["above_target"]]
    if piores:
        out.append({
            "severity": "warning",
            "code": "marcas_concentram",
            "message": (
                "Acima da meta (medição interna) em: "
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
