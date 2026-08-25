"""Gate V3-BE / BE6 — `opportunity_map`, escopo de marca e frescor.

Por que este arquivo existe
---------------------------
`test_inteligencia_contract.py` congela o payload campo a campo. Aqui o alvo e'
diferente: a LOGICA de classificacao, os invariantes de contagem e o contrato do
escopo de marca. A maior parte roda sobre `_monta_opportunity_map`, que e' funcao
pura — nenhuma dessas assercoes depende de banco, e nenhuma congela contagem
viva da fotografia.

O que o BE6 nao pode fazer, e cada regra tem um teste:
  - usar `urgent U scale U organic` como universo;
  - usar `product_status` como proxy de volume;
  - inventar quadrante quando nao existe eixo de GMV;
  - confundir `ad_roas = 0` com indisponibilidade;
  - deixar cair uma classe do payload por estar vazia;
  - deixar o LIMIT dos destaques influenciar os agregados;
  - herdar a mediana global quando ha filtro de marca;
  - deixar string do usuario chegar ao SQL.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services import gold_service as gs

HOJE = date(2026, 8, 19)
QUADRANTES = ("escalar", "testar_investimento", "monitorar", "reduzir_parar")
FAIXAS = ("sem_ads", "roas_indisponivel_com_investimento")


def _classifica(sql: str) -> str:
    s = " ".join(sql.split()).lower()
    if "percentile_disc" in s and "row_number" in s:
        return "opp_destaques"
    if "percentile_disc" in s:
        return "opp_agregados"
    if "total_buyers" in s and "repeat_buyers" in s:
        return "ltv"
    if "pct_gmv_video" in s:
        return "tk_products"
    if "group by product_status" in s:
        return "signals"
    if "group by brand, pareto_bucket" in s:
        return "pareto"
    if "product_status = 'ad_spend_no_sales'" in s:
        return "urgent"
    if "product_status = 'sells+advertised'" in s:
        return "scale"
    if "product_status = 'sells_organic_only'" in s:
        return "organic"
    raise AssertionError(f"consulta inesperada: {s[:140]}")


@pytest.fixture
def fixar(monkeypatch):
    dados: dict[str, list[dict]] = {
        "signals": [], "urgent": [], "scale": [], "organic": [], "pareto": [],
        "ltv": [], "tk_products": [], "opp_agregados": [], "opp_destaques": [],
    }
    vistas: list[tuple[str, str]] = []

    def fake_query(db, sql):
        tipo = _classifica(sql)
        vistas.append((tipo, " ".join(sql.split())))
        return dados[tipo]

    class _DataFixa(date):
        @classmethod
        def today(cls):
            return HOJE

    monkeypatch.setattr(gs, "date", _DataFixa)
    monkeypatch.setattr(gs, "_hoje_operacional", lambda *a, **k: HOJE)
    monkeypatch.setattr(gs, "_query", fake_query)
    return dados, vistas


def _sql_de(vistas, tipo: str) -> str:
    for t, sql in vistas:
        if t == tipo:
            return sql
    raise AssertionError(f"consulta {tipo!r} nao executada")


def _agregado(classe, count, gmv=0.0, ad_spend=0.0, gmv_ref=100, basis=5):
    return {"classe": classe, "count": count, "gmv": gmv, "ad_spend": ad_spend,
            "gmv_reference": gmv_ref, "basis_count": basis}


def _destaque(classe, item_id="MLB1", brand="barbours", gmv=1.0, ad_spend=1.0, ad_roas=1.0):
    return {"classe": classe, "item_id": item_id, "brand": brand,
            "title": "produto", "gmv": gmv, "ad_spend": ad_spend, "ad_roas": ad_roas}


# ===========================================================================
# Escopo de marca — derivacao canonica pela allowlist
# ===========================================================================

def test_o01_sem_filtro_devolve_as_quatro_marcas_ml():
    assert gs.resolve_ml_scope(None) == ("barbours", "kokeshi", "lescent", "rituaria")
    assert gs.resolve_ml_scope(None) == gs.ML_BRANDS


def test_o02_subconjunto_valido_devolve_somente_essas_marcas():
    assert gs.resolve_ml_scope(["kokeshi"]) == ("kokeshi",)
    assert gs.resolve_ml_scope(["kokeshi", "rituaria"]) == ("kokeshi", "rituaria")


def test_o03_ordem_e_repeticao_do_usuario_nao_mudam_a_saida():
    """Saida canonica e deterministica: sempre na ordem de `ML_BRANDS`."""
    esperado = ("barbours", "kokeshi")
    for entrada in (["kokeshi", "barbours"], ["barbours", "kokeshi"],
                    ["kokeshi", "kokeshi", "barbours"], ["BARBOURS", "Kokeshi"]):
        assert gs.resolve_ml_scope(entrada) == esperado, entrada


def test_o04_apice_nao_pertence_ao_universo_ml():
    """Apice e' marca valida do grupo, mas nao vende no Mercado Livre: sai do
    escopo ML em vez de virar filtro vazio silencioso."""
    assert "apice" not in gs.ML_BRANDS
    assert "apice" in gs.BRANDS_IN_SCOPE
    assert gs.resolve_ml_scope(["apice"]) == ()


def test_o05_entrada_injetavel_e_descartada_antes_do_sql():
    for veneno in ("x'; DROP TABLE marts.fact_ml_produto_ranking; --",
                   "') OR 1=1 --", "barbours' UNION SELECT", ""):
        assert gs.resolve_ml_scope([veneno]) == ()


def test_o06_escopo_vazio_nao_monta_in_vazio_e_nao_fabrica_dado(fixar):
    """Somente Apice: `ml_scope_brands` vazio, universo ML vazio, nenhuma
    consulta ML emitida e nenhum `IN ()` invalido."""
    _, vistas = fixar
    out = gs.get_inteligencia(object(), ml_brands=["apice"])
    assert out["ml_scope_brands"] == []
    assert out["signals"] == [] and out["urgent"] == [] and out["scale"] == []
    assert out["organic"] == [] and out["pareto"] == [] and out["ltv"] == []
    assert out["urgent_total_count"] == 0
    assert out["scale_total_count"] == 0
    assert out["organic_total_count"] == 0
    assert out["ml_snapshot_refreshed_at"] is None
    assert out["opportunity_map"]["classification_status"] == "empty"
    assert out["opportunity_map"]["brands"] == []
    # so' a consulta do TikTok roda, e nenhuma clausula ficou degenerada
    assert [t for t, _ in vistas] == ["tk_products"]
    for _, sql in vistas:
        assert "IN ()" not in sql and "in ()" not in sql.lower()


def test_o07_escopo_alcanca_todos_os_blocos_ml(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object(), ml_brands=["kokeshi"])
    for tipo in ("signals", "urgent", "scale", "organic", "pareto", "ltv",
                 "opp_agregados", "opp_destaques"):
        sql = _sql_de(vistas, tipo)
        assert "'kokeshi'" in sql, f"{tipo} sem o escopo"
        for outra in ("barbours", "lescent", "rituaria"):
            assert f"'{outra}'" not in sql, f"{tipo} vazou {outra}"


def test_o08_tk_products_permanece_global_e_nao_e_filtrado_pelo_escopo_ml(fixar):
    """Contrato do V3-1A preservado: `tk_products` e' TikTok e nao muda de grao
    nem de escopo por causa do filtro ML."""
    _, vistas = fixar
    gs.get_inteligencia(object(), ml_brands=["kokeshi"])
    sql = _sql_de(vistas, "tk_products")
    for marca in gs.BRANDS_IN_SCOPE:
        assert f"'{marca}'" in sql, f"tk_products perdeu {marca}"


def test_o09_chamada_antiga_sem_parametro_continua_global(fixar):
    _, vistas = fixar
    out = gs.get_inteligencia(object())
    assert out["ml_scope_brands"] == list(gs.ML_BRANDS)
    for marca in gs.ML_BRANDS:
        assert f"'{marca}'" in _sql_de(vistas, "signals")


def test_o10_nenhuma_string_do_usuario_chega_ao_sql(fixar):
    """A unica coisa que chega ao SQL sao elementos da constante `ML_BRANDS`."""
    _, vistas = fixar
    gs.get_inteligencia(object(), ml_brands=["barbours", "apice", "nao-existe", "'; --"])
    for tipo, sql in vistas:
        assert "nao-existe" not in sql and "'; --" not in sql, tipo
        assert "apice" not in sql or tipo == "tk_products"


# ===========================================================================
# Precedencia da classificacao — os seis ramos
# ===========================================================================

def test_o11_os_seis_ramos_existem_no_sql_na_ordem_de_decisao(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = _sql_de(vistas, "opp_agregados").lower()
    ordem = [sql.index(k) for k in (
        "'sem_ads'", "'roas_indisponivel_com_investimento'", "'unclassified'",
        "'escalar'", "'testar_investimento'", "'monitorar'", "'reduzir_parar'")]
    assert ordem == sorted(ordem), "a precedencia do CASE mudou de ordem"


def test_o12_sem_ads_cobre_null_e_zero_e_nunca_se_mistura(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "opp_agregados").split()).lower()
    assert "u.ad_spend is null or u.ad_spend <= 0" in sql
    # o ramo de indisponibilidade vem DEPOIS e exige ad_spend > 0 por exclusao
    assert sql.index("ad_spend <= 0") < sql.index("u.ad_roas is null")


def test_o13_roas_null_com_investimento_nao_cai_em_sem_ads(fixar):
    """Contraprova: `ad_spend > 0` + `ad_roas IS NULL` tem faixa propria."""
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "opp_agregados").split()).lower()
    ramo = sql[sql.index("u.ad_roas is null"):]
    assert ramo.startswith("u.ad_roas is null then 'roas_indisponivel_com_investimento'")


def test_o14_roas_zero_e_valor_baixo_nao_indisponibilidade(fixar):
    """`ad_roas = 0` nao satisfaz `IS NULL`, entao cai nos quadrantes de retorno
    baixo — `monitorar` ou `reduzir_parar`, conforme o GMV."""
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "opp_agregados").split()).lower()
    assert "ad_roas = 0" not in sql, "zero nao pode ter ramo proprio de indisponibilidade"
    assert "u.ad_roas >= 8" in sql


def test_o15_fronteiras_altas_sao_inclusivas(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "opp_agregados").split()).lower()
    assert "u.ad_roas >= 8" in sql, "ROAS 8 tem de contar como alto"
    assert "u.gmv >= r.gmv_ref" in sql, "GMV na mediana tem de contar como alto"
    assert "u.ad_roas > 8" not in sql and "u.gmv > r.gmv_ref" not in sql


# ===========================================================================
# Universo e referencias
# ===========================================================================

def test_o16_universo_nao_e_a_uniao_das_tres_listas(fixar):
    """O universo e' o snapshot inteiro no escopo: nenhum filtro de
    `product_status` especifico e nenhum LIMIT participam dele."""
    _, vistas = fixar
    gs.get_inteligencia(object())
    for tipo in ("opp_agregados", "opp_destaques"):
        sql = " ".join(_sql_de(vistas, tipo).split()).lower()
        universo = sql[sql.index("with universo as"):sql.index(", ref as")]
        assert "product_status is not null" in universo
        for status in ("'ad_spend_no_sales'", "'sells+advertised'",
                       "'sells_organic_only'", "'inactive'"):
            assert status not in universo, f"{tipo}: universo restrito por {status}"
        assert "limit" not in universo


def test_o17_mediana_usa_percentile_disc_somente_sobre_gmv_positivo(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "opp_agregados").split()).lower()
    assert "percentile_disc(0.5) within group (order by gmv)" in sql
    assert "percentile_cont" not in sql, "CONT devolve float e desloca a fronteira"
    assert "avg(" not in sql.split("from classificado")[0], "media nao e mediana"
    ref = sql[sql.index(", ref as"):sql.index(", classificado as")]
    assert "where gmv > 0" in ref


def test_o18_product_status_nao_entra_na_classificacao(fixar):
    """`product_status` seleciona o universo, mas NAO decide quadrante: ele nao
    e' proxy de volume."""
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "opp_agregados").split()).lower()
    case = sql[sql.index("case"):sql.index("end as classe")]
    assert "product_status" not in case


