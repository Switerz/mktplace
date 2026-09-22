"""Gate EXP-3B2-H2 — apply do Mercado Livre habilitado, mas fail-closed.

O que estes testes travam, em uma frase: tirar a trava artificial nao tirou
NENHUMA barreira real, e a reconciliacao compara instantes iguais, nunca
instantes diferentes.
"""
from __future__ import annotations

import ast
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import cli, transform
from pipelines.expedicao.contract import (
    Channel,
    RegistryError,
    SellerAccount,
    SourceHealth,
    SourceUnhealthy,
)
from pipelines.expedicao.publisher import IndeterminateCommit, LockNotAcquired

RAIZ = Path(__file__).resolve().parents[2]
AGORA = datetime(2026, 9, 22, 18, 0, tzinfo=timezone.utc)
BATCH = "lote-h2"

REGISTRY_ML = {
    "2227056661": SellerAccount("2227056661", 3, "kokeshi"),
    "2532564723": SellerAccount("2532564723", 2, "barbours"),
    "2579732860": SellerAccount("2579732860", 4, "lescent"),
    "1366932565": SellerAccount("1366932565", 5, "rituaria"),
}


def linha(ship=700001, ext="2227056661", marca="kokeshi", **o):
    base = {
        "seller_id": int(ext), "brand": marca, "shipment_id": ship,
        "order_id": 880000 + ship, "shipment_status": "ready_to_ship",
        "substatus": "ready_for_pickup", "logistic_type": "cross_docking",
        "order_status": "paid",
        "order_created_at": datetime(2026, 9, 21, 10, 0),
        "date_created": datetime(2026, 9, 21, 10, 5),
        "date_ready_to_ship": datetime(2026, 9, 21, 11, 0),
        "date_shipped": None, "date_cancelled": None,
        "tracking_method": "Normal",
        "extracted_at": datetime(2026, 9, 22, 17, 30),
    }
    base.update(o)
    return base


# ---------------------------------------------------------------------------
# Fakes ESTRITOS
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.sqls.append(sql)
        baixo = sql.lower()
        if "pg_try_advisory_lock" in baixo:
            self._r = [{"pg_try_advisory_lock": self.conn.lock_livre}]
        elif "pg_advisory_unlock" in baixo:
            self._r = [{"pg_advisory_unlock": True}]
        elif "delete from" in baixo:
            self.conn.deletes += 1
            self._r = []
        elif "insert into" in baixo:
            self.conn.inserts += 1
            if self.conn.falhar_no_insert:
                raise RuntimeError("INSERT recusado pelo banco (simulado)")
            # `audit_start` usa INSERT ... RETURNING e le o id com fetchone().
            self._r = ([{"sync_run_id": 1}] if "returning" in baixo else [])
        elif "select" in baixo:
            self._r = self.conn.respostas.pop(0) if self.conn.respostas else []
        else:
            self._r = []

    def fetchall(self):
        return getattr(self, "_r", [])

    def fetchone(self):
        r = getattr(self, "_r", [])
        return r[0] if r else None


class FakeTarget:
    """Destino gravavel de mentira. Conta DELETE, INSERT e commit."""

    def __init__(self, *, lock_livre=True, falhar_no_insert=False,
                 falhar_no_commit=False, respostas=None):
        self.autocommit = False
        self.lock_livre = lock_livre
        self.falhar_no_insert = falhar_no_insert
        self.falhar_no_commit = falhar_no_commit
        self.respostas = list(respostas or [])
        self.sqls, self.deletes, self.inserts = [], 0, 0
        self.commits, self.rollbacks = 0, 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        if self.falhar_no_commit:
            raise RuntimeError("COMMIT sem resposta (simulado)")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class FakeSource:
    def __init__(self, candidatos, watermarks):
        self.candidatos = candidatos
        self.watermarks = watermarks

    def cursor(self):
        return _SourceCursor(self)

    def close(self):
        pass


class _SourceCursor:
    def __init__(self, f):
        self.f = f

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self._r = (self.f.watermarks if "MAX(s.extracted_at)" in sql
                   else self.f.candidatos)

    def fetchall(self):
        return self._r


