"""Gate SHOPEE-MANUAL-RETIREMENT-1 — contrato puro do shadow diario da Shopee.

Sem banco: regras de janela, allowlist, montagem do grao, guardas e a
reconciliacao (`comparar` / `classificar_meses`). O comportamento de SQL,
transacao e CHECK fica em `test_sync_shopee_daily_api_shadow_integracao.py`,
contra Postgres real.
"""
from __future__ import annotations

import re
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines import sync_shopee_daily_api_shadow as S
from pipelines.reconciliation import shopee_daily_api_vs_manual as R

REPO = Path(__file__).resolve().parents[2]
HOJE = date(2026, 10, 16)
AGORA = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)


def carregar_migration_027():
    caminho = REPO / "apps" / "api" / "alembic" / "versions" / "027_create_fact_shopee_daily_api_shadow.py"
    fonte = caminho.read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(fonte.replace("from alembic import op", "op = None"), str(caminho), "exec"), ns)  # noqa: S102
    return types.SimpleNamespace(**{k: v for k, v in ns.items() if not k.startswith("__")}), fonte


def bruta(brand="apice", d=date(2026, 10, 1), **kw):
    r = {"brand": brand, "d": d, "orders": 10, "units_sold": 12, "canceled_orders": 2,
         "delivered_orders": 7, "to_return_orders": 1, "unpaid_orders": 0,
         "seller_shipping_cost": 100.0, "product_subtotal": 900.0, "sem_item": 0, "frete_nulo": 0}
    r.update(kw)
    return r


def linha(brand="apice", d=date(2026, 10, 1), **kw) -> S.LinhaDiaria:
    base = dict(date=d, brand=brand, orders=10, units_sold=12, canceled_orders=2, delivered_orders=7,
                to_return_orders=1, unpaid_orders=0, seller_shipping_cost=100.0, product_subtotal=900.0,
                is_partial=False)
    base.update(kw)
    return S.LinhaDiaria(**base)


def snap(rows, marcas=S.CONTAS_API, inicio=date(2026, 9, 1), fim=date(2026, 10, 15)) -> S.Snapshot:
    return S.Snapshot(captured_at=AGORA, inicio=inicio, fim=fim, marcas=marcas,
                      max_ingested_at=AGORA, rows=list(rows))


# ---------------------------------------------------------------------------
# Janela e allowlist
# ---------------------------------------------------------------------------

def test_janela_termina_sempre_em_d_menos_1():
    assert S.janela(HOJE) == (HOJE - timedelta(days=S.DIAS_JANELA), HOJE - timedelta(days=1))
    assert S.janela(HOJE, desde=date(2026, 1, 1)) == (date(2026, 1, 1), date(2026, 10, 15))


def test_janela_vazia_ou_invertida_levanta():
    with pytest.raises(S.ShopeeDailyShadowError):
        S.janela(HOJE, desde=HOJE)
    with pytest.raises(S.ShopeeDailyShadowError):
        S.janela(HOJE, dias=0)


def test_hoje_e_dia_brt_nao_utc():
    # 02:00 UTC de 17/10 ainda e' 16/10 em Brasilia.
    assert S.hoje_brt(datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc)) == date(2026, 10, 16)


@pytest.mark.parametrize("marcas", [("kokeshi",), ("apice", "kokeshi"), ("bysamia",)])
def test_marca_fora_da_allowlist_e_recusada(marcas):
    with pytest.raises(S.ShopeeDailyShadowError, match="kokeshi e' manual"):
        S.validar_marcas(marcas)


def test_allowlist_igual_ao_check_da_migration_e_sem_kokeshi():
    mig, fonte = carregar_migration_027()
    assert tuple(mig.MARCAS) == S.CONTAS_API
    assert "kokeshi" not in fonte.split("UPGRADE_SQL")[1]
    lista = ", ".join(f"'{m}'" for m in S.CONTAS_API)
    assert f"brand IN ({lista})" in fonte


# ---------------------------------------------------------------------------
# Montagem do grao: falha fechada
# ---------------------------------------------------------------------------

def test_montar_linhas_marca_parcial_nos_ultimos_dias():
    corte = HOJE - timedelta(days=S.DIAS_MATURACAO)
    out = S.montar_linhas([bruta(d=corte), bruta(d=corte + timedelta(days=1))], S.CONTAS_API, HOJE)
    assert [r.is_partial for r in out] == [False, True]


