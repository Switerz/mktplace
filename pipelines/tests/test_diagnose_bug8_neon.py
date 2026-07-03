"""
Testes de pipelines/reconciliation/diagnose_bug8_neon.py (Gate 4A.1):
diagnose e' somente leitura, drift e dados novos sao detectados,
credenciais nunca aparecem no relatorio, prepare e' bloqueado sem
flag/variavel de ambiente, e guardas estruturais contra referenciar o
Data Mart ou conter comandos destrutivos.

Usa conexoes falsas — nenhum banco real e' tocado. FakeCursor levanta
AssertionError se qualquer query nao comecar com SELECT, o que torna
"diagnose e' somente leitura" uma propriedade verificada, nao apenas
presumida.
"""
import re
import types
from datetime import date
from pathlib import Path

import pytest

import pipelines.reconciliation.diagnose_bug8_neon as diag

MODULE_PATH = Path(diag.__file__)

SECRET_NEON_URL = "postgresql://neonuser:S3gredoNeon@ep-example-123.us-east-2.aws.neon.tech:5432/marts"
SECRET_LOCAL_URL = "postgresql://postgres:S3nh4Local@localhost:5432/mktplace_control"


def _row(ref_month=date(2026, 1, 1), brand="kokeshi", sku_ref="SKU1", sku_ref_key="SKU1",
         product_name="Produto A", variation_name=None, gmv=100.0, units_sold=5,
         completed_orders=4, canceled_orders=1, cancel_rate_pct=20.0, unique_buyers=3, avg_price=25.0):
    return dict(ref_month=ref_month, brand=brand, sku_ref=sku_ref, sku_ref_key=sku_ref_key,
                product_name=product_name, variation_name=variation_name, gmv=gmv, units_sold=units_sold,
                completed_orders=completed_orders, canceled_orders=canceled_orders,
                cancel_rate_pct=cancel_rate_pct, unique_buyers=unique_buyers, avg_price=avg_price)


class FakeCursor:
    def __init__(self, rows, executed_log):
        self.rows = rows
        self.executed_log = executed_log

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.executed_log.append(norm)
        if not norm.upper().startswith("SELECT"):
            raise AssertionError(f"query nao-SELECT detectada (diagnose deve ser somente leitura): {norm}")

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.executed_log = []
        self.closed = False

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.rows, self.executed_log)

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# diagnose e' somente leitura / caminho feliz
# ---------------------------------------------------------------------------

def test_diagnose_e_somente_leitura_e_sem_problemas_quando_identico():
    rows = [_row(), _row(product_name="Produto B", sku_ref_key="SKU2")]
    neon_conn = FakeConn(list(rows))
    local_conn = FakeConn(list(rows))

    report = diag.run_diagnose(neon_conn, local_conn)

    assert report["problems"] == []
    assert report["new_in_neon_count"] == 0
    assert report["missing_from_neon_count"] == 0
    assert report["drifted_count"] == 0
    assert report["except_neon_not_local"] == 0
    assert report["except_local_not_neon"] == 0
    # todas as queries emitidas em ambas as conexoes foram SELECT
    assert neon_conn.executed_log and all(q.upper().startswith("SELECT") for q in neon_conn.executed_log)
    assert local_conn.executed_log and all(q.upper().startswith("SELECT") for q in local_conn.executed_log)


# ---------------------------------------------------------------------------
# drift e dados novos
# ---------------------------------------------------------------------------

def test_drift_e_detectado_mesma_chave_valor_diferente():
    local_rows = [_row(gmv=100.0)]
    neon_rows = [_row(gmv=999.0)]  # mesma chave (brand/ref_month/sku_ref_key/product_name), gmv diferente

    report = diag.run_diagnose(FakeConn(neon_rows), FakeConn(local_rows))

    assert report["drifted_count"] == 1
    assert report["new_in_neon_count"] == 0
    assert report["missing_from_neon_count"] == 0
    assert any("drift" in p for p in report["problems"])


def test_dados_novos_no_neon_sao_detectados():
    local_rows = [_row()]
    neon_rows = [_row(), _row(product_name="Produto Novo", sku_ref_key="SKU_NOVO")]

    report = diag.run_diagnose(FakeConn(neon_rows), FakeConn(local_rows))

    assert report["new_in_neon_count"] == 1
    assert report["drifted_count"] == 0
    assert any("dados novos" in p for p in report["problems"])


def test_chave_ausente_do_neon_e_detectada():
    local_rows = [_row(), _row(product_name="Produto Perdido", sku_ref_key="SKU_PERDIDO")]
    neon_rows = [_row()]

    report = diag.run_diagnose(FakeConn(neon_rows), FakeConn(local_rows))

    assert report["missing_from_neon_count"] == 1
    assert any("NAO no Neon" in p for p in report["problems"])


