"""Estoque Full do Mercado Livre — ML-FULL-STOCK-API-1. ESTRITAMENTE READ-ONLY.

Le SOMENTE as tres fatos da migration 029:
    marts.fact_ml_full_stock_inventory_daily   (grao: dia, marca, conta, inventario)
    marts.fact_ml_full_stock_status_daily      (nao disponivel por status cru)
    marts.fact_ml_full_stock_brand_daily       (agregado por marca/dia)
Nenhuma consulta ao Data Mart, a `raw` ou a' API do ML acontece no request.

NAO E' `/performance/ml-fulfillment`
-------------------------------------
Aquela superficie classifica o PEDIDO pela modalidade de envio. Esta mede o
ESTOQUE fisico no CD do ML. Nao compartilham fonte, flag nem rotulo.

ESTA CAMADA NAO CLASSIFICA
---------------------------
`classificacao_torre`, `cobertura_torre_dias` e `media_diaria_28d` sao LIDOS
da fato (calculados em `pipelines/transforms/ml_full_stock.py`). Os totais por
marca vem da fato de MARCA, que o publicador reconciliou antes do COMMIT. Esta
camada so' conta, filtra, ordena -- e CONFERE: o disponivel Full somado por
inventario tem de bater com o da fato de marca; divergencia e' contrato
quebrado (503), nunca um numero escolhido em silencio.

GRAO: INVENTARIO, NUNCA ANUNCIO
--------------------------------
Estoque aparece uma vez por inventario. `anuncios*` sao contagens de
associacao e nunca multiplicam nem somam estoque. Nem a soma de `anuncios`
entre inventarios e' exposta: um anuncio com variacoes liga-se a mais de um
inventario e seria contado mais de uma vez.

AUSENCIA NUNCA E' ZERO
-----------------------
    feature_flag_desligada    -> decisao nossa, tratada no router
    fato_inexistente          -> migration 029 nao aplicada (`to_regclass`)
    sem_fotografia_publicada  -> tabelas existem, publicador nunca gravou
    sem_fotografia_na_data    -> data pedida sem nenhuma marca publicada
Dentro de uma resposta `ok`, inventario SEM_LEITURA tem quantidades `None`,
marca sem fotografia no dia tem todos os numeros `None` e o total diz que e'
PARCIAL. Nada disso vira 0.

SEGURANCA
---------
Allowlist explicita de colunas; nunca `SELECT *`. Filtros sempre como
parametros nomeados. `seller_id` e' lido so' para ordenar, e nao sai no
payload.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

INVENTORY_TABLE = "marts.fact_ml_full_stock_inventory_daily"
STATUS_TABLE = "marts.fact_ml_full_stock_status_daily"
BRAND_TABLE = "marts.fact_ml_full_stock_brand_daily"

#: ALLOWLIST, espelhando o CHECK da 029. Sem filtro = exatamente estas.
EXPECTED_BRANDS = ("barbours", "kokeshi", "lescent", "rituaria")

SCOPE_LABEL = "Estoque Full Mercado Livre — por inventário"

#: Dominio FECHADO, espelhando o CHECK da 029.
CLASSIFICACOES = (
    "RUPTURA_CANDIDATA",
    "BAIXO_CANDIDATO",
    "EXCESSO_CANDIDATO",
    "SEM_GIRO_CANDIDATO",
    "SUFICIENTE",
    "SEM_DEMANDA_MEDIDA",
    "SEM_LEITURA",
    "FORA_DO_FULL",
)
CLASSIFICACOES_EXIGEM_ACAO = ("RUPTURA_CANDIDATA", "BAIXO_CANDIDATO")

#: 🔴 PROVISORIOS, herdados da Shopee FBS. Rotulo de exibicao, nao regra.
COBERTURA_BAIXA_DIAS = 7
COBERTURA_EXCESSO_DIAS = 90
JANELA_VENDAS_DIAS = 28

MAX_INVENTARIOS = 2000

# ---------------------------------------------------------------------------
# TEXTO QUE VAI PARA A TELA (acentuado: o gestor le como esta)
# ---------------------------------------------------------------------------

MOTIVO_FATO_INEXISTENTE = (
    "O estoque Full do Mercado Livre ainda não existe neste ambiente: a "
    "migration 029 não foi aplicada. Não há estoque medido — isto não "
    "significa estoque zero."
)
MOTIVO_SEM_FOTOGRAFIA = (
    "As tabelas de estoque Full do Mercado Livre existem, mas nenhuma "
    "fotografia foi publicada ainda (`publish_ml_full_stock_daily` nunca "
    "gravou). Não há estoque medido — isto não significa estoque zero."
)
MOTIVO_SEM_FOTOGRAFIA_NA_DATA = (
    "Nenhuma marca tem fotografia de estoque Full do Mercado Livre publicada "
    "na data pedida. Não há estoque medido nesse dia — isto não significa "
    "estoque zero."
)

LIMITACAO_GRAO = (
    "Estoque contado UMA vez por inventário. Um inventário pode servir a "
    "vários anúncios; os anúncios aparecem só como contagem de associação e "
    "nunca somam estoque."
)
LIMITACAO_AUSENCIA = (
    "“Sem leitura” e marca sem fotografia aparecem como ausência (—), nunca "
    "como zero. Quando há ausência no escopo, o total é declarado PARCIAL."
)
LIMITACAO_FULL = (
    "Só entra na operação o inventário com ao menos um anúncio Full "
    "(fulfillment). Os demais aparecem como “Fora do Full”, contexto."
)
LIMITACAO_TRANSFER = (
    "“Transferência” é movimento INTERNO do Mercado Livre. Não é remessa do "
    "operador para o Full."
)
LIMITACAO_COBERTURA_TORRE = (
    "“Cobertura da Torre” é cálculo NOSSO: disponível dividido pela média "
    f"diária de {JANELA_VENDAS_DIAS} dias de vendas Full pagas. NÃO reproduz "
    "nenhuma fórmula do Mercado Livre."
)
LIMITACAO_LIMIARES = (
    f"Limiares PROVISÓRIOS, não ratificados para o Mercado Livre: abaixo de "
    f"{COBERTURA_BAIXA_DIAS} dias é “baixo”, a partir de "
    f"{COBERTURA_EXCESSO_DIAS} dias é “excesso”."
)
LIMITACAO_ALOCACAO = (
    "Venda sem vínculo com exatamente um inventário NÃO é rateada: fica fora "
    "da velocidade do inventário e aparece na cobertura da alocação da marca."
)
LIMITACAO_SEM_AGENDAMENTO = (
    "A publicação ainda não tem agendamento: a data mostrada é a da última "
    "execução autorizada do publicador."
)


class MLFullEstoqueIndisponivel(Exception):
    motivo_tecnico = "fato_inexistente"
    mensagem = MOTIVO_FATO_INEXISTENTE


class FatoInexistente(MLFullEstoqueIndisponivel):
    motivo_tecnico = "fato_inexistente"
    mensagem = MOTIVO_FATO_INEXISTENTE


class SemFotografiaPublicada(MLFullEstoqueIndisponivel):
    motivo_tecnico = "sem_fotografia_publicada"
    mensagem = MOTIVO_SEM_FOTOGRAFIA


class SemFotografiaNaData(MLFullEstoqueIndisponivel):
    motivo_tecnico = "sem_fotografia_na_data"
    mensagem = MOTIVO_SEM_FOTOGRAFIA_NA_DATA


class MLFullEstoqueContractError(Exception):
    """A fonte devolveu algo fora do contrato (dominio ou reconciliacao)."""


class FiltroInvalido(ValueError):
    """Filtro fora da allowlist. `mensagem_segura` so' usa constantes do
    servidor e nunca ecoa o valor recebido."""

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

#: `to_regclass` devolve NULL em vez de levantar: consultar a tabela direto
#: abortaria a transacao e transformaria "029 nao aplicada" em 500.
SQL_FATOS_EXISTEM = text("""
    SELECT to_regclass(:inventario) IS NOT NULL AS tem_inventario,
           to_regclass(:status)     IS NOT NULL AS tem_status,
           to_regclass(:marca)      IS NOT NULL AS tem_marca
