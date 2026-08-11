"""
Gate SD1 — layout HORÁRIO do shop-stats Shopee (export de 10/08/2026).

Contrato comprovado por inspeção agregada dos 5 arquivos reais (uma marca
por arquivo), sem copiar nenhum dado real para cá — todos os fixtures abaixo
são sintéticos:

  - Row 1 traz UMA linha de total do período, cobrindo um único dia;
  - Rows 4+ trazem 24 linhas horárias 'DD/MM/YYYY HH:00', horas 00..23;
  - a representação diária vem da LINHA DE TOTAL, nunca de uma hora isolada;
  - a soma das 24 horas reconcilia os campos comprovadamente ADITIVOS
    (Vendas, Vendas Canceladas, Vendas Devolvidas/Reembolsadas, Pedidos) e
    serve apenas como validação;
  - Visitantes (únicos deduplicados no dia) e Taxa de Conversão (razão)
    NUNCA são somados: vêm do total.

O layout diário histórico continua idêntico e é reexercitado aqui.
"""
from __future__ import annotations

import openpyxl
import pytest

from pipelines.connectors.shopee import _parser_shop_stats as sps
from pipelines.transforms import shopee_shop_stats_daily as transform_mod

MARCAS = ("apice", "barbours", "kokeshi", "lescent", "rituaria")

HOURLY_HEADER = [
    "Tempo",
    "Vendas (BRL)",
    "Vendas Sem os Descontos da Shopee",
    "Pedidos",
    "Vendas por Pedido",
    "Cliques Por Produto",
    "Visitantes",
    "Taxa de Conversão de Pedidos",
    "Pedidos Cancelados",
    "Vendas Canceladas",
    "Pedidos Devolvidos / Reembolsados",
    "Vendas Devolvidas / Reembolsadas",
]

DAILY_HEADER = [
    "Data", "Visitantes", "Taxa de Conversão de Pedidos",
    "# de compradores", "# de novos compradores", "# de compradores existentes",
    "Repetir Índice de Compras",
    "Vendas (BRL)", "Vendas Canceladas", "Vendas Devolvidas / Reembolsadas",
]

DIA = "10/08/2026"


def _hora(h, *, dia=DIA, vendas="100.00", cancel="10.00", devol="1.00",
          pedidos="2", visitantes="50", conv="1,50%"):
    """Uma linha horária sintética."""
    return [
        f"{dia} {h:02d}:00", vendas, vendas, pedidos, "50.00", "20",
        visitantes, conv, "0", cancel, "0", devol,
    ]


def _total(*, dia=DIA, vendas="2400.00", cancel="240.00", devol="24.00",
           pedidos="48", visitantes="900", conv="2,00%", tempo=None):
    """Linha de total do período. Os defaults reconciliam com 24x _hora()."""
    return [
        tempo if tempo is not None else f"{dia}-{dia}",
        vendas, vendas, pedidos, "50.00", "480",
        visitantes, conv, "0", cancel, "0", devol,
    ]


def _escreve(path, header, total_row, detail_rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)                          # row 0
    ws.append(total_row)                       # row 1: total do período
    ws.append([None] * len(header))            # row 2: separador
    ws.append(header)                          # row 3: header de detalhe
    for r in detail_rows:
        ws.append(r)
    wb.save(path)


def _parse_hourly(tmp_path, *, brand="apice", total_row=None, horas=None,
                  header=None):
    brand_dir = tmp_path / brand
    brand_dir.mkdir(exist_ok=True)
    _escreve(
        brand_dir / f"{brand}.shopee-shop-stats.20260810-20260810.xlsx",
        header or HOURLY_HEADER,
        total_row if total_row is not None else _total(),
        horas if horas is not None else [_hora(h) for h in range(24)],
    )
    return sps.parse_brand_shop_stats(tmp_path, brand)


# ---------------------------------------------------------------------------
# 1. Layout diário histórico continua aceito, sem mudança de comportamento
# ---------------------------------------------------------------------------
def test_layout_diario_historico_continua_aceito(tmp_path):
    brand_dir = tmp_path / "apice"
    brand_dir.mkdir()
    _escreve(
        brand_dir / "apice.shopee-shop-stats.20260101-20260131.xlsx",
        DAILY_HEADER,
        ["01/01/2026-31/01/2026"] + [None] * (len(DAILY_HEADER) - 1),
        [["01/01/2026", "100", "1,50%", "10", "5", "5", "5,00%",
          "1000.00", "100.00", "50.00"]],
    )
    out = sps.parse_brand_shop_stats(tmp_path, "apice")
    assert len(out) == 1
    assert out[0]["gmv"] == 850.0
    assert out[0]["visitors"] == 100
    assert out[0]["unique_buyers"] == 10


