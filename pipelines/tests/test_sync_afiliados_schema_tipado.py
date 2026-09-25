"""Gate UE-9C2E4-B — o contrato de custo de afiliado contra PostgreSQL REAL.

Por que banco de verdade e nao dublê: o defeito que originou este gate foi de
SCHEMA. Em 18/09/2026 o commit `ea6a90aa` do repo do Airflow removeu a coluna
`fee_breakdown` da Silver e este modulo — consumidor EXTERNO daquele
repositorio — passou a referenciar coluna inexistente. Um fake devolve o que o
teste mandou: ele nao sabe que a coluna sumiu, entao continuaria verde enquanto
producao quebrava. Foi exatamente o que aconteceu.

Aqui o schema e' criado de verdade, o SQL do modulo roda de verdade, e uma
coluna ausente reprova de verdade.

Dados 100% sinteticos. Nenhuma credencial, nenhum `.env`, nenhum ID real:
`conftest.py` ja recusa qualquer conexao que nao seja localhost.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from pipelines import sync_tiktok_affiliate_cost_order_monthly as sync
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel,
)

pytestmark = pytest.mark.skipif(not postgres_disponivel(),
                                reason=MOTIVO_SEM_POSTGRES)

CUTOFF = datetime(2026, 9, 22, 2, 0, 0)
ANTERIOR = datetime(2026, 9, 15, 0, 0, 0)
JULHO = datetime(2026, 7, 10, 12, 0, 0)
AGOSTO = datetime(2026, 8, 11, 9, 0, 0)

#: Colunas extras que a Silver real tem e o contrato NAO exige. Existem aqui
#: para provar que o modulo nao depende delas.
EXTRAS = ("statement_id", "revenue_amount", "settlement_amount", "adjustment_amount")


def _ddl(colunas: list[str]) -> str:
    tipos = {
        "brand": "varchar", "order_id": "varchar", "transaction_id": "varchar",
        "transaction_type": "varchar", "currency": "varchar",
        "order_create_time": "timestamp", "updated_at": "timestamp",
        "statement_id": "varchar",
    }
    partes = [
        f"{c} {tipos.get(c, 'numeric')}" for c in colunas
    ]
    return (
        "DROP SCHEMA IF EXISTS silver CASCADE; CREATE SCHEMA silver; "
        "CREATE TABLE silver.stg_tiktok_payments_by_order (" + ", ".join(partes) + ")"
    )


def _linha(**over):
    base = {
        "brand": "apice", "order_id": "o1", "transaction_id": "t1",
        "transaction_type": "ORDER", "currency": "BRL",
        "order_create_time": JULHO, "updated_at": ANTERIOR,
        "statement_id": "s1",
        "affiliate_commission_amount": Decimal("-10.00"),
        "affiliate_partner_commission_amount": Decimal("-2.00"),
        "affiliate_ads_commission_amount": Decimal("-1.00"),
        "revenue_amount": Decimal("100"), "settlement_amount": Decimal("87"),
        "adjustment_amount": Decimal("0"),
    }
    base.update(over)
    return base


def _excluida(tipo, **over):
    """Transacao de tipo fora do escopo: sem pedido, componentes em ZERO —
    como medido em producao em 22/09/2026."""
    campos = {
        "transaction_type": tipo, "order_id": None,
        "affiliate_commission_amount": Decimal("0"),
        "affiliate_partner_commission_amount": Decimal("0"),
        "affiliate_ads_commission_amount": Decimal("0"),
        "adjustment_amount": Decimal("500.00"),
    }
    campos.update(over)
    return _linha(**campos)


@pytest.fixture
def banco():
    """Cursor RealDict sobre um schema recem-criado. Cada teste comeca limpo."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with cluster_da_sessao() as url:
        conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        conn.autocommit = True
        try:
            yield conn
        finally:
            conn.close()


def _montar(conn, linhas, colunas=None):
    colunas = list(colunas or (list(sync.REQUIRED_SOURCE_COLUMNS) + list(EXTRAS)))
    cur = conn.cursor()
    cur.execute(_ddl(colunas))
    for linha in linhas:
        presentes = [c for c in colunas if c in linha]
        cur.execute(
            "INSERT INTO silver.stg_tiktok_payments_by_order ("
            + ", ".join(presentes) + ") VALUES ("
            + ", ".join(["%s"] * len(presentes)) + ")",
            [linha[c] for c in presentes],
        )
    return cur


