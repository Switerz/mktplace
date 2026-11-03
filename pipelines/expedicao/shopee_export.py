"""Fila da Shopee a partir do export de pedidos do Seller Centre.

EXPEDICAO-KOKESHI-EXPORT. A Kokeshi nao tem app na Open Platform, entao nao
existe em `raw.shopee_orders`. O operador ja' baixa o export de pedidos do
Seller Centre; este modulo le esse arquivo e devolve, por pedido, as MESMAS
colunas que `shopee_extract.SHOPEE_BACKLOG_SQL` le do Data Mart. Dai' em diante
a classificacao e' a de sempre (`transform.build_fila_shopee`).

O QUE E' LIDO
-------------
So' as colunas de `COLUNAS`. O arquivo traz nome, telefone e endereco do
comprador; essas celulas nunca viram campo, nem em memoria: o indice das
colunas e' montado pela allowlist e o resto da linha e' ignorado. O numero de
rastreamento so' vira booleano (`has_tracking`).

STATUS
------
O export escreve o status em portugues e junta duas etapas em "A Enviar". O
operador mediu (03/11/2026, 881 pedidos de um dia inteiro, 100% de acerto):
com rastreio e' "Aguardando coleta" (`PROCESSED`), sem rastreio e' "A preparar"
(`READY_TO_SHIP`). Status fora de `STATUS_EXPORT` NAO bloqueia a carga: o
pedido e' guardado com `order_status` nulo, contado no relatorio, e fica fora
da fila e do baseline. A fila depende so' de "A Enviar"; travar a Kokeshi
inteira por um status raro de devolucao seria pior que conta-lo.

DATAS
-----
`AAAA-MM-DD HH:MM`, horario de Brasilia, sem fuso (mesmo formato que
`pipelines/staging/shopee/mapping.py` documenta). "-" e vazio sao ausencia.
"Tempo de Envio" e' a coleta pela transportadora nos enviados pelo vendedor e a
primeira chegada ao centro logistico nos Full — medido pelo operador nos dois
casos, no minuto exato. Vira `pickup_done_time`.

GRAO
----
Uma linha do arquivo e' um ITEM. O pedido junta os itens: o status tem de ser
o mesmo em todos (divergencia e' arquivo incoerente e para a carga); as datas
vem da primeira linha que as tiver.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pipelines.expedicao.shopee_extract import (
    BASELINE_EXCLUDED_STATUSES,
    SHOPEE_BACKLOG_STATUSES,
)
from pipelines.expedicao.transform import _percentil

FUSO_DO_EXPORT = ZoneInfo("America/Sao_Paulo")

#: Header EXATO do export -> chave interna. ALLOWLIST: coluna fora daqui nao e'
#: lida. Nenhuma coluna de comprador entra (o teste confere por intersecao).
COLUNAS = {
    "ID do pedido": "order_sn",
    "Status do pedido": "status",
    "Número de rastreamento": "tracking",
    "Opção de envio": "carrier",
    "Data de criação do pedido": "create_time",
    "Hora do pagamento do pedido": "pay_time",
    "Data prevista de envio": "ship_by_date",
    "Tempo de Envio": "pickup_done_time",
    "Pedido FBS": "fbs",
    "Cancelar Motivo": "cancel_reason",
}

#: O export da aba "A Enviar" nao traz "Cancelar Motivo" (65 colunas contra 66
#: do geral, medido pelo operador); "Pedido FBS" falta no template antigo.
OPCIONAIS = frozenset({"Pedido FBS", "Cancelar Motivo"})
OBRIGATORIAS = frozenset(COLUNAS) - OPCIONAIS

#: "A Enviar" (normalizado). Os demais status vao para `STATUS_EXPORT`.
STATUS_A_ENVIAR = "a enviar"

#: Status do export (normalizado: sem acento, minusculo) -> codigo da API.
STATUS_EXPORT = {
    "nao pago": "UNPAID",
    "enviado": "SHIPPED",
    "entregue": "TO_CONFIRM_RECEIVE",
    "concluido": "COMPLETED",
    "cancelado": "CANCELLED",
}

#: Codigo de "A Enviar", pela presenca de rastreio.
STATUS_COM_RASTREIO = "PROCESSED"
STATUS_SEM_RASTREIO = "READY_TO_SHIP"

#: Fulfillment, nos mesmos valores de `raw.shopee_orders.fulfillment_flag`.
FULFILLMENT = {"yes": "fulfilled_by_shopee", "no": "fulfilled_by_local_seller"}

#: Janela do baseline: a MESMA de `shopee_extract.SHOPEE_BASELINE_SQL`.
JANELA_BASELINE = timedelta(days=30)

#: Nome do arquivo como o Seller Centre o entrega. O sufixo ` (1)` e' o que o
#: navegador acrescenta a um segundo download com o mesmo nome.
_NOME_RE = re.compile(
    r"^Order\.(?P<tipo>all|toship)\.order_creation_date\."
    r"(?P<de>\d{8})_(?P<ate>\d{8})"
    r"(?:_part_(?P<parte>\d+)_of_(?P<partes>\d+))?"
    r"(?: \(\d+\))?\.xlsx$"
)
_DATA_HORA_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$")
_AUSENTE = {"", "-"}


class ExportInvalido(ValueError):
    """O arquivo nao serve para publicar a fila. A mensagem nunca cita pedido."""


@dataclass(frozen=True)
class ArquivoExport:
    caminho: Path
    tipo: str
    de: date
    ate: date
    parte: int
    partes: int


@dataclass
class LeituraExport:
    """O export inteiro (todas as partes), ja' no grao de pedido."""

    tipo: str
    de: date
    ate: date
    arquivos: list[dict]
    linhas: int
    pedidos: list[dict]
    #: Fim do download, em UTC: o `mtime` mais antigo entre as partes.
    ingerido_em: datetime
    status_desconhecidos: Counter = field(default_factory=Counter)
    avisos: list[str] = field(default_factory=list)


