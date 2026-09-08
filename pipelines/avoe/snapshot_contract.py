"""Gate AVH-4A / AVH-4A-R — contrato de leitura dos snapshots manuais da Avoe.

Este modulo NAO fala com banco e NAO fala com a Avoe. Ele le' um diretorio de
snapshot ja capturado, valida manifesto e hashes, e devolve as linhas prontas
para as duas tabelas de destino. Toda regra de elegibilidade, normalizacao e
recusa vive aqui, para que o importador seja so' transacao.

CREDENCIAL: NENHUMA
-------------------
Nao ha leitura de usuario, senha, token ou cookie da Avoe em nenhum ponto. O
snapshot e' um artefato de arquivo; a autenticacao aconteceu no gate que o
gerou e nao e' repetida aqui.

FAIL-CLOSED (AVH-4A-R)
----------------------
O contrato falha em vez de degradar, em todos estes casos:

  * `MANIFEST.sha256` ausente, vazio, malformado, ambiguo ou divergente;
  * `row_count` ausente, nao inteiro, negativo, ou divergente do fisico
    — inclusive quando declarado zero;
  * coluna obrigatoria faltando em qualquer linha;
  * coluna fora do schema declarado (obrigatoria + opcional);
  * duplicata na chave de origem, identica ou conflitante;
  * canal NAO OFICIAL fora da allowlist — bloqueia o snapshot inteiro;
  * timestamp de proveniencia invalido ou sem timezone.

Canal OFICIAL conhecido (TikTok, ML, Shopee) e' o unico descarte contabilizado
e silencioso, porque descartar oficial e' o proposito da tabela de proxy.

O CAMPO `faturamento` DA ORIGEM E' LIDO E DESCARTADO
----------------------------------------------------
`resumo_marca_mes` traz `faturamento` — o realizado que a Avoe digita. Ele
permanece DECLARADO no schema como coluna opcional lida-e-descartada, de
proposito: se a Avoe deixar de envia-lo, ninguem percebe; se ele sair deste
schema sem revisao, alguem pode achar que virou coluna de destino. O realizado
oficial e' da Torre e nunca e' copiado para ca'.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Constantes de contrato
# --------------------------------------------------------------------------

SOURCE = "avoe_hub"

TARGET_TABLE_TARGETS = "marts.proxy_avoe_brand_monthly_target_snapshot"
TARGET_TABLE_CHANNELS = "marts.proxy_avoe_extra_channel_monthly_snapshot"

# Vigencias imutaveis. Espelham os CHECKs da migration 015.
TARGETS_MIN_REF_MONTH = date(2026, 8, 1)
CHANNELS_MIN_BUSINESS_DATE = date(2026, 6, 10)   # inicio do regime diario
CHANNELS_MIN_REF_MONTH = date(2026, 6, 1)

CURRENCY_CODE = "BRL"
CURRENCY_STATUS_ASSUMED = "assumed_unconfirmed"
CURRENCY_WARNING = (
    "Moeda nao declarada pela fonte; BRL inferido pelo contrato do Gate AVH-4A."
)

DEFINITION_STATUS = "unconfirmed"
DEFINITION_WARNING = (
    "Faturamento informado pela Avoe. Definicao nao confirmada pela fonte "
    "(bruto/liquido, cancelados e devolucoes indeterminados). Proxy: nunca "
    "somar ao GMV oficial da Torre."
)

FILE_TARGETS = "resumo_marca_mes.jsonl"
FILE_CHANNELS = "faturamento_diario_marca.jsonl"
ALLOWED_FILES = (FILE_TARGETS, FILE_CHANNELS)

# --------------------------------------------------------------------------
# FINDING 6 — schema de origem: obrigatorio / opcional / proibido
# --------------------------------------------------------------------------
# Obrigatorio: sem a coluna a linha nao pode ser interpretada. Falta => falha.
# Opcional: pode ausentar-se, e a razao esta documentada por coluna.
# Proibido: coluna que, se aparecer, indica que a origem mudou de natureza.

REQUIRED_TARGETS = frozenset({"marca", "mes_referencia", "meta"})
OPTIONAL_TARGETS: dict[str, str] = {
    # PK tecnica da origem; nao viaja para o destino.
    "id": "identificador tecnico da origem, irrelevante para o contrato",
    # Realizado digitado pela Avoe. LIDO E DESCARTADO — ver docstring do modulo.
    "faturamento": "realizado da Avoe: lido e descartado, nunca importado",
    # Proveniencia: a origem pode nao ter carimbo para uma linha antiga.
    "criado_em": "carimbo de criacao na origem; fallback de source_recorded_at",
    "atualizado_em": "carimbo de atualizacao na origem; preferido em source_recorded_at",
}
FORBIDDEN_TARGETS = frozenset({
    # Se qualquer um destes aparecer, a origem passou a mandar realizado
    # apurado ou identificacao de pessoa, e o contrato precisa de revisao.
    "gmv", "realizado", "official_realized_amount", "receita_liquida",
    "usuario", "cliente", "pedido", "cpf", "cnpj", "email",
})

REQUIRED_CHANNELS = frozenset({"marca", "plataforma", "data_referencia"})
OPTIONAL_CHANNELS: dict[str, str] = {
    "id": "identificador tecnico da origem, irrelevante para o contrato",
    # NULL e' legitimo: significa competencia sem valor disponivel.
    "faturamento": "valor informado; NULL legitimo = indisponivel, nunca zero",
    # Coluna de Ads da origem; fora do escopo desta fundacao.
    "ads": "investimento em ads na origem; fora do escopo, nao importado",
    "criado_em": "carimbo de gravacao na origem; usado em source_recorded_at",
}
FORBIDDEN_CHANNELS = frozenset({
    "gmv", "realizado", "official_realized_amount",
    "usuario", "cliente", "pedido", "cpf", "cnpj", "email",
})

# Chave de origem esperada, provada no snapshot de referencia:
# 4.818 linhas => 4.818 chaves distintas, zero duplicata.
SOURCE_KEY_TARGETS = ("mes_referencia", "marca")
SOURCE_KEY_CHANNELS = ("data_referencia", "marca", "plataforma")

# Crosswalk EXPLICITO. Ausencia de regra => brand_key NULL, nunca inferencia.
BRAND_KEY_MAP: dict[str, str | None] = {
    "Apice": "apice",
    "Barbours": "barbours",
    "Kokeshi": "kokeshi",
    "Lescent": "lescent",
    "Rituária": "rituaria",
    "Denavita": "denavita",
    "GoCase": "gocase",
    "Bloom": None,
    "Alfahall": None,
}

# Allowlist de canal adicional. Espelha o CHECK da migration.
CHANNEL_MAP: dict[str, str] = {
    "MAGALU": "magalu",
    "SHEIN": "shein",
    "KWAI": "kwai",
    "BLZ NA WEB": "beleza_na_web",
    "RD MARKETPLACE": "rd_marketplace",
    "AMAZON": "amazon",
}

# Canais oficiais da Torre. Unico descarte contabilizado e nao-fatal.
OFFICIAL_CHANNELS = frozenset({"TIKTOK", "MELI", "SHOPEE"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"[^A-Za-z0-9_.:-]")


class SnapshotContractError(RuntimeError):
    """Falha de contrato. A mensagem nunca carrega credencial nem caminho pessoal."""


# --------------------------------------------------------------------------
# Utilitarios
# --------------------------------------------------------------------------

def sanitize_run_id(raw: str) -> str:
    """Reduz o run id ao alfabeto seguro — nada de caminho ou segredo dentro."""
    limpo = _RUN_ID_RE.sub("-", str(raw)).strip("-")
    if not limpo:
        raise SnapshotContractError("run id vazio depois da sanitizacao.")
    return limpo[:80]


def default_run_id(now: datetime | None = None) -> str:
    agora = now or datetime.now(timezone.utc)
    return sanitize_run_id("avh4a-" + agora.strftime("%Y%m%dT%H%M%S%fZ"))


def sanitize_path(caminho: Path) -> str:
    """So' o nome do arquivo vai para relatorio e log. Nunca o caminho completo."""
    return Path(caminho).name


