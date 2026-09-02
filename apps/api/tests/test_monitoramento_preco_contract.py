"""Gate PMA-1A / PMA-1A-R — contrato de match, comparacao e endpoint.

Escrito sem fixture e sem `parametrize`, o que mantem cada funcao chamavel
isoladamente. As secoes 1-9 sao de unidade e nao tocam rede nem banco; a secao
10 sobe o app FastAPI de verdade com `TestClient` e uma `Session` de mentira,
para provar a traducao HTTP (422 de contrato x 500 de inconsistencia) que o
teste de unidade nao alcanca.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services import pma_match as pm

SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "monitoramento_preco_service.py"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "schemas" / "monitoramento_preco.py"
ROUTER_PATH = Path(__file__).resolve().parents[1] / "app" / "routers" / "performance.py"

HOJE = date(2026, 9, 3)
REF_DATE = date(2026, 9, 2)


# ---------------------------------------------------------------------------
# Helpers de construcao — nada de fixture
# ---------------------------------------------------------------------------

def _ref(brand, sku, gtin, pdv, quality="ok", row_id=None, name="Produto"):
    return {
        "brand": brand,
        "source_sku": sku,
        "source_gtin": gtin,
        "suggested_retail_amount": None if pdv is None else Decimal(str(pdv)),
        "quality_status": quality,
        "captured_at": "2026-09-02T12:00:00+00:00",
        "reference_row_id": row_id or ("0" * 64),
        "product_name": name,
    }


def _listing(brand, item_id, sku, gtin, price, status="active", ref_date=REF_DATE):
    return {
        "brand": brand, "marketplace": "ml", "item_id": item_id,
        "seller_sku": sku, "gtin": gtin,
        "advertised_price": Decimal(str(price)),
        "original_price": None,
        "listing_status": status, "ref_date": ref_date,
        "listing_title": f"Anuncio {item_id}", "permalink": f"https://x/{item_id}",
        "currency": "BRL", "catalog_listing": False,
        # PMA-1A-R, F2 — captura do PRECO e alteracao do CADASTRO, separadas.
        "price_captured_at": "2026-09-02T06:03:58",
        "listing_metadata_updated_at": "2026-08-30T11:00:00",
    }


def _one(listing, refs, hoje=HOJE):
    return pm.compare_all([listing], refs, hoje)[0]


# ---------------------------------------------------------------------------
# 1. Contrato semantico
# ---------------------------------------------------------------------------

def test_constantes_de_contrato_sao_as_exigidas():
    assert pm.REFERENCE_TYPE == "suggested_retail_pdv"
    assert pm.POLICY_STATUS == "not_applicable_to_own_store_monitoring"
    assert pm.VALIDITY_STATUS == "missing"
    assert pm.COVERAGE_STATUS == "advertised_only"


def test_contrato_do_servico_bate_com_o_do_pipeline():
    """As duas fronteiras repetem os literais; aqui elas sao confrontadas.

    `apps/api` nao importa `pipelines` em runtime — esta task nao abre essa
    fronteira. O teste importa os dois lados para que uma edicao em um deles nao
    passe sem a outra.
    """
    from pipelines.pma import reference_contract as rc

    assert pm.REFERENCE_TYPE == rc.REFERENCE_TYPE
    assert pm.POLICY_STATUS == rc.POLICY_STATUS
    assert pm.VALIDITY_STATUS == rc.VALIDITY_STATUS
    assert pm.COVERAGE_STATUS == rc.COVERAGE_STATUS
    assert pm.MONITORED_BRANDS == rc.MONITORED_BRANDS
    assert pm.REFERENCE_BRANDS == rc.REFERENCE_BRANDS
    assert pm.COMPARABLE_BRANDS == rc.COMPARABLE_BRANDS
    assert pm.NO_REFERENCE_BRANDS == rc.NO_REFERENCE_BRANDS
    assert pm.OUT_OF_SCOPE_BRANDS == rc.OUT_OF_SCOPE_BRANDS


def test_pdv_nunca_e_chamado_de_pma():
    """Nenhum arquivo do endpoint pode equiparar PDV a PMA."""
    padrao = re.compile(r"pma\s*[=:]\s*['\"]?pdv|pdv\s*[=:]\s*['\"]?pma", re.I)
    for caminho in (SERVICE_PATH, SCHEMA_PATH):
        assert not padrao.search(caminho.read_text(encoding="utf-8")), caminho.name
    # `reference_type` so admite o valor de PDV no schema.
    assert 'ReferenceType = Literal["suggested_retail_pdv"]' in SCHEMA_PATH.read_text(encoding="utf-8")


def test_escopo_de_marcas_medido():
    assert pm.MONITORED_BRANDS == ("barbours", "kokeshi", "lescent", "rituaria")
    assert pm.COMPARABLE_BRANDS == ("barbours", "kokeshi", "rituaria")
    assert pm.NO_REFERENCE_BRANDS == ("lescent",)
    assert pm.OUT_OF_SCOPE_BRANDS == ("apice", "yenzah")


# ---------------------------------------------------------------------------
# 2. Contrato de match
# ---------------------------------------------------------------------------

def test_ean_e_a_chave_primaria():
    refs = [_ref("kokeshi", "OUTRO_SKU", "7908790700922", "32.90")]
    linha = _one(_listing("kokeshi", "MLB1", "SKU_QUE_NAO_CASA", "7908790700922", "40"), refs)
    assert linha["match_method"] == pm.MATCH_GTIN
    assert linha["match_quality"] == pm.QUALITY_PRIMARY
    assert linha["comparison_status"] == pm.STATUS_AT_OR_ABOVE


def test_sku_e_secundario_e_so_dentro_da_marca():
    refs = [_ref("barbours", "BB03038", None, "54.90")]
    linha = _one(_listing("barbours", "MLB2", "BB03038", None, "40"), refs)
    assert linha["match_method"] == pm.MATCH_SKU
    assert linha["match_quality"] == pm.QUALITY_SECONDARY
    assert linha["comparison_status"] == pm.STATUS_BELOW


def test_colisao_cross_brand_e_recusada():
    """SKU numerico de 5 digitos existe em varias marcas — match global casaria errado.

    Medido no PMA-0: 100 itens de barbours, 188 de kokeshi e 94 de lescent tem
    SELLER_SKU numerico de 5 digitos, o MESMO formato dos SKU do Apice.
    """
    refs = [_ref("apice", "20910", "7898652879838", "99.90")]
    linha = _one(_listing("kokeshi", "MLB3", "20910", None, "10"), refs)
    assert linha["match_method"] is None
    assert linha["match_quality"] == pm.QUALITY_UNMATCHED
    assert linha["comparison_status"] == pm.STATUS_NO_REFERENCE
    assert linha["suggested_retail_amount"] is None


def test_gtin_igual_em_marcas_diferentes_nao_casa():
    refs = [_ref("apice", "X", "7898652879838", "99.90")]
    linha = _one(_listing("barbours", "MLB4", None, "7898652879838", "10"), refs)
    assert linha["comparison_status"] == pm.STATUS_NO_REFERENCE


def test_sku_ambiguo_na_marca_e_recusado():
    refs = [
        _ref("rituaria", "RT01016", "7901128300047", "109.90", row_id="a" * 64),
        _ref("rituaria", "RT01016", "7908407007789", "38.72", row_id="b" * 64),
    ]
    linha = _one(_listing("rituaria", "MLB5", "RT01016", None, "50"), refs)
    assert linha["comparison_status"] == pm.STATUS_AMBIGUOUS
    assert linha["match_quality"] == pm.QUALITY_AMBIGUOUS
    assert linha["reference_candidate_count"] == 2
    assert linha["difference_amount"] is None


def test_referencia_rituaria_ambigua_por_ean_nao_cai_para_sku():
    """O caso real medido: EAN 7901128300047 com PDV 109,90 e 109,01.

    Cair para o SKU depois de um GTIN ambiguo escolheria uma das duas
    referencias por acidente de ordenacao. O gate proibe: ambiguo para.
    """
    refs = [
        _ref("rituaria", "RT01016", "7901128300047", "109.90", row_id="a" * 64),
        _ref("rituaria", "RT01024", "7901128300047", "109.01", row_id="b" * 64),
    ]
    # O anuncio tem SKU que casaria UNICO por SKU — e ainda assim nao pode casar.
    linha = _one(_listing("rituaria", "MLB6", "RT01024", "7901128300047", "50"), refs)
    assert linha["comparison_status"] == pm.STATUS_AMBIGUOUS
    assert linha["reference_candidate_count"] == 2
    assert linha["suggested_retail_amount"] is None


def test_dun_de_14_digitos_nao_e_tratado_como_ean():
    assert pm.consumer_ean_or_none("79087907006940") is None
    assert pm.consumer_ean_or_none("7901128400051") == "7901128400051"


def test_dun_nao_bloqueia_o_match_secundario_por_sku():
    """Regra 7 do gate: DUN invalida o GTIN, nao a linha."""
    refs = [_ref("barbours", "BB03038", None, "54.90")]
    linha = _one(_listing("barbours", "MLB7", "BB03038", "79087907006940", "40"), refs)
    assert linha["match_method"] == pm.MATCH_SKU
    assert linha["comparison_status"] == pm.STATUS_BELOW


def test_zero_fuzzy_match():
    """Somente igualdade exata. Nenhuma variacao aproximada casa."""
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "32.90")]
    for sku_ruim in ("KS0600", "KS060040", "KS-06004", "KS 06004", "ks06004x"):
        linha = _one(_listing("kokeshi", "MLB8", sku_ruim, None, "10"), refs)
        assert linha["comparison_status"] == pm.STATUS_NO_REFERENCE, sku_ruim
    # Caixa e espaco em volta sao NORMALIZACAO, nao fuzzy.
    linha = _one(_listing("kokeshi", "MLB8", " ks06004 ", None, "10"), refs)
    assert linha["comparison_status"] == pm.STATUS_BELOW


def test_match_method_e_quality_ficam_no_resultado():
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "32.90")]
    linha = _one(_listing("kokeshi", "MLB9", "KS06004", "7908790700922", "10"), refs)
    assert linha["match_method"] == pm.MATCH_GTIN
    assert linha["match_quality"] == pm.QUALITY_PRIMARY


# ---------------------------------------------------------------------------
# 3. Escopo de marca
# ---------------------------------------------------------------------------

def test_lescent_fica_no_reference():
    refs = [_ref("barbours", "BB03038", None, "54.90")]
    linha = _one(_listing("lescent", "MLB10", "LS0001", "7890000000017", "10"), refs)
    assert linha["comparison_status"] == pm.STATUS_NO_REFERENCE
    assert any("lescent" in t for t in linha["limitations"])


def test_apice_e_yenzah_fora_do_escopo():
    assert "apice" in pm.OUT_OF_SCOPE_BRANDS
    assert "yenzah" in pm.OUT_OF_SCOPE_BRANDS
    assert "apice" not in pm.MONITORED_BRANDS
    assert "yenzah" not in pm.MONITORED_BRANDS
    avisos = pm.build_warnings([], HOJE)
    texto = " ".join(avisos)
    assert pm.BRAND_SCOPE_OUT_OF_SCOPE in texto
    assert "apice" in texto and "yenzah" in texto


# ---------------------------------------------------------------------------
# 4. Nulo nao e' zero
# ---------------------------------------------------------------------------

def test_campos_de_checkout_sao_sempre_nulos_nunca_zero():
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "32.90")]
    linha = _one(_listing("kokeshi", "MLB11", "KS06004", "7908790700922", "10"), refs)
    for campo in ("shipping_amount", "seller_coupon_amount",
                  "platform_subsidy_amount", "checkout_price"):
        assert linha[campo] is None, campo
        assert linha[campo] != 0
    assert linha["coverage_status"] == "advertised_only"


def test_observed_effective_amount_e_o_preco_anunciado():
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "32.90")]
    linha = _one(_listing("kokeshi", "MLB12", "KS06004", "7908790700922", "27.50"), refs)
    assert linha["observed_effective_amount"] == linha["advertised_price"]
    assert linha["observed_effective_amount"] == Decimal("27.50")


def test_sem_referencia_a_diferenca_e_nula_nao_zero():
    linha = _one(_listing("lescent", "MLB13", "ZZ", None, "10"), [])
    assert linha["difference_amount"] is None
    assert linha["difference_pct"] is None
    assert linha["suggested_retail_amount"] is None


def test_referencia_sem_preco_nao_entra_no_indice():
    refs = [_ref("kokeshi", "KS06004", "7908790700922", None,
                 quality="missing_suggested_price")]
    linha = _one(_listing("kokeshi", "MLB14", "KS06004", "7908790700922", "10"), refs)
    assert linha["comparison_status"] == pm.STATUS_NO_REFERENCE
    assert linha["difference_amount"] is None


# ---------------------------------------------------------------------------
# 5. Status, precedencia e KPIs
# ---------------------------------------------------------------------------

def test_anuncio_inativo_nao_recebe_veredito_de_preco():
    refs = [_ref("barbours", "BB03038", None, "54.90")]
    for estado in ("paused", "under_review", "inactive"):
        linha = _one(_listing("barbours", "MLB15", "BB03038", None, "10", status=estado), refs)
        assert linha["comparison_status"] == pm.STATUS_INACTIVE, estado
        assert linha["difference_amount"] is None


def test_observacao_vencida_precede_qualquer_veredito():
    refs = [_ref("barbours", "BB03038", None, "54.90")]
    antiga = _listing("barbours", "MLB16", "BB03038", None, "10",
                      ref_date=date(2026, 8, 1))
    linha = _one(antiga, refs)
    assert linha["comparison_status"] == pm.STATUS_STALE
    assert linha["difference_amount"] is None


def test_somente_d_menos_1_sustenta_comparacao():
    """F4: a tolerancia que aceitava D-2 foi REMOVIDA. Fronteiras exatas."""
    refs = [_ref("barbours", "BB03038", None, "54.90")]
    esperado = {
        1: pm.STATUS_BELOW,   # D-1 -> elegivel
        2: pm.STATUS_STALE,   # D-2 -> vencida (antes era aceita como fresca)
        3: pm.STATUS_STALE,
        30: pm.STATUS_STALE,
    }
    for delta, alvo_status in esperado.items():
        alvo = date.fromordinal(HOJE.toordinal() - delta)
        linha = _one(_listing("barbours", "X", "BB03038", None, "10", ref_date=alvo), refs)
        assert linha["comparison_status"] == alvo_status, (delta, linha["comparison_status"])
        if alvo_status == pm.STATUS_STALE:
            assert linha["difference_amount"] is None
            assert linha["difference_pct"] is None
            assert linha["suggested_retail_amount"] is None


def test_classificacao_da_data_tem_tres_estados():
    assert pm.classify_observation_date(date(2026, 9, 2), HOJE) == pm.OBS_ELIGIBLE
    assert pm.classify_observation_date(date(2026, 9, 1), HOJE) == pm.OBS_STALE
    assert pm.classify_observation_date(HOJE, HOJE) == pm.OBS_INVALID
    assert pm.classify_observation_date(date(2026, 9, 4), HOJE) == pm.OBS_INVALID
    assert pm.last_eligible_date(HOJE) == date(2026, 9, 2)


def test_dia_corrente_e_futuro_falham_fechado():
    """F4: D0 e futuro nao sao "dado tardio" — o sync proibe publica-los.

    Se aparecerem no serving, a camada esta inconsistente, e o caminho correto e'
    levantar, nunca devolver um veredito de preco.
    """
    refs = [_ref("barbours", "BB03038", None, "54.90")]
    for alvo in (HOJE, date(2026, 9, 10)):
        erro = None
        try:
            _one(_listing("barbours", "X", "BB03038", None, "10", ref_date=alvo), refs)
        except pm.PmaMatchError as exc:
            erro = str(exc)
        assert erro is not None, alvo
        assert "dia operacional corrente" in erro
        assert "inconsistente" in erro


def test_tolerancia_de_frescor_nao_existe_mais():
    assert not hasattr(pm, "STALE_TOLERANCE_DAYS")
    assert not hasattr(pm, "is_stale")


def test_abaixo_e_acima_da_referencia():
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "100.00")]
    abaixo = _one(_listing("kokeshi", "A", "KS06004", "7908790700922", "90.00"), refs)
    assert abaixo["comparison_status"] == pm.STATUS_BELOW
    assert abaixo["difference_amount"] == Decimal("-10.00")
    assert abaixo["difference_pct"] == Decimal("-10.0000")

    igual = _one(_listing("kokeshi", "B", "KS06004", "7908790700922", "100.00"), refs)
    assert igual["comparison_status"] == pm.STATUS_AT_OR_ABOVE
    assert igual["difference_amount"] == Decimal("0.00")

    acima = _one(_listing("kokeshi", "C", "KS06004", "7908790700922", "125.00"), refs)
    assert acima["comparison_status"] == pm.STATUS_AT_OR_ABOVE
    assert acima["difference_pct"] == Decimal("25.0000")


def test_kpis_fecham_com_o_total():
    refs = [
        _ref("kokeshi", "KS06004", "7908790700922", "100.00"),
        _ref("rituaria", "RT01016", "7901128300047", "109.90", row_id="a" * 64),
        _ref("rituaria", "RT01024", "7901128300047", "109.01", row_id="b" * 64),
    ]
    listings = [
        _listing("kokeshi", "A", "KS06004", "7908790700922", "90"),
        _listing("kokeshi", "B", "KS06004", "7908790700922", "110"),
        _listing("rituaria", "C", None, "7901128300047", "50"),
        _listing("lescent", "D", "ZZ", None, "10"),
        _listing("kokeshi", "E", "KS06004", "7908790700922", "90", status="paused"),
        _listing("kokeshi", "F", "KS06004", "7908790700922", "90",
                 ref_date=date(2026, 7, 1)),
    ]
    rows = pm.compare_all(listings, refs, HOJE)
    k = pm.build_kpis(rows)
    assert k["monitored_count"] == 6
    assert k["below_reference_count"] == 1
    assert k["at_or_above_reference_count"] == 1
    assert k["comparable_count"] == 2
    assert k["ambiguous_reference_count"] == 1
    assert k["no_reference_count"] == 1
    assert k["inactive_count"] == 1
    assert k["stale_count"] == 1
    soma = (k["below_reference_count"] + k["at_or_above_reference_count"]
            + k["no_reference_count"] + k["ambiguous_reference_count"]
            + k["stale_count"] + k["inactive_count"])
    assert soma == k["monitored_count"]


def test_nao_existe_severidade_no_payload():
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "100.00")]
    linha = _one(_listing("kokeshi", "A", "KS06004", "7908790700922", "50"), refs)
    for proibido in ("severity", "severidade", "gravidade", "priority", "critical",
                     "critico", "threshold", "limiar"):
        assert not any(proibido in c.lower() for c in linha), proibido


# ---------------------------------------------------------------------------
# 6. Vocabulario — nenhuma palavra de infracao/violacao no payload
# ---------------------------------------------------------------------------

_PROIBIDAS = (
    "infracao", "infração", "infringement", "violacao", "violação", "violation",
    "sancao", "sanção", "penalidade", "multa", "denuncia", "denúncia",
    "descadastr", "proibido", "ilegal", "punicao", "punição",
)


def test_payload_nao_tem_vocabulario_de_infracao():
    refs = [
        _ref("kokeshi", "KS06004", "7908790700922", "100.00"),
        _ref("rituaria", "RT01016", "7901128300047", "109.90", row_id="a" * 64),
        _ref("rituaria", "RT01024", "7901128300047", "109.01", row_id="b" * 64),
    ]
    listings = [
        _listing("kokeshi", "A", "KS06004", "7908790700922", "50"),
        _listing("rituaria", "B", None, "7901128300047", "50"),
        _listing("lescent", "C", "ZZ", None, "10"),
        _listing("kokeshi", "D", "KS06004", "7908790700922", "50", status="paused"),
        _listing("kokeshi", "E", "KS06004", "7908790700922", "50",
                 ref_date=date(2026, 7, 1)),
    ]
    rows = pm.compare_all(listings, refs, HOJE)
    texto = " ".join(
        str(v).lower()
        for r in rows for v in list(r.values()) + list(r.keys())
        if v is not None
    )
    texto += " " + " ".join(pm.build_warnings(rows, HOJE)).lower()
    texto += " " + " ".join(str(v).lower() for v in pm.build_kpis(rows).keys())
    for proibida in _PROIBIDAS:
        assert proibida not in texto, proibida


def test_status_usa_o_vocabulario_do_gate():
    assert pm.STATUS_BELOW == "below_reference"
    assert pm.STATUS_AT_OR_ABOVE == "at_or_above_reference"
    assert pm.STATUS_NO_REFERENCE == "no_reference"
    assert pm.STATUS_AMBIGUOUS == "non_comparable_reference_ambiguous"
    assert pm.STATUS_INACTIVE == "inactive_listing"
    assert pm.STATUS_STALE == "stale_observation"
    assert len(set(pm.COMPARISON_STATUSES)) == 6


# ---------------------------------------------------------------------------
# 7. O endpoint le somente `marts.*` no Neon, e nunca escreve
# ---------------------------------------------------------------------------

def _queries() -> tuple:
    """A lista REAL de consultas do servico, nao um regex sobre o arquivo.

    Varrer `ALL_QUERIES` em vez do texto garante que uma consulta nova nao escape
    da verificacao por ficar fora do padrao textual esperado.
    """
    from app.services import monitoramento_preco_service as svc

    return svc.ALL_QUERIES


def test_servico_nao_toca_gold_nem_raw_nem_silver():
    for sql in _queries():
        baixo = sql.lower()
        assert "gold." not in baixo, sql[:120]
        assert "raw." not in baixo, sql[:120]
        assert "silver." not in baixo, sql[:120]


def test_servico_le_apenas_as_duas_tabelas_de_marts():
    tabelas = set()
    for sql in _queries():
        tabelas |= set(re.findall(r"marts\.[a-z_]+", sql))
    assert tabelas == {
        "marts.fact_marketplace_listing_price_daily",
        "marts.fact_suggested_price_reference_snapshot",
    }, tabelas


def test_servico_nao_tem_escrita():
    for sql in _queries():
        baixo = sql.lower()
        for verbo in ("insert", "update ", "delete", "truncate", "create",
                      "drop", "alter", "grant"):
            assert verbo not in baixo, (verbo, sql[:120])


def test_servico_nao_interpola_valor_no_sql():
    """Valores sempre por parametro nomeado. Interpolacao seria injecao."""
    for sql in _queries():
        assert "{" not in sql, sql[:160]
        assert "%s" not in sql, sql[:160]
    # E o texto-fonte so interpola nomes de tabela, constantes do modulo.
    fonte = SERVICE_PATH.read_text(encoding="utf-8")
    for bloco in re.findall(r'SQL_[A-Z_]+\s*=\s*f?"""(.*?)"""', fonte, re.S):
        for chave in re.findall(r"\{([^}]*)\}", bloco):
            assert chave in ("LISTING_TABLE", "REFERENCE_TABLE"), chave


def test_as_duas_pks_cobrem_todas_as_consultas():
    """F9: nenhum indice secundario porque cada consulta usa prefixo de PK.

    PK do fato     = (ref_date, marketplace, seller_id, item_id)
    PK do snapshot = (snapshot_id, reference_row_id)
    """
    for sql in _queries():
        baixo = " ".join(sql.lower().split())
        if "fact_marketplace_listing_price_daily" in baixo:
            assert ("ref_date =" in baixo or "max(ref_date)" in baixo), baixo
        elif "fact_suggested_price_reference_snapshot" in baixo:
            assert ("snapshot_id =" in baixo
                    or "group by snapshot_id" in baixo), baixo
        else:
            raise AssertionError(f"consulta sem tabela reconhecida: {baixo}")


def test_endpoint_declarado_com_o_caminho_do_gate():
    texto = ROUTER_PATH.read_text(encoding="utf-8")
    assert '@router.get("/monitoramento-preco"' in texto
    assert 'router = APIRouter(prefix="/api/v1/performance"' in texto


def _assinatura_do_endpoint() -> str:
    texto = ROUTER_PATH.read_text(encoding="utf-8")
    trecho = texto[texto.index('"/monitoramento-preco"'):]
    return trecho[:trecho.index("sessao = _require_db")]


def test_endpoint_tem_todos_os_filtros_minimos():
    trecho = _assinatura_do_endpoint()
    for filtro in ("marketplace", "brand", "status", "product_query",
                   "limit", "offset"):
        assert f"{filtro}:" in trecho, filtro


def test_endpoint_nao_expoe_filtro_de_data():
    """F3 + PMA-1B: nao existe FILTRO de data — existe uma RECUSA de data.

    O PMA-1A-R removeu o parametro da assinatura, e isso era insuficiente: o
    FastAPI ignora query parameters desconhecidos, entao `?ref_date=...` voltava
    200 como se o filtro historico tivesse sido aplicado. Agora o parametro
    existe na assinatura APENAS para ser recusado, oculto do OpenAPI.

    O contrato verificado aqui e' o negativo: `ref_date` nunca chega ao servico.
    """
    trecho = _assinatura_do_endpoint()
    assert "include_in_schema=False" in trecho
    # A unica mencao a `ref_date` na chamada do servico e' a AUSENCIA dela.
    texto = ROUTER_PATH.read_text(encoding="utf-8")
    chamada = texto[texto.index("mp_svc.get_monitoramento_preco("):]
    chamada = chamada[:chamada.index(")")]
    assert "ref_date" not in chamada, chamada


def test_servico_nao_aceita_parametro_de_data():
    import inspect

    from app.services import monitoramento_preco_service as svc

    parametros = inspect.signature(svc.get_monitoramento_preco).parameters
    assert "ref_date" not in parametros
    # `today` permanece, e existe SO para fixar o dia operacional em teste.
    assert "today" in parametros


def test_teto_de_tamanho_nao_usa_o_validador_nativo_que_ecoa():
    """F6: `max_length` do FastAPI ECHOA o valor recusado no corpo do 422.

    Medido: um `brand` de 200 caracteres voltava com os 200 caracteres em
    `{"input": ...}`. Por isso os tres parametros de texto NAO declaram
    `max_length`, e o teto e' aplicado no corpo do endpoint com mensagem
    constante. O limite continua documentado na descricao, para nao sair do
    OpenAPI.
    """
    trecho = _assinatura_do_endpoint()
    for parametro in ("brand", "status", "product_query"):
        bloco = re.search(rf"{parametro}: Optional\[str\] = Query\((.*?)\n    \),",
                          trecho, re.S)
        assert bloco, parametro
        assert "max_length" not in bloco.group(1), parametro
        assert "Maximo de" in bloco.group(1), parametro


def test_tetos_de_tamanho_existem_no_servico():
    svc = _servico()
    assert svc.MAX_BRAND_PARAM_CHARS == 120
    assert svc.MAX_STATUS_PARAM_CHARS == 240
    assert svc.MAX_PRODUCT_QUERY_CHARS == 120


def test_erro_interno_nao_e_422_e_tem_mensagem_fixa():
    """F7: `PmaMatchError` e' inconsistencia NOSSA, nao erro do cliente."""
    texto = ROUTER_PATH.read_text(encoding="utf-8")
    trecho = texto[texto.index('"/monitoramento-preco"'):]
    assert "except pma_match.PmaMatchError" in trecho
    assert "HTTPException(500, ERRO_SERVING_INCONSISTENTE)" in trecho
    # A mensagem devolvida NAO carrega o texto da excecao.
    assert "HTTPException(500, str(exc))" not in trecho
    # E nao existe `except Exception` no caminho: erro de programacao propaga.
    assert "except Exception" not in trecho


