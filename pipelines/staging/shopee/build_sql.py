"""
Gerador do DDL e da transformação Raw → staging da Shopee (DRAFT).

Os arquivos em db/sql/staging/ são artefatos gerados a partir de mapping.py
e validations.py:

    python -m pipelines.staging.shopee.build_sql --write   # regenera
    python -m pipelines.staging.shopee.build_sql --check   # confere sincronismo

NADA aqui executa SQL em banco — este módulo só monta e escreve texto. A
execução real do DDL/transformação fica para uma fase futura, com aprovação
explícita e credencial de escrita dedicada (nunca DATAMART_DATABASE_URL).

## Arquitetura transacional da transformação

1. `SET LOCAL lock_timeout` / `statement_timeout` (nunca vazam da transação).
2. `pg_advisory_xact_lock` com uma chave FIXA desta staging — impede duas
   execuções concorrentes do mesmo script.
3. `LOCK TABLE ... IN SHARE MODE` — as 4 tabelas Raw/manifesto E as 3
   tabelas staging, numa ÚNICA instrução (evita deadlock entre execuções
   concorrentes: uma instrução `LOCK TABLE a, b, c` adquire todos os locks
   atomicamente, sem janela onde uma sessão detém só parte deles; a lista é
   sempre ordenada alfabeticamente, então mesmo que o código mude no
   futuro, todas as execuções pedem os locks na MESMA ordem). SHARE
   permite SELECT concorrente (inclusive um preview rodando ao mesmo
   tempo) mas bloqueia INSERT/UPDATE/DELETE de OUTRAS sessões nas 7
   tabelas até o fim da transação — a Raw não muda sob os pés da validação,
   e nenhuma escrita externa pode ocorrer entre o pós-check e o COMMIT.
   Autolock nunca bloqueia a própria transação: o INSERT feito por ESTA
   mesma sessão no passo 5, nas mesmas tabelas staging já bloqueadas em
   SHARE por ela mesma, prossegue normalmente.
4. Validações fail-fast (PRÉ-VALIDAÇÃO) — revisão de performance: UMA
   query agregada por fonte (`validations.build_merged_row_query`,
   `count(*) FILTER` por condição) cobre todas as condições de linha, mais
   só as poucas checagens estruturais que precisam de scan próprio
   (`validations.build_scan_checks` — duplicidade, apoiada no índice único
   já existente na Raw; schema drift, via `headers_json` do manifesto em
   vez de `raw_payload` linha a linha). Total: ~3 scans de PRÉ-VALIDAÇÃO
   por fonte (1 agregado + 2 estruturais) = 9 no total das 3 fontes, não
   mais um scan por condição (~55-60 antes) — ver "Contabilização de
   scans" abaixo para os passos 5/6, que somam scans ADICIONAIS a este
   número. Qualquer contagem > 0 aborta com `RAISE EXCEPTION` ANTES de
   qualquer INSERT — mensagem só com motivo (texto estático) e contagem,
   nunca payload. A query agregada também calcula, na MESMA passada,
   quantas linhas DISTINTAS violam qualquer condição (`rejected_any_expr`
   em `validations.py`) — usado pelo preview para não supercontar uma
   linha que viola vários motivos ao mesmo tempo; a transformação em si
   não precisa desse número (aborta na primeira condição com contagem > 0).
   A query agregada é restrita a linhas AINDA NÃO presentes na staging
   (anti-join por `raw_id`) — não revalida formato de linhas já carregadas
   em execuções anteriores; na primeira carga, elegível = 100% da Raw (a
   staging está vazia, o anti-join não exclui nada).
5. INSERTs das 3 tabelas — 1 leitura da Raw + 1 INSERT na staging por
   fonte (3 pares leitura/INSERT no total), com `ON CONFLICT (raw_id) DO
   NOTHING` (defesa redundante ao anti-join para corrida entre avaliação e
   INSERT). Conflitos na UNIQUE `(file_id, source_row_number)` por um
   `raw_id` DIFERENTE não são cobertos por este `ON CONFLICT` e continuam
   abortando a transação com erro de unicidade, como deve ser.
6. Validações pós-insert (`validations.post_insert_check`) — 1 scan por
   fonte (3 no total) comparando Raw × staging: toda linha Raw elegível
   deve existir na staging com os MESMOS `file_id`, `brand`,
   `source_row_number` e `row_sha256` (não só o hash — uma linha com
   `brand`/`file_id` alterado manualmente não passaria só por preservar o
   hash do conteúdo do payload).
7. `COMMIT` só é alcançado se todos os passos anteriores passarem.

## Contabilização de scans (não confundir as categorias)

| Etapa | Scans | O que conta |
|---|---|---|
| Passo 4 — pré-validação | **9** (3 por fonte × 3 fontes) | 1 query agregada de linha + 2 checagens estruturais, por fonte |
| Passo 5 — INSERTs | 3 (1 por fonte) | a leitura da Raw dentro do próprio INSERT ... SELECT |
| Passo 6 — pós-insert | 3 (1 por fonte) | comparação Raw × staging já carregada |
| **Total da transformação real (passos 4+5+6)** | **15** | — |

`preview.py` roda SÓ o equivalente ao passo 4 (9 scans de pré-validação)
mais consultas adicionais que não fazem parte da transformação real
(contagem do manifesto, SELECT tipado completo, contagem por brand/mês) —
por isso nunca dizer "9 scans por execução completa": 9 é só a
pré-validação: ver docs/staging_shopee_contract.md §13.1/§13.2 para o
detalhamento completo.

## FKs físicas (ver docs/staging_shopee_contract.md §13.3)

Cada tabela staging ganha `FOREIGN KEY (raw_id) REFERENCES <raw_table>(id)`
e `FOREIGN KEY (file_id) REFERENCES raw.shopee_ingestion_file(file_id)`.
Decisão tomada com evidência (sondagem read-only, 2026-07-06): `raw.shopee_*`
JÁ usa FK física internamente (`file_id REFERENCES
raw.shopee_ingestion_file`), então FK física para lineage é o padrão
correto quando a Raw é append-only e a staging depende diretamente dela
— mesmo que nenhuma das 51 tabelas `silver.stg_*` hoje tenha FK física
(0/51, confirmado por consulta a `pg_constraint`), porque aquelas são
mantidas por uma ferramenta/owner diferente (`dbt`), não pelo mesmo
pipeline que escreve a Raw.

**Privilégio REFERENCES — dois cenários distintos**: `raw.shopee_ingestion_file`
é de propriedade da role `postgres` (confirmado via `pg_tables`), a MESMA
role usada hoje por `.env.shopee-write.local` (`DATAMART_SHOPEE_WRITE_URL`)
para escrever a Raw — um owner tem implicitamente todos os privilégios
sobre seus próprios objetos, `REFERENCES` incluso. Ou seja: **uma aplicação
manual deste DDL usando essa MESMA credencial de escrita não precisa de
nenhum GRANT adicional** — o FK físico funcionaria de imediato. Só uma
FUTURA role de automação DEDICADA à staging (diferente de `postgres`, um
credential próprio ainda não criado, seguindo o mesmo padrão de segregação
já adotado para a Raw) precisaria de um `GRANT REFERENCES ON
raw.shopee_ingestion_file, raw.shopee_order_item_export,
raw.shopee_shop_stats_export, raw.shopee_ads_export TO <nova_role>` do
owner da Raw antes de aplicar este DDL. Nenhuma role nova foi criada e
nenhum GRANT foi executado nesta fase.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pipelines.staging.shopee import mapping, sql_rules, validations
from pipelines.staging.shopee import rules_registry as rr

REPO_ROOT = Path(__file__).resolve().parents[3]
DDL_PATH = REPO_ROOT / "db" / "sql" / "staging" / "shopee_staging_ddl.sql"
TRANSFORM_PATH = REPO_ROOT / "db" / "sql" / "staging" / "shopee_staging_transform.sql"

# Namespace fixo e arbitrário para o advisory lock desta fase — escolhido
# uma única vez em 2026-07-06, nunca reutilizado por outro script.
ADVISORY_LOCK_NAMESPACE = 84772001

_DRAFT_HEADER = """\
-- ============================================================================
-- DRAFT — NÃO EXECUTADO EM NENHUM BANCO (Fase Staging Shopee 2A — draft
-- não aplicado; contrato original da Fase Staging Shopee 1, revisado nas
-- rodadas de Gate 2B — source_metadata de ads e buyer_cpf).
-- Gerado por: python -m pipelines.staging.shopee.build_sql --write
-- Fonte da verdade do contrato: pipelines/staging/shopee/mapping.py
-- Fonte da verdade das validações: pipelines/staging/shopee/validations.py
-- NÃO EDITAR À MÃO — regenerar pelos comandos acima.
--
-- Alvo futuro: schema `silver` do Data Mart (convenção confirmada por
-- inspeção read-only em 2026-07-04: staging tipada de marketplaces usa
-- silver.stg_* — ex.: silver.stg_ml_orders, silver.stg_tiktok_orders).
-- Execução exigirá credencial de escrita dedicada e aprovação explícita.
-- FKs físicas de raw_id/file_id: funcionam de imediato se aplicadas com a
-- MESMA credencial de escrita da Raw (.env.shopee-write.local, role
-- "postgres", dona das tabelas raw.shopee_*, já tem REFERENCES sobre elas).
-- Só uma FUTURA role de automação dedicada e diferente precisaria de um
-- GRANT REFERENCES prévio do owner da Raw — ver docstring de build_sql.py.
-- ============================================================================
"""


def column_expression(col: mapping.StagingColumn) -> str:
    """Expressão SELECT (sem alias) para uma coluna da staging."""
    if col.rule.startswith("prov:"):
        return col.rule.removeprefix("prov:")
    if col.rule.startswith("coalesce:"):
        inner_rule, params = rr.resolve(col.rule.removeprefix("coalesce:"))
        parts = [inner_rule.value(sql_rules.payload(k), *params) for k in col.source_keys]
        return "COALESCE(" + ", ".join(parts) + ")"
    rule, params = rr.resolve(col.rule)
    if not col.source_keys:
        # Regra "zero-arg", baseada em campo do manifesto f.* (ex.: período
        # de ads, vindo de f.source_metadata — nunca de f.source_filename).
        return rule.value(*params)
    (key,) = col.source_keys
    return rule.value(sql_rules.payload(key), *params)


def _column_check_sql(col: mapping.StagingColumn) -> str:
    """CHECK inline da coluna: não-negatividade (quando marcado) + rejeição
    explícita de NaN em toda coluna `numeric(p,s)`.

    Achado por sondagem read-only em 2026-07-06: `numeric(p,s)` com
    precisão/escala explícitas JÁ rejeita ±Infinity nativamente
    ("numeric field overflow" — nenhuma das colunas deste contrato usa
    numeric sem precisão/escala, então Infinity está coberto pelo próprio
    tipo). NaN, porém, passa livremente por `numeric(p,s)` E por
    `CHECK (col >= 0)` — confirmado que `'NaN'::numeric >= 0` avalia `TRUE`
    (Postgres trata NaN como "maior que qualquer valor não-NaN" na
    ordenação). Por isso todo `numeric` ganha `col <> 'NaN'` explícito.
    Nenhum `IS NULL OR` é necessário: um CHECK que avalia NULL (não FALSE)
    já é satisfeito pela semântica de três valores do Postgres."""
    conditions = []
    if col.sql_type.startswith("numeric"):
        conditions.append(f"{col.column} <> 'NaN'")
    if col.non_negative:
        conditions.append(f"{col.column} >= 0")
    if not conditions:
        return ""
    return f" CHECK ({' AND '.join(conditions)})"


def build_ddl(spec: mapping.TableSpec) -> str:
    lines = [f"CREATE TABLE {spec.staging_table} ("]
    body = []
    for col in spec.columns:
        null_sql = "" if col.nullable else " NOT NULL"
        pk = " PRIMARY KEY" if col.column == "raw_id" else ""
        check_sql = _column_check_sql(col)
        body.append(f"    {col.column:<32}{col.sql_type}{null_sql}{pk}{check_sql}")
    body.append("    staging_built_at                timestamptz NOT NULL DEFAULT now()")
    lines.append(",\n".join(body))
    lines.append(");")
    lines.append("")
    comment = spec.comment.replace("'", "''")
    lines.append(f"COMMENT ON TABLE {spec.staging_table} IS\n    '{comment}';")
    for col in spec.columns:
        if col.note:
            note = col.note.replace("'", "''")
            lines.append(
                f"COMMENT ON COLUMN {spec.staging_table}.{col.column} IS '{note}';"
            )
    lines.append("")
    lines.append(
        "-- FKs físicas de lineage — funcionam de imediato com a MESMA credencial"
    )
    lines.append(
        "-- de escrita da Raw (.env.shopee-write.local, role \"postgres\", já é"
    )
    lines.append(
        "-- dona de raw.shopee_* e tem REFERENCES sobre elas). Só uma FUTURA role"
    )
    lines.append(
        "-- de automação dedicada e diferente precisaria de GRANT REFERENCES"
    )
    lines.append(
        "-- prévio do owner da Raw — ver docstring de build_sql.py."
    )
    lines.append(
        f"ALTER TABLE {spec.staging_table} ADD CONSTRAINT "
        f"fk_{spec.staging_table.split('.')[-1]}_raw_id "
        f"FOREIGN KEY (raw_id) REFERENCES {spec.raw_table} (id);"
    )
    lines.append(
        f"ALTER TABLE {spec.staging_table} ADD CONSTRAINT "
        f"fk_{spec.staging_table.split('.')[-1]}_file_id "
        f"FOREIGN KEY (file_id) REFERENCES raw.shopee_ingestion_file (file_id);"
    )
    lines.append("")
    for stmt in spec.extra_ddl:
        lines.append(stmt)
    lines.append("")
    lines.append("-- REVOKE ALL FROM PUBLIC só remove o acesso implícito do pseudo-role")
    lines.append("-- PUBLIC — NÃO revoga privilégios já concedidos a roles NOMEADAS via")
    lines.append("-- ALTER DEFAULT PRIVILEGES de schema (mesmo achado documentado para as")
    lines.append("-- tabelas raw.shopee_* em db/sql/raw/shopee_raw_ddl.sql). Nenhum")
    lines.append("-- GRANT/REVOKE adicional é decidido por este DDL.")
    lines.append(f"REVOKE ALL ON {spec.staging_table} FROM PUBLIC;")
    return "\n".join(lines)


def build_select(spec: mapping.TableSpec, *, incremental: bool) -> str:
    """SELECT tipado sobre a Raw. Com incremental=True inclui o anti-join
    (idempotência por raw_id); sem, serve para preview read-only."""
    select_lines = []
    for col in spec.columns:
        select_lines.append(f"    {column_expression(col):<40} AS {col.column}")
    sql = [
        "SELECT",
        ",\n".join(select_lines),
        f"FROM {spec.raw_table} r",
        "JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id",
    ]
    if incremental:
        sql.append(
            "WHERE NOT EXISTS (\n"
            f"    SELECT 1 FROM {spec.staging_table} s WHERE s.raw_id = r.id\n"
            ")"
        )
    return "\n".join(sql)


def build_transform(spec: mapping.TableSpec) -> str:
    """INSERT incremental de uma tabela. `ON CONFLICT (raw_id) DO NOTHING` é
    defesa redundante ao anti-join (corrida entre avaliação e INSERT);
    conflitos na UNIQUE (file_id, source_row_number) por um raw_id
    DIFERENTE não são alvo deste ON CONFLICT e continuam abortando a
    transação. Sem ORDER BY: nenhuma ordem é garantida nem necessária para
    um INSERT idempotente por raw_id."""
    cols = ",\n".join(f"    {c.column}" for c in spec.columns)
    return (
        f"INSERT INTO {spec.staging_table} (\n{cols}\n)\n"
        + build_select(spec, incremental=True)
        + "\nON CONFLICT (raw_id) DO NOTHING;"
    )


def _lock_table_targets() -> list[str]:
    """As 4 tabelas Raw/manifesto + as 3 tabelas staging, em ordem
    alfabética fixa — uma única instrução LOCK TABLE, para nunca haver uma
    janela em que só parte dos locks foi adquirida (evita deadlock entre
    execuções concorrentes deste mesmo script: todas pedem os locks na
    MESMA ordem, sempre)."""
    tables = {"raw.shopee_ingestion_file"}
    for spec in mapping.ALL_SPECS:
        tables.add(spec.raw_table)
        tables.add(spec.staging_table)
    return sorted(tables)


def _render_preinsert_do_block() -> str:
    """Bloco de validação fail-fast (PRÉ-INSERT): por fonte, UMA query
    agregada (`count(*) FILTER`) cobrindo todas as condições de linha + as
    poucas checagens estruturais com scan próprio. ~3 scans de
    pré-validação por fonte (9 no total das 3 fontes) — não mais um scan
    por condição; não inclui os scans dos passos 5 (INSERT) e 6
    (pós-insert), contabilizados à parte (ver módulo docstring). Mensagens
    nunca interpolam payload — `reason` é texto estático de validations.py."""
    lines = ["DO $$", "DECLARE", "    c RECORD;", "    v_count bigint;", "BEGIN"]
    for spec in mapping.ALL_SPECS:
        q = validations.build_merged_row_query(spec, incremental=True)
        lines.append(
            f"    -- {spec.source_type}: 1 scan agregado cobre {len(q.reasons)} condições de linha"
        )
        select_sql = ",\n        ".join(q.select_exprs)
        indented_from = "\n    ".join(q.from_clause.splitlines())
        lines.append(f"    SELECT\n        {select_sql}\n    INTO c\n    {indented_from};")
        for i, reason in enumerate(q.reasons):
            reason_esc = reason.replace("'", "''")
            lines.append(
                f"    IF c.c{i} > 0 THEN RAISE EXCEPTION "
                f"'validacao pre-insert falhou -- %: % linha(s)', '{reason_esc}', c.c{i}; END IF;"
            )
        for chk in validations.build_scan_checks(spec):
            reason_esc = chk.reason.replace("'", "''")
            lines.append(f"    SELECT count(*) INTO v_count {chk.body_sql};")
            lines.append(
                "    IF v_count > 0 THEN RAISE EXCEPTION "
                f"'validacao pre-insert falhou -- %: % linha(s)', '{reason_esc}', v_count; END IF;"
            )
    lines.append("END $$;")
    return "\n".join(lines)


def _render_scan_checks_do_block(checks: list[validations.ScanCheck], step_label: str) -> str:
    lines = ["DO $$", "DECLARE", "    v_count bigint;", "BEGIN"]
    for chk in checks:
        reason = chk.reason.replace("'", "''")
        lines.append(f"    SELECT count(*) INTO v_count {chk.body_sql};")
        lines.append(
            f"    IF v_count > 0 THEN RAISE EXCEPTION '{step_label} falhou -- %: % linha(s)', "
            f"'{reason}', v_count; END IF;"
        )
    lines.append("END $$;")
    return "\n".join(lines)


def render_ddl_file() -> str:
    parts = [_DRAFT_HEADER]
    parts.append("BEGIN;")
    parts.append("")
    parts.append("SET LOCAL lock_timeout = '5s';")
    parts.append("SET LOCAL statement_timeout = '120s';")
    for spec in mapping.ALL_SPECS:
        parts.append("")
        parts.append("-- " + "-" * 76)
        parts.append(f"-- {spec.staging_table} — grão: {spec.grain}")
        parts.append("-- " + "-" * 76)
        parts.append(build_ddl(spec))
    parts.append("")
    parts.append("COMMIT;")
    parts.append("")
    return "\n".join(parts)


def render_transform_file() -> str:
    parts = [_DRAFT_HEADER]
    parts.append(
        "-- Transformação incremental e idempotente Raw → silver.stg_shopee_*.\n"
        "-- Ver arquitetura transacional completa no docstring de build_sql.py.\n"
        "-- Fail-fast: qualquer valor fora do formato/domínio comprovado é\n"
        "-- contado e ABORTA a transação ANTES de qualquer INSERT (passo 4) —\n"
        "-- nunca carga parcial silenciosa. As mesmas condições de validação são\n"
        "-- usadas pelo preview read-only (pipelines/staging/shopee/preview.py).\n"
    )
    parts.append("BEGIN;")
    parts.append("")
    parts.append("SET LOCAL lock_timeout = '5s';")
    parts.append("SET LOCAL statement_timeout = '600s';")
    parts.append("")
    parts.append(
        "-- Passo 2: advisory lock de transação — impede duas execuções\n"
        "-- concorrentes deste script (idempotência concorrente). Namespace\n"
        "-- fixo e arbitrário desta fase; '1' cobre as 7 tabelas processadas\n"
        "-- juntas por este script (não há um lock por tabela)."
    )
    parts.append(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_NAMESPACE}, 1);")
    parts.append("")
    parts.append(
        "-- Passo 3: LOCK TABLE em modo compatível com leitura concorrente (SHARE\n"
        "-- permite SELECT de outras sessões, inclusive um preview rodando ao\n"
        "-- mesmo tempo), mas bloqueia INSERT/UPDATE/DELETE de OUTRAS sessões nas\n"
        "-- 4 tabelas Raw/manifesto E nas 3 tabelas staging até o fim desta\n"
        "-- transação — nem a Raw muda sob os pés da validação, nem a staging\n"
        "-- pode ser alterada externamente entre o pós-check e o COMMIT. Uma\n"
        "-- única instrução com todas as 7 tabelas em ordem alfabética fixa\n"
        "-- (evita deadlock entre execuções concorrentes). O INSERT desta mesma\n"
        "-- transação no passo 5 não é bloqueado por este próprio SHARE lock —\n"
        "-- uma transação nunca bloqueia a si mesma."
    )
    lock_targets = ", ".join(_lock_table_targets())
    parts.append(f"LOCK TABLE {lock_targets} IN SHARE MODE;")
    parts.append("")
    parts.append(
        "-- Passo 4 (PRÉ-VALIDAÇÃO): TODAS as fontes, ANTES de qualquer INSERT.\n"
        "-- Mesma fonte de regras do preview (validations.py) — nunca duas\n"
        "-- listas divergentes. ~3 scans de pré-validação por fonte (1 agregado\n"
        "-- + 2 estruturais) = 9 no total das 3 fontes — não mais um scan por\n"
        "-- condição, e não confundir com o total da transformação completa\n"
        "-- (passos 5 e 6 somam mais scans à parte). Mensagens contêm só motivo\n"
        "-- e contagem, nunca payload. Escopo: só linhas AINDA NÃO presentes na\n"
        "-- staging (anti-join por raw_id) — não revalida formato de linhas já\n"
        "-- carregadas; na 1ª carga, elegível = 100% da Raw."
    )
    parts.append(_render_preinsert_do_block())

    parts.append("")
    parts.append(
        "-- Passo 5 (1 leitura+INSERT por fonte, 3 no total): só executam se\n"
        "-- TODAS as validações acima passaram."
    )
    for spec in mapping.ALL_SPECS:
        parts.append("")
        parts.append("-- " + "-" * 76)
        parts.append(f"-- {spec.source_type}: {spec.raw_table} → {spec.staging_table}")
        parts.append("-- " + "-" * 76)
        parts.append(build_transform(spec))

    parts.append("")
    parts.append(
        "-- Passo 6 (1 scan por fonte, 3 no total): validações pós-insert — toda\n"
        "-- linha Raw elegível deve existir na staging com os MESMOS\n"
        "-- file_id/brand/source_row_number/row_sha256 (nunca uma carga parcial\n"
        "-- ou uma linha corrompida manualmente que só preservou o hash)."
    )
    post_checks = [validations.post_insert_check(spec) for spec in mapping.ALL_SPECS]
    parts.append(_render_scan_checks_do_block(post_checks, "validacao pos-insert"))

    parts.append("")
    parts.append("-- Passo 7: só chega aqui se nada acima abortou.")
    parts.append("COMMIT;")
    parts.append("")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args == ["--write"]:
        DDL_PATH.write_text(render_ddl_file(), encoding="utf-8", newline="\n")
        TRANSFORM_PATH.write_text(render_transform_file(), encoding="utf-8", newline="\n")
        print(f"gerado: {DDL_PATH}")
        print(f"gerado: {TRANSFORM_PATH}")
        return 0
    if args == ["--check"]:
        ok = True
        for path, render in ((DDL_PATH, render_ddl_file), (TRANSFORM_PATH, render_transform_file)):
            if not path.exists() or path.read_text(encoding="utf-8") != render():
                print(f"DESSINCRONIZADO: {path}")
                ok = False
        if ok:
            print("arquivos gerados em sincronia com mapping.py/validations.py")
        return 0 if ok else 1
    print("uso: python -m pipelines.staging.shopee.build_sql [--write|--check]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
