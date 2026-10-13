"""Publicador direto XLSX -> Neon da procedencia `manual_export` (SHOPEE-PRODUCTS-KOKESHI-3).

POR QUE EXISTE
--------------
A Kokeshi nao tem aplicacao Shopee: a unica fonte de Produtos dela e o export
do Seller Center. O caminho antigo (loader -> PostgreSQL local -> `sync_produtos`)
depende de `LOCAL_PG_URL`, de um schema local que nunca recebeu as migrations
023/024 e de um UPSERT que nunca remove chave que sumiu. Este modulo publica o
export DIRETO no Neon, com a MESMA transformacao oficial do loader, reaproveitando
as portas ja testadas de `etl.backfill_shopee_products`.

O QUE ELE GARANTE (e prova em teste)
------------------------------------
- dry-run por padrao. `--apply` exige consentimento proprio e credencial de
  escrita dedicada (as mesmas variaveis do backfill: BACKFILL_NEON_RW_URL etc.);
- grava `source = 'manual_export'` explicitamente em toda linha;
- o DELETE e o backup sao SEMPRE limitados a `source = 'manual_export'` e aos
  pares (marca, competencia) pedidos;
- dentro da transacao, antes do commit: impressao digital de TODAS as linhas
  fora dos pares pedidos (qualquer procedencia, qualquer mes) e de TODAS as
  linhas `api` identicas as de antes. Divergencia faz rollback;
- `source_mode` e relido DENTRO da transacao de mutacao, com `FOR SHARE`,
  depois do lock e imediatamente antes do DELETE: uma troca concorrente ou
  espera o commit ou faz a publicacao recusar;
- recusa marca cuja procedencia ativa nao seja explicitamente `manual_export`
  (inclusive marca AUSENTE da tabela de modo); so a `kokeshi` e aceita;
- recusa competencia FUTURA (no --scope e na janela);
- so aceita exports de MES INTEIRO numa pasta ISOLADA de verdade: sem symlink,
  junction ou reparse point, e todo arquivo resolvido dentro da raiz;
- UMA lista imutavel de arquivos, com SHA-256, usada do inicio ao fim; o hash e
  reconferido depois do preparo (arquivo trocado no meio = recusa);
- limites explicitos de arquivos, bytes e linhas;
- validacao ESTRITA da celula antes da transformacao: data invalida, celula
  obrigatoria vazia, formula e linha fora da janela sao recusadas; e o total de
  linhas que entra na staging e conferido contra o bruto (nada descartado em
  silencio). Ausencia nunca vira zero;
- backup duravel conferido, advisory lock de sessao, transacao unica, ZERO
  retry, manifesto auditavel (run_id, hashes, competencias, resultado) sem DSN;
- nunca escreve em `marts.shopee_product_source_mode`.

USO
---
    python -m etl.publish_shopee_manual_export --source-root <pasta isolada> \\
        --scope kokeshi:2026-07 ... --audit-dir <pasta>               # dry-run (Neon read-only)
    ... --offline                                                     # so arquivos, sem banco
    ... --apply                                                       # exige autorizacao
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from sqlalchemy import text as sqlalchemy_text

from etl import backfill_shopee_products as bf
from etl import load_shopee_products as loader

TABLE = bf.TABLE
MODE_TABLE = "marts.shopee_product_source_mode"
SOURCE = "manual_export"
TARGET = bf.TARGET_NEON

#: Primeira utilizacao: so a Kokeshi. Ampliar e decisao explicita, nunca default.
ALLOWED_BRANDS: tuple[str, ...] = ("kokeshi",)

#: Consentimento PROPRIO, diferente do backfill: autorizar um nao autoriza o outro.
CONSENT_ENV = "I_UNDERSTAND_THIS_PUBLISHES_SHOPEE_MANUAL_EXPORT"

#: Mesma chave do backfill: os dois escrevem a mesma tabela e nunca rodam juntos.
LOCK_KEY = bf.ADVISORY_LOCK_KEY

#: Fuso operacional da Torre (mesmo do publisher da API).
OPERATIONAL_TZ = timezone(timedelta(hours=-3))

#: Limites explicitos, medidos nos exports reais da Kokeshi de 13/10/2026
#: (66 arquivos, 122,6 MB, maior parte 1,92 MB, ~6 mil linhas por parte,
#: ~400 mil linhas em jul–out). Folga de ~3x: suficiente para um mes de pico
#: e pequena o bastante para que uma pasta errada (um ano inteiro, outra
#: marca, um dump) seja recusada antes de qualquer leitura de celula.
MAX_FILES_PER_BRAND = 150
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 400 * 1024 * 1024
MAX_ROWS_PER_FILE = 25_000
MAX_TOTAL_ROWS = 1_500_000

#: Colunas cuja celula vazia NAO pode virar zero nem sumir.
STRICT_COLS = ("ID do pedido", "Data de criação do pedido", "Nome do Produto",
               "Quantidade", "Subtotal do produto", "Status do pedido")
DATE_FMT = "%Y-%m-%d %H:%M"

#: Colunas gravadas. `ingested_at` vem de NOW() do banco; `id` da sequence.
INSERT_COLS: tuple[str, ...] = bf.DATA_COLS + ("source", "source_run_id", "is_partial")
#: Colunas do backup e das impressoes digitais: tudo, inclusive procedencia.
BACKUP_COLS: tuple[str, ...] = bf.DATA_COLS + (
    "source", "source_run_id", "source_captured_at", "is_partial", "ingested_at")

EXIT_OK = bf.EXIT_OK
EXIT_REFUSED = bf.EXIT_VALIDATION_REFUSED
EXIT_ROLLED_BACK = bf.EXIT_ROLLED_BACK
EXIT_INDETERMINATE = bf.EXIT_INDETERMINATE
EXIT_USAGE = bf.EXIT_USAGE

_SCOPE_RE = re.compile(r"^(?P<brand>[a-z0-9_]+):(?P<month>\d{4}-\d{2})$")


class PublishRefused(bf.BackfillValidationError):
    """Uma porta do publicador reprovou. Nada foi escrito."""


def today_brt() -> date:
    return datetime.now(OPERATIONAL_TZ).date()


# ---------------------------------------------------------------------------
# Escopo
# ---------------------------------------------------------------------------

def parse_scopes(raw: list[str] | None, *, today: date) -> list[tuple[str, str]]:
    """Escopos validados: allowlist de marca e NUNCA competencia futura."""
    scopes = bf.parse_scopes(raw)
    assert_brands_allowed(scopes)
    corrente = f"{today:%Y-%m}"
    futuras = sorted(f"{b}:{m}" for b, m in scopes if m > corrente)
    if futuras:
        raise PublishRefused(
            f"competencia futura nao pode ser publicada: {', '.join(futuras)} "
            f"(hoje BRT = {today}; nada foi escrito)")
    return scopes


def assert_brands_allowed(scopes: list[tuple[str, str]]) -> None:
    if not scopes:
        raise PublishRefused("nenhum --scope informado (nada foi escrito)")
    fora = sorted({b for b, _ in scopes} - set(ALLOWED_BRANDS))
    if fora:
        raise PublishRefused(
            f"marca(s) fora da allowlist desta operacao: {', '.join(fora)}. "
            f"Permitidas: {', '.join(ALLOWED_BRANDS)} (nada foi escrito)")


# ---------------------------------------------------------------------------
# Isolamento real da pasta e lista unica de arquivos
# ---------------------------------------------------------------------------

def is_link_like(p: Path) -> bool:
    """Symlink, junction ou qualquer reparse point (Windows). Sem seguir o link."""
    try:
        if p.is_symlink():
            return True
        is_junction = getattr(p, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        st = os.lstat(p)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def assert_source_root_isolated(root: Path) -> None:
    """A pasta de trabalho `shopee/` tem os exports SEMANAIS antigos. Publicar a
    partir dela (ou de dentro/acima dela) reintroduziria os snapshots imaturos."""
    if is_link_like(root):
        raise PublishRefused("--source-root e symlink/junction/reparse point (nada foi escrito)")
    trabalho = Path(loader.SHOPEE_ROOT).resolve()
    alvo = root.resolve()
    if alvo == trabalho or trabalho in alvo.parents or alvo in trabalho.parents:
        raise PublishRefused(
            "--source-root nao pode ser, conter nem estar dentro da pasta de "
            "trabalho dos exports semanais; use uma pasta NOVA e isolada "
            "(nada foi escrito)")


@dataclass(frozen=True)
class SourceFile:
    path: Path
    name: str
    size: int
    sha256: str


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_brand_files(root: Path, brand: str) -> tuple[SourceFile, ...]:
    """A UNICA listagem da pasta da marca. Recusa link em qualquer nivel,
    arquivo resolvido fora da raiz, subpasta, arquivo que nao seja export de
    pedidos e qualquer estouro de limite."""
    raiz = root.resolve()
    pasta = root / brand
    if not pasta.exists():
        raise PublishRefused(f"pasta da marca ausente: {brand} (nada foi escrito)")
    if is_link_like(pasta):
        raise PublishRefused(
            f"pasta da marca {brand} e symlink/junction/reparse point (nada foi escrito)")
    pasta_r = pasta.resolve()
    if pasta_r.parent != raiz:
        raise PublishRefused(f"pasta da marca {brand} resolve fora da raiz (nada foi escrito)")

    arquivos: list[SourceFile] = []
    total = 0
    for p in sorted(pasta.iterdir(), key=lambda x: x.name):
        if is_link_like(p):
            raise PublishRefused(f"{brand}: {p.name} e link/reparse point (nada foi escrito)")
        if p.is_dir():
            raise PublishRefused(f"{brand}: subpasta inesperada {p.name} (nada foi escrito)")
        if not loader._SNAPSHOT_NAME_RE.match(p.name):
            raise PublishRefused(
                f"{brand}: arquivo que nao e export de pedidos com janela: {p.name} "
                f"(a pasta isolada so pode ter Order.all.*; nada foi escrito)")
        pr = p.resolve()
        if pr.parent != pasta_r:
            raise PublishRefused(f"{brand}: {p.name} resolve fora da pasta (nada foi escrito)")
        tam = pr.stat().st_size
        if tam > MAX_FILE_BYTES:
            raise PublishRefused(f"{brand}: {p.name} excede {MAX_FILE_BYTES} bytes (nada foi escrito)")
        total += tam
        arquivos.append(SourceFile(path=pr, name=p.name, size=tam, sha256=_sha256(pr)))
    if not arquivos:
        raise PublishRefused(f"{brand}: nenhum export na pasta isolada (nada foi escrito)")
    if len(arquivos) > MAX_FILES_PER_BRAND:
        raise PublishRefused(f"{brand}: {len(arquivos)} arquivos > limite {MAX_FILES_PER_BRAND}")
    if total > MAX_TOTAL_BYTES:
        raise PublishRefused(f"{brand}: {total} bytes > limite {MAX_TOTAL_BYTES}")
    return tuple(arquivos)


def assert_files_unchanged(files: tuple[SourceFile, ...]) -> None:
    """Reconfere o hash de cada arquivo DEPOIS do preparo: arquivo trocado no
    meio do caminho invalida a staging inteira."""
    for f in files:
        if _sha256(f.path) != f.sha256:
            raise PublishRefused(f"{f.name} mudou durante o preparo (nada foi escrito)")


# ---------------------------------------------------------------------------
# Janelas de mes inteiro
# ---------------------------------------------------------------------------

def _ultimo_dia(ano: int, mes: int) -> date:
    prox = date(ano + (mes // 12), (mes % 12) + 1, 1)
    return prox - timedelta(days=1)


def assert_full_month_windows(brand: str, plan: dict[str, tuple[str, str]],
                              months: list[str], *, today: date) -> dict[str, str]:
    """Cada competencia pedida tem de vir de EXATAMENTE uma janela de mes inteiro.

    Mes fechado: `AAAAMM01_<ultimo dia>`. Mes corrente: `AAAAMM01_<D-1 ou antes>`.
    Mes FUTURO: sempre recusado — nunca e tratado como fechado. Janela que nao
    comeca no dia 1, que atravessa meses, de mes nao pedido ou repetida: recusa.
    E isso que barra os semanais: o loader ordena por janela, nao por download,
    e uma semanal antiga venceria a reexportacao nos dias que cobre."""
    por_mes: dict[str, list[tuple[str, str]]] = {}
    for ini, fim in sorted(set(plan.values())):
        d0 = date(int(ini[:4]), int(ini[4:6]), int(ini[6:]))
        d1 = date(int(fim[:4]), int(fim[4:6]), int(fim[6:]))
        mes = f"{d0:%Y-%m}"
        if (d0.year, d0.month) > (today.year, today.month):
            raise PublishRefused(
                f"{brand}: janela {ini}_{fim} e de competencia FUTURA (nada foi escrito)")
        if d0.day != 1 or (d1.year, d1.month) != (d0.year, d0.month):
            raise PublishRefused(
                f"{brand}: janela {ini}_{fim} nao e de mes inteiro (comeca fora do "
                f"dia 1 ou atravessa meses). Exports semanais sao recusados "
                f"(nada foi escrito)")
        if (d0.year, d0.month) == (today.year, today.month):
            if d1 >= today:
                raise PublishRefused(
                    f"{brand}: janela {ini}_{fim} do mes corrente vai ate D0 ou "
                    f"alem; use ate D-1 (nada foi escrito)")
        elif d1 != _ultimo_dia(d0.year, d0.month):
            raise PublishRefused(
                f"{brand}: janela {ini}_{fim} de mes fechado nao termina no ultimo "
                f"dia do mes (nada foi escrito)")
        por_mes.setdefault(mes, []).append((ini, fim))

    repetidos = sorted(m for m, js in por_mes.items() if len(js) > 1)
    if repetidos:
        raise PublishRefused(
            f"{brand}: mais de uma janela para {', '.join(repetidos)} (nada foi escrito)")
    sobra = sorted(set(por_mes) - set(months))
    if sobra:
        raise PublishRefused(
            f"{brand}: a pasta tem export de competencia nao pedida: "
            f"{', '.join(sobra)} (nada foi escrito)")
    falta = sorted(set(months) - set(por_mes))
    if falta:
        raise PublishRefused(
            f"{brand}: falta o export de mes inteiro para {', '.join(falta)} "
            f"(nada foi escrito)")
    return {m: f"{js[0][0]}_{js[0][1]}" for m, js in sorted(por_mes.items())}


# ---------------------------------------------------------------------------
# Validacao estrita das celulas (antes da transformacao)
# ---------------------------------------------------------------------------

def has_formula(path: Path) -> bool:
    """Formula no XML de qualquer planilha. Lida pelo pandas, uma formula sem
    valor em cache vira celula vazia — e celula vazia viraria zero."""
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("xl/worksheets/") and n.endswith(".xml"):
                with z.open(n) as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        if b"<f>" in chunk or b"<f " in chunk:
                            return True
    return False


def _vazio(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() in ("", "nan", "None")


def _decimal(v) -> Decimal | None:
    s = str(v).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


@dataclass
class RawStats:
    """Totais medidos DIRETO do XLSX, para provar que nada sumiu na staging."""
    rows: int = 0
    per_month: dict[tuple[str, str], dict] = field(default_factory=dict)


def validate_raw(brand: str, files: tuple[SourceFile, ...],
                 plan: dict[str, tuple[str, str]]) -> RawStats:
    """Recusa: formula, coluna obrigatoria ausente, celula obrigatoria vazia,
    data invalida, data fora da janela declarada, quantidade/subtotal que nao e
    numero finito nao negativo, e estouro de linhas. Mede, por competencia, as
    linhas Concluido/Cancelado, unidades e GMV Concluido do bruto."""
    stats = RawStats()
    for f in files:
        if f.name not in plan:
            continue
        if has_formula(f.path):
            raise PublishRefused(f"{f.name}: contem formula (nada foi escrito)")
        df = pd.read_excel(f.path, dtype=str)
        df.columns = [c.strip() for c in df.columns]
        faltam = [c for c in STRICT_COLS if c not in df.columns]
        if faltam:
            raise PublishRefused(f"{f.name}: colunas ausentes {faltam} (nada foi escrito)")
        if len(df) > MAX_ROWS_PER_FILE:
            raise PublishRefused(f"{f.name}: {len(df)} linhas > limite {MAX_ROWS_PER_FILE}")
        stats.rows += len(df)
        if stats.rows > MAX_TOTAL_ROWS:
            raise PublishRefused(f"total de linhas > limite {MAX_TOTAL_ROWS} (nada foi escrito)")

        for col in STRICT_COLS:
            n = int(df[col].map(_vazio).sum())
            if n:
                raise PublishRefused(
                    f"{f.name}: {n} celula(s) vazia(s) em '{col}' — ausencia nao vira "
                    f"zero nem some (nada foi escrito)")
        datas = pd.to_datetime(df["Data de criação do pedido"], format=DATE_FMT, errors="coerce")
        n = int(datas.isna().sum())
        if n:
            raise PublishRefused(f"{f.name}: {n} data(s) invalida(s) (nada foi escrito)")
        ini, fim = plan[f.name]
        d0 = pd.Timestamp(datetime.strptime(ini, "%Y%m%d"))
        d1 = pd.Timestamp(datetime.strptime(fim, "%Y%m%d")) + pd.Timedelta(days=1)
        fora = int(((datas < d0) | (datas >= d1)).sum())
        if fora:
            raise PublishRefused(
                f"{f.name}: {fora} linha(s) fora da janela declarada {ini}_{fim} (nada foi escrito)")
        qtd = df["Quantidade"].map(_decimal)
        sub = df["Subtotal do produto"].map(_decimal)
        ruins = int(qtd.isna().sum() + sub.isna().sum())
        ruins += int(sum(1 for q in qtd if q is not None and (q < 0 or q != q.to_integral_value())))
        ruins += int(sum(1 for s in sub if s is not None and s < 0))
        if ruins:
            raise PublishRefused(
                f"{f.name}: {ruins} quantidade/subtotal invalido(s) (nada foi escrito)")

        status = df["Status do pedido"].str.strip()
        meses = datas.dt.strftime("%Y-%m")
        for mes in meses.unique():
            sel = meses == mes
            conc = sel & (status == "Concluído")
            canc = sel & (status == "Cancelado")
            m = stats.per_month.setdefault((brand, mes), {"rows": 0, "concluido": 0, "cancelado": 0,
                                                 "units": 0, "gmv": Decimal("0")})
            m["rows"] += int(sel.sum())
            m["concluido"] += int(conc.sum())
            m["cancelado"] += int(canc.sum())
            m["units"] += int(sum(qtd[conc]))
            m["gmv"] += sum(sub[conc], Decimal("0"))
    return stats


def assert_nothing_dropped(staging: bf.Staging, raw: RawStats) -> None:
    """A staging tem de carregar EXATAMENTE as linhas Concluido/Cancelado do
    bruto, as unidades e o GMV. Diferenca = linha descartada em silencio."""
    problemas = []
    for brand, month in staging.scopes:
        r = raw.per_month.get((brand, month))
        c = bf._cut(staging.rows, brand, month)
        if r is None:
            problemas.append(f"{month}: nada no bruto")
            continue
        got = {"concluido": bf._isum(c, "completed_orders"), "cancelado": bf._isum(c, "canceled_orders"),
               "units": bf._isum(c, "units_sold"), "gmv": bf._dsum(c, "gmv")}
        exp = {"concluido": r["concluido"], "cancelado": r["cancelado"], "units": r["units"],
               "gmv": r["gmv"].quantize(Decimal("0.01"))}
        for k in exp:
            if k == "gmv":
                if abs(got[k] - exp[k]) > Decimal("0.01"):
                    problemas.append(f"{month} gmv {got[k]} != bruto {exp[k]}")
            elif got[k] != exp[k]:
                problemas.append(f"{month} {k} {got[k]} != bruto {exp[k]}")
    if problemas:
        raise PublishRefused(
            "a staging nao reproduz o bruto (linha descartada ou duplicada em "
            "silencio): " + "; ".join(problemas) + " (nada foi escrito)")


def assert_scopes_have_value(staging: bf.Staging) -> None:
    """Ausencia nunca vira zero: competencia sem linha, ou so com GMV zero, nao
    e publicada. Publicar substituiria o mart por um 'zero' que e falta de dado."""
    problemas = []
    for brand, month in staging.scopes:
        corte = bf._cut(staging.rows, brand, month)
        if corte.empty:
            problemas.append(f"{brand}:{month} sem nenhuma linha")
        elif bf._dsum(corte, "gmv") <= 0:
            problemas.append(f"{brand}:{month} so com GMV zero ({len(corte)} linhas)")
    if problemas:
        raise PublishRefused(
            "competencia sem valor confiavel — nao se publica zero no lugar de "
            "ausencia: " + "; ".join(problemas) + " (nada foi escrito)")


def mark_partial(staging: bf.Staging, *, today: date) -> pd.DataFrame:
    """`is_partial` = competencia corrente (BRT). Futura nunca chega aqui."""
    rows = staging.rows.copy()
    corrente = date(today.year, today.month, 1)
    rows["is_partial"] = pd.to_datetime(rows["ref_month"]).dt.date == corrente
    return rows


# ---------------------------------------------------------------------------
# Preparo (zero banco)
# ---------------------------------------------------------------------------

@contextmanager
def _loader_files(root: Path, files_by_brand: dict[str, tuple[SourceFile, ...]]):
    """Faz o loader oficial ler EXATAMENTE a lista ja validada, sem relistar a
    pasta: `_find_xlsx` devolve a lista congelada da marca."""
    orig_root, orig_find = loader.SHOPEE_ROOT, loader._find_xlsx
    por_pasta = {(root / b).resolve(): [f.path for f in fs] for b, fs in files_by_brand.items()}

    def _congelada(brand_dir):
        chave = Path(brand_dir).resolve()
        if chave not in por_pasta:
            raise PublishRefused("loader tentou ler pasta fora da lista congelada")
        return list(por_pasta[chave])

    loader.SHOPEE_ROOT, loader._find_xlsx = root, _congelada
    try:
        yield
    finally:
        loader.SHOPEE_ROOT, loader._find_xlsx = orig_root, orig_find


@dataclass
class Prepared:
    staging: bf.Staging
    files: dict[str, tuple[SourceFile, ...]]
    windows: dict[str, dict[str, str]]
    raw: RawStats
    today: date


def prepare(scopes: list[tuple[str, str]], root: Path, *, today: date) -> Prepared:
    """Portas offline + staging com a transformacao OFICIAL do loader."""
    assert_brands_allowed(scopes)
    assert_source_root_isolated(root)
    files_by_brand: dict[str, tuple[SourceFile, ...]] = {}
    windows: dict[str, dict[str, str]] = {}
    raw = RawStats()
    for brand in sorted({b for b, _ in scopes}):
        files = list_brand_files(root, brand)                        # unica listagem
        plan = loader._plan_brand_snapshots(brand, [f.path for f in files])
        windows[brand] = assert_full_month_windows(
            brand, plan, sorted(m for b, m in scopes if b == brand), today=today)
        r = validate_raw(brand, files, plan)
        raw.rows += r.rows
        raw.per_month.update(r.per_month)
        files_by_brand[brand] = files
    with _loader_files(root, files_by_brand):
        staging = bf.build_staging(scopes, root)
    bf.assert_staging_unique(staging)
    bf.assert_staging_within_scope(staging)
    assert_scopes_have_value(staging)
    assert_nothing_dropped(staging, raw)
    for fs in files_by_brand.values():
        assert_files_unchanged(fs)
    return Prepared(staging=staging, files=files_by_brand, windows=windows, raw=raw, today=today)


# ---------------------------------------------------------------------------
# Leituras e predicados
# ---------------------------------------------------------------------------

def scoped_predicate(scopes: list[tuple[str, str]]) -> tuple[str, dict]:
    """Pares pedidos E procedencia manual. Unico predicado de backup, DELETE e
    contagem: nao existe caminho de escrita sem o filtro de `source`."""
    pred, params = bf._scope_predicate(scopes)
    params = dict(params, src=SOURCE)
    return f"({pred}) AND source = :src", params


def read_source_modes(conn, *, lock: bool = False, brands=None) -> dict[str, str]:
    """`lock=True`: `FOR SHARE` — bloqueia UPDATE concorrente da linha de modo
    ate o fim da transacao corrente."""
    sql = f"SELECT brand, source FROM {MODE_TABLE}"
    params = {}
    if brands is not None:
        sql += " WHERE brand = ANY(:brands)"
        params["brands"] = sorted(brands)
    if lock:
        sql += " FOR SHARE"
    return {r[0]: r[1] for r in conn.execute(sqlalchemy_text(sql), params).all()}


def assert_brands_read_manual(modes: dict[str, str], brands) -> None:
    """Recusa marca ligada na API e marca AUSENTE da tabela de modo."""
    problemas = []
    for b in sorted(set(brands)):
        modo = modes.get(b)
        if modo is None:
            problemas.append(f"{b}: ausente de {MODE_TABLE}")
        elif modo != SOURCE:
            problemas.append(f"{b}: procedencia ativa '{modo}'")
    if problemas:
        raise PublishRefused(
            "so se publica manual_export para marca que LE manual_export: "
            + "; ".join(problemas) + " (nada foi escrito)")


_BEFORE_COLS = ("ref_month", "brand", "sku_ref", "sku_ref_key", "product_name",
                "variation_name", "gmv", "units_sold", "completed_orders",
                "canceled_orders", "unique_buyers")


def read_before(conn, scopes) -> pd.DataFrame:
    """Estado anterior SO da procedencia manual nos pares pedidos."""
    pred, params = scoped_predicate(scopes)
    rows = conn.execute(sqlalchemy_text(
        f"SELECT {', '.join(_BEFORE_COLS)} FROM {TABLE} WHERE {pred}"), params).mappings().all()
    return pd.DataFrame(list(rows), columns=list(_BEFORE_COLS))


def fingerprint(conn, predicate: str, params: dict | None = None) -> dict:
    sql = bf.fingerprint_sql(TABLE, BACKUP_COLS, predicate)
    r = conn.execute(sqlalchemy_text(sql), params or {}).mappings().first()
    return {"n": int(r["n"]), "checksum": r["checksum"]}


def api_fingerprint(conn) -> dict:
    """TODAS as linhas `api` da tabela, em qualquer marca e competencia."""
    return fingerprint(conn, "source = 'api'")


def outside_fingerprint(conn, scopes) -> dict:
    """TODAS as linhas fora dos pares pedidos da procedencia manual — junho,
    outras marcas, linhas `api` do mesmo par. Nada disso pode mudar."""
    pred, params = scoped_predicate(scopes)
    return fingerprint(conn, f"NOT ({pred})", params)


DAILY_SQL = """
SELECT to_char(date_trunc('month', f.date), 'YYYY-MM') AS mes,
       COALESCE(SUM(f.gmv), 0) AS gmv, MAX(f.date) AS ate
  FROM marts.fact_marketplace_daily_performance f
  JOIN marts.dim_loja l ON l.loja_id = f.loja_id
 WHERE f.marketplace_id = 3 AND l.brand_key = :brand
   AND to_char(f.date, 'YYYY-MM') = ANY(:meses)
 GROUP BY 1
