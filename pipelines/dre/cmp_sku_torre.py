"""DRE-CMP-SKU-1/2 — cobertura do CMP sobre as vendas da Torre e shadow de margem por produto.

SOMENTE ESTUDO. Le o Data Mart em transacao READ ONLY, cruza com o indice de
CMP de `pipelines.dre.cmp_sku` e devolve agregados. Nada e' publicado em API
ou banco.

FONTE DAS VENDAS
----------------
`gold.nf_vendas_plataforma_sku_mensal` (mes x marca x plataforma x SKU, por
emissao de NF), `canal = 'marketplace'`. A receita e' a receita FISCAL do
produto, nao o GMV da Torre. Atribuicao de plataforma: reconcilia com a Torre
no TikTok e NAO reconcilia em ML/Shopee (`docs/margem_real_1_diagnostico.md`
§4.2) — o recorte por canal desses dois e' indicativo.

TEMPORALIDADE (decidida em 13/10/2026)
--------------------------------------
Alternativa 3, entidade x vigencia (`cmp_sku.cmp_vigente`): o CMP vale a partir
da competencia em que aparece, segue vigente ate nova entrada, nunca retroage
e nunca usa `Custo Atual`. Cada casamento registra competencia de origem,
competencia usada, carry-forward e se a origem e' uma fotografia.

CASAMENTO (decidido em 13/10/2026)
----------------------------------
Escopo: so' entidades da marca da venda (`ENTIDADES_POR_MARCA`) e filial SP.
Codigo igual NAO prova produto igual — os codigos numericos colidem entre ERPs
e marcas — entao cada candidato passa por GTIN e por volume declarado;
evidencia DIVERGENTE descarta o candidato.

1. Codigo exato (`codigo_sku` ou `sku_canonico`), com prioridade:
   * candidatos validos com CMP diferente => `conflito`. Nada escolhe entre eles:
     nem GTIN, nem alias, nem media, minimo, maximo ou o mais recente;
   * GTIN concorda => `CODIGO_EXATO_GTIN`; so' volume concorda =>
     `CODIGO_EXATO_VOLUME`;
   * sem evidencia nenhuma, um UNICO candidato => `CODIGO_EXATO_PROVISORIO`
     (baixa confianca); mais de um => `ambiguo`.
2. Via GTIN — so' se o codigo nao achou candidato valido: GTIN valido e unico
   na venda, candidato unico no escopo com CMP vigente, volume CONCORDANTE e o
   GTIN sem reuso entre volumes incompativeis. Qualquer falta deixa sem
   casamento, com o motivo registrado. Nunca resolve conflito.

`KS03041`/`KS03042` (Kokeshi) ficam `origem_a_confirmar`: a origem empresarial
desses SKUs nao e' conhecida e nenhuma outra filial/entidade e' buscada.

Kits nunca entram no shadow: sem regra aprovada, nao se escolhe entre o CMP do
proprio kit e a soma dos componentes. Os dois sao apenas medidos.

NOME DO NUMERO
--------------
`receita fiscal coberta - quantidade x CMP` = **margem bruta apos CMP**.
Nenhum custo variavel (comissao, frete, afiliado, ads, imposto, bonificacao)
esta atribuido por SKU, entao "margem de contribuicao" nao e' calculada e
"margem liquida" nunca e' usada.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from pipelines.dre.cmp_sku import Indice, Vigencia, chave_sku, cmp_vigente

PLATAFORMAS = ("tiktokshop", "mercadolivre", "shopee")

#: [ASSUNCAO a ratificar] Entidades em que cada marca e' vendida, segundo a
#: auditoria das planilhas de 13/10/2026 (Lescent em AP/BB, Apice em AP/Kokeshi)
#: mais o CMV-LCV, cujos codigos `LC...` sao Lescent. Fora daqui, nenhum
#: candidato e' considerado.
ENTIDADES_POR_MARCA = {
    "apice": ("AP_COSMETICS", "KOKESHI"),
    "barbours": ("BB_VAREJO",),
    "kokeshi": ("KOKESHI",),
    "lescent": ("AP_COSMETICS", "BB_VAREJO", "LCV"),
    "rituaria": ("MAGA_COMERCIO",),
}
FILIAIS_SP = frozenset({"SP", "0301"})

#: Decisao de 13/10/2026: sem CMP ate a origem empresarial ser confirmada.
ORIGEM_A_CONFIRMAR = frozenset({("kokeshi", "KS03041"), ("kokeshi", "KS03042")})

NIVEL_GTIN = "CODIGO_EXATO_GTIN"
NIVEL_VOLUME = "CODIGO_EXATO_VOLUME"
NIVEL_PROVISORIO = "CODIGO_EXATO_PROVISORIO"
NIVEL_VIA_GTIN = "VIA_GTIN"
NIVEIS = (NIVEL_GTIN, NIVEL_VIA_GTIN, NIVEL_VOLUME, NIVEL_PROVISORIO)
#: Os dois niveis confirmados por GTIN; nunca misturados com o provisorio.
NIVEIS_GTIN = (NIVEL_GTIN, NIVEL_VIA_GTIN)

#: MARGEM-REAL-1 §4.2, jul+ago/2026: receita fiscal / GMV servido.
ATRIBUICAO_CANAL = {
    "tiktokshop": "reconcilia com a Torre (razao 0,99)",
    "mercadolivre": "nao reconcilia (razao 1,69) - recorte indicativo",
    "shopee": "nao reconcilia (razao 0,39) - recorte indicativo",
}
#: Custos variaveis e o grao em que existem hoje. Nenhum chega ao SKU.
CUSTOS_AUSENTES_POR_SKU = {
    "tiktokshop": ["comissao/tarifa (so' dia x marca)", "afiliados (so' mes x marca)", "frete (so' statement)",
                   "ads (inexistente no mart)", "impostos (sem premissa)", "bonificacao (premissa % pendente)"],
    "mercadolivre": ["comissao (item de pedido em api.ml_order_line_items, fora do mart)", "frete (dia x marca)",
                     "ads (dia x marca)", "afiliados (sem fonte)", "impostos (sem premissa)",
                     "bonificacao (premissa % pendente)"],
    "shopee": ["comissao/tarifa (so' dia x marca)", "frete (dia x marca)", "ads (dia x marca)",
               "afiliados (sem fonte)", "impostos (sem premissa)", "bonificacao (premissa % pendente)"],
}

SQL_VENDAS = """
    SELECT data_mes AS competencia, marca, plataforma, tipo_operacao,
           codigo_sku, sku_canonico, MIN(produto) AS produto,
           SUM(quantidade) AS quantidade, SUM(receita_produto) AS receita,
           SUM(custo) AS custo_fiscal, COUNT(*) AS linhas
    FROM gold.nf_vendas_plataforma_sku_mensal
    WHERE canal = 'marketplace'
      AND plataforma = ANY(:plataformas)
      AND data_mes >= :de AND data_mes <= :ate
    GROUP BY 1, 2, 3, 4, 5, 6
