"""
MARGEM-REAL-3B — publicação da comissão do Mercado Livre, e SÓ dela.

POR QUE ESTE MÓDULO EXISTE

`daily_performance --source ml` republica a linha INTEIRA da fato. Medido em
25/09/2026 num dry-run de 90 dias: além de `total_fees`, ele mudaria **22
colunas históricas** — GMV (−R$ 100.906,63, −0,897% em 264 de 364 linhas),
pedidos, unidades, Ads, frete e operação. A causa é legítima: a Gold maturou
desde o dia em que cada linha foi publicada, e o upsert republica tudo.

Para a janela incremental isso é exatamente o desejado — é a função do
pipeline diário manter a fotografia recente atualizada. Para um backfill de 90
dias, é uma atualização histórica ampla que ninguém pediu.

Este módulo é a alternativa de escopo restrito: um UPDATE que toca **duas
colunas**, `total_fees` e `avg_fee_pct`, e mais nada. Ele NÃO substitui o
pipeline diário — existe para o backfill histórico e para eventual reparo
financeiro.

O QUE É REUTILIZADO, SEM CÓPIA

  * a consulta do conector (`ml_connector.QUERY`), com a população
    `status = 'paid'`, join por `order_id` e `sale_fee * quantity`;
  * a janela fechada em D−1 BRT (`closed_window`);
  * o guardrail de reconciliação (`ml_fee_reconciliation.reconciliar`);
  * o cálculo de `avg_fee_pct` (`ml_gestao_diaria._fee_pct`);
  * o advisory lock 918130002 (`fato_diaria_lock`).

Divergir de qualquer um deles criaria uma segunda definição de comissão, e
duas definições é o mesmo que nenhuma.

CONTRATO

  * transação única: ou todas as linhas, ou nenhuma;
  * **nunca insere**: a chave tem de existir na fato, senão falha;
  * ausência de observação não é escrita — a linha não é tocada;
  * zero medido é escrito como zero;
  * idempotente: reexecutar com a mesma fonte não muda nada;
  * auditoria própria (`ml_marketplace_fees`), nunca abandonada em `running`
    quando se sabe o desfecho;
  * nenhum DSN, host, senha ou IP em log ou mensagem de erro.

USO

    python -m pipelines.sync_ml_marketplace_fees --mode backfill --days 90
    python -m pipelines.sync_ml_marketplace_fees --mode backfill --days 90 --apply

Sem `--apply` é diagnóstico: lê, reconcilia, compara e relata. Zero escrita.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import text

from pipelines.common.db import LocalSession
from pipelines.common.logging import get_logger
from pipelines.common.operational_calendar import closed_window
from pipelines.connectors.mercadolivre import connector as ml_connector
from pipelines.ingestion.fato_diaria_lock import (
    EXIT_CODE_LOCK_UNAVAILABLE,
    FatoDiariaLockUnavailable,
    fato_diaria_lock,
)
from pipelines.quality.ml_fee_reconciliation import (
    MlFeeReconciliationError,
    reconciliar,
)
from pipelines.transforms.ml_gestao_diaria import BRAND_TO_LOJA, _fee_pct

logger = get_logger(__name__)

MARKETPLACE_ID = 2
SOURCE_NAME = "ml_marketplace_fees"
FACT_TABLE = "marts.fact_marketplace_daily_performance"

#: Exit code quando o diagnóstico ou a reconciliação reprovam. Distinto de 1
#: (erro genérico) e de 75 (lock ocupado) para que o log diga o que aconteceu
#: sem precisar abrir o traceback.
EXIT_CODE_RECONCILIACAO = 65


class MlFeeSyncError(RuntimeError):
    """Falha na publicação da comissão do ML."""


class MlFeeKeyMissingError(MlFeeSyncError):
    """A fonte trouxe uma chave que não existe na fato.

    Subclasse própria porque a ação é diferente: não é para reexecutar, é para
    descobrir por que a fato não tem o dia — provavelmente o pipeline diário
    não rodou nessa janela.
    """


# ---------------------------------------------------------------------------
# Sanitização — nada de DSN, host, senha ou IP em log
# ---------------------------------------------------------------------------

_PADROES_SENSIVEIS = (
    re.compile(r"postgres(?:ql)?://[^\s]*", re.I),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    re.compile(r"password=\S+", re.I),
    re.compile(r"\bhost=\S+", re.I),
    re.compile(r"\buser=\S+", re.I),
)


def sanitizar(exc: BaseException | str) -> str:
    """Mensagem sem DSN, IP, host, senha ou usuário."""
    texto = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    for padrao in _PADROES_SENSIVEIS:
        texto = padrao.sub("[REDACTED]", texto)
    return " ".join(texto.split())[:500]


# ---------------------------------------------------------------------------
# SQL — escopo restrito, sem COALESCE
# ---------------------------------------------------------------------------

#: As DUAS colunas autorizadas, e nada mais.
#:
#: Sem `COALESCE`: o valor vem do guardrail, que já recusou publicar qualquer
#: coisa que não reconcilie. Um COALESCE aqui preservaria em silêncio um valor
#: antigo e faria a linha parecer atual.
#:
#: Sem `INSERT`: `rowcount = 0` significa que a chave não existe na fato, e a
#: resposta certa é falhar — inserir criaria uma linha só com comissão, sem
#: GMV nem pedidos, que nenhuma tela saberia ler.
SQL_PATCH_FEES = text(f"""
    UPDATE {FACT_TABLE}
       SET total_fees  = :total_fees,
           avg_fee_pct = :avg_fee_pct
     WHERE date           = :date
       AND loja_id        = :loja_id
       AND marketplace_id = {MARKETPLACE_ID}
