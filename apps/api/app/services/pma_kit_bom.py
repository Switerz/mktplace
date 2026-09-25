"""Contrato da composicao de kits (BOM) para o monitoramento de precos.

Gate KITS-MAP-2. Este modulo transforma em comportamento de produto SO' o que
o KITS-MAP-1 provou. Ele nao decide quais ofertas viram kit — isso e' do
`pma_domain` — e nao publica nada. Ele responde uma pergunta so':

    dada uma oferta com ponte PROVADA para um kit Protheus, qual e' a
    referencia do kit, ou por que ela nao existe?

TRES RECUSAS DELIBERADAS
------------------------
1. **Ponte.** Somente `EXACT_DIRECT` e `EXACT_ALIAS` promovem, e o
   `EXACT_ALIAS` so' quando e' unico E da mesma marca. `CANDIDATE_REVIEW`,
   `AMBIGUOUS`, `CROSS_BRAND_CONFLICT` e `UNMAPPED` ficam bloqueados. A
   composicao empirica reconstruida de NF — 571 ofertas no diagnostico — NAO
   entra aqui: ela e' evidencia, e a contraprova contra o Protheus concordou
   integralmente em 1 de 10 kits. Titulo, distancia textual e preco nunca
   entram.

2. **Contrato da BOM.** `KitBomIndex.build` RECUSA a carga inteira quando
   encontra quantidade conflitante para o mesmo par, ciclo, autorreferencia ou
   quantidade nao positiva. Recusar a carga, e nao a linha, existe para que uma
   BOM corrompida nao produza referencia silenciosamente menor. Duplicata com
   quantidade IDENTICA e' colapsada, nunca somada: `KBB99170|BB02030` aparece
   duas vezes na fonte e vale 1 unidade, nao 2.

3. **Referencia do componente.** Precedencia EAN -> `(marca, source_sku)`, e
   qualquer duvida recusa: mais de um candidato em qualquer das duas chaves,
   marca ausente, marca conflitante declarada pela dimensao, ou as duas chaves
   apontando para referencias DIFERENTES. Recusa vira `None` com motivo, nunca
   zero.

POR QUE O FALLBACK POR `(marca, source_sku)` EXISTE
---------------------------------------------------
A planilha de referencia B2B carrega, em varios produtos, o EAN da geracao
ANTIGA de codigo. `KS03042` e' "Creme Gel Facial Pele Plena": o `dim_produto`
guarda EAN `7908790700137` e a referencia guarda `7899459312597`. A chave por
EAN nao casa; a chave `(kokeshi, KS03042)` casa, e casa de forma unica. Sem o
fallback, dois dos cinco componentes de `KKS00006` ficam sem referencia e o kit
inteiro perde o preco. O fallback nao e' uma flexibilizacao: e' uma SEGUNDA
chave exata, sujeita as mesmas recusas da primeira.

UNIDADES, NAO SKUs
------------------
A faixa de desconto olha `SUM(qty_per_kit)`. No catalogo medido, 85 kits tem
numero de SKUs diferente do numero de unidades e 82 deles MUDAM de faixa
conforme a leitura escolhida. Usar SKUs distintos como denominador daria
desconto menor em 82 kits.

NADA AQUI ESCREVE
-----------------
Sem I/O, sem banco, sem relogio proprio: `today` entra por parametro. O modulo
e' puro para que o teste possa exercitar a regra sem `.env` e sem rede.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException, ROUND_HALF_UP
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

from app.services.pma_match import (
    ReferenceIndex,
    consumer_ean_or_none,
    normalize_brand_key,
    normalize_sku_key,
)

# --------------------------------------------------------------------------
# Status da ponte oferta -> kit Protheus (vocabulario do KITS-MAP-1)
# --------------------------------------------------------------------------

#: SKU do canal casa diretamente com o codigo interno unico.
BRIDGE_EXACT_DIRECT = "EXACT_DIRECT"
#: Alias (codigo Bling/Tiny/Shopify/Omie ou `sku_antigo`) casa com UM SKU Protheus.
BRIDGE_EXACT_ALIAS = "EXACT_ALIAS"
#: Candidato com evidencia, exige aprovacao humana. NUNCA promove.
BRIDGE_CANDIDATE_REVIEW = "CANDIDATE_REVIEW"
#: Mais de um candidato. NUNCA promove, nunca desempata.
BRIDGE_AMBIGUOUS = "AMBIGUOUS"
#: Composicao ou produto de outra marca. NUNCA promove.
BRIDGE_CROSS_BRAND_CONFLICT = "CROSS_BRAND_CONFLICT"
#: Nenhuma ponte encontrada.
BRIDGE_UNMAPPED = "UNMAPPED"

BRIDGE_STATUSES = (
    BRIDGE_EXACT_DIRECT,
    BRIDGE_EXACT_ALIAS,
    BRIDGE_CANDIDATE_REVIEW,
    BRIDGE_AMBIGUOUS,
    BRIDGE_CROSS_BRAND_CONFLICT,
    BRIDGE_UNMAPPED,
)

#: Allowlist. Qualquer status fora daqui — inclusive um status NOVO que alguem
#: acrescente amanha — e' bloqueado por omissao, nao por lembrarem de veta-lo.
PROMOTABLE_BRIDGE_STATUSES = (BRIDGE_EXACT_DIRECT, BRIDGE_EXACT_ALIAS)

# --------------------------------------------------------------------------
# Motivos. Todo `None` publicado carrega exatamente um destes.
# --------------------------------------------------------------------------

REASON_BRIDGE_NOT_PROMOTABLE = "kit_bridge_not_promotable"
REASON_BRIDGE_BRAND_MISMATCH = "kit_bridge_brand_mismatch"
REASON_BOM_ABSENT = "kit_bom_absent"
REASON_COMPONENT_NOT_IN_CATALOG = "kit_component_not_in_catalog"
REASON_COMPONENT_BRAND_MISSING = "kit_component_brand_missing"
REASON_COMPONENT_BRAND_CONFLICT = "kit_component_brand_conflict"
REASON_COMPONENT_REFERENCE_MISSING = "kit_component_reference_missing"
REASON_COMPONENT_REFERENCE_AMBIGUOUS = "kit_component_reference_ambiguous"
REASON_COMPONENT_REFERENCE_INCOMPATIBLE = "kit_component_reference_incompatible"

KIT_REFERENCE_REASONS = (
    REASON_BRIDGE_NOT_PROMOTABLE,
    REASON_BRIDGE_BRAND_MISMATCH,
    REASON_BOM_ABSENT,
    REASON_COMPONENT_NOT_IN_CATALOG,
    REASON_COMPONENT_BRAND_MISSING,
    REASON_COMPONENT_BRAND_CONFLICT,
    REASON_COMPONENT_REFERENCE_MISSING,
    REASON_COMPONENT_REFERENCE_AMBIGUOUS,
    REASON_COMPONENT_REFERENCE_INCOMPATIBLE,
)

#: Metodo com que a referencia do COMPONENTE foi resolvida.
COMPONENT_MATCH_EAN = "component_brand_gtin_exact"
COMPONENT_MATCH_SKU = "component_brand_sku_exact_unique"

#: Teto de linhas da BOM. Recusa em vez de truncar — o mesmo criterio de
#: `MAX_REFERENCE_ROWS` no `pma_match`. Medido em 2026-09-25: 4.628 relacoes
#: ativas. O teto e' ~43x a escala atual.
MAX_BOM_ROWS = 200_000

#: Faixas de desconto por UNIDADES (nao por SKUs distintos).
_DISCOUNT_TIERS = ((Decimal(4), Decimal("0.15")),
                   (Decimal(3), Decimal("0.10")),
                   (Decimal(2), Decimal("0.05")))

_CENTS = Decimal("0.01")


class BomContractError(RuntimeError):
    """A carga da BOM violou o contrato. A carga INTEIRA e' recusada."""


