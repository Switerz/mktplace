"""Gate FULL-PUBLISH-FIX-1 — publicacao transacional do Estoque Full.

POR QUE ESTES TESTES SAO CONTRA POSTGRES REAL
----------------------------------------------
O defeito corrigido aqui NAO aparece em teste com duble. `InvalidRequestError`
nasce do ciclo de vida real da Connection do SQLAlchemy: `session.connection()`
faz autobegin e devolve uma Connection ja' em transacao, e o `.begin()` seguinte
levanta. Um fake devolve o que mandarmos, inclusive para um limite transacional
impossivel. Rollback, advisory lock e `ON CONFLICT` tambem so' existem no banco.

GUARDA CONTRA ESCREVER NO LUGAR ERRADO
---------------------------------------
Este modulo ESCREVE. Ele so' roda quando `TEST_PG_DSN` esta definido **e** a
DSN que o pipeline realmente usa e' exatamente essa -- senao pula. Sem essa
igualdade, um `.env` esquecido apontando para producao transformaria a suite
num gravador. A verificacao e' do alvo efetivo (`local_engine().url`), nao da
variavel que alguem pretendeu configurar.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta

import pytest

DSN = os.getenv("TEST_PG_DSN")

pytestmark = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN nao configurado")

if DSN:  # pragma: no branch
    from pipelines.common.db import local_engine

    _ALVO = local_engine().url.render_as_string(hide_password=False)
    if _ALVO != DSN:
        pytestmark = pytest.mark.skip(
            reason="o pipeline aponta para outra DSN que nao a de teste: "
                   "recusado para nao escrever fora do banco descartavel")

from sqlalchemy import text  # noqa: E402

import pipelines.sync_shopee_fbs_stock_daily as P  # noqa: E402

# Numeros que o dry-run reconciliou em producao. O fixture reproduz a MESMA
# forma de dado, para que o teste meça o que a operacao vai medir.
TOTAL_PRODUTOS = 301
TOTAL_KITS = 22
TOTAL_OPERACIONAIS = TOTAL_PRODUTOS - TOTAL_KITS   # 279
TOTAL_LOCALIZACOES = 679
TOTAL_UNIDADES = 119_847

CONTAS = ("apice", "barbours", "lescent", "rituaria")

DDL_FONTE = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS silver;
DROP TABLE IF EXISTS raw.shopee_products;
CREATE TABLE raw.shopee_products (
    shop_account text NOT NULL, item_id bigint NOT NULL,
    stock_info jsonb, ingested_at timestamptz NOT NULL,
    PRIMARY KEY (shop_account, item_id));
DROP TABLE IF EXISTS silver.stg_shopee_products;
CREATE TABLE silver.stg_shopee_products (
    shop_account text NOT NULL, item_id bigint NOT NULL, brand text NOT NULL,
    item_name text, item_sku text, item_status text, is_kit boolean,
    is_fulfillment_by_shopee boolean NOT NULL,
    PRIMARY KEY (shop_account, item_id));
DROP TABLE IF EXISTS silver.stg_shopee_orders;
CREATE TABLE silver.stg_shopee_orders (
    shop_account text NOT NULL, order_sn text NOT NULL, order_status text,
    created_date_brt date NOT NULL, PRIMARY KEY (shop_account, order_sn));
DROP TABLE IF EXISTS silver.stg_shopee_order_items;
CREATE TABLE silver.stg_shopee_order_items (
    shop_account text NOT NULL, order_sn text NOT NULL,
    item_id bigint NOT NULL, quantity bigint NOT NULL);
"""


def _distribuir(total: int, partes: int) -> list[int]:
    base, resto = divmod(total, partes)
    return [base + (1 if i < resto else 0) for i in range(partes)]


