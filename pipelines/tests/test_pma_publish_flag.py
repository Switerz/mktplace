"""Gate PMA-OPS-2 — a flag de publicacao sai do codigo e vira configuracao.

O que estes testes travam, em uma frase: o default continua NAO PUBLICAR, ligar
exige configuracao explicita, `--operator-override` continua vencendo a flag, e
um valor malformado falha alto em vez de virar um canal silenciosamente parado.

POR QUE A FLAG MUDOU DE LUGAR
------------------------------
`run_apply` passava `channel_enabled=False` LITERAL. Era o guarda-corpo certo do
piloto — nenhum agendador conseguia publicar sozinho —, mas tornou o canal
impossivel de automatizar: o `pma_refresh` chama este modulo sem
`--operator-override` e por isso os steps `pma_shopee` e `pma_tiktok` recusam
sempre. Medido em `audit.source_sync_run`:
`2026-09-22 21:22 — recusado: channel_flag_disabled`.

O QUE ESTES TESTES **NAO** FAZEM
---------------------------------
Nao ligam flag nenhuma fora do proprio processo, nao abrem conexao e nao
publicam. Todo ambiente e' injetado por parametro (`env=`), nunca por
`os.environ` global — um teste que exportasse a variavel de verdade deixaria o
rastro para o teste seguinte e poderia autorizar uma publicacao que ninguem
pediu. Ver `pipelines/tests/conftest.py`, que ja' recusa conexao real.
"""
from __future__ import annotations

import pytest

from pipelines import channel_offer_publisher as pub
from pipelines import channel_offer_sync as cos

CANAIS = ("shopee", "tiktok")


# ---------------------------------------------------------------------------
# 1. O DEFAULT E' NAO PUBLICAR
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("canal", CANAIS)
def test_flag_ausente_desliga(canal):
    """Instalacao nova, container sem a variavel, `.env` incompleto: nao publica."""
    assert pub.channel_enabled_from_env(canal, env={}) is False


@pytest.mark.parametrize("canal", CANAIS)
def test_ambiente_com_outras_variaveis_nao_liga_por_acidente(canal):
    """Uma variavel parecida nao pode ser lida no lugar da certa."""
    env = {"PMA_PUBLISH_ENABLED": "true", "PMA_PUBLISH": "1",
           f"{pub.PUBLISH_FLAG_ENV[canal]}_X": "true"}
    assert pub.channel_enabled_from_env(canal, env=env) is False


@pytest.mark.parametrize("canal", CANAIS)
@pytest.mark.parametrize("valor", ["0", "false", "FALSE", "no", "off", "", "  "])
def test_valores_de_desligado(canal, valor):
    env = {pub.PUBLISH_FLAG_ENV[canal]: valor}
    assert pub.channel_enabled_from_env(canal, env=env) is False


@pytest.mark.parametrize("canal", CANAIS)
@pytest.mark.parametrize("valor", ["1", "true", "TRUE", " True ", "yes", "on"])
def test_valores_de_ligado(canal, valor):
    env = {pub.PUBLISH_FLAG_ENV[canal]: valor}
    assert pub.channel_enabled_from_env(canal, env=env) is True


# ---------------------------------------------------------------------------
# 2. VALOR DESCONHECIDO LEVANTA — nao vira "desligado" silencioso
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("valor", ["treu", "sim", "2", "enabled", "null", "-"])
def test_valor_invalido_levanta_em_vez_de_desligar_calado(valor):
    """Tratar lixo como desligado tambem nao publica — mas nao publica em
    SILENCIO, e quem digitou errado veria a fotografia envelhecer sem pista."""
    with pytest.raises(pub.PublishFlagError):
        pub.channel_enabled_from_env("shopee", env={
            pub.PUBLISH_FLAG_ENV["shopee"]: valor})


def test_erro_de_flag_nomeia_a_variavel_e_nunca_ecoa_o_valor():
    """Uma variavel de ambiente mal preenchida pode conter qualquer coisa,
    inclusive um segredo colado no lugar errado."""
    segredo = "npg_umsegredoqualquer"
    with pytest.raises(pub.PublishFlagError) as exc:
        pub.channel_enabled_from_env("tiktok", env={
            pub.PUBLISH_FLAG_ENV["tiktok"]: segredo})
    texto = str(exc.value)
    assert "PMA_PUBLISH_TIKTOK_ENABLED" in texto
    assert segredo not in texto


def test_erro_de_flag_e_falha_e_nao_recusa():
    """`PublishFlagError` e' `PublisherError`, que o `main` traduz em FALHA.
    Recusa (`exit 2`) diria "a regra decidiu nao publicar"; aqui a regra nem
    pode ser avaliada."""
    assert issubclass(pub.PublishFlagError, pub.PublisherError)


def test_canal_fora_do_dominio_nao_tem_flag():
    with pytest.raises(pub.PublisherError):
        pub.channel_enabled_from_env("ml", env={})


