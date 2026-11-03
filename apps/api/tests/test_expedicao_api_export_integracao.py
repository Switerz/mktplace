"""EXPEDICAO-KOKESHI-EXPORT — ponta a ponta contra PostgreSQL REAL.

Migration 034 -> carga do export pelo CLI do operador (arquivo .xlsx de
verdade) -> API juntando a fila agendada (quatro contas) com a do export.
Exige `EXPEDICAO_API_TEST_DSN` (banco descartavel), como os demais testes de
integracao da Expedicao.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services import expedicao_service as svc

DSN = os.environ.get("EXPEDICAO_API_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="EXPEDICAO_API_TEST_DSN ausente - exige PostgreSQL descartavel")

RAIZ = Path(__file__).resolve().parents[3]
BRT = ZoneInfo("America/Sao_Paulo")
LOJAS = {"apice": 1, "barbours": 2, "kokeshi": 3, "lescent": 4, "rituaria": 5}
LOTE = "cccccccc-3333-4333-8333-cccccccccccc"
CABECALHO = [
    "ID do pedido", "Status do pedido", "Cancelar Motivo", "Número de rastreamento",
    "Opção de envio", "Data de criação do pedido", "Hora do pagamento do pedido",
    "Data prevista de envio", "Tempo de Envio", "Pedido FBS",
    "Nome do destinatário", "Telefone", "Endereço de entrega",
]


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _prazo(dias: int) -> datetime:
    """23:59 de Brasilia, `dias` depois de hoje (dia de Brasilia)."""
    hoje = _agora().astimezone(BRT).date()
    return datetime.combine(hoje + timedelta(days=dias), time(23, 59), tzinfo=BRT)


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine, text

    ambiente = dict(os.environ)
    ambiente["DATABASE_URL"] = DSN
    ambiente["PYTHONPATH"] = str(RAIZ)
    api = RAIZ / "apps" / "api"
    roteiro = ("import alembic.config as C, sys;"
               "sys.path.insert(0, r%s);" % repr(str(api))
               + "C.main(argv=['upgrade', 'head'])")
    r = subprocess.run([sys.executable, "-P", "-c", roteiro], cwd=str(api),
                       env=ambiente, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"alembic indisponivel: {r.stderr[-300:]}")

    eng = create_engine(DSN)
    with eng.begin() as c:
        c.execute(text("INSERT INTO marts.dim_empresa (empresa_id, nome_empresa,"
                       " nome_normalizado) VALUES (1, 'GoBeaute', 'gobeaute')"
                       " ON CONFLICT DO NOTHING"))
        for marca, loja in LOJAS.items():
            c.execute(text("INSERT INTO marts.dim_loja (loja_id, empresa_id,"
                           " brand_key, nome_loja, nome_normalizado)"
                           " VALUES (:l, 1, :b, :n, :b) ON CONFLICT DO NOTHING"),
                      {"l": loja, "b": marca, "n": marca.title()})
        c.execute(text("INSERT INTO marts.dim_marketplace (marketplace_id,"
                       " nome_marketplace, slug) VALUES (3, 'Shopee', 'shopee')"
                       " ON CONFLICT DO NOTHING"))
        for i, marca in enumerate(("apice", "barbours", "lescent", "rituaria")):
            c.execute(text("INSERT INTO marts.dim_seller_account (marketplace_id,"
                           " loja_id, external_seller_id, account_name, ativo)"
                           " VALUES (3, :l, :e, :a, true) ON CONFLICT DO NOTHING"),
                      {"l": LOJAS[marca], "e": f"77700000{i}", "a": f"SH {marca}"})
        # A migration so' cadastra a Kokeshi com a loja existente (producao);
        # aqui a loja nasceu agora.
        c.execute(text("INSERT INTO marts.expedicao_conta_export (channel,"
                       " shop_account, external_seller_id, brand, account_name)"
                       " VALUES ('shopee', 'kokeshi', '395166866', 'kokeshi',"
                       " 'Shopee Kokeshi') ON CONFLICT DO NOTHING"))
    yield eng
    eng.dispose()


def _publicar_agendado(eng, agora: datetime) -> None:
    """A fotografia do publicador agendado: quatro contas, um lote."""
    from sqlalchemy import text

    hora = agora.replace(minute=0, second=0)
    with eng.begin() as c:
        for tabela in ("marts.expedicao_fila_atual", "marts.expedicao_refresh_run"):
            c.execute(text(f"DELETE FROM {tabela} WHERE channel = 'shopee'"))
        for marca in ("apice", "barbours", "lescent", "rituaria"):
            prazos = [_prazo(0), _prazo(1)] if marca == "apice" else [_prazo(1)]
            for i, prazo in enumerate(prazos):
                c.execute(text("""
                    INSERT INTO marts.expedicao_fila_atual (
                        effective_at, refresh_batch_id, channel, shop_account,
                        marketplace_order_id, brand, created_at, paid_at,
                        dispatch_deadline, deadline_source, deadline_status,
                        hours_overdue, hours_open, operational_age_status,
                        is_slow_vs_baseline, is_source_zombie, is_stalled,
                        timestamp_quality, source_freshness_status,
                        source_ingested_at, logistic_type, etapa)
                    VALUES (:ef, :lote, 'shopee', :sa, :oid, :sa, :cr, :cr, :dl,
                            'marketplace_native', 'on_time', NULL, 10.0,
                            'within_48h', false, false, false, 'verified',
                            'fresh', :ef, NULL, 'a_preparar')"""), {
                    "ef": agora, "lote": LOTE, "sa": marca, "oid": f"{marca}-{i}",
                    "cr": agora - timedelta(hours=10), "dl": prazo})
            c.execute(text("""
                INSERT INTO marts.expedicao_refresh_run (
                    refresh_batch_id, channel, shop_account, brand, snapshot_hour,
                    observed_at, source_watermark_at, source_advanced,
                    backlog_count, overdue_count, due_within_24h_count,
                    on_time_count, deadline_unavailable_count, over_48h_count,
                    slow_count, zombie_count, stalled_count, run_status, ingested_at)
                VALUES (:lote, 'shopee', :sa, :sa, :hora, :ef, :ef, true,
                        :n, 0, 0, :n, 0, 0, 0, 0, 0, 'success', :ef)"""), {
                "lote": LOTE, "sa": marca, "hora": hora, "ef": agora,
                "n": len(prazos)})


def _brt(instante: datetime) -> str:
    return instante.astimezone(BRT).strftime("%Y-%m-%d %H:%M")


def _export(pasta: Path, pedidos: list[tuple]) -> Path:
    import openpyxl

    hoje = _agora().astimezone(BRT).date()
    nome = (f"Order.all.order_creation_date.{hoje - timedelta(days=29):%Y%m%d}_"
            f"{hoje:%Y%m%d}.xlsx")
    livro = openpyxl.Workbook()
    folha = livro.active
    folha.title = "orders"
    folha.append(CABECALHO)
    criado = _brt(_agora() - timedelta(hours=20))
    for sn, status, rastreio, prazo, envio in pedidos:
        folha.append([sn, status, "", rastreio, "Shopee Xpress", criado, criado,
                      _brt(prazo), envio, "No", "Comprador Ficticio", "000", "Rua X"])
    caminho = pasta / nome
    livro.save(caminho)
    return caminho


def _carregar(caminho: Path) -> int:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from pipelines.expedicao import shopee_export_cli as C

    def abrir():
        conn = psycopg2.connect(DSN, cursor_factory=RealDictCursor)
        conn.autocommit = False
        return conn

    # Relogio com microssegundo, como o `main` do CLI: duas cargas no mesmo
    # segundo empatariam o `observed_at`, e o resumo da segunda nao substitui
    # o da primeira (`WHERE EXCLUDED.observed_at >`).
    return C.executar([caminho], "kokeshi", datetime.now(timezone.utc), aplicar=True,
                      max_idade=timedelta(hours=3), open_target=abrir,
                      open_audit=lambda: psycopg2.connect(DSN, cursor_factory=RealDictCursor),
                      saida=lambda _m: None, log=print)


@pytest.fixture
def juntas(engine, tmp_path, monkeypatch):
    """Fila agendada (4 contas) + carga da Kokeshi pelo export."""
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", "", raising=False)
    _publicar_agendado(engine, _agora() - timedelta(minutes=30))
    enviado = _brt(_agora() - timedelta(hours=4))
    codigo = _carregar(_export(tmp_path, [
        ("K-HOJE", "A Enviar", "", _prazo(0), ""),
        ("K-AMANHA", "A Enviar", "BR1", _prazo(1), ""),
        ("K-VENCIDO", "A Enviar", "", _prazo(-1), ""),
        ("K-ENVIADO", "Enviado", "BR2", _prazo(0), enviado),
    ]))
    assert codigo == 0
    S = sessionmaker(bind=engine)
    with S() as s:
        yield s, engine, tmp_path


def test_a_kokeshi_entra_na_resposta_da_shopee(juntas):
    sessao, _e, _p = juntas
    r = svc.get_expedicao(sessao, channel="shopee", include_queue=False)
    assert r["availability"] == "available"
    contas = {a["shop_account"]: a for a in r["accounts"]}
    assert set(contas) == {"apice", "barbours", "lescent", "rituaria", "kokeshi"}
    k = contas["kokeshi"]
    assert (k["backlog_count"], k["overdue_count"]) == (3, 1)
    assert (k["due_today_count"], k["due_next_day_count"], k["due_soon_count"]) == (1, 1, 2)
    assert k["observed_at"] > r["snapshot"]["effective_at"]
    # Totais = agendada (5) + Kokeshi (3); perto de vencer = 5 + 2.
    assert r["totals"]["backlog_count"] == 8
    assert r["totals"]["overdue_count"] == 1
    assert r["totals"]["due_soon_count"] == 7
    assert r["snapshot"]["source_health"] == "healthy"
    assert r["coverage"]["brands_not_covered"] == []
    assert r["coverage"]["unexpected_accounts"] == []
    assert r["limitations"]["brands_not_covered"] == []
    notas = r["limitations"]["notes"]
    assert svc.NOTA_KOKESHI_SEM_FONTE not in notas
    assert any("kokeshi: fila do export" in n for n in notas)
    frescor = {f["brand"]: f for f in r["freshness"]}
    assert frescor["kokeshi"]["freshness"] == "fresh"


def test_listas_e_cartao_contam_a_kokeshi_igual(juntas):
    sessao, _e, _p = juntas
    perto = svc.get_expedicao(sessao, channel="shopee", situacoes=["due_soon"],
                              limit=500)
    assert perto["pagination"]["total"] == perto["totals"]["due_soon_count"] == 7
    so_k = svc.get_expedicao(sessao, channel="shopee", brands=["kokeshi"], limit=500)
    assert so_k["pagination"]["total"] == 3
    assert {q["brand"] for q in so_k["queue"]} == {"kokeshi"}
    vencidos = svc.get_expedicao(sessao, channel="shopee", situacoes=["overdue"])
    assert [q["brand"] for q in vencidos["queue"]] == ["kokeshi"]


def test_jornada_e_tendencia_incluem_a_kokeshi(juntas):
    sessao, _e, _p = juntas
    fila = svc.get_jornada(sessao, channel="shopee")["fila_aberta"]
    assert fila["base"] == 8
    etapas = {e["key"]: e["count"] for e in fila["etapas"]}
    assert etapas["aguardando_coleta"] == 1          # K-AMANHA (com rastreio)
    assert etapas["a_preparar"] == 7
    pontos = svc.get_tendencia(sessao, channel="shopee", window_hours=6)["points"]
    assert "kokeshi" in {p["shop_account"] for p in pontos}


def test_nova_carga_substitui_so_a_kokeshi(juntas):
    sessao, engine, pasta = juntas
    nova = pasta / "nova"
    nova.mkdir()
    assert _carregar(_export(nova, [("K-HOJE", "A Enviar", "", _prazo(0), "")])) == 0
    sessao.rollback()
    r = svc.get_expedicao(sessao, channel="shopee", include_queue=False)
    contas = {a["shop_account"]: a["backlog_count"] for a in r["accounts"]}
    assert contas == {"apice": 2, "barbours": 1, "lescent": 1, "rituaria": 1,
                      "kokeshi": 1}
    from sqlalchemy import text
    with engine.connect() as c:
        estado = c.execute(text(
            "SELECT order_status FROM marts.expedicao_shopee_export_pedido"
            " WHERE order_sn = 'K-ENVIADO'")).scalar()
    # O pedido que nao veio na carga nova continua com o ultimo estado conhecido.
    assert estado == "SHIPPED"


def test_fila_do_export_incoerente_fica_fora_sem_derrubar_a_shopee(juntas):
    sessao, engine, _p = juntas
    from sqlalchemy import text
    with engine.begin() as c:
        c.execute(text("DELETE FROM marts.expedicao_fila_export"
                       " WHERE marketplace_order_id = 'K-HOJE'"))
    sessao.rollback()
    r = svc.get_expedicao(sessao, channel="shopee", include_queue=False)
    assert r["availability"] == "available"
    assert "kokeshi" not in {a["shop_account"] for a in r["accounts"]}
    assert r["coverage"]["brands_not_covered"] == ["kokeshi"]
    assert r["totals"]["backlog_count"] == 5