def file_sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as fh:
        for bloco in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _parse_captured_at(bruto: Any) -> datetime:
    if bruto in (None, ""):
        raise SnapshotContractError(
            "captured_at ausente no manifesto: sem ele nao ha criterio de versao."
        )
    texto = str(bruto).strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError as exc:
        raise SnapshotContractError(f"captured_at invalido: {exc}") from exc
    if dt.tzinfo is None:
        raise SnapshotContractError(
            "captured_at sem timezone: recusado. O contrato nao assume UTC."
        )
    return dt.astimezone(timezone.utc)


def _parse_date(bruto: Any, campo: str) -> date:
    if bruto in (None, ""):
        raise SnapshotContractError(f"{campo} ausente.")
    try:
        return date.fromisoformat(str(bruto)[:10])
    except ValueError as exc:
        raise SnapshotContractError(f"{campo} invalido: {exc}") from exc


def _parse_timestamp_estrito(bruto: Any, campo: str) -> datetime | None:
    """FINDING 8 — timestamp de proveniencia.

    None/vazio => None (ausencia legitima, permitida pelo contrato).
    Texto invalido => FALHA. Nunca vira None silenciosamente.
    Sem timezone => FALHA. O contrato nao assume UTC.
    Com offset => normalizado para UTC.
    """
    if bruto is None or (isinstance(bruto, str) and bruto.strip() == ""):
        return None
    texto = str(bruto).strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError as exc:
        raise SnapshotContractError(
            f"{campo}: timestamp invalido ({exc}). Nao sera convertido em nulo."
        ) from exc
    if dt.tzinfo is None:
        raise SnapshotContractError(
            f"{campo}: timestamp sem timezone. O contrato nao assume UTC."
        )
    return dt.astimezone(timezone.utc)


