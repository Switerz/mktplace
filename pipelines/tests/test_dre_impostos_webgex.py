"""T-0069 — imposto sobre venda realizado (webgex) no publisher de insumos da DRE.

Duas partes:
  * regras PURAS (sem banco): componentes, nulo != zero, parciais, inercia
    antes da 040, recusas e o contrato com a migration 040;
  * provas de SERVIDOR contra PostgreSQL descartavel (mesmo harness de
    `test_dre_insumo_publisher_integracao.py`): a SQL de leitura nao multiplica
    o imposto pelos itens da NF, a 040 sobe/desce sem apagar dado e o
    publisher so' grava os impostos depois dela. Sem binarios do PostgreSQL,
    essa parte e' SKIP.
"""
from __future__ import annotations

import pathlib
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from pipelines.dre import insumo_publisher as P
from pipelines.tests.postgres_descartavel import MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel
from pipelines.tests.test_dre_insumo_publisher import (
    _carregar_migration,
    _lista_do_check,
    carregar_migration_025,
    carregar_migration_037,
    cmv,
    cp,
    esperado,
    linha,
)

JUL = date(2026, 7, 1)
SET = date(2026, 9, 1)
OUT = date(2026, 10, 1)
NOV = date(2026, 11, 1)


def carregar_migration_040():
    return _carregar_migration("040_dre_insumo_impostos_webgex.py")


def imp(canal="ml", marca="kokeshi", mes=JUL, receita=1_000_000, coberta=None, notas=100, com=None,
        icms="127000", difal="73000", pis_cofins="21000", ambiguas=0):
    """Uma linha de `SQL_IMPOSTOS` (competencia x marca x plataforma).

    Padrao: ML set/26 medido no relatorio (ICMS 12,7%, DIFAL 7,3%, PIS+COF
    2,1% = 22,1% da receita fiscal), cobertura total.
    """
    coberta = receita if coberta is None else coberta
    com = notas if com is None else com
    return {"competencia": mes, "marca": marca, "plataforma": P.PLATAFORMA_FISCAL[canal],
            "receita_fiscal": Decimal(receita), "receita_com_imposto": None if com == 0 else Decimal(coberta),
            "notas": notas, "notas_com_imposto": com,
            "icms": None if icms is None or com == 0 else Decimal(icms),
            "difal": None if difal is None or com == 0 else Decimal(difal),
            "pis_cofins": None if pis_cofins is None or com == 0 else Decimal(pis_cofins),
            "chaves_ambiguas": ambiguas, "atualizado_em": datetime(2026, 11, 5, 12, 6)}


def lote(cels, impostos, mes=JUL):
    """`cels` = [(canal, marca, receita)]; CMV com a mesma receita fiscal."""
    esp = [esperado(c, m, gmv=r, mes=mes) for c, m, r in cels]
    cmvs = [cmv(c, m, receita=r, mes=mes, emissao=date(mes.year, mes.month, 28)) for c, m, r in cels]
    canais = [c for c in P.CANAIS if any(c == x for x, _, _ in cels)]
    return P.montar_lote(esperado=esp, cmv=cmvs, contraprova=[cp(c) for c in cmvs], repasse=[],
                         canais=canais, comps=[mes], impostos=impostos)


def linhas_imp(lt, canal="ml", marca="kokeshi"):
    return {c: linha(lt, canal, marca, c) for c in P.IMPOSTOS}


# ---------------------------------------------------------------------------
# Componentes, sinal, cobertura
# ---------------------------------------------------------------------------

def test_tres_componentes_em_valor_absoluto_com_sinal_de_custo_e_cobertura():
    lt = lote([("ml", "kokeshi", 1_000_000)], [imp()])
    assert lt.recusas == []
    ln = linhas_imp(lt)
    assert {c: r["valor"] for c, r in ln.items()} == {
        P.IMPOSTO_ICMS: Decimal(127000), P.IMPOSTO_DIFAL: Decimal(73000), P.IMPOSTO_PIS_COFINS: Decimal(21000)}
    for r in ln.values():
        assert r["sinal_origem"] == 1 and r["status_reconciliacao"] == "nao_avaliado"
        assert r["cobertura"] == Decimal("1.000000") and r["procedencia"] == P.PROC_IMPOSTOS
        assert r["motivo"] == "imposto de 100 de 100 NFs casadas no webgex (VX/DB); receita fiscal coberta = 1.0000"
        assert P.IMPOSTO_PARCIAL not in r["motivo"]
        assert r["data_max_origem"] == datetime(2026, 11, 5, 12, 6, tzinfo=timezone.utc)
    assert lt.impostos_parciais == []


def test_faixa_realista_da_aliquota_efetiva_total():
    """Fixture realista (TikTok set/26: ICMS 13,5 + DIFAL 9,7 + PIS/COF 0,3 = 23,5%)."""
    receita = Decimal("2109360.00")
    lt = lote([("tiktok", "apice", 2_109_360)],
              [imp("tiktok", "apice", receita=receita, icms=receita * Decimal("0.135"),
                   difal=receita * Decimal("0.097"), pis_cofins=receita * Decimal("0.003"))])
    total = sum(linha(lt, "tiktok", "apice", c)["valor"] for c in P.IMPOSTOS)
    rf = linha(lt, "tiktok", "apice", "cmv_receita_fiscal")["valor"]
    assert Decimal("0.21") <= total / rf <= Decimal("0.25")
    # e o default (ML set/26) tambem
    lt_ml = lote([("ml", "kokeshi", 1_000_000)], [imp()])
    assert Decimal("0.21") <= sum(linha(lt_ml, "ml", "kokeshi", c)["valor"] for c in P.IMPOSTOS) / 1_000_000 \
        <= Decimal("0.25")


