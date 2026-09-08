"""Gate AVH-4B-S Task 1/2 — contrato do serving read-only do snapshot Avoe.

Sem fixture e sem `parametrize`, para que cada funcao seja chamavel isolada. A
`Session` e' de mentira e responde por padrao de SQL, entao nada aqui toca
banco, rede ou arquivo.

A secao final sobe o app FastAPI de verdade com `TestClient`, o que prova a
traducao HTTP (200 com estado vazio x 500 sanitizado) e o schema OpenAPI.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.services import avoe_snapshot_service as svc

SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "avoe_snapshot_service.py"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "app" / "schemas" / "avoe_snapshot.py"
ROUTER_PATH = Path(__file__).resolve().parents[1] / "app" / "routers" / "performance.py"

ROTA = "/api/v1/performance/avoe-snapshot"

#: Captura de referencia, igual a do snapshot 285 medido no Neon.
CAP = datetime(2026, 9, 1, 15, 32, 34, 320000, tzinfo=timezone.utc)
CAP_ANTIGA = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
IMPORTADO = datetime(2026, 9, 8, 15, 56, 59, 204546, tzinfo=timezone.utc)
IMPORTADO_ANTIGO = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)
SNAP = "c8f6be83e576" + "0" * 20
SNAP_ANTIGO = "aa11bb22cc33" + "0" * 20

MARCAS = ["Apice", "Barbours", "Denavita", "GoCase", "Kokeshi", "Lescent", "Yenzah"]
CANAIS = ["magalu", "shein", "kwai", "beleza_na_web", "rd_marketplace", "amazon"]


# ---------------------------------------------------------------------------
# Construcao das linhas — espelha as colunas reais medidas no Neon
# ---------------------------------------------------------------------------

def _meta(brand, valor, ref_month=date(2026, 8, 1), status_moeda="assumed_unconfirmed"):
    return {
        "brand": brand,
        "brand_key": brand.lower(),
        "ref_month": ref_month,
        "target_amount": Decimal(str(valor)),
        "currency_code": "BRL",
        "currency_status": status_moeda,
        "currency_warning": "moeda assumida BRL; origem nao declara",
        "source_recorded_at": datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
    }


def _canal(brand, channel, valor, ref_month=date(2026, 8, 1), dias=20):
    return {
        "brand": brand,
        "brand_key": brand.lower(),
        "channel": channel,
        "channel_source_label": channel.upper(),
        "ref_month": ref_month,
        "reported_amount": None if valor is None else Decimal(str(valor)),
        "is_proxy": True,
        "definition_status": "unconfirmed",
        "definition_warning": "definicao nao confirmada pela Avoe",
        "currency_code": "BRL",
        "currency_status": "assumed_unconfirmed",
        "days_covered": dias,
        "first_business_date": ref_month,
        "last_business_date": ref_month.replace(day=min(28, dias)),
        "coverage_status": "partial_month",
        "source_recorded_at": None,
    }


def _sete_metas():
    valores = ["5000000.00", "10000000.00", "5000000.00", "5000000.00",
               "10000000.00", "5000000.00", "5000000.00"]
    return [_meta(m, v) for m, v in zip(MARCAS, valores)]


def _vinte_quatro_canais():
    linhas = []
    for i, mes in enumerate((date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1))):
        for j, canal in enumerate(CANAIS):
            linhas.append(_canal(MARCAS[j % len(MARCAS)], canal,
                                 f"{1000 * (i + 1) + j}.15", ref_month=mes))
    # 18 linhas; completa 24 com seis marcas extras em agosto.
    for k in range(6):
        linhas.append(_canal(MARCAS[(k + 1) % len(MARCAS)], CANAIS[k],
                             f"{500 + k}.07", ref_month=date(2026, 8, 1)))
    return linhas


def _run(sync_run_id=285, status="success",
         started=IMPORTADO - timedelta(seconds=1),
         finished=IMPORTADO + timedelta(seconds=2)):
    return {"sync_run_id": sync_run_id, "status": status,
            "started_at": started, "finished_at": finished}


def _candidata(captured_at=CAP, metas=None, canais=None, snapshot_id=SNAP,
               importado=IMPORTADO, **over):
    metas = _sete_metas() if metas is None else metas
    canais = _vinte_quatro_canais() if canais is None else canais
    base = {
        "captured_at": captured_at,
        "targets_count": len(metas),
        "target_snapshots": 1,
        "target_imports": 1,
        "target_grao": len({(m["ref_month"], m["brand"]) for m in metas}),
        "target_imp_min": importado,
        "target_imp_max": importado,
        "target_snapshot_id": snapshot_id,
        "currency_code": "BRL",
        "currency_status": "assumed_unconfirmed",
        "moedas": 1,
        "status_moeda": 1,
        "channel_rows_count": len(canais),
        "channel_snapshots": 1,
        "channel_imports": 1,
        "channel_grao": len({(c["ref_month"], c["brand"], c["channel"]) for c in canais}),
        "channel_imp_min": importado,
        "channel_imp_max": importado,
        "channel_snapshot_id": snapshot_id,
    }
    base.update(over)
    return base


class FakeSession:
    """Responde por padrao de SQL. Registra as consultas para contagem."""

    def __init__(self, candidatas=None, runs=None, metas=None, canais=None,
                 erro_em=None):
        self.candidatas = [] if candidatas is None else candidatas
        self.runs = [_run()] if runs is None else runs
        self.metas = _sete_metas() if metas is None else metas
        self.canais = _vinte_quatro_canais() if canais is None else canais
        self.erro_em = erro_em
        self.sqls: list[str] = []
        self.params: list[dict] = []

    def execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        self.sqls.append(sql)
        self.params.append(params or {})
        baixo = sql.lower()
        if self.erro_em and self.erro_em in baixo:
            raise RuntimeError(
                "FATAL: postgres" + "://u:senha@host-interno:5432/db recusou; "
                "SQL: SELECT brand FROM marts.proxy_avoe_brand_monthly_target_snapshot")
        if "with capturas as" in baixo:
            return _Result(self.candidatas)
        if "audit.source_sync_run" in baixo:
            return _Result(self.runs)
        if "target_amount" in baixo:
            return _Result(self.metas)
        if "reported_amount" in baixo:
            return _Result(self.canais)
        return _Result([])

    def close(self):
        pass


class _Result:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return list(self._linhas)


# ---------------------------------------------------------------------------
# 1. Ultima captura valida
# ---------------------------------------------------------------------------

def test_ultima_captura_valida_e_servida():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["status"] == "available"
    assert r["meta"]["source"] == "avoe_hub"
    assert r["meta"]["source_kind"] == "external_manual_snapshot"
    assert r["meta"]["is_official_torre_source"] is False
    assert r["meta"]["captured_at"] == CAP.isoformat()
    assert r["meta"]["snapshot_id"] == SNAP
    assert r["meta"]["sync_run_id"] == 285
    assert r["meta"]["sync_run_status"] == "success"
    assert r["meta"]["sync_run_link_method"] == "audit_time_window"
    assert r["meta"]["unavailable_reason"] is None
    assert r["meta"]["refreshed_at"], "refreshed_at proprio da resposta"
    assert r["meta"]["refreshed_at"] != r["meta"]["captured_at"]


def test_sete_metas_e_vinte_quatro_canais():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["targets_count"] == 7
    assert r["meta"]["channel_rows_count"] == 24
    assert len(r["targets"]) == 7
    assert len(r["extra_channels"]) == 24


def test_captura_mais_nova_invalida_e_ignorada():
    """Publicacao parcial na captura nova: serve a anterior, nao falha global."""
    nova = _candidata(captured_at=CAP + timedelta(days=1), canais=[],
                      snapshot_id="ff" + "0" * 30)
    antiga = _candidata()
    db = FakeSession(candidatas=[nova, antiga])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["status"] == "available"
    assert r["meta"]["captured_at"] == CAP.isoformat(), "escolheu a captura valida"
    assert r["meta"]["snapshot_id"] == SNAP


def test_captura_mais_nova_sem_run_conclusivo_e_ignorada():
    """Run `running` (finished_at nulo) nao casa: a captura nova e' pulada."""
    nova = _candidata(captured_at=CAP + timedelta(days=2),
                      importado=IMPORTADO + timedelta(days=2),
                      snapshot_id="ee" + "0" * 30)
    antiga = _candidata()
    db = FakeSession(
        candidatas=[nova, antiga],
        runs=[_run(sync_run_id=300, status="running",
                   started=IMPORTADO + timedelta(days=2) - timedelta(seconds=1),
                   finished=None),
              _run()],
    )
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["status"] == "available"
    assert r["meta"]["sync_run_id"] == 285
    assert r["meta"]["captured_at"] == CAP.isoformat()


