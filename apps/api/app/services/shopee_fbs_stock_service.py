"""Estoque Full (FBS) da Shopee — Gate FULL-SOURCE-3. ESTRITAMENTE READ-ONLY.

Le SOMENTE `marts.fact_shopee_fbs_stock_daily` e
`marts.fact_shopee_fbs_stock_location_daily`. Nenhuma consulta a' `raw`, a'
`silver`, ao Data Mart ou a' API da Shopee acontece durante o request.

ESTA CAMADA NAO CLASSIFICA
---------------------------
`classificacao_torre`, `cobertura_torre_dias` e `avg_daily_units_28d` sao LIDOS
da fato, nunca recalculados aqui. Duas implementacoes da mesma regra divergem
no primeiro ajuste, e a que a Torre mostraria seria a errada. A fato e' a
autoridade; esta camada soma, filtra e ordena.

Os limiares em `COBERTURA_*_DIAS` existem so' para EXIBIR "abaixo de 7 dias" na
tela. Se algum dia divergirem do pipeline, quem esta' certo e' a coluna
`classificacao_torre` ja' gravada -- por isso a tela rotula a faixa, e nao
reclassifica a linha.

INDISPONIVEL NAO E' ZERO
-------------------------
Tres causas distintas de "sem numero", e cada uma tem motivo proprio:
    feature_flag_desligada    -> decisao nossa, tratada no router
    fato_inexistente          -> migrations 020/021 ainda nao aplicadas
    sem_fotografia_publicada  -> tabela existe, pipeline nunca rodou
Nenhuma delas pode chegar a' tela como estoque 0. Um zero diz "medimos e nao
ha estoque" e dispara reposicao; ausencia diz "nao sabemos".

A deteccao usa `to_regclass`, que devolve NULL para relacao inexistente em vez
de levantar -- consultar a tabela direto abortaria a transacao e transformaria
"ainda nao migrou" em erro 500.

KITS FICAM FORA DO OPERACIONAL
-------------------------------
A fato publica kit com `classificacao_torre = 'KIT_NAO_CONCILIADO'` porque a
semantica de estoque de kit nunca foi conciliada com o Seller Center. As
unidades de kit sao contadas em linha SEPARADA dos indicadores, nunca somadas
ao operacional.

SEGURANCA
---------
Allowlist explicita de colunas; nunca `SELECT *`. Todo filtro e' parametrizado
(`= ANY(:brands)`), nenhum valor e' interpolado no texto do SQL -- inclusive a
busca textual, que escapa os curingas do LIKE antes de virar parametro.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

FACT_TABLE = "marts.fact_shopee_fbs_stock_daily"
FACT_LOCATION_TABLE = "marts.fact_shopee_fbs_stock_location_daily"

#: Contas da esteira API. ALLOWLIST: ausencia de filtro significa exatamente
#: estas quatro, nunca "tudo que estiver na tabela".
EXPECTED_ACCOUNTS = ("apice", "barbours", "lescent", "rituaria")
EXPECTED_BRANDS = ("apice", "barbours", "lescent", "rituaria")

#: Marcas Shopee FORA da esteira API. Declaradas para que a ausencia viaje no
#: payload em vez de virar silencio.
BRANDS_NOT_COVERED = ("kokeshi",)

SCOPE_LABEL = "Estoque Full Shopee — cobertura API"

#: Dominio FECHADO, espelhando o CHECK `ck_fsfs_classificacao_conhecida`.
#: Valor fora disto e' contrato quebrado da fonte.
CLASSIFICACOES = (
    "RUPTURA_CANDIDATA",
    "BAIXO_CANDIDATO",
    "EXCESSO_CANDIDATO",
    "SEM_GIRO_CANDIDATO",
    "SUFICIENTE",
    "SEM_DEMANDA_MEDIDA",
    "KIT_NAO_CONCILIADO",
)

#: As que a operacao precisa olhar HOJE. Usadas pelo filtro "somente itens que
#: exigem acao" e pelo indicador `produtos_exigem_acao`.
CLASSIFICACOES_EXIGEM_ACAO = ("RUPTURA_CANDIDATA", "BAIXO_CANDIDATO")

#: 🔴 PROVISORIOS: nao foram ratificados para a Shopee. Ver docstring -- sao
#: rotulo de exibicao, nao regra de classificacao.
COBERTURA_BAIXA_DIAS = 7
COBERTURA_EXCESSO_DIAS = 90
JANELA_VENDAS_DIAS = 28

#: A carga e' MANUAL e nao ha DAG.
LOAD_MODE = "manual_snapshot"
NO_AUTOMATION = True

#: Teto de linhas por resposta. A fato tem ~300 produtos FBS hoje, entao o teto
#: nunca e' atingido -- ele existe para que um crescimento de catalogo nao vire
#: payload de megabytes sem ninguem perceber. Quando corta, a resposta DIZ.
MAX_PRODUTOS = 1000

# ---------------------------------------------------------------------------
# TEXTO QUE VAI PARA A TELA
# ---------------------------------------------------------------------------
# Estas constantes sao LIDAS PELO GESTOR: viajam no payload e a tela as imprime
# como estao. Por isso levam acentuacao correta, ao contrario dos comentarios e
# docstrings deste arquivo, que seguem a convencao ASCII do repositorio.
# Medido na captura local do gate: sem acento, a nota de limiares aparecia como
# "Limiares PROVISORIOS, nao ratificados" no meio de uma tela acentuada.

MOTIVO_FATO_INEXISTENTE = (
    "A fotografia de Estoque Full ainda não existe neste ambiente: as "
    "migrations 020 e 021 não foram aplicadas. Não há estoque medido — isto "
    "não significa estoque zero."
)
MOTIVO_SEM_FOTOGRAFIA = (
    "A tabela de Estoque Full existe, mas nenhuma fotografia foi publicada "
    "ainda: a carga `sync_shopee_fbs_stock_daily` nunca rodou neste ambiente. "
    "Não há estoque medido — isto não significa estoque zero."
)

LIMITACAO_COBERTURA = (
    "Cobertura PARCIAL: a esteira de API cobre apice, barbours, lescent e "
    "rituaria. Kokeshi NÃO está na API — este total é “Estoque Full Shopee "
    "(cobertura API)”, nunca “Shopee total”."
)
LIMITACAO_CARGA_MANUAL = (
    "Carga MANUAL: não existe agendamento para esta fotografia. A data "
    "mostrada é a da última execução autorizada, não de um ciclo diário."
)
LIMITACAO_KITS = (
    "Kits ficam FORA dos indicadores operacionais: a semântica de estoque de "
    "kit não foi conciliada com o Seller Center. Aparecem em contagem própria."
)
LIMITACAO_COBERTURA_TORRE = (
    "“Cobertura da Torre” é cálculo NOSSO: estoque vendável dividido pela "
    "média diária de 28 dias. NÃO reproduz nenhuma fórmula da Shopee e não "
    "deve ser apresentada como número oficial do marketplace."
)
LIMITACAO_LIMIARES = (
    f"Limiares PROVISÓRIOS, não ratificados: abaixo de {COBERTURA_BAIXA_DIAS} "
    f"dias é “baixo”, a partir de {COBERTURA_EXCESSO_DIAS} dias é “excesso”."
)
LIMITACAO_CONTEXTO = (
    "“Reservado”, “estoque do vendedor” e “disponível (Shopee)” são CONTEXTO: "
    "não são estoque Full e não entram em cobertura nem em classificação."
)
LIMITACAO_DEMANDA = (
    f"Demanda = unidades de pedidos PAGOS nos {JANELA_VENDAS_DIAS} dias "
    "completos anteriores à fotografia. Pedidos não pagos ficam de fora: "
    "nunca consumiram estoque. O critério antigo, que os inclui, viaja "
    "separado, apenas como contexto."
)


class EstoqueFullIndisponivel(Exception):
    """Base. Carrega o motivo tecnico que o router traduz para o payload."""

    motivo_tecnico = "fato_inexistente"
    mensagem = MOTIVO_FATO_INEXISTENTE


class FatoInexistente(EstoqueFullIndisponivel):
    motivo_tecnico = "fato_inexistente"
    mensagem = MOTIVO_FATO_INEXISTENTE


class SemFotografiaPublicada(EstoqueFullIndisponivel):
    motivo_tecnico = "sem_fotografia_publicada"
    mensagem = MOTIVO_SEM_FOTOGRAFIA


class EstoqueFullContractError(Exception):
    """A fonte devolveu algo fora do dominio declarado."""


class FiltroInvalido(ValueError):
    """Filtro fora da allowlist.

    `mensagem_segura` e' montada SO' com constantes do servidor: ela lista o
    que e' aceito e NUNCA ecoa o que foi recebido. Devolver a entrada no corpo
    da resposta transformaria o endpoint em espelho de texto arbitrario.
    O valor recusado fica em `recebidos`, para log interno -- nao para o HTTP.
    """

    def __init__(self, campo: str, permitidos: Sequence[str],
                 recebidos: Sequence[str]):
        self.campo = campo
        self.permitidos = tuple(permitidos)
        self.recebidos = tuple(recebidos)
        self.mensagem_segura = (
            f"Filtro “{campo}” inválido. Valores aceitos: "
            f"{', '.join(permitidos)}.")
        super().__init__(self.mensagem_segura)


# ---------------------------------------------------------------------------
# SQL — allowlist de colunas, filtros sempre parametrizados
# ---------------------------------------------------------------------------

SQL_SNAPSHOT_COERENTE = text(
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

#: `to_regclass` devolve NULL em vez de levantar. Ver docstring.
SQL_FATO_EXISTE = text("""
    SELECT to_regclass(:fato)     IS NOT NULL AS tem_agregado,
           to_regclass(:fato_loc) IS NOT NULL AS tem_grao_fino
