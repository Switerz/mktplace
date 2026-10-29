"""TikTok Ads -> shadow diario por campanha x dia + cobertura. Logica pura, sem I/O.

Entrada: `raw.tiktok_ads_campaign_daily` e `raw.tiktok_ads_coverage`
publicadas pela DAG `tiktok_ads_campaign_daily` do airflow (inerte; DDL nao
aplicada).

Saida (migration 031)
---------------------
- fato: uma linha por (advertiser_id, campaign_id, stat_date, moeda), so' para
  linhas que a fonte DEVOLVEU. `spend = 0` aqui e' zero MEDIDO.
- cobertura: uma linha por (advertiser_id, stat_date) CONSULTADO, com `linhas`
  = quantas linhas da fato o par tem. `linhas = 0` e' "consultado sem linha":
  a API nao devolver linha nao prova spend zero, e nenhuma metrica e' gerada.
  Par fora da cobertura nao foi consultado: ausencia, nunca zero.

Regras
------
- So' apice e barbours (capability do probe de 20/10/2026). Outras marcas
  nunca aparecem: a jusante sao `sem_dado`, nunca zero.
- `escopo` vem do objetivo da campanha: `shop` (PRODUCT_SALES) e' o UNICO que
  representa Ads do TikTok Shop. `nao_shop` e `desconhecido` ficam separados.
- So' o relatorio INTEGRADO (leilao). GMV Max nao tem fonte e nunca e' somado
  aqui. Comissao de afiliado e recarga do repasse nao sao midia.
- Moeda NULL/vazia vira o marcador 'DESCONHECIDA' (NULL nao deduplica numa
  UNIQUE). Fuso NULL fica NULL. Nunca se assume BRL nem America/Sao_Paulo.
- Fail-closed: chave (anunciante, campanha, dia) repetida na fonte, marca
  divergente entre linha e cobertura, ou escopo fora do dominio levantam
  `FonteInconsistente` -- somar duplicata seria dupla contagem.
- Linha da fonte sem cobertura do par nao e' publicada (contada em
  `linhas_sem_cobertura`): sem prova de consulta completa, nao ha medicao.
- Contagem RAW por par: a cobertura da raw DECLARA `linhas` por (anunciante,
  dia); as linhas da fato raw lidas para o par tem de ser exatamente essa
  quantidade. Declarado > 0 com linhas ausentes ou parciais, declarado 0 com
  linhas presentes, `linhas` nula/negativa ou duas declaracoes diferentes do
  mesmo par levantam `FonteInconsistente`. Sem isso, leitura incompleta viraria
  "consultado sem linha" e o DELETE do par apagaria a foto anterior.
- Completude por marca (todos os anunciantes da marca consultados no dia) e'
  regra de SERVING, nao do shadow; o resumo so' informa os dias incompletos.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

MARCAS = ("apice", "barbours")
ESCOPOS = ("shop", "nao_shop", "desconhecido")
MOEDA_DESCONHECIDA = "DESCONHECIDA"


class FonteInconsistente(ValueError):
    """A raw contradiz o contrato; publicar seria inventar ou duplicar dado."""


def _moeda(valor):
    v = (valor or "").strip().upper()
    return v or MOEDA_DESCONHECIDA


def _fuso(valor):
    v = (valor or "").strip()
    return v or None


def divergencias_por_par(cobertura, linhas):
    """Pares da `cobertura` cuja `linhas` declarada difere de quantas `linhas` o par tem.

    Lista ordenada de (advertiser_id, stat_date, declarado, contado); vazia = coerente.
    Par ausente de `linhas` conta 0 -- e' exatamente o caso que nao pode passar
    quando a cobertura declara > 0.
    """
    contadas = Counter((str(x["advertiser_id"]), x["stat_date"]) for x in linhas)
    div = []
    for c in cobertura:
        chave = (str(c["advertiser_id"]), c["stat_date"])
        if c["linhas"] != contadas.get(chave, 0):
            div.append((chave[0], chave[1], c["linhas"], contadas.get(chave, 0)))
    return sorted(div, key=lambda d: (d[1], d[0]))


def _linhas_declaradas(c):
    n = c.get("linhas")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise FonteInconsistente(f"cobertura sem contagem valida: {c['advertiser_id']} {c['stat_date']} "
                                 f"linhas={n!r}")
    return n


def montar_shadow(linhas, cobertura):
    """(fato, cobertura, resumo). `linhas`/`cobertura` sao dicts do raw."""
    pares, declarado = {}, {}                         # (adv, dia) -> brand / linhas declaradas
    for c in cobertura:
        if c["brand"] not in MARCAS:
            continue
        chave = (str(c["advertiser_id"]), c["stat_date"])
        if pares.setdefault(chave, c["brand"]) != c["brand"]:
            raise FonteInconsistente(f"anunciante {chave[0]} em duas marcas no dia {chave[1]}")
        n = _linhas_declaradas(c)
        if declarado.setdefault(chave, n) != n:
            raise FonteInconsistente(f"cobertura declara duas contagens para {chave[0]} {chave[1]}: "
                                     f"{declarado[chave]} e {n}")

    fato, vistos, sem_cobertura = [], set(), 0
    for l in linhas:
        if l["brand"] not in MARCAS:
            continue
        adv, camp, dia = str(l["advertiser_id"]), str(l["campaign_id"]), l["stat_date"]
        if l["escopo"] not in ESCOPOS:
            raise FonteInconsistente(f"escopo fora do dominio: {l['escopo']!r}")
        if (adv, dia) not in pares:
            sem_cobertura += 1                         # sem prova de consulta: nao publica
            continue
        if pares[(adv, dia)] != l["brand"]:
            raise FonteInconsistente(f"marca da linha diverge da cobertura: {adv} {dia}")
        if (adv, camp, dia) in vistos:
            raise FonteInconsistente(f"chave repetida na fonte: {adv} {camp} {dia}")
        vistos.add((adv, camp, dia))
        fato.append({
            "stat_date": dia, "brand": l["brand"], "advertiser_id": adv, "campaign_id": camp,
            "escopo": l["escopo"], "moeda": _moeda(l.get("moeda")), "fuso": _fuso(l.get("fuso")),
            "spend": Decimal(l["spend"]),
            "impressions": int(l["impressions"]),
            "clicks": int(l["clicks"]),
            "onsite_shopping": int(l["onsite_shopping"]),
            "onsite_shopping_value": Decimal(l["total_onsite_shopping_value"]),
        })

    # Toda linha raw de par coberto virou exatamente uma linha da fato (ou a
    # leitura ja' foi recusada acima); logo a fato do par e' a contagem RAW lida.
    div = divergencias_por_par(
        [{"advertiser_id": adv, "stat_date": dia, "linhas": n} for (adv, dia), n in declarado.items()], fato)
    if div:
        exemplos = "; ".join(f"{a} {d} declarado={n} lido={m}" for a, d, n, m in div[:5])
        raise FonteInconsistente(f"contagem RAW por par diverge da cobertura em {len(div)} par(es): {exemplos}")
    cob = [{"stat_date": dia, "brand": brand, "advertiser_id": adv, "linhas": declarado[(adv, dia)]}
           for (adv, dia), brand in sorted(pares.items(), key=lambda kv: (kv[0][1], kv[0][0]))]

    advs_marca, advs_dia = defaultdict(set), defaultdict(set)
    for (adv, dia), brand in pares.items():
        advs_marca[brand].add(adv)
        advs_dia[(brand, dia)].add(adv)
    resumo = {
        "linhas_fato": len(fato),
        "pares_consultados": len(cob),
        "pares_consultados_sem_linha": sum(1 for c in cob if c["linhas"] == 0),
        "linhas_sem_cobertura": sem_cobertura,
        "dias_marca_incompletos": sum(1 for (b, _), a in advs_dia.items() if a != advs_marca[b]),
        "moedas": sorted({f["moeda"] for f in fato}),
        "spend_medido_por_escopo": {e: sum((f["spend"] for f in fato if f["escopo"] == e), Decimal("0"))
                                    for e in ESCOPOS},
    }
    return fato, cob, resumo


def roas(valor, spend):
    """ROAS so' com denominador valido: spend medido > 0. Senao None."""
    if valor is None or spend is None or spend <= 0:
        return None
    return (Decimal(valor) / Decimal(spend)).quantize(Decimal("0.01"))