def _source_recorded_at(obj: dict) -> datetime | None:
    """FINDING 8 — preferencia documentada para metas e canais.

    1) `atualizado_em` valido, quando presente — e' o carimbo mais recente;
    2) senao `criado_em` valido;
    3) senao NULL (ausencia legitima).

    Invalido em qualquer um dos dois FALHA — nao ha degradacao para o proximo.
    """
    atualizado = _parse_timestamp_estrito(obj.get("atualizado_em"), "atualizado_em")
    if atualizado is not None:
        return atualizado
    return _parse_timestamp_estrito(obj.get("criado_em"), "criado_em")


def _parse_decimal(bruto: Any, campo: str) -> Decimal | None:
    """Converte para Decimal. NULL permanece NULL — nunca virou zero."""
    if bruto is None or bruto == "":
        return None
    try:
        valor = Decimal(str(bruto))
    except (InvalidOperation, ValueError) as exc:
        raise SnapshotContractError(f"{campo} nao numerico: {exc}") from exc
    if not valor.is_finite():
        raise SnapshotContractError(f"{campo} nao finito (NaN/Inf) — recusado.")
    if valor < 0:
        raise SnapshotContractError(f"{campo} negativo — recusado pelo contrato.")
    return valor.quantize(Decimal("0.01"))


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _days_in_month(ref_month: date) -> int:
    if ref_month.month == 12:
        proximo = date(ref_month.year + 1, 1, 1)
    else:
        proximo = date(ref_month.year, ref_month.month + 1, 1)
    return (proximo - ref_month).days


# --------------------------------------------------------------------------
# Leitura e validacao do snapshot
# --------------------------------------------------------------------------

@dataclass
class SnapshotFile:
    name: str
    sha256: str
    row_count: int


