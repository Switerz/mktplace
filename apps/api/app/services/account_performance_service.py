"""T-0014 — Performance das contas: agregador INERTE de leitura.

Contrato: `docs/performance_contas_contrato.md` (§1.1, §4, §6).

SO' COMPOE SERVICOS EXISTENTES
------------------------------
Nenhum SQL novo mora aqui. Cada indicador Torre vem de uma funcao de servico
que ja' serve uma tela em producao:

  ml_cancelamento_torre / ml_nao_entregue_torre  performance_service.get_quality
  ml_expedicao_48h_torre                          expedicao_service.get_expedicao
  tiktok_ldr_operacional_torre                    expedicao_tiktok_service.obter_serie
  shopee_ldr_operacional_torre                    expedicao_shopee_ldr_service.obter_ldr

Os tres indicadores OFICIAIS sao estados fixos: nenhuma metrica oficial de
performance de conta esta ingerida hoje (§3 do contrato), entao nada e' chamado
para eles.

UMA FONTE CAI, AS OUTRAS SEGUEM
-------------------------------
Toda chamada de fonte e toda montagem de indicador ficam isoladas. Excecao vira
estado `erro` SO' do indicador afetado, com motivo FIXO: o texto da excecao nunca
sai na resposta nem no log (pode carregar DSN, SQL ou dado de cliente). Depois de
uma falha a transacao e' desfeita, senao o PostgreSQL recusaria as consultas das
fontes seguintes ("current transaction is aborted").

UNIDADES
--------
`pct` sai em ponto percentual (0-100). As LDRs dos servicos de expedicao vem em
fracao (0-1) e sao convertidas aqui, junto com o limiar, para a mesma unidade.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.deps.period import APP_TIMEZONE, EffectivePeriod
from app.schemas.account_performance import (
    ORDEM_CANAIS,
    CanalResposta,
    Cobertura,
    Indicador,
    Limiar,
    MarcaFora,
    PerformanceContasResponse,
)
from app.services import expedicao_service
from app.services import expedicao_shopee_ldr_service
from app.services import expedicao_tiktok_service
from app.services import performance_service
from app.services.gold_service import BRANDS_IN_SCOPE, ML_BRANDS, SHOPEE_BRANDS

logger = logging.getLogger(__name__)

#: Allowlist do filtro `brands`. E' o escopo de marcas que a Torre ja' usa.
MARCAS_PERMITIDAS: tuple[str, ...] = tuple(BRANDS_IN_SCOPE)

#: Janela do cancelamento e do nao entregue ML: ultimos N dias FECHADOS (D-1),
#: porque a fonte e' diaria e o dia corrente e' parcial.
JANELA_ML_DIAS = 30

#: Publicacao mais velha que isto ganha aviso de frescor (mesmo limite da
#: "fonte vencida" da LDR Shopee).
LIMITE_FRESCOR_HORAS = 6.0

#: Motivo FIXO do estado `erro`. Nunca o texto da excecao.
MOTIVO_ERRO = "A fonte falhou ao responder; nenhum valor foi exibido."

MOTIVO_ML_NAO_ENTREGUE_AUSENTE = "Dado de não entregue ausente na fonte."
MOTIVO_ML_SEM_MARCA = "Nenhuma marca selecionada tem conta no Mercado Livre."
MOTIVO_SHOPEE_SEM_MARCA = "Nenhuma marca selecionada tem app Shopee."

MOTIVO_FORA_ML = "Sem conta Mercado Livre no cadastro."
MOTIVO_FORA_SHOPEE = "Conta sem app Shopee (ausente da fonte de pedidos da Shopee)."

TEXTO_REPUTACAO_ML = (
    "Reputação oficial do Mercado Livre ainda não capturada pela Torre."
)
#: Limite da DESCOBERTA, nao prova universal de inexistencia: a doc consultada
#: so' documenta lojas dos EUA; para o BR a capacidade nao foi comprovada.
TEXTO_SPS_TIKTOK = (
    "SPS/saúde da conta do TikTok não ingeridos pela Torre; capacidade por API "
    "para lojas BR não comprovada (doc oficial consultada em 26/10/2026 indica "
    "disponibilidade só para lojas dos EUA)."
)
TEXTO_HEALTH_SHOPEE = (
    "Autorização da API de saúde da conta da Shopee não comprovada; probe de "
    "21/10/2026 inconclusivo, sem chamadas."
)
TEXTO_LDR_TIKTOK_NAO_PUBLICADA = (
    "LDR operacional do TikTok (cálculo Torre) não publicada."
)
TEXTO_LDR_SHOPEE_NAO_PUBLICADA = (
    "LDR operacional da Shopee (cálculo Torre) ainda não publicada. Não é "
    "métrica oficial de saúde da conta."
)

#: `unavailable_reason` da Expedicao -> texto. ALLOWLIST: um motivo fora daqui
#: cai no generico e o codigo nao e' ecoado.
MOTIVOS_EXPEDICAO = {
    "feature_flag_disabled": "Expedição desligada na API.",
    "channel_disabled": "Expedição do Mercado Livre ainda não liberada na API.",
    "no_snapshot_published": "Nenhuma fotografia de expedição do Mercado Livre publicada.",
    "inconsistent_batch": "Fotografia de expedição inconsistente; nada exibido.",
}
MOTIVO_EXPEDICAO_GENERICO = "Fotografia de expedição indisponível."

#: Estados de serving que significam "serie nao existe" (§1.1 decisao 3).
NAO_PUBLICADA_TIKTOK = {
    expedicao_tiktok_service.UNAVAILABLE_NOT_MIGRATED,
    expedicao_tiktok_service.UNAVAILABLE_NO_DATA,
}
NAO_PUBLICADA_SHOPEE = {
    expedicao_shopee_ldr_service.UNAVAILABLE_DISABLED,
    expedicao_shopee_ldr_service.UNAVAILABLE_NOT_MIGRATED,
    expedicao_shopee_ldr_service.UNAVAILABLE_NO_DATA,
}

AJUDA_TIKTOK_RECONSTRUIDO = (
    "Cálculo Torre · prazo reconstruído (2/1 dias úteis): coleta 2 dias úteis e "
    "despacho 1 dia útil após o pagamento. Atrasados (coletados após o prazo + "
    "não coletados vencidos) sobre a base madura, por vencimento. Não é a LDR "
    "oficial do TikTok, que não é ingerida pela Torre."
)
AJUDA_TIKTOK_OFICIAL = (
    "Cálculo Torre sobre o prazo oficial TikTok por pedido. Atrasados sobre a "
    "base madura, por vencimento. Não é a LDR oficial agregada do TikTok."
)

#: Metadado ESTATICO de cada indicador. O que depende da fonte (estado, valor,
#: frescor, cobertura) e' montado em tempo de requisicao.
META: dict[str, dict[str, Any]] = {
    "ml_reputacao_oficial": {
        "rotulo": "Reputação oficial do Mercado Livre (termômetro, MercadoLíder, "
                  "reclamações, atraso de despacho, cancelamentos)",
        "tipo": "oficial",
        "unidade": None,
        "fonte": "Mercado Livre GET /users/{id} → seller_reputation (não ingerido)",
        "ajuda": "Métrica oficial da plataforma. Enquanto não for capturada, "
                 "nenhum número Torre a substitui.",
    },
    "ml_cancelamento_torre": {
        "rotulo": "Taxa de cancelamento · Cálculo Torre · todos os cancelamentos, "
                  "qualquer autor · não é o cancelamento da reputação ML",
        "tipo": "torre",
        "unidade": "pct",
        "fonte": "marts.fact_marketplace_daily_performance → /api/v1/performance/quality "
                 "(ml_cancel_rate_pct)",
        "ajuda": "Cancelados / (pedidos + cancelados), qualquer autor. A reputação "
                 "ML conta só cancelamentos feitos pelo vendedor; as faixas do ML "
                 "não se aplicam a este número.",
    },
    "ml_nao_entregue_torre": {
        "rotulo": "Não entregue · Cálculo Torre · shipments não entregues / "
                  "pedidos pagos",
        "tipo": "torre",
        "unidade": "pct",
        "fonte": "marts.fact_marketplace_daily_performance → /api/v1/performance/quality "
                 "(ml_not_delivered_rate_pct)",
        "ajuda": "Shipments não entregues / pedidos pagos (pedidos − cancelados) "
                 "do período, somados só nas marcas com dado de não entregue "
                 "(numerador e denominador). Não é métrica de reputação do ML.",
    },
    "ml_expedicao_48h_torre": {
        "rotulo": "Expedição acima de 48 h · Cálculo Torre · 48 h é limite "
                  "interno, não SLA do ML",
        "tipo": "torre",
        "unidade": "contagem",
        "fonte": "marts.expedicao_fila_atual / marts.expedicao_refresh_run → "
                 "/api/v1/expedicao?channel=mercadolivre (over_48h_count)",
        "ajuda": "Shipments ready_to_ship de pedidos pagos abertos há mais de 48 h "
                 "na fotografia atual. Prazo de despacho do Mercado Livre não "
                 "ingerido pela Torre; 48 h é limite operacional interno.",
    },
    "shopee_account_health_oficial": {
        "rotulo": "Saúde da conta oficial da Shopee (nota, métricas, pontos, punições)",
        "tipo": "oficial",
        "unidade": None,
        "fonte": "Shopee AccountHealth (GET /api/v2/account_health/*), não ingerido",
        "ajuda": "Métrica oficial da plataforma. O endpoint existe, mas a "
                 "autorização do nosso app ainda não foi provada.",
    },
    "shopee_ldr_operacional_torre": {
        "rotulo": "LDR operacional (coleta × prazo Shopee) · Cálculo Torre",
        "tipo": "torre",
        "unidade": "pct",
        "fonte": "marts.expedicao_shopee_ldr_daily → /api/v1/expedicao/shopee/ldr",
        "ajuda": "Cálculo Torre · não é a Late Shipment Rate oficial nem métrica de "
                 "saúde da conta da Shopee. Pedidos pagos e despachados pelo "
                 "vendedor não coletados até o ship_by_date, por vencimento; FBS "
                 "fica fora.",
    },
    "tiktok_sps_ahr_oficial": {
        "rotulo": "SPS, AHR e violações (saúde da conta oficial do TikTok)",
        "tipo": "oficial",
        "unidade": None,
        "fonte": "TikTok Shop API analytics/shop_performances, não ingerido (doc "
                 "oficial consultada em 26/10/2026 indica disponibilidade só para "
                 "lojas dos EUA); AHR e violações: capacidade por API para lojas "
                 "BR não comprovada",
        "ajuda": "Métrica oficial da plataforma, não ingerida pela Torre; "
                 "capacidade por API para lojas BR não comprovada.",
    },
    "tiktok_ldr_operacional_torre": {
        "rotulo": "LDR operacional de coleta · Cálculo Torre",
        "tipo": "torre",
        "unidade": "pct",
        "fonte": "marts.expedicao_tiktok_dispatch_daily → "
                 "/api/v1/expedicao/tiktok?event=coleta",
        "ajuda": AJUDA_TIKTOK_RECONSTRUIDO,
    },
}


# ---------------------------------------------------------------------------
# Isolamento de falhas
# ---------------------------------------------------------------------------
def _desfazer(db) -> None:
    """Libera a transacao abortada para a proxima fonte. Nunca levanta."""
    try:
        rollback = getattr(db, "rollback", None)
        if rollback is not None:
            rollback()
    except Exception:  # noqa: BLE001 - limpeza best-effort
        pass


def _registrar_falha(onde: str, exc: BaseException) -> None:
    # So' a CLASSE da excecao: a mensagem pode trazer DSN, SQL ou dado.
    logger.warning("performance-contas: falha em %s (%s)", onde, type(exc).__name__)


def _chamar(db, onde: str, fn: Callable[[], Any]) -> tuple[Any, bool]:
    """(resultado, falhou)."""
    try:
        return fn(), False
    except Exception as exc:  # noqa: BLE001 - isolamento por fonte e' o contrato
        _registrar_falha(onde, exc)
        _desfazer(db)
        return None, True


def _erro(codigo: str) -> Indicador:
    return Indicador(codigo=codigo, **META[codigo], estado="erro",
                     motivo_indisponivel=MOTIVO_ERRO)


def _indicador(codigo: str, dinamico: Callable[[], dict]) -> Indicador:
    """Monta o indicador; qualquer falha (inclusive de validacao) vira `erro`."""
    try:
        return Indicador(codigo=codigo, **{**META[codigo], **dinamico()})
    except Exception as exc:  # noqa: BLE001
        _registrar_falha(codigo, exc)
        return _erro(codigo)


# ---------------------------------------------------------------------------
# Utilitarios
# ---------------------------------------------------------------------------
def _data(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _idade_horas(instante: Optional[datetime], agora: datetime) -> Optional[float]:
    if instante is None:
        return None
    if isinstance(instante, str):
        instante = datetime.fromisoformat(instante)
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=timezone.utc)
    return (agora - instante).total_seconds() / 3600.0


def _fracao_para_pct(v: Any) -> float:
    return round(float(v) * 100, 2)


# ---------------------------------------------------------------------------
# Mercado Livre
# ---------------------------------------------------------------------------
def _ml_quality(codigo: str, campo: str, res: Optional[dict], falhou: bool,
                janela: str) -> Indicador:
    if falhou:
        return _erro(codigo)

    def _dinamico() -> dict:
        valor = res["kpis"][campo]
        marcas = [str(r["brand"]) for r in res.get("brands") or []
                  if r.get("ml_total_orders")]
        comum = {"janela": janela, "atualizado_em": res.get("refreshed_at"),
                 "cobertura": ", ".join(marcas) or None}
        if valor is None:
            # A fonte respondeu e nao havia base: `get_quality` devolve None
            # justamente quando o denominador e' zero.
            return {**comum, "estado": "vazio_legitimo",
                    "motivo_indisponivel": f"Nenhum pedido Mercado Livre em {janela}."}
        return {**comum, "estado": "dado", "valor": float(valor)}

    return _indicador(codigo, _dinamico)


def _tem_nao_entregue(linha: dict) -> bool:
    # So' a contagem: e' ela que entra no numerador recalculado.
    return linha.get("ml_not_delivered_shipments") is not None


def _pedidos_pagos(linha: dict) -> int:
    # Mesma definicao do agregado de `get_quality`: pedidos - cancelados.
    return int(linha.get("ml_total_orders") or 0) - int(linha.get("ml_cancelled_orders") or 0)


def _ml_nao_entregue(res: Optional[dict], falhou: bool, janela: str) -> Indicador:
    """Nao entregue sem falso zero e sem denominador maior que o numerador.

    O agregado de `get_quality` soma `ml_not_delivered_shipments or 0`: marca
    sem o dado (nenhuma entrega registrada) entra como 0 no numerador e com os
    pedidos no denominador. Por isso a taxa e' RECALCULADA aqui a partir das
    linhas por marca (sem SQL novo, sem mexer no servico da Gerencial), so'
    sobre as marcas com o dado, no numerador E no denominador:

        soma(ml_not_delivered_shipments) / soma(ml_total_orders - ml_cancelled_orders) * 100

    Com todas as marcas com dado, a formula e' a mesma do agregado do servico.
    """
    codigo = "ml_nao_entregue_torre"
    if falhou:
        return _erro(codigo)

    def _dinamico() -> dict:
        com_pedidos = [r for r in res.get("brands") or [] if r.get("ml_total_orders")]
        com_dado = [r for r in com_pedidos if _tem_nao_entregue(r)]
        sem_dado = [str(r["brand"]) for r in com_pedidos if not _tem_nao_entregue(r)]
        comum = {"janela": janela, "atualizado_em": res.get("refreshed_at"),
                 "cobertura": ", ".join(str(r["brand"]) for r in com_dado) or None}
        if not com_pedidos:
            return {**comum, "estado": "vazio_legitimo",
                    "motivo_indisponivel": f"Nenhum pedido Mercado Livre em {janela}."}
        if not com_dado:
            return {**comum, "estado": "sem_fonte",
                    "motivo_indisponivel": MOTIVO_ML_NAO_ENTREGUE_AUSENTE}
        # int(None) levanta: contagem corrompida vira `erro`, nunca zero.
        numerador = sum(int(r["ml_not_delivered_shipments"]) for r in com_dado)
        denominador = sum(_pedidos_pagos(r) for r in com_dado)
        if denominador <= 0:
            return {**comum, "estado": "vazio_legitimo",
                    "motivo_indisponivel": "Nenhum pedido pago Mercado Livre nas "
                                           f"marcas com dado de não entregue em {janela}."}
        aviso = None
        if sem_dado:
            aviso = ("Dado de não entregue ausente na fonte para: "
                     + ", ".join(sem_dado)
                     + ". Essas marcas ficaram fora do numerador e do denominador; "
                       "a taxa cobre só as marcas listadas na cobertura.")
        return {**comum, "estado": "dado",
                "valor": round(numerador / denominador * 100, 2),
                "aviso_frescor": aviso}

    return _indicador(codigo, _dinamico)


def _ml_expedicao(res: Optional[dict], falhou: bool,
                  marcas_ml: Optional[list[str]]) -> Indicador:
    codigo = "ml_expedicao_48h_torre"
    if falhou:
        return _erro(codigo)
    limiar = Limiar(valor=48, origem="interno",
                    rotulo="48 h é limite interno, não SLA do ML")

    def _dinamico() -> dict:
        janela = "fotografia atual da fila (shipments abertos)"
        if res.get("availability") != "available":
            motivo = res.get("unavailable_reason")
            texto = MOTIVOS_EXPEDICAO.get(motivo)
            texto = f"{texto} ({motivo})" if texto else MOTIVO_EXPEDICAO_GENERICO
            return {"estado": "sem_fonte", "janela": janela, "limiar": limiar,
                    "motivo_indisponivel": texto}

        contas = list(res.get("accounts") or [])
        if marcas_ml is not None:
            contas = [c for c in contas if str(c["brand"]) in marcas_ml]
        if not contas:
            return {"estado": "sem_fonte", "janela": janela, "limiar": limiar,
                    "motivo_indisponivel": "Nenhuma conta das marcas selecionadas "
                                           "na fotografia de expedição."}
        # int(None) levanta: contagem ausente vira `erro`, nunca zero.
        valor = sum(int(c["over_48h_count"]) for c in contas)
        marcas = sorted({str(c["brand"]) for c in contas})

        avisos = []
        velhas = [f"{f['brand']} ({f['freshness']})" for f in res.get("freshness") or []
                  if str(f.get("brand")) in marcas and f.get("freshness") != "fresh"]
        if velhas:
            avisos.append("Fonte desatualizada em: " + ", ".join(velhas) + ".")
        idade = (res.get("limitations") or {}).get("snapshot_age_hours")
        # So' a idade, sinal explicito da fonte. Idade NAO prova ausencia de
        # agendamento (a Expedicao e' agendada): a causa nao e' inferida.
        if idade is not None and idade > LIMITE_FRESCOR_HORAS:
            avisos.append(f"Fotografia de há {idade:.0f} h (acima do limite de "
                          f"{LIMITE_FRESCOR_HORAS:.0f} h).")

        snapshot = res.get("snapshot") or {}
        return {"estado": "dado", "valor": float(valor), "janela": janela,
                "limiar": limiar, "atualizado_em": snapshot.get("effective_at"),
                "aviso_frescor": " ".join(avisos) or None,
                "cobertura": ", ".join(marcas)}

    return _indicador(codigo, _dinamico)


def _canal_ml(db, selecionadas: list[str], filtrado: bool, hoje: date,
              agora: datetime) -> CanalResposta:
    marcas = [b for b in selecionadas if b in ML_BRANDS]
    fora = [MarcaFora(marca=b, motivo=MOTIVO_FORA_ML)
            for b in selecionadas if b not in ML_BRANDS]
    oficial = Indicador(codigo="ml_reputacao_oficial", **META["ml_reputacao_oficial"],
                        estado="sem_fonte", motivo_indisponivel=TEXTO_REPUTACAO_ML)

    if filtrado and not marcas:
        torre = [
            Indicador(codigo=c, **META[c], estado="sem_fonte",
                      motivo_indisponivel=MOTIVO_ML_SEM_MARCA)
            for c in ("ml_cancelamento_torre", "ml_nao_entregue_torre",
                      "ml_expedicao_48h_torre")
        ]
        return CanalResposta(canal="mercado_livre",
                             cobertura=Cobertura(marcas=marcas, fora=fora),
                             indicadores=[oficial, *torre])

    brand_keys = marcas if filtrado else None
    periodo = EffectivePeriod(start=hoje - timedelta(days=JANELA_ML_DIAS),
                              end=hoje - timedelta(days=1))
    janela = (f"últimos {JANELA_ML_DIAS} dias fechados "
              f"({_data(periodo.start)} a {_data(periodo.end)})")
    qualidade, q_falhou = _chamar(db, "ml_quality", lambda: performance_service.get_quality(
        db, "ml", periodo.start.year, periodo.start.month,
        brand_keys=brand_keys, period=periodo, compare_period=None,
    ))
    expedicao, e_falhou = _chamar(db, "ml_expedicao", lambda: expedicao_service.get_expedicao(
        db, channel="mercadolivre", include_queue=False,
    ))
    indicadores = [
        oficial,
        _ml_quality("ml_cancelamento_torre", "ml_cancel_rate_pct",
                    qualidade, q_falhou, janela),
        _ml_nao_entregue(qualidade, q_falhou, janela),
        _ml_expedicao(expedicao, e_falhou, brand_keys),
    ]
    return CanalResposta(canal="mercado_livre",
                         cobertura=Cobertura(marcas=marcas, fora=fora),
                         indicadores=indicadores)


# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------
def _shopee_ldr(res: Optional[dict], falhou: bool) -> Indicador:
    codigo = "shopee_ldr_operacional_torre"
    if falhou:
        return _erro(codigo)

    def _dinamico() -> dict:
        padrao = (f"{expedicao_shopee_ldr_service.JANELA_PADRAO_DIAS} dias de "
                  f"vencimento antes de hoje")
        if res.get("availability") != "available":
            motivo = res.get("unavailable_reason")
            texto = (TEXTO_LDR_SHOPEE_NAO_PUBLICADA if motivo in NAO_PUBLICADA_SHOPEE
                     else "LDR operacional da Shopee indisponível.")
            return {"estado": "sem_fonte", "janela": padrao, "motivo_indisponivel": texto}

        ldr = res["ldr"]
        snapshot = res.get("snapshot") or {}
        janela = (f"vencimentos de {_data(ldr['from'])} a {_data(ldr['to'])}, "
                  f"só vencimentos maduros")
        comum = {
            "janela": janela,
            "atualizado_em": snapshot.get("effective_at"),
            "aviso_frescor": ("Fonte vencida: publicação ou leitura da fonte acima de "
                              f"{LIMITE_FRESCOR_HORAS:.0f} h; o número não é atual."
                              if snapshot.get("stale") else None),
            "cobertura": ", ".join(str(b["brand"]) for b in res.get("brands") or []) or None,
        }
        if ldr.get("rate") is None:
            if ldr.get("rate_hidden_reason") == "coverage_insufficient":
                return {**comum, "estado": "sem_fonte",
                        "motivo_indisponivel": "Taxa ocultada: cobertura insuficiente "
                                               "(mais de 10% da base sem prazo ou sem "
                                               "marco de coleta)."}
            if int(ldr.get("base") or 0) == 0:
                return {**comum, "estado": "vazio_legitimo",
                        "motivo_indisponivel": f"Nenhum vencimento maduro em {janela}."}
            return {**comum, "estado": "sem_fonte",
                    "motivo_indisponivel": "Taxa não calculada pela fonte."}
        return {**comum, "estado": "dado", "valor": _fracao_para_pct(ldr["rate"])}

    return _indicador(codigo, _dinamico)


def _canal_shopee(db, selecionadas: list[str], filtrado: bool,
                  agora: datetime) -> CanalResposta:
    sem_app = set(expedicao_service.MARCAS_SEM_COBERTURA)
    marcas = [b for b in selecionadas if b in SHOPEE_BRANDS and b not in sem_app]
    fora = [MarcaFora(marca=b, motivo=MOTIVO_FORA_SHOPEE)
            for b in selecionadas if b in sem_app]
    oficial = Indicador(codigo="shopee_account_health_oficial",
                        **META["shopee_account_health_oficial"],
                        estado="nao_comprovado", motivo_indisponivel=TEXTO_HEALTH_SHOPEE)

    if filtrado and not marcas:
        ldr = Indicador(codigo="shopee_ldr_operacional_torre",
                        **META["shopee_ldr_operacional_torre"], estado="sem_fonte",
                        motivo_indisponivel=MOTIVO_SHOPEE_SEM_MARCA)
    else:
        res, falhou = _chamar(db, "shopee_ldr", lambda: expedicao_shopee_ldr_service.obter_ldr(
            db, dias=expedicao_shopee_ldr_service.JANELA_PADRAO_DIAS,
            brands=marcas if filtrado else None, agora=agora,
        ))
        ldr = _shopee_ldr(res, falhou)
    return CanalResposta(canal="shopee", cobertura=Cobertura(marcas=marcas, fora=fora),
                         indicadores=[oficial, ldr])


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------
def _tiktok_ldr(res: Optional[dict], falhou: bool, selecionadas: list[str],
                filtrado: bool, agora: datetime) -> Indicador:
    codigo = "tiktok_ldr_operacional_torre"
    if falhou:
        return _erro(codigo)

    def _dinamico() -> dict:
        alvo = (res.get("targets") or {}).get("tiktok_ldr")
        # A referencia de 4% nao tem fonte oficial: entra SEMPRE como interna.
        limiar = (Limiar(valor=_fracao_para_pct(alvo), origem="interno",
                         rotulo="referência interna, sem fonte oficial")
                  if alvo is not None else None)
        # Ausente conta como reconstruido: afirmar prazo oficial sem a fonte
        # dizer seria pior que a ressalva a mais.
        ajuda = (AJUDA_TIKTOK_OFICIAL if res.get("deadline_is_reconstructed") is False
                 else AJUDA_TIKTOK_RECONSTRUIDO)
        padrao = (f"{expedicao_tiktok_service.JANELA_PADRAO_DIAS} dias de vencimento "
                  f"antes de hoje")
        if res.get("availability") != "available":
            motivo = res.get("unavailable_reason")
            texto = (TEXTO_LDR_TIKTOK_NAO_PUBLICADA if motivo in NAO_PUBLICADA_TIKTOK
                     else "LDR operacional do TikTok indisponível.")
            return {"estado": "sem_fonte", "janela": padrao, "limiar": limiar,
                    "ajuda": ajuda, "motivo_indisponivel": texto}

        ldr = res["ldr"]
        snapshot = res.get("snapshot") or {}
        efetivo = snapshot.get("effective_at")
        idade = _idade_horas(efetivo, agora)
        janela = (f"vencimentos de {_data(ldr['from'])} a {_data(ldr['to'])}, "
                  f"só coortes maduras")
        comum = {
            "janela": janela, "limiar": limiar, "ajuda": ajuda,
            "atualizado_em": efetivo,
            "aviso_frescor": (f"Última publicação há {idade:.0f} h (o refresh roda a "
                              f"cada 2 h)." if idade is not None
                              and idade > LIMITE_FRESCOR_HORAS else None),
            "cobertura": (", ".join(selecionadas) if filtrado
                          else "todas as marcas publicadas na série do TikTok"),
        }
        if ldr.get("rate") is None:
            return {**comum, "estado": "vazio_legitimo",
                    "motivo_indisponivel": f"Nenhum vencimento maduro com base em {janela}."}
        return {**comum, "estado": "dado", "valor": _fracao_para_pct(ldr["rate"])}

    return _indicador(codigo, _dinamico)


def _canal_tiktok(db, selecionadas: list[str], filtrado: bool,
                  agora: datetime) -> CanalResposta:
    oficial = Indicador(codigo="tiktok_sps_ahr_oficial", **META["tiktok_sps_ahr_oficial"],
                        estado="sem_fonte", motivo_indisponivel=TEXTO_SPS_TIKTOK)
    res, falhou = _chamar(db, "tiktok_ldr", lambda: expedicao_tiktok_service.obter_serie(
        db, evento=expedicao_tiktok_service.EVENTO_PADRAO,
        dias=expedicao_tiktok_service.JANELA_PADRAO_DIAS,
        brands=selecionadas if filtrado else None, agora=agora,
    ))
    # Cobertura do canal: so' o escopo filtrado. Exclusoes do TikTok (ex.: marca
    # fora da LDR) nao sao derivaveis das constantes da API e nao sao inventadas.
    return CanalResposta(canal="tiktok",
                         cobertura=Cobertura(marcas=list(selecionadas), fora=[]),
                         indicadores=[oficial, _tiktok_ldr(res, falhou, selecionadas,
                                                           filtrado, agora)])


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def montar_performance(
    db,
    *,
    brands: Optional[list[str]] = None,
    agora: Optional[datetime] = None,
) -> PerformanceContasResponse:
    """Resposta completa, canais na ordem fixa `ORDEM_CANAIS`.

    `brands` ja' chega validado pela borda contra `MARCAS_PERMITIDAS`; `None`
    significa sem filtro.
    """
    agora = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hoje = agora.astimezone(APP_TIMEZONE).date()
    filtrado = brands is not None
    pedidas = set(brands or ())
    selecionadas = [b for b in MARCAS_PERMITIDAS if not filtrado or b in pedidas]

    canais = {
        "mercado_livre": _canal_ml(db, selecionadas, filtrado, hoje, agora),
        "shopee": _canal_shopee(db, selecionadas, filtrado, agora),
        "tiktok": _canal_tiktok(db, selecionadas, filtrado, agora),
    }
    return PerformanceContasResponse(gerado_em=agora,
                                     canais=[canais[c] for c in ORDEM_CANAIS])