def test_pma_match_error_nao_e_value_error():
    """Se voltasse a ser `ValueError`, um `except ValueError` a mapearia p/ 422."""
    assert issubclass(pm.PmaMatchError, RuntimeError)
    assert not issubclass(pm.PmaMatchError, ValueError)


def test_mensagem_de_erro_interno_nao_vaza_detalhe():
    from app.routers import performance as rp

    msg = rp.ERRO_SERVING_INCONSISTENTE.lower()
    for vazamento in ("select", "marts.", "nan", "ref_date", "teto", "sql",
                      "postgres", "traceback", "d0"):
        assert vazamento not in msg, vazamento


def test_seller_id_nao_e_exposto_na_linha():
    """`seller_id` nao ajuda a decisao da tela: as 4 contas sao todas proprias."""
    texto = SCHEMA_PATH.read_text(encoding="utf-8")
    trecho = texto[texto.index("class MonitoramentoPrecoRow"):]
    trecho = trecho[:trecho.index("class MonitoramentoPrecoResponse")]
    assert "seller_id" not in trecho


def test_envelope_tem_todos_os_campos_exigidos():
    texto = SCHEMA_PATH.read_text(encoding="utf-8")
    for campo in ("timezone", "currency", "refreshed_at", "observed_ref_date",
                  "reference_captured_at", "reference_type", "validity_status",
                  "coverage_status", "warnings"):
        assert f"{campo}:" in texto, campo
    for kpi in ("monitored_count", "comparable_count", "below_reference_count",
                "at_or_above_reference_count", "no_reference_count",
                "ambiguous_reference_count", "stale_count"):
        assert f"{kpi}:" in texto, kpi
    for chave in ("rows:", "returned_count:", "total_count:", "truncated:"):
        assert chave in texto, chave


