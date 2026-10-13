"""DRE-CMP-SKU-1 — cobertura do CMP sobre as vendas da Torre e shadow de margem por produto.

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

CASAMENTO
---------
So' por igualdade exata de codigo (`codigo_sku` ou `sku_canonico` da venda
contra o `Cod. Proprio`/`Produto` da planilha). Cada via e' registrada.

Codigo igual NAO prova produto igual: os codigos numericos sao de ERPs
diferentes e colidem entre marcas (medido: `10005` de uma venda Lescent e' um
condicionador Barbours na BB VAREJO). Por isso cada candidato passa por duas
evidencias independentes:

  * GTIN — EAN-13 valido da venda (prefixo do nome ou cadastro) contra o GTIN
    da planilha (coluna ou prefixo da descricao);
  * volume declarado (ml, g, caps...) dos dois nomes.

Candidato com evidencia DIVERGENTE e' descartado. Os demais recebem o nivel
da evidencia mais forte que concorda: `GTIN` (forte), `VOLUME` (fraca: linhas
inteiras de produto tem o mesmo volume) ou `INDETERMINADO`. Se os candidatos
restantes devolvem CMP diferentes, o SKU fica `conflito` — nenhuma precedencia
e' escolhida. O shadow sai em dois niveis: so' GTIN, e todos os aceitos.

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

from pipelines.dre.cmp_sku import ALTERNATIVAS, Indice, chave_sku, cmp_alternativa

PLATAFORMAS = ("tiktokshop", "mercadolivre", "shopee")
#: Alternativa temporal usada no shadow. Nao e' decisao: as tres sao medidas e
#: a diferenca entre elas aparece no relatorio.
ALTERNATIVA_SHADOW = "por_competencia"

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
EVID_GTIN, EVID_VOLUME = "GTIN", "VOLUME"
#: Forca da evidencia: a resolucao herda a mais forte entre os candidatos aceitos.
FORCA = {EVID_GTIN: 2, EVID_VOLUME: 1, INDETERMINADO: 0}


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


# ---------------------------------------------------------------------------
# Catalogo e resolucao
# ---------------------------------------------------------------------------

def chave_marca(valor: Optional[str]) -> str:
    """`Rituária` -> `rituaria`, `By Samia` -> `bysamia`: o grao de marca da Torre."""
    sem_acento = unicodedata.normalize("NFKD", valor or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


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


@dataclass
class Resolucao:
    status: str                       # coberto | sem_cmp | conflito | evidencia_diverge | kit_sem_regra
    cmp: Optional[Decimal] = None
    metodos: tuple = ()
    entidades: tuple = ()
    evidencia: str = ""               # GTIN | VOLUME | INDETERMINADO (so' em `coberto`)
    descartados: int = 0              # candidatos descartados por evidencia divergente
    candidatos_por_alias: bool = False


def gtins_da_venda(cat: Optional[Catalogo], marca: str, produto: Optional[str], codigos: dict) -> set:
    g = gtins(produto)
    if cat is not None:
        for cod in codigos.values():
            g |= cat.gtin.get((chave_marca(marca), chave_sku(cod)), set())
    return g


def resolver(ind: Indice, codigos: dict, comp: date, alternativa: str, produto: Optional[str],
             cat: Optional[Catalogo] = None, marca: str = "") -> Resolucao:
    """CMP inequivoco de uma venda, ou o motivo de nao haver."""
    g_venda = gtins_da_venda(cat, marca, produto, codigos)
    aceitos, descartados, conflito_fonte = [], 0, False
    for metodo, cod in codigos.items():
        cod = chave_sku(cod)
        if not cod:
            continue
        for ent, fil in sorted(ind.por_sku.get(cod, ())):
            k = (ent, fil, cod)
            estado, valor = cmp_alternativa(ind, k, comp, alternativa)
            if estado == "conflito":
                conflito_fonte = True
            if estado != "ok":
                continue
            descricoes = ind.descricoes.get(k, set())
            ev_gtin = comparar(g_venda, gtins(*descricoes, *ind.gtins.get(k, set())))
            tams = {comparar_tamanho(produto, d) for d in descricoes} or {INDETERMINADO}
            ev_tam = DIVERGE if DIVERGE in tams else (CONCORDA if CONCORDA in tams else INDETERMINADO)
            if DIVERGE in (ev_gtin, ev_tam):
                descartados += 1
                continue
            ev = EVID_GTIN if ev_gtin == CONCORDA else (EVID_VOLUME if ev_tam == CONCORDA else INDETERMINADO)
            aceitos.append((metodo, ent, valor, ev))
    if conflito_fonte:
        return Resolucao("conflito", descartados=descartados)
    if not aceitos:
        if descartados:
            return Resolucao("evidencia_diverge", descartados=descartados)
        alias = False
        if cat is not None:
            for cod in codigos.values():
                for a in cat.alias.get((chave_marca(marca), chave_sku(cod)), ()):
                    if any(cmp_alternativa(ind, (e, f, a), comp, alternativa)[0] == "ok"
                           for e, f in ind.por_sku.get(a, ())):
                        alias = True
        return Resolucao("sem_cmp", candidatos_por_alias=alias)
    valores = {v for _, _, v, _ in aceitos}
    metodos = tuple(sorted({m for m, _, _, _ in aceitos}))
    entidades = tuple(sorted({e for _, e, _, _ in aceitos}))
    if len(valores) > 1:
        return Resolucao("conflito", metodos=metodos, entidades=entidades, descartados=descartados)
    ev = max((e for *_, e in aceitos), key=FORCA.__getitem__)
    return Resolucao("coberto", next(iter(valores)), metodos, entidades, ev, descartados)


def avaliar_kit(ind: Indice, cat: Catalogo, marca: str, codigos: dict, comp: date, alternativa: str) -> dict:
    """Mede as duas leituras possiveis de um kit sem escolher nenhuma."""
    m = chave_marca(marca)
    proprio = resolver(ind, codigos, comp, alternativa, None, cat, marca)
    chaves = {chave_sku(c) for c in codigos.values()} - {""}
    for c in list(chaves):
        chaves |= cat.alias.get((m, c), set())
    comps = next((cat.componentes[(m, k)] for k in sorted(chaves) if (m, k) in cat.componentes), [])
    com_cmp = sum(resolver(ind, {"componente": cod}, comp, alternativa, None, cat, marca).status == "coberto"
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


PARES_ALTERNATIVAS = ("por_competencia_vs_ultimo_disponivel", "por_competencia_vs_entidade_vigencia",
                      "ultimo_disponivel_vs_entidade_vigencia")


def analisar(ind: Indice, cat: Catalogo, vendas: list[dict]) -> dict:
    """Cobertura, kits, alternativas temporais e shadow por competencia x canal x marca."""
    celulas: dict = defaultdict(lambda: defaultdict(Decimal))
    contagem: dict = defaultdict(Counter)
    kits, tipo_status, operacoes = Counter(), Counter(), Counter()
    detalhe = []
    mudam = {p: set() for p in PARES_ALTERNATIVAS}
    por_alt: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(Decimal)))

    for v in vendas:
        operacoes[v["tipo_operacao"]] += 1
        comp, plat, marca = v["competencia"], v["plataforma"], v["marca"]
        codigos = {"codigo_sku": v["codigo_sku"], "sku_canonico": v["sku_canonico"]}
        tipo = classificar_tipo(cat, marca, [chave_sku(c) for c in codigos.values() if chave_sku(c)])
        qtd, rec, cf = _d(v["quantidade"]), _d(v["receita"]), v["custo_fiscal"]
        cel = celulas[(comp, plat, marca)]
        cel["quantidade"] += qtd
        cel["receita"] += rec
        if v["tipo_operacao"] != "normal":
            r = Resolucao("fora_do_shadow_operacao")
        elif tipo == "kit":
            k = avaliar_kit(ind, cat, marca, codigos, comp, ALTERNATIVA_SHADOW)
            kits["kits_vendidos"] += 1
            kits["com_cmp_proprio"] += k["cmp_proprio"]
            kits["com_composicao"] += k["composicao_disponivel"]
            kits["soma_componentes_calculavel"] += k["soma_componentes_calculavel"]
            kits["ambas_leituras_disponiveis"] += k["cmp_proprio"] and k["soma_componentes_calculavel"]
            kits["nenhuma_leitura"] += not k["cmp_proprio"] and not k["soma_componentes_calculavel"]
            r = Resolucao("kit_sem_regra")
        else:
            res = {a: resolver(ind, codigos, comp, a, v["produto"], cat, marca) for a in ALTERNATIVAS}
            sku_id = (comp.isoformat(), marca, chave_sku(v["codigo_sku"]), chave_sku(v["sku_canonico"]))
            for par in mudam:
                a, b = par.split("_vs_")
                if (res[a].status, res[a].cmp) != (res[b].status, res[b].cmp):
                    mudam[par].add(sku_id)
            for a, ra in res.items():
                mes = por_alt[a][comp.isoformat()]
                if ra.status == "coberto":
                    mes["custo"] += qtd * ra.cmp
                    mes["receita_coberta"] += rec
                mes[f"linhas_{ra.status}"] += 1
            r = res[ALTERNATIVA_SHADOW]
        status = r.status
        tipo_status[(comp.isoformat(), tipo, status)] += 1
        contagem[(comp, plat, marca)][status] += 1
        cel[f"receita_{status}"] += rec
        cel[f"quantidade_{status}"] += qtd
        if r.descartados:
            cel["receita_com_candidato_descartado_por_evidencia"] += rec
        if status == "coberto":
            cel["custo_cmp"] += qtd * r.cmp
            cel["custo_fiscal_fonte_nas_linhas_cobertas"] += _d(cf)
            cel[f"receita_evidencia_{r.evidencia}"] += rec
            if r.evidencia == EVID_GTIN:
                cel["custo_cmp_gtin"] += qtd * r.cmp
            for m in r.metodos:
                cel[f"receita_via_{m}"] += rec
            if len(r.entidades) > 1:
                cel["receita_cmp_igual_em_mais_de_uma_entidade"] += rec
        if status == "sem_cmp" and r.candidatos_por_alias:
            cel["receita_sem_cmp_mas_alias_do_cadastro_tem_cmp"] += rec
        detalhe.append({
            "competencia": comp.isoformat(), "marca": marca, "plataforma": plat, "tipo_operacao": v["tipo_operacao"],
            "codigo_sku": v["codigo_sku"], "sku_canonico": v["sku_canonico"], "produto": v["produto"],
            "tipo": tipo, "status": status, "evidencia": r.evidencia, "descartados": r.descartados,
            "metodos": "|".join(r.metodos), "entidades": "|".join(r.entidades),
            "quantidade": str(qtd), "receita": str(rec),
            "cmp": str(r.cmp) if r.cmp is not None else "",
            "custo_cmp": str(qtd * r.cmp) if r.cmp is not None else "",
            "custo_fiscal_fonte": "" if cf is None else str(cf),
        })

    shadow = []
    for (comp, plat, marca), cel in sorted(celulas.items()):
        rec_cob, rec_gtin = cel["receita_coberto"], cel[f"receita_evidencia_{EVID_GTIN}"]
        nao_comp = (cel["receita_kit_sem_regra"] + cel["receita_conflito"] + cel["receita_evidencia_diverge"]
                    + cel["receita_fora_do_shadow_operacao"])
        shadow.append({
            "competencia": comp.isoformat(), "plataforma": plat, "marca": marca,
            "atribuicao_canal": ATRIBUICAO_CANAL.get(plat, ""),
            "quantidade_total": cel["quantidade"], "receita_fiscal_total": cel["receita"],
            # Nivel 1: so' casamentos confirmados por GTIN.
            "receita_coberta_gtin": rec_gtin,
            "custo_cmp_gtin": cel["custo_cmp_gtin"],
            "margem_bruta_apos_cmp_gtin": rec_gtin - cel["custo_cmp_gtin"],
            "pct_margem_bruta_apos_cmp_gtin": _pct(rec_gtin - cel["custo_cmp_gtin"], rec_gtin),
            "pct_receita_coberta_gtin": _pct(rec_gtin, cel["receita"]),
            # Nivel 2: todos os aceitos (GTIN + VOLUME + INDETERMINADO).
            "quantidade_coberta": cel["quantidade_coberto"], "receita_coberta": rec_cob,
            "custo_cmp_coberto": cel["custo_cmp"],
            "margem_bruta_apos_cmp": rec_cob - cel["custo_cmp"],
            "pct_margem_bruta_apos_cmp": _pct(rec_cob - cel["custo_cmp"], rec_cob),
            "pct_receita_coberta": _pct(rec_cob, cel["receita"]),
            "pct_receita_sem_cmp": _pct(cel["receita_sem_cmp"], cel["receita"]),
            "pct_receita_nao_comparavel": _pct(nao_comp, cel["receita"]),
            "custo_fiscal_fonte_nas_linhas_cobertas": cel["custo_fiscal_fonte_nas_linhas_cobertas"],
            "componentes_receita": {k: v for k, v in cel.items() if k.startswith("receita_")},
            "skus_por_status": dict(contagem[(comp, plat, marca)]),
            "custos_variaveis_ausentes_por_sku": CUSTOS_AUSENTES_POR_SKU.get(plat, []),
        })
    return {
        "shadow": shadow,
        "detalhe": detalhe,
        "kits": dict(kits),
        "tipo_x_status": {"|".join(k): n for k, n in sorted(tipo_status.items())},
        "tipo_operacao": dict(operacoes),
        "alternativas": {
            "competencia_x_sku_que_mudam": {k: len(s) for k, s in mudam.items()},
            "por_alternativa_e_mes": {a: {m: dict(x) for m, x in sorted(meses.items())}
                                      for a, meses in por_alt.items()},
        },
    }


def resumo_publico(analise: dict) -> dict:
    """Percentuais e contagens. Sem R$, sem CMP, sem margem."""
    campos = ("competencia", "plataforma", "marca", "pct_receita_coberta_gtin", "pct_receita_coberta",
              "pct_receita_sem_cmp", "pct_receita_nao_comparavel", "skus_por_status")
    return {"cobertura": [{k: s[k] for k in campos} for s in analise["shadow"]],
            "kits": analise["kits"],
            "alternativas_competencia_x_sku_que_mudam": analise["alternativas"]["competencia_x_sku_que_mudam"]}


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
