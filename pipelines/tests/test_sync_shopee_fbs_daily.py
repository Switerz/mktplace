"""Gate FULL-SH-1A-R — contrato de marts.fact_shopee_fbs_daily.

Testes de SEMANTICA e CONTRATO: classificacao, formula de GMV, shares,
cobertura, proibicoes. A maquina de estados (lock, rollback, commit
indeterminado, idempotencia) vive em test_sync_shopee_fbs_execucao.py.
"""
from __future__ import annotations

import ast
import io
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import sync_shopee_fbs_daily as mod

RAIZ = Path(__file__).resolve().parents[2]
FONTE = io.open(RAIZ / "pipelines" / "sync_shopee_fbs_daily.py",
                encoding="utf-8").read()
MIGRATION = io.open(
    RAIZ / "apps" / "api" / "alembic" / "versions"
    / "019_create_fact_shopee_fbs_daily.py", encoding="utf-8").read()

#: Codigo executavel, sem docstrings nem comentarios. Varias proibicoes deste
#: gate sao sobre o que o codigo FAZ -- procurar no texto cru daria falso
#: positivo em cada explicacao que cita o termo proibido.
def _codigo_sem_texto(fonte: str) -> str:
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)):
            corpo = no.body
            if (corpo and isinstance(corpo[0], ast.Expr)
                    and isinstance(corpo[0].value, ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                corpo[0].value.value = ""
    return ast.unparse(arvore)


CODIGO = _codigo_sem_texto(FONTE)

#: DDL EXECUTAVEL da migration: so' o texto passado a `op.execute`. A docstring
#: do 019 explica em prosa por que `reserved_stock`, `delivered_date` e
#: `recipient_address` NAO entram -- procurar esses termos no arquivo cru
#: reprovaria justamente a documentacao que os proibe.
def _ddl_da_migration(fonte: str) -> str:
    partes: list[str] = []
    for no in ast.walk(ast.parse(fonte)):
        if (isinstance(no, ast.Call)
                and isinstance(no.func, ast.Attribute)
                and no.func.attr == "execute"):
            for arg in no.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    partes.append(arg.value)
                elif isinstance(arg, ast.JoinedStr):
                    partes.extend(v.value for v in arg.values
                                  if isinstance(v, ast.Constant)
                                  and isinstance(v.value, str))
    return "\n".join(partes)


DDL = _ddl_da_migration(MIGRATION)


def _linha(ref_date=date(2026, 8, 1), brand="barbours", conta="barbours",
           classe="fbs", criados=10, elegiveis=8, cancelados=2,
           to_return=0, unpaid=0, gmv="1000.00", unidades=12,
           to_return_gmv="0", unpaid_gmv="0", h_sum=3600, h_n=5,
           ingest=None) -> mod.FactRow:
    return mod.FactRow(
        ref_date=ref_date, brand=brand, shop_account=conta, fbs_class=classe,
        created_orders=criados, eligible_orders=elegiveis,
        cancelled_orders=cancelados, to_return_orders=to_return,
        unpaid_orders=unpaid, gross_gmv=Decimal(gmv), gross_units=unidades,
        to_return_gmv=Decimal(to_return_gmv), unpaid_gmv=Decimal(unpaid_gmv),
        handling_seconds_sum=h_sum, handling_sample_count=h_n,
        source_max_ingested_at=ingest,
    )


def _snap(linhas, janela=None) -> mod.SourceSnapshot:
    janela = janela or mod.Window(date(2026, 8, 1), date(2026, 8, 31))
    return mod.SourceSnapshot(window=janela, rows=list(linhas), preflight={})


# ---------------------------------------------------------------------------
# 1-3. Classificacao pela flag OBSERVADA no pedido
# ---------------------------------------------------------------------------

def test_1_fulfilled_by_shopee_classifica_como_fbs():
    assert mod.FLAG_FBS == "fulfilled_by_shopee"
    # O CASE do SQL e' a unica fonte da classe; a ligacao precisa estar la'.
    assert "WHEN :flag_fbs    THEN 'fbs'" in mod.SQL_AGREGA.text


def test_2_fulfilled_by_local_seller_classifica_como_seller():
    assert mod.FLAG_SELLER == "fulfilled_by_local_seller"
    assert "WHEN :flag_seller THEN 'seller'" in mod.SQL_AGREGA.text


def test_3_flag_nula_ou_inesperada_falha_fechado():
    """Sem classe 'unknown': valor fora do dominio tem de ABORTAR a carga."""
    assert mod.FLAGS_AUTORIZADAS == (mod.FLAG_FBS, mod.FLAG_SELLER)
    assert mod.CLASSES == ("fbs", "seller")

    # O preflight precisa medir as duas condicoes...
    assert "flag_nula" in mod.SQL_PREFLIGHT.text
    assert "flag_fora_dominio" in mod.SQL_PREFLIGHT.text
    # ...e `read_source` precisa LEVANTAR em cada uma.
    corpo = FONTE.split("def read_source")[1].split("\ndef ")[0]
    for chave in ("flag_nula", "flag_fora_dominio"):
        trecho = corpo.split(f'pre["{chave}"]')[1][:400]
        assert "raise SourceUnavailableError" in trecho, (
            f"{chave} precisa falhar fechado, nao apenas avisar")

    # O CASE nao tem ELSE: valor novo produz NULL, e a linha NOT NULL barra.
    assert "ELSE" not in mod.SQL_AGREGA.text.split("END")[0].upper()

    # E a migration nao admite terceira classe.
    assert "CHECK (fbs_class IN ('fbs', 'seller'))" in MIGRATION


def test_4_flag_do_catalogo_nunca_classifica_pedido():
    """`is_fulfillment_by_shopee` nao pode aparecer em NENHUM SQL executavel."""
    assert "is_fulfillment_by_shopee" not in CODIGO, (
        "a flag de catalogo nao pode ser lida por este pipeline")
    assert "stg_shopee_products" not in CODIGO, (
        "o catalogo nao e' fonte deste pipeline")
    assert "stg_shopee_product_models" not in CODIGO


def test_5_mesmo_item_nas_duas_modalidades_fica_separado_por_pedido():
    """A classe e' do PEDIDO: o mesmo item vendido nas duas modalidades produz
    duas linhas distintas, e nada some.

    Medido no FULL-SH-0: 333 de 587 itens (56,7%) tem essa dupla natureza.
    """
    linhas = [
        _linha(classe="fbs", elegiveis=1, criados=1, cancelados=0,
               gmv="100.00", unidades=1, h_sum=0, h_n=0),
        _linha(classe="seller", elegiveis=1, criados=1, cancelados=0,
               gmv="250.00", unidades=2, h_sum=0, h_n=0),
    ]
    s = mod.summarize(_snap(linhas))
    assert s["by_class"]["fbs"]["gross_gmv"] == "100.00"
    assert s["by_class"]["seller"]["gross_gmv"] == "250.00"
    assert s["gross_gmv"] == "350.00"
    # A chave da fato separa por classe, entao as duas linhas coexistem.
    assert "PRIMARY KEY (ref_date, brand, shop_account, fbs_class)" in MIGRATION


# ---------------------------------------------------------------------------
# 6, 29. Fechamento aritmetico
# ---------------------------------------------------------------------------

def test_6_gmv_pedidos_e_unidades_fecham_entre_classes_e_total():
    linhas = [
        _linha(classe="fbs", criados=10, elegiveis=8, cancelados=2,
               gmv="800.00", unidades=9),
        _linha(classe="seller", criados=5, elegiveis=4, cancelados=1,
               gmv="200.00", unidades=5),
    ]
    s = mod.summarize(_snap(linhas))
    assert s["gross_gmv"] == "1000.00"
    assert s["eligible_orders"] == 12
    assert s["gross_units"] == 14
    assert s["created_orders"] == 15
    assert s["cancelled_orders"] == 3


def test_29_fbs_mais_seller_fecha_exatamente_o_total_elegivel():
    linhas = [
        _linha(classe="fbs", criados=7, elegiveis=6, cancelados=1,
               gmv="761555.21", unidades=11),
        _linha(classe="seller", criados=4, elegiveis=3, cancelados=1,
               gmv="180856.45", unidades=4),
    ]
    s = mod.summarize(_snap(linhas))
    soma = (Decimal(s["by_class"]["fbs"]["gross_gmv"])
            + Decimal(s["by_class"]["seller"]["gross_gmv"]))
    assert soma == Decimal(s["gross_gmv"])
    assert (s["by_class"]["fbs"]["eligible_orders"]
            + s["by_class"]["seller"]["eligible_orders"]) == s["eligible_orders"]
    assert (s["by_class"]["fbs"]["gross_units"]
            + s["by_class"]["seller"]["gross_units"]) == s["gross_units"]


# ---------------------------------------------------------------------------
# 7, 26, 28, 30. Semantica de zero x ausencia no share
# ---------------------------------------------------------------------------

def test_7_28_share_com_denominador_zero_retorna_none():
    assert mod.share(Decimal(0), Decimal(0)) is None
    assert mod.share(Decimal(10), 0) is None
    assert mod.share(None, None) is None


def test_26_denominador_positivo_e_numerador_zero_retorna_zero_por_cento():
    """Apice: vendeu, nao teve FBS. Isso e' 0%, nunca NULL nem ausencia."""
    z = mod.share(Decimal(0), Decimal("274629.58"))
    assert z == Decimal(0)
    assert z is not None
    # Numerador ausente (classe sem linha) tambem e' zero MEDIDO.
    assert mod.share(None, Decimal("274629.58")) == Decimal(0)


def test_26b_apice_com_pedidos_e_sem_fbs_da_zero_por_cento():
    linhas = [_linha(brand="apice", conta="apice", classe="seller",
                     criados=3095, elegiveis=2696, cancelados=399,
                     gmv="274629.58", unidades=3677, h_sum=100, h_n=10)]
    cob = mod.cobertura_por_conta(_snap(linhas))
    assert cob["apice"]["coberta"] is True
    assert cob["apice"]["share_fbs_gmv"] == Decimal(0)
    assert cob["apice"]["share_fbs_gmv"] is not None


def test_30_shares_usam_valores_integrais_sem_arredondamento():
    """A divisao nao pode arredondar: quem formata decide a precisao."""
    linhas = [
        _linha(classe="fbs", criados=1, elegiveis=1, cancelados=0,
               gmv="762898.83", unidades=1, h_sum=0, h_n=0),
        _linha(classe="seller", criados=1, elegiveis=1, cancelados=0,
               gmv="181459.47", unidades=1, h_sum=0, h_n=0),
    ]
    s = mod.summarize(_snap(linhas))
    esperado = Decimal("762898.83") / (Decimal("762898.83") + Decimal("181459.47"))
    assert s["share_fbs_gmv"] == esperado
    # Precisao efetiva bem alem de 2 casas -- prova de que nao houve round().
    assert str(s["share_fbs_gmv"])[:12] == str(esperado)[:12]
    assert len(str(s["share_fbs_gmv"]).split(".")[1]) > 4


# ---------------------------------------------------------------------------
# 8-9, 27. Cobertura: Apice, Kokeshi
# ---------------------------------------------------------------------------

def test_9_27_kokeshi_ausente_e_declarada_nunca_zero():
    linhas = [_linha(brand="barbours", conta="barbours")]
    cob = mod.cobertura_por_conta(_snap(linhas))
    assert "kokeshi" in cob
    assert cob["kokeshi"]["coberta"] is False
    assert cob["kokeshi"]["share_fbs_gmv"] is None, (
        "Kokeshi ausente nao pode virar 0%")
    assert "fora da esteira API" in cob["kokeshi"]["motivo"]
    assert mod.MARCAS_FORA_DA_API == ("kokeshi",)
    # E nao pode ser suprida pelo XLSX neste pipeline.
    assert "order_item_snapshots" not in CODIGO


def test_9b_conta_esperada_sem_pedido_e_fora_da_cobertura_nao_zero():
    linhas = [_linha(brand="barbours", conta="barbours")]
    cob = mod.cobertura_por_conta(_snap(linhas))
    for conta in ("apice", "lescent", "rituaria"):
        assert cob[conta]["coberta"] is False
        assert cob[conta]["share_fbs_gmv"] is None


def test_9c_aviso_de_cobertura_viaja_com_o_resultado():
    snap = mod.SourceSnapshot(
        window=mod.Window(date(2026, 8, 1), date(2026, 8, 31)),
        rows=[_linha()],
        preflight={"contas_observadas": ["barbours"],
                   "contas_ausentes": ["apice", "lescent", "rituaria"]})
    avisos = " ".join(mod.validate_contract(snap))
    assert "kokeshi" in avisos.lower()
    assert "cobertura API" in avisos
    assert "Shopee total" in avisos, "o aviso precisa negar o rotulo 'Shopee total'"
    assert "entrega" in avisos.lower()


# ---------------------------------------------------------------------------
# 10. Cancelamento com denominador proprio
# ---------------------------------------------------------------------------

def test_10_cancelamento_usa_denominador_proprio():
    """Denominador = TODOS os criados, nao os elegiveis."""
    linhas = [_linha(criados=100, elegiveis=90, cancelados=10,
                     gmv="900.00", unidades=90)]
    s = mod.summarize(_snap(linhas))
    assert s["cancellation_rate"] == Decimal(10) / Decimal(100)
    # Explicitamente NAO e' cancelados/elegiveis.
    assert s["cancellation_rate"] != Decimal(10) / Decimal(90)
    assert "eligible_orders + cancelled_orders = created_orders" in MIGRATION


# ---------------------------------------------------------------------------
# 11. Handling
# ---------------------------------------------------------------------------

def test_11_handling_exclui_sem_pickup_e_informa_sample_count():
    sql = mod.SQL_AGREGA.text
    assert "pickup_done_time - pay_time" in sql
    assert "handling_sample_count" in sql
    # Ambos os filtros exigem os dois carimbos presentes.
    assert sql.count("pickup_done_time IS NOT NULL AND pay_time IS NOT NULL") == 2
    # Nao ha COALESCE do carimbo para zero em lugar nenhum.
    assert "COALESCE(pickup_done_time" not in sql
    assert "COALESCE(pay_time" not in sql
    # Soma e amostra andam juntas, para a media ser recomposta e censurada.
    assert "ck_fsfd_soma_exige_amostra" in MIGRATION
    assert "ck_fsfd_amostra_cabe_na_coorte" in MIGRATION


def test_11b_nao_existe_metrica_de_entrega():
    """A API Shopee nao tem entrega real; coluna vazia convidaria a mentira."""
    for proibida in ("delivery_seconds", "delivered_date", "delivery_sample"):
        assert proibida not in DDL, f"{proibida} nao pode ser COLUNA desta fato"
        assert proibida not in CODIGO


def test_11c_media_nunca_e_armazenada_sozinha():
    assert "handling_seconds_sum" in MIGRATION
    assert "handling_sample_count" in MIGRATION
    assert "handling_avg" not in MIGRATION
    assert "avg_handling" not in MIGRATION


# ---------------------------------------------------------------------------
# 12. Timezone
# ---------------------------------------------------------------------------

def test_12_timezone_operacional_aplicado():
    assert mod.OPERATIONAL_TZ.utcoffset(None).total_seconds() == -3 * 3600
    # A competencia e' o campo ja' convertido para BRT na silver.
    assert "created_date_brt" in mod.SQL_AGREGA.text
    assert "create_time" not in mod.SQL_AGREGA.text, (
        "usar create_time (UTC) deslocaria a coorte em 3 horas")
    hoje = mod._hoje_brt(datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc))
    assert hoje == date(2026, 9, 16), "02:00Z ainda e' o dia anterior em BRT"


def test_12b_janela_fecha_em_d_menos_1():
    w = mod.resolve_window(mod.MODE_INCREMENTAL,
                           datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc))
    assert w.date_to == date(2026, 9, 15), "D0 nunca e' publicado"


