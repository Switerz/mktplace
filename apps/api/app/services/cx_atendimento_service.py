"""CX-MKT-1 — aba "Atendimento" da Performance das contas.

Contrato: `docs/cx_atendimento_contrato.md` (§3, §6, §7).

LE O DATA MART DIRETO, SEM INGESTAO NOVA
----------------------------------------
Esta fatia usa so' o que o Airflow ja' grava:

  ML      `api.vw_ml_orders`          `cancel_detail` (motivo do cancelamento)
  Shopee  `silver.stg_shopee_orders`  `cancel_by`, `cancel_reason`, `order_status`

O que depende de API nova (fila com prazo, motivo da reclamacao, devolucoes do
ML/TikTok/Shopee) sai como `pendencias`, com motivo. Ausencia nunca vira zero.

DEFINICOES (todas "calculo Torre")
----------------------------------
* Pedido ML: um por `order_id` (a view pode repetir o pedido; fica a linha mais
  recente). Dia = `date_created` (horario do ML), igual ao resto da Torre.
* Reclamacoes do ML NAO sao contadas aqui. `mediations` do pedido mistura
  tipos (inclui cancelamento pedido pelo comprador) e nao aplica as exclusoes
  da plataforma: medido em 03/11/2026, Kokeshi daria 2,5% contra 0,61% na
  reputacao oficial. A taxa oficial vem do snapshot de reputacao.
* Pedido Shopee: um por linha da Silver (`order_sn`), dia = `created_date_brt`.
  "Pedido nao pago" cancelado pela Shopee nao e' venda nem atendimento: sai do
  denominador e dos motivos, e o volume aparece como `excluidos`.
* Em devolucao (Shopee): pedidos com `order_status = 'to_return'` AGORA, entre
  os criados nos ultimos 90 dias. E' fotografia, nao fluxo da janela.

UMA FONTE CAI, AS OUTRAS SEGUEM
-------------------------------
Cada consulta e' isolada. Excecao vira estado `erro` so' do bloco afetado, com
motivo FIXO: o texto da excecao nunca sai na resposta nem no log (pode carregar
DSN ou SQL).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.schemas.account_performance import Cobertura, Indicador, MarcaFora
from app.schemas.cx_atendimento import (
    AtendimentoResponse,
    ChatShopee,
    CxShopeeResponse,
    DevolucoesShopee,
    LinhaDevolucao,
    NaoLidas,
    BlocoMotivos,
    CanalAtendimento,
    Janela,
    LinhaMarca,
    Motivo,
    Pendencia,
)
from app.services.gold_service import BRAND_LABELS, BRANDS_IN_SCOPE, ML_BRANDS, TZ_OPERACIONAL

logger = logging.getLogger(__name__)

Consultar = Callable[[str, dict], list[dict]]

#: Allowlist do filtro `brands`: o escopo de marcas da Torre.
MARCAS_PERMITIDAS: tuple[str, ...] = tuple(BRANDS_IN_SCOPE)

#: Lojas Shopee com API (a Kokeshi so' tem export manual).
SHOPEE_MARCAS: tuple[str, ...] = ("apice", "barbours", "lescent", "rituaria")
#: Lojas Shopee sem app: na aba CX entram pelo que o bot do Seller Centre grava
#: no Neon (CX-KOKESHI-BOT, 05/11/2026): pedidos e cancelamentos do export de
#: pedidos (034), chat e devolucoes da 036.
SHOPEE_EXPORT: tuple[str, ...] = ("kokeshi",)
FONTE_SHOPEE_EXPORT = "Neon · export do Seller Centre (bot da Kokeshi)"

JANELA_PADRAO_DIAS = 30
DIAS_FOTOGRAFIA_DEVOLUCAO = 90

#: Publicacao mais velha que isto ganha aviso. O `ml_orders_etl` roda a cada
#: 15 min e o `shopee_orders_etl` a cada 6 h.
LIMITE_FRESCOR_HORAS = {"mercado_livre": 3.0, "shopee": 12.0}

MOTIVO_ERRO = "Fonte indisponível nesta carga. Tente novamente em alguns minutos."

FONTE_ML = "Data Mart · api.vw_ml_orders (ml_orders_etl)"
FONTE_SHOPEE = "Data Mart · silver.stg_shopee_orders (shopee_orders_etl)"

# ---------------------------------------------------------------------------
# Rotulos de motivo. Codigo fora do mapa NUNCA some: sai com o texto da
# plataforma e `mapeado = False`, para o mapa ser corrigido.
# ---------------------------------------------------------------------------

#: `cancel_detail.code` do ML -> (rotulo, natureza). Medido em 03/11/2026.
MOTIVOS_ML: dict[str, tuple[str, str]] = {
    "buyer_cancel_express": ("Comprador cancelou antes do envio", "comprador"),
    "mediations": ("Cancelado por reclamação do comprador", "reclamacao"),
    "shipment_not_delivered": ("Envio não entregue", "logistica"),
    "shipment_operative_issue": ("Problema operacional no envio", "logistica"),
    "pack_splitted": ("Pacote dividido pelo ML", "plataforma"),
    "pack_creation_error": ("Erro do ML ao criar o pacote", "plataforma"),
    "fraud": ("Suspeita de fraude", "plataforma"),
    "unknown": ("Sem causa identificada pelo ML", "plataforma"),
    "fiscal_buyer_forbidden": ("Restrição fiscal do comprador", "plataforma"),
}

#: `cancel_reason` da Shopee (minusculas, sem espaco nas pontas) ->
#: (codigo, rotulo, natureza). Grafias diferentes do mesmo motivo caem no
#: mesmo codigo. Medido em 03/11/2026.
MOTIVOS_SHOPEE: dict[str, tuple[str, str, str]] = {
    "don't want to buy anymore": ("desistiu", "Desistiu da compra", "comprador"),
    "others / change of mind": ("mudou_de_ideia", "Mudou de ideia", "comprador"),
    "others": ("outros_comprador", "Outros motivos do comprador", "comprador"),
    "need to modify order": ("alterar_pedido", "Quer alterar o pedido", "comprador"),
    "modify existing order (colour, size, address, voucher, etc.)":
        ("alterar_pedido", "Quer alterar o pedido", "comprador"),
    "need to input / change voucher code": ("cupom", "Quer usar ou trocar o cupom", "comprador"),
    "found cheaper elsewhere": ("mais_barato", "Achou mais barato em outro lugar", "comprador"),
    "need to change delivery address": ("endereco", "Quer mudar o endereço de entrega", "comprador"),
    "payment procedure too troublesome": ("pagamento", "Achou o pagamento complicado", "comprador"),
    "failed delivery": ("falha_entrega", "Falha na entrega", "logistica"),
    "seller did not ship": ("vendedor_nao_enviou", "Vendedor não enviou no prazo", "vendedor"),
    "out of stock": ("sem_estoque", "Sem estoque", "vendedor"),
}
#: Motivos que comecam com estes prefixos (o texto da Shopee traz frase extra).
PREFIXOS_SHOPEE: tuple[tuple[str, tuple[str, str, str]], ...] = (
    ("parcel lost", ("extravio", "Pacote extraviado no transporte", "logistica")),
)
#: Cancelado por falta de pagamento: nao e' venda nem atendimento.
SHOPEE_NAO_PAGO = "unpaid order"

_NATUREZA_POR_AUTOR = {"buyer": "comprador", "seller": "vendedor"}


def _natureza_do_autor(autor: Any) -> str:
    return _NATUREZA_POR_AUTOR.get(str(autor or "").strip().lower(), "plataforma")


def _slug(texto: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_")
    return s[:60] or "sem_codigo"


def rotular_ml(codigo: Any, solicitante: Any) -> tuple[str, str, str, bool]:
    """(codigo, rotulo, natureza, mapeado) de um cancelamento ML."""
    cod = str(codigo or "").strip()
    if not cod:
        return "sem_motivo", "Sem motivo informado", _natureza_do_autor(solicitante), True
    if cod in MOTIVOS_ML:
        rotulo, natureza = MOTIVOS_ML[cod]
        return cod, rotulo, natureza, True
    return cod, cod, _natureza_do_autor(solicitante), False


def rotular_shopee(motivo: Any, autor: Any) -> tuple[str, str, str, bool]:
    """(codigo, rotulo, natureza, mapeado) de um cancelamento Shopee."""
    bruto = str(motivo or "").strip()
    chave = bruto.lower()
    if not chave:
        return "sem_motivo", "Sem motivo informado", _natureza_do_autor(autor), True
    if chave in MOTIVOS_SHOPEE:
        cod, rotulo, natureza = MOTIVOS_SHOPEE[chave]
        return cod, rotulo, natureza, True
    for prefixo, (cod, rotulo, natureza) in PREFIXOS_SHOPEE:
        if chave.startswith(prefixo):
            return cod, rotulo, natureza, True
    return _slug(chave), bruto[:120], _natureza_do_autor(autor), False


# ---------------------------------------------------------------------------
# Janela
# ---------------------------------------------------------------------------
def calcular_janela(dias: int, agora: Optional[datetime] = None) -> Janela:
    """`dias` fechados ate' D-1 no fuso do negocio (o dia corrente e' parcial)."""
    hoje = (agora or datetime.now(timezone.utc)).astimezone(TZ_OPERACIONAL).date()
    fim = hoje - timedelta(days=1)
    return Janela(inicio=fim - timedelta(days=dias - 1), fim=fim, dias=dias)


def _texto_janela(j: Janela) -> str:
    return f"{j.dias} dias fechados ({j.inicio.strftime('%d/%m')} a {j.fim.strftime('%d/%m')})"


# ---------------------------------------------------------------------------
# SQL (somente leitura; parametros nomeados, nada interpolado)
# ---------------------------------------------------------------------------
_CTE_ML = """
WITH o AS (
    SELECT DISTINCT ON (order_id)
           order_id, brand, status, cancel_detail, updated_at
      FROM api.vw_ml_orders
     WHERE brand = ANY(:marcas)
       AND date_created >= :inicio
       AND date_created < :fim_exclusivo
     ORDER BY order_id, updated_at DESC NULLS LAST
)
"""

SQL_ML_MARCAS = _CTE_ML + """
SELECT brand AS marca,
       COUNT(*) AS pedidos,
       COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelados,
       MAX(updated_at) AS atualizado_em
  FROM o
 GROUP BY brand
