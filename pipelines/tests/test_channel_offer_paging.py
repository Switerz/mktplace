"""Gate PMA-2C3C — insercao paginada e contagem acumulada do publisher.

O piloto do PMA-2C3B desfez uma transacao CORRETA porque a guarda lia
`cur.rowcount` depois de um `execute_values` que pagina POR DENTRO: 692 ofertas
viraram 6 paginas de 100 mais uma de 92, e a guarda comparou 92 com 692. Estes
testes travam o mecanismo novo — paginacao explicita, contagem somada e
verificada pagina a pagina.

Aqui `psycopg2.extras.execute_values` roda DE VERDADE. O cursor e' fiel (faz
`mogrify` com a adaptacao real do driver e recebe um `execute` por pagina), nao
um dublê permissivo: um stub que engolisse a chamada provaria apenas que a
funcao foi chamada, e foi exatamente esse tipo de cegueira que deixou o defeito
passar.
"""
from __future__ import annotations

import pytest

from pipelines import channel_offer_publisher as pub
from pipelines import channel_offer_sync as cos
from pipelines.tests.test_channel_offer_publisher import (
    APICE,
    BARBOURS,
    ConexaoFake,
    _plano,
    registro,
)

PAGINA = pub.INSERT_PAGE_SIZE


# ---------------------------------------------------------------------------
# Cursor fiel: `execute_values` real o dirige
# ---------------------------------------------------------------------------
class ConexaoFiel:
    """Conexao DBAPI minima: so' o que `execute_values` consulta."""

    encoding = "UTF8"

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):  # pragma: no cover - so' existe para flagrar uso indevido
        self.commits += 1

    def rollback(self):  # pragma: no cover - idem
        self.rollbacks += 1


class CursorFiel:
    """Cursor fiel o bastante para o `execute_values` REAL dirigi-lo.

    `execute_values` usa tres coisas do cursor: `connection.encoding`,
    `mogrify(template, args)` por LINHA e `execute(sql)` por PAGINA. As tres
    estao implementadas, e o `mogrify` adapta com `psycopg2.extensions.adapt`.

    O tamanho de cada pagina nao e' declarado: e' CONTADO pelos `mogrify` que
    chegaram antes do `execute`. Assim a medicao vem do comportamento real da
    funcao, nao de um numero que o teste escolheu.
    """

    def __init__(self, conexao=None, *, rowcount_de=None, falhar_na_pagina=None):
        self.connection = conexao or ConexaoFiel()
        self.paginas: list[int] = []
        self.rowcount = None
        self._linhas_na_pagina = 0
        self._rowcount_de = rowcount_de
        self._falhar_na_pagina = falhar_na_pagina

    def mogrify(self, template, args):
        import psycopg2.extensions as ext

        self._linhas_na_pagina += 1
        partes = [b"NULL" if a is None else ext.adapt(a).getquoted()
                  for a in args]
        return b"(" + b",".join(partes) + b")"

    def execute(self, sql, args=None):
        tamanho = self._linhas_na_pagina
        self._linhas_na_pagina = 0
        indice = len(self.paginas)
        self.paginas.append(tamanho)
        if self._falhar_na_pagina == indice:
            raise RuntimeError("falha injetada na pagina")
        self.rowcount = (self._rowcount_de(tamanho, indice)
                         if self._rowcount_de else tamanho)

    def close(self):
        pass


def _registros(quantidade: int, canal: str = "shopee") -> list[dict]:
    conta = "apice" if canal == "shopee" else "tiktok"
    return [registro(conta=conta, chave=f"k{i}", canal=canal)
            for i in range(quantidade)]


# ---------------------------------------------------------------------------
# Tamanhos: a soma vem do driver, nao do comprimento da lista
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("quantidade, paginas_esperadas", [
    (0, []),
    (1, [1]),
    (PAGINA - 1, [PAGINA - 1]),
    (PAGINA, [PAGINA]),
    (PAGINA + 1, [PAGINA, 1]),
    (692, [PAGINA, 192]),
    (1208, [PAGINA, PAGINA, 208]),
])
def test_paginacao_explicita_e_soma_por_pagina(quantidade, paginas_esperadas):
    cur = CursorFiel()
    total = pub.insert_offers_paged(cur, _registros(quantidade))
    assert cur.paginas == paginas_esperadas
    assert total == quantidade
    assert sum(cur.paginas) == quantidade


