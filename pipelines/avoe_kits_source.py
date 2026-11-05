"""Composicao de kits vinda da API da Avoe, no formato que o publisher consome.

    python -m pipelines.avoe_kits_source --secret-file <arquivo> --out <snapshot.json>

O QUE ESTE MODULO FAZ — E O QUE ELE NAO FAZ
-------------------------------------------
Ele so' TRADUZ. Entrega a BOM da Avoe nas mesmas tres listas de linhas que
`kit_reference_publisher` ja' le' do Data Mart:

- `bom_rows`        -> `KitBomIndex.build` (kit_sku, component_sku, qty_per_kit,
                       valid_from, valid_to, active);
- `brand_code_rows` -> `KitBridgeIndex.build(brand_code_rows=...)`;
- `kit_brand_rows`  -> `KitBridgeIndex.build(kit_brand_rows=...)`.

Toda decisao — validacao da BOM, ponte, promocao, referencia do componente,
faixa de desconto, motivo de cada `NULL` — continua em
`app.services.pma_kit_bom`. Aqui nao ha `if status ==`, nao ha soma de preco e
nao ha regra de ponte paralela.

POR QUE A CHAVE DO KIT E' `AVOE:<MARCA>:<SKU>`
----------------------------------------------
A Avoe numera kit POR MARCA: `KIT001` existe na Apice, na Barbour's e na
Lescent, com composicoes diferentes. O `KitBomIndex` indexa so' por `kit_sku`;
com a chave crua as tres BOMs colidiriam e o contrato recusaria a carga (ou,
pior, somaria componentes de marcas diferentes). O prefixo torna a chave unica
e deixa a procedencia visivel em `kit_protheus_sku`.

A oferta chega ao kit por `(marca, seller_sku)` EXATO — a mesma ponte
`brand_code_exact` que o contrato ja' usa para o cadastro. `KIT001` de uma loja
Lescent so' alcanca `AVOE:LESCENT:KIT001`; nunca o da Apice.

COMPONENTE: SO' `(marca, codigo)` EXATO, NUNCA EAN
---------------------------------------------------
A Avoe devolve o componente no espaco de codigo do ERP da marca (`10019`,
`20498`, `LC03034`). Esses codigos COLIDEM entre marcas (`10005` e' Lescent
numa venda e Barbour's na outra). A traducao para o SKU Protheus usa as linhas
de `gold.map_produto_codigo_gobeauty` que o publisher ja' carrega
(`SQL_BRIDGE_BRAND_CODE`), escopadas pela MARCA DO KIT e com resultado UNICO.

Sem traducao unica, o componente NAO e' inventado nem descartado: ele entra
como `AVOE:<MARCA>:<codigo>`, o catalogo nao o conhece e o contrato publica o
kit como `blocked` com `kit_component_not_in_catalog`. Descartar o componente
produziria um kit mais barato, que e' o defeito que o contrato existe para
impedir. O EAN que a Avoe devolve nunca e' usado como criterio.

DE-PARA VERSIONADO (T-0063)
---------------------------
`avoe_de_para_componentes.csv`, ao lado deste modulo, e' uma SEGUNDA fonte de
traducao, mantida a mao e aprovada linha a linha. Ela so' cobre o que o
`map_produto` nao traduz, com cinco travas:

(a) so' vale quando o `map` NAO traduz aquele `(marca, codigo)`;
(b) se o `map` traduz para um SKU DIFERENTE do de-para, o componente fica
    ambiguo (bloqueado) — nenhum dos dois e' escolhido;
(c) o `sku_destino` tem de estar no catalogo do contrato (`SQL_CATALOG`), sob
    a mesma marca; senao o componente fica sem ponte;
(d) a linha so' vale para kit da MESMA marca (a chave e' `(marca, codigo)`);
(e) `ativo=false` e' ignorada.

LINHA NEGATIVA (`bloquear=true`, `sku_destino` vazio): a traducao daquele
`(marca, codigo)` esta' provada errada. Ela vale MESMO quando o `map` traduz
(e' a excecao a' trava (a)): o componente fica ambiguo com motivo
`de_para_bloqueio`, nunca traduzido. Positiva e negativa ativas na mesma chave
sao conflito e o carregamento levanta.

TRAVA DE VOLUME
---------------
Vale para TODA traducao (map ou de-para). Se o nome do componente na Avoe e o
nome do SKU destino no catalogo declaram volume/peso (`ml`, `l`, `g`, `gr`,
`kg`) e, normalizados (1 l = 1000 ml, 1 kg = 1000 g), os conjuntos diferem, a
traducao vira ambigua com motivo `volume_diverge`. Unidades de dimensoes
diferentes (g x ml) contam como diferentes: `volume_unidade_incompativel`,
tambem bloqueado (regra conservadora: sem densidade, nao ha conversao). Se um
dos lados nao declara volume, a traducao NAO e' bloqueada; o par e' contado
como `sem_prova` no resumo. Os nomes da Avoe sao os de TODOS os kits em que o
codigo aparece: a traducao e' uma so', entao um nome que a contradiz bloqueia
o codigo em todos os kits. Ambiguo, aqui, tem o mesmo efeito de sempre: o
componente fica com chave `AVOE:` e o contrato bloqueia o kit; o motivo fino
fica em `summary["ambiguos_por_motivo"]` e `summary["bloqueios"]`.

QUANTIDADE: REPETICAO NA LISTA
------------------------------
A Avoe nao tem coluna de quantidade: um kit de 3 unidades do mesmo produto
vem com o componente repetido tres vezes. `qty_per_kit` e' a contagem das
repeticoes — o que a fonte diz, sem suposicao.

O QUE E' RECUSADO NA ENTRADA (o kit some; a oferta fica sem linha)
------------------------------------------------------------------
- marca fora de `MARCAS` (fail-closed: marca nova nasce recusada);
- `sku_kit` vazio ou com caractere fora de `[A-Z0-9._-]` (medido: ha um kit
  cujo `sku_kit` e' o NOME do kit, com espacos e apostrofo);
- componente sem `sku`;
- kit sem componente;
- a mesma `(marca, sku_kit)` com duas composicoes diferentes (as duas saem).

CREDENCIAL
----------
A chave so' existe dentro de `_carregar_credencial` -> `_post`. Ela vem de um
ARQUIVO cujo caminho e' passado por argumento ou por `AVOE_API_SECRET_FILE`;
nunca e' logada, impressa, gravada no snapshot nem incluida em mensagem de
erro. O snapshot gravado contem apenas a resposta da Avoe.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

#: Prefixo das chaves sinteticas. Nao colide com chave Protheus (`KAP99003`,
#: `KBB99086`...) nem com codigo de canal.
PREFIXO = "AVOE"

#: Marca da Avoe -> marca da Torre (a de `fact_channel_offer_observation.brand`
#: e de `gold.map_produto_codigo_gobeauty.marca`). Allowlist: marca ausente
#: daqui e' recusada, nao adivinhada. As quatro sao as que a Avoe cobre hoje.
MARCAS = {
    "apice": "apice",
    "barbour's": "barbours",
    "barbour's beauty": "barbours",
    "barbours": "barbours",
    "lescent": "lescent",
    "denavita": "denavita",
}

#: Filtro `marca` que a ferramenta `consultar_kit` aceita, por marca da Torre.
MARCA_NA_API = {
    "apice": "Apice",
    "barbours": "Barbour's",
    "lescent": "Lescent",
    "denavita": "Denavita",
}

#: Termos iniciais de busca por marca. `consultar_kit` devolve no maximo
#: `TETO_RESULTADOS` kits por chamada e a busca casa SUBSTRING do SKU; um termo
#: que bate no teto e' refinado com um digito a mais (`KIT0` -> `KIT00`..`KIT09`).
TERMOS_INICIAIS = {
    "apice": ("KIT0", "KIT1", "KIT2"),
    "barbours": ("KIT0", "KIT1", "KIT2"),
    "lescent": ("KIT0", "KIT1", "KIT2"),
    "denavita": ("kit",),
}
TETO_RESULTADOS = 20
#: Teto de chamadas de uma captura. Estourar aborta a captura inteira — um
#: snapshot parcial gravado em silencio pareceria completo.
MAX_CHAMADAS = 80
MAX_PROFUNDIDADE = 3

ENV_SECRET_FILE = "AVOE_API_SECRET_FILE"
ENV_SNAPSHOT = "AVOE_KITS_SNAPSHOT"

MOTIVO_MARCA_DESCONHECIDA = "marca_desconhecida"
MOTIVO_SKU_INVALIDO = "sku_kit_invalido"
MOTIVO_COMPONENTE_SEM_SKU = "componente_sem_sku"
MOTIVO_SEM_COMPONENTES = "kit_sem_componentes"
MOTIVO_COMPOSICAO_CONFLITANTE = "composicao_conflitante"

_SKU_VALIDO = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")

#: De-para versionado, empacotado ao lado deste modulo (`package-data` do
#: `pyproject.toml`). Ausente ou malformado: `AvoeSourceError`, nunca vazio
#: silencioso.
DE_PARA_CSV = Path(__file__).with_name("avoe_de_para_componentes.csv")
DE_PARA_COLUNAS = ("marca", "codigo_avoe", "sku_destino", "classe",
                   "evidencia", "aprovado_por", "ativo", "bloquear")

#: Origem de uma traducao aceita.
ORIGEM_MAP = "map_produto"
ORIGEM_DE_PARA = "de_para"

#: Motivos de componente AMBIGUO (bloqueado, nunca desempatado).
AMBIGUO_MAP_MULTIPLO = "map_multiplo"
AMBIGUO_MAP_DIVERGE_DE_PARA = "map_diverge_de_para"
AMBIGUO_DE_PARA_MULTIPLO = "de_para_multiplo"
AMBIGUO_DE_PARA_BLOQUEIO = "de_para_bloqueio"
AMBIGUO_VOLUME_DIVERGE = "volume_diverge"
AMBIGUO_VOLUME_UNIDADE = "volume_unidade_incompativel"

#: Motivos de componente SEM PONTE.
SEM_PONTE_SEM_TRADUCAO = "sem_traducao"
SEM_PONTE_DE_PARA_FORA_DO_CATALOGO = "de_para_destino_fora_do_catalogo"
SEM_PONTE_DE_PARA_OUTRA_MARCA = "de_para_destino_de_outra_marca"

#: Veredito da trava de volume.
VOLUME_CONCORDA = "concorda"
VOLUME_SEM_PROVA = "sem_prova"
VOLUME_DIVERGE = "diverge"
VOLUME_UNIDADE_INCOMPATIVEL = "unidade_incompativel"

#: Numero + unidade de volume/peso. O numero nao pode vir colado a letra ou
#: digito (`AP3000`, `Q10`, `N20`) e a unidade nao pode continuar em letra
#: (`3 Lescent` nao e' 3 litros).
_VOLUME = re.compile(
    r"(?<![0-9a-z])(\d+(?:[.,]\d+)?)\s*"
    r"(ml|litros?|lts?|l|kg|gramas?|grs?|g)(?![0-9a-z])")
_UNIDADE = {"ml": ("ml", 1), "l": ("ml", 1000), "lt": ("ml", 1000),
            "lts": ("ml", 1000), "litro": ("ml", 1000), "litros": ("ml", 1000),
            "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "grama": ("g", 1),
            "gramas": ("g", 1), "kg": ("g", 1000)}


class AvoeSourceError(RuntimeError):
    """Falha que impede usar a Avoe como fonte. Nunca carrega a chave."""


# ---------------------------------------------------------------------------
# Traducao — pura, sem I/O
# ---------------------------------------------------------------------------

def _sku(raw: object) -> str | None:
    """Mesma normalizacao de `pma_match.normalize_sku_key`: strip + upper.

    Repetida aqui (uma linha) para que este modulo nao dependa de `apps/api`;
    o contrato normaliza de novo tudo o que recebe.
    """
    if raw is None:
        return None
    texto = str(raw).strip().upper()
    return texto or None


def marca_da_torre(marca_avoe: object) -> str | None:
    if marca_avoe is None:
        return None
    return MARCAS.get(str(marca_avoe).strip().lower())


def chave(marca: str, codigo: str) -> str:
    return f"{PREFIXO}:{marca.upper()}:{codigo}"


@dataclass
class AvoeBom:
    bom_rows: list = field(default_factory=list)
    brand_code_rows: list = field(default_factory=list)
    kit_brand_rows: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _indice_de_codigos(brand_code_rows: Iterable[Mapping]) -> dict:
    """`(marca, codigo)` -> conjunto de SKUs Protheus, das linhas do cadastro."""
    indice: dict[tuple[str, str], set[str]] = {}
    for row in brand_code_rows:
        marca = row.get("brand") or row.get("marca")
        marca = str(marca).strip().lower() if marca is not None else None
        codigo = _sku(row.get("code") or row.get("codigo"))
        alvo = _sku(row.get("protheus_sku"))
        if marca and codigo and alvo:
            indice.setdefault((marca, codigo), set()).add(alvo)
    return indice


def _ativo(raw: object) -> bool:
    """`ativo` do de-para: so' `true` (ou booleano verdadeiro) liga a linha."""
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() == "true"