def test_12c_piso_historico_barra_janela_anterior():
    assert mod.SOURCE_COMPLETE_FROM_DATE == date(2026, 1, 1)
    with pytest.raises(mod.ShopeeFbsSyncError, match="piso historico"):
        mod.Window(date(2025, 12, 1), date(2026, 1, 31))


# ---------------------------------------------------------------------------
# 13. PII
# ---------------------------------------------------------------------------

def test_13_zero_pii_no_schema_no_codigo_e_nos_erros():
    proibidos = (
        "buyer_username", "buyer_user_id", "buyer_cpf", "buyer_cpf_id",
        "recipient_address", "dropshipper_phone", "message_to_seller",
        "invoice_data", "note",
    )
    for campo in proibidos:
        assert campo not in DDL, f"{campo} nao pode ser coluna do schema"
        assert campo not in CODIGO, f"{campo} nao pode ser lido pelo pipeline"

    # A sanitizacao remove PII que escape por mensagem do driver.
    sujo = ("erro em 123.456.789-00 de a@b.com tel (11)91234-5678 "
            "dsn postgresql://u:p@h/db")
    limpo = mod.sanitizar(sujo)
    for vazamento in ("123.456.789-00", "a@b.com", "91234-5678", "u:p@h"):
        assert vazamento not in limpo
    assert "[REMOVIDO]" in limpo and "postgresql://***" in limpo


