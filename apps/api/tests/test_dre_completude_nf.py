"""T-0064 — buraco de numeracao de NF chega a' DRE como CMV `parcial`.

O publisher de insumos nao muda valor nem status quando a numeracao de NF da
marca tem buraco no mes: acrescenta ao `motivo` do CMV o trecho
`buraco de numeracao de NF em DD/MM (N numeros)`. O CHECK de status da 025 nao
tem `parcial`, entao e' o serving que le o trecho e poe o CMV em `parcial`
(fora da margem conhecida), com o MESMO valor publicado.

Numeros sinteticos.
"""
from __future__ import annotations

from app.schemas.dre import DREResponse
from app.services import dre_service as dre

from tests.test_dre_mvp import celula, cmv, mart, montar

BURACO = "buraco de numeracao de NF em 03/10 (1.914 numeros)"


def cmv_com(motivo: str, status="reconciliado", marca="kokeshi", **kw):
    return cmv("ml", marca=marca, status=status, motivo=motivo, **kw)


def test_buraco_poe_o_cmv_em_parcial_sem_mudar_o_valor():
    c = celula("ml", c=cmv_com(f"receita fiscal / GMV = 1.0000; {BURACO}"))
    cm = c["componentes"]["cmv"]
    assert cm["status"] == dre.COBERTURA_PARCIAL
    assert cm["valor"] == 300_000.0  # o publicado, nada extrapolado
    assert cm["motivo"].startswith(BURACO + ": notas ausentes da base fiscal")
    assert "(publicacao: reconciliado)" in cm["motivo"]
    assert c["classificacao"] == dre.NAO_CALCULAVEL
    assert c["margem_contribuicao_conhecida"]["valor"] is None
    assert any(m.startswith("cmv parcial") for m in c["motivos_classificacao"])


def test_buraco_prevalece_sobre_nao_reconciliado_e_guarda_o_status_publicado():
    """Apice out/26: razao 0,911 causada pelo buraco de 03/10."""
    motivo = f"receita fiscal / GMV = 0.9110; aceito entre 0.95 e 1.05; {BURACO}"
    cm = celula("ml", c=cmv_com(motivo, status="nao_reconciliado"))["componentes"]["cmv"]
    assert cm["status"] == dre.COBERTURA_PARCIAL
    assert "(publicacao: nao_reconciliado)" in cm["motivo"]


def test_sem_buraco_nada_muda():
    assert celula("ml", c=cmv_com("receita fiscal / GMV = 1.0000"))["componentes"]["cmv"]["status"] == dre.DISPONIVEL
    cm = celula("ml", c=cmv_com("receita fiscal / GMV = 0.5000", status="nao_reconciliado"))["componentes"]["cmv"]
    assert cm["status"] == dre.NAO_RECONCILIADO


def test_rotulo_so_vale_como_trecho_proprio_do_motivo():
    """Texto que so' cita o rotulo no meio de outro trecho nao vira parcial."""
    cm = celula("ml", c=cmv_com("receita fiscal / GMV = 1.0000; nota: sem buraco de numeracao de NF em 03/10"))
    assert cm["componentes"]["cmv"]["status"] == dre.DISPONIVEL


def test_buraco_em_uma_marca_nao_afeta_a_outra_e_o_agregado_fica_parcial():
    r = montar(
        [mart("ml", "kokeshi"), mart("ml", "barbours")],
        cmv_rows=[cmv_com("receita fiscal / GMV = 1.0000", marca="kokeshi"),
                  cmv_com(f"receita fiscal / GMV = 1.0000; {BURACO}", marca="barbours")],
        canais=("ml",),
    )
    por_marca = {c["marca"]: c["componentes"]["cmv"]["status"] for c in r["celulas_base"]}
    assert por_marca == {"kokeshi": dre.DISPONIVEL, "barbours": dre.COBERTURA_PARCIAL}
    assert r["total"]["componentes"]["cmv"]["status"] == dre.COBERTURA_PARCIAL
    DREResponse.model_validate(r)
