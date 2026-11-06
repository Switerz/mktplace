"""DRE-DATA-BRIDGE-1 — regras puras do publisher de insumos da DRE.

Sem banco. As provas de servidor (lock, transacao, rollback, idempotencia,
migration) estao em `apps/api/tests/test_dre_bridge_integracao.py`, contra um
PostgreSQL descartavel.
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines.dre import insumo_publisher as P

REPO = Path(__file__).resolve().parents[2]
JUL = date(2026, 7, 1)


def esperado(canal="ml", marca="kokeshi", gmv=1_000_000, mes=JUL):
    return {"competencia": mes, "marketplace_id": P.MKT_ID[canal], "marca": marca, "gmv": Decimal(gmv)}


def cmv(canal="ml", marca="kokeshi", receita=1_000_000, custo=300_000, prov=0, rcc=None, mes=JUL,
        emissao=date(2026, 7, 31)):
    """Uma linha da leitura do CMV, ja' agregada para `competencia x marca x plataforma`.

    `custo=None` e' o que o `SUM(custo)` devolve quando nenhum item da celula
    tem custo; ai' o `SUM ... FILTER (custo IS NOT NULL)` tambem e' NULL.
    """
    sem_custo = custo is None
    return {"competencia": mes, "marca": marca, "plataforma": P.PLATAFORMA_FISCAL.get(canal, canal),
            "receita_fiscal": Decimal(receita), "custo": None if sem_custo else Decimal(custo),
            "custo_provisorio": None if sem_custo else Decimal(prov),
            "receita_com_custo": None if sem_custo else Decimal(rcc if rcc is not None else receita),
            "ultima_emissao": emissao, "linhas": 10}


def cp(c, **over):
    """Linha da contraprova (agregado gold) igual a' do CMV, salvo `over`."""
    return {k: c[k] for k in ("competencia", "marca", "plataforma", "receita_fiscal", "custo", "ultima_emissao")} | over


def repasse(marca="kokeshi", taxa=-290_000, frete=-12_000, afiliado=-90_000, residuo=0, viol=0, mes=JUL):
    return {"competencia": mes, "marca": marca, "taxa": Decimal(taxa), "frete": Decimal(frete),
            "receita": Decimal(1_050_000), "afiliado": Decimal(afiliado), "residuo": Decimal(residuo),
            "tx_afiliado_maior_taxa": viol, "transacoes": 1000, "atualizado_em": datetime(2026, 10, 1, 3, 0)}


def lote(esp, cmvs, reps=(), contraprova=None, canais=None, comps=(JUL,), sem_vendas=frozenset(),
         publicadas=frozenset(), remover=frozenset()):
    """Por padrao, os canais pedidos sao os que tem celula esperada — o mesmo que
    o operador faria. Testes de escopo passam `canais` explicitamente."""
    if canais is None:
        presentes = {r["marketplace_id"] for r in esp}
        canais = [c for c in P.CANAIS if P.MKT_ID[c] in presentes] or ["ml"]
    return P.montar_lote(esperado=list(esp), cmv=list(cmvs),
                         contraprova=list(contraprova if contraprova is not None else [cp(c) for c in cmvs]),
                         repasse=list(reps), canais=list(canais), comps=list(comps), sem_vendas=sem_vendas,
                         publicadas=publicadas, remover=remover)


def linha(lt, canal, marca, comp):
    return next(r for r in lt.linhas if (r["canal"], r["marca"], r["componente"]) == (canal, marca, comp))


# ---------------------------------------------------------------------------
# Ausencia versus zero
# ---------------------------------------------------------------------------

def test_chave_esperada_sem_cmv_vira_nulo_explicito_nunca_zero():
    lt = lote([esperado("ml", "kokeshi")], [])
    for comp in ("cmv", "cmv_receita_fiscal"):
        r = linha(lt, "ml", "kokeshi", comp)
        assert r["valor"] is None and r["status_reconciliacao"] == "ausente_na_origem"
    assert lt.recusas == []


def test_cmv_medido_zero_e_gravado_zero():
    lt = lote([esperado()], [cmv(custo=0, receita=1_000_000)])
    r = linha(lt, "ml", "kokeshi", "cmv")
    assert r["valor"] == Decimal(0) and r["status_reconciliacao"] == "reconciliado"


