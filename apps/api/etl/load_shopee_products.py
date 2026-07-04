"""
ETL: carrega ficheiros XLSX de pedidos Shopee em marts.fact_shopee_product_monthly.

Uso:
    cd apps/api
    python -m etl.load_shopee_products
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

# Este loader so' deve escrever no PostgreSQL LOCAL (nunca no Neon nem no
# Data Mart/RDS de producao) — exige LOCAL_PG_URL explicitamente, sem
# fallback com credencial hardcoded, e restringe o host a localhost. A
# resolucao e' LAZY (so' dentro de main(), nunca no import do modulo):
# outros scripts (reconcile_bug8_canceled_only.py, monitor_bug8_invariants.py,
# fix_shopee_product_dates.py, diagnose_bug8_neon.py) importam so' as
# funcoes puras deste arquivo (BRANDS, DDL, _aggregate, _load_brand) sem
# precisar de nenhuma conexao — um _get_local_pg_url() eager no topo do
# modulo quebraria esses imports sempre que a variavel nao estivesse
# definida no ambiente de quem so' quer reaproveitar a logica pura.
_ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _sanitize_url(url: str) -> str:
    if not url:
        return "(nao configurado)"
    p = urlsplit(url)
    host = p.hostname or "?"
    port = p.port if p.port is not None else "?"
    db = p.path.lstrip("/") or "?"
    return f"{host}:{port}/{db}"


def _get_local_pg_url() -> str:
    url = os.environ.get("LOCAL_PG_URL", "")
    if not url:
        raise RuntimeError(
            "LOCAL_PG_URL nao definido. Este loader escreve exclusivamente no "
            "PostgreSQL local — a variavel e' exigida explicitamente, sem "
            "fallback com credencial hardcoded, para nunca escrever num banco "
            "nao pretendido (Neon/Data Mart)."
        )
    host = (urlsplit(url).hostname or "").lower()
    if host not in _ALLOWED_LOCAL_HOSTS:
        raise RuntimeError(
            f"LOCAL_PG_URL aponta para um host nao permitido ({_sanitize_url(url)}). "
            f"So' localhost/127.0.0.1/::1 sao aceitos — este loader nunca deve "
            f"escrever num host remoto (Neon/Data Mart)."
        )
    return url


SHOPEE_ROOT = Path(r"C:\Users\Notebook\Desktop\mktplace\shopee")
BRANDS = ["apice", "barbours", "kokeshi", "lescent", "rituaria"]

# Mapeamento colunas XLSX → nomes internos
COL_MAP = {
    "Data de criação do pedido": "order_date",
    "Nº de referência do SKU principal": "sku_ref",
    "Nome do Produto": "product_name",
    "Nome da variação": "variation_name",
    "Quantidade": "qty",
    "Subtotal do produto": "subtotal",
    "Status do pedido": "status",
    "Nome de usuário (comprador)": "buyer_username",
}

DDL = """
CREATE SCHEMA IF NOT EXISTS marts;

CREATE TABLE IF NOT EXISTS marts.fact_shopee_product_monthly (
    id               SERIAL PRIMARY KEY,
    ref_month        DATE NOT NULL,
    brand            VARCHAR(50) NOT NULL,
    sku_ref          VARCHAR(100),
    sku_ref_key      VARCHAR(100) NOT NULL DEFAULT '',
    product_name     VARCHAR(500) NOT NULL,
    variation_name   VARCHAR(200),
    gmv              NUMERIC(18,2) DEFAULT 0,
    units_sold       BIGINT DEFAULT 0,
    completed_orders BIGINT DEFAULT 0,
    canceled_orders  BIGINT DEFAULT 0,
    cancel_rate_pct  NUMERIC(8,4),
    unique_buyers    BIGINT DEFAULT 0,
    avg_price        NUMERIC(14,2),
    UNIQUE (ref_month, brand, sku_ref_key, product_name)
);
"""

UPSERT_SQL = """
INSERT INTO marts.fact_shopee_product_monthly
    (ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
     gmv, units_sold, completed_orders, canceled_orders,
     cancel_rate_pct, unique_buyers, avg_price)
