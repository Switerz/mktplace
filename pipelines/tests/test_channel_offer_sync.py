"""Gate PMA-2C1A — sync inerte: barreira do --apply, stale por conta, PII.

O teste mais importante deste arquivo e' o que prova que `--apply` NAO publica:
a barreira consulta o banco real (revisao Alembic + existencia da relacao) e as
duas condicoes sao obrigatorias.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from apps.api.app.services import pma_domain as dom  # noqa: F401  (contrato)
from pipelines import channel_offer_sync as cos

AGORA = datetime(2026, 9, 15, 9, 7, 1, tzinfo=timezone.utc)


class CursorFalso:
    def __init__(self, respostas):
        self._respostas = list(respostas)
        self._atual = None

    def execute(self, sql, params=None):
        self._atual = self._respostas.pop(0)

    def fetchall(self):
        return self._atual

    def fetchone(self):
        return self._atual[0] if self._atual else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConexaoFalsa:
    """Modela o Neon nas duas perguntas da barreira, nesta ordem."""

    def __init__(self, revisoes, relacao):
        self._respostas = [[(r,) for r in revisoes], [(relacao,)]]
        self.escreveu = False

    def cursor(self):
        return CursorFalso(self._respostas)


# ---------------------------------------------------------------------------
# Barreira do --apply
# ---------------------------------------------------------------------------


def test_apply_recusado_enquanto_a_migration_nao_existir():
    conn = ConexaoFalsa(revisoes=["015"], relacao=None)
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)
    assert conn.escreveu is False


def test_apply_recusado_mesmo_com_a_tabela_criada_a_mao():
    """Tabela fora do Alembic nao autoriza: o schema pertence a migration."""
    conn = ConexaoFalsa(revisoes=["015"], relacao="marts.fact_channel_offer_observation")
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)


def test_apply_recusado_com_carimbo_sem_relacao():
    """Stamp manual sem DDL tambem nao autoriza."""
    conn = ConexaoFalsa(revisoes=["017"], relacao=None)
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)


def test_apply_so_autoriza_com_as_DUAS_provas():
    """A revisao do PMA e' a 017; a 016 pertence a frente Full."""
    conn = ConexaoFalsa(revisoes=["017"], relacao="marts.fact_channel_offer_observation")
    cos.assert_apply_authorized(conn)  # nao levanta


def test_cli_recusa_apply_sem_abrir_conexao(capsys):
    codigo = cos.main(["--marketplace", "shopee", "--apply"])
    assert codigo == cos.EXIT_REFUSED
    assert "RECUSADO" in capsys.readouterr().err


def test_cli_diagnose_e_o_modo_padrao():
    assert cos.main(["--marketplace", "tiktok"]) == cos.EXIT_OK


def test_cli_recusa_o_ml_como_canal():
    """O ML vive na fato antiga; uma oferta nunca existe nas duas."""
    with pytest.raises(SystemExit):
        cos.main(["--marketplace", "ml"])


def _codigo_executavel(modulo) -> str:
    """Fonte do modulo SEM docstrings e SEM comentarios.

    A varredura precisa olhar so' o que executa: as docstrings deste modulo
    citam `CREATE TABLE` justamente para explicar por que ele nao faz DDL, e um
    scan ingenuo reprovaria a documentacao em vez do codigo.
    """
    import ast
    import pathlib
    import tokenize

    caminho = pathlib.Path(modulo.__file__)
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    linhas_docstring: set[int] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            continue
        corpo = getattr(no, "body", None)
        if not corpo:
            continue
        primeiro = corpo[0]
        if (isinstance(primeiro, ast.Expr)
                and isinstance(primeiro.value, ast.Constant)
                and isinstance(primeiro.value.value, str)):
            linhas_docstring.update(
                range(primeiro.lineno, (primeiro.end_lineno or primeiro.lineno) + 1))

    linhas_comentario: set[int] = set()
    with caminho.open("rb") as fh:
        for token in tokenize.tokenize(fh.readline):
            if token.type == tokenize.COMMENT:
                linhas_comentario.add(token.start[0])

    fonte = caminho.read_text(encoding="utf-8").splitlines()
    return "\n".join(
        linha for numero, linha in enumerate(fonte, start=1)
        if numero not in linhas_docstring and numero not in linhas_comentario
    )


