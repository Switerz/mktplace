"""ML-FULL-STOCK-PRODUCTIZATION-1 — publica o estoque Full do ML no Neon. INERTE.

Le a fotografia do airflow (`raw.ml_full_*`, Data Mart, numa unica transacao
REPEATABLE READ READ ONLY: snapshot, status, ponte, vendas e runs no MESMO
instante) pelo mesmo leitor do dry-run (`sync_ml_full_stock_daily.ler_fonte`), calcula
os indicadores por inventario (`pipelines.transforms.ml_full_stock`) e, SO'
com as travas abaixo, publica nas fatos da migration 029.

TRAVAS PARA ESCREVER (todas obrigatorias)
-----------------------------------------
1. `--apply` na linha de comando;
2. `ML_FULL_STOCK_PUBLISH_ENABLED=true` no ambiente;
3. as tres tabelas da 029 existindo no Neon (`to_regclass`);
4. contrato do chamador (T-0019): `--marca` (allowlist), um `--raw-run
   <marca>=<run_id>` por marca pedida e `--publish-run-id <uuid hex 32>`;
5. `--destino-esperado <arquivo.json>`: o arquivo aprovado gerado pelo
   `-Identificar` do pacote humano. Ausente/ilegivel, ou sem identificador
   FORTE (`system_identifier` ou o par `neon_tenant_id`+`neon_timeline_id`),
   = recusa antes de qualquer conexao de escrita.
Sem qualquer uma delas o run e' dry-run (ou recusa explicita), sem escrita.

CONTRATO DO APPLY
-----------------
- A fonte e' FILTRADA para as marcas pedidas antes de montar o pacote. Marca
  com fotografia que nao foi pedida e' ignorada e reportada; nunca e' gravada.
  Marca pedida sem fotografia = recusa.
- Linhagem RAW: toda linha lida (snapshot, ponte e status) da marca/data tem
  o `run_id` informado, e em `raw.ml_full_stock_runs` esse run pediu a marca,
  comecou na data (fuso de Sao Paulo, mesmo relogio do `ref_date` do
  airflow), terminou (`completo`/`parcial`) e registrou `PUBLICADA` para ela.
  Qualquer divergencia = recusa ANTES de qualquer DELETE/INSERT.
- Coerencia da fotografia do run, por marca pedida (`conferir_coerencia`,
  regras do produtor): snapshot e ponte com >= 1 linha do run, todo
  inventario do snapshot na ponte e todo inventario do status no snapshot.
  Falha = `fonte_incoerente`, recusa `nao_iniciada`.
- `--publish-run-id` vira o `source_run_id` das linhas. Id ja' presente em
  qualquer fato = recusa (checado antes e de novo dentro do lock).

PUBLICACAO
----------
- `engine.begin()`: uma transacao; `pg_try_advisory_xact_lock` e' o PRIMEIRO
  comando (devolvido pelo banco no COMMIT e no ROLLBACK).
- Logo depois do lock, ANTES de qualquer DELETE, a impressao digital do banco
  (mesmas queries do `identificar` do helper) e' lida NA conexao da transacao e
  comparada campo a campo com `identidade.alvo_neon` (null so' bate com null).
  Destino sem identificador forte = `destino_nao_identificavel`; divergencia =
  `destino_divergente` com o NOME do campo; ambos `revertida_comprovada`.
- So' as marcas pedidas. DELETE do (ref_date, marca) + INSERT: reexecutar
  substitui, nunca duplica, e nunca toca marca nao pedida.
- Reconciliacao PRE-COMMIT: linhas e soma do disponivel no destino tem de
  bater com o calculado; qualquer diferenca aborta.
- Auditoria em `audit.source_sync_run` numa conexao PROPRIA: o rollback das
  fatos nao apaga o registro de que a execucao existiu e falhou. As DUAS
  conexoes da auditoria conferem a mesma identidade ANTES do INSERT/UPDATE:
  nenhuma escrita (fato ou auditoria) vai para destino nao aprovado. No
  `audit_start` a recusa e' `nao_iniciada`; no `audit_finish` vira
  `auditoria_ok=false` com a causa.

ESTADO DA TRANSACAO (JSON `estado_transacao`)
---------------------------------------------
- `nao_iniciada`: recusa/erro antes de abrir a transacao de escrita.
- `revertida_comprovada`: excecao DENTRO do bloco, antes de o COMMIT ser
  enviado; o `engine.begin()` fez rollback (o COMMIT nunca saiu do cliente).
- `commit_confirmado`: o bloco `engine.begin()` saiu sem excecao. Falha da
  auditoria depois disso NAO vira "falha": `auditoria_ok=false` e exit 5.
- `indeterminado`: a excecao veio do proprio COMMIT. Quem chama usa
  `--diagnosticar --ref-date D --publish-run-id X` (so' leitura).

SAIDA
-----
stdout traz SO' o JSON final; logs vao para stderr (os handlers que apontam
para o stdout sao redirecionados e qualquer `print` durante o run tambem).
Exit: 0 ok | 2 recusa (nada gravado) | 5 commit confirmado com pendencia de
auditoria | 1 erro (ver `estado_transacao`).
"""
from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import logging
import os
import re
import sys
import uuid
from datetime import date, datetime

from psycopg2.extras import execute_values
from sqlalchemy import text

from pipelines import sync_ml_full_stock_daily as fonte_mod
from pipelines.common.db import DataMartSession, LocalSession, local_engine
from pipelines.common.logging import get_logger
from pipelines.transforms import ml_full_stock as T

logger = get_logger(__name__)

