"""CX-KOKESHI-BOT — JSON do bot (Seller Centre) -> linhas do Neon (036)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from pipelines.cx import kokeshi as K
from pipelines.cx import kokeshi_cli as C

AGORA = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def _payload(**sobre):
    base = {
        "conta": "kokeshi",
        "capturado_em": "05/11/2026 08:30",
        "saude_conta": {
            "nivel": "Bom",
            "pontos_penalidade": 0,
            "punicoes_em_andamento": 0,
            "metricas": {
                "taxa_envio_atrasado": {"valor": "6,2%", "anterior": 4.1},
                "taxa_nao_cumprimento": {"valor": 1.5},
                "taxa_cancelamento": {"valor": 1.0},
                "taxa_resposta_chat": {"valor": 55},
                "nota_loja": {"valor": 4.8},
                "violacoes_graves": 0,
            },
        },
        "chat": {"nao_lidas": 12, "conversas_7d": 300, "conversas_30d": 1200, "aguardando_resposta": 9,
                 "taxa_resposta_pct": 55, "tempo_resposta_medio_min": 42.5, "csat_pct": 91.2,
                 "visitantes": 5000, "conversao_pct": 13.4, "periodo_dias": 30},
        "devolucoes": [
            {"id": "RS1", "pedido": "P1", "criada_em": "2026-11-01 10:00", "status": "Reembolso concluído",
             "motivo": "Não recebi o produto", "valor_reembolso": "R$ 1.234,56"},
            {"id": "RS2", "pedido": "P2", "criada_em": "02/11/2026 09:15", "status": "Cancelada pelo comprador",
             "motivo": "Mudei de ideia", "valor_reembolso": None},
            {"id": "RS3", "criada_em": "2026-11-03T11:00:00-03:00", "status": "Em análise pela Shopee",
             "motivo": "Embalagem chegou molhada"},
        ],
    }
    base.update(sobre)
    return json.dumps(base).encode()


def test_payload_completo_vira_linhas_no_formato_do_data_mart():
    lt = K.ler_payload(_payload(), agora=AGORA)
    assert lt.conta == "kokeshi"
    assert lt.capturado_em == datetime(2026, 11, 5, 11, 30, tzinfo=timezone.utc)
    s = lt.saude
    assert s["overall_rating"] == 3 and s["captured_date"] == date(2026, 11, 5)
    # envio atrasado 6,2 >= 5 (fora); atendimento: resposta 55 < 60 (fora)
    assert (s["fulfillment_failed"], s["listing_failed"], s["custom_service_failed"]) == (1, 0, 1)
    m = {x["metric_id"]: x for x in lt.metricas}
    assert m[1]["current_period"] == 6.2 and m[1]["last_period"] == 4.1
    assert (m[1]["target_value"], m[1]["target_comparator"], m[1]["unit"]) == (5, "<", 2)
    assert m[42]["parent_metric_id"] == 3, "sub-metrica nao conta no 'fora'"
    assert m[52]["current_period"] == 0
    c = lt.chat
    assert (c["unread_count"], c["conversas_30d"], c["aguardando_resposta"]) == (12, 1200, 9)
    assert c["tempo_resposta_medio_s"] == 2550 and c["csat_pct"] == 91.2
    d = {x["return_sn"]: x for x in lt.devolucoes}
    assert (d["RS1"]["status"], d["RS1"]["reason"], d["RS1"]["refund_amount"]) == ("ACCEPTED", "NOT_RECEIPT", 1234.56)
    assert (d["RS2"]["status"], d["RS2"]["reason"]) == ("CANCELLED", "CHANGE_MIND")
    assert d["RS2"]["create_time"] == datetime(2026, 11, 2, 12, 15, tzinfo=timezone.utc)
    assert d["RS3"]["status"] == "JUDGING"
    assert d["RS3"]["reason"] == "Embalagem chegou molhada", "fora do mapa: texto original, nunca some"
    assert lt.motivos_sem_mapa == ["Embalagem chegou molhada"]
    assert len(lt.sha256) == 64


def test_cada_secao_e_opcional_e_ausencia_nunca_vira_zero():
    lt = K.ler_payload(json.dumps({"conta": "kokeshi", "capturado_em": "2026-11-05 08:00",
                                   "chat": {"nao_lidas": 3}}).encode(), agora=AGORA)
    assert lt.saude is None and lt.devolucoes == []
    assert lt.chat["unread_count"] == 3 and lt.chat["conversas_30d"] is None
    lt2 = K.ler_payload(json.dumps({"conta": "kokeshi", "capturado_em": "2026-11-05 08:00",
                                    "saude_conta": {"nivel": 4}}).encode(), agora=AGORA)
    assert lt2.saude["overall_rating"] == 4
    assert lt2.saude["fulfillment_failed"] is None, "sem metrica do grupo = desconhecido"


def test_recusa_lista_todos_os_problemas_de_uma_vez():
    ruim = json.dumps({
        "conta": "kokeshi", "capturado_em": "2026-11-05 08:00",
        "saude_conta": {"nivel": "Ótimo", "metricas": {"taxa_xyz": 1, "taxa_envio_atrasado": {"valor": 140}}},
        "chat": {"nao_lidas": -1, "campo_novo": 3},
        "devolucoes": [{"status": "Concluída"}, {"id": "X", "status": "ok", "criada_em": "31-12-2026"}],
    }).encode()
    with pytest.raises(K.PayloadInvalido) as e:
        K.ler_payload(ruim, agora=AGORA)
    txt = " | ".join(e.value.problemas)
    for trecho in ("nivel", "taxa_xyz", "taxa_envio_atrasado.valor", "chat.nao_lidas", "campo_novo",
                   "devolucoes[0].id", "devolucoes[1]"):
        assert trecho in txt, trecho


@pytest.mark.parametrize("cap,erro", [
    (None, "capturado_em: obrigatório"),
    ("2026-11-09 08:00", "no futuro"),
    ("2026-10-20 08:00", "mais de 3 dias"),
])
def test_capturado_em_obrigatorio_e_recente(cap, erro):
    with pytest.raises(K.PayloadInvalido) as e:
        K.ler_payload(_payload(capturado_em=cap), agora=AGORA)
    assert any(erro in p for p in e.value.problemas)


def test_json_invalido_e_secao_desconhecida():
    with pytest.raises(K.PayloadInvalido):
        K.ler_payload(b"{nao e json", agora=AGORA)
    with pytest.raises(K.PayloadInvalido) as e:
        K.ler_payload(_payload(avaliacoes=[]), agora=AGORA)
    assert any("seções desconhecidas" in p for p in e.value.problemas)


@pytest.mark.parametrize("texto,codigo", [
    ("Reembolso concluído", "ACCEPTED"), ("Aprovada", "ACCEPTED"), ("Cancelado", "CANCELLED"),
    ("Em disputa", "JUDGING"), ("Solicitação pendente", "REQUESTED"), ("Aguardando devolução", "PROCESSING"),
    ("ACCEPTED", "ACCEPTED"), ("Estado novo", None),
])
def test_status_em_portugues(texto, codigo):
    assert K.traduzir_status(texto) == codigo


@pytest.mark.parametrize("texto,codigo", [
    ("Produto diferente da descrição", "DIFFERENT_DESCRIPTION"), ("Não recebi o pedido", "NOT_RECEIPT"),
    ("O produto vazou", "SPILLED_CONTENTS"), ("Produto danificado", "DAMAGED_OTHERS"),
    ("Item faltando", "ITEM_MISSING"), ("Recebi o produto errado", "WRONG_ITEM"),
    ("Produto vencido", "EXPIRED_PRODUCT"), ("Suspeita de produto falsificado", "ITEM_FAKE"),
    ("-", None), ("", None),
])
def test_motivo_em_portugues(texto, codigo):
    assert K.traduzir_motivo(texto) == codigo


def test_catalogo_bate_com_os_ids_que_a_api_rotula():
    """Os ids do catalogo sao os que a Torre ja' rotula para as outras lojas."""
    ids = {v[0] for v in K.CATALOGO.values()}
    assert ids == {1, 3, 42, 43, 2032, 2033, 2037, 12, 52, 53, 11, 22}


