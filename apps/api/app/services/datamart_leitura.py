"""Leitura do Data Mart com reserva pelo Metabase.

A API le o Data Mart direto (`datamart_engine`), com o usuario de banco dela.
Tabelas fora do que esse usuario enxerga (ex.: `raw.*`) respondem
"permission denied". Nesse caso, e SO' nesse (ou sem engine configurado), a
MESMA consulta e' refeita pelo Metabase (`/api/dataset`, banco
`metabase_database_id`), que le o Data Mart com a conexao dele — desde que
`METABASE_API_KEY` esteja configurada. Sem a chave, o erro original sobe e o
servico o transforma em estado `erro` com motivo fixo (nunca zero).

PARAMETROS NO METABASE
----------------------
A consulta nativa do Metabase nao aceita parametros nomeados do SQLAlchemy.
`renderizar` troca cada `:nome` por um LITERAL SQL escapado, com tipos
fechados: str, int, float, date, datetime e lista de str. Qualquer outro
tipo, ou um `:nome` sem valor, levanta erro antes de chamar o Metabase.
Os valores vem de allowlists e datas calculadas no servidor, nunca do
cliente cru; mesmo assim, aspas sao dobradas e NUL e' recusado.

TIPOS NA VOLTA
--------------
O Metabase devolve data/hora como texto ISO e jsonb como texto. `normalizar`
devolve as colunas `*_date` como `date`, `*_at`/`*_em` como `datetime` e texto
que parece JSON (objeto/lista) como dict/list, para os servicos receberem a
mesma forma da leitura direta.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text

from app.config import settings

logger = logging.getLogger(__name__)

#: `:nome`, mas nao `::tipo` (cast do Postgres) nem `a:b` dentro de palavra.
_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)\b")
_DATA_PURA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATA_ZERADA = re.compile(r"^(\d{4}-\d{2}-\d{2})T00:00(:00(\.0+)?)?(Z|[+-]00:?00)?$")


class ParametroInvalido(ValueError):
    """Tipo de parametro que a reserva pelo Metabase nao sabe escrever."""


def _texto(v: str) -> str:
    if "\x00" in v:
        raise ParametroInvalido("texto com NUL")
    return "'" + v.replace("'", "''") + "'"


def literal(v: Any) -> str:
    """Valor Python -> literal SQL do Postgres. Tipos fechados."""
    if isinstance(v, bool) or v is None:
        raise ParametroInvalido(f"tipo nao suportado: {type(v).__name__}")
    if isinstance(v, str):
        return _texto(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ParametroInvalido("float nao finito")
        return repr(v)
    if isinstance(v, datetime):
        instante = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return f"TIMESTAMPTZ {_texto(instante.isoformat())}"
    if isinstance(v, date):
        return f"DATE {_texto(v.isoformat())}"
    if isinstance(v, (list, tuple)):
        if not all(isinstance(x, str) for x in v):
            raise ParametroInvalido("lista so' de texto")
        return "ARRAY[" + ",".join(_texto(x) for x in v) + "]::text[]"
    raise ParametroInvalido(f"tipo nao suportado: {type(v).__name__}")


def renderizar(sql: str, params: dict) -> str:
    """Troca `:nome` pelo literal. Nome sem valor = erro (nunca SQL pela metade)."""
    def troca(m: re.Match) -> str:
        nome = m.group(1)
        if nome not in params:
            raise ParametroInvalido(f"parametro sem valor: {nome}")
        return literal(params[nome])
    return _PARAM.sub(troca, sql)


def _valor(coluna: str, v: Any) -> Any:
    if not isinstance(v, str):
        return v
    if coluna.endswith("_date"):
        m = _DATA_ZERADA.match(v)
        if _DATA_PURA.match(v) or m:
            return date.fromisoformat(m.group(1) if m else v)
    if coluna.endswith("_at") or coluna.endswith("_em"):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return v
    if v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def normalizar(linhas: list[dict]) -> list[dict]:
    return [{k: _valor(k, v) for k, v in linha.items()} for linha in linhas]


def _recusa_de_permissao(exc: Exception) -> bool:
    texto = f"{type(exc).__name__} {getattr(exc, 'orig', '')} {exc}".lower()
    return "permission denied" in texto or "insufficientprivilege" in texto


def _ler_direto(sql: str, params: dict) -> list[dict]:
    from app.database import datamart_engine  # import tardio: testes substituem

    if datamart_engine is None:
        raise RuntimeError("datamart indisponivel")
    with datamart_engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def _ler_metabase(sql: str) -> list[dict]:
    from app import metabase_client  # import tardio: testes substituem

    return metabase_client.query(sql)


def consultar(sql: str, params: dict) -> list[dict]:
    """Direto no Data Mart; pelo Metabase so' se o banco recusar por permissao
    (ou nao houver engine) E houver `METABASE_API_KEY`."""
    try:
        return _ler_direto(sql, params)
    except Exception as exc:  # noqa: BLE001 — decide abaixo; o texto nunca sai
        sem_engine = isinstance(exc, RuntimeError) and str(exc) == "datamart indisponivel"
        if not (sem_engine or _recusa_de_permissao(exc)) or not settings.metabase_api_key:
            raise
        logger.info("datamart_leitura: leitura direta recusada (%s); usando o Metabase", type(exc).__name__)
    return normalizar(_ler_metabase(renderizar(sql, params)))
