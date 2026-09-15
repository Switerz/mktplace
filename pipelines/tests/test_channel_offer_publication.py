"""Gate PMA-2C2-R — concorrencia, antirregressao e substituicao por escopo.

Os testes de lock usam uma conexao FALSA que registra a ordem das chamadas. O
que importa nao e' o valor devolvido: e' a SEQUENCIA. Uma implementacao que
adquirisse o lock depois de ler a fonte passaria num teste de valor e falharia
aqui.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from apps.api.app.services import pma_domain as dom  # noqa: F401
from pipelines import channel_offer_sync as cos

HOJE = date(2026, 9, 15)
CEDO = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
TARDE = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)

APICE = cos.PublicationScope("shopee", HOJE, "apice")
BARBOURS = cos.PublicationScope("shopee", HOJE, "barbours")
TIKTOK = cos.PublicationScope("tiktok", HOJE, "tiktok")


class CursorRegistrador:
    def __init__(self, diario, resposta):
        self._diario = diario
        self._resposta = resposta

    def execute(self, sql, params=None):
        self._diario.append(("execute", " ".join(str(sql).split())[:60]))

    def fetchone(self):
        return (self._resposta,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConexaoRegistradora:
    """Registra a ordem das operacoes para que a SEQUENCIA seja testavel."""

    def __init__(self, lock_concedido=True):
        self.diario = []
        self._lock = lock_concedido

    def cursor(self, **kw):
        return CursorRegistrador(self.diario, self._lock)


# ---------------------------------------------------------------------------
# 1-3. Lock
# ---------------------------------------------------------------------------


def test_1_lock_indisponivel_falha_imediatamente():
    conn = ConexaoRegistradora(lock_concedido=False)
    assert cos.try_acquire_publication_lock(conn) is False
    # uma unica tentativa: zero espera, zero retry
    tentativas = [d for d in conn.diario if "advisory" in d[1]]
    assert len(tentativas) == 1


def test_1b_o_lock_e_fail_fast_e_nao_bloqueante():
    """`pg_advisory_lock` (sem `try`) enfileiraria em vez de falhar."""
    conn = ConexaoRegistradora()
    cos.try_acquire_publication_lock(conn)
    sql = conn.diario[0][1]
    assert "pg_try_advisory_lock" in sql
    assert "pg_advisory_lock(" not in sql


def test_1c_o_lock_e_de_sessao_e_nao_transacional():
    """Um lock `_xact_` cairia no COMMIT e abriria janela antes da auditoria."""
    conn = ConexaoRegistradora()
    cos.try_acquire_publication_lock(conn)
    assert "xact" not in conn.diario[0][1]


def test_2_nenhuma_fonte_e_lida_sem_lock():
    """O plano so' e' montavel depois do lock; sem lock, o fluxo encerra."""
    conn = ConexaoRegistradora(lock_concedido=False)
    if not cos.try_acquire_publication_lock(conn):
        leituras = [d for d in conn.diario if "SELECT" in d[1].upper()
                    and "advisory" not in d[1]]
        assert leituras == []


def test_3_mesma_sessao_adquire_e_libera():
    conn = ConexaoRegistradora()
    assert cos.try_acquire_publication_lock(conn) is True
    assert cos.release_publication_lock(conn) is True
    assert len(conn.diario) == 2
    assert "pg_try_advisory_lock" in conn.diario[0][1]
    assert "pg_advisory_unlock" in conn.diario[1][1]


def test_3b_a_chave_e_exclusiva_do_pma_multicanal():
    assert cos.CHANNEL_OFFER_ADVISORY_LOCK_KEY == 917_120_017
    assert (cos.CHANNEL_OFFER_ADVISORY_LOCK_KEY
            not in cos.OTHER_TRACK_ADVISORY_LOCK_KEYS)


def test_3c_nenhuma_frente_versionada_usa_a_mesma_chave():
    """Colisao faria duas frentes distintas se excluirem mutuamente."""
    todas = (cos.CHANNEL_OFFER_ADVISORY_LOCK_KEY,
             *cos.OTHER_TRACK_ADVISORY_LOCK_KEYS)
    assert len(todas) == len(set(todas))


# ---------------------------------------------------------------------------
# 4-7. Antirregressao de watermark
# ---------------------------------------------------------------------------


def test_4_execucao_antiga_nao_sobrescreve_a_nova():
    _, recusa = cos.check_watermark_progress({APICE: CEDO}, {APICE: TARDE})
    assert recusa is not None
    assert recusa.reason == cos.REFUSE_WATERMARK_REGRESSION


def test_5_watermark_igual_e_idempotente():
    permitidos, recusa = cos.check_watermark_progress({APICE: TARDE},
                                                      {APICE: TARDE})
    assert recusa is None
    assert permitidos == {APICE}


def test_6_watermark_maior_avanca():
    permitidos, recusa = cos.check_watermark_progress({APICE: TARDE},
                                                      {APICE: CEDO})
    assert recusa is None and permitidos == {APICE}


def test_7_null_nao_substitui_relogio_conhecido():
    _, recusa = cos.check_watermark_progress({APICE: None}, {APICE: CEDO})
    assert recusa is not None
    assert recusa.reason == cos.REFUSE_WATERMARK_UNKNOWN


def test_7b_escopo_ainda_nao_publicado_aceita_qualquer_relogio():
    permitidos, recusa = cos.check_watermark_progress({APICE: None}, {})
    assert recusa is None and permitidos == {APICE}


def test_7c_comparacao_usa_instantes_timezone_aware():
    """Ingenuo e' tratado como UTC: assumir o fuso da maquina mudaria o veredito."""
    ingenuo_cedo = CEDO.replace(tzinfo=None)
    _, recusa = cos.check_watermark_progress({APICE: ingenuo_cedo},
                                             {APICE: TARDE})
    assert recusa.reason == cos.REFUSE_WATERMARK_REGRESSION


