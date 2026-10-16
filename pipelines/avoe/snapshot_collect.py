"""Gate AVH-5B — coletor read-only de snapshot da Avoe Hub.

Este modulo produz os quatro artefatos que `snapshot_contract.py` sabe ler:

    MANIFEST.json
    MANIFEST.sha256
    resumo_marca_mes.jsonl
    faturamento_diario_marca.jsonl

Ele NAO importa nada, NAO fala com Neon nem com o Data Mart, e NAO modifica o
contrato nem o importador. A fronteira e' o diretorio de captura.

CREDENCIAL: NENHUMA ATRAVESSA ESTE MODULO
-----------------------------------------
O coletor nao aceita usuario, senha, token, cookie, apikey nem storage state —
por argumento, por ambiente ou por arquivo. Ele recebe uma sessao de navegador
JA autenticada e le' atraves dela.

A leitura acontece DENTRO da pagina: o JavaScript de `_JS_READER` monta a
requisicao usando os globais que a propria aplicacao ja tem em memoria, e
devolve ao Python apenas `{status, rows, content_range}`. Nenhum cabecalho de
autenticacao e' retornado, e portanto nenhum pode ser registrado, serializado
ou persistido deste lado. Ha teste que falha se o envelope crescer um campo.

SOMENTE LEITURA
---------------
`install_read_only_guard()` instala um bloqueio de rede na sessao: POST, PUT,
PATCH e DELETE sao abortados antes de sair do navegador, para QUALQUER destino.
O guard e' armado depois do login, entao a requisicao de autenticacao ja
aconteceu e nao precisa de excecao — nao existe excecao. O contador de
bloqueios viaja para o manifesto como evidencia.

FAIL-CLOSED
-----------
A coleta falha, sem gravar arquivo nenhum, em todos estes casos:

  * HTTP != 200 em qualquer pagina (401, 403, 429, 5xx, qualquer um);
  * resposta que nao e' lista de objetos;
  * resposta truncada (mais linhas do que o limite pedido);
  * `Content-Range` ausente, malformado, ou com total que muda no meio;
  * pagina repetida (mesmo conjunto de `id` de uma pagina anterior);
  * pagina ausente (a fonte devolve menos do que o total declarado);
  * coluna obrigatoria ausente, coluna nova, ou coluna proibida;
  * duplicata na chave de origem;
  * `id` ausente ou duplicado (a paginacao por offset perde a ancora);
  * diretorio de saida dentro de um repositorio git.

Nada e' escrito antes de a coleta inteira passar. Um erro no meio do caminho
deixa o diretorio de saida como estava.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from pipelines.avoe.snapshot_contract import (
    FILE_CHANNELS,
    FILE_TARGETS,
    FORBIDDEN_CHANNELS,
    FORBIDDEN_TARGETS,
    OPTIONAL_CHANNELS,
    OPTIONAL_TARGETS,
    REQUIRED_CHANNELS,
    REQUIRED_TARGETS,
    SOURCE,
    SOURCE_KEY_CHANNELS,
    SOURCE_KEY_TARGETS,
    file_sha256,
)

GATE = "AVH-5B"
COLLECTOR_VERSION = "1"

# Teto do servidor, documentado no proprio bundle da Avoe: pedir mais que isso
# nao adianta, o PostgREST corta de volta. Pedimos exatamente o teto para que
# "pagina curta" seja sinal confiavel de fim de tabela.
SOURCE_PAGE_SIZE = 1000

# Teto de paginas por tabela. Existe para que um `Content-Range` mentindo o
# total nao vire laco infinito.
MAX_PAGES = 200

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# O PostgREST devolve 206 quando a faixa pedida nao cobre a tabela inteira e a
# contagem e' exata. Os dois status significam "pagina integra"; qualquer outro
# — 401, 403, 429, 5xx — para a coleta.
STATUS_ACEITOS = frozenset({200, 206})

# Status sinteticos do leitor de navegador. Nao sao HTTP: distinguem "a fonte
# respondeu errado" de "a leitura nem chegou a acontecer".
FALHAS_DE_LEITOR: dict[int, str] = {
    -1: "a sessao do navegador nao expoe a aplicacao autenticada "
        "(globais de leitura ausentes). Faca o login antes de coletar.",
    -2: "falha de rede dentro do navegador; nenhuma resposta foi recebida.",
    -3: "corpo da resposta nao e' JSON.",
}

_CONTENT_RANGE_RE = re.compile(r"^\s*(?:\*|(\d+)-(\d+))\s*/\s*(\d+)\s*$")


class SnapshotCollectError(RuntimeError):
    """Falha de coleta. A mensagem nunca carrega credencial nem linha de dado."""


# --------------------------------------------------------------------------
# Especificacao por tabela — o schema vem do contrato, nao e' redigitado aqui
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TableSpec:
    table: str
    file_name: str
    required: frozenset[str]
    optional: frozenset[str]
    forbidden: frozenset[str]
    source_key: tuple[str, ...]

    @property
    def allowed(self) -> frozenset[str]:
        return self.required | self.optional


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        table="resumo_marca_mes",
        file_name=FILE_TARGETS,
        required=REQUIRED_TARGETS,
        optional=frozenset(OPTIONAL_TARGETS),
        forbidden=FORBIDDEN_TARGETS,
        source_key=SOURCE_KEY_TARGETS,
    ),
    TableSpec(
        table="faturamento_diario_marca",
        file_name=FILE_CHANNELS,
        required=REQUIRED_CHANNELS,
        optional=frozenset(OPTIONAL_CHANNELS),
        forbidden=FORBIDDEN_CHANNELS,
        source_key=SOURCE_KEY_CHANNELS,
    ),
)


# --------------------------------------------------------------------------
# Envelope de leitura — o unico formato que atravessa a fronteira navegador/Python
# --------------------------------------------------------------------------

# FAIL-CLOSED de vazamento: o envelope tem exatamente estes tres campos. Se um
# dia alguem devolver `headers`, `token` ou `apikey` junto, a validacao abaixo
# recusa a pagina inteira em vez de aceitar de favor.
ENVELOPE_FIELDS = frozenset({"status", "rows", "content_range"})


@dataclass(frozen=True)
class RawPage:
    status: int
    rows: list[dict]
    content_range: str | None


class PageReader(Protocol):
    """Contrato minimo de leitura. Nao ha metodo de escrita, de proposito."""

    def fetch(self, table: str, offset: int, limit: int) -> dict:
        """Devolve o envelope cru: {status, rows, content_range}."""


def _valida_envelope(bruto: Any, table: str, offset: int) -> RawPage:
    if not isinstance(bruto, dict):
        raise SnapshotCollectError(
            f"{table} offset={offset}: leitor devolveu {type(bruto).__name__}, "
            f"esperado envelope."
        )
    extras = set(bruto) - ENVELOPE_FIELDS
    if extras:
        raise SnapshotCollectError(
            f"{table} offset={offset}: envelope com campo fora do contrato "
            f"({', '.join(sorted(extras))}). O leitor so' pode devolver "
            f"status, rows e content_range — nunca cabecalho."
        )
    faltando = ENVELOPE_FIELDS - set(bruto)
    if faltando:
        raise SnapshotCollectError(
            f"{table} offset={offset}: envelope incompleto, falta "
            f"{', '.join(sorted(faltando))}."
        )

    status = bruto["status"]
    if isinstance(status, bool) or not isinstance(status, int):
        raise SnapshotCollectError(f"{table} offset={offset}: status nao inteiro.")
    if status in FALHAS_DE_LEITOR:
        raise SnapshotCollectError(
            f"{table} offset={offset}: {FALHAS_DE_LEITOR[status]}"
        )
    # 206 e' o normal do PostgREST com `Prefer: count=exact` e faixa parcial.
    if status not in STATUS_ACEITOS:
        raise SnapshotCollectError(
            f"{table} offset={offset}: HTTP {status}. A coleta para — um "
            f"snapshot parcial nao e' snapshot."
        )

    linhas = bruto["rows"]
    if not isinstance(linhas, list):
        raise SnapshotCollectError(
            f"{table} offset={offset}: corpo nao e' lista de objetos."
        )
    for n, obj in enumerate(linhas):
        if not isinstance(obj, dict):
            raise SnapshotCollectError(
                f"{table} offset={offset} item {n}: objeto esperado, "
                f"recebido {type(obj).__name__}."
            )

    cr = bruto["content_range"]
    if cr is not None and not isinstance(cr, str):
        raise SnapshotCollectError(
            f"{table} offset={offset}: content_range de tipo invalido."
        )
    return RawPage(status=status, rows=linhas, content_range=cr)


def _parse_total(content_range: str | None, table: str, offset: int) -> int:
    """`Content-Range: 0-999/4818` -> 4818.

    O total e' obrigatorio: sem ele nao ha como distinguir "acabou" de
    "a fonte parou de responder no meio".
    """
    if not content_range:
        raise SnapshotCollectError(
            f"{table} offset={offset}: Content-Range ausente. Sem total "
            f"declarado nao se prova que a pagina nao esta faltando."
        )
    m = _CONTENT_RANGE_RE.match(content_range)
    if not m:
        raise SnapshotCollectError(
            f"{table} offset={offset}: Content-Range malformado."
        )
    return int(m.group(3))


# --------------------------------------------------------------------------
# Validacao de schema e de chave
# --------------------------------------------------------------------------

def _valida_schema(linhas: list[dict], spec: TableSpec, offset: int) -> None:
    for n, obj in enumerate(linhas):
        presentes = set(obj)

        vetadas = presentes & spec.forbidden
        if vetadas:
            raise SnapshotCollectError(
                f"{spec.table} offset={offset} item {n}: coluna PROIBIDA na "
                f"origem: {', '.join(sorted(vetadas))}. A origem mudou de "
                f"natureza — o snapshot nao e' capturado."
            )

        faltando = spec.required - presentes
        if faltando:
            raise SnapshotCollectError(
                f"{spec.table} offset={offset} item {n}: coluna obrigatoria "
                f"ausente: {', '.join(sorted(faltando))}."
            )

        novas = presentes - spec.allowed
        if novas:
            raise SnapshotCollectError(
                f"{spec.table} offset={offset} item {n}: coluna nova na origem: "
                f"{', '.join(sorted(novas))}. O contrato do Gate AVH-4A precisa "
                f"ser revisto antes que esse campo entre em qualquer captura."
            )


def _valida_ancora(linhas: list[dict], spec: TableSpec) -> None:
    """`id` e' a ancora da paginacao por offset. Sem ela, offset nao ordena."""
    vistos: set[str] = set()
    for obj in linhas:
        if "id" not in obj or obj["id"] is None:
            raise SnapshotCollectError(
                f"{spec.table}: linha sem `id`. A paginacao por offset depende "
                f"de ordenacao estavel por `id`; sem ancora a captura nao e' "
                f"reproduzivel."
            )
        chave = str(obj["id"])
        if chave in vistos:
            raise SnapshotCollectError(
                f"{spec.table}: `id` repetido na captura. A fonte devolveu a "
                f"mesma linha em paginas diferentes."
            )
        vistos.add(chave)


