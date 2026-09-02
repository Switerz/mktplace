"""Gate PMA-1A — contraprovas do contrato e do importador da referencia (PDV).

Sem fixture e sem `parametrize`, para permitir execucao por runner de stdlib.
"""
from __future__ import annotations

import ast
import io
import tokenize
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pipelines.pma import reference_contract as rc
from pipelines.pma import reference_import as ri

CONTRACT_PATH = Path(rc.__file__)
IMPORT_PATH = Path(ri.__file__)

CAPTURADO = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


def _codigo(caminho: Path) -> str:
    """Codigo sem comentario e sem string literal — varre o que FAZ."""
    pedacos: list[str] = []
    fonte = caminho.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        if tok.type in (tokenize.NAME, tokenize.OP):
            pedacos.append(tok.string)
    return " ".join(pedacos).lower()


def _linha(brand, sku, gtin, pdv, atacado=None, row=33, nome="Produto X"):
    classificado = rc.classify_gtin(gtin)
    return rc.ReferenceRow(
        brand=brand,
        reference_row_id=rc.reference_row_id(brand, sku, str(gtin or ""), row),
        source_sku=sku,
        source_gtin=classificado.value,
        product_name=nome,
        wholesale_amount=None if atacado is None else Decimal(str(atacado)),
        suggested_retail_amount=None if pdv is None else Decimal(str(pdv)),
        source_row_number=row,
        quality_notes=[classificado.note] if classificado.note else [],
    )


# ---------------------------------------------------------------------------
# 1. Contrato semantico
# ---------------------------------------------------------------------------

def test_constantes_do_contrato():
    assert rc.REFERENCE_TYPE == "suggested_retail_pdv"
    assert rc.POLICY_STATUS == "not_applicable_to_own_store_monitoring"
    assert rc.VALIDITY_STATUS == "missing"
    assert rc.COVERAGE_STATUS == "advertised_only"


def test_pma_e_valor_proibido_de_reference_type():
    assert rc.FORBIDDEN_REFERENCE_TYPE == "pma"
    assert rc.REFERENCE_TYPE != rc.FORBIDDEN_REFERENCE_TYPE


def test_vigencia_permanece_missing_nunca_inventada():
    linhas = [_linha("kokeshi", "KS1", "7908790700922", "32.90")]
    rc.classify_quality(linhas)
    snapshot_rows = []
    for r in linhas:
        snapshot_rows.append({
            "snapshot_id": "s", "reference_row_id": r.reference_row_id,
            "captured_at": CAPTURADO, "brand": r.brand,
            "source_sku": r.source_sku, "source_gtin": r.source_gtin,
            "product_name": r.product_name,
            "wholesale_amount": r.wholesale_amount,
            "suggested_retail_amount": r.suggested_retail_amount,
            "reference_type": rc.REFERENCE_TYPE,
            "validity_status": rc.VALIDITY_STATUS,
            "quality_status": r.quality_status,
            "quality_notes": rc.notes_to_text(r.quality_notes),
            "source_file_hash": "0" * 64, "source_run_id": "run",
        })
    ri._assert_snapshot_contract(snapshot_rows)
    assert all(r["validity_status"] == "missing" for r in snapshot_rows)


def test_validity_status_diferente_de_missing_e_recusado():
    reg = {
        "reference_type": rc.REFERENCE_TYPE, "validity_status": "declared",
        "quality_status": "ok", "brand": "kokeshi", "source_gtin": None,
        "suggested_retail_amount": Decimal("1"),
    }
    erro = None
    try:
        ri._assert_snapshot_contract([reg])
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "missing" in erro


def test_reference_type_pma_e_recusado_no_snapshot():
    reg = {
        "reference_type": "pma", "validity_status": "missing",
        "quality_status": "ok", "brand": "kokeshi", "source_gtin": None,
        "suggested_retail_amount": Decimal("1"),
    }
    erro = None
    try:
        ri._assert_snapshot_contract([reg])
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "PDV nao e" in erro