""")

SQL_ULTIMA_FOTOGRAFIA = text(f"""
    SELECT MAX(ref_date) AS ref_date FROM {FACT_TABLE}
""")

#: Filtro de escopo compartilhado entre indicadores e listagem. Marca e conta
#: sao dominios DIFERENTES: uma marca poderia ganhar uma segunda loja.
_ESCOPO = """
       AND (:brands   IS NULL OR brand        = ANY(:brands))
       AND (:accounts IS NULL OR shop_account = ANY(:accounts))
"""

#: Indicadores: NAO recebem os filtros de classificacao/busca/acao. Os cartoes
#: descrevem o ESCOPO (conta/marca); a tabela abaixo deles e' que se refina.
#: Se os cartoes seguissem o filtro, "3 rupturas" viraria "1 ruptura" so' por
#: alguem ter digitado um SKU na busca.
SQL_INDICADORES = text(f"""
    SELECT
        COALESCE(SUM(full_stock_saleable) FILTER (WHERE NOT is_kit), 0)
            AS unidades_vendaveis_operacional,
        COALESCE(SUM(full_stock_saleable) FILTER (WHERE is_kit), 0)
            AS unidades_vendaveis_kits_contexto,
        COALESCE(SUM(full_stock_saleable), 0) AS unidades_vendaveis_total,
        COUNT(*) FILTER (WHERE classificacao_torre = 'RUPTURA_CANDIDATA')
            AS produtos_ruptura,
        COUNT(*) FILTER (WHERE classificacao_torre = 'BAIXO_CANDIDATO')
            AS produtos_baixo,
        COUNT(*) FILTER (WHERE classificacao_torre = 'EXCESSO_CANDIDATO')
            AS produtos_excesso,
        COUNT(*) FILTER (WHERE classificacao_torre = 'SEM_GIRO_CANDIDATO')
            AS produtos_sem_giro,
        COUNT(*) FILTER (WHERE classificacao_torre = 'SUFICIENTE')
            AS produtos_suficientes,
        COUNT(*) FILTER (WHERE classificacao_torre = 'SEM_DEMANDA_MEDIDA')
            AS produtos_sem_demanda_medida,
        COUNT(*) FILTER (WHERE classificacao_torre = 'KIT_NAO_CONCILIADO')
            AS produtos_kit_nao_conciliado,
        COUNT(*) FILTER (WHERE classificacao_torre = ANY(:acao))
            AS produtos_exigem_acao,
        COUNT(*) AS produtos_total
      FROM {FACT_TABLE}
     WHERE ref_date = :ref_date
    {_ESCOPO}
