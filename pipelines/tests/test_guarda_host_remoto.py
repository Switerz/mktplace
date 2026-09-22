"""Gate PMA-2C5B-R/V — a guarda que fecha a porta do incidente EXP-3B2-I1.

A guarda do #26 fecha UMA porta: `fato_diaria_lock._default_connect`. Qualquer
outro modulo que chame `psycopg2.connect` com URL vinda do ambiente continua
podendo abrir conexao com Neon ou Data Mart quando existe `.env` na maquina —
e foi assim que o incidente passou despercebido: os testes ficavam verdes na
maquina COM `.env` (tocando producao a cada execucao) e quebravam sem ela.

A guarda deste gate fecha a porta pelo DESTINO. Estes testes provam que ela
levanta para host remoto e deixa passar o cluster local, sem jamais tentar
alcancar host de producao: o host usado aqui e' inventado e nao resolve.
"""
from __future__ import annotations

import pytest

HOST_INVENTADO = "banco-que-nao-existe.invalido"


def test_a_guarda_recusa_host_remoto_antes_de_tentar_conectar():
    """Levanta `AssertionError` — nao um erro de rede. Se chegasse a tentar,
    o erro seria de DNS ou timeout, e a conexao teria saido da maquina."""
    import psycopg2
    with pytest.raises(AssertionError) as exc:
        psycopg2.connect(f"postgresql://u:s@{HOST_INVENTADO}:5432/prod")
    mensagem = str(exc.value)
    assert HOST_INVENTADO in mensagem
    assert "host remoto" in mensagem
    assert "EXP-3B2-I1" in mensagem, (
        "a mensagem precisa apontar o incidente, senao quem tropecar nela "
        "nao sabe por que existe")


def test_a_mensagem_da_guarda_nao_imprime_a_URL_inteira():
    """Host sim, credencial nao: a mensagem circula em log de CI."""
    import psycopg2
    with pytest.raises(AssertionError) as exc:
        psycopg2.connect(f"postgresql://usuario:senha_secreta@{HOST_INVENTADO}/db")
    mensagem = str(exc.value)
    assert "senha_secreta" not in mensagem
    assert "usuario" not in mensagem
    assert "postgresql://" not in mensagem


@pytest.mark.parametrize("host", [
    "ep-lively-frost-a6eg1wh2.us-west-2.aws.neon.tech",
    "datamart-gogroup-reader.cvfsx8dkoxhw.us-east-1.rds.amazonaws.com",
])
def test_os_hosts_produtivos_reais_sao_recusados(host):
    """Nomes usados como DADO, nunca como destino: a guarda levanta antes de
    qualquer resolucao de nome."""
    import psycopg2
    with pytest.raises(AssertionError):
        psycopg2.connect(f"postgresql://u@{host}:5432/db")


@pytest.mark.parametrize("url", [
    "postgresql://postgres@127.0.0.1:54321/postgres",
    "postgresql://postgres@localhost:54321/postgres",
])
def test_a_guarda_deixa_passar_o_cluster_local(url):
    """Passa da guarda e falha na CONEXAO — prova que a guarda nao barrou.
    A porta e' inventada, entao nada e' realmente aberto."""
    import psycopg2
    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(url, connect_timeout=2)


def test_a_guarda_do_26_continua_ativa():
    """As duas coexistem: a do #26 e' pontual, esta e' geral."""
    from pipelines.ingestion import fato_diaria_lock
    with pytest.raises(AssertionError) as exc:
        fato_diaria_lock._default_connect()
    assert "advisory lock" in str(exc.value)
