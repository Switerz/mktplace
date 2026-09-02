"""Gate PMA-1A — contraprovas de seguranca do sync de preco anunciado.

Sem fixture e sem `parametrize`, para que as funcoes sejam chamaveis tambem por
um runner minimo de stdlib.

O FAKE DE CURSOR DEVOLVE DICIONARIO, NAO TUPLA
----------------------------------------------
A conexao real usa `RealDictCursor`, e `fetchone()` devolve mapeamento. Um fake
que devolvesse tupla faria a suite passar e o runtime falhar sempre. Por isso o
fake abaixo devolve `dict`, e `test_todo_agregado_do_sql_tem_alias` garante que
todo agregado do SQL tenha `AS`: sem alias, a chave do dicionario seria o nome
gerado pelo servidor e o codigo nao a encontraria.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pipelines import sync_ml_listing_price_serving as s

MODULE_PATH = Path(s.__file__)
HOJE = date(2026, 9, 3)


def _codigo_executavel() -> str:
    """Codigo do modulo SEM comentario e SEM docstring/string literal.

    As varreduras de palavra proibida ("retry", "truncate") tem de olhar o que o
    modulo FAZ, nao o que ele explica: a propria docstring diz "ZERO retry" e
    "nunca ha TRUNCATE", e uma varredura ingenua reprovaria o texto que documenta
    a garantia. Aqui as strings sao removidas por tokenizacao, nao por regex.
    """
    fonte = MODULE_PATH.read_text(encoding="utf-8")
    pedacos: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        if tok.type == tokenize.NAME or tok.type == tokenize.OP:
            pedacos.append(tok.string)
    return " ".join(pedacos).lower()


def _sql_do_modulo() -> str:
    """Todo o SQL que o modulo constroi, montado de verdade."""
    return (s.build_source_query() + "\n" + s.build_source_aggregate_query()).lower()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeCursor:
    """Cursor de mentira com semantica de `RealDictCursor`: linhas sao dicts.

    Implementa `connection` e `mogrify` porque o `execute_values` REAL do
    psycopg2 usa os dois (`cur.connection.encoding` e um `mogrify` por linha) e
    entrega o comando final como BYTES. Sob o runner de stdlib o
    `execute_values` era falso e nada disso aparecia — 17 testes de publicacao
    so revelaram a lacuna quando o psycopg2 de verdade entrou no ambiente.
    """

    def __init__(self, conn, respostas=None):
        self.conn = conn
        self.connection = conn
        self.respostas = respostas or {}
        self._ultima = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        if isinstance(sql, bytes):
            sql = sql.decode("utf-8", "replace")
        self.conn.executed.append((" ".join(sql.split()), params))
        baixo = " ".join(sql.lower().split())
        # Escolhe o padrao MAIS LONGO que casa, nunca o primeiro: duas consultas
        # deste modulo comecam por `count(*) as n from pg_temp...` e um matcher
        # por ordem de insercao responderia a errada.
        candidatos = [p for p in self.respostas if p in baixo]
        chave = max(candidatos, key=len) if candidatos else None
        if chave is not None:
            valor = self.respostas[chave]
            self._ultima = valor() if callable(valor) else valor
        else:
            self._ultima = None
        if baixo.strip().startswith("delete"):
            self.rowcount = self.conn.delete_rowcount
        elif "insert into marts." in baixo:
            self.rowcount = self.conn.insert_rowcount
        return None

    def fetchone(self):
        return self._ultima

    def fetchall(self):
        return self._ultima or []

    def mogrify(self, sql, args=None):
        """`psycopg2.extras.execute_values` real chama `mogrify`.

        Sob o runner de stdlib o `execute_values` era falso e nunca chegava aqui;
        com o psycopg2 de verdade instalado, a ausencia deste metodo derrubava
        seis testes de publicacao. Devolve bytes, como o cursor real.
        """
        return str(sql).encode()

    def close(self):
        pass


class FakeConn:
    #: `execute_values` real le `cur.connection.encoding`.
    encoding = "UTF8"

    def __init__(self, respostas=None, delete_rowcount=0, insert_rowcount=0):
        self.executed: list[tuple] = []
        self.commits = 0
        self.rollbacks = 0
        self.respostas = respostas or {}
        self.delete_rowcount = delete_rowcount
        self.insert_rowcount = insert_rowcount
        self.autocommit = False

    def cursor(self):
        return FakeCursor(self, self.respostas)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _row(brand="barbours", item="MLB1", ref=date(2026, 9, 2), price="10.00"):
    return {
        "ref_date": ref, "marketplace": "ml", "brand": brand,
        "seller_id": 1, "item_id": item,
        "seller_sku": "BB1", "gtin": "7901128400051",
        "listing_title": "t", "permalink": "https://x/1",
        "advertised_price": Decimal(price), "original_price": None,
        "currency": "BRL", "listing_status": "active",
        "catalog_listing": False,
        # PMA-1A-R, F2 — dois tempos distintos. `price_captured_at` e' a captura
        # do PRECO (obrigatoria); `listing_metadata_updated_at` e' a alteracao
        # do CADASTRO (anulavel) e nunca representa hora de preco.
        "price_captured_at": datetime(2026, 9, 2, 6, 3, 58),
        "listing_metadata_updated_at": None,
    }


def _snapshot(rows, de=date(2026, 9, 2), ate=date(2026, 9, 2)):
    return s.SourceSnapshot(rows, de, ate, s.aggregates_from_rows(rows))


# ---------------------------------------------------------------------------
# 1. Nada conecta no import
# ---------------------------------------------------------------------------

def test_import_nao_conecta():
    """Importar o modulo nao abre conexao nem le variavel de ambiente.

    Prova por AST, nao por texto: percorre somente os nos de NIVEL DE MODULO e
    exige que nenhum deles seja uma chamada. Assim `connect`, `_get_neon_url`,
    `os.environ` e qualquer outro efeito colateral so podem existir dentro de
    funcao, que e' o que o gate pede.
    """
    arvore = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    permitidos = (
        ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
        ast.ClassDef, ast.Expr, ast.Assign, ast.AnnAssign, ast.If,
    )
    def _e_guard_de_entrypoint(no) -> bool:
        """`if __name__ == "__main__":` nao roda no import — nao conta."""
        if not isinstance(no, ast.If):
            return False
        teste = no.test
        return (
            isinstance(teste, ast.Compare)
            and isinstance(teste.left, ast.Name)
            and teste.left.id == "__name__"
        )

    for no in arvore.body:
        assert isinstance(no, permitidos), type(no).__name__
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # corpo de funcao/classe nao executa no import
        if _e_guard_de_entrypoint(no):
            continue
        # Sobram docstring, imports e constantes. A unica chamada tolerada e' a
        # construcao de dado inerte (`date(...)`, decorator `dataclass`).
        for filho in ast.walk(no):
            if isinstance(filho, ast.Call):
                nome = getattr(filho.func, "id", None) or getattr(filho.func, "attr", "")
                assert nome in ("date", "dataclass"), nome


# ---------------------------------------------------------------------------
# 2. Janela: teto D-1, timezone e recusas
# ---------------------------------------------------------------------------

def test_dia_corrente_e_recusado():
    erro = None
    try:
        s.validate_window(date(2026, 9, 1), HOJE, HOJE)
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "dia corrente" in erro


def test_dia_futuro_e_recusado():
    erro = None
    try:
        s.validate_window(date(2026, 9, 1), date(2026, 9, 10), HOJE)
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None


def test_teto_e_d_menos_1():
    de, ate = s.validate_window(date(2026, 9, 1), date(2026, 9, 2), HOJE)
    assert ate == date(2026, 9, 2) == HOJE - (HOJE - date(2026, 9, 2))
    assert ate < HOJE


def test_janela_invertida_e_recusada():
    erro = None
    try:
        s.validate_window(date(2026, 9, 2), date(2026, 8, 1), HOJE)
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "invertida" in erro


def test_janela_antes_do_primeiro_dado_e_recusada():
    erro = None
    try:
        s.validate_window(date(2026, 1, 1), date(2026, 9, 2), HOJE)
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None
    assert s.SOURCE_MIN_DATE == date(2026, 6, 20)


def test_incremental_termina_em_d_menos_1():
    de, ate = s.incremental_window(HOJE, 30)
    assert ate == date(2026, 9, 2)
    assert de == date(2026, 8, 4)
    assert (ate - de).days == 29


def test_incremental_respeita_o_piso_da_fonte():
    de, ate = s.incremental_window(date(2026, 6, 25), 30)
    assert de == s.SOURCE_MIN_DATE


def test_lookback_abaixo_do_piso_e_recusado():
    erro = None
    try:
        s.incremental_window(HOJE, 1)
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None


def test_timezone_e_sao_paulo():
    assert s.TZ_OPERACIONAL.key == "America/Sao_Paulo"


# ---------------------------------------------------------------------------
# 3. Fonte read-only e fotografia consistente
# ---------------------------------------------------------------------------

def test_snapshot_session_exige_repeatable_read_e_read_only():
    conn = FakeConn({"transaction_isolation": {
        "isolation": "read committed", "read_only": "on"}})
    erro = None
    try:
        s.assert_snapshot_session(conn.cursor())
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "repeatable read" in erro

    conn = FakeConn({"transaction_isolation": {
        "isolation": "repeatable read", "read_only": "off"}})
    erro = None
    try:
        s.assert_snapshot_session(conn.cursor())
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "read-only" in erro

    conn = FakeConn({"transaction_isolation": {
        "isolation": "repeatable read", "read_only": "on"}})
    assert s.assert_snapshot_session(conn.cursor())["read_only"] == "on"


def test_conexao_da_fonte_e_declarada_read_only_no_codigo():
    texto = MODULE_PATH.read_text(encoding="utf-8")
    trecho = texto[texto.index("def _datamart_snapshot"):]
    trecho = trecho[:trecho.index("def _neon_writable")]
    assert "readonly=True" in trecho
    assert 'isolation_level="REPEATABLE READ"' in trecho


def test_nao_ha_leitura_de_gmv_nem_unidades():
    """Preco anunciado nunca pode ser derivado de receita/quantidade."""
    sql = _sql_do_modulo()
    for proibido in ("gmv", "units_sold", "quantity", "unidades", "revenue",
                     "sub_total", "order_amount", "avg("):
        assert proibido not in sql, proibido
    # Nenhuma divisao no SQL da fonte: preco medio nao e' preco anunciado.
    assert "/" not in sql


def test_fonte_e_apenas_silver_de_preco_e_de_itens():
    sql = s.build_source_query() + s.build_source_aggregate_query()
    tabelas = set(re.findall(r"(?:silver|gold|raw|marts)\.[a-z_]+", sql))
    assert tabelas == {
        "silver.stg_ml_item_price_history", "silver.stg_ml_items",
    }, tabelas


def test_variations_vazia_nao_e_usada():
    sql = s.build_source_query()
    assert "stg_ml_item_variations" not in sql


# ---------------------------------------------------------------------------
# Gate PMA-2R — normalizacao de GTIN
# ---------------------------------------------------------------------------
# A primeira tentativa de carga falhou com `value too long for type character
# varying(14)`: 15 de 903 itens tem 26 DIGITOS no atributo GTIN do ML (dois
# EAN-13 concatenados), afetando 429 linhas da janela. O schema NAO estava
# errado — o CHECK `^[0-9]{8,14}$` reprovaria 26 digitos de qualquer forma. O
# defeito era a assimetria: a referencia classificava com `classify_gtin`, a
# observacao nao classificava nada.

def _linha_gtin(bruto, sku="BB1"):
    r = _row()
    del r["gtin"]
    r["gtin_raw"] = bruto
    r["seller_sku"] = sku
    return r


def test_ean8_ean12_ean13_sao_preservados():
    rows = [_linha_gtin("12345670"), _linha_gtin("012345678905"),
            _linha_gtin("7901128400051")]
    m = s.normalize_gtin_in_rows(rows)
    assert [r["gtin"] for r in rows] == ["12345670", "012345678905",
                                         "7901128400051"]
    assert m["gtin_valido"] == 3
    assert m["gtin_null_total"] == 0


def test_pontuacao_e_espaco_sao_normalizados():
    rows = [_linha_gtin(" 790-1128.400 051 ")]
    s.normalize_gtin_in_rows(rows)
    assert rows[0]["gtin"] == "7901128400051"


def test_valor_ausente_vira_nulo():
    rows = [_linha_gtin(None), _linha_gtin(""), _linha_gtin("   ")]
    m = s.normalize_gtin_in_rows(rows)
    assert all(r["gtin"] is None for r in rows)
    assert m[s.GTIN_KIND_ABSENT] == 3
    assert m["gtin_null_total"] == 3


def test_dun14_vira_nulo():
    rows = [_linha_gtin("79087907006940")]
    m = s.normalize_gtin_in_rows(rows)
    assert rows[0]["gtin"] is None
    assert m[s.GTIN_KIND_DUN14] == 1


def test_26_digitos_concatenados_viram_nulo():
    """O caso real que derrubou a primeira carga."""
    concatenado = "7901128400051" + "7901128400068"
    assert len(concatenado) == 26
    rows = [_linha_gtin(concatenado)]
    m = s.normalize_gtin_in_rows(rows)
    assert rows[0]["gtin"] is None
    assert m[s.GTIN_KIND_INVALID] == 1


def test_linha_com_26_digitos_e_preservada_com_sku():
    """A observacao de preco nao se perde: so o GTIN vira nulo."""
    rows = [_linha_gtin("7901128400051" + "7901128400068", sku="BB03038")]
    s.normalize_gtin_in_rows(rows)
    assert len(rows) == 1
    assert rows[0]["gtin"] is None
    # SKU intacto -> match secundario por SKU unico na marca continua possivel.
    assert rows[0]["seller_sku"] == "BB03038"
    assert rows[0]["advertised_price"] is not None
    assert rows[0]["price_captured_at"] is not None
    # E a linha passa a validacao.
    assert s.validate_rows(rows, date(2026, 9, 2), date(2026, 9, 2))["rows"] == 1


def test_nenhum_dos_dois_ean_concatenados_e_escolhido():
    """Escolher um seria adivinhar qual produto o anuncio representa."""
    a, b = "7901128400051", "7901128400068"
    rows = [_linha_gtin(a + b)]
    s.normalize_gtin_in_rows(rows)
    assert rows[0]["gtin"] not in (a, b)
    assert rows[0]["gtin"] is None


def test_valor_bruto_e_descartado_da_linha():
    """`gtin_raw` nao pode sobreviver: nao vai a staging, destino nem log."""
    rows = [_linha_gtin("79087907006940")]
    s.normalize_gtin_in_rows(rows)
    assert "gtin_raw" not in rows[0]
    assert "79087907006940" not in repr(rows[0])


def test_metricas_sao_agregadas_e_sem_codigo_bruto():
    rows = [_linha_gtin("7901128400051"), _linha_gtin(None),
            _linha_gtin("79087907006940"),
            _linha_gtin("7901128400051" + "7901128400068")]
    m = s.normalize_gtin_in_rows(rows)
    assert m[s.GTIN_KIND_CONSUMER] == 1
    assert m[s.GTIN_KIND_ABSENT] == 1
    assert m[s.GTIN_KIND_DUN14] == 1
    assert m[s.GTIN_KIND_INVALID] == 1
    assert m["gtin_null_total"] == 3
    assert m["gtin_valido"] == 1
    # Nenhum valor da metrica e' texto: sao contagens.
    assert all(isinstance(v, int) for v in m.values())
    for bruto in ("7901128400051", "79087907006940"):
        assert bruto not in repr(m)


def test_validate_rows_recusa_gtin_fora_do_dominio():
    """A trava que faltava: dominio, nao so formato.

    Uma regressao que deixe passar 14, 26 ou outro comprimento tem de falhar no
    DIAGNOSTICO, antes de qualquer transacao gravavel.
    """
    for ruim in ("79087907006940", "7901128400051" + "7901128400068",
                 "1234567", "123456789012345"):
        r = _row()
        r["gtin"] = ruim
        erro = None
        try:
            s.validate_rows([r], date(2026, 9, 2), date(2026, 9, 2))
        except s.SyncError as exc:
            erro = str(exc)
        assert erro is not None, ruim
        assert "digitos apos a normalizacao" in erro
        # A mensagem NAO ecoa o codigo.
        assert ruim not in erro


def test_validate_rows_recusa_gtin_nao_numerico():
    r = _row()
    r["gtin"] = "790112840005X"
    erro = None
    try:
        s.validate_rows([r], date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "nao numerico" in erro


def test_validate_rows_recusa_gtin_raw_residual():
    """Validar antes de normalizar tem de falhar alto, nao passar silencioso."""
    r = _linha_gtin("7901128400051")
    erro = None
    try:
        s.validate_rows([r], date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "normalize_gtin_in_rows" in erro


def test_gtin_nulo_e_aceito():
    r = _row()
    r["gtin"] = None
    assert s.validate_rows([r], date(2026, 9, 2), date(2026, 9, 2))["gtin_null"] == 1


def test_normalizacao_precede_validacao_no_codigo():
    """Prova de ORDEM em `read_source`, nao apenas convencao."""
    texto = MODULE_PATH.read_text(encoding="utf-8")
    corpo = texto[texto.index("def read_source"):]
    corpo = corpo[:corpo.index("return SourceSnapshot")]
    i_norm = corpo.index("normalize_gtin_in_rows(rows)")
    i_val = corpo.index("validate_rows(rows")
    assert i_norm < i_val, "normalizar tem de vir antes de validar"


def test_sql_da_fonte_entrega_bruto_nao_gtin_final():
    """O SELECT EXTERNO produz `gtin_raw`, nunca `gtin`.

    O alias interno do `LATERAL` continua `gtin` — e' escopo local da subconsulta
    e nao vira coluna do resultado. O que importa e' a coluna que chega ao
    Python: ela tem de ser a bruta, para forcar a classificacao.
    """
    sql = " ".join(s.build_source_query().split()).lower()
    assert "a.gtin as gtin_raw" in sql
    assert "a.gtin as gtin," not in sql
    # E o nome final `gtin` nao esta entre as colunas do resultado do SQL.
    externo = sql[:sql.index("from ")]
    assert "as gtin_raw" in externo
    assert "as gtin," not in externo


def test_regra_de_gtin_e_a_canonica_compartilhada():
    """Equivalencia com a referencia: uma regra, dois lados.

    Se alguem reimplementar a classificacao no sync, este teste reprova — foi a
    divergencia entre os dois lados que causou a falha da primeira carga.
    """
    from pipelines.pma import reference_contract as rc

    assert s.ALLOWED_GTIN_LENGTHS == rc.CONSUMER_EAN_LENGTHS
    assert set(s.GTIN_KINDS) == {"consumer_ean", "absent", "dun14", "invalid"}
    # E o sync usa a funcao canonica, nao uma copia.
    texto = MODULE_PATH.read_text(encoding="utf-8")
    assert "from pipelines.pma.reference_contract import classify_gtin" in texto
    assert "def classify_gtin" not in texto

    # Mesma entrada, mesma saida nos dois lados.
    for bruto in ("12345670", "012345678905", "7901128400051",
                  "79087907006940", "7901128400051" + "7901128400068",
                  None, "", "123"):
        rows = [_linha_gtin(bruto)]
        s.normalize_gtin_in_rows(rows)
        assert rows[0]["gtin"] == rc.classify_gtin(bruto).value, bruto


def test_normalizacao_nao_abre_conexao_nem_escreve():
    """A normalizacao e' pura: roda sobre linhas em memoria."""
    import ast

    texto = MODULE_PATH.read_text(encoding="utf-8")
    arvore = ast.parse(texto)
    alvo = next(n for n in arvore.body
                if isinstance(n, ast.FunctionDef)
                and n.name == "normalize_gtin_in_rows")
    for filho in ast.walk(alvo):
        if isinstance(filho, ast.Call):
            nome = getattr(filho.func, "id", None) or getattr(filho.func, "attr", "")
            assert nome in ("classify_gtin", "pop", "get", "sum", "items"), nome


