"""Estoque Full do ML: indicadores por INVENTARIO (logica pura, sem I/O).

Entrada: a fotografia publicada pela DAG do airflow
`mercadolivre_full_stock_snapshot` (tabelas `raw.ml_full_*`, ainda NAO
criadas) mais as vendas Full pagas dos ultimos 28 dias.

Regras que nao se negociam
--------------------------
- O grao e' o INVENTARIO (marca, conta, inventory_id). Estoque nunca e'
  somado por anuncio: 74,5% das unidades Full vendidas em 28 dias (medido em
  16/10/2026) sairam de anuncios que dividem o inventario com outro.
- Velocidade de um inventario = vendas Full de TODOS os anuncios/variacoes
  ligados a ele na ponte, INCLUSIVE inativos e nao-Full hoje (rituaria: 29,8%
  das unidades vieram de anuncio hoje inativo/nao-Full).
- Venda que nao se liga a exatamente um inventario NAO e' rateada: fica em
  contadores de "nao alocada" e aparece no resumo. Inventar rateio esconderia
  o buraco da ponte.
- `inventory_id` nao prova Full: so' entra na classificacao operacional o
  inventario com pelo menos um anuncio `logistic_type = fulfillment`.
- Leitura sem OK: SEM_LEITURA, quantidades nulas. Ausencia nunca vira zero.
- `transfer` e' movimento INTERNO do ML. Nao e' remessa do operador ao Full.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

#: Janela de demanda em dias COMPLETOS [ref_date - 28, ref_date), igual a' da
#: Shopee FBS e a' de `gold.tiktok_inventory_daily`.
JANELA_VENDAS_DIAS = 28

#: 🔴 PROVISORIOS, herdados da Shopee FBS (que herdou do TikTok). Nenhum dono de
#: numero ratificou estes limites para o ML. Viajam no resumo como provisorios.
COBERTURA_BAIXA_DIAS_PROVISORIO = 7
COBERTURA_EXCESSO_DIAS_PROVISORIO = 90

ROTULO_FULL = "fulfillment"
STATUS_TRANSFER = "transfer"

CLASSES = (
    "RUPTURA_CANDIDATA", "BAIXO_CANDIDATO", "EXCESSO_CANDIDATO", "SEM_GIRO_CANDIDATO",
    "SUFICIENTE", "SEM_DEMANDA_MEDIDA",
    "SEM_LEITURA",          # inventario nao lido: nada a afirmar
    "FORA_DO_FULL",         # inventario sem nenhum anuncio Full: contexto, nao operacao
)
CLASSES_OPERACIONAIS = CLASSES[:6]


def classificar(disponivel: int, unidades_28d: int, cobertura: Decimal | None) -> str:
    """Mesma ordem da Shopee FBS. Ruptura exige demanda."""
    if unidades_28d == 0:
        return "SEM_GIRO_CANDIDATO" if disponivel > 0 else "SEM_DEMANDA_MEDIDA"
    if disponivel == 0:
        return "RUPTURA_CANDIDATA"
    if cobertura is not None and cobertura < COBERTURA_BAIXA_DIAS_PROVISORIO:
        return "BAIXO_CANDIDATO"
    if cobertura is not None and cobertura >= COBERTURA_EXCESSO_DIAS_PROVISORIO:
        return "EXCESSO_CANDIDATO"
    return "SUFICIENTE"


def cobertura_dias(disponivel: int, unidades_28d: int) -> Decimal | None:
    """disponivel / media diaria. Nula sem demanda (nunca zero, nunca infinito)."""
    if unidades_28d <= 0:
        return None
    media = Decimal(unidades_28d) / Decimal(JANELA_VENDAS_DIAS)
    return (Decimal(disponivel) / media).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _var(v) -> str:
    return "0" if v in (None, "", 0, "0") else str(v)


def indicadores(snapshot, status, ponte, vendas):
    """Indicadores por inventario + resumo por marca. So' dados, sem I/O.

    snapshot: dicts com brand, seller_id, inventory_id, read_status,
              available_quantity, not_available_quantity, total_coerente
    status:   dicts com brand, seller_id, inventory_id, status_raw, quantity
    ponte:    dicts com brand, seller_id, item_id, variation_id, inventory_id,
              logistic_type, listing_status
    vendas:   {(brand, item_id, variation_id): unidades Full pagas na janela}
    """
    chave = lambda r: (r["brand"], r["seller_id"], r["inventory_id"])

    status_por_inv = defaultdict(dict)
    for s in status:
        status_por_inv[chave(s)][s["status_raw"]] = (
            status_por_inv[chave(s)].get(s["status_raw"], 0) + int(s["quantity"]))

    anuncios_por_inv = defaultdict(list)
    inv_por_var = defaultdict(set)       # (brand, item, variacao) -> inventarios
    inv_por_item = defaultdict(set)      # (brand, item) -> inventarios
    for p in ponte:
        if not p.get("inventory_id"):
            continue
        k = chave(p)
        anuncios_por_inv[k].append(p)
        inv_por_var[(p["brand"], p["item_id"], _var(p.get("variation_id")))].add(k)
        inv_por_item[(p["brand"], p["item_id"])].add(k)
    itens_na_ponte = {(p["brand"], p["item_id"]) for p in ponte}

    # --- alocacao das vendas (sem rateio) ----------------------------------
    vel = defaultdict(int)
    nao_alocada = defaultdict(lambda: defaultdict(int))
    for (brand, item, variacao), un in vendas.items():
        un = int(un or 0)
        if un <= 0:
            continue
        alvo = inv_por_var.get((brand, item, _var(variacao)))
        if not alvo and _var(variacao) == "0":
            # Venda sem variacao: so' casa por item se o item tem 1 inventario.
            alvo = inv_por_item.get((brand, item))
        if alvo and len(alvo) == 1:
            vel[next(iter(alvo))] += un
        elif alvo:
            nao_alocada[brand]["item_com_varios_inventarios"] += un
        elif (brand, item) in itens_na_ponte:
            nao_alocada[brand]["anuncio_sem_inventory_id"] += un
        else:
            nao_alocada[brand]["anuncio_fora_da_ponte"] += un

    # --- linha por inventario -----------------------------------------------
    linhas = []
    for s in snapshot:
        k = chave(s)
        anuncios = anuncios_por_inv.get(k, [])
        eh_full = any(a.get("logistic_type") == ROTULO_FULL for a in anuncios)
        ok = s.get("read_status") == "OK"
        disp = s.get("available_quantity") if ok else None
        nao_disp = s.get("not_available_quantity") if ok else None
        st = status_por_inv.get(k, {}) if ok else {}
        un = vel.get(k, 0)
        cob = cobertura_dias(disp, un) if ok else None
        if not ok:
            classe = "SEM_LEITURA"
        elif not eh_full:
            classe = "FORA_DO_FULL"
        else:
            classe = classificar(disp, un, cob)
        linhas.append({
            "brand": s["brand"], "seller_id": s["seller_id"], "inventory_id": s["inventory_id"],
            "read_status": s.get("read_status"),
            "disponivel": disp, "nao_disponivel": nao_disp,
            "transfer": st.get(STATUS_TRANSFER, 0) if ok else None,
            "nao_disponivel_por_status": dict(sorted(st.items())) if ok else None,
            "total_coerente": s.get("total_coerente"),
            "anuncios": len(anuncios),
            "anuncios_ativos": sum(1 for a in anuncios if a.get("listing_status") == "active"),
            "anuncios_full": sum(1 for a in anuncios if a.get("logistic_type") == ROTULO_FULL),
            "eh_full": eh_full,
            "unidades_28d": un,
            "media_diaria_28d": (Decimal(un) / Decimal(JANELA_VENDAS_DIAS)).quantize(Decimal("0.01")) if un else Decimal("0"),
            "cobertura_torre_dias": cob,
            "classificacao_torre": classe,
            "vinculo_vendas": "COM_VENDA" if un > 0 else "SEM_VENDA_NA_JANELA",
        })

    # --- resumo por marca -----------------------------------------------------
    resumo = {}
    for brand in sorted({l["brand"] for l in linhas} | set(nao_alocada)):
        ls = [l for l in linhas if l["brand"] == brand]
        full_ok = [l for l in ls if l["eh_full"] and l["read_status"] == "OK"]
        por_classe = defaultdict(int)
        for l in ls:
            por_classe[l["classificacao_torre"]] += 1
        alocadas = sum(l["unidades_28d"] for l in ls)
        perdidas = dict(nao_alocada.get(brand, {}))
        resumo[brand] = {
            "inventarios": len(ls),
            "inventarios_full_lidos": len(full_ok),
            "inventarios_compartilhados": sum(1 for l in ls if l["anuncios"] >= 2),
            "disponivel_full": sum(l["disponivel"] for l in full_ok) if full_ok else None,
            "nao_disponivel_full": sum(l["nao_disponivel"] for l in full_ok) if full_ok else None,
            "transfer_full": sum(l["transfer"] for l in full_ok) if full_ok else None,
            "por_classificacao": dict(sorted(por_classe.items())),
            "unidades_28d_alocadas": alocadas,
            "unidades_28d_nao_alocadas": perdidas,
            "cobertura_da_alocacao": (
                round(alocadas / (alocadas + sum(perdidas.values())), 4)
                if (alocadas + sum(perdidas.values())) else None),
        }
    return linhas, resumo