VALUES
    (:ref_month, :brand, :sku_ref, :sku_ref_key, :product_name, :variation_name,
     :gmv, :units_sold, :completed_orders, :canceled_orders,
     :cancel_rate_pct, :unique_buyers, :avg_price)
ON CONFLICT (ref_month, brand, sku_ref_key, product_name)
DO UPDATE SET
    sku_ref          = EXCLUDED.sku_ref,
    variation_name   = EXCLUDED.variation_name,
    gmv              = EXCLUDED.gmv,
    units_sold       = EXCLUDED.units_sold,
    completed_orders = EXCLUDED.completed_orders,
    canceled_orders  = EXCLUDED.canceled_orders,
    cancel_rate_pct  = EXCLUDED.cancel_rate_pct,
    unique_buyers    = EXCLUDED.unique_buyers,
    avg_price        = EXCLUDED.avg_price
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_xlsx(brand_dir: Path) -> list[Path]:
    """Devolve todos os XLSX com 'order' no nome (case-insensitive)."""
    return [
        p for p in brand_dir.glob("*.xlsx")
        if re.search(r"order", p.name, re.IGNORECASE)
    ]


_EMPTY_NUMERIC_TOKENS = {"", "-", "N/A", "NA", "NULL", "NONE"}


class ShopeeNumericParseError(ValueError):
    """Valor numérico Shopee não vazio que não pôde ser interpretado.

    Implementação LOCAL e independente do parser canônico em
    pipelines/connectors/shopee/_numeric.py::parse_brl_float — mesmo
    contrato de formatos (ver docstring de _parse_brl_float abaixo e
    apps/api/etl/tests/test_load_shopee_products_numeric.py), mas
    deliberadamente duplicada em vez de importada:

    `apps/api` é empacotado e implantado de forma independente
    (pyproject.toml próprio em apps/api/, .venv próprio, sem `pipelines`
    nas dependências) e é executado com cwd=apps/api (ver docstring do
    módulo). Confirmado empiricamente nesta sessão: `import pipelines`
    levanta ModuleNotFoundError no .venv real de apps/api quando o cwd é
    apps/api — pipelines nunca é instalado nesse ambiente. Um
    `sys.path.insert()` para alcançá-lo seria, além de frágil, a direção
    OPOSTA do único precedente já existente no repositório
    (pipelines/reconciliation/*.py insere apps/api no sys.path para
    reaproveitar ESTE arquivo — nunca o contrário). A mensagem desta
    exceção nunca contém o valor bruto da célula (nem repr, nem conteúdo
    original) — só uma descrição genérica do problema; contexto de
    localização (arquivo/linha/coluna/marca) é responsabilidade do
    chamador (_clean_numeric).
    """


class ShopeeProductInputError(ValueError):
    """Entrada de uma marca incompleta ou ilegível: nenhum arquivo Order
    encontrado, falha ao ler um arquivo XLSX, ou coluna obrigatória
    ausente (order_date/product_name/status/qty/subtotal).

    Levantada em vez de logar-e-pular (comportamento anterior, que
    permitia _prepare_all_brands concluir e a Fase B começar com uma
    marca ou arquivo silenciosamente omitido). Qualquer entrada
    incompleta interrompe TODA a Fase A — nenhuma marca parcial chega a
    _write_prepared_brands.

    A mensagem contém apenas marca/arquivo/coluna (o que estiver
    disponível para o caso específico) — nunca conteúdo de célula, nunca
    a exceção original de pd.read_excel (que pode conter detalhes
    internos do arquivo/planilha). Nunca encadeada: __cause__ e
    __context__ sempre None, mesmo padrão de ShopeeNumericParseError."""


