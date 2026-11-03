"""Alerta no Slack quando os VENCIDOS passam de 3% da fila da Shopee (EXPEDICAO-ALERTA-VENCIDOS-1).

O QUE MEDE
----------
A MESMA razao que a tabela por marca da aba Expedicao mostra como "% vencidos":
soma de `overdue_count` sobre soma de `backlog_count` do lote SERVIDO
(`marts.expedicao_refresh_run`, uma linha por conta). Vencido e' o pedido que
passou do prazo de despacho da Shopee (`ship_by_date`, 23:59 de Brasilia) sem
sair. Nenhuma contagem nova: so' a divisao.

Substitui o alerta de travados (EXPEDICAO-ALERTA-TRAVADOS-1), que ficava acima
de 3% em toda leitura: travado diz que o pedido esta' parado, nao que o prazo
estourou. So' a Shopee por ora: o Mercado Livre nao publica prazo de despacho,
entao "vencido" nao existe la' e o canal precisa de regra propria.

Medido antes da decisao (26/10 a 03/11/2026, 98 fotografias): acima de 3% em
21, todas entre 28 e 30/10; maximo 4,6%; desde 31/10 entre 0,1% e 0,7%.

QUANDO AVISA (03/11/2026)
-------------------------
* passou de 3% (a fotografia anterior estava em 3% ou menos, ou nao existe)
  -> "entrou";
* continua acima e, desde a fotografia anterior, o relogio de Brasilia passou
  por 9h, 13h ou 17h -> "lembrete" (no maximo tres por dia);
* voltou para 3% ou menos e a anterior estava acima -> "saiu", UMA vez;
* qualquer outro caso -> nada.

"Passou pela hora" e nao "a fotografia e' daquela hora": com a DAG a cada 2 h
nas horas pares, 9h cairia sempre no vao. Este modulo nao grava estado: a
fotografia anterior vem do historico que o resumo ja' guarda.

FOTOGRAFIA VELHA NAO AVISA
--------------------------
Se a publicacao falhou, a fila servida continua a da hora anterior, e o
mesmo evento sairia de novo. Fotografia mais velha que `--max-idade-min`
(padrao 45) nao e' avaliada: a falha da publicacao e' outro alerta.

Uso:
    python -m pipelines.expedicao.alerta_vencidos            # mostra, nao envia
    python -m pipelines.expedicao.alerta_vencidos --enviar   # envia ao Slack

O envio e' uma DM do bot da Torre (`pipelines.common.slack_dm`) para cada
destinatario de `EXPEDICAO_ALERTA_DESTINATARIOS` (separados por virgula).
O token do bot vem de `SLACK_BOT_TOKEN` e nunca e' impresso. Sem retry
automatico: repetir manda a mensagem de novo.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from pipelines.common.slack_dm import SlackFalhou, enviar_dm
from pipelines.expedicao.contract import FILA_TABLE, RUN_TABLE, Channel

#: Limite de negocio: acima disto (estritamente) o canal entra em alerta.
LIMIAR_VENCIDOS = 0.03

#: Horas de Brasilia em que um alerta em curso e' lembrado.
HORAS_LEMBRETE = (9, 13, 17)

CANAIS = (Channel.SHOPEE,)

#: Fotografia mais velha que isto nao e' avaliada (ver docstring).
MAX_IDADE_PADRAO_MIN = 45

#: Teto da busca de hora de lembrete entre duas fotografias (2 dias de vao).
_MAX_HORAS_NO_VAO = 48

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_TARGET = "DATABASE_URL"
ENV_TOKEN = "SLACK_BOT_TOKEN"
ENV_DESTINATARIOS = "EXPEDICAO_ALERTA_DESTINATARIOS"
CONNECT_TIMEOUT_SECONDS = 20

URL_DA_EXPEDICAO = "https://mktplace-gobeaute.vercel.app/expedicao"

ROTULO_CANAL = {Channel.SHOPEE: "Shopee"}
#: Marca sem entrada aqui sai em maiusculas, como na Torre.
ROTULO_MARCA = {"apice": "ÁPICE", "rituaria": "RITUÁRIA"}

#: Brasilia sem horario de verao desde 2019; evita depender de `tzdata`.
BRT = timezone(timedelta(hours=-3))

EXIT_OK = 0
EXIT_FALHA = 1

ENTROU = "entrou"
LEMBRETE = "lembrete"
SAIU = "saiu"


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
    canal: Channel
    effective_at: datetime
    #: Hora cheia (UTC) do resumo do lote servido.
    hora: datetime
    total: Medida
    por_marca: dict[str, Medida]
    #: Fotografia anterior do resumo; `None` quando o canal nao tem historico.
    anterior: Medida | None
    hora_anterior: datetime | None


# ---------------------------------------------------------------------------
# Leitura (read-only)
# ---------------------------------------------------------------------------
def ler_canal(conn, canal: Channel) -> Leitura | None:
    """Le o lote servido e a fotografia anterior, no MESMO snapshot.

    Sem fotografia, ou com mais de um lote na fila, devolve `None`: a API
    tambem nao mostra nada nesse caso, e alertar sobre um estado que a tela
    nao exibe seria um alarme sem onde conferir.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT refresh_batch_id, effective_at FROM {FILA_TABLE} "
            "WHERE channel = %s",
            (canal.value,),
        )
        lotes = cur.fetchall()
        if len(lotes) != 1:
            return None
        lote = lotes[0]
        cur.execute(
            f"SELECT brand, backlog_count, overdue_count, snapshot_hour FROM {RUN_TABLE} "
            "WHERE channel = %s AND refresh_batch_id = %s",
            (canal.value, lote["refresh_batch_id"]),
        )
        resumos = cur.fetchall()
        if not resumos:
            return None
        hora = resumos[0]["snapshot_hour"]
        cur.execute(
            "SELECT snapshot_hour, COUNT(*) AS contas, "
            "COALESCE(SUM(backlog_count), 0) AS backlog, "
            "COALESCE(SUM(overdue_count), 0) AS vencidos "
            f"FROM {RUN_TABLE} WHERE channel = %s AND snapshot_hour = ("
            f"  SELECT MAX(snapshot_hour) FROM {RUN_TABLE} "
            "  WHERE channel = %s AND snapshot_hour < %s) "
            "GROUP BY snapshot_hour",
            (canal.value, canal.value, hora),
        )
        ant = cur.fetchone()

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


