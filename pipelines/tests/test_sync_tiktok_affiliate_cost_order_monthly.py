"""Testes de pipelines/sync_tiktok_affiliate_cost_order_monthly.py — Gate UE2-B.

Usa conexões psycopg2 falsas e leitura de texto-fonte — NENHUM banco real é
tocado, nem o Data Mart nem o Neon. Não há fixture de rede, DSN ou variável de
ambiente de conexão em nenhum teste deste arquivo.

Os testes de concorrência e de transação usam um `Recorder` COMPARTILHADO entre as
conexões falsas, para que a ordem relativa dos eventos das DUAS conexões (Neon e
Data Mart) seja observável. Sem isso não seria possível provar o requisito
central: o advisory lock é adquirido antes da leitura autoritativa do watermark, e
a fotografia da fonte permanece aberta até staging e reconciliação terminarem.

Cada teste corresponde a um item verificável do contrato
docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md §18.8/§18.9, ou a um modo de falha que a
revisão do gate exige provar impossível.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import sync_tiktok_affiliate_cost_order_monthly as sync
from pipelines.connectors.tiktok.connector import BRANDS_IN_SCOPE

MIGRATION_PATH = (
    Path(sync.__file__).resolve().parent.parent
    / "apps" / "api" / "alembic" / "versions"
    / "012_create_fact_tiktok_affiliate_cost_order_monthly.py"
)
MODULE_PATH = Path(sync.__file__).resolve()

CUTOFF = datetime(2026, 8, 21, 0, 3, 27)
ANTERIOR = datetime(2026, 8, 1, 0, 0, 0)
POSTERIOR = datetime(2026, 9, 1, 0, 0, 0)
JULHO = datetime(2026, 7, 1).date()


# ---------------------------------------------------------------------------
# Fakes com ordem observável entre conexões
# ---------------------------------------------------------------------------

class _WouldBlock(RuntimeError):
    """Marca a situação em que o Postgres BLOQUEARIA a segunda execução.

    Um advisory lock real bloqueia; um teste não pode bloquear. Modelamos a espera
    como exceção para poder afirmar o que importa: no instante em que a segunda
    execução está presa no lock, ela ainda NÃO leu o watermark.
    """


class Recorder:
    """Log único e ordenado dos eventos de todas as conexões falsas."""

    def __init__(self):
        self.log = []
        self.locks_held = []

    def add(self, label, kind, sql=None, params=None):
        self.log.append((label, kind, sql, params))

    def sqls(self, label=None):
        return [s for lb, k, s, _ in self.log
                if k == "execute" and (label is None or lb == label)]

    def kinds(self, label=None):
        return [k for lb, k, _, _ in self.log if label is None or lb == label]


class Seq:
    """Valor de regra que muda a cada chamada.

    Necessário porque a MESMA consulta de watermark é emitida duas vezes na
    sessão única — a leitura autoritativa sob o lock e a releitura dentro da
    transação. Sem sequência não seria possível exercitar divergência entre elas.
    """

    def __init__(self, *valores):
        self.valores = list(valores)
        self.i = 0

    def proximo(self):
        v = self.valores[min(self.i, len(self.valores) - 1)]
        self.i += 1
        return v


_CLASSES = (
    # `unlock` antes de `lock`: "pg_advisory_unlock" contém "advisory".
    ("unlock", r"pg_advisory_unlock"),
    ("lock_held", r"FROM\s+pg_locks"),
    ("lock", r"pg_advisory_lock\("),
    ("xact_lock", r"pg_advisory_xact_lock"),
    ("lock_timeout", r"SET (?:LOCAL )?lock_timeout"),
    ("timeout", r"SET LOCAL statement_timeout"),
    ("watermark_for_update", r"AS wm.*FOR UPDATE"),
    ("watermark_read", r"SELECT last_successful_upper_bound AS wm"),
    ("state_insert", r"INSERT INTO \S*_sync_state"),
    ("state_update", r"UPDATE \S*_sync_state"),
    ("state_delete", r"DELETE FROM \S*_sync_state"),
    ("isolation", r"transaction_isolation"),
    ("bounds", r"COUNT\(updated_at\)"),
    ("types", r"GROUP BY transaction_type"),
    ("population", r"nulo_transaction_id"),
    ("touched_keys", r"DISTINCT DATE_TRUNC"),
    ("recompute", r"GROUP BY ref_month, brand"),
    ("detail_totals", r"AS source_row_count\s*\n\s*FROM"),
    ("staging_create", r"CREATE TEMP TABLE"),
    ("staging_insert", r"INSERT INTO stg_ftacom_publish"),
    ("staging_max", r"MAX\(source_max_updated_at\)"),
    ("fact_delete", r"DELETE FROM marts\.fact_tiktok_affiliate_cost_order_monthly(?!_)"),
    ("fact_insert", r"INSERT INTO marts\.fact_tiktok_affiliate_cost_order_monthly \("),
    ("keys_check", r"faltando_no_destino"),
    ("except", r"EXCEPT"),
    ("sign", r"SIGN\("),
    ("nan", r"'NaN'::numeric"),
)


def classify(sql: str) -> str | None:
    for nome, padrao in _CLASSES:
        if re.search(padrao, sql, re.I | re.S):
            return nome
    return None


def events(rec: Recorder) -> list[str]:
    """Fluxo de eventos rotulado por conexão, para asserção de ordem."""
    out = []
    for label, kind, sql, _ in rec.log:
        if kind == "execute":
            nome = classify(sql)
            if nome and nome not in ("timeout", "lock_timeout"):
                out.append(f"{label}.{nome}")
        elif kind in ("open", "commit", "rollback", "close",
                      "autocommit_on", "autocommit_off"):
            out.append(f"{label}.{kind}")
    return out


class FakeCursor:
    """Cursor falso dirigido por padrão de SQL, não por ordem de chamada."""

    def __init__(self, conn):
        self.conn = conn
        # `psycopg2.extras.execute_values` lê `cur.connection.encoding`.
        self.connection = conn
        self._pending = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        if isinstance(sql, bytes):
            sql = sql.decode("utf-8")
        self.conn.rec.add(self.conn.label, "execute", sql, params)

        # Lock consultivo de SESSAO. Locks de sessao e de transacao compartilham
        # o mesmo espaco de chaves, entao o fake trata as duas formas como
        # conflitantes — que e' o comportamento real do Postgres.
        if re.search(r"pg_advisory_(xact_)?lock\(", sql):
            if self.conn.rec.locks_held:
                raise _WouldBlock(
                    "advisory lock ja detido por outra execucao: o Postgres "
                    "bloquearia aqui (ou abortaria por lock_timeout)."
                )
            self.conn.rec.locks_held.append(self.conn.label)
        elif re.search(r"pg_advisory_unlock", sql):
            if self.conn.label in self.conn.rec.locks_held:
                self.conn.rec.locks_held.remove(self.conn.label)

        self._pending = None
        for pat, res in self.conn.rules:
            if re.search(pat, sql, re.I | re.S):
                self._pending = res
                break
        self.rowcount = 0
        for pat, n in self.conn.rowcounts:
            if re.search(pat, sql, re.I | re.S):
                self.rowcount = n
                break

    def mogrify(self, template, args=None):
        """`execute_values` real chama `mogrify` para montar cada tupla de VALUES.

        Implementado para que os testes de publicação exercitem o
        `psycopg2.extras.execute_values` DE VERDADE, em vez de substituí-lo por um
        duplo: a montagem do INSERT da staging é então a mesma de produção.
        """
        txt = template.decode("utf-8") if isinstance(template, bytes) else template
        if args is None:
            return txt.encode("utf-8")
        literais = tuple("NULL" if a is None else f"'{a}'" for a in args)
        return (txt % literais).encode("utf-8")

    def fetchone(self):
        r = self._pending
        if isinstance(r, Seq):
            r = r.proximo()
        if isinstance(r, list):
            return r[0] if r else None
        return r

    def fetchall(self):
        r = self._pending
        if isinstance(r, Seq):
            r = r.proximo()
        if r is None:
            return []
        return r if isinstance(r, list) else [r]

    def close(self):
        self.conn.rec.add(self.conn.label, "close_cursor")


class FakeConn:
    def __init__(self, label="neon", rec=None, rules=(), rowcounts=()):
        self.label = label
        self.rec = rec or Recorder()
        self.rules = list(rules)
        self.rowcounts = list(rowcounts)
        self.session = None
        self.encoding = "UTF8"
        self._autocommit = False
        # `open` NAO e' registrado aqui: o teste constroi a conexao antes de
        # ligar o modulo, e registrar na construcao poria `dm.open` no log antes
        # de `neon.lock`, invertendo a ordem que se quer medir. Quem registra e'
        # a fabrica em `_wire`, no instante em que o modulo de fato abre.

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, valor):
        """Registrado no log: a ORDEM em que o autocommit é desligado é o que
        prova que a transação gravável só existe depois do snapshot."""
        self._autocommit = valor
        self.rec.add(self.label, "autocommit_on" if valor else "autocommit_off")

    def mark_open(self):
        self.rec.add(self.label, "open")
        return self

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.rec.add(self.label, "commit")

    def rollback(self):
        self.rec.add(self.label, "rollback")
        if self.label in self.rec.locks_held:
            self.rec.locks_held.remove(self.label)

    def close(self):
        self.rec.add(self.label, "close")
        if self.label in self.rec.locks_held:
            self.rec.locks_held.remove(self.label)

    def set_session(self, **kwargs):
        self.session = kwargs

    # atalhos de leitura do log, escopados a esta conexão
    def sqls(self):
        return self.rec.sqls(self.label)

    def kinds(self):
        return self.rec.kinds(self.label)


# ---------------------------------------------------------------------------
# Regras padrão de cada lado
# ---------------------------------------------------------------------------

def _linha_agregada(**over):
    base = {
        "ref_month": JULHO, "brand": "apice",
        "affiliate_creator_commission": Decimal("-100.50"),
        "affiliate_partner_commission": None,
        "affiliate_ads_commission": Decimal("0"),
        "source_row_count": 3, "source_max_updated_at": CUTOFF,
    }
    base.update(over)
    return base


def _totais(**over):
    base = {
        "affiliate_creator_commission": Decimal("-100.50"),
        "affiliate_partner_commission": None,
        "affiliate_ads_commission": Decimal("0"),
        "source_row_count": 3,
    }
    base.update(over)
    return base


def dm_rules(total=10, com=None, mx=CUTOFF, tipos=None, rows=None, totais=None,
             keys=None, populacao=None):
    """Regras do lado Data Mart. A ordem importa: `recompute` (com GROUP BY) tem
    de ser testada antes de `detail_totals` (sem GROUP BY)."""
    com = total if com is None else com
    tipos = tipos if tipos is not None else [{"transaction_type": "ORDER", "n": total}]
    rows = rows if rows is not None else [_linha_agregada()]
    totais = totais if totais is not None else _totais()
    keys = keys if keys is not None else [{"ref_month": JULHO, "brand": "apice"}]
    pop = populacao or {
        "lidas": total, "nulo_transaction_id": 0, "nulo_order_create_time": 0,
        "nulo_brand": 0, "nulo_fee_breakdown": 0, "fora_da_fotografia": 0,
        "transaction_ids_distintos": total, "marcas_distintas": 5,
    }
    return [
        (r"transaction_isolation",
         {"isolation": "repeatable read", "read_only": "on"}),
        (r"COUNT\(updated_at\)",
         {"total": total, "com_updated_at": com, "max_updated_at": mx}),
        (r"GROUP BY transaction_type", tipos),
        (r"nulo_transaction_id", pop),
        (r"DISTINCT DATE_TRUNC", keys),
        (r"GROUP BY ref_month, brand", rows),
        (r"AS source_row_count\s*\n\s*FROM", totais),
    ]


def neon_rules(watermark=ANTERIOR, chaves=None, staging_max=CUTOFF,
               except_n=0, sign_n=0, nan_n=0, lock_held=1, watermark_seq=None):
    """Regras da conexão única do destino.

    A ordem importa: `pg_locks` e `faltando_no_destino` vêm antes dos padrões
    genéricos, senão `EXCEPT` capturaria a consulta de chaves.
    """
    chaves = chaves or {"faltando_no_destino": 0, "sobrando_no_destino": 0,
                        "linhas_no_escopo": 1, "linhas_na_staging": 1}
    wm = (watermark_seq if watermark_seq is not None
          else (None if watermark is None else {"wm": watermark}))
    return [
        (r"pg_advisory_unlock", {"liberado": True}),
        (r"FROM\s+pg_locks", {"n": lock_held}),
        (r"pg_advisory_lock", {"pg_advisory_lock": ""}),
        # Casa tanto a leitura autoritativa quanto a releitura com FOR UPDATE.
        (r"SELECT last_successful_upper_bound AS wm", wm),
        (r"MAX\(source_max_updated_at\)", {"mx": staging_max}),
        (r"faltando_no_destino", chaves),
        (r"SIGN\(", {"n": sign_n}),
        (r"'NaN'::numeric", {"n": nan_n}),
        (r"EXCEPT", {"n": except_n}),
    ]


def neon_rowcounts(fact_delete=1, fact_insert=1, state=1):
    return [
        (r"DELETE FROM \S*_sync_state", state),
        (r"UPDATE \S*_sync_state", state),
        (r"INSERT INTO \S*_sync_state", state),
        (r"DELETE FROM marts\.fact_tiktok_affiliate_cost_order_monthly(?!_)",
         fact_delete),
        (r"INSERT INTO marts\.fact_tiktok_affiliate_cost_order_monthly \(",
         fact_insert),
    ]


def _wire(monkeypatch, neon, dm):
    """Liga as fábricas do módulo às conexões falsas.

    O evento `open` é registrado AQUI, no instante em que o módulo pede a
    conexão — não na construção do fake. É o que torna observável a ordem entre
    `neon.lock`, `neon.watermark_read` e `dm.open`.

    `_neon_session` é sempre ligado, mesmo nos testes de diagnóstico: sem isso o
    módulo chamaria o `psycopg2.connect` real.
    """
    monkeypatch.setattr(sync, "_get_neon_url", lambda: "postgresql://neon")
    monkeypatch.setattr(sync, "_get_datamart_url", lambda: "postgresql://dm")
    monkeypatch.setattr(sync, "_neon_session", lambda url: neon.mark_open())
    monkeypatch.setattr(sync, "_neon_readonly", lambda url: neon.mark_open())
    monkeypatch.setattr(sync, "_datamart_snapshot", lambda url: dm.mark_open())


#: SQL que só existe se algo foi efetivamente escrito no destino.
_ESCRITA = ("staging_create", "staging_insert", "fact_delete", "fact_insert",
            "state_insert", "state_update", "state_delete")


def sem_escrita(rec, label="neon") -> bool:
    """Nenhum SQL de escrita foi emitido na conexão do destino.

    Substitui a asserção antiga `rec.sqls("neon") == []`: com sessão única a
    conexão do destino legitimamente emite lock e leitura do watermark, então
    "conexão não usada" deixou de ser o critério. O critério é "nada escrito".
    """
    return not any(classify(s) in _ESCRITA for s in rec.sqls(label))


_UNSET = object()


def _apply_env(monkeypatch, watermark=ANTERIOR, lock_wm=_UNSET,
               neon_kw=None, dm_kw=None, rowcounts=None, lock_held=1):
    """Ambiente de uma execução `--apply`: **UMA** conexão de destino, um Recorder.

    A sessão única é o ponto do desenho: a mesma conexão adquire o lock, lê o
    watermark e publica. `lock_wm` diverge deliberadamente o watermark da
    releitura em relação ao da leitura sob o lock, para exercitar
    `assert_watermark_unchanged`. `lock_held=0` simula perda do advisory lock.
    """
    rec = Recorder()
    neon_kw = dict(neon_kw or {})
    neon_kw.setdefault("watermark", watermark)
    if lock_wm is not _UNSET:
        # 1ª leitura (sob o lock) devolve `lock_wm`; a releitura devolve o outro.
        neon_kw["watermark_seq"] = Seq(
            None if lock_wm is None else {"wm": lock_wm},
            None if watermark is None else {"wm": watermark},
        )
    neon_kw["lock_held"] = lock_held
    neon = FakeConn("neon", rec, neon_rules(**neon_kw),
                    neon_rowcounts() if rowcounts is None else rowcounts)
    dm = FakeConn("dm", rec, dm_rules(**(dm_kw or {})))
    _wire(monkeypatch, neon, dm)
    return rec, neon, dm


def _snapshot_ok(rows=None, totals=None, cutoff=CUTOFF, empty=False):
    rows = [_linha_agregada()] if rows is None else rows
    totals = _totais() if totals is None else totals
    return sync.SourceSnapshot(
        cutoff=cutoff, lower_bound=ANTERIOR, rows=rows, detail_totals=totals,
        observed_types={"ORDER": 3}, source_empty=empty,
        source_bounds={"total": 10, "empty": empty}, boundary_a={},
    )


def _snapshot_vazio_de_chaves():
    return sync.SourceSnapshot(
        cutoff=CUTOFF, lower_bound=ANTERIOR, rows=[],
        detail_totals=sync._totais_vazios(), observed_types={"ORDER": 0},
        source_empty=False, source_bounds={"total": 10, "empty": False},
        boundary_a={},
    )


def _op_execute_sql(texto: str) -> str:
    """SQL que a migration realmente EXECUTA, sem docstring nem comentário.

    Testar o texto bruto do arquivo mediria a prosa: a docstring explica por que
    `IF NOT EXISTS` e `affiliate_cost_total` estão ausentes, e mencioná-los para
    explicar a ausência não é usá-los.
    """
    blocos = re.findall(r'op\.execute\(\s*"""(.*?)"""\s*\)', texto, re.S)
    return re.sub(r"--[^\n]*", "", "\n".join(blocos))


# ===========================================================================
# F1 — SERIALIZAÇÃO REAL DA EXECUÇÃO
# ===========================================================================

def test_f1_lock_e_adquirido_antes_da_leitura_do_watermark(monkeypatch):
    """O defeito original: ler o watermark antes do lock permitia que duas
    execuções observassem o mesmo valor, abrissem fotografias diferentes e
    publicassem em qualquer ordem — a mais antiga podendo sobrescrever a mais
    nova enquanto o watermark ficava com o valor novo."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    f = events(rec)
    assert f.index("neon.lock") < f.index("neon.watermark_read")


