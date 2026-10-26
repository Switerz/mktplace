"""Operacao do Estoque Full Shopee: preflight, primeira execucao, reconciliacao e rollback.

STOCK-DAILY-SHOPEE-TIKTOK-1. Quatro subcomandos, um por pergunta do operador:

    preflight            -- da' para publicar agora? (SO' LEITURA)
    primeira-execucao    -- preflight + publica + reconcilia, num comando so'
    reconciliar          -- o que esta' publicado bate com a fonte? (SO' LEITURA)
    reverter             -- desfaz UMA publicacao, ancorada no run_id

POR QUE A RECONCILIACAO NAO REUSA O PUBLICADOR
-----------------------------------------------
Comparar o publicado com o que o PROPRIO publicador recalcula so' prova que ele
e' deterministico. Aqui o recalculo e' SQL independente, direto do JSON de
`raw.shopee_products.stock_info`. O que se reaproveita do publicador e' so' o
CONTRATO (status que contam como demanda, janela, limites provisorios), porque
discordar dele seria medir outra definicao, nao reconciliar.

A fonte e' um RETRATO sobrescrito todo dia as 06:00 BRT. Se ela mudou depois da
fotografia (`ingested_at` da raw > `source_captured_at` da fato), as checagens
de quantidade NAO se aplicam e saem como `NAO_APLICAVEL` -- nunca como OK.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from pipelines import sync_shopee_fbs_stock_daily as P

#: Confirmacao explicita: a primeira execucao ESCREVE no Neon de producao.
CONFIRMACAO = "--confirmo-escrita-em-producao"

#: Head minimo do Alembic: a 021 cria as duas fatos.
MIGRATION_MINIMA = "021"


# --------------------------------------------------------------------------- #
# CONEXOES                                                                     #
# --------------------------------------------------------------------------- #
# Somente leitura por TRANSACAO (`SET TRANSACTION`), nunca por `set_session`.
# As conexoes vem do POOL do mesmo processo que, na primeira execucao, publica
# logo em seguida: `set_session(readonly=True)` sobrevive ao `close()` e a
# auditoria do publicador receberia uma conexao que recusa INSERT. Medido no
# teste `test_preflight_usa_o_mesmo_portao_do_publicador` antes desta forma.
def _neon_leitura():
    conn = P.local_engine().raw_connection()
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
    return conn


def _dm_leitura():
    conn = P.DataMartSession().get_bind().raw_connection()
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    return conn


def _linhas(conn, sql: str, params: dict | None = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# PREFLIGHT                                                                    #
# --------------------------------------------------------------------------- #
SQL_NEON_ESTADO = """
    SELECT (SELECT version_num FROM alembic_version LIMIT 1)              AS alembic,
           to_regclass('marts.fact_shopee_fbs_stock_daily')::text           AS fato_produto,
           to_regclass('marts.fact_shopee_fbs_stock_location_daily')::text  AS fato_location,
           (SELECT COUNT(*) FROM pg_locks
             WHERE locktype = 'advisory'
               AND ((classid::bigint << 32) | objid::bigint) = %(k)s)       AS locks,
           (SELECT COUNT(*) FROM audit.source_sync_run
             WHERE source_name = %(s)s AND status = 'running')              AS auditorias_running