def _parse_brl_float(val: object) -> float | None:
    """Mesmo contrato de pipelines/connectors/shopee/_numeric.py::parse_brl_float:

    - None/vazio/"-"/"N/A"/"NULL"/"NONE" (case-insensitive) -> None (sem
      valor; o chamador decide a contribuição, aqui 0.0).
    - "1234.56" (ponto decimal, formato real confirmado nos exports) e
      "1234,56"/"1.234,56" (BR, vírgula decimal, com ou sem separador de
      milhar) -> interpretados corretamente.
    - "R$", espaços e NBSP são removidos antes do parsing.
    - Formato US ("1,234.56", vírgula de milhar + ponto decimal) ->
      ShopeeNumericParseError — decisão posicional (último "," depois do
      último "." = BR aceito; antes = US/ambíguo rejeitado), nenhuma
      evidência desse formato nos exports atuais.
    - float nativo NaN -> None (AUSÊNCIA, não erro). Divergência
      deliberada e documentada em relação ao parser canônico de
      pipelines/connectors/shopee/_numeric.py (que rejeita NaN nativo):
      lá, o valor vem direto de célula openpyxl, onde um NaN só
      apareceria por uma anomalia real. Aqui, o valor vem de
      `pd.read_excel(..., dtype=str)`, e foi confirmado empiricamente
      nesta sessão que o PRÓPRIO pandas converte célula vazia E texto
      reconhecido como "não disponível" (ex.: a string literal "NaN",
      "NA", "N/A", "null") para o mesmo sentinela float NaN ANTES desta
      função ver o valor — não há como distinguir os dois casos depois
      do read_excel, e ambos já significam "sem valor" no contrato desta
      função (mesma categoria de None/""/"-"/"N/A" string). Tratar como
      erro quebraria qualquer célula genuinamente vazia na coluna
      "Subtotal do produto" (confirmado 0 ocorrências nos 383.298 linhas
      auditadas — não é o caso hoje, mas seria um falso positivo grave
      se ocorresse).
    - float nativo Infinity/-Infinity -> ShopeeNumericParseError. Ao
      contrário de NaN, pandas NUNCA usa Infinity como sentinela de
      ausência — um Infinity nativo só pode vir de dado genuinamente
      inválido, nunca de célula vazia.
    - String "Infinity"/"-Infinity"/"inf" (texto que pandas NÃO
      reconhece como token de ausência, ao contrário de "NaN"/"NA"/
      "N/A"/"null"/"None") -> ShopeeNumericParseError, via math.isfinite
      após a tentativa de conversão.
    - Qualquer outro valor não vazio e não interpretável ->
      ShopeeNumericParseError. Nunca levanta encadeada (__cause__ e
      __context__ sempre None) — a conversão é tentada e o resultado
      guardado numa flag ANTES de qualquer `raise`, nunca dentro do bloco
      `except`, para que nenhuma exceção esteja "sendo tratada" no
      momento do raise (mesmo padrão de _numeric.py; verificado com
      traceback.format_exception nos testes).
    """
    if val is None:
        return None
    if isinstance(val, bool):
        raise ShopeeNumericParseError("valor booleano inesperado em campo numérico") from None
    if isinstance(val, float) and math.isnan(val):
        return None
    if isinstance(val, (int, float)):
        value = float(val)
        if not math.isfinite(value):
            raise ShopeeNumericParseError("valor numérico não finito (Infinity)") from None
        return value

    s = str(val).replace("\xa0", " ").replace("R$", "").strip()
    s = s.replace(" ", "")
    if s.upper() in _EMPTY_NUMERIC_TOKENS:
        return None

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            raise ShopeeNumericParseError("formato numérico ambíguo (padrão US ou inválido)") from None
    elif "," in s:
        s = s.replace(",", ".")

    conversion_ok = True
    value = None
    try:
        value = float(s)
    except ValueError:
        conversion_ok = False

    if not conversion_ok:
        raise ShopeeNumericParseError("valor numérico inválido") from None

    if not math.isfinite(value):
        raise ShopeeNumericParseError("valor numérico não finito (NaN/Infinity)") from None
    return value


