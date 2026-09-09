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
                           depois de um commit possivelmente realizado.

    ZERO RETRY, por desenho. Uma unica tentativa: qualquer falha vira exit
    code e para. Retry automatico aqui seria especialmente perigoso porque o
    caso ambiguo (`EXIT_INDETERMINATE`) e' justamente aquele em que nao se sabe
    se a escrita foi aplicada — repetir poderia duplicar o efeito. A decisao de
    repetir e' humana, depois de inspecionar o destino e o backup."""
    if os.environ.get(CONSENT_ENV) != "1":
        raise BackfillValidationError(
            f"apply exige {CONSENT_ENV}=1 (nada foi escrito)"
        )
    # Porta 1 tambem aqui, e nao so' na CLI: quem chamar esta funcao por outro
    # caminho (script, notebook, teste) passa pela MESMA allowlist.
    assert_scopes_authorized(list(staging.scopes))

    # Backup COMMITADO antes de abrir a transacao de mutacao. Se o backup
    # falhar, nada e' apagado e a excecao propaga — nunca se segue sem ele.
    executor.backup_scope(staging.scopes)

    executor.begin()
    try:
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

    def __init__(self, connection, *, target: str, table: str = TABLE,
                 clock=None) -> None:
        # `target` e OBRIGATORIO (Gate SH-API-2E1): as colunas do INSERT e o
        # nome do backup dependem dele. Sem destino declarado o executor nao
        # sabe se `ingested_at` existe, e essa e' exatamente a coluna que
        # difere entre os dois bancos.
        if target not in TARGETS:
            raise BackfillUsageError(f"alvo desconhecido: {target!r}")
        self.conn = connection
        self.target = target
        self.table = table
        self._clock = clock or (lambda: __import__("datetime").datetime.now())
        self.backup_table: str | None = None
        self.backup_committed = False
        self.backup_fingerprint: dict | None = None
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

    def backup_scope(self, scopes) -> dict:
        """Backup DURAVEL, em transacao PROPRIA, COMMITADO antes da mutacao.

        Tres mudancas do Gate SH-API-2E1, cada uma fechando um modo de falha
        real:

        1. Transacao propria e commit imediato. Antes o backup vivia na mesma
           transacao do DELETE — se ela caisse, o backup caia junto, e o
           "backup" nao protegia de nada. Agora, se a mutacao morrer ou o
           processo for morto, a tabela de backup ja esta no disco.
        2. Colunas EXPLICITAS, nunca `SELECT *`. Os dois destinos tem schemas
           diferentes (14 x 15 colunas) e `SELECT *` produziria backups com
           formatos distintos, impossiveis de comparar entre si.
        3. Contagem e CHECKSUM conferidos contra a origem antes de qualquer
           DELETE. Um backup que existe mas nao confere e' pior que nenhum:
           passa confianca falsa na hora de restaurar.
        """
        pred, params = _scope_predicate(scopes)
        cols = insert_columns(self.target)
        stamp = self._clock().strftime("%Y%m%d_%H%M%S")
        nome = backup_table_name(self.target, stamp, table=self.table)
        lista = ", ".join(cols)

        trans = self.conn.begin()
        try:
            # Tabela REAL e nomeada — sobrevive a sessao. Nunca TEMP.
            self._exec(f"CREATE TABLE {nome} AS "
                       f"SELECT {lista} FROM {self.table} WHERE {pred}", params)

            origem = self._exec(
                fingerprint_sql(self.table, cols, pred), params).mappings().first()
            copia = self._exec(
                fingerprint_sql(nome, cols, "TRUE")).mappings().first()

            if int(origem["n"]) != int(copia["n"]) or origem["checksum"] != copia["checksum"]:
                raise BackfillValidationError(
                    f"backup NAO confere com a origem "
                    f"(linhas {origem['n']} x {copia['n']}); nada foi apagado")
            trans.commit()
        except (KeyboardInterrupt, SystemExit):
            # Ordem de encerramento: tenta desfazer e PROPAGA, igual ao resto
            # do modulo. Nunca vira exit code.
            try:
                trans.rollback()
            except OPERATIONAL_ERRORS:
                pass
            raise
        except OPERATIONAL_ERRORS:
            # Backup que falhou nao pode deixar tabela pela metade nem
            # transacao aberta. Propaga sempre: sem backup nao ha mutacao.
            try:
                trans.rollback()
            except OPERATIONAL_ERRORS:
                pass
            raise

        self.backup_table = nome
        self.backup_committed = True
        self.backup_fingerprint = {"rows": int(origem["n"]),
                                   "checksum": origem["checksum"]}
        self._trail("trans.commit()  -- backup duravel, ANTES da mutacao")
        return {"table": nome, "target": self.target,
                "rows": int(origem["n"]), "checksum": origem["checksum"],
                "retention_days": BACKUP_RETENTION_DAYS}

    def delete_scope(self, scopes):
        if not self.backup_table or not self.backup_committed:
            raise BackfillValidationError(
                "delete_scope sem backup COMMITADO: sequencia invalida "
                "(nada foi apagado)")
        pred, params = _scope_predicate(scopes)
        # Sempre filtrado por escopo. Nunca TRUNCATE, nunca DELETE aberto.
        self._exec(f"DELETE FROM {self.table} WHERE {pred}", params)

    def insert_rows(self, rows: pd.DataFrame):
        """UM unico executemany, com colunas EXPLICITAS do destino.

        As colunas vem de `insert_columns(target)`, nunca do DataFrame: uma
        coluna a mais na staging (ou a menos) mudaria o INSERT silenciosamente.
        `ingested_at` e' preenchido por NOW() do proprio banco no Neon e nao
        existe no local — por isso os parametros carregam so' `DATA_COLS`."""
        if rows.empty:
            return
        faltando = [c for c in DATA_COLS if c not in rows.columns]
        if faltando:
            raise BackfillValidationError(
                f"staging sem as colunas obrigatorias {faltando} (nada foi escrito)")

        sql = insert_sql(self.target, table=self.table)
        if self.target == TARGET_LOCAL:
            assert_no_ingested_at_in_local_sql(sql)
        registros = rows[list(DATA_COLS)].to_dict("records")
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


