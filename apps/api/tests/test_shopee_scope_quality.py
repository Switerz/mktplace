"""
Gate SH-API-2D — contrato de qualidade de escopo dos Produtos Shopee.

Cobre os SEIS eixos com uma Session falsa (sem banco). O ponto central destes
testes e provar ORTOGONALIDADE: cada eixo tem de responder por si, e um estado
ruim num eixo nao pode apagar nem inventar o estado de outro. Foi exatamente
isso que falhou antes (F5 do Gate SH-API-2B-R2), quando um unico rotulo
mutuamente exclusivo escondia a carga defasada atras da maturidade pendente.

Nenhum teste depende de julho/agosto de 2026 nem do piso 0,99 como constante
literal: os cenarios sao construidos por RAZAO contra o piso lido da config.
Trocar o piso ou o mes muda os numeros de entrada, nunca o codigo testado.
"""
from datetime import date, datetime, timezone

import pytest

from app.config import settings
from app.schemas import performance as perf_schemas
from app.services import performance_service as svc


class FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class FakeSession:
    """Devolve UMA linha de mapping e registra os parametros recebidos — a
    prova do escopo consultado e o parametro que chegou ao SQL, nao o eco."""

    def __init__(self, row):
        self._row = row
        self.params: list[dict] = []
        self.sql: list[str] = []

    def execute(self, stmt, params=None):
        self.params.append(dict(params or {}))
        self.sql.append(str(stmt))
        return FakeResult(self._row)


THRESHOLD = float(settings.shopee_maturation_threshold)
TODAY = date(2026, 9, 8)
PUB = datetime(2026, 8, 5, 14, 29, 3, tzinfo=timezone.utc)


def row(**over) -> dict:
    """Escopo saudavel por construcao; cada teste degrada UM eixo."""
    base = {
        "rows_present": 100,
        "eligible_rows": 100,
        "prod_gmv": 1000.0,
        "brands_present": 5,
        "loaded_at": PUB,
        "daily_gmv": 1000.0,
        "brands_expected": 5,
        "daily_max_date": date(2026, 6, 30),
        "last_sync_finished_at": PUB,
        "source_min_date": date(2026, 1, 1),
        "source_max_date": date(2026, 8, 1),
        "ever_loaded": True,
    }
    base.update(over)
    return base


def q(**over):
    db = FakeSession(row(**over))
    return svc.get_shopee_scope_quality(db, None, 2026, 6, today=TODAY), db


# ---------------------------------------------------------------------------
# Caso de referencia
# ---------------------------------------------------------------------------

def test_escopo_saudavel_e_definitivo_nos_seis_eixos():
    r, _ = q()
    assert r["source_status"] == "source_ever_loaded"
    assert r["load_status"] == "load_present"
    assert r["eligibility_status"] == "eligible"
    assert r["maturity_status"] == "mature"
    assert r["coverage_status"] == "coverage_ok"
    assert r["loaded_at"] == PUB.isoformat()
    assert r["definitive"] is True
    assert r["warnings"] == []


def test_payload_valida_contra_o_schema_publicado():
    r, _ = q()
    modelo = perf_schemas.ScopeQuality.model_validate(r)
    assert modelo.ref_month == "2026-06"
    assert modelo.measured.maturation_threshold == THRESHOLD


# ---------------------------------------------------------------------------
# Eixo 1 — fonte
# ---------------------------------------------------------------------------

def test_fonte_coberta_vem_de_qualquer_execucao_historica_nao_da_ultima():
    # A carga e incremental: a ultima execucao cobre so 07..08, mas 06 foi
    # coberto por uma execucao anterior. `covers_ref_month` (bool_or sobre
    # TODAS as execucoes) e quem responde — nunca a janela da ultima.
    r, _ = q(source_min_date=date(2026, 7, 1), source_max_date=date(2026, 8, 1),
             ever_loaded=True)
    assert r["source_status"] == "source_ever_loaded"


