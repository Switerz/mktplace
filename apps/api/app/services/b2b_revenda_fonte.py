"""Adapter da Torre B2B como FONTE EXTERNA somente leitura (Gate TORRE-B2B-OCTAPRICE).

Transporte: Data API do Supabase (PostgREST), chamada SO' pelo backend. A
chave nunca vai ao navegador: o header e' montado aqui, a partir de variavel
de ambiente, e nunca aparece em log, resposta, excecao ou traceback.

ALLOWLIST FIXA de seis RPCs — nada alem disso e' chamado, e nenhum nome de RPC
vem da requisicao (contrato: docs/b2b_revenda_contrato.md):

  - `fn_monit_datas`          dias com relatorio do OctaPrice
  - `fn_monit_resumo`         KPIs do dia
  - `fn_monit_severidade`     leve / media / grave + buy box
  - `fn_monit_marca_canal`    recorte marca x canal (e, sem filtro, os canais
                              presentes na carga mais recente)
  - `fn_monit_top_produtos`   ranking de SKUs
  - `fn_monit_sellers`        SO' as colunas `identificado` e `violando`
                              (`select=`), para contar a cobertura. Nome de
                              seller, cliente e vendedor NAO atravessam a rede.

As RPCs sao as MESMAS que alimentam a tela da Torre B2B, para os numeros
baterem por construcao. Argumentos vao POR NOME no corpo JSON: se a fonte
renomear um parametro, a chamada falha alto em vez de deslocar valores.
`p_vendedores` e `p_cnpjs` nunca sao enviados.

SEGURANCA
---------
- So' o header `apikey`: o gateway do Supabase sintetiza o `Authorization`
  a partir dele, para chave nova (`sb_*`) e legada (JWT). Uma copia a menos
  do segredo em transito.
- Nenhum corpo de erro da fonte e' lido alem do campo `code` (SQLSTATE /
  PGRST). Mensagem, detalhe e hint podem trazer dado; nao sao tocados.
- `raise ... from None` corta o encadeamento: um log de traceback nao carrega
  a excecao do httpx (que tem a URL).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Literal, Optional, Protocol

import httpx

logger = logging.getLogger(__name__)

#: Vocabulario FECHADO de motivos. A tela traduz cada um; nenhum carrega valor.
MotivoIndisponivel = Literal[
    "credencial_ausente",
    "credencial_invalida",
    "fonte_inacessivel",
    "permissao_negada",
    "contrato_da_fonte_quebrado",
    "fonte_lenta",
    "falha_desconhecida",
]

#: As UNICAS RPCs que este adapter chama.
RPCS_PERMITIDAS = frozenset({
    "fn_monit_datas",
    "fn_monit_resumo",
    "fn_monit_severidade",
    "fn_monit_marca_canal",
    "fn_monit_top_produtos",
    "fn_monit_sellers",
})

#: Colunas pedidas a cada RPC (`select=`). E' o contrato de leitura: a fonte
#: so' devolve isto, e uma coluna que sumir quebra a chamada (PGRST/42703).
COLUNAS = {
    "fn_monit_datas": ("data_ref",),
    "fn_monit_resumo": ("data_ref", "total", "violando", "pct", "sellers_violando", "produtos", "desvio_medio"),
    "fn_monit_severidade": ("leve", "media", "grave", "com_buybox", "viol_com_buybox"),
    "fn_monit_marca_canal": ("marca_id", "canal", "anuncios", "violando", "desvio_medio", "preco_medio"),
    "fn_monit_top_produtos": ("sku", "marca_id", "exemplo", "anuncios", "violando", "pct", "desvio_medio"),
    "fn_monit_sellers": ("identificado", "violando"),
}

#: SQLSTATE / codigos PostgREST que significam "a fonte mudou o que prometia".
_CODIGOS_CONTRATO = {
    "42883", "42P01", "42703", "42804", "42725", "42P13",
    "PGRST202", "PGRST203", "PGRST204", "PGRST100", "PGRST116",
}
_CODIGO_PERMISSAO = "42501"
_CODIGO_TIMEOUT = "57014"

#: Identifica a origem das chamadas nos logs da fonte, sem expor nada.
USER_AGENT = "torre-mktplace-b2b-revenda"

#: Teto do `p_limit` de `fn_monit_sellers` ao contar identificados.
LIMITE_SELLERS_COBERTURA = 100000
#: Pagina para RPCs que podem passar do `max-rows` do PostgREST.
PAGINA = 1000
MAX_PAGINAS = 50


class FonteIndisponivel(Exception):
    """A fonte nao respondeu como prometido. `motivo` e' do vocabulario fechado."""

    def __init__(self, motivo: MotivoIndisponivel):
        super().__init__(motivo)
        self.motivo: MotivoIndisponivel = motivo