def test_f1_lock_usa_pg_advisory_lock_de_sessao_nao_o_transacional(monkeypatch):
    """`pg_advisory_xact_lock` amarrava a exclusão mútua a uma transação gravável
    que ficava ociosa durante toda a leitura da fonte — e o destino encerra
    sessões ociosas em transação em 300 s."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    todos = rec.sqls()
    assert any("pg_advisory_lock(" in s for s in todos)
    assert not any("pg_advisory_xact_lock" in s for s in todos)


def test_f1_lock_usa_a_mesma_chave_reservada(monkeypatch):
    """Mesma chave da versão anterior: locks consultivos de sessão e de transação
    compartilham o espaço de chaves, então as duas versões conflitam entre si e
    não podem se sobrepor durante o rollout."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    chamadas = [(s, p) for lb, k, s, p in rec.log
                if k == "execute" and "pg_advisory_lock(" in s]
    assert len(chamadas) == 1
    assert chamadas[0][1] == (sync.ADVISORY_LOCK_KEY,)
    unlocks = [(s, p) for lb, k, s, p in rec.log
               if k == "execute" and "pg_advisory_unlock" in s]
    assert len(unlocks) == 1
    assert unlocks[0][1] == (sync.ADVISORY_LOCK_KEY,)


def test_f1_conexao_comeca_em_autocommit_sem_transacao_ociosa(monkeypatch):
    """Em autocommit o psycopg2 não abre transação implícita, então a sessão não
    fica em `idle in transaction` durante a leitura da fonte."""
    monkeypatch.setattr(sync.psycopg2, "connect",
                        lambda url, **kw: FakeConn("neon"))
    conn = sync._neon_session("postgresql://x")
    assert conn.autocommit is True
    assert "commit" not in conn.kinds()
    assert "rollback" not in conn.kinds()


def test_f1_nao_existe_segunda_fabrica_de_conexao_gravavel():
    """Garantia ESTRUTURAL: sem função para abrir uma segunda conexão gravável,
    o defeito não volta por descuido. `_neon_writable` foi removida."""
    assert not hasattr(sync, "_neon_writable")
    assert not hasattr(sync, "_neon_lock_session")
    fabricas = sorted(n for n in dir(sync)
                      if n.startswith("_neon") and callable(getattr(sync, n)))
    assert fabricas == ["_neon_readonly", "_neon_session"]


def test_f1_apply_abre_exatamente_uma_conexao_de_destino(monkeypatch):
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    assert [(lb, k) for lb, k, _, _ in rec.log if k == "open"] == [
        ("neon", "open"), ("dm", "open")
    ]


def test_f1_mesma_sessao_detem_o_lock_e_publica(monkeypatch):
    """Prova central: lock, leitura do watermark e publicação saem todos da MESMA
    conexão. Com duas conexões era possível perder o lock numa (queda) e
    continuar escrevendo pela outra."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    rotulos = {
        lb for lb, k, s, _ in rec.log
        if k == "execute" and classify(s) in (
            "lock", "unlock", "lock_held", "watermark_read",
            "watermark_for_update", "staging_create", "fact_insert",
            "state_update",
        )
    }
    assert rotulos == {"neon"}


def test_f1_autocommit_desligado_somente_apos_snapshot_e_validacao(monkeypatch):
    """A transação gravável nasce depois de a fonte ter sido lida por inteiro."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    f = events(rec)
    i_off = f.index("neon.autocommit_off")
    ultima_fonte = max(i for i, e in enumerate(f)
                       if e.startswith("dm.")
                       and e not in ("dm.open", "dm.rollback", "dm.close"))
    assert ultima_fonte < f.index("neon.lock_held") < i_off
    assert i_off < f.index("neon.staging_create")
    assert f.index("neon.watermark_read") < ultima_fonte


def test_f1_posse_do_lock_e_confirmada_antes_de_transacionar(monkeypatch):
    """Se a sessão perdeu o lock, nada é publicado — e não há segunda conexão
    para onde escapar."""
    rec, neon, dm = _apply_env(monkeypatch, lock_held=0)
    with pytest.raises(RuntimeError, match="NAO detem mais o advisory lock"):
        sync.run("incremental", "run:1", apply=True)
    assert "neon.autocommit_off" not in events(rec)
    assert not any(classify(s) in ("staging_create", "fact_insert", "state_update")
                   for s in rec.sqls("neon"))
    assert "commit" not in neon.kinds()


