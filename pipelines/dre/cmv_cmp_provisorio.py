"""DRE-CMV-CMP-1 — CMV PROVISORIO por CMP, por celula `competencia x canal x marca`.

O QUE E'
--------
`CMV provisorio = unidades vendidas x CMP efetivo na competencia`, somado so'
sobre os SKUs com casamento valido. Serve para destravar o cenario ESTIMADO da
DRE quando o CMV fiscal da competencia ainda nao existe. Nunca substitui o CMV
fiscal: e' gravado em tabela propria (`marts.fact_dre_cmv_provisorio_mensal`,
migration 027) e o serving so' o usa quando o fiscal esta inutilizavel.

FONTES (somente leitura, somente API)
-------------------------------------
Vendas por SKU, recortadas ate a MESMA data maxima que a DRE usa na receita da
celula (`marts.fact_marketplace_daily_performance`), para reconciliar:

  * Mercado Livre — `gold.ml_produto_diario` (pedidos da API), por `seller_sku`;
  * TikTok — `api.tiktok_analytics_skus`, `sku_id` -> `seller_sku` pelo
    inventario (`gold.tiktok_inventory_daily`); `sku_id` com mais de um
    `seller_sku` no historico fica `sku_ambiguo`, sem CMP;
  * Shopee — `gold.shopee_product_daily` (pedidos da API), SO' as marcas
    servidas pela API. Kokeshi (export manual) fica `ausente_na_fonte`, nunca zero.

CMP E CASAMENTO
---------------
Indice de CMP de `pipelines.dre.cmp_sku` (filial SP/0301, MAGA SP) e o resolvedor
de `pipelines.dre.cmp_sku_torre`, com as regras aprovadas: vigencia por
entidade (alternativa 3, carry-forward so' para frente), escopo marca ->
entidade, conflito sem escolha, codigo exato provisorio, via GTIN restrita,
KS03041/KS03042 sem CMP, kit sem regra. Sem casamento => sem CMP (NUNCA zero).

O nome do indicador continua "margem bruta apos CMP" quando o CMV e' so' CMP;
no cenario estimado da DRE ele e' rotulado como CMV provisorio por CMP.

Codigos de saida: 0 ok; 65 recusa; 69 fonte indisponivel; 75 lock; 78 migration ausente.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from pipelines.dre import cmp_sku as C
from pipelines.dre import cmp_sku_torre as T

logger = logging.getLogger("pipelines.dre.cmv_cmp_provisorio")

SOURCE_NAME = "dre_cmv_provisorio_mensal"
TABELA = "marts.fact_dre_cmv_provisorio_mensal"
LOCK_KEY = 918140027
EXIT_OK, EXIT_RECUSA, EXIT_FONTE, EXIT_LOCK, EXIT_MIGRATION = 0, 65, 69, 75, 78

CANAIS = ("tiktok", "ml", "shopee")
MKT_ID = {"tiktok": 1, "ml": 2, "shopee": 3}
CANAL_POR_MKT = {v: k for k, v in MKT_ID.items()}
MARCAS = tuple(T.ENTIDADES_POR_MARCA)
#: Shopee via API: Kokeshi entra por export manual e fica fora desta fonte.
MARCAS_SHOPEE_API = ("apice", "barbours", "lescent", "rituaria")
REGRA_TEMPORAL = "entidade_vigencia"
FONTE_VENDAS = {
    "ml": "gold.ml_produto_diario (API)",
    "tiktok": "api.tiktok_analytics_skus + gold.tiktok_inventory_daily (API)",
    "shopee": "gold.shopee_product_daily (API)",
}
#: Nivel de casamento -> confianca.
CONFIANCA = {T.NIVEL_GTIN: "alta", T.NIVEL_VIA_GTIN: "alta", T.NIVEL_VOLUME: "media", T.NIVEL_PROVISORIO: "baixa"}
RE_KIT = re.compile(r"(?i)(^kit|\bkit\b)")

# ---------------------------------------------------------------------------
# SQL (somente leitura)
# ---------------------------------------------------------------------------

#: Receita da celula e a data maxima por canal — o mesmo recorte da DRE.
SQL_RECEITA_CELULA = """
    SELECT f.marketplace_id, l.brand_key AS marca, SUM(f.gmv) AS receita, MAX(f.date) AS data_max
    FROM marts.fact_marketplace_daily_performance f
    JOIN marts.dim_loja l ON l.loja_id = f.loja_id
    WHERE f.date >= :inicio AND f.date <= :fim AND l.brand_key = ANY(:marcas)
    GROUP BY 1, 2
