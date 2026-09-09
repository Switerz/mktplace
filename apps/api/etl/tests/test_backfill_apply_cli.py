"""
Gate SH-API-2E3 — a CLI `--apply` percorrida de ponta a ponta com fakes.

Todos os testes entram por `bf.main(...)` de verdade. Nenhuma conexao real e
aberta: `_default_writable_engine` e substituido por uma fabrica falsa que
registra tudo — e, nos testes de guardrail, por uma que EXPLODE se for chamada,
provando que nenhum I/O gravavel acontece antes das portas.

O que estes testes protegem, em uma frase: uma escrita so' pode acontecer
depois de dez portas, numa unica conexao, com o lock liberado no fim, sem
retry, e sem que nenhuma mensagem vaze DSN, SQL ou parametro.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

from etl import backfill_shopee_products as bf

AUT_ARGS = ["--scope", "apice:2026-05", "--scope", "barbours:2026-05"]

ENV_LOCAL = {
    bf.CONSENT_ENV: "1",
    "BACKFILL_LOCAL_RW_URL": "postgresql://gravador@localhost:5432/mkt",
    "BACKFILL_LOCAL_RO_URL": "postgresql://leitor@localhost:5432/mkt",
    "BACKFILL_LOCAL_EXPECT_DB": "mktplace_control",
}
ENV_NEON = {
    bf.CONSENT_ENV: "1",
    "BACKFILL_NEON_RW_URL": "postgresql://gravador@remoto.example:5432/neondb",
    "BACKFILL_NEON_RO_URL": "postgresql://leitor@remoto.example:5432/neondb",
    "BACKFILL_NEON_EXPECT_DB": "neondb",
}


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, escalar=None, linha=None):
        self._e, self._l = escalar, linha

    def scalar(self): return self._e
    def mappings(self): return self
    def first(self): return self._l


class FakeTrans:
    def __init__(self, conn): self.conn = conn

    def commit(self):
        self.conn.eventos.append("trans.commit")
        self.conn._em_transacao = False

    def rollback(self):
        self.conn.eventos.append("trans.rollback")
        self.conn._em_transacao = False


class FakeConn:
    """Conexao gravavel falsa que MODELA O SQLAlchemy 2.0.

    Gate SH-API-2E3-H1: a versao anterior deste fake tinha `begin()`
    permissivo — devolvia transacao nova sempre, sem reclamar. Os 43 testes
    passaram e o runtime falhou SEMPRE, porque no SQLAlchemy 2.0 real:

      - o primeiro `execute()` faz AUTOBEGIN;
      - `begin()` com transacao ativa levanta `InvalidRequestError`.

    Agora o fake reproduz as duas regras. Um fake mais permissivo que o
    original nao e' um dublê: e' um teste que mente.
    """

    def __init__(self, *, db="neondb", tem_produtos=1, in_recovery=False,
                 tx_readonly="off", privs=True, ssl=True, lock=True,
                 backup_confere=True, rollback_falha=False):
        self.cfg = locals()
        self.eventos, self.sqls = [], []
        self.fechada = False
        self.connection = type("C", (), {"info": type("I", (), {"ssl_in_use": ssl})()})()
        self._md5 = 0
        self._em_transacao = False
        self._rollback_falha = rollback_falha
        # `count_scope` tem de refletir o que foi REALMENTE inserido: um numero
        # fixo faria a validacao pos-insert de `apply_scoped_replace` reprovar
        # (ou aprovar) por acidente, e o teste mediria o fake, nao o codigo.
        self.inseridas = 0
        self.linhas_backup = 0

    # --- semantica de transacao do SQLAlchemy 2.0 -------------------------
    def in_transaction(self):
        return self._em_transacao

    def begin(self):
        if self._em_transacao:
            # Mesma classe e mesma mensagem do SQLAlchemy real.
            from sqlalchemy.exc import InvalidRequestError
            raise InvalidRequestError(
                "This connection has already initialized a SQLAlchemy "
                "Transaction() object via begin() or autobegin; can't call "
                "begin() here unless rollback() or commit() is called first.")
        self.eventos.append("conn.begin")
        self._em_transacao = True
        return FakeTrans(self)

    def rollback(self):
        """Rollback no nivel da CONEXAO — e' o que encerra o autobegin."""
        self.eventos.append("conn.rollback")
        if self._rollback_falha:
            raise RuntimeError("rollback do preflight falhou")
        self._em_transacao = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sqls.append(sql)
        if not self._em_transacao:
            self.eventos.append("autobegin")
            self._em_transacao = True
        c = self.cfg
        if "pg_try_advisory_lock" in sql:
            self.eventos.append("lock.acquire")
            return _Res(escalar=c["lock"])
        if "pg_advisory_unlock" in sql:
            self.eventos.append("lock.release")
            return _Res(escalar=True)
        if "current_database" in sql:
            return _Res(linha={"db": c["db"], "tem_produtos": c["tem_produtos"],
                               "tem_serving": 1})
        if "pg_is_in_recovery" in sql:
            return _Res(linha={"in_recovery": c["in_recovery"],
                               "tx_readonly": c["tx_readonly"],
                               "pode_insert": c["privs"], "pode_delete": c["privs"],
                               "pode_criar_backup": c["privs"]})
        if "md5(" in sql:
            self._md5 += 1
            primeiro = self._md5 % 2 == 1
            ck = "abc" if (primeiro or c["backup_confere"]) else "DIVERGE"
            return _Res(linha={"n": self.linhas_backup, "checksum": ck})
        if sql.strip().upper().startswith("CREATE TABLE"):
            self.eventos.append("backup.create")
            return _Res()
        if sql.strip().upper().startswith("DELETE"):
            self.eventos.append("mutacao.delete")
            return _Res()
        if sql.strip().upper().startswith("INSERT"):
            self.eventos.append("mutacao.insert")
            self.inseridas = len(params) if isinstance(params, list) else 1
            return _Res()
        if sql.strip().upper().startswith("SELECT COUNT"):
            return _Res(escalar=self.inseridas)
        return _Res(escalar=None)

    def close(self):
        self.fechada = True
        self.eventos.append("conn.close")


