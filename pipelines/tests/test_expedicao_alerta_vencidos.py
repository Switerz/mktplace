"""EXPEDICAO-ALERTA-VENCIDOS-1 — alerta no Slack com vencidos acima de 3% da fila da Shopee.

Prova: a razao e' a mesma da tela (soma de vencidos sobre soma de backlog do
lote servido); avisa ao entrar, lembra as 9h/13h/17h de Brasilia e avisa uma
vez na volta; fotografia velha nao avisa; o CLI nao envia nada sem `--enviar`
nem vaza o token.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipelines.common import slack_dm
from pipelines.expedicao import alerta_vencidos as av
from pipelines.expedicao.contract import Channel


def _utc(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 10, dia, hora, minuto, tzinfo=timezone.utc)


# 29/10 17:12 UTC = 14:12 em Brasilia; resumo da hora 17:00 UTC.
AGORA = _utc(29, 17, 12)
HORA = _utc(29, 17)
HORA_ANTERIOR = _utc(29, 15)  # DAG a cada 2 h: 12:00 em Brasilia


class _Cursor:
    def __init__(self, banco: dict):
        self.banco = banco
        self._resultado: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=()):
        dados = self.banco.get(params[0])
        if "SELECT DISTINCT refresh_batch_id" in sql:
            self._resultado = dados["lotes"] if dados else []
        elif "SELECT brand" in sql:
            self._resultado = dados["resumos"]
        else:
            assert params[2] == HORA  # anterior e' estritamente antes do resumo servido
            ant = dados.get("anterior")
            self._resultado = (
                []
                if ant is None
                else [{"snapshot_hour": HORA_ANTERIOR, "contas": 2,
                       "backlog": ant[0], "vencidos": ant[1]}]
            )

    def fetchall(self):
        return self._resultado

    def fetchone(self):
        return self._resultado[0] if self._resultado else None


class _Conn:
    def __init__(self, banco: dict):
        self.banco = banco
        self.fechada = False

    def cursor(self):
        return _Cursor(self.banco)

    def rollback(self):
        pass

    def close(self):
        self.fechada = True


def _canal(resumos: list[tuple[str, int, int]], anterior: tuple[int, int] | None) -> dict:
    return {
        "lotes": [{"refresh_batch_id": "lote-1", "effective_at": AGORA}],
        "resumos": [
            {"brand": m, "backlog_count": b, "overdue_count": v, "snapshot_hour": HORA}
            for m, b, v in resumos
        ],
        "anterior": anterior,
    }


def _leitura(backlog: int, vencidos: int, anterior: tuple[int, int] | None,
             hora: datetime = HORA, hora_anterior: datetime = HORA_ANTERIOR) -> av.Leitura:
    return av.Leitura(
        canal=Channel.SHOPEE,
        effective_at=AGORA,
        hora=hora,
        total=av.Medida(backlog, vencidos),
        por_marca={"apice": av.Medida(backlog, vencidos)},
        anterior=av.Medida(*anterior) if anterior else None,
        hora_anterior=hora_anterior if anterior else None,
    )


# --- limite ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("backlog", "vencidos", "acima"),
    [(100, 3, False), (100, 4, True), (1000, 31, True), (0, 0, False)],
)
def test_limite_e_estritamente_acima_de_3_por_cento(backlog, vencidos, acima):
    assert av.Medida(backlog, vencidos).acima is acima


# --- quando avisa ------------------------------------------------------------
def test_avisa_quando_entra_acima_de_3_por_cento():
    assert av.decidir(_leitura(1000, 40, anterior=(1000, 20))) == av.ENTROU


def test_sem_historico_e_acima_conta_como_entrada():
    assert av.decidir(_leitura(1000, 40, anterior=None)) == av.ENTROU


def test_continua_acima_sem_passar_por_hora_de_lembrete_fica_quieto():
    # (12:00, 14:00] em Brasilia: 13h passou -> lembrete; (14:00, 16:00]: nao.
    assert av.decidir(_leitura(1000, 50, anterior=(1000, 40),
                               hora=_utc(29, 19), hora_anterior=_utc(29, 17))) is None


def test_continua_acima_e_passou_das_13h_lembra():
    assert av.decidir(_leitura(1000, 50, anterior=(1000, 40))) == av.LEMBRETE


@pytest.mark.parametrize(
    ("anterior_brt", "atual_brt", "lembra"),
    [
        (8, 9, True),     # DAG de hora em hora: a fotografia das 9h lembra
        (9, 10, False),   # ...e a seguinte nao repete
        (8, 10, True),    # DAG a cada 2 h nas horas pares: 9h cai no vao e conta
        (16, 18, True),   # 17h
        (18, 20, False),
        (20, 33, True),   # vao de 13 h atravessando a noite: 9h do dia seguinte
    ],
)
def test_hora_de_lembrete_e_a_que_o_relogio_atravessa(anterior_brt, atual_brt, lembra):
    base = _utc(29, 3)  # 00:00 em Brasilia
    desde = base + timedelta(hours=anterior_brt)
    ate = base + timedelta(hours=atual_brt)
    assert av.passou_hora_de_lembrete(desde, ate) is lembra


def test_avisa_uma_vez_quando_volta_ao_normal():
    assert av.decidir(_leitura(1000, 20, anterior=(1000, 40))) == av.SAIU


def test_abaixo_e_continua_abaixo_fica_quieto_mesmo_na_hora_de_lembrete():
    assert av.decidir(_leitura(1000, 10, anterior=(1000, 20))) is None


def test_exatamente_3_por_cento_nao_avisa():
    assert av.decidir(_leitura(1000, 30, anterior=(1000, 30))) is None


# --- leitura --------------------------------------------------------------
def test_leitura_soma_contas_e_agrupa_por_marca():
    conn = _Conn({
        "shopee": _canal(
            [("apice", 600, 30), ("apice", 400, 10), ("lescent", 1000, 5)],
            anterior=(2000, 30),
        ),
    })
    leitura = av.ler_canal(conn, Channel.SHOPEE)
    assert leitura.total == av.Medida(2000, 45)
    assert leitura.por_marca["apice"] == av.Medida(1000, 40)
    assert leitura.por_marca["lescent"] == av.Medida(1000, 5)
    assert leitura.anterior == av.Medida(2000, 30)
    assert (leitura.hora, leitura.hora_anterior) == (HORA, HORA_ANTERIOR)


def test_leitura_sem_historico():
    leitura = av.ler_canal(_Conn({"shopee": _canal([("apice", 100, 1)], None)}), Channel.SHOPEE)
    assert leitura.anterior is None and leitura.hora_anterior is None


def test_sem_fotografia_nao_avalia():
    assert av.ler_canal(_Conn({}), Channel.SHOPEE) is None


def test_lote_incoerente_nao_avalia():
    banco = {"shopee": _canal([("apice", 100, 10)], anterior=None)}
    banco["shopee"]["lotes"].append({"refresh_batch_id": "lote-2", "effective_at": AGORA})
    assert av.ler_canal(_Conn(banco), Channel.SHOPEE) is None


def test_so_a_shopee_e_avaliada():
    assert av.CANAIS == (Channel.SHOPEE,)


# --- mensagem ---------------------------------------------------------------
def _leitura_com_marcas() -> av.Leitura:
    return av.Leitura(
        canal=Channel.SHOPEE,
        effective_at=AGORA,
        hora=HORA,
        total=av.Medida(1264, 58),
        por_marca={
            "apice": av.Medida(727, 44),
            "barbours": av.Medida(316, 11),
            "lescent": av.Medida(111, 3),
        },
        anterior=None,
        hora_anterior=None,
    )


def test_mensagem_de_entrada_traz_numeros_marcas_e_link():
    texto = av.montar_mensagem(_leitura_com_marcas(), av.ENTROU)
    assert ":rotating_light: *Expedição Shopee: 4,6% da fila vencida* (limite 3%)" in texto
    assert "58 de 1.264 pedidos da fila passaram do prazo de despacho" in texto
    assert "29/10 às 14:12" in texto
    assert "Marcas acima de 3%: ÁPICE 6,1% (44 de 727) · BARBOURS 3,5% (11 de 316)" in texto
    assert "LESCENT" not in texto
    assert "<https://mktplace-gobeaute.vercel.app/expedicao|" in texto


def test_mensagem_de_lembrete_diz_que_continua():
    texto = av.montar_mensagem(_leitura_com_marcas(), av.LEMBRETE)
    assert ":warning: *Expedição Shopee: continua com 4,6% da fila vencida*" in texto
    assert "Marcas acima de 3%" in texto


def test_mensagem_de_normalizacao():
    texto = av.montar_mensagem(_leitura(1000, 21, anterior=(1000, 40)), av.SAIU)
    assert "vencidos voltaram a 2,1% da fila" in texto
    assert "Marcas acima" not in texto


# --- CLI --------------------------------------------------------------------
@pytest.fixture
def banco_em_alerta():
    return {"shopee": _canal([("apice", 1000, 50)], anterior=(1000, 10))}


@pytest.fixture
def slack_configurado(monkeypatch):
    monkeypatch.setenv(av.ENV_TOKEN, "xoxb-SEGREDO")
    monkeypatch.setenv(av.ENV_DESTINATARIOS, "a@gocase.com, b@gocase.com")


def _agora():
    return AGORA + timedelta(minutes=3)


def test_sem_enviar_so_mostra(capsys, slack_configurado, banco_em_alerta):
    enviados: list[str] = []
    conn = _Conn(banco_em_alerta)
    codigo = av.main([], abrir=lambda: conn,
                     enviar=lambda tok, em, txt: enviados.append(txt), agora=_agora)
    saida = capsys.readouterr().out
    assert codigo == av.EXIT_OK
    assert enviados == []
    assert "nao enviada" in saida
    assert "SEGREDO" not in saida
    assert conn.fechada


def test_com_enviar_manda_para_todos(slack_configurado, banco_em_alerta):
    enviados: list[tuple] = []
    codigo = av.main(
        ["--enviar"],
        abrir=lambda: _Conn(banco_em_alerta),
        enviar=lambda tok, em, txt: enviados.append((tok, em, txt)),
        agora=_agora,
    )
    assert codigo == av.EXIT_OK
    assert len(enviados) == 1
    tok, emails, texto = enviados[0]
    assert tok == "xoxb-SEGREDO"
    assert emails == ["a@gocase.com", "b@gocase.com"]
    assert "Expedição Shopee: 5,0% da fila vencida" in texto


def test_fotografia_velha_nao_avisa(capsys, slack_configurado, banco_em_alerta):
    enviados: list[str] = []
    codigo = av.main(
        ["--enviar"],
        abrir=lambda: _Conn(banco_em_alerta),
        enviar=lambda tok, em, txt: enviados.append(txt),
        agora=lambda: AGORA + timedelta(minutes=46),
    )
    assert codigo == av.EXIT_OK
    assert enviados == []
    assert "mais velha que 45 min" in capsys.readouterr().out


def test_max_idade_configuravel(slack_configurado, banco_em_alerta):
    enviados: list[str] = []
    av.main(
        ["--enviar", "--max-idade-min", "120"],
        abrir=lambda: _Conn(banco_em_alerta),
        enviar=lambda tok, em, txt: enviados.append(txt),
        agora=lambda: AGORA + timedelta(minutes=90),
    )
    assert len(enviados) == 1


@pytest.mark.parametrize("faltando", [av.ENV_TOKEN, av.ENV_DESTINATARIOS])
def test_enviar_sem_configuracao_falha_antes_de_ler(monkeypatch, slack_configurado, banco_em_alerta, faltando):
    monkeypatch.setenv(faltando, "")
    abriu: list[bool] = []
    codigo = av.main(["--enviar"], abrir=lambda: abriu.append(True) or _Conn(banco_em_alerta))
    assert codigo == av.EXIT_FALHA
    assert abriu == []


def test_falha_do_slack_vira_exit_1_sem_vazar_token(capsys, slack_configurado, banco_em_alerta):
    def quebra(tok, em, txt):
        raise av.SlackFalhou("users.lookupByEmail: users_not_found")

    codigo = av.main(["--enviar"], abrir=lambda: _Conn(banco_em_alerta), enviar=quebra, agora=_agora)
    err = capsys.readouterr().err
    assert codigo == av.EXIT_FALHA
    assert "users_not_found" in err
    assert "SEGREDO" not in err


# --- DM pela Web API ----------------------------------------------------------
def test_dm_procura_cada_email_e_posta_no_id(monkeypatch):
    chamadas: list[tuple] = []

    def falso(token, metodo, campos):
        chamadas.append((metodo, campos))
        if metodo == "users.lookupByEmail":
            return {"ok": True, "user": {"id": "U-" + campos["email"][0]}}
        return {"ok": True}

    monkeypatch.setattr(slack_dm, "_slack", falso)
    slack_dm.enviar_dm("xoxb", ["ana@gocase.com", "bia@gocase.com"], "oi")
    assert chamadas == [
        ("users.lookupByEmail", {"email": "ana@gocase.com"}),
        ("chat.postMessage", {"channel": "U-a", "text": "oi"}),
        ("users.lookupByEmail", {"email": "bia@gocase.com"}),
        ("chat.postMessage", {"channel": "U-b", "text": "oi"}),
    ]


class _Resposta:
    def __init__(self, corpo: dict):
        self.corpo = corpo

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        import json

        return json.dumps(self.corpo).encode()


def test_ok_false_da_web_api_vira_falha_sem_token(monkeypatch):
    pedidos: list = []

    def urlopen(req, timeout):
        pedidos.append(req)
        return _Resposta({"ok": False, "error": "not_in_channel"})

    monkeypatch.setattr(slack_dm.urllib.request, "urlopen", urlopen)
    with pytest.raises(slack_dm.SlackFalhou) as erro:
        slack_dm._slack("xoxb-SEGREDO", "chat.postMessage", {"channel": "U1", "text": "x"})
    assert "not_in_channel" in str(erro.value)
    assert "SEGREDO" not in str(erro.value)
    assert pedidos[0].get_header("Authorization") == "Bearer xoxb-SEGREDO"
    assert pedidos[0].full_url == "https://slack.com/api/chat.postMessage"
