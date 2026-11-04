# tests/test_sessao.py — tokens sinteticos, sem rede
import time, jwt
from app.sessao import (Sessao, dominios_permitidos, email_permitido, validar_sessao,
                        SESSION_ISSUER, SESSION_AUDIENCE)

SEGREDO = "s" * 32
DOM = ("gocase.com", "gobeaute.com.br")

def _tok(email="joao@gocase.com", segredo=SEGREDO, iss=SESSION_ISSUER, aud=SESSION_AUDIENCE,
         exp_em=300, alg="HS256", **extra):
    agora = int(time.time())
    claims = {"email": email, "name": "Joao", "iss": iss, "aud": aud, "iat": agora, "exp": agora + exp_em, **extra}
    return jwt.encode(claims, segredo, algorithm=alg)

def test_dominios_padrao_quando_vazio():
    assert dominios_permitidos("") == DOM
    assert dominios_permitidos(" GoCase.com , ") == ("gocase.com",)

def test_email_permitido_match_exato():
    assert email_permitido("JOAO@GOCASE.COM", DOM)
    for ruim in ["x@gocase.com.evil.com", "x@mail.gocase.com", "x@evil.com", "gocase.com", "", None]:
        assert not email_permitido(ruim, DOM), ruim

def test_sessao_valida():
    assert validar_sessao(_tok(), SEGREDO, DOM) == Sessao(email="joao@gocase.com", nome="Joao")

def test_sessao_invalida_vira_none():
    casos = [
        None, "", "lixo",
        _tok(segredo="o" * 32),                 # outro segredo (ex.: o do GoHunter)
        _tok(iss="gohunter"), _tok(aud="outro"), # outro emissor/publico
        _tok(exp_em=-120),                       # expirado alem do leeway
        _tok(email="x@evil.com"),                # dominio barrado na leitura
    ]
    for t in casos:
        assert validar_sessao(t, SEGREDO, DOM) is None

def test_segredo_curto_nega_tudo():
    assert validar_sessao(_tok(segredo="curto"), "curto", DOM) is None

def test_alg_none_recusado():
    t = jwt.encode({"email": "joao@gocase.com", "iss": SESSION_ISSUER, "aud": SESSION_AUDIENCE,
                    "iat": int(time.time()), "exp": int(time.time()) + 300}, None, algorithm="none")
    assert validar_sessao(t, SEGREDO, DOM) is None