def test_modulo_nao_contem_escrita_nem_ddl():
    executavel = _codigo_executavel(cos).upper()
    for proibido in ("CREATE TABLE", "INSERT INTO", "UPDATE ", "DELETE FROM",
                     "TRUNCATE", "COPY ", "DROP "):
        assert proibido not in executavel, proibido


def test_a_varredura_enxerga_o_codigo_e_nao_so_a_documentacao():
    """Contraprova: o filtro nao pode esvaziar o arquivo e passar por vacuidade."""
    executavel = _codigo_executavel(cos)
    assert "def build_shopee_records" in executavel
    assert "SET TRANSACTION READ ONLY" in executavel
    assert "CREATE TABLE" not in executavel.upper()


# ---------------------------------------------------------------------------
# Stale por conta
# ---------------------------------------------------------------------------


def test_stale_por_conta_difere_de_stale_global():
    """As contas terminam em lotes distintos; o maximo global reprova todas."""
    apice = cos.AccountClock("shopee", "apice", AGORA - timedelta(seconds=16))
    rituaria = cos.AccountClock("shopee", "rituaria", AGORA)
    linha_apice = AGORA - timedelta(seconds=16)
    # Contra o relogio da PROPRIA conta a linha esta em dia.
    assert apice.snapshot_status_for(linha_apice) == dom.SNAPSHOT_CURRENT
    # Contra o maximo GLOBAL (o da rituaria) ela apareceria como atrasada.
    assert rituaria.snapshot_status_for(linha_apice) == dom.SNAPSHOT_STALE


def test_linha_anterior_ao_watermark_da_propria_conta_e_stale():
    relogio = cos.AccountClock("shopee", "apice", AGORA)
    assert relogio.snapshot_status_for(AGORA - timedelta(days=1)) == dom.SNAPSHOT_STALE


def test_conta_que_nao_rodou_nao_produz_absent():
    """`absent` afirma remocao; aqui so' ha desconhecimento."""
    relogio = cos.AccountClock("shopee", "lescent", watermark_at=None)
    assert relogio.snapshot_status_for(AGORA) == dom.SNAPSHOT_ACCOUNT_DID_NOT_RUN
    assert relogio.snapshot_status_for(AGORA) != dom.SNAPSHOT_ABSENT


def test_fotografia_incompleta_nunca_produz_absent():
    relogio = cos.AccountClock("shopee", "apice", AGORA, complete=False)
    assert relogio.snapshot_status_for(AGORA) == dom.SNAPSHOT_PARTIAL_LOAD
    assert relogio.snapshot_status_for(AGORA) != dom.SNAPSHOT_ABSENT


def test_absent_nao_e_produzido_por_nenhum_caminho_desta_rodada():
    """So' sera' afirmavel com fotografia completa registrada pela captura nova."""
    combinacoes = [
        cos.AccountClock("shopee", "a", AGORA),
        cos.AccountClock("shopee", "a", AGORA, complete=False),
        cos.AccountClock("shopee", "a", None),
    ]
    for relogio in combinacoes:
        for instante in (AGORA, AGORA - timedelta(days=1), None):
            assert relogio.snapshot_status_for(instante) != dom.SNAPSHOT_ABSENT


# ---------------------------------------------------------------------------
# Transformacao
# ---------------------------------------------------------------------------


def _linha_shopee(**kw):
    base = {
        "is_model": False, "has_model": False, "shop_account": "apice",
        "item_id": 900, "model_id": None, "brand": "apice",
        "seller_sku": "20052", "gtin": "7898652874765", "listing_title": "Creme",
        "is_kit": False, "internal_is_kit": None, "internal_has_bom": None,
        "is_active": True, "current_price": Decimal("49.63"),
        "original_price": Decimal("49.90"), "promotion_id": None,
        "discount_pct": None, "ingested_at": AGORA,
    }
    base.update(kw)
    return base


