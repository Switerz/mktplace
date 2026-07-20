"""
Conexão de escrita dedicada ao Gate S3 — refresh/restore da Gold regional
Shopee POR JANELA (`--refresh-shopee-window`/`--restore-shopee-window` em
`loader.py`).

Este módulo é o boundary de segurança para o PRIMEIRO caminho de escrita da
Gold regional que faz `DELETE` (todos os outros — `execute_first_load`,
`execute_incremental_load`, DDL — só fazem `INSERT`/DDL). Por isso o secret
é DELIBERADAMENTE separado do usado por `--incremental`
(`.env.gold-write.local`, que autoriza só `INSERT`): reaproveitar aquele
secret esconderia, atrás de uma flag de consentimento já "gasta", que este
caminho novo pode apagar linhas.

Secret dedicado: `.env.gold-window-write.local` (nunca `.env.gold-write.local`,
nunca o `.env` principal, nunca `os.environ`). Exatamente duas chaves:
  - `DATAMART_GOLD_WINDOW_WRITE_URL`
  - `I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW` (deve ser `"1"`)

Guardrails aplicados aqui (mesmo padrão já auditado em
`pipelines/ingestion/gold_regional/write_conn.py`, reimplementado — não
importado — porque o secret/preflight é de um caminho de escrita distinto,
com privilégio mínimo diferente):
  - o arquivo de secret precisa existir, estar coberto por uma regra do
    .gitignore e não estar rastreado pelo git;
  - só as duas chaves esperadas são aceitas;
  - a URL de escrita nunca pode ser igual à URL de leitura
    (`DATAMART_DATABASE_URL`);
  - o preflight (somente leitura) confirma, TODOS bloqueantes — "não foi
    possível confirmar" (`None`) NUNCA equivale a aprovado: conexão
    autenticada; `pg_is_in_recovery()=false`; `system_identifier` disponível
    nos dois lados e EXATAMENTE igual (nunca cai para database+porta como
    substituto, ao contrário do preflight genérico do `--incremental`);
    `rolsuper`/`rolcreatedb`/`rolcreaterole`/`rolreplication`/`rolbypassrls`
    todos `false`; membro de `rds_superuser` CONFIRMADO `false`; SSL
    CONFIRMADO em uso; `gold.marketplace_region_daily` e sua sequence
    existem; `USAGE` em `silver`/`gold`; `SELECT` em
    `silver.stg_shopee_order_item_snapshots`; `SELECT`/`INSERT`/`DELETE` em
    `gold.marketplace_region_daily`; `USAGE` na sequence do `id`; `TEMP` no
    database. Também bloqueia se a credencial TIVER privilégios proibidos
    (least-privilege violado): `CREATE` no schema `gold`, `UPDATE` ou
    `TRUNCATE` em `gold.marketplace_region_daily`. Nunca exige nem concede
    nada em tabelas ML/TikTok;
  - nenhuma mensagem de erro pode conter usuário/senha — todo texto de
    exceção passa por `sanitize_error_message` (reaproveitado de
    `write_conn.py` — mesmo texto/categorias, sem duplicar a lógica).

Este módulo NUNCA cria/altera role, secret ou arquivo `.env.gold-window-write.local`
real — só lê e valida o que já existir em disco.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg2
from dotenv import dotenv_values

from pipelines.ingestion.gold_regional import write_conn
from pipelines.ingestion.gold_regional.write_conn import sanitize_error_message  # re-exportado

EXPECTED_SECRET_KEYS = frozenset({
    "DATAMART_GOLD_WINDOW_WRITE_URL",
    "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW",
})

_GOLD_TABLE_SCHEMA = "gold"
_GOLD_TABLE_NAME = "marketplace_region_daily"
_GOLD_TABLE_QUALIFIED = f"{_GOLD_TABLE_SCHEMA}.{_GOLD_TABLE_NAME}"
_SILVER_SOURCE_TABLE_QUALIFIED = "silver.stg_shopee_order_item_snapshots"
_GOLD_ID_SEQUENCE_QUALIFIED = f"{_GOLD_TABLE_SCHEMA}.{_GOLD_TABLE_NAME}_id_seq"


class WindowSecretLoadError(RuntimeError):
    """Falha ao carregar/validar `.env.gold-window-write.local`. Mensagem
    NUNCA contém valores do arquivo."""


class WindowWritePreflightBlocked(RuntimeError):
    """Preflight do refresh/restore de janela encontrou uma condição
    bloqueante — nada foi escrito."""


def load_window_write_secret(env_path: Path, repo_root: Path) -> dict[str, str]:
    """Lê `.env.gold-window-write.local` para um dict local, sem tocar em
    `os.environ`. Um `.env.gold-write.local` (secret do `--incremental`)
    NUNCA passa aqui: suas chaves (`DATAMART_GOLD_WRITE_URL`/
    `I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD`) são diferentes das duas
    exigidas por `EXPECTED_SECRET_KEYS`, então seria rejeitado pela
    checagem de chaves exatas abaixo mesmo se apontado por engano para
    este caminho."""
    if not env_path.is_file():
        raise WindowSecretLoadError(f"arquivo de secret não encontrado: {env_path.name}")

    ignore_check = write_conn._run_git(["check-ignore", "-q", str(env_path)], cwd=repo_root)
    if ignore_check.returncode != 0:
        raise WindowSecretLoadError(f"{env_path.name} não está coberto por nenhuma regra do .gitignore — bloqueado")

    tracked_check = write_conn._run_git(["ls-files", "--error-unmatch", str(env_path)], cwd=repo_root)
    if tracked_check.returncode == 0:
        raise WindowSecretLoadError(f"{env_path.name} está RASTREADO pelo git — bloqueado")

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
        raise WindowSecretLoadError(
            f"{env_path.name} não contém exatamente as 2 chaves esperadas ({'; '.join(parts)})"
        )

    values = {k: raw_values[k] for k in EXPECTED_SECRET_KEYS}

    if values["I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW"] != "1":
        raise WindowSecretLoadError("I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW != '1'")

    return values


def validate_window_write_guardrails(secret: dict[str, str], datamart_read_url: str) -> str:
    """Confere os guardrails que não dependem de conexão de rede. Retorna a
    write_url validada. Nunca loga os valores."""
    write_url = secret.get("DATAMART_GOLD_WINDOW_WRITE_URL", "")
    if not write_url:
        raise WindowSecretLoadError("DATAMART_GOLD_WINDOW_WRITE_URL vazio")
    if datamart_read_url and write_url == datamart_read_url:
        raise WindowSecretLoadError(
            "DATAMART_GOLD_WINDOW_WRITE_URL é idêntica a DATAMART_DATABASE_URL — nunca reutilizar a "
            "credencial de leitura para escrita."
        )
    return write_url


@dataclass
class WindowPreflightReport:
    ok: bool = False
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Resumo seguro para exibição — nunca inclui host/IP/usuário/senha.
    safe_summary: dict = field(default_factory=dict)


def run_window_preflight(write_url: str, expected_read_url: str) -> WindowPreflightReport:
    """Preflight somente-SELECT para o caminho de refresh/restore de janela
    — o PRIMEIRO caminho de escrita da Gold regional com DELETE. Gate S3.1
    (revisão de segurança): least-privilege de verdade, com uma regra
    central — **"não foi possível confirmar" NUNCA equivale a "aprovado"**.
    Qualquer checagem que volte `None` (desconhecida/inconclusiva) BLOQUEIA,
    exatamente como uma checagem que volte um valor explicitamente ruim.

    Obrigatório e bloqueante: `DATAMART_DATABASE_URL` configurado;
    `pg_is_in_recovery()=false`; `system_identifier` disponível nos DOIS
    lados e EXATAMENTE igual (nunca cai para comparação por database+porta
    — esse fallback é aceitável no preflight genérico do `--incremental`,
    mas não aqui, num caminho com DELETE); `rolsuper`/`rolcreatedb`/
    `rolcreaterole`/`rolreplication`/`rolbypassrls` todos `false`; membro de
    `rds_superuser` CONFIRMADO `false` (não apenas "papel não existe, deve
    ser não-RDS" tratado como aprovação silenciosa — só a mensagem nativa
    "does not exist" é aceita como confirmação válida de não-membro; QUALQUER
    outra falha na consulta bloqueia); SSL CONFIRMADO em uso; tabela E
    sequence existentes; `USAGE` em `silver`/`gold`; `SELECT` na Silver;
    `SELECT`/`INSERT`/`DELETE` na Gold; `USAGE` na sequence; `TEMP` no
    database.

    Privilégios PROIBIDOS, também bloqueantes (se a credencial os tiver, é
    sinal de que não foi escopada como least-privilege e não deve ser usada
    até ser corrigida): `CREATE` no schema `gold`; `UPDATE`/`TRUNCATE` em
    `gold.marketplace_region_daily`. Nunca exige nem concede nada em
    tabelas ML/TikTok.

    Nunca escreve nada — mesmo em caso de bug, a sessão está em
    `readonly=True` desde a conexão (reaproveita `write_conn._connect_readonly`/
    `write_conn._fetch_target_identity`)."""
    report = WindowPreflightReport()

    same_physical_cluster = None
    target_check_note = None

    try:
        write_identity = write_conn._fetch_target_identity(write_url)
    except Exception as exc:  # noqa: BLE001
        report.blocking_reasons.append(f"falha ao conectar: {sanitize_error_message(exc)}")
        return report

    if not expected_read_url:
        report.blocking_reasons.append(
            "DATAMART_DATABASE_URL não configurado -- impossível confirmar que o alvo de escrita é o "
            "mesmo cluster físico da leitura; bloqueando por segurança (nunca aprovar sem essa confirmação)"
        )
    else:
        try:
            read_identity = write_conn._fetch_target_identity(expected_read_url)
        except Exception as exc:  # noqa: BLE001
            report.blocking_reasons.append(f"falha ao conectar: {sanitize_error_message(exc)}")
            return report

        if write_identity["sysid"] is None or read_identity["sysid"] is None:
            report.blocking_reasons.append(
                "system_identifier indisponível em um dos lados -- não é possível confirmar o mesmo "
                "cluster físico (database+porta NUNCA é usado como substituto para autorizar um caminho com DELETE)"
            )
        else:
            same_physical_cluster = write_identity["sysid"] == read_identity["sysid"]
            target_check_note = "comparado via system_identifier (pg_control_system)"
            if not same_physical_cluster:
                report.blocking_reasons.append(
                    f"conexão de escrita não aponta para o mesmo cluster físico da leitura ({target_check_note})"
                )

    try:
        conn = write_conn._connect_readonly(write_url)
    except Exception as exc:  # noqa: BLE001
        report.blocking_reasons.append(f"falha ao conectar: {sanitize_error_message(exc)}")
        return report

    # Gate S3.2 (Finding 1): TODOS os campos inicializados com o valor
    # inconclusivo (None) ANTES de qualquer consulta. Se qualquer consulta
    # falhar no meio, nenhuma variável fica indefinida — os campos ainda
    # não coletados permanecem None, e "None bloqueia" (checagens abaixo)
    # garante que o report resultante nunca aprova. A função SEMPRE retorna
    # um WindowPreflightReport — nenhuma exceção de consulta escapa para o
    # chamador (a falha vira blocking_reason sanitizado).
    in_recovery = None
    rolsuper = rolcreatedb = rolcreaterole = rolreplication = rolbypassrls = None
    gold_table_exists = None
    gold_sequence_exists = None
    silver_usage = gold_usage = gold_create = can_temp = None
    can_select_silver_source = None
    can_select_gold = can_insert_gold = can_delete_gold = None
    can_update_gold = can_truncate_gold = None
    can_use_id_sequence = None
    is_rds_superuser_member = None
    ssl_in_use = None
    server_version = None

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_is_in_recovery()")
            (in_recovery,) = cur.fetchone()

            cur.execute("SELECT current_user")
            cur.fetchone()

            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls = cur.fetchone()

            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s)",
                (_GOLD_TABLE_SCHEMA, _GOLD_TABLE_NAME),
            )
            (gold_table_exists,) = cur.fetchone()

            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.sequences "
                "WHERE sequence_schema = %s AND sequence_name = %s)",
                (_GOLD_TABLE_SCHEMA, f"{_GOLD_TABLE_NAME}_id_seq"),
            )
            (gold_sequence_exists,) = cur.fetchone()

            cur.execute(
                "SELECT has_schema_privilege(current_user, 'silver', 'USAGE') AS silver_usage, "
                "has_schema_privilege(current_user, 'gold', 'USAGE') AS gold_usage, "
                "has_schema_privilege(current_user, 'gold', 'CREATE') AS gold_create, "
                "has_database_privilege(current_user, current_database(), 'TEMP') AS can_temp"
            )
            silver_usage, gold_usage, gold_create, can_temp = cur.fetchone()

            if gold_table_exists:
                cur.execute(
                    "SELECT has_table_privilege(current_user, %s, 'SELECT')",
                    (_SILVER_SOURCE_TABLE_QUALIFIED,),
                )
                (can_select_silver_source,) = cur.fetchone()

                cur.execute(
                    "SELECT has_table_privilege(current_user, %s, 'SELECT'), "
                    "has_table_privilege(current_user, %s, 'INSERT'), "
                    "has_table_privilege(current_user, %s, 'DELETE'), "
                    "has_table_privilege(current_user, %s, 'UPDATE'), "
                    "has_table_privilege(current_user, %s, 'TRUNCATE')",
                    (_GOLD_TABLE_QUALIFIED,) * 5,
                )
                can_select_gold, can_insert_gold, can_delete_gold, can_update_gold, can_truncate_gold = cur.fetchone()
            else:
                try:
                    cur.execute("SELECT has_table_privilege(current_user, %s, 'SELECT')", (_SILVER_SOURCE_TABLE_QUALIFIED,))
                    (can_select_silver_source,) = cur.fetchone()
                except Exception:  # noqa: BLE001
                    can_select_silver_source = None

            if gold_sequence_exists:
                try:
                    cur.execute(
                        "SELECT has_sequence_privilege(current_user, %s, 'USAGE')",
                        (_GOLD_ID_SEQUENCE_QUALIFIED,),
                    )
                    (can_use_id_sequence,) = cur.fetchone()
                except Exception:  # noqa: BLE001
                    can_use_id_sequence = None

            try:
                cur.execute("SELECT pg_has_role(current_user, 'rds_superuser', 'MEMBER')")
                (is_rds_superuser_member,) = cur.fetchone()
            except Exception as exc:  # noqa: BLE001
                # Só a mensagem nativa "does not exist" é aceita como
                # confirmação válida de que o papel rds_superuser não existe
                # neste Postgres (não é RDS) -- QUALQUER outra falha aqui é
                # tratada como desconhecida (None), que bloqueia abaixo.
                is_rds_superuser_member = False if "does not exist" in str(exc).lower() else None

            try:
                cur.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
                row = cur.fetchone()
                ssl_in_use = bool(row[0]) if row else None
            except Exception:  # noqa: BLE001
                ssl_in_use = None

            cur.execute("SELECT current_setting('server_version')")
            (server_version,) = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        # Finding 1 (Gate S3.2): uma falha INESPERADA em qualquer consulta
        # (role, privilégio, tabela, sequence...) nunca escapa como
        # traceback/mensagem nativa — vira blocking_reason sanitizado, e os
        # campos ainda não coletados (None) reforçam o bloqueio abaixo.
        report.blocking_reasons.append(
            f"falha ao executar as consultas do preflight: {sanitize_error_message(exc)}"
        )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass  # best-effort: conexão readonly; nada a preservar aqui

    # --- checagens obrigatórias e bloqueantes ------------------------------
    # Finding 2 (Gate S3.2): "inconclusivo bloqueia" vale LITERALMENTE.
    # Toda condição usa comparação de identidade explícita (`is True`/`is
    # not False`/`is not True`) — nunca truthiness (`if valor`), que
    # aprovaria None silenciosamente nos privilégios proibidos e trataria
    # 1/0/strings como booleanos. Só o valor esperado EXATO aprova.
    if in_recovery is True:
        report.blocking_reasons.append("pg_is_in_recovery()=true — conexão de escrita aponta para uma réplica, não o primary")
    elif in_recovery is not False:
        report.blocking_reasons.append("pg_is_in_recovery() não confirmado como false -- inconclusivo bloqueia")

    for attr_name, attr_value in (
        ("rolsuper", rolsuper), ("rolcreatedb", rolcreatedb), ("rolcreaterole", rolcreaterole),
        ("rolreplication", rolreplication), ("rolbypassrls", rolbypassrls),
    ):
        if attr_value is True:
            report.blocking_reasons.append(f"{attr_name}=true")
        elif attr_value is not False:
            report.blocking_reasons.append(f"{attr_name} não confirmado como false -- inconclusivo bloqueia")

    if is_rds_superuser_member is not False:
        report.blocking_reasons.append(
            "membro de rds_superuser não confirmado como false -- este caminho tem DELETE; "
            "\"não foi possível confirmar\" nunca equivale a aprovado"
        )
    if ssl_in_use is not True:
        report.blocking_reasons.append("SSL não confirmado como ativo -- obrigatório para este caminho")
    if gold_table_exists is not True:
        report.blocking_reasons.append(f"tabela {_GOLD_TABLE_QUALIFIED} não existe ou não confirmada — rode o DDL/primeira carga antes")
    if gold_sequence_exists is not True:
        report.blocking_reasons.append(f"sequence {_GOLD_ID_SEQUENCE_QUALIFIED} não existe ou não confirmada")
    if silver_usage is not True:
        report.blocking_reasons.append("permissão insuficiente/não confirmada: falta USAGE no schema silver")
    if gold_usage is not True:
        report.blocking_reasons.append("permissão insuficiente/não confirmada: falta USAGE no schema gold")
    if can_temp is not True:
        report.blocking_reasons.append("permissão insuficiente/não confirmada: falta TEMP no database (necessário para a staging da carga por janela)")
    if gold_table_exists is True:
        if can_select_silver_source is not True:
            report.blocking_reasons.append(f"permissão insuficiente/não confirmada: falta SELECT em {_SILVER_SOURCE_TABLE_QUALIFIED}")
        if can_select_gold is not True:
            report.blocking_reasons.append(f"permissão insuficiente/não confirmada: falta SELECT em {_GOLD_TABLE_QUALIFIED}")
        if can_insert_gold is not True:
            report.blocking_reasons.append(f"permissão insuficiente/não confirmada: falta INSERT em {_GOLD_TABLE_QUALIFIED}")
        if can_delete_gold is not True:
            report.blocking_reasons.append(f"permissão insuficiente/não confirmada: falta DELETE em {_GOLD_TABLE_QUALIFIED}")
        if can_update_gold is True:
            report.blocking_reasons.append(f"privilégio proibido: UPDATE concedido em {_GOLD_TABLE_QUALIFIED} (least-privilege violado)")
        elif can_update_gold is not False:
            report.blocking_reasons.append(f"privilégio UPDATE em {_GOLD_TABLE_QUALIFIED} não confirmado como ausente -- inconclusivo bloqueia")
        if can_truncate_gold is True:
            report.blocking_reasons.append(f"privilégio proibido: TRUNCATE concedido em {_GOLD_TABLE_QUALIFIED} (least-privilege violado)")
        elif can_truncate_gold is not False:
            report.blocking_reasons.append(f"privilégio TRUNCATE em {_GOLD_TABLE_QUALIFIED} não confirmado como ausente -- inconclusivo bloqueia")
    if gold_create is True:
        report.blocking_reasons.append("privilégio proibido: CREATE concedido no schema gold (least-privilege violado)")
    elif gold_create is not False:
        report.blocking_reasons.append("privilégio CREATE no schema gold não confirmado como ausente -- inconclusivo bloqueia")
    if gold_sequence_exists is True and can_use_id_sequence is not True:
        report.blocking_reasons.append(f"permissão insuficiente/não confirmada: falta USAGE na sequence {_GOLD_ID_SEQUENCE_QUALIFIED}")

    report.safe_summary = {
        "pg_is_in_recovery": in_recovery,
        "target_confirmado": same_physical_cluster,
        "target_check_method": target_check_note,
        "rolsuper": rolsuper,
        "rolcreatedb": rolcreatedb,
        "rolcreaterole": rolcreaterole,
        "rolreplication": rolreplication,
        "rolbypassrls": rolbypassrls,
        "gold_table_exists": gold_table_exists,
        "gold_sequence_exists": gold_sequence_exists,
        "silver_usage": silver_usage,
        "gold_usage": gold_usage,
        "gold_create": gold_create,
        "can_temp": can_temp,
        "can_select_silver_source": can_select_silver_source,
        "can_select_gold": can_select_gold,
        "can_insert_gold": can_insert_gold,
        "can_delete_gold": can_delete_gold,
        "can_update_gold": can_update_gold,
        "can_truncate_gold": can_truncate_gold,
        "can_use_id_sequence": can_use_id_sequence,
        "membro_rds_superuser": is_rds_superuser_member,
        "ssl_in_use": ssl_in_use,
        "server_version": server_version,
    }
    report.ok = not report.blocking_reasons
    return report