"""
SQL_ML = """
    SELECT brand AS marca, seller_sku AS sku, MAX(title) AS produto, NULL::text AS ean,
           SUM(units_sold) AS unidades, SUM(revenue) AS receita, false AS sku_ambiguo
    FROM gold.ml_produto_diario
    WHERE ref_date BETWEEN :inicio AND :corte AND brand = ANY(:marcas)
    GROUP BY 1, 2
"""
#: `sku_id` -> `seller_sku`: com mais de um codigo no historico, nao ha escolha.
SQL_TIKTOK = """
    WITH inv AS (
        SELECT sku_id, MAX(seller_sku) AS seller_sku, MAX(product_title) AS produto,
               COUNT(DISTINCT seller_sku) AS n_codigos
        FROM gold.tiktok_inventory_daily
        WHERE seller_sku IS NOT NULL AND seller_sku <> ''
        GROUP BY sku_id
    )
    SELECT a.brand AS marca,
           CASE WHEN i.n_codigos = 1 THEN i.seller_sku END AS sku,
           MAX(i.produto) AS produto, NULL::text AS ean,
           SUM(a.units_sold) AS unidades, SUM(a.gmv) AS receita,
           COALESCE(i.n_codigos, 0) > 1 AS sku_ambiguo
    FROM api.tiktok_analytics_skus a
    LEFT JOIN inv i ON i.sku_id = a.sku_id
    WHERE a.date BETWEEN :inicio AND :corte AND a.brand = ANY(:marcas)
    GROUP BY 1, 2, 7
"""
SQL_SHOPEE = """
    SELECT brand AS marca, sku, MAX(product_name) AS produto, MAX(ean) AS ean,
           SUM(units) AS unidades, SUM(gmv) AS receita, false AS sku_ambiguo
    FROM gold.shopee_product_daily
    WHERE date BETWEEN :inicio AND :corte AND brand = ANY(:marcas)
    GROUP BY 1, 2
