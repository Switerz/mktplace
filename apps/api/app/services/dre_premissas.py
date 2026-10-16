"""Cenario "Margem de contribuicao estimada com premissas" — DRE-PREMISSAS-1.

COMPLEMENTAR, NUNCA SUBSTITUTO
------------------------------
A DRE realizada continua entregando `margem_contribuicao_conhecida` exatamente
como antes. Este modulo acrescenta, em cada celula, o objeto opcional
`cenario_premissas` — sempre `natureza = "estimada"` — que completa as linhas
sem fonte realizada (impostos, devolucoes, bonificacao, embalagem, publicidade,
logistica...) com PERCENTUAIS de orcamento por marca, publicados em
`marts.fact_dre_premissa_mensal`.

REGRAS
------
- **Celula base primeiro.** `valor = receita_bruta_da_celula x taxa_da_marca`
  em `competencia x canal x marca`. Canal, marca e total sao SOMAS de celulas;
  nenhuma taxa e' reaplicada depois da agregacao.
- **Competencia exata.** A premissa so' vale para a competencia publicada: sem
  retroacao, sem carregar para o mes seguinte. Mes sem premissa => sem cenario.
- **Ausencia nunca vira zero.** Taxa ausente = linha `indisponivel`, fora da
  soma, e o cenario fica `parcial`. Zero so' quando a matriz traz zero.
- **Outras Receitas Operacionais** fica fora da margem (`somavel=False`,
  motivo `sinal_nao_ratificado`): a matriz a rotula como receita e a formula a
  trata como deducao.
- **Afiliado TikTok uma vez so'.** Esta dentro das taxas do repasse. Quando a
  decomposicao e' segura: `despesas_marketplace = taxas - afiliados` e
  `influencer = afiliados`, somando exatamente as taxas. Senao, taxa integral e
  influencer apenas informativo.
- **Frete realizado nao entra de novo.** "Logistica - Fretes" vem da matriz; o
  frete medido da DRE aparece so' como comparacao.
- **Realizado intacto.** Receita, CMV, Ads e taxas vem da celula realizada,
  com o status dela. Nenhuma regra de maturidade e' enfraquecida: mes em
  andamento e' PREVIA, mes imaturo nunca e' `calculavel`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

NATUREZA = "estimada"
ROTULO_MARGEM_ESTIMADA = "Margem de contribuição estimada com premissas"

CALCULAVEL = "calculavel"
PARCIAL = "parcial"
NAO_CALCULAVEL = "nao_calculavel"
_ORDEM = {NAO_CALCULAVEL: 0, PARCIAL: 1, CALCULAVEL: 2}

ORIGEM_REALIZADO = "realizado"
ORIGEM_PREMISSA = "premissa"
ORIGEM_DECOMPOSICAO = "decomposicao"
ORIGEM_INDISPONIVEL = "indisponivel"
ORIGEM_CALCULADO = "calculado"   # subtotais

#: Origem do CMV usada hoje. Contrato preparado para `cmp_sku` (PR #78) sem usa-la.
FONTE_CMV = "dre_realizada"
FONTES_CMV_PREVISTAS = ("dre_realizada", "cmp_sku")

SINAL_NAO_RATIFICADO = "sinal_nao_ratificado"
SOBREPOSICAO_NAO_RATIFICADA = "sobreposicao_nao_ratificada"
#: Premissas armazenadas e VISIVEIS, mas fora da margem, com o motivo. Mesma
#: lista do CHECK `ck_fdpm_nao_somavel` da migration 026.
NAO_SOMAVEIS = {
    "outras_receitas_operacionais": (SINAL_NAO_RATIFICADO,
                                     "rotulo de receita, formula de deducao: fora da margem ate ratificacao"),
    "comissoes": (SOBREPOSICAO_NAO_RATIFICADA,
                  "pode se sobrepor as taxas reais de marketplace ja usadas: fora da margem ate ratificacao"),
}
MESES_NOME = {1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril", 5: "maio", 6: "junho", 7: "julho",
              8: "agosto", 9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro"}


def rotulo_premissas(mes: date) -> str:
    """Procedencia PUBLICA. Derivada da competencia; o nome do arquivo de orcamento nunca sai."""
    return f"Premissas de {MESES_NOME[mes.month]}/{mes.year}"
TABELA_PREMISSAS = "marts.fact_dre_premissa_mensal"

#: Estrutura da DRE estimada. `grupo` diz a qual subtotal o item pertence.
#: Itens de premissa trazem o componente da tabela 026.
ESTRUTURA: tuple[dict, ...] = (
    {"linha": "receita_bruta", "rotulo": "Receita Bruta", "tipo": "item", "fonte": "receita"},
    {"linha": "devolucoes_reembolsos", "rotulo": "(-) Devoluções e Reembolsos", "tipo": "item", "fonte": "premissa"},
    {"linha": "impostos_sobre_receita", "rotulo": "(-) Impostos sobre Receita", "tipo": "subtotal"},
    {"linha": "icms", "rotulo": "ICMS", "tipo": "item", "fonte": "premissa", "grupo": "impostos_sobre_receita"},
    {"linha": "difal", "rotulo": "DIFAL", "tipo": "item", "fonte": "premissa", "grupo": "impostos_sobre_receita"},
    {"linha": "pis", "rotulo": "PIS", "tipo": "item", "fonte": "premissa", "grupo": "impostos_sobre_receita"},
    {"linha": "cofins", "rotulo": "COFINS", "tipo": "item", "fonte": "premissa", "grupo": "impostos_sobre_receita"},
    {"linha": "iss_ipi_simples", "rotulo": "ISS/IPI/Simples", "tipo": "item", "fonte": "premissa",
     "grupo": "impostos_sobre_receita"},
    {"linha": "receita_liquida_estimada", "rotulo": "(=) Receita Líquida Estimada", "tipo": "subtotal"},
    {"linha": "cmv", "rotulo": "(-) Custo das Mercadorias", "tipo": "item", "fonte": "cmv"},
    {"linha": "frete_primario", "rotulo": "(-) Frete Primário", "tipo": "item", "fonte": "premissa"},
    {"linha": "bonificacoes", "rotulo": "(-) Bonificações", "tipo": "item", "fonte": "premissa"},
    {"linha": "embalagens", "rotulo": "(-) Embalagens", "tipo": "item", "fonte": "premissa"},
    {"linha": "provisao_perda_estoque", "rotulo": "(-) Provisão para Perda de Estoque", "tipo": "item",
     "fonte": "premissa"},
    {"linha": "lucro_bruto_estimado", "rotulo": "(=) Lucro Bruto Estimado", "tipo": "subtotal"},
    {"linha": "midia_online", "rotulo": "(-) Mídia Online", "tipo": "item", "fonte": "ads"},
    {"linha": "influencer", "rotulo": "(-) Influencer", "tipo": "item", "fonte": "influencer"},
    {"linha": "publicidade_propaganda", "rotulo": "(-) Publicidade e Propaganda", "tipo": "item", "fonte": "premissa"},
    {"linha": "comissoes", "rotulo": "(-) Comissões", "tipo": "item", "fonte": "premissa"},
    {"linha": "agencia_growth", "rotulo": "(-) Agência de Growth", "tipo": "item", "fonte": "premissa"},
    {"linha": "despesas_gateway", "rotulo": "(-) Gateway", "tipo": "item", "fonte": "premissa"},
    {"linha": "despesas_marketplace", "rotulo": "(-) Despesas de Marketplace", "tipo": "item", "fonte": "taxas"},
    {"linha": "brindes", "rotulo": "(-) Brindes", "tipo": "item", "fonte": "premissa"},
    {"linha": "logistica_fretes", "rotulo": "(-) Logística — Fretes", "tipo": "item", "fonte": "premissa"},
    {"linha": "logistica_operador", "rotulo": "(-) Logística — Operador", "tipo": "item", "fonte": "premissa"},
    {"linha": "margem_contribuicao_estimada", "rotulo": "(=) Margem de Contribuição Estimada", "tipo": "subtotal"},
)
#: Fora da soma: exibidas, nunca subtraidas.
INFORMATIVAS: tuple[dict, ...] = (
    {"linha": "outras_receitas_operacionais", "rotulo": "Outras Receitas Operacionais", "tipo": "informativo",
     "fonte": "premissa"},
    {"linha": "frete_realizado_comparacao", "rotulo": "Frete realizado (comparação)", "tipo": "informativo",
     "fonte": "frete"},
)
COMPONENTES_PREMISSA = tuple(e["linha"] for e in ESTRUTURA + INFORMATIVAS if e.get("fonte") == "premissa")
#: Itens subtraidos da receita na margem estimada (quando tem valor e e' somavel).
DEDUCOES = tuple(e["linha"] for e in ESTRUTURA if e["tipo"] == "item" and e["linha"] != "receita_bruta")
SUBTOTAL_ATE = {
    "impostos_sobre_receita": ("icms", "difal", "pis", "cofins", "iss_ipi_simples"),
    "receita_liquida_estimada": ("devolucoes_reembolsos", "icms", "difal", "pis", "cofins", "iss_ipi_simples"),
}
ANTES_LUCRO_BRUTO = SUBTOTAL_ATE["receita_liquida_estimada"] + (
    "cmv", "frete_primario", "bonificacoes", "embalagens", "provisao_perda_estoque")

#: Status da celula realizada que tornam o componente utilizavel no cenario.
USAVEL_OK = ("disponivel",)
USAVEL_PARCIAL = ("parcial", "provisorio", "nao_confirmado")


# ---------------------------------------------------------------------------
# Leitura (linhas ja lidas do Neon)
# ---------------------------------------------------------------------------

@dataclass
class LeituraPremissas:
    tabela_existe: bool
    linhas: list[dict] = field(default_factory=list)


def _mes(v) -> date:
    if isinstance(v, datetime):
        v = v.date()
    return date(v.year, v.month, 1)


def indexar_premissas(leitura: LeituraPremissas | None) -> dict[tuple[date, str], dict]:
    """(competencia, marca) -> {componentes, meta, valida}. So' o que foi publicado."""
    out: dict[tuple[date, str], dict] = {}
    if leitura is None:
        return out
    for r in leitura.linhas:
        k = (_mes(r["competencia"]), r["brand"])
        g = out.setdefault(k, {"componentes": {}, "runs": set(), "lotes_ok": True, "sha": set(),
                               "data_fonte": r.get("source_sent_on"), "periodo": r.get("source_period"),
                               "carregado_em": r.get("loaded_at")})
        g["componentes"][r["component"]] = r
        g["runs"].add(r.get("run_id"))
        g["sha"].add(r.get("source_sha256"))
        if r.get("lote_status") != "success":
            g["lotes_ok"] = False
    for g in out.values():
        g["valida"] = g["lotes_ok"] and len(g["runs"]) == 1 and len(g["sha"]) == 1
    return out


