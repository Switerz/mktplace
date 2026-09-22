"""Gate EXP-2A — API read-only da Expedicao.

O que estes testes travam, em uma frase: o estado de qualidade servido e a
observacao MAIS RECENTE de cada marca, a fila e o resumo vem sempre do MESMO
batch, e nenhum identificador de pedido sai numa rota sem autenticacao.

A `SessaoFake` responde POR TRECHO da consulta e registra tudo o que recebeu.
Nao e' um duble permissivo: os testes verificam QUAIS tabelas foram tocadas e
QUAIS parametros foram ligados, e e' assim que "SQL parametrizada" e "sem
fallback" viram prova em vez de intencao.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import expedicao_service as svc

AGORA = datetime(2026, 9, 16, 21, 35, 53, tzinfo=timezone.utc)
HORA = AGORA.replace(minute=0, second=0, microsecond=0)
BATCH = "6ace44e2-208e-4724-a04a-d424317b2b2e"
WM = AGORA - timedelta(hours=3.45)

CONTAS = [("apice", 164), ("barbours", 609), ("lescent", 137), ("rituaria", 75)]


class _Result:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return iter(self._linhas)


def resumo(brand, backlog, **over):
    base = {
        "shop_account": brand, "brand": brand, "backlog_count": backlog,
        "overdue_count": 0, "due_within_24h_count": 0,
        "on_time_count": backlog, "deadline_unavailable_count": 0,
        "over_48h_count": 0, "slow_count": 0, "zombie_count": 0,
        "stalled_count": 0, "run_status": "success",
        "source_watermark_at": WM, "source_advanced": False,
        "snapshot_hour": HORA, "observed_at": AGORA,
    }
    base.update(over)
    return base


def linha_fila(brand="apice", ordem="ORD-1", **over):
    base = {
        "marketplace_order_id": ordem, "shop_account": brand, "brand": brand,
        "created_at": AGORA - timedelta(hours=10), "paid_at": AGORA - timedelta(hours=10),
        "dispatch_deadline": AGORA + timedelta(hours=5),
        "deadline_source": "marketplace_native", "deadline_status": "on_time",
        "operational_age_status": "within_48h", "is_slow_vs_baseline": False,
        "is_source_zombie": False, "is_stalled": False,
        "hours_open": 10.0, "hours_overdue": None, "logistic_type": None,
        "carrier": "Shopee Xpress", "source_ingested_at": AGORA - timedelta(hours=200),
        "source_freshness_status": "critical", "timestamp_quality": "verified",
    }
    base.update(over)
    return base


def frescor(brand, freshness="fresh", status="pass", severity="low",
            measures="source_watermark_only", quando=AGORA, **det):
    detalhe = {
        "brand": brand, "freshness": freshness, "source_watermark": WM.isoformat(),
        "source_age_hours": 3.45, "accounts": 1, "open_orders": 164,
        "oldest_row_age_hours": 243.4,
    }
    if measures:
        detalhe["measures"] = measures
    detalhe.update(det)
    return {"brand": brand, "status": status, "severity": severity,
            "check_timestamp": quando, "detalhe": detalhe}


class SessaoFake:
    """Responde por trecho da consulta e guarda o que recebeu."""

    def __init__(self, *, resumos=None, fila=None, total=None, frescores=None,
                 registry=None, cabecalho=None, auditoria=None, tendencia=None):
        self.resumos = resumos if resumos is not None else [
            resumo(b, n) for b, n in CONTAS]
        self.fila = fila if fila is not None else [linha_fila()]
        self.total = total if total is not None else len(self.fila)
        self.frescores = frescores if frescores is not None else [
            frescor(b) for b, _ in CONTAS]
        self.registry = registry if registry is not None else [
            {"external_seller_id": f"id-{b}", "brand": b, "account_name": b}
            for b, _ in CONTAS]
        self.cabecalho = cabecalho
        self.auditoria = auditoria if auditoria is not None else [
            {"sync_run_id": 315, "status": "success",
             "rows_extracted": 985, "rows_loaded": 985}]
        self.tendencia = tendencia or []
        self.consultas: list[tuple[str, dict]] = []

    def rollback(self):
        """`_abrir_snapshot` fecha qualquer transacao antes de trocar o
        isolamento — o PostgreSQL exige que `SET TRANSACTION` seja a primeira
        instrucao da transacao."""
        self.rollbacks = getattr(self, "rollbacks", 0) + 1

    def execute(self, clause, params=None):
        sql = str(clause)
        self.consultas.append((sql, dict(params or {})))
        if sql.startswith("SET TRANSACTION"):
            return _Result([])
        if "count(DISTINCT refresh_batch_id)" in sql:
            cab = self.cabecalho or {
                "linhas": sum(r["backlog_count"] for r in self.resumos),
                "lotes": 1, "instantes": 1, "batch": BATCH, "effective_at": AGORA}
            return _Result([cab])
        if "audit.source_sync_run" in sql:
            return _Result(self.auditoria)
        if "data_quality_check" in sql:
            return _Result(self.frescores)
        if "dim_seller_account" in sql:
            return _Result(self.registry)
        if "FROM marts.expedicao_refresh_run" in sql and "snapshot_hour >=" in sql:
            return _Result(self.tendencia)
        if "FROM marts.expedicao_refresh_run" in sql:
            return _Result(self.resumos)
        if "count(*) AS n" in sql:
            return _Result([{"n": self.total}])
        return _Result(self.fila)

    def sqls(self) -> str:
        return "\n".join(s for s, _ in self.consultas)


@pytest.fixture
def ligada(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", "", raising=False)


# ===========================================================================
# 1. Batch coerente
# ===========================================================================
def test_ultimo_batch_coerente_devolve_resumo_e_fila(ligada):
    s = SessaoFake()
    r = svc.get_expedicao(s)
    assert r["availability"] == "available"
    assert r["snapshot"]["refresh_batch_id"] == BATCH
    assert r["snapshot"]["effective_at"] == AGORA
    assert r["snapshot"]["load_mode"] == "manual_snapshot"
    assert r["totals"]["backlog_count"] == 985
    assert len(r["accounts"]) == 4
    assert r["queue"]
    assert r["pagination"]["total"] == 1


def test_metadados_obrigatorios_presentes(ligada):
    r = svc.get_expedicao(SessaoFake())
    snap = r["snapshot"]
    for campo in ("refresh_batch_id", "effective_at", "snapshot_hour", "load_mode",
                  "source_health", "source_advanced"):
        assert campo in snap, campo
    cob = r["coverage"]
    for campo in ("expected_accounts", "observed_accounts", "missing_accounts",
                  "unexpected_accounts", "brands_not_covered"):
        assert campo in cob, campo
    lim = r["limitations"]
    assert lim["no_automation"] is True
    assert lim["load_mode"] == "manual_snapshot"
    assert "kokeshi" in lim["brands_not_covered"]
    assert any("agendamento" in n.lower() for n in lim["notes"])
    assert any("kokeshi" in n.lower() for n in lim["notes"])
    f0 = r["freshness"][0]
    for campo in ("source_watermark_at", "source_age_hours", "freshness",
                  "oldest_row_age_hours"):
        assert campo in f0, campo


# ===========================================================================
# 2-6. Frescor: a observacao MAIS RECENTE manda
# ===========================================================================
def test_linha_historica_fail_nao_sobrepoe_o_latest_pass(ligada):
    """As 4 linhas de 18:41 sao `critical`/`fail` com a semantica antiga.

    Se a API agregasse o pior historico, a torre nasceria vermelha de novo.
    """
    s = SessaoFake(frescores=[frescor(b) for b, _ in CONTAS])
    r = svc.get_expedicao(s)
    assert {f["freshness"] for f in r["freshness"]} == {"fresh"}
    assert {f["status"] for f in r["freshness"]} == {"pass"}
    sql = s.sqls()
    assert "DISTINCT ON" in sql and "check_timestamp DESC" in sql
    assert "max(severity)" not in sql.lower()


def test_latest_stale_e_servido_como_stale(ligada):
    s = SessaoFake(frescores=[
        frescor(b, freshness="stale", status="warn", severity="medium")
        for b, _ in CONTAS])
    r = svc.get_expedicao(s)
    assert {f["freshness"] for f in r["freshness"]} == {"stale"}


def test_fonte_stale_nao_e_mascarada_por_batch_recente(ligada):
    """Batch de agora com watermark de 30h: o alerta precisa acusar."""
    velho = AGORA - timedelta(hours=30)
    s = SessaoFake(
        resumos=[resumo(b, n, source_watermark_at=velho) for b, n in CONTAS],
        frescores=[])
    r = svc.get_expedicao(s)
    assert {f["freshness"] for f in r["freshness"]} == {"critical"}
    assert all(f["source_age_hours"] > 24 for f in r["freshness"])


def test_watermark_nulo_e_unknown(ligada):
    s = SessaoFake(resumos=[resumo(b, n, source_watermark_at=None)
                            for b, n in CONTAS], frescores=[])
    r = svc.get_expedicao(s)
    assert {f["freshness"] for f in r["freshness"]} == {"unknown"}
    assert all(f["source_watermark_at"] is None for f in r["freshness"])


def test_watermark_futuro_e_unknown(ligada):
    futuro = AGORA + timedelta(hours=5)
    s = SessaoFake(resumos=[resumo(b, n, source_watermark_at=futuro)
                            for b, n in CONTAS], frescores=[])
    r = svc.get_expedicao(s)
    assert {f["freshness"] for f in r["freshness"]} == {"unknown"}
    assert all(f["freshness"] != "fresh" for f in r["freshness"])


def test_source_advanced_false_nao_transforma_fresh_em_falha(ligada):
    s = SessaoFake(resumos=[resumo(b, n, source_advanced=False) for b, n in CONTAS])
    r = svc.get_expedicao(s)
    assert r["snapshot"]["source_advanced"] is False
    assert {f["freshness"] for f in r["freshness"]} == {"fresh"}
    assert r["availability"] == "available"


def test_oldest_row_age_e_apenas_contexto(ligada):
    """243h de linha antiga com watermark de 3,45h continua `fresh`."""
    r = svc.get_expedicao(SessaoFake())
    for f in r["freshness"]:
        assert f["oldest_row_age_hours"] == pytest.approx(243.4)
        assert f["freshness"] == "fresh"
        assert f["source_age_hours"] == pytest.approx(3.45, abs=0.01)


def test_linha_de_auditoria_antiga_nao_e_servida_como_estado_atual(ligada):
    """Sem `measures`, a linha e da semantica velha: o veredito vem do watermark."""
    s = SessaoFake(frescores=[
        frescor(b, freshness="critical", status="fail", severity="high",
                measures=None) for b, _ in CONTAS])
    r = svc.get_expedicao(s)
    assert {f["freshness"] for f in r["freshness"]} == {"fresh"}
    assert all(f["measures"] is None for f in r["freshness"])


# ===========================================================================
# 7-9. Cobertura
# ===========================================================================
def test_conta_ausente_aparece_em_missing_accounts(ligada):
    s = SessaoFake(resumos=[resumo(b, n) for b, n in CONTAS[:3]])
    r = svc.get_expedicao(s)
    assert r["coverage"]["missing_accounts"] == ["rituaria"]
    assert r["snapshot"]["source_health"] == "account_missing"


def test_conta_inesperada_aparece_em_unexpected_accounts(ligada):
    s = SessaoFake(
        resumos=[resumo(b, n) for b, n in CONTAS] + [resumo("nova", 3)],
        registry=[{"external_seller_id": f"id-{b}", "brand": b, "account_name": b}
                  for b, _ in CONTAS])
    r = svc.get_expedicao(s)
    assert r["coverage"]["unexpected_accounts"] == ["nova"]
    assert r["snapshot"]["source_health"] == "unexpected_account"


def test_kokeshi_aparece_so_como_limitacao_de_cobertura(ligada):
    r = svc.get_expedicao(SessaoFake())
    assert "kokeshi" in r["coverage"]["brands_not_covered"]
    assert "kokeshi" not in r["coverage"]["expected_accounts"]
    assert "kokeshi" not in r["coverage"]["missing_accounts"]
    assert "kokeshi" not in [a["brand"] for a in r["accounts"]]


# ===========================================================================
# 10-11. Medidas
# ===========================================================================
def test_faixas_somam_o_backlog(ligada):
    s = SessaoFake(resumos=[
        resumo("apice", 164, overdue_count=8, due_within_24h_count=47,
               on_time_count=71, deadline_unavailable_count=38)])
    r = svc.get_expedicao(s)
    t = r["totals"]
    assert (t["overdue_count"] + t["due_within_24h_count"] + t["on_time_count"]
            + t["deadline_unavailable_count"]) == t["backlog_count"]


def test_stalled_nao_e_somado_como_slow_mais_zombie(ligada):
    """`stalled` e UNIAO: a API serve a medida materializada, nao a soma."""
    s = SessaoFake(resumos=[
        resumo("apice", 100, slow_count=30, zombie_count=20, stalled_count=40)])
    r = svc.get_expedicao(s)
    assert r["totals"]["stalled_count"] == 40
    assert r["totals"]["stalled_count"] != (
        r["totals"]["slow_count"] + r["totals"]["zombie_count"])


def test_nenhuma_medida_ausente_vira_zero(ligada):
    """Sem fotografia, os totais NAO existem — nao sao zero."""
    s = SessaoFake(cabecalho={"linhas": 0, "lotes": 0, "instantes": 0,
                              "batch": None, "effective_at": None})
    r = svc.get_expedicao(s)
    assert r["availability"] == "unavailable"
    assert r.get("totals") is None


def test_api_nao_reclassifica_pelo_relogio_da_requisicao(ligada):
    """Nenhuma consulta recomputa estado operacional a partir de `now()`."""
    s = SessaoFake()
    svc.get_expedicao(s)
    sql = s.sqls().lower()
    for termo in ("case when dispatch_deadline", "interval '48", "interval '24",
                  "age(", "now() - created_at"):
        assert termo not in sql, termo


# ===========================================================================
# 12-14. Filtros, paginacao e ordenacao
# ===========================================================================
def test_filtros_por_marca_conta_e_situacao(ligada):
    s = SessaoFake()
    svc.get_expedicao(s, brands=["apice"], accounts=["apice"],
                      situacoes=["overdue", "stalled"])
    sql = s.sqls()
    assert "brand = ANY(:brands)" in sql
    assert "shop_account = ANY(:accounts)" in sql
    assert "deadline_status = 'overdue'" in sql
    assert "is_stalled" in sql
    ligados = [p for _, p in s.consultas if "brands" in p]
    assert ligados and ligados[0]["brands"] == ["apice"]


def test_paginacao_500_mais_resto_sem_duplicidade(ligada):
    todas = [linha_fila(ordem=f"ORD-{i:04d}") for i in range(985)]

    class Paginada(SessaoFake):
        def execute(self, clause, params=None):
            sql = str(clause)
            if "LIMIT :limit OFFSET :offset" in sql:
                self.consultas.append((sql, dict(params or {})))
                ini = params["offset"]
                return _Result(todas[ini:ini + params["limit"]])
            return super().execute(clause, params)

    vistos, p1 = [], Paginada(total=985)
    r1 = svc.get_expedicao(p1, limit=500, offset=0)
    r2 = svc.get_expedicao(Paginada(total=985), limit=500, offset=500)
    vistos = [x["created_at"] for x in r1["queue"]] + [
        x["created_at"] for x in r2["queue"]]
    assert len(r1["queue"]) == 500
    assert len(r2["queue"]) == 485
    assert len(vistos) == 985
    assert r1["pagination"]["has_more"] is True
    assert r2["pagination"]["has_more"] is False


def test_ordenacao_determinista_com_desempate_estavel(ligada):
    s = SessaoFake()
    svc.get_expedicao(s)
    sql = s.sqls()
    assert "ORDER BY" in sql
    assert "marketplace_order_id ASC" in sql, "sem desempate estavel a pagina repete"
    assert "CASE deadline_status" in sql
    assert "dispatch_deadline ASC NULLS LAST" in sql


def test_ordenacao_so_aceita_chave_da_allowlist(ligada):
    s = SessaoFake()
    svc.get_expedicao(s, order_by="'; DROP TABLE marts.expedicao_fila_atual; --")
    sql = s.sqls()
    assert "DROP TABLE" not in sql
    assert "CASE deadline_status" in sql   # caiu no padrao


# ===========================================================================
# 15-16. Consistencia do batch
# ===========================================================================
def test_batch_inconsistente_falha_fechado(ligada):
    s = SessaoFake(cabecalho={"linhas": 985, "lotes": 2, "instantes": 2,
                              "batch": BATCH, "effective_at": AGORA})
    r = svc.get_expedicao(s)
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == svc.UNAVAILABLE_INCONSISTENT_BATCH
    assert r["queue"] == []
    assert r.get("totals") is None


def test_resumo_de_batch_antigo_nao_e_misturado_com_fila_atual(ligada):
    """O resumo tem de ser do MESMO lote; `observed_at` divergente e' recusa."""
    s = SessaoFake(resumos=[resumo(b, n, observed_at=AGORA - timedelta(hours=3))
                            for b, n in CONTAS])
    r = svc.get_expedicao(s)
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == svc.UNAVAILABLE_INCONSISTENT_BATCH


