"""Rotina do dia da Expedicao no bot "Torre" (A5 e A6 em docs/torre_bot_alertas.md).

  A5  9h    "Vencem hoje": por canal, quantos pedidos tem prazo HOJE e em que
            etapa estao, mais o que vence amanha, o que ja' venceu e o travado.
  A6  15h30 "Corte da coleta": o que vence hoje e ainda nao saiu; em destaque o
            que nem esta' pronto (a preparar, faturar, imprimir, separar).

FONTES (as mesmas da aba Expedicao)
-----------------------------------
  Shopee e Mercado Livre  `marts.expedicao_fila_atual` do lote SERVIDO
      (`expedicao_service.FILA`), com o dia do prazo em Brasilia
      (`DIA_DO_PRAZO_SQL`) e a `etapa` publicada. O ML so' entra quando a tela
      mostra o prazo dele (`expedicao_service.prazo_publicado`).
  TikTok Shop  `marts.expedicao_tiktok_dispatch_daily`: pendentes de COLETA com
      vencimento hoje (prazo reconstruido, 2 dias uteis). Nao ha' etapa.

Fotografia mais velha que `MAX_IDADE_H` nao entra: a mensagem diria um numero
que a fila ja' nao tem.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import text

from app.services import expedicao_service as exp
from app.services import expedicao_tiktok_service as tiktok
from app.services import slack_blocks as sb
from app.services.gold_service import TZ_OPERACIONAL
from app.services.slack_dm import Item
from app.services.torre_alertas import ROTULO_CANAL, inteiro, rotulo_marca

logger = logging.getLogger(__name__)

MANHA = "manha"
CORTE = "corte"
MOMENTOS = (MANHA, CORTE)

MAX_IDADE_H = 3

#: Etapas em que o pedido ainda NAO esta' pronto para a transportadora.
NAO_PRONTO = ("a_preparar", "a_faturar", "a_imprimir", "em_separacao")
AGUARDANDO_COLETA = "aguardando_coleta"
#: Etapas que ja' sairam da mao do time (nao contam como pendencia de hoje).
JA_SAIU = ("entregue_transportadora",)

ROTULO_ETAPA = dict(exp.ETAPAS_FILA)
ORDEM_ETAPA = [e for e, _ in exp.ETAPAS_FILA]

CANAIS_FILA = ("shopee", "mercadolivre")


@dataclass
class Canal:
    canal: str
    effective_at: datetime
    #: marca -> etapa -> pedidos com prazo HOJE ainda nao vencidos.
    hoje: dict[str, dict[str, int]] = field(default_factory=dict)
    amanha: int = 0
    dia_amanha: Optional[date] = None
    vencidos: int = 0
    travados: int = 0
    #: TikTok nao publica etapa: tudo entra como "aguardando coleta".
    com_etapa: bool = True

    def total_hoje(self, etapas: Optional[tuple[str, ...]] = None) -> int:
        return sum(n for por in self.hoje.values() for e, n in por.items()
                   if etapas is None or e in etapas)

    def por_etapa(self) -> dict[str, int]:
        soma: dict[str, int] = {}
        for por in self.hoje.values():
            for e, n in por.items():
                soma[e] = soma.get(e, 0) + n
        return soma

    def por_marca(self, etapas: Optional[tuple[str, ...]] = None) -> list[tuple[str, int]]:
        pares = [(m, sum(n for e, n in por.items() if etapas is None or e in etapas))
                 for m, por in self.hoje.items()]
        return sorted([p for p in pares if p[1] > 0], key=lambda p: p[1], reverse=True)


@dataclass
class LeituraDia:
    hoje: date
    canais: list[Canal] = field(default_factory=list)
    indisponiveis: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
_SQL_LOTE = f"SELECT DISTINCT refresh_batch_id, effective_at FROM {exp.FILA} WHERE channel = :canal"
_SQL_RESUMO = (f"SELECT backlog_count, deadline_unavailable_count FROM {exp.RUN} "
               "WHERE channel = :canal AND refresh_batch_id = :lote")
_SQL_FILA = (
    f"SELECT brand, etapa, deadline_status, {exp.DIA_DO_PRAZO_SQL} AS dia, "
    "COALESCE(is_stalled, false) AS travado, COUNT(*) AS n "
    f"FROM {exp.FILA} WHERE channel = :canal AND refresh_batch_id = :lote "
    "GROUP BY 1, 2, 3, 4, 5"
)


def ler_fila(db, canal: str, hoje: date) -> Optional[Canal]:
    """Um canal da fila no lote servido. `None` sem lote unico ou sem prazo."""
    lotes = db.execute(text(_SQL_LOTE), {"canal": canal}).mappings().all()
    if len(lotes) != 1:
        return None
    lote = lotes[0]
    resumos = [dict(r) for r in db.execute(
        text(_SQL_RESUMO), {"canal": canal, "lote": lote["refresh_batch_id"]}).mappings()]
    if not exp.prazo_publicado(exp.CANAIS[canal], resumos):
        return None
    linhas = db.execute(text(_SQL_FILA),
                        {"canal": canal, "lote": lote["refresh_batch_id"]}).mappings().all()
    c = Canal(canal, lote["effective_at"])
    futuros: dict[date, int] = {}
    for r in linhas:
        n, etapa, dia = int(r["n"]), r["etapa"] or "nao_publicada", r["dia"]
        if etapa in JA_SAIU:
            continue
        if r["travado"]:
            c.travados += n
        if r["deadline_status"] == "overdue":
            c.vencidos += n
        elif dia == hoje:
            por = c.hoje.setdefault(str(r["brand"] or "sem marca").lower(), {})
            por[etapa] = por.get(etapa, 0) + n
        elif dia is not None and dia > hoje:
            futuros[dia] = futuros.get(dia, 0) + n
    if futuros:
        c.dia_amanha = min(futuros)
        c.amanha = futuros[c.dia_amanha]
    return c


def ler_tiktok(db, hoje: date) -> Optional[Canal]:
    """Coleta que vence hoje e ainda nao saiu, por marca (sem etapa)."""
    if not tiktok._tabela_existe(db):
        return None
    desde = hoje - timedelta(days=tiktok.FOLGA_PAGAMENTO_DIAS + 7)
    linhas = db.execute(text(tiktok.SERIE_SQL), {"canal": tiktok.CANAL, "desde": desde,
                                                 "ate": hoje}).mappings().all()
    if not linhas:
        return None
    c = Canal("tiktokshop", max(x["effective_at"] for x in linhas), com_etapa=False)
    futuros: dict[date, int] = {}
    for x in linhas:
        prazo, no_prazo = tiktok._deadline(x, "coleta"), tiktok._pend_no_prazo(x, "coleta")
        if prazo == hoje and no_prazo:
            por = c.hoje.setdefault(str(x["brand"]).lower(), {})
            por[AGUARDANDO_COLETA] = por.get(AGUARDANDO_COLETA, 0) + no_prazo
        elif prazo > hoje and no_prazo:
            futuros[prazo] = futuros.get(prazo, 0) + no_prazo
        c.vencidos += tiktok._pend_vencidos(x, "coleta") if tiktok._madura(x, "coleta") else 0
    if futuros:
        c.dia_amanha = min(futuros)
        c.amanha = futuros[c.dia_amanha]
    return c


def _velha(c: Canal, agora: datetime) -> bool:
    return agora - c.effective_at > timedelta(hours=MAX_IDADE_H)


def ler_dia(db, agora: datetime) -> LeituraDia:
    """Os tres canais, cada um isolado: um que cai nao derruba os outros."""
    hoje = agora.astimezone(TZ_OPERACIONAL).date()
    leitura = LeituraDia(hoje)
    fontes = [(c, lambda c=c: ler_fila(db, c, hoje)) for c in CANAIS_FILA]
    fontes.append(("tiktokshop", lambda: ler_tiktok(db, hoje)))
    for canal, ler in fontes:
        try:
            db.rollback()
            c = ler()
        except Exception as exc:  # noqa: BLE001 — motivo fixo; o texto nunca sai
            logger.warning("expedicao_dia: %s falhou (%s)", canal, type(exc).__name__)
            c = None
        finally:
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        if c is None or _velha(c, agora):
            leitura.indisponiveis.append(ROTULO_CANAL[canal])
        else:
            leitura.canais.append(c)
    return leitura


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------
DIAS_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def _dia(d: date) -> str:
    return f"{DIAS_SEMANA[d.weekday()]}, {d:%d/%m}"


def _marcas(pares: list[tuple[str, int]], limite: int = 6) -> str:
    txt = "  ·  ".join(f"*{rotulo_marca(m)}* {inteiro(n)}" for m, n in pares[:limite])
    resto = len(pares) - limite
    return txt + (f"  ·  +{resto}" if resto > 0 else "")


def _hora(c: Canal) -> str:
    return f"{c.effective_at.astimezone(TZ_OPERACIONAL):%H:%M}"


def _botao(c: Canal, pares: list[tuple[str, int]]) -> dict:
    marca = pares[0][0] if len(pares) == 1 else None
    return sb.botao(f"Ver fila {ROTULO_CANAL[c.canal]}", sb.url_expedicao(c.canal, marca, detalhe=True))


def _secao_manha(c: Canal) -> list[dict]:
    total = c.total_hoje()
    linhas = [f"{sb.EMOJI_CANAL[c.canal]} *{ROTULO_CANAL[c.canal]}*  ·  *{inteiro(total)}* "
              f"pedido{'s' if total != 1 else ''} vencem hoje"]
    if c.com_etapa:
        etapas = c.por_etapa()
        nao_pronto = sum(etapas.get(e, 0) for e in NAO_PRONTO)
        if nao_pronto:
            detalhe = ", ".join(f"{ROTULO_ETAPA[e].lower()} {inteiro(etapas[e])}"
                                for e in ORDEM_ETAPA if e in NAO_PRONTO and etapas.get(e))
            linhas.append(f":red_circle: *{inteiro(nao_pronto)}* ainda não estão prontos ({detalhe})")
        if etapas.get(AGUARDANDO_COLETA):
            linhas.append(f":truck: *{inteiro(etapas[AGUARDANDO_COLETA])}* aguardando coleta")
        outras = sum(n for e, n in etapas.items() if e not in NAO_PRONTO + (AGUARDANDO_COLETA,))
        if outras:
            linhas.append(f":grey_question: {inteiro(outras)} em outra etapa")
    else:
        linhas.append(":truck: com a coleta vencendo hoje, ainda não coletados")
    pares = c.por_marca()
    if pares:
        linhas.append(_marcas(pares))
    rodape = [f"fotografia das {_hora(c)}"]
    if c.amanha:
        rodape.insert(0, f"vencem {_dia(c.dia_amanha)}: {inteiro(c.amanha)}")
    if c.vencidos:
        rodape.insert(0, f"já vencidos: {inteiro(c.vencidos)}")
    if c.travados:
        rodape.insert(0, f"travados: {inteiro(c.travados)}")
    return [sb.secao("\n".join(linhas), _botao(c, pares)), sb.nota("  ·  ".join(rodape))]


def _secao_corte(c: Canal) -> list[dict]:
    coleta = c.total_hoje((AGUARDANDO_COLETA,))
    nao_pronto = c.total_hoje(NAO_PRONTO) if c.com_etapa else 0
    linhas = [f"{sb.EMOJI_CANAL[c.canal]} *{ROTULO_CANAL[c.canal]}*"]
    if nao_pronto:
        pares_np = c.por_marca(NAO_PRONTO)
        linhas.append(f":red_circle: *{inteiro(nao_pronto)}* vencem hoje e *ainda não estão prontos*: "
                      + _marcas(pares_np))
    if coleta:
        linhas.append(f":truck: *{inteiro(coleta)}* vencem hoje e aguardam coleta: "
                      + _marcas(c.por_marca((AGUARDANDO_COLETA,))))
    pares = c.por_marca()
    return [sb.secao("\n".join(linhas), _botao(c, pares)),
            sb.nota(f"fotografia das {_hora(c)}"
                    + (f"  ·  vencem {_dia(c.dia_amanha)}: {inteiro(c.amanha)}" if c.amanha else ""))]


def montar(leitura: LeituraDia, momento: str) -> Optional[sb.Mensagem]:
    """`None` quando nada vence hoje (nao ha' o que avisar)."""
    canais = [c for c in leitura.canais if c.total_hoje() > 0]
    if momento == CORTE:
        canais = [c for c in canais if c.total_hoje((AGUARDANDO_COLETA,) + NAO_PRONTO) > 0]
    if not canais:
        return None
    total = sum(c.total_hoje() for c in canais)
    dia = _dia(leitura.hoje)
    if momento == MANHA:
        cab = f":package: Vencem hoje · {dia}"
        resumo = f"{inteiro(total)} pedidos vencem hoje"
        nao_pronto = sum(c.total_hoje(NAO_PRONTO) for c in canais if c.com_etapa)
        guia = ("Comece pelo que *ainda não está pronto*; depois confirme a coleta do resto."
                if nao_pronto else "Tudo pronto: confirme a coleta antes do fim do dia.")
        corpo = [b for c in canais for b in _secao_manha(c)]
    else:
        cab = f":alarm_clock: Corte da coleta · {dia}"
        resumo = f"{inteiro(total)} pedidos vencem hoje e ainda não saíram"
        guia = "Faltam poucas horas: cobre a coleta com a transportadora e priorize o que não está pronto."
        corpo = [b for c in canais for b in _secao_corte(c)]
    blocos = [sb.titulo(cab), sb.nota(f"*{resumo}*  ·  {guia}"), sb.divisor(), *corpo]
    if leitura.indisponiveis:
        blocos.append(sb.nota("Sem fotografia recente de: " + ", ".join(leitura.indisponiveis) + "."))
    blocos.append(sb.atalhos(sb.botao("Abrir a Expedição", sb.url_expedicao(), "primary")))
    return sb.Mensagem(texto=f"{cab.split(' ', 1)[0]} Torre: {resumo}", blocos=sb.limitar(blocos))


def chave(leitura: LeituraDia, momento: str) -> str:
    return f"expedicao-dia:{momento}:{leitura.hoje.isoformat()}"


def item(leitura: LeituraDia, momento: str) -> Optional[tuple[Item, sb.Mensagem]]:
    m = montar(leitura, momento)
    return (Item(chave(leitura, momento), m.texto), m) if m else None