def wm(ext, marca, quando=datetime(2026, 9, 22, 17, 30)):
    return {"seller_id": int(ext), "brand": marca, "max_extracted_at": quando}


WATERMARKS_OK = [wm(e, c.brand_key) for e, c in REGISTRY_ML.items()]


#: Resposta do `preflight_target`: primary gravavel com a 018 aplicada.
PREFLIGHT_OK = [{
    "db": "neondb", "usr": "app", "replica": False,
    "somente_leitura": "off", "tem_fila": True, "tem_resumo": True,
}]


def _rodar(*, registry=None, candidatos=None, watermarks=None,
           target=None, log=None, monkeypatch=None):
    """Executa `run_apply` do ML com tudo injetado. Nenhuma conexao real.

    `preflight_target` e `previous_watermarks` nao entram por parametro: sao
    lidos do proprio destino, entao o fake responde os SELECTs deles na ordem.
    """
    alvo = target or FakeTarget()
    # 1) preflight  2) previous_watermarks
    alvo.respostas = [PREFLIGHT_OK, [], *alvo.respostas]
    fonte = FakeSource(candidatos if candidatos is not None else [linha()],
                       watermarks if watermarks is not None else WATERMARKS_OK)
    auditoria = FakeTarget()
    auditoria.respostas = [[{"sync_run_id": 1}]] * 6
    registrado = REGISTRY_ML if registry is None else registry
    mensagens = []

    adaptador = cli.AdaptadorDeCanal(
        load_registry=lambda _c, _m: (registrado, []),
        extract=cli.ADAPTADORES[Channel.MERCADOLIVRE].extract,
        build_fila=cli.ADAPTADORES[Channel.MERCADOLIVRE].build_fila,
    )
    originais = dict(cli.ADAPTADORES)
    cli.ADAPTADORES[Channel.MERCADOLIVRE] = adaptador
    try:
        codigo = cli.run_apply(
            Channel.MERCADOLIVRE, AGORA,
            open_target=lambda: alvo,
            open_source=lambda: fonte,
            open_audit=lambda: auditoria,
            uuid_factory=lambda: BATCH,
            execute_values=lambda cur, sql, args: cur.execute(sql),
            log=(log or mensagens.append),
        )
    finally:
        cli.ADAPTADORES.clear()
        cli.ADAPTADORES.update(originais)
    return codigo, alvo, mensagens


# ---------------------------------------------------------------------------
# A trava artificial saiu, as barreiras reais ficaram
# ---------------------------------------------------------------------------
def test_apply_do_ml_nao_e_mais_recusado_incondicionalmente():
    fonte = io.open(RAIZ / "pipelines/expedicao/cli.py", encoding="utf-8").read()
    assert "--apply bloqueado no EXP-3B1" not in fonte
    assert "ADAPTADORES" in fonte


def test_mercadolivre_tem_adaptador_e_shopee_tambem():
    assert set(cli.ADAPTADORES) == {Channel.SHOPEE, Channel.MERCADOLIVRE}
    assert cli.ADAPTADORES[Channel.MERCADOLIVRE].build_fila is cli._fila_ml
    assert cli.ADAPTADORES[Channel.SHOPEE].build_fila is cli._fila_shopee


def test_canal_sem_adaptador_nao_publica():
    """ALLOWLIST: TikTok existe no enum e NAO publica."""
    assert Channel.TIKTOKSHOP not in cli.ADAPTADORES
    r = cli.run_apply(Channel.TIKTOKSHOP, AGORA, log=lambda _m: None)
    assert r == cli.EXIT_PRECONDICAO


def test_registry_vazio_bloqueia_antes_da_auditoria():
    codigo, alvo, _ = _rodar(registry={})
    assert codigo == cli.EXIT_PRECONDICAO
    assert alvo.deletes == 0 and alvo.inserts == 0 and alvo.commits == 0