# ---------------------------------------------------------------------------
# 8. Escala: recusa em vez de truncar
# ---------------------------------------------------------------------------

def test_excesso_de_linhas_e_recusado_nao_truncado():
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "100.00")]
    grandes = [_listing("kokeshi", f"I{i}", "KS06004", None, "10")
               for i in range(3)]
    original = pm.MAX_LISTING_ROWS
    try:
        pm.MAX_LISTING_ROWS = 2
        erro = None
        try:
            pm.compare_all(grandes, refs, HOJE)
        except pm.PmaMatchError as exc:
            erro = str(exc)
        assert erro is not None and "truncado" in erro
    finally:
        pm.MAX_LISTING_ROWS = original


def test_nan_e_recusado():
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "100.00")]
    ruim = _listing("kokeshi", "A", "KS06004", "7908790700922", "10")
    ruim["advertised_price"] = Decimal("NaN")
    erro = None
    try:
        pm.compare_all([ruim], refs, HOJE)
    except pm.PmaMatchError as exc:
        erro = str(exc)
    assert erro is not None and "NaN" in erro


# ---------------------------------------------------------------------------
# 9. Envelope de ponta a ponta: filtros, paginacao, contagens, truncamento
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return self._linhas


class FakeSession:
    """Session de mentira: responde por trecho da consulta. Registra tudo."""

    def __init__(self, listings, referencias, ref_date=REF_DATE,
                 synced_at="2026-09-03T09:03:00+00:00"):
        self.listings = listings
        self.referencias = referencias
        self.ref_date = ref_date
        self.synced_at = synced_at
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        texto = " ".join(str(sql).lower().split())
        self.executed.append((texto, params or {}))
        if "max(ref_date)" in texto:
            return _Result([{"ref_date": self.ref_date}])
        if "max(synced_at)" in texto:
            return _Result([{"synced_at": self.synced_at}])
        if "group by snapshot_id" in texto:
            return _Result([{"snapshot_id": "pma-ref:20260902T120000Z",
                             "captured_at": "2026-09-02T12:00:00+00:00"}])
        if "from marts.fact_suggested_price_reference_snapshot" in texto:
            return _Result(list(self.referencias))
        if "from marts.fact_marketplace_listing_price_daily" in texto:
            return _Result(list(self.listings))
        return _Result([])