def test_cobertura_parcial_publica_so_o_medido_sem_extrapolar():
    lt = lote([("ml", "lescent", 1_000_000)],
              [imp(marca="lescent", coberta=640_000, com=64, icms="81280", difal="46720", pis_cofins="13440")])
    ln = linhas_imp(lt, marca="lescent")
    assert ln[P.IMPOSTO_ICMS]["valor"] == Decimal(81280)  # nada escalado para 100%
    assert ln[P.IMPOSTO_ICMS]["cobertura"] == Decimal("0.640000")


# ---------------------------------------------------------------------------
# Nulo != zero
# ---------------------------------------------------------------------------

def test_celula_sem_nf_casada_vira_nulo_nunca_zero():
    lt = lote([("ml", "kokeshi", 1_000_000), ("ml", "barbours", 500_000)],
              [imp(marca="kokeshi", com=0)])  # barbours: nem linha na leitura
    for marca in ("kokeshi", "barbours"):
        for r in linhas_imp(lt, marca=marca).values():
            assert r["valor"] is None and r["status_reconciliacao"] == "ausente_na_origem"
            assert r["sinal_origem"] is None
    assert "nenhuma das 100 NFs" in linha(lt, "ml", "kokeshi", P.IMPOSTO_ICMS)["motivo"]
    assert linha(lt, "ml", "barbours", P.IMPOSTO_ICMS)["motivo"] == "sem NF de venda para a chave na origem"
    assert lt.recusas == []


def test_imposto_zero_com_nf_casada_e_zero_legitimo():
    """PIS/COFINS CST 04 (aliquota zero) com NF casada: zero medido."""
    lt = lote([("tiktok", "apice", 1_000_000)], [imp("tiktok", "apice", pis_cofins="0")])
    r = linha(lt, "tiktok", "apice", P.IMPOSTO_PIS_COFINS)
    assert r["valor"] == Decimal(0) and r["status_reconciliacao"] == "nao_avaliado"


def test_componente_sem_linha_do_codigo_nas_nfs_casadas_vira_nulo():
    lt = lote([("ml", "kokeshi", 1_000_000)], [imp(pis_cofins=None)])
    r = linha(lt, "ml", "kokeshi", P.IMPOSTO_PIS_COFINS)
    assert r["valor"] is None and r["status_reconciliacao"] == "ausente_na_origem"
    assert "sem linha PIS/COF" in r["motivo"]
    assert linha(lt, "ml", "kokeshi", P.IMPOSTO_ICMS)["valor"] == Decimal(127000)


# ---------------------------------------------------------------------------
# Parciais
# ---------------------------------------------------------------------------

def test_lescent_setembro_com_cobertura_abaixo_do_minimo_fica_parcial():
    lt = lote([("tiktok", "lescent", 1_000_000)],
              [imp("tiktok", "lescent", mes=SET, coberta=650_000, com=65)], mes=SET)
    m = linha(lt, "tiktok", "lescent", P.IMPOSTO_ICMS)["motivo"]
    partes = m.split("; ")
    assert partes[-1] == "imposto parcial: cobertura 0.6500 da receita fiscal, minimo 0.98"
    assert lt.impostos_parciais == [{"competencia": "2026-09", "canal": "tiktok", "marca": "lescent",
                                     "motivos": ["cobertura 0.6500 da receita fiscal, minimo 0.98"]}]


@pytest.mark.parametrize("coberta,parcial", [(980_000, False), (979_999, True)])
def test_minimo_de_cobertura_e_inclusivo(coberta, parcial):
    lt = lote([("ml", "kokeshi", 1_000_000)], [imp(coberta=coberta)])
    assert (P.IMPOSTO_PARCIAL in linha(lt, "ml", "kokeshi", P.IMPOSTO_ICMS)["motivo"]) is parcial


def test_shopee_e_sempre_parcial_mesmo_com_cobertura_total():
    lt = lote([("shopee", "apice", 300_000)], [imp("shopee", "apice", receita=300_000)])
    for r in linhas_imp(lt, "shopee", "apice").values():
        assert r["motivo"].endswith("; imposto parcial: imposto so' sobre a receita fiscal faturada (F1 em aberto)")
        assert r["valor"] is not None  # parcial nao apaga o valor medido
    assert lt.impostos_parciais[0]["motivos"] == ["imposto so' sobre a receita fiscal faturada (F1 em aberto)"]


@pytest.mark.parametrize("mes,marca,parcial", [
    (OUT, "rituaria", True), (NOV, "rituaria", True),   # out/26 e posteriores, ate' decisao
    (SET, "rituaria", False), (OUT, "kokeshi", False),
])
def test_rituaria_a_partir_de_outubro_fica_parcial_pela_base_do_icms(mes, marca, parcial):
    lt = lote([("tiktok", marca, 1_000_000)], [imp("tiktok", marca, mes=mes)], mes=mes)
    m = linha(lt, "tiktok", marca, P.IMPOSTO_ICMS)["motivo"]
    assert (P.IMPOSTO_PARCIAL_BASE in m) is parcial


