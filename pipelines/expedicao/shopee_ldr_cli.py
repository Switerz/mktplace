"""CLI da LDR operacional da Shopee (coleta x prazo Shopee).

    python -m pipelines.expedicao.shopee_ldr_cli --diagnose
    python -m pipelines.expedicao.shopee_ldr_cli --reconcile
    python -m pipelines.expedicao.shopee_ldr_cli --apply [--max-idade-fonte-horas H]

Entrada SEPARADA da `cli.py` (fila Shopee/ML) e da `tiktok_cli.py`, pelo mesmo
motivo daquela separacao: outro grao, outro DELETE, e nenhuma mudanca aqui pode
arriscar os canais ja' em producao. O lock tambem e' PROPRIO (`LOCK_KEY`): a
LDR rodando nunca faz o refresh da fila Shopee sair com "lock ocupado".

O QUE O --apply GARANTE
------------------------
1. Le a fonte num UNICO snapshot (transacao REPEATABLE READ, read-only) e
   classifica por DUAS implementacoes independentes — `classificar` em Python e
   um CASE em SQL. Se discordarem em qualquer celula, nada e' publicado.
2. Recusa (exit 3, nada escrito) se a leitura mais recente de TODAS as contas
   for mais velha que `--max-idade-fonte-horas`. Conta parada sozinha nao
   bloqueia as outras: seus vencimentos simplesmente nao amadurecem.
3. DELETE da janela + INSERT + releitura na MESMA transacao; a releitura tem
   de bater com o calculado antes do commit.
4. `audit.source_sync_run` abre antes e fecha depois; os alertas de qualidade
   (motivo de cancelamento desconhecido, cobertura, reconciliacao) vao para
   `audit.data_quality_check`.

CODIGOS DE SAIDA (identicos aos da `cli.py` e da `tiktok_cli.py`):
    0 ok · 1 falha · 2 lock ocupado · 3 fonte nao publicavel
    4 commit indeterminado · 5 auditoria incompleta · 6 precondicao
`--reconcile` sai com 1 quando ha' divergencia em vencimento maduro.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pipelines.expedicao import audit as audit_mod
from pipelines.expedicao import shopee_ldr as L
from pipelines.expedicao.contract import MARKETPLACE_ID
from pipelines.expedicao.publisher import IndeterminateCommit, LockNotAcquired
from pipelines.expedicao.shopee_extract import load_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_TARGET = "DATABASE_URL"
ENV_SOURCE = "DATAMART_DATABASE_URL"

EXIT_OK = 0
EXIT_FALHA = 1
EXIT_LOCK_OCUPADO = 2
EXIT_FONTE_NAO_PUBLICAVEL = 3
EXIT_COMMIT_INDETERMINADO = 4
EXIT_AUDITORIA_INCOMPLETA = 5
EXIT_PRECONDICAO = 6

MAX_IDADE_FONTE_HORAS_TETO = 72

CHECK_MOTIVO = "expedicao_shopee_ldr_motivo_cancelamento"
CHECK_COBERTURA = "expedicao_shopee_ldr_cobertura"
CHECK_RECONCILIACAO = "expedicao_shopee_ldr_reconciliacao"
CHECK_CONTAS = "expedicao_shopee_ldr_contas"


class FonteNaoPublicavel(RuntimeError):
    """Fonte defasada ou sem leitura. Nada foi escrito."""


class RegistryAmbiguo(RuntimeError):
    """O registry de contas do Neon esta' ambiguo. Nada foi escrito."""


def log(msg: str) -> None:
    print(msg, flush=True)


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.2f}%".replace(".", ",")


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _conectar(url: str, *, readonly: bool, nome: str):
    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=30,
                            application_name=nome)
    try:
        conn.set_session(readonly=readonly, autocommit=True)
        yield conn
    finally:
        conn.close()