# ---------------------------------------------------------------------------
# 1. Forma da fonte
# ---------------------------------------------------------------------------

def test_a_silver_tipada_satisfaz_o_contrato(banco):
    cur = _montar(banco, [_linha()])
    presentes = sync.validate_source_schema(cur)
    assert "fee_breakdown" not in presentes
    assert set(sync.REQUIRED_SOURCE_COLUMNS) <= set(presentes)


@pytest.mark.parametrize("ausente", sync.REQUIRED_SOURCE_COLUMNS)
def test_coluna_obrigatoria_ausente_reprova_contra_banco_real(banco, ausente):
    """A prova que um dublê nao daria: a coluna some do CATALOGO de verdade."""
    colunas = [c for c in sync.REQUIRED_SOURCE_COLUMNS if c != ausente]
    cur = _montar(banco, [], colunas=colunas)
    with pytest.raises(RuntimeError, match="contrato da fonte violado") as e:
        sync.validate_source_schema(cur)
    assert ausente in str(e.value)


def test_o_sql_do_modulo_roda_contra_a_silver_sem_fee_breakdown(banco):
    """Regressao direta do incidente de 18/09: antes, isto levantava
    `UndefinedColumn: column "fee_breakdown" does not exist`."""
    cur = _montar(banco, [_linha()])
    sync.validate_source_schema(cur)
    keys = sync.discover_touched_keys(cur, None, CUTOFF)
    linhas = sync.recompute_keys(cur, keys, CUTOFF)
    assert linhas and linhas[0]["affiliate_creator_commission"] == Decimal("-10.00")


# ---------------------------------------------------------------------------
# 2. Os tipos conhecidos
# ---------------------------------------------------------------------------

def test_todos_os_tipos_excluidos_atravessam_sem_falhar_e_nao_contribuem(banco):
    """O coracao do gate: reconhecidos, logo nao derrubam a execucao; fora do
    escopo, logo nao entram na soma nem na contagem."""
    linhas = [_linha()] + [
        _excluida(t, transaction_id=f"x{i}")
        for i, t in enumerate(sync.TRANSACTION_TYPE_EXCLUDED)
    ]
    cur = _montar(banco, linhas)
    sync.validate_source_schema(cur)
    tipos = sync.validate_transaction_types(cur, None, CUTOFF)
    assert set(tipos) == set(sync.TRANSACTION_TYPE_KNOWN)

    sync.validate_excluded_components_are_zero(cur, CUTOFF)
    populacao = sync.validate_read_population(cur, CUTOFF)
    assert int(populacao["lidas"]) == 1, "so' a linha ORDER entra na populacao"

    agregado = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert len(agregado) == 1
    assert agregado[0]["source_row_count"] == 1
    assert agregado[0]["affiliate_creator_commission"] == Decimal("-10.00")


def test_tipo_desconhecido_falha_fechado(banco):
    cur = _montar(banco, [_linha(), _linha(transaction_id="t2",
                                           transaction_type="TIPO_NOVO_QUALQUER")])
    with pytest.raises(RuntimeError, match="transaction_type DESCONHECIDO") as e:
        sync.validate_transaction_types(cur, None, CUTOFF)
    assert "TIPO_NOVO_QUALQUER=1" in str(e.value)


def test_tipo_nulo_falha_fechado(banco):
    cur = _montar(banco, [_linha(), _linha(transaction_id="t2",
                                           transaction_type=None)])
    with pytest.raises(RuntimeError, match=r"<NULL>=1"):
        sync.validate_transaction_types(cur, None, CUTOFF)


@pytest.mark.parametrize("tipo", sync.TRANSACTION_TYPE_EXCLUDED)
def test_componente_nao_zero_em_tipo_excluido_falha(banco, tipo):
    """Contraprova por tipo, contra banco real: se a premissa da exclusao cair,
    continuar filtrando em silencio esconderia custo real."""
    cur = _montar(banco, [
        _linha(),
        _excluida(tipo, transaction_id="x9",
                  affiliate_commission_amount=Decimal("-77.50")),
    ])
    with pytest.raises(RuntimeError, match="componente de afiliado") as e:
        sync.validate_excluded_components_are_zero(cur, CUTOFF)
    assert f"{tipo}.affiliate_commission_amount=-77.50" in str(e.value)