# ---------------------------------------------------------------------------
# Linhas da fonte (ja' tipadas; nenhuma carrega dado de cliente)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FiltrosFonte:
    """Recorte enviado as RPCs. Listas vazias viram NULL (= sem filtro)."""

    data: Optional[date]
    marcas: tuple[str, ...]
    canais: tuple[str, ...]
    severidades: tuple[str, ...]
    skus: tuple[str, ...]
    ufs: tuple[str, ...]


@dataclass(frozen=True)
class CargaFonte:
    data_ref: Optional[date]
    coleta_inicio: Optional[datetime]
    coleta_fim: Optional[datetime]
    importado_inicio: Optional[datetime]
    importado_fim: Optional[datetime]
    linhas: int
    canais: tuple[str, ...]


@dataclass(frozen=True)
class ResumoFonte:
    data_ref: Optional[date]
    total: int
    violando: int
    pct: Optional[Decimal]
    sellers_violando: int
    produtos: int
    desvio_medio: Optional[Decimal]


@dataclass(frozen=True)
class SeveridadeFonte:
    leve: int
    media: int
    grave: int
    com_buybox: int
    viol_com_buybox: int


@dataclass(frozen=True)
class CoberturaFonte:
    sellers_violando: int
    sellers_violando_identificados: int
    anuncios_violando: int
    anuncios_violando_identificados: int


@dataclass(frozen=True)
class MarcaCanalFonte:
    marca_id: Optional[str]
    canal: Optional[str]
    anuncios: int
    violando: int
    desvio_medio: Optional[Decimal]
    preco_medio: Optional[Decimal]


@dataclass(frozen=True)
class SkuFonte:
    sku: str
    marca_id: Optional[str]
    exemplo: Optional[str]
    anuncios: int
    violando: int
    pct: Optional[Decimal]
    desvio_medio: Optional[Decimal]


class FonteRevenda(Protocol):
    """O que o servico precisa da Torre B2B. A implementacao falsa dos testes
    e a real (HTTP) cumprem o mesmo contrato."""

    def datas(self) -> list[date]: ...
    def carga_mais_recente(self) -> CargaFonte: ...
    def resumo(self, f: FiltrosFonte) -> ResumoFonte: ...
    def severidade(self, f: FiltrosFonte) -> SeveridadeFonte: ...
    def cobertura(self, f: FiltrosFonte) -> CoberturaFonte: ...
    def marca_canal(self, f: FiltrosFonte) -> list[MarcaCanalFonte]: ...
    def top_skus(self, f: FiltrosFonte, limite: int) -> list[SkuFonte]: ...


# ---------------------------------------------------------------------------
# Conversao
# ---------------------------------------------------------------------------
def _lista_ou_nulo(valores: tuple[str, ...]) -> Optional[list[str]]:
    """Lista vazia vira NULL: na fonte, NULL e' "sem filtro"."""
    return list(valores) if valores else None


def argumentos(f: FiltrosFonte, *, com_severidade: bool = True) -> dict[str, Any]:
    """Corpo JSON comum, por NOME de parametro. Nunca `p_vendedores`/`p_cnpjs`."""
    corpo: dict[str, Any] = {
        "p_data": f.data.isoformat() if f.data else None,
        "p_marcas": _lista_ou_nulo(f.marcas),
        "p_canais": _lista_ou_nulo(f.canais),
        "p_skus": _lista_ou_nulo(f.skus),
        "p_ufs": _lista_ou_nulo(f.ufs),
    }
    if com_severidade:
        corpo["p_sevs"] = _lista_ou_nulo(f.severidades)
    return corpo


def _int(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, (int, float, str, Decimal)):
        raise FonteIndisponivel("contrato_da_fonte_quebrado")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise FonteIndisponivel("contrato_da_fonte_quebrado") from None


