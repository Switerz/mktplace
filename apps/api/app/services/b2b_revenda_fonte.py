"""Adapter da Torre B2B como FONTE EXTERNA somente leitura (Gate TORRE-B2B-OCTAPRICE).

O que este modulo le, e nada alem disso (contrato: docs/b2b_revenda_contrato.md):

  - `fn_monit_datas()`                      dias com relatorio do OctaPrice
  - `fn_monit_resumo(...)`                  KPIs do dia
  - `fn_monit_severidade(...)`              leve / media / grave + buy box
  - `fn_monit_marca_canal(...)`             recorte marca x canal
  - `fn_monit_top_produtos(...)`            ranking de SKUs
  - `fn_monit_sellers(...)`                 SO' agregado em SQL: contagens de
                                            identificados. Nome de seller,
                                            cliente e vendedor NUNCA atravessam
                                            a conexao.
  - `fato_monitoramento_preco`              SO' carimbos da carga mais recente
                                            (data_ref, data_coleta, created_at,
                                            canal) para o frescor.

As RPCs sao as MESMAS que alimentam a tela da Torre B2B, para os numeros
baterem por construcao. Chamadas por NOME de parametro (`p_x => ...`): se a
fonte renomear um parametro, a chamada falha alto em vez de deslocar valores.

`p_vendedores` e `p_cnpjs` nunca sao enviados. Filtrar por cliente ou vendedor
e' recorte de dado de cliente, e nenhuma rota com dado de cliente existe
enquanto a autenticacao da Torre de Marketplaces nao estiver definida.

SEGURANCA
---------
- Cada leitura roda numa transacao `READ ONLY` com `statement_timeout` local,
  alem do `default_transaction_read_only` que a role dedicada ja' traz.
- Nenhuma mensagem de excecao sai daqui. O texto de erro do driver pode
  carregar host, usuario e DSN; so' a CATEGORIA (vocabulario fechado) e o nome
  da classe sao registrados. `raise ... from None` corta o encadeamento para
  que um log de traceback tambem nao a carregue.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Literal, Optional, Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

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

#: SQLSTATEs que significam "a fonte mudou o que prometia": funcao, tabela ou
#: coluna sumiu, ou o tipo de retorno mudou.
_PGCODES_CONTRATO = {"42883", "42P01", "42703", "42804", "42725", "42P13", "25006"}
_PGCODE_PERMISSAO = "42501"
_PGCODE_TIMEOUT = "57014"

#: Drivers aceitos na DSN. Qualquer outro esquema e' recusado SEM ecoar o valor.
_DRIVERS_ACEITOS = {"postgresql", "postgresql+psycopg2"}

#: Identifica a conexao no `pg_stat_activity` da fonte: o dono do B2B ve quem
#: esta lendo, sem precisar perguntar.
APPLICATION_NAME = "torre-mktplace-b2b-revenda"

#: Teto do `p_limit` de `fn_monit_sellers` ao contar identificados. Se o numero
#: de sellers violando chegar nele, a cobertura e' marcada como inconsistente.
LIMITE_SELLERS_COBERTURA = 100000


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
    e a real (Postgres) cumprem o mesmo contrato."""

    def datas(self) -> list[date]: ...
    def carga_mais_recente(self) -> CargaFonte: ...
    def resumo(self, f: FiltrosFonte) -> ResumoFonte: ...
    def severidade(self, f: FiltrosFonte) -> SeveridadeFonte: ...
    def cobertura(self, f: FiltrosFonte) -> CoberturaFonte: ...
    def marca_canal(self, f: FiltrosFonte) -> list[MarcaCanalFonte]: ...
    def top_skus(self, f: FiltrosFonte, limite: int) -> list[SkuFonte]: ...


# ---------------------------------------------------------------------------
# SQL — o contrato de leitura, inteiro, num lugar so'
# ---------------------------------------------------------------------------
# Parametros comuns, por NOME. `p_vendedores`/`p_cnpjs` ficam de fora de
# proposito (default NULL na fonte).
_ARGS_FILTRO = (
    "p_marcas => CAST(:marcas AS text[]), "
    "p_canais => CAST(:canais AS text[]), "
    "p_skus => CAST(:skus AS text[]), "
    "p_ufs => CAST(:ufs AS text[])"
)

SQL_DATAS = "SELECT data_ref FROM fn_monit_datas()"

SQL_CARGA = """
WITH ult AS (SELECT max(data_ref) AS data_ref FROM fato_monitoramento_preco)
SELECT u.data_ref,
       min(f.data_coleta)  AS coleta_inicio,
       max(f.data_coleta)  AS coleta_fim,
       min(f.created_at)   AS importado_inicio,
       max(f.created_at)   AS importado_fim,
       count(f.data_ref)::int AS linhas,
       array_remove(array_agg(DISTINCT f.canal), NULL) AS canais
FROM ult u
LEFT JOIN fato_monitoramento_preco f ON f.data_ref = u.data_ref
GROUP BY u.data_ref
"""