def test_parciais_se_acumulam_e_nenhum_trecho_tem_separador_interno():
    lt = lote([("shopee", "rituaria", 1_000_000)],
              [imp("shopee", "rituaria", mes=OUT, coberta=500_000, com=50)], mes=OUT)
    m = linha(lt, "shopee", "rituaria", P.IMPOSTO_ICMS)["motivo"]
    trechos = [p for p in m.split("; ") if p.startswith(P.IMPOSTO_PARCIAL)]
    assert len(trechos) == 3
    for t in (P.IMPOSTO_PARCIAL_SHOPEE, P.IMPOSTO_PARCIAL_BASE):
        assert "; " not in t and t.startswith(P.IMPOSTO_PARCIAL)


def test_parcial_nunca_muda_valor_nem_status():
    base = linhas_imp(lote([("tiktok", "kokeshi", 1_000_000)], [imp("tiktok", mes=OUT)], mes=OUT), "tiktok")
    ritu = linhas_imp(lote([("tiktok", "rituaria", 1_000_000)], [imp("tiktok", "rituaria", mes=OUT)], mes=OUT),
                      "tiktok", "rituaria")
    assert P.IMPOSTO_PARCIAL_BASE in ritu[P.IMPOSTO_ICMS]["motivo"]
    assert base[P.IMPOSTO_ICMS]["valor"] == Decimal(127000)  # compara valor medido, nao nulo com nulo
    for c in P.IMPOSTOS:
        assert (base[c]["valor"], base[c]["status_reconciliacao"], base[c]["cobertura"]) == \
            (ritu[c]["valor"], ritu[c]["status_reconciliacao"], ritu[c]["cobertura"])


# ---------------------------------------------------------------------------
# Validacao dos impostos: reprova SO' os impostos, nunca o lote
# ---------------------------------------------------------------------------

def _sem_impostos_e_lote_intacto(lt, cels, trecho):
    """Lote publicado sem NENHUMA linha de imposto (fail-closed), resto igual ao inerte."""
    assert lt.recusas == []
    assert not any(r["componente"] in P.IMPOSTOS for r in lt.linhas)
    assert lt.linhas == lote(cels, None).linhas
    assert lt.impostos_estado.startswith("recusado: ") and trecho in lt.impostos_estado
    assert any(trecho in m for m in lt.impostos_recusas)


ML = [("ml", "kokeshi", 1_000_000)]


def test_chave_de_nf_em_mais_de_uma_celula_reprova_so_os_impostos():
    _sem_impostos_e_lote_intacto(lote(ML, [imp(ambiguas=2)]), ML, "cai em mais de uma celula")


def test_receita_fiscal_diferente_da_do_cmv_reprova_so_os_impostos():
    """O caso da recarga da gold entre o SQL_CMV e o SQL_IMPOSTOS."""
    _sem_impostos_e_lote_intacto(lote(ML, [imp(receita=1_000_002, coberta=1_000_002)]), ML, "diverge da do CMV")
    _sem_impostos_e_lote_intacto(lote(ML, [imp(marca="apice")]), ML, "sem CMV")  # celula sem CMV


def test_reprovacao_numa_celula_derruba_os_impostos_de_todas():
    """Nada de publicar imposto parcial de uma leitura inconsistente."""
    cels = ML + [("ml", "apice", 500_000)]
    lt = lote(cels, [imp(), imp(marca="apice", receita=500_000, difal="-1")])
    _sem_impostos_e_lote_intacto(lt, cels, "imposto_difal negativo")


def test_imposto_negativo_reprova_so_os_impostos():
    _sem_impostos_e_lote_intacto(lote(ML, [imp(difal="-1")]), ML, "imposto_difal negativo")


def test_cobertura_acima_de_um_reprova_so_os_impostos():
    _sem_impostos_e_lote_intacto(lote(ML, [imp(coberta=1_000_100)]), ML, "cobertura fora de [0,1]")


def test_chave_repetida_na_leitura_reprova_so_os_impostos():
    _sem_impostos_e_lote_intacto(lote(ML, [imp(), imp()]), ML, "impostos: chave repetida")


def test_estado_da_reprovacao_lista_no_maximo_cinco_motivos():
    cels = [("ml", f"m{i}", 1_000_000) for i in range(7)]
    lt = lote(cels, [imp(marca=f"m{i}", difal="-1") for i in range(7)])
    assert lt.recusas == [] and len(lt.impostos_recusas) == 7
    assert lt.impostos_estado.endswith("(e mais 2)")


def test_validacao_ok_marca_publicado():
    lt = lote(ML, [imp()])
    assert lt.impostos_estado == "publicado" and lt.impostos_recusas == []
    assert P._resumo_impostos(lt, "lido")["estado"] == "publicado"


def test_celula_fora_do_esperado_nao_e_publicada():
    lt = lote([("ml", "kokeshi", 1_000_000)], [imp(), imp(marca="denavita")])
    assert ("impostos", JUL, "ml", "denavita") in lt.fora_do_escopo
    assert not any(r["marca"] == "denavita" for r in lt.linhas)


# ---------------------------------------------------------------------------
# Inerte antes da 040
# ---------------------------------------------------------------------------