"""
SQL_POR_CANAL = {"ml": SQL_ML, "tiktok": SQL_TIKTOK, "shopee": SQL_SHOPEE}


class FonteIndisponivel(RuntimeError):
    pass


class LockOcupado(RuntimeError):
    pass


class MigrationAusente(RuntimeError):
    pass


class PublicacaoRecusada(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Regras puras
# ---------------------------------------------------------------------------

def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


@dataclass(frozen=True)
class LinhaVenda:
    canal: str
    marca: str
    sku: Optional[str]
    produto: Optional[str]
    ean: Optional[str]
    unidades: Decimal
    receita: Decimal
    sku_ambiguo: bool = False


def classificar(ind: C.Indice, cat: T.Catalogo, vendas: list[LinhaVenda], comp: date) -> list[dict]:
    """Resolve o CMP de cada SKU vendido. Sem casamento => `cmp=None` (nunca zero)."""
    idx_gtin = T.indice_gtin(ind)
    out = []
    for v in vendas:
        r: Optional[T.Resolucao] = None
        if v.sku_ambiguo:
            status, nivel, motivo = "sku_ambiguo", "", "sku_id com mais de um seller_sku no inventario"
        elif not v.sku:
            status, nivel, motivo = "sem_sku", "", "venda sem seller_sku"
        elif T.classificar_tipo(cat, v.marca, [C.chave_sku(v.sku)]) == "kit":
            status, nivel, motivo = "kit_sem_regra", "", "kit: CMP do kit x soma dos componentes sem regra aprovada"
        else:
            texto = " ".join(x for x in (v.ean, v.produto) if x)
            r = T.resolver(ind, {"seller_sku": v.sku}, comp, texto, cat, v.marca, idx_gtin)
            status, nivel, motivo = r.status, r.nivel, r.motivo
        cmp = r.cmp if (r is not None and r.status == "coberto") else None
        vig = r.vigencia if (r is not None and r.status == "coberto") else None
        out.append({
            "canal": v.canal, "marca": v.marca, "sku": v.sku, "produto": v.produto,
            "unidades": v.unidades, "receita": v.receita, "status": status, "nivel": nivel,
            "confianca": CONFIANCA.get(nivel, ""), "motivo": motivo,
            "cmp": cmp, "cmv": (v.unidades * cmp) if cmp is not None else None,
            "competencia_origem": vig.competencia_origem if vig else None,
            "carry_forward": bool(vig and vig.carry_forward),
            "entidades": "|".join(r.entidades) if r is not None else "",
            "candidatos": "|".join(f"{e}:{s}:{c}" for e, s, c, _ in r.candidatos) if r is not None else "",
            "parece_kit": bool(RE_KIT.search(f"{v.sku or ''} {v.produto or ''}")),
        })
    return out


def confianca_predominante(receita_por_nivel: dict) -> Optional[str]:
    """Confianca do nivel com mais receita coberta; empate vai para a MENOR confianca."""
    ordem = {"baixa": 0, "media": 1, "alta": 2}
    por_conf: Counter = Counter()
    for nivel, rec in receita_por_nivel.items():
        por_conf[CONFIANCA[nivel]] += rec
    if not por_conf or sum(por_conf.values()) == 0:
        return None
    topo = max(por_conf.values())
    return min((c for c, v in por_conf.items() if v == topo), key=ordem.get)


def agregar(linhas: list[dict], receita_celula: dict, cortes: dict, comp: date) -> list[dict]:
    """Uma linha por celula ESPERADA (as que tem receita na DRE).

    Celula sem nenhuma venda na fonte -> `ausente_na_fonte`, com receita e CMV
    NULL. Celula com venda e nenhum SKU coberto -> `cmv_provisorio = NULL`.
    """
    por_celula: dict = defaultdict(list)
    for ln in linhas:
        por_celula[(ln["canal"], ln["marca"])].append(ln)
    out = []
    for (canal, marca), rec_cel in sorted(receita_celula.items()):
        ls = por_celula.get((canal, marca), [])
        base = {"competencia": comp, "canal": canal, "marca": marca, "data_corte": cortes.get(canal),
                "receita_celula": rec_cel, "fonte_vendas": FONTE_VENDAS[canal], "regra_temporal": REGRA_TEMPORAL}
        if not ls:
            out.append({**base, "status": "ausente_na_fonte", "receita_fonte": None, "unidades": None,
                        "receita_coberta": None, "cobertura": None, "cmv_provisorio": None,
                        "unidades_sem_cmp": None, "skus_total": 0, "skus_cobertos": 0, "skus_conflito": 0,
                        "skus_sem_cmp": 0, "receita_gtin": None, "receita_volume": None,
                        "receita_codigo_provisorio": None, "receita_carry_forward": None,
                        "receita_por_status": {}, "confianca": None})
            continue
        cobertos = [x for x in ls if x["cmp"] is not None]
        rec_fonte = sum((x["receita"] for x in ls), Decimal(0))
        rec_cob = sum((x["receita"] for x in cobertos), Decimal(0))
        por_nivel: Counter = Counter()
        por_status: Counter = Counter()
        for x in ls:
            por_status[x["status"]] += x["receita"]
            if x["cmp"] is not None:
                por_nivel[x["nivel"]] += x["receita"]
        out.append({
            **base, "status": "calculado", "receita_fonte": rec_fonte,
            "unidades": sum((x["unidades"] for x in ls), Decimal(0)),
            "receita_coberta": rec_cob,
            "cobertura": (rec_cob / rec_fonte).quantize(Decimal("0.000001")) if rec_fonte else Decimal(0),
            "cmv_provisorio": sum((x["cmv"] for x in cobertos), Decimal(0)) if cobertos else None,
            "unidades_sem_cmp": sum((x["unidades"] for x in ls if x["cmp"] is None), Decimal(0)),
            "skus_total": len(ls), "skus_cobertos": len(cobertos),
            "skus_conflito": sum(1 for x in ls if x["status"] == "conflito"),
            "skus_sem_cmp": sum(1 for x in ls if x["cmp"] is None),
            "receita_gtin": por_nivel[T.NIVEL_GTIN] + por_nivel[T.NIVEL_VIA_GTIN],
            "receita_volume": por_nivel[T.NIVEL_VOLUME],
            "receita_codigo_provisorio": por_nivel[T.NIVEL_PROVISORIO],
            "receita_carry_forward": sum((x["receita"] for x in cobertos if x["carry_forward"]), Decimal(0)),
            "receita_por_status": dict(por_status),
            "confianca": confianca_predominante(dict(por_nivel)),
        })
    return out


def resumo_publico(celulas: list[dict]) -> list[dict]:
    """So' percentuais e contagens: nada de receita, CMP, CMV ou SKU."""
    out = []
    for c in celulas:
        pct = None if c["cobertura"] is None else float((c["cobertura"] * 100).quantize(Decimal("0.1")))
        out.append({"canal": c["canal"], "marca": c["marca"], "status": c["status"], "cobertura_pct": pct,
                    "skus_total": c["skus_total"], "skus_cobertos": c["skus_cobertos"],
                    "skus_conflito": c["skus_conflito"], "confianca": c["confianca"]})
    return out


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def _rows(conn, sql: str, p: dict) -> list[dict]:
    from sqlalchemy import text
    return [dict(r) for r in conn.execute(text(sql), p).mappings().all()]


