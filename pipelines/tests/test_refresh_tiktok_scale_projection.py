"""Gate DQ-D1 — projecao exata dos tipos NUMERIC do destino no refresh DQ-TK1.

CAUSA PROVADA (28/08/2026): o refresh histórico reprovou com
`esperado-destino=1008 destino-esperado=1008` — mesmas chaves, valores
diferentes. O diagnostico coluna a coluna, contra o schema real, mostrou:

    avg_delivery_hours  numeric(10,2)  1.006 chaves  maior delta 0,0049987399830795
    conversion_rate     numeric(8,4)     570 chaves  maior delta 0,00004980981706212643
    -> 1.008 chaves DISTINTAS, exatamente o numero observado

Todo delta ficou abaixo de meio digito da ultima casa representavel (0,005 e
0,00005): arredondamento do PostgreSQL ao converter para NUMERIC(p,s), nunca
valor de negocio diferente. Nenhuma tolerancia foi introduzida — o que mudou e'
que o lado ESPERADO passa a ser projetado nos tipos do destino antes do EXCEPT.

Nenhum teste acessa banco.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pipelines.ops import refresh_tiktok_daily_contract as rf
from pipelines.tests.test_refresh_tiktok_daily_contract import (
    ESCALAS_REAIS,
    FakeConn,
    SCHEMA_PAT,
    TIPOS_NAO_NUMERIC,
    regra_schema,
    schema_rows,
)


def _cur(escalas=None, tipos=None):
    return FakeConn(rules=(regra_schema(escalas, tipos),)).cursor()


# ---------------------------------------------------------------------------
# 1. Leitura do information_schema
# ---------------------------------------------------------------------------
def test_escalas_lidas_do_information_schema():
    escalas = rf.target_numeric_scales(_cur())
    assert escalas["avg_delivery_hours"] == 2
    assert escalas["conversion_rate"] == 4
    assert escalas["gmv"] == 2
    assert set(escalas) == set(rf.DECIMAL_COLUMNS)


def test_consulta_a_tabela_certa_e_parametrizada():
    conn = FakeConn(rules=(regra_schema(),))
    rf.target_numeric_scales(conn.cursor())
    sqls = [(s, p) for k, s, p in conn.log if k == "sql" and "information_schema" in s]
    assert sqls, "information_schema nao foi consultado"
    sql, params = sqls[0]
    assert params["t"] == "fact_marketplace_daily_performance"
    assert params["s"] == "marts"
    # nenhuma interpolacao literal do nome da tabela
    assert "fact_marketplace_daily_performance" not in sql


def test_nenhuma_escala_hardcoded_no_modulo():
    """A escala tem de vir do schema. Numero magico no codigo divergiria do
    banco no dia de uma migration."""
    import inspect
    import re
    src = inspect.getsource(rf.target_numeric_scales) + inspect.getsource(rf._quantiza)
    codigo = re.sub(r"#.*", "", src)
    codigo = re.sub(r'""".*?"""', "", codigo, flags=re.S)
    assert "== 2" not in codigo and "== 4" not in codigo
    assert "numeric_scale" in codigo


# ---------------------------------------------------------------------------
# 2. Validacao inversa contra mudanca silenciosa de schema
# ---------------------------------------------------------------------------
def test_coluna_numeric_esperada_ausente_reprova():
    escalas = {k: v for k, v in ESCALAS_REAIS.items() if k != "gmv"}
    with pytest.raises(rf.RefreshError, match="ausente no destino"):
        rf.target_numeric_scales(_cur(escalas))


def test_coluna_deixou_de_ser_numeric_reprova():
    escalas = {k: v for k, v in ESCALAS_REAIS.items() if k != "gmv"}
    tipos = dict(TIPOS_NAO_NUMERIC, gmv="text")
    with pytest.raises(rf.RefreshError, match="esperado numeric"):
        rf.target_numeric_scales(_cur(escalas, tipos))


@pytest.mark.parametrize("ruim", [None, -1, True])
def test_escala_nula_ou_invalida_reprova(ruim):
    linhas = schema_rows()
    for r in linhas:
        if r["column_name"] == "gmv":
            r["numeric_scale"] = ruim
    cur = FakeConn(rules=((SCHEMA_PAT, linhas),)).cursor()
    with pytest.raises(rf.RefreshError,
                       match="numeric_scale invalida|esperado numeric"):
        rf.target_numeric_scales(cur)


def test_coluna_de_negocio_numeric_fora_da_allowlist_reprova():
    """`orders` virando numeric(10,2) sem entrar em DECIMAL_COLUMNS ficaria
    comparada crua contra o destino arredondado — a divergencia voltaria."""
    tipos = {k: v for k, v in TIPOS_NAO_NUMERIC.items() if k != "orders"}
    linhas = schema_rows(ESCALAS_REAIS, tipos)
    linhas.append({"column_name": "orders", "data_type": "numeric",
                   "numeric_precision": 10, "numeric_scale": 2})
    cur = FakeConn(rules=((SCHEMA_PAT, linhas),)).cursor()
    with pytest.raises(rf.RefreshError, match="fora de DECIMAL_COLUMNS"):
        rf.target_numeric_scales(cur)


# ---------------------------------------------------------------------------
# 3. Arredondamento: ROUND_HALF_UP, nunca HALF_EVEN, nunca float
# ---------------------------------------------------------------------------
def test_arredondamento_positivo_no_meio():
    assert rf._quantiza(Decimal("1.005"), 2) == Decimal("1.01")
    assert rf._quantiza(Decimal("0.00005"), 4) == Decimal("0.0001")


def test_arredondamento_negativo_no_meio():
    """ties AWAY FROM ZERO, como o PostgreSQL."""
    assert rf._quantiza(Decimal("-1.005"), 2) == Decimal("-1.01")
    assert rf._quantiza(Decimal("-0.00005"), 4) == Decimal("-0.0001")


def test_e_half_up_e_nunca_half_even():
    """HALF_EVEN daria 1.02 para 1.025; HALF_UP da 1.03. E 2.5 -> 3, nao 2."""
    assert rf._quantiza(Decimal("1.025"), 2) == Decimal("1.03")
    assert rf._quantiza(Decimal("1.015"), 2) == Decimal("1.02")
    assert rf._quantiza(Decimal("2.5"), 0) == Decimal("3")


def test_float_e_recusado():
    with pytest.raises(rf.RefreshError, match="float"):
        rf._quantiza(1.005, 2)


def test_none_preservado_nunca_vira_zero():
    assert rf._quantiza(None, 2) is None
    proj = rf.project_to_target([{"gmv": None, "date": date(2026, 8, 1)}], {"gmv": 2})
    assert proj[0]["gmv"] is None


def test_int_passa_intacto():
    assert rf._quantiza(7, 2) == 7


def test_projecao_idempotente():
    v = Decimal("429.6649329338888889")
    p1 = rf._quantiza(v, 2)
    assert rf._quantiza(p1, 2) == p1


# ---------------------------------------------------------------------------
# 4. A fotografia original nao e' mutada
# ---------------------------------------------------------------------------
def test_linhas_originais_nao_sao_mutadas():
    orig = [{"date": date(2026, 8, 1),
             "avg_delivery_hours": Decimal("1862.4606059508333333")}]
    antes = Decimal(str(orig[0]["avg_delivery_hours"]))
    proj = rf.project_to_target(orig, {"avg_delivery_hours": 2})
    assert orig[0]["avg_delivery_hours"] == antes, "fotografia original mutada"
    assert proj[0]["avg_delivery_hours"] == Decimal("1862.46")
    assert proj[0] is not orig[0]


def test_insert_usa_a_fotografia_original_nao_a_projetada():
    import inspect
    src = inspect.getsource(rf.publish_in_transaction)
    assert "_insert_rows(cur, snapshot.canonical_rows" in src
    assert "esperado = project_to_target" in src


# ---------------------------------------------------------------------------
# 5. Reconciliacao: o caso real fecha; diferenca real continua reprovando
# ---------------------------------------------------------------------------
def _linha(**kw):
    base = {"date": date(2026, 8, 1), "loja_id": 1, "marketplace_id": 1}
    base.update(kw)
    return base


def test_destino_com_escala_aplicada_reconcilia_em_zero_zero():
    """O caso medido: memoria com 16 casas, destino com 2 e 4."""
    memoria = [_linha(avg_delivery_hours=Decimal("429.6649329338888889"),
                      conversion_rate=Decimal("0.02870498098170621"))]
    destino = [_linha(avg_delivery_hours=Decimal("429.66"),
                      conversion_rate=Decimal("0.0287"))]
    esperado = rf.project_to_target(
        memoria, {"avg_delivery_hours": 2, "conversion_rate": 4})
    r = rf.reconcile(destino, esperado, date(2026, 8, 1), date(2026, 8, 1))
    assert r["chaves"] == 1


def test_sem_projecao_o_mesmo_caso_reprovaria():
    """Contraprova: e' a projecao que resolve, nao uma tolerancia."""
    memoria = [_linha(avg_delivery_hours=Decimal("429.6649329338888889"))]
    destino = [_linha(avg_delivery_hours=Decimal("429.66"))]
    with pytest.raises(rf.RefreshError, match="EXCEPT bidirecional divergiu"):
        rf.reconcile(destino, memoria, date(2026, 8, 1), date(2026, 8, 1))


