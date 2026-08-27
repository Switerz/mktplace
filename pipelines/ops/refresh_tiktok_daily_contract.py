"""Refresh historico do TikTok Daily sob o contrato comercial do Gate DQ-TK1.

PARA QUE ISTO EXISTE, E POR QUE NAO E' O PIPELINE DIARIO
--------------------------------------------------------
O pipeline diario (`pipelines/ingestion/daily_performance.py`) grava por UPSERT
puro: `INSERT ... ON CONFLICT (date, loja_id, marketplace_id) DO UPDATE`. Isso
atualiza o que existe e insere o que falta, mas **nunca remove**. Depois da
correcao do contrato comercial (b43ffbe), uma chave que deixe de existir na nova
extracao — por exemplo um (dia, marca) cujo unico pedido era `CANCELLED` — ficaria
no destino com o valor ANTIGO para sempre, invisivel. Chave orfa e' o defeito que
este modulo existe para reparar, e por isso ele apaga a janela antes de inserir.

Operacao de MANUTENCAO, deliberadamente separada do fluxo diario: escopo
explicito, backup obrigatorio, reconciliacao antes do commit e caminho de
restore. Nao e' chamada pelo Scheduler e nao deve ser.

CONTRATO REUTILIZADO, NAO REESCRITO
-----------------------------------
A definicao comercial NAO e' redefinida aqui. `connector.QUERY` e' reutilizada
verbatim, com as mesmas constantes (`COMMERCIAL_ORDER_STATUSES`,
`KNOWN_ORDER_STATUSES`, `BRANDS_IN_SCOPE`), e o mapeamento marca -> loja vem de
`transforms.tiktok_brand_daily`. Uma segunda definicao de GMV divergiria da
primeira no dia em que uma delas mudasse — foi exatamente o tipo de divergencia
que produziu o defeito original.

DUAS LISTAS DE COLUNAS, E A DIFERENCA IMPORTA
---------------------------------------------
A tabela tem 46 colunas: as 44 canonicas que o pipeline escreve, mais `id`
(surrogate, da sequence) e `ingested_at` (carimbo de carga).

  WRITE_COLUMNS  (44) — o que o refresh INSERE. `id` continua vindo da sequence
                        e `ingested_at` do DEFAULT, exatamente como no diario.
  BACKUP_COLUMNS (46) — o que o backup GUARDA e o restore RECOLOCA. Sem `id` e
                        `ingested_at` o restore geraria linhas novas: outro `id`
                        e um `ingested_at` de agora, falsificando o "quando isto
                        foi carregado" da Torre. Restaurar precisa devolver a
                        fotografia, nao uma copia parecida.

O restore NAO executa `setval`: a sequence ja avancou, e reinserir ids antigos
explicitamente nao deve faze-la retroceder — isso arriscaria colisao futura.

ORDEM TRANSACIONAL — POR QUE O BACKUP FICA DENTRO DA TRANSACAO
--------------------------------------------------------------
O advisory lock so' exclui outras instancias DESTE CLI. O `daily_performance.py`
nao o conhece e escreve na mesma tabela. Se o backup fosse lido em autocommit e
so' depois a transacao abrisse, outro writer poderia mudar a janela nesse
intervalo — e o backup deixaria de representar o estado imediatamente anterior a
publicacao.

Por isso, sob `--apply`: advisory lock -> BEGIN -> timeouts -> LOCK TABLE ->
leitura das 46 colunas -> backup gravado e RELIDO -> DELETE -> INSERT ->
reconciliacao -> commit -> advisory unlock. Manter a transacao aberta durante a
gravacao de um backup local pequeno e' aceitavel nesta operacao de manutencao, e
e' mais seguro que deixar a janela de corrida aberta.

O lock de tabela e' `SHARE ROW EXCLUSIVE`: o MENOR nivel que conflita com
`ROW EXCLUSIVE` — o que `INSERT`/`UPDATE`/`DELETE` adquirem — e portanto bloqueia
DML concorrente, sem conflitar com `ACCESS SHARE`, de modo que `SELECT` comum
continua funcionando. `ACCESS EXCLUSIVE` bloquearia tambem leitura e seria
desnecessariamente agressivo.

SEGURANCA
---------
Dry-run por padrao — e o dry-run le fonte E destino para dizer o que MUDARIA,
sem escrever nada. `--apply` exige `--run-id` e `--backup-dir`; `--mode restore
--apply` exige tambem `--date-from`/`--date-to`, que precisam coincidir com o
manifesto. `marketplace_id` fixo em 1 — nunca parametrizavel. Nenhum `SELECT *`.
Erros sanitizados. Sem retry, sleep, backoff ou agendamento. Nada e' executado no
import.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from sqlalchemy import text

from pipelines.common import db as _common_db
from pipelines.connectors.tiktok import connector as tk
from pipelines.transforms.tiktok_brand_daily import (
    BRAND_TO_LOJA,
    transform_batch,
)

# Helpers de seguranca reutilizados: duplicar as regexes de sanitizacao
# garantiria que as duas copias divergissem, e a atrasada vazaria topologia.
from pipelines.sync_tiktok_serving import (
    CONNECT_TIMEOUT_SECONDS,
    _get_neon_url,
    sanitize_error_message,
    sanitize_run_id,
    validate_identifier,
    validate_qualified,
)

# ---------------------------------------------------------------------------
# Especificacao — literal, fixa, versionada
# ---------------------------------------------------------------------------

TARGET_TABLE = "marts.fact_marketplace_daily_performance"
STAGING_TABLE = "stg_tk_daily_restore"

TZ_OPERACIONAL = ZoneInfo("America/Sao_Paulo")

#: Primeiro dia JA EXISTENTE no destino. A janela padrao nao expande o historico
#: da Torre: qualquer extensao para tras e' decisao separada, medida e aprovada
#: em outra rodada, nunca efeito colateral de um refresh.
DEFAULT_DATE_FROM = date(2025, 12, 26)

#: Extensao candidata, deliberadamente FORA do padrao. Existe como constante
#: apenas para ser medida e reportada; nenhuma funcao a usa como janela.
CANDIDATE_EXTENSION = (date(2025, 10, 5), date(2025, 12, 25))

#: Advisory lock proprio desta operacao. Exclui outras instancias DESTE CLI —
#: nao exclui o pipeline diario, que nao o conhece. Quem exclui DML concorrente
#: e' o LOCK TABLE, dentro da transacao.
ADVISORY_LOCK_KEY = 913_120_001

#: Menor nivel que conflita com ROW EXCLUSIVE (INSERT/UPDATE/DELETE) e NAO
#: conflita com ACCESS SHARE (SELECT comum). Ver docstring do modulo.
TABLE_LOCK_MODE = "SHARE ROW EXCLUSIVE"

#: TikTok Shop. Fixo, nao parametrizavel: um refresh que pudesse apontar para
#: outro marketplace apagaria a janela de ML ou Shopee.
TIKTOK_MARKETPLACE_ID = 1

KEY_COLUMNS = ("date", "loja_id", "marketplace_id")

#: As 44 colunas canonicas que o pipeline diario escreve, na mesma ordem do
#: `UPSERT_SQL` de `ingestion/daily_performance.py`. E' o que o refresh INSERE.
WRITE_COLUMNS = (
    "date", "loja_id", "marketplace_id", "empresa_id",
    "gmv", "orders", "units_sold", "avg_ticket",
    "unique_buyers", "new_buyers", "repeat_buyers", "repeat_buyer_rate_pct",
    "visitors", "conversion_rate",
    "canceled_orders", "returned_orders", "refunded_orders", "problem_rate",
    "cancel_rate_pct",
    "delivered_orders", "avg_delivery_hours", "avg_delivery_days",
    "ad_spend", "ad_revenue", "ad_impressions", "ad_clicks", "roas", "acos_pct",
    "ctr_pct", "cpc",
    "gmv_video", "gmv_live", "gmv_card",
    "total_settlement", "total_fees", "avg_fee_pct", "avg_settlement_pct",
    "seller_shipping_cost", "shipping_pct_of_gmv",
    "target_revenue", "target_attainment_pct", "projected_month_revenue",
    "data_quality_score", "source_updated_at",
)

#: As 46 colunas que o backup guarda e o restore recoloca. `id` e `ingested_at`
#: entram aqui e SO aqui: sem eles o restore devolveria uma copia parecida, com
#: id novo e carimbo de carga de agora, em vez da fotografia.
BACKUP_COLUMNS = ("id",) + WRITE_COLUMNS + ("ingested_at",)

#: Colunas numericas com escala — serializadas como STRING no backup, para que
#: nenhum valor passe por float. Um `0.1` em float nao volta como `0.1`.
DECIMAL_COLUMNS = (
    "gmv", "avg_ticket", "repeat_buyer_rate_pct", "conversion_rate",
    "problem_rate", "cancel_rate_pct", "avg_delivery_hours", "avg_delivery_days",
    "ad_spend", "ad_revenue", "roas", "acos_pct", "ctr_pct", "cpc",
    "gmv_video", "gmv_live", "gmv_card", "total_settlement", "total_fees",
    "avg_fee_pct", "avg_settlement_pct", "seller_shipping_cost",
    "shipping_pct_of_gmv", "target_revenue", "target_attainment_pct",
    "projected_month_revenue", "data_quality_score",
)

TIMESTAMP_COLUMNS = ("source_updated_at", "ingested_at")

#: Agregados do manifesto e do dry-run. Decimal como string; contagens como int.
MANIFEST_DECIMAL_AGGREGATES = ("gmv", "gmv_video", "gmv_live", "gmv_card")
MANIFEST_INT_AGGREGATES = ("orders", "canceled_orders", "units_sold")

BACKUP_SCHEMA_VERSION = 2
BACKUP_CSV_NAME = "tk_daily_backup.csv"
BACKUP_MANIFEST_NAME = "tk_daily_backup_manifest.json"
#: Companion EXTERNO, cobrindo CSV **e** manifesto. Um companion que so' cobrisse
#: o CSV deixaria alteracao do manifesto — inclusive da janela declarada nele —
#: passar sem deteccao.
#:
#: ESCOPO: detecta CORRUPCAO ou ALTERACAO dos dois arquivos enquanto o proprio
#: companion permanecer confiavel. NAO da autenticidade criptografica — nao ha
#: assinatura nem HMAC —, entao quem puder alterar os tres arquivos recalcula os
#: hashes e passa por esta camada. O modelo operacional pressupoe diretorio local
#: controlado pelo proprietario. As validacoes semanticas de `load_backup`
#: (agregado recalculado, janela do operador, marketplace, chave duplicada, linha
#: fora da janela) sao independentes dos hashes e continuam valendo.
BACKUP_SUMS_NAME = "SHA256SUMS"

SOURCE_STATEMENT_TIMEOUT = "600s"
TARGET_STATEMENT_TIMEOUT = "300s"
LOCK_TIMEOUT = "30s"
INSERT_PAGE_SIZE = 500

_RUN_ID_PREFIX = "refresh_tiktok_daily_contract"

LOJA_TO_BRAND = {v: k for k, v in BRAND_TO_LOJA.items()}


class RefreshError(RuntimeError):
    """Erro de contrato ou de integridade desta operacao."""


class BackupIntegrityError(RefreshError):
    """Backup ausente, incompleto, ilegivel ou adulterado."""


@dataclass(frozen=True)
class SourceSnapshot:
    """Fotografia materializada da fonte. Reconciliacao compara contra ISTO."""

    date_from: date
    date_to: date
    raw_rows: list[dict]
    canonical_rows: list[dict]
    provas: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Janela
# ---------------------------------------------------------------------------

def operational_cutoff(agora: datetime | None = None) -> date:
    """D-1 em America/Sao_Paulo.

    O fuso e' explicito porque a Torre e' operada no horario de Sao Paulo: usar
    UTC adiantaria o cutoff em ate tres horas e deixaria entrar um dia ainda
    aberto.
    """
    agora = agora or datetime.now(TZ_OPERACIONAL)
    if agora.tzinfo is None:
        raise RefreshError("instante sem timezone: o cutoff exige fuso explicito.")
    return agora.astimezone(TZ_OPERACIONAL).date() - timedelta(days=1)


def resolve_window(date_from: date | None, date_to: date | None,
                   cutoff: date) -> tuple[date, date]:
    """Janela INCLUSIVA nas duas pontas, validada contra o cutoff."""
    inicio = DEFAULT_DATE_FROM if date_from is None else date_from
    fim = cutoff if date_to is None else date_to
    if fim > cutoff:
        raise RefreshError(
            f"date_to={fim.isoformat()} e posterior a D-1 "
            f"({cutoff.isoformat()}) em America/Sao_Paulo. O dia corrente esta "
            "aberto e seus status ainda maturam; publicar como definitivo "
            "gravaria numero que muda sozinho."
        )
    if inicio > fim:
        raise RefreshError(
            f"janela vazia: date_from={inicio.isoformat()} > date_to={fim.isoformat()}."
        )
    if inicio < DEFAULT_DATE_FROM:
        raise RefreshError(
            f"date_from={inicio.isoformat()} e anterior a {DEFAULT_DATE_FROM.isoformat()}, "
            "o primeiro dia existente no destino. Estender o historico da Torre e "
            "decisao separada (ver CANDIDATE_EXTENSION), nao efeito colateral de "
            "um refresh."
        )
    return inicio, fim


def dias_da_janela(date_from: date, date_to: date) -> list[date]:
    n = (date_to - date_from).days + 1
    return [date_from + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Fonte — fotografia read-only
# ---------------------------------------------------------------------------

def read_source_snapshot(date_from: date, date_to: date) -> SourceSnapshot:
    """Le a fonte em `REPEATABLE READ` + `READ ONLY` e materializa em memoria.

    Reutiliza `connector.QUERY` verbatim: a definicao comercial e' a do conector,
    nao uma copia. O que este modulo controla e' a TRANSACAO, que o
    `datamart_query` do conector nao expoe.
    """
    engine = getattr(_common_db, "_datamart_engine", None)
    if engine is None:
        raise RefreshError("Data Mart nao configurado: defina DATAMART_DATABASE_URL.")
    params = {
        "brands": tk.BRANDS_IN_SCOPE,
        "date_from": date_from,
        "date_to_exclusive": date_to + timedelta(days=1),
        "commercial_statuses": tk.COMMERCIAL_ORDER_STATUSES,
        "known_statuses": tk.KNOWN_ORDER_STATUSES,
    }
    conn = engine.connect().execution_options(isolation_level="REPEATABLE READ")
    try:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text(f"SET LOCAL statement_timeout = '{SOURCE_STATEMENT_TIMEOUT}'"))
        prova = conn.execute(text(
            "SELECT current_setting('transaction_isolation') AS iso, "
            "current_setting('transaction_read_only') AS ro"
        )).mappings().one()
        if prova["iso"].lower() != "repeatable read" or prova["ro"] != "on":
            raise RefreshError(
                "fotografia da fonte nao foi concedida: isolamento "
                f"'{prova['iso']}', read_only '{prova['ro']}'."
            )
        raw = [dict(r) for r in conn.execute(text(tk.QUERY), params).mappings()]
    finally:
        conn.close()

    canon = transform_batch(raw)
    provas = {
        "isolamento": prova["iso"], "read_only": prova["ro"],
        "linhas_fonte": len(raw), "linhas_canonicas": len(canon),
    }
    return SourceSnapshot(date_from, date_to, raw, canon, provas)


# ---------------------------------------------------------------------------
# Pre-condicoes de escrita — todas ANTES de qualquer DML
# ---------------------------------------------------------------------------

def assert_source_contract(snapshot: SourceSnapshot) -> dict:
    """Pre-condicoes obrigatorias.

    NAO exige o produto cartesiano de cinco marcas x todos os dias: uma
    combinacao marca x dia ausente pode significar ausencia legitima de pedidos
    naquele dia, e nao ha prova comercial de que toda marca venda todo dia.
    Fabricar linha zero para preencher inventaria medicao. O que se exige e' que
    as cinco marcas existam no CONJUNTO da janela e que todo dia tenha ALGUMA
    linha; as combinacoes ausentes sao CONTADAS e reportadas para revisao.
    """
    raw = snapshot.raw_rows
    problemas: list[str] = []

    if not raw:
        problemas.append(
            "fonte VAZIA na janela: um refresh com fonte vazia apagaria o destino "
            "e nao publicaria nada"
        )

    marcas = {r.get("brand") for r in raw}
    esperadas = set(tk.BRANDS_IN_SCOPE)
    faltando = sorted(esperadas - marcas)
    extras = sorted(m for m in marcas - esperadas if m is not None)
    if faltando:
        problemas.append(f"marcas ausentes na janela inteira: {faltando}")
    if extras:
        problemas.append(f"marcas fora de BRANDS_IN_SCOPE na fonte: {extras}")

    dias_esperados = set(dias_da_janela(snapshot.date_from, snapshot.date_to))
    dias_vistos = {r["date"] for r in raw if r.get("date") is not None}
    lacunas = sorted(dias_esperados - dias_vistos)
    if lacunas:
        amostra = [d.isoformat() for d in lacunas[:5]]
        problemas.append(
            f"{len(lacunas)} dia(s) sem NENHUMA linha na fonte (primeiros: {amostra})"
        )

    chaves = [(r.get("date"), r.get("brand")) for r in raw]
    if len(chaves) != len(set(chaves)):
        problemas.append(
            f"{len(chaves) - len(set(chaves))} chave(s) (date, brand) duplicada(s)"
        )

    desconhecidos = sum(int(r.get("orders_unknown_status") or 0) for r in raw)
    if desconhecidos:
        problemas.append(
            f"{desconhecidos} pedido(s) com status nulo ou fora dos "
            f"{len(tk.KNOWN_ORDER_STATUSES)} conhecidos"
        )
    sem_valor = sum(int(r.get("orders_commercial_null_amount") or 0) for r in raw)
    if sem_valor:
        problemas.append(f"{sem_valor} pedido(s) comercial(is) com total_amount NULO")

    canon = snapshot.canonical_rows
    if len(canon) != len(raw):
        problemas.append(
            f"transformacao perdeu linhas: fonte={len(raw)} canonicas={len(canon)}"
        )
    fora = sorted({r["marketplace_id"] for r in canon} - {TIKTOK_MARKETPLACE_ID})
    if fora:
        problemas.append(f"marketplace_id fora do TikTok nas linhas canonicas: {fora}")
    lojas_invalidas = sorted({r["loja_id"] for r in canon} - set(BRAND_TO_LOJA.values()))
    if lojas_invalidas:
        problemas.append(f"loja_id fora do mapeamento oficial: {lojas_invalidas}")
    fora_janela = sorted({r["date"] for r in canon
                          if not (snapshot.date_from <= r["date"] <= snapshot.date_to)})
    if fora_janela:
        problemas.append(f"{len(fora_janela)} linha(s) canonica(s) fora da janela")

    if problemas:
        raise RefreshError("pre-condicoes reprovaram: " + "; ".join(problemas))

    combinacoes_possiveis = len(dias_esperados) * len(esperadas)
    ausentes = combinacoes_possiveis - len(set(chaves))
    return {
        "linhas": len(raw),
        "marcas": sorted(m for m in marcas if m is not None),
        "dias": len(dias_vistos),
        "dias_esperados": len(dias_esperados),
        "combinacoes_marca_dia_ausentes": ausentes,
        "nota_combinacoes": (
            "ausencia NAO e' preenchida com linha zero; pode ser ausencia "
            "legitima de pedidos. Reportada para revisao."
        ),
        "status_desconhecido": 0,
        "comercial_sem_valor": 0,
    }


# ---------------------------------------------------------------------------
# Serializacao do backup
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _serialize(col: str, valor):
    """Decimal como string, datas em ISO, `None` como campo vazio.

    A distincao entre `None` e vazio e' recuperada por `_deserialize`: ausencia
    nunca vira zero.
    """
    if valor is None:
        return ""
    if col == "date":
        return valor.isoformat()
    if col in TIMESTAMP_COLUMNS:
        return valor.isoformat()          # preserva offset de fuso
    return str(valor)


def _deserialize(col: str, texto: str):
    if texto == "":
        return None
    if col == "date":
        return date.fromisoformat(texto)
    if col in TIMESTAMP_COLUMNS:
        return datetime.fromisoformat(texto)
    if col in DECIMAL_COLUMNS:
        try:
            d = Decimal(texto)
        except (InvalidOperation, ValueError) as exc:
            raise BackupIntegrityError(
                f"coluna {col} nao e um decimal valido no backup"
            ) from exc
        if not d.is_finite():
            raise BackupIntegrityError(f"coluna {col} nao e finita no backup")
        return d
    return int(texto)


def _aggregates(rows: list[dict]) -> dict:
    ag: dict = {}
    for col in MANIFEST_DECIMAL_AGGREGATES:
        presentes = [r.get(col) for r in rows if r.get(col) is not None]
        ag[col] = None if not presentes else str(
            sum((Decimal(str(v)) for v in presentes), Decimal("0"))
        )
    for col in MANIFEST_INT_AGGREGATES:
        presentes = [r.get(col) for r in rows if r.get(col) is not None]
        ag[col] = None if not presentes else int(sum(int(v) for v in presentes))
    return ag


def _agregado_igual(a, b) -> bool:
    """Igualdade NUMERICA de agregados serializados.

    `_aggregates` devolve Decimal como string. Comparar as strings confundiria
    representacao com valor: "1.10" e "1.1" sao o mesmo numero. `None` so e'
    igual a `None` — ausencia nunca e' comparada com zero.
    """
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return False


def _publish_atomic(path: Path, data: bytes) -> None:
    """Publica sem possibilidade de sobrescrita.

    `os.link` cria uma segunda entrada de diretorio e falha com
    `FileExistsError` se o destino existir — diferente de `os.replace`, que
    sobrescreveria em silencio. O temporario e' removido em qualquer caminho.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise BackupIntegrityError(
                f"{path.name} passou a existir durante a publicacao; "
                "nada foi sobrescrito"
            ) from exc
    finally:
        if tmp.exists():
            tmp.unlink()