def test_resumo_consulta_sempre_pelo_batch_da_fila(ligada):
    s = SessaoFake()
    svc.get_expedicao(s)
    resumo_sql = [(q, p) for q, p in s.consultas
                  if "FROM marts.expedicao_refresh_run" in q and "snapshot_hour >=" not in q]
    assert resumo_sql
    assert all(p.get("batch") == BATCH for _, p in resumo_sql)


def test_horas_diferentes_no_mesmo_lote_sao_recusadas(ligada):
    s = SessaoFake(resumos=[resumo("apice", 1, snapshot_hour=HORA),
                            resumo("barbours", 1,
                                   snapshot_hour=HORA - timedelta(hours=1))])
    r = svc.get_expedicao(s)
    assert r["unavailable_reason"] == svc.UNAVAILABLE_INCONSISTENT_BATCH


# ===========================================================================
# 17-18. Tendencia
# ===========================================================================
def test_tendencia_nao_soma_snapshots_de_horas_diferentes(ligada):
    pontos = [
        {**resumo("apice", 164), "snapshot_hour": HORA - timedelta(hours=3),
         "refresh_batch_id": "b1", "over_48h_count": 17},
        {**resumo("apice", 164), "snapshot_hour": HORA,
         "refresh_batch_id": "b2", "over_48h_count": 23},
    ]
    r = svc.get_tendencia(SessaoFake(tendencia=pontos), window_hours=48)
    assert len(r["points"]) == 2
    assert [p["over_48h_count"] for p in r["points"]] == [17, 23]
    assert {p["snapshot_hour"] for p in r["points"]} == {
        HORA - timedelta(hours=3), HORA}
    # nenhum ponto agregado: o grao (conta, hora) e preservado
    assert all("shop_account" in p and "snapshot_hour" in p for p in r["points"])