@contextmanager
def advisory_lock(conn, chave: int):
    """Lock de SESSAO no Neon, nao bloqueante. Mesma semantica de `channel_lock`."""
    anterior = conn.autocommit
    conn.autocommit = True
    adquirido = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (chave,))
            linha = cur.fetchone()
            adquirido = bool(linha["ok"] if isinstance(linha, dict) else linha[0])
        if not adquirido:
            raise LockNotAcquired(
                f"publicacao da LDR Shopee ja' em andamento (advisory key {chave})")
        yield
    finally:
        # A LIBERACAO E' CLEANUP e nunca pode SUBSTITUIR a excecao que trouxe o
        # fluxo ate' aqui (mesma regra de `publisher.channel_lock`). Com a
        # conexao morta — o caso do commit indeterminado — `cursor()` e o setter
        # de `autocommit` levantam; o lock e' de SESSAO e cai junto com ela.
        try:
            if adquirido:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (chave,))
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.autocommit = anterior
        except Exception:  # noqa: BLE001
            pass


def verificar_frescor(watermarks: dict, agora: datetime, max_horas: float) -> float:
    """Idade da leitura MAIS RECENTE entre as contas. Recusa se passar do limite.

    Ausencia de watermark recusa: ausencia nao e' frescor.
    """
    validos = [w for w in watermarks.values() if w is not None]
    if not validos:
        raise FonteNaoPublicavel("fonte sem watermark (max(ingested_at)); nada publicado")
    idade = (agora - max(validos)).total_seconds() / 3600
    if idade > max_horas:
        raise FonteNaoPublicavel(
            f"fonte defasada: leitura mais recente com {idade:.1f}h, limite "
            f"{max_horas:g}h; nada publicado")
    return idade


def ler_fonte(fonte, agora: datetime, registry: dict[str, str]):
    """Le pedidos, watermarks e a contagem SQL NUM UNICO snapshot da replica.

    Sem o snapshot unico, a replica avanca entre as leituras e Python e SQL
    divergiriam por tempo, nao por regra — a reconciliacao deixaria de provar
    alguma coisa.
    """
    import psycopg2.extensions as ext  # noqa: PLC0415

    from pipelines.expedicao.shopee_extract import fetch_baselines  # noqa: PLC0415

    hoje = L.hoje_brt(agora)
    fonte.set_session(readonly=True, autocommit=False,
                      isolation_level=ext.ISOLATION_LEVEL_REPEATABLE_READ)
    try:
        pedidos = L.ler_pedidos(fonte, hoje)
        watermarks, contas = L.ler_watermarks_e_contas(fonte)
        sql = L.ler_reconciliacao(fonte, hoje, agora)
        # Fila por fluxo: MESMO snapshot, mesmo predicado e mesma linha de base
        # de lentidao da fila publicada (`fetch_baselines` e' o da propria fila).
        fila_bruta, contagem_fila = L.ler_fila_aberta(fonte)
        baselines = fetch_baselines(fonte, agora)
    finally:
        fonte.rollback()
        fonte.set_session(readonly=True, autocommit=True)
    apuracao = L.apurar(pedidos, registry=registry, watermarks=watermarks, agora=agora)
    celulas = L.reconciliar(apuracao, sql, registry)
    apuracao.fila_fluxo = L.apurar_fila_por_fluxo(
        fila_bruta, registry=registry, contas=contas, baselines=baselines, agora=agora)
    L.reconciliar_fila(apuracao.fila_fluxo, contagem_fila, registry)
    return apuracao, watermarks, celulas, len(pedidos)


def ler_registry(neon) -> dict[str, str]:
    contas, problemas = load_registry(neon, MARKETPLACE_ID[L.CANAL])
    if problemas:
        raise RegistryAmbiguo("registry ambiguo: " + "; ".join(problemas))
    return {ext: c.brand_key for ext, c in contas.items()}