def test_tiktok_sem_repasse_vira_sete_linhas_nulas():
    lt = lote([esperado("tiktok")], [cmv("tiktok")], reps=[])
    nulas = [r for r in lt.linhas if r["componente"].startswith("tiktok_")]
    assert len(nulas) == 7 and all(r["valor"] is None for r in nulas)


def test_chave_fora_do_esperado_nao_e_publicada():
    lt = lote([esperado("ml", "kokeshi")], [cmv("ml", "kokeshi"), cmv("ml", "apice")])
    assert {r["marca"] for r in lt.linhas} == {"kokeshi"}
    assert ("cmv", JUL, "ml", "apice") in lt.fora_do_escopo


# ---------------------------------------------------------------------------
# Reconciliacao: estado do dado x recusa da publicacao
# ---------------------------------------------------------------------------

def test_cmv_fora_da_faixa_e_estado_nao_recusa():
    lt = lote([esperado("shopee", gmv=1_000_000)], [cmv("shopee", receita=450_000)])
    r = linha(lt, "shopee", "kokeshi", "cmv")
    assert r["status_reconciliacao"] == "nao_reconciliado" and "0.4500" in r["motivo"]
    assert lt.recusas == []


@pytest.mark.parametrize("receita,status", [(950_000, "reconciliado"), (1_050_000, "reconciliado"),
                                            (949_000, "nao_reconciliado"), (1_051_000, "nao_reconciliado")])
def test_faixa_do_cmv_e_inclusiva(receita, status):
    lt = lote([esperado()], [cmv(receita=receita)])
    assert linha(lt, "ml", "kokeshi", "cmv")["status_reconciliacao"] == status


def test_contraprova_divergente_recusa_tudo():
    c = cmv()
    outra = cp(c) | {"custo": Decimal(299_000)}
    lt = lote([esperado()], [c], contraprova=[outra])
    assert any("diverge do agregado mensal" in m for m in lt.recusas)


def test_chave_so_numa_tabela_fiscal_recusa():
    lt = lote([esperado()], [cmv()], contraprova=[cp(cmv(marca="apice"))])
    assert any("so' em uma" in m for m in lt.recusas)


# ---------------------------------------------------------------------------
# T-0055 C2 — contraprova contra o agregado gold que anda atras da base de item
# ---------------------------------------------------------------------------

D27, D31 = date(2026, 7, 27), date(2026, 7, 31)


def test_contraprova_completa_e_igual_publica_sem_observacao():
    lt = lote([esperado()], [cmv()])
    assert lt.recusas == [] and lt.observacoes == []
    assert "contraprova" not in linha(lt, "ml", "kokeshi", "cmv")["motivo"]


def test_contraprova_completa_e_divergente_recusa_mesmo_com_a_base_de_item_maior():
    c = cmv(receita=1_000_000)
    lt = lote([esperado()], [c], contraprova=[cp(c, receita_fiscal=Decimal(900_000))])
    assert any("receita_fiscal" in m and "diverge do agregado mensal" in m for m in lt.recusas)


def test_agregado_defasado_nao_recusa_e_deixa_observacao_no_motivo():
    """O caso de out/26: agregado parado em 27/10 com ~85% da receita."""
    c = cmv(receita=1_000_000, custo=300_000, emissao=D31)
    lt = lote([esperado()], [c],
              contraprova=[cp(c, receita_fiscal=Decimal(850_000), custo=Decimal(240_000), ultima_emissao=D27)])
    assert lt.recusas == []
    r = linha(lt, "ml", "kokeshi", "cmv")
    assert r["valor"] == Decimal(300_000)  # publica a base de item, nao o agregado
    assert r["status_reconciliacao"] == "reconciliado"  # a razao com o GMV segue a regra de sempre
    assert r["motivo"].startswith("receita fiscal / GMV = 1.0000; ")
    assert "contraprova nao bloqueante: agregado gold ate 27/07, base de item ate 31/07" in r["motivo"]
    assert lt.observacoes == ["2026-07: contraprova nao bloqueante: agregado gold ate 27/07, base de item ate 31/07"]