def _clean_numeric(series: pd.Series, *, column: str, brand: str,
                    source_files: pd.Series, source_rows: pd.Series) -> pd.Series:
    """Converte uma coluna de export Shopee (string) para float64.

    Contrato (ver _parse_brl_float acima):
      - Ausência legítima (None/vazio/"-"/"N/A") -> 0.0 (nenhuma
        contribuição no GMV — mesmo comportamento já existente).
      - Valor não vazio e inválido -> ShopeeNumericParseError, ANTES de
        qualquer fillna/agregação/escrita — nunca vira 0.0 silenciosamente.
        A mensagem inclui só brand/arquivo/linha/coluna — nunca o valor
        bruto, nunca comprador/pedido/CPF.

    dtype de retorno é sempre float64 explícito (nunca object), mesmo
    quando toda a série é ausência (para não quebrar somas downstream).
    """
    values: list[float] = []
    for val, file_name, row_num in zip(series, source_files, source_rows):
        # Flag booleana em vez de `raise ... from exc` dentro do `except`:
        # garante __context__ = None de verdade (nao apenas suprimido na
        # exibicao por `from None`) — nenhuma excecao esta "sendo tratada"
        # no momento do raise abaixo. Mesmo padrao de
        # pipelines/connectors/shopee/_parser.py::_to_float.
        parse_ok = True
        parsed = None
        try:
            parsed = _parse_brl_float(val)
        except ShopeeNumericParseError:
            parse_ok = False

        if not parse_ok:
            raise ShopeeNumericParseError(
                f"valor numérico inválido: brand={brand} arquivo={file_name} linha={row_num} coluna={column}"
            ) from None

        values.append(0.0 if parsed is None else parsed)
    return pd.Series(values, index=series.index, dtype="float64")


def _parse_qty_int(val: object) -> int | None:
    """Quantidade: reaproveita _parse_brl_float para o parsing numérico de
    base (mesmo contrato de ausência/formato BR/US/NaN/Infinity — ver
    docstring acima) e adiciona duas validações específicas de contagem:

    - deve ser um inteiro exato (sem parte fracionária): "1.5"/"2,50" são
      REJEITADOS, nunca truncados silenciosamente para 1/2 — uma
      quantidade fracionária é sempre um erro de dado, não um valor
      legítimo arredondável.
    - deve ser >= 0: quantidade negativa é rejeitada (nunca aceita como
      um "estorno" implícito ou similar — não há evidência desse uso nos
      exports auditados).

    Ausência legítima (None) vem de _parse_brl_float sem alteração.
    """
    value = _parse_brl_float(val)
    if value is None:
        return None
    if value != int(value):
        raise ShopeeNumericParseError("quantidade não inteira") from None
    if value < 0:
        raise ShopeeNumericParseError("quantidade negativa") from None
    return int(value)


def _clean_int(series: pd.Series, *, column: str, brand: str,
               source_files: pd.Series, source_rows: pd.Series) -> pd.Series:
    """Converte uma coluna de export Shopee (string) para int64, seguindo
    o mesmo padrão fail-fast/sanitizado de _clean_numeric (ver acima),
    mas via _parse_qty_int (inteiro exato, não negativo).

    - Ausência legítima (None/vazio/"-"/"N/A"/NaN nativo) -> 0 (mesmo
      comportamento já existente para Quantidade).
    - Valor não vazio e inválido (texto, decimal não inteiro, negativo,
      Infinity, formato US) -> ShopeeNumericParseError, ANTES de qualquer
      fillna/agregação/escrita — nunca vira 0 silenciosamente, nunca é
      truncado. Mensagem inclui só brand/arquivo/linha/coluna — nunca o
      valor bruto.
    """
    values: list[int] = []
    for val, file_name, row_num in zip(series, source_files, source_rows):
        parse_ok = True
        parsed = None
        try:
            parsed = _parse_qty_int(val)
        except ShopeeNumericParseError:
            parse_ok = False

        if not parse_ok:
            raise ShopeeNumericParseError(
                f"quantidade inválida: brand={brand} arquivo={file_name} linha={row_num} coluna={column}"
            ) from None

        values.append(0 if parsed is None else parsed)
    return pd.Series(values, index=series.index, dtype="int64")