def test_a_mensagem_de_erro_nao_vaza_identificador(banco):
    """Nome do tipo e contagem — nunca `order_id`/`transaction_id`."""
    cur = _montar(banco, [_linha(transaction_id="SEGREDO-123",
                                 order_id="PEDIDO-SIGILOSO",
                                 transaction_type="TIPO_NOVO")])
    with pytest.raises(RuntimeError) as e:
        sync.validate_transaction_types(cur, None, CUTOFF)
    texto = str(e.value)
    assert "SEGREDO-123" not in texto
    assert "PEDIDO-SIGILOSO" not in texto


# ---------------------------------------------------------------------------
# 3. Valores: sinal, zero, nulo, grao
# ---------------------------------------------------------------------------

def test_sinal_vem_da_fonte_sem_abs(banco):
    """Positivo e negativo na mesma chave: a soma tem de ser algebrica."""
    cur = _montar(banco, [
        _linha(transaction_id="a", affiliate_commission_amount=Decimal("-30.00")),
        _linha(transaction_id="b", affiliate_commission_amount=Decimal("10.00")),
    ])
    linhas = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert linhas[0]["affiliate_creator_commission"] == Decimal("-20.00")


def test_zero_e_zero_e_nulo_em_todas_as_linhas_e_nulo(banco):
    """`SUM` ignora nulo e devolve NULL quando TODAS sao nulas — nunca 0.
    Distinguir 'medido zero' de 'nao medido' e' exigencia do contrato."""
    cur = _montar(banco, [
        _linha(transaction_id="a", affiliate_commission_amount=Decimal("0"),
               affiliate_partner_commission_amount=None,
               affiliate_ads_commission_amount=None),
        _linha(transaction_id="b", affiliate_commission_amount=Decimal("0"),
               affiliate_partner_commission_amount=None,
               affiliate_ads_commission_amount=Decimal("-5.00")),
    ])
    linha = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)[0]
    assert linha["affiliate_creator_commission"] == Decimal("0")
    assert linha["affiliate_partner_commission"] is None, "nulo nao vira zero"
    assert linha["affiliate_ads_commission"] == Decimal("-5.00"), "nulo ignorado"


def test_grao_mensal_por_marca(banco):
    cur = _montar(banco, [
        _linha(transaction_id="a", brand="apice", order_create_time=JULHO),
        _linha(transaction_id="b", brand="apice", order_create_time=JULHO),
        _linha(transaction_id="c", brand="apice", order_create_time=AGOSTO),
        _linha(transaction_id="d", brand="kokeshi", order_create_time=JULHO),
    ])
    linhas = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    chaves = {(linha["ref_month"].isoformat(), linha["brand"]) for linha in linhas}
    assert chaves == {
        ("2026-07-01", "apice"), ("2026-08-01", "apice"), ("2026-07-01", "kokeshi"),
    }
    julho_apice = next(linha for linha in linhas
                       if linha["brand"] == "apice"
                       and linha["ref_month"].isoformat() == "2026-07-01")
    assert julho_apice["source_row_count"] == 2


def test_zero_dupla_contagem_o_detalhe_bate_com_o_agregado(banco):
    """Se o `GROUP BY` duplicasse uma chave, o total do detalhe divergiria."""
    cur = _montar(banco, [
        _linha(transaction_id=f"t{i}", brand=b, order_create_time=d)
        for i, (b, d) in enumerate(
            [("apice", JULHO), ("apice", AGOSTO), ("kokeshi", JULHO)] * 3)
    ])
    keys = sync.discover_touched_keys(cur, None, CUTOFF)
    linhas = sync.recompute_keys(cur, keys, CUTOFF)
    totais = sync.detail_totals(cur, keys, CUTOFF)
    for coluna in sync.COMPONENT_COLUMNS:
        assert sum(linha[coluna] for linha in linhas) == totais[coluna]
    assert sum(linha["source_row_count"] for linha in linhas) == \
        totais["source_row_count"]


def test_invariancia_por_ordem_de_insercao(banco):
    """O agregado nao pode depender da ordem fisica das linhas."""
    base = [
        _linha(transaction_id="a", affiliate_commission_amount=Decimal("-1.11")),
        _linha(transaction_id="b", affiliate_commission_amount=Decimal("-2.22")),
        _linha(transaction_id="c", affiliate_commission_amount=Decimal("-3.33")),
    ]
    cur = _montar(banco, base)
    direto = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    cur = _montar(banco, list(reversed(base)))
    invertido = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert direto == invertido


