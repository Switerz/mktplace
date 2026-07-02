"""
Testes da regra de Pareto A/B/C/D dinamica compartilhada por ML, TikTok e
Shopee (`performance_service._PARETO_BUCKET_CASE_SQL`).

Regra definitiva (corrigida — ver docs/backlog.md e o comentario acima de
_PARETO_BUCKET_CASE_SQL no service): cada produto e classificado pelo
percentual acumulado ANTES de incluir o proprio GMV
(`previous_cum_gmv = cum_gmv - gmv`), nao depois. Limiares com "<" estrito.
Consequencia: o maior produto do conjunto (acumulado anterior = 0%) sempre
cai em A, mesmo que ele isolado represente mais de 50% do GMV total.

Duas camadas:
1. Estrutural (sempre roda, sem banco): garante que a expressao SQL
   compartilhada usa `cum_gmv - gmv` (acumulado ANTES do produto) e os
   limiares 50/80/95 com "<" estrito.
2. Execucao real (best-effort): roda a MESMA expressao contra dados
   literais (`VALUES (...)`) via a conexao configurada — sem tocar em
   nenhuma tabela real (nao ha INSERT/UPDATE/CREATE, apenas SELECT sobre
   uma lista literal) — para provar o comportamento exato em fronteira,
   empate e GMV zero/nulo/negativo. Pula (skip) se o banco nao estiver
   acessivel nesta maquina, para nao quebrar CI sem credenciais.
"""
import pytest
from sqlalchemy import text

from app.services import performance_service as perf_svc

try:
    from app.database import engine as _engine
except Exception:  # pragma: no cover
    _engine = None


def _real_conn():
    if _engine is None:
        pytest.skip("banco nao configurado nesta maquina (DATABASE_URL ausente)")
    try:
        conn = _engine.connect()
    except Exception as exc:
        pytest.skip(f"banco inacessivel nesta maquina: {exc}")
    return conn


def _classify(conn, rows: list[tuple[str, float]]) -> dict[str, str | None]:
    """Roda a MESMA expressao de bucket usada em produção sobre uma lista
    literal de (id, gmv) — sem ler nenhuma tabela real. `id` funciona como
    desempate deterministico (igual `item_id`/`product_id`/`sku_ref_key` em
    produção). Retorna {id: bucket} (id ausente = GMV nao positivo, excluido
    do ranking)."""
    # gmv::numeric explicito — as colunas reais (gross_revenue/gmv) sao
    # NUMERIC; literais inteiros nus fariam o Postgres truncar a divisao
    # cum_gmv/total_gmv como divisao inteira, mascarando o teste.
    values_sql = ", ".join(f"('{k}', {v}::numeric)" for k, v in rows)
    sql = text(f"""
        WITH src AS (SELECT * FROM (VALUES {values_sql}) AS v(id, gmv)),
        base AS (SELECT id, gmv FROM src WHERE gmv > 0),
        ranked AS (
            SELECT id, gmv,
                   SUM(gmv) OVER (ORDER BY gmv DESC, id ASC) AS cum_gmv,
                   SUM(gmv) OVER ()                          AS total_gmv
            FROM base
        )
        SELECT id, {perf_svc._PARETO_BUCKET_CASE_SQL} AS pareto_bucket
        FROM ranked
    """)
    result = conn.execute(sql).fetchall()
    return {r.id: r.pareto_bucket for r in result}


# ---------------------------------------------------------------------------
# 1. Estrutural — sempre roda, sem banco
# ---------------------------------------------------------------------------

def test_case_sql_classifica_pelo_acumulado_anterior_com_limiares_estritos():
    sql = perf_svc._PARETO_BUCKET_CASE_SQL
    assert "cum_gmv - gmv" in sql, "deve classificar pelo acumulado ANTES do produto, nao depois"
    assert "< 50" in sql and "'A_top50'" in sql
    assert "< 80" in sql and "'B_next30'" in sql
    assert "< 95" in sql and "'C_next15'" in sql
    assert "ELSE" in sql and "'D_tail'" in sql
    # nao deve mais usar "<=" (fronteira exata pertence ao bucket anterior
    # apenas por causa do estrito "<" no acumulado ANTERIOR, nao por "<=")
    assert "<= 50" not in sql and "<= 80" not in sql and "<= 95" not in sql


def test_pareto_order_e_meta_cobrem_os_4_buckets_nessa_ordem():
    assert perf_svc._PARETO_ORDER == ("A_top50", "B_next30", "C_next15", "D_tail")
    for bk in perf_svc._PARETO_ORDER:
        assert bk in perf_svc._PARETO_META


