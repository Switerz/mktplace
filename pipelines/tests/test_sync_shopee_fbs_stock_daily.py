"""Gate FULL-SOURCE-1 Fase 2 — contrato de marts.fact_shopee_fbs_stock_daily.

Testes de SEMANTICA, CARDINALIDADE e PROIBICOES. Nenhum teste abre conexao,
rede ou subprocesso; nenhum payload contem dado de comprador.
"""
from __future__ import annotations

import ast
import io
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import sync_shopee_fbs_stock_daily as mod

MIGRATION = (Path(mod.__file__).resolve().parents[1]
             / "apps" / "api" / "alembic" / "versions"
             / "021_create_fact_shopee_fbs_stock_daily.py")


def _codigo_sem_texto(fonte: str) -> str:
    """Remove docstrings e comentarios: um termo proibido citado em texto
    explicativo nao pode reprovar o teste."""
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)) and ast.get_docstring(no):
            no.body = no.body[1:]
    sem_doc = ast.unparse(arvore)
    return "\n".join(l for l in sem_doc.splitlines()
                     if not l.strip().startswith("#"))


def _loc(item="1", location="CD1", stock=10, saleable=True, kit=False,
         conta="barbours"):
    return mod.LocationRow(
        ref_date=date(2026, 9, 24), brand=conta, shop_account=conta,
        item_id=item, model_id="0", location_id=location, full_stock=stock,
        is_saleable=saleable, is_kit=kit, item_status="normal")


def _prod(item="1", saleable=10, total=10, units=28, kit=False,
          conta="barbours", locs=1, legado_extra=0):
    media = Decimal(units) / Decimal(28) if units else Decimal(0)
    cob = (Decimal(saleable) / media) if units else None
    return mod.ProdutoRow(
        ref_date=date(2026, 9, 24), brand=conta, shop_account=conta,
        item_id=item, model_id="0", item_name="Produto", item_sku="SKU",
        item_status="normal", is_kit=kit, full_stock_saleable=saleable,
        full_stock_total=total, location_count=locs, seller_stock_total=99,
        reserved_stock=5, summary_available_stock=123, units_sold_28d=units,
        days_with_sales_28d=1 if units else 0,
        units_sold_28d_legado_com_unpaid=units + legado_extra,
        avg_daily_units_28d=media,
        cobertura_torre_dias=cob,
        classificacao_torre=mod.classificar(kit, saleable, units, cob),
        vinculo_vendas="COM_VENDA" if units else "SEM_VENDA_NA_JANELA")


def _snap(locs, prods):
    return mod.Snapshot(ref_date=date(2026, 9, 24), locations=locs,
                        produtos=prods,
                        captured_at=datetime(2026, 9, 24, 9, tzinfo=timezone.utc),
                        avisos=[])


# --------------------------------------------------------------------- #
# A. A FONTE DO ESTOQUE FULL                                             #
# --------------------------------------------------------------------- #
def test_1_estoque_full_vem_de_shopee_stock():
    sql = str(mod.SQL_LOCATIONS)
    assert "shopee_stock" in sql


def test_2_summary_nunca_alimenta_estoque_full():
    """summary_available_stock existe como CONTEXTO, nunca como full_stock."""
    sql = str(mod.SQL_PRODUTOS)
    assert "total_available_stock" in sql, "o contexto deve ser publicado"
    # a coluna de estoque Full sai de shopee_stock, nao do summary
    trecho = sql[sql.index("AS full_saleable") - 400:sql.index("AS full_saleable")]
    assert "shopee_stock" in trecho
    assert "summary_info" not in trecho


def test_3_seller_stock_nao_alimenta_estoque_full():
    sql = str(mod.SQL_PRODUTOS)
    trecho = sql[sql.index("AS full_total") - 300:sql.index("AS full_total")]
    assert "seller_stock" not in trecho


def test_4_apenas_produtos_fbs_entram():
    for sql in (str(mod.SQL_LOCATIONS), str(mod.SQL_PRODUTOS)):
        assert "is_fulfillment_by_shopee" in sql


