"""Publicador direto XLSX -> Neon da procedencia `manual_export` (SHOPEE-PRODUCTS-KOKESHI-3).

POR QUE EXISTE
--------------
A Kokeshi nao tem aplicacao Shopee: a unica fonte de Produtos dela e o export
do Seller Center. O caminho antigo (loader -> PostgreSQL local -> `sync_produtos`)
depende de `LOCAL_PG_URL`, de um schema local que nunca recebeu as migrations
023/024 e de um UPSERT que nunca remove chave que sumiu. Este modulo publica o
export DIRETO no Neon, com a MESMA transformacao oficial do loader, reaproveitando
as portas ja testadas de `etl.backfill_shopee_products`.

O QUE ELE GARANTE (e prova em teste)
------------------------------------
- dry-run por padrao. `--apply` exige consentimento proprio e credencial de
  escrita dedicada (as mesmas variaveis do backfill: BACKFILL_NEON_RW_URL etc.);
- grava `source = 'manual_export'` explicitamente em toda linha;
- o DELETE e o backup sao SEMPRE limitados a `source = 'manual_export'` e aos
  pares (marca, competencia) pedidos. Linha `api` nao pode ser apagada: alem do
  predicado, a impressao digital de TODAS as linhas `api` da tabela e conferida
  antes e depois da mutacao, dentro da transacao — divergencia faz rollback;
- recusa marca cuja procedencia ativa em `marts.shopee_product_source_mode` nao
  seja explicitamente `manual_export` (inclusive marca AUSENTE da tabela de modo);
- nesta primeira utilizacao, so a `kokeshi` e aceita (`ALLOWED_BRANDS`);
- so aceita exports de MES INTEIRO numa pasta ISOLADA: os semanais antigos
  congelaram status imaturo, e a ordenacao por janela os faria vencer a
  reexportacao (ver `assert_full_month_windows`);
- ausencia nunca vira zero: competencia sem linha, ou so com GMV zero, e
  RECUSADA em vez de publicada; estado anterior nao lido aparece como N/D;
- backup duravel conferido por checksum, advisory lock de sessao (a MESMA chave
  do backfill: os dois nunca rodam juntos), transacao unica de mutacao,
  reconciliacao antes/depois por competencia, ZERO retry;
- nunca toca `marts.shopee_product_source_mode`.

USO
---
    python -m etl.publish_shopee_manual_export --source-root <pasta isolada> \\
        --scope kokeshi:2026-07 --scope kokeshi:2026-08 ...          # dry-run (Neon read-only)
    ... --offline                                                     # so arquivos, sem banco
    ... --apply                                                       # NAO executado neste gate
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import text as sqlalchemy_text

from etl import backfill_shopee_products as bf
from etl import load_shopee_products as loader

TABLE = bf.TABLE
MODE_TABLE = "marts.shopee_product_source_mode"
SOURCE = "manual_export"
TARGET = bf.TARGET_NEON

#: Primeira utilizacao: so a Kokeshi. Ampliar e decisao explicita, nunca default.
ALLOWED_BRANDS: tuple[str, ...] = ("kokeshi",)

#: Consentimento PROPRIO, diferente do backfill: autorizar um nao autoriza o outro.
CONSENT_ENV = "I_UNDERSTAND_THIS_PUBLISHES_SHOPEE_MANUAL_EXPORT"

#: Mesma chave do backfill: os dois escrevem a mesma tabela e nunca rodam juntos.
LOCK_KEY = bf.ADVISORY_LOCK_KEY

#: Fuso operacional da Torre (mesmo do publisher da API).
OPERATIONAL_TZ = timezone(timedelta(hours=-3))

#: Colunas gravadas. `ingested_at` vem de NOW() do banco; `id` da sequence.
INSERT_COLS: tuple[str, ...] = bf.DATA_COLS + ("source", "source_run_id", "is_partial")
#: Colunas do backup: tudo que a restauracao precisa, inclusive procedencia.
BACKUP_COLS: tuple[str, ...] = bf.DATA_COLS + (
    "source", "source_run_id", "source_captured_at", "is_partial", "ingested_at")

EXIT_OK = bf.EXIT_OK
EXIT_REFUSED = bf.EXIT_VALIDATION_REFUSED
EXIT_ROLLED_BACK = bf.EXIT_ROLLED_BACK
EXIT_INDETERMINATE = bf.EXIT_INDETERMINATE
EXIT_USAGE = bf.EXIT_USAGE


class PublishRefused(bf.BackfillValidationError):
    """Uma porta do publicador reprovou. Nada foi escrito."""


# ---------------------------------------------------------------------------
# Portas offline (zero banco)
# ---------------------------------------------------------------------------

def assert_brands_allowed(scopes: list[tuple[str, str]]) -> None:
    if not scopes:
        raise PublishRefused("nenhum --scope informado (nada foi escrito)")
    fora = sorted({b for b, _ in scopes} - set(ALLOWED_BRANDS))
    if fora:
        raise PublishRefused(
            f"marca(s) fora da allowlist desta operacao: {', '.join(fora)}. "
            f"Permitidas: {', '.join(ALLOWED_BRANDS)} (nada foi escrito)")


def assert_source_root_isolated(root: Path) -> None:
    """A pasta de trabalho `shopee/` tem os exports SEMANAIS antigos. Publicar a
    partir dela (ou de dentro/acima dela) reintroduziria os snapshots imaturos."""
    trabalho = Path(loader.SHOPEE_ROOT).resolve()
    alvo = root.resolve()
    if alvo == trabalho or trabalho in alvo.parents or alvo in trabalho.parents:
        raise PublishRefused(
            "--source-root nao pode ser, conter nem estar dentro da pasta de "
            "trabalho dos exports semanais; use uma pasta NOVA e isolada "
            "(nada foi escrito)")


def _ultimo_dia(ano: int, mes: int) -> date:
    prox = date(ano + (mes // 12), (mes % 12) + 1, 1)
    return prox - timedelta(days=1)


def assert_full_month_windows(brand: str, plan: dict[str, tuple[str, str]],
                              months: list[str], *, today: date) -> dict[str, str]:
    """Cada competencia pedida tem de vir de EXATAMENTE uma janela de mes inteiro.

    Mes fechado: janela `AAAAMM01_<ultimo dia>`. Mes corrente: `AAAAMM01_<D-1 ou
    antes>`, nunca D0 nem futuro. Qualquer janela que nao comece no dia 1, que
    atravesse meses, que pertenca a mes nao pedido ou que repita um mes e
    RECUSADA. E isso que impede os exports semanais: a ordenacao do loader e por
    janela, nao por instante de download, e uma semanal antiga venceria a
    reexportacao nos dias que cobre."""
    janelas = sorted(set(plan.values()))
    por_mes: dict[str, list[tuple[str, str]]] = {}
    for ini, fim in janelas:
        d0 = date(int(ini[:4]), int(ini[4:6]), int(ini[6:]))
        d1 = date(int(fim[:4]), int(fim[4:6]), int(fim[6:]))
        mes = f"{d0:%Y-%m}"
        if d0.day != 1 or (d1.year, d1.month) != (d0.year, d0.month):
            raise PublishRefused(
                f"{brand}: janela {ini}_{fim} nao e de mes inteiro (comeca fora do "
                f"dia 1 ou atravessa meses). Exports semanais sao recusados "
                f"(nada foi escrito)")
        corrente = (d0.year, d0.month) == (today.year, today.month)
        if corrente:
            if d1 >= today:
                raise PublishRefused(
                    f"{brand}: janela {ini}_{fim} do mes corrente vai ate D0 ou "
                    f"alem; use ate D-1 (nada foi escrito)")
        elif d1 != _ultimo_dia(d0.year, d0.month):
            raise PublishRefused(
                f"{brand}: janela {ini}_{fim} de mes fechado nao termina no ultimo "
                f"dia do mes (nada foi escrito)")
        por_mes.setdefault(mes, []).append((ini, fim))

    repetidos = sorted(m for m, js in por_mes.items() if len(js) > 1)
    if repetidos:
        raise PublishRefused(
            f"{brand}: mais de uma janela para {', '.join(repetidos)} (nada foi escrito)")
    sobra = sorted(set(por_mes) - set(months))
    if sobra:
        raise PublishRefused(
            f"{brand}: a pasta tem export de competencia nao pedida: "
            f"{', '.join(sobra)} (nada foi escrito)")
    falta = sorted(set(months) - set(por_mes))
    if falta:
        raise PublishRefused(
            f"{brand}: falta o export de mes inteiro para {', '.join(falta)} "
            f"(nada foi escrito)")
    return {m: f"{js[0][0]}_{js[0][1]}" for m, js in sorted(por_mes.items())}


def assert_scopes_have_value(staging: bf.Staging) -> None:
    """Ausencia nunca vira zero: competencia sem linha, ou so com GMV zero, nao
    e publicada. Publicar substituiria o mart por um 'zero' que e falta de dado."""
    problemas = []
    for brand, month in staging.scopes:
        corte = bf._cut(staging.rows, brand, month)
        if corte.empty:
            problemas.append(f"{brand}:{month} sem nenhuma linha")
        elif bf._dsum(corte, "gmv") <= 0:
            problemas.append(f"{brand}:{month} so com GMV zero ({len(corte)} linhas)")
    if problemas:
        raise PublishRefused(
            "competencia sem valor confiavel — nao se publica zero no lugar de "
            "ausencia: " + "; ".join(problemas) + " (nada foi escrito)")


def mark_partial(staging: bf.Staging, *, today: date) -> pd.DataFrame:
    """`is_partial` = competencia corrente (BRT). Os meses fechados nao sao."""
    rows = staging.rows.copy()
    corrente = date(today.year, today.month, 1)
    rows["is_partial"] = pd.to_datetime(rows["ref_month"]).dt.date == corrente
    return rows


@dataclass
class Prepared:
    staging: bf.Staging
    windows: dict[str, dict[str, str]] = field(default_factory=dict)
    today: date = field(default_factory=lambda: datetime.now(OPERATIONAL_TZ).date())


def prepare(scopes: list[tuple[str, str]], root: Path, *, today: date) -> Prepared:
    """Portas offline + staging com a transformacao OFICIAL do loader."""
    assert_brands_allowed(scopes)
    assert_source_root_isolated(root)
    windows: dict[str, dict[str, str]] = {}
    for brand in sorted({b for b, _ in scopes}):
        files = loader._find_xlsx(root / brand)
        plan = loader._plan_brand_snapshots(brand, files)
        windows[brand] = assert_full_month_windows(
            brand, plan, sorted(m for b, m in scopes if b == brand), today=today)
    staging = bf.build_staging(scopes, root)
    bf.assert_staging_unique(staging)
    bf.assert_staging_within_scope(staging)
    assert_scopes_have_value(staging)
    return Prepared(staging=staging, windows=windows, today=today)


# ---------------------------------------------------------------------------
# Leituras (read-only) e portas que precisam do banco
# ---------------------------------------------------------------------------

def scoped_predicate(scopes: list[tuple[str, str]]) -> tuple[str, dict]:
    """Pares pedidos E procedencia manual. Unico predicado de backup, DELETE e
    contagem: nao existe caminho de escrita sem o filtro de `source`."""
    pred, params = bf._scope_predicate(scopes)
    params = dict(params, src=SOURCE)
    return f"({pred}) AND source = :src", params


def read_source_modes(conn) -> dict[str, str]:
    rows = conn.execute(sqlalchemy_text(f"SELECT brand, source FROM {MODE_TABLE}")).all()
    return {r[0]: r[1] for r in rows}


def assert_brands_read_manual(modes: dict[str, str], brands) -> None:
    """Recusa marca ligada na API e marca AUSENTE da tabela de modo. A leitura da
    tela trata ausencia como manual, mas para ESCREVER a prova tem de ser
    explicita."""
    problemas = []
    for b in sorted(set(brands)):
        modo = modes.get(b)
        if modo is None:
            problemas.append(f"{b}: ausente de {MODE_TABLE}")
        elif modo != SOURCE:
            problemas.append(f"{b}: procedencia ativa '{modo}'")
    if problemas:
        raise PublishRefused(
            "so se publica manual_export para marca que LE manual_export: "
            + "; ".join(problemas) + " (nada foi escrito)")


_BEFORE_COLS = ("ref_month", "brand", "sku_ref", "sku_ref_key", "product_name",
                "variation_name", "gmv", "units_sold", "completed_orders",
                "canceled_orders", "unique_buyers")


def read_before(conn, scopes) -> pd.DataFrame:
    """Estado anterior SO da procedencia manual nos pares pedidos."""
    pred, params = scoped_predicate(scopes)
    rows = conn.execute(sqlalchemy_text(
        f"SELECT {', '.join(_BEFORE_COLS)} FROM {TABLE} WHERE {pred}"), params).mappings().all()
    return pd.DataFrame(list(rows), columns=list(_BEFORE_COLS))


API_FINGERPRINT_COLS: tuple[str, ...] = BACKUP_COLS


def api_fingerprint(conn) -> dict:
    """Contagem + checksum de TODAS as linhas `api` da tabela, em qualquer marca
    e competencia. Mesmo SQL antes e depois: igualdade prova que nada `api` mudou."""
    sql = bf.fingerprint_sql(TABLE, API_FINGERPRINT_COLS, "source = 'api'")
    r = conn.execute(sqlalchemy_text(sql)).mappings().first()
    return {"n": int(r["n"]), "checksum": r["checksum"]}


DAILY_SQL = """
SELECT to_char(date_trunc('month', f.date), 'YYYY-MM') AS mes,
       COALESCE(SUM(f.gmv), 0) AS gmv, MAX(f.date) AS ate
  FROM marts.fact_marketplace_daily_performance f
  JOIN marts.dim_loja l ON l.loja_id = f.loja_id
 WHERE f.marketplace_id = 3 AND l.brand_key = :brand
   AND to_char(f.date, 'YYYY-MM') = ANY(:meses)
 GROUP BY 1