_REQUIRED_COLS = ("order_date", "product_name", "status", "qty", "subtotal")
_OPTIONAL_COLS = ("sku_ref", "variation_name", "buyer_username")


def _load_brand(brand: str) -> pd.DataFrame:
    """Levanta ShopeeProductInputError (nunca retorna None nem prossegue
    silenciosamente) se a marca não tiver nenhum arquivo Order, se
    qualquer arquivo falhar ao ler, ou se faltar qualquer uma das colunas
    obrigatórias — ver contrato completo em ShopeeProductInputError."""
    brand_dir = SHOPEE_ROOT / brand
    files = _find_xlsx(brand_dir)
    if not files:
        raise ShopeeProductInputError(f"nenhum arquivo Order encontrado: brand={brand}") from None

    frames = []
    for f in sorted(files):
        # Flag booleana em vez de `raise ... from e` dentro do `except`:
        # __cause__/__context__ ficam None de verdade (mesmo padrão de
        # _parse_brl_float/_clean_numeric) — a exceção original de
        # pd.read_excel nunca é encadeada nem sua mensagem é incluída
        # (pode conter detalhes internos do arquivo/planilha).
        read_ok = True
        df = None
        try:
            df = pd.read_excel(f, dtype=str)
        except Exception:
            read_ok = False

        if not read_ok:
            raise ShopeeProductInputError(
                f"falha ao ler arquivo Order: brand={brand} arquivo={f.name}"
            ) from None

        # Schema validado POR ARQUIVO INDIVIDUAL, ANTES de qualquer
        # pd.concat com outros arquivos da marca. Se a validação rodasse
        # só depois do concat, uma coluna obrigatória ausente neste
        # arquivo mas presente em outro arquivo da mesma marca existiria
        # globalmente no DataFrame concatenado — as linhas DESTE arquivo
        # virariam NaN/None nessa coluna, que _clean_int/_clean_numeric
        # tratam como ausência legítima (0), mascarando silenciosamente
        # um arquivo com schema incompleto atrás de outro arquivo válido.
        df.columns = [c.strip() for c in df.columns]
        rename = {k: v for k, v in COL_MAP.items() if k in df.columns}
        df = df.rename(columns=rename)

        for col in _REQUIRED_COLS:
            if col not in df.columns:
                raise ShopeeProductInputError(
                    f"coluna obrigatória ausente: brand={brand} arquivo={f.name} coluna={col}"
                ) from None

        # Colunas opcionais: podem faltar neste arquivo (viram coluna de
        # None) — contrato inalterado, confirmado sem evidência de uso
        # obrigatório nos exports. Preenchidas por arquivo (não só depois
        # do concat) para que todo elemento de `frames` já tenha o schema
        # completo antes de ser concatenado.
        for col in _OPTIONAL_COLS:
            if col not in df.columns:
                df[col] = None

        # Provenance por linha (nome do arquivo + numero fisico da
        # linha no Excel, header=linha 1) — usada so' para contexto
        # sanitizado em erros de parsing numerico (_clean_numeric),
        # nunca persistida no banco (removida antes do return).
        df["_source_file"] = f.name
        df["_source_row"] = df.index + 2
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    # Tipos
    # Os exports Shopee usam "Data de criação do pedido" em formato ISO
    # ("YYYY-MM-DD HH:MM", confirmado em 85/85 arquivos .xlsx do diretório shopee/).
    # dayfirst=True aqui era o bug: para strings ISO, o parser do pandas/dateutil
    # ainda troca os tokens de dia/mês quando dayfirst=True é passado explicitamente,
    # projetando pedidos do dia 1-12 de qualquer mês real (jan-jun/2026) para meses
    # futuros inexistentes (jul-dez/2026). Ver docs/sections/produtos_audit.md.
    df["order_date"] = pd.to_datetime(df["order_date"], format="%Y-%m-%d %H:%M", errors="coerce")
    df["qty"] = _clean_int(
        df["qty"], column="Quantidade", brand=brand,
        source_files=df["_source_file"], source_rows=df["_source_row"],
    )
    df["subtotal"] = _clean_numeric(
        df["subtotal"], column="Subtotal do produto", brand=brand,
        source_files=df["_source_file"], source_rows=df["_source_row"],
    )
    df = df.drop(columns=["_source_file", "_source_row"])
    df["status"] = df["status"].fillna("").str.strip()
    df["brand"] = brand

    # ref_month = primeiro dia do mês
    df["ref_month"] = df["order_date"].dt.to_period("M").dt.to_timestamp()

    # Remover linhas sem data ou produto
    df = df.dropna(subset=["order_date", "product_name", "ref_month"])
    df["product_name"] = df["product_name"].astype(str).str.strip()

    return df


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    grp_cols = ["brand", "ref_month", "sku_ref", "product_name", "variation_name"]

    completed = df[df["status"] == "Concluído"].copy()
    canceled  = df[df["status"] == "Cancelado"].copy()

    agg_completed = (
        completed.groupby(grp_cols, dropna=False)
        .agg(
            gmv=("subtotal", "sum"),
            units_sold=("qty", "sum"),
            completed_orders=("status", "count"),
            unique_buyers=("buyer_username", "nunique"),
        )
        .reset_index()
    )

    agg_canceled = (
        canceled.groupby(grp_cols, dropna=False)
        .agg(canceled_orders=("status", "count"))
        .reset_index()
    )

    # outer (nao left): um grupo com SOMENTE pedidos "Cancelado" (zero
    # "Concluido") nao existe em agg_completed e seria descartado inteiro
    # pelo left merge, subestimando canceled_orders/cancel_rate_pct (Bug 8,
    # ver docs/sections/produtos_audit.md). gmv/units_sold ficam 0 e
    # unique_buyers fica 0 para esses grupos — nunique() e' calculado so'
    # sobre compradores de pedidos concluidos, nunca sobre cancelados.
    result = agg_completed.merge(agg_canceled, on=grp_cols, how="outer")
    result["gmv"] = result["gmv"].fillna(0.0)
    result["units_sold"] = result["units_sold"].fillna(0).astype(int)
    result["completed_orders"] = result["completed_orders"].fillna(0).astype(int)
    result["unique_buyers"] = result["unique_buyers"].fillna(0).astype(int)
    result["canceled_orders"] = result["canceled_orders"].fillna(0).astype(int)

    total_orders = result["completed_orders"] + result["canceled_orders"]
    result["cancel_rate_pct"] = [
        round(result.loc[i, "canceled_orders"] / total_orders[i] * 100, 4)
        if total_orders[i] > 0 else None
        for i in result.index
    ]
    result["avg_price"] = [
        round(result.loc[i, "gmv"] / result.loc[i, "units_sold"], 2)
        if result.loc[i, "units_sold"] > 0 else None
        for i in result.index
    ]

    # sku_ref_key: substitui NULL por '' para a constraint UNIQUE
    result["sku_ref_key"] = result["sku_ref"].fillna("").astype(str)

    return result


