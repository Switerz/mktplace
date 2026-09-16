"""Desempenho FBS da Shopee — Gate FULL-SH-1C. ESTRITAMENTE READ-ONLY.

Le SOMENTE `marts.fact_shopee_fbs_daily`. Nenhuma consulta ao Data Mart,
a `silver`, a `raw` ou a API da Shopee acontece durante o request.

REGRA DE AGREGACAO, E O MOTIVO
-------------------------------
Medidas aditivas sao SOMADAS; razoes sao calculadas DEPOIS, sobre as somas.

    share      = medida(fbs) / medida(fbs + seller)
    cancel     = cancelled_orders / created_orders
    handling   = SUM(handling_seconds_sum) / SUM(handling_sample_count)

Media de shares diarios daria o mesmo peso a um domingo de R$ 300 e a uma
Black Friday de R$ 300.000. Media de medias de handling ignoraria que um dia
teve 3 pedidos e outro teve 3.000. Nenhuma razao e' calculada por dia e depois
promediada -- nem aqui, nem no `daily`, que so' publica medidas aditivas.

ZERO x AUSENCIA
---------------
    denominador > 0, numerador ausente -> 0.0   (mediu e deu zero)
    denominador = 0                    -> None  (nada a dividir)

SEGURANCA
---------
Allowlist explicita de colunas; nunca `SELECT *`. Todo filtro e' parametrizado
(`= ANY(:brands)`), nenhum valor e' interpolado no texto do SQL. A fato e'
agregada e nao tem coluna de pessoa -- e a allowlist garante que continue
assim mesmo se alguem adicionar uma.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

FACT_TABLE = "marts.fact_shopee_fbs_daily"

#: Contas da esteira API. ALLOWLIST: ausencia de filtro significa exatamente
#: estas quatro, nunca "tudo que estiver na tabela".
EXPECTED_ACCOUNTS = ("apice", "barbours", "lescent", "rituaria")

#: Marcas COBERTAS pela esteira API. Hoje coincidem 1:1 com as contas, mas sao
#: dominios DIFERENTES -- uma marca poderia ganhar uma segunda loja sem que a
#: lista de contas mudasse. Validar `brands` contra a lista de contas
#: funcionaria por acidente, e deixaria de funcionar no dia da divergencia.
EXPECTED_BRANDS = ("apice", "barbours", "lescent", "rituaria")

#: Marcas Shopee que NAO estao na esteira API. Declaradas para que a ausencia
#: viaje com o payload em vez de virar silencio.
BRANDS_NOT_COVERED = ("kokeshi",)

#: Dominio FECHADO. Classe fora disto e' contrato quebrado.
VALID_CLASSES = ("fbs", "seller")

SCOPE_LABEL = "Shopee — cobertura API"

#: A carga e' MANUAL e nao ha DAG. Constantes, e nao literais espalhados, para
#: que o dia em que isso mudar tenha UM lugar para mudar.
LOAD_MODE = "manual_snapshot"
NO_AUTOMATION = True

#: Limiares de frescor. A carga e' MANUAL: 48h e' generoso de proposito, e
#: ainda assim a limitacao viaja no payload.
MAX_SNAPSHOT_AGE_HOURS = 48.0
MAX_SOURCE_AGE_HOURS = 48.0

GMV_DEFINITION = (
    "GMV bruto de pedidos nao cancelados: SUM(item_total) dos itens de pedidos "
    "com order_status <> 'cancelled'. INCLUI to_return e unpaid, publicados "
    "tambem em coluna propria. NAO e' receita liquida nem realizada."
)

LIMITACAO_COBERTURA = (
    "Cobertura PARCIAL: a esteira API cobre apice, barbours, lescent e "
    "rituaria. Kokeshi NAO esta na API e nao e' suprida por planilha aqui -- "
    "este total e' 'Shopee (cobertura API)', nunca 'Shopee total'."
)
LIMITACAO_CARGA_MANUAL = (
    "Carga MANUAL (load_mode=manual_snapshot). Nao existe DAG nem agenda: o "
    "dado so' avanca quando alguem executa o sync."
)
LIMITACAO_ENTREGA = (
    "handling mede pagamento ate' COLETA (pay_time -> pickup_done_time). A API "
    "da Shopee nao expoe data real de entrega, e esta superficie nao publica "
    "metrica de entrega nem de devolucao."
)
LIMITACAO_D0 = (
    "Politica closed_day: a fato so' publica dias FECHADOS, ate' D-1. D0 nao "
    "e' materializado, projetado nem fabricado."
)
LIMITACAO_SERIE = (
    "Serie disponivel a partir de 2026-01-01. Nao ha historico anterior na "
    "esteira API."
)
LIMITACAO_GMV_BRUTO = (
    "to_return e unpaid estao DENTRO do GMV bruto e aparecem em coluna "
    "propria. Cancelados ficam fora do GMV, mas contam no denominador da taxa "
    "de cancelamento."
)


class ShopeeFbsUnavailable(RuntimeError):
    """Fato indisponivel. Mensagem ja' sanitizada, sem detalhe de driver."""