def test_5_contas_restritas_a_allowlist():
    assert mod.CONTAS_ESPERADAS == ("apice", "barbours", "lescent", "rituaria")
    assert "kokeshi" not in str(mod.SQL_PRODUTOS).lower()


# --------------------------------------------------------------------- #
# B. CLASSIFICACAO DA TORRE                                              #
# --------------------------------------------------------------------- #
def test_6_ruptura_exige_demanda():
    """Estoque zero SEM demanda nao e' ruptura."""
    assert mod.classificar(False, 0, 10, Decimal(0)) == "RUPTURA_CANDIDATA"
    assert mod.classificar(False, 0, 0, None) == "SEM_DEMANDA_MEDIDA"


def test_7_sem_giro_exige_estoque():
    assert mod.classificar(False, 50, 0, None) == "SEM_GIRO_CANDIDATO"


def test_8_baixo_usa_cobertura_e_nao_unidades():
    assert mod.classificar(False, 1, 28, Decimal(1)) == "BAIXO_CANDIDATO"
    # mesmas 1 unidade, mas demanda baixissima -> cobertura alta
    assert mod.classificar(False, 1, 28, Decimal(200)) == "EXCESSO_CANDIDATO"


def test_9_excesso_usa_o_limite_provisorio():
    lim = mod.COBERTURA_EXCESSO_DIAS_PROVISORIO
    assert mod.classificar(False, 5, 28, Decimal(lim)) == "EXCESSO_CANDIDATO"
    assert mod.classificar(False, 5, 28, Decimal(lim - 1)) == "SUFICIENTE"


def test_10_kit_nunca_recebe_classificacao_operacional():
    for saleable, units, cob in ((0, 10, Decimal(0)), (50, 0, None),
                                 (5, 28, Decimal(1)), (5, 28, Decimal(999))):
        assert mod.classificar(True, saleable, units, cob) == "KIT_NAO_CONCILIADO"


def test_11_dominio_da_classificacao_e_fechado():
    ddl = MIGRATION.read_text(encoding="utf-8")
    for c in ("RUPTURA_CANDIDATA", "BAIXO_CANDIDATO", "EXCESSO_CANDIDATO",
              "SEM_GIRO_CANDIDATO", "SUFICIENTE", "SEM_DEMANDA_MEDIDA",
              "KIT_NAO_CONCILIADO"):
        assert c in ddl


def test_12_parametros_reutilizados_do_tiktok():
    assert mod.JANELA_VENDAS_DIAS == 28
    assert mod.COBERTURA_BAIXA_DIAS_PROVISORIO == 7


def test_13_os_DOIS_limites_sao_declarados_provisorios():
    """Auditoria: nem so' o de excesso -- o de baixo tambem nao foi ratificado."""
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "COBERTURA_BAIXA_DIAS_PROVISORIO" in fonte
    assert "COBERTURA_EXCESSO_DIAS_PROVISORIO" in fonte
    assert set(mod.LIMITES_PROVISORIOS) == {
        "cobertura_baixa_dias_PROVISORIO", "cobertura_excesso_dias_PROVISORIO"}
    for nome in ("COBERTURA_BAIXA_DIAS_PROVISORIO", "COBERTURA_EXCESSO_DIAS_PROVISORIO"):
        i = fonte.index(nome)
        assert "PROVISORIO" in fonte[max(0, i - 800):i].upper()


# --------------------------------------------------------------------- #
# C. COBERTURA DA TORRE                                                  #
# --------------------------------------------------------------------- #
def test_14_cobertura_e_nula_sem_demanda():
    p = _prod(units=0, saleable=100)
    assert p.cobertura_torre_dias is None
    assert p.avg_daily_units_28d == 0


def test_15_cobertura_e_estoque_sobre_media_diaria():
    p = _prod(saleable=56, units=28)      # media 1/dia
    assert p.cobertura_torre_dias == Decimal(56)