SQL_RESUMO = f"""
SELECT data_ref, total, violando, pct, sellers_violando, produtos, desvio_medio
FROM fn_monit_resumo(p_data => CAST(:data AS date),
                     p_sevs => CAST(:sevs AS text[]), {_ARGS_FILTRO})
"""

# `fn_monit_severidade` nao recebe `p_sevs`: e' o proprio breakdown.
SQL_SEVERIDADE = f"""
SELECT leve, media, grave, com_buybox, viol_com_buybox
FROM fn_monit_severidade(p_data => CAST(:data AS date), {_ARGS_FILTRO})
"""

# Agregado DENTRO do banco: a conexao so' devolve quatro inteiros. Nome do
# seller, cliente e vendedor nao saem da fonte.
SQL_COBERTURA = f"""
SELECT count(*)::int                                   AS sellers_violando,
       count(*) FILTER (WHERE s.identificado)::int     AS sellers_violando_identificados,
       COALESCE(sum(s.violando), 0)::int               AS anuncios_violando,
       COALESCE(sum(s.violando) FILTER (WHERE s.identificado), 0)::int
                                                       AS anuncios_violando_identificados
FROM fn_monit_sellers(p_data => CAST(:data AS date), p_limit => :limite_sellers,
                      p_sevs => CAST(:sevs AS text[]), {_ARGS_FILTRO}) s
"""

SQL_MARCA_CANAL = f"""
SELECT marca_id, canal, anuncios, violando, desvio_medio, preco_medio
FROM fn_monit_marca_canal(p_data => CAST(:data AS date),
                          p_sevs => CAST(:sevs AS text[]), {_ARGS_FILTRO})
"""

SQL_TOP_SKUS = f"""
SELECT sku, marca_id, exemplo, anuncios, violando, pct, desvio_medio
FROM fn_monit_top_produtos(p_data => CAST(:data AS date), p_limit => :limite,
                           p_sevs => CAST(:sevs AS text[]), {_ARGS_FILTRO})
"""


def _lista_ou_nulo(valores: tuple[str, ...]) -> Optional[list[str]]:
    """Lista vazia vira NULL: na fonte, NULL e' "sem filtro" e `ARRAY[]` nem
    tem tipo inferivel."""
    return list(valores) if valores else None


def _params(f: FiltrosFonte) -> dict[str, Any]:
    return {
        "data": f.data,
        "sevs": _lista_ou_nulo(f.severidades),
        "marcas": _lista_ou_nulo(f.marcas),
        "canais": _lista_ou_nulo(f.canais),
        "skus": _lista_ou_nulo(f.skus),
        "ufs": _lista_ou_nulo(f.ufs),
    }


def _int(v: Any) -> int:
    return int(v) if v is not None else 0


