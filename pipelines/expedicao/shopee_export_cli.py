"""Carga da Expedicao Shopee a partir do export do Seller Centre.

EXPEDICAO-KOKESHI-EXPORT. Quem roda e' o operador da conta (o bot da Kokeshi),
logo depois de baixar o export de pedidos:

    python -m pipelines.expedicao.shopee_export_cli --conta kokeshi \\
        --arquivo "<pasta>/Order.all.order_creation_date.20261004_20261103.xlsx"

Sem `--apply` e' PREVIA: le o arquivo, monta a fila e imprime os numeros, sem
gravar nada. Com `--apply`:

  1. lock proprio (`EXPORT_ADVISORY_LOCK_KEY`) e auditoria
     `expedicao_shopee_export` em `audit.source_sync_run`;
  2. grava o ultimo estado de cada pedido do arquivo em
     `marts.expedicao_shopee_export_pedido` (sem dado de comprador);
  3. calcula o baseline de lentidao sobre essa tabela (30 dias, como no Data
     Mart) e monta a fila com `transform.build_fila_shopee`;
  4. publica fila e resumo da CONTA em `marts.expedicao_fila_export` e
     `marts.expedicao_refresh_run_export`, pelo MESMO `publish_channel` do
     publicador agendado, com o DELETE restrito a conta.

A fila sai SO' do arquivo desta carga, nunca da tabela de pedidos: um pedido
que saiu de "A Enviar" deixa de vir no export, e ler a tabela o deixaria
pendurado na fila para sempre.

Nada aqui toca o Data Mart nem as tabelas do publicador agendado.

Saidas: 0 publicado (ou previa ok), 1 falha antes do commit, 2 lock ocupado,
3 export recusado, 4 commit indeterminado, 5 auditoria incompleta apos o
commit, 6 precondicao (migration/conta).
"""
from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipelines.expedicao import audit as audit_mod
from pipelines.expedicao import shopee_export as E
from pipelines.expedicao import transform
from pipelines.expedicao.cli import (
    EXIT_AUDITORIA_INCOMPLETA,
    EXIT_COMMIT_INDETERMINADO,
    EXIT_FALHA,
    EXIT_FONTE_NAO_PUBLICAVEL,
    EXIT_LOCK_OCUPADO,
    EXIT_OK,
    EXIT_PRECONDICAO,
    REPO_ROOT,
    PreflightFalhou,
    open_audit_default,
    open_target_default,
    source_advanced,
)
from pipelines.expedicao.contract import (
    CONTA_EXPORT_TABLE,
    EXPORT_ADVISORY_LOCK_KEY,
    FILA_EXPORT_TABLE,
    MARKETPLACE_ID,
    PEDIDO_EXPORT_TABLE,
    RUN_EXPORT_TABLE,
    Channel,
    SellerAccount,
    SourceHealth,
)
from pipelines.expedicao.publisher import (
    Destino,
    IndeterminateCommit,
    LockNotAcquired,
    publish_channel,
)

FONTE_AUDITORIA = "expedicao_shopee_export"
CANAL = Channel.SHOPEE
MAX_IDADE_PADRAO_HORAS = 3.0

#: Colunas gravadas em `marts.expedicao_shopee_export_pedido`, em ordem.
COLUNAS_PEDIDO = (
    "shop_account", "order_sn", "shop_id", "order_status", "status_export",
    "has_tracking", "create_time", "pay_time", "ship_by_date",
    "pickup_done_time", "shipping_carrier", "fulfillment_flag", "cancel_reason",
    "ingested_at", "source_file", "source_sha256",
)

#: Estado mais novo vence; um arquivo mais velho rodado depois nao regride.
UPSERT_PEDIDO_SQL = (
    f"INSERT INTO {PEDIDO_EXPORT_TABLE} ({', '.join(COLUNAS_PEDIDO)}) VALUES %s "
    "ON CONFLICT (shop_account, order_sn) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUNAS_PEDIDO[2:])
    + ", loaded_at = now() "
    f"WHERE EXCLUDED.ingested_at >= {PEDIDO_EXPORT_TABLE.split('.')[-1]}.ingested_at"
)