def test_run_failed_nunca_e_servido():
    db = FakeSession(candidatas=[_candidata()],
                     runs=[_run(sync_run_id=301, status="failed")])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["status"] == "unavailable"
    assert r["meta"]["unavailable_reason"] == "audit_run_not_conclusive"
    assert r["meta"]["sync_run_id"] is None


def test_run_indeterminado_nunca_e_servido():
    """`running` com nota de indeterminacao: mesmo tratamento, nao e' servido."""
    db = FakeSession(candidatas=[_candidata()],
                     runs=[_run(sync_run_id=302, status="running", finished=None)])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["status"] == "unavailable"
    assert r["meta"]["unavailable_reason"] == "audit_run_not_conclusive"


def test_dois_runs_casando_e_ambiguidade_fail_closed():
    db = FakeSession(candidatas=[_candidata()],
                     runs=[_run(sync_run_id=285), _run(sync_run_id=286)])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["status"] == "unavailable"
    assert r["meta"]["unavailable_reason"] == "audit_run_not_conclusive"


# ---------------------------------------------------------------------------
# 2. Metas e canais da MESMA captura
# ---------------------------------------------------------------------------

def test_metas_e_canais_lidos_com_a_mesma_captura():
    db = FakeSession(candidatas=[_candidata()])
    svc.get_avoe_snapshot(db)
    consultas = [(s, p) for s, p in zip(db.sqls, db.params)
                 if "captured_at = :captured_at" in s]
    assert len(consultas) == 2, "uma consulta de metas e uma de canais"
    for _sql, p in consultas:
        assert p["captured_at"] == CAP
        assert p["source"] == "avoe_hub"


