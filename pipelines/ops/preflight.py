"""
Preflight de dependencias — SOMENTE LEITURA (`SELECT 1`). Verifica, antes de
disparar uma carga, se as pre-condicoes daquela fonte estao disponiveis:
RDS/Data Mart (TikTok/ML), PostgreSQL local (Produtos Shopee), arquivos
XLSX locais (Shopee), Neon (destino de todas). Nunca escreve em nada, nunca
executa ETL/sync/backfill/migration.

Um wrapper de agendamento deve chamar isto ANTES do comando real e, se o
preflight falhar, reportar BLOCKED e abortar sem sequer chamar o script da
carga — logo, nada e' registrado em audit.source_sync_run (BLOCKED
significa "nunca tentamos", distinto de "failed", que significa "tentamos
e deu erro").

Uso:
    python -m pipelines.ops.preflight --source tiktok_daily
    python -m pipelines.ops.preflight --source produtos_shopee
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from pipelines.connectors.shopee.connector import BRANDS_IN_SCOPE  # noqa: E402
from pipelines.ingestion.gold_regional import write_conn as gold_write_conn  # noqa: E402

_ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_GOLD_REGIONAL_WRITE_SECRET_PATH = REPO_ROOT / ".env.gold-write.local"


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def sanitize_url(url: str) -> str:
    """host:porta/database — nunca usuario/senha. Usar SEMPRE em vez da URL
    bruta em qualquer print/log/mensagem de erro."""
    if not url:
        return "(nao configurado)"
    p = urlsplit(url)
    host = p.hostname or "?"
    port = p.port if p.port is not None else "?"
    db = p.path.lstrip("/") or "?"
    return f"{host}:{port}/{db}"


def _select_1(url: str, label: str, timeout: int = 5) -> CheckResult:
    """Abre a conexao com a sessao explicitamente somente leitura — defesa
    em profundidade: mesmo sendo so' um diagnostico com `SELECT 1`, uma
    conexao de preflight nunca deve ser capaz de escrever nada no servidor,
    nem por engano num refactor futuro deste modulo."""
    if not url:
        return CheckResult(label, False, f"{label}: variavel de conexao nao configurada")
    try:
        conn = psycopg2.connect(url, connect_timeout=timeout)
        try:
            conn.set_session(readonly=True)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
        finally:
            conn.close()
        return CheckResult(label, True, f"{label}: conectividade OK ({sanitize_url(url)})")
    except Exception as e:
        return CheckResult(label, False, f"{label}: falha de conexao ({sanitize_url(url)}) — {type(e).__name__}")


def check_rds() -> CheckResult:
    return _select_1(os.environ.get("DATAMART_DATABASE_URL", ""), "RDS/Data Mart")


def check_neon() -> CheckResult:
    return _select_1(os.environ.get("DATABASE_URL", ""), "Neon")


def check_local_pg() -> CheckResult:
    """LOCAL_PG_URL e' exigida explicitamente, sem fallback com credencial
    hardcoded, e o host e' restrito ao allowlist local — mesmo padrao de
    `apps/api/etl/load_shopee_products.py._get_local_pg_url()`. Um preflight
    que "funciona" contra um banco diferente do pretendido (fallback
    silencioso ou host remoto) e' pior do que um preflight que bloqueia."""
    url = os.environ.get("LOCAL_PG_URL", "")
    if not url:
        return CheckResult("PostgreSQL local", False, "PostgreSQL local: LOCAL_PG_URL nao configurado (sem fallback)")
    host = (urlsplit(url).hostname or "").lower()
    if host not in _ALLOWED_LOCAL_HOSTS:
        return CheckResult(
            "PostgreSQL local", False,
            f"PostgreSQL local: host nao permitido ({sanitize_url(url)}) — so' localhost/127.0.0.1/::1 sao aceitos",
        )
    return _select_1(url, "PostgreSQL local")


