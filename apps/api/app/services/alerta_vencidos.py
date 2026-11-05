"""Alerta de VENCIDOS acima de 3% da fila da Shopee (EXPEDICAO-ALERTA-VENCIDOS-1).

Antes era um CLI em `pipelines/expedicao/alerta_vencidos.py`. Mudou para a API
porque o gatilho agora e' um cron do GoDeploy que chama uma rota daqui
(`app/routers/alertas.py`), sem passar pelo Airflow. A regra de negocio e' a
mesma.

O QUE MEDE
----------
A MESMA razao que a tabela por marca da aba Expedicao mostra como "% vencidos":
soma de `overdue_count` sobre soma de `backlog_count` do lote SERVIDO
(`marts.expedicao_refresh_run`, uma linha por conta). Nenhuma contagem nova.
So' a Shopee: o Mercado Livre nao publica prazo de despacho.

QUANDO AVISA
------------
* passou de 3% (a fotografia anterior estava em 3% ou menos, ou nao existe)
  -> "entrou";
* continua acima e, desde a fotografia anterior, o relogio de Brasilia passou
  por 9h, 13h ou 17h -> "lembrete" (no maximo tres por dia);
* voltou para 3% ou menos e a anterior estava acima -> "saiu", UMA vez;
* qualquer outro caso -> nada.

FOTOGRAFIA VELHA NAO AVISA
--------------------------
Mais velha que `MAX_IDADE_MIN` (60) nao e' avaliada. O valor era 45 quando o
alerta rodava logo depois da publicacao; com o cron a cada 15 min, a idade
maxima precisa ser maior ou igual ao atraso da publicacao somado a' cadencia,
senao uma fotografia pode passar inteira entre dois disparos.

UMA MENSAGEM POR FOTOGRAFIA
---------------------------
O cron avalia a mesma fotografia varias vezes nos 60 min dela. Quem impede a
repeticao e' o envio (`app/services/slack_dm.py`): cada DM leva a
`chave_da_fotografia` em `metadata`, e o envio pula quem ja' a recebeu. Nada e'
gravado no Neon.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import slack_blocks as sb
from app.services.expedicao_service import FILA, RUN

#: Limite de negocio: acima disto (estritamente) o canal entra em alerta.
LIMIAR_VENCIDOS = 0.03

#: Horas de Brasilia em que um alerta em curso e' lembrado.
HORAS_LEMBRETE = (9, 13, 17)

#: Fotografia mais velha que isto (minutos) nao e' avaliada (ver docstring).
MAX_IDADE_MIN = 60

CANAL = "shopee"

#: Teto da busca de hora de lembrete entre duas fotografias (2 dias de vao).
_MAX_HORAS_NO_VAO = 48

URL_DA_EXPEDICAO = "https://mktplace-gobeaute.vercel.app/expedicao"

ROTULO_CANAL = {"shopee": "Shopee"}
#: Marca sem entrada aqui sai em maiusculas, como na Torre.
ROTULO_MARCA = {"apice": "ÁPICE", "rituaria": "RITUÁRIA"}

#: Brasilia sem horario de verao desde 2019; evita depender de `tzdata`.
BRT = timezone(timedelta(hours=-3))

ENTROU = "entrou"
LEMBRETE = "lembrete"
SAIU = "saiu"

SEM_FOTOGRAFIA = "sem_fotografia"
FOTOGRAFIA_VELHA = "fotografia_velha"
SEM_EVENTO = "sem_evento"
EVENTO = "evento"


@dataclass(frozen=True)
class Medida:
    backlog: int
    vencidos: int

    @property
    def fracao(self) -> float | None:
        return self.vencidos / self.backlog if self.backlog > 0 else None

    @property
    def acima(self) -> bool:
        f = self.fracao
        return f is not None and f > LIMIAR_VENCIDOS


@dataclass(frozen=True)
class Leitura:
    canal: str
    effective_at: datetime
    #: Hora cheia (UTC) do resumo do lote servido.
    hora: datetime
    total: Medida
    por_marca: dict[str, Medida]
    #: Fotografia anterior do resumo; `None` quando o canal nao tem historico.
    anterior: Medida | None
    hora_anterior: datetime | None


@dataclass(frozen=True)
class Avaliacao:
    canal: str
    situacao: str
    evento: str | None
    mensagem: str | None
    chave: str | None
    fracao: float | None
    #: Blocos do Slack da mesma mensagem (`montar_blocos`); `None` sem evento.
    blocos: list[dict] | None = None


# ---------------------------------------------------------------------------
# Leitura (read-only)
# ---------------------------------------------------------------------------
def ler_canal(db: Session, canal: str) -> Leitura | None:
    """Le o lote servido e a fotografia anterior, no MESMO snapshot.

    Sem fotografia, ou com mais de um lote na fila, devolve `None`: a API
    tambem nao mostra nada nesse caso, e alertar sobre um estado que a tela
    nao exibe seria um alarme sem onde conferir.
    """
    # Primeira instrucao da transacao: as tres consultas veem o mesmo instante,
    # mesmo com a DAG republicando no meio, e nada aqui consegue escrever.
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    lotes = db.execute(
        text(f"SELECT DISTINCT refresh_batch_id, effective_at FROM {FILA} "
             "WHERE channel = :canal"),
        {"canal": canal},
    ).mappings().all()
    if len(lotes) != 1:
        return None
    lote = lotes[0]
    resumos = db.execute(
        text(f"SELECT brand, backlog_count, overdue_count, snapshot_hour FROM {RUN} "
             "WHERE channel = :canal AND refresh_batch_id = :lote"),
        {"canal": canal, "lote": lote["refresh_batch_id"]},
    ).mappings().all()
    if not resumos:
        return None
    hora = resumos[0]["snapshot_hour"]
    ant = db.execute(
        text("SELECT snapshot_hour, COUNT(*) AS contas, "
             "COALESCE(SUM(backlog_count), 0) AS backlog, "
             "COALESCE(SUM(overdue_count), 0) AS vencidos "
             f"FROM {RUN} WHERE channel = :canal AND snapshot_hour = ("
             f"  SELECT MAX(snapshot_hour) FROM {RUN} "
             "  WHERE channel = :canal AND snapshot_hour < :hora) "
             "GROUP BY snapshot_hour"),
        {"canal": canal, "hora": hora},
    ).mappings().first()

    por_marca: dict[str, list[int]] = {}
    for r in resumos:
        soma = por_marca.setdefault(r["brand"] or "sem marca", [0, 0])
        soma[0] += int(r["backlog_count"])
        soma[1] += int(r["overdue_count"] or 0)
    tem_anterior = bool(ant) and int(ant["contas"]) > 0
    return Leitura(
        canal=canal,
        effective_at=lote["effective_at"],
        hora=hora,
        total=Medida(
            backlog=sum(int(r["backlog_count"]) for r in resumos),
            vencidos=sum(int(r["overdue_count"] or 0) for r in resumos),
        ),
        por_marca={m: Medida(b, v) for m, (b, v) in por_marca.items()},
        anterior=Medida(int(ant["backlog"]), int(ant["vencidos"])) if tem_anterior else None,
        hora_anterior=ant["snapshot_hour"] if tem_anterior else None,
    )


# ---------------------------------------------------------------------------
# Decisao e mensagem (puras)
# ---------------------------------------------------------------------------
def passou_hora_de_lembrete(desde: datetime, ate: datetime) -> bool:
    """Alguma hora cheia de `HORAS_LEMBRETE` (Brasilia) em `(desde, ate]`?"""
    h = desde.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for _ in range(_MAX_HORAS_NO_VAO):
        if h > ate:
            return False
        if h.astimezone(BRT).hour in HORAS_LEMBRETE:
            return True
        h += timedelta(hours=1)
    return False


def decidir(leitura: Leitura) -> str | None:
    """`ENTROU`, `LEMBRETE`, `SAIU` ou `None` (ver docstring do modulo)."""
    anterior_acima = leitura.anterior is not None and leitura.anterior.acima
    if leitura.total.acima:
        if not anterior_acima:
            return ENTROU
        if passou_hora_de_lembrete(leitura.hora_anterior, leitura.hora):
            return LEMBRETE
        return None
    return SAIU if anterior_acima else None


def fotografia_velha(leitura: Leitura, agora: datetime, max_idade_min: int) -> bool:
    return agora - leitura.effective_at > timedelta(minutes=max_idade_min)


def chave_da_fotografia(leitura: Leitura) -> str:
    """Identidade da fotografia: canal + instante em UTC."""
    return f"{leitura.canal}:{leitura.effective_at.astimezone(timezone.utc).isoformat()}"


def _inteiro(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _pct(f: float) -> str:
    return f"{f * 100:.1f}".replace(".", ",") + "%"


def _rotulo_marca(marca: str) -> str:
    return ROTULO_MARCA.get(marca, marca.upper())


def montar_mensagem(leitura: Leitura, evento: str) -> str:
    canal = ROTULO_CANAL[leitura.canal]
    t = leitura.total
    pct = _pct(t.fracao or 0.0)
    limite = f"{LIMIAR_VENCIDOS:.0%}"
    quando = leitura.effective_at.astimezone(BRT).strftime("%d/%m às %H:%M")
    corpo = (
        f"{_inteiro(t.vencidos)} de {_inteiro(t.backlog)} pedidos da fila passaram do "
        f"prazo de despacho, segundo a fotografia de {quando}."
    )
    if evento == SAIU:
        linhas = [
            f":white_check_mark: *Expedição {canal}: vencidos voltaram a {pct} da fila* "
            f"(limite {limite})",
            corpo,
        ]
    else:
        titulo = (
            f":rotating_light: *Expedição {canal}: {pct} da fila vencida*"
            if evento == ENTROU
            else f":warning: *Expedição {canal}: continua com {pct} da fila vencida*"
        )
        linhas = [f"{titulo} (limite {limite})", corpo]
        marcas = sorted(
            ((m, x) for m, x in leitura.por_marca.items() if x.acima),
            key=lambda par: par[1].fracao or 0.0,
            reverse=True,
        )
        if marcas:
            linhas.append(
                f"Marcas acima de {limite}: "
                + " · ".join(
                    f"{_rotulo_marca(m)} {_pct(x.fracao or 0.0)} "
                    f"({_inteiro(x.vencidos)} de {_inteiro(x.backlog)})"
                    for m, x in marcas
                )
            )
    linhas.append(f"<{URL_DA_EXPEDICAO}|Abrir a Expedição na Torre>")
    return "\n".join(linhas)


def montar_blocos(leitura: Leitura, evento: str) -> list[dict]:
    """A mesma mensagem em blocos: titulo, numero, marcas e o botao da fila."""
    canal = ROTULO_CANAL[leitura.canal]
    t = leitura.total
    pct = _pct(t.fracao or 0.0)
    limite = f"{LIMIAR_VENCIDOS:.0%}"
    quando = leitura.effective_at.astimezone(BRT).strftime("%d/%m às %H:%M")
    if evento == SAIU:
        cab = f":white_check_mark: {canal}: vencidos voltaram a {pct} da fila"
    elif evento == ENTROU:
        cab = f":rotating_light: {canal}: {pct} da fila vencida"
    else:
        cab = f":warning: {canal}: continua com {pct} da fila vencida"
    blocos = [
        sb.titulo(cab),
        sb.nota(f"Limite {limite}  ·  fotografia de {quando}"),
        sb.secao(f"*{_inteiro(t.vencidos)}* de {_inteiro(t.backlog)} pedidos da fila passaram do "
                 f"prazo de despacho."),
    ]
    if evento != SAIU:
        marcas = sorted(((m, x) for m, x in leitura.por_marca.items() if x.vencidos > 0),
                        key=lambda par: par[1].fracao or 0.0, reverse=True)
        if marcas:
            linhas = [f"{':red_circle:' if x.acima else ':large_green_circle:'} "
                      f"*{_rotulo_marca(m)}*  {_pct(x.fracao or 0.0)}  ·  "
                      f"{_inteiro(x.vencidos)} de {_inteiro(x.backlog)}" for m, x in marcas]
            blocos.append(sb.secao("*Por marca*\n" + "\n".join(linhas)))
    blocos.append(sb.atalhos(sb.botao(f"Ver fila da {canal}",
                                      sb.url_expedicao(leitura.canal, detalhe=True), "primary")))
    return blocos


# ---------------------------------------------------------------------------
# Avaliacao (o que a rota chama)
# ---------------------------------------------------------------------------
def avaliar(db: Session, agora: datetime) -> Avaliacao:
    """Le a Shopee e decide. Nao envia nada: o envio e' da rota."""
    db.rollback()  # garante que o SET TRANSACTION abra a transacao
    try:
        leitura = ler_canal(db, CANAL)
    finally:
        db.rollback()
    if leitura is None:
        return Avaliacao(CANAL, SEM_FOTOGRAFIA, None, None, None, None)
    fracao = leitura.total.fracao
    if fotografia_velha(leitura, agora, MAX_IDADE_MIN):
        return Avaliacao(CANAL, FOTOGRAFIA_VELHA, None, None, None, fracao)
    evento = decidir(leitura)
    if evento is None:
        return Avaliacao(CANAL, SEM_EVENTO, None, None, None, fracao)
    return Avaliacao(
        CANAL, EVENTO, evento, montar_mensagem(leitura, evento),
        chave_da_fotografia(leitura), fracao, montar_blocos(leitura, evento),
    )