def test_escopo_de_marcas():
    assert rc.REFERENCE_BRANDS == ("apice", "barbours", "kokeshi", "rituaria", "yenzah")
    assert rc.MONITORED_BRANDS == ("barbours", "kokeshi", "lescent", "rituaria")
    assert rc.NO_REFERENCE_BRANDS == ("lescent",)
    assert rc.OUT_OF_SCOPE_BRANDS == ("apice", "yenzah")


# ---------------------------------------------------------------------------
# 2. Normalizacao e chave de match
# ---------------------------------------------------------------------------

def test_sku_numerico_nao_ganha_ponto_zero():
    """SKU do Apice e' numerico de 5 digitos e chega como float do xlsx."""
    assert rc.normalize_sku(20910.0) == "20910"
    assert rc.normalize_sku(20910) == "20910"
    assert rc.normalize_sku(" bb03038 ") == "BB03038"
    assert rc.normalize_sku(None) is None
    assert rc.normalize_sku("") is None


def test_dun_de_14_digitos_nao_vira_ean():
    c = rc.classify_gtin(79087907006940)
    assert c.value is None
    assert c.kind == "dun14"
    assert c.note and "14 digitos" in c.note
    assert "79087907006940" in c.note  # valor bruto preservado para auditoria


def test_ean_de_consumidor_e_aceito():
    for bruto, esperado in ((7901128400051, "7901128400051"),
                            ("7901128400051", "7901128400051"),
                            (7901128400051.0, "7901128400051")):
        c = rc.classify_gtin(bruto)
        assert c.value == esperado and c.kind == "consumer_ean"


def test_codigo_com_tamanho_invalido_e_marcado():
    c = rc.classify_gtin("12345")
    assert c.value is None and c.kind == "invalid" and c.note


def test_reference_row_id_e_deterministico():
    a = rc.reference_row_id("rituaria", "RT01016", "7901128300047", 40)
    b = rc.reference_row_id("rituaria", "RT01016", "7901128300047", 40)
    assert a == b and len(a) == 64
    # A linha de origem entra na identidade: RT01016 aparece DUAS vezes.
    c = rc.reference_row_id("rituaria", "RT01016", "7908407007789", 41)
    assert a != c


def test_snapshot_id_exige_timezone():
    erro = None
    try:
        rc.snapshot_id_for(datetime(2026, 9, 2, 12, 0, 0))
    except rc.ReferenceContractError as exc:
        erro = str(exc)
    assert erro is not None
    assert rc.snapshot_id_for(CAPTURADO) == "pma-ref:20260902T120000Z"


def test_cabecalho_e_normalizado_sem_acento():
    assert rc.normalize_header("Preço Atacado") == "preco atacado"
    assert rc.normalize_header("EAN (Código de barras)") == "ean codigo de barras"
    assert rc.normalize_header("Preço na Ponta (PDV)") == "preco na ponta pdv"


def test_helper_morto_foi_removido():
    """`normalize_brand` colapsava espacos e ficou sem chamador (Fase 4)."""
    assert not hasattr(rc, "normalize_brand")


# ---------------------------------------------------------------------------
# 3. Ambiguidade: marcada, nunca resolvida por adivinhacao
# ---------------------------------------------------------------------------

def test_sku_duplicado_na_marca_marca_ambiguidade():
    linhas = [
        _linha("rituaria", "RT01016", "7901128300047", "109.90", row=40),
        _linha("rituaria", "RT01016", "7908407007789", "38.72", row=41),
    ]
    rc.classify_quality(linhas)
    assert linhas[0].quality_status == rc.QUALITY_AMBIGUOUS_SKU
    assert linhas[1].quality_status == rc.QUALITY_AMBIGUOUS_SKU
    assert all(not r.sku_match_allowed for r in linhas)
    assert all(r.gtin_match_allowed for r in linhas)