def test_registros_precisam_ser_sequencia_e_generator_falha_alto():
    """Um generator nao pode ser aceito em silencio.

    O helper depende de `len()` e de fatiamento, entao hoje um generator morre
    no `len()` antes de qualquer SQL. A trava existe para o futuro: se alguem
    reescrever o laco para consumir o iteravel preguicosamente, o generator
    seria esgotado pela primeira pagina e as seguintes viriam vazias — carga
    parcial em silencio, que e' exatamente a classe de defeito deste gate.
    """
    cur = CursorFiel()
    with pytest.raises(TypeError):
        pub.insert_offers_paged(cur, (r for r in _registros(600)))
    assert cur.paginas == []


@pytest.mark.parametrize("quantidade", [1, 499, 500, 501, 692, 1208])
def test_nenhuma_pagina_vazia_e_nenhum_page_size_zero(monkeypatch, quantidade):
    """Pagina vazia nunca acontece, e `page_size` nunca e' zero.

    Nao e' preciosismo: `_paginate` do psycopg2 com `page_size=0` faz
    `for i in range(0)` e nunca consome o iterador, entao rende pagina vazia
    PARA SEMPRE. O sintoma seria travamento, nao excecao — pior de diagnosticar
    que um erro. O fatiamento atual torna isso impossivel; este teste mantem
    assim.
    """
    vistos = []
    real = pub.execute_values

    def espia(cur, sql, argslist, *a, **kw):
        vistos.append((len(argslist), kw.get("page_size")))
        real(cur, sql, argslist, *a, **kw)

    monkeypatch.setattr(pub, "execute_values", espia)
    pub.insert_offers_paged(CursorFiel(), _registros(quantidade))
    assert vistos, "nenhuma pagina foi emitida"
    assert all(n > 0 for n, _ in vistos), vistos
    assert all(ps == n for n, ps in vistos), vistos


def test_a_lista_original_nao_e_mutada():
    """O fatiamento nao pode consumir nem reordenar o que o plano entregou."""
    registros = _registros(692)
    copia = list(registros)
    pub.insert_offers_paged(CursorFiel(), registros)
    assert registros == copia
    assert len(registros) == 692


def test_mesmo_sql_em_todas_as_paginas(monkeypatch):
    """SQL identico pagina a pagina: uma variacao mudaria colunas ou ordem."""
    sqls = []
    real = pub.execute_values

    def espia(cur, sql, argslist, *a, **kw):
        sqls.append(sql)
        real(cur, sql, argslist, *a, **kw)

    monkeypatch.setattr(pub, "execute_values", espia)
    pub.insert_offers_paged(CursorFiel(), _registros(1208))
    assert len(sqls) == 3
    assert len(set(sqls)) == 1
    assert sqls[0] is pub.SQL_INSERT_OFFERS


def test_lista_vazia_nao_emite_execute():
    cur = CursorFiel()
    assert pub.insert_offers_paged(cur, []) == 0
    assert cur.paginas == []
    assert cur.rowcount is None


def test_o_defeito_do_piloto_nao_volta():
    """692 linhas: o rowcount FINAL nao pode mais ser a contagem usada.

    Com o mecanismo antigo (`execute_values` paginando sozinho em 100 e a guarda
    lendo `cur.rowcount`), o valor observado seria 92. A soma e' 692.
    """
    cur = CursorFiel()
    total = pub.insert_offers_paged(cur, _registros(692))
    assert total == 692
    assert cur.rowcount == 192           # ultima pagina, e nao o total
    assert total != cur.rowcount         # justamente por isso nao se le' o final
    assert 692 % 100 == 92               # o numero do piloto era resto de pagina


def test_uma_chamada_por_pagina_e_um_execute_por_chamada(monkeypatch):
    """Cada iteracao chama `execute_values` uma vez, e ela emite UM execute."""
    chamadas = []
    real = pub.execute_values

    def espia(cur, sql, argslist, *a, **kw):
        antes = len(cur.paginas)
        real(cur, sql, argslist, *a, **kw)
        chamadas.append((len(argslist), kw.get("page_size"),
                         len(cur.paginas) - antes))

    monkeypatch.setattr(pub, "execute_values", espia)
    cur = CursorFiel()
    pub.insert_offers_paged(cur, _registros(1208))
    assert [c[0] for c in chamadas] == [PAGINA, PAGINA, 208]
    assert [c[1] for c in chamadas] == [PAGINA, PAGINA, 208]
    assert [c[2] for c in chamadas] == [1, 1, 1]


# ---------------------------------------------------------------------------
# Rowcount que o driver nao confirma NUNCA vira sucesso
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("valor, rotulo", [
    (None, "None"),
    (-1, "-1"),
])
def test_rowcount_desconhecido_falha(valor, rotulo):
    cur = CursorFiel(rowcount_de=lambda tamanho, indice: valor)
    with pytest.raises(pub.PublisherError) as erro:
        pub.insert_offers_paged(cur, _registros(10))
    assert rotulo in str(erro.value)
    assert "nao pode ser confirmada" in str(erro.value)


