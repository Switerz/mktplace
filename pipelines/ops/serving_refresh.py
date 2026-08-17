"""
Ponte TEMPORARIA de atualizacao recorrente do serving (Checkpoint O1, Task 2/2).

Existe para resolver UM problema medido: os tres CLIs de serving
(`pipelines.sync_ml_gestao_diaria`, `pipelines.sync_tiktok_serving`) calculam a
janela incremental com teto FIXO em D-1. Quando a fonte Gold daquela tabela
ainda nao tem D-1 — situacao real e recorrente do `gold.tiktok_creator_daily`,
medido em D-2 no diagnostico de 17/08/2026 — a validacao de cobertura por dia
reprova a janela e o CLI sai com codigo nao-zero SEM escrever nada. O
comportamento e' seguro (nunca fabrica dia ausente como zero), mas inviabiliza
execucao automatica: falharia todo dia e congelaria o serving.

Este wrapper NAO altera os dois CLIs validados. Ele resolve a janela por fora,
com `--date-from/--date-to` explicitos, aplicando o contrato:

    effective_date_to = min(D-1 em America/Sao_Paulo, source_max)
    date_from         = max(source_min, effective_date_to - (lookback_days - 1))

`source_max` e' lido da propria fonte, por target, com a MESMA allowlist de
marca que o CLI usa na leitura real. Sem isso o numero seria inconsistente: se
`gold.tiktok_brand_daily` tiver 16/08 para uma marca FORA da allowlist e apenas
15/08 dentro dela, um `source_max` sem filtro pediria uma janela que o CLI nao
consegue cobrir, e a cobertura reprovaria — exatamente a falha que este modulo
existe para evitar.

Garantias de desenho, cada uma coberta por teste:
  - watermark INDEPENDENTE por tabela: ML e brand nunca sao rebaixados porque
    creator esta em D-2. Nao existe watermark comum;
  - D0 e' impossivel: o teto e' D-1, e o CLI ainda revalida por conta propria
    (`validate_window` rejeita `date_to == today`) — duas barreiras;
  - dia ausente permanece AUSENTE: este modulo nunca escreve, nunca gera linha
    e nunca pede um dia que a fonte nao tem;
  - `--table all` nunca e' usado: uma invocacao por target, sempre com
    `--table` explicito no caso TikTok;
  - EXATAMENTE um subprocesso por invocacao, ZERO retry e ZERO backoff. Uma
    falha e' reportada, nunca reexecutada;
  - `subprocess.run` recebe uma LISTA de argumentos e nunca `shell=True`;
  - o exit code do processo filho e' propagado sem traducao;
  - o default e' DIAGNOSTICO: so' `--apply` encaminha `--apply` ao CLI interno;
  - nenhuma URL, credencial ou host aparece em stdout/stderr — mensagens de erro
    passam pelo sanitizador ja validado do modulo TikTok.

O advisory lock de cada tabela e' registrado apenas para leitura humana do log.
Este modulo NUNCA adquire lock: quem serializa a escrita e' a transacao do CLI,
e duplicar o lock aqui criaria auto-bloqueio.

Airflow permanece o destino arquitetural. Este modulo e' a ponte enquanto ele
nao existe, e nao cria, altera nem consulta agendamento algum.

Uso:
    python -m pipelines.ops.serving_refresh --target creator            # diagnostico
    python -m pipelines.ops.serving_refresh --target creator --apply    # escreve
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from pipelines import sync_ml_gestao_diaria as ml_sync  # noqa: E402
from pipelines import sync_tiktok_serving as tk_sync  # noqa: E402

#: Reuso deliberado dos helpers JA validados, nunca uma segunda implementacao:
#: um sanitizador divergente e' pior que nenhum. `_RUN_ID_RE` e o fuso sao
#: identicos nos dois modulos de sync; um teste trava essa identidade.
TZ_OPERACIONAL = tk_sync.TZ_OPERACIONAL
sanitize_error_message = tk_sync.sanitize_error_message
sanitize_run_id = tk_sync.sanitize_run_id
validate_identifier = tk_sync.validate_identifier
validate_qualified = tk_sync.validate_qualified

DEFAULT_LOOKBACK_DAYS = tk_sync.DEFAULT_LOOKBACK_DAYS
MIN_LOOKBACK_DAYS = tk_sync.MIN_LOOKBACK_DAYS

#: Timeout da leitura de `source_max`. E' um `MAX(coluna_indexada)`, nao um scan
#: de tabela: se nao responder nisto, a VPN/replica esta em estado que nao
#: justifica seguir para uma carga de 90 dias.
CONNECT_TIMEOUT_SECONDS = 15
SOURCE_MAX_STATEMENT_TIMEOUT_MS = 30_000

#: Convencao Unix para timeout, a mesma de scripts/run_with_lock.ps1.
EXIT_TIMEOUT = 124
#: Falha do proprio wrapper (contrato de janela, allowlist, conexao) — nunca se
#: confunde com o exit code do CLI interno, que e' propagado sem traducao.
EXIT_WRAPPER_FAILURE = 1


@dataclass(frozen=True)
class Target:
    """Um destino de serving. Allowlist FECHADA: `resolve_target` so' aceita as
    chaves de `TARGETS`, e nenhum valor vindo de fora chega ao SQL sem passar
    por `validate_identifier`/`validate_qualified`."""

    name: str
    #: Modulo CLI real, invocado como `python -m <module>`.
    module: str
    #: Argumentos FIXOS do CLI para este target (ex.: `--table brand`). Nunca
    #: contem `all`: uma invocacao cobre exatamente uma tabela.
    module_args: tuple[str, ...]
    source_relation: str
    date_column: str
    source_min_date: date
    #: Somente para log. Este modulo nunca adquire lock.
    advisory_lock_key: int
    #: Allowlist de marca da fonte, ou None quando a fonte nao filtra por marca.
    #: Precisa ser a MESMA lista que o CLI usa, nunca uma copia local.
    brand_allowlist: tuple[str, ...] | None
    #: Nome da fonte em pipelines.ops.preflight.SOURCE_CHECKS.
    preflight_source: str
    #: Nome do step em pipelines.ops.orchestrate.
    step_name: str
    #: Orcamento do step no orquestrador e o do processo filho aqui dentro. O
    #: filho termina ANTES para que o wrapper ainda consiga reportar; sem isso o
    #: orquestrador mataria o wrapper e deixaria o CLI orfao escrevendo no Neon.
    step_timeout_seconds: int
    child_timeout_seconds: int


TARGETS: dict[str, Target] = {
    "ml": Target(
        name="ml",
        module="pipelines.sync_ml_gestao_diaria",
        module_args=(),
        source_relation=ml_sync.SOURCE_RELATION,
        date_column="ref_date",
        source_min_date=ml_sync.SOURCE_MIN_DATE,
        advisory_lock_key=ml_sync.ADVISORY_LOCK_KEY,
        # gold.ml_gestao_diaria nao e' filtrada por marca na leitura real
        # (build_source_query do modulo ML nao tem clausula de brand), entao um
        # source_max filtrado aqui divergiria do que o CLI de fato le.
        brand_allowlist=None,
        preflight_source="serving_ml",
        step_name="serving_ml",
        step_timeout_seconds=600,
        child_timeout_seconds=540,
    ),
    "brand": Target(
        name="brand",
        module="pipelines.sync_tiktok_serving",
        module_args=("--table", "brand"),
        source_relation=tk_sync.BRAND_SPEC.source_relation,
        date_column=tk_sync.BRAND_SPEC.date_column,
        source_min_date=tk_sync.BRAND_SPEC.source_min_date,
        advisory_lock_key=tk_sync.BRAND_SPEC.advisory_lock_key,
        brand_allowlist=tk_sync.ALLOWED_BRANDS,
        preflight_source="serving_tiktok_brand",
        step_name="serving_tiktok_brand",
        step_timeout_seconds=600,
        child_timeout_seconds=540,
    ),
    "creator": Target(
        name="creator",
        module="pipelines.sync_tiktok_serving",
        module_args=("--table", "creator"),
        source_relation=tk_sync.CREATOR_SPEC.source_relation,
        date_column=tk_sync.CREATOR_SPEC.date_column,
        source_min_date=tk_sync.CREATOR_SPEC.source_min_date,
        advisory_lock_key=tk_sync.CREATOR_SPEC.advisory_lock_key,
        brand_allowlist=tk_sync.ALLOWED_BRANDS,
        preflight_source="serving_tiktok_creator",
        step_name="serving_tiktok_creator",
        step_timeout_seconds=1800,
        child_timeout_seconds=1740,
    ),
}

#: Ordem canonica: ML primeiro (mais barato, canal independente), creator por
#: ultimo (66.347 linhas numa janela de 90 dias, contra 360 do ML).
TARGET_ORDER: tuple[str, ...] = ("ml", "brand", "creator")


def resolve_target(name: str) -> Target:
    """Allowlist fechada. Um target desconhecido falha aqui, antes de qualquer
    conexao, SQL ou subprocesso."""
    target = TARGETS.get(name)
    if target is None:
        raise ValueError(f"target desconhecido: {name!r}. Opcoes: {sorted(TARGETS)}")
    return target


# ---------------------------------------------------------------------------
# Funcoes puras — dia operacional, contrato de watermark, janela
# ---------------------------------------------------------------------------

def hoje_operacional(agora: datetime | None = None) -> date:
    """Dia no Brasil, nunca o do processo. Delega ao modulo ja validado para
    que exista UMA definicao de dia operacional no repositorio."""
    return tk_sync.hoje_operacional(agora)


def ultimo_dia_fechado(today: date | None = None) -> date:
    """D-1 no fuso do Brasil. O teto absoluto de qualquer janela: D0 nunca e'
    publicavel porque ainda esta em curso."""
    return (today or hoje_operacional()) - timedelta(days=1)


def require_lookback(lookback_days: int) -> int:
    """Rejeita lookback invalido ANTES de abrir conexao ou subprocesso."""
    if not isinstance(lookback_days, int) or isinstance(lookback_days, bool):
        raise ValueError(f"lookback_days precisa ser inteiro: recebido {lookback_days!r}.")
    if lookback_days < MIN_LOOKBACK_DAYS:
        raise ValueError(
            f"lookback_days precisa ser >= {MIN_LOOKBACK_DAYS} (dias fechados): "
            f"recebido {lookback_days}."
        )
    return lookback_days


def resolve_effective_date_to(target: Target, source_max: date | None,
                              today: date | None = None) -> date:
    """`min(D-1, source_max)` para ESTE target, isolado dos outros.

    Uma fonte atrasada encurta a janela apenas da sua propria tabela. Rebaixar
    todas ao menor watermark comum atrasaria ML e brand de graca — e foi
    explicitamente descartado.
    """
    fechado = ultimo_dia_fechado(today)
    if source_max is None:
        raise ValueError(
            f"source_max de {target.source_relation} veio vazio: a fonte nao tem "
            f"nenhuma linha na allowlist deste target. Nada sera escrito."
        )
    if not isinstance(source_max, date) or isinstance(source_max, datetime):
        raise ValueError(
            f"source_max de {target.source_relation} nao e' uma data: "
            f"{type(source_max).__name__}."
        )
    if source_max < target.source_min_date:
        raise ValueError(
            f"source_max ({source_max}) e' anterior ao primeiro dado conhecido de "
            f"{target.source_relation} ({target.source_min_date}) — fonte "
            f"inconsistente, nada sera escrito."
        )
    if fechado < target.source_min_date:
        raise ValueError(
            f"o ultimo dia fechado ({fechado}) e' anterior ao primeiro dado de "
            f"{target.source_relation} ({target.source_min_date})."
        )
    return min(fechado, source_max)


def resolve_window(target: Target, source_max: date | None,
                   lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                   today: date | None = None) -> tuple[date, date]:
    """Janela INCLUSIVA de `lookback_days` datas, terminando em
    `effective_date_to`. 90 datas inclusivas significam `date_to - 89`."""
    require_lookback(lookback_days)
    date_to = resolve_effective_date_to(target, source_max, today)
    date_from = max(target.source_min_date, date_to - timedelta(days=lookback_days - 1))
    return date_from, date_to


def default_run_id(target: Target, now: datetime | None = None) -> str:
    """Distinto por target e por segundo — dois targets da mesma execucao nunca
    colidem, e duas execucoes do mesmo target so' colidiriam no mesmo segundo."""
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return sanitize_run_id(f"serving_{target.name}_{stamp}")