""")

#: Filtros da TABELA. `:q IS NULL` desliga a busca sem ramificar o SQL.
#: A busca casa SKU **ou** nome: quem digita "ABC12" nao sabe em qual dos dois
#: o texto esta'. O ESCAPE neutraliza `%` e `_` digitados pelo usuario.
_FILTROS_TABELA = """
       AND (:classificacoes IS NULL
            OR classificacao_torre = ANY(:classificacoes))
       AND (:somente_acao = FALSE OR classificacao_torre = ANY(:acao))
       AND (:q IS NULL
            OR COALESCE(item_sku, '')  ILIKE :q ESCAPE '!'
            OR COALESCE(item_name, '') ILIKE :q ESCAPE '!')
"""

SQL_TOTAL_NO_FILTRO = text(f"""
    SELECT COUNT(*) AS total
      FROM {FACT_TABLE}
     WHERE ref_date = :ref_date
    {_ESCOPO}
    {_FILTROS_TABELA}
""")

#: Ordenacao OPERACIONAL, nao alfabetica: a tela existe para decidir reposicao.
#: Ruptura primeiro, depois baixo; dentro da faixa, menor cobertura primeiro
#: (NULLS LAST porque "sem venda" nao e' urgencia). O desempate por chave
#: completa mantem a ordem estavel entre requisicoes.
SQL_PRODUTOS = text(f"""
    SELECT ref_date, brand, shop_account, item_id, model_id,
           item_name, item_sku, item_status, is_kit,
           full_stock_saleable, full_stock_total, location_count,
           seller_stock_total, reserved_stock, summary_available_stock,
           units_sold_28d, days_with_sales_28d,
           units_sold_28d_legado_com_unpaid, avg_daily_units_28d,
           cobertura_torre_dias, classificacao_torre, vinculo_vendas
      FROM {FACT_TABLE}
     WHERE ref_date = :ref_date
    {_ESCOPO}
    {_FILTROS_TABELA}
     ORDER BY
        CASE classificacao_torre
            WHEN 'RUPTURA_CANDIDATA'  THEN 0
            WHEN 'BAIXO_CANDIDATO'    THEN 1
            WHEN 'EXCESSO_CANDIDATO'  THEN 2
            WHEN 'SEM_GIRO_CANDIDATO' THEN 3
            ELSE 4
        END,
        cobertura_torre_dias ASC NULLS LAST,
        full_stock_saleable DESC,
        shop_account, item_id, model_id
     LIMIT :limite
