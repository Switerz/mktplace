"""Backfill por escopo de marts.fact_shopee_product_monthly (Gate SH-API-2B).

POR QUE UM CAMINHO DEDICADO, e nao o loader normal
---------------------------------------------------
O loader (`etl.load_shopee_products`) publica com
`INSERT ... ON CONFLICT (ref_month, brand, sku_ref_key, product_name) DO UPDATE`.
Isso atualiza chave existente e insere chave nova, mas **nunca remove** uma
chave que deixou de existir. Depois da deduplicacao por snapshot vigente,
algumas chaves desaparecem legitimamente (um SKU cujo unico pedido vinha de um
snapshot superado). Com UPSERT puro, essas chaves ficam no mart com o valor
antigo — residuo silencioso, que soma no total da tela de Produtos. O mesmo
risco existe em `pipelines.sync_produtos` ao propagar local -> Neon.

A correcao exige **scoped replace**: dentro de uma unica transacao, apagar
somente os pares `(brand, ref_month)` autorizados e reinserir integralmente a
staging daquele escopo. Nunca `TRUNCATE` da tabela inteira.

MODOS
-----
`--dry-run` (padrao): le arquivos, monta a staging em memoria, consulta o
estado atual por conexao **estritamente read-only** e imprime a reconciliacao.
Nunca abre conexao gravavel, nunca emite CREATE/INSERT/UPDATE/DELETE/TRUNCATE.

`--apply`: exige `I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES=1`. NAO foi
executado no Gate SH-API-2B.

EXIT CODES
----------
0  sucesso (dry-run concluido, ou apply commitado e reconciliado)
2  validacao recusada — nada foi escrito
3  escrita falhou e rollback CONFIRMADO
4  estado INDETERMINADO — o commit pode ou nao ter ocorrido; exige inspecao
5  erro de uso (escopo vazio/invalido, source-root inexistente)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy import text as sqlalchemy_text

from etl import load_shopee_products as loader

# Decimal, nunca float, em tudo que soma dinheiro: a reconciliacao compara
# centavos e um float acumulado em 200 linhas ja' erra na segunda casa.
from decimal import Decimal

EXIT_OK = 0
EXIT_VALIDATION_REFUSED = 2
EXIT_ROLLED_BACK = 3
EXIT_INDETERMINATE = 4
EXIT_USAGE = 5

TABLE = "marts.fact_shopee_product_monthly"
KEY_COLS = ("ref_month", "brand", "sku_ref_key", "product_name")
LOCK_NAME = "shopee_product_monthly_backfill.lock"

_SCOPE_RE = re.compile(r"^(?P<brand>[a-z0-9_]+):(?P<month>\d{4}-\d{2})$")


class BackfillUsageError(ValueError):
    """Escopo/source-root invalido. Detectado antes de qualquer conexao."""


class BackfillValidationError(ValueError):
    """Uma validacao da staging ou da reconciliacao reprovou. Nada e' escrito."""


# ---------------------------------------------------------------------------
# Escopo
# ---------------------------------------------------------------------------

def parse_scopes(raw: list[str] | None) -> list[tuple[str, str]]:
    """`['apice:2026-05', ...]` -> `[('apice','2026-05'), ...]`, ordenado e
    sem repeticao. Escopo vazio, marca fora de BRANDS ou mes malformado sao
    recusados (nunca normalizados em silencio)."""
    if not raw:
        raise BackfillUsageError("escopo vazio: informe ao menos um --scope brand:YYYY-MM")

    seen: dict[tuple[str, str], int] = {}
    for item in raw:
        m = _SCOPE_RE.match((item or "").strip())
        if m is None:
            raise BackfillUsageError(f"escopo malformado (esperado brand:YYYY-MM): {item!r}")
        brand, month = m.group("brand"), m.group("month")
        if brand not in loader.BRANDS:
            raise BackfillUsageError(f"marca desconhecida no escopo: {brand}")
        mm = int(month[5:7])
        if not 1 <= mm <= 12:
            raise BackfillUsageError(f"mes invalido no escopo: {month}")
        seen[(brand, month)] = seen.get((brand, month), 0) + 1
    return sorted(seen)


def resolve_source_root(raw: str | None) -> Path:
    """`--source-root` explicito ou o `SHOPEE_ROOT` do loader. Diretorio
    inexistente falha aqui, antes de qualquer conexao."""
    root = Path(raw) if raw else Path(loader.SHOPEE_ROOT)
    if not root.is_dir():
        raise BackfillUsageError(f"source-root inexistente ou nao e' diretorio: {root.name}")
    return root


# ---------------------------------------------------------------------------
# Staging (memoria)
# ---------------------------------------------------------------------------

@dataclass
class Staging:
    rows: pd.DataFrame
    scopes: list[tuple[str, str]]
    file_hashes: dict[str, str] = field(default_factory=dict)
    accepted: dict[str, list[str]] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)
    winning_snapshots: dict[str, str] = field(default_factory=dict)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_staging(scopes: list[tuple[str, str]], source_root: Path) -> Staging:
    """Monta a staging dos escopos pedidos — SO memoria e arquivos.

    Reaproveita integralmente a regra fail-closed do loader
    (`_plan_brand_snapshots` + `_select_current_snapshot` + `_aggregate` +
    `_collapse_variation_collisions`): a deduplicacao e' a MESMA, e um arquivo
    de nome desconhecido aborta aqui do mesmo jeito."""
    brands = sorted({b for b, _ in scopes})
    months = {b: sorted(m for bb, m in scopes if bb == b) for b in brands}

    frames, hashes, accepted, winners = [], {}, {}, {}
    original_root = loader.SHOPEE_ROOT
    try:
        loader.SHOPEE_ROOT = source_root
        for brand in brands:
            files = loader._find_xlsx(source_root / brand)
            plan = loader._plan_brand_snapshots(brand, files)
            accepted[brand] = sorted(plan)
            for f in files:
                if f.name in plan:
                    hashes[f"{brand}/{f.name}"] = _sha(f)
            df = loader._load_brand(brand)
            df = df[df["ref_month"].dt.strftime("%Y-%m").isin(months[brand])]
            if not df.empty:
                agg = loader._aggregate(df)
                frames.append(loader._collapse_variation_collisions(agg))
            winners[brand] = f"{len(set(plan.values()))} snapshots logicos"
    finally:
        loader.SHOPEE_ROOT = original_root

    cols = ["brand", "ref_month", "sku_ref", "sku_ref_key", "product_name",
            "variation_name", "gmv", "units_sold", "completed_orders",
            "canceled_orders", "cancel_rate_pct", "unique_buyers", "avg_price"]
    rows = (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame(columns=cols))
    return Staging(rows=rows, scopes=scopes, file_hashes=hashes,
                   accepted=accepted, winning_snapshots=winners)