def test_inerte_sem_a_040_o_lote_e_identico_ao_de_antes():
    cels = [("ml", "kokeshi", 1_000_000), ("shopee", "apice", 300_000)]
    antes = lote(cels, None)
    assert not any(r["componente"] in P.IMPOSTOS for r in antes.linhas)
    assert antes.recusas == [] and antes.observacoes == [] and antes.impostos_parciais == []
    depois = lote(cels, [imp(), imp("shopee", "apice", receita=300_000)])
    assert [r for r in depois.linhas if r["componente"] not in P.IMPOSTOS] == antes.linhas
    assert "migration 040 pendente" in P.estado_impostos(None)
    assert P.estado_impostos([]) == "publicado"


def test_lista_vazia_publica_nulo_para_toda_celula_esperada():
    lt = lote([("ml", "kokeshi", 1_000_000)], [])
    assert all(r["valor"] is None for r in linhas_imp(lt).values())


class _Conn:
    def __init__(self, definicao):
        self.definicao = definicao

    def execute(self, _sql):
        return self

    def scalar(self):
        return self.definicao


def _definicao(componentes) -> str:
    return "CHECK ((componente = ANY (ARRAY[" + ", ".join(f"'{c}'::text" for c in componentes) + "])))"


def test_so_le_os_impostos_quando_o_check_aceita_os_tres():
    m037, m040 = carregar_migration_037(), carregar_migration_040()
    assert P.check_aceita_impostos(_Conn(_definicao(m037.COMPONENTES))) is False
    assert P.check_aceita_impostos(_Conn(_definicao(m037.COMPONENTES + ("imposto_icms",)))) is False
    assert P.check_aceita_impostos(_Conn(None)) is False
    assert P.check_aceita_impostos(_Conn(_definicao(m040.COMPONENTES))) is True
    # a taxa Shopee continua aceita depois da 040
    assert P.check_aceita_taxa_shopee(_Conn(_definicao(m040.COMPONENTES))) is True


class _Resultado:
    def __init__(self, linhas):
        self.linhas = linhas

    def mappings(self):
        return self

    def all(self):
        return self.linhas


class _ConnFonte:
    def __init__(self, lidas):
        self.lidas = lidas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.lidas.append(sql)
        return _Resultado([])

    def rollback(self):
        pass


class _EngFonte:
    def __init__(self):
        self.lidas = []

    def connect(self):
        return _ConnFonte(self.lidas)


@pytest.mark.parametrize("aceita", [False, True])
def test_leitura_dos_impostos_so_acontece_com_o_check(aceita):
    eng = _EngFonte()
    impostos, estado = P.ler_impostos(eng, [JUL], ["ml"], ["kokeshi"], aceita)
    assert any(s is P.SQL_IMPOSTOS for s in eng.lidas) is aceita
    assert (impostos is None) is (not aceita)
    assert estado == ("lido" if aceita else P.estado_impostos(None))
    if aceita:
        assert [str(x) for x in eng.lidas[:2]] == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '600s'"]
    # a leitura principal nao toca mais nos impostos
    eng2 = _EngFonte()
    P.ler_fonte(eng2, [JUL], ["ml"], ["kokeshi"], False)
    assert not any(s is P.SQL_IMPOSTOS for s in eng2.lidas)


class _EngTimeout:
    """`SQL_IMPOSTOS` estoura o statement_timeout; o resto da fonte responde."""

    def __init__(self):
        self.lidas = []

    def connect(self):
        eng = self

        class C(_ConnFonte):
            def execute(self, sql, params=None):
                if sql is P.SQL_IMPOSTOS:
                    from sqlalchemy.exc import OperationalError
                    raise OperationalError("SELECT", {}, Exception(
                        "canceling statement due to statement timeout postgresql://u:SEGREDO@10.0.0.1/dm"))
                return super().execute(sql, params)
        return C(eng.lidas)


def test_erro_na_leitura_dos_impostos_vira_indisponivel_sem_dsn():
    impostos, estado = P.ler_impostos(_EngTimeout(), [JUL], ["ml"], ["kokeshi"], True)
    assert impostos is None and estado == "indisponivel: OperationalError"
    assert "SEGREDO" not in estado and "10.0.0.1" not in estado


def test_leitura_dos_impostos_so_engole_erro_de_sql():
    fonte = (pathlib.Path(P.__file__)).read_text(encoding="utf-8")
    trecho = fonte[fonte.index("def ler_impostos"):fonte.index("def ler_numeracao_nf")]
    assert "except SQLAlchemyError" in trecho and "except Exception" not in trecho
    assert trecho.index("SET TRANSACTION READ ONLY") < trecho.index("statement_timeout = '600s'") \
        < trecho.index("SQL_IMPOSTOS")


# ---------------------------------------------------------------------------
# SQL de leitura
# ---------------------------------------------------------------------------

def _sql(t) -> str:
    return " ".join(str(t).split())


def test_sql_le_as_duas_tabelas_so_venda_e_difal_separado():
    sql = _sql(P.SQL_IMPOSTOS)
    assert "FROM raw.webgex_impostos_gobeaute" in sql and "FROM raw.webgex_impostos_ap" in sql
    assert "UNION ALL" in sql
    assert sql.count("WHERE circuito = 'VX' AND debito_credito = 'DB' AND data >= :inicio_webgex "
                     "AND data < :fim_webgex") == 2
    assert "SUM(valor_imposto) FILTER (WHERE codigo_imposto = 'ICM') AS icms" in sql
    assert "SUM(valor_difal) FILTER (WHERE codigo_imposto = 'ICM') AS difal" in sql
    assert "SUM(valor_imposto) FILTER (WHERE codigo_imposto IN ('PIS', 'COF')) AS pis_cofins" in sql
    assert "COALESCE" not in sql.upper()  # ausencia != zero
    assert "{{" not in sql and "[[" not in sql


