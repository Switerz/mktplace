"""
Checagens de validação — FONTE ÚNICA compartilhada entre o preview read-only
(`preview.py`) e a transformação transacional gerada por `build_sql.py`.

## Arquitetura de scans (revisão de 2026-07-06, item de performance)

A primeira versão desta staging gerava um `SELECT count(*)` INDEPENDENTE por
condição (~55-60 por fonte, ~130 no total) — cada um um scan completo de
`raw.shopee_*_export`. Substituído por:

- `build_merged_row_query(spec)` — UMA query agregada por fonte, usando
  `count(*) FILTER (WHERE <condição>)` para TODAS as condições de linha
  (obrigatoriedade, formato/domínio, não-negatividade, padrão de `order_id`,
  período de ads, órfã de manifesto, `brand`/`source_type` incompatível).
  Isso é 1 scan cobrindo dezenas de condições, não dezenas de scans. A
  detecção de órfã/incompatibilidade passou a usar `LEFT JOIN` (em vez do
  `INNER JOIN` anterior) exatamente para poder entrar nessa mesma query —
  uma linha órfã (`f.file_id IS NULL`) simplesmente não tem como estar
  duplamente errada em nenhuma outra condição que dependa de `f`.
- `build_scan_checks(spec)` — as poucas checagens que têm formato
  estrutural diferente (agregação por chave, não por linha) e por isso não
  cabem no `FILTER` acima. Cada uma é UM scan, e cada uma tira proveito de
  algo que já existe:
  - duplicidade de `(file_id, source_row_number)`: já é a UNIQUE constraint
    da própria Raw (`uk_..._file_row`) — o `GROUP BY` pode usar esse índice
    (index scan, não sequential scan). Mantido como checagem defensiva
    (nunca deveria disparar, já que a Raw impede fisicamente a duplicidade).
  - chave do JSONB fora do contrato: reconstruída a partir de
    `raw.shopee_ingestion_file.headers_json` (uma linha por ARQUIVO — 85
    arquivos para orders) em vez de `raw_payload` por LINHA (383k linhas) —
    validado por sondagem read-only em 2026-07-06 que as duas abordagens
    produzem exatamente o mesmo conjunto de chaves (headers_json guarda a
    lista bruta de headers; a desambiguação `__col<posição>` do loader Raw
    é reconstruída aqui com `WITH ORDINALITY` + `row_number()` sobre o
    array, reproduzindo `_row_to_payload` em `inventory.py`).
- Só a query agregada de linha é restringida a "linhas elegíveis" (anti-join
  por `raw_id`) quando chamada pela TRANSFORMAÇÃO — o preview sempre passa
  `incremental=False` porque seu objetivo é reconciliar 100% da Raw a cada
  execução. As checagens estruturais (duplicidade, schema drift) não são
  escopadas a "elegíveis": duplicidade compara pares que podem envolver
  linhas antigas e novas, e schema drift opera sobre o manifesto (por
  arquivo), não sobre linhas.

Cada `RowCondition`/`ScanCheck` carrega só `reason` (texto sanitizado: nunca
contém valor de payload/PII) — usado tanto pelo preview (informativo) quanto
pela transformação (`RAISE EXCEPTION` com motivo + contagem, nunca payload).
"""
from __future__ import annotations

from dataclasses import dataclass

from pipelines.staging.shopee import mapping, semantics, sql_rules
from pipelines.staging.shopee import rules_registry as rr


@dataclass(frozen=True)
class RowCondition:
    reason: str
    condition_sql: str  # booleano sobre r (LEFT JOIN raw.shopee_ingestion_file f) — nunca lança erro


@dataclass(frozen=True)
class ScanCheck:
    reason: str
    body_sql: str        # "FROM ... WHERE ..." — próprio scan, formato não cabe no FILTER
    justification: str   # por que precisa de scan próprio (documentado, testado)