def test_fonte_nao_coberta_bloqueia_definitivo_mesmo_com_tudo_mais_ok():
    r, _ = q(ever_loaded=False)
    assert r["source_status"] == "source_never_loaded"
    assert r["definitive"] is False
    assert r["maturity_status"] == "mature"      # o eixo vizinho nao muda
    assert "shopee_produtos_fonte_nunca_carregada" in _codes(r)


def test_sem_auditoria_a_fonte_e_indeterminada_e_nao_bloqueia_mes_maduro():
    # Ausencia de historico de execucao NAO e evidencia de ausencia de fonte:
    # a maturidade medida contra a diaria ja e evidencia propria.
    r, _ = q(ever_loaded=None, source_min_date=None, source_max_date=None)
    assert r["source_status"] == "source_history_unknown"
    assert r["definitive"] is True
    assert "shopee_produtos_historico_de_carga_ausente" in _codes(r)


# ---------------------------------------------------------------------------
# Eixo 2 — carga
# ---------------------------------------------------------------------------

def test_carga_ausente_quando_nao_ha_linha():
    r, _ = q(rows_present=0, eligible_rows=0, prod_gmv=0.0, brands_present=0)
    assert r["load_status"] == "load_absent"
    assert r["definitive"] is False


def test_carga_defasada_e_provada_pela_diaria_nao_por_idade_absoluta():
    # A diaria mediu 2026-08-31, posterior a publicacao do mart (2026-08-05):
    # existe venda conhecida que o mart comprovadamente nao viu.
    r, _ = q(daily_max_date=date(2026, 8, 31))
    assert r["load_status"] == "load_behind_daily"
    assert "shopee_produtos_carga_atras_da_diaria" in _codes(r)


def test_mes_fechado_publicado_ha_muito_tempo_nao_e_defasado():
    # 34 dias de idade, mas a diaria nao registrou nada depois da publicacao:
    # idade nao e defasagem. `load_age_days` continua exposto para quem quiser
    # decidir diferente.
    r, _ = q(daily_max_date=date(2026, 6, 30))
    assert r["load_status"] == "load_present"
    assert r["load_age_days"] == 34


def test_loaded_at_cai_para_o_ultimo_sync_quando_o_escopo_esta_vazio():
    r, _ = q(rows_present=0, eligible_rows=0, prod_gmv=0.0, loaded_at=None)
    assert r["loaded_at"] == PUB.isoformat()
    assert r["load_age_days"] == 34


def test_sem_carga_e_sem_sync_a_publicacao_e_nd_nunca_zero():
    r, _ = q(rows_present=0, eligible_rows=0, prod_gmv=0.0,
             loaded_at=None, last_sync_finished_at=None)
    assert r["loaded_at"] is None
    assert r["load_age_days"] is None


# ---------------------------------------------------------------------------
# Eixo 3 — elegibilidade
# ---------------------------------------------------------------------------

def test_elegibilidade_parcial_e_apenas_informativa():
    r, _ = q(eligible_rows=70)
    assert r["eligibility_status"] == "partially_eligible"
    assert r["measured"]["excluded_zero_gmv"] == 30
    assert r["definitive"] is True                       # nao bloqueia
    assert _sev(r, "shopee_produtos_elegibilidade_parcial") == "info"


def test_linhas_carregadas_e_100pct_excluidas_e_critico():
    r, _ = q(eligible_rows=0, prod_gmv=0.0)
    assert r["eligibility_status"] == "no_eligible_rows"
    assert r["definitive"] is False
    assert _sev(r, "shopee_produtos_nenhuma_linha_elegivel") == "critical"
    # a mensagem tem de dizer QUANTAS linhas existem — "vazio" e "tudo
    # excluido" sao problemas distintos e a tela precisa distinguir.
    assert "100" in _msg(r, "shopee_produtos_nenhuma_linha_elegivel")


def test_escopo_sem_linha_nenhuma_nao_emite_o_aviso_de_100pct_excluido():
    r, _ = q(rows_present=0, eligible_rows=0, prod_gmv=0.0)
    assert "shopee_produtos_nenhuma_linha_elegivel" not in _codes(r)
    assert "shopee_produtos_carga_ausente" in _codes(r)


