"""Estoque Full ML: indicadores por inventario (transform) e pipeline DRY-RUN.

Nada aqui toca banco: a sessao do Data Mart e' duble. As provas estaticas
garantem que o pipeline nao tem caminho de escrita neste gate.
"""
from __future__ import annotations

import ast
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import sync_ml_full_stock_daily as mod
from pipelines.transforms import ml_full_stock as T

FONTE = Path(mod.__file__).read_text(encoding="utf-8")


def snap(brand, inv, disp=None, nao=0, status="OK", seller="S1"):
    return {"brand": brand, "seller_id": seller, "inventory_id": inv, "read_status": status,
            "available_quantity": disp if status == "OK" else None,
            "not_available_quantity": nao if status == "OK" else None, "total_coerente": True}


def ponte(brand, item, inv, var="0", lt="fulfillment", st="active", seller="S1"):
    return {"brand": brand, "seller_id": seller, "item_id": item, "variation_id": var,
            "inventory_id": inv, "logistic_type": lt, "listing_status": st}


def cenario():
    # Fiel ao produtor (airflow full_stock/snapshot.py L424): uma linha de snapshot por
    # inventory_id NAO nulo da ponte, nas duas direcoes; status so' de inventario OK.
    snapshot = [snap("b", "IA", 56, nao=5), snap("b", "IB", 0), snap("b", "IC", status="NAO_ENCONTRADO"),
                snap("b", "ID", 30), snap("b", "IE", 500),
                snap("b", "IV1", status="NAO_ENCONTRADO"), snap("b", "IV2", status="NAO_ENCONTRADO")]
    status = [{"brand": "b", "seller_id": "S1", "inventory_id": "IA", "status_raw": "transfer", "quantity": 4},
              {"brand": "b", "seller_id": "S1", "inventory_id": "IA", "status_raw": "statusNovo", "quantity": 1}]
    p = [ponte("b", "M1", "IA"), ponte("b", "M2", "IA"), ponte("b", "M3", "IA", st="closed", lt="cross_docking"),
         ponte("b", "M4", "IB"), ponte("b", "M5", "IC"),
         ponte("b", "M6", "ID", lt="cross_docking"),              # inventario sem nenhum anuncio Full
         ponte("b", "M7", "IE"),
         ponte("b", "MV", "IV1", var="11"), ponte("b", "MV", "IV2", var="12"),
         ponte("b", "M8", None)]                                   # anuncio sem inventario
    vendas = {("b", "M1", "0"): 28, ("b", "M2", "0"): 14, ("b", "M3", "0"): 14,   # IA: 56 un -> 2/dia
              ("b", "M4", "0"): 10,                                              # IB: ruptura
              ("b", "MV", "0"): 9,                                               # item com 2 inventarios
              ("b", "M8", "0"): 3,                                               # sem inventory_id
              ("b", "MX", "0"): 2}                                               # fora da ponte
    return snapshot, status, p, vendas


def por_inv(linhas):
    return {l["inventory_id"]: l for l in linhas}


# ---------------------------------------------------------------------------
# Grao: inventario, nunca anuncio
# ---------------------------------------------------------------------------
def test_inventario_compartilhado_conta_estoque_uma_vez_e_soma_a_velocidade_de_todos():
    linhas, resumo = T.indicadores(*cenario())
    ia = por_inv(linhas)["IA"]
    assert ia["disponivel"] == 56 and ia["anuncios"] == 3
    assert ia["unidades_28d"] == 56                     # inclui o anuncio FECHADO e nao-Full
    assert ia["cobertura_torre_dias"] == Decimal("28.0")
    assert ia["classificacao_torre"] == "SUFICIENTE"
    # soma por anuncio daria 56*3=168; o resumo usa o inventario
    assert resumo["b"]["disponivel_full"] == 56 + 0 + 500


def test_venda_de_anuncio_inativo_entra_na_velocidade():
    s, st, p, v = cenario()
    sem_inativo = {k: q for k, q in v.items() if k[1] != "M3"}
    a = por_inv(T.indicadores(s, st, p, v)[0])["IA"]["unidades_28d"]
    b = por_inv(T.indicadores(s, st, p, sem_inativo)[0])["IA"]["unidades_28d"]
    assert a - b == 14


def test_variacao_casa_pelo_par_item_variacao():
    s, st, p, v = cenario()
    s = [x for x in s if x["inventory_id"] not in ("IV1", "IV2")] + [snap("b", "IV1", 10), snap("b", "IV2", 10)]
    v = {**v, ("b", "MV", "11"): 5}
    l = por_inv(T.indicadores(s, st, p, v)[0])
    assert l["IV1"]["unidades_28d"] == 5 and l["IV2"]["unidades_28d"] == 0