def _parse_sums(texto: str) -> dict[str, str]:
    """Le o companion `SHA256SUMS`. Nome extra, ausente ou duplicado REPROVA."""
    esperados = {BACKUP_CSV_NAME, BACKUP_MANIFEST_NAME}
    vistos: dict[str, str] = {}
    for linha in texto.splitlines():
        if not linha.strip():
            continue
        partes = linha.split()
        if len(partes) != 2:
            raise BackupIntegrityError("SHA256SUMS tem linha malformada")
        digest, nome = partes
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BackupIntegrityError("SHA256SUMS tem digest malformado")
        if nome in vistos:
            raise BackupIntegrityError(f"SHA256SUMS tem entrada duplicada: {nome}")
        vistos[nome] = digest
    if set(vistos) != esperados:
        raise BackupIntegrityError(
            "SHA256SUMS deve cobrir exatamente o CSV e o manifesto; "
            f"encontrado: {sorted(vistos)}"
        )
    return vistos


def write_backup(destino: Path, date_from: date, date_to: date,
                 rows_before: list[dict], run_id: str) -> dict:
    """Grava CSV (46 colunas) + manifesto + `SHA256SUMS` e RELE do disco.

    O backup e' gravado e revalidado ANTES do primeiro DML. Um backup que nao
    possa ser relido nao serve de reversibilidade, e descobrir isso depois do
    DELETE seria descobrir tarde.
    """
    destino = Path(destino)
    if not destino.is_dir():
        raise BackupIntegrityError(
            "diretorio de backup nao existe; forneca um caminho explicito ja criado"
        )
    csv_path = destino / BACKUP_CSV_NAME
    man_path = destino / BACKUP_MANIFEST_NAME
    sums_path = destino / BACKUP_SUMS_NAME
    for p in (csv_path, man_path, sums_path):
        if p.exists():
            raise BackupIntegrityError(
                f"{p.name} ja existe no diretorio de backup; nao sobrescrevo"
            )

    sio = io.StringIO()
    w = csv.writer(sio, lineterminator="\n")
    w.writerow(BACKUP_COLUMNS)
    w.writerows([[_serialize(c, r.get(c)) for c in BACKUP_COLUMNS]
                 for r in rows_before])
    _publish_atomic(csv_path, sio.getvalue().encode("utf-8"))

    datas = [r["date"] for r in rows_before]
    manifesto = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_id,
        "target_table": TARGET_TABLE,
        "marketplace_id": TIKTOK_MARKETPLACE_ID,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "grain_key": list(KEY_COLUMNS),
        "columns": list(BACKUP_COLUMNS),
        "row_count": len(rows_before),
        "distinct_dates": len(set(datas)),
        "min_date": min(datas).isoformat() if datas else None,
        "max_date": max(datas).isoformat() if datas else None,
        "loja_ids": sorted({r["loja_id"] for r in rows_before}),
        "aggregates": _aggregates(rows_before),
        "csv_sha256": _sha256_file(csv_path),
    }
    _publish_atomic(
        man_path,
        json.dumps(manifesto, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )
    # Companion EXTERNO cobrindo os DOIS arquivos: com so' o hash do CSV, uma
    # alteracao do manifesto — inclusive da janela declarada nele — passaria sem
    # deteccao. Integridade contra corrupcao/alteracao, nao autenticidade: ver a
    # nota de escopo em BACKUP_SUMS_NAME.
    _publish_atomic(sums_path, (
        f"{_sha256_file(csv_path)}  {BACKUP_CSV_NAME}\n"
        f"{_sha256_file(man_path)}  {BACKUP_MANIFEST_NAME}\n"
    ).encode("utf-8"))

    relido = load_backup(destino, date_from, date_to)
    if relido["row_count"] != len(rows_before):
        raise BackupIntegrityError(
            "backup relido do disco tem contagem diferente da esperada"
        )
    return {
        "csv": csv_path.name, "manifesto": man_path.name, "sums": sums_path.name,
        "colunas": len(BACKUP_COLUMNS), "row_count": len(rows_before),
        "csv_sha256": manifesto["csv_sha256"],
        "manifest_sha256": relido["manifest_sha256"],
        "aggregates": manifesto["aggregates"],
    }


def load_backup(origem: Path, date_from: date | None = None,
                date_to: date | None = None) -> dict:
    """Le e VALIDA o backup. Hashes PRIMEIRO, conteudo depois.

    A ordem importa: validar conteudo antes dos hashes deixaria o validador
    consumir bytes que ainda nao se provaram integros.

    DUAS CAMADAS, COM ALCANCES DIFERENTES:

    1. Hashes (`SHA256SUMS`) — detectam corrupcao ou alteracao do CSV e do
       manifesto ENQUANTO o companion permanecer confiavel. Nao ha assinatura nem
       HMAC, logo isto NAO e' autenticidade: quem alterar os tres arquivos
       recalcula os hashes e passa. Pressupoe diretorio local controlado pelo
       proprietario.
    2. Validacoes semanticas — independentes dos hashes: agregado declarado
       contra o RECALCULADO a partir do CSV, `marketplace_id`, chave duplicada,
       linha fora da janela, linha sem `id`, e a janela do manifesto contra a que
       o OPERADOR informou. Esta ultima e' a unica cuja fonte da verdade nao esta
       nos arquivos — e' o que impede o proprio backup de decidir o que apagar.
    """
    origem = Path(origem)
    csv_path = origem / BACKUP_CSV_NAME
    man_path = origem / BACKUP_MANIFEST_NAME
    sums_path = origem / BACKUP_SUMS_NAME
    for p in (csv_path, man_path, sums_path):
        if not p.is_file():
            raise BackupIntegrityError(f"backup incompleto: {p.name} ausente")

    # 1. Hashes, antes de qualquer parse de conteudo.
    sums = _parse_sums(sums_path.read_text(encoding="utf-8"))
    sha_csv = _sha256_file(csv_path)
    sha_man = _sha256_file(man_path)
    if sums[BACKUP_CSV_NAME] != sha_csv:
        raise BackupIntegrityError(
            "SHA-256 do CSV divergiu do SHA256SUMS: backup adulterado ou corrompido"
        )
    if sums[BACKUP_MANIFEST_NAME] != sha_man:
        raise BackupIntegrityError(
            "SHA-256 do manifesto divergiu do SHA256SUMS: manifesto adulterado"
        )

    # 2. Conteudo.
    manifesto = json.loads(man_path.read_text(encoding="utf-8"))
    if manifesto.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise BackupIntegrityError("schema_version do manifesto nao reconhecido")
    if manifesto.get("target_table") != TARGET_TABLE:
        raise BackupIntegrityError("manifesto aponta para outra tabela de destino")
    if manifesto.get("marketplace_id") != TIKTOK_MARKETPLACE_ID:
        raise BackupIntegrityError("manifesto aponta para outro marketplace")
    if list(manifesto.get("columns") or []) != list(BACKUP_COLUMNS):
        raise BackupIntegrityError(
            "colunas do manifesto divergem das 46 colunas de backup do fato"
        )
    if manifesto.get("csv_sha256") != sha_csv:
        raise BackupIntegrityError("csv_sha256 do manifesto divergiu do CSV")

    with open(csv_path, newline="", encoding="utf-8") as f:
        leitor = csv.reader(f)
        cabecalho = next(leitor, None)
        if cabecalho != list(BACKUP_COLUMNS):
            raise BackupIntegrityError("cabecalho do CSV divergiu das colunas de backup")
        registros = []
        for linha in leitor:
            if len(linha) != len(BACKUP_COLUMNS):
                raise BackupIntegrityError("linha do CSV com numero de colunas errado")
            registros.append({c: _deserialize(c, v)
                              for c, v in zip(BACKUP_COLUMNS, linha)})

    if len(registros) != manifesto.get("row_count"):
        raise BackupIntegrityError("contagem de linhas do CSV divergiu do manifesto")
    recalc = _aggregates(registros)
    declarado = manifesto.get("aggregates") or {}
    for chave in sorted(set(recalc) | set(declarado)):
        if not _agregado_igual(recalc.get(chave), declarado.get(chave)):
            raise BackupIntegrityError(
                f"agregado {chave} recalculado divergiu do manifesto"
            )

    m_from = date.fromisoformat(manifesto["date_from"])
    m_to = date.fromisoformat(manifesto["date_to"])
    if date_from is not None and m_from != date_from:
        raise BackupIntegrityError(
            f"date_from informado ({date_from.isoformat()}) nao coincide com o "
            f"manifesto ({m_from.isoformat()})"
        )
    if date_to is not None and m_to != date_to:
        raise BackupIntegrityError(
            f"date_to informado ({date_to.isoformat()}) nao coincide com o "
            f"manifesto ({m_to.isoformat()})"
        )
    for r in registros:
        if not (m_from <= r["date"] <= m_to):
            raise BackupIntegrityError("CSV contem linha fora da janela do manifesto")
        if r["marketplace_id"] != TIKTOK_MARKETPLACE_ID:
            raise BackupIntegrityError("CSV contem linha de outro marketplace")
        if r["id"] is None:
            raise BackupIntegrityError("CSV contem linha sem id: restore nao seria fiel")

    return {
        "records": registros, "manifesto": manifesto,
        "row_count": len(registros), "date_from": m_from, "date_to": m_to,
        "csv_sha256": sha_csv, "manifest_sha256": sha_man,
    }


# ---------------------------------------------------------------------------
# Destino
# ---------------------------------------------------------------------------

def _cols_sql(cols) -> str:
    return ", ".join(validate_identifier(c) for c in cols)


def _neon_session(url: str):
    """Conexao unica do destino, em autocommit ate a hora de transacionar.

    O advisory lock e' de SESSAO e vive nesta mesma conexao que publica: se ela
    cair, perde-se o lock E o unico caminho de escrita ao mesmo tempo.
    """
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor,
                            connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.autocommit = True
    return conn


def _neon_readonly(url: str):
    """Somente para o dry-run: sessao read-only, sem lock e sem escrita."""
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor,
                            connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.set_session(readonly=True)
    return conn


def acquire_advisory_lock(conn) -> None:
    cur = conn.cursor()
    try:
        cur.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        cur.fetchone()
    finally:
        cur.close()


def release_advisory_lock(conn) -> bool:
    """Nunca levanta: roda em `finally`, possivelmente com excecao em voo."""
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT pg_advisory_unlock(%s) AS liberado",
                        (ADVISORY_LOCK_KEY,))
            linha = cur.fetchone()
            return bool(linha and linha["liberado"])
        finally:
            cur.close()
    except Exception:  # noqa: BLE001 — ver docstring
        return False