"""


def preflight(ref_date: date) -> dict:
    """Tudo que impede publicar, medido SO' com leitura. Nada e' escrito."""
    problemas: list[str] = []
    neon = _neon_leitura()
    try:
        estado = _linhas(neon, SQL_NEON_ESTADO,
                         {"k": P.ADVISORY_LOCK_KEY, "s": P.AUDIT_SOURCE})[0]
        ultima = None
        if estado["fato_produto"]:
            ultima = _linhas(neon, f"SELECT MAX(ref_date) AS d FROM {P.FACT_PRODUTO}")[0]["d"]
    finally:
        neon.rollback()
        neon.close()
    if not estado["alembic"] or estado["alembic"] < MIGRATION_MINIMA:
        problemas.append(f"alembic {estado['alembic']} anterior a {MIGRATION_MINIMA}")
    if not (estado["fato_produto"] and estado["fato_location"]):
        problemas.append("fatos do estoque Full ausentes no Neon")
    if estado["locks"]:
        problemas.append(f"advisory lock {P.ADVISORY_LOCK_KEY} ocupado")
    if estado["auditorias_running"]:
        problemas.append(f"{estado['auditorias_running']} auditoria(s) em running")

    dm = _dm_leitura()
    try:
        # O MESMO SQL do portao do publicador, com o parametro no estilo do
        # psycopg2: o preflight nao pode ter uma regra propria de frescor.
        portao = _linhas(dm, P.SQL_PORTAO_FONTE.text.replace(":contas", "%(contas)s"),
                         {"contas": list(P.CONTAS_ESPERADAS)})
        regime = _linhas(dm, SQL_DM_REGIME_POR_CONTA, {"contas": list(P.CONTAS_ESPERADAS)})
    finally:
        dm.rollback()
        dm.close()
    problemas += P.avaliar_portao(portao, ref_date)
    fisico = {r["shop_account"]: int(r["fisico"] or 0) for r in regime}
    vendavel = {r["shop_account"]: int(r["vendavel"] or 0) for r in regime}
    problemas += [P.mensagem_full_pausado(c, fisico[c])
                  for c in P.contas_com_full_pausado(fisico, vendavel)]
    return {
        "ref_date": str(ref_date),
        "pronto": not problemas,
        "problemas": problemas,
        "neon": {**estado, "ultima_fotografia": str(ultima) if ultima else None},
        "portao_fonte": [{k: str(v) if isinstance(v, datetime) else v
                          for k, v in r.items()} for r in portao],
        "full_por_conta": {c: {"fisico": fisico[c], "vendavel": vendavel.get(c, 0)}
                           for c in sorted(fisico)},
    }


#: Fisico x vendavel por conta, direto do JSON da raw: alimenta a MESMA regra
#: de Full pausado que o publicador aplica.
SQL_DM_REGIME_POR_CONTA = """
    SELECT r.shop_account,
           SUM((e->>'stock')::bigint)                                       AS fisico,
           SUM((e->>'stock')::bigint) FILTER (WHERE e->>'if_saleable' = 'true') AS vendavel
      FROM raw.shopee_products r
      JOIN silver.stg_shopee_products s
        ON s.shop_account = r.shop_account AND s.item_id = r.item_id
     CROSS JOIN LATERAL jsonb_array_elements(r.stock_info->'shopee_stock') e
     WHERE s.is_fulfillment_by_shopee AND r.shop_account = ANY(%(contas)s)
     GROUP BY 1
"""


# --------------------------------------------------------------------------- #
# RECONCILIACAO                                                                #
# --------------------------------------------------------------------------- #
#: Recalculo INDEPENDENTE por produto, direto do JSON da raw.
SQL_DM_PRODUTOS = """
    SELECT r.shop_account, r.item_id::text AS item_id,
           COALESCE(s.is_kit, FALSE) AS is_kit,
           COALESCE((SELECT SUM((e->>'stock')::bigint)
                       FROM jsonb_array_elements(r.stock_info->'shopee_stock') e
                      WHERE e->>'if_saleable' = 'true'), 0) AS vendavel,
           COALESCE((SELECT SUM((e->>'stock')::bigint)
                       FROM jsonb_array_elements(r.stock_info->'shopee_stock') e), 0) AS total,
           r.ingested_at
      FROM raw.shopee_products r
      JOIN silver.stg_shopee_products s
        ON s.shop_account = r.shop_account AND s.item_id = r.item_id
     WHERE s.is_fulfillment_by_shopee AND r.shop_account = ANY(%(contas)s)
