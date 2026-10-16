"""EXPEDICAO-TIKTOK-AUTOMATION-1 — guarda de frescor da fotografia da LDR do TikTok.

Por que 13/10 ficou congelado: `marts.expedicao_tiktok_dispatch_daily` nunca
esteve em pipeline nenhum. A ultima publicacao foi manual, em 13/10/2026. Como
`*_is_mature` e a particao pendente-no-prazo/pendente-vencido sao calculados no
INSTANTE da publicacao (contra `hoje`), sem republicar o vencimento de 13/10
seguiu "parcial" e os pendentes seguiram "no prazo" dias depois.

Duas camadas de prova:
  * unitaria — guarda de frescor, CLI e ausencia do step na cadeia (a
    Expedicao saiu para a DAG de 2h do Airflow);
  * integracao contra PostgreSQL DE VERDADE (pulada sem servidor) — o SQL de
    producao classifica a mesma coorte de outro jeito quando `hoje` avanca, a
    reexecucao e' idempotente e a fonte defasada nao escreve nada. Um duble
    concordaria com qualquer coisa; a classificacao mora no SQL.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pipelines.expedicao import tiktok_cli as cli
from pipelines.expedicao import tiktok_daily as td
from pipelines.ops import orchestrate as o
from pipelines.ops import preflight
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel,
)

UTC = timezone.utc


# ===========================================================================
# Guarda de frescor
# ===========================================================================

def test_watermark_ausente_recusa():
    with pytest.raises(cli.FonteNaoPublicavel, match="sem watermark"):
        cli.verificar_frescor(None, datetime(2026, 10, 16, 12, tzinfo=UTC), 6)


def test_watermark_acima_do_limite_recusa():
    agora = datetime(2026, 10, 16, 12, tzinfo=UTC)
    with pytest.raises(cli.FonteNaoPublicavel, match="defasada"):
        cli.verificar_frescor(agora - timedelta(hours=6, minutes=1), agora, 6)


def test_watermark_dentro_do_limite_passa_e_devolve_idade():
    agora = datetime(2026, 10, 16, 12, tzinfo=UTC)
    assert cli.verificar_frescor(agora - timedelta(hours=2), agora, 6) == pytest.approx(2)


@pytest.mark.parametrize("argv", [
    ["--diagnose", "--max-idade-fonte-horas", "6"],      # so' com --apply
    ["--apply", "--max-idade-fonte-horas", "0"],
    ["--apply", "--max-idade-fonte-horas", "73"],
])
def test_cli_recusa_uso_invalido_da_flag_antes_de_conectar(argv, monkeypatch):
    monkeypatch.setattr(cli, "_run_apply", lambda *a, **k: pytest.fail("conectou"))
    monkeypatch.setattr(cli, "_run_diagnose", lambda *a, **k: pytest.fail("conectou"))
    assert cli.main(argv) == cli.EXIT_FALHA


def test_cli_repassa_o_limite_ao_apply(monkeypatch):
    visto = {}
    monkeypatch.setattr(cli, "_run_apply",
                        lambda dias, max_h=None: visto.update(dias=dias, max_h=max_h) or 0)
    assert cli.main(["--apply", "--max-idade-fonte-horas", "6"]) == 0
    assert visto == {"dias": td.JANELA_PADRAO_DIAS, "max_h": 6.0}


# ===========================================================================
# Fora da cadeia diaria (adendo "Expedicao a cada 2 horas no Airflow")
# ===========================================================================

def test_expedicao_nao_esta_em_pipeline_nenhum_do_orquestrador():
    """A tela inteira da Expedicao e' da DAG de 2h (`marketplace_tower_expedicao_refresh`).

    Um step aqui faria duas rotinas escreverem a mesma tabela e poria a cadeia
    para disputar com o PMA o advisory lock 917120017.
    """
    for nome, steps in o.PIPELINES.items():
        modulos = {s.module for s in steps}
        assert not any(m.startswith("pipelines.expedicao") for m in modulos), nome
    assert not hasattr(o, "EXPEDICAO_TIKTOK_STEP")


def test_preflight_da_fonte_continua_disponivel_para_execucao_manual():
    assert "expedicao_tiktok" in preflight.SOURCE_CHECKS


# ===========================================================================
# Integracao: SQL de producao contra PostgreSQL real
# ===========================================================================

pg = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

DDL = """
DROP SCHEMA IF EXISTS raw CASCADE; DROP SCHEMA IF EXISTS marts CASCADE;
DROP SCHEMA IF EXISTS audit CASCADE;
CREATE SCHEMA raw; CREATE SCHEMA marts; CREATE SCHEMA audit;
CREATE TABLE raw.tiktok_shop_orders (
    order_id text PRIMARY KEY, brand text, shop_name text, paid_at timestamp,
    is_sample_order boolean, order_status text, extracted_at timestamp);