class FakeEngine:
    def __init__(self, conn, url):
        self.conn, self.url = conn, url
        self.conexoes = 0
        self.descartada = False

    def connect(self):
        self.conexoes += 1
        return self.conn

    def dispose(self):
        self.descartada = True


class Espiao:
    """Fabrica de engine que registra a URL recebida e conta as aberturas."""

    def __init__(self, conn):
        self.conn, self.urls, self.engines = conn, [], []

    def __call__(self, url):
        self.urls.append(url)
        eng = FakeEngine(self.conn, url)
        self.engines.append(eng)
        return eng


@pytest.fixture()
def raiz(tmp_path, monkeypatch):
    """Raiz de origem valida, com staging determinista (sem ler XLSX real)."""
    for marca in ("apice", "barbours"):
        (tmp_path / marca).mkdir()

    linhas = []
    for marca in ("apice", "barbours"):
        linhas.append({c: (marca if c == "brand" else
                           ("2026-05" if c == "ref_month" else
                            (f"SKU-{marca}" if "sku" in c or c == "product_name" else 1)))
                       for c in bf.DATA_COLS})
    monkeypatch.setattr(bf, "build_staging",
                        lambda scopes, root: bf.Staging(rows=pd.DataFrame(linhas),
                                                        scopes=scopes))
    return tmp_path


def _args(target, raiz, extra=None):
    return ["--apply", "--target", target, "--source-root", str(raiz)] + AUT_ARGS + (extra or [])


def _env(monkeypatch, env):
    for k in list(ENV_LOCAL) + list(ENV_NEON):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def _proibido(url):                                        # pragma: no cover
    raise AssertionError("abriu conexao gravavel antes dos guardrails")


# ---------------------------------------------------------------------------
# Caminho feliz — a ligacao de fato acontece
# ---------------------------------------------------------------------------