def assert_staging_unique(staging: Staging) -> None:
    """Unicidade na chave REAL da tabela. Sem isso, o INSERT integral do
    scoped replace violaria a UNIQUE e a transacao inteira cairia."""
    if staging.rows.empty:
        return
    dup = staging.rows.duplicated(subset=list(KEY_COLS), keep=False)
    n = int(dup.sum())
    if n:
        grupos = int(staging.rows.loc[dup].groupby(list(KEY_COLS)).ngroups)
        raise BackfillValidationError(
            f"staging viola a chave real {KEY_COLS}: {n} linhas em {grupos} grupos "
            f"duplicados (nada foi escrito)"
        )


def assert_staging_within_scope(staging: Staging) -> None:
    """Nenhuma linha da staging pode cair fora dos pares autorizados — a
    garantia de que o DELETE por escopo cobre tudo que o INSERT vai gravar."""
    if staging.rows.empty:
        return
    pares = set(zip(staging.rows["brand"],
                    pd.to_datetime(staging.rows["ref_month"]).dt.strftime("%Y-%m")))
    fora = sorted(pares - set(staging.scopes))
    if fora:
        raise BackfillValidationError(
            f"staging contem {len(fora)} par(es) (brand, ref_month) fora do escopo "
            f"autorizado (nada foi escrito)"
        )


# ---------------------------------------------------------------------------
# Reconciliacao
# ---------------------------------------------------------------------------

ND = "N/D"   # estado anterior NAO consultado — nunca confundir com zero


@dataclass
class Reconciliation:
    """`*_before` e' `None` quando NENHUM banco foi consultado (modo offline).

    `None` significa "nao sei", e as propriedades de delta devolvem `None` —
    jamais zero. Apresentar `before=0` sem ter lido banco foi o defeito
    corrigido no Gate SH-API-2B-R."""
    after_rows: int
    gmv_after: Decimal
    units_after: int
    completed_after: int
    canceled_after: int
    before_rows: int | None = None
    keys_added: int | None = None
    keys_updated: int | None = None
    keys_removed: int | None = None
    gmv_before: Decimal | None = None
    units_before: int | None = None
    completed_before: int | None = None
    canceled_before: int | None = None
    compared_target: str | None = None      # 'local' | 'neon' | None (offline)
    per_scope: dict[tuple[str, str], dict] = field(default_factory=dict)

    @property
    def compared(self) -> bool:
        return self.compared_target is not None

    @property
    def gmv_delta(self) -> Decimal | None:
        if self.gmv_before is None:
            return None
        return (self.gmv_after - self.gmv_before).quantize(Decimal("0.01"))

    @property
    def units_delta(self) -> int | None:
        if self.units_before is None:
            return None
        return self.units_after - self.units_before


def _keyset(df: pd.DataFrame) -> set[tuple]:
    if df.empty:
        return set()
    m = pd.to_datetime(df["ref_month"]).dt.strftime("%Y-%m")
    return set(zip(m, df["brand"], df["sku_ref_key"].fillna(""),
                   df["product_name"]))


def _dsum(df: pd.DataFrame, col: str) -> Decimal:
    """Soma em Decimal, sem passar por float em nenhum ponto."""
    if df.empty or col not in df.columns:
        return Decimal("0.00")
    total = Decimal("0")
    for v in df[col].tolist():
        if v is None or (isinstance(v, float) and v != v):
            continue
        total += v if isinstance(v, Decimal) else Decimal(str(v))
    return total.quantize(Decimal("0.01"))


def _isum(df: pd.DataFrame, col: str) -> int:
    if df.empty or col not in df.columns:
        return 0
    return int(sum(0 if v is None or (isinstance(v, float) and v != v) else int(v)
                   for v in df[col].tolist()))


def _cut(df: pd.DataFrame, brand: str, month: str) -> pd.DataFrame:
    if df.empty:
        return df
    m = pd.to_datetime(df["ref_month"]).dt.strftime("%Y-%m")
    return df[(df["brand"] == brand) & (m == month)]


def reconcile(before: pd.DataFrame | None, staging: Staging, *,
              compared_target: str | None = None) -> Reconciliation:
    """Compara o estado do alvo com a staging. Puro.

    `before=None` significa **nao consultado** (modo offline): todos os campos
    de estado anterior e de delta ficam `None`/N/D. Passar um DataFrame vazio
    e' diferente — significa "consultei e o escopo esta' vazio de verdade"."""
    after = staging.rows
    rec = Reconciliation(
        after_rows=len(after),
        gmv_after=_dsum(after, "gmv"),
        units_after=_isum(after, "units_sold"),
        completed_after=_isum(after, "completed_orders"),
        canceled_after=_isum(after, "canceled_orders"),
    )
    if before is None:
        # Offline: nada de banco foi lido. So' o candidato "depois" existe.
        for brand, month in staging.scopes:
            a = _cut(after, brand, month)
            rec.per_scope[(brand, month)] = {
                "rows_before": None, "rows_after": len(a),
                "gmv_before": None, "gmv_after": _dsum(a, "gmv"),
                "units_before": None, "units_after": _isum(a, "units_sold"),
                "keys_removed": None, "keys_added": None,
            }
        return rec

    kb, ka = _keyset(before), _keyset(after)
    rec.compared_target = compared_target
    rec.before_rows = len(before)
    rec.keys_added = len(ka - kb)
    rec.keys_updated = len(ka & kb)
    rec.keys_removed = len(kb - ka)
    rec.gmv_before = _dsum(before, "gmv")
    rec.units_before = _isum(before, "units_sold")
    rec.completed_before = _isum(before, "completed_orders")
    rec.canceled_before = _isum(before, "canceled_orders")
    for brand, month in staging.scopes:
        b, a = _cut(before, brand, month), _cut(after, brand, month)
        rec.per_scope[(brand, month)] = {
            "rows_before": len(b), "rows_after": len(a),
            "gmv_before": _dsum(b, "gmv"), "gmv_after": _dsum(a, "gmv"),
            "units_before": _isum(b, "units_sold"), "units_after": _isum(a, "units_sold"),
            "keys_removed": len(_keyset(b) - _keyset(a)),
            "keys_added": len(_keyset(a) - _keyset(b)),
        }
    return rec


def assert_reconciliation_sane(rec: Reconciliation, *,
                              allow_empty_staging: bool = False) -> None:
    """Guardas que reprovam ANTES de qualquer escrita."""
    if not rec.compared:
        raise BackfillValidationError(
            "reconciliacao nao comparada com banco: nao e' possivel validar "
            "'staging vazia com destino nao vazio' sem ler o alvo (use --dry-run "
            "com --target, ou --offline-dry-run e aceite que nao ha' validacao "
            "de destino)"
        )
    if rec.after_rows == 0 and (rec.before_rows or 0) > 0 and not allow_empty_staging:
        raise BackfillValidationError(
            f"staging vazia com destino nao vazio ({rec.before_rows} linhas): "
            f"apagaria o escopo inteiro. Exige --allow-empty-staging explicito "
            f"(nada foi escrito)"
        )