def test_o19_agregados_nao_dependem_do_limite_dos_destaques(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    agregados = " ".join(_sql_de(vistas, "opp_agregados").split()).lower()
    destaques = " ".join(_sql_de(vistas, "opp_destaques").split()).lower()
    assert "limit" not in agregados and "row_number" not in agregados
    assert "row_number" in destaques and "rn <= 10" in destaques


def test_o20_destaques_vem_somente_dos_quatro_quadrantes(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "opp_destaques").split()).lower()
    for q in QUADRANTES:
        assert f"'{q}'" in sql
    for f in FAIXAS:
        recorte = sql[sql.index("where c.classe in ("):]
        assert f"'{f}'" not in recorte[:recorte.index(")")], f"faixa {f} entrou nos destaques"


def test_o21_ordenacao_dos_destaques_e_totalmente_determinada(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "opp_destaques").split()).lower()
    assert ("order by c.ad_spend desc, c.gmv desc, c.brand asc, c.item_id asc" in sql)


def test_o22_frescor_vem_de_refreshed_at_e_nunca_de_now_ou_last_sale(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = " ".join(_sql_de(vistas, "signals").split()).lower()
    assert "max(refreshed_at)" in sql
    assert "now()" not in sql and "current_timestamp" not in sql
    assert "last_sale" not in sql and "ingested_at" not in sql


def test_o23_be3_conta_antes_do_limit(fixar):
    """As tres contagens saem da consulta AGREGADA de `signals`, que nao tem
    LIMIT — nunca do array truncado."""
    dados, vistas = fixar
    dados["signals"] = [
        {"product_status": "ad_spend_no_sales", "n_products": 36, "gmv": 0,
         "ad_spend": 100, "avg_roas": None, "n_roas_ref": 0},
        {"product_status": "sells_organic_only", "n_products": 532, "gmv": 10,
         "ad_spend": 0, "avg_roas": None, "n_roas_ref": 0},
        {"product_status": "sells+advertised", "n_products": 860, "gmv": 99,
         "ad_spend": 9, "avg_roas": 12.0, "n_roas_ref": 672},
    ]
    dados["urgent"] = []
    out = gs.get_inteligencia(object())
    assert out["urgent_total_count"] == 36
    assert out["organic_total_count"] == 532
    # `scale` tem o corte extra `ad_roas >= 8`: 672, nao os 860 do status
    assert out["scale_total_count"] == 672
    assert out["scale_total_count"] != 860
    assert "limit" not in _sql_de(vistas, "signals").lower()
    assert out["urgent_total_count"] >= len(out["urgent"])


def test_o24_desempate_deterministico_nas_tres_listas(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    for tipo, chave in (("urgent", "ad_spend desc"), ("scale", "ad_roas desc"),
                        ("organic", "gross_revenue desc")):
        sql = " ".join(_sql_de(vistas, tipo).split()).lower()
        assert f"order by {chave}, brand asc, item_id asc" in sql, tipo
        # filtros e limites intactos
        assert "limit" in sql


# ===========================================================================
# Invariantes — funcao pura
# ===========================================================================

def test_o25_estado_disponivel_soma_quatro_quadrantes_mais_duas_faixas():
    agregados = [
        _agregado("escalar", 490, 34645249.16, 1290291.78),
        _agregado("testar_investimento", 188, 185034.41, 6425.32),
        _agregado("monitorar", 57, 918685.06, 24567.40),
        _agregado("reduzir_parar", 161, 82893.08, 3347.33),
        _agregado("sem_ads", 752, 3124892.65, 0.0),
    ]
    m = gs._monta_opportunity_map(gs.ML_BRANDS, agregados, [])
    assert m["classification_status"] == "available"
    assert m["unclassified_count"] == 0
    soma = sum(q["count"] for q in m["quadrants"]) + sum(b["count"] for b in m["bands"])
    assert soma == m["total_count"] == 1648


def test_o26_faixa_vazia_permanece_no_payload_com_zeros():
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS, [_agregado("escalar", 5, 100.0, 10.0)], [])
    faixas = {b["key"]: b for b in m["bands"]}
    assert list(faixas) == list(FAIXAS), "nenhuma faixa pode desaparecer"
    vazia = faixas["roas_indisponivel_com_investimento"]
    assert vazia == {"key": "roas_indisponivel_com_investimento",
                     "count": 0, "gmv": 0.0, "ad_spend": 0.0}


def test_o27_quadrantes_zerados_permanecem_no_payload():
    m = gs._monta_opportunity_map(gs.ML_BRANDS, [_agregado("sem_ads", 3)], [])
    assert [q["key"] for q in m["quadrants"]] == list(QUADRANTES)
    for q in m["quadrants"]:
        assert q["count"] == 0 and q["gmv"] == 0.0 and q["ad_spend"] == 0.0
        assert q["returned_count"] == 0


def test_o28_sem_gmv_positivo_nao_fabrica_quadrante():
    """Decisao aprovada: sem eixo de GMV a matriz e' declarada indisponivel e os
    produtos com Ads e ROAS numerico vao para `unclassified_count`."""
    agregados = [
        {"classe": "sem_ads", "count": 100, "gmv": 0.0, "ad_spend": 0.0,
         "gmv_reference": None, "basis_count": 0},
        {"classe": "unclassified", "count": 20, "gmv": 0.0, "ad_spend": 50.0,
         "gmv_reference": None, "basis_count": 0},
    ]
    m = gs._monta_opportunity_map(gs.ML_BRANDS, agregados, [])
    assert m["classification_status"] == "unavailable_no_positive_gmv"
    assert m["gmv_reference"] is None
    assert m["gmv_reference_basis_count"] == 0
    assert m["unclassified_count"] == 20
    assert all(q["count"] == 0 for q in m["quadrants"]), "quadrante fabricado"
    assert m["highlights"] == []
    faixas = {b["key"]: b["count"] for b in m["bands"]}
    # invariante do estado indisponivel
    assert faixas["sem_ads"] + faixas["roas_indisponivel_com_investimento"] \
        + m["unclassified_count"] == m["total_count"] == 120


def test_o29_universo_vazio_e_estado_empty():
    m = gs._monta_opportunity_map(gs.ML_BRANDS, [], [])
    assert m["classification_status"] == "empty"
    assert m["total_count"] == 0
    assert m["gmv_reference"] is None and m["gmv_reference_basis_count"] == 0
    assert m["unclassified_count"] == 0
    assert [q["key"] for q in m["quadrants"]] == list(QUADRANTES)
    assert [b["key"] for b in m["bands"]] == list(FAIXAS)
    assert m["highlights"] == []
    assert m["scope"] == "ml_snapshot"


def test_o30_returned_count_nunca_excede_count_e_respeita_o_limite():
    destaques = [_destaque("escalar", item_id=f"MLB{i}") for i in range(10)]
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS, [_agregado("escalar", 490, 1.0, 1.0)], destaques)
    q = {x["key"]: x for x in m["quadrants"]}["escalar"]
    assert q["count"] == 490
    assert q["returned_count"] == 10 == gs.HIGHLIGHT_LIMIT_PER_QUADRANT
    assert q["returned_count"] <= q["count"]
    for x in m["quadrants"]:
        assert x["returned_count"] <= x["count"]