def lock_target_table(cur) -> None:
    """`SHARE ROW EXCLUSIVE` — bloqueia DML concorrente, preserva SELECT.

    Conflita com `ROW EXCLUSIVE` (adquirido por INSERT/UPDATE/DELETE), com
    `SHARE`, consigo mesmo, com `EXCLUSIVE` e com `ACCESS EXCLUSIVE`. NAO conflita
    com `ACCESS SHARE`, entao leitura comum — inclusive a da API — continua
    passando. E' o menor nivel que fecha a corrida com o `daily_performance.py`,
    que nao conhece o advisory lock desta operacao.
    """
    cur.execute(
        f"LOCK TABLE {validate_qualified(TARGET_TABLE)} IN {TABLE_LOCK_MODE} MODE"
    )


def read_target_window(cur, date_from: date, date_to: date,
                       cols=BACKUP_COLUMNS) -> list[dict]:
    """Le a janela do destino, colunas EXPLICITAS. Nunca `SELECT *`.

    Default nas 46 colunas de backup; a reconciliacao pede as 44 de escrita,
    porque `id` e `ingested_at` sao gerados na insercao e nao existem no
    conjunto esperado.
    """
    cur.execute(
        f"SELECT {_cols_sql(cols)} FROM {validate_qualified(TARGET_TABLE)} "
        f"WHERE marketplace_id = %(mkt)s AND date BETWEEN %(df)s AND %(dt)s "
        f"ORDER BY date, loja_id",
        {"mkt": TIKTOK_MARKETPLACE_ID, "df": date_from, "dt": date_to},
    )
    return [dict(r) for r in cur.fetchall()]