def _relogios():
    return {"apice": cos.AccountClock("shopee", "apice", AGORA)}


def test_pai_com_variacao_nao_gera_registro():
    linhas = [_linha_shopee(is_model=False, has_model=True)]
    assert cos.build_shopee_records(linhas, _relogios()) == []


def test_modelo_gera_registro_com_chave_composta():
    linhas = [_linha_shopee(is_model=True, has_model=True, model_id=7)]
    r = cos.build_shopee_records(linhas, _relogios())[0]
    assert r["offer_key"] == "900:7"
    assert r["model_id"] == "7"
    assert r["parent_item_id"] == "900"


def test_pai_simples_e_modelo_nao_colidem_na_chave():
    linhas = [_linha_shopee(item_id=900),
              _linha_shopee(item_id=901, is_model=True, has_model=True, model_id=7)]
    registros = cos.build_shopee_records(linhas, _relogios())
    cos.assert_offer_keys_unique(registros)
    assert {r["offer_key"] for r in registros} == {"900", "901:7"}


def test_flag_nativa_da_shopee_vira_kit_confirmed_no_registro():
    r = cos.build_shopee_records([_linha_shopee(is_kit=True)], _relogios())[0]
    assert r["product_type"] == dom.PRODUCT_KIT_CONFIRMED
    assert r["product_type_source"] == dom.SOURCE_CHANNEL_FLAG


def test_registro_shopee_declara_snapshot_current_e_nao_serie_diaria():
    r = cos.build_shopee_records([_linha_shopee()], _relogios())[0]
    assert r["observation_mode"] == dom.OBSERVATION_MODE_SNAPSHOT_CURRENT


def test_shopee_usa_current_price_e_jamais_inflated():
    r = cos.build_shopee_records([_linha_shopee()], _relogios())[0]
    assert r["observed_price_source"] == "current_price"
    assert r["observed_price"] == Decimal("49.63")
    assert "inflated_current_price" not in r
    assert "inflated_original_price" not in r


def test_tiktok_nunca_cai_para_preco_cheio():
    linhas = [{"sku_id": "1731", "product_id": "1730", "brand": "apice",
               "seller_sku": "AP01", "listing_title": "Serum",
               "internal_is_kit": None, "internal_has_bom": None,
               "is_active": True, "sale_price": Decimal("57.00"),
               "fetched_at": AGORA, "shop_account": "tiktok"}]
    r = cos.build_tiktok_records(linhas, AGORA.date(), {})[0]
    assert r["observed_price_source"] == "sale_price"
    assert r["list_price"] is None
    assert r["promo_context"] == dom.PROMO_UNAVAILABLE


def test_tiktok_nao_inventa_ean():
    linhas = [{"sku_id": "1731", "product_id": "1730", "brand": "apice",
               "seller_sku": "AP01", "listing_title": "Serum",
               "internal_is_kit": None, "internal_has_bom": None,
               "is_active": True, "sale_price": Decimal("57.00"),
               "fetched_at": AGORA, "shop_account": "tiktok"}]
    assert cos.build_tiktok_records(linhas, AGORA.date(), {})[0]["gtin"] is None


def test_tiktok_usa_a_data_da_fonte_sem_aproximar():
    from datetime import date
    dia = date(2026, 9, 15)
    linhas = [{"sku_id": "1", "product_id": "0", "brand": "apice",
               "seller_sku": "A", "listing_title": "x", "internal_is_kit": None,
               "internal_has_bom": None, "is_active": True,
               "sale_price": Decimal("1"), "fetched_at": AGORA,
               "shop_account": "tiktok"}]
    assert cos.build_tiktok_records(linhas, dia, {})[0]["observed_date"] == dia


def test_gocase_marcada_fora_do_escopo_de_negocio():
    linhas = [{"sku_id": "1", "product_id": "0", "brand": "gocase",
               "seller_sku": "G1", "listing_title": "Capinha",
               "internal_is_kit": None, "internal_has_bom": None,
               "is_active": True, "sale_price": Decimal("39.90"),
               "fetched_at": AGORA, "shop_account": "tiktok"}]
    r = cos.build_tiktok_records(linhas, AGORA.date(), {})[0]
    assert r["business_scope"] == dom.BUSINESS_SCOPE_OUT


