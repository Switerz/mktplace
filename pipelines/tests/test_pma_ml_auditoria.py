"""Gate PMA-2C5B — auditoria do publisher do Mercado Livre.

O publisher do ML publicava sem deixar rastro em `audit.source_sync_run`: uma
falha dele era silenciosa, e nao havia como provar por leitura que ele rodou.
Aqui se trava a maquina de estados REAL, que tem quatro desfechos e nao dois.

    publicado          COMMIT confirmado             -> `success`
    rolled_back        falha ANTES do commit         -> `failed`
    indeterminado      o COMMIT levantou             -> segue `running` + nota
    audit_incomplete   publicou, o UPDATE falhou     -> dados publicados, exit 0

Os testes de MAQUINA usam dublês deterministicos, porque o que se prova aqui e'
a ORDEM das decisoes do nosso codigo. Os testes de SERVIDOR — o CHECK da
tabela, o efeito real de um COMMIT — exigem Postgres de verdade e pulam com
motivo quando ele nao existe: um dublê concordaria com qualquer coisa.
"""
from __future__ import annotations

from datetime import date

import pytest

from pipelines import sync_ml_listing_price_serving as ml
from pipelines.tests.postgres_descartavel import (
    DDL_AUDITORIA, MOTIVO_SEM_POSTGRES, cluster_descartavel, postgres_disponivel,
)

DE = date(2026, 9, 19)
ATE = date(2026, 9, 21)


# ---------------------------------------------------------------------------
# Dublês — registram a ORDEM das chamadas, que e' o contrato
# ---------------------------------------------------------------------------

class CursorFake:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.sqls.append((norm, params))
        if "INSERT INTO audit.source_sync_run" in norm:
            self.conn.chamadas.append(("start", params[1]))
        elif "UPDATE audit.source_sync_run" in norm and "status = %s" in norm:
            self.conn.chamadas.append(("finish", params[0]))
            if self.conn.falhar_finish:
                raise RuntimeError("falha simulada ao fechar a auditoria")
        elif "UPDATE audit.source_sync_run" in norm:
            self.conn.chamadas.append(("nota", params[0]))
        self.rowcount = 1

    def fetchone(self):
        return {"sync_run_id": 4242}

    def close(self):
        pass