CONTA_SQL = f"""
SELECT c.shop_account, c.external_seller_id, c.brand, l.loja_id,
       (c.ativo AND l.ativo) AS ativo,
       EXISTS (SELECT 1 FROM marts.dim_seller_account sa
               WHERE sa.marketplace_id = %(mkt)s
                 AND sa.external_seller_id = c.external_seller_id) AS no_registry_api
FROM {CONTA_EXPORT_TABLE} c
JOIN marts.dim_loja l ON l.brand_key = c.brand
WHERE c.channel = %(canal)s AND c.shop_account = %(conta)s
"""

PREFLIGHT_SQL = (
    "SELECT pg_is_in_recovery() AS replica,"
    " current_setting('transaction_read_only') AS somente_leitura,"
    " to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL"
    " AND to_regclass(%s) IS NOT NULL AND to_regclass(%s) IS NOT NULL AS tem_034"
)

#: Pedidos da conta para o baseline: so' o que a janela de 30 dias usa.
PEDIDOS_DO_BASELINE_SQL = f"""
SELECT order_sn, order_status, create_time, pay_time, pickup_done_time
FROM {PEDIDO_EXPORT_TABLE}
WHERE shop_account = %(conta)s
  AND create_time >= %(inicio)s AND create_time < %(fim)s
"""

WATERMARK_ANTERIOR_SQL = f"""
SELECT MAX(source_watermark_at) AS wm
FROM {RUN_EXPORT_TABLE}
WHERE channel = %(canal)s AND shop_account = %(conta)s
"""


def _campo(linha, nome: str, posicao: int):
    return linha[nome] if isinstance(linha, dict) else linha[posicao]


