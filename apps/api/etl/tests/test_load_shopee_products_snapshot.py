"""Gate SH-API-2A-R — deduplicacao fail-closed por snapshot logico.

Cobre os 20 casos exigidos pelo gate mais a contraprova do arquivo
timestampado. Nenhum teste imprime ou asserta sobre order_id real,
comprador, telefone, CPF ou endereco: os ids usados sao sinteticos
("P1", "P2", ...) e as assercoes de erro verificam AUSENCIA de PII.
"""
from __future__ import annotations

import pandas as pd
import pytest

# IMPORTANTE: importar o MODULO, nunca os nomes. Outro modulo desta suite
# (test_load_shopee_products_local_pg_guard.py) chama importlib.reload() no
# loader; um `from X import Y` feito antes do reload fica com referencia a'
# classe ANTIGA, que nao e' mais `is`-identica a' nova, e
# pytest.raises(ClasseAntiga) passa a falhar. Mesma convencao ja' documentada
# em test_load_shopee_products_numeric.py.
from etl import load_shopee_products as mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakePath:
    """Stand-in de pathlib.Path: só `.name` é usado por mod._plan_brand_snapshots."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - diagnostico
        return f"FakePath({self.name!r})"


def _p(*names: str) -> list[FakePath]:
    return [FakePath(n) for n in names]


def _rows(*specs) -> pd.DataFrame:
    """specs: (order_id, snap_start, snap_end, status, sku, qty, subtotal)."""
    return pd.DataFrame(
        [
            {
                "brand": "apice",
                "order_id": o,
                "_snap_start": s0,
                "_snap_end": s1,
                "status": st,
                "sku_ref": sku,
                "product_name": f"Produto {sku}",
                "variation_name": "unica",
                "qty": q,
                "subtotal": v,
                "buyer_username": "u",
                "ref_month": pd.Timestamp("2026-05-01"),
                "order_date": pd.Timestamp("2026-05-01 10:00"),
            }
            for (o, s0, s1, st, sku, q, v) in specs
        ]
    )


def _never_hashes(path):  # pragma: no cover - guarda
    raise AssertionError("hasher nao deveria ser chamado neste caso")


# ---------------------------------------------------------------------------
# 1. janela mensal sobreposta a outra janela
# ---------------------------------------------------------------------------

def test_01_janela_sobreposta_vence_a_de_fim_maior():
    df = _rows(
        ("P1", "20260401", "20260501", "A Enviar", "S1", 1, 100.0),
        ("P1", "20260501", "20260531", "Concluído", "S1", 1, 100.0),
    )
    out = mod._select_current_snapshot(df, brand="apice")
    assert len(out) == 1
    assert out.iloc[0]["_snap_end"] == "20260531"
    assert out.iloc[0]["status"] == "Concluído"


# ---------------------------------------------------------------------------
# 2. arquivo timestampado provoca aborto
# ---------------------------------------------------------------------------

def test_02_arquivo_timestampado_aborta():
    assert mod._classify_order_file("Order.all.20260717T155433Z.xlsx")["kind"] == "unsupported"
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._plan_brand_snapshots(
            "kokeshi",
            _p("Order.all.order_creation_date.20260601_20260630.xlsx",
               "Order.all.20260717T155433Z.xlsx"),
            hasher=_never_hashes,
        )
    msg = str(ei.value)
    assert "convencao de nome desconhecida" in msg
    assert "nada foi escrito" in msg


# ---------------------------------------------------------------------------
# 3-7. partes
# ---------------------------------------------------------------------------

def test_03_multiplas_partes_completas_formam_um_snapshot():
    acc = mod._plan_brand_snapshots(
        "barbours",
        _p("Order.all.order_creation_date.20260301_20260331_part_1_of_3.xlsx",
           "Order.all.order_creation_date.20260301_20260331_part_2_of_3.xlsx",
           "Order.all.order_creation_date.20260301_20260331_part_3_of_3.xlsx"),
        hasher=_never_hashes,
    )
    assert len(acc) == 3
    assert set(acc.values()) == {("20260301", "20260331")}


def test_04_parte_ausente_aborta():
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._plan_brand_snapshots(
            "barbours",
            _p("Order.all.20260301_20260331_part_1_of_3.xlsx",
               "Order.all.20260301_20260331_part_3_of_3.xlsx"),
            hasher=_never_hashes,
        )
    assert "parte ausente" in str(ei.value)
    assert "ausentes=[2]" in str(ei.value)


def test_05_parte_duplicada_aborta():
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._plan_brand_snapshots(
            "barbours",
            _p("Order.all.20260301_20260331_part_1_of_2.xlsx",
               "Order.all.order_creation_date.20260301_20260331_part_1_of_2.xlsx",
               "Order.all.20260301_20260331_part_2_of_2.xlsx"),
            hasher=_never_hashes,
        )
    assert "numero de parte repetido" in str(ei.value)


def test_06_pedidos_disjuntos_entre_partes_sobrevivem_todos():
    df = _rows(
        ("P1", "20260301", "20260331", "Concluído", "S1", 1, 10.0),
        ("P2", "20260301", "20260331", "Concluído", "S2", 1, 20.0),
    )
    out = mod._select_current_snapshot(df, brand="apice")
    assert len(out) == 2
    assert set(out["order_id"]) == {"P1", "P2"}


def test_07_pedido_em_duas_partes_do_mesmo_snapshot_nao_e_deduplicado():
    # Mesmo snapshot logico: as duas linhas empatam no topo e AMBAS ficam.
    # Deduplicar aqui removeria item legitimo — o teste trava esse risco.
    df = _rows(
        ("P1", "20260301", "20260331", "Concluído", "S1", 1, 10.0),
        ("P1", "20260301", "20260331", "Concluído", "S2", 2, 40.0),
    )
    out = mod._select_current_snapshot(df, brand="apice")
    assert len(out) == 2
    assert out["subtotal"].sum() == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 8-9. copias e divergencia na mesma janela
# ---------------------------------------------------------------------------

def test_08_copia_byte_identica_reduz_a_uma():
    acc = mod._plan_brand_snapshots(
        "lescent",
        _p("Order.all.20260805_20260805.xlsx",
           "Order.all.order_creation_date.20260805_20260805.xlsx"),
        hasher=lambda p: "mesmo-digest",
    )
    assert len(acc) == 1
    assert set(acc.values()) == {("20260805", "20260805")}


def test_09_dois_arquivos_divergentes_para_a_mesma_janela_aborta():
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._plan_brand_snapshots(
            "lescent",
            _p("Order.all.20260805_20260805.xlsx",
               "Order.all.order_creation_date.20260805_20260805.xlsx"),
            hasher=lambda p: f"digest-{p.name}",
        )
    msg = str(ei.value)
    assert "arquivos inteiros divergentes" in msg
    assert "nada foi escrito" in msg


# ---------------------------------------------------------------------------
# 10-11. maturacao de status
# ---------------------------------------------------------------------------

def test_10_antigo_a_enviar_novo_concluido_vence_o_novo():
    df = _rows(
        ("P1", "20260601", "20260630", "A Enviar", "S1", 1, 100.0),
        ("P1", "20260701", "20260731", "Concluído", "S1", 1, 100.0),
    )
    out = mod._select_current_snapshot(df, brand="apice")
    assert list(out["status"]) == ["Concluído"]


def test_11_antigo_concluido_novo_cancelado_vence_o_novo():
    df = _rows(
        ("P1", "20260601", "20260630", "Concluído", "S1", 1, 100.0),
        ("P1", "20260701", "20260731", "Cancelado", "S1", 1, 100.0),
    )
    out = mod._select_current_snapshot(df, brand="apice")
    assert list(out["status"]) == ["Cancelado"]
    # E o cancelado nao entra no GMV do agregado.
    agg = mod._aggregate(out.assign(brand="apice"))
    assert agg["gmv"].sum() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 12-13. integridade do pedido
# ---------------------------------------------------------------------------

def test_12_pedido_com_multiplos_itens_mantem_todos():
    df = _rows(
        ("P1", "20260401", "20260501", "Concluído", "S1", 1, 10.0),
        ("P1", "20260401", "20260501", "Concluído", "S2", 1, 20.0),
        ("P1", "20260401", "20260501", "Concluído", "S3", 1, 30.0),
        ("P1", "20260501", "20260531", "Concluído", "S1", 1, 10.0),
        ("P1", "20260501", "20260531", "Concluído", "S2", 1, 20.0),
        ("P1", "20260501", "20260531", "Concluído", "S3", 1, 30.0),
    )
    out = mod._select_current_snapshot(df, brand="apice")
    assert len(out) == 3
    assert out["subtotal"].sum() == pytest.approx(60.0)
    assert set(out["sku_ref"]) == {"S1", "S2", "S3"}


def test_13_sku_repetido_legitimamente_no_mesmo_pedido_e_preservado():
    df = _rows(
        ("P1", "20260501", "20260531", "Concluído", "S1", 1, 10.0),
        ("P1", "20260501", "20260531", "Concluído", "S1", 2, 20.0),
    )
    out = mod._select_current_snapshot(df, brand="apice")
    assert len(out) == 2
    assert out["qty"].sum() == 3
    assert out["subtotal"].sum() == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# 14. ID do pedido ausente
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vazio", [None, "", "   "])
def test_14_id_do_pedido_ausente_aborta(vazio):
    df = _rows(("P1", "20260501", "20260531", "Concluído", "S1", 1, 10.0))
    df.loc[0, "order_id"] = vazio
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._select_current_snapshot(df, brand="apice")
    assert "ID do pedido ausente" in str(ei.value)


# ---------------------------------------------------------------------------
# 15-16. independencia de ordem do glob e de mtime
# ---------------------------------------------------------------------------

def test_15_ordem_do_glob_invertida_produz_o_mesmo_resultado():
    a = _rows(
        ("P1", "20260401", "20260501", "A Enviar", "S1", 1, 100.0),
        ("P1", "20260501", "20260531", "Concluído", "S1", 1, 100.0),
    )
    b = a.iloc[::-1].reset_index(drop=True)
    oa = mod._select_current_snapshot(a, brand="apice").reset_index(drop=True)
    ob = mod._select_current_snapshot(b, brand="apice").reset_index(drop=True)
    assert list(oa["status"]) == list(ob["status"]) == ["Concluído"]
    assert oa["subtotal"].sum() == ob["subtotal"].sum()


def test_16_mtime_invertido_nao_altera_o_resultado():
    # mtime nunca entra na decisao: a unica ordem e' (_snap_end, _snap_start).
    # Anexar um mtime contraditorio nao muda nada.
    df = _rows(
        ("P1", "20260401", "20260501", "A Enviar", "S1", 1, 100.0),
        ("P1", "20260501", "20260531", "Concluído", "S1", 1, 100.0),
    )
    df["_mtime"] = [9_999_999_999, 1]  # o snapshot ANTIGO tem mtime mais novo
    out = mod._select_current_snapshot(df, brand="apice")
    assert list(out["status"]) == ["Concluído"]


# ---------------------------------------------------------------------------
# 17-18. contrato e PII
# ---------------------------------------------------------------------------

def test_17_proveniencia_nao_aparece_no_resultado_final():
    df = _rows(
        ("P1", "20260501", "20260531", "Concluído", "S1", 1, 100.0),
        ("P2", "20260501", "20260531", "Concluído", "S2", 2, 50.0),
    )
    sel = mod._select_current_snapshot(df, brand="apice").drop(
        columns=["_snap_start", "_snap_end"]
    )
    out = mod._aggregate(sel)
    tecnicas = {"_snap_start", "_snap_end", "_snap_rank", "order_id",
                "_source_file", "_source_row"}
    assert tecnicas.isdisjoint(set(out.columns))


def test_18_erros_nunca_contem_pii_nem_order_id():
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._plan_brand_snapshots("barbours", _p("Order.toship.x.xlsx"), hasher=_never_hashes)
    msg = str(ei.value)
    for proibido in ("cpf", "telefone", "endereco", "endereço", "comprador", "@"):
        assert proibido not in msg.lower()

    df = _rows(("PEDIDO-SENSIVEL-123", "20260501", "20260531", "Concluído", "S1", 1, 10.0))
    df.loc[0, "order_id"] = None
    with pytest.raises(mod.ShopeeSnapshotError) as ei2:
        mod._select_current_snapshot(df, brand="apice")
    assert "PEDIDO-SENSIVEL-123" not in str(ei2.value)


# ---------------------------------------------------------------------------
# 19-20. neutralidade e ordem das etapas
# ---------------------------------------------------------------------------

def test_19_arquivos_sem_sobreposicao_permanecem_identicos():
    df = _rows(
        ("P1", "20260401", "20260430", "Concluído", "S1", 1, 10.0),
        ("P2", "20260501", "20260531", "Concluído", "S2", 1, 20.0),
        ("P3", "20260601", "20260630", "Concluído", "S3", 1, 30.0),
    )
    out = mod._select_current_snapshot(df, brand="apice")
    assert len(out) == len(df)
    assert out["subtotal"].sum() == pytest.approx(df["subtotal"].sum())


def test_20_dedup_ocorre_antes_do_filtro_de_status():
    """A ordem das etapas muda o resultado — e este teste trava a ordem certa.

    Pedido cujo snapshot antigo diz 'Concluído' e o novo diz 'Cancelado'.
      certo  (dedup -> status): vence o novo (Cancelado) -> GMV 0
      errado (status -> dedup): o filtro guarda a linha antiga -> GMV 100
    """
    df = _rows(
        ("P1", "20260601", "20260630", "Concluído", "S1", 1, 100.0),
        ("P1", "20260701", "20260731", "Cancelado", "S1", 1, 100.0),
    )

    certo = mod._aggregate(
        mod._select_current_snapshot(df, brand="apice").drop(columns=["_snap_start", "_snap_end"])
    )
    assert certo["gmv"].sum() == pytest.approx(0.0)

    errado = mod._aggregate(df[df["status"] == "Concluído"].drop(columns=["_snap_start", "_snap_end"]))
    assert errado["gmv"].sum() == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Contraprova: a regra antiga (nome timestampado como ordem) removeria
# R$ 971.946,52 — o loader novo aborta em vez de produzir esse resultado.
# ---------------------------------------------------------------------------

def test_contraprova_regra_antiga_removeria_971946_e_o_loader_aborta():
    """Reproduz em miniatura o caso kokeshi 19-25/06 medido no SH-API-2A.

    Dois snapshots do mesmo pedido: o mensal `20260601_20260630` (posterior,
    'Concluído') e `Order.all.20260717T155433Z.xlsx` (anterior, 'A Enviar'),
    cujo NOME carrega uma data maior. Ordenar pelo nome elegeria o instante,
    e o filtro `status == 'Concluído'` descartaria a venda.
    """
    valor = 971_946.52

    # (a) A regra antiga, baseada no nome, escolheria o snapshot 'A Enviar'
    #     e a venda desapareceria do mart.
    como_o_nome_ordenaria = _rows(
        ("P1", "20260717", "20260717", "A Enviar", "S1", 1, valor),
    )
    perdido = mod._aggregate(
        como_o_nome_ordenaria.drop(columns=["_snap_start", "_snap_end"])
    )
    assert perdido["gmv"].sum() == pytest.approx(0.0), (
        "a regra antiga zera o GMV desta venda — e' exatamente a perda medida"
    )

    # (b) O snapshot correto (mensal, posterior) preserva a venda.
    correto = _rows(("P1", "20260601", "20260630", "Concluído", "S1", 1, valor))
    mantido = mod._aggregate(correto.drop(columns=["_snap_start", "_snap_end"]))
    assert mantido["gmv"].sum() == pytest.approx(valor)

    # (c) O loader novo nunca chega a decidir: a population com o arquivo
    #     timestampado aborta na triagem, antes de qualquer leitura/escrita.
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._plan_brand_snapshots(
            "kokeshi",
            _p("Order.all.order_creation_date.20260601_20260630.xlsx",
               "Order.all.20260717T155433Z.xlsx"),
            hasher=_never_hashes,
        )
    assert "convencao de nome desconhecida" in str(ei.value)


# ---------------------------------------------------------------------------
# Guardas extras da classificacao
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome,esperado", [
    ("Order.all.20260401_20260501.xlsx", "window_single"),
    ("Order.all.order_creation_date.20260601_20260630.xlsx", "window_single"),
    ("Order.all.20260801_20260804_part_1_of_2.xlsx", "window_part"),
    ("Order.all.order_creation_date.20260601_20260630_part_19_of_19.xlsx", "window_part"),
    ("Order.all.20260717T155433Z.xlsx", "unsupported"),
    ("Order.toship.order_creation_date.20260805_20260805.xlsx", "unsupported"),
    ("Order.all.order_creation_date.20260805_20260805 (1).xlsx", "unsupported"),
    ("Order.all.20260501_20260401.xlsx", "unsupported"),
    ("Order.all.20260801_20260804_part_0_of_2.xlsx", "unsupported"),
    ("Order.all.20260801_20260804_part_3_of_2.xlsx", "unsupported"),
    ("Order.all.20260801_20260804_part_1_of_0.xlsx", "unsupported"),
    ("order.all.20260401_20260501.xlsx", "unsupported"),
])
def test_classificacao_dos_formatos_reais(nome, esperado):
    assert mod._classify_order_file(nome)["kind"] == esperado


def test_partes_com_totais_divergentes_aborta():
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._plan_brand_snapshots(
            "kokeshi",
            _p("Order.all.20260601_20260630_part_1_of_2.xlsx",
               "Order.all.20260601_20260630_part_2_of_3.xlsx"),
            hasher=_never_hashes,
        )
    assert "totais divergentes" in str(ei.value)


def test_mistura_de_particionado_e_inteiro_na_mesma_janela_aborta():
    with pytest.raises(mod.ShopeeSnapshotError) as ei:
        mod._plan_brand_snapshots(
            "kokeshi",
            _p("Order.all.20260601_20260630.xlsx",
               "Order.all.20260601_20260630_part_1_of_1.xlsx"),
            hasher=_never_hashes,
        )
    assert "particionado e nao" in str(ei.value)


def test_population_vazia_aborta():
    with pytest.raises(mod.ShopeeSnapshotError):
        mod._plan_brand_snapshots("apice", [], hasher=_never_hashes)