@dataclass
class ReadResult:
    snapshot_id: str
    captured_at: datetime
    files: dict[str, SnapshotFile]
    target_rows: list[dict] = field(default_factory=list)
    channel_rows: list[dict] = field(default_factory=list)
    rejections: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _validate_manifest_sha(snapshot_dir: Path, mf_path: Path) -> None:
    """FINDING 5 — `MANIFEST.sha256` e' OBRIGATORIO e nao tolera ambiguidade."""
    sha_path = snapshot_dir / "MANIFEST.sha256"
    if not sha_path.is_file():
        raise SnapshotContractError(
            "MANIFEST.sha256 ausente: obrigatorio para provar a integridade do manifesto."
        )
    conteudo = sha_path.read_text(encoding="utf-8").strip()
    if not conteudo:
        raise SnapshotContractError("MANIFEST.sha256 vazio.")
    linhas = [linha for linha in conteudo.splitlines() if linha.strip()]
    if len(linhas) != 1:
        raise SnapshotContractError(
            f"MANIFEST.sha256 com {len(linhas)} entradas: ambiguo, esperada exatamente 1."
        )
    partes = linhas[0].split()
    if not partes:
        raise SnapshotContractError("MANIFEST.sha256 malformado.")
    declarado = partes[0].strip().lower()
    if not _SHA256_RE.match(declarado):
        raise SnapshotContractError(
            "MANIFEST.sha256 malformado: esperado sha256 hexadecimal de 64 caracteres."
        )
    obtido = file_sha256(mf_path)
    if declarado != obtido:
        raise SnapshotContractError(
            "hash do MANIFEST.json divergente de MANIFEST.sha256: manifesto alterado."
        )


def _validate_manifest(snapshot_dir: Path) -> tuple[dict, datetime, dict[str, SnapshotFile]]:
    mf_path = snapshot_dir / "MANIFEST.json"
    if not mf_path.is_file():
        raise SnapshotContractError("MANIFEST.json ausente no diretorio de snapshot.")

    _validate_manifest_sha(snapshot_dir, mf_path)

    try:
        manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotContractError(f"MANIFEST.json ilegivel: {exc}") from exc

    if manifest.get("source_system") != SOURCE:
        raise SnapshotContractError(f"manifesto de outra origem: esperado {SOURCE}.")
    if manifest.get("status") != "OK":
        raise SnapshotContractError(
            f"snapshot com status {manifest.get('status')!r}: somente OK e' importavel."
        )

    captured_at = _parse_captured_at(manifest.get("captured_at"))

    tabelas = manifest.get("tables") or []
    if not tabelas:
        raise SnapshotContractError("manifesto sem tabelas.")

    arquivos: dict[str, SnapshotFile] = {}
    for t in tabelas:
        nome = t.get("file")
        if nome not in ALLOWED_FILES:
            continue  # snapshot pode conter mais tabelas; lemos so' as duas
        if nome in arquivos:
            raise SnapshotContractError(
                f"manifesto declara {nome} mais de uma vez: ambiguo."
            )
        sha = str(t.get("sha256") or "").lower()
        if not _SHA256_RE.match(sha):
            raise SnapshotContractError(f"sha256 malformado para {nome}.")

        # FINDING 5 — row_count obrigatorio, inteiro, >= 0.
        if "row_count" not in t or t["row_count"] is None:
            raise SnapshotContractError(f"{nome}: row_count ausente no manifesto.")
        bruto = t["row_count"]
        if isinstance(bruto, bool) or not isinstance(bruto, int):
            raise SnapshotContractError(
                f"{nome}: row_count deve ser inteiro, recebido {type(bruto).__name__}."
            )
        if bruto < 0:
            raise SnapshotContractError(f"{nome}: row_count negativo ({bruto}).")

        caminho = snapshot_dir / nome
        if not caminho.is_file():
            raise SnapshotContractError(f"arquivo declarado no manifesto e ausente: {nome}.")
        obtido = file_sha256(caminho)
        if obtido != sha:
            raise SnapshotContractError(
                f"hash divergente em {nome}: arquivo alterado depois da captura."
            )
        arquivos[nome] = SnapshotFile(name=nome, sha256=sha, row_count=bruto)

    faltando = [f for f in ALLOWED_FILES if f not in arquivos]
    if faltando:
        raise SnapshotContractError(
            "manifesto nao declara arquivo(s) obrigatorio(s): " + ", ".join(faltando)
        )

    return manifest, captured_at, arquivos


