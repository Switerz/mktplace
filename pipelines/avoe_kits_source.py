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
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def build_avoe_bom(kits: Iterable[Mapping], *,
                   brand_code_rows: Iterable[Mapping]) -> AvoeBom:
    """Kits da Avoe -> linhas de BOM + ponte, no formato do contrato.

    `brand_code_rows` sao as linhas de `SQL_BRIDGE_BRAND_CODE` (marca, codigo,
    protheus_sku). Sao usadas SO' para traduzir o codigo do componente.
    """
    indice = _indice_de_codigos(brand_code_rows)
    recusados: Counter = Counter()
    aceitos: dict[tuple[str, str], Counter] = {}
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
        aceitos.setdefault((marca, sku_kit), composicao)

    for par in conflitantes:
        del aceitos[par]
        recusados[MOTIVO_COMPOSICAO_CONFLITANTE] += 1

    out = AvoeBom()
    traduzidos, sem_ponte, ambiguos = 0, 0, 0
    por_marca: Counter = Counter()
    for (marca, sku_kit), composicao in sorted(aceitos.items()):
        chave_kit = chave(marca, sku_kit)
        por_marca[marca] += 1
        for codigo, qtd in sorted(composicao.items()):
            alvos = indice.get((marca, codigo), set())
            if len(alvos) == 1:
                componente = next(iter(alvos))
                traduzidos += 1
            else:
                # Sem traducao unica: o componente fica com chave propria, o
                # catalogo nao o conhece e o contrato bloqueia o kit com motivo.
                componente = chave(marca, codigo)
                if alvos:
                    ambiguos += 1
                else:
                    sem_ponte += 1
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
    }
    return out


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