@pytest.mark.parametrize("comps,janela", [
    ([JUL], (date(2026, 6, 24), date(2026, 8, 8))),
    ([date(2026, 6, 1), JUL, date(2026, 8, 1), SET, OUT], (date(2026, 5, 25), date(2026, 11, 8))),
    ([date(2026, 12, 1)], (date(2026, 11, 24), date(2027, 1, 8))),
])
def test_janela_do_webgex_tem_sete_dias_de_folga(comps, janela):
    assert P.janela_webgex(comps) == janela


class _EngParams(_EngFonte):
    def connect(self):
        eng = self

        class C(_ConnFonte):
            def execute(self, sql, params=None):
                if sql is P.SQL_IMPOSTOS:
                    eng.params = params
                return super().execute(sql, params)
        return C(self.lidas)


def test_leitura_passa_a_janela_do_webgex_e_a_competencia_segue_da_gold():
    eng = _EngParams()
    P.ler_impostos(eng, [SET, OUT], ["ml"], ["kokeshi"], True)
    assert (eng.params["inicio_webgex"], eng.params["fim_webgex"]) == (date(2026, 8, 25), date(2026, 11, 8))
    assert eng.params["competencias"] == [SET, OUT]  # a celula continua sendo a `data_mes` da gold


def test_sql_usa_os_mesmos_filtros_de_celula_do_cmv():
    sql = _sql(P.SQL_IMPOSTOS)
    notas = sql[sql.index("notas AS"):sql.index("webgex AS")]
    cmv_sql = _sql(P.SQL_CMV)
    filtros = cmv_sql[cmv_sql.index("WHERE data_mes"):cmv_sql.index("GROUP BY")]
    assert filtros in notas
    assert "SUM(receita_produto) AS receita_fiscal" in notas


def test_sql_colapsa_a_chave_antes_do_join():
    """A gold tem uma linha por ITEM: o JOIN e' com a chave ja' agrupada."""
    sql = _sql(P.SQL_IMPOSTOS)
    notas = sql[sql.index("notas AS"):sql.index("webgex AS")]
    assert "GROUP BY chave_nfe, data_mes, marca, plataforma" in notas
    assert "COUNT(*) OVER (PARTITION BY chave_nfe) AS celulas_da_chave" in notas
    impostos = sql[sql.index("impostos AS"):sql.index("SELECT n.data_mes")]
    assert "GROUP BY chave_nota" in impostos
    assert "LEFT JOIN impostos i ON i.chave_nota = n.chave_nfe" in sql
    assert sql.endswith("GROUP BY n.data_mes, n.marca, n.plataforma")


# ---------------------------------------------------------------------------
# Contrato com a migration 040
# ---------------------------------------------------------------------------

def test_040_estende_a_037_com_os_tres_impostos():
    m025, m037, m040 = carregar_migration_025(), carregar_migration_037(), carregar_migration_040()
    assert (m040.revision, m040.down_revision) == ("040", "039")
    assert m040.COMPONENTES == m037.COMPONENTES + P.IMPOSTOS
    assert m040.NOVOS == P.IMPOSTOS
    (up,) = m040.UPGRADE_SQL
    guarda, restaura = m040.DOWNGRADE_SQL
    assert _lista_do_check(up, "ck_fdim_componente") == m040.COMPONENTES
    assert _lista_do_check(restaura, "ck_fdim_componente") == m037.COMPONENTES
    for sql in (up, restaura):
        assert sql.count("CONSTRAINT") == 2 and "DROP CONSTRAINT ck_fdim_componente," in sql
        assert m040.TABELA in sql
    # O downgrade NAO apaga: recusa se houver imposto publicado.
    assert "DELETE" not in " ".join(m040.DOWNGRADE_SQL).upper()
    assert "RAISE EXCEPTION" in guarda and all(f"'{c}'" in guarda for c in P.IMPOSTOS)
    assert "%" not in " ".join(m040.UPGRADE_SQL + m040.DOWNGRADE_SQL)
    # status: nenhum novo; o `parcial` viaja no motivo
    lt = lote([("shopee", "apice", 300_000)], [imp("shopee", "apice", receita=300_000)])
    assert {r["status_reconciliacao"] for r in lt.linhas} <= set(m025.STATUS)
    assert {r["componente"] for r in lt.linhas} <= set(m040.COMPONENTES)


# ===========================================================================
# Servidor real (PostgreSQL descartavel)
# ===========================================================================

pg = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

AGORA = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)

