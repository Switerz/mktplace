"""Desempate determinístico entre snapshots de Ads (SH-MANUAL-20260923-H1).

O defeito medido: dois exports com período sobreposto — `07/09–15/09` e
`14/09–22/09` — produziam CADA UM uma linha para 15/09, com valores diferentes
(cada arquivo rateia o total do SEU período). Resultado: 45 linhas para 40
chaves nas cinco marcas, e o vencedor dependia da ordem do glob e do UPSERT.
Somar as duas inflaria `ad_spend` em 15,1% na janela medida.

A regra: por marca e data, vence o snapshot de `date_to` maior; empate de
`date_to` resolve por `date_from` maior; empate exato de período FALHA
FECHADO. A janela vem do CABEÇALHO, nunca do nome do arquivo.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import date

import pytest

from pipelines.connectors.shopee import _parser_ads
from pipelines.connectors.shopee._parser_ads import (
    AdsPeriodoInvalido,
    AdsSnapshotAmbiguo,
    deduplicar_ads_por_data,
)

HEADER = ["#", "Impressões", "Cliques", "Despesas", "GMV"]


def _escreve_csv(path, period, *, despesas="1000.00", gmv="10000.00",
                 impressoes="100000", cliques="1000"):
    preamble = [
        "Relatório de Todos os Anúncios CPC - Shopee Brasil\n",
        "Nome de Usuário,marca_teste\n",
        "Nome da loja,Marca Teste\n",
        "ID da Loja,123456\n",
        "Data de Criação do Relatório,01/01/2026 00:00\n",
        f"Período,{period}\n",
        "\n",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerow([1, impressoes, cliques, despesas, gmv])
    path.write_text("".join(preamble) + buf.getvalue(), encoding="utf-8-sig")
    return path


def _por_data(linhas):
    return {l["date"]: l for l in linhas}


# --- 1) sem sobreposição: comportamento anterior preservado ----------------

def test_periodos_sem_sobreposicao_permanecem_identicos(tmp_path):
    """A dedup não pode mexer no caso comum: janelas disjuntas somam dias, não
    competem."""
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-a.csv", "01/09/2026 - 07/09/2026", despesas="700.00")
    _escreve_csv(d / "Dados-b.csv", "08/09/2026 - 14/09/2026", despesas="1400.00")

    linhas = _parser_ads.parse_brand_ads(tmp_path, "apice")

    assert len(linhas) == 14
    assert len({l["date"] for l in linhas}) == 14
    porta = _por_data(linhas)
    assert porta[date(2026, 9, 3)]["ad_spend"] == round(700.00 / 7, 2)
    assert porta[date(2026, 9, 10)]["ad_spend"] == round(1400.00 / 7, 2)


# --- 2) sobreposição: vence o date_to maior --------------------------------

def test_sobreposicao_em_um_dia_escolhe_date_to_maior(tmp_path):
    """O caso REAL: 07/09–15/09 x 14/09–22/09 disputam 15/09."""
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-antigo.csv", "07/09/2026 - 15/09/2026", despesas="900.00")
    _escreve_csv(d / "Dados-novo.csv", "14/09/2026 - 22/09/2026", despesas="450.00")

    linhas = _parser_ads.parse_brand_ads(tmp_path, "apice")
    porta = _por_data(linhas)

    # 15/09 fica com o snapshot de date_to 22/09 (450/9), nao 900/9.
    assert porta[date(2026, 9, 15)]["ad_spend"] == round(450.00 / 9, 2)
    # 14/09 tambem e disputado, e o novo tambem vence.
    assert porta[date(2026, 9, 14)]["ad_spend"] == round(450.00 / 9, 2)
    # 07/09 so existe no antigo.
    assert porta[date(2026, 9, 7)]["ad_spend"] == round(900.00 / 9, 2)
    assert len(linhas) == len(set(l["date"] for l in linhas))


# --- 3) empate de date_to: desempata por date_from maior -------------------

def test_empate_de_date_to_usa_date_from_maior(tmp_path):
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-largo.csv", "01/09/2026 - 20/09/2026", despesas="2000.00")
    _escreve_csv(d / "Dados-estreito.csv", "15/09/2026 - 20/09/2026", despesas="600.00")

    porta = _por_data(_parser_ads.parse_brand_ads(tmp_path, "apice"))

    # date_to igual (20/09) -> vence date_from maior (15/09): 600/6.
    assert porta[date(2026, 9, 18)]["ad_spend"] == round(600.00 / 6, 2)
    # fora da janela estreita, so o largo cobre.
    assert porta[date(2026, 9, 5)]["ad_spend"] == round(2000.00 / 20, 2)


def test_date_to_tem_precedencia_sobre_date_from(tmp_path):
    """O caso que SEPARA as duas ordenações possíveis.

    Nos outros cenários `date_to` e `date_from` apontam para o mesmo vencedor,
    então ambos ordenariam igual — uma mutação da chave passaria despercebida.
    Aqui os dois discordam de propósito:

        largo   01/09..22/09  -> date_to MAIOR, date_from MENOR
        recente 14/09..20/09  -> date_to menor, date_from MAIOR

    A regra é `date_to` primeiro, logo o LARGO vence. Se a chave fosse
    `(date_from, date_to)`, venceria o recente e este teste reprova.
    """
    d = tmp_path / "apice"; d.mkdir()
    # Taxas diarias DIFERENTES de proposito: 2200/22 = 100,00 e 350/7 = 50,00.
    # Se as duas dessem o mesmo valor por dia, o teste nao distinguiria nada.
    _escreve_csv(d / "Dados-largo.csv", "01/09/2026 - 22/09/2026", despesas="2200.00")
    _escreve_csv(d / "Dados-recente.csv", "14/09/2026 - 20/09/2026", despesas="350.00")

    porta = _por_data(_parser_ads.parse_brand_ads(tmp_path, "apice"))

    assert porta[date(2026, 9, 18)]["ad_spend"] == round(2200.00 / 22, 2)
    assert porta[date(2026, 9, 18)]["ad_spend"] != round(350.00 / 7, 2)
    # e o dia fora da janela do recente continua vindo do largo
    assert porta[date(2026, 9, 22)]["ad_spend"] == round(2200.00 / 22, 2)


def test_chave_de_ordem_poe_date_to_na_primeira_posicao():
    """Trava a chave diretamente, sem depender de dados de exemplo."""
    from pipelines.connectors.shopee._parser_ads import _chave_de_ordem
    assert _chave_de_ordem((date(2026, 9, 1), date(2026, 9, 22))) == (
        date(2026, 9, 22), date(2026, 9, 1))
    largo = _chave_de_ordem((date(2026, 9, 1), date(2026, 9, 22)))
    recente = _chave_de_ordem((date(2026, 9, 14), date(2026, 9, 20)))
    assert max(largo, recente) == largo


# --- 4) mesma janela em dois arquivos: recusa ------------------------------

def test_mesma_janela_em_dois_arquivos_recusa(tmp_path):
    """Sem desempate confiável, falha fechado — nunca escolhe arbitrariamente."""
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-original.csv", "14/09/2026 - 22/09/2026", despesas="900.00")
    _escreve_csv(d / "Dados-original (1).csv", "14/09/2026 - 22/09/2026", despesas="450.00")

    with pytest.raises(AdsSnapshotAmbiguo) as exc:
        _parser_ads.parse_brand_ads(tmp_path, "apice")

    msg = str(exc.value)
    assert "2026-09-14" in msg and "2026-09-22" in msg
    assert "Dados-original.csv" in msg


# --- 5) ordem dos arquivos não altera o resultado -------------------------

def test_ordem_dos_arquivos_invertida_produz_resultado_identico(tmp_path):
    """A dedup opera sobre `max` de uma chave, não sobre a ordem de chegada."""
    p_antigo = ((date(2026, 9, 7), date(2026, 9, 15)), "antigo.csv",
                [{"date": date(2026, 9, 15), "ad_spend": 100.0}])
    p_novo = ((date(2026, 9, 14), date(2026, 9, 22)), "novo.csv",
              [{"date": date(2026, 9, 15), "ad_spend": 50.0}])

    direto = deduplicar_ads_por_data([p_antigo, p_novo])
    invertido = deduplicar_ads_por_data([p_novo, p_antigo])

    assert direto == invertido
    assert direto[0]["ad_spend"] == 50.0      # vencedor e o de date_to maior


def test_ordem_no_disco_invertida_produz_resultado_identico(tmp_path):
    """Mesma prova, mas atravessando o glob real: o nome que ordena primeiro
    passa a ser o do snapshot PERDEDOR."""
    a = tmp_path / "a"; a.mkdir()
    _escreve_csv(a / "Dados-aaa.csv", "07/09/2026 - 15/09/2026", despesas="900.00")
    _escreve_csv(a / "Dados-zzz.csv", "14/09/2026 - 22/09/2026", despesas="450.00")
    b = tmp_path / "b"; b.mkdir()
    _escreve_csv(b / "Dados-zzz.csv", "07/09/2026 - 15/09/2026", despesas="900.00")
    _escreve_csv(b / "Dados-aaa.csv", "14/09/2026 - 22/09/2026", despesas="450.00")

    va = _por_data(_parser_ads.parse_brand_ads(tmp_path, "a"))
    vb = _por_data(_parser_ads.parse_brand_ads(tmp_path, "b"))

    assert va[date(2026, 9, 15)]["ad_spend"] == vb[date(2026, 9, 15)]["ad_spend"]
    assert va[date(2026, 9, 15)]["ad_spend"] == round(450.00 / 9, 2)


# --- 6) mtime não é contrato ----------------------------------------------

def test_mtime_invertido_nao_altera_resultado(tmp_path):
    """Envelhecer o arquivo vencedor não pode promover o perdedor."""
    d = tmp_path / "apice"; d.mkdir()
    antigo = _escreve_csv(d / "Dados-antigo.csv", "07/09/2026 - 15/09/2026", despesas="900.00")
    novo = _escreve_csv(d / "Dados-novo.csv", "14/09/2026 - 22/09/2026", despesas="450.00")
    # novo fica com mtime MAIS VELHO que o antigo.
    os.utime(novo, (1_600_000_000, 1_600_000_000))
    os.utime(antigo, (1_700_000_000, 1_700_000_000))

    porta = _por_data(_parser_ads.parse_brand_ads(tmp_path, "apice"))
    assert porta[date(2026, 9, 15)]["ad_spend"] == round(450.00 / 9, 2)


# --- 7) período inválido, ausente ou invertido recusa ----------------------

def test_periodo_ausente_recusa_sem_vazar_conteudo(tmp_path):
    d = tmp_path / "apice"; d.mkdir()
    (d / "Dados-sem-periodo.csv").write_text(
        "Relatório de Todos os Anúncios CPC - Shopee Brasil\n"
        "Nome de Usuário,marca_teste\n\n#,Impressões,Cliques,Despesas,GMV\n"
        "1,100,10,5.00,50.00\n", encoding="utf-8-sig")

    with pytest.raises(AdsPeriodoInvalido) as exc:
        _parser_ads.parse_brand_ads(tmp_path, "apice")
    msg = str(exc.value)
    assert "apice" in msg and "Dados-sem-periodo.csv" in msg
    assert "marca_teste" not in msg          # nao ecoa conteudo do arquivo


def test_periodo_ilegivel_recusa(tmp_path):
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-ruim.csv", "nao-e-uma-data - 22/09/2026")
    with pytest.raises(AdsPeriodoInvalido):
        _parser_ads.parse_brand_ads(tmp_path, "apice")


def test_periodo_invertido_recusa(tmp_path):
    """`date_from > date_to` daria num_days <= 0 — divisao por zero ou taxa
    negativa. Recusa antes de ratear."""
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-invertido.csv", "22/09/2026 - 14/09/2026")
    with pytest.raises(AdsPeriodoInvalido) as exc:
        _parser_ads.parse_brand_ads(tmp_path, "apice")
    assert "invertido" in str(exc.value)


# --- 8) nunca soma as concorrentes ----------------------------------------

def test_nenhuma_soma_das_duas_linhas_concorrentes(tmp_path):
    """O erro oposto ao de escolher errado: somar. 450/9 + 900/9 = 150,00."""
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-antigo.csv", "07/09/2026 - 15/09/2026", despesas="900.00")
    _escreve_csv(d / "Dados-novo.csv", "14/09/2026 - 22/09/2026", despesas="450.00")

    porta = _por_data(_parser_ads.parse_brand_ads(tmp_path, "apice"))
    dia = porta[date(2026, 9, 15)]["ad_spend"]
    assert dia == round(450.00 / 9, 2)
    assert dia != round(900.00 / 9 + 450.00 / 9, 2)


# --- 9 e 10) cinco marcas x oito dias = 40 chaves, zero duplicata ---------

def test_cinco_marcas_oito_dias_dao_40_chaves_sem_duplicata(tmp_path):
    """Reproduz a forma do caso real: em cada marca, dois exports sobrepostos
    cobrindo 15/09."""
    marcas = ("apice", "barbours", "kokeshi", "lescent", "rituaria")
    for m in marcas:
        d = tmp_path / m; d.mkdir()
        _escreve_csv(d / "Dados-antigo.csv", "07/09/2026 - 15/09/2026", despesas="900.00")
        _escreve_csv(d / "Dados-novo.csv", "14/09/2026 - 22/09/2026", despesas="450.00")

    chaves = []
    for m in marcas:
        for l in _parser_ads.parse_brand_ads(tmp_path, m):
            if date(2026, 9, 15) <= l["date"] <= date(2026, 9, 22):
                chaves.append((m, l["date"]))

    assert len(chaves) == 40
    assert len(set(chaves)) == 40


# --- 11) totais do arquivo vencedor preservados ---------------------------

def test_totais_do_arquivo_vencedor_preservados(tmp_path):
    """A dedup escolhe a linha; não recalcula rateio nem métricas derivadas."""
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-antigo.csv", "07/09/2026 - 15/09/2026",
                 despesas="900.00", gmv="9000.00", impressoes="90000", cliques="900")
    _escreve_csv(d / "Dados-novo.csv", "14/09/2026 - 22/09/2026",
                 despesas="450.00", gmv="9000.00", impressoes="45000", cliques="450")

    linha = _por_data(_parser_ads.parse_brand_ads(tmp_path, "apice"))[date(2026, 9, 15)]

    assert linha["ad_spend"] == round(450.00 / 9, 2)
    assert linha["ad_revenue"] == round(9000.00 / 9, 2)
    assert linha["ad_impressions"] == int(round(45000 / 9))
    assert linha["ad_clicks"] == int(round(450 / 9))
    # derivadas continuam vindo dos TOTAIS do vencedor, nao da media diaria
    assert linha["roas"] == round(9000.00 / 450.00, 4)
    assert linha["acos_pct"] == round(450.00 / 9000.00 * 100, 4)
    assert linha["ctr_pct"] == round(450 / 45000 * 100, 4)
    assert linha["cpc"] == round(450.00 / 450, 4)


def test_grain_e_campos_do_canonical_inalterados(tmp_path):
    """Nenhum campo novo, nenhum removido: o grão continua (brand, date)."""
    d = tmp_path / "apice"; d.mkdir()
    _escreve_csv(d / "Dados-a.csv", "01/09/2026 - 07/09/2026")
    linha = _parser_ads.parse_brand_ads(tmp_path, "apice")[0]
    assert set(linha) == {
        "date", "brand", "ad_spend", "ad_revenue", "ad_impressions",
        "ad_clicks", "roas", "acos_pct", "ctr_pct", "cpc",
    }


# --- 12) Orders e Shop Stats intactos -------------------------------------

def test_orders_e_shop_stats_nao_dependem_da_dedup_de_ads():
    """A mudança é confinada ao Ads: os outros dois parsers não importam nada
    daqui, e o módulo de snapshots de Orders não foi tocado."""
    from pathlib import Path
    raiz = Path(_parser_ads.__file__).parent
    for nome in ("_parser.py", "_parser_shop_stats.py"):
        texto = (raiz / nome).read_text(encoding="utf-8", errors="replace")
        assert "_parser_ads" not in texto
        assert "deduplicar_ads_por_data" not in texto

    snapshots = (raiz / "_snapshots.py").read_text(encoding="utf-8", errors="replace")
    assert "ads" not in snapshots.lower().replace("loads", "").replace("threads", "")


def test_dedup_de_ads_nao_reaproveita_o_modulo_de_orders():
    """`_snapshots.py` modela `Order.all`: multipartes e janela no NOME. Ads
    tira a janela do CABEÇALHO — reaproveitar traria um contrato errado."""
    from pathlib import Path
    texto = Path(_parser_ads.__file__).read_text(encoding="utf-8", errors="replace")
    assert "from pipelines.connectors.shopee._snapshots import" not in texto
    assert "agrupar_snapshots" not in texto
