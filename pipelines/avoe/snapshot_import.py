"""Gate AVH-4A / AVH-4A-R — importador dos snapshots manuais da Avoe.

SEM `--apply`, NAO ESCREVE
--------------------------
O padrao e' dry-run: le' o snapshot, valida manifesto e hashes, monta as linhas
e imprime o relatorio. Nenhuma conexao de escrita e' aberta. Com `--apply` ele
escreve as duas tabelas de snapshot no Neon, dentro de UMA transacao de dados,
sob advisory lock proprio.

APPEND-ONLY
-----------
Nao ha DELETE nem UPDATE nas tabelas de snapshot em nenhum caminho. Reimportar
a mesma captura e' no-op idempotente; importar conteudo de negocio diferente
com o mesmo `captured_at` e' recusado. Um snapshot novo exige `captured_at`
novo e coexiste com os antigos.

IDEMPOTENCIA INDEPENDENTE DO RUN ID (FINDING 1)
-----------------------------------------------
A equivalencia entre o que esta gravado e o que foi lido compara SOMENTE as
colunas de negocio e proveniencia. `import_run_id` e `imported_at` sao
operacionais da execucao e ficam FORA da comparacao: a mesma captura lida numa
segunda execucao, com run id diferente, e' no-op — nao divergencia.

`snapshot_id` E' PROVENIENCIA, NAO OPERACIONAL
----------------------------------------------
Ele entra na comparacao de proposito: um `snapshot_id` diferente sob o mesmo
`captured_at` significa arquivos diferentes carimbados com a mesma captura, e
isso e' conflito, nao repeticao.

AUDITORIA DURAVEL (FINDING 9, alternativa A)
--------------------------------------------
`audit.source_sync_run` e' escrito numa CONEXAO INDEPENDENTE, com commit
proprio: `running` antes da transacao de dados, `success`/`failed` depois. Uma
tentativa revertida deixa rastro, ao contrario do desenho anterior. Se o commit
dos dados ficar INDETERMINADO (excecao no proprio commit), o registro NAO e'
marcado `failed` — permanece `running` com mensagem explicita, porque afirmar
falha seria afirmar mais do que se sabe.

ZERO RETRY
----------
Falha nao e' repetida automaticamente. O operador le' o erro sanitizado, decide
e roda de novo.

CREDENCIAL
----------
`DATABASE_URL` (Neon) e' o unico segredo lido, e somente com `--apply`. Nenhuma
credencial da Avoe e' lida em ponto algum: o snapshot e' arquivo.

Uso:
    python -m pipelines.avoe.snapshot_import --snapshot-dir <DIR>
    python -m pipelines.avoe.snapshot_import --snapshot-dir <DIR> --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from pipelines.avoe.snapshot_contract import (
    SnapshotContractError,
    TARGET_TABLE_CHANNELS,
    TARGET_TABLE_TARGETS,
    ReadResult,
    default_run_id,
    read_snapshot,
    sanitize_run_id,
    select_current_version,
)

# Chave propria, distinta da do PMA (913_120_013) e de qualquer outra frente.
ADVISORY_LOCK_KEY = 913_120_041
LOCK_TIMEOUT = "30s"
STATEMENT_TIMEOUT = "120s"
INSERT_PAGE_SIZE = 500

SYNC_SOURCE_NAME = "avoe_manual_snapshot"
STATUS_VALIDOS = frozenset({"running", "success", "failed"})

# Colunas OPERACIONAIS da execucao. Ficam fora da comparacao de equivalencia.
# `imported_at` nao entra no INSERT (tem DEFAULT NOW()); esta listado para que a
# intencao seja legivel e testavel.
OPERATIONAL_COLUMNS = ("import_run_id", "imported_at")

# Colunas de NEGOCIO + PROVENIENCIA. Definem a identidade do snapshot.
TARGET_BUSINESS_COLUMNS = (
    "source", "captured_at", "ref_month", "brand", "brand_key",
    "target_amount", "currency_code", "currency_status", "currency_warning",
    "source_recorded_at", "source_file", "source_file_hash", "snapshot_id",
)
CHANNEL_BUSINESS_COLUMNS = (
    "source", "captured_at", "ref_month", "brand", "channel", "brand_key",
    "channel_source_label", "reported_amount", "is_proxy",
    "definition_status", "definition_warning", "currency_code",
    "currency_status", "days_covered", "first_business_date",
    "last_business_date", "coverage_status", "source_recorded_at",
    "source_file", "source_file_hash", "snapshot_id",
)

# O que efetivamente vai no INSERT: negocio/proveniencia + run id.
TARGET_INSERT_COLUMNS = TARGET_BUSINESS_COLUMNS + ("import_run_id",)
CHANNEL_INSERT_COLUMNS = CHANNEL_BUSINESS_COLUMNS + ("import_run_id",)


class SnapshotImportError(RuntimeError):
    """Falha de importacao. Mensagem sanitizada, sem credencial nem caminho."""


def _sanitize_erro(exc: BaseException) -> str:
    """Mensagem curta e sem segredo, para auditoria e stderr."""
    texto = f"{type(exc).__name__}: {exc}"
    for proibido in ("postgres://", "postgresql://", "password", "apikey", "eyJ"):
        if proibido in texto:
            texto = f"{type(exc).__name__}: <mensagem suprimida por conter segredo>"
            break
    return texto.replace("\n", " ")[:400]


# --------------------------------------------------------------------------
# Relatorio
# --------------------------------------------------------------------------

def _fmt(valor: Decimal | None) -> str:
    return "indisponivel (NULL)" if valor is None else f"{valor:,.2f}"


def build_report(resultado: ReadResult, aplicado: dict | None) -> str:
    linhas: list[str] = []
    add = linhas.append

    add("=" * 78)
    add("Gate AVH-4A — importacao de snapshot manual da Avoe")
    add("=" * 78)
    add(f"snapshot_id : {resultado.snapshot_id}")
    add(f"captured_at : {resultado.captured_at.isoformat()}")
    add(f"run_id      : {resultado.stats['run_id']}")
    add("")
    add("ARQUIVOS (allowlist, MANIFEST.sha256 e hash por arquivo conferidos)")
    for nome, f in sorted(resultado.files.items()):
        add(f"  {nome:<32} {f.row_count:>6} linhas  sha256={f.sha256[:16]}...")
    add("")

    add(f"A. METAS -> {TARGET_TABLE_TARGETS}")
    add(f"   lidas: {resultado.stats['targets_lidos']}  "
        f"elegiveis: {resultado.stats['targets_elegiveis']}")
    meses = sorted({r["ref_month"].isoformat() for r in resultado.target_rows})
    marcas = sorted({r["brand"] for r in resultado.target_rows})
    add(f"   competencias: {', '.join(meses) or '(nenhuma)'}")
    add(f"   marcas      : {', '.join(marcas) or '(nenhuma)'}")
    total_meta = sum((r["target_amount"] for r in resultado.target_rows), Decimal("0"))
    add(f"   soma das metas: {_fmt(total_meta)}  "
        f"(moeda BRL ASSUMIDA, nao confirmada pela fonte)")
    sem_key = [r["brand"] for r in resultado.target_rows if r["brand_key"] is None]
    if sem_key:
        add(f"   sem brand_key (sem regra de crosswalk): {', '.join(sorted(set(sem_key)))}")
    add("   coluna de realizado: NENHUMA — o campo `faturamento` da origem foi descartado")
    add("")

    add(f"B. CANAIS ADICIONAIS -> {TARGET_TABLE_CHANNELS}")
    add(f"   lidas: {resultado.stats['channels_lidos']}  "
        f"elegiveis (agregadas): {resultado.stats['channels_elegiveis']}")
    canais = sorted({r["channel"] for r in resultado.channel_rows})
    cmeses = sorted({r["ref_month"].isoformat() for r in resultado.channel_rows})
    cmarcas = sorted({r["brand"] for r in resultado.channel_rows})
    add(f"   canais      : {', '.join(canais) or '(nenhum)'}")
    add(f"   competencias: {', '.join(cmeses) or '(nenhuma)'}")
    add(f"   marcas      : {', '.join(cmarcas) or '(nenhuma)'}")
    nulos = sum(1 for r in resultado.channel_rows if r["reported_amount"] is None)
    parciais = sum(1 for r in resultado.channel_rows if r["coverage_status"] == "partial_month")
    add(f"   reported_amount NULL (indisponivel, nunca 0): {nulos}")
    add(f"   competencias com cobertura parcial: {parciais}")
    add("   is_proxy=TRUE  definition_status=unconfirmed  (por CHECK, nao por convencao)")
    add("   NAO e' GMV oficial e nao pode ser somado a TikTok / ML / Shopee")
    add("")

    add("   por canal x competencia:")
    agrupado: dict[tuple[str, str], list[dict]] = {}
    for r in resultado.channel_rows:
        agrupado.setdefault((r["channel"], r["ref_month"].isoformat()), []).append(r)
    for (canal, mes), grupo in sorted(agrupado.items()):
        soma = sum(
            (r["reported_amount"] for r in grupo if r["reported_amount"] is not None),
            Decimal("0"),
        )
        dias = max(r["days_covered"] for r in grupo)
        add(f"     {canal:<16} {mes}  marcas={len(grupo)}  dias_max={dias:>2}  "
            f"informado={_fmt(soma)}")
    add("")

    reais = [r for r in resultado.rejections if r.get("motivo") != "resumo de descartes"]
    resumo = [r for r in resultado.rejections if r.get("motivo") == "resumo de descartes"]
    add(f"REJEICOES: {len(reais)}")
    for r in reais[:20]:
        det = " ".join(f"{k}={v}" for k, v in r.items() if k != "dataset")
        add(f"  [{r['dataset']}] {det}")
    if len(reais) > 20:
        add(f"  ... e mais {len(reais) - 20}")
    for r in resumo:
        add(f"  [channels] oficiais ignorados={r['canais_oficiais_ignorados']}  "
            f"fora do regime diario={r['linhas_fora_do_regime_diario']}")
    add("")

    add("VERSAO CORRENTE (unidade = CAPTURA, por (source, ref_month); nunca imported_at)")
    correntes_t = select_current_version(resultado.target_rows)
    correntes_c = select_current_version(resultado.channel_rows)
    add(f"  metas correntes neste snapshot : {len(correntes_t)}")
    add(f"  canais correntes neste snapshot: {len(correntes_c)}")
    add("")

    add("ACOES")
    if aplicado is None:
        add(f"  seriam inseridas {len(resultado.target_rows)} linhas em {TARGET_TABLE_TARGETS}")
        add(f"  seriam inseridas {len(resultado.channel_rows)} linhas em {TARGET_TABLE_CHANNELS}")
        add("  nenhum UPDATE, nenhum DELETE (tabelas append-only)")
        add("  ESCRITA: nenhuma (sem --apply). Zero linha gravada.")
    else:
        add(f"  inseridas em {TARGET_TABLE_TARGETS}: {aplicado['targets_inserted']}")
        add(f"  inseridas em {TARGET_TABLE_CHANNELS}: {aplicado['channels_inserted']}")
        if aplicado.get("no_op"):
            add("  NO-OP idempotente: captura ja presente com o mesmo conteudo de negocio.")
        add(f"  sync_run_id: {aplicado.get('sync_run_id')}")
    add("=" * 78)
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Auditoria — conexao INDEPENDENTE, commit proprio (FINDING 9 / alternativa A)
# --------------------------------------------------------------------------

def audit_start(audit_conn, rows_extracted: int) -> int:
    cur = audit_conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO audit.source_sync_run
                (source_name, marketplace_id, loja_id, status, started_at, rows_extracted)
            VALUES (%s, NULL, NULL, 'running', NOW(), %s)
            RETURNING sync_run_id
            """,
            (SYNC_SOURCE_NAME, rows_extracted),
        )
        sync_run_id = cur.fetchone()["sync_run_id"]
        audit_conn.commit()
        return sync_run_id
    except Exception:
        audit_conn.rollback()
        raise
    finally:
        cur.close()


