"""Shadow de TikTok Ads (inerte): transform, travas, publicacao e migration 031.

A maior parte roda sem banco. O bloco final (`test_pg_*`) roda contra
PostgreSQL REAL DESCARTAVEL, e so' quando `TIKTOK_ADS_SHADOW_PG_URL` aponta
para localhost com a 031 ja' aplicada (`alembic upgrade head`). Ele cria e
recria `raw.tiktok_ads_campaign_daily` / `raw.tiktok_ads_coverage` nesse banco
e apaga as linhas das duas tabelas shadow -- nunca aponte para banco real.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pytest

from pipelines import publish_tiktok_ads_daily_shadow as pub
from pipelines.transforms import tiktok_ads_shadow as T

MIG = Path(__file__).resolve().parents[2] / "apps" / "api" / "alembic" / "versions" / "031_create_fact_tiktok_ads_daily_shadow.py"
D1, D2 = date(2026, 10, 14), date(2026, 10, 15)


def l(brand, adv, camp, dia, escopo, spend="10.00", val="50.00", moeda=None, fuso=None):
    return {"brand": brand, "advertiser_id": adv, "campaign_id": camp, "stat_date": dia, "escopo": escopo,
            "spend": Decimal(spend), "impressions": 100, "clicks": 5, "onsite_shopping": 2,
            "total_onsite_shopping_value": Decimal(val), "moeda": moeda, "fuso": fuso}


def cob(brand, adv, dia, n=1):
    return {"brand": brand, "advertiser_id": adv, "stat_date": dia, "linhas": n}


def base():
    linhas = [l("apice", "A1", "C1", D1, "shop", "10.00"), l("apice", "A1", "C2", D1, "nao_shop", "4.00"),
              l("apice", "A1", "C3", D1, "desconhecido", "1.50"),
              l("apice", "A1", "C1", D2, "shop", "0.00", "0.00"),
              l("apice", "A2", "C9", D1, "shop", "7.25", moeda="BRL", fuso="America/Sao_Paulo"),
              l("barbours", "B1", "CB", D2, "shop", "3.00"),            # D2 nao consultado
              l("kokeshi", "K1", "X", D1, "shop", "99.00")]              # sem capability
    cobertura = [cob("apice", "A1", D1, 3), cob("apice", "A1", D2, 1), cob("apice", "A2", D1, 1),
                 cob("barbours", "B1", D1, 0), cob("kokeshi", "K1", D1)]
    return linhas, cobertura


def fato_por(fato):
    return {(f["advertiser_id"], f["campaign_id"], f["stat_date"]): f for f in fato}


def cob_por(cobertura):
    return {(c["advertiser_id"], c["stat_date"]): c for c in cobertura}


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------
def test_grao_campanha_dia_e_escopos_separados():
    fato, _, r = T.montar_shadow(*base())
    f = fato_por(fato)
    assert f[("A1", "C1", D1)]["escopo"] == "shop" and f[("A1", "C1", D1)]["spend"] == Decimal("10.00")
    assert f[("A1", "C2", D1)]["escopo"] == "nao_shop"
    assert f[("A1", "C3", D1)]["escopo"] == "desconhecido"
    assert r["spend_medido_por_escopo"] == {"shop": Decimal("17.25"), "nao_shop": Decimal("4.00"),
                                            "desconhecido": Decimal("1.50")}


def test_marca_sem_capability_nunca_aparece():
    fato, cobertura, _ = T.montar_shadow(*base())
    assert {x["brand"] for x in fato} == {"apice"}
    assert {x["brand"] for x in cobertura} == {"apice", "barbours"}


def test_consultado_sem_linha_e_so_cobertura_nunca_spend_zero():
    fato, cobertura, r = T.montar_shadow(*base())
    assert cob_por(cobertura)[("B1", D1)]["linhas"] == 0
    assert not [f for f in fato if f["advertiser_id"] == "B1"]
    assert r["pares_consultados_sem_linha"] == 1


def test_spend_zero_medido_continua_zero():
    f = fato_por(T.montar_shadow(*base())[0])
    assert f[("A1", "C1", D2)]["spend"] == Decimal("0.00")


def test_dia_nao_consultado_nao_gera_linha_nem_cobertura():
    fato, cobertura, r = T.montar_shadow(*base())
    assert ("B1", "CB", D2) not in fato_por(fato) and ("B1", D2) not in cob_por(cobertura)
    assert r["linhas_sem_cobertura"] == 1


def test_cobertura_conta_as_linhas_publicadas():
    _, cobertura, _ = T.montar_shadow(*base())
    c = cob_por(cobertura)
    assert (c[("A1", D1)]["linhas"], c[("A1", D2)]["linhas"], c[("A2", D1)]["linhas"]) == (3, 1, 1)


def test_moeda_nula_vira_marcador_e_nunca_brl_assumido():
    f = fato_por(T.montar_shadow(*base())[0])
    assert f[("A1", "C1", D1)]["moeda"] == T.MOEDA_DESCONHECIDA and f[("A1", "C1", D1)]["fuso"] is None
    assert f[("A2", "C9", D1)]["moeda"] == "BRL" and f[("A2", "C9", D1)]["fuso"] == "America/Sao_Paulo"


def test_chave_repetida_na_fonte_recusa():
    linhas, cobertura = base()
    linhas.append(l("apice", "A1", "C1", D1, "shop", "10.00", moeda="BRL"))  # mesma chave, outra moeda
    with pytest.raises(T.FonteInconsistente, match="repetida"):
        T.montar_shadow(linhas, cobertura)


def test_marca_divergente_e_escopo_invalido_recusam():
    linhas, cobertura = base()
    with pytest.raises(T.FonteInconsistente):
        T.montar_shadow(linhas, cobertura + [cob("barbours", "A1", D1)])
    with pytest.raises(T.FonteInconsistente, match="escopo"):
        T.montar_shadow(linhas + [l("apice", "A1", "C7", D1, "gmv_max")], cobertura)


def test_completude_por_marca_so_informada():
    linhas, cobertura = base()
    _, _, r = T.montar_shadow(linhas, cobertura)
    assert r["dias_marca_incompletos"] == 1          # apice D2: A2 nao consultado


def _sem(linhas, adv, dia, quantas=None):
    """Tira `quantas` (todas, se None) linhas raw do par."""
    fora, out = 0, []
    for x in linhas:
        if x["advertiser_id"] == adv and x["stat_date"] == dia and (quantas is None or fora < quantas):
            fora += 1
            continue
        out.append(x)
    return out


def _raw_positiva_ausente():
    linhas, cobertura = base()
    return _sem(linhas, "A1", D1), cobertura                      # declara 3, le 0


def _raw_parcial():
    linhas, cobertura = base()
    return _sem(linhas, "A1", D1, 1), cobertura                   # declara 3, le 2


def _raw_zero_com_linhas():
    linhas, cobertura = base()
    return linhas + [l("barbours", "B1", "CB", D1, "shop")], cobertura   # declara 0, le 1


RAW_INCOERENTE = {"positiva_ausente": (_raw_positiva_ausente, "A1 2026-10-14 declarado=3 lido=0"),
                  "parcial": (_raw_parcial, "A1 2026-10-14 declarado=3 lido=2"),
                  "zero_com_linhas": (_raw_zero_com_linhas, "B1 2026-10-14 declarado=0 lido=1")}


@pytest.mark.parametrize("caso", sorted(RAW_INCOERENTE))
def test_contagem_raw_por_par_divergente_recusa(caso):
    fabrica, trecho = RAW_INCOERENTE[caso]
    with pytest.raises(T.FonteInconsistente, match="contagem RAW por par") as e:
        T.montar_shadow(*fabrica())
    assert trecho in str(e.value)


def test_contagem_raw_por_par_ok_publica_a_declarada():
    linhas, cobertura = base()
    _, cob_out, _ = T.montar_shadow(linhas, cobertura)
    declarada = {(c["advertiser_id"], c["stat_date"]): c["linhas"] for c in cobertura if c["brand"] != "kokeshi"}
    assert {k: c["linhas"] for k, c in cob_por(cob_out).items()} == declarada


@pytest.mark.parametrize("valor", [None, -1, True, "3", Decimal("3")])
def test_contagem_declarada_invalida_recusa(valor):
    linhas, cobertura = base()
    cobertura[0]["linhas"] = valor
    with pytest.raises(T.FonteInconsistente, match="contagem valida"):
        T.montar_shadow(linhas, cobertura)


def test_cobertura_com_duas_contagens_para_o_par_recusa():
    linhas, cobertura = base()
    with pytest.raises(T.FonteInconsistente, match="duas contagens"):
        T.montar_shadow(linhas, cobertura + [cob("apice", "A1", D1, 0)])
    # declaracao repetida identica nao contradiz nada
    assert T.montar_shadow(linhas, cobertura + [cob("apice", "A1", D1, 3)])[1] == T.montar_shadow(linhas, cobertura)[1]


def test_divergencias_por_par():
    c = [cob("apice", "A1", D1, 2), cob("apice", "A2", D1, 0), cob("apice", "A3", D1, 1)]
    linhas = [l("apice", "A1", "C1", D1, "shop"), l("apice", "A2", "C1", D1, "shop"), l("apice", "A3", "C1", D1, "shop")]
    assert T.divergencias_por_par(c, linhas) == [("A1", D1, 2, 1), ("A2", D1, 0, 1)]
    assert T.divergencias_por_par(c[2:], linhas) == []


@pytest.mark.parametrize("valor,spend,esperado", [
    (Decimal("50"), Decimal("10"), Decimal("5.00")), (Decimal("50"), Decimal("0"), None),
    (None, Decimal("10"), None), (Decimal("50"), None, None)])
def test_roas_so_com_denominador_valido(valor, spend, esperado):
    assert T.roas(valor, spend) == esperado


# ---------------------------------------------------------------------------
# Travas e publicacao (fake)
# ---------------------------------------------------------------------------
class Res:
    def __init__(self, escalar=None, mapa=None, linhas=None, rowcount=0, um=None):
        self.escalar, self.mapa, self.linhas = escalar, mapa, linhas or []
        self.rowcount, self.um = rowcount, um

    def fetchone(self):
        return self.um

    def __iter__(self):
        return iter(self.linhas)

    def scalar(self):
        return self.escalar

    def mappings(self):
        return self

    def one(self):
        return self.mapa

    def all(self):
        return self.linhas


class Conn:
    def __init__(self, lock=True, rec=None, cob=None, audit_rowcount=1):
        self.lock, self.rec, self.cob, self.sqls = lock, rec or [], cob, []
        self.audit_rowcount = audit_rowcount
        self.connection = self

    def execute(self, sql, params=None):
        s = str(sql)
        self.sqls.append((s, params))
        if "pg_try_advisory_xact_lock" in s:
            return Res(escalar=self.lock)
        if "audit.source_sync_run" in s:
            return Res(rowcount=self.audit_rowcount)
        if "GROUP BY" in s:
            return Res(linhas=self.rec)
        if "COUNT(*)" in s:
            return Res(mapa=self.cob)
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
    monkeypatch.setattr(pub, "audit_finish",
                        lambda sid, st, **kw: estado["audit"].append((st, kw.get("error_message"))))
    monkeypatch.setattr(pub, "execute_values",
                        lambda cur, sql, vals, page_size: estado["ins"].append((sql.split()[2], len(vals))))
    monkeypatch.setenv(pub.FLAG_AMBIENTE, "true")
    return estado


def _conn_certo():
    fato, cobertura, _ = T.montar_shadow(*base())
    rec = {}
    for f in fato:
        k = (f["stat_date"], f["advertiser_id"], f["escopo"], f["moeda"])
        n, sp = rec.get(k, (0, Decimal("0")))
        rec[k] = (n + 1, sp + f["spend"])
    linhas = [{"stat_date": k[0], "advertiser_id": k[1], "escopo": k[2], "moeda": k[3], "n": v[0], "spend": v[1]}
              for k, v in rec.items()]
    return Conn(rec=linhas, cob={"n": len(cobertura), "linhas": len(fato)})


def test_flag_desligada_por_padrao(monkeypatch):
    monkeypatch.delenv(pub.FLAG_AMBIENTE, raising=False)
    assert pub.flag_ligada() is False


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
    with pytest.raises(pub.PublicacaoRecusada, match="031"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    monkeypatch.setattr(pub, "_ler", lambda i, f: None)
    with pytest.raises(pub.PublicacaoRecusada, match="fonte_inexistente"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"] == []


def test_fonte_inconsistente_dry_run_reporta_e_apply_recusa_auditado(amb, monkeypatch):
    linhas, cobertura = base()
    linhas.append(dict(linhas[0]))
    monkeypatch.setattr(pub, "_ler", lambda i, f: {"linhas": linhas, "cobertura": cobertura})
    monkeypatch.setattr(pub, "local_engine", lambda: pytest.fail("recusa tocou o destino"))
    assert pub.run(apply=False, hoje=date(2026, 10, 20))["estado"] == "fonte_inconsistente"
    with pytest.raises(pub.PublicacaoRecusada, match="fonte_inconsistente"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"][0] == ("start", len(linhas)) and amb["audit"][1][0] == "failed"
    assert "fonte_inconsistente" in amb["audit"][1][1] and "repetida" in amb["audit"][1][1]
    assert amb["ins"] == []


@pytest.mark.parametrize("caso", sorted(RAW_INCOERENTE))
def test_apply_com_contagem_raw_divergente_recusa_sem_tocar_destino(amb, monkeypatch, caso):
    linhas, cobertura = RAW_INCOERENTE[caso][0]()
    monkeypatch.setattr(pub, "_ler", lambda i, f: {"linhas": linhas, "cobertura": cobertura})
    monkeypatch.setattr(pub, "local_engine", lambda: pytest.fail("recusa tocou o destino"))
    monkeypatch.setattr(pub, "tabela_destino_existe", lambda: pytest.fail("recusa consultou o destino"))
    with pytest.raises(pub.PublicacaoRecusada, match="contagem RAW por par"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert [a[0] for a in amb["audit"]] == ["start", "failed"]
    assert RAW_INCOERENTE[caso][1] in amb["audit"][1][1] and amb["ins"] == []


def test_recusa_continua_recusa_se_a_auditoria_falhar(amb, monkeypatch):
    monkeypatch.setattr(pub, "_ler", lambda i, f: dict(zip(("linhas", "cobertura"), _raw_parcial())))
    monkeypatch.setattr(pub, "audit_start", lambda n: (_ for _ in ()).throw(OSError("sem rede")))
    with pytest.raises(pub.PublicacaoRecusada, match="contagem RAW por par"):
        pub.run(apply=True, hoje=date(2026, 10, 20))


def test_publica_com_lock_primeiro_cobertura_antes_da_fato(amb, monkeypatch):
    conn = _conn_certo()
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    r = pub.run(apply=True, hoje=date(2026, 10, 20))
    assert r["applied"] is True and r["marcas_publicadas"] == ["apice", "barbours"]
    assert "pg_try_advisory_xact_lock" in conn.sqls[0][0]
    assert [t for t, _ in amb["ins"]] == [pub.FACT_COBERTURA, pub.FACT]
    deletes = [s for s, _ in conn.sqls if s.lstrip().startswith("DELETE")]
    assert pub.FACT in deletes[0] and pub.FACT_COBERTURA in deletes[1]
    # success sai DENTRO da transacao da publicacao, como ultimo comando, e so' sobre 'running'
    ultimo, params = conn.sqls[-1]
    assert "status = 'success'" in ultimo and "status = 'running'" in ultimo and params == {"n": 5, "sid": 9}
    assert amb["audit"] == [("start", 5)] and r["auditoria"] == "success" and r["sync_run_id"] == 9


def test_success_nao_fecha_desfaz_a_publicacao(amb, monkeypatch):
    conn = _conn_certo()
    conn.audit_rowcount = 0                      # linha de auditoria nao esta' 'running'
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.AuditoriaNaoFechou):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"][-1][0] == "failed" and "AuditoriaNaoFechou" in amb["audit"][-1][1]


def test_conferencia_por_par_antes_do_delete():
    fato, cobertura, _ = T.montar_shadow(*base())
    for fato_ruim, cob_ruim in ((fato[1:], cobertura),                                   # parcial
                                ([f for f in fato if f["advertiser_id"] != "A2"], cobertura),  # ausente
                                (fato, [c for c in cobertura if c["advertiser_id"] != "A2"])):  # fora da cobertura
        conn = Conn()
        with pytest.raises(pub.ReconciliacaoFalhou, match="antes do DELETE"):
            pub.publicar_em_transacao(conn, fato_ruim, cob_ruim, "r")
        assert conn.sqls == []


def test_reconciliacao_divergente_aborta(amb, monkeypatch):
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(Conn(rec=[], cob={"n": 0, "linhas": 0})))
    with pytest.raises(pub.ReconciliacaoFalhou):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"][-1][0] == "failed" and "ReconciliacaoFalhou" in amb["audit"][-1][1]


class EngineCommitAmbiguo(Engine):
    """A transacao inteira roda e o erro chega NO COMMIT (ACK perdido): o servidor pode ter confirmado."""

    @contextmanager
    def begin(self):
        yield self.conn
        raise OSError("server closed the connection unexpectedly")


class LogGravador:
    def __init__(self):
        self.msgs = []

    def _grava(self, msg, *args):
        self.msgs.append(msg % args if args else msg)

    error = warning = info = _grava


PROMESSAS_SEM_PROVA = ("revertid", "rollback", "nada foi gravado", "nada foi confirmado", "desfeit")


def _sem_promessa(msgs):
    return [m for m in msgs if any(p in m.lower() for p in PROMESSAS_SEM_PROVA)]


@pytest.mark.parametrize("desfecho", ["linha_ja_terminal", "auditoria_inacessivel"])
def test_commit_ambiguo_loga_estado_indeterminado_sem_prometer(amb, monkeypatch, desfecho):
    erro = pub.AuditoriaNaoFechou("ja' terminal") if desfecho == "linha_ja_terminal" else OSError("sem rede")
    conn, log = _conn_certo(), LogGravador()
    monkeypatch.setattr(pub, "local_engine", lambda: EngineCommitAmbiguo(conn))
    monkeypatch.setattr(pub, "audit_finish", lambda sid, st, **kw: (_ for _ in ()).throw(erro))
    monkeypatch.setattr(pub, "uuid", types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex="run-amb")))
    monkeypatch.setattr(pub, "logger", log)
    with pytest.raises(OSError, match="closed the connection"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert "status = 'success'" in conn.sqls[-1][0]          # success foi no mesmo COMMIT ambiguo
    assert len(log.msgs) == 1
    m = log.msgs[0]
    assert m.startswith("estado INDETERMINADO: ") and "pode ter sido confirmado" in m
    assert "conferir audit.source_sync_run (sync_run_id=9)" in m and "source_run_id=run-amb" in m
    assert m.endswith("antes de republicar") and _sem_promessa(log.msgs) == []


def test_commit_ambiguo_com_failed_gravado_nao_loga_indeterminado(amb, monkeypatch):
    # `failed` so' casa linha 'running': grava-lo prova que o COMMIT da publicacao nao confirmou.
    log = LogGravador()
    monkeypatch.setattr(pub, "local_engine", lambda: EngineCommitAmbiguo(_conn_certo()))
    monkeypatch.setattr(pub, "logger", log)
    with pytest.raises(OSError):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"][-1][0] == "failed" and "OSError" in amb["audit"][-1][1] and log.msgs == []


def test_recusa_com_auditoria_inacessivel_nao_promete(amb, monkeypatch):
    log = LogGravador()
    monkeypatch.setattr(pub, "_ler", lambda i, f: dict(zip(("linhas", "cobertura"), _raw_parcial())))
    monkeypatch.setattr(pub, "audit_start", lambda n: (_ for _ in ()).throw(OSError("sem rede")))
    monkeypatch.setattr(pub, "logger", log)
    with pytest.raises(pub.PublicacaoRecusada):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert len(log.msgs) == 1 and "conferir audit.source_sync_run" in log.msgs[0]
    assert _sem_promessa(log.msgs) == []


def test_docstring_nao_promete_rollback_no_exit_1():
    assert "rollback, nada confirmado" not in pub.__doc__ and "INDETERMINADO" in pub.__doc__


class ConnLeitura:
    def __init__(self, snapshot=pub.SNAPSHOT_ESPERADO):
        self.snapshot, self.sqls = snapshot, []

    def execute(self, sql, params=None):
        self.sqls.append(sql)
        if sql is pub.SQL_CONFERE_SNAPSHOT:
            return Res(um=self.snapshot)
        if sql is pub.SQL_PRESENCA:
            return Res(um=(True, True))
        return Res()


def test_ler_fonte_abre_snapshot_antes_de_tudo():
    conn = ConnLeitura()
    assert pub.ler_fonte(conn, D1, D2) == {"linhas": [], "cobertura": []}
    assert conn.sqls[:2] == [pub.SQL_SNAPSHOT, pub.SQL_CONFERE_SNAPSHOT]
    assert conn.sqls[-2:] == [pub.SQL_LINHAS, pub.SQL_COBERTURA]
    assert str(pub.SQL_SNAPSHOT) == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


@pytest.mark.parametrize("snapshot", [("read committed", "on"), ("repeatable read", "off")])
def test_ler_fonte_sem_snapshot_recusa(snapshot):
    conn = ConnLeitura(snapshot)
    with pytest.raises(pub.LeituraSemSnapshot):
        pub.ler_fonte(conn, D1, D2)
    assert pub.SQL_LINHAS not in conn.sqls


def test_lock_ocupado(amb, monkeypatch):
    conn = Conn(lock=False)
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    with pytest.raises(pub.ConcurrentRunError):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert not any(s.lstrip().startswith("DELETE") for s, _ in conn.sqls) and amb["ins"] == []


def test_auditoria_marca_tiktok_como_1():
    assert pub.AUDIT_MARKETPLACE_ID == 1


# ---------------------------------------------------------------------------
# Migration 031 (estatico)
# ---------------------------------------------------------------------------
def _mig():
    sys.modules.setdefault("alembic", types.SimpleNamespace(op=None))
    spec = importlib.util.spec_from_file_location("m031", MIG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_migration_031():
    m = _mig()
    assert (m.revision, m.down_revision) == ("031", "030")
    sql = " ".join(m.UPGRADE_SQL)
    assert "PRIMARY KEY (advertiser_id, campaign_id, stat_date, moeda)" in sql
    assert "moeda                  TEXT          NOT NULL" in sql and "'DESCONHECIDA'" in sql
    assert "fonte = 'integrado_leilao'" in sql and "brand IN ('apice', 'barbours')" in sql
    assert sql.count("<> 'NaN'") == 2 and "fk_ftads_cobertura" in sql
    for c in pub.COLS + pub.COLS_COBERTURA:
        assert c in sql, c
    assert m.MOEDA_DESCONHECIDA == T.MOEDA_DESCONHECIDA
    # downgrade derruba a fato (dependente) antes da cobertura
    assert m.DOWNGRADE_SQL[0].endswith(m.TABELA) and m.DOWNGRADE_SQL[1].endswith(m.TABELA_COBERTURA)


# ---------------------------------------------------------------------------
# PostgreSQL real descartavel (opt-in)
# ---------------------------------------------------------------------------
PG_URL = os.environ.get("TIKTOK_ADS_SHADOW_PG_URL", "")
pg = pytest.mark.skipif(not PG_URL, reason="TIKTOK_ADS_SHADOW_PG_URL ausente (PG descartavel)")

RAW_DDL = [
    "CREATE SCHEMA IF NOT EXISTS raw",
    "DROP TABLE IF EXISTS raw.tiktok_ads_campaign_daily",
    "DROP TABLE IF EXISTS raw.tiktok_ads_coverage",
    """CREATE TABLE raw.tiktok_ads_campaign_daily (
        brand TEXT, advertiser_id TEXT, campaign_id TEXT, stat_date DATE, escopo TEXT,
        spend NUMERIC(14,2), impressions BIGINT, clicks BIGINT, onsite_shopping BIGINT,
        total_onsite_shopping_value NUMERIC(14,2), moeda TEXT, fuso TEXT)""",
    "CREATE TABLE raw.tiktok_ads_coverage (brand TEXT, advertiser_id TEXT, stat_date DATE, linhas INTEGER)",
]


@pytest.fixture
def pg_engine():
    from sqlalchemy import create_engine, text

    host = urlparse(PG_URL).hostname
    if host not in ("localhost", "127.0.0.1"):
        pytest.fail(f"TIKTOK_ADS_SHADOW_PG_URL so' aceita PG descartavel local, veio host={host!r}")
    eng = create_engine(PG_URL)
    with eng.begin() as c:
        if not c.execute(text(f"SELECT to_regclass('{pub.FACT}') IS NOT NULL "
                              f"AND to_regclass('{pub.FACT_COBERTURA}') IS NOT NULL")).scalar():
            pytest.fail("rode `alembic upgrade head` (031) no PG descartavel antes")
        for sql in RAW_DDL:
            c.execute(text(sql))
        c.execute(text(f"DELETE FROM {pub.FACT}"))
        c.execute(text(f"DELETE FROM {pub.FACT_COBERTURA}"))
        # audit.source_sync_run tem FK para dim_marketplace, que nenhuma migration semeia
        c.execute(text("INSERT INTO marts.dim_marketplace (marketplace_id, nome_marketplace, slug) "
                       "VALUES (1, 'TikTok', 'tiktok') ON CONFLICT DO NOTHING"))
    yield eng
    eng.dispose()


def _carregar_raw(eng, linhas, cobertura):
    from sqlalchemy import text

    with eng.begin() as c:
        c.execute(text("TRUNCATE raw.tiktok_ads_campaign_daily, raw.tiktok_ads_coverage"))
        for x in linhas:
            c.execute(text("""INSERT INTO raw.tiktok_ads_campaign_daily VALUES
                (:brand, :advertiser_id, :campaign_id, :stat_date, :escopo, :spend, :impressions, :clicks,
                 :onsite_shopping, :total_onsite_shopping_value, :moeda, :fuso)"""), x)
        for x in cobertura:
            c.execute(text("INSERT INTO raw.tiktok_ads_coverage VALUES (:brand, :advertiser_id, :stat_date, :linhas)"), x)


def _publicar_pg(eng, inicio=D1, fim=D2):
    from sqlalchemy import text

    with eng.begin() as c:
        fonte = pub.ler_fonte(c, inicio, fim)
    fato, cobertura, _ = T.montar_shadow(fonte["linhas"], fonte["cobertura"])
    with eng.begin() as c:
        assert c.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": pub.ADVISORY_LOCK_KEY}).scalar()
        return pub.publicar_em_transacao(c, fato, cobertura, uuid.uuid4().hex)


def _foto(eng):
    from sqlalchemy import text

    cols_f = ", ".join(c for c in pub.COLS if c != "source_run_id")
    cols_c = ", ".join(c for c in pub.COLS_COBERTURA if c != "source_run_id")
    with eng.connect() as c:
        return (c.execute(text(f"SELECT {cols_f} FROM {pub.FACT} ORDER BY 3, 4, 1")).all(),
                c.execute(text(f"SELECT {cols_c} FROM {pub.FACT_COBERTURA} ORDER BY 3, 1")).all())


@pg
def test_pg_idempotente_e_ausencia_nao_vira_zero(pg_engine):
    from sqlalchemy import text

    _carregar_raw(pg_engine, *base())
    r1 = _publicar_pg(pg_engine)
    foto1 = _foto(pg_engine)
    r2 = _publicar_pg(pg_engine)
    foto2 = _foto(pg_engine)
    assert foto1 == foto2 and len(foto1[0]) == 5 and len(foto1[1]) == 4
    assert r1["spend_medido_publicado"] == r2["spend_medido_publicado"] == Decimal("22.75")
    with pg_engine.connect() as c:
        # consultado sem linha: so' cobertura com 0, nenhuma linha (nem spend 0) na fato
        assert c.execute(text(f"SELECT linhas FROM {pub.FACT_COBERTURA} WHERE advertiser_id='B1'")).all() == [(0,)]
        assert c.execute(text(f"SELECT COUNT(*) FROM {pub.FACT} WHERE advertiser_id='B1'")).scalar() == 0
        # nao consultado (B1 em D2): nem cobertura nem fato; kokeshi nunca entra
        assert c.execute(text(f"SELECT COUNT(*) FROM {pub.FACT_COBERTURA} "
                              f"WHERE stat_date = :d AND advertiser_id='B1'"), {"d": D2}).scalar() == 0
        assert c.execute(text(f"SELECT COUNT(*) FROM {pub.FACT} WHERE brand NOT IN ('apice','barbours')")).scalar() == 0
        # zero medido continua zero
        assert c.execute(text(f"SELECT spend FROM {pub.FACT} WHERE advertiser_id='A1' AND campaign_id='C1' "
                              f"AND stat_date=:d"), {"d": D2}).scalar() == Decimal("0.00")
        # moeda/fuso nunca assumidos
        assert c.execute(text(f"SELECT COUNT(*) FROM {pub.FACT} WHERE moeda='DESCONHECIDA' AND fuso IS NULL")).scalar() == 4
        assert c.execute(text(f"SELECT moeda, fuso FROM {pub.FACT} WHERE advertiser_id='A2'")).one() == \
            ("BRL", "America/Sao_Paulo")


@pg
def test_pg_chave_com_moeda_desconhecida_nao_duplica(pg_engine):
    from sqlalchemy import exc, text

    _carregar_raw(pg_engine, *base())
    _publicar_pg(pg_engine)
    ins = text(f"""INSERT INTO {pub.FACT} (stat_date, brand, advertiser_id, campaign_id, escopo, moeda, spend,
                   impressions, clicks, onsite_shopping, onsite_shopping_value, source_run_id)
                   VALUES (:d, 'apice', :adv, 'C1', 'shop', :moeda, 1, 1, 1, 1, 1, 'x')""")
    casos = [({"d": D1, "adv": "A1", "moeda": "DESCONHECIDA"}, "pk_ftads"),       # mesma chave
             ({"d": D1, "adv": "A1", "moeda": None}, "moeda"),                    # NULL recusado
             ({"d": D1, "adv": "A1", "moeda": "brl"}, "moeda"),                   # fora do CHECK
             ({"d": D2, "adv": "B1", "moeda": "DESCONHECIDA"}, "fk_ftads_cobertura")]  # sem consulta
    for params, marca in casos:
        with pytest.raises(exc.IntegrityError, match=marca):
            with pg_engine.begin() as c:
                c.execute(ins, params)


@pg
def test_pg_reconcilia_com_a_raw_por_dia_anunciante_escopo_moeda(pg_engine):
    from sqlalchemy import text

    _carregar_raw(pg_engine, *base())
    _publicar_pg(pg_engine)
    raw = f"""
        SELECT l.stat_date, l.advertiser_id, l.escopo,
               COALESCE(NULLIF(UPPER(TRIM(l.moeda)), ''), 'DESCONHECIDA') AS moeda, SUM(l.spend) AS spend,
               COUNT(*) AS n
          FROM raw.tiktok_ads_campaign_daily l
          JOIN (SELECT DISTINCT brand, advertiser_id, stat_date FROM raw.tiktok_ads_coverage) c
            USING (brand, advertiser_id, stat_date)
         WHERE l.brand IN ('apice', 'barbours')
         GROUP BY 1, 2, 3, 4"""
    fato = f"SELECT stat_date, advertiser_id, escopo, moeda, SUM(spend), COUNT(*) FROM {pub.FACT} GROUP BY 1, 2, 3, 4"
    with pg_engine.connect() as c:
        assert c.execute(text(f"({raw}) EXCEPT ({fato})")).all() == []
        assert c.execute(text(f"({fato}) EXCEPT ({raw})")).all() == []
        assert c.execute(text(f"SELECT COUNT(*) FROM ({fato}) f")).scalar() == 5


@pg
def test_pg_republicacao_parcial_so_substitui_pares_consultados(pg_engine):
    from sqlalchemy import text

    linhas, cobertura = base()
    _carregar_raw(pg_engine, linhas, cobertura)
    _publicar_pg(pg_engine)
    # Nova leitura so' de D2, com spend revisado: D1 nao foi consultado de novo e fica intacto.
    novas = [l("apice", "A1", "C1", D2, "shop", "2.00")]
    _carregar_raw(pg_engine, novas, [cob("apice", "A1", D2, 1)])
    _publicar_pg(pg_engine, D2, D2)
    with pg_engine.connect() as c:
        assert c.execute(text(f"SELECT COUNT(*) FROM {pub.FACT} WHERE stat_date=:d"), {"d": D1}).scalar() == 4
        assert c.execute(text(f"SELECT spend FROM {pub.FACT} WHERE stat_date=:d"), {"d": D2}).all() == \
            [(Decimal("2.00"),)]
        assert c.execute(text(f"SELECT COUNT(*) FROM {pub.FACT_COBERTURA}")).scalar() == 4


# ---------------------------------------------------------------------------
# PG: contagem RAW por par, snapshot da leitura e auditoria (R14)
# ---------------------------------------------------------------------------
HOJE = date(2026, 10, 20)


def _ler_pg(eng):
    def ler(inicio, fim):
        with eng.begin() as c:
            return pub.ler_fonte(c, inicio, fim)
    return ler


@pytest.fixture
def run_pg(pg_engine, monkeypatch):
    """`pub.run(apply=True)` de verdade: leitura, destino e auditoria no PG descartavel."""
    monkeypatch.setattr(pub, "_ler", _ler_pg(pg_engine))
    monkeypatch.setattr(pub, "local_engine", lambda: pg_engine)
    monkeypatch.setattr(pub, "_audit_conn", pg_engine.raw_connection)
    monkeypatch.setenv(pub.FLAG_AMBIENTE, "true")
    return lambda: pub.run(apply=True, hoje=HOJE)


def _foto_bytes(eng):
    """Destino inteiro, TODAS as colunas (inclusive source_run_id e published_at), como texto."""
    from sqlalchemy import text

    with eng.connect() as c:
        return tuple(tuple(c.execute(text(f"SELECT COUNT(*), COALESCE(string_agg(t::text, '|' ORDER BY t::text), '') "
                                          f"FROM {tabela} t")).one())
                     for tabela in (pub.FACT, pub.FACT_COBERTURA))


def _auditoria(eng, sid=None):
    from sqlalchemy import text

    with eng.connect() as c:
        if sid is None:
            sid = c.execute(text("SELECT MAX(sync_run_id) FROM audit.source_sync_run "
                                 "WHERE source_name = :s"), {"s": pub.AUDIT_SOURCE}).scalar()
        return tuple(c.execute(text("SELECT sync_run_id, status, rows_loaded, error_message, finished_at IS NOT NULL "
                                    "FROM audit.source_sync_run WHERE sync_run_id = :i"), {"i": sid}).one())


@pg
@pytest.mark.parametrize("caso", sorted(RAW_INCOERENTE))
def test_pg_raw_incoerente_recusa_e_destino_fica_byte_a_byte(pg_engine, run_pg, caso):
    _carregar_raw(pg_engine, *base())
    r = run_pg()                                                      # foto anterior, publicada de verdade
    assert r["applied"] is True and _auditoria(pg_engine, r["sync_run_id"])[1:3] == ("success", 5)
    antes = _foto_bytes(pg_engine)
    assert antes[0][0] == 5 and antes[1][0] == 4

    _carregar_raw(pg_engine, *RAW_INCOERENTE[caso][0]())
    with pytest.raises(pub.PublicacaoRecusada, match="contagem RAW por par"):
        run_pg()
    assert _foto_bytes(pg_engine) == antes                            # contagem e conteudo iguais
    sid, status, rows, erro, fechou = _auditoria(pg_engine)
    assert sid > r["sync_run_id"] and (status, rows, fechou) == ("failed", 0, True)
    assert "fonte_inconsistente" in erro and RAW_INCOERENTE[caso][1] in erro


@pg
def test_pg_caso_positivo_continua_publicando_e_substitui(pg_engine, run_pg):
    from sqlalchemy import text

    _carregar_raw(pg_engine, *base())
    r1 = run_pg()
    antes = _foto_bytes(pg_engine)
    linhas, cobertura = base()
    linhas[0] = l("apice", "A1", "C1", D1, "shop", "11.00")          # spend revisado, contagem coerente
    _carregar_raw(pg_engine, linhas, cobertura)
    r2 = run_pg()
    assert r2["applied"] is True and r2["spend_medido_publicado"] == Decimal("23.75")
    assert _foto_bytes(pg_engine) != antes
    assert _auditoria(pg_engine, r2["sync_run_id"])[1:3] == ("success", 5)
    with pg_engine.connect() as c:
        assert set(c.execute(text(f"SELECT source_run_id FROM {pub.FACT}")).scalars()) == {r2["run_id"]}
        assert set(c.execute(text(f"SELECT source_run_id FROM {pub.FACT_COBERTURA}")).scalars()) == {r2["run_id"]}
    assert r1["run_id"] != r2["run_id"]


@pg
def test_pg_success_que_nao_fecha_desfaz_a_publicacao(pg_engine, run_pg, monkeypatch):
    _carregar_raw(pg_engine, *base())
    run_pg()
    antes = _foto_bytes(pg_engine)
    linhas, cobertura = base()
    linhas[0] = l("apice", "A1", "C1", D1, "shop", "11.00")
    _carregar_raw(pg_engine, linhas, cobertura)
    real = pub.audit_start
    sids = []

    def start_e_fecha(n):                       # outro processo "fecha" a linha antes do COMMIT
        sid = real(n)
        sids.append(sid)
        pub.audit_finish(sid, "failed", rows_loaded=0, error_message="fechada por fora")
        return sid

    monkeypatch.setattr(pub, "audit_start", start_e_fecha)
    with pytest.raises(pub.AuditoriaNaoFechou):
        run_pg()
    assert _foto_bytes(pg_engine) == antes                            # success nao saiu => dados nao sairam
    assert _auditoria(pg_engine, sids[0])[1:4] == ("failed", 0, "fechada por fora")   # nao foi sobrescrita


@pg
def test_pg_audit_finish_nunca_rebaixa_success(pg_engine, run_pg):
    _carregar_raw(pg_engine, *base())
    r = run_pg()
    with pytest.raises(pub.AuditoriaNaoFechou):
        pub.audit_finish(r["sync_run_id"], "failed", rows_loaded=0, error_message="tardio")
    pub._auditar_falha(r["sync_run_id"], "tardio")                  # caminho do except: so' loga
    assert _auditoria(pg_engine, r["sync_run_id"])[1:4] == ("success", 5, None)


def _inserir_raw_alheio(eng):
    from sqlalchemy import text

    with eng.begin() as o:
        o.execute(text("INSERT INTO raw.tiktok_ads_campaign_daily VALUES "
                       "('apice', 'A9', 'C1', :d, 'shop', 1, 1, 1, 1, 1, NULL, NULL), "
                       "('apice', 'A9', 'C2', :d, 'shop', 1, 1, 1, 1, 1, NULL, NULL)"), {"d": D2})
        o.execute(text("INSERT INTO raw.tiktok_ads_coverage VALUES ('apice', 'A9', :d, 2)"), {"d": D2})


class _Intercalada:
    """Conexao que, logo apos ler a fato da raw, deixa outro processo COMMITar fato + cobertura novas."""

    def __init__(self, conn, eng):
        self.conn, self.eng = conn, eng

    def execute(self, sql, params=None):
        res = self.conn.execute(sql) if params is None else self.conn.execute(sql, params)
        if sql is pub.SQL_LINHAS:
            res = list(res)                                           # materializa antes do commit alheio
            _inserir_raw_alheio(self.eng)
        return res


@pg
def test_pg_leitura_ve_um_snapshot_so(pg_engine):
    _carregar_raw(pg_engine, *base())
    with pg_engine.begin() as c:
        fonte = pub.ler_fonte(_Intercalada(c, pg_engine), D1, D2)
    # REPEATABLE READ: nem as linhas nem a cobertura do commit alheio aparecem -> coerente
    assert not [x for x in fonte["linhas"] + fonte["cobertura"] if x["advertiser_id"] == "A9"]
    T.montar_shadow(fonte["linhas"], fonte["cobertura"])


@pg
def test_pg_contraprova_read_committed_le_cobertura_sem_as_linhas(pg_engine, monkeypatch):
    """Discriminador do teste acima: sem o snapshot, a mesma intercalacao produz par incoerente."""
    from sqlalchemy import text

    monkeypatch.setattr(pub, "SQL_SNAPSHOT", text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
    monkeypatch.setattr(pub, "SNAPSHOT_ESPERADO", ("read committed", "on"))
    _carregar_raw(pg_engine, *base())
    with pg_engine.begin() as c:
        fonte = pub.ler_fonte(_Intercalada(c, pg_engine), D1, D2)
    assert [x["linhas"] for x in fonte["cobertura"] if x["advertiser_id"] == "A9"] == [2]
    assert not [x for x in fonte["linhas"] if x["advertiser_id"] == "A9"]
    with pytest.raises(T.FonteInconsistente, match="A9 2026-10-15 declarado=2 lido=0"):
        T.montar_shadow(fonte["linhas"], fonte["cobertura"])


@pg
def test_pg_leitura_e_read_only_e_exige_inicio_de_transacao(pg_engine):
    from sqlalchemy import exc, text

    _carregar_raw(pg_engine, *base())
    with pytest.raises(exc.DBAPIError, match="read-only"):
        with pg_engine.begin() as c:
            pub.ler_fonte(c, D1, D2)
            c.execute(text("INSERT INTO raw.tiktok_ads_coverage VALUES ('apice', 'Z', '2026-10-14', 0)"))
    with pytest.raises(exc.DBAPIError, match="before any query"):
        with pg_engine.begin() as c:
            c.execute(text("SELECT 1"))
            pub.ler_fonte(c, D1, D2)
