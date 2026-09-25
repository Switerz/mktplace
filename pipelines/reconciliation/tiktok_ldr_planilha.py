"""Reconcilia a LDR do TikTok publicada pela Torre com a planilha da gestao.

    python -m pipelines.reconciliation.tiktok_ldr_planilha

SOMENTE LEITURA. Nao escreve em lugar nenhum, nao publica e nao toca em flag.

O QUE ESTE SCRIPT EXISTE PARA RESPONDER
----------------------------------------
Todo numero de reconciliacao citado na migration 020, no contrato do frontend e
no handoff de ingestao sai DAQUI. Sem ele, aqueles numeros seriam precisao sem
origem: ninguem conseguiria refaze-los seis meses depois.

A referencia e' `docs/reconciliation/tiktok_ldr_planilha_gestor_2026-09-23.csv`,
transcrita de uma CAPTURA DE TELA enviada pelo gestor. Ela nao e' auditavel por
reexecucao — ver o `.md` ao lado. O que este script garante e' que a COMPARACAO
seja reproduzivel, nao que a referencia esteja certa.

AS TRES REGRAS DE PRAZO
------------------------
A politica diz "2 dias uteis", mas "util" admite leituras. O script recalcula a
serie da Torre sob as tres candidatas e mede qual reproduz a planilha:

    uteis_com_feriado   seg-sex, menos feriados nacionais   (a que a Torre usa)
    uteis_sem_feriado   seg-sex, feriado conta como util
    corridos            dois dias de calendario

Tudo o mais — exclusao de amostra, cancelado antes do SLA, `IN_TRANSIT` como
unica fonte do instante, RTS estrito — vem do MODULO de producao
(`pipelines.expedicao.tiktok_daily`), e nao de SQL copiado. Duplicar a
transformacao aqui faria a reconciliacao concordar consigo mesma em vez de com
o que a Torre publica.

RESOLUCAO DA COMPARACAO
------------------------
A planilha exibe a taxa ARREDONDADA (1%, 6%, 55%, 100%), sem casas decimais.
Logo a diferenca por dia nao pode ser reportada com precisao melhor que ~1 pp,
e este script nunca afirma mais que isso.
"""