def _semear_fonte(conn) -> None:
    """Fonte sintetica: nenhum campo de pessoa, nenhum identificador real."""
    hoje = date.today()
    conn.exec_driver_sql(DDL_FONTE)
    # `marketplace_id = 3` e' Shopee em producao, e a auditoria referencia essa
    # FK. Sem a linha aqui o INSERT falharia SO' no teste, escondendo que em
    # producao ele passa -- ou empurrando para um `NULL` que perderia a
    # atribuicao que producao espera.
    conn.execute(text("""
        INSERT INTO marts.dim_marketplace
            (marketplace_id, nome_marketplace, slug, ativo)
        VALUES (3, 'Shopee', 'shopee', TRUE)
        ON CONFLICT (marketplace_id) DO NOTHING"""))

    com_tres = 189            # 189*3 + 112*1 = 679
    locs = [3] * com_tres + [1] * (TOTAL_PRODUTOS - com_tres)
    unidades = _distribuir(TOTAL_UNIDADES, TOTAL_PRODUTOS)
    assert sum(locs) == TOTAL_LOCALIZACOES
    assert sum(unidades) == TOTAL_UNIDADES

    for i in range(TOTAL_PRODUTOS):
        item_id = 100_000 + i
        conta = CONTAS[i % len(CONTAS)]
        is_kit = i >= TOTAL_OPERACIONAIS
        # Todas as entradas vendaveis: assim a soma do grao fino e a soma de
        # `full_stock_saleable` coincidem, que e' o fechamento sob teste.
        stock_info = {
            "shopee_stock": [
                {"location_id": f"CD-{j:02d}", "stock": q, "if_saleable": True}
                for j, q in enumerate(_distribuir(unidades[i], locs[i]))],
            "seller_stock": [{"stock": 0}],
            "summary_info": {"total_reserved_stock": 0,
                             "total_available_stock": unidades[i]},
        }
        conn.execute(text(
            "INSERT INTO raw.shopee_products VALUES (:c,:i,CAST(:s AS jsonb),:t)"),
            {"c": conta, "i": item_id, "s": json.dumps(stock_info),
             "t": f"{hoje} 08:00:00-03"})
        conn.execute(text(
            "INSERT INTO silver.stg_shopee_products VALUES "
            "(:c,:i,:b,:n,:k,'NORMAL',:kit,TRUE)"),
            {"c": conta, "i": item_id, "b": conta,
             "n": f"Produto Sintetico {i:03d}", "k": f"SKU-{i:05d}",
             "kit": is_kit})

        if not is_kit and i % 3 != 2:
            qtd = 1 + (i % 40)
            for d in (1, 2, 3):
                sn = f"ORD-{item_id}-{d}"
                conn.execute(text(
                    "INSERT INTO silver.stg_shopee_orders VALUES "
                    "(:c,:s,'completed',:d)"),
                    {"c": conta, "s": sn, "d": hoje - timedelta(days=d)})
                conn.execute(text(
                    "INSERT INTO silver.stg_shopee_order_items VALUES "
                    "(:c,:s,:i,:q)"),
                    {"c": conta, "s": sn, "i": item_id, "q": qtd})


@pytest.fixture
def banco():
    """Banco COMMITADO e limpo. O pipeline abre as proprias transacoes."""
    eng = local_engine()
    with eng.begin() as conn:
        conn.execute(text(f"TRUNCATE {P.FACT_LOCATION}"))
        conn.execute(text(f"TRUNCATE {P.FACT_PRODUTO}"))
        conn.execute(text("DELETE FROM audit.source_sync_run "
                          "WHERE source_name = :s"), {"s": P.AUDIT_SOURCE})
        _semear_fonte(conn)
    yield eng


def _contagens(eng) -> dict:
    with eng.connect() as c:
        return {
            "loc_linhas": c.execute(text(
                f"SELECT COUNT(*) FROM {P.FACT_LOCATION}")).scalar(),
            "loc_soma": c.execute(text(
                f"SELECT COALESCE(SUM(full_stock),0) FROM {P.FACT_LOCATION}"
            )).scalar(),
            "prod_linhas": c.execute(text(
                f"SELECT COUNT(*) FROM {P.FACT_PRODUTO}")).scalar(),
            "prod_soma": c.execute(text(
                f"SELECT COALESCE(SUM(full_stock_saleable),0) "
                f"FROM {P.FACT_PRODUTO}")).scalar(),
            "operacionais": c.execute(text(
                f"SELECT COUNT(*) FROM {P.FACT_PRODUTO} WHERE NOT is_kit"
            )).scalar(),
            "kits": c.execute(text(
                f"SELECT COUNT(*) FROM {P.FACT_PRODUTO} "
                f"WHERE classificacao_torre = 'KIT_NAO_CONCILIADO'")).scalar(),
        }