def test_f1_conexao_caida_na_leitura_da_fonte_nao_abre_outra(monkeypatch):
    """Requisito 10: a próxima operação na mesma conexão falha, e o processo não
    abre outra conexão gravável para continuar."""
    rec, neon, dm = _apply_env(monkeypatch)
    original = neon.cursor
    chamadas = {"n": 0}

    def cursor_que_morre():
        chamadas["n"] += 1
        if chamadas["n"] == 1:          # leitura autoritativa do watermark
            return original()
        raise RuntimeError("server closed the connection unexpectedly")

    neon.cursor = cursor_que_morre
    with pytest.raises(RuntimeError, match="server closed the connection"):
        sync.run("incremental", "run:1", apply=True)
    assert len([lb for lb, k, _, _ in rec.log if k == "open" and lb == "neon"]) == 1
    assert "commit" not in neon.kinds()
    assert not any(classify(s) in ("staging_create", "fact_insert", "state_update")
                   for s in rec.sqls("neon"))


def test_f1_releitura_do_watermark_usa_for_update(monkeypatch):
    """`FOR UPDATE` protege a linha EXISTENTE. Sobre linha inexistente não trava
    nada — quem protege a primeira carga é o advisory lock da mesma sessão."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    leituras = [s for s in rec.sqls("neon")
                if "last_successful_upper_bound AS wm" in s]
    assert len(leituras) == 2
    assert "FOR UPDATE" not in leituras[0]      # autoritativa, em autocommit
    assert leituras[1].rstrip().endswith("FOR UPDATE")


def test_f1_primeira_carga_sem_linha_segue_serializada_pelo_advisory_lock(monkeypatch):
    """Sem linha de watermark, `FOR UPDATE` não tem tupla a travar. A execução
    ainda atravessa lock → posse confirmada → transação → publicação."""
    rec, neon, dm = _apply_env(monkeypatch, watermark=None)
    rel = sync.run("full", "run:1", apply=True)
    f = events(rec)
    assert f.index("neon.lock") < f.index("neon.lock_held")
    assert f.index("neon.lock_held") < f.index("neon.autocommit_off")
    assert f.index("neon.autocommit_off") < f.index("neon.state_insert")
    assert rel["watermark"] == sync.WATERMARK_ADVANCED
    assert "commit" in neon.kinds()


def test_f1_lock_tem_timeout_finito_sem_retry(monkeypatch):
    """Dois `lock_timeout`: um de SESSÃO na aquisição do advisory lock (em
    autocommit não há transação a que prender um `SET LOCAL`), e um `SET LOCAL`
    na transação gravável, para o lock de linha do `sync_state`."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    sets = [s.strip() for s in rec.sqls("neon") if "lock_timeout" in s]
    assert len(sets) == 2
    assert not sets[0].startswith("SET LOCAL")
    assert sets[1].startswith("SET LOCAL")
    for s in sets:
        assert sync.LOCK_TIMEOUT in s
        assert "= '0'" not in s and "= 0" not in s


def test_f1_nenhuma_transacao_gravavel_durante_a_leitura_da_fonte(monkeypatch):
    """PROVA CENTRAL: a transação gravável só nasce DEPOIS de todas as leituras
    da fonte. Antes ficava aberta e ociosa durante a leitura, exposta ao
    `idle_in_transaction_session_timeout` de 300 s do destino.

    O marcador não é mais `neon.open` — com sessão única a conexão abre no
    início, ainda em autocommit. O marcador correto é `neon.autocommit_off`."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    f = events(rec)
    inicio_transacao = f.index("neon.autocommit_off")
    ultima_consulta_fonte = max(
        i for i, e in enumerate(f)
        if e.startswith("dm.") and e not in ("dm.open", "dm.rollback", "dm.close")
    )
    fim_fotografia = max(i for i, e in enumerate(f) if e.startswith("dm."))
    assert inicio_transacao > ultima_consulta_fonte
    # a fotografia só fecha depois da publicação (18.8.3 regra 4)
    assert fim_fotografia > inicio_transacao
    # e nada de escrita antes de a transação existir
    escritas = [i for i, e in enumerate(f)
                if e.startswith("neon.") and e.split(".", 1)[1] in _ESCRITA]
    assert all(i > inicio_transacao for i in escritas)


def test_f1_ordem_transacional_autorizada_do_apply(monkeypatch):
    """Ordem inteira: lock → watermark → fotografia → validação em memória →
    transação gravável → releitura do watermark → publicação → commit →
    fechamento da fotografia → unlock."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    f = events(rec)
    ordem = [
        "neon.open", "neon.lock", "neon.watermark_read",
        "dm.open", "dm.isolation", "dm.bounds", "dm.types", "dm.population",
        "dm.touched_keys", "dm.recompute", "dm.detail_totals",
        "neon.lock_held", "neon.autocommit_off", "neon.watermark_for_update",
        "neon.staging_create", "neon.staging_insert", "neon.staging_max",
        "neon.fact_delete", "neon.fact_insert", "neon.keys_check",
        "neon.sign", "neon.nan", "neon.state_update",
        "neon.commit", "neon.autocommit_on",
        "dm.rollback", "dm.close",
        "neon.unlock", "neon.close",
    ]
    posicoes = [f.index(e) for e in ordem]
    assert posicoes == sorted(posicoes), f
    # uma única conexão de destino, aberta uma única vez
    assert f.count("neon.open") == 1


def test_f1_watermark_relido_e_comparado_antes_de_publicar(monkeypatch):
    """Guardrail da janela em que a conexão de lock caia: se o watermark mudou
    entre a leitura sob o lock e a transação de escrita, nada é publicado."""
    rec, neon, dm = _apply_env(monkeypatch, watermark=ANTERIOR,
                                     lock_wm=datetime(2026, 7, 1))
    with pytest.raises(RuntimeError, match="watermark mudou"):
        sync.run("incremental", "run:1", apply=True)
    assert "commit" not in neon.kinds()
    assert "rollback" in neon.kinds()
    assert not any(classify(s) in ("fact_delete", "fact_insert", "state_update")
                   for s in rec.sqls("neon"))
    assert rec.locks_held == []


def test_f1_segunda_execucao_nao_observa_watermark_sem_o_lock(monkeypatch):
    """Execução B, com A detendo o lock, não chega a ler o watermark nem a abrir
    a fotografia."""
    rec = Recorder()
    a = FakeConn("A", rec, neon_rules())
    a.cursor().execute("SELECT pg_advisory_lock(%s)", (sync.ADVISORY_LOCK_KEY,))
    assert rec.locks_held == ["A"]

    b_neon = FakeConn("B", rec, neon_rules(), neon_rowcounts())
    b_dm = FakeConn("Bdm", rec, dm_rules())
    _wire(monkeypatch, b_neon, b_dm)

    with pytest.raises(_WouldBlock):
        sync.run("incremental", "run:B", apply=True)

    assert any("pg_advisory_lock(" in s for s in rec.sqls("B"))
    assert not any(classify(s) == "watermark_read" for s in rec.sqls("B"))
    assert rec.sqls("Bdm") == []                     # nem abriu a fotografia
    assert "autocommit_off" not in b_neon.kinds()    # nem transacionou


def test_f1_lock_conflita_entre_versao_de_sessao_e_transacional():
    """Compatibilidade de rollout: as duas formas disputam a mesma chave."""
    rec = Recorder()
    antiga = FakeConn("antiga", rec, [])
    antiga.cursor().execute("SELECT pg_advisory_xact_lock(%s)",
                            (sync.ADVISORY_LOCK_KEY,))
    nova = FakeConn("nova", rec, [])
    with pytest.raises(_WouldBlock):
        nova.cursor().execute("SELECT pg_advisory_lock(%s)",
                              (sync.ADVISORY_LOCK_KEY,))


def test_f1_watermark_usado_pelo_incremental_vem_de_sob_o_lock(monkeypatch):
    rec, neon, dm = _apply_env(monkeypatch, watermark=ANTERIOR)
    visto = {}
    original = sync.read_source_snapshot

    def espiao(conn, lb):
        visto["lb"] = lb
        return original(conn, lb)

    monkeypatch.setattr(sync, "read_source_snapshot", espiao)
    sync.run("incremental", "run:1", apply=True)
    assert visto["lb"] == ANTERIOR
    assert len([s for s in rec.sqls("neon") if classify(s) == "watermark_read"]) == 1


def test_f1_publicacao_nao_readquire_um_segundo_lock(monkeypatch):
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("full", "run:1", apply=True)
    assert len([s for s in rec.sqls() if "pg_advisory_lock(" in s]) == 1
    assert len([s for s in rec.sqls() if "pg_advisory_unlock" in s]) == 1
    assert not any("pg_advisory" in s for s in rec.sqls("dm"))


def test_f1_erro_na_fonte_libera_o_lock_e_nao_abre_conexao_gravavel(monkeypatch):
    """Se a leitura da fonte falhar, zero DDL/DML — por construção, não por
    rollback: a transação gravável nem chega a existir."""
    rec, neon, dm = _apply_env(
        monkeypatch, dm_kw={"tipos": [{"transaction_type": "ADJUST", "n": 2}]})
    with pytest.raises(RuntimeError, match="transaction_type fora da allowlist"):
        sync.run("incremental", "run:1", apply=True)
    assert sem_escrita(rec)
    assert "neon.autocommit_off" not in events(rec)
    assert any("pg_advisory_unlock" in s for s in rec.sqls("neon"))
    assert rec.locks_held == []


def test_f1_erro_na_publicacao_faz_rollback_e_libera_o_lock(monkeypatch):
    rec, neon, dm = _apply_env(monkeypatch, neon_kw={"except_n": 1})
    with pytest.raises(RuntimeError, match="EXCEPT bidirecional divergiu"):
        sync.run("incremental", "run:1", apply=True)
    assert "rollback" in neon.kinds()
    assert "commit" not in neon.kinds()
    assert rec.locks_held == []
    assert not any(classify(s) == "state_update" for s in rec.sqls("neon"))


def test_f1_unlock_ocorre_em_todos_os_caminhos(monkeypatch):
    """Sucesso, erro da fonte, erro de reconciliação e erro de watermark."""
    casos = [
        ("sucesso", {}, None),
        ("erro_fonte", {"dm_kw": {"tipos": [{"transaction_type": "X", "n": 1}]}},
         "transaction_type"),
        ("erro_reconciliacao", {"neon_kw": {"sign_n": 1}}, "sinal divergiu"),
        ("erro_watermark", {"lock_wm": datetime(2026, 7, 1)}, "watermark mudou"),
    ]
    for nome, kw, erro in casos:
        rec, neon, dm = _apply_env(monkeypatch, **kw)
        if erro:
            with pytest.raises(RuntimeError, match=erro):
                sync.run("incremental", f"run:{nome}", apply=True)
        else:
            sync.run("incremental", f"run:{nome}", apply=True)
        assert any("pg_advisory_unlock" in s for s in rec.sqls("neon")), nome
        assert rec.locks_held == [], nome
        assert "close" in neon.kinds(), nome