DDL_FONTE_EXTRA = [
    "CREATE SCHEMA raw",
    """CREATE TABLE raw.webgex_impostos_gobeaute (
        chave_nota TEXT, razao_social TEXT, circuito TEXT, debito_credito TEXT, codigo_imposto TEXT, cst TEXT,
        aliquota NUMERIC, base_calculo NUMERIC, valor_imposto NUMERIC, valor_difal NUMERIC, data DATE,
        ingested_at TIMESTAMPTZ)""",
    "CREATE TABLE raw.webgex_impostos_ap (LIKE raw.webgex_impostos_gobeaute)",
    # A 037 liga a leitura da taxa Shopee: as tabelas existem (vazias).
    """CREATE TABLE silver.stg_shopee_orders (order_sn TEXT, brand TEXT, created_date_brt DATE, order_status TEXT,
        update_time TIMESTAMP)""",
    """CREATE TABLE silver.fct_gobeaute_despesas_shopee (pedido TEXT, canal_origem TEXT,
        comissao_plataforma_fee NUMERIC, taxa_servico_fee NUMERIC, taxa_transacao_fee NUMERIC, data_carga TIMESTAMP)""",
    "ALTER TABLE gold.nf_vendas_unificada_v2 ADD COLUMN chave_nfe TEXT",
]


def _executar(eng, stmts):
    with eng.begin() as conn:
        for s in stmts:
            conn.execute(text(s))


def _semear_impostos(fonte):
    """Uma chave por (marca, plataforma, canal, emissao): cada NF tem DOIS itens
    (SKU A e B), e o webgex tem uma linha por imposto x ITEM -- o caso que
    multiplicaria o imposto sem o agrupamento por chave.

      * kokeshi ML (2 NFs, _gobeaute): ICMS 12%, DIFAL 7%, PIS 0,65%, COF 3%;
      * kokeshi TikTok: so' a NF de 15/07 casada (cobertura 0,5);
      * barbours TikTok (_ap): ICMS 18%, DIFAL 0, PIS/COF CST 04 = zero;
      * kokeshi Shopee: nenhuma NF casada (nulo, nunca zero).
    Ruido: OX, devolucao (DV/CR), IPI, e imposto de NF intercompany.
    """
    with fonte.begin() as c:
        c.execute(text("UPDATE gold.nf_vendas_unificada_v2 SET chave_nfe = "
                       "md5(marca || coalesce(plataforma, '') || canal || data_emissao::text)"))
        itens = c.execute(text("""SELECT chave_nfe, marca, plataforma, canal, intercompany, receita_produto, data_emissao
                                   FROM gold.nf_vendas_unificada_v2""")).mappings().all()
        ing = datetime(2026, 11, 5, 12, 6)

        def ins(tabela, chave, circ, dc, cod, valor, difal=0, dia=date(2026, 7, 15)):
            c.execute(text(f"""INSERT INTO {tabela} (chave_nota, circuito, debito_credito, codigo_imposto,
                valor_imposto, valor_difal, data, ingested_at) VALUES (:k, :ci, :dc, :cod, :v, :d, :dia, :i)"""),
                      {"k": chave, "ci": circ, "dc": dc, "cod": cod, "v": valor, "d": difal, "dia": dia, "i": ing})

        for it in itens:
            k, rec, marca, plat = it["chave_nfe"], it["receita_produto"], it["marca"], it["plataforma"]
            if it["canal"] != "marketplace" or rec == 7777777 and not it["intercompany"]:
                continue
            if it["intercompany"]:  # imposto da NF intercompany: a NF nao e' da celula
                ins("raw.webgex_impostos_gobeaute", k, "VX", "DB", "ICM", 999999)
                continue
            if marca == "kokeshi" and plat == "mercadolivre":
                t = "raw.webgex_impostos_gobeaute"
                ins(t, k, "VX", "DB", "ICM", rec * Decimal("0.12"), rec * Decimal("0.07"))
                ins(t, k, "VX", "DB", "PIS", rec * Decimal("0.0065"))
                ins(t, k, "VX", "DB", "COF", rec * Decimal("0.03"))
                ins(t, k, "VX", "DB", "IPI", 0)
                ins(t, k, "OX", "DB", "ICM", 55555)          # outra saida
                ins(t, k, "DV", "CR", "ICM", 44444, 3333)    # devolucao (credito)
                # VX/DB da MESMA chave, mas com `data` fora da janela de julho
                # (fim + 7 dias = 08/08; inicio - 7 = 24/06): o filtro ignora.
                ins(t, k, "VX", "DB", "ICM", 888888, 777, dia=date(2026, 8, 8))
                ins(t, k, "VX", "DB", "ICM", 666666, 555, dia=date(2026, 6, 23))
                # Na folga (lancamento fiscal fora do mes da emissao): entra, com valor zero.
                ins(t, k, "VX", "DB", "ICM", 0, 0, dia=date(2026, 8, 7))
            elif marca == "kokeshi" and plat == "tiktokshop" and it["data_emissao"].day == 15:
                ins("raw.webgex_impostos_gobeaute", k, "VX", "DB", "ICM", rec * Decimal("0.12"), rec * Decimal("0.07"))
                ins("raw.webgex_impostos_gobeaute", k, "VX", "DB", "PIS", 0)
                ins("raw.webgex_impostos_gobeaute", k, "VX", "DB", "COF", 0)
            elif marca == "barbours" and plat == "tiktokshop":
                ins("raw.webgex_impostos_ap", k, "VX", "DB", "ICM", rec * Decimal("0.18"), 0)
                ins("raw.webgex_impostos_ap", k, "VX", "DB", "PIS", 0)
                ins("raw.webgex_impostos_ap", k, "VX", "DB", "COF", 0)