""")

SQL_ULTIMA_FOTOGRAFIA = text(f"""
    SELECT MAX(ref_date) AS ref_date
      FROM {BRAND_TABLE}
     WHERE brand = ANY(CAST(:brands AS text[]))
""")

SQL_MARCAS_DO_DIA = text(f"""
    SELECT brand, inventarios, inventarios_full_lidos,
           inventarios_compartilhados, disponivel_full, nao_disponivel_full,
           transfer_full, unidades_28d_alocadas,
           unidades_28d_item_varios_inventarios,
           unidades_28d_sem_inventory_id, unidades_28d_fora_da_ponte,
           cobertura_da_alocacao, published_at
      FROM {BRAND_TABLE}
     WHERE ref_date = :ref_date
       AND brand = ANY(CAST(:brands AS text[]))
     ORDER BY brand
""")

#: Ultima fotografia de CADA marca ate' a data exibida: a marca ausente no
#: dia mostra desde quando esta' sem fotografia.
SQL_ULTIMA_POR_MARCA = text(f"""
    SELECT DISTINCT ON (brand) brand, ref_date, published_at
      FROM {BRAND_TABLE}
     WHERE brand = ANY(CAST(:brands AS text[]))
       AND ref_date <= :ref_date
     ORDER BY brand, ref_date DESC
