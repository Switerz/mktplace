"""
Gate SH-AUTO-1B — deduplicação por snapshot vencedor dos exports `Order.all`.

Nenhum teste aqui abre conexão de banco. Os que exercitam `parse_brand` de ponta
a ponta geram arquivos `.xlsx` mínimos em `tmp_path`.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from pipelines.connectors.shopee import _parser as P
from pipelines.connectors.shopee._snapshots import (
    NomeDeExportInvalido,
    Snapshot,
    SnapshotAmbiguo,
    SnapshotIncompleto,
    agrupar_snapshots,
    deduplicar_por_pedido,
    validar_desempate,
)

# Nomes reais, copiados da pasta de produção.
SIMPLES = "Order.all.20260805_20260805.xlsx"
COM_PREFIXO = "Order.all.order_creation_date.20260901_20260908.xlsx"
DUPLICADO = "Order.all.order_creation_date.20260805_20260805 (1).xlsx"


def _p(*nomes: str) -> list[Path]:
    return [Path(n) for n in nomes]


# ---------------------------------------------------------------------------
# Agrupamento: a unidade é o snapshot, não o arquivo
# ---------------------------------------------------------------------------
def test_arquivo_simples_vira_um_snapshot():
    snaps = agrupar_snapshots(_p(SIMPLES))
    assert len(snaps) == 1
    assert snaps[0].date_from == date(2026, 8, 5)
    assert snaps[0].date_to == date(2026, 8, 5)


def test_os_dois_prefixos_reais_sao_aceitos():
    """`Order.all.<janela>` (até julho) e `Order.all.order_creation_date.<janela>`
    (desde setembro) convivem na mesma pasta."""
    snaps = agrupar_snapshots(_p(SIMPLES, COM_PREFIXO))
    assert len(snaps) == 2


def test_partes_do_mesmo_export_formam_UM_snapshot():
    """🔑 O ponto do gate: `part_3_of_8` não é candidato concorrente de
    `part_4_of_8`. Tratá-las como snapshots rivais descartaria 7/8 do export."""
    nomes = [f"Order.all.20260301_20260331_part_{i}_of_8.xlsx" for i in range(1, 9)]
    snaps = agrupar_snapshots(_p(*nomes))
    assert len(snaps) == 1
    assert len(snaps[0].arquivos) == 8


def test_partes_sao_ordenadas_pelo_indice_e_nao_pelo_glob():
    nomes = [f"Order.all.20260301_20260331_part_{i}_of_3.xlsx" for i in (3, 1, 2)]
    snaps = agrupar_snapshots(_p(*nomes))
    indices = [int(re.search(r"part_(\d+)_of", a.name).group(1)) for a in snaps[0].arquivos]
    assert indices == [1, 2, 3]


def test_exports_distintos_com_partes_nao_se_misturam():
    nomes = [
        "Order.all.20260301_20260331_part_1_of_2.xlsx",
        "Order.all.20260301_20260331_part_2_of_2.xlsx",
        "Order.all.20260401_20260430_part_1_of_2.xlsx",
        "Order.all.20260401_20260430_part_2_of_2.xlsx",
    ]
    snaps = agrupar_snapshots(_p(*nomes))
    assert len(snaps) == 2
    assert all(len(s.arquivos) == 2 for s in snaps)


# ---------------------------------------------------------------------------
# Multipart incompleto: fail-closed
# ---------------------------------------------------------------------------
def test_multipart_com_parte_faltando_recusa():
    """Publicar 7/8 como se fosse inteiro perde um oitavo do faturamento sem
    nenhum sinal."""
    nomes = [f"Order.all.20260301_20260331_part_{i}_of_8.xlsx" for i in range(1, 8)]
    with pytest.raises(SnapshotIncompleto, match="faltando"):
        agrupar_snapshots(_p(*nomes))


def test_multipart_com_parte_repetida_recusa():
    nomes = [
        "Order.all.20260301_20260331_part_1_of_2.xlsx",
        "Order.all.20260301_20260331_part_1_of_2.xlsx",
        "Order.all.20260301_20260331_part_2_of_2.xlsx",
    ]
    with pytest.raises(SnapshotIncompleto, match="repetidas|encontradas"):
        agrupar_snapshots(_p(*nomes))


def test_multipart_com_totais_divergentes_recusa():
    nomes = [
        "Order.all.20260301_20260331_part_1_of_2.xlsx",
        "Order.all.20260301_20260331_part_2_of_3.xlsx",
    ]
    with pytest.raises(SnapshotIncompleto, match="mais de um total"):
        agrupar_snapshots(_p(*nomes))


def test_nome_sem_janela_recusa():
    with pytest.raises(NomeDeExportInvalido, match="sem janela"):
        agrupar_snapshots(_p("Order.all.export_final.xlsx"))


# ---------------------------------------------------------------------------
# A regra do vencedor
# ---------------------------------------------------------------------------
def test_ordem_e_por_date_to_depois_date_from():
    snaps = agrupar_snapshots(_p(
        "Order.all.20260901_20260908.xlsx",
        "Order.all.20260907_20260915.xlsx",
        "Order.all.20260828_20260901.xlsx",
    ))
    assert [s.rotulo for s in snaps] == [
        "20260828..20260901", "20260901..20260908", "20260907..20260915",
    ]


def test_mesmo_teto_a_janela_mais_estreita_vence():
    """`0810..0810` foi exportado depois de `0805..0810`: mesma data final, mas
    a janela que começa depois é o pedido mais recente. Caso real medido na
    pasta de produção das cinco marcas."""
    snaps = agrupar_snapshots(_p(
        "Order.all.20260805_20260810.xlsx",
        "Order.all.20260810_20260810.xlsx",
    ))
    assert snaps[-1].rotulo == "20260810..20260810"


def test_empate_de_janela_recusa_em_vez_de_chutar():
    """O sufixo ` (1)` do navegador cria um segundo snapshot com a MESMA janela.
    Escolher o primeiro do `glob` faria o número publicado depender do sistema
    de arquivos."""
    snaps = agrupar_snapshots(_p(
        "Order.all.order_creation_date.20260805_20260805.xlsx", DUPLICADO,
    ))
    with pytest.raises(SnapshotAmbiguo, match="MESMA janela"):
        validar_desempate(snaps)


def test_duplicado_sozinho_e_aceito():
    """Na pasta real da barbours só existe a cópia ` (1)` — o original foi
    removido. Um snapshot só não é ambíguo."""
    snaps = agrupar_snapshots(_p(DUPLICADO))
    validar_desempate(snaps)
    assert len(snaps) == 1


def test_a_regra_nao_usa_mtime_nem_ordem_de_glob():
    """Contraprova de fonte instável: a chave de ordem vem só do nome.

    Inspeciona o CÓDIGO EXECUTÁVEL, não o texto do arquivo: o docstring do
    módulo cita `Path.stat().st_mtime` de propósito, para explicar por que ele é
    proibido. Um teste que varresse o arquivo inteiro reprovaria a própria
    explicação — foi o que aconteceu na primeira escrita deste teste.
    """
    import ast

    from pipelines.connectors.shopee import _snapshots

    arvore = ast.parse(Path(_snapshots.__file__).read_text(encoding="utf-8"))
    # remove todos os docstrings antes de inspecionar
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            corpo = no.body
            if corpo and isinstance(corpo[0], ast.Expr) and isinstance(
                corpo[0].value, ast.Constant
            ) and isinstance(corpo[0].value.value, str):
                no.body = corpo[1:] or [ast.Pass()]
    codigo = ast.dump(arvore)
    for proibido in ("st_mtime", "getmtime", "stat"):
        assert proibido not in codigo, f"fonte instável na regra do vencedor: {proibido}"


def test_chave_de_ordem_e_estavel_entre_copias():
    """Mesmo nome, diretórios diferentes: mesma chave. É o que faz a regra
    sobreviver a cópia de pasta, backup e `git clone`."""
    a = agrupar_snapshots([Path("/maquina_a") / SIMPLES])[0]
    b = agrupar_snapshots([Path("/outra/pasta/qualquer") / SIMPLES])[0]
    assert a.chave_de_ordem == b.chave_de_ordem


# ---------------------------------------------------------------------------
# Deduplicação: pedido inteiro, nunca campo a campo
# ---------------------------------------------------------------------------
def _snap(df: str, dt: str) -> Snapshot:
    return agrupar_snapshots(_p(f"Order.all.{df}_{dt}.xlsx"))[0]


ANTIGO = _snap("20260901", "20260908")
NOVO = _snap("20260907", "20260915")


def test_pedido_em_um_snapshot_so_passa_intacto():
    linhas = [{"order_id": "A", "status": "Concluído", "subtotal": 10}]
    saida, resumo = deduplicar_por_pedido([(ANTIGO, linhas)])
    assert saida == linhas
    assert resumo["linhas_descartadas"] == 0
    assert resumo["pedidos_em_mais_de_um_snapshot"] == 0


def test_pedido_repetido_conta_uma_vez_e_vem_do_vencedor():
    antigo = [{"order_id": "A", "status": "A Enviar", "subtotal": 10}]
    novo = [{"order_id": "A", "status": "Concluído", "subtotal": 10}]
    saida, resumo = deduplicar_por_pedido([(ANTIGO, antigo), (NOVO, novo)])
    assert len(saida) == 1
    assert saida[0]["status"] == "Concluído"
    assert resumo["linhas_descartadas"] == 1
    assert resumo["pedidos_em_mais_de_um_snapshot"] == 1


def test_resultado_independe_da_ordem_dos_lotes():
    antigo = [{"order_id": "A", "status": "A Enviar", "subtotal": 10}]
    novo = [{"order_id": "A", "status": "Cancelado", "subtotal": 10}]
    a, _ = deduplicar_por_pedido([(ANTIGO, antigo), (NOVO, novo)])
    b, _ = deduplicar_por_pedido([(NOVO, novo), (ANTIGO, antigo)])
    assert a == b == novo


def test_nao_mistura_campos_de_snapshots_diferentes():
    """🔴 O `max()` campo a campo produziria status novo + valor antigo — um
    número que não existe em export nenhum."""
    antigo = [{"order_id": "A", "status": "A Enviar", "subtotal": 999, "qty": 5}]
    novo = [{"order_id": "A", "status": "Cancelado", "subtotal": 10, "qty": 1}]
    saida, _ = deduplicar_por_pedido([(ANTIGO, antigo), (NOVO, novo)])
    assert saida == novo
    assert saida[0]["subtotal"] == 10, "o valor vem do vencedor, não é o maior"
    assert saida[0]["qty"] == 1


def test_snapshot_vencedor_com_menos_skus_remove_de_verdade():
    """O vendedor removeu um item do pedido. O SKU que só existia no export
    antigo tem de sumir, não sobreviver por 'união'."""
    antigo = [
        {"order_id": "A", "sku": "X", "subtotal": 10},
        {"order_id": "A", "sku": "Y", "subtotal": 20},
    ]
    novo = [{"order_id": "A", "sku": "X", "subtotal": 10}]
    saida, _ = deduplicar_por_pedido([(ANTIGO, antigo), (NOVO, novo)])
    assert [l["sku"] for l in saida] == ["X"]


def test_pedido_distribuido_entre_partes_do_mesmo_snapshot_e_preservado():
    """Partes não competem: as duas linhas do mesmo export sobrevivem."""
    snap = agrupar_snapshots(_p(
        "Order.all.20260301_20260331_part_1_of_2.xlsx",
        "Order.all.20260301_20260331_part_2_of_2.xlsx",
    ))[0]
    linhas = [
        {"order_id": "A", "sku": "X", "subtotal": 10},
        {"order_id": "A", "sku": "Y", "subtotal": 20},
    ]
    saida, resumo = deduplicar_por_pedido([(snap, linhas)])
    assert len(saida) == 2
    assert resumo["linhas_descartadas"] == 0


def test_pedidos_disjuntos_sobrevivem_todos():
    a = [{"order_id": "A", "subtotal": 1}]
    b = [{"order_id": "B", "subtotal": 2}]
    saida, resumo = deduplicar_por_pedido([(ANTIGO, a), (NOVO, b)])
    assert len(saida) == 2
    assert resumo["pedidos_em_mais_de_um_snapshot"] == 0


def test_linha_sem_order_id_e_preservada():
    """O nível 1 do parser já a ignora; descartá-la aqui mudaria o
    comportamento anterior sem necessidade."""
    linhas = [{"order_id": None, "subtotal": 1}]
    saida, _ = deduplicar_por_pedido([(ANTIGO, linhas)])
    assert saida == linhas


def test_resumo_nao_carrega_order_id():
    antigo = [{"order_id": "PEDIDO-SECRETO", "subtotal": 1}]
    novo = [{"order_id": "PEDIDO-SECRETO", "subtotal": 2}]
    _, resumo = deduplicar_por_pedido([(ANTIGO, antigo), (NOVO, novo)])
    assert "PEDIDO-SECRETO" not in repr(resumo)


def test_mensagens_de_erro_nao_carregam_pii():
    with pytest.raises(SnapshotIncompleto) as exc:
        agrupar_snapshots(_p("Order.all.20260301_20260331_part_1_of_3.xlsx"))
    texto = str(exc.value)
    for proibido in ("cpf", "comprador", "telefone", "@"):
        assert proibido not in texto.lower()


# ---------------------------------------------------------------------------
# parse_brand de ponta a ponta, com xlsx de verdade
# ---------------------------------------------------------------------------
CAB = [
    "ID do pedido", "Status do pedido", "Status da Devolução / Reembolso",
    "Data de criação do pedido", "Quantidade", "Subtotal do produto",
    "Total global", "Taxa de comissão líquida", "Taxa de serviço líquida",
    "Valor estimado do frete", "Nome de usuário (comprador)",
]


def _escrever(caminho: Path, linhas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(CAB)
    for l in linhas:
        ws.append([
            l["oid"], l.get("status", "Concluído"), l.get("dev", ""),
            l.get("data", "2026-09-10"), l.get("qty", 1), l.get("sub", 100.0),
            l.get("total", 100.0), l.get("com", 5.0), l.get("serv", 2.0),
            l.get("frete", 10.0), l.get("buyer", "comprador1"),
        ])
    wb.save(caminho)
    wb.close()


def test_parse_brand_conta_uma_vez_pedido_em_dois_snapshots(tmp_path):
    """🔴 O teste que falha sem a deduplicação: o mesmo pedido em dois exports
    sobrepostos dobrava GMV e unidades."""
    marca = tmp_path / "apice"
    _escrever(marca / "Order.all.20260901_20260908.xlsx",
              [{"oid": "P1", "qty": 2, "sub": 100.0, "status": "A Enviar"}])
    _escrever(marca / "Order.all.20260907_20260915.xlsx",
              [{"oid": "P1", "qty": 2, "sub": 100.0, "status": "Concluído"}])

    dias = P.parse_brand(tmp_path, "apice")
    assert len(dias) == 1
    d = dias[0]
    assert d["gmv"] == 100.0, "sem dedup daria 200.00"
    assert d["units_sold"] == 2, "sem dedup daria 4"
    assert d["orders"] == 1
    assert d["delivered_orders"] == 1, "o status vem do snapshot vencedor"


def test_parse_brand_snapshot_unico_inalterado(tmp_path):
    """Pedido presente uma vez tem de sair exatamente como antes do gate."""
    marca = tmp_path / "apice"
    _escrever(marca / "Order.all.20260901_20260908.xlsx", [
        {"oid": "P1", "qty": 2, "sub": 100.0},
        {"oid": "P2", "qty": 1, "sub": 50.0, "status": "Cancelado"},
    ])
    dias = P.parse_brand(tmp_path, "apice")
    d = dias[0]
    assert d["gmv"] == 100.0
    assert d["orders"] == 1
    assert d["canceled_orders"] == 1
    assert d["units_sold"] == 2


def test_parse_brand_independe_da_ordem_das_linhas(tmp_path):
    marca = tmp_path / "a"
    _escrever(marca / "Order.all.20260901_20260908.xlsx", [
        {"oid": "P1", "qty": 1, "sub": 10.0}, {"oid": "P2", "qty": 1, "sub": 20.0},
    ])
    primeiro = P.parse_brand(tmp_path, "a")

    marca2 = tmp_path / "b"
    _escrever(marca2 / "Order.all.20260901_20260908.xlsx", [
        {"oid": "P2", "qty": 1, "sub": 20.0}, {"oid": "P1", "qty": 1, "sub": 10.0},
    ])
    segundo = P.parse_brand(tmp_path, "b")

    assert primeiro[0]["gmv"] == segundo[0]["gmv"] == 30.0
    assert primeiro[0]["orders"] == segundo[0]["orders"] == 2


def test_parse_brand_status_maturado_vence_integralmente(tmp_path):
    """Snapshot mais novo com valor MENOR: o cancelamento tira o pedido do GMV."""
    marca = tmp_path / "apice"
    # P2 existe só para o dia continuar tendo pedido ativo: `_aggregate_daily`
    # não emite linha para dia com zero pedidos ativos, e essa regra é anterior
    # a este gate.
    _escrever(marca / "Order.all.20260901_20260908.xlsx", [
        {"oid": "P1", "qty": 3, "sub": 300.0, "status": "A Enviar"},
        {"oid": "P2", "qty": 1, "sub": 50.0, "status": "Concluído"},
    ])
    _escrever(marca / "Order.all.20260907_20260915.xlsx", [
        {"oid": "P1", "qty": 3, "sub": 300.0, "status": "Cancelado"},
        {"oid": "P2", "qty": 1, "sub": 50.0, "status": "Concluído"},
    ])

    dias = P.parse_brand(tmp_path, "apice")
    d = dias[0]
    assert d["canceled_orders"] == 1, "o cancelamento do snapshot vencedor vale"
    assert d["orders"] == 1, "P1 saiu dos ativos"
    assert d["gmv"] == 50.0, "os 300 do P1 não entram — snapshot mais novo, valor menor"
    assert d["units_sold"] == 1
    assert "P1" not in repr(d)


def test_parse_brand_multipart_incompleto_recusa(tmp_path):
    marca = tmp_path / "apice"
    _escrever(marca / "Order.all.20260901_20260908_part_1_of_2.xlsx",
              [{"oid": "P1"}])
    with pytest.raises(SnapshotIncompleto):
        P.parse_brand(tmp_path, "apice")


def test_parse_brand_empate_ambiguo_recusa(tmp_path):
    marca = tmp_path / "apice"
    _escrever(marca / "Order.all.order_creation_date.20260901_20260908.xlsx",
              [{"oid": "P1"}])
    _escrever(marca / "Order.all.order_creation_date.20260901_20260908 (1).xlsx",
              [{"oid": "P1"}])
    with pytest.raises(SnapshotAmbiguo):
        P.parse_brand(tmp_path, "apice")


def test_parse_brand_arquivo_vazio_nao_quebra(tmp_path):
    marca = tmp_path / "apice"
    _escrever(marca / "Order.all.20260901_20260908.xlsx", [])
    assert P.parse_brand(tmp_path, "apice") == []


def test_parse_brand_pasta_sem_arquivo_devolve_vazio(tmp_path):
    (tmp_path / "apice").mkdir()
    assert P.parse_brand(tmp_path, "apice") == []


def test_parse_brand_ignora_order_toship(tmp_path):
    """`Order.toship...` existe na pasta real da barbours e nunca casou o glob.
    O gate não pode passar a incluí-lo."""
    marca = tmp_path / "apice"
    _escrever(marca / "Order.all.20260901_20260908.xlsx", [{"oid": "P1", "sub": 10.0}])
    _escrever(marca / "Order.toship.20260901_20260908.xlsx", [{"oid": "P9", "sub": 999.0}])
    dias = P.parse_brand(tmp_path, "apice")
    assert dias[0]["gmv"] == 10.0


# ---------------------------------------------------------------------------
# Contraprovas: mutações que devem quebrar os testes acima
# ---------------------------------------------------------------------------
def test_contraprova_concatenar_tudo_volta_a_duplicar(tmp_path):
    """Reproduz o comportamento de `origin/main` e mede a inflação. Se este
    teste parar de ver duplicação, o defeito não existia."""
    marca = tmp_path / "apice"
    _escrever(marca / "Order.all.20260901_20260908.xlsx",
              [{"oid": "P1", "qty": 2, "sub": 100.0}])
    _escrever(marca / "Order.all.20260907_20260915.xlsx",
              [{"oid": "P1", "qty": 2, "sub": 100.0}])

    linhas = []
    for f in sorted(marca.glob("Order.all*.xlsx")):
        linhas.extend(P._read_xlsx(f))
    antigo = P._aggregate_daily(linhas, "apice")

    assert antigo[0]["gmv"] == 200.0, "o comportamento anterior dobrava o GMV"
    assert antigo[0]["units_sold"] == 4
    novo = P.parse_brand(tmp_path, "apice")
    assert novo[0]["gmv"] == 100.0


def test_contraprova_max_campo_a_campo_produziria_linha_inexistente():
    """Documenta por que `max()` não serve como regra de escolha."""
    antigo = [{"order_id": "A", "status": "A Enviar", "subtotal": 999}]
    novo = [{"order_id": "A", "status": "Cancelado", "subtotal": 10}]
    saida, _ = deduplicar_por_pedido([(ANTIGO, antigo), (NOVO, novo)])
    maximo = max(antigo[0]["subtotal"], novo[0]["subtotal"])
    assert saida[0]["subtotal"] != maximo
    assert saida[0]["subtotal"] == 10


def test_contraprova_parte_competindo_sozinha_perderia_o_export():
    """Se as partes competissem, só a última sobreviveria."""
    snaps = agrupar_snapshots(_p(
        "Order.all.20260301_20260331_part_1_of_2.xlsx",
        "Order.all.20260301_20260331_part_2_of_2.xlsx",
    ))
    assert len(snaps) == 1, "as partes viraram snapshots rivais"
    assert len(snaps[0].arquivos) == 2


def test_contraprova_regex_de_parte_bate_com_a_do_inventario_raw():
    """A expressão é duplicada de `shopee_raw/inventory.py` para evitar ciclo de
    import. Este teste faz o drift aparecer aqui, e não no número publicado."""
    from pipelines.connectors.shopee import _snapshots

    nomes = [
        SIMPLES, COM_PREFIXO, DUPLICADO,
        "Order.all.20260301_20260331_part_7_of_8.xlsx",
        "Order.all.order_creation_date.20260907_20260915_part_1_of_2.xlsx",
    ]
    inventario_part = re.compile(r"_part_(\d+)_of_(\d+)", re.IGNORECASE)
    for n in nomes:
        meu = _snapshots._PART_RE.search(n)
        dele = inventario_part.search(n)
        assert (meu is None) == (dele is None)
        if meu:
            assert meu.groups() == dele.groups()


# ---------------------------------------------------------------------------
# SH-AUTO-1B-OFFLINE — valor inválido e a ordem seleção → conversão
#
# Achado do diagnóstico focal da Kokeshi (22/09/2026, offline): o snapshot
# `20260805..20260805` traz 2.494 pedidos com valores em formato US
# ("1,234.56", rejeitado por `_numeric.py`) em quatro campos financeiros. Esse
# snapshot é o PERDEDOR — o `20260805..20260810` tem os mesmos 2.617 registros
# desses pedidos, com valores válidos, e é o vencedor pela regra.
#
# 🔑 A deduplicação elimina INCIDENTALMENTE um defeito histórico que derrubou o
# refresh manual em 28/08 e 15/09. Isso NÃO é uma correção genérica de valores
# inválidos, e os testes abaixo fixam exatamente essa fronteira: inválido no
# perdedor some; inválido no vencedor continua levantando.
# ---------------------------------------------------------------------------
VALOR_US_INVALIDO = "1,234.56"  # formato US, rejeitado de propósito por _numeric


def test_valor_invalido_no_perdedor_e_descartado_com_o_snapshot(tmp_path):
    """O caso real da Kokeshi. O pedido é processado só com o vencedor, e o
    valor inválido do perdedor nunca chega ao conversor numérico."""
    marca = tmp_path / "kokeshi"
    _escrever(marca / "Order.all.20260805_20260805.xlsx",
              [{"oid": "P1", "qty": 1, "sub": 100.0, "total": VALOR_US_INVALIDO,
                "status": "A Enviar"}])
    _escrever(marca / "Order.all.20260805_20260810.xlsx",
              [{"oid": "P1", "qty": 1, "sub": 100.0, "total": 150.0,
                "status": "Concluído"}])

    dias = P.parse_brand(tmp_path, "kokeshi")
    assert len(dias) == 1
    assert dias[0]["gmv"] == 100.0
    assert dias[0]["total_settlement"] == 150.0, "o financeiro vem do vencedor"
    assert dias[0]["delivered_orders"] == 1


def test_valor_invalido_no_VENCEDOR_continua_levantando(tmp_path):
    """🔴 O fail-fast NÃO foi afrouxado. A deduplicação escolhe o snapshot; ela
    não conserta, não coage e não ignora valor inválido."""
    marca = tmp_path / "kokeshi"
    _escrever(marca / "Order.all.20260805_20260805.xlsx",
              [{"oid": "P1", "qty": 1, "sub": 100.0, "total": 150.0}])
    _escrever(marca / "Order.all.20260805_20260810.xlsx",
              [{"oid": "P1", "qty": 1, "sub": 100.0, "total": VALOR_US_INVALIDO}])

    from pipelines.connectors.shopee._numeric import ShopeeNumericParseError

    with pytest.raises(ShopeeNumericParseError):
        P.parse_brand(tmp_path, "kokeshi")


def test_valor_invalido_em_snapshot_unico_continua_levantando(tmp_path):
    """Sem snapshot concorrente não há o que escolher — o defeito aparece."""
    marca = tmp_path / "kokeshi"
    _escrever(marca / "Order.all.20260805_20260805.xlsx",
              [{"oid": "P1", "qty": 1, "sub": 100.0, "total": VALOR_US_INVALIDO}])

    from pipelines.connectors.shopee._numeric import ShopeeNumericParseError

    with pytest.raises(ShopeeNumericParseError):
        P.parse_brand(tmp_path, "kokeshi")


def test_valor_invalido_em_pedido_que_so_existe_no_perdedor_ainda_levanta(tmp_path):
    """Fronteira fina: o snapshot perde para OUTRO pedido, mas este pedido só
    existe nele — então ele é o vencedor deste pedido e o defeito aparece.
    A escolha é por pedido, não por snapshot inteiro."""
    marca = tmp_path / "kokeshi"
    _escrever(marca / "Order.all.20260805_20260805.xlsx", [
        {"oid": "P1", "qty": 1, "sub": 100.0, "total": 150.0},
        {"oid": "SO_AQUI", "qty": 1, "sub": 50.0, "total": VALOR_US_INVALIDO},
    ])
    _escrever(marca / "Order.all.20260805_20260810.xlsx",
              [{"oid": "P1", "qty": 1, "sub": 100.0, "total": 150.0}])

    from pipelines.connectors.shopee._numeric import ShopeeNumericParseError

    with pytest.raises(ShopeeNumericParseError):
        P.parse_brand(tmp_path, "kokeshi")


def test_a_selecao_do_snapshot_acontece_antes_da_conversao_numerica():
    """Prova estrutural da ordem, que é o que faz o caso acima funcionar:
    `_read_xlsx` devolve células cruas, `deduplicar_por_pedido` escolhe, e só
    então `_aggregate_daily` chama `_to_float`. Inverter a ordem faria o parser
    quebrar em dados que ele vai descartar."""
    import ast

    fonte = Path(P.__file__).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    func = next(n for n in ast.walk(arvore)
                if isinstance(n, ast.FunctionDef) and n.name == "parse_brand")
    chamadas = [
        (n.lineno, n.func.id)
        for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    ordem = [nome for _, nome in sorted(chamadas)]
    assert "deduplicar_por_pedido" in ordem
    assert "_aggregate_daily" in ordem
    assert ordem.index("deduplicar_por_pedido") < ordem.index("_aggregate_daily")

    # e `_read_xlsx` não converte: quem converte é `_to_float`, dentro de
    # `_aggregate_daily`.
    leitura = next(n for n in ast.walk(arvore)
                   if isinstance(n, ast.FunctionDef) and n.name == "_read_xlsx")
    assert "_to_float" not in ast.dump(leitura)
