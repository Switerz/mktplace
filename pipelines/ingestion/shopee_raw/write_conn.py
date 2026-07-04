"""
Conexão de escrita para a Fase Raw Shopee 2.

Este é o ÚNICO módulo que abre uma conexão de escrita no Data Mart para a
ingestão raw da Shopee, e só faz isso quando `--apply` é usado. Nunca lido
com `os.environ` global: o secret é lido de um arquivo dedicado
(`.env.shopee-write.local`, fora do `.env` principal) para um dicionário
local, e esse dicionário é passado explicitamente por quem chama.

Guardrails aplicados aqui:
  - o arquivo de secret precisa existir, estar coberto por uma regra do
    .gitignore e não estar rastreado pelo git;
  - só as duas chaves esperadas são aceitas (nada a mais, nada a menos);
  - a URL de escrita nunca pode ser igual à URL de leitura
    (`DATAMART_DATABASE_URL`);
  - antes de qualquer DDL/INSERT, um preflight somente-leitura confirma
    host/database esperados, `rolsuper=false`, permissões mínimas no
    schema `raw`, e o estado esperado das tabelas `raw.shopee_*`
    (inexistentes antes do DDL, existentes antes da carga);
  - nenhuma mensagem de erro pode conter usuário/senha — todo texto de
    exceção passa por `sanitize_error_message` antes de virar log/print.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
import psycopg2
from dotenv import dotenv_values

EXPECTED_SECRET_KEYS = frozenset({"DATAMART_SHOPEE_WRITE_URL", "I_UNDERSTAND_THIS_WRITES_DATAMART_RAW"})

# Chave fixa e exclusiva desta ingestão — nunca reaproveitar para outra
# finalidade em outro módulo. Usada com pg_try_advisory_lock/pg_advisory_unlock
# (lock de sessão, não de transação) para impedir duas execuções concorrentes
# de --apply (DDL, piloto ou backfill).
ADVISORY_LOCK_KEY = 987654321123

_SHOPEE_TABLES = (
    "shopee_ingestion_file",
    "shopee_order_item_export",
    "shopee_shop_stats_export",
    "shopee_ads_export",
)

_CREDENTIAL_IN_MESSAGE_RE = re.compile(r"//[^/\s@]+:[^/\s@]+@")


class SecretLoadError(RuntimeError):
    """Falha ao carregar/validar o arquivo de secret de escrita."""


class WritePreflightBlocked(RuntimeError):
    """Preflight de escrita encontrou uma condição bloqueante — nada foi escrito."""


def sanitize_error_message(exc: BaseException) -> str:
    """Nunca confia cegamente em str(exc): remove qualquer trecho
    usuario:senha@ antes de virar log/print, mesmo que uma exceção de baixo
    nível do driver algum dia inclua a DSN inteira na mensagem."""
    return _CREDENTIAL_IN_MESSAGE_RE.sub("//<redacted>@", str(exc))


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def load_write_secret(env_path: Path, repo_root: Path) -> dict[str, str]:
    """Lê `.env.shopee-write.local` (ou caminho equivalente) para um dict
    local, sem tocar em `os.environ`. Levanta SecretLoadError com uma
    mensagem que NUNCA contém valores do arquivo."""
    if not env_path.is_file():
        raise SecretLoadError(f"arquivo de secret não encontrado: {env_path.name}")

    ignore_check = _run_git(["check-ignore", "-q", str(env_path)], cwd=repo_root)
    if ignore_check.returncode != 0:
        raise SecretLoadError(f"{env_path.name} não está coberto por nenhuma regra do .gitignore — bloqueado")

    tracked_check = _run_git(["ls-files", "--error-unmatch", str(env_path)], cwd=repo_root)
    if tracked_check.returncode == 0:
        raise SecretLoadError(f"{env_path.name} está RASTREADO pelo git — bloqueado")

    raw_values = dotenv_values(env_path)
    present_keys = frozenset(k for k, v in raw_values.items() if v not in (None, ""))

    if present_keys != EXPECTED_SECRET_KEYS:
        missing = EXPECTED_SECRET_KEYS - present_keys
        extra = present_keys - EXPECTED_SECRET_KEYS
        parts = []
        if missing:
            parts.append(f"faltando: {sorted(missing)}")
        if extra:
            parts.append(f"chave(s) inesperada(s): {sorted(extra)}")
        raise SecretLoadError(
            f"{env_path.name} não contém exatamente as 2 chaves esperadas ({'; '.join(parts)})"
        )

    values = {k: raw_values[k] for k in EXPECTED_SECRET_KEYS}

    if values["I_UNDERSTAND_THIS_WRITES_DATAMART_RAW"] != "1":
        raise SecretLoadError("I_UNDERSTAND_THIS_WRITES_DATAMART_RAW != '1'")

    return values


def validate_write_guardrails(secret: dict[str, str], datamart_read_url: str) -> str:
    """Confere os guardrails que não dependem de conexão de rede. Retorna a
    write_url validada. Nunca loga os valores."""
    write_url = secret.get("DATAMART_SHOPEE_WRITE_URL", "")
    if not write_url:
        raise SecretLoadError("DATAMART_SHOPEE_WRITE_URL vazio")
    if datamart_read_url and write_url == datamart_read_url:
        raise SecretLoadError(
            "DATAMART_SHOPEE_WRITE_URL é idêntica a DATAMART_DATABASE_URL — nunca reutilizar a credencial de leitura."
        )
    return write_url


@dataclass
class PreflightReport:
    ok: bool = False
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Resumo seguro para exibição — nunca inclui host/IP/usuário/senha.
    safe_summary: dict = field(default_factory=dict)


def _connect_readonly(write_url: str, timeout: int = 15):
    conn = psycopg2.connect(write_url, connect_timeout=timeout)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def _fetch_target_identity(url: str, timeout: int = 15) -> dict:
    """`system_identifier` (pg_control_system) é um fingerprint de 64 bits
    gravado uma vez no initdb do cluster e nunca muda — é uma forma de
    confirmar "mesmo servidor físico" muito mais confiável do que comparar
    hostnames em texto, porque um mesmo RDS pode ter múltiplos endpoints
    DNS (ex: privado via VPN vs público) que resolvem para IPs diferentes
    sem serem bancos diferentes. Faz fallback para porta+database se
    `pg_control_system()` não estiver acessível."""
    conn = psycopg2.connect(url, connect_timeout=timeout)
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), inet_server_port()")
            db, port = cur.fetchone()
            try:
                cur.execute("SELECT system_identifier::text FROM pg_control_system()")
                (sysid,) = cur.fetchone()
            except Exception:
                sysid = None
        return {"db": db, "port": port, "sysid": sysid}
    finally:
        conn.close()


def run_preflight(write_url: str, expected_read_url: str, expect_tables_exist: bool) -> PreflightReport:
    """Preflight somente-SELECT. `expect_tables_exist=False` é usado antes
    do DDL (as 4 tabelas NÃO podem existir ainda); `expect_tables_exist=True`
    é usado antes da carga de dados (as 4 tabelas PRECISAM existir).

    Nunca escreve nada — mesmo em caso de bug neste código, a sessão está
    em `readonly=True` desde a conexão."""
    report = PreflightReport()

    same_physical_cluster = None
    target_check_note = None
    try:
        write_identity = _fetch_target_identity(write_url)
        if expected_read_url:
            read_identity = _fetch_target_identity(expected_read_url)
            if write_identity["sysid"] is not None and read_identity["sysid"] is not None:
                same_physical_cluster = write_identity["sysid"] == read_identity["sysid"]
                target_check_note = "comparado via system_identifier (pg_control_system)"
            else:
                same_physical_cluster = (
                    write_identity["db"] == read_identity["db"] and write_identity["port"] == read_identity["port"]
                )
                target_check_note = "system_identifier indisponível — comparado via database+porta apenas"
    except Exception as exc:  # noqa: BLE001
        report.blocking_reasons.append(f"falha ao conectar: {sanitize_error_message(exc)}")
        return report

    try:
        conn = _connect_readonly(write_url)
    except Exception as exc:  # noqa: BLE001
        report.blocking_reasons.append(f"falha ao conectar: {sanitize_error_message(exc)}")
        return report

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            (current_user,) = cur.fetchone()

            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls = cur.fetchone()

            try:
                cur.execute("SELECT pg_has_role(current_user, 'rds_superuser', 'MEMBER')")
                (is_rds_superuser_member,) = cur.fetchone()
            except Exception:
                is_rds_superuser_member = None  # role rds_superuser não existe (não é RDS)

            cur.execute(
                "SELECT has_schema_privilege(current_user, 'raw', 'CREATE') AS can_create, "
                "has_schema_privilege(current_user, 'raw', 'USAGE') AS can_use"
            )
            can_create_in_raw, can_use_raw = cur.fetchone()

            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'raw' AND table_name = ANY(%s) ORDER BY 1",
                (list(_SHOPEE_TABLES),),
            )
            existing_shopee_objects = [r[0] for r in cur.fetchall()]

            try:
                cur.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
                row = cur.fetchone()
                ssl_in_use = bool(row[0]) if row else None
            except Exception:
                ssl_in_use = None

            cur.execute("SELECT current_setting('server_version')")
            (server_version,) = cur.fetchone()
    finally:
        conn.close()

    if rolsuper:
        report.blocking_reasons.append("rolsuper=true")
    if same_physical_cluster is False:
        report.blocking_reasons.append(
            f"conexão de escrita não aponta para o mesmo cluster físico da leitura ({target_check_note})"
        )
    if not can_create_in_raw or not can_use_raw:
        report.blocking_reasons.append("permissão insuficiente no schema raw (CREATE/USAGE)")

    if expect_tables_exist:
        missing = sorted(set(_SHOPEE_TABLES) - set(existing_shopee_objects))
        if missing:
            report.blocking_reasons.append(f"tabelas raw.shopee_* ainda não existem: {missing} — rode o DDL primeiro")
    else:
        if existing_shopee_objects:
            report.blocking_reasons.append(f"objeto(s) raw.shopee_* já existem: {existing_shopee_objects}")

    if is_rds_superuser_member:
        report.warnings.append("role é membro de rds_superuser (autorizado explicitamente, não bloqueante)")
    if ssl_in_use is False:
        report.warnings.append("conexão sem SSL")
    if ssl_in_use is None:
        report.warnings.append("não foi possível determinar uso de SSL (pg_stat_ssl indisponível)")

    report.safe_summary = {
        "target_confirmado": same_physical_cluster,
        "target_check_method": target_check_note,
        "rolsuper": rolsuper,
        "rolcreatedb": rolcreatedb,
        "rolcreaterole": rolcreaterole,
        "rolreplication": rolreplication,
        "rolbypassrls": rolbypassrls,
        "membro_rds_superuser": is_rds_superuser_member,
        "can_create_in_raw": can_create_in_raw,
        "can_use_raw": can_use_raw,
        "existing_shopee_objects": existing_shopee_objects,
        "ssl_in_use": ssl_in_use,
        "server_version": server_version,
    }
    report.ok = not report.blocking_reasons
    return report


def open_write_connection(write_url: str, timeout: int = 15):
    """Conexão de escrita (não readonly) — só usada dentro de execute_ddl/
    writer.py, sempre com timeouts locais e advisory lock antes de qualquer
    instrução de escrita."""
    conn = psycopg2.connect(write_url, connect_timeout=timeout)
    conn.autocommit = False
    return conn


def try_acquire_advisory_lock(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        (acquired,) = cur.fetchone()
    return bool(acquired)


def release_advisory_lock(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        cur.fetchone()