def test_rowcount_menor_que_a_pagina_falha():
    cur = CursorFiel(rowcount_de=lambda tamanho, indice: tamanho - 1)
    with pytest.raises(pub.PublisherError):
        pub.insert_offers_paged(cur, _registros(10))


def test_rowcount_maior_que_a_pagina_falha():
    cur = CursorFiel(rowcount_de=lambda tamanho, indice: tamanho + 1)
    with pytest.raises(pub.PublisherError):
        pub.insert_offers_paged(cur, _registros(10))


def test_rowcount_errado_so_na_ultima_pagina_falha():
    """A conferencia e' POR PAGINA: a ultima nao escapa por as anteriores terem
    batido."""
    cur = CursorFiel(
        rowcount_de=lambda tamanho, indice: 0 if indice == 2 else tamanho)
    with pytest.raises(pub.PublisherError):
        pub.insert_offers_paged(cur, _registros(1208))
    assert len(cur.paginas) == 3      # parou NA pagina defeituosa


# ---------------------------------------------------------------------------
# Falha em qualquer pagina sobe e derruba a transacao inteira
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("pagina_ruim, paginas_ate_a_falha", [
    (0, 1),
    (1, 2),
    (2, 3),
])
def test_falha_em_qualquer_pagina_propaga(pagina_ruim, paginas_ate_a_falha):
    cur = CursorFiel(falhar_na_pagina=pagina_ruim)
    with pytest.raises(RuntimeError):
        pub.insert_offers_paged(cur, _registros(1208))
    assert len(cur.paginas) == paginas_ate_a_falha
    assert cur.connection.commits == 0
    assert cur.connection.rollbacks == 0


def test_helper_nunca_comita_nem_desfaz():
    cur = CursorFiel()
    pub.insert_offers_paged(cur, _registros(1208))
    assert cur.connection.commits == 0
    assert cur.connection.rollbacks == 0


def test_todas_as_paginas_no_mesmo_cursor_e_na_mesma_conexao():
    """Sem segunda conexao e sem cursor novo: as paginas pertencem a UMA
    transacao. Se cada pagina abrisse a sua, a falha da terceira deixaria as
    duas primeiras gravadas."""
    cur = CursorFiel()
    conexao = cur.connection
    pub.insert_offers_paged(cur, _registros(1208))
    assert len(cur.paginas) == 3
    assert cur.connection is conexao


# ---------------------------------------------------------------------------
# Os dois canais usam o MESMO helper
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("canal, quantidade, paginas_esperadas", [
    ("shopee", 692, [PAGINA, 192]),
    ("tiktok", 1208, [PAGINA, PAGINA, 208]),
])
def test_shopee_e_tiktok_pelo_mesmo_caminho(canal, quantidade,
                                            paginas_esperadas):
    cur = CursorFiel()
    total = pub.insert_offers_paged(cur, _registros(quantidade, canal))
    assert cur.paginas == paginas_esperadas
    assert total == quantidade


# ---------------------------------------------------------------------------
# `execute_plan`: a ordem e as DUAS defesas
# ---------------------------------------------------------------------------
def _executa_plano(monkeypatch, registros, scopes=(APICE,), rowcount_de=None):
    """Roda `execute_plan` com um `execute_values` que alimenta a reconciliacao.

    O fake respeita a paginacao: registra por escopo o que cada pagina recebeu e
    devolve o `rowcount` daquela pagina, como o driver faz.
    """
    destino = ConexaoFake()

    def paginado(cur, sql, argslist, *a, **kw):
        for tupla in argslist:
            d = dict(zip(cos.RECORD_COLUMNS, tupla))
            chave = (d["marketplace"], d["observed_date"], d["shop_account"])
            destino.inseridos_por_escopo[chave] = (
                destino.inseridos_por_escopo.get(chave, 0) + 1)
        cur.rowcount = (rowcount_de(len(argslist)) if rowcount_de
                        else len(argslist))

    monkeypatch.setattr(pub, "execute_values", paginado)
    return destino, pub.execute_plan(destino, _plano(registros, scopes))


def test_execute_plan_publica_692_sem_falso_alarme(monkeypatch):
    registros = [registro(conta="apice", chave=f"k{i}") for i in range(692)]
    destino, (inseridas, por_escopo) = _executa_plano(monkeypatch, registros)
    assert inseridas == 692
    assert sum(por_escopo.values()) == 692
    assert destino.commits == 0          # quem comita e' `run_publication`
    assert destino.rollbacks == 0


