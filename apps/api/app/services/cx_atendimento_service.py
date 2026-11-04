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

from sqlalchemy import text

from app.schemas.account_performance import Cobertura, Indicador, MarcaFora
from app.schemas.cx_atendimento import (
    AtendimentoResponse,
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


def consultar_datamart(sql: str, params: dict) -> list[dict]:
    """Execucao real, somente leitura, no engine do Data Mart."""
    from app.database import datamart_engine  # import tardio: testes injetam `consultar`

    if datamart_engine is None:
        raise RuntimeError("datamart indisponivel")
    with datamart_engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


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
                   rotulo_excluidos: Optional[str] = None) -> BlocoMotivos:
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
                            motivo_indisponivel="Nenhum cancelamento no recorte.", **extra)
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


def _canal_shopee(consultar: Consultar, marcas: list[str], janela: Janela, agora: datetime) -> CanalAtendimento:
    cobertura = _cobertura(marcas, SHOPEE_MARCAS, "Sem app Shopee (só export manual).")
    if not cobertura.marcas:
        return CanalAtendimento(canal="shopee", cobertura=cobertura, indicadores=[])
    params = {"marcas": cobertura.marcas, "inicio": janela.inicio, "fim": janela.fim,
              "nao_pago": SHOPEE_NAO_PAGO}
    linhas = _rodar(consultar, "shopee_marcas", SQL_SHOPEE_MARCAS, params)
    motivos = _rodar(consultar, "shopee_motivos", SQL_SHOPEE_MOTIVOS, params)
    devol = _rodar(consultar, "shopee_devolucao", SQL_SHOPEE_DEVOLUCAO,
                   {"marcas": cobertura.marcas,
                    "desde": janela.fim - timedelta(days=DIAS_FOTOGRAFIA_DEVOLUCAO - 1)})
    jt = _texto_janela(janela)
    ok = linhas is not None
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
            # fotografia: sem linha para a marca = nenhum pedido em devolucao (zero medido)
            em_devolucao=(dev_marca.get(m, 0) if ok_dev else None),
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
                   valor=float(soma("em_devolucao")) if ok_dev else None, unidade="contagem",
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