"""

SQL_DM_LOCATIONS = """
    SELECT r.shop_account, r.item_id::text AS item_id, e->>'location_id' AS location_id,
           SUM((e->>'stock')::bigint) AS estoque
      FROM raw.shopee_products r
      JOIN silver.stg_shopee_products s
        ON s.shop_account = r.shop_account AND s.item_id = r.item_id
     CROSS JOIN LATERAL jsonb_array_elements(r.stock_info->'shopee_stock') e
     WHERE s.is_fulfillment_by_shopee AND r.shop_account = ANY(%(contas)s)
     GROUP BY 1, 2, 3
"""

#: Demanda recalculada com o CONTRATO do publicador (status e janela).
SQL_DM_DEMANDA = """
    SELECT i.shop_account, i.item_id::text AS item_id, SUM(i.quantity)::bigint AS unidades
      FROM silver.stg_shopee_order_items i
      JOIN silver.stg_shopee_orders o
        ON o.shop_account = i.shop_account AND o.order_sn = i.order_sn
     WHERE o.created_date_brt >= %(inicio)s AND o.created_date_brt < %(fim)s
       AND o.order_status = ANY(%(status)s) AND o.shop_account = ANY(%(contas)s)
     GROUP BY 1, 2
"""

SQL_NEON_PRODUTOS = f"""
    SELECT shop_account, item_id, is_kit, full_stock_saleable, full_stock_total,
           units_sold_28d, classificacao_torre, cobertura_torre_dias,
           source_run_id, source_captured_at
      FROM {P.FACT_PRODUTO} WHERE ref_date = %(d)s
"""

SQL_NEON_LOCATIONS = f"""
    SELECT shop_account, item_id, location_id, full_stock, is_saleable
      FROM {P.FACT_LOCATION} WHERE ref_date = %(d)s