# ---------------------------------------------------------------------------
# 2. Layout horário válido produz UMA única linha diária
# ---------------------------------------------------------------------------
def test_layout_horario_produz_uma_unica_linha_diaria(tmp_path):
    out = _parse_hourly(tmp_path)
    assert len(out) == 1
    assert out[0]["date"].isoformat() == "2026-08-10"
    # 2400 - 240 - 24
    assert out[0]["gmv"] == 2136.0


def test_layout_horario_uma_linha_por_loja_data_apos_o_transform(tmp_path):
    out = _parse_hourly(tmp_path)
    canon = transform_mod.transform_batch(out)
    chaves = [(r["date"], r["loja_id"], r["marketplace_id"]) for r in canon]
    assert len(chaves) == 1
    assert len(set(chaves)) == 1


# ---------------------------------------------------------------------------
# 3. A linha total é a representação diária (não a soma, não uma hora)
# ---------------------------------------------------------------------------
def test_visitantes_vem_do_total_e_nunca_da_soma_horaria(tmp_path):
    # 24 horas x 50 visitantes = 1200 na soma; o total declara 900 (únicos).
    out = _parse_hourly(tmp_path, total_row=_total(visitantes="900"))
    assert out[0]["visitors"] == 900


def test_financeiro_vem_do_total(tmp_path):
    out = _parse_hourly(tmp_path)
    assert out[0]["sales_brl"] == 2400.0
    assert out[0]["cancelled_sales_brl"] == 240.0
    assert out[0]["refunded_sales_brl"] == 24.0


# ---------------------------------------------------------------------------
# 4. Soma horária reconcilia os campos aditivos
# ---------------------------------------------------------------------------
def test_soma_horaria_reconcilia_campos_aditivos(tmp_path):
    # Valores por hora variados, total = soma exata.
    horas = [_hora(h, vendas="10.00", cancel="1.00", devol="0.50", pedidos="1")
             for h in range(24)]
    total = _total(vendas="240.00", cancel="24.00", devol="12.00", pedidos="24")
    out = _parse_hourly(tmp_path, total_row=total, horas=horas)
    assert out[0]["gmv"] == 204.0


# ---------------------------------------------------------------------------
# 5. Taxa/conversão nunca é somada
# ---------------------------------------------------------------------------
def test_conversao_vem_do_total_e_nao_e_somada(tmp_path):
    # 24 x 1,50% somaria 36,0; o total declara 2,00%.
    out = _parse_hourly(tmp_path, total_row=_total(conv="2,00%"))
    assert out[0]["conversion_rate"] == 2.0


# ---------------------------------------------------------------------------
# 6. A última hora nunca substitui o total
# ---------------------------------------------------------------------------
def test_ultima_hora_nunca_vence_o_total(tmp_path):
    horas = [_hora(h, vendas="100.00", cancel="10.00", devol="1.00", pedidos="2")
             for h in range(23)]
    # A hora 23 é deliberadamente diferente; o total continua sendo a soma.
    horas.append(_hora(23, vendas="0.00", cancel="0.00", devol="0.00",
                       pedidos="0", visitantes="0", conv="0,00%"))
    total = _total(vendas="2300.00", cancel="230.00", devol="23.00", pedidos="46")
    out = _parse_hourly(tmp_path, total_row=total, horas=horas)
    assert out[0]["sales_brl"] == 2300.0          # total, não 0.00 da hora 23
    assert out[0]["gmv"] == 2047.0
    assert out[0]["visitors"] == 900              # total, não 0 da hora 23


# ---------------------------------------------------------------------------
# 7-9. Estrutura horária inválida bloqueia
# ---------------------------------------------------------------------------
def test_hora_duplicada_bloqueia(tmp_path):
    horas = [_hora(h) for h in range(23)] + [_hora(22)]
    with pytest.raises(sps.ShopeeShopStatsError, match="hora duplicada"):
        _parse_hourly(tmp_path, horas=horas)


def test_hora_ausente_bloqueia(tmp_path):
    horas = [_hora(h) for h in range(23)]  # falta a hora 23
    with pytest.raises(sps.ShopeeShopStatsError, match="24 horas"):
        _parse_hourly(tmp_path, horas=horas)


def test_mais_de_uma_data_nas_horas_bloqueia(tmp_path):
    horas = [_hora(h) for h in range(23)] + [_hora(23, dia="11/08/2026")]
    with pytest.raises(sps.ShopeeShopStatsError, match="mais de uma data"):
        _parse_hourly(tmp_path, horas=horas)


