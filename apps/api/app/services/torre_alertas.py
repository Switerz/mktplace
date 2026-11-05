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
  Performance  performance_oficial_service.montar_performance_oficial (ML, Shopee; TikTok sem fonte)
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
from app.services import slack_blocks as sb
from app.services.slack_dm import Item

logger = logging.getLogger(__name__)

URL_DA_EXPEDICAO = "https://mktplace-ten.vercel.app/expedicao"

DIAS_LDR = 7

#: (meta, teto) em fracao, por canal. Mesmo valor da faixa do topo.
META_TETO: dict[str, tuple[float, float]] = {
    "shopee": (0.02, 0.05),
    "mercadolivre": (0.03, 0.06),
    "tiktokshop": (0.02, 0.04),
}

ROTULO_CANAL = {"shopee": "Shopee", "mercadolivre": "Mercado Livre", "tiktokshop": "TikTok Shop"}
#: "na Shopee", "no Mercado Livre", "no TikTok Shop".
NO_CANAL = {"shopee": "na Shopee", "mercadolivre": "no Mercado Livre", "tiktokshop": "no TikTok Shop"}

#: Contas por canal (mesma cobertura da faixa). Fora daqui nao e' avaliado.
CONTAS_POR_CANAL: dict[str, tuple[str, ...]] = {
    "shopee": ("apice", "barbours", "lescent", "rituaria"),
    "mercadolivre": ("barbours", "kokeshi", "lescent", "rituaria"),
    "tiktokshop": ("apice", "barbours", "kokeshi", "lescent", "rituaria"),
}

#: Abaixo disto a taxa nao vira alerta: 1 atraso em 10 pedidos e' 10% e nao diz nada.
BASE_MINIMA = 30

#: Canais da performance oficial observados (A3), todas as marcas de cada um.
#: O TikTok entra para a mensagem dizer que ainda nao ha' nota oficial dele.
CANAIS_SAUDE: tuple[str, ...] = ("mercadolivre", "shopee", "tiktokshop")

#: Comparador da meta oficial -> palavra no texto ("maximo 8%", "minimo 60%").
LIMITE_POR_COMPARADOR = {"<=": "máximo", "<": "abaixo de", ">=": "mínimo", ">": "acima de", "=": "igual a"}

#: Unidade da metrica oficial -> sufixo no texto.
SUFIXO_UNIDADE = {"pct": "%", "segundos": " s", "horas": " h", "dias": " dias", "contagem": ""}

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
    return (f"{rotulo_marca(c.marca)} {NO_CANAL[c.canal]}: LDR {pct(c.taxa)} "
            f"({inteiro(c.atrasados)} de {inteiro(c.base)} pedidos; meta {pct(meta)}, teto {pct(teto)}{extra})")