def test_agregado_defasado_sem_a_chave_nao_recusa():
    """Marca que so' vendeu depois do corte do agregado: a defasagem explica."""
    c, outra = cmv(marca="kokeshi"), cmv(marca="apice")
    lt = lote([esperado(marca="kokeshi"), esperado(marca="apice")], [c, outra],
              contraprova=[cp(c, ultima_emissao=D27)])
    assert lt.recusas == []
    assert "agregado gold ate 27/07" in linha(lt, "ml", "apice", "cmv")["motivo"]


def test_agregado_sem_a_competencia_nao_recusa():
    lt = lote([esperado()], [cmv()], contraprova=[])
    assert lt.recusas == []
    assert "agregado gold sem a competencia" in linha(lt, "ml", "kokeshi", "cmv")["motivo"]


def test_agregado_defasado_com_mais_receita_que_a_base_de_item_recusa():
    """A defasagem explica o agregado ter MENOS; ter MAIS e' a base de item que regrediu."""
    c = cmv(receita=1_000_000, emissao=D31)
    lt = lote([esperado()], [c], contraprova=[cp(c, receita_fiscal=Decimal(1_000_002), ultima_emissao=D27)])
    assert any("abaixo do agregado gold defasado" in m for m in lt.recusas)


def test_agregado_defasado_com_chave_que_a_base_de_item_perdeu_recusa():
    c = cmv(marca="kokeshi")
    lt = lote([esperado()], [c],
              contraprova=[cp(c, ultima_emissao=D27), cp(cmv(marca="apice"), ultima_emissao=D27)])
    assert any("a base de item perdeu a chave" in m for m in lt.recusas)


def test_agregado_a_frente_da_base_de_item_e_completo_e_bloqueia():
    c = cmv(emissao=D27)
    lt = lote([esperado()], [c], contraprova=[cp(c, custo=Decimal(310_000), ultima_emissao=D31)])
    assert any("custo" in m and "diverge do agregado mensal" in m for m in lt.recusas)


def test_completude_da_contraprova_e_por_competencia():
    """Agosto alcancado e divergente recusa; julho defasado so' observa."""
    ago = date(2026, 8, 1)
    cj, ca = cmv(mes=JUL, emissao=D31), cmv(mes=ago, emissao=date(2026, 8, 31))
    lt = lote([esperado(mes=JUL), esperado(mes=ago)], [cj, ca], comps=(JUL, ago),
              contraprova=[cp(cj, receita_fiscal=Decimal(800_000), ultima_emissao=D27),
                           cp(ca, custo=Decimal(1))])
    assert len(lt.recusas) == 1 and "2026, 8, 1" in lt.recusas[0]
    assert lt.observacoes == ["2026-07: contraprova nao bloqueante: agregado gold ate 27/07, base de item ate 31/07"]


def test_contraprova_aceita_emissao_como_datetime():
    c = cmv(emissao=datetime(2026, 7, 31, 15, 0))
    lt = lote([esperado()], [c], contraprova=[cp(c, ultima_emissao=D31)])
    assert lt.recusas == [] and lt.observacoes == []


# ---------------------------------------------------------------------------
# T-0055 C1 — fonte de item: forma, nulo, cobertura, plataforma, grao
# ---------------------------------------------------------------------------

def _sql(t) -> str:
    return " ".join(str(t).split())


def test_sql_do_cmv_le_a_base_de_item_com_os_filtros_da_query_do_fpa():
    sql = _sql(P.SQL_CMV)
    assert "FROM gold.nf_vendas_unificada_v2" in sql
    assert "canal = 'marketplace'" in sql and "plataforma = ANY(:plataformas)" in sql
    assert "marca = ANY(:marcas)" in sql and "data_mes = ANY(:competencias)" in sql
    # o default da query do FP&A: intercompany NULL conta como 'normal'
    assert "intercompany IS NOT TRUE" in sql and "NOT intercompany" not in sql.replace("IS NOT TRUE", "")
    # ausencia != zero: nenhum COALESCE no custo
    assert "COALESCE" not in sql.upper()
    assert "SUM(receita_produto) FILTER (WHERE custo IS NOT NULL) AS receita_com_custo" in sql
    assert "{{" not in sql and "[[" not in sql  # nada de template do Metabase