def _valida_chave_de_origem(linhas: list[dict], spec: TableSpec) -> None:
    """Mesma regra do contrato: duplicata de chave FALHA, identica ou nao."""
    vistos: set[tuple[str, ...]] = set()
    for obj in linhas:
        k = tuple(str(obj.get(c)) for c in spec.source_key)
        if k in vistos:
            raise SnapshotCollectError(
                f"{spec.table}: duplicata na chave de origem "
                f"({' x '.join(spec.source_key)}). O coletor nao soma nem "
                f"deduplica — e o importador tambem nao aceitaria."
            )
        vistos.add(k)


# --------------------------------------------------------------------------
# Paginacao
# --------------------------------------------------------------------------

@dataclass
class TableCapture:
    spec: TableSpec
    rows: list[dict]
    pages: int
    declared_total: int
    columns: list[str] = field(default_factory=list)


def collect_table(reader: PageReader, spec: TableSpec,
                  page_size: int = SOURCE_PAGE_SIZE) -> TableCapture:
    """Le a tabela inteira, pagina a pagina, e falha em vez de degradar."""
    if page_size < 1:
        raise SnapshotCollectError("page_size deve ser >= 1.")

    linhas: list[dict] = []
    assinaturas: list[frozenset[str]] = []
    total_declarado: int | None = None
    offset = 0
    paginas = 0

    while True:
        if paginas >= MAX_PAGES:
            raise SnapshotCollectError(
                f"{spec.table}: teto de {MAX_PAGES} paginas atingido sem fechar "
                f"o total declarado. A fonte nao esta convergindo."
            )

        pagina = _valida_envelope(reader.fetch(spec.table, offset, page_size),
                                  spec.table, offset)
        paginas += 1

        total = _parse_total(pagina.content_range, spec.table, offset)
        if total_declarado is None:
            total_declarado = total
        elif total != total_declarado:
            raise SnapshotCollectError(
                f"{spec.table}: total declarado mudou no meio da coleta "
                f"({total_declarado} -> {total}). A fonte foi escrita durante "
                f"a captura; o snapshot seria uma mistura de dois instantes."
            )

        if len(pagina.rows) > page_size:
            raise SnapshotCollectError(
                f"{spec.table} offset={offset}: resposta truncada ao contrario "
                f"— {len(pagina.rows)} linhas para um limite de {page_size}."
            )

        _valida_schema(pagina.rows, spec, offset)

        # Pagina repetida: mesmo conjunto de `id` de alguma pagina anterior.
        if pagina.rows:
            assinatura = frozenset(
                str(obj["id"]) for obj in pagina.rows if obj.get("id") is not None
            )
            if len(assinatura) != len(pagina.rows):
                raise SnapshotCollectError(
                    f"{spec.table} offset={offset}: pagina com `id` ausente ou "
                    f"repetido dentro dela mesma."
                )
            if assinatura in assinaturas:
                raise SnapshotCollectError(
                    f"{spec.table} offset={offset}: pagina REPETIDA — a fonte "
                    f"devolveu o mesmo conjunto de linhas de uma pagina "
                    f"anterior. O offset nao esta avancando."
                )
            assinaturas.append(assinatura)

        linhas.extend(pagina.rows)

        if len(pagina.rows) < page_size:
            break
        offset += page_size

    assert total_declarado is not None  # a primeira pagina sempre define

    # Zero linha NAO e' captura vazia: e' captura sem permissao de leitura.
    # Com RLS, o PostgREST responde 200 com `[]` e total 0 tanto para "a
    # tabela esta vazia" quanto para "esta sessao nao enxerga nada". As duas
    # tabelas desta fonte tem milhares de linhas e nunca esvaziam, entao a
    # segunda leitura e' a unica plausivel — e ela nao pode virar snapshot.
    if not linhas:
        raise SnapshotCollectError(
            f"{spec.table}: zero linha. A fonte respondeu sem erro e sem dado, "
            f"o que e' o sintoma de sessao nao autenticada ou sem permissao de "
            f"leitura — nao de tabela vazia. A captura e' recusada."
        )

    if len(linhas) != total_declarado:
        raise SnapshotCollectError(
            f"{spec.table}: pagina AUSENTE — {len(linhas)} linhas lidas para um "
            f"total declarado de {total_declarado}."
        )

    _valida_ancora(linhas, spec)
    _valida_chave_de_origem(linhas, spec)

    colunas = sorted({c for obj in linhas for c in obj})
    return TableCapture(spec=spec, rows=linhas, pages=paginas,
                        declared_total=total_declarado, columns=colunas)


