"""
Testes de completude da Fase A de apps/api/etl/load_shopee_products.py
(endurecimento final, 2026-07-04): _load_brand/_prepare_all_brands não
podem mais concluir com uma marca, arquivo ou coluna obrigatória
silenciosamente omitidos. Antes, _load_brand logava e retornava None
(marca sem arquivo, arquivo ilegível, coluna obrigatória ausente) e
_prepare_all_brands só dava `continue` — permitindo que Fase B (banco)
começasse com entrada parcial.

Todos os testes de fluxo real usam engine/conexão FAKE (nunca um banco
real) e provam comportamento executando main()/_prepare_all_brands de
verdade — não apenas inspecionando o texto-fonte.
"""
from __future__ import annotations

import traceback

import openpyxl
import pandas as pd
import pytest

from etl import load_shopee_products as mod

ORDERS_HEADER = [
    "Data de criação do pedido", "Nº de referência do SKU principal",
    "Nome do Produto", "Nome da variação", "Quantidade",
    "Subtotal do produto", "Status do pedido", "Nome de usuário (comprador)",
]


def _write_valid_order_xlsx(path, buyer="u1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(ORDERS_HEADER)
    ws.append(["2026-01-05 10:00", "SKU1", "Produto A", None, "2", "100.00", "Concluído", buyer])
    wb.save(path)


def _write_order_xlsx_missing_column(path, missing_column):
    header = [c for c in ORDERS_HEADER if c != missing_column]
    row_by_header = {
        "Data de criação do pedido": "2026-01-05 10:00",
        "Nº de referência do SKU principal": "SKU1",
        "Nome do Produto": "Produto A",
        "Nome da variação": None,
        "Quantidade": "2",
        "Subtotal do produto": "100.00",
        "Status do pedido": "Concluído",
        "Nome de usuário (comprador)": "u1",
    }
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    ws.append([row_by_header[h] for h in header])
    wb.save(path)


def _write_corrupted_xlsx(path):
    """Não é um xlsx válido — openpyxl/pandas devem falhar ao abrir."""
    path.write_bytes(b"isto nao e um arquivo xlsx valido, apenas bytes soltos")


class _FakeConnection:
    def __init__(self, calls):
        self._calls = calls

    def execute(self, stmt, params=None):
        self._calls.append((str(stmt), params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def begin(self):
        return _FakeConnection(self.calls)


def _patch_db_tracking(monkeypatch):
    """Substitui _get_local_pg_url/create_engine por stubs que só
    registram chamadas — usados para provar que a Fase B nunca começa."""
    pg_url_calls = []
    engine_calls = []
    monkeypatch.setattr(mod, "_get_local_pg_url", lambda: pg_url_calls.append(1))
    monkeypatch.setattr(mod, "create_engine", lambda *a, **k: engine_calls.append((a, k)))
    return pg_url_calls, engine_calls


# ---------------------------------------------------------------------------
# _load_brand — fail-fast para completude (nunca retorna None)
# ---------------------------------------------------------------------------

def test_load_brand_sem_arquivo_levanta_erro_controlado(tmp_path, monkeypatch):
    (tmp_path / "apice").mkdir()  # pasta existe, mas sem nenhum Order.*.xlsx
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    assert "apice" in str(excinfo.value)


def test_load_brand_arquivo_corrompido_levanta_erro_sanitizado(tmp_path, monkeypatch):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_corrupted_xlsx(brand_dir / "Order.all.corrompido.xlsx")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    message = str(excinfo.value)
    assert "apice" in message
    assert "Order.all.corrompido.xlsx" in message


def test_load_brand_dois_arquivos_um_valido_um_corrompido_falha_por_completo(tmp_path, monkeypatch):
    """Cenário explícito pedido: um arquivo válido + um corrompido na
    mesma marca — a execução tem que falhar por completo, nunca carregar
    silenciosamente só o válido."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_valid_order_xlsx(brand_dir / "Order.all.A_valido.xlsx")
    _write_corrupted_xlsx(brand_dir / "Order.all.B_corrompido.xlsx")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    assert "Order.all.B_corrompido.xlsx" in str(excinfo.value)


@pytest.mark.parametrize("missing_column", ["Quantidade", "Subtotal do produto", "Data de criação do pedido", "Nome do Produto", "Status do pedido"])
def test_load_brand_coluna_obrigatoria_ausente_levanta_erro(tmp_path, monkeypatch, missing_column):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_order_xlsx_missing_column(brand_dir / "Order.all.xlsx", missing_column)
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    assert "apice" in str(excinfo.value)


@pytest.mark.parametrize("missing_column", ["Nº de referência do SKU principal", "Nome da variação", "Nome de usuário (comprador)"])
def test_load_brand_coluna_opcional_ausente_nao_levanta_erro(tmp_path, monkeypatch, missing_column):
    """sku_ref/variation_name/buyer_username continuam opcionais —
    contrato inalterado."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_order_xlsx_missing_column(brand_dir / "Order.all.xlsx", missing_column)
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    df = mod._load_brand("apice")

    assert df is not None
    assert len(df) == 1


@pytest.mark.parametrize("missing_column", ["Quantidade", "Subtotal do produto"])
def test_load_brand_arquivo_incompleto_nao_e_mascarado_por_arquivo_valido(tmp_path, monkeypatch, missing_column):
    """Regressão específica: schema era validado só DEPOIS do pd.concat.
    Arquivo A tinha a coluna, arquivo B não tinha — depois do concat a
    coluna existia globalmente (vinda de A) e as linhas de B viravam
    NaN/None, que _clean_int/_clean_numeric tratam como ausência
    legítima (0) em vez de como B estando incompleto. Agora o schema é
    validado POR ARQUIVO, antes de qualquer concat — B tem que ser
    rejeitado mesmo com A perfeitamente válido na mesma marca."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_valid_order_xlsx(brand_dir / "Order.all.A_valido.xlsx")
    _write_order_xlsx_missing_column(brand_dir / "Order.all.B_incompleto.xlsx", missing_column)
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    message = str(excinfo.value)
    assert "apice" in message
    assert "Order.all.B_incompleto.xlsx" in message
    assert mod.COL_MAP[missing_column] in message  # mensagem usa o nome interno (ex.: "qty"), não o header XLSX


@pytest.mark.parametrize("missing_column", ["Quantidade", "Subtotal do produto"])
def test_load_brand_arquivo_incompleto_detectado_mesmo_processado_primeiro(tmp_path, monkeypatch, missing_column):
    """Ordem inversa do teste acima: o arquivo INCOMPLETO ordena/é
    processado ANTES do válido (nome começa com "A", o válido com "B") —
    confirma que a validação por arquivo não depende de qual arquivo
    _find_xlsx/sorted() processa primeiro; o arquivo incompleto tem que
    ser rejeitado de qualquer forma, nunca "salvo" por um arquivo válido
    processado depois dele."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_order_xlsx_missing_column(brand_dir / "Order.all.A_incompleto.xlsx", missing_column)
    _write_valid_order_xlsx(brand_dir / "Order.all.B_valido.xlsx")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    message = str(excinfo.value)
    assert "apice" in message
    assert "Order.all.A_incompleto.xlsx" in message
    assert mod.COL_MAP[missing_column] in message  # mensagem usa o nome interno (ex.: "qty"), não o header XLSX


def test_load_brand_nunca_retorna_none(tmp_path, monkeypatch):
    """Confirma a mudança de assinatura: todo caminho de sucesso devolve
    um DataFrame; todo caminho de falha levanta, nunca retorna None."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_valid_order_xlsx(brand_dir / "Order.all.xlsx")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    df = mod._load_brand("apice")

    assert df is not None
    assert isinstance(df, pd.DataFrame)


# ---------------------------------------------------------------------------
# _prepare_all_brands / main() — fluxo real: create_engine nunca chamado
# quando a Fase A está incompleta, para qualquer um dos motivos.
# ---------------------------------------------------------------------------

def test_main_sem_arquivos_para_uma_marca_nao_chama_create_engine(tmp_path, monkeypatch):
    (tmp_path / "apice").mkdir()  # sem nenhum arquivo Order
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", ["apice"])
    pg_url_calls, engine_calls = _patch_db_tracking(monkeypatch)

    with pytest.raises(mod.ShopeeProductInputError):
        mod.main()

    assert pg_url_calls == []
    assert engine_calls == []


def test_main_arquivo_ilegivel_nao_chama_create_engine(tmp_path, monkeypatch):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_corrupted_xlsx(brand_dir / "Order.all.xlsx")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", ["apice"])
    pg_url_calls, engine_calls = _patch_db_tracking(monkeypatch)

    with pytest.raises(mod.ShopeeProductInputError):
        mod.main()

    assert pg_url_calls == []
    assert engine_calls == []


def test_main_dois_arquivos_um_valido_um_corrompido_nao_carrega_so_o_valido(tmp_path, monkeypatch):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_valid_order_xlsx(brand_dir / "Order.all.A_valido.xlsx")
    _write_corrupted_xlsx(brand_dir / "Order.all.B_corrompido.xlsx")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", ["apice"])
    pg_url_calls, engine_calls = _patch_db_tracking(monkeypatch)

    with pytest.raises(mod.ShopeeProductInputError):
        mod.main()

    assert pg_url_calls == []
    assert engine_calls == []


@pytest.mark.parametrize("missing_column", ["Quantidade", "Subtotal do produto"])
def test_main_arquivo_incompleto_mascarado_por_valido_nao_chama_create_engine(tmp_path, monkeypatch, missing_column):
    """Fluxo real de main() para a regressão específica desta correção:
    um arquivo válido + um arquivo sem coluna obrigatória na MESMA marca
    — create_engine nunca pode ser chamado, mesmo o arquivo válido
    existindo ao lado do incompleto."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_valid_order_xlsx(brand_dir / "Order.all.A_valido.xlsx")
    _write_order_xlsx_missing_column(brand_dir / "Order.all.B_incompleto.xlsx", missing_column)
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", ["apice"])
    pg_url_calls, engine_calls = _patch_db_tracking(monkeypatch)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod.main()

    assert "Order.all.B_incompleto.xlsx" in str(excinfo.value)
    assert pg_url_calls == []
    assert engine_calls == []


def test_main_falta_quantidade_nao_chama_create_engine(tmp_path, monkeypatch):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_order_xlsx_missing_column(brand_dir / "Order.all.xlsx", "Quantidade")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", ["apice"])
    pg_url_calls, engine_calls = _patch_db_tracking(monkeypatch)

    with pytest.raises(mod.ShopeeProductInputError):
        mod.main()

    assert pg_url_calls == []
    assert engine_calls == []


def test_main_falta_subtotal_nao_chama_create_engine(tmp_path, monkeypatch):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_order_xlsx_missing_column(brand_dir / "Order.all.xlsx", "Subtotal do produto")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", ["apice"])
    pg_url_calls, engine_calls = _patch_db_tracking(monkeypatch)

    with pytest.raises(mod.ShopeeProductInputError):
        mod.main()

    assert pg_url_calls == []
    assert engine_calls == []


def test_main_load_brand_retorna_none_inesperado_nao_chama_create_engine(tmp_path, monkeypatch):
    """Cobre a segunda barreira defensiva em _prepare_all_brands: mesmo
    que _load_brand (hipoteticamente, por uma regressão futura) volte a
    devolver None em vez de levantar, main() ainda assim nunca chega à
    Fase B."""
    monkeypatch.setattr(mod, "BRANDS", ["apice"])
    monkeypatch.setattr(mod, "_load_brand", lambda brand: None)
    pg_url_calls, engine_calls = _patch_db_tracking(monkeypatch)

    with pytest.raises(mod.ShopeeProductInputError):
        mod.main()

    assert pg_url_calls == []
    assert engine_calls == []


def test_main_quatro_de_cinco_marcas_preparadas_nao_chama_create_engine(tmp_path, monkeypatch):
    """5 marcas configuradas, 4 com dados válidos e 1 sem nenhum arquivo
    — a execução tem que falhar por completo (nunca gravar as 4 válidas
    silenciosamente omitindo a 5ª)."""
    brands = ["apice", "barbours", "kokeshi", "lescent", "rituaria"]
    for brand in brands[:-1]:
        brand_dir = tmp_path / brand
        brand_dir.mkdir()
        _write_valid_order_xlsx(brand_dir / "Order.all.xlsx")
    (tmp_path / brands[-1]).mkdir()  # rituaria: pasta existe, sem arquivo

    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", brands)
    pg_url_calls, engine_calls = _patch_db_tracking(monkeypatch)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod.main()

    assert "rituaria" in str(excinfo.value)
    assert pg_url_calls == []
    assert engine_calls == []


def test_main_todas_as_marcas_validas_ainda_chama_create_engine(tmp_path, monkeypatch):
    """Caminho feliz (contraste): com todas as marcas configuradas
    válidas, a Fase B roda normalmente — confirma que o endurecimento não
    quebrou o fluxo de sucesso."""
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_valid_order_xlsx(brand_dir / "Order.all.xlsx")

    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", ["apice"])
    monkeypatch.setattr(mod, "_get_local_pg_url", lambda: "postgresql://u:p@localhost/db")
    fake_engine = _FakeEngine()
    monkeypatch.setattr(mod, "create_engine", lambda *a, **k: fake_engine)

    mod.main()

    assert any("CREATE TABLE" in c[0] for c in fake_engine.calls)
    assert any("INSERT INTO" in c[0] for c in fake_engine.calls)


# ---------------------------------------------------------------------------
# Sanitização: mensagem, traceback completo, __cause__, __context__
# ---------------------------------------------------------------------------

def test_load_brand_arquivo_corrompido_nao_encadeia_cause_nem_context(tmp_path, monkeypatch):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_corrupted_xlsx(brand_dir / "Order.all.xlsx")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    exc = excinfo.value
    assert exc.__cause__ is None
    assert exc.__context__ is None


def test_load_brand_arquivo_corrompido_traceback_formatado_sem_conteudo_bruto(tmp_path, monkeypatch):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    conteudo_bruto = b"BYTES_FICTICIOS_SENSIVEIS_NUNCA_DEVEM_VAZAR"
    (brand_dir / "Order.all.xlsx").write_bytes(conteudo_bruto)
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    exc = excinfo.value
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert conteudo_bruto.decode() not in formatted


def test_load_brand_coluna_ausente_nao_encadeia_cause_nem_context(tmp_path, monkeypatch):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _write_order_xlsx_missing_column(brand_dir / "Order.all.xlsx", "Subtotal do produto")
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._load_brand("apice")

    exc = excinfo.value
    assert exc.__cause__ is None
    assert exc.__context__ is None
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert "u1" not in formatted  # buyer da fixture nunca aparece


def test_prepare_all_brands_erro_nao_encadeia_cause_nem_context(tmp_path, monkeypatch):
    (tmp_path / "apice").mkdir()
    monkeypatch.setattr(mod, "SHOPEE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "BRANDS", ["apice"])

    with pytest.raises(mod.ShopeeProductInputError) as excinfo:
        mod._prepare_all_brands()

    exc = excinfo.value
    assert exc.__cause__ is None
    assert exc.__context__ is None