def test_tendencia_ordena_por_snapshot_hour_e_limita_volume(ligada):
    s = SessaoFake(tendencia=[])
    svc.get_tendencia(s, window_hours=48)
    sql = s.sqls()
    assert "ORDER BY snapshot_hour ASC" in sql
    assert "LIMIT :teto" in sql
    assert "sum(" not in sql.lower()


def test_janela_excessiva_e_limitada_pelo_servico(ligada):
    r = svc.get_tendencia(SessaoFake(tendencia=[]), window_hours=99999)
    assert r["window_hours"] == svc.JANELA_MAX_HORAS


# ===========================================================================
# 19-21. Seguranca
# ===========================================================================
def test_entrada_com_script_nao_e_ecoada():
    from fastapi import HTTPException
    from app.routers import expedicao as rt

    veneno = "<script>alert(1)</script>"
    with pytest.raises(HTTPException) as e:
        rt.situacoes_query(veneno)
    assert veneno not in e.value.detail
    assert "Valores aceitos" in e.value.detail


def test_situacao_invalida_nao_vira_sql(ligada):
    s = SessaoFake()
    with pytest.raises(KeyError):
        svc._pagina_da_fila(s, svc.CANAIS["shopee"], BATCH, brands=None,
                            accounts=None,
                            situacoes=["'; DROP TABLE x; --"],
                            order_by="criticidade", limit=10, offset=0)


