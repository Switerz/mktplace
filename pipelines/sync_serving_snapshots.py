"""
Gate S3 — sync de SNAPSHOT das duas fontes de serving que nao tem janela.

Por que este modulo existe, e por que nao e' o `serving_refresh.py`
------------------------------------------------------------------
`pipelines/ops/serving_refresh.py` resolve `min(D-1, source_max)` e exige, no seu
dataclass `Target`, `date_column` e `source_min_date` — os dois obrigatorios, sem
default — alem de sempre emitir `--date-from/--date-to` ao CLI interno. As duas
fontes deste modulo nao cabem nesse contrato:

- `gold.ml_cross_company_summary` e' snapshot **sem dimensao temporal**: quatro
  linhas, uma por marca ML, recalculadas na origem. Nao existe `date_column` para
  declarar, e fabricar uma mentiria sobre o grao;
- `gold.v_channel_efficiency` TEM data, mas `/brand-detail` aceita **qualquer
  mes** desde outubro/2025, e a medicao de 17-18/08/2026 mostrou **71,4% das
  linhas fora da janela de 90 dias** (3.378 de 4.728). Uma janela movel
  congelaria esse historico no que o primeiro backfill capturou e nunca
  absorveria reafirmacao antiga. A leitura integral custa 1,25 s a 2,45 s, com
  fingerprint identico em duas medicoes — substituir tudo e' mais simples E mais
  correto.

Generalizar `serving_refresh.py` para cobrir os dois regimes foi descartado de
proposito: seria um wrapper tentando servir contratos incompativeis. Este modulo
e' estreito por decisao — **allowlist literal de exatamente dois targets**, sem
registro dinamico e sem framework.

Contrato de execucao
--------------------
1. a fonte e' lida com sessao READ-ONLY e capturada **uma unica vez**;
2. sem `--apply` nenhuma conexao de escrita e' aberta;
3. a fotografia capturada e' validada (chaves, tipos, duplicidade, volume);
4. o fingerprint canonico e' calculado sobre essa fotografia, em Python;
5. uma transacao no Neon: advisory lock proprio -> staging `pg_temp` com
   `ON COMMIT DROP` -> `DELETE` integral do alvo -> `INSERT` de colunas
   explicitas -> reconciliacao contra a **fotografia**, nunca contra uma
   releitura da fonte;
6. commit somente se contagem, chaves, agregados, `EXCEPT` bidirecional e
   fingerprint coincidirem; qualquer falha faz rollback integral.

A fonte pode mudar depois da captura — as duas sao views, e uma delas e'
recalculada a cada leitura. Isso **nao** invalida a execucao: o contrato e'
"destino igual a fotografia daquela execucao", o mesmo criterio ratificado no
Gate S2.

Fingerprint em Python, nao em SQL
---------------------------------
`MD5(STRING_AGG(... ORDER BY texto))` depende de colacao, e as duas pontas deste
projeto usam locales diferentes (`en_US.UTF-8` no RDS, `C.UTF-8` no Neon) — o
mesmo dado produziria hashes distintos. Ordenar e serializar em Python elimina a
classe inteira do problema, e a fotografia ja esta em memoria.

Uso:
    python -m pipelines.sync_serving_snapshots --target ml_cross_company
    python -m pipelines.sync_serving_snapshots --target tiktok_channel_efficiency --apply
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

REPO_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from pipelines import sync_tiktok_serving as tk_sync  # noqa: E402

#: Reuso dos helpers JA validados, nunca uma segunda implementacao: um
#: sanitizador divergente e' pior que nenhum. Testes travam a identidade por `is`.
sanitize_error_message = tk_sync.sanitize_error_message
sanitize_run_id = tk_sync.sanitize_run_id
validate_identifier = tk_sync.validate_identifier
validate_qualified = tk_sync.validate_qualified

CONNECT_TIMEOUT_SECONDS = 20
#: Leitura integral medida em 1,25 s a 2,45 s; 120 s cobre uma replica lenta sem
#: deixar a transacao aberta indefinidamente.
STATEMENT_TIMEOUT_MS = 120_000

EXIT_FALHA = 1

#: Limite do `error_message` gravado no audit log. Mesmo teto usado pelos syncs
#: existentes; evita que uma mensagem longa vire payload no banco de auditoria.
MAX_AUDIT_ERROR_CHARS = 500


@dataclass(frozen=True)
class SnapshotSpec:
    """Um dos DOIS targets. Nao ha' registro dinamico: a allowlist e' literal."""

    name: str
    source_relation: str
    target_table: str
    staging_name: str
    key_columns: tuple[str, ...]
    #: Colunas de negocio, na ordem em que entram no INSERT.
    value_columns: tuple[str, ...]
    #: Somaveis; entram na reconciliacao por agregado.
    additive_columns: tuple[str, ...]
    advisory_lock_key: int
    #: Piso absoluto de linhas. Fonte abaixo disso aborta antes de qualquer escrita.
    min_rows: int
    #: Fracao minima do que o destino ja tem. Protege contra fonte truncada.
    min_ratio_vs_target: float
    #: Allowlist de marca, ou None quando a fonte nao filtra marca.
    brand_allowlist: tuple[str, ...] | None
    preflight_source: str
    step_name: str
    step_timeout_seconds: int
    #: Usado em `audit.source_sync_run.marketplace_id`: 1=tiktok, 2=ml, 3=shopee,
    #: a mesma convencao de sync_produtos.py e daily_performance.py.
    marketplace_id: int

    @property
    def audit_source_name(self) -> str:
        """Nome em `audit.source_sync_run.source_name`.

        E' o MESMO `name` do target de proposito: o CLI, o audit log, o
        `EXPECTED_SOURCES` do health_check e as mensagens usam UMA string, para
        que nao exista um quarto vocabulario para a mesma coisa. Um teste do
        health check trava essa igualdade.
        """
        return self.name

    @property
    def all_columns(self) -> tuple[str, ...]:
        return self.key_columns + self.value_columns


ML_CROSS_COMPANY = SnapshotSpec(
    name="ml_cross_company",
    source_relation="gold.ml_cross_company_summary",
    target_table="marts.fact_ml_cross_company_summary",
    staging_name="sync_ml_cross_company_staging",
    key_columns=("brand",),
    value_columns=(
        "total_buyers", "repeat_buyers", "repeat_rate_pct", "avg_customer_ltv",
        "vip_buyers", "one_and_done_buyers", "at_risk_or_churned", "overall_roas",
    ),
    additive_columns=(
        "total_buyers", "repeat_buyers", "vip_buyers",
        "one_and_done_buyers", "at_risk_or_churned",
    ),
    advisory_lock_key=909_120_009,
    #: Quatro marcas ML. Uma fonte com menos de 4 esta incompleta.
    min_rows=4,
    min_ratio_vs_target=1.0,
    brand_allowlist=None,
    preflight_source="serving_ml_cross_company",
    step_name="serving_ml_cross_company",
    step_timeout_seconds=300,
    marketplace_id=2,   # ml
)

TIKTOK_CHANNEL_EFFICIENCY = SnapshotSpec(
    name="tiktok_channel_efficiency",
    source_relation="gold.v_channel_efficiency",
    target_table="marts.fact_tiktok_channel_efficiency_daily",
    staging_name="sync_tiktok_channel_efficiency_staging",
    key_columns=("date", "brand", "channel"),
    value_columns=("impressions", "page_views", "items_sold", "gmv"),
    additive_columns=("impressions", "page_views", "items_sold", "gmv"),
    advisory_lock_key=910_120_010,
    #: 4.728 linhas medidas nas cinco marcas. O piso protege contra a view
    #: devolver um recorte pequeno por erro de filtro na origem.
    min_rows=3_000,
    min_ratio_vs_target=0.90,
    brand_allowlist=tk_sync.ALLOWED_BRANDS,
    preflight_source="serving_tiktok_channel_efficiency",
    step_name="serving_tiktok_channel_efficiency",
    step_timeout_seconds=600,
    marketplace_id=1,   # tiktok
)

#: A allowlist. Literal, com exatamente dois targets.
SPECS: dict[str, SnapshotSpec] = {
    ML_CROSS_COMPANY.name: ML_CROSS_COMPANY,
    TIKTOK_CHANNEL_EFFICIENCY.name: TIKTOK_CHANNEL_EFFICIENCY,
}

TARGET_ORDER: tuple[str, ...] = (ML_CROSS_COMPANY.name, TIKTOK_CHANNEL_EFFICIENCY.name)


def resolve_spec(name: str) -> SnapshotSpec:
    """Allowlist fechada: um target desconhecido falha aqui, antes de qualquer
    conexao, SQL ou escrita."""
    spec = SPECS.get(name)
    if spec is None:
        raise ValueError(f"target desconhecido: {name!r}. Opcoes: {sorted(SPECS)}")
    return spec


# ---------------------------------------------------------------------------
# Leitura da fonte — colunas explicitas, allowlist parametrizada
# ---------------------------------------------------------------------------

def build_source_query(spec: SnapshotSpec) -> str:
    """Colunas explicitas, zero `SELECT *`.

    A allowlist de marca vai como PARAMETRO (`brand = ANY(%(brands)s)`), nunca
    interpolada: interpolar `IN ('a','b')` funcionaria hoje e viraria injecao no
    dia em que a lista vier de fora do codigo.
    """
    cols = ", ".join(validate_identifier(c) for c in spec.all_columns)
    relacao = validate_qualified(spec.source_relation)
    sql = f"SELECT {cols} FROM {relacao}"
    if spec.brand_allowlist is not None:
        sql += " WHERE brand = ANY(%(brands)s)"
    ordem = ", ".join(validate_identifier(c) for c in spec.key_columns)
    return sql + f" ORDER BY {ordem}"


def source_params(spec: SnapshotSpec) -> dict:
    if spec.brand_allowlist is None:
        return {}
    return {"brands": list(spec.brand_allowlist)}


def capture_source(conn, spec: SnapshotSpec) -> list[dict]:
    """UMA leitura. A fotografia devolvida aqui e' a unica verdade da execucao:
    nenhuma releitura posterior da fonte entra na reconciliacao."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
        cur.execute(build_source_query(spec), source_params(spec))
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Validacao e fingerprint — funcoes puras
# ---------------------------------------------------------------------------