def test_16_nome_nao_promete_formula_da_shopee():
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "cobertura_torre_dias" in ddl
    assert "NAO reproduz" in ddl or "calculo NOSSO" in ddl


# --------------------------------------------------------------------- #
# D. CARDINALIDADE E CONTRATO                                            #
# --------------------------------------------------------------------- #
def test_17_agregado_reproduz_o_grao_fino():
    locs = [_loc(location="CD1", stock=30), _loc(location="CD2", stock=12)]
    prods = [_prod(saleable=42, total=42, locs=2)]
    assert mod.validate_contract(_snap(locs, prods)) is not None


def test_18_agregado_divergente_do_grao_fino_falha():
    locs = [_loc(location="CD1", stock=30)]
    prods = [_prod(saleable=999, total=999)]
    with pytest.raises(mod.ShopeeStockSyncError, match="grao fino"):
        mod.validate_contract(_snap(locs, prods))


def test_19_chave_duplicada_no_grao_fino_falha():
    locs = [_loc(location="CD1", stock=10), _loc(location="CD1", stock=10)]
    with pytest.raises(mod.ShopeeStockSyncError, match="duplicada"):
        mod.validate_contract(_snap(locs, [_prod(saleable=20, total=20)]))


def test_20_vendavel_maior_que_total_falha():
    locs = [_loc(stock=5)]
    p = _prod(saleable=5, total=1)
    with pytest.raises(mod.ShopeeStockSyncError, match="vendavel"):
        mod.validate_contract(_snap(locs, [p]))


def test_21_nao_saleable_fica_fora_do_vendavel():
    """Grao fino com if_saleable=False nao entra no agregado vendavel."""
    locs = [_loc(location="CD1", stock=10, saleable=True),
            _loc(location="CD2", stock=7, saleable=False)]
    prods = [_prod(saleable=10, total=17, locs=2)]
    mod.validate_contract(_snap(locs, prods))   # nao levanta


def test_22_saleable_ausente_e_distinto_de_false():
    l = _loc(saleable=None)
    assert l.is_saleable is None


# --------------------------------------------------------------------- #
# E. JOIN COM VENDAS                                                     #
# --------------------------------------------------------------------- #
def test_23_vendas_sao_pre_agregadas_antes_do_join():
    """Sem o GROUP BY na CTE, um produto com N pedidos multiplicaria o estoque."""
    sql = str(mod.SQL_PRODUTOS)
    cte = sql[sql.index("WITH vendas"):sql.index("SELECT\n        s.brand")]
    assert "GROUP BY" in cte
    assert cte.index("GROUP BY") < len(cte)
    assert "LEFT JOIN vendas" in sql


def test_24_cancelado_fora_da_demanda():
    """Agora por lista explicita de elegiveis, nao por exclusao isolada."""
    assert "cancelled" not in mod.STATUS_DEMANDA_OPERACIONAL
    assert "cancelled" in mod.STATUS_FORA_DA_DEMANDA
    assert "= ANY(:status_operacional)" in str(mod.SQL_PRODUTOS)


def test_25_janela_e_determinista_e_exclui_o_dia_corrente():
    """[ref_date - 28, ref_date): 28 dias COMPLETOS, sem o dia de hoje."""
    inicio, fim = mod.janela_demanda(date(2026, 9, 24))
    assert inicio == date(2026, 8, 27)
    assert fim == date(2026, 9, 24)
    assert (fim - inicio).days == mod.JANELA_VENDAS_DIAS
    sql = str(mod.SQL_PRODUTOS)
    assert ">= :janela_inicio" in sql
    assert "<  :janela_fim" in sql or "< :janela_fim" in sql


def test_25b_dia_corrente_nunca_entra_na_demanda():
    ref = date(2026, 9, 24)
    inicio, fim = mod.janela_demanda(ref)
    assert fim == ref, "o fim e' EXCLUSIVO, entao ref_date fica de fora"
    assert ref not in (inicio + timedelta(days=i) for i in range((fim - inicio).days))


