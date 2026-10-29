"""T-0015 - etapa no `--apply` (auditoria do `outro`) e no `--reconcile`.

Sem conexao real: reaproveita os fakes ESTRITOS de
`test_expedicao_apply_orquestracao` (run_apply de ponta a ponta) e de
`test_expedicao_ml_apply` (reconciliacao com respostas na ordem). A prova
contra PostgreSQL real esta' em `test_expedicao_etapa_integracao`.
"""
from __future__ import annotations

import json

import pytest

from pipelines.expedicao import audit as audit_mod
from pipelines.expedicao import cli, transform
from pipelines.expedicao.contract import Channel, SourceUnhealthy
from pipelines.tests.test_expedicao_apply_orquestracao import (
    Cenario,
    _linha_backlog,
)
from pipelines.tests.test_expedicao_ml_apply import (
    REGISTRY_ML,
    WATERMARKS_OK,
    FakeSource,
    FakeTarget,
    _publicado_fake,
    linha,
)
from pipelines.tests.test_expedicao_ml_apply import AGORA as AGORA_ML
from pipelines.tests.test_expedicao_ml_apply import BATCH as BATCH_ML


# ===========================================================================
# --apply: contador `outro` na auditoria do run
# ===========================================================================
def _checks_de_etapa(c: Cenario) -> list:
    return [
        p for s, p in zip(c.audit.sqls, c.audit.params)
        if "data_quality_check" in s and p and p[0] == audit_mod.CHECK_ETAPA
    ]


def test_apply_sem_outro_grava_pass_com_contagem_zero():
    c = Cenario()
    assert c.rodar() == cli.EXIT_OK
    checks = _checks_de_etapa(c)
    assert len(checks) == 1
    nome, tabela, _mkt, status, severidade, falhas, detalhe = checks[0]
    assert (status, severidade, falhas) == ("pass", "low", 0)
    d = json.loads(detalhe)
    assert d["etapa_outro_count"] == 0
    assert d["etapa_outro_fontes"] == []
    # Sem a 033 no destino: medido, mas nao persistido por linha.
    assert d["etapa_colunas_escritas"] == []
    assert sum(d["etapa_por_etapa"].values()) == len(c.enviadas["insert_fila"])


def test_apply_com_outro_avisa_e_publica_mesmo_assim():
    novo = _linha_backlog("1609671923", "N1")
    novo["order_status"] = "STATUS_NOVO"
    c = Cenario(backlog=[_linha_backlog("1609671923", "A1"), novo])
    c.colunas_etapa = ["etapa", "etapa_fonte"]

    assert c.rodar() == cli.EXIT_OK          # nunca bloqueia
    assert c.target.commits == 1
    assert len(c.enviadas["insert_fila"]) == 2

    checks = _checks_de_etapa(c)
    assert len(checks) == 1
    _n, _t, _m, status, severidade, falhas, detalhe = checks[0]
    assert (status, severidade, falhas) == ("warn", "medium", 1)
    d = json.loads(detalhe)
    assert d["channel"] == "shopee"
    assert d["etapa_outro_count"] == 1
    assert d["etapa_outro_fontes"] == ["STATUS_NOVO"]
    assert d["etapa_colunas_escritas"] == ["etapa", "etapa_fonte"]
    assert d["etapa_por_etapa"] == {"aguardando_coleta": 1, "outro": 1}


def test_registro_da_etapa_vem_depois_do_commit_da_publicacao():
    c = Cenario()
    c.rodar()
    assert c.ordem.index("audit_finish:success") < len(c.ordem) - 1
    # O check de etapa e' o ULTIMO registro: so' existe se a publicacao commitou.
    assert c.audit.sqls[-1].strip().startswith("INSERT INTO audit.data_quality_check")
    assert c.audit.params[-1][0] == audit_mod.CHECK_ETAPA


def test_falha_ao_gravar_a_etapa_nao_reverte_a_publicacao():
    c = Cenario()
    # commits da auditoria: 1 start, 2 finish, 3 observacao, 4 frescor, 5 etapa
    c.audit.falha_no_commit = RuntimeError("auditoria fora do ar")
    c.audit.commit_que_falha = 5
    assert c.rodar() == cli.EXIT_AUDITORIA_INCOMPLETA
    assert c.target.commits == 1
    assert c.target.rollbacks_da_publicacao == 0
    assert "audit_finish:failed" not in c.ordem