FACT_INVENTARIO = "marts.fact_ml_full_stock_inventory_daily"
FACT_STATUS = "marts.fact_ml_full_stock_status_daily"
FACT_MARCA = "marts.fact_ml_full_stock_brand_daily"
TABELAS = (FACT_INVENTARIO, FACT_STATUS, FACT_MARCA)
FLAG_AMBIENTE = "ML_FULL_STOCK_PUBLISH_ENABLED"

ADVISORY_LOCK_KEY = 916140029
TARGET_STATEMENT_TIMEOUT_MS = 180_000
PAGE_SIZE = 500
#: `audit.source_sync_run.marketplace_id`: 1=tiktok, 2=ml, 3=shopee (documentado
#: em `sync_serving_snapshots.py` e no seed `db/seeds/01_marketplaces.sql`).
#: ⚠️ `sync_ml_fulfillment_daily` usa 1 para o ML: defeito a corrigir la'.
AUDIT_MARKETPLACE_ID = 2
AUDIT_SOURCE = "ml_full_stock_daily"
AUDIT_ERRO_MAX_CHARS = 4000

#: Contrato do chamador (T-0019).
PUBLISH_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RAW_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")      # raw.*.run_id e' VARCHAR(36)
#: `raw.ml_full_stock_runs`: status do run (DDL do airflow) e desfecho por marca
#: (airflow `snapshot.py`: PUBLICADA so' depois do DELETE+INSERT da marca).
RUN_STATUS_TERMINADO = ("completo", "parcial")
DESFECHO_PUBLICADA = "PUBLICADA"

ESTADO_NAO_INICIADA = "nao_iniciada"
ESTADO_REVERTIDA = "revertida_comprovada"
ESTADO_COMMIT = "commit_confirmado"
ESTADO_INDETERMINADO = "indeterminado"

EXIT_OK, EXIT_ERRO, EXIT_RECUSA, EXIT_COMMIT_COM_PENDENCIA = 0, 1, 2, 5

COLS_INV = ("ref_date", "brand", "seller_id", "inventory_id", "read_status", "disponivel",
            "nao_disponivel", "transfer", "total_coerente", "detalhe_coerente", "eh_full", "anuncios",
            "anuncios_ativos", "anuncios_full", "unidades_28d", "media_diaria_28d",
            "cobertura_torre_dias", "classificacao_torre", "vinculo_vendas", "source_run_id")
COLS_STATUS = ("ref_date", "brand", "seller_id", "inventory_id", "status_raw", "quantidade",
               "source_run_id")
COLS_MARCA = ("ref_date", "brand", "inventarios", "inventarios_full_lidos",
              "inventarios_compartilhados", "disponivel_full", "nao_disponivel_full",
              "transfer_full", "unidades_28d_alocadas", "unidades_28d_item_varios_inventarios",
              "unidades_28d_sem_inventory_id", "unidades_28d_fora_da_ponte",
              "cobertura_da_alocacao", "source_run_id")

SQL_RUNS_PRESENCA = text("SELECT to_regclass('raw.ml_full_stock_runs') IS NOT NULL")
SQL_RUNS = text("""
SELECT run_id, started_at, brands_requested, status, outcome_by_brand
  FROM raw.ml_full_stock_runs
 WHERE run_id = ANY(:runs)
""")
#: Id de publicacao ja' usado em QUALQUER data/marca/fato.
SQL_PUBLISH_RUN_ID_EM_USO = text("SELECT " + " OR ".join(
    f"EXISTS (SELECT 1 FROM {t} WHERE source_run_id = :publish_run_id)" for t in TABELAS))


class PublicacaoRecusada(RuntimeError):
    """Trava ou contrato nao satisfeito: nada foi gravado pelo publicador."""


class ConcurrentRunError(RuntimeError):
    pass


class ReconciliacaoFalhou(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Erro sanitizado: classe + motivo, sem host/DSN/usuario/IP
# ---------------------------------------------------------------------------
_REDACOES = (
    (re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I), "<url>"),
    (re.compile(r"\b(host|hostaddr|user|password|dbname|port|sslrootcert|sslkey|sslcert|service|passfile)"
                r"\s*=\s*('[^']*'|\"[^\"]*\"|\S+)", re.I), r"\1=<redigido>"),
    (re.compile(r'\b(server at|host|user|role|database)\s+"[^"]*"', re.I), r'\1 "<redigido>"'),
    (re.compile(r"\bport\s+\d+", re.I), "port <redigido>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|tech|io|aws|cloud|internal|local|br|dev)\b", re.I),
     "<host>"),
)


def _motivo(exc) -> str:
    """Primeira linha nao vazia da mensagem, sem host/DSN/usuario/IP."""
    linhas = [l.strip() for l in str(exc).splitlines() if l.strip()]
    msg = linhas[0] if linhas else ""
    for padrao, troca in _REDACOES:
        msg = padrao.sub(troca, msg)
    return msg[:AUDIT_ERRO_MAX_CHARS]


def sanitizar(exc) -> str:
    return f"{type(exc).__name__}: {_motivo(exc)}"[:AUDIT_ERRO_MAX_CHARS]


def flag_ligada() -> bool:
    return os.environ.get(FLAG_AMBIENTE, "").strip().lower() == "true"


# ---------------------------------------------------------------------------
# Destino esperado: impressao digital NAO secreta do Neon (T-0019, fase 3)
# ---------------------------------------------------------------------------
#: Mesmo formato, mesmas queries e mesma normalizacao do `identificar` do helper
#: do pacote humano (`ml_full_neon_db.py`): o arquivo aprovado por `-Identificar`
#: e' `{"identidade": {"alvo_neon": {...}, "fonte_datamart": {...}}, ...}` e o
#: publicador usa so' `identidade.alvo_neon`.
DESTINO_LADO = "alvo_neon"