def test_capturas_divergentes_entre_tabelas_sao_recusadas():
    db = FakeSession(candidatas=[_candidata(canais=[])])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["status"] == "unavailable"
    assert r["meta"]["unavailable_reason"] == "targets_and_channels_capture_mismatch"
    assert r["targets"] == [] and r["extra_channels"] == []


def test_snapshot_id_divergente_entre_tabelas_e_recusado():
    db = FakeSession(candidatas=[_candidata(channel_snapshot_id="zz" + "0" * 30)])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["unavailable_reason"] == "capture_mixes_multiple_imports"


def test_mistura_de_imports_na_mesma_captura_e_recusada():
    for campo in ("target_snapshots", "channel_snapshots",
                  "target_imports", "channel_imports"):
        db = FakeSession(candidatas=[_candidata(**{campo: 2})])
        r = svc.get_avoe_snapshot(db)
        assert r["meta"]["unavailable_reason"] == "capture_mixes_multiple_imports", campo


def test_duplicidade_de_grao_e_fail_closed():
    db = FakeSession(candidatas=[_candidata(target_grao=6)])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["unavailable_reason"] == "duplicate_grain_in_capture"
    db = FakeSession(candidatas=[_candidata(channel_grao=23)])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["unavailable_reason"] == "duplicate_grain_in_capture"