# ===========================================================================
# Gate SH-API-2E1 — habilitacao TECNICA do caminho de escrita
# ===========================================================================
#
# Nada aqui executa escrita. O que este bloco entrega e' o CONTRATO que uma
# escrita futura tera' de satisfazer, com cada porta implementada e testada.
# `main()` continua recusando `--apply` (ver AVISO no fim do modulo).
#
# Principio: toda porta e' FAIL-CLOSED e independente. Nenhuma delas infere
# outra. "Passou na identidade" nao implica "e' primary"; "e' primary" nao
# implica "tem SSL". Um preflight que deduz condicao a partir de outra e' um
# preflight que mente quando o ambiente muda.
# ---------------------------------------------------------------------------

# --- Allowlist EXATA -------------------------------------------------------
#
# Esta operacao existe para remover duplicata de snapshot sobreposto em DOIS
# escopos medidos, e para mais nada. A allowlist e' uma tupla literal e o
# comando recusa qualquer conjunto diferente — inclusive um SUBCONJUNTO. Nao
# aceitar subconjunto e' deliberado: rodar so' `apice` deixaria o par medido
# pela metade e a reconciliacao total nao fecharia contra o valor esperado.
AUTHORIZED_SCOPES: tuple[tuple[str, str], ...] = (
    ("apice", "2026-05"),
    ("barbours", "2026-05"),
)

# Escopos que JA foram considerados e reprovados por medicao. Estao nomeados
# para que a recusa cite o motivo em vez de dizer so' "nao autorizado" — quem
# tentar rodar precisa entender por que aquele mes especifico e' pior, nao
# apenas que a lista nao o contem.
EXPLICITLY_REJECTED: dict[tuple[str, str], str] = {
    ("rituaria", "2026-07"): (
        "competencia medida como materialmente imatura: o backfill trocaria "
        "um numero provisorio por outro provisorio"),
    ("kokeshi", "2026-08"): (
        "competencia sem nenhum pedido concluido e com carga atras da diaria: "
        "o passo correto e' recarregar, nao corrigir retroativamente"),
}


class ScopeNotAuthorizedError(BackfillUsageError):
    """O conjunto de escopos pedido nao e' EXATAMENTE o autorizado."""


def assert_scopes_authorized(scopes: list[tuple[str, str]]) -> None:
    """Porta 1. Exige o conjunto autorizado, exato, sem sobra nem falta."""
    if not scopes:
        raise ScopeNotAuthorizedError(
            "execucao SEM escopo e' recusada: --apply exige os dois --scope "
            "explicitos (nada foi escrito)")
    if len(scopes) != len(set(scopes)):
        raise ScopeNotAuthorizedError(
            "escopo repetido na linha de comando (nada foi escrito)")

    pedido, autorizado = set(scopes), set(AUTHORIZED_SCOPES)

    for s in sorted(pedido - autorizado):
        motivo = EXPLICITLY_REJECTED.get(s)
        alvo = f"{s[0]}:{s[1]}"
        if motivo:
            raise ScopeNotAuthorizedError(
                f"escopo {alvo} RECUSADO — {motivo} (nada foi escrito)")
        raise ScopeNotAuthorizedError(
            f"escopo {alvo} nao esta na allowlist desta operacao "
            f"(autorizados: {_fmt_scopes(AUTHORIZED_SCOPES)}; nada foi escrito)")

    faltando = autorizado - pedido
    if faltando:
        raise ScopeNotAuthorizedError(
            f"execucao PARCIAL recusada: falta {_fmt_scopes(sorted(faltando))}. "
            f"A expectativa medida e' do par completo; rodar metade nao fecha a "
            f"reconciliacao total (nada foi escrito)")


def _fmt_scopes(scopes) -> str:
    return ", ".join(f"{b}:{m}" for b, m in scopes)


# --- Expectativa MEDIDA, usada como trava ----------------------------------
#
# Numeros do Gate ADMIN-SH-RO-1 (candidato x local x Neon, paridade perfeita).
# Sao CONTRATO DE VERIFICACAO, nunca alvo a ser forcado: se a staging de hoje
# nao reproduzir estes deltas, a premissa mudou (arquivo novo, arquivo
# retirado, regra de dedup alterada) e a escrita e' bloqueada para reanalise.
EXPECTED_GMV_DELTA: dict[tuple[str, str], Decimal] = {
    ("apice", "2026-05"): Decimal("-23292.43"),
    ("barbours", "2026-05"): Decimal("-80987.03"),
}
EXPECTED_TOTAL_GMV_DELTA = Decimal("-104279.46")
EXPECTED_KEYS_ADDED = 0
EXPECTED_KEYS_REMOVED = 0
#: Centavos. A soma vem de agregacao em ponto flutuante rio acima; exigir
#: igualdade exata transformaria ruido de arredondamento em bloqueio falso.
DELTA_TOLERANCE = Decimal("0.01")
#: O indice operacional de maturacao tem de continuar acima do limiar depois
#: da correcao (medido: 1,0782 -> 1,0361 e 1,0758 -> 1,0266).
MATURATION_THRESHOLD_AFTER = Decimal("0.99")


class ExpectationMismatchError(BackfillValidationError):
    """A medicao de hoje divergiu da expectativa. Bloqueia antes de escrever."""