def key_of(spec: SnapshotSpec, row: dict) -> tuple:
    return tuple(str(row[c]) for c in spec.key_columns)


def duplicates_in_rows(spec: SnapshotSpec, rows: list[dict]) -> int:
    vistas, dup = set(), 0
    for r in rows:
        k = key_of(spec, r)
        if k in vistas:
            dup += 1
        vistas.add(k)
    return dup


def validate_source_rows(spec: SnapshotSpec, rows: list[dict],
                         target_count: int | None = None) -> list[str]:
    """Todas as reprovacoes possiveis ANTES de qualquer escrita.

    `target_count` e' a contagem atual do destino, quando conhecida: e' o que
    permite a guarda de proporcao detectar fonte truncada.
    """
    problemas: list[str] = []

    if len(rows) < spec.min_rows:
        problemas.append(
            f"volume abaixo do piso: {len(rows)} linha(s), minimo {spec.min_rows}")

    if target_count and target_count > 0:
        minimo = int(target_count * spec.min_ratio_vs_target)
        if len(rows) < minimo:
            problemas.append(
                f"fonte com {len(rows)} linha(s) contra {target_count} no destino: "
                f"abaixo do minimo de {minimo} ({spec.min_ratio_vs_target:.0%})")

    dup = duplicates_in_rows(spec, rows)
    if dup:
        problemas.append(f"{dup} chave(s) {spec.key_columns} duplicada(s) na fonte")

    faltando = [c for c in spec.all_columns if rows and c not in rows[0]]
    if faltando:
        problemas.append(f"colunas ausentes na fonte: {faltando}")

    nulos_chave = sum(1 for r in rows if any(r.get(c) is None for c in spec.key_columns))
    if nulos_chave:
        problemas.append(f"{nulos_chave} linha(s) com chave nula")

    negativos = 0
    nan = 0
    for r in rows:
        for c in spec.additive_columns:
            v = r.get(c)
            if v is None:
                continue
            if isinstance(v, Decimal) and v.is_nan():
                nan += 1
                continue
            try:
                if float(v) < 0:
                    negativos += 1
            except (TypeError, ValueError):
                problemas.append(f"valor nao numerico em {c}: {type(v).__name__}")
    if negativos:
        problemas.append(f"{negativos} valor(es) negativo(s) em coluna somavel")
    if nan:
        problemas.append(f"{nan} valor(es) NaN em coluna somavel")

    if spec.brand_allowlist is not None:
        fora = {r.get("brand") for r in rows if r.get("brand") not in spec.brand_allowlist}
        if fora:
            problemas.append(f"{len(fora)} marca(s) fora da allowlist na fonte")

    return problemas