def _servico():
    from app.services import monitoramento_preco_service as svc
    return svc


def _cenario(n_abaixo=3, n_acima=2):
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "100.00")]
    listings = []
    for i in range(n_abaixo):
        listings.append(_listing("kokeshi", f"B{i}", "KS06004", "7908790700922",
                                 str(90 - i)))
    for i in range(n_acima):
        listings.append(_listing("kokeshi", f"A{i}", "KS06004", "7908790700922",
                                 str(110 + i)))
    return listings, refs


def test_envelope_completo_e_coerente():
    svc = _servico()
    listings, refs = _cenario()
    db = FakeSession(listings, refs)
    saida = svc.get_monitoramento_preco(db, today=HOJE)
    assert saida["meta"]["reference_type"] == "suggested_retail_pdv"
    assert saida["meta"]["validity_status"] == "missing"
    assert saida["meta"]["coverage_status"] == "advertised_only"
    assert saida["meta"]["policy_status"] == "not_applicable_to_own_store_monitoring"
    assert saida["meta"]["timezone"] == "America/Sao_Paulo"
    assert saida["meta"]["currency"] == "BRL"
    assert saida["meta"]["observed_ref_date"] == REF_DATE.isoformat()
    assert saida["meta"]["refreshed_at"]
    assert saida["meta"]["reference_captured_at"]
    assert saida["meta"]["warnings"]
    assert saida["kpis"]["monitored_count"] == 5
    assert saida["kpis"]["below_reference_count"] == 3
    assert saida["kpis"]["at_or_above_reference_count"] == 2
    assert saida["total_count"] == 5
    assert saida["returned_count"] == 5
    assert saida["truncated"] is False