def _indice_de_para(de_para_rows: Iterable[Mapping]) -> dict:
    """`(marca, codigo)` -> conjunto de SKUs destino, so' das linhas ATIVAS.

    Trava (e): `ativo=false` nao entra. Trava (d): a chave carrega a marca,
    entao a linha so' alcanca componente de kit da mesma marca.
    """
    indice: dict[tuple[str, str], set[str]] = {}
    for row in de_para_rows:
        if not _ativo(row.get("ativo")) or _ativo(row.get("bloquear")):
            continue
        marca = row.get("marca")
        marca = str(marca).strip().lower() if marca is not None else None
        codigo = _sku(row.get("codigo_avoe"))
        alvo = _sku(row.get("sku_destino"))
        if marca and codigo and alvo:
            indice.setdefault((marca, codigo), set()).add(alvo)
    return indice


def _bloqueios_de_para(de_para_rows: Iterable[Mapping]) -> set:
    """`(marca, codigo)` das linhas NEGATIVAS ativas (`bloquear=true`).

    `bloquear` usa a mesma leitura de `ativo`: so' `true` liga. Linha sem a
    coluna (chamador antigo) e' positiva.
    """
    chaves: set[tuple[str, str]] = set()
    for row in de_para_rows:
        if not (_ativo(row.get("ativo")) and _ativo(row.get("bloquear"))):
            continue
        marca = row.get("marca")
        marca = str(marca).strip().lower() if marca is not None else None
        codigo = _sku(row.get("codigo_avoe"))
        if marca and codigo:
            chaves.add((marca, codigo))
    return chaves