def test_sql_do_cmv_nao_tem_join_e_agrupa_na_chave_inteira_da_saida():
    for t in (P.SQL_CMV, P.SQL_CMV_CONTRAPROVA):
        sql = _sql(t).upper()
        assert " JOIN " not in sql and sql.count(" FROM ") == 1
        assert sql.endswith("GROUP BY DATA_MES, MARCA, PLATAFORMA")
    assert "FROM gold.nf_vendas_plataforma_sku_mensal" in _sql(P.SQL_CMV_CONTRAPROVA)


def test_leitura_da_fonte_mantem_read_only_e_timeout():
    fonte = (REPO / "pipelines" / "dre" / "insumo_publisher.py").read_text(encoding="utf-8")
    trecho = fonte[fonte.index("def ler_fonte"):fonte.index("def _esperado")]
    assert trecho.index("SET TRANSACTION READ ONLY") < trecho.index("statement_timeout = '600s'") \
        < trecho.index("SQL_CMV,")


def test_procedencia_nomeia_a_base_de_item():
    lt = lote([esperado()], [cmv()])
    assert {linha(lt, "ml", "kokeshi", c)["procedencia"] for c in ("cmv", "cmv_receita_fiscal")} == {P.PROC_CMV}
    assert P.PROC_CMV.startswith("gold.nf_vendas_unificada_v2")


def test_celula_sem_custo_nenhum_fica_nula_nunca_zero():
    lt = lote([esperado()], [cmv(custo=None)])
    r = linha(lt, "ml", "kokeshi", "cmv")
    assert r["valor"] is None and r["status_reconciliacao"] == "ausente_na_origem"
    assert r["cobertura"] is None and r["provisorio_share"] is None
    assert linha(lt, "ml", "kokeshi", "cmv_receita_fiscal")["valor"] == Decimal(1_000_000)
    assert lt.recusas == []


def test_cmv_e_so_o_custo_da_receita_coberta_sem_extrapolar():
    """90% da receita com custo: o CMV e' o custo medido, e a cobertura diz quanto falta."""
    lt = lote([esperado()], [cmv(receita=1_000_000, custo=270_000, rcc=900_000)])
    r = linha(lt, "ml", "kokeshi", "cmv")
    assert r["valor"] == Decimal(270_000) and r["cobertura"] == Decimal("0.9")


def test_mapeamento_de_plataforma_e_o_da_torre():
    assert P.PLATAFORMA_FISCAL == {"tiktok": "tiktokshop", "ml": "mercadolivre", "shopee": "shopee"}


def test_plataforma_fora_da_torre_nao_e_publicada_nem_redistribuida():
    amazon = cmv("amazon", receita=777_777, custo=111_111)
    lt = lote([esperado("ml")], [cmv("ml"), amazon])
    assert lt.recusas == []
    assert {(r["canal"], r["marca"]) for r in lt.linhas} == {("ml", "kokeshi")}
    assert linha(lt, "ml", "kokeshi", "cmv")["valor"] == Decimal(300_000)  # nada da amazon entrou
    assert ("cmv", JUL, None, "kokeshi") in lt.fora_do_escopo


def test_mesma_entrada_mesmo_lote():
    entrada = dict(esp=[esperado("tiktok"), esperado("ml")], cmvs=[cmv("tiktok"), cmv("ml", custo=None)],
                   reps=[repasse()])
    assert lote(**entrada).linhas == lote(**entrada).linhas


def test_identidade_do_repasse_violada_recusa():
    lt = lote([esperado("tiktok")], [cmv("tiktok")], reps=[repasse(residuo=5)])
    assert any("identidade nao fecha" in m for m in lt.recusas)


def test_transacao_com_afiliado_maior_que_taxa_recusa():
    lt = lote([esperado("tiktok")], [cmv("tiktok")], reps=[repasse(viol=2)])
    assert any("afiliado maior que a taxa" in m for m in lt.recusas)


def test_afiliado_maior_que_taxa_no_agregado_recusa():
    lt = lote([esperado("tiktok")], [cmv("tiktok")], reps=[repasse(taxa=-50_000, afiliado=-90_000)])
    assert any("no agregado" in m for m in lt.recusas)


def test_taxa_positiva_recusa():
    lt = lote([esperado("tiktok")], [cmv("tiktok")], reps=[repasse(taxa=10)])
    assert any("taxa positiva" in m for m in lt.recusas)