def test_o31_destaque_declara_o_proprio_quadrante_e_nunca_vem_de_faixa():
    destaques = [_destaque("escalar"), _destaque("monitorar", item_id="MLB2"),
                 _destaque("sem_ads", item_id="MLB3")]
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS,
        [_agregado("escalar", 1, 1.0, 1.0), _agregado("monitorar", 1, 1.0, 1.0),
         _agregado("sem_ads", 1, 1.0, 0.0)],
        destaques)
    quadrantes_vistos = {h["quadrant"] for h in m["highlights"]}
    assert quadrantes_vistos == {"escalar", "monitorar"}
    assert all(h["quadrant"] in QUADRANTES for h in m["highlights"])
    assert "MLB3" not in [h["item_id"] for h in m["highlights"]], "faixa virou destaque"


def test_o32_destaque_traz_item_id_e_o_titulo_e_so_exibicao():
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS, [_agregado("escalar", 1, 1.0, 1.0)],
        [_destaque("escalar", item_id="MLB123")])
    h = m["highlights"][0]
    assert list(h) == ["item_id", "brand", "title", "gmv", "ad_spend", "ad_roas", "quadrant"]
    assert h["item_id"] == "MLB123"
    # nenhum valor monetario, percentual ou titulo compoe identificador
    assert isinstance(h["item_id"], str) and h["item_id"]