def _dec(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        # `str()` primeiro: float JSON vira Decimal pela representacao curta.
        return v if isinstance(v, Decimal) else Decimal(str(v))
    except Exception:  # noqa: BLE001
        raise FonteIndisponivel("contrato_da_fonte_quebrado") from None


def _data(v: Any) -> Optional[date]:
    if v is None:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise FonteIndisponivel("contrato_da_fonte_quebrado") from None


def _codigo_do_erro(resp: httpx.Response) -> Optional[str]:
    """SO' o campo `code` do corpo de erro. Mensagem/detalhe/hint nao sao lidos."""
    try:
        corpo = resp.json()
    except (ValueError, json.JSONDecodeError):
        return None
    codigo = corpo.get("code") if isinstance(corpo, dict) else None
    return str(codigo) if codigo is not None else None


def classificar_http(status: int, codigo: Optional[str]) -> MotivoIndisponivel:
    """Categoria da falha pelo status HTTP e pelo codigo — nunca pela mensagem."""
    if codigo == _CODIGO_TIMEOUT:
        return "fonte_lenta"
    if codigo == _CODIGO_PERMISSAO:
        return "permissao_negada"
    if codigo in _CODIGOS_CONTRATO:
        return "contrato_da_fonte_quebrado"
    if status == 401:
        return "credencial_invalida"
    if status == 403:
        return "permissao_negada"
    if status in (404, 400, 300):
        return "contrato_da_fonte_quebrado"
    if status in (408, 504):
        return "fonte_lenta"
    if status >= 500 or status == 429:
        return "fonte_inacessivel"
    return "falha_desconhecida"


# ---------------------------------------------------------------------------
# Implementacao real: Data API do Supabase
# ---------------------------------------------------------------------------
class FonteSupabaseHttp:
    """Le as seis RPCs pela Data API (PostgREST) com uma chave server-side."""

    def __init__(self, cliente: httpx.Client):
        # O cliente ja' nasce com base_url e o header `apikey`; esta classe
        # nunca ve o valor da chave.
        self._http = cliente

    def _rpc(
        self,
        nome: str,
        corpo: dict[str, Any],
        *,
        paginar: bool = False,
    ) -> list[dict[str, Any]]:
        if nome not in RPCS_PERMITIDAS:
            # Defesa em profundidade: nenhum caminho de codigo monta nome de
            # RPC a partir da requisicao, mas se um dia montar, para aqui.
            raise ValueError("RPC fora da allowlist")
        params = {"select": ",".join(COLUNAS[nome])}
        linhas: list[dict[str, Any]] = []
        for pagina in range(MAX_PAGINAS if paginar else 1):
            headers = {}
            if paginar:
                inicio = pagina * PAGINA
                headers = {"Range-Unit": "items", "Range": f"{inicio}-{inicio + PAGINA - 1}"}
            try:
                resp = self._http.post(f"/rest/v1/rpc/{nome}", params=params, json=corpo, headers=headers)
            except httpx.TimeoutException as exc:
                motivo: MotivoIndisponivel = (
                    "fonte_inacessivel" if isinstance(exc, httpx.ConnectTimeout) else "fonte_lenta"
                )
                logger.warning("b2b_revenda: %s em %s (%s)", motivo, nome, type(exc).__name__)
                raise FonteIndisponivel(motivo) from None
            except httpx.HTTPError as exc:
                logger.warning("b2b_revenda: fonte_inacessivel em %s (%s)", nome, type(exc).__name__)
                raise FonteIndisponivel("fonte_inacessivel") from None
            if resp.status_code not in (200, 206):
                motivo = classificar_http(resp.status_code, _codigo_do_erro(resp))
                logger.warning("b2b_revenda: %s em %s (HTTP %s)", motivo, nome, resp.status_code)
                raise FonteIndisponivel(motivo)
            try:
                lote = resp.json()
            except (ValueError, json.JSONDecodeError):
                raise FonteIndisponivel("contrato_da_fonte_quebrado") from None
            if not isinstance(lote, list) or any(not isinstance(r, dict) for r in lote):
                raise FonteIndisponivel("contrato_da_fonte_quebrado")
            faltando = [c for c in COLUNAS[nome] if lote and c not in lote[0]]
            if faltando:
                raise FonteIndisponivel("contrato_da_fonte_quebrado")
            linhas.extend(lote)
            if not paginar or len(lote) < PAGINA:
                break
        return linhas

    def datas(self) -> list[date]:
        return [d for d in (_data(r["data_ref"]) for r in self._rpc("fn_monit_datas", {})) if d]

    def carga_mais_recente(self) -> CargaFonte:
        """Dia mais recente + canais e anuncios dele (marca x canal SEM filtro).

        Coleta e importacao (`data_coleta`, `created_at`) NAO saem de nenhuma
        das seis RPCs: ficam `None` e a tela diz "nao informado".
        """
        datas = self.datas()
        if not datas:
            return CargaFonte(None, None, None, None, None, 0, ())
        dia = max(datas)
        vazio = FiltrosFonte(dia, (), (), (), (), ())
        linhas = self.marca_canal(vazio)
        canais = tuple(sorted({r.canal for r in linhas if r.canal}))
        return CargaFonte(dia, None, None, None, None, sum(r.anuncios for r in linhas), canais)

    def resumo(self, f: FiltrosFonte) -> ResumoFonte:
        linhas = self._rpc("fn_monit_resumo", argumentos(f))
        if len(linhas) != 1:
            raise FonteIndisponivel("contrato_da_fonte_quebrado")
        r = linhas[0]
        return ResumoFonte(
            data_ref=_data(r["data_ref"]), total=_int(r["total"]), violando=_int(r["violando"]),
            pct=_dec(r["pct"]), sellers_violando=_int(r["sellers_violando"]),
            produtos=_int(r["produtos"]), desvio_medio=_dec(r["desvio_medio"]),
        )

    def severidade(self, f: FiltrosFonte) -> SeveridadeFonte:
        # `fn_monit_severidade` nao recebe `p_sevs`: e' o proprio breakdown.
        linhas = self._rpc("fn_monit_severidade", argumentos(f, com_severidade=False))
        if len(linhas) != 1:
            raise FonteIndisponivel("contrato_da_fonte_quebrado")
        r = linhas[0]
        return SeveridadeFonte(
            leve=_int(r["leve"]), media=_int(r["media"]), grave=_int(r["grave"]),
            com_buybox=_int(r["com_buybox"]), viol_com_buybox=_int(r["viol_com_buybox"]),
        )

    def cobertura(self, f: FiltrosFonte) -> CoberturaFonte:
        corpo = argumentos(f) | {"p_limit": LIMITE_SELLERS_COBERTURA}
        # Uma linha por seller violando, so' com `identificado` e `violando`.
        # Contado AQUI e descartado: nenhuma linha sai deste metodo.
        linhas = self._rpc("fn_monit_sellers", corpo, paginar=True)
        ident = [r for r in linhas if r.get("identificado") is True]
        return CoberturaFonte(
            sellers_violando=len(linhas),
            sellers_violando_identificados=len(ident),
            anuncios_violando=sum(_int(r["violando"]) for r in linhas),
            anuncios_violando_identificados=sum(_int(r["violando"]) for r in ident),
        )

    def marca_canal(self, f: FiltrosFonte) -> list[MarcaCanalFonte]:
        return [
            MarcaCanalFonte(
                marca_id=r["marca_id"], canal=r["canal"], anuncios=_int(r["anuncios"]),
                violando=_int(r["violando"]), desvio_medio=_dec(r["desvio_medio"]),
                preco_medio=_dec(r["preco_medio"]),
            )
            for r in self._rpc("fn_monit_marca_canal", argumentos(f), paginar=True)
        ]

    def top_skus(self, f: FiltrosFonte, limite: int) -> list[SkuFonte]:
        corpo = argumentos(f) | {"p_limit": int(limite)}
        return [
            SkuFonte(
                sku=str(r["sku"]), marca_id=r["marca_id"], exemplo=r["exemplo"],
                anuncios=_int(r["anuncios"]), violando=_int(r["violando"]),
                pct=_dec(r["pct"]), desvio_medio=_dec(r["desvio_medio"]),
            )
            for r in self._rpc("fn_monit_top_produtos", corpo)
        ]


# ---------------------------------------------------------------------------
# Cache TTL em volta da fonte
# ---------------------------------------------------------------------------
class FonteComCache:
    """Cache em memoria, por metodo + recorte, com TTL de 15-30 min.

    - Falha NAO e' cacheada: a proxima requisicao tenta de novo, e uma queda
      curta da fonte nao vira 20 minutos de "indisponivel".
    - Cada valor guarda QUANDO foi lido da fonte (`lido_em`, relogio de
      parede), para a tela dizer a idade do numero.
    - Teto de entradas: as combinacoes de filtro sao abertas; sem teto, o
      cache cresceria sem limite. Estourou, sai a mais antiga.
    - Relogio monotonico injetavel para teste.
    """

    def __init__(
        self,
        fonte: FonteRevenda,
        ttl_segundos: int,
        *,
        max_entradas: int = 256,
        monotonico: Callable[[], float] = time.monotonic,
        agora: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ):
        self._fonte = fonte
        self._ttl = float(ttl_segundos)
        self._max = max_entradas
        self._mono = monotonico
        self._agora = agora
        self._lock = threading.Lock()
        self._itens: dict[tuple, tuple[float, datetime, Any]] = {}

    def _obter(self, chave: tuple, carregar: Callable[[], Any]) -> tuple[Any, datetime]:
        agora = self._mono()
        with self._lock:
            item = self._itens.get(chave)
            if item is not None and agora - item[0] < self._ttl:
                return item[2], item[1]
        valor = carregar()  # levanta FonteIndisponivel sem cachear nada
        lido_em = self._agora()
        with self._lock:
            if len(self._itens) >= self._max and chave not in self._itens:
                mais_antiga = min(self._itens, key=lambda k: self._itens[k][0])
                del self._itens[mais_antiga]
            self._itens[chave] = (agora, lido_em, valor)
        return valor, lido_em

    def limpar(self) -> None:
        with self._lock:
            self._itens.clear()

    def datas(self):
        return self._obter(("datas",), self._fonte.datas)

    def carga_mais_recente(self):
        return self._obter(("carga",), self._fonte.carga_mais_recente)

    def resumo(self, f: FiltrosFonte):
        return self._obter(("resumo", f), lambda: self._fonte.resumo(f))

    def severidade(self, f: FiltrosFonte):
        return self._obter(("severidade", f), lambda: self._fonte.severidade(f))

    def cobertura(self, f: FiltrosFonte):
        return self._obter(("cobertura", f), lambda: self._fonte.cobertura(f))

    def marca_canal(self, f: FiltrosFonte):
        return self._obter(("marca_canal", f), lambda: self._fonte.marca_canal(f))

    def top_skus(self, f: FiltrosFonte, limite: int):
        return self._obter(("top_skus", f, limite), lambda: self._fonte.top_skus(f, limite))


# ---------------------------------------------------------------------------
# Construcao preguicosa (so' com a flag ligada)
# ---------------------------------------------------------------------------
def criar_cliente(url: str, chave: str, *, timeout_segundos: float) -> httpx.Client:
    """Cliente HTTP da fonte. Valida a URL SEM ecoar valor; nao abre conexao."""
    if not url or not chave:
        raise FonteIndisponivel("credencial_ausente")
    try:
        base = httpx.URL(url.strip())
    except Exception:  # noqa: BLE001
        raise FonteIndisponivel("credencial_invalida") from None
    # So' HTTPS, sem credencial embutida, sem caminho/consulta: a chave vai no
    # header e a URL e' so' a raiz do projeto.
    if base.scheme != "https" or not base.host or base.userinfo or base.path not in ("", "/") or base.query:
        raise FonteIndisponivel("credencial_invalida")
    if any(c in chave for c in "\r\n") or not chave.strip():
        raise FonteIndisponivel("credencial_invalida")
    return httpx.Client(
        base_url=str(base).rstrip("/"),
        headers={"apikey": chave.strip(), "User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(timeout_segundos, connect=min(5.0, timeout_segundos)),
        follow_redirects=False,
    )


_singleton_lock = threading.Lock()
_singleton: Optional[FonteComCache] = None


def obter_fonte() -> FonteComCache:
    """Fonte real com cache, criada na primeira chamada. Levanta
    `FonteIndisponivel` se a credencial faltar ou for invalida — e nao guarda
    a falha, para a configuracao corrigida valer no proximo request."""
    global _singleton
    from app.config import settings

    with _singleton_lock:
        if _singleton is None:
            cliente = criar_cliente(
                settings.b2b_revenda_supabase_url,
                settings.b2b_revenda_supabase_key.get_secret_value(),
                timeout_segundos=settings.b2b_revenda_timeout_seconds,
            )
            _singleton = FonteComCache(FonteSupabaseHttp(cliente), settings.b2b_revenda_cache_ttl_seconds)
        return _singleton