"""
SQL_CADASTRO = """
    SELECT marca, sku, sku_antigo, tipo, gtin_ean
    FROM silver.gobeaute_produto_cadastro
"""
SQL_KITS = """
    SELECT kit_codigo, componente_codigo, qty_per_kit, kit_marca
    FROM gold.bridge_kit_componente_gobeauty
"""

RE_TAMANHO = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|l|g|kg|mg|caps|cps|c[aá]psulas)\b", re.IGNORECASE)
RE_EAN13 = re.compile(r"(?<!\d)(\d{13})(?!\d)")

CONCORDA, DIVERGE, INDETERMINADO = "CONCORDA", "DIVERGE", "INDETERMINADO"


class TorreIndisponivel(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Evidencias
# ---------------------------------------------------------------------------

def tamanhos(texto: Optional[str]) -> set:
    """Volumes declarados normalizados: {('ml', 200.0), ('caps', 60.0)}."""
    out = set()
    for num, un in RE_TAMANHO.findall(texto or ""):
        v = float(num.replace(",", "."))
        un = un.lower()
        if un == "l":
            un, v = "ml", v * 1000
        elif un == "kg":
            un, v = "g", v * 1000
        elif un in ("cps", "capsulas", "cápsulas"):
            un = "caps"
        out.add((un, v))
    return out


def ean13_valido(codigo: str) -> bool:
    """Digito verificador GS1. Placeholders de cadastro (`0000000000029`) caem aqui."""
    if not re.fullmatch(r"\d{13}", codigo or "") or codigo.startswith("0000"):
        return False
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(codigo[:12]))
    return (10 - soma % 10) % 10 == int(codigo[12])


def gtins(*textos: Optional[str]) -> set:
    out = set()
    for t in textos:
        for g in RE_EAN13.findall(t or ""):
            if ean13_valido(g):
                out.add(g)
    return out


def comparar(a: set, b: set) -> str:
    """CONCORDA | DIVERGE | INDETERMINADO (um dos lados sem evidencia)."""
    if not a or not b:
        return INDETERMINADO
    return CONCORDA if a & b else DIVERGE


def comparar_tamanho(a: Optional[str], b: Optional[str]) -> str:
    return comparar(tamanhos(a), tamanhos(b))


def chave_marca(valor: Optional[str]) -> str:
    """`Rituária` -> `rituaria`, `By Samia` -> `bysamia`: o grao de marca da Torre."""
    sem_acento = unicodedata.normalize("NFKD", valor or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def gtins_da_chave(ind: Indice, k: tuple) -> set:
    return gtins(*ind.descricoes.get(k, set()), *ind.gtins.get(k, set()))


def volumes_da_chave(ind: Indice, k: tuple) -> set:
    out = set()
    for d in ind.descricoes.get(k, set()):
        out |= tamanhos(d)
    return out


def indice_gtin(ind: Indice) -> dict:
    """GTIN -> chaves (entidade, filial, sku) que o declaram (coluna ou descricao)."""
    out: dict = defaultdict(set)
    for k in ind.chaves():
        for g in gtins_da_chave(ind, k):
            out[g].add(k)
    return out


def gtin_reutilizado(ind: Indice, chaves: set) -> bool:
    """Mesmo GTIN em chaves com volumes declarados incompativeis (EAN reaproveitado)."""
    vols = [v for v in (volumes_da_chave(ind, k) for k in chaves) if v]
    return any(not (a & b) for i, a in enumerate(vols) for b in vols[i + 1:])


# ---------------------------------------------------------------------------
# Catalogo
# ---------------------------------------------------------------------------

@dataclass
class Catalogo:
    """Cadastro e kits ESCOPADOS POR MARCA: o mesmo codigo e' outro produto em outra marca.

    Todas as chaves sao (marca, codigo).
    """
    kits: set = field(default_factory=set)
    simples: set = field(default_factory=set)
    alias: dict = field(default_factory=lambda: defaultdict(set))         # -> codigos equivalentes
    gtin: dict = field(default_factory=lambda: defaultdict(set))          # -> GTINs validos
    componentes: dict = field(default_factory=lambda: defaultdict(list))  # kit -> [(componente, qtd)]


def montar_catalogo(cadastro: list[dict], kits: list[dict]) -> Catalogo:
    cat = Catalogo()
    for r in cadastro:
        m = chave_marca(r.get("marca"))
        codigos = {chave_sku(r["sku"]), chave_sku(r.get("sku_antigo"))} - {""}
        alvo = cat.kits if (r.get("tipo") or "").upper() == "KT" else cat.simples
        alvo |= {(m, c) for c in codigos}
        g = gtins(r.get("gtin_ean"))
        for c in codigos:
            cat.alias[(m, c)] |= codigos - {c}
            cat.gtin[(m, c)] |= g
    for r in kits:
        m = chave_marca(r.get("kit_marca"))
        k, c = chave_sku(r["kit_codigo"]), chave_sku(r["componente_codigo"])
        if k and c:
            cat.kits.add((m, k))
            qtd = Decimal(str(r["qty_per_kit"])) if r["qty_per_kit"] is not None else None
            cat.componentes[(m, k)].append((c, qtd))
    return cat


def classificar_tipo(cat: Catalogo, marca: str, codigos: list[str]) -> str:
    m = chave_marca(marca)
    if any((m, c) in cat.kits for c in codigos):
        return "kit"
    if any((m, c) in cat.simples for c in codigos):
        return "simples"
    return "desconhecido"


# ---------------------------------------------------------------------------
# Resolucao
# ---------------------------------------------------------------------------

@dataclass
class Resolucao:
    status: str          # coberto | conflito | ambiguo | evidencia_diverge | sem_cmp | origem_a_confirmar | kit_sem_regra
    cmp: Optional[Decimal] = None
    nivel: str = ""      # um de NIVEIS, so' em `coberto`
    metodos: tuple = ()
    entidades: tuple = ()
    descartados: int = 0
    motivo: str = ""     # por que ficou sem casamento (inclui a tentativa pela via GTIN)
    vigencia: Optional[Vigencia] = None
    candidatos: tuple = ()  # (entidade, sku, cmp, competencia_origem) — para o de-para de conflitos


def gtins_da_venda(cat: Optional[Catalogo], marca: str, produto: Optional[str], codigos: dict) -> set:
    g = gtins(produto)
    if cat is not None:
        for cod in codigos.values():
            g |= cat.gtin.get((chave_marca(marca), chave_sku(cod)), set())
    return g


def _no_escopo(marca: str, k: tuple) -> bool:
    return k[0] in ENTIDADES_POR_MARCA.get(chave_marca(marca), ()) and k[1] in FILIAIS_SP


def _vigencia_resumo(vigs: list[Vigencia]) -> Vigencia:
    """Uma vigencia por resolucao: a mais antiga origem declarada, carry-forward se algum carregou."""
    v0 = min(vigs, key=lambda v: v.competencia_origem)
    return Vigencia("ok", v0.cmp, v0.competencia_origem, v0.competencia_usada,
                    any(v.carry_forward for v in vigs), any(v.fotografia for v in vigs))


def resolver_codigo(ind: Indice, codigos: dict, comp: date, produto: Optional[str], marca: str,
                    g_venda: set) -> Resolucao:
    """Via 1: codigo exato no escopo da marca, com a vigencia da alternativa 3."""
    vistos: dict = defaultdict(set)
    for metodo, cod in codigos.items():
        cod = chave_sku(cod)
        for ent, fil in ind.por_sku.get(cod, ()) if cod else ():
            if _no_escopo(marca, (ent, fil, cod)):
                vistos[(ent, fil, cod)].add(metodo)
    validos, descartados, conflito_fonte = [], 0, False
    for k, metodos in sorted(vistos.items()):
        vg = cmp_vigente(ind, k, comp)
        if vg.estado == "conflito":
            conflito_fonte = True
            continue
        if vg.estado != "ok":
            continue
        ev_gtin = comparar(g_venda, gtins_da_chave(ind, k))
        tams = {comparar_tamanho(produto, d) for d in ind.descricoes.get(k, set())} or {INDETERMINADO}
        ev_tam = DIVERGE if DIVERGE in tams else (CONCORDA if CONCORDA in tams else INDETERMINADO)
        if DIVERGE in (ev_gtin, ev_tam):
            descartados += 1
            continue
        validos.append((k, metodos, vg, ev_gtin, ev_tam))
    cands = tuple((k[0], k[2], vg.cmp, vg.competencia_origem) for k, _, vg, _, _ in validos)
    if conflito_fonte:
        return Resolucao("conflito", descartados=descartados, motivo="cmp_conflitante_na_fonte", candidatos=cands)
    if not validos:
        return Resolucao("evidencia_diverge" if descartados else "sem_cmp", descartados=descartados,
                         motivo="candidato_codigo_descartado" if descartados else "sem_candidato_codigo")
    metodos = tuple(sorted({m for _, ms, _, _, _ in validos for m in ms}))
    entidades = tuple(sorted({k[0] for k, *_ in validos}))
    if len({vg.cmp for _, _, vg, _, _ in validos}) > 1:
        return Resolucao("conflito", metodos=metodos, entidades=entidades, descartados=descartados,
                         motivo="candidatos_validos_com_cmp_diferente", candidatos=cands)
    vig = _vigencia_resumo([vg for _, _, vg, _, _ in validos])
    if any(eg == CONCORDA for *_, eg, _ in validos):
        nivel = NIVEL_GTIN
    elif any(et == CONCORDA for *_, et in validos):
        nivel = NIVEL_VOLUME
    elif len(validos) == 1:
        nivel = NIVEL_PROVISORIO
    else:
        return Resolucao("ambiguo", metodos=metodos, entidades=entidades, descartados=descartados,
                         motivo="mais_de_um_candidato_sem_evidencia", candidatos=cands)
    return Resolucao("coberto", vig.cmp, nivel, metodos, entidades, descartados, vigencia=vig)


def resolver_gtin(ind: Indice, idx_gtin: dict, comp: date, produto: Optional[str], marca: str,
                  g_venda: set) -> tuple[Optional[Resolucao], str]:
    """Via 2: GTIN. Devolve (resolucao, '') ou (None, motivo da recusa)."""
    if not g_venda:
        return None, "gtin_ausente_na_venda"
    if len(g_venda) > 1:
        return None, "gtin_venda_ambiguo"
    g = next(iter(g_venda))
    todas = idx_gtin.get(g, set())
    if gtin_reutilizado(ind, todas):
        return None, "gtin_reutilizado_volume_incompativel"
    cands = [k for k in sorted(todas) if _no_escopo(marca, k)]
    if not cands:
        return None, "gtin_sem_candidato_no_escopo"
    vigs = [(k, cmp_vigente(ind, k, comp)) for k in cands]
    if any(vg.estado == "conflito" for _, vg in vigs):
        return None, "gtin_candidato_em_conflito"
    oks = [(k, vg) for k, vg in vigs if vg.estado == "ok"]
    if not oks:
        return None, "gtin_sem_cmp_vigente"
    if len(oks) > 1:
        return None, "gtin_ambiguo"
    k, vg = oks[0]
    vol = comparar(tamanhos(produto), volumes_da_chave(ind, k))
    if vol != CONCORDA:
        return None, "gtin_volume_divergente" if vol == DIVERGE else "gtin_sem_volume_para_afastar_reuso"
    return Resolucao("coberto", vg.cmp, NIVEL_VIA_GTIN, ("gtin",), (k[0],), vigencia=vg), ""


def origem_a_confirmar(marca: str, codigos: dict) -> bool:
    m = chave_marca(marca)
    return any((m, chave_sku(c)) in ORIGEM_A_CONFIRMAR for c in codigos.values())


def resolver(ind: Indice, codigos: dict, comp: date, produto: Optional[str], cat: Optional[Catalogo] = None,
             marca: str = "", idx_gtin: Optional[dict] = None) -> Resolucao:
    """CMP de uma venda, ou o motivo de nao haver. Codigo exato tem precedencia sobre GTIN."""
    if origem_a_confirmar(marca, codigos):
        return Resolucao("origem_a_confirmar", motivo="origem_empresarial_nao_confirmada")
    g_venda = gtins_da_venda(cat, marca, produto, codigos)
    r = resolver_codigo(ind, codigos, comp, produto, marca, g_venda)
    # A via GTIN so' entra quando o codigo nao achou candidato valido. Conflito
    # e ambiguidade do codigo nunca sao resolvidos por ela.
    if r.status not in ("sem_cmp", "evidencia_diverge") or idx_gtin is None:
        return r
    via, motivo = resolver_gtin(ind, idx_gtin, comp, produto, marca, g_venda)
    if via is not None:
        via.descartados = r.descartados
        return via
    r.motivo = f"{r.motivo};{motivo}"
    return r


def avaliar_kit(ind: Indice, cat: Catalogo, marca: str, codigos: dict, comp: date) -> dict:
    """Mede as duas leituras possiveis de um kit sem escolher nenhuma."""
    m = chave_marca(marca)
    proprio = resolver(ind, codigos, comp, None, cat, marca)
    chaves = {chave_sku(c) for c in codigos.values()} - {""}
    for c in list(chaves):
        chaves |= cat.alias.get((m, c), set())
    comps = next((cat.componentes[(m, k)] for k in sorted(chaves) if (m, k) in cat.componentes), [])
    com_cmp = sum(resolver(ind, {"componente": cod}, comp, None, cat, marca).status == "coberto"
                  for cod, _qtd in comps)
    return {
        "cmp_proprio": proprio.status == "coberto",
        "composicao_disponivel": bool(comps),
        "componentes": len(comps),
        "componentes_com_cmp": com_cmp,
        "soma_componentes_calculavel": (bool(comps) and com_cmp == len(comps)
                                        and all(q is not None for _, q in comps)),
    }


# ---------------------------------------------------------------------------
# Analise
# ---------------------------------------------------------------------------

def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _pct(num: Decimal, den: Decimal) -> Optional[Decimal]:
    return (Decimal(100) * num / den).quantize(Decimal("0.01")) if den else None


STATUS_NAO_COMPARAVEL = ("conflito", "ambiguo", "evidencia_diverge", "kit_sem_regra", "fora_do_shadow_operacao")


def analisar(ind: Indice, cat: Catalogo, vendas: list[dict]) -> dict:
    """Cobertura por nivel, carry-forward, kits e shadow por competencia x canal x marca."""
    idx_gtin = indice_gtin(ind)
    celulas: dict = defaultdict(lambda: defaultdict(Decimal))
    contagem: dict = defaultdict(Counter)
    kits, tipo_status, operacoes, motivos = Counter(), Counter(), Counter(), Counter()
    skus_nivel: dict = defaultdict(set)
    detalhe = []

    for v in vendas:
        operacoes[v["tipo_operacao"]] += 1
        comp, plat, marca = v["competencia"], v["plataforma"], v["marca"]
        codigos = {"codigo_sku": v["codigo_sku"], "sku_canonico": v["sku_canonico"]}
        tipo = classificar_tipo(cat, marca, [chave_sku(c) for c in codigos.values() if chave_sku(c)])
        qtd, rec, cf = _d(v["quantidade"]), _d(v["receita"]), v["custo_fiscal"]
        sku_id = (marca, chave_sku(v["codigo_sku"]), chave_sku(v["sku_canonico"]))
        cel = celulas[(comp, plat, marca)]
        cel["quantidade"] += qtd
        cel["receita"] += rec
        if v["tipo_operacao"] != "normal":
            r = Resolucao("fora_do_shadow_operacao")
        elif tipo == "kit":
            k = avaliar_kit(ind, cat, marca, codigos, comp)
            kits["kits_vendidos"] += 1
            kits["com_cmp_proprio"] += k["cmp_proprio"]
            kits["com_composicao"] += k["composicao_disponivel"]
            kits["soma_componentes_calculavel"] += k["soma_componentes_calculavel"]
            kits["ambas_leituras_disponiveis"] += k["cmp_proprio"] and k["soma_componentes_calculavel"]
            kits["nenhuma_leitura"] += not k["cmp_proprio"] and not k["soma_componentes_calculavel"]
            r = Resolucao("kit_sem_regra")
        else:
            r = resolver(ind, codigos, comp, v["produto"], cat, marca, idx_gtin)
        status = r.status
        tipo_status[(comp.isoformat(), tipo, status)] += 1
        contagem[(comp, plat, marca)][status] += 1
        skus_nivel[r.nivel or status].add(sku_id)
        cel[f"receita_{status}"] += rec
        cel[f"quantidade_{status}"] += qtd
        if r.motivo and status != "coberto":
            for m in r.motivo.split(";"):
                motivos[(status, m)] += 1
        if status == "coberto":
            custo = qtd * r.cmp
            cel["custo_cmp"] += custo
            cel[f"receita_{r.nivel}"] += rec
            cel[f"custo_{r.nivel}"] += custo
            cel["custo_fiscal_fonte_nas_linhas_cobertas"] += _d(cf)
            if r.vigencia.carry_forward:
                cel["receita_carry_forward"] += rec
                if r.vigencia.fotografia:
                    cel["receita_fotografia_carregada"] += rec
        detalhe.append({
            "competencia": comp.isoformat(), "marca": marca, "plataforma": plat, "tipo_operacao": v["tipo_operacao"],
            "codigo_sku": v["codigo_sku"], "sku_canonico": v["sku_canonico"], "produto": v["produto"],
            "tipo": tipo, "status": status, "nivel": r.nivel, "motivo": r.motivo, "descartados": r.descartados,
            "metodos": "|".join(r.metodos), "entidades": "|".join(r.entidades),
            "competencia_origem": r.vigencia.competencia_origem.isoformat() if r.vigencia else "",
            "competencia_usada": comp.isoformat() if r.vigencia else "",
            "carry_forward": r.vigencia.carry_forward if r.vigencia else "",
            "fotografia": r.vigencia.fotografia if r.vigencia else "",
            "candidatos": "|".join(f"{e}:{s}:{c}:{o:%Y-%m}" for e, s, c, o in r.candidatos),
            "quantidade": str(qtd), "receita": str(rec),
            "cmp": str(r.cmp) if r.cmp is not None else "",
            "custo_cmp": str(qtd * r.cmp) if r.cmp is not None else "",
            "custo_fiscal_fonte": "" if cf is None else str(cf),
        })

    shadow = []
    for (comp, plat, marca), cel in sorted(celulas.items()):
        tot = cel["receita"]
        rec_cob = cel["receita_coberto"]
        rec_gtin = sum((cel[f"receita_{n}"] for n in NIVEIS_GTIN), Decimal(0))
        custo_gtin = sum((cel[f"custo_{n}"] for n in NIVEIS_GTIN), Decimal(0))
        linha = {
            "competencia": comp.isoformat(), "plataforma": plat, "marca": marca,
            "atribuicao_canal": ATRIBUICAO_CANAL.get(plat, ""),
            "quantidade_total": cel["quantidade"], "receita_fiscal_total": tot,
            "quantidade_coberta": cel["quantidade_coberto"], "receita_coberta": rec_cob,
            "custo_cmp_coberto": cel["custo_cmp"],
            "margem_bruta_apos_cmp": rec_cob - cel["custo_cmp"],
            "pct_margem_bruta_apos_cmp": _pct(rec_cob - cel["custo_cmp"], rec_cob),
            # Confirmado por GTIN (codigo+GTIN e via GTIN); o provisorio nunca entra aqui.
            "receita_confirmada_gtin": rec_gtin, "custo_cmp_confirmado_gtin": custo_gtin,
            "margem_bruta_apos_cmp_confirmada_gtin": rec_gtin - custo_gtin,
            "pct_margem_bruta_apos_cmp_confirmada_gtin": _pct(rec_gtin - custo_gtin, rec_gtin),
            "pct_receita_coberta": _pct(rec_cob, tot),
            "pct_receita_carry_forward": _pct(cel["receita_carry_forward"], tot),
            "pct_receita_fotografia_carregada": _pct(cel["receita_fotografia_carregada"], tot),
            "pct_receita_sem_cmp": _pct(cel["receita_sem_cmp"], tot),
            "pct_receita_origem_a_confirmar": _pct(cel["receita_origem_a_confirmar"], tot),
            "custo_fiscal_fonte_nas_linhas_cobertas": cel["custo_fiscal_fonte_nas_linhas_cobertas"],
            "componentes_receita": {k: val for k, val in cel.items() if k.startswith("receita_")},
            "skus_por_status": dict(contagem[(comp, plat, marca)]),
            "custos_variaveis_ausentes_por_sku": CUSTOS_AUSENTES_POR_SKU.get(plat, []),
        }
        for n in NIVEIS:
            linha[f"pct_receita_{n}"] = _pct(cel[f"receita_{n}"], tot)
        for s in STATUS_NAO_COMPARAVEL:
            linha[f"pct_receita_{s}"] = _pct(cel[f"receita_{s}"], tot)
        shadow.append(linha)
    return {
        "shadow": shadow,
        "detalhe": detalhe,
        "kits": dict(kits),
        "tipo_x_status": {"|".join(k): n for k, n in sorted(tipo_status.items())},
        "tipo_operacao": dict(operacoes),
        "motivos_sem_casamento": {"|".join(k): n for k, n in sorted(motivos.items())},
        "skus_distintos_por_nivel_ou_status": {k: len(s) for k, s in sorted(skus_nivel.items())},
    }


CAMPOS_PUBLICOS = ("competencia", "plataforma", "marca", "pct_receita_coberta",
                   *(f"pct_receita_{n}" for n in NIVEIS), "pct_receita_carry_forward",
                   "pct_receita_fotografia_carregada", "pct_receita_sem_cmp", "pct_receita_origem_a_confirmar",
                   *(f"pct_receita_{s}" for s in STATUS_NAO_COMPARAVEL), "skus_por_status")


def resumo_publico(analise: dict) -> dict:
    """Percentuais e contagens. Sem R$, sem CMP, sem margem."""
    return {"cobertura": [{k: s[k] for k in CAMPOS_PUBLICOS} for s in analise["shadow"]],
            "kits": analise["kits"],
            "skus_distintos_por_nivel_ou_status": analise["skus_distintos_por_nivel_ou_status"],
            "motivos_sem_casamento": analise["motivos_sem_casamento"]}


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _engine_padrao():
    from sqlalchemy import create_engine

    from pipelines.common.config import settings
    from pipelines.dre.insumo_publisher import url_psycopg2

    if not settings.datamart_url:
        raise TorreIndisponivel("Data Mart nao configurado")
    return create_engine(url_psycopg2(settings.datamart_url), pool_pre_ping=True, future=True)


def ler_torre(engine, de: date, ate: date) -> tuple[list[dict], list[dict], list[dict]]:
    """Vendas, cadastro e composicao de kits, numa transacao READ ONLY."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from pipelines.dre.insumo_publisher import sanitizar

    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '600s'"))
            p = {"plataformas": list(PLATAFORMAS), "de": de, "ate": ate}
            vendas = [dict(r) for r in conn.execute(text(SQL_VENDAS), p).mappings().all()]
            cadastro = [dict(r) for r in conn.execute(text(SQL_CADASTRO)).mappings().all()]
            kits = [dict(r) for r in conn.execute(text(SQL_KITS)).mappings().all()]
            conn.rollback()
    except SQLAlchemyError as exc:
        raise TorreIndisponivel(sanitizar(exc)) from None
    return vendas, cadastro, kits


