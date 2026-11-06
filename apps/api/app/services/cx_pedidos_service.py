"""CX-PEDIDOS-MOTIVO (05/11/2026) — os pedidos de UM motivo do Pareto.

A aba CX mostra o Pareto de cancelamentos e devolucoes por motivo. Ao clicar
num motivo, a tela pede a lista dos pedidos dele: numero do pedido, marca,
dia, situacao e valor reembolsado (devolucao).

COMO FICA BARATO
----------------
O codigo do motivo sai de um mapa em Python (`rotular_shopee`,
`rotular_shopee_pt`, `rotular_devolucao`), entao o SQL nao sabe filtrar por
ele. Em vez de trazer todos os pedidos e filtrar aqui:

1. a mesma consulta AGRUPADA do Pareto devolve os textos crus de motivo;
2. os textos que viram o codigo pedido sao escolhidos aqui;
3. a lista sai com `motivo = ANY(:textos)` e `LIMIT`, do mais recente.

Duas consultas pequenas por fonte. O total vem do passo 1: e' exatamente o
numero da barra do Pareto, mesmo quando a lista e' cortada no teto.

AS MESMAS REGRAS DO PARETO
--------------------------
* Cancelamento: sem os nao pagos que a Shopee cancela sozinha.
* Devolucao: sem as que o proprio comprador cancelou.
* Kokeshi pelo Neon (bot); falha numa fonte nao derruba a outra (`parcial`).

NUMERO DO PEDIDO
----------------
Segue a decisao da fila da Expedicao (`expedicao_numero_pedido_publico`, ver
`config.py`): ligada, o numero sai; desligada, `pedido` e `devolucao` voltam
`None` e a lista continua util (marca, dia, situacao, valor).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Literal, Optional

from app.config import settings
from app.schemas.cx_atendimento import PedidoMotivo, PedidosMotivoResponse
from app.services import cx_atendimento_service as cx

Consultar = Callable[[str, dict], list[dict]]
Tipo = Literal["cancelamento", "devolucao"]

#: Teto da lista. O total exato vem da consulta agrupada.
TETO = 500

SQL_CANC_PEDIDOS = """
SELECT shop_account AS marca, order_sn, created_date_brt AS dia, cancel_by AS autor
  FROM silver.stg_shopee_orders
 WHERE shop_account = ANY(:marcas)
   AND created_date_brt BETWEEN :inicio AND :fim
   AND order_status = 'cancelled'
   AND COALESCE(cancel_reason, '') = ANY(:textos)
 ORDER BY created_date_brt DESC, order_sn
 LIMIT :teto