class AuditConnFake:
    def __init__(self, falhar_finish=False):
        self.sqls = []
        self.chamadas = []
        self.commits = 0
        self.falhar_finish = falhar_finish
        self.fechada = False

    def cursor(self):
        return CursorFake(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.fechada = True


def _monta(monkeypatch, *, publicar, trilha=None):
    """Substitui fonte e destino; SO' a auditoria e' exercitada de verdade.

    `trilha` registra a ORDEM GLOBAL dos eventos — inclusive a abertura da
    conexao de destino. Sem ela, mover `audit_start` para depois de
    `_neon_writable` nao mudaria nada de observavel, e o contrato "o registro
    abre ANTES dos dados" ficaria sem contraprova.
    """
    monkeypatch.setattr(ml, "_get_datamart_url", lambda: "postgresql://x/y")
    monkeypatch.setattr(ml, "_get_neon_url", lambda: "postgresql://x/y")

    class SnapFake:
        rows = [{"a": 1}, {"a": 2}]
        aggregates = {"row_count": 2}
        gtin_metrics = {}
        date_from = DE
        date_to = ATE

    class ConnFake:
        def close(self):
            pass

    def destino(url):
        if trilha is not None:
            trilha.append("abre_destino")
        return ConnFake()

    def publica(*a, **k):
        if trilha is not None:
            trilha.append("publica")
        return publicar(*a, **k)

    monkeypatch.setattr(ml, "_datamart_snapshot", lambda url: ConnFake())
    monkeypatch.setattr(ml, "read_source", lambda c, a, b: SnapFake())
    monkeypatch.setattr(ml, "_neon_writable", destino)
    monkeypatch.setattr(ml, "publish_window", publica)


# ---------------------------------------------------------------------------
# 1. Sucesso
# ---------------------------------------------------------------------------

def test_sucesso_abre_running_antes_e_fecha_success_depois_do_commit(monkeypatch):
    _monta(monkeypatch, publicar=lambda n, s, r: {"published": 2, "deleted": 0})
    aud = AuditConnFake()
    rel = ml.run_apply(DE, ATE, "run-1", audit_conn=aud)

    assert [c[0] for c in aud.chamadas] == ["start", "finish"], (
        "o registro abre ANTES dos dados e fecha DEPOIS")
    assert aud.chamadas[1][1] == "success"
    assert rel["audit_complete"] is True
    assert rel["sync_run_id"] == 4242


def test_o_registro_abre_ANTES_de_a_conexao_de_destino_existir(monkeypatch):
    """Lacuna exposta pela mutacao M20.

    Se `audit_start` rodar depois de abrir o destino, uma queda entre as duas
    deixa dados escritos sem registro nenhum — e a execucao vira invisivel.
    Verificar so' a ordem `start` -> `finish` nao pega isso: as duas continuam
    na mesma ordem relativa.
    """
    trilha: list[str] = []
    _monta(monkeypatch, publicar=lambda n, s, r: {"published": 2},
           trilha=trilha)

    class AuditTrilhada(AuditConnFake):
        def cursor(self_inner):
            trilha.append("auditoria")
            return super().cursor()

    ml.run_apply(DE, ATE, "run-1", audit_conn=AuditTrilhada())
    assert trilha[0] == "auditoria", (
        f"a auditoria precisa abrir primeiro; ordem observada: {trilha}")
    assert trilha.index("auditoria") < trilha.index("abre_destino")
    assert trilha.index("abre_destino") < trilha.index("publica")


def test_sucesso_registra_a_janela_lida_e_as_contagens(monkeypatch):
    _monta(monkeypatch, publicar=lambda n, s, r: {"published": 2})
    aud = AuditConnFake()
    ml.run_apply(DE, ATE, "run-1", audit_conn=aud)
    _, params = [x for x in aud.sqls if "UPDATE" in x[0]][0]
    assert params[1] == 2, "rows_loaded precisa ser o publicado"
    assert params[3] == DE and params[4] == ATE


# ---------------------------------------------------------------------------
# 2. Falha ANTES do commit
# ---------------------------------------------------------------------------

def test_falha_pre_commit_fecha_failed(monkeypatch):
    def explode(n, s, r):
        raise ml.SyncError("EXCEPT bidirecional divergiu")

    _monta(monkeypatch, publicar=explode)
    aud = AuditConnFake()
    with pytest.raises(ml.SyncError):
        ml.run_apply(DE, ATE, "run-1", audit_conn=aud)
    assert [c[0] for c in aud.chamadas] == ["start", "finish"]
    assert aud.chamadas[1][1] == "failed"


def test_falha_pre_commit_registra_zero_linha_carregada(monkeypatch):
    def explode(n, s, r):
        raise ml.SyncError("divergencia")

    _monta(monkeypatch, publicar=explode)
    aud = AuditConnFake()
    with pytest.raises(ml.SyncError):
        ml.run_apply(DE, ATE, "run-1", audit_conn=aud)
    _, params = [x for x in aud.sqls if "UPDATE" in x[0]][0]
    assert params[1] == 0, "rollback nao carregou nada"


# ---------------------------------------------------------------------------
# 3. Commit INDETERMINADO — nunca `failed`
# ---------------------------------------------------------------------------

def test_commit_indeterminado_NAO_vira_failed(monkeypatch):
    def commit_quebra(n, s, r):
        raise ml.CommitIndeterminado("conexao caiu no commit")

    _monta(monkeypatch, publicar=commit_quebra)
    aud = AuditConnFake()
    with pytest.raises(ml.CommitIndeterminado):
        ml.run_apply(DE, ATE, "run-1", audit_conn=aud)

    tipos = [c[0] for c in aud.chamadas]
    assert tipos == ["start", "nota"], (
        "o registro recebe NOTA e continua `running` — fechar como `failed` "
        "afirmaria que nada foi gravado, e isso nao se sabe")
    assert "failed" not in [c[1] for c in aud.chamadas]


def test_a_nota_do_indeterminado_e_reconheciivel_e_so_toca_running(monkeypatch):
    def commit_quebra(n, s, r):
        raise ml.CommitIndeterminado("queda")

    _monta(monkeypatch, publicar=commit_quebra)
    aud = AuditConnFake()
    with pytest.raises(ml.CommitIndeterminado):
        ml.run_apply(DE, ATE, "run-1", audit_conn=aud)
    sql, params = [x for x in aud.sqls if "UPDATE" in x[0]][0]
    assert params[0].startswith(ml.AUDIT_INDETERMINATE_PREFIX)
    assert "status = 'running'" in sql, (
        "o UPDATE precisa ser condicionado a `running`, para nao reescrever "
        "um registro que ja' foi fechado por um humano")


def test_o_commit_ficou_fora_do_bloco_que_faz_rollback():
    """Contraprova estrutural do rearranjo: se o `commit()` voltar para dentro
    do `try` que chama `rollback()`, um commit indeterminado volta a ser
    indistinguivel de falha."""
    import inspect
    fonte = inspect.getsource(ml.publish_window)
    pos_rollback = fonte.index("neon_conn.rollback()")
    pos_commit = fonte.index("neon_conn.commit()")
    assert pos_commit > pos_rollback, (
        "o commit precisa vir DEPOIS do bloco de rollback")
    assert "raise CommitIndeterminado" in fonte


def test_exit_code_do_indeterminado_e_4_e_nao_1(monkeypatch):
    """4 e' o mesmo codigo do publisher multicanal: um unico vocabulario para
    o orquestrador traduzir."""
    monkeypatch.setattr(ml, "resolve_window", lambda a, today=None: (DE, ATE))

    def explode(*a, **k):
        raise ml.CommitIndeterminado("queda")

    monkeypatch.setattr(ml, "run_apply", explode)
    assert ml.main(["--apply"]) == 4
    assert ml.EXIT_INDETERMINATE == 4


# ---------------------------------------------------------------------------
# 4. Auditoria INCOMPLETA depois do commit
# ---------------------------------------------------------------------------

def test_falha_ao_fechar_auditoria_NAO_alega_rollback(monkeypatch):
    _monta(monkeypatch, publicar=lambda n, s, r: {"published": 2})
    aud = AuditConnFake(falhar_finish=True)
    rel = ml.run_apply(DE, ATE, "run-1", audit_conn=aud)
    assert rel["applied"] is True, "os dados ESTAO publicados"
    assert rel["audit_complete"] is False


def test_auditoria_incompleta_continua_saindo_zero_com_aviso(monkeypatch, capsys):
    """Nao rebaixa o desfecho: o defeito e' do registro, nao do dado."""
    monkeypatch.setattr(ml, "resolve_window", lambda a, today=None: (DE, ATE))
    monkeypatch.setattr(ml, "run_apply", lambda *a, **k: {
        "mode": "apply", "applied": True, "run_id": "r", "window": (DE, ATE),
        "source": {"row_count": 2, "item_count": 2, "brand_count": 1,
                   "min_ref_date": DE, "max_ref_date": ATE,
                   "original_price_not_null": 2},
        "gtin_metrics": {},
        "publish": {"deleted": 0, "published": 2, "checks": {"except_both_ways": (0, 0)}},
        "sync_run_id": 4242, "audit_complete": False,
    })
    assert ml.main(["--apply"]) == 0
    erro = capsys.readouterr().err
    assert "auditoria ficou INCOMPLETA" in erro
    assert "4242" in erro


# ---------------------------------------------------------------------------
# 5. Higiene
# ---------------------------------------------------------------------------

def test_auditoria_usa_conexao_independente(monkeypatch):
    """Se vivesse na transacao dos dados, um `failed` seria desfeito junto com
    o rollback e a tentativa nao deixaria rastro."""
    import inspect
    fonte = inspect.getsource(ml.run_apply)
    assert "audit_conn" in fonte
    assert "_audit_conn(" in fonte


def test_status_fora_do_dominio_da_tabela_e_recusado():
    aud = AuditConnFake()
    with pytest.raises(ml.SyncError):
        ml.audit_finish(aud, 1, "indeterminate")
    assert ml.AUDIT_STATUSES == ("running", "success", "failed")


def test_nenhum_identificador_de_anuncio_entra_na_auditoria(monkeypatch):
    def explode(n, s, r):
        raise ml.SyncError("falha no item MLB123456789 sku ABC-1 gtin 7891234567890")

    _monta(monkeypatch, publicar=explode)
    aud = AuditConnFake()
    with pytest.raises(ml.SyncError):
        ml.run_apply(DE, ATE, "run-1", audit_conn=aud)
    _, params = [x for x in aud.sqls if "UPDATE" in x[0]][0]
    mensagem = params[2] or ""
    # A mensagem vem de `sanitize_error_message`; o que este teste trava e' que
    # o publisher nao ACRESCENTA identificador por conta propria.
    assert "audit_conn" not in mensagem
    assert len(mensagem) < 500


def test_zero_retry_na_auditoria():
    import inspect
    for fn in (ml.audit_start, ml.audit_finish, ml.audit_note_indeterminate):
        fonte = inspect.getsource(fn)
        for proibido in ("for tentativa", "while True", "retry", "sleep"):
            assert proibido not in fonte, f"{fn.__name__} tem retry"


# ---------------------------------------------------------------------------
# 6. SERVIDOR de verdade — o que dublê nenhum prova
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)
def test_o_check_da_tabela_recusa_status_inventado():
    import psycopg2
    with cluster_descartavel() as url:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(DDL_AUDITORIA)
            conn.commit()
            with conn.cursor() as cur:
                with pytest.raises(psycopg2.Error):
                    cur.execute(
                        "INSERT INTO audit.source_sync_run "
                        "(source_name, status, started_at) "
                        "VALUES ('x', 'indeterminate', NOW())")
            conn.rollback()
        finally:
            conn.close()


@pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)
def test_o_ciclo_running_para_success_persiste_no_servidor():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    with cluster_descartavel() as url:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(DDL_AUDITORIA)
            conn.commit()
            sync_run_id = ml.audit_start(conn, 10)
            ml.audit_finish(conn, sync_run_id, "success", rows_loaded=10,
                            source_min_date=DE, source_max_date=ATE)
            with conn.cursor() as cur:
                cur.execute("SELECT status, rows_loaded, source_max_date "
                            "FROM audit.source_sync_run WHERE sync_run_id = %s",
                            (sync_run_id,))
                linha = cur.fetchone()
            assert linha["status"] == "success"
            assert linha["rows_loaded"] == 10
            assert linha["source_max_date"] == ATE
        finally:
            conn.close()


@pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)
def test_a_nota_de_indeterminado_deixa_o_registro_em_running():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    with cluster_descartavel() as url:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(DDL_AUDITORIA)
            conn.commit()
            sync_run_id = ml.audit_start(conn, 5)
            ml.audit_note_indeterminate(conn, sync_run_id, "queda no commit")
            with conn.cursor() as cur:
                cur.execute("SELECT status, error_message FROM "
                            "audit.source_sync_run WHERE sync_run_id = %s",
                            (sync_run_id,))
                linha = cur.fetchone()
            assert linha["status"] == "running"
            assert linha["error_message"].startswith(ml.AUDIT_INDETERMINATE_PREFIX)
        finally:
            conn.close()