# ---------------------------------------------------------------------------
# Venda sem vinculo unico: nunca rateada
# ---------------------------------------------------------------------------
def test_venda_sem_vinculo_unico_fica_nao_alocada_e_visivel():
    _, resumo = T.indicadores(*cenario())
    na = resumo["b"]["unidades_28d_nao_alocadas"]
    assert na == {"item_com_varios_inventarios": 9, "anuncio_sem_inventory_id": 3, "anuncio_fora_da_ponte": 2}
    assert resumo["b"]["unidades_28d_alocadas"] == 66
    assert resumo["b"]["cobertura_da_alocacao"] == round(66 / 80, 4)


# ---------------------------------------------------------------------------
# Ausencia nunca e' zero; inventory_id nao prova Full
# ---------------------------------------------------------------------------
def test_inventario_nao_lido_e_sem_leitura_com_nulos():
    ic = por_inv(T.indicadores(*cenario())[0])["IC"]
    assert ic["classificacao_torre"] == "SEM_LEITURA"
    assert ic["disponivel"] is None and ic["transfer"] is None and ic["cobertura_torre_dias"] is None


def test_inventario_sem_anuncio_full_fica_fora_da_operacao():
    linhas, resumo = T.indicadores(*cenario())
    assert por_inv(linhas)["ID"]["classificacao_torre"] == "FORA_DO_FULL"
    assert resumo["b"]["disponivel_full"] == 556       # ID (30) NAO entra


def test_sem_inventario_full_lido_o_total_e_nulo_nao_zero():
    _, resumo = T.indicadores([snap("k", "I1", status="NAO_ENCONTRADO")], [],
                              [ponte("k", "M", "I1")], {})
    assert resumo["k"]["disponivel_full"] is None
    assert resumo["k"]["transfer_full"] is None


def test_transfer_separado_e_status_cru_preservado():
    ia = por_inv(T.indicadores(*cenario())[0])["IA"]
    assert ia["transfer"] == 4
    assert ia["nao_disponivel_por_status"] == {"statusNovo": 1, "transfer": 4}
    assert ia["detalhe_coerente"] is True


def test_detalhe_incoerente_fica_visivel_sem_bloquear():
    s, st, p, v = cenario()
    s[0] = snap("b", "IA", 56, nao=0)        # detalhe soma 5, nao_disponivel diz 0
    l = por_inv(T.indicadores(s, st, p, v)[0])
    assert l["IA"]["detalhe_coerente"] is False
    assert l["IB"]["detalhe_coerente"] is True     # os demais seguem normais
    assert l["IC"]["detalhe_coerente"] is None     # sem leitura: nada a afirmar


# ---------------------------------------------------------------------------
# Classificacao (limiares provisorios)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("disp,un,esperado", [
    (0, 10, "RUPTURA_CANDIDATA"), (5, 0, "SEM_GIRO_CANDIDATO"), (0, 0, "SEM_DEMANDA_MEDIDA"),
    (6, 28, "BAIXO_CANDIDATO"), (7, 28, "SUFICIENTE"), (90, 28, "EXCESSO_CANDIDATO"),
    (89, 28, "SUFICIENTE"),
])
def test_classificacao(disp, un, esperado):
    assert T.classificar(disp, un, T.cobertura_dias(disp, un)) == esperado


def test_cobertura_nula_sem_demanda():
    assert T.cobertura_dias(100, 0) is None


def test_classes_do_cenario():
    l = por_inv(T.indicadores(*cenario())[0])
    assert l["IB"]["classificacao_torre"] == "RUPTURA_CANDIDATA"
    assert l["IE"]["classificacao_torre"] == "SEM_GIRO_CANDIDATO"


# ---------------------------------------------------------------------------
# Pipeline dry-run
# ---------------------------------------------------------------------------
def test_fonte_inexistente_e_sem_fonte_por_marca_nunca_zero():
    r = mod.resumir(None, date(2026, 10, 20))
    assert r["estado"] == "fonte_inexistente" and r["grava"] is False
    assert set(r["por_marca"]) == set(mod.MARCAS)
    assert all(v == {"estado": "sem_fonte"} for v in r["por_marca"].values())
    assert "0" not in json.dumps(r["por_marca"])


def test_marca_sem_fotografia_no_dia_e_sem_dado():
    s, st, p, v = cenario()
    s = [dict(x, brand="barbours") for x in s]
    p = [dict(x, brand="barbours") for x in p]
    st = [dict(x, brand="barbours") for x in st]
    v = {("barbours",) + k[1:]: q for k, q in v.items()}
    r = mod.resumir({"snapshot": s, "status": st, "ponte": p, "vendas": v}, date(2026, 10, 20))
    assert r["por_marca"]["barbours"]["estado"] == "medido"
    assert r["por_marca"]["kokeshi"] == {"estado": "sem_fotografia_no_dia"}