# ---------------------------------------------------------------------------
# 15. Escopo do DELETE
# ---------------------------------------------------------------------------

def test_15_delete_e_escopado_por_data_e_nao_apaga_outro_periodo():
    sql = mod.SQL_DELETE_JANELA.text
    assert "ref_date BETWEEN :date_from AND :date_to" in sql
    assert "DELETE FROM marts.fact_shopee_fbs_daily" in sql
    # Sem DELETE incondicional em lugar nenhum.
    assert not re.search(r"DELETE\s+FROM\s+marts\.\w+\s*(;|$)", CODIGO)
    assert "TRUNCATE" not in CODIGO.upper()
    # A fato e' exclusiva da Shopee: nao ha outra tabela alvo no pipeline.
    # O SQL referencia a fato por f-string (`{FACT_TABLE}`), entao o alvo real
    # so' aparece depois de resolvido. Inspeciona os objetos ja' construidos.
    executaveis = " ".join(
        obj.text for nome, obj in vars(mod).items()
        if nome.startswith("SQL_") and hasattr(obj, "text"))
    alvos = set(re.findall(r"(?:INSERT INTO|DELETE FROM|UPDATE)\s+([\w.]+)",
                           executaveis))
    permitidos = {"marts.fact_shopee_fbs_daily", mod.STAGING_TABLE,
                  "audit.source_sync_run"}
    assert alvos <= permitidos, f"alvo de escrita nao autorizado: {alvos - permitidos}"
    assert "marts.fact_shopee_fbs_daily" in alvos
    # Nenhuma outra tabela de marts pode ser escrita por este pipeline.
    assert not {a for a in alvos if a.startswith("marts.")} - {"marts.fact_shopee_fbs_daily"}