def test_o33_destaque_preserva_roas_zero_e_distingue_null():
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS, [_agregado("reduzir_parar", 2, 1.0, 1.0)],
        [_destaque("reduzir_parar", item_id="A", ad_roas=0),
         _destaque("reduzir_parar", item_id="B", ad_roas=None)])
    por_id = {h["item_id"]: h for h in m["highlights"]}
    assert por_id["A"]["ad_roas"] == 0
    assert por_id["B"]["ad_roas"] is None


def test_o34_referencias_sao_descritivas_e_declaradas():
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS, [_agregado("escalar", 1, 1.0, 1.0, gmv_ref=2207.05, basis=1392)], [])
    assert m["roas_reference"] == 8.0
    assert m["gmv_reference"] == 2207.05
    assert m["gmv_reference_basis_count"] == 1392
    # texto ACENTUADO desde o patch terminal de formatacao: a nota vai crua
    # para a interface pt-BR, e o frontend nao normaliza
    assert "não são metas" in m["reference_note"]
    assert "Referências descritivas do portfólio" in m["reference_note"]
    assert m["highlight_limit_per_quadrant"] == 10
    assert m["highlight_order"] == "ad_spend_desc_gmv_desc_brand_item"


def test_o35_escopo_do_mapa_e_inequivoco_e_espelha_o_escopo_ml():
    for escopo in ((), ("kokeshi",), gs.ML_BRANDS):
        m = gs._monta_opportunity_map(escopo, [], [])
        assert m["scope"] == "ml_snapshot"
        assert m["brands"] == list(escopo)