def _chave(r: dict) -> tuple:
    return (r["date"], r["loja_id"], r["marketplace_id"])


def _comparavel(r: dict, cols=WRITE_COLUMNS) -> tuple:
    """Tupla canonica para comparacao exata, insensivel a representacao decimal.

    `Decimal("1.10")` e `Decimal("1.1")` sao iguais em valor e diferentes em
    texto; comparar por `str()` acusaria divergencia falsa. `None` permanece
    `None` — nulo nunca e' comparado como zero.
    """
    out = []
    for c in cols:
        v = r.get(c)
        if v is None:
            out.append(None)
        elif c in DECIMAL_COLUMNS:
            out.append(Decimal(str(v)))
        elif c == "date" or c in TIMESTAMP_COLUMNS:
            out.append(v)
        else:
            out.append(int(v))
    return tuple(out)


def reconcile(destino: list[dict], esperado: list[dict],
              date_from: date, date_to: date) -> dict:
    """Reconciliacao exata destino x fotografia, nas 44 colunas de escrita."""
    problemas = []
    d_keys = {_chave(r) for r in destino}
    e_keys = {_chave(r) for r in esperado}
    if len(d_keys) != len(destino):
        problemas.append("destino tem chave duplicada na janela")
    faltando = e_keys - d_keys
    sobrando = d_keys - e_keys
    if faltando:
        problemas.append(f"{len(faltando)} chave(s) esperada(s) ausente(s) no destino")
    if sobrando:
        problemas.append(f"{len(sobrando)} chave(s) orfa(s) restante(s) no destino")
    if len(destino) != len(esperado):
        problemas.append(f"linhas: destino={len(destino)} esperado={len(esperado)}")

    d_set = {_comparavel(r) for r in destino}
    e_set = {_comparavel(r) for r in esperado}
    so_esperado, so_destino = len(e_set - d_set), len(d_set - e_set)
    if so_esperado or so_destino:
        problemas.append(
            f"EXCEPT bidirecional divergiu: esperado-destino={so_esperado} "
            f"destino-esperado={so_destino}"
        )

    ag_d, ag_e = _aggregates(destino), _aggregates(esperado)
    for k in sorted(set(ag_d) | set(ag_e)):
        if not _agregado_igual(ag_d.get(k), ag_e.get(k)):
            problemas.append(f"agregado {k}: destino={ag_d.get(k)} esperado={ag_e.get(k)}")

    fora = [r for r in destino if not (date_from <= r["date"] <= date_to)]
    if fora:
        problemas.append(f"{len(fora)} linha(s) fora da janela no conjunto lido")

    if problemas:
        raise RefreshError("reconciliacao reprovou: " + "; ".join(problemas))
    return {
        "chaves": len(e_keys), "linhas": len(esperado),
        "except_bidirecional": (so_esperado, so_destino), "agregados": ag_e,
    }