def test_f1_lock_nao_e_liberado_antes_do_commit(monkeypatch):
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("incremental", "run:1", apply=True)
    f = events(rec)
    assert f.index("neon.commit") < f.index("neon.unlock")


def test_f1_lock_nao_e_liberado_antes_do_rollback(monkeypatch):
    rec, neon, dm = _apply_env(monkeypatch, neon_kw={"except_n": 1})
    with pytest.raises(RuntimeError):
        sync.run("incremental", "run:1", apply=True)
    f = events(rec)
    assert f.index("neon.rollback") < f.index("neon.unlock")


def test_f1_release_advisory_lock_nunca_levanta():
    """Roda em `finally`, possivelmente com exceção em voo: levantar aqui
    mascararia a causa real. A queda da conexão libera o lock no servidor."""
    class Quebrada:
        def cursor(self):
            raise RuntimeError("conexao morta")

    assert sync.release_advisory_lock(Quebrada()) is False


def test_f1_fotografia_antiga_nao_publica_depois_de_estado_mais_novo(monkeypatch):
    """Cutoff MENOR que o watermark registrado: não pode publicar."""
    rec, neon, dm = _apply_env(
        monkeypatch, watermark=POSTERIOR,
        neon_kw={"staging_max": ANTERIOR},
        dm_kw={"mx": ANTERIOR,
               "rows": [_linha_agregada(source_max_updated_at=ANTERIOR)]})
    with pytest.raises(RuntimeError, match="mais antiga que o watermark"):
        sync.run("incremental", "run:1", apply=True)
    assert "commit" not in neon.kinds()
    assert "rollback" in neon.kinds()
    assert rec.locks_held == []


def test_f1_noop_incremental_usa_a_mesma_transacao_protegida(monkeypatch):
    """Nenhuma chave tocada continua passando pelo lock, pela fotografia e pela
    reconciliação — não existe caminho de atalho."""
    rec, neon, dm = _apply_env(
        monkeypatch,
        neon_kw={"chaves": {"faltando_no_destino": 0, "sobrando_no_destino": 0,
                            "linhas_no_escopo": 0, "linhas_na_staging": 0},
                 "staging_max": None},
        dm_kw={"keys": [], "rows": [], "totais": sync._totais_vazios()},
        rowcounts=neon_rowcounts(fact_delete=0, fact_insert=0),
    )
    rel = sync.run("incremental", "run:1", apply=True)
    f = events(rec)
    assert f.index("neon.lock") < f.index("neon.keys_check") < f.index("neon.commit")
    assert rel["publicacao"]["published"] == 0
    assert rel["watermark"] == sync.WATERMARK_ADVANCED


def test_f1_diagnostico_nao_toma_lock_e_nao_escreve(monkeypatch):
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(), neon_rowcounts())
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)

    rel = sync.run("incremental", "run:1", apply=False)
    assert not any("pg_advisory" in s for s in rec.sqls())
    assert "autocommit_off" not in neon.kinds()   # nunca transacionou
    assert sem_escrita(rec)
    for proibido in ("INSERT", "UPDATE", "DELETE", "CREATE"):
        assert not any(proibido in s.upper() for s in rec.sqls("neon"))
    assert "commit" not in neon.kinds()
    assert rel["resultado"] == "diagnostico; nada escrito"


# --- correção estreita: full dry-run não toca o Neon -----------------------

def _explode(*a, **kw):
    raise AssertionError("o Neon nao pode ser acessado no full dry-run")


def test_full_dry_run_nao_chama_get_neon_url(monkeypatch):
    """`full` ignora o watermark por definição, então ler o state era dependência
    indevida: fazia o diagnóstico do backfill inicial depender de uma tabela que
    só existe DEPOIS da migration — impossível justamente no primeiro full."""
    rec = Recorder()
    dm = FakeConn("dm", rec, dm_rules())
    monkeypatch.setattr(sync, "_get_datamart_url", lambda: "postgresql://dm")
    monkeypatch.setattr(sync, "_datamart_snapshot", lambda url: dm.mark_open())
    monkeypatch.setattr(sync, "_get_neon_url", _explode)

    rel = sync.run("full", "run:1", apply=False)
    assert rel["neon"] == "nao acessado"
    assert rel["resultado"] == "diagnostico; nada escrito"


def test_full_dry_run_nao_abre_neon_readonly(monkeypatch):
    rec = Recorder()
    dm = FakeConn("dm", rec, dm_rules())
    monkeypatch.setattr(sync, "_get_datamart_url", lambda: "postgresql://dm")
    monkeypatch.setattr(sync, "_datamart_snapshot", lambda url: dm.mark_open())
    monkeypatch.setattr(sync, "_neon_readonly", _explode)

    sync.run("full", "run:1", apply=False)


def test_full_dry_run_nao_executa_read_watermark(monkeypatch):
    rec = Recorder()
    dm = FakeConn("dm", rec, dm_rules())
    monkeypatch.setattr(sync, "_get_datamart_url", lambda: "postgresql://dm")
    monkeypatch.setattr(sync, "_datamart_snapshot", lambda url: dm.mark_open())
    monkeypatch.setattr(sync, "read_watermark", _explode)

    rel = sync.run("full", "run:1", apply=False)
    assert "nao consultado" in rel["watermark_anterior"]
    assert not any(classify(s) == "watermark_read" for s in rec.sqls())


def test_full_dry_run_acessa_somente_o_data_mart(monkeypatch):
    rec = Recorder()
    dm = FakeConn("dm", rec, dm_rules())
    monkeypatch.setattr(sync, "_get_datamart_url", lambda: "postgresql://dm")
    monkeypatch.setattr(sync, "_datamart_snapshot", lambda url: dm.mark_open())
    monkeypatch.setattr(sync, "_get_neon_url", _explode)
    monkeypatch.setattr(sync, "_neon_readonly", _explode)
    monkeypatch.setattr(sync, "_neon_session", _explode)

    sync.run("full", "run:1", apply=False)
    assert {lb for lb, *_ in rec.log} == {"dm"}
    # e nada além de leitura na fotografia
    for s in rec.sqls("dm"):
        assert s.strip().upper().startswith(("SELECT", "SET LOCAL"))


def test_incremental_dry_run_continua_exigindo_o_estado_persistido(monkeypatch):
    """Sem linha, falha exigindo `full`. Nunca cai silenciosamente para full:
    tratar 'sem state' como 'lê a história inteira' transformaria um erro de
    configuração em backfill acidental."""
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(watermark=None))
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)

    with pytest.raises(RuntimeError, match="backfill integral"):
        sync.run("incremental", "run:1", apply=False)
    assert rec.sqls("dm") == []          # nem abriu a fotografia


def test_incremental_dry_run_propaga_erro_de_tabela_ausente(monkeypatch):
    """Tabela sync_state inexistente também tem de falhar, e falhar dizendo o
    que é — não virar full."""
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules())
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)

    def sem_tabela(cur):
        raise RuntimeError('relation "marts.fact_..._sync_state" does not exist')

    monkeypatch.setattr(sync, "read_watermark", sem_tabela)
    with pytest.raises(RuntimeError, match="does not exist"):
        sync.run("incremental", "run:1", apply=False)
    assert rec.sqls("dm") == []


@pytest.mark.parametrize("mode", ["full", "incremental"])
def test_nenhum_caminho_sem_apply_abre_conexao_gravavel(monkeypatch, mode):
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules())
    dm = FakeConn("dm", rec, dm_rules())
    monkeypatch.setattr(sync, "_get_neon_url", lambda: "postgresql://neon")
    monkeypatch.setattr(sync, "_get_datamart_url", lambda: "postgresql://dm")
    monkeypatch.setattr(sync, "_neon_readonly", lambda url: neon.mark_open())
    monkeypatch.setattr(sync, "_datamart_snapshot", lambda url: dm.mark_open())
    monkeypatch.setattr(sync, "_neon_session", _explode)

    rel = sync.run(mode, "run:1", apply=False)
    assert rel["applied"] is False


def test_apply_continua_lendo_o_watermark_sob_lock(monkeypatch):
    """Contraprova da correção do dry-run: ela vale só sem `--apply`. O apply de
    `full` continua tomando lock e lendo o state, porque escreve o watermark."""
    rec, neon, dm = _apply_env(monkeypatch)
    sync.run("full", "run:1", apply=True)
    f = events(rec)
    assert f.index("neon.lock") < f.index("neon.watermark_read") < f.index("dm.open")


def test_full_dry_run_nao_abre_a_conexao_de_lock(monkeypatch):
    """A correção do dry-run vale também para a conexão dedicada de lock: `full`
    sem `--apply` não abre nenhuma conexão com o destino."""
    rec = Recorder()
    dm = FakeConn("dm", rec, dm_rules())
    monkeypatch.setattr(sync, "_get_datamart_url", lambda: "postgresql://dm")
    monkeypatch.setattr(sync, "_datamart_snapshot", lambda url: dm.mark_open())
    monkeypatch.setattr(sync, "_get_neon_url", _explode)
    monkeypatch.setattr(sync, "_neon_session", _explode)
    monkeypatch.setattr(sync, "_neon_readonly", _explode)
    monkeypatch.setattr(sync, "_neon_session", _explode)

    rel = sync.run("full", "run:1", apply=False)
    assert rel["neon"] == "nao acessado"
    assert {lb for lb, *_ in rec.log} == {"dm"}


def test_apply_incremental_sem_watermark_recusa_antes_de_abrir_a_fonte(monkeypatch):
    """Recusa acontece sob o lock, antes da fotografia: nunca cai para full."""
    rec, neon, dm = _apply_env(monkeypatch, watermark=None)
    with pytest.raises(RuntimeError, match="backfill integral"):
        sync.run("incremental", "run:1", apply=True)
    assert rec.sqls("dm") == []
    assert sem_escrita(rec)
    assert rec.locks_held == []
    assert any("pg_advisory_unlock" in s for s in rec.sqls("neon"))


def test_erro_ao_adquirir_o_lock_nao_expoe_topologia(monkeypatch, capsys):
    """Falha de conexão na aquisição do lock é classificada, nunca ecoada."""
    def explode_lock(url):
        raise RuntimeError(
            'connection to server at "10.9.9.9", port 5432 failed: timeout')

    monkeypatch.setattr(sync, "_get_neon_url", lambda: "postgresql://neon")
    monkeypatch.setattr(sync, "_neon_session", explode_lock)
    assert sync.main(["--mode", "full", "--apply"]) == 2
    err = capsys.readouterr().err
    for proibido in ("10.9.9.9", "5432", "postgresql://", "neon"):
        assert proibido not in err


def test_modulo_nao_tem_retry_sleep_nem_backoff():
    """Verificação ESTRUTURAL, não textual: a docstring do módulo diz "zero
    backoff" para explicar a ausência, e um scan de texto acusaria essa própria
    frase. O que importa é o que o código faz."""
    arvore = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))

    chamados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            f = no.func
            chamados.add(f.attr if isinstance(f, ast.Attribute)
                         else getattr(f, "id", ""))
    for suspeito in chamados:
        baixo = suspeito.lower()
        assert "sleep" not in baixo, suspeito
        assert "retry" not in baixo, suspeito
        assert "backoff" not in baixo, suspeito

    # Nenhum laço `while`: é a forma que um retry tomaria.
    assert not [n for n in ast.walk(arvore) if isinstance(n, ast.While)]
    # Nenhum decorador de retry.
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert not no.decorator_list, no.name


