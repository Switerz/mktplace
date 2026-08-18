"""
Sync incremental idempotente das tabelas de produto para o Neon.

Uso:
    python -m pipelines.sync_produtos --source shopee
    python -m pipelines.sync_produtos --source ml
    python -m pipelines.sync_produtos --source tiktok
    python -m pipelines.sync_produtos --source all
    python -m pipelines.sync_produtos --source tiktok --days 14

Fontes (somente leitura):
    shopee  -> local PG localhost:5432/mktplace_control (marts.fact_shopee_product_monthly)
    ml      -> RDS gold.ml_produto_ranking  (snapshot — full refresh sempre)
    tiktok  -> RDS gold.tiktok_product_daily (incremental por date)

Destino (escrita):
    Neon marts.*  via ON CONFLICT DO UPDATE (idempotente)

Regras de seguranca:
    - Nao escreve nas fontes (RDS ou local PG)
    - Nao deleta dados do Neon (apenas upsert)
    - brands fora do escopo sao filtrados na leitura
"""
import argparse
import hashlib
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dotenv import load_dotenv

# Carregar .env ANTES de ler as variaveis de conexao abaixo — bug anterior:
# load_dotenv() so' era chamado dentro de main(), depois que NEON_URL/RDS_URL/
# LOCAL_URL ja tinham sido lidas do ambiente no import do modulo, entao o
# script so' funcionava se as variaveis já estivessem exportadas no shell.
load_dotenv(dotenv_path=str(Path(__file__).resolve().parent.parent / ".env"))
load_dotenv()

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
BRANDS_IN_SCOPE = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
DEFAULT_TIKTOK_DAYS = 7  # re-sync ultimos N dias (garante idempotencia em meses parciais)

# Abaixo deste percentual do total anterior no Neon, uma fonte que fez full
# refresh/backfill e' considerada suspeita e a carga e' abortada sem commit
# (protege contra fonte RDS/local retornando parcial por erro silencioso).
MIN_ROWS_RATIO = 0.5

NEON_URL  = os.environ.get("DATABASE_URL", "")
RDS_URL   = os.environ.get("DATAMART_DATABASE_URL", "")
LOCAL_URL = os.environ.get(
    "LOCAL_PG_URL",
    "postgresql://postgres:postgres@localhost:5432/mktplace_control",
)


def _assert_distinct_targets() -> None:
    """Guarda contra .env mal configurado apontando origem e destino para o mesmo host.

    Nao decodifica credenciais: compara apenas as strings de conexao completas.
    """
    urls = {"NEON (destino)": NEON_URL, "RDS (fonte)": RDS_URL, "LOCAL (fonte)": LOCAL_URL}
    if NEON_URL and NEON_URL in (RDS_URL, LOCAL_URL):
        raise RuntimeError(
            "DATABASE_URL (Neon/destino) e igual a uma das fontes (RDS/local). "
            "Sync abortado para evitar escrita no banco errado."
        )
    _ = urls  # mantido para depuracao futura sem expor valores em log


def _neon():
    if not NEON_URL:
        raise RuntimeError("DATABASE_URL nao definido")
    return psycopg2.connect(NEON_URL, connect_timeout=15)


def _rds():
    if not RDS_URL:
        raise RuntimeError("DATAMART_DATABASE_URL nao definido")
    return psycopg2.connect(RDS_URL, cursor_factory=RealDictCursor, connect_timeout=15)


def _local():
    return psycopg2.connect(LOCAL_URL, cursor_factory=RealDictCursor, connect_timeout=5)


def _brands_sql(brands=BRANDS_IN_SCOPE):
    return "(" + ",".join(f"'{b}'" for b in sorted(brands)) + ")"


def _active_brands(default=BRANDS_IN_SCOPE) -> set:
    """Le brands ativas de marts.dim_loja no Neon (fonte de verdade do projeto).

    Cai para o conjunto hardcoded se a leitura falhar (Neon indisponivel etc.),
    para nao travar o sync inteiro por um problema de conectividade pontual.
    """
    try:
        conn = _neon()
        cur = conn.cursor()
        cur.execute("SELECT brand_key FROM marts.dim_loja WHERE ativo = true")
        rows = {r[0] for r in cur.fetchall()}
        cur.close(); conn.close()
        if rows:
            return rows
    except Exception as e:
        print(f"[aviso] falha ao ler marts.dim_loja, usando lista hardcoded: {e}")
    return set(default)


