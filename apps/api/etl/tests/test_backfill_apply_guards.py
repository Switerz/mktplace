"""
Gate SH-API-2E1 — guardrails do caminho de escrita do backfill Shopee.

Nada aqui abre conexao com banco. O que se testa e' o CONTRATO: cada porta
reprova sozinha, o backup e' commitado ANTES da mutacao, as colunas sao
explicitas por destino, os estados parciais tem nome, e nao ha retry.

Regra que guiou os testes: uma porta que so' reprova quando outra ja reprovou
nao e' uma porta. Por isso quase todo teste deixa o resto do mundo VALIDO e
degrada exatamente um requisito.
"""
import datetime
from decimal import Decimal

import pandas as pd
import pytest

from etl import backfill_shopee_products as bf

AUT = list(bf.AUTHORIZED_SCOPES)
ENV_OK = {
    bf.CONSENT_ENV: "1",
    "BACKFILL_NEON_RW_URL": "postgresql://gravador@remoto.example/db",
    "BACKFILL_NEON_RO_URL": "postgresql://leitor@remoto.example/db",
    "BACKFILL_LOCAL_RW_URL": "postgresql://gravador@localhost:5432/db",
    "BACKFILL_LOCAL_RO_URL": "postgresql://leitor@localhost:5432/db",
}


def env(**over):
    e = dict(ENV_OK)
    for k, v in over.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


# ---------------------------------------------------------------------------
# Porta 1 — allowlist EXATA
# ---------------------------------------------------------------------------

def test_allowlist_aceita_exatamente_o_par_autorizado():
    bf.assert_scopes_authorized(AUT)                      # nao levanta
    bf.assert_scopes_authorized(list(reversed(AUT)))      # ordem nao importa


def test_execucao_sem_escopo_e_recusada():
    with pytest.raises(bf.ScopeNotAuthorizedError) as ei:
        bf.assert_scopes_authorized([])
    assert "SEM escopo" in str(ei.value)
    assert "nada foi escrito" in str(ei.value)


@pytest.mark.parametrize("parcial", [[AUT[0]], [AUT[1]]])
def test_execucao_parcial_e_recusada(parcial):
    # Metade do par nao fecha a reconciliacao total medida — por isso
    # subconjunto tambem e' recusado, nao so' conjunto extra.
    with pytest.raises(bf.ScopeNotAuthorizedError) as ei:
        bf.assert_scopes_authorized(parcial)
    assert "PARCIAL" in str(ei.value)


def test_escopo_extra_junto_dos_autorizados_e_recusado():
    with pytest.raises(bf.ScopeNotAuthorizedError):
        bf.assert_scopes_authorized(AUT + [("lescent", "2026-05")])


def test_escopo_repetido_e_recusado():
    with pytest.raises(bf.ScopeNotAuthorizedError) as ei:
        bf.assert_scopes_authorized(AUT + [AUT[0]])
    assert "repetido" in str(ei.value)


@pytest.mark.parametrize("escopo,marcador", [
    (("rituaria", "2026-07"), "imatura"),
    (("kokeshi", "2026-08"), "recarregar"),
])
def test_julho_e_agosto_sao_recusados_com_o_motivo_medido(escopo, marcador):
    # Recusa generica ("nao autorizado") faria o operador achar que basta
    # adicionar a lista. O motivo tem de viajar junto.
    with pytest.raises(bf.ScopeNotAuthorizedError) as ei:
        bf.assert_scopes_authorized([escopo])
    assert marcador in str(ei.value)
    assert f"{escopo[0]}:{escopo[1]}" in str(ei.value)


def test_execucao_ampla_marca_inteira_e_recusada():
    with pytest.raises(bf.ScopeNotAuthorizedError):
        bf.assert_scopes_authorized([("apice", "2026-01"), ("apice", "2026-02"),
                                     ("apice", "2026-03")])


def test_nenhum_mes_fora_de_maio_entra_na_allowlist():
    assert {m for _, m in bf.AUTHORIZED_SCOPES} == {"2026-05"}
    assert {b for b, _ in bf.AUTHORIZED_SCOPES} == {"apice", "barbours"}