def audit_finish(audit_conn, sync_run_id: int, status: str,
                 rows_loaded: int | None = None,
                 error_message: str | None = None) -> None:
    """Fecha o registro. Exige `rowcount == 1` e status validado."""
    if status not in STATUS_VALIDOS:
        raise SnapshotImportError(f"status de auditoria invalido: {status!r}.")
    cur = audit_conn.cursor()
    try:
        cur.execute(
            """
            UPDATE audit.source_sync_run
               SET status = %s, finished_at = NOW(),
                   rows_loaded = %s, error_message = %s
             WHERE sync_run_id = %s
            """,
            (status, rows_loaded, error_message, sync_run_id),
        )
        if cur.rowcount != 1:
            audit_conn.rollback()
            raise SnapshotImportError(
                f"UPDATE de auditoria afetou {cur.rowcount} linhas, esperava 1."
            )
        audit_conn.commit()
    except SnapshotImportError:
        raise
    except Exception:
        audit_conn.rollback()
        raise
    finally:
        cur.close()


def audit_mark_indeterminate(audit_conn, sync_run_id: int, detalhe: str) -> None:
    """Commit de dados indeterminado: NAO marca failed.

    Mantem `running` e grava a mensagem. Afirmar `failed` seria afirmar que
    nada foi gravado, e isso nao se sabe.
    """
    cur = audit_conn.cursor()
    try:
        cur.execute(
            """
            UPDATE audit.source_sync_run
               SET error_message = %s
             WHERE sync_run_id = %s
            """,
            (f"INDETERMINADO: {detalhe}", sync_run_id),
        )
        if cur.rowcount != 1:
            audit_conn.rollback()
            raise SnapshotImportError(
                f"UPDATE de auditoria afetou {cur.rowcount} linhas, esperava 1."
            )
        audit_conn.commit()
    except SnapshotImportError:
        raise
    except Exception:
        audit_conn.rollback()
        raise
    finally:
        cur.close()