# Um arquivo por marca por fonte — orders/stats/ads sao exportacoes
# DIFERENTES (nomes de arquivo diferentes), nao a mesma pasta checada 3x.
# Ver pipelines/connectors/shopee/_parser.py, _parser_shop_stats.py,
# _parser_ads.py (fonte de verdade destes globs).
_SHOPEE_FILE_PATTERNS = {
    "shopee": ("Arquivos Shopee (orders)", "Order.all*.xlsx"),
    "shopee-stats": ("Arquivos Shopee (stats)", "*.shopee-shop-stats.*.xlsx"),
    "shopee-ads": ("Arquivos Shopee (ads)", "Dados*.csv"),
}


def _check_shopee_pattern(label: str, glob_pattern: str) -> CheckResult:
    """Nunca imprime o valor de SHOPEE_DATA_PATH (pode revelar estrutura de
    diretorio/usuario da maquina) — so' o nome das marcas OFICIAIS
    (`BRANDS_IN_SCOPE`, a mesma lista usada pelo conector real — nunca uma
    whitelist duplicada aqui) que estao FALTANDO o arquivo esperado.

    Decisao documentada: se QUALQUER marca oficial estiver sem o arquivo
    esperado, a fonte inteira e' BLOQUEADA (nao so' um aviso). Motivo: uma
    carga parcial (algumas marcas com dado, outras nao) registraria
    `audit.source_sync_run` como "success" sem sinalizar que faltou dado de
    marcas especificas — bloquear forca um humano a investigar antes de a
    carga rodar incompleta silenciosamente."""
    data_path = os.environ.get("SHOPEE_DATA_PATH", "")
    if not data_path:
        return CheckResult(label, False, f"{label}: SHOPEE_DATA_PATH nao configurado")
    p = Path(data_path)
    if not p.is_dir():
        return CheckResult(label, False, f"{label}: diretorio configurado em SHOPEE_DATA_PATH nao encontrado")

    missing = sorted(brand for brand in BRANDS_IN_SCOPE if not any((p / brand).glob(glob_pattern)))
    if missing:
        return CheckResult(label, False, f"{label}: marca(s) sem arquivo esperado: {', '.join(missing)}")
    return CheckResult(label, True, f"{label}: arquivo esperado presente para todas as {len(BRANDS_IN_SCOPE)} marca(s)")


def check_shopee_orders_files() -> CheckResult:
    label, pattern = _SHOPEE_FILE_PATTERNS["shopee"]
    return _check_shopee_pattern(label, pattern)


def check_shopee_stats_files() -> CheckResult:
    label, pattern = _SHOPEE_FILE_PATTERNS["shopee-stats"]
    return _check_shopee_pattern(label, pattern)


def check_shopee_ads_files() -> CheckResult:
    label, pattern = _SHOPEE_FILE_PATTERNS["shopee-ads"]
    return _check_shopee_pattern(label, pattern)


def check_gold_regional_write() -> CheckResult:
    """Gate B2: read-only — nunca abre uma conexao de ESCRITA aqui. Confirma
    em sequencia (1) que `.env.gold-write.local` existe/esta' gitignored/nao
    rastreado e tem exatamente as 2 chaves esperadas (`load_write_secret`),
    (2) que a write_url nao e' identica a DATAMART_DATABASE_URL
    (`validate_write_guardrails`), (3) o preflight somente-leitura do proprio
    pacote gold_regional (`write_conn.run_preflight`, sessao
    readonly=True desde a conexao) aprova o alvo: nao esta' em recovery, nao
    e' rolsuper, mesmo cluster fisico da leitura, permissao no schema gold, e
    `gold.marketplace_region_daily` ja existe. Nunca imprime o conteudo do
    secret nem qualquer host/URL — so' as mensagens ja saneadas de
    SecretLoadError/PreflightReport.blocking_reasons (nenhuma delas ecoa a
    DSN, ver write_conn.sanitize_error_message)."""
    label = "Gold regional (escrita)"
    try:
        secret = gold_write_conn.load_write_secret(_GOLD_REGIONAL_WRITE_SECRET_PATH, REPO_ROOT)
    except gold_write_conn.SecretLoadError as exc:
        return CheckResult(label, False, f"{label}: {exc}")

    datamart_read_url = os.environ.get("DATAMART_DATABASE_URL", "")
    try:
        write_url = gold_write_conn.validate_write_guardrails(secret, datamart_read_url)
    except gold_write_conn.SecretLoadError as exc:
        return CheckResult(label, False, f"{label}: {exc}")

    report = gold_write_conn.run_preflight(write_url, datamart_read_url, expect_table_exists=True)
    if not report.ok:
        return CheckResult(label, False, f"{label}: preflight bloqueado — {'; '.join(report.blocking_reasons)}")
    return CheckResult(label, True, f"{label}: secret valido, preflight de escrita OK")