# ---------------------------------------------------------------------------
# CLI com banco dublê
# ---------------------------------------------------------------------------
class Cursor:
    def __init__(self, banco):
        self.banco = banco
        self._ultimo = None

    def execute(self, sql, params=None):
        self.banco.sqls.append((sql, params))
        if sql.startswith("SELECT to_regclass"):
            self._ultimo = {"tem_036": self.banco.tem_036, "somente_leitura": "off"}
        elif "FROM marts.expedicao_conta_export" in sql:
            self._ultimo = self.banco.conta
        elif "pg_try_advisory_xact_lock" in sql:
            self._ultimo = {"ok": self.banco.lock_livre}
        else:
            self._ultimo = None
        if self.banco.falhar_em and self.banco.falhar_em in sql:
            raise RuntimeError("senha=segredo")

    def fetchone(self):
        return self._ultimo


class Banco:
    def __init__(self, tem_036=True, conta=None, lock_livre=True, falhar_em=None):
        self.tem_036, self.lock_livre, self.falhar_em = tem_036, lock_livre, falhar_em
        self.conta = conta if conta is not None else {"brand": "kokeshi", "ativo": True}
        self.sqls, self.valores, self.commits, self.rollbacks, self.fechado = [], [], 0, 0, False

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.fechado = True


