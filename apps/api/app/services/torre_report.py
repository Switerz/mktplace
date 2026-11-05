"""Report diario das 8h do bot "Torre" (R1 em `docs/torre_bot_alertas.md`).

"Ontem tivemos X de GMV nas marcas, N avancos, M problemas" e o detalhe:
GMV de ontem por marca contra a meta do dia e contra o mesmo dia da semana
anterior, o mes ate' ontem contra o ritmo da meta, e a lista de avancos e
problemas (LDR, Kokeshi, metas, quedas, carga atrasada).

A leitura (`ler_dados`) e' fina; a regra mora em `compor_report`, pura e
testavel sem banco.

FONTES
------
  GMV     marts.fact_marketplace_daily_performance (a mesma da Gerencial)
  Metas   marts.meta_gmv_mensal (`metas_service.ler`), distribuida pela curva
          do canal (`metas_curva`)
  LDR     torre_alertas.ler_ldr      (faixa do topo da Expedicao)
  Kokeshi torre_alertas.ler_saude    (reputacao oficial do ML)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import text

from app.services import metas_curva, metas_service
from app.services import slack_blocks as sb
from app.services import torre_alertas as ta
from app.services.gold_service import TZ_OPERACIONAL

URL_DA_TORRE = "https://mktplace-ten.vercel.app"

MARCAS: tuple[str, ...] = metas_service.MARCAS
#: marketplace_id da fact -> canal das metas.
CANAL_DO_MKT = {1: "tiktok", 2: "ml", 3: "shopee"}
ROTULO_CANAL_META = {"tiktok": "TikTok Shop", "ml": "Mercado Livre", "shopee": "Shopee"}

# Limiares do report (ver o doc). Fracoes.
META_DIA_CRITICO = 0.70
META_DIA_ATENCAO = 0.80
QUEDA_ATENCAO = -0.20
ALTA_AVANCO = 0.15
#: Base minima (R$) do mesmo dia da semana anterior para comparar variacao.
PISO_COMPARACAO = 10_000.0
RITMO_ATENCAO = 0.90

DIAS_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro")

SQL_GMV = text("""
    SELECT l.brand_key AS marca, f.marketplace_id AS mkt, f.date AS dia,
           COALESCE(SUM(f.gmv), 0) AS gmv
      FROM marts.fact_marketplace_daily_performance f
      JOIN marts.dim_loja l ON l.loja_id = f.loja_id
     WHERE f.date BETWEEN :inicio AND :fim
     GROUP BY l.brand_key, f.marketplace_id, f.date