def _indice_do_catalogo(catalog_rows: Iterable[Mapping] | None) -> dict | None:
    """SKU do catalogo -> {"marcas": set, "nomes": list}. `None` = sem catalogo."""
    if catalog_rows is None:
        return None
    indice: dict[str, dict] = {}
    for row in catalog_rows:
        sku = _sku(row.get("component_sku") or row.get("sku"))
        if sku is None:
            continue
        item = indice.setdefault(sku, {"marcas": set(), "nomes": []})
        marca = row.get("brand") or row.get("marca")
        item["marcas"].add(str(marca).strip().lower() if marca else None)
        nome = row.get("name") or row.get("nome")
        if nome:
            item["nomes"].append(str(nome))
    return indice


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def volumes_declarados(nomes: Iterable[object]) -> frozenset:
    """Volumes/pesos declarados nos nomes, normalizados: {("ml"|"g", Decimal)}.

    `1L` e `1000ml` viram o mesmo `("ml", 1000)`; `1kg` e `1000g`, o mesmo
    `("g", 1000)`. Nome sem volume nao contribui.
    """
    achados = set()
    for nome in nomes:
        if not nome:
            continue
        for numero, unidade in _VOLUME.findall(_sem_acento(str(nome))):
            dimensao, fator = _UNIDADE[unidade]
            valor = Decimal(numero.replace(",", ".")) * fator
            achados.add((dimensao, valor.normalize()))
    return frozenset(achados)