def carregar_destino_esperado(caminho) -> dict:
    """`identidade.alvo_neon` do arquivo aprovado. Ausente/ilegivel = recusa (sem conexao)."""
    if not caminho:
        raise PublicacaoRecusada("--apply exige --destino-esperado <arquivo aprovado por -Identificar>")
    try:
        with open(caminho, encoding="utf-8-sig") as f:
            ident = json.load(f).get("identidade")
    except Exception:
        raise PublicacaoRecusada("destino_esperado_ilegivel: arquivo ausente ou nao e' o JSON do identificar") \
            from None
    esperado = ident.get(DESTINO_LADO) if isinstance(ident, dict) else None
    if not isinstance(esperado, dict) or not esperado:
        raise PublicacaoRecusada(f"destino_esperado_sem_identidade: falta identidade.{DESTINO_LADO}")
    if not identificador_forte(esperado):
        raise PublicacaoRecusada(f"destino_nao_identificavel: o arquivo aprovado nao tem {IDENT_FORTE_TEXTO}")
    return esperado


#: Identificador FORTE: os metadados genericos (banco, versao, recuperacao,
#: alembic, modo) se repetem entre servidores distintos; sozinhos nunca provam
#: que o destino e' o aprovado. Exige-se `system_identifier` ou o par Neon
#: `neon_tenant_id` + `neon_timeline_id`, nao nulos, no arquivo E no destino vivo.
IDENT_FORTE_TEXTO = "system_identifier nem o par neon_tenant_id+neon_timeline_id"


def identificador_forte(ident: dict) -> bool:
    tem = lambda k: ident.get(k) not in (None, "")
    return tem("system_identifier") or (tem("neon_tenant_id") and tem("neon_timeline_id"))


def _host_loopback(host) -> bool:
    """Mesma regra do helper; socket (host vazio ou diretorio) conta como local."""
    if not host:
        return True
    h = str(host).strip().strip("[]").lower()
    if h.startswith("/") or h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def _um(cur, q):
    cur.execute(q)
    return cur.fetchone()[0]


def _opcional(cur, q):
    """Campo que o papel pode nao ter permissao de ler: erro vira null, sem abortar a transacao."""
    cur.execute("SAVEPOINT ml_full_ident")
    try:
        cur.execute(q)
        v = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT ml_full_ident")
        return None if v is None or v[0] is None else str(v[0])
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT ml_full_ident")
        return None


def identidade_destino(cur) -> dict:
    """Impressao digital do banco do cursor, identica a `ml_full_neon_db.identidade`.

    `modo` nao vem de argumento: e' "teste" quando o host REAL da conexao e'
    loopback/socket (o helper so' aceita loopback no modo teste e recusa no real)
    e "real" fora disso. O `localhost` padrao do config.py nunca confere com um
    arquivo real.
    """
    host = getattr(getattr(cur.connection, "info", None), "host", None)
    r = {"modo": "teste" if _host_loopback(host) else "real",
         "current_database": _um(cur, "SELECT current_database()"),
         "server_version_num": _um(cur, "SELECT current_setting('server_version_num')"),
         "em_recuperacao": bool(_um(cur, "SELECT pg_is_in_recovery()")),
         "system_identifier": None,
         "neon_tenant_id": _opcional(cur, "SELECT current_setting('neon.tenant_id', true)"),
         "neon_timeline_id": _opcional(cur, "SELECT current_setting('neon.timeline_id', true)"),
         "alembic_version_num": None}
    if _um(cur, "SELECT has_function_privilege('pg_catalog.pg_control_system()', 'EXECUTE')"):
        r["system_identifier"] = _opcional(cur, "SELECT system_identifier FROM pg_catalog.pg_control_system()")
    if _um(cur, "SELECT to_regclass('public.alembic_version') IS NOT NULL"):
        r["alembic_version_num"] = _opcional(
            cur, "SELECT string_agg(version_num::text, ',' ORDER BY version_num) FROM public.alembic_version")
    return r


def _igual(x, y) -> bool:
    if x is None or y is None:
        return x is None and y is None
    return type(x) is type(y) and x == y


def campos_divergentes(atual: dict, esperado: dict) -> list[str]:
    """Nomes dos campos que nao batem (null so' com null). Sem valores: nada sensivel sai."""
    return [k for k in sorted(set(atual) | set(esperado))
            if k not in atual or k not in esperado or not _igual(atual[k], esperado[k])]


def conferir_identidade(cur, esperado: dict) -> None:
    """No cursor da conexao que VAI escrever: destino identificavel e igual ao aprovado, ou recusa.

    Usada nas tres conexoes que escrevem no Neon: a transacao das fatos e as
    duas da auditoria (`audit_start` e `audit_finish`).
    """
    if not esperado or not identificador_forte(esperado):     # cinto: o carregador ja' recusa
        raise PublicacaoRecusada(f"destino_nao_identificavel: o arquivo aprovado nao tem {IDENT_FORTE_TEXTO}")
    atual = identidade_destino(cur)
    if not identificador_forte(atual):
        raise PublicacaoRecusada(f"destino_nao_identificavel: o destino nao expoe {IDENT_FORTE_TEXTO}")
    div = campos_divergentes(atual, esperado)
    if div:
        raise PublicacaoRecusada("destino_divergente: " + ", ".join(f"{DESTINO_LADO}.{k}" for k in div))