def _snapshot_id(arquivos: dict[str, SnapshotFile], captured_at: datetime) -> str:
    material = json.dumps(
        {n: a.sha256 for n, a in sorted(arquivos.items())}, sort_keys=True
    ) + "|" + captured_at.isoformat()
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _read_jsonl(caminho: Path, obrigatorias: frozenset[str],
                opcionais: frozenset[str], proibidas: frozenset[str]) -> list[dict]:
    """FINDING 6 — valida obrigatoria / opcional / proibida por linha."""
    permitidas = obrigatorias | opcionais
    linhas: list[dict] = []
    with open(caminho, "r", encoding="utf-8") as fh:
        for n, linha in enumerate(fh, start=1):
            linha = linha.strip()
            if not linha:
                continue
            try:
                obj = json.loads(linha)
            except json.JSONDecodeError as exc:
                raise SnapshotContractError(
                    f"{sanitize_path(caminho)} linha {n}: JSON invalido ({exc.msg})."
                ) from exc
            if not isinstance(obj, dict):
                raise SnapshotContractError(
                    f"{sanitize_path(caminho)} linha {n}: objeto esperado."
                )
            presentes = set(obj)
            vetadas = presentes & proibidas
            if vetadas:
                raise SnapshotContractError(
                    f"{sanitize_path(caminho)} linha {n}: coluna PROIBIDA na origem: "
                    f"{', '.join(sorted(vetadas))}. A origem mudou de natureza."
                )
            faltando = obrigatorias - presentes
            if faltando:
                raise SnapshotContractError(
                    f"{sanitize_path(caminho)} linha {n}: coluna obrigatoria ausente: "
                    f"{', '.join(sorted(faltando))}."
                )
            extras = presentes - permitidas
            if extras:
                raise SnapshotContractError(
                    f"{sanitize_path(caminho)} linha {n}: coluna de origem fora do "
                    f"schema declarado: {', '.join(sorted(extras))}. Um campo novo na "
                    f"fonte exige revisao do contrato antes de entrar."
                )
            linhas.append(obj)
    return linhas


def _assert_chave_unica(linhas: list[dict], chave: tuple[str, ...], rotulo: str) -> None:
    """FINDING 3 — duplicata na chave de origem FALHA, identica ou conflitante.

    Regra conservadora: a origem nao documenta duplicatas, e o snapshot de
    referencia nao tem nenhuma (4.818 linhas, 4.818 chaves). Somar duas linhas
    da mesma chave silenciosamente seria inventar um valor; deduplicar
    implicitamente seria escolher um lado sem regra. Portanto: falha.
    """
    vistos: dict[tuple, dict] = {}
    for obj in linhas:
        k = tuple(str(obj.get(c)) for c in chave)
        anterior = vistos.get(k)
        if anterior is None:
            vistos[k] = obj
            continue
        iguais = all(anterior.get(c) == obj.get(c)
                     for c in set(anterior) | set(obj) if c != "id")
        tipo = "IDENTICA" if iguais else "CONFLITANTE"
        raise SnapshotContractError(
            f"{rotulo}: duplicata {tipo} na chave de origem "
            f"({' x '.join(chave)}) = ({', '.join(k)}). O contrato nao soma nem "
            f"deduplica silenciosamente."
        )


# --------------------------------------------------------------------------
# Construcao das linhas de destino
# --------------------------------------------------------------------------