def normalizar_status(bruto) -> str:
    texto = unicodedata.normalize("NFKD", str(bruto or "").strip())
    return "".join(c for c in texto if not unicodedata.combining(c)).casefold()


def classificar_status(bruto, tem_rastreio: bool) -> str | None:
    """Codigo da API para o status do export; `None` se desconhecido."""
    chave = normalizar_status(bruto)
    if chave == STATUS_A_ENVIAR:
        return STATUS_COM_RASTREIO if tem_rastreio else STATUS_SEM_RASTREIO
    return STATUS_EXPORT.get(chave)


def ler_data_hora(bruto, rotulo: str) -> datetime | None:
    """`AAAA-MM-DD HH:MM` de Brasilia -> UTC aware. Formato novo PARA a carga."""
    if bruto is None:
        return None
    if isinstance(bruto, datetime):
        local = bruto if bruto.tzinfo else bruto.replace(tzinfo=FUSO_DO_EXPORT)
        return local.astimezone(timezone.utc)
    texto = str(bruto).strip()
    if texto in _AUSENTE:
        return None
    m = _DATA_HORA_RE.match(texto)
    if not m:
        raise ExportInvalido(
            f"coluna {rotulo!r} fora do formato AAAA-MM-DD HH:MM: o export mudou"
        )
    ano, mes, dia, hora, minuto, segundo = (int(g) if g else 0 for g in m.groups())
    try:
        local = datetime(ano, mes, dia, hora, minuto, segundo, tzinfo=FUSO_DO_EXPORT)
    except ValueError as exc:
        raise ExportInvalido(f"coluna {rotulo!r} com data impossivel") from exc
    return local.astimezone(timezone.utc)


def _texto(bruto) -> str | None:
    if bruto is None:
        return None
    texto = str(bruto).strip()
    return None if texto in _AUSENTE else texto


def identificar(caminho: Path) -> ArquivoExport:
    m = _NOME_RE.match(caminho.name)
    if not m:
        raise ExportInvalido(
            f"{caminho.name}: nome fora do padrao do Seller Centre "
            "(Order.all|toship.order_creation_date.AAAAMMDD_AAAAMMDD...xlsx); "
            "nao renomeie o download"
        )
    de = datetime.strptime(m["de"], "%Y%m%d").date()
    ate = datetime.strptime(m["ate"], "%Y%m%d").date()
    partes = int(m["partes"] or 1)
    return ArquivoExport(caminho, m["tipo"], de, ate, int(m["parte"] or 1), partes)