def test_moeda_estranha_reprova_contra_banco_real(banco):
    cur = _montar(banco, [_linha(), _linha(transaction_id="t2", currency="USD")])
    with pytest.raises(RuntimeError, match="moeda diferente de BRL"):
        sync.validate_read_population(cur, CUTOFF)


# ---------------------------------------------------------------------------
# UE-9C2E4-D2 — EARLY_SETTLEMENT_DISBURSEMENT
#
# O tipo que derrubou o run natural de 23/09/2026 as 06:00. As fixtures abaixo
# reproduzem a FORMA exata medida em producao nas 3 linhas existentes: sem
# pedido, componentes de afiliado em zero, todo o valor em
# `settlement_amount` == `adjustment_amount`, com `adjustment_id` preenchido.
# ---------------------------------------------------------------------------

ESD = "EARLY_SETTLEMENT_DISBURSEMENT"


def _desembolso(**over):
    campos = {
        "transaction_type": ESD, "order_id": None,
        "affiliate_commission_amount": Decimal("0"),
        "affiliate_partner_commission_amount": Decimal("0"),
        "affiliate_ads_commission_amount": Decimal("0"),
        "revenue_amount": Decimal("0"),
        "settlement_amount": Decimal("204683.00"),
        "adjustment_amount": Decimal("204683.00"),
    }
    campos.update(over)
    return _linha(**campos)


def test_esd_atravessa_sem_falhar_e_nao_contribui(banco):
    """Reconhecido: nao derruba a execucao. Fora do escopo: nao entra na soma
    nem na contagem."""
    linhas = [_linha()] + [
        _desembolso(transaction_id=f"esd{i}", brand=b)
        for i, b in enumerate(("apice", "kokeshi", "rituaria"))
    ]
    cur = _montar(banco, linhas)
    sync.validate_source_schema(cur)
    tipos = sync.validate_transaction_types(cur, None, CUTOFF)
    assert ESD in tipos and tipos[ESD] == 3

    sync.validate_excluded_components_are_zero(cur, CUTOFF)
    populacao = sync.validate_read_population(cur, CUTOFF)
    assert int(populacao["lidas"]) == 1, "so' a linha ORDER entra na populacao"

    agregado = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert len(agregado) == 1
    assert agregado[0]["source_row_count"] == 1
    assert agregado[0]["affiliate_creator_commission"] == Decimal("-10.00")


@pytest.mark.parametrize("componente", sorted(sync.COMPONENT_SOURCE_COLUMNS.values()))
def test_esd_com_componente_nao_zero_falha(banco, componente):
    """A exclusao vale PORQUE os componentes sao zero. Contraprova por coluna."""
    cur = _montar(banco, [
        _desembolso(transaction_id="esd1", **{componente: Decimal("-1.00")}),
    ])
    with pytest.raises(RuntimeError, match="componente de afiliado") as e:
        sync.validate_excluded_components_are_zero(cur, CUTOFF)
    assert f"{ESD}.{componente}=-1.00" in str(e.value)


def test_esd_nao_altera_os_valores_de_order(banco):
    """Invariancia: a presenca do tipo novo nao pode mexer no que ORDER produz."""
    so_order = [_linha(transaction_id="a"),
                _linha(transaction_id="b", brand="kokeshi")]
    cur = _montar(banco, so_order)
    sem = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    cur = _montar(banco, so_order + [_desembolso(transaction_id="esd1"),
                                     _desembolso(transaction_id="esd2")])
    com = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert sem == com


def test_um_tipo_desconhecido_continua_falhando_com_o_esd_presente(banco):
    """O guardrail nao foi afrouxado: reconhecer o desembolso nao abre a porta.

    (SOURCES-RECOVERY-2 renomeou: o universo conhecido deixou de ter oito
    valores, entao contar a ordinal do proximo tipo envelhecia o nome a cada
    adicao. O que o teste prova nao mudou.)"""
    cur = _montar(banco, [_linha(),
                          _desembolso(transaction_id="esd1"),
                          _linha(transaction_id="z", transaction_type="TIPO_AINDA_NOVO")])
    with pytest.raises(RuntimeError, match="transaction_type DESCONHECIDO") as e:
        sync.validate_transaction_types(cur, None, CUTOFF)
    assert "TIPO_AINDA_NOVO=1" in str(e.value)
    assert ESD not in str(e.value).split("DESCONHECIDO na janela lida:")[1].split(".")[0]