def test_agregado_por_marca_mes_reflete_drift_de_gmv():
    local_rows = [_row(brand="kokeshi", ref_month=date(2026, 1, 1), gmv=100.0)]
    neon_rows = [_row(brand="kokeshi", ref_month=date(2026, 1, 1), gmv=150.0)]

    report = diag.run_diagnose(FakeConn(neon_rows), FakeConn(local_rows))

    row = report["by_brand_month"][0]
    assert row["gmv_neon"] == 150.0
    assert row["gmv_local"] == 100.0


# ---------------------------------------------------------------------------
# credenciais nunca aparecem no relatorio
# ---------------------------------------------------------------------------

def test_credenciais_nunca_aparecem_no_relatorio_impresso(capsys):
    rows = [_row()]
    report = diag.run_diagnose(FakeConn(rows), FakeConn(rows))
    diag._print_report(report, SECRET_NEON_URL, SECRET_LOCAL_URL)

    out = capsys.readouterr().out
    assert "S3gredoNeon" not in out
    assert "S3nh4Local" not in out
    assert "neonuser" not in out
    # host sanitizado deve aparecer (prova que o relatorio nao omitiu tudo)
    assert "ep-example-123.us-east-2.aws.neon.tech" in out


# ---------------------------------------------------------------------------
# prepare bloqueado
# ---------------------------------------------------------------------------

def test_prepare_bloqueado_sem_flag(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_TOUCHES_NEON", "1")
    args = types.SimpleNamespace(prepare=False)
    with pytest.raises(RuntimeError, match="flag --prepare"):
        diag.run_prepare(args)


def test_prepare_bloqueado_sem_variavel_de_ambiente(monkeypatch):
    monkeypatch.delenv("I_UNDERSTAND_THIS_TOUCHES_NEON", raising=False)
    args = types.SimpleNamespace(prepare=True)
    with pytest.raises(RuntimeError, match="I_UNDERSTAND_THIS_TOUCHES_NEON"):
        diag.run_prepare(args)


def test_prepare_recusa_se_diagnostico_encontrar_problema(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_TOUCHES_NEON", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@fake-neon-host/fakedb")
    args = types.SimpleNamespace(prepare=True)

    def fake_diagnose_com_problema():
        return {"problems": ["drift simulado"]}

    with pytest.raises(RuntimeError, match="diagnostico encontrou"):
        diag.run_prepare(args, diagnose_fn=fake_diagnose_com_problema)


# A implementacao completa de do_prepare_neon (criacao de backup/staging
# no Neon, reconciliacao, commit/rollback) e' testada em
# pipelines/tests/test_prepare_bug8_neon.py, com conexoes falsas dedicadas.


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

def test_nunca_referencia_datamart_database_url():
    source = MODULE_PATH.read_text(encoding="utf-8")
    read_patterns = [
        r'os\.environ(?:\.get)?\(\s*["\']DATAMART_DATABASE_URL',
        r'os\.getenv\(\s*["\']DATAMART_DATABASE_URL',
    ]
    for pattern in read_patterns:
        assert not re.search(pattern, source), f"padrao proibido encontrado: {pattern}"


def test_database_url_neon_e_lido_em_exatamente_um_lugar():
    source = MODULE_PATH.read_text(encoding="utf-8")
    matches = re.findall(r'os\.environ\.get\(\s*["\']DATABASE_URL["\']', source)
    assert len(matches) == 1, f"DATABASE_URL deveria ser lido em exatamente 1 lugar (_get_neon_url), encontrado {len(matches)}"


def test_nenhum_comando_destrutivo_existe_no_script():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for word in ("DROP", "DELETE", "UPDATE"):
        assert not re.search(rf"\b{word}\b", source, re.IGNORECASE), f"palavra proibida encontrada: {word}"
    # "truncate" verificado separadamente para nao repetir o literal usado
    # na palavra proibida em si dentro do proprio arquivo de teste
    forbidden = "".join(["t", "r", "u", "n", "c", "a", "t", "e"])
    assert forbidden not in source.lower()


def test_insert_e_create_table_nunca_tem_a_tabela_real_como_alvo():
    """O Gate 4A.2 (implementado neste modulo) cria backup/staging via
    CREATE TABLE/INSERT — mas SEMPRE em marts.{backup_name}/{staging_name}
    gerados, nunca em marts.{REAL_TABLE} diretamente. Verifica a fonte:
    nenhuma ocorrencia literal de 'INSERT INTO marts.{REAL_TABLE}' ou
    'CREATE TABLE marts.{REAL_TABLE}' (as f-strings do modulo usam nomes
    de variavel diferentes para backup/staging, nunca REAL_TABLE)."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO marts.{REAL_TABLE}" not in source
    assert "CREATE TABLE marts.{REAL_TABLE}" not in source
    assert re.search(r"\bINSERT\s+INTO\b", source, re.IGNORECASE), "esperado: Gate 4A.2 cria staging via INSERT"
    assert re.search(r"\bCREATE\s+TABLE\b", source, re.IGNORECASE), "esperado: Gate 4A.2 cria backup/staging via CREATE TABLE"