"""

SQL_ML_MOTIVOS = _CTE_ML + """
SELECT cancel_detail->>'code' AS codigo,
       cancel_detail->>'requested_by' AS solicitante,
       COUNT(*) AS quantidade
  FROM o
 WHERE status = 'cancelled'
 GROUP BY 1, 2
"""

SQL_SHOPEE_MARCAS = """
SELECT shop_account AS marca,
       COUNT(*) FILTER (WHERE NOT (order_status = 'unpaid'
                                   OR (order_status = 'cancelled'
                                       AND lower(trim(cancel_reason)) = :nao_pago))) AS pedidos,
       COUNT(*) FILTER (WHERE order_status = 'cancelled'
                          AND lower(trim(coalesce(cancel_reason, ''))) <> :nao_pago) AS cancelados,
       COUNT(*) FILTER (WHERE order_status = 'cancelled' AND cancel_by = 'buyer') AS cancelados_comprador,
       COUNT(*) FILTER (WHERE order_status = 'cancelled'
                          AND lower(trim(cancel_reason)) = :nao_pago) AS nao_pagos,
       MAX(ingested_at) AS atualizado_em
  FROM silver.stg_shopee_orders
 WHERE shop_account = ANY(:marcas)
   AND created_date_brt BETWEEN :inicio AND :fim
 GROUP BY shop_account
"""

SQL_SHOPEE_MOTIVOS = """
SELECT cancel_reason AS motivo, cancel_by AS autor, COUNT(*) AS quantidade
  FROM silver.stg_shopee_orders
 WHERE shop_account = ANY(:marcas)
   AND created_date_brt BETWEEN :inicio AND :fim
   AND order_status = 'cancelled'
 GROUP BY 1, 2
"""

SQL_SHOPEE_DEVOLUCAO = """
SELECT shop_account AS marca, COUNT(*) AS em_devolucao
  FROM silver.stg_shopee_orders
 WHERE shop_account = ANY(:marcas)
   AND order_status = 'to_return'
   AND created_date_brt >= :desde
 GROUP BY shop_account