# --------------------------------------------------------------------------
# Contrato da BOM
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BomComponent:
    component_sku: str
    qty_per_kit: Decimal


def _to_qty(raw: object, *, kit: str, component: str) -> Decimal:
    if raw is None:
        raise BomContractError(
            f"qty_per_kit nula em {kit}|{component}: quantidade ausente nao e' 1."
        )
    try:
        qty = Decimal(str(raw))
    except (DecimalException, ValueError) as exc:  # pragma: no cover - defensivo
        raise BomContractError(
            f"qty_per_kit ilegivel em {kit}|{component}: {raw!r}"
        ) from exc
    if not qty.is_finite():
        raise BomContractError(
            f"qty_per_kit nao finita em {kit}|{component}: {raw!r}"
        )
    if qty <= 0:
        raise BomContractError(
            f"qty_per_kit nao positiva em {kit}|{component}: {qty}"
        )
    return qty


def _is_vigente(row: Mapping, today: date) -> bool:
    """Vigencia com bordas ABERTAS: nulo em `valid_from`/`valid_to` nao limita.

    1.850 das 4.691 linhas da fonte tem `valid_from` nulo (todas de
    `apice_sheet`). Ler nulo como "nao vigente" descartaria a BOM inteira da
    Apice; ler como "inicio aberto" e' o significado que a fonte pratica.
    """
    inicio = row.get("valid_from")
    fim = row.get("valid_to")
    if inicio is not None and inicio > today:
        return False
    if fim is not None and fim < today:
        return False
    return True