def test_registry_ambiguo_bloqueia():
    """`load_registry` devolvendo problema vira REGISTRY_AMBIGUOUS no extract."""
    from pipelines.expedicao import ml_extract

    r = ml_extract.extract(
        FakeSource([linha()], WATERMARKS_OK), AGORA, frozenset(REGISTRY_ML),
        registry_problems=["external_seller_id duplicado: 2227056661"],
    )
    assert r.source_health is SourceHealth.REGISTRY_AMBIGUOUS
    assert r.source_health.can_publish is False
    assert r.backlog_rows == []


def test_registry_incompleto_bloqueia():
    """Conta esperada ausente da fonte: ACCOUNT_MISSING, nada publicado."""
    codigo, alvo, _ = _rodar(watermarks=WATERMARKS_OK[:2])
    assert codigo == cli.EXIT_FONTE_NAO_PUBLICAVEL
    assert alvo.deletes == 0 and alvo.commits == 0


def test_seller_inesperado_bloqueia():
    extra = [*WATERMARKS_OK, wm("9999999999", "marca-nova")]
    codigo, alvo, _ = _rodar(watermarks=extra)
    assert codigo == cli.EXIT_FONTE_NAO_PUBLICAVEL
    assert alvo.deletes == 0 and alvo.commits == 0


def test_fonte_stale_bloqueia_antes_do_delete():
    velho = datetime(2026, 5, 1, 0, 0)
    codigo, alvo, _ = _rodar(
        candidatos=[linha(extracted_at=velho)],
        watermarks=[wm(e, c.brand_key, velho) for e, c in REGISTRY_ML.items()],
    )
    assert codigo == cli.EXIT_FONTE_NAO_PUBLICAVEL
    assert alvo.deletes == 0, "DELETE rodou com fonte parada"
    assert alvo.commits == 0


def test_modalidade_fora_da_allowlist_bloqueia():
    codigo, alvo, _ = _rodar(candidatos=[linha(logistic_type="flex_v2")])
    assert codigo == cli.EXIT_FONTE_NAO_PUBLICAVEL
    assert alvo.deletes == 0 and alvo.commits == 0


def test_conta_fora_do_registry_na_fila_levanta():
    with pytest.raises(RegistryError):
        transform.build_fila_ml([linha(ext="7777777777")], REGISTRY_ML, AGORA, BATCH)


def test_lock_ocupado_nao_le_a_fonte_nem_publica():
    codigo, alvo, _ = _rodar(target=FakeTarget(lock_livre=False))
    assert codigo == cli.EXIT_LOCK_OCUPADO
    assert alvo.deletes == 0 and alvo.inserts == 0 and alvo.commits == 0


def test_rollback_apos_delete_quando_o_insert_falha():
    alvo = FakeTarget(falhar_no_insert=True)
    codigo, alvo, _ = _rodar(target=alvo)
    assert alvo.deletes == 1, "o DELETE precisa ter sido tentado"
    assert alvo.rollbacks >= 1, "faltou rollback depois do DELETE"
    assert alvo.commits == 0
    assert codigo == cli.EXIT_FALHA


def test_commit_indeterminado_nao_vira_sucesso_nem_retry():
    alvo = FakeTarget(falhar_no_commit=True)
    codigo, alvo, _ = _rodar(target=alvo)
    assert codigo == cli.EXIT_COMMIT_INDETERMINADO
    assert alvo.commits == 0
    assert alvo.deletes == 1, "houve DELETE; o estado e desconhecido"


def test_publicacao_saudavel_commita_uma_unica_vez():
    codigo, alvo, msgs = _rodar()
    assert codigo in (cli.EXIT_OK, cli.EXIT_AUDITORIA_INCOMPLETA)
    assert alvo.commits == 1, f"commits={alvo.commits}"
    assert alvo.deletes == 1