def test_25c_data_futura_nao_inventa_janela_negativa():
    """ref_date no futuro ainda produz janela de 28 dias completos."""
    inicio, fim = mod.janela_demanda(date(2027, 1, 1))
    assert (fim - inicio).days == 28
    assert inicio < fim


def test_25d_status_elegiveis_sao_lista_explicita():
    assert "cancelled" not in mod.STATUS_DEMANDA_OPERACIONAL
    assert set(mod.STATUS_FORA_DA_DEMANDA) == {"unpaid", "cancelled"}
    assert "= ANY(:status_operacional)" in str(mod.SQL_PRODUTOS)
    assert "<> 'cancelled'" not in str(mod.SQL_PRODUTOS)


def test_25e_to_return_conta_e_unpaid_NAO():
    """Decisao de negocio: unpaid nunca foi pago, entao nao e' demanda."""
    assert "to_return" in mod.STATUS_DEMANDA_OPERACIONAL
    assert "unpaid" not in mod.STATUS_DEMANDA_OPERACIONAL
    assert "unpaid" in mod.STATUS_FORA_DA_DEMANDA
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "pay_time" in fonte and "206/206" in fonte


def test_25f_status_novo_bloqueia_a_carga():
    assert "SQL_STATUS_DESCONHECIDOS" in dir(mod)
    sql = str(mod.SQL_STATUS_DESCONHECIDOS)
    assert "NOT (o.order_status = ANY(:conhecidos))" in sql
    assert "IS NULL" in sql
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "order_status desconhecido na janela" in fonte


def test_25g_dominio_conhecido_cobre_o_medido():
    medidos = {"completed", "cancelled", "shipped", "to_confirm_receive",
               "processed", "to_return", "unpaid", "ready_to_ship"}
    assert medidos == set(mod.STATUS_CONHECIDOS)


# --------------------------------------------------------------------- #
# I. unpaid FORA DA DEMANDA OPERACIONAL                                  #
# --------------------------------------------------------------------- #
def test_47_unpaid_nao_altera_a_cobertura():
    """Dois produtos identicos; um tem unpaid no legado. Cobertura igual."""
    sem = _prod(item="A", saleable=56, units=28, legado_extra=0)
    com = _prod(item="B", saleable=56, units=28, legado_extra=99)
    assert sem.cobertura_torre_dias == com.cobertura_torre_dias == Decimal(56)
    assert sem.avg_daily_units_28d == com.avg_daily_units_28d
    assert com.units_sold_28d_legado_com_unpaid == 127
    assert com.units_sold_28d == 28


def test_48_unpaid_nao_remove_uma_ruptura():
    """Estoque zero + demanda operacional 0, mas unpaid>0: segue sem demanda."""
    p = _prod(item="R", saleable=0, total=0, units=0, legado_extra=50)
    assert p.units_sold_28d == 0
    assert p.classificacao_torre == "SEM_DEMANDA_MEDIDA"
    # e com demanda REAL vira ruptura, provando que a regra nao esta' quebrada
    q = _prod(item="R2", saleable=0, total=0, units=10)
    assert q.classificacao_torre == "RUPTURA_CANDIDATA"


def test_49_unpaid_nao_tira_produto_do_sem_giro():
    """Estoque parado com unpaid nao pode virar 'tem demanda'."""
    p = _prod(item="S", saleable=80, total=80, units=0, legado_extra=40)
    assert p.classificacao_torre == "SEM_GIRO_CANDIDATO"
    assert p.cobertura_torre_dias is None


def test_50_to_return_segue_a_decisao_documentada():
    """Permanece como demanda E a condicao (pagamento) esta' medida no codigo."""
    assert "to_return" in mod.STATUS_COM_PAGAMENTO_COMPROVADO
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "206/206" in fonte
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "to_return" in ddl and "passaram por pagamento" in ddl


