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
from pipelines.ops import region_sync_consent  # noqa: E402

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
    """Verifica o consentimento exigido por pipelines.sync_region_daily.run_sync
    antes de disparar o sync — nunca abre conexao aqui (RDS/Neon ja sao
    cobertos por check_rds/check_neon, registrados junto com esta mesma
    fonte em SOURCE_CHECKS). Isso garante BLOCKED explicito ANTES do wrapper
    sync_region_if_needed sequer tentar diagnosticar, em vez de deixar
    run_sync levantar RuntimeError no meio da execucao se o sync acabar
    sendo necessario.

    Gate B6.1b: alem da variavel de ambiente (setada manualmente para uma
    unica invocacao, como em todos os Gates B2-B5), tambem aceita o
    consentimento persistente e gitignored de
    `region_sync_consent.DEFAULT_REGION_SYNC_CONSENT_PATH`
    (`.env.region-sync.local`) — necessario para a execucao AGENDADA (Task
    Scheduler, processo novo, sem a sessao interativa de quem seta a
    variavel manualmente). `ensure_region_sync_consent()` prioriza a
    variavel de ambiente ja definida e nunca imprime o conteudo do arquivo,
    so' o nome (fixo, sem informacao sensivel)."""
    label = "Sync regional (consentimento)"
    already_set = os.environ.get("I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY") == "1"
    if not region_sync_consent.ensure_region_sync_consent():
        return CheckResult(
            label, False,
            f"{label}: I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY != '1' (nem por variavel de ambiente, nem por "
            f"{region_sync_consent.DEFAULT_REGION_SYNC_CONSENT_PATH.name}) — sync nao sera' disparado, mesmo que necessario",
        )
    source = "variavel de ambiente" if already_set else region_sync_consent.DEFAULT_REGION_SYNC_CONSENT_PATH.name
    return CheckResult(label, True, f"{label}: OK (via {source})")


#: Tabelas de serving que `/operacoes` le em producao, criadas pelas migrations
#: 006/007/008. Nomes FIXOS, vindos dos proprios modulos de sync (nunca uma
#: quarta copia da lista) — ver o teste que trava essa identidade.
_SERVING_TARGET_TABLES = (
    "marts.fact_ml_gestao_diaria",
    "marts.fact_tiktok_brand_content_daily",
    "marts.fact_tiktok_creator_daily",
)


def check_serving_tables() -> CheckResult:
    """Confirma que as migrations do serving foram aplicadas, ANTES de disparar
    um sync que falharia no meio.

    Checa a EXISTENCIA das tres tabelas (`to_regclass`, uma consulta so', sem
    ler linha nenhuma) em vez de comparar `alembic_version` com o literal
    '008'. Os dois provariam a mesma coisa hoje, mas a versao travada no
    literal passaria a bloquear todo o serving no dia em que uma migration 009
    de qualquer outro assunto entrasse — a pre-condicao real do sync e' a
    tabela existir, nao o numero da revisao. A revisao corrente e' reportada no
    detalhe, para diagnostico, sem virar critério de aprovacao.

    Somente leitura: `to_regclass` e `alembic_version` nao escrevem nada.
    """
    label = "Serving (migrations 006/007/008)"
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return CheckResult(label, False, f"{label}: variavel de conexao nao configurada")
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        try:
            conn.set_session(readonly=True)
            cur = conn.cursor()
            faltando = []
            for tabela in _SERVING_TARGET_TABLES:
                cur.execute("SELECT to_regclass(%s)", (tabela,))
                if cur.fetchone()[0] is None:
                    faltando.append(tabela)
            revisao = "?"
            try:
                cur.execute("SELECT version_num FROM alembic_version")
                linha = cur.fetchone()
                if linha:
                    revisao = str(linha[0])
            except Exception:
                # Ausencia/erro de leitura do controle de versao nao invalida o
                # que importa: a existencia das tabelas, ja verificada acima.
                revisao = "indisponivel"
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        return CheckResult(label, False, f"{label}: falha de conexao ({sanitize_url(url)}) — {type(e).__name__}")

    if faltando:
        nomes = ", ".join(t.split(".")[-1] for t in faltando)
        return CheckResult(
            label, False,
            f"{label}: {len(faltando)} tabela(s) ausente(s) ({nomes}) — migration nao aplicada; "
            f"revisao corrente do Alembic: {revisao}",
        )
    return CheckResult(
        label, True,
        f"{label}: as 3 tabelas existem (revisao corrente do Alembic: {revisao})",
    )


#: Tabelas de serving criadas pelo Gate S3 (migrations 009 e 010). Separadas das
#: do S2 porque as duas fontes sao SNAPSHOT sem janela e tem sync proprio
#: (pipelines/sync_serving_snapshots.py), nao o wrapper do O1.
_SERVING_S3_TARGET_TABLES = (
    "marts.fact_ml_cross_company_summary",
    "marts.fact_tiktok_channel_efficiency_daily",
)


