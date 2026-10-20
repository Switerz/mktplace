"""Remessa do operador logistico (Eli) para o Full do ML, via NF do Bling.

Fato SEPARADO do estoque Full (que vem da API do ML). Logica pura, sem I/O.

O que e' uma remessa para o Full aqui
--------------------------------------
Item de NF de SAIDA, com CFOP de "outras saidas" (5949 dentro do estado,
6949 fora) e destinatario cujo DOCUMENTO esta' na lista de documentos do
grupo Mercado Livre CONFIRMADOS. A lista nasce VAZIA de proposito: o
documento do destinatario ainda nao foi medido no Data Mart, e casar pelo
nome ("EBAZAR") e' indicio, nao prova. Ate' la', o maximo que uma NF alcanca
e' `REMESSA_ML_DESTINATARIO_NAO_CONFIRMADO`.

O que NUNCA acontece
--------------------
- Venda (CFOP 5.1xx/6.1xx/5.4xx/6.4xx) nunca vira remessa, mesmo que o
  destinatario seja do grupo ML.
- Remessa nunca e' somada com venda: as classes saem separadas.
- Nenhum vinculo com `inbound_reception` / `inventory_id` e' afirmado por
  SKU, quantidade ou proximidade de data. Este modulo nao tenta.
- Conta do Bling nao vira marca sem de-para confirmado.
- `transfer` (movimento interno do ML) nao tem nada a ver com esta NF.
"""
from __future__ import annotations

import re
from collections import defaultdict

CFOP_OUTRAS_SAIDAS = frozenset({"5949", "6949"})
#: Prefixos de venda de mercadoria (inclui ST: 54xx/64xx).
PREFIXOS_VENDA = ("51", "61", "54", "64")
#: Indicio por nome (NUNCA suficiente sozinho).
_NOME_GRUPO_ML = re.compile(r"\bEBAZAR\b|MERCADO\s*LIVRE|MERCADO\s*ENVIOS|MERCADOLIVRE", re.I)

#: Documentos (so' digitos) do grupo ML confirmados como destinatario de
#: remessa ao Full. VAZIA ate' gate de medicao + confirmacao.
DOCUMENTOS_ML_CONFIRMADOS: frozenset[str] = frozenset()

#: Conta do Bling -> marca da Torre. So' entra o que esta' confirmado.
#: `bb_varejo_ltda` (provavelmente barbours) fica de fora ate' confirmacao.
CONTA_PARA_MARCA = {"kokeshi_mkt_place": "kokeshi", "lescent": "lescent", "rituaria": "rituaria"}

REMESSA_FULL_ML = "REMESSA_FULL_ML"
REMESSA_ML_NAO_CONFIRMADO = "REMESSA_ML_DESTINATARIO_NAO_CONFIRMADO"
VENDA = "VENDA"
OUTRA_OPERACAO = "OUTRA_OPERACAO"
NAO_E_SAIDA = "NAO_E_SAIDA"
CFOP_INVALIDO = "CFOP_INVALIDO"
CLASSES = (REMESSA_FULL_ML, REMESSA_ML_NAO_CONFIRMADO, VENDA, OUTRA_OPERACAO, NAO_E_SAIDA, CFOP_INVALIDO)


def so_digitos(valor) -> str:
    return re.sub(r"\D", "", valor or "") if isinstance(valor, str) else ""


def classificar_item(*, tipo, cfop, documento, nome, confirmados=DOCUMENTOS_ML_CONFIRMADOS) -> str:
    """Classe de UM item de NF. Ordem deliberada: venda sai antes de qualquer remessa."""
    c = so_digitos(cfop)
    if len(c) != 4:
        return CFOP_INVALIDO
    if str(tipo) != "1":                       # Bling: 1 = saida
        return NAO_E_SAIDA
    if c.startswith(PREFIXOS_VENDA):
        return VENDA
    if c in CFOP_OUTRAS_SAIDAS:
        if so_digitos(documento) in confirmados:
            return REMESSA_FULL_ML
        if isinstance(nome, str) and _NOME_GRUPO_ML.search(nome):
            return REMESSA_ML_NAO_CONFIRMADO
    return OUTRA_OPERACAO


def _quantidade(v):
    try:
        q = float(v)
    except (TypeError, ValueError):
        return None
    return q if q == q and q >= 0 else None


def agregar(itens, confirmados=DOCUMENTOS_ML_CONFIRMADOS) -> dict:
    """Agregado por conta x classe x situacao. Nunca soma classes entre si.

    itens: dicts com conta, nf_id, data (date), situacao, tipo, cfop, documento,
           nome, sku, quantidade. Documento, nome e SKU NAO saem do agregado.
    """
    acc = defaultdict(lambda: {"nfs": set(), "itens": 0, "unidades": 0.0,
                               "itens_quantidade_invalida": 0, "skus": set(),
                               "primeira": None, "ultima": None})
    for it in itens:
        classe = classificar_item(tipo=it.get("tipo"), cfop=it.get("cfop"),
                                  documento=it.get("documento"), nome=it.get("nome"),
                                  confirmados=confirmados)
        k = (it.get("conta"), classe, str(it.get("situacao")))
        a = acc[k]
        a["nfs"].add(it.get("nf_id"))
        a["itens"] += 1
        q = _quantidade(it.get("quantidade"))
        if q is None:
            a["itens_quantidade_invalida"] += 1   # ausente nao vira zero: e' contada a parte
        else:
            a["unidades"] += q
        if it.get("sku"):
            a["skus"].add(it["sku"])
        d = it.get("data")
        if d is not None:
            a["primeira"] = d if a["primeira"] is None else min(a["primeira"], d)
            a["ultima"] = d if a["ultima"] is None else max(a["ultima"], d)

    saida = defaultdict(dict)
    for (conta, classe, situacao), a in sorted(acc.items(), key=lambda kv: tuple(map(str, kv[0]))):
        saida[conta].setdefault(classe, {})[situacao] = {
            "nfs": len(a["nfs"]), "itens": a["itens"], "unidades": a["unidades"],
            "itens_quantidade_invalida": a["itens_quantidade_invalida"],
            "skus_distintos": len(a["skus"]),
            "primeira": str(a["primeira"]) if a["primeira"] else None,
            "ultima": str(a["ultima"]) if a["ultima"] else None,
        }
    return {conta: {"marca_torre": CONTA_PARA_MARCA.get(conta), "por_classe": classes}
            for conta, classes in saida.items()}