""")

#: Grao fino: onde o estoque esta'. Buscado para os itens JA' selecionados,
#: por chave completa, para nao trazer o catalogo inteiro.
SQL_POR_CD = text(f"""
    SELECT shop_account, item_id, model_id, location_id, full_stock, is_saleable
      FROM {FACT_LOCATION_TABLE}
     WHERE ref_date = :ref_date
       AND (shop_account, item_id, model_id) IN (
           SELECT * FROM unnest(
               CAST(:contas AS text[]), CAST(:itens AS text[]),
               CAST(:modelos AS text[]))
       )
     ORDER BY full_stock DESC, location_id
""")

SQL_FRESCOR = text(f"""
    SELECT MAX(source_captured_at) AS source_captured_at,
           MAX(ingested_at)        AS ingested_at
      FROM {FACT_TABLE}
     WHERE ref_date = :ref_date
""")

SQL_CONTAS_OBSERVADAS = text(f"""
    SELECT DISTINCT shop_account
      FROM {FACT_TABLE}
     WHERE ref_date = :ref_date
    {_ESCOPO}
     ORDER BY shop_account
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def escapar_like(termo: str) -> str:
    """Neutraliza os curingas do LIKE e devolve o padrao `%termo%`.

    Sem isso, um usuario digitando `100%` faria `%` casar com qualquer coisa e
    a busca devolveria o catalogo inteiro -- parecendo filtro quebrado. O
    proprio caractere de escape tem de ser escapado PRIMEIRO, senao escaparia
    o escape.
    """
    limpo = (termo.replace("!", "!!")
                  .replace("%", "!%")
                  .replace("_", "!_"))
    return f"%{limpo}%"


def _validar_lista(valores: Optional[Sequence[str]],
                   permitidos: Sequence[str],
                   campo: str) -> Optional[list[str]]:
    """Fail-closed: valor fora da allowlist e' erro, nao filtro vazio.

    Devolver lista vazia silenciosamente faria a tela mostrar zero produtos e
    parecer "sem estoque" -- exatamente a confusao que este modulo evita.
    """
    if valores is None:
        return None
    normalizados = [v.strip().lower() for v in valores if v and v.strip()]
    if not normalizados:
        return None
    desconhecidos = sorted(set(normalizados) - set(permitidos))
    if desconhecidos:
        raise FiltroInvalido(campo, permitidos, desconhecidos)
    # `dict.fromkeys` deduplica PRESERVANDO a ordem pedida.
    return list(dict.fromkeys(normalizados))