# --------------------------------------------------------------------------
# Serializacao deterministica
# --------------------------------------------------------------------------

def _chave_ordenacao(obj: dict, spec: TableSpec) -> tuple:
    """Ordem total e estavel: chave de origem, com `id` como desempate final.

    Tudo vira texto antes de comparar. A fonte mistura tipos entre linhas
    (numero e string na mesma coluna), e comparar tipos diferentes levantaria
    TypeError no meio da captura.
    """
    return tuple(str(obj.get(c)) for c in spec.source_key) + (str(obj.get("id")),)


def render_jsonl(rows: list[dict], spec: TableSpec) -> str:
    """JSONL canonico: ordem de linhas fixa, chaves ordenadas, LF, sem BOM.

    Duas capturas do mesmo conteudo produzem byte a byte o mesmo arquivo,
    qualquer que tenha sido a ordem em que a fonte devolveu as paginas.
    """
    ordenadas = sorted(rows, key=lambda o: _chave_ordenacao(o, spec))
    partes = [
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for obj in ordenadas
    ]
    return "".join(p + "\n" for p in partes)


def _escreve_texto(caminho: Path, conteudo: str) -> None:
    # newline="" impede que o Windows troque \n por \r\n: o hash de uma captura
    # nao pode depender do sistema operacional de quem capturou.
    with open(caminho, "w", encoding="utf-8", newline="") as fh:
        fh.write(conteudo)