def _rodar(tmp_path: Path, banco: Banco, *, aplicar=True, conteudo=None):
    arq = tmp_path / "kokeshi_cx.json"
    arq.write_bytes(conteudo if conteudo is not None else _payload())
    saida: list[str] = []

    def execute_values(cur, sql, linhas, page_size=100):
        banco.valores.append((sql, linhas))
        if banco.falhar_em and banco.falhar_em in sql:
            raise RuntimeError("senha=segredo")

    code = C.executar(arq, aplicar=aplicar, agora=AGORA, abrir=lambda: banco, saida=saida.append,
                      execute_values=execute_values)
    return code, saida


def test_previa_nao_grava(tmp_path):
    banco = Banco()
    code, saida = _rodar(tmp_path, banco, aplicar=False)
    assert code == C.EXIT_OK and banco.valores == [] and banco.commits == 0
    texto = "\n".join(saida)
    assert "PRÉVIA" in texto and "Devoluções: 3" in texto and "Embalagem chegou molhada" in texto


def test_apply_grava_as_quatro_tabelas_numa_transacao(tmp_path):
    banco = Banco()
    code, saida = _rodar(tmp_path, banco)
    assert code == C.EXIT_OK and banco.commits == 1 and "GRAVADO." in saida
    tabelas = [sql.split(" (")[0].replace("INSERT INTO ", "") for sql, _ in banco.valores]
    assert tabelas == [C.SAUDE, C.METRICA, C.CHAT, C.RETURNS]
    assert any(f"DELETE FROM {C.METRICA}" in s for s, _ in banco.sqls), "metricas do dia trocadas inteiras"
    saude = banco.valores[0][1][0]
    assert saude[0] == "kokeshi" and len(saude) == len(C.COLS_SAUDE)
    assert "WHERE EXCLUDED.captured_at >= shopee_returns_export.captured_at" in C.UPSERT_RETURNS


def test_precondicoes(tmp_path):
    assert _rodar(tmp_path, Banco(tem_036=False))[0] == C.EXIT_PRECONDICAO
    assert _rodar(tmp_path, Banco(conta={}))[0] == C.EXIT_PRECONDICAO
    assert _rodar(tmp_path, Banco(conta={"brand": "kokeshi", "ativo": False}))[0] == C.EXIT_PRECONDICAO
    assert _rodar(tmp_path, Banco(lock_livre=False))[0] == C.EXIT_LOCK_OCUPADO


def test_json_recusado_nem_abre_o_banco(tmp_path):
    aberto = []
    arq = tmp_path / "x.json"
    arq.write_bytes(b"[]")
    code = C.executar(arq, aplicar=True, agora=AGORA, abrir=lambda: aberto.append(1), saida=lambda s: None)
    assert code == C.EXIT_RECUSADO and aberto == []


def test_falha_de_banco_faz_rollback_e_nao_vaza_texto(tmp_path):
    banco = Banco(falhar_em=C.RETURNS)
    code, saida = _rodar(tmp_path, banco)
    assert code == C.EXIT_FALHA and banco.commits == 0 and banco.rollbacks >= 1 and banco.fechado
    assert "segredo" not in "\n".join(saida)


def test_upserts_nao_tocam_colunas_de_chave():
    assert "brand = EXCLUDED.brand" not in C.UPSERT_SAUDE
    assert "return_sn = EXCLUDED.return_sn" not in C.UPSERT_RETURNS
    assert C.UPSERT_CHAT.count("ON CONFLICT (brand, captured_at)") == 1


def test_colunas_da_cli_existem_na_migration_036():
    fonte = (Path(__file__).resolve().parents[2] / "apps/api/alembic/versions/036_cx_kokeshi_export.py").read_text(
        encoding="utf-8")
    for col in set(C.COLS_SAUDE + C.COLS_METRICA + C.COLS_CHAT + C.COLS_RETURNS):
        assert f"{col} " in fonte, col