def _auditoria(eng) -> list[dict]:
    with eng.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT sync_run_id, status, rows_extracted, rows_loaded, "
            "       finished_at, error_message, marketplace_id "
            "  FROM audit.source_sync_run WHERE source_name = :s "
            " ORDER BY sync_run_id"), {"s": P.AUDIT_SOURCE}).mappings()]


def _locks_advisory(eng) -> int:
    with eng.connect() as c:
        return c.execute(text(
            "SELECT COUNT(*) FROM pg_locks WHERE locktype = 'advisory' "
            "  AND objid = :k"), {"k": P.ADVISORY_LOCK_KEY}).scalar()


# ===========================================================================
# 1. O defeito: o padrao anterior levanta InvalidRequestError
# ===========================================================================

def test_padrao_anterior_levanta_invalid_request_error(banco):
    """Reproduz o limite transacional que quebrou o primeiro `--apply`.

    Nao importa o arquivo antigo -- importa a FORMA: `session.connection()` faz
    autobegin, e o `.begin()` seguinte e' impossivel. Se algum dia o SQLAlchemy
    passar a aceitar isso, este teste falha e nos avisa que a justificativa da
    correcao mudou.
    """
    from sqlalchemy.exc import InvalidRequestError
    from pipelines.common.db import LocalSession

    session = LocalSession()
    try:
        with pytest.raises(InvalidRequestError) as exc:
            with session.connection().begin():
                pass
        assert "already initialized" in str(exc.value)
    finally:
        session.close()


def test_a_alternativa_ingenua_tambem_falharia(banco):
    """`with session.begin():` DEPOIS de tocar a Session tambem levanta.

    Esta' aqui porque a troca obvia -- e errada -- seria essa. A saida foi nao
    depender de autobegin, usando `engine.begin()` sobre uma Connection.
    """
    from sqlalchemy.exc import InvalidRequestError
    from pipelines.common.db import LocalSession

    session = LocalSession()
    try:
        session.connection()                     # autobegin acontece aqui
        with pytest.raises(InvalidRequestError):
            with session.begin():
                pass
    finally:
        session.close()


def test_publicacao_usa_transacao_explicita_do_engine():
    """Contrato no codigo: nada de Session no caminho de escrita."""
    import ast
    import inspect
    fonte = inspect.getsource(P._publicar)
    # Mede o CODIGO, nao a docstring: ela explica o defeito antigo e cita
    # `session.connection()` de proposito. Varrer o texto inteiro acusaria a
    # propria documentacao da correcao.
    arvore = ast.parse(fonte.lstrip())
    corpo = arvore.body[0].body
    if isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant):
        corpo = corpo[1:]
    codigo = "\n".join(ast.unparse(no) for no in corpo)

    assert "local_engine().begin()" in codigo
    assert "session" not in codigo.lower(), (
        "a Session do ORM voltou ao caminho de escrita")
    assert "pg_try_advisory_xact_lock" in codigo


# ===========================================================================
# 2. A correcao publica, e os numeros fecham
# ===========================================================================

def test_apply_publica_e_reproduz_a_fotografia_do_dry_run(banco):
    res = P.run(apply=True)

    assert res["applied"] is True
    assert res["locations"] == TOTAL_LOCALIZACOES
    assert res["produtos"] == TOTAL_PRODUTOS
    assert res["produtos_operacionais"] == TOTAL_OPERACIONAIS
    assert res["produtos_kit_contexto"] == TOTAL_KITS
    assert res["full_stock_saleable_total"] == TOTAL_UNIDADES

    c = _contagens(banco)
    assert c["loc_linhas"] == TOTAL_LOCALIZACOES
    assert c["prod_linhas"] == TOTAL_PRODUTOS
    assert c["loc_soma"] == TOTAL_UNIDADES, "soma do grao fino mudou"
    assert c["prod_soma"] == TOTAL_UNIDADES


def test_operacionais_mais_kits_fecham_o_total(banco):
    P.run(apply=True)
    c = _contagens(banco)
    assert c["operacionais"] == TOTAL_OPERACIONAIS
    assert c["kits"] == TOTAL_KITS
    assert c["operacionais"] + c["kits"] == TOTAL_PRODUTOS == c["prod_linhas"]