def _delete_window(cur, date_from: date, date_to: date) -> int:
    """DELETE restrito a janela E ao TikTok. `marketplace_id` e' constante do
    modulo, nunca parametro do CLI: ML e Shopee nao podem ser alcancados.
    """
    cur.execute(
        f"DELETE FROM {validate_qualified(TARGET_TABLE)} "
        f"WHERE marketplace_id = %(mkt)s AND date BETWEEN %(df)s AND %(dt)s",
        {"mkt": TIKTOK_MARKETPLACE_ID, "df": date_from, "dt": date_to},
    )
    return cur.rowcount


def _insert_rows(cur, rows: list[dict], cols=WRITE_COLUMNS) -> int:
    if not rows:
        return 0
    sql = (f"INSERT INTO {validate_qualified(TARGET_TABLE)} "
           f"({_cols_sql(cols)}) VALUES %s")
    execute_values(cur, sql, [tuple(r.get(c) for c in cols) for r in rows],
                   page_size=INSERT_PAGE_SIZE)
    return len(rows)


def publish_in_transaction(cur, snapshot: SourceSnapshot) -> dict:
    """DELETE + INSERT (44 colunas) + reconciliacao, na transacao do chamador."""
    resultado = {}
    resultado["deleted"] = _delete_window(cur, snapshot.date_from, snapshot.date_to)
    resultado["inserted"] = _insert_rows(cur, snapshot.canonical_rows, WRITE_COLUMNS)
    destino = read_target_window(cur, snapshot.date_from, snapshot.date_to,
                                 WRITE_COLUMNS)
    resultado["reconciliacao"] = reconcile(
        destino, snapshot.canonical_rows, snapshot.date_from, snapshot.date_to
    )
    return resultado