@dataclass(frozen=True)
class KitBomIndex:
    """BOM validada e indexada por kit. Construir e' o unico jeito de obter uma."""

    components_by_kit: Mapping[str, tuple[BomComponent, ...]]
    #: pares colapsados por duplicata IDENTICA, para a reconciliacao auditar.
    collapsed_duplicates: tuple[tuple[str, str], tuple] | tuple

    def components(self, kit_sku: object) -> tuple[BomComponent, ...]:
        chave = normalize_sku_key(kit_sku)
        if chave is None:
            return ()
        return self.components_by_kit.get(chave, ())

    def units(self, kit_sku: object) -> Decimal | None:
        comps = self.components(kit_sku)
        if not comps:
            return None
        return sum((c.qty_per_kit for c in comps), Decimal(0))

    @staticmethod
    def build(rows: Iterable[Mapping], *, today: date) -> "KitBomIndex":
        """Valida e indexa. Levanta `BomContractError` no primeiro defeito real.

        Ordem das recusas e' deliberada: normalizacao -> vigencia -> quantidade
        -> autorreferencia -> duplicata divergente -> ciclo. Uma linha fora de
        vigencia nunca chega a ser avaliada quanto a quantidade, porque ela nao
        participa do resultado.
        """
        linhas = list(rows)
        if len(linhas) > MAX_BOM_ROWS:
            raise BomContractError(
                f"BOM com {len(linhas)} linhas excede o teto de {MAX_BOM_ROWS}. "
                "Recusada em vez de truncada."
            )

        qty_por_par: dict[tuple[str, str], Decimal] = {}
        colapsadas: list[tuple[str, str]] = []

        for row in linhas:
            kit = normalize_sku_key(row.get("kit_sku"))
            comp = normalize_sku_key(row.get("component_sku"))
            if kit is None or comp is None:
                raise BomContractError(
                    f"relacao sem chave: kit={row.get('kit_sku')!r} "
                    f"componente={row.get('component_sku')!r}"
                )
            if not row.get("active", True):
                continue
            if not _is_vigente(row, today):
                continue
            if kit == comp:
                raise BomContractError(
                    f"autorreferencia: {kit} e' componente de si mesmo."
                )
            qty = _to_qty(row.get("qty_per_kit"), kit=kit, component=comp)

            anterior = qty_por_par.get((kit, comp))
            if anterior is None:
                qty_por_par[(kit, comp)] = qty
            elif anterior == qty:
                # Duplicata IDENTICA: colapsa. Somar daria 2 unidades onde a
                # fonte quis dizer 1, e mudaria a faixa de desconto do kit.
                colapsadas.append((kit, comp))
            else:
                raise BomContractError(
                    f"quantidade conflitante para {kit}|{comp}: "
                    f"{anterior} e {qty}. A carga foi recusada — desempatar "
                    "seria escolher por acidente de ordenacao."
                )

        filhos: dict[str, set[str]] = {}
        for (kit, comp) in qty_por_par:
            filhos.setdefault(kit, set()).add(comp)

        ciclo = _primeiro_ciclo(filhos)
        if ciclo is not None:
            raise BomContractError(
                "ciclo na BOM: " + " -> ".join(ciclo)
            )

        por_kit: dict[str, list[BomComponent]] = {}
        for (kit, comp), qty in qty_por_par.items():
            por_kit.setdefault(kit, []).append(BomComponent(comp, qty))

        congelado = MappingProxyType({
            kit: tuple(sorted(comps, key=lambda c: c.component_sku))
            for kit, comps in por_kit.items()
        })
        return KitBomIndex(congelado, tuple(sorted(set(colapsadas))))