# ---------------------------------------------------------------------------
# Portas 2-4 — consentimento, credencial dedicada, destino
# ---------------------------------------------------------------------------

def test_consentimento_ausente_bloqueia():
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.assert_write_preconditions(scopes=AUT, target="neon",
                                      env=env(**{bf.CONSENT_ENV: None}))
    assert bf.CONSENT_ENV in str(ei.value)


def test_consentimento_com_valor_errado_nao_conta_como_consentimento():
    with pytest.raises(bf.WriteGuardError):
        bf.assert_write_preconditions(scopes=AUT, target="neon",
                                      env=env(**{bf.CONSENT_ENV: "true"}))


def test_credencial_de_escrita_ausente_bloqueia():
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.resolve_write_url("neon", env=env(BACKFILL_NEON_RW_URL=None))
    assert "BACKFILL_NEON_RW_URL" in str(ei.value)
    assert "DATABASE_URL" in str(ei.value)


def test_credencial_de_escrita_igual_a_de_leitura_e_recusada():
    # Reaproveitar a read-only esconde o momento em que a operacao deixou de
    # ser segura.
    mesma = "postgresql://mesma@remoto.example/db"
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.resolve_write_url("neon", env=env(BACKFILL_NEON_RW_URL=mesma,
                                             BACKFILL_NEON_RO_URL=mesma))
    assert "dedicada" in str(ei.value)


def test_url_de_escrita_nunca_vaza_na_mensagem():
    segredo = "postgresql://u:supersegredo@localhost:5432/db"
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.resolve_write_url("neon", env=env(BACKFILL_NEON_RW_URL=segredo))
    msg = str(ei.value)
    assert "supersegredo" not in msg and "://" not in msg


@pytest.mark.parametrize("target,url", [
    ("local", "postgresql://u@remoto.example/db"),
    ("neon", "postgresql://u@localhost:5432/db"),
])
def test_classe_de_host_da_credencial_de_escrita_e_conferida(target, url):
    var = {"local": "BACKFILL_LOCAL_RW_URL", "neon": "BACKFILL_NEON_RW_URL"}[target]
    with pytest.raises(bf.WriteGuardError):
        bf.resolve_write_url(target, env=env(**{var: url}))


def test_target_invalido_bloqueia():
    with pytest.raises(bf.BackfillUsageError):
        bf.assert_write_preconditions(scopes=AUT, target="producao", env=env())


def test_portas_sem_conexao_ficam_nao_avaliadas_nunca_aprovadas():
    # Ausencia de avaliacao NAO pode virar verde: e' exatamente assim que um
    # preflight passa a mentir quando o ambiente muda.
    laudo = bf.assert_write_preconditions(scopes=AUT, target="neon", env=env())
    for porta in ("5_identidade", "6_primary", "7_ssl", "8_advisory_lock"):
        assert laudo[porta] == "nao_avaliada"
    assert laudo["9_backup"] == "pendente"
    assert laudo["10_expectativa"] == "nao_avaliada"


# ---------------------------------------------------------------------------
# Portas 5-8 — identidade, primary, SSL, advisory lock
# ---------------------------------------------------------------------------

class FakeRow(dict):
    pass


class FakeConn:
    """Conexao falsa que responde as consultas de preflight."""

    def __init__(self, *, in_recovery=False, tx_readonly="off", privs=True,
                 ssl=True, lock=True, db="neondb", tem_produtos=1, tem_serving=1,
                 seq_usage=True):
        self.cfg = dict(in_recovery=in_recovery, tx_readonly=tx_readonly,
                        privs=privs, lock=lock, db=db, seq_usage=seq_usage,
                        tem_produtos=tem_produtos, tem_serving=tem_serving)
        self.connection = type("C", (), {"info": type("I", (), {"ssl_in_use": ssl})()})()
        self.sqls = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sqls.append(sql)
        cfg = self.cfg
        if "pg_try_advisory_lock" in sql:
            return _Escalar(cfg["lock"])
        if "pg_advisory_unlock" in sql:
            return _Escalar(True)
        if "pg_get_serial_sequence" in sql:
            return _Mapping(FakeRow(seq="marts.fact_shopee_product_monthly_id_seq"))
        if "has_sequence_privilege" in sql:
            return _Mapping(FakeRow(ok=cfg["seq_usage"]))
        if "pg_is_in_recovery" in sql:
            return _Mapping(FakeRow(in_recovery=cfg["in_recovery"],
                                    tx_readonly=cfg["tx_readonly"],
                                    pode_insert=cfg["privs"],
                                    pode_delete=cfg["privs"],
                                    pode_criar_backup=cfg["privs"]))
        if "current_database" in sql:
            return _Mapping(FakeRow(db=cfg["db"], tem_produtos=cfg["tem_produtos"],
                                    tem_serving=cfg["tem_serving"]))
        raise AssertionError(f"SQL inesperado no preflight: {sql[:60]}")