def test_moeda_nao_unica_na_captura_e_fail_closed():
    db = FakeSession(candidatas=[_candidata(moedas=2)])
    assert svc.get_avoe_snapshot(db)["meta"]["unavailable_reason"] == "serving_inconsistent"
    db = FakeSession(candidatas=[_candidata(status_moeda=2)])
    assert svc.get_avoe_snapshot(db)["meta"]["unavailable_reason"] == "serving_inconsistent"


def test_contagem_agregada_divergente_das_linhas_levanta():
    db = FakeSession(candidatas=[_candidata()], metas=_sete_metas()[:5])
    try:
        svc.get_avoe_snapshot(db)
    except svc.AvoeSnapshotError as exc:
        assert "contagem de metas divergiu" in str(exc)
    else:
        raise AssertionError("deveria levantar AvoeSnapshotError")


# ---------------------------------------------------------------------------
# 3. Ausencia nao e' zero
# ---------------------------------------------------------------------------

def test_ausencia_de_valor_chega_null_e_zero_chega_zero():
    canais = [_canal("Apice", "magalu", None),
              _canal("Barbours", "shein", "0.00"),
              _canal("Kokeshi", "kwai", "1234.56")]
    db = FakeSession(candidatas=[_candidata(canais=canais)], canais=canais)
    r = svc.get_avoe_snapshot(db)
    valores = {c["brand"]: c["reported_amount"] for c in r["extra_channels"]}
    assert valores["Apice"] is None, "ausencia jamais vira zero"
    assert valores["Barbours"] == 0.0, "zero informado continua zero"
    assert valores["Kokeshi"] == 1234.56
    # E os dois estados sao distinguiveis no JSON.
    bruto = json.dumps(r["extra_channels"])
    assert '"reported_amount": null' in bruto
    assert '"reported_amount": 0.0' in bruto


def test_brand_key_ausente_nao_e_inventado():
    metas = [_meta("Marca Sem Chave", "1000.00")]
    metas[0]["brand_key"] = None
    db = FakeSession(candidatas=[_candidata(metas=metas)], metas=metas)
    r = svc.get_avoe_snapshot(db)
    assert r["targets"][0]["brand_key"] is None


def test_source_recorded_at_nulo_continua_nulo():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    nulos = [c for c in r["extra_channels"] if c["source_recorded_at"] is None]
    assert nulos, "a fixture tem canal sem source_recorded_at"


# ---------------------------------------------------------------------------
# 4. Snapshot indisponivel
# ---------------------------------------------------------------------------

def test_sem_captura_publicada_devolve_estado_vazio():
    db = FakeSession(candidatas=[])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["status"] == "unavailable"
    assert r["meta"]["unavailable_reason"] == "no_snapshot_published"
    assert r["meta"]["captured_at"] is None
    assert r["meta"]["targets_count"] == 0 and r["meta"]["channel_rows_count"] == 0
    assert r["targets"] == [] and r["extra_channels"] == []
    assert r["meta"]["target_ref_months"] == [] and r["meta"]["channel_ref_months"] == []
    assert r["meta"]["currency"] is None
    assert r["meta"]["captured_age_days"] is None
    assert r["limitations"]["manual_snapshot"] is True
    # Nenhuma consulta de linha foi disparada.
    assert not any("captured_at = :captured_at" in s for s in db.sqls)


def test_indisponivel_ainda_avisa_fonte_externa():
    r = svc.get_avoe_snapshot(FakeSession(candidatas=[]))
    assert any("Fonte externa e manual" in a for a in r["meta"]["warnings"])
    assert any("Sem automacao" in a for a in r["meta"]["warnings"])


def test_sessao_ausente_levanta_em_vez_de_fingir_vazio():
    try:
        svc.get_avoe_snapshot(None)
    except svc.AvoeSnapshotError:
        pass
    else:
        raise AssertionError("db None deveria levantar AvoeSnapshotError")


