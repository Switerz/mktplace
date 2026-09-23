"""Gate AVH-5B — testes do coletor read-only da Avoe.

Nenhum teste toca a Avoe, o navegador, o Neon ou o Data Mart. As respostas sao
sinteticas e a fronteira exercitada e' exatamente a que o coletor tem na
producao: o envelope `{status, rows, content_range}`.
"""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipelines.avoe import snapshot_collect as sc
from pipelines.avoe.snapshot_collect import (
    BrowserPageReader,
    ReadOnlyGuard,
    SnapshotCollectError,
    TABLE_SPECS,
    collect_snapshot,
    collect_table,
    render_jsonl,
)
from pipelines.avoe.snapshot_contract import SnapshotContractError, read_snapshot

SPEC_TARGETS, SPEC_CHANNELS = TABLE_SPECS

RELOGIO = lambda: datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731


# --------------------------------------------------------------------------
# Leitores sinteticos
# --------------------------------------------------------------------------

class FakeReader:
    """Devolve paginas pre-montadas. Registra o que foi pedido."""

    def __init__(self, paginas: dict[str, list[dict]]) -> None:
        self.paginas = paginas
        self.pedidos: list[tuple[str, int, int]] = []

    def fetch(self, table: str, offset: int, limit: int) -> dict:
        self.pedidos.append((table, offset, limit))
        fila = self.paginas[table]
        if not fila:
            raise AssertionError(f"{table}: pagina a mais pedida (offset={offset})")
        return fila.pop(0)


def env(rows: list[dict], total: int, status: int = 206,
        content_range: str | None = None) -> dict:
    if content_range is None:
        content_range = f"0-{max(len(rows) - 1, 0)}/{total}"
    return {"status": status, "rows": rows, "content_range": content_range}


def linha_meta(i: int, mes: str = "2026-08-01", marca: str | None = None) -> dict:
    return {
        "id": i,
        "marca": marca or f"Marca{i:04d}",
        "mes_referencia": mes,
        "meta": "1000.00",
        "faturamento": "900.00",
        "criado_em": "2026-08-01T10:00:00+00:00",
        "atualizado_em": "2026-08-02T10:00:00+00:00",
    }


def linha_canal(i: int, data: str = "2026-06-15", plataforma: str = "SHEIN",
                marca: str | None = None) -> dict:
    return {
        "id": i,
        "marca": marca or "Kokeshi",
        "plataforma": plataforma,
        "data_referencia": data,
        "faturamento": "123.45",
        "ads": "10.00",
        "criado_em": "2026-06-15T10:00:00+00:00",
    }


def reader_minimo() -> FakeReader:
    """Uma pagina curta por tabela — o caminho feliz mais barato."""
    metas = [linha_meta(1), linha_meta(2)]
    canais = [linha_canal(1), linha_canal(2, data="2026-06-16")]
    return FakeReader({
        "resumo_marca_mes": [env(metas, 2)],
        "faturamento_diario_marca": [env(canais, 2)],
    })


# --------------------------------------------------------------------------
# Paginacao
# --------------------------------------------------------------------------

def test_paginacao_completa_le_todas_as_paginas():
    p1 = [linha_meta(i) for i in range(1, 4)]
    p2 = [linha_meta(i) for i in range(4, 7)]
    p3 = [linha_meta(7)]
    reader = FakeReader({"resumo_marca_mes": [
        env(p1, 7), env(p2, 7), env(p3, 7),
    ]})

    cap = collect_table(reader, SPEC_TARGETS, page_size=3)

    assert len(cap.rows) == 7
    assert cap.pages == 3
    assert cap.declared_total == 7
    assert [o for _, o, _ in reader.pedidos] == [0, 3, 6]


def test_ultima_pagina_parcial_encerra_sem_pagina_extra():
    p1 = [linha_meta(i) for i in range(1, 4)]
    p2 = [linha_meta(4)]  # 1 < 3 => fim
    reader = FakeReader({"resumo_marca_mes": [env(p1, 4), env(p2, 4)]})

    cap = collect_table(reader, SPEC_TARGETS, page_size=3)

    assert len(cap.rows) == 4
    assert cap.pages == 2


def test_pagina_exatamente_cheia_pede_a_seguinte_e_aceita_vazia():
    """Total multiplo do page_size: a pagina vazia final e' obrigatoria."""
    p1 = [linha_meta(i) for i in range(1, 3)]
    reader = FakeReader({"resumo_marca_mes": [env(p1, 2), env([], 2)]})

    cap = collect_table(reader, SPEC_TARGETS, page_size=2)

    assert len(cap.rows) == 2
    assert cap.pages == 2


def test_pagina_repetida_falha():
    p1 = [linha_meta(i) for i in range(1, 4)]
    reader = FakeReader({"resumo_marca_mes": [env(p1, 6), env(list(p1), 6)]})

    with pytest.raises(SnapshotCollectError, match="pagina REPETIDA"):
        collect_table(reader, SPEC_TARGETS, page_size=3)