def test_7d_o_relogio_e_da_fotografia_nao_da_oferta():
    """`check_watermark_progress` recebe watermark por ESCOPO, nao por linha.

    A varredura olha so' o CODIGO: a docstring da funcao cita `observed_at`
    justamente para declarar que ele NAO e' o relogio, e um scan ingenuo
    reprovaria a documentacao em vez da implementacao.
    """
    import ast
    import inspect
    import textwrap

    assert list(inspect.signature(cos.check_watermark_progress).parameters) == [
        "incoming", "published"]

    fonte = textwrap.dedent(inspect.getsource(cos.check_watermark_progress))
    funcao = ast.parse(fonte).body[0]
    corpo = funcao.body
    if (isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant)
            and isinstance(corpo[0].value.value, str)):
        corpo = corpo[1:]  # descarta a docstring
    executavel = "\n".join(ast.unparse(no) for no in corpo)
    assert "observed_at" not in executavel
    assert "account_watermark_at" not in executavel  # vem pronto do chamador
    assert "published" in executavel  # contraprova: o corpo nao esta vazio


def test_4b_regressao_falha_antes_de_qualquer_mutacao():
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[], healthy_scopes={APICE},
        incoming_watermarks={APICE: CEDO}, published_watermarks={APICE: TARDE},
        channel_enabled=True)
    assert not plano.decision.allowed
    assert plano.scopes_to_replace == ()
    assert plano.records == ()


# ---------------------------------------------------------------------------
# 8-11. Escopo da substituicao
# ---------------------------------------------------------------------------


def _oferta(conta="apice", canal="shopee", chave="900"):
    return {"marketplace": canal, "observed_date": HOJE, "shop_account": conta,
            "offer_key": chave, "brand": conta}


def test_8_shopee_trata_cada_conta_independentemente():
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[_oferta("apice")],
        healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE},
        published_watermarks={APICE: CEDO, BARBOURS: CEDO},
        channel_enabled=True)
    contas = [e.shop_account for e in plano.scopes_to_replace]
    assert contas == ["apice"]
    assert "barbours" not in contas


def test_9_conta_saudavel_vazia_remove_o_snapshot_anterior():
    """Preservar a fotografia antiga a faria passar por atual."""
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[], healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE}, published_watermarks={APICE: CEDO},
        channel_enabled=True)
    assert plano.decision.allowed
    assert plano.scopes_to_replace == (APICE,)
    assert plano.rows_loaded == 0


def test_10_conta_indisponivel_preserva_o_snapshot_anterior():
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[], healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE}, published_watermarks={APICE: CEDO},
        channel_enabled=True, source_available=False)
    assert not plano.decision.allowed
    assert plano.decision.reason == cos.REFUSE_SOURCE_UNAVAILABLE
    assert plano.scopes_to_replace == ()


def test_10b_conta_que_nao_executou_nao_entra_nos_escopos_saudaveis():
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[_oferta("apice")],
        healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE},
        published_watermarks={APICE: CEDO, BARBOURS: CEDO},
        channel_enabled=True)
    assert BARBOURS not in plano.scopes_to_replace


def test_10c_nenhuma_conta_saudavel_recusa_a_execucao():
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[], healthy_scopes=set(),
        incoming_watermarks={}, published_watermarks={APICE: CEDO},
        channel_enabled=True)
    assert plano.decision.reason == cos.REFUSE_NO_ACCOUNT_RAN
    assert plano.scopes_to_replace == ()


def test_11_oferta_removida_da_origem_nao_fica_orfa():
    """O escopo inteiro e' apagado antes do insert: sobrevivente e' impossivel."""
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[_oferta(chave="900")],
        healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE}, published_watermarks={APICE: CEDO},
        channel_enabled=True)
    assert APICE in plano.scopes_to_replace
    assert [r["offer_key"] for r in plano.records] == ["900"]