def executar(ind: Indice, grade: list[date], *, engine=None) -> dict:
    engine = engine or _engine_padrao()
    vendas, cadastro, kits = ler_torre(engine, grade[0], grade[-1])
    if not vendas:
        raise TorreIndisponivel("nenhuma venda na janela pedida")
    analise = analisar(ind, montar_catalogo(cadastro, kits), vendas)
    analise["publico"] = resumo_publico(analise)
    return analise


def escrever_detalhes(pasta: Path, analise: dict) -> None:
    det = analise.get("detalhe", [])
    if not det:
        return
    with open(pasta / "vendas_classificadas.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(det[0]))
        w.writeheader()
        w.writerows(det)
    # Produtos que precisam de de-para temporal: dois candidatos validos com CMP diferente.
    conflitos: dict = {}
    for r in det:
        if r["status"] == "conflito":
            k = (r["marca"], r["codigo_sku"], r["sku_canonico"])
            c = conflitos.setdefault(k, {"produto": r["produto"], "competencias": set(), "receita": Decimal(0),
                                         "candidatos": set()})
            c["competencias"].add(r["competencia"])
            c["receita"] += Decimal(r["receita"])
            c["candidatos"].add(r["candidatos"])
    with open(pasta / "depara_temporal_necessario.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["marca", "codigo_sku", "sku_canonico", "produto", "competencias", "receita", "candidatos"])
        for (m, cs, sc), c in sorted(conflitos.items(), key=lambda x: -x[1]["receita"]):
            w.writerow([m, cs, sc, c["produto"], ",".join(sorted(c["competencias"])), c["receita"],
                        " || ".join(sorted(x for x in c["candidatos"] if x))])