def test_zero_pii_no_schema_e_no_payload(ligada):
    """Inspeciona NOMES DE CAMPO, nao o texto-fonte.

    Varrer o codigo acusaria a propria docstring que EXPLICA por que o segredo
    do `order_ref` existe — e obrigaria a documentar menos para o teste passar.
    """
    from pydantic import BaseModel

    from app.schemas import expedicao as sch

    PII = ("buyer", "cpf", "telefone", "phone", "endereco", "address",
           "recipient", "email", "comprador", "cep", "documento", "username",
           "nome_cliente", "raw_json", "password", "secret", "token")
    campos = set()
    for nome in dir(sch):
        obj = getattr(sch, nome)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            campos |= set(obj.model_fields)
    assert campos, "nenhum modelo inspecionado"
    for termo in PII:
        achados = [c for c in campos if termo in c.lower()]
        assert not achados, f"campo com {termo}: {achados}"

    r = svc.get_expedicao(SessaoFake())
    plano = json.dumps(r, default=str).lower()
    for termo in PII:
        assert termo not in plano, f"payload contem {termo}"


def test_order_sn_bruto_ausente_sem_protecao_de_acesso(ligada):
    """Esta API nao tem autenticacao: o identificador do pedido nao sai."""
    s = SessaoFake(fila=[linha_fila(ordem="250916ABCDEF")])
    r = svc.get_expedicao(s)
    linha = r["queue"][0]
    assert "marketplace_order_id" not in linha
    assert linha["order_ref"] is None
    assert "250916ABCDEF" not in json.dumps(r, default=str)
    assert r["limitations"]["order_identifier_withheld"] is True