def test_caso_real_rituaria_gera_os_tres_diagnosticos():
    """RT01016 duas vezes; EAN 7901128300047 em RT01016 e RT01024, com PDV divergente."""
    linhas = [
        _linha("rituaria", "RT01016", "7901128300047", "109.90", row=40),
        _linha("rituaria", "RT01016", "7908407007789", "38.72", row=41),
        _linha("rituaria", "RT01024", "7901128300047", "109.01", row=46),
    ]
    rc.classify_quality(linhas)
    assert linhas[0].quality_status == rc.QUALITY_AMBIGUOUS_BOTH
    assert linhas[1].quality_status == rc.QUALITY_AMBIGUOUS_SKU
    assert linhas[2].quality_status == rc.QUALITY_AMBIGUOUS_GTIN
    # Nenhum PDV foi escolhido nem descartado: os dois seguem no snapshot.
    assert {str(linhas[0].suggested_retail_amount),
            str(linhas[2].suggested_retail_amount)} == {"109.90", "109.01"}


def test_ambiguidade_e_escopada_por_marca():
    """SKU igual em marcas DIFERENTES nao e' ambiguidade."""
    linhas = [
        _linha("barbours", "20910", None, "50.00", row=40),
        _linha("kokeshi", "20910", None, "60.00", row=41),
    ]
    rc.classify_quality(linhas)
    assert all(r.quality_status == rc.QUALITY_OK for r in linhas)
    assert all(r.sku_match_allowed for r in linhas)


def test_biconditional_pdv_x_quality_status_nos_dois_sentidos():
    """F1: `missing_suggested_price` <=> `suggested_retail_amount` NULO."""
    base = {
        "reference_type": rc.REFERENCE_TYPE, "validity_status": "missing",
        "brand": "kokeshi", "source_gtin": None, "quality_notes": None,
    }
    # Valido: missing + NULO
    ri._assert_snapshot_contract([
        dict(base, quality_status="missing_suggested_price",
             suggested_retail_amount=None)
    ])
    # Valido: ok + valor
    ri._assert_snapshot_contract([
        dict(base, quality_status="ok", suggested_retail_amount=Decimal("10.00"))
    ])
    # Invalido: missing + valor (mentiria sobre a planilha)
    erro = None
    try:
        ri._assert_snapshot_contract([
            dict(base, quality_status="missing_suggested_price",
                 suggested_retail_amount=Decimal("10.00"))
        ])
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "NULO" in erro
    # Invalido: ok + NULO (referencia fantasma)
    erro = None
    try:
        ri._assert_snapshot_contract([
            dict(base, quality_status="ok", suggested_retail_amount=None)
        ])
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "fantasma" in erro


def test_pdv_zero_e_recusado_e_ausencia_se_registra_como_nulo():
    base = {
        "reference_type": rc.REFERENCE_TYPE, "validity_status": "missing",
        "brand": "kokeshi", "source_gtin": None, "quality_notes": None,
        "quality_status": "ok",
    }
    erro = None
    try:
        ri._assert_snapshot_contract([
            dict(base, suggested_retail_amount=Decimal("0.00"))
        ])
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "nao e' referencia" in erro


def test_linha_sem_pdv_sobrevive_no_snapshot_construido():
    """Prova de ponta a ponta: leitura -> classificacao -> contrato do snapshot.

    A linha sem PDV chega ao snapshot com `quality_status` de ausencia e valor
    NULO, e o contrato (que espelha o CHECK do DDL) a aceita. Antes do PMA-1A-R
    esta combinacao existia no importador e o DDL declarava a coluna NOT NULL —
    o primeiro `--apply` com planilha incompleta teria falhado.
    """
    linhas = [
        _linha("kokeshi", "KS_SEM", "7908790700922", None, row=40),
        _linha("kokeshi", "KS_COM", "7908790700915", "32.90", row=41),
    ]
    rc.classify_quality(linhas)
    registros = []
    for r in linhas:
        registros.append({
            "snapshot_id": "s", "reference_row_id": r.reference_row_id,
            "captured_at": CAPTURADO, "brand": r.brand,
            "source_sku": r.source_sku, "source_gtin": r.source_gtin,
            "product_name": r.product_name,
            "wholesale_amount": r.wholesale_amount,
            "suggested_retail_amount": r.suggested_retail_amount,
            "reference_type": rc.REFERENCE_TYPE,
            "validity_status": rc.VALIDITY_STATUS,
            "quality_status": r.quality_status,
            "quality_notes": rc.notes_to_text(r.quality_notes),
            "source_file_hash": "0" * 64, "source_run_id": "run",
        })
    ri._assert_snapshot_contract(registros)
    sem = [r for r in registros if r["source_sku"] == "KS_SEM"][0]
    assert sem["quality_status"] == "missing_suggested_price"
    assert sem["suggested_retail_amount"] is None
    # E a linha permanece auditavel: produto e SKU continuam la.
    assert sem["product_name"] and sem["source_sku"]