def test_51_status_novo_bloqueia_a_publicacao():
    sql = str(mod.SQL_STATUS_DESCONHECIDOS)
    assert "NOT (o.order_status = ANY(:conhecidos))" in sql
    assert "o.order_status IS NULL" in sql
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    i = fonte.index("order_status desconhecido na janela")
    # a guarda roda ANTES de ler a demanda
    assert i < fonte.index("locs = conn.execute(SQL_LOCATIONS")
    assert "raise ShopeeStockSyncError" in fonte[max(0, i - 300):i]


def test_52_demanda_legada_e_contexto_e_nao_classifica():
    """A coluna existe, mas nenhuma regra de classificacao a le."""
    import inspect
    src = inspect.getsource(mod.classificar)
    assert "legado" not in src
    assert "units_sold_28d_legado_com_unpaid" not in src
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "units_sold_28d_legado_com_unpaid" in ddl
    # e' nullable: contexto nunca bloqueia a carga
    i = ddl.index("units_sold_28d_legado_com_unpaid")
    assert "NOT NULL" not in ddl[i:i + 80]


# --------------------------------------------------------------------- #
# F. PROIBICOES E PII                                                    #
# --------------------------------------------------------------------- #
def test_26_nao_le_coluna_de_comprador():
    codigo = _codigo_sem_texto(Path(mod.__file__).read_text(encoding="utf-8"))
    for termo in ("cpf", "buyer", "comprador", "telefone", "endereco", "email"):
        assert termo not in codigo.lower()


def test_27_nao_ESCREVE_na_fato_de_desempenho():
    """Citar a fato vigente e' obrigatorio (a demanda se reconcilia com ela);
    o que nao pode e' escrever nela."""
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert mod.FACT_PRODUTO != "marts.fact_shopee_fbs_daily"
    assert mod.FACT_LOCATION != "marts.fact_shopee_fbs_daily"
    for verbo in ("INSERT INTO marts.fact_shopee_fbs_daily",
                  "DELETE FROM marts.fact_shopee_fbs_daily",
                  "UPDATE marts.fact_shopee_fbs_daily"):
        assert verbo not in fonte


def test_28_publicacao_e_idempotente_por_data():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "DELETE FROM {FACT_LOCATION} WHERE ref_date = :ref_date" in fonte
    assert "DELETE FROM {FACT_PRODUTO} WHERE ref_date = :ref_date" in fonte


def test_29_reconciliacao_acontece_antes_do_commit():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    i_rec = fonte.index("reconciliacao pre-commit falhou")
    i_ret = fonte.index('return {"locations_publicadas"')
    assert i_rec < i_ret


def test_30_dry_run_e_o_default():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "def run(apply: bool = False" in fonte
    assert '"--apply", action="store_true"' in fonte


# --------------------------------------------------------------------- #
# G. MIGRATION                                                           #
# --------------------------------------------------------------------- #
def test_31_migration_encadeia_em_020_da_expedicao():
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "021"' in ddl
    assert 'down_revision = "020"' in ddl


def test_32_migration_nao_altera_a_fato_existente():
    ddl = MIGRATION.read_text(encoding="utf-8")
    corpo = ddl[ddl.index("def upgrade"):]
    assert "ALTER TABLE marts.fact_shopee_fbs_daily" not in corpo
    assert "DROP TABLE marts.fact_shopee_fbs_daily\n" not in corpo


def test_33_kit_fica_fora_por_constraint():
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "ck_fsfs_kit_fica_fora" in ddl


def test_34_cobertura_exige_demanda_por_constraint():
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "ck_fsfs_cobertura_exige_demanda" in ddl


def test_35_vendavel_cabe_no_total_por_constraint():
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "ck_fsfs_vendavel_cabe_no_total" in ddl


def test_36_grao_fino_preserva_location_e_model():
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "PRIMARY KEY (ref_date, shop_account, item_id, model_id, location_id)" in ddl


def test_37_model_id_sentinela_documentada():
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "model_id" in ddl and "sem variacao" in ddl