def build_argv(target: Target, date_from: date, date_to: date, run_id: str,
               apply: bool = False) -> list[str]:
    """LISTA de argumentos, nunca string de shell.

    Passa `--date-from/--date-to` explicitos de proposito: e' o unico caminho
    dos CLIs que aceita um teto diferente de D-1 fixo. `--lookback-days` NAO e'
    encaminhado (seria ignorado no ramo de datas explicitas e daria a impressao
    falsa de que o CLI recalcularia a janela); `--backfill` nunca e' usado.
    """
    argv = [
        sys.executable,
        "-m",
        target.module,
        *target.module_args,
        "--date-from",
        date_from.isoformat(),
        "--date-to",
        date_to.isoformat(),
        "--run-id",
        run_id,
    ]
    if apply:
        argv.append("--apply")
    return argv


# ---------------------------------------------------------------------------
# Leitura de source_max — SELECT agregado, allowlisted por target
# ---------------------------------------------------------------------------

def build_source_max_query(target: Target) -> str:
    """`MAX(<coluna de data>)` da fonte deste target.

    Consulta barata de proposito: um agregado sobre a coluna de data, sem
    juncao e sem trazer linha. A allowlist de marca vai como PARAMETRO
    (`brand = ANY(%(brands)s)`), nunca interpolada — interpolar `IN ('a','b')`
    funcionaria hoje e viraria injecao no dia em que a lista vier de fora.
    """
    dcol = validate_identifier(target.date_column)
    relation = validate_qualified(target.source_relation)
    sql = f"SELECT MAX({dcol}) AS source_max FROM {relation}"
    if target.brand_allowlist is not None:
        sql += " WHERE brand = ANY(%(brands)s)"
    return sql