def _primeiro_ciclo(filhos: Mapping[str, set[str]]) -> list[str] | None:
    """DFS com cores. Devolve o primeiro ciclo encontrado, ou `None`.

    Busca em profundidade iterativa: a BOM tem 952 kits vigentes hoje, mas
    recursao aqui seria um limite artificial para um grafo que vem de fora.
    """
    BRANCO, CINZA, PRETO = 0, 1, 2
    cor: dict[str, int] = {}
    for raiz in filhos:
        if cor.get(raiz, BRANCO) != BRANCO:
            continue
        pilha: list[tuple[str, list[str]]] = [(raiz, [raiz])]
        cor[raiz] = CINZA
        while pilha:
            no, caminho = pilha[-1]
            pendentes = [f for f in filhos.get(no, ()) if cor.get(f, BRANCO) != PRETO]
            proximo = next((f for f in pendentes if cor.get(f, BRANCO) == BRANCO), None)
            em_cinza = next((f for f in pendentes if cor.get(f) == CINZA), None)
            if em_cinza is not None:
                return caminho + [em_cinza]
            if proximo is None:
                cor[no] = PRETO
                pilha.pop()
                continue
            cor[proximo] = CINZA
            pilha.append((proximo, caminho + [proximo]))
    return None


# --------------------------------------------------------------------------
# Identidade do componente
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ComponentIdentity:
    """O que o catalogo interno sabe sobre UM componente.

    `brand_conflict` e' `gold.dim_produto_gobeauty.marca_conflitante`: a propria
    dimensao declarando que nao sabe a marca. 21 dos 4.705 produtos estao assim.
    Quando esta ligado, o componente e' recusado — resolver a referencia sob uma
    das marcas seria escolher por acidente.
    """

    brand: str | None
    ean: str | None = None
    brand_conflict: bool = False