def comparar_volume(nomes_avoe: Iterable[object],
                    nomes_destino: Iterable[object]) -> str:
    """Veredito da trava de volume entre a Avoe e o SKU destino."""
    va, vd = volumes_declarados(nomes_avoe), volumes_declarados(nomes_destino)
    if not va or not vd:
        return VOLUME_SEM_PROVA
    if va == vd:
        return VOLUME_CONCORDA
    if {d for d, _ in va}.isdisjoint({d for d, _ in vd}):
        return VOLUME_UNIDADE_INCOMPATIVEL
    return VOLUME_DIVERGE


def _traduzir(marca: str, codigo: str, nomes_avoe: list, *, indice: dict,
              de_para: dict, catalogo: dict | None,
              bloqueados: set = frozenset()) -> dict:
    """Decide UM componente. Devolve `alvo` (ou None), `estado` e `motivo`.

    `estado`: `traduzido` | `ambiguo` | `sem_ponte`. Nada aqui desempata.
    """
    alvos_map = indice.get((marca, codigo), set())
    alvos_dp = de_para.get((marca, codigo), set())
    res = {"alvo": None, "origem": None, "estado": None, "motivo": None,
           "volume": None, "map": sorted(alvos_map), "de_para": sorted(alvos_dp)}

    if (marca, codigo) in bloqueados:
        # Linha negativa: a traducao esta' provada errada. Vale mesmo com o
        # `map` traduzindo (excecao a' trava (a)) e vence qualquer positiva.
        return dict(res, estado="ambiguo", motivo=AMBIGUO_DE_PARA_BLOQUEIO)
    if len(alvos_map) > 1:
        return dict(res, estado="ambiguo", motivo=AMBIGUO_MAP_MULTIPLO)
    if len(alvos_map) == 1:
        alvo = next(iter(alvos_map))
        if alvos_dp and alvos_dp != {alvo}:
            # Trava (b): map e de-para discordam; nenhum dos dois vence.
            return dict(res, estado="ambiguo", motivo=AMBIGUO_MAP_DIVERGE_DE_PARA)
        origem = ORIGEM_MAP
    else:
        # Trava (a): so' aqui, com o map calado, o de-para e' consultado.
        if not alvos_dp:
            return dict(res, estado="sem_ponte", motivo=SEM_PONTE_SEM_TRADUCAO)
        if len(alvos_dp) > 1:
            return dict(res, estado="ambiguo", motivo=AMBIGUO_DE_PARA_MULTIPLO)
        alvo = next(iter(alvos_dp))
        item = (catalogo or {}).get(alvo)
        if item is None:
            # Trava (c): destino fora do catalogo (ou catalogo nao fornecido).
            return dict(res, estado="sem_ponte",
                        motivo=SEM_PONTE_DE_PARA_FORA_DO_CATALOGO)
        if item["marcas"] != {marca}:
            return dict(res, estado="sem_ponte",
                        motivo=SEM_PONTE_DE_PARA_OUTRA_MARCA)
        origem = ORIGEM_DE_PARA

    nomes_destino = ((catalogo or {}).get(alvo) or {}).get("nomes", [])
    volume = comparar_volume(nomes_avoe, nomes_destino)
    res.update(alvo=alvo, origem=origem, volume=volume)
    if volume == VOLUME_DIVERGE:
        return dict(res, alvo=None, estado="ambiguo", motivo=AMBIGUO_VOLUME_DIVERGE)
    if volume == VOLUME_UNIDADE_INCOMPATIVEL:
        return dict(res, alvo=None, estado="ambiguo", motivo=AMBIGUO_VOLUME_UNIDADE)
    return dict(res, estado="traduzido")