"""

SQL_CANC_PEDIDOS_EXPORT = f"""
SELECT shop_account AS marca, order_sn,
       (create_time AT TIME ZONE 'America/Sao_Paulo')::date AS dia, NULL::text AS autor
  FROM marts.expedicao_shopee_export_pedido
 WHERE shop_account = ANY(:marcas)
   AND (create_time AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN :inicio AND :fim
   AND order_status = 'CANCELLED' AND NOT {cx._NAO_PAGO_EXPORT}
   AND COALESCE(cancel_reason, '') = ANY(:textos)
 ORDER BY create_time DESC, order_sn
 LIMIT :teto
"""

_SQL_DEV_PEDIDOS = """
SELECT brand AS marca, order_sn, return_sn,
       (create_time AT TIME ZONE 'America/Sao_Paulo')::date AS dia, status, refund_amount
  FROM {tabela}
 WHERE brand = ANY(:marcas)
   AND (create_time AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN :inicio AND :fim
   AND status IS DISTINCT FROM 'CANCELLED'
   AND COALESCE(reason, '') = ANY(:textos)
 ORDER BY create_time DESC, return_sn
 LIMIT :teto
"""
SQL_DEV_PEDIDOS = _SQL_DEV_PEDIDOS.format(tabela="raw.shopee_returns")
SQL_DEV_PEDIDOS_EXPORT = _SQL_DEV_PEDIDOS.format(tabela="marts.shopee_returns_export")

#: Situacao da devolucao em portugues (status da Shopee).
SITUACAO_DEVOLUCAO = {
    "ACCEPTED": "Reembolsada",
    "REQUESTED": "Solicitada",
    "PROCESSING": "Em andamento",
    "JUDGING": "Em análise na Shopee",
    "SELLER_DISPUTE": "Contestada pela loja",
    "CLOSED": "Encerrada",
}
SITUACAO_CANCELAMENTO = {"buyer": "Cancelado pelo comprador", "seller": "Cancelado pela loja",
                         "system": "Cancelado pela Shopee"}


def _numero_publico() -> bool:
    return bool(getattr(settings, "expedicao_numero_pedido_publico", False))


def _texto(v: Any) -> Optional[str]:
    s = str(v).strip() if v is not None else ""
    return s or None


def _dia(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _textos_do_codigo(linhas: list[dict], rotular, codigo: str, *, com_autor: bool = False,
                      excluir: Callable[[dict], bool] = lambda r: False) -> tuple[list[str], int, Optional[str]]:
    """(textos crus, total, rotulo) dos motivos agrupados que viram `codigo`."""
    textos: set[str] = set()
    total = 0
    rotulo = None
    for r in linhas:
        if excluir(r):
            continue
        cod, rot, *_ = rotular(r.get("motivo"), r.get("autor")) if com_autor else rotular(r.get("motivo"))
        if cod != codigo:
            continue
        # o SQL compara o texto CRU (`COALESCE(motivo, '') = ANY(:textos)`)
        textos.add("" if r.get("motivo") is None else str(r.get("motivo")))
        total += cx._int(r.get("quantidade")) or 0
        rotulo = rotulo or rot
    return sorted(textos), total, rotulo


def _cancelamentos(consultar: Consultar, marcas: list[str], params: dict, codigo: str, exp: bool):
    """(pedidos, total, rotulo) de uma fonte, ou None se ela falhou."""
    if not marcas:
        return [], 0, None
    p = dict(params, marcas=marcas, nao_pago=cx.SHOPEE_NAO_PAGO)
    if exp:
        agrup = cx._rodar(consultar, "pedidos_canc_motivos_export", cx.SQL_SHOPEE_EXPORT_MOTIVOS, p)
        if agrup is None:
            return None
        textos, total, rotulo = _textos_do_codigo(agrup, cx.rotular_shopee_pt, codigo)
    else:
        agrup = cx._rodar(consultar, "pedidos_canc_motivos", cx.SQL_SHOPEE_MOTIVOS, p)
        if agrup is None:
            return None
        textos, total, rotulo = _textos_do_codigo(
            agrup, cx.rotular_shopee, codigo, com_autor=True,
            excluir=lambda r: str(r.get("motivo") or "").strip().lower() == cx.SHOPEE_NAO_PAGO)
    if not textos:
        return [], 0, None
    linhas = cx._rodar(consultar, "pedidos_canc", SQL_CANC_PEDIDOS_EXPORT if exp else SQL_CANC_PEDIDOS,
                       dict(p, textos=textos, teto=TETO + 1))
    if linhas is None:
        return None
    pedidos = [
        {"marca": str(r.get("marca")), "pedido": _texto(r.get("order_sn")), "devolucao": None,
         "dia": _dia(r.get("dia")),
         "situacao": SITUACAO_CANCELAMENTO.get(str(r.get("autor") or "").lower(), "Cancelado"),
         "valor": None}
        for r in linhas
    ]
    return pedidos, total, rotulo


def _devolucoes(consultar: Consultar, marcas: list[str], params: dict, codigo: str, exp: bool):
    if not marcas:
        return [], 0, None
    p = dict(params, marcas=marcas)
    agrup = cx._rodar(consultar, "pedidos_dev_motivos",
                      cx.SQL_DEVOLUCOES_MOTIVOS_EXPORT if exp else cx.SQL_DEVOLUCOES_MOTIVOS, p)
    if agrup is None:
        return None
    textos, total, rotulo = _textos_do_codigo(agrup, cx.rotular_devolucao, codigo)
    if not textos:
        return [], 0, None
    linhas = cx._rodar(consultar, "pedidos_dev", SQL_DEV_PEDIDOS_EXPORT if exp else SQL_DEV_PEDIDOS,
                       dict(p, textos=textos, teto=TETO + 1))
    if linhas is None:
        return None
    pedidos = []
    for r in linhas:
        valor = r.get("refund_amount")
        status = str(r.get("status") or "").upper()
        pedidos.append({
            "marca": str(r.get("marca")), "pedido": _texto(r.get("order_sn")),
            "devolucao": _texto(r.get("return_sn")), "dia": _dia(r.get("dia")),
            "situacao": SITUACAO_DEVOLUCAO.get(status, _texto(r.get("status"))),
            # so' a reembolsada tem valor; as outras ficam sem (nunca zero)
            "valor": round(float(valor), 2) if valor is not None and status == "ACCEPTED" else None,
        })
    return pedidos, total, rotulo


def montar_pedidos_motivo(*, tipo: Tipo, codigo: str, brands: Optional[list[str]] = None,
                          dias: int = cx.JANELA_PADRAO_DIAS, consultar: Optional[Consultar] = None,
                          consultar_torre: Optional[Consultar] = None,
                          agora: Optional[datetime] = None) -> PedidosMotivoResponse:
    agora = agora or datetime.now(timezone.utc)
    consultar = consultar or cx.consultar_datamart
    consultar_torre = consultar_torre or cx.consultar_torre_padrao
    janela = cx.calcular_janela(dias, agora)
    marcas = [m for m in cx.MARCAS_PERMITIDAS if brands is None or m in brands]
    api = [m for m in marcas if m in cx.SHOPEE_MARCAS]
    exp = [m for m in marcas if m in cx.SHOPEE_EXPORT]
    params = {"inicio": janela.inicio, "fim": janela.fim}

    if tipo == "devolucao":
        tab = cx._tabelas_cx(consultar) if api else {"returns": True}
        tab_exp = cx._tabelas_cx_export(consultar_torre, exp)
        api_ok = api if (tab or {}).get("returns") else []
        exp_ok = exp if (tab_exp or {}).get("returns") else []
        falhou = (api and tab is None) or (exp and tab_exp is None)
        partes = [_devolucoes(consultar, api_ok, params, codigo, False),
                  _devolucoes(consultar_torre, exp_ok, params, codigo, True)]
    else:
        falhou = False
        partes = [_cancelamentos(consultar, api, params, codigo, False),
                  _cancelamentos(consultar_torre, exp, params, codigo, True)]

    ok = [p for p in partes if p is not None]
    falhou = bool(falhou) or len(ok) < len(partes)
    base = dict(gerado_em=agora, janela=janela, tipo=tipo, codigo=codigo,
                numero_publico=_numero_publico())
    if not ok:
        return PedidosMotivoResponse(**base, estado="erro", motivo_indisponivel=cx.MOTIVO_ERRO,
                                     total=None, truncado=False, parcial=True, pedidos=[])

    pedidos = [x for p in ok for x in p[0]]
    total = sum(p[1] for p in ok)
    rotulo = next((p[2] for p in ok if p[2]), None)
    pedidos.sort(key=lambda x: (x["dia"] or date.min, x["pedido"] or ""), reverse=True)
    truncado = len(pedidos) > TETO or total > TETO
    pedidos = pedidos[:TETO]
    if not base["numero_publico"]:
        for x in pedidos:
            x["pedido"] = None
            x["devolucao"] = None
    return PedidosMotivoResponse(
        **base, rotulo=rotulo,
        estado="dado" if pedidos else "vazio_legitimo",
        motivo_indisponivel=(cx.MOTIVO_ERRO if falhou and not pedidos else
                             None if pedidos else "Nenhum pedido com este motivo no período."),
        total=total, truncado=truncado, parcial=falhou,
        pedidos=[PedidoMotivo(**x) for x in pedidos],
    )