def test_cmv_negativo_recusa():
    lt = lote([esperado()], [cmv(custo=-1)])
    assert any("cmv negativo" in m for m in lt.recusas)


def test_provisorio_acima_de_um_recusa_sem_clamp():
    lt = lote([esperado()], [cmv(custo=100, prov=150)])
    assert any("provisorio fora de [0,1]" in m for m in lt.recusas)


def test_chave_repetida_na_fonte_recusa():
    c = cmv()
    lt = lote([esperado()], [c, dict(c)])
    assert any("chave repetida" in m for m in lt.recusas)


def test_repasse_ok_publica_evidencia_da_inclusao_do_afiliado():
    lt = lote([esperado("tiktok")], [cmv("tiktok")], reps=[repasse()])
    assert lt.recusas == []
    assert linha(lt, "tiktok", "kokeshi", "tiktok_residuo_identidade")["valor"] == Decimal(0)
    assert linha(lt, "tiktok", "kokeshi", "tiktok_tx_afiliado_maior_taxa")["valor"] == Decimal(0)
    tx = linha(lt, "tiktok", "kokeshi", "tiktok_taxa_repasse")
    af = linha(lt, "tiktok", "kokeshi", "tiktok_afiliado_repasse")
    assert tx["sinal_origem"] == -1 and af["sinal_origem"] == -1
    assert af["valor"] >= tx["valor"]  # afiliado contido na taxa (ambos negativos)


def test_sinal_da_origem_e_preservado_sem_abs():
    lt = lote([esperado("tiktok")], [cmv("tiktok")], reps=[repasse()])
    assert linha(lt, "tiktok", "kokeshi", "tiktok_taxa_repasse")["valor"] == Decimal(-290_000)
    assert linha(lt, "tiktok", "kokeshi", "cmv")["valor"] == Decimal(300_000)


def test_provisorio_e_cobertura_viajam_por_linha():
    lt = lote([esperado()], [cmv(custo=300_000, prov=30_000, rcc=990_000)])
    r = linha(lt, "ml", "kokeshi", "cmv")
    assert r["provisorio_share"] == Decimal("0.1") and r["cobertura"] == Decimal("0.99")


def test_data_sem_fuso_da_silver_vira_utc_e_data_fiscal_vira_brt():
    lt = lote([esperado("tiktok")], [cmv("tiktok")], reps=[repasse()])
    assert linha(lt, "tiktok", "kokeshi", "tiktok_taxa_repasse")["data_max_origem"].tzinfo == timezone.utc
    assert linha(lt, "tiktok", "kokeshi", "cmv")["data_max_origem"].utcoffset().total_seconds() == -3 * 3600


# ---------------------------------------------------------------------------
# Escopo vazio nunca vira success (revisao estrita #68, item 2)
# ---------------------------------------------------------------------------

def test_universo_esperado_vazio_e_recusado():
    lt = lote([], [], canais=["tiktok", "ml", "shopee"])
    assert any("universo esperado vazio" in m for m in lt.recusas)


def test_par_competencia_canal_sem_celula_e_recusado():
    lt = lote([esperado("ml")], [cmv("ml")], canais=["ml", "shopee"])
    assert any(m.startswith("2026-07 shopee: nenhuma celula") for m in lt.recusas)
    assert not any("2026-07 ml" in m for m in lt.recusas)


def test_mes_sem_celula_dentro_da_janela_e_recusado():
    ago = date(2026, 8, 1)
    lt = lote([esperado("ml")], [cmv("ml")], canais=["ml"], comps=(JUL, ago))
    assert any(m.startswith("2026-08 ml: nenhuma celula") for m in lt.recusas)


def test_sem_vendas_declarado_libera_o_par():
    lt = lote([esperado("ml")], [cmv("ml")], canais=["ml", "shopee"], sem_vendas=frozenset({(JUL, "shopee")}))
    assert lt.recusas == []


def test_sem_vendas_declarado_com_receita_e_contradicao():
    lt = lote([esperado("ml")], [cmv("ml")], canais=["ml"], sem_vendas=frozenset({(JUL, "ml")}))
    assert any("declarado --sem-vendas, mas ha receita" in m for m in lt.recusas)