def resumo_premissas(leitura: LeituraPremissas | None, meses: list[date], marcas: list[str]) -> dict:
    if leitura is None:
        return {"estado": "nao_consultado", "competencias_publicadas": [], "source_sha256": None,
                "rotulo": None, "data_fonte": None, "carregado_em": None, "motivo": "premissas nao consultadas"}
    if not leitura.tabela_existe:
        return {"estado": "tabela_ausente", "competencias_publicadas": [], "source_sha256": None,
                "rotulo": None, "data_fonte": None, "carregado_em": None,
                "motivo": f"{TABELA_PREMISSAS} nao existe (migration 026 nao aplicada)"}
    idx = indexar_premissas(leitura)
    pub = sorted({m for m, b in idx if b in marcas and m in meses})
    if not pub:
        return {"estado": "nao_publicado", "competencias_publicadas": [], "source_sha256": None,
                "rotulo": None, "data_fonte": None, "carregado_em": None,
                "motivo": "nenhuma competencia do periodo tem premissa publicada"}
    grupos = [g for (m, b), g in idx.items() if m in pub and b in marcas]
    shas = sorted({s for g in grupos for s in g["sha"] if s})
    estado = "ok" if len(pub) == len(meses) else "parcial"
    if not all(g["valida"] for g in grupos):
        estado = "lote_invalido"
    return {"estado": estado, "competencias_publicadas": [m.isoformat()[:7] for m in pub],
            "source_sha256": shas[0] if len(shas) == 1 else None,
            "rotulo": ", ".join(rotulo_premissas(m) for m in pub),
            "data_fonte": max((g["data_fonte"] for g in grupos if g["data_fonte"]), default=None),
            "carregado_em": max(g["carregado_em"] for g in grupos),
            "motivo": None if estado == "ok" else (
                "lote de premissas nao concluido ou misto" if estado == "lote_invalido"
                else "ha competencia do periodo sem premissa publicada (premissa nunca e' estendida)")}


