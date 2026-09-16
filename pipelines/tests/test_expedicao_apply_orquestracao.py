"""Orquestracao do `--apply`: ordem, desfechos e exit codes.

NENHUMA CONEXAO REAL. Os fakes falam o protocolo do psycopg2 e sao ESTRITOS de
proposito: um fake mais permissivo que o driver faz a suite passar contra um
runtime que quebra sempre — foi assim que um fake posicional escondeu o uso de
`RealDictCursor` uma vez. Aqui os fakes devolvem MAPPING, recusam cursor em
conexao fechada, recusam `BEGIN` aninhado, contam commit/rollback separadamente
e so' liberam o advisory lock se ele tiver sido adquirido.

Estes testes exercitam `run_apply` DE PONTA A PONTA: `load_registry`, `extract`,
`build_fila_shopee`, `build_account_summaries` e `publish_channel` rodam de
verdade. Nada de dublar a peca que se quer testar.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from pipelines.expedicao import cli
from pipelines.expedicao.contract import (
    ADVISORY_LOCK_KEYS,
    FILA_TABLE,
    RUN_TABLE,
    Channel,
)

AGORA = datetime(2026, 9, 15, 20, 30, 0, tzinfo=timezone.utc)
CHAVE = ADVISORY_LOCK_KEYS[Channel.SHOPEE]

CONTAS = {
    "1609671923": ("apice", 1),
    "1579330222": ("barbours", 2),
    "1593864538": ("lescent", 4),
    "1457734799": ("rituaria", 5),
}


# ===========================================================================
# Fakes estritos
# ===========================================================================
class ErroDeUso(AssertionError):
    """O codigo usou a conexao de um jeito que o psycopg2 nao aceitaria."""


class CursorFake:
    def __init__(self, conn):
        self.conn = conn
        self._linhas: list[dict] = []
        self.rowcount = -1
        self._fechado = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._fechado = True
        return False

    def execute(self, sql, params=None):
        if self._fechado:
            raise ErroDeUso("execute em cursor ja fechado")
        if self.conn.fechada:
            raise ErroDeUso("execute em conexao fechada")
        texto = str(sql)
        if "BEGIN" in texto.upper().split() and self.conn.em_transacao:
            raise ErroDeUso("BEGIN aninhado")
        self.conn.sqls.append(texto)
        self.conn.params.append(params)
        if not self.conn.autocommit:
            self.conn.em_transacao = True
        self._linhas = self.conn.responder(texto, params)
        self.rowcount = len(self._linhas)

    def fetchone(self):
        return self._linhas[0] if self._linhas else None

    def fetchall(self):
        return list(self._linhas)


class ConexaoFake:
    """Conexao generica. `roteador` devolve as linhas de cada SQL."""

    def __init__(self, papel: str, roteador):
        self.papel = papel
        self._roteador = roteador
        self.autocommit = False
        self.fechada = False
        self.em_transacao = False
        self.sqls: list[str] = []
        self.params: list = []
        self.commits = 0
        self.rollbacks = 0
        self.eventos: list[str] = []
        self.lock_concedido = True
        self.lock_segurado = False
        self.falha_no_commit: Exception | None = None
        #: 1 = o proximo commit; 2 = o segundo; e assim por diante.
        self.commit_que_falha = 1

    # -- protocolo psycopg2 -------------------------------------------------
    def cursor(self, *_, **__):
        if self.fechada:
            raise ErroDeUso("cursor() em conexao fechada")
        return CursorFake(self)

    def commit(self):
        if self.fechada:
            raise ErroDeUso("commit em conexao fechada")
        if self.falha_no_commit is not None:
            self.commit_que_falha -= 1
            if self.commit_que_falha <= 0:
                # psycopg2 deixa a transacao em estado desconhecido aqui.
                erro, self.falha_no_commit = self.falha_no_commit, None
                raise erro
        self.commits += 1
        self.em_transacao = False
        self.eventos.append("commit")

    def rollback(self):
        if self.fechada:
            raise ErroDeUso("rollback em conexao fechada")
        self.rollbacks += 1
        self.em_transacao = False
        self.eventos.append("rollback")

    def close(self):
        self.fechada = True

    def set_session(self, **_):
        pass

    # -- roteamento ---------------------------------------------------------
    def responder(self, sql: str, params):
        if "pg_try_advisory_lock" in sql:
            self.eventos.append("try_lock")
            if self.lock_concedido:
                self.lock_segurado = True
            return [{"pg_try_advisory_lock": self.lock_concedido}]
        if "pg_advisory_unlock" in sql:
            self.eventos.append("unlock")
            self.lock_segurado = False
            return []
        return self._roteador(self, sql, params)


def _linha_backlog(shop_id: str, order_sn: str, *, horas=1.0, ship_by=None):
    conta = CONTAS[shop_id][0]
    marco = AGORA - timedelta(hours=horas)
    return {
        "shop_account": conta,
        "order_sn": order_sn,
        "shop_id": shop_id,
        "order_status": "PROCESSED",
        "create_time": marco,
        "pay_time": marco,
        "ship_by_date": ship_by,
        "pickup_done_time": None,
        "shipping_carrier": "Shopee Xpress",
        "ingested_at": AGORA - timedelta(hours=1),
    }


class Cenario:
    """Monta o trio de conexoes e guarda o que cada uma recebeu."""

    def __init__(
        self,
        *,
        backlog=None,
        registry=None,
        watermarks_fonte=None,
        preflight=None,
        anteriores=None,
    ):
        self.backlog = backlog if backlog is not None else [
            _linha_backlog("1609671923", "A1"),
            _linha_backlog("1579330222", "B1", horas=60.0),
        ]
        self.registry = registry if registry is not None else [
            {
                "external_seller_id": ext,
                "loja_id": loja,
                "brand_key": marca,
                "ativo": True,
            }
            for ext, (marca, loja) in CONTAS.items()
        ]
        self.watermarks_fonte = (
            watermarks_fonte
            if watermarks_fonte is not None
            else [
                {
                    "shop_id": ext,
                    "shop_account": marca,
                    "max_ingested_at": AGORA - timedelta(hours=1),
                }
                for ext, (marca, _) in CONTAS.items()
            ]
        )
        self.preflight = preflight or {
            "db": "neon", "usr": "app", "replica": False,
            "somente_leitura": "off", "tem_fila": True, "tem_resumo": True,
        }
        self.anteriores = anteriores or []

        self.target = ConexaoFake("target", self._rotear_target)
        self.source = ConexaoFake("source", self._rotear_source)
        self.audit = ConexaoFake("audit", self._rotear_audit)
        self.ordem: list[str] = []
        self.abertas: list[str] = []
        self.lotes: list[str] = []
        self.mensagens: list[str] = []
        self.enviadas: dict[str, list] = {}

    # -- roteadores ---------------------------------------------------------
    def _rotear_target(self, conn, sql, params):
        if "current_database()" in sql:
            self.ordem.append("preflight")
            return [dict(self.preflight)]
        if "dim_seller_account" in sql:
            self.ordem.append("registry")
            return list(self.registry)
        if "MAX(source_watermark_at)" in sql:
            return list(self.anteriores)
        if sql.startswith("DELETE FROM"):
            self.ordem.append("delete")
            return []
        raise ErroDeUso(f"SQL inesperada no target: {sql[:60]!r}")

    def _rotear_source(self, conn, sql, params):
        if "MAX(ingested_at)" in sql:
            self.ordem.append("watermarks")
            return list(self.watermarks_fonte)
        if "percentile_cont" in sql:
            self.ordem.append("baseline")
            return []
        if "order_sn" in sql:
            self.ordem.append("backlog")
            return list(self.backlog)
        raise ErroDeUso(f"SQL inesperada na fonte: {sql[:60]!r}")

    def _rotear_audit(self, conn, sql, params):
        if "RETURNING sync_run_id" in sql:
            self.ordem.append("audit_start")
            return [{"sync_run_id": 77}]
        if sql.strip().startswith("UPDATE audit.source_sync_run"):
            self.ordem.append(f"audit_finish:{params[0]}")
            return []
        if "data_quality_check" in sql:
            self.ordem.append("audit_check")
            return []
        raise ErroDeUso(f"SQL inesperada na auditoria: {sql[:60]!r}")

    # -- execucao -----------------------------------------------------------
    def rodar(self, **kwargs):
        def _uuid():
            novo = str(uuid.uuid4())
            self.lotes.append(novo)
            return novo

        opcoes = {
            "open_target": self._abrir("target", self.target),
            "open_source": self._abrir("source", self.source),
            "open_audit": self._abrir("audit", self.audit),
            "uuid_factory": _uuid,
            "execute_values": self._execute_values,
            "log": self.mensagens.append,
        }
        opcoes.update(kwargs)
        return cli.run_apply(Channel.SHOPEE, AGORA, **opcoes)

    def _abrir(self, nome, conn):
        def abrir():
            self.abertas.append(nome)
            self.ordem.append(f"abrir:{nome}")
            return conn
        return abrir

    def _execute_values(self, cur, sql, argslist, *a, **k):
        alvo = "insert_fila" if FILA_TABLE in sql else "insert_resumo"
        self.ordem.append(f"{alvo}:{len(argslist)}")
        self.enviadas.setdefault(alvo, []).extend(argslist)
        cur.conn.sqls.append(sql)
        if not cur.conn.autocommit:
            cur.conn.em_transacao = True


@pytest.fixture
def cenario():
    return Cenario()


def _novo(**kw):
    return Cenario(**kw)


# ===========================================================================
# 1-2. O caminho produtivo existe e chega ao fim
# ===========================================================================
def test_apply_alcanca_publicacao_e_nao_recusa_incondicionalmente(cenario):
    """O gate anterior devolvia EXIT_FALHA sem tentar. Isso nao pode voltar."""
    assert cenario.rodar() == cli.EXIT_OK
    assert "delete" in cenario.ordem
    assert any(e.startswith("insert_fila") for e in cenario.ordem)
    assert any(e.startswith("insert_resumo") for e in cenario.ordem)
    assert cenario.target.commits == 1


def test_ordem_das_quinze_etapas(cenario):
    cenario.rodar()
    o = cenario.ordem
    assert o.index("abrir:target") < o.index("preflight")
    assert o.index("preflight") < o.index("registry")
    assert o.index("registry") < o.index("audit_start")
    assert o.index("audit_start") < o.index("abrir:source")
    assert o.index("abrir:source") < o.index("watermarks")
    assert o.index("watermarks") < o.index("delete")
    assert o.index("delete") < o.index("audit_finish:success")


# ===========================================================================
# 3-5. refresh_batch_id
# ===========================================================================
def test_batch_id_e_uuid4_valido(cenario):
    cenario.rodar()
    valor = uuid.UUID(cenario.lotes[0])
    assert valor.version == 4


def test_batch_id_injetado_e_o_que_chega_nas_linhas_publicadas():
    """O gerador e injetavel de verdade: o valor injetado chega ao INSERT.

    Sem esta prova, `uuid_factory` poderia ser aceito e ignorado, e o teste do
    formato UUIDv4 passaria olhando para um valor que ninguem publica.
    """
    from pipelines.expedicao.publisher import FILA_COLUMNS, SUMMARY_COLUMNS

    c = _novo()
    assert c.rodar(uuid_factory=lambda: "lote-A") == cli.EXIT_OK

    i_fila = FILA_COLUMNS.index("refresh_batch_id")
    i_resumo = SUMMARY_COLUMNS.index("refresh_batch_id")
    assert {linha[i_fila] for linha in c.enviadas["insert_fila"]} == {"lote-A"}
    assert {linha[i_resumo] for linha in c.enviadas["insert_resumo"]} == {"lote-A"}


def test_batch_id_nao_deriva_de_relogio_canal_nem_effective_at():
    """Duas execucoes no MESMO `effective_at` precisam de identidades distintas.

    Se o lote fosse derivado do instante, a auditoria nao conseguiria separar
    duas tentativas do mesmo minuto — exatamente o caso que interessa investigar.
    """
    vistos = []
    for _ in range(50):
        c = _novo()
        c.rodar()
        vistos.append(c.lotes[0])
    assert len(set(vistos)) == 50
    carimbo = AGORA.isoformat()
    assert all(carimbo not in v and Channel.SHOPEE.value not in v for v in vistos)


# ===========================================================================
# 6-9. Lock
# ===========================================================================
def test_lock_adquirido_antes_de_ler_a_fonte(cenario):
    cenario.rodar()
    assert cenario.target.eventos[0] == "try_lock"
    assert cenario.ordem.index("abrir:target") < cenario.ordem.index("abrir:source")
    assert "try_lock" in cenario.target.eventos[: cenario.target.eventos.index("commit")]


def test_lock_usa_a_chave_do_canal(cenario):
    cenario.rodar()
    assert any(p == (CHAVE,) for p in cenario.target.params if p is not None)


def test_lock_ocupado_para_sem_ler_fonte_sem_auditar_e_sem_apagar():
    c = _novo()
    c.target.lock_concedido = False
    assert c.rodar() == cli.EXIT_LOCK_OCUPADO
    assert "abrir:source" not in c.ordem
    assert "audit_start" not in c.ordem
    assert "delete" not in c.ordem
    assert c.target.commits == 0
    assert "unlock" not in c.target.eventos


def test_lock_liberado_mesmo_quando_a_publicacao_falha():
    c = _novo()
    c.target.falha_no_commit = RuntimeError("queda de rede")
    assert c.rodar() == cli.EXIT_COMMIT_INDETERMINADO
    assert "unlock" in c.target.eventos
    assert c.target.lock_segurado is False


# ===========================================================================
# 10-12. Preflight e configuracao
# ===========================================================================
def test_schema_da_018_ausente_para_antes_da_fonte():
    c = _novo(preflight={
        "db": "neon", "usr": "app", "replica": False,
        "somente_leitura": "off", "tem_fila": False, "tem_resumo": False,
    })
    assert c.rodar() == cli.EXIT_PRECONDICAO
    assert "abrir:source" not in c.ordem
    assert "try_lock" not in c.target.eventos
    assert "018" in " ".join(c.mensagens)


def test_replica_em_recovery_nao_recebe_publicacao():
    c = _novo(preflight={
        "db": "neon", "usr": "app", "replica": True,
        "somente_leitura": "off", "tem_fila": True, "tem_resumo": True,
    })
    assert c.rodar() == cli.EXIT_PRECONDICAO
    assert c.target.commits == 0


def test_sessao_read_only_nao_recebe_publicacao():
    c = _novo(preflight={
        "db": "neon", "usr": "app", "replica": False,
        "somente_leitura": "on", "tem_fila": True, "tem_resumo": True,
    })
    assert c.rodar() == cli.EXIT_PRECONDICAO


def test_env_ausente_falha_fechado_sem_fallback(monkeypatch):
    monkeypatch.delenv(cli.ENV_TARGET, raising=False)
    with pytest.raises(cli.PreflightFalhou):
        cli._url(cli.ENV_TARGET)


# ===========================================================================
# 13-14. Registry
# ===========================================================================
def test_registry_vazio_e_precondicao_externa_nao_tentativa():
    """Sem conta cadastrada nada foi tentado contra a fonte.

    Abrir uma execucao em `audit.source_sync_run` aqui registraria uma tentativa
    que nao houve, e o health check leria isso como fonte que falhou.
    """
    c = _novo(registry=[])
    assert c.rodar() == cli.EXIT_PRECONDICAO
    assert "delete" not in c.ordem
    assert "abrir:source" not in c.ordem
    assert "audit_start" not in c.ordem
    assert "dim_seller_account" in " ".join(c.mensagens)


def test_registry_ambiguo_bloqueia_sem_escolher_linha():
    duplicado = [
        {"external_seller_id": "1609671923", "loja_id": 1,
         "brand_key": "apice", "ativo": True},
        {"external_seller_id": "1609671923", "loja_id": 9,
         "brand_key": "outra", "ativo": True},
    ]
    c = _novo(registry=duplicado)
    assert c.rodar() == cli.EXIT_FONTE_NAO_PUBLICAVEL
    assert "delete" not in c.ordem
    assert c.target.commits == 0
    # Registry ambiguo E defeito de dado: deixa rastro de execucao falhada.
    assert "audit_finish:failed" in c.ordem


# ===========================================================================
# 15-17. Saude da fonte x fila vazia
# ===========================================================================
def test_fonte_nao_saudavel_preserva_a_fila_anterior():
    """Conta esperada ausente na fonte: nada de DELETE, nada de INSERT."""
    faltando = [w for w in _novo().watermarks_fonte if w["shop_id"] != "1457734799"]
    c = _novo(watermarks_fonte=faltando)
    assert c.rodar() == cli.EXIT_FONTE_NAO_PUBLICAVEL
    assert "delete" not in c.ordem
    assert not any(e.startswith("insert_") for e in c.ordem)
    assert c.target.commits == 0
    assert "audit_finish:failed" in c.ordem


def test_fila_vazia_com_fonte_saudavel_E_PUBLICADA():
    """Backlog zero nao e' fonte doente: a fotografia vazia e' um resultado.

    Sem isso, um dia realmente zerado manteria a fila de ontem no ar e a torre
    mostraria pendencia que ja' foi expedida.
    """
    c = _novo(backlog=[])
    assert c.rodar() == cli.EXIT_OK
    assert "delete" in c.ordem
    assert not any(e.startswith("insert_fila") for e in c.ordem)
    assert "insert_resumo:4" in c.ordem
    assert c.target.commits == 1
    assert "audit_finish:success" in c.ordem


def test_fila_vazia_e_fonte_doente_tem_desfechos_diferentes():
    vazia = _novo(backlog=[])
    doente = _novo(watermarks_fonte=[])
    assert vazia.rodar() == cli.EXIT_OK
    assert doente.rodar() == cli.EXIT_FONTE_NAO_PUBLICAVEL
    assert vazia.target.commits == 1
    assert doente.target.commits == 0


# ===========================================================================
# 18-21. Transacao, commit indeterminado e auditoria
# ===========================================================================
def test_delete_e_inserts_na_mesma_transacao_com_um_unico_commit(cenario):
    cenario.rodar()
    eventos = [e for e in cenario.ordem if e in ("delete",) or e.startswith("insert_")]
    assert eventos[0] == "delete"
    assert cenario.target.commits == 1
    assert cenario.target.rollbacks == 0


def test_falha_antes_do_commit_reverte_e_audita_failed():
    c = _novo()

    def falhar(cur, sql, argslist, *a, **k):
        raise RuntimeError("erro de copy")

    assert c.rodar(execute_values=falhar) == cli.EXIT_FALHA
    assert c.target.rollbacks == 1
    assert c.target.commits == 0
    assert "audit_finish:failed" in c.ordem


def test_commit_indeterminado_nao_reverte_nao_repete_e_nao_marca_auditoria():
    c = _novo()
    c.target.falha_no_commit = RuntimeError("conexao caiu no COMMIT")
    assert c.rodar() == cli.EXIT_COMMIT_INDETERMINADO
    assert c.target.rollbacks == 0
    assert not any(e.startswith("audit_finish") for e in c.ordem)
    assert c.ordem.count("delete") == 1


def test_auditoria_pos_commit_incompleta_nao_reverte_nem_marca_failed():
    c = _novo()
    # commit 1 = audit_start (antes da publicacao); commit 2 = finish_after_commit
    c.audit.falha_no_commit = RuntimeError("auditoria fora do ar")
    c.audit.commit_que_falha = 2
    codigo = c.rodar()
    assert codigo == cli.EXIT_AUDITORIA_INCOMPLETA
    assert c.target.commits == 1          # o dado ESTA publicado
    assert c.target.rollbacks == 0
    assert "audit_finish:failed" not in c.ordem
    texto = " ".join(c.mensagens).lower()
    assert "commitada" in texto and "reversao" in texto


def test_auditoria_usa_conexao_independente_da_publicacao(cenario):
    cenario.rodar()
    assert cenario.audit is not cenario.target
    assert cenario.audit.commits >= 2      # start + finish, alem dos checks


# ===========================================================================
# 22-24. Interrupcao, sanitizacao e ausencia de retry
# ===========================================================================
def test_keyboard_interrupt_propaga_depois_do_cleanup():
    c = _novo()

    def interromper(cur, sql, argslist, *a, **k):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        c.rodar(execute_values=interromper)
    assert c.target.fechada and c.source.fechada and c.audit.fechada
    assert "unlock" in c.target.eventos


def test_system_exit_propaga_depois_do_cleanup():
    c = _novo()

    def sair(cur, sql, argslist, *a, **k):
        raise SystemExit(9)

    with pytest.raises(SystemExit):
        c.rodar(execute_values=sair)
    assert c.target.fechada and c.source.fechada and c.audit.fechada


def test_todas_as_conexoes_fecham_no_caminho_feliz(cenario):
    cenario.rodar()
    assert cenario.target.fechada
    assert cenario.source.fechada
    assert cenario.audit.fechada


_SEGREDO = re.compile(
    r"(postgres(ql)?://|@[\w.-]+:\d{2,5}|password|sslmode=|INSERT INTO|SELECT )",
    re.IGNORECASE,
)


def test_nenhuma_mensagem_expoe_dsn_host_usuario_ou_sql():
    for c in (
        _novo(),
        _novo(registry=[]),
        _novo(watermarks_fonte=[]),
        _novo(preflight={"db": "neon", "usr": "app", "replica": True,
                         "somente_leitura": "off", "tem_fila": True,
                         "tem_resumo": True}),
    ):
        c.rodar()
        for msg in c.mensagens:
            assert not _SEGREDO.search(msg), msg


def test_credencial_em_excecao_e_redigida():
    c = _novo()

    def vazar(cur, sql, argslist, *a, **k):
        raise RuntimeError("falha em postgresql://usr:senha@host:5432/db")

    c.rodar(execute_values=vazar)
    juntas = " ".join(c.mensagens)
    assert "senha" not in juntas
    assert "<redacted>" in juntas


def test_sem_retry_uma_execucao_um_desfecho():
    c = _novo()
    c.target.falha_no_commit = RuntimeError("timeout")
    c.rodar()
    assert c.ordem.count("delete") == 1
    assert c.ordem.count("abrir:source") == 1
    assert c.ordem.count("audit_start") == 1


# ===========================================================================
# Estrutura: nenhuma porta dos fundos
# ===========================================================================
def test_nao_existe_segunda_flag_de_desbloqueio():
    """`--apply` E a confirmacao. Qualquer outro gesto vira habito e some."""
    acoes = {a.dest for a in cli.build_parser()._actions}
    assert acoes == {"help", "channel", "diagnose", "apply"}


def test_nenhuma_variavel_de_ambiente_de_desbloqueio():
    import ast
    from pathlib import Path

    arvore = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    lidas = {
        no.args[0].value
        for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Attribute)
        and no.func.attr == "get"
        and "environ" in ast.unparse(no.func.value)
        and no.args
        and isinstance(no.args[0], ast.Constant)
        and isinstance(no.args[0].value, str)
    }
    assert lidas <= {cli.ENV_TARGET, cli.ENV_SOURCE}


def test_nomes_de_secret_sao_os_oficiais_do_repositorio():
    assert cli.ENV_TARGET == "DATABASE_URL"
    assert cli.ENV_SOURCE == "DATAMART_DATABASE_URL"


def test_exit_codes_sao_distintos():
    codigos = [
        cli.EXIT_OK, cli.EXIT_FALHA, cli.EXIT_LOCK_OCUPADO,
        cli.EXIT_FONTE_NAO_PUBLICAVEL, cli.EXIT_COMMIT_INDETERMINADO,
        cli.EXIT_AUDITORIA_INCOMPLETA, cli.EXIT_PRECONDICAO,
    ]
    assert len(set(codigos)) == len(codigos)


def test_publicacao_usa_as_tabelas_do_contrato(cenario):
    cenario.rodar()
    juntas = " ".join(cenario.target.sqls)
    assert FILA_TABLE in juntas
    assert RUN_TABLE in juntas