def test_minuto_diferente_de_zero_bloqueia(tmp_path):
    horas = [_hora(h) for h in range(23)]
    linha = _hora(23)
    linha[0] = f"{DIA} 23:30"
    horas.append(linha)
    with pytest.raises(sps.ShopeeShopStatsError, match="formato"):
        _parse_hourly(tmp_path, horas=horas)


# ---------------------------------------------------------------------------
# 10-11. Linha de total ausente ou divergente bloqueia
# ---------------------------------------------------------------------------
def test_total_do_periodo_ausente_bloqueia(tmp_path):
    total = _total(tempo="")  # sem range válido
    with pytest.raises(sps.ShopeeShopStatsError, match="total do período"):
        _parse_hourly(tmp_path, total_row=total)


def test_total_cobrindo_mais_de_um_dia_bloqueia(tmp_path):
    total = _total(tempo="09/08/2026-10/08/2026")
    with pytest.raises(sps.ShopeeShopStatsError, match="mais de um dia"):
        _parse_hourly(tmp_path, total_row=total)


def test_total_divergente_da_soma_aditiva_bloqueia(tmp_path):
    # Soma horária = 2400; total declara 9999 → contradição.
    total = _total(vendas="9999.00")
    with pytest.raises(sps.ShopeeShopStatsError, match="não reconcilia"):
        _parse_hourly(tmp_path, total_row=total)


def test_total_de_pedidos_divergente_bloqueia(tmp_path):
    total = _total(pedidos="999")
    with pytest.raises(sps.ShopeeShopStatsError, match="não reconcilia"):
        _parse_hourly(tmp_path, total_row=total)


# ---------------------------------------------------------------------------
# 12. Financeiro inválido/negativo bloqueia
# ---------------------------------------------------------------------------
def test_financeiro_negativo_no_total_bloqueia(tmp_path):
    horas = [_hora(h, vendas="-100.00") for h in range(24)]
    total = _total(vendas="-2400.00")
    with pytest.raises(sps.ShopeeShopStatsError, match="negativo"):
        _parse_hourly(tmp_path, total_row=total, horas=horas)


def test_coluna_financeira_ausente_no_layout_horario_bloqueia(tmp_path):
    header = [c for c in HOURLY_HEADER if c != "Vendas Canceladas"]
    total = [v for c, v in zip(HOURLY_HEADER, _total()) if c != "Vendas Canceladas"]
    horas = [[v for c, v in zip(HOURLY_HEADER, _hora(h)) if c != "Vendas Canceladas"]
             for h in range(24)]
    with pytest.raises(sps.ShopeeShopStatsError, match="financeira obrigatória"):
        _parse_hourly(tmp_path, header=header, total_row=total, horas=horas)


def test_gmv_negativo_no_total_bloqueia(tmp_path):
    horas = [_hora(h, vendas="10.00", cancel="100.00", devol="0.00") for h in range(24)]
    total = _total(vendas="240.00", cancel="2400.00", devol="0.00")
    with pytest.raises(sps.ShopeeShopStatsError, match="GMV líquido negativo"):
        _parse_hourly(tmp_path, total_row=total, horas=horas)


# ---------------------------------------------------------------------------
# 13. Terceiro layout desconhecido bloqueia (nunca 0 linhas silencioso)
# ---------------------------------------------------------------------------
def test_terceiro_layout_sem_coluna_de_data_bloqueia(tmp_path):
    header = ["Periodo"] + HOURLY_HEADER[1:]
    total = ["10/08/2026-10/08/2026"] + _total()[1:]
    horas = [[f"{DIA} {h:02d}:00"] + _hora(h)[1:] for h in range(24)]
    with pytest.raises(sps.ShopeeShopStatsError, match="sem coluna de data reconhecida"):
        _parse_hourly(tmp_path, header=header, total_row=total, horas=horas)


# ---------------------------------------------------------------------------
# 14. As cinco marcas seguem exatamente o mesmo contrato
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("brand", MARCAS)
def test_cinco_marcas_mesmo_contrato_horario(tmp_path, brand):
    out = _parse_hourly(tmp_path, brand=brand)
    assert len(out) == 1
    assert out[0]["gmv"] == 2136.0
    assert out[0]["brand"] == brand
    assert out[0]["visitors"] == 900


def test_colunas_de_compradores_ausentes_viram_none_nunca_zero(tmp_path):
    out = _parse_hourly(tmp_path)
    for campo in ("unique_buyers", "new_buyers", "repeat_buyers", "repeat_buyer_rate_pct"):
        assert out[0][campo] is None, campo