def build_avoe_bom(kits: Iterable[Mapping], *,
                   brand_code_rows: Iterable[Mapping],
                   catalog_rows: Iterable[Mapping] | None = None,
                   de_para_rows: Iterable[Mapping] = ()) -> AvoeBom:
    """Kits da Avoe -> linhas de BOM + ponte, no formato do contrato.

    `brand_code_rows` sao as linhas de `SQL_BRIDGE_BRAND_CODE` (marca, codigo,
    protheus_sku). Sao usadas SO' para traduzir o codigo do componente.

    `catalog_rows` sao as de `SQL_CATALOG` (com `name`): dao a trava (c) do
    de-para e o nome do SKU destino para a trava de volume. Sem catalogo,
    nenhum destino do de-para e' aceito e a trava de volume fica `sem_prova`.

    `de_para_rows` sao as linhas de `avoe_de_para_componentes.csv`. Vazio (o
    padrao) = comportamento anterior ao de-para.
    """
    indice = _indice_de_codigos(brand_code_rows)
    de_para_rows = list(de_para_rows)
    de_para = _indice_de_para(de_para_rows)
    bloqueados = _bloqueios_de_para(de_para_rows)
    catalogo = _indice_do_catalogo(catalog_rows)
    recusados: Counter = Counter()
    aceitos: dict[tuple[str, str], Counter] = {}
    nomes_por_kit: dict[tuple[str, str], dict[str, list]] = {}
    conflitantes: set[tuple[str, str]] = set()

    for kit in kits:
        marca = marca_da_torre(kit.get("marca"))
        if marca is None:
            recusados[MOTIVO_MARCA_DESCONHECIDA] += 1
            continue
        sku_kit = _sku(kit.get("sku_kit"))
        if sku_kit is None or not _SKU_VALIDO.match(sku_kit):
            recusados[MOTIVO_SKU_INVALIDO] += 1
            continue
        componentes = list(kit.get("componentes") or [])
        if not componentes:
            recusados[MOTIVO_SEM_COMPONENTES] += 1
            continue
        codigos = [_sku((c or {}).get("sku")) for c in componentes]
        if any(c is None for c in codigos):
            recusados[MOTIVO_COMPONENTE_SEM_SKU] += 1
            continue

        composicao = Counter(codigos)
        anterior = aceitos.get((marca, sku_kit))
        if anterior is not None and anterior != composicao:
            conflitantes.add((marca, sku_kit))
        if anterior is None:
            nomes: dict[str, list] = {}
            for codigo, c in zip(codigos, componentes):
                if (c or {}).get("nome"):
                    nomes.setdefault(codigo, []).append(c["nome"])
            nomes_por_kit[(marca, sku_kit)] = nomes
        aceitos.setdefault((marca, sku_kit), composicao)

    for par in conflitantes:
        del aceitos[par]
        recusados[MOTIVO_COMPOSICAO_CONFLITANTE] += 1

    # A trava de volume julga a TRADUCAO `(marca, codigo) -> SKU`, que e' a
    # mesma em todo kit. Por isso os nomes sao os de TODOS os kits aceitos: um
    # nome que contradiz o destino em um kit bloqueia o codigo em todos, inclusive
    # nos kits em que a Avoe mandou o componente sem nome.
    nomes_por_codigo: dict[tuple[str, str], list] = {}
    for (marca, sku_kit) in aceitos:
        for codigo, lista in nomes_por_kit[(marca, sku_kit)].items():
            nomes_por_codigo.setdefault((marca, codigo), []).extend(lista)

    out = AvoeBom()
    traduzidos, sem_ponte, ambiguos = 0, 0, 0
    por_marca: Counter = Counter()
    por_origem: Counter = Counter()
    ambiguos_motivo: Counter = Counter()
    sem_ponte_motivo: Counter = Counter()
    volume: Counter = Counter()
    bloqueios: list[dict] = []
    for (marca, sku_kit), composicao in sorted(aceitos.items()):
        chave_kit = chave(marca, sku_kit)
        por_marca[marca] += 1
        for codigo, qtd in sorted(composicao.items()):
            nomes = nomes_por_codigo.get((marca, codigo), [])
            r = _traduzir(marca, codigo, nomes, indice=indice,
                          de_para=de_para, catalogo=catalogo,
                          bloqueados=bloqueados)
            if r["volume"] is not None:
                volume[r["volume"]] += 1
            if r["estado"] == "traduzido":
                componente = r["alvo"]
                traduzidos += 1
                por_origem[r["origem"]] += 1
            else:
                # Sem traducao unica: o componente fica com chave propria, o
                # catalogo nao o conhece e o contrato bloqueia o kit com motivo.
                componente = chave(marca, codigo)
                if r["estado"] == "ambiguo":
                    ambiguos += 1
                    ambiguos_motivo[r["motivo"]] += 1
                else:
                    sem_ponte += 1
                    sem_ponte_motivo[r["motivo"]] += 1
                if r["motivo"] != SEM_PONTE_SEM_TRADUCAO:
                    bloqueios.append({
                        "kit": chave_kit, "codigo": codigo,
                        "motivo": r["motivo"], "map": r["map"],
                        "de_para": r["de_para"],
                        "nomes_avoe": sorted(set(nomes))})
            out.bom_rows.append({
                "kit_sku": chave_kit, "component_sku": componente,
                "qty_per_kit": qtd, "valid_from": None, "valid_to": None,
                "active": True,
            })
        out.brand_code_rows.append(
            {"brand": marca, "code": sku_kit, "protheus_sku": chave_kit})
        out.kit_brand_rows.append({"protheus_sku": chave_kit, "brand": marca})

    out.summary = {
        "kits_aceitos": len(aceitos),
        "kits_por_marca": dict(sorted(por_marca.items())),
        "kits_recusados": dict(sorted(recusados.items())),
        "componentes_traduzidos": traduzidos,
        "componentes_sem_ponte": sem_ponte,
        "componentes_ambiguos": ambiguos,
        "traduzidos_por_origem": dict(sorted(por_origem.items())),
        "ambiguos_por_motivo": dict(sorted(ambiguos_motivo.items())),
        "sem_ponte_por_motivo": dict(sorted(sem_ponte_motivo.items())),
        "volume": dict(sorted(volume.items())),
        "de_para_linhas_ativas": sum(len(v) for v in de_para.values()),
        "de_para_bloqueios_ativos": len(bloqueados),
        "bloqueios": bloqueios,
    }
    return out