def test_universo_vazio_so_passa_com_todos_os_pares_declarados():
    tudo = frozenset({(JUL, "ml"), (JUL, "shopee")})
    assert lote([], [], canais=["ml", "shopee"], sem_vendas=tudo).recusas == []
    parcial = frozenset({(JUL, "ml")})
    assert any("universo esperado vazio" in m
               for m in lote([], [], canais=["ml", "shopee"], sem_vendas=parcial).recusas)


# ---------------------------------------------------------------------------
# Validacao do CLI (revisao estrita #68, item 3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv,trecho", [
    (["--meses", "0"], "--meses precisa ser inteiro positivo"),
    (["--meses", "-2"], "--meses precisa ser inteiro positivo"),
    (["--de", "2026-08", "--ate", "2026-07"], "posterior a --ate"),
    (["--de", "2026-07"], "--de e --ate vao juntos"),
    (["--de", "2026-7", "--ate", "2026-07"], "use YYYY-MM"),
    (["--canais", "tiktok,amazon"], "canal desconhecido: amazon"),
    (["--canais", ""], "--canais vazio"),
    (["--canais", " , ,"], "--canais vazio"),
    (["--sem-vendas", "2026-07"], "use YYYY-MM:canal"),
    (["--sem-vendas", "2026-07:amazon"], "canal fora de --canais"),
    (["--de", "2026-07", "--ate", "2026-07", "--sem-vendas", "2026-06:ml"], "fora da janela"),
    (["--de", "2026-07", "--ate", "2026-07", "--remover-marca", "2026-07:ml"], "use YYYY-MM:canal:marca"),
    (["--de", "2026-07", "--ate", "2026-07", "--remover-marca", "2026-07:ml:kok:x"], "use YYYY-MM:canal:marca"),
    (["--de", "2026-07", "--ate", "2026-07", "--remover-marca", "2026-7:ml:kokeshi"], "use YYYY-MM"),
    (["--de", "2026-07", "--ate", "2026-07", "--remover-marca", "2026-06:ml:kokeshi"], "fora da janela"),
    (["--de", "2026-07", "--ate", "2026-07", "--remover-marca", "2026-07:amazon:kokeshi"], "canal fora de --canais"),
    (["--de", "2026-07", "--ate", "2026-07", "--remover-marca", "2026-07:ml:"], "marca invalida"),
    (["--de", "2026-07", "--ate", "2026-07", "--remover-marca", "2026-07:ml:Kokeshi Ltda"], "marca invalida"),
    (["--de", "2026-07", "--ate", "2026-07", "--sem-vendas", "2026-07:ml", "--remover-marca", "2026-07:ml:kokeshi"],
     "ja esta declarado em --sem-vendas"),
])
def test_cli_recusa_argumento_invalido_com_mensagem(argv, trecho, capsys):
    chamou = []
    with pytest.raises(SystemExit) as exc:
        P.main(argv, engines=lambda: chamou.append(1), hoje=date(2026, 10, 13))
    assert exc.value.code == 2
    assert trecho in capsys.readouterr().err
    assert chamou == []  # nenhuma conexao foi aberta


def test_cli_canais_validos_sao_normalizados_na_ordem_canonica():
    import argparse

    args = argparse.Namespace(de="2026-07", ate="2026-07", meses=4, canais=" shopee ,ml", sem_vendas=None)
    comps, canais, sem, remover = P.validar_argumentos(args, date(2026, 10, 13))
    assert comps == [JUL] and canais == ["ml", "shopee"] and sem == frozenset() and remover == frozenset()


# ---------------------------------------------------------------------------
# Contrato com o serving e com a migration
# ---------------------------------------------------------------------------

def _carregar_migration(arquivo: str):
    """Constantes de uma migration SEM importar `alembic`.

    Executa o fonte num namespace isolado, com `op = None`. Injetar um
    `alembic` falso em `sys.modules` contaminaria qualquer outro teste da
    mesma sessao que importe o Alembic de verdade.
    """
    import types

    caminho = REPO / "apps" / "api" / "alembic" / "versions" / arquivo
    fonte = caminho.read_text(encoding="utf-8")
    assert fonte.count("from alembic import op") == 1
    ns: dict = {}
    exec(compile(fonte.replace("from alembic import op", "op = None"), str(caminho), "exec"), ns)  # noqa: S102 — fonte do proprio repo
    return types.SimpleNamespace(**{k: v for k, v in ns.items() if not k.startswith("__")})