def test_paginacao_e_truncamento_coerentes():
    svc = _servico()
    listings, refs = _cenario(n_abaixo=4, n_acima=3)
    db = FakeSession(listings, refs)

    p1 = svc.get_monitoramento_preco(db, limit=3, offset=0, today=HOJE)
    assert p1["returned_count"] == 3
    assert p1["total_count"] == 7
    assert p1["truncated"] is True

    p2 = svc.get_monitoramento_preco(db, limit=3, offset=3, today=HOJE)
    assert p2["returned_count"] == 3 and p2["truncated"] is True

    p3 = svc.get_monitoramento_preco(db, limit=3, offset=6, today=HOJE)
    assert p3["returned_count"] == 1 and p3["truncated"] is False

    vistos = [r["item_id"] for r in p1["rows"] + p2["rows"] + p3["rows"]]
    assert len(set(vistos)) == 7


def test_offset_alem_do_total_devolve_pagina_vazia():
    svc = _servico()
    listings, refs = _cenario()
    saida = svc.get_monitoramento_preco(
        FakeSession(listings, refs), limit=10, offset=99, today=HOJE
    )
    assert saida["returned_count"] == 0
    assert saida["total_count"] == 5
    assert saida["truncated"] is False


def test_ordenacao_poe_o_mais_abaixo_primeiro_e_nulo_no_fim():
    svc = _servico()
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "100.00")]
    listings = [
        _listing("kokeshi", "ACIMA", "KS06004", "7908790700922", "150"),
        _listing("lescent", "SEMREF", "ZZ", None, "10"),
        _listing("kokeshi", "ABAIXO", "KS06004", "7908790700922", "10"),
    ]
    saida = svc.get_monitoramento_preco(FakeSession(listings, refs), today=HOJE)
    ordem = [r["item_id"] for r in saida["rows"]]
    assert ordem == ["ABAIXO", "ACIMA", "SEMREF"]
    assert saida["rows"][-1]["difference_pct"] is None


def test_kpis_nao_respondem_ao_filtro_de_status():
    """Filtrar a tabela nao pode destruir o denominador dos KPIs."""
    svc = _servico()
    listings, refs = _cenario(n_abaixo=3, n_acima=2)
    db = FakeSession(listings, refs)
    saida = svc.get_monitoramento_preco(db, status="below_reference", today=HOJE)
    assert saida["kpis"]["monitored_count"] == 5
    assert saida["kpis"]["at_or_above_reference_count"] == 2
    assert saida["total_count"] == 3
    assert all(r["comparison_status"] == "below_reference" for r in saida["rows"])


def test_marketplace_fora_do_escopo_e_recusado_sem_ecoar_a_entrada():
    svc = _servico()
    listings, refs = _cenario()
    for canal in ("shopee", "tiktok", "amazon", "'; DROP TABLE x; --"):
        erro = None
        try:
            svc.get_monitoramento_preco(
                FakeSession(listings, refs), marketplace=canal, today=HOJE
            )
        except svc.MonitoramentoPrecoError as exc:
            erro = str(exc)
        assert erro is not None, canal
        assert canal not in erro, canal
        assert "ml" in erro


def test_marca_fora_do_escopo_e_recusada_com_a_razao_certa():
    svc = _servico()
    listings, refs = _cenario()
    for marca in ("apice", "yenzah"):
        erro = None
        try:
            svc.get_monitoramento_preco(
                FakeSession(listings, refs), brand=marca, today=HOJE
            )
        except svc.MonitoramentoPrecoError as exc:
            erro = str(exc)
        assert erro is not None and pm.BRAND_SCOPE_OUT_OF_SCOPE in erro, marca


def test_marca_inexistente_e_recusada():
    svc = _servico()
    listings, refs = _cenario()
    erro = None
    try:
        svc.get_monitoramento_preco(
            FakeSession(listings, refs), brand="marca_que_nao_existe", today=HOJE
        )
    except svc.MonitoramentoPrecoError as exc:
        erro = str(exc)
    assert erro is not None


def test_status_invalido_e_recusado():
    svc = _servico()
    listings, refs = _cenario()
    erro = None
    try:
        svc.get_monitoramento_preco(
            FakeSession(listings, refs), status="infracao", today=HOJE
        )
    except svc.MonitoramentoPrecoError as exc:
        erro = str(exc)
    assert erro is not None


def test_limit_fora_do_intervalo_e_recusado():
    svc = _servico()
    listings, refs = _cenario()
    for limit in (0, -1, 10_000):
        erro = None
        try:
            svc.get_monitoramento_preco(
                FakeSession(listings, refs), limit=limit, today=HOJE
            )
        except svc.MonitoramentoPrecoError as exc:
            erro = str(exc)
        assert erro is not None, limit