# ---------------------------------------------------------------------------
# Conexao
# ---------------------------------------------------------------------------
def abrir_neon():
    """Neon em sessao READ ONLY e REPEATABLE READ: as tres consultas de um
    canal veem o mesmo instante, mesmo com a DAG republicando no meio."""
    import os  # noqa: PLC0415

    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    url = os.environ.get(ENV_TARGET, "")
    if not url:
        raise RuntimeError(f"{ENV_TARGET} nao configurado.")
    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
    return conn


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="alerta_vencidos",
        description=(
            "Avisa no Slack quando os vencidos passam de 3% da fila da Expedicao "
            "da Shopee. Sem --enviar, so' mostra o que seria enviado."
        ),
    )
    p.add_argument(
        "--enviar", action="store_true",
        help=f"envia por DM no Slack (requer {ENV_TOKEN} e {ENV_DESTINATARIOS})",
    )
    p.add_argument(
        "--max-idade-min", type=int, default=MAX_IDADE_PADRAO_MIN,
        help="fotografia mais velha que isto (minutos) nao e' avaliada "
             f"(padrao {MAX_IDADE_PADRAO_MIN})",
    )
    return p


def main(
    argv: list[str] | None = None,
    *,
    abrir: Callable = abrir_neon,
    enviar: Callable[[str, list[str], str], None] = enviar_dm,
    agora: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    args = build_parser().parse_args(argv)

    import os  # noqa: PLC0415

    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))
    token = os.environ.get(ENV_TOKEN, "")
    destinatarios = [d.strip() for d in os.environ.get(ENV_DESTINATARIOS, "").split(",") if d.strip()]
    if args.enviar and not (token and destinatarios):
        print(f"{ENV_TOKEN} e {ENV_DESTINATARIOS} sao obrigatorios com --enviar.", file=sys.stderr)
        return EXIT_FALHA

    try:
        conn = abrir()
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI
        print(f"FALHA ao abrir o Neon: {type(exc).__name__}", file=sys.stderr)
        return EXIT_FALHA

    mensagens: list[str] = []
    try:
        for canal in CANAIS:
            try:
                leitura = ler_canal(conn, canal)
            finally:
                conn.rollback()
            if leitura is None:
                print(f"{canal.value}: sem fotografia servida, nada a avaliar.")
                continue
            if fotografia_velha(leitura, agora(), args.max_idade_min):
                print(
                    f"{canal.value}: fotografia de {leitura.effective_at.isoformat()} "
                    f"mais velha que {args.max_idade_min} min, nada a avaliar."
                )
                continue
            f = leitura.total.fracao
            evento = decidir(leitura)
            print(
                f"{canal.value}: {leitura.total.vencidos}/{leitura.total.backlog} vencidos"
                f" ({_pct(f) if f is not None else 'sem fila'}); "
                f"evento: {evento or 'nenhum'}"
            )
            if evento:
                mensagens.append(montar_mensagem(leitura, evento))
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI
        print(f"FALHA na leitura: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FALHA
    finally:
        conn.close()

    for texto in mensagens:
        if not args.enviar:
            print("--- mensagem (nao enviada, use --enviar) ---")
            print(texto)
            continue
        try:
            enviar(token, destinatarios, texto)
        except SlackFalhou as exc:
            print(f"FALHA no envio: {exc}", file=sys.stderr)
            return EXIT_FALHA
        print(f"mensagem enviada por DM a {len(destinatarios)} pessoa(s).")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