def _sha256_texto(conteudo: str) -> str:
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Destino da captura
# --------------------------------------------------------------------------

def assert_fora_do_repositorio(out_dir: Path) -> None:
    """Uma captura real nunca cai dentro de um repositorio git.

    O dado da Avoe e' de terceiro e nao e' versionavel. Escrever dentro da
    arvore de trabalho e' como um `git add` acidental acontece.
    """
    atual = Path(out_dir).resolve()
    for candidato in (atual, *atual.parents):
        if (candidato / ".git").exists():
            raise SnapshotCollectError(
                "diretorio de captura dentro de um repositorio git: recusado. "
                "Use o scratchpad da sessao ou outro diretorio fora da arvore "
                "de trabalho."
            )


# --------------------------------------------------------------------------
# Manifesto
# --------------------------------------------------------------------------

def build_manifest(capturas: list[TableCapture], arquivos: dict[str, dict],
                   captured_at: datetime, read_method: str,
                   blocked_mutations: int) -> dict:
    if captured_at.tzinfo is None:
        raise SnapshotCollectError(
            "captured_at sem timezone: o contrato recusaria o manifesto."
        )
    utc = captured_at.astimezone(timezone.utc)
    return {
        "source_system": SOURCE,
        "status": "OK",
        "captured_at": utc.isoformat(),
        "collector": {
            "gate": GATE,
            "version": COLLECTOR_VERSION,
            "read_method": read_method,
            "page_size": SOURCE_PAGE_SIZE,
            "credentials_in_artifact": "none",
            "blocked_mutating_requests": blocked_mutations,
        },
        "tables": [
            {
                "table": c.spec.table,
                "file": c.spec.file_name,
                "sha256": arquivos[c.spec.file_name]["sha256"],
                "row_count": len(c.rows),
                "declared_total": c.declared_total,
                "pages": c.pages,
                "columns": c.columns,
            }
            for c in capturas
        ],
    }