def test_source_statement_timeout_permanece_600s():
    """Deliberadamente NÃO reduzido para 180s. `statement_timeout` é POR
    STATEMENT, e `read_source_snapshot` executa sete consultas sequenciais —
    reduzir a constante não produziria teto acumulado, apenas a aparência de um.
    Depois de o lock virar de sessão, este valor voltou a ser exclusivamente um
    limite de proteção da FONTE, sem relação com o timeout do destino."""
    assert sync.SOURCE_STATEMENT_TIMEOUT == "600s"
    assert sync.LOCK_TIMEOUT == "30s"


def test_f1_datamart_e_repeatable_read_read_only_sem_autocommit(monkeypatch):
    capturado = {}
    monkeypatch.setattr(sync.psycopg2, "connect",
                        lambda url, **kw: capturado.setdefault("c", FakeConn("dm")))
    sync._datamart_snapshot("postgresql://x")
    assert capturado["c"].session == {
        "isolation_level": "REPEATABLE READ", "readonly": True, "autocommit": False
    }


# ===========================================================================
# F2 — ESCOPO DA RECONCILIAÇÃO
# ===========================================================================

def test_f2_incremental_projeta_o_destino_pelas_chaves_da_staging():
    escopo = sync.target_scope("incremental", sync.STAGING_TABLE, sync.TARGET_TABLE)
    assert "JOIN stg_ftacom_publish s" in escopo
    assert "t.ref_month = s.ref_month" in escopo
    assert "t.brand = s.brand" in escopo


def test_f2_full_compara_o_destino_inteiro():
    escopo = sync.target_scope("full", sync.STAGING_TABLE, sync.TARGET_TABLE)
    assert escopo == sync.TARGET_TABLE
    assert "JOIN" not in escopo


def test_f2_escopo_nao_interpola_valores_de_chave():
    """As chaves vêm da própria staging via join, nunca de lista de valores
    montada no texto do SQL."""
    escopo = sync.target_scope("incremental", sync.STAGING_TABLE, sync.TARGET_TABLE)
    assert "apice" not in escopo
    assert "2026" not in escopo
    assert "IN (" not in escopo


def test_f2_incremental_com_dez_meses_reconcilia_somente_o_mes_tocado(monkeypatch):
    """Antes da correção, `destino EXCEPT staging` encontrava por construção as
    nove linhas históricas não tocadas e reprovava toda execução correta."""
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(), neon_rowcounts())
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)

    sync.run("incremental", "run:1", apply=True)
    excepts = [s for s in rec.sqls("neon")
               if "EXCEPT" in s and "faltando_no_destino" not in s]
    assert excepts, "nenhuma consulta EXCEPT emitida"
    for s in excepts:
        assert "JOIN stg_ftacom_publish s" in s   # destino projetado, não inteiro


def test_f2_full_nao_projeta_o_destino(monkeypatch):
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(), neon_rowcounts())
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)

    sync.run("full", "run:1", apply=True)
    excepts = [s for s in rec.sqls("neon")
               if "EXCEPT" in s and "faltando_no_destino" not in s]
    for s in excepts:
        assert "JOIN stg_ftacom_publish s" not in s


def test_f2_except_permanece_bidirecional():
    conn = FakeConn("neon", None, [(r"EXCEPT", {"n": 0})])
    sync.except_both_ways(conn.cursor(), "stg", "marts.t")
    sqls = conn.sqls()
    assert len(sqls) == 2
    assert "FROM stg EXCEPT" in re.sub(r"\s+", " ", sqls[0])
    assert "FROM marts.t AS escopo_b EXCEPT" in re.sub(r"\s+", " ", sqls[1])


def test_f2_except_compara_somente_colunas_de_negocio():
    """`synced_at`/`source_run_id` são gerados no destino e sempre difeririam:
    incluí-los transformaria a comparação em ruído em vez de prova."""
    conn = FakeConn("neon", None, [(r"EXCEPT", {"n": 0})])
    sync.except_both_ways(conn.cursor(), "stg", "marts.t")
    sql = conn.sqls()[0]
    assert "synced_at" not in sql
    assert "source_run_id" not in sql
    for c in sync.COMPONENT_COLUMNS:
        assert c in sql
    assert "source_max_updated_at" in sql


def test_f2_linha_divergente_em_chave_tocada_reprova(monkeypatch):
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(except_n=1), neon_rowcounts())
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)

    with pytest.raises(RuntimeError, match="EXCEPT bidirecional divergiu"):
        sync.run("incremental", "run:1", apply=True)
    assert "commit" not in neon.kinds()


def test_f2_linha_extra_no_destino_para_chave_tocada_reprova():
    """Uma linha duplicada tem a MESMA chave, então nenhum EXCEPT de chaves a
    encontraria. Só a cardinalidade revela — é para isso que ela existe."""
    conn = FakeConn("neon", None, [
        (r"faltando_no_destino", {"faltando_no_destino": 0, "sobrando_no_destino": 0,
                                  "linhas_no_escopo": 2, "linhas_na_staging": 1}),
    ])
    with pytest.raises(RuntimeError, match="cardinalidade: escopo=2 staging=1"):
        sync.assert_keys_match(conn.cursor(), "stg", "escopo")


def test_f2_chave_da_staging_ausente_no_destino_reprova():
    conn = FakeConn("neon", None, [
        (r"faltando_no_destino", {"faltando_no_destino": 1, "sobrando_no_destino": 0,
                                  "linhas_no_escopo": 1, "linhas_na_staging": 1}),
    ])
    with pytest.raises(RuntimeError, match="faltando_no_destino=1"):
        sync.assert_keys_match(conn.cursor(), "stg", "escopo")


def test_f2_full_com_chave_sobrando_no_destino_reprova():
    """Hard delete não reparado: chave no destino que a fonte não tem mais."""
    conn = FakeConn("neon", None, [
        (r"faltando_no_destino", {"faltando_no_destino": 0, "sobrando_no_destino": 3,
                                  "linhas_no_escopo": 4, "linhas_na_staging": 1}),
    ])
    with pytest.raises(RuntimeError, match="sobrando_no_destino=3"):
        sync.assert_keys_match(conn.cursor(), "stg", sync.TARGET_TABLE)


def test_f2_sinal_usa_join_pelas_chaves_da_staging():
    """O JOIN pela staging já restringe o escopo nos dois modos — não precisa de
    target_scope, e usá-lo aninharia projeção dentro de projeção."""
    conn = FakeConn("neon", None, [(r"SIGN\(", {"n": 0})])
    sync.assert_signs_preserved(conn.cursor(), "stg", sync.TARGET_TABLE)
    sql = conn.sqls()[0]
    assert "FROM stg s" in sql
    assert "t.ref_month = s.ref_month" in sql
    assert sql.upper().count("IS DISTINCT FROM") == len(sync.COMPONENT_COLUMNS)


def test_f2_nan_verificado_no_escopo_e_nao_no_destino_inteiro(monkeypatch):
    """No incremental, um NaN histórico não tocado não foi introduzido por esta
    execução e não pode ser corrigido por ela: verificar o destino inteiro faria
    toda execução futura falhar sem ação possível."""
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(), neon_rowcounts())
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)

    sync.run("incremental", "run:1", apply=True)
    nan_sql = next(s for s in rec.sqls("neon") if classify(s) == "nan")
    assert "JOIN stg_ftacom_publish s" in nan_sql


# ===========================================================================
# F3 — FONTE COMPLETAMENTE VAZIA
# ===========================================================================

DM_VAZIO = {"total": 0, "com": 0, "mx": None}


def test_f3_incremental_com_fonte_vazia_falha_sem_escrever(monkeypatch):
    """O incremental não pode inferir hard delete total: 'a fonte esvaziou' e 'a
    janela não mudou' são indistinguíveis para ele, e apagar o fato com base
    nessa ambiguidade destruiria a história por causa de um TRUNCATE acidental a
    montante ou de uma réplica apontada para o banco errado.

    Com o novo desenho a recusa acontece em `validate_snapshot_in_memory`, antes
    de a transação gravável existir: zero DDL/DML por construção."""
    rec, neon, dm = _apply_env(monkeypatch, dm_kw=DM_VAZIO)
    with pytest.raises(RuntimeError, match="modo incremental"):
        sync.run("incremental", "run:1", apply=True)
    assert sem_escrita(rec)
    assert "neon.autocommit_off" not in events(rec)
    assert rec.locks_held == []


def test_f3_full_dry_run_com_fonte_vazia_apenas_diagnostica(monkeypatch):
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(), neon_rowcounts())
    dm = FakeConn("dm", rec, dm_rules(**DM_VAZIO))
    _wire(monkeypatch, neon, dm)

    rel = sync.run("full", "run:1", apply=False)
    assert "ESVAZIARIA" in rel["resultado"]
    assert "Nada foi escrito" in rel["resultado"]
    for proibido in ("DELETE", "INSERT", "UPDATE"):
        assert not any(proibido in s.upper() for s in rec.sqls("neon"))
    assert "commit" not in neon.kinds()


def test_f3_full_apply_com_fonte_vazia_limpa_fato_e_remove_state(monkeypatch):
    rec, neon, dm = _apply_env(
        monkeypatch, dm_kw=DM_VAZIO, rowcounts=neon_rowcounts(fact_delete=42))
    rel = sync.run("full", "run:1", apply=True)
    f = events(rec)
    assert f.index("neon.lock") < f.index("neon.fact_delete") < f.index("neon.state_delete")
    assert f.index("neon.state_delete") < f.index("neon.commit") < f.index("neon.unlock")
    assert rel["limpeza"] == {"fato_apagado": 42, "state_removido": 1}
    assert rel["watermark"] == "removido"
    assert not any(classify(s) in ("state_update", "state_insert")
                   for s in rec.sqls("neon"))


def test_f3_falha_ao_remover_state_faz_rollback_do_delete_do_fato(monkeypatch):
    """A operação é atômica: se o state não puder ser removido, o fato volta."""
    rec, neon, dm = _apply_env(
        monkeypatch, dm_kw=DM_VAZIO,
        rowcounts=neon_rowcounts(fact_delete=42, state=2))
    with pytest.raises(RuntimeError, match="remocao do watermark afetou 2"):
        sync.run("full", "run:1", apply=True)
    assert "rollback" in neon.kinds()
    assert "commit" not in neon.kinds()
    assert rec.locks_held == []


def test_f3_fonte_vazia_nunca_fabrica_timestamp(monkeypatch):
    """Nenhum `now()` entra como cutoff, e nenhum watermark é gravado."""
    rec, neon, dm = _apply_env(monkeypatch, dm_kw=DM_VAZIO)
    rel = sync.run("full", "run:1", apply=True)
    assert "watermark_novo" not in rel
    for s in rec.sqls("neon"):
        if classify(s) in ("state_insert", "state_update"):
            pytest.fail("watermark gravado com fonte vazia")


def test_f3_snapshot_de_fonte_vazia_tem_cutoff_none_e_flag_explicita():
    rec = Recorder()
    dm = FakeConn("dm", rec, dm_rules(**DM_VAZIO))
    snap = sync.read_source_snapshot(dm, None)
    assert snap.source_empty is True
    assert snap.cutoff is None
    assert snap.source_bounds["total"] == 0