def test_preco_ausente_domina_o_diagnostico():
    linhas = [
        _linha("kokeshi", "KS1", "7908790700922", None, row=40),
        _linha("kokeshi", "KS1", "7908790700915", None, row=41),
    ]
    rc.classify_quality(linhas)
    for r in linhas:
        assert r.quality_status == rc.QUALITY_MISSING_PRICE
        assert not r.sku_match_allowed and not r.gtin_match_allowed
        assert any("nao e' zero" in n or "NAO e' zero" in n for n in r.quality_notes)


def test_dun_nao_impede_match_por_sku():
    """Regra 7: DUN invalida o GTIN, nao a linha."""
    linhas = [_linha("barbours", "BB03038", 79087907006940, "54.90", row=40)]
    rc.classify_quality(linhas)
    r = linhas[0]
    assert r.source_gtin is None
    assert not r.gtin_match_allowed
    assert r.sku_match_allowed


# ---------------------------------------------------------------------------
# 4. PII: nunca lida, nunca persistida
# ---------------------------------------------------------------------------

def test_leitura_comeca_depois_do_bloco_cadastral():
    assert rc.HEADER_ROW == 32
    assert rc.FIRST_DATA_ROW == 33


def test_valor_com_forma_de_pii_e_recusado():
    for valor in ("12.345.678/0001-99", "123.456.789-00",
                  "contato@empresa.com.br", "(11) 98765-4321", "01310-100"):
        erro = None
        try:
            rc.assert_no_pii_shaped_value(valor, "product_name")
        except rc.ReferenceContractError as exc:
            erro = str(exc)
        assert erro is not None, valor
        # A mensagem descreve, mas NAO ecoa o valor recusado.
        assert valor not in erro, valor


def test_snapshot_nao_tem_coluna_cadastral():
    proibidas = ("razao", "cnpj", "cpf", "endereco", "cep", "telefone",
                 "email", "e_mail", "contato", "bairro", "cidade", "cliente")
    for coluna in ri.BUSINESS_COLUMNS + ri.AUDIT_COLUMNS:
        for p in proibidas:
            assert p not in coluna.lower(), (coluna, p)


def test_colunas_de_pedido_sao_ignoradas():
    """`preco final`, `quantidade` e `valor` sao campos de PEDIDO, nao referencia."""
    assert rc.IGNORED_COLUMNS == ("preco final", "quantidade", "valor")
    for campo, aliases in rc.COLUMN_ALIASES.items():
        for alias in aliases:
            assert alias not in rc.IGNORED_COLUMNS, (campo, alias)


def test_allowlist_de_colunas_nao_inclui_campo_cadastral():
    todas = [a for aliases in rc.COLUMN_ALIASES.values() for a in aliases]
    for a in todas:
        for p in ("razao", "cnpj", "cpf", "endereco", "cep", "telefone", "mail"):
            assert p not in a, (a, p)


# ---------------------------------------------------------------------------
# 5. Snapshot local: fora do repositorio, sem sobrescrita
# ---------------------------------------------------------------------------

def test_destino_dentro_do_repositorio_e_recusado():
    repo = Path(ri.__file__).resolve().parents[2]
    erro = None
    try:
        ri.assert_outside_repo(repo / "docs" / "snapshot.json")
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "DENTRO do repositorio" in erro


def test_destino_fora_do_repositorio_e_aceito():
    alvo = Path(ri.__file__).resolve().parents[3] / "fora_do_repo_snapshot.json"
    assert ri.assert_outside_repo(alvo) == alvo.resolve()