def itens_ldr_criticos(leitura: LeituraLdr, dia: date) -> list[Item]:
    return [
        Item(f"ldr:{c.canal}:{c.marca}:{dia.isoformat()}", texto_celula(c), c)
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
    #: (marca, canal) lidas com tudo em ordem (para "avancos" no report).
    em_ordem: list[tuple[str, str]] = field(default_factory=list)
    #: Canais cuja fonte caiu agora (rotulo legivel).
    indisponiveis: list[str] = field(default_factory=list)
    #: Canais que ainda nao tem nota oficial (ex.: TikTok Shop).
    sem_fonte: list[str] = field(default_factory=list)

    @property
    def indisponivel(self) -> bool:
        return bool(self.indisponiveis)


def _num_br(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(",.").replace(".", ",")


def _valor(v: float, unidade: str) -> str:
    return f"{_num_br(v)}{SUFIXO_UNIDADE.get(unidade, '')}"


def _destaque(conta, prefixo: str) -> Optional[str]:
    return next((d.valor for d in conta.destaques if d.rotulo.startswith(prefixo)), None)


def sinais_da_conta(conta, canal: str = "mercadolivre") -> list[SinalSaude]:
    """Regras da A3 sobre uma `ContaOficial` (schema da secao Performance).

    Mercado Livre: termometro fora do verde e perda do MercadoLider sao criticos.
    Shopee: nota da loja "Ruim" ou punicao em andamento sao criticas; nota
    "Precisa melhorar" ou pontos de penalidade sao atencao.
    Os dois: metrica oficial fora da meta e' critica; metrica com limite
    maximo em 80% dele ou mais e' atencao.
    """
    marca, dia = conta.marca, conta.dia
    onde = f"{rotulo_marca(marca)} {NO_CANAL[canal]}"
    sinais: list[SinalSaude] = []

    def sinal(nivel: str, codigo: str, texto: str) -> None:
        sinais.append(SinalSaude(marca, canal, nivel, codigo, f"{onde}: {texto}", dia))

    if canal == "mercadolivre":
        if conta.tom in ("critico", "atencao"):
            termometro = _destaque(conta, "Termômetro")
            sinal(CRITICO, "termometro",
                  f"termômetro da reputação em {termometro or conta.nivel} (fora do verde).")
        if conta.nivel and not conta.nivel.startswith("MercadoLíder"):
            sinal(CRITICO, "lider", f"sem MercadoLíder (nível atual: {conta.nivel}).")
    elif canal == "shopee":
        if conta.tom == "critico":
            sinal(CRITICO, "nota", f"nota da loja em {conta.nivel} (Saúde da Conta).")
        elif conta.tom == "atencao":
            sinal(ATENCAO, "nota", f"nota da loja em {conta.nivel} (Saúde da Conta).")
        punicoes = _destaque(conta, "Punições")
        if punicoes not in (None, "0"):
            sinal(CRITICO, "punicoes", f"{punicoes} punição(ões) em andamento.")
        pontos = _destaque(conta, "Pontos de penalidade")
        if pontos not in (None, "0"):
            sinal(ATENCAO, "pontos", f"{pontos} ponto(s) de penalidade no trimestre.")

    for m in conta.metricas:
        if m.valor is None or m.meta is None or m.comparador is None:
            continue
        if canal == "mercadolivre" and m.unidade != "pct":
            continue
        valor, limite = _valor(m.valor, m.unidade), _valor(m.meta, m.unidade)
        if m.situacao == "fora":
            alvo = "para MercadoLíder" if canal == "mercadolivre" else "da Shopee"
            sinal(CRITICO, m.codigo,
                  f"{m.rotulo} em {valor}, fora da meta "
                  f"({LIMITE_POR_COMPARADOR.get(m.comparador, m.comparador)} {limite} {alvo}).")
        elif (m.comparador in ("<=", "<") and m.meta > 0
              and m.valor >= LIMIAR_PERTO * m.meta):
            sinal(ATENCAO, m.codigo, f"{m.rotulo} em {valor}, perto do limite de {limite}.")
    return sinais


def ler_saude(agora: datetime,
              montar: Callable[..., object] = perf_oficial.montar_performance_oficial) -> LeituraSaude:
    """Os tres canais da Performance oficial, cada um isolado."""
    leitura = LeituraSaude()
    for canal in CANAIS_SAUDE:
        rotulo = ROTULO_CANAL[canal]
        try:
            resp = montar(canal=canal, brands=None, agora=agora)
        except Exception as exc:  # noqa: BLE001
            logger.warning("torre_alertas: performance oficial %s falhou (%s)", canal,
                           type(exc).__name__)
            leitura.indisponiveis.append(rotulo)
            continue
        if resp.estado == "sem_fonte":
            leitura.sem_fonte.append(rotulo)
            continue
        if resp.estado != "dado":
            leitura.indisponiveis.append(rotulo)
            continue
        for conta in resp.contas:
            sinais = sinais_da_conta(conta, canal)
            leitura.sinais.extend(sinais)
            if not sinais:
                leitura.em_ordem.append((conta.marca, canal))
    return leitura


def itens_saude_criticos(leitura: LeituraSaude) -> list[Item]:
    return [Item(s.chave, s.texto, s) for s in leitura.sinais if s.nivel == CRITICO]


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
    texto += "."
    dia = agora.astimezone(TZ_OPERACIONAL).date().isoformat()
    return [Item(f"vencidos:{leitura.canal}:{dia}:{turno(agora)}", texto, leitura)]


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


def _sem_prefixo(texto: str) -> str:
    """"KOKESHI no Mercado Livre: termometro..." -> "termometro..." (o grupo ja' diz)."""
    _, _, resto = texto.partition(": ")
    return (resto or texto).rstrip(".")


def _bloco_ldr(canal: str, celulas: list[CelulaLdr]) -> dict:
    meta, teto = META_TETO[canal]
    linhas = [f"{sb.EMOJI_CANAL[canal]} *LDR acima do teto · {ROTULO_CANAL[canal]}*  "
              f"_(meta {pct(meta)} · teto {pct(teto)})_"]
    for c in sorted(celulas, key=lambda x: x.taxa, reverse=True):
        extra = f"  _{c.parcial}_" if c.parcial else ""
        linhas.append(f":red_circle: *{rotulo_marca(c.marca)}*  {pct(c.taxa)}  ·  "
                      f"{inteiro(c.atrasados)} de {inteiro(c.base)} pedidos{extra}")
    marca = celulas[0].marca if len(celulas) == 1 else None
    return sb.secao("\n".join(linhas), sb.botao(
        f"Ver {ROTULO_CANAL[canal]}", sb.url_expedicao(canal, marca)))


def _bloco_vencidos(v: "LeituraVencidos") -> list[dict]:
    canal = ROTULO_CANAL[v.canal]
    linhas = [f"{sb.EMOJI_CANAL[v.canal]} *Pedidos vencidos parados · {canal}*",
              f"*{inteiro(v.vencidos)}* de {inteiro(v.fila)} pedidos parados passaram do prazo de "
              f"coleta: *{pct(v.fracao or 0)}* _(limite {LIMIAR_VENCIDOS:.0%})_"]
    marcas = sorted((m for m in v.marcas if m.fila >= BASE_MINIMA and m.vencidos > 0),
                    key=lambda m: m.fracao or 0, reverse=True)
    for m in marcas:
        bola = ":red_circle:" if (m.fracao or 0) > LIMIAR_VENCIDOS else ":large_green_circle:"
        linhas.append(f"{bola} *{rotulo_marca(m.marca)}*  {pct(m.fracao or 0)}  ·  "
                      f"{inteiro(m.vencidos)} de {inteiro(m.fila)}")
    return [sb.secao("\n".join(linhas), sb.botao(f"Ver fila do {canal}",
                                                  sb.url_expedicao(v.canal, detalhe=True)))]


def _bloco_saude(marca: str, canal: str, sinais: list[str]) -> dict:
    linhas = [f"{sb.EMOJI_CANAL[canal]} *Performance oficial · {rotulo_marca(marca)} "
              f"{NO_CANAL[canal]}*"]
    linhas += [f":red_circle: {t}" for t in sinais]
    return sb.secao("\n".join(linhas), sb.botao("Ver performance", sb.url_performance(canal, marca)))


def _resumo_criticos(ldr: list[CelulaLdr], vencidos: list["LeituraVencidos"], saude: int) -> str:
    partes = []
    if ldr:
        partes.append(f"LDR acima do teto em {len(ldr)} conta{'s' if len(ldr) != 1 else ''}")
    for v in vencidos:
        partes.append(f"{pct(v.fracao or 0)} dos pedidos parados vencidos {NO_CANAL[v.canal]}")
    if saude:
        partes.append(f"{saude} {'sinal' if saude == 1 else 'sinais'} na performance oficial")
    return " · ".join(partes) or "alerta crítico"


def montar_mensagem_criticos(itens: list[Item], agora: Optional[datetime] = None) -> sb.Mensagem:
    """DM dos alertas criticos: um grupo por canal, cada um com o seu botao."""
    agora = agora or datetime.now(TZ_OPERACIONAL)
    ldr = [i.dados for i in itens if i.chave.startswith("ldr:") and isinstance(i.dados, CelulaLdr)]
    vencidos = [i.dados for i in itens
                if i.chave.startswith("vencidos:") and isinstance(i.dados, LeituraVencidos)]
    saude = [i for i in itens if i.chave.startswith("saude:")]
    soltos = [i for i in itens if not i.chave.startswith(("ldr:", "vencidos:", "saude:"))
              or (i.chave.startswith(("ldr:", "vencidos:")) and i.dados is None)]

    resumo = _resumo_criticos(ldr, vencidos, len(saude))
    blocos: list[dict] = [
        sb.titulo(":rotating_light: Torre · alerta crítico"),
        sb.nota(f"*{resumo[0].upper() + resumo[1:]}*  ·  "
                f"{agora.astimezone(TZ_OPERACIONAL):%d/%m às %H:%M}"),
        sb.divisor(),
    ]
    for canal in ROTULO_CANAL:
        doc = [c for c in ldr if c.canal == canal]
        if doc:
            blocos.append(_bloco_ldr(canal, doc))
    if ldr:
        blocos.append(sb.nota("LDR dos últimos 7 dias = pedidos atrasados ÷ "
                              "pedidos que venceram no período."))
    for v in vencidos:
        blocos.append(sb.divisor())
        blocos += _bloco_vencidos(v)
    grupos: dict[tuple[str, str], list[str]] = {}
    for i in saude:
        d = i.dados
        chave = (d.marca, d.canal) if isinstance(d, SinalSaude) else ("kokeshi", "mercadolivre")
        grupos.setdefault(chave, []).append(_sem_prefixo(i.texto))
    for (marca, canal), sinais in grupos.items():
        blocos.append(sb.divisor())
        blocos.append(_bloco_saude(marca, canal, sinais))
    if soltos:
        blocos.append(sb.secao("\n".join(f"• {i.texto}" for i in soltos)))
    blocos.append(sb.divisor())
    blocos.append(sb.atalhos(sb.botao("Abrir a Expedição", sb.url_expedicao(), "primary")))
    return sb.Mensagem(texto=f":rotating_light: Torre: {resumo}", blocos=sb.limitar(blocos))