# ---------------------------------------------------------------------------
# 5. Consultas: quantidade determinística, zero N+1
# ---------------------------------------------------------------------------

def test_quatro_consultas_no_caminho_disponivel():
    db = FakeSession(candidatas=[_candidata()])
    svc.get_avoe_snapshot(db)
    assert len(db.sqls) == 4, db.sqls


def test_duas_consultas_no_caminho_indisponivel_sem_captura():
    db = FakeSession(candidatas=[])
    svc.get_avoe_snapshot(db)
    assert len(db.sqls) == 1, "so' a de candidatas"


def test_numero_de_consultas_nao_cresce_com_o_volume():
    poucos = FakeSession(candidatas=[_candidata(metas=_sete_metas()[:1],
                                                canais=_vinte_quatro_canais()[:1])],
                         metas=_sete_metas()[:1], canais=_vinte_quatro_canais()[:1])
    svc.get_avoe_snapshot(poucos)
    muitos = FakeSession(candidatas=[_candidata()])
    svc.get_avoe_snapshot(muitos)
    assert len(poucos.sqls) == len(muitos.sqls) == 4


def test_consultas_sao_parametrizadas():
    db = FakeSession(candidatas=[_candidata()])
    svc.get_avoe_snapshot(db)
    for sql, params in zip(db.sqls, db.params):
        assert ":" in sql, f"consulta sem placeholder: {sql[:60]}"
        assert params, "consulta sem parametros"
    # E nenhum valor foi interpolado no texto do SQL.
    assert not any("avoe_hub'" in s for s in db.sqls)
    assert not any("2026-09-01" in s for s in db.sqls)


def test_teto_de_capturas_avaliadas_e_declarado():
    db = FakeSession(candidatas=[_candidata()])
    svc.get_avoe_snapshot(db)
    assert db.params[0]["max_capturas"] == svc.MAX_CAPTURAS_AVALIADAS
    assert svc.MAX_CAPTURAS_AVALIADAS <= 50
    assert db.params[1]["max_runs"] == svc.MAX_RUNS_AUDITORIA


# ---------------------------------------------------------------------------
# 6. Somente leitura e nenhuma fonte proibida
# ---------------------------------------------------------------------------

def _codigo_executavel(caminho: Path) -> str:
    """O arquivo sem comentarios e sem literais de texto.

    Sem isto, a varredura acerta a PROSA do docstring, que cita justamente os
    termos proibidos para dizer que nao os usa. O SQL do modulo vive em
    constantes de texto, entao as consultas sao varridas a parte, por nome.
    """
    import io
    import tokenize

    descartar = {tokenize.COMMENT, tokenize.STRING}
    for nome in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        if hasattr(tokenize, nome):
            descartar.add(getattr(tokenize, nome))
    fonte = caminho.read_text(encoding="utf-8")
    saida = []
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type in descartar:
            continue
        saida.append(tok.string)
    return " ".join(saida)


def _sql_do_servico() -> str:
    """Todo o SQL que o servico emite, concatenado."""
    return " ".join([svc.SQL_CANDIDATAS, svc.SQL_RUNS_AUDITORIA,
                     svc.SQL_METAS, svc.SQL_CANAIS]).lower()


def test_servico_nao_tem_escrita():
    alvos = _codigo_executavel(SERVICE_PATH).lower() + " " + _sql_do_servico()
    for proibido in ("insert into", "update ", "delete from", "truncate",
                     "create table", "alter table", "drop table"):
        assert proibido not in alvos, proibido


def test_servico_nao_le_datamart():
    alvos = _codigo_executavel(SERVICE_PATH).lower() + " " + _sql_do_servico()
    for proibido in ("gold.", "raw.", "silver."):
        assert proibido not in alvos, proibido