def _canonico(valor) -> str:
    """Serializacao canonica de um valor para o fingerprint.

    `Decimal` e' normalizado para evitar que `1.10` e `1.1` produzam hashes
    diferentes; `None` recebe um marcador que nenhum valor real pode gerar.
    """
    if valor is None:
        return "\x00"
    if isinstance(valor, Decimal):
        return format(valor.normalize(), "f")
    return str(valor)


def fingerprint(spec: SnapshotSpec, rows: list[dict]) -> str:
    """Hash determinístico da fotografia, calculado em PYTHON.

    Deliberadamente nao em SQL: `MD5(STRING_AGG(... ORDER BY texto))` depende de
    colacao, e as duas pontas usam locales diferentes — o mesmo dado geraria
    hashes distintos. Ordenar aqui elimina a classe do problema.
    """
    h = hashlib.md5()
    for r in sorted(rows, key=lambda x: key_of(spec, x)):
        h.update("|".join(_canonico(r.get(c)) for c in spec.all_columns).encode("utf-8"))
        h.update(b";")
    return h.hexdigest()


def aggregates(spec: SnapshotSpec, rows: list[dict]) -> dict:
    """Agregados em `Decimal`, nunca `float`: soma de milhares de valores
    monetarios em ponto flutuante divergiu em 0,00278 numa medicao anterior deste
    projeto."""
    out: dict[str, object] = {
        "count": len(rows),
        "keys": len({key_of(spec, r) for r in rows}),
    }
    for c in spec.additive_columns:
        total = Decimal(0)
        for r in rows:
            v = r.get(c)
            if v is not None:
                total += Decimal(str(v))
        out[f"sum_{c}"] = total
    return out


