"""
Gate G4, Task 2 — fail-fast EXCLUSIVO do engine do Data Mart.

Contexto (diagnostico em docs/DRILLDOWN_ARCHITECTURE.md §8.9): as 5 consultas de
`gold_service.get_brand_detail` leem `gold.*`, entao `_uses_datamart()` as roteia
para o `datamart_engine` (RDS AWS, que exige VPN). O Render nao tem essa
conectividade, logo `connect()` fica pendurado 45-120s sem receber byte algum —
o tempo e' 100% de CONEXAO, nao de consulta (as 5 rodam em 4,07s com acesso).

Estes testes provam o isolamento da mitigacao: SO' o Data Mart recebe
`connect_timeout`; o engine principal/Neon continua criado exatamente como
antes. Nenhuma rede real e' usada — `create_engine` e' monkeypatchado.

NAO cobrem "os dados voltaram": a mitigacao apenas encurta a espera. As 4 rotas
servidas pelo Data Mart seguem sem conteudo em producao ate a decisao de
serving.
"""
from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

from app.config import Settings


class _FakeEngine:
    """Substituto inerte de Engine — nunca abre conexao."""


@pytest.fixture()
def engine_calls(monkeypatch):
    """Captura as chamadas a `create_engine` feitas no import de app.database,
    sem tocar a rede. Devolve a lista de (url, kwargs) na ordem de criacao."""
    calls: list[tuple[str, dict]] = []

    def fake_create_engine(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeEngine()

    import app.database as database

    monkeypatch.setattr(database, "create_engine", fake_create_engine)
    return calls, database


def _reload_with(monkeypatch, calls_holder, *, database_url: str, datamart_url: str, timeout: int):
    """Recria os engines do modulo chamando `_make_engine` com o mesmo contrato
    do import real — evita reimportar o modulo (que reinstalaria o
    `create_engine` verdadeiro e poderia tentar rede)."""
    calls, database = calls_holder
    calls.clear()
    main_engine = database._make_engine(database_url)
    dm_engine = database._make_engine(datamart_url, connect_timeout=timeout)
    return main_engine, dm_engine, calls


# ---------------------------------------------------------------------------
# 1. URL vazia continua devolvendo None (comportamento preservado)
# ---------------------------------------------------------------------------

def test_url_vazia_continua_retornando_none(engine_calls):
    calls, database = engine_calls
    assert database._make_engine("") is None
    assert database._make_engine("", connect_timeout=10) is None
    assert calls == [], "URL vazia nao deve nem chamar create_engine"


# ---------------------------------------------------------------------------
# 2 e 3. Isolamento: Neon sem connect_timeout; Data Mart com o valor exato
# ---------------------------------------------------------------------------

def test_engine_principal_sem_connect_timeout_e_datamart_com_o_valor(engine_calls):
    main_engine, dm_engine, calls = _reload_with(
        None, engine_calls, database_url="postgresql://x/neon", datamart_url="postgresql://y/dm", timeout=7
    )
    assert main_engine is not None and dm_engine is not None
    assert len(calls) == 2

    (_, main_kwargs), (_, dm_kwargs) = calls
    # engine principal: NENHUM connect_args novo
    assert "connect_args" not in main_kwargs, "Neon nao pode receber connect_timeout"
    # Data Mart: exatamente o timeout configurado
    assert dm_kwargs["connect_args"] == {"connect_timeout": 7}


# ---------------------------------------------------------------------------
# 4. pool_pre_ping preservado nos dois engines
# ---------------------------------------------------------------------------

def test_pool_pre_ping_permanece_nos_dois_engines(engine_calls):
    _, _, calls = _reload_with(
        None, engine_calls, database_url="postgresql://x/neon", datamart_url="postgresql://y/dm", timeout=10
    )
    for _, kwargs in calls:
        assert kwargs.get("pool_pre_ping") is True


# ---------------------------------------------------------------------------
# 5 e 6. Default e faixa validada pelo proprio Pydantic
# ---------------------------------------------------------------------------

def test_default_do_timeout_e_dez_segundos():
    s = Settings(_env_file=None)
    assert s.datamart_connect_timeout_seconds == 10


@pytest.mark.parametrize("valor", [1, 10, 30])
def test_valores_dentro_da_faixa_sao_aceitos(valor):
    s = Settings(_env_file=None, datamart_connect_timeout_seconds=valor)
    assert s.datamart_connect_timeout_seconds == valor


@pytest.mark.parametrize("valor", [0, -1, 31, 120])
def test_valores_fora_da_faixa_sao_rejeitados(valor):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, datamart_connect_timeout_seconds=valor)


