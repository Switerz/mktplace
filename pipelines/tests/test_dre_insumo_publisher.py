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


def cmv(canal="ml", marca="kokeshi", receita=1_000_000, custo=300_000, prov=0, rcc=None, mes=JUL):
    return {"competencia": mes, "marca": marca, "plataforma": P.PLATAFORMA_FISCAL[canal],
            "receita_fiscal": Decimal(receita), "custo": None if custo is None else Decimal(custo),
            "custo_provisorio": Decimal(prov), "receita_com_custo": Decimal(rcc if rcc is not None else receita),
            "ultima_emissao": date(2026, 7, 31), "linhas": 10}


def cp(c):
    return {k: c[k] for k in ("competencia", "marca", "plataforma", "receita_fiscal", "custo")}


def repasse(marca="kokeshi", taxa=-290_000, frete=-12_000, afiliado=-90_000, residuo=0, viol=0, mes=JUL):
    return {"competencia": mes, "marca": marca, "taxa": Decimal(taxa), "frete": Decimal(frete),
            "receita": Decimal(1_050_000), "afiliado": Decimal(afiliado), "residuo": Decimal(residuo),
            "tx_afiliado_maior_taxa": viol, "transacoes": 1000, "atualizado_em": datetime(2026, 10, 1, 3, 0)}


def lote(esp, cmvs, reps=(), contraprova=None, canais=None, comps=(JUL,), sem_vendas=frozenset()):
    """Por padrao, os canais pedidos sao os que tem celula esperada — o mesmo que
    o operador faria. Testes de escopo passam `canais` explicitamente."""
    if canais is None:
        presentes = {r["marketplace_id"] for r in esp}
        canais = [c for c in P.CANAIS if P.MKT_ID[c] in presentes] or ["ml"]
    return P.montar_lote(esperado=list(esp), cmv=list(cmvs),
                         contraprova=list(contraprova if contraprova is not None else [cp(c) for c in cmvs]),
                         repasse=list(reps), canais=list(canais), comps=list(comps), sem_vendas=sem_vendas)


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
    lt = lote([esperado()], [cmv()], contraprova=[])
    assert any("so' em uma" in m for m in lt.recusas)


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
    comps, canais, sem = P.validar_argumentos(args, date(2026, 10, 13))
    assert comps == [JUL] and canais == ["ml", "shopee"] and sem == frozenset()


# ---------------------------------------------------------------------------
# Contrato com o serving e com a migration
# ---------------------------------------------------------------------------

def carregar_migration_025():
    """Constantes da migration 025 SEM importar `alembic`.

    Executa o fonte num namespace isolado, com `op = None`. Injetar um
    `alembic` falso em `sys.modules` contaminaria qualquer outro teste da
    mesma sessao que importe o Alembic de verdade.
    """
    import types

    caminho = REPO / "apps" / "api" / "alembic" / "versions" / "025_create_fact_dre_insumo_mensal.py"
    fonte = caminho.read_text(encoding="utf-8")
    assert fonte.count("from alembic import op") == 1
    ns: dict = {}
    exec(compile(fonte.replace("from alembic import op", "op = None"), str(caminho), "exec"), ns)  # noqa: S102 — fonte do proprio repo
    return types.SimpleNamespace(**{k: v for k, v in ns.items() if not k.startswith("__")})


_migration = carregar_migration_025


def test_componentes_publicados_existem_no_check_da_migration():
    lt = lote([esperado("tiktok"), esperado("ml")], [cmv("tiktok"), cmv("ml")], reps=[repasse()])
    m = _migration()
    assert {r["componente"] for r in lt.linhas} <= set(m.COMPONENTES)
    assert {r["status_reconciliacao"] for r in lt.linhas} <= set(m.STATUS)
    assert m.down_revision == "024" and m.revision == "025"


def test_check_literal_da_migration_lista_exatamente_as_tuplas():
    import re

    m = carregar_migration_025()
    sql = m.UPGRADE_SQL[0]

    def lista(nome):
        bloco = re.search(nome + r" CHECK \((?:\w+) IN \((.*?)\)\)", sql, re.DOTALL).group(1)
        return tuple(re.findall(r"'([a-z_]+)'", bloco))

    assert lista("ck_fdim_componente") == m.COMPONENTES
    assert lista("ck_fdim_status") == m.STATUS


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