def render_manifest(manifest: dict) -> str:
    return json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------
# Orquestracao
# --------------------------------------------------------------------------

@dataclass
class CollectResult:
    out_dir: Path
    captured_at: datetime
    manifest: dict
    files: dict[str, dict]
    blocked_mutations: int

    def resumo_sanitizado(self) -> list[str]:
        """Somente agregados. Nunca uma linha, nunca um valor individual."""
        linhas = [
            f"captured_at : {self.captured_at.astimezone(timezone.utc).isoformat()}",
            f"destino     : {self.out_dir.name}  (fora do repositorio)",
            f"mutacoes bloqueadas pelo guard: {self.blocked_mutations}",
        ]
        for t in self.manifest["tables"]:
            linhas.append(
                f"  {t['file']:<32} {t['row_count']:>6} linhas  "
                f"{t['pages']:>3} pag  sha256={t['sha256'][:16]}..."
            )
            linhas.append(f"      colunas: {', '.join(t['columns'])}")
        for nome in ("MANIFEST.json", "MANIFEST.sha256"):
            linhas.append(
                f"  {nome:<32} {'':>6}         sha256="
                f"{self.files[nome]['sha256'][:16]}..."
            )
        return linhas


def collect_snapshot(
    reader: PageReader,
    out_dir: Path,
    *,
    now: Callable[[], datetime] | None = None,
    read_method: str = "browser_session_get",
    blocked_mutations: int = 0,
    page_size: int = SOURCE_PAGE_SIZE,
    specs: tuple[TableSpec, ...] = TABLE_SPECS,
) -> CollectResult:
    """Le as duas tabelas e grava a captura. Nada e' escrito antes do fim.

    `reader` ja carrega a sessao autenticada. Este modulo nao sabe como ela foi
    obtida e nao tem como obte-la.
    """
    out_dir = Path(out_dir)
    assert_fora_do_repositorio(out_dir)

    capturas = [collect_table(reader, spec, page_size=page_size) for spec in specs]

    relogio = now or (lambda: datetime.now(timezone.utc))
    captured_at = relogio()
    if captured_at.tzinfo is None:
        raise SnapshotCollectError("relogio devolveu datetime sem timezone.")

    # Renderiza tudo em memoria primeiro: se algo falhar, o disco nao foi tocado.
    corpos: dict[str, str] = {
        c.spec.file_name: render_jsonl(c.rows, c.spec) for c in capturas
    }
    arquivos: dict[str, dict] = {
        nome: {"sha256": _sha256_texto(texto), "bytes": len(texto.encode("utf-8"))}
        for nome, texto in corpos.items()
    }

    manifest = build_manifest(capturas, arquivos, captured_at, read_method,
                              blocked_mutations)
    manifest_texto = render_manifest(manifest)
    manifest_sha = _sha256_texto(manifest_texto)
    sha_texto = f"{manifest_sha}  MANIFEST.json\n"

    out_dir.mkdir(parents=True, exist_ok=True)
    for nome, texto in corpos.items():
        _escreve_texto(out_dir / nome, texto)
    _escreve_texto(out_dir / "MANIFEST.json", manifest_texto)
    _escreve_texto(out_dir / "MANIFEST.sha256", sha_texto)

    arquivos["MANIFEST.json"] = {"sha256": manifest_sha,
                                 "bytes": len(manifest_texto.encode("utf-8"))}
    arquivos["MANIFEST.sha256"] = {"sha256": _sha256_texto(sha_texto),
                                   "bytes": len(sha_texto.encode("utf-8"))}

    # Reconferencia do que ficou no disco: o hash declarado e' o hash do arquivo.
    for nome in list(corpos) + ["MANIFEST.json"]:
        if file_sha256(out_dir / nome) != arquivos[nome]["sha256"]:
            raise SnapshotCollectError(
                f"{nome}: hash do arquivo gravado difere do declarado."
            )

    return CollectResult(out_dir=out_dir, captured_at=captured_at,
                         manifest=manifest, files=arquivos,
                         blocked_mutations=blocked_mutations)