# ---------------------------------------------------------------------------
# Main — duas fases deliberadamente separadas: NENHUMA conexão/engine/DDL
# acontece enquanto qualquer marca ainda não foi validada e agregada.
# ---------------------------------------------------------------------------

def _prepare_all_brands() -> list[tuple[str, pd.DataFrame]]:
    """Fase A — somente memória/arquivos, nenhum banco.

    Carrega, valida (fail-fast via _clean_int/_clean_numeric) e agrega
    CADA marca de BRANDS antes de qualquer engine/conexão/DDL ser sequer
    considerado. Se qualquer marca levantar ShopeeNumericParseError,
    ShopeeProductInputError (arquivo ausente/ilegível, coluna obrigatória
    ausente — ver _load_brand) ou qualquer outra exceção, ela propaga
    daqui e main() nunca chega a chamar
    _get_local_pg_url()/create_engine() — a garantia fail-fast vale para
    TODAS as marcas, não só a primeira a falhar, e o retorno só acontece
    se TODAS as marcas de BRANDS tiverem sido preparadas com sucesso
    (nunca uma lista parcial).

    _load_brand nunca deveria retornar None (todo caminho de falha
    levanta ShopeeProductInputError) — o `if df is None: raise` abaixo é
    uma segunda barreira defensiva: mesmo que uma regressão futura em
    _load_brand volte a retornar None silenciosamente, esta função ainda
    assim recusa prosseguir para a Fase B com uma marca sem dados.

    Só os agregados (pequenos — uma linha por sku/mês, não por pedido)
    são retidos; o DataFrame bruto de cada marca (uma linha por SKU por
    pedido, até centenas de milhares de linhas) é descartado assim que a
    agregação daquela marca termina, nunca acumulado para todas as marcas
    simultaneamente.
    """
    prepared: list[tuple[str, pd.DataFrame]] = []
    for brand in BRANDS:
        print(f"\n[{brand}] a carregar...")
        df = _load_brand(brand)
        if df is None:
            raise ShopeeProductInputError(f"marca sem dados preparados inesperadamente: brand={brand}") from None
        agg = _aggregate(df)
        print(f"  {len(agg)} linhas agregadas.")
        prepared.append((brand, agg))
        del df
    return prepared