def ler_receita_celulas(neon, comp: date, fim: date) -> tuple[dict, dict]:
    """Receita da DRE por celula e data maxima por canal (Neon, READ ONLY)."""
    from sqlalchemy import text
    with neon.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = _rows(conn, SQL_RECEITA_CELULA, {"inicio": comp, "fim": fim, "marcas": list(MARCAS)})
        conn.rollback()
    receita, cortes = {}, {}
    for r in rows:
        canal = CANAL_POR_MKT.get(r["marketplace_id"])
        if canal is None:
            continue
        receita[(canal, r["marca"])] = _d(r["receita"])
        cortes[canal] = max(cortes.get(canal, r["data_max"]), r["data_max"])
    return receita, cortes


def ler_vendas(datamart, comp: date, cortes: dict) -> tuple[list[LinhaVenda], T.Catalogo]:
    """Vendas por SKU de cada canal ate a data de corte da DRE (Data Mart, READ ONLY)."""
    from sqlalchemy import text
    vendas = []
    with datamart.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '600s'"))
        for canal, sql in SQL_POR_CANAL.items():
            if canal not in cortes:
                continue
            marcas = list(MARCAS_SHOPEE_API) if canal == "shopee" else list(MARCAS)
            for r in _rows(conn, sql, {"inicio": comp, "corte": cortes[canal], "marcas": marcas}):
                vendas.append(LinhaVenda(canal, r["marca"], r["sku"], r["produto"], r["ean"],
                                         _d(r["unidades"]), _d(r["receita"]), bool(r["sku_ambiguo"])))
        cad = _rows(conn, T.SQL_CADASTRO, {})
        kits = _rows(conn, T.SQL_KITS, {})
        conn.rollback()
    return vendas, T.montar_catalogo(cad, kits)


# ---------------------------------------------------------------------------
# Publicacao (so' com --apply)
# ---------------------------------------------------------------------------

COLUNAS = ("competencia", "canal", "marca", "data_corte", "status", "receita_fonte", "unidades", "receita_coberta",
           "cobertura", "cmv_provisorio", "unidades_sem_cmp", "skus_total", "skus_cobertos", "skus_conflito",
           "skus_sem_cmp", "receita_gtin", "receita_volume", "receita_codigo_provisorio", "receita_carry_forward",
           "confianca", "regra_temporal", "fonte_vendas", "cmp_fontes_sha256")
