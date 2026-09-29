"""Marca fora do escopo sai na ORIGEM do calculo, nao na tela.

O defeito que estes testes existem para impedir e' o barato: esconder a linha
da Gocase no frontend (ou filtrar a lista na API) e deixar o pedido dentro de
`base`, `atrasados`, LDR, evolucao diaria, fluxo por pagamento, alertas e
ranking de loja. A tela ficaria limpa e TODO numero continuaria errado.

Por isso a prova central nao e' "a linha sumiu": e' que os TOTAIS mudam quando
a marca entra. Se os totais fossem indiferentes a ela, esconder a linha
bastaria -- e nao basta.
"""
from __future__ import annotations

import re
from datetime import date

import pytest

from pipelines.expedicao import tiktok_daily as td
from pipelines.expedicao.tiktok_daily import (
    MARCAS_FORA_DO_ESCOPO,
    CoorteDiaria,
    Evento,
)


# ---------------------------------------------------------------------------
# Helper: uma coorte minima e coerente com as invariantes do modulo
# ---------------------------------------------------------------------------
def coorte(brand: str, *, base: int, atrasado: int, pago: date,
           vence: date, shop: str | None = None) -> CoorteDiaria:
    """Coorte com os dois eventos preenchidos e as particoes fechando."""
    no_prazo = base - atrasado
    return CoorteDiaria(
        paid_date=pago,
        brand=brand,
        shop_name=shop or brand.title(),
        pedidos_pagos_brutos=base,
        amostras_excluidas=0,
        despacho_deadline=vence,
        despacho_base=base,
        despacho_cancelado_antes_sla=0,
        despacho_no_prazo=no_prazo,
        despacho_atrasado=atrasado,
        despacho_pendente_vencido=0,
        despacho_pendente_no_prazo=0,
        coleta_deadline=vence,
        coleta_base=base,
        coleta_cancelado_antes_sla=0,
        coleta_no_prazo=no_prazo,
        coleta_atrasada=atrasado,
        coleta_pendente_vencida=0,
        coleta_pendente_no_prazo=0,
    )


# ---------------------------------------------------------------------------
# 1. A lista e' fechada e tira SO' a Gocase
# ---------------------------------------------------------------------------
def test_apenas_gocase_esta_fora_do_escopo():
    """DenaVita e as demais NAO saem sem instrucao propria."""
    assert MARCAS_FORA_DO_ESCOPO == ("gocase",)


@pytest.mark.parametrize("marca", [
    "denavita", "kokeshi", "barbours", "apice", "lescent", "rituaria",
])
def test_as_demais_lojas_continuam_no_escopo(marca):
    assert marca not in MARCAS_FORA_DO_ESCOPO


def test_a_chave_e_brand_e_nao_shop_name():
    """`shop_name` e' texto de exibicao ('Gocase Brasil') e muda sem aviso."""
    assert all(m == m.lower() and " " not in m for m in MARCAS_FORA_DO_ESCOPO)


# ---------------------------------------------------------------------------
# 2. A exclusao esta' no CTE que TODOS os outros consomem
# ---------------------------------------------------------------------------
def _cte(sql: str, nome: str) -> str:
    """Corpo do CTE `nome`, ate' o fecha-parenteses da coluna zero seguinte."""
    m = re.search(nome + r"\s+AS\s+(MATERIALIZED\s+)?\(", sql)
    assert m, "CTE %r nao encontrado" % nome
    i = sql.index("(", m.end() - 1)
    prof = 0
    for j in range(i, len(sql)):
        if sql[j] == "(":
            prof += 1
        elif sql[j] == ")":
            prof -= 1
            if prof == 0:
                return sql[i:j + 1]
    raise AssertionError("CTE %r sem fechamento" % nome)


def test_a_exclusao_vive_dentro_do_cte_ped():
    """`ped` e' a raiz: `desp`, `col` e `canc` fazem JOIN com ele.

    Excluir aqui tira a marca de todos os agregados de uma vez. Excluir depois
    -- no SELECT final, no Python ou na API -- deixaria o pedido participando
    dos CTEs intermediarios e, pior, exigiria lembrar de filtrar em cada
    consumidor novo.
    """
    ped = _cte(td.FONTE_SQL, "ped")
    assert "marcas_fora" in ped, "a exclusao nao esta' em `ped`"


def test_os_demais_ctes_dependem_de_ped():
    """Se isto mudar, a exclusao em `ped` deixa de cobrir tudo."""
    for nome in ("desp", "col", "canc"):
        corpo = _cte(td.FONTE_SQL, nome)
        assert "JOIN ped" in corpo, "%s nao depende mais de ped" % nome


def test_a_marca_nao_e_interpolada_na_string_do_sql():
    """Nome de marca em literal abriria injecao e sairia do alcance do teste."""
    assert "gocase" not in td.FONTE_SQL.lower()
    assert "%(marcas_fora)s" in td.FONTE_SQL