def test_nenhum_retry_apos_o_inicio_da_publicacao():
    """Estrutural, nao textual.

    A versao textual desta checagem reprovava o proprio comentario que EXPLICA
    a ausencia de retry — a palavra aparece la justamente porque a garantia
    esta documentada. O que importa e a ARVORE: `publish_channel` nao pode
    estar dentro de nenhum laco, e uma execucao faz no maximo um DELETE e um
    commit.
    """
    codigo, alvo, _ = _rodar()
    del codigo
    assert alvo.deletes <= 1
    assert alvo.commits <= 1

    arvore = ast.parse(io.open(RAIZ / "pipelines/expedicao/cli.py",
                               encoding="utf-8").read())

    def dentro_de_laco(alvo_no, raiz):
        for no in ast.walk(raiz):
            if not isinstance(no, (ast.For, ast.While, ast.AsyncFor)):
                continue
            for filho in ast.walk(no):
                if filho is alvo_no:
                    return True
        return False

    chamadas = [
        n for n in ast.walk(arvore)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
        == "publish_channel"
    ]
    assert len(chamadas) == 1, f"{len(chamadas)} chamadas de publish_channel"
    assert not dentro_de_laco(chamadas[0], arvore), (
        "publish_channel esta dentro de um laco: isso E retry"
    )


def test_delete_do_apply_e_sempre_por_canal():
    codigo, alvo, _ = _rodar()
    del codigo
    deletes = [s for s in alvo.sqls if "delete from" in s.lower()]
    assert deletes, "nenhum DELETE emitido"
    for d in deletes:
        assert "channel = %s" in d.lower(), d


def test_rituaria_entra_no_resumo_com_backlog_zero():
    """Conta saudavel sem shipment continua representada."""
    fila = transform.build_fila_ml([linha()], REGISTRY_ML, AGORA, BATCH)
    contas = {e: (c.brand_key, c.brand_key) for e, c in REGISTRY_ML.items()}
    resumos = transform.build_account_summaries(
        fila, AGORA, channel=Channel.MERCADOLIVRE.value, refresh_batch_id=BATCH,
        accounts=contas,
        watermarks={e: datetime(2026, 9, 22, 17, 30, tzinfo=timezone.utc)
                    for e in REGISTRY_ML},
        source_advanced=True,
    )
    assert len(resumos) == 4
    rit = next(r for r in resumos if r["shop_account"] == "rituaria")
    assert rit["backlog_count"] == 0


def test_candidata_vazia_inesperada_nao_passa_por_fonte_doente():
    """Fila vazia com fonte SAUDAVEL publica; com fonte parada, nao."""
    codigo, alvo, _ = _rodar(candidatos=[])
    assert codigo in (cli.EXIT_OK, cli.EXIT_AUDITORIA_INCOMPLETA)
    assert alvo.commits == 1, "fotografia vazia legitima deve publicar"


# ---------------------------------------------------------------------------
# Reconciliacao deterministica
# ---------------------------------------------------------------------------
def _publicado_fake(linhas_fila, efetivo=AGORA, batch=BATCH):
    """Respostas do destino: primeiro o cabecalho, depois as linhas."""
    cabecalho = [{"refresh_batch_id": batch, "effective_at": efetivo}]
    corpo = [
        {"channel": x["channel"], "shop_account": x["shop_account"],
         "marketplace_order_id": x["marketplace_order_id"],
         "brand": x["brand"], "deadline_status": x["deadline_status"],
         "operational_age_status": x["operational_age_status"],
         "is_stalled": x["is_stalled"], "logistic_type": x["logistic_type"]}
        for x in linhas_fila
    ]
    return [cabecalho, corpo]


def test_reconciliacao_usa_o_effective_at_do_batch_publicado():
    linhas = [linha(700001), linha(700002)]
    fila = transform.build_fila_ml(linhas, REGISTRY_ML, AGORA, BATCH)
    alvo = FakeTarget(respostas=_publicado_fake(fila))
    fonte = FakeSource(linhas, WATERMARKS_OK)
    r = cli.reconcile_channel(
        alvo, fonte, Channel.MERCADOLIVRE,
        open_registry=lambda _c, _m: (REGISTRY_ML, []),
    )
    assert r["effective_at"] == AGORA
    assert r["publicadas"] == r["recomputadas"] == r["chaves_comuns"] == 2
    assert r["campos_divergentes"] == 0
    assert r["fingerprint_comuns_igual"] is True