"""


def classificacao_esperada(is_kit: bool, vendavel: int, unidades: int) -> str:
    """A regra documentada, reescrita aqui de proposito (nao importada)."""
    if is_kit:
        return "KIT_NAO_CONCILIADO"
    if unidades == 0:
        return "SEM_GIRO_CANDIDATO" if vendavel > 0 else "SEM_DEMANDA_MEDIDA"
    if vendavel == 0:
        return "RUPTURA_CANDIDATA"
    cobertura = Decimal(vendavel) / (Decimal(unidades) / Decimal(P.JANELA_VENDAS_DIAS))
    if cobertura < P.COBERTURA_BAIXA_DIAS_PROVISORIO:
        return "BAIXO_CANDIDATO"
    if cobertura >= P.COBERTURA_EXCESSO_DIAS_PROVISORIO:
        return "EXCESSO_CANDIDATO"
    return "SUFICIENTE"


def comparar(ref_date: date, neon_prod, neon_loc, dm_prod, dm_loc, dm_dem) -> dict:
    """Funcao pura: devolve as checagens nomeadas. Sem I/O."""
    chk: dict[str, dict] = {}

    def marcar(nome, ok, **detalhe):
        chk[nome] = {"status": "OK" if ok else "DIVERGENTE", **detalhe}

    runs = {r["source_run_id"] for r in neon_prod}
    marcar("um_run_por_fotografia", len(runs) == 1, runs=len(runs))

    # Grao fino x agregado, DENTRO do publicado: vale sempre, fonte mudada ou nao.
    soma_loc: dict[tuple, int] = {}
    for r in neon_loc:
        if r["is_saleable"] is True:
            k = (r["shop_account"], r["item_id"])
            soma_loc[k] = soma_loc.get(k, 0) + int(r["full_stock"])
    div_loc = sorted((r["shop_account"], r["item_id"]) for r in neon_prod
                     if soma_loc.get((r["shop_account"], r["item_id"]), 0)
                     != int(r["full_stock_saleable"]))
    marcar("localizacoes_somam_o_produto", not div_loc, divergentes=div_loc[:10])

    kits = [r for r in neon_prod if r["is_kit"]]
    marcar("kits_isolados", all(r["classificacao_torre"] == "KIT_NAO_CONCILIADO" for r in kits),
           kits=len(kits), unidades_kit=sum(int(r["full_stock_saleable"]) for r in kits))

    captura = max((r["source_captured_at"] for r in neon_prod), default=None)
    fonte = max((r["ingested_at"] for r in dm_prod), default=None)
    fonte_mudou = captura is None or fonte is None or fonte > captura
    resumo = {
        "produtos": len(neon_prod),
        "localizacoes": len(neon_loc),
        "vendavel_total": sum(int(r["full_stock_saleable"]) for r in neon_prod),
        "vendavel_operacional": sum(int(r["full_stock_saleable"]) for r in neon_prod
                                    if not r["is_kit"]),
        "vendavel_kits": sum(int(r["full_stock_saleable"]) for r in kits),
        "ruptura": sum(1 for r in neon_prod if r["classificacao_torre"] == "RUPTURA_CANDIDATA"),
        "sem_giro": sum(1 for r in neon_prod if r["classificacao_torre"] == "SEM_GIRO_CANDIDATO"),
        "captura_publicada": str(captura), "captura_fonte_atual": str(fonte),
    }
    if fonte_mudou:
        for nome in ("produtos_batem_com_a_fonte", "localizacoes_batem_com_a_fonte",
                     "vendavel_total_bate", "classificacao_recalculada_bate"):
            chk[nome] = {"status": "NAO_APLICAVEL",
                         "motivo": "a fonte foi recapturada depois da fotografia"}
        return {"ref_date": str(ref_date), "resumo": resumo, "checagens": chk}

    pub = {(r["shop_account"], r["item_id"]): r for r in neon_prod}
    src = {(r["shop_account"], r["item_id"]): r for r in dm_prod}
    so_pub, so_src = sorted(set(pub) - set(src)), sorted(set(src) - set(pub))
    qtd = [k for k in set(pub) & set(src)
           if (int(pub[k]["full_stock_saleable"]), int(pub[k]["full_stock_total"]),
               bool(pub[k]["is_kit"]))
           != (int(src[k]["vendavel"]), int(src[k]["total"]), bool(src[k]["is_kit"]))]
    marcar("produtos_batem_com_a_fonte", not (so_pub or so_src or qtd),
           so_publicado=so_pub[:10], so_fonte=so_src[:10], quantidade_divergente=sorted(qtd)[:10])

    loc_pub: dict[tuple, int] = {}
    for r in neon_loc:
        k = (r["shop_account"], r["item_id"], r["location_id"])
        loc_pub[k] = loc_pub.get(k, 0) + int(r["full_stock"])
    loc_src = {(r["shop_account"], r["item_id"], r["location_id"]): int(r["estoque"])
               for r in dm_loc}
    div = sorted(k for k in set(loc_pub) | set(loc_src) if loc_pub.get(k) != loc_src.get(k))
    marcar("localizacoes_batem_com_a_fonte", not div, divergentes=div[:10])

    soma_src = sum(int(r["vendavel"]) for r in dm_prod)
    marcar("vendavel_total_bate", soma_src == resumo["vendavel_total"],
           publicado=resumo["vendavel_total"], fonte=soma_src)

    dem = {(r["shop_account"], r["item_id"]): int(r["unidades"]) for r in dm_dem}
    cls = []
    for k, r in pub.items():
        if k not in src:
            continue
        unidades = dem.get(k, 0)
        vendavel = int(src[k]["vendavel"])
        esperado = classificacao_esperada(bool(src[k]["is_kit"]), vendavel, unidades)
        # Cobertura = vendavel / (unidades / janela); nula sem demanda.
        cobertura = (Decimal(vendavel) * P.JANELA_VENDAS_DIAS / Decimal(unidades)
                     if unidades else None)
        publicada = r["cobertura_torre_dias"]
        cobertura_ok = ((cobertura is None and publicada is None)
                        or (cobertura is not None and publicada is not None
                            and abs(Decimal(publicada) - cobertura) < Decimal("1e-6")))
        if (esperado, unidades) != (r["classificacao_torre"], int(r["units_sold_28d"])) \
                or not cobertura_ok:
            cls.append({"chave": list(k), "publicado": r["classificacao_torre"],
                        "esperado": esperado, "unidades_pub": int(r["units_sold_28d"]),
                        "unidades_fonte": unidades, "cobertura_pub": str(publicada),
                        "cobertura_fonte": str(cobertura)})
    marcar("classificacao_recalculada_bate", not cls, divergentes=cls[:10])
    return {"ref_date": str(ref_date), "resumo": resumo, "checagens": chk}


def reconciliar(ref_date: date) -> dict:
    contas = list(P.CONTAS_ESPERADAS)
    neon = _neon_leitura()
    try:
        neon_prod = _linhas(neon, SQL_NEON_PRODUTOS, {"d": ref_date})
        neon_loc = _linhas(neon, SQL_NEON_LOCATIONS, {"d": ref_date})
        estado = _linhas(neon, SQL_NEON_ESTADO,
                         {"k": P.ADVISORY_LOCK_KEY, "s": P.AUDIT_SOURCE})[0]
    finally:
        neon.rollback()
        neon.close()
    if not neon_prod:
        return {"ref_date": str(ref_date), "ok": False,
                "erro": "nenhuma fotografia publicada nesta data"}

    inicio, fim = P.janela_demanda(ref_date)
    dm = _dm_leitura()
    try:
        dm_prod = _linhas(dm, SQL_DM_PRODUTOS, {"contas": contas})
        dm_loc = _linhas(dm, SQL_DM_LOCATIONS, {"contas": contas})
        dm_dem = _linhas(dm, SQL_DM_DEMANDA, {
            "contas": contas, "inicio": inicio, "fim": fim,
            "status": list(P.STATUS_DEMANDA_OPERACIONAL)})
    finally:
        dm.rollback()
        dm.close()

    out = comparar(ref_date, neon_prod, neon_loc, dm_prod, dm_loc, dm_dem)
    out["checagens"]["zero_locks"] = {
        "status": "OK" if not estado["locks"] else "DIVERGENTE", "locks": estado["locks"]}
    out["checagens"]["zero_auditorias_presas"] = {
        "status": "OK" if not estado["auditorias_running"] else "DIVERGENTE",
        "running": estado["auditorias_running"]}
    out["ok"] = all(c["status"] != "DIVERGENTE" for c in out["checagens"].values())
    return out


# --------------------------------------------------------------------------- #
# ROLLBACK                                                                     #
# --------------------------------------------------------------------------- #
def reverter(ref_date: date, run_id: str, apply: bool) -> dict:
    """Apaga a fotografia de UM run. Sem `apply`, so' conta o que apagaria.

    Ancorado em (ref_date, source_run_id): nunca apaga outro dia nem outro run.
    Usa o MESMO advisory lock da publicacao, entao nao corre junto com ela.
    Depois do rollback a tela volta para a fotografia anterior (`MAX(ref_date)`),
    com a data dela. Uma fotografia do MESMO dia que este run sobrescreveu nao
    volta: a publicacao e' DELETE + INSERT do dia.
    """
    # Transacao EXPLICITA, com commit so' no ramo `apply`: o mesmo codigo em
    # SQLAlchemy 1.4 (imagem do Airflow) e 2.x. O lock de transacao sai junto.
    with P.local_engine().connect() as conn:
        trans = conn.begin()
        try:
            res = _reverter_na_transacao(conn, ref_date, run_id, apply)
        except BaseException:
            trans.rollback()
            raise
        if apply:
            trans.commit()
        else:
            trans.rollback()
    return res


def _reverter_na_transacao(conn, ref_date: date, run_id: str, apply: bool) -> dict:
    if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                        {"k": P.ADVISORY_LOCK_KEY}).scalar():
        raise P.ConcurrentRunError("advisory lock ocupado: publicacao em curso")
    p = {"d": ref_date, "r": run_id}
    contagem = {}
    for nome, tabela in (("produto", P.FACT_PRODUTO), ("location", P.FACT_LOCATION)):
        linhas = conn.execute(text(
            f"SELECT source_run_id = :r AS deste_run, COUNT(*) AS n FROM {tabela} "
            "WHERE ref_date = :d GROUP BY 1"), p).mappings().all()
        por_run = {bool(r["deste_run"]): int(r["n"]) for r in linhas}
        if por_run.get(False):
            raise P.ShopeeStockSyncError(
                f"{tabela} tem {por_run[False]} linha(s) de OUTRO run em {ref_date}: recusado")
        contagem[nome] = por_run.get(True, 0)
    if not contagem["produto"]:
        raise P.ShopeeStockSyncError(f"nenhuma linha do run {run_id} em {ref_date}")
    if apply:
        for tabela in (P.FACT_LOCATION, P.FACT_PRODUTO):
            conn.execute(text(f"DELETE FROM {tabela} "
                              "WHERE ref_date = :d AND source_run_id = :r"), p)
        restante = conn.execute(text(
            f"SELECT COUNT(*) FROM {P.FACT_PRODUTO} WHERE ref_date = :d"), p).scalar()
        if restante:
            raise P.ShopeeStockSyncError("rollback incompleto: transacao desfeita")
    return {"ref_date": str(ref_date), "run_id": run_id, "aplicado": apply,
            "linhas_produto": contagem["produto"], "linhas_location": contagem["location"]}


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _data(valor: str | None) -> date:
    return date.fromisoformat(valor) if valor else P._hoje_brt()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight")
    pe = sub.add_parser("primeira-execucao")
    pe.add_argument(CONFIRMACAO, dest="confirmado", action="store_true")
    rc = sub.add_parser("reconciliar")
    rc.add_argument("--ref-date")
    rv = sub.add_parser("reverter")
    rv.add_argument("--ref-date", required=True)
    rv.add_argument("--run-id", required=True)
    rv.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    def emitir(obj) -> None:
        print(json.dumps(obj, default=str, ensure_ascii=False, indent=2))

    try:
        if args.cmd == "preflight":
            res = preflight(P._hoje_brt())
            emitir(res)
            return 0 if res["pronto"] else P.EXIT_FONTE_NAO_PRONTA
        if args.cmd == "reconciliar":
            res = reconciliar(_data(args.ref_date))
            emitir(res)
            return 0 if res["ok"] else 1
        if args.cmd == "reverter":
            emitir(reverter(_data(args.ref_date), args.run_id, args.apply))
            return 0
        # primeira-execucao
        if not args.confirmado:
            print(f"recusado: a primeira execucao ESCREVE no Neon. Repita com {CONFIRMACAO}.",
                  file=sys.stderr)
            return P.EXIT_FALHA
        pre = preflight(P._hoje_brt())
        if not pre["pronto"]:
            emitir({"etapa": "preflight", **pre})
            return P.EXIT_FONTE_NAO_PRONTA
        pub = P.run(apply=True)
        rec = reconciliar(date.fromisoformat(pub["ref_date"]))
        emitir({"etapa": "concluida", "preflight": pre, "publicacao": pub,
                "reconciliacao": rec,
                "rollback": (f"python -m pipelines.ops.shopee_fbs_stock_ops reverter "
                             f"--ref-date {pub['ref_date']} --run-id {pub['run_id']} --apply")})
        return 0 if rec["ok"] else 1
    except P.FonteNaoProntaError as exc:
        print(P.sanitizar(exc), file=sys.stderr)
        return P.EXIT_FONTE_NAO_PRONTA
    except P.ConcurrentRunError as exc:
        print(P.sanitizar(exc), file=sys.stderr)
        return P.EXIT_LOCK
    except Exception as exc:  # noqa: BLE001 — erro de driver carrega a DSN
        print(f"{type(exc).__name__}: {P.sanitizar(exc)}", file=sys.stderr)
        return P.EXIT_FALHA


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