# ---------------------------------------------------------------------------
# Celula base — PURA
# ---------------------------------------------------------------------------

def _r2(x: float) -> float:
    return round(x, 2)


def _pct(valor: float | None, receita: float | None) -> float | None:
    if valor is None or not receita:
        return None
    return round(valor / receita * 100, 2)


def _linha(e: dict, *, valor=None, origem=ORIGEM_INDISPONIVEL, percentual=None, status="indisponivel",
           motivo=None, somavel=None) -> dict:
    return {"linha": e["linha"], "rotulo": e["rotulo"], "tipo": e["tipo"], "valor": valor, "origem": origem,
            "percentual": percentual, "status": status, "motivo": motivo,
            "somavel": (e["tipo"] == "item") if somavel is None else somavel}


def _uso(comp: dict) -> str:
    """`ok` | `parcial` | `inutilizavel` para um componente realizado."""
    if comp["valor"] is None:
        return "inutilizavel"
    if comp["status"] in USAVEL_OK:
        return "ok"
    if comp["status"] in USAVEL_PARCIAL:
        return "parcial"
    return "inutilizavel"


def montar_cenario(celula: dict, premissa: dict | None, *, mes: date, hoje: date, maduro: bool,
                   fim_do_mes: date) -> dict | None:
    """Cenario estimado de UMA celula base. `None` quando a competencia nao tem premissa."""
    if premissa is None:
        return None
    canal = celula["canal"]
    comps = celula["componentes"]
    rec = comps["receita"]
    receita = rec["valor"]
    linhas: dict[str, dict] = {}
    motivos_nc: list[str] = []
    motivos_parcial: list[str] = []
    avisos: list[str] = []
    ausentes: list[dict] = []
    por_linha = {e["linha"]: e for e in ESTRUTURA + INFORMATIVAS}

    if not premissa["valida"]:
        motivos_nc.append("origem da premissa invalida: lote nao concluido, misto ou de mais de uma fonte")

    # --- receita -----------------------------------------------------------
    if receita is None:
        motivos_nc.append("receita bruta indisponivel")
        linhas["receita_bruta"] = _linha(por_linha["receita_bruta"], status=rec["status"], motivo=rec["motivo"],
                                         somavel=False)
    else:
        linhas["receita_bruta"] = _linha(por_linha["receita_bruta"], valor=receita, origem=ORIGEM_REALIZADO,
                                         status=rec["status"], motivo=rec["motivo"], somavel=False)
        if rec["status"] != "disponivel":
            motivos_parcial.append(f"receita com cobertura parcial: {rec['motivo']}")

    # --- premissas ---------------------------------------------------------
    for nome in COMPONENTES_PREMISSA:
        e = por_linha[nome]
        row = premissa["componentes"].get(nome)
        rate = None if row is None or row.get("rate") is None else Decimal(str(row["rate"]))
        if rate is None:
            linhas[nome] = _linha(e, status="ausente_na_origem",
                                  motivo="premissa ausente na matriz (ausencia nao e' zero)",
                                  somavel=False if e["tipo"] == "informativo" else None)
            if e["tipo"] == "item" and nome not in NAO_SOMAVEIS:
                ausentes.append({"linha": nome, "motivo": "premissa ausente"})
                motivos_parcial.append(f"premissa ausente: {nome}")
            continue
        valor = None if receita is None else _r2(float(Decimal(str(receita)) * rate))
        if nome in NAO_SOMAVEIS:
            status, motivo = NAO_SOMAVEIS[nome]
            # Visivel como premissa, com taxa e valor; NUNCA subtraida da margem.
            linhas[nome] = _linha(e, valor=valor, origem=ORIGEM_PREMISSA, percentual=float(rate),
                                  status=status, somavel=False, motivo=motivo)
            continue
        linhas[nome] = _linha(e, valor=valor, origem=ORIGEM_PREMISSA, percentual=float(rate), status="ok")

    # --- CMV (realizado; contrato pronto para CMP por SKU) -------------------
    cmv = comps["cmv"]
    uso = _uso(cmv)
    e = por_linha["cmv"]
    if uso == "inutilizavel":
        motivos_nc.append(f"cmv {cmv['status']}: {cmv['motivo']}")
        linhas["cmv"] = _linha(e, status=cmv["status"], motivo=cmv["motivo"])
        ausentes.append({"linha": "cmv", "motivo": cmv["status"]})
    else:
        linhas["cmv"] = _linha(e, valor=cmv["valor"], origem=ORIGEM_REALIZADO, status=cmv["status"],
                               motivo=cmv["motivo"])
        if uso == "parcial":
            motivos_parcial.append(f"cmv {cmv['status']}")

    # --- Midia Online (Ads realizado) --------------------------------------
    ads = comps["ads"]
    e = por_linha["midia_online"]
    if ads["status"] == "nao_aplicavel":
        linhas["midia_online"] = _linha(e, status="nao_aplicavel", motivo=ads["motivo"])
    elif _uso(ads) == "inutilizavel":
        linhas["midia_online"] = _linha(e, status=ads["status"], motivo=ads["motivo"])
        ausentes.append({"linha": "midia_online", "motivo": ads["status"]})
        motivos_parcial.append(f"midia online {ads['status']}: {ads['motivo']}")
    else:
        linhas["midia_online"] = _linha(e, valor=ads["valor"], origem=ORIGEM_REALIZADO, status=ads["status"],
                                        motivo=ads["motivo"])
        if _uso(ads) == "parcial":
            motivos_parcial.append(f"midia online {ads['status']}")

    # --- Despesas de Marketplace e Influencer --------------------------------
    tx, af = comps["taxas"], comps["afiliados"]
    e_mk, e_in = por_linha["despesas_marketplace"], por_linha["influencer"]
    uso_tx = _uso(tx)
    if uso_tx == "inutilizavel":
        motivos_nc.append(f"taxas {tx['status']}: {tx['motivo']}")
        linhas["despesas_marketplace"] = _linha(e_mk, status=tx["status"], motivo=tx["motivo"])
        ausentes.append({"linha": "despesas_marketplace", "motivo": tx["status"]})
    else:
        if uso_tx == "parcial":
            motivos_parcial.append(f"taxas {tx['status']}")
        linhas["despesas_marketplace"] = _linha(e_mk, valor=tx["valor"], origem=ORIGEM_REALIZADO,
                                                status=tx["status"], motivo=tx["motivo"])
    if canal == "tiktok":
        af_ok = af["status"] == "disponivel" and af["valor"] is not None
        if af_ok and uso_tx != "inutilizavel" and af["valor"] <= tx["valor"] + 0.01:
            mk = _r2(tx["valor"] - af["valor"])
            linhas["despesas_marketplace"] = _linha(
                e_mk, valor=mk, origem=ORIGEM_DECOMPOSICAO, status=tx["status"],
                motivo="taxas do repasse menos afiliados (decomposicao; soma = taxas)")
            linhas["influencer"] = _linha(e_in, valor=af["valor"], origem=ORIGEM_DECOMPOSICAO, status=af["status"],
                                          motivo="afiliados ja contidos nas taxas do TikTok; separados uma vez so'")
        elif af_ok:
            # Decomposicao insegura (afiliado > taxa, ou taxa inutilizavel): taxa integral.
            linhas["influencer"] = _linha(e_in, valor=af["valor"], origem=ORIGEM_REALIZADO, status=af["status"],
                                          somavel=False,
                                          motivo="informativo: decomposicao insegura, a taxa integral ja o contem")
            if uso_tx != "inutilizavel":
                motivos_parcial.append("decomposicao afiliado x taxa do TikTok insegura (regra de dupla contagem)")
        else:
            linhas["influencer"] = _linha(e_in, status=af["status"], somavel=False,
                                          motivo="afiliados nao medidos; a taxa integral ja os contem")
            avisos.append("Influencer do TikTok nao separado: afiliados nao medidos neste mes.")
    elif canal == "ml":
        linhas["influencer"] = _linha(e_in, status="nao_aplicavel", motivo=af["motivo"])
    else:
        linhas["influencer"] = _linha(e_in, status=af["status"], somavel=False, motivo=af["motivo"])
        avisos.append("Influencer da Shopee indisponivel/nao confirmado: nao somado.")

    # --- Frete realizado: so' comparacao ------------------------------------
    fr = comps["frete"]
    linhas["frete_realizado_comparacao"] = _linha(
        por_linha["frete_realizado_comparacao"], valor=fr["valor"],
        origem=ORIGEM_REALIZADO if fr["valor"] is not None else ORIGEM_INDISPONIVEL, status=fr["status"],
        somavel=False, motivo="comparacao com 'Logistica - Fretes' da matriz; nunca somado")

    # --- subtotais e margem ---------------------------------------------------
    previa = hoje <= fim_do_mes
    if previa:
        motivos_parcial.append("previa: competencia em andamento")
    elif not maduro:
        motivos_parcial.append("competencia ainda imatura pela regra M+2 da DRE realizada")

    estado = NAO_CALCULAVEL if motivos_nc else (PARCIAL if motivos_parcial else CALCULAVEL)
    _subtotais(linhas, receita, calcular=estado != NAO_CALCULAVEL)
    for ln in linhas.values():
        ln["pct_receita"] = _pct(ln["valor"], receita)
    margem = linhas["margem_contribuicao_estimada"]["valor"]
    if linhas["comissoes"]["status"] == SOBREPOSICAO_NAO_RATIFICADA:
        avisos.append("Comissoes fora da margem: sobreposicao com as taxas de marketplace nao ratificada.")
    if linhas["outras_receitas_operacionais"]["status"] == SINAL_NAO_RATIFICADO:
        avisos.append("Outras Receitas Operacionais fora da margem: sinal (receita x deducao) nao ratificado.")
    avisos.append("Logistica - Fretes vem da matriz; o frete realizado aparece so' como comparacao.")
    return {
        "competencia_premissas": mes.isoformat()[:7],
        "natureza": NATUREZA,
        "rotulo": ROTULO_MARGEM_ESTIMADA,
        "estado": estado,
        "previa": previa,
        "competencia_madura": maduro,
        "receita_base": receita,
        "fonte_cmv": FONTE_CMV,
        "linhas": [linhas[e["linha"]] for e in ESTRUTURA + INFORMATIVAS],
        "componentes_ausentes": ausentes,
        "motivos": motivos_nc + motivos_parcial,
        "avisos": avisos,
        "margem_contribuicao_estimada": margem,
        "margem_contribuicao_estimada_pct": _pct(margem, receita),
        "fonte_premissas": {"rotulo": rotulo_premissas(mes), "competencia": mes.isoformat()[:7],
                            "data_fonte": premissa["data_fonte"],
                            "source_sha256": next(iter(premissa["sha"]), None),
                            "carregado_em": premissa["carregado_em"]},
        "celulas": 1,
    }