def test_15b_leitura_vazia_nao_apaga_historico():
    corpo = FONTE.split("def publish_in_transaction")[1].split("\ndef ")[0]
    assert "if not snapshot.rows" in corpo
    assert "leitura vazia" in corpo and "nao apaga historico" in corpo


# ---------------------------------------------------------------------------
# 19-21. Dinheiro e a proibicao de total_amount
# ---------------------------------------------------------------------------

def test_19_dinheiro_em_decimal_nunca_float():
    # `_valor_finito` usa float('inf') como SENTINELA de comparacao -- e' o
    # guarda que barra infinito, nao uma conversao de dinheiro. Fora dele,
    # nenhuma chamada a float() pode existir.
    depois = CODIGO.split("def _valor_finito")[1]
    sem_guarda = CODIGO.split("def _valor_finito")[0] + depois.split("\ndef ", 1)[1]
    assert "float(" not in sem_guarda, "dinheiro nao pode passar por float"
    # O tipo declarado da fato e as conversoes sao Decimal/NUMERIC.
    assert "gross_gmv                NUMERIC     NOT NULL" in MIGRATION
    assert "gross_gmv=Decimal(" in FONTE
    campo = mod.FactRow.__dataclass_fields__["gross_gmv"]
    assert campo.type in ("Decimal", Decimal)
    # E o guarda de finitude cobre Decimal explicitamente.
    assert mod._valor_finito(Decimal("1.5")) is True
    assert mod._valor_finito(Decimal("NaN")) is False
    assert mod._valor_finito(Decimal("Infinity")) is False


