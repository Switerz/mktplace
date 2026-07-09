"""
Conexão de escrita para o Gate 6A — aplicação de `gold.marketplace_region_daily`
no Data Mart primary.

Este é o ÚNICO módulo que abre uma conexão de escrita no Data Mart para a
Gold regional, e só faz isso quando explicitamente chamado por `ddl.py`/o
futuro loader da primeira carga. Nunca lido com `os.environ` global: o
secret é lido de um arquivo dedicado (`.env.gold-write.local`, fora do
`.env` principal) para um dicionário local, e esse dicionário é passado
explicitamente por quem chama. Mesmo padrão de segurança já usado e testado
em `pipelines/ingestion/shopee_raw/write_conn.py` — deliberadamente NÃO
compartilhado com aquele módulo (escopo e ADVISORY_LOCK_KEY exclusivos desta
finalidade, para nunca um write acidentalmente rodar sob o lock/credencial
de outra ingestão).

Guardrails aplicados aqui:
  - o arquivo de secret precisa existir, estar coberto por uma regra do
    .gitignore e não estar rastreado pelo git;
  - só as duas chaves esperadas são aceitas (nada a mais, nada a menos);
  - a URL de escrita nunca pode ser igual à URL de leitura
    (`DATAMART_DATABASE_URL`);
  - antes de qualquer DDL/INSERT, um preflight somente-leitura confirma
    host/database esperados (via `system_identifier`, mais confiável que
    hostname em texto), `rolsuper=false`, permissões mínimas no schema
    `gold`, e o estado esperado de `gold.marketplace_region_daily`
    (inexistente antes do DDL, existente antes da carga);
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

EXPECTED_SECRET_KEYS = frozenset({"DATAMART_GOLD_WRITE_URL", "I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD"})

# Chave fixa e exclusiva desta ingestão — nunca reaproveitar para outra
# finalidade em outro módulo (em particular, nunca a mesma chave usada por
# pipelines/ingestion/shopee_raw/write_conn.py). Usada com
# pg_try_advisory_lock/pg_advisory_unlock (lock de sessão, não de transação)
# para impedir duas execuções concorrentes de DDL/carga da Gold regional.
ADVISORY_LOCK_KEY = 564738291056

_GOLD_TABLES = ("marketplace_region_daily",)

_CREDENTIAL_IN_MESSAGE_RE = re.compile(r"//[^/\s@]+:[^/\s@]+@")


class SecretLoadError(RuntimeError):
    """Falha ao carregar/validar o arquivo de secret de escrita."""


class WritePreflightBlocked(RuntimeError):
    """Preflight de escrita encontrou uma condição bloqueante — nada foi escrito."""


def sanitize_error_message(exc: BaseException) -> str:
    """Nunca repassa a mensagem NATIVA de erro de conexão do libpq/psycopg2
    sem antes classificar — descoberto em uso real (Gate 6A): uma falha de
    autenticação vem no formato `connection to server at "host" (IP), port
    N failed: FATAL: password authentication failed for user "X"` / `no
    pg_hba.conf entry for host "IP", user "X", database "Y"`, que expõe
    host, IP, porta, usuário e database em texto puro — e NÃO segue o
    formato `scheme://user:pass@host` que a regex abaixo cobre, então não
    era pega antes. Por isso, mensagens de conexão são classificadas em
    categorias fixas e seguras (a mensagem original nunca é ecoada); só
    mensagens SEM nenhum sinal de erro de conexão passam pelo strip de DSN
    como fallback (ex: um erro de SQL comum, sem endereço de servidor)."""
    text = str(exc)
    lowered = text.lower()
    has_ip = bool(re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", text))

    if "password authentication failed" in lowered or "authentication failed" in lowered:
        return f"{type(exc).__name__}: falha de autenticação (usuário/senha incorretos para a URL de escrita)"
    if "pg_hba.conf" in lowered:
        return f"{type(exc).__name__}: conexão recusada por regra de acesso (pg_hba.conf) — rede/host/ssl não permitido"
    if "could not connect" in lowered or "connection refused" in lowered or "timed out" in lowered or "timeout expired" in lowered:
        return f"{type(exc).__name__}: falha ao alcançar o servidor (rede/host/porta indisponível ou timeout)"
    if "server at" in lowered or has_ip:
        # Fallback generico para qualquer outra mensagem nativa do libpq que
        # mencione endereco de servidor — nunca arriscar repassar host/IP
        # nao cobertos por uma categoria especifica acima.
        return f"{type(exc).__name__}: falha de conexão (detalhes de rede/host omitidos por segurança)"
    return _CREDENTIAL_IN_MESSAGE_RE.sub("//<redacted>@", text)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def load_write_secret(env_path: Path, repo_root: Path) -> dict[str, str]:
    """Lê `.env.gold-write.local` (ou caminho equivalente) para um dict
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

    if values["I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD"] != "1":
        raise SecretLoadError("I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD != '1'")

    return values


