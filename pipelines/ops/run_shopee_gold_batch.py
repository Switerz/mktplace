"""
Gate S5.4b — camada de evidência operacional para a DAG: gera os 3 nomes
determinísticos (backup/sha/receipt) a partir de `artifacts_dir`/`batch_id`/
`run_id`, chama o wrapper já auditado do Gate S5.3
(`refresh_shopee_window_if_needed`) SEM alterar uma linha dele, e publica um
receipt atômico com os metadados da execução. Este módulo NÃO tem SQL
próprio, NÃO abre conexão, NÃO chama o resolvedor do Gate S5.2, o preflight,
`execute_shopee_window_refresh`, `diagnose_shopee_window`,
`execute_shopee_window_restore` nem qualquer `sync_region_*` (Neon) —
a ÚNICA operação de dados chamada por este módulo é
`refresh_shopee_window_if_needed`.

`batch_id`/`run_id` são SEMPRE obrigatórios e fornecidos pelo chamador
(a DAG) — nunca gerados automaticamente por este módulo. `batch_id`
identifica a execução Raw; `run_id` identifica esta tentativa operacional
específica. Uma nova tentativa manual precisa vir com um `run_id` novo —
isso é responsabilidade de quem chama, não deste módulo (a garantia "nunca
sobrescrever" já torna perigoso/rejeitado reusar um `run_id` cuja tentativa
anterior tenha publicado qualquer artefato). `batch_id` é registrado no
receipt como evidência fornecida pelo chamador — `batch_id_verified` é
SEMPRE `False`, hardcoded, porque este módulo (como o S5.2) nunca confirma
`batch_id` contra `raw.shopee_ingestion_file` nem qualquer tabela de
controle de lote com a credencial Gold.

Decisão central (mesma do desenho do Gate S5.4a): `operation_outcome`
(`committed`/`no_op`/`blocked`/`failed`, vindo EXCLUSIVAMENTE do resultado
de `refresh_shopee_window_if_needed`) e `receipt_status`
(`ok`/`failed`/`not_attempted`, só sobre a publicação local do receipt) são
DOIS CAMPOS INDEPENDENTES. Uma falha ao publicar o receipt NUNCA altera,
esconde ou reinterpreta `operation_outcome` — `committed`/`no_op` nunca
viram `failed` por causa disso. A única forma como uma falha de receipt se
manifesta é: (a) `receipt_status="failed"`; (b) uma entrada sanitizada em
`problems`; (c) exit code — que ESCALA para 5 (novo, exclusivo desta
combinação) só quando `operation_outcome` é `committed`/`no_op` (os únicos
casos em que o exit code base seria 0, o que esconderia silenciosamente a
lacuna de evidência). `blocked`/`failed` já têm exit codes (2/3/4) que não
sugerem sucesso, então NUNCA escalam para 5 mesmo com receipt falho — o
exit code original é sempre preservado nesses casos.

Nomes determinísticos (dentro de `--artifacts-dir`, absoluto/fora do
repo/já existente/gravável):
  shopee_window_backup_{batch_id}_{run_id}.json
  shopee_window_backup_{batch_id}_{run_id}.json.sha256
  shopee_window_receipt_{batch_id}_{run_id}.json
`batch_id`/`run_id` só chegam a virar nome de arquivo depois de passar pela
allowlist ASCII estrita (`_validate_id_token`) — nunca concatenados crus
antes disso. A proteção FINAL contra sobrescrita/corrida (TOCTOU) nunca é
só o `exists()` antecipado (que é apenas fail-fast) — é a publicação
atômica/exclusiva via `os.link` (mesmo padrão técnico já auditado em
`loader._write_window_backup_atomic`, reimplementado aqui para o receipt,
NUNCA importado — o payload do receipt não tem nenhuma relação com o
payload interno do backup).

Git: `git rev-parse HEAD`/`git status --porcelain` via `subprocess.run`
com lista de argumentos (nunca `shell=True`), timeout curto
(`_GIT_TIMEOUT_SECONDS`). Nunca imprime/propaga stderr nativo do processo
Git — só `returncode`/`stdout` (para o hash) ou `type(exc).__name__` (para
qualquer falha). `.git` ausente, binário `git` não encontrado, ou timeout
NUNCA bloqueiam — sempre viram `warning`, nunca `problem`, nunca
reason_code. `git_dirty` é só um booleano (nunca a lista de arquivos
modificados) — quando `True`, também gera um warning GENÉRICO (sem nomes
de arquivo, sem a saída de `git status`) avisando que `git_commit` pode
não representar integralmente o código em execução; nunca bloqueia. Clock
(`_utc_now`) e coletor Git (`_run_git_subprocess`) são funções soltas no
nível do módulo — testes fazem `monkeypatch.setattr` nelas diretamente;
não há parâmetro de injeção na assinatura pública nem framework genérico
de dependência.

Gate S5.4b.1 — hardening final antes do commit (quatro correções, sem
tocar `loader.py`/`window_write_conn.py`/`shopee_batch_window.py`/
`refresh_shopee_window_if_needed.py`):
  1. `file_ids` agora é validado explicitamente com a função pública
     `shopee_batch_window.validate_batch_file_ids` — tanto na CLI (logo
     após converter string→int, antes de Git/`artifacts_dir`/probe/
     secret/conexão) quanto dentro da própria `run_shopee_gold_batch`
     (defesa em profundidade, antes até de coletar Git ou tocar o
     filesystem). A lista já validada/ordenada é a mesma usada nos nomes
     determinísticos, na chamada a `refresh_shopee_window_if_needed`, no
     receipt e no stdout. Entrada inválida (vazia, duplicada, bool, zero,
     negativa, fora da faixa bigint, acima do limite) bloqueia como
     `blocked`/`invalid_input`/`receipt_status=not_attempted`/exit 2 —
     o refresh nunca é chamado.
  2. `git_dirty=True` agora sempre produz o warning genérico descrito
     acima (antes, só a FALHA em determinar `git_dirty` gerava warning —
     o estado "dirty" em si era silencioso).
  3. `_publish_receipt_atomic` foi reestruturado para nunca retornar de
     dentro do bloco protegido por `finally`: `publish_problem`/
     `cleanup_warning`/`linked_successfully` são variáveis locais
     preenchidas primeiro, e a função só retorna depois que o
     `try`/`finally` termina — garantindo que uma falha de escrita/link E
     uma falha de limpeza do temporário cheguem SEMPRE juntas no retorno
     (antes, um `return` dentro do `try` "fixava" o valor antes do
     `finally` rodar, perdendo silenciosamente `cleanup_warning` sempre
     que a limpeza também falhasse).
  4. A revalidação pós-publicação agora compara o payload relido do disco
     INTEGRALMENTE (`reread != payload`) — não mais só `schema_version`/
     `run_id`. Qualquer campo divergente (`operation_outcome`,
     `backup_sha256`, `file_ids`, etc.) vira `problem`/
     `receipt_status=failed`; o receipt divergente nunca é removido
     automaticamente — fica preservado como evidência.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

from pipelines.common.config import settings
from pipelines.ingestion.gold_regional import shopee_batch_window
from pipelines.ingestion.gold_regional import window_write_conn
from pipelines.ingestion.gold_regional.window_write_conn import sanitize_error_message
from pipelines.ops import refresh_shopee_window_if_needed as refresh_wrapper

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WINDOW_WRITE_SECRET_PATH = REPO_ROOT / ".env.gold-window-write.local"

__all__ = [
    "ShopeeGoldBatchResult",
    "BatchInputError",
    "run_shopee_gold_batch",
    "run_cli",
    "main",
]

RECEIPT_SCHEMA_VERSION = 1
_GIT_TIMEOUT_SECONDS = 5
_MAX_ID_LENGTH = 100
# Allowlist ASCII estrita: primeiro caractere alfanumérico; depois letras,
# dígitos, ponto, underscore, hífen. Qualquer Unicode (inclusive
# lookalikes/RTL/zero-width), espaço, `/`, `\` já é rejeitado pelo simples
# não-casamento -- nenhuma normalização Unicode separada é necessária.
_ID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Vocabulário FECHADO de reason_code deste módulo (todos exit 2 -- nenhum
# deles chega perto do banco). Reason_codes que vêm de
# refresh_shopee_window_if_needed são reaproveitados VERBATIM (nunca
# reinventados) -- ver _BASE_EXIT_CODE abaixo.
REASON_INVALID_INPUT = "invalid_input"
REASON_ARTIFACTS_DIR_INVALID = "artifacts_dir_invalid"
REASON_ARTIFACTS_DIR_NOT_WRITABLE = "artifacts_dir_not_writable"
REASON_DATAMART_URL_NOT_CONFIGURED = "datamart_url_not_configured"
REASON_SECRET_LOAD_ERROR = "secret_load_error"
REASON_UNEXPECTED_ERROR = "unexpected_error"

_LOCAL_REASON_EXIT_CODE = {
    REASON_INVALID_INPUT: 2,
    REASON_ARTIFACTS_DIR_INVALID: 2,
    REASON_ARTIFACTS_DIR_NOT_WRITABLE: 2,
    REASON_DATAMART_URL_NOT_CONFIGURED: 2,
    REASON_SECRET_LOAD_ERROR: 2,
    REASON_UNEXPECTED_ERROR: 4,
}
# Tabela combinada: reason_codes deste módulo + os que
# refresh_shopee_window_if_needed já usa, reaproveitados da PRÓPRIA tabela
# do S5.3 (nunca copiados à mão) -- fonte única, sem risco de drift.
_BASE_EXIT_CODE = {**_LOCAL_REASON_EXIT_CODE, **refresh_wrapper._REASON_CODE_EXIT_CODE}


class BatchInputError(ValueError):
    """Entrada inválida (file_id/batch_id/run_id) detectada ANTES de
    qualquer secret/filesystem mutável/conexão."""


@dataclass
class ShopeeGoldBatchResult:
    """Espelha 1:1 o schema do receipt (ver docstring do módulo).
    `operation_outcome`/`receipt_status` são SEMPRE dois campos
    independentes. Nunca linhas individuais, nunca order_id, nunca
    filename original, nunca conteúdo do backup."""
    operation_outcome: str  # "committed" | "no_op" | "blocked" | "failed"
    reason_code: str
    receipt_status: str  # "ok" | "failed" | "not_attempted"
    batch_id: str = ""
    batch_id_verified: bool = False  # SEMPRE False -- nunca setado em nenhum outro lugar deste módulo.
    run_id: str = ""
    file_ids: list = field(default_factory=list)
    started_at_utc: Optional[str] = None
    finished_at_utc: Optional[str] = None
    duration_seconds: Optional[float] = None
    git_commit: Optional[str] = None
    git_dirty: Optional[bool] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    window_days: Optional[int] = None
    silver_row_count: int = 0
    rows_deleted: int = 0
    rows_inserted: int = 0
    gmv_before: Optional[Decimal] = None
    gmv_after: Optional[Decimal] = None
    backup_path: Optional[str] = None
    backup_sha256: Optional[str] = None
    receipt_path: Optional[str] = None
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Clock / Git -- funções soltas no nível do módulo, monkeypatcháveis
# diretamente nos testes. Nenhum parâmetro de injeção na assinatura
# pública, nenhum framework genérico.
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_git_subprocess(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        timeout=_GIT_TIMEOUT_SECONDS, shell=False,
    )


def _collect_git_commit(repo_root: Path) -> tuple[Optional[str], Optional[str]]:
    """Nunca levanta, nunca imprime stderr nativo do Git. Retorna
    (git_commit, warning) -- warning nunca vira problem/bloqueio."""
    try:
        result = _run_git_subprocess(["rev-parse", "HEAD"], repo_root)
    except (OSError, subprocess.TimeoutExpired):
        return None, "git_commit indisponível: falha ao executar git (repositório .git ausente, git não encontrado, ou tempo esgotado)"
    if result.returncode != 0:
        return None, "git_commit indisponível: git rev-parse HEAD falhou (repositório .git ausente ou não é um repo git)"
    commit = result.stdout.strip()
    if not commit:
        return None, "git_commit indisponível: saída vazia de git rev-parse HEAD"
    return commit, None


def _collect_git_dirty(repo_root: Path) -> tuple[Optional[bool], Optional[str]]:
    """git_dirty é só um booleano -- nunca a lista de arquivos modificados.
    Quando `dirty=True`, retorna também um warning GENÉRICO (nunca nomes de
    arquivo, nunca a saída de `git status`) avisando que `git_commit` pode
    não refletir integralmente o código em execução. Nunca bloqueia."""
    try:
        result = _run_git_subprocess(["status", "--porcelain"], repo_root)
    except (OSError, subprocess.TimeoutExpired):
        return None, "git_dirty indisponível: falha ao executar git (repositório .git ausente, git não encontrado, ou tempo esgotado)"
    if result.returncode != 0:
        return None, "git_dirty indisponível: git status --porcelain falhou"
    dirty = bool(result.stdout.strip())
    if dirty:
        return True, (
            "working tree possui alterações não commitadas; git_commit pode não "
            "representar integralmente o código executado"
        )
    return False, None


def _dedup_stable(items: Sequence[str]) -> list[str]:
    seen: set = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Validação local -- allowlist de ids, artifacts_dir, nomes determinísticos,
# probe de escrita. Tudo isto roda ANTES de secret/filesystem mutável/banco.
# ---------------------------------------------------------------------------

def _validate_id_token(value, field_name: str) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return f"{field_name} não pode ser vazio"
    if len(value) > _MAX_ID_LENGTH:
        return f"{field_name} acima do limite de {_MAX_ID_LENGTH} caracteres"
    if ".." in value:
        return f"{field_name} não pode conter '..'"
    if not _ID_TOKEN_RE.match(value):
        return (
            f"{field_name} contém caractere fora da allowlist (permitido: ASCII "
            "letra/dígito/ponto/underscore/hífen, começando por alfanumérico)"
        )
    return None


def _validate_artifacts_dir(artifacts_dir: Path, repo_root: Path) -> Optional[str]:
    """Mesmo espírito de mensagens de `loader._validate_new_window_audit_path`
    -- categoria do problema, nunca o caminho absoluto na mensagem."""
    try:
        if not artifacts_dir.is_absolute():
            return "artifacts_dir precisa ser um caminho absoluto"
        resolved = artifacts_dir.resolve()
        repo_resolved = repo_root.resolve()
        if resolved == repo_resolved or repo_resolved in resolved.parents:
            return "artifacts_dir não pode estar dentro do repositório"
        if not artifacts_dir.exists():
            return "artifacts_dir não existe"
        if not artifacts_dir.is_dir():
            return "artifacts_dir existe mas não é um diretório (é um arquivo ou outro tipo de entrada)"
    except (OSError, RuntimeError, ValueError) as exc:
        return f"falha ao validar artifacts_dir (caminho inacessível ou inválido): {type(exc).__name__}"
    return None


def _artifact_paths(artifacts_dir: Path, batch_id: str, run_id: str) -> tuple[Path, Path, Path]:
    backup_path = artifacts_dir / f"shopee_window_backup_{batch_id}_{run_id}.json"
    sha_path = Path(str(backup_path) + ".sha256")
    receipt_path = artifacts_dir / f"shopee_window_receipt_{batch_id}_{run_id}.json"
    return backup_path, sha_path, receipt_path


def _existing_artifact_problem(backup_path: Path, sha_path: Path, receipt_path: Path) -> Optional[str]:
    for label, p in (("backup", backup_path), ("sha256", sha_path), ("receipt", receipt_path)):
        try:
            exists = p.exists()
        except OSError as exc:
            return f"falha ao verificar existência do artefato {label} (caminho inacessível): {type(exc).__name__}"
        if exists:
            return f"artefato {label} já existe para este batch_id/run_id (recusado — nunca sobrescrever): {p.name}"
    return None


def _probe_artifacts_dir_writable(artifacts_dir: Path) -> Optional[str]:
    """Cria um arquivo temporário EXCLUSIVO, flush+fsync, remove. Falha na
    criação OU na remoção bloqueia -- nunca deixa resíduo silenciosamente."""
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(artifacts_dir), prefix=".probe_", suffix=".tmp")
    except OSError as exc:
        return f"falha ao criar arquivo de teste em artifacts_dir (permissão/disco?): {type(exc).__name__}"

    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(b"probe")
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return f"falha ao escrever/sincronizar arquivo de teste em artifacts_dir: {type(exc).__name__}"

    try:
        tmp_path.unlink()
    except OSError as exc:
        return f"falha ao remover arquivo de teste de artifacts_dir (resíduo pode ter ficado): {type(exc).__name__}"

    return None


# ---------------------------------------------------------------------------
# Receipt -- payload + publicação atômica (mesmo padrão técnico já auditado
# em loader._write_window_backup_atomic, reimplementado aqui, nunca
# importado -- schema completamente diferente do backup).
# ---------------------------------------------------------------------------

def _decimal_to_str(value: Optional[Decimal]) -> Optional[str]:
    return str(value) if value is not None else None


def _build_receipt_payload(
    *, batch_id: str, run_id: str, file_ids: list, started_at: datetime, finished_at: datetime,
    duration_seconds: float, git_commit: Optional[str], git_dirty: Optional[bool],
    refresh_result, receipt_path: Path,
) -> dict:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "batch_id": batch_id,
        "batch_id_verified": False,
        "run_id": run_id,
        "file_ids": list(file_ids),
        "started_at_utc": _format_utc(started_at),
        "finished_at_utc": _format_utc(finished_at),
        "duration_seconds": duration_seconds,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "operation_outcome": refresh_result.outcome,
        "reason_code": refresh_result.reason_code,
        "date_from": refresh_result.date_from.isoformat() if refresh_result.date_from else None,
        "date_to": refresh_result.date_to.isoformat() if refresh_result.date_to else None,
        "window_days": refresh_result.window_days,
        "silver_row_count": refresh_result.silver_row_count,
        "rows_deleted": refresh_result.rows_deleted,
        "rows_inserted": refresh_result.rows_inserted,
        "gmv_before": _decimal_to_str(refresh_result.gmv_before),
        "gmv_after": _decimal_to_str(refresh_result.gmv_after),
        "backup_path": refresh_result.backup_path,
        "backup_sha256": refresh_result.backup_sha256,
        "receipt_path": str(receipt_path),
        # Sempre "ok" DENTRO do arquivo: um receipt fisicamente presente e
        # íntegro é, por definição, publicado com sucesso -- "failed"/
        # "not_attempted" só existem no RESULTADO em memória
        # (ShopeeGoldBatchResult), nunca escritos neste payload.
        "receipt_status": "ok",
        "problems": list(refresh_result.problems),
        "warnings": list(refresh_result.warnings),
    }


def _publish_receipt_atomic(receipt_path: Path, payload: dict) -> tuple[Optional[str], Optional[str]]:
    """Retorna (problem, warning). Gate S5.4b.1: NUNCA retorna de DENTRO do
    bloco protegido por `finally` -- fazer isso perderia `cleanup_warning`
    sempre que a limpeza do temporário TAMBÉM falhasse (o valor de retorno
    de um `return` dentro do `try` já fica fixado antes do `finally` rodar;
    o `finally` não pode mais alterá-lo, só rodar por cima). Em vez disso,
    `publish_problem`/`cleanup_warning`/`linked_successfully` são variáveis
    locais preenchidas primeiro, e a função só retorna DEPOIS que o
    try/finally termina — garantindo que as duas informações (causa
    principal + aviso de limpeza) cheguem juntas sempre que ambas
    existirem.

    `problem` só é não-None quando a publicação em si falhou (ou a
    revalidação pós-publicação divergiu) -- `receipt_status` vira "failed"
    nesse caso. Falha apenas na limpeza do temporário NUNCA mascara o
    sucesso da publicação -- vira só um warning. Nunca sobrescreve
    (`os.link` + `FileExistsError`). Nunca apaga backup/sha -- este helper
    só toca o próprio `receipt_path`/temp; um receipt divergente relido do
    disco também NUNCA é removido automaticamente -- fica preservado como
    evidência para investigação manual."""
    try:
        data = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return f"falha ao serializar o receipt para JSON: {type(exc).__name__}", None

    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(receipt_path.parent), prefix=receipt_path.name + ".", suffix=".tmp")
    except OSError as exc:
        return f"falha ao criar arquivo temporário do receipt: {type(exc).__name__}", None

    tmp_path = Path(tmp_name)
    publish_problem: Optional[str] = None
    cleanup_warning: Optional[str] = None
    linked_successfully = False

    try:
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            publish_problem = f"falha ao escrever/sincronizar o receipt: {type(exc).__name__}"

        if publish_problem is None:
            try:
                os.link(tmp_path, receipt_path)
                linked_successfully = True
            except FileExistsError:
                publish_problem = "receipt_path passou a existir entre a validação e a publicação (corrida detectada, nada sobrescrito)"
            except OSError as exc:
                publish_problem = f"falha ao publicar o receipt (link): {type(exc).__name__}"
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError as exc:
                if linked_successfully:
                    cleanup_warning = (
                        f"falha ao remover temporário do receipt ({type(exc).__name__}) — "
                        "receipt já publicado com sucesso, resíduo pode ter ficado"
                    )
                else:
                    cleanup_warning = (
                        f"falha ao remover temporário do receipt ({type(exc).__name__}) — "
                        "publicação também falhou, resíduo pode ter ficado"
                    )

    if publish_problem is not None:
        return publish_problem, cleanup_warning

    # Revalida o payload INTEGRAL relido do disco -- nunca só schema_version/
    # run_id. Qualquer campo divergente (operation_outcome, backup_sha256,
    # file_ids, o que for) vira problem/receipt_status=failed; o arquivo em
    # si nunca é removido automaticamente.
    try:
        reread = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"receipt publicado mas falhou na revalidação de leitura: {type(exc).__name__}", cleanup_warning
    if reread != payload:
        return "receipt relido do disco não bate integralmente com o payload esperado", cleanup_warning

    return None, cleanup_warning


def _blocked_result(
    *, batch_id: str, run_id: str, file_ids: list, reason_code: str, problem: str,
    receipt_path: Optional[Path] = None, started_at: Optional[datetime] = None,
    git_commit: Optional[str] = None, git_dirty: Optional[bool] = None,
    warnings: Optional[list] = None, operation_outcome: str = "blocked",
) -> ShopeeGoldBatchResult:
    started_at = started_at or _utc_now()
    finished_at = _utc_now()
    duration = max(0.0, (finished_at - started_at).total_seconds())
    return ShopeeGoldBatchResult(
        operation_outcome=operation_outcome, reason_code=reason_code, receipt_status="not_attempted",
        batch_id=batch_id, run_id=run_id, file_ids=list(file_ids),
        started_at_utc=_format_utc(started_at), finished_at_utc=_format_utc(finished_at),
        duration_seconds=duration, git_commit=git_commit, git_dirty=git_dirty,
        receipt_path=str(receipt_path) if receipt_path else None,
        problems=[problem], warnings=list(warnings or []),
    )


def _exit_code_for(result: ShopeeGoldBatchResult) -> int:
    """Nunca deriva só de reason_code: a MESMA operação pode ter dois exit
    codes diferentes dependendo de receipt_status. Só committed/no_op
    escalam para 5 quando o receipt falha -- blocked/failed já têm exit
    codes (2/3/4) que não sugerem sucesso, então nunca escalam."""
    base = _BASE_EXIT_CODE.get(result.reason_code, 4)
    if result.receipt_status == "failed" and result.operation_outcome in ("committed", "no_op"):
        return 5
    return base


def run_shopee_gold_batch(
    write_url: str,
    datamart_read_url: str,
    file_ids: Sequence[int],
    artifacts_dir: Path,
    batch_id: str,
    run_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> ShopeeGoldBatchResult:
    """Único contrato público deste módulo.

    Ordem: valida `file_ids` (`shopee_batch_window.validate_batch_file_ids`,
    a MESMA função pública do Gate S5.2 -- defesa em profundidade, já que a
    CLI já validou antes de chegar aqui) -> valida `batch_id`/`run_id`
    (allowlist) -- AMBOS ainda antes de coletar Git ou tocar o filesystem
    -> valida `artifacts_dir` (absoluto/fora do repo/existe/é diretório) ->
    computa os 3 nomes determinísticos (a partir da lista de `file_ids` já
    validada/ordenada) e recusa se QUALQUER um já existir -> probe de
    escrita (cria+fsync+remove) -> SÓ ENTÃO chama
    `refresh_shopee_window_if_needed(write_url, datamart_read_url,
    file_ids, backup_path)` -- a ÚNICA operação de dados deste módulo,
    nunca o resolvedor S5.2/preflight/`execute_shopee_window_refresh`/
    diagnose/restore/sync diretamente -- -> monta e publica o receipt
    atômico -> `operation_outcome`/`receipt_status` permanecem SEMPRE dois
    campos independentes."""
    try:
        file_ids_list = shopee_batch_window.validate_batch_file_ids(file_ids)
    except shopee_batch_window.BatchWindowInputError as exc:
        return _blocked_result(
            batch_id=batch_id, run_id=run_id, file_ids=list(file_ids),
            reason_code=REASON_INVALID_INPUT, problem=str(exc),
        )

    id_problem = _validate_id_token(batch_id, "batch_id") or _validate_id_token(run_id, "run_id")
    if id_problem:
        return _blocked_result(
            batch_id=batch_id, run_id=run_id, file_ids=file_ids_list,
            reason_code=REASON_INVALID_INPUT, problem=id_problem,
        )

    started_at = _utc_now()
    git_commit, git_commit_warning = _collect_git_commit(repo_root)
    git_dirty, git_dirty_warning = _collect_git_dirty(repo_root)
    git_warnings = _dedup_stable([w for w in (git_commit_warning, git_dirty_warning) if w])

    dir_problem = _validate_artifacts_dir(artifacts_dir, repo_root)
    if dir_problem:
        return _blocked_result(
            batch_id=batch_id, run_id=run_id, file_ids=file_ids_list,
            reason_code=REASON_ARTIFACTS_DIR_INVALID, problem=dir_problem,
            started_at=started_at, git_commit=git_commit, git_dirty=git_dirty, warnings=git_warnings,
        )

    backup_path, sha_path, receipt_path = _artifact_paths(artifacts_dir, batch_id, run_id)

    existing_problem = _existing_artifact_problem(backup_path, sha_path, receipt_path)
    if existing_problem:
        return _blocked_result(
            batch_id=batch_id, run_id=run_id, file_ids=file_ids_list,
            reason_code=REASON_ARTIFACTS_DIR_INVALID, problem=existing_problem, receipt_path=receipt_path,
            started_at=started_at, git_commit=git_commit, git_dirty=git_dirty, warnings=git_warnings,
        )

    probe_problem = _probe_artifacts_dir_writable(artifacts_dir)
    if probe_problem:
        return _blocked_result(
            batch_id=batch_id, run_id=run_id, file_ids=file_ids_list,
            reason_code=REASON_ARTIFACTS_DIR_NOT_WRITABLE, problem=probe_problem, receipt_path=receipt_path,
            started_at=started_at, git_commit=git_commit, git_dirty=git_dirty, warnings=git_warnings,
        )

    # Única operação de dados chamada por este módulo.
    refresh_result = refresh_wrapper.refresh_shopee_window_if_needed(
        write_url, datamart_read_url, file_ids_list, backup_path, repo_root=repo_root,
    )

    finished_at = _utc_now()
    duration = max(0.0, (finished_at - started_at).total_seconds())

    receipt_payload = _build_receipt_payload(
        batch_id=batch_id, run_id=run_id, file_ids=file_ids_list,
        started_at=started_at, finished_at=finished_at, duration_seconds=duration,
        git_commit=git_commit, git_dirty=git_dirty,
        refresh_result=refresh_result, receipt_path=receipt_path,
    )
    publish_problem, publish_warning = _publish_receipt_atomic(receipt_path, receipt_payload)
    receipt_status = "failed" if publish_problem else "ok"

    problems = list(refresh_result.problems)
    if publish_problem:
        problems.append(publish_problem)
    warnings = _dedup_stable(
        git_warnings + list(refresh_result.warnings) + ([publish_warning] if publish_warning else [])
    )

    return ShopeeGoldBatchResult(
        operation_outcome=refresh_result.outcome,
        reason_code=refresh_result.reason_code,
        receipt_status=receipt_status,
        batch_id=batch_id, run_id=run_id, file_ids=file_ids_list,
        started_at_utc=_format_utc(started_at), finished_at_utc=_format_utc(finished_at),
        duration_seconds=duration,
        git_commit=git_commit, git_dirty=git_dirty,
        date_from=refresh_result.date_from, date_to=refresh_result.date_to, window_days=refresh_result.window_days,
        silver_row_count=refresh_result.silver_row_count,
        rows_deleted=refresh_result.rows_deleted, rows_inserted=refresh_result.rows_inserted,
        gmv_before=refresh_result.gmv_before, gmv_after=refresh_result.gmv_after,
        backup_path=refresh_result.backup_path, backup_sha256=refresh_result.backup_sha256,
        receipt_path=str(receipt_path),
        problems=problems, warnings=warnings,
    )


# ---------------------------------------------------------------------------
# CLI — python -m pipelines.ops.run_shopee_gold_batch
#   --file-id <id1> --file-id <id2> --artifacts-dir <dir-absoluto>
#   --batch-id <id> --run-id <id> [--json]
# ---------------------------------------------------------------------------

def _parse_cli_file_ids(raw_values: list[str]) -> list[int]:
    """Só converte string -> int. A validação de fato (faixa, duplicados,
    limite) continua sendo responsabilidade EXCLUSIVA de
    `shopee_batch_window.validate_batch_file_ids`, chamada mais adiante via
    `refresh_shopee_window_if_needed` -> `resolve_shopee_batch_window`.
    Nunca importa o parser privado de nenhum outro módulo (mesma correção
    já aplicada no Gate S5.3.1)."""
    parsed: list[int] = []
    for raw in raw_values:
        try:
            parsed.append(int(raw))
        except (TypeError, ValueError):
            raise BatchInputError(f"file_id inválido (esperado inteiro): {raw!r}") from None
    return parsed


def _result_to_dict(result: ShopeeGoldBatchResult) -> dict:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "batch_id": result.batch_id,
        "batch_id_verified": result.batch_id_verified,
        "run_id": result.run_id,
        "file_ids": result.file_ids,
        "started_at_utc": result.started_at_utc,
        "finished_at_utc": result.finished_at_utc,
        "duration_seconds": result.duration_seconds,
        "git_commit": result.git_commit,
        "git_dirty": result.git_dirty,
        "operation_outcome": result.operation_outcome,
        "reason_code": result.reason_code,
        "date_from": result.date_from.isoformat() if result.date_from else None,
        "date_to": result.date_to.isoformat() if result.date_to else None,
        "window_days": result.window_days,
        "silver_row_count": result.silver_row_count,
        "rows_deleted": result.rows_deleted,
        "rows_inserted": result.rows_inserted,
        "gmv_before": _decimal_to_str(result.gmv_before),
        "gmv_after": _decimal_to_str(result.gmv_after),
        "backup_path": result.backup_path,
        "backup_sha256": result.backup_sha256,
        "receipt_path": result.receipt_path,
        "receipt_status": result.receipt_status,
        "problems": result.problems,
        "warnings": result.warnings,
    }


def _print_json(result: ShopeeGoldBatchResult) -> None:
    # Único documento JSON no stdout -- mesmo quando o receipt falha (o
    # stdout é a evidência de emergência nesse caso). Logs/avisos sempre em
    # stderr, ver run_cli().
    print(json.dumps(_result_to_dict(result), ensure_ascii=False, sort_keys=True))


def _print_human(result: ShopeeGoldBatchResult) -> None:
    print("=== Job Shopee Gold por lote (S5.3 + evidência operacional) ===")
    print(f"  operation_outcome: {result.operation_outcome}")
    print(f"  reason_code: {result.reason_code}")
    print(f"  receipt_status: {result.receipt_status}")
    print(f"  batch_id: {result.batch_id}")
    print(f"  run_id: {result.run_id}")
    print(f"  date_from: {result.date_from}")
    print(f"  date_to: {result.date_to}")
    print(f"  rows_deleted: {result.rows_deleted}")
    print(f"  rows_inserted: {result.rows_inserted}")
    if result.backup_path:
        print(f"  backup_path: {result.backup_path}")
        print(f"  backup_sha256: {result.backup_sha256}")
    if result.receipt_path:
        print(f"  receipt_path: {result.receipt_path}")
    for p in result.problems:
        print(f"  problema: {p}")
    for w in result.warnings:
        print(f"  aviso: {w}")


def _emit(result: ShopeeGoldBatchResult, as_json: bool) -> None:
    if as_json:
        _print_json(result)
    else:
        _print_human(result)


def run_cli(
    raw_file_ids: list[str],
    artifacts_dir_raw: str,
    batch_id: str,
    run_id: str,
    as_json: bool,
    secret_path: Path = DEFAULT_WINDOW_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
) -> int:
    """CLI fina. Ordem: file_ids (parse string->int, depois
    `shopee_batch_window.validate_batch_file_ids` -- a MESMA função pública
    do Gate S5.2, nunca uma reimplementação) -> batch_id/run_id (allowlist)
    -- tudo isso ANTES de Git/artifacts_dir/probe/secret/banco -- ->
    artifacts_dir (estrutural + 3 nomes ainda não existentes) -> probe de
    escrita -> `DATAMART_DATABASE_URL` configurado -> secret dedicado ->
    guardrails -> `run_shopee_gold_batch` (a ÚNICA API pública deste
    módulo, que revalida tudo de novo em profundidade). Exit code via
    `_exit_code_for`."""
    try:
        file_ids = _parse_cli_file_ids(raw_file_ids)
    except BatchInputError as exc:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=[],
                                  reason_code=REASON_INVALID_INPUT, problem=str(exc))
        _emit(result, as_json)
        return _exit_code_for(result)

    try:
        file_ids = shopee_batch_window.validate_batch_file_ids(file_ids)
    except shopee_batch_window.BatchWindowInputError as exc:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=file_ids,
                                  reason_code=REASON_INVALID_INPUT, problem=str(exc))
        _emit(result, as_json)
        return _exit_code_for(result)

    id_problem = _validate_id_token(batch_id, "batch_id") or _validate_id_token(run_id, "run_id")
    if id_problem:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=file_ids,
                                  reason_code=REASON_INVALID_INPUT, problem=id_problem)
        _emit(result, as_json)
        return _exit_code_for(result)

    artifacts_dir = Path(artifacts_dir_raw)
    dir_problem = _validate_artifacts_dir(artifacts_dir, repo_root)
    if dir_problem:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=file_ids,
                                  reason_code=REASON_ARTIFACTS_DIR_INVALID, problem=dir_problem)
        _emit(result, as_json)
        return _exit_code_for(result)

    backup_path, sha_path, receipt_path = _artifact_paths(artifacts_dir, batch_id, run_id)
    existing_problem = _existing_artifact_problem(backup_path, sha_path, receipt_path)
    if existing_problem:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=file_ids,
                                  reason_code=REASON_ARTIFACTS_DIR_INVALID, problem=existing_problem,
                                  receipt_path=receipt_path)
        _emit(result, as_json)
        return _exit_code_for(result)

    probe_problem = _probe_artifacts_dir_writable(artifacts_dir)
    if probe_problem:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=file_ids,
                                  reason_code=REASON_ARTIFACTS_DIR_NOT_WRITABLE, problem=probe_problem,
                                  receipt_path=receipt_path)
        _emit(result, as_json)
        return _exit_code_for(result)

    if not settings.datamart_url:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=file_ids,
                                  reason_code=REASON_DATAMART_URL_NOT_CONFIGURED,
                                  problem="DATAMART_DATABASE_URL não configurado — job Shopee abortado.",
                                  receipt_path=receipt_path)
        _emit(result, as_json)
        return _exit_code_for(result)

    try:
        secret = window_write_conn.load_window_write_secret(secret_path, repo_root)
    except window_write_conn.WindowSecretLoadError as exc:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=file_ids,
                                  reason_code=REASON_SECRET_LOAD_ERROR, problem=str(exc), receipt_path=receipt_path)
        _emit(result, as_json)
        return _exit_code_for(result)

    try:
        write_url = window_write_conn.validate_window_write_guardrails(secret, settings.datamart_url)
    except window_write_conn.WindowSecretLoadError as exc:
        result = _blocked_result(batch_id=batch_id, run_id=run_id, file_ids=file_ids,
                                  reason_code=REASON_SECRET_LOAD_ERROR, problem=str(exc), receipt_path=receipt_path)
        _emit(result, as_json)
        return _exit_code_for(result)

    try:
        result = run_shopee_gold_batch(
            write_url, settings.datamart_url, file_ids, artifacts_dir, batch_id, run_id, repo_root=repo_root,
        )
    except Exception as exc:  # noqa: BLE001
        result = _blocked_result(
            batch_id=batch_id, run_id=run_id, file_ids=file_ids,
            reason_code=REASON_UNEXPECTED_ERROR,
            problem=f"falha inesperada e não tratada: {sanitize_error_message(exc)}",
            receipt_path=receipt_path, operation_outcome="failed",
        )

    for w in result.warnings:
        print(f"AVISO: {w}", file=sys.stderr)
    if result.reason_code == refresh_wrapper.REASON_PREFLIGHT_BLOCKED:
        for p in result.problems:
            print(f"PREFLIGHT: {p}", file=sys.stderr)

    _emit(result, as_json)
    return _exit_code_for(result)


def main(argv: Optional[list[str]] = None) -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(
        description=(
            "Gate S5.4b — camada de evidência operacional sobre o wrapper S5.3: gera "
            "backup/sha/receipt deterministicamente a partir de artifacts_dir/batch_id/"
            "run_id e publica um receipt atômico. batch_id/run_id são obrigatórios "
            "(fornecidos pela DAG, nunca gerados automaticamente)."
        )
    )
    parser.add_argument(
        "--file-id", dest="file_ids", action="append", default=[], metavar="<bigint>",
        help="file_id da Silver (repetível). Pelo menos um obrigatório.",
    )
    parser.add_argument(
        "--artifacts-dir", dest="artifacts_dir", required=True, metavar="<dir-absoluto>",
        help="Diretório absoluto, fora do repositório, já existente e gravável para backup/sha/receipt.",
    )
    parser.add_argument(
        "--batch-id", dest="batch_id", required=True, metavar="<id>",
        help="Identificador do lote Raw/Silver, fornecido pela automação externa.",
    )
    parser.add_argument(
        "--run-id", dest="run_id", required=True, metavar="<id>",
        help="Identificador desta tentativa operacional, fornecido pela DAG. Uma nova "
             "tentativa manual deve usar um run_id novo.",
    )
    parser.add_argument("--json", action="store_true", help="Saída como um único documento JSON em stdout.")
    args = parser.parse_args(argv)

    try:
        return run_cli(args.file_ids, args.artifacts_dir, args.batch_id, args.run_id, args.json)
    except Exception as exc:  # noqa: BLE001
        result = _blocked_result(
            batch_id=args.batch_id, run_id=args.run_id, file_ids=[],
            reason_code=REASON_UNEXPECTED_ERROR,
            problem=f"falha inesperada e não tratada: {sanitize_error_message(exc)}",
            operation_outcome="failed",
        )
        _emit(result, args.json)
        return _exit_code_for(result)


if __name__ == "__main__":
    sys.exit(main())