class _Escalar:
    def __init__(self, v): self._v = v
    def scalar(self): return self._v


class _Mapping:
    def __init__(self, row): self._row = row
    def mappings(self): return self
    def first(self): return self._row


ENV_ID = {"BACKFILL_NEON_EXPECT_DB": "neondb", "BACKFILL_LOCAL_EXPECT_DB": "mkt"}


def test_identidade_reprovada_bloqueia_antes_das_outras_portas():
    conn = FakeConn(db="outro_banco")
    with pytest.raises(bf.BackfillIdentityError):
        bf.assert_write_preconditions(scopes=AUT, target="neon", conn=conn,
                                      env=env(**ENV_ID))
    # nunca chegou ao advisory lock
    assert not any("advisory" in s for s in conn.sqls)


def test_replica_em_recovery_e_recusada():
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.assert_writable_primary(FakeConn(in_recovery=True), "neon")
    assert "RECOVERY" in str(ei.value)


def test_transacao_read_only_e_recusada():
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.assert_writable_primary(FakeConn(tx_readonly="on"), "neon")
    assert "read_only" in str(ei.value)


def test_credencial_sem_privilegio_real_e_recusada():
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.assert_writable_primary(FakeConn(privs=False), "neon")
    assert "privilegio" in str(ei.value)


def test_privilegio_e_consultado_nunca_testado_com_dml():
    conn = FakeConn()
    bf.assert_writable_primary(conn, "neon")
    juntos = " ".join(conn.sqls).upper()
    for dml in ("INSERT INTO", "DELETE FROM", "UPDATE ", "CREATE TABLE"):
        assert dml not in juntos, f"preflight executou {dml}"


def test_neon_sem_ssl_e_recusado():
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.assert_ssl_required(FakeConn(ssl=False), "neon")
    assert "SSL" in str(ei.value)


def test_ssl_nao_e_exigido_no_local():
    bf.assert_ssl_required(FakeConn(ssl=False), "local")   # nao levanta


def test_advisory_lock_tomado_bloqueia_sem_esperar():
    conn = FakeConn(lock=False)
    with pytest.raises(bf.WriteGuardError) as ei:
        bf.acquire_advisory_lock(conn)
    assert "nada foi aguardado" in str(ei.value)
    # `pg_try_advisory_lock` (nao bloqueante), nunca `pg_advisory_lock`
    assert any("pg_try_advisory_lock" in s for s in conn.sqls)
    assert not any("pg_advisory_lock(" in s for s in conn.sqls)


def test_chave_do_advisory_lock_e_deterministica():
    assert bf.ADVISORY_LOCK_KEY == bf.ADVISORY_LOCK_KEY
    assert isinstance(bf.ADVISORY_LOCK_KEY, int)
    assert -(2 ** 63) <= bf.ADVISORY_LOCK_KEY < 2 ** 63


def test_cadeia_completa_com_conexao_valida_passa():
    conn = FakeConn()
    laudo = bf.assert_write_preconditions(scopes=AUT, target="neon", conn=conn,
                                          env=env(**ENV_ID))
    assert laudo["5_identidade"] == "ok"
    assert laudo["6_primary"] == "ok"
    assert laudo["7_ssl"] == "ok"
    assert laudo["8_advisory_lock"] == "adquirido"


# ---------------------------------------------------------------------------
# Porta 10 — expectativa medida
# ---------------------------------------------------------------------------