def check_serving_s3_tables() -> CheckResult:
    """Confirma que as migrations 009/010 foram aplicadas, ANTES de disparar um
    snapshot que falharia no meio.

    Mesmo desenho de `check_serving_tables`: `to_regclass`, uma consulta so', sem
    ler linha nenhuma, e a revisao corrente do Alembic apenas no detalhe — nunca
    como critério. Travar no literal '011' bloquearia todo o serving no dia em que
    uma migration 012 de outro assunto entrasse; a pre-condicao real do sync e' a
    tabela existir.
    """
    label = "Serving S3 (migrations 009/010)"
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return CheckResult(label, False, f"{label}: variavel de conexao nao configurada")
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        try:
            conn.set_session(readonly=True)
            cur = conn.cursor()
            faltando = []
            for tabela in _SERVING_S3_TARGET_TABLES:
                cur.execute("SELECT to_regclass(%s)", (tabela,))
                if cur.fetchone()[0] is None:
                    faltando.append(tabela)
            revisao = "?"
            try:
                cur.execute("SELECT version_num FROM alembic_version")
                linha = cur.fetchone()
                if linha:
                    revisao = str(linha[0])
            except Exception:
                revisao = "indisponivel"
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        return CheckResult(label, False,
                           f"{label}: falha de conexao ({sanitize_url(url)}) — {type(e).__name__}")

    if faltando:
        nomes = ", ".join(t.split(".")[-1] for t in faltando)
        return CheckResult(
            label, False,
            f"{label}: {len(faltando)} tabela(s) ausente(s) ({nomes}) — migration nao "
            f"aplicada; revisao corrente do Alembic: {revisao}",
        )
    return CheckResult(label, True,
                       f"{label}: as 2 tabelas existem (revisao corrente do Alembic: {revisao})")