# ---------------------------------------------------------------------------
# Auditoria (audit.source_sync_run) — mesmo contrato usado por
# pipelines/ingestion/daily_performance.py, para manter um unico historico
# de execucoes consultavel via docs/runbook_sync_produtos.md.
# ---------------------------------------------------------------------------
def _audit_start(conn, source_name: str, marketplace_id: int) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO audit.source_sync_run (source_name, marketplace_id, status, started_at)
        VALUES (%s, %s, 'running', NOW())
        RETURNING sync_run_id
        """,
        (source_name, marketplace_id),
    )
    run_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return run_id


def _audit_finish(conn, run_id: int, status: str, extracted: int, loaded: int,
                   min_d=None, max_d=None, error: str | None = None) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE audit.source_sync_run SET
            finished_at = NOW(), status = %s, rows_extracted = %s, rows_loaded = %s,
            source_min_date = %s, source_max_date = %s, error_message = %s
        WHERE sync_run_id = %s
        """,
        (status, extracted, loaded, min_d, max_d, error, run_id),
    )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Shopee: local PG -> Neon
# Estrategia: sincroniza ref_months onde Neon tem menos linhas que a fonte,
#             mais o mes corrente (para capturar atualizacoes recentes).
# ---------------------------------------------------------------------------
def sync_shopee(full: bool = False, brands: set = None) -> dict:
    brands = brands or BRANDS_IN_SCOPE
    audit_conn = _neon()
    run_id = _audit_start(audit_conn, "shopee_product_monthly", marketplace_id=3)

    try:
        print("[shopee] lendo fonte (local PG)...")
        src = _local()
        sc = src.cursor(cursor_factory=RealDictCursor)

        if full:
            sc.execute(
                "SELECT * FROM marts.fact_shopee_product_monthly WHERE brand = ANY(%s)",
                (list(brands),),
            )
        else:
            # Descobrir quais ref_months precisam de sync:
            # 1) mes atual e anterior (dados podem ter mudado)
            today = date.today()
            first_this  = date(today.year, today.month, 1)
            first_prev  = (first_this - timedelta(days=1)).replace(day=1)
            sc.execute(
                """
                SELECT * FROM marts.fact_shopee_product_monthly
                WHERE brand = ANY(%s)
                  AND ref_month >= %s
                """,
                (list(brands), first_prev),
            )

        rows = sc.fetchall()
        sc.close(); src.close()
        print(f"[shopee] fonte: {len(rows)} linhas")

        if not rows:
            print("[shopee] nada a sincronizar")
            _audit_finish(audit_conn, run_id, "success", 0, 0)
            audit_conn.close()
            return {"source": 0, "upserted": 0}

        dst = _neon()
        try:
            dc = dst.cursor()

            UPSERT = """
                INSERT INTO marts.fact_shopee_product_monthly
                    (ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
                     gmv, units_sold, completed_orders, canceled_orders,
                     cancel_rate_pct, unique_buyers, avg_price)
                VALUES %s
                ON CONFLICT (ref_month, brand, sku_ref_key, product_name)
                DO UPDATE SET
                    sku_ref          = EXCLUDED.sku_ref,
                    variation_name   = EXCLUDED.variation_name,
                    gmv              = EXCLUDED.gmv,
                    units_sold       = EXCLUDED.units_sold,
                    completed_orders = EXCLUDED.completed_orders,
                    canceled_orders  = EXCLUDED.canceled_orders,
                    cancel_rate_pct  = EXCLUDED.cancel_rate_pct,
                    unique_buyers    = EXCLUDED.unique_buyers,
                    avg_price        = EXCLUDED.avg_price,
                    ingested_at      = NOW()
            """

            batch = [
                (
                    r["ref_month"], r["brand"], r["sku_ref"], r["sku_ref_key"],
                    r["product_name"], r["variation_name"],
                    r["gmv"], r["units_sold"], r["completed_orders"], r["canceled_orders"],
                    r["cancel_rate_pct"], r["unique_buyers"], r["avg_price"],
                )
                for r in rows
            ]
            execute_values(dc, UPSERT, batch, page_size=500)
            dst.commit()
            dc.close()
        except Exception:
            dst.rollback()
            raise
        finally:
            dst.close()

        print(f"[shopee] Neon: {len(batch)} linhas upserted")
        ref_months = [r["ref_month"] for r in rows]
        _audit_finish(
            audit_conn, run_id, "success", len(rows), len(batch),
            min(ref_months), max(ref_months),
        )
        audit_conn.close()
        return {"source": len(rows), "upserted": len(batch)}

    except Exception as exc:
        _audit_finish(audit_conn, run_id, "failed", 0, 0, error=str(exc)[:500])
        audit_conn.close()
        raise