def source_max_params(target: Target) -> dict:
    if target.brand_allowlist is None:
        return {}
    return {"brands": list(target.brand_allowlist)}


def _datamart_readonly():
    """Conexao de LEITURA da fonte. Nunca imprime a URL, nem sanitizada."""
    url = os.environ.get("DATAMART_DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATAMART_DATABASE_URL nao configurado — a fonte Gold nao pode ser lida."
        )
    conn = psycopg2.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def read_source_max(target: Target, conn) -> date | None:
    cur = conn.cursor()
    try:
        # Escopo de SESSAO, nao `SET LOCAL`: a conexao e' autocommit, logo nao
        # existe transacao para um `LOCAL` valer, e ele seria silenciosamente
        # ignorado. A conexao e' descartada em seguida, entao o efeito colateral
        # nao escapa desta leitura.
        cur.execute(f"SET statement_timeout = {SOURCE_MAX_STATEMENT_TIMEOUT_MS}")
        cur.execute(build_source_max_query(target), source_max_params(target))
        row = cur.fetchone()
    finally:
        cur.close()
    if not row:
        return None
    return row[0]


# ---------------------------------------------------------------------------
# Execucao — exatamente um subprocesso, zero retry
# ---------------------------------------------------------------------------

def _default_runner(argv: list[str], timeout_seconds: int) -> int:
    """`shell=False` (default de subprocess.run, nunca sobrescrito aqui) e uma
    UNICA chamada. Nao ha laco, nao ha retry, nao ha backoff."""
    proc = subprocess.run(argv, cwd=str(REPO_ROOT), timeout=timeout_seconds)
    return proc.returncode