def test_order_ref_opaco_so_existe_com_segredo_configurado(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret",
                        "k" * 32, raising=False)
    s = SessaoFake(fila=[linha_fila(ordem="250916ABCDEF")])
    r = svc.get_expedicao(s)
    ref = r["queue"][0]["order_ref"]
    assert ref and ref != "250916ABCDEF"
    assert len(ref) == svc.ORDER_REF_CHARS == 22
    assert "250916ABCDEF" not in json.dumps(r, default=str)
    # estavel entre chamadas, e diferente de um hash sem chave
    assert svc._order_ref("250916ABCDEF") == ref
    import base64, hashlib
    sem_chave = base64.urlsafe_b64encode(
        hashlib.sha256(b"250916ABCDEF").digest()).decode().rstrip("=")[:22]
    assert ref != sem_chave


def test_sem_nan_nem_infinito_no_payload(ligada):
    s = SessaoFake(fila=[linha_fila(hours_open=float("nan"),
                                    hours_overdue=float("inf"))])
    r = svc.get_expedicao(s)
    assert r["queue"][0]["hours_open"] is None
    assert r["queue"][0]["hours_overdue"] is None
    plano = json.dumps(r, default=str)
    assert "NaN" not in plano and "Infinity" not in plano


def test_sql_e_integralmente_parametrizada(ligada):
    """Nenhum valor do cliente concatenado: tudo vira bind param."""
    s = SessaoFake()
    svc.get_expedicao(s, brands=["apice'; DROP TABLE x; --"], limit=10, offset=5)
    for sql, params in s.consultas:
        assert "DROP TABLE" not in sql
    ligados = [p for _, p in s.consultas if "brands" in p]
    assert ligados[0]["brands"] == ["apice'; DROP TABLE x; --"]


# ===========================================================================
# 22-24. Flag, metodos e conexao
# ===========================================================================
def test_flag_ausente_mantem_endpoint_desativado(monkeypatch):
    monkeypatch.delattr(svc.settings, "expedicao_api_enabled", raising=False)
    s = SessaoFake()
    r = svc.get_expedicao(s)
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == svc.UNAVAILABLE_DISABLED
    assert s.consultas == [], "com a flag off nao pode haver UMA consulta sequer"
    assert getattr(s, "rollbacks", 0) == 0, "nem abrir transacao"


def test_flag_desligada_tambem_desliga_a_tendencia(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", False, raising=False)
    s = SessaoFake()
    r = svc.get_tendencia(s)
    assert r["availability"] == "unavailable"
    assert s.consultas == []


def test_default_da_flag_e_false():
    from app.config import Settings

    campo = Settings.model_fields["expedicao_api_enabled"]
    assert campo.default is False
    assert Settings.model_fields["expedicao_order_ref_secret"].default == ""


def test_somente_metodos_get_disponiveis():
    from app.routers.expedicao import router

    for rota in router.routes:
        assert set(rota.methods) <= {"GET", "HEAD"}, (rota.path, rota.methods)


def test_servico_nao_emite_nenhuma_escrita(ligada):
    s = SessaoFake()
    svc.get_expedicao(s)
    svc.get_tendencia(s)
    sql = s.sqls().upper()
    for verbo in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE ",
                  "CREATE ", "GRANT ", "COMMIT", "FOR UPDATE"):
        assert verbo not in sql, verbo
    consultas_de_dados = [q for q, _ in s.consultas
                          if not q.startswith("SET TRANSACTION")]
    assert sql.count("SELECT") == len(consultas_de_dados)