# ---------------------------------------------------------------------------
# 7. Nenhum DSN/segredo em mensagens ou no que o modulo expoe
# ---------------------------------------------------------------------------

def test_falha_de_criacao_nao_vaza_dsn(engine_calls):
    """Se create_engine explode, `_make_engine` devolve None e nao propaga a
    URL em mensagem alguma."""
    calls, database = engine_calls
    # Valor sintetico e inequivocamente falso (nunca uma credencial real) —
    # existe apenas para provar que a URL nao reaparece em saida alguma.
    secret_url = "postgresql://PLACEHOLDER_USER:PLACEHOLDER_SECRET" + "@" + "placeholder.invalid:5432/db"

    def boom(url, **kwargs):
        raise RuntimeError(f"falha contendo {url}")

    database.create_engine = boom  # type: ignore[assignment]
    result = database._make_engine(secret_url, connect_timeout=5)
    assert result is None, "falha deve virar None, nunca propagar a excecao com a URL"


def test_checagens_de_conexao_nao_retornam_dsn_quando_engine_ausente(engine_calls):
    _, database = engine_calls
    original_engine = database.engine
    original_dm = database.datamart_engine
    try:
        database.engine = None
        database.datamart_engine = None
        ok_local, msg_local = database.check_connection()
        ok_dm, msg_dm = database.check_datamart_connection()
        assert ok_local is False and ok_dm is False
        for msg in (msg_local, msg_dm):
            assert msg is not None
            for leak in ("postgresql://", "senha", "password", "@"):
                assert leak not in msg, f"mensagem nao pode conter {leak!r}"
    finally:
        database.engine = original_engine
        database.datamart_engine = original_dm


# ---------------------------------------------------------------------------
# 8. Nenhuma consulta/regra do gold_service foi alterada, e nada de retry
# ---------------------------------------------------------------------------

def test_gold_service_intocado_e_sem_retry_ou_fallback():
    from pathlib import Path

    gold = Path(importlib.import_module("app.services.gold_service").__file__).read_text(encoding="utf-8")
    # O roteamento por prefixo continua exatamente como antes: e' ele que manda
    # qualquer SQL com gold./raw. para o datamart_engine.
    assert 'return any(token in lowered for token in (" gold.", "from gold."' in gold

    # Fontes Gold que PERMANECEM. O Gate S3 migrou `/inteligencia` e
    # `/brand-detail` para `marts.*`, mas estas quatro continuam sendo lidas da
    # Gold por OUTRAS funcoes do arquivo (`get_overview`, `get_canais`,
    # `get_tempo_real`, `get_produtos_tiktok`...), que nao foram tocadas.
    for src in (
        "FROM gold.tiktok_brand_daily",
        "FROM gold.tiktok_product_daily",
        "FROM gold.v_channel_efficiency",
        "FROM gold.tiktok_shop_hourly",
    ):
        assert src in gold, f"fonte {src} nao pode ter mudado"

    # Fontes que o Gate S3 removeu do arquivo por completo, porque as duas rotas
    # migradas eram as UNICAS consumidoras. Se voltarem a aparecer, alguem
    # reintroduziu dependencia de Data Mart numa rota ja migrada.
    for migrada in ("FROM gold.tiktok_creator_daily", "FROM gold.ml_cross_company_summary"):
        assert migrada not in gold, (
            f"{migrada} voltou ao gold_service: as rotas que a consumiam foram "
            "migradas para marts.* no Gate S3")
    database_src = Path(importlib.import_module("app.database").__file__).read_text(encoding="utf-8")

    def code_only(src: str) -> str:
        """Descarta comentarios e docstrings de uma linha — a proibicao vale
        para CODIGO, nao para o texto que explica a decisao."""
        lines = []
        for raw in src.splitlines():
            stripped = raw.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("*"):
                continue
            lines.append(raw.split("#", 1)[0])
        return "\n".join(lines).lower()

    # nenhuma mecanica de retry/backoff/fallback foi introduzida no caminho do
    # Data Mart, e a falha nunca e' convertida em sucesso vazio
    for forbidden in ("for attempt", "while true", "time.sleep", "retrying", "except exception:\n        return []"):
        assert forbidden not in code_only(database_src), f"database.py nao deve conter {forbidden!r}"
        assert forbidden not in code_only(gold), f"gold_service.py nao deve conter {forbidden!r}"