CREATE TABLE raw.tiktok_shop_line_items (order_id text, rts_time timestamp);
CREATE TABLE raw.tiktok_shop_order_status_log (
    order_id text, new_status text, updated_at_tiktok timestamp, detected_at timestamp);
CREATE TABLE marts.expedicao_tiktok_dispatch_daily (
    refresh_batch_id uuid NOT NULL, channel text NOT NULL, paid_date date NOT NULL,
    brand text NOT NULL, shop_name text,
    pedidos_pagos_brutos int, amostras_excluidas int,
    despacho_deadline date, despacho_base int, despacho_cancelado_antes_sla int,
    despacho_no_prazo int, despacho_atrasado int,
    despacho_pendente_vencido int, despacho_pendente_no_prazo int, despacho_is_mature boolean,
    coleta_deadline date, coleta_base int, coleta_cancelado_antes_sla int,
    coleta_no_prazo int, coleta_atrasada int,
    coleta_pendente_vencida int, coleta_pendente_no_prazo int, coleta_is_mature boolean,
    effective_at timestamptz, source_watermark_at timestamptz,
    PRIMARY KEY (channel, paid_date, brand));
CREATE TABLE audit.source_sync_run (
    sync_run_id serial PRIMARY KEY, source_name text, marketplace_id int, loja_id int,
    started_at timestamptz, finished_at timestamptz, status text,
    rows_extracted int, rows_loaded int, error_message text,
    source_min_date date, source_max_date date);