def test_servico_le_apenas_as_duas_tabelas_e_a_auditoria():
    codigo = SERVICE_PATH.read_text(encoding="utf-8")
    assert "marts.proxy_avoe_brand_monthly_target_snapshot" in codigo
    assert "marts.proxy_avoe_extra_channel_monthly_snapshot" in codigo
    assert "audit.source_sync_run" in codigo
    # Nenhuma tabela canonica da Torre.
    for proibida in ("fact_marketplace_daily_performance", "dim_loja",
                     "dim_marketplace", "fact_shopee", "vw_dre"):
        assert proibida not in codigo, proibida


# ---------------------------------------------------------------------------
# 7. Zero PII e zero identificador operacional
# ---------------------------------------------------------------------------

def _chaves_e_valores_de_dado(r: dict) -> str:
    """Chaves do envelope + valores das linhas, SEM os textos de aviso.

    `warnings` e `notes` sao prosa destinada ao operador e citam termos como
    "documentou" de proposito; varre-los como se fossem dado transformaria a
    explicacao em falso positivo. O que interessa aqui e' o DADO.
    """
    partes = [json.dumps(sorted(r["meta"])), json.dumps(r["targets"]),
              json.dumps(r["extra_channels"]),
              json.dumps(sorted(r["limitations"]))]
    for chave, valor in r["meta"].items():
        if chave != "warnings":
            partes.append(f"{chave}={valor}")
    return " ".join(partes).lower()


def test_resposta_nao_expoe_pii_nem_identificador_operacional():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    bruto = _chaves_e_valores_de_dado(r)
    for proibido in ("cpf", "cnpj", "email", "telefone", "phone", "endereco",
                     "address", "documento", "customer", "buyer", "cliente",
                     "import_run_id", "source_file", "source_file_hash",
                     "postgres://", "senha", "password"):
        assert proibido not in bruto, proibido
    # E os avisos, embora sejam prosa, tambem nao carregam credencial.
    avisos = " ".join(r["meta"]["warnings"] + r["limitations"]["notes"]).lower()
    for proibido in ("postgres://", "senha", "password", "cpf", "cnpj", "@"):
        assert proibido not in avisos, proibido


def test_campos_da_resposta_sao_exatamente_os_do_contrato():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    assert set(r) == {"meta", "targets", "extra_channels", "limitations"}
    assert set(r["targets"][0]) == {
        "brand", "brand_key", "ref_month", "target_amount", "currency_code",
        "currency_status", "currency_warning", "source_recorded_at"}
    assert set(r["extra_channels"][0]) == {
        "brand", "brand_key", "channel", "channel_source_label", "ref_month",
        "reported_amount", "is_proxy", "definition_status", "definition_warning",
        "currency_code", "currency_status", "days_covered",
        "first_business_date", "last_business_date", "coverage_status",
        "source_recorded_at"}


def test_nenhum_campo_de_realizado_atingimento_ou_margem():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    campos = set(r["targets"][0]) | set(r["extra_channels"][0]) | set(r["meta"])
    for proibido in ("realized_amount", "realizado", "attainment", "atingimento",
                     "margin", "margem", "gmv", "variacao", "achievement",
                     "pct_meta", "delta"):
        assert not any(proibido in c for c in campos), proibido
    assert r["limitations"]["provides_realized_amount"] is False
    assert r["limitations"]["provides_attainment_or_margin"] is False
    assert r["limitations"]["replaces_canonical_torre_kpi"] is False


# ---------------------------------------------------------------------------
# 8. Serializacao de datas e decimais
# ---------------------------------------------------------------------------

def test_datas_serializadas_em_iso():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    assert r["meta"]["captured_at"] == "2026-09-01T15:32:34.320000+00:00"
    assert r["targets"][0]["ref_month"] == "2026-08-01"
    for c in r["extra_channels"]:
        assert len(c["ref_month"]) == 10 and c["ref_month"][4] == "-"
        assert len(c["first_business_date"]) == 10
        assert len(c["last_business_date"]) == 10
    assert r["meta"]["target_ref_months"] == ["2026-08-01"]
    assert r["meta"]["channel_ref_months"] == ["2026-06-01", "2026-07-01", "2026-08-01"]