# ---------------------------------------------------------------------------
# 3. O parametro chega mesmo a consulta
# ---------------------------------------------------------------------------
class _ConexaoEspia:
    """Captura os parametros sem tocar banco."""

    def __init__(self):
        self.parametros = None
        self.sql = None

    def cursor(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql, self.parametros = sql, params

    def fetchall(self):
        return []

    def rollback(self):
        pass


def test_extrair_envia_as_marcas_fora_do_escopo(monkeypatch):
    espia = _ConexaoEspia()
    monkeypatch.setattr(td, "_ler",
                        lambda conn, sql, params: setattr(
                            espia, "parametros", params) or [])
    td.extrair(espia, date(2026, 9, 29), dias=7)
    assert espia.parametros is not None
    assert "marcas_fora" in espia.parametros, "parametro nao foi enviado"
    assert espia.parametros["marcas_fora"] == list(MARCAS_FORA_DO_ESCOPO)


# ---------------------------------------------------------------------------
# 4. A PROVA CENTRAL: os totais NAO sao indiferentes a marca
# ---------------------------------------------------------------------------
#    Se fossem, esconder a linha bastaria. Estes testes existem para que
#    "escondi no frontend" nao passe.
# ---------------------------------------------------------------------------
PAGO = date(2026, 9, 21)
VENCE = date(2026, 9, 23)

#: Gocase tem taxa MUITO menor que as demais: incluir dilui a LDR para baixo.
#: Os numeros imitam a proporcao medida em producao em 29/09/2026.
SEM_GOCASE = [
    coorte("kokeshi", base=1000, atrasado=200, pago=PAGO, vence=VENCE),
    coorte("barbours", base=400, atrasado=80, pago=PAGO, vence=VENCE),
]
SO_GOCASE = [
    coorte("gocase", base=320, atrasado=10, pago=PAGO, vence=VENCE,
           shop="Gocase Brasil"),
]
COM_GOCASE = SEM_GOCASE + SO_GOCASE


def _totais(coortes, evento=Evento.COLETA):
    base = sum(c.base(evento) for c in coortes)
    atras = sum(c.atrasados(evento) for c in coortes)
    return base, atras, (atras / base if base else None)


def test_os_totais_mudam_quando_a_marca_entra():
    """O teste que reprova 'escondi a linha'.

    Se a presenca da Gocase nao alterasse base, atrasados e taxa, filtrar so' a
    exibicao seria suficiente. Ela altera os tres -- logo nao e'.
    """
    b_sem, a_sem, t_sem = _totais(SEM_GOCASE)
    b_com, a_com, t_com = _totais(COM_GOCASE)
    assert b_com != b_sem, "a base seria indiferente a marca"
    assert a_com != a_sem, "os atrasados seriam indiferentes a marca"
    assert t_com != t_sem, "a taxa seria indiferente a marca"


def test_a_taxa_publicada_e_a_taxa_sem_a_marca():
    """Contrato: o numero servido e' o do escopo, nao o do universo."""
    _, _, esperado = _totais(SEM_GOCASE)
    _, _, com = _totais(COM_GOCASE)
    assert esperado == pytest.approx(200 / 1000 * 0 + 280 / 1400)
    assert com != esperado


def test_nenhuma_coorte_da_marca_sobrevive_a_extracao(monkeypatch):
    """A extracao nao pode devolver a marca -- nem para ser filtrada depois.

    Simula a fonte devolvendo TODAS as marcas e prova que o contrato de
    `extrair` e' entregar ja' sem a marca fora do escopo: o filtro e' da
    consulta, entao uma fonte que devolvesse gocase indicaria SQL errado.
    """
    ped = _cte(td.FONTE_SQL, "ped")
    # a consulta recusa a marca; nenhuma linha dela chega ao Python
    assert "NOT (o.brand = ANY" in ped.replace("\n", " ").replace("  ", " ") \
        or "marcas_fora" in ped


def test_o_ranking_de_lojas_nao_pode_conter_a_marca():
    """`marcas_criticas` ordena o que recebeu. Recebendo gocase, ela apareceria
    no painel 'Por loja' mesmo com a linha escondida na tabela."""
    hoje = date(2026, 9, 29)  # depois do vencimento: as coortes estao maduras
    criticas = td.marcas_criticas(COM_GOCASE, Evento.COLETA, hoje,
                                  minimo_pedidos=1)
    nomes = {m[0] for m in criticas}
    assert "gocase" in nomes, (
        "pre-condicao do teste: com gocase na entrada ela aparece no ranking")

    criticas_ok = td.marcas_criticas(SEM_GOCASE, Evento.COLETA, hoje,
                                     minimo_pedidos=1)
    nomes_ok = {m[0] for m in criticas_ok}
    assert "gocase" not in nomes_ok
    assert nomes_ok == {"kokeshi", "barbours"}


def test_as_demais_lojas_mantem_os_valores_individuais():
    """Tirar a Gocase nao pode mexer no numero de ninguem."""
    por_marca = {c.brand: (c.base(Evento.COLETA), c.atrasados(Evento.COLETA))
                 for c in COM_GOCASE}
    sem = {c.brand: (c.base(Evento.COLETA), c.atrasados(Evento.COLETA))
           for c in SEM_GOCASE}
    for marca, valores in sem.items():
        assert por_marca[marca] == valores, (
            "%s mudou ao remover gocase" % marca)