""")

#: Contagens do ESCOPO inteiro (nao seguem o limite da listagem). Agrupado
#: para devolver poucas linhas; o somatorio por marca e' feito em Python.
SQL_CONTAGENS = text(f"""
    SELECT brand, classificacao_torre, eh_full,
           (read_status = 'OK') AS lido,
           (anuncios >= 2)      AS compartilhado,
           COUNT(*)             AS inventarios,
           SUM(disponivel)      AS disponivel,
           SUM(nao_disponivel)  AS nao_disponivel,
           SUM(transfer)        AS transfer
      FROM {INVENTORY_TABLE}
     WHERE ref_date = :ref_date
       AND brand = ANY(CAST(:brands AS text[]))
     GROUP BY brand, classificacao_torre, eh_full,
              (read_status = 'OK'), (anuncios >= 2)
""")

#: Nao disponivel por status, so' de inventario Full LIDO. O JOIN e' pela
#: chave completa do inventario: cada inventario entra uma vez por status.
SQL_STATUS = text(f"""
    SELECT s.brand, s.status_raw,
           SUM(s.quantidade) AS quantidade,
           COUNT(*)          AS inventarios
      FROM {STATUS_TABLE} s
      JOIN {INVENTORY_TABLE} i
        ON i.ref_date = s.ref_date AND i.brand = s.brand
       AND i.seller_id = s.seller_id AND i.inventory_id = s.inventory_id
     WHERE s.ref_date = :ref_date
       AND s.brand = ANY(CAST(:brands AS text[]))
       AND i.eh_full
       AND i.read_status = 'OK'
     GROUP BY s.brand, s.status_raw
     ORDER BY s.brand, quantidade DESC, s.status_raw
""")

_FILTRO_LISTAGEM = """
     WHERE ref_date = :ref_date
       AND brand = ANY(CAST(:brands AS text[]))
       AND (:incluir_fora_do_full OR classificacao_torre <> 'FORA_DO_FULL')
"""

SQL_TOTAL_NO_FILTRO = text(f"""
    SELECT COUNT(*) AS total FROM {INVENTORY_TABLE}
    {_FILTRO_LISTAGEM}