@dataclass(frozen=True)
class ComponentCatalog:
    by_sku: Mapping[str, ComponentIdentity]

    @staticmethod
    def build(rows: Iterable[Mapping]) -> "ComponentCatalog":
        """`rows`: mapeamentos com `component_sku`/`sku`, `brand`/`marca`, `ean`.

        Chave repetida com identidade DIVERGENTE vira `brand_conflict=True` em
        vez de a ultima linha vencer.
        """
        acumulado: dict[str, ComponentIdentity] = {}
        for row in rows:
            sku = normalize_sku_key(row.get("component_sku") or row.get("sku"))
            if sku is None:
                continue
            identidade = ComponentIdentity(
                brand=normalize_brand_key(row.get("brand") or row.get("marca")),
                ean=consumer_ean_or_none(row.get("ean") or row.get("gtin")),
                brand_conflict=bool(row.get("brand_conflict")
                                    or row.get("marca_conflitante")),
            )
            anterior = acumulado.get(sku)
            if anterior is None:
                acumulado[sku] = identidade
            elif anterior != identidade:
                conflito = anterior.brand != identidade.brand
                acumulado[sku] = ComponentIdentity(
                    brand=anterior.brand,
                    ean=anterior.ean or identidade.ean,
                    brand_conflict=(anterior.brand_conflict
                                    or identidade.brand_conflict
                                    or conflito),
                )
        return ComponentCatalog(MappingProxyType(acumulado))

    def get(self, component_sku: object) -> ComponentIdentity | None:
        chave = normalize_sku_key(component_sku)
        return self.by_sku.get(chave) if chave else None


# --------------------------------------------------------------------------
# Referencia do componente
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ComponentReference:
    component_sku: str
    qty_per_kit: Decimal
    reference: Mapping | None
    amount: Decimal | None
    method: str | None
    reason: str | None

    @property
    def resolved(self) -> bool:
        return self.amount is not None


def _row_identity(ref: Mapping) -> object:
    """Identidade de uma linha de referencia, para comparar candidatos.

    `reference_row_id` e' a chave do snapshot. Quando falta — fixture de teste,
    por exemplo — cai para a tupla que define a linha para efeito de preco.
    """
    rid = ref.get("reference_row_id")
    if rid is not None:
        return ("row_id", str(rid).strip())
    return ("tupla",
            normalize_brand_key(ref.get("brand")),
            normalize_sku_key(ref.get("source_sku")),
            consumer_ean_or_none(ref.get("source_gtin")),
            str(ref.get("suggested_retail_amount")))


def _amount(ref: Mapping) -> Decimal | None:
    bruto = ref.get("suggested_retail_amount")
    if bruto is None:
        return None
    try:
        valor = Decimal(str(bruto))
    except (DecimalException, ValueError):
        return None
    if not valor.is_finite() or valor <= 0:
        return None
    return valor


def resolve_component_reference(
    component_sku: object,
    qty_per_kit: Decimal,
    *,
    identity: ComponentIdentity | None,
    index: ReferenceIndex,
) -> ComponentReference:
    """Resolve a referencia B2B de UM componente. Precedencia explicita.

    1. EAN de consumidor + marca, exato e unico;
    2. fallback: `(marca, source_sku)`, exato e unico.

    Recusa — e a recusa NAO cai para a chave seguinte — quando:

    - a marca e' ausente ou a dimensao a declara conflitante;
    - qualquer das duas chaves tem mais de um candidato;
    - as duas chaves resolvem para linhas de referencia DIFERENTES. Esse e' o
      caso "produtos incompativeis": o EAN diz um produto e o SKU diz outro, e
      preferir um dos dois seria decidir por ordem de precedencia um empate que
      e' na verdade um defeito de cadastro.
    """
    sku = normalize_sku_key(component_sku)
    vazio = ComponentReference(sku or "", qty_per_kit, None, None, None, None)

    if identity is None:
        return _com_motivo(vazio, REASON_COMPONENT_NOT_IN_CATALOG)
    if identity.brand_conflict:
        return _com_motivo(vazio, REASON_COMPONENT_BRAND_CONFLICT)
    marca = normalize_brand_key(identity.brand)
    if marca is None:
        return _com_motivo(vazio, REASON_COMPONENT_BRAND_MISSING)

    ean = consumer_ean_or_none(identity.ean)
    por_ean = list(index.by_gtin.get((marca, ean), ())) if ean else []
    por_sku = list(index.by_sku.get((marca, sku), ())) if sku else []

    if len(por_ean) > 1 or len(por_sku) > 1:
        return _com_motivo(vazio, REASON_COMPONENT_REFERENCE_AMBIGUOUS)

    if len(por_ean) == 1 and len(por_sku) == 1:
        if _row_identity(por_ean[0]) != _row_identity(por_sku[0]):
            return _com_motivo(vazio, REASON_COMPONENT_REFERENCE_INCOMPATIBLE)

    escolhida, metodo = (por_ean[0], COMPONENT_MATCH_EAN) if por_ean else (
        (por_sku[0], COMPONENT_MATCH_SKU) if por_sku else (None, None))
    if escolhida is None:
        return _com_motivo(vazio, REASON_COMPONENT_REFERENCE_MISSING)

    valor = _amount(escolhida)
    if valor is None:
        return _com_motivo(vazio, REASON_COMPONENT_REFERENCE_MISSING)

    return ComponentReference(sku or "", qty_per_kit, escolhida, valor, metodo, None)