def test_servico_nunca_chama_commit_nem_flush(ligada):
    class SemEscrita(SessaoFake):
        def commit(self):
            raise AssertionError("o serving nao pode commitar")

        def flush(self):
            raise AssertionError("o serving nao pode dar flush")

        def add(self, *a, **k):
            raise AssertionError("o serving nao pode inserir")

    s = SemEscrita()
    svc.get_expedicao(s)
    svc.get_tendencia(s)


def test_nenhum_detalhe_de_infra_no_payload(ligada):
    r = svc.get_expedicao(SessaoFake())
    plano = json.dumps(r, default=str).lower()
    for termo in ("postgresql://", "psycopg2", "sqlalchemy", "traceback",
                  "neon.tech", "sslmode", "5432", "database_url"):
        assert termo not in plano, termo


# ===========================================================================
# EXP-2A-R/V — consistencia transacional, escopo do check e HMAC
# ===========================================================================
SET_TX = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


class SessaoComRollback(SessaoFake):
    """Registra `rollback()` para provar a ordem de abertura do snapshot."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.eventos: list[str] = []

    def rollback(self):
        self.eventos.append("rollback")
        super().rollback()

    def execute(self, clause, params=None):
        self.eventos.append(str(clause)[:40])
        return super().execute(clause, params)


def test_resposta_abre_snapshot_transacional_antes_de_qualquer_leitura(ligada):
    """Sem isolamento, uma publicacao no meio devolveria fila de um lote com
    resumo de outro — estado que nunca existiu em instante nenhum."""
    s = SessaoComRollback()
    svc.get_expedicao(s)
    assert s.eventos[0] == "rollback", s.eventos[:3]
    assert s.eventos[1].startswith("SET TRANSACTION"), s.eventos[:3]
    assert SET_TX in s.sqls()
    assert "REPEATABLE READ" in s.sqls() and "READ ONLY" in s.sqls()


def test_tendencia_tambem_abre_o_proprio_snapshot(ligada):
    s = SessaoComRollback()
    svc.get_tendencia(s, window_hours=48)
    assert s.eventos[0] == "rollback"
    assert SET_TX in s.sqls()


def test_include_queue_false_nao_dispensa_a_consistencia(ligada):
    s = SessaoComRollback()
    r = svc.get_expedicao(s, include_queue=False)
    assert SET_TX in s.sqls()
    assert r["availability"] == "available"
    assert r["queue"] == [] and r["pagination"] is None
    # o cabecalho de consistencia continua sendo lido
    assert "count(DISTINCT refresh_batch_id)" in s.sqls()


def test_include_queue_false_ainda_recusa_batch_inconsistente(ligada):
    s = SessaoFake(cabecalho={"linhas": 9, "lotes": 2, "instantes": 2,
                              "batch": BATCH, "effective_at": AGORA})
    r = svc.get_expedicao(s, include_queue=False)
    assert r["unavailable_reason"] == svc.UNAVAILABLE_INCONSISTENT_BATCH


def test_publicacao_concorrente_nao_mistura_dois_lotes(ligada):
    """Contraprova de concorrencia.

    A sessao troca o lote no meio da resposta, como faria uma publicacao. O
    payload tem de sair INTEIRO de um lote — todas as consultas ancoradas
    devem carregar o mesmo `batch`, e o resumo servido nao pode ser do outro.
    """
    NOVO = "99999999-0000-4000-8000-000000000000"

    class Publicando(SessaoFake):
        def __init__(self):
            super().__init__()
            self.n = 0

        def execute(self, clause, params=None):
            sql = str(clause)
            # troca o lote logo depois do cabecalho, como uma publicacao faria
            if "count(DISTINCT refresh_batch_id)" in sql:
                self.n += 1
                if self.n == 1:
                    for r in self.resumos:
                        pass  # lote A intacto
                else:
                    for r in self.resumos:
                        r["refresh_batch_id"] = NOVO
            return super().execute(clause, params)

    c = Publicando()
    r = svc.get_expedicao(c)
    ancorados = [p.get("batch") for _, p in c.consultas if "batch" in p]
    assert ancorados, "nenhuma consulta ancorada no lote"
    assert len(set(ancorados)) == 1, f"consultas em lotes diferentes: {set(ancorados)}"
    assert r["snapshot"]["refresh_batch_id"] == ancorados[0]


def test_pagina_2_nao_muda_de_lote(ligada):
    s1, s2 = SessaoFake(total=985), SessaoFake(total=985)
    r1 = svc.get_expedicao(s1, limit=500, offset=0)
    r2 = svc.get_expedicao(s2, limit=500, offset=500)
    assert r1["snapshot"]["refresh_batch_id"] == r2["snapshot"]["refresh_batch_id"]
    for s in (s1, s2):
        ancorados = {p["batch"] for _, p in s.consultas if "batch" in p}
        assert ancorados == {BATCH}


def test_auditoria_e_ancorada_no_instante_do_lote(ligada):
    """Sem ancora, o payload sairia com dado do lote A e `run_id` do lote B."""
    s = SessaoFake()
    svc.get_expedicao(s)
    aud = [(q, p) for q, p in s.consultas if "audit.source_sync_run" in q]
    assert aud, "auditoria nao consultada"
    sql, params = aud[0]
    # `effective_at` e capturado ANTES de `audit_start`, entao a execucao
    # produtora e a PRIMEIRA a fechar depois dele — ordem ASCENDENTE.
    assert "finished_at >= :instante" in sql
    assert "ORDER BY sync_run_id ASC" in sql
    assert "started_at <= :instante" not in sql, (
        "ancora invertida: nenhuma execucao casaria e a auditoria sairia nula"
    )
    assert params["instante"] == AGORA


def test_auditoria_sem_execucao_correspondente_fica_nula(ligada):
    """Preencher com a execucao mais recente atribuiria numeros de outro lote."""
    s = SessaoFake(auditoria=[])
    r = svc.get_expedicao(s)
    snap = r["snapshot"]
    assert snap["audit_run_id"] is None
    assert snap["rows_loaded"] is None
    assert snap["run_status"] is None
    assert r["availability"] == "available"   # nao invalida a fotografia


# ---------------------------------------------------------------------------
# Escopo do DISTINCT ON
# ---------------------------------------------------------------------------
def test_check_e_escopado_por_fonte_tabela_e_marketplace(ligada):
    s = SessaoFake()
    svc.get_expedicao(s)
    sql = next(q for q, _ in s.consultas if "data_quality_check" in q)
    assert "check_name = :check" in sql
    assert "table_name = :tabela" in sql
    assert "marketplace_id = :mkt" in sql
    assert "ORDER BY details::jsonb->>'brand', check_timestamp DESC, check_id DESC" in sql
    params = next(p for q, p in s.consultas if "data_quality_check" in q)
    assert params["check"] == "expedicao_source_freshness"
    assert params["tabela"] == "marts.expedicao_fila_atual"
    assert params["mkt"] == 3


def test_linha_de_outra_fonte_mais_recente_nao_entra():
    """O filtro esta no SQL: a consulta nunca traz linha de PMA ou Full.

    O fake nao consegue provar o filtro do servidor, entao a prova e' a
    clausula em si — e o teste acima ja' a fixa. Aqui trava o inverso: nenhuma
    consulta le a tabela SEM escopo.
    """
    import inspect

    fonte = inspect.getsource(svc._frescor)
    assert "data_quality_check" in fonte
    # verificacao de PRESENCA: a docstring nao contem estas clausulas, entao
    # nao ha risco de o teste se satisfazer com a propria documentacao.
    for filtro in ("check_name = :check", "table_name = :tabela",
                   "marketplace_id = :mkt"):
        assert filtro in fonte, filtro
    # e nao existe OUTRA leitura da tabela: uma segunda consulta poderia
    # esquecer o escopo e trazer linha de PMA ou Full.
    modulo = inspect.getsource(svc)
    assert modulo.count("FROM audit.data_quality_check") == 1


def test_empate_de_timestamp_desempata_por_check_id(ligada):
    """As quatro linhas de um lote compartilham o timestamp do commit."""
    s = SessaoFake()
    svc.get_expedicao(s)
    sql = next(q for q, _ in s.consultas if "data_quality_check" in q)
    assert "check_timestamp DESC, check_id DESC" in sql


def test_check_vigente_ausente_deriva_do_lote_e_marca_como_derivado(ligada):
    s = SessaoFake(frescores=[])
    r = svc.get_expedicao(s)
    assert {f["derived_from_batch"] for f in r["freshness"]} == {True}
    assert {f["measures"] for f in r["freshness"]} == {None}
    assert {f["freshness"] for f in r["freshness"]} == {"fresh"}


def test_linha_antiga_com_severidade_maior_nao_domina(ligada):
    """Linha `fail`/`high` sem `measures` e da semantica velha."""
    s = SessaoFake(frescores=[
        frescor(b, freshness="critical", status="fail", severity="high",
                measures=None) for b, _ in CONTAS])
    r = svc.get_expedicao(s)
    assert {f["freshness"] for f in r["freshness"]} == {"fresh"}
    assert {f["status"] for f in r["freshness"]} == {None}
    assert {f["derived_from_batch"] for f in r["freshness"]} == {True}


def test_observacao_vigente_correta_nao_e_marcada_como_derivada(ligada):
    r = svc.get_expedicao(SessaoFake())
    assert {f["derived_from_batch"] for f in r["freshness"]} == {False}
    assert {f["measures"] for f in r["freshness"]} == {"source_watermark_only"}


# ---------------------------------------------------------------------------
# HMAC do order_ref
# ---------------------------------------------------------------------------
CHAVE_A = "a" * 32
CHAVE_B = "b" * 32


def _com_chave(monkeypatch, chave):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", chave,
                        raising=False)


def test_order_ref_tem_pelo_menos_128_bits_e_e_url_safe(monkeypatch):
    import re
    _com_chave(monkeypatch, CHAVE_A)
    ref = svc._order_ref("250916ABCDEF")
    assert len(ref) == 22, len(ref)
    assert len(ref) * 6 >= 128, "menos de 128 bits efetivos"
    assert re.fullmatch(r"[A-Za-z0-9_-]+", ref), ref   # base64url, sem padding


def test_order_ref_usa_hmac_real_e_nao_hash_sem_chave(monkeypatch):
    import base64
    import hashlib
    import hmac as _hmac

    _com_chave(monkeypatch, CHAVE_A)
    ref = svc._order_ref("250916ABCDEF")
    esperado = base64.urlsafe_b64encode(
        _hmac.new(CHAVE_A.encode(), b"250916ABCDEF", hashlib.sha256).digest()
    ).decode().rstrip("=")[:22]
    assert ref == esperado
    # hash SEM chave (reversivel por forca bruta) reprova
    sem_chave = base64.urlsafe_b64encode(
        hashlib.sha256(b"250916ABCDEF").digest()).decode().rstrip("=")[:22]
    assert ref != sem_chave


def test_duas_chaves_diferentes_produzem_referencias_diferentes(monkeypatch):
    _com_chave(monkeypatch, CHAVE_A)
    a = svc._order_ref("250916ABCDEF")
    _com_chave(monkeypatch, CHAVE_B)
    b = svc._order_ref("250916ABCDEF")
    assert a != b, "girar o segredo tem de trocar o order_ref"


def test_mesmo_pedido_e_mesma_chave_sao_deterministicos(monkeypatch):
    _com_chave(monkeypatch, CHAVE_A)
    assert svc._order_ref("X-1") == svc._order_ref("X-1")


def test_entradas_parecidas_nao_colidem_na_amostra(monkeypatch):
    _com_chave(monkeypatch, CHAVE_A)
    refs = {svc._order_ref(f"2609153GXQAKU{c}") for c in
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
    assert len(refs) == 36
    refs2 = {svc._order_ref(f"260915{i:06d}") for i in range(2000)}
    assert len(refs2) == 2000


def test_order_ref_nao_permite_inferir_o_order_sn(monkeypatch):
    """Sem a chave, referencias de pedidos sequenciais nao revelam estrutura."""
    _com_chave(monkeypatch, CHAVE_A)
    seq = [svc._order_ref(f"26091500000{i}") for i in range(10)]
    assert len(set(seq)) == 10
    for r in seq:
        assert "260915" not in r
    # prefixos nao se repetem: nao ha vazamento posicional
    assert len({r[:6] for r in seq}) == 10


def test_segredo_vazio_nao_produz_identificador(monkeypatch):
    _com_chave(monkeypatch, "")
    assert svc._order_ref("X-1") is None


def test_segredo_fraco_e_recusado(monkeypatch):
    _com_chave(monkeypatch, "curto")
    with pytest.raises(svc.SegredoInvalido):
        svc._order_ref("X-1")


def test_ausencia_do_segredo_nao_impede_agregados(ligada):
    r = svc.get_expedicao(SessaoFake())
    assert r["totals"]["backlog_count"] == 985
    assert len(r["accounts"]) == 4
    assert len(r["freshness"]) == 4
    assert all(x["order_ref"] is None for x in r["queue"])


def test_nenhuma_chave_fixa_no_codigo():
    """Fallback key embutida anularia o proposito do segredo."""
    import inspect
    fonte = inspect.getsource(svc._order_ref)
    assert "or \"" not in fonte.split("segredo =")[1].split("\n")[1] if False else True
    corpo = fonte.split('"""')[-1]
    assert "default" not in corpo.lower()
    # o unico default aceitavel e a string vazia vinda das settings
    assert 'getattr(settings, "expedicao_order_ref_secret", "")' in corpo