def test_o36_mapa_e_escopo_batem_no_payload(fixar):
    dados, _ = fixar
    dados["opp_agregados"] = [_agregado("escalar", 3, 30.0, 3.0)]
    out = gs.get_inteligencia(object(), ml_brands=["kokeshi", "barbours"])
    assert out["ml_scope_brands"] == ["barbours", "kokeshi"]
    assert out["opportunity_map"]["brands"] == ["barbours", "kokeshi"]


def test_o37_mediana_e_recalculada_dentro_do_escopo(fixar):
    """A CTE `ref` vive DENTRO da consulta filtrada: trocar o escopo troca a
    base da mediana. Nenhuma mediana global e' herdada."""
    _, vistas = fixar
    gs.get_inteligencia(object(), ml_brands=["lescent"])
    sql = " ".join(_sql_de(vistas, "opp_agregados").split()).lower()
    ref = sql[sql.index(", ref as"):sql.index(", classificado as")]
    assert "from universo" in ref
    universo = sql[sql.index("with universo as"):sql.index(", ref as")]
    assert "'lescent'" in universo and "'barbours'" not in universo


def test_o38_frescor_usa_o_maximo_real_e_sai_em_iso_utc(fixar):
    dados, _ = fixar
    dados["signals"] = [
        {"product_status": "a", "n_products": 1, "gmv": 0, "ad_spend": 0,
         "avg_roas": None, "n_roas_ref": 0,
         "max_refreshed_at": datetime(2026, 8, 19, 15, 20, 1, 557915, tzinfo=timezone.utc)},
        {"product_status": "b", "n_products": 1, "gmv": 0, "ad_spend": 0,
         "avg_roas": None, "n_roas_ref": 0,
         "max_refreshed_at": datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)},
    ]
    out = gs.get_inteligencia(object())
    # o maximo, nao o primeiro nem o menor; segundos inteiros, sufixo Z
    assert out["ml_snapshot_refreshed_at"] == "2026-08-19T15:20:01Z"


def test_o39_frescor_null_quando_a_fonte_nao_declara(fixar):
    dados, _ = fixar
    dados["signals"] = [{"product_status": "a", "n_products": 1, "gmv": 0,
                         "ad_spend": 0, "avg_roas": None, "n_roas_ref": 0,
                         "max_refreshed_at": None}]
    assert gs.get_inteligencia(object())["ml_snapshot_refreshed_at"] is None