@dataclass(frozen=True)
class MergedRowQuery:
    reasons: list[str]           # na mesma ordem de select_exprs — c0, c1, ...
    select_exprs: list[str]      # "count(*) FILTER (WHERE <cond>) AS c{i}" — diagnóstico por motivo
    from_clause: str             # "FROM raw.X r LEFT JOIN raw.shopee_ingestion_file f ON ... [WHERE ...]"
    rejected_any_expr: str       # "count(*) FILTER (WHERE (c0) OR (c1) OR ...) AS rejected_any"


def _blank_all(source_keys: tuple[str, ...]) -> str:
    conds = [f"NULLIF(btrim({sql_rules.payload(k)}), '') IS NULL" for k in source_keys]
    return "(" + " AND ".join(conds) + ")"


def _present_any_negative(source_keys: tuple[str, ...]) -> str:
    """'-' sozinho é o placeholder documentado de ausência em várias colunas
    de ads (ver mapping.py) — não é um número negativo. Exigir um dígito
    logo após o sinal evita o falso positivo de contar o placeholder como
    valor negativo."""
    conds = [f"btrim({sql_rules.payload(k)}) ~ '^-[0-9]'" for k in source_keys]
    return "(" + " OR ".join(conds) + ")"


def _column_format_invalid(col: mapping.StagingColumn) -> str | None:
    """Condição "valor presente e inválido" para uma coluna (OR'd entre suas
    `source_keys`, no caso de coalesce). `None` quando a regra não tem
    validação de formato própria (texto livre) ou a coluna é derivada
    (provenance/nome de arquivo — tratada à parte)."""
    if col.rule.startswith("prov:") or not col.source_keys:
        return None
    if col.rule.startswith("coalesce:"):
        inner_rule, params = rr.resolve(col.rule.removeprefix("coalesce:"))
        if inner_rule.is_invalid is None:
            return None
        conds = [inner_rule.is_invalid(sql_rules.payload(k), *params) for k in col.source_keys]
        return "(" + " OR ".join(conds) + ")"
    rule, params = rr.resolve(col.rule)
    if rule.is_invalid is None:
        return None
    (key,) = col.source_keys
    return rule.is_invalid(sql_rules.payload(key), *params)


def _column_row_conditions(spec: mapping.TableSpec) -> list[RowCondition]:
    """Uma condição por coluna: obrigatoriedade, formato/domínio e
    não-negatividade — a mesma lógica de antes, agora devolvendo só a
    condição booleana (não mais um "FROM...WHERE" próprio por condição)."""
    conditions: list[RowCondition] = []
    prefix = f"{spec.source_type}: "
    for col in spec.columns:
        if col.rule.startswith("prov:"):
            continue
        if not col.nullable and col.source_keys:
            conditions.append(RowCondition(
                reason=f"{prefix}{col.column}: campo obrigatório vazio",
                condition_sql=_blank_all(col.source_keys),
            ))
        cond = _column_format_invalid(col)
        if cond is not None:
            conditions.append(RowCondition(
                reason=f"{prefix}{col.column}: valor fora do formato/domínio esperado",
                condition_sql=cond,
            ))
        if col.non_negative and col.source_keys:
            conditions.append(RowCondition(
                reason=f"{prefix}{col.column}: valor negativo",
                condition_sql=_present_any_negative(col.source_keys),
            ))
    return conditions


def _extra_row_conditions(spec: mapping.TableSpec) -> list[RowCondition]:
    """Checagens de negócio que dependem de mais de uma chave ou de um
    campo do manifesto — não se encaixam no modelo genérico por coluna."""
    prefix = f"{spec.source_type}: "
    if spec.source_type == "orders":
        return [RowCondition(
            reason=f"{prefix}order_id fora do padrão de 14 alfanuméricos maiúsculos",
            condition_sql=(
                "(NULLIF(btrim(r.raw_payload ->> 'ID do pedido'), '') IS NOT NULL "
                "AND btrim(r.raw_payload ->> 'ID do pedido') !~ '^[0-9A-Z]{14}$')"
            ),
        )]
    if spec.source_type == "ads":
        return [RowCondition(
            reason=f"{prefix}período do relatório (nome do arquivo) com calendário ou ordem inválidos",
            condition_sql=semantics.filename_period_is_invalid("f.source_filename"),
        )]
    return []