def assert_expected_delta(*, gmv_delta_por_escopo: dict[tuple[str, str], Decimal],
                          keys_added: int, keys_removed: int) -> None:
    """Porta 10. Compara a reconciliacao de hoje com o que foi medido.

    Divergencia NAO e' ajustada nem tolerada: e' bloqueio. O objetivo desta
    operacao e' remover duplicata conhecida — se o delta mudou, o que seria
    removido tambem mudou, e ninguem mediu isso."""
    problemas = []
    for escopo, esperado in EXPECTED_GMV_DELTA.items():
        obtido = gmv_delta_por_escopo.get(escopo)
        if obtido is None:
            problemas.append(f"{_fmt_scopes([escopo])}: sem delta medido")
            continue
        if abs(obtido - esperado) > DELTA_TOLERANCE:
            problemas.append(
                f"{_fmt_scopes([escopo])}: delta {obtido} != esperado {esperado} "
                f"(tolerancia {DELTA_TOLERANCE})")

    extras = set(gmv_delta_por_escopo) - set(EXPECTED_GMV_DELTA)
    if extras:
        problemas.append(f"delta medido em escopo nao autorizado: {_fmt_scopes(sorted(extras))}")

    total = sum(gmv_delta_por_escopo.values(), Decimal("0"))
    if abs(total - EXPECTED_TOTAL_GMV_DELTA) > DELTA_TOLERANCE:
        problemas.append(f"total {total} != esperado {EXPECTED_TOTAL_GMV_DELTA}")

    if keys_added != EXPECTED_KEYS_ADDED:
        problemas.append(f"{keys_added} chave(s) adicionada(s); esperado {EXPECTED_KEYS_ADDED}")
    if keys_removed != EXPECTED_KEYS_REMOVED:
        problemas.append(f"{keys_removed} chave(s) removida(s); esperado {EXPECTED_KEYS_REMOVED}")

    if problemas:
        raise ExpectationMismatchError(
            "expectativa medida NAO reproduzida (nada foi escrito): "
            + "; ".join(problemas))


# --- Colunas EXPLICITAS por destino ----------------------------------------
#
# `SELECT *` entre local e Neon e' proibido no modulo inteiro: os dois destinos
# tem schemas DIFERENTES (medido — 14 colunas no local, 15 no Neon). Um
# `INSERT ... SELECT *` desalinharia silenciosamente na primeira divergencia.
# Cada destino declara suas colunas aqui, e todo SQL as enumera.
DATA_COLS: tuple[str, ...] = (
    "ref_month", "brand", "sku_ref", "sku_ref_key", "product_name",
    "variation_name", "gmv", "units_sold", "completed_orders",
    "canceled_orders", "cancel_rate_pct", "unique_buyers", "avg_price",
)

#: So' existe no Neon. Semantica: instante de PUBLICACAO/REPUBLICACAO do mart.
#: NUNCA a data do dado na fonte — republicar um mes fechado move este carimbo
#: sem que nenhuma venda tenha mudado. E' exatamente o que a Torre exibe como
#: "publicado no mart em ...".
INGESTED_AT_COL = "ingested_at"

TARGET_COLS: dict[str, tuple[str, ...]] = {
    TARGET_LOCAL: DATA_COLS,
    TARGET_NEON: DATA_COLS + (INGESTED_AT_COL,),
}


def insert_columns(target: str) -> tuple[str, ...]:
    """Colunas do INSERT no destino. `ingested_at` NUNCA aparece no local."""
    if target not in TARGET_COLS:
        raise BackfillUsageError(f"alvo desconhecido: {target!r}")
    return TARGET_COLS[target]


def insert_sql(target: str, *, table: str = TABLE) -> str:
    """INSERT com colunas explicitas. No Neon, `ingested_at` e' preenchido
    pelo BANCO (NOW()), nunca por valor vindo da staging: o carimbo tem de ser
    o instante real da publicacao naquele destino, e a staging nao sabe disso."""
    cols = insert_columns(target)
    valores = []
    for c in cols:
        valores.append("NOW()" if c == INGESTED_AT_COL else f":{c}")
    return (f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(valores)})")


def assert_no_ingested_at_in_local_sql(sql: str) -> None:
    """Trava de regressao: qualquer SQL destinado ao local que mencione
    `ingested_at` e' um bug — a coluna nao existe la e o comando falharia no
    meio da transacao, depois do DELETE."""
    if INGESTED_AT_COL in sql:
        raise BackfillValidationError(
            f"SQL do destino 'local' menciona {INGESTED_AT_COL}, que so' existe "
            f"no Neon (nada foi executado)")


# --- Credencial dedicada de escrita ----------------------------------------
#
# Variaveis SEPARADAS das de leitura. Reaproveitar a URL read-only para
# escrever esconderia o momento em que a operacao deixou de ser segura; e
# reaproveitar DATABASE_URL faria o backfill herdar a credencial da aplicacao.
_WRITE_ENV = {
    TARGET_LOCAL: "BACKFILL_LOCAL_RW_URL",
    TARGET_NEON: "BACKFILL_NEON_RW_URL",
}
CONSENT_ENV = "I_UNDERSTAND_THIS_REWRITES_SHOPEE_PRODUCT_SCOPES"


class WriteGuardError(BackfillValidationError):
    """Uma porta do caminho de escrita reprovou. Nada foi escrito."""


def resolve_write_url(target: str, *, env=None) -> str:
    """Porta 3. URL de ESCRITA dedicada, validada por classe de host.

    Recusa explicitamente reaproveitar `DATABASE_URL` e as variaveis
    read-only. Nunca devolve nem imprime a URL em mensagem de erro."""
    env = os.environ if env is None else env
    if target not in TARGETS:
        raise BackfillUsageError(f"alvo desconhecido: {target!r}")

    var = _WRITE_ENV[target]
    url = env.get(var, "")
    if not url:
        raise WriteGuardError(
            f"escrita em '{target}' exige {var} definida explicitamente. "
            f"DATABASE_URL e as variaveis read-only sao recusadas de proposito "
            f"(nada foi escrito)")
    ro = env.get(_TARGET_ENV[target], "")
    if ro and url == ro:
        raise WriteGuardError(
            f"{var} tem o MESMO valor de {_TARGET_ENV[target]}: a credencial de "
            f"escrita precisa ser dedicada, nao a de leitura (nada foi escrito)")

    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        raise WriteGuardError(f"{var} sem host reconhecivel")
    is_local = host in _LOCAL_HOSTS
    if target == TARGET_LOCAL and not is_local:
        raise WriteGuardError(f"{var} aponta para host REMOTO com alvo 'local'")
    if target == TARGET_NEON and is_local:
        raise WriteGuardError(f"{var} aponta para LOCALHOST com alvo 'neon'")
    return url


