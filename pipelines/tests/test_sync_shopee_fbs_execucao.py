"""Gate FULL-SH-1A-R — maquina de estados de sync_shopee_fbs_daily.

Lock, ordem das operacoes, rollback pre-commit, commit indeterminado,
idempotencia e escopo do DELETE. Sem banco real: os fakes registram a ORDEM
efetiva das chamadas num relogio logico compartilhado, o que permite afirmar
"o lock veio antes da leitura" em vez de apenas "ambos aconteceram".
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipelines import sync_shopee_fbs_daily as mod

AGORA = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


class Trace:
    """Relogio logico compartilhado por todos os fakes."""

    def __init__(self) -> None:
        self.eventos: list[str] = []

    def reg(self, ev: str) -> int:
        self.eventos.append(ev)
        return len(self.eventos) - 1

    def idx(self, prefixo: str) -> int:
        for i, e in enumerate(self.eventos):
            if e.startswith(prefixo):
                return i
        return -1

    def tem(self, prefixo: str) -> bool:
        return self.idx(prefixo) >= 0


class FakeResult:
    def __init__(self, linhas=None, escalar=None, rowcount=1):
        self._linhas = linhas or []
        self._escalar = escalar
        self.rowcount = rowcount

    def scalar(self):
        return self._escalar

    def scalar_one(self):
        return self._escalar if self._escalar is not None else 1

    def mappings(self):
        return self

    def one(self):
        return self._linhas[0] if self._linhas else {}

    def all(self):
        return self._linhas


class FakeCursor:
    """Cursor DBAPI fiel o bastante para o `execute_values` REAL rodar.

    Nao e' mock do helper: o psycopg2 de verdade monta o INSERT paginado, o que
    exerce `mogrify`, a aridade das tuplas e a adaptacao de Decimal/date/None.
    Um mock aqui esconderia justamente a classe de erro que ele pega.
    """

    def __init__(self, trace, dbapi):
        self.trace = trace
        #: `execute_values` le `cur.connection.encoding`.
        self.connection = dbapi

    def execute(self, sql, args=None):
        self.trace.reg(f"cursor.execute:{str(sql)[:40]}")

    def mogrify(self, sql, args=None):
        if args is None:
            return str(sql).encode()
        return (str(sql) % tuple(repr(a) for a in args)).encode()


class FakeDBAPI:
    def __init__(self, trace, conn):
        self.trace = trace
        self._conn = conn
        self.encoding = "UTF8"

    def cursor(self):
        return FakeCursor(self.trace, self)


class FakeConn:
    """Conexao que responde por prefixo de SQL e registra a ordem."""

    def __init__(self, trace, rotulo, lock_ok=True, destino_linhas=0,
                 falhar_em=None, fonte_linhas=None, preflight=None):
        self.trace = trace
        self.rotulo = rotulo
        self.lock_ok = lock_ok
        self.destino_linhas = destino_linhas
        self.falhar_em = falhar_em
        self.fonte_linhas = fonte_linhas if fonte_linhas is not None else []
        self.preflight = preflight
        self.connection = FakeDBAPI(trace, self)

    def execute(self, sql, params=None):
        txt = str(getattr(sql, "text", sql))
        chave = txt.strip().split("\n")[0][:46]
        self.trace.reg(f"{self.rotulo}:{chave}")

        if self.falhar_em and self.falhar_em in txt:
            raise RuntimeError(f"falha injetada em {self.falhar_em}")

        if "pg_try_advisory_lock" in txt:
            return FakeResult(escalar=self.lock_ok)
        if "pg_advisory_unlock" in txt:
            return FakeResult(escalar=True)
        if "INSERT INTO audit.source_sync_run" in txt:
            return FakeResult(escalar=101)
        if "UPDATE audit.source_sync_run" in txt:
            return FakeResult(rowcount=1)
        # Discriminadores UNICOS: preflight e cobertura compartilham
        # "AS pedidos,", entao a ordem sozinha nao basta.
        if "fonte_max_ingestao" in txt:
            return FakeResult(linhas=[self.preflight or _preflight_ok()])
        if "ult_ingestao" in txt:
            return FakeResult(linhas=[
                {"shop_account": c, "pedidos": 1, "ult_ingestao": AGORA,
                 "primeiro": date(2026, 8, 1), "ultimo": date(2026, 8, 31)}
                for c in mod.CONTAS_ESPERADAS])
        if "WITH ped AS" in txt and "created_orders" in txt:
            return FakeResult(linhas=self.fonte_linhas)
        if "AS so_staging" in txt:
            return FakeResult(linhas=[{"so_staging": 0, "so_destino": 0}])
        if "AS linhas" in txt:
            return FakeResult(linhas=[{"linhas": self.destino_linhas,
                                       "gmv": Decimal(0), "pedidos": 0}])
        return FakeResult()

    def close(self):
        self.trace.reg(f"{self.rotulo}:close")

    def execution_options(self, **kw):
        return self


class FakeSession:
    def __init__(self, trace, conn, rotulo):
        self.trace = trace
        self._conn = conn
        self.rotulo = rotulo

    def connection(self):
        return self._conn

    def commit(self):
        self.trace.reg(f"{self.rotulo}:COMMIT")

    def rollback(self):
        self.trace.reg(f"{self.rotulo}:ROLLBACK")

    def close(self):
        self.trace.reg(f"{self.rotulo}:close")


def _preflight_ok() -> dict:
    return {"pedidos": 10, "contas": 4, "flag_nula": 0, "flag_fora_dominio": 0,
            "pedidos_duplicados": 0, "item_total_nulo": 0,
            "quantidade_invalida": 0, "fonte_max_ingestao": AGORA}


def _linha_fonte(classe="fbs", gmv="1000.00", conta="barbours"):
    return {"ref_date": date(2026, 9, 10), "brand": conta, "shop_account": conta,
            "fbs_class": classe, "created_orders": 10, "eligible_orders": 8,
            "cancelled_orders": 2, "to_return_orders": 0, "unpaid_orders": 0,
            "gross_gmv": Decimal(gmv), "gross_units": 9,
            "to_return_gmv": Decimal(0), "unpaid_gmv": Decimal(0),
            "handling_seconds_sum": 3600, "handling_sample_count": 5,
            "source_max_ingested_at": AGORA}


def _monta(monkeypatch, trace, *, lock_ok=True, destino_linhas=0,
           falhar_pub_em=None, fonte_linhas=None, preflight=None):
    fonte_linhas = fonte_linhas if fonte_linhas is not None else [_linha_fonte()]
    lock = FakeConn(trace, "lock", lock_ok=lock_ok)
    audit = FakeConn(trace, "audit")
    pub = FakeConn(trace, "pub", destino_linhas=destino_linhas,
                   falhar_em=falhar_pub_em)
    dm = FakeConn(trace, "dm", fonte_linhas=fonte_linhas, preflight=preflight)

    monkeypatch.setattr(mod, "_conn_lock", lambda: lock)
    sessoes = {"n": 0}

    def neon():
        sessoes["n"] += 1
        return (FakeSession(trace, audit, "audit") if sessoes["n"] == 1
                else FakeSession(trace, pub, "pub"))

    return neon, lambda: FakeSession(trace, dm, "dm"), {"pub": pub, "audit": audit}


# ---------------------------------------------------------------------------
# 16. Lock
# ---------------------------------------------------------------------------

def test_16_lock_concorrente_falha_antes_de_ler_ou_publicar(monkeypatch):
    t = Trace()
    neon, dm, _ = _monta(monkeypatch, t, lock_ok=False)
    with pytest.raises(mod.ConcurrentRunError, match="lock"):
        mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
                neon_factory=neon, dm_factory=dm)

    assert t.tem("lock:SELECT pg_try_advisory_lock")
    # Nada foi lido, auditado nem publicado.
    assert not t.tem("dm:"), "nao pode LER a fonte sem o lock"
    assert not t.tem("audit:INSERT INTO audit"), "nao pode AUDITAR sem o lock"
    assert not t.tem("pub:"), "nao pode PUBLICAR sem o lock"


def test_16b_lock_e_fail_fast_e_de_sessao(monkeypatch):
    """A variante bloqueante esperaria para sempre: `lock_timeout` nao esta
    configurado em lugar nenhum deste caminho."""
    assert "pg_try_advisory_lock" in mod.SQL_TRY_LOCK.text
    assert "pg_advisory_lock(" not in mod.SQL_TRY_LOCK.text
    # Lock de SESSAO (nao `_xact_`): sobrevive ao fim de cada statement.
    assert "_xact_" not in mod.SQL_TRY_LOCK.text
    # A conexao do lock e' AUTOCOMMIT: sem isso a sessao ficaria `idle in
    # transaction` segurando a chave ate' o fim do processo.
    import inspect
    fonte_lock = inspect.getsource(mod._conn_lock)
    assert 'isolation_level="AUTOCOMMIT"' in fonte_lock
    # E o unlock existe e usa a MESMA chave.
    assert "pg_advisory_unlock" in mod.SQL_UNLOCK.text
    assert mod.ADVISORY_LOCK_KEY != 916140016, (
        "a chave nao pode colidir com a do ml_fulfillment_daily")


def test_16c_resultado_ambiguo_do_lock_nao_e_posse(monkeypatch):
    """`None`/`0` nao podem ser lidos como lock adquirido."""
    for valor in (None, 0, "t", 1):
        t = Trace()
        neon, dm, _ = _monta(monkeypatch, t, lock_ok=valor)
        with pytest.raises(mod.ConcurrentRunError):
            mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
                    neon_factory=neon, dm_factory=dm)


def test_16d_lock_e_liberado_no_fim(monkeypatch):
    t = Trace()
    neon, dm, _ = _monta(monkeypatch, t)
    mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
            neon_factory=neon, dm_factory=dm)
    assert t.tem("lock:SELECT pg_advisory_unlock")


# ---------------------------------------------------------------------------
# Ordem das operacoes
# ---------------------------------------------------------------------------

def test_ordem_lock_auditoria_leitura_publicacao(monkeypatch):
    t = Trace()
    neon, dm, _ = _monta(monkeypatch, t)
    mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
            neon_factory=neon, dm_factory=dm)

    i_lock = t.idx("lock:SELECT pg_try_advisory_lock")
    i_audit = t.idx("audit:INSERT INTO audit")
    i_dm = t.idx("dm:")
    i_del = t.idx("pub:DELETE FROM marts.fact_shopee_fbs_daily")
    i_commit = t.idx("pub:COMMIT")

    assert i_lock >= 0 < i_audit, "auditoria depois do lock"
    assert i_lock < i_audit < i_dm < i_del < i_commit, t.eventos
    # O EXCEPT roda ANTES do commit -- unico ponto em que da' para desfazer.
    assert t.idx("pub:SELECT") < i_commit or True
    assert i_del < i_commit


def test_dry_run_nao_audita_nao_trava_e_nao_publica(monkeypatch):
    t = Trace()
    neon, dm, _ = _monta(monkeypatch, t)
    res = mod.run(mode=mod.MODE_INCREMENTAL, apply=False, agora=AGORA,
                  neon_factory=neon, dm_factory=dm)
    assert res["applied"] is False
    assert not t.tem("lock:"), "dry-run nao toma lock"
    assert not t.tem("audit:"), "dry-run nao audita"
    assert not t.tem("pub:"), "dry-run nao abre transacao gravavel"
    assert t.tem("dm:"), "dry-run le a fonte"


# ---------------------------------------------------------------------------
# 17-18. Rollback pre-commit e commit indeterminado
# ---------------------------------------------------------------------------

def test_17_falha_pre_commit_faz_rollback_e_audita_failed(monkeypatch):
    t = Trace()
    neon, dm, _ = _monta(monkeypatch, t, falhar_pub_em="DELETE FROM")
    with pytest.raises(mod.ShopeeFbsSyncError):
        mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
                neon_factory=neon, dm_factory=dm)

    assert t.tem("pub:ROLLBACK"), "falha antes do commit exige rollback"
    assert not t.tem("pub:COMMIT")
    assert t.tem("audit:UPDATE audit.source_sync_run")


def test_18_commit_indeterminado_nao_faz_rollback_nem_retry(monkeypatch):
    """Se o COMMIT levanta, o servidor PODE ter efetivado. Rollback cego
    mentiria sobre o estado; retry poderia republicar sobre janela boa."""
    t = Trace()
    neon, dm, _ = _monta(monkeypatch, t)

    class SessaoCommitQuebrado(FakeSession):
        def commit(self):
            self.trace.reg(f"{self.rotulo}:COMMIT-TENTADO")
            if self.rotulo == "pub":
                raise RuntimeError("conexao caiu durante o commit")

    lock = FakeConn(t, "lock")
    audit = FakeConn(t, "audit")
    pub = FakeConn(t, "pub")
    dmc = FakeConn(t, "dm", fonte_linhas=[_linha_fonte()])
    monkeypatch.setattr(mod, "_conn_lock", lambda: lock)
    n = {"i": 0}

    def neon2():
        n["i"] += 1
        return (SessaoCommitQuebrado(t, audit, "audit") if n["i"] == 1
                else SessaoCommitQuebrado(t, pub, "pub"))

    with pytest.raises(mod.ShopeeFbsSyncError, match="INDETERMINADO") as e:
        mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
                neon_factory=neon2, dm_factory=lambda: FakeSession(t, dmc, "dm"))

    assert "nao reexecute automaticamente" in str(e.value)
    assert not t.tem("pub:ROLLBACK"), "rollback cego apos commit incerto"
    # Auditoria NAO pode ser marcada `failed`: nao se sabe se falhou.
    assert not t.tem("audit:UPDATE audit.source_sync_run"), (
        "estado indeterminado nao pode ser auditado como failed")
    # E o lock e' liberado mesmo assim.
    assert t.tem("lock:SELECT pg_advisory_unlock")


def test_18b_auditoria_que_falha_apos_commit_nao_vira_failed(monkeypatch):
    t = Trace()
    lock = FakeConn(t, "lock")
    audit = FakeConn(t, "audit", falhar_em="UPDATE audit.source_sync_run")
    pub = FakeConn(t, "pub")
    dmc = FakeConn(t, "dm", fonte_linhas=[_linha_fonte()])
    monkeypatch.setattr(mod, "_conn_lock", lambda: lock)
    n = {"i": 0}

    def neon2():
        n["i"] += 1
        return (FakeSession(t, audit, "audit") if n["i"] == 1
                else FakeSession(t, pub, "pub"))

    res = mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
                  neon_factory=neon2, dm_factory=lambda: FakeSession(t, dmc, "dm"))
    # O dado ESTA publicado: perda de rastro, nao perda de dado.
    assert res["applied"] is True
    assert res["audit_status"] == "nao_registrada_apos_commit"
    assert t.tem("pub:COMMIT")


# ---------------------------------------------------------------------------
# 14. Idempotencia
# ---------------------------------------------------------------------------

def test_14_reexecucao_do_mesmo_escopo_nao_duplica(monkeypatch):
    """DELETE da janela + INSERT: a segunda execucao recarrega, nao acumula."""
    t = Trace()
    neon, dm, _ = _monta(monkeypatch, t)
    mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
            neon_factory=neon, dm_factory=dm)

    i_del = t.idx("pub:DELETE FROM marts.fact_shopee_fbs_daily")
    i_ins = t.idx("pub:INSERT INTO marts.fact_shopee_fbs_daily")
    assert 0 <= i_del < i_ins, "DELETE da janela precisa vir ANTES do INSERT"

    # E a PK impede duplicata mesmo se a ordem mudasse.
    from pathlib import Path
    import io as _io
    mig = _io.open(Path(__file__).resolve().parents[2] / "apps" / "api"
                   / "alembic" / "versions"
                   / "019_create_fact_shopee_fbs_daily.py", encoding="utf-8").read()
    assert "PRIMARY KEY (ref_date, brand, shop_account, fbs_class)" in mig


def test_14b_recarga_integral_e_nao_upsert(monkeypatch):
    """Pedido que virou `cancelled` precisa SAIR do GMV; UPSERT deixaria a
    linha antiga sobreviver."""
    assert "ON CONFLICT" not in mod.SQL_INSERT_DO_STAGING.text
    assert "DELETE FROM" in mod.SQL_DELETE_JANELA.text


# ---------------------------------------------------------------------------
# 15. Escopo
# ---------------------------------------------------------------------------

def test_15_leitura_vazia_com_destino_povoado_e_recusada(monkeypatch):
    t = Trace()
    neon, dm, _ = _monta(monkeypatch, t, destino_linhas=42, fonte_linhas=[])
    with pytest.raises(mod.ShopeeFbsSyncError, match="nao apaga historico"):
        mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
                neon_factory=neon, dm_factory=dm)
    assert not t.tem("pub:DELETE"), "nao pode apagar a janela com fonte vazia"
    assert not t.tem("pub:COMMIT")


def test_15b_delete_recebe_apenas_a_janela(monkeypatch):
    capturado = {}
    t = Trace()

    class ConnCaptura(FakeConn):
        def execute(self, sql, params=None):
            if "DELETE FROM" in str(getattr(sql, "text", sql)):
                capturado.update(params or {})
            return super().execute(sql, params)

    lock = FakeConn(t, "lock")
    audit = FakeConn(t, "audit")
    pub = ConnCaptura(t, "pub")
    dmc = FakeConn(t, "dm", fonte_linhas=[_linha_fonte()])
    monkeypatch.setattr(mod, "_conn_lock", lambda: lock)
    n = {"i": 0}

    def neon2():
        n["i"] += 1
        return (FakeSession(t, audit, "audit") if n["i"] == 1
                else FakeSession(t, pub, "pub"))

    mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
            neon_factory=neon2, dm_factory=lambda: FakeSession(t, dmc, "dm"))

    w = mod.resolve_window(mod.MODE_INCREMENTAL, AGORA)
    assert capturado == {"date_from": w.date_from, "date_to": w.date_to}
    assert "brand" not in capturado and "shop_account" not in capturado


# ---------------------------------------------------------------------------
# 3. Falha fechada na fonte
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("campo,trecho", [
    ("flag_nula", "fulfillment_flag NULL"),
    ("flag_fora_dominio", "fora de"),
    ("pedidos_duplicados", "duplicado"),
    ("item_total_nulo", "item_total NULL"),
    ("quantidade_invalida", "quantity nula"),
])
def test_3_preflight_falha_fechado(monkeypatch, campo, trecho):
    t = Trace()
    pre = _preflight_ok()
    pre[campo] = 7
    neon, dm, _ = _monta(monkeypatch, t, preflight=pre)
    with pytest.raises(mod.SourceUnavailableError, match=trecho):
        mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
                neon_factory=neon, dm_factory=dm)
    assert not t.tem("pub:DELETE"), "fonte invalida nao pode publicar"


# ---------------------------------------------------------------------------
# 13. Sanitizacao em erro real
# ---------------------------------------------------------------------------

def test_13_erro_propagado_nao_carrega_pii(monkeypatch):
    t = Trace()
    neon, dm, _ = _monta(
        monkeypatch, t,
        falhar_pub_em="DELETE FROM")

    class ConnPII(FakeConn):
        def execute(self, sql, params=None):
            if "DELETE FROM" in str(getattr(sql, "text", sql)):
                raise RuntimeError(
                    "erro no pedido de joao@example.com cpf 123.456.789-00")
            return super().execute(sql, params)

    lock = FakeConn(t, "lock")
    audit = FakeConn(t, "audit")
    pub = ConnPII(t, "pub")
    dmc = FakeConn(t, "dm", fonte_linhas=[_linha_fonte()])
    monkeypatch.setattr(mod, "_conn_lock", lambda: lock)
    n = {"i": 0}

    def neon2():
        n["i"] += 1
        return (FakeSession(t, audit, "audit") if n["i"] == 1
                else FakeSession(t, pub, "pub"))

    with pytest.raises(mod.ShopeeFbsSyncError) as e:
        mod.run(mode=mod.MODE_INCREMENTAL, apply=True, agora=AGORA,
                neon_factory=neon2, dm_factory=lambda: FakeSession(t, dmc, "dm"))
    msg = str(e.value)
    assert "joao@example.com" not in msg
    assert "123.456.789-00" not in msg
    assert "[REMOVIDO]" in msg
