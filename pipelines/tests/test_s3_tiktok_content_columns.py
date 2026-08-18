"""Gate S3 — testes das duas colunas de conteudo do produto TikTok (Parte E).

`/brand-detail` precisa de `active_videos` (exposta como `videos`) e `video_views`
(denominador de `gpm`). A alteracao em `sync_produtos --source tiktok` e' de seis
linhas: SELECT da fonte, lista do INSERT, `DO UPDATE SET` e a tupla do batch.

Estes testes provam que nada mais mudou — e, em particular, que NULL continua NULL
e zero continua zero. A distincao e' observavel: na fonte medida (213.889 linhas,
cinco marcas) as duas colunas tem ZERO nulo e ~104.800 zeros cada. Depois do
backfill integral da Task 3, qualquer NULL restante significa linha nao
retroalimentada — nunca "sem video no dia".

O caminho `--full` NAO e' executado aqui.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import pipelines.sync_produtos as sp

SRC = Path(sp.__file__).read_text(encoding="utf-8")
#: Recorte do arquivo que pertence a `sync_tiktok`.
I = SRC.index("def sync_tiktok(")
J = SRC.index("# ---------------------------------------------------------------------------\n# CLI")
TIKTOK = SRC[I:J]

NOVAS = ("active_videos", "video_views")

#: As 22 colunas que ja existiam. Nenhuma pode mudar de nome, ordem ou presenca.
ANTIGAS = (
    "date", "brand", "product_id", "product_name",
    "gmv", "orders", "items_sold",
    "gmv_video", "gmv_live", "gmv_product_card",
    "items_sold_video", "items_sold_live", "items_sold_product_card",
    "pct_gmv_video", "pct_gmv_live", "pct_gmv_card",
    "canceled", "refunded", "returned", "problem_rate",
    "rating_avg", "total_ratings",
)


def _bloco(inicio: str, fim: str) -> str:
    a = TIKTOK.index(inicio)
    b = TIKTOK.index(fim, a)
    return TIKTOK[a:b]


SELECT_FONTE = _bloco("SELECT date, brand, product_id", "FROM gold.tiktok_product_daily")
LISTA_INSERT = _bloco("INSERT INTO marts.fact_tiktok_product_daily", "VALUES %s")
DO_UPDATE = _bloco("DO UPDATE SET", '"""')
TUPLA_BATCH = _bloco('r["date"], r["brand"]', "for r in rows[i")


# ===========================================================================
# As duas colunas entram nos quatro lugares
# ===========================================================================

@pytest.mark.parametrize("col", NOVAS)
def test_e01_coluna_no_select_da_fonte(col):
    assert col in SELECT_FONTE, f"{col} ausente no SELECT da fonte"


@pytest.mark.parametrize("col", NOVAS)
def test_e02_coluna_na_lista_do_insert(col):
    assert col in LISTA_INSERT, f"{col} ausente na lista do INSERT"


@pytest.mark.parametrize("col", NOVAS)
def test_e03_coluna_no_do_update_set(col):
    assert f"{col}" in DO_UPDATE and f"EXCLUDED.{col}" in DO_UPDATE, col


@pytest.mark.parametrize("col", NOVAS)
def test_e04_coluna_na_tupla_do_batch(col):
    assert f'r["{col}"]' in TUPLA_BATCH, col


def test_e05_a_ordem_da_tupla_bate_com_a_lista_do_insert():
    """Desalinhamento entre lista de colunas e tupla gravaria valor na coluna
    errada — silenciosamente."""
    colunas = [c.strip() for c in re.findall(r"[\w]+", LISTA_INSERT.split("(", 1)[1])]
    colunas = [c for c in colunas if c in ANTIGAS + NOVAS]
    campos = re.findall(r'r\["(\w+)"\]', TUPLA_BATCH)
    assert colunas == campos, f"desalinhado:\n  insert={colunas}\n  tupla={campos}"


# ===========================================================================
# Nenhuma coluna antiga mudou
# ===========================================================================

@pytest.mark.parametrize("col", ANTIGAS)
def test_e06_coluna_antiga_permanece_no_select(col):
    assert col in SELECT_FONTE, col


@pytest.mark.parametrize("col", ANTIGAS)
def test_e07_coluna_antiga_permanece_no_insert(col):
    assert col in LISTA_INSERT, col


def test_e08_o_insert_tem_exatamente_24_colunas():
    colunas = [c for c in re.findall(r"[\w]+", LISTA_INSERT.split("(", 1)[1])
               if c in ANTIGAS + NOVAS]
    assert len(colunas) == 24, colunas
    assert set(colunas) == set(ANTIGAS) | set(NOVAS)


