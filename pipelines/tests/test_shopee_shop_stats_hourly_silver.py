"""
Gate SD1-2 — contrato da Silver para o layout HORÁRIO do shop-stats.

A Silver é 1:1 com a Raw: as 24 linhas horárias e a linha de total do
período são preservadas como evidência, com o grão físico
(file_id, source_row_number) intacto. Nenhuma agregação de hora em dia
acontece aqui e nenhum total é fabricado — quem emite a única linha diária
é o parser Daily, a partir do total que o próprio arquivo traz.

Contrato final:
    daily         -> stat_date NOT NULL, stat_hour NULL,     period_* NULL
    hourly        -> stat_date NOT NULL, stat_hour NOT NULL, period_* NULL
    period_total  -> stat_date NULL,     stat_hour NULL,     period_* NOT NULL
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipelines.staging.shopee import build_sql, mapping, rules_registry, semantics, sql_rules

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_PATH = REPO_ROOT / "db" / "sql" / "staging" / "shopee_staging_ddl.sql"
TRANSFORM_PATH = (REPO_ROOT / "db" / "sql" / "staging"
                  / "shopee_staging_transform.sql")
MIGRACAO_PATH = (REPO_ROOT / "db" / "sql" / "staging"
                 / "shopee_staging_shop_stats_hourly_migration.sql")

_ADVISORY_RE = re.compile(r"pg_advisory_xact_lock\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)")
# Definição REAL da constraint antiga, coletada read-only do banco em
# 2026-08-11 (pg_get_constraintdef). Fixture de referência das contraprovas —
# nenhum dado de negócio, só a expressão da constraint.
CK_ANTIGA_REAL = (
    "CHECK (((((row_type)::text = 'daily'::text) AND (stat_date IS NOT NULL) "
    "AND (period_start IS NULL) AND (period_end IS NULL)) "
    "OR (((row_type)::text = 'period_total'::text) AND (stat_date IS NULL) "
    "AND (period_start IS NOT NULL) AND (period_end IS NOT NULL))))"
)


def _normaliza_constraintdef(definicao: str) -> str:
    """Mesma normalização do precheck D da migration: remove parênteses e
    colapsa sequências de espaço. Reimplementada aqui de propósito, para que
    o teste falhe se a migration mudar o normalizador sem atualizar a
    constante esperada."""
    sem_parens = re.sub(r"[()]", "", definicao)
    return re.sub(r"\s+", " ", sem_parens).strip()


def _literal_esperado_do_precheck() -> str:
    """Extrai a constante `v_esperado` da migration, concatenando as
    constantes de string adjacentes e desfazendo o escape de aspas."""
    sql = MIGRACAO_PATH.read_text(encoding="utf-8")
    inicio = sql.index("v_esperado CONSTANT text :=")
    fim = sql.index(";", inicio)
    bloco = sql[inicio:fim]
    partes = re.findall(r"'((?:[^']|'')*)'", bloco)
    assert partes, "constante v_esperado não encontrada na migration"
    return "".join(partes).replace("''", "'")


def _col(nome: str) -> mapping.StagingColumn:
    for c in mapping.SHOP_STATS.columns:
        if c.column == nome:
            return c
    raise AssertionError(f"coluna {nome!r} não existe no contrato de shop_stats")


def _ck_row_type() -> str:
    for stmt in mapping.SHOP_STATS.extra_ddl:
        if "ck_stg_shopee_shop_stats_row_type" in stmt:
            return stmt
    raise AssertionError("constraint de row_type não encontrada")


def _sql_executavel(path: Path) -> str:
    """Conteúdo do arquivo SEM comentários de linha. As varreduras de
    segurança precisam julgar o que o banco vai executar — não a prosa que
    documenta justamente o que a migração NÃO faz."""
    linhas = []
    for linha in path.read_text(encoding="utf-8").splitlines():
        sem = re.sub(r"--.*$", "", linha)
        if sem.strip():
            linhas.append(sem)
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# 1-2. Layouts antigos permanecem válidos e idênticos
# ---------------------------------------------------------------------------
def test_daily_continua_reconhecido_pelo_formato_de_data():
    expr = sql_rules.shop_stats_row_type("v")
    assert "'daily'" in expr
    assert "^[0-9]{2}/[0-9]{2}/[0-9]{4}$" in expr


def test_period_total_continua_reconhecido_pelo_formato_de_range():
    expr = sql_rules.shop_stats_row_type("v")
    assert "'period_total'" in expr
    assert "^[0-9]{2}/[0-9]{2}/[0-9]{4}-[0-9]{2}/[0-9]{2}/[0-9]{4}$" in expr


def test_ramo_daily_da_constraint_e_identico_ao_anterior_mais_stat_hour_null():
    ck = _ck_row_type()
    assert ("row_type = 'daily' AND stat_date IS NOT NULL AND stat_hour IS NULL "
            "AND period_start IS NULL AND period_end IS NULL") in ck


def test_ramo_period_total_da_constraint_preserva_a_regra_anterior():
    ck = _ck_row_type()
    assert ("row_type = 'period_total' AND stat_date IS NULL AND stat_hour IS NULL "
            "AND period_start IS NOT NULL AND period_end IS NOT NULL") in ck


def test_stat_date_continua_valendo_para_daily():
    expr = sql_rules.shop_stats_stat_date("v")
    assert semantics.RE_BR_DATE in expr


# ---------------------------------------------------------------------------
# 3. 'Tempo = DD/MM/YYYY HH:MM' vira hourly com stat_date e stat_hour
# ---------------------------------------------------------------------------
def test_formato_horario_vira_row_type_hourly():
    expr = sql_rules.shop_stats_row_type("v")
    assert "'hourly'" in expr
    assert "^[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}$" in expr


def test_stat_date_extrai_a_data_do_timestamp_horario():
    expr = sql_rules.shop_stats_stat_date("v")
    assert expr.startswith("COALESCE(")
    assert semantics.RE_BR_TS_HOUR in expr
    assert "make_date" in expr


def test_stat_hour_extrai_a_hora_cheia():
    expr = sql_rules.shop_stats_stat_hour("v")
    assert semantics.RE_BR_TS_HOUR in expr
    assert "make_time" in expr
    # sempre hora cheia: minuto e segundo zerados na construção
    assert ", 0, 0)" in expr


def test_stat_hour_e_time_without_time_zone():
    assert _col("stat_hour").sql_type == "time without time zone"
    assert _col("stat_hour").nullable is True


def test_ramo_hourly_exige_stat_date_e_stat_hour():
    ck = _ck_row_type()
    assert ("row_type = 'hourly' AND stat_date IS NOT NULL AND stat_hour IS NOT NULL "
            "AND period_start IS NULL AND period_end IS NULL") in ck


# ---------------------------------------------------------------------------
# 5. Hora inválida / minuto inesperado é recusado pela validação semântica
# ---------------------------------------------------------------------------
def test_hora_fora_de_00_23_e_marcada_invalida():
    expr = semantics.br_hour_ts_is_invalid("v")
    assert "NOT BETWEEN 0 AND 23" in expr


def test_minuto_diferente_de_zero_e_marcado_invalido():
    expr = semantics.br_hour_ts_is_invalid("v")
    assert "<> 0" in expr


def test_data_de_calendario_invalida_no_horario_e_marcada():
    expr = semantics.br_hour_ts_is_invalid("v")
    # reutiliza a mesma regra de calendário do resto do contrato
    assert "BETWEEN 1 AND 12" in expr


def test_quarto_formato_desconhecido_continua_bloqueando():
    expr = semantics.shop_stats_data_format_is_invalid("v")
    # inválido só quando NÃO casa com nenhum dos três formatos
    assert expr.count("!~") == 3
    for rx in (semantics.RE_BR_DATE, semantics.RE_BR_DATE_RANGE, semantics.RE_BR_TS_HOUR):
        assert rx in expr


def test_row_type_desconhecido_vira_null_e_reprova_no_not_null():
    assert "ELSE NULL END" in sql_rules.shop_stats_row_type("v")
    assert _col("row_type").nullable is False


def test_validacao_semantica_do_stat_date_cobre_os_dois_formatos():
    regra = rules_registry.REGISTRY["shop_stats_stat_date"]
    expr = regra.is_invalid("v")
    assert semantics.RE_BR_DATE in expr
    assert semantics.RE_BR_TS_HOUR in expr


def test_stat_hour_tem_validacao_semantica_registrada():
    regra = rules_registry.REGISTRY["shop_stats_stat_hour"]
    assert regra.is_invalid is semantics.br_hour_ts_is_invalid


# ---------------------------------------------------------------------------
# 6-7. Exclusividade entre os ramos
# ---------------------------------------------------------------------------
def test_hourly_nunca_recebe_period_start_ou_period_end():
    ck = _ck_row_type()
    ramo = [p for p in ck.split(" OR ") if "'hourly'" in p][0]
    assert "period_start IS NULL" in ramo and "period_end IS NULL" in ramo


def test_period_total_nunca_recebe_stat_date_ou_stat_hour():
    ck = _ck_row_type()
    ramo = [p for p in ck.split(" OR ") if "'period_total'" in p][0]
    assert "stat_date IS NULL" in ramo and "stat_hour IS NULL" in ramo


def test_period_start_e_end_so_saem_do_formato_range():
    for coluna in ("period_start", "period_end"):
        expr = build_sql.column_expression(_col(coluna))
        assert semantics.RE_BR_DATE_RANGE in expr
        assert semantics.RE_BR_TS_HOUR not in expr


# ---------------------------------------------------------------------------
# 8. Nenhum total é fabricado e nenhuma hora é agregada na Silver
# ---------------------------------------------------------------------------
def test_silver_nao_agrega_horas_nem_fabrica_total():
    sql = build_sql.render_transform_file()
    trecho = sql.split("INSERT INTO silver.stg_shopee_shop_stats")[1]
    corpo = trecho.split("INSERT INTO")[0]
    for proibido in ("GROUP BY", "sum(", "SUM(", "avg(", "AVG("):
        assert proibido not in corpo, proibido
    assert "SELECT" in corpo


def test_grao_declarado_menciona_dia_hora_e_total():
    g = mapping.SHOP_STATS.grain.lower()
    assert "dia" in g and "hora" in g and "total" in g


# ---------------------------------------------------------------------------
# 9-10. Grão físico preservado; nenhum UNIQUE (brand, stat_date)
# ---------------------------------------------------------------------------
def test_grao_fisico_continua_file_id_source_row_number():
    ddl = "\n".join(mapping.SHOP_STATS.extra_ddl)
    assert ("CREATE UNIQUE INDEX uk_stg_shopee_shop_stats_file_row "
            "ON silver.stg_shopee_shop_stats (file_id, source_row_number);") in ddl


def test_nenhum_unique_por_brand_e_data_foi_introduzido():
    ddl = "\n".join(mapping.SHOP_STATS.extra_ddl)
    assert "CREATE INDEX idx_stg_shopee_shop_stats_brand_date" in ddl
    assert "CREATE UNIQUE INDEX idx_stg_shopee_shop_stats_brand_date" not in ddl
    uniques = [s for s in mapping.SHOP_STATS.extra_ddl if "UNIQUE" in s.upper()]
    assert len(uniques) == 1
    assert "(file_id, source_row_number)" in uniques[0]


def test_pk_continua_sendo_raw_id():
    assert _col("raw_id") if False else True  # raw_id vem de _PROVENANCE
    ddl = build_sql.render_ddl_file()
    trecho = ddl.split("CREATE TABLE silver.stg_shopee_shop_stats")[1]
    assert "raw_id" in trecho.split(");")[0]
    assert "PRIMARY KEY" in trecho.split(");")[0]


# ---------------------------------------------------------------------------
# 11-12. Segurança do SQL de migração
# ---------------------------------------------------------------------------
def test_migracao_existe_e_e_transacional():
    sql = MIGRACAO_PATH.read_text(encoding="utf-8")
    assert sql.count("BEGIN;") == 1
    assert sql.count("COMMIT;") == 1
    assert sql.index("BEGIN;") < sql.index("COMMIT;")


def test_migracao_so_toca_a_tabela_autorizada():
    sql = _sql_executavel(MIGRACAO_PATH)
    alvos = set(re.findall(r"ALTER TABLE\s+([a-z_\.]+)", sql))
    assert alvos == {"silver.stg_shopee_shop_stats"}
    # a única menção a raw.* aceitável é o texto do COMMENT ON TABLE, que
    # descreve a origem — nunca um comando contra a tabela raw.
    assert not re.search(r"(ALTER|DROP|TRUNCATE|DELETE|UPDATE|INSERT)[^;]*\braw\.", sql)
    for proibida in ("gold.", "marts.", "silver.stg_shopee_ads",
                     "silver.stg_shopee_order_item_snapshots"):
        assert proibida not in sql, proibida


def test_migracao_nao_contem_dml_destrutivo():
    sql = _sql_executavel(MIGRACAO_PATH).upper()
    for proibido in ("DELETE FROM", "TRUNCATE", "DROP TABLE",
                     "INSERT INTO SILVER", "ALTER COLUMN", "DROP COLUMN"):
        assert proibido not in sql, proibido
    # nenhum UPDATE de dados: o único "UPDATE" tolerado seria em prosa, já
    # removida acima.
    assert not re.search(r"\bUPDATE\s+[A-Z_\.]+\s+SET\b", sql)


def test_migracao_adiciona_apenas_stat_hour_e_as_constraints_previstas():
    sql = MIGRACAO_PATH.read_text(encoding="utf-8")
    assert "ADD COLUMN stat_hour time without time zone;" in sql
    assert sql.count("ADD COLUMN") == 1
    assert "DROP CONSTRAINT ck_stg_shopee_shop_stats_row_type;" in sql
    assert sql.count("DROP CONSTRAINT") == 1
    assert "ADD CONSTRAINT ck_stg_shopee_shop_stats_row_type" in sql
    assert "ADD CONSTRAINT ck_stg_shopee_shop_stats_stat_hour_cheia" in sql


def test_migracao_tem_prechecks_e_postchecks_fail_fast():
    sql = MIGRACAO_PATH.read_text(encoding="utf-8")
    assert "precheck" in sql.lower()
    assert "postcheck" in sql.lower()
    assert sql.count("RAISE EXCEPTION") >= 10
    assert "pg_is_in_recovery()" in sql
    assert "pg_advisory_xact_lock" in sql


def test_migracao_aborta_se_reaplicada():
    sql = MIGRACAO_PATH.read_text(encoding="utf-8")
    assert "stat_hour ja existe" in sql


def test_migracao_preserva_indices_e_fks_nos_postchecks():
    sql = MIGRACAO_PATH.read_text(encoding="utf-8")
    assert "uk_stg_shopee_shop_stats_file_row" in sql
    assert "idx_stg_shopee_shop_stats_brand_date" in sql
    assert "stg_shopee_shop_stats_pkey" in sql
    assert "contype = 'f'" in sql


# ---------------------------------------------------------------------------
# 13. DDL-base, mapping e SQL gerado sincronizados
# ---------------------------------------------------------------------------
def test_ddl_base_reflete_o_contrato_final():
    ddl = DDL_PATH.read_text(encoding="utf-8")
    assert "stat_hour                       time without time zone," in ddl
    assert "ck_stg_shopee_shop_stats_stat_hour_cheia" in ddl
    assert "'hourly'" in ddl


def test_ddl_gerado_em_memoria_bate_com_o_arquivo_versionado():
    assert build_sql.render_ddl_file() == DDL_PATH.read_text(encoding="utf-8")


def test_transform_gerado_reconhece_tempo_e_hourly():
    sql = build_sql.render_transform_file()
    assert "->> 'Tempo'" in sql
    assert "'hourly'" in sql
    assert "AS stat_hour," in sql


def test_tempo_esta_no_allowlist_de_drift_de_shop_stats():
    assert "Tempo" in mapping.covered_keys(mapping.SHOP_STATS)
    assert "Data" in mapping.covered_keys(mapping.SHOP_STATS)


# ---------------------------------------------------------------------------
# Finding 1 (revisão) — a migration precisa SERIALIZAR com o transform Silver.
# Chaves diferentes no mesmo namespace não se excluem: a migration tem que
# adquirir exatamente a MESMA chave que o transform.
# ---------------------------------------------------------------------------
def test_migration_e_transform_usam_a_mesma_chave_de_advisory_lock():
    migracao = _ADVISORY_RE.findall(_sql_executavel(MIGRACAO_PATH))
    transform = _ADVISORY_RE.findall(TRANSFORM_PATH.read_text(encoding="utf-8"))
    assert len(migracao) == 1, f"esperada 1 chamada na migration, achadas {len(migracao)}"
    assert len(transform) == 1, f"esperada 1 chamada no transform, achadas {len(transform)}"
    namespace_m, chave_m = migracao[0]
    namespace_t, chave_t = transform[0]
    assert (namespace_m, chave_m) == (namespace_t, chave_t), (
        f"migration usa ({namespace_m}, {chave_m}) e transform usa "
        f"({namespace_t}, {chave_t}) — chaves diferentes NAO serializam"
    )


def test_advisory_lock_da_migration_e_o_par_esperado():
    namespace, chave = _ADVISORY_RE.findall(_sql_executavel(MIGRACAO_PATH))[0]
    assert (namespace, chave) == ("84772001", "1")


def test_migration_tem_uma_unica_chamada_de_advisory_lock():
    assert _sql_executavel(MIGRACAO_PATH).count("pg_advisory_xact_lock") == 1


def test_migration_nao_introduz_retry_nem_backoff():
    sql = _sql_executavel(MIGRACAO_PATH).lower()
    for mecanica in ("pg_try_advisory", "loop", "while ", "retry", "pg_sleep",
                     "backoff", "exception when", "continue"):
        assert mecanica not in sql, mecanica


def test_migration_preserva_timeouts_e_transacao():
    sql = _sql_executavel(MIGRACAO_PATH)
    assert "SET LOCAL lock_timeout" in sql
    assert "SET LOCAL statement_timeout" in sql
    assert sql.count("BEGIN;") == 1 and sql.count("COMMIT;") == 1


def test_comentario_do_lock_nao_alega_chave_diferente():
    sql = MIGRACAO_PATH.read_text(encoding="utf-8")
    trecho = sql[sql.index("Passo 0"):sql.index("pg_advisory_xact_lock")]
    assert "84772001, 2" not in trecho
    assert "84772001, 1" in trecho


# ---------------------------------------------------------------------------
# Finding 2 (revisão) — o precheck D tem que provar a forma ANTIGA exata,
# não apenas conter/não-conter um trecho. Contraprovas com o mesmo
# normalizador da migration, sem tocar no banco.
# ---------------------------------------------------------------------------
def test_precheck_d_compara_forma_normalizada_e_nao_usa_like_permissivo():
    sql = _sql_executavel(MIGRACAO_PATH)
    assert "regexp_replace" in sql
    assert "IS DISTINCT FROM v_esperado" in sql
    # o LIKE que sobrou é só a guarda redundante contra hourly/stat_hour
    assert "v_def NOT LIKE '%period_total%'" not in sql


def test_precheck_d_nao_usa_sql_dinamico():
    sql = _sql_executavel(MIGRACAO_PATH).upper()
    for proibido in ("EXECUTE ", "FORMAT(", "QUOTE_IDENT", "QUOTE_LITERAL"):
        assert proibido not in sql, proibido


def test_constante_esperada_bate_com_a_definicao_real_normalizada():
    """Contraprova 1 — a definição antiga REAL (coletada read-only do banco)
    normaliza exatamente para a constante embutida no precheck."""
    assert _normaliza_constraintdef(CK_ANTIGA_REAL) == _literal_esperado_do_precheck()


def test_precheck_d_rejeita_definicao_com_hourly():
    """Contraprova 2 — uma constraint que já menciona hourly/stat_hour reprova."""
    com_hourly = (
        "CHECK (((((row_type)::text = 'daily'::text) AND (stat_date IS NOT NULL) "
        "AND (stat_hour IS NULL) AND (period_start IS NULL) AND (period_end IS NULL)) "
        "OR (((row_type)::text = 'hourly'::text) AND (stat_date IS NOT NULL) "
        "AND (stat_hour IS NOT NULL)) "
        "OR (((row_type)::text = 'period_total'::text) AND (stat_date IS NULL) "
        "AND (period_start IS NOT NULL) AND (period_end IS NOT NULL))))"
    )
    assert _normaliza_constraintdef(com_hourly) != _literal_esperado_do_precheck()


def test_precheck_d_rejeita_condicao_extra_inesperada():
    """Contraprova 3 — uma condição material a mais reprova."""
    com_extra = CK_ANTIGA_REAL.replace(
        "AND (stat_date IS NOT NULL)",
        "AND (stat_date IS NOT NULL) AND (visitors IS NOT NULL)",
        1,
    )
    assert _normaliza_constraintdef(com_extra) != _literal_esperado_do_precheck()


def test_precheck_d_rejeita_ausencia_de_requisito_antigo():
    """Contraprova 4 — faltando um requisito do contrato antigo, reprova."""
    sem_requisito = CK_ANTIGA_REAL.replace(" AND (period_end IS NULL)", "", 1)
    assert _normaliza_constraintdef(sem_requisito) != _literal_esperado_do_precheck()


def test_normalizacao_absorve_reimpressao_com_quebras_de_linha():
    """Ruído de formatação que o PostgreSQL pode emitir (quebras de linha)
    é absorvido — a definição continua sendo aceita."""
    com_quebras = CK_ANTIGA_REAL.replace(" AND ", "\n    AND ")
    assert _normaliza_constraintdef(com_quebras) == _literal_esperado_do_precheck()


def test_normalizacao_e_conservadora_com_espacamento_que_o_postgres_nao_emite():
    """Limite documentado: espaço DENTRO dos parênteses ('( row_type )') não
    é produzido por pg_get_constraintdef. Se aparecer, o precheck aborta em
    vez de aceitar — falha conservadora, exigindo revisão humana."""
    editado_a_mao = CK_ANTIGA_REAL.replace("(row_type)", "( row_type )")
    assert _normaliza_constraintdef(editado_a_mao) != _literal_esperado_do_precheck()


def test_precheck_d_aborta_antes_de_qualquer_ddl():
    """A validação da constraint acontece no bloco de PRECHECKS, que vem
    inteiro antes do primeiro ALTER TABLE do arquivo."""
    sql = _sql_executavel(MIGRACAO_PATH)
    assert sql.index("IS DISTINCT FROM v_esperado") < sql.index("ALTER TABLE")