def restore_in_transaction(cur, backup: dict) -> dict:
    """Restore das 46 colunas via staging temporaria.

    Recoloca `id` e `ingested_at` EXPLICITAMENTE — e' o que torna o restore uma
    fotografia e nao uma copia. Nao executa `setval`: a sequence ja avancou, e
    faze-la retroceder arriscaria colisao futura de id.
    """
    date_from, date_to = backup["date_from"], backup["date_to"]
    registros = backup["records"]
    staging = validate_identifier(STAGING_TABLE)
    cur.execute(
        f"CREATE TEMP TABLE {staging} "
        f"(LIKE {validate_qualified(TARGET_TABLE)} INCLUDING DEFAULTS) "
        f"ON COMMIT DROP"
    )
    if registros:
        execute_values(
            cur,
            f"INSERT INTO {staging} ({_cols_sql(BACKUP_COLUMNS)}) VALUES %s",
            [tuple(r.get(c) for c in BACKUP_COLUMNS) for r in registros],
            page_size=INSERT_PAGE_SIZE,
        )
    cur.execute(
        f"SELECT COUNT(*) AS n, "
        f"COUNT(*) FILTER (WHERE marketplace_id <> %(mkt)s) AS fora_mkt, "
        f"COUNT(*) FILTER (WHERE date NOT BETWEEN %(df)s AND %(dt)s) AS fora_janela, "
        f"COUNT(*) FILTER (WHERE id IS NULL) AS sem_id, "
        f"COUNT(DISTINCT (date, loja_id, marketplace_id)) AS chaves "
        f"FROM {staging}",
        {"mkt": TIKTOK_MARKETPLACE_ID, "df": date_from, "dt": date_to},
    )
    v = cur.fetchone()
    if int(v["n"]) != len(registros):
        raise BackupIntegrityError("staging nao recebeu todas as linhas do backup")
    if int(v["fora_mkt"]):
        raise BackupIntegrityError("staging contem linha de outro marketplace")
    if int(v["fora_janela"]):
        raise BackupIntegrityError("staging contem linha fora da janela")
    if int(v["sem_id"]):
        raise BackupIntegrityError("staging contem linha sem id")
    if int(v["chaves"]) != len(registros):
        raise BackupIntegrityError("staging contem chave duplicada")

    apagadas = _delete_window(cur, date_from, date_to)
    cur.execute(
        f"INSERT INTO {validate_qualified(TARGET_TABLE)} "
        f"({_cols_sql(BACKUP_COLUMNS)}) "
        f"SELECT {_cols_sql(BACKUP_COLUMNS)} FROM {staging}"
    )
    inseridas = cur.rowcount
    destino = read_target_window(cur, date_from, date_to, BACKUP_COLUMNS)
    prova = reconcile(destino, registros, date_from, date_to)
    # Fidelidade das duas colunas que so' o restore recoloca.
    por_chave = {_chave(r): r for r in destino}
    for r in registros:
        d = por_chave.get(_chave(r))
        if d is None or d["id"] != r["id"] or d["ingested_at"] != r["ingested_at"]:
            raise RefreshError(
                "restore nao preservou id/ingested_at: nao e' a fotografia anterior"
            )
    return {"deleted": apagadas, "inserted": inseridas, "reconciliacao": prova}