def preflight(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(PREFLIGHT_SQL, (CONTA_EXPORT_TABLE, PEDIDO_EXPORT_TABLE,
                                    FILA_EXPORT_TABLE, RUN_EXPORT_TABLE))
        linha = cur.fetchone()
    conn.rollback()
    if linha is None:
        raise PreflightFalhou("preflight do Neon nao retornou linha")
    if _campo(linha, "replica", 0):
        raise PreflightFalhou("Neon em recovery: nao e' o primary gravavel")
    if str(_campo(linha, "somente_leitura", 1)).lower() == "on":
        raise PreflightFalhou("Neon em transaction_read_only")
    if not _campo(linha, "tem_034", 2):
        raise PreflightFalhou(
            "tabelas da migration 034 ausentes no Neon: aplicar a 034 e' "
            "precondicao deste comando")


def carregar_conta(conn, conta: str) -> SellerAccount:
    """A conta do REGISTRY do export. Nunca adivinhada do arquivo."""
    with conn.cursor() as cur:
        cur.execute(CONTA_SQL, {"mkt": MARKETPLACE_ID[CANAL], "canal": CANAL.value,
                                "conta": conta})
        linhas = cur.fetchall() or []
    conn.rollback()
    if len(linhas) != 1:
        raise PreflightFalhou(f"conta {conta!r} nao cadastrada em {CONTA_EXPORT_TABLE}")
    linha = linhas[0]
    if not _campo(linha, "ativo", 4):
        raise PreflightFalhou(f"conta {conta!r} inativa no registry do export")
    if _campo(linha, "no_registry_api", 5):
        # A conta passou a vir da API: duas fontes para a mesma loja contariam
        # cada pedido duas vezes. Desative a linha do export.
        raise PreflightFalhou(
            f"conta {conta!r} ja' esta em marts.dim_seller_account (fonte API): "
            "desative-a no registry do export")
    return SellerAccount(
        external_seller_id=str(_campo(linha, "external_seller_id", 1)),
        loja_id=int(_campo(linha, "loja_id", 3)),
        brand_key=str(_campo(linha, "brand", 2)),
    )


def pedidos_do_baseline(conn, conta: str, effective_at: datetime) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(PEDIDOS_DO_BASELINE_SQL, {
            "conta": conta, "inicio": effective_at - E.JANELA_BASELINE,
            "fim": effective_at})
        linhas = cur.fetchall() or []
    conn.rollback()
    colunas = ("order_sn", "order_status", "create_time", "pay_time",
               "pickup_done_time")
    return [dict(r) if isinstance(r, dict) else dict(zip(colunas, r)) for r in linhas]


def watermark_anterior(conn, conta: str) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute(WATERMARK_ANTERIOR_SQL, {"canal": CANAL.value, "conta": conta})
        linha = cur.fetchone()
    conn.rollback()
    return _campo(linha, "wm", 0) if linha else None


def gravar_pedidos(conn, pedidos: list[dict], execute_values=None) -> None:
    if not pedidos:
        return
    if execute_values is None:
        from psycopg2.extras import execute_values as _ev  # noqa: PLC0415

        execute_values = _ev
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            execute_values(cur, UPSERT_PEDIDO_SQL,
                           [tuple(p[c] for c in COLUNAS_PEDIDO) for p in pedidos],
                           page_size=1000)
        conn.commit()
    except Exception:
        with suppress(Exception):
            conn.rollback()
        raise


def mesclar_estado(anteriores: list[dict], atuais: list[dict]) -> list[dict]:
    """Estado da tabela com o arquivo por cima: o que a carga deixaria gravado."""
    por_pedido = {p["order_sn"]: p for p in anteriores}
    for p in atuais:
        por_pedido[p["order_sn"]] = p
    return list(por_pedido.values())


def montar_fotografia(leitura: E.LeituraExport, conta: SellerAccount, shop_account: str,
                      estado: list[dict], effective_at: datetime, batch_id: str,
                      anterior: datetime | None) -> tuple[list[dict], list[dict], tuple]:
    """(fila, resumos, baseline) com a MESMA classificacao do Data Mart."""
    amostra, p50 = E.baseline(estado, effective_at)
    registry = {conta.external_seller_id: conta}
    fila = transform.build_fila_shopee(
        E.linhas_de_backlog(leitura.pedidos), registry,
        {conta.external_seller_id: (amostra, p50)}, effective_at, batch_id)
    avancou = source_advanced({shop_account: leitura.ingerido_em},
                              {shop_account: anterior} if anterior else {})
    resumos = transform.build_account_summaries(
        fila, effective_at, channel=CANAL.value, refresh_batch_id=batch_id,
        accounts={conta.external_seller_id: (shop_account, conta.brand_key)},
        watermarks={conta.external_seller_id: leitura.ingerido_em},
        source_advanced=avancou)
    return fila, resumos, (amostra, p50)


def _dia_brt(instante: datetime):
    return instante.astimezone(E.FUSO_DO_EXPORT).date()


def relatorio(leitura: E.LeituraExport, fila: list[dict], resumos: list[dict],
              base: tuple, effective_at: datetime) -> str:
    """Numeros para o operador conferir com o Seller Centre. Nenhum pedido citado."""
    def n(x) -> str:
        return f"{x:,}".replace(",", ".")

    status = sorted({p["status_export"] for p in leitura.pedidos})
    por_status = {s: sum(1 for p in leitura.pedidos if p["status_export"] == s)
                  for s in status}
    etapas = {"a_preparar": 0, "aguardando_coleta": 0}
    for r in fila:
        etapas[r["etapa"]] = etapas.get(r["etapa"], 0) + 1
    hoje = _dia_brt(effective_at)
    futuros = sorted({_dia_brt(r["dispatch_deadline"]) for r in fila
                      if r["dispatch_deadline"] is not None
                      and r["deadline_status"] != "overdue"
                      and _dia_brt(r["dispatch_deadline"]) > hoje})
    proximo = futuros[0] if futuros else None

    def vencem(dia) -> int:
        return sum(1 for r in fila if r["dispatch_deadline"] is not None
                   and r["deadline_status"] != "overdue"
                   and _dia_brt(r["dispatch_deadline"]) == dia)

    resumo = resumos[0]
    idade = (effective_at - leitura.ingerido_em).total_seconds() / 3600
    linhas = [
        f"Export: {leitura.tipo}, pedidos criados de {leitura.de:%d/%m/%Y} a "
        f"{leitura.ate:%d/%m/%Y}, {len(leitura.arquivos)} parte(s), "
        f"{n(leitura.linhas)} linha(s), {n(len(leitura.pedidos))} pedido(s); "
        f"baixado ha' {idade:.1f} h",
        "Status no export: " + ", ".join(f"{s or '(vazio)'} {n(q)}"
                                          for s, q in por_status.items()),
        f"Fila da Expedicao: {n(len(fila))} pedido(s) — A preparar "
        f"{n(etapas['a_preparar'])}, Aguardando coleta {n(etapas['aguardando_coleta'])}",
        f"Vencidos {n(resumo['overdue_count'])}, vencem hoje {n(vencem(hoje))}"
        + (f", vencem em {proximo:%d/%m} {n(vencem(proximo))}" if proximo else "")
        + f", mais de 48 h {n(resumo['over_48h_count'])}, travados "
        f"{n(resumo['stalled_count'])}",
        f"Baseline de lentidao: {n(base[0])} pedido(s) coletado(s) em 30 dias, "
        + (f"mediana {base[1]:.1f} h" if base[1] is not None else "sem mediana"),
    ]
    if leitura.status_desconhecidos:
        linhas.append("Status sem mapeamento (fora da fila; avise o time): "
                      + ", ".join(f"{s} {n(q)}" for s, q
                                  in sorted(leitura.status_desconhecidos.items())))
    linhas += [f"Aviso: {a}" for a in leitura.avisos]
    return "\n".join(linhas)


@contextmanager
def lock_do_export(conn):
    """Lock de SESSAO, nao bloqueante. Mesma semantica de `channel_lock`."""
    anterior = conn.autocommit
    conn.autocommit = True
    adquirido = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS ok",
                        (EXPORT_ADVISORY_LOCK_KEY,))
            adquirido = bool(_campo(cur.fetchone(), "ok", 0))
        if not adquirido:
            raise LockNotAcquired(
                "carga do export da Shopee ja' em andamento "
                f"(advisory key {EXPORT_ADVISORY_LOCK_KEY})")
        yield
    finally:
        # Liberar e' cleanup: nunca substitui a excecao que trouxe ate' aqui.
        with suppress(Exception):
            if adquirido:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)",
                                (EXPORT_ADVISORY_LOCK_KEY,))
        with suppress(Exception):
            conn.autocommit = anterior