def test_20_21_total_amount_nunca_participa_de_gmv():
    """Contraprova: `total_amount` mede 1,6%-9,4% ABAIXO do canonico."""
    assert "total_amount" not in CODIGO, (
        "total_amount nao pode aparecer em nenhuma expressao executavel")
    # A formula e' a soma de item_total dos elegiveis.
    assert "sum(i.item_total)" in mod.SQL_AGREGA.text
    assert "item_total" in mod.SQL_PREFLIGHT.text
    # E a divergencia medida esta registrada na migration, nao esquecida.
    assert "854.911,95" in MIGRATION and "-9,38%" in MIGRATION


def test_22_is_sale_nao_e_usado():
    """`is_sale` exclui to_return e unpaid, que este contrato mantem no GMV."""
    assert "is_sale" not in CODIGO, (
        "is_sale excluiria to_return e unpaid do GMV bruto")
    assert "order_status <> 'cancelled'" in mod.SQL_AGREGA.text


# ---------------------------------------------------------------------------
# 23-25. Elegibilidade: so' cancelled sai
# ---------------------------------------------------------------------------

def test_25_cancelled_nao_entra_no_gmv():
    sql = mod.SQL_AGREGA.text
    assert "COALESCE(sum(gmv) FILTER (WHERE order_status <> 'cancelled'), 0) AS gross_gmv" in sql
    assert "count(*) FILTER (WHERE order_status =  'cancelled')            AS cancelled_orders" in sql