# ---------------------------------------------------------------------------
# Dry-run — le fonte E destino, escreve nada
# ---------------------------------------------------------------------------

def _mes(d: date) -> str:
    return d.strftime("%Y-%m")


def _ticket(gmv, orders):
    if gmv is None or not orders:
        return None
    return (Decimal(str(gmv)) / Decimal(int(orders))).quantize(Decimal("0.01"))


def _por_grupo(rows: list[dict], chave) -> dict:
    grupos: dict = {}
    for r in rows:
        k = chave(r)
        g = grupos.setdefault(k, {"gmv": None, "orders": 0, "canceled_orders": 0})
        if r.get("gmv") is not None:
            g["gmv"] = (g["gmv"] or Decimal("0")) + Decimal(str(r["gmv"]))
        g["orders"] += int(r.get("orders") or 0)
        g["canceled_orders"] += int(r.get("canceled_orders") or 0)
    for k, g in grupos.items():
        g["avg_ticket"] = _ticket(g["gmv"], g["orders"])
        g["gmv"] = None if g["gmv"] is None else str(g["gmv"])
        if g["avg_ticket"] is not None:
            g["avg_ticket"] = str(g["avg_ticket"])
    return grupos


def compute_impact(atual: list[dict], novo: list[dict],
                   date_from: date, date_to: date) -> dict:
    """O que MUDARIA. Puro: nao toca banco, nao escreve, nao fabrica linha.

    As quatro classes de chave sao disjuntas e fecham: inseridas + removidas +
    alteradas + inalteradas cobre a uniao das duas fotografias.
    """
    a_por_chave = {_chave(r): r for r in atual}
    n_por_chave = {_chave(r): r for r in novo}
    a_keys, n_keys = set(a_por_chave), set(n_por_chave)

    inseridas = sorted(n_keys - a_keys)
    removidas = sorted(a_keys - n_keys)
    comuns = a_keys & n_keys
    alteradas = sorted(
        k for k in comuns
        if _comparavel(a_por_chave[k]) != _comparavel(n_por_chave[k])
    )
    inalteradas = sorted(comuns - set(alteradas))

    ag_a, ag_n = _aggregates(atual), _aggregates(novo)
    deltas = {}
    for k in sorted(set(ag_a) | set(ag_n)):
        va, vn = ag_a.get(k), ag_n.get(k)
        if va is None and vn is None:
            deltas[k] = None
        elif k in MANIFEST_INT_AGGREGATES:
            deltas[k] = int(vn or 0) - int(va or 0)
        else:
            deltas[k] = str(Decimal(str(vn or "0")) - Decimal(str(va or "0")))

    dias_esperados = set(dias_da_janela(date_from, date_to))
    marcas_possiveis = set(BRAND_TO_LOJA)
    combos_novo = {(r["date"], LOJA_TO_BRAND.get(r["loja_id"])) for r in novo}
    ausentes = len(dias_esperados) * len(marcas_possiveis) - len(combos_novo)

    def cobertura(rows):
        datas = [r["date"] for r in rows]
        return {
            "linhas": len(rows),
            "min_date": min(datas).isoformat() if datas else None,
            "max_date": max(datas).isoformat() if datas else None,
            "dias_distintos": len(set(datas)),
            "lojas": sorted({r["loja_id"] for r in rows}),
        }

    return {
        "linhas_atuais": len(atual),
        "linhas_novas": len(novo),
        "chaves_inseridas": len(inseridas),
        "chaves_removidas_orfas": len(removidas),
        "chaves_alteradas": len(alteradas),
        "chaves_inalteradas": len(inalteradas),
        "soma_das_quatro_classes": (
            len(inseridas) + len(removidas) + len(alteradas) + len(inalteradas)
        ),
        "uniao_das_chaves": len(a_keys | n_keys),
        "agregados_antigos": ag_a,
        "agregados_novos": ag_n,
        "deltas": deltas,
        "por_mes_antigo": _por_grupo(atual, lambda r: _mes(r["date"])),
        "por_mes_novo": _por_grupo(novo, lambda r: _mes(r["date"])),
        "por_marca_antigo": _por_grupo(
            atual, lambda r: LOJA_TO_BRAND.get(r["loja_id"], str(r["loja_id"]))),
        "por_marca_novo": _por_grupo(
            novo, lambda r: LOJA_TO_BRAND.get(r["loja_id"], str(r["loja_id"]))),
        "cobertura_atual": cobertura(atual),
        "cobertura_nova": cobertura(novo),
        "combinacoes_marca_dia_ausentes": ausentes,
        "nota_combinacoes": (
            "combinacao marca x dia ausente NAO e' preenchida com linha zero: "
            "pode ser ausencia legitima de pedidos. Reportada para revisao."
        ),
        "amostra_removidas": [
            {"date": k[0].isoformat(), "loja_id": k[1]} for k in removidas[:10]
        ],
    }


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