def test_nenhum_xlsx_ou_csv_e_versionado():
    """O repo nao pode conter planilha nem CSV de preco desta frente."""
    repo = Path(ri.__file__).resolve().parents[2]
    for padrao in ("**/*.xlsx", "**/*.xls"):
        for achado in repo.glob(padrao):
            partes = {p.lower() for p in achado.parts}
            assert "pma" not in partes, achado
            assert "pma0" not in partes, achado


# ---------------------------------------------------------------------------
# 6. `--apply` obrigatorio
# ---------------------------------------------------------------------------

def test_apply_e_flag_e_default_e_dry_run():
    args = ri.build_parser().parse_args(
        ["--file", "x.xlsx", "--out", "y.json"]
    )
    assert args.apply is False
    args = ri.build_parser().parse_args(
        ["--file", "x.xlsx", "--out", "y.json", "--apply"]
    )
    assert args.apply is True


def test_dry_run_nao_chama_publicacao():
    texto = IMPORT_PATH.read_text(encoding="utf-8")
    trecho = texto[texto.index("def main("):]
    assert "if args.apply:" in trecho
    idx = trecho.index("if args.apply:")
    antes = trecho[:idx]
    assert "publish_snapshot" not in antes
    assert "_neon_writable" not in antes


def test_out_e_obrigatorio():
    erro = None
    try:
        ri.build_parser().parse_args(["--file", "x.xlsx"])
    except SystemExit as exc:
        erro = exc
    assert erro is not None


# ---------------------------------------------------------------------------
# 7. Publicacao: append-only, lock, EXCEPT, rollback
# ---------------------------------------------------------------------------

class FakeCursor:
    """Ver a nota do fake equivalente em `test_sync_ml_listing_price_serving.py`:
    o `execute_values` real usa `connection.encoding` + `mogrify` e entrega
    bytes."""

    def __init__(self, conn, respostas):
        self.conn = conn
        self.connection = conn
        self.respostas = respostas
        self._ultima = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        if isinstance(sql, bytes):
            sql = sql.decode("utf-8", "replace")
        baixo = " ".join(sql.lower().split())
        self.conn.executed.append((baixo, params))
        candidatos = [p for p in self.respostas if p in baixo]
        chave = max(candidatos, key=len) if candidatos else None
        self._ultima = self.respostas[chave] if chave else None
        if "insert into marts." in baixo:
            self.rowcount = self.conn.insert_rowcount
        return None

    def fetchone(self):
        return self._ultima

    def mogrify(self, sql, args=None):
        """`psycopg2.extras.execute_values` real chama `mogrify`."""
        return str(sql).encode()

    def close(self):
        pass


class FakeConn:
    encoding = "UTF8"

    def __init__(self, respostas, insert_rowcount=1):
        self.executed: list[tuple] = []
        self.commits = 0
        self.rollbacks = 0
        self.respostas = respostas
        self.insert_rowcount = insert_rowcount

    def cursor(self):
        return FakeCursor(self, self.respostas)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _snapshot_minimo(n=1):
    rows = []
    for i in range(n):
        rows.append({c: None for c in ri.BUSINESS_COLUMNS + ri.AUDIT_COLUMNS})
        rows[-1].update({
            "snapshot_id": "pma-ref:20260902T120000Z",
            "reference_row_id": f"{i:064d}",
            "captured_at": CAPTURADO, "brand": "kokeshi",
            "source_sku": f"KS{i}", "source_gtin": None,
            "product_name": "P", "suggested_retail_amount": Decimal("10.00"),
            "reference_type": rc.REFERENCE_TYPE,
            "validity_status": rc.VALIDITY_STATUS, "quality_status": "ok",
            "source_file_hash": "0" * 64, "source_run_id": "run",
        })
    return {
        "snapshot_id": "pma-ref:20260902T120000Z", "rows": rows,
        "total_rows": n, "captured_at": CAPTURADO,
    }


def _respostas(existentes=0, staging=1, except_n=0):
    return {
        f"count(*) as n from {ri.TARGET_TABLE} where snapshot_id": {"n": existentes},
        f"count(*) as n from {ri.STAGING_TABLE}": {"n": staging},
        "except": {"n": except_n},
    }


