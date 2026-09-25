"""Gate SHOPEE-API-PRODUTOS-2 — contrato do publisher de produtos Shopee via API.

Testes de SEMANTICA e PROIBICOES. Nenhum teste abre conexao, rede ou
subprocesso; nenhum payload contem dado de comprador.
"""
from __future__ import annotations

import ast
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines import sync_shopee_produtos_api as mod

MIGRATION = (Path(mod.__file__).resolve().parents[1]
             / "apps" / "api" / "alembic" / "versions"
             / "022_add_procedencia_shopee_product_monthly.py")

CAPTURED = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)


def _row(brand="apice", ref_month=date(2026, 9, 1), sku="KIT001",
         gmv=100.0, units=2, orders=2, parcial=False):
    return mod.ProdutoRow(
        ref_month=ref_month, brand=brand, sku_ref=sku, sku_ref_key=sku.upper(),
        product_name=f"Produto {sku}", variation_name=None,
        gmv=gmv, units_sold=units, completed_orders=orders,
        avg_price=round(gmv / units, 2) if units else None,
        is_partial=parcial,
    )


def _snap(rows, marcas=("apice",), meses=(date(2026, 9, 1),)):
    return mod.Snapshot(captured_at=CAPTURED, ref_months=list(meses),
                        marcas=tuple(marcas), mes_corrente=date(2026, 9, 1),
                        rows=list(rows))


# --------------------------------------------------------------------------- #
# Competencias — a janela que torna o passado recalculavel                      #
# --------------------------------------------------------------------------- #

def test_competencias_devolve_a_janela_terminando_no_mes_corrente():
    assert mod.competencias(date(2026, 9, 25), 3) == [
        date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]


def test_competencias_atravessa_a_virada_do_ano():
    assert mod.competencias(date(2026, 1, 15), 3) == [
        date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1)]


def test_competencias_recusa_janela_vazia():
    with pytest.raises(mod.ShopeeProdutosSyncError):
        mod.competencias(date(2026, 9, 25), 0)


def test_janela_padrao_inclui_mais_de_um_mes():
    """Publicar so' o mes corrente congelaria cada mes no valor imaturo que ele
    tinha ao virar — que e' o defeito do export que este publisher substitui."""
    assert mod.JANELA_MESES >= 2


# --------------------------------------------------------------------------- #
# Procedencia — o que vai para o banco                                          #
# --------------------------------------------------------------------------- #

def test_linha_grava_procedencia_api_e_instante_de_captura():
    linha = mod._linha(_row(), "run123", CAPTURED)
    d = dict(zip(mod._COLS, linha))
    assert d["source"] == mod.SOURCE_API
    assert d["source_run_id"] == "run123"
    assert d["source_captured_at"] == CAPTURED


def test_ausencia_e_null_e_nunca_zero():
    """A API nao mede cancelamento nem comprador unico. Zero afirmaria
    'nenhum cancelado', que nao foi medido."""
    d = dict(zip(mod._COLS, mod._linha(_row(), "r", CAPTURED)))
    for col in ("canceled_orders", "cancel_rate_pct", "unique_buyers"):
        assert d[col] is None, f"{col} deveria ser NULL, veio {d[col]!r}"


def test_mes_corrente_e_marcado_parcial_e_mes_fechado_nao():
    corrente = dict(zip(mod._COLS, mod._linha(
        _row(ref_month=date(2026, 9, 1), parcial=True), "r", CAPTURED)))
    fechado = dict(zip(mod._COLS, mod._linha(
        _row(ref_month=date(2026, 7, 1), parcial=False), "r", CAPTURED)))
    assert corrente["is_partial"] is True
    assert fechado["is_partial"] is False


def test_sku_ref_key_normaliza_caixa():
    """No export `Kit112` e `KIT112` convivem como dois produtos. A chave do
    destino nao pode herdar isso."""
    snap_rows = mod.ProdutoRow(
        ref_month=date(2026, 9, 1), brand="barbours", sku_ref="Kit112",
        sku_ref_key="Kit112".upper(), product_name="x", variation_name=None,
        gmv=1.0, units_sold=1, completed_orders=1, avg_price=1.0,
        is_partial=False)
    assert snap_rows.sku_ref_key == "KIT112"


# --------------------------------------------------------------------------- #
# Contrato da fonte                                                             #
# --------------------------------------------------------------------------- #