def test_reconciliacao_nao_exige_hash_igual_entre_instantes_diferentes():
    """A ancora e o effective_at publicado, nao o relogio de agora.

    A mesma fonte, reconciliada em `AGORA`, bate. Se a reconciliacao usasse o
    relogio corrente, a idade reclassificaria e o hash divergiria sozinho — foi
    o que o EXP-3B2-P2 mediu (over_48h 269 -> 358 em 33 minutos).
    """
    linhas = [linha(700001, date_ready_to_ship=datetime(2026, 9, 22, 13, 0))]
    no_publicado = transform.build_fila_ml(linhas, REGISTRY_ML, AGORA, BATCH)
    muito_depois = AGORA + timedelta(days=10)
    no_futuro = transform.build_fila_ml(linhas, REGISTRY_ML, muito_depois, BATCH)
    assert (no_publicado[0]["operational_age_status"]
            != no_futuro[0]["operational_age_status"]), (
        "o cenario precisa de uma linha que MUDE de faixa com o tempo"
    )
    alvo = FakeTarget(respostas=_publicado_fake(no_publicado))
    r = cli.reconcile_channel(
        alvo, FakeSource(linhas, WATERMARKS_OK), Channel.MERCADOLIVRE,
        open_registry=lambda _c, _m: (REGISTRY_ML, []),
    )
    assert r["campos_divergentes"] == 0, (
        "reconciliar no effective_at publicado tem que bater mesmo com o "
        "relogio ja adiantado"
    )


def test_reconciliacao_falha_fechada_em_drift_material():
    """Mesma chave, mesmo instante, classificacao diferente: levanta."""
    linhas = [linha(700001)]
    fila = transform.build_fila_ml(linhas, REGISTRY_ML, AGORA, BATCH)
    corrompido = _publicado_fake(fila)
    corrompido[1][0]["operational_age_status"] = "over_48h"
    alvo = FakeTarget(respostas=corrompido)
    with pytest.raises(SourceUnhealthy) as erro:
        cli.reconcile_channel(
            alvo, FakeSource(linhas, WATERMARKS_OK), Channel.MERCADOLIVRE,
            open_registry=lambda _c, _m: (REGISTRY_ML, []),
        )
    assert "DRIFT MATERIAL" in str(erro.value)


def test_reconciliacao_nao_imprime_identificador_de_pedido():
    linhas = [linha(700001)]
    fila = transform.build_fila_ml(linhas, REGISTRY_ML, AGORA, BATCH)
    corrompido = _publicado_fake(fila)
    corrompido[1][0]["brand"] = "outra"
    with pytest.raises(SourceUnhealthy) as erro:
        cli.reconcile_channel(
            FakeTarget(respostas=corrompido),
            FakeSource(linhas, WATERMARKS_OK), Channel.MERCADOLIVRE,
            open_registry=lambda _c, _m: (REGISTRY_ML, []),
        )
    assert "700001" not in str(erro.value)


def test_reconciliacao_conta_churn_sem_reprovar():
    """Chave so' de um lado e churn da fonte: reportado, nao fatal."""
    publicadas = [linha(700001), linha(700002)]
    fila = transform.build_fila_ml(publicadas, REGISTRY_ML, AGORA, BATCH)
    agora_na_fonte = [linha(700002), linha(700003)]  # 1 saiu, 1 entrou
    alvo = FakeTarget(respostas=_publicado_fake(fila))
    r = cli.reconcile_channel(
        alvo, FakeSource(agora_na_fonte, WATERMARKS_OK), Channel.MERCADOLIVRE,
        open_registry=lambda _c, _m: (REGISTRY_ML, []),
    )
    assert r["somente_publicadas"] == 1
    assert r["somente_recomputadas"] == 1
    assert r["chaves_comuns"] == 1
    assert r["campos_divergentes"] == 0
    assert r["fingerprint_comuns_igual"] is True