def test_tipo_nulo_continua_falhando_com_o_esd_presente(banco):
    cur = _montar(banco, [_linha(),
                          _desembolso(transaction_id="esd1"),
                          _linha(transaction_id="z", transaction_type=None)])
    with pytest.raises(RuntimeError, match=r"<NULL>=1"):
        sync.validate_transaction_types(cur, None, CUTOFF)


# ---------------------------------------------------------------------------
# SOURCES-RECOVERY-2 — EARLY_SETTLEMENT_RECOVERY
#
# O tipo que derrubou o run de 24/09/2026. As fixtures reproduzem a FORMA exata
# medida em producao — duas vezes, 3 linhas em 24/09 e 6 em 25/09, nas marcas
# apice, barbours e kokeshi: sem pedido, componentes de afiliado em zero,
# `revenue`/`fee`/`shipping` zero, todo o valor em
# `settlement_amount` == `adjustment_amount`, `adjustment_id` preenchido.
#
# O SINAL e' o que o distingue do desembolso: -141.757,00 no total, contra
# +614.049,00 do ESD. O fato nao depende da direcao economica — os componentes
# sao zero dos dois lados —, mas a fixture usa o sinal REAL de proposito: uma
# fixture positiva esconderia um eventual `abs()` no caminho de exclusao.
# ---------------------------------------------------------------------------

ESR = "EARLY_SETTLEMENT_RECOVERY"


def _recuperacao(**over):
    campos = {
        "transaction_type": ESR, "order_id": None,
        "affiliate_commission_amount": Decimal("0"),
        "affiliate_partner_commission_amount": Decimal("0"),
        "affiliate_ads_commission_amount": Decimal("0"),
        "revenue_amount": Decimal("0"),
        "settlement_amount": Decimal("-23626.17"),
        "adjustment_amount": Decimal("-23626.17"),
    }
    campos.update(over)
    return _linha(**campos)


def test_esr_e_reconhecido_como_excluido():
    """Requisito 1. A constante e' a autoridade: o resto do modulo deriva dela."""
    assert ESR in sync.TRANSACTION_TYPE_EXCLUDED
    assert ESR in sync.TRANSACTION_TYPE_KNOWN
    assert ESR not in sync.TRANSACTION_TYPE_ALLOWLIST


def test_esr_atravessa_sem_falhar_e_nao_contribui(banco):
    """Requisitos 1, 2 e 3: reconhecido (nao derruba), fora do escopo (nao entra
    na soma de afiliado nem no denominador de cobertura)."""
    linhas = [_linha()] + [
        _recuperacao(transaction_id=f"esr{i}", brand=b)
        for i, b in enumerate(("apice", "barbours", "kokeshi"))
    ]
    cur = _montar(banco, linhas)
    sync.validate_source_schema(cur)
    tipos = sync.validate_transaction_types(cur, None, CUTOFF)
    assert ESR in tipos and tipos[ESR] == 3

    sync.validate_excluded_components_are_zero(cur, CUTOFF)
    populacao = sync.validate_read_population(cur, CUTOFF)
    assert int(populacao["lidas"]) == 1, "so' a linha ORDER entra na populacao"

    agregado = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert len(agregado) == 1
    assert agregado[0]["source_row_count"] == 1
    assert agregado[0]["affiliate_creator_commission"] == Decimal("-10.00")


def test_esr_sozinho_nao_produz_chave_alguma(banco):
    """Requisito 4: sem pedido, nao ha coorte a que pertencer. Uma janela SO'
    com recuperacoes tem de produzir zero chaves — nunca uma chave sintetica."""
    cur = _montar(banco, [_recuperacao(transaction_id=f"esr{i}") for i in range(3)])
    sync.validate_transaction_types(cur, None, CUTOFF)
    sync.validate_excluded_components_are_zero(cur, CUTOFF)
    assert int(sync.validate_read_population(cur, CUTOFF)["lidas"]) == 0
    assert sync.discover_touched_keys(cur, None, CUTOFF) == []


