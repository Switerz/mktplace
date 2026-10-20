"""Shadow de TikTok Ads (inerte): transform, travas, publicacao e migration 030.

Sem banco. A 030 e o publicador sao executados contra PostgreSQL real
descartavel pelo script de validacao do gate.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines import publish_tiktok_ads_daily_shadow as pub
from pipelines.transforms import tiktok_ads_shadow as T

MIG = Path(__file__).resolve().parents[2] / "apps" / "api" / "alembic" / "versions" / "030_create_fact_tiktok_ads_daily_shadow.py"
D1, D2 = date(2026, 10, 14), date(2026, 10, 15)


def l(brand, adv, camp, dia, escopo, spend="10.00", val="50.00"):
    return {"brand": brand, "advertiser_id": adv, "campaign_id": camp, "stat_date": dia, "escopo": escopo,
            "spend": Decimal(spend), "impressions": 100, "clicks": 5, "onsite_shopping": 2,
            "total_onsite_shopping_value": Decimal(val), "moeda": None, "fuso": None}


def cob(brand, adv, dia, n=1):
    return {"brand": brand, "advertiser_id": adv, "stat_date": dia, "linhas": n}


def base():
    linhas = [l("apice", "A1", "C1", D1, "shop", "10.00"), l("apice", "A1", "C2", D1, "nao_shop", "4.00"),
              l("apice", "A1", "C1", D2, "shop", "0.00", "0.00"),
              l("kokeshi", "K1", "X", D1, "shop", "99.00")]              # sem capability
    cobertura = [cob("apice", "A1", D1, 2), cob("apice", "A1", D2, 1), cob("barbours", "B1", D1, 0),
                 cob("kokeshi", "K1", D1)]
    return linhas, cobertura


def por(saida):
    return {(s["stat_date"], s["brand"], s["escopo"]): s for s in saida}


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------
def test_escopos_separados_shop_nao_mistura_site():
    s = por(T.montar_shadow(*base())[0])
    assert s[(D1, "apice", "shop")]["spend"] == Decimal("10.00")
    assert s[(D1, "apice", "nao_shop")]["spend"] == Decimal("4.00")
    assert s[(D1, "apice", "desconhecido")]["estado"] == T.SEM_LINHA


def test_marca_sem_capability_nunca_aparece():
    saida, _ = T.montar_shadow(*base())
    assert {x["brand"] for x in saida} == {"apice", "barbours"}


def test_consultado_sem_linha_tem_metricas_nulas_nao_zero():
    s = por(T.montar_shadow(*base())[0])
    b = s[(D1, "barbours", "shop")]
    assert b["estado"] == T.SEM_LINHA and b["spend"] is None and b["impressions"] is None


def test_spend_zero_medido_continua_zero():
    s = por(T.montar_shadow(*base())[0])
    assert s[(D2, "apice", "shop")]["estado"] == T.MEDIDO
    assert s[(D2, "apice", "shop")]["spend"] == Decimal("0.00")


def test_dia_nao_consultado_nao_sai():
    s = por(T.montar_shadow(*base())[0])
    assert (D2, "barbours", "shop") not in s        # barbours so' foi consultada em D1


def test_dia_com_anunciante_faltando_nao_sai():
    linhas, cobertura = base()
    cobertura += [cob("apice", "A2", D2, 1)]        # A2 existe na janela mas nao em D1
    saida, r = T.montar_shadow(linhas, cobertura)
    assert (D1, "apice", "shop") not in por(saida)
    assert r["dias_marca_incompletos"] == 1


def test_moeda_so_quando_todas_concordam():
    linhas, cobertura = base()
    linhas = [dict(x, moeda="BRL") for x in linhas]
    linhas[1]["moeda"] = None
    s = por(T.montar_shadow(linhas, cobertura)[0])
    assert s[(D1, "apice", "shop")]["moeda"] == "BRL"
    assert s[(D1, "apice", "nao_shop")]["moeda"] is None


@pytest.mark.parametrize("valor,spend,esperado", [
    (Decimal("50"), Decimal("10"), Decimal("5.00")), (Decimal("50"), Decimal("0"), None),
    (None, Decimal("10"), None), (Decimal("50"), None, None)])
def test_roas_so_com_denominador_valido(valor, spend, esperado):
    assert T.roas(valor, spend) == esperado


# ---------------------------------------------------------------------------
# Travas e publicacao
# ---------------------------------------------------------------------------
class Res:
    def __init__(self, escalar=None, mapa=None):
        self.escalar, self.mapa = escalar, mapa

    def scalar(self):
        return self.escalar

    def mappings(self):
        return self

    def one(self):
        return self.mapa


class Conn:
    def __init__(self, lock=True, destino=None):
        self.lock, self.destino, self.sqls = lock, destino, []
        self.connection = self

    def execute(self, sql, params=None):
        s = str(sql)
        self.sqls.append((s, params))
        if "pg_try_advisory_xact_lock" in s:
            return Res(escalar=self.lock)
        if "COUNT(*)" in s:
            return Res(mapa=self.destino)
        return Res()

    @contextmanager
    def cursor(self):
        yield self


class Engine:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def begin(self):
        yield self.conn


@pytest.fixture
def amb(monkeypatch):
    estado = {"audit": [], "ins": []}
    linhas, cobertura = base()
    monkeypatch.setattr(pub, "_ler", lambda i, f: {"linhas": linhas, "cobertura": cobertura})
    monkeypatch.setattr(pub, "tabela_destino_existe", lambda: True)
    monkeypatch.setattr(pub, "audit_start", lambda n: estado["audit"].append(("start", n)) or 9)
    monkeypatch.setattr(pub, "audit_finish", lambda sid, st, **kw: estado["audit"].append((st,)))
    monkeypatch.setattr(pub, "execute_values", lambda cur, sql, vals, page_size: estado["ins"].append(len(vals)))
    monkeypatch.setenv(pub.FLAG_AMBIENTE, "true")
    return estado


def _conn_certo():
    saida, _ = T.montar_shadow(*base())
    return Conn(destino={"n": len(saida), "spend": sum((s["spend"] for s in saida if s["spend"] is not None), Decimal("0"))})


def test_dry_run_nao_abre_neon(amb, monkeypatch):
    monkeypatch.setattr(pub, "local_engine", lambda: pytest.fail("abriu o Neon"))
    r = pub.run(apply=False, hoje=date(2026, 10, 20))
    assert r["modo"] == "dry_run" and r["applied"] is False and r["fonte_gmv_max"] == "sem_fonte"
    assert amb["audit"] == []


def test_travas(amb, monkeypatch):
    monkeypatch.delenv(pub.FLAG_AMBIENTE)
    with pytest.raises(pub.PublicacaoRecusada, match="desligada"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    monkeypatch.setenv(pub.FLAG_AMBIENTE, "true")
    monkeypatch.setattr(pub, "tabela_destino_existe", lambda: False)
    with pytest.raises(pub.PublicacaoRecusada, match="030"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    monkeypatch.setattr(pub, "_ler", lambda i, f: None)
    with pytest.raises(pub.PublicacaoRecusada, match="fonte_inexistente"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"] == []


def test_publica_com_lock_primeiro_e_reconciliacao(amb, monkeypatch):
    conn = _conn_certo()
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    r = pub.run(apply=True, hoje=date(2026, 10, 20))
    assert r["applied"] is True and r["marcas_publicadas"] == ["apice", "barbours"]
    assert "pg_try_advisory_xact_lock" in conn.sqls[0][0]
    assert amb["audit"][-1] == ("success",)


def test_reconciliacao_divergente_aborta(amb, monkeypatch):
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(Conn(destino={"n": 0, "spend": 0})))
    with pytest.raises(pub.ReconciliacaoFalhou):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"][-1] == ("failed",)


def test_lock_ocupado(amb, monkeypatch):
    conn = Conn(lock=False)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.ConcurrentRunError):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert not any(s.startswith("DELETE") for s, _ in conn.sqls) and amb["ins"] == []


def test_auditoria_marca_tiktok_como_1():
    assert pub.AUDIT_MARKETPLACE_ID == 1


# ---------------------------------------------------------------------------
# Migration 030 (estatico)
# ---------------------------------------------------------------------------
def test_migration_030():
    sys.modules.setdefault("alembic", types.SimpleNamespace(op=None))
    spec = importlib.util.spec_from_file_location("m030", MIG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert (m.revision, m.down_revision) == ("030", "027")
    sql = " ".join(m.UPGRADE_SQL)
    assert "ck_ftads_estado_metricas" in sql and "fonte = 'integrado_leilao'" in sql
    assert "brand IN ('apice', 'barbours')" in sql and sql.count("<> 'NaN'") == 2
    for c in pub.COLS:
        assert c in sql, c