def test_pagina_ausente_falha():
    """A fonte diz 10 e entrega 3: o buraco nao vira snapshot."""
    p1 = [linha_meta(i) for i in range(1, 4)]
    reader = FakeReader({"resumo_marca_mes": [env(p1, 10)]})

    with pytest.raises(SnapshotCollectError, match="pagina AUSENTE"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_zero_linha_e_recusado_e_nao_vira_snapshot():
    """Com RLS, `200 []` e' sessao sem permissao — nao e' tabela vazia.

    Foi o desfecho real da primeira tentativa de captura deste gate: a sonda
    de prontidao aceitou a tela de login como sessao, o PostgREST respondeu
    sem erro e sem dado, e a captura terminou com exit 0 e dois arquivos
    vazios. Um snapshot vazio com manifesto valido e' pior que uma falha.
    """
    reader = FakeReader({"resumo_marca_mes": [
        {"status": 200, "rows": [], "content_range": "*/0"},
    ]})

    with pytest.raises(SnapshotCollectError, match="zero linha"):
        collect_table(reader, SPEC_TARGETS, page_size=1000)


def test_zero_linha_nao_grava_arquivo_nenhum(tmp_path):
    reader = FakeReader({
        "resumo_marca_mes": [{"status": 200, "rows": [], "content_range": "*/0"}],
    })
    destino = tmp_path / "cap"

    with pytest.raises(SnapshotCollectError, match="zero linha"):
        collect_snapshot(reader, destino, now=RELOGIO)

    assert not destino.exists()


def test_mudanca_de_contagem_no_meio_falha():
    p1 = [linha_meta(i) for i in range(1, 4)]
    p2 = [linha_meta(i) for i in range(4, 6)]
    reader = FakeReader({"resumo_marca_mes": [env(p1, 5), env(p2, 6)]})

    with pytest.raises(SnapshotCollectError, match="total declarado mudou"):
        collect_table(reader, SPEC_TARGETS, page_size=3)


def test_resposta_truncada_ao_contrario_falha():
    """Mais linhas do que o limite pedido: a fonte nao respeitou o contrato."""
    reader = FakeReader({"resumo_marca_mes": [
        env([linha_meta(i) for i in range(1, 6)], 5),
    ]})

    with pytest.raises(SnapshotCollectError, match="truncada"):
        collect_table(reader, SPEC_TARGETS, page_size=2)


def test_content_range_ausente_falha():
    reader = FakeReader({"resumo_marca_mes": [
        {"status": 200, "rows": [linha_meta(1)], "content_range": None},
    ]})

    with pytest.raises(SnapshotCollectError, match="Content-Range ausente"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_content_range_malformado_falha():
    reader = FakeReader({"resumo_marca_mes": [
        {"status": 200, "rows": [linha_meta(1)], "content_range": "0-0/"},
    ]})

    with pytest.raises(SnapshotCollectError, match="malformado"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_teto_de_paginas_impede_laco_infinito(monkeypatch):
    monkeypatch.setattr(sc, "MAX_PAGES", 3)
    # Total mentiroso: cada pagina vem cheia e o fim nunca chega.
    paginas = [env([linha_meta(i + k) for k in range(2)], 999) for i in (1, 3, 5, 7)]
    reader = FakeReader({"resumo_marca_mes": paginas})

    with pytest.raises(SnapshotCollectError, match="teto de 3 paginas"):
        collect_table(reader, SPEC_TARGETS, page_size=2)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

def test_coluna_obrigatoria_ausente_falha():
    linha = linha_meta(1)
    del linha["meta"]
    reader = FakeReader({"resumo_marca_mes": [env([linha], 1)]})

    with pytest.raises(SnapshotCollectError, match="obrigatoria ausente: meta"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_coluna_nova_falha_e_nomeia_a_coluna():
    linha = linha_meta(1)
    linha["meta_trimestral"] = "9999.00"
    reader = FakeReader({"resumo_marca_mes": [env([linha], 1)]})

    with pytest.raises(SnapshotCollectError, match="coluna nova na origem: meta_trimestral"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_coluna_proibida_falha():
    linha = linha_canal(1)
    linha["cpf"] = "000"
    reader = FakeReader({"faturamento_diario_marca": [env([linha], 1)]})

    with pytest.raises(SnapshotCollectError, match="coluna PROIBIDA"):
        collect_table(reader, SPEC_CHANNELS, page_size=5)


def test_mudanca_de_schema_entre_paginas_falha():
    """A primeira pagina esta no contrato; a segunda ganhou campo."""
    p1 = [linha_meta(1), linha_meta(2)]
    nova = linha_meta(3)
    nova["canal_meta"] = "x"
    reader = FakeReader({"resumo_marca_mes": [env(p1, 3), env([nova], 3)]})

    with pytest.raises(SnapshotCollectError, match="coluna nova na origem: canal_meta"):
        collect_table(reader, SPEC_TARGETS, page_size=2)


def test_objeto_que_nao_e_dict_falha():
    reader = FakeReader({"resumo_marca_mes": [env(["texto solto"], 1)]})

    with pytest.raises(SnapshotCollectError, match="objeto esperado"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_corpo_que_nao_e_lista_falha():
    reader = FakeReader({"resumo_marca_mes": [
        {"status": 200, "rows": {"marca": "x"}, "content_range": "0-0/1"},
    ]})

    with pytest.raises(SnapshotCollectError, match="nao e' lista"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


# --------------------------------------------------------------------------
# Chaves
# --------------------------------------------------------------------------

def test_duplicata_de_chave_de_origem_falha():
    a = linha_meta(1, marca="Kokeshi")
    b = linha_meta(2, marca="Kokeshi")  # mesmo mes + mesma marca, `id` diferente
    reader = FakeReader({"resumo_marca_mes": [env([a, b], 2)]})

    with pytest.raises(SnapshotCollectError, match="duplicata na chave de origem"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_duplicata_de_chave_em_canais_falha():
    a = linha_canal(1)
    b = linha_canal(2)  # mesma data + marca + plataforma
    reader = FakeReader({"faturamento_diario_marca": [env([a, b], 2)]})

    with pytest.raises(SnapshotCollectError, match="duplicata na chave de origem"):
        collect_table(reader, SPEC_CHANNELS, page_size=5)


def test_id_ausente_falha():
    """`id` e' a ancora do offset: sem ele a paginacao nao e' reproduzivel."""
    linha = linha_meta(1)
    del linha["id"]
    reader = FakeReader({"resumo_marca_mes": [env([linha], 1)]})

    with pytest.raises(SnapshotCollectError, match="`id` ausente ou repetido"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_validacao_de_ancora_tambem_pega_id_ausente():
    """Defesa em profundidade: a checagem final nao depende da checagem por pagina."""
    linha = linha_meta(1)
    del linha["id"]

    with pytest.raises(SnapshotCollectError, match="sem `id`"):
        sc._valida_ancora([linha], SPEC_TARGETS)


def test_validacao_de_ancora_pega_id_repetido_entre_paginas():
    with pytest.raises(SnapshotCollectError, match="`id` repetido"):
        sc._valida_ancora([linha_meta(1, marca="Apice"),
                           linha_meta(1, marca="Kokeshi")], SPEC_TARGETS)


def test_id_repetido_dentro_da_pagina_falha():
    a = linha_meta(1, marca="Apice")
    b = linha_meta(1, marca="Kokeshi")  # mesmo id, marcas diferentes
    reader = FakeReader({"resumo_marca_mes": [env([a, b], 2)]})

    with pytest.raises(SnapshotCollectError, match="`id` ausente ou repetido"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


# --------------------------------------------------------------------------
# Status HTTP
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
def test_status_de_erro_para_a_coleta(status):
    reader = FakeReader({"resumo_marca_mes": [
        {"status": status, "rows": [], "content_range": None},
    ]})

    with pytest.raises(SnapshotCollectError, match=f"HTTP {status}"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


@pytest.mark.parametrize("status", [200, 206])
def test_status_de_sucesso_aceitos(status):
    reader = FakeReader({"resumo_marca_mes": [
        env([linha_meta(1)], 1, status=status),
    ]})

    assert len(collect_table(reader, SPEC_TARGETS, page_size=5).rows) == 1


def test_erro_de_status_na_segunda_pagina_para_tudo():
    p1 = [linha_meta(1), linha_meta(2)]
    reader = FakeReader({"resumo_marca_mes": [
        env(p1, 4), {"status": 401, "rows": [], "content_range": None},
    ]})

    with pytest.raises(SnapshotCollectError, match="HTTP 401"):
        collect_table(reader, SPEC_TARGETS, page_size=2)


@pytest.mark.parametrize("status,trecho", [
    (-1, "nao expoe a aplicacao autenticada"),
    (-2, "falha de rede"),
    (-3, "nao e' JSON"),
])
def test_falhas_sinteticas_do_leitor_tem_mensagem_propria(status, trecho):
    reader = FakeReader({"resumo_marca_mes": [
        {"status": status, "rows": [], "content_range": None},
    ]})

    with pytest.raises(SnapshotCollectError, match=re.escape(trecho)):
        collect_table(reader, SPEC_TARGETS, page_size=5)


# --------------------------------------------------------------------------
# Timeout
# --------------------------------------------------------------------------

class PaginaQueEstoura:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def evaluate(self, *args, **kwargs):
        raise self.exc


def test_timeout_do_navegador_vira_falha_de_coleta():
    class TimeoutError_(Exception):
        pass

    leitor = BrowserPageReader(PaginaQueEstoura(TimeoutError_("Timeout 60000ms")))

    with pytest.raises(SnapshotCollectError, match="nao concluida no navegador"):
        leitor.fetch("resumo_marca_mes", 0, 1000)


def test_timeout_nao_vaza_a_mensagem_original():
    """A excecao de origem pode carregar URL com query. Nao e' reemitida."""
    segredo = "Timeout ao chamar https://x/rest/v1/t?apikey=SEGREDO123"
    leitor = BrowserPageReader(PaginaQueEstoura(RuntimeError(segredo)))

    with pytest.raises(SnapshotCollectError) as exc:
        leitor.fetch("resumo_marca_mes", 0, 1000)

    texto = str(exc.value) + repr(exc.value.__cause__) + repr(exc.value.__context__)
    assert "SEGREDO123" not in texto
    assert "apikey" not in texto


def test_timeout_na_segunda_pagina_nao_grava_nada(tmp_path):
    class ReaderQueCai:
        def __init__(self):
            self.n = 0

        def fetch(self, table, offset, limit):
            self.n += 1
            if self.n == 1:
                return env([linha_meta(1), linha_meta(2)], 4)
            raise SnapshotCollectError("leitura nao concluida no navegador")

    destino = tmp_path / "captura"
    with pytest.raises(SnapshotCollectError):
        collect_snapshot(ReaderQueCai(), destino, now=RELOGIO, page_size=2)

    assert not destino.exists()


# --------------------------------------------------------------------------
# Envelope: a fronteira que nao deixa credencial passar
# --------------------------------------------------------------------------

def test_envelope_com_campo_extra_e_recusado():
    reader = FakeReader({"resumo_marca_mes": [{
        "status": 200, "rows": [linha_meta(1)], "content_range": "0-0/1",
        "apikey": "eyJhbGciOiJIUzI1NiJ9.SEGREDO",
    }]})

    with pytest.raises(SnapshotCollectError) as exc:
        collect_table(reader, SPEC_TARGETS, page_size=5)

    assert "fora do contrato" in str(exc.value)
    assert "apikey" in str(exc.value)          # o NOME do campo e' nomeado
    assert "SEGREDO" not in str(exc.value)     # o VALOR nunca aparece


def test_envelope_incompleto_e_recusado():
    reader = FakeReader({"resumo_marca_mes": [{"status": 200, "rows": []}]})

    with pytest.raises(SnapshotCollectError, match="envelope incompleto"):
        collect_table(reader, SPEC_TARGETS, page_size=5)


def test_envelope_tem_exatamente_tres_campos():
    assert sc.ENVELOPE_FIELDS == {"status", "rows", "content_range"}


# --------------------------------------------------------------------------
# Guard de somente-leitura
# --------------------------------------------------------------------------

class RotaFake:
    def __init__(self):
        self.abortada = False
        self.continuada = False

    def abort(self):
        self.abortada = True

    def continue_(self):
        self.continuada = True


class RequisicaoFake:
    def __init__(self, method, url):
        self.method = method
        self.url = url


@pytest.mark.parametrize("metodo", ["POST", "PUT", "PATCH", "DELETE",
                                    "post", "patch"])
def test_requisicao_mutavel_e_bloqueada(metodo):
    guard = ReadOnlyGuard()
    rota = RotaFake()
    guard.handle(rota, RequisicaoFake(metodo, "https://x.supabase.co/rest/v1/t?q=1"))

    assert rota.abortada is True
    assert rota.continuada is False
    assert guard.count == 1


@pytest.mark.parametrize("metodo", ["GET", "HEAD", "OPTIONS", "get"])
def test_requisicao_de_leitura_passa(metodo):
    guard = ReadOnlyGuard()
    rota = RotaFake()
    guard.handle(rota, RequisicaoFake(metodo, "https://x.supabase.co/rest/v1/t"))

    assert rota.continuada is True
    assert rota.abortada is False
    assert guard.count == 0


def test_guard_armado_nao_tem_excecao_por_destino():
    """Nem a rota de autenticacao passa depois que o guard esta armado."""
    guard = ReadOnlyGuard()
    guard.arm()
    for url in ("https://x.supabase.co/auth/v1/token?grant_type=password",
                "https://x.supabase.co/functions/v1/gerenciar-usuarios",
                "https://outro.dominio/qualquer"):
        rota = RotaFake()
        guard.handle(rota, RequisicaoFake("POST", url))
        assert rota.abortada is True
    assert guard.count == 3


def test_repetir_o_mesmo_passo_de_login_e_bloqueado():
    """A regra de "no maximo uma tentativa" e' do codigo, nao da disciplina."""
    guard = ReadOnlyGuard()
    url = "https://x.supabase.co/auth/v1/token?grant_type=password"

    primeira = RotaFake()
    guard.handle(primeira, RequisicaoFake("POST", url))
    segunda = RotaFake()
    guard.handle(segunda, RequisicaoFake("POST", url))

    assert primeira.continuada is True
    assert segunda.abortada is True
    assert guard.auth_attempts == 1
    assert guard.count == 1


def test_login_de_dois_passos_distintos_passa_uma_vez_cada():
    """O login real da fonte confere a credencial num lugar e troca por sessao
    noutro. Os dois passos sao UMA tentativa; repetir qualquer um e' a segunda.
    """
    guard = ReadOnlyGuard(auth_paths=("/auth/v1/", "/functions/v1/login-usuario"))
    passo1 = "https://x.supabase.co/functions/v1/login-usuario"
    passo2 = "https://x.supabase.co/auth/v1/token?grant_type=password"

    rotas = [RotaFake() for _ in range(3)]
    guard.handle(rotas[0], RequisicaoFake("POST", passo1))
    guard.handle(rotas[1], RequisicaoFake("POST", passo2))
    guard.handle(rotas[2], RequisicaoFake("POST", passo1))  # retentativa

    assert rotas[0].continuada is True
    assert rotas[1].continuada is True
    assert rotas[2].abortada is True
    assert guard.auth_attempts == 2
    assert guard.count == 1


def test_rota_de_auth_nao_declarada_e_bloqueada():
    """Sem declaracao explicita, uma Edge Function e' mutacao como outra qualquer."""
    guard = ReadOnlyGuard()  # so' a rota padrao
    rota = RotaFake()
    guard.handle(rota, RequisicaoFake(
        "POST", "https://x.supabase.co/functions/v1/login-usuario"))

    assert rota.abortada is True
    assert guard.auth_attempts == 0


def test_query_nao_confunde_a_deteccao_de_rota_de_auth():
    """`?next=/auth/v1/` na query nao transforma uma escrita em login."""
    guard = ReadOnlyGuard()
    rota = RotaFake()
    guard.handle(rota, RequisicaoFake(
        "POST", "https://x.supabase.co/rest/v1/metas?redirect=/auth/v1/token"))

    assert rota.abortada is True
    assert guard.auth_attempts == 0


def test_mutacao_que_nao_e_login_ja_e_bloqueada_antes_do_armamento():
    guard = ReadOnlyGuard()
    rota = RotaFake()
    guard.handle(rota, RequisicaoFake("PATCH", "https://x.supabase.co/rest/v1/metas"))

    assert rota.abortada is True
    assert guard.auth_attempts == 0


def test_registro_do_guard_guarda_caminho_e_nunca_query():
    guard = ReadOnlyGuard()
    guard.arm()
    guard.handle(RotaFake(), RequisicaoFake(
        "POST", "https://x.supabase.co/auth/v1/token?grant_type=password&senha=abc"))

    (metodo, host, caminho), = guard.blocked
    assert metodo == "POST"
    assert host == "x.supabase.co"
    assert caminho == "/auth/v1/token"
    registro = f"{metodo} {host}{caminho}"
    assert "senha" not in registro and "?" not in registro
    assert "grant_type" not in registro


def test_callback_de_bloqueio_recebe_o_mesmo_registro_sanitizado():
    """Sem aviso na hora, bloqueio por rota mal declarada parece senha errada."""
    avisos: list[tuple[str, str, str]] = []
    guard = ReadOnlyGuard(on_block=lambda m, h, c: avisos.append((m, h, c)))

    guard.handle(RotaFake(), RequisicaoFake(
        "POST", "https://x.supabase.co/functions/v1/login-usuario?token=abc"))

    assert avisos == [("POST", "x.supabase.co", "/functions/v1/login-usuario")]
    assert avisos == guard.blocked


def test_callback_nao_e_chamado_para_leitura_nem_para_login_permitido():
    avisos: list[tuple[str, str, str]] = []
    guard = ReadOnlyGuard(on_block=lambda *a: avisos.append(a))

    guard.handle(RotaFake(), RequisicaoFake("GET", "https://x.co/rest/v1/t"))
    guard.handle(RotaFake(), RequisicaoFake("POST", "https://x.co/auth/v1/token"))

    assert avisos == []


# --------------------------------------------------------------------------
# Determinismo
# --------------------------------------------------------------------------

def test_ordem_de_resposta_diferente_produz_arquivo_identico():
    linhas = [linha_meta(i, marca=m) for i, m in
              enumerate(["Kokeshi", "Apice", "Barbours", "Lescent"], start=1)]

    a = render_jsonl(list(linhas), SPEC_TARGETS)
    b = render_jsonl(list(reversed(linhas)), SPEC_TARGETS)
    c = render_jsonl([linhas[2], linhas[0], linhas[3], linhas[1]], SPEC_TARGETS)

    assert a == b == c


def test_ordem_das_chaves_dentro_da_linha_nao_muda_o_arquivo():
    base = linha_meta(1)
    invertida = dict(reversed(list(base.items())))

    assert render_jsonl([base], SPEC_TARGETS) == render_jsonl([invertida], SPEC_TARGETS)


def test_jsonl_termina_em_lf_e_nao_em_crlf():
    texto = render_jsonl([linha_meta(1), linha_meta(2)], SPEC_TARGETS)

    assert "\r" not in texto
    assert texto.endswith("\n")
    assert len(texto.splitlines()) == 2


def test_captura_repetida_produz_bytes_e_hashes_identicos(tmp_path):
    def capture(destino):
        return collect_snapshot(reader_minimo(), destino, now=RELOGIO)

    a = capture(tmp_path / "a")
    b = capture(tmp_path / "b")

    for nome in ("resumo_marca_mes.jsonl", "faturamento_diario_marca.jsonl",
                 "MANIFEST.json", "MANIFEST.sha256"):
        assert (a.out_dir / nome).read_bytes() == (b.out_dir / nome).read_bytes()
        assert a.files[nome]["sha256"] == b.files[nome]["sha256"]


def test_paginacao_diferente_produz_a_mesma_captura(tmp_path):
    """Mesmo conteudo, cortado em paginas diferentes: mesmos hashes."""
    metas = [linha_meta(i) for i in range(1, 6)]
    canais = [linha_canal(i, data=f"2026-06-{10 + i:02d}") for i in range(1, 6)]

    inteiro = FakeReader({
        "resumo_marca_mes": [env(metas, 5), env([], 5)],
        "faturamento_diario_marca": [env(canais, 5), env([], 5)],
    })
    picado = FakeReader({
        "resumo_marca_mes": [env(metas[:2], 5), env(metas[2:4], 5), env(metas[4:], 5)],
        "faturamento_diario_marca": [env(canais[:2], 5), env(canais[2:4], 5),
                                     env(canais[4:], 5)],
    })

    a = collect_snapshot(inteiro, tmp_path / "a", now=RELOGIO, page_size=5)
    b = collect_snapshot(picado, tmp_path / "b", now=RELOGIO, page_size=2)

    # O conteudo e' identico; `pages` nao, e nao deveria ser — ele descreve a
    # leitura, nao o dado. Por isso ele viaja no manifesto e muda o hash dele.
    for ta, tb in zip(a.manifest["tables"], b.manifest["tables"]):
        assert {k: v for k, v in ta.items() if k != "pages"} == \
               {k: v for k, v in tb.items() if k != "pages"}
    assert (a.out_dir / "resumo_marca_mes.jsonl").read_bytes() == \
           (b.out_dir / "resumo_marca_mes.jsonl").read_bytes()
    assert (a.out_dir / "faturamento_diario_marca.jsonl").read_bytes() == \
           (b.out_dir / "faturamento_diario_marca.jsonl").read_bytes()
    assert a.manifest["tables"][0]["pages"] == 2
    assert b.manifest["tables"][0]["pages"] == 3


# --------------------------------------------------------------------------
# Manifesto
# --------------------------------------------------------------------------

def test_manifesto_e_hashes_conferem_com_os_arquivos(tmp_path):
    res = collect_snapshot(reader_minimo(), tmp_path / "cap", now=RELOGIO)

    manifest = json.loads((res.out_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["source_system"] == "avoe_hub"
    assert manifest["status"] == "OK"
    assert manifest["captured_at"].endswith("+00:00")

    for t in manifest["tables"]:
        conteudo = (res.out_dir / t["file"]).read_bytes()
        assert sc.file_sha256(res.out_dir / t["file"]) == t["sha256"]
        assert t["row_count"] == len(
            [linha for linha in conteudo.decode("utf-8").splitlines() if linha])

    declarado = (res.out_dir / "MANIFEST.sha256").read_text(encoding="utf-8").split()[0]
    assert declarado == sc.file_sha256(res.out_dir / "MANIFEST.json")


def test_manifesto_recusa_relogio_sem_timezone(tmp_path):
    with pytest.raises(SnapshotCollectError, match="sem timezone"):
        collect_snapshot(reader_minimo(), tmp_path / "cap",
                         now=lambda: datetime(2026, 9, 23, 12, 0, 0))


def test_manifesto_registra_mutacoes_bloqueadas(tmp_path):
    res = collect_snapshot(reader_minimo(), tmp_path / "cap", now=RELOGIO,
                           blocked_mutations=4)

    assert res.manifest["collector"]["blocked_mutating_requests"] == 4
    assert res.manifest["collector"]["credentials_in_artifact"] == "none"


# --------------------------------------------------------------------------
# Destino
# --------------------------------------------------------------------------

def test_recusa_gravar_dentro_de_repositorio_git(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    destino = repo / "pipelines" / "captura"

    with pytest.raises(SnapshotCollectError, match="dentro de um repositorio git"):
        collect_snapshot(reader_minimo(), destino, now=RELOGIO)

    assert not destino.exists()


def test_recusa_mesmo_em_subdiretorio_profundo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    with pytest.raises(SnapshotCollectError, match="repositorio git"):
        sc.assert_fora_do_repositorio(repo / "a" / "b" / "c")


def test_aceita_destino_fora_de_repositorio(tmp_path):
    sc.assert_fora_do_repositorio(tmp_path / "solto")


# --------------------------------------------------------------------------
# Integracao com o contrato do importador (AVH-4A)
# --------------------------------------------------------------------------

def test_captura_sintetica_e_aceita_pelo_contrato(tmp_path):
    """A prova que interessa: o que o coletor grava, o importador le'."""
    metas = [
        linha_meta(1, marca="Apice"), linha_meta(2, marca="Kokeshi"),
        linha_meta(3, marca="Barbours", mes="2026-09-01"),
    ]
    canais = [
        linha_canal(1, data="2026-06-15", plataforma="SHEIN", marca="Kokeshi"),
        linha_canal(2, data="2026-06-16", plataforma="SHEIN", marca="Kokeshi"),
        linha_canal(3, data="2026-06-15", plataforma="MAGALU", marca="Barbours"),
        linha_canal(4, data="2026-06-15", plataforma="SHOPEE", marca="Apice"),
    ]
    reader = FakeReader({
        "resumo_marca_mes": [env(metas, 3)],
        "faturamento_diario_marca": [env(canais, 4)],
    })

    res = collect_snapshot(reader, tmp_path / "cap", now=RELOGIO)
    lido = read_snapshot(res.out_dir, run_id="teste-avh5b")

    assert len(lido.target_rows) == 3
    assert {r["channel"] for r in lido.channel_rows} == {"shein", "magalu"}
    assert lido.captured_at == RELOGIO()


def test_contrato_recusa_captura_adulterada(tmp_path):
    res = collect_snapshot(reader_minimo(), tmp_path / "cap", now=RELOGIO)
    alvo = res.out_dir / "resumo_marca_mes.jsonl"
    alvo.write_text(alvo.read_text(encoding="utf-8").replace("1000.00", "9999.00"),
                    encoding="utf-8", newline="")

    with pytest.raises(SnapshotContractError, match="hash divergente"):
        read_snapshot(res.out_dir, run_id="teste-avh5b")


# --------------------------------------------------------------------------
# Relatorio sanitizado
# --------------------------------------------------------------------------

def test_resumo_nao_expoe_linha_individual(tmp_path):
    reader = FakeReader({
        "resumo_marca_mes": [env([linha_meta(1, marca="Kokeshi")], 1)],
        "faturamento_diario_marca": [env([linha_canal(1)], 1)],
    })
    res = collect_snapshot(reader, tmp_path / "cap", now=RELOGIO)

    texto = "\n".join(res.resumo_sanitizado())

    assert "1000.00" not in texto      # nenhum valor de meta
    assert "123.45" not in texto       # nenhum valor de faturamento
    assert "Kokeshi" not in texto      # nenhuma linha
    assert str(tmp_path) not in texto  # nem o caminho completo do operador
    assert "captured_at" in texto and "linhas" in texto


# --------------------------------------------------------------------------
# Varredura do codigo executavel
# --------------------------------------------------------------------------

def codigo_executavel(modulo, excluir: tuple[str, ...] = ()) -> str:
    """Devolve o codigo SEM comentarios e SEM docstrings.

    A varredura tem de medir o que executa. Uma docstring que promete "nao ha
    senha aqui" contem a palavra `senha` e faria a varredura acusar a propria
    documentacao — e, pior, ensinaria a nao documentar.
    """
    arvore = ast.parse(Path(modulo.__file__).read_text(encoding="utf-8"))

    for no in ast.walk(arvore):
        corpo = getattr(no, "body", None)
        if not isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef,
                               ast.AsyncFunctionDef)) or not corpo:
            continue
        primeiro = corpo[0]
        if (isinstance(primeiro, ast.Expr)
                and isinstance(primeiro.value, ast.Constant)
                and isinstance(primeiro.value.value, str)):
            no.body = corpo[1:] or [ast.Pass()]

    if excluir:
        arvore.body = [
            no for no in arvore.body
            if not (isinstance(no, ast.Assign)
                    and any(isinstance(a, ast.Name) and a.id in excluir
                            for a in no.targets))
        ]
    return ast.unparse(arvore)


FONTE = Path(sc.__file__).read_text(encoding="utf-8")

# O bloco JS roda DENTRO do navegador e precisa nomear os cabecalhos que a
# propria aplicacao ja envia. Ele sai do corpo varrido como Python e e' medido
# a parte, por criterio proprio.
BLOCO_JS = sc._JS_READER
CODIGO = codigo_executavel(sc, excluir=("_JS_READER",))

import pipelines.avoe.snapshot_collect_session as scs  # noqa: E402

CODIGO_SESSAO = codigo_executavel(scs, excluir=("_JS_PRONTO", "_JS_SAIR"))


@pytest.mark.parametrize("termo", [
    "password", "senha", "passwd", "apikey", "api_key", "bearer", "token",
    "cookie", "storage_state", "storagestate", "signin", "sign_in",
])
def test_codigo_executavel_do_coletor_nao_nomeia_credencial(termo):
    assert termo not in CODIGO.lower(), (
        f"{termo!r} no codigo executavel do coletor: a credencial tem de "
        f"ficar inteira dentro do navegador."
    )


@pytest.mark.parametrize("termo", [
    "password", "senha", "passwd", "apikey", "api_key", "bearer",
    "cookie", "storage_state", "storagestate", "fill(", "type(",
])
def test_codigo_executavel_da_sessao_nao_toca_credencial(termo):
    assert termo not in CODIGO_SESSAO.lower(), (
        f"{termo!r} no driver de sessao: quem digita a credencial e' a pessoa."
    )


def test_nenhum_modulo_carrega_valor_de_credencial():
    """Nenhum JWT, nenhuma chave publicavel, nenhum host da Avoe embutido."""
    for fonte in (FONTE, Path(scs.__file__).read_text(encoding="utf-8")):
        assert not re.search(r"eyJ[A-Za-z0-9_\-]{10,}", fonte)
        assert not re.search(r"sb_[a-z]+_[A-Za-z0-9_\-]{10,}", fonte)
        assert not re.search(r"https?://[a-z0-9\-]+\.supabase\.co", fonte)
        assert "avoehub" not in fonte.lower()


def test_nenhum_modulo_traz_usuario_fixo():
    for fonte in (CODIGO, CODIGO_SESSAO):
        assert not re.search(r"usuario\s*=\s*[\"']", fonte)
        assert not re.search(r"user(name)?\s*=\s*[\"']", fonte, re.I)
        assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                             fonte)


def test_coletor_nao_le_ambiente_nem_arquivo_de_configuracao():
    for termo in ("os.environ", "getenv", "dotenv", ".env", "input(",
                  "getpass", "argparse"):
        assert termo not in CODIGO, (
            f"{termo!r} no coletor: credencial nao entra por ambiente, "
            f"arquivo, prompt nem argumento de linha de comando."
        )


def test_sessao_nao_le_ambiente_nem_arquivo_de_configuracao():
    """A sessao tem CLI, mas so' para URL e destino — nunca para credencial."""
    for termo in ("os.environ", "getenv", "dotenv", "getpass", "input("):
        assert termo not in CODIGO_SESSAO, f"{termo!r} no driver de sessao."

    nomes = re.findall(r"add_argument\(\s*[\"']--([a-z\-]+)", CODIGO_SESSAO)
    assert set(nomes) == {"url", "out-dir", "auth-path",
                          "login-timeout-seconds", "page-size"}


@pytest.mark.parametrize("fonte_nome", ["coletor", "sessao"])
def test_nenhum_modulo_fala_com_banco(fonte_nome):
    fonte = CODIGO if fonte_nome == "coletor" else CODIGO_SESSAO
    for termo in ("psycopg", "database_url", "datamart", "neon", "postgres",
                  "sqlalchemy", "marts.", "gold."):
        assert termo not in fonte.lower(), (
            f"{termo!r} no {fonte_nome}: ele nao fala com Neon nem com o "
            f"Data Mart."
        )


def test_coletor_nao_escreve_na_avoe():
    """O unico metodo que o JS usa e' GET, e nenhum verbo de escrita aparece."""
    assert "method: 'GET'" in BLOCO_JS
    for verbo in ("'POST'", "'PUT'", "'PATCH'", "'DELETE'",
                  "supaPost", "supaPatch", "supaDelete"):
        assert verbo not in BLOCO_JS, f"{verbo} no JS de leitura."
    assert "body:" not in BLOCO_JS


def test_js_da_sessao_nao_le_nem_preenche_campo_de_credencial():
    for js in (scs._JS_PRONTO, scs._JS_SAIR):
        for termo in ("inp-pass", "inp-user", "value", "password", "senha",
                      "access_token", "apikey"):
            assert termo not in js, f"{termo!r} no JS da sessao."


def test_js_da_sessao_devolve_apenas_palavras_fixas():
    """A sonda diz em que estado a sessao esta, e mais nada.

    Nenhum identificador, nenhum perfil, nenhum pedaco de sessao. O conjunto
    de retornos possiveis e' fechado e conferido contra as constantes do
    modulo, para que um retorno novo nao entre sem revisao.
    """
    permitidos = {f"'{scs.PRONTO}'", f"'{scs.SEM_SESSAO_DE_LEITURA}'",
                  f"'{scs.AGUARDANDO}'", "true", "false"}
    for js in (scs._JS_PRONTO, scs._JS_SAIR):
        retornos = set(re.findall(r"return\s+([A-Za-z'_]+)\s*;", js))
        assert retornos <= permitidos, (
            f"o JS da sessao devolveu {sorted(retornos - permitidos)}: so' as "
            f"palavras fixas do contrato podem atravessar a fronteira."
        )


def test_estados_da_sonda_sao_tres_e_distintos():
    assert len({scs.PRONTO, scs.SEM_SESSAO_DE_LEITURA, scs.AGUARDANDO}) == 3


class PaginaComEstado:
    def __init__(self, estados):
        self.estados = list(estados)

    def evaluate(self, *args, **kwargs):
        return self.estados.pop(0)


def test_espera_sessao_retorna_quando_pronto():
    scs.espera_sessao(PaginaComEstado([scs.AGUARDANDO, scs.PRONTO]),
                      timeout_s=30, intervalo_s=0)


def test_espera_sessao_para_quando_entrou_sem_sessao_de_leitura():
    """Entrar na tela nao e' poder ler. Parar aqui evita o snapshot vazio."""
    with pytest.raises(SnapshotCollectError, match="nao tem sessao que autorize"):
        scs.espera_sessao(PaginaComEstado([scs.SEM_SESSAO_DE_LEITURA]),
                          timeout_s=30, intervalo_s=0)


def test_espera_sessao_estoura_o_prazo_sem_segunda_tentativa():
    with pytest.raises(SnapshotCollectError, match="nao apareceu em"):
        scs.espera_sessao(PaginaComEstado([scs.AGUARDANDO] * 50),
                          timeout_s=0, intervalo_s=0)


def test_js_devolve_apenas_os_tres_campos_do_envelope():
    retornos = re.findall(r"return\s*\{([^}]*)\}", BLOCO_JS)
    assert retornos, "o JS precisa devolver um envelope"
    for corpo in retornos:
        campos = {c.split(":")[0].strip() for c in corpo.split(",") if c.strip()}
        assert campos == {"status", "rows", "content_range"}, (
            f"retorno do JS com campos {sorted(campos)}: so' os tres do "
            f"envelope podem atravessar a fronteira."
        )


def test_js_nao_le_cabecalho_de_resposta_alem_do_content_range():
    lidos = re.findall(r"headers\.get\(\s*'([^']+)'", BLOCO_JS)
    assert lidos == ["Content-Range"]


def test_nenhum_modulo_persiste_sessao():
    for fonte in (CODIGO, CODIGO_SESSAO):
        for termo in ("storage_state", "storageState", "save_storage",
                      "launch_persistent_context", "user_data_dir"):
            assert termo not in fonte, f"{termo!r}: sessao nao se guarda."


def test_guard_cobre_os_quatro_verbos_mutaveis():
    assert sc.MUTATING_METHODS == {"POST", "PUT", "PATCH", "DELETE"}