# ---------------------------------------------------------------------------
# Resumo — a MESMA conta que a serving faz
# ---------------------------------------------------------------------------
def resumo_por_marca(apuracao: L.Apuracao) -> dict[str, dict]:
    """Vencimentos MADUROS e passados da janela, por marca + cobertura."""
    out: dict[str, dict] = {}
    for x in apuracao.vencimentos:
        if not x.is_mature:
            continue
        a = out.setdefault(x.brand, {"base": 0, "atraso": 0, "fbs": 0, "cancelados": 0,
                                     "nao_enviou": 0, "enviado_sem_coleta": 0,
                                     "sem_prazo": 0})
        a["base"] += x.base
        a["atraso"] += x.atrasado + x.pendente_vencido
        a["fbs"] += x.excl_fbs
        a["cancelados"] += x.excl_cancelado_sem_coleta
        a["nao_enviou"] += x.cancelado_vendedor_nao_enviou
        a["enviado_sem_coleta"] += x.excl_enviado_sem_coleta
    for s in apuracao.sem_prazo:
        # So' pagamentos de dias FECHADOS, como a base (vencimentos maduros):
        # contar o dia de hoje inflaria a ausencia com pedido que a Shopee
        # ainda nem terminou de publicar.
        if s.brand in out and s.paid_date < apuracao.hoje:
            out[s.brand]["sem_prazo"] += s.sem_prazo
    for a in out.values():
        frac, estado = L.cobertura(a["base"], a["sem_prazo"], a["enviado_sem_coleta"])
        a["ausencia"] = frac
        a["cobertura"] = estado
        a["taxa"] = None if estado == "insuficiente" else L.taxa(a["atraso"], a["base"])
    return out


def _relatorio(apuracao: L.Apuracao, celulas: int, n_pedidos: int) -> None:
    q = apuracao.qualidade
    desde, ate = L.janela_de_vencimento(apuracao.hoje)
    log(f"DIAGNOSTICO {L.NOME_METRICA} — hoje (BRT) = {apuracao.hoje}")
    log(f"  {L.AVISO_NAO_OFICIAL}")
    log(f"  janela de vencimento: {desde} .. {ate}")
    log(f"  pedidos lidos: {n_pedidos} · linhas (vencimento x conta): "
        f"{len(apuracao.vencimentos)} · sem prazo (pagamento x conta): {len(apuracao.sem_prazo)}")
    log(f"  reconciliacao Python x SQL no mesmo snapshot: {celulas} celula(s), 0 divergencias")
    log("")
    log("  marca          base  atraso      LDR   cobertura      FBS  cancel(nao enviou)")
    for marca, a in sorted(resumo_por_marca(apuracao).items()):
        log(f"  {marca:<12}{a['base']:6d}  {a['atraso']:6d}  {_pct(a['taxa']):>7}   "
            f"{a['cobertura']:<12} {a['fbs']:6d}  {a['cancelados']}({a['nao_enviou']})")
    log("")
    log("  FILA ABERTA POR FLUXO (mesmo predicado da fila; vendedor+FBS = contagem SQL)")
    log("  marca        fluxo      backlog vencidos  24h  >48h travados")
    for x in apuracao.fila_fluxo:
        log(f"  {x.brand:<12} {x.fluxo:<9} {x.backlog:7d} {x.overdue:8d} {x.due_within_24h:4d} "
            f"{x.over_48h:5d} {x.stalled:8d}")
    log("")
    log(f"  motivos de cancelamento desconhecidos: {dict(q.motivos_desconhecidos) or 'nenhum'}")
    log(f"  motivos ausentes (nulos): {q.motivos_ausentes}")
    log(f"  status fora do vocabulario: {dict(q.status_desconhecidos) or 'nenhum'}")
    log(f"  contas do registry sem leitura na fonte: {q.contas_sem_leitura or 'nenhuma'}")
    log(f"  contas do registry com leitura defasada: {q.contas_defasadas or 'nenhuma'}")
    log(f"  contas fora do registry (nao publicadas): "
        f"{sum(q.contas_fora_do_registry.values())} pedido(s) em "
        f"{len(q.contas_fora_do_registry)} conta(s)")