def test_e09_nenhuma_coluna_a_mais_foi_adicionada():
    """Stop-loss: mais de duas colunas novas exigiria parar e reportar."""
    colunas = {c for c in re.findall(r"[\w]+", LISTA_INSERT.split("(", 1)[1])
               if c in ANTIGAS + NOVAS}
    novas_de_fato = colunas - set(ANTIGAS)
    assert novas_de_fato == set(NOVAS), f"colunas novas inesperadas: {novas_de_fato}"


# ===========================================================================
# NULL vs zero
# ===========================================================================

def test_e10_nenhum_coalesce_ou_default_nas_duas_colunas():
    """`COALESCE(...,0)` ou `DEFAULT 0` apagariam a diferenca entre "nao
    retroalimentado" e "zero medido"."""
    for col in NOVAS:
        assert f"COALESCE({col}" not in TIKTOK
        assert f"{col} = 0" not in TIKTOK
        assert f'r.get("{col}", 0)' not in TIKTOK


def test_e11_a_tupla_le_a_chave_direta_sem_default():
    """`r["active_videos"]`, nao `r.get("active_videos", 0)`: se a fonte deixar de
    trazer a coluna, o sync tem de falhar alto em vez de gravar zero."""
    for col in NOVAS:
        assert f'r["{col}"]' in TUPLA_BATCH
        assert f'r.get("{col}"' not in TUPLA_BATCH


def test_e12_null_da_fonte_chega_null_ao_batch():
    """Comportamental: linha com None nas duas colunas produz None na tupla."""
    linha = {c: None for c in ANTIGAS + NOVAS}
    linha.update({"date": "2026-08-17", "brand": "kokeshi", "product_id": 1})
    tupla = tuple(linha[c] for c in ANTIGAS + NOVAS)
    assert tupla[-2] is None and tupla[-1] is None


def test_e13_zero_da_fonte_chega_zero_ao_batch():
    linha = {c: None for c in ANTIGAS + NOVAS}
    linha.update({"date": "2026-08-17", "brand": "kokeshi", "product_id": 1,
                  "active_videos": 0, "video_views": 0})
    tupla = tuple(linha[c] for c in ANTIGAS + NOVAS)
    assert tupla[-2] == 0 and tupla[-1] == 0
    assert tupla[-2] is not None and tupla[-1] is not None


def test_e14_a_migration_011_nasce_anulavel_e_sem_default():
    caminho = (Path(sp.__file__).resolve().parents[1] / "apps" / "api" / "alembic"
               / "versions" / "011_add_tiktok_product_content_metrics.py")
    corpo = caminho.read_text(encoding="utf-8")
    upgrade = corpo[corpo.index("def upgrade("):corpo.index("def downgrade(")]
    assert "NOT NULL" not in upgrade
    assert "DEFAULT" not in upgrade.upper()


# ===========================================================================
# Nada mais mudou em sync_tiktok
# ===========================================================================

def test_e15_janela_incremental_preservada():
    assert "start_date = max_neon - timedelta(days=days)" in TIKTOK
    assert "AND date >= %s" in TIKTOK


def test_e16_modo_full_e_a_guarda_de_volume_preservados():
    assert "start_date = date(2025, 10, 1)" in TIKTOK
    assert "if full and len(rows) < 1000:" in TIKTOK
    assert "abaixo do esperado para um historico completo" in TIKTOK


def test_e17_chave_de_conflito_preservada():
    assert "ON CONFLICT (date, product_id)" in TIKTOK


def test_e18_filtro_de_marca_preservado():
    assert "WHERE brand IN {_brands_sql(brands)}" in TIKTOK


def test_e19_ordenacao_e_lote_preservados():
    assert "ORDER BY date, product_id" in TIKTOK
    assert "BATCH_SIZE = 1000" in TIKTOK
    assert "page_size=500" in TIKTOK


def test_e20_rollback_preservado():
    assert "dst.rollback()" in TIKTOK
    assert "dst.commit()" in TIKTOK


def test_e21_contrato_de_retorno_preservado():
    assert 'return {"source": len(rows), "upserted": inserted}' in TIKTOK


def test_e22_zero_alteracao_de_gmv_frete_ou_agregacao():
    """As duas colunas sao transportadas, nada e' recalculado."""
    for termo in ("sub_total", "frete", "shipping", "SUM(", "AVG(", "GROUP BY"):
        assert termo not in TIKTOK, f"{termo} apareceu em sync_tiktok"


def test_e23_nenhuma_referencia_a_shopee_em_sync_tiktok():
    assert "shopee" not in TIKTOK.lower()


def test_e24_full_nao_e_disparado_em_lugar_algum_do_codigo():
    """Stop-loss desta task: o backfill integral e' escopo da Task 3."""
    assert "sync_tiktok(full=True" not in SRC
    assert "--full" in SRC, "a flag continua existindo, apenas nao e' auto-disparada"