def test_saida_sem_ids():
    s, st, p, v = cenario()
    s = [dict(x, brand="barbours") for x in s]
    p = [dict(x, brand="barbours") for x in p]
    r = mod.resumir({"snapshot": s, "status": [], "ponte": p, "vendas": {}}, date(2026, 10, 20))
    texto = json.dumps(r, default=str)
    for proibido in ("IA", "IB", "M1", "S1"):
        assert f'"{proibido}"' not in texto


def test_janela_e_dias_completos_sem_o_corrente():
    assert mod.janela_demanda(date(2026, 10, 20)) == (date(2026, 9, 22), date(2026, 10, 20))


class _Res:
    def __init__(self, linhas):
        self.linhas = linhas

    def fetchone(self):
        return self.linhas[0]

    def __iter__(self):
        return iter(self.linhas)


class _Conn:
    def __init__(self, presentes):
        self.presentes, self.sqls = presentes, []

    def execute(self, sql, params=None):
        self.sqls.append(str(sql))
        if "to_regclass" in str(sql):
            return _Res([tuple(self.presentes)])
        return _Res([])


def test_ler_fonte_para_se_tabela_ausente():
    c = _Conn([True, False, True])
    assert mod.ler_fonte(c, date(2026, 10, 20)) is None
    assert len(c.sqls) == 1


@pytest.mark.parametrize("sql", [mod.SQL_SNAPSHOT, mod.SQL_STATUS, mod.SQL_PONTE])
def test_linhas_raw_trazem_o_run_id_da_linhagem(sql):
    # O publicador confere a linhagem RAW linha a linha, no MESMO SELECT dos dados.
    colunas = str(sql).split("SELECT", 1)[1].split("FROM", 1)[0]
    assert "run_id" in [c.strip() for c in colunas.split(",")]


def test_resumo_dry_run_nao_expoe_run_id():
    s, st, p, v = cenario()
    marcar = lambda xs: [dict(x, brand="barbours", run_id="run-x") for x in xs]
    r = mod.resumir({"snapshot": marcar(s), "status": marcar(st), "ponte": marcar(p), "vendas": {}},
                    date(2026, 10, 20))
    assert "run-x" not in json.dumps(r, default=str)


def test_run_usa_transacao_repeatable_read_read_only_e_sempre_fecha(monkeypatch):
    eventos = []

    class Raw:
        def set_session(self, **kw):                 # persistiria no pool: nao e' mais usado
            eventos.append(("set_session", kw))

    class Conexao:
        connection = Raw()

        def execute(self, sql, params=None):
            eventos.append(str(sql))
            if str(sql) == mod.SQL_CONFERE_LEITURA:
                return _Res([("repeatable read", "on")])
            return _Res([(False, False, False)])

    class Sessao:
        def connection(self):
            return Conexao()

        def rollback(self):
            eventos.append("rollback")

        def close(self):
            eventos.append("close")

    monkeypatch.setattr(mod, "DataMartSession", Sessao)
    r = mod.run(date(2026, 10, 20))
    assert r["estado"] == "fonte_inexistente"
    assert eventos[0] == mod.SQL_LEITURA_CONSISTENTE and eventos[1] == mod.SQL_CONFERE_LEITURA
    assert not any(isinstance(e, tuple) for e in eventos)
    assert eventos[-2:] == ["rollback", "close"]


@pytest.mark.parametrize("resposta", [("read committed", "on"), ("repeatable read", "off"), None])
def test_leitura_que_nao_ficou_repeatable_read_read_only_falha_fechada(resposta):
    class Conexao:
        def __init__(self):
            self.sqls = []

        def execute(self, sql, params=None):
            self.sqls.append(str(sql))
            return _Res([resposta])

    c = Conexao()
    with pytest.raises(mod.LeituraInconsistente, match="leitura_inconsistente"):
        mod.iniciar_leitura_consistente(c)
    assert c.sqls == [mod.SQL_LEITURA_CONSISTENTE, mod.SQL_CONFERE_LEITURA]
    assert "SERIALIZABLE" not in mod.SQL_LEITURA_CONSISTENTE          # proibido em hot standby


# ---------------------------------------------------------------------------
# Provas estaticas: nenhum caminho de escrita neste gate
# ---------------------------------------------------------------------------
def test_pipeline_nao_tem_caminho_de_escrita():
    sqls = " ".join(re.findall(r'text\("""(.*?)"""\)', FONTE, re.S))
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE)\b", sqls, re.I)
    nomes = {n.id if isinstance(n, ast.Name) else n.attr
             for n in ast.walk(ast.parse(FONTE)) if isinstance(n, (ast.Name, ast.Attribute))}
    assert not nomes & {"LocalSession", "local_engine", "execute_values", "commit"}
    # Por AST, nao por texto: o docstring cita `--apply` justamente para dizer
    # que ele nao existe (teste que casa com o proprio comentario e' falso).
    argumentos = [a.value for n in ast.walk(ast.parse(FONTE))
                  if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "add_argument"
                  for a in n.args if isinstance(a, ast.Constant)]
    assert argumentos == ["--ref-date"]