DELTA_OK = {("apice", "2026-05"): Decimal("-23292.43"),
            ("barbours", "2026-05"): Decimal("-80987.03")}


def test_expectativa_medida_bate():
    bf.assert_expected_delta(gmv_delta_por_escopo=DELTA_OK,
                             keys_added=0, keys_removed=0)


def test_total_esperado_e_a_soma_dos_dois_escopos():
    assert sum(bf.EXPECTED_GMV_DELTA.values()) == bf.EXPECTED_TOTAL_GMV_DELTA
    assert bf.EXPECTED_TOTAL_GMV_DELTA == Decimal("-104279.46")


def test_delta_divergente_bloqueia():
    ruim = {**DELTA_OK, ("apice", "2026-05"): Decimal("-20000.00")}
    with pytest.raises(bf.ExpectationMismatchError) as ei:
        bf.assert_expected_delta(gmv_delta_por_escopo=ruim, keys_added=0, keys_removed=0)
    assert "nada foi escrito" in str(ei.value)


def test_tolerancia_de_centavos_nao_bloqueia():
    quase = {**DELTA_OK, ("apice", "2026-05"): Decimal("-23292.44")}
    bf.assert_expected_delta(gmv_delta_por_escopo=quase, keys_added=0, keys_removed=0)


@pytest.mark.parametrize("add,rem", [(1, 0), (0, 1), (3, 2)])
def test_qualquer_chave_adicionada_ou_removida_bloqueia(add, rem):
    # A operacao remove DUPLICATA: o conjunto de chaves nao pode mudar.
    with pytest.raises(bf.ExpectationMismatchError):
        bf.assert_expected_delta(gmv_delta_por_escopo=DELTA_OK,
                                 keys_added=add, keys_removed=rem)


def test_delta_em_escopo_nao_autorizado_bloqueia():
    intruso = {**DELTA_OK, ("kokeshi", "2026-08"): Decimal("0")}
    with pytest.raises(bf.ExpectationMismatchError) as ei:
        bf.assert_expected_delta(gmv_delta_por_escopo=intruso, keys_added=0, keys_removed=0)
    assert "nao autorizado" in str(ei.value)


def test_maturidade_esperada_apos_correcao_continua_acima_do_limiar():
    # 1,0782 -> 1,0361 e 1,0758 -> 1,0266 (medido no Gate ADMIN-SH-RO-1).
    assert bf.MATURATION_THRESHOLD_AFTER == Decimal("0.99")
    for depois in (Decimal("1.0361"), Decimal("1.0266")):
        assert depois > bf.MATURATION_THRESHOLD_AFTER


# ---------------------------------------------------------------------------
# Colunas explicitas e `ingested_at`
# ---------------------------------------------------------------------------

def test_ingested_at_existe_so_no_neon():
    assert bf.INGESTED_AT_COL in bf.insert_columns("neon")
    assert bf.INGESTED_AT_COL not in bf.insert_columns("local")
    assert bf.insert_columns("local") == bf.DATA_COLS


def test_sql_do_local_nunca_menciona_ingested_at():
    sql = bf.insert_sql("local")
    assert bf.INGESTED_AT_COL not in sql
    bf.assert_no_ingested_at_in_local_sql(sql)             # nao levanta


def test_trava_de_regressao_pega_ingested_at_no_local():
    with pytest.raises(bf.BackfillValidationError):
        bf.assert_no_ingested_at_in_local_sql(
            "INSERT INTO t (gmv, ingested_at) VALUES (:gmv, NOW())")


def test_ingested_at_no_neon_e_preenchido_pelo_banco_nao_pela_staging():
    # E' instante de PUBLICACAO. Se viesse da staging, republicar um mes
    # fechado carimbaria uma data que nao e' a da publicacao.
    sql = bf.insert_sql("neon")
    assert "NOW()" in sql
    assert ":ingested_at" not in sql


def test_nenhum_insert_usa_select_estrela():
    for target in ("local", "neon"):
        assert "*" not in bf.insert_sql(target)