def test_publicacao_toma_advisory_lock_proprio():
    conn = FakeConn(_respostas())
    ri.publish_snapshot(conn, _snapshot_minimo())
    locks = [p for sql, p in conn.executed if "pg_advisory_xact_lock" in sql]
    assert locks == [(ri.ADVISORY_LOCK_KEY,)]
    assert ri.ADVISORY_LOCK_KEY not in (907_120_007, 908_120_008, 912_120_012, 914_120_014)


def test_snapshot_id_existente_e_recusado_append_only():
    conn = FakeConn(_respostas(existentes=221))
    erro = None
    try:
        ri.publish_snapshot(conn, _snapshot_minimo())
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "append-only" in erro
    assert conn.rollbacks == 1 and conn.commits == 0


def test_publicacao_nao_tem_delete_nem_truncate():
    conn = FakeConn(_respostas())
    ri.publish_snapshot(conn, _snapshot_minimo())
    for sql, _ in conn.executed:
        assert not sql.startswith("delete"), sql
        assert "truncate" not in sql, sql


def test_except_bidirecional_nos_dois_sentidos():
    conn = FakeConn(_respostas())
    resultado = ri.publish_snapshot(conn, _snapshot_minimo())
    excepts = [sql for sql, _ in conn.executed if "except" in sql]
    assert len(excepts) == 2
    assert resultado["checks"]["except_both_ways"] == (0, 0)
    assert conn.commits == 1


def test_except_divergente_faz_rollback():
    conn = FakeConn(_respostas(except_n=2))
    erro = None
    try:
        ri.publish_snapshot(conn, _snapshot_minimo())
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "EXCEPT bidirecional" in erro
    assert conn.rollbacks == 1 and conn.commits == 0


def test_staging_divergente_faz_rollback():
    conn = FakeConn(_respostas(staging=99))
    erro = None
    try:
        ri.publish_snapshot(conn, _snapshot_minimo())
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "staging divergiu" in erro
    assert conn.rollbacks == 1 and conn.commits == 0


def test_staging_e_temp_com_on_commit_drop():
    conn = FakeConn(_respostas())
    ri.publish_snapshot(conn, _snapshot_minimo())
    criacoes = [sql for sql, _ in conn.executed if "create temp table" in sql]
    assert len(criacoes) == 1 and "on commit drop" in criacoes[0]


# ---------------------------------------------------------------------------
# 8. Marca a partir do nome do arquivo
# ---------------------------------------------------------------------------

def test_marca_do_nome_do_arquivo():
    assert ri.brand_from_filename(Path("/tmp/rituaria.xlsx")) == "rituaria"
    assert ri.brand_from_filename(Path("/tmp/Tabela de Preco Yenzah.xlsx")) == "yenzah"


def test_nome_ambiguo_ou_desconhecido_e_recusado():
    for nome in ("/tmp/desconhecida.xlsx", "/tmp/kokeshi_e_yenzah.xlsx"):
        erro = None
        try:
            ri.brand_from_filename(Path(nome))
        except ri.ReferenceImportError as exc:
            erro = str(exc)
        assert erro is not None, nome


# ---------------------------------------------------------------------------
# 9. Valores: nulo nao e' zero, NaN recusado
# ---------------------------------------------------------------------------

def test_ausencia_nao_vira_zero():
    assert ri._to_decimal(None, "pdv", 33) is None
    assert ri._to_decimal("", "pdv", 33) is None
    # Zero na planilha nao e' referencia: vira None, que cai em missing_price.
    assert ri._to_decimal(0, "pdv", 33) is None
    assert ri._to_decimal(Decimal("0.00"), "pdv", 33) is None


def test_nan_e_recusado():
    erro = None
    try:
        ri._to_decimal(float("nan"), "pdv", 33)
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None and "NaN" in erro


def test_valor_nao_numerico_e_recusado():
    erro = None
    try:
        ri._to_decimal("abc", "pdv", 33)
    except ri.ReferenceImportError as exc:
        erro = str(exc)
    assert erro is not None