def _subtotais(linhas: dict[str, dict], receita: float | None, *, calcular: bool) -> None:
    """Subtotais e margem a partir dos itens SOMAVEIS com valor. Nunca reaplica taxa."""
    def soma(nomes) -> float:
        return sum(linhas[n]["valor"] for n in nomes if linhas[n]["valor"] is not None and linhas[n]["somavel"])

    por_linha = {e["linha"]: e for e in ESTRUTURA}
    ok = calcular and receita is not None
    imp = soma(SUBTOTAL_ATE["impostos_sobre_receita"]) if ok else None
    rl = receita - soma(SUBTOTAL_ATE["receita_liquida_estimada"]) if ok else None
    lb = receita - soma(ANTES_LUCRO_BRUTO) if ok else None
    mc = receita - soma(DEDUCOES) if ok else None
    for nome, v in (("impostos_sobre_receita", imp), ("receita_liquida_estimada", rl),
                    ("lucro_bruto_estimado", lb), ("margem_contribuicao_estimada", mc)):
        linhas[nome] = _linha(por_linha[nome], valor=None if v is None else _r2(v), origem=ORIGEM_CALCULADO,
                              status="ok" if v is not None else "nao_calculavel", somavel=False)


# ---------------------------------------------------------------------------
# Agregacao — soma de celulas, nunca reaplicacao de taxa
# ---------------------------------------------------------------------------