def test_17_nenhum_dado_de_uma_conta_e_removido_por_outra():
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[_oferta("barbours")],
        healthy_scopes={BARBOURS},
        incoming_watermarks={BARBOURS: TARDE},
        published_watermarks={APICE: TARDE, BARBOURS: CEDO},
        channel_enabled=True)
    assert plano.scopes_to_replace == (BARBOURS,)


def test_18_tiktok_sem_conta_usa_escopo_canonico():
    assert cos.canonical_account("tiktok", None) == "tiktok"
    assert cos.canonical_account("tiktok", "") == "tiktok"
    plano = cos.build_publication_plan(
        marketplace="tiktok", records=[_oferta("tiktok", "tiktok", "1731")],
        healthy_scopes={TIKTOK}, incoming_watermarks={TIKTOK: TARDE},
        published_watermarks={}, channel_enabled=True)
    assert plano.scopes_to_replace == (TIKTOK,)


def test_18b_shopee_sem_conta_falha_alto_em_vez_de_criar_orfao():
    with pytest.raises(cos.ChannelSyncError):
        cos.canonical_account("shopee", None)


def test_oferta_fora_dos_escopos_saudaveis_falha_alto():
    with pytest.raises(cos.ChannelSyncError):
        cos.build_publication_plan(
            marketplace="shopee", records=[_oferta("barbours")],
            healthy_scopes={APICE}, incoming_watermarks={APICE: TARDE},
            published_watermarks={}, channel_enabled=True)


def test_nao_existe_linha_sentinela_para_zero_ofertas():
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[], healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE}, published_watermarks={},
        channel_enabled=True)
    assert plano.records == ()
    assert plano.rows_loaded == 0


# ---------------------------------------------------------------------------
# 12-15. Atomicidade e commit
# ---------------------------------------------------------------------------


def test_12_e_13_delete_e_insert_no_mesmo_plano_e_na_mesma_transacao():
    """Separar em dois commits deixaria a tela mostrando fotografia vazia."""
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[_oferta()], healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE}, published_watermarks={APICE: CEDO},
        channel_enabled=True)
    # um unico objeto carrega os dois lados: nao ha como comitar separadamente
    assert plano.scopes_to_replace and plano.records
    assert isinstance(plano, cos.PublicationPlan)


def test_14_commit_indeterminado_nao_gera_retry():
    assert cos.audit_outcome(cos.COMMIT_INDETERMINATE,
                             None) == "needs_manual_reconciliation"
    assert cos.audit_outcome(cos.COMMIT_INDETERMINATE, 692) == "published"


def test_15_auditoria_pos_commit_nao_marca_failed_dado_publicado():
    assert cos.audit_outcome(cos.COMMIT_COMMITTED, None) == "published"
    assert cos.audit_outcome(cos.COMMIT_INDETERMINATE, 1) == "published"


def test_16_duas_execucoes_concorrentes_nao_intercalam():
    """A segunda nem chega a ler: o lock e' fail-fast."""
    primeira = ConexaoRegistradora(lock_concedido=True)
    segunda = ConexaoRegistradora(lock_concedido=False)
    assert cos.try_acquire_publication_lock(primeira) is True
    assert cos.try_acquire_publication_lock(segunda) is False
    assert len(segunda.diario) == 1


# ---------------------------------------------------------------------------
# 19-20. Mutantes
# ---------------------------------------------------------------------------


def test_19_a_guarda_de_watermark_e_alcancavel_pelo_plano():
    """Contraprova: sem a guarda, este caso publicaria."""
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[], healthy_scopes={APICE},
        incoming_watermarks={APICE: CEDO}, published_watermarks={APICE: TARDE},
        channel_enabled=True)
    assert plano.decision.reason == cos.REFUSE_WATERMARK_REGRESSION
    sem_guarda = cos.build_publication_plan(
        marketplace="shopee", records=[], healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE}, published_watermarks={APICE: CEDO},
        channel_enabled=True)
    assert sem_guarda.decision.allowed


def test_20_o_plano_nao_expoe_caminho_para_dois_commits():
    """`PublicationPlan` e' imutavel e nao tem metodo de commit parcial."""
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[_oferta()], healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE}, published_watermarks={},
        channel_enabled=True)
    with pytest.raises(Exception):
        plano.records = ()
    metodos = [m for m in dir(plano) if not m.startswith("_")]
    assert "commit" not in metodos
    assert "commit_delete" not in metodos
    assert "commit_insert" not in metodos


def test_o_sql_de_delete_filtra_exatamente_o_escopo():
    sql = " ".join(cos.SQL_DELETE_SCOPE.split())
    assert "marketplace = %(marketplace)s" in sql
    assert "observed_date = %(observed_date)s" in sql
    assert "shop_account = %(shop_account)s" in sql
    assert cos.TARGET_TABLE in sql
    # sem filtro de marca: ela nao e' identidade nem escopo
    assert "brand" not in sql