def test_o40_nenhum_retry_ou_relogio_local_no_frescor(fixar):
    """Contraprova final do BE4: o valor vem do banco, nao do processo."""
    dados, _ = fixar
    dados["signals"] = [{"product_status": "a", "n_products": 1, "gmv": 0,
                         "ad_spend": 0, "avg_roas": None, "n_roas_ref": 0,
                         "max_refreshed_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}]
    out = gs.get_inteligencia(object())
    assert out["ml_snapshot_refreshed_at"] == "2020-01-01T00:00:00Z"
    assert not out["ml_snapshot_refreshed_at"].startswith("2026")


# ===========================================================================
# `returned_count` — os DOIS niveis (§15.1-D)
#
# `opportunity_map.returned_count`  = total de pontos de destaque retornados
# `quadrants[*].returned_count`     = destaques daquele quadrante
#
# Nenhum dos dois se confunde com `total_count` (universo) nem com
# `quadrants[*].count` (universo do quadrante). A regra de interface do
# §15.1-D compara `returned_count` com `total_count` para dizer que os pontos
# sao DESTAQUES, e nao o universo.
# ===========================================================================

def _invariantes_returned_count(m):
    """Os seis invariantes exigidos, num lugar so."""
    q = m["quadrants"]
    assert m["returned_count"] == len(m["highlights"])
    assert m["returned_count"] == sum(x["returned_count"] for x in q)
    for x in q:
        assert x["returned_count"] <= x["count"], x["key"]
    assert m["returned_count"] <= sum(x["count"] for x in q)
    assert all(h["quadrant"] in QUADRANTES for h in m["highlights"])
    for x in q:
        assert x["returned_count"] <= gs.HIGHLIGHT_LIMIT_PER_QUADRANT, x["key"]


def test_o41_returned_count_de_topo_existe_e_e_o_total_de_destaques():
    destaques = ([_destaque("escalar", item_id=f"E{i}") for i in range(4)]
                 + [_destaque("monitorar", item_id="M1")])
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS,
        [_agregado("escalar", 490, 1.0, 1.0), _agregado("monitorar", 56, 1.0, 1.0)],
        destaques)
    assert m["returned_count"] == 5
    assert m["returned_count"] != m["total_count"]
    _invariantes_returned_count(m)


def test_o42_returned_count_e_zero_no_mapa_vazio():
    m = gs._monta_opportunity_map(gs.ML_BRANDS, [], [])
    assert m["classification_status"] == "empty"
    assert m["returned_count"] == 0
    assert m["highlights"] == []
    _invariantes_returned_count(m)


def test_o43_returned_count_com_destaques_em_varios_quadrantes():
    destaques = ([_destaque("escalar", item_id=f"E{i}") for i in range(3)]
                 + [_destaque("testar_investimento", item_id=f"T{i}") for i in range(2)]
                 + [_destaque("monitorar", item_id="M1")]
                 + [_destaque("reduzir_parar", item_id=f"R{i}") for i in range(4)])
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS,
        [_agregado("escalar", 492, 1.0, 1.0), _agregado("testar_investimento", 188, 1.0, 1.0),
         _agregado("monitorar", 56, 1.0, 1.0), _agregado("reduzir_parar", 161, 1.0, 1.0)],
        destaques)
    por = {x["key"]: x["returned_count"] for x in m["quadrants"]}
    assert por == {"escalar": 3, "testar_investimento": 2, "monitorar": 1, "reduzir_parar": 4}
    assert m["returned_count"] == 10 == sum(por.values())
    _invariantes_returned_count(m)


def test_o44_returned_count_com_um_quadrante_truncado_no_limite():
    """`escalar` tem 490 no universo e 12 destaques oferecidos: o limite de 10
    corta, e o total de topo reflete o CORTE, nao a oferta."""
    destaques = ([_destaque("escalar", item_id=f"E{i}") for i in range(10)]
                 + [_destaque("monitorar", item_id="M1")])
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS,
        [_agregado("escalar", 490, 1.0, 1.0), _agregado("monitorar", 56, 1.0, 1.0)],
        destaques)
    por = {x["key"]: x for x in m["quadrants"]}
    assert por["escalar"]["returned_count"] == 10 == gs.HIGHLIGHT_LIMIT_PER_QUADRANT
    assert por["escalar"]["count"] == 490
    assert m["returned_count"] == 11
    assert m["returned_count"] < m["total_count"], "a regra de interface do §15.1-D"
    _invariantes_returned_count(m)


def test_o45_returned_count_zero_no_estado_sem_gmv_positivo():
    agregados = [
        {"classe": "sem_ads", "count": 100, "gmv": 0.0, "ad_spend": 0.0,
         "gmv_reference": None, "basis_count": 0},
        {"classe": "unclassified", "count": 20, "gmv": 0.0, "ad_spend": 50.0,
         "gmv_reference": None, "basis_count": 0},
    ]
    m = gs._monta_opportunity_map(gs.ML_BRANDS, agregados, [])
    assert m["classification_status"] == "unavailable_no_positive_gmv"
    assert m["returned_count"] == 0
    assert m["highlights"] == []
    _invariantes_returned_count(m)


def test_o46_faixas_com_produtos_mas_zero_destaque():
    """As faixas tem universo, mas nunca contribuem com destaque — logo o total
    de topo pode ser zero mesmo com `total_count` grande."""
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS,
        [_agregado("sem_ads", 753, 3124892.65, 0.0),
         _agregado("roas_indisponivel_com_investimento", 12, 100.0, 50.0)],
        [])
    faixas = {x["key"]: x["count"] for x in m["bands"]}
    assert faixas == {"sem_ads": 753, "roas_indisponivel_com_investimento": 12}
    assert m["total_count"] == 765
    assert m["returned_count"] == 0
    assert all(x["returned_count"] == 0 for x in m["quadrants"])
    _invariantes_returned_count(m)


def test_o47_destaque_de_faixa_e_ignorado_e_nao_conta_no_total():
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS,
        [_agregado("escalar", 5, 1.0, 1.0), _agregado("sem_ads", 9, 1.0, 0.0)],
        [_destaque("escalar", item_id="OK"), _destaque("sem_ads", item_id="NAO"),
         _destaque("roas_indisponivel_com_investimento", item_id="NAO2")])
    assert m["returned_count"] == 1
    assert [h["item_id"] for h in m["highlights"]] == ["OK"]
    _invariantes_returned_count(m)