SQL_EXISTE = f"SELECT to_regclass('{TABELA}') IS NOT NULL"
SQL_LOCK = f"SELECT pg_try_advisory_xact_lock({LOCK_KEY})"
SQL_APAGA = f"DELETE FROM {TABELA} WHERE competencia = :competencia"
SQL_INSERE = (f"INSERT INTO {TABELA} ({', '.join(COLUNAS)}, publicado_em, run_id) VALUES ("
              + ", ".join(f":{c}" for c in COLUNAS) + ", :publicado_em, :run_id)")
SQL_CONTA = f"SELECT count(*) FROM {TABELA} WHERE competencia = :competencia"
SQL_FORA = f"SELECT count(*) FROM {TABELA} WHERE competencia <> :competencia"
SQL_AUDIT_ABRE = """INSERT INTO audit.source_sync_run (source_name, status, started_at, source_min_date, source_max_date)
    VALUES (:fonte, 'running', :agora, :competencia, :competencia) RETURNING sync_run_id"""
SQL_AUDIT_FECHA = """UPDATE audit.source_sync_run SET status = 'success', finished_at = :agora,
    rows_extracted = :n, rows_loaded = :n WHERE sync_run_id = :run_id"""
SQL_AUDIT_FALHA = """INSERT INTO audit.source_sync_run (source_name, status, started_at, finished_at, error_message,
    source_min_date, source_max_date) VALUES (:fonte, 'failed', :agora, :agora, :erro, :competencia, :competencia)"""


def sha_das_fontes(leituras: list[C.Leitura]) -> str:
    return hashlib.sha256("|".join(sorted(le.sha256 for le in leituras)).encode()).hexdigest()


def publicar(neon, celulas: list[dict], comp: date, cmp_sha: str, *, agora: Optional[datetime] = None,
             antes_do_commit: Optional[Callable[[Any], None]] = None) -> dict:
    """Transacao unica, lock sem espera, DELETE so' da competencia, reconciliacao. Sem retry."""
    from sqlalchemy import text
    from pipelines.dre.premissa_publisher import sanitizar

    agora = agora or datetime.now(timezone.utc)
    p = {"competencia": comp}
    try:
        with neon.begin() as conn:
            if not conn.execute(text(SQL_LOCK)).scalar():
                raise LockOcupado(f"advisory lock {LOCK_KEY} ocupado")
            if not conn.execute(text(SQL_EXISTE)).scalar():
                raise MigrationAusente(f"{TABELA} nao existe: aplique a migration 027 antes do --apply")
            run_id = conn.execute(text(SQL_AUDIT_ABRE), {"fonte": SOURCE_NAME, "agora": agora,
                                                          "competencia": comp}).scalar_one()
            fora = conn.execute(text(SQL_FORA), p).scalar_one()
            conn.execute(text(SQL_APAGA), p)
            for c in celulas:
                conn.execute(text(SQL_INSERE), {**{k: c.get(k) for k in COLUNAS}, "cmp_fontes_sha256": cmp_sha,
                                                "publicado_em": agora, "run_id": run_id})
            if conn.execute(text(SQL_CONTA), p).scalar_one() != len(celulas):
                raise PublicacaoRecusada("linhas gravadas diferem das montadas")
            if conn.execute(text(SQL_FORA), p).scalar_one() != fora:
                raise PublicacaoRecusada("linhas de outras competencias mudaram")
            conn.execute(text(SQL_AUDIT_FECHA), {"agora": agora, "n": len(celulas), "run_id": run_id})
            if antes_do_commit is not None:
                antes_do_commit(conn)
    except BaseException as exc:
        if not isinstance(exc, LockOcupado):
            try:
                with neon.begin() as conn:
                    conn.execute(text(SQL_AUDIT_FALHA), {"fonte": SOURCE_NAME, "agora": agora,
                                                          "erro": sanitizar(exc), "competencia": comp})
            except Exception as aud:  # noqa: BLE001
                logger.error("auditoria da falha nao registrada: %s", sanitizar(aud))
        raise
    return {"run_id": run_id, "gravadas": len(celulas), "outras_competencias_preservadas": fora}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _mes(s: str) -> date:
    m = re.fullmatch(r"(\d{4})-(\d{2})", s or "")
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise argparse.ArgumentTypeError(f"competencia invalida {s!r}; use YYYY-MM")
    return date(int(m.group(1)), int(m.group(2)), 1)