# --- Primary gravavel e SSL ------------------------------------------------

WRITE_PREFLIGHT_SQL = """
SELECT pg_is_in_recovery()                              AS in_recovery,
       current_setting('transaction_read_only')         AS tx_readonly,
       has_table_privilege(current_user, :tabela, 'INSERT') AS pode_insert,
       has_table_privilege(current_user, :tabela, 'DELETE') AS pode_delete,
       has_schema_privilege(current_user, :schema, 'CREATE') AS pode_criar_backup
"""


def assert_writable_primary(conn, target: str, *, table: str = TABLE) -> dict:
    """Porta 6. O destino tem de ser PRIMARY gravavel, com privilegio real.

    `pg_is_in_recovery()` verdadeiro significa replica: escrever la falha no
    meio da transacao, depois do backup. Privilegio e' consultado, nunca
    testado por DML de mentira — tentar um INSERT para "ver se da" ja e' a
    escrita que este preflight existe para evitar."""
    schema, _, _tab = table.partition(".")
    row = conn.execute(sqlalchemy_text(WRITE_PREFLIGHT_SQL),
                       {"tabela": table, "schema": schema}).mappings().first()
    if row is None:
        raise WriteGuardError(f"preflight de escrita em '{target}' nao retornou linha")

    if row["in_recovery"]:
        raise WriteGuardError(
            f"destino '{target}' esta EM RECOVERY (replica): nao e' primary "
            f"gravavel (nada foi escrito)")
    if str(row["tx_readonly"]).lower() == "on":
        raise WriteGuardError(
            f"destino '{target}' esta com transaction_read_only=on "
            f"(nada foi escrito)")
    for chave, rotulo in (("pode_insert", "INSERT"), ("pode_delete", "DELETE"),
                          ("pode_criar_backup", "CREATE no schema (backup)")):
        if not row[chave]:
            raise WriteGuardError(
                f"a credencial de escrita nao tem privilegio de {rotulo} no "
                f"destino '{target}' (nada foi escrito)")
    return {"target": target, "primary": True, "privileges_ok": True}


def _dbapi_connection(conn):
    """Desce ate a conexao DBAPI real, sem assumir a versao do SQLAlchemy."""
    for atributo in ("driver_connection", "dbapi_connection", "connection"):
        candidato = getattr(getattr(conn, "connection", conn), atributo, None)
        if candidato is not None and hasattr(candidato, "info"):
            return candidato
    return getattr(conn, "connection", conn)


def assert_ssl_required(conn, target: str) -> None:
    """Porta 7. SSL obrigatorio no Neon.

    Medido no Gate ADMIN-SH-RO-1: `pg_stat_ssl` descreve a perna
    proxy->compute do Neon e responde FALSO mesmo com a conexao do cliente
    cifrada. A verdade do lado do cliente e' `connection.info.ssl_in_use`."""
    if target != TARGET_NEON:
        return
    info = getattr(_dbapi_connection(conn), "info", None)
    if info is None or not getattr(info, "ssl_in_use", False):
        raise WriteGuardError(
            "conexao de escrita com o Neon SEM SSL confirmado pelo cliente "
            "(nada foi escrito)")


# --- Advisory lock ---------------------------------------------------------
#
# Chave DETERMINISTICA derivada do nome da tabela: duas execucoes simultaneas
# do backfill disputam a mesma chave, em qualquer maquina, sem combinar nada.
ADVISORY_LOCK_KEY = int.from_bytes(
    hashlib.sha256(f"backfill:{TABLE}".encode("utf-8")).digest()[:8],
    "big", signed=True)


def acquire_advisory_lock(conn, *, key: int = ADVISORY_LOCK_KEY) -> None:
    """Porta 8. Lock de SESSAO, nao de transacao.

    Precisa ser de sessao porque o backup e a mutacao sao transacoes
    SEPARADAS: um lock de transacao seria liberado no commit do backup e
    deixaria a janela mais perigosa desprotegida.

    `pg_try_advisory_lock` (nao `pg_advisory_lock`): falha na hora se outra
    execucao esta em curso, em vez de ficar pendurada. Esperar em silencio e'
    uma forma de retry — e retry aqui e' proibido."""
    obtido = conn.execute(sqlalchemy_text("SELECT pg_try_advisory_lock(:k) AS ok"),
                          {"k": key}).scalar()
    if not obtido:
        raise WriteGuardError(
            f"advisory lock {key} ja' esta tomado: outra execucao do backfill "
            f"esta em curso (nada foi escrito, nada foi aguardado)")


def release_advisory_lock(conn, *, key: int = ADVISORY_LOCK_KEY) -> bool:
    return bool(conn.execute(sqlalchemy_text("SELECT pg_advisory_unlock(:k) AS ok"),
                             {"k": key}).scalar())


# --- Backup duravel por destino e por execucao -----------------------------
#
# Contrato:
#   - contem SOMENTE as duas competencias autorizadas (mesmo predicado do
#     DELETE — o backup nao pode cobrir menos do que a mutacao apaga);
#   - nome deterministico e VALIDADO (nunca interpolacao livre de identificador);
#   - colunas EXPLICITAS do destino (jamais SELECT *);
#   - contagem e checksum conferidos contra a origem ANTES de qualquer DELETE;
#   - COMMITADO em transacao propria, antes da mutacao — se a transacao de
#     mutacao morrer ou o processo cair, o backup sobrevive;
#   - retencao documentada.
BACKUP_RETENTION_DAYS = 90
BACKUP_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]{0,62}$")