def _write_prepared_brands(prepared: list[tuple[str, pd.DataFrame]]) -> int:
    """Fase B — só chega aqui depois que TODAS as marcas da Fase A foram
    validadas e agregadas com sucesso. Abre a única conexão/engine desta
    execução, executa o DDL e grava os agregados já prontos."""
    local_pg_url = _get_local_pg_url()
    print(f"PostgreSQL local (destino): {_sanitize_url(local_pg_url)}")
    engine = create_engine(local_pg_url)

    with engine.begin() as conn:
        conn.execute(text(DDL))
    print("Schema/tabela verificados.")

    total_inserted = 0

    for brand, agg in prepared:
        rows_inserted = 0
        with engine.begin() as conn:
            for _, row in agg.iterrows():
                ref_month_val = row["ref_month"]
                if pd.isna(ref_month_val):
                    continue
                params = {
                    "ref_month":        ref_month_val.date().isoformat(),
                    "brand":            brand,
                    "sku_ref":          row["sku_ref"] if pd.notna(row["sku_ref"]) else None,
                    "sku_ref_key":      row["sku_ref_key"],
                    "product_name":     row["product_name"],
                    "variation_name":   row["variation_name"] if pd.notna(row.get("variation_name")) else None,
                    "gmv":              float(row["gmv"]),
                    "units_sold":       int(row["units_sold"]),
                    "completed_orders": int(row["completed_orders"]),
                    "canceled_orders":  int(row["canceled_orders"]),
                    "cancel_rate_pct":  float(row["cancel_rate_pct"]) if row["cancel_rate_pct"] is not None and pd.notna(row["cancel_rate_pct"]) else None,
                    "unique_buyers":    int(row["unique_buyers"]),
                    "avg_price":        float(row["avg_price"]) if row["avg_price"] is not None and pd.notna(row["avg_price"]) else None,
                }
                conn.execute(text(UPSERT_SQL), params)
                rows_inserted += 1

        print(f"  {rows_inserted} linhas inseridas/actualizadas.")
        total_inserted += rows_inserted

    return total_inserted


def main() -> None:
    prepared = _prepare_all_brands()
    total_inserted = _write_prepared_brands(prepared)
    print(f"\nTotal: {total_inserted} linhas carregadas.")


if __name__ == "__main__":
    main()