def test_pedido_sem_item_aborta_em_vez_de_subcontar():
    with pytest.raises(S.ShopeeDailyShadowError, match="sem item"):
        S.montar_linhas([bruta(sem_item=1)], S.CONTAS_API, HOJE)


def test_frete_ausente_aborta_nunca_vira_zero():
    with pytest.raises(S.ShopeeDailyShadowError, match="ausencia nao vira zero"):
        S.montar_linhas([bruta(frete_nulo=3)], S.CONTAS_API, HOJE)


def test_conta_inesperada_na_fonte_aborta():
    with pytest.raises(S.ShopeeDailyShadowError, match="fora da allowlist"):
        S.montar_linhas([bruta(brand="kokeshi")], S.CONTAS_API, HOJE)


# ---------------------------------------------------------------------------
# Contrato e guardas
# ---------------------------------------------------------------------------

def test_fonte_vazia_aborta():
    with pytest.raises(S.ShopeeDailyShadowError, match="fonte vazia"):
        S.validate_contract(snap([]))


def test_chave_duplicada_aborta():
    with pytest.raises(S.ShopeeDailyShadowError, match="duplicada"):
        S.validate_contract(snap([linha(), linha()]))


@pytest.mark.parametrize("kw", [{"delivered_orders": 11}, {"unpaid_orders": 11}, {"to_return_orders": 11},
                                {"orders": -1}, {"seller_shipping_cost": -0.01}])
def test_linha_incoerente_aborta(kw):
    with pytest.raises(S.ShopeeDailyShadowError):
        S.validate_contract(snap([linha(**kw)]))


def test_linha_fora_da_janela_aborta():
    with pytest.raises(S.ShopeeDailyShadowError, match="fora da janela"):
        S.validate_contract(snap([linha(d=date(2026, 8, 1))]))


def test_conta_sem_pedido_vira_aviso_e_nao_some_em_silencio():
    avisos = S.validate_contract(snap([linha()]))
    assert any("barbours" in a for a in avisos)


def test_colapso_da_fonte_aborta_abaixo_do_piso():
    with pytest.raises(S.ShopeeDailyShadowError, match="colapso"):
        S.verificar_colapso({"apice": 100}, snap([linha(orders=79)]))
    S.verificar_colapso({"apice": 100}, snap([linha(orders=80)]))   # no piso: passa
    S.verificar_colapso({}, snap([linha(orders=1)]))                 # primeira publicacao


def test_marca_que_some_da_fonte_conta_como_zero_no_colapso():
    with pytest.raises(S.ShopeeDailyShadowError, match="barbours"):
        S.verificar_colapso({"barbours": 50}, snap([linha()]))


class _ConnFrescor:
    def __init__(self, cargas):
        self.cargas = cargas

    def execute(self, *_a, **_k):
        rows = [{"brand": b, "ultima_carga": c} for b, c in self.cargas.items()]
        return types.SimpleNamespace(mappings=lambda: types.SimpleNamespace(all=lambda: rows))


def test_frescor_conta_velha_ou_ausente_aborta():
    velho = AGORA - timedelta(hours=S.FRESCOR_MAX_HORAS + 1)
    fresco = AGORA - timedelta(hours=1)
    with pytest.raises(S.ShopeeDailyShadowError, match="apice"):
        S.validate_frescor(_ConnFrescor({"apice": velho, "barbours": fresco}), ("apice", "barbours"), AGORA)
    with pytest.raises(S.ShopeeDailyShadowError, match="sem nenhuma carga"):
        S.validate_frescor(_ConnFrescor({"apice": fresco}), ("apice", "barbours"), AGORA)
    assert S.validate_frescor(_ConnFrescor({"apice": fresco}), ("apice",), AGORA) == fresco


def test_conteudo_ignora_run_id_e_horario():
    a, b = linha(), linha()
    assert S._conteudo(a) == S._conteudo(b)
    assert S._linha(a, "a" * 32, snap([a]))[10:12] == ("api", "a" * 32)


def test_sanitizar_remove_credencial_de_dsn():
    assert "senha" not in S.sanitizar("falhou em postgresql://user:senha@host/db")


# ---------------------------------------------------------------------------
# Isolamento: nenhum consumidor le' o shadow; o shadow nao toca a fato diaria
# ---------------------------------------------------------------------------