def _build_targets(
    origem: list[dict], captured_at: datetime, arquivo: SnapshotFile,
    snapshot_id: str, run_id: str, rejections: list[dict],
) -> list[dict]:
    saida: list[dict] = []

    for obj in origem:
        marca = str(obj.get("marca") or "").strip()
        if not marca:
            rejections.append({"dataset": "targets", "motivo": "marca vazia"})
            continue

        ref_month = _month_start(_parse_date(obj.get("mes_referencia"), "mes_referencia"))

        if ref_month < TARGETS_MIN_REF_MONTH:
            rejections.append({
                "dataset": "targets", "brand": marca, "ref_month": ref_month.isoformat(),
                "motivo": f"competencia anterior a {TARGETS_MIN_REF_MONTH.isoformat()} "
                          f"(escala incompativel, AVH-3B)",
            })
            continue

        meta = _parse_decimal(obj.get("meta"), "meta")
        if meta is None:
            rejections.append({
                "dataset": "targets", "brand": marca, "ref_month": ref_month.isoformat(),
                "motivo": "meta nula — nao ha default, e nulo nao vira zero",
            })
            continue

        # `faturamento` da origem NAO e' lido para o destino. Nao existe coluna.
        saida.append({
            "source": SOURCE,
            "captured_at": captured_at,
            "ref_month": ref_month,
            "brand": marca,
            "brand_key": BRAND_KEY_MAP.get(marca),
            "target_amount": meta,
            "currency_code": CURRENCY_CODE,
            "currency_status": CURRENCY_STATUS_ASSUMED,
            "currency_warning": CURRENCY_WARNING,
            "source_recorded_at": _source_recorded_at(obj),
            "source_file": arquivo.name,
            "source_file_hash": arquivo.sha256,
            "snapshot_id": snapshot_id,
            "import_run_id": run_id,
        })

    return sorted(saida, key=lambda r: (r["ref_month"], r["brand"]))


def _build_channels(
    origem: list[dict], captured_at: datetime, arquivo: SnapshotFile,
    snapshot_id: str, run_id: str, rejections: list[dict],
) -> list[dict]:
    # Agrega diario -> mensal por (ref_month, brand, channel). A agregacao e'
    # legitima porque o AVH-3B provou que o campo e' incremento diario a partir
    # de 2026-06-10 (CV de 44% a 514%, 45-67% de dias nao-decrescentes) e a
    # chave de origem e' unica (FINDING 3, verificada antes desta funcao).
    baldes: dict[tuple[date, str, str], dict] = {}
    ignorados_oficiais = 0
    ignorados_regime = 0

    for obj in origem:
        plataforma = str(obj.get("plataforma") or "").strip().upper()
        if plataforma in OFFICIAL_CHANNELS:
            ignorados_oficiais += 1
            continue
        canal = CHANNEL_MAP.get(plataforma)
        if canal is None:
            # FINDING 7 — canal NAO OFICIAL desconhecido bloqueia o snapshot.
            raise SnapshotContractError(
                f"canal desconhecido na origem: {plataforma!r}. Nao esta na allowlist "
                f"de canais adicionais nem na lista de canais oficiais. O snapshot "
                f"inteiro e' recusado — nenhuma linha e' importada — ate que o "
                f"contrato decida o destino desse canal."
            )

        business_date = _parse_date(obj.get("data_referencia"), "data_referencia")
        if business_date < CHANNELS_MIN_BUSINESS_DATE:
            ignorados_regime += 1
            continue

        marca = str(obj.get("marca") or "").strip()
        if not marca:
            rejections.append({"dataset": "channels", "motivo": "marca vazia"})
            continue

        valor = _parse_decimal(obj.get("faturamento"), "faturamento")
        recorded = _source_recorded_at(obj)
        ref_month = _month_start(business_date)
        chave = (ref_month, marca, canal)
        balde = baldes.setdefault(chave, {
            "soma": None, "dias": set(), "primeira": business_date,
            "ultima": business_date, "label": str(obj.get("plataforma")).strip(),
            "recorded": recorded,
        })
        # NULL na origem nao contamina a soma nem virou zero: o dia entra na
        # cobertura, mas so' valores presentes somam. Se NENHUM dia tiver valor,
        # a soma permanece NULL.
        if valor is not None:
            balde["soma"] = valor if balde["soma"] is None else balde["soma"] + valor
        balde["dias"].add(business_date)
        balde["primeira"] = min(balde["primeira"], business_date)
        balde["ultima"] = max(balde["ultima"], business_date)
        if recorded is not None and (balde["recorded"] is None or recorded > balde["recorded"]):
            balde["recorded"] = recorded

    saida: list[dict] = []
    for (ref_month, marca, canal), b in baldes.items():
        if ref_month < CHANNELS_MIN_REF_MONTH:
            rejections.append({
                "dataset": "channels", "brand": marca, "channel": canal,
                "ref_month": ref_month.isoformat(), "motivo": "competencia fora da vigencia",
            })
            continue
        dias = len(b["dias"])
        cobertura = "full_month" if dias >= _days_in_month(ref_month) else "partial_month"
        saida.append({
            "source": SOURCE,
            "captured_at": captured_at,
            "ref_month": ref_month,
            "brand": marca,
            "channel": canal,
            "brand_key": BRAND_KEY_MAP.get(marca),
            "channel_source_label": b["label"],
            "reported_amount": b["soma"],
            "is_proxy": True,
            "definition_status": DEFINITION_STATUS,
            "definition_warning": DEFINITION_WARNING,
            "currency_code": CURRENCY_CODE,
            "currency_status": CURRENCY_STATUS_ASSUMED,
            "days_covered": dias,
            "first_business_date": b["primeira"],
            "last_business_date": b["ultima"],
            "coverage_status": cobertura,
            "source_recorded_at": b["recorded"],
            "source_file": arquivo.name,
            "source_file_hash": arquivo.sha256,
            "snapshot_id": snapshot_id,
            "import_run_id": run_id,
        })

    saida.sort(key=lambda r: (r["ref_month"], r["channel"], r["brand"]))
    rejections.append({
        "dataset": "channels", "motivo": "resumo de descartes",
        "canais_oficiais_ignorados": ignorados_oficiais,
        "linhas_fora_do_regime_diario": ignorados_regime,
    })
    return saida


