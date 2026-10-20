"""Remessa Eli -> Full ML via NF do Bling: classificador puro e dry-run.

Sem banco. O ponto central: venda nunca vira remessa, remessa so' com
documento confirmado, e nada de documento/nome/SKU sai do agregado.
"""
from __future__ import annotations

import ast
import json
import re
from datetime import date
from pathlib import Path

import pytest

from pipelines import sync_bling_remessa_full_dryrun as mod
from pipelines.transforms import bling_remessa_full as T

FONTE = Path(mod.__file__).read_text(encoding="utf-8")
DOC_ML = "11222333000144"          # documento ficticio para teste


@pytest.mark.parametrize("cfop,doc,nome,confirmados,esperado", [
    ("5949", DOC_ML, "EBAZAR.COM.BR LTDA", {DOC_ML}, T.REMESSA_FULL_ML),
    ("6949", "11.222.333/0001-44", "qualquer", {DOC_ML}, T.REMESSA_FULL_ML),          # doc formatado
    ("5949", DOC_ML, "EBAZAR.COM.BR LTDA", frozenset(), T.REMESSA_ML_NAO_CONFIRMADO),  # lista vazia
    ("5949", "99999999000199", "Mercado Livre", {DOC_ML}, T.REMESSA_ML_NAO_CONFIRMADO),
    ("5102", DOC_ML, "EBAZAR.COM.BR LTDA", {DOC_ML}, T.VENDA),       # venda NUNCA vira remessa
    ("6108", DOC_ML, "EBAZAR", {DOC_ML}, T.VENDA),
    ("5405", "", "Cliente", {DOC_ML}, T.VENDA),
    ("5949", "123", "Outro destinatario", {DOC_ML}, T.OUTRA_OPERACAO),
    ("5915", DOC_ML, "EBAZAR", {DOC_ML}, T.OUTRA_OPERACAO),
    ("59", DOC_ML, "EBAZAR", {DOC_ML}, T.CFOP_INVALIDO),
    (None, DOC_ML, "EBAZAR", {DOC_ML}, T.CFOP_INVALIDO),
])
def test_classificacao(cfop, doc, nome, confirmados, esperado):
    assert T.classificar_item(tipo="1", cfop=cfop, documento=doc, nome=nome,
                              confirmados=frozenset(confirmados)) == esperado


def test_nf_de_entrada_nao_e_remessa():
    assert T.classificar_item(tipo="0", cfop="5949", documento=DOC_ML, nome="EBAZAR",
                              confirmados={DOC_ML}) == T.NAO_E_SAIDA


def test_lista_de_documentos_confirmados_nasce_vazia():
    assert T.DOCUMENTOS_ML_CONFIRMADOS == frozenset()


def test_conta_nao_confirmada_nao_vira_marca():
    assert "bb_varejo_ltda" not in T.CONTA_PARA_MARCA


def item(**kw):
    base = {"conta": "kokeshi_mkt_place", "nf_id": "n1", "data": date(2026, 10, 1), "situacao": 6,
            "tipo": "1", "cfop": "5949", "documento": DOC_ML, "nome": "EBAZAR", "sku": "SKU-X",
            "quantidade": 10}
    base.update(kw)
    return base


def test_agregado_separa_classes_e_nunca_soma_venda_com_remessa():
    itens = [item(), item(nf_id="n2", quantidade=5), item(nf_id="n3", cfop="5102", quantidade=100)]
    r = T.agregar(itens, confirmados=frozenset({DOC_ML}))["kokeshi_mkt_place"]
    assert r["marca_torre"] == "kokeshi"
    assert r["por_classe"][T.REMESSA_FULL_ML]["6"]["unidades"] == 15
    assert r["por_classe"][T.VENDA]["6"]["unidades"] == 100
    assert r["por_classe"][T.REMESSA_FULL_ML]["6"]["nfs"] == 2


def test_situacao_fica_separada():
    itens = [item(situacao=6), item(nf_id="n2", situacao=2)]
    r = T.agregar(itens, confirmados=frozenset({DOC_ML}))["kokeshi_mkt_place"]["por_classe"][T.REMESSA_FULL_ML]
    assert set(r) == {"6", "2"}


def test_quantidade_ausente_nao_vira_zero():
    r = T.agregar([item(quantidade=None), item(nf_id="n2", quantidade="abc")],
                  confirmados=frozenset({DOC_ML}))["kokeshi_mkt_place"]["por_classe"][T.REMESSA_FULL_ML]["6"]
    assert r["itens_quantidade_invalida"] == 2 and r["itens"] == 2


def test_agregado_nao_expoe_documento_nome_ou_sku():
    texto = json.dumps(T.agregar([item()], confirmados=frozenset({DOC_ML})), default=str)
    for proibido in (DOC_ML, "EBAZAR", "SKU-X"):
        assert proibido not in texto


def test_sql_nao_le_colunas_pessoais_e_nao_escreve():
    sql = re.search(r'text\("""(.*?)"""\)', FONTE, re.S).group(1)
    for coluna in ("endereco", "telefone", "email", "cep", "rg", "etiqueta"):
        assert coluna not in sql
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b", sql, re.I)
    nomes = {n.id if isinstance(n, ast.Name) else n.attr
             for n in ast.walk(ast.parse(FONTE)) if isinstance(n, (ast.Name, ast.Attribute))}
    assert not nomes & {"LocalSession", "local_engine", "execute_values", "commit"}
    args = [a.value for n in ast.walk(ast.parse(FONTE))
            if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "add_argument"
            for a in n.args if isinstance(a, ast.Constant)]
    assert args == ["--fim", "--dias"]


def test_run_e_read_only_e_fecha(monkeypatch):
    eventos = []

    class Raw:
        def set_session(self, **kw):
            eventos.append(("set_session", kw))

    class Res(list):
        pass

    class Conexao:
        connection = Raw()

        def execute(self, sql, params=None):
            eventos.append(("sql", params["contas"]))
            return Res()

    class Sessao:
        def connection(self):
            return Conexao()

        def rollback(self):
            eventos.append("rollback")

        def close(self):
            eventos.append("close")

    monkeypatch.setattr(mod, "DataMartSession", Sessao)
    r = mod.run(date(2026, 10, 20), 30)
    assert r["grava"] is False and r["por_conta"] == {} and r["documentos_ml_confirmados"] == 0
    assert ("set_session", {"readonly": True}) in eventos
    assert eventos[-2:] == ["rollback", "close"]