def compare_aggregates(esperado: dict, obtido: dict) -> list[str]:
    problemas = []
    for chave, valor in esperado.items():
        if obtido.get(chave) != valor:
            problemas.append(f"{chave}: fotografia={valor} destino={obtido.get(chave)}")
    return problemas


# ---------------------------------------------------------------------------
# Auditoria — mesmo padrao de sync_produtos.py / daily_performance.py
# ---------------------------------------------------------------------------
# Registrada numa conexao SEPARADA da transacao de dados, de proposito: se a
# publicacao falhar e sofrer rollback, o registro `failed` tem de sobreviver.
# Fosse na mesma transacao, a falha apagaria a propria evidencia da falha.
#
# So' o caminho `--apply` registra. Diagnostico nao publica nada e nao pode
# aparecer no audit log como se tivesse publicado — o health check leria isso
# como fonte saudavel.

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


def _neon_audit(url: str):
    """Conexao de auditoria. Separada da de dados e com autocommit por chamada
    explicita de `commit()` nos dois helpers acima."""
    return psycopg2.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS)


def source_date_bounds(spec: SnapshotSpec, rows: list[dict]) -> tuple:
    """MIN/MAX da coluna de data da fotografia, quando o target tem data.

    `ml_cross_company` e' snapshot sem dimensao temporal: devolve `(None, None)`
    em vez de fabricar uma data para preencher a coluna do audit.
    """
    if "date" not in spec.key_columns:
        return (None, None)
    datas = [r["date"] for r in rows if r.get("date") is not None]
    if not datas:
        return (None, None)
    return (min(datas), max(datas))