def _dias_desde(ref: date, hoje: date) -> int:
    return (hoje - ref).days


def _opt_int(valor) -> Optional[int]:
    """`None` continua `None`. Nao vira 0: a coluna e' anulavel de proposito."""
    return None if valor is None else int(valor)


def fato_disponivel(db: Session) -> bool:
    """As DUAS tabelas precisam existir: a tela usa agregado e grao fino."""
    linha = db.execute(SQL_FATO_EXISTE, {
        "fato": FACT_TABLE, "fato_loc": FACT_LOCATION_TABLE}).mappings().one()
    return bool(linha["tem_agregado"]) and bool(linha["tem_grao_fino"])


def _carregar_por_cd(db: Session, ref_date: date,
                     linhas: list[dict]) -> dict[tuple, list]:
    """Distribuicao por CD dos itens JA' selecionados. Uma consulta, nao N."""
    from app.schemas.shopee_fbs_stock import EstoquePorCD

    if not linhas:
        return {}
    contas = [r["shop_account"] for r in linhas]
    itens = [r["item_id"] for r in linhas]
    modelos = [r["model_id"] for r in linhas]

    agrupado: dict[tuple, list] = {}
    for r in db.execute(SQL_POR_CD, {
        "ref_date": ref_date, "contas": contas,
        "itens": itens, "modelos": modelos,
    }).mappings().all():
        chave = (r["shop_account"], r["item_id"], r["model_id"])
        agrupado.setdefault(chave, []).append(EstoquePorCD(
            location_id=r["location_id"],
            full_stock=int(r["full_stock"]),
            # NAO coagir para False: a fato distingue "nao vendavel" de
            # "a API nao disse".
            is_saleable=r["is_saleable"],
        ))
    return agrupado


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def get_estoque_full_block(
    db: Session,
    *,
    hoje: date,
    brands: Optional[Sequence[str]] = None,
    accounts: Optional[Sequence[str]] = None,
    classificacoes: Optional[Sequence[str]] = None,
    busca: Optional[str] = None,
    somente_acao: bool = False,
    limite: int = MAX_PRODUTOS,
):
    """Fotografia mais recente do Estoque Full, com indicadores e limitacoes.

    Levanta `FatoInexistente` ou `SemFotografiaPublicada` -- o router as
    traduz para uma resposta `unavailable`, NUNCA para zeros.
    """
    from app.schemas.shopee_fbs_stock import (  # import local: evita ciclo
        EstoqueFullResponse, Frescor, Indicadores, LimitesProvisorios,
        ProdutoEstoque)

    marcas = _validar_lista(brands, EXPECTED_BRANDS, "brands")
    contas_filtro = _validar_lista(accounts, EXPECTED_ACCOUNTS, "accounts")
    classes = _validar_lista(classificacoes, CLASSIFICACOES, "classificacoes")
    termo = escapar_like(busca.strip()) if busca and busca.strip() else None
    limite = max(1, min(int(limite), MAX_PRODUTOS))

    # Isolamento coerente ANTES de qualquer leitura: os indicadores e a tabela
    # tem de descrever a MESMA fotografia. Sem isso, uma carga concorrente faria
    # os cartoes somarem um dia e a tabela listar outro.
    #
    # PRIMEIRO comando da transacao: `SET TRANSACTION` so' e' aceito antes de
    # qualquer query. O rollback fecha transacao implicita que tenha sobrado e
    # garante que este seja o inicio -- e' seguro porque este caminho nunca
    # escreve.
    db.rollback()
    db.execute(SQL_SNAPSHOT_COERENTE)

    if not fato_disponivel(db):
        raise FatoInexistente()

    ref_date = db.execute(SQL_ULTIMA_FOTOGRAFIA).scalar()
    if ref_date is None:
        raise SemFotografiaPublicada()

    acao = list(CLASSIFICACOES_EXIGEM_ACAO)
    escopo = {"ref_date": ref_date, "brands": marcas,
              "accounts": contas_filtro}
    filtros = {
        **escopo,
        "classificacoes": classes,
        "somente_acao": bool(somente_acao),
        "q": termo,
        "acao": acao,
    }

    indicadores = dict(db.execute(
        SQL_INDICADORES, {**escopo, "acao": acao}).mappings().one())

    total_no_filtro = int(
        db.execute(SQL_TOTAL_NO_FILTRO, filtros).scalar() or 0)
    linhas = [dict(r) for r in db.execute(
        SQL_PRODUTOS, {**filtros, "limite": limite}).mappings().all()]

    desconhecidas = sorted({r["classificacao_torre"] for r in linhas}
                           - set(CLASSIFICACOES))
    if desconhecidas:
        raise EstoqueFullContractError(
            "classificacao_torre fora do dominio: " + ", ".join(desconhecidas))

    por_cd = _carregar_por_cd(db, ref_date, linhas)
    frescor = db.execute(SQL_FRESCOR, {"ref_date": ref_date}).mappings().one()
    contas_cobertas = [
        r["shop_account"] for r in
        db.execute(SQL_CONTAS_OBSERVADAS, escopo).mappings().all()]

    limitacoes = [
        LIMITACAO_COBERTURA, LIMITACAO_CARGA_MANUAL, LIMITACAO_COBERTURA_TORRE,
        LIMITACAO_LIMIARES, LIMITACAO_DEMANDA, LIMITACAO_KITS,
        LIMITACAO_CONTEXTO,
    ]
    truncado = len(linhas) < total_no_filtro
    if truncado:
        limitacoes.append(
            f"Listagem TRUNCADA: {len(linhas)} de {total_no_filtro} produtos. "
            "Refine o filtro para ver o restante.")

    produtos = [
        ProdutoEstoque(
            shop_account=r["shop_account"],
            brand=r["brand"],
            item_id=r["item_id"],
            model_id=r["model_id"],
            item_sku=r["item_sku"],
            item_name=r["item_name"],
            item_status=r["item_status"],
            is_kit=bool(r["is_kit"]),
            full_stock_saleable=int(r["full_stock_saleable"]),
            full_stock_total=int(r["full_stock_total"]),
            location_count=int(r["location_count"]),
            por_cd=por_cd.get(
                (r["shop_account"], r["item_id"], r["model_id"]), []),
            units_sold_28d=int(r["units_sold_28d"]),
            days_with_sales_28d=int(r["days_with_sales_28d"]),
            avg_daily_units_28d=Decimal(str(r["avg_daily_units_28d"])),
            cobertura_torre_dias=(
                None if r["cobertura_torre_dias"] is None
                else Decimal(str(r["cobertura_torre_dias"]))),
            classificacao_torre=r["classificacao_torre"],
            vinculo_vendas=r["vinculo_vendas"],
            reserved_stock=_opt_int(r["reserved_stock"]),
            seller_stock_total=_opt_int(r["seller_stock_total"]),
            summary_available_stock=_opt_int(r["summary_available_stock"]),
            units_sold_28d_legado_com_unpaid=_opt_int(
                r["units_sold_28d_legado_com_unpaid"]),
            ref_date=r["ref_date"],
        )
        for r in linhas
    ]

    return EstoqueFullResponse(
        scope_label=SCOPE_LABEL,
        indicadores=Indicadores(**{k: int(v) for k, v in indicadores.items()}),
        produtos=produtos,
        total_no_filtro=total_no_filtro,
        truncado=truncado,
        frescor=Frescor(
            ref_date=ref_date,
            source_captured_at=frescor["source_captured_at"],
            ingested_at=frescor["ingested_at"],
            dias_desde_a_fotografia=_dias_desde(ref_date, hoje),
            load_mode=LOAD_MODE,
            no_automation=NO_AUTOMATION,
        ),
        limites=LimitesProvisorios(
            cobertura_baixa_dias=COBERTURA_BAIXA_DIAS,
            cobertura_excesso_dias=COBERTURA_EXCESSO_DIAS,
            provisorio=True,
            observacao=LIMITACAO_LIMIARES,
        ),
        contas_cobertas=contas_cobertas,
        marcas_nao_cobertas=list(BRANDS_NOT_COVERED),
        limitacoes=limitacoes,
    )