def backup_table_name(target: str, stamp: str, *, table: str = TABLE) -> str:
    """Nome deterministico: <tabela>_bkp_<destino>_<carimbo>.

    Inclui o DESTINO porque local e Neon sao execucoes independentes e cada um
    tem o seu backup; sem isso, dois backups da mesma execucao colidiriam ao
    serem comparados. O resultado e' validado contra `BACKUP_NAME_RE` — o nome
    entra numa DDL, e identificador nunca deve ser interpolado sem validacao."""
    if target not in TARGETS:
        raise BackfillUsageError(f"alvo desconhecido: {target!r}")
    if not re.fullmatch(r"\d{8}_\d{6}", stamp):
        raise BackfillValidationError(
            f"carimbo de backup invalido: {stamp!r} (esperado YYYYMMDD_HHMMSS)")
    nome = f"{table}_bkp_{target}_{stamp}"
    if not BACKUP_NAME_RE.fullmatch(nome):
        raise BackfillValidationError(f"nome de backup invalido: {nome!r}")
    return nome


def fingerprint_sql(source: str, cols: tuple[str, ...], predicate: str) -> str:
    """Contagem + checksum sobre COLUNAS EXPLICITAS, em ordem deterministica.

    Aplicado a origem e ao backup, o mesmo texto prova fidelidade. `concat_ws`
    com separador de unidade (0x1F) evita que valores adjacentes se fundam e
    produzam colisao; `ORDER BY linha` torna o agregado independente da ordem
    fisica das linhas."""
    projecao = ", ".join(f"COALESCE({c}::text, '\\N')" for c in cols)
    return (f"SELECT count(*) AS n, "
            f"md5(coalesce(string_agg(linha, '|' ORDER BY linha), '')) AS checksum "
            f"FROM (SELECT concat_ws(chr(31), {projecao}) AS linha "
            f"FROM {source} WHERE {predicate}) t")


# ---------------------------------------------------------------------------
# Orquestracao XLSX -> local -> Neon (desenho; NAO executado)
# ---------------------------------------------------------------------------

STATUS_OK = "OK"
STATUS_PARTIAL = "PARTIAL"
STATUS_REFUSED = "REFUSED"


# --- Cadeia de preflight de escrita ----------------------------------------
#
# AS DEZ PORTAS, na ordem. Cada uma e' independente e fail-closed; a ordem
# existe para que o custo cresca aos poucos — o que da para reprovar sem tocar
# no banco reprova ANTES de abrir conexao.
WRITE_GATES: tuple[str, ...] = (
    "1. dois --scope explicitos, exatamente a allowlist",
    "2. flag de consentimento no ambiente",
    "3. credencial de escrita DEDICADA (nunca a read-only, nunca DATABASE_URL)",
    "4. --target confirmado explicitamente (local|neon), nunca inferido",
    "5. identidade do banco comprovada (EXPECT_DB + tabela presente)",
    "6. primary gravavel (nao em recovery, tx nao read-only, privilegio real)",
    "7. SSL confirmado pelo cliente no Neon",
    "8. advisory lock de sessao adquirido sem espera",
    "9. backup duravel CRIADO, CONFERIDO e COMMITADO",
    "10. reconciliacao previa bate com a expectativa medida",
)


def assert_write_preconditions(
    *, scopes, target, conn=None, env=None,
    gmv_delta_por_escopo=None, keys_added=None, keys_removed=None,
    backup_committed=False,
) -> dict:
    """Roda as dez portas na ordem e devolve o laudo. Levanta na primeira que
    reprovar; nunca acumula falhas para "decidir depois".

    `conn` opcional: sem conexao, as portas 5-8 nao podem ser avaliadas e o
    laudo as marca como `nao_avaliada` — jamais como aprovadas. Isso permite
    testar as portas offline sem que a ausencia vire falso verde."""
    env = os.environ if env is None else env
    laudo: dict[str, str] = {}

    assert_scopes_authorized(list(scopes))
    laudo["1_allowlist"] = "ok"

    if env.get(CONSENT_ENV) != "1":
        raise WriteGuardError(
            f"porta 2: consentimento ausente ({CONSENT_ENV}=1 obrigatorio; "
            f"nada foi escrito)")
    laudo["2_consentimento"] = "ok"

    resolve_write_url(target, env=env)          # levanta se ausente/duplicada
    laudo["3_credencial_dedicada"] = "ok"

    if target not in TARGETS:
        raise BackfillUsageError(f"porta 4: --target invalido: {target!r}")
    laudo["4_target_confirmado"] = target

    if conn is None:
        for porta in ("5_identidade", "6_primary", "7_ssl", "8_advisory_lock"):
            laudo[porta] = "nao_avaliada"
    else:
        assert_target_identity(conn, target, env=env)
        laudo["5_identidade"] = "ok"
        assert_writable_primary(conn, target)
        laudo["6_primary"] = "ok"
        assert_ssl_required(conn, target)
        laudo["7_ssl"] = "ok" if target == TARGET_NEON else "nao_aplicavel"
        acquire_advisory_lock(conn)
        laudo["8_advisory_lock"] = "adquirido"

    laudo["9_backup"] = "commitado" if backup_committed else "pendente"

    if gmv_delta_por_escopo is None:
        laudo["10_expectativa"] = "nao_avaliada"
    else:
        assert_expected_delta(gmv_delta_por_escopo=gmv_delta_por_escopo,
                              keys_added=keys_added or 0,
                              keys_removed=keys_removed or 0)
        laudo["10_expectativa"] = "ok"
    return laudo


