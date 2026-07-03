"""
Gate 4B do Bug 8 (marts.fact_shopee_product_monthly) — swap da TABELA REAL
no Neon, a partir do backup e da staging ja criados e auditados no Gate
4A.2. NAO cria nenhum objeto novo — so' consome os tres objetos fixos
abaixo, todos ja existentes no Neon:

    backup:  marts.fact_shopee_product_monthly_backup_bug8_neon_20260702_232445
    staging: marts.fact_shopee_product_monthly_staging_bug8_neon_20260702_232445
    real:    marts.fact_shopee_product_monthly

Este e' o unico script desta serie autorizado a esvaziar a tabela real do
Neon (via TRUNCATE) — e faz isso SOMENTE dentro de uma unica transacao que
adquire ACCESS EXCLUSIVE LOCK primeiro, revalida tudo sob esse lock, e so'
commita se toda a validacao pos-INSERT passar. Nenhuma outra operacao que
remova ou sobrescreva linhas existentes (fora o proprio TRUNCATE
autorizado da tabela real) e' emitida em nenhum lugar deste arquivo. Nunca
referencia DATAMART_DATABASE_URL. Nunca apaga backup/staging.

Guardas obrigatorias e simultaneas (run_swap_neon):
  - flag --swap-neon explicita;
  - variavel de ambiente I_UNDERSTAND_THIS_REPLACES_NEON_DATA=1;
  - DATABASE_URL explicita (sem fallback), sanitizada em qualquer log;
  - nomes de backup/staging FIXOS (constantes do modulo — nunca aceitos
    via argumento nem descobertos dinamicamente);
  - diagnostico limpo (Neon real vs. backup LOCAL pre-fix) recalculado
    imediatamente antes de qualquer conexao de escrita.

Preflight, TUDO executado SOB o ACCESS EXCLUSIVE LOCK (nao antes dele),
antes de qualquer TRUNCATE/INSERT:
  - os 3 objetos existem;
  - a tabela real ainda e' identica ao backup NEON (agregados + EXCEPT
    bidirecional) — repete essa comparacao especificamente sob o lock,
    fechando a janela de corrida entre a autorizacao e a execucao;
  - a staging Neon tem exatamente 2.471 linhas, GMV 21.174.272,80 e
    53.599 cancelados, sem duplicatas nem nulos obrigatorios;
  - nao existe nenhuma foreign key de outra tabela apontando para
    marts.fact_shopee_product_monthly — aborta se existir (TRUNCATE sem
    CASCADE falharia de qualquer forma, mas preferimos uma mensagem clara
    a um erro cru do Postgres).

So' depois de TODO o preflight passar: TRUNCATE (sem CASCADE, sem RESTART
IDENTITY) exclusivamente da tabela real, INSERT com as 13 colunas de
negocio explicitas a partir da staging, EXCEPT bidirecional real vs.
staging pos-INSERT, validacao de agregados, e COMMIT. Qualquer excecao em
qualquer etapa aciona ROLLBACK antes de propagar.

Uso:
    python -m pipelines.reconciliation.swap_bug8_neon --swap-neon
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from pipelines.reconciliation.diagnose_bug8_neon import (  # noqa: E402
    BUSINESS_COLUMNS,
    EXPECTED_STAGING_CANCELED,
    EXPECTED_STAGING_GMV,
    EXPECTED_STAGING_ROWS,
    REAL_TABLE,
    _aggregates_from_table,
    _duplicates_and_nulls,
    _except_both_directions,
    _get_neon_url,
    _neon_writable,
    _run_diagnose_from_env,
    _sanitize_url,
    _table_exists,
)

# Nomes FIXOS dos objetos ja criados e auditados no Gate 4A.2 — nunca
# aceitos via argumento de linha de comando, nunca descobertos via
# information_schema por padrao/LIKE. Se esses objetos forem recriados
# com outro timestamp no futuro, estas constantes precisam ser atualizadas
# manualmente e revisadas — nao ha "auto-discovery" por design.
BACKUP_NEON_NAME = "fact_shopee_product_monthly_backup_bug8_neon_20260702_232445"
STAGING_NEON_NAME = "fact_shopee_product_monthly_staging_bug8_neon_20260702_232445"


class SwapPreflightError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Foreign keys apontando para a tabela real
# ---------------------------------------------------------------------------
def _foreign_keys_referencing(conn, table: str) -> list[str]:
    """Lista 'schema.tabela.coluna -> marts.table' para qualquer FK de
    OUTRA tabela que referencie marts.{table}. Lista vazia = seguro para
    truncar sem CASCADE."""
    cur = conn.cursor()
    cur.execute("""
        SELECT tc.table_schema AS ref_schema, tc.table_name AS ref_table,
               kcu.column_name AS ref_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
         AND tc.constraint_schema = ccu.constraint_schema
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.constraint_schema = kcu.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND ccu.table_schema = 'marts' AND ccu.table_name = %s
    """, (table,))
    rows = cur.fetchall()
    cur.close()
    return [f"{r['ref_schema']}.{r['ref_table']}.{r['ref_column']} -> marts.{table}" for r in rows]


# ---------------------------------------------------------------------------
# Gate 4B — swap real no Neon (implementado, mas so' executado sob as
# guardas de run_swap_neon; nao chamado com conexoes reais nesta sessao)
# ---------------------------------------------------------------------------
def do_swap_neon(neon_conn) -> dict:
    """UNICA transacao: timeouts locais -> ACCESS EXCLUSIVE LOCK ->
    preflight completo sob o lock (existencia, real==backup Neon, staging
    valida, sem FK de terceiros) -> TRUNCATE so' da tabela real (sem
    CASCADE/RESTART IDENTITY) -> INSERT com 13 colunas explicitas a partir
    da staging -> EXCEPT bidirecional real vs. staging -> validacao de
    agregados -> COMMIT. Qualquer excecao (incluindo timeout ao adquirir o
    lock) aciona ROLLBACK antes de propagar — nunca ha nova tentativa
    automatica. Nunca apaga backup/staging; a unica instrucao que remove/
    sobrescreve linhas existentes em qualquer lugar deste modulo e' o
    proprio TRUNCATE autorizado da tabela real, abaixo."""
    cur = neon_conn.cursor()
    try:
        # Timeouts locais a esta transacao (SET LOCAL nunca vaza para fora
        # dela), definidos ANTES do lock: se o ACCESS EXCLUSIVE LOCK nao
        # for adquirido em 10s (outra sessao segurando um lock conflitante),
        # o proprio Postgres cancela o LOCK TABLE com erro — cai no except
        # abaixo, faz ROLLBACK e propaga, sem TRUNCATE/INSERT jamais serem
        # alcancados e sem qualquer retry automatico. statement_timeout de
        # 60s e' uma rede de seguranca para qualquer instrucao individual
        # (inclusive as de reconciliacao) ficar presa.
        cur.execute("SET LOCAL lock_timeout = '10s'")
        cur.execute("SET LOCAL statement_timeout = '60s'")

        cur.execute(f"LOCK TABLE marts.{REAL_TABLE} IN ACCESS EXCLUSIVE MODE")

        # --- preflight completo, SOB o lock, antes de qualquer escrita ---
        for name in (REAL_TABLE, BACKUP_NEON_NAME, STAGING_NEON_NAME):
            if not _table_exists(cur, name):
                raise SwapPreflightError(f"objeto nao encontrado: marts.{name}")

        real_agg = _aggregates_from_table(neon_conn, REAL_TABLE)
        backup_agg = _aggregates_from_table(neon_conn, BACKUP_NEON_NAME)
        if real_agg != backup_agg:
            raise SwapPreflightError(f"tabela real diverge do backup Neon sob o lock: real={real_agg} backup={backup_agg}")
        real_not_backup, backup_not_real = _except_both_directions(neon_conn, REAL_TABLE, BACKUP_NEON_NAME)
        if real_not_backup or backup_not_real:
            raise SwapPreflightError(
                f"tabela real diverge do backup Neon sob o lock (EXCEPT nao-zero: "
                f"real_not_backup={real_not_backup} backup_not_real={backup_not_real})"
            )

        staging_agg = _aggregates_from_table(neon_conn, STAGING_NEON_NAME)
        expected = {"n": EXPECTED_STAGING_ROWS, "gmv": EXPECTED_STAGING_GMV, "canceled_orders": EXPECTED_STAGING_CANCELED}
        for key, exp in expected.items():
            if staging_agg[key] != exp:
                raise SwapPreflightError(f"staging Neon nao confere com os numeros esperados em {key}: {staging_agg[key]} != {exp}")
        dupes, nulls = _duplicates_and_nulls(neon_conn, STAGING_NEON_NAME)
        if dupes:
            raise SwapPreflightError(f"staging Neon com {dupes} chaves duplicadas")
        if nulls:
            raise SwapPreflightError(f"staging Neon com {nulls} linhas com nulos obrigatorios")

        fks = _foreign_keys_referencing(neon_conn, REAL_TABLE)
        if fks:
            raise SwapPreflightError(
                f"existe(m) {len(fks)} foreign key(s) de outra(s) tabela(s) apontando para "
                f"marts.{REAL_TABLE} — abortando (TRUNCATE sem CASCADE falharia ou um CASCADE "
                f"apagaria dados de terceiros): {fks}"
            )

        # --- swap propriamente dito ---
        cur.execute(f"TRUNCATE TABLE marts.{REAL_TABLE}")

        cur.execute(f"""
            INSERT INTO marts.{REAL_TABLE} ({', '.join(BUSINESS_COLUMNS)})
            SELECT {', '.join(BUSINESS_COLUMNS)} FROM marts.{STAGING_NEON_NAME}
        """)

        real_not_staging, staging_not_real = _except_both_directions(neon_conn, REAL_TABLE, STAGING_NEON_NAME)
        if real_not_staging or staging_not_real:
            raise SwapPreflightError(
                f"tabela real diverge da staging Neon apos o INSERT (EXCEPT nao-zero: "
                f"real_not_staging={real_not_staging} staging_not_real={staging_not_real})"
            )

        real_after_agg = _aggregates_from_table(neon_conn, REAL_TABLE)
        if real_after_agg != staging_agg:
            raise SwapPreflightError(f"agregados divergem apos o INSERT: real={real_after_agg} staging={staging_agg}")

        neon_conn.commit()
        cur.close()
        return {
            "backup_table": BACKUP_NEON_NAME, "staging_table": STAGING_NEON_NAME,
            "real_agg_before": real_agg, "real_agg_after": real_after_agg,
        }
    except Exception:
        neon_conn.rollback()
        cur.close()
        raise


# ---------------------------------------------------------------------------
# --swap-neon — orquestracao gated
# ---------------------------------------------------------------------------
def run_swap_neon(args, diagnose_fn=None, connect_fn=None) -> dict:
    """Guardas obrigatorias e simultaneas: flag --swap-neon, variavel de
    ambiente I_UNDERSTAND_THIS_REPLACES_NEON_DATA=1, DATABASE_URL
    explicita, e diagnostico limpo (Neon real vs. backup LOCAL pre-fix)
    recalculado IMEDIATAMENTE antes de abrir qualquer conexao de escrita.
    So' depois disso delega a do_swap_neon."""
    if not getattr(args, "swap_neon", False):
        raise RuntimeError("Gate 4B requer a flag --swap-neon explicita")
    if os.environ.get("I_UNDERSTAND_THIS_REPLACES_NEON_DATA") != "1":
        raise RuntimeError(
            "Gate 4B requer a variavel de ambiente "
            "I_UNDERSTAND_THIS_REPLACES_NEON_DATA=1 explicitamente definida"
        )

    neon_url = _get_neon_url()
    print(f"Neon (swap): {_sanitize_url(neon_url)}")
    print(f"Backup Neon (fixo): marts.{BACKUP_NEON_NAME}")
    print(f"Staging Neon (fixa): marts.{STAGING_NEON_NAME}")

    diagnose_fn = diagnose_fn or _run_diagnose_from_env
    report = diagnose_fn()
    if report["problems"]:
        raise RuntimeError(
            f"diagnostico encontrou {len(report['problems'])} problema(s) — Gate 4B recusado: "
            + "; ".join(report["problems"])
        )
    print("Diagnostico limpo, executado agora — prosseguindo com o swap da tabela real no Neon.")

    neon_conn = connect_fn() if connect_fn is not None else _neon_writable(neon_url)
    try:
        result = do_swap_neon(neon_conn)
    finally:
        neon_conn.close()

    print(f"SWAP CONCLUIDO: tabela real agora com {result['real_agg_after']['n']} linhas, "
          f"gmv={result['real_agg_after']['gmv']}, canceled_orders={result['real_agg_after']['canceled_orders']}")
    print(f"Backup preservado: marts.{BACKUP_NEON_NAME}")
    print(f"Staging preservada: marts.{STAGING_NEON_NAME}")
    return result