@pytest.mark.parametrize("status", ["to_return", "unpaid"])
def test_23_24_to_return_e_unpaid_entram_no_gmv_bruto(status):
    sql = mod.SQL_AGREGA.text
    # Nao ha exclusao deles do GMV: o unico predicado e' <> 'cancelled'.
    assert f"order_status <> '{status}'" not in sql
    assert f"order_status NOT IN ('cancelled', '{status}')" not in sql
    # E cada um tem coluna propria de contagem e de valor.
    assert f"count(*) FILTER (WHERE order_status =  '{status}')" in sql
    assert f"COALESCE(sum(gmv) FILTER (WHERE order_status =  '{status}'), 0)" in sql
    assert f"{status}_orders" in MIGRATION
    assert f"{status}_gmv" in MIGRATION


def test_23b_recortes_sao_parcela_do_bruto_nao_deducao():
    """to_return/unpaid sao publicados, nunca subtraidos em silencio."""
    assert "ck_fsfd_recorte_gmv_cabe" in MIGRATION
    assert "to_return_gmv + unpaid_gmv <= gross_gmv" in MIGRATION
    linhas = [_linha(criados=10, elegiveis=10, cancelados=0, gmv="1000.00",
                     to_return=1, to_return_gmv="100.00",
                     unpaid=1, unpaid_gmv="50.00", unidades=10)]
    s = mod.summarize(_snap(linhas))
    # O bruto continua inteiro: os recortes nao foram descontados.
    assert s["gross_gmv"] == "1000.00"


def test_23c_gmv_bruto_nao_e_chamado_de_receita_liquida():
    avisos = " ".join(mod.validate_contract(_snap([_linha()])))
    assert "BRUTO" in avisos
    assert "nao e' receita liquida nem realizada" in avisos.lower().replace("ã", "a")


# ---------------------------------------------------------------------------
# Integridade estrutural da migration
# ---------------------------------------------------------------------------