def test_apply_neon_percorre_o_caminho_real_inteiro(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    espiao = Espiao(conn)
    monkeypatch.setattr(bf, "_default_writable_engine", espiao)

    code = bf.main(_args("neon", raiz))
    assert code == bf.EXIT_OK

    # 1. usou a credencial de ESCRITA do destino, nunca a read-only
    assert espiao.urls == [ENV_NEON["BACKFILL_NEON_RW_URL"]]
    # 2. exatamente UMA conexao
    assert sum(e.conexoes for e in espiao.engines) == 1
    # 3. portas 5-8 na conexao viva, ANTES de qualquer mutacao
    e = conn.eventos
    assert "lock.acquire" in e
    assert e.index("lock.acquire") < e.index("backup.create")
    # 4. backup commitado ANTES do DELETE
    assert e.index("backup.create") < e.index("trans.commit") < e.index("mutacao.delete")
    # 5. mutacao na ordem certa
    assert e.index("mutacao.delete") < e.index("mutacao.insert")
    # 6. lock liberado e conexao fechada
    assert e.index("lock.release") > e.index("mutacao.insert")
    assert conn.fechada and espiao.engines[0].descartada
    # 7. nenhuma segunda tentativa
    assert e.count("backup.create") == 1
    assert e.count("mutacao.delete") == 1
    assert e.count("mutacao.insert") == 1

    err = capsys.readouterr().err
    assert "APPLY CONCLUIDO" in err and "sem retry" in err


def test_local_e_neon_seguem_caminhos_distintos(raiz, monkeypatch):
    urls = {}
    for destino, env in (("local", ENV_LOCAL), ("neon", ENV_NEON)):
        _env(monkeypatch, env)
        conn = FakeConn(db=env[f"BACKFILL_{destino.upper()}_EXPECT_DB"])
        espiao = Espiao(conn)
        monkeypatch.setattr(bf, "_default_writable_engine", espiao)
        assert bf.main(_args(destino, raiz)) == bf.EXIT_OK
        urls[destino] = espiao.urls[0]
        # o INSERT usa as colunas do destino: `ingested_at` so' no Neon
        insert = [s for s in conn.sqls if s.strip().upper().startswith("INSERT")][0]
        assert (bf.INGESTED_AT_COL in insert) == (destino == "neon")
        if destino == "neon":
            assert "NOW()" in insert
    assert urls["local"] != urls["neon"]


def test_uma_chamada_trata_um_unico_destino(raiz, monkeypatch):
    # Nao existe comando que atualize os dois bancos: uma chamada, um destino.
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    espiao = Espiao(conn)
    monkeypatch.setattr(bf, "_default_writable_engine", espiao)
    bf.main(_args("neon", raiz))
    assert len(espiao.urls) == 1
    assert all("neon" in u or "remoto" in u for u in espiao.urls)


def test_apply_scoped_replace_e_chamado_uma_unica_vez(raiz, monkeypatch):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    chamadas = []
    real = bf.apply_scoped_replace
    monkeypatch.setattr(bf, "apply_scoped_replace",
                        lambda st, *, executor: (chamadas.append(executor) or
                                                 real(st, executor=executor)))
    assert bf.main(_args("neon", raiz)) == bf.EXIT_OK
    assert len(chamadas) == 1
    assert isinstance(chamadas[0], bf.ScopedReplaceExecutor)
    assert chamadas[0].target == "neon"
    assert chamadas[0].conn is conn          # MESMA sessao do lock


# ---------------------------------------------------------------------------
# Guardrails: nenhum I/O gravavel antes das portas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("extra,esperado,marcador", [
    ([], bf.EXIT_VALIDATION_REFUSED, "SEM escopo"),
    (["--scope", "apice:2026-05"], bf.EXIT_VALIDATION_REFUSED, "PARCIAL"),
    (["--scope", "apice:2026-05", "--scope", "apice:2026-05",
      "--scope", "barbours:2026-05"], bf.EXIT_VALIDATION_REFUSED, "repetido"),
    (["--scope", "apice:2026-05", "--scope", "barbours:2026-05",
      "--scope", "lescent:2026-05"], bf.EXIT_VALIDATION_REFUSED, "allowlist"),
    (["--scope", "rituaria:2026-07"], bf.EXIT_VALIDATION_REFUSED, "imatura"),
    (["--scope", "kokeshi:2026-08"], bf.EXIT_VALIDATION_REFUSED, "recarregar"),
])
def test_escopos_invalidos_recusam_sem_abrir_conexao(
        raiz, monkeypatch, capsys, extra, esperado, marcador):
    _env(monkeypatch, ENV_NEON)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    code = bf.main(["--apply", "--target", "neon", "--source-root", str(raiz)] + extra)
    assert code == esperado
    assert marcador in capsys.readouterr().err


