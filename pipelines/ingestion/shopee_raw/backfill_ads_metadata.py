"""
Backfill HISTÓRICO e CONTROLADO (Fase Staging Shopee 2A, Gate 2B — CLI
operacional desde 2026-07-07) de `raw.shopee_ingestion_file.source_metadata`
para os 10 manifestos `source_type='ads'` conhecidos hoje, usando o
preâmbulo do CSV local correspondente (ver `ads_metadata.py`).

A migration `db/sql/raw/shopee_raw_add_source_metadata.sql` já foi aplicada
na primary (2026-07-07) — coluna `source_metadata jsonb` e constraint
`ck_shopee_ingestion_file_source_metadata_is_object` confirmadas e
validadas. Este módulo ainda não aplicou nenhum backfill real: `--dry-run`
é somente leitura e pode ser executado livremente; `--apply-confirmed`
exige autorização explícita separada a cada execução (ver seção "Uso" e
`main()` abaixo).

## Uso

    python -m pipelines.ingestion.shopee_raw.backfill_ads_metadata --dry-run

    # Só depois de autorização explícita separada:
    python -m pipelines.ingestion.shopee_raw.backfill_ads_metadata \\
        --apply-confirmed --audit-path <caminho fora do repo ou .gitignore>

`--dry-run` nunca abre conexão de escrita — só uma sessão explicitamente
`postgresql_readonly=True` na mesma primary usada pelo secret de escrita
(precisa ser a mesma credencial porque a réplica de leitura, `DATAMART_
DATABASE_URL`, está sujeita a lag e não é adequada para confirmar o estado
mais recente da migration). `--apply-confirmed` é o único caminho que abre
uma conexão de escrita de verdade, e só chega a fazê-lo depois de: secret
com as 2 chaves esperadas (`I_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1`
incluído), preflight de escrita limpo (`write_conn.run_preflight`), estado
da migration confirmado (coluna/constraint/sem drift de contagem) e um
plano 10/10 recalculado NA HORA (nunca reaproveita um plano de uma
chamada anterior). `restore_from_backup_atomic` continua sem CLI — só
testado, nunca exposto como comando.

## Validação compartilhada (revisão de 2026-07-07)

`validate_record_identity`, `validate_applied_metadata` e
`validate_manifest_scope` são a FONTE ÚNICA das regras de identidade
técnica, escopo exato (10/5×2) e formato da metadata aplicada — usadas
por `plan_backfill`, `apply_backfill_atomic` (que revalida `plan.items`
por conta própria, nunca confiando em `plan.ready`) e
`_validate_backup_records` (restore). Nenhuma das três tem uma cópia
divergente das mesmas regras. As duas primeiras NUNCA levantam exceção,
seja qual for o tipo de entrada — são o portão de type-safety que
antecede qualquer uso de valor em `set()`/`dict`/comparação.

Este NÃO é um backfill genérico reutilizável para qualquer volume futuro
de ads pendente — é um script de UMA aplicação, escopado ao estado
conhecido e auditado em 2026-07-06: as 5 marcas OFICIAIS de
`pipelines.connectors.shopee.connector.BRANDS_IN_SCOPE` (apice, barbours,
kokeshi, lescent, rituaria), 2 arquivos cada = 10 manifestos. Uma futura
ingestão incremental de ads passa pelo caminho normal (`writer.py`, que já
extrai `source_metadata` na mesma transação do arquivo) e nunca por este
script.

## Desenho em duas fases

**Fase A — `plan_backfill` (somente leitura, sempre pode ser executada)**

1. Localiza TODOS os manifestos `source_type='ads'` com `source_metadata
   IS NULL`.
2. **Escopo exato exigido** (`validate_manifest_scope`): exatamente
   `EXPECTED_TOTAL_PENDING_ADS_MANIFESTS` (10) manifestos; o CONJUNTO de
   marcas presentes deve ser EXATAMENTE `EXPECTED_BRANDS` (as 5 marcas
   oficiais — não só "5 marcas quaisquer"); exatamente
   `EXPECTED_FILES_PER_BRAND` (2) arquivos por marca; 10 `file_id`s únicos;
   10 `file_sha256` únicos; `source_type == 'ads'` em todos. Qualquer
   desvio aborta o plano IMEDIATAMENTE, antes até de tentar ler qualquer
   arquivo local.
3. Para cada um dos 10, localiza o arquivo local por `file_id` +
   reverificação de `file_sha256` — NUNCA o nome do arquivo sozinho.
4. Extrai e valida o metadado via `ads_metadata.parse_ads_preamble`.
   Falhas de LEITURA/DECODIFICAÇÃO do arquivo (`SourceReadError`, `OSError`)
   são capturadas e viram um problema sanitizado do plano.
5. **Tudo-ou-nada**: qualquer problema em qualquer um dos 10 derruba o
   plano inteiro (`ready=False`, `items=[]`).

**Fase B — `apply_backfill_atomic` (transação real, NUNCA chamada por
`main()` nesta fase)**

1. Confirmação DUPLA obrigatória (`confirm_flag` + `confirm_secret_value
   == "1"`).
2. `audit_path`/`repo_root` OBRIGATÓRIOS (sem valor padrão). Validados
   ANTES de tocar o banco: recusa arquivo preexistente, recusa caminho
   dentro do repositório sem `.gitignore`.
3. `SET LOCAL lock_timeout`/`statement_timeout` + `pg_advisory_xact_lock`
   (mesma chave de `write_conn.ADVISORY_LOCK_KEY`) + `LOCK TABLE
   raw.shopee_ingestion_file IN SHARE MODE`.
4. **Revalidação do ESCOPO GLOBAL sob o lock**: relê TODOS os manifestos
   `source_type='ads' AND source_metadata IS NULL` (não só os 10 do
   plano) e confirma que o conjunto de `file_id`s é EXATAMENTE o do plano
   — nem um a mais (ex.: um 11º manifesto que apareceu entre a Fase A e o
   lock), nem um a menos. Depois, revalida CADA um dos 10 campo a campo
   (`source_filename`, `file_sha256`, `brand`, `source_type`,
   `source_metadata IS NULL`). Qualquer diferença aborta tudo ANTES do
   backup e dos UPDATEs.
5. Monta o **backup auditável** e publica de forma ATÔMICA e SEM
   POSSIBILIDADE DE SOBRESCRITA: escreve em arquivo temporário criado com
   exclusividade (`tempfile.mkstemp`) no mesmo diretório, `flush`+`fsync`,
   e publica com `os.link` (cria uma segunda entrada de diretório apontando
   pro mesmo arquivo — em NTFS/Windows e em POSIX, falha com
   `FileExistsError` se o destino já existir, nunca sobrescreve
   silenciosamente; diferente de `os.rename`/`os.replace`, que substituem o
   destino). O temporário é sempre removido, em sucesso ou falha. Relê o
   arquivo publicado, revalida a estrutura, calcula o SHA-256. Só DEPOIS
   disso o primeiro UPDATE roda.
6. `UPDATE ... WHERE file_id = :id AND file_sha256 = :hash AND
   source_metadata IS NULL` por item. `rowcount != 1` é CONFLITO — aborta
   tudo.
7. Reconcilia 10/10 contra o valor aplicado.
8. `COMMIT` só se tudo passar; `ROLLBACK` explícito em qualquer
   divergência. Sem retry automático.

## Plano de restauração (`restore_from_backup_atomic`) — NUNCA EXECUTADO

Trata o arquivo de backup como ENTRADA NÃO CONFIÁVEL (pode ter sido
adulterado, truncado, ou vir de uma execução diferente):

1. Confirmação dupla obrigatória.
2. `expected_backup_sha256` OBRIGATÓRIO: o SHA-256 do arquivo em disco é
   calculado e comparado ANTES de abrir qualquer cursor — mismatch aborta
   sem tocar o banco.
3. Validação estrutural COMPLETA do JSON (`_validate_backup_records`):
   lista no nível superior; exatamente 10 registros; sem chave extra/
   faltante por registro; `file_id`s únicos; `file_sha256` válidos (64 hex)
   e únicos; conjunto de marcas EXATAMENTE o oficial, 2 por marca;
   `source_type == 'ads'`; `source_metadata_before` deve ser `None`;
   `source_metadata_applied` deve ser objeto com EXATAMENTE `period_start`,
   `period_end`, `report_created_at`, `shop_id` (sem chave extra), datas em
   formato ISO e `period_start <= period_end`, `shop_id` só dígitos.
4. Sob o lock, revalida `file_id`/`source_filename`/`file_sha256`/`brand`/
   `source_type`/`source_metadata` (== o valor aplicado registrado) de
   cada um dos 10.
5. `UPDATE` usa compare-and-swap incluindo o `source_metadata` ATUAL no
   `WHERE` (nunca confia só no `file_id`+hash).
6. Relê os 10 após o UPDATE, confirma que TODOS voltaram exatamente para
   `source_metadata_before`, e só então `COMMIT`. Qualquer divergência →
   `ROLLBACK` integral, sem retry.

Nenhum teste deste módulo chama `apply_backfill_atomic`/
`restore_from_backup_atomic` contra um banco real — só contra conexão
falsa.

## Ordem operacional completa (ver também db/sql/raw/shopee_raw_add_source_metadata.sql)

1. Commit/revisão deste código (mapping/writer/backfill/testes) — feito.
2. Aplicar SOMENTE a migration `shopee_raw_add_source_metadata.sql` — não
   o DDL base (esse já está atualizado neste working tree, é só para
   ambientes NOVOS futuros) e não a staging. **Feito em 2026-07-07**:
   coluna e constraint confirmadas e validadas na primary.
3. Validar a coluna e a constraint (`information_schema.columns` +
   `pg_constraint`) antes de prosseguir — **feito** (parte do Gate 1/3 da
   migration); `--dry-run` deste módulo revalida o mesmo estado a cada
   execução (`_check_migration_state`), para detectar drift.
4. Executar este backfill histórico dos 10 manifestos (`apply_backfill_atomic`,
   via `--apply-confirmed`) — **pendente, autorização separada exigida**.
5. Reconciliar 10/10 (já embutido no passo 7 da Fase B; reconferir
   manualmente antes de seguir).
6. Executar o preview read-only completo (`pipelines/staging/shopee/preview.py`)
   contra 100% da Raw — gate obrigatório antes do próximo passo.
7. Só depois disso considerar aplicar o DDL/transform da staging
   (`db/sql/staging/*.sql`) — nunca antes.

**Risco operacional que já passou (histórico)**: entre o commit do código
(passo 1) e a aplicação da migration (passo 2), o `writer.py` dependia da
coluna `source_metadata` existir — uma ingestão Raw nessa janela teria
rejeitado o arquivo `ads` inteiro (política success-only). A migration já
foi aplicada, então esse risco específico não existe mais; o mesmo cuidado
se aplica a qualquer nova coluna futura que dependa de uma migration ainda
não aplicada.

Pré-requisitos para uma execução real do backfill (passo 4):
  1. Migration `db/sql/raw/shopee_raw_add_source_metadata.sql` aplicada —
     **satisfeito** (2026-07-07).
  2. Credencial de escrita da Raw (`DATAMART_SHOPEE_WRITE_URL` via
     `.env.shopee-write.local`) — nunca `DATAMART_DATABASE_URL` —
     **satisfeito** (mesmo arquivo usado pela migration).
  3. Autorização explícita do usuário para a execução real do backfill —
     **pendente**, separada da autorização já dada para `--dry-run`.

`main()` executa `--dry-run` de verdade (somente leitura). `--apply-confirmed`
está implementado e testado, mas nenhuma chamada a ele está autorizada nesta
rodada — ver guardrails em `run_apply_confirmed`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import psycopg2.extras
from sqlalchemy import create_engine, text

from pipelines.common.config import settings
from pipelines.connectors.shopee.connector import BRANDS_IN_SCOPE
from pipelines.ingestion.shopee_raw import write_conn
from pipelines.ingestion.shopee_raw.ads_metadata import AdsPreambleError, parse_ads_preamble
from pipelines.ingestion.shopee_raw.hashing import sha256_file
from pipelines.ingestion.shopee_raw.inventory import SourceReadError, _decode_ads_csv

DEFAULT_WRITE_SECRET_PATH = Path(__file__).resolve().parents[3] / ".env.shopee-write.local"
REPO_ROOT = Path(__file__).resolve().parents[3]

# Escopo histórico EXATO desta aplicação (auditoria de 2026-07-06) — não é
# um parâmetro de configuração para volumes futuros. `EXPECTED_BRANDS` vem
# da fonte canônica única de marcas oficiais (a mesma usada pelo inventário
# e por toda a ingestão Raw) — nunca uma lista redigitada à mão.
EXPECTED_BRANDS = frozenset(BRANDS_IN_SCOPE)
EXPECTED_FILES_PER_BRAND = 2
EXPECTED_TOTAL_PENDING_ADS_MANIFESTS = len(EXPECTED_BRANDS) * EXPECTED_FILES_PER_BRAND

# Total de manifestos (todos os source_type) confirmado no Gate 1/3 da
# migration source_metadata (2026-07-07) — usado só como DETECTOR DE DRIFT
# no --dry-run (uma ingestão Raw nova nesta janela mudaria este número), não
# como um parâmetro de escopo do backfill em si (esse é sempre 10/5×2, ver
# EXPECTED_TOTAL_PENDING_ADS_MANIFESTS acima).
EXPECTED_TOTAL_MANIFESTS_AT_MIGRATION = 120

_RE_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_RE_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_RE_ISO_DATETIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
_RE_DIGITS_ONLY = re.compile(r"^[0-9]+$")

_APPLIED_METADATA_KEYS = frozenset({"period_start", "period_end", "report_created_at", "shop_id"})
_BACKUP_RECORD_KEYS = frozenset({
    "file_id", "source_filename", "file_sha256", "brand", "source_type",
    "source_metadata_before", "source_metadata_applied",
})


@dataclass(frozen=True)
class PendingItem:
    file_id: int
    source_filename: str
    file_sha256: str
    brand: str
    source_type: str
    metadata: dict


@dataclass
class BackfillPlan:
    ready: bool
    items: list[PendingItem] = field(default_factory=list)
    # Nunca contém valor de célula/loja — só file_id, brand e motivo estrutural.
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BackupRecord:
    file_id: int
    source_filename: str
    file_sha256: str
    brand: str
    source_type: str
    source_metadata_before: Optional[dict]
    source_metadata_applied: dict


@dataclass
class BackfillResult:
    outcome: str
    # "committed" | "aborted_plan_not_ready" | "aborted_confirmation_missing"
    # | "aborted_nothing_pending" | "aborted_items_invalid"
    # | "aborted_audit_path_invalid" | "aborted_backup_failed"
    # | "aborted_reconciliation_conflict"
    backup: list[BackupRecord] = field(default_factory=list)
    backup_sha256: Optional[str] = None
    updated_file_ids: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass
class RestoreResult:
    outcome: str
    # "committed" | "aborted_confirmation_missing" | "aborted_backup_sha_mismatch"
    # | "aborted_backup_invalid" | "aborted_reconciliation_conflict"
    restored_file_ids: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


class BackupIntegrityError(RuntimeError):
    """Backup gravado/lido não reconferiu com o esperado."""


def _pending_ads_manifests(conn) -> list[dict]:
    """Manifestos ads ainda sem source_metadata. Usa uma conexão
    SQLAlchemy (read-only ou de escrita, a critério do chamador) — só
    leitura nesta função."""
    rows = conn.execute(text(
        "SELECT file_id, source_filename, file_sha256, brand, source_type "
        "FROM raw.shopee_ingestion_file "
        "WHERE source_type = 'ads' AND source_metadata IS NULL "
        "ORDER BY file_id"
    )).mappings().all()
    return [dict(r) for r in rows]


def _valid_iso_date(value) -> Optional[date]:
    """`None` se `value` não for uma string no formato ISO `YYYY-MM-DD`
    COM calendário válido (regex sozinha aceita '2026-13-40'; o regex é só
    o pré-filtro de forma, `date.fromisoformat` valida o calendário de
    verdade e nunca propaga `ValueError` para fora desta função)."""
    if not isinstance(value, str) or not _RE_ISO_DATE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _valid_iso_datetime(value) -> bool:
    """Mesma ideia de `_valid_iso_date`, para `YYYY-MM-DDTHH:MM:SS`."""
    if not isinstance(value, str) or not _RE_ISO_DATETIME.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Validação COMPARTILHADA — fonte única usada por `plan_backfill`,
# `apply_backfill_atomic` e `_validate_backup_records` (restore). Nenhuma
# das três tem sua própria cópia divergente das regras de identidade
# técnica, escopo exato (10/5×2) ou formato da metadata aplicada.
#
# `validate_record_identity`/`validate_applied_metadata` NUNCA levantam
# exceção, seja qual for o tipo de entrada (string, lista, dict, bool,
# None, número) — são o portão de type-safety que precisa passar ANTES de
# qualquer valor ser usado como chave de `set()`/`dict` ou comparado com
# `<`/`>` em `validate_manifest_scope`.
# ---------------------------------------------------------------------------

def validate_record_identity(record) -> list[str]:
    """Type-safety de um registro candidato a manifesto/item/backup:
    `file_id` inteiro positivo (bool excluído explicitamente — `bool` é
    subclasse de `int` em Python, então `True`/`False` passariam num
    `isinstance(x, int)` ingênuo), `source_filename`/`brand`/`source_type`
    strings não vazias, `file_sha256` hexadecimal de 64 caracteres. Só
    depois desta função dizer "sem problemas" para TODOS os registros é
    seguro colocar `file_id`/`file_sha256`/`brand` em `set()` ou usar
    `brand` como chave de `dict` em `validate_manifest_scope`."""
    if not isinstance(record, dict):
        return ["registro não é um objeto"]

    problems: list[str] = []

    file_id = record.get("file_id")
    if isinstance(file_id, bool) or not isinstance(file_id, int) or file_id <= 0:
        problems.append("file_id inválido (esperado inteiro positivo)")

    filename = record.get("source_filename")
    if not isinstance(filename, str) or not filename:
        problems.append("source_filename inválido (esperado string não vazia)")

    file_hash = record.get("file_sha256")
    if not isinstance(file_hash, str) or not _RE_SHA256_HEX.match(file_hash):
        problems.append("file_sha256 inválido (esperado hexadecimal de 64 caracteres)")

    brand = record.get("brand")
    if not isinstance(brand, str) or not brand:
        problems.append("brand inválido (esperado string não vazia)")

    source_type = record.get("source_type")
    if not isinstance(source_type, str) or not source_type:
        problems.append("source_type inválido (esperado string não vazia)")

    return problems


def validate_applied_metadata(metadata) -> list[str]:
    """Type-safety + formato de um `source_metadata` já aplicado/a
    aplicar: deve ser um objeto com EXATAMENTE `period_start`,
    `period_end`, `report_created_at`, `shop_id` — sem chave extra, sem
    chave faltante — com datas/calendário válidos, `period_start <=
    period_end`, e `shop_id` só dígitos. Nunca levanta, seja qual for o
    tipo de `metadata` (lista, string, número, None...)."""
    if not isinstance(metadata, dict):
        return ["metadata inválida (esperado objeto)"]

    problems: list[str] = []
    extra = set(metadata.keys()) - _APPLIED_METADATA_KEYS
    missing = _APPLIED_METADATA_KEYS - set(metadata.keys())
    if extra:
        problems.append(f"metadata com chave(s) extra(s): {sorted(extra)}")
    if missing:
        problems.append(f"metadata com chave(s) ausente(s): {sorted(missing)}")
    if extra or missing:
        return problems

    start, end = metadata["period_start"], metadata["period_end"]
    created, shop_id = metadata["report_created_at"], metadata["shop_id"]

    start_date = _valid_iso_date(start)
    end_date = _valid_iso_date(end)
    if start_date is None:
        problems.append("period_start com formato ou calendário inválido")
    if end_date is None:
        problems.append("period_end com formato ou calendário inválido")
    if start_date is not None and end_date is not None and start_date > end_date:
        problems.append("period_start posterior a period_end")
    if not _valid_iso_datetime(created):
        problems.append("report_created_at com formato ou calendário inválido")
    if not isinstance(shop_id, str) or not _RE_DIGITS_ONLY.match(shop_id):
        problems.append("shop_id não é composto só por dígitos")

    return problems


def validate_manifest_scope(records) -> list[str]:
    """Escopo HISTÓRICO e CONTROLADO compartilhado — não um backfill
    genérico. Passo 1 (SEMPRE primeiro): `validate_record_identity` em
    CADA registro — se qualquer um falhar, retorna só esses problemas
    (nunca chega a colocar um valor não-type-safe num `set()`/`dict`).
    Só depois disso verifica: exatamente `EXPECTED_TOTAL_PENDING_ADS_
    MANIFESTS` (10) registros; `file_id`s únicos; `file_sha256` únicos;
    `source_type == 'ads'` em todos; o CONJUNTO de marcas EXATAMENTE igual
    a `EXPECTED_BRANDS` (as oficiais — não só "N marcas quaisquer");
    exatamente `EXPECTED_FILES_PER_BRAND` (2) arquivos por marca."""
    if not isinstance(records, list):
        return ["registros não formam uma lista"]

    identity_problems: list[str] = []
    for i, r in enumerate(records):
        identity_problems.extend(f"registro #{i}: {e}" for e in validate_record_identity(r))
    if identity_problems:
        return identity_problems

    total = len(records)
    if total != EXPECTED_TOTAL_PENDING_ADS_MANIFESTS:
        return [
            f"esperado exatamente {EXPECTED_TOTAL_PENDING_ADS_MANIFESTS} registros "
            f"(este backfill é histórico e controlado, não genérico), encontrado {total}"
        ]

    problems: list[str] = []

    file_ids = [r["file_id"] for r in records]
    if len(set(file_ids)) != len(file_ids):
        problems.append("file_id duplicado")

    hashes = [r["file_sha256"] for r in records]
    if len(set(hashes)) != len(hashes):
        problems.append("file_sha256 duplicado")

    wrong_source_type = sorted(r["file_id"] for r in records if r["source_type"] != "ads")
    if wrong_source_type:
        problems.append(f"registro(s) com source_type != 'ads': file_id={wrong_source_type}")

    by_brand: dict[str, int] = {}
    for r in records:
        by_brand[r["brand"]] = by_brand.get(r["brand"], 0) + 1

    if set(by_brand) != EXPECTED_BRANDS:
        problems.append(
            f"conjunto de marcas não bate com o oficial {sorted(EXPECTED_BRANDS)}: "
            f"encontrado {sorted(by_brand)}"
        )

    wrong_count = {b: n for b, n in sorted(by_brand.items()) if n != EXPECTED_FILES_PER_BRAND}
    if wrong_count:
        problems.append(f"marca(s) com quantidade de arquivos != {EXPECTED_FILES_PER_BRAND}: {wrong_count}")

    return problems


def plan_backfill(conn, data_path: Path) -> BackfillPlan:
    """FASE A — somente leitura. Escopo exato (fonte única:
    `validate_manifest_scope`) verificado ANTES de qualquer leitura de
    arquivo; tudo-ou-nada depois disso. A metadata extraída de cada
    arquivo TAMBÉM passa pela validação compartilhada
    (`validate_applied_metadata`) antes do plano ser declarado pronto —
    nunca confia cegamente no que `ads_metadata.parse_ads_preamble`
    devolveu, mesmo que aquele parser já garanta o formato por
    construção."""
    manifests = _pending_ads_manifests(conn)

    scope_problems = validate_manifest_scope(manifests)
    if scope_problems:
        return BackfillPlan(ready=False, items=[], problems=scope_problems)

    items: list[PendingItem] = []
    problems: list[str] = []

    for manifest in manifests:
        file_id = manifest["file_id"]
        local_path = data_path / manifest["source_filename"]
        if not local_path.exists():
            problems.append(f"file_id={file_id} brand={manifest['brand']}: arquivo local não encontrado")
            continue

        try:
            actual_hash = sha256_file(local_path)
        except OSError as exc:
            problems.append(
                f"file_id={file_id} brand={manifest['brand']}: falha ao ler arquivo para hash "
                f"({type(exc).__name__})"
            )
            continue

        if actual_hash != manifest["file_sha256"]:
            problems.append(f"file_id={file_id} brand={manifest['brand']}: hash local diverge do manifesto")
            continue

        try:
            lines, _ = _decode_ads_csv(local_path)
            preamble = parse_ads_preamble(lines)
        except (AdsPreambleError, SourceReadError, OSError) as exc:
            problems.append(
                f"file_id={file_id} brand={manifest['brand']}: falha ao ler/validar preâmbulo "
                f"({type(exc).__name__})"
            )
            continue

        metadata = preamble.to_jsonb_dict()
        metadata_problems = validate_applied_metadata(metadata)
        if metadata_problems:
            problems.extend(f"file_id={file_id} brand={manifest['brand']}: {e}" for e in metadata_problems)
            continue

        items.append(PendingItem(
            file_id=file_id,
            source_filename=manifest["source_filename"],
            file_sha256=manifest["file_sha256"],
            brand=manifest["brand"],
            source_type=manifest["source_type"],
            metadata=metadata,
        ))

    ready = not problems
    return BackfillPlan(ready=ready, items=items if ready else [], problems=problems)


def _validate_audit_path(audit_path: Path, repo_root: Path) -> Optional[str]:
    """Checagem RÁPIDA e antecipada (não é a garantia final — essa vem de
    `os.link` na publicação, que é imune à corrida TOCTOU entre esta
    checagem e a escrita). Recusa se o arquivo já existe, ou se está dentro
    do repositório sem `.gitignore`/já rastreado pelo git."""
    if audit_path.exists():
        return f"audit_path já existe (recusado — nunca sobrescrever um backup anterior): {audit_path.name}"

    resolved = audit_path.resolve()
    repo_resolved = repo_root.resolve()
    if resolved == repo_resolved or repo_resolved in resolved.parents:
        ignore_check = write_conn._run_git(["check-ignore", "-q", str(resolved)], cwd=repo_root)
        if ignore_check.returncode != 0:
            return f"audit_path está dentro do repositório mas NÃO coberto por .gitignore: {audit_path}"
        tracked_check = write_conn._run_git(["ls-files", "--error-unmatch", str(resolved)], cwd=repo_root)
        if tracked_check.returncode == 0:
            return f"audit_path está RASTREADO pelo git: {audit_path}"
    return None


def _write_audit_file_atomic(backup: list[BackupRecord], audit_path: Path, expected_file_ids: set[int]) -> str:
    """Publica o backup em `audit_path` SEM POSSIBILIDADE DE SOBRESCRITA:

    1. Cria um temporário com EXCLUSIVIDADE (`tempfile.mkstemp`, O_CREAT|
       O_EXCL) no mesmo diretório de `audit_path` (garante mesmo
       filesystem, necessário para o link a seguir).
    2. Escreve o conteúdo, `flush` + `os.fsync` antes de fechar.
    3. Publica com `os.link(tmp, audit_path)` — cria uma segunda entrada de
       diretório apontando para o mesmo arquivo; falha com
       `FileExistsError` se `audit_path` já existir (em NTFS e em POSIX) —
       NUNCA sobrescreve, ao contrário de `os.rename`/`os.replace`. Isso
       fecha a corrida TOCTOU entre a checagem antecipada
       (`_validate_audit_path`) e a publicação: mesmo que outro processo
       crie o destino nesse meio-tempo, `os.link` detecta e aborta.
    4. O temporário é removido em QUALQUER caminho (sucesso ou falha).
    5. Relê `audit_path` do disco, revalida a estrutura, retorna o SHA-256.

    Só identificadores técnicos e metadata — nunca payload/PII."""
    payload = [
        {
            "file_id": b.file_id,
            "source_filename": b.source_filename,
            "file_sha256": b.file_sha256,
            "brand": b.brand,
            "source_type": b.source_type,
            "source_metadata_before": b.source_metadata_before,
            "source_metadata_applied": b.source_metadata_applied,
        }
        for b in backup
    ]
    data = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")

    fd, tmp_name = tempfile.mkstemp(
        dir=str(audit_path.parent), prefix=audit_path.name + ".", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        try:
            os.link(tmp_path, audit_path)
        except FileExistsError:
            raise BackupIntegrityError(
                f"audit_path passou a existir entre a validação e a publicação "
                f"(corrida detectada, nada sobrescrito): {audit_path.name}"
            )
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    reread_text = audit_path.read_text(encoding="utf-8")
    reread_payload = json.loads(reread_text)
    if len(reread_payload) != len(expected_file_ids):
        raise BackupIntegrityError(
            f"backup relido do disco tem {len(reread_payload)} registro(s), "
            f"esperado {len(expected_file_ids)}"
        )
    reread_ids = {r["file_id"] for r in reread_payload}
    if reread_ids != expected_file_ids:
        raise BackupIntegrityError("backup relido do disco não contém exatamente os file_ids esperados")

    return sha256_file(audit_path)


def _item_identity_record(item: PendingItem) -> dict:
    """Adapta um `PendingItem` para o formato canônico de registro que
    `validate_manifest_scope` espera — mesma forma usada pelos manifestos
    de `plan_backfill` e pelos registros de backup do restore."""
    return {
        "file_id": item.file_id,
        "source_filename": item.source_filename,
        "file_sha256": item.file_sha256,
        "brand": item.brand,
        "source_type": item.source_type,
    }


def apply_backfill_atomic(
    conn,
    plan: BackfillPlan,
    *,
    confirm_flag: bool,
    confirm_secret_value: Optional[str],
    audit_path: Path,
    repo_root: Path,
) -> BackfillResult:
    """FASE B — a ÚNICA função deste módulo que executa DML real. `conn` é
    uma conexão psycopg2 de ESCRITA (`autocommit=False`) — nunca aberta
    por esta função. `audit_path`/`repo_root` são OBRIGATÓRIOS (sem valor
    padrão) — uma aplicação real nunca pode rodar sem backup auditável.

    NUNCA confia cegamente em `plan.ready=True`: revalida `plan.items`
    pela MESMA `validate_manifest_scope`/`validate_applied_metadata`
    compartilhada com `plan_backfill`/restore ANTES de abrir cursor — um
    `BackfillPlan` montado manualmente (ou corrompido em memória) com
    `ready=True` mas um conjunto de itens que não é exatamente o escopo
    histórico (10/5×2) é recusado aqui, independente do que `ready` diz.

    Revalida o ESCOPO GLOBAL sob o lock (não só os 10 file_ids do plano) —
    um 11º manifesto que apareça entre a Fase A e o lock é detectado e
    aborta tudo. `rowcount != 1` em qualquer UPDATE é CONFLITO — aborta a
    transação inteira. Nenhum retry automático."""
    if not plan.ready:
        return BackfillResult(outcome="aborted_plan_not_ready", problems=list(plan.problems))
    if not confirm_flag or confirm_secret_value != "1":
        return BackfillResult(
            outcome="aborted_confirmation_missing",
            problems=["confirmação dupla ausente (flag --apply-confirmed + I_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1)"],
        )
    if not plan.items:
        return BackfillResult(outcome="aborted_nothing_pending")

    independent_problems = validate_manifest_scope([_item_identity_record(item) for item in plan.items])
    for item in plan.items:
        independent_problems.extend(
            f"file_id={item.file_id}: {e}" for e in validate_applied_metadata(item.metadata)
        )
    if independent_problems:
        return BackfillResult(outcome="aborted_items_invalid", problems=independent_problems)

    audit_problem = _validate_audit_path(audit_path, repo_root)
    if audit_problem:
        return BackfillResult(outcome="aborted_audit_path_invalid", problems=[audit_problem])

    file_ids = [item.file_id for item in plan.items]

    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SET LOCAL statement_timeout = '60s'")
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (write_conn.ADVISORY_LOCK_KEY,))
        cur.execute("LOCK TABLE raw.shopee_ingestion_file IN SHARE MODE")

        # Revalidação do ESCOPO GLOBAL: relê TODOS os manifestos ads
        # pendentes (sem filtrar por file_id) para detectar um manifesto
        # ADICIONAL que tenha surgido entre a Fase A e este lock — a
        # revalidação por item (abaixo) sozinha nunca perceberia um 11º
        # manifesto, porque ela só olha para os file_ids que já estão no
        # plano.
        cur.execute(
            "SELECT file_id, source_filename, file_sha256, brand, source_type, source_metadata "
            "FROM raw.shopee_ingestion_file "
            "WHERE source_type = 'ads' AND source_metadata IS NULL "
            "ORDER BY file_id"
        )
        global_rows = {row[0]: row for row in cur.fetchall()}

        problems: list[str] = []
        plan_ids = set(file_ids)
        global_ids = set(global_rows)
        extra_global = sorted(global_ids - plan_ids)
        missing_global = sorted(plan_ids - global_ids)
        if extra_global:
            problems.append(
                f"manifesto(s) ads pendente(s) ADICIONAL(is) surgiram entre o planejamento e o "
                f"lock (escopo deixou de ser exatamente o do plano): file_id={extra_global}"
            )
        if missing_global:
            problems.append(f"manifesto(s) sumiram sob o lock: file_id={missing_global}")

        backup: list[BackupRecord] = []
        for item in plan.items:
            row = global_rows.get(item.file_id)
            if row is None:
                continue
            _, source_filename, file_sha256, brand, source_type, source_metadata = row
            if source_filename != item.source_filename:
                problems.append(f"file_id={item.file_id}: source_filename mudou sob o lock")
                continue
            if file_sha256 != item.file_sha256:
                problems.append(f"file_id={item.file_id}: hash mudou sob o lock desde a Fase A")
                continue
            if brand != item.brand:
                problems.append(f"file_id={item.file_id}: brand mudou sob o lock")
                continue
            if source_type != item.source_type:
                problems.append(f"file_id={item.file_id}: source_type mudou sob o lock")
                continue
            if source_metadata is not None:
                problems.append(f"file_id={item.file_id}: source_metadata deixou de ser NULL sob o lock")
                continue
            backup.append(BackupRecord(
                file_id=item.file_id,
                source_filename=source_filename,
                file_sha256=file_sha256,
                brand=brand,
                source_type=source_type,
                source_metadata_before=source_metadata,
                source_metadata_applied=item.metadata,
            ))

        if problems:
            conn.rollback()
            return BackfillResult(outcome="aborted_reconciliation_conflict", backup=backup, problems=problems)

        try:
            backup_sha256 = _write_audit_file_atomic(backup, audit_path, expected_file_ids=set(file_ids))
        except (OSError, BackupIntegrityError, json.JSONDecodeError) as exc:
            conn.rollback()
            return BackfillResult(
                outcome="aborted_backup_failed",
                backup=backup,
                problems=[f"falha ao gravar/validar backup atômico: {type(exc).__name__}"],
            )

        updated_ids: list[int] = []
        for item in plan.items:
            cur.execute(
                "UPDATE raw.shopee_ingestion_file SET source_metadata = %s "
                "WHERE file_id = %s AND file_sha256 = %s AND source_metadata IS NULL",
                (psycopg2.extras.Json(item.metadata), item.file_id, item.file_sha256),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return BackfillResult(
                    outcome="aborted_reconciliation_conflict",
                    backup=backup,
                    backup_sha256=backup_sha256,
                    problems=[
                        f"file_id={item.file_id}: UPDATE afetou {cur.rowcount} linha(s) "
                        "(esperado exatamente 1) — conflito concorrente, não um skip"
                    ],
                )
            updated_ids.append(item.file_id)

        cur.execute(
            "SELECT file_id, source_metadata FROM raw.shopee_ingestion_file "
            "WHERE file_id = ANY(%s) ORDER BY file_id",
            (file_ids,),
        )
        final_rows = {row[0]: row[1] for row in cur.fetchall()}

        reconcile_problems: list[str] = []
        missing_after = sorted(set(file_ids) - set(final_rows))
        if missing_after:
            reconcile_problems.append(f"manifesto(s) sumiram após o UPDATE: file_id={missing_after}")
        for item in plan.items:
            if item.file_id in final_rows and final_rows[item.file_id] != item.metadata:
                reconcile_problems.append(f"file_id={item.file_id}: source_metadata pós-UPDATE não bate com o esperado")

        if reconcile_problems:
            conn.rollback()
            return BackfillResult(
                outcome="aborted_reconciliation_conflict",
                backup=backup,
                backup_sha256=backup_sha256,
                updated_file_ids=updated_ids,
                problems=reconcile_problems,
            )

        conn.commit()
        return BackfillResult(
            outcome="committed", backup=backup, backup_sha256=backup_sha256, updated_file_ids=updated_ids
        )
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return BackfillResult(
            outcome="aborted_reconciliation_conflict",
            problems=[f"{type(exc).__name__} durante a Fase B — rollback completo executado"],
        )
    finally:
        cur.close()


def _validate_backup_records(records) -> list[str]:
    """Trata o JSON do backup como ENTRADA NÃO CONFIÁVEL — type-safety
    PRIMEIRO (nunca usa um valor em `set()`/`dict`/comparação antes de
    saber que é do tipo esperado), depois a validação de escopo
    COMPARTILHADA (`validate_manifest_scope` — mesma fonte de
    `plan_backfill`/`apply_backfill_atomic`), depois os campos
    específicos do formato de backup (`source_metadata_before`/
    `source_metadata_applied`). Retorna problemas sanitizados (nunca ecoa
    valor de célula; nomes de campo e marcas não são sensíveis). NUNCA
    levanta exceção, seja qual for o JSON de entrada."""
    if not isinstance(records, list):
        return ["backup não é uma lista JSON no nível superior"]

    # Passo 1 (type-safety antes de qualquer set()/dict): cada registro
    # precisa ser um objeto com EXATAMENTE as chaves do formato de backup
    # — só depois disso é seguro acessar `r["source_metadata_before"]`/
    # `r["source_metadata_applied"]` (chaves que `validate_record_identity`
    # não conhece, por isso este passo roda ANTES da validação
    # compartilhada, não depois).
    structural_problems: list[str] = []
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            structural_problems.append(f"registro #{i}: não é um objeto JSON")
            continue
        extra_keys = set(r.keys()) - _BACKUP_RECORD_KEYS
        missing_keys = _BACKUP_RECORD_KEYS - set(r.keys())
        if extra_keys:
            structural_problems.append(f"registro #{i}: chave(s) inesperada(s): {sorted(extra_keys)}")
        if missing_keys:
            structural_problems.append(f"registro #{i}: chave(s) ausente(s): {sorted(missing_keys)}")
    if structural_problems:
        return structural_problems

    # Passo 2: identidade técnica + escopo exato (10/5×2) — fonte única
    # compartilhada com plan_backfill/apply_backfill_atomic. Só chega aqui
    # sabendo que cada registro é um dict com as 7 chaves certas; ainda
    # não sabe se file_id/file_sha256/brand/source_type têm o TIPO certo
    # — é exatamente isso que validate_manifest_scope checa antes de
    # qualquer set()/dict agregado.
    scope_problems = validate_manifest_scope(records)
    if scope_problems:
        return scope_problems

    # Passo 3: campos específicos do formato de backup. Neste ponto,
    # file_id/source_filename/file_sha256/brand/source_type já são
    # type-safe e formam o escopo exato esperado (garantido pelo passo 2).
    problems: list[str] = []
    for r in records:
        fid = r["file_id"]
        if r["source_metadata_before"] is not None:
            problems.append(f"file_id={fid}: source_metadata_before deveria ser NULL neste backfill")
        problems.extend(
            f"file_id={fid}: {e}" for e in validate_applied_metadata(r["source_metadata_applied"])
        )

    return problems


def restore_from_backup_atomic(
    conn,
    audit_path: Path,
    *,
    confirm_flag: bool,
    confirm_secret_value: Optional[str],
    expected_backup_sha256: str,
) -> RestoreResult:
    """PLANO DE RESTAURAÇÃO — NUNCA CHAMADO/EXECUTADO nesta fase.

    O arquivo de backup é tratado como ENTRADA NÃO CONFIÁVEL: o SHA-256 é
    conferido ANTES de abrir qualquer cursor, e a estrutura completa é
    validada (`_validate_backup_records`) antes de usar qualquer campo
    para revalidar/reverter algo no banco. Sob o lock, cada registro é
    revalidado campo a campo contra o estado atual; o UPDATE de
    restauração usa compare-and-swap incluindo o `source_metadata` ATUAL
    no `WHERE` (nunca confia só em file_id+hash). Reconciliação pós-UPDATE
    obrigatória antes do COMMIT."""
    if not confirm_flag or confirm_secret_value != "1":
        return RestoreResult(outcome="aborted_confirmation_missing", problems=["confirmação dupla ausente"])

    if not isinstance(expected_backup_sha256, str) or not _RE_SHA256_HEX.match(expected_backup_sha256):
        return RestoreResult(
            outcome="aborted_backup_invalid",
            problems=["expected_backup_sha256 inválido (esperado hexadecimal de 64 caracteres)"],
        )

    try:
        actual_sha256 = sha256_file(audit_path)
    except OSError as exc:
        return RestoreResult(outcome="aborted_backup_invalid", problems=[f"falha ao ler backup: {type(exc).__name__}"])

    if actual_sha256 != expected_backup_sha256:
        return RestoreResult(
            outcome="aborted_backup_sha_mismatch",
            problems=["SHA-256 do arquivo de backup não bate com o esperado — arquivo pode ter sido alterado"],
        )

    try:
        raw_text = audit_path.read_text(encoding="utf-8")
        records = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        return RestoreResult(outcome="aborted_backup_invalid", problems=[f"falha ao ler backup: {type(exc).__name__}"])

    structure_problems = _validate_backup_records(records)
    if structure_problems:
        return RestoreResult(outcome="aborted_backup_invalid", problems=structure_problems)

    file_ids = [r["file_id"] for r in records]

    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SET LOCAL statement_timeout = '60s'")
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (write_conn.ADVISORY_LOCK_KEY,))
        cur.execute("LOCK TABLE raw.shopee_ingestion_file IN SHARE MODE")

        cur.execute(
            "SELECT file_id, source_filename, file_sha256, brand, source_type, source_metadata "
            "FROM raw.shopee_ingestion_file WHERE file_id = ANY(%s) ORDER BY file_id",
            (file_ids,),
        )
        current = {row[0]: row for row in cur.fetchall()}

        problems: list[str] = []
        missing = sorted(set(file_ids) - set(current))
        if missing:
            problems.append(f"manifesto(s) sumiram: file_id={missing}")

        for r in records:
            row = current.get(r["file_id"])
            if row is None:
                continue
            _, source_filename, file_sha256, brand, source_type, source_metadata = row
            if source_filename != r["source_filename"]:
                problems.append(f"file_id={r['file_id']}: source_filename mudou desde o backfill")
            if file_sha256 != r["file_sha256"]:
                problems.append(f"file_id={r['file_id']}: hash mudou desde o backfill")
            if brand != r["brand"]:
                problems.append(f"file_id={r['file_id']}: brand mudou desde o backfill")
            if source_type != r["source_type"]:
                problems.append(f"file_id={r['file_id']}: source_type mudou desde o backfill")
            if source_metadata != r["source_metadata_applied"]:
                problems.append(
                    f"file_id={r['file_id']}: source_metadata atual não bate com o valor "
                    "aplicado registrado no backup — recusando reverter às cegas"
                )

        if problems:
            conn.rollback()
            return RestoreResult(outcome="aborted_reconciliation_conflict", problems=problems)

        restored_ids: list[int] = []
        for r in records:
            cur.execute(
                "UPDATE raw.shopee_ingestion_file SET source_metadata = %s "
                "WHERE file_id = %s AND file_sha256 = %s AND source_metadata = %s",
                (
                    r["source_metadata_before"],
                    r["file_id"],
                    r["file_sha256"],
                    psycopg2.extras.Json(r["source_metadata_applied"]),
                ),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return RestoreResult(
                    outcome="aborted_reconciliation_conflict",
                    problems=[f"file_id={r['file_id']}: UPDATE de restauração afetou {cur.rowcount} linha(s)"],
                )
            restored_ids.append(r["file_id"])

        cur.execute(
            "SELECT file_id, source_metadata FROM raw.shopee_ingestion_file "
            "WHERE file_id = ANY(%s) ORDER BY file_id",
            (file_ids,),
        )
        final_rows = {row[0]: row[1] for row in cur.fetchall()}

        recon_problems: list[str] = []
        missing_after = sorted(set(file_ids) - set(final_rows))
        if missing_after:
            recon_problems.append(f"manifesto(s) sumiram após a restauração: file_id={missing_after}")
        for r in records:
            if r["file_id"] in final_rows and final_rows[r["file_id"]] != r["source_metadata_before"]:
                recon_problems.append(
                    f"file_id={r['file_id']}: source_metadata pós-restauração não bate com source_metadata_before"
                )

        if recon_problems:
            conn.rollback()
            return RestoreResult(
                outcome="aborted_reconciliation_conflict", restored_file_ids=restored_ids, problems=recon_problems
            )

        conn.commit()
        return RestoreResult(outcome="committed", restored_file_ids=restored_ids)
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        return RestoreResult(outcome="aborted_reconciliation_conflict", problems=[f"{type(exc).__name__} durante a restauração"])
    finally:
        cur.close()


# =============================================================================
# CLI operacional — --dry-run (somente leitura, sempre pode ser executado) e
# --apply-confirmed (escrita real, protegido por múltiplos guardrails; ver
# `run_apply_confirmed`). `restore_from_backup_atomic` NÃO tem comando aqui
# — continua só testado, nunca exposto.
# =============================================================================


@dataclass
class MigrationStateCheck:
    """Estado da migration `source_metadata` confirmado NA HORA (não
    reaproveita a validação feita quando a migration foi aplicada) — ver
    `_check_migration_state`. `ok=True` só se coluna, constraint e as
    contagens de drift (total de manifestos, 100% NULL) baterem com o
    esperado."""
    ok: bool
    in_recovery: Optional[bool] = None
    column_exists: bool = False
    column_type: Optional[str] = None
    constraint_validated: bool = False
    total_manifests: Optional[int] = None
    manifests_with_metadata_not_null: Optional[int] = None
    problems: list[str] = field(default_factory=list)


def _check_migration_state(conn) -> MigrationStateCheck:
    """Confirma, nesta ordem (para nunca rodar uma query que dependeria de
    algo ainda não confirmado): `pg_is_in_recovery()=false` (a sessão
    precisa ser a PRIMARY); coluna `source_metadata` existe e é `jsonb`;
    constraint `ck_shopee_ingestion_file_source_metadata_is_object` existe
    e está `convalidated`; e só então o drift de contagem desde a migration
    (2026-07-07: 120 manifestos, 120/120 NULL) — um desvio aqui não impede
    a leitura, mas marca `ok=False` (sinal de ingestão Raw nova ou backfill
    parcial já aplicado por fora deste módulo). Conexão SQLAlchemy somente
    leitura — nunca escreve."""
    check = MigrationStateCheck(ok=False)

    check.in_recovery = conn.execute(text("SELECT pg_is_in_recovery()")).scalar()
    if check.in_recovery:
        check.problems.append("pg_is_in_recovery()=true -- alvo não é a primary")
        return check

    col = conn.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema='raw' AND table_name='shopee_ingestion_file' "
        "AND column_name='source_metadata'"
    )).fetchone()
    check.column_exists = col is not None
    check.column_type = col[0] if col else None
    if not check.column_exists:
        check.problems.append("coluna source_metadata não existe -- migration não aplicada")
        return check
    if check.column_type != "jsonb":
        check.problems.append(f"coluna source_metadata tem tipo inesperado: {check.column_type}")
        return check

    con = conn.execute(text(
        "SELECT convalidated FROM pg_constraint "
        "WHERE conname = 'ck_shopee_ingestion_file_source_metadata_is_object'"
    )).fetchone()
    if con is None:
        check.problems.append("constraint ck_shopee_ingestion_file_source_metadata_is_object não existe")
        return check
    check.constraint_validated = bool(con[0])
    if not check.constraint_validated:
        check.problems.append("constraint existe mas não está validada (convalidated=false)")
        return check

    check.total_manifests = conn.execute(text("SELECT count(*) FROM raw.shopee_ingestion_file")).scalar()
    check.manifests_with_metadata_not_null = conn.execute(text(
        "SELECT count(*) FROM raw.shopee_ingestion_file WHERE source_metadata IS NOT NULL"
    )).scalar()
    if check.total_manifests != EXPECTED_TOTAL_MANIFESTS_AT_MIGRATION:
        check.problems.append(
            f"total de manifestos mudou desde a migration (esperado "
            f"{EXPECTED_TOTAL_MANIFESTS_AT_MIGRATION}, encontrado {check.total_manifests}) "
            "-- possível ingestão Raw nova nesta janela"
        )
    if check.manifests_with_metadata_not_null:
        check.problems.append(
            f"{check.manifests_with_metadata_not_null} manifesto(s) já têm source_metadata "
            "preenchido -- estado não é mais 0/120, revisar antes de prosseguir"
        )

    check.ok = not check.problems
    return check


@dataclass
class DryRunReport:
    """Só o suficiente para decidir/relatar — NUNCA carrega shop_id,
    filename completo ou conteúdo de célula (ver `_print_dry_run_report`,
    que é a única função que imprime isto)."""
    ready: bool
    exit_code: int
    migration_state: MigrationStateCheck
    total_pending_ads: int = 0
    count_by_brand: dict = field(default_factory=dict)
    periods: list = field(default_factory=list)  # [{"brand", "period_start", "period_end"}]
    problems: list = field(default_factory=list)


def _dry_run_with_conn(conn, data_path: Path) -> DryRunReport:
    """Núcleo testável do dry-run — recebe uma conexão SQLAlchemy já aberta
    (readonly, a critério do chamador real; uma conexão falsa nos testes).
    Nunca abre/fecha conexão, nunca lê o secret — isso é responsabilidade de
    `run_dry_run`."""
    migration_state = _check_migration_state(conn)
    if not migration_state.ok:
        return DryRunReport(
            ready=False, exit_code=3, migration_state=migration_state,
            problems=list(migration_state.problems),
        )

    manifests = _pending_ads_manifests(conn)
    count_by_brand: dict[str, int] = {}
    for m in manifests:
        count_by_brand[m["brand"]] = count_by_brand.get(m["brand"], 0) + 1

    plan = plan_backfill(conn, data_path)
    periods = [
        {
            "brand": item.brand,
            "period_start": item.metadata["period_start"],
            "period_end": item.metadata["period_end"],
        }
        for item in plan.items
    ]

    return DryRunReport(
        ready=plan.ready,
        exit_code=0 if plan.ready else 5,
        migration_state=migration_state,
        total_pending_ads=len(manifests),
        count_by_brand=count_by_brand,
        periods=periods,
        problems=list(plan.problems),
    )


def run_dry_run(
    secret_path: Path = DEFAULT_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
    data_path: Optional[Path] = None,
) -> DryRunReport:
    """Fluxo real: carrega o secret pelo mesmo fluxo seguro de
    `write_conn` (nunca `os.environ`), abre uma sessão explicitamente
    `postgresql_readonly=True` na mesma primary da escrita (a réplica de
    leitura tem lag — ver docstring do módulo) e delega a
    `_dry_run_with_conn`. Nunca levanta para o chamador — qualquer falha
    de secret/conexão vira um `DryRunReport` com `ready=False` e o motivo
    sanitizado em `problems`."""
    try:
        secret = write_conn.load_write_secret(secret_path, repo_root)
        write_url = write_conn.validate_write_guardrails(secret, settings.datamart_url or "")
    except write_conn.SecretLoadError as exc:
        return DryRunReport(
            ready=False, exit_code=2,
            migration_state=MigrationStateCheck(ok=False, problems=[str(exc)]),
            problems=[str(exc)],
        )

    data_path = data_path or Path(settings.shopee_data_path)

    try:
        engine = create_engine(write_url, pool_pre_ping=True)
        with engine.connect().execution_options(postgresql_readonly=True) as conn:
            return _dry_run_with_conn(conn, data_path)
    except Exception as exc:  # noqa: BLE001
        sanitized = write_conn.sanitize_error_message(exc)
        return DryRunReport(
            ready=False, exit_code=4,
            migration_state=MigrationStateCheck(ok=False, problems=[sanitized]),
            problems=[f"erro de conexão/consulta: {sanitized}"],
        )


def _print_dry_run_report(report: DryRunReport) -> None:
    """Única função que imprime o resultado do dry-run — nunca imprime
    URL/credencial/shop_id/filename completo/conteúdo de célula. `periods`
    só carrega marca + datas (ver `DryRunReport`)."""
    ms = report.migration_state
    print("=== Dry-run backfill source_metadata (Fase Staging Shopee 2A, Gate 2B) ===")
    print(
        f"Migration: coluna existe={ms.column_exists} tipo={ms.column_type} "
        f"constraint validada={ms.constraint_validated} pg_is_in_recovery={ms.in_recovery}"
    )
    if ms.total_manifests is not None:
        print(f"Total de manifestos: {ms.total_manifests} (esperado {EXPECTED_TOTAL_MANIFESTS_AT_MIGRATION})")
        print(f"Manifestos com source_metadata preenchido: {ms.manifests_with_metadata_not_null}")
    print(f"Manifestos ads pendentes localizados: {report.total_pending_ads} (esperado {EXPECTED_TOTAL_PENDING_ADS_MANIFESTS})")
    print(f"Contagem por marca: {dict(sorted(report.count_by_brand.items()))}")
    print("Períodos identificados (só marca + datas -- sem shop_id/filename):")
    for p in sorted(report.periods, key=lambda x: x["brand"]):
        print(f"  - {p['brand']}: {p['period_start']} a {p['period_end']}")
    print(f"ready={report.ready}")
    if report.problems:
        print("Problemas:")
        for p in report.problems:
            print(f"  - {p}")


_RE_CPF_LIKE = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_RE_FILE_PATH_LIKE = re.compile(r"[\w./\\+-]+\.(?:csv|xlsx|xls|json|txt)", re.IGNORECASE)
_RE_LONG_DIGIT_RUN = re.compile(r"\d{6,}")  # shop_id/CPF residual, telefone, etc.


def _sanitize_write_path_exception(exc: BaseException) -> str:
    """Mensagem SEGURA para qualquer falha no caminho de escrita de
    `run_apply_confirmed` -- nunca o texto livre de `str(exc)`. Uma
    exceção de baixo nível não prevista (driver, rede, um bug futuro) pode
    conter QUALQUER coisa: DSN com senha, nome de arquivo, `shop_id`, CPF.
    `write_conn.sanitize_error_message` só cobre o padrão `usuario:senha@`
    de uma URL — não é (e não tenta ser) um scrubber genérico de PII, então
    NUNCA é suficiente sozinho aqui. Por isso o valor exibido ao usuário é
    sempre `tipo da exceção + mensagem passada por 3 filtros em série`
    (credencial de DSN, CPF, caminho de arquivo, sequência longa de
    dígitos) — mas o TIPO é a garantia real; os filtros são defesa em
    profundidade, nunca a única barreira."""
    msg = write_conn.sanitize_error_message(exc)
    msg = _RE_CPF_LIKE.sub("<redacted>", msg)
    msg = _RE_FILE_PATH_LIKE.sub("<redacted>", msg)
    msg = _RE_LONG_DIGIT_RUN.sub("<redacted>", msg)
    return f"{type(exc).__name__}: {msg}"


def run_apply_confirmed(
    audit_path: Path,
    secret_path: Path = DEFAULT_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
    data_path: Optional[Path] = None,
) -> int:
    """ÚNICA função que pode chegar a chamar `apply_backfill_atomic` contra
    um banco real. Cada guarda abaixo retorna ANTES de abrir uma conexão de
    escrita (`write_conn.open_write_connection`) se falhar — nenhuma delas
    pode ser pulada:

    1. Secret com as 2 chaves esperadas, incluindo
       `I_UNDERSTAND_THIS_WRITES_DATAMART_RAW=1` (`write_conn.load_write_secret`).
    2. URL de escrita diferente da de leitura (`validate_write_guardrails`).
    3. Preflight de escrita limpo (`write_conn.run_preflight`) — inclui
       `rolsuper=false`, mesmo cluster físico da leitura, permissões no
       schema `raw`.
    4. Estado da migration confirmado NA HORA (`_check_migration_state`) —
       inclui `pg_is_in_recovery()=false` (primary confirmada).
    5. Plano 10/10 RECALCULADO nesta chamada (nunca reaproveita um plano de
       uma chamada anterior a `--dry-run`).

    Só depois de 1-5 passarem é que uma conexão de escrita é aberta, e a
    única operação de escrita real é a chamada a `apply_backfill_atomic`.

    TODA exceção nos 9 pontos de risco do caminho de escrita (preflight,
    conexão read-only, checagem de migration, cálculo do plano, abertura
    da conexão de escrita, aquisição/liberação do advisory lock,
    `apply_backfill_atomic`, fechamento da conexão) é capturada aqui e
    convertida em `_sanitize_write_path_exception` — nunca um traceback
    cru chega a `stdout`/`stderr`.

    Caso especial (item crítico): se `apply_backfill_atomic` já retornou
    `committed` e SÓ a liberação do advisory lock ou o fechamento da
    conexão falharem depois disso, o resultado ainda é reportado como
    `committed` (o COMMIT já aconteceu, não pode virar falha por causa de
    limpeza pós-transação) — a falha vira um AVISO sanitizado, e o `finally`
    fecha a conexão de qualquer forma, o que libera um advisory lock de
    SESSÃO mesmo que `release_advisory_lock` tenha lançado. Nunca sugere
    retry automático (um retry reaplicaria um backfill já commitado)."""
    try:
        secret = write_conn.load_write_secret(secret_path, repo_root)
        write_url = write_conn.validate_write_guardrails(secret, settings.datamart_url or "")
    except write_conn.SecretLoadError as exc:
        print(f"--apply-confirmed bloqueado: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"--apply-confirmed bloqueado ao carregar o secret -- {_sanitize_write_path_exception(exc)}", file=sys.stderr)
        return 2

    try:
        preflight = write_conn.run_preflight(write_url, settings.datamart_url or "", expect_tables_exist=True)
    except Exception as exc:  # noqa: BLE001
        print(f"--apply-confirmed bloqueado -- preflight falhou inesperadamente: {_sanitize_write_path_exception(exc)}", file=sys.stderr)
        return 3
    print("\n=== Preflight de escrita (--apply-confirmed) — nunca exibe host/IP/usuário/senha ===")
    for key, value in preflight.safe_summary.items():
        print(f"  {key}: {value}")
    for warning in preflight.warnings:
        print(f"  AVISO (não bloqueante): {warning}")
    if not preflight.ok:
        print("--apply-confirmed NÃO executado -- preflight bloqueado:", file=sys.stderr)
        for reason in preflight.blocking_reasons:
            print(f"  - {reason}", file=sys.stderr)
        return 3

    data_path = data_path or Path(settings.shopee_data_path)

    try:
        engine = create_engine(write_url, pool_pre_ping=True)
        with engine.connect().execution_options(postgresql_readonly=True) as ro_conn:
            migration_state = _check_migration_state(ro_conn)
            if not migration_state.ok:
                print("--apply-confirmed NÃO executado -- estado da migration não confirmado:", file=sys.stderr)
                for p in migration_state.problems:
                    print(f"  - {p}", file=sys.stderr)
                return 4
            plan = plan_backfill(ro_conn, data_path)
    except Exception as exc:  # noqa: BLE001
        print(
            f"--apply-confirmed bloqueado -- falha ao confirmar migration/plano na sessão de leitura: "
            f"{_sanitize_write_path_exception(exc)}",
            file=sys.stderr,
        )
        return 4

    if not plan.ready:
        print("--apply-confirmed NÃO executado -- plano recalculado agora não está 10/10 pronto:", file=sys.stderr)
        for p in plan.problems:
            print(f"  - {p}", file=sys.stderr)
        return 5

    try:
        conn = write_conn.open_write_connection(write_url)
    except Exception as exc:  # noqa: BLE001
        print(f"--apply-confirmed bloqueado -- falha ao abrir conexão de escrita: {_sanitize_write_path_exception(exc)}", file=sys.stderr)
        return 6

    warnings_post_result: list[str] = []
    result: Optional[BackfillResult] = None
    try:
        try:
            lock_acquired = write_conn.try_acquire_advisory_lock(conn)
        except Exception as exc:  # noqa: BLE001
            print(f"--apply-confirmed abortado -- falha ao adquirir advisory lock: {_sanitize_write_path_exception(exc)}", file=sys.stderr)
            return 6
        if not lock_acquired:
            print("--apply-confirmed abortado: advisory lock em uso -- sem retry automático.", file=sys.stderr)
            return 6

        try:
            result = apply_backfill_atomic(
                conn,
                plan,
                confirm_flag=True,
                confirm_secret_value=secret["I_UNDERSTAND_THIS_WRITES_DATAMART_RAW"],
                audit_path=audit_path,
                repo_root=repo_root,
            )
        except Exception as exc:  # noqa: BLE001
            # apply_backfill_atomic já tem seu próprio catch-all interno e
            # nunca deveria propagar -- esta é defesa em profundidade contra
            # um bug futuro que quebre essa garantia.
            print(f"--apply-confirmed -- falha inesperada durante o backfill: {_sanitize_write_path_exception(exc)}", file=sys.stderr)
            return 9

        try:
            write_conn.release_advisory_lock(conn)
        except Exception as exc:  # noqa: BLE001
            # O COMMIT (se `result.outcome == "committed"`) já aconteceu --
            # uma falha ao liberar o advisory lock de SESSÃO não desfaz o
            # backfill e NUNCA deve ser reportada como falha dele. Fechar a
            # conexão no `finally` externo libera o lock de qualquer jeito
            # (lock de sessão morre com a conexão). Nunca sugere retry.
            warnings_post_result.append(
                f"release_advisory_lock falhou após o resultado acima (lock de sessão será liberado ao "
                f"fechar a conexão, sem retry automático): {_sanitize_write_path_exception(exc)}"
            )
    finally:
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001
            warnings_post_result.append(f"falha ao fechar a conexão de escrita: {_sanitize_write_path_exception(exc)}")

    print(f"\nResultado: {result.outcome}")
    print(f"file_ids atualizados: {sorted(result.updated_file_ids)}")
    if result.backup_sha256:
        print(f"backup_sha256: {result.backup_sha256}")
    if result.problems:
        print("Problemas:")
        for p in result.problems:
            print(f"  - {p}")
    for w in warnings_post_result:
        print(f"AVISO (não bloqueante, não afeta o resultado do backfill acima): {w}", file=sys.stderr)
    return 0 if result.outcome == "committed" else 9


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply-confirmed", action="store_true")
    parser.add_argument("--audit-path", default=None, help="Obrigatório com --apply-confirmed.")
    parser.add_argument("--data-path", default=None, help="Sobrepõe SHOPEE_DATA_PATH.")
    args = parser.parse_args(argv)

    try:
        data_path = Path(args.data_path) if args.data_path else None

        if args.apply_confirmed:
            if not args.audit_path:
                print("--apply-confirmed exige --audit-path.", file=sys.stderr)
                return 1
            return run_apply_confirmed(Path(args.audit_path), data_path=data_path)

        report = run_dry_run(data_path=data_path)
        _print_dry_run_report(report)
        return report.exit_code
    except Exception as exc:  # noqa: BLE001
        # Última barreira: mesmo que um bug escape de todo o resto (dry-run
        # ou apply-confirmed), NUNCA deixa um traceback cru -- que poderia
        # conter DSN/senha/filename/shop_id/PII de uma exceção de baixo
        # nível não prevista -- chegar ao stdout/stderr do usuário. Só tipo
        # + mensagem já filtrada por `_sanitize_write_path_exception`.
        print(f"Falha inesperada -- {_sanitize_write_path_exception(exc)}", file=sys.stderr)
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