def carregar_de_para(path: str | os.PathLike = DE_PARA_CSV) -> list[dict]:
    """Le' e valida o de-para versionado. Nunca toca rede nem banco.

    Malformado -> `AvoeSourceError`: uma linha com marca digitada errada ou
    `ativo` fora de `true|false` nao pode virar "sem de-para" em silencio.
    Linha negativa = `sku_destino` vazio E `bloquear=true`; qualquer outra
    combinacao de destino vazio/`bloquear` levanta, e uma positiva e uma
    negativa ATIVAS na mesma `(marca, codigo)` tambem (conflito, nao desempate).
    """
    caminho = Path(path)
    try:
        texto = caminho.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AvoeSourceError(f"de-para da Avoe ausente: {caminho.name}") from exc
    leitor = csv.DictReader(texto.splitlines())
    if tuple(leitor.fieldnames or ()) != DE_PARA_COLUNAS:
        raise AvoeSourceError(
            f"de-para da Avoe com colunas inesperadas: {leitor.fieldnames}")
    marcas_validas = set(MARCAS.values())
    linhas = []
    # (marca, codigo) -> {"positiva": n, "negativa": n} das linhas ATIVAS.
    ativas: dict[tuple[str, str], dict[str, int]] = {}
    for n, row in enumerate(leitor, start=2):
        marca = (row.get("marca") or "").strip().lower()
        ativo = (row.get("ativo") or "").strip().lower()
        bloquear = (row.get("bloquear") or "").strip().lower()
        if marca not in marcas_validas:
            raise AvoeSourceError(f"de-para linha {n}: marca desconhecida {marca!r}")
        if ativo not in ("true", "false"):
            raise AvoeSourceError(f"de-para linha {n}: ativo deve ser true|false")
        if bloquear not in ("true", "false"):
            raise AvoeSourceError(f"de-para linha {n}: bloquear deve ser true|false")
        codigo = _sku(row.get("codigo_avoe"))
        destino = _sku(row.get("sku_destino"))
        if not codigo:
            raise AvoeSourceError(f"de-para linha {n}: codigo vazio")
        if bloquear == "true" and destino:
            raise AvoeSourceError(
                f"de-para linha {n}: linha negativa (bloquear=true) com destino")
        if bloquear == "false" and not destino:
            raise AvoeSourceError(
                f"de-para linha {n}: destino vazio sem bloquear=true")
        if ativo == "true":
            tipo = "negativa" if bloquear == "true" else "positiva"
            par = ativas.setdefault((marca, codigo), {})
            outro = "positiva" if tipo == "negativa" else "negativa"
            if outro in par:
                raise AvoeSourceError(
                    f"de-para linha {n}: linha {tipo} ativa conflita com a "
                    f"linha {outro} ativa {par[outro]} na mesma (marca, codigo)")
            par.setdefault(tipo, n)
        linhas.append(dict(row, marca=marca, ativo=(ativo == "true"),
                           bloquear=(bloquear == "true")))
    return linhas