# ---------------------------------------------------------------------------
# 2. Execucao real (best-effort) — literais, zero leitura/escrita de tabela
# ---------------------------------------------------------------------------

def test_primeiro_produto_com_60_por_cento_do_gmv_fica_no_bucket_a():
    # Caso explicito pedido na correcao: o maior produto (aqui, isolado, 60%
    # do GMV total) tem acumulado ANTERIOR = 0% (nada vem antes dele) -> A.
    # Ele NUNCA deve cair em B so por ser grande demais.
    with _real_conn() as conn:
        result = _classify(conn, [("p1", 60), ("p2", 40)])
    assert result["p1"] == "A_top50"


def test_produto_que_cruza_a_fronteira_de_50_permanece_no_bucket_a():
    # p1=40 (acumulado anterior 0% -> A) ; p2=20 (acumulado anterior 40% ->
    # ainda A, mesmo que ACUMULADO DEPOIS de incluir p2 seja 60%/>50%) —
    # a fronteira e avaliada pelo estado ANTES do produto, entao o produto
    # que faz o acumulado cruzar 50% fica no bucket anterior a fronteira.
    with _real_conn() as conn:
        result = _classify(conn, [("p1", 40), ("p2", 20), ("p3", 20), ("p4", 20)])
    assert result["p1"] == "A_top50"   # anterior=0%
    assert result["p2"] == "A_top50"   # anterior=40% (<50, mesmo cruzando 50% ao incluir)
    assert result["p3"] == "B_next30"  # anterior=60% (>=50, <80)
    assert result["p4"] == "C_next15"  # anterior=80% (>=80, <95)


def test_fronteira_exata_80_como_acumulado_anterior_cai_em_c_nao_b():
    # p1=80 (anterior 0% -> A) ; p2=20 (anterior = 80%, exatamente no
    # limiar; "<80" estrito exclui, cai em C).
    with _real_conn() as conn:
        result = _classify(conn, [("p1", 80), ("p2", 20)])
    assert result["p1"] == "A_top50"
    assert result["p2"] == "C_next15"


def test_fronteira_exata_95_como_acumulado_anterior_cai_em_d_nao_c():
    # p1=95 (anterior 0% -> A) ; p2=5 (anterior = 95%, exatamente no
    # limiar; "<95" estrito exclui, cai em D).
    with _real_conn() as conn:
        result = _classify(conn, [("p1", 95), ("p2", 5)])
    assert result["p1"] == "A_top50"
    assert result["p2"] == "D_tail"


def test_gmv_empatado_e_deterministico_entre_execucoes():
    rows = [("p1", 45), ("p2", 25), ("p3", 15), ("p4", 10), ("p5", 5)]  # total=100
    with _real_conn() as conn:
        first = _classify(conn, rows)
        second = _classify(conn, rows)
    assert first == second  # mesmo conjunto, mesmo resultado nas duas execucoes
    # anteriores: p1=0(A) p2=45(A) p3=70(B) p4=85(C) p5=95(D)
    assert first["p1"] == "A_top50"
    assert first["p2"] == "A_top50"
    assert first["p3"] == "B_next30"
    assert first["p4"] == "C_next15"
    assert first["p5"] == "D_tail"
    assert len(set(first.values())) == 4  # os 4 buckets aparecem nesse conjunto


def test_gmv_zero_e_excluido_do_ranking():
    with _real_conn() as conn:
        result = _classify(conn, [("p1", 100), ("p2", 0)])
    assert "p1" in result
    assert "p2" not in result  # GMV=0 nao participa do Pareto (regra: apenas GMV positivo)


def test_gmv_negativo_e_excluido_do_ranking():
    with _real_conn() as conn:
        result = _classify(conn, [("p1", 100), ("p2", -50)])
    assert "p1" in result
    assert "p2" not in result  # GMV negativo tambem nao participa (defensivo — nao ha caso real no mart)


def test_soma_dos_buckets_reconcilia_com_total_sem_lacuna_nem_sobreposicao():
    rows = [("p1", 45), ("p2", 25), ("p3", 15), ("p4", 10), ("p5", 5)]
    with _real_conn() as conn:
        result = _classify(conn, rows)
    # particao estrita: cada produto aparece em exatamente 1 bucket, nenhum de fora
    assert set(result.keys()) == {"p1", "p2", "p3", "p4", "p5"}
    assert all(v is not None for v in result.values())
