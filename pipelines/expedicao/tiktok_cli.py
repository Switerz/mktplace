"""CLI da LDR de expedicao do TikTok Shop.

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
from datetime import datetime, timezone
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
    LIMIAR_CRITICO_INTERNO,
    META_TIKTOK_LDR,
    PRAZO_DIAS_UTEIS,
    PRAZO_E_RECONSTRUIDO,
    SOURCE_NAME,
    TABELA,
    Evento,
    janela_de_vencimento,
    janela_de_pagamento,
    calcular_ldr,
    extrair,
    hoje_brt,
    ler_proveniencia,
    ler_watermark,
    linhas_para_publicar,
    marcas_criticas,
    primeiro_dia_do_incidente,
    serie_por_pagamento,
    serie_por_vencimento,
    ultimo_dia_do_incidente,
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

#: Abaixo disto a cobertura de `IN_TRANSIT` nao sustenta a medicao de coleta:
#: o diagnostico avisa em vez de apresentar uma taxa que mede outra coisa.
COBERTURA_MINIMA_IN_TRANSIT = 0.95


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
def _relatorio(coortes, proveniencia, hoje, dias: int) -> None:
    desde, ate = janela_de_vencimento(hoje, dias)
    log(f"DIAGNOSTICO expedicao/{CANAL.value} — hoje (BRT) = {hoje}")
    log(f"  janela de vencimento: {desde} .. {ate}")
    log(f"  {len(coortes)} coortes (data de pagamento x marca)")
    if PRAZO_E_RECONSTRUIDO:
        log("  ATENCAO: o prazo e' RECONSTRUIDO da politica de dias uteis")
        log(f"  (RTS {PRAZO_DIAS_UTEIS[Evento.DESPACHO]} du, "
            f"TTS {PRAZO_DIAS_UTEIS[Evento.COLETA]} du).")
        log("  O SLA oficial do TikTok nao e' ingerido (ver tiktok_daily.py).")

    # --- proveniencia do instante de coleta -------------------------------
    tot = sum(int(r["pedidos"]) for r in proveniencia)
    com_it = sum(int(r["com_in_transit"]) for r in proveniencia)
    so_post = sum(int(r["so_posterior"]) for r in proveniencia)
    cobertura = (com_it / tot) if tot else 0.0
    log("")
    log("  === PROVENIENCIA DO INSTANTE DE COLETA ===")
    log(f"    pedidos                           {tot}")
    log(f"    com IN_TRANSIT (fonte usada)      {com_it}  ({_pct(cobertura)})")
    log(f"    so' DELIVERED/COMPLETED (NAO usado) {so_post}")
    log(f"    sem nenhum evento                 {tot - com_it - so_post}")
    if cobertura < COBERTURA_MINIMA_IN_TRANSIT:
        log("    AVISO: cobertura abaixo do minimo — a taxa de COLETA nao")
        log("    sustenta conclusao sobre a transportadora.")

    # --- as duas leituras --------------------------------------------------
    for evento in (Evento.COLETA, Evento.DESPACHO):
        ldr = calcular_ldr(coortes, evento, hoje, desde=desde, ate=ate)
        log("")
        log(f"  === LDR OPERACIONAL — VENCIMENTOS NA JANELA · {evento.value.upper()} "
            f"({PRAZO_DIAS_UTEIS[evento]} dia(s) util(eis)) ===")
        log(f"    taxa                 {_pct(ldr.taxa)}")
        log(f"    meta do TikTok       {_pct(META_TIKTOK_LDR)}  -> "
            f"{'FORA DA META' if ldr.fora_da_meta else 'dentro da meta'}")
        if ldr.critico_interno:
            log(f"    limiar interno       {_pct(LIMIAR_CRITICO_INTERNO)}  -> CRITICO")
        log(f"    base (vencem na janela)   {ldr.base}")
        log(f"    atrasados                 {ldr.atrasados}")
        log(f"    pendentes ja vencidos     {ldr.pendentes_vencidos}")
        log(f"    pendentes ainda no prazo  {ldr.pendentes_no_prazo} "
            f"(em risco: {ldr.em_risco})")
        log(f"    vencimentos maduros={ldr.vencimentos_maduros} "
            f"parciais={ldr.vencimentos_parciais}")
        log("    vence em    base  atrasados     taxa")
        for d, taxa, base, atras, madura in serie_por_vencimento(coortes, evento, hoje):
            if not (desde <= d <= ate):
                continue
            marca = "" if madura else "   PARCIAL (prazo ainda nao venceu)"
            log(f"    {d}  {base:6d}  {atras:9d}  {_pct(taxa):>7}{marca}")

    # --- fluxo por data de pagamento (a visao da gestao) -------------------
    log("")
    log("  === FLUXO POR DATA DE PAGAMENTO · COLETA ===")
    log("  (denominador = quem PAGOU no dia; NAO e' a LDR oficial)")
    log("    pago em     vence em    base  atrasados     taxa")
    for d, venc, taxa, base, atras, madura in serie_por_pagamento(
        coortes, Evento.COLETA, hoje
    ):
        if d < desde - (ate - desde):
            continue
        marca = "" if madura else "   PARCIAL"
        log(f"    {d}  {venc}  {base:6d}  {atras:9d}  {_pct(taxa):>7}{marca}")

    ini = primeiro_dia_do_incidente(coortes, Evento.COLETA, hoje)
    fim = ultimo_dia_do_incidente(coortes, Evento.COLETA, hoje)
    log("")
    log(f"  incidente de COLETA (>= {_pct(LIMIAR_CRITICO_INTERNO)}, por vencimento): "
        f"{ini or 'nenhum'} .. {fim or 'nenhum'}")
    log("  marcas por LDR de coleta:")
    for marca, taxa, base in marcas_criticas(
        coortes, Evento.COLETA, hoje, desde=desde, ate=ate
    ):
        selo = "  FORA DA META" if taxa > META_TIKTOK_LDR else ""
        log(f"    {marca:<14}{_pct(taxa):>8}   ({base} pedidos){selo}")


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
        proveniencia = ler_proveniencia(fonte, hoje, dias)
        _relatorio(coortes, proveniencia, hoje, dias)
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

    O DELETE e' limitado por canal E por intervalo de DATA DE PAGAMENTO — o
    mesmo intervalo que foi extraido. A serie fora dele e' historico e nao pode
    ser tocada por uma execucao de rotina.
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
            desde, ate = janela_de_pagamento(hoje, dias)

            with _conectar(fonte_url, readonly=True, nome="expedicao_tiktok_src") as fonte:
                coortes = extrair(fonte, hoje, dias)
                watermark = ler_watermark(fonte, hoje, dias)
            extraidas = len(coortes)
            if not coortes:
                raise RuntimeError("fonte sem coorte na janela; nada a publicar")

            lote, gravadas, apagadas = publicar(
                alvo, coortes, hoje=hoje, effective_at=effective_at,
                watermark=watermark, desde=desde, ate=ate,
            )
            publicado = True
            log(f"PUBLICADO lote={lote} linhas={gravadas} (substituiu {apagadas})")
            log(f"  pagamentos {desde} .. {ate} · effective_at={effective_at.isoformat()}")

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
            "LDR de despacho do TikTok Shop. Sem --apply, apenas diagnostico "
            "read-only."
        ),
    )
    p.add_argument(
        "--diagnose", action="store_true",
        help="le a fonte e imprime as duas leituras; nao abre conexao gravavel",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="publica a janela no Neon. A flag E a confirmacao.",
    )
    p.add_argument(
        "--dias", type=int, default=JANELA_PADRAO_DIAS,
        help=f"dias de VENCIMENTO antes de hoje (padrao {JANELA_PADRAO_DIAS}, "
             f"totalizando {JANELA_PADRAO_DIAS + 1} vencimentos)",
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