def test_erro_de_contrato_nao_vaza_sql_dsn_host_nem_credencial():
    svc = _servico()
    listings, refs = _cenario()
    mensagens = []
    for kwargs in (
        {"marketplace": "shopee"}, {"brand": "apice"}, {"status": "xyz"},
        {"limit": 0}, {"offset": -5}, {"product_query": "x" * 200},
    ):
        try:
            svc.get_monitoramento_preco(FakeSession(listings, refs),
                                        today=HOJE, **kwargs)
        except svc.MonitoramentoPrecoError as exc:
            mensagens.append(str(exc).lower())
    assert len(mensagens) == 6
    for msg in mensagens:
        for vazamento in ("select", "from marts", "postgres://", "postgresql://",
                          "password", "senha", "@", "5432", "neon", "dsn",
                          "insert", "delete", "sqlalchemy", "traceback"):
            assert vazamento not in msg, (vazamento, msg)


def test_servico_so_consulta_as_duas_tabelas_de_marts_em_runtime():
    """Prova comportamental: toda consulta emitida cita apenas `marts.*`."""
    svc = _servico()
    listings, refs = _cenario()
    db = FakeSession(listings, refs)
    svc.get_monitoramento_preco(db, today=HOJE)
    assert db.executed
    for sql, _ in db.executed:
        assert "gold." not in sql, sql
        assert "raw." not in sql, sql
        assert "silver." not in sql, sql
        for verbo in ("insert", "update ", "delete", "truncate", "create"):
            assert verbo not in sql, (verbo, sql)


def test_modo_latest_usa_a_maior_ref_date_publicada_nao_hoje():
    svc = _servico()
    listings, refs = _cenario()
    db = FakeSession(listings, refs, ref_date=date(2026, 9, 1))
    saida = svc.get_monitoramento_preco(db, today=HOJE)
    assert saida["meta"]["observed_ref_date"] == "2026-09-01"
    assert saida["meta"]["observed_ref_date"] != HOJE.isoformat()


def test_sem_dado_publicado_o_envelope_e_vazio_mas_honesto():
    svc = _servico()

    class Vazia(FakeSession):
        def execute(self, sql, params=None):
            texto = " ".join(str(sql).lower().split())
            self.executed.append((texto, params or {}))
            if "max(ref_date)" in texto:
                return _Result([{"ref_date": None}])
            return _Result([])

    saida = svc.get_monitoramento_preco(Vazia([], []), today=HOJE)
    assert saida["meta"]["observed_ref_date"] is None
    assert saida["meta"]["reference_captured_at"] is None
    assert saida["kpis"]["monitored_count"] == 0
    assert saida["kpis"]["below_reference_count"] == 0
    assert saida["rows"] == []
    assert saida["total_count"] == 0
    assert saida["truncated"] is False


#: Payloads hostis: HTML, quebra de linha, DSN falso, IP privado, texto longo,
#: injecao de SQL e caractere de controle.
_PAYLOADS_HOSTIS = (
    "<script>alert('x')</script>",
    "linha1\nlinha2\r\nlinha3",
    "postgresql://usuario:senha_secreta@db-privado.interno:5432/base",
    "10.20.30.40",
    "A" * 5000,
    "'; DROP TABLE marts.fact_marketplace_listing_price_daily; --",
    "marca\x00nula",
    "barbours‮evil",
)


_FRAGMENTOS_HOSTIS = (
    "<script", "alert", "senha_secreta", "db-privado", "10.20.30.40",
    "DROP TABLE", "AAAA", "\x00", "‮", "\n", "\r",
)


def test_recusa_de_enum_nao_reflete_payload_hostil():
    """F6: em `marketplace`, `brand` e `status` todo payload hostil e' RECUSADO,
    e a mensagem e' FIXA — nao devolve um caractere da entrada."""
    svc = _servico()
    listings, refs = _cenario()
    for payload in _PAYLOADS_HOSTIS:
        for campo in ("marketplace", "brand", "status"):
            erro = None
            try:
                svc.get_monitoramento_preco(
                    FakeSession(listings, refs), today=HOJE, **{campo: payload}
                )
            except svc.MonitoramentoPrecoError as exc:
                erro = str(exc)
            assert erro is not None, (campo, payload[:40])
            for fragmento in _FRAGMENTOS_HOSTIS:
                assert fragmento not in erro, (campo, fragmento)


def test_product_query_hostil_e_aceita_mas_nunca_devolvida():
    """`product_query` e' texto de BUSCA, nao enum: recusa-la seria errado.

    A garantia aqui e' outra e mais forte: o payload nao aparece em NENHUM lugar
    da resposta — nao ha eco de filtro no envelope — e vai ao SQL somente como
    parametro nomeado.
    """
    svc = _servico()
    listings, refs = _cenario()
    for payload in _PAYLOADS_HOSTIS:
        if len(payload) > svc.MAX_PRODUCT_QUERY_CHARS:
            erro = None
            try:
                svc.get_monitoramento_preco(FakeSession(listings, refs),
                                            today=HOJE, product_query=payload)
            except svc.MonitoramentoPrecoError as exc:
                erro = str(exc)
            assert erro == svc.ERRO_PRODUCT_QUERY_TAMANHO
            continue

        db = FakeSession(listings, refs)
        saida = svc.get_monitoramento_preco(db, today=HOJE, product_query=payload)
        serializado = repr(saida)
        for fragmento in _FRAGMENTOS_HOSTIS:
            if fragmento in payload:
                assert fragmento not in serializado, fragmento
        # E no SQL o valor viajou como PARAMETRO, nunca no texto.
        for sql, params in db.executed:
            assert payload not in sql
            if params and "query_like" in params:
                assert params["query_like"] == f"%{payload.strip()}%"


def test_mensagens_de_recusa_sao_constantes_do_modulo():
    """Nenhuma mensagem e' construida por interpolacao de entrada."""
    svc = _servico()
    fixas = {
        svc.ERRO_MARKETPLACE, svc.ERRO_BRAND_INVALIDA, svc.ERRO_BRAND_TAMANHO,
        svc.ERRO_STATUS_INVALIDO, svc.ERRO_STATUS_TAMANHO,
        svc.ERRO_PRODUCT_QUERY_TAMANHO, svc.ERRO_PAGINACAO_LIMIT,
        svc.ERRO_PAGINACAO_OFFSET,
    }
    listings, refs = _cenario()
    vistas = set()
    for kwargs in ({"marketplace": "shopee"}, {"brand": "apice"},
                   {"brand": "x" * 200}, {"status": "xyz"},
                   {"status": "y" * 400}, {"product_query": "z" * 500},
                   {"limit": 0}, {"offset": -1}):
        try:
            svc.get_monitoramento_preco(FakeSession(listings, refs),
                                        today=HOJE, **kwargs)
        except svc.MonitoramentoPrecoError as exc:
            vistas.add(str(exc))
    assert vistas, "nenhuma recusa observada"
    assert vistas <= fixas, vistas - fixas


def test_constantes_de_erro_so_interpolam_constantes_do_modulo():
    """Uma `f-string` numa mensagem de erro e' aceitavel se, e somente se, o que
    ela interpola for constante do modulo — nunca entrada do cliente.

    `ERRO_PAGINACAO_LIMIT` interpola `MAX_LIMIT`, que e' uma constante; por isso
    a mensagem continua identica a cada chamada, o que o teste de constancia ao
    lado tambem confirma.
    """
    import ast

    fonte = SERVICE_PATH.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    constantes = {
        alvo.id
        for no in arvore.body if isinstance(no, ast.Assign)
        for alvo in no.targets if isinstance(alvo, ast.Name)
    }
    for no in arvore.body:
        if not isinstance(no, ast.Assign):
            continue
        nomes = [a.id for a in no.targets if isinstance(a, ast.Name)]
        if not any(n.startswith("ERRO_") for n in nomes):
            continue
        for filho in ast.walk(no.value):
            if isinstance(filho, ast.FormattedValue):
                assert isinstance(filho.value, ast.Name), (nomes, ast.dump(filho))
                assert filho.value.id in constantes, (nomes, filho.value.id)