def test_esr_com_order_id_preenchido_nao_vira_custo_de_afiliado(banco):
    """Requisito 4, contraprova: mesmo que a fonte passe a trazer `order_id`, o
    tipo continua fora da populacao. A exclusao e' por TIPO, nao por ausencia de
    pedido — do contrario a mudanca de um campo mudaria a semantica do fato."""
    cur = _montar(banco, [_linha(), _recuperacao(transaction_id="esr1",
                                                 order_id="PEDIDO-X")])
    sync.validate_excluded_components_are_zero(cur, CUTOFF)
    assert int(sync.validate_read_population(cur, CUTOFF)["lidas"]) == 1
    agregado = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert len(agregado) == 1
    assert agregado[0]["source_row_count"] == 1


@pytest.mark.parametrize("componente", sorted(sync.COMPONENT_SOURCE_COLUMNS.values()))
def test_esr_com_componente_nao_zero_falha(banco, componente):
    """A exclusao vale PORQUE os componentes sao zero. Contraprova por coluna:
    se a premissa cair, filtrar em silencio esconderia custo real."""
    cur = _montar(banco, [
        _recuperacao(transaction_id="esr1", **{componente: Decimal("-1.00")}),
    ])
    with pytest.raises(RuntimeError, match="componente de afiliado") as e:
        sync.validate_excluded_components_are_zero(cur, CUTOFF)
    assert f"{ESR}.{componente}=-1.00" in str(e.value)


def test_esr_nao_altera_os_valores_de_order(banco):
    """Requisito 7: invariancia. A presenca do tipo novo nao pode mexer em
    NADA do que ORDER produz — nem valor, nem sinal, nem contagem."""
    so_order = [_linha(transaction_id="a"),
                _linha(transaction_id="b", brand="kokeshi")]
    cur = _montar(banco, so_order)
    sem = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    cur = _montar(banco, so_order + [_recuperacao(transaction_id="esr1"),
                                     _recuperacao(transaction_id="esr2")])
    com = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert sem == com


def test_esd_e_esr_convivem_sem_se_contaminar(banco):
    """Requisito 6: o desembolso mantem o comportamento anterior com o novo tipo
    presente. Os dois sao espelhos de sinal, e a soma dos dois — que e' zero
    neste cenario — nao pode vazar para lugar nenhum do fato."""
    cur = _montar(banco, [
        _linha(),
        _desembolso(transaction_id="esd1", settlement_amount=Decimal("500.00"),
                    adjustment_amount=Decimal("500.00")),
        _recuperacao(transaction_id="esr1", settlement_amount=Decimal("-500.00"),
                     adjustment_amount=Decimal("-500.00")),
    ])
    tipos = sync.validate_transaction_types(cur, None, CUTOFF)
    assert tipos[ESD] == 1 and tipos[ESR] == 1
    sync.validate_excluded_components_are_zero(cur, CUTOFF)
    assert int(sync.validate_read_population(cur, CUTOFF)["lidas"]) == 1
    agregado = sync.recompute_keys(
        cur, sync.discover_touched_keys(cur, None, CUTOFF), CUTOFF)
    assert len(agregado) == 1
    assert agregado[0]["affiliate_creator_commission"] == Decimal("-10.00")


def test_um_tipo_desconhecido_continua_falhando_com_o_esr_presente(banco):
    """Requisito 5: o guardrail NAO foi afrouxado. Reconhecer a recuperacao nao
    abre a porta para nenhum tipo seguinte."""
    cur = _montar(banco, [_linha(),
                          _recuperacao(transaction_id="esr1"),
                          _linha(transaction_id="z",
                                 transaction_type="OUTRO_TIPO_INEDITO")])
    with pytest.raises(RuntimeError, match="transaction_type DESCONHECIDO") as e:
        sync.validate_transaction_types(cur, None, CUTOFF)
    assert "OUTRO_TIPO_INEDITO=1" in str(e.value)
    desconhecidos = str(e.value).split("DESCONHECIDO na janela lida:")[1].split(".")[0]
    assert ESR not in desconhecidos and ESD not in desconhecidos


def test_tipo_nulo_continua_falhando_com_o_esr_presente(banco):
    cur = _montar(banco, [_linha(),
                          _recuperacao(transaction_id="esr1"),
                          _linha(transaction_id="z", transaction_type=None)])
    with pytest.raises(RuntimeError, match=r"<NULL>=1"):
        sync.validate_transaction_types(cur, None, CUTOFF)