def agrupar(caminhos: list[Path]) -> list[ArquivoExport]:
    """Todas as partes de UM export, em ordem. Faltar parte para a carga."""
    if not caminhos:
        raise ExportInvalido("nenhum arquivo informado")
    arquivos = sorted((identificar(c) for c in caminhos), key=lambda a: a.parte)
    janelas = {(a.tipo, a.de, a.ate, a.partes) for a in arquivos}
    if len(janelas) != 1:
        raise ExportInvalido(
            "os arquivos sao de exports diferentes; passe so' as partes de um"
        )
    partes = arquivos[0].partes
    numeros = [a.parte for a in arquivos]
    if numeros != list(range(1, partes + 1)):
        raise ExportInvalido(
            f"partes do export incompletas ou repetidas: {numeros} de {partes}"
        )
    return arquivos


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _linhas_do_arquivo(caminho: Path):
    """(indice por chave, linhas) — so' as colunas da allowlist."""
    import openpyxl  # noqa: PLC0415

    try:
        livro = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ExportInvalido(f"{caminho.name}: nao abriu como xlsx") from exc
    try:
        folha = livro["orders"] if "orders" in livro.sheetnames else livro.active
        # Em `read_only` o openpyxl confia na dimensao DECLARADA pelo arquivo; um
        # export com dimensao errada ("A1:A1") viria so' com a primeira coluna.
        if hasattr(folha, "reset_dimensions"):
            folha.reset_dimensions()
        linhas = folha.iter_rows(values_only=True)
        cabecalho = next(linhas, None) or ()
        indice: dict[str, int] = {}
        for i, nome in enumerate(cabecalho):
            # Primeira ocorrencia vale: o export repete alguns nomes ("Cidade").
            chave = COLUNAS.get(str(nome).strip()) if nome is not None else None
            if chave and chave not in indice:
                indice[chave] = i
        faltando = sorted(n for n in OBRIGATORIAS if COLUNAS[n] not in indice)
        if faltando:
            raise ExportInvalido(f"{caminho.name}: colunas ausentes {faltando}")
        for linha in linhas:
            if linha is None or all(v is None or str(v).strip() == "" for v in linha):
                continue
            yield indice, {k: (linha[i] if i < len(linha) else None)
                           for k, i in indice.items()}
    finally:
        livro.close()


def _pedido_da_linha(bruto: dict, shop_account: str, shop_id: str,
                     ingerido_em: datetime, arquivo: str, sha: str) -> dict:
    tem_rastreio = _texto(bruto.get("tracking")) is not None
    fbs = normalizar_status(bruto.get("fbs"))
    return {
        "shop_account": shop_account,
        "order_sn": str(bruto["order_sn"]).strip(),
        "shop_id": shop_id,
        "order_status": classificar_status(bruto.get("status"), tem_rastreio),
        "status_export": str(bruto.get("status") or "").strip()[:60],
        "has_tracking": tem_rastreio,
        "create_time": ler_data_hora(bruto.get("create_time"), "Data de criação do pedido"),
        "pay_time": ler_data_hora(bruto.get("pay_time"), "Hora do pagamento do pedido"),
        "ship_by_date": ler_data_hora(bruto.get("ship_by_date"), "Data prevista de envio"),
        "pickup_done_time": ler_data_hora(bruto.get("pickup_done_time"), "Tempo de Envio"),
        "shipping_carrier": (_texto(bruto.get("carrier")) or None),
        "fulfillment_flag": FULFILLMENT.get(fbs),
        "cancel_reason": (_texto(bruto.get("cancel_reason")) or None),
        "ingested_at": ingerido_em,
        "source_file": arquivo[:300],
        "source_sha256": sha,
    }


_PREENCHIVEIS = ("create_time", "pay_time", "ship_by_date", "pickup_done_time",
                 "shipping_carrier", "fulfillment_flag", "cancel_reason")


def _juntar_item(pedido: dict, item: dict) -> None:
    """Item seguinte do MESMO pedido: status igual; campo vazio herda o do item."""
    if item["status_export"] != pedido["status_export"]:
        raise ExportInvalido(
            "pedido com status diferente entre os itens: export incoerente"
        )
    if item["has_tracking"] and not pedido["has_tracking"]:
        pedido["has_tracking"] = True
        pedido["order_status"] = classificar_status(pedido["status_export"], True)
    for campo in _PREENCHIVEIS:
        if pedido[campo] is None and item[campo] is not None:
            pedido[campo] = item[campo]