# ---------------------------------------------------------------------------
# Alvo explicito: LOCAL x NEON — nunca inferido de um DATABASE_URL ambiguo
# ---------------------------------------------------------------------------
#
# Os dois destinos NAO podem divergir: o PostgreSQL local e' alimentado pelos
# XLSX e o Neon e' alimentado por `pipelines.sync_produtos` a partir do local.
# Ler (ou escrever) o alvo errado por acidente e' o modo de falha mais caro
# desta ferramenta, entao o alvo e' argumento OBRIGATORIO e cada alvo tem sua
# PROPRIA variavel de ambiente. `DATABASE_URL` generico e' recusado de
# proposito: nenhuma decisao silenciosa sobre qual banco esta' sendo lido.

TARGET_LOCAL = "local"
TARGET_NEON = "neon"
TARGETS = (TARGET_LOCAL, TARGET_NEON)

_TARGET_ENV = {
    TARGET_LOCAL: "BACKFILL_LOCAL_RO_URL",
    TARGET_NEON: "BACKFILL_NEON_RO_URL",
}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def resolve_target_url(target: str, *, env=None) -> str:
    """URL read-only do alvo pedido, validada por CLASSE DE HOST.

    `local` exige host em localhost/127.0.0.1/::1; `neon` exige host remoto.
    Isso impede que a variavel do Neon aponte para o local (ou vice-versa) e
    que um alvo seja lido pensando que era o outro. Nunca devolve nem imprime
    a URL em mensagem de erro — so' o nome da variavel e a classe do host."""
    env = os.environ if env is None else env
    if target not in TARGETS:
        raise BackfillUsageError(f"alvo desconhecido: {target!r} (use {'|'.join(TARGETS)})")

    var = _TARGET_ENV[target]
    url = env.get(var, "")
    if not url:
        raise BackfillUsageError(
            f"alvo '{target}' exige a variavel {var} definida explicitamente "
            f"(DATABASE_URL generico e' recusado de proposito)"
        )
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        raise BackfillUsageError(f"{var} sem host reconhecivel")
    is_local = host in _LOCAL_HOSTS
    if target == TARGET_LOCAL and not is_local:
        raise BackfillUsageError(
            f"{var} aponta para host REMOTO, mas o alvo pedido foi 'local' "
            f"(nada foi lido)"
        )
    if target == TARGET_NEON and is_local:
        raise BackfillUsageError(
            f"{var} aponta para LOCALHOST, mas o alvo pedido foi 'neon' "
            f"(nada foi lido)"
        )
    return url


# ---------------------------------------------------------------------------
# F4 — preflight de identidade do destino (read-only)
# ---------------------------------------------------------------------------
#
# Classe de host (localhost x remoto) NAO prova identidade: qualquer Postgres
# remoto passaria por "neon", e qualquer Postgres em localhost passaria por
# "local". O preflight abaixo exige DUAS evidencias independentes, ambas
# read-only, e nenhuma delas e' impressa:
#
#   1. nome do banco == o nome que o operador declarou esperar
#      (BACKFILL_LOCAL_EXPECT_DB / BACKFILL_NEON_EXPECT_DB). Sem a variavel, o
#      alvo e' BLOQUEADO — nunca adivinhado.
#   2. impressao digital estrutural POSITIVA: o schema `marts` tem de conter
#      `fact_shopee_product_monthly` (a tabela que o backfill toca). Um banco
#      sem ela nao e' destino valido, seja qual for o nome.
#
# REGRA REMOVIDA no Gate SH-API-2C-R, por ter sido REFUTADA por medicao:
# a versao anterior exigia que o LOCAL **nao** tivesse
# `marts.fact_marketplace_daily_performance`, presumindo que o PG local so'
# tem o que o DDL de load_shopee_products cria. Falso: o PG local real tem 16
# tabelas em `marts` (10 `fact_*`), incluindo a de serving. A regra rejeitaria
# o banco local verdadeiro. Discriminadores que a medicao sustenta:
#   - nome do banco declarado (EXPECT_DB)  <- prova primaria
#   - classe de host (pre-filtro)
#   - SSL obrigatorio no remoto
#   - escala do schema, apenas como SINAL (medido: 123 schemas / 54 tabelas em
#     `marts` no serving, contra 9 / 16 no local) — sinal, nunca gate, porque
#     contagem de tabela muda com qualquer migration.
#
# Drift conhecido entre os dois destinos (medido): `marts.fact_shopee_product_monthly`
# tem 15 colunas no serving e 14 no local — o serving tem `ingested_at` a mais.
# Qualquer propagacao local -> Neon precisa ser explicita em colunas; um
# `INSERT ... SELECT *` desalinharia.
#
# Nenhuma mensagem de erro contem host, URL, usuario, senha nem o nome do banco.

_EXPECT_DB_ENV = {
    TARGET_LOCAL: "BACKFILL_LOCAL_EXPECT_DB",
    TARGET_NEON: "BACKFILL_NEON_EXPECT_DB",
}

# EXISTENCIA via pg_catalog, NAO via information_schema: medido no Gate
# ADMIN-SH-RO-1 — `information_schema.tables` so' lista o que a role corrente
# pode acessar, entao uma role de MENOR PRIVILEGIO (SELECT em uma unica tabela,
# que e' exatamente o que queremos) nao enxerga a tabela de serving e o
# fingerprint reprovaria o banco certo. `pg_class` responde existencia
# independentemente de privilegio, que e' a pergunta que estamos fazendo.
IDENTITY_SQL = """
SELECT current_database() AS db,
       (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'marts' AND c.relname = 'fact_shopee_product_monthly'
           AND c.relkind IN ('r','p','v','m')) AS tem_produtos,
       (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'marts' AND c.relname = 'fact_marketplace_daily_performance'
           AND c.relkind IN ('r','p','v','m')) AS tem_serving
"""


class BackfillIdentityError(BackfillUsageError):
    """A identidade do destino nao foi comprovada. Nada e' lido nem escrito."""