def test_price_captured_at_vem_do_historico_de_preco():
    """F2: a captura do PRECO vem de `h.extracted_at`, nunca de `i.updated_at`."""
    sql = " ".join(s.build_source_query().split()).lower()
    assert "h.extracted_at as price_captured_at" in sql
    assert "i.updated_at as listing_metadata_updated_at" in sql
    # O nome antigo, semanticamente falso, nao pode voltar.
    assert "source_updated_at" not in sql
    assert "source_updated_at" not in s.BUSINESS_COLUMNS


def test_metadata_do_anuncio_nao_alimenta_o_tempo_do_preco():
    """`i.updated_at` NUNCA pode virar `price_captured_at`."""
    sql = " ".join(s.build_source_query().split()).lower()
    assert "i.updated_at as price_captured_at" not in sql
    assert "h.extracted_at as listing_metadata_updated_at" not in sql


def test_price_captured_at_nulo_e_recusado():
    ruim = _row()
    ruim["price_captured_at"] = None
    erro = None
    try:
        s.validate_rows([ruim], date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "price_captured_at" in erro


def test_metadata_do_anuncio_pode_ser_nula():
    bom = _row()
    bom["listing_metadata_updated_at"] = None
    assert s.validate_rows([bom], date(2026, 9, 2), date(2026, 9, 2))["rows"] == 1


# --- F10: marca nova ABORTA antes de qualquer escrita ----------------------

def test_consulta_de_marca_desconhecida_nao_filtra_pela_allowlist():
    """A consulta procura o que ficaria de FORA, logo usa `NOT (... = ANY(...))`."""
    sql = " ".join(s.build_unknown_brand_query().split()).lower()
    assert "not (h.brand = any(%(brands)s))" in sql
    assert "group by h.brand" in sql
    # E nenhuma marca literal: a allowlist vai por parametro.
    for marca in s.ALLOWED_BRANDS:
        assert f"'{marca}'" not in sql, marca


def test_marca_desconhecida_na_fonte_aborta_o_sync():
    conn = FakeConn({"not (h.brand = any": [{"brand": "novamarca", "linhas": 12}]})
    erro = None
    try:
        s.assert_no_unknown_brands(conn.cursor(), date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None
    assert "novamarca" in erro
    assert "ABORTA" in erro
    assert "nenhum DELETE" in erro


def test_marca_desconhecida_nao_deixa_nenhum_delete_acontecer():
    """Prova de ORDEM: a checagem roda na leitura, antes da transacao gravavel.

    `read_source` chama `assert_no_unknown_brands` como PRIMEIRA consulta depois
    de provar a sessao. Se ela levantar, `publish_window` nunca e' chamada e
    nenhum DELETE e' emitido — o destino fica intacto.
    """
    conn = FakeConn({
        "transaction_isolation": {"isolation": "repeatable read", "read_only": "on"},
        "not (h.brand = any": [{"brand": "novamarca", "linhas": 3}],
    })
    erro = None
    try:
        s.read_source(conn, date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "novamarca" in erro
    emitidos = [sql for sql, _ in conn.executed]
    assert not any(sql.lower().startswith("delete") for sql in emitidos)
    assert not any("insert" in sql.lower() for sql in emitidos)
    # A checagem de marca precede a leitura do agregado e do detalhe.
    assert any("not (h.brand = any" in sql.lower() for sql in emitidos)
    assert not any("count(distinct h.item_id)" in sql.lower() for sql in emitidos)


def test_fonte_sem_marca_nova_passa_pela_checagem():
    conn = FakeConn({"not (h.brand = any": []})
    s.assert_no_unknown_brands(conn.cursor(), date(2026, 9, 2), date(2026, 9, 2))


def test_allowlist_do_sync_e_a_do_ddl():
    """Trava 1 de 3. A concordancia com o contrato da API e' verificada em
    `apps/api/tests/test_monitoramento_preco_contract.py`, e a do CHECK da
    tabela em `pipelines/tests/test_pma_serving_ddl.py`. Aqui fica a do sync,
    sem importar `apps/api` — para que rodar so `pytest pipelines/tests`
    continue funcionando."""
    assert s.ALLOWED_BRANDS == ("barbours", "kokeshi", "lescent", "rituaria")
    assert "apice" not in s.ALLOWED_BRANDS
    assert "yenzah" not in s.ALLOWED_BRANDS


def test_status_vem_do_historico_diario_nao_da_dimensao():
    """`listing_status` precisa ser o de `ref_date`, nao o de hoje."""
    sql = " ".join(s.build_source_query().split()).lower()
    assert "h.status as listing_status" in sql
    # `i.status` e' o estado CORRENTE do anuncio e nao pode virar historico.
    assert "i.status" not in sql


def test_valores_vao_por_parametro_nomeado():
    """Data e marca chegam ao SQL como parametro, nunca interpoladas no texto."""
    sql = _sql_do_modulo()
    assert "%(date_from)s" in sql and "%(date_to)s" in sql
    assert "%(brands)s" in sql and "%(marketplace)s" in sql
    # Nenhuma data literal no SQL construido: se uma janela fosse interpolada,
    # apareceria aqui como AAAA-MM-DD.
    assert not re.search(r"\d{4}-\d{2}-\d{2}", sql), sql
    # E nenhuma marca literal: a allowlist vai por parametro.
    for marca in s.ALLOWED_BRANDS:
        assert f"'{marca}'" not in sql, marca


def test_todo_agregado_do_sql_tem_alias():
    """Sem `AS`, a chave do dict do RealDictCursor nao seria a esperada."""
    sql = s.build_source_aggregate_query()
    for agregado in re.finditer(r"\b(count|sum|min|max|coalesce)\s*\(", sql, re.I):
        resto = sql[agregado.start():]
        fim = resto.find("\n")
        linha = resto[:fim if fim > 0 else len(resto)]
        assert re.search(r"\bAS\s+[a-z_]+", linha, re.I), linha.strip()


# ---------------------------------------------------------------------------
# 4. Validacao de entrada
# ---------------------------------------------------------------------------

def test_chave_duplicada_no_detalhe_e_recusada():
    rows = [_row(item="MLB1"), _row(item="MLB1")]
    erro = None
    try:
        s.validate_rows(rows, date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "duplicada" in erro


def test_linha_fora_da_janela_e_recusada():
    rows = [_row(ref=date(2026, 8, 1))]
    erro = None
    try:
        s.validate_rows(rows, date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "fora da janela" in erro


def test_marca_fora_da_allowlist_e_recusada():
    rows = [_row(brand="apice")]
    erro = None
    try:
        s.validate_rows(rows, date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "allowlist" in erro
    assert "apice" not in s.ALLOWED_BRANDS
    assert "yenzah" not in s.ALLOWED_BRANDS


def test_nan_e_preco_negativo_sao_recusados():
    ruim = _row()
    ruim["advertised_price"] = Decimal("NaN")
    erro = None
    try:
        s.validate_rows([ruim], date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "NaN" in erro

    ruim = _row(price="-1.00")
    erro = None
    try:
        s.validate_rows([ruim], date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "negativo" in erro


def test_seller_sku_vazio_e_recusado_nulo_e_aceito():
    ruim = _row()
    ruim["seller_sku"] = "   "
    erro = None
    try:
        s.validate_rows([ruim], date(2026, 9, 2), date(2026, 9, 2))
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None

    bom = _row()
    bom["seller_sku"] = None
    assert s.validate_rows([bom], date(2026, 9, 2), date(2026, 9, 2))["seller_sku_null"] == 1


def test_excesso_de_linhas_e_recusado_nao_truncado():
    original = s.MAX_ROWS_PER_WINDOW
    try:
        s.MAX_ROWS_PER_WINDOW = 1
        erro = None
        try:
            s.validate_rows([_row(item="A"), _row(item="B")],
                            date(2026, 9, 2), date(2026, 9, 2))
        except s.SyncError as exc:
            erro = str(exc)
        assert erro is not None and "truncado" in erro
    finally:
        s.MAX_ROWS_PER_WINDOW = original


def test_reconciliacao_agregada_detecta_divergencia():
    rows = [_row(item="A"), _row(item="B")]
    recomputado = s.aggregates_from_rows(rows)
    fonte = dict(recomputado)
    fonte["row_count"] = 3
    problemas = s.compare_aggregates(fonte, recomputado)
    assert problemas and "row_count" in problemas[0]
    assert not s.compare_aggregates(recomputado, recomputado)


def test_original_price_nulo_nao_conta_como_zero():
    rows = [_row(item="A"), _row(item="B")]
    rows[0]["original_price"] = Decimal("20.00")
    agg = s.aggregates_from_rows(rows)
    assert agg["original_price_not_null"] == 1


# ---------------------------------------------------------------------------
# 5. Publicacao: lock, staging, DELETE da janela, EXCEPT, rollback
# ---------------------------------------------------------------------------

def _respostas_ok(n):
    return {
        "count(*) as n from pg_temp": {"n": n},
        "where ref_date < %(date_from)s or ref_date > %(date_to)s": {"n": 0},
        "except": {"n": 0},
    }


def _publicar(rows, respostas=None, delete=5):
    snap = _snapshot(rows)
    conn = FakeConn(
        respostas or _respostas_ok(len(rows)),
        delete_rowcount=delete, insert_rowcount=len(rows),
    )
    return conn, s.publish_window(conn, snap, "run-1")


def test_publicacao_toma_advisory_lock_proprio():
    conn, _ = _publicar([_row()])
    locks = [p for sql, p in conn.executed if "pg_advisory_xact_lock" in sql]
    assert locks == [(s.ADVISORY_LOCK_KEY,)]
    # Chave distinta das outras frentes.
    assert s.ADVISORY_LOCK_KEY not in (907_120_007, 908_120_008, 912_120_012, 913_120_013)


def test_staging_e_temporaria_com_on_commit_drop():
    conn, _ = _publicar([_row()])
    criacoes = [sql for sql, _ in conn.executed if "create temp table" in sql.lower()]
    assert len(criacoes) == 1
    assert "on commit drop" in criacoes[0].lower()
    # A staging e' sempre referenciada com o schema `pg_temp`, nunca sem
    # qualificacao: sem isso um objeto permanente de mesmo nome poderia ser lido.
    leituras = [sql for sql, _ in conn.executed
                if "from" in sql.lower() and s.STAGING_TABLE in sql]
    assert leituras
    for sql in leituras:
        assert f"pg_temp.{s.STAGING_TABLE}" in sql, sql


def test_delete_e_restrito_a_janela():
    conn, _ = _publicar([_row()])
    deletes = [(sql, p) for sql, p in conn.executed if sql.lower().startswith("delete")]
    assert len(deletes) == 1
    sql, params = deletes[0]
    assert "where ref_date between %(date_from)s and %(date_to)s" in sql.lower()
    assert "marketplace = %(marketplace)s" in sql.lower()
    assert params["date_from"] == date(2026, 9, 2)
    assert params["date_to"] == date(2026, 9, 2)


def test_nao_existe_truncate_no_codigo():
    """`TRUNCATE` nao pode ser executado. Varre CODIGO, nao docstring."""
    assert "truncate" not in _codigo_executavel()


def test_todo_delete_executado_tem_where_de_janela():
    """Prova comportamental: o unico DELETE emitido carrega WHERE de janela."""
    conn, _ = _publicar([_row()])
    deletes = [sql for sql, _ in conn.executed if sql.lower().startswith("delete")]
    assert len(deletes) == 1
    assert "where" in deletes[0].lower()
    # E nenhum DELETE/TRUNCATE sem WHERE em nenhum statement emitido.
    for sql, _ in conn.executed:
        baixo = sql.lower()
        assert "truncate" not in baixo, sql
        if baixo.startswith("delete"):
            assert "where" in baixo, sql


def test_insert_usa_lista_explicita_de_colunas():
    conn, _ = _publicar([_row()])
    inserts = [sql for sql, _ in conn.executed
               if "insert into marts." in sql.lower()]
    assert len(inserts) == 1
    for coluna in s.BUSINESS_COLUMNS + s.AUDIT_COLUMNS:
        assert coluna in inserts[0], coluna
    assert "select *" not in inserts[0].lower()


def test_except_bidirecional_e_executado_nos_dois_sentidos():
    conn, resultado = _publicar([_row()])
    excepts = [sql for sql, _ in conn.executed if "except" in sql.lower()]
    assert len(excepts) == 2
    assert resultado["checks"]["except_both_ways"] == (0, 0)


def test_except_divergente_faz_rollback_e_levanta():
    respostas = _respostas_ok(1)
    respostas["except"] = {"n": 3}
    erro = None
    conn = FakeConn(respostas, delete_rowcount=1, insert_rowcount=1)
    try:
        s.publish_window(conn, _snapshot([_row()]), "run-1")
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "EXCEPT bidirecional" in erro
    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_staging_divergente_faz_rollback_antes_do_commit():
    respostas = _respostas_ok(99)
    conn = FakeConn(respostas, delete_rowcount=1, insert_rowcount=1)
    erro = None
    try:
        s.publish_window(conn, _snapshot([_row()]), "run-1")
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None
    assert conn.rollbacks == 1 and conn.commits == 0


def test_insert_com_contagem_divergente_faz_rollback():
    conn = FakeConn(_respostas_ok(1), delete_rowcount=1, insert_rowcount=7)
    erro = None
    try:
        s.publish_window(conn, _snapshot([_row()]), "run-1")
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "publicou" in erro
    assert conn.rollbacks == 1 and conn.commits == 0


def test_publicacao_bem_sucedida_commita_uma_vez():
    conn, resultado = _publicar([_row()])
    assert conn.commits == 1 and conn.rollbacks == 0
    assert resultado["published"] == 1


def test_source_run_id_vai_em_toda_linha():
    assert "source_run_id" in s.AUDIT_COLUMNS


def test_zero_retry():
    """Nenhum mecanismo de retentativa no CODIGO (docstring pode explicar)."""
    codigo = _codigo_executavel()
    for palavra in ("retry", "tenacity", "backoff", "max_attempts", "reconnect",
                    "sleep"):
        assert palavra not in codigo, palavra
    # E nenhum `while` no modulo: laco de repeticao seria o retry implicito.
    arvore = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(arvore) if isinstance(n, ast.While)]


# ---------------------------------------------------------------------------
# 6. `--apply` obrigatorio; dry-run nao escreve
# ---------------------------------------------------------------------------

def test_apply_e_flag_e_default_e_diagnostico():
    args = s.build_parser().parse_args([])
    assert args.apply is False
    args = s.build_parser().parse_args(["--apply"])
    assert args.apply is True


def test_dry_run_nunca_chama_publicacao():
    """`main` sem `--apply` roteia para `run_diagnostic`, que nao publica."""
    texto = MODULE_PATH.read_text(encoding="utf-8")
    trecho = texto[texto.index("def main("):]
    assert "run_apply(de, ate, run_id) if args.apply else run_diagnostic(de, ate)" in trecho
    diag = texto[texto.index("def run_diagnostic"):texto.index("def run_apply")]
    for verbo in ("publish_window", "INSERT", "DELETE", "_neon_writable"):
        assert verbo not in diag, verbo
    assert "_neon_readonly" in diag


def test_diagnostico_usa_conexao_read_only_no_destino():
    texto = MODULE_PATH.read_text(encoding="utf-8")
    trecho = texto[texto.index("def _neon_readonly"):texto.index("def default_run_id")]
    assert "readonly=True" in trecho


def test_date_from_e_date_to_sao_usados_juntos():
    p = s.build_parser()
    erro = None
    try:
        s.resolve_window(p.parse_args(["--date-from", "2026-09-01"]), HOJE)
    except s.SyncError as exc:
        erro = str(exc)
    assert erro is not None and "juntos" in erro


# ---------------------------------------------------------------------------
# 7. Mensagens sanitizadas
# ---------------------------------------------------------------------------

def test_erro_de_conexao_nao_vaza_topologia():
    nativo = Exception(
        'connection to server at "db-privado.interno" (10.20.30.40), port 5432 '
        'failed: timeout expired'
    )
    limpo = s.sanitize_error_message(nativo)
    for vazamento in ("db-privado", "10.20.30.40", "5432", "interno"):
        assert vazamento not in limpo, vazamento


def test_erro_com_credencial_e_redigido():
    nativo = Exception("postgresql://usuario:senha_secreta@host:5432/base falhou")
    limpo = s.sanitize_error_message(nativo)
    assert "senha_secreta" not in limpo


def test_run_id_e_sanitizado():
    assert s.sanitize_run_id("a b;drop--'\"") == "a_b_drop--__"
    assert len(s.sanitize_run_id("x" * 200)) == 64


def test_cobertura_declarada_e_advertised_only():
    texto = MODULE_PATH.read_text(encoding="utf-8")
    assert "advertised_only" in texto
    # As quatro colunas de checkout nao existem no destino.
    for coluna in ("shipping_amount", "seller_coupon_amount",
                   "platform_subsidy_amount", "checkout_price"):
        assert coluna not in s.BUSINESS_COLUMNS, coluna