def test_38_nenhuma_coluna_de_pii_na_ddl():
    ddl = MIGRATION.read_text(encoding="utf-8")
    corpo = ddl[ddl.index("def upgrade"):]
    for termo in ("cpf", "buyer", "phone", "email", "endereco"):
        assert termo not in corpo.lower()


# --------------------------------------------------------------------- #
# H. AUDITORIA: kits nos totais, CD nao-vendavel, vinculo                #
# --------------------------------------------------------------------- #
def test_39_totais_separam_operacional_de_kit():
    """O total geral inclui kits; quem opera reposicao precisa do sem-kit."""
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    for chave in ("full_stock_saleable_total",
                  "full_stock_saleable_operacional",
                  "full_stock_saleable_kits_contexto"):
        assert chave in fonte


def test_40_operacional_mais_kit_fecha_o_total():
    prods = [_prod(item="A", saleable=100, total=100, units=28),
             _prod(item="K", saleable=25, total=25, units=28, kit=True)]
    total = sum(p.full_stock_saleable for p in prods)
    oper = sum(p.full_stock_saleable for p in prods if not p.is_kit)
    kits = sum(p.full_stock_saleable for p in prods if p.is_kit)
    assert oper + kits == total
    assert oper == 100 and kits == 25


def test_41_kit_nao_entra_em_contagem_operacional():
    prods = [_prod(item="K", saleable=0, total=0, units=99, kit=True)]
    # estoque zero com demanda alta seria RUPTURA se nao fosse kit
    assert prods[0].classificacao_torre == "KIT_NAO_CONCILIADO"
    rupturas = [p for p in prods if p.classificacao_torre == "RUPTURA_CANDIDATA"]
    assert rupturas == []


def test_42_cd_nao_vendavel_fica_fora_do_vendavel_mas_conta_no_total():
    locs = [_loc(location="CD1", stock=40, saleable=True),
            _loc(location="CD2", stock=9, saleable=False)]
    prods = [_prod(saleable=40, total=49, locs=2)]
    mod.validate_contract(_snap(locs, prods))       # nao levanta
    assert prods[0].full_stock_total - prods[0].full_stock_saleable == 9


def test_43_cd_com_saleable_ausente_tambem_fica_fora_do_vendavel():
    locs = [_loc(location="CD1", stock=40, saleable=True),
            _loc(location="CD2", stock=3, saleable=None)]
    prods = [_prod(saleable=40, total=43, locs=2)]
    mod.validate_contract(_snap(locs, prods))       # nao levanta


def test_44_vinculo_sem_valor_inalcancavel():
    """O valor sai do DOMINIO; pode sobreviver em comentario que explica a saida."""
    ddl = MIGRATION.read_text(encoding="utf-8")
    i = ddl.index("VINCULOS = (")
    assert "FORA_DO_CATALOGO_DE_VENDAS" not in ddl[i:i + 200]
    codigo = _codigo_sem_texto(Path(mod.__file__).read_text(encoding="utf-8"))
    assert "FORA_DO_CATALOGO_DE_VENDAS" not in codigo


def test_45_advance_stock_documentado_e_nao_somado():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "advance_stock" in fonte
    assert "in_transit" in fonte
    # citado em texto e em aviso, mas NUNCA lido de SQL
    for sql in (str(mod.SQL_LOCATIONS), str(mod.SQL_PRODUTOS)):
        assert "advance_stock" not in sql
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert "advance_stock" in ddl and "in_transit" in ddl
    corpo = ddl[ddl.index("def upgrade"):]
    assert "advance_stock" not in corpo, "nao vira coluna"


def test_46_resumo_declara_a_janela_e_os_limites():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    for chave in ("janela_demanda", "inicio_inclusivo", "fim_exclusivo",
                  "dia_corrente_incluido", "limites_provisorios",
                  "status_demanda_operacional", "status_fora_da_demanda",
                  "demanda_operacional_unidades",
                  "demanda_legada_com_unpaid_unidades",
                  "produtos_afetados_por_unpaid"):
        assert chave in fonte