def assert_target_identity(conn, target: str, *, env=None) -> dict:
    """Preflight read-only de identidade. Levanta se a prova falhar.

    Devolve so' o veredito, nunca o nome do banco — quem chama nao precisa
    dele e o relatorio nao deve carrega-lo."""
    env = os.environ if env is None else env
    var = _EXPECT_DB_ENV[target]
    esperado = (env.get(var) or "").strip()
    if not esperado:
        raise BackfillIdentityError(
            f"identidade do alvo '{target}' NAO comprovada: defina {var} com o "
            f"nome do banco esperado. Classe de host (local x remoto) nao e' "
            f"prova suficiente (nada foi lido)"
        )

    row = conn.execute(sqlalchemy_text(IDENTITY_SQL)).mappings().first()
    if row is None:
        raise BackfillIdentityError(
            f"preflight de identidade do alvo '{target}' nao retornou linha")

    if row["db"] != esperado:
        raise BackfillIdentityError(
            f"identidade do alvo '{target}' RECUSADA: o banco conectado nao e' o "
            f"declarado em {var} (nomes omitidos de proposito; nada foi lido)"
        )

    tem_produtos = int(row["tem_produtos"]) > 0
    tem_serving = int(row["tem_serving"]) > 0

    if not tem_produtos:
        raise BackfillIdentityError(
            f"identidade do alvo '{target}' RECUSADA: o banco nao contem "
            f"marts.fact_shopee_product_monthly (nada foi lido)"
        )
    if target == TARGET_NEON and not tem_serving:
        raise BackfillIdentityError(
            "identidade do alvo 'neon' RECUSADA: o banco nao contem "
            "marts.fact_marketplace_daily_performance, entao nao e' o banco de "
            "serving esperado (nada foi lido)"
        )
    # NAO existe regra "local nao pode ter a tabela de serving": medido no
    # Gate SH-API-2C-R, o local real TEM. Ver comentario do bloco acima.
    return {"target": target, "identity_proven": True,
            "fingerprint": {"produtos": tem_produtos, "serving": tem_serving}}


# ---------------------------------------------------------------------------
# Leitura read-only PRODUTIVA do estado atual
# ---------------------------------------------------------------------------

_SELECT_COLS = ("ref_month", "brand", "sku_ref", "sku_ref_key", "product_name",
                "variation_name", "gmv", "units_sold", "completed_orders",
                "canceled_orders", "unique_buyers")


def build_scope_query(scopes: list[tuple[str, str]]) -> tuple[str, dict]:
    """SQL + parametros para ler exatamente os pares `(brand, ref_month)`.

    Marca e competencia NUNCA sao interpoladas no texto: entram como bind
    params (`:b0`, `:m0`, ...). A contagem de placeholders varia com o numero
    de escopos — isso e' estrutura, nao dado. O predicado usa comparacao de
    TUPLA, entao nunca produz o produto cartesiano que `brand = ANY(...) AND
    mes = ANY(...)` produziria."""
    if not scopes:
        raise BackfillUsageError("build_scope_query exige ao menos um escopo")
    pares, params = [], {}
    for i, (brand, month) in enumerate(scopes):
        pares.append(f"(:b{i}, :m{i})")
        params[f"b{i}"] = brand
        params[f"m{i}"] = month
    sql = (
        f"SELECT {', '.join(_SELECT_COLS)}\n"
        f"  FROM {TABLE}\n"
        f" WHERE (brand, to_char(ref_month, 'YYYY-MM')) IN ({', '.join(pares)})"
    )
    return sql, params