""")

#: Ordem OPERACIONAL: ruptura, baixo, e logo depois SEM_LEITURA -- "nao
#: sabemos" precisa ser visto antes do que esta' sobrando. Desempate pela
#: chave completa para ordem estavel.
SQL_INVENTARIOS = text(f"""
    SELECT ref_date, brand, inventory_id, read_status, eh_full,
           disponivel, nao_disponivel, transfer,
           total_coerente, detalhe_coerente,
           anuncios, anuncios_ativos, anuncios_full,
           unidades_28d, media_diaria_28d, cobertura_torre_dias,
           classificacao_torre, vinculo_vendas
      FROM {INVENTORY_TABLE}
    {_FILTRO_LISTAGEM}
     ORDER BY
        CASE classificacao_torre
            WHEN 'RUPTURA_CANDIDATA'  THEN 0
            WHEN 'BAIXO_CANDIDATO'    THEN 1
            WHEN 'SEM_LEITURA'        THEN 2
            WHEN 'EXCESSO_CANDIDATO'  THEN 3
            WHEN 'SEM_GIRO_CANDIDATO' THEN 4
            WHEN 'SUFICIENTE'         THEN 5
            WHEN 'SEM_DEMANDA_MEDIDA' THEN 6
            ELSE 7
        END,
        cobertura_torre_dias ASC NULLS LAST,
        disponivel DESC NULLS LAST,
        brand, seller_id, inventory_id
     LIMIT :limite
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def validar_marcas(valores: Optional[Sequence[str]]) -> list[str]:
    """Fail-closed: marca fora da allowlist e' 422, nao filtro vazio.

    Sem filtro devolve a allowlist inteira (nunca "tudo o que estiver na
    tabela"). Casa sem caixa e devolve a forma canonica, deduplicada na ordem
    pedida.
    """
    limpos = [v.strip() for v in (valores or []) if v and v.strip()]
    if not limpos:
        return list(EXPECTED_BRANDS)
    canonico = {p.lower(): p for p in EXPECTED_BRANDS}
    desconhecidos = sorted({v for v in limpos if v.lower() not in canonico})
    if desconhecidos:
        raise FiltroInvalido("brands", EXPECTED_BRANDS, desconhecidos)
    return list(dict.fromkeys(canonico[v.lower()] for v in limpos))


def _opt_int(valor) -> Optional[int]:
    """`None` continua `None`. Nao vira 0."""
    return None if valor is None else int(valor)


def _opt_dec(valor) -> Optional[Decimal]:
    return None if valor is None else Decimal(str(valor))


def _soma_opcional(valores) -> Optional[int]:
    """Soma so' o que foi medido; `None` se nada foi medido."""
    medidos = [int(v) for v in valores if v is not None]
    return sum(medidos) if medidos else None


def fatos_disponiveis(db: Session) -> bool:
    """As TRES tabelas precisam existir: meia migration nao e' meia tela."""
    linha = db.execute(SQL_FATOS_EXISTEM, {
        "inventario": INVENTORY_TABLE, "status": STATUS_TABLE,
        "marca": BRAND_TABLE}).mappings().one()
    return all(bool(linha[k]) for k in
               ("tem_inventario", "tem_status", "tem_marca"))


def _horas_desde(publicado: Optional[datetime],
                 agora: datetime) -> Optional[float]:
    if publicado is None:
        return None
    if publicado.tzinfo is None:
        publicado = publicado.replace(tzinfo=timezone.utc)
    return round((agora - publicado).total_seconds() / 3600, 1)


def _agregar_contagens(linhas: list[dict]) -> dict[str, dict]:
    """Contagens por marca a partir das linhas agrupadas de SQL_CONTAGENS."""
    por_marca: dict[str, dict] = defaultdict(lambda: {
        "inventarios": 0, "full": 0, "full_lidos": 0, "fora_do_full": 0,
        "compartilhados": 0, "lidos": 0, "sem_leitura": 0,
        "full_sem_leitura": 0, "por_classificacao": defaultdict(int),
        "disp_full": [], "nao_disp_full": [], "transfer_full": [],
    })
    for r in linhas:
        m = por_marca[r["brand"]]
        n = int(r["inventarios"])
        m["inventarios"] += n
        m["por_classificacao"][r["classificacao_torre"]] += n
        if r["compartilhado"]:
            m["compartilhados"] += n
        if r["lido"]:
            m["lidos"] += n
        else:
            m["sem_leitura"] += n
        if r["classificacao_torre"] == "FORA_DO_FULL":
            m["fora_do_full"] += n
        if r["eh_full"]:
            m["full"] += n
            if r["lido"]:
                m["full_lidos"] += n
                m["disp_full"].append(r["disponivel"])
                m["nao_disp_full"].append(r["nao_disponivel"])
                m["transfer_full"].append(r["transfer"])
            else:
                m["full_sem_leitura"] += n
    return por_marca