def test_f3_fonte_nao_vazia_com_zero_marca_allowlisted_limpa_e_avanca(monkeypatch):
    """Caso distinto do anterior: a fonte TEM linhas, só nenhuma em escopo. O
    `full` esvazia o fato e avança para o cutoff global válido — aqui existe
    cutoff, e não há nada a fabricar."""
    rec = Recorder()
    neon = FakeConn("neon", rec,
                    neon_rules(chaves={"faltando_no_destino": 0,
                                       "sobrando_no_destino": 0,
                                       "linhas_no_escopo": 0,
                                       "linhas_na_staging": 0},
                               staging_max=None),
                    neon_rowcounts(fact_delete=7, fact_insert=0))
    dm = FakeConn("dm", rec, dm_rules(keys=[], rows=[], totais=sync._totais_vazios()))
    _wire(monkeypatch, neon, dm)

    rel = sync.run("full", "run:1", apply=True)
    assert rel["publicacao"]["deleted"] == 7
    assert rel["publicacao"]["published"] == 0
    assert rel["watermark"] == sync.WATERMARK_ADVANCED
    assert rel["publicacao"]["watermark_novo"] == str(CUTOFF)
    # esvaziou pela via normal de publicação, não por wipe_fact
    assert "limpeza" not in rel


def test_f3_assert_empty_source_allowed_so_permite_full():
    sync.assert_empty_source_allowed("full")
    with pytest.raises(RuntimeError, match="nao infere hard delete total"):
        sync.assert_empty_source_allowed("incremental")


# ===========================================================================
# F5 — UPDATED_AT NULO NÃO É FONTE VAZIA
# ===========================================================================

def _cur_bounds(total, com, mx):
    conn = FakeConn("dm", None, [
        (r"COUNT\(updated_at\)",
         {"total": total, "com_updated_at": com, "max_updated_at": mx}),
    ])
    return conn.cursor()


def test_f5_total_zero_e_fonte_realmente_vazia():
    b = sync.capture_source_bounds(_cur_bounds(0, 0, None))
    assert b == {"total": 0, "com_updated_at": 0, "max_updated_at": None,
                 "empty": True}


def test_f5_updated_at_nulo_em_fonte_nao_vazia_e_falha_de_contrato():
    """Este é o cenário perigoso: tratá-lo como 'fonte vazia' faria o `full`
    apagar o fato inteiro por causa de uma coluna de watermark nula."""
    with pytest.raises(RuntimeError, match="4 de 10 linha"):
        sync.capture_source_bounds(_cur_bounds(10, 6, CUTOFF))


def test_f5_max_nulo_com_linhas_presentes_e_falha_nao_caso_vazio():
    with pytest.raises(RuntimeError, match="NAO e fonte vazia"):
        sync.capture_source_bounds(_cur_bounds(10, 10, None))


def test_f5_caso_valido_devolve_cutoff():
    b = sync.capture_source_bounds(_cur_bounds(10, 10, CUTOFF))
    assert b["empty"] is False
    assert b["max_updated_at"] == CUTOFF


def test_f5_contagem_e_teto_vem_na_mesma_consulta():
    """Duas consultas separadas poderiam ver estados diferentes mesmo dentro do
    snapshot, se alguém as movesse para fora dele numa edição futura."""
    conn = FakeConn("dm", None, [
        (r"COUNT\(updated_at\)",
         {"total": 1, "com_updated_at": 1, "max_updated_at": CUTOFF}),
    ])
    sync.capture_source_bounds(conn.cursor())
    assert len(conn.sqls()) == 1
    sql = conn.sqls()[0]
    assert "COUNT(*)" in sql and "COUNT(updated_at)" in sql and "MAX(updated_at)" in sql


def test_f5_validacao_ocorre_antes_de_qualquer_filtro(monkeypatch):
    """A captura roda antes de marca e de transaction_type."""
    rec = Recorder()
    dm = FakeConn("dm", rec, dm_rules())
    sync.read_source_snapshot(dm, None)
    f = events(rec)
    assert f.index("dm.bounds") < f.index("dm.types") < f.index("dm.population")


def test_f5_erro_nao_expoe_identificador_individual():
    with pytest.raises(RuntimeError) as exc:
        sync.capture_source_bounds(_cur_bounds(10, 6, CUTOFF))
    msg = str(exc.value)
    for proibido in ("order_id", "transaction_id", "statement_id", "buyer"):
        assert proibido not in msg


# ===========================================================================
# F6 — AVANÇO DO WATERMARK OBSERVÁVEL
# ===========================================================================

def _cur_state(rowcount=1):
    conn = FakeConn("neon", None, rowcounts=[(r"_sync_state", rowcount)])
    return conn, conn.cursor()


def test_f6_avanco_maior_atualiza_uma_linha_e_reporta_avancado():
    conn, cur = _cur_state(1)
    assert sync.advance_watermark(cur, CUTOFF, "run:1", ANTERIOR) == sync.WATERMARK_ADVANCED
    assert classify(conn.sqls()[0]) == "state_update"


def test_f6_primeira_carga_insere_uma_linha():
    conn, cur = _cur_state(1)
    assert sync.advance_watermark(cur, CUTOFF, "run:1", None) == sync.WATERMARK_ADVANCED
    assert classify(conn.sqls()[0]) == "state_insert"


def test_f6_cutoff_igual_e_noop_idempotente_sem_escrita():
    """E, deliberadamente, sem tocar `source_run_id`: o run_id gravado continua
    sendo o da execução que de fato moveu o watermark. Sobrescrevê-lo atribuiria
    a esta execução um avanço que não houve."""
    conn, cur = _cur_state(1)
    assert sync.advance_watermark(cur, CUTOFF, "run:2", CUTOFF) == sync.WATERMARK_UNCHANGED
    assert conn.sqls() == []


def test_f6_cutoff_menor_falha():
    conn, cur = _cur_state(1)
    with pytest.raises(RuntimeError, match="mais antiga que o watermark"):
        sync.advance_watermark(cur, ANTERIOR, "run:1", POSTERIOR)
    assert conn.sqls() == []


@pytest.mark.parametrize("rowcount", [0, 2])
def test_f6_rowcount_inesperado_no_update_falha(rowcount):
    """Se o `WHERE` monotônico barrou a linha, a premissa do lock não valeu e
    nada pode ser publicado — declarar sucesso aqui seria o defeito original."""
    _, cur = _cur_state(rowcount)
    with pytest.raises(RuntimeError, match=f"afetou {rowcount} linha"):
        sync.advance_watermark(cur, CUTOFF, "run:1", ANTERIOR)


@pytest.mark.parametrize("rowcount", [0, 2])
def test_f6_rowcount_inesperado_no_insert_falha(rowcount):
    _, cur = _cur_state(rowcount)
    with pytest.raises(RuntimeError, match=f"afetou {rowcount} linha"):
        sync.advance_watermark(cur, CUTOFF, "run:1", None)


def test_f6_where_monotonico_permanece_como_defesa():
    conn, cur = _cur_state(1)
    sync.advance_watermark(cur, CUTOFF, "run:1", ANTERIOR)
    assert "last_successful_upper_bound < %(wm)s" in conn.sqls()[0]


def test_f6_relatorio_nao_diz_avancado_quando_inalterado(capsys):
    sync._print_report({
        "mode": "incremental", "run_id": "r", "applied": True,
        "watermark": sync.WATERMARK_UNCHANGED, "resultado": "publicado",
    })
    out = capsys.readouterr().out
    assert "inalterado" in out
    assert "avancado para" not in out
    assert "advanced_to" not in out


def test_f6_relatorio_diz_avancado_somente_com_valor_novo(capsys):
    sync._print_report({
        "mode": "full", "run_id": "r", "applied": True,
        "watermark": sync.WATERMARK_ADVANCED, "watermark_novo": str(CUTOFF),
        "resultado": "publicado",
    })
    assert f"avancado para {CUTOFF}" in capsys.readouterr().out


def test_f6_noop_de_cutoff_igual_atravessa_o_run_completo(monkeypatch):
    rec, neon, dm = _apply_env(monkeypatch, watermark=CUTOFF,
                                     dm_kw={"mx": CUTOFF})
    rel = sync.run("incremental", "run:1", apply=True)
    assert rel["watermark"] == sync.WATERMARK_UNCHANGED
    assert "watermark_novo" not in rel["publicacao"]
    assert "commit" in neon.kinds()
    assert not any(classify(s) in ("state_update", "state_insert")
                   for s in rec.sqls("neon"))


def test_f6_delete_watermark_aceita_zero_ou_uma_linha():
    for n in (0, 1):
        _, cur = _cur_state(n)
        assert sync.delete_watermark(cur) == n


# ===========================================================================
# F4 — SCHEMA E NULLABILIDADE
# ===========================================================================

@pytest.fixture(scope="module")
def migration_text():
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _coluna(texto, nome):
    """Declaração completa de uma coluna, para inspecionar NOT NULL.

    Sem `\\b` depois do tipo: `VARCHAR(64)` termina em `)`, que não é caractere
    de palavra, então `\\b` seguido de espaço nunca casaria.
    """
    m = re.search(
        rf"^\s*{nome}\s+(?:BIGINT|TIMESTAMPTZ|TIMESTAMP|VARCHAR\(64\)|NUMERIC|DATE|TEXT)"
        rf"[^,\n]*",
        texto, re.M,
    )
    assert m is not None, nome
    return m.group(0)


@pytest.mark.parametrize("coluna", [
    "source_row_count", "source_max_updated_at", "source_run_id",
])
def test_f4_colunas_de_publicacao_sao_not_null(migration_text, coluna):
    """Toda linha do fato é produto de uma publicação concluída. Anuláveis, essas
    colunas permitiriam linha 'publicada pela metade', indistinguível de linha
    válida na leitura."""
    upgrade = migration_text.split("def upgrade()")[1]
    fato = upgrade.split("_sync_state")[0]
    assert "NOT NULL" in _coluna(fato, coluna)


@pytest.mark.parametrize("coluna", [
    "last_successful_upper_bound", "source_run_id",
])
def test_f4_sync_state_e_not_null(migration_text, coluna):
    """Ausência de watermark é ausência de LINHA, nunca linha parcialmente nula."""
    state = migration_text.split("_sync_state")[1]
    assert "NOT NULL" in _coluna(state, coluna)


@pytest.mark.parametrize("coluna", sync.COMPONENT_COLUMNS)
def test_f4_componentes_financeiros_permanecem_anulaveis(migration_text, coluna):
    """Nulo = chave ausente na fonte; zero = valor medido zero. NOT NULL forçaria
    converter um no outro."""
    assert "NOT NULL" not in _coluna(migration_text, coluna).upper()


def test_f4_documentacao_da_migration_explica_ausencia_de_linha(migration_text):
    assert "AUSENCIA DE WATERMARK E' AUSENCIA DE LINHA" in migration_text


# ===========================================================================
# Migration — contratos preservados
# ===========================================================================

def test_migration_encadeia_012_sobre_011(migration_text):
    assert re.search(r'^revision = "012"$', migration_text, re.M)
    assert re.search(r'^down_revision = "011"$', migration_text, re.M)


def test_migration_nao_usa_if_not_exists_no_upgrade(migration_text):
    upgrade = migration_text.split("def upgrade()")[1].split("def downgrade()")[0]
    assert "IF NOT EXISTS" not in _op_execute_sql(upgrade).upper()