# --------------------------------------------------------------------------
# Escrita dos dados — somente com --apply
# --------------------------------------------------------------------------

def _get_neon_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SnapshotImportError(
            "DATABASE_URL nao definido: o destino dos snapshots e' o Neon."
        )
    return url


def _existing_business_rows(cur, tabela: str, colunas: tuple[str, ...], captured_at) -> set[tuple]:
    """Le SOMENTE as colunas de negocio/proveniencia da captura."""
    cur.execute(
        f"SELECT {', '.join(colunas)} FROM {tabela} "
        f"WHERE source = %s AND captured_at = %s",
        ("avoe_hub", captured_at),
    )
    return {tuple(r[c] for c in colunas) for r in cur.fetchall()}


def publish(neon_conn, resultado: ReadResult, execute_values=None) -> dict:
    """UMA transacao de dados: lock -> idempotencia -> INSERT -> verificacao.

    `execute_values` e' injetavel para permitir contraprova sem psycopg2
    instalado. Em producao ele e' importado tarde, DENTRO do try, para que uma
    falha de import tambem passe pelo rollback.
    """
    saida = {"targets_inserted": 0, "channels_inserted": 0, "no_op": False,
             "sync_run_id": None, "checks": {}}
    cur = neon_conn.cursor()
    try:
        if execute_values is None:
            from psycopg2.extras import execute_values  # noqa: PLC0415 — import tardio

        cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))

        planejado = {
            TARGET_TABLE_TARGETS: (
                TARGET_BUSINESS_COLUMNS, TARGET_INSERT_COLUMNS,
                resultado.target_rows, "targets_inserted",
            ),
            TARGET_TABLE_CHANNELS: (
                CHANNEL_BUSINESS_COLUMNS, CHANNEL_INSERT_COLUMNS,
                resultado.channel_rows, "channels_inserted",
            ),
        }

        # FINDING 1 — equivalencia por colunas de NEGOCIO, sem import_run_id.
        ja_presente = 0
        for tabela, (bcols, _icols, linhas, _) in planejado.items():
            existentes = _existing_business_rows(cur, tabela, bcols, resultado.captured_at)
            if not existentes:
                continue
            ja_presente += 1
            novos = {tuple(r[c] for c in bcols) for r in linhas}
            if existentes != novos:
                raise SnapshotImportError(
                    f"{tabela}: captured_at ja presente com conteudo de negocio "
                    f"DIVERGENTE ({len(existentes)} linhas no destino contra "
                    f"{len(linhas)} na leitura). Tabela append-only: nada sera "
                    f"sobrescrito. Reimportar exige uma captura nova."
                )
        if ja_presente == len(planejado):
            saida["no_op"] = True
            neon_conn.commit()
            return saida

        for tabela, (_bcols, icols, linhas, chave_saida) in planejado.items():
            if not linhas:
                continue
            execute_values(
                cur,
                f"INSERT INTO {tabela} ({', '.join(icols)}) VALUES %s",
                [tuple(r[c] for c in icols) for r in linhas],
                page_size=INSERT_PAGE_SIZE,
            )
            saida[chave_saida] = cur.rowcount

            cur.execute(
                f"SELECT count(*) AS n FROM {tabela} "
                f"WHERE source = %s AND captured_at = %s",
                ("avoe_hub", resultado.captured_at),
            )
            gravadas = cur.fetchone()["n"]
            if gravadas != len(linhas):
                raise SnapshotImportError(
                    f"{tabela}: {gravadas} linhas no destino para a captura "
                    f"contra {len(linhas)} lidas."
                )
            saida["checks"][tabela] = gravadas

        neon_conn.commit()
        return saida
    except Exception:
        neon_conn.rollback()
        raise
    finally:
        cur.close()