# ---------------------------------------------------------------------------
# Auditoria de qualidade
# ---------------------------------------------------------------------------
def registrar_qualidade(conn, apuracao: L.Apuracao, celulas: int) -> None:
    q = apuracao.qualidade
    mkt = MARKETPLACE_ID[L.CANAL]
    linhas = []
    desconhecidos = sum(q.motivos_desconhecidos.values())
    linhas.append((
        CHECK_MOTIVO, L.TABELA, mkt,
        "warn" if desconhecidos else "pass", "medium" if desconhecidos else "low",
        desconhecidos,
        json.dumps({
            # Texto do motivo e' vocabulario da plataforma, nao dado do comprador.
            "motivos_desconhecidos": dict(q.motivos_desconhecidos),
            "motivos_ausentes": q.motivos_ausentes,
            "status_desconhecidos": dict(q.status_desconhecidos),
            "contas_fora_do_registry": len(q.contas_fora_do_registry),
            "pedidos_fora_do_registry": sum(q.contas_fora_do_registry.values()),
        }, ensure_ascii=False, sort_keys=True),
    ))
    for marca, a in sorted(resumo_por_marca(apuracao).items()):
        status, sev = {"completa": ("pass", "low"), "aviso": ("warn", "medium"),
                       "insuficiente": ("fail", "high")}.get(a["cobertura"], ("warn", "medium"))
        linhas.append((
            CHECK_COBERTURA, L.TABELA, mkt, status, sev,
            a["sem_prazo"] + a["enviado_sem_coleta"],
            json.dumps({"brand": marca, "cobertura": a["cobertura"],
                        "ausencia": a["ausencia"], "base": a["base"],
                        "sem_prazo": a["sem_prazo"],
                        "enviado_sem_coleta": a["enviado_sem_coleta"]},
                       ensure_ascii=False, sort_keys=True),
        ))
    problemas = len(q.contas_sem_leitura) + len(q.contas_defasadas)
    linhas.append((
        CHECK_CONTAS, L.TABELA, mkt,
        "warn" if problemas else "pass", "high" if q.contas_sem_leitura else
        ("medium" if problemas else "low"),
        problemas,
        json.dumps({"sem_leitura": q.contas_sem_leitura, "defasadas": q.contas_defasadas,
                    "limite_defasada_horas": L.LIMITE_CONTA_DEFASADA.total_seconds() / 3600},
                   ensure_ascii=False, sort_keys=True),
    ))
    linhas.append((
        CHECK_RECONCILIACAO, L.TABELA, mkt, "pass", "low", 0,
        json.dumps({"celulas": celulas, "implementacoes": ["python", "sql"],
                    "snapshot": "repeatable_read",
                    "fila_vendedor": sum(x.backlog for x in apuracao.fila_fluxo
                                         if x.fluxo == "vendedor"),
                    "fila_fbs": sum(x.backlog for x in apuracao.fila_fluxo if x.fluxo == "fbs"),
                    "fila_reconciliada_com_contagem_sql": True}, sort_keys=True),
    ))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO audit.data_quality_check
                (check_name, table_name, marketplace_id, status, severity,
                 failed_rows, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            linhas,
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
def publicar(alvo, apuracao: L.Apuracao, watermarks: dict):
    """DELETE das duas janelas + INSERT + releitura, numa so' transacao."""
    lote, venc, semp = L.linhas_para_publicar(apuracao, watermarks)
    fila = L.linhas_fila_para_publicar(apuracao.fila_fluxo, lote, apuracao.agora, watermarks)
    v_desde, v_ate = L.janela_de_vencimento(apuracao.hoje)
    p_desde, p_ate = L.janela_de_pagamento(apuracao.hoje)
    alvo.autocommit = False
    try:
        with alvo.cursor() as cur:
            cur.execute(L.DELETE_VENCIMENTO_SQL, {"channel": L.CANAL.value,
                        "desde": v_desde.isoformat(), "ate": v_ate.isoformat()})
            apagadas = cur.rowcount
            cur.execute(L.DELETE_SEM_PRAZO_SQL, {"channel": L.CANAL.value,
                        "desde": p_desde.isoformat(), "ate": p_ate.isoformat()})
            apagadas += cur.rowcount
            if venc:
                cur.executemany(L.INSERT_VENCIMENTO_SQL, venc)
            if semp:
                cur.executemany(L.INSERT_SEM_PRAZO_SQL, semp)
            # Fila por fluxo: fotografia, substituida inteira (DELETE do canal).
            cur.execute(L.DELETE_FILA_FLUXO_SQL, {"channel": L.CANAL.value})
            apagadas += cur.rowcount
            if fila:
                cur.executemany(L.INSERT_FILA_FLUXO_SQL, fila)
            cur.execute(L.RELEITURA_SQL, {"channel": L.CANAL.value, "lote": str(lote)})
            lido = cur.fetchone()
            lido = {k: int(lido[k]) for k in ("linhas", "pagos", "base", "atraso")}
            cur.execute(L.RELEITURA_SEM_PRAZO_SQL, {"channel": L.CANAL.value, "lote": str(lote)})
            lido_sp = cur.fetchone()
            lido.update({k: int(lido_sp[k]) for k in ("linhas_sem_prazo", "sem_prazo")})
            cur.execute(L.RELEITURA_FILA_FLUXO_SQL, {"channel": L.CANAL.value, "lote": str(lote)})
            lido_fila = cur.fetchone()
            lido.update({k: int(lido_fila[k]) for k in ("linhas_fila", "backlog_fila")})
            esperado = L.esperado_na_releitura(apuracao)
            if lido != esperado:
                raise RuntimeError(
                    f"releitura diverge do calculado: lido={lido} esperado={esperado}")
        try:
            alvo.commit()
        except Exception as exc:  # noqa: BLE001
            raise IndeterminateCommit(
                "commit indeterminado ao publicar a LDR Shopee: "
                f"{audit_mod.sanitize_error_message(exc)}") from exc
    except IndeterminateCommit:
        raise
    except Exception:
        # Rollback com a conexao morta levantaria e ESCONDERIA o erro de origem.
        try:
            alvo.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        # Depois de um commit que falhou a conexao pode estar fechada, e o
        # setter levantaria `InterfaceError` por cima do `IndeterminateCommit`:
        # o exit 4 viraria exit 1 e o runbook leria "nada publicado".
        try:
            alvo.autocommit = True
        except Exception:  # noqa: BLE001
            pass
    return lote, len(venc) + len(semp) + len(fila), apagadas


def _run_diagnose() -> int:
    fonte_url, alvo_url = os.environ.get(ENV_SOURCE, ""), os.environ.get(ENV_TARGET, "")
    if not fonte_url or not alvo_url:
        log(f"{ENV_SOURCE} e {ENV_TARGET} sao obrigatorias (o registry vem do Neon).")
        return EXIT_PRECONDICAO
    agora = _agora_utc()
    with _conectar(alvo_url, readonly=True, nome="expedicao_shopee_ldr_diag_reg") as neon:
        registry = ler_registry(neon)
    with _conectar(fonte_url, readonly=True, nome="expedicao_shopee_ldr_diag") as fonte:
        apuracao, _wm, celulas, n = ler_fonte(fonte, agora, registry)
    _relatorio(apuracao, celulas, n)
    return EXIT_OK


PUBLICADO_SQL = f"""
SELECT deadline_date, shop_id, pedidos_pagos, excl_fbs, base,
       atrasado + pendente_vencido AS atraso, is_mature, effective_at
  FROM {L.TABELA}
 WHERE channel = %(channel)s AND is_mature
   AND deadline_date >= %(desde)s::date AND deadline_date <= %(ate)s::date
"""


def _run_reconcile() -> int:
    """Publicado x fonte AGORA, so' em vencimentos maduros. Read-only nos dois.

    Compara o que nao muda depois que o vencimento amadurece: populacao, FBS,
    base e atraso total. Divergencia aqui quer dizer que a fonte mudou depois
    da publicacao (cancelamento tardio, correcao da Shopee) ou que a
    publicacao esta' errada — os dois casos pedem olhar.
    """
    fonte_url, alvo_url = os.environ.get(ENV_SOURCE, ""), os.environ.get(ENV_TARGET, "")
    if not fonte_url or not alvo_url:
        log(f"{ENV_SOURCE} e {ENV_TARGET} sao obrigatorias.")
        return EXIT_PRECONDICAO
    agora = _agora_utc()
    with _conectar(alvo_url, readonly=True, nome="expedicao_shopee_ldr_rec_dst") as neon:
        registry = ler_registry(neon)
        desde, _ = L.janela_de_vencimento(L.hoje_brt(agora))
        with neon.cursor() as cur:
            cur.execute(PUBLICADO_SQL, {"channel": L.CANAL.value, "desde": desde.isoformat(),
                                        "ate": L.hoje_brt(agora).isoformat()})
            publicado = {(str(r["shop_id"]), r["deadline_date"]): r for r in cur.fetchall()}
    with _conectar(fonte_url, readonly=True, nome="expedicao_shopee_ldr_rec_src") as fonte:
        apuracao, _wm, _c, _n = ler_fonte(fonte, agora, registry)
    agora_ = {(x.shop_id, x.deadline_date): x for x in apuracao.vencimentos}
    divergencias = []
    for k, p in sorted(publicado.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        x = agora_.get(k)
        atual = None if x is None else (x.pedidos_pagos, x.excl_fbs, x.base,
                                        x.atrasado + x.pendente_vencido)
        pub = (int(p["pedidos_pagos"]), int(p["excl_fbs"]), int(p["base"]), int(p["atraso"]))
        if atual != pub:
            divergencias.append((k, pub, atual))
    log(f"RECONCILIACAO publicado x fonte — {len(publicado)} vencimento(s) maduro(s)")
    for (shop, dia), pub, atual in divergencias[:20]:
        log(f"  {dia} {shop}: publicado(pagos,fbs,base,atraso)={pub} fonte={atual}")
    log(f"  divergencias: {len(divergencias)}")
    return EXIT_OK if not divergencias else EXIT_FALHA


def _run_apply(max_idade_fonte_horas: float | None) -> int:
    alvo_url, fonte_url = os.environ.get(ENV_TARGET, ""), os.environ.get(ENV_SOURCE, "")
    if not alvo_url or not fonte_url:
        log(f"{ENV_TARGET} e {ENV_SOURCE} sao obrigatorias.")
        return EXIT_PRECONDICAO

    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    alvo = psycopg2.connect(alvo_url, cursor_factory=RealDictCursor, connect_timeout=30,
                            application_name="expedicao_shopee_ldr_apply")
    auditoria = psycopg2.connect(alvo_url, cursor_factory=RealDictCursor, connect_timeout=30,
                                 application_name="expedicao_shopee_ldr_audit")
    run_id: int | None = None
    publicado = False
    extraidos = 0

    def _falhar(exc: BaseException, codigo: int) -> int:
        if run_id is not None:
            try:
                audit_mod.audit_finish(auditoria, run_id, "failed", rows_extracted=extraidos,
                                       rows_loaded=None,
                                       error=audit_mod.sanitize_error_message(exc))
            except Exception:  # noqa: BLE001
                log("auditoria nao pode ser fechada; execucao fica em 'running'.")
                return EXIT_AUDITORIA_INCOMPLETA
        return codigo

    try:
        alvo.autocommit = True
        auditoria.autocommit = False
        with advisory_lock(alvo, L.LOCK_KEY):
            run_id = audit_mod.audit_start(auditoria, L.SOURCE_NAME, MARKETPLACE_ID[L.CANAL])
            agora = _agora_utc()
            registry = ler_registry(alvo)
            with _conectar(fonte_url, readonly=True, nome="expedicao_shopee_ldr_src") as fonte:
                apuracao, watermarks, celulas, extraidos = ler_fonte(fonte, agora, registry)
            if max_idade_fonte_horas is not None:
                idade = verificar_frescor(watermarks, agora, max_idade_fonte_horas)
                log(f"  fonte fresca: leitura mais recente com {idade:.2f}h "
                    f"(limite {max_idade_fonte_horas:g}h)")
            if not apuracao.vencimentos:
                raise FonteNaoPublicavel("fonte sem vencimento na janela; nada publicado")

            lote, gravadas, apagadas = publicar(alvo, apuracao, watermarks)
            publicado = True
            log(f"PUBLICADO lote={lote} linhas={gravadas} (substituiu {apagadas}) "
                f"· reconciliacao {celulas} celula(s)")
            try:
                registrar_qualidade(auditoria, apuracao, celulas)
                audit_mod.finish_after_commit(auditoria, run_id, extraidos, gravadas)
            except Exception as exc:  # noqa: BLE001
                log(f"auditoria incompleta apos publicar: "
                    f"{audit_mod.sanitize_error_message(exc)}")
                return EXIT_AUDITORIA_INCOMPLETA
        return EXIT_OK

    except LockNotAcquired as exc:
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_LOCK_OCUPADO
    except FonteNaoPublicavel as exc:
        log(f"RECUSADO: {audit_mod.sanitize_error_message(exc)}")
        return _falhar(exc, EXIT_FONTE_NAO_PUBLICAVEL)
    except RegistryAmbiguo as exc:
        log(f"PRECONDICAO: {audit_mod.sanitize_error_message(exc)}")
        return _falhar(exc, EXIT_PRECONDICAO)
    except IndeterminateCommit as exc:
        log(audit_mod.sanitize_error_message(exc))
        return EXIT_COMMIT_INDETERMINADO
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI
        log(f"FALHA: {audit_mod.sanitize_error_message(exc)}")
        codigo = _falhar(exc, EXIT_FALHA)
        return codigo if not publicado else EXIT_AUDITORIA_INCOMPLETA
    finally:
        alvo.close()
        auditoria.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="expedicao-shopee-ldr",
        description=f"{L.NOME_METRICA}. {L.AVISO_NAO_OFICIAL}",
    )
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument("--diagnose", action="store_true",
                      help="le fonte e registry e imprime a apuracao; nao escreve")
    modo.add_argument("--reconcile", action="store_true",
                      help="compara o publicado com a fonte atual; nao escreve")
    modo.add_argument("--apply", action="store_true",
                      help="publica a janela no Neon. A flag E a confirmacao.")
    p.add_argument("--max-idade-fonte-horas", type=float, default=None,
                   help="so' com --apply: recusa (exit 3) se a leitura mais recente "
                        "da fonte for mais velha que isto")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        # argparse sai com 2, que aqui significa "lock ocupado". Erro de uso e'
        # falha comum: quem repetisse um exit 2 repetiria a flag errada.
        return EXIT_FALHA if exc.code else EXIT_OK
    if args.max_idade_fonte_horas is not None:
        if not args.apply:
            print("--max-idade-fonte-horas so' vale com --apply.", file=sys.stderr)
            return EXIT_FALHA
        if not 0 < args.max_idade_fonte_horas <= MAX_IDADE_FONTE_HORAS_TETO:
            print(f"--max-idade-fonte-horas deve estar em (0, {MAX_IDADE_FONTE_HORAS_TETO}].",
                  file=sys.stderr)
            return EXIT_FALHA

    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    if args.apply:
        return _run_apply(args.max_idade_fonte_horas)
    try:
        return _run_diagnose() if args.diagnose else _run_reconcile()
    except Exception as exc:  # noqa: BLE001
        print(f"FALHA: {audit_mod.sanitize_error_message(exc)}", file=sys.stderr)
        return EXIT_FALHA


if __name__ == "__main__":
    raise SystemExit(main())