def test_migration_nao_le_a_fonte(migration_text):
    assert not re.search(r"\bSELECT\b", migration_text, re.I)
    for schema in ("silver.", "gold.", "raw."):
        assert schema not in migration_text


def test_migration_nada_executado_no_import(migration_text):
    corpo = migration_text.split("def upgrade()")[0]
    assert "op.execute" not in corpo


def test_migration_downgrade_remove_somente_objetos_desta_migration(migration_text):
    downgrade = migration_text.split("def downgrade()")[1]
    dropados = set(re.findall(r"DROP (?:TABLE|INDEX) IF EXISTS ([\w.]+)", downgrade))
    assert dropados == {
        "marts.fact_tiktok_affiliate_cost_order_monthly",
        "marts.fact_tiktok_affiliate_cost_order_monthly_sync_state",
        "marts.idx_ftacom_brand_ref_month",
    }


def test_migration_nao_cria_affiliate_cost_total(migration_text):
    assert "affiliate_cost_total" not in _op_execute_sql(migration_text)


def test_migration_nao_impoe_check_de_sinal_nos_componentes(migration_text):
    upgrade = migration_text.split("def upgrade()")[1].split("def downgrade()")[0]
    for coluna in sync.COMPONENT_COLUMNS:
        assert not re.search(rf"CHECK\s*\(\s*{coluna}\s*>=", upgrade, re.I)
        assert not re.search(rf"CHECK\s*\(\s*{coluna}\s*<=", upgrade, re.I)


def test_migration_tem_check_nao_nan_em_cada_componente(migration_text):
    for coluna in sync.COMPONENT_COLUMNS:
        assert re.search(rf"CHECK\s*\(\s*{coluna}\s*<>\s*'NaN'\s*\)", migration_text)


def test_migration_watermark_sem_timezone(migration_text):
    assert re.search(r"^\s*source_max_updated_at\s+TIMESTAMP\s+NOT NULL", migration_text, re.M)
    assert re.search(r"^\s*last_successful_upper_bound TIMESTAMP\s+NOT NULL", migration_text, re.M)
    assert not re.search(r"source_max_updated_at\s+TIMESTAMPTZ", migration_text, re.I)


def test_migration_synced_at_tem_default(migration_text):
    assert re.search(r"synced_at\s+TIMESTAMPTZ NOT NULL DEFAULT now\(\)", migration_text)


def test_migration_pk_e_o_grao_declarado(migration_text):
    assert "PRIMARY KEY (ref_month, brand)" in migration_text


def test_migration_ref_month_e_mes_nao_dia(migration_text):
    assert re.search(
        r"CHECK \(ref_month = DATE_TRUNC\('month', ref_month\)::date\)", migration_text
    )


def test_migration_cria_a_tabela_de_sync_state(migration_text):
    assert "CREATE TABLE marts.fact_tiktok_affiliate_cost_order_monthly_sync_state" in migration_text
    assert "PRIMARY KEY (source_table, target_table)" in migration_text


# ===========================================================================
# Contratos de negócio reconfirmados
# ===========================================================================

def test_allowlist_de_transaction_type_e_somente_order():
    assert sync.TRANSACTION_TYPE_ALLOWLIST == ("ORDER",)


def test_tres_componentes_com_as_chaves_json_do_contrato():
    assert sync.COMPONENT_COLUMNS == (
        "affiliate_creator_commission",
        "affiliate_partner_commission",
        "affiliate_ads_commission",
    )
    assert sync.COMPONENT_JSON_KEYS == {
        "affiliate_creator_commission": "affiliate_commission_amount_before_pit",
        "affiliate_partner_commission": "affiliate_partner_commission_amount",
        "affiliate_ads_commission": "affiliate_ads_commission_amount",
    }


def test_guardrail_acusa_chave_proibida():
    with pytest.raises(RuntimeError, match="proibida"):
        sync.assert_no_forbidden_component(
            "SUM((fee_breakdown->>'affiliate_commission_amount')::numeric)"
        )


def test_guardrail_nao_acusa_falso_positivo_em_before_pit():
    """`affiliate_commission_amount` é prefixo de
    `affiliate_commission_amount_before_pit`. Um padrão sem as aspas de
    fechamento acusaria falso positivo em toda execução e o guardrail seria
    desligado por inútil."""
    sync.assert_no_forbidden_component(
        "SUM((fee_breakdown->>'affiliate_commission_amount_before_pit')::numeric)"
    )


def test_component_sql_usa_before_pit_e_nunca_a_chave_nua():
    sql = sync._component_sql()
    assert "affiliate_commission_amount_before_pit" in sql
    assert "'affiliate_commission_amount'" not in sql


def test_component_sql_nao_converte_nulo_em_zero():
    sql = sync._component_sql()
    assert "COALESCE" not in sql.upper()
    assert sql.upper().count("SUM(") == 3


def test_nenhum_sql_do_modulo_aplica_abs():
    """abs() faria um crédito parecer custo."""
    for s in (sync._component_sql(), sync._filtro_populacao(),
              sync.target_scope("incremental", "stg", sync.TARGET_TABLE)):
        assert not re.search(r"\babs\s*\(", s, re.I)


def test_modulo_nao_materializa_total_nem_metricas_fora_de_escopo():
    materializado = " ".join(sync.BUSINESS_COLUMNS) + " " + sync._component_sql()
    assert "affiliate_cost_total" not in materializado
    for fora in ("gmv_max_ad_fee", "marketplace_sfp_service_fee",
                 "marketplace_fee_per_item", "marketplace_platform_commission",
                 "transactions", "orders"):
        assert fora not in materializado
    assert sync.BUSINESS_COLUMNS == (
        "ref_month", "brand",
        "affiliate_creator_commission", "affiliate_partner_commission",
        "affiliate_ads_commission", "source_row_count", "source_max_updated_at",
    )


# --- fronteira A ------------------------------------------------------------

def _cur_sessao(isolation, read_only):
    conn = FakeConn("dm", None, [(r"transaction_isolation",
                                  {"isolation": isolation, "read_only": read_only})])
    return conn.cursor()


def test_assert_snapshot_session_aprova_repeatable_read_read_only():
    assert sync.assert_snapshot_session(_cur_sessao("repeatable read", "on")) == {
        "isolation": "repeatable read", "read_only": "on"
    }


def test_assert_snapshot_session_reprova_read_committed():
    with pytest.raises(RuntimeError, match="repeatable read"):
        sync.assert_snapshot_session(_cur_sessao("read committed", "on"))


def test_assert_snapshot_session_reprova_sessao_gravavel():
    with pytest.raises(RuntimeError, match="read-only"):
        sync.assert_snapshot_session(_cur_sessao("repeatable read", "off"))


def _cur_tipos(linhas):
    conn = FakeConn("dm", None, [(r"GROUP BY transaction_type", linhas)])
    return conn.cursor()


def test_validate_transaction_types_aprova_somente_order():
    cur = _cur_tipos([{"transaction_type": "ORDER", "n": 1200}])
    assert sync.validate_transaction_types(cur, ANTERIOR, CUTOFF) == {"ORDER": 1200}


def test_validate_transaction_types_reprova_desconhecido_sem_expor_identificador():
    cur = _cur_tipos([{"transaction_type": "ORDER", "n": 1200},
                      {"transaction_type": "ADJUSTMENT", "n": 7}])
    with pytest.raises(RuntimeError) as exc:
        sync.validate_transaction_types(cur, ANTERIOR, CUTOFF)
    msg = str(exc.value)
    assert "ADJUSTMENT=7" in msg
    assert "watermark NAO avanca" in msg
    for proibido in ("order_id", "transaction_id", "statement_id"):
        assert proibido not in msg


def test_validate_transaction_types_reprova_tipo_nulo():
    with pytest.raises(RuntimeError, match=r"<NULL>=2"):
        sync.validate_transaction_types(
            _cur_tipos([{"transaction_type": None, "n": 2}]), ANTERIOR, CUTOFF)


def test_validate_transaction_types_nao_aplica_filtro_comercial():
    """O defeito do desenho anterior: filtrar dentro da própria descoberta tornava
    o guardrail inoperante — um tipo novo não seria selecionado."""
    conn = FakeConn("dm", None, [(r"GROUP BY transaction_type",
                                  [{"transaction_type": "ORDER", "n": 1}])])
    sync.validate_transaction_types(conn.cursor(), ANTERIOR, CUTOFF)
    sql = conn.sqls()[0]
    assert "transaction_type = ANY" not in sql
    assert "brand = ANY" not in sql


def _populacao(**overrides):
    base = {
        "lidas": 10, "nulo_transaction_id": 0, "nulo_order_create_time": 0,
        "nulo_brand": 0, "nulo_fee_breakdown": 0, "fora_da_fotografia": 0,
        "transaction_ids_distintos": 10, "marcas_distintas": 5,
    }
    base.update(overrides)
    return FakeConn("dm", None, [(r"nulo_transaction_id", base)]).cursor()


def test_validate_read_population_aprova_populacao_limpa():
    assert sync.validate_read_population(_populacao(), CUTOFF)["lidas"] == 10


@pytest.mark.parametrize("campo", [
    "transaction_id", "order_create_time", "brand", "fee_breakdown",
])
def test_validate_read_population_reprova_nulo_em_campo_chave(campo):
    with pytest.raises(RuntimeError, match=rf"{campo} nulo em 4"):
        sync.validate_read_population(_populacao(**{f"nulo_{campo}": 4}), CUTOFF)


def test_validate_read_population_reprova_transaction_id_duplicado():
    with pytest.raises(RuntimeError, match="transaction_id nao e unico"):
        sync.validate_read_population(
            _populacao(lidas=12, transaction_ids_distintos=10), CUTOFF)


def test_validate_read_population_reprova_linha_fora_da_fotografia():
    with pytest.raises(RuntimeError, match="acima do cutoff"):
        sync.validate_read_population(_populacao(fora_da_fotografia=1), CUTOFF)


def test_assert_brands_in_scope_reprova_marca_estranha():
    with pytest.raises(RuntimeError, match="fora de BRANDS_IN_SCOPE"):
        sync._assert_brands_in_scope([{"brand": "marca_nao_autorizada"}])


def test_assert_brands_in_scope_aprova_as_marcas_oficiais():
    sync._assert_brands_in_scope([{"brand": b} for b in BRANDS_IN_SCOPE])


def test_assert_rows_within_cutoff_reprova_leitura_fora_do_snapshot():
    with pytest.raises(RuntimeError, match="escapou da fotografia"):
        sync._assert_rows_within_cutoff(
            [{"source_max_updated_at": datetime(2026, 9, 1)}], CUTOFF)


def test_discover_touched_keys_aplica_o_filtro_comercial():
    conn = FakeConn("dm", None, [(r"DISTINCT DATE_TRUNC", [])])
    sync.discover_touched_keys(conn.cursor(), ANTERIOR, CUTOFF)
    sql = conn.sqls()[0]
    assert "transaction_type = ANY" in sql
    assert "brand = ANY" in sql