# ---------------------------------------------------------------------------
# Gate C2.4 (2026-07-17) — retry estrito e unico para conflito de recovery
# numa read replica do RDS. Achado no Gate C2.2/C2.3: gold.ml_produto_ranking
# e' uma VIEW cara (joins/agregacoes sobre ml_order_line_items/ml_orders/
# ml_ads_items, dezenas de milhares de linhas), lida contra um read replica
# com hot_standby_feedback=off — nessas condicoes, o Postgres pode cancelar
# uma query longa ("canceling statement due to conflict with recovery")
# quando o replay do WAL precisa remover versoes de linha que a query ainda
# precisa. Historico (audit.source_sync_run) mostra isso como raro (1 falha
# em 9+ execucoes), mas a condicao estrutural pode recorrer a qualquer
# momento — nao e' um bug de dado, e' comportamento documentado do Postgres
# para esse tipo de conflito, e a propria documentacao do Postgres recomenda
# retry para essa classe de erro.
#
# Escopo do retry, deliberadamente estreito: cobre SOMENTE a leitura da fonte
# RDS (abrir conexao + executar + fetchall) — nunca _audit_start/_audit_finish,
# nunca a leitura de prev_count no Neon, nunca a conexao/escrita de destino no
# Neon. Isso garante: _audit_start roda uma unica vez por chamada de sync_ml,
# a escrita no Neon so' e' tentada depois de uma leitura RDS bem-sucedida (nunca
# escrita parcial), e nenhum outro tipo de erro (RuntimeError de validacao do
# MIN_ROWS_RATIO, erro de conexao generico, etc) e' mascarado por um retry
# que nao faz sentido para ele.
def _is_recovery_conflict_error(exc: BaseException) -> bool:
    """True somente para o conflito de recovery especifico de read replica —
    nunca classifica um OperationalError/erro de conexao generico como
    retryable so' pelo tipo. `pgcode` (quando disponivel num psycopg2 real)
    e' so' reforco opcional de contexto no log, nunca uma dependencia: um
    teste com exception fake sem `pgcode` deve continuar funcionando."""
    message = str(exc)
    return (
        "conflict with recovery" in message
        or "User query might have needed to see row versions" in message
    )


def _sleep(seconds: float) -> None:
    """Wrapper fino sobre time.sleep — existe so' para ser monkeypatchado
    nos testes (evita esperar o backoff real de verdade)."""
    time.sleep(seconds)