def test_fonte_vazia_levanta_em_vez_de_apagar_a_janela():
    with pytest.raises(mod.ShopeeProdutosSyncError, match="fonte vazia"):
        mod.validate_contract(_snap([]))


def test_chave_duplicada_e_recusada_antes_do_banco():
    r = _row()
    with pytest.raises(mod.ShopeeProdutosSyncError, match="duplicada"):
        mod.validate_contract(_snap([r, r]))


def test_faturamento_concluido_com_zero_pedidos_concluidos_e_recusado():
    with pytest.raises(mod.ShopeeProdutosSyncError, match="zero pedidos"):
        mod.validate_contract(_snap([_row(orders=0)]))


def test_conta_ausente_vira_aviso_e_nao_erro():
    avisos = mod.validate_contract(
        _snap([_row(brand="apice")], marcas=("apice", "barbours")))
    assert any("barbours" in a for a in avisos)


# --------------------------------------------------------------------------- #
# Allowlist — fail-closed                                                       #
# --------------------------------------------------------------------------- #

def test_kokeshi_nao_esta_na_allowlist_da_api():
    """Ela nao tem aplicacao Shopee cadastrada: o app e' registrado por loja,
    no console da empresa dona."""
    assert "kokeshi" not in mod.CONTAS_API


def test_allowlist_e_exatamente_as_quatro_contas_medidas():
    assert set(mod.CONTAS_API) == {"apice", "barbours", "lescent", "rituaria"}


def test_marca_fora_da_allowlist_e_recusada_antes_de_qualquer_leitura():
    with pytest.raises(mod.ShopeeProdutosSyncError, match="fora da allowlist"):
        mod.run(apply=False, marcas=("kokeshi",))


# --------------------------------------------------------------------------- #
# Proibicoes no codigo                                                          #
# --------------------------------------------------------------------------- #

def _codigo_sem_texto(fonte: str) -> str:
    """Remove docstrings: um termo citado em texto explicativo nao reprova."""
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)) and ast.get_docstring(no):
            no.body = no.body[1:]
    return ast.unparse(arvore)


def test_o_publisher_nunca_le_a_coluna_is_sale():
    """O recorte publicado e' `completed`. Ler `is_sale` aqui trocaria a
    definicao da tela em silencio, inflando o mes corrente em 21-29%."""
    codigo = _codigo_sem_texto(Path(mod.__file__).read_text(encoding="utf-8"))
    assert "is_sale" not in codigo


def test_o_delete_e_escopado_por_procedencia():
    """Sem o escopo, a publicacao apagaria as linhas do export — e o rollback
    deixaria de ser um UPDATE no interruptor."""
    codigo = Path(mod.__file__).read_text(encoding="utf-8")
    delete = re.search(r"DELETE FROM \{FACT\}(.+?)\"\"\"", codigo, re.S)
    assert delete is not None
    assert "source = :src" in delete.group(1)


def test_a_fonte_lida_e_o_recorte_concluido():
    sql = str(mod.SQL_GOLD)
    for coluna in ("gmv_completed", "units_completed", "orders_completed"):
        assert coluna in sql


# --------------------------------------------------------------------------- #
# Migration                                                                     #
# --------------------------------------------------------------------------- #

def test_migration_encadeia_na_021():
    fonte = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "022"' in fonte
    assert 'down_revision = "021"' in fonte


def test_migration_remove_o_default_de_source():
    """Com DEFAULT, um INSERT que esquecesse a coluna herdaria
    `manual_export` — uma linha da API rotulada como export e' pior do que um
    INSERT que falha."""
    fonte = MIGRATION.read_text(encoding="utf-8")
    assert "ALTER COLUMN source DROP DEFAULT" in fonte


def test_migration_nasce_com_todas_as_marcas_em_manual():
    """A migration nao liga nada: ligar e' decisao operacional, uma marca por
    vez, depois da reconciliacao."""
    fonte = MIGRATION.read_text(encoding="utf-8")
    trecho = fonte[fonte.index("INSERT INTO marts.shopee_product_source_mode"):]
    trecho = trecho[:trecho.index("ON CONFLICT")]
    assert "'api'" not in trecho
    for marca in ("apice", "barbours", "lescent", "rituaria", "kokeshi"):
        assert marca in trecho