def _uuid() -> str:
    import uuid  # noqa: PLC0415

    return str(uuid.uuid4())


def executar(arquivos: list[Path], shop_account: str, effective_at: datetime, *,
             aplicar: bool, max_idade: timedelta,
             open_target=open_target_default, open_audit=open_audit_default,
             uuid_factory=_uuid, execute_values=None, saida=print,
             log=lambda m: print(m, file=sys.stderr)) -> int:
    """Uma carga (ou previa). Devolve o exit code; nunca faz retry."""
    target = auditoria = None
    run_id: int | None = None
    publicado = False
    extraidas = 0
    try:
        target = open_target()
        preflight(target)
        conta = carregar_conta(target, shop_account)
        leitura = E.ler_export(arquivos, shop_account=shop_account,
                               shop_id=conta.external_seller_id,
                               agora=effective_at, max_idade=max_idade)
        extraidas = len(leitura.pedidos)
        batch_id = uuid_factory()

        if not aplicar:
            estado = mesclar_estado(
                pedidos_do_baseline(target, shop_account, effective_at), leitura.pedidos)
            fila, resumos, base = montar_fotografia(
                leitura, conta, shop_account, estado, effective_at, batch_id,
                watermark_anterior(target, shop_account))
            saida(relatorio(leitura, fila, resumos, base, effective_at))
            saida("PREVIA: nada foi gravado. Rode de novo com --apply para publicar.")
            return EXIT_OK

        with lock_do_export(target):
            auditoria = open_audit()
            run_id = audit_mod.audit_start(auditoria, FONTE_AUDITORIA,
                                           MARKETPLACE_ID[CANAL])
            anterior = watermark_anterior(target, shop_account)
            gravar_pedidos(target, leitura.pedidos, execute_values)
            estado = pedidos_do_baseline(target, shop_account, effective_at)
            fila, resumos, base = montar_fotografia(
                leitura, conta, shop_account, estado, effective_at, batch_id, anterior)
            linhas, _contas = publish_channel(
                target, CANAL, fila, resumos,
                source_health=SourceHealth.HEALTHY,
                refresh_batch_id=batch_id,
                expected_accounts=frozenset({shop_account}),
                execute_values=execute_values,
                destino=Destino(fila=FILA_EXPORT_TABLE, resumo=RUN_EXPORT_TABLE,
                                contas=(shop_account,)),
            )
            publicado = True
            try:
                audit_mod.finish_after_commit(auditoria, run_id, extraidas, linhas)
            except Exception as exc:  # noqa: BLE001 — pos-commit: nunca vira failed
                saida(relatorio(leitura, fila, resumos, base, effective_at))
                log("publicacao COMMITADA e auditoria incompleta (nenhuma reversao "
                    f"ocorreu): {audit_mod.sanitize_error_message(exc)}")
                return EXIT_AUDITORIA_INCOMPLETA

        saida(relatorio(leitura, fila, resumos, base, effective_at))
        saida(f"PUBLICADO: {linhas} pedido(s) na fila de {shop_account}, batch {batch_id}")
        return EXIT_OK

    except LockNotAcquired as exc:
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_LOCK_OCUPADO
    except PreflightFalhou as exc:
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_PRECONDICAO
    except E.ExportInvalido as exc:
        _falhar(auditoria, run_id, exc, publicado, extraidas, log)
        log(f"EXPORT RECUSADO: {exc}")
        return EXIT_FONTE_NAO_PUBLICAVEL
    except IndeterminateCommit as exc:
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_COMMIT_INDETERMINADO
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI
        if publicado:
            log("publicacao COMMITADA e finalizacao incompleta (nenhuma reversao "
                f"ocorreu): {audit_mod.sanitize_error_message(exc)}")
            return EXIT_AUDITORIA_INCOMPLETA
        _falhar(auditoria, run_id, exc, publicado, extraidas, log)
        log(f"FALHA: {audit_mod.sanitize_error_message(exc)}")
        return EXIT_FALHA
    finally:
        for conn in (auditoria, target):
            if conn is not None:
                with suppress(Exception):
                    conn.close()


