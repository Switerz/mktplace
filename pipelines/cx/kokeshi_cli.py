"""CX-KOKESHI-BOT — grava no Neon o JSON que o bot monta no Seller Centre.

    python -m pipelines.cx.kokeshi_cli --arquivo kokeshi_cx.json          # previa
    python -m pipelines.cx.kokeshi_cli --arquivo kokeshi_cx.json --apply  # grava

Mesmo banco e mesmo perfil do export de pedidos (`DATABASE_URL`, `.env` da
raiz). Uma transacao por arquivo: ou tudo entra, ou nada. Contrato do JSON e
rotina do bot: `docs/cx_kokeshi_bot_runbook.md`.

Saidas (as mesmas da Expedicao):
  0 ok · 2 outra carga em andamento · 3 JSON recusado · 6 precondicao
  (migration 036 ausente, conta nao cadastrada) · 1 falha de banco.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from pipelines.cx import kokeshi as K

EXIT_OK = 0
EXIT_FALHA = 1
EXIT_LOCK_OCUPADO = 2
EXIT_RECUSADO = 3
EXIT_PRECONDICAO = 6

REPO_ROOT = Path(__file__).resolve().parents[2]

SAUDE = "marts.shopee_saude_conta_export"
METRICA = "marts.shopee_saude_metrica_export"
CHAT = "marts.shopee_chat_export"
RETURNS = "marts.shopee_returns_export"
CONTA_EXPORT = "marts.expedicao_conta_export"

PREFLIGHT_SQL = (
    "SELECT to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL"
    " AND to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL AS tem_036,"
    " current_setting('transaction_read_only') AS somente_leitura"
)
CONTA_SQL = (
    f"SELECT c.brand, (c.ativo AND l.ativo) AS ativo FROM {CONTA_EXPORT} c"
    " JOIN marts.dim_loja l ON l.brand_key = c.brand"
    " WHERE c.channel = 'shopee' AND c.shop_account = %s"
)

COLS_SAUDE = ("brand", "captured_date", "captured_at", "overall_rating", "fulfillment_failed",
              "listing_failed", "custom_service_failed", "penalty_points", "ongoing_punishments",
              "source_sha256")
COLS_METRICA = ("brand", "captured_date", "metric_id", "metric_name", "metric_type",
                "parent_metric_id", "unit", "current_period", "last_period", "target_value",
                "target_comparator")
COLS_CHAT = ("brand", "captured_at", "unread_count", "conversas_7d", "conversas_30d",
             "aguardando_resposta", "conversas_truncado", "periodo_dias", "visitantes_chat",
             "taxa_resposta_pct", "tempo_resposta_medio_s", "csat_pct", "conversao_chat_pct",
             "source_sha256")
COLS_RETURNS = ("brand", "return_sn", "order_sn", "create_time", "update_time", "status",
                "status_export", "reason", "reason_export", "refund_amount", "currency",
                "captured_at")


def _upsert(tabela: str, cols: tuple[str, ...], chave: tuple[str, ...], extra_where: str = "") -> str:
    atualiza = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in chave)
    return (f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES %s "
            f"ON CONFLICT ({', '.join(chave)}) DO UPDATE SET {atualiza}, loaded_at = now()"
            + extra_where)


UPSERT_SAUDE = _upsert(SAUDE, COLS_SAUDE, ("brand", "captured_date"))
UPSERT_CHAT = _upsert(CHAT, COLS_CHAT, ("brand", "captured_at"))
#: Foto mais velha nunca sobrescreve a mais nova (o bot pode reenviar um arquivo antigo).
UPSERT_RETURNS = _upsert(RETURNS, COLS_RETURNS, ("brand", "return_sn"),
                         " WHERE EXCLUDED.captured_at >= shopee_returns_export.captured_at")
INSERT_METRICA = f"INSERT INTO {METRICA} ({', '.join(COLS_METRICA)}) VALUES %s"


def relatorio(leitura: K.Leitura, brand: Optional[str]) -> list[str]:
    linhas = [f"Conta: {leitura.conta} (marca {brand or '?'}) · capturado em "
              f"{leitura.capturado_em.astimezone(K.BRT):%d/%m/%Y %H:%M}"]
    if leitura.saude:
        s = leitura.saude
        fora = [m for m in leitura.metricas if m["parent_metric_id"] == 0
                and K._situacao_fora(m["current_period"], m["target_value"], m["target_comparator"])]
        linhas.append(f"Saúde da Conta: nota {s['overall_rating'] or '—'} · {len(leitura.metricas)} métrica(s)"
                      f" · {len(fora)} fora da meta · pontos {s['penalty_points'] if s['penalty_points'] is not None else '—'}")
    else:
        linhas.append("Saúde da Conta: não enviada")
    if leitura.chat:
        c = leitura.chat
        linhas.append(f"Chat: não lidas {c['unread_count'] if c['unread_count'] is not None else '—'} · conversas 30d "
                      f"{c['conversas_30d'] if c['conversas_30d'] is not None else '—'} · resposta "
                      f"{c['taxa_resposta_pct'] if c['taxa_resposta_pct'] is not None else '—'}%")
    else:
        linhas.append("Chat: não enviado")
    linhas.append(f"Devoluções: {len(leitura.devolucoes)}")
    if leitura.status_sem_mapa:
        linhas.append(f"Status sem mapeamento (avisar o time): {sorted(set(leitura.status_sem_mapa))}")
    if leitura.motivos_sem_mapa:
        linhas.append(f"Motivos sem tradução (aparecem com o texto original): {sorted(set(leitura.motivos_sem_mapa))}")
    return linhas


def gravar(conn, leitura: K.Leitura, brand: str, execute_values=None) -> None:
    """Uma transacao. O chamador faz commit/rollback."""
    if execute_values is None:
        from psycopg2.extras import execute_values  # noqa: PLC0415
    cur = conn.cursor()
    cur.execute("SET LOCAL statement_timeout = 60000")
    if leitura.saude:
        s = dict(leitura.saude, brand=brand, source_sha256=leitura.sha256)
        execute_values(cur, UPSERT_SAUDE, [tuple(s[c] for c in COLS_SAUDE)])
        # a foto do dia e' a ultima enviada: as metricas do dia sao trocadas inteiras
        cur.execute(f"DELETE FROM {METRICA} WHERE brand = %s AND captured_date = %s",
                    (brand, s["captured_date"]))
        if leitura.metricas:
            execute_values(cur, INSERT_METRICA,
                           [tuple(dict(m, brand=brand)[c] for c in COLS_METRICA) for m in leitura.metricas])
    if leitura.chat:
        c = dict(leitura.chat, brand=brand, source_sha256=leitura.sha256)
        execute_values(cur, UPSERT_CHAT, [tuple(c[k] for k in COLS_CHAT)])
    if leitura.devolucoes:
        execute_values(cur, UPSERT_RETURNS,
                       [tuple(dict(d, brand=brand)[k] for k in COLS_RETURNS) for d in leitura.devolucoes],
                       page_size=500)


def executar(arquivo: Path, *, aplicar: bool, agora: datetime,
             abrir: Optional[Callable[[], object]] = None,
             saida: Callable[[str], None] = print, execute_values=None) -> int:
    try:
        leitura = K.ler_payload(arquivo.read_bytes(), agora=agora)
    except K.PayloadInvalido as exc:
        saida("JSON RECUSADO:")
        for p in exc.problemas:
            saida(f"  - {p}")
        return EXIT_RECUSADO

    if abrir is None:
        from pipelines.expedicao.cli import open_target_default as abrir  # noqa: PLC0415
    try:
        conn = abrir()
    except Exception as exc:  # noqa: BLE001 - nunca o texto (pode ter a URL)
        saida(f"falha ao conectar no banco: {type(exc).__name__}")
        return EXIT_FALHA
    try:
        cur = conn.cursor()
        cur.execute(PREFLIGHT_SQL, (SAUDE, METRICA, CHAT, RETURNS))
        pre = cur.fetchone()
        if not pre["tem_036"]:
            saida("migration 036 ausente no banco: avisar o time da Torre")
            return EXIT_PRECONDICAO
        cur.execute(CONTA_SQL, (leitura.conta,))
        conta = cur.fetchone()
        if not conta or not conta["ativo"]:
            saida(f"conta {leitura.conta!r} não cadastrada ou inativa em {CONTA_EXPORT}")
            return EXIT_PRECONDICAO
        brand = conta["brand"]
        for linha in relatorio(leitura, brand):
            saida(linha)
        if not aplicar:
            saida("PRÉVIA: nada foi gravado. Rode de novo com --apply para gravar.")
            conn.rollback()
            return EXIT_OK
        if pre["somente_leitura"] == "on":
            saida("banco em modo somente leitura")
            return EXIT_PRECONDICAO
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok", ("cx_kokeshi:" + brand,))
        if not cur.fetchone()["ok"]:
            conn.rollback()
            saida("outra carga em andamento: espere e rode de novo")
            return EXIT_LOCK_OCUPADO
        gravar(conn, leitura, brand, execute_values)
        conn.commit()
        saida("GRAVADO.")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        saida(f"falha de banco: {type(exc).__name__} (nada foi gravado; avise o time, não repita em laço)")
        return EXIT_FALHA
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipelines.cx.kokeshi_cli",
        description="Grava no Neon a Saúde da Conta, o chat e as devoluções da Kokeshi (JSON do bot).")
    p.add_argument("--arquivo", required=True, type=Path, help="JSON montado pelo bot")
    p.add_argument("--apply", action="store_true", help="grava (sem isto, só mostra a prévia)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.arquivo.is_file():
        print(f"arquivo não encontrado: {args.arquivo}", file=sys.stderr)
        return EXIT_PRECONDICAO
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))
    return executar(args.arquivo, aplicar=args.apply, agora=datetime.now(timezone.utc))


if __name__ == "__main__":
    sys.exit(main())