def test_recompute_keys_nao_tem_limite_inferior():
    """O ponto do desenho: lê TODAS as transações da chave dentro da fotografia,
    não apenas as alteradas na janela. Somar o delta produziria valor errado,
    porque revisão substitui o valor anterior — e 34,0% das linhas são revisadas
    mais de 30 dias depois."""
    conn = FakeConn("dm", None, [(r"GROUP BY ref_month, brand", [])])
    sync.recompute_keys(conn.cursor(), [(JULHO, "apice")], CUTOFF)
    _, _, sql, params = conn.rec.log[-1]
    assert "lower_bound" not in sql
    assert params["lower_bound"] is None


def test_recompute_keys_sem_chaves_nao_consulta():
    conn = FakeConn("dm", None)
    assert sync.recompute_keys(conn.cursor(), [], CUTOFF) == []
    assert conn.sqls() == []


def test_recompute_keys_parametriza_as_chaves():
    conn = FakeConn("dm", None, [(r"GROUP BY ref_month, brand", [])])
    sync.recompute_keys(conn.cursor(), [(JULHO, "apice")], CUTOFF)
    _, _, sql, params = conn.rec.log[-1]
    assert "unnest(%(meses)s::date[], %(marcas)s::text[])" in sql
    assert "apice" not in sql
    assert params["marcas"] == ["apice"]


# --- sinal e nulo -----------------------------------------------------------

def test_sum_signed_todos_nulos_continua_nulo():
    assert sync._sum_signed([None, None, None]) is None


def test_sum_signed_ignora_nulos_sem_converter_em_zero():
    assert sync._sum_signed([None, Decimal("10"), None]) == Decimal("10")


def test_sum_signed_preserva_sinal_negativo():
    assert sync._sum_signed([Decimal("-10.5"), Decimal("-2")]) == Decimal("-12.5")


def test_sum_signed_soma_credito_e_debito_sem_abs():
    """Estorno chega com sinal oposto. abs() faria o crédito parecer custo e o
    total dobraria em vez de compensar."""
    assert sync._sum_signed([Decimal("-100"), Decimal("30")]) == Decimal("-70")


def test_sum_signed_zero_medido_nao_virou_nulo():
    assert sync._sum_signed([Decimal("0")]) == Decimal("0")


def test_assert_no_nan_reprova_nan():
    with pytest.raises(RuntimeError, match="NaN"):
        sync._assert_no_nan([{
            "ref_month": JULHO,
            "affiliate_creator_commission": Decimal("NaN"),
            "affiliate_partner_commission": None,
            "affiliate_ads_commission": None,
        }])


def test_assert_no_nan_aceita_nulo_e_negativo():
    sync._assert_no_nan([_linha_agregada()])


# --- fronteira B ------------------------------------------------------------

def test_reconcile_aprova_quando_detalhe_bate_com_agregado():
    provas = sync.reconcile_detail_vs_aggregate([_linha_agregada()], _totais())
    assert provas["source_row_count"] == 3


def test_reconcile_reprova_contagem_dobrada():
    with pytest.raises(RuntimeError, match="fronteira B reprovou"):
        sync.reconcile_detail_vs_aggregate(
            [_linha_agregada(), _linha_agregada()], _totais())


def test_reconcile_trata_nulo_dos_dois_lados_como_igual():
    rows = [_linha_agregada(**{c: None for c in sync.COMPONENT_COLUMNS},
                            source_row_count=2)]
    totals = sync._totais_vazios() | {"source_row_count": 2}
    assert sync.reconcile_detail_vs_aggregate(rows, totals)["source_row_count"] == 2


def test_reconcile_reprova_nulo_no_agregado_com_valor_no_detalhe():
    """Componente perdido na agregação: sem esta prova o custo desapareceria em
    silêncio."""
    rows = [_linha_agregada(**{c: None for c in sync.COMPONENT_COLUMNS},
                            source_row_count=2)]
    totals = _totais(affiliate_creator_commission=Decimal("-50"),
                     affiliate_ads_commission=None, source_row_count=2)
    with pytest.raises(RuntimeError, match="affiliate_creator_commission"):
        sync.reconcile_detail_vs_aggregate(rows, totals)


def test_reconcile_reprova_sinal_invertido_entre_detalhe_e_agregado():
    with pytest.raises(RuntimeError, match="affiliate_creator_commission"):
        sync.reconcile_detail_vs_aggregate(
            [_linha_agregada()], _totais(affiliate_creator_commission=Decimal("100.50")))


def test_assert_staging_within_cutoff_aceita_abaixo_do_cutoff():
    """Igualdade estrita com o cutoff NÃO é invariante: o cutoff é o teto do
    snapshot sobre a tabela inteira, e a staging cobre só marcas e tipos
    allowlisted."""
    conn = FakeConn("neon", None, [(r"MAX\(source_max_updated_at\)",
                                    {"mx": datetime(2026, 8, 20)})])
    assert sync.assert_staging_within_cutoff(conn.cursor(), CUTOFF) == datetime(2026, 8, 20)


def test_assert_staging_within_cutoff_reprova_acima_do_cutoff():
    conn = FakeConn("neon", None, [(r"MAX\(source_max_updated_at\)",
                                    {"mx": datetime(2026, 9, 1)})])
    with pytest.raises(RuntimeError, match="escapou do snapshot"):
        sync.assert_staging_within_cutoff(conn.cursor(), CUTOFF)


# --- staging e publicação ---------------------------------------------------

def test_staging_e_temp_com_on_commit_drop():
    conn = FakeConn("neon", None)
    sync.create_staging(conn.cursor())
    sql = conn.sqls()[0]
    assert "CREATE TEMP TABLE" in sql
    assert "ON COMMIT DROP" in sql
    assert "INCLUDING DEFAULTS" in sql


def test_insert_into_staging_grava_run_id_e_preserva_sinal_e_nulo():
    conn = FakeConn("neon", None)
    sync.insert_into_staging(conn.cursor(), [_linha_agregada()], "run:1")
    sql = conn.sqls()[0]
    assert "source_run_id" in sql
    assert "synced_at" not in sql          # DEFAULT now() no destino
    assert "'-100.50'" in sql              # sinal preservado
    assert "NULL" in sql                   # nulo não virou zero
    assert "'run:1'" in sql


def test_publish_full_apaga_o_destino_inteiro(monkeypatch):
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(), neon_rowcounts())
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)
    sync.run("full", "run:1", apply=True)
    deletes = [s for s in rec.sqls("neon") if classify(s) == "fact_delete"]
    assert len(deletes) == 1
    assert "USING" not in deletes[0]


def test_publish_incremental_apaga_somente_as_chaves_recalculadas(monkeypatch):
    rec = Recorder()
    neon = FakeConn("neon", rec, neon_rules(), neon_rowcounts())
    dm = FakeConn("dm", rec, dm_rules())
    _wire(monkeypatch, neon, dm)
    sync.run("incremental", "run:1", apply=True)
    deletes = [s for s in rec.sqls("neon") if classify(s) == "fact_delete"]
    assert len(deletes) == 1
    assert "USING" in deletes[0]
    assert "t.ref_month = s.ref_month" in deletes[0]


def test_publish_in_transaction_nao_comita_nem_toma_lock():
    """A transação pertence ao chamador: um commit aqui quebraria a atomicidade
    entre fato e watermark."""
    conn = FakeConn("neon", None, neon_rules(), neon_rowcounts())
    sync.publish_in_transaction(conn.cursor(), _snapshot_ok(), "incremental",
                               "run:1", ANTERIOR)
    assert "commit" not in conn.kinds()
    assert "rollback" not in conn.kinds()
    assert not any("pg_advisory_xact_lock" in s for s in conn.sqls())


# --- watermark / lower bound ------------------------------------------------

def test_resolve_lower_bound_full_ignora_o_watermark():
    assert sync.resolve_lower_bound("full", ANTERIOR) is None


def test_resolve_lower_bound_incremental_usa_o_watermark():
    assert sync.resolve_lower_bound("incremental", ANTERIOR) == ANTERIOR


def test_resolve_lower_bound_incremental_sem_watermark_exige_backfill():
    """Nunca tratar 'sem watermark' como 'desde o início da janela': `updated_at`
    só existe na fonte desde 2026-03-12 e os pedidos começam em 2025-06-04."""
    with pytest.raises(RuntimeError, match="backfill integral"):
        sync.resolve_lower_bound("incremental", None)


def test_read_watermark_sem_linha_devolve_none():
    conn = FakeConn("neon", None, [(r"last_successful_upper_bound", None)])
    assert sync.read_watermark(conn.cursor()) is None


def test_read_watermark_le_o_par_fonte_destino():
    conn = FakeConn("neon", None, [(r"last_successful_upper_bound", {"wm": ANTERIOR})])
    assert sync.read_watermark(conn.cursor()) == ANTERIOR
    _, _, _, params = conn.rec.log[-1]
    assert params == {"src": sync.SOURCE_TABLE, "tgt": sync.TARGET_TABLE}


# --- CLI e segurança --------------------------------------------------------

def test_cli_exige_mode():
    with pytest.raises(SystemExit):
        sync.build_parser().parse_args([])


def test_cli_apply_e_opt_in():
    assert sync.build_parser().parse_args(["--mode", "full"]).apply is False
    assert sync.build_parser().parse_args(["--mode", "full", "--apply"]).apply is True


def test_cli_nao_tem_agendamento_retry_ou_sleep():
    texto = MODULE_PATH.read_text(encoding="utf-8")
    for proibido in ("time.sleep", "import time", "schedule", "while True",
                     "for _ in range"):
        assert proibido not in texto


def test_run_id_e_sanitizado():
    assert sync.sanitize_run_id("run id;DROP TABLE x--") == "run_id_DROP_TABLE_x--"
    assert len(sync.default_run_id("full")) <= 64


def test_main_sem_apply_reporta_diagnostico(monkeypatch):
    chamadas = {}
    monkeypatch.setattr(sync, "run", lambda mode, run_id, apply: chamadas.update(
        {"apply": apply}) or {"mode": mode, "run_id": run_id, "applied": apply,
                              "resultado": "diagnostico; nada escrito"})
    assert sync.main(["--mode", "incremental"]) == 0
    assert chamadas["apply"] is False


def test_main_erro_sai_com_2_e_mensagem_sanitizada(monkeypatch, capsys):
    """Nenhuma credencial, DSN ou topologia pode aparecer em log."""
    def explode(mode, run_id, apply):
        raise RuntimeError(
            'connection to server at "10.0.3.44", port 5432 failed: timeout')

    monkeypatch.setattr(sync, "run", explode)
    assert sync.main(["--mode", "full"]) == 2
    err = capsys.readouterr().err
    assert "10.0.3.44" not in err
    assert "5432" not in err


def test_modulo_nao_introduz_dependencia_nova():
    texto = MODULE_PATH.read_text(encoding="utf-8")
    importados = set(re.findall(r"^(?:from|import)\s+([\w.]+)", texto, re.M))
    permitidos = {
        "__future__", "argparse", "re", "sys", "dataclasses", "datetime",
        "decimal", "psycopg2", "psycopg2.extras",
        "pipelines.connectors.tiktok.connector", "pipelines.sync_tiktok_serving",
    }
    assert importados <= permitidos, importados - permitidos


def test_modulo_nao_imprime_dsn_nem_variavel_de_ambiente():
    texto = MODULE_PATH.read_text(encoding="utf-8")
    assert "os.environ" not in texto      # leitura fica em _get_*_url reutilizados
    assert not re.search(r"print\([^)]*url", texto, re.I)
    assert not re.search(r"print\([^)]*DATABASE_URL", texto)