"""


def read_daily_reference(conn, brand: str, months: list[str]) -> dict[str, dict]:
    rows = conn.execute(sqlalchemy_text(DAILY_SQL),
                        {"brand": brand, "meses": list(months)}).mappings().all()
    return {r["mes"]: {"gmv": Decimal(str(r["gmv"])), "ate": r["ate"]} for r in rows}


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------

class ManualExportExecutor:
    """Scoped replace restrito a `source = 'manual_export'`. Recebe a conexao ja
    aberta: quem chama decide o banco."""

    def __init__(self, connection, *, run_id: str, clock=None) -> None:
        self.conn = connection
        self.run_id = run_id
        self._clock = clock or datetime.now
        self.backup_table: str | None = None
        self.backup_fingerprint: dict | None = None
        self.emitted: list[str] = []
        self._trans = None

    def _exec(self, sql: str, params: dict | None = None):
        self.emitted.append(sql.strip().split("\n")[0][:160])
        return self.conn.execute(sqlalchemy_text(sql), params or {})

    def backup_scope(self, scopes) -> dict:
        pred, params = scoped_predicate(scopes)
        nome = bf.backup_table_name(TARGET, self._clock().strftime("%Y%m%d_%H%M%S"))
        cols = ", ".join(BACKUP_COLS)
        trans = self.conn.begin()
        try:
            self._exec(f"CREATE TABLE {nome} AS SELECT {cols} FROM {TABLE} WHERE {pred}", params)
            origem = self._exec(bf.fingerprint_sql(TABLE, BACKUP_COLS, pred), params).mappings().first()
            copia = self._exec(bf.fingerprint_sql(nome, BACKUP_COLS, "TRUE")).mappings().first()
            if int(origem["n"]) != int(copia["n"]) or origem["checksum"] != copia["checksum"]:
                raise PublishRefused("backup NAO confere com a origem; nada foi apagado")
            trans.commit()
        except BaseException:
            try:
                trans.rollback()
            except Exception:
                pass
            raise
        self.backup_table = nome
        self.backup_fingerprint = {"rows": int(origem["n"]), "checksum": origem["checksum"]}
        return {"table": nome, **self.backup_fingerprint,
                "retention_days": bf.BACKUP_RETENTION_DAYS}

    def begin(self):
        self._trans = self.conn.begin()

    def recheck_modes(self, brands) -> dict[str, str]:
        """Dentro da transacao de mutacao, com `FOR SHARE`: uma troca concorrente
        ou espera este commit ou ja aconteceu — e entao a publicacao recusa."""
        modos = read_source_modes(self.conn, lock=True, brands=brands)
        assert_brands_read_manual(modos, brands)
        return modos

    def delete_scope(self, scopes) -> int:
        if not self.backup_table:
            raise PublishRefused("DELETE sem backup commitado: sequencia invalida")
        pred, params = scoped_predicate(scopes)
        return int(self._exec(f"DELETE FROM {TABLE} WHERE {pred}", params).rowcount)

    def insert_rows(self, rows: pd.DataFrame) -> None:
        registros = []
        for r in rows[list(bf.DATA_COLS) + ["is_partial"]].to_dict("records"):
            r["source"] = SOURCE
            r["source_run_id"] = self.run_id
            r["is_partial"] = bool(r["is_partial"])
            registros.append(r)
        registros = bf.normalize_records(registros)
        bf.assert_bind_params_clean(registros)
        cols = INSERT_COLS + ("ingested_at",)
        valores = ", ".join("NOW()" if c == "ingested_at" else f":{c}" for c in cols)
        sql = f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({valores})"
        self.emitted.append(sql[:160])
        self.conn.execute(sqlalchemy_text(sql), registros)

    def count_scope(self, scopes) -> int:
        pred, params = scoped_predicate(scopes)
        return int(self._exec(f"SELECT count(*) FROM {TABLE} WHERE {pred}", params).scalar() or 0)

    def commit(self):
        self._trans.commit()
        self._trans = None

    def rollback(self):
        if self._trans is not None:
            self._trans.rollback()
            self._trans = None


def publish_scoped(staging: bf.Staging, rows: pd.DataFrame, *, executor) -> dict:
    """Backup -> transacao unica (relê modo, DELETE manual, INSERT) -> invariantes -> commit.

    Invariantes conferidos DENTRO da transacao, antes do commit:
      1. `source_mode` relido com FOR SHARE imediatamente antes do DELETE;
      2. linhas manuais no escopo == linhas da staging;
      3. impressao digital de TUDO fora do escopo identica a de antes;
      4. impressao digital de todas as linhas `api` identica a de antes.
    Qualquer divergencia -> rollback. ZERO retry."""
    scopes = list(staging.scopes)
    brands = sorted({b for b, _ in scopes})
    backup = executor.backup_scope(scopes)
    executor.begin()
    try:
        executor.recheck_modes(brands)
        fora_antes = outside_fingerprint(executor.conn, scopes)
        api_antes = api_fingerprint(executor.conn)
        apagadas = executor.delete_scope(scopes)
        executor.insert_rows(rows)
        gravadas = executor.count_scope(scopes)
        if gravadas != len(rows):
            raise PublishRefused(f"contagem pos-insert divergente ({gravadas} != {len(rows)})")
        if outside_fingerprint(executor.conn, scopes) != fora_antes:
            raise PublishRefused("linhas FORA do escopo mudaram durante a publicacao: rollback")
        if api_fingerprint(executor.conn) != api_antes:
            raise PublishRefused("linhas `api` mudaram durante a publicacao: rollback")
    except (KeyboardInterrupt, SystemExit):
        try:
            executor.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            executor.rollback()
        except Exception:
            return {"code": EXIT_INDETERMINATE, "backup": backup}
        return {"code": EXIT_ROLLED_BACK, "backup": backup, "erro": bf._sanitize(e)}
    try:
        executor.commit()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return {"code": EXIT_INDETERMINATE, "backup": backup}
    return {"code": EXIT_OK, "backup": backup, "apagadas": apagadas,
            "inseridas": len(rows), "fora_do_escopo": fora_antes, "api": api_antes}


# ---------------------------------------------------------------------------
# Manifesto auditavel e relatorio
# ---------------------------------------------------------------------------

def build_manifest(prep: Prepared, *, run_id: str, mode: str, result: dict | None = None,
                   rec: bf.Reconciliation | None = None) -> dict:
    """Run_id, competencias, hashes de TODOS os arquivos e resultado. Nunca DSN,
    usuario, host ou senha — so dado que ja esta nos arquivos ou foi medido."""
    return {
        "run_id": run_id,
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today_brt": str(prep.today),
        "scopes": [f"{b}:{m}" for b, m in prep.staging.scopes],
        "windows": prep.windows,
        "files": {b: [{"name": f.name, "bytes": f.size, "sha256": f.sha256} for f in fs]
                  for b, fs in prep.files.items()},
        "raw_rows": prep.raw.rows,
        "per_scope_after": {f"{b}:{m}": {"rows": s["rows_after"], "gmv": str(s["gmv_after"]),
                                         "rows_before": s["rows_before"],
                                         "gmv_before": None if s["gmv_before"] is None else str(s["gmv_before"])}
                            for (b, m), s in (rec.per_scope.items() if rec else [])},
        "result": result,
    }


def write_manifest(manifest: dict, audit_dir: Path) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    p = audit_dir / f"publish_manual_export_{manifest['run_id']}_{manifest['mode']}.json"
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return p


def format_report(prep: Prepared, rec: bf.Reconciliation, *, mode: str, run_id: str,
                  daily: dict[str, dict] | None = None, modes: dict | None = None,
                  api_rows_in_scope: int | None = None) -> str:
    L = [f"=== PUBLICADOR manual_export {TABLE} — {mode.upper()} — run_id={run_id} ==="]
    L.append("estado anterior: " + ("lido do Neon (read-only, so source=manual_export)"
                                    if rec.compared else "NAO lido — antes e deltas = N/D"))
    for brand, wins in sorted(prep.windows.items()):
        modo = (modes or {}).get(brand, bf.ND)
        fs = prep.files[brand]
        L.append(f"marca {brand}: procedencia ativa = {modo}; {len(fs)} arquivos, "
                 f"{sum(f.size for f in fs)} bytes; janelas: "
                 + ", ".join(f"{m}={w}" for m, w in wins.items()))
    L.append(f"linhas brutas validadas: {prep.raw.rows}")
    if api_rows_in_scope is not None:
        L.append(f"linhas `api` nos pares pedidos: {api_rows_in_scope} (nao sao tocadas)")
    L.append("competencia | linhas antes -> depois | GMV antes -> depois | delta | "
             "chaves +/- | parcial | GMV diaria (referencia)")
    corrente = f"{prep.today:%Y-%m}"
    for (brand, month), s in sorted(rec.per_scope.items()):
        delta = (bf.ND if s["gmv_before"] is None
                 else bf._fmt((s["gmv_after"] - s["gmv_before"]).quantize(Decimal("0.01"))))
        ref = (daily or {}).get(month)
        ref_txt = bf.ND if ref is None else f"{bf._fmt(ref['gmv'])} (ate {ref['ate']})"
        L.append(f"{brand}:{month} | {bf._fmt(s['rows_before'])} -> {s['rows_after']} | "
                 f"{bf._fmt(s['gmv_before'])} -> {bf._fmt(s['gmv_after'])} | {delta} | "
                 f"+{bf._fmt(s['keys_added'])}/-{bf._fmt(s['keys_removed'])} | "
                 f"{'sim' if month == corrente else 'nao'} | {ref_txt}")
    L.append("GMV da diaria e so referencia de maturacao: populacoes diferentes, "
             "a razao NAO e percentual de conclusao.")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m etl.publish_shopee_manual_export",
        description="Publica exports manuais Shopee (so manual_export) direto no Neon. "
                    "Dry-run por padrao.")
    p.add_argument("--scope", action="append", metavar="marca:AAAA-MM", required=True)
    p.add_argument("--source-root", required=True,
                   help="Pasta NOVA e isolada com <marca>/Order.all.*.xlsx de mes inteiro.")
    p.add_argument("--audit-dir", default="publish_audit",
                   help="Onde gravar o manifesto JSON (run_id, hashes, resultado).")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--offline", action="store_true",
                   help="So arquivos e memoria; nenhum banco. Antes e deltas = N/D.")
    m.add_argument("--apply", action="store_true",
                   help="Escreve. Exige consentimento, credencial dedicada e todas as portas.")
    return p


def _prepare_from_args(args, today: date) -> Prepared:
    scopes = parse_scopes(args.scope, today=today)
    root = bf.resolve_source_root(args.source_root)
    return prepare(scopes, root, today=today)


_REFUSALS = (PublishRefused, bf.BackfillValidationError,
             loader.ShopeeSnapshotError, loader.ShopeeProductInputError)


def run_dry(args, *, env=None, engine_factory=None, today: date | None = None) -> int:
    env = os.environ if env is None else env
    today = today or today_brt()
    run_id = uuid.uuid4().hex
    audit_dir = Path(getattr(args, "audit_dir", "publish_audit"))
    try:
        prep = _prepare_from_args(args, today)
    except _REFUSALS as e:
        print(f"RECUSADO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_REFUSED
    except bf.BackfillUsageError as e:
        print(f"ERRO DE USO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE
    scopes = list(prep.staging.scopes)

    if args.offline:
        rec = bf.reconcile(None, prep.staging)
        print(format_report(prep, rec, mode="offline-dry-run", run_id=run_id))
        p = write_manifest(build_manifest(prep, run_id=run_id, mode="offline", rec=rec,
                                          result={"code": EXIT_OK, "escrita": False}), audit_dir)
        print(f"manifesto: {p}", file=sys.stderr)
        return EXIT_OK

    try:
        url = bf.resolve_target_url(TARGET, env=env)
        engine = (engine_factory or bf._default_readonly_engine)(url, readonly=True)
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            bf.assert_target_identity(conn, TARGET, env=env)
            modes = read_source_modes(conn)
            assert_brands_read_manual(modes, {b for b, _ in scopes})
            before = read_before(conn, scopes)
            pred, params = bf._scope_predicate(scopes)
            api_n = int(conn.execute(sqlalchemy_text(
                f"SELECT count(*) FROM {TABLE} WHERE ({pred}) AND source = 'api'"),
                params).scalar() or 0)
            daily = {}
            for brand in sorted({b for b, _ in scopes}):
                daily.update(read_daily_reference(conn, brand, [m for b, m in scopes if b == brand]))
            conn.rollback()
    except _REFUSALS as e:
        print(f"RECUSADO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_REFUSED
    except bf.BackfillUsageError as e:
        print(f"ERRO DE USO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE

    rec = bf.reconcile(before, prep.staging, compared_target=TARGET)
    print(format_report(prep, rec, mode="dry-run", run_id=run_id, daily=daily, modes=modes,
                        api_rows_in_scope=api_n))
    p = write_manifest(build_manifest(prep, run_id=run_id, mode="dry-run", rec=rec,
                                      result={"code": EXIT_OK, "escrita": False}), audit_dir)
    print(f"manifesto: {p}", file=sys.stderr)
    return EXIT_OK


def run_apply(args, *, env=None, engine_factory=None, today: date | None = None) -> int:
    """Caminho de escrita. So com autorizacao explicita."""
    env = os.environ if env is None else env
    today = today or today_brt()
    run_id = uuid.uuid4().hex
    audit_dir = Path(getattr(args, "audit_dir", "publish_audit"))
    try:
        if env.get(CONSENT_ENV) != "1":
            raise PublishRefused(f"--apply exige {CONSENT_ENV}=1 (nada foi escrito)")
        url = bf.resolve_write_url(TARGET, env=env)
        prep = _prepare_from_args(args, today)
    except _REFUSALS as e:
        print(f"APPLY RECUSADO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_REFUSED
    except bf.BackfillUsageError as e:
        print(f"ERRO DE USO: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE
    scopes = list(prep.staging.scopes)

    try:
        engine = (engine_factory or bf._default_writable_engine)(url)
        conn = engine.connect()
    except Exception as e:
        print(f"ERRO DE CONFIGURACAO: conexao gravavel: {bf._sanitize(e)}", file=sys.stderr)
        return EXIT_USAGE

    lock = False
    resultado: dict = {"code": EXIT_REFUSED}
    rec = None
    try:
        bf.assert_target_identity(conn, TARGET, env=env)
        bf.assert_writable_primary(conn, TARGET)
        bf.assert_sequence_privilege(conn, TARGET)
        bf.assert_ssl_required(conn, TARGET)
        bf.acquire_advisory_lock(conn, key=LOCK_KEY)
        lock = True
        modes = read_source_modes(conn)
        assert_brands_read_manual(modes, {b for b, _ in scopes})
        before = read_before(conn, scopes)
        bf.close_preflight_transaction(conn)
        rec = bf.reconcile(before, prep.staging, compared_target=TARGET)
        print(format_report(prep, rec, mode="apply (antes)", run_id=run_id, modes=modes))
        resultado = publish_scoped(prep.staging, mark_partial(prep.staging, today=today),
                                   executor=ManualExportExecutor(conn, run_id=run_id))
        return resultado["code"]
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        print(f"APPLY RECUSADO: {bf._sanitize(e)}", file=sys.stderr)
        resultado = {"code": EXIT_REFUSED, "erro": bf._sanitize(e)}
        return EXIT_REFUSED
    finally:
        if lock:
            try:
                bf.release_advisory_lock(conn, key=LOCK_KEY)
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass
        p = write_manifest(build_manifest(prep, run_id=run_id, mode="apply", rec=rec,
                                          result=resultado), audit_dir)
        print(f"run_id={run_id} resultado={resultado.get('code')} manifesto={p}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_apply(args) if args.apply else run_dry(args)


if __name__ == "__main__":
    raise SystemExit(main())