# ---------------------------------------------------------------------------
# Eixo 4 — maturidade
# ---------------------------------------------------------------------------

def test_share_exatamente_no_piso_e_maduro():
    r, _ = q(prod_gmv=THRESHOLD * 1000.0, daily_gmv=1000.0)
    assert r["maturity_status"] == "mature"


def test_share_logo_abaixo_do_piso_e_imaturo():
    r, _ = q(prod_gmv=(THRESHOLD - 0.01) * 1000.0, daily_gmv=1000.0)
    assert r["maturity_status"] == "materially_immature"
    assert r["definitive"] is False
    assert _sev(r, "shopee_produtos_maturacao_insuficiente") == "critical"


def test_aviso_de_imaturidade_diz_para_onde_o_numero_vai_se_mover():
    # Sem direcao o aviso e inutil para decidir: o leitor precisa saber que os
    # numeros SOBEM, senao pode interpretar imaturidade como queda de venda.
    r, _ = q(prod_gmv=700.0, daily_gmv=1000.0)
    assert "SUBIR" in _msg(r, "shopee_produtos_maturacao_insuficiente")


def test_share_acima_de_um_e_maduro_porque_as_definicoes_diferem():
    # Subtotal do item (Produtos) > GMV liquido do shop stats (diaria). Razao
    # > 1 e o regime NORMAL de um mes fechado, nao um erro.
    r, _ = q(prod_gmv=1120.0, daily_gmv=1000.0)
    assert r["maturity_status"] == "mature"
    assert r["measured"]["maturation_index"] == 1.12


def test_sem_carga_a_maturidade_e_indeterminada_e_nao_imatura():
    # Share 0 por falta de carga nao acusa a fonte: `load_absent` ja diz o que
    # se sabe. Atribuir imaturidade aqui mandaria o leitor cobrar o time errado.
    r, _ = q(rows_present=0, eligible_rows=0, prod_gmv=0.0)
    assert r["maturity_status"] == "maturity_unknown"
    assert r["measured"]["maturation_index"] is None
    assert "nao ha linha de produto carregada" in _msg(
        r, "shopee_produtos_maturacao_nao_medida")


def test_sem_referencia_na_diaria_a_maturidade_e_indeterminada():
    r, _ = q(daily_gmv=0.0)
    assert r["maturity_status"] == "maturity_unknown"
    assert r["measured"]["maturation_index"] is None
    assert "diaria" in _msg(r, "shopee_produtos_maturacao_nao_medida")


def test_maturidade_indeterminada_nunca_vira_definitivo():
    r, _ = q(daily_gmv=0.0)
    assert r["definitive"] is False


def test_piso_exposto_no_payload_para_o_veredito_ser_auditavel():
    r, _ = q()
    assert r["measured"]["maturation_threshold"] == THRESHOLD
    assert r["measured"]["completed_gmv"] == 1000.0
    assert r["measured"]["reference_gmv"] == 1000.0


# ---------------------------------------------------------------------------
# Eixo 5 — cobertura de marcas
# ---------------------------------------------------------------------------

def test_cobertura_abaixo_do_esperado_avisa_sem_bloquear():
    r, _ = q(brands_present=3, brands_expected=5)
    assert r["coverage_status"] == "coverage_below_expected"
    assert "3 de 5" in _msg(r, "shopee_produtos_cobertura_de_marcas")
    # Cobertura parcial nao invalida o que ESTA la; a tela decide se usa.
    assert r["definitive"] is True


def test_sem_marca_esperada_a_cobertura_e_indeterminada():
    r, _ = q(brands_expected=0, brands_present=0)
    assert r["coverage_status"] == "coverage_unknown"


# ---------------------------------------------------------------------------
# Ortogonalidade — o ponto do gate
# ---------------------------------------------------------------------------