def read_current_scope(scopes, *, target, engine_factory=None, env=None):
    """Estado atual dos escopos no alvo, por conexao ESTRITAMENTE read-only.

    Implementacao PRODUTIVA: monta a query parametrizada, abre a engine com
    `postgresql_readonly=True`, marca a transacao com `SET TRANSACTION READ
    ONLY` e devolve um DataFrame preservando `Decimal` (nenhuma conversao
    antecipada para float — arredondamento de centavo em GMV e' exatamente o
    que nao se pode perder numa reconciliacao).

    `engine_factory` existe para os testes injetarem um fake; em producao o
    default usa SQLAlchemy de verdade."""
    url = resolve_target_url(target, env=env)
    sql, params = build_scope_query(scopes)
    factory = engine_factory or _default_readonly_engine

    engine = factory(url, readonly=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        # F4: identidade comprovada ANTES de qualquer leitura de dado.
        assert_target_identity(conn, target, env=env)
        rows = conn.execute(sqlalchemy_text(sql), params).mappings().all()

    df = pd.DataFrame(list(rows), columns=list(_SELECT_COLS))
    if df.empty:
        return df
    # Guarda: nenhuma linha fora do escopo pedido pode ter voltado.
    mes = pd.to_datetime(df["ref_month"]).dt.strftime("%Y-%m")
    fora = set(zip(df["brand"], mes)) - set(scopes)
    if fora:
        raise BackfillValidationError(
            f"consulta devolveu {len(fora)} par(es) (brand, ref_month) fora do "
            f"escopo pedido — leitura descartada"
        )
    return df


def _default_readonly_engine(url: str, *, readonly: bool):  # pragma: no cover
    """Engine read-only real. Coberto por teste de contrato (kwargs), nao por
    execucao contra banco: este gate nao conecta a banco nenhum."""
    from sqlalchemy import create_engine
    if not readonly:
        raise BackfillValidationError("este caminho so' abre conexao read-only")
    return create_engine(url).execution_options(postgresql_readonly=True)


# ---------------------------------------------------------------------------
# Scoped replace (NAO executado neste gate)
# ---------------------------------------------------------------------------

def plan_scoped_replace(staging: Staging) -> dict:
    """Descreve, sem executar, o que o apply faria. Usado no dry-run e como
    contrato dos testes."""
    return {
        "table": TABLE,
        "scopes": list(staging.scopes),
        "steps": [
            "conn.begin() — transacao SQLAlchemy, nunca 'BEGIN' textual",
            f"CREATE TABLE {BACKUP_PREFIX}<timestamp> AS SELECT * FROM {TABLE} "
            f"WHERE (brand, mes) IN escopos   -- tabela DURAVEL, nunca TEMP",
            f"DELETE FROM {TABLE} WHERE (brand, mes) IN escopos   -- nunca TRUNCATE",
            f"INSERT INTO {TABLE} (...) VALUES (...)  -- UM executemany, insercao integral",
            "validar: contagem, unicidade da chave real, nenhuma chave fora do escopo",
            "trans.commit()  ou  trans.rollback() integral se qualquer validacao falhar",
        ],
        "never": ["TEMP TABLE (morre com a sessao; nao e' backup operacional)",
                  "TRUNCATE", "DROP", "DELETE sem filtro de escopo",
                  "UPSERT como unico mecanismo",
                  "BEGIN/COMMIT/ROLLBACK como SQL textual"],
        "rows_to_insert": len(staging.rows),
    }


# Excecoes OPERACIONAIS que o caminho de escrita sabe tratar. KeyboardInterrupt
# e SystemExit NAO estao aqui de proposito: nao sao falhas do banco, sao ordens
# de encerramento, e engoli-las para "tratar" faria o processo mentir sobre o
# proprio estado. Elas propagam — depois de uma tentativa de rollback, porque
# deixar transacao aberta e' pior do que propagar.
OPERATIONAL_ERRORS = (BackfillValidationError, Exception)


def apply_scoped_replace(staging: Staging, *, executor) -> int:
    """Aplica o scoped replace numa unica transacao do executor.

    `executor` e' injetado (fake nos testes, ScopedReplaceExecutor em
    producao) e expoe begin/backup_scope/delete_scope/insert_rows/
    count_scope/commit/rollback. NAO habilitado: `main()` recusa --apply.

    Semantica dos retornos:
      EXIT_ROLLED_BACK   — falhou ANTES do commit e o rollback foi CONFIRMADO
      EXIT_INDETERMINATE — o rollback tambem falhou, OU o commit foi tentado e
                           o resultado e' desconhecido. Nunca se alega rollback
                           depois de um commit possivelmente realizado."""
    if os.environ.get("I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES") != "1":
        raise BackfillValidationError(
            "apply exige I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES=1 "
            "(nada foi escrito)"
        )
    executor.begin()
    try:
        executor.backup_scope(staging.scopes)
        executor.delete_scope(staging.scopes)
        executor.insert_rows(staging.rows)
        gravadas = executor.count_scope(staging.scopes)
        if gravadas != len(staging.rows):
            raise BackfillValidationError(
                f"contagem pos-insert divergente ({gravadas} != {len(staging.rows)})"
            )
    except (KeyboardInterrupt, SystemExit):
        # Ordem de encerramento: tenta desfazer e PROPAGA. Nunca vira exit code.
        try:
            executor.rollback()
        except OPERATIONAL_ERRORS:
            pass
        raise
    except OPERATIONAL_ERRORS:
        try:
            executor.rollback()
        except OPERATIONAL_ERRORS:
            return EXIT_INDETERMINATE
        return EXIT_ROLLED_BACK

    try:
        executor.commit()
    except (KeyboardInterrupt, SystemExit):
        raise
    except OPERATIONAL_ERRORS:
        # O commit pode ou nao ter sido aplicado. NAO tentar rollback aqui e
        # NAO alegar que nada mudou: exige inspecao humana.
        return EXIT_INDETERMINATE
    return EXIT_OK


# ---------------------------------------------------------------------------
# Executor produtivo — interface e adapter (APPLY BLOQUEADO neste gate)
# ---------------------------------------------------------------------------
#
# `apply_scoped_replace` acima e' agnostico ao executor. O fake dos testes e o
# adapter produtivo implementam o MESMO protocolo; nada em apply_scoped_replace
# sabe qual dos dois esta' rodando. O que segue e' o adapter produtivo:
# implementado, coberto por teste de CONTRATO (SQL emitido, ordem das
# operacoes), e NAO habilitado — `main()` recusa `--apply`.
#
# BACKUP DURAVEL, nao TEMP: uma TEMP TABLE morre com a sessao e portanto nao e'
# backup operacional. O adapter grava numa tabela real e nomeada
# `marts.fact_shopee_product_monthly_bkp_<timestamp>`, que sobrevive a sessao e
# pode ser lida depois do incidente.

BACKUP_PREFIX = f"{TABLE}_bkp_"


def _scope_predicate(scopes: list[tuple[str, str]]) -> tuple[str, dict]:
    pares, params = [], {}
    for i, (brand, month) in enumerate(scopes):
        pares.append(f"(:b{i}, :m{i})")
        params[f"b{i}"], params[f"m{i}"] = brand, month
    return f"(brand, to_char(ref_month, 'YYYY-MM')) IN ({', '.join(pares)})", params


class ScopedReplaceExecutor:
    """Adapter produtivo do protocolo de escrita. NAO habilitado neste gate.

    Uma unica transacao por destino. Recebe uma `connection` SQLAlchemy ja'
    aberta (injetada) — nunca cria a sua propria, para que quem chama seja
    obrigado a decidir explicitamente qual banco esta' sendo escrito."""

    def __init__(self, connection, *, table: str = TABLE, clock=None) -> None:
        self.conn = connection
        self.table = table
        self._clock = clock or (lambda: __import__("datetime").datetime.now())
        self.backup_table: str | None = None
        self.emitted: list[str] = []          # trilha sanitizada, nao executavel
        self._trans = None                    # handle da transacao SQLAlchemy

    def _trail(self, sql: str) -> None:
        """Registra a trilha SEM executar nada. Foi o defeito F1: a trilha
        chamava execute() e o INSERT saia duas vezes."""
        self.emitted.append(sql.strip().split("\n")[0][:120])

    def _exec(self, sql: str, params: dict | None = None):
        self._trail(sql)
        return self.conn.execute(sqlalchemy_text(sql), params or {})

    def begin(self):
        """Transacao pela API do SQLAlchemy, guardando o handle. Nunca 'BEGIN'
        textual — com autocommit/implicit begin, o texto pode nao abrir
        transacao nenhuma e o rollback depois nao desfaz coisa alguma."""
        self._trail("conn.begin()")
        self._trans = self.conn.begin()

    def backup_scope(self, scopes):
        pred, params = _scope_predicate(scopes)
        stamp = self._clock().strftime("%Y%m%d_%H%M%S")
        self.backup_table = f"{BACKUP_PREFIX}{stamp}"
        # Tabela REAL e nomeada — sobrevive a sessao. Nunca TEMP.
        self._exec(
            f"CREATE TABLE {self.backup_table} AS "
            f"SELECT * FROM {self.table} WHERE {pred}", params)

    def delete_scope(self, scopes):
        if not self.backup_table:
            raise BackfillValidationError(
                "delete_scope antes de backup_scope: sequencia invalida "
                "(nada foi apagado)")
        pred, params = _scope_predicate(scopes)
        # Sempre filtrado por escopo. Nunca TRUNCATE, nunca DELETE aberto.
        self._exec(f"DELETE FROM {self.table} WHERE {pred}", params)

    def insert_rows(self, rows: pd.DataFrame):
        """UM unico executemany. A trilha e' registrada sem executar SQL."""
        if rows.empty:
            return
        cols = [c for c in rows.columns if not c.startswith("_")]
        placeholders = ", ".join(f":{c}" for c in cols)
        sql = f"INSERT INTO {self.table} ({', '.join(cols)}) VALUES ({placeholders})"
        registros = rows[cols].to_dict("records")
        self._trail(sql)                       # so' trilha — nao executa
        self.conn.execute(sqlalchemy_text(sql), registros)   # unica execucao

    def count_scope(self, scopes) -> int:
        pred, params = _scope_predicate(scopes)
        r = self._exec(f"SELECT count(*) AS n FROM {self.table} WHERE {pred}", params)
        return int(r.scalar() or 0)

    def commit(self):
        if self._trans is None:
            raise BackfillValidationError(
                "commit sem transacao aberta: sequencia invalida")
        self._trail("trans.commit()")
        self._trans.commit()
        self._trans = None

    def rollback(self):
        if self._trans is None:
            raise BackfillValidationError(
                "rollback sem transacao aberta: sequencia invalida")
        self._trail("trans.rollback()")
        self._trans.rollback()
        self._trans = None


# ---------------------------------------------------------------------------
# Orquestracao XLSX -> local -> Neon (desenho; NAO executado)
# ---------------------------------------------------------------------------

STATUS_OK = "OK"
STATUS_PARTIAL = "PARTIAL"
STATUS_REFUSED = "REFUSED"


def plan_local_then_neon(scopes: list[tuple[str, str]]) -> dict:
    """Contrato do caminho completo, em ordem obrigatoria.

    Os dois bancos NAO participam de uma transacao distribuida: sao dois
    commits separados, e o estado parcial (local commitado, Neon nao) e' um
    resultado POSSIVEL e explicitamente nomeado — `PARTIAL` — nunca mascarado
    como sucesso nem como falha total."""
    return {
        "scopes": list(scopes),
        "ordem": [
            "1. XLSX -> scoped replace no LOCAL (transacao 1, backup duravel)",
            "2. validar LOCAL pos-commit (contagem, chave real, escopo)",
            "3. LOCAL -> scoped replace dos MESMOS escopos no NEON (transacao 2, backup duravel)",
            "4. validar NEON contra LOCAL (paridade de chaves e agregados por escopo)",
            "5. registrar auditoria das DUAS etapas (run_id comum)",
        ],
        "proibido": [
            "aplicar somente no NEON deixando o LOCAL antigo",
            "TRUNCATE de tabela inteira",
            "transacao distribuida ficticia entre os dois bancos",
            "backup em TEMP TABLE (morre com a sessao)",
            "UPSERT como unico mecanismo",
        ],
        "resultados_possiveis": {
            STATUS_OK: "as duas etapas commitaram e validaram",
            STATUS_PARTIAL: "LOCAL commitado, NEON falhou -> retry SOMENTE da etapa 3-4",
            STATUS_REFUSED: "validacao reprovou antes de qualquer escrita",
        },
        "retry_partial": "somente a propagacao LOCAL -> NEON, nunca reprocessar XLSX",
        "lock": {
            "nome": LOCK_NAME,
            "compartilhado_com": ["sync_produtos_shopee", "full_daily",
                                  "shopee_manual_refresh"],
            "regra": "quem nao adquirir o lock ABORTA; nunca espera indefinidamente",
        },
    }


def assert_not_neon_only(steps_done: list[str]) -> None:
    """Recusa, por contrato, escrever no Neon sem o local ter sido escrito e
    validado antes. E' a regra 7 do gate, executavel."""
    if "neon" in steps_done and "local" not in steps_done:
        raise BackfillValidationError(
            "recusado: escrita no NEON sem o LOCAL ter sido escrito e validado "
            "antes — deixaria o local como fonte antiga e um sync posterior "
            "reintroduziria residuo (nada foi escrito)"
        )


# ---------------------------------------------------------------------------
# Relatorio sanitizado
# ---------------------------------------------------------------------------

def _fmt(v, casas=2):
    """N/D e' N/D — nunca 0."""
    if v is None:
        return ND
    if isinstance(v, Decimal):
        return f"{v:.{casas}f}"
    return str(v)


def format_report(staging: Staging, rec: Reconciliation, *, mode: str) -> str:
    L = [f"=== BACKFILL {TABLE} — {mode.upper()} ==="]
    if rec.compared:
        L.append(f"comparado contra: ALVO={rec.compared_target.upper()} (conexao read-only)")
    else:
        L.append("comparado contra: NENHUM BANCO — estado anterior e deltas = N/D")
    L.append(f"escopos: {', '.join(f'{b}:{m}' for b, m in staging.scopes)}")
    L.append(f"arquivos aceitos: {sum(len(v) for v in staging.accepted.values())}"
             f" em {len(staging.accepted)} marca(s)")
    for brand, names in sorted(staging.accepted.items()):
        L.append(f"  {brand}: {len(names)} arquivos | {staging.winning_snapshots.get(brand,'-')}")
    if staging.rejected:
        L.append(f"arquivos recusados: {len(staging.rejected)}")
        for nome, motivo in sorted(staging.rejected.items()):
            L.append(f"  RECUSADO {nome} — {motivo}")
    L.append(f"hashes declarados: {len(staging.file_hashes)}")
    L.append("")
    L.append(f"linhas   antes={_fmt(rec.before_rows)}  depois={rec.after_rows}")
    L.append(f"chaves   +{_fmt(rec.keys_added)}  ~{_fmt(rec.keys_updated)}  "
             f"-{_fmt(rec.keys_removed)}")
    L.append(f"gmv      antes={_fmt(rec.gmv_before)}  depois={_fmt(rec.gmv_after)}  "
             f"delta={_fmt(rec.gmv_delta)}")
    L.append(f"units    antes={_fmt(rec.units_before)}  depois={rec.units_after}  "
             f"delta={_fmt(rec.units_delta)}")
    L.append(f"concl.   antes={_fmt(rec.completed_before)}  depois={rec.completed_after}")
    L.append(f"cancel.  antes={_fmt(rec.canceled_before)}  depois={rec.canceled_after}")
    L.append("")
    L.append("por escopo:")
    for (b, m), d in sorted(rec.per_scope.items()):
        L.append(f"  {b}:{m}  linhas {_fmt(d['rows_before'])}->{d['rows_after']}  "
                 f"gmv {_fmt(d['gmv_before'])}->{_fmt(d['gmv_after'])}  "
                 f"units {_fmt(d['units_before'])}->{d['units_after']}  "
                 f"chaves +{_fmt(d['keys_added'])} -{_fmt(d['keys_removed'])}")
    L.append("")
    if not rec.compared:
        L.append("MODO OFFLINE: nenhuma conexao foi aberta. Os campos 'antes' e os")
        L.append("deltas sao N/D por construcao — este modo NAO prova nada sobre o")
        L.append("estado do banco. Use --dry-run --target local|neon para isso.")
    else:
        L.append("NENHUMA escrita foi feita. Somente conexao read-only foi aberta.")
    L.append("APPLY PRODUTIVO BLOQUEADO neste gate.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Estados de frescor de Produtos Shopee (Gate SH-API-2B-R, Fase 5)
# ---------------------------------------------------------------------------
#
# A formula comercial NAO muda neste gate. O que muda e' passar a ter um NOME
# para cada situacao, em vez de deixar a tela vazia parecer "sem dados" ou,
# pior, "saudavel". Agosto/2026 e' o caso concreto: 188 chaves existem no
# destino, todas com GMV zero, e a API as remove por `WHERE gmv > 0` — presenca
# fisica com indisponibilidade analitica.

FRESHNESS_SOURCE_MISSING = "source_missing"
FRESHNESS_LOAD_STALE = "load_stale"
FRESHNESS_PRESENT_NOT_ELIGIBLE = "present_but_not_eligible"
FRESHNESS_MATURATION_PENDING = "maturation_pending"
FRESHNESS_COMPLETE = "complete"

FRESHNESS_STATES = (FRESHNESS_SOURCE_MISSING, FRESHNESS_LOAD_STALE,
                    FRESHNESS_PRESENT_NOT_ELIGIBLE, FRESHNESS_MATURATION_PENDING,
                    FRESHNESS_COMPLETE)


SOURCE_ABSENT = "source_absent"
SOURCE_PRESENT = "source_present"

LOAD_ABSENT = "load_absent"
LOAD_STALE = "load_stale"
LOAD_CURRENT = "load_current"

ELIG_NONE = "no_eligible_rows"
ELIG_PARTIAL = "partially_eligible"
ELIG_OK = "eligible"

MATURITY_IMMATURE = "source_materially_immature"
MATURITY_MATURE = "source_mature"
MATURITY_UNKNOWN = "maturity_unknown"

COVERAGE_OK = "coverage_ok"
COVERAGE_BELOW = "coverage_below_allowlist"


# Piso operacional PROVISORIO, medido (Gate SH-API-2B-R2): o minimo observado
# entre os seis meses fechados historicamente estaveis (2026-01..2026-06) e'
# 0,9987. 0,99 e' um piso conservador derivado dessa evidencia — NAO e' verdade
# comercial definitiva, e a decisao do valor final e' do proprietario.
MATURITY_FLOOR_PROVISIONAL = Decimal("0.99")


class MaturityInputError(BackfillValidationError):
    """Insumo de maturidade impossivel (GMV negativo, razao fora de [0,1]).

    Erro explicito em vez de estado: um numero impossivel nao e' um estado do
    mundo, e' um defeito de medicao — e degrada-lo para 'unknown' esconderia
    o defeito."""


def compute_completed_share(completed_gmv: Decimal,
                            active_gmv: Decimal) -> Decimal | None:
    """Participacao do GMV concluido sobre o nao-cancelado.

    `None` (=> maturity_unknown) quando o denominador e' zero: nao ha' base
    para julgar maturidade, e inventar 0 ou 1 seria mentir. Valores negativos
    ou razao fora de [0,1] levantam."""
    if completed_gmv < 0 or active_gmv < 0:
        raise MaturityInputError(
            "GMV negativo no calculo de maturidade: insumo invalido "
            "(valores omitidos; nada foi lido nem escrito)")
    if active_gmv == 0:
        return None
    share = (completed_gmv / active_gmv)
    if share < Decimal("0") or share > Decimal("1"):
        raise MaturityInputError(
            f"razao de maturidade fora de [0,1] ({share.quantize(Decimal('0.0001'))}): "
            f"concluido maior que o nao-cancelado indica erro de medicao")
    return share.quantize(Decimal("0.0001"))


def classify_scope(*, rows_present: int, eligible_rows: int,
                   daily_gmv: Decimal, source_files: int,
                   load_is_stale: bool, month_is_closed: bool,
                   completed_share: Decimal | None = None,
                   mature_share_floor: Decimal | None = None,
                   brands_present: int = 0, brands_expected: int = 0) -> dict:
    """Classificacao ORTOGONAL de um par (marca, competencia).

    Quatro eixos INDEPENDENTES, porque a realidade e' independente: um mes pode
    estar simultaneamente com carga defasada E com fonte imatura E fisicamente
    presente E com cobertura incompleta. Colapsar isso num unico estado
    mutuamente exclusivo foi o defeito F5 — `maturation_pending` escondia
    `load_stale`.

    `completed_share` = participacao de GMV concluido sobre o GMV da diaria no
    mesmo escopo. `mature_share_floor` deve ser MEDIDO nos meses fechados
    historicamente estaveis, nunca arbitrado aqui: se vier None, a maturidade
    fica `maturity_unknown` em vez de inventar um limite."""
    material = daily_gmv > Decimal("0")

    source_status = SOURCE_PRESENT if source_files > 0 else SOURCE_ABSENT

    if rows_present == 0:
        load_status = LOAD_ABSENT
    elif load_is_stale:
        load_status = LOAD_STALE
    else:
        load_status = LOAD_CURRENT

    if rows_present == 0:
        elig_status = ELIG_NONE
    elif eligible_rows == 0:
        elig_status = ELIG_NONE
    elif eligible_rows < rows_present:
        elig_status = ELIG_PARTIAL
    else:
        elig_status = ELIG_OK

    if completed_share is not None and (completed_share < 0 or completed_share > 1):
        raise MaturityInputError(
            "completed_share fora de [0,1]: insumo invalido para maturidade")
    if completed_share is None or mature_share_floor is None:
        maturity_status = MATURITY_UNKNOWN
    elif completed_share < mature_share_floor:
        maturity_status = MATURITY_IMMATURE
    else:
        maturity_status = MATURITY_MATURE

    if brands_expected and brands_present < brands_expected:
        coverage_status = COVERAGE_BELOW
    else:
        coverage_status = COVERAGE_OK

    analitica_insuficiente = material and elig_status == ELIG_NONE
    alertas = []
    if source_status == SOURCE_ABSENT:
        alertas.append("shopee_produtos_fonte_ausente")
    if load_status in (LOAD_ABSENT, LOAD_STALE) and material:
        alertas.append("shopee_produtos_carga_defasada")
    if analitica_insuficiente:
        alertas.append("shopee_produtos_presente_mas_100pct_excluido")
    if maturity_status == MATURITY_IMMATURE:
        alertas.append("shopee_produtos_fonte_imatura")
    if coverage_status == COVERAGE_BELOW:
        alertas.append("shopee_produtos_cobertura_de_marcas")

    return {
        "source_status": source_status,
        "load_status": load_status,
        "eligibility_status": elig_status,
        "maturity_status": maturity_status,
        "coverage_status": coverage_status,
        "physically_present": rows_present > 0,
        "analytically_sufficient": not analitica_insuficiente,
        "daily_gmv_material": material,
        "rows_present": rows_present,
        "eligible_rows": eligible_rows,
        "excluded_zero_gmv": max(rows_present - eligible_rows, 0),
        "alerts": alertas,
        "alert": bool(alertas),
        # refreshed_at NUNCA sai daqui: vem da auditoria real da carga/sync.
        "refreshed_at_source": "audit.source_sync_run (a implementar)",
    }


def classify_scope_freshness(*, rows_present: int, eligible_rows: int,
                             daily_gmv: Decimal, source_files: int,
                             load_is_stale: bool,
                             month_is_closed: bool) -> dict:
    """Classifica UM par (brand, ref_month). Funcao pura.

    Nunca devolve "sem dados" quando ha' linha fisica, e nunca devolve
    `complete` em silencio quando a Shopee Daily tem GMV material e Produtos
    nao tem nenhuma linha elegivel.

    `daily_gmv` e' o GMV da fato diaria no mesmo escopo — a referencia externa
    que separa "nao vendeu" de "vendeu e nao aparece"."""
    material = daily_gmv > Decimal("0")

    if source_files == 0:
        estado, alerta = FRESHNESS_SOURCE_MISSING, True
    elif rows_present == 0:
        # Nenhuma linha no destino. Se a diaria tem GMV, e' carga atrasada.
        estado = FRESHNESS_LOAD_STALE if material else FRESHNESS_COMPLETE
        alerta = material
    elif eligible_rows == 0:
        # Linhas existem, nenhuma passa no filtro de elegibilidade.
        # Mes fechado -> maturacao pendente; mes corrente -> esperado.
        estado = (FRESHNESS_MATURATION_PENDING if month_is_closed
                  else FRESHNESS_PRESENT_NOT_ELIGIBLE)
        alerta = material
    elif load_is_stale:
        estado, alerta = FRESHNESS_LOAD_STALE, True
    else:
        estado, alerta = FRESHNESS_COMPLETE, False

    return {
        "state": estado,
        "alert": alerta,
        "rows_present": rows_present,
        "eligible_rows": eligible_rows,
        "excluded_zero_gmv": max(rows_present - eligible_rows, 0),
        "daily_gmv_material": material,
        # refreshed_at NUNCA vem daqui: deve vir da auditoria real da
        # carga/sync. Nem NOW(), nem a data da competencia.
        "refreshed_at_source": "audit.source_sync_run (a implementar)",
    }


QUALITY_ALERT_BLUEPRINT = (
    {"id": "shopee_produtos_vazio_com_diaria_positiva",
     "regra": "ultimo mes fechado com Shopee Daily > 0 e Produtos Shopee elegiveis = 0",
     "onde": ["health_check", "torre_qualidade_dados"], "critico_para_exit": False},
    {"id": "shopee_produtos_ref_month_atrasado",
     "regra": "MAX(ref_month) de Produtos Shopee atras do ultimo mes fechado",
     "onde": ["health_check", "torre_qualidade_dados"], "critico_para_exit": False},
    {"id": "shopee_produtos_cobertura_de_marcas",
     "regra": "cobertura de marcas na competencia abaixo da allowlist esperada",
     "onde": ["health_check", "torre_qualidade_dados"], "critico_para_exit": False},
    {"id": "shopee_produtos_presente_mas_100pct_excluido",
     "regra": "competencia fisicamente presente e 100% excluida por GMV zero",
     "onde": ["health_check", "torre_qualidade_dados"], "critico_para_exit": False},
)
# Fonte manual pode ser nao critica para o exit code, mas NUNCA silenciosa
# para o usuario: os quatro alertas aparecem nas duas superficies.


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m etl.backfill_shopee_products",
        description="Backfill por escopo de marts.fact_shopee_product_monthly. "
                    "APPLY produtivo BLOQUEADO neste gate.",
    )
    p.add_argument("--scope", action="append", metavar="brand:YYYY-MM",
                   help="Par autorizado. Repetivel. Obrigatorio.")
    p.add_argument("--source-root", default=None,
                   help="Raiz dos exports. Padrao: SHOPEE_ROOT do loader.")
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--offline-dry-run", dest="mode", action="store_const",
                   const="offline-dry-run",
                   help="Arquivos e memoria apenas. Zero conexao. 'antes' e delta = N/D.")
    m.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run",
                   help="Le o alvo por conexao READ ONLY e compara de verdade. "
                        "Exige --target.")
    m.add_argument("--apply", dest="mode", action="store_const", const="apply",
                   help="BLOQUEADO neste gate.")
    p.add_argument("--target", choices=TARGETS, default=None,
                   help="Alvo da leitura: local (BACKFILL_LOCAL_RO_URL) ou "
                        "neon (BACKFILL_NEON_RO_URL). Obrigatorio em --dry-run.")
    p.add_argument("--allow-empty-staging", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.mode == "apply":
        print("APPLY PRODUTIVO BLOQUEADO: o executor real (backup duravel, "
              "DELETE escopado, INSERT, validacao, commit/rollback) esta' "
              "implementado como interface e coberto por fake, mas nao foi "
              "habilitado. Nada foi escrito.", file=sys.stderr)
        return EXIT_VALIDATION_REFUSED

    try:
        scopes = parse_scopes(args.scope)
        root = resolve_source_root(args.source_root)
        if args.mode == "dry-run":
            if args.target is None:
                raise BackfillUsageError(
                    "--dry-run exige --target local|neon: o alvo nunca e' inferido "
                    "de um DATABASE_URL generico"
                )
            resolve_target_url(args.target)   # valida classe de host ANTES de ler arquivo
        elif args.target is not None:
            raise BackfillUsageError("--target so' faz sentido com --dry-run")
    except BackfillUsageError as e:
        print(f"ERRO DE USO: {e}", file=sys.stderr)
        return EXIT_USAGE

    try:
        staging = build_staging(scopes, root)
    except (loader.ShopeeSnapshotError, loader.ShopeeProductInputError) as e:
        print(f"VALIDACAO RECUSADA (triagem de arquivos): {e}", file=sys.stderr)
        return EXIT_VALIDATION_REFUSED

    try:
        assert_staging_unique(staging)
        assert_staging_within_scope(staging)
    except BackfillValidationError as e:
        print(f"VALIDACAO RECUSADA: {e}", file=sys.stderr)
        return EXIT_VALIDATION_REFUSED

    if args.mode == "offline-dry-run":
        rec = reconcile(None, staging)
    else:
        try:
            before = read_current_scope(scopes, target=args.target)
        except BackfillUsageError as e:
            print(f"ERRO DE USO: {e}", file=sys.stderr)
            return EXIT_USAGE
        except BackfillValidationError as e:
            print(f"VALIDACAO RECUSADA: {e}", file=sys.stderr)
            return EXIT_VALIDATION_REFUSED
        rec = reconcile(before, staging, compared_target=args.target)
        try:
            assert_reconciliation_sane(rec, allow_empty_staging=args.allow_empty_staging)
        except BackfillValidationError as e:
            print(f"VALIDACAO RECUSADA: {e}", file=sys.stderr)
            return EXIT_VALIDATION_REFUSED

    print(format_report(staging, rec, mode=args.mode))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