def _sanitize_error_message(exc: Exception) -> str:
    """Nunca confia cegamente em str(exc): remove qualquer trecho no
    formato usuario:senha@ (caso uma excecao de baixo nivel do driver
    algum dia inclua a DSN na mensagem) antes de imprimir. Isso cobre
    tambem erros de timeout (lock_timeout/statement_timeout), que por si
    so' ja nao trazem credenciais, mas nao dependemos so' disso."""
    import re as _re
    return _re.sub(r"//[^/\s@]+:[^/\s@]+@", "//<redacted>@", str(exc))


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[2] / ".env"))

    parser = argparse.ArgumentParser(description="Gate 4B — swap da tabela real no Neon")
    parser.add_argument(
        "--swap-neon", action="store_true", dest="swap_neon",
        help="Substitui marts.fact_shopee_product_monthly no Neon pela staging ja auditada no Gate 4A.2 — exige guardas explicitas",
    )
    args = parser.parse_args()

    try:
        run_swap_neon(args)
    except RuntimeError as e:
        print(f"!!! RECUSADO/ABORTADO: {e}")
        return 1
    except Exception as e:
        # Qualquer falha nao prevista (ex.: timeout ao adquirir o lock,
        # erro de conexao) ja foi revertida dentro de do_swap_neon — aqui
        # so' reportamos de forma sanitizada, sem tentar de novo.
        print(f"!!! ABORTADO ({type(e).__name__}, transacao revertida, sem nova tentativa): {_sanitize_error_message(e)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