def test_consentimento_ausente_recusa_sem_abrir_conexao(raiz, monkeypatch, capsys):
    env = dict(ENV_NEON); env.pop(bf.CONSENT_ENV)
    _env(monkeypatch, env)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    assert bf.main(_args("neon", raiz)) == bf.EXIT_VALIDATION_REFUSED
    assert bf.CONSENT_ENV in capsys.readouterr().err


def test_credencial_de_escrita_ausente_recusa_sem_abrir_conexao(raiz, monkeypatch, capsys):
    env = dict(ENV_NEON); env.pop("BACKFILL_NEON_RW_URL")
    _env(monkeypatch, env)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    assert bf.main(_args("neon", raiz)) == bf.EXIT_VALIDATION_REFUSED
    assert "BACKFILL_NEON_RW_URL" in capsys.readouterr().err


def test_target_ausente_recusa_sem_abrir_conexao(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    code = bf.main(["--apply", "--source-root", str(raiz)] + AUT_ARGS)
    assert code == bf.EXIT_VALIDATION_REFUSED
    assert "porta 4" in capsys.readouterr().err


def test_source_root_e_obrigatorio_no_apply(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    code = bf.main(["--apply", "--target", "neon"] + AUT_ARGS)
    assert code == bf.EXIT_VALIDATION_REFUSED
    assert "--source-root explicito" in capsys.readouterr().err


def test_default_da_pasta_padrao_nunca_e_herdado_no_apply(monkeypatch, capsys, tmp_path):
    """Mesmo com `loader.SHOPEE_ROOT` apontando para um diretorio VALIDO, o
    apply recusa: a origem de uma escrita tem de ser declarada, e uma pasta de
    trabalho recebe arquivo novo sem ninguem avisar."""
    _env(monkeypatch, ENV_NEON)
    (tmp_path / "apice").mkdir()
    monkeypatch.setattr(bf.loader, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    assert bf.main(["--apply", "--target", "neon"] + AUT_ARGS) == bf.EXIT_VALIDATION_REFUSED
    assert "--source-root explicito" in capsys.readouterr().err


def test_source_root_inexistente_e_erro_de_uso(monkeypatch, capsys, tmp_path):
    _env(monkeypatch, ENV_NEON)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    code = bf.main(["--apply", "--target", "neon",
                    "--source-root", str(tmp_path / "nao_existe")] + AUT_ARGS)
    assert code == bf.EXIT_USAGE


# ---------------------------------------------------------------------------
# Portas 5-8, na conexao viva
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kw,marcador", [
    ({"db": "banco_errado"}, "RECUSADA"),
    ({"tem_produtos": 0}, "RECUSADA"),
    ({"in_recovery": True}, "RECOVERY"),
    ({"tx_readonly": "on"}, "read_only"),
    ({"privs": False}, "privilegio"),
    ({"ssl": False}, "SSL"),
    ({"lock": False}, "advisory lock"),
])
def test_portas_da_conexao_viva_recusam_antes_de_mutar(
        raiz, monkeypatch, capsys, kw, marcador):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn(**kw)
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    assert bf.main(_args("neon", raiz)) == bf.EXIT_VALIDATION_REFUSED
    assert marcador in capsys.readouterr().err
    # nada foi mutado e a conexao foi fechada mesmo assim
    assert "mutacao.delete" not in conn.eventos
    assert "mutacao.insert" not in conn.eventos
    assert "backup.create" not in conn.eventos
    assert conn.fechada


def test_ssl_ausente_nao_bloqueia_o_destino_local(raiz, monkeypatch):
    _env(monkeypatch, ENV_LOCAL)
    conn = FakeConn(db="mktplace_control", ssl=False)
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    assert bf.main(_args("local", raiz)) == bf.EXIT_OK


def test_lock_ocupado_nao_gera_espera_nem_retry(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn(lock=False)
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    assert bf.main(_args("neon", raiz)) == bf.EXIT_VALIDATION_REFUSED
    assert "nada foi aguardado" in capsys.readouterr().err
    assert conn.eventos.count("lock.acquire") == 1        # uma tentativa so'
    assert "pg_advisory_lock(" not in " ".join(conn.sqls)  # nunca a versao que espera


def test_lock_e_liberado_na_mesma_sessao_no_caminho_feliz(raiz, monkeypatch):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    bf.main(_args("neon", raiz))
    assert conn.eventos.count("lock.acquire") == 1
    assert conn.eventos.count("lock.release") == 1
    assert conn.eventos.index("lock.release") < conn.eventos.index("conn.close")


# ---------------------------------------------------------------------------
# Backup, rollback, indeterminado
# ---------------------------------------------------------------------------

def test_backup_que_nao_confere_recusa_e_nao_apaga_nada(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn(backup_confere=False)
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    assert bf.main(_args("neon", raiz)) == bf.EXIT_VALIDATION_REFUSED
    assert "mutacao.delete" not in conn.eventos
    assert "trans.rollback" in conn.eventos
    # lock liberado e conexao fechada mesmo na falha
    assert "lock.release" in conn.eventos and conn.fechada


def test_rollback_confirmado_vira_exit_3(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    monkeypatch.setattr(bf, "apply_scoped_replace",
                        lambda st, *, executor: bf.EXIT_ROLLED_BACK)
    assert bf.main(_args("neon", raiz)) == bf.EXIT_ROLLED_BACK
    err = capsys.readouterr().err
    assert "rollback CONFIRMADO" in err and "nada mudou" in err
    assert "lock.release" in conn.eventos


def test_commit_indeterminado_vira_exit_4_e_manda_nao_repetir(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    monkeypatch.setattr(bf, "apply_scoped_replace",
                        lambda st, *, executor: bf.EXIT_INDETERMINATE)
    assert bf.main(_args("neon", raiz)) == bf.EXIT_INDETERMINATE
    err = capsys.readouterr().err
    assert "INDETERMINADO" in err
    assert "NAO repita" in err
    assert "backup" in err
    assert "lock.release" in conn.eventos and conn.fechada


def test_falha_de_conexao_e_erro_de_configuracao_sanitizado(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)

    class Explode:
        def connect(self): raise RuntimeError(
            "could not connect to host=segredo.example user=admin password=hunter2")
        def dispose(self): pass

    monkeypatch.setattr(bf, "_default_writable_engine", lambda url: Explode())
    assert bf.main(_args("neon", raiz)) == bf.EXIT_USAGE
    err = capsys.readouterr().err
    assert "ERRO DE CONFIGURACAO" in err
    for vazamento in ("hunter2", "segredo.example", "admin", "password"):
        assert vazamento not in err


# ---------------------------------------------------------------------------
# Interrupcoes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_interrupcao_libera_lock_e_conexao_e_propaga(raiz, monkeypatch, exc):
    """Interrupcao NAO vira exit code: propaga. Mas o lock e a conexao tem de
    ser liberados antes — deixar transacao e lock pendurados e' pior."""
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))

    def interrompe(st, *, executor):
        raise exc()

    monkeypatch.setattr(bf, "apply_scoped_replace", interrompe)
    with pytest.raises(exc):
        bf.main(_args("neon", raiz))
    assert "lock.release" in conn.eventos
    assert conn.fechada


def test_interrupcao_nunca_vira_sucesso(raiz, monkeypatch):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    monkeypatch.setattr(bf, "apply_scoped_replace",
                        lambda st, *, executor: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        bf.main(_args("neon", raiz))
    # nao houve segunda tentativa
    assert conn.eventos.count("lock.acquire") == 1


def test_codigo_nao_usa_except_baseexception():
    fonte = Path(bf.__file__).read_text(encoding="utf-8")
    assert "except BaseException" not in fonte


# ---------------------------------------------------------------------------
# Sanitizacao
# ---------------------------------------------------------------------------

def test_sanitize_reduz_excecao_desconhecida_ao_nome_da_classe():
    class DriverError(Exception):
        pass

    e = DriverError("INSERT INTO t VALUES (...) -- host=x user=y password=z")
    saida = bf._sanitize(e)
    assert saida.startswith("DriverError")
    for vazamento in ("INSERT", "password", "host=", "user="):
        assert vazamento not in saida


def test_sanitize_preserva_mensagens_proprias_ja_seguras():
    e = bf.WriteGuardError("porta 2: consentimento ausente")
    assert bf._sanitize(e) == "porta 2: consentimento ausente"


def test_erro_no_meio_da_mutacao_nao_vaza_sql_nem_parametro(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))

    def explode(st, *, executor):
        raise RuntimeError("INSERT INTO marts.x VALUES ('produto secreto', 999.99)")

    monkeypatch.setattr(bf, "apply_scoped_replace", explode)
    assert bf.main(_args("neon", raiz)) == bf.EXIT_VALIDATION_REFUSED
    err = capsys.readouterr().err
    for vazamento in ("produto secreto", "999.99", "INSERT INTO"):
        assert vazamento not in err


def test_nenhuma_saida_contem_a_dsn(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    bf.main(_args("neon", raiz))
    saida = capsys.readouterr()
    for texto in (saida.out, saida.err):
        assert "postgresql://" not in texto
        assert "gravador" not in texto
        assert "remoto.example" not in texto


# ---------------------------------------------------------------------------
# Os outros modos nao mudaram
# ---------------------------------------------------------------------------

def test_offline_dry_run_continua_sem_banco_e_sem_credencial_de_escrita(
        monkeypatch, capsys, tmp_path):
    _env(monkeypatch, ENV_NEON)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    monkeypatch.setattr(bf, "resolve_write_url", _proibido)
    monkeypatch.setattr(bf, "read_current_scope", _proibido)
    monkeypatch.setattr(bf.loader, "_find_xlsx", lambda d: [], raising=True)
    monkeypatch.setattr(bf.loader, "_plan_brand_snapshots", lambda b, f, **k: {}, raising=True)
    monkeypatch.setattr(bf.loader, "_load_brand", lambda b: pd.DataFrame(
        {"brand": [], "ref_month": pd.to_datetime([]), "sku_ref": [],
         "product_name": [], "variation_name": [], "qty": [], "subtotal": [],
         "status": [], "buyer_username": []}), raising=True)
    for marca in ("apice", "barbours"):
        (tmp_path / marca).mkdir()
    code = bf.main(["--offline-dry-run", "--source-root", str(tmp_path)] + AUT_ARGS)
    assert code == bf.EXIT_OK
    assert "NENHUM BANCO" in capsys.readouterr().out


def test_dry_run_continua_exigindo_target_e_nao_usa_credencial_de_escrita(
        monkeypatch, capsys, tmp_path):
    _env(monkeypatch, ENV_NEON)
    monkeypatch.setattr(bf, "_default_writable_engine", _proibido)
    monkeypatch.setattr(bf, "resolve_write_url", _proibido)
    for marca in ("apice", "barbours"):
        (tmp_path / marca).mkdir()
    code = bf.main(["--dry-run", "--source-root", str(tmp_path)] + AUT_ARGS)
    assert code == bf.EXIT_USAGE
    assert "--dry-run exige --target" in capsys.readouterr().err


def test_exit_codes_continuam_com_os_mesmos_valores():
    assert (bf.EXIT_OK, bf.EXIT_VALIDATION_REFUSED, bf.EXIT_ROLLED_BACK,
            bf.EXIT_INDETERMINATE, bf.EXIT_USAGE) == (0, 2, 3, 4, 5)


def test_allowlist_e_deltas_esperados_nao_mudaram():
    from decimal import Decimal
    assert bf.AUTHORIZED_SCOPES == (("apice", "2026-05"), ("barbours", "2026-05"))
    assert bf.EXPECTED_GMV_DELTA[("apice", "2026-05")] == Decimal("-23292.43")
    assert bf.EXPECTED_GMV_DELTA[("barbours", "2026-05")] == Decimal("-80987.03")
    assert bf.EXPECTED_TOTAL_GMV_DELTA == Decimal("-104279.46")
    assert bf.MATURATION_THRESHOLD_AFTER == Decimal("0.99")


def test_um_unico_ponto_do_modulo_abre_conexao_gravavel():
    fonte = Path(bf.__file__).read_text(encoding="utf-8")
    assert fonte.count("def _default_writable_engine") == 1
    # `create_engine` sem readonly so' aparece nessa funcao
    bloco = fonte.split("def _default_writable_engine")[1].split("\ndef ")[0]
    assert "create_engine" in bloco


# ---------------------------------------------------------------------------
# Gate SH-API-2E3-H1 — autobegin do SQLAlchemy 2.0
# ---------------------------------------------------------------------------
#
# Defeito medido no Gate SH-API-2E4: os SELECTs do preflight abrem transacao
# IMPLICITA (autobegin), e o `begin()` explicito do executor entao levanta
# `InvalidRequestError`. O apply real morria com exit 2 sem criar backup.


def test_o_fake_reproduz_o_autobegin_do_sqlalchemy():
    """Prova que o duble e ESTRITO. Sem isto, os testes abaixo passariam por
    permissividade do fake — foi exatamente assim que o defeito escapou."""
    from sqlalchemy.exc import InvalidRequestError
    conn = FakeConn()
    assert conn.in_transaction() is False
    conn.execute("SELECT 1")
    assert conn.in_transaction() is True, "execute() deveria fazer autobegin"
    with pytest.raises(InvalidRequestError):
        conn.begin()
    conn.rollback()
    assert conn.in_transaction() is False
    conn.begin()
    assert conn.in_transaction() is True


def test_preflight_deixa_transacao_ativa_e_o_helper_a_encerra():
    conn = FakeConn()
    conn.execute("SELECT pg_is_in_recovery()")
    assert conn.in_transaction() is True
    assert bf.close_preflight_transaction(conn) is True
    assert conn.in_transaction() is False
    assert conn.eventos.count("conn.rollback") == 1


def test_sem_transacao_ativa_o_helper_nao_faz_rollback_desnecessario():
    conn = FakeConn()
    assert conn.in_transaction() is False
    assert bf.close_preflight_transaction(conn) is False
    assert "conn.rollback" not in conn.eventos


def test_helper_levanta_se_a_transacao_persistir_depois_do_rollback():
    class Teimosa:
        def in_transaction(self): return True
        def rollback(self): pass

    with pytest.raises(bf.PreflightTransactionError) as ei:
        bf.close_preflight_transaction(Teimosa())
    assert "nada foi criado nem mutado" in str(ei.value)


def test_sequencia_final_preflight_lock_rollback_begin(raiz, monkeypatch, capsys):
    """A ordem que o gate exige, provada pelo main() real."""
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    espiao = Espiao(conn)
    monkeypatch.setattr(bf, "_default_writable_engine", espiao)

    assert bf.main(_args("neon", raiz)) == bf.EXIT_OK
    e = conn.eventos
    assert e[0] == "autobegin"
    assert e.index("lock.acquire") > e.index("autobegin")
    assert e.index("conn.rollback") > e.index("lock.acquire")
    assert e.index("conn.begin") > e.index("conn.rollback")
    assert e.index("conn.begin") < e.index("backup.create")
    # o lock de SESSAO sobreviveu ao rollback: nao foi reobtido
    assert e.count("lock.acquire") == 1
    assert e.index("lock.release") > e.index("mutacao.insert")
    # duas transacoes EXPLICITAS: backup e publicacao
    assert e.count("conn.begin") == 2
    assert e.count("trans.commit") == 2
    assert "transacao implicita do preflight: encerrada" in capsys.readouterr().err


def test_executor_consegue_backup_e_publicacao_depois_do_rollback(raiz, monkeypatch):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    assert bf.main(_args("neon", raiz)) == bf.EXIT_OK
    for etapa in ("backup.create", "mutacao.delete", "mutacao.insert"):
        assert conn.eventos.count(etapa) == 1, etapa


def test_o_executor_nunca_reaproveita_transacao_implicita():
    """O executor abre transacao EXPLICITA. Se encontrar uma implicita aberta,
    tem de levantar — nunca herda-la silenciosamente."""
    from sqlalchemy.exc import InvalidRequestError
    conn = FakeConn()
    conn.execute("SELECT 1")
    ex = bf.ScopedReplaceExecutor(conn, target="neon")
    with pytest.raises(InvalidRequestError):
        ex.begin()


def test_falha_no_rollback_nao_chama_o_executor_e_libera_tudo(raiz, monkeypatch, capsys):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn(rollback_falha=True)
    espiao = Espiao(conn)
    monkeypatch.setattr(bf, "_default_writable_engine", espiao)
    chamou = []
    monkeypatch.setattr(bf, "apply_scoped_replace",
                        lambda st, *, executor: chamou.append(1))

    assert bf.main(_args("neon", raiz)) == bf.EXIT_VALIDATION_REFUSED
    assert chamou == [], "executor foi chamado apesar da falha no rollback"
    for etapa in ("backup.create", "mutacao.delete", "mutacao.insert"):
        assert etapa not in conn.eventos
    assert "lock.release" in conn.eventos
    assert conn.fechada and espiao.engines[0].descartada
    err = capsys.readouterr().err
    assert "APPLY RECUSADO" in err
    assert "postgresql://" not in err and "gravador" not in err


def test_falha_no_rollback_nao_gera_retry(raiz, monkeypatch):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn(rollback_falha=True)
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))
    bf.main(_args("neon", raiz))
    assert conn.eventos.count("conn.rollback") == 1
    assert conn.eventos.count("lock.acquire") == 1


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_interrupcao_apos_o_rollback_continua_propagando_com_cleanup(
        raiz, monkeypatch, exc):
    _env(monkeypatch, ENV_NEON)
    conn = FakeConn()
    monkeypatch.setattr(bf, "_default_writable_engine", Espiao(conn))

    def interrompe(st, *, executor):
        raise exc()

    monkeypatch.setattr(bf, "apply_scoped_replace", interrompe)
    with pytest.raises(exc):
        bf.main(_args("neon", raiz))
    assert "conn.rollback" in conn.eventos
    assert "lock.release" in conn.eventos
    assert conn.fechada


# ---------------------------------------------------------------------------
# Prova com Connection REAL do SQLAlchemy (engine descartavel, zero dependencia)
# ---------------------------------------------------------------------------

def _engine_descartavel():
    """SQLite em memoria: `Connection` REAL do SQLAlchemy, mesma semantica de
    autobegin, sem servidor e sem dependencia nova (sqlite3 e stdlib).
    Nenhuma conexao gravavel a local ou Neon e aberta."""
    from sqlalchemy import create_engine
    return create_engine("sqlite+pysqlite:///:memory:")


def test_autobegin_e_real_no_sqlalchemy_nao_so_no_fake():
    from sqlalchemy import text
    from sqlalchemy.exc import InvalidRequestError
    eng = _engine_descartavel()
    try:
        with eng.connect() as conn:
            assert conn.in_transaction() is False
            conn.execute(text("SELECT 1"))
            assert conn.in_transaction() is True
            with pytest.raises(InvalidRequestError):
                conn.begin()
    finally:
        eng.dispose()


def test_helper_resolve_o_autobegin_numa_connection_real():
    from sqlalchemy import text
    eng = _engine_descartavel()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
            assert conn.in_transaction() is True
            assert bf.close_preflight_transaction(conn) is True
            assert conn.in_transaction() is False
            trans = conn.begin()
            assert conn.in_transaction() is True
            trans.rollback()
    finally:
        eng.dispose()


def test_helper_e_idempotente_numa_connection_real():
    eng = _engine_descartavel()
    try:
        with eng.connect() as conn:
            assert bf.close_preflight_transaction(conn) is False
            assert bf.close_preflight_transaction(conn) is False
    finally:
        eng.dispose()


def test_o_fix_nao_mexeu_em_formula_allowlist_deltas_nem_maturidade():
    from decimal import Decimal
    assert bf.AUTHORIZED_SCOPES == (("apice", "2026-05"), ("barbours", "2026-05"))
    assert bf.EXPECTED_GMV_DELTA[("apice", "2026-05")] == Decimal("-23292.43")
    assert bf.EXPECTED_GMV_DELTA[("barbours", "2026-05")] == Decimal("-80987.03")
    assert bf.EXPECTED_TOTAL_GMV_DELTA == Decimal("-104279.46")
    assert bf.MATURATION_THRESHOLD_AFTER == Decimal("0.99")
    assert (bf.EXIT_OK, bf.EXIT_VALIDATION_REFUSED, bf.EXIT_ROLLED_BACK,
            bf.EXIT_INDETERMINATE, bf.EXIT_USAGE) == (0, 2, 3, 4, 5)
    assert bf.insert_columns("local") == bf.DATA_COLS
    assert bf.INGESTED_AT_COL in bf.insert_columns("neon")
