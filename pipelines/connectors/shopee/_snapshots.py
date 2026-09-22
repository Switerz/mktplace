"""Agrupamento e escolha de snapshot dos exports `Order.all` da Shopee.

Gate SH-AUTO-1B. Este modulo resolve UMA pergunta: quando o mesmo pedido aparece
em mais de um export, qual export vale?

🔴 O DEFEITO QUE ELE CORRIGE
----------------------------
`parse_brand` lia TODOS os `Order.all*.xlsx` da pasta e empilhava as linhas num
unico lote; `_aggregate_daily` agrupava por `order_id` e SOMAVA `subtotal` e
`qty` linha a linha. Pedido presente em dois exports com janelas sobrepostas
tinha os itens contados DUAS VEZES. Medido em 01-24/08/2026, pedidos em mais de
um arquivo: barbours 1.007 · lescent 478 · rituaria 369 · apice 294 ·
kokeshi 10.161.

O dedup por `max()` que ja existia cobria apenas os campos *order-level*
(`total_global`, `commission_net`, `service_fee_net`, `freight_est`) — e `max()`
tambem esta errado como regra de escolha, porque mistura campos de instantes
diferentes: pega o maior valor de cada export, nao o valor de UM export.

🔑 A UNIDADE E O SNAPSHOT, NAO O ARQUIVO
----------------------------------------
Um export grande vem partido em `_part_N_of_M`. As partes sao pedacos do MESMO
retrato e nunca competem entre si: elas sao reagrupadas antes de qualquer
escolha. Tratar `part_3_of_8` como candidato concorrente de `part_4_of_8`
descartaria 7/8 do export.

🔑 A REGRA DO VENCEDOR: `(date_to, date_from)` DO NOME
------------------------------------------------------
Um export mais recente tem `date_to` maior, porque nao se exporta pedido de um
dia que ainda nao aconteceu. A chave completa e `(date_to, date_from)`: com o
mesmo teto, a janela que comeca depois e a mais estreita e mais recente.

✅ POR QUE NAO E' CHUTE — MEDIDO em 22/09/2026 sobre os arquivos reais:

  · Todos os 9 pares de snapshots sobrepostos de agosto/setembro em que ha
    divergencia de conteudo divergem EXCLUSIVAMENTE em status (apice, medido:
    50/50, 56/56 e 108/108 pedidos divergentes; quantidade e numero de SKUs
    identicos em 100%). Em todos, o snapshot de `date_to` maior e o de status
    mais maduro — que e exatamente o que se quer publicar.
  · O unico par em que a ordenacao por janela DISCORDA do `mtime` do arquivo
    (`20260401..20260501` x `20260501..20260531`, apice e barbours) tem
    conteudo IDENTICO nos pedidos em comum: 386/386 e 1.025/1.025. Os dois
    sairam da mesma carga historica, entao a escolha e indiferente e a
    discordancia nao tem consequencia observavel.

🔴 O QUE E' PROIBIDO COMO AUTORIDADE, E POR QUE
-----------------------------------------------
`Path.stat().st_mtime`: nao sobrevive a copia de pasta, a restauracao de backup
nem ao `git clone`, e nos arquivos reais ele e apenas o instante do DOWNLOAD —
medido, os exports de abril e maio da barbours tem o MESMO mtime (17:03), entao
ele nem desempata. Uma regra baseada nele daria resultado diferente em cada
maquina.

Ordem do `glob`, ordem lexicografica e ordem das linhas: sao acidentes do
sistema de arquivos. A regra tem de dar o mesmo resultado em qualquer ordem.

`max()` campo a campo: mistura instantes. Um pedido cancelado depois teria o
status novo e o valor antigo, e o numero publicado nao existiria em export
nenhum.

🔴 EMPATE RECUSA, NAO CHUTA
----------------------------
Dois snapshots com a MESMA janela (o caso do sufixo ` (1)` que o navegador cria
ao baixar duas vezes) nao tem desempate confiavel no nome. O modulo levanta
`SnapshotAmbiguo` em vez de escolher o primeiro do `glob` — escolher em silencio
seria publicar um retrato que depende da ordem do sistema de arquivos.

Parte faltando tem o mesmo tratamento: `SnapshotIncompleto`. Um export 7/8
publicado como se fosse inteiro perde um oitavo do faturamento sem nenhum sinal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

#: `_part_N_of_M`, em qualquer caixa. Mesma expressao de
#: `pipelines/ingestion/shopee_raw/inventory.py`, DUPLICADA de proposito: aquele
#: modulo importa `connectors.shopee.connector`, e importa-lo daqui fecharia um
#: ciclo de import. A duplicacao e travada por teste — `test_shopee_snapshots.py`
#: confere que as duas implementacoes concordam sobre os nomes reais, para que o
#: drift apareca no CI e nao no numero publicado.
_PART_RE = re.compile(r"_part_(\d+)_of_(\d+)", re.IGNORECASE)

#: `YYYYMMDD_YYYYMMDD` em qualquer posicao do nome. Cobre os dois prefixos reais:
#: `Order.all.<janela>.xlsx` (usado ate julho) e
#: `Order.all.order_creation_date.<janela>.xlsx` (usado desde setembro).
_JANELA_RE = re.compile(r"(\d{8})_(\d{8})")


class SnapshotError(ValueError):
    """Base dos erros de composicao de snapshot. Nunca carrega order_id,
    comprador, CPF ou valor de celula — apenas nome de arquivo e contagens,
    que e o que permite agir."""


class NomeDeExportInvalido(SnapshotError):
    """Arquivo de export sem janela `YYYYMMDD_YYYYMMDD` no nome.

    Sem janela nao ha como ordenar o arquivo contra os outros, e incluir um
    arquivo inordenavel na agregacao reintroduziria a dupla contagem pela porta
    dos fundos.
    """


class SnapshotIncompleto(SnapshotError):
    """Export multipart com parte faltando, repetida ou com total divergente."""


class SnapshotAmbiguo(SnapshotError):
    """Dois snapshots com a mesma janela e nenhum desempate confiavel."""


@dataclass(frozen=True)
class Snapshot:
    """Um export completo: uma janela, uma ou mais partes."""

    date_from: date
    date_to: date
    #: Nome do grupo — o nome do arquivo com `_part_N_of_M` removido. E' ele que
    #: distingue um download duplicado (` (1)`) do original.
    grupo: str
    arquivos: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def chave_de_ordem(self) -> tuple[date, date]:
        """🔑 A regra do vencedor, num lugar so. `date_to` primeiro."""
        return (self.date_to, self.date_from)

    @property
    def rotulo(self) -> str:
        """Identificacao curta para log e mensagem de erro, sem caminho."""
        return f"{self.date_from:%Y%m%d}..{self.date_to:%Y%m%d}"


def _janela(nome: str) -> tuple[date, date]:
    m = _JANELA_RE.search(nome)
    if not m:
        raise NomeDeExportInvalido(
            f"export sem janela YYYYMMDD_YYYYMMDD no nome: {nome!r}. Sem ela nao "
            f"ha como decidir qual export vence quando o mesmo pedido aparece em "
            f"dois arquivos, e somar os dois duplicaria as unidades."
        )
    try:
        return (
            datetime.strptime(m.group(1), "%Y%m%d").date(),
            datetime.strptime(m.group(2), "%Y%m%d").date(),
        )
    except ValueError as exc:
        raise NomeDeExportInvalido(
            f"janela invalida no nome do export {nome!r}: {exc}"
        ) from None


def _grupo_e_parte(nome: str) -> tuple[str, Optional[int], Optional[int]]:
    m = _PART_RE.search(nome)
    if not m:
        return nome, None, None
    return nome[: m.start()] + nome[m.end():], int(m.group(1)), int(m.group(2))


def agrupar_snapshots(arquivos: Iterable[Path]) -> list[Snapshot]:
    """Agrupa arquivos em snapshots completos, em ordem determinística.

    Levanta `SnapshotIncompleto` para multipart com buraco, parte repetida ou
    totais divergentes — nunca publica um export pela metade.
    """
    por_grupo: dict[str, list[tuple[Optional[int], Optional[int], Path]]] = {}
    for caminho in arquivos:
        grupo, indice, total = _grupo_e_parte(caminho.name)
        _janela(caminho.name)  # valida cedo, com o nome do arquivo na mensagem
        por_grupo.setdefault(grupo, []).append((indice, total, caminho))

    snapshots: list[Snapshot] = []
    for grupo in sorted(por_grupo):
        partes = por_grupo[grupo]
        totais = {t for _, t, _ in partes}
        indices = [i for i, _, _ in partes]

        if len(totais) > 1:
            raise SnapshotIncompleto(
                f"export {grupo!r} declara mais de um total de partes "
                f"({sorted(t for t in totais if t is not None)}). Arquivos de "
                f"exports diferentes provavelmente foram misturados na pasta."
            )
        total = totais.pop()

        if total is None:
            if len(partes) > 1:
                raise SnapshotIncompleto(
                    f"export {grupo!r} sem `_part_N_of_M` aparece "
                    f"{len(partes)} vezes."
                )
        else:
            if sorted(indices) != list(range(1, total + 1)):
                faltando = sorted(set(range(1, total + 1)) - set(indices))
                repetidas = sorted({i for i in indices if indices.count(i) > 1})
                raise SnapshotIncompleto(
                    f"export multipart {grupo!r} incompleto: esperadas {total} "
                    f"parte(s), encontradas {len(indices)}"
                    + (f", faltando {faltando}" if faltando else "")
                    + (f", repetidas {repetidas}" if repetidas else "")
                    + ". Um export publicado sem todas as partes perde "
                      "faturamento sem nenhum sinal."
                )

        date_from, date_to = _janela(grupo)
        snapshots.append(
            Snapshot(
                date_from=date_from,
                date_to=date_to,
                grupo=grupo,
                # ordenado pelo indice da parte: o resultado nao pode depender da
                # ordem em que o `glob` devolveu os arquivos.
                arquivos=tuple(c for _, _, c in sorted(partes, key=lambda p: (p[0] or 0, p[2].name))),
            )
        )

    # Ordem crescente pela chave do vencedor: o ULTIMO da lista e o mais recente.
    return sorted(snapshots, key=lambda s: (s.chave_de_ordem, s.grupo))


def validar_desempate(snapshots: Iterable[Snapshot]) -> None:
    """Recusa quando dois snapshots distintos compartilham a janela.

    E' o caso do ` (1)` que o navegador cria ao baixar duas vezes. Os dois podem
    ter conteudos diferentes e o nome nao diz qual e o mais novo; escolher pela
    ordem do `glob` faria o numero publicado depender do sistema de arquivos.
    """
    vistos: dict[tuple[date, date], str] = {}
    for s in snapshots:
        anterior = vistos.get(s.chave_de_ordem)
        if anterior is not None and anterior != s.grupo:
            raise SnapshotAmbiguo(
                f"dois exports com a MESMA janela {s.rotulo} e nenhum desempate "
                f"confiavel no nome: {anterior!r} e {s.grupo!r}. O nome do "
                f"arquivo nao carrega a data de exportacao (medido: os xlsx da "
                f"Shopee trazem `dcterms:created` fixo em 2006-09-16 e nenhuma "
                f"coluna de geracao), e `mtime` nao sobrevive a copia de pasta. "
                f"Remova o arquivo que nao deve valer, ou renomeie-o para fora "
                f"do padrao `Order.all*.xlsx`."
            )
        vistos[s.chave_de_ordem] = s.grupo


def deduplicar_por_pedido(
    lotes: list[tuple[Snapshot, list[dict]]],
    *,
    chave_pedido: str = "order_id",
) -> tuple[list[dict], dict]:
    """Mantem, para cada pedido, SOMENTE as linhas do snapshot vencedor.

    `lotes` chega em qualquer ordem; o resultado nao depende dela. Devolve as
    linhas sobreviventes e um resumo agregado (nunca `order_id`) para log.

    🔑 A escolha e por PEDIDO INTEIRO, e e' isso que distingue esta funcao de um
    `max()` campo a campo: status, devolucao, datas, comprador, itens e valores
    financeiros vem todos do MESMO export. Nao existe "status novo + itens
    antigos"; se o snapshot vencedor tem menos SKUs, os SKUs que so existiam no
    antigo somem de verdade — o vendedor removeu o item do pedido.
    """
    ordenados = sorted(lotes, key=lambda par: (par[0].chave_de_ordem, par[0].grupo))

    # `vencedor_de` guarda o indice do snapshot vencedor por pedido. Como a lista
    # esta em ordem crescente, basta sobrescrever: o ultimo a ver o pedido vence.
    vencedor_de: dict[str, int] = {}
    for posicao, (_, linhas) in enumerate(ordenados):
        for linha in linhas:
            pedido = linha.get(chave_pedido)
            if pedido:
                vencedor_de[str(pedido)] = posicao

    sobreviventes: list[dict] = []
    descartadas = 0
    pedidos_em_varios = 0
    vistos: set[str] = set()
    for posicao, (_, linhas) in enumerate(ordenados):
        for linha in linhas:
            pedido = linha.get(chave_pedido)
            if not pedido:
                # Linha sem chave nunca foi agregada (o nivel 1 do parser ja a
                # ignora); preserva-la aqui manteria o comportamento anterior.
                sobreviventes.append(linha)
                continue
            pedido = str(pedido)
            if vencedor_de[pedido] == posicao:
                sobreviventes.append(linha)
            else:
                descartadas += 1
                if pedido not in vistos:
                    pedidos_em_varios += 1
                    vistos.add(pedido)

    resumo = {
        "snapshots": len(ordenados),
        "linhas_entrada": sum(len(linhas) for _, linhas in ordenados),
        "linhas_mantidas": len(sobreviventes),
        "linhas_descartadas": descartadas,
        "pedidos_em_mais_de_um_snapshot": pedidos_em_varios,
        "ordem_dos_snapshots": [s.rotulo for s, _ in ordenados],
    }
    return sobreviventes, resumo
