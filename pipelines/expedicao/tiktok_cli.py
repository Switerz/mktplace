"""CLI da serie diaria de expedicao do TikTok Shop.

    python -m pipelines.expedicao.tiktok_cli --diagnose
    python -m pipelines.expedicao.tiktok_cli --apply

Entrada SEPARADA da `pipelines.expedicao.cli` de proposito. Aquela publica a
fotografia da fila (Shopee, Mercado Livre) e toda a sua orquestracao gira em
torno de `expedicao_fila_atual` + `expedicao_refresh_run`. Esta publica uma
SERIE por coorte, com outro grao e outro DELETE. Enfiar as duas no mesmo
`--apply` obrigaria a ramificar o fluxo inteiro por canal e poria em risco os
dois canais ja em producao a cada mudanca aqui. Os codigos de saida, a
auditoria e a chave de lock sao os mesmos — o que muda e' so' o que se publica.

CODIGOS DE SAIDA (identicos aos da `cli.py`, para o agendador nao precisar
saber qual das duas chamou):
    0 ok · 1 falha · 2 lock ocupado · 3 fonte nao publicavel
    4 commit indeterminado · 5 auditoria incompleta · 6 precondicao
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipelines.expedicao import audit as audit_mod
from pipelines.expedicao.contract import MARKETPLACE_ID, Channel
from pipelines.expedicao.publisher import (
    IndeterminateCommit,
    LockNotAcquired,
    channel_lock,
)
from pipelines.expedicao.tiktok_daily import (
    COLUNAS,
    DELETE_JANELA_SQL,
    JANELA_PADRAO_DIAS,
    PRAZO_E_RECONSTRUIDO,
    SOURCE_NAME,
    TABELA,
    Evento,
    agregar_janela,
    extrair,
    hoje_brt,
    ler_watermark,
    linhas_para_publicar,
    marcas_criticas,
    primeiro_dia_do_incidente,
    serie_por_dia,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CANAL = Channel.TIKTOKSHOP

ENV_TARGET = "DATABASE_URL"
ENV_SOURCE = "DATAMART_DATABASE_URL"

EXIT_OK = 0
EXIT_FALHA = 1
EXIT_LOCK_OCUPADO = 2
EXIT_FONTE_NAO_PUBLICAVEL = 3
EXIT_COMMIT_INDETERMINADO = 4
EXIT_AUDITORIA_INCOMPLETA = 5
EXIT_PRECONDICAO = 6


def log(msg: str) -> None:
    print(msg, flush=True)


def _pct(v: float | None) -> str:
    return "n/d" if v is None else f"{v * 100:.2f}%"


@contextmanager
def _conectar(url: str, *, readonly: bool, nome: str):
    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=30, application_name=nome
    )
    try:
        # `readonly` vai no SERVIDOR: um SELECT mal escrito que virasse escrita
        # seria recusado pelo proprio Postgres, e nao pela nossa disciplina.
        conn.set_session(readonly=readonly, autocommit=True)
        yield conn
    finally:
        conn.close()


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------
def _relatorio(coortes, hoje, dias: int) -> None:
    log(f"DIAGNOSTICO expedicao/{CANAL.value} — hoje (BRT) = {hoje}")
    log(f"  janela: {dias} dias + hoje · {len(coortes)} coortes (dia x marca)")
    if PRAZO_E_RECONSTRUIDO:
        log("  ATENCAO: o prazo e' RECONSTRUIDO da regra de 2 dias uteis.")
        log("  O SLA oficial do TikTok nao e' ingerido (ver tiktok_daily.py).")

    for evento in (Evento.DESPACHO, Evento.COLETA):
        log("")
        log(f"  === {evento.value.upper()} ===")
        log("    dia         pagos   atrasados    taxa")
        for dia, taxa, pagos, madura in serie_por_dia(coortes, evento, hoje):
            atras = sum(
                c.atrasados(evento) for c in coortes if c.paid_date == dia
            )
            marca = "" if madura else "   PARCIAL (prazo ainda nao venceu)"
            log(f"    {dia}  {pagos:6d}  {atras:10d}  {_pct(taxa):>7}{marca}")
        j = agregar_janela(coortes, evento, hoje)
        log(f"    janela — razao dos totais  : {_pct(j.razao_dos_totais)}")
        log(f"    janela — media das diarias : {_pct(j.media_das_diarias)}")
        if j.divergencia_pp is not None:
            log(f"    as duas leituras divergem em {j.divergencia_pp:+.2f} pp")
        log(f"    coortes maduras={j.coortes_maduras} parciais={j.coortes_parciais}")

    inicio = primeiro_dia_do_incidente(coortes, Evento.COLETA, hoje)
    log("")
    log(f"  inicio do incidente de COLETA (limiar 10%): {inicio or 'nenhum'}")
    log("  marcas por taxa de coleta atrasada:")
    for marca, taxa, pagos in marcas_criticas(coortes, Evento.COLETA, hoje):
        log(f"    {marca:<14}{_pct(taxa):>8}   ({pagos} pedidos)")


def _run_diagnose(dias: int) -> int:
    fonte_url = os.environ.get(ENV_SOURCE, "")
    if not fonte_url:
        log(f"{ENV_SOURCE} nao configurada.")
        return EXIT_PRECONDICAO
    with _conectar(fonte_url, readonly=True, nome="expedicao_tiktok_diag") as fonte:
        hoje = hoje_brt(_agora_utc())
        coortes = extrair(fonte, hoje, dias)
        if not coortes:
            log("fonte sem coorte na janela — nada a diagnosticar.")
            return EXIT_FONTE_NAO_PUBLICAVEL
        _relatorio(coortes, hoje, dias)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
INSERT_SQL = (
    f"INSERT INTO {TABELA} ({', '.join(COLUNAS)}) VALUES "
    f"({', '.join(['%s'] * len(COLUNAS))})"
)


def publicar(alvo, coortes, *, hoje, effective_at, watermark, desde, ate):
    """DELETE da janela + INSERT, na MESMA transacao.

    A janela inteira e' reescrita, e nao so' os dias novos: uma coorte antiga
    MUDA quando um pedido dela e' finalmente coletado. Manter a linha velha
    congelaria a taxa historica num valor que deixou de ser verdade.

    O DELETE e' limitado por canal E por intervalo de datas: a serie fora da
    janela e' historico e nao pode ser tocada por uma execucao de rotina.
    """
    lote, linhas = linhas_para_publicar(
        coortes, hoje=hoje, effective_at=effective_at, watermark=watermark
    )
    alvo.autocommit = False
    try:
        with alvo.cursor() as cur:
            cur.execute(
                DELETE_JANELA_SQL,
                {"channel": CANAL.value, "desde": desde.isoformat(), "ate": ate.isoformat()},
            )
            apagadas = cur.rowcount
            cur.executemany(INSERT_SQL, linhas)
        try:
            alvo.commit()
        except Exception as exc:  # noqa: BLE001
            # Falha NO commit deixa o estado desconhecido: nem "publicou" nem
            # "nao publicou" tem prova. Rollback aqui poderia estar desfazendo
            # algo que ja entrou. Quem le exit 4 sabe que precisa olhar.
            raise IndeterminateCommit(
                f"commit indeterminado ao publicar {CANAL.value}: "
                f"{audit_mod.sanitize_error_message(exc)}"
            ) from exc
    except IndeterminateCommit:
        raise
    except Exception:
        alvo.rollback()
        raise
    finally:
        alvo.autocommit = True
    return lote, len(linhas), apagadas


def _run_apply(dias: int) -> int:
    alvo_url = os.environ.get(ENV_TARGET, "")
    fonte_url = os.environ.get(ENV_SOURCE, "")
    if not alvo_url or not fonte_url:
        log(f"{ENV_TARGET} e {ENV_SOURCE} sao obrigatorias.")
        return EXIT_PRECONDICAO

    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    alvo = psycopg2.connect(
        alvo_url, cursor_factory=RealDictCursor, connect_timeout=30,
        application_name="expedicao_tiktok_apply",
    )
    auditoria = psycopg2.connect(
        alvo_url, cursor_factory=RealDictCursor, connect_timeout=30,
        application_name="expedicao_tiktok_audit",
    )
    run_id: int | None = None
    publicado = False
    extraidas = 0
    try:
        alvo.autocommit = True
        auditoria.autocommit = False
        with channel_lock(alvo, CANAL):
            run_id = audit_mod.audit_start(auditoria, SOURCE_NAME, MARKETPLACE_ID[CANAL])
            effective_at = _agora_utc()
            hoje = hoje_brt(effective_at)
            desde = hoje - timedelta(days=dias)

            with _conectar(fonte_url, readonly=True, nome="expedicao_tiktok_src") as fonte:
                coortes = extrair(fonte, hoje, dias)
                watermark = ler_watermark(fonte, hoje, dias)
            extraidas = len(coortes)
            if not coortes:
                raise RuntimeError("fonte sem coorte na janela; nada a publicar")

            lote, gravadas, apagadas = publicar(
                alvo, coortes, hoje=hoje, effective_at=effective_at,
                watermark=watermark, desde=desde, ate=hoje,
            )
            publicado = True
            log(f"PUBLICADO lote={lote} linhas={gravadas} (substituiu {apagadas})")
            log(f"  janela {desde} .. {hoje} · effective_at={effective_at.isoformat()}")

            audit_mod.audit_finish(
                auditoria, run_id, "success",
                rows_extracted=extraidas, rows_loaded=gravadas,
            )
        return EXIT_OK

    except LockNotAcquired as exc:
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_LOCK_OCUPADO
    except IndeterminateCommit as exc:
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_COMMIT_INDETERMINADO
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI
        log(f"FALHA: {audit_mod.sanitize_error_message(exc)}")
        if run_id is not None:
            try:
                audit_mod.audit_finish(
                    auditoria, run_id, "failed",
                    rows_extracted=extraidas,
                    rows_loaded=None,
                    error=audit_mod.sanitize_error_message(exc),
                )
            except Exception:  # noqa: BLE001
                log("auditoria nao pode ser fechada; execucao fica em 'running'.")
                return EXIT_AUDITORIA_INCOMPLETA
        return EXIT_FALHA if not publicado else EXIT_AUDITORIA_INCOMPLETA
    finally:
        alvo.close()
        auditoria.close()


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="expedicao-tiktok",
        description=(
            "Serie diaria de atraso de despacho do TikTok Shop. Sem --apply, "
            "apenas diagnostico read-only."
        ),
    )
    p.add_argument(
        "--diagnose", action="store_true",
        help="le a fonte e imprime a serie; nao abre conexao gravavel",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="publica a janela no Neon. A flag E a confirmacao.",
    )
    p.add_argument(
        "--dias", type=int, default=JANELA_PADRAO_DIAS,
        help=f"dias antes de hoje na janela (padrao {JANELA_PADRAO_DIAS}, "
             f"totalizando {JANELA_PADRAO_DIAS + 1} coortes)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and args.diagnose:
        print("--apply e --diagnose sao mutuamente exclusivos.", file=sys.stderr)
        return EXIT_FALHA
    if not (args.apply or args.diagnose):
        print("informe --diagnose ou --apply.", file=sys.stderr)
        return EXIT_FALHA
    if args.dias < 1 or args.dias > 90:
        print("--dias deve estar entre 1 e 90.", file=sys.stderr)
        return EXIT_FALHA

    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    if args.diagnose:
        log("MODO DIAGNOSTICO: nenhuma escrita, nenhuma conexao gravavel.")
        try:
            return _run_diagnose(args.dias)
        except Exception as exc:  # noqa: BLE001
            print(f"FALHA (diagnose): {audit_mod.sanitize_error_message(exc)}",
                  file=sys.stderr)
            return EXIT_FALHA
    return _run_apply(args.dias)


if __name__ == "__main__":
    raise SystemExit(main())