def conferir_destino(conn, esperado: dict) -> None:
    """Na conexao DA TRANSACAO de escrita: o banco e' o aprovado, ou recusa."""
    with conn.connection.cursor() as cur:
        conferir_identidade(cur, esperado)


# ---------------------------------------------------------------------------
# Contrato do chamador
# ---------------------------------------------------------------------------
def validar_contrato(marcas, raw_runs, publish_run_id, exigir: bool) -> None:
    """Formato sempre; presenca so' quando `exigir` (o `--apply`)."""
    marcas = list(marcas or [])
    raw_runs = dict(raw_runs or {})
    fora = sorted(set(marcas) - set(fonte_mod.MARCAS))
    if fora:
        raise PublicacaoRecusada(f"marca_fora_da_allowlist: {fora} (aceitas: {list(fonte_mod.MARCAS)})")
    if len(set(marcas)) != len(marcas):
        raise PublicacaoRecusada("marca_repetida em --marca")
    for m, r in raw_runs.items():
        if m not in fonte_mod.MARCAS:
            raise PublicacaoRecusada(f"marca_fora_da_allowlist em --raw-run: {m!r}")
        if not RAW_RUN_ID_RE.match(r or ""):
            raise PublicacaoRecusada(f"raw_run_invalido para {m}: esperado [A-Za-z0-9_-]{{1,36}}")
    if raw_runs and set(raw_runs) != set(marcas):
        raise PublicacaoRecusada(
            f"raw_run_sem_par: --raw-run para {sorted(raw_runs)}, --marca {sorted(marcas)}")
    if publish_run_id is not None and not PUBLISH_RUN_ID_RE.match(publish_run_id):
        raise PublicacaoRecusada("publish_run_id_invalido: esperado uuid em hex, 32 caracteres minusculos")
    if not exigir:
        return
    if not marcas:
        raise PublicacaoRecusada("--apply exige --marca (uma ou mais)")
    if set(raw_runs) != set(marcas):
        raise PublicacaoRecusada("--apply exige um --raw-run <marca>=<run_id> para cada --marca")
    if not publish_run_id:
        raise PublicacaoRecusada("--apply exige --publish-run-id")


def filtrar_fonte(fonte, marcas):
    """Fonte so' com as marcas pedidas. Devolve (filtrada, ignoradas, pedidas_sem_fotografia)."""
    pedidas = set(marcas)
    presentes = {r["brand"] for k in ("snapshot", "status", "ponte") for r in fonte[k]}
    com_foto = {r["brand"] for r in fonte["snapshot"]}
    filtrada = {k: [r for r in fonte[k] if r["brand"] in pedidas] for k in ("snapshot", "status", "ponte")}
    filtrada["vendas"] = {k: v for k, v in fonte["vendas"].items() if k[0] in pedidas}
    if "runs" in fonte:
        filtrada["runs"] = fonte["runs"]
    return filtrada, sorted(presentes - pedidas), sorted(pedidas - com_foto)


def linhagem(fonte) -> dict:
    """run_id(s) RAW por marca, nas tres tabelas lidas (para o chamador montar o --raw-run)."""
    out: dict = {}
    for k in ("snapshot", "status", "ponte"):
        for r in fonte[k]:
            out.setdefault(r["brand"], set()).add(r.get("run_id"))
    return {m: sorted(v, key=str) for m, v in sorted(out.items())}


def _outcome(valor) -> dict:
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return {}
    return valor if isinstance(valor, dict) else {}


def conferir_linhagem(fonte, ref_date: date, raw_runs: dict) -> None:
    """Recusa se a fonte lida nao e' exatamente a linhagem publicada informada."""
    runs = fonte.get("runs")
    if runs is None:
        raise PublicacaoRecusada("runs_raw_inexistente: raw.ml_full_stock_runs ausente; linhagem nao verificavel")
    for m, esperado in sorted(raw_runs.items()):
        for k in ("snapshot", "status", "ponte"):
            vistos = {r.get("run_id") for r in fonte[k] if r["brand"] == m}
            if vistos - {esperado}:
                raise PublicacaoRecusada(
                    f"linhagem_divergente: {m} em {k} tem run(s) {sorted(vistos, key=str)}; esperado {esperado}")
        reg = runs.get(esperado)
        if reg is None:
            raise PublicacaoRecusada(f"run_raw_nao_registrado: {esperado} ausente de raw.ml_full_stock_runs")
        pedidas = [b.strip() for b in (reg.get("brands_requested") or "").split(",") if b.strip()]
        if m not in pedidas:
            raise PublicacaoRecusada(f"run_raw_de_outra_marca: {esperado} nao pediu {m}")
        inicio = reg.get("started_at")
        if inicio is None or inicio.astimezone(fonte_mod.OPERATIONAL_TZ).date() != ref_date:
            raise PublicacaoRecusada(f"run_raw_de_outra_data: {esperado} nao e' de {ref_date}")
        if reg.get("status") not in RUN_STATUS_TERMINADO:
            raise PublicacaoRecusada(f"run_raw_nao_terminado: {esperado} status={reg.get('status')!r}")
        desfecho = _outcome(reg.get("outcome_by_brand")).get(m)
        if desfecho != DESFECHO_PUBLICADA:
            raise PublicacaoRecusada(f"run_raw_nao_publicado: {esperado} desfecho de {m}={desfecho!r}")