"""


#: Pedido nao pago cancelado pela Shopee. O sinal principal e' o export nao
#: ter "Hora do pagamento do pedido" (`pay_time` NULL): pedido cancelado que
#: nunca foi pago nao depende do texto. O texto do motivo (portugues, sem
#: acento) fica como reforco para o caso de a hora vir preenchida.
_NAO_PAGO_EXPORT = ("(pay_time IS NULL OR translate(lower(coalesce(cancel_reason, '')),"
                    " 'ãáàâéêíóôõúç', 'aaaaeeiooouc')"
                    " ~ '(nao pago|nao foi pago|nao pagou|sem pagamento|pagamento nao)')")

#: Mesma regra de `SQL_SHOPEE_MARCAS`, no export de pedidos da Kokeshi (034).
#: O export nao diz quem cancelou: `cancelados_comprador` fica NULL (nunca 0).
SQL_SHOPEE_EXPORT_MARCAS = f"""
SELECT shop_account AS marca,
       COUNT(*) FILTER (WHERE NOT (order_status = 'UNPAID'
                                   OR (order_status = 'CANCELLED' AND {_NAO_PAGO_EXPORT}))) AS pedidos,
       COUNT(*) FILTER (WHERE order_status = 'CANCELLED' AND NOT {_NAO_PAGO_EXPORT}) AS cancelados,
       NULL::int AS cancelados_comprador,
       COUNT(*) FILTER (WHERE order_status = 'CANCELLED' AND {_NAO_PAGO_EXPORT}) AS nao_pagos,
       MAX(ingested_at) AS atualizado_em
  FROM marts.expedicao_shopee_export_pedido
 WHERE shop_account = ANY(:marcas)
   AND (create_time AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN :inicio AND :fim
 GROUP BY shop_account
"""

SQL_SHOPEE_EXPORT_MOTIVOS = f"""
SELECT cancel_reason AS motivo, COUNT(*) AS quantidade
  FROM marts.expedicao_shopee_export_pedido
 WHERE shop_account = ANY(:marcas)
   AND (create_time AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN :inicio AND :fim
   AND order_status = 'CANCELLED' AND NOT {_NAO_PAGO_EXPORT}
 GROUP BY 1
"""


def rotular_shopee_pt(motivo: Any) -> tuple[str, str, str, bool]:
    """Motivo de cancelamento do export (portugues, ja' legivel).

    O texto vira o proprio rotulo (mapeado: nao precisa de traducao); a
    natureza sai de palavras do texto.
    """
    bruto = str(motivo or "").strip()
    if not bruto or bruto == "-":
        return "sem_motivo", "Sem motivo informado", "plataforma", True
    n = _sem_acento(bruto)
    if "comprador" in n or "cliente" in n:
        natureza = "comprador"
    elif "vendedor" in n or "estoque" in n:
        natureza = "vendedor"
    elif any(t in n for t in ("entrega", "transport", "logistic", "extravi", "envio")):
        natureza = "logistica"
    else:
        natureza = "plataforma"
    return "pt_" + _slug(n), bruto[:120], natureza, True


def _sem_acento(texto: str) -> str:
    import unicodedata  # noqa: PLC0415

    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def consultar_torre(sql: str, params: dict) -> list[dict]:
    """Neon da Torre (contas do bot, CX-KOKESHI-BOT)."""
    from app.services import torre_leitura  # import tardio: testes injetam `consultar_torre`

    return torre_leitura.consultar(sql, params)


def consultar_datamart(sql: str, params: dict) -> list[dict]:
    """Direto no Data Mart; se o banco recusar por permissao, pelo Metabase
    (ver `datamart_leitura`, a mesma leitura da Performance oficial)."""
    from app.services import datamart_leitura  # import tardio: testes injetam `consultar`

    return datamart_leitura.consultar(sql, params)


def _rodar(consultar: Consultar, nome: str, sql: str, params: dict) -> Optional[list[dict]]:
    """None = falhou (o motivo fixo sai na resposta; o log leva so' a classe)."""
    try:
        return consultar(sql, params)
    except Exception as exc:  # noqa: BLE001 — isolamento por fonte e' o contrato
        logger.warning("cx_atendimento: consulta %s falhou (%s)", nome, type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------
def _int(v: Any) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _pct(num: Optional[int], den: Optional[int]) -> Optional[float]:
    if num is None or not den:
        return None
    return round(num / den * 100, 2)


def _utc(v: Any) -> Optional[datetime]:
    """Instante da fonte em UTC. Naive = UTC (o `updated_at` do ML e' UTC)."""
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _aviso_frescor(canal: str, instante: Optional[datetime], agora: datetime) -> Optional[str]:
    if instante is None:
        return None
    horas = (agora - instante).total_seconds() / 3600
    limite = LIMITE_FRESCOR_HORAS[canal]
    if horas > limite:
        return f"Fonte sem atualização há {horas:.0f} h (esperado até {limite:.0f} h)."
    return None


def _float_ou_none(v: Optional[int]) -> Optional[float]:
    return None if v is None else float(v)


def _rotulo_marca(chave: str) -> str:
    return BRAND_LABELS.get(chave, chave.title())


def _cobertura(selecionadas: list[str], do_canal: tuple[str, ...], motivo_fora: str) -> Cobertura:
    return Cobertura(
        marcas=[m for m in selecionadas if m in do_canal],
        fora=[MarcaFora(marca=m, motivo=motivo_fora) for m in selecionadas if m not in do_canal],
    )


def _indicador(codigo: str, rotulo: str, *, valor: Optional[float], unidade: str, janela: str,
               fonte: str, ajuda: str, ok: bool, atualizado_em: Optional[datetime] = None,
               aviso: Optional[str] = None, cobertura: Optional[str] = None,
               motivo_vazio: Optional[str] = None) -> Indicador:
    """`ok=False` = fonte falhou (erro). `valor=None` com fonte ok = vazio legitimo."""
    if not ok:
        estado, valor, motivo = "erro", None, MOTIVO_ERRO
    elif valor is None:
        estado, motivo = "vazio_legitimo", motivo_vazio or "Sem pedidos no recorte."
    else:
        estado, motivo = "dado", None
    return Indicador(
        codigo=codigo, rotulo=rotulo, tipo="torre", estado=estado, valor=valor,
        unidade=unidade, janela=janela, limiar=None, fonte=fonte,
        atualizado_em=atualizado_em, aviso_frescor=aviso, cobertura=cobertura,
        ajuda=ajuda, motivo_indisponivel=motivo,
    )


def _sem_fonte(codigo: str, rotulo: str, unidade: str, motivo: str, ajuda: str) -> Indicador:
    return Indicador(
        codigo=codigo, rotulo=rotulo, tipo="torre", estado="sem_fonte", valor=None,
        unidade=unidade, janela=None, limiar=None, fonte="—", ajuda=ajuda,
        motivo_indisponivel=motivo,
    )


def _bloco_motivos(titulo: str, ajuda: str, linhas: Optional[list[tuple]],
                   excluidos: Optional[int] = None,
                   rotulo_excluidos: Optional[str] = None,
                   texto_vazio: str = "Nenhum cancelamento no recorte.") -> BlocoMotivos:
    """`linhas` = [(codigo, rotulo, natureza, mapeado, quantidade)] ou None (erro)."""
    if linhas is None:
        return BlocoMotivos(titulo=titulo, estado="erro", ajuda=ajuda,
                            motivo_indisponivel=MOTIVO_ERRO)
    agregados: dict[str, dict] = {}
    for codigo, rotulo, natureza, mapeado, qtd in linhas:
        if not qtd:
            continue
        item = agregados.setdefault(codigo, {"codigo": codigo, "rotulo": rotulo,
                                             "natureza": natureza, "mapeado": mapeado,
                                             "quantidade": 0})
        item["quantidade"] += qtd
    total = sum(i["quantidade"] for i in agregados.values())
    extra = {"excluidos": excluidos, "rotulo_excluidos": rotulo_excluidos} if excluidos else {}
    if total == 0:
        return BlocoMotivos(titulo=titulo, estado="vazio_legitimo", ajuda=ajuda,
                            motivo_indisponivel=texto_vazio, **extra)
    itens = sorted(agregados.values(), key=lambda i: (-i["quantidade"], i["rotulo"]))
    return BlocoMotivos(
        titulo=titulo, estado="dado", total=total, ajuda=ajuda,
        itens=[Motivo(pct=round(i["quantidade"] / total * 100, 1), **i) for i in itens],
        **extra,
    )


PENDENCIAS_ML = [
    Pendencia(codigo="ml_fila_acao", titulo="Reclamações aguardando nossa resposta",
              motivo="Depende da leitura das reclamações pela API do ML (post-purchase), ainda não publicada."),
    Pendencia(codigo="ml_motivos_reclamacao", titulo="Motivo das reclamações",
              motivo="O pedido guarda só o número da reclamação; o motivo vem da API do ML, ainda não publicada."),
    Pendencia(codigo="ml_perguntas", titulo="Perguntas sem resposta",
              motivo="Depende da leitura das perguntas pela API do ML, ainda não publicada."),
]
PENDENCIAS_SHOPEE = [
    Pendencia(codigo="shopee_devolucoes", titulo="Devoluções com motivo e prazo",
              motivo="Depende da leitura de devoluções pela API da Shopee (v2.returns), ainda não publicada."),
]
PENDENCIAS_TIKTOK = [
    Pendencia(codigo="tiktok_devolucoes", titulo="Devoluções e reembolsos",
              motivo="Depende da leitura pela API Return & Refund do TikTok, ainda não publicada."),
    Pendencia(codigo="tiktok_cancelamentos", titulo="Cancelamentos com motivo",
              motivo="Os pedidos do TikTok no Data Mart não trazem o motivo do cancelamento."),
]


def _canal_ml(consultar: Consultar, marcas: list[str], janela: Janela, agora: datetime) -> CanalAtendimento:
    cobertura = _cobertura(marcas, tuple(ML_BRANDS), "Sem conta Mercado Livre integrada.")
    if not cobertura.marcas:
        return CanalAtendimento(canal="mercado_livre", cobertura=cobertura, indicadores=[])
    params = {"marcas": cobertura.marcas, "inicio": janela.inicio,
              "fim_exclusivo": janela.fim + timedelta(days=1)}
    linhas = _rodar(consultar, "ml_marcas", SQL_ML_MARCAS, params)
    motivos = _rodar(consultar, "ml_motivos", SQL_ML_MOTIVOS, params)
    jt = _texto_janela(janela)
    ok = linhas is not None

    por_marca = {str(r.get("marca")): r for r in (linhas or [])}
    marcas_linhas = []
    for m in cobertura.marcas:
        r = por_marca.get(m)
        pedidos = _int(r.get("pedidos")) if r else None
        cancelados = _int(r.get("cancelados")) if r else None
        marcas_linhas.append(LinhaMarca(
            marca=m, pedidos=pedidos, cancelamentos=cancelados,
            taxa_cancelamento_pct=_pct(cancelados, pedidos),
        ))
    pedidos = sum(x.pedidos for x in marcas_linhas if x.pedidos is not None) if ok else None
    canc = sum(x.cancelamentos for x in marcas_linhas if x.cancelamentos is not None) if ok else None
    instante = max((t for t in (_utc(r.get("atualizado_em")) for r in (linhas or [])) if t),
                   default=None)
    aviso = _aviso_frescor("mercado_livre", instante, agora)
    sem_linha = [m for m in cobertura.marcas if m not in por_marca]
    cob_txt = (f"Sem pedidos no recorte: {', '.join(_rotulo_marca(m) for m in sem_linha)}."
               if ok and sem_linha else None)
    vazio = ok and not pedidos

    # Sem "taxa de reclamacao" aqui DE PROPOSITO: a contagem por `mediations`
    # inclui cancelamento pedido pelo comprador e nao aplica as exclusoes do ML.
    # Medido em 03/11/2026: Kokeshi 2,5% aqui contra 0,61% na reputacao oficial.
    # O numero oficial vem do snapshot de reputacao (aba Performance).
    indicadores = [
        _indicador("ml_cancelamentos", "Cancelamentos",
                   valor=None if vazio else canc, unidade="contagem", janela=jt, fonte=FONTE_ML,
                   ok=ok, atualizado_em=instante, aviso=aviso, cobertura=cob_txt,
                   ajuda="Pedidos criados na janela e cancelados, de qualquer autor (comprador, ML ou logística)."),
        _indicador("ml_taxa_cancelamento", "Taxa de cancelamento",
                   valor=_pct(canc, pedidos), unidade="pct", janela=jt, fonte=FONTE_ML, ok=ok,
                   atualizado_em=instante, aviso=aviso, cobertura=cob_txt,
                   ajuda="Cancelamentos ÷ pedidos criados na janela, de qualquer autor. Não é a taxa "
                         "\"Canceladas por você\" da reputação do ML, que conta só o que o vendedor cancelou."),
    ]
    bloco = _bloco_motivos(
        "Motivos de cancelamento",
        "Motivo registrado pelo ML no pedido cancelado (cancel_detail).",
        None if motivos is None else [
            (*rotular_ml(r.get("codigo"), r.get("solicitante")), _int(r.get("quantidade")) or 0)
            for r in motivos
        ],
    )
    return CanalAtendimento(
        canal="mercado_livre", cobertura=cobertura, indicadores=indicadores, motivos=bloco,
        marcas=marcas_linhas, pendencias=PENDENCIAS_ML, atualizado_em=instante,
    )


def _canal_shopee(consultar: Consultar, marcas: list[str], janela: Janela, agora: datetime,
                  consultar_torre: Optional[Consultar] = None) -> CanalAtendimento:
    """`consultar_torre` None = so' as lojas com app (a aba antiga de Atendimento)."""
    do_canal = SHOPEE_MARCAS + (SHOPEE_EXPORT if consultar_torre else ())
    cobertura = _cobertura(marcas, do_canal, "Sem app Shopee (só export manual).")
    if not cobertura.marcas:
        return CanalAtendimento(canal="shopee", cobertura=cobertura, indicadores=[])
    api = [m for m in cobertura.marcas if m in SHOPEE_MARCAS]
    exp = [m for m in cobertura.marcas if m in SHOPEE_EXPORT]
    params = {"marcas": api, "inicio": janela.inicio, "fim": janela.fim,
              "nao_pago": SHOPEE_NAO_PAGO}
    if api:
        linhas = _rodar(consultar, "shopee_marcas", SQL_SHOPEE_MARCAS, params)
        motivos = _rodar(consultar, "shopee_motivos", SQL_SHOPEE_MOTIVOS, params)
        devol = _rodar(consultar, "shopee_devolucao", SQL_SHOPEE_DEVOLUCAO,
                       {"marcas": api,
                        "desde": janela.fim - timedelta(days=DIAS_FOTOGRAFIA_DEVOLUCAO - 1)})
    else:
        linhas, motivos, devol = [], [], []
    linhas_exp, motivos_exp = [], []
    if exp and consultar_torre:
        p_exp = {"marcas": exp, "inicio": janela.inicio, "fim": janela.fim}
        linhas_exp = _rodar(consultar_torre, "shopee_export_marcas", SQL_SHOPEE_EXPORT_MARCAS, p_exp)
        motivos_exp = _rodar(consultar_torre, "shopee_export_motivos", SQL_SHOPEE_EXPORT_MOTIVOS, p_exp)
    jt = _texto_janela(janela)
    # a Kokeshi caiu e as outras nao: as linhas dela ficam None (nunca zero)
    if linhas is not None and linhas_exp is not None:
        linhas = list(linhas) + list(linhas_exp)
    # so' a Kokeshi no filtro e o Neon caiu: erro, nunca "sem pedidos"
    ok = linhas is not None and not (exp and consultar_torre and linhas_exp is None and not api)
    ok_dev = devol is not None

    por_marca = {str(r.get("marca")): r for r in (linhas or [])}
    dev_marca = {str(r.get("marca")): _int(r.get("em_devolucao")) for r in (devol or [])}
    marcas_linhas = []
    for m in cobertura.marcas:
        r = por_marca.get(m)
        pedidos_m = _int(r.get("pedidos")) if r else None
        cancelados_m = _int(r.get("cancelados")) if r else None
        marcas_linhas.append(LinhaMarca(
            marca=m,
            pedidos=pedidos_m,
            cancelamentos=cancelados_m,
            taxa_cancelamento_pct=_pct(cancelados_m, pedidos_m),
            cancelamentos_comprador=_int(r.get("cancelados_comprador")) if r else None,
            # fotografia: sem linha para a marca = nenhum pedido em devolucao (zero medido).
            # A Kokeshi nao tem essa fotografia no export: ausente, nunca zero.
            em_devolucao=(None if m in SHOPEE_EXPORT else (dev_marca.get(m, 0) if ok_dev else None)),
        ))

    def soma(campo):
        vals = [getattr(x, campo) for x in marcas_linhas if getattr(x, campo) is not None]
        return sum(vals) if vals else None

    pedidos = soma("pedidos") if ok else None
    canc = soma("cancelamentos") if ok else None
    nao_pagos = sum(_int(r.get("nao_pagos")) or 0 for r in (linhas or [])) if ok else None
    instante = max((t for t in (_utc(r.get("atualizado_em")) for r in (linhas or [])) if t),
                   default=None)
    aviso = _aviso_frescor("shopee", instante, agora)
    vazio = ok and not pedidos

    indicadores = [
        _indicador("shopee_cancelamentos", "Cancelamentos",
                   valor=None if vazio else canc, unidade="contagem", janela=jt, fonte=FONTE_SHOPEE,
                   ok=ok, atualizado_em=instante, aviso=aviso,
                   ajuda="Pedidos criados na janela e cancelados, exceto os não pagos que a Shopee cancela sozinha."),
        _indicador("shopee_taxa_cancelamento", "Taxa de cancelamento",
                   valor=_pct(canc, pedidos), unidade="pct", janela=jt, fonte=FONTE_SHOPEE, ok=ok,
                   atualizado_em=instante, aviso=aviso,
                   ajuda="Cancelamentos ÷ pedidos criados na janela, os dois sem os pedidos não pagos."),
        _indicador("shopee_em_devolucao", "Pedidos em devolução agora",
                   valor=_float_ou_none(soma("em_devolucao")) if ok_dev else None, unidade="contagem",
                   janela=f"fotografia atual · pedidos dos últimos {DIAS_FOTOGRAFIA_DEVOLUCAO} dias",
                   fonte=FONTE_SHOPEE, ok=ok_dev,
                   ajuda="Pedidos com status 'to_return' na Shopee neste momento. Sem motivo nem prazo: "
                         "esses detalhes dependem da API de devoluções."),
    ]
    bloco = _bloco_motivos(
        "Motivos de cancelamento",
        "Motivo registrado pela Shopee no pedido cancelado (cancel_reason).",
        None if motivos is None else [
            (*rotular_shopee(r.get("motivo"), r.get("autor")), _int(r.get("quantidade")) or 0)
            for r in motivos
            if str(r.get("motivo") or "").strip().lower() != SHOPEE_NAO_PAGO
        ] + [
            (*rotular_shopee_pt(r.get("motivo")), _int(r.get("quantidade")) or 0)
            for r in (motivos_exp or [])
        ],
        excluidos=nao_pagos or None,
        rotulo_excluidos="pedidos não pagos cancelados pela Shopee (fora da contagem)",
    )
    return CanalAtendimento(
        canal="shopee", cobertura=cobertura, indicadores=indicadores, motivos=bloco,
        marcas=marcas_linhas, pendencias=PENDENCIAS_SHOPEE, atualizado_em=instante,
    )


def _canal_tiktok(marcas: list[str]) -> CanalAtendimento:
    cobertura = _cobertura(marcas, tuple(BRANDS_IN_SCOPE), "Fora do escopo da Torre.")
    if not cobertura.marcas:
        return CanalAtendimento(canal="tiktok", cobertura=cobertura, indicadores=[])
    motivo = "Devoluções e cancelamentos do TikTok ainda não são lidos pela Torre (API Return & Refund)."
    return CanalAtendimento(
        canal="tiktok", cobertura=cobertura,
        indicadores=[
            _sem_fonte("tiktok_devolucoes", "Devoluções e reembolsos", "contagem", motivo,
                       "Pedidos de devolução ou reembolso abertos pelo comprador."),
            _sem_fonte("tiktok_cancelamentos", "Cancelamentos com motivo", "contagem", motivo,
                       "Cancelamentos com o motivo registrado pelo TikTok."),
        ],
        pendencias=PENDENCIAS_TIKTOK,
    )


def montar_atendimento(*, brands: Optional[list[str]] = None, dias: int = JANELA_PADRAO_DIAS,
                       consultar: Optional[Consultar] = None,
                       agora: Optional[datetime] = None) -> AtendimentoResponse:
    agora = agora or datetime.now(timezone.utc)
    consultar = consultar or consultar_datamart
    marcas = [m for m in MARCAS_PERMITIDAS if brands is None or m in brands]
    janela = calcular_janela(dias, agora)
    return AtendimentoResponse(
        gerado_em=agora,
        janela=janela,
        canais=[
            _canal_ml(consultar, marcas, janela, agora),
            _canal_shopee(consultar, marcas, janela, agora),
            _canal_tiktok(marcas),
        ],
    )


# ---------------------------------------------------------------------------
# Aba CX (menu principal) — primeira versao, so' Shopee (04/11/2026)
# ---------------------------------------------------------------------------
GRUPO_ATENDIMENTO = "Atendimento"


def _chat_shopee(brands: Optional[list[str]], consultar: Consultar, agora: datetime,
                 consultar_torre: Optional[Consultar] = None) -> ChatShopee:
    """Recorte de atendimento da Saude da Conta, a mesma leitura da Performance."""
    from app.services import performance_oficial_service as po  # import tardio: evita ciclo

    oficial = po.montar_performance_oficial(canal="shopee", brands=brands,
                                            consultar=consultar, consultar_torre=consultar_torre,
                                            agora=agora)
    contas = []
    for conta in oficial.contas:
        metricas = [m for m in conta.metricas if m.grupo == GRUPO_ATENDIMENTO]
        fora = sum(1 for m in metricas if m.situacao == "fora")
        contas.append(conta.model_copy(update={
            "metricas": metricas,
            "destaques": [],
            "resumo": (f"{fora} métrica(s) de atendimento fora da meta" if fora
                       else "Atendimento dentro da meta da Shopee"),
            "tom": "atencao" if fora else "bom",
        }))
    return ChatShopee(estado=oficial.estado, motivo=oficial.motivo, fonte=oficial.fonte,
                      contas=contas, sem_foto=oficial.sem_foto)



# ---------------------------------------------------------------------------
# Coleta de CX da Shopee no Airflow (DAG `shopee_cx_coleta`, 04/11/2026)
# ---------------------------------------------------------------------------
FONTE_CX_SHOPEE = "Data Mart · raw.shopee_returns e raw.shopee_chat_snapshot (shopee_cx_coleta)"
MOTIVO_SEM_COLETA = "Aguardando a primeira coleta de CX da Shopee no Airflow (DAG shopee_cx_coleta)."

SQL_TABELAS_CX = (
    "SELECT to_regclass('raw.shopee_returns') IS NOT NULL AS returns, "
    "to_regclass('raw.shopee_chat_snapshot') IS NOT NULL AS chat, "
    "to_regclass('raw.shopee_chat_respostas') IS NOT NULL AS respostas"
)

#: Status da devolucao na Shopee (04/11/2026: ACCEPTED, CANCELLED, PROCESSING,
#: JUDGING, REQUESTED, CLOSED, SELLER_DISPUTE). So' ACCEPTED vira reembolso;
#: CANCELLED = o comprador desistiu do pedido de devolucao.
STATUS_ACEITA = "ACCEPTED"
STATUS_CANCELADA = "CANCELLED"
STATUS_EM_ANDAMENTO = ("REQUESTED", "PROCESSING", "JUDGING", "SELLER_DISPUTE")

_SQL_DEVOLUCOES_MARCAS = """
SELECT brand AS marca,
       COUNT(*) AS devolucoes,
       COUNT(*) FILTER (WHERE status = 'ACCEPTED') AS aceitas,
       COUNT(*) FILTER (WHERE status IN ('REQUESTED', 'PROCESSING', 'JUDGING', 'SELLER_DISPUTE')) AS em_andamento,
       COUNT(*) FILTER (WHERE status = 'CANCELLED') AS canceladas,
       SUM(refund_amount) FILTER (WHERE status = 'ACCEPTED') AS reembolsado
  FROM {tabela}
 WHERE brand = ANY(:marcas)
   AND (create_time AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN :inicio AND :fim
 GROUP BY brand
"""
SQL_DEVOLUCOES_MARCAS = _SQL_DEVOLUCOES_MARCAS.format(tabela="raw.shopee_returns")
SQL_DEVOLUCOES_MARCAS_EXPORT = _SQL_DEVOLUCOES_MARCAS.format(tabela="marts.shopee_returns_export")

#: Motivos so' dos pedidos que o comprador NAO cancelou: o desistido nao e'
#: problema a tratar e aparece a' parte (`excluidos`).
_SQL_DEVOLUCOES_MOTIVOS = """
SELECT COALESCE(reason, '') AS motivo,
       COUNT(*) FILTER (WHERE status IS DISTINCT FROM 'CANCELLED') AS quantidade,
       COUNT(*) FILTER (WHERE status = 'CANCELLED') AS canceladas
  FROM {tabela}
 WHERE brand = ANY(:marcas)
   AND (create_time AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN :inicio AND :fim
 GROUP BY 1
"""
SQL_DEVOLUCOES_MOTIVOS = _SQL_DEVOLUCOES_MOTIVOS.format(tabela="raw.shopee_returns")
SQL_DEVOLUCOES_MOTIVOS_EXPORT = _SQL_DEVOLUCOES_MOTIVOS.format(tabela="marts.shopee_returns_export")

#: `to_jsonb(s)` le as colunas de conversas mesmo antes de a coleta cria-las
#: (ALTER TABLE ... ADD COLUMN IF NOT EXISTS no primeiro run): coluna que nao
#: existe vira NULL em vez de quebrar a consulta.
_SQL_NAO_LIDAS = """
SELECT DISTINCT ON (brand) brand AS marca, unread_count, captured_at,
       (to_jsonb(s) ->> 'conversas_7d')::int        AS conversas_7d,
       (to_jsonb(s) ->> 'conversas_30d')::int       AS conversas_30d,
       (to_jsonb(s) ->> 'aguardando_resposta')::int AS aguardando_resposta,
       (to_jsonb(s) ->> 'conversas_truncado')::boolean AS conversas_truncado,
       (to_jsonb(s) ->> 'aguardando_ate_1h')::int   AS aguardando_ate_1h,
       (to_jsonb(s) ->> 'aguardando_1a4h')::int     AS aguardando_1a4h,
       (to_jsonb(s) ->> 'aguardando_4a24h')::int    AS aguardando_4a24h,
       (to_jsonb(s) ->> 'aguardando_mais_24h')::int AS aguardando_mais_24h,
       (to_jsonb(s) ->> 'espera_max_s')::int        AS espera_max_s,
       (to_jsonb(s) ->> 'tempo_resposta_medio_s')::int AS tempo_resposta_medio_s
  FROM {tabela} s
 WHERE brand = ANY(:marcas)
 ORDER BY brand, captured_at DESC
"""
SQL_NAO_LIDAS = _SQL_NAO_LIDAS.format(tabela="raw.shopee_chat_snapshot")
SQL_NAO_LIDAS_EXPORT = _SQL_NAO_LIDAS.format(tabela="marts.shopee_chat_export")

#: Tempo de resposta do chat (05/11/2026): uma linha por resposta da loja
#: (`shopee_cx_coleta` + `get_message`). Mediana e p90 da espera, sem as
#: respostas automaticas (origem com "auto" ou "bot").
SQL_TEMPO_RESPOSTA = """
SELECT brand AS marca,
       COUNT(*) AS respostas,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY espera_s) AS mediana_s,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY espera_s) AS p90_s,
       COUNT(*) FILTER (WHERE espera_s <= 3600) AS ate_1h,
       MIN(respondido_em) AS desde
  FROM raw.shopee_chat_respostas
 WHERE brand = ANY(:marcas)
   AND respondido_em >= :desde
   AND COALESCE(origem, '') !~* '(auto|bot)'
 GROUP BY brand
"""

#: O bot manda a lista inteira de devolucoes dos ultimos 30 dias: a Kokeshi so'
#: tem "zero medido" depois da primeira carga de devolucoes.
SQL_TABELAS_CX_EXPORT = (
    "SELECT to_regclass('marts.shopee_returns_export') IS NOT NULL AS returns, "
    "to_regclass('marts.shopee_chat_export') IS NOT NULL AS chat"
)
SQL_DEVOLUCOES_COM_CARGA_EXPORT = """
SELECT DISTINCT brand AS marca FROM marts.shopee_returns_export WHERE brand = ANY(:marcas)
"""

#: Motivos de devolucao da Shopee (campo `reason`) -> (rotulo, natureza).
MOTIVOS_DEVOLUCAO: dict[str, tuple[str, str]] = {
    "CHANGE_MIND": ("Mudou de ideia", "comprador"),
    "MUTUAL_AGREE": ("Acordo entre as partes", "comprador"),
    "NOT_RECEIPT": ("Não recebeu o pedido", "logistica"),
    "NON_RECEIPT": ("Não recebeu o pedido", "logistica"),
    "DAMAGED_OTHERS": ("Chegou danificado", "logistica"),
    "PHYSICAL_DMG": ("Chegou danificado", "logistica"),
    "DAMAGED": ("Chegou danificado", "logistica"),
    "FUNCTIONAL_DMG": ("Com defeito de funcionamento", "vendedor"),
    "WRONG_ITEM": ("Item errado", "vendedor"),
    "ITEM_WRONGDAMAGED": ("Item errado ou danificado", "vendedor"),
    "ITEM_MISSING": ("Item faltando", "vendedor"),
    "DIFFERENT_DESCRIPTION": ("Diferente do anúncio", "vendedor"),
    "DIFF_DESC": ("Diferente do anúncio", "vendedor"),
    "EXPIRED_PRODUCT": ("Produto vencido", "vendedor"),
    "SPILLED_CONTENTS": ("Produto vazou", "vendedor"),
    "ITEM_FAKE": ("Suspeita de falsificação", "vendedor"),
    "SUSPICIOUS_PARCEL": ("Pacote suspeito ou violado", "logistica"),
    "COUNTERFEIT": ("Suspeita de falsificação", "vendedor"),
    "USED_PRODUCT": ("Produto usado", "vendedor"),
    "OTHERS": ("Outros", "comprador"),
    "OTHER": ("Outros", "comprador"),
}


def rotular_devolucao(motivo: Any) -> tuple[str, str, str, bool]:
    """(codigo, rotulo, natureza, mapeado) de um motivo de devolucao Shopee."""
    bruto = str(motivo or "").strip()
    if not bruto or bruto.upper() == "NONE":
        return "sem_motivo", "Sem motivo informado", "reclamacao", True
    chave = bruto.upper()
    if chave in MOTIVOS_DEVOLUCAO:
        rotulo, natureza = MOTIVOS_DEVOLUCAO[chave]
        return chave.lower(), rotulo, natureza, True
    return _slug(bruto.lower()), bruto[:120], "reclamacao", False


def _tabelas_cx(consultar: Consultar) -> Optional[dict]:
    linhas = _rodar(consultar, "tabelas_cx", SQL_TABELAS_CX, {})
    if linhas is None:
        return None
    r = linhas[0] if linhas else {}
    return {"returns": bool(r.get("returns")), "chat": bool(r.get("chat")),
            "respostas": bool(r.get("respostas"))}


def _devolucoes_shopee(consultar: Consultar, marcas: list[str], janela: Janela,
                       existe: Optional[bool], *, consultar_torre: Optional[Consultar] = None,
                       exp: tuple[list[str], Optional[bool]] = ([], None)) -> DevolucoesShopee:
    """Lojas com app (Data Mart) + lojas do bot (`exp` = marcas, tabela existe)."""
    marcas_exp, existe_exp = exp
    if marcas_exp and consultar_torre is not None:
        return _devolucoes_juntas(consultar, marcas, janela, existe, consultar_torre, marcas_exp, existe_exp)
    return _devolucoes_uma_fonte(consultar, marcas, janela, existe)


def _devolucoes_uma_fonte(consultar: Consultar, marcas: list[str], janela: Janela,
                          existe: Optional[bool], *, sql_marcas: str = SQL_DEVOLUCOES_MARCAS,
                          sql_motivos: str = SQL_DEVOLUCOES_MOTIVOS,
                          com_carga: Optional[set] = None) -> DevolucoesShopee:
    ajuda = "Motivo registrado pela Shopee na devolução ou reembolso (campo reason)."
    if existe is None:
        return DevolucoesShopee(estado="erro", fonte=FONTE_CX_SHOPEE, motivo_indisponivel=MOTIVO_ERRO)
    if not existe:
        return DevolucoesShopee(estado="sem_fonte", fonte=FONTE_CX_SHOPEE,
                                motivo_indisponivel=MOTIVO_SEM_COLETA)
    if not marcas:
        return DevolucoesShopee(estado="vazio_legitimo", fonte=FONTE_CX_SHOPEE,
                                motivo_indisponivel="Nenhuma loja Shopee no filtro.")
    params = {"marcas": marcas, "inicio": janela.inicio, "fim": janela.fim}
    por_marca = _rodar(consultar, "devolucoes_marcas", sql_marcas, params)
    motivos = _rodar(consultar, "devolucoes_motivos", sql_motivos, params)
    if por_marca is None:
        return DevolucoesShopee(estado="erro", fonte=FONTE_CX_SHOPEE, motivo_indisponivel=MOTIVO_ERRO)
    linhas = {str(r.get("marca")): r for r in por_marca}
    marcas_linhas = []
    for m in marcas:
        r = linhas.get(m)
        if r is None and com_carga is not None and m not in com_carga:
            # conta do bot que nunca mandou devolucoes: ausente, nunca zero
            marcas_linhas.append(LinhaDevolucao(marca=m, devolucoes=None, aceitas=None, em_andamento=None,
                                                canceladas=None, reembolsado=None))
            continue
        # tabela existe e a marca nao tem linha = zero medido na janela
        if r is None:
            marcas_linhas.append(LinhaDevolucao(marca=m, devolucoes=0, aceitas=0, em_andamento=0,
                                                canceladas=0, reembolsado=0.0))
            continue
        reemb = r.get("reembolsado")
        aceitas = _int(r.get("aceitas"))
        marcas_linhas.append(LinhaDevolucao(
            marca=m,
            devolucoes=_int(r.get("devolucoes")),
            aceitas=aceitas,
            em_andamento=_int(r.get("em_andamento")),
            canceladas=_int(r.get("canceladas")),
            # sem aceita = nada reembolsado (zero); com aceita e soma nula = ausente
            reembolsado=round(float(reemb), 2) if reemb is not None else (0.0 if not aceitas else None),
        ))

    def soma(campo):
        vals = [getattr(x, campo) for x in marcas_linhas if getattr(x, campo) is not None]
        return sum(vals) if vals else None

    reembolsado = soma("reembolsado")
    canceladas_motivos = sum(_int(r.get("canceladas")) or 0 for r in (motivos or []))
    bloco = _bloco_motivos(
        "Motivos de devolução", ajuda,
        None if motivos is None else [
            (*rotular_devolucao(r.get("motivo")), _int(r.get("quantidade")) or 0) for r in motivos
        ],
        excluidos=canceladas_motivos or None,
        rotulo_excluidos="pedidos de devolução cancelados pelo próprio comprador (fora dos motivos)",
        texto_vazio="Nenhuma devolução no recorte.",
    )
    return DevolucoesShopee(
        estado="dado", fonte=FONTE_CX_SHOPEE, total=soma("devolucoes"), aceitas=soma("aceitas"),
        em_andamento=soma("em_andamento"), canceladas=soma("canceladas"),
        reembolsado=None if reembolsado is None else round(reembolsado, 2),
        motivos=bloco, marcas=marcas_linhas,
    )


def _nao_lidas(consultar: Consultar, marcas: list[str],
               existe: Optional[bool], sql: str = SQL_NAO_LIDAS) -> tuple[str, list[NaoLidas]]:
    if existe is None:
        return "erro", []
    if not existe:
        return "sem_fonte", []
    if not marcas:
        return "vazio_legitimo", []
    linhas = _rodar(consultar, "nao_lidas", sql, {"marcas": marcas})
    if linhas is None:
        return "erro", []
    return "dado", [
        NaoLidas(marca=str(r.get("marca")), valor=_int(r.get("unread_count")),
                 capturado_em=_utc(r.get("captured_at")),
                 conversas_7d=_int(r.get("conversas_7d")),
                 conversas_30d=_int(r.get("conversas_30d")),
                 aguardando_resposta=_int(r.get("aguardando_resposta")),
                 conversas_truncado=(r.get("conversas_truncado")
                                     if isinstance(r.get("conversas_truncado"), bool) else None),
                 aguardando_ate_1h=_int(r.get("aguardando_ate_1h")),
                 aguardando_1a4h=_int(r.get("aguardando_1a4h")),
                 aguardando_4a24h=_int(r.get("aguardando_4a24h")),
                 aguardando_mais_24h=_int(r.get("aguardando_mais_24h")),
                 espera_max_s=_int(r.get("espera_max_s")),
                 tempo_resposta_s=_int(r.get("tempo_resposta_medio_s")),
                 tempo_resposta_fonte=("media_seller_centre"
                                       if _int(r.get("tempo_resposta_medio_s")) is not None else None))
        for r in linhas if str(r.get("marca")) in marcas
    ]


def _com_tempo_resposta(consultar: Consultar, nao_lidas: list[NaoLidas], marcas: list[str],
                        existe: Optional[bool], desde: datetime) -> list[NaoLidas]:
    """Junta a mediana do tempo de resposta (lojas com app) em cada foto.

    Tabela ausente ou falha: as fotos seguem sem tempo (None, nunca zero).
    """
    if not existe or not marcas:
        return nao_lidas
    linhas = _rodar(consultar, "tempo_resposta", SQL_TEMPO_RESPOSTA, {"marcas": marcas, "desde": desde})
    if not linhas:
        return nao_lidas
    por_marca = {str(r.get("marca")): r for r in linhas}
    out = []
    for n in nao_lidas:
        r = por_marca.get(n.marca)
        total = _int(r.get("respostas")) if r else None
        if not r or not total:
            out.append(n)
            continue
        ate_1h = _int(r.get("ate_1h")) or 0
        mediana, p90 = r.get("mediana_s"), r.get("p90_s")
        out.append(n.model_copy(update={
            "tempo_resposta_s": None if mediana is None else int(round(float(mediana))),
            "tempo_resposta_p90_s": None if p90 is None else int(round(float(p90))),
            "respostas": total,
            "respostas_ate_1h_pct": round(ate_1h * 100 / total, 1),
            "respostas_desde": _utc(r.get("desde")),
            "tempo_resposta_fonte": "mediana",
        }))
    return out


def _juntar_estados(a: str, b: str) -> str:
    """Estado do bloco com duas fontes: basta uma com dado."""
    for estado in ("dado", "erro", "sem_fonte", "vazio_legitimo"):
        if estado in (a, b):
            return estado
    return a


def _devolucoes_juntas(consultar, marcas, janela, existe, consultar_torre, marcas_exp,
                       existe_exp) -> DevolucoesShopee:
    """Data Mart + Neon. Junta por marca e por motivo (o mesmo `_bloco_motivos`)."""
    com_carga = None
    if existe_exp:
        achadas = _rodar(consultar_torre, "devolucoes_carga_export", SQL_DEVOLUCOES_COM_CARGA_EXPORT,
                         {"marcas": marcas_exp})
        com_carga = None if achadas is None else {str(r.get("marca")) for r in achadas}
    if existe_exp is None or (existe_exp and com_carga is None):
        d_exp = DevolucoesShopee(estado="erro", fonte=FONTE_SHOPEE_EXPORT, motivo_indisponivel=MOTIVO_ERRO)
    elif not existe_exp or not com_carga:
        d_exp = DevolucoesShopee(estado="sem_fonte", fonte=FONTE_SHOPEE_EXPORT,
                                 motivo_indisponivel="Aguardando a primeira carga do bot (devoluções da Kokeshi).")
    else:
        d_exp = _devolucoes_uma_fonte(consultar_torre, marcas_exp, janela, True,
                                      sql_marcas=SQL_DEVOLUCOES_MARCAS_EXPORT,
                                      sql_motivos=SQL_DEVOLUCOES_MOTIVOS_EXPORT, com_carga=com_carga)
    d_api = (_devolucoes_uma_fonte(consultar, marcas, janela, existe) if marcas
             else DevolucoesShopee(estado="vazio_legitimo", fonte=FONTE_CX_SHOPEE))
    if d_exp.estado != "dado":
        if d_api.estado != "dado":
            return d_api if marcas else d_exp
        # a Kokeshi aparece na tabela como ausente, com o motivo no bloco
        linhas = d_api.marcas + [LinhaDevolucao(marca=m, devolucoes=None, reembolsado=None) for m in marcas_exp]
        return d_api.model_copy(update={"marcas": linhas})
    if d_api.estado != "dado":
        if marcas and d_api.estado == "erro":
            linhas = [LinhaDevolucao(marca=m, devolucoes=None, reembolsado=None) for m in marcas] + d_exp.marcas
            return d_exp.model_copy(update={"marcas": linhas, "fonte": f"{FONTE_CX_SHOPEE} + {FONTE_SHOPEE_EXPORT}"})
        return d_exp

    def soma(*vals):
        ok = [v for v in vals if v is not None]
        return sum(ok) if ok else None

    itens: list[tuple] = []
    for bloco in (d_api.motivos, d_exp.motivos):
        if bloco is not None and bloco.estado == "dado":
            itens += [(i.codigo, i.rotulo, i.natureza, i.mapeado, i.quantidade) for i in bloco.itens]
    excluidos = soma(*(b.excluidos for b in (d_api.motivos, d_exp.motivos) if b is not None))
    erro_motivos = any(b is not None and b.estado == "erro" for b in (d_api.motivos, d_exp.motivos))
    motivos = _bloco_motivos(
        "Motivos de devolução", "Motivo registrado pela Shopee na devolução ou reembolso (campo reason).",
        None if erro_motivos and not itens else itens,
        excluidos=excluidos or None,
        rotulo_excluidos="pedidos de devolução cancelados pelo próprio comprador (fora dos motivos)",
        texto_vazio="Nenhuma devolução no recorte.",
    )
    reemb = soma(d_api.reembolsado, d_exp.reembolsado)
    return DevolucoesShopee(
        estado="dado", fonte=f"{FONTE_CX_SHOPEE} + {FONTE_SHOPEE_EXPORT}",
        total=soma(d_api.total, d_exp.total), aceitas=soma(d_api.aceitas, d_exp.aceitas),
        em_andamento=soma(d_api.em_andamento, d_exp.em_andamento),
        canceladas=soma(d_api.canceladas, d_exp.canceladas),
        reembolsado=None if reemb is None else round(reemb, 2),
        motivos=motivos, marcas=d_api.marcas + d_exp.marcas,
    )


def _tabelas_cx_export(consultar_torre: Optional[Consultar], marcas_exp: list[str]) -> Optional[dict]:
    if not marcas_exp or consultar_torre is None:
        return {"returns": False, "chat": False}
    linhas = _rodar(consultar_torre, "tabelas_cx_export", SQL_TABELAS_CX_EXPORT, {})
    if linhas is None:
        return None
    r = linhas[0] if linhas else {}
    return {"returns": bool(r.get("returns")), "chat": bool(r.get("chat"))}


def montar_cx_shopee(*, brands: Optional[list[str]] = None, dias: int = JANELA_PADRAO_DIAS,
                     consultar: Optional[Consultar] = None,
                     consultar_torre: Optional[Consultar] = None,
                     agora: Optional[datetime] = None) -> CxShopeeResponse:
    """Aba CX: chat (Saude da Conta) + cancelamentos e devolucoes (pedidos).

    Lojas com app pelo Data Mart; Kokeshi pelo que o bot grava no Neon.
    """
    agora = agora or datetime.now(timezone.utc)
    consultar = consultar or consultar_datamart
    consultar_torre = consultar_torre or consultar_torre_padrao
    marcas = [m for m in MARCAS_PERMITIDAS if brands is None or m in brands]
    janela = calcular_janela(dias, agora)
    marcas_shopee = [m for m in marcas if m in SHOPEE_MARCAS]
    marcas_exp = [m for m in marcas if m in SHOPEE_EXPORT]
    # sem loja com app no filtro: nada a checar no Data Mart (bloco vazio legitimo)
    tabelas = _tabelas_cx(consultar) if marcas_shopee else {"returns": True, "chat": True}
    tab_exp = _tabelas_cx_export(consultar_torre, marcas_exp)
    chat = _chat_shopee(brands, consultar, agora, consultar_torre)

    estado_nl, nao_lidas = _nao_lidas(consultar, marcas_shopee,
                                      None if tabelas is None else tabelas["chat"])
    nao_lidas = _com_tempo_resposta(consultar, nao_lidas, marcas_shopee,
                                    None if tabelas is None else tabelas.get("respostas"),
                                    agora - timedelta(days=janela.dias))
    if marcas_exp:
        estado_exp, nl_exp = _nao_lidas(consultar_torre, marcas_exp,
                                        None if tab_exp is None else tab_exp["chat"],
                                        sql=SQL_NAO_LIDAS_EXPORT)
        estado_nl = estado_exp if not marcas_shopee else _juntar_estados(estado_nl, estado_exp)
        nao_lidas = nao_lidas + nl_exp
    chat = chat.model_copy(update={"nao_lidas_estado": estado_nl, "nao_lidas": nao_lidas})
    return CxShopeeResponse(
        gerado_em=agora,
        janela=janela,
        chat=chat,
        atendimento=_canal_shopee(consultar, marcas, janela, agora, consultar_torre),
        devolucoes=_devolucoes_shopee(
            consultar, marcas_shopee, janela, None if tabelas is None else tabelas["returns"],
            consultar_torre=consultar_torre,
            exp=(marcas_exp, None if tab_exp is None else tab_exp["returns"])),
    )


consultar_torre_padrao = consultar_torre