"""

PAGO = date(2026, 10, 8)
PRAZO_RTS = td.prazo_do_evento(PAGO, td.Evento.DESPACHO)
PRAZO_TTS = td.prazo_do_evento(PAGO, td.Evento.COLETA)
#: Dia 1: o prazo de despacho ainda nao venceu (vence hoje).
DIA_1 = PRAZO_RTS
#: Dia 2: os dois prazos ja' venceram.
DIA_2 = max(PRAZO_RTS, PRAZO_TTS) + timedelta(days=1)


def _meio_dia_brt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 15, 0, tzinfo=UTC)   # 12:00 BRT


def _preparar(url):
    import psycopg2
    with psycopg2.connect(url) as c, c.cursor() as cur:
        cur.execute(DDL)
        # A: pago, nunca despachado nem coletado — o pedido que muda de classe.
        # B: pago e despachado no prazo — referencia que nao pode mudar.
        cur.executemany(
            "INSERT INTO raw.tiktok_shop_orders VALUES (%s,'apice','loja',%s,false,'AWAITING_SHIPMENT',%s)",
            [("A", f"{PAGO} 12:00", f"{DIA_1} 14:00"),
             ("B", f"{PAGO} 12:00", f"{DIA_1} 14:00")])
        cur.execute("INSERT INTO raw.tiktok_shop_line_items VALUES ('A', NULL), ('B', %s)",
                    (f"{PAGO} 18:00",))


def _ingestao_avancou(url, d: date):
    import psycopg2
    with psycopg2.connect(url) as c, c.cursor() as cur:
        cur.execute("UPDATE raw.tiktok_shop_orders SET extracted_at = %s", (f"{d} 14:00",))


def _ler_serie(url):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    with psycopg2.connect(url, cursor_factory=RealDictCursor) as c, c.cursor() as cur:
        cur.execute("SELECT * FROM marts.expedicao_tiktok_dispatch_daily ORDER BY paid_date, brand")
        return [dict(r) for r in cur.fetchall()]


def _negocio(linhas):
    """A linha sem as colunas de lote/instante, que mudam a cada publicacao."""
    return [{k: v for k, v in r.items()
             if k not in ("refresh_batch_id", "effective_at", "source_watermark_at")}
            for r in linhas]


def _publicar_em(monkeypatch, url, agora: datetime) -> int:
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATAMART_DATABASE_URL", url)
    monkeypatch.setattr(cli, "_agora_utc", lambda: agora)
    return cli.main(["--apply", "--max-idade-fonte-horas", "6"])


@pg
def test_janela_recalculada_amadurece_coorte_e_vence_pendente(monkeypatch):
    with cluster_da_sessao() as url:
        _preparar(url)

        assert _publicar_em(monkeypatch, url, _meio_dia_brt(DIA_1)) == cli.EXIT_OK
        (antes,) = _ler_serie(url)
        assert antes["paid_date"] == PAGO
        assert antes["despacho_is_mature"] is False, "vence hoje: ainda parcial"
        assert antes["despacho_pendente_no_prazo"] == 1
        assert antes["despacho_pendente_vencido"] == 0
        assert antes["despacho_no_prazo"] == 1

        _ingestao_avancou(url, DIA_2)
        assert _publicar_em(monkeypatch, url, _meio_dia_brt(DIA_2)) == cli.EXIT_OK
        (depois,) = _ler_serie(url)
        # A MESMA coorte (pago em 08/10), republicada inteira: parcial -> madura,
        # pendente no prazo -> pendente vencido.
        assert depois["paid_date"] == PAGO
        assert depois["despacho_is_mature"] is True
        assert depois["coleta_is_mature"] is True
        assert depois["despacho_pendente_no_prazo"] == 0
        assert depois["despacho_pendente_vencido"] == 1
        assert depois["coleta_pendente_vencida"] == 2
        assert depois["despacho_no_prazo"] == 1, "o pedido despachado nao muda"
        assert depois["refresh_batch_id"] != antes["refresh_batch_id"]


@pg
def test_reexecucao_e_idempotente(monkeypatch):
    with cluster_da_sessao() as url:
        _preparar(url)
        _ingestao_avancou(url, DIA_2)
        agora = _meio_dia_brt(DIA_2)
        assert _publicar_em(monkeypatch, url, agora) == cli.EXIT_OK
        primeira = _ler_serie(url)
        assert _publicar_em(monkeypatch, url, agora) == cli.EXIT_OK
        segunda = _ler_serie(url)
        assert len(primeira) == len(segunda) == 1
        assert _negocio(primeira) == _negocio(segunda)


@pg
def test_fonte_defasada_recusa_sem_escrever_e_audita_failed(monkeypatch):
    import psycopg2
    with cluster_da_sessao() as url:
        _preparar(url)
        assert _publicar_em(monkeypatch, url, _meio_dia_brt(DIA_1)) == cli.EXIT_OK
        antes = _ler_serie(url)

        # A ingestao parou: watermark continua no DIA_1, a execucao e' 2 dias depois.
        codigo = _publicar_em(monkeypatch, url, _meio_dia_brt(DIA_1 + timedelta(days=2)))
        assert codigo == cli.EXIT_FONTE_NAO_PUBLICAVEL
        assert _ler_serie(url) == antes, "nada pode ter sido reescrito"

        with psycopg2.connect(url) as c, c.cursor() as cur:
            cur.execute("SELECT status, error_message FROM audit.source_sync_run ORDER BY sync_run_id")
            runs = cur.fetchall()
            cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
            locks = cur.fetchone()[0]
        assert [r[0] for r in runs] == ["success", "failed"]
        assert "defasada" in runs[-1][1]
        assert locks == 0
