"""Invariante estrutural do lote, conferido ANTES de qualquer DELETE/INSERT.

POR QUE ESTE MODULO EXISTE
--------------------------
O incidente EXP-3B2-I1 publicou 1.056 pedidos com os quatro resumos zerados, e
nada no caminho percebeu. A causa foi uma divergencia de CHAVE: o resumo era
indexado pela marca (`kokeshi`) e a fila pelo `seller_id` (`2227056661`), entao
`por_conta.get("kokeshi")` devolvia lista vazia e `backlog_count` saia 0 para
todas as contas. A publicacao seguiu, o commit foi aceito e a fotografia ficou
internamente contraditoria: a tabela de pedidos dizia 1.056, a de tendencia
dizia 0.

Corrigir a chave resolve ESTE defeito. Este modulo existe para o proximo: uma
fila e um resumo que nao fecham entre si nao podem ser publicados, seja qual
for a causa. A conferencia roda dentro de `publish_channel`, antes do DELETE,
porque e' o unico ponto por onde toda publicacao passa obrigatoriamente - um
chamador novo nao tem como esquecer de chamar.

O QUE SOMA E O QUE NAO SOMA
---------------------------
As quatro faixas de prazo (`overdue`, `due_within_24h`, `on_time`,
`unavailable`) sao uma PARTICAO de `DeadlineStatus`: mutuamente exclusivas e
exaustivas, entao somam exatamente o backlog da conta.

`over_48h`, `slow`, `zombie` e `stalled` sao TRANSVERSAIS. O mesmo pedido pode
ser lento E zumbi, e `stalled` e' o OR das duas - nunca a soma. Exigir que
somem o backlog seria codificar uma regra que o contrato nega. Aqui eles sao
conferidos apenas como contagens plausiveis: nunca negativos, nunca maiores que
o backlog da propria conta.

SAIDA SANITIZADA
----------------
As mensagens carregam contagens, nomes de campo e identificadores de CONTA.
Nunca `marketplace_order_id`, nunca dado de comprador: um invariante que vaza
pedido no log de erro troca um defeito por outro.
"""
from __future__ import annotations

from collections import Counter

#: Particao de `DeadlineStatus`: exclusiva e exaustiva, entao SOMA o backlog.
CAMPOS_DE_PRAZO = (
    "overdue_count",
    "due_within_24h_count",
    "on_time_count",
    "deadline_unavailable_count",
)

#: Dimensoes ORTOGONAIS. Nao somam entre si nem com as faixas de prazo.
CAMPOS_TRANSVERSAIS = (
    "over_48h_count",
    "slow_count",
    "zombie_count",
    "stalled_count",
)


def problemas_do_lote(
    channel: str,
    fila_rows: list[dict],
    summary_rows: list[dict],
    *,
    expected_accounts,
) -> list[str]:
    """Todos os problemas estruturais do lote. Lista vazia = lote coerente.

    Devolve a lista inteira em vez de parar no primeiro: quem le' o erro quer
    saber se a fotografia tem um desalinhamento ou varios.
    """
    esperadas = set(expected_accounts)
    problemas: list[str] = []

    contas_resumo = [r["shop_account"] for r in summary_rows]
    repetidas = sorted(c for c, n in Counter(contas_resumo).items() if n > 1)
    if repetidas:
        problemas.append(
            f"resumo com conta repetida: {repetidas} "
            "(o grao e' uma linha por conta por hora)"
        )

    conjunto_resumo = set(contas_resumo)
    if conjunto_resumo != esperadas:
        faltando = sorted(esperadas - conjunto_resumo)
        sobrando = sorted(conjunto_resumo - esperadas)
        problemas.append(
            "contas do resumo nao batem com as esperadas: "
            f"faltando={faltando} inesperadas={sobrando}"
        )

    por_conta = Counter(r["shop_account"] for r in fila_rows)
    desconhecidas = sorted(set(por_conta) - esperadas)
    if desconhecidas:
        problemas.append(
            f"fila com conta desconhecida: {desconhecidas} "
            "(toda linha publicada precisa pertencer a uma conta do registry)"
        )

    total_resumo = sum(r["backlog_count"] for r in summary_rows)
    if total_resumo != len(fila_rows):
        problemas.append(
            f"soma dos resumos ({total_resumo}) diferente das linhas da fila "
            f"({len(fila_rows)}) - foi assim que o incidente EXP-3B2-I1 "
            "publicou 1.056 pedidos com resumo zerado"
        )

    for r in summary_rows:
        conta = r["shop_account"]
        backlog = r["backlog_count"]
        real = por_conta.get(conta, 0)
        if backlog != real:
            problemas.append(
                f"conta {conta!r}: backlog_count={backlog} mas a fila tem "
                f"{real} linha(s)"
            )

        soma_prazo = sum(r[c] for c in CAMPOS_DE_PRAZO)
        if soma_prazo != backlog:
            detalhe = ", ".join(f"{c}={r[c]}" for c in CAMPOS_DE_PRAZO)
            problemas.append(
                f"conta {conta!r}: faixas de prazo somam {soma_prazo}, "
                f"backlog_count={backlog} ({detalhe})"
            )

        negativos = sorted(
            c for c in ("backlog_count",) + CAMPOS_DE_PRAZO + CAMPOS_TRANSVERSAIS
            if r[c] < 0
        )
        if negativos:
            problemas.append(f"conta {conta!r}: contagem negativa em {negativos}")

        # Transversais NAO somam - ver o cabecalho. So' checamos o teto.
        acima = sorted(c for c in CAMPOS_TRANSVERSAIS if r[c] > backlog)
        if acima:
            detalhe = ", ".join(f"{c}={r[c]}" for c in acima)
            problemas.append(
                f"conta {conta!r}: dimensao transversal maior que o backlog "
                f"({backlog}): {detalhe}"
            )

    return [f"{channel}: {p}" for p in problemas]