def read_snapshot(snapshot_dir: Path, run_id: str | None = None) -> ReadResult:
    """Le, valida e monta as linhas dos dois datasets. Nao escreve nada."""
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.is_dir():
        raise SnapshotContractError("--snapshot-dir nao e' um diretorio.")

    _, captured_at, arquivos = _validate_manifest(snapshot_dir)
    run = sanitize_run_id(run_id) if run_id else default_run_id()
    snapshot_id = _snapshot_id(arquivos, captured_at)

    rejections: list[dict] = []

    brutos_targets = _read_jsonl(
        snapshot_dir / FILE_TARGETS, REQUIRED_TARGETS,
        frozenset(OPTIONAL_TARGETS), FORBIDDEN_TARGETS,
    )
    brutos_channels = _read_jsonl(
        snapshot_dir / FILE_CHANNELS, REQUIRED_CHANNELS,
        frozenset(OPTIONAL_CHANNELS), FORBIDDEN_CHANNELS,
    )

    # FINDING 5 — row_count sempre comparado, inclusive quando declarado zero.
    for nome, lidas in ((FILE_TARGETS, brutos_targets), (FILE_CHANNELS, brutos_channels)):
        declarado = arquivos[nome].row_count
        if declarado != len(lidas):
            raise SnapshotContractError(
                f"{nome}: manifesto declara {declarado} linhas, arquivo tem {len(lidas)}."
            )

    # FINDING 3 — chave de origem antes de qualquer agregacao.
    _assert_chave_unica(brutos_targets, SOURCE_KEY_TARGETS, FILE_TARGETS)
    _assert_chave_unica(brutos_channels, SOURCE_KEY_CHANNELS, FILE_CHANNELS)

    target_rows = _build_targets(
        brutos_targets, captured_at, arquivos[FILE_TARGETS], snapshot_id, run, rejections,
    )
    channel_rows = _build_channels(
        brutos_channels, captured_at, arquivos[FILE_CHANNELS], snapshot_id, run, rejections,
    )

    _assert_contract(target_rows, channel_rows)

    return ReadResult(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        files=arquivos,
        target_rows=target_rows,
        channel_rows=channel_rows,
        rejections=rejections,
        stats={
            "run_id": run,
            "targets_lidos": len(brutos_targets),
            "targets_elegiveis": len(target_rows),
            "channels_lidos": len(brutos_channels),
            "channels_elegiveis": len(channel_rows),
        },
    )


