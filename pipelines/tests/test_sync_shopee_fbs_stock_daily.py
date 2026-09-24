"""Gate FULL-SOURCE-1 Fase 2 — contrato de marts.fact_shopee_fbs_stock_daily.

Testes de SEMANTICA, CARDINALIDADE e PROIBICOES. Nenhum teste abre conexao,
rede ou subprocesso; nenhum payload contem dado de comprador.
"""
from __future__ import annotations

import ast
import io
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import sync_shopee_fbs_stock_daily as mod

MIGRATION = (Path(mod.__file__).resolve().parents[1]
             / "apps" / "api" / "alembic" / "versions"
             / "020_create_fact_shopee_fbs_stock_daily.py")


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
          conta="barbours", locs=1):
    media = Decimal(units) / Decimal(28) if units else Decimal(0)
    cob = (Decimal(saleable) / media) if units else None
    return mod.ProdutoRow(
        ref_date=date(2026, 9, 24), brand=conta, shop_account=conta,
        item_id=item, model_id="0", item_name="Produto", item_sku="SKU",
        item_status="normal", is_kit=kit, full_stock_saleable=saleable,
        full_stock_total=total, location_count=locs, seller_stock_total=99,
        reserved_stock=5, summary_available_stock=123, units_sold_28d=units,
        days_with_sales_28d=1 if units else 0, avg_daily_units_28d=media,
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
    lim = mod.COBERTURA_EXCESSO_DIAS
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
    assert mod.COBERTURA_BAIXA_DIAS == 7


def test_13_limite_de_excesso_declarado_provisorio():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    i = fonte.index("COBERTURA_EXCESSO_DIAS")
    assert "PROVISORIO" in fonte[max(0, i - 700):i].upper()


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
    assert "order_status <> 'cancelled'" in str(mod.SQL_PRODUTOS)


def test_25_janela_de_vendas_respeita_o_parametro():
    snap_corte = mod.JANELA_VENDAS_DIAS
    assert snap_corte == 28
    assert ":corte" in str(mod.SQL_PRODUTOS)


# --------------------------------------------------------------------- #
# F. PROIBICOES E PII                                                    #
# --------------------------------------------------------------------- #
def test_26_nao_le_coluna_de_comprador():
    codigo = _codigo_sem_texto(Path(mod.__file__).read_text(encoding="utf-8"))
    for termo in ("cpf", "buyer", "comprador", "telefone", "endereco", "email"):
        assert termo not in codigo.lower()


def test_27_nao_toca_a_fato_de_desempenho():
    codigo = _codigo_sem_texto(Path(mod.__file__).read_text(encoding="utf-8"))
    assert "fact_shopee_fbs_daily" not in codigo


def test_28_publicacao_e_idempotente_por_data():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "DELETE FROM {FACT_LOCATION} WHERE ref_date = :ref_date" in fonte
    assert "DELETE FROM {FACT_PRODUTO} WHERE ref_date = :ref_date" in fonte


def test_29_reconciliacao_acontece_antes_do_commit():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    i_rec = fonte.index("reconciliacao pre-commit falhou")
    i_ret = fonte.index('return {"locations"')
    assert i_rec < i_ret


def test_30_dry_run_e_o_default():
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "def run(apply: bool = False" in fonte
    assert '"--apply", action="store_true"' in fonte


# --------------------------------------------------------------------- #
# G. MIGRATION                                                           #
# --------------------------------------------------------------------- #
def test_31_migration_encadeia_em_019():
    ddl = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "020"' in ddl
    assert 'down_revision = "019"' in ddl


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