def test_transformacao_e_deterministica():
    linhas = [_linha_shopee(item_id=i) for i in range(20)]
    assert (cos.build_shopee_records(linhas, _relogios())
            == cos.build_shopee_records(linhas, _relogios()))


# ---------------------------------------------------------------------------
# PII
# ---------------------------------------------------------------------------


def test_nenhum_campo_do_contrato_sugere_dado_pessoal():
    for coluna in cos.RECORD_COLUMNS:
        for token in cos.FORBIDDEN_FIELD_TOKENS:
            assert token not in coluna.lower(), coluna


def test_assert_no_pii_reprova_campo_pessoal():
    with pytest.raises(cos.ChannelSyncError):
        cos.assert_no_pii([{"offer_key": "1", "buyer_name": "x"}])


def test_assert_no_pii_aceita_o_registro_real():
    r = cos.build_shopee_records([_linha_shopee()], _relogios())
    cos.assert_no_pii(r)
    assert set(r[0]) == set(cos.RECORD_COLUMNS)


def test_mensagem_de_erro_nao_vaza_detalhe_do_driver():
    msg = cos._sanitize(RuntimeError("host=10.0.0.1 user=admin password=segredo"))
    for vazamento in ("10.0.0.1", "admin", "segredo", "password"):
        assert vazamento not in msg


# ---------------------------------------------------------------------------
# Adaptadores reais — contraprovas do Gate PMA-2C1A-R
# ---------------------------------------------------------------------------


def test_nenhuma_consulta_usa_select_estrela():
    for nome in dir(cos):
        if not nome.startswith("SQL_"):
            continue
        sql = getattr(cos, nome)
        assert "SELECT *" not in sql.upper(), nome
        assert "select *" not in sql.lower(), nome


def test_nenhuma_consulta_toca_pedido_comprador_ou_pagamento():
    """A comparacao de preco nao precisa de nenhuma dessas relacoes."""
    proibidas = ("_orders", "_order_items", "_payments", "_settlements",
                 "_customer", "_buyer", "affiliate", "creator")
    for nome in dir(cos):
        if not nome.startswith("SQL_"):
            continue
        sql = getattr(cos, nome).lower()
        for relacao in proibidas:
            assert relacao not in sql, (nome, relacao)


def test_a_consulta_de_modelos_nao_seleciona_preco_do_pai():
    """CONTRAPROVA: modelo herdando preco do pai deve falhar.

    O pai COM variacao tem `current_price` NULO em 338/338 — herdar dele seria
    herdar NULL e transformar 371 ofertas validas em `invalid_channel_price`.
    """
    sql = cos.SQL_SHOPEE_MODELS
    assert "m.current_price" in sql
    assert "m.original_price" in sql
    assert "p.current_price" not in sql
    assert "p.original_price" not in sql


def test_nenhuma_consulta_le_campos_inflated():
    """CONTRAPROVA: uso de inflated price deve falhar."""
    for nome in dir(cos):
        if not nome.startswith("SQL_"):
            continue
        sql = getattr(cos, nome).lower()
        for proibido in dom.FORBIDDEN_PRICE_FIELDS:
            assert proibido not in sql, (nome, proibido)


def test_o_watermark_e_agrupado_por_conta_e_nunca_global():
    """CONTRAPROVA: `MAX()` global sem GROUP BY deve falhar."""
    sql = cos.SQL_SHOPEE_ACCOUNT_CLOCKS
    assert "GROUP BY shop_account" in sql
    assert "max(ingested_at)" in sql
    # o agregado tem de estar sempre acompanhado do agrupamento por conta
    assert sql.upper().count("GROUP BY") == 1


def test_o_watermark_cobre_as_duas_tabelas_da_conta():
    """Modelos fecham ~25s depois dos pais: o watermark precisa dos dois."""
    sql = cos.SQL_SHOPEE_ACCOUNT_CLOCKS
    assert "stg_shopee_products" in sql
    assert "stg_shopee_product_models" in sql
    assert "UNION ALL" in sql