# ---------------------------------------------------------------------------
# Snapshot local
# ---------------------------------------------------------------------------

def load_snapshot(path: str | os.PathLike) -> dict:
    """Le' e valida o snapshot gravado por `fetch_snapshot`. Nunca toca rede."""
    caminho = Path(path)
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AvoeSourceError(f"snapshot da Avoe ausente: {caminho}") from exc
    except json.JSONDecodeError as exc:
        raise AvoeSourceError(f"snapshot da Avoe ilegivel: {caminho}") from exc
    if not isinstance(dados, dict) or not isinstance(dados.get("kits"), list):
        raise AvoeSourceError("snapshot da Avoe sem a lista `kits`")
    if not dados["kits"]:
        raise AvoeSourceError("snapshot da Avoe vazio: nada a publicar")
    return dados


# ---------------------------------------------------------------------------
# Captura — rede, so' leitura
# ---------------------------------------------------------------------------

def _carregar_credencial(path: str | os.PathLike) -> tuple[str, str]:
    """Le' `AVOE_API_URL=` e `AVOE_API_KEY=` do arquivo. Nada e' impresso."""
    valores: dict[str, str] = {}
    try:
        texto = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AvoeSourceError("arquivo de credencial da Avoe ilegivel") from exc
    for linha in texto.splitlines():
        s = linha.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        valores[k.strip()] = v.strip()
    url, key = valores.get("AVOE_API_URL"), valores.get("AVOE_API_KEY")
    if not url or not key:
        raise AvoeSourceError(
            "arquivo de credencial sem AVOE_API_URL e/ou AVOE_API_KEY")
    return url, key