""")


# ---------------------------------------------------------------------------
# Formatos
# ---------------------------------------------------------------------------
def brl(v: float) -> str:
    """R$ curto: 'R$ 375,7 mil', 'R$ 1,23 mi', 'R$ 950'."""
    if abs(v) >= 1_000_000:
        return "R$ " + f"{v / 1_000_000:.2f}".replace(".", ",") + " mi"
    if abs(v) >= 1_000:
        return "R$ " + f"{v / 1_000:.1f}".replace(".", ",") + " mil"
    return "R$ " + f"{v:.0f}"


def pct0(f: float) -> str:
    return f"{f * 100:.0f}%"


def variacao(f: float) -> str:
    return ("+" if f >= 0 else "−") + f"{abs(f) * 100:.0f}%"


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
@dataclass
class Dados:
    ontem: date
    #: (marca, canal, dia) -> GMV
    gmv: dict[tuple[str, str, date], float]
    #: (marca, canal) -> meta do mes de `ontem`
    metas: dict[tuple[str, str], float]
    ldr: ta.LeituraLdr
    saude: ta.LeituraSaude


def ler_dados(db, agora: datetime) -> Dados:
    ontem = agora.astimezone(TZ_OPERACIONAL).date() - timedelta(days=1)
    mes = ontem.replace(day=1)
    inicio = min(mes, ontem - timedelta(days=7))
    gmv: dict[tuple[str, str, date], float] = {}
    for r in db.execute(SQL_GMV, {"inicio": inicio, "fim": ontem}).mappings():
        canal = CANAL_DO_MKT.get(int(r["mkt"]))
        if canal and r["marca"] in MARCAS:
            gmv[(r["marca"], canal, r["dia"])] = float(r["gmv"] or 0)
    metas = {(c["brand"], c["channel"]): float(c["meta_gmv"])
             for c in metas_service.ler(db, mes)["celulas"]}
    db.rollback()
    ldr = ta.ler_ldr(db, agora)
    saude = ta.ler_saude(agora)
    return Dados(ontem, gmv, metas, ldr, saude)


# ---------------------------------------------------------------------------
# Regra (pura)
# ---------------------------------------------------------------------------
@dataclass
class LinhaMarca:
    marca: str
    gmv: float
    gmv_semana_passada: float
    #: Realizado dos canais com meta / meta do dia; None sem meta.
    pct_meta_dia: Optional[float]

    @property
    def var_semana(self) -> Optional[float]:
        if self.gmv_semana_passada < PISO_COMPARACAO:
            return None
        return self.gmv / self.gmv_semana_passada - 1


@dataclass
class Report:
    ontem: date
    linhas: list[LinhaMarca]
    total: LinhaMarca
    #: (realizado do mes com meta, meta acumulada ate' ontem, meta do mes)
    mes: Optional[tuple[float, float, float]]
    #: Canais que tem meta no mes (rotulos), na ordem de `CANAL_DO_MKT`.
    canais_com_meta: list[str] = field(default_factory=list)
    avancos: list[str] = field(default_factory=list)
    problemas: list[str] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)


CRIT = ":red_circle:"
ATEN = ":warning:"


def compor_report(d: Dados) -> Report:
    ontem, semana = d.ontem, d.ontem - timedelta(days=7)
    canais = tuple(CANAL_DO_MKT.values())

    def soma(marca: Optional[str], canal: Optional[str], dias: list[date]) -> float:
        return sum(v for (m, c, dia), v in d.gmv.items()
                   if (marca is None or m == marca) and (canal is None or c == canal) and dia in dias)

    dias_mes = [ontem.replace(day=i) for i in range(1, ontem.day + 1)]
    linhas: list[LinhaMarca] = []
    meta_dia_total = real_meta_total = 0.0
    for marca in MARCAS:
        meta_dia = real = 0.0
        for canal in canais:
            meta = d.metas.get((marca, canal), 0.0)
            if meta > 0:
                meta_dia += meta * metas_curva.fracao_do_dia(ontem, canal)
                real += soma(marca, canal, [ontem])
        meta_dia_total += meta_dia
        real_meta_total += real
        linhas.append(LinhaMarca(
            marca, soma(marca, None, [ontem]), soma(marca, None, [semana]),
            (real / meta_dia) if meta_dia > 0 else None))
    linhas.sort(key=lambda x: -x.gmv)
    total = LinhaMarca("total", sum(x.gmv for x in linhas), sum(x.gmv_semana_passada for x in linhas),
                       (real_meta_total / meta_dia_total) if meta_dia_total > 0 else None)

    mes = None
    if any(v > 0 for v in d.metas.values()):
        real_mes = esperado = meta_mes = 0.0
        for (marca, canal), meta in d.metas.items():
            if meta <= 0:
                continue
            meta_mes += meta
            esperado += meta * metas_curva.fracao_ate(ontem, canal)
            real_mes += soma(marca, canal, dias_mes)
        mes = (real_mes, esperado, meta_mes)

    com_meta = [ROTULO_CANAL_META[c] for c in canais
                 if any(v > 0 for (_, cc), v in d.metas.items() if cc == c)]
    rep = Report(ontem, linhas, total, mes, com_meta)
    nome = ta.rotulo_marca

    # --- GMV e metas
    for x in linhas:
        if x.pct_meta_dia is not None:
            if x.pct_meta_dia >= 1:
                rep.avancos.append(f"{nome(x.marca)} bateu a meta do dia ({pct0(x.pct_meta_dia)}).")
            elif x.pct_meta_dia < META_DIA_CRITICO:
                rep.problemas.append(f"{CRIT} {nome(x.marca)} fez {pct0(x.pct_meta_dia)} da meta do dia.")
            elif x.pct_meta_dia < META_DIA_ATENCAO:
                rep.problemas.append(f"{ATEN} {nome(x.marca)} fez {pct0(x.pct_meta_dia)} da meta do dia.")
        v = x.var_semana
        if v is not None and v > ALTA_AVANCO:
            rep.avancos.append(f"{nome(x.marca)} cresceu {variacao(v)} contra a "
                               f"{DIAS_SEMANA[semana.weekday()]} passada.")
        elif v is not None and v < QUEDA_ATENCAO:
            rep.problemas.append(f"{ATEN} {nome(x.marca)} caiu {variacao(v)} contra a "
                                 f"{DIAS_SEMANA[semana.weekday()]} passada ({brl(x.gmv)} × "
                                 f"{brl(x.gmv_semana_passada)}).")
    if mes and mes[1] > 0:
        ritmo = mes[0] / mes[1]
        if ritmo >= 1:
            rep.avancos.append(f"Mês no ritmo da meta ({pct0(ritmo)} do esperado até ontem).")
        elif ritmo < RITMO_ATENCAO:
            rep.problemas.append(f"{ATEN} Mês abaixo do ritmo da meta ({pct0(ritmo)} do esperado até ontem).")

    # --- carga atrasada: canal sem GMV ontem que vendia na semana passada
    for canal in canais:
        if soma(None, canal, [ontem]) <= 0 < soma(None, canal, [semana]):
            rep.problemas.append(f"{ATEN} {ROTULO_CANAL_META[canal]} sem GMV ontem: "
                                 "a carga pode estar atrasada.")

    # --- LDR
    validas = [c for c in d.ldr.celulas if c.base >= ta.BASE_MINIMA]
    for c in validas:
        if c.faixa == ta.CRITICO:
            rep.problemas.append(f"{CRIT} {ta.texto_celula(c)}")
    for c in validas:
        if c.faixa == ta.ATENCAO:
            rep.problemas.append(f"{ATEN} {ta.texto_celula(c)}")
    na_meta = sum(1 for c in validas if c.faixa == ta.DENTRO)
    if validas and na_meta:
        rep.avancos.append(f"LDR na meta em {na_meta} de {len(validas)} contas.")
    if d.ldr.indisponiveis:
        rep.notas.append("Sem LDR agora de: " + ", ".join(d.ldr.indisponiveis) + ".")

    # --- Kokeshi (performance oficial)
    for s in d.saude.sinais:
        rep.problemas.append(f"{CRIT if s.nivel == ta.CRITICO else ATEN} {s.texto}")
    for canal in ta.CANAIS_SAUDE:
        ok = [m for m, c in d.saude.em_ordem if c == canal]
        if ok:
            rep.avancos.append(f"Performance oficial em ordem {ta.NO_CANAL[canal]}: "
                               + ", ".join(nome(m) for m in ok) + ".")
    if d.saude.indisponiveis:
        rep.notas.append("Performance oficial indisponível agora: "
                         + ", ".join(d.saude.indisponiveis) + ".")
    if d.saude.sem_fonte:
        rep.notas.append("Ainda sem nota oficial: " + ", ".join(d.saude.sem_fonte) + ".")
    return rep


def montar_mensagem(rep: Report) -> str:
    o = rep.ontem
    quando = f"{DIAS_SEMANA[o.weekday()]}, {o:%d/%m}"
    s = rep.total
    linhas = [
        f":sunrise: *Bom dia! Ontem ({quando}): {brl(s.gmv)} de GMV nas marcas, "
        f"{len(rep.avancos)} avanço{'s' if len(rep.avancos) != 1 else ''}, "
        f"{len(rep.problemas)} problema{'s' if len(rep.problemas) != 1 else ''}.*"
    ]
    evento = metas_curva.evento_do_dia(o)
    if evento:
        linhas.append(f"_Dia de evento: {evento}._")

    def detalhe(x: LinhaMarca) -> str:
        partes = [brl(x.gmv)]
        if x.pct_meta_dia is not None:
            partes.append(f"{pct0(x.pct_meta_dia)} da meta do dia")
        if x.var_semana is not None:
            partes.append(f"{variacao(x.var_semana)} vs {DIAS_SEMANA[o.weekday()]} passada")
        return " · ".join(partes)

    linhas.append("")
    linhas.append("*GMV de ontem por marca*")
    linhas += [f"• {ta.rotulo_marca(x.marca)}: {detalhe(x)}" for x in rep.linhas]
    linhas.append(f"• *Total: {detalhe(s)}*")
    if rep.mes:
        real, esperado, meta = rep.mes
        partes = [brl(real)]
        if esperado > 0:
            partes.append(f"{pct0(real / esperado)} do ritmo da meta")
        partes.append(f"{pct0(real / meta)} da meta do mês ({brl(meta)})")
        linhas.append(f"*{MESES[o.month - 1].capitalize()} até ontem:* " + " · ".join(partes))
    if rep.canais_com_meta:
        linhas.append(f"_Meta e mês contam só os canais com meta ({' e '.join(rep.canais_com_meta)})._")

    linhas.append("")
    linhas.append(f"*:white_check_mark: Avanços ({len(rep.avancos)})*")
    linhas += [f"• {a}" for a in rep.avancos] or ["• Nenhum destaque ontem."]
    linhas.append(f"*:warning: Problemas ({len(rep.problemas)})*")
    linhas += [f"• {p}" for p in rep.problemas] or ["• Nenhum problema. :tada:"]
    if rep.notas:
        linhas.append("_" + " ".join(rep.notas) + "_")
    linhas.append(f"<{URL_DA_TORRE}|Abrir a Torre>")
    return "\n".join(linhas)


def _bola_meta(p: Optional[float]) -> str:
    if p is None:
        return ":white_circle:"
    if p >= 1:
        return ":large_green_circle:"
    if p >= META_DIA_ATENCAO:
        return ":large_yellow_circle:"
    return ":large_orange_circle:" if p >= META_DIA_CRITICO else ":red_circle:"


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def montar_blocos(rep: Report) -> sb.Mensagem:
    """O report em blocos: numeros do dia, marcas, avancos, problemas, atalhos."""
    o = rep.ontem
    dia_sem = DIAS_SEMANA[o.weekday()]
    t = rep.total
    resumo = (f"{brl(t.gmv)} de GMV ontem, {_plural(len(rep.avancos), 'avanço', 'avanços')}, "
              f"{_plural(len(rep.problemas), 'problema', 'problemas')}")

    numeros = [("GMV de ontem", brl(t.gmv))]
    if t.pct_meta_dia is not None:
        numeros.append(("Meta do dia", f"{_bola_meta(t.pct_meta_dia)} {pct0(t.pct_meta_dia)}"))
    if t.var_semana is not None:
        numeros.append((f"vs {dia_sem} passada", variacao(t.var_semana)))
    if rep.mes:
        real, esperado, meta = rep.mes
        numeros.append((f"{MESES[o.month - 1].capitalize()} até ontem",
                        f"{brl(real)} · {pct0(real / meta)} da meta"))
        if esperado > 0:
            numeros.append(("Ritmo do mês", f"{_bola_meta(real / esperado)} {pct0(real / esperado)} do esperado"))

    blocos: list[dict] = [
        sb.titulo(f":sunrise: Bom dia! Resumo de ontem · {dia_sem}, {o:%d/%m}"),
        sb.nota(f"*{resumo}*"),
        sb.campos(*numeros),
    ]
    evento = metas_curva.evento_do_dia(o)
    rodape = []
    if evento:
        rodape.append(f":tada: Dia de evento: *{evento}*")
    if rep.canais_com_meta:
        rodape.append(f"Meta e mês contam só {' e '.join(rep.canais_com_meta)}")
    if rodape:
        blocos.append(sb.nota("  ·  ".join(rodape)))

    blocos.append(sb.divisor())
    linhas = ["*GMV por marca*"]
    for x in rep.linhas:
        partes = [f"*{ta.rotulo_marca(x.marca)}*  {brl(x.gmv)}"]
        if x.pct_meta_dia is not None:
            partes.append(f"{pct0(x.pct_meta_dia)} da meta")
        if x.var_semana is not None:
            partes.append(f"{variacao(x.var_semana)} vs {dia_sem}")
        linhas.append(f"{_bola_meta(x.pct_meta_dia)} " + "  ·  ".join(partes))
    blocos.append(sb.secao("\n".join(linhas), sb.botao("Ver metas", sb.url_metas())))

    blocos.append(sb.divisor())
    blocos.append(sb.secao(f":white_check_mark: *Avanços ({len(rep.avancos)})*\n"
                           + ("\n".join(f"• {a}" for a in rep.avancos) or "• Nenhum destaque ontem.")))
    blocos.append(sb.secao(f":warning: *Problemas ({len(rep.problemas)})*\n"
                           + ("\n".join(rep.problemas) or ":tada: Nenhum problema.")))
    if rep.notas:
        blocos.append(sb.nota(" ".join(rep.notas)))
    blocos.append(sb.divisor())
    blocos.append(sb.atalhos(
        sb.botao("Metas", sb.url_metas(), "primary"),
        sb.botao("Expedição", sb.url_expedicao()),
        sb.botao("Performance", sb.url_performance()),
    ))
    return sb.Mensagem(texto=f":sunrise: Bom dia! {resumo}.", blocos=sb.limitar(blocos))


def chave_do_report(rep: Report) -> str:
    return f"report:{rep.ontem.isoformat()}"