def _reconciliar(marca: str, fato: dict, contagem: Optional[dict]) -> None:
    """A fato de marca e a de inventario descrevem o MESMO dia. Se divergem,
    nenhuma das duas e' escolhida em silencio."""
    if contagem is None:
        raise MLFullEstoqueContractError(
            f"marca {marca}: agregado sem inventarios no dia")
    pares = (
        ("inventarios", int(fato["inventarios"]), contagem["inventarios"]),
        ("inventarios_full_lidos", int(fato["inventarios_full_lidos"]),
         contagem["full_lidos"]),
        ("disponivel_full", _opt_int(fato["disponivel_full"]),
         _soma_opcional(contagem["disp_full"])),
    )
    for campo, no_agregado, nos_inventarios in pares:
        if no_agregado != nos_inventarios:
            raise MLFullEstoqueContractError(
                f"marca {marca}: {campo} diverge entre agregado e inventarios")


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

def get_ml_full_estoque_block(
    db: Session,
    *,
    hoje: date,
    agora: Optional[datetime] = None,
    brands: Optional[Sequence[str]] = None,
    ref_date: Optional[date] = None,
    incluir_fora_do_full: bool = False,
    limite: int = MAX_INVENTARIOS,
):
    """Fotografia (a ultima, ou a da data pedida) do estoque Full do ML.

    Levanta `MLFullEstoqueIndisponivel` (o router traduz para `unavailable`),
    `FiltroInvalido` (422) ou `MLFullEstoqueContractError` (503).
    """
    from app.schemas.ml_full_stock import (  # import local: evita ciclo
        Cobertura, Frescor, Indicadores, InventarioEstoque, Leitura,
        LimitesProvisorios, MarcaEstoque, MLFullEstoqueResponse,
        StatusNaoDisponivel)

    agora = agora or datetime.now(timezone.utc)
    marcas = validar_marcas(brands)
    limite = max(1, min(int(limite), MAX_INVENTARIOS))

    # PRIMEIRO comando da transacao: snapshot coerente e READ ONLY. O rollback
    # fecha transacao implicita que tenha sobrado; seguro porque nada escreve.
    db.rollback()
    db.execute(SQL_SNAPSHOT_COERENTE)

    if not fatos_disponiveis(db):
        raise FatoInexistente()

    ultima = db.execute(SQL_ULTIMA_FOTOGRAFIA, {"brands": marcas}).scalar()
    if ultima is None:
        raise SemFotografiaPublicada()
    dia = ref_date or ultima

    p = {"ref_date": dia, "brands": marcas}
    fatos_marca = {r["brand"]: dict(r) for r in
                   db.execute(SQL_MARCAS_DO_DIA, p).mappings().all()}
    if not fatos_marca:
        raise SemFotografiaNaData()

    ultima_por_marca = {r["brand"]: dict(r) for r in
                        db.execute(SQL_ULTIMA_POR_MARCA, p).mappings().all()}
    linhas_contagem = [dict(r) for r in
                       db.execute(SQL_CONTAGENS, p).mappings().all()]

    desconhecidas = sorted({r["classificacao_torre"] for r in linhas_contagem}
                           - set(CLASSIFICACOES))
    if desconhecidas:
        raise MLFullEstoqueContractError(
            "classificacao_torre fora do dominio: " + ", ".join(desconhecidas))

    contagens = _agregar_contagens(linhas_contagem)
    orfas = sorted(set(contagens) - set(fatos_marca))
    if orfas:
        raise MLFullEstoqueContractError(
            "inventarios sem agregado de marca no dia: " + ", ".join(orfas))
    for marca, fato in fatos_marca.items():
        _reconciliar(marca, fato, contagens.get(marca))

    status_por_marca: dict[str, list] = defaultdict(list)
    for r in db.execute(SQL_STATUS, p).mappings().all():
        status_por_marca[r["brand"]].append(StatusNaoDisponivel(
            status_raw=r["status_raw"], quantidade=int(r["quantidade"]),
            inventarios=int(r["inventarios"])))

    filtro = {**p, "incluir_fora_do_full": bool(incluir_fora_do_full)}
    total_no_filtro = int(db.execute(SQL_TOTAL_NO_FILTRO, filtro).scalar() or 0)
    linhas = [dict(r) for r in db.execute(
        SQL_INVENTARIOS, {**filtro, "limite": limite}).mappings().all()]

    # ---- marcas: com e sem fotografia, na ordem da allowlist pedida --------
    blocos_marca = []
    for marca in marcas:
        ult = ultima_por_marca.get(marca)
        fato = fatos_marca.get(marca)
        if fato is None:
            blocos_marca.append(MarcaEstoque(
                brand=marca, tem_fotografia_no_dia=False,
                ultima_fotografia=ult["ref_date"] if ult else None,
                published_at=ult["published_at"] if ult else None))
            continue
        c = contagens[marca]
        blocos_marca.append(MarcaEstoque(
            brand=marca, tem_fotografia_no_dia=True,
            ultima_fotografia=dia, published_at=fato["published_at"],
            inventarios=int(fato["inventarios"]),
            inventarios_full_lidos=int(fato["inventarios_full_lidos"]),
            inventarios_compartilhados=int(fato["inventarios_compartilhados"]),
            leitura=Leitura(inventarios=c["inventarios"],
                            inventarios_lidos=c["lidos"],
                            inventarios_sem_leitura=c["sem_leitura"]),
            disponivel_full=_opt_int(fato["disponivel_full"]),
            nao_disponivel_full=_opt_int(fato["nao_disponivel_full"]),
            transfer_full=_opt_int(fato["transfer_full"]),
            unidades_28d_alocadas=int(fato["unidades_28d_alocadas"]),
            unidades_28d_item_varios_inventarios=int(
                fato["unidades_28d_item_varios_inventarios"]),
            unidades_28d_sem_inventory_id=int(
                fato["unidades_28d_sem_inventory_id"]),
            unidades_28d_fora_da_ponte=int(fato["unidades_28d_fora_da_ponte"]),
            cobertura_da_alocacao=_opt_dec(fato["cobertura_da_alocacao"]),
            por_classificacao=dict(sorted(c["por_classificacao"].items())),
            nao_disponivel_por_status=status_por_marca.get(marca, []),
        ))

    com_foto = [m for m in marcas if m in fatos_marca]
    sem_foto = [m for m in marcas if m not in fatos_marca]

    # ---- indicadores do escopo ---------------------------------------------
    def total(campo: str) -> int:
        return sum(c[campo] for c in contagens.values())

    def por_classe(classe: str) -> int:
        return sum(c["por_classificacao"].get(classe, 0)
                   for c in contagens.values())

    motivos_parcial = []
    if sem_foto:
        motivos_parcial.append(
            "Sem fotografia no dia: " + ", ".join(sem_foto) + ".")
    sem_full_lido = [m for m in com_foto
                     if fatos_marca[m]["disponivel_full"] is None]
    if sem_full_lido:
        motivos_parcial.append(
            "Sem inventário Full lido: " + ", ".join(sem_full_lido) + ".")
    full_sem_leitura = total("full_sem_leitura")
    if full_sem_leitura:
        motivos_parcial.append(
            f"{full_sem_leitura} inventário(s) Full sem leitura.")

    leitura_total = Leitura(inventarios=total("inventarios"),
                            inventarios_lidos=total("lidos"),
                            inventarios_sem_leitura=total("sem_leitura"))
    indicadores = Indicadores(
        disponivel_full=_soma_opcional(
            fatos_marca[m]["disponivel_full"] for m in com_foto),
        nao_disponivel_full=_soma_opcional(
            fatos_marca[m]["nao_disponivel_full"] for m in com_foto),
        transfer_full=_soma_opcional(
            fatos_marca[m]["transfer_full"] for m in com_foto),
        total_parcial=bool(motivos_parcial),
        motivos_total_parcial=motivos_parcial,
        inventarios=leitura_total.inventarios,
        inventarios_full=total("full"),
        inventarios_full_lidos=total("full_lidos"),
        inventarios_fora_do_full=total("fora_do_full"),
        inventarios_compartilhados=total("compartilhados"),
        inventarios_lidos=leitura_total.inventarios_lidos,
        inventarios_sem_leitura=leitura_total.inventarios_sem_leitura,
        inventarios_ruptura=por_classe("RUPTURA_CANDIDATA"),
        inventarios_baixo=por_classe("BAIXO_CANDIDATO"),
        inventarios_excesso=por_classe("EXCESSO_CANDIDATO"),
        inventarios_sem_giro=por_classe("SEM_GIRO_CANDIDATO"),
        inventarios_suficientes=por_classe("SUFICIENTE"),
        inventarios_sem_demanda_medida=por_classe("SEM_DEMANDA_MEDIDA"),
        inventarios_exigem_acao=sum(
            por_classe(k) for k in CLASSIFICACOES_EXIGEM_ACAO),
    )

    inventarios = [
        InventarioEstoque(
            brand=r["brand"],
            inventory_id=r["inventory_id"],
            read_status=r["read_status"],
            eh_full=bool(r["eh_full"]),
            disponivel=_opt_int(r["disponivel"]),
            nao_disponivel=_opt_int(r["nao_disponivel"]),
            transfer=_opt_int(r["transfer"]),
            total_coerente=r["total_coerente"],
            detalhe_coerente=r["detalhe_coerente"],
            anuncios=int(r["anuncios"]),
            anuncios_ativos=int(r["anuncios_ativos"]),
            anuncios_full=int(r["anuncios_full"]),
            compartilhado=int(r["anuncios"]) >= 2,
            unidades_28d=int(r["unidades_28d"]),
            media_diaria_28d=Decimal(str(r["media_diaria_28d"])),
            cobertura_torre_dias=_opt_dec(r["cobertura_torre_dias"]),
            classificacao_torre=r["classificacao_torre"],
            vinculo_vendas=r["vinculo_vendas"],
            ref_date=r["ref_date"],
        )
        for r in linhas
    ]

    publicados = [fatos_marca[m]["published_at"] for m in com_foto
                  if fatos_marca[m]["published_at"] is not None]
    published_at = max(publicados) if publicados else None

    limitacoes = [
        LIMITACAO_GRAO, LIMITACAO_AUSENCIA, LIMITACAO_FULL,
        LIMITACAO_COBERTURA_TORRE, LIMITACAO_LIMIARES, LIMITACAO_ALOCACAO,
        LIMITACAO_TRANSFER, LIMITACAO_SEM_AGENDAMENTO,
    ]
    truncado = len(inventarios) < total_no_filtro
    if truncado:
        limitacoes.append(
            f"Listagem TRUNCADA: {len(inventarios)} de {total_no_filtro} "
            "inventários. Os indicadores cobrem o escopo inteiro.")

    return MLFullEstoqueResponse(
        scope_label=SCOPE_LABEL,
        frescor=Frescor(
            ref_date=dia,
            ultima_fotografia_disponivel=ultima,
            published_at=published_at,
            dias_desde_a_fotografia=(hoje - dia).days,
            horas_desde_a_publicacao=_horas_desde(published_at, agora),
            publicacao_agendada=False,
        ),
        cobertura=Cobertura(
            marcas_esperadas=marcas,
            marcas_com_fotografia=com_foto,
            marcas_sem_fotografia=sem_foto,
            leitura=leitura_total,
        ),
        indicadores=indicadores,
        marcas=blocos_marca,
        inventarios=inventarios,
        total_no_filtro=total_no_filtro,
        truncado=truncado,
        incluiu_fora_do_full=bool(incluir_fora_do_full),
        limites=LimitesProvisorios(
            cobertura_baixa_dias=COBERTURA_BAIXA_DIAS,
            cobertura_excesso_dias=COBERTURA_EXCESSO_DIAS,
            provisorio=True,
            observacao=LIMITACAO_LIMIARES,
        ),
        limitacoes=limitacoes,
    )