class ShopeeFbsContractError(RuntimeError):
    """A fonte devolveu algo fora do contrato (ex: classe desconhecida)."""


# ---------------------------------------------------------------------------
# SQL — allowlist de colunas, tudo parametrizado.
# ---------------------------------------------------------------------------

#: `:brands` e `:accounts` chegam como array ou NULL. Nenhum valor e'
#: interpolado: `= ANY(:param)` deixa a adaptacao com o driver.
_FILTRO = """
    WHERE ref_date BETWEEN :date_from AND :date_to
      AND (:brands   IS NULL OR brand        = ANY(:brands))
      AND (:accounts IS NULL OR shop_account = ANY(:accounts))
      AND shop_account = ANY(:allowlist)
"""

_MEDIDAS = """
           SUM(gross_gmv)              AS gross_gmv,
           SUM(gross_units)            AS gross_units,
           SUM(created_orders)         AS created_orders,
           SUM(eligible_orders)        AS eligible_orders,
           SUM(cancelled_orders)       AS cancelled_orders,
           SUM(to_return_orders)       AS to_return_orders,
           SUM(to_return_gmv)          AS to_return_gmv,
           SUM(unpaid_orders)          AS unpaid_orders,
           SUM(unpaid_gmv)             AS unpaid_gmv,
           SUM(handling_seconds_sum)   AS handling_seconds_sum,
           SUM(handling_sample_count)  AS handling_sample_count
"""

SQL_POR_CLASSE = text(f"""
    SELECT fbs_class, {_MEDIDAS}
      FROM {FACT_TABLE}
    {_FILTRO}
     GROUP BY fbs_class
     ORDER BY fbs_class
""")

SQL_POR_MARCA = text(f"""
    SELECT brand, fbs_class, {_MEDIDAS}
      FROM {FACT_TABLE}
    {_FILTRO}
     GROUP BY brand, fbs_class
     ORDER BY brand, fbs_class
""")

SQL_POR_CONTA = text(f"""
    SELECT shop_account, brand, fbs_class, {_MEDIDAS},
           MAX(source_max_ingested_at) AS source_watermark_at
      FROM {FACT_TABLE}
    {_FILTRO}
     GROUP BY shop_account, brand, fbs_class
     ORDER BY shop_account, fbs_class
""")

SQL_DIARIO = text(f"""
    SELECT ref_date, fbs_class,
           SUM(gross_gmv)        AS gross_gmv,
           SUM(gross_units)      AS gross_units,
           SUM(eligible_orders)  AS eligible_orders,
           SUM(created_orders)   AS created_orders,
           SUM(cancelled_orders) AS cancelled_orders
      FROM {FACT_TABLE}
    {_FILTRO}
     GROUP BY ref_date, fbs_class
     ORDER BY ref_date, fbs_class
""")