def test_redacao_declara_direcao_indeterminada_nao_conservadora():
    """F8: checkout = produto + frete - cupom; frete eleva, cupom reduz."""
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "100.00")]
    linha = _one(_listing("kokeshi", "A", "KS06004", "7908790700922", "50"), refs)
    texto = " ".join(linha["limitations"]).lower()
    assert "comparacao parcial" in texto
    assert "preco final de checkout" in texto
    assert "pode mudar" in texto
    avisos = " ".join(pm.build_warnings([linha], HOJE)).lower()
    assert "indeterminada" in avisos
    assert "produto + frete - cupom" in avisos


def test_nenhuma_afirmacao_de_conservadorismo_no_codigo():
    """A afirmacao de que a ausencia de frete/cupom torna o resultado
    necessariamente conservador esta ERRADA e nao pode existir em nenhum
    artefato."""
    from pathlib import Path as _P

    raiz = _P(__file__).resolve().parents[3]
    alvos = [
        raiz / "apps/api/app/services/pma_match.py",
        raiz / "apps/api/app/services/monitoramento_preco_service.py",
        raiz / "apps/api/app/schemas/monitoramento_preco.py",
        raiz / "apps/api/app/routers/performance.py",
        raiz / "db/sql/marts/pma_listing_price_serving_ddl.sql",
        raiz / "pipelines/sync_ml_listing_price_serving.py",
        raiz / "pipelines/pma/reference_contract.py",
        raiz / "pipelines/pma/reference_import.py",
    ]
    for caminho in alvos:
        texto = caminho.read_text(encoding="utf-8").lower()
        for frase in ("conservador na direcao", "necessariamente conservador",
                      "subestima violacoes", "subestima desvios",
                      "limite superior de violac"):
            assert frase not in texto, (caminho.name, frase)


def test_referencia_sem_preco_atravessa_o_pipeline_sem_quebrar():
    """F1: linha com PDV ausente e' auditavel, nao entra em match, nao vira zero."""
    svc = _servico()
    refs = [
        _ref("kokeshi", "KS06004", "7908790700922", None,
             quality="missing_suggested_price", row_id="a" * 64),
        _ref("kokeshi", "KS06005", "7908790700915", "100.00", row_id="b" * 64),
    ]
    listings = [
        _listing("kokeshi", "SEMPDV", "KS06004", "7908790700922", "10"),
        _listing("kokeshi", "COMPDV", "KS06005", "7908790700915", "90"),
    ]
    saida = svc.get_monitoramento_preco(FakeSession(listings, refs), today=HOJE)
    por_item = {r["item_id"]: r for r in saida["rows"]}
    assert por_item["SEMPDV"]["comparison_status"] == "no_reference"
    assert por_item["SEMPDV"]["suggested_retail_amount"] is None
    assert por_item["SEMPDV"]["difference_amount"] is None
    assert por_item["COMPDV"]["comparison_status"] == "below_reference"
    assert saida["kpis"]["no_reference_count"] == 1
    assert saida["kpis"]["below_reference_count"] == 1


def test_observed_at_e_a_captura_do_preco_nao_o_cadastro():
    """F2: `observed_at` vem de `price_captured_at`, nunca do metadado."""
    refs = [_ref("kokeshi", "KS06004", "7908790700922", "100.00")]
    linha = _one(_listing("kokeshi", "A", "KS06004", "7908790700922", "50"), refs)
    assert linha["observed_at"] == "2026-09-02T06:03:58"
    assert linha["listing_metadata_updated_at"] == "2026-08-30T11:00:00"
    assert linha["observed_at"] != linha["listing_metadata_updated_at"]


def test_validity_status_do_schema_admite_somente_missing():
    """F5: o contrato HTTP nao pode oferecer um estado que o banco nao prova."""
    texto = SCHEMA_PATH.read_text(encoding="utf-8")
    assert 'ValidityStatus = Literal["missing"]' in texto
    assert '"declared"' not in texto
    assert '"expired"' not in texto


def test_meta_declara_modo_latest_e_data_elegivel():
    svc = _servico()
    listings, refs = _cenario()
    saida = svc.get_monitoramento_preco(FakeSession(listings, refs), today=HOJE)
    assert saida["meta"]["mode"] == "latest"
    assert saida["meta"]["eligible_ref_date"] == "2026-09-02"


def test_serving_com_data_no_dia_corrente_falha_fechado():
    """F3+F4: se o serving publicou D0, o endpoint levanta em vez de servir."""
    svc = _servico()
    listings, refs = _cenario()
    db = FakeSession(listings, refs, ref_date=HOJE)
    erro = None
    try:
        svc.get_monitoramento_preco(db, today=HOJE)
    except pm.PmaMatchError as exc:
        erro = str(exc)
    assert erro is not None
    assert "inconsistente" in erro


def test_observacao_anterior_a_d_menos_1_vem_toda_stale():
    """Pipeline atrasado aparece como atraso, nao como veredito de preco."""
    svc = _servico()
    listings, refs = _cenario(n_abaixo=3, n_acima=2)
    for li in listings:
        li["ref_date"] = date(2026, 8, 20)
    db = FakeSession(listings, refs, ref_date=date(2026, 8, 20))
    saida = svc.get_monitoramento_preco(db, today=HOJE)
    assert saida["kpis"]["stale_count"] == 5
    assert saida["kpis"]["comparable_count"] == 0
    assert saida["kpis"]["below_reference_count"] == 0
    assert all(r["difference_amount"] is None for r in saida["rows"])
    assert any("anteriores a" in a for a in saida["meta"]["warnings"])


# ---------------------------------------------------------------------------
# 10. Camada HTTP real — TestClient sobre o app FastAPI de verdade
# ---------------------------------------------------------------------------
# Prova o que o teste de unidade nao alcanca: que a borda HTTP traduz recusa de
# contrato em 422 e inconsistencia de serving em 500, com corpo sanitizado.

def _client(db):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _limpa_overrides():
    from app.main import app

    app.dependency_overrides.clear()


ROTA = "/api/v1/performance/monitoramento-preco"


def _cenario_http(n_abaixo=3, n_acima=2):
    """Cenario ancorado no D-1 do relogio REAL.

    O endpoint nao aceita `today` — de proposito: o dia operacional vem de
    `America/Sao_Paulo` em producao. Por isso o teste HTTP tem de posicionar a
    observacao em D-1 de verdade; um `ref_date` fixo cairia em `stale` (ou em
    fail-closed, se coincidisse com hoje) e o teste mediria a coisa errada.
    """
    from datetime import timedelta

    svc = _servico()
    d1 = svc.today_operacional() - timedelta(days=1)
    listings, refs = _cenario(n_abaixo=n_abaixo, n_acima=n_acima)
    for li in listings:
        li["ref_date"] = d1
    return listings, refs, d1


def test_http_200_com_envelope_completo():
    listings, refs, d1 = _cenario_http()
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    try:
        r = cli.get(ROTA)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["meta"]["mode"] == "latest"
        assert corpo["meta"]["validity_status"] == "missing"
        assert corpo["meta"]["coverage_status"] == "advertised_only"
        assert corpo["meta"]["reference_type"] == "suggested_retail_pdv"
        assert corpo["kpis"]["monitored_count"] == 5
        assert corpo["returned_count"] == 5
        assert corpo["truncated"] is False
    finally:
        _limpa_overrides()


def test_http_422_em_recusa_de_contrato_sem_eco():
    listings, refs, d1 = _cenario_http()
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    try:
        for parametro, payload in (
            ("marketplace", "shopee"),
            ("brand", "apice"),
            ("status", "infracao"),
            ("brand", "<script>alert(1)</script>"),
            ("status", "postgresql://u:senha_secreta@10.20.30.40:5432/b"),
        ):
            r = cli.get(ROTA, params={parametro: payload})
            assert r.status_code == 422, (parametro, r.status_code, r.text)
            texto = r.text.lower()
            for fragmento in ("<script", "senha_secreta", "10.20.30.40",
                              "select", "marts."):
                assert fragmento not in texto, (parametro, fragmento)
    finally:
        _limpa_overrides()