def test_reconciliacao_recusa_fonte_stale():
    linhas = [linha(700001)]
    fila = transform.build_fila_ml(linhas, REGISTRY_ML, AGORA, BATCH)
    velho = datetime(2026, 5, 1, 0, 0)
    with pytest.raises(SourceUnhealthy):
        cli.reconcile_channel(
            FakeTarget(respostas=_publicado_fake(fila)),
            FakeSource(linhas, [wm(e, c.brand_key, velho)
                                for e, c in REGISTRY_ML.items()]),
            Channel.MERCADOLIVRE,
            open_registry=lambda _c, _m: (REGISTRY_ML, []),
        )


def test_reconciliacao_recusa_canal_sem_fotografia():
    alvo = FakeTarget(respostas=[[]])
    with pytest.raises(SourceUnhealthy):
        cli.reconcile_channel(
            alvo, FakeSource([], WATERMARKS_OK), Channel.MERCADOLIVRE,
            open_registry=lambda _c, _m: (REGISTRY_ML, []),
        )


def test_reconciliacao_recusa_dois_lotes_simultaneos():
    alvo = FakeTarget(respostas=[[
        {"refresh_batch_id": "a", "effective_at": AGORA},
        {"refresh_batch_id": "b", "effective_at": AGORA},
    ]])
    with pytest.raises(SourceUnhealthy) as erro:
        cli.reconcile_channel(
            alvo, FakeSource([], WATERMARKS_OK), Channel.MERCADOLIVRE,
            open_registry=lambda _c, _m: (REGISTRY_ML, []),
        )
    assert "lotes simultaneos" in str(erro.value)


def test_fingerprint_e_independente_de_ordem():
    fila = transform.build_fila_ml(
        [linha(700001), linha(700002), linha(700003)], REGISTRY_ML, AGORA, BATCH)
    assert cli.fingerprint_fila(fila) == cli.fingerprint_fila(list(reversed(fila)))


def test_reconciliacao_nao_escreve_e_nao_toma_lock():
    linhas = [linha(700001)]
    fila = transform.build_fila_ml(linhas, REGISTRY_ML, AGORA, BATCH)
    alvo = FakeTarget(respostas=_publicado_fake(fila))
    cli.reconcile_channel(
        alvo, FakeSource(linhas, WATERMARKS_OK), Channel.MERCADOLIVRE,
        open_registry=lambda _c, _m: (REGISTRY_ML, []),
    )
    assert alvo.deletes == 0 and alvo.inserts == 0 and alvo.commits == 0
    assert not any("advisory" in s.lower() for s in alvo.sqls)


# ---------------------------------------------------------------------------
# Shopee inalterada
# ---------------------------------------------------------------------------
def test_shopee_continua_com_o_proprio_adaptador():
    import inspect

    fonte = inspect.getsource(cli._fila_shopee)
    assert "fetch_baselines" in fonte
    assert "build_fila_shopee" in fonte
    assert "build_fila_ml" not in fonte


def test_o_build_do_ml_nao_usa_baseline():
    import inspect

    fonte = inspect.getsource(cli._fila_ml)
    assert "fetch_baselines" not in fonte
    assert "build_fila_ml" in fonte


def test_run_apply_tem_um_unico_caminho_de_publicacao():
    """Um so `publish_channel` no orquestrador: nao ha fluxo paralelo por canal."""
    arvore = ast.parse(io.open(RAIZ / "pipelines/expedicao/cli.py",
                               encoding="utf-8").read())
    chamadas = [
        n for n in ast.walk(arvore)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
        == "publish_channel"
    ]
    assert len(chamadas) == 1, f"{len(chamadas)} chamadas de publish_channel"


def test_cli_aceita_reconcile_e_recusa_combinacoes():
    p = cli.build_parser()
    a = p.parse_args(["--channel", "mercadolivre", "--reconcile"])
    assert a.reconcile is True and a.apply is False and a.diagnose is False
    a2 = p.parse_args(["--channel", "mercadolivre", "--apply"])
    assert a2.apply is True and a2.reconcile is False


def test_reconcile_e_apply_sao_mutuamente_exclusivos():
    assert cli.main(["--channel", "mercadolivre", "--apply", "--reconcile"]) == (
        cli.EXIT_FALHA
    )
    assert cli.main(["--channel", "mercadolivre"]) == cli.EXIT_FALHA