def test_diferenca_real_acima_da_escala_continua_reprovando():
    """Um centavo nao e' arredondamento."""
    memoria = [_linha(gmv=Decimal("100.00"))]
    destino = [_linha(gmv=Decimal("100.01"))]
    esperado = rf.project_to_target(memoria, {"gmv": 2})
    with pytest.raises(rf.RefreshError, match="EXCEPT bidirecional divergiu"):
        rf.reconcile(destino, esperado, date(2026, 8, 1), date(2026, 8, 1))


def test_reconcile_nao_tem_tolerancia_nem_epsilon():
    import inspect
    src = inspect.getsource(rf.reconcile).lower()
    for proibido in ("tolerancia", "tolerance", "epsilon", "approx", "isclose"):
        assert proibido not in src, f"{proibido} apareceu em reconcile"


def test_chave_orfa_e_faltante_seguem_reprovando():
    a = [_linha(gmv=Decimal("1.00"))]
    b = [_linha(loja_id=2, gmv=Decimal("1.00"))]
    with pytest.raises(rf.RefreshError):
        rf.reconcile(a, b, date(2026, 8, 1), date(2026, 8, 1))


# ---------------------------------------------------------------------------
# 6. Ordem transacional e escopo
# ---------------------------------------------------------------------------
def test_falha_de_schema_acontece_antes_de_qualquer_delete():
    escalas = {k: v for k, v in ESCALAS_REAIS.items() if k != "gmv"}
    conn = FakeConn(rules=(regra_schema(escalas),))
    snap = rf.SourceSnapshot(date(2026, 8, 1), date(2026, 8, 1), [], [], {})
    with pytest.raises(rf.RefreshError, match="schema do destino incompativel"):
        rf.publish_in_transaction(conn.cursor(), snap)
    sqls = " ".join(s for k, s, _ in conn.log if k == "sql").upper()
    assert "DELETE" not in sqls, "DELETE emitido apesar do schema incompativel"
    assert "INSERT" not in sqls


def test_schema_conferido_antes_do_delete_na_ordem_do_codigo():
    import inspect
    src = inspect.getsource(rf.publish_in_transaction)
    assert src.index("target_numeric_scales") < src.index("_delete_window")


def test_ml_e_shopee_nunca_alcancados():
    """`marketplace_id` e' constante do modulo, nunca parametro do CLI."""
    import inspect
    assert rf.TIKTOK_MARKETPLACE_ID == 1
    src = inspect.getsource(rf._delete_window)
    assert "TIKTOK_MARKETPLACE_ID" in src
    assert "marketplace_id = %(mkt)s" in src


def test_backup_continua_com_as_46_colunas():
    """O contrato de backup/restore nao mudou."""
    assert len(rf.BACKUP_COLUMNS) == 46
    assert len(rf.WRITE_COLUMNS) == 44
    assert "id" in rf.BACKUP_COLUMNS and "ingested_at" in rf.BACKUP_COLUMNS
    assert "id" not in rf.WRITE_COLUMNS