# --- Estados parciais entre os dois destinos -------------------------------
#
# Local e Neon sao DOIS bancos, com DUAS transacoes independentes. Nao existe
# transacao distribuida aqui e o modulo nao encena uma. O que existe e' uma
# ordem obrigatoria (local primeiro) e uma tabela de estados possiveis, cada um
# com a acao correta — porque um estado parcial sem nome vira "deu erro" e
# alguem repete o comando inteiro.
PARTIAL_STATES: dict[str, dict[str, str]] = {
    "LOCAL_OK_NEON_FALHOU": {
        "significado": "local commitado e validado; Neon nao aplicou",
        "visivel_para_o_usuario": "a Torre continua servindo o dado ANTIGO "
                                  "(a Torre le o Neon)",
        "acao": "repetir SOMENTE a propagacao local -> Neon, com novo backup "
                "no Neon; nunca reprocessar XLSX nem reescrever o local",
        "backup_util": "o backup do Neon daquela tentativa, se chegou a ser "
                       "commitado",
    },
    "NEON_OK_LOCAL_FALHOU": {
        "significado": "estado PROIBIDO por contrato — a ordem impede que o "
                       "Neon seja escrito antes do local",
        "visivel_para_o_usuario": "a Torre mostraria dado que a fonte local "
                                  "nao tem: divergencia silenciosa",
        "acao": "se ocorrer, e' bug de orquestracao: restaurar o Neon pelo "
                "backup e investigar antes de qualquer nova tentativa",
        "backup_util": "backup do Neon (restauracao imediata)",
    },
    "COMMIT_INDETERMINADO": {
        "significado": "o commit foi enviado e a resposta se perdeu; nao se "
                       "sabe se foi aplicado",
        "visivel_para_o_usuario": "indeterminado ate inspecao",
        "acao": "NAO repetir. Inspecionar o destino contra o backup (contagem "
                "e checksum) e so' entao decidir",
        "backup_util": "e' o unico jeito de saber o que havia antes",
    },
}


def describe_partial_state(*, local: str, neon: str) -> dict:
    """Traduz o par de resultados no estado nomeado e na acao correta."""
    if local == STATUS_OK and neon == STATUS_OK:
        return {"estado": STATUS_OK, "acao": "nenhuma"}
    if local == "INDETERMINATE" or neon == "INDETERMINATE":
        return {"estado": "COMMIT_INDETERMINADO", **PARTIAL_STATES["COMMIT_INDETERMINADO"]}
    if local == STATUS_OK and neon != STATUS_OK:
        return {"estado": "LOCAL_OK_NEON_FALHOU", **PARTIAL_STATES["LOCAL_OK_NEON_FALHOU"]}
    if neon == STATUS_OK and local != STATUS_OK:
        return {"estado": "NEON_OK_LOCAL_FALHOU", **PARTIAL_STATES["NEON_OK_LOCAL_FALHOU"]}
    return {"estado": STATUS_REFUSED, "acao": "nada foi escrito"}


