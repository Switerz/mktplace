"""Report diario das 8h (R1): regra pura sobre GMV, metas, LDR e Kokeshi."""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from app.services import metas_curva
from app.services import torre_alertas as ta
from app.services import torre_report as tr

ONTEM = date(2026, 11, 4)  # quarta
SEMANA = ONTEM - timedelta(days=7)


def _dados(gmv=None, metas=None, ldr=None, saude=None):
    return tr.Dados(ONTEM, gmv or {}, metas or {}, ldr or ta.LeituraLdr(), saude or ta.LeituraSaude())


def test_meta_do_dia_usa_a_curva_do_canal():
    meta = 3_000_000.0
    alvo = meta * metas_curva.fracao_do_dia(ONTEM, "ml")
    gmv = {("kokeshi", "ml", ONTEM): alvo * 1.1, ("kokeshi", "tiktok", ONTEM): 50_000.0}
    rep = tr.compor_report(_dados(gmv, {("kokeshi", "ml"): meta}))
    k = next(x for x in rep.linhas if x.marca == "kokeshi")
    assert abs(k.pct_meta_dia - 1.1) < 1e-9          # so' canal com meta
    assert k.gmv == alvo * 1.1 + 50_000.0            # GMV mostra todos os canais
    assert "KOKESHI bateu a meta do dia (110%)." in rep.avancos


def test_abaixo_da_meta_do_dia_critico_e_atencao():
    meta = 1_000_000.0
    alvo = meta * metas_curva.fracao_do_dia(ONTEM, "shopee")
    gmv = {("apice", "shopee", ONTEM): alvo * 0.6, ("lescent", "shopee", ONTEM): alvo * 0.75}
    rep = tr.compor_report(_dados(gmv, {("apice", "shopee"): meta, ("lescent", "shopee"): meta}))
    assert f"{tr.CRIT} ÁPICE fez 60% da meta do dia." in rep.problemas
    assert f"{tr.ATEN} LESCENT fez 75% da meta do dia." in rep.problemas


def test_variacao_contra_a_semana_passada_com_piso():
    gmv = {("barbours", "ml", ONTEM): 130_000.0, ("barbours", "ml", SEMANA): 100_000.0,
           ("rituaria", "ml", ONTEM): 70_000.0, ("rituaria", "ml", SEMANA): 100_000.0,
           ("lescent", "ml", ONTEM): 9_000.0, ("lescent", "ml", SEMANA): 2_000.0}
    rep = tr.compor_report(_dados(gmv))
    assert "BARBOURS cresceu +30% contra a qua passada." in rep.avancos
    assert any(p.startswith(f"{tr.ATEN} RITUÁRIA caiu −30%") for p in rep.problemas)
    assert not any("LESCENT" in t for t in rep.avancos + rep.problemas)  # base < piso


def test_ritmo_do_mes():
    meta = 3_000_000.0
    esperado = meta * metas_curva.fracao_ate(ONTEM, "ml")
    gmv = {("kokeshi", "ml", ONTEM.replace(day=1)): esperado * 0.4,
           ("kokeshi", "ml", ONTEM.replace(day=2)): esperado * 0.4}
    rep = tr.compor_report(_dados(gmv, {("kokeshi", "ml"): meta}))
    assert rep.mes == (esperado * 0.8, esperado, meta)
    assert any("Mês abaixo do ritmo da meta (80%" in p for p in rep.problemas)


def test_canal_sem_gmv_ontem_e_carga_atrasada():
    rep = tr.compor_report(_dados({("kokeshi", "shopee", SEMANA): 100_000.0}))
    assert f"{tr.ATEN} Shopee sem GMV ontem: a carga pode estar atrasada." in rep.problemas