def _assert_contract(target_rows: list[dict], channel_rows: list[dict]) -> None:
    """Ultima barreira antes do banco. Espelha os CHECKs da migration 015."""
    proibidas = {"faturamento", "realizado", "gmv", "official_realized_amount"}

    for r in target_rows:
        vazando = proibidas & set(r)
        if vazando:
            raise SnapshotContractError(
                f"coluna de realizado vazando para metas: {', '.join(sorted(vazando))}."
            )
        if r["ref_month"] < TARGETS_MIN_REF_MONTH:
            raise SnapshotContractError("meta anterior a vigencia escapou do filtro.")
        if r["ref_month"].day != 1:
            raise SnapshotContractError("ref_month nao truncado no mes.")
        if r["currency_status"] == CURRENCY_STATUS_ASSUMED and not r["currency_warning"]:
            raise SnapshotContractError("moeda assumida sem warning.")
        if r["target_amount"] is None or r["target_amount"] < 0:
            raise SnapshotContractError("target_amount nulo ou negativo.")

    for r in channel_rows:
        vazando = proibidas & set(r)
        if vazando:
            raise SnapshotContractError(
                f"coluna de realizado vazando para canais: {', '.join(sorted(vazando))}."
            )
        if r["channel"] not in set(CHANNEL_MAP.values()):
            raise SnapshotContractError(f"canal fora da allowlist: {r['channel']}.")
        if r["is_proxy"] is not True:
            raise SnapshotContractError("is_proxy deve ser True.")
        if r["definition_status"] != DEFINITION_STATUS:
            raise SnapshotContractError("definition_status deve ser unconfirmed.")
        if not r["definition_warning"]:
            raise SnapshotContractError("definition_warning obrigatorio.")
        if r["days_covered"] < 1:
            raise SnapshotContractError("days_covered deve ser >= 1.")
        if not (r["ref_month"] <= r["first_business_date"] <= r["last_business_date"]):
            raise SnapshotContractError("datas de cobertura fora da competencia.")
        if r["reported_amount"] is not None and r["reported_amount"] < 0:
            raise SnapshotContractError("reported_amount negativo.")

    chaves_t = {(r["source"], r["captured_at"], r["ref_month"], r["brand"]) for r in target_rows}
    if len(chaves_t) != len(target_rows):
        raise SnapshotContractError("PK duplicada em metas.")
    chaves_c = {
        (r["source"], r["captured_at"], r["ref_month"], r["brand"], r["channel"])
        for r in channel_rows
    }
    if len(chaves_c) != len(channel_rows):
        raise SnapshotContractError("PK duplicada em canais adicionais.")


# --------------------------------------------------------------------------
# FINDING 2 — snapshot corrente ATOMICO, por (source, ref_month)
# --------------------------------------------------------------------------

def select_current_version(rows: list[dict]) -> list[dict]:
    """Versao corrente = maior `captured_at` valido por `(source, ref_month)`.

    A unidade e' a CAPTURA, nao a marca nem o canal. Escolhido o `captured_at`
    da competencia, devolve EXCLUSIVAMENTE as linhas daquela captura. Uma marca
    ou canal que existia na captura anterior e desapareceu na nova NAO e'
    ressuscitada — a ausencia na captura corrente e' informacao, nao lacuna a
    preencher.

    Nunca decide por `imported_at`. Empate de `captured_at` na mesma competencia
    com conteudo divergente FALHA de forma explicita.
    """
    if not rows:
        return []

    por_competencia: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("captured_at") is None:
            raise SnapshotContractError(
                "linha sem captured_at: selecao de versao exige o carimbo da origem."
            )
        por_competencia.setdefault((r.get("source"), r["ref_month"]), []).append(r)

    saida: list[dict] = []
    for chave, grupo in sorted(por_competencia.items(), key=lambda kv: str(kv[0])):
        maximo = max(r["captured_at"] for r in grupo)
        da_captura = [r for r in grupo if r["captured_at"] == maximo]

        # Empate real = mesma captura declarando a mesma linha de negocio duas
        # vezes com conteudo diferente. `snapshot_id` diferente sob o mesmo
        # `captured_at` tambem e' conflito.
        ids = {r.get("snapshot_id") for r in da_captura if "snapshot_id" in r}
        if len(ids) > 1:
            raise SnapshotContractError(
                f"empate de captured_at com snapshot_id divergente em {chave}: "
                f"conflito explicito, nenhum lado escolhido."
            )
        saida.extend(da_captura)

    return saida