def test_migration_encadeia_em_018_sem_criar_branch():
    assert mod is not None
    assert 'revision = "019"' in MIGRATION
    assert 'down_revision = "018"' in MIGRATION
    assert "branch_labels = None" in MIGRATION


def test_migration_barra_nan_e_infinito_explicitamente():
    """'NaN'::numeric >= 0 e' TRUE no Postgres: CHECK de sinal NAO pega NaN."""
    assert "ck_fsfd_gmv_nao_nan" in MIGRATION
    assert "gross_gmv <> 'NaN'" in MIGRATION
    assert "ck_fsfd_gmv_finito" in MIGRATION
    assert "gross_gmv > '-Infinity'" in MIGRATION


def test_migration_nao_tem_coluna_de_estoque():
    """Estoque e' o gate FULL-SH-2, e a flag de catalogo nao entra aqui."""
    for proibida in ("available_stock", "reserved_stock", "is_kit",
                     "item_status", "has_model"):
        assert proibida not in DDL


def test_preflight_mede_o_contrato_antes_de_agregar():
    sql = mod.SQL_PREFLIGHT.text
    for medida in ("flag_nula", "flag_fora_dominio", "pedidos_duplicados",
                   "item_total_nulo", "quantidade_invalida", "contas"):
        assert medida in sql


# ---------------------------------------------------------------------------
# GRAO: o join pedido x itens NAO pode multiplicar metrica de pedido
#
# Este e' o ponto critico da fato. `silver.stg_shopee_order_items` tem UMA
# linha por item; `stg_shopee_orders` tem uma por pedido. Um join ingenuo
# faria um pedido de 3 itens contar 3 vezes em created_orders, 3 vezes em
# cancelled_orders e somar o handling 3 vezes.
#
# A defesa e' estrutural: os itens sao PRE-AGREGADOS por (shop_account,
# order_sn) na CTE `itn` ANTES do join, o que torna a relacao 1:1. Os testes
# abaixo travam essa estrutura -- uma refatoracao que mova o SUM para o join
# principal quebra aqui.
#
# Verificado tambem em PostgreSQL 16 descartavel no gate FULL-SH-1A-R/V, com
# pedido de 3 itens, cancelado de 2 itens, to_return de 4 itens e pedido sem
# item nenhum. Resultado medido: created=4 (nao 10), cancelled=1 (nao 2),
# to_return=1 (nao 4), handling=21600s (nao 64800s), amostra=1 (nao 3).
# ---------------------------------------------------------------------------

def test_grao_itens_sao_pre_agregados_por_pedido_antes_do_join():
    sql = mod.SQL_AGREGA.text
    # A CTE dos itens existe e agrega por pedido.
    assert "itn AS (" in sql, "os itens precisam de CTE propria"
    # Ate' o fechamento da CTE (`),`), e nao ate' o primeiro ")" -- que fica
    # dentro de `sum(i.item_total)`.
    itn = sql.split("itn AS (")[1].split("),")[0]
    assert "sum(i.item_total)" in itn and "sum(i.quantity)" in itn
    assert "GROUP BY 1, 2" in itn, (
        "a CTE de itens precisa agregar por (shop_account, order_sn) -- sem "
        "isso o join vira 1:N e multiplica as metricas de pedido")
    # E o join usa a CTE ja' agregada, nao a tabela crua.
    j = sql.split("j AS (")[1].split("SELECT\n")[0]
    assert "LEFT JOIN itn t" in j, "o join tem de ser com a CTE agregada"
    assert "stg_shopee_order_items" not in j, (
        "a tabela crua de itens nao pode ser joinada direto no grao do pedido")