def test_ldr_e_kokeshi_entram_em_problemas_e_avancos():
    ldr = ta.LeituraLdr(celulas=[
        ta.CelulaLdr("mercadolivre", "rituaria", 0.11, 9, 82, ta.CRITICO),
        ta.CelulaLdr("tiktokshop", "kokeshi", 0.03, 371, 12503, ta.ATENCAO),
        ta.CelulaLdr("shopee", "apice", 0.015, 18, 1187, ta.DENTRO),
    ], indisponiveis=["Mercado Livre"])
    saude = ta.LeituraSaude(sinais=[ta.SinalSaude("kokeshi", "mercadolivre", ta.ATENCAO, "x", "perto", ONTEM)])
    rep = tr.compor_report(_dados(ldr=ldr, saude=saude))
    assert rep.problemas[0].startswith(f"{tr.CRIT} RITUÁRIA no Mercado Livre: LDR 11,0%")
    assert rep.problemas[1].startswith(f"{tr.ATEN} KOKESHI no TikTok Shop")
    assert f"{tr.ATEN} perto" in rep.problemas
    assert "LDR na meta em 1 de 3 contas." in rep.avancos
    assert rep.notas == ["Sem LDR agora de: Mercado Livre."]


def test_kokeshi_em_ordem_e_avanco():
    rep = tr.compor_report(_dados(saude=ta.LeituraSaude(em_ordem=["kokeshi"])))
    assert any(a.startswith("KOKESHI no Mercado Livre: reputação no verde") for a in rep.avancos)


def test_mensagem_abre_com_o_resumo_e_tem_chave_do_dia():
    gmv = {("kokeshi", "ml", ONTEM): 1_234_567.0}
    rep = tr.compor_report(_dados(gmv, {("kokeshi", "ml"): 30_000_000.0}))
    texto = tr.montar_mensagem(rep)
    assert texto.splitlines()[0].startswith(
        ":sunrise: *Bom dia! Ontem (qua, 04/11): R$ 1,23 mi de GMV nas marcas,")
    assert "Novembro até ontem:" in texto and "(Mercado Livre)" in texto
    assert tr.chave_do_report(rep) == "report:2026-11-04"


def test_mensagem_sem_nada_diz_que_nao_ha_problema():
    texto = tr.montar_mensagem(tr.compor_report(_dados()))
    assert "0 avanços, 0 problemas" in texto and "Nenhum problema." in texto


def test_formatos_em_reais():
    assert tr.brl(375_700) == "R$ 375,7 mil"
    assert tr.brl(13_450_000) == "R$ 13,45 mi"
    assert tr.brl(950) == "R$ 950"


def test_curva_python_igual_a_do_web():
    """As duas copias da curva (web e API) precisam bater."""
    ts = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "metas-curva.ts"
    src = ts.read_text(encoding="utf-8")
    for mes, dias in metas_curva.CURVAS.items():
        bloco = src[src.index(f'"{mes}": ['):]
        bloco = bloco[: bloco.index("],")]
        web = [(None if e == "null" else e.strip('"'), float(m), float(s)) for e, m, s in re.findall(
            r'\{ evento: (null|"[^"]*"), ml: ([\d.]+), shopee: ([\d.]+) \}', bloco)]
        assert web == [(e, float(m), float(s)) for e, m, s in dias]


def test_blocos_do_report_numeros_marcas_e_atalhos():
    meta = 3_000_000.0
    alvo = meta * metas_curva.fracao_do_dia(ONTEM, "ml")
    gmv = {("kokeshi", "ml", ONTEM): alvo * 0.75, ("kokeshi", "ml", SEMANA): alvo}
    m = tr.montar_blocos(tr.compor_report(_dados(gmv, {("kokeshi", "ml"): meta})))
    assert m.texto.startswith(":sunrise: Bom dia! R$ ")
    titulo, resumo, numeros = m.blocos[:3]
    assert titulo["text"]["text"] == ":sunrise: Bom dia! Resumo de ontem · qua, 04/11"
    rotulos = [f["text"].split("\n")[0] for f in numeros["fields"]]
    assert rotulos[:3] == ["*GMV de ontem*", "*Meta do dia*", "*vs qua passada*"]
    marcas = next(b for b in m.blocos if "GMV por marca" in b.get("text", {}).get("text", ""))
    assert ":large_orange_circle: *KOKESHI*" in marcas["text"]["text"]  # 75%: entre 70% e 80%
    assert marcas["accessory"]["url"].endswith("/metas")
    assert [b["text"]["text"] for b in m.blocos[-1]["elements"]] == ["Metas", "Expedição", "Performance"]


def test_bolas_da_meta():
    assert [tr._bola_meta(p) for p in (None, 1.0, 0.85, 0.72, 0.5)] == [
        ":white_circle:", ":large_green_circle:", ":large_yellow_circle:",
        ":large_orange_circle:", ":red_circle:"]