def test_execute_plan_publica_1208_do_tiktok(monkeypatch):
    registros = [registro(conta="tiktok", chave=f"k{i}", canal="tiktok")
                 for i in range(1208)]
    escopo = cos.PublicationScope("tiktok", registros[0]["observed_date"],
                                  "tiktok")
    _, (inseridas, por_escopo) = _executa_plano(
        monkeypatch, registros, scopes=(escopo,))
    assert inseridas == 1208
    assert sum(por_escopo.values()) == 1208


def test_pagina_que_nao_confirma_derruba_antes_do_commit(monkeypatch):
    """Driver confirma MENOS do que a pagina recebeu: para na hora.

    A conferencia POR PAGINA e' a mais estreita das duas e dispara primeiro —
    inclusive quando ha uma unica pagina, que e' o caso aqui.
    """
    destino = ConexaoFake()

    def confirma_de_menos(cur, sql, argslist, *a, **kw):
        for tupla in argslist:
            d = dict(zip(cos.RECORD_COLUMNS, tupla))
            chave = (d["marketplace"], d["observed_date"], d["shop_account"])
            destino.inseridos_por_escopo[chave] = (
                destino.inseridos_por_escopo.get(chave, 0) + 1)
        cur.rowcount = len(argslist) - 1      # uma a menos, sem levantar

    monkeypatch.setattr(pub, "execute_values", confirma_de_menos)
    with pytest.raises(pub.PublisherError) as erro:
        pub.execute_plan(destino, _plano([registro()]))
    assert "nao pode ser confirmada" in str(erro.value)
    assert destino.commits == 0


def test_guarda_do_total_pega_helper_que_devolve_numero_errado(monkeypatch):
    """A guarda do total defende contra o PROPRIO helper.

    Com a conferencia por pagina intacta, a soma sempre bate — a guarda do total
    so' e' alcancavel se a insercao paginada mentir sobre quanto inseriu. E' esse
    o papel dela: uma checagem de fora, que nao depende do que o helper faz por
    dentro. Aqui a mentira e' injetada de proposito.
    """
    destino = ConexaoFake()
    monkeypatch.setattr(pub, "insert_offers_paged",
                        lambda cur, registros: len(registros) - 1)
    with pytest.raises(pub.PublisherError) as erro:
        pub.execute_plan(destino, _plano([registro(), registro(chave="901")]))
    assert "confirmou 1 linhas e o plano tinha 2" in str(erro.value)
    assert destino.commits == 0
    assert destino.rollbacks == 0        # quem desfaz e' `run_publication`


def test_reconciliacao_por_escopo_continua_sendo_defesa_propria(monkeypatch):
    """Driver confirma o total CERTO, mas a linha foi parar noutro escopo.

    A primeira defesa passa (a soma bate); so' a contagem por escopo pega.
    """
    destino = ConexaoFake()

    def grava_no_escopo_errado(cur, sql, argslist, *a, **kw):
        for tupla in argslist:
            d = dict(zip(cos.RECORD_COLUMNS, tupla))
            chave = (d["marketplace"], d["observed_date"], "outra_conta")
            destino.inseridos_por_escopo[chave] = (
                destino.inseridos_por_escopo.get(chave, 0) + 1)
        cur.rowcount = len(argslist)

    monkeypatch.setattr(pub, "execute_values", grava_no_escopo_errado)
    with pytest.raises(pub.PublisherError) as erro:
        pub.execute_plan(destino, _plano([registro()]))
    assert "reconciliacao pre-commit divergiu" in str(erro.value)
    assert destino.commits == 0


def test_uma_conta_nao_apaga_a_outra(monkeypatch):
    """Duas contas no mesmo plano: cada escopo recebe o seu, e o DELETE de uma
    nao leva a outra junto."""
    registros = ([registro(conta="apice", chave=f"a{i}") for i in range(600)]
                 + [registro(conta="barbours", chave=f"b{i}") for i in range(92)])
    destino, (inseridas, por_escopo) = _executa_plano(
        monkeypatch, registros, scopes=(APICE, BARBOURS))
    assert inseridas == 692
    assert por_escopo[APICE] == 600
    assert por_escopo[BARBOURS] == 92
    apagados = [c for c in destino.comandos if "DELETE" in c[0].upper()]
    assert len(apagados) == 2


def test_escopo_saudavel_vazio_continua_com_zero(monkeypatch):
    """DELETE roda, nenhum INSERT o segue, `rows_loaded` e' zero. Sem sentinela."""
    destino, (inseridas, por_escopo) = _executa_plano(monkeypatch, [])
    assert inseridas == 0
    assert por_escopo == {APICE: 0}
    apagados = [c for c in destino.comandos if "DELETE" in c[0].upper()]
    assert len(apagados) == 1