def test_fingerprint_enumera_colunas_e_nao_usa_select_estrela():
    sql = bf.fingerprint_sql("marts.t", bf.DATA_COLS, "TRUE")
    assert "SELECT *" not in sql
    for c in bf.DATA_COLS:
        assert c in sql
    assert "md5(" in sql and "ORDER BY linha" in sql


# ---------------------------------------------------------------------------
# Backup duravel
# ---------------------------------------------------------------------------

def test_nome_do_backup_e_deterministico_e_carrega_o_destino():
    a = bf.backup_table_name("neon", "20260908_120000")
    b = bf.backup_table_name("neon", "20260908_120000")
    c = bf.backup_table_name("local", "20260908_120000")
    assert a == b
    assert a != c
    assert a.endswith("_bkp_neon_20260908_120000")


@pytest.mark.parametrize("stamp", ["2026-09-08", "; DROP TABLE x --", "", "2026090812000"])
def test_carimbo_invalido_no_backup_e_recusado(stamp):
    # O nome entra numa DDL; identificador nunca e' interpolado sem validacao.
    with pytest.raises(bf.BackfillValidationError):
        bf.backup_table_name("neon", stamp)


def test_retencao_do_backup_esta_documentada():
    assert isinstance(bf.BACKUP_RETENTION_DAYS, int)
    assert bf.BACKUP_RETENTION_DAYS >= 30


class BackupConn:
    """Conexao falsa que grava a ordem real das operacoes de backup."""

    def __init__(self, *, n_origem=7, n_copia=7, ck_origem="abc", ck_copia="abc"):
        self.n_origem, self.n_copia = n_origem, n_copia
        self.ck_origem, self.ck_copia = ck_origem, ck_copia
        self.eventos, self.sqls = [], []
        self._vezes = 0

    def begin(self):
        self.eventos.append("begin")
        conn = self

        class T:
            def commit(self_inner): conn.eventos.append("commit")
            def rollback(self_inner): conn.eventos.append("rollback")
        return T()

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sqls.append(sql)
        if "md5(" in sql:
            self._vezes += 1
            primeiro = self._vezes == 1
            return _Mapping({"n": self.n_origem if primeiro else self.n_copia,
                             "checksum": self.ck_origem if primeiro else self.ck_copia})
        return _Escalar(None)


def _executor(conn, target="neon"):
    return bf.ScopedReplaceExecutor(
        conn, target=target,
        clock=lambda: datetime.datetime(2026, 9, 8, 12, 0, 0))


def test_backup_e_commitado_antes_de_qualquer_mutacao():
    conn = BackupConn()
    ex = _executor(conn)
    info = ex.backup_scope(AUT)
    assert conn.eventos == ["begin", "commit"]
    assert ex.backup_committed is True
    assert info["rows"] == 7 and info["retention_days"] == bf.BACKUP_RETENTION_DAYS
    # nenhuma mutacao aconteceu na transacao do backup
    juntos = " ".join(conn.sqls).upper()
    assert "DELETE" not in juntos and "INSERT" not in juntos


def test_backup_cobre_exatamente_os_escopos_autorizados():
    conn = BackupConn()
    _executor(conn).backup_scope(AUT)
    create = [s for s in conn.sqls if s.upper().startswith("CREATE TABLE")][0]
    assert "IN ((:b0, :m0), (:b1, :m1))" in create
    assert "SELECT *" not in create
    for c in bf.DATA_COLS:
        assert c in create


def test_backup_com_checksum_divergente_bloqueia_e_desfaz():
    conn = BackupConn(ck_copia="OUTRO")
    with pytest.raises(bf.BackfillValidationError) as ei:
        _executor(conn).backup_scope(AUT)
    assert "nada foi apagado" in str(ei.value)
    assert conn.eventos == ["begin", "rollback"]


def test_backup_com_contagem_divergente_bloqueia():
    conn = BackupConn(n_copia=6)
    with pytest.raises(bf.BackfillValidationError):
        _executor(conn).backup_scope(AUT)
    assert "commit" not in conn.eventos


def test_delete_sem_backup_commitado_e_recusado():
    conn = BackupConn()
    ex = _executor(conn)
    ex.begin()
    with pytest.raises(bf.BackfillValidationError) as ei:
        ex.delete_scope(AUT)
    assert "backup COMMITADO" in str(ei.value)