""")

SQL_AUDIT_START = text("""
    INSERT INTO audit.source_sync_run
        (source_name, marketplace_id, status, source_min_date, source_max_date)
    VALUES (:fonte, :mkt, 'running', :min_date, :max_date)
    RETURNING sync_run_id
""")

SQL_AUDIT_FINISH = text("""
    UPDATE audit.source_sync_run
       SET status = :status, finished_at = NOW(),
           rows_extracted = :extracted, rows_loaded = :loaded,
           error_message = :erro
     WHERE sync_run_id = :sync_run_id
""")


# ---------------------------------------------------------------------------
# Preparação das linhas
# ---------------------------------------------------------------------------

@dataclass
class Preparo:
    """O que sai da fonte, pronto para o UPDATE."""
    para_escrever: list[dict] = field(default_factory=list)
    sem_observacao: list[tuple] = field(default_factory=list)
    fora_da_dimensao: list[tuple] = field(default_factory=list)
    linhas_fonte: int = 0

    @property
    def soma_fees(self) -> Decimal:
        total = Decimal(0)
        for r in self.para_escrever:
            v = r["total_fees"]
            if v is not None:
                total += Decimal(str(v))
        return total


def preparar(brutas: list[dict]) -> Preparo:
    """Converte as linhas cruas em parâmetros do UPDATE.

    Linha SEM comissão observada não entra: não se escreve ausência por cima
    do que já está publicado. O guardrail já garantiu que ausência só sobra
    onde não houve pedido pago.
    """
    p = Preparo(linhas_fonte=len(brutas))
    vistas: set[tuple] = set()

    for row in brutas:
        ref_date, brand = row.get("date"), row.get("brand")
        loja_id = BRAND_TO_LOJA.get(brand)
        if loja_id is None:
            p.fora_da_dimensao.append((ref_date, brand))
            continue

        chave = (ref_date, loja_id)
        if chave in vistas:
            raise MlFeeSyncError(
                f"chave repetida na fonte: {ref_date} loja_id={loja_id}. "
                "O agregado por dia x marca deveria ser unico; publicar assim "
                "somaria a comissao duas vezes."
            )
        vistas.add(chave)

        fee = row.get("marketplace_fee")
        if fee is None:
            p.sem_observacao.append((ref_date, brand))
            continue

        p.para_escrever.append({
            "date": ref_date,
            "loja_id": loja_id,
            "total_fees": fee,
            "avg_fee_pct": _fee_pct(fee, row.get("gmv")),
        })

    return p


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------

def _ler_fonte(date_from: date, date_to: date) -> list[dict]:
    brutas = ml_connector.fetch(date_from, date_to)
    reconciliar(
        brutas,
        brands_esperadas=ml_connector.BRANDS_IN_SCOPE,
        date_from=date_from,
        date_to=date_to,
    )
    return brutas


def diagnosticar(date_from: date, date_to: date,
                 fetch: Optional[Callable] = None) -> dict:
    """Lê, reconcilia e relata. NÃO escreve, NÃO abre auditoria, NÃO trava."""
    brutas = (fetch or _ler_fonte)(date_from, date_to)
    p = preparar(brutas)
    resumo = {
        "mode": "diagnose",
        "applied": False,
        "date_from": date_from,
        "date_to": date_to,
        "linhas_fonte": p.linhas_fonte,
        "a_escrever": len(p.para_escrever),
        "sem_observacao": len(p.sem_observacao),
        "fora_da_dimensao": len(p.fora_da_dimensao),
        "soma_fees": p.soma_fees,
    }
    logger.info(
        "diagnostico %s..%s: %d linha(s) da fonte, %d a escrever, "
        "%d sem observacao, comissao %s",
        date_from, date_to, p.linhas_fonte, len(p.para_escrever),
        len(p.sem_observacao), p.soma_fees,
    )
    return resumo


def publicar(date_from: date, date_to: date, *,
             fetch: Optional[Callable] = None,
             session_factory: Optional[Callable] = None,
             lock_cm: Optional[Callable] = None) -> dict:
    """Publica a comissão numa transação única, sob o lock do ML.

    Ordem: lock -> auditoria `running` -> leitura + reconciliação -> UPDATE ->
    commit -> auditoria `success`. A leitura acontece DEPOIS do lock: ler antes
    publicaria uma fotografia que outro escritor já pode ter invalidado.
    """
    fabrica = session_factory or LocalSession
    trava = lock_cm or fato_diaria_lock

    extracted = 0
    loaded = 0
    sync_run_id: int | None = None
    sessao_audit = None
    #: `failed` afirma que NADA foi publicado. Depois do commit isso deixa de
    #: ser verdade, e um `failed` mentiroso faria o próximo operador republicar
    #: sobre dado bom. A partir do commit, o desfecho honesto de uma falha é
    #: deixar a auditoria em `running` — um alarme visível que pede inspeção.
    publicado = False

    with trava(MARKETPLACE_ID):
        try:
            # --- auditoria: linha `running` em transação PRÓPRIA -----------
            sessao_audit = fabrica()
            sync_run_id = sessao_audit.execute(SQL_AUDIT_START, {
                "fonte": SOURCE_NAME, "mkt": MARKETPLACE_ID,
                "min_date": date_from, "max_date": date_to,
            }).scalar_one()
            sessao_audit.commit()
            logger.info("sync_run_id=%s aberto", sync_run_id)

            # --- fonte + reconciliação (nenhuma escrita ainda) -------------
            brutas = (fetch or _ler_fonte)(date_from, date_to)
            p = preparar(brutas)
            extracted = p.linhas_fonte

            if p.fora_da_dimensao:
                raise MlFeeSyncError(
                    f"{len(p.fora_da_dimensao)} linha(s) com marca fora de "
                    "marts.dim_loja; nada foi publicado."
                )

            # --- transação única de publicação -----------------------------
            sessao_pub = fabrica()
            try:
                for params in p.para_escrever:
                    res = sessao_pub.execute(SQL_PATCH_FEES, params)
                    if res.rowcount == 0:
                        raise MlFeeKeyMissingError(
                            f"chave {params['date']} loja_id={params['loja_id']} "
                            f"nao existe em {FACT_TABLE}. Este publisher NAO "
                            "insere linha: uma linha so' com comissao, sem GMV "
                            "nem pedidos, nenhuma tela saberia ler. Transacao "
                            "inteira desfeita."
                        )
                    if res.rowcount > 1:
                        raise MlFeeSyncError(
                            f"UPDATE afetou {res.rowcount} linhas para a chave "
                            f"{params['date']} loja_id={params['loja_id']}; "
                            "esperado no maximo 1. Transacao desfeita."
                        )
                    loaded += res.rowcount
                sessao_pub.commit()
                publicado = True
            except BaseException:
                sessao_pub.rollback()
                raise
            finally:
                sessao_pub.close()

            logger.info("comissao publicada: %d linha(s), soma %s",
                        loaded, p.soma_fees)

            _fechar_auditoria(sessao_audit, sync_run_id, "success",
                              extracted, loaded, None)
            return {
                "mode": "apply",
                "applied": True,
                "sync_run_id": sync_run_id,
                "date_from": date_from,
                "date_to": date_to,
                "linhas_fonte": p.linhas_fonte,
                "rows_updated": loaded,
                "sem_observacao": len(p.sem_observacao),
                "soma_fees": p.soma_fees,
            }

        except BaseException as exc:
            # `BaseException`, não `Exception`: KeyboardInterrupt e SystemExit
            # também têm de fechar a auditoria. Um `running` preso por Ctrl+C
            # faz o próximo operador acreditar que há carga em andamento.
            msg = sanitizar(exc)
            if publicado:
                # A publicação está COMMITADA e algo falhou depois (fechar a
                # auditoria, montar o resumo). Marcar `failed` diria que nada
                # foi escrito — falso, e levaria a uma republicação sobre dado
                # correto. A linha fica em `running`, que é o estado honesto:
                # ninguém sabe se a execução terminou, e isso pede um humano.
                logger.error(
                    "comissao COMMITADA (%d linha[s]) mas a execucao falhou "
                    "depois: %s. sync_run_id=%s fica em 'running' de proposito "
                    "— verifique %s antes de qualquer reexecucao.",
                    loaded, msg, sync_run_id, FACT_TABLE)
                raise
            logger.error("publicacao da comissao ML falhou: %s", msg)
            if sync_run_id is not None and sessao_audit is not None:
                _fechar_auditoria(sessao_audit, sync_run_id, "failed",
                                  extracted, loaded, msg)
            raise
        finally:
            if sessao_audit is not None:
                try:
                    sessao_audit.close()
                except Exception:   # pragma: no cover
                    pass


def _fechar_auditoria(sessao, sync_run_id: int, status: str,
                      extracted: int, loaded: int, erro: str | None) -> None:
    """Fecha a linha de auditoria. Nunca deixa `running` quando se sabe o fim.

    Falhar aqui não pode mascarar a exceção original: a publicação já aconteceu
    (ou já foi desfeita), e perder o rastro é menos grave que trocar a causa
    raiz por um erro de auditoria.
    """
    try:
        res = sessao.execute(SQL_AUDIT_FINISH, {
            "status": status, "extracted": extracted, "loaded": loaded,
            "erro": erro, "sync_run_id": sync_run_id,
        })
        if res.rowcount != 1:
            logger.error("auditoria %s afetou %d linha(s); esperado 1",
                         sync_run_id, res.rowcount)
        sessao.commit()
    except Exception as aud:
        logger.error("auditoria %s nao registrou %s: %s",
                     sync_run_id, status, sanitizar(aud))
        try:
            sessao.rollback()
        except Exception:   # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def resolver_janela(mode: str, days: int) -> tuple[date, date]:
    """Janela sempre fechada em D−1 BRT, igual à do pipeline diário."""
    if mode == "incremental":
        return closed_window(3 if days is None else days)
    return closed_window(days)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publica SOMENTE total_fees e avg_fee_pct do Mercado Livre "
                    "na fato diaria. Nao toca em nenhuma outra coluna."
    )
    parser.add_argument("--mode", choices=["incremental", "backfill"],
                        default="backfill")
    parser.add_argument("--days", type=int, default=90,
                        help="Dias para tras (backfill=90, incremental=3)")
    parser.add_argument("--apply", action="store_true",
                        help="Sem esta flag o comando e' diagnostico: zero escrita.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(name)s %(message)s")

    date_from, date_to = resolver_janela(args.mode, args.days)

    try:
        if not args.apply:
            resumo = diagnosticar(date_from, date_to)
            print(f"DIAGNOSTICO (nenhuma escrita)")
            for k, v in resumo.items():
                print(f"  {k:18} {v}")
            return 0

        resumo = publicar(date_from, date_to)
        print("PUBLICADO")
        for k, v in resumo.items():
            print(f"  {k:18} {v}")
        return 0

    except FatoDiariaLockUnavailable as exc:
        logger.error("%s", sanitizar(exc))
        return EXIT_CODE_LOCK_UNAVAILABLE
    except MlFeeReconciliationError as exc:
        logger.error("reconciliacao reprovou: %s", sanitizar(exc))
        return EXIT_CODE_RECONCILIACAO
    except MlFeeSyncError as exc:
        logger.error("%s", sanitizar(exc))
        return 1


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