def default_run_id(modo: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return sanitize_run_id(f"{_RUN_ID_PREFIX}:{modo}:{stamp}")


def run_refresh(date_from: date | None, date_to: date | None, apply: bool,
                run_id: str | None, backup_dir: Path | None,
                agora: datetime | None = None) -> dict:
    """Refresh. Sem `apply`, le fonte E destino e reporta o que MUDARIA."""
    cutoff = operational_cutoff(agora)
    inicio, fim = resolve_window(date_from, date_to, cutoff)
    relatorio = {
        "modo": "refresh", "applied": apply, "cutoff": cutoff.isoformat(),
        "date_from": inicio.isoformat(), "date_to": fim.isoformat(),
        "marketplace_id": TIKTOK_MARKETPLACE_ID,
    }
    if apply:
        if not run_id:
            raise RefreshError("--apply exige --run-id explicito.")
        if backup_dir is None:
            raise RefreshError("--apply exige --backup-dir explicito.")

    snapshot = read_source_snapshot(inicio, fim)
    relatorio["fonte"] = snapshot.provas
    relatorio["pre_condicoes"] = assert_source_contract(snapshot)

    if not apply:
        # Dry-run: le o destino em sessao READ-ONLY. Zero advisory lock, zero
        # LOCK TABLE, zero backup, zero DDL/DML.
        neon = _neon_readonly(_get_neon_url())
        try:
            cur = neon.cursor()
            atual = read_target_window(cur, inicio, fim, WRITE_COLUMNS)
            cur.close()
        finally:
            neon.close()
        relatorio["impacto"] = compute_impact(atual, snapshot.canonical_rows,
                                              inicio, fim)
        relatorio["resultado"] = "dry-run; nada escrito"
        return relatorio

    neon = _neon_session(_get_neon_url())
    try:
        acquire_advisory_lock(neon)
        relatorio["lock_advisory"] = f"pg_advisory_lock({ADVISORY_LOCK_KEY})"

        # A transacao abre ANTES da leitura do estado anterior: o advisory lock
        # so' exclui outras instancias deste CLI, e o pipeline diario escreve na
        # mesma tabela sem conhece-lo. Sem o LOCK TABLE aqui, outro writer
        # poderia mudar a janela entre a leitura e o DELETE, e o backup deixaria
        # de representar o estado imediatamente anterior a publicacao.
        neon.autocommit = False
        try:
            cur = neon.cursor()
            cur.execute(f"SET LOCAL statement_timeout = '{TARGET_STATEMENT_TIMEOUT}'")
            cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            lock_target_table(cur)
            relatorio["lock_tabela"] = TABLE_LOCK_MODE

            antes = read_target_window(cur, inicio, fim, BACKUP_COLUMNS)
            relatorio["destino_antes"] = len(antes)
            # Backup gravado e RELIDO ainda sob o lock, antes do primeiro DML.
            relatorio["backup"] = write_backup(backup_dir, inicio, fim, antes, run_id)

            relatorio["publicacao"] = publish_in_transaction(cur, snapshot)
            neon.commit()
            cur.close()
            relatorio["resultado"] = "publicado"
        except Exception:
            neon.rollback()
            raise
        finally:
            try:
                neon.autocommit = True
            except Exception:  # noqa: BLE001
                pass
        return relatorio
    finally:
        release_advisory_lock(neon)
        neon.close()


def run_restore(backup_dir: Path, apply: bool, run_id: str | None,
                date_from: date | None = None,
                date_to: date | None = None) -> dict:
    """Restore. Com `--apply`, a janela e' OBRIGATORIA e conferida no manifesto.

    Exigir a janela no CLI impede que o proprio arquivo decida o que sera
    apagado: um manifesto adulterado com outra janela seria recusado por nao
    coincidir com o que o operador declarou.
    """
    if apply:
        if not run_id:
            raise RefreshError("--apply exige --run-id explicito.")
        if date_from is None or date_to is None:
            raise RefreshError(
                "--mode restore --apply exige --date-from e --date-to explicitos: "
                "a janela apagada nao pode ser decidida pelo proprio arquivo."
            )
    backup = load_backup(backup_dir, date_from, date_to)
    relatorio = {
        "modo": "restore", "applied": apply,
        "date_from": backup["date_from"].isoformat(),
        "date_to": backup["date_to"].isoformat(),
        "row_count": backup["row_count"], "colunas": len(BACKUP_COLUMNS),
        "csv_sha256": backup["csv_sha256"],
        "manifest_sha256": backup["manifest_sha256"],
    }
    if not apply:
        relatorio["resultado"] = "dry-run; backup valido, nada escrito"
        return relatorio

    neon = _neon_session(_get_neon_url())
    try:
        acquire_advisory_lock(neon)
        relatorio["lock_advisory"] = f"pg_advisory_lock({ADVISORY_LOCK_KEY})"
        neon.autocommit = False
        try:
            cur = neon.cursor()
            cur.execute(f"SET LOCAL statement_timeout = '{TARGET_STATEMENT_TIMEOUT}'")
            cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            lock_target_table(cur)
            relatorio["lock_tabela"] = TABLE_LOCK_MODE
            relatorio["restauracao"] = restore_in_transaction(cur, backup)
            neon.commit()
            cur.close()
            relatorio["resultado"] = "restaurado"
        except Exception:
            neon.rollback()
            raise
        finally:
            try:
                neon.autocommit = True
            except Exception:  # noqa: BLE001
                pass
        return relatorio
    finally:
        release_advisory_lock(neon)
        neon.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="refresh_tiktok_daily_contract",
        description=(
            "Refresh historico do TikTok Daily sob o contrato comercial do Gate "
            "DQ-TK1. Remove chaves orfas, que o UPSERT do pipeline diario nunca "
            "remove. SEM --apply o comando le a fonte E o destino e reporta o "
            "impacto (chaves inseridas, removidas, alteradas e inalteradas, "
            "agregados e deltas), sem escrever, sem lock e sem backup. Nao "
            "agenda, nao dorme, nao repete e nao tenta de novo."
        ),
    )
    p.add_argument("--mode", choices=("refresh", "restore"), default="refresh")
    p.add_argument("--date-from", help="inicio inclusivo (YYYY-MM-DD); "
                                       f"default {DEFAULT_DATE_FROM.isoformat()}. "
                                       "Obrigatorio em restore --apply.")
    p.add_argument("--date-to", help="fim inclusivo (YYYY-MM-DD); default D-1. "
                                     "Obrigatorio em restore --apply.")
    p.add_argument("--backup-dir", help="diretorio EXISTENTE para o backup "
                                        "(obrigatorio com --apply)")
    p.add_argument("--apply", action="store_true",
                   help="EFETIVA a escrita. Sem esta flag, tudo e somente leitura.")
    p.add_argument("--run-id", help="identificador da execucao (obrigatorio com --apply)")
    return p


def _parse_date(valor: str | None) -> date | None:
    if valor is None:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError as exc:
        raise RefreshError(f"data invalida: {valor!r} (esperado YYYY-MM-DD)") from exc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.apply:
        print("MODO DRY-RUN (sem --apply): le fonte e destino, nao escreve nada.")
    run_id = sanitize_run_id(args.run_id) if args.run_id else None
    try:
        if args.mode == "restore":
            if not args.backup_dir:
                raise RefreshError("--mode restore exige --backup-dir.")
            rel = run_restore(Path(args.backup_dir), args.apply, run_id,
                              _parse_date(args.date_from), _parse_date(args.date_to))
        else:
            rel = run_refresh(
                _parse_date(args.date_from), _parse_date(args.date_to),
                args.apply, run_id,
                Path(args.backup_dir) if args.backup_dir else None,
            )
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI
        print(f"FALHA: {sanitize_error_message(exc)}", file=sys.stderr)
        return 2
    for k, v in rel.items():
        print(f"{k:24s}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
