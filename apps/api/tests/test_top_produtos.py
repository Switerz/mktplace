"""Top 5 produtos por marca (Gerencial): escopo de marcas, meses e montagem."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.top_produtos_service import get_top_produtos, limpar_nome


def test_limpar_nome_tira_ean_marcador_e_posicao():
    assert limpar_nome("7898652877971 - (N)Ativador e Modelador Anti-frizz Cachos 500gr - I4") == \
        "Ativador e Modelador Anti-frizz Cachos 500gr"
    assert limpar_nome("7908790700687-SERUM ADEUS OLHEIRAS KOKESHI 10ML -F5") == "SERUM ADEUS OLHEIRAS KOKESHI 10ML"
    assert limpar_nome("7908787702625 - DEO COLONIA NO 13 - 25ML - Masculino - E7(B)") == \
        "DEO COLONIA NO 13 - 25ML - Masculino"
    assert limpar_nome("PREBIOTICA FIBRAS 200G - RITUARIA . .") == "PREBIOTICA FIBRAS 200G - RITUARIA"
    assert limpar_nome("Creme Hidratante Facial Pele De Porcelana - Kokeshi 30g") == \
        "Creme Hidratante Facial Pele De Porcelana - Kokeshi 30g"


def test_monta_marcas_com_share_preco_e_meses():
    chamadas = []

    def consultar(sql, params):
        chamadas.append(params)
        return [
            {"marca": "kokeshi", "sku": "20002", "produto": "7908790700120 - Creme Gota de Colágeno - F6",
             "categoria": "Skin Care", "pecas": Decimal("100"), "receita": Decimal("2500"),
             "receita_marca": Decimal("10000"), "posicao": 1, "nf_ate": "2026-11-05"},
            {"marca": "kokeshi", "sku": "KS03016", "produto": "Olhos de Gueixa", "categoria": None,
             "pecas": 0, "receita": 50.0, "receita_marca": 10000.0, "posicao": 2, "nf_ate": "2026-11-05"},
        ]

    r = get_top_produtos(consultar, date(2026, 10, 15), date(2026, 11, 5), ["kokeshi", "denavita"])

    assert chamadas[0]["marcas"] == ["kokeshi"]  # fora do escopo da Torre nao entra
    assert (r.mes_inicio, r.mes_fim, r.nf_ate) == (date(2026, 10, 1), date(2026, 11, 1), date(2026, 11, 5))
    [k] = r.marcas
    assert k.receita_marca == 10000
    top1, top2 = k.produtos
    assert top1.produto == "Creme Gota de Colágeno"
    assert top1.preco_medio == 25 and top1.share_marca == 0.25
    assert top2.preco_medio is None  # sem pecas, sem preco (nunca divisao por zero)


def test_sem_marca_no_escopo_nao_consulta():
    def consultar(sql, params):
        raise AssertionError("nao deveria consultar")

    r = get_top_produtos(consultar, date(2026, 11, 1), date(2026, 11, 5), ["denavita"])
    assert r.marcas == []
