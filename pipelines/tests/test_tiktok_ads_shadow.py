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


@pytest.mark.parametrize("valor,spend,esperado", [
    (Decimal("50"), Decimal("10"), Decimal("5.00")), (Decimal("50"), Decimal("0"), None),
    (None, Decimal("10"), None), (Decimal("50"), None, None)])
def test_roas_so_com_denominador_valido(valor, spend, esperado):
    assert T.roas(valor, spend) == esperado


# ---------------------------------------------------------------------------
# Travas e publicacao (fake)
# ---------------------------------------------------------------------------
class Res:
    def __init__(self, escalar=None, mapa=None, linhas=None):
        self.escalar, self.mapa, self.linhas = escalar, mapa, linhas or []

    def scalar(self):
        return self.escalar

    def mappings(self):
        return self

    def one(self):
        return self.mapa

    def all(self):
        return self.linhas


class Conn:
    def __init__(self, lock=True, rec=None, cob=None):
        self.lock, self.rec, self.cob, self.sqls = lock, rec or [], cob, []
        self.connection = self

    def execute(self, sql, params=None):
        s = str(sql)
        self.sqls.append((s, params))
        if "pg_try_advisory_xact_lock" in s:
            return Res(escalar=self.lock)
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
    monkeypatch.setattr(pub, "audit_finish", lambda sid, st, **kw: estado["audit"].append((st,)))
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


def test_fonte_inconsistente_dry_run_reporta_e_apply_recusa(amb, monkeypatch):
    linhas, cobertura = base()
    linhas.append(dict(linhas[0]))
    monkeypatch.setattr(pub, "_ler", lambda i, f: {"linhas": linhas, "cobertura": cobertura})
    assert pub.run(apply=False, hoje=date(2026, 10, 20))["estado"] == "fonte_inconsistente"
    with pytest.raises(pub.PublicacaoRecusada, match="fonte_inconsistente"):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"] == []


def test_publica_com_lock_primeiro_cobertura_antes_da_fato(amb, monkeypatch):
    conn = _conn_certo()
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(conn))
    r = pub.run(apply=True, hoje=date(2026, 10, 20))
    assert r["applied"] is True and r["marcas_publicadas"] == ["apice", "barbours"]
    assert "pg_try_advisory_xact_lock" in conn.sqls[0][0]
    assert [t for t, _ in amb["ins"]] == [pub.FACT_COBERTURA, pub.FACT]
    deletes = [s for s, _ in conn.sqls if s.lstrip().startswith("DELETE")]
    assert pub.FACT in deletes[0] and pub.FACT_COBERTURA in deletes[1]
    assert amb["audit"][-1] == ("success",)


def test_reconciliacao_divergente_aborta(amb, monkeypatch):
    monkeypatch.setattr(pub, "local_engine", lambda: Engine(Conn(rec=[], cob={"n": 0, "linhas": 0})))
    with pytest.raises(pub.ReconciliacaoFalhou):
        pub.run(apply=True, hoje=date(2026, 10, 20))
    assert amb["audit"][-1] == ("failed",)


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
        c.execute(text("SET TRANSACTION READ ONLY"))
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