def test_as_duas_fatos_fecham_produto_a_produto(banco):
    """Nao basta o TOTAL bater: dois erros opostos se cancelariam no agregado."""
    P.run(apply=True)
    with banco.connect() as c:
        divergentes = c.execute(text(f"""
            SELECT p.item_id, p.full_stock_saleable, COALESCE(SUM(l.full_stock),0)
              FROM {P.FACT_PRODUTO} p
              LEFT JOIN {P.FACT_LOCATION} l
                ON l.ref_date = p.ref_date AND l.shop_account = p.shop_account
               AND l.item_id = p.item_id AND l.model_id = p.model_id
             GROUP BY p.item_id, p.full_stock_saleable
            HAVING p.full_stock_saleable <> COALESCE(SUM(l.full_stock),0)
        """)).all()
    assert divergentes == [], f"{len(divergentes)} produtos nao fecham"


def test_contagem_de_cds_bate_com_o_grao_fino(banco):
    P.run(apply=True)
    with banco.connect() as c:
        ruins = c.execute(text(f"""
            SELECT p.item_id FROM {P.FACT_PRODUTO} p
              LEFT JOIN {P.FACT_LOCATION} l
                ON l.ref_date = p.ref_date AND l.shop_account = p.shop_account
               AND l.item_id = p.item_id AND l.model_id = p.model_id
             GROUP BY p.item_id, p.location_count
            HAVING p.location_count <> COUNT(l.location_id)
        """)).all()
    assert ruins == []


# ===========================================================================
# 3. Idempotencia e isolamento entre dias
# ===========================================================================

def test_reexecutar_no_mesmo_dia_e_idempotente(banco):
    P.run(apply=True)
    primeira = _contagens(banco)
    P.run(apply=True)
    segunda = _contagens(banco)
    assert primeira == segunda, "a segunda execucao duplicou ou perdeu linha"


def test_dia_anterior_e_preservado(banco):
    """A publicacao toca SO' o ref_date corrente."""
    ontem = P._hoje_brt() - timedelta(days=1)
    with banco.begin() as c:
        c.execute(text(f"""
            INSERT INTO {P.FACT_PRODUTO}
                (ref_date, brand, shop_account, item_id, model_id, is_kit,
                 full_stock_saleable, full_stock_total, location_count,
                 units_sold_28d, days_with_sales_28d, avg_daily_units_28d,
                 cobertura_torre_dias, classificacao_torre, vinculo_vendas,
                 source_run_id, source_captured_at)
            VALUES (:d,'apice','apice','999','0',FALSE,
                    7, 7, 1, 0, 0, 0, NULL,
                    'SEM_GIRO_CANDIDATO','SEM_VENDA_NA_JANELA',
                    'anterior', NOW())"""), {"d": ontem})
        c.execute(text(f"""
            INSERT INTO {P.FACT_LOCATION}
                (ref_date, brand, shop_account, item_id, model_id, location_id,
                 full_stock, is_saleable, is_kit, source_run_id,
                 source_captured_at)
            VALUES (:d,'apice','apice','999','0','CD-99',7,TRUE,FALSE,
                    'anterior', NOW())"""), {"d": ontem})

    P.run(apply=True)

    with banco.connect() as c:
        assert c.execute(text(
            f"SELECT COUNT(*) FROM {P.FACT_PRODUTO} WHERE ref_date = :d"),
            {"d": ontem}).scalar() == 1
        assert c.execute(text(
            f"SELECT COUNT(*) FROM {P.FACT_LOCATION} WHERE ref_date = :d"),
            {"d": ontem}).scalar() == 1
        assert c.execute(text(
            f"SELECT COUNT(*) FROM {P.FACT_PRODUTO} WHERE ref_date = :d"),
            {"d": P._hoje_brt()}).scalar() == TOTAL_PRODUTOS


# ===========================================================================
# 4. Falha entre as duas escritas -> rollback total
# ===========================================================================

def test_falha_entre_as_duas_escritas_desfaz_tudo(banco, monkeypatch):
    """A fato de localizacao ja' foi inserida quando a de produto explode.

    Sem transacao unica, o grao fino ficaria publicado e o agregado nao -- a
    tela mostraria estoque por CD sem produto correspondente. E' o estado
    "parcialmente publicado" que este desenho existe para tornar impossivel.
    """
    original = P._inserir
    chamadas = {"n": 0}

    def _falha_na_segunda(conn, tabela, cols, linhas):
        chamadas["n"] += 1
        if tabela == P.FACT_PRODUTO:
            raise RuntimeError("falha simulada entre as duas escritas")
        return original(conn, tabela, cols, linhas)

    monkeypatch.setattr(P, "_inserir", _falha_na_segunda)

    with pytest.raises(RuntimeError, match="falha simulada"):
        P.run(apply=True)

    assert chamadas["n"] == 2, "a segunda escrita nem foi tentada"
    c = _contagens(banco)
    assert c["loc_linhas"] == 0, "grao fino ficou publicado sem o agregado"
    assert c["prod_linhas"] == 0