def test_o48_os_dois_niveis_de_returned_count_sao_distintos_de_count():
    m = gs._monta_opportunity_map(
        gs.ML_BRANDS, [_agregado("escalar", 492, 1.0, 1.0)],
        [_destaque("escalar", item_id=f"E{i}") for i in range(10)])
    q = {x["key"]: x for x in m["quadrants"]}["escalar"]
    assert m["total_count"] == 492 and m["returned_count"] == 10
    assert q["count"] == 492 and q["returned_count"] == 10
    # os quatro numeros tem significados distintos e nao podem ser confundidos
    assert m["total_count"] != m["returned_count"]
    assert q["count"] != q["returned_count"]


def test_o49_ordem_das_chaves_do_mapa_com_os_dois_niveis(fixar):
    dados, _ = fixar
    dados["opp_agregados"] = [_agregado("escalar", 3, 30.0, 3.0)]
    m = gs.get_inteligencia(object())["opportunity_map"]
    assert list(m) == [
        "scope", "classification_status", "brands", "total_count", "returned_count",
        "roas_reference", "gmv_reference", "gmv_reference_basis_count",
        "reference_note", "unclassified_count", "highlight_limit_per_quadrant",
        "highlight_order", "quadrants", "bands", "highlights",
    ]
    for q in m["quadrants"]:
        assert list(q) == ["key", "count", "gmv", "ad_spend", "returned_count"]
    for b in m["bands"]:
        assert list(b) == ["key", "count", "gmv", "ad_spend"], "faixa nao tem returned_count"


def test_o50_agregados_seguem_independentes_do_limite_mesmo_com_truncamento():
    """O corte dos destaques nao pode encostar nos agregados."""
    poucos = gs._monta_opportunity_map(
        gs.ML_BRANDS, [_agregado("escalar", 492, 34645249.16, 1290291.78)], [])
    muitos = gs._monta_opportunity_map(
        gs.ML_BRANDS, [_agregado("escalar", 492, 34645249.16, 1290291.78)],
        [_destaque("escalar", item_id=f"E{i}") for i in range(10)])
    a = {x["key"]: (x["count"], x["gmv"], x["ad_spend"]) for x in poucos["quadrants"]}
    b = {x["key"]: (x["count"], x["gmv"], x["ad_spend"]) for x in muitos["quadrants"]}
    assert a == b, "os agregados mudaram por causa dos destaques"
    assert poucos["returned_count"] == 0 and muitos["returned_count"] == 10


# ===========================================================================
# Wiring REAL da rota HTTP — o que o cliente de fato observa
#
# Diferente dos testes acima, que exercitam o helper puro e o servico, estes
# passam pelo router com `TestClient`. Isso importa porque a rota valida o
# parametro com `resolve_brands`, o helper CANONICO compartilhado com as demais
# rotas: ele e case-sensitive e consulta `marts.dim_loja`. `resolve_ml_scope`
# e a SEGUNDA defesa, pura e allowlisted, e nao a primeira.
# ===========================================================================

class _ResultadoDimLoja:
    """Espelha `db.execute(...).scalars().all()` de `get_scope_brand_keys`."""

    def __init__(self, chaves):
        self._chaves = chaves

    def scalars(self):
        return self

    def all(self):
        return list(self._chaves)


class _SessaoFake:
    """Registra as consultas que a ROTA emite antes de chegar ao servico."""

    def __init__(self, chaves=gs.BRANDS_IN_SCOPE):
        self.consultas: list[str] = []
        self._chaves = chaves

    def execute(self, clausula, *a, **k):
        self.consultas.append(str(clausula))
        return _ResultadoDimLoja(self._chaves)