def carregar_migration_025():
    return _carregar_migration("025_create_fact_dre_insumo_mensal.py")


def carregar_migration_037():
    """T-0059: estende o CHECK de componentes da 025 com `shopee_taxa_escrow`."""
    return _carregar_migration("037_dre_insumo_taxa_shopee_escrow.py")


_migration = carregar_migration_025


def _lista_do_check(sql: str, nome: str) -> tuple:
    import re

    bloco = re.search(nome + r" CHECK \((?:\w+) IN \((.*?)\)\)", sql, re.DOTALL).group(1)
    return tuple(re.findall(r"'([a-z_]+)'", bloco))


def test_componentes_publicados_existem_no_check_da_migration():
    """Lista EFETIVA no head: a da 025 estendida pela 037 (T-0059)."""
    lt = lote([esperado("tiktok"), esperado("ml"), esperado("shopee", "barbours")],
              [cmv("tiktok"), cmv("ml")], reps=[repasse()])
    lt_tx = P.montar_lote(
        esperado=[esperado("shopee", "barbours")], cmv=[], contraprova=[], repasse=[], canais=["shopee"],
        comps=[JUL], taxa_shopee=[{"competencia": JUL, "marca": "barbours", "pedidos": 10, "pedidos_com_escrow": 9,
                                   "taxa": Decimal(100), "parcelas_negativas": 0, "atualizado_em": None}])
    m025, m037 = _migration(), carregar_migration_037()
    efetiva = set(m037.COMPONENTES)
    assert {r["componente"] for r in lt.linhas + lt_tx.linhas} <= efetiva
    assert P.TAXA_SHOPEE in {r["componente"] for r in lt_tx.linhas}
    # A 037 so' acrescenta: a lista da 025 continua inteira, na mesma ordem.
    assert m037.COMPONENTES == m025.COMPONENTES + (P.TAXA_SHOPEE,)
    assert {r["status_reconciliacao"] for r in lt.linhas + lt_tx.linhas} <= set(m025.STATUS)
    assert m025.down_revision == "024" and m025.revision == "025"
    assert m037.down_revision == "036" and m037.revision == "037"


def test_037_troca_so_o_check_de_componentes_e_o_downgrade_restaura_a_025():
    m025, m037 = carregar_migration_025(), carregar_migration_037()
    (up,) = m037.UPGRADE_SQL
    apaga, restaura = m037.DOWNGRADE_SQL
    assert _lista_do_check(up, "ck_fdim_componente") == m037.COMPONENTES
    assert _lista_do_check(restaura, "ck_fdim_componente") == m025.COMPONENTES
    for sql in (up, restaura):
        assert sql.count("CONSTRAINT") == 2 and "DROP CONSTRAINT ck_fdim_componente," in sql
        assert m037.TABELA in sql
    # Downgrade apaga SO' o componente novo, com WHERE, antes de restaurar o CHECK.
    assert apaga.strip() == f"DELETE FROM {m037.TABELA} WHERE componente = '{P.TAXA_SHOPEE}'"


def test_check_literal_da_migration_lista_exatamente_as_tuplas():
    m = carregar_migration_025()
    sql = m.UPGRADE_SQL[0]
    assert _lista_do_check(sql, "ck_fdim_componente") == m.COMPONENTES
    assert _lista_do_check(sql, "ck_fdim_status") == m.STATUS


def test_faixa_do_cmv_e_a_mesma_do_serving():
    fonte = (REPO / "apps" / "api" / "app" / "services" / "dre_service.py").read_text(encoding="utf-8")
    assert "CMV_RAZAO_MIN = 0.95" in fonte and "CMV_RAZAO_MAX = 1.05" in fonte
    assert (P.CMV_RAZAO_MIN, P.CMV_RAZAO_MAX) == (Decimal("0.95"), Decimal("1.05"))


# ---------------------------------------------------------------------------
# Janela, CLI e seguranca operacional
# ---------------------------------------------------------------------------

def test_janela_padrao_nunca_inclui_o_mes_corrente():
    assert P.janela_padrao(date(2026, 10, 13), 4) == [date(2026, 6, 1), date(2026, 7, 1),
                                                      date(2026, 8, 1), date(2026, 9, 1)]