def _post(url: str, key: str, tool: str, args: Mapping) -> dict:
    corpo = json.dumps({"tool": tool, "args": dict(args)}).encode("utf-8")
    req = urllib.request.Request(
        url, data=corpo, method="POST",
        headers={"x-api-key": key, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # So' o status: o corpo do erro nao e' repassado.
        raise AvoeSourceError(f"Avoe respondeu HTTP {exc.code} em {tool}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AvoeSourceError(
            f"Avoe inalcancavel em {tool}: {type(exc).__name__}") from None


def fetch_snapshot(*, consultar: Callable[[str, Mapping], dict],
                   marcas: Iterable[str] = tuple(MARCA_NA_API),
                   max_chamadas: int = MAX_CHAMADAS) -> dict:
    """Pagina `consultar_kit` por refinamento de termo. So' leitura.

    `consultar(tool, args)` e' injetado para o teste rodar sem rede. Um termo
    que continua no teto na profundidade maxima vai para `termos_no_teto`: o
    snapshot e' gravado, mas declara que pode estar incompleto.
    """
    kits: dict[tuple[str, str], dict] = {}
    chamadas = 0
    no_teto: list[str] = []
    for marca in marcas:
        fila = [(t, 0) for t in TERMOS_INICIAIS[marca]]
        while fila:
            termo, prof = fila.pop(0)
            if chamadas >= max_chamadas:
                raise AvoeSourceError(
                    f"captura excedeu {max_chamadas} chamadas: abortada, "
                    "nada gravado")
            chamadas += 1
            resposta = consultar("consultar_kit", {
                "busca": termo, "marca": MARCA_NA_API[marca]})
            lista = ((resposta or {}).get("result") or {}).get("kits")
            if not isinstance(lista, list):
                raise AvoeSourceError(
                    f"resposta de consultar_kit sem `result.kits` ({marca}/{termo})")
            for k in lista:
                kits.setdefault((str(k.get("marca")), _sku(k.get("sku_kit")) or ""), k)
            if len(lista) >= TETO_RESULTADOS:
                if prof >= MAX_PROFUNDIDADE:
                    no_teto.append(f"{marca}/{termo}")
                else:
                    fila.extend((f"{termo}{d}", prof + 1) for d in "0123456789")
    return {
        "fonte": "avoe:consultar_kit",
        "capturado_em": datetime.now(timezone.utc).isoformat(),
        "chamadas": chamadas,
        "termos_no_teto": no_teto,
        "kits": [kits[k] for k in sorted(kits)],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--secret-file", default=os.environ.get(ENV_SECRET_FILE),
                   help=f"CAMINHO do arquivo de credencial (ou {ENV_SECRET_FILE})")
    p.add_argument("--out", required=True, help="snapshot JSON a gravar")
    args = p.parse_args(argv)
    if not args.secret_file:
        print(f"--secret-file ou {ENV_SECRET_FILE} e' obrigatorio.", file=sys.stderr)
        return 2
    try:
        url, key = _carregar_credencial(args.secret_file)
        snap = fetch_snapshot(consultar=lambda t, a: _post(url, key, t, a))
    except AvoeSourceError as exc:
        print(f"FALHA: {exc}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(snap, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(json.dumps({"kits": len(snap["kits"]), "chamadas": snap["chamadas"],
                      "termos_no_teto": snap["termos_no_teto"]}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