def test_backup_do_local_e_do_neon_tem_colunas_diferentes():
    for target, esperado in (("local", bf.DATA_COLS),
                             ("neon", bf.DATA_COLS + (bf.INGESTED_AT_COL,))):
        conn = BackupConn()
        _executor(conn, target).backup_scope(AUT)
        create = [s for s in conn.sqls if s.upper().startswith("CREATE TABLE")][0]
        assert (bf.INGESTED_AT_COL in create) == (target == "neon")
        assert bf.insert_columns(target) == esperado


# ---------------------------------------------------------------------------
# Estados parciais entre os dois destinos
# ---------------------------------------------------------------------------

def test_os_tres_estados_parciais_tem_nome_e_acao():
    for nome, d in bf.PARTIAL_STATES.items():
        assert d["significado"] and d["acao"], nome
        assert "visivel_para_o_usuario" in d


def test_local_ok_neon_falhou_diz_que_a_torre_serve_o_dado_antigo():
    e = bf.describe_partial_state(local=bf.STATUS_OK, neon="FAILED")
    assert e["estado"] == "LOCAL_OK_NEON_FALHOU"
    assert "ANTIGO" in e["visivel_para_o_usuario"]


def test_neon_ok_local_falhou_e_declarado_proibido():
    e = bf.describe_partial_state(local="FAILED", neon=bf.STATUS_OK)
    assert e["estado"] == "NEON_OK_LOCAL_FALHOU"
    assert "PROIBIDO" in e["significado"]


def test_commit_indeterminado_manda_nao_repetir():
    e = bf.describe_partial_state(local="INDETERMINATE", neon="")
    assert e["estado"] == "COMMIT_INDETERMINADO"
    assert "NAO repetir" in e["acao"]


def test_nao_se_encena_atomicidade_distribuida():
    plano = bf.plan_local_then_neon(AUT)
    assert "NAO existe transacao distribuida" in plano["atomicidade"]
    assert "transacao distribuida ficticia entre os dois bancos" in plano["proibido"]


def test_neon_sozinho_e_recusado_por_contrato():
    with pytest.raises(bf.BackfillValidationError):
        bf.assert_not_neon_only(["neon"])
    bf.assert_not_neon_only(["local", "neon"])             # nao levanta


# ---------------------------------------------------------------------------
# Zero retry
# ---------------------------------------------------------------------------

def test_plano_declara_zero_retry():
    plano = bf.plan_local_then_neon(AUT)
    assert "ZERO retry" in plano["retry"]
    assert "retry automatico de qualquer etapa" in plano["proibido"]


def test_apply_nao_repete_nenhuma_etapa(monkeypatch):
    monkeypatch.setenv(bf.CONSENT_ENV, "1")

    class Contador:
        def __init__(self):
            self.n = {}
            self.backup_committed = False

        def _c(self, k): self.n[k] = self.n.get(k, 0) + 1

        def backup_scope(self, s): self._c("backup"); self.backup_committed = True; return {}
        def begin(self): self._c("begin")
        def delete_scope(self, s): self._c("delete")
        def insert_rows(self, r): self._c("insert"); raise RuntimeError("falha unica")
        def count_scope(self, s): self._c("count"); return 0
        def commit(self): self._c("commit")
        def rollback(self): self._c("rollback")

    ex = Contador()
    st = bf.Staging(rows=pd.DataFrame([{c: 1 for c in bf.DATA_COLS}]), scopes=AUT)
    code = bf.apply_scoped_replace(st, executor=ex)
    assert code == bf.EXIT_ROLLED_BACK
    for etapa in ("backup", "begin", "delete", "insert", "rollback"):
        assert ex.n[etapa] == 1, f"{etapa} rodou {ex.n[etapa]}x — houve retry"


def test_docstring_do_apply_declara_zero_retry():
    assert "ZERO RETRY" in bf.apply_scoped_replace.__doc__


# ---------------------------------------------------------------------------
# Rollback e commit indeterminado (com a nova ordem)
# ---------------------------------------------------------------------------