def test_cli_recusa_competencia_corrente():
    with pytest.raises(SystemExit) as exc:
        P.main(["--de", "2026-10", "--ate", "2026-10"], engines=lambda: (None, None), hoje=date(2026, 10, 13))
    assert exc.value.code == 2


def test_sanitizar_tira_dsn_host_e_ip():
    msg = P.sanitizar("falhou postgresql://u:SEGREDO@db.exemplo:5432/x host=10.1.2.3 password=abc")
    assert "SEGREDO" not in msg and "10.1.2.3" not in msg and "abc" not in msg


@pytest.mark.parametrize("url,esperada", [
    ("postgresql://u@h/d", "postgresql+psycopg2://u@h/d"),
    ("postgresql+psycopg2://u@h/d", "postgresql+psycopg2://u@h/d"),
    ("postgresql+psycopg://u@h/d", "postgresql+psycopg://u@h/d"),
])
def test_driver_psycopg2_fixado_so_quando_ausente(url, esperada):
    assert P.url_psycopg2(url) == esperada


def test_publisher_nao_importa_alembic_nem_app():
    """Compatibilidade com o Airflow: o modulo nao pode puxar Alembic (que
    colidiria com o banco de metadados do Airflow) nem a API."""
    arvore = ast.parse((REPO / "pipelines" / "dre" / "insumo_publisher.py").read_text(encoding="utf-8"))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados |= {a.name.split(".")[0] for a in no.names}
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    assert "alembic" not in importados and "app" not in importados
    assert importados <= {"__future__", "argparse", "json", "logging", "re", "sys", "dataclasses", "datetime",
                          "decimal", "typing", "sqlalchemy", "pipelines"}


def test_pacote_dre_entra_na_wheel_dos_pipelines():
    texto = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pipelines.dre"' in texto


# ---------------------------------------------------------------------------
# Marca ja publicada nao some em silencio (DRE-REVIEW-FINAL)
# ---------------------------------------------------------------------------

KOK, BAR = (JUL, "ml", "kokeshi"), (JUL, "ml", "barbours")


def test_marca_publicada_que_some_de_par_ativo_e_recusada_e_nomeada():
    lt = lote([esperado("ml", "kokeshi")], [cmv("ml", "kokeshi")], publicadas=frozenset({KOK, BAR}))
    assert any("chave publicada desapareceu: 2026-07:ml:barbours" in m for m in lt.recusas)
    assert any("--remover-marca 2026-07:ml:barbours" in m for m in lt.recusas)


def test_remocao_explicita_libera_a_chave():
    lt = lote([esperado("ml", "kokeshi")], [cmv("ml", "kokeshi")], publicadas=frozenset({KOK, BAR}),
              remover=frozenset({BAR}))
    assert lt.recusas == []


def test_remocao_de_chave_com_receita_e_contradicao():
    lt = lote([esperado("ml", "kokeshi")], [cmv("ml", "kokeshi")], publicadas=frozenset({KOK}),
              remover=frozenset({KOK}))
    assert any("ainda tem receita" in m for m in lt.recusas)


def test_remocao_de_chave_nao_publicada_e_recusada():
    lt = lote([esperado("ml", "kokeshi")], [cmv("ml", "kokeshi")], publicadas=frozenset({KOK}),
              remover=frozenset({BAR}))
    assert any("nao esta publicada" in m for m in lt.recusas)


def test_marca_nova_nao_e_problema():
    lt = lote([esperado("ml", "kokeshi"), esperado("ml", "barbours")],
              [cmv("ml", "kokeshi"), cmv("ml", "barbours")], publicadas=frozenset({KOK}))
    assert lt.recusas == []


def test_par_declarado_sem_vendas_autoriza_no_grao_do_par():
    lt = lote([], [], canais=["ml"], publicadas=frozenset({KOK, BAR}), sem_vendas=frozenset({(JUL, "ml")}))
    assert lt.recusas == []


def test_excecoes_autorizadas_sao_texto_auditavel():
    assert P.excecoes_autorizadas(frozenset(), frozenset()) is None
    txt = P.excecoes_autorizadas(frozenset({(JUL, "shopee")}), frozenset({BAR}))
    assert txt == "excecoes autorizadas: sem_vendas=2026-07:shopee; remover_marca=2026-07:ml:barbours"