def test_eixos_degradam_ao_mesmo_tempo_sem_um_apagar_o_outro():
    r, _ = q(rows_present=200, eligible_rows=0, prod_gmv=0.0,
             daily_gmv=1000.0, daily_max_date=date(2026, 8, 31),
             brands_present=2, brands_expected=5, ever_loaded=True)
    assert r["source_status"] == "source_ever_loaded"
    assert r["load_status"] == "load_behind_daily"
    assert r["eligibility_status"] == "no_eligible_rows"
    assert r["maturity_status"] == "materially_immature"
    assert r["coverage_status"] == "coverage_below_expected"
    # quatro problemas simultaneos, quatro avisos — nenhum engolido
    assert {"shopee_produtos_carga_atras_da_diaria",
            "shopee_produtos_nenhuma_linha_elegivel",
            "shopee_produtos_maturacao_insuficiente",
            "shopee_produtos_cobertura_de_marcas"} <= set(_codes(r))


def test_os_cinco_eixos_sao_campos_separados_no_payload():
    r, _ = q()
    for eixo in ("source_status", "load_status", "eligibility_status",
                 "maturity_status", "coverage_status"):
        assert eixo in r, eixo
    assert "loaded_at" in r


# ---------------------------------------------------------------------------
# Escopo consultado
# ---------------------------------------------------------------------------

def test_marca_valida_entra_como_parametro_e_filtra_os_dois_lados():
    db = FakeSession(row())
    r = svc.get_shopee_scope_quality(db, "kokeshi", 2026, 6, today=TODAY)
    assert r["brand"] == "kokeshi"
    assert db.params[0]["brand"] == "kokeshi"
    sql = db.sql[0]
    assert "AND brand = :brand" in sql            # lado Produtos
    assert "AND l.brand_key = :brand" in sql      # lado diaria


def test_marca_desconhecida_e_ignorada_igual_a_rota_que_serve_os_dados():
    # get_produtos_shopee ignora marca fora de _SH_BRANDS. Se o selo filtrasse
    # e a tabela nao, o selo descreveria outro conjunto de linhas.
    db = FakeSession(row())
    r = svc.get_shopee_scope_quality(db, "marca_que_nao_existe", 2026, 6, today=TODAY)
    assert r["brand"] is None
    assert "brand" not in db.params[0]
    assert "AND brand = :brand" not in db.sql[0]


def test_competencia_e_janela_do_mes_chegam_ao_sql():
    _, db = q()
    p = db.params[0]
    assert p["ref_month"] == date(2026, 6, 1)
    assert p["month_start"] == date(2026, 6, 1)
    assert p["month_end"] == date(2026, 6, 30)
    assert p["shopee_id"] == svc.SHOPEE_ID
    assert p["sync_source"] == svc.SHOPEE_PRODUTOS_SYNC_SOURCE


def test_consulta_vazia_nao_inventa_estado_bom():
    # Banco sem linha nenhuma (ou destino indisponivel) tem de degradar para
    # "nao sei", nunca para "esta tudo certo".
    db = FakeSession(None)
    r = svc.get_shopee_scope_quality(db, None, 2026, 6, today=TODAY)
    assert r["definitive"] is False
    assert r["load_status"] == "load_absent"
    assert r["source_status"] == "source_history_unknown"
    assert r["maturity_status"] == "maturity_unknown"


def test_ref_month_de_dezembro_fecha_a_janela_no_dia_31():
    db = FakeSession(row())
    svc.get_shopee_scope_quality(db, None, 2026, 12, today=TODAY)
    assert db.params[0]["month_end"] == date(2026, 12, 31)


# ---------------------------------------------------------------------------
# Propagacao para as rotas
# ---------------------------------------------------------------------------

def test_o_limiar_e_parametro_de_configuracao_nao_literal_no_service():
    import inspect
    fonte = inspect.getsource(svc.get_shopee_scope_quality)
    assert "settings.shopee_maturation_threshold" in fonte
    assert "0.99" not in fonte
    # e nenhum mes fica gravado na regra
    for proibido in ("2026-07", "2026-08", "julho", "agosto"):
        assert proibido not in fonte, proibido


