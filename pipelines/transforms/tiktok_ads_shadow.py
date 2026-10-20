"""TikTok Ads -> shadow diario por (dia, marca, escopo). Logica pura, sem I/O.

Entrada: `raw.tiktok_ads_campaign_daily` e `raw.tiktok_ads_coverage`
publicadas pela DAG `tiktok_ads_campaign_daily` do airflow (inerte; DDL nao
aplicada).

Regras
------
- So' apice e barbours (capability do probe de 20/10/2026). Outras marcas
  nunca aparecem: a jusante sao `sem_dado`, nunca zero.
- `escopo` vem do objetivo da campanha: `shop` (PRODUCT_SALES) e' o UNICO que
  representa Ads do TikTok Shop. `nao_shop` e' midia de site/marca.
- So' o relatorio INTEGRADO (leilao). GMV Max nao tem fonte e nunca e' somado
  aqui. Comissao de afiliado e recarga do repasse nao sao midia.
- Um (dia, marca) so' sai se TODOS os anunciantes da marca na janela foram
  consultados naquele dia. Faltou um -> o dia nao sai (ausencia).
- Dia consultado sem linha num escopo -> `consultado_sem_linha` com metricas
  NULAS: a API nao devolver linha nao prova spend zero.
- Moeda e fuso: so' preenchidos se TODAS as linhas concordam num valor nao
  nulo. Hoje sao desconhecidos (advertiser/info negado) -> NULL.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

MARCAS = ("apice", "barbours")
ESCOPOS = ("shop", "nao_shop", "desconhecido")
MEDIDO, SEM_LINHA = "medido", "consultado_sem_linha"


def _unico(valores):
    vs = {v for v in valores}
    return next(iter(vs)) if len(vs) == 1 and None not in vs else None


def montar_shadow(linhas, cobertura):
    """Linhas do shadow + resumo. `linhas`/`cobertura` sao dicts do raw."""
    adv_por_marca = defaultdict(set)
    consultados = defaultdict(set)                  # (brand, dia) -> anunciantes
    for c in cobertura:
        if c["brand"] not in MARCAS:
            continue
        adv_por_marca[c["brand"]].add(c["advertiser_id"])
        consultados[(c["brand"], c["stat_date"])].add(c["advertiser_id"])

    grupos = defaultdict(list)
    for l in linhas:
        if l["brand"] in MARCAS:
            grupos[(l["stat_date"], l["brand"], l["escopo"])].append(l)

    saida, incompletos = [], 0
    for (brand, dia), advs in sorted(consultados.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if advs != adv_por_marca[brand]:
            incompletos += 1                         # algum anunciante nao consultado
            continue
        for escopo in ESCOPOS:
            g = grupos.get((dia, brand, escopo), [])
            base = {"stat_date": dia, "brand": brand, "escopo": escopo, "anunciantes": len(advs)}
            if not g:
                saida.append(dict(base, estado=SEM_LINHA, spend=None, impressions=None, clicks=None,
                                  onsite_shopping=None, onsite_shopping_value=None, campanhas=None,
                                  moeda=None, fuso=None))
                continue
            saida.append(dict(
                base, estado=MEDIDO,
                spend=sum((Decimal(x["spend"]) for x in g), Decimal("0")),
                impressions=sum(int(x["impressions"]) for x in g),
                clicks=sum(int(x["clicks"]) for x in g),
                onsite_shopping=sum(int(x["onsite_shopping"]) for x in g),
                onsite_shopping_value=sum((Decimal(x["total_onsite_shopping_value"]) for x in g), Decimal("0")),
                campanhas=len({x["campaign_id"] for x in g}),
                moeda=_unico(x.get("moeda") for x in g),
                fuso=_unico(x.get("fuso") for x in g),
            ))
    resumo = {
        "linhas": len(saida),
        "dias_marca_publicaveis": len(saida) // len(ESCOPOS),
        "dias_marca_incompletos": incompletos,
        "por_estado": {e: sum(1 for s in saida if s["estado"] == e) for e in (MEDIDO, SEM_LINHA)},
        "spend_shop_medido": sum((s["spend"] for s in saida if s["estado"] == MEDIDO and s["escopo"] == "shop"),
                                 Decimal("0")),
    }
    return saida, resumo


def roas(valor, spend):
    """ROAS so' com denominador valido: spend medido > 0. Senao None."""
    if valor is None or spend is None or spend <= 0:
        return None
    return (Decimal(valor) / Decimal(spend)).quantize(Decimal("0.01"))