def check_sync_region_consent() -> CheckResult:
    """So' verifica a variavel de ambiente de consentimento exigida por
    pipelines.sync_region_daily.run_sync antes de disparar o sync — nunca
    abre conexao aqui (RDS/Neon ja sao cobertos por check_rds/check_neon,
    registrados junto com esta mesma fonte em SOURCE_CHECKS). Isso garante
    BLOCKED explicito ANTES do wrapper sync_region_if_needed sequer tentar
    diagnosticar, em vez de deixar run_sync levantar RuntimeError no meio da
    execucao se o sync acabar sendo necessario."""
    label = "Sync regional (consentimento)"
    if os.environ.get("I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY") != "1":
        return CheckResult(
            label, False,
            f"{label}: I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY != '1' — sync nao sera' disparado, mesmo que necessario",
        )
    return CheckResult(label, True, f"{label}: OK")


# Fontes suportadas e suas dependencias. produtos_shopee depende do
# PostgreSQL local (populado manualmente por apps/api/etl/load_shopee_products.py
# a partir dos XLSX — esse passo NAO faz parte desta automacao, ver runbook),
# nao dos arquivos XLSX diretamente nem do RDS.
SOURCE_CHECKS = {
    "tiktok_daily": (check_rds, check_neon),
    "ml_daily": (check_rds, check_neon),
    "shopee_daily": (check_shopee_orders_files, check_neon),
    "shopee-stats_daily": (check_shopee_stats_files, check_neon),
    "shopee-ads_daily": (check_shopee_ads_files, check_neon),
    "produtos_tiktok": (check_rds, check_neon),
    "produtos_ml": (check_rds, check_neon),
    "produtos_shopee": (check_local_pg, check_neon),
    # Gate B2 (2026-07-15): regional (Gold incremental + sync Neon
    # condicional) — ambos CRITICOS em orchestrate.py, sem gap manual
    # conhecido aceito (diferente de produtos_shopee).
    "gold_regional_incremental": (check_gold_regional_write, check_rds),
    "sync_region_daily": (check_sync_region_consent, check_rds, check_neon),
}


def run_preflight(source: str) -> tuple[bool, list[CheckResult]]:
    checks = SOURCE_CHECKS.get(source)
    if checks is None:
        raise ValueError(f"fonte desconhecida: {source!r}. Opcoes: {sorted(SOURCE_CHECKS)}")
    results = [check() for check in checks]
    return all(r.ok for r in results), results


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(description="Preflight read-only de dependencias de uma fonte de carga")
    parser.add_argument("--source", required=True, choices=sorted(SOURCE_CHECKS))
    args = parser.parse_args()

    ok, results = run_preflight(args.source)
    for r in results:
        print(f"[{'OK' if r.ok else 'BLOCKED'}] {r.detail}")

    if ok:
        print(f"\nSTATUS=OK fonte={args.source} — seguro prosseguir com a carga.")
        return 0
    print(f"\nSTATUS=BLOCKED fonte={args.source} — carga NAO deve ser disparada.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