def run_target(target_name: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
               apply: bool = False, run_id: str | None = None,
               today: date | None = None, now: datetime | None = None,
               conn_factory=None, runner=None) -> int:
    """Resolve a janela deste target e invoca o CLI UMA vez.

    `conn_factory`/`runner` sao injetaveis para teste — nos testes nenhum banco
    e' tocado e nenhum processo e' criado.
    """
    target = resolve_target(target_name)
    require_lookback(lookback_days)

    conn_factory = conn_factory or _datamart_readonly
    runner = runner or _default_runner

    conn = conn_factory()
    try:
        source_max = read_source_max(target, conn)
    finally:
        conn.close()

    fechado = ultimo_dia_fechado(today)
    date_from, date_to = resolve_window(target, source_max, lookback_days, today)
    resolved_run_id = sanitize_run_id(run_id) if run_id else default_run_id(target, now)
    argv = build_argv(target, date_from, date_to, resolved_run_id, apply=apply)

    modo = "APPLY" if apply else "DIAGNOSTICO"
    print(f"[serving_refresh:{target.name}] modo={modo}")
    print(f"  fonte             : {target.source_relation}")
    print(f"  source_max        : {source_max}")
    print(f"  D-1 (Sao Paulo)   : {fechado}")
    print(f"  effective_date_to : {date_to}"
          + ("  (limitado pela fonte)" if date_to < fechado else "  (igual a D-1)"))
    print(f"  date_from         : {date_from}")
    print(f"  lookback_days     : {lookback_days} datas inclusivas")
    print(f"  run_id            : {resolved_run_id}")
    print(f"  advisory lock      : {target.advisory_lock_key} (informativo; o lock e' da transacao do CLI)")
    print(f"  comando           : -m {target.module} {' '.join(argv[3:])}")

    try:
        exit_code = runner(argv, target.child_timeout_seconds)
    except subprocess.TimeoutExpired:
        print(
            f"  TIMEOUT: {target.module} nao terminou em {target.child_timeout_seconds}s; "
            f"processo encerrado. Nenhuma nova tentativa sera feita.",
            file=sys.stderr,
        )
        print(f"  exit code         : {EXIT_TIMEOUT} (timeout)")
        return EXIT_TIMEOUT

    print(f"  exit code         : {exit_code} (propagado do CLI, sem traducao)")
    return exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="serving_refresh",
        description=(
            "Resolve a janela de serving por target (min(D-1, source_max)) e invoca "
            "o CLI de sync correspondente uma unica vez. Sem --apply, nada e' escrito."
        ),
    )
    p.add_argument("--target", required=True, choices=TARGET_ORDER,
                   help="tabela de serving a atualizar; uma por invocacao (nunca 'all')")
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help=(f"datas inclusivas terminando em effective_date_to. "
                         f"Default {DEFAULT_LOOKBACK_DAYS}, minimo {MIN_LOOKBACK_DAYS}."))
    p.add_argument("--apply", action="store_true",
                   help="encaminha --apply ao CLI interno. Sem esta flag, diagnostico.")
    p.add_argument("--run-id", help="identificador da execucao; sanitizado antes de usar")
    return p


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    args = build_parser().parse_args(argv)
    if not args.apply:
        print("MODO DIAGNOSTICO (sem --apply): nenhuma escrita sera feita.")
    try:
        return run_target(
            args.target,
            lookback_days=args.lookback_days,
            apply=args.apply,
            run_id=args.run_id,
        )
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI, mensagem sanitizada
        print(f"FALHA ({args.target}): {sanitize_error_message(exc)}", file=sys.stderr)
        return EXIT_WRAPPER_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