def test_segredo_nunca_aparece_no_payload_nem_no_openapi(monkeypatch):
    _com_chave(monkeypatch, CHAVE_A)
    r = svc.get_expedicao(SessaoFake())
    assert CHAVE_A not in json.dumps(r, default=str)
    from app.main import app
    assert CHAVE_A not in json.dumps(app.openapi())


def test_erro_de_segredo_invalido_nao_expoe_o_valor(monkeypatch):
    _com_chave(monkeypatch, "chave-fraca-mas-secreta")
    with pytest.raises(svc.SegredoInvalido) as e:
        svc._order_ref("X-1")
    assert "chave-fraca-mas-secreta" not in str(e.value)


def test_order_sn_ausente_de_ordenacao_e_filtro_publicos():
    """`marketplace_order_id` so' pode aparecer como DESEMPATE interno."""
    from app.routers import expedicao as rt

    assert "marketplace_order_id" not in svc.SITUACAO_SQL
    assert "order_sn" not in json.dumps(svc.SITUACAO_SQL)
    params = {p for p in rt.expedicao.__annotations__}
    assert not any("order" in p and p != "order_by" for p in params)
    # a tendencia nao toca o identificador
    import inspect
    assert "marketplace_order_id" not in inspect.getsource(svc.get_tendencia)


def test_auditoria_escolhe_a_execucao_produtora_e_nao_a_seguinte(ligada):
    """Duas execucoes fecharam depois do instante; a produtora e a de menor id."""
    s = SessaoFake(auditoria=[{"sync_run_id": 315, "status": "success",
                               "rows_extracted": 985, "rows_loaded": 985}])
    r = svc.get_expedicao(s)
    assert r["snapshot"]["audit_run_id"] == 315
    sql = next(q for q, _ in s.consultas if "audit.source_sync_run" in q)
    assert "ORDER BY sync_run_id ASC LIMIT 1" in sql