def test_reconciliacao_pre_commit_tambem_desfaz_tudo(banco, monkeypatch):
    """A guarda de reconciliacao roda DENTRO da transacao, e abortar desfaz."""
    original = P._inserir

    def _insere_a_menos(conn, tabela, cols, linhas):
        # Publica uma linha a menos no agregado: a reconciliacao tem de pegar.
        return original(conn, tabela, cols,
                        linhas[:-1] if tabela == P.FACT_PRODUTO else linhas)

    monkeypatch.setattr(P, "_inserir", _insere_a_menos)

    with pytest.raises(P.ShopeeStockSyncError, match="reconciliacao"):
        P.run(apply=True)

    c = _contagens(banco)
    assert c["loc_linhas"] == 0 and c["prod_linhas"] == 0


# ===========================================================================
# 5. Advisory lock
# ===========================================================================

def test_lock_liberado_no_sucesso(banco):
    P.run(apply=True)
    assert _locks_advisory(banco) == 0


def test_lock_liberado_na_falha(banco, monkeypatch):
    monkeypatch.setattr(P, "_inserir", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("estoura")))
    with pytest.raises(RuntimeError):
        P.run(apply=True)
    assert _locks_advisory(banco) == 0, "lock ficou preso apos a falha"


def test_lock_ocupado_recusa_a_execucao(banco):
    """Prova que o lock e' REALMENTE tomado: sem isso, 'liberado' e' trivial."""
    outro = banco.connect()
    try:
        outro.execute(text("SELECT pg_advisory_lock(:k)"),
                      {"k": P.ADVISORY_LOCK_KEY})
        outro.commit()
        with pytest.raises(P.ConcurrentRunError):
            P.run(apply=True)
        # E nada foi publicado enquanto a chave estava com outro dono.
        assert _contagens(banco)["prod_linhas"] == 0
    finally:
        outro.execute(text("SELECT pg_advisory_unlock(:k)"),
                      {"k": P.ADVISORY_LOCK_KEY})
        outro.commit()
        outro.close()


# ===========================================================================
# 6. Auditoria operacional
# ===========================================================================

def test_auditoria_termina_em_success(banco):
    res = P.run(apply=True)
    linhas = _auditoria(banco)
    assert len(linhas) == 1
    a = linhas[0]
    assert a["sync_run_id"] == res["sync_run_id"]
    assert a["status"] == "success"
    assert a["finished_at"] is not None
    assert a["marketplace_id"] == P.AUDIT_MARKETPLACE_ID
    assert a["rows_extracted"] == TOTAL_LOCALIZACOES + TOTAL_PRODUTOS
    assert a["rows_loaded"] == TOTAL_LOCALIZACOES + TOTAL_PRODUTOS
    assert a["error_message"] is None


def test_auditoria_termina_em_failed_com_detalhe(banco, monkeypatch):
    monkeypatch.setattr(P, "_inserir", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("falha proposital na escrita")))
    with pytest.raises(RuntimeError):
        P.run(apply=True)

    linhas = _auditoria(banco)
    assert len(linhas) == 1
    a = linhas[0]
    assert a["status"] == "failed"
    assert a["finished_at"] is not None
    assert a["rows_loaded"] == 0
    assert "falha proposital" in a["error_message"]


def test_nenhuma_execucao_nova_fica_em_running(banco, monkeypatch):
    """Os dois desfechos fecham o registro. `running` nao sobrevive a nenhum."""
    P.run(apply=True)
    monkeypatch.setattr(P, "_inserir", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("segunda execucao falha")))
    with pytest.raises(RuntimeError):
        P.run(apply=True)
    monkeypatch.undo()
    P.run(apply=True)

    linhas = _auditoria(banco)
    assert len(linhas) == 3
    assert [a["status"] for a in linhas] == ["success", "failed", "success"]
    assert all(a["finished_at"] is not None for a in linhas)
    assert not [a for a in linhas if a["status"] == "running"]