def test_preco_e_quantizado_em_duas_casas():
    assert ri._to_decimal("54.899", "pdv", 33) == Decimal("54.90")


# ---------------------------------------------------------------------------
# 10. Modulo de contrato nao depende de terceiros nem conecta no import
# ---------------------------------------------------------------------------

def test_contrato_usa_somente_stdlib():
    arvore = ast.parse(CONTRACT_PATH.read_text(encoding="utf-8"))
    stdlib = {"hashlib", "re", "unicodedata", "dataclasses", "datetime",
              "__future__"}
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for a in no.names:
                assert a.name.split(".")[0] in stdlib, a.name
        elif isinstance(no, ast.ImportFrom) and no.module:
            assert no.module.split(".")[0] in stdlib, no.module


def test_importador_nao_conecta_no_import():
    codigo = _codigo(IMPORT_PATH)
    arvore = ast.parse(IMPORT_PATH.read_text(encoding="utf-8"))
    for no in arvore.body:
        if isinstance(no, (ast.FunctionDef, ast.ClassDef)):
            continue
        if isinstance(no, ast.If):
            continue
        for filho in ast.walk(no):
            if isinstance(filho, ast.Call):
                nome = getattr(filho.func, "id", None) or getattr(filho.func, "attr", "")
                assert nome in ("compile", "dataclass"), nome
    # openpyxl e psycopg2 sao importados TARDIAMENTE, dentro de funcao.
    topo = [n for n in arvore.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    nomes_topo = []
    for n in topo:
        if isinstance(n, ast.Import):
            nomes_topo += [a.name.split(".")[0] for a in n.names]
        elif n.module:
            nomes_topo.append(n.module.split(".")[0])
    assert "openpyxl" not in nomes_topo
    assert "psycopg2" not in nomes_topo
    # `os` esta no topo, mas `os.environ` so e' lido dentro de `_get_neon_url`.
    assert "os" in nomes_topo
    assert "environ" in codigo  # existe...
    for no in arvore.body:  # ...e nunca no nivel do modulo
        if isinstance(no, (ast.FunctionDef, ast.ClassDef, ast.If)):
            continue
        assert "environ" not in ast.dump(no)


def test_notas_geradas_nao_tem_vocabulario_de_politica():
    """`quality_notes` chega ao payload: nao pode carregar palavra de sancao."""
    linhas = [
        _linha("rituaria", "RT01016", "7901128300047", "109.90", row=40),
        _linha("rituaria", "RT01016", "7908407007789", "38.72", row=41),
        _linha("rituaria", "RT01024", "7901128300047", "109.01", row=46),
        _linha("barbours", "BB03038", 79087907006940, "54.90", row=50),
        _linha("kokeshi", "KS9", "7908790700922", None, row=60),
    ]
    rc.classify_quality(linhas)
    for r in linhas:
        texto = rc.notes_to_text(r.quality_notes)
        if texto:
            rc.assert_payload_has_no_forbidden_terms(texto, "quality_notes")


def test_guard_de_vocabulario_reprova_termo_de_sancao():
    for termo in ("multa", "descadastramento", "PMA vigente", "infracao"):
        erro = None
        try:
            rc.assert_payload_has_no_forbidden_terms(
                f"referencia com {termo} aplicada", "quality_notes"
            )
        except rc.ReferenceContractError as exc:
            erro = str(exc)
        assert erro is not None, termo


def test_snapshot_recusa_nota_com_termo_proibido():
    reg = {
        "reference_type": rc.REFERENCE_TYPE, "validity_status": "missing",
        "quality_status": "ok", "brand": "kokeshi", "source_gtin": None,
        "suggested_retail_amount": Decimal("1"),
        "quality_notes": "PMA vigente do SKU",
    }
    erro = None
    try:
        ri._assert_snapshot_contract([reg])
    except rc.ReferenceContractError as exc:
        erro = str(exc)
    assert erro is not None


def test_zero_retry_no_importador():
    codigo = _codigo(IMPORT_PATH)
    for palavra in ("retry", "backoff", "tenacity", "sleep", "reconnect"):
        assert palavra not in codigo, palavra