def _manifest_join_conditions(spec: mapping.TableSpec) -> list[RowCondition]:
    """Órfã / `source_type` incompatível / `brand` incompatível — expressas
    sobre um LEFT JOIN (não INNER) para caber na mesma query agregada das
    demais condições de linha. `f.file_id IS NULL` identifica a órfã; as
    outras duas só fazem sentido quando `f` existe."""
    prefix = f"{spec.source_type}: "
    return [
        RowCondition(
            reason=f"{prefix}linha órfã sem manifesto correspondente",
            condition_sql="(f.file_id IS NULL)",
        ),
        RowCondition(
            reason=f"{prefix}source_type do manifesto incompatível com a tabela-filha",
            condition_sql=f"(f.file_id IS NOT NULL AND f.source_type <> '{spec.source_type}')",
        ),
        RowCondition(
            reason=f"{prefix}brand diferente entre linha Raw e manifesto",
            condition_sql="(f.file_id IS NOT NULL AND r.brand <> f.brand)",
        ),
    ]


def build_row_conditions(spec: mapping.TableSpec) -> list[RowCondition]:
    """TODAS as condições de linha desta fonte — a lista que vira UMA única
    query agregada via `build_merged_row_query`."""
    return (
        _column_row_conditions(spec)
        + _extra_row_conditions(spec)
        + _manifest_join_conditions(spec)
    )


def build_merged_row_query(spec: mapping.TableSpec, *, incremental: bool) -> MergedRowQuery:
    """UMA query cobrindo todas as condições de linha via `count(*) FILTER`.

    `incremental=True` restringe a linhas ainda não presentes na staging
    (anti-join por `raw_id`) — uso exclusivo da transformação real, para não
    revalidar formato de linhas já carregadas em execuções anteriores. Na
    primeira carga (staging vazia), elegível = toda a Raw (o anti-join não
    exclui nada). O preview sempre usa `incremental=False`: seu objetivo é
    reconciliar 100% da Raw a cada execução, não só o delta.

    `rejected_any_expr` conta LINHAS DISTINTAS que violam QUALQUER condição
    — não a soma das contagens por motivo, que superconta uma linha que
    viola mais de uma condição simultaneamente (ex.: `order_id` vazio E
    `quantity` negativo na mesma linha soma 2 nos motivos individuais, mas é
    1 linha rejeitada). As contagens por motivo (`select_exprs`) continuam
    existindo só para DIAGNÓSTICO (qual regra específica falhou, e quantas
    vezes) — nunca para somar e chamar de "total de linhas rejeitadas"."""
    conditions = build_row_conditions(spec)
    select_exprs = [
        f"count(*) FILTER (WHERE {c.condition_sql}) AS c{i}" for i, c in enumerate(conditions)
    ]
    combined = " OR ".join(f"({c.condition_sql})" for c in conditions)
    rejected_any_expr = f"count(*) FILTER (WHERE {combined}) AS rejected_any"
    from_clause = (
        f"FROM {spec.raw_table} r "
        f"LEFT JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id"
    )
    if incremental:
        from_clause += (
            f"\nWHERE NOT EXISTS (SELECT 1 FROM {spec.staging_table} s WHERE s.raw_id = r.id)"
        )
    return MergedRowQuery(
        reasons=[c.reason for c in conditions],
        select_exprs=select_exprs,
        from_clause=from_clause,
        rejected_any_expr=rejected_any_expr,
    )