def test_record_etapa_corta_as_fontes_em_20():
    class _Cur:
        def __init__(self, sink):
            self.sink = sink

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, params):
            self.sink.append(params)

    class _Conn:
        def __init__(self):
            self.params = []

        def cursor(self):
            return _Cur(self.params)

        def commit(self):
            pass

    conn = _Conn()
    audit_mod.record_etapa(conn, Channel.MERCADOLIVRE, 2, {
        "etapa_outro_count": 30,
        "etapa_outro_fontes": [f"s{i:02d}" for i in range(30)],
        "etapa_por_etapa": {"outro": 30},
        "etapa_colunas_escritas": ("etapa", "etapa_fonte"),
    })
    d = json.loads(conn.params[0][6])
    assert len(d["etapa_outro_fontes"]) == 20
    assert conn.params[0][5] == 30


# ===========================================================================
# --reconcile: etapa entra na comparacao SO' com a 033 no destino
# ===========================================================================
COLUNAS_033 = [{"column_name": "etapa"}, {"column_name": "etapa_fonte"}]


def _com_etapa(respostas, fila, sobrescrever=None):
    """Acrescenta a deteccao da 033 e a releitura com `etapa`, na ordem."""
    corpo = [dict(x) for x in respostas[1]]
    etapas = {x["marketplace_order_id"]: x["etapa"] for x in fila}
    for x in corpo:
        x["etapa"] = etapas[x["marketplace_order_id"]]
    if sobrescrever:
        for x in corpo:
            x["etapa"] = sobrescrever(x["etapa"])
    return [*respostas, COLUNAS_033, corpo]


def _reconciliar(alvo, linhas):
    return cli.reconcile_channel(
        alvo, FakeSource(linhas, WATERMARKS_OK), Channel.MERCADOLIVRE,
        open_registry=lambda _c, _m: (REGISTRY_ML, []),
    )


def _cenario_ml():
    linhas = [
        linha(700001, substatus="ready_for_pickup"),
        linha(700002, substatus="picked_up"),
        linha(700003, substatus="ready_to_print"),
    ]
    fila = transform.build_fila_ml(linhas, REGISTRY_ML, AGORA_ML, BATCH_ML)
    return linhas, fila


def test_reconcile_sem_a_033_e_identico_ao_de_hoje():
    linhas, fila = _cenario_ml()
    alvo = FakeTarget(respostas=_publicado_fake(fila))
    r = _reconciliar(alvo, linhas)
    assert "etapa_reconciliada" not in r
    assert "etapa_nao_publicada" not in r
    # Nenhuma releitura com etapa foi emitida.
    assert not any(", etapa FROM" in s for s in alvo.sqls)
    assert "ETAPA" not in cli.format_reconcile(r)


def test_reconcile_com_a_033_compara_a_etapa():
    linhas, fila = _cenario_ml()
    alvo = FakeTarget(respostas=_com_etapa(_publicado_fake(fila), fila))
    r = _reconciliar(alvo, linhas)
    assert r["etapa_reconciliada"] is True
    assert r["etapa_nao_publicada"] == 0
    assert r["comparaveis"] == 3
    assert any("information_schema.columns" in s for s in alvo.sqls)
    assert "ETAPA (033 no destino)" in cli.format_reconcile(r)


def test_reconcile_com_a_033_e_etapa_divergente_falha_fechada():
    linhas, fila = _cenario_ml()
    respostas = _com_etapa(
        _publicado_fake(fila), fila,
        sobrescrever=lambda e: "a_faturar" if e == "a_imprimir" else e,
    )
    with pytest.raises(SourceUnhealthy) as erro:
        _reconciliar(FakeTarget(respostas=respostas), linhas)
    assert "DRIFT DETERMINISTICO" in str(erro.value)
    assert "etapa: publicado='a_faturar' recomputado='a_imprimir'" in str(erro.value)


def test_reconcile_com_a_033_nao_compara_nao_publicada():
    """Janela entre a migration e a proxima publicacao: default da coluna."""
    linhas, fila = _cenario_ml()
    respostas = _com_etapa(
        _publicado_fake(fila), fila, sobrescrever=lambda _e: "nao_publicada"
    )
    r = _reconciliar(FakeTarget(respostas=respostas), linhas)
    assert r["etapa_reconciliada"] is True
    assert r["etapa_nao_publicada"] == 3
    assert r["veredito"] == "deterministico_no_subconjunto_comparavel"


def test_fingerprint_nao_inclui_a_etapa():
    """Sem a 033 o publicado nao tem etapa; incluir mudaria o hash de hoje."""
    assert "etapa" not in cli.CAMPOS_RECONCILIADOS
    assert "etapa" not in cli.COLUNAS_PUBLICADAS