def validate_write_guardrails(secret: dict[str, str], datamart_read_url: str) -> str:
    """Confere os guardrails que não dependem de conexão de rede. Retorna a
    write_url validada. Nunca loga os valores."""
    write_url = secret.get("DATAMART_GOLD_WRITE_URL", "")
    if not write_url:
        raise SecretLoadError("DATAMART_GOLD_WRITE_URL vazio")
    if datamart_read_url and write_url == datamart_read_url:
        raise SecretLoadError(
            "DATAMART_GOLD_WRITE_URL é idêntica a DATAMART_DATABASE_URL — nunca reutilizar a credencial de leitura "
            "(a leitura é a réplica; escrever na réplica falha no próprio Postgres, mas a checagem existe para "
            "nunca depender só disso)."
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
    hostnames em texto. Faz fallback para porta+database se
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


def run_preflight(write_url: str, expected_read_url: str, expect_table_exists: bool) -> PreflightReport:
    """Preflight somente-SELECT. `expect_table_exists=False` é usado antes
    do DDL (a tabela NÃO pode existir ainda); `expect_table_exists=True`
    é usado antes da carga de dados (a tabela PRECISA existir).

    IMPORTANTE: também confirma que o alvo NÃO é uma réplica em recovery
    (`pg_is_in_recovery() = false`) — é a checagem que primeiro bloqueou o
    Gate 6A nesta rodada, então fica explícita aqui, não só implícita no
    fato de a escrita falhar no servidor.

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
            cur.execute("SELECT pg_is_in_recovery()")
            (in_recovery,) = cur.fetchone()

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
                "SELECT has_schema_privilege(current_user, 'gold', 'CREATE') AS can_create, "
                "has_schema_privilege(current_user, 'gold', 'USAGE') AS can_use"
            )
            can_create_in_gold, can_use_gold = cur.fetchone()

            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'gold' AND table_name = ANY(%s) ORDER BY 1",
                (list(_GOLD_TABLES),),
            )
            existing_gold_objects = [r[0] for r in cur.fetchall()]

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

    if in_recovery:
        report.blocking_reasons.append("pg_is_in_recovery()=true — conexao de escrita aponta para uma replica, nao o primary")
    if rolsuper:
        report.blocking_reasons.append("rolsuper=true")
    if same_physical_cluster is False:
        report.blocking_reasons.append(
            f"conexão de escrita não aponta para o mesmo cluster físico da leitura ({target_check_note})"
        )
    if not can_create_in_gold or not can_use_gold:
        report.blocking_reasons.append("permissão insuficiente no schema gold (CREATE/USAGE)")

    if expect_table_exists:
        missing = sorted(set(_GOLD_TABLES) - set(existing_gold_objects))
        if missing:
            report.blocking_reasons.append(f"tabela(s) gold.* ainda não existem: {missing} — rode o DDL primeiro")
    else:
        if existing_gold_objects:
            report.blocking_reasons.append(f"objeto(s) gold.* já existem: {existing_gold_objects}")

    if is_rds_superuser_member:
        report.warnings.append("role é membro de rds_superuser (autorizado explicitamente, não bloqueante)")
    if ssl_in_use is False:
        report.warnings.append("conexão sem SSL")
    if ssl_in_use is None:
        report.warnings.append("não foi possível determinar uso de SSL (pg_stat_ssl indisponível)")

    report.safe_summary = {
        "pg_is_in_recovery": in_recovery,
        "target_confirmado": same_physical_cluster,
        "target_check_method": target_check_note,
        "rolsuper": rolsuper,
        "rolcreatedb": rolcreatedb,
        "rolcreaterole": rolcreaterole,
        "rolreplication": rolreplication,
        "rolbypassrls": rolbypassrls,
        "membro_rds_superuser": is_rds_superuser_member,
        "can_create_in_gold": can_create_in_gold,
        "can_use_gold": can_use_gold,
        "existing_gold_objects": existing_gold_objects,
        "ssl_in_use": ssl_in_use,
        "server_version": server_version,
    }
    report.ok = not report.blocking_reasons
    return report


# ---------------------------------------------------------------------------
# Relatorio restrito — vocabulario fixo de categorias/booleans, nunca texto
# livre. Usado quando o chamador (ex: relatorio ao usuario) nao pode expor
# nem a mensagem ja sanitizada de sanitize_error_message (que ainda inclui
# uma frase descritiva) — so uma categoria de um conjunto fechado.
# ---------------------------------------------------------------------------

CONNECTION_FAILURE_CATEGORIES = (
    "auth_failed",
    "ssl_required_or_failed",
    "network_unreachable",
    "not_primary",
    "unknown_connection_error",
)


def categorize_connection_failure(message: str) -> str:
    """Classifica uma mensagem de falha (idealmente ja passada por
    sanitize_error_message, mas a funcao so faz *matching* de palavras-chave
    e nunca ecoa a entrada) numa categoria fixa de CONNECTION_FAILURE_CATEGORIES.
    SSL/pg_hba.conf tem precedencia sobre autenticacao porque um bloqueio de
    SSL costuma impedir o servidor de sequer avaliar a senha (a causa mais
    "de baixo nivel" das duas, quando ambas aparecem na mesma mensagem)."""
    lowered = message.lower()
    if "ssl" in lowered or "encryption" in lowered or "pg_hba" in lowered:
        return "ssl_required_or_failed"
    if "autentica" in lowered or "authentication" in lowered:
        return "auth_failed"
    if (
        "rede" in lowered or "timeout" in lowered or "could not connect" in lowered
        or "connection refused" in lowered or "porta indispon" in lowered or "unreachable" in lowered
    ):
        return "network_unreachable"
    return "unknown_connection_error"


def restricted_preflight_summary(report: PreflightReport) -> dict:
    """Reduz um PreflightReport a um vocabulario fixo e seguro: se a conexao
    falhou, so a categoria (nunca a mensagem); se conectou, so 4 booleans.
    Nunca inclui host/IP/porta/usuario/database/versao do servidor/texto
    livre de qualquer tipo."""
    connection_failed = any("falha ao conectar" in r for r in report.blocking_reasons)
    if connection_failed:
        combined_message = " ".join(report.blocking_reasons)
        return {
            "connected": False,
            "failure_category": categorize_connection_failure(combined_message),
        }

    in_recovery = report.safe_summary.get("pg_is_in_recovery")
    if in_recovery is True:
        return {"connected": True, "failure_category": "not_primary", "pg_is_in_recovery": True}

    role_validada = (
        report.safe_summary.get("rolsuper") is False
        and bool(report.safe_summary.get("can_create_in_gold"))
        and bool(report.safe_summary.get("can_use_gold"))
    )
    return {
        "connected": True,
        "failure_category": None,
        "pg_is_in_recovery": in_recovery,
        "cluster_fisico_esperado": report.safe_summary.get("target_confirmado"),
        "role_validada": role_validada,
        "schema_tabela_alvo_ok": report.ok,
    }


def open_write_connection(write_url: str, timeout: int = 15):
    """Conexão de escrita (não readonly) — só usada dentro de ddl.py/o
    futuro loader, sempre com timeouts locais e advisory lock antes de
    qualquer instrução de escrita."""
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