def _com_motivo(base: ComponentReference, motivo: str) -> ComponentReference:
    return ComponentReference(base.component_sku, base.qty_per_kit,
                              None, None, None, motivo)


# --------------------------------------------------------------------------
# Referencia do kit
# --------------------------------------------------------------------------

def discount_for_units(units: Decimal) -> Decimal:
    """Faixa pela quantidade total de UNIDADES.

    2 -> 5%, 3 -> 10%, 4 ou mais -> 15%. Abaixo de 2, nenhum desconto: um
    "kit" de uma unidade nao e' combo.

    O teste e' por limiar (`>=`) e nao por igualdade porque `qty_per_kit` e'
    `numeric` na fonte. Com os valores medidos (1 a 4, inteiros) os dois
    criterios coincidem; com soma fracionaria, o limiar mantem a faixa
    monotonica em vez de cair para zero.
    """
    for piso, pct in _DISCOUNT_TIERS:
        if units >= piso:
            return pct
    return Decimal(0)


@dataclass(frozen=True)
class KitBridge:
    """A ponte oferta -> kit Protheus."""

    kit_sku: str | None
    status: str
    brand: str | None = None
    kit_brand: str | None = None
    method: str | None = None


#: Metodos de ponte que o resolvedor sabe produzir. Todos sao IGUALDADE EXATA.
BRIDGE_METHOD_SKU_IS_KIT = "seller_sku_is_protheus_kit"
BRIDGE_METHOD_BRAND_CODE = "brand_code_exact"
BRIDGE_METHOD_ALIAS = "alias_code_exact"


@dataclass(frozen=True)
class KitBridgeIndex:
    """Pontes cadastrais oferta -> kit Protheus, montadas do catalogo interno.

    Existe para que o runtime NAO dependa do CSV analitico do KITS-MAP-1. O CSV
    e' diagnostico: ele carrega `CANDIDATE_REVIEW`, que nasce de composicao
    empirica de NF e nunca pode virar configuracao. Este indice so' conhece
    igualdade exata sobre codigo cadastrado.
    """

    #: `(marca, codigo)` -> codigos Protheus alcancados.
    by_brand_code: Mapping[tuple[str, str], frozenset]
    #: alias (codigo Bling/Tiny/Shopify/Omie, `sku_antigo`) -> codigos Protheus.
    by_alias: Mapping[str, frozenset]
    #: codigo Protheus do kit -> marca do kit.
    brand_of_kit: Mapping[str, str]

    @staticmethod
    def build(*, brand_code_rows: Iterable[Mapping] = (),
              alias_rows: Iterable[Mapping] = (),
              kit_brand_rows: Iterable[Mapping] = ()) -> "KitBridgeIndex":
        """Cada `row` traz `code`/`codigo`, `protheus_sku` e, quando couber, `brand`."""
        por_marca: dict[tuple[str, str], set[str]] = {}
        for row in brand_code_rows:
            marca = normalize_brand_key(row.get("brand") or row.get("marca"))
            codigo = normalize_sku_key(row.get("code") or row.get("codigo"))
            alvo = normalize_sku_key(row.get("protheus_sku"))
            if marca and codigo and alvo:
                por_marca.setdefault((marca, codigo), set()).add(alvo)

        por_alias: dict[str, set[str]] = {}
        for row in alias_rows:
            codigo = normalize_sku_key(row.get("code") or row.get("codigo"))
            alvo = normalize_sku_key(row.get("protheus_sku"))
            if codigo and alvo:
                por_alias.setdefault(codigo, set()).add(alvo)

        marca_kit: dict[str, str] = {}
        for row in kit_brand_rows:
            alvo = normalize_sku_key(row.get("protheus_sku") or row.get("sku"))
            marca = normalize_brand_key(row.get("brand") or row.get("marca"))
            if alvo and marca:
                marca_kit.setdefault(alvo, marca)

        return KitBridgeIndex(
            MappingProxyType({k: frozenset(v) for k, v in por_marca.items()}),
            MappingProxyType({k: frozenset(v) for k, v in por_alias.items()}),
            MappingProxyType(marca_kit),
        )