def _json(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o).__name__)


def _engines():
    from sqlalchemy import create_engine
    from pipelines.common.config import settings
    from pipelines.dre.insumo_publisher import url_psycopg2

    if not settings.database_url or not settings.datamart_url:
        raise FonteIndisponivel("DATABASE_URL ou Data Mart nao configurado")
    return (create_engine(url_psycopg2(settings.database_url), pool_pre_ping=True, future=True),
            create_engine(url_psycopg2(settings.datamart_url), pool_pre_ping=True, future=True))


def main(argv: Optional[list[str]] = None, *, engines: Optional[Callable] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipelines.dre.cmv_cmp_provisorio",
                                 description="CMV provisorio por CMP. Dry-run por padrao.")
    ap.add_argument("--pasta", required=True, type=Path, help="planilhas de CMP (privadas)")
    ap.add_argument("--competencia", required=True, type=_mes)
    ap.add_argument("--saida", type=Path, default=Path(".local/dre_cmv_cmp"), help="relatorio privado")
    ap.add_argument("--apply", action="store_true", help="publica no Neon; sem ele e' dry-run")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    comp = args.competencia
    fim = C.somar_meses(comp, 1) - timedelta(days=1)
    try:
        fontes, _ = C.descobrir_fontes(args.pasta)
        leituras = [C.ler(f) for f in fontes]
    except C.FonteRecusada as exc:
        print(json.dumps({"status": "recusado", "motivo": str(exc)}, ensure_ascii=False))
        return EXIT_RECUSA
    ind = C.construir_indice(leituras)
    try:
        neon, datamart = (engines or _engines)()
        receita_cel, cortes = ler_receita_celulas(neon, comp, fim)
        vendas, cat = ler_vendas(datamart, comp, cortes)
    except Exception as exc:  # noqa: BLE001
        from pipelines.dre.premissa_publisher import sanitizar
        print(json.dumps({"status": "fonte_indisponivel", "detalhe": sanitizar(exc)}, ensure_ascii=False))
        return EXIT_FONTE
    linhas = classificar(ind, cat, vendas, comp)
    celulas = agregar(linhas, receita_cel, cortes, comp)
    args.saida.mkdir(parents=True, exist_ok=True)
    with open(args.saida / f"matriz_cmv_cmp_{comp:%Y-%m}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]) if linhas else ["vazio"])
        w.writeheader()
        w.writerows(linhas)
    (args.saida / f"celulas_cmv_cmp_{comp:%Y-%m}.json").write_text(
        json.dumps(celulas, default=_json, ensure_ascii=False, indent=1), encoding="utf-8")
    saida: dict = {"modo": "apply" if args.apply else "dry-run", "competencia": comp.isoformat()[:7],
                   "cortes": {k: v.isoformat() for k, v in cortes.items()}, "celulas": resumo_publico(celulas)}
    codigo = EXIT_OK
    if args.apply:
        try:
            saida["escrita"] = publicar(neon, celulas, comp, sha_das_fontes(leituras))
        except LockOcupado as exc:
            saida["erro"], codigo = str(exc), EXIT_LOCK
        except MigrationAusente as exc:
            saida["erro"], codigo = str(exc), EXIT_MIGRATION
        except PublicacaoRecusada as exc:
            saida["erro"], codigo = str(exc), EXIT_RECUSA
    print(json.dumps(saida, default=_json, ensure_ascii=False, indent=1))
    return codigo


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