def test_grao_contagens_de_pedido_nao_somam_itens():
    """Toda contagem de populacao e' `count(*)` sobre o grao do pedido."""
    sql = mod.SQL_AGREGA.text
    for coluna in ("created_orders", "eligible_orders", "cancelled_orders",
                   "to_return_orders", "unpaid_orders", "handling_sample_count"):
        # A expressao pode ocupar varias linhas (`count(*) FILTER (...)`),
        # entao inspeciona o texto que PRECEDE o alias, recortado a partir da
        # virgula que separa a expressao anterior.
        assert f"AS {coluna}" in sql, f"{coluna} nao encontrada no SELECT"
        antes = sql.split(f"AS {coluna}")[0]
        trecho = antes[antes.rfind(",", 0, len(antes) - 1) + 1:]
        assert "count(*)" in trecho, (
            f"{coluna} tem de ser count(*) de PEDIDOS, nunca sum() de itens: "
            f"{trecho.strip()[:80]}")
        assert "sum(" not in trecho, f"{coluna} nao pode somar item"


def test_grao_gmv_e_unidades_permanecem_metricas_de_item():
    """Valor e quantidade vem dos itens -- pre-agregados, mas de item."""
    sql = mod.SQL_AGREGA.text
    assert "sum(gmv) FILTER" in sql, "GMV soma o valor pre-agregado do pedido"
    assert "sum(un)  FILTER" in sql or "sum(un) FILTER" in sql


def test_grao_handling_contribui_uma_vez_por_pedido():
    """`handling_seconds_sum` soma sobre `j`, que tem uma linha por pedido."""
    sql = mod.SQL_AGREGA.text
    bloco = sql.split("handling_seconds_sum")[0]
    # A expressao do handling usa os carimbos do PEDIDO, nunca do item.
    assert "pickup_done_time - pay_time" in sql
    assert "i.pickup_done_time" not in sql and "i.pay_time" not in sql
    # E a amostra e' count(*), ja' coberto acima -- aqui travamos o par.
    assert sql.count("pickup_done_time IS NOT NULL AND pay_time IS NOT NULL") == 2


def test_grao_pedido_sem_item_nao_e_eliminado():
    """LEFT JOIN + COALESCE: pedido sem item vira GMV zero, nunca linha ausente.

    Medido no descartavel: o pedido G1 (lescent, sem item) publicou
    eligible_orders=1, gross_gmv=0, gross_units=0 e ainda contou no handling.
    """
    sql = mod.SQL_AGREGA.text
    assert "LEFT JOIN itn" in sql, "INNER JOIN eliminaria pedido sem item"
    assert "COALESCE(t.gmv, 0)" in sql
    assert "COALESCE(t.un, 0)" in sql


def test_grao_contraprova_aritmetica_dos_quatro_casos():
    """Reproduz, na camada de agregacao Python, os quatro casos exigidos pelo
    gate FULL-SH-1A-R/V. Os numeros sao os MEDIDOS no PostgreSQL 16 real.

        A1 completed FBS, 3 itens, pay+pickup  -> GMV 600, 6 un, handling 21600
        B1 cancelled FBS, 2 itens              -> fora do GMV, conta 1 pedido
        C1 to_return FBS, 4 itens, sem pickup  -> GMV 100, fora da amostra
        D1 unpaid    FBS, 1 item,  sem pay     -> GMV 50,  fora da amostra
    """
    linha = _linha(
        classe="fbs",
        criados=4,      # A1 + B1 + C1 + D1 -- NAO os 10 itens
        elegiveis=3,    # A1 + C1 + D1
        cancelados=1,   # B1, apesar de ter 2 itens
        to_return=1,    # C1, apesar de ter 4 itens
        unpaid=1,       # D1
        gmv="750.00",   # 600 + 100 + 50
        unidades=11,    # 6 + 4 + 1
        to_return_gmv="100.00", unpaid_gmv="50.00",
        h_sum=21600,    # so' A1: 6h. Nao 3x6h.
        h_n=1,          # so' A1
    )
    s = mod.summarize(_snap([linha]))
    assert s["created_orders"] == 4
    assert s["eligible_orders"] == 3
    assert s["cancelled_orders"] == 1
    assert s["gross_gmv"] == "750.00"
    assert s["gross_units"] == 11
    assert s["handling_sample_count"] == 1
    # Cancelamento no denominador proprio: 1 de 4 criados.
    assert s["cancellation_rate"] == Decimal(1) / Decimal(4)
    # E as invariantes da linha passam pelo validador.
    mod.validate_contract(_snap([linha]))