def resolve_bridge(seller_sku: object, brand: object, *,
                   bom: KitBomIndex, index: KitBridgeIndex) -> KitBridge:
    """Classifica a ponte de UMA oferta. So' igualdade exata, zero heuristica.

    Precedencia: o proprio SKU ser um kit Protheus -> `(marca, codigo)` ->
    alias. Candidatos de TODOS os metodos sao unidos antes de decidir: dois
    metodos apontando para kits diferentes e' ambiguidade, nao precedencia.

    `CANDIDATE_REVIEW` nao e' produzido aqui e nunca sera: ele descreve
    evidencia empirica, que este gate proibiu de virar fato.
    """
    sku = normalize_sku_key(seller_sku)
    marca = normalize_brand_key(brand)
    if sku is None:
        return KitBridge(None, BRIDGE_UNMAPPED, marca, None, None)

    candidatos: dict[str, str] = {}
    if sku in bom.components_by_kit:
        candidatos[sku] = BRIDGE_METHOD_SKU_IS_KIT
    if marca is not None:
        for alvo in index.by_brand_code.get((marca, sku), ()):
            if alvo in bom.components_by_kit:
                candidatos.setdefault(alvo, BRIDGE_METHOD_BRAND_CODE)
    for alvo in index.by_alias.get(sku, ()):
        if alvo in bom.components_by_kit:
            candidatos.setdefault(alvo, BRIDGE_METHOD_ALIAS)

    if not candidatos:
        return KitBridge(None, BRIDGE_UNMAPPED, marca, None, None)
    if len(candidatos) > 1:
        return KitBridge("|".join(sorted(candidatos)), BRIDGE_AMBIGUOUS,
                         marca, None, None)

    alvo, metodo = next(iter(candidatos.items()))
    marca_kit = index.brand_of_kit.get(alvo)

    if marca is not None and marca_kit is not None and marca != marca_kit:
        return KitBridge(alvo, BRIDGE_CROSS_BRAND_CONFLICT, marca, marca_kit, metodo)

    if marca is None or marca_kit is None:
        # Codigo casa, marca NAO esta provada. Isso e' candidato, nao fato: as
        # chaves de kit vindas de `apice_sheet` vivem no espaco de codigo do
        # Bling e nao tem no' no `dim_produto` de onde tirar marca.
        return KitBridge(alvo, BRIDGE_CANDIDATE_REVIEW, marca, marca_kit, metodo)

    status = (BRIDGE_EXACT_DIRECT
              if metodo in (BRIDGE_METHOD_SKU_IS_KIT, BRIDGE_METHOD_BRAND_CODE)
              else BRIDGE_EXACT_ALIAS)
    return KitBridge(alvo, status, marca, marca_kit, metodo)