"""


def read_daily_reference(conn, brand: str, months: list[str]) -> dict[str, dict]:
    """GMV da diaria por competencia: so REFERENCIA de maturacao no relatorio."""
    rows = conn.execute(sqlalchemy_text(DAILY_SQL),
                        {"brand": brand, "meses": list(months)}).mappings().all()
    return {r["mes"]: {"gmv": Decimal(str(r["gmv"])), "ate": r["ate"]} for r in rows}


# ---------------------------------------------------------------------------
# Escrita (NAO executada neste gate)
# ---------------------------------------------------------------------------

class ManualExportExecutor:
    """Scoped replace restrito a `source = 'manual_export'`.

    Mesmo protocolo do `ScopedReplaceExecutor` do backfill, com o predicado de
    procedencia em backup, DELETE e contagem, e colunas de procedencia no
    INSERT. Recebe a conexao ja aberta: quem chama decide o banco."""

    def __init__(self, connection, *, run_id: str, clock=None) -> None:
        self.conn = connection
        self.run_id = run_id
        self._clock = clock or datetime.now
        self.backup_table: str | None = None
        self.backup_fingerprint: dict | None = None
        self.emitted: list[str] = []
        self._trans = None

    def _exec(self, sql: str, params: dict | None = None):
        self.emitted.append(sql.strip().split("\n")[0][:160])
        return self.conn.execute(sqlalchemy_text(sql), params or {})

    def backup_scope(self, scopes) -> dict:
        """Backup duravel em transacao PROPRIA, conferido e commitado antes."""
        pred, params = scoped_predicate(scopes)
        nome = bf.backup_table_name(TARGET, self._clock().strftime("%Y%m%d_%H%M%S"))
        cols = ", ".join(BACKUP_COLS)
        trans = self.conn.begin()
        try:
            self._exec(f"CREATE TABLE {nome} AS SELECT {cols} FROM {TABLE} WHERE {pred}", params)
            origem = self._exec(bf.fingerprint_sql(TABLE, BACKUP_COLS, pred), params).mappings().first()
            copia = self._exec(bf.fingerprint_sql(nome, BACKUP_COLS, "TRUE")).mappings().first()
            if int(origem["n"]) != int(copia["n"]) or origem["checksum"] != copia["checksum"]:
                raise PublishRefused("backup NAO confere com a origem; nada foi apagado")
            trans.commit()
        except BaseException:
            try:
                trans.rollback()
            except Exception:
                pass
            raise
        self.backup_table = nome
        self.backup_fingerprint = {"rows": int(origem["n"]), "checksum": origem["checksum"]}
        return {"table": nome, **self.backup_fingerprint,
                "retention_days": bf.BACKUP_RETENTION_DAYS}

    def begin(self):
        self._trans = self.conn.begin()

    def delete_scope(self, scopes) -> int:
        if not self.backup_table:
            raise PublishRefused("DELETE sem backup commitado: sequencia invalida")
        pred, params = scoped_predicate(scopes)
        return int(self._exec(f"DELETE FROM {TABLE} WHERE {pred}", params).rowcount)

    def insert_rows(self, rows: pd.DataFrame) -> None:
        registros = []
        for r in rows[list(bf.DATA_COLS) + ["is_partial"]].to_dict("records"):
            r["source"] = SOURCE
            r["source_run_id"] = self.run_id
            r["is_partial"] = bool(r["is_partial"])
            registros.append(r)
        registros = bf.normalize_records(registros)
        bf.assert_bind_params_clean(registros)
        cols = INSERT_COLS + ("ingested_at",)
        valores = ", ".join("NOW()" if c == "ingested_at" else f":{c}" for c in cols)
        sql = f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({valores})"
        self.emitted.append(sql[:160])
        self.conn.execute(sqlalchemy_text(sql), registros)

    def count_scope(self, scopes) -> int:
        pred, params = scoped_predicate(scopes)
        return int(self._exec(f"SELECT count(*) FROM {TABLE} WHERE {pred}", params).scalar() or 0)

    def count_all(self) -> int:
        return int(self._exec(f"SELECT count(*) FROM {TABLE}").scalar() or 0)

    def commit(self):
        self._trans.commit()
        self._trans = None

    def rollback(self):
        if self._trans is not None:
            self._trans.rollback()
            self._trans = None


def publish_scoped(staging: bf.Staging, rows: pd.DataFrame, *, executor) -> dict:
    """Backup -> transacao unica (DELETE manual + INSERT) -> invariantes -> commit.

    Invariantes conferidos DENTRO da transacao, antes do commit:
      1. linhas manuais no escopo == linhas da staging;
      2. total da tabela == antes - apagadas + inseridas (nada fora do escopo mudou);
      3. impressao digital das linhas `api` identica a de antes.
    Qualquer divergencia -> rollback. ZERO retry."""
    scopes = list(staging.scopes)
    backup = executor.backup_scope(scopes)
    executor.begin()
    try:
        api_antes = api_fingerprint(executor.conn)
        total_antes = executor.count_all()
        apagadas = executor.delete_scope(scopes)
        executor.insert_rows(rows)
        gravadas = executor.count_scope(scopes)
        if gravadas != len(rows):
            raise PublishRefused(f"contagem pos-insert divergente ({gravadas} != {len(rows)})")
        total_depois = executor.count_all()
        if total_depois != total_antes - apagadas + len(rows):
            raise PublishRefused(
                f"linhas fora do escopo mudaram (total {total_antes} -> {total_depois}, "
                f"apagadas {apagadas}, inseridas {len(rows)})")
        api_depois = api_fingerprint(executor.conn)
        if api_depois != api_antes:
            raise PublishRefused("linhas `api` mudaram durante a publicacao: rollback")
    except BaseException:
        try:
            executor.rollback()
        except Exception:
            return {"code": EXIT_INDETERMINATE, "backup": backup}
        if isinstance(sys.exc_info()[1], (KeyboardInterrupt, SystemExit)):
            raise
        return {"code": EXIT_ROLLED_BACK, "backup": backup, "erro": bf._sanitize(sys.exc_info()[1])}
    try:
        executor.commit()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return {"code": EXIT_INDETERMINATE, "backup": backup}
    return {"code": EXIT_OK, "backup": backup, "apagadas": apagadas,
            "inseridas": len(rows), "api": api_antes}


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------

def format_report(prep: Prepared, rec: bf.Reconciliation, *, mode: str,
                  daily: dict[str, dict] | None = None, modes: dict | None = None,
                  api_rows_in_scope: int | None = None) -> str:
    L = [f"=== PUBLICADOR manual_export {TABLE} — {mode.upper()} ==="]
    L.append("estado anterior: " + ("lido do Neon (read-only, so source=manual_export)"
                                    if rec.compared else "NAO lido — antes e deltas = N/D"))
    for brand, wins in sorted(prep.windows.items()):
        modo = (modes or {}).get(brand, bf.ND) if modes is not None else bf.ND
        L.append(f"marca {brand}: procedencia ativa = {modo}; janelas aceitas: "
                 + ", ".join(f"{m}={w}" for m, w in wins.items()))
    if api_rows_in_scope is not None:
        L.append(f"linhas `api` nos pares pedidos: {api_rows_in_scope} (nao sao tocadas)")
    L.append("competencia | linhas antes -> depois | GMV antes -> depois | delta | "
             "chaves +/- | parcial | GMV diaria (referencia)")
    corrente = date(prep.today.year, prep.today.month, 1).strftime("%Y-%m")
    for (brand, month), s in sorted(rec.per_scope.items()):
        delta = (bf.ND if s["gmv_before"] is None
                 else bf._fmt((s["gmv_after"] - s["gmv_before"]).quantize(Decimal("0.01"))))
        ref = (daily or {}).get(month)
        ref_txt = bf.ND if ref is None else f"{bf._fmt(ref['gmv'])} (ate {ref['ate']})"
        L.append(f"{brand}:{month} | {bf._fmt(s['rows_before'])} -> {s['rows_after']} | "
                 f"{bf._fmt(s['gmv_before'])} -> {bf._fmt(s['gmv_after'])} | {delta} | "
                 f"+{bf._fmt(s['keys_added'])}/-{bf._fmt(s['keys_removed'])} | "
                 f"{'sim' if month == corrente else 'nao'} | {ref_txt}")
    L.append("GMV da diaria e so referencia de maturacao: populacoes diferentes, "
             "a razao NAO e percentual de conclusao.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m etl.publish_shopee_manual_export",
        description="Publica exports manuais Shopee (so manual_export) direto no Neon. "
                    "Dry-run por padrao.")
    p.add_argument("--scope", action="append", metavar="marca:AAAA-MM", required=True)
    p.add_argument("--source-root", required=True,
                   help="Pasta NOVA e isolada com <marca>/Order.all.*.xlsx de mes inteiro.")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--offline", action="store_true",
                   help="So arquivos e memoria; nenhum banco. Antes e deltas = N/D.")
    m.add_argument("--apply", action="store_true",
                   help="Escreve. Exige consentimento, credencial dedicada e todas as portas.")
    return p


def _today() -> date:
    return datetime.now(OPERATIONAL_TZ).date()


def run_dry(args, *, env=None, engine_factory=None, today: date | None = None) -> int:
    env = os.environ if env is None else env
    today = today or _today()
    try:
        scopes = bf.parse_scopes(args.scope)
        root = bf.resolve_source_root(args.source_root)
        prep = prepare(scopes, root, today=today)
    except (PublishRefused, bf.BackfillValidationError,
            loader.ShopeeSnapshotError, loader.ShopeeProductInputError) as e:
        print(f"RECUSADO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_REFUSED
    except bf.BackfillUsageError as e:
        print(f"ERRO DE USO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE

    if args.offline:
        print(format_report(prep, bf.reconcile(None, prep.staging), mode="offline-dry-run"))
        return EXIT_OK

    try:
        url = bf.resolve_target_url(TARGET, env=env)
        engine = (engine_factory or bf._default_readonly_engine)(url, readonly=True)
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            bf.assert_target_identity(conn, TARGET, env=env)
            modes = read_source_modes(conn)
            assert_brands_read_manual(modes, {b for b, _ in scopes})
            before = read_before(conn, scopes)
            pred, params = bf._scope_predicate(scopes)
            api_n = int(conn.execute(sqlalchemy_text(
                f"SELECT count(*) FROM {TABLE} WHERE ({pred}) AND source = 'api'"),
                params).scalar() or 0)
            daily = {}
            for brand in sorted({b for b, _ in scopes}):
                daily.update(read_daily_reference(conn, brand, [m for b, m in scopes if b == brand]))
            conn.rollback()
    except (PublishRefused, bf.BackfillValidationError) as e:
        print(f"RECUSADO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_REFUSED
    except bf.BackfillUsageError as e:
        print(f"ERRO DE USO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE

    rec = bf.reconcile(before, prep.staging, compared_target=TARGET)
    print(format_report(prep, rec, mode="dry-run", daily=daily, modes=modes,
                        api_rows_in_scope=api_n))
    return EXIT_OK


def run_apply(args, *, env=None, engine_factory=None, today: date | None = None) -> int:
    """Caminho de escrita. NAO executado no gate KOKESHI-3."""
    env = os.environ if env is None else env
    today = today or _today()
    try:
        if env.get(CONSENT_ENV) != "1":
            raise PublishRefused(f"--apply exige {CONSENT_ENV}=1 (nada foi escrito)")
        scopes = bf.parse_scopes(args.scope)
        root = bf.resolve_source_root(args.source_root)
        url = bf.resolve_write_url(TARGET, env=env)
        prep = prepare(scopes, root, today=today)
    except (PublishRefused, bf.BackfillValidationError,
            loader.ShopeeSnapshotError, loader.ShopeeProductInputError) as e:
        print(f"APPLY RECUSADO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_REFUSED
    except bf.BackfillUsageError as e:
        print(f"ERRO DE USO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE

    try:
        engine = (engine_factory or bf._default_writable_engine)(url)
        conn = engine.connect()
    except Exception as e:
        print(f"ERRO DE CONFIGURACAO: conexao gravavel: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE

    lock = False
    try:
        bf.assert_target_identity(conn, TARGET, env=env)
        bf.assert_writable_primary(conn, TARGET)
        bf.assert_sequence_privilege(conn, TARGET)
        bf.assert_ssl_required(conn, TARGET)
        bf.acquire_advisory_lock(conn, key=LOCK_KEY)
        lock = True
        modes = read_source_modes(conn)
        assert_brands_read_manual(modes, {b for b, _ in scopes})
        before = read_before(conn, scopes)
        bf.close_preflight_transaction(conn)
        rec = bf.reconcile(before, prep.staging, compared_target=TARGET)
        print(format_report(prep, rec, mode="apply (antes)", modes=modes))
        run_id = uuid.uuid4().hex
        res = publish_scoped(prep.staging, mark_partial(prep.staging, today=today),
                             executor=ManualExportExecutor(conn, run_id=run_id))
        print(f"run_id={run_id} resultado={res}", file=sys.stderr)
        return res["code"]
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        print(f"APPLY RECUSADO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        if lock:
            try:
                bf.release_advisory_lock(conn, key=LOCK_KEY)
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_apply(args) if args.apply else run_dry(args)


if __name__ == "__main__":
    raise SystemExit(main())