def conferir_coerencia(fonte, raw_runs: dict) -> None:
    """Fotografia do run esperado coerente por marca, ou `fonte_incoerente` (antes de qualquer escrita).

    `conferir_linhagem` so' recusa run ALHEIO: conjunto vazio passa. Estas regras
    sao as que o produtor garante para marca PUBLICADA (airflow
    `mercadolivre/ingestion/full_stock/snapshot.py`, `coletar_marca` +
    `GravadorDataMart.publicar_marca`, as tres tabelas numa transacao):
    - snapshot = um registro por `inventory_id` NAO nulo da ponte => snapshot
      com >= 1 linha (sem ela nao ha' fotografia) e ponte com >= 1 linha;
    - todo inventario do snapshot esta' na ponte do mesmo run;
    - status so' e' emitido para inventario lido => todo inventario do status
      esta' no snapshot do mesmo run. Status VAZIO e' legitimo (nenhum
      indisponivel) e nao e' recusado.
    Chave: (seller_id, inventory_id). A mensagem leva contagens, nunca ids.
    """
    for m, rid in sorted(raw_runs.items()):
        do_run = {k: [r for r in fonte[k] if r["brand"] == m and r.get("run_id") == rid]
                  for k in ("snapshot", "status", "ponte")}
        for k in ("snapshot", "ponte"):
            if not do_run[k]:
                raise PublicacaoRecusada(f"fonte_incoerente: {m} sem linha do run {rid} em {k}")
        snap = {(r["seller_id"], r["inventory_id"]) for r in do_run["snapshot"]}
        ponte = {(r["seller_id"], r["inventory_id"]) for r in do_run["ponte"] if r.get("inventory_id")}
        status = {(r["seller_id"], r["inventory_id"]) for r in do_run["status"]}
        if snap - ponte:
            raise PublicacaoRecusada(
                f"fonte_incoerente: {m} tem {len(snap - ponte)} inventario(s) do snapshot fora da ponte do run {rid}")
        if status - snap:
            raise PublicacaoRecusada(
                f"fonte_incoerente: {m} tem {len(status - snap)} inventario(s) do status fora do snapshot do run {rid}")


# ---------------------------------------------------------------------------
# Pacote e escrita
# ---------------------------------------------------------------------------
def montar(fonte, ref_date: date, run_id: str) -> dict:
    """Linhas prontas para as tres fatos. So' marcas com fotografia na fonte (ja' filtrada)."""
    linhas, resumo = T.indicadores(fonte["snapshot"], fonte["status"], fonte["ponte"], fonte["vendas"])
    marcas = sorted({l["brand"] for l in linhas})
    ok = {(l["brand"], l["seller_id"], l["inventory_id"]) for l in linhas if l["read_status"] == "OK"}
    inv = [tuple({**l, "ref_date": ref_date, "source_run_id": run_id}[c] for c in COLS_INV) for l in linhas]
    st = [(ref_date, s["brand"], s["seller_id"], s["inventory_id"], s["status_raw"], int(s["quantity"]), run_id)
          for s in fonte["status"] if (s["brand"], s["seller_id"], s["inventory_id"]) in ok]
    mar = []
    for m in marcas:
        r = resumo[m]
        na = r["unidades_28d_nao_alocadas"]
        mar.append((ref_date, m, r["inventarios"], r["inventarios_full_lidos"],
                    r["inventarios_compartilhados"], r["disponivel_full"], r["nao_disponivel_full"],
                    r["transfer_full"], r["unidades_28d_alocadas"],
                    na.get("item_com_varios_inventarios", 0), na.get("anuncio_sem_inventory_id", 0),
                    na.get("anuncio_fora_da_ponte", 0), r["cobertura_da_alocacao"], run_id))
    soma_disp = sum(l["disponivel"] or 0 for l in linhas if l["read_status"] == "OK")
    return {"marcas": marcas, "inventario": inv, "status": st, "marca": mar,
            "esperado": {"inventarios": len(inv), "soma_disponivel": soma_disp}}