def _neon_writable(url: str):
    import psycopg2  # noqa: PLC0415 — import tardio, so' com --apply
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    return psycopg2.connect(url, cursor_factory=RealDictCursor)


def apply_with_audit(neon_conn, audit_conn, resultado: ReadResult,
                     execute_values=None) -> dict:
    """Orquestra auditoria duravel + transacao de dados."""
    total = len(resultado.target_rows) + len(resultado.channel_rows)
    sync_run_id = audit_start(audit_conn, total)
    try:
        aplicado = publish(neon_conn, resultado, execute_values=execute_values)
    except Exception as exc:
        audit_finish(audit_conn, sync_run_id, "failed",
                     rows_loaded=0, error_message=_sanitize_erro(exc))
        raise
    aplicado["sync_run_id"] = sync_run_id
    carregadas = aplicado["targets_inserted"] + aplicado["channels_inserted"]
    nota = "no-op idempotente: captura ja presente" if aplicado["no_op"] else None
    audit_finish(audit_conn, sync_run_id, "success",
                 rows_loaded=carregadas, error_message=nota)
    return aplicado


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipelines.avoe.snapshot_import",
        description=(
            "Importa snapshot manual da Avoe (metas mensais e faturamento "
            "informado de canais adicionais). Dry-run por padrao."
        ),
    )
    p.add_argument("--snapshot-dir", required=True, metavar="DIR",
                   help="Diretorio do snapshot, com MANIFEST.json e os JSONL.")
    p.add_argument("--run-id", default=None,
                   help="Identificador da execucao. Gerado se omitido.")
    p.add_argument("--apply", action="store_true",
                   help="Escreve no Neon. Sem esta flag, nada e' gravado.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = sanitize_run_id(args.run_id) if args.run_id else default_run_id()

    try:
        resultado = read_snapshot(Path(args.snapshot_dir), run_id=run_id)
    except SnapshotContractError as exc:
        print(f"FALHA DE CONTRATO: {exc}", file=sys.stderr)
        return 2

    aplicado = None
    if args.apply:
        try:
            url = _get_neon_url()
            neon_conn = _neon_writable(url)
            audit_conn = _neon_writable(url)
        except SnapshotImportError as exc:
            print(f"FALHA: {exc}", file=sys.stderr)
            return 3
        except Exception as exc:  # pragma: no cover — depende de ambiente
            print(f"FALHA ao conectar no destino: {_sanitize_erro(exc)}", file=sys.stderr)
            return 3
        try:
            aplicado = apply_with_audit(neon_conn, audit_conn, resultado)
        except SnapshotImportError as exc:
            print(f"FALHA NA PUBLICACAO (rollback aplicado): {exc}", file=sys.stderr)
            return 4
        except Exception as exc:
            print(f"FALHA NA PUBLICACAO (rollback aplicado): {_sanitize_erro(exc)}",
                  file=sys.stderr)
            return 4
        finally:
            neon_conn.close()
            audit_conn.close()

    print(build_report(resultado, aplicado))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