def build_scan_checks(spec: mapping.TableSpec) -> list[ScanCheck]:
    """As poucas checagens que precisam de scan/agregação própria — cada
    uma justificada e apoiada em algo que já existe (índice ou tabela
    pequena), nunca um scan por coluna/regra."""
    covered = sorted(mapping.covered_keys(spec))
    covered_array = "ARRAY[" + ", ".join("'" + k.replace("'", "''") + "'" for k in covered) + "]::text[]"
    return [
        ScanCheck(
            reason=f"{spec.source_type}: duplicidade de (file_id, source_row_number)",
            body_sql=(
                f"FROM (SELECT file_id, source_row_number FROM {spec.raw_table} "
                f"GROUP BY 1, 2 HAVING count(*) > 1) d"
            ),
            justification=(
                "GROUP BY sobre exatamente a UNIQUE constraint já existente na Raw "
                f"(uk_..._file_row de {spec.raw_table}) — o planner pode satisfazer via "
                "index scan dessa constraint, não um sequential scan. Checagem puramente "
                "defensiva: a Raw já impede fisicamente a duplicidade; isto nunca deveria "
                "disparar > 0."
            ),
        ),
        ScanCheck(
            reason=f"{spec.source_type}: chave do JSONB fora do contrato (schema drift não mapeado)",
            body_sql=(
                "FROM (\n"
                "    SELECT DISTINCT CASE WHEN row_number() OVER "
                "(PARTITION BY f.file_id, h.value ORDER BY h.ordinality) = 1 "
                "THEN h.value ELSE h.value || '__col' || (h.ordinality - 1)::text END AS key\n"
                "    FROM raw.shopee_ingestion_file f,\n"
                "         LATERAL jsonb_array_elements_text(f.headers_json) WITH ORDINALITY AS h(value, ordinality)\n"
                f"    WHERE f.source_type = '{spec.source_type}'\n"
                ") k\n"
                f"WHERE k.key <> ALL({covered_array})"
            ),
            justification=(
                "Reconstrói o mesmo conjunto de chaves de raw_payload a partir de "
                "raw.shopee_ingestion_file.headers_json (1 linha por ARQUIVO, não por "
                "linha de dado) — validado por sondagem read-only em 2026-07-06 que produz "
                "exatamente o mesmo conjunto (71/17/36 chaves) que iterar jsonb_object_keys "
                "sobre raw_payload de cada linha, a uma fração do custo (85 vs 383.298 "
                "linhas escaneadas para orders). A desambiguação '__col<posição>' do loader "
                "(inventory.py::_row_to_payload) é reproduzida via WITH ORDINALITY + "
                "row_number() sobre o array de headers."
            ),
        ),
    ]


def post_insert_check(spec: mapping.TableSpec) -> ScanCheck:
    """Toda linha Raw elegível deve existir na staging com os MESMOS
    `file_id`, `brand`, `source_row_number` e `row_sha256` — não só o hash
    (revisão de 2026-07-06: comparar só `row_sha256` deixaria passar uma
    linha cujo `file_id`/`brand`/`source_row_number` tenha sido alterado
    manualmente na staging, já que esses campos não entram no hash de
    conteúdo do payload). Usada só pela transformação (depois que a tabela
    staging existe e foi escrita nesta mesma transação)."""
    return ScanCheck(
        reason=(
            f"{spec.source_type}: linha elegível sem staging correspondente "
            "(raw_id/file_id/brand/source_row_number/row_sha256) após o INSERT"
        ),
        body_sql=(
            f"FROM {spec.raw_table} r LEFT JOIN {spec.staging_table} s ON s.raw_id = r.id "
            "WHERE s.raw_id IS NULL "
            "OR s.file_id <> r.file_id "
            "OR s.brand <> r.brand "
            "OR s.source_row_number <> r.source_row_number "
            "OR s.row_sha256 <> r.row_sha256"
        ),
        justification=(
            "1 scan por tabela staging, comparando contra a Raw via LEFT JOIN — já é o "
            "mínimo possível para garantir que toda linha elegível foi carregada "
            "corretamente; não há como reduzir sem deixar de comparar alguma linha."
        ),
    )