def test_decimais_preservados_ao_centavo():
    metas = [_meta("Kokeshi", "9557070.49"), _meta("Apice", "45000000.00")]
    db = FakeSession(candidatas=[_candidata(metas=metas)], metas=metas)
    r = svc.get_avoe_snapshot(db)
    valores = {t["brand"]: t["target_amount"] for t in r["targets"]}
    assert round(valores["Kokeshi"], 2) == 9557070.49
    assert round(valores["Apice"], 2) == 45000000.00
    # E o JSON preserva o centavo.
    assert "9557070.49" in json.dumps(r["targets"])


def test_tudo_serializavel_em_json():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    texto = json.dumps(r)  # levanta se sobrar Decimal, date ou datetime
    assert len(texto) > 500


def test_idade_da_captura_em_dias():
    db = FakeSession(candidatas=[_candidata()])
    r = svc.get_avoe_snapshot(db)
    assert isinstance(r["meta"]["captured_age_days"], int)
    assert r["meta"]["captured_age_days"] >= 0


# ---------------------------------------------------------------------------
# 9. Avisos e limitacoes
# ---------------------------------------------------------------------------

def test_avisos_obrigatorios_no_caminho_disponivel():
    db = FakeSession(candidatas=[_candidata()])
    avisos = " ".join(svc.get_avoe_snapshot(db)["meta"]["warnings"])
    assert "Fonte externa e manual" in avisos
    assert "Moeda ASSUMIDA" in avisos
    assert "PROXY com definicao nao confirmada" in avisos
    assert "nao traz realizado" in avisos
    assert "Sem automacao" in avisos
    assert "janela de tempo da auditoria" in avisos


def test_aviso_de_moeda_desaparece_se_a_origem_confirmar():
    db = FakeSession(candidatas=[_candidata(currency_status="confirmed")])
    avisos = " ".join(svc.get_avoe_snapshot(db)["meta"]["warnings"])
    assert "Moeda ASSUMIDA" not in avisos


def test_limitacoes_declaram_ausencia_de_automacao():
    r = svc.get_avoe_snapshot(FakeSession(candidatas=[_candidata()]))
    lim = r["limitations"]
    assert lim["manual_snapshot"] is True
    assert lim["automated_refresh"] is False
    assert lim["channel_amount_definition_confirmed"] is False
    assert lim["currency_confirmed"] is False
    assert any("Sem automacao" in n for n in lim["notes"])
    assert any("janela de tempo" in n or "coluna de ligacao" in n for n in lim["notes"])


# ---------------------------------------------------------------------------
# 10. Camada HTTP real
# ---------------------------------------------------------------------------

def _client(db):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _limpa_overrides():
    from app.main import app

    app.dependency_overrides.clear()


def test_http_200_com_envelope_completo():
    cli = _client(FakeSession(candidatas=[_candidata()]))
    try:
        r = cli.get(ROTA)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["meta"]["status"] == "available"
        assert corpo["meta"]["is_official_torre_source"] is False
        assert corpo["meta"]["sync_run_id"] == 285
        assert len(corpo["targets"]) == 7
        assert len(corpo["extra_channels"]) == 24
        assert corpo["limitations"]["automated_refresh"] is False
    finally:
        _limpa_overrides()


def test_http_200_quando_indisponivel():
    cli = _client(FakeSession(candidatas=[]))
    try:
        r = cli.get(ROTA)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["meta"]["status"] == "unavailable"
        assert corpo["meta"]["unavailable_reason"] == "no_snapshot_published"
        assert corpo["targets"] == [] and corpo["extra_channels"] == []
    finally:
        _limpa_overrides()