def test_http_500_em_inconsistencia_de_serving_com_corpo_fixo():
    """F7: `PmaMatchError` NAO pode virar 422 — nao e' erro do cliente."""
    from app.routers import performance as rp

    listings, refs = _cenario()
    # `ref_date` do serving em D0: o sync proibe publicar isso.
    cli = _client(FakeSession(listings, refs, ref_date=HOJE))
    try:
        r = cli.get(ROTA)
        assert r.status_code == 500, (r.status_code, r.text)
        corpo = r.json()
        assert corpo["detail"] == rp.ERRO_SERVING_INCONSISTENTE
        texto = r.text.lower()
        for fragmento in ("nan", "ref_date", "select", "marts.", "traceback",
                          "sync proibe"):
            assert fragmento not in texto, fragmento
    finally:
        _limpa_overrides()


def test_http_422_por_tamanho_sem_ecoar_o_payload():
    """F6: excesso de tamanho e' 422 com mensagem FIXA, sem eco.

    Este e' o teste que pegou o defeito: com `max_length` no `Query`, o corpo do
    422 trazia `{"input": "AAAA...200 vezes..."}`. Com o teto no corpo do
    endpoint, o corpo traz somente a constante do servico.
    """
    svc = _servico()
    listings, refs, d1 = _cenario_http()
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    esperado = {
        "brand": svc.ERRO_BRAND_TAMANHO,
        "status": svc.ERRO_STATUS_TAMANHO,
        "product_query": svc.ERRO_PRODUCT_QUERY_TAMANHO,
    }
    try:
        for parametro, tamanho in (("brand", 200), ("status", 400),
                                   ("product_query", 300)):
            r = cli.get(ROTA, params={parametro: "A" * tamanho})
            assert r.status_code == 422, (parametro, r.status_code, r.text)
            assert r.json()["detail"] == esperado[parametro], parametro
            assert "AAAA" not in r.text, parametro
    finally:
        _limpa_overrides()


def test_http_paginacao():
    listings, refs, d1 = _cenario_http(n_abaixo=4, n_acima=3)
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    try:
        r = cli.get(ROTA, params={"limit": 3, "offset": 0})
        assert r.status_code == 200
        c = r.json()
        assert c["returned_count"] == 3 and c["total_count"] == 7
        assert c["truncated"] is True
        r = cli.get(ROTA, params={"limit": 3, "offset": 6})
        assert r.json()["truncated"] is False
        # Fora do intervalo: recusado pela borda.
        assert cli.get(ROTA, params={"limit": 0}).status_code == 422
        assert cli.get(ROTA, params={"limit": 9999}).status_code == 422
        assert cli.get(ROTA, params={"offset": -1}).status_code == 422
    finally:
        _limpa_overrides()


def test_http_ref_date_e_recusado_nunca_ignorado():
    """PMA-1B: `?ref_date=...` responde 422, nao 200.

    O FastAPI ignora query parameters desconhecidos por padrao. Ignorar em
    silencio devolveria 200 e faria o consumidor crer que o filtro historico foi
    aplicado — um numero certo sob uma pergunta errada.
    """
    svc = _servico()
    listings, refs, d1 = _cenario_http()
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    try:
        r = cli.get(ROTA, params={"ref_date": "2026-08-01"})
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == svc.ERRO_REF_DATE_NAO_SUPORTADO
    finally:
        _limpa_overrides()


def test_http_ref_date_invalido_recebe_a_mesma_mensagem():
    """Nada e' interpretado nem convertido: toda entrada da a MESMA resposta."""
    svc = _servico()
    listings, refs, d1 = _cenario_http()
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    entradas = [
        "2026-08-01", "01/08/2026", "ontem", "", "0", "9999-99-99",
        "2026-08-01T00:00:00Z", "latest", "null", "-1",
        *_PAYLOADS_HOSTIS,
    ]
    try:
        vistas = set()
        for valor in entradas:
            r = cli.get(ROTA, params={"ref_date": valor})
            assert r.status_code == 422, (valor[:40], r.status_code)
            vistas.add(r.json()["detail"])
        assert vistas == {svc.ERRO_REF_DATE_NAO_SUPORTADO}, vistas
    finally:
        _limpa_overrides()


def test_http_ref_date_hostil_nao_aparece_na_resposta():
    listings, refs, d1 = _cenario_http()
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    try:
        for payload in _PAYLOADS_HOSTIS:
            r = cli.get(ROTA, params={"ref_date": payload})
            assert r.status_code == 422
            for fragmento in _FRAGMENTOS_HOSTIS:
                if fragmento in payload:
                    assert fragmento not in r.text, fragmento
    finally:
        _limpa_overrides()


def test_http_ref_date_recusado_sem_tocar_o_banco():
    """A recusa precede `_require_db` e o servico: zero consulta emitida."""
    listings, refs, d1 = _cenario_http()
    db = FakeSession(listings, refs, ref_date=d1)
    cli = _client(db)
    try:
        r = cli.get(ROTA, params={"ref_date": "2026-08-01"})
        assert r.status_code == 422
        assert db.executed == [], db.executed
    finally:
        _limpa_overrides()


def test_ref_date_nao_aparece_no_openapi():
    from app.main import app

    op = app.openapi()["paths"][ROTA]["get"]
    nomes = [p["name"] for p in op["parameters"]]
    assert "ref_date" not in nomes, nomes
    assert nomes == ["marketplace", "brand", "status", "product_query",
                     "limit", "offset"], nomes


def test_ref_date_e_oculto_e_recebido_como_texto():
    """Tipado `str`, nunca `date`: `date` faria o FastAPI validar e ECOAR."""
    trecho = _assinatura_do_endpoint()
    assert "ref_date: Optional[str] = Query(None, include_in_schema=False)" in trecho
    assert "ref_date: Optional[date]" not in trecho


def test_recusa_de_ref_date_precede_require_db_no_codigo():
    texto = ROUTER_PATH.read_text(encoding="utf-8")
    corpo = texto[texto.index('"/monitoramento-preco"'):]
    corpo = corpo[:corpo.index("mp_svc.get_monitoramento_preco")]
    i_recusa = corpo.index("ERRO_REF_DATE_NAO_SUPORTADO")
    i_db = corpo.index("_require_db(db)")
    assert i_recusa < i_db, "a recusa tem de vir antes de _require_db"


def test_http_sem_ref_date_continua_normal():
    listings, refs, d1 = _cenario_http()
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    try:
        r = cli.get(ROTA)
        assert r.status_code == 200, r.text
        assert r.json()["meta"]["mode"] == "latest"
        assert r.json()["kpis"]["monitored_count"] == 5
    finally:
        _limpa_overrides()


def test_http_payload_nao_tem_vocabulario_de_infracao():
    listings, refs, d1 = _cenario_http()
    cli = _client(FakeSession(listings, refs, ref_date=d1))
    try:
        texto = cli.get(ROTA).text.lower()
        for proibida in _PROIBIDAS:
            assert proibida not in texto, proibida
    finally:
        _limpa_overrides()


def test_valores_monetarios_saem_como_float_e_nulo_permanece_nulo():
    svc = _servico()
    listings, refs = _cenario(n_abaixo=1, n_acima=0)
    saida = svc.get_monitoramento_preco(FakeSession(listings, refs), today=HOJE)
    linha = saida["rows"][0]
    assert isinstance(linha["advertised_price"], float)
    assert isinstance(linha["suggested_retail_amount"], float)
    assert isinstance(linha["difference_amount"], float)
    for campo in ("shipping_amount", "seller_coupon_amount",
                  "platform_subsidy_amount", "checkout_price"):
        assert linha[campo] is None, campo
