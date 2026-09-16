"""Gate PMA-2C1A-R fase 9 — a API NUNCA toca o Data Mart nem os adaptadores.

Estes testes sao ESTRUTURAIS: nao verificam o que a resposta diz, e sim o que o
codigo de serving e' capaz de alcancar. Um teste de payload passaria mesmo se a
API abrisse uma conexao com o Data Mart e engolisse a falha; estes nao.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.services import monitoramento_preco_service as mp
from app.services import pma_domain as dom
from app.services import pma_match as pm

#: Modulos que compoem o caminho de request do monitoramento de precos.
MODULOS_DE_SERVING = (mp, pm, dom)

#: Schemas que a API nao pode consultar. `marts.*` no Neon e' o unico permitido.
SCHEMAS_PROIBIDOS_NO_SERVING = ("gold.", "silver.", "raw.", "audit.")


def _fonte(modulo) -> str:
    return pathlib.Path(modulo.__file__).read_text(encoding="utf-8")


def _codigo_executavel(modulo) -> str:
    """Fonte sem docstrings: os comentarios explicam o Data Mart de proposito."""
    caminho = pathlib.Path(modulo.__file__)
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    linhas_doc: set[int] = set()
    for no in ast.walk(arvore):
        corpo = getattr(no, "body", None)
        if not isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)) or not corpo:
            continue
        primeiro = corpo[0]
        if (isinstance(primeiro, ast.Expr)
                and isinstance(primeiro.value, ast.Constant)
                and isinstance(primeiro.value.value, str)):
            linhas_doc.update(
                range(primeiro.lineno, (primeiro.end_lineno or primeiro.lineno) + 1))
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    return "\n".join(
        linha for n, linha in enumerate(linhas, 1)
        if n not in linhas_doc and not linha.strip().startswith("#")
    )


@pytest.mark.parametrize("modulo", MODULOS_DE_SERVING, ids=lambda m: m.__name__)
def test_serving_nao_consulta_schema_do_data_mart(modulo):
    codigo = _codigo_executavel(modulo)
    for schema in SCHEMAS_PROIBIDOS_NO_SERVING:
        assert schema not in codigo, (modulo.__name__, schema)


@pytest.mark.parametrize("modulo", MODULOS_DE_SERVING, ids=lambda m: m.__name__)
def test_serving_nao_importa_o_sync_nem_seus_adaptadores(modulo):
    """Importar `channel_offer_sync` daria a API acesso ao Data Mart."""
    arvore = ast.parse(_fonte(modulo))
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module)
    for proibido in ("pipelines", "pipelines.channel_offer_sync", "psycopg2"):
        assert not any(i == proibido or i.startswith(proibido + ".")
                       for i in importados), (modulo.__name__, proibido)


@pytest.mark.parametrize("modulo", MODULOS_DE_SERVING, ids=lambda m: m.__name__)
def test_serving_nao_menciona_DATAMART_DATABASE_URL(modulo):
    assert "DATAMART" not in _codigo_executavel(modulo).upper()


def test_a_varredura_enxerga_o_codigo_e_nao_passa_por_vacuidade():
    """Contraprova do filtro: ele nao pode esvaziar o arquivo."""
    codigo = _codigo_executavel(mp)
    assert "def get_monitoramento_preco" in codigo
    assert "marts.fact_marketplace_listing_price_daily" in codigo
    assert "gold." not in codigo


def test_a_unica_tabela_de_listing_do_serving_e_a_do_ml():
    assert mp.LISTING_TABLE == "marts.fact_marketplace_listing_price_daily"
    assert mp.LISTING_TABLE.startswith("marts.")
    assert mp.REFERENCE_TABLE.startswith("marts.")


def test_o_sync_e_que_le_o_data_mart_e_nao_e_alcancavel_pela_api():
    """O adaptador existe — mas do lado do sync, nao do serving."""
    from pipelines import channel_offer_sync as cos
    assert "silver.stg_shopee_products" in cos.SQL_SHOPEE_SIMPLE_PARENTS
    assert "gold.map_produto_codigo_gobeauty" in cos.SQL_INTERNAL_PRODUCT_MAP
    # e nenhum modulo de serving consegue chegar nele
    for modulo in MODULOS_DE_SERVING:
        assert "channel_offer_sync" not in _codigo_executavel(modulo)


def test_a_api_le_somente_a_futura_fato_no_neon():
    """Quando a Shopee/TikTok forem ligadas, a leitura sera' de `marts.*`."""
    from pipelines import channel_offer_sync as cos
    assert cos.TARGET_TABLE.startswith("marts.")
    assert cos.TARGET_TABLE == "marts.fact_channel_offer_observation"