def is_promotable(bridge: KitBridge) -> tuple[bool, str | None]:
    """Allowlist + mesma marca PROVADA. Devolve `(pode, motivo_da_recusa)`.

    "Mesma marca" exige as DUAS marcas conhecidas e iguais. Marca do kit
    desconhecida nao e' "sem conflito": e' ausencia de prova, e ausencia de
    prova bloqueia. Medido: `40126`–`40129` sao chaves de kit da planilha da
    Apice (`source = 'apice_sheet'`) em codigo Bling, anunciadas em loja
    Kokeshi. Com a checagem fail-open elas passavam por `EXACT_DIRECT` e so'
    nao viravam preco porque os componentes faltavam no catalogo — isto e',
    estavam bloqueadas por acidente, nao por regra.
    """
    if bridge.status not in PROMOTABLE_BRIDGE_STATUSES:
        return False, REASON_BRIDGE_NOT_PROMOTABLE
    if not normalize_sku_key(bridge.kit_sku):
        return False, REASON_BRIDGE_NOT_PROMOTABLE
    oferta = normalize_brand_key(bridge.brand)
    kit = normalize_brand_key(bridge.kit_brand)
    if oferta is None or kit is None or oferta != kit:
        return False, REASON_BRIDGE_BRAND_MISMATCH
    return True, None


@dataclass(frozen=True)
class KitReference:
    """Resultado. `amount is None` SEMPRE vem com `reason` preenchido."""

    kit_sku: str | None
    amount: Decimal | None
    components_base: Decimal | None
    units: Decimal | None
    discount_pct: Decimal | None
    reason: str | None
    components: tuple[ComponentReference, ...] = ()

    @property
    def component_count(self) -> int:
        return len(self.components)


def resolve_kit_reference(
    bridge: KitBridge,
    *,
    bom: KitBomIndex,
    catalog: ComponentCatalog,
    index: ReferenceIndex,
) -> KitReference:
    """Referencia do kit, ou `None` com o motivo exato.

    Nunca devolve zero por ausencia: zero e' um preco, ausencia e' `None`.
    """
    pode, motivo = is_promotable(bridge)
    if not pode:
        return KitReference(normalize_sku_key(bridge.kit_sku), None, None,
                            None, None, motivo)

    kit = normalize_sku_key(bridge.kit_sku)
    componentes = bom.components(kit)
    if not componentes:
        return KitReference(kit, None, None, None, None, REASON_BOM_ABSENT)

    resolvidos = tuple(
        resolve_component_reference(c.component_sku, c.qty_per_kit,
                                    identity=catalog.get(c.component_sku),
                                    index=index)
        for c in componentes
    )
    unidades = sum((c.qty_per_kit for c in componentes), Decimal(0))
    pct = discount_for_units(unidades)

    faltantes = [c for c in resolvidos if not c.resolved]
    if faltantes:
        # Motivo do PRIMEIRO componente irresolvido na ordem do kit. Um motivo
        # so' porque a tela publica um: a lista completa sai na reconciliacao.
        return KitReference(kit, None, None, unidades, pct,
                            faltantes[0].reason, resolvidos)

    base = sum((c.qty_per_kit * c.amount for c in resolvidos), Decimal(0))
    valor = (base * (Decimal(1) - pct)).quantize(_CENTS, rounding=ROUND_HALF_UP)
    return KitReference(kit, valor, base.quantize(_CENTS, rounding=ROUND_HALF_UP),
                        unidades, pct, None, resolvidos)


def resolve_many(
    bridges: Sequence[KitBridge],
    *,
    bom: KitBomIndex,
    catalog: ComponentCatalog,
    index: ReferenceIndex,
) -> tuple[KitReference, ...]:
    return tuple(resolve_kit_reference(b, bom=bom, catalog=catalog, index=index)
                 for b in bridges)