@pytest.fixture
def bancos():
    from pipelines.tests import test_dre_insumo_publisher_integracao as I

    with cluster_da_sessao() as base:
        adm = create_engine(base, future=True, isolation_level="AUTOCOMMIT")
        with adm.connect() as conn:
            for nome in ("imp_alvo", "imp_fonte"):
                conn.execute(text(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)"))
                conn.execute(text(f"CREATE DATABASE {nome}"))
        adm.dispose()
        alvo = create_engine(I._url_db(base, "imp_alvo"), future=True)
        fonte = create_engine(I._url_db(base, "imp_fonte"), future=True)
        # Estado da 039: 025 + 037 (a 038 e a 039 nao tocam esta tabela).
        _executar(alvo, I.DDL_ALVO + carregar_migration_025().UPGRADE_SQL + carregar_migration_037().UPGRADE_SQL)
        _executar(fonte, I.DDL_FONTE + DDL_FONTE_EXTRA)
        I.semear(alvo, fonte)
        _semear_impostos(fonte)
        yield alvo, fonte
        alvo.dispose()
        fonte.dispose()


def _upgrade_040(alvo):
    _executar(alvo, carregar_migration_040().UPGRADE_SQL)


def _impostos_publicados(alvo) -> dict:
    with alvo.connect() as c:
        rows = c.execute(text("SELECT canal, marca, componente, valor, cobertura, status_reconciliacao, motivo "
                              "FROM marts.fact_dre_insumo_mensal WHERE componente LIKE 'imposto_%'")).mappings().all()
    return {(r["canal"], r["marca"], r["componente"]): dict(r) for r in rows}


def _publicar(alvo, fonte):
    return P.publicar(alvo, fonte, [JUL], ["tiktok", "ml", "shopee"], agora=AGORA)


@pg
def test_sql_real_nao_multiplica_pelos_itens_e_ignora_ruido(bancos):
    _, fonte = bancos
    with fonte.connect() as conn:
        ini, fim = P.janela_webgex([JUL])
        rows = conn.execute(P.SQL_IMPOSTOS, {"competencias": [JUL], "plataformas": list(P.PLATAFORMA_FISCAL.values()),
                                             "marcas": ["kokeshi", "barbours"], "inicio_webgex": ini,
                                             "fim_webgex": fim}).mappings().all()
        conn.rollback()
    por = {(r["marca"], r["plataforma"]): r for r in rows}
    ml = por[("kokeshi", "mercadolivre")]
    assert ml["notas"] == 2 and ml["notas_com_imposto"] == 2  # 2 NFs (4 itens), nao 4
    # as linhas VX/DB de 23/06 e 08/08 (fora da janela) nao entraram: senao ICMS teria +1.555.554
    assert (ml["icms"], ml["difal"], ml["pis_cofins"]) == (Decimal("120000.00"), Decimal("70000.00"),
                                                            Decimal("36500.0000"))
    assert ml["receita_fiscal"] == ml["receita_com_imposto"] == 1_000_000
    assert ml["chaves_ambiguas"] == 0
    tk = por[("kokeshi", "tiktokshop")]
    assert tk["receita_com_imposto"] == 495_000 and tk["receita_fiscal"] == 990_000
    assert por[("barbours", "tiktokshop")]["icms"] == Decimal("90000.00")  # da tabela _ap
    sh = por[("kokeshi", "shopee")]
    assert sh["notas_com_imposto"] == 0 and sh["icms"] is None and sh["receita_com_imposto"] is None


def _cmv_publicado(alvo) -> dict:
    with alvo.connect() as c:
        rows = c.execute(text("SELECT canal, marca, valor FROM marts.fact_dre_insumo_mensal "
                              "WHERE componente = 'cmv'")).mappings().all()
    return {(r["canal"], r["marca"]): r["valor"] for r in rows}


@pg
def test_chave_em_duas_celulas_publica_o_lote_sem_impostos(bancos):
    alvo, fonte = bancos
    _upgrade_040(alvo)
    with fonte.begin() as c:  # um item da Shopee carrega a chave de uma NF do ML: a chave cai em duas celulas
        c.execute(text("""UPDATE gold.nf_vendas_unificada_v2
                          SET chave_nfe = (SELECT chave_nfe FROM gold.nf_vendas_unificada_v2
                                           WHERE plataforma = 'mercadolivre' AND marca = 'kokeshi'
                                             AND canal = 'marketplace' AND intercompany IS NOT TRUE LIMIT 1)
                          WHERE ctid = (SELECT ctid FROM gold.nf_vendas_unificada_v2
                                        WHERE plataforma = 'shopee' AND marca = 'kokeshi' LIMIT 1)"""))
    res = _publicar(alvo, fonte)
    assert res["recusas"] == []
    assert res["impostos"]["estado"].startswith("recusado: ")
    assert "cai em mais de uma celula" in res["impostos"]["estado"]
    assert _impostos_publicados(alvo) == {}  # fail-closed: nenhum imposto, nem das celulas boas
    assert _cmv_publicado(alvo)[("ml", "kokeshi")] == Decimal("300000.00")  # o resto do lote saiu


@pg
def test_erro_na_leitura_dos_impostos_publica_o_lote_com_cmv_e_sem_impostos(bancos):
    alvo, fonte = bancos
    _upgrade_040(alvo)
    with fonte.begin() as c:  # a SQL de impostos passa a falhar (tabela some da fonte)
        c.execute(text("DROP TABLE raw.webgex_impostos_ap"))
    res = _publicar(alvo, fonte)
    assert res["recusas"] == [] and res["impostos"]["estado"] == "indisponivel: ProgrammingError"
    assert _impostos_publicados(alvo) == {}
    assert _cmv_publicado(alvo)[("ml", "kokeshi")] == Decimal("300000.00")
    with alvo.connect() as c:
        assert c.execute(text("SELECT status FROM audit.source_sync_run")).scalars().all() == ["success"]


@pg
def test_antes_da_040_publicacao_e_inerte_e_nao_falha(bancos):
    alvo, fonte = bancos
    res = _publicar(alvo, fonte)
    assert res["recusas"] == [] and "migration 040 pendente" in res["impostos"]["estado"]
    assert _impostos_publicados(alvo) == {}
    # e o CHECK de fato recusaria a linha
    with pytest.raises(Exception) as exc:
        with alvo.begin() as c:
            c.execute(text("""INSERT INTO marts.fact_dre_insumo_mensal (competencia, canal, marca, componente,
                valor, status_reconciliacao, publicado_em, lote_id, procedencia)
                VALUES ('2026-07-01', 'ml', 'kokeshi', 'imposto_icms', 1, 'nao_avaliado', now(), :l, 't')"""),
                      {"l": res["lote_id"]})
    assert "ck_fdim_componente" in str(exc.value)


@pg
def test_depois_da_040_publica_os_impostos(bancos):
    alvo, fonte = bancos
    _upgrade_040(alvo)
    res = _publicar(alvo, fonte)
    assert res["recusas"] == [] and res["impostos"]["estado"] == "publicado"
    ln = _impostos_publicados(alvo)
    assert ln[("ml", "kokeshi", "imposto_icms")]["valor"] == Decimal("120000.00")
    assert ln[("ml", "kokeshi", "imposto_difal")]["valor"] == Decimal("70000.00")
    assert ln[("ml", "kokeshi", "imposto_pis_cofins")]["valor"] == Decimal("36500.00")
    assert ln[("ml", "kokeshi", "imposto_icms")]["cobertura"] == Decimal("1.000000")
    total = sum(ln[("ml", "kokeshi", c)]["valor"] for c in P.IMPOSTOS)
    assert Decimal("0.21") <= total / Decimal(1_000_000) <= Decimal("0.25")
    # TikTok kokeshi: metade casada -> parcial por cobertura, valor so' do medido
    tk = ln[("tiktok", "kokeshi", "imposto_icms")]
    assert tk["valor"] == Decimal("59400.00") and tk["cobertura"] == Decimal("0.500000")
    assert "imposto parcial: cobertura 0.5000" in tk["motivo"]
    # barbours (tabela _ap): PIS/COF zero legitimo, DIFAL zero medido
    assert ln[("tiktok", "barbours", "imposto_pis_cofins")]["valor"] == Decimal("0.00")
    assert ln[("tiktok", "barbours", "imposto_difal")]["valor"] == Decimal("0.00")
    # Shopee sem NF casada: nulo, nunca zero
    for c in P.IMPOSTOS:
        r = ln[("shopee", "kokeshi", c)]
        assert r["valor"] is None and r["status_reconciliacao"] == "ausente_na_origem"
    # idempotente
    _publicar(alvo, fonte)
    assert {k: (v["valor"], v["motivo"]) for k, v in _impostos_publicados(alvo).items()} == \
        {k: (v["valor"], v["motivo"]) for k, v in ln.items()}


@pg
def test_040_sobe_desce_sem_apagar_dado_e_sobe_de_novo(bancos):
    alvo, fonte = bancos
    m037, m040 = carregar_migration_037(), carregar_migration_040()

    def definicao():
        with alvo.connect() as c:
            return c.execute(P.SQL_CHECK_COMPONENTE).scalar()

    assert "'imposto_icms'" not in definicao()
    _upgrade_040(alvo)
    with alvo.connect() as c:
        assert P.check_aceita_impostos(c) and P.check_aceita_taxa_shopee(c)
    _publicar(alvo, fonte)
    n_antes = len(_impostos_publicados(alvo))
    assert n_antes > 0

    # Com imposto publicado, o downgrade RECUSA e nada e' apagado nem trocado.
    with pytest.raises(Exception) as exc:
        _executar(alvo, m040.DOWNGRADE_SQL)
    assert "downgrade 040 recusado" in str(exc.value) and "Nada foi apagado" in str(exc.value)
    assert len(_impostos_publicados(alvo)) == n_antes
    assert "'imposto_icms'" in definicao()

    # Sem imposto, desce para o CHECK da 037 (taxa Shopee continua aceita).
    with alvo.begin() as c:
        c.execute(text("DELETE FROM marts.fact_dre_insumo_mensal WHERE componente LIKE 'imposto_%'"))
    _executar(alvo, m040.DOWNGRADE_SQL)
    d = definicao()
    assert "'imposto_icms'" not in d and "'shopee_taxa_escrow'" in d
    assert _lista_do_check(m040.DOWNGRADE_SQL[1], "ck_fdim_componente") == m037.COMPONENTES
    # e o publisher volta a ser inerte
    res = _publicar(alvo, fonte)
    assert "migration 040 pendente" in res["impostos"]["estado"] and _impostos_publicados(alvo) == {}

    _upgrade_040(alvo)
    assert "'imposto_pis_cofins'" in definicao()