# ---------------------------------------------------------------------------
# Publicacao — uma transacao, rollback integral
# ---------------------------------------------------------------------------

def _neon_writable(url: str):
    conn = psycopg2.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.autocommit = False
    return conn


def _datamart_readonly(url: str):
    conn = psycopg2.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _get_url(env: str) -> str:
    url = os.environ.get(env, "")
    if not url:
        raise RuntimeError(f"{env} nao configurado.")
    return url


def target_count(conn, spec: SnapshotSpec) -> int:
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {validate_qualified(spec.target_table)}")
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def read_target_rows(conn, spec: SnapshotSpec) -> list[dict]:
    cols = ", ".join(validate_identifier(c) for c in spec.all_columns)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(f"SELECT {cols} FROM {validate_qualified(spec.target_table)}")
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()


def publish_snapshot(neon_conn, spec: SnapshotSpec, rows: list[dict], run_id: str) -> dict:
    """UMA transacao: lock -> staging -> validacao -> DELETE integral -> INSERT ->
    reconciliacao contra a FOTOGRAFIA. Qualquer falha faz `ROLLBACK` integral e o
    destino fica exatamente como estava; `ON COMMIT DROP` limpa a staging tambem
    no rollback.

    O `DELETE` sem `WHERE` e' deliberado e e' o que distingue este sync de um
    upsert: chave que desapareceu da fonte tem de desaparecer do destino.
    """
    resultado: dict = {"deleted": 0, "published": 0, "checks": {}}
    alvo = validate_qualified(spec.target_table)
    staging = f"pg_temp.{validate_identifier(spec.staging_name)}"
    cols = ", ".join(validate_identifier(c) for c in spec.all_columns)

    try:
        cur = neon_conn.cursor(cursor_factory=RealDictCursor)

        # Lock EXCLUSIVO deste target, liberado no fim da transacao. Duas
        # execucoes do mesmo target nunca se sobrepoem; targets diferentes nao
        # se bloqueiam.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (spec.advisory_lock_key,))

        cur.execute(f"""
            CREATE TEMP TABLE {validate_identifier(spec.staging_name)}
                (LIKE {alvo} INCLUDING DEFAULTS)
            ON COMMIT DROP
        """)

        agora = datetime.now(timezone.utc)
        if rows:
            destino_cols = list(spec.all_columns) + ["synced_at", "source_run_id"]
            lote = [
                tuple(r[c] for c in spec.all_columns) + (agora, run_id)
                for r in rows
            ]
            execute_values(
                cur,
                f"INSERT INTO {staging} ({', '.join(destino_cols)}) VALUES %s",
                lote, page_size=500,
            )

        # A staging tem de refletir a fotografia antes de qualquer DELETE.
        staging_rows = _rows_de(cur, staging, spec)
        problemas = compare_aggregates(aggregates(spec, rows), aggregates(spec, staging_rows))
        if problemas:
            raise RuntimeError("staging divergiu da fotografia: " + "; ".join(problemas))
        if fingerprint(spec, staging_rows) != fingerprint(spec, rows):
            raise RuntimeError("staging divergiu da fotografia no fingerprint")

        cur.execute(f"DELETE FROM {alvo}")
        resultado["deleted"] = cur.rowcount

        cur.execute(f"""
            INSERT INTO {alvo} ({cols}, synced_at, source_run_id)
            SELECT {cols}, synced_at, source_run_id FROM {staging}
        """)
        resultado["published"] = cur.rowcount

        # Reconciliacao contra a FOTOGRAFIA, nunca contra uma releitura da fonte.
        destino_rows = _rows_de(cur, alvo, spec)
        esperado = aggregates(spec, rows)
        obtido = aggregates(spec, destino_rows)
        problemas = compare_aggregates(esperado, obtido)
        if problemas:
            raise RuntimeError("destino divergiu da fotografia: " + "; ".join(problemas))

        so_foto = {key_of(spec, r) for r in rows} - {key_of(spec, r) for r in destino_rows}
        so_destino = {key_of(spec, r) for r in destino_rows} - {key_of(spec, r) for r in rows}
        if so_foto or so_destino:
            raise RuntimeError(
                f"EXCEPT bidirecional divergiu: fotografia-destino={len(so_foto)} "
                f"destino-fotografia={len(so_destino)}")

        fp_foto = fingerprint(spec, rows)
        fp_destino = fingerprint(spec, destino_rows)
        if fp_foto != fp_destino:
            raise RuntimeError("fingerprint do destino difere do da fotografia")

        resultado["checks"] = {
            "aggregates": {k: str(v) for k, v in esperado.items()},
            "except_both_ways": (len(so_foto), len(so_destino)),
            "fingerprint": fp_foto,
            "synced_at": agora.isoformat(),
        }
        cur.close()
        neon_conn.commit()
        return resultado
    except Exception:
        neon_conn.rollback()
        raise