def test_auditoria_sobrevive_ao_rollback_dos_dados(banco, monkeypatch):
    """A linha de auditoria NAO pode viver na transacao da publicacao.

    Se vivesse, o rollback que protege as fatos apagaria junto a prova de que a
    execucao aconteceu -- e a falha ficaria invisivel.
    """
    monkeypatch.setattr(P, "_inserir", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("estoura depois do audit_start")))
    with pytest.raises(RuntimeError):
        P.run(apply=True)

    assert _contagens(banco)["prod_linhas"] == 0, "dado ficou publicado"
    assert len(_auditoria(banco)) == 1, "o rastro da falha sumiu"


def test_status_fora_do_dominio_e_recusado(banco):
    P.run(apply=True)
    sync_id = _auditoria(banco)[0]["sync_run_id"]
    with pytest.raises(P.ShopeeStockSyncError, match="dominio"):
        P.audit_finish(sync_id, "concluido")


def test_dry_run_nao_escreve_nada_nem_audita(banco):
    res = P.run(apply=False)
    assert res["applied"] is False
    assert "sync_run_id" not in res
    assert _contagens(banco)["prod_linhas"] == 0
    assert _auditoria(banco) == []


# ===========================================================================
# 7. Nenhum dado pessoal ou credencial
# ===========================================================================

def test_sanitizar_remove_credencial_de_dsn():
    msg = ("could not connect to server: "
           "postgresql://usuario:SenhaSuperSecreta@host.neon.tech:5432/db")
    limpo = P.sanitizar(msg)
    assert "SenhaSuperSecreta" not in limpo
    # O host permanece: sem ele a mensagem deixa de dizer QUAL banco recusou.
    assert "host.neon.tech" in limpo


def test_sanitizar_remove_cpf_e_email():
    limpo = P.sanitizar("erro em 123.456.789-00 de fulano@exemplo.com.br")
    assert "123.456.789-00" not in limpo
    assert "fulano@exemplo.com.br" not in limpo


def test_error_message_gravado_nao_carrega_credencial(banco, monkeypatch):
    """O caminho REAL: excecao com DSN -> coluna da auditoria."""
    segredo = "SenhaSuperSecreta"
    monkeypatch.setattr(P, "_inserir", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError(f"falhou em postgresql://user:{segredo}@host:5432/db")))
    with pytest.raises(RuntimeError):
        P.run(apply=True)

    gravado = _auditoria(banco)[0]["error_message"]
    assert segredo not in gravado
    assert "<removido>" in gravado


def test_error_message_e_truncado(banco, monkeypatch):
    monkeypatch.setattr(P, "_inserir", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("x" * (P.AUDIT_ERRO_MAX_CHARS * 3))))
    with pytest.raises(RuntimeError):
        P.run(apply=True)
    assert len(_auditoria(banco)[0]["error_message"]) <= P.AUDIT_ERRO_MAX_CHARS


def test_nenhuma_coluna_de_pessoa_nas_fatos(banco):
    """A fonte nao traz pessoa, e as fatos nao tem onde guardar."""
    P.run(apply=True)
    with banco.connect() as c:
        colunas = [r[0] for r in c.execute(text("""
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'marts'
               AND table_name LIKE 'fact_shopee_fbs_stock%'"""))]
    proibidas = re.compile(
        r"buyer|compr|cpf|cnpj|email|phone|telefone|address|endereco|"
        r"recipient|destinat|customer", re.I)
    assert not [c for c in colunas if proibidas.search(c)]


def test_limiares_provisorios_nao_mudaram(banco):
    """O gate proibe mexer nos limites, e a ressalva tem de continuar visivel."""
    assert P.COBERTURA_BAIXA_DIAS_PROVISORIO == 7
    assert P.COBERTURA_EXCESSO_DIAS_PROVISORIO == 90
    res = P.run(apply=True)
    assert res["limites_provisorios"] == {
        "cobertura_baixa_dias_PROVISORIO": 7,
        "cobertura_excesso_dias_PROVISORIO": 90,
    }
    assert any("PROVISORIOS" in a for a in res["warnings"])
    assert all(cl.endswith("_CANDIDATO") or cl.endswith("_CANDIDATA")
               or cl in ("SUFICIENTE", "SEM_DEMANDA_MEDIDA",
                         "KIT_NAO_CONCILIADO")
               for cl in res["por_classificacao"])