@pytest.mark.parametrize("modelo,campo", [
    (perf_schemas.ProdutosShopeeResponse, "quality"),
    (perf_schemas.ProdutosShopeeSummaryResponse, "quality"),
    (perf_schemas.QualityResponse, "produtos_shopee_quality"),
])
def test_as_tres_respostas_publicam_o_selo(modelo, campo):
    assert campo in modelo.model_fields


@pytest.mark.parametrize("modelo", [
    perf_schemas.ProdutosShopeeResponse,
    perf_schemas.ProdutosShopeeSummaryResponse,
])
def test_as_rotas_de_produtos_publicam_a_ultima_publicacao_do_mart(modelo):
    # Era exatamente isto que faltava: o summary nunca preenchia refreshed_at.
    assert "refreshed_at" in modelo.model_fields


def test_campos_novos_sao_opcionais_para_nao_quebrar_consumidor_antigo():
    m = perf_schemas.ProdutosShopeeSummaryResponse.model_validate({
        "ref_month": "2026-06", "total_gmv": 1.0, "total_count": 1,
        "eligible_count": 1, "excluded_zero_gmv_count": 0, "buckets": [],
    })
    assert m.quality is None
    assert m.refreshed_at is None


def test_status_desconhecido_e_recusado_pelo_schema():
    # Um estado novo na API tem de QUEBRAR o consumidor no schema, em vez de
    # ser renderizado como se fosse normal.
    r, _ = q()
    r["maturity_status"] = "provavelmente_ok"
    with pytest.raises(Exception):
        perf_schemas.ScopeQuality.model_validate(r)


# ---------------------------------------------------------------------------
# Gate SH-API-2D-R/V — indice operacional de maturacao
# ---------------------------------------------------------------------------

def test_indice_maior_que_um_nao_e_truncado():
    # Truncar em 1,00 apagaria o sinal de que numerador e denominador sao
    # populacoes diferentes, e faria o numero parecer percentual.
    r, _ = q(prod_gmv=1123.4, daily_gmv=1000.0)
    assert r["measured"]["maturation_index"] == 1.1234
    assert r["maturity_status"] == "mature"


def test_indice_negativo_e_erro_e_nunca_vira_veredito():
    r, _ = q(prod_gmv=-1.0, daily_gmv=1000.0)
    assert r["maturity_status"] == "maturity_unknown"
    assert r["measured"]["maturation_index"] is None
    assert r["definitive"] is False
    assert _sev(r, "shopee_produtos_indice_invalido") == "critical"


def test_referencia_negativa_e_erro():
    r, _ = q(prod_gmv=1000.0, daily_gmv=-5.0)
    assert r["maturity_status"] == "maturity_unknown"
    assert "shopee_produtos_indice_invalido" in _codes(r)


def test_nan_e_erro_e_nao_passa_como_numero():
    # 'NaN' >= limiar e False em Python, entao sem a guarda o mes viraria
    # "imaturo" — um veredito inventado a partir de lixo.
    r, _ = q(prod_gmv=float("nan"), daily_gmv=1000.0)
    assert r["maturity_status"] == "maturity_unknown"
    assert "shopee_produtos_indice_invalido" in _codes(r)


def test_entrada_invalida_nao_emite_aviso_de_imaturidade():
    # Os dois avisos sao mutuamente exclusivos: dizer "imatura" sobre uma
    # entrada que nem foi calculada seria afirmar o que nao se mediu.
    r, _ = q(prod_gmv=float("nan"), daily_gmv=1000.0)
    assert "shopee_produtos_maturacao_insuficiente" not in _codes(r)
    assert "shopee_produtos_maturacao_nao_medida" not in _codes(r)


def test_funcao_pura_do_indice_levanta_em_entrada_impossivel():
    import pytest as _pytest
    with _pytest.raises(svc.ScopeQualityInputError):
        svc.compute_maturation_index(-1.0, 10.0)
    with _pytest.raises(svc.ScopeQualityInputError):
        svc.compute_maturation_index(10.0, float("nan"))
    assert svc.compute_maturation_index(10.0, 0.0) is None
    assert svc.compute_maturation_index(12.0, 10.0) == 1.2