def _dec(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def classificar_falha(exc: BaseException) -> MotivoIndisponivel:
    """Categoria da falha SEM ler a mensagem — so' SQLSTATE e classe."""
    pgcode = getattr(getattr(exc, "orig", None), "pgcode", None)
    if pgcode == _PGCODE_PERMISSAO:
        return "permissao_negada"
    if pgcode == _PGCODE_TIMEOUT:
        return "fonte_lenta"
    if pgcode in _PGCODES_CONTRATO:
        return "contrato_da_fonte_quebrado"
    nome = type(exc).__name__
    if nome in ("OperationalError", "InterfaceError", "TimeoutError"):
        return "fonte_inacessivel"
    return "falha_desconhecida"


class FontePostgres:
    """Implementacao real: Postgres da Torre B2B via role dedicada read-only."""

    def __init__(self, engine: Engine, statement_timeout_ms: int):
        self._engine = engine
        self._timeout = str(int(statement_timeout_ms))

    def _ler(self, sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                with conn.begin():
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    # `set_config(..., true)` = SET LOCAL, com valor por bind.
                    conn.execute(
                        text("SELECT set_config('statement_timeout', :t, true)"),
                        {"t": self._timeout},
                    )
                    res = conn.execute(text(sql), params or {})
                    return [dict(r._mapping) for r in res]
        except (DBAPIError, SQLAlchemyError, OSError) as exc:
            motivo = classificar_falha(exc)
            # So' categoria e classe. NUNCA `str(exc)`: carrega host/usuario.
            logger.warning("b2b_revenda: fonte indisponivel: %s (%s)", motivo, type(exc).__name__)
            raise FonteIndisponivel(motivo) from None

    def datas(self) -> list[date]:
        return [r["data_ref"] for r in self._ler(SQL_DATAS) if r["data_ref"] is not None]

    def carga_mais_recente(self) -> CargaFonte:
        linhas = self._ler(SQL_CARGA)
        if not linhas:
            return CargaFonte(None, None, None, None, None, 0, ())
        r = linhas[0]
        return CargaFonte(
            data_ref=r["data_ref"],
            coleta_inicio=r["coleta_inicio"],
            coleta_fim=r["coleta_fim"],
            importado_inicio=r["importado_inicio"],
            importado_fim=r["importado_fim"],
            linhas=_int(r["linhas"]),
            canais=tuple(sorted(r["canais"] or ())),
        )

    def resumo(self, f: FiltrosFonte) -> ResumoFonte:
        linhas = self._ler(SQL_RESUMO, _params(f))
        if not linhas:
            raise FonteIndisponivel("contrato_da_fonte_quebrado")
        r = linhas[0]
        return ResumoFonte(
            data_ref=r["data_ref"], total=_int(r["total"]), violando=_int(r["violando"]),
            pct=_dec(r["pct"]), sellers_violando=_int(r["sellers_violando"]),
            produtos=_int(r["produtos"]), desvio_medio=_dec(r["desvio_medio"]),
        )

    def severidade(self, f: FiltrosFonte) -> SeveridadeFonte:
        p = _params(f)
        p.pop("sevs")
        linhas = self._ler(SQL_SEVERIDADE, p)
        if not linhas:
            raise FonteIndisponivel("contrato_da_fonte_quebrado")
        r = linhas[0]
        return SeveridadeFonte(
            leve=_int(r["leve"]), media=_int(r["media"]), grave=_int(r["grave"]),
            com_buybox=_int(r["com_buybox"]), viol_com_buybox=_int(r["viol_com_buybox"]),
        )

    def cobertura(self, f: FiltrosFonte) -> CoberturaFonte:
        p = _params(f)
        p["limite_sellers"] = LIMITE_SELLERS_COBERTURA
        r = self._ler(SQL_COBERTURA, p)[0]
        return CoberturaFonte(
            sellers_violando=_int(r["sellers_violando"]),
            sellers_violando_identificados=_int(r["sellers_violando_identificados"]),
            anuncios_violando=_int(r["anuncios_violando"]),
            anuncios_violando_identificados=_int(r["anuncios_violando_identificados"]),
        )

    def marca_canal(self, f: FiltrosFonte) -> list[MarcaCanalFonte]:
        return [
            MarcaCanalFonte(
                marca_id=r["marca_id"], canal=r["canal"], anuncios=_int(r["anuncios"]),
                violando=_int(r["violando"]), desvio_medio=_dec(r["desvio_medio"]),
                preco_medio=_dec(r["preco_medio"]),
            )
            for r in self._ler(SQL_MARCA_CANAL, _params(f))
        ]

    def top_skus(self, f: FiltrosFonte, limite: int) -> list[SkuFonte]:
        p = _params(f)
        p["limite"] = int(limite)
        return [
            SkuFonte(
                sku=r["sku"], marca_id=r["marca_id"], exemplo=r["exemplo"],
                anuncios=_int(r["anuncios"]), violando=_int(r["violando"]),
                pct=_dec(r["pct"]), desvio_medio=_dec(r["desvio_medio"]),
            )
            for r in self._ler(SQL_TOP_SKUS, p)
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
def criar_engine(url: str, *, sslmode: str, connect_timeout: int) -> Engine:
    """Engine da fonte. Valida o esquema SEM ecoar a URL.

    `create_engine` nao conecta: nenhuma rede acontece aqui.
    """
    if not url:
        raise FonteIndisponivel("credencial_ausente")
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 — a mensagem traria a URL inteira
        raise FonteIndisponivel("credencial_invalida") from None
    if parsed.drivername not in _DRIVERS_ACEITOS:
        raise FonteIndisponivel("credencial_invalida")
    # Driver EXPLICITO: o projeto depende de psycopg2, e o SQLAlchemy 2.1 muda
    # o padrao de `postgresql://` para psycopg 3. Sem isto, subir a versao do
    # SQLAlchemy derrubaria a integracao com `ModuleNotFoundError`.
    parsed = parsed.set(drivername="postgresql+psycopg2")
    try:
        return create_engine(
            parsed,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
            pool_recycle=300,
            connect_args={
                "connect_timeout": connect_timeout,
                "sslmode": sslmode,
                "application_name": APPLICATION_NAME,
            },
        )
    except Exception as exc:  # noqa: BLE001 — fronteira de configuracao
        logger.error("b2b_revenda: engine nao criado (%s)", type(exc).__name__)
        raise FonteIndisponivel("credencial_invalida") from None


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
            engine = criar_engine(
                settings.b2b_revenda_database_url,
                sslmode=settings.b2b_revenda_sslmode,
                connect_timeout=settings.b2b_revenda_connect_timeout_seconds,
            )
            _singleton = FonteComCache(
                FontePostgres(engine, settings.b2b_revenda_statement_timeout_ms),
                settings.b2b_revenda_cache_ttl_seconds,
            )
        return _singleton