def test_uma_conta_atualizada_nao_esconde_outra_parada():
    parada = cos.AccountClock("shopee", "lescent", watermark_at=None)
    ativa = cos.AccountClock("shopee", "apice", watermark_at=AGORA)
    assert parada.snapshot_status_for(AGORA) == dom.SNAPSHOT_ACCOUNT_DID_NOT_RUN
    assert ativa.snapshot_status_for(AGORA) == dom.SNAPSHOT_CURRENT


def test_observed_date_usa_o_dia_da_fotografia_em_brt():
    """Uma execucao produz UMA data, nao nove."""
    from datetime import timedelta
    relogios = {"apice": cos.AccountClock("shopee", "apice", AGORA)}
    linhas = [
        _linha_shopee(item_id=1, ingested_at=AGORA),
        _linha_shopee(item_id=2, ingested_at=AGORA - timedelta(days=18)),
    ]
    registros = cos.build_shopee_records(linhas, relogios)
    assert len({r["observed_date"] for r in registros}) == 1
    # o carimbo proprio da linha nao se perde
    assert registros[0]["observed_at"] != registros[1]["observed_at"]
    assert registros[1]["snapshot_status"] == dom.SNAPSHOT_STALE


def test_observed_date_e_dia_civil_brasileiro_nao_utc():
    from datetime import datetime, timezone
    meia_noite_utc = datetime(2026, 9, 15, 2, 30, tzinfo=timezone.utc)
    relogios = {"apice": cos.AccountClock("shopee", "apice", meia_noite_utc)}
    r = cos.build_shopee_records(
        [_linha_shopee(ingested_at=meia_noite_utc)], relogios)[0]
    assert str(r["observed_date"]) == "2026-09-14"


def test_tiktok_nunca_fabrica_data():
    assert "snapshot_date = %(snapshot_date)s" in cos.SQL_TIKTOK_OFFERS
    assert "current_date" not in cos.SQL_TIKTOK_OFFERS.lower()
    assert "now()" not in cos.SQL_TIKTOK_OFFERS.lower()
    assert "interval" not in cos.SQL_TIKTOK_OFFERS.lower()


def test_sinal_interno_indisponivel_e_none_nao_false():
    """`None` mantem `product_type_unknown`; `False` viraria `no_kit_signal`."""
    assert cos._internal_signals(None, "apice", "X") == (None, None)
    catalogo = {"map": {}, "ambiguous": {("apice", "AMB")}, "dim": {}, "bom": set()}
    assert cos._internal_signals(catalogo, "apice", "AMB") == (None, None)
    assert cos._internal_signals(catalogo, "apice", "NAOEXISTE") == (None, None)


def test_a_migration_do_pma_e_017_porque_full_reservou_a_016():
    """Duas revisoes com `down_revision = 015` produziriam heads concorrentes."""
    assert cos.REQUIRED_MIGRATION == "017"
    assert cos.BLOCKING_MIGRATION_OWNED_BY_OTHER_TRACK == "016"
    assert cos.REQUIRED_MIGRATION != cos.BLOCKING_MIGRATION_OWNED_BY_OTHER_TRACK


def test_apply_continua_recusado_com_a_016_de_full_aplicada():
    """A 016 de Full nao autoriza o PMA: ela cria outra tabela."""
    conn = ConexaoFalsa(revisoes=["016"], relacao=None)
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)


def test_publisher_nao_abre_sessao_gravavel():
    """CONTRAPROVA: `_read_only` precisa impor readonly no servidor."""
    import inspect
    fonte = inspect.getsource(cos._read_only)
    assert "readonly=True" in fonte
    assert "SET TRANSACTION READ ONLY" in fonte
    assert "readonly=False" not in fonte
    assert "autocommit=True" not in fonte


def test_nenhuma_funcao_do_modulo_abre_conexao_gravavel():
    codigo = _codigo_executavel(cos)
    assert codigo.count("psycopg2.connect") == 1, "so' `_read_only` conecta"
    assert "set_session(readonly=True" in codigo.replace(" ", "").replace(
        "set_session(readonly=True", "set_session(readonly=True")
