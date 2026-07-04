"""
Parser numérico canônico dos exports Shopee (orders, ads, shop-stats).

Contrato (confirmado por auditoria de 100% das linhas de 85 arquivos
Order.all*.xlsx, 10 CSVs de ads e 25 xlsx de shop-stats em 2026-07-04 —
ver docs/runbook_shopee_raw.md):

- Formato efetivamente encontrado nas 3 fontes para colunas financeiras/
  contagem: decimal com ponto, sem separador de milhar (ex.: "1546.30",
  "38147.83"). Percentuais em shop-stats usam vírgula decimal, sempre
  abaixo de 100 (ex.: "3,84%"), tratados por outro parser (_parse_pct em
  _parser_shop_stats.py, não por esta função).
- Nenhum valor com separador de milhar, prefixo "R$", NBSP, "-"/"N/A"/
  vazio ou negativo foi encontrado nas colunas numéricas usadas pelo
  agregador de produção nos dados atuais — mas o parser aceita esses
  formatos deliberadamente, como proteção para exports futuros.

Formatos aceitos:
  "1234"          -> 1234.0
  "1234.56"       -> 1234.56   (formato real confirmado, ponto decimal)
  "1234,56"       -> 1234.56   (BR, vírgula decimal, sem milhar)
  "1.234,56"      -> 1234.56   (BR, ponto milhar + vírgula decimal)
  "R$ 1.234,56"   -> 1234.56   (prefixo de moeda, com/sem espaço, NBSP)
  "-1.234,56"     -> -1234.56  (negativo)

Retorno para ausência de valor:
  None, "", "-", "N/A"/"NA"/"NULL"/"NONE" (case-insensitive) -> None.
  None representa "sem valor" — o chamador decide se isso vira 0 na
  agregação (comportamento atual) ou é tratado de outra forma.

Comportamento para valor inválido (não vazio, não interpretável), para
NaN/±Infinity, e para o formato US (ver abaixo):
  levanta ShopeeNumericParseError — NUNCA retorna 0.0 silenciosamente
  para um valor que não pôde ser interpretado, e nunca devolve um float
  não finito. A mensagem da exceção NUNCA inclui o valor bruto da célula
  (nem `repr(val)`, nem qualquer fragmento do conteúdo original) — só uma
  descrição genérica do problema. Contexto útil para localizar a célula
  (marca, arquivo, linha, campo) é responsabilidade do chamador, que já
  não tem acesso ao conteúdo original nesta função.

  Garantia adicional: `ShopeeNumericParseError.__cause__` e `__context__`
  são sempre `None`. O `ValueError` interno de `float()` (cuja mensagem
  inclui o valor bruto, ex.: `"could not convert string to float: 'xyz'"`)
  nunca é encadeado — é convertido numa flag booleana antes de qualquer
  `raise`, para que nenhuma exceção esteja "sendo tratada" no momento em
  que `ShopeeNumericParseError` é levantada (não basta `from None`
  sozinho: isso só suprime a exibição de `__context__`, não o limpa —
  ver `pipelines/tests/test_shopee_numeric.py` para a verificação via
  `traceback.format_exception` e inspeção direta de `__context__`).

Formato US ("1,234.56", vírgula de milhar + ponto decimal) — decisão:
  REJEITADO explicitamente (ShopeeNumericParseError), nunca convertido
  silenciosamente. A posição relativa do último "," e do último "."
  decide o caso:
    - último "," depois do último "." (ex.: "1.234,56") -> BR: o "."
      é separador de milhar, a "," é decimal. Suportado.
    - último "," antes do último "." (ex.: "1,234.56") -> padrão US ou
      ambíguo -> rejeitado. Não há nenhuma evidência desse formato nas
      383.298 linhas de orders, 804 registros de ads nem 755 linhas de
      shop-stats auditadas (2026-07-04) — rejeitar é mais simples e mais
      seguro do que inferir automaticamente, dado que nenhum caso real
      precisa ser suportado.
"""
from __future__ import annotations

import math

_EMPTY_TOKENS = {"", "-", "N/A", "NA", "NULL", "NONE"}


class ShopeeNumericParseError(ValueError):
    """Valor numérico não vazio que não pôde ser interpretado.

    A mensagem nunca contém o valor bruto da célula (nem repr, nem
    conteúdo original) — só uma descrição genérica do problema."""


def _finite_or_raise(value: float) -> float:
    if not math.isfinite(value):
        raise ShopeeNumericParseError("valor numérico não finito (NaN/Infinity)") from None
    return value


def parse_brl_float(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, bool):
        raise ShopeeNumericParseError("valor booleano inesperado em campo numérico") from None
    if isinstance(val, (int, float)):
        return _finite_or_raise(float(val))

    s = str(val).replace("\xa0", " ").replace("R$", "").strip()
    s = s.replace(" ", "")
    if s.upper() in _EMPTY_TOKENS:
        return None

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # BR: "." é separador de milhar, "," é decimal (ex.: "1.234,56").
            s = s.replace(".", "").replace(",", ".")
        else:
            # "," antes do último "." -> padrão US ("1,234.56") ou
            # ambíguo -- rejeitado explicitamente, nunca interpretado.
            raise ShopeeNumericParseError("formato numérico ambíguo (padrão US ou inválido)") from None
    elif "," in s:
        s = s.replace(",", ".")

    # A conversão é tentada e o resultado (sucesso/falha) guardado numa
    # flag ANTES de qualquer `raise` — nunca dentro do bloco `except`.
    # Isso garante __context__ = None (não apenas __suppress_context__),
    # já que nenhuma exceção está "sendo tratada" no momento do `raise`:
    # o ValueError original de float() — que inclui o valor bruto em
    # `str(exc)` — nunca fica acessível via __cause__/__context__ da
    # exceção pública. Verificado empiricamente (não apenas por leitura
    # do código): ver pipelines/tests/test_shopee_numeric.py.
    conversion_ok = True
    value = None
    try:
        value = float(s)
    except ValueError:
        conversion_ok = False

    if not conversion_ok:
        raise ShopeeNumericParseError("valor numérico inválido") from None

    return _finite_or_raise(value)