def _rows_de(cur, relacao: str, spec: SnapshotSpec) -> list[dict]:
    cols = ", ".join(validate_identifier(c) for c in spec.all_columns)
    cur.execute(f"SELECT {cols} FROM {relacao}")
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Orquestracao de um target
# ---------------------------------------------------------------------------

def default_run_id(spec: SnapshotSpec, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return sanitize_run_id(f"snapshot_{spec.name}_{stamp}")


def run_target(target_name: str, apply: bool = False, run_id: str | None = None,
               now: datetime | None = None,
               source_factory=None, neon_read_factory=None, neon_write_factory=None,
               audit_factory=None) -> int:
    """Diagnostico por padrao. Sem `apply`, NENHUMA conexao de escrita e' aberta e
    NADA e' registrado em `audit.source_sync_run`.

    As quatro fabricas de conexao sao injetaveis para teste: nos testes nenhum
    banco e' tocado.
    """
    spec = resolve_spec(target_name)
    modo = "APPLY" if apply else "DIAGNOSTICO"

    source_factory = source_factory or (lambda: _datamart_readonly(_get_url("DATAMART_DATABASE_URL")))
    neon_read_factory = neon_read_factory or (
        lambda: _datamart_readonly(_get_url("DATABASE_URL")))
    neon_write_factory = neon_write_factory or (lambda: _neon_writable(_get_url("DATABASE_URL")))
    audit_factory = audit_factory or (lambda: _neon_audit(_get_url("DATABASE_URL")))

    # ------------------------------------------------------------------
    # DIAGNOSTICO: nenhuma conexao de auditoria, nenhum registro. Identico ao
    # comportamento anterior — um dry-run que aparecesse no audit log seria lido
    # pelo health check como fonte saudavel sem que uma linha fosse escrita.
    # ------------------------------------------------------------------
    if not apply:
        rows, atual, problemas = _ler_e_validar(spec, source_factory, neon_read_factory)
        _imprimir_diagnostico(spec, modo, rows, atual, problemas)
        if problemas:
            return EXIT_FALHA
        print("  MODO DIAGNOSTICO: nenhuma escrita foi feita.")
        return 0

    # ------------------------------------------------------------------
    # APPLY: a auditoria abre ANTES da primeira leitura operacional.
    # ------------------------------------------------------------------
    # Uma chamada `--apply` bloqueada pelo guardrail continua sendo tentativa
    # operacional e precisa ficar auditavel: sem isso, uma falha de VPN, de leitura
    # do destino ou de volume nao deixaria rastro, e o health check seguiria vendo
    # o sucesso do dia anterior como recente.
    rid = sanitize_run_id(run_id) if run_id else default_run_id(spec, now)
    print(f"[snapshot:{spec.name}] modo={modo}")
    print(f"  run_id             : {rid}")

    # Se a PROPRIA abertura/inicializacao da auditoria falhar, a execucao falha
    # aqui, antes de qualquer leitura. E' o unico caso em que nao havera registro —
    # nao ha como registrar a falha do proprio mecanismo de registro. Documentado
    # de proposito, e coberto por teste.
    audit = audit_factory()
    try:
        audit_run_id = _audit_start(audit, spec.audit_source_name, spec.marketplace_id)
    except Exception:
        audit.close()
        raise

    capturadas = 0
    dmin = dmax = None
    # `finish_attempted`, nao `finalizado`: a marca significa "uma tentativa de
    # finalizacao JA ocorreu", e e' escrita ANTES da chamada.
    #
    # Com a semantica anterior ("finalizado com sucesso", marcada DEPOIS), uma
    # excecao dentro do proprio `_audit_finish` deixava a marca em False, o `except`
    # chamava `_audit_finish` de novo, e a mesma tentativa podia produzir DOIS
    # registros de encerramento — inclusive reclassificando como `failed` um
    # snapshot que ja tinha sido publicado e reconciliado.
    finish_attempted = False
    try:
        rows, atual, problemas = _ler_e_validar(spec, source_factory, neon_read_factory)
        capturadas = len(rows)
        # Datas so' depois de a captura ter dado certo, e so' quando o target tem
        # dimensao temporal. ML cross-company devolve (None, None) sempre.
        dmin, dmax = source_date_bounds(spec, rows)
        _imprimir_diagnostico(spec, modo, rows, atual, problemas)

        if problemas:
            # Reprovacao pelo guardrail: EXIT_FALHA preservado, mas agora auditado.
            # A conexao de ESCRITA nunca e' aberta neste caminho.
            #
            # Se este `_audit_finish` falhar, a excecao sobe: o `EXIT_FALHA` NAO e'
            # devolvido como se a auditoria tivesse fechado normalmente. Quem chama
            # precisa saber que a tentativa ficou sem encerramento registrado.
            finish_attempted = True
            _audit_finish(audit, audit_run_id, "failed", capturadas, 0, dmin, dmax,
                          error=_descricao_reprovacao(problemas))
            return EXIT_FALHA

        neon = neon_write_factory()
        try:
            res = publish_snapshot(neon, spec, rows, rid)
        finally:
            neon.close()

        # Publicacao e reconciliacao concluidas. Se o registro de `success` falhar,
        # a falha de auditoria propaga e NENHUMA segunda tentativa e' feita — em
        # particular, o snapshot ja publicado nunca e' reclassificado como `failed`
        # nem republicado.
        finish_attempted = True
        _audit_finish(audit, audit_run_id, "success", capturadas, res["published"],
                      dmin, dmax)
    except Exception as exc:
        if not finish_attempted:
            finish_attempted = True
            try:
                # Mensagem SANITIZADA: nunca DSN, senha, host, IP ou caminho.
                _audit_finish(audit, audit_run_id, "failed", capturadas, 0, dmin, dmax,
                              error=sanitize_error_message(exc)[:MAX_AUDIT_ERROR_CHARS])
            except Exception as audit_exc:
                # Falha de dados E falha ao registra-la: nao existe terceira
                # operacao. O erro PRINCIPAL continua sendo o da carga — e' ele que
                # descreve o que aconteceu com o dado; a falha da auditoria entra
                # como causa, tambem sanitizada.
                raise exc from RuntimeError(
                    "falha ao registrar o encerramento da auditoria: "
                    + sanitize_error_message(audit_exc)[:MAX_AUDIT_ERROR_CHARS]
                )
        # A excecao original nunca e' engolida.
        raise
    finally:
        # Fechada em TODOS os caminhos: sucesso, falha, retorno por validacao e
        # falha do proprio encerramento da auditoria.
        audit.close()

    print(f"  apagadas           : {res['deleted']}")
    print(f"  publicadas         : {res['published']}")
    print(f"  EXCEPT bidirecional: {res['checks']['except_both_ways']}")
    print(f"  fingerprint destino: {res['checks']['fingerprint']}")
    print("  reconciliado contra a FOTOGRAFIA capturada nesta execucao.")
    return 0


def _ler_e_validar(spec: SnapshotSpec, source_factory, neon_read_factory):
    """Le a fonte, le a contagem do destino e valida. Extraida para que o caminho
    de diagnostico e o de apply compartilhem exatamente a mesma sequencia."""
    src = source_factory()
    try:
        rows = capture_source(src, spec)
    finally:
        src.close()

    leitura = neon_read_factory()
    try:
        atual = target_count(leitura, spec)
    finally:
        leitura.close()

    return rows, atual, validate_source_rows(spec, rows, target_count=atual)


def _imprimir_diagnostico(spec: SnapshotSpec, modo: str, rows: list[dict],
                          atual: int, problemas: list[str]) -> None:
    if modo != "APPLY":
        print(f"[snapshot:{spec.name}] modo={modo}")
    print(f"  fonte              : {spec.source_relation}")
    print(f"  grao               : {spec.key_columns}")
    print(f"  linhas capturadas  : {len(rows)}")
    print(f"  chaves distintas   : {aggregates(spec, rows)['keys']}")
    print(f"  destino atual      : {atual} linha(s) em {spec.target_table}")
    print(f"  piso de volume     : {spec.min_rows} absoluto, "
          f"{spec.min_ratio_vs_target:.0%} do destino")
    print(f"  fingerprint        : {fingerprint(spec, rows)}")
    print(f"  advisory lock      : {spec.advisory_lock_key}")
    if problemas:
        print("  REPROVADO antes de qualquer escrita:")
        for p in problemas:
            print(f"    - {p}")
    else:
        print("  validacoes da fonte: OK")


def _descricao_reprovacao(problemas: list[str]) -> str:
    """Descricao compacta e sanitizada da reprovacao, para o audit log.

    As mensagens de `validate_source_rows` carregam contagens e nomes de coluna,
    nunca valor de linha — mas passam pelo mesmo sanitizador das excecoes como
    defesa em profundidade, e sao truncadas.
    """
    texto = "fonte reprovada pelo guardrail: " + "; ".join(problemas)
    return sanitize_error_message(RuntimeError(texto))[:MAX_AUDIT_ERROR_CHARS]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sync_serving_snapshots",
        description=("Substitui integralmente uma das DUAS fatos de serving sem janela, "
                     "reconciliando o destino contra a fotografia capturada. Sem --apply, "
                     "nada e' escrito."),
    )
    p.add_argument("--target", required=True, choices=TARGET_ORDER,
                   help="target a sincronizar; um por invocacao")
    p.add_argument("--apply", action="store_true",
                   help="executa a substituicao. Sem esta flag, diagnostico.")
    p.add_argument("--run-id", help="identificador da execucao; sanitizado antes de usar")
    return p


def main(argv: list[str] | None = None) -> int:
    # `parse_args` ANTES de `load_dotenv`: `--help` e argumento invalido saem por
    # `SystemExit` aqui mesmo, sem ler `.env` e sem tocar em auditoria, fonte,
    # destino ou escrita. Ler configuracao para depois descobrir que a linha de
    # comando estava errada e' efeito colateral sem proposito.
    args = build_parser().parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    if not args.apply:
        print("MODO DIAGNOSTICO (sem --apply): nenhuma escrita sera feita.")
    try:
        return run_target(args.target, apply=args.apply, run_id=args.run_id)
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI, mensagem sanitizada
        print(f"FALHA ({args.target}): {sanitize_error_message(exc)}", file=sys.stderr)
        return EXIT_FALHA


if __name__ == "__main__":
    raise SystemExit(main())