def test_o_indice_sempre_viaja_com_a_explicacao():
    # Proibicao do gate: nunca expor o valor bruto sem dizer o que ele e.
    r, _ = q()
    nota = r["measured"]["maturation_index_note"]
    assert "percentual de conclusao" in nota
    assert "ACIMA de 1,00" in nota


def test_aviso_de_imaturidade_carrega_indice_limiar_e_explicacao():
    r, _ = q(prod_gmv=754.0, daily_gmv=1000.0)
    msg = _msg(r, "shopee_produtos_maturacao_insuficiente")
    assert "0.7540" in msg                    # indice, com 4 casas
    assert "limiar heuristico" in msg
    assert "percentual de conclusao" in msg   # a explicacao acompanha
    assert "SUBIR" in msg
    assert "%" not in msg.replace("%", "", 0) or "0.7540" in msg


def test_nenhuma_mensagem_formata_o_indice_como_percentual():
    for kwargs in ({"prod_gmv": 754.0, "daily_gmv": 1000.0},
                   {"prod_gmv": 0.0, "daily_gmv": 1000.0}):
        r, _ = q(**kwargs)
        for w in r["warnings"]:
            assert "%" not in w["message"], f"{w['code']} formata como %: {w['message']}"


# ---------------------------------------------------------------------------
# Gate SH-API-2D-R/V — nomes nao-temporais
# ---------------------------------------------------------------------------

def test_carga_presente_nao_afirma_frescor():
    # `load_present` e ausencia de evidencia de atraso, nunca prova de estar
    # em dia: um mart publicado ha 34 dias continua `load_present` para um mes
    # fechado, e o nome nao pode dizer "atual".
    r, _ = q(daily_max_date=date(2026, 6, 30))
    assert r["load_status"] == "load_present"
    assert r["load_age_days"] == 34
    assert "current" not in r["load_status"]
    assert "atual" not in r["load_status"]


def test_carga_atras_da_diaria_declara_a_propria_evidencia():
    r, _ = q(daily_max_date=date(2026, 8, 31))
    assert r["load_status"] == "load_behind_daily"
    assert "diaria" in _msg(r, "shopee_produtos_carga_atras_da_diaria")


def test_historico_da_fonte_nao_afirma_que_a_fonte_esta_atualizada():
    r, _ = q(ever_loaded=True)
    assert r["source_status"] == "source_ever_loaded"
    # nenhum aviso e emitido, e o nome nao promete frescor
    assert not any(w["code"].startswith("shopee_produtos_fonte") for w in r["warnings"])


def test_nunca_carregada_e_diferente_de_historico_desconhecido():
    nunca, _ = q(ever_loaded=False)
    desconhecido, _ = q(ever_loaded=None)
    assert nunca["source_status"] == "source_never_loaded"
    assert desconhecido["source_status"] == "source_history_unknown"
    assert nunca["definitive"] is False          # contradicao explicita bloqueia
    assert desconhecido["definitive"] is True    # indeterminacao nao bloqueia


def test_o_service_nao_usa_mais_vocabulario_temporal_nos_estados():
    import inspect
    fonte = inspect.getsource(svc.get_shopee_scope_quality)
    for proibido in ('"load_current"', '"load_stale"', '"source_covered"',
                     '"completed_share"', '"maturity_floor"'):
        assert proibido not in fonte, proibido


def test_loaded_at_e_rotulado_como_publicacao_no_mart():
    import inspect
    fonte = inspect.getsource(svc.get_shopee_scope_quality)
    assert "publicacao NO MART" in fonte or "publicacao do mart" in fonte


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _codes(r) -> list[str]:
    return [w["code"] for w in r["warnings"]]


def _one(r, code) -> dict:
    achados = [w for w in r["warnings"] if w["code"] == code]
    assert len(achados) == 1, f"{code}: {len(achados)} ocorrencias em {_codes(r)}"
    return achados[0]


def _sev(r, code) -> str:
    return _one(r, code)["severity"]


def _msg(r, code) -> str:
    return _one(r, code)["message"]