class Exec:
    def __init__(self, falha_em=None, falha_rollback=False, falha_commit=False):
        self.falha_em, self.falha_rollback, self.falha_commit = (
            falha_em, falha_rollback, falha_commit)
        self.calls = []
        self.backup_committed = False

    def _f(self, k):
        self.calls.append(k)
        if self.falha_em == k:
            raise RuntimeError(f"falha em {k}")

    def backup_scope(self, s): self._f("backup"); self.backup_committed = True; return {}
    def begin(self): self._f("begin")
    def delete_scope(self, s): self._f("delete")
    def insert_rows(self, r): self._f("insert")
    def count_scope(self, s): self._f("count"); return 1
    def commit(self):
        self.calls.append("commit")
        if self.falha_commit:
            raise RuntimeError("commit indeterminado")
    def rollback(self):
        self.calls.append("rollback")
        if self.falha_rollback:
            raise RuntimeError("rollback falhou")


def _st():
    return bf.Staging(rows=pd.DataFrame([{c: 1 for c in bf.DATA_COLS}]), scopes=AUT)


def test_falha_no_backup_impede_qualquer_mutacao(monkeypatch):
    monkeypatch.setenv(bf.CONSENT_ENV, "1")
    ex = Exec(falha_em="backup")
    with pytest.raises(RuntimeError):
        bf.apply_scoped_replace(_st(), executor=ex)
    # sem backup nao ha transacao de mutacao nem DELETE
    assert ex.calls == ["backup"]


def test_rollback_confirmado_vira_exit_rolled_back(monkeypatch):
    monkeypatch.setenv(bf.CONSENT_ENV, "1")
    ex = Exec(falha_em="delete")
    assert bf.apply_scoped_replace(_st(), executor=ex) == bf.EXIT_ROLLED_BACK
    assert ex.calls == ["backup", "begin", "delete", "rollback"]


def test_rollback_que_falha_vira_indeterminado(monkeypatch):
    monkeypatch.setenv(bf.CONSENT_ENV, "1")
    ex = Exec(falha_em="insert", falha_rollback=True)
    assert bf.apply_scoped_replace(_st(), executor=ex) == bf.EXIT_INDETERMINATE


def test_commit_que_falha_e_indeterminado_e_nunca_alega_rollback(monkeypatch):
    monkeypatch.setenv(bf.CONSENT_ENV, "1")
    ex = Exec(falha_commit=True)
    assert bf.apply_scoped_replace(_st(), executor=ex) == bf.EXIT_INDETERMINATE
    # depois de um commit possivelmente aplicado, NAO se tenta rollback
    assert "rollback" not in ex.calls


def test_apply_sem_consentimento_nao_toca_no_executor(monkeypatch):
    monkeypatch.delenv(bf.CONSENT_ENV, raising=False)
    ex = Exec()
    with pytest.raises(bf.BackfillValidationError):
        bf.apply_scoped_replace(_st(), executor=ex)
    assert ex.calls == []


def test_apply_com_escopo_nao_autorizado_nao_toca_no_executor(monkeypatch):
    monkeypatch.setenv(bf.CONSENT_ENV, "1")
    ex = Exec()
    st = bf.Staging(rows=pd.DataFrame([{c: 1 for c in bf.DATA_COLS}]),
                    scopes=[("kokeshi", "2026-08")])
    with pytest.raises(bf.ScopeNotAuthorizedError):
        bf.apply_scoped_replace(st, executor=ex)
    assert ex.calls == []


# ---------------------------------------------------------------------------
# CLI — dry-run continua read-only e --apply continua bloqueado
# ---------------------------------------------------------------------------

def test_apply_sem_source_root_e_recusado_sem_abrir_conexao(capsys, monkeypatch):
    """Gate SH-API-2E3: com o caminho real ligado, a porta que sobra aqui e a
    da ORIGEM. Nenhuma conexao pode ser aberta — a fabrica de engine e
    substituida por uma que explode se for chamada."""
    for k, v in ENV_OK.items():
        monkeypatch.setenv(k, v)

    def proibido(url):                                  # pragma: no cover
        raise AssertionError("abriu conexao gravavel antes dos guardrails")

    monkeypatch.setattr(bf, "_default_writable_engine", proibido)
    code = bf.main(["--apply", "--target", "neon",
                    "--scope", "apice:2026-05", "--scope", "barbours:2026-05"])
    assert code == bf.EXIT_VALIDATION_REFUSED
    err = capsys.readouterr().err
    assert "--source-root explicito" in err
    assert "nada foi escrito" in err