def test_nenhum_consumidor_da_api_ou_da_web_le_o_shadow():
    achados = []
    for raiz in ("apps/api/app", "apps/web/src", "apps/web/app"):
        for p in (REPO / raiz).rglob("*"):
            if p.suffix in {".py", ".ts", ".tsx"} and "fact_shopee_daily_api_shadow" in p.read_text(
                    encoding="utf-8", errors="ignore"):
                achados.append(str(p.relative_to(REPO)))
    assert achados == [], f"consumidor lendo o shadow (dupla contagem possivel): {achados}"


def test_publisher_nunca_escreve_na_fato_diaria():
    fonte = (REPO / "pipelines" / "sync_shopee_daily_api_shadow.py").read_text(encoding="utf-8")
    # Codigo sem o docstring do modulo e sem comentarios: o docstring CITA a
    # fato diaria justamente para dizer que nao a toca.
    codigo = re.sub(r"^\s*#.*$", "", fonte.split('"""', 2)[2], flags=re.M)
    assert "fact_marketplace_daily_performance" not in codigo
    # So' alvos com cara de tabela: `{CONSTANTE}` ou `schema.tabela` (evita
    # casar texto de mensagem, como "UPDATE de auditoria").
    escritas = re.findall(r"(?:INSERT INTO|DELETE FROM|UPDATE)\s+(\{[A-Z_]+\}|[a-z_]+\.[a-z_]+)", codigo)
    assert set(escritas) <= {"{SHADOW}", "audit.source_sync_run"}, escritas


def test_reconciliador_so_le():
    fonte = (REPO / "pipelines" / "reconciliation" / "shopee_daily_api_vs_manual.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE)\b", fonte.split('"""', 2)[2])
    assert "READ ONLY" in fonte


def test_migration_027_encadeia_na_026_e_nao_insere_dado():
    mig, fonte = carregar_migration_027()
    assert (mig.revision, mig.down_revision) == ("027", "026")
    assert "INSERT" not in fonte.upper().split("UPGRADE_SQL")[1]
    assert mig.DOWNGRADE_SQL == ["DROP TABLE IF EXISTS marts.fact_shopee_daily_api_shadow"]
    # NaN passa por `>= 0` no Postgres: a faixa precisa do `<> 'NaN'`.
    assert fonte.count("<> 'NaN'") >= 2


# ---------------------------------------------------------------------------
# Reconciliacao (funcao pura)
# ---------------------------------------------------------------------------

def pub(brand="apice", d=date(2026, 1, 5), **kw):
    r = {"brand": brand, "d": d, "orders": 10, "units_sold": 12, "canceled_orders": 2,
         "delivered_orders": 7, "seller_shipping_cost": 100.0}
    r.update(kw)
    return r


def _classe(api, publicado):
    return R.classificar_meses(R.comparar(api, publicado)["divergencias"])


def test_reconciliacao_exata():
    assert _classe([linha(d=date(2026, 1, 5))], [pub()]) == {("apice", "2026-01"): "exato"}


def test_status_amadurecido_e_maturacao_e_nao_defeito():
    # 2 pedidos ativos no export viraram cancelados na API: o total criado nao muda.
    api = [linha(d=date(2026, 8, 5), orders=8, canceled_orders=4, delivered_orders=8)]
    publicado = [pub(d=date(2026, 8, 5), orders=10, canceled_orders=2, delivered_orders=0)]
    assert _classe(api, publicado) == {("apice", "2026-08"): "maturacao"}


def test_pedido_faltando_de_um_lado_e_defeito():
    api = [linha(d=date(2026, 8, 5), orders=9, canceled_orders=2)]
    assert _classe(api, [pub(d=date(2026, 8, 5))]) == {("apice", "2026-08"): "defeito"}


def test_dia_de_um_lado_so_e_reportado_e_nunca_vira_zero():
    res = R.comparar([linha(d=date(2026, 1, 5)), linha(d=date(2026, 1, 6))], [pub(d=date(2026, 1, 5))])
    assert res["dias_comuns"] == 1
    assert res["dias_so_api"] == [("apice", date(2026, 1, 6))]
    pedidos = next(d for d in res["divergencias"] if d.metrica == "orders")
    assert (pedidos.api, pedidos.publicado) == (10.0, 10.0)   # o dia sem par nao entra


def test_publicado_nulo_nao_entra_em_nenhum_lado():
    res = R.comparar([linha(d=date(2026, 1, 5))], [pub(seller_shipping_cost=None)])
    assert not [d for d in res["divergencias"] if d.metrica == "seller_shipping_cost"]


def test_divergencia_sobre_zero_publicado_e_none_nao_infinito():
    assert R.Divergencia("apice", "2026-01", "orders", 5.0, 0.0).pct is None