def agregar_cenarios(cenarios: list[dict | None]) -> dict | None:
    """Soma os cenarios das celulas base do recorte.

    - todas sem cenario -> None;
    - parte sem cenario -> `nao_calculavel`: o recorte mistura celulas sem
      premissa (outra competencia ou marca fora da matriz) e a margem nao pode
      ser comparada;
    - senao: cada linha e' a SOMA dos valores das celulas; o percentual da
      marca nao existe no agregado (`percentual=None`), so' o efetivo
      `pct_receita`; margem = soma das margens das celulas, e so' se nenhuma
      celula for `nao_calculavel`.
    """
    if not cenarios or all(c is None for c in cenarios):
        return None
    validos = [c for c in cenarios if c is not None]
    misto = len(validos) != len(cenarios)
    receita = sum(c["receita_base"] for c in validos if c["receita_base"] is not None)
    linhas = []
    for i, e in enumerate(ESTRUTURA + INFORMATIVAS):
        partes = [c["linhas"][i] for c in validos]
        com_valor = [p for p in partes if p["valor"] is not None]
        valor = _r2(sum(p["valor"] for p in com_valor)) if com_valor else None
        origens = {p["origem"] for p in partes}
        status = ("ok" if len(com_valor) == len(partes)
                  else ("parcial" if com_valor else partes[0]["status"]))
        linhas.append({"linha": e["linha"], "rotulo": e["rotulo"], "tipo": e["tipo"], "valor": valor,
                       "origem": origens.pop() if len(origens) == 1 else "misto",
                       "percentual": None, "status": status,
                       "motivo": None if status == "ok" else f"valor em {len(com_valor)} de {len(partes)} celulas",
                       "somavel": partes[0]["somavel"], "pct_receita": _pct(valor, receita)})
    estado = min((c["estado"] for c in validos), key=_ORDEM.get)
    motivos = []
    if misto:
        estado = NAO_CALCULAVEL
        motivos.append("o recorte mistura celulas sem premissa publicada; premissa nunca e' estendida")
    for c in validos:
        for m in c["motivos"]:
            if m not in motivos:
                motivos.append(m)
    margem = None
    if estado != NAO_CALCULAVEL:
        margem = _r2(sum(c["margem_contribuicao_estimada"] for c in validos))
    # A linha de margem do agregado e' a soma das margens das celulas, nao um recalculo.
    for ln in linhas:
        if ln["linha"] == "margem_contribuicao_estimada":
            ln["valor"], ln["pct_receita"] = margem, _pct(margem, receita)
    ausentes, avisos = [], []
    for c in validos:
        for a in c["componentes_ausentes"]:
            if a not in ausentes:
                ausentes.append(a)
        for a in c["avisos"]:
            if a not in avisos:
                avisos.append(a)
    comps = sorted({c["competencia_premissas"] for c in validos})
    return {
        "competencia_premissas": comps[0] if len(comps) == 1 else ",".join(comps),
        "natureza": NATUREZA, "rotulo": ROTULO_MARGEM_ESTIMADA, "estado": estado,
        "previa": any(c["previa"] for c in validos),
        "competencia_madura": all(c["competencia_madura"] for c in validos),
        "receita_base": _r2(receita) if validos else None, "fonte_cmv": FONTE_CMV,
        "linhas": linhas, "componentes_ausentes": ausentes, "motivos": motivos, "avisos": avisos,
        "margem_contribuicao_estimada": margem,
        "margem_contribuicao_estimada_pct": _pct(margem, receita),
        "fonte_premissas": validos[0]["fonte_premissas"],
        "celulas": sum(c["celulas"] for c in validos),
    }


def linha(cenario: dict, nome: str) -> dict[str, Any]:
    return next(ln for ln in cenario["linhas"] if ln["linha"] == nome)