# --------------------------------------------------------------------------
# Guard de somente-leitura
# --------------------------------------------------------------------------

def is_mutating(method: str) -> bool:
    return str(method).upper() in MUTATING_METHODS


# Rota de autenticacao padrao do Supabase. A aplicacao pode ter outras — o
# login da Avoe passa por uma Edge Function antes de chegar aqui — e por isso
# a lista e' configuravel: o repositorio nao versiona o endereco da fonte.
DEFAULT_AUTH_PATHS: tuple[str, ...] = ("/auth/v1/",)


def _path(url: str) -> str:
    m = re.match(r"^[a-z]+://[^/?#]+([^?#]*)", str(url))
    return m.group(1) if m else ""


def _host(url: str) -> str:
    m = re.match(r"^[a-z]+://([^/?#]+)", str(url))
    return m.group(1) if m else "(desconhecido)"


@dataclass
class ReadOnlyGuard:
    """Aborta requisicao mutavel antes de ela sair do navegador.

    Antes de `arm()`, cada rota de autenticacao declarada passa UMA vez. Um
    login pode ser um POST so' ou uma sequencia de passos distintos — conferir
    a credencial num lugar e trocar por sessao noutro. O que caracteriza
    SEGUNDA TENTATIVA e' repetir o MESMO passo, e e' isso que fica bloqueado:
    a regra de "no maximo uma tentativa" vive no codigo, nao na disciplina de
    quem opera. Qualquer outra mutacao e' bloqueada desde o inicio.

    Depois de `arm()`, nada mutavel passa, nem a propria autenticacao. Nao ha
    excecao por destino e nao ha excecao por origem.
    """
    auth_paths: tuple[str, ...] = DEFAULT_AUTH_PATHS
    armed: bool = False
    blocked: list[tuple[str, str, str]] = field(default_factory=list)
    auth_allowed: list[str] = field(default_factory=list)
    on_block: Callable[[str, str, str], None] | None = None

    @property
    def count(self) -> int:
        return len(self.blocked)

    @property
    def auth_attempts(self) -> int:
        return len(self.auth_allowed)

    def arm(self) -> None:
        self.armed = True

    def is_auth_request(self, url: str) -> bool:
        caminho = _path(url)
        return any(p in caminho for p in self.auth_paths)

    def handle(self, route: Any, request: Any) -> None:
        metodo = str(getattr(request, "method", "")).upper()
        url = getattr(request, "url", "")
        if is_mutating(metodo):
            passo = _path(url)
            if (not self.armed and self.is_auth_request(url)
                    and passo not in self.auth_allowed):
                self.auth_allowed.append(passo)
                route.continue_()
                return
            # Metodo, host e CAMINHO entram no registro. Nunca a query, nunca
            # o corpo. Sem o caminho, um bloqueio legitimo e um bloqueio por
            # rota mal declarada sao indistinguiveis para quem opera — foi o
            # que atrasou o diagnostico na primeira execucao deste gate.
            host = _host(url)
            self.blocked.append((metodo, host, passo))
            if self.on_block is not None:
                self.on_block(metodo, host, passo)
            route.abort()
            return
        route.continue_()