from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pipelines.expedicao.tiktok_daily import (
    FERIADOS_NACIONAIS,
    PRAZO_DIAS_UTEIS,
    Evento,
    extrair,
    janela_de_pagamento,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCIA = (
    REPO_ROOT / "docs" / "reconciliation"
    / "tiktok_ldr_planilha_gestor_2026-09-23.csv"
)

#: A planilha e' de UMA marca. Descoberto procurando qual populacao reproduz o
#: total de 23.317 pedidos pagos — a carteira inteira tem ~173 mil no periodo.
MARCA = "barbours"

#: Instante em que a Torre leu a fonte para esta reconciliacao. A planilha foi
#: extraida ~24 h antes (ver o `.md`), e as colunas de PENDENTE divergem por
#: causa disso — nao por defeito.
HOJE_TORRE = date(2026, 9, 24)

#: A planilha arredonda a taxa para inteiro. Comparar com mais precisao que
#: isso seria inventar resolucao que a referencia nao tem.
RESOLUCAO_PP = 1.0

#: Acima disto a regra vigente deixou de reproduzir a planilha e a
#: reconciliacao FALHA — algum contrato mudou sem que ninguem percebesse.
#: Valor folgado de proposito: e' um alarme de regressao, nao uma meta.
ERRO_MEDIO_MAXIMO_PP = 1.5


# ---------------------------------------------------------------------------
# Regras de prazo candidatas
# ---------------------------------------------------------------------------
def _prazo(pago_em: date, dias: int, *, considerar_feriado: bool, corridos: bool) -> date:
    if corridos:
        return pago_em + timedelta(days=dias)
    d, restantes = pago_em, dias
    while restantes:
        d += timedelta(days=1)
        util = d.weekday() < 5 and (not considerar_feriado or d not in FERIADOS_NACIONAIS)
        if util:
            restantes -= 1
    return d


REGRAS = {
    "uteis_com_feriado": dict(considerar_feriado=True, corridos=False),
    "uteis_sem_feriado": dict(considerar_feriado=False, corridos=False),
    "corridos": dict(considerar_feriado=False, corridos=True),
}

#: A regra que a Torre usa em producao. As outras existem so' para comparacao.
REGRA_VIGENTE = "uteis_com_feriado"


def prazos_para(regra: str, hoje: date, dias: int) -> list[list[str]]:
    """Tabela [pago_em, vence_rts, vence_tts] sob uma das regras candidatas."""
    cfg = REGRAS[regra]
    desde, ate = janela_de_pagamento(hoje, dias)
    saida, d = [], desde
    while d <= ate:
        saida.append([
            d.isoformat(),
            _prazo(d, PRAZO_DIAS_UTEIS[Evento.DESPACHO], **cfg).isoformat(),
            _prazo(d, PRAZO_DIAS_UTEIS[Evento.COLETA], **cfg).isoformat(),
        ])
        d += timedelta(days=1)
    return saida


# ---------------------------------------------------------------------------
# Referencia versionada
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LinhaReferencia:
    paid_date: date
    paid_orders: int
    shipped_within_sla: int
    shipped_after_sla: int
    not_shipped_overdue: int
    not_shipped_on_time: int
    late_rate_pct_rounded: int

    def fecha(self) -> bool:
        return (
            self.shipped_within_sla + self.shipped_after_sla
            + self.not_shipped_overdue + self.not_shipped_on_time
        ) == self.paid_orders

    @property
    def atrasados(self) -> int:
        """Numerador da planilha: postados apos o SLA + vencidos sem postagem."""
        return self.shipped_after_sla + self.not_shipped_overdue


def carregar_referencia(caminho: Path = REFERENCIA) -> list[LinhaReferencia]:
    with caminho.open(encoding="utf-8", newline="") as fh:
        linhas = [
            LinhaReferencia(
                paid_date=date.fromisoformat(r["paid_date"]),
                paid_orders=int(r["paid_orders"]),
                shipped_within_sla=int(r["shipped_within_sla"]),
                shipped_after_sla=int(r["shipped_after_sla"]),
                not_shipped_overdue=int(r["not_shipped_overdue"]),
                not_shipped_on_time=int(r["not_shipped_on_time"]),
                late_rate_pct_rounded=int(r["late_rate_pct_rounded"]),
            )
            for r in csv.DictReader(fh)
        ]
    return sorted(linhas, key=lambda x: x.paid_date)


# ---------------------------------------------------------------------------
# Comparacao
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Diferenca:
    regra: str
    erro_acumulado_pp: float
    erro_medio_pp: float
    dias_comparados: int
    dias_dentro_da_resolucao: int


def comparar(referencia, torre_por_dia, regra: str) -> Diferenca:
    """Erro entre a taxa da planilha e a da Torre, dia a dia."""
    erros = []
    dentro = 0
    for ref in referencia:
        c = torre_por_dia.get(ref.paid_date)
        if c is None:
            continue
        taxa_torre = (c.taxa(Evento.COLETA) or 0.0) * 100.0
        erro = abs(taxa_torre - ref.late_rate_pct_rounded)
        erros.append(erro)
        if erro <= RESOLUCAO_PP:
            dentro += 1
    n = len(erros) or 1
    return Diferenca(
        regra=regra,
        erro_acumulado_pp=sum(erros),
        erro_medio_pp=sum(erros) / n,
        dias_comparados=len(erros),
        dias_dentro_da_resolucao=dentro,
    )


def _conectar():
    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    url = os.environ.get("DATAMART_DATABASE_URL", "")
    if not url:
        print("DATAMART_DATABASE_URL nao configurada.", file=sys.stderr)
        raise SystemExit(6)
    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=60,
        application_name="recon_tiktok_planilha",
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    referencia = carregar_referencia()
    print("RECONCILIACAO — LDR do TikTok x planilha da gestao")
    print(f"  referencia : {REFERENCIA.relative_to(REPO_ROOT).as_posix()}")
    print(f"  marca      : {MARCA}")
    print(f"  periodo    : {referencia[0].paid_date} .. {referencia[-1].paid_date}"
          f"  ({len(referencia)} dias)")
    print(f"  Torre leu  : {HOJE_TORRE} (a planilha foi extraida ~24 h antes)")
    print(f"  resolucao  : {RESOLUCAO_PP:.0f} pp (a planilha arredonda a taxa)")

    fora = [r for r in referencia if not r.fecha()]
    if fora:
        print(f"  REFERENCIA INCOERENTE em {len(fora)} dia(s): as quatro colunas nao "
              f"somam o total de pagos.", file=sys.stderr)
        return 1

    # Janela de extracao: cobre a referencia inteira com folga para o feriado.
    dias = (HOJE_TORRE - referencia[0].paid_date).days + 5

    conn = _conectar()
    try:
        resultados = {}
        por_dia_vigente = None
        for regra in REGRAS:
            coortes = [
                c for c in extrair(conn, HOJE_TORRE, dias,
                                   prazos=prazos_para(regra, HOJE_TORRE, dias))
                if c.brand == MARCA
            ]
            por_dia = {c.paid_date: c for c in coortes}
            if regra == REGRA_VIGENTE:
                por_dia_vigente = por_dia
            resultados[regra] = comparar(referencia, por_dia, regra)
    finally:
        conn.close()

    print("")
    print("=== qual regra de prazo reproduz a planilha ===")
    print("  regra                erro acumulado   erro medio   dias dentro da resolucao")
    for regra in sorted(resultados, key=lambda r: resultados[r].erro_acumulado_pp):
        d = resultados[regra]
        selo = "  <- vigente" if regra == REGRA_VIGENTE else ""
        print(f"  {regra:<20} {d.erro_acumulado_pp:9.1f} pp {d.erro_medio_pp:10.2f} pp"
              f" {d.dias_dentro_da_resolucao:14d}/{d.dias_comparados}{selo}")

    assert por_dia_vigente is not None
    print("")
    print("=== reconciliacao dos pedidos pagos (regra vigente) ===")
    print("  dia         planilha    torre    delta")
    igual = 0
    soma_ref = soma_torre = 0
    for ref in referencia:
        c = por_dia_vigente.get(ref.paid_date)
        t = c.pedidos_pagos_brutos if c else 0
        soma_ref += ref.paid_orders
        soma_torre += t
        if t == ref.paid_orders:
            igual += 1
        print(f"  {ref.paid_date}  {ref.paid_orders:8d} {t:8d} {t - ref.paid_orders:+8d}")
    print(f"  TOTAL       {soma_ref:8d} {soma_torre:8d} {soma_torre - soma_ref:+8d}"
          f"  ({100.0 * (soma_torre - soma_ref) / soma_ref:+.2f}%)")
    print(f"  dias com pedidos pagos IDENTICOS: {igual}/{len(referencia)}")

    # Totais do rodape da imagem, recalculados a partir das linhas.
    print("")
    print("=== rodape da planilha, recalculado das linhas ===")
    print(f"  pagos                 {sum(r.paid_orders for r in referencia)}   (imagem: 23317)")
    print(f"  postados no prazo     {sum(r.shipped_within_sla for r in referencia)}   (imagem: 15258)")
    print(f"  postados atrasados    {sum(r.shipped_after_sla for r in referencia)}   (imagem: 7668)")
    print(f"  nao postados vencidos {sum(r.not_shipped_overdue for r in referencia)}   (imagem: 140)")
    print(f"  nao postados no prazo {sum(r.not_shipped_on_time for r in referencia)}   (imagem: 251)")
    media8 = sum(r.late_rate_pct_rounded for r in referencia[-8:]) / 8
    print(f"  media dos ultimos 8 dias (col. arredondada) = {media8:.2f}%"
          f"   (imagem: 1,09%; a diferenca e' o arredondamento da propria planilha)")

    vigente = resultados[REGRA_VIGENTE]
    print("")
    if vigente.erro_medio_pp > ERRO_MEDIO_MAXIMO_PP:
        print(f"FALHA: a regra vigente deixou de reproduzir a planilha "
              f"({vigente.erro_medio_pp:.2f} pp > {ERRO_MEDIO_MAXIMO_PP:.2f} pp).",
              file=sys.stderr)
        return 1
    melhor = min(resultados.values(), key=lambda d: d.erro_acumulado_pp)
    if melhor.regra != REGRA_VIGENTE:
        print(f"FALHA: a regra '{melhor.regra}' reproduz a planilha melhor que a "
              f"vigente '{REGRA_VIGENTE}'.", file=sys.stderr)
        return 1
    print(f"OK: a regra vigente ({REGRA_VIGENTE}) e' a que melhor reproduz a "
          f"planilha, com erro medio de {vigente.erro_medio_pp:.2f} pp.")
    print(f"agora: {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
