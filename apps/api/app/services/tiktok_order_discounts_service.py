"""Bloco "Descontos e subsídios do pedido — TikTok Shop" para /canais — UE8-I3.

Implementa o contrato §28 de docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md. Módulo
separado pela mesma razão de `affiliate_costs_service`: `performance_service` já
é grande, e este bloco tem regras próprias — grão diário, teto em D−1, dois
financiadores distintos, isolamento de falha — que não se misturam com o resto
de /canais.

O QUE ESTE MODULO NAO FAZ, E POR QUE
-----------------------------------
- **Não soma os dois componentes.** `seller_discount_signed` sai do bolso da
  marca e reduz a receita dela; `platform_subsidy_amount` é ressarcido pelo
  TikTok e NÃO a reduz. Somar funde dois caixas com donos diferentes. Não
  existe `total_discount`, e a ausência é intencional.
- **Não aplica `abs()` e não inverte sinal.** A única inversão do sistema
  acontece no sync (`-SUM(seller_discount)`); repeti-la aqui devolveria o valor
  ao positivo e inverteria a leitura contábil.
- **Não usa `official_gmv` como denominador das taxas.** O GMV já é líquido dos
  descontos; a razão sobre ele não teria significado. O denominador é sempre
  `full_product_value`.
- **Não calcula margem, caixa, receita econômica nem retorno.** Falta CMV, e
  nada aqui é fechamento financeiro.
- **Não mistura cancelados com comerciais.** São populações disjuntas; somá-las
  inventaria venda que não houve.
- **Não preenche ausência com zero.** `NULL` é indisponibilidade; `0` é medição
  igual a zero. Chave ausente não vira linha.
- **Não lê `raw` nem o Data Mart.** A única fonte é a fato já materializada.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.deps.period import today_brt
from app.services.performance_service import TIKTOK_ID

logger = logging.getLogger(__name__)

FACT_TABLE = "marts.fact_tiktok_order_discounts_daily"

#: Idade máxima da carga para ser chamada de `recent_load`. Espelha
#: `FRESHNESS_MAX_EXECUTION_AGE_HOURS` do bloco de afiliados: mesma pergunta,
#: mesmo limiar, para que as duas seções da página não classifiquem "recente"
#: de formas diferentes.
DISCOUNT_MAX_SNAPSHOT_AGE_HOURS = 30

#: Como a grade esperada de cobertura é construída. Valor FIXO, exposto no
#: payload para que o consumidor não precise adivinhar o universo: é a mesma
#: definição de `_coverage` no sync versionado.
COVERAGE_BASIS = "observed_grid"

# --- Notas FIXAS e sanitizadas. Nunca interpolam SQL, DSN, host, credencial
# --- nem mensagem de driver.
SELLER_NOTE = (
    "Desconto financiado pela marca: sai do bolso da marca e reduz a receita "
    "dela. Valor negativo, com o sinal da fonte."
)
SUBSIDY_NOTE = (
    "Subsídio financiado pelo TikTok Shop: a plataforma ressarce, então ele "
    "NÃO reduz a receita da marca e não deve ser somado ao desconto da marca."
)
LIMITATION_NOTE = (
    "Os dois componentes são exibidos separadamente e nunca somados, porque "
    "têm financiadores diferentes. A fonte é um retrato do pedido e pode ser "
    "revisada retroativamente. Não são receita líquida, caixa nem margem. "
    "Pedidos cancelados são contexto separado e não integram as vendas "
    "comerciais."
)
ERROR_NOTE = (
    "Não foi possível ler os descontos de pedido do TikTok nesta consulta. O "
    "restante da página não foi afetado."
)
NO_SOURCE_NOTE = (
    "Descontos de pedido só têm fonte confirmada no TikTok Shop. Selecione o "
    "TikTok Shop para ver o bloco."
)
NO_BRAND_NOTE = "Nenhuma marca elegível no filtro selecionado."

#: Literal EXIGIDO pelo contrato. Não reescrever: a página e os testes comparam
#: a frase exata.
D0_WARNING = (
    "O dia de hoje foi excluído: os pedidos ainda estão entrando e o número "
    "mudaria sozinho."
)
EMPTY_WINDOW_WARNING = (
    "A janela selecionada não tem nenhum dia fechado: só existem hoje e/ou "
    "datas futuras, e nada é exibido."
)
NO_DATA_WARNING = (
    "Nenhum pedido do TikTok Shop na janela e nas marcas selecionadas. "
    "Ausência de registro não é o mesmo que desconto zero."
)
COVERAGE_WARNING = (
    "Cobertura incompleta: {faltando} de {esperadas} chaves (dia × marca) da "
    "janela não têm linha na fonte. A ausência pode significar que não houve "
    "pedido naquele dia, ou uma lacuna de ingestão — as duas são "
    "indistinguíveis com as fontes atuais. As chaves ausentes NÃO foram "
    "preenchidas com zero."
)
STALE_WARNING = (
    "A última carga tem mais de {horas} horas. Os valores podem não refletir "
    "revisões recentes da fonte."
)
UNKNOWN_FRESHNESS_WARNING = (
    "Não foi possível classificar a idade da carga: o carimbo está ausente, "
    "sem fuso, ou à frente do relógio da aplicação."
)

#: Categoria FIXA registrada em log quando a consulta falha. Sem `exc_info`,
#: sem SQL, sem host, sem DSN — o traceback de `SQLAlchemyError` carrega
#: exatamente isso, e log de aplicação costuma sair do perímetro.
LOG_QUERY_FAILURE = "tiktok_order_discounts_block: consulta indisponivel"


# ---------------------------------------------------------------------------
# Classificacao de periodo — funcao PURA, sem banco
# ---------------------------------------------------------------------------

def _is_month_start(d: date) -> bool:
    return d.day == 1


def _is_month_end(d: date) -> bool:
    proximo = (date(d.year + 1, 1, 1) if d.month == 12
               else date(d.year, d.month + 1, 1))
    return (proximo - d).days == 1


def _months_spanned(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


class EffectiveWindow:
    """Janela efetiva do bloco, já com D0 e futuro removidos.

    `end` é `None` quando NADA sobrou — a janela pedida estava inteiramente em
    D0 ou no futuro. Nesse caso o chamador não consulta valores.
    """

    __slots__ = ("start", "end", "period_status", "d0_excluded")

    def __init__(self, start: date, end: date | None,
                 period_status: str, d0_excluded: bool) -> None:
        self.start = start
        self.end = end
        self.period_status = period_status
        self.d0_excluded = d0_excluded

    @property
    def day_count(self) -> int:
        """Dias de CALENDÁRIO na janela efetiva. Não é `date_count`, que conta
        só os dias com dado na fonte."""
        if self.end is None:
            return 0
        return (self.end - self.start).days + 1


def classify_discount_period(start: date, end: date,
                             today: date) -> EffectiveWindow:
    """Resolve a janela efetiva e classifica o período. PURA — zero I/O.

    Diferença deliberada em relação a `affiliate_costs_service.classify_period`:
    lá um período parcial devolve lista vazia e o bloco NÃO consulta valores,
    porque o grão é mensal e um mês pela metade pareceria comparável a um mês
    fechado. **Aqui o grão é diário**, então um recorte parcial é uma soma
    honesta de dias fechados. `period_status` DESCREVE o período; não bloqueia
    a consulta.

    O teto é sempre `today − 1` em America/São_Paulo. D0 nunca aparece: os
    pedidos ainda estão entrando e o número mudaria sozinho.
    """
    ultimo_fechado = today - timedelta(days=1)
    alcanca_d0 = end > ultimo_fechado
    fim_efetivo = min(end, ultimo_fechado)

    if start > fim_efetivo:
        # Janela inteiramente em D0/futuro: nada sobrou para consultar.
        return EffectiveWindow(start, None, "partial_month", alcanca_d0)

    if alcanca_d0:
        # Contrato: o corte de D0 torna o período parcial, qualquer que fosse
        # o alinhamento pedido. Um mês truncado hoje não é mês fechado.
        return EffectiveWindow(start, fim_efetivo, "partial_month", True)

    if _is_month_start(start) and _is_month_end(fim_efetivo):
        inicio_mes_atual = date(today.year, today.month, 1)
        if fim_efetivo < inicio_mes_atual:
            status = ("complete_month" if _months_spanned(start, fim_efetivo) == 1
                      else "complete_months")
            return EffectiveWindow(start, fim_efetivo, status, False)

    mesmo_mes = (start.year, start.month) == (fim_efetivo.year, fim_efetivo.month)
    return EffectiveWindow(start, fim_efetivo,
                           "partial_month" if mesmo_mes else "not_month_aligned",
                           False)


# ---------------------------------------------------------------------------
# Conversoes — `None` e zero NUNCA se confundem
# ---------------------------------------------------------------------------

def _float_or_none(v) -> float | None:
    """Converte preservando o SINAL e mantendo `None` como `None`.

    `None` é ausência de medição; `0` é medição igual a zero. Trocar um pelo
    outro inventaria ou apagaria informação.
    """
    if v is None:
        return None
    return float(v) if isinstance(v, Decimal) else float(v)


def _rate(numerador, denominador) -> float | None:
    """Percentual sobre `full_product_value`, com o SINAL preservado.

    Denominador ausente ou zero devolve `None`: não existe taxa quando não há
    base. Zero no numerador com base válida devolve `0.0` — medição real.

    Nunca `abs()`: a taxa da marca sai negativa porque o desconto é negativo, e
    a da plataforma sai positiva porque o subsídio é positivo. É o sinal que
    diz quem financia o quê.
    """
    if numerador is None or denominador is None:
        return None
    base = Decimal(str(denominador))
    if base == 0:
        return None
    return float(Decimal(str(numerador)) / base * 100)


def _iso(v) -> str | None:
    return None if v is None else v.isoformat()


def classify_discount_freshness(refreshed_at: datetime | None,
                                agora: datetime | None = None) -> str:
    """Idade da CARGA. PURA — zero I/O.

    Os nomes falam de CARGA, não de dado: `recent_load` diz que o sync rodou há
    pouco, e nada além disso. Não diz que o dado é atual, estável, maduro ou
    fechado — a fonte é um retrato do pedido e pode ser revisada
    retroativamente. `current_snapshot` foi descartado justamente por sugerir
    a propriedade errada.

    Três casos, e `unknown` cobre os dois defeitos:
      - ausente        -> não há o que classificar;
      - naive          -> `synced_at` é TIMESTAMPTZ; um naive significa que a
                          coluna mudou de tipo, e classificar inventaria fuso;
      - no FUTURO      -> relógio inconsistente entre banco e aplicação. Idade
                          negativa passaria trivialmente por "recente" e
                          esconderia justamente o defeito.
    """
    if refreshed_at is None or refreshed_at.tzinfo is None:
        return "unknown"
    agora = agora or datetime.now(timezone.utc)
    idade_h = (agora - refreshed_at).total_seconds() / 3600.0
    if idade_h < 0:
        return "unknown"
    return ("recent_load" if idade_h <= DISCOUNT_MAX_SNAPSHOT_AGE_HOURS
            else "stale_load")


# ---------------------------------------------------------------------------
# Consulta UNICA
# ---------------------------------------------------------------------------

def _scope_sql(brand_sql: str, brand_sql_conhecidas: str):
    """Consulta ÚNICA do bloco: valores por marca, cobertura e metadados.

    Um round-trip só. Três decisões estruturais:

    1. `meta LEFT JOIN agregado ON TRUE` garante **pelo menos uma linha** mesmo
       quando nenhuma marca tem dado — os metadados sobrevivem, e a linha com
       `brand IS NULL` é descartada pelo chamador em vez de virar um zero
       fabricado.
    2. `marcas_conhecidas` conta sobre a fato INTEIRA, não sobre a janela: é a
       grade ESPERADA. Contá-la dentro da janela seria circular — tudo que
       existisse estaria presente, e a cobertura seria sempre `complete`.
    3. `source_max_date` também é da fato inteira: diz até onde a carga chegou,
       independentemente do recorte pedido.

    Colunas EXPLÍCITAS, nunca `SELECT *`. Zero `COALESCE`: transformar ausência
    de origem em zero é exatamente o que este bloco não pode fazer.
    """
    return text(f"""
        WITH escopo AS (
            SELECT
                f.ref_date,
                f.brand,
                f.commercial_orders,
                f.official_gmv,
                f.full_product_value,
                f.seller_discount_signed,
                f.platform_subsidy_amount,
                f.cancelled_orders,
                f.cancelled_seller_discount_signed,
                f.cancelled_platform_subsidy_amount,
                f.source_max_updated_at,
                f.synced_at
            FROM {FACT_TABLE} f
            WHERE f.ref_date >= :start AND f.ref_date <= :end{brand_sql}
        ),
        agregado AS (
            SELECT
                e.brand,
                SUM(e.commercial_orders)                     AS commercial_orders,
                SUM(e.official_gmv)                          AS official_gmv,
                SUM(e.full_product_value)                    AS full_product_value,
                SUM(e.seller_discount_signed)                AS seller_discount_signed,
                SUM(e.platform_subsidy_amount)               AS platform_subsidy_amount,
                SUM(e.cancelled_orders)                      AS cancelled_orders,
                SUM(e.cancelled_seller_discount_signed)      AS cancelled_seller_discount_signed,
                SUM(e.cancelled_platform_subsidy_amount)     AS cancelled_platform_subsidy_amount,
                COUNT(*)                                     AS chaves_da_marca
            FROM escopo e
            GROUP BY e.brand
        ),
        marcas_conhecidas AS (
            SELECT DISTINCT g.brand
            FROM {FACT_TABLE} g
            WHERE TRUE{brand_sql_conhecidas}
        ),
        meta AS (
            SELECT
                -- Grade OBSERVADA, identica a `_coverage` do sync versionado:
                -- (dias com atividade) x (marcas com atividade), derivada da
                -- propria fotografia. O produto e' calculado em Python; aqui
                -- saem os tres fatores.
                (SELECT COUNT(DISTINCT e.ref_date) FROM escopo e)        AS dias_observados,
                (SELECT COUNT(DISTINCT e.brand) FROM escopo e)           AS marcas_observadas,
                (SELECT COUNT(*) FROM escopo e)                          AS chaves_presentes,
                (SELECT MAX(e.synced_at) FROM escopo e)                  AS discounts_refreshed_at,
                (SELECT MAX(e.source_max_updated_at) FROM escopo e)      AS source_max_updated_at,
                -- Maximo do ESCOPO EFETIVO -- mesma janela, mesmas marcas,
                -- mesmo teto. Um maximo global afirmaria que a selecao esta
                -- atualizada ate uma data que a MARCA filtrada nao alcancou.
                (SELECT MAX(e.ref_date) FROM escopo e)                   AS source_max_date,
                -- Marcas que a FATO conhece dentro do filtro. Nao entra na
                -- cobertura: serve so' para distinguir "filtro nao casa marca
                -- alguma" de "marca existe, mas nao vendeu nesta janela".
                (SELECT COUNT(*) FROM marcas_conhecidas)                 AS marcas_conhecidas
        )
        SELECT
            m.dias_observados,
            m.marcas_observadas,
            m.chaves_presentes,
            m.discounts_refreshed_at,
            m.source_max_updated_at,
            m.source_max_date,
            m.marcas_conhecidas,
            a.brand,
            a.commercial_orders,
            a.official_gmv,
            a.full_product_value,
            a.seller_discount_signed,
            a.platform_subsidy_amount,
            a.cancelled_orders,
            a.cancelled_seller_discount_signed,
            a.cancelled_platform_subsidy_amount,
            a.chaves_da_marca
        FROM meta m
        LEFT JOIN agregado a ON TRUE
        ORDER BY a.brand NULLS LAST
    """)


# ---------------------------------------------------------------------------
# Montagem do bloco
# ---------------------------------------------------------------------------

def _empty_block(janela: EffectiveWindow, availability: str,
                 limitation: str, warnings: list[str], *,
                 coverage: str = "unknown") -> dict:
    """Bloco sem linha monetária.

    `freshness_status` é SEMPRE `unknown` aqui: nenhuma fotografia foi lida, e
    dizer qualquer coisa sobre a idade de um dado que ninguém consultou seria
    afirmação sem medição.
    """
    return {
        "availability_status": availability,
        "period_status": janela.period_status,
        "coverage_status": coverage,
        "coverage_basis": COVERAGE_BASIS,
        # Nenhuma grade foi observada: zero em vez de numero inventado.
        "coverage_expected_keys": 0,
        "coverage_present_keys": 0,
        "coverage_missing_keys": 0,
        "freshness_status": "unknown",
        "rows": [],
        "date_from": janela.start if janela.end is not None else None,
        "date_to": janela.end,
        "date_count": 0,
        "discounts_refreshed_at": None,
        "source_max_date": None,
        "source_max_updated_at": None,
        "seller_note": SELLER_NOTE,
        "subsidy_note": SUBSIDY_NOTE,
        "limitation_note": limitation,
        "warnings": warnings,
    }


def build_tiktok_order_discounts_block(
    db: Session, mkt_ids: list[int], start: date, end: date, *,
    brand_keys: list[str] | None = None,
    today: date | None = None,
    agora: datetime | None = None,
) -> dict:
    """Monta o bloco. Falha de banco é tratada em `safe_...` abaixo.

    `today` resolve em **America/São_Paulo** (`today_brt`), nunca no fuso do SO
    nem em UTC: entre 21h e 00h BRT o UTC já virou o dia, e o teto D−1 saltaria
    24 h antes da hora, publicando D0.
    """
    today = today or today_brt()
    janela = classify_discount_period(start, end, today)
    avisos: list[str] = []
    if janela.d0_excluded:
        avisos.append(D0_WARNING)

    # TikTok fora do filtro: o bloco existe, declara indisponibilidade e NAO
    # consulta valor nenhum.
    if TIKTOK_ID not in mkt_ids:
        return _empty_block(janela, "unavailable_no_source", LIMITATION_NOTE,
                            avisos + [NO_SOURCE_NOTE])

    # Nada sobrou depois de cortar D0/futuro. HTTP 200, rows vazio, aviso
    # explícito — nunca dado antigo de outra janela.
    if janela.end is None:
        return _empty_block(janela, "available", LIMITATION_NOTE,
                            avisos + [EMPTY_WINDOW_WARNING])

    params: dict = {"start": janela.start, "end": janela.end}
    # Dois predicados EXPLICITOS em vez de reescrever o alias por substituicao
    # de string: os CTEs usam aliases diferentes (`f` no escopo, `g` nas marcas
    # conhecidas), e derivar um do outro por `replace` quebraria em silencio se
    # o alias mudasse.
    brand_sql = ""
    brand_sql_conhecidas = ""
    if brand_keys is not None:
        if not brand_keys:
            return _empty_block(janela, "no_eligible_brand", LIMITATION_NOTE,
                                avisos + [NO_BRAND_NOTE])
        params["brands"] = list(brand_keys)
        brand_sql = " AND f.brand = ANY(:brands)"
        brand_sql_conhecidas = " AND g.brand = ANY(:brands)"

    registros = db.execute(
        _scope_sql(brand_sql, brand_sql_conhecidas), params).mappings().all()

    if not registros:
        # Nem a linha de metadados voltou: a fato não tem nenhuma marca.
        return _empty_block(janela, "available", LIMITATION_NOTE,
                            avisos + [NO_DATA_WARNING])

    cabecalho = registros[0]
    if int(cabecalho["marcas_conhecidas"] or 0) == 0:
        # O filtro nao intersecta NENHUMA marca que a fonte conhece. Distinto
        # de "a marca existe, mas nao vendeu nesta janela", que e' `available`
        # com grade observada vazia.
        return _empty_block(janela, "no_eligible_brand", LIMITATION_NOTE,
                            avisos + [NO_BRAND_NOTE])

    rows: list[dict] = []
    for r in registros:
        # `brand IS NULL` é a linha SÓ de metadados produzida pelo
        # `LEFT JOIN ... ON TRUE` quando nenhuma marca tem dado na janela.
        # NUNCA vira linha monetária: fabricar R$ 0,00 afirmaria que não houve
        # desconto, quando o que houve foi ausência de pedido.
        if r["brand"] is None:
            continue
        fpv = r["full_product_value"]
        rows.append({
            "brand": r["brand"],
            "commercial_orders": int(r["commercial_orders"] or 0),
            "official_gmv": _float_or_none(r["official_gmv"]),
            "full_product_value": _float_or_none(fpv),
            "seller_discount_signed": _float_or_none(r["seller_discount_signed"]),
            "platform_subsidy_amount": _float_or_none(r["platform_subsidy_amount"]),
            "cancelled_orders": int(r["cancelled_orders"] or 0),
            "cancelled_seller_discount_signed":
                _float_or_none(r["cancelled_seller_discount_signed"]),
            "cancelled_platform_subsidy_amount":
                _float_or_none(r["cancelled_platform_subsidy_amount"]),
            "seller_discount_rate": _rate(r["seller_discount_signed"], fpv),
            "platform_subsidy_rate": _rate(r["platform_subsidy_amount"], fpv),
        })

    # GRADE OBSERVADA — a MESMA definição de `_coverage` no sync versionado
    # (`pipelines/sync_tiktok_order_discounts_daily.py`): o produto
    # (dias com atividade) × (marcas com atividade), derivado da própria
    # fotografia, respeitando o filtro. Nunca dias de calendário: um dia em
    # que NENHUMA marca vendeu não é evidência de lacuna, e contá-lo faria o
    # mesmo `CoverageStatus` significar universos diferentes na carga e na
    # exposição.
    date_count = int(cabecalho["dias_observados"] or 0)
    marcas_observadas = int(cabecalho["marcas_observadas"] or 0)
    presentes = int(cabecalho["chaves_presentes"] or 0)
    esperadas = date_count * marcas_observadas
    ausentes = max(0, esperadas - presentes)

    if not rows or esperadas <= 0:
        cobertura = "unknown"
    elif ausentes == 0:
        cobertura = "complete"
    else:
        cobertura = "incomplete_brand_coverage"

    if cobertura == "incomplete_brand_coverage":
        avisos.append(COVERAGE_WARNING.format(
            faltando=ausentes, esperadas=esperadas))

    refreshed = cabecalho["discounts_refreshed_at"]
    frescor = classify_discount_freshness(refreshed, agora)
    if frescor == "stale_load":
        avisos.append(STALE_WARNING.format(
            horas=DISCOUNT_MAX_SNAPSHOT_AGE_HOURS))
    elif frescor == "unknown" and rows:
        avisos.append(UNKNOWN_FRESHNESS_WARNING)

    if not rows:
        avisos.append(NO_DATA_WARNING)

    return {
        "availability_status": "available",
        "period_status": janela.period_status,
        "coverage_status": cobertura,
        "coverage_basis": COVERAGE_BASIS,
        "coverage_expected_keys": esperadas,
        "coverage_present_keys": presentes,
        "coverage_missing_keys": ausentes,
        "freshness_status": frescor,
        "rows": rows,
        "date_from": janela.start,
        "date_to": janela.end,
        "date_count": date_count,
        "discounts_refreshed_at": _iso(refreshed),
        "source_max_date": _iso(cabecalho["source_max_date"]),
        # Timestamp NAIVE da fonte: sai sem conversão e sem rótulo de fuso.
        "source_max_updated_at": _iso(cabecalho["source_max_updated_at"]),
        "seller_note": SELLER_NOTE,
        "subsidy_note": SUBSIDY_NOTE,
        "limitation_note": LIMITATION_NOTE,
        "warnings": avisos,
    }


def safe_tiktok_order_discounts_block(
    db: Session, mkt_ids: list[int], start: date, end: date, *,
    brand_keys: list[str] | None = None,
    today: date | None = None,
    agora: datetime | None = None,
) -> dict:
    """Isolamento de falha: captura SOMENTE erro esperado da camada de banco.

    `SQLAlchemyError` cobre indisponibilidade, timeout, tabela ausente e erro de
    SQL — as falhas que este bloco pode legitimamente sofrer sem que o resto de
    /canais deva cair.

    Um `except Exception` amplo esconderia bug do próprio bloco (`KeyError`,
    `TypeError`, `AttributeError`) sob um estado de "erro de fonte", e o defeito
    viveria em produção parecendo indisponibilidade. Esses sobem e falham alto.

    A nota devolvida é FIXA e sanitizada, e o log é uma CATEGORIA fixa sem
    `exc_info`: o traceback de `SQLAlchemyError` carrega o SQL e, dependendo do
    driver, host e parâmetros de conexão — e log de aplicação costuma sair do
    perímetro.
    """
    try:
        return build_tiktok_order_discounts_block(
            db, mkt_ids, start, end, brand_keys=brand_keys,
            today=today, agora=agora)
    except SQLAlchemyError:
        logger.warning(LOG_QUERY_FAILURE)
        janela = classify_discount_period(start, end, today or today_brt())
        avisos = [D0_WARNING] if janela.d0_excluded else []
        return _empty_block(janela, "error", ERROR_NOTE, avisos + [ERROR_NOTE])