def _falhar(auditoria, run_id, exc, publicado, extraidas, log) -> None:
    """`failed` SO quando a falha e' comprovadamente anterior ao commit."""
    if auditoria is None or run_id is None or publicado:
        return
    try:
        audit_mod.audit_finish(auditoria, run_id, "failed", extraidas, 0,
                               error=audit_mod.sanitize_error_message(exc))
    except Exception as falha:  # noqa: BLE001 — rastro, nao desfecho
        log(f"auditoria de falha nao registrada: {audit_mod.sanitize_error_message(falha)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipelines.expedicao.shopee_export_cli",
        description="Publica a fila da Expedicao Shopee de uma conta a partir do "
                    "export de pedidos do Seller Centre. Sem --apply e' previa.")
    p.add_argument("--conta", required=True,
                   help="shop_account cadastrada em marts.expedicao_conta_export (ex.: kokeshi)")
    p.add_argument("--arquivo", required=True, action="append", type=Path,
                   help="arquivo do export; repita para cada parte (part_1_of_N...)")
    p.add_argument("--apply", action="store_true", help="grava e publica")
    p.add_argument("--max-idade-horas", type=float, default=MAX_IDADE_PADRAO_HORAS,
                   help="recusa download mais velho que isto (padrao: 3)")
    return p


def main(argv: list[str] | None = None) -> int:
    # `parse_args` ANTES do `.env`: `--help` e argumento invalido nao leem segredo.
    args = build_parser().parse_args(argv)
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))
    faltando = [str(a) for a in args.arquivo if not a.is_file()]
    if faltando:
        print(f"arquivo nao encontrado: {', '.join(faltando)}", file=sys.stderr)
        return EXIT_PRECONDICAO
    return executar(args.arquivo, args.conta.strip().lower(),
                    datetime.now(timezone.utc), aplicar=args.apply,
                    max_idade=timedelta(hours=args.max_idade_horas))


if __name__ == "__main__":
    sys.exit(main())
