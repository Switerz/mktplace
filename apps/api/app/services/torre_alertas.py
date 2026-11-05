"""Alertas criticos do bot "Torre": LDR acima do teto, performance da Kokeshi
e vencidos parados no TikTok.

Mapa completo em `docs/torre_bot_alertas.md` (A2, A3 e A4). Chamado pela rota
`POST /api/v1/alertas/criticos` (cron de 15 min) e reaproveitado pelo report
diario (`torre_report.py`), que tambem mostra os itens de "atencao".

NADA DE SQL NOVO
----------------
Cada numero vem do mesmo servico que a tela usa:

  LDR Shopee   expedicao_shopee_ldr_service.obter_ldr (reserva: shopee_ldr_dm_service)
  LDR ML       ml_ldr_service.obter_ldr_ml
  LDR TikTok   expedicao_tiktok_service.obter_serie (coleta)
  Kokeshi      performance_oficial_service.montar_performance_oficial (ML)
  Vencidos TT  marts.expedicao_tiktok_dispatch_daily, com a MESMA consulta e as
               MESMAS colunas da /expedicao/tiktok (A4, ver `ler_vencidos_tiktok`)

Meta e teto sao os da faixa "LDR · ultimos 7 dias"
(`apps/web/src/lib/expedicao-ldr-topo.ts`, confirmados em 04/11/2026). Uma
fonte fora do ar tira so' aquele canal; ausencia nunca vira 0%.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import text

from app.services import expedicao_service
from app.services import expedicao_shopee_ldr_service as shopee_ldr
from app.services import expedicao_tiktok_service as tiktok
from app.services import ml_ldr_service as ml_ldr
from app.services import performance_oficial_service as perf_oficial
from app.services import shopee_ldr_dm_service as shopee_dm
from app.services.alerta_vencidos import HORAS_LEMBRETE, LIMIAR_VENCIDOS
from app.services.gold_service import BRAND_LABELS, TZ_OPERACIONAL
from app.services.slack_dm import Item

logger = logging.getLogger(__name__)

URL_DA_EXPEDICAO = "https://mktplace-gobeaute.vercel.app/expedicao"

DIAS_LDR = 7

#: (meta, teto) em fracao, por canal. Mesmo valor da faixa do topo.
META_TETO: dict[str, tuple[float, float]] = {
    "shopee": (0.02, 0.05),
    "mercadolivre": (0.03, 0.06),
    "tiktokshop": (0.02, 0.04),
}

ROTULO_CANAL = {"shopee": "Shopee", "mercadolivre": "Mercado Livre", "tiktokshop": "TikTok Shop"}

#: Contas por canal (mesma cobertura da faixa). Fora daqui nao e' avaliado.
CONTAS_POR_CANAL: dict[str, tuple[str, ...]] = {
    "shopee": ("apice", "barbours", "lescent", "rituaria"),
    "mercadolivre": ("barbours", "kokeshi", "lescent", "rituaria"),
    "tiktokshop": ("apice", "barbours", "kokeshi", "lescent", "rituaria"),
}

#: Abaixo disto a taxa nao vira alerta: 1 atraso em 10 pedidos e' 10% e nao diz nada.
BASE_MINIMA = 30

#: Marcas com a performance oficial observada (A3). Para incluir outra, acrescente aqui.
MARCAS_SAUDE: tuple[str, ...] = ("kokeshi",)

#: Metrica oficial a partir desta fracao do limite vira "atencao".
LIMIAR_PERTO = 0.8

CRITICO = "critico"
ATENCAO = "atencao"
DENTRO = "dentro"


def rotulo_marca(marca: str) -> str:
    return BRAND_LABELS.get(marca, marca.upper())


def pct(fracao: float) -> str:
    return f"{fracao * 100:.1f}".replace(".", ",") + "%"


def inteiro(n: int | float) -> str:
    return f"{int(round(n)):,}".replace(",", ".")


# ---------------------------------------------------------------------------
# LDR
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CelulaLdr:
    canal: str
    marca: str
    taxa: float
    atrasados: int
    base: int
    faixa: str
    #: "janela parcial: 1 de 7 dias" quando a serie ainda nao tem a janela toda.
    parcial: Optional[str] = None


@dataclass
class LeituraLdr:
    celulas: list[CelulaLdr] = field(default_factory=list)
    #: Canais que nao responderam (rotulo legivel).
    indisponiveis: list[str] = field(default_factory=list)


def classificar(taxa: float, canal: str) -> str:
    """Limite exato conta como dentro, como na tela."""
    meta, teto = META_TETO[canal]
    if taxa <= meta:
        return DENTRO
    if taxa <= teto:
        return ATENCAO
    return CRITICO


def celulas_do_canal(canal: str, resposta: dict) -> Optional[list[CelulaLdr]]:
    """`brands[]` de uma rota de LDR -> celulas. `None` = canal indisponivel."""
    if not isinstance(resposta, dict) or resposta.get("availability") != "available":
        return None
    marcas = resposta.get("brands")
    if not isinstance(marcas, list):
        return None
    maduros, pedidos = resposta.get("mature_days"), resposta.get("requested_days")
    parcial = (f"janela parcial: {maduros} de {pedidos} dias"
               if isinstance(maduros, int) and isinstance(pedidos, int) and maduros < pedidos else None)
    out = []
    for m in marcas:
        marca, base, late, rate = m.get("brand"), m.get("base"), m.get("late"), m.get("rate")
        if marca not in CONTAS_POR_CANAL[canal] or rate is None or not base or base <= 0:
            continue
        out.append(CelulaLdr(canal, marca, float(rate), int(late or 0), int(base),
                             classificar(float(rate), canal), parcial))
    return out


def _shopee(db, agora: datetime) -> dict:
    r = shopee_ldr.obter_ldr(db, dias=DIAS_LDR, agora=agora)
    # Mesma regra da tela: sem a publicacao oficial, a reserva do Data Mart.
    if r.get("availability") != "available" or not r.get("brands"):
        r = shopee_dm.obter_ldr_shopee_dm(dias=DIAS_LDR, agora=agora)
    return r


def _tiktok(db, agora: datetime) -> dict:
    return tiktok.obter_serie(db, evento="coleta", dias=DIAS_LDR, agora=agora)


def _ml(db, agora: datetime) -> dict:
    return ml_ldr.obter_ldr_ml(dias=DIAS_LDR, agora=agora)


FONTES_LDR: dict[str, Callable[[object, datetime], dict]] = {
    "shopee": _shopee,
    "mercadolivre": _ml,
    "tiktokshop": _tiktok,
}


def _desfazer(db) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass


def ler_ldr(db, agora: datetime) -> LeituraLdr:
    """Os tres canais, isolados: um que cai nao derruba os outros."""
    leitura = LeituraLdr()
    for canal, fonte in FONTES_LDR.items():
        try:
            celulas = celulas_do_canal(canal, fonte(db, agora))
        except Exception as exc:  # noqa: BLE001 — motivo fixo; o texto nunca sai
            logger.warning("torre_alertas: LDR %s falhou (%s)", canal, type(exc).__name__)
            celulas = None
            _desfazer(db)
        if celulas is None:
            leitura.indisponiveis.append(ROTULO_CANAL[canal])
        else:
            leitura.celulas.extend(celulas)
    ordem = list(FONTES_LDR)
    leitura.celulas.sort(key=lambda c: (ordem.index(c.canal), -c.taxa))
    return leitura


def texto_celula(c: CelulaLdr) -> str:
    meta, teto = META_TETO[c.canal]
    extra = f"; {c.parcial}" if c.parcial else ""
    return (f"{rotulo_marca(c.marca)} no {ROTULO_CANAL[c.canal]}: LDR {pct(c.taxa)} "
            f"({inteiro(c.atrasados)} de {inteiro(c.base)} pedidos; meta {pct(meta)}, teto {pct(teto)}{extra})")


def itens_ldr_criticos(leitura: LeituraLdr, dia: date) -> list[Item]:
    return [
        Item(f"ldr:{c.canal}:{c.marca}:{dia.isoformat()}", texto_celula(c))
        for c in leitura.celulas
        if c.faixa == CRITICO and c.base >= BASE_MINIMA
    ]


# ---------------------------------------------------------------------------
# Performance oficial (Kokeshi)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SinalSaude:
    marca: str
    canal: str
    nivel: str  # CRITICO | ATENCAO
    codigo: str
    texto: str
    dia: Optional[date]

    @property
    def chave(self) -> str:
        dia = self.dia.isoformat() if self.dia else "sem-dia"
        return f"saude:{self.canal}:{self.marca}:{self.codigo}:{dia}"


@dataclass
class LeituraSaude:
    sinais: list[SinalSaude] = field(default_factory=list)
    #: Marcas lidas com tudo em ordem (para "avancos" no report).
    em_ordem: list[str] = field(default_factory=list)
    indisponivel: bool = False


def _num_br(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(",.").replace(".", ",")


def sinais_da_conta(conta, canal: str = "mercadolivre") -> list[SinalSaude]:
    """Regras da A3 sobre uma `ContaOficial` (schema da secao Performance)."""
    marca, dia, nome = conta.marca, conta.dia, rotulo_marca(conta.marca)
    sinais: list[SinalSaude] = []
    if conta.tom == "critico" or conta.tom == "atencao":
        termometro = next((d.valor for d in conta.destaques if d.rotulo == "Termômetro"), None)
        sinais.append(SinalSaude(
            marca, canal, CRITICO, "termometro",
            f"{nome} no {ROTULO_CANAL[canal]}: termômetro da reputação em {termometro or conta.nivel} "
            f"(fora do verde).", dia))
    if canal == "mercadolivre" and conta.nivel and not conta.nivel.startswith("MercadoLíder"):
        sinais.append(SinalSaude(
            marca, canal, CRITICO, "lider",
            f"{nome} no {ROTULO_CANAL[canal]}: sem MercadoLíder (nível atual: {conta.nivel}).", dia))
    for m in conta.metricas:
        if m.valor is None or m.meta is None or m.unidade != "pct" or m.comparador not in ("<=", "<"):
            continue
        valor, limite = _num_br(m.valor), _num_br(m.meta)
        if m.situacao == "fora":
            sinais.append(SinalSaude(
                marca, canal, CRITICO, m.codigo,
                f"{nome} no {ROTULO_CANAL[canal]}: {m.rotulo} em {valor}%, acima do limite "
                f"de {limite}% para MercadoLíder.", dia))
        elif m.meta > 0 and m.valor >= LIMIAR_PERTO * m.meta:
            sinais.append(SinalSaude(
                marca, canal, ATENCAO, m.codigo,
                f"{nome} no {ROTULO_CANAL[canal]}: {m.rotulo} em {valor}%, perto do limite de "
                f"{limite}%.", dia))
    return sinais


def ler_saude(agora: datetime,
              montar: Callable[..., object] = perf_oficial.montar_performance_oficial) -> LeituraSaude:
    leitura = LeituraSaude()
    try:
        resp = montar(canal="mercadolivre", brands=list(MARCAS_SAUDE), agora=agora)
    except Exception as exc:  # noqa: BLE001
        logger.warning("torre_alertas: performance oficial falhou (%s)", type(exc).__name__)
        leitura.indisponivel = True
        return leitura
    if resp.estado != "dado":
        leitura.indisponivel = True
        return leitura
    for conta in resp.contas:
        sinais = sinais_da_conta(conta)
        leitura.sinais.extend(sinais)
        if not sinais:
            leitura.em_ordem.append(conta.marca)
    return leitura


def itens_saude_criticos(leitura: LeituraSaude) -> list[Item]:
    return [Item(s.chave, s.texto) for s in leitura.sinais if s.nivel == CRITICO]


# ---------------------------------------------------------------------------
# A4 - vencidos parados no TikTok (coleta)
# ---------------------------------------------------------------------------
# O A1 cobre a fila da Shopee. O TikTok nao tem fila fotografada: a mesma
# pergunta ("quanto do que esta' parado ja' passou do prazo?") sai da serie
# diaria, somando os pendentes de coleta de todas as datas de pagamento que a
# /expedicao/tiktok le. O Mercado Livre fica de fora enquanto a TRAVA de
# `expedicao_service.prazo_publicado` estiver ligada: hoje ele marcaria como
# vencido o envio ja' coletado.
#
# Limite: o MESMO 3% do A1. Repeticao: uma vez por turno de Brasilia (antes das
# 9h, 9h, 13h, 17h), como os lembretes do A1, enquanto continuar acima.

@dataclass(frozen=True)
class VencidosMarca:
    marca: str
    vencidos: int
    fila: int

    @property
    def fracao(self) -> Optional[float]:
        return self.vencidos / self.fila if self.fila > 0 else None


@dataclass(frozen=True)
class LeituraVencidos:
    canal: str
    effective_at: datetime
    marcas: list[VencidosMarca]
    velha: bool = False

    @property
    def vencidos(self) -> int:
        return sum(m.vencidos for m in self.marcas)

    @property
    def fila(self) -> int:
        return sum(m.fila for m in self.marcas)

    @property
    def fracao(self) -> Optional[float]:
        return self.vencidos / self.fila if self.fila > 0 else None


def ler_vencidos_tiktok(db, agora: datetime) -> Optional[LeituraVencidos]:
    """Pendentes de coleta por marca. `None` sem tabela ou sem linhas."""
    if not tiktok._tabela_existe(db):
        return None
    hoje = tiktok._hoje_brt(agora)
    desde = hoje - timedelta(days=DIAS_LDR + tiktok.FOLGA_PAGAMENTO_DIAS)
    linhas = db.execute(text(tiktok.SERIE_SQL),
                        {"canal": tiktok.CANAL, "desde": desde, "ate": hoje}).mappings().all()
    if not linhas:
        return None
    # Mesmos recortes da tela: vencidos = pendentes vencidos dos vencimentos
    # MADUROS da janela ("N pedidos passaram do prazo e ainda nao foram
    # registrados"); no prazo = pendentes das coortes ainda abertas.
    desde_venc = hoje - timedelta(days=DIAS_LDR)
    soma: dict[str, list[int]] = {}
    for x in linhas:
        a = soma.setdefault(str(x["brand"]).lower(), [0, 0])
        if not tiktok._madura(x, "coleta"):
            a[1] += tiktok._pend_no_prazo(x, "coleta")
        elif desde_venc <= tiktok._deadline(x, "coleta") <= hoje:
            venc = tiktok._pend_vencidos(x, "coleta")
            a[0] += venc
            a[1] += venc
    publicado = max(x["effective_at"] for x in linhas)
    marcas = [VencidosMarca(m, v, f) for m, (v, f) in sorted(soma.items())
              if m in CONTAS_POR_CANAL["tiktokshop"]]
    return LeituraVencidos("tiktokshop", publicado, marcas,
                           expedicao_service._tiktok_fotografia_velha(publicado, agora))


def turno(agora: datetime) -> str:
    """Hora de Brasilia -> turno do lembrete ("00", "09", "13" ou "17")."""
    h = agora.astimezone(TZ_OPERACIONAL).hour
    return f"{max([0] + [x for x in HORAS_LEMBRETE if h >= x]):02d}"


def itens_vencidos(leitura: Optional[LeituraVencidos], agora: datetime) -> list[Item]:
    """Um item por turno enquanto os vencidos parados passam do limite do A1."""
    if leitura is None or leitura.velha or leitura.fila < BASE_MINIMA:
        return []
    f = leitura.fracao
    if f is None or f <= LIMIAR_VENCIDOS:
        return []
    canal = ROTULO_CANAL[leitura.canal]
    texto = (f"{canal}: {inteiro(leitura.vencidos)} de {inteiro(leitura.fila)} pedidos parados "
             f"passaram do prazo de coleta ({pct(f)}; limite {LIMIAR_VENCIDOS:.0%})")
    acima = sorted((m for m in leitura.marcas
                    if m.fila >= BASE_MINIMA and (m.fracao or 0) > LIMIAR_VENCIDOS),
                   key=lambda m: m.vencidos, reverse=True)
    if acima:
        texto += ". " + " · ".join(
            f"{rotulo_marca(m.marca)} {inteiro(m.vencidos)} ({pct(m.fracao or 0)})" for m in acima)
    texto += ". _Prazo reconstruído pela regra de 2 dias úteis._"
    dia = agora.astimezone(TZ_OPERACIONAL).date().isoformat()
    return [Item(f"vencidos:{leitura.canal}:{dia}:{turno(agora)}", texto)]


def _ler_vencidos(db, agora: datetime) -> tuple[Optional[LeituraVencidos], bool]:
    """(leitura, indisponivel). Falha isolada, como o LDR."""
    try:
        return ler_vencidos_tiktok(db, agora), False
    except Exception as exc:  # noqa: BLE001 — motivo fixo; o texto nunca sai
        logger.warning("torre_alertas: vencidos tiktok falhou (%s)", type(exc).__name__)
        _desfazer(db)
        return None, True


# ---------------------------------------------------------------------------
# Avaliacao e mensagem (o que a rota chama)
# ---------------------------------------------------------------------------
@dataclass
class AvaliacaoCriticos:
    itens: list[Item]
    canais_indisponiveis: list[str]
    saude_indisponivel: bool


def avaliar_criticos(db, agora: datetime) -> AvaliacaoCriticos:
    """Le e decide. Nao envia: o envio (sem repetir) e' da rota."""
    hoje = agora.astimezone(TZ_OPERACIONAL).date()
    ldr = ler_ldr(db, agora)
    saude = ler_saude(agora)
    vencidos, sem_vencidos = _ler_vencidos(db, agora)
    itens = (itens_ldr_criticos(ldr, hoje) + itens_vencidos(vencidos, agora)
             + itens_saude_criticos(saude))
    indisponiveis = ldr.indisponiveis + (["Vencidos TikTok Shop"] if sem_vencidos else [])
    return AvaliacaoCriticos(itens, indisponiveis, saude.indisponivel)


def montar_mensagem_criticos(itens: list[Item]) -> str:
    ldr = [i for i in itens if i.chave.startswith("ldr:")]
    vencidos = [i for i in itens if i.chave.startswith("vencidos:")]
    saude = [i for i in itens if not i.chave.startswith(("ldr:", "vencidos:"))]
    linhas = [":rotating_light: *Torre: alerta crítico*"]
    if ldr:
        linhas.append("*LDR acima do teto* (últimos 7 dias):")
        linhas += [f"• {i.texto}" for i in ldr]
    if vencidos:
        linhas.append("*Pedidos vencidos parados*:")
        linhas += [f"• {i.texto}" for i in vencidos]
    if saude:
        linhas.append("*Performance oficial*:")
        linhas += [f"• {i.texto}" for i in saude]
    linhas.append(f"<{URL_DA_EXPEDICAO}|Abrir a Expedição na Torre>")
    return "\n".join(linhas)