def _inserir(conn, tabela, cols, linhas):
    if not linhas:
        return
    with conn.connection.cursor() as cur:
        execute_values(cur, f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES %s", linhas, page_size=PAGE_SIZE)


def publicar_em_transacao(conn, pacote, ref_date: date) -> dict:
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    p = {"ref_date": ref_date, "marcas": pacote["marcas"]}
    for tabela in (FACT_STATUS, FACT_INVENTARIO, FACT_MARCA):
        conn.execute(text(f"DELETE FROM {tabela} WHERE ref_date = :ref_date AND brand = ANY(:marcas)"), p)
    _inserir(conn, FACT_INVENTARIO, COLS_INV, pacote["inventario"])
    _inserir(conn, FACT_STATUS, COLS_STATUS, pacote["status"])
    _inserir(conn, FACT_MARCA, COLS_MARCA, pacote["marca"])
    destino = conn.execute(text(f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(disponivel) FILTER (WHERE read_status = 'OK'), 0) AS soma
          FROM {FACT_INVENTARIO} WHERE ref_date = :ref_date AND brand = ANY(:marcas)"""), p).mappings().one()
    esp = pacote["esperado"]
    if destino["n"] != esp["inventarios"] or int(destino["soma"]) != esp["soma_disponivel"]:
        raise ReconciliacaoFalhou(
            f"destino n={destino['n']} soma={destino['soma']}; esperado n={esp['inventarios']} "
            f"soma={esp['soma_disponivel']}")
    return {"inventarios_publicados": esp["inventarios"], "status_publicados": len(pacote["status"]),
            "marcas_publicadas": pacote["marcas"], "disponivel_publicado": esp["soma_disponivel"]}


def _marcar(exc, **atributos):
    for k, v in atributos.items():
        try:
            setattr(exc, k, v)
        except (AttributeError, TypeError):   # pragma: no cover - excecao com __slots__
            pass


def _publicar(pacote, ref_date, publish_run_id, destino_esperado: dict):
    """Uma transacao. Marca na excecao se o COMMIT chegou a ser enviado."""
    corpo_concluido = False
    try:
        with local_engine().begin() as conn:
            if not conn.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
                raise ConcurrentRunError(f"advisory lock {ADVISORY_LOCK_KEY} ocupado")
            # Identidade NA conexao desta transacao, antes de qualquer DELETE: fecha a
            # janela entre a checagem do chamador e a conexao do publicador.
            conferir_destino(conn, destino_esperado)
            # Rechecagem sob o lock: fecha a corrida entre a pre-checagem e a escrita.
            if conn.execute(SQL_PUBLISH_RUN_ID_EM_USO, {"publish_run_id": publish_run_id}).scalar():
                raise PublicacaoRecusada(f"publish_run_id_reutilizado: {publish_run_id} ja' existe no destino")
            publicado = publicar_em_transacao(conn, pacote, ref_date)
            corpo_concluido = True
    except BaseException as exc:
        # Corpo incompleto: o COMMIT nunca foi enviado e o begin() fez rollback.
        # Corpo completo: a excecao veio do proprio COMMIT, desfecho desconhecido.
        _marcar(exc, estado_transacao=ESTADO_INDETERMINADO if corpo_concluido else ESTADO_REVERTIDA)
        raise
    return publicado


def tabelas_destino_existem() -> bool:
    with local_engine().connect() as conn:
        linha = conn.execute(text("SELECT " + ", ".join(
            f"to_regclass('{t}') IS NOT NULL" for t in TABELAS))).fetchone()
    return bool(linha) and all(linha)


def publish_run_id_em_uso(publish_run_id: str) -> bool:
    """Pre-checagem so' leitura (a decisiva e' a rechecagem dentro do lock)."""
    with local_engine().connect() as conn:
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            return bool(conn.execute(SQL_PUBLISH_RUN_ID_EM_USO, {"publish_run_id": publish_run_id}).scalar())
        finally:
            conn.rollback()


def _audit_conn():
    return LocalSession().get_bind().raw_connection()


def audit_start(rows_extracted: int, esperado: dict) -> int:
    """INSERT `running`, so' depois de conferir NESTA conexao que o banco e' o aprovado.

    Destino divergente/nao identificavel = `PublicacaoRecusada` sem INSERT nem
    COMMIT (a conexao fecha com rollback); como roda antes da transacao das
    fatos, o run sai `nao_iniciada`.
    """
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            conferir_identidade(cur, esperado)
            cur.execute("""INSERT INTO audit.source_sync_run
                               (source_name, marketplace_id, loja_id, status, started_at, rows_extracted)
                           VALUES (%s, %s, NULL, 'running', NOW(), %s) RETURNING sync_run_id""",
                        (AUDIT_SOURCE, AUDIT_MARKETPLACE_ID, rows_extracted))
            sid = cur.fetchone()[0]
        conn.commit()
        return sid
    finally:
        conn.close()


def audit_finish(sid: int, status: str, rows_loaded=None, error_message=None, *, esperado: dict) -> None:
    """UPDATE do desfecho, so' depois de conferir NESTA conexao que o banco e' o aprovado.

    Divergencia aqui (o destino mudou depois do `audit_start`) levanta sem
    UPDATE nem COMMIT; quem chama reporta `auditoria_ok=false` com a causa.
    """
    conn = _audit_conn()
    try:
        with conn.cursor() as cur:
            conferir_identidade(cur, esperado)
            cur.execute("""UPDATE audit.source_sync_run SET status = %s, finished_at = NOW(),
                               rows_loaded = %s, error_message = %s WHERE sync_run_id = %s""",
                        (status, rows_loaded, error_message, sid))
            if cur.rowcount != 1:
                raise RuntimeError("UPDATE de auditoria nao afetou exatamente 1 linha")
        conn.commit()
    finally:
        conn.close()


def _ler_runs(conn, runs):
    if not conn.execute(SQL_RUNS_PRESENCA).scalar():
        return None
    return {r["run_id"]: r for r in (dict(x._mapping) for x in conn.execute(SQL_RUNS, {"runs": list(runs)}))}


def _ler_fonte(ref_date, runs=()):
    """Fonte + registro dos runs RAW pedidos: TUDO numa unica transacao REPEATABLE READ READ ONLY.

    Snapshot, status, ponte, vendas e runs veem o MESMO instante do Data Mart:
    uma republicacao do airflow no meio da leitura nao intercala runs.
    """
    dm = DataMartSession()
    try:
        fonte_mod.iniciar_leitura_consistente(dm.connection())
        fonte = fonte_mod.ler_fonte(dm.connection(), ref_date)
        if fonte is not None and runs:
            fonte["runs"] = _ler_runs(dm.connection(), runs)
        return fonte
    finally:
        dm.rollback()
        dm.close()


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------
def run(apply: bool = False, ref_date: date | None = None, agora: datetime | None = None,
        marcas=None, raw_runs=None, publish_run_id: str | None = None,
        destino_esperado: str | None = None) -> dict:
    ref_date = ref_date or fonte_mod._hoje_brt(agora)
    marcas, raw_runs = list(marcas or []), dict(raw_runs or {})
    validar_contrato(marcas, raw_runs, publish_run_id, exigir=apply)
    if apply and not flag_ligada():
        raise PublicacaoRecusada(f"{FLAG_AMBIENTE} desligada")
    esperado_neon = carregar_destino_esperado(destino_esperado) if (apply or destino_esperado) else None
    run_id = publish_run_id or uuid.uuid4().hex
    fonte = _ler_fonte(ref_date, tuple(sorted(set(raw_runs.values()))))
    ignoradas, sem_foto = [], []
    if fonte is not None and marcas:
        fonte, ignoradas, sem_foto = filtrar_fonte(fonte, marcas)
    resumo = fonte_mod.resumir(fonte, ref_date, marcas=tuple(marcas) or fonte_mod.MARCAS)
    resumo.update(run_id=run_id, applied=False)
    if fonte is not None:
        resumo["linhagem_raw"] = linhagem(fonte)
    if marcas:
        resumo.update(marcas_pedidas=marcas, marcas_ignoradas=ignoradas)
    if fonte is not None and sem_foto:
        raise PublicacaoRecusada(f"marca_pedida_sem_fotografia: {sem_foto} em {ref_date}")
    if fonte is not None and raw_runs:
        conferir_linhagem(fonte, ref_date, raw_runs)     # dry-run com --raw-run: mesma conferencia
        conferir_coerencia(fonte, raw_runs)
    if not apply:
        return resumo
    # --- travas: qualquer uma ausente = recusa explicita, nada gravado -----
    if fonte is None:
        raise PublicacaoRecusada("fonte_inexistente: tabelas raw.ml_full_* ausentes")
    if not tabelas_destino_existem():
        raise PublicacaoRecusada("fato_inexistente: migration 029 nao aplicada")
    if publish_run_id_em_uso(run_id):
        raise PublicacaoRecusada(f"publish_run_id_reutilizado: {run_id} ja' existe no destino")
    pacote = montar(fonte, ref_date, run_id)
    if pacote["marcas"] != sorted(marcas):
        raise PublicacaoRecusada(f"pacote_fora_do_pedido: {pacote['marcas']} != {sorted(marcas)}")
    resumo.update(modo="apply", source_run_id=run_id)
    # Identidade conferida na conexao do audit ANTES do INSERT: destino divergente
    # recusa aqui, sem nenhuma escrita (`nao_iniciada`).
    sid = audit_start(len(pacote["inventario"]), esperado_neon)
    try:
        publicado = _publicar(pacote, ref_date, run_id, esperado_neon)
    except BaseException as exc:
        estado = getattr(exc, "estado_transacao", ESTADO_REVERTIDA)
        prefixo = "" if estado == ESTADO_REVERTIDA else f"estado_transacao={estado}; "
        try:
            audit_finish(sid, "failed", rows_loaded=0 if estado == ESTADO_REVERTIDA else None,
                         error_message=prefixo + sanitizar(exc), esperado=esperado_neon)
            _marcar(exc, auditoria_ok=True, sync_run_id=sid)
        except Exception as exc_audit:
            logger.error("auditoria da falha nao gravada: %s", sanitizar(exc_audit))
            _marcar(exc, auditoria_ok=False, erro_auditoria=sanitizar(exc_audit), sync_run_id=sid)
        raise
    # Daqui em diante o COMMIT esta' confirmado: nada abaixo pode virar "falha".
    resumo.update(publicado, applied=True, grava=True, sync_run_id=sid, estado_transacao=ESTADO_COMMIT)
    try:
        audit_finish(sid, "success", rows_loaded=publicado["inventarios_publicados"], esperado=esperado_neon)
        resumo["auditoria_ok"] = True
    except Exception as exc:
        logger.error("commit confirmado, auditoria pendente: %s", sanitizar(exc))
        resumo.update(auditoria_ok=False, erro_auditoria=sanitizar(exc))
    return resumo


# ---------------------------------------------------------------------------
# Diagnostico so' leitura (depois de timeout/JSON perdido)
# ---------------------------------------------------------------------------
def diagnosticar(ref_date: date, publish_run_id: str) -> dict:
    """Linhas com o `source_run_id` e o vigente por marca. Read-only, sem lock, sem escrita."""
    base = {"modo": "diagnostico", "grava": False, "ref_date": str(ref_date), "publish_run_id": publish_run_id}
    with local_engine().connect() as conn:
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            linha = conn.execute(text("SELECT " + ", ".join(
                f"to_regclass('{t}') IS NOT NULL" for t in TABELAS))).fetchone()
            if not (linha and all(linha)):
                return dict(base, veredito="fato_inexistente", motivo="migration 029 nao aplicada")
            p = {"ref_date": ref_date, "publish_run_id": publish_run_id}
            do_run, outras, vigentes = {}, 0, {}
            for t in TABELAS:
                do_run[t] = {r[0]: int(r[1]) for r in conn.execute(text(
                    f"SELECT brand, COUNT(*) FROM {t} WHERE ref_date = :ref_date "
                    f"AND source_run_id = :publish_run_id GROUP BY brand ORDER BY brand"), p).fetchall()}
                outras += int(conn.execute(text(
                    f"SELECT COUNT(*) FROM {t} WHERE source_run_id = :publish_run_id "
                    f"AND ref_date <> :ref_date"), p).scalar() or 0)
                for marca, sid in conn.execute(text(
                        f"SELECT DISTINCT brand, source_run_id FROM {t} WHERE ref_date = :ref_date"), p).fetchall():
                    vigentes.setdefault(marca, set()).add(sid)
        finally:
            conn.rollback()
    total = sum(sum(v.values()) for v in do_run.values())
    vigente = {m: (sorted(v)[0] if len(v) == 1 else sorted(v)) for m, v in sorted(vigentes.items())}
    marcas_do_run = sorted({m for v in do_run.values() for m in v})
    if total:
        veredito = ESTADO_COMMIT
        motivo = "linhas com este source_run_id existem: a transacao foi confirmada"
    else:
        veredito = "nenhuma_linha"
        motivo = ("nenhuma linha com este source_run_id: nao houve commit dele "
                  "ou outra publicacao ja' substituiu o (dia, marca)")
    return dict(base, veredito=veredito, motivo=motivo, linhas_do_run=do_run, total_linhas_do_run=total,
                linhas_do_run_em_outras_datas=outras, marcas_do_run=marcas_do_run,
                vigente_para_as_marcas_do_run=bool(marcas_do_run) and all(
                    vigente.get(m) == publish_run_id for m in marcas_do_run),
                source_run_id_vigente=vigente)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class ArgumentoInvalido(PublicacaoRecusada):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):     # JSON de recusa (exit 2) em vez de usage solto no stderr
        raise ArgumentoInvalido(message)


def _raw_run(valor: str):
    marca, sep, rid = valor.partition("=")
    if not sep or not marca.strip() or not rid.strip():
        raise argparse.ArgumentTypeError("use <marca>=<run_id>")
    return marca.strip(), rid.strip()


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(description="Publica o estoque Full do ML (apply exige --marca, --raw-run, "
                             "--publish-run-id, --destino-esperado, flag e migration 029).")
    ap.add_argument("--apply", action="store_true", help="publica; sem ela o run e' dry-run")
    ap.add_argument("--diagnosticar", action="store_true",
                    help="so' leitura: linhas do --publish-run-id na --ref-date e o vigente por marca")
    ap.add_argument("--ref-date", type=date.fromisoformat, default=None)
    ap.add_argument("--marca", action="append", default=[], help=f"repetivel; allowlist {list(fonte_mod.MARCAS)}")
    ap.add_argument("--raw-run", action="append", default=[], type=_raw_run, metavar="MARCA=RUN_ID",
                    help="run RAW publicado esperado, um por marca pedida")
    ap.add_argument("--publish-run-id", default=None, help="uuid hex 32; vira o source_run_id")
    ap.add_argument("--destino-esperado", default=None, metavar="ARQUIVO.json",
                    help="obrigatorio no --apply: JSON aprovado do -Identificar (usa identidade.alvo_neon)")
    return ap


def _logs_para_stderr() -> None:
    """Handlers que escrevem no stdout passam a escrever no stderr."""
    loggers = [logging.getLogger()] + [l for l in logging.Logger.manager.loggerDict.values()
                                       if isinstance(l, logging.Logger)]
    for lg in loggers:
        for h in lg.handlers:
            if type(h) is logging.StreamHandler and h.stream is sys.stdout:
                h.setStream(sys.stderr)


def _saida_erro(resultado: str, exc, a) -> dict:
    estado = getattr(exc, "estado_transacao", ESTADO_NAO_INICIADA)
    out = {"resultado": resultado, "erro_classe": type(exc).__name__, "motivo": _motivo(exc),
           "estado_transacao": estado,
           "applied": None if estado == ESTADO_INDETERMINADO else False,
           "ref_date": str(a.ref_date) if a is not None and a.ref_date else None,
           "publish_run_id": getattr(a, "publish_run_id", None)}
    for k in ("auditoria_ok", "erro_auditoria", "sync_run_id"):
        if hasattr(exc, k):
            out[k] = getattr(exc, k)
    if estado == ESTADO_INDETERMINADO:
        out["proximo_passo"] = "--diagnosticar --ref-date <D> --publish-run-id <X>"
    return out


def _executar(argv) -> tuple[int, dict]:
    a = None
    try:
        a = _parser().parse_args(argv)
        if a.diagnosticar:
            if a.apply or a.marca or a.raw_run:
                raise ArgumentoInvalido("--diagnosticar nao combina com --apply/--marca/--raw-run")
            if not a.ref_date or not a.publish_run_id:
                raise ArgumentoInvalido("--diagnosticar exige --ref-date e --publish-run-id")
            validar_contrato([], {}, a.publish_run_id, exigir=False)
            return EXIT_OK, diagnosticar(a.ref_date, a.publish_run_id)
        raw_runs = {}
        for marca, rid in a.raw_run:
            if marca in raw_runs:
                raise ArgumentoInvalido(f"--raw-run repetido para {marca}")
            raw_runs[marca] = rid
        res = run(apply=a.apply, ref_date=a.ref_date, marcas=a.marca, raw_runs=raw_runs,
                  publish_run_id=a.publish_run_id, destino_esperado=a.destino_esperado)
    except PublicacaoRecusada as exc:
        logger.error("publicacao recusada: %s", sanitizar(exc))
        return EXIT_RECUSA, _saida_erro("recusada", exc, a)
    except Exception as exc:
        logger.error("publicacao falhou: %s", sanitizar(exc))
        return EXIT_ERRO, _saida_erro("erro", exc, a)
    if res.get("auditoria_ok") is False:
        return EXIT_COMMIT_COM_PENDENCIA, res
    return EXIT_OK, res


def main(argv: list[str] | None = None) -> int:
    stdout = sys.stdout
    _logs_para_stderr()
    with contextlib.redirect_stdout(sys.stderr):     # stdout so' com o JSON final
        codigo, saida = _executar(argv)
    print(json.dumps(saida, default=str, ensure_ascii=False, indent=2), file=stdout)
    stdout.flush()
    return codigo


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