def install_read_only_guard(
    context: Any,
    auth_paths: tuple[str, ...] = DEFAULT_AUTH_PATHS,
    on_block: Callable[[str, str, str], None] | None = None,
) -> ReadOnlyGuard:
    """Instala o guard num BrowserContext do Playwright, ainda desarmado."""
    guard = ReadOnlyGuard(auth_paths=auth_paths, on_block=on_block)
    context.route("**/*", guard.handle)
    return guard


# --------------------------------------------------------------------------
# Leitor de navegador
# --------------------------------------------------------------------------

# O JS roda DENTRO da pagina. Ele usa os globais que a aplicacao ja tem e
# devolve tres campos. Nenhum cabecalho de requisicao e' lido de volta, e
# nenhum e' incluido no retorno — nem em caminho de erro.
_JS_READER = """
async (arg) => {
  if (typeof SUPABASE_URL === 'undefined' ||
      typeof SUPABASE_ANON_KEY === 'undefined' ||
      typeof tokenAutenticacao !== 'function') {
    return { status: -1, rows: [], content_range: null };
  }
  const url = SUPABASE_URL + '/rest/v1/' + arg.table +
              '?select=*&order=id.asc&limit=' + arg.limit + '&offset=' + arg.offset;
  let res;
  try {
    res = await fetch(url, {
      method: 'GET',
      headers: {
        'apikey': SUPABASE_ANON_KEY,
        'Authorization': 'Bearer ' + (await tokenAutenticacao()),
        'Accept': 'application/json',
        'Prefer': 'count=exact',
      },
    });
  } catch (e) {
    return { status: -2, rows: [], content_range: null };
  }
  const cr = res.headers.get('Content-Range');
  if (!res.ok) {
    return { status: res.status, rows: [], content_range: cr };
  }
  let rows;
  try {
    rows = await res.json();
  } catch (e) {
    return { status: -3, rows: [], content_range: cr };
  }
  return { status: res.status, rows: rows, content_range: cr };
}
"""


class BrowserPageReader:
    """Le' pela sessao ja autenticada. Nao recebe e nao guarda credencial."""

    def __init__(self, page: Any) -> None:
        self._page = page

    def fetch(self, table: str, offset: int, limit: int) -> dict:
        # A excecao do Playwright carrega a URL da chamada, e a URL carrega
        # query. Guardamos so' o NOME da classe e levantamos FORA do `except`:
        # assim nem `__cause__` nem `__context__` seguram a mensagem original.
        classe: str | None = None
        try:
            return self._page.evaluate(
                _JS_READER, {"table": table, "offset": offset, "limit": limit}
            )
        except Exception as exc:  # timeout, pagina fechada, navegacao
            classe = type(exc).__name__
        raise SnapshotCollectError(
            f"{table} offset={offset}: leitura nao concluida no navegador "
            f"({classe}). A coleta para."
        )