def test_migration_exige_captura_em_linha_de_api():
    fonte = MIGRATION.read_text(encoding="utf-8")
    assert "ck_shopee_prod_api_tem_captura" in fonte
    assert "source <> 'api' OR source_captured_at IS NOT NULL" in fonte


# --------------------------------------------------------------------------- #
# Frescor da fonte — falha FECHADA                                              #
# --------------------------------------------------------------------------- #

class _ConnFrescor:
    """Dublê mínimo: `execute(...).scalar()` devolve o dia configurado."""

    def __init__(self, ultimo):
        self._ultimo = ultimo

    def execute(self, *_a, **_k):
        return self

    def scalar(self):
        return self._ultimo


def test_gold_vazio_levanta_em_vez_de_publicar():
    with pytest.raises(mod.ShopeeProdutosSyncError, match="VAZIA"):
        mod.validate_frescor(_ConnFrescor(None), date(2026, 9, 25))


def test_gold_velho_levanta():
    """Publicar um gold parado reescreve a competência corrente com dado velho
    — indistinguível de 'vendeu menos'."""
    velho = date(2026, 9, 25) - timedelta(days=mod.FRESCOR_MAX_DIAS + 1)
    with pytest.raises(mod.ShopeeProdutosSyncError, match="VELHA"):
        mod.validate_frescor(_ConnFrescor(velho), date(2026, 9, 25))


def test_gold_no_limite_do_frescor_passa():
    no_limite = date(2026, 9, 25) - timedelta(days=mod.FRESCOR_MAX_DIAS)
    assert mod.validate_frescor(_ConnFrescor(no_limite),
                                date(2026, 9, 25)) == no_limite


def test_frescor_e_checado_antes_de_ler_a_fonte():
    """Fonte velha tem de levantar ANTES de qualquer leitura de dado, e muito
    antes de abrir a transação do destino."""
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    corpo = fonte[fonte.index("def run("):]
    assert corpo.index("validate_frescor") < corpo.index("read_source")


# --------------------------------------------------------------------------- #
# NO-OP — upstream que não avançou, e retry seguro                              #
# --------------------------------------------------------------------------- #

class _ConnConteudo:
    """Dublê que devolve linhas no formato de `.mappings().all()`."""

    def __init__(self, linhas):
        self._linhas = linhas

    def execute(self, *_a, **_k):
        return self

    def mappings(self):
        return self

    def all(self):
        return self._linhas


def _publicado_de(rows):
    return [{"ref_month": r.ref_month, "brand": r.brand,
             "sku_ref_key": r.sku_ref_key, "product_name": r.product_name,
             "gmv": r.gmv, "units_sold": r.units_sold,
             "completed_orders": r.completed_orders,
             "is_partial": r.is_partial} for r in rows]


def test_conteudo_identico_nao_conta_como_avanco():
    rows = [_row(sku="A"), _row(sku="B")]
    snap = _snap(rows)
    conn = _ConnConteudo(_publicado_de(rows))
    assert mod.upstream_avancou(conn, snap) is False


def test_gmv_diferente_conta_como_avanco():
    rows = [_row(sku="A", gmv=100.0)]
    snap = _snap(rows)
    conn = _ConnConteudo(_publicado_de([_row(sku="A", gmv=101.0)]))
    assert mod.upstream_avancou(conn, snap) is True


def test_destino_vazio_conta_como_avanco():
    assert mod.upstream_avancou(_ConnConteudo([]), _snap([_row()])) is True


def test_linha_a_mais_no_destino_conta_como_avanco():
    """SKU que saiu da fonte precisa provocar republicação, senão ele ficaria
    publicado para sempre."""
    snap = _snap([_row(sku="A")])
    conn = _ConnConteudo(_publicado_de([_row(sku="A"), _row(sku="B")]))
    assert mod.upstream_avancou(conn, snap) is True


def test_comparacao_ignora_run_id_e_captura():
    """Incluí-los faria a equivalência nunca dar igual — é o bug clássico de
    fingerprint, e mataria tanto o NO-OP quanto o retry seguro."""
    for col in ("source_run_id", "source_captured_at"):
        assert col not in mod._COLS_CONTEUDO


def test_noop_e_verificado_dentro_do_lock():
    """Checar fora do lock seria corrida: outra execução poderia publicar
    entre a checagem e o DELETE."""
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    corpo = fonte[fonte.index("def _publicar("):]
    assert corpo.index("pg_try_advisory_xact_lock") < corpo.index("upstream_avancou")