#: Escopo EFETIVO + watermarks da JANELA consultada.
SQL_ESCOPO = text(f"""
    SELECT MIN(ref_date)                  AS effective_date_from,
           MAX(ref_date)                  AS effective_date_to,
           MAX(ingested_at)               AS refreshed_at,
           MAX(source_max_ingested_at)    AS source_watermark_at,
           COUNT(*)                       AS linhas
      FROM {FACT_TABLE}
    {_FILTRO}
""")

#: Limites da fato INTEIRA -- independem do filtro. `source_max_date` daqui e'
#: o que revela atraso da serie: sob filtro de marca, o maximo da janela diria
#: apenas ate' onde AQUELA marca vendeu.
SQL_LIMITES_FATO = text(f"""
    SELECT MIN(ref_date) AS source_min_date,
           MAX(ref_date) AS source_max_date,
           MAX(ingested_at) AS refreshed_at,
           MAX(source_max_ingested_at) AS source_watermark_at,
           COUNT(*) AS linhas
      FROM {FACT_TABLE}
""")

#: UM SNAPSHOT PARA A RESPOSTA INTEIRA.
#:
#: A resposta e' montada com SETE consultas. No default do PostgreSQL
#: (READ COMMITTED) cada statement enxerga um snapshot NOVO -- e o pipeline
#: publica com DELETE + INSERT da janela numa transacao. Se ela commitar entre
#: a consulta de `by_class` e a de `daily`, a resposta mistura dois instantes:
#: medido em PostgreSQL 16, `totals` devolveu 1500 enquanto `daily` devolveu
#: 3000 na MESMA resposta. A tela mostraria FBS + seller sem fechar o total, e
#: ninguem saberia por que.
#:
#: `REPEATABLE READ` fixa o snapshot na primeira leitura e o mantem ate' o fim
#: da transacao. `READ ONLY` e' defesa em profundidade: mesmo que alguem
#: acrescente uma escrita por engano neste caminho, o banco recusa.
#:
#: O escopo e' a TRANSACAO, nao a sessao nem a aplicacao: nenhuma configuracao
#: global e' alterada, e a proxima transacao volta ao default do projeto.
SQL_SNAPSHOT_COERENTE = text(
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

SQL_CONTAS_OBSERVADAS = text(f"""
    SELECT DISTINCT shop_account, brand
      FROM {FACT_TABLE}
     WHERE ref_date BETWEEN :date_from AND :date_to
       AND (:brands   IS NULL OR brand        = ANY(:brands))
       AND (:accounts IS NULL OR shop_account = ANY(:accounts))
     ORDER BY shop_account
""")


# ---------------------------------------------------------------------------
# Razoes — SEMPRE depois da agregacao
# ---------------------------------------------------------------------------

def _share(num, den) -> Optional[float]:
    """Regra canonica. Denominador zero/ausente -> None; numerador ausente com
    denominador positivo -> 0.0.

    A divisao acontece em Decimal e so' vira float na serializacao, para nao
    introduzir erro binario antes da hora.
    """
    if den is None:
        return None
    d = Decimal(den)
    if d == 0:
        return None
    n = Decimal(0) if num is None else Decimal(num)
    return float(n / d)


def _handling(seconds_sum, sample_count, eligible) -> dict:
    s = int(seconds_sum or 0)
    n = int(sample_count or 0)
    media = (s / n) if n > 0 else None
    return {
        "seconds_avg": media,
        "hours_avg": (media / 3600.0) if media is not None else None,
        "days_avg": (media / 86400.0) if media is not None else None,
        "seconds_sum": s,
        "sample_count": n,
        "coverage_ratio": (n / eligible) if eligible else None,
    }


def _dec(v) -> Decimal:
    """Decimal seguro. `None` vira 0 -- a ausencia de linha de uma classe
    significa zero MEDIDO, nao desconhecido, porque a janela foi consultada."""
    if v is None:
        return Decimal(0)
    d = Decimal(v)
    if not d.is_finite():
        raise ShopeeFbsContractError("valor nao finito na fato")
    return d


def _freshness(limites, agora: datetime, last_closed: date) -> dict:
    """Frescor com TRES relogios. Publicacao recente nao mascara fonte antiga.

    Uma republicacao do mesmo periodo move `refreshed_at` sem mover
    `source_max_date`. Por isso `stale` pode vir de qualquer um dos dois, e
    `closed_days_behind` explicita o atraso da SERIE.
    """
    if not limites or not limites["linhas"]:
        return {"freshness_status": "never_loaded", "source_watermark_at": None,
                "refreshed_at": None, "source_max_date": None,
                "source_min_date": None, "source_age_hours": None,
                "snapshot_age_hours": None, "closed_days_behind": None}

    ref = limites["refreshed_at"]
    wat = limites["source_watermark_at"]
    smax = limites["source_max_date"]

    snap_age = ((agora - ref).total_seconds() / 3600.0) if ref else None
    src_age = ((agora - wat).total_seconds() / 3600.0) if wat else None
    behind = (last_closed - smax).days if smax else None

    if snap_age is None and src_age is None:
        status = "unknown"
    elif ((snap_age is not None and snap_age > MAX_SNAPSHOT_AGE_HOURS)
          or (src_age is not None and src_age > MAX_SOURCE_AGE_HOURS)
          or (behind is not None and behind > 1)):
        # `behind > 1`: um dia de atraso e' a propria politica closed_day; dois
        # ou mais significam que o sync nao rodou.
        status = "stale"
    else:
        status = "fresh"

    return {"freshness_status": status, "source_watermark_at": wat,
            "refreshed_at": ref, "source_max_date": smax,
            "source_min_date": limites["source_min_date"],
            "source_age_hours": src_age, "snapshot_age_hours": snap_age,
            "closed_days_behind": behind,
            # Declarados AQUI, e nao deixados para o default do schema: o
            # contrato e' do servico. Se um dia a carga deixar de ser manual,
            # o lugar de mudar e' este, e nao um default esquecido no Pydantic.
            "load_mode": LOAD_MODE, "no_automation": NO_AUTOMATION}


def _soma(linhas: list, campo: str):
    return sum((r[campo] or 0) for r in linhas)


def get_shopee_fbs_block(
    db: Session,
    date_from: date,
    date_to: date,
    *,
    brands: Optional[list[str]] = None,
    accounts: Optional[list[str]] = None,
    last_closed_date: date,
    agora: Optional[datetime] = None,
) -> dict:
    """Monta a resposta. NENHUMA escrita, nenhuma transacao gravavel."""
    agora = agora or datetime.now(timezone.utc)
    params = {
        "date_from": date_from, "date_to": date_to,
        "brands": brands or None, "accounts": accounts or None,
        "allowlist": list(EXPECTED_ACCOUNTS),
    }

    try:
        # PRIMEIRO comando da transacao: `SET TRANSACTION` so' e' aceito antes
        # de qualquer query. O rollback fecha transacao implicita que tenha
        # sobrado e garante que este seja o inicio -- e' seguro porque este
        # caminho nunca escreve.
        db.rollback()
        db.execute(SQL_SNAPSHOT_COERENTE)

        por_classe = [dict(r) for r in
                      db.execute(SQL_POR_CLASSE, params).mappings().all()]
        por_marca = [dict(r) for r in
                     db.execute(SQL_POR_MARCA, params).mappings().all()]
        por_conta = [dict(r) for r in
                     db.execute(SQL_POR_CONTA, params).mappings().all()]
        diario = [dict(r) for r in
                  db.execute(SQL_DIARIO, params).mappings().all()]
        escopo = db.execute(SQL_ESCOPO, params).mappings().one()
        limites = db.execute(SQL_LIMITES_FATO).mappings().one()
        observadas = [dict(r) for r in
                      db.execute(SQL_CONTAS_OBSERVADAS, params).mappings().all()]
    except Exception as exc:                      # pragma: no cover - driver
        raise ShopeeFbsUnavailable(
            "fato de FBS Shopee indisponivel") from exc

    # --- Dominio FECHADO: classe inesperada falha, nunca vira categoria -----
    for r in por_classe + por_marca + por_conta + diario:
        if r["fbs_class"] not in VALID_CLASSES:
            raise ShopeeFbsContractError(
                "classe de fulfillment fora do dominio autorizado")

    # --- Totais: soma das medidas ADITIVAS ---------------------------------
    gmv_total = sum((_dec(r["gross_gmv"]) for r in por_classe), Decimal(0))
    elig_total = _soma(por_classe, "eligible_orders")
    crea_total = _soma(por_classe, "created_orders")
    unid_total = _soma(por_classe, "gross_units")
    h_sum = _soma(por_classe, "handling_seconds_sum")
    h_n = _soma(por_classe, "handling_sample_count")

    fbs = next((r for r in por_classe if r["fbs_class"] == "fbs"), None)
    gmv_fbs = _dec(fbs["gross_gmv"]) if fbs else Decimal(0)
    elig_fbs = int(fbs["eligible_orders"] or 0) if fbs else 0
    unid_fbs = int(fbs["gross_units"] or 0) if fbs else 0

    totals = {
        "gross_gmv": gmv_total,
        "gross_units": unid_total,
        "created_orders": crea_total,
        "eligible_orders": elig_total,
        "cancelled_orders": _soma(por_classe, "cancelled_orders"),
        "to_return_orders": _soma(por_classe, "to_return_orders"),
        "to_return_gmv": sum((_dec(r["to_return_gmv"]) for r in por_classe), Decimal(0)),
        "unpaid_orders": _soma(por_classe, "unpaid_orders"),
        "unpaid_gmv": sum((_dec(r["unpaid_gmv"]) for r in por_classe), Decimal(0)),
        "handling": _handling(h_sum, h_n, elig_total),
    }
    rates = {"cancellation_rate": _share(totals["cancelled_orders"], crea_total)}
    shares = {
        "share_fbs_gmv": _share(gmv_fbs, gmv_total),
        "share_fbs_orders": _share(elig_fbs, elig_total),
        "share_fbs_units": _share(unid_fbs, unid_total),
    }

    by_class = [{
        "fbs_class": r["fbs_class"],
        "gross_gmv": _dec(r["gross_gmv"]),
        "gross_units": int(r["gross_units"] or 0),
        "created_orders": int(r["created_orders"] or 0),
        "eligible_orders": int(r["eligible_orders"] or 0),
        "cancelled_orders": int(r["cancelled_orders"] or 0),
        "to_return_gmv": _dec(r["to_return_gmv"]),
        "unpaid_gmv": _dec(r["unpaid_gmv"]),
        "handling": _handling(r["handling_seconds_sum"],
                              r["handling_sample_count"],
                              int(r["eligible_orders"] or 0)),
    } for r in por_classe]

    # --- Quebras: agrega por chave e SO' DEPOIS divide ----------------------
    def _agrupa(linhas, chave):
        out: dict = {}
        for r in linhas:
            k = r[chave]
            a = out.setdefault(k, {
                "gmv": Decimal(0), "gmv_fbs": Decimal(0), "un": 0, "un_fbs": 0,
                "elig": 0, "elig_fbs": 0, "canc": 0, "crea": 0,
                "brand": r.get("brand"), "wat": None})
            g = _dec(r["gross_gmv"])
            a["gmv"] += g
            a["un"] += int(r["gross_units"] or 0)
            a["elig"] += int(r["eligible_orders"] or 0)
            a["canc"] += int(r["cancelled_orders"] or 0)
            a["crea"] += int(r["created_orders"] or 0)
            if r["fbs_class"] == "fbs":
                a["gmv_fbs"] += g
                a["elig_fbs"] += int(r["eligible_orders"] or 0)
            w = r.get("source_watermark_at")
            if w and (a["wat"] is None or w > a["wat"]):
                a["wat"] = w
        return out

    marcas = _agrupa(por_marca, "brand")
    by_brand = [{
        "brand": k,
        "gross_gmv": v["gmv"], "gross_units": v["un"],
        "eligible_orders": v["elig"], "cancelled_orders": v["canc"],
        "created_orders": v["crea"],
        "share_fbs_gmv": _share(v["gmv_fbs"], v["gmv"]),
        "share_fbs_orders": _share(v["elig_fbs"], v["elig"]),
    } for k, v in sorted(marcas.items())]

    contas = _agrupa(por_conta, "shop_account")
    by_account = [{
        "shop_account": k, "brand": v["brand"] or k,
        "gross_gmv": v["gmv"], "eligible_orders": v["elig"],
        "created_orders": v["crea"],
        "share_fbs_gmv": _share(v["gmv_fbs"], v["gmv"]),
        "source_watermark_at": v["wat"],
    } for k, v in sorted(contas.items())]

    daily = [{
        "ref_date": r["ref_date"], "fbs_class": r["fbs_class"],
        "gross_gmv": _dec(r["gross_gmv"]),
        "gross_units": int(r["gross_units"] or 0),
        "eligible_orders": int(r["eligible_orders"] or 0),
        "created_orders": int(r["created_orders"] or 0),
        "cancelled_orders": int(r["cancelled_orders"] or 0),
    } for r in diario]

    # --- Cobertura ---------------------------------------------------------
    obs = sorted({r["shop_account"] for r in observadas})
    esperadas = list(EXPECTED_ACCOUNTS)
    if accounts:
        esperadas = [a for a in esperadas if a in accounts]
    missing = [a for a in esperadas if a not in obs]
    unexpected = [a for a in obs if a not in EXPECTED_ACCOUNTS]
    covered = sorted({r["brand"] for r in observadas if r["brand"]})

    avisos: list[str] = []
    if missing:
        avisos.append(
            f"conta(s) esperada(s) sem linha na janela: {', '.join(missing)}. "
            "Fora da cobertura medida -- nao e' 0%.")
    if unexpected:
        avisos.append(
            f"conta(s) FORA da allowlist observada(s): {', '.join(unexpected)}. "
            "O contrato da fonte mudou; investigue antes de usar o total.")
    avisos.append(LIMITACAO_COBERTURA)
    avisos.append(LIMITACAO_CARGA_MANUAL)

    frescor = _freshness(limites, agora, last_closed_date)
    if frescor["freshness_status"] == "stale":
        avisos.append(
            "dado DESATUALIZADO: verifique a idade da fonte e da publicacao "
            "antes de decidir com estes numeros.")

    meta = {
        "marketplace": "shopee",
        "scope_label": SCOPE_LABEL,
        "date_policy": "closed_day",
        "d0_materialized": False,
        "last_closed_date": last_closed_date,
        "requested_date_from": date_from,
        "requested_date_to": date_to,
        "effective_date_from": escopo["effective_date_from"],
        "effective_date_to": escopo["effective_date_to"],
        "expected_accounts": esperadas,
        "observed_accounts": obs,
        "missing_accounts": missing,
        "unexpected_accounts": unexpected,
        "covered_brands": covered,
        "brands_not_covered": list(BRANDS_NOT_COVERED),
        "gmv_definition": GMV_DEFINITION,
        "freshness": frescor,
        "warnings": avisos,
        "limitations": [LIMITACAO_GMV_BRUTO, LIMITACAO_COBERTURA,
                        LIMITACAO_ENTREGA, LIMITACAO_D0, LIMITACAO_SERIE,
                        LIMITACAO_CARGA_MANUAL],
    }

    return {"meta": meta, "totals": totals, "rates": rates, "shares": shares,
            "by_class": by_class, "by_brand": by_brand,
            "by_account": by_account, "daily": daily}