def test_http_500_sanitizado_em_erro_de_sql():
    """Erro de driver com DSN e SQL no texto: a resposta nao ecoa nada disso."""
    cli = _client(FakeSession(candidatas=[_candidata()], erro_em="with capturas as"))
    try:
        r = cli.get(ROTA)
        assert r.status_code == 500, r.text
        texto = r.text.lower()
        for proibido in ("postgres://", "senha", "host-interno", "5432",
                         "select brand", "proxy_avoe_brand_monthly_target_snapshot"):
            assert proibido not in texto, proibido
        assert "inconsistencia na camada de serving" in r.json()["detail"].lower()
    finally:
        _limpa_overrides()


def test_http_nao_aceita_metodo_de_escrita():
    cli = _client(FakeSession(candidatas=[_candidata()]))
    try:
        for metodo in ("post", "put", "patch", "delete"):
            r = getattr(cli, metodo)(ROTA)
            assert r.status_code == 405, f"{metodo}: {r.status_code}"
    finally:
        _limpa_overrides()


def test_openapi_declara_a_rota_e_o_schema():
    cli = _client(FakeSession(candidatas=[_candidata()]))
    try:
        spec = cli.get("/openapi.json").json()
        assert ROTA in spec["paths"], sorted(spec["paths"])[:5]
        rota = spec["paths"][ROTA]
        assert set(rota) == {"get"}, "somente GET"
        ref = rota["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "AvoeSnapshotResponse" in json.dumps(ref)
        comp = spec["components"]["schemas"]
        assert "AvoeSnapshotResponse" in comp
        assert "AvoeSnapshotMeta" in comp
        assert "AvoeTargetRow" in comp
        assert "AvoeExtraChannelRow" in comp
        assert "AvoeSnapshotLimitations" in comp
        # Sem parametro: o contrato e' "a ultima captura valida".
        assert not rota["get"].get("parameters")
    finally:
        _limpa_overrides()


def test_rotas_existentes_seguem_registradas():
    cli = _client(FakeSession(candidatas=[_candidata()]))
    try:
        spec = cli.get("/openapi.json").json()
        for rota in ("/api/v1/performance/overview",
                     "/api/v1/performance/canais",
                     "/api/v1/performance/monitoramento-preco",
                     "/api/v1/performance/brands",
                     "/api/v1/performance/produtos/shopee",
                     "/api/v1/performance/executive-summary"):
            assert rota in spec["paths"], rota
    finally:
        _limpa_overrides()


def test_nenhuma_metrica_avoe_entrou_em_outro_contrato():
    """Os schemas das rotas existentes nao ganharam campo algum da Avoe."""
    cli = _client(FakeSession(candidatas=[_candidata()]))
    try:
        spec = cli.get("/openapi.json").json()
        avoe = {"AvoeSnapshotResponse", "AvoeSnapshotMeta", "AvoeTargetRow",
                "AvoeExtraChannelRow", "AvoeSnapshotLimitations"}
        for nome, esquema in spec["components"]["schemas"].items():
            if nome in avoe:
                continue
            bruto = json.dumps(esquema).lower()
            for proibido in ("avoe", "proxy_avoe", "reported_amount",
                             "target_amount"):
                assert proibido not in bruto, f"{nome} menciona {proibido}"
    finally:
        _limpa_overrides()


def test_router_registra_a_rota_como_get_unico():
    codigo = ROUTER_PATH.read_text(encoding="utf-8")
    assert codigo.count('@router.get("/avoe-snapshot"') == 1
    for verbo in ("@router.post(\"/avoe-snapshot\"", "@router.put(\"/avoe-snapshot\"",
                  "@router.delete(\"/avoe-snapshot\""):
        assert verbo not in codigo
    # O handler nao ecoa a excecao.
    assert "ERRO_AVOE_SERVING_INCONSISTENTE" in codigo
    assert "str(exc)" not in codigo.split("avoe-snapshot")[1]