def test_apply_sem_target_e_recusado(capsys, monkeypatch):
    for k, v in ENV_OK.items():
        monkeypatch.setenv(k, v)
    code = bf.main(["--apply", "--scope", "apice:2026-05", "--scope", "barbours:2026-05"])
    assert code == bf.EXIT_VALIDATION_REFUSED
    assert "porta 4" in capsys.readouterr().err


def test_dry_run_continua_read_only_e_nao_toca_credencial_de_escrita(
        capsys, tmp_path, monkeypatch):
    """O modo offline segue sem banco NENHUM — e, com o Gate SH-API-2E1, sem
    tocar tambem nas variaveis de ESCRITA, mesmo quando elas estao no ambiente.
    O teste deixa as credenciais de escrita definidas de proposito: se o
    caminho de leitura passar a le-las, este teste quebra."""
    for k, v in ENV_OK.items():
        monkeypatch.setenv(k, v)

    def proibido(*a, **k):                     # pragma: no cover
        raise AssertionError("dry-run tentou resolver alvo/conexao/escrita")

    monkeypatch.setattr(bf, "resolve_target_url", proibido)
    monkeypatch.setattr(bf, "read_current_scope", proibido)
    monkeypatch.setattr(bf, "resolve_write_url", proibido)
    monkeypatch.setattr(bf.loader, "_find_xlsx", lambda d: [], raising=True)
    monkeypatch.setattr(bf.loader, "_plan_brand_snapshots", lambda b, f, **k: {}, raising=True)
    monkeypatch.setattr(bf.loader, "_load_brand", lambda b: pd.DataFrame(
        {"brand": [], "ref_month": pd.to_datetime([]), "sku_ref": [],
         "product_name": [], "variation_name": [], "qty": [], "subtotal": [],
         "status": [], "buyer_username": []}), raising=True)
    for marca in ("apice", "barbours"):
        (tmp_path / marca).mkdir()

    code = bf.main(["--offline-dry-run", "--source-root", str(tmp_path),
                    "--scope", "apice:2026-05", "--scope", "barbours:2026-05"])
    out = capsys.readouterr().out
    assert code == bf.EXIT_OK
    assert "NENHUM BANCO" in out
    assert "antes=" + bf.ND in out             # N/D, nunca 0
    assert "APPLY PRODUTIVO BLOQUEADO" in out


def _fonte_do_modulo() -> str:
    import inspect
    return inspect.getsource(bf)


def _codigo_executavel() -> str:
    """Fonte SEM comentario nem docstring. Assercao por substring sobre o
    arquivo inteiro e' armadilha conhecida deste repo: o comentario que EXPLICA
    por que algo foi proibido contem o proprio termo proibido."""
    import io
    import tokenize
    src = _fonte_do_modulo()
    saida = []
    anterior = None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and anterior in (
                None, tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
            continue                                   # docstring
        saida.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.NEWLINE):
            anterior = tok.type
        else:
            anterior = tok.type
    return " ".join(saida)


def test_o_extrator_de_codigo_funciona():
    # Sem esta prova os dois testes abaixo poderiam passar por um extrator que
    # devolve string vazia — verde e inutil.
    codigo = _codigo_executavel()
    assert "def apply_scoped_replace" in codigo
    assert "AUTHORIZED_SCOPES" in codigo
    assert "SELECT * entre local e Neon" not in codigo      # comentario removido


def test_dry_run_nunca_usa_a_credencial_de_escrita():
    fonte = _fonte_do_modulo()
    sep = chr(10) + "def "
    leitura = fonte.split("def read_current_scope")[1].split(sep)[0]
    assert "resolve_write_url" not in leitura
    assert "_WRITE_ENV" not in leitura


def test_modulo_nao_usa_select_estrela_no_codigo_executavel():
    assert "SELECT *" not in _codigo_executavel()
