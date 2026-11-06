"""CX-KOKESHI-BOT — o JSON que o bot monta no Seller Centre vira linhas do Neon.

A Kokeshi nao tem app na Shopee Open Platform. Para as abas CX e Performance
da Torre ela precisa das MESMAS informacoes que o Airflow grava para as outras
quatro lojas (Saude da Conta, chat, devolucoes). O bot le essas telas no
Seller Centre e escreve um JSON (`docs/cx_kokeshi_bot_runbook.md`); este
modulo valida o JSON e devolve as linhas no formato das tabelas da 036.

Funcoes puras (sem banco): `ler_payload` devolve `Leitura` ou levanta
`PayloadInvalido` com TODOS os problemas de uma vez (o bot corrige tudo numa
rodada so').

REGRAS
------
* Ausencia nunca vira zero: campo que o bot nao mandou fica NULL.
* As metas da Saude da Conta vem do CATALOGO (as mesmas que a Shopee devolve
  pela API para as outras lojas em 04/11/2026). O bot so' manda o valor; se o
  Seller Centre mostrar outra meta, ele pode mandar `meta`.
* Status e motivo das devolucoes chegam em portugues, como aparecem na tela,
  e sao traduzidos para os codigos da API (`raw.shopee_returns`). Motivo fora
  do mapa e' guardado com o texto original: aparece na tela "sem traducao",
  nunca some.
* Nenhum dado do comprador: so' ids da devolucao e do pedido, datas, status,
  motivo e valor.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

BRT = timezone(timedelta(hours=-3))

#: codigo do bot -> (metric_id, metric_name, metric_type, parent, unit, meta, comparador, rotulo no Seller Centre)
#: metric_type: 1 Envio, 2 Anuncios, 3 Atendimento. unit: 1 numero, 2 %, 4 dias.
CATALOGO: dict[str, tuple[int, str, int, int, int, float, str, str]] = {
    "taxa_envio_atrasado": (1, "late_shipment_rate", 1, 0, 2, 5, "<", "Taxa de envio atrasado"),
    "taxa_nao_cumprimento": (3, "non_fulfillment_rate", 1, 0, 2, 8, "<", "Taxa de pedidos não concluídos"),
    "taxa_cancelamento": (42, "cancellation_rate", 1, 3, 2, 8, "<", "Taxa de cancelamento"),
    "taxa_devolucao_reembolso": (43, "return_refund_rate", 1, 3, 2, 8, "<", "Taxa de devolução/reembolso"),
    "envios_aos_sabados": (2032, "saturday_shipment_rate", 1, 0, 2, 20, ">=", "Envios aos sábados"),
    "tempo_medio_preparacao_dias": (2033, "avg_preparation_time_ps", 1, 0, 4, 1.3, "<", "Tempo médio de preparação"),
    "taxa_entrega_no_prazo": (2037, "otdr_dd_rate", 1, 0, 2, 75, ">=", "Taxa de entrega no prazo"),
    "anuncios_pre_venda_pct": (12, "pre_order_listing_rate", 2, 0, 2, 20, "<=", "% de anúncios em pré-venda"),
    "violacoes_graves": (52, "severe_listing_violations", 2, 0, 1, 0, "<=", "Violações graves de anúncio"),
    "outras_violacoes": (53, "other_listing_violations", 2, 0, 1, 0, "<=", "Outras violações de anúncio"),
    "taxa_resposta_chat": (11, "response_rate", 3, 0, 2, 60, ">=", "Taxa de resposta do chat"),
    "nota_loja": (22, "shop_rating", 3, 0, 1, 4.5, ">=", "Avaliação da loja"),
}

#: Nivel da loja na Saude da Conta (normalizado) -> nota 1-4 (`overall_rating`).
NIVEIS = {"excelente": 4, "bom": 3, "precisa melhorar": 2, "ruim": 1}

STATUS_API = ("ACCEPTED", "CANCELLED", "PROCESSING", "REQUESTED", "JUDGING", "SELLER_DISPUTE", "CLOSED")
#: (trecho do status em portugues, normalizado) -> codigo da API. Ordem importa.
STATUS_PT: tuple[tuple[str, str], ...] = (
    ("cancel", "CANCELLED"),
    ("disputa", "JUDGING"),
    ("analise", "JUDGING"),
    ("revisao", "JUDGING"),
    ("conclu", "ACCEPTED"),
    ("reembolsad", "ACCEPTED"),
    ("reembolso realizado", "ACCEPTED"),
    ("aprovad", "ACCEPTED"),
    ("aceit", "ACCEPTED"),
    ("fechad", "CLOSED"),
    ("encerrad", "CLOSED"),
    ("solicitad", "REQUESTED"),
    ("pendente", "REQUESTED"),
    ("aguardando", "PROCESSING"),
    ("processando", "PROCESSING"),
    ("em andamento", "PROCESSING"),
    ("devolucao em transito", "PROCESSING"),
)

#: (trecho do motivo em portugues, normalizado) -> codigo de `raw.shopee_returns.reason`.
MOTIVO_PT: tuple[tuple[str, str], ...] = (
    ("diferente da descricao", "DIFFERENT_DESCRIPTION"),
    ("diferente do anuncio", "DIFFERENT_DESCRIPTION"),
    ("nao corresponde", "DIFFERENT_DESCRIPTION"),
    ("nao receb", "NOT_RECEIPT"),
    ("nao cheg", "NOT_RECEIPT"),
    ("vazad", "SPILLED_CONTENTS"),
    ("vazou", "SPILLED_CONTENTS"),
    ("derram", "SPILLED_CONTENTS"),
    ("vencid", "EXPIRED_PRODUCT"),
    ("validade", "EXPIRED_PRODUCT"),
    ("falsific", "ITEM_FAKE"),
    ("falso", "ITEM_FAKE"),
    ("defeito", "FUNCTIONAL_DMG"),
    ("nao funciona", "FUNCTIONAL_DMG"),
    ("danific", "DAMAGED_OTHERS"),
    ("quebrad", "DAMAGED_OTHERS"),
    ("avariad", "DAMAGED_OTHERS"),
    ("faltando", "ITEM_MISSING"),
    ("faltante", "ITEM_MISSING"),
    ("incomplet", "ITEM_MISSING"),
    ("errad", "WRONG_ITEM"),
    ("usado", "USED_PRODUCT"),
    ("mudei de ideia", "CHANGE_MIND"),
    ("mudou de ideia", "CHANGE_MIND"),
    ("nao quero mais", "CHANGE_MIND"),
    ("desist", "CHANGE_MIND"),
    ("arrepend", "CHANGE_MIND"),
    ("acordo", "MUTUAL_AGREE"),
)

MAX_DEVOLUCOES = 20_000


class PayloadInvalido(ValueError):
    def __init__(self, problemas: list[str]):
        super().__init__("; ".join(problemas))
        self.problemas = problemas


@dataclass
class Leitura:
    conta: str
    capturado_em: datetime
    sha256: str
    saude: Optional[dict] = None
    metricas: list[dict] = field(default_factory=list)
    chat: Optional[dict] = None
    devolucoes: list[dict] = field(default_factory=list)
    #: status/motivos que o mapa nao conhece (para o relatorio)
    status_sem_mapa: list[str] = field(default_factory=list)
    motivos_sem_mapa: list[str] = field(default_factory=list)


def normalizar(texto: Any) -> str:
    s = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _instante(v: Any) -> Optional[datetime]:
    """ISO com fuso, `AAAA-MM-DD HH:MM` ou `DD/MM/AAAA HH:MM` (sem fuso = Brasilia)."""
    if v is None or (isinstance(v, str) and v.strip() in ("", "-")):
        return None
    s = str(v).strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=BRT)
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"data fora do formato: {s!r}") from exc
    return d if d.tzinfo else d.replace(tzinfo=BRT)


def _numero(v: Any, nome: str, problemas: list[str], *, inteiro=False, minimo=0.0,
            maximo: Optional[float] = None) -> Optional[float]:
    if v is None or (isinstance(v, str) and v.strip() in ("", "-")):
        return None
    if isinstance(v, bool):
        problemas.append(f"{nome}: booleano não é número")
        return None
    if isinstance(v, str):
        bruto = v.strip().replace("%", "").replace("R$", "").strip()
        # "1.234,56" (pt-BR) ou "1234.56"
        if "," in bruto:
            bruto = bruto.replace(".", "").replace(",", ".")
        v = bruto
    try:
        n = float(v)
    except (TypeError, ValueError):
        problemas.append(f"{nome}: {v!r} não é número")
        return None
    if n != n or n < minimo or (maximo is not None and n > maximo):
        problemas.append(f"{nome}: {n:g} fora do intervalo")
        return None
    if inteiro:
        if n != int(n):
            problemas.append(f"{nome}: {n:g} deveria ser inteiro")
            return None
        return int(n)
    return n


def _situacao_fora(valor: Optional[float], meta: float, comp: str) -> bool:
    if valor is None:
        return False
    return not {"<": valor < meta, "<=": valor <= meta, ">": valor > meta,
                ">=": valor >= meta}[comp]


def _saude(bruto: Any, capturado: datetime, problemas: list[str]) -> tuple[Optional[dict], list[dict]]:
    if bruto is None:
        return None, []
    if not isinstance(bruto, dict):
        problemas.append("saude_conta: deveria ser um objeto")
        return None, []
    nivel = bruto.get("nivel")
    nota: Optional[int] = None
    if isinstance(nivel, int) and not isinstance(nivel, bool) and 1 <= nivel <= 4:
        nota = nivel
    elif nivel is not None:
        nota = NIVEIS.get(normalizar(nivel))
        if nota is None:
            problemas.append(f"saude_conta.nivel: {nivel!r} (use Excelente, Bom, Precisa melhorar ou Ruim)")
    pontos = _numero(bruto.get("pontos_penalidade"), "saude_conta.pontos_penalidade", problemas, inteiro=True)
    punicoes = _numero(bruto.get("punicoes_em_andamento"), "saude_conta.punicoes_em_andamento", problemas, inteiro=True)

    met_bruto = bruto.get("metricas") or {}
    if not isinstance(met_bruto, dict):
        problemas.append("saude_conta.metricas: deveria ser um objeto {codigo: {valor, anterior}}")
        met_bruto = {}
    dia = capturado.astimezone(BRT).date()
    metricas, fora = [], {1: 0, 2: 0, 3: 0}
    for codigo, item in met_bruto.items():
        cat = CATALOGO.get(codigo)
        if cat is None:
            problemas.append(f"saude_conta.metricas.{codigo}: código desconhecido (veja a lista no runbook)")
            continue
        if not isinstance(item, dict):
            item = {"valor": item}
        mid, nome, tipo, pai, unidade, meta, comp, _rot = cat
        maximo = 100 if unidade == 2 else (5 if codigo == "nota_loja" else None)
        valor = _numero(item.get("valor"), f"{codigo}.valor", problemas, maximo=maximo)
        anterior = _numero(item.get("anterior"), f"{codigo}.anterior", problemas, maximo=maximo)
        meta_bot = _numero(item.get("meta"), f"{codigo}.meta", problemas, maximo=maximo)
        meta_final = meta if meta_bot is None else meta_bot
        if pai == 0 and _situacao_fora(valor, meta_final, comp):
            fora[tipo] += 1
        metricas.append({
            "metric_id": mid, "metric_name": nome, "metric_type": tipo, "parent_metric_id": pai,
            "unit": unidade, "current_period": valor, "last_period": anterior,
            "target_value": meta_final, "target_comparator": comp, "captured_date": dia,
        })
    if nota is None and not metricas and pontos is None and punicoes is None:
        problemas.append("saude_conta: veio vazia (mande nivel, metricas ou pontos)")
        return None, []
    saude = {
        "captured_date": dia, "captured_at": capturado, "overall_rating": nota,
        # sem nenhuma metrica do grupo = desconhecido (NULL), nunca "0 fora"
        "fulfillment_failed": fora[1] if any(m["metric_type"] == 1 for m in metricas) else None,
        "listing_failed": fora[2] if any(m["metric_type"] == 2 for m in metricas) else None,
        "custom_service_failed": fora[3] if any(m["metric_type"] == 3 for m in metricas) else None,
        "penalty_points": pontos, "ongoing_punishments": punicoes,
    }
    return saude, metricas


CAMPOS_CHAT = {
    # chave do bot: (coluna, inteiro, maximo)
    "nao_lidas": ("unread_count", True, None),
    "conversas_7d": ("conversas_7d", True, None),
    "conversas_30d": ("conversas_30d", True, None),
    "aguardando_resposta": ("aguardando_resposta", True, None),
    "periodo_dias": ("periodo_dias", True, 366),
    "visitantes": ("visitantes_chat", True, None),
    "taxa_resposta_pct": ("taxa_resposta_pct", False, 100),
    "csat_pct": ("csat_pct", False, 100),
    "conversao_pct": ("conversao_chat_pct", False, 100),
}


def _chat(bruto: Any, capturado: datetime, problemas: list[str]) -> Optional[dict]:
    if bruto is None:
        return None
    if not isinstance(bruto, dict):
        problemas.append("chat: deveria ser um objeto")
        return None
    linha: dict[str, Any] = {"captured_at": capturado, "conversas_truncado": None}
    for chave, (coluna, inteiro, maximo) in CAMPOS_CHAT.items():
        linha[coluna] = _numero(bruto.get(chave), f"chat.{chave}", problemas, inteiro=inteiro, maximo=maximo)
    minutos = _numero(bruto.get("tempo_resposta_medio_min"), "chat.tempo_resposta_medio_min", problemas)
    linha["tempo_resposta_medio_s"] = None if minutos is None else int(round(minutos * 60))
    desconhecidas = set(bruto) - set(CAMPOS_CHAT) - {"tempo_resposta_medio_min"}
    if desconhecidas:
        problemas.append(f"chat: campos desconhecidos {sorted(desconhecidas)}")
    if all(linha[c] is None for c, _, _ in CAMPOS_CHAT.values()) and linha["tempo_resposta_medio_s"] is None:
        problemas.append("chat: veio vazio")
        return None
    return linha


def traduzir_status(texto: Any) -> Optional[str]:
    bruto = str(texto or "").strip()
    if bruto.upper() in STATUS_API:
        return bruto.upper()
    n = normalizar(bruto)
    for trecho, codigo in STATUS_PT:
        if trecho in n:
            return codigo
    return None


def traduzir_motivo(texto: Any) -> Optional[str]:
    """Codigo da API, ou o proprio texto (cortado) quando o mapa nao conhece."""
    bruto = str(texto or "").strip()
    if not bruto or bruto == "-":
        return None
    n = normalizar(bruto)
    for trecho, codigo in MOTIVO_PT:
        if trecho in n:
            return codigo
    return bruto[:60]


def _devolucoes(bruto: Any, capturado: datetime, problemas: list[str], leitura: Leitura) -> list[dict]:
    if bruto is None:
        return []
    if not isinstance(bruto, list):
        problemas.append("devolucoes: deveria ser uma lista")
        return []
    if len(bruto) > MAX_DEVOLUCOES:
        problemas.append(f"devolucoes: {len(bruto)} linhas (máximo {MAX_DEVOLUCOES})")
        return []
    linhas: dict[str, dict] = {}
    for i, d in enumerate(bruto):
        rot = f"devolucoes[{i}]"
        if not isinstance(d, dict):
            problemas.append(f"{rot}: deveria ser um objeto")
            continue
        sn = str(d.get("id") or "").strip()
        if not sn:
            problemas.append(f"{rot}.id: obrigatório (número da solicitação de devolução)")
            continue
        status_txt = str(d.get("status") or "").strip()
        if not status_txt:
            problemas.append(f"{rot}.status: obrigatório")
            continue
        try:
            criada = _instante(d.get("criada_em"))
            atualizada = _instante(d.get("atualizada_em"))
        except ValueError as exc:
            problemas.append(f"{rot}: {exc}")
            continue
        status = traduzir_status(status_txt)
        if status is None:
            leitura.status_sem_mapa.append(status_txt)
        motivo_txt = str(d.get("motivo") or "").strip() or None
        motivo = traduzir_motivo(motivo_txt)
        if motivo_txt and motivo == motivo_txt[:60]:
            leitura.motivos_sem_mapa.append(motivo_txt)
        valor = _numero(d.get("valor_reembolso"), f"{rot}.valor_reembolso", problemas)
        linhas[sn] = {  # id repetido: fica a ultima linha
            "return_sn": sn[:64],
            "order_sn": (str(d.get("pedido") or "").strip()[:64] or None),
            "create_time": criada, "update_time": atualizada,
            "status": status, "status_export": status_txt[:80],
            "reason": motivo, "reason_export": (motivo_txt[:200] if motivo_txt else None),
            "refund_amount": None if valor is None else round(valor, 2),
            "currency": "BRL", "captured_at": capturado,
        }
    return list(linhas.values())


def ler_payload(conteudo: bytes, *, agora: datetime) -> Leitura:
    """Valida o JSON do bot. Levanta `PayloadInvalido` com todos os problemas."""
    try:
        dados = json.loads(conteudo.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadInvalido([f"JSON inválido: {type(exc).__name__}"]) from exc
    if not isinstance(dados, dict):
        raise PayloadInvalido(["o arquivo deveria ser um objeto JSON"])
    problemas: list[str] = []
    conta = str(dados.get("conta") or "").strip().lower()
    if not conta:
        problemas.append("conta: obrigatória (ex.: \"kokeshi\")")
    try:
        capturado = _instante(dados.get("capturado_em"))
    except ValueError as exc:
        capturado = None
        problemas.append(f"capturado_em: {exc}")
    if capturado is None and not any("capturado_em" in p for p in problemas):
        problemas.append("capturado_em: obrigatório (hora em que o bot leu as telas)")
    if capturado is not None and capturado > agora + timedelta(minutes=10):
        problemas.append("capturado_em: no futuro")
    if capturado is not None and capturado < agora - timedelta(days=3):
        problemas.append("capturado_em: mais de 3 dias atrás (leia as telas de novo)")
    secoes = {"saude_conta", "chat", "devolucoes"}
    desconhecidas = set(dados) - secoes - {"conta", "capturado_em"}
    if desconhecidas:
        problemas.append(f"seções desconhecidas: {sorted(desconhecidas)}")
    if not secoes & set(dados):
        problemas.append("nenhuma seção: mande saude_conta, chat e/ou devolucoes")
    if problemas:
        raise PayloadInvalido(problemas)

    leitura = Leitura(conta=conta, capturado_em=capturado, sha256=hashlib.sha256(conteudo).hexdigest())
    leitura.saude, leitura.metricas = _saude(dados.get("saude_conta"), capturado, problemas)
    leitura.chat = _chat(dados.get("chat"), capturado, problemas)
    leitura.devolucoes = _devolucoes(dados.get("devolucoes"), capturado, problemas, leitura)
    if problemas:
        raise PayloadInvalido(problemas)
    return leitura


def dia_brt(instante: datetime) -> date:
    return instante.astimezone(BRT).date()