def ler_export(caminhos: list[Path], *, shop_account: str, shop_id: str,
               agora: datetime, max_idade: timedelta) -> LeituraExport:
    """Le as partes de UM export e devolve os pedidos, ou levanta.

    Recusa (`ExportInvalido`) o que nao pode virar a fila de AGORA: export que
    nao termina hoje (a fila de hoje ficaria de fora), download mais velho que
    `max_idade`, parte faltando, coluna obrigatoria ausente, data fora do
    formato ou pedido com status divergente entre os itens.
    """
    arquivos = agrupar(caminhos)
    primeiro = arquivos[0]
    hoje = agora.astimezone(FUSO_DO_EXPORT).date()
    if primeiro.ate != hoje:
        raise ExportInvalido(
            f"o export termina em {primeiro.ate:%d/%m/%Y}; a fila precisa do "
            f"dia de hoje ({hoje:%d/%m/%Y})"
        )
    if primeiro.de > primeiro.ate:
        raise ExportInvalido("periodo do export invertido no nome do arquivo")

    mtimes = [datetime.fromtimestamp(a.caminho.stat().st_mtime, tz=timezone.utc)
              for a in arquivos]
    ingerido_em = min(min(mtimes), agora)
    if agora - ingerido_em > max_idade:
        horas = (agora - ingerido_em).total_seconds() / 3600
        raise ExportInvalido(
            f"download de {horas:.1f} h atras (limite "
            f"{max_idade.total_seconds() / 3600:.0f} h): baixe o export de novo"
        )

    pedidos: dict[str, dict] = {}
    linhas = 0
    descritos = []
    for a in arquivos:
        sha = _sha256(a.caminho)
        descritos.append({"arquivo": a.caminho.name, "sha256": sha})
        for _indice, bruto in _linhas_do_arquivo(a.caminho):
            if _texto(bruto.get("order_sn")) is None:
                raise ExportInvalido(f"{a.caminho.name}: linha sem ID do pedido")
            linhas += 1
            item = _pedido_da_linha(bruto, shop_account, shop_id, ingerido_em,
                                    a.caminho.name, sha)
            atual = pedidos.get(item["order_sn"])
            if atual is None:
                pedidos[item["order_sn"]] = item
            else:
                _juntar_item(atual, item)

    leitura = LeituraExport(
        tipo=primeiro.tipo, de=primeiro.de, ate=primeiro.ate, arquivos=descritos,
        linhas=linhas, pedidos=list(pedidos.values()), ingerido_em=ingerido_em,
    )
    leitura.status_desconhecidos = Counter(
        p["status_export"] for p in leitura.pedidos if p["order_status"] is None)
    if leitura.tipo == "all" and (leitura.ate - leitura.de).days < 29:
        leitura.avisos.append(
            f"export de {(leitura.ate - leitura.de).days + 1} dia(s): o baseline "
            "de lentidao usa 30 dias e fica com amostra menor")
    if not any(normalizar_status(p["status_export"]) == STATUS_A_ENVIAR
               for p in leitura.pedidos) and leitura.pedidos:
        leitura.avisos.append(
            "nenhum pedido em 'A Enviar': confira se o export e' o de pedidos")
    return leitura


def linhas_de_backlog(pedidos: list[dict]) -> list[dict]:
    """Os pedidos da fila, na forma de `SHOPEE_BACKLOG_SQL`.

    MESMO filtro do Data Mart: status de backlog E sem coleta. Pedido em
    "A Enviar" que ja' tem "Tempo de Envio" saiu da operacao do vendedor.
    """
    return [
        {
            "shop_account": p["shop_account"],
            "order_sn": p["order_sn"],
            "shop_id": p["shop_id"],
            "order_status": p["order_status"],
            "create_time": p["create_time"],
            "pay_time": p["pay_time"],
            "ship_by_date": p["ship_by_date"],
            "pickup_done_time": p["pickup_done_time"],
            "shipping_carrier": p["shipping_carrier"],
            "ingested_at": p["ingested_at"],
        }
        for p in pedidos
        if p["order_status"] in SHOPEE_BACKLOG_STATUSES and p["pickup_done_time"] is None
    ]


def baseline(pedidos: list[dict], effective_at: datetime) -> tuple[int, float | None]:
    """`(amostra, p50 em horas)` ate' a coleta. Espelho de `SHOPEE_BASELINE_SQL`.

    Criados nos 30 dias anteriores a `effective_at`, com coleta e marco inicial
    (pagamento, senao criacao), status conhecido e fora de
    `BASELINE_EXCLUDED_STATUSES`. Status nulo fica fora como no SQL, onde
    `NOT (NULL = ANY(...))` e' nulo.
    """
    inicio = effective_at - JANELA_BASELINE
    horas = []
    for p in pedidos:
        marco = p["pay_time"] or p["create_time"]
        if (p["pickup_done_time"] is None or marco is None
                or p["order_status"] is None
                or p["order_status"] in BASELINE_EXCLUDED_STATUSES
                or p["create_time"] is None
                or not (inicio <= p["create_time"] < effective_at)):
            continue
        horas.append((p["pickup_done_time"] - marco).total_seconds() / 3600.0)
    return len(horas), _percentil(horas, 0.5)