def check_tiktok_product_content_columns() -> CheckResult:
    """Confirma que a migration 011 adicionou `active_videos` e `video_views` a
    `marts.fact_tiktok_product_daily`.

    Sem elas o sync TikTok falharia no INSERT, e `/brand-detail` nao poderia
    montar `top_produtos` pelo Neon. Le somente `information_schema`.
    """
    label = "Produto TikTok (migration 011)"
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return CheckResult(label, False, f"{label}: variavel de conexao nao configurada")
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        try:
            conn.set_session(readonly=True)
            cur = conn.cursor()
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema = 'marts' AND table_name = 'fact_tiktok_product_daily'
                   AND column_name IN ('active_videos', 'video_views')
            """)
            presentes = {r[0] for r in cur.fetchall()}
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        return CheckResult(label, False,
                           f"{label}: falha de conexao ({sanitize_url(url)}) — {type(e).__name__}")

    faltando = {"active_videos", "video_views"} - presentes
    if faltando:
        return CheckResult(label, False,
                           f"{label}: coluna(s) ausente(s) ({', '.join(sorted(faltando))}) — "
                           f"migration 011 nao aplicada")
    return CheckResult(label, True, f"{label}: as 2 colunas existem")


# Fontes suportadas e suas dependencias. produtos_shopee depende do
# PostgreSQL local (populado manualmente por apps/api/etl/load_shopee_products.py
# a partir dos XLSX — esse passo NAO faz parte desta automacao, ver runbook),
# nao dos arquivos XLSX diretamente nem do RDS.
#: Relacoes que o sync de afiliados exige — destino, estado e auditoria (Neon).
_AFFILIATE_NEON_RELATIONS = (
    "marts.fact_tiktok_affiliate_cost_order_monthly",
    "marts.fact_tiktok_affiliate_cost_order_monthly_sync_state",
    "audit.source_sync_run",
)
_AFFILIATE_SOURCE_RELATION = "silver.stg_tiktok_payments_by_order"


def check_affiliate_cost_relations() -> CheckResult:
    """Existencia das relacoes do sync de afiliados, nos DOIS bancos.

    `to_regclass` nao le linha nenhuma. Confere o destino e o `sync_state` da
    migration 012 e a `audit.source_sync_run` da migration 003 — sem esta
    ultima o sync abortaria ao abrir a auditoria, depois de o step ja ter
    comecado. Do lado da fonte, so' a existencia da relacao.
    """
    label = "Afiliados TikTok (migrations 012/003 + fonte)"
    neon_url = os.environ.get("DATABASE_URL", "")
    dm_url = os.environ.get("DATAMART_DATABASE_URL", "")
    if not neon_url or not dm_url:
        return CheckResult(label, False, f"{label}: variavel de conexao nao configurada")

    faltando: list[str] = []
    try:
        conn = psycopg2.connect(neon_url, connect_timeout=5)
        try:
            conn.set_session(readonly=True)
            cur = conn.cursor()
            for rel in _AFFILIATE_NEON_RELATIONS:
                cur.execute("SELECT to_regclass(%s)", (rel,))
                if cur.fetchone()[0] is None:
                    faltando.append(rel)
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        return CheckResult(label, False,
                           f"{label}: falha de conexao no Neon "
                           f"({sanitize_url(neon_url)}) — {type(e).__name__}")

    try:
        conn = psycopg2.connect(dm_url, connect_timeout=5)
        try:
            conn.set_session(readonly=True)
            cur = conn.cursor()
            cur.execute("SELECT to_regclass(%s)", (_AFFILIATE_SOURCE_RELATION,))
            if cur.fetchone()[0] is None:
                faltando.append(_AFFILIATE_SOURCE_RELATION)
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        return CheckResult(label, False,
                           f"{label}: falha de conexao no Data Mart "
                           f"({sanitize_url(dm_url)}) — {type(e).__name__}")

    if faltando:
        nomes = ", ".join(faltando)
        return CheckResult(label, False,
                           f"{label}: {len(faltando)} relacao(oes) ausente(s) ({nomes})")
    return CheckResult(label, True,
                       f"{label}: {len(_AFFILIATE_NEON_RELATIONS)} relacoes no Neon "
                       "e a fonte no Data Mart existem")


def check_affiliate_cost_source_not_empty() -> CheckResult:
    """Fonte tem ao menos uma linha com `updated_at`, sem varrer 2,1 milhoes.

    Duas provas BARATAS, ambas com `LIMIT 1` — nenhum `COUNT(*)` integral, que
    seria scan completo a cada execucao do `full_daily`. As validacoes profundas
    (grao, tipos, NaN, reconciliacao) continuam dentro do proprio sync.

    Fonte vazia BLOQUEIA de proposito: no caminho automatizado, um `full` sobre
    fonte vazia esvaziaria a fact. Se a fonte realmente deve estar vazia, isso e'
    decisao operacional explicita, com `--mode full --apply` manual.
    """
    label = "Afiliados TikTok (fonte nao vazia)"
    url = os.environ.get("DATAMART_DATABASE_URL", "")
    if not url:
        return CheckResult(label, False, f"{label}: variavel de conexao nao configurada")
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        try:
            conn.set_session(readonly=True)
            cur = conn.cursor()
            cur.execute(
                f"SELECT 1 FROM {_AFFILIATE_SOURCE_RELATION} LIMIT 1"  # noqa: S608
            )
            tem_linha = cur.fetchone() is not None
            cur.execute(
                f"SELECT 1 FROM {_AFFILIATE_SOURCE_RELATION} "  # noqa: S608
                "WHERE updated_at IS NOT NULL LIMIT 1"
            )
            tem_updated = cur.fetchone() is not None
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        return CheckResult(label, False,
                           f"{label}: falha de conexao ({sanitize_url(url)}) — "
                           f"{type(e).__name__}")

    if not tem_linha:
        return CheckResult(label, False,
                           f"{label}: fonte VAZIA — bloqueado. O caminho "
                           "automatizado nunca esvazia a fact sozinho; confirme "
                           "operacionalmente e rode --mode full --apply manual")
    if not tem_updated:
        return CheckResult(label, False,
                           f"{label}: nenhuma linha com `updated_at` — o "
                           "incremental nao teria watermark utilizavel")
    return CheckResult(label, True, f"{label}: fonte tem linhas com `updated_at`")


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
    # Checkpoint O1 Task 2/2 (2026-08-17): os tres steps de serving. Data Mart
    # (fonte Gold) e Neon (destino) sao AMBOS obrigatorios — sem um dos dois nao
    # ha' o que ler nem onde escrever — e as tabelas de destino precisam existir
    # antes de o wrapper sequer resolver a janela. Uma fonte por target, mesmo
    # que os checks sejam identicos: e' o que faz `serving_ml` poder passar
    # enquanto `serving_tiktok_creator` bloqueia, e vice-versa.
    "serving_ml": (check_rds, check_neon, check_serving_tables),
    "serving_tiktok_brand": (check_rds, check_neon, check_serving_tables),
    "serving_tiktok_creator": (check_rds, check_neon, check_serving_tables),
    # Gate S3 (2026-08-18): os dois snapshots sem janela. Data Mart (fonte) e Neon
    # (destino) obrigatorios, mais a existencia das tabelas que as migrations
    # 009/010 criam. Uma fonte por target, mesmo com checks parcialmente iguais:
    # e' o que permite um passar enquanto o outro bloqueia.
    "serving_ml_cross_company": (check_rds, check_neon, check_serving_s3_tables),
    "serving_tiktok_channel_efficiency": (check_rds, check_neon, check_serving_s3_tables),
    # `sync_produtos_tiktok` passa a escrever as duas colunas da migration 011;
    # sem elas o INSERT falharia no meio da carga.
    "produtos_tiktok_s3": (check_rds, check_neon, check_tiktok_product_content_columns),
    # Gate UE2-C Task 2/3: custo de afiliado do TikTok por coorte de pedido.
    # Data Mart (fonte, via VPN) e Neon (destino + auditoria) sao ambos
    # obrigatorios; mais a existencia das relacoes das migrations 012 e 003 e
    # uma prova BARATA de que a fonte nao esta vazia.
    "tiktok_affiliate_cost_order_monthly": (
        check_rds, check_neon, check_affiliate_cost_relations,
        check_affiliate_cost_source_not_empty,
    ),
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