def _read_rds_with_recovery_retry(read_fn, *, max_attempts=2, backoff_seconds=8):
    """Executa read_fn() com retry unico e estrito para conflito de recovery.

    Qualquer erro que nao seja _is_recovery_conflict_error(exc) sobe
    imediatamente, sem retry (erro de dado/validacao, conexao generica,
    etc). Se a ultima tentativa tambem falhar por conflito de recovery, o
    erro original e' propagado (sem mascarar). Nunca envolve escrita —
    read_fn deve ser uma leitura pura, idempotente por natureza."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return read_fn()
        except Exception as exc:
            if attempt >= max_attempts or not _is_recovery_conflict_error(exc):
                raise
            print("[ml] RDS recovery conflict during ML product read; retrying once...")
            _sleep(backoff_seconds)


# ---------------------------------------------------------------------------
# Snapshot ML — helpers puros da reconciliacao
# ---------------------------------------------------------------------------
# Deliberadamente locais e minusculos, em vez de importados de
# pipelines/sync_serving_snapshots.py: aquele modulo tem allowlist LITERAL de
# exatamente dois targets, e registrar um terceiro spec so' para reaproveitar
# tres funcoes puras transformaria uma allowlist fechada em framework.

#: Colunas de negocio do ranking ML, na ordem do INSERT. `refreshed_at` entra
#: separado porque recebe o MESMO instante para todas as linhas do snapshot.
ML_BUSINESS_COLUMNS = (
    "brand", "item_id", "seller_sku", "title",
    "gross_revenue", "units_sold", "unique_buyers", "units_per_buyer",
    "cancel_rate_pct", "ad_spend", "ad_roas", "ad_acos_pct", "days_advertised",
    "revenue_share_pct", "cumulative_revenue_pct", "estimated_margin",
    "price_spread_pct", "pareto_bucket", "revenue_velocity",
    "ad_efficiency", "action_signal", "product_status",
    "first_sale", "last_sale",
)

ML_KEY_COLUMNS = ("brand", "item_id")

#: Somaveis, para a reconciliacao por agregado.
ML_ADDITIVE_COLUMNS = ("gross_revenue", "units_sold", "unique_buyers", "ad_spend")

#: Advisory lock proprio do ranking ML. Fora da faixa das fatos do Gate S2
#: (906120006/907120007/908120008) e das duas do S3 (909120009/910120010).
ML_RANKING_ADVISORY_LOCK_KEY = 911_120_011

#: Nome da staging temporaria. `pg_temp` + `ON COMMIT DROP`: some no commit e
#: tambem no rollback.
ML_STAGING_NAME = "stg_ml_produto_ranking"


def _ml_key(row: dict) -> tuple:
    return tuple(str(row[c]) for c in ML_KEY_COLUMNS)


def _ml_canonico(valor) -> str:
    """Serializacao canonica para o fingerprint. `Decimal` normalizado para que
    1.10 e 1.1 nao gerem hashes diferentes; None recebe marcador improduzivel."""
    if valor is None:
        return "\x00"
    if isinstance(valor, Decimal):
        return format(valor.normalize(), "f")
    return str(valor)


def ml_fingerprint(rows: list) -> str:
    """Hash determinístico da fotografia, calculado em PYTHON.

    Nao em SQL de proposito: `MD5(STRING_AGG(... ORDER BY texto))` depende de
    colacao, e as duas pontas deste projeto usam locales diferentes
    (`en_US.UTF-8` no RDS, `C.UTF-8` no Neon) — o mesmo dado geraria hashes
    distintos. Ordenar aqui elimina a classe do problema.
    """
    h = hashlib.md5()
    for r in sorted(rows, key=_ml_key):
        h.update("|".join(_ml_canonico(r.get(c)) for c in ML_BUSINESS_COLUMNS).encode("utf-8"))
        h.update(b";")
    return h.hexdigest()


def ml_aggregates(rows: list) -> dict:
    """Agregados em `Decimal`, nunca `float`: somar milhares de valores
    monetarios em ponto flutuante ja divergiu neste projeto."""
    out = {"count": len(rows), "keys": len({_ml_key(r) for r in rows})}
    for c in ML_ADDITIVE_COLUMNS:
        total = Decimal(0)
        for r in rows:
            v = r.get(c)
            if v is not None:
                total += Decimal(str(v))
        out[f"sum_{c}"] = total
    return out


def ml_compare(esperado: dict, obtido: dict) -> list:
    return [f"{k}: fotografia={v} destino={obtido.get(k)}"
            for k, v in esperado.items() if obtido.get(k) != v]


# ---------------------------------------------------------------------------
# Projecao canonica da fotografia para os TIPOS do destino
# ---------------------------------------------------------------------------
# A fonte `gold.ml_produto_ranking` usa NUMERIC sem escala e carrega precisao
# cheia (ex.: cumulative_revenue_pct = 94.838155642022304628). O destino declara
# escalas (NUMERIC(8,4), NUMERIC(18,2), ...), e a staging e' criada com
# `LIKE marts.fact_ml_produto_ranking`, herdando essas escalas: o PostgreSQL
# arredonda no INSERT.
#
# Comparar o fingerprint da staging tipada com o Decimal bruto da Gold e'
# incorreto — foi o que reprovou a primeira tentativa de carga em 18/08/2026,
# com 1.388 de 1.648 linhas divergindo so' em cumulative_revenue_pct. O
# arredondamento nao e' perda nova: o upsert anterior ao Gate S3 gravava
# exatamente os mesmos valores.
#
# A correcao NAO relaxa o guardrail. Ela coloca as duas pontas na mesma lingua:
# projeta a fotografia para o tipo do destino e compara projetada x staging x
# destino. Diferenca acima do arredondamento declarado continua reprovando.

#: Colunas de `ML_BUSINESS_COLUMNS` que o destino declara como NUMERIC com
#: escala. Allowlist EXPLICITA: nem o codigo descobre colunas sozinho, nem
#: aceita que o schema mude sem que alguem olhe.
ML_NUMERIC_COLUMNS = (
    "gross_revenue", "units_per_buyer", "cancel_rate_pct", "ad_spend",
    "ad_roas", "ad_acos_pct", "revenue_share_pct", "cumulative_revenue_pct",
    "estimated_margin", "price_spread_pct",
)


def ml_target_numeric_scales(cur) -> dict:
    """Escalas NUMERIC realmente declaradas em `marts.fact_ml_produto_ranking`.

    Lidas do `information_schema`, nunca hardcoded: quem decide para qual escala
    o PostgreSQL converte a fotografia e' o schema, nao este modulo.

    Levanta antes de qualquer escrita se codigo e schema divergirem: coluna da
    allowlist ausente, deixou de ser `numeric`, escala nula/invalida, ou coluna
    que virou `numeric` com escala sem entrar na allowlist. Nos quatro casos, o
    fingerprint esperado seria calculado sobre uma premissa falsa.
    """
    cur.execute("""
        SELECT column_name, data_type, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = 'marts' AND table_name = 'fact_ml_produto_ranking'
    """)
    schema = {r["column_name"]: r for r in cur.fetchall()}

    escalas, problemas = {}, []
    for col in ML_NUMERIC_COLUMNS:
        info = schema.get(col)
        if info is None:
            problemas.append(f"{col}: coluna numerica esperada ausente no destino")
            continue
        if info["data_type"] != "numeric":
            problemas.append(
                f"{col}: esperado numeric, schema declara {info['data_type']}")
            continue
        escala = info["numeric_scale"]
        if escala is None or isinstance(escala, bool) or not isinstance(escala, int) \
                or escala < 0:
            problemas.append(f"{col}: numeric_scale invalida ({escala!r})")
            continue
        escalas[col] = escala

    # O inverso importa igual: coluna de negocio que virou numeric com escala
    # sem entrar na allowlist ficaria comparada crua contra staging arredondada.
    for col in ML_BUSINESS_COLUMNS:
        info = schema.get(col)
        if info is None:
            problemas.append(f"{col}: coluna de negocio ausente no destino")
        elif (info["data_type"] == "numeric" and info["numeric_scale"] is not None
                and col not in ML_NUMERIC_COLUMNS):
            problemas.append(
                f"{col}: numeric(scale={info['numeric_scale']}) no schema mas fora "
                "de ML_NUMERIC_COLUMNS")

    if problemas:
        raise RuntimeError(
            "schema do destino incompativel com o codigo, nada foi escrito: "
            + "; ".join(problemas))
    return escalas


def _ml_quantiza(valor, escala: int):
    """Reproduz a conversao para `NUMERIC(p,s)` do PostgreSQL.

    `ROUND_HALF_UP` do modulo `decimal` e' "ties away from zero" — exatamente a
    regra do PostgreSQL — e nao o meio-par que `round()` do Python usa por
    default. Sem isso, 0.00005 e -0.00005 divergiriam da staging.

    Nenhuma tolerancia, nenhum epsilon, nenhuma passagem por float.
    """
    if valor is None:
        return None
    if isinstance(valor, float):
        raise RuntimeError(
            "valor float em coluna numeric: o arredondamento binario nao e' "
            "reproduzivel de forma exata — a fonte precisa entregar Decimal")
    if not isinstance(valor, Decimal):
        # int e afins passam intactos: nao ha' arredondamento a reproduzir.
        return valor
    return valor.quantize(Decimal(1).scaleb(-escala), rounding=ROUND_HALF_UP)


def ml_project_to_target(rows: list, escalas: dict) -> list:
    """Fotografia projetada para os tipos do destino, em lista NOVA.

    `rows` nunca e' mutada: a staging continua recebendo o valor original e e' o
    PostgreSQL que aplica o tipo real. Aqui apenas se calcula o que ele vai
    produzir.

    Toca EXCLUSIVAMENTE as colunas de `escalas`. Texto, data, inteiro, NULL e
    coluna de chave atravessam byte a byte.
    """
    projetadas = []
    for r in rows:
        novo = dict(r)
        for col, escala in escalas.items():
            if col in novo:
                novo[col] = _ml_quantiza(novo[col], escala)
        projetadas.append(novo)
    return projetadas


def ml_validate_snapshot(rows: list) -> list:
    """Reprovacoes possiveis ANTES de qualquer escrita: chave unica, chave nula,
    coluna ausente, tipo nao numerico em coluna somavel."""
    problemas = []
    vistas, dup = set(), 0
    for r in rows:
        k = _ml_key(r)
        if k in vistas:
            dup += 1
        vistas.add(k)
    if dup:
        problemas.append(f"{dup} chave(s) (brand, item_id) duplicada(s) na fotografia")

    faltando = [c for c in ML_BUSINESS_COLUMNS if rows and c not in rows[0]]
    if faltando:
        problemas.append(f"colunas ausentes na fonte: {faltando}")

    nulos = sum(1 for r in rows if any(r.get(c) is None for c in ML_KEY_COLUMNS))
    if nulos:
        problemas.append(f"{nulos} linha(s) com chave nula")

    for r in rows:
        for c in ML_ADDITIVE_COLUMNS:
            v = r.get(c)
            if v is None:
                continue
            if isinstance(v, Decimal) and v.is_nan():
                problemas.append(f"NaN em {c}")
                break
    return problemas


def ml_publish_snapshot(dst, rows: list, now, staging_reader=None) -> dict:
    """UMA transacao: advisory lock -> staging -> validacao -> DELETE integral ->
    INSERT -> reconciliacao contra a FOTOGRAFIA -> commit.

    O `DELETE` sem `WHERE` e' o ponto: e' o que faz chave desaparecida da fonte
    desaparecer do destino. O upsert anterior nunca removia nada, e por isso o
    "full refresh" declarado no docstring deste modulo era falso.

    A reconciliacao compara o destino com `rows` — a fotografia capturada nesta
    execucao — e NUNCA com uma releitura posterior da Gold, que e' mutavel e sem
    dimensao temporal.
    """
    cols = ", ".join(ML_BUSINESS_COLUMNS)
    staging = f"pg_temp.{ML_STAGING_NAME}"
    resultado = {"deleted": 0, "published": 0, "checks": {}}
    leitor = staging_reader or _ml_read_rows

    try:
        dc = dst.cursor(cursor_factory=RealDictCursor)

        # PRIMEIRA acao, antes do lock e muito antes do DELETE: se o schema do
        # destino nao for o que o codigo supoe, a carga morre aqui sem ter
        # tocado em nada e sem ter tomado lock.
        escalas = ml_target_numeric_scales(dc)
        # A fotografia contra a qual tudo sera' reconciliado. `rows` segue
        # intacta e e' ela que vai para a staging.
        foto = ml_project_to_target(rows, escalas)

        dc.execute("SELECT pg_advisory_xact_lock(%s)", (ML_RANKING_ADVISORY_LOCK_KEY,))
        dc.execute(f"""
            CREATE TEMP TABLE {ML_STAGING_NAME}
                (LIKE marts.fact_ml_produto_ranking INCLUDING DEFAULTS)
            ON COMMIT DROP
        """)

        if rows:
            batch = [
                tuple(r[c] for c in ML_BUSINESS_COLUMNS) + (now,)
                for r in rows
            ]
            execute_values(
                dc,
                f"INSERT INTO {staging} ({cols}, refreshed_at) VALUES %s",
                batch, page_size=500,
            )

        staging_rows = leitor(dc, staging)
        problemas = ml_compare(ml_aggregates(foto), ml_aggregates(staging_rows))
        if problemas:
            raise RuntimeError("staging divergiu da fotografia: " + "; ".join(problemas))
        if ml_fingerprint(staging_rows) != ml_fingerprint(foto):
            raise RuntimeError("staging divergiu da fotografia no fingerprint")

        dc.execute("DELETE FROM marts.fact_ml_produto_ranking")
        resultado["deleted"] = dc.rowcount

        dc.execute(f"""
            INSERT INTO marts.fact_ml_produto_ranking ({cols}, refreshed_at)
            SELECT {cols}, refreshed_at FROM {staging}
        """)
        resultado["published"] = dc.rowcount

        destino_rows = leitor(dc, "marts.fact_ml_produto_ranking")
        esperado = ml_aggregates(foto)
        problemas = ml_compare(esperado, ml_aggregates(destino_rows))
        if problemas:
            raise RuntimeError("destino divergiu da fotografia: " + "; ".join(problemas))

        so_foto = {_ml_key(r) for r in foto} - {_ml_key(r) for r in destino_rows}
        so_dest = {_ml_key(r) for r in destino_rows} - {_ml_key(r) for r in foto}
        if so_foto or so_dest:
            raise RuntimeError(
                f"EXCEPT bidirecional divergiu: fotografia-destino={len(so_foto)} "
                f"destino-fotografia={len(so_dest)}")

        fp = ml_fingerprint(foto)
        if ml_fingerprint(destino_rows) != fp:
            raise RuntimeError("fingerprint do destino difere do da fotografia")

        resultado["checks"] = {
            "aggregates": {k: str(v) for k, v in esperado.items()},
            "except_both_ways": (len(so_foto), len(so_dest)),
            # O fingerprint REPORTADO e' o da fotografia tipada — o unico que
            # staging e destino podem igualar. O bruto fica ao lado apenas para
            # rastreabilidade: se os dois diferirem, houve arredondamento
            # declarado pelo schema, e isso e' esperado, nao defeito.
            "fingerprint": fp,
            "fingerprint_raw": ml_fingerprint(rows),
            "target_scales": dict(escalas),
        }
        dc.close()
        dst.commit()
        return resultado
    except Exception:
        dst.rollback()
        raise


def _ml_read_rows(cur, relacao: str) -> list:
    cur.execute(f"SELECT {', '.join(ML_BUSINESS_COLUMNS)} FROM {relacao}")
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# ML: RDS gold.ml_produto_ranking -> Neon
# Estrategia: SNAPSHOT TRANSACIONAL (Gate S3). A fonte nao tem dimensao
#             temporal, entao a tabela e' substituida por INTEIRO dentro de
#             uma transacao: staging pg_temp -> DELETE integral -> INSERT ->
#             reconciliacao contra a fotografia capturada -> commit.
#             Antes do Gate S3 o docstring dizia "full refresh" mas a escrita
#             era apenas ON CONFLICT DO UPDATE, que nunca remove chave que
#             desapareceu da fonte — o refresh era declarado, nao real.
#             Deduplicar por (brand, item_id) mantendo maior gross_revenue.
# ---------------------------------------------------------------------------
def sync_ml(brands: set = None) -> dict:
    brands = brands or BRANDS_IN_SCOPE
    audit_conn = _neon()
    run_id = _audit_start(audit_conn, "ml_produto_ranking", marketplace_id=2)

    try:
        cur = audit_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM marts.fact_ml_produto_ranking")
        prev_count = cur.fetchone()[0]
        cur.close()

        print("[ml] lendo fonte (RDS gold.ml_produto_ranking)...")

        def _read_from_rds():
            src = _rds()
            try:
                sc = src.cursor(cursor_factory=RealDictCursor)
                sc.execute(f"""
                    SELECT DISTINCT ON (brand, item_id)
                           brand, item_id, seller_sku, title,
                           gross_revenue, units_sold, unique_buyers, units_per_buyer,
                           cancel_rate_pct, ad_spend, ad_roas, ad_acos_pct, days_advertised,
                           revenue_share_pct, cumulative_revenue_pct, estimated_margin,
                           price_spread_pct, pareto_bucket, revenue_velocity,
                           ad_efficiency, action_signal, product_status,
                           first_sale, last_sale
                    FROM gold.ml_produto_ranking
                    WHERE brand IN {_brands_sql(brands)}
                    ORDER BY brand, item_id, gross_revenue DESC NULLS LAST
                """)
                fetched = sc.fetchall()
                sc.close()
                return fetched
            finally:
                src.close()

        rows = _read_rds_with_recovery_retry(_read_from_rds)
        print(f"[ml] fonte (pos-dedup): {len(rows)} linhas")

        if prev_count > 0 and len(rows) < prev_count * MIN_ROWS_RATIO:
            raise RuntimeError(
                f"[ml] queda suspeita de linhas: fonte={len(rows)} vs Neon atual={prev_count} "
                f"(limite={MIN_ROWS_RATIO:.0%}). Carga abortada sem commit — investigar RDS antes de repetir."
            )

        problemas = ml_validate_snapshot(rows)
        if problemas:
            raise RuntimeError(
                "[ml] fotografia reprovada, nada foi escrito: " + "; ".join(problemas))

        # Fingerprint BRUTO, sem a tipagem do destino. Informativo: nao e' o que
        # a reconciliacao usa, porque a staging herda as escalas do destino e
        # arredonda no INSERT. O valor conferido sai de `ml_publish_snapshot`.
        print(f"[ml] fingerprint da fonte (bruto): {ml_fingerprint(rows)}")

        dst = _neon()
        # `refreshed_at` UNICO para todas as linhas do mesmo snapshot: antes do
        # Gate S3 o INSERT usava `now` e o DO UPDATE usava `NOW()`, o que fazia
        # linhas do mesmo refresh carregarem instantes diferentes.
        now = datetime.now(timezone.utc)
        try:
            res = ml_publish_snapshot(dst, rows, now)
        finally:
            dst.close()

        batch = rows  # contrato de retorno preservado: len(batch) == linhas publicadas
        print(f"[ml] apagadas: {res['deleted']}   publicadas: {res['published']}")
        print(f"[ml] EXCEPT bidirecional: {res['checks']['except_both_ways']}")
        print(f"[ml] escalas do destino: {res['checks']['target_scales']}")
        print("[ml] fingerprint conferido (fotografia tipada = staging = destino): "
              f"{res['checks']['fingerprint']}")
        print(f"[ml] Neon: {len(batch)} linha(s) no snapshot publicado")
        sales_dates = [r["last_sale"] for r in rows if r.get("last_sale")]
        _audit_finish(
            audit_conn, run_id, "success", len(rows), len(batch),
            min(sales_dates) if sales_dates else None,
            max(sales_dates) if sales_dates else None,
        )
        audit_conn.close()
        return {"source": len(rows), "upserted": len(batch)}

    except Exception as exc:
        _audit_finish(audit_conn, run_id, "failed", 0, 0, error=str(exc)[:500])
        audit_conn.close()
        raise


# ---------------------------------------------------------------------------
# TikTok: RDS gold.tiktok_product_daily -> Neon
# Estrategia: incremental por date. Sincroniza ultimos `days` dias + hoje.
#             Re-sincronizar semana garante idempotencia se fonte for corrigida.
# ---------------------------------------------------------------------------
def sync_tiktok(days: int = DEFAULT_TIKTOK_DAYS, full: bool = False, brands: set = None) -> dict:
    brands = brands or BRANDS_IN_SCOPE
    audit_conn = _neon()
    run_id = _audit_start(audit_conn, "tiktok_product_daily", marketplace_id=1)

    try:
        ac = audit_conn.cursor()
        if full:
            start_date = date(2025, 10, 1)
            print(f"[tiktok] full backfill desde {start_date}...")
        else:
            # Determinar data inicial: max(date) no Neon - days (ou 2025-10-01 se vazio)
            ac.execute("SELECT MAX(date) AS max_d FROM marts.fact_tiktok_product_daily")
            r = ac.fetchone()
            max_neon = r[0] if r and r[0] else date(2025, 9, 30)
            start_date = max_neon - timedelta(days=days)
            print(f"[tiktok] incremental desde {start_date} (Neon max={max_neon}, lookback={days}d)...")
        ac.close()

        src = _rds()
        sc = src.cursor(cursor_factory=RealDictCursor)

        sc.execute(f"""
            SELECT date, brand, product_id, product_name,
                   gmv, orders, items_sold,
                   gmv_video, gmv_live, gmv_product_card,
                   items_sold_video, items_sold_live, items_sold_product_card,
                   pct_gmv_video, pct_gmv_live, pct_gmv_card,
                   canceled, refunded, returned, problem_rate,
                   rating_avg, total_ratings,
                   active_videos, video_views
            FROM gold.tiktok_product_daily
            WHERE brand IN {_brands_sql(brands)}
              AND date >= %s
            ORDER BY date, product_id
        """, (start_date,))
        rows = sc.fetchall()
        sc.close(); src.close()
        print(f"[tiktok] fonte: {len(rows)} linhas")

        if not rows:
            print("[tiktok] nada a sincronizar")
            _audit_finish(audit_conn, run_id, "success", 0, 0, start_date, start_date)
            audit_conn.close()
            return {"source": 0, "upserted": 0}

        if full and len(rows) < 1000:
            raise RuntimeError(
                f"[tiktok] full backfill retornou apenas {len(rows)} linhas desde {start_date} "
                "— abaixo do esperado para um historico completo. Carga abortada sem commit."
            )

        dst = _neon()
        UPSERT = """
            INSERT INTO marts.fact_tiktok_product_daily
                (date, brand, product_id, product_name,
                 gmv, orders, items_sold,
                 gmv_video, gmv_live, gmv_product_card,
                 items_sold_video, items_sold_live, items_sold_product_card,
                 pct_gmv_video, pct_gmv_live, pct_gmv_card,
                 canceled, refunded, returned, problem_rate,
                 rating_avg, total_ratings,
                 active_videos, video_views)
            VALUES %s
            ON CONFLICT (date, product_id)
            DO UPDATE SET
                brand                   = EXCLUDED.brand,
                product_name            = EXCLUDED.product_name,
                gmv                     = EXCLUDED.gmv,
                orders                  = EXCLUDED.orders,
                items_sold              = EXCLUDED.items_sold,
                gmv_video               = EXCLUDED.gmv_video,
                gmv_live                = EXCLUDED.gmv_live,
                gmv_product_card        = EXCLUDED.gmv_product_card,
                items_sold_video        = EXCLUDED.items_sold_video,
                items_sold_live         = EXCLUDED.items_sold_live,
                items_sold_product_card = EXCLUDED.items_sold_product_card,
                pct_gmv_video           = EXCLUDED.pct_gmv_video,
                pct_gmv_live            = EXCLUDED.pct_gmv_live,
                pct_gmv_card            = EXCLUDED.pct_gmv_card,
                canceled                = EXCLUDED.canceled,
                refunded                = EXCLUDED.refunded,
                returned                = EXCLUDED.returned,
                problem_rate            = EXCLUDED.problem_rate,
                rating_avg              = EXCLUDED.rating_avg,
                total_ratings           = EXCLUDED.total_ratings,
                active_videos           = EXCLUDED.active_videos,
                video_views             = EXCLUDED.video_views,
                ingested_at             = NOW()
        """

        BATCH_SIZE = 1000
        inserted = 0
        try:
            dc = dst.cursor()
            for i in range(0, len(rows), BATCH_SIZE):
                chunk = [
                    (
                        r["date"], r["brand"], r["product_id"], r["product_name"],
                        r["gmv"], r["orders"], r["items_sold"],
                        r["gmv_video"], r["gmv_live"], r["gmv_product_card"],
                        r["items_sold_video"], r["items_sold_live"], r["items_sold_product_card"],
                        r["pct_gmv_video"], r["pct_gmv_live"], r["pct_gmv_card"],
                        r["canceled"], r["refunded"], r["returned"], r["problem_rate"],
                        r["rating_avg"], r["total_ratings"],
                        r["active_videos"], r["video_views"],
                    )
                    for r in rows[i : i + BATCH_SIZE]
                ]
                execute_values(dc, UPSERT, chunk, page_size=500)
                inserted += len(chunk)
            dst.commit()
            dc.close()
        except Exception:
            dst.rollback()
            raise
        finally:
            dst.close()

        print(f"[tiktok] Neon: {inserted} linhas upserted")
        dates = [r["date"] for r in rows]
        _audit_finish(audit_conn, run_id, "success", len(rows), inserted, min(dates), max(dates))
        audit_conn.close()
        return {"source": len(rows), "upserted": inserted}

    except Exception as exc:
        _audit_finish(audit_conn, run_id, "failed", 0, 0, error=str(exc)[:500])
        audit_conn.close()
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Sync produtos para Neon")
    parser.add_argument("--source", choices=["shopee", "ml", "tiktok", "all"], default="all")
    parser.add_argument("--days",   type=int, default=DEFAULT_TIKTOK_DAYS,
                        help="Lookback em dias para TikTok incremental (default: %(default)s)")
    parser.add_argument("--full",   action="store_true",
                        help="Forcear full backfill (Shopee e TikTok)")
    args = parser.parse_args()

    _assert_distinct_targets()
    brands = _active_brands()
    print(f"[main] brands ativas (marts.dim_loja): {sorted(brands)}")

    t0 = time.time()
    results = {}
    failures = {}

    sources = []
    if args.source in ("shopee", "all"):
        sources.append(("shopee", lambda: sync_shopee(full=args.full, brands=brands)))
    if args.source in ("ml", "all"):
        sources.append(("ml", lambda: sync_ml(brands=brands)))
    if args.source in ("tiktok", "all"):
        sources.append(("tiktok", lambda: sync_tiktok(days=args.days, full=args.full, brands=brands)))

    for name, fn in sources:
        try:
            results[name] = fn()
        except Exception as exc:
            print(f"[ERRO] [{name}] sync falhou: {exc}")
            failures[name] = str(exc)

    elapsed = time.time() - t0
    print(f"\n[DONE] {elapsed:.1f}s | sucesso={results} | falhas={failures}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