@pytest.fixture
def rota(monkeypatch):
    """TestClient com `get_db` substituido e `get_inteligencia` espionado."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app
    from app.routers import performance as router_mod

    sessao = _SessaoFake()
    recebido: dict = {}

    def fake_get_inteligencia(db, ml_brands=None):
        recebido["chamado"] = True
        recebido["ml_brands"] = ml_brands
        escopo = gs.resolve_ml_scope(ml_brands)
        return {
            "signals": [], "urgent": [], "scale": [], "organic": [], "pareto": [],
            "ltv": [], "tk_products": [{"brand": b, "product_name": "p", "gmv": 1.0,
                                        "orders": 1, "avg_pct_video": None,
                                        "avg_pct_live": None, "avg_pct_card": None,
                                        "avg_rating": None}
                                       for b in gs.BRANDS_IN_SCOPE],
            "urgent_total_count": 0, "scale_total_count": 0, "organic_total_count": 0,
            "ml_snapshot_refreshed_at": None, "ml_scope_brands": list(escopo),
            "opportunity_map": gs._empty_opportunity_map(escopo, "empty"),
        }

    monkeypatch.setattr(router_mod.svc, "get_inteligencia", fake_get_inteligencia)
    app.dependency_overrides[get_db] = lambda: sessao
    cliente = TestClient(app)
    yield cliente, sessao, recebido
    app.dependency_overrides.clear()


URL = "/api/v1/performance/inteligencia"


def test_o51_rota_sem_brands_encaminha_none_e_nao_consulta_dim_loja(rota):
    cliente, sessao, recebido = rota
    r = cliente.get(URL)
    assert r.status_code == 200
    assert recebido["ml_brands"] is None, "sem filtro o servico recebe None"
    assert sessao.consultas == [], "sem `brands` nao ha consulta de validacao"
    assert r.json()["ml_scope_brands"] == list(gs.ML_BRANDS)


def test_o52_rota_com_brands_encaminha_as_marcas_validadas(rota):
    cliente, sessao, recebido = rota
    r = cliente.get(URL, params={"brands": "barbours,kokeshi"})
    assert r.status_code == 200
    assert recebido["ml_brands"] == ["barbours", "kokeshi"]
    assert r.json()["ml_scope_brands"] == ["barbours", "kokeshi"]
    # a validacao custa UMA consulta read-only a `marts.dim_loja`
    assert len(sessao.consultas) == 1
    assert "dim_loja" in sessao.consultas[0]


def test_o53_rota_normaliza_ordem_e_repeticao_pelo_contrato_canonico(rota):
    """`resolve_brands` ordena e deduplica; `resolve_ml_scope` reordena para a
    ordem de `ML_BRANDS`. O resultado observavel e deterministico."""
    cliente, _, _ = rota
    for entrada in ("kokeshi,barbours", "barbours,kokeshi", "kokeshi,barbours,kokeshi"):
        r = cliente.get(URL, params={"brands": entrada})
        assert r.status_code == 200, entrada
        assert r.json()["ml_scope_brands"] == ["barbours", "kokeshi"], entrada


def test_o54_rota_e_case_sensitive_e_maiusculas_dao_422(rota):
    """Fato do contrato PUBLICO: `resolve_brands` compara com `marts.dim_loja`
    sem normalizar caixa, entao `BARBOURS` nao equivale a `barbours`. O helper
    puro `resolve_ml_scope` normaliza, mas ele e a segunda defesa — a rota
    rejeita antes. O contrato canonico do frontend e lowercase.
    """
    cliente, sessao, recebido = rota
    r = cliente.get(URL, params={"brands": "BARBOURS"})
    assert r.status_code == 422
    assert not recebido.get("chamado"), "o servico nao pode ser chamado"
    # e a rejeicao acontece DEPOIS do SELECT de validacao, nao antes
    assert len(sessao.consultas) == 1
    # contraprova: o helper puro, sozinho, normalizaria
    assert gs.resolve_ml_scope(["BARBOURS"]) == ("barbours",)


def test_o55_rota_com_apice_e_valida_mas_gera_escopo_ml_vazio(rota):
    """Apice existe no grupo (esta em `dim_loja`), entao a rota aceita — mas ela
    nao vende no Mercado Livre, e o escopo ML sai vazio, sem dado fabricado."""
    cliente, _, recebido = rota
    r = cliente.get(URL, params={"brands": "apice"})
    assert r.status_code == 200
    assert recebido["ml_brands"] == ["apice"]
    corpo = r.json()
    assert corpo["ml_scope_brands"] == []
    assert corpo["opportunity_map"]["brands"] == []
    assert corpo["opportunity_map"]["classification_status"] == "empty"
    assert corpo["opportunity_map"]["total_count"] == 0
    assert corpo["opportunity_map"]["returned_count"] == 0


def test_o56_rota_com_marca_inexistente_da_422_e_nao_chama_o_servico(rota):
    cliente, sessao, recebido = rota
    r = cliente.get(URL, params={"brands": "marca_que_nao_existe"})
    assert r.status_code == 422
    assert not recebido.get("chamado")
    # houve UMA consulta: o SELECT read-only de validacao em `marts.dim_loja`.
    # Nenhuma consulta COMERCIAL de `get_inteligencia` foi emitida.
    assert len(sessao.consultas) == 1
    assert "dim_loja" in sessao.consultas[0]


def test_o57_rota_com_payload_injetavel_da_422_e_nao_chama_o_servico(rota):
    cliente, sessao, recebido = rota
    for veneno in ("x'; DROP TABLE marts.fact_ml_produto_ranking; --",
                   "') OR 1=1 --", "barbours' UNION SELECT 1"):
        sessao.consultas.clear()
        recebido.clear()
        r = cliente.get(URL, params={"brands": veneno})
        assert r.status_code == 422, veneno
        assert not recebido.get("chamado"), veneno
        # a string nunca chega ao SQL comercial; ela aparece so' como VALOR
        # comparado em memoria contra as chaves de `dim_loja`
        assert len(sessao.consultas) == 1
        assert veneno not in sessao.consultas[0]


def test_o58_rota_mantem_tk_products_global_mesmo_com_escopo_ml_de_uma_marca(rota):
    cliente, _, _ = rota
    corpo = cliente.get(URL, params={"brands": "kokeshi"}).json()
    assert corpo["ml_scope_brands"] == ["kokeshi"]
    marcas_tk = {p["brand"] for p in corpo["tk_products"]}
    assert marcas_tk == set(gs.BRANDS_IN_SCOPE), "tk_products deixou de ser global"


def test_o59_payload_http_traz_as_treze_chaves_e_os_dois_returned_count(rota):
    cliente, _, _ = rota
    corpo = cliente.get(URL).json()
    assert list(corpo) == [
        "signals", "urgent", "scale", "organic", "pareto", "ltv", "tk_products",
        "urgent_total_count", "scale_total_count", "organic_total_count",
        "ml_snapshot_refreshed_at", "ml_scope_brands", "opportunity_map",
    ]
    m = corpo["opportunity_map"]
    assert "returned_count" in m
    assert all("returned_count" in q for q in m["quadrants"])
    assert all("returned_count" not in b for b in m["bands"])