# ---------------------------------------------------------------------------
# 3. UMA VARIAVEL POR CANAL — ligar um nao liga o outro
# ---------------------------------------------------------------------------
def test_ligar_a_shopee_nao_liga_o_tiktok():
    env = {pub.PUBLISH_FLAG_ENV["shopee"]: "true"}
    assert pub.channel_enabled_from_env("shopee", env=env) is True
    assert pub.channel_enabled_from_env("tiktok", env=env) is False


def test_as_duas_variaveis_sao_distintas():
    nomes = set(pub.PUBLISH_FLAG_ENV.values())
    assert len(nomes) == len(pub.PUBLISH_FLAG_ENV) == 2


def test_o_dominio_da_flag_cobre_exatamente_a_fato_multicanal():
    """Um canal novo na fato sem flag publicaria por um caminho sem porteiro —
    ou nao publicaria nunca, sem ninguem saber qual dos dois."""
    assert set(pub.PUBLISH_FLAG_ENV) == set(cos.CHANNEL_MARKETPLACES)


# ---------------------------------------------------------------------------
# 4. A FLAG CHEGA AO PLANO, e o override continua vencendo
# ---------------------------------------------------------------------------
def test_plano_recusa_com_flag_desligada_e_sem_override():
    d = cos.plan_publication(
        marketplace="shopee",
        channel_enabled=pub.channel_enabled_from_env("shopee", env={}),
        operator_override=False,
        accounts_that_ran=4, record_count=695)
    assert not d.allowed
    assert d.reason == cos.REFUSE_FLAG_OFF


def test_override_publica_mesmo_com_a_flag_desligada():
    """O caminho do humano nao foi removido — deixou de ser o unico."""
    d = cos.plan_publication(
        marketplace="shopee",
        channel_enabled=pub.channel_enabled_from_env("shopee", env={}),
        operator_override=True,
        accounts_that_ran=4, record_count=695)
    assert d.allowed


def test_flag_ligada_publica_sem_override():
    env = {pub.PUBLISH_FLAG_ENV["tiktok"]: "1"}
    d = cos.plan_publication(
        marketplace="tiktok",
        channel_enabled=pub.channel_enabled_from_env("tiktok", env=env),
        operator_override=False,
        accounts_that_ran=1, record_count=1215)
    assert d.allowed


@pytest.mark.parametrize("guarda,kwargs", [
    ("source_available", dict(source_available=False)),
    ("accounts_that_ran", dict(accounts_that_ran=0)),
    ("record_count", dict(record_count=0)),
])
def test_flag_ligada_nao_atropela_as_demais_guardas(guarda, kwargs):
    """Ligar a flag autoriza a INTENCAO de publicar, nao a publicacao. As
    recusas que protegem contra apagar a fotografia anterior continuam de pe."""
    base = dict(marketplace="shopee", channel_enabled=True,
                operator_override=False, accounts_that_ran=4, record_count=695)
    base.update(kwargs)
    assert not cos.plan_publication(**base).allowed


# ---------------------------------------------------------------------------
# 5. O DIAGNOSTICO EXPLICA, e declara o que nao avaliou
# ---------------------------------------------------------------------------
def test_relatorio_do_portao_explica_a_recusa_por_flag():
    r = pub.publication_gate_report("shopee", operator_override=False, env={})
    assert r["flag_state"] == "disabled"
    assert r["flag_env_var"] == "PMA_PUBLISH_SHOPEE_ENABLED"
    assert r["authorized_to_publish"] is False
    assert r["refusal_reason"] == cos.REFUSE_FLAG_OFF


def test_relatorio_do_portao_mostra_o_override():
    r = pub.publication_gate_report("tiktok", operator_override=True, env={})
    assert r["channel_enabled"] is False
    assert r["authorized_to_publish"] is True
    assert r["refusal_reason"] is None


def test_relatorio_do_portao_nao_levanta_em_valor_invalido():
    """O diagnostico e' justamente onde o operador precisa VER o erro. Levantar
    aqui esconderia o resto do relatorio."""
    r = pub.publication_gate_report(
        "shopee", operator_override=False,
        env={pub.PUBLISH_FLAG_ENV["shopee"]: "treu"})
    assert r["flag_state"] == "invalid_value"
    assert r["channel_enabled"] is False
    assert r["authorized_to_publish"] is False
    assert "PMA_PUBLISH_SHOPEE_ENABLED" in r["flag_error"]


def test_relatorio_declara_o_que_nao_conseguiu_avaliar():
    """Sem isto, `authorized_to_publish = true` pareceria garantia de
    publicacao — e ele so' responde pelo portao da flag."""
    r = pub.publication_gate_report("shopee", operator_override=True, env={})
    assert "snapshot_older_than_published" in r["not_evaluated"]
    assert "record_count" in r["not_evaluated"]


def test_relatorio_do_portao_nunca_ecoa_o_valor_bruto():
    segredo = "postgres://u:p@h/d"
    r = pub.publication_gate_report(
        "tiktok", operator_override=False,
        env={pub.PUBLISH_FLAG_ENV["tiktok"]: segredo})
    assert segredo not in str(r)