def plan_local_then_neon(scopes: list[tuple[str, str]]) -> dict:
    """Contrato do caminho completo, em ordem obrigatoria.

    Os dois bancos NAO participam de uma transacao distribuida: sao dois
    commits separados, e o estado parcial (local commitado, Neon nao) e' um
    resultado POSSIVEL e explicitamente nomeado — `PARTIAL` — nunca mascarado
    como sucesso nem como falha total."""
    return {
        "scopes": list(scopes),
        "gates": list(WRITE_GATES),
        "ordem": [
            "1. preflight das 10 portas no LOCAL",
            "2. backup duravel do LOCAL, conferido e COMMITADO (transacao propria)",
            "3. XLSX -> scoped replace no LOCAL (transacao de mutacao, separada)",
            "4. validar LOCAL pos-commit (contagem, chave real, escopo)",
            "5. preflight das 10 portas no NEON",
            "6. backup duravel do NEON, conferido e COMMITADO (transacao propria)",
            "7. LOCAL -> scoped replace dos MESMOS escopos no NEON (transacao de mutacao)",
            "8. validar NEON contra LOCAL (paridade de chaves e agregados por escopo)",
            "9. registrar auditoria das DUAS etapas (run_id comum)",
        ],
        "estados_parciais": PARTIAL_STATES,
        "atomicidade": "NAO existe transacao distribuida entre local e Neon. "
                       "Sao dois commits independentes e o estado parcial e' "
                       "um resultado possivel, nomeado e com acao definida.",
        "retry": "ZERO retry automatico em qualquer etapa. A repeticao e' "
                 "decisao humana, depois de inspecionar destino e backup.",
        "proibido": [
            "aplicar somente no NEON deixando o LOCAL antigo",
            "TRUNCATE de tabela inteira",
            "transacao distribuida ficticia entre os dois bancos",
            "backup em TEMP TABLE (morre com a sessao)",
            "backup na MESMA transacao da mutacao (cai junto com ela)",
            "SELECT * entre local e Neon (schemas diferentes)",
            "ingested_at no SQL do destino local (a coluna nao existe la)",
            "UPSERT como unico mecanismo",
            "retry automatico de qualquer etapa",
        ],
        "resultados_possiveis": {
            STATUS_OK: "as duas etapas commitaram e validaram",
            STATUS_PARTIAL: "LOCAL commitado, NEON falhou -> retry SOMENTE da etapa 3-4",
            STATUS_REFUSED: "validacao reprovou antes de qualquer escrita",
        },
        "retry_partial": "decisao HUMANA; se autorizada, somente a propagacao "
                         "LOCAL -> NEON, nunca reprocessar XLSX",
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

# ===========================================================================
# Gate SH-API-2E3 — orquestracao do --apply na CLI
# ===========================================================================
#
# Ate aqui o caminho de escrita existia como BIBLIOTECA coberta por fakes e
# nunca era alcancado por `main()`: a CLI recusava `--apply` com um `return`
# incondicional. Este bloco liga as duas pontas sem afrouxar nenhuma porta.
#
# Ordem deliberada, do mais barato ao mais caro. Tudo que da para reprovar sem
# tocar em disco reprova antes de ler arquivo; tudo que da para reprovar sem
# banco reprova antes de abrir conexao. Uma conexao gravavel so' e' aberta
# depois que argumentos, allowlist, origem, consentimento e credencial ja
# passaram.
# ---------------------------------------------------------------------------


class PreflightTransactionError(BackfillValidationError):
    """A transacao implicita do preflight nao pode ser encerrada.

    Sem isso o executor nao consegue abrir a propria transacao, e insistir
    deixaria o backup e a mutacao dentro de uma transacao que ninguem
    declarou. Nada e' criado nem mutado."""


def close_preflight_transaction(conn) -> bool:
    """Encerra a transacao IMPLICITA aberta pelos SELECTs de preflight.

    CAUSA (medida no Gate SH-API-2E4): no SQLAlchemy 2.0 o primeiro
    `conn.execute(...)` faz AUTOBEGIN. As portas 5-8 sao consultas, entao ao
    fim do preflight a conexao ja esta em transacao — e o
    `ScopedReplaceExecutor.begin()` seguinte levanta
    `InvalidRequestError: This connection has already initialized a
    SQLAlchemy Transaction() object via begin() or autobegin`.

    O rollback e' seguro e deliberado nesta posicao:
      - o preflight so' LEU; nao ha nada a preservar;
      - o advisory lock e' de SESSAO (`pg_try_advisory_lock`), nao de
        transacao, portanto SOBREVIVE ao rollback. Fosse
        `pg_advisory_xact_lock`, este rollback soltaria o lock e abriria
        justamente a janela que ele existe para fechar;
      - o executor continua dono de transacoes EXPLICITAS e separadas para
        backup e publicacao. Nada aqui reaproveita transacao implicita.

    Devolve True se havia transacao e ela foi encerrada; False se nao havia.
    Levanta `PreflightTransactionError` se a conexao continuar em transacao."""
    tinha = bool(conn.in_transaction())
    if tinha:
        conn.rollback()
    if conn.in_transaction():
        raise PreflightTransactionError(
            "a transacao implicita do preflight continua aberta depois do "
            "rollback: o executor nao pode abrir a propria transacao "
            "(nada foi criado nem mutado)")
    return tinha


def _default_writable_engine(url: str):  # pragma: no cover - requer banco real
    """Engine GRAVAVEL. Isolada numa funcao propria para que os testes possam
    substitui-la e para que exista um unico ponto no modulo capaz de abrir
    conexao de escrita — auditavel por leitura."""
    from sqlalchemy import create_engine
    return create_engine(url)


#: Excecoes cujas mensagens foram ESCRITAS para nao vazar nada (nunca contem
#: DSN, host, usuario, senha, SQL nem parametros). Só estas sao impressas na
#: integra; qualquer outra e' reduzida ao nome da classe.
SAFE_TO_PRINT = (
    ScopeNotAuthorizedError, WriteGuardError, ExpectationMismatchError,
    BackfillIdentityError, BackfillUsageError, BackfillValidationError,
)


def _sanitize(e: BaseException) -> str:
    """Texto seguro para stderr.

    Uma excecao do driver carrega o SQL e os parametros — e os parametros do
    INSERT sao linhas do mart. Imprimir `str(e)` de qualquer excecao seria um
    vazamento silencioso, entao o default e' o NOME DA CLASSE e nada mais."""
    if isinstance(e, SAFE_TO_PRINT):
        return str(e)
    return f"{type(e).__name__} (detalhe omitido para nao vazar dado/credencial)"


def run_apply(args, *, env=None, engine_factory=None) -> int:
    """Executa o backfill num UNICO destino. Nunca nos dois.

    Cada chamada trata `--target local` OU `--target neon`. Nao existe comando
    que atualize os dois bancos: sao transacoes independentes, o estado parcial
    e' possivel e nomeado, e esconder isso atras de um comando unico venderia
    uma atomicidade que nao existe."""
    env = os.environ if env is None else env
    factory = engine_factory or _default_writable_engine

    # -- FASE 1: argumentos, allowlist, origem e credencial. Zero I/O gravavel.
    try:
        # `parse_scopes` deduplica em silencio (contrato dele, usado tambem
        # pelos dry-runs). Numa ESCRITA, `--scope` repetido e' invocacao
        # ambigua: o operador digitou algo que nao queria, e deduplicar
        # esconderia isso. A checagem e feita no argumento CRU, antes do parse,
        # sem alterar o contrato de `parse_scopes`.
        crus = [s.strip() for s in (args.scope or [])]
        if len(crus) != len(set(crus)):
            repetidos = sorted({s for s in crus if crus.count(s) > 1})
            raise ScopeNotAuthorizedError(
                f"--scope repetido na linha de comando: {', '.join(repetidos)}. "
                f"Uma escrita nao aceita invocacao ambigua (nada foi escrito)")
        scopes = parse_scopes(args.scope) if args.scope else []
        assert_scopes_authorized(scopes)                       # porta 1

        if args.target is None:
            raise WriteGuardError(
                "porta 4: --apply exige --target local|neon explicito "
                "(o destino nunca e' inferido; nada foi escrito)")
        if not args.source_root:
            # O default (`loader.SHOPEE_ROOT`) e' a pasta de trabalho, onde
            # arquivo novo aparece sem aviso. Uma escrita tem de declarar de
            # ONDE veio o dado; herdar a pasta padrao torna a origem implicita.
            raise WriteGuardError(
                "--apply exige --source-root explicito: a origem do dado nao "
                "pode ser herdada da pasta padrao (nada foi escrito)")
        root = resolve_source_root(args.source_root)

        laudo = assert_write_preconditions(                    # portas 2, 3, 4
            scopes=scopes, target=args.target, conn=None, env=env)
        url = resolve_write_url(args.target, env=env)
    # ORDEM DE CAPTURA IMPORTA: `ScopeNotAuthorizedError` e
    # `BackfillIdentityError` herdam de `BackfillUsageError`, e
    # `WriteGuardError`/`ExpectationMismatchError` de `BackfillValidationError`.
    # Se `BackfillUsageError` viesse primeiro, uma violacao de ALLOWLIST sairia
    # como erro de uso (5) em vez de recusa de guardrail (2) — o operador leria
    # "digitei errado" onde a verdade e' "esta operacao nao esta autorizada".
    except (ScopeNotAuthorizedError, BackfillIdentityError, WriteGuardError,
            ExpectationMismatchError, BackfillValidationError) as e:
        print(f"APPLY RECUSADO: {_sanitize(e)}", file=sys.stderr)
        return EXIT_VALIDATION_REFUSED
    except BackfillUsageError as e:
        # Sobra aqui o erro de uso de verdade: --scope malformado, source-root
        # inexistente, alvo desconhecido.
        print(f"ERRO DE USO: {_sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE

    for porta, estado in laudo.items():
        print(f"  porta {porta}: {estado}", file=sys.stderr)

    # -- FASE 2: staging (arquivos e memoria). Ainda sem banco.
    try:
        staging = build_staging(scopes, root)
        assert_staging_unique(staging)
        assert_staging_within_scope(staging)
    except (loader.ShopeeSnapshotError, loader.ShopeeProductInputError) as e:
        print(f"VALIDACAO RECUSADA (triagem de arquivos): {_sanitize(e)}", file=sys.stderr)
        return EXIT_VALIDATION_REFUSED
    except BackfillValidationError as e:
        print(f"VALIDACAO RECUSADA: {_sanitize(e)}", file=sys.stderr)
        return EXIT_VALIDATION_REFUSED

    # -- FASE 3: conexao GRAVAVEL. Primeira e unica.
    #
    # A ABERTURA fica no seu proprio try: a excecao de conexao do driver
    # costuma carregar host, usuario e banco na mensagem, e deixa-la propagar
    # imprimiria um traceback com esses dados. Falha aqui e' problema de
    # configuracao/ambiente, nao de guardrail -> EXIT_USAGE.
    try:
        engine = factory(url)
        conn = engine.connect()
    except (KeyboardInterrupt, SystemExit):
        raise
    except OPERATIONAL_ERRORS as e:
        print(f"ERRO DE CONFIGURACAO: nao foi possivel abrir conexao gravavel "
              f"com o destino '{args.target}': {_sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE

    lock_tomado = False
    try:
        assert_target_identity(conn, args.target, env=env)     # porta 5
        assert_writable_primary(conn, args.target)             # porta 6
        assert_ssl_required(conn, args.target)                 # porta 7
        acquire_advisory_lock(conn)                            # porta 8
        lock_tomado = True
        print("  porta 5_identidade: ok", file=sys.stderr)
        print("  porta 6_primary: ok", file=sys.stderr)
        print("  porta 7_ssl: ok" if args.target == TARGET_NEON
              else "  porta 7_ssl: nao_aplicavel", file=sys.stderr)
        print("  porta 8_advisory_lock: adquirido", file=sys.stderr)

        # Encerra a transacao IMPLICITA que os SELECTs do preflight abriram
        # (autobegin do SQLAlchemy 2.0). Depois do lock, de proposito: o lock
        # e' de sessao e sobrevive ao rollback, entao a janela protegida nao
        # se abre em momento nenhum.
        encerrou = close_preflight_transaction(conn)
        print(f"  transacao implicita do preflight: "
              f"{'encerrada' if encerrou else 'nao havia'}", file=sys.stderr)

        # O lock fica na MESMA sessao que faz backup e mutacao — por isso o
        # executor recebe esta `conn`, e nao uma nova. Um lock numa conexao e
        # a escrita em outra protegeria a coisa errada.
        executor = ScopedReplaceExecutor(conn, target=args.target)
        codigo = apply_scoped_replace(staging, executor=executor)  # portas 9-10
    except (KeyboardInterrupt, SystemExit):
        # Ordem de encerramento: o `finally` abaixo libera lock e conexao, e a
        # excecao PROPAGA. Nunca vira exit code — transformar interrupcao em
        # codigo de saida faria o processo mentir sobre o proprio estado.
        raise
    except (WriteGuardError, BackfillIdentityError,
            PreflightTransactionError) as e:
        print(f"APPLY RECUSADO: {_sanitize(e)}", file=sys.stderr)
        codigo = EXIT_VALIDATION_REFUSED
    except OPERATIONAL_ERRORS as e:
        # Chega aqui, sobretudo, a falha do BACKUP (porta 9), que propaga de
        # `apply_scoped_replace` depois de desfazer a propria transacao. Nada
        # foi mutado: e' recusa de guardrail, nao rollback de mutacao.
        print(f"APPLY RECUSADO (guardrail de backup ou preflight): "
              f"{_sanitize(e)}", file=sys.stderr)
        codigo = EXIT_VALIDATION_REFUSED
    finally:
        # Liberacao SEMPRE, inclusive em interrupcao. O lock e' de sessao: se a
        # conexao fechar sem liberar, o Postgres solta sozinho, mas depender
        # disso deixaria a janela aberta enquanto o pool nao recicla.
        if lock_tomado:
            try:
                release_advisory_lock(conn)
            except OPERATIONAL_ERRORS:
                print("  aviso: falha ao liberar o advisory lock; a sessao sera "
                      "encerrada e o Postgres o libera no fim dela",
                      file=sys.stderr)
        try:
            conn.close()
        except OPERATIONAL_ERRORS:
            pass
        try:
            engine.dispose()
        except (AttributeError, *OPERATIONAL_ERRORS):
            pass

    # ZERO RETRY: qualquer codigo diferente de 0 encerra aqui. Nao ha segunda
    # tentativa em nenhuma hipotese — o caso indeterminado e' justamente aquele
    # em que repetir poderia duplicar o efeito.
    rotulo = {
        EXIT_OK: "APPLY CONCLUIDO e commitado",
        EXIT_ROLLED_BACK: "APPLY revertido: rollback CONFIRMADO, nada mudou",
        EXIT_INDETERMINATE: ("APPLY INDETERMINADO: o commit pode ou nao ter "
                             "sido aplicado. NAO repita. Inspecione o destino "
                             "contra a tabela de backup (contagem e checksum)"),
        EXIT_VALIDATION_REFUSED: "APPLY RECUSADO: nada foi escrito",
    }.get(codigo, f"APPLY terminou com codigo {codigo}")
    print(f"{rotulo} (destino: {args.target}; sem retry)", file=sys.stderr)
    return codigo


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
        # Gate SH-API-2E3: o caminho real. `run_apply` roda as dez portas na
        # ordem, abre UMA conexao gravavel so' depois que as offline passaram,
        # e trata um unico destino por chamada.
        return run_apply(args)

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
