"""Gate PMA-2C4D1-H1 — a barreira do `--apply` e a data da CLI.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
No PMA-2C4D1 a publicacao autorizada foi recusada por dois defeitos:

1. `assert_apply_authorized` exigia que "017" fosse UMA DAS LINHAS de
   `alembic_version`. Essa tabela guarda so' o head corrente. Assim que a 018 e
   a 019 entraram, o head virou "019" e a 017 — aplicada, jamais revertida —
   sumiu da tabela. A barreira passou a recusar para sempre;
2. `--observed-date` chegava como TEXTO do argparse e `tiktok_snapshot_exists`
   o comparava com o `datetime.date` do driver. `date(2026, 9, 17) ==
   "2026-09-17"` e' False, entao uma data existente era recusada.

Os testes abaixo prendem os dois comportamentos e, no fim do arquivo, proibem
estruturalmente a volta das formas defeituosas.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pytest

from pipelines import channel_offer_publisher as pub
from pipelines import channel_offer_sync as cos

FONTE_SYNC = Path(cos.__file__)
FONTE_PUB = Path(pub.__file__)


# ---------------------------------------------------------------------------
# Fakes — despacham pelo SQL, nunca pela ordem da chamada
# ---------------------------------------------------------------------------
class _Cursor:
    def __init__(self, conexao):
        self._c = conexao
        self._atual = None

    def execute(self, sql, params=None):
        texto = " ".join(sql.split())
        self._c.queries.append(texto)
        if "to_regclass('alembic_version')" in texto:
            self._atual = [(("alembic_version" if self._c.tem_controle else None),)]
        elif "FROM alembic_version" in texto:
            if not self._c.tem_controle:
                raise AssertionError("consultou alembic_version sem checar se existe")
            self._atual = [(r,) for r in self._c.revisoes]
        elif "to_regclass(%s)" in texto:
            self._atual = [(self._c.relacao,)]
        else:  # pragma: no cover
            raise AssertionError(f"query nao prevista: {texto[:60]}")

    def fetchall(self):
        return self._atual

    def fetchone(self):
        return self._atual[0] if self._atual else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conexao:
    def __init__(self, revisoes=("019",), relacao=cos.TARGET_TABLE, tem_controle=True):
        self.revisoes = list(revisoes)
        self.relacao = relacao
        self.tem_controle = tem_controle
        self.queries: list[str] = []
        self.escreveu = False

    def cursor(self, *a, **k):
        return _Cursor(self)


# ---------------------------------------------------------------------------
# Grafo real das migrations versionadas
# ---------------------------------------------------------------------------

def test_o_grafo_real_carrega_e_e_uma_cadeia_unica():
    g = cos.load_migration_graph()
    assert len(g) >= 19, "o repositorio tem pelo menos 19 revisoes"
    # Exatamente uma raiz (down_revision None) e nenhum id repetido.
    raizes = [r for r, d in g.items() if d is None]
    assert len(raizes) == 1, f"esperava uma raiz, achei {raizes}"
    assert cos.REQUIRED_MIGRATION in g, "a 017 precisa existir no repositorio"


def test_017_e_ancestral_de_018_e_de_019_no_grafo_real():
    g = cos.load_migration_graph()
    for head in ("017", "018", "019"):
        assert cos.migration_is_ancestor("017", head, g) is True, head


def test_017_nao_e_ancestral_de_revisao_anterior():
    g = cos.load_migration_graph()
    for head in ("016", "015", "001"):
        assert cos.migration_is_ancestor("017", head, g) is False, head


# ---------------------------------------------------------------------------
# A barreira, ponta a ponta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("head", ["017", "018", "019"])
def test_autoriza_com_a_017_aplicada_seja_ela_head_ou_ancestral(head):
    conn = Conexao(revisoes=[head])
    cos.assert_apply_authorized(conn)  # nao levanta
    assert conn.escreveu is False


@pytest.mark.parametrize("head", ["016", "015", "001"])
def test_recusa_head_anterior_a_017(head):
    conn = Conexao(revisoes=[head])
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)
    assert conn.escreveu is False


def test_recusa_revisao_desconhecida():
    conn = Conexao(revisoes=["nao-existe-no-repo"])
    with pytest.raises(cos.MigrationGraphError):
        cos.assert_apply_authorized(conn)


def test_recusa_alembic_version_vazia():
    conn = Conexao(revisoes=[])
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)


def test_recusa_banco_sem_alembic_version():
    conn = Conexao(revisoes=[], tem_controle=False)
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)
    # E nao pode ter tentado consultar a tabela que nao existe: isso abortaria
    # a transacao do chamador.
    assert not any("FROM alembic_version" in q for q in conn.queries)


def test_recusa_multiplos_heads():
    conn = Conexao(revisoes=["019", "018"])
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)


def test_recusa_tabela_fisica_ausente_mesmo_com_a_017_aplicada():
    conn = Conexao(revisoes=["019"], relacao=None)
    with pytest.raises(cos.ApplyNotAuthorizedError):
        cos.assert_apply_authorized(conn)


def test_recusa_quando_o_grafo_nao_pode_ser_lido(tmp_path):
    vazio = tmp_path / "sem_revisoes"
    vazio.mkdir()
    with pytest.raises(cos.MigrationGraphError):
        cos.assert_apply_authorized(Conexao(), versions_dir=vazio)
    with pytest.raises(cos.MigrationGraphError):
        cos.assert_apply_authorized(Conexao(), versions_dir=tmp_path / "nao_existe")


def test_o_carregador_recusa_na_ORIGEM_e_nao_devolve_grafo_de_consolacao(tmp_path):
    """Exige a recusa em `load_migration_graph`, nao so' la' na frente.

    Exercicio de mutacao: trocar o `raise` do diretorio ausente por
    `return {"017": None}` fazia o teste acima continuar verde — a barreira
    quebrava depois, em "head nao pertence a cadeia", e o veredito final era o
    mesmo pelo motivo errado. Um fallback permissivo no carregador precisa
    falhar AQUI.
    """
    vazio = tmp_path / "sem_revisoes"
    vazio.mkdir()
    with pytest.raises(cos.MigrationGraphError):
        cos.load_migration_graph(vazio)
    with pytest.raises(cos.MigrationGraphError):
        cos.load_migration_graph(tmp_path / "nao_existe")
    # E nao pode devolver dicionario nenhum nesses casos.
    for alvo in (vazio, tmp_path / "nao_existe"):
        try:
            resultado = cos.load_migration_graph(alvo)
        except cos.MigrationGraphError:
            continue
        pytest.fail(f"devolveu {resultado!r} em vez de recusar")


def test_recusa_down_revision_nao_linear(tmp_path):
    """Ponto de merge (tupla) torna a ancestralidade ambigua — recusa."""
    d = tmp_path / "versions"
    d.mkdir()
    (d / "a.py").write_text('revision = "a"\ndown_revision = None\n', encoding="utf-8")
    (d / "b.py").write_text('revision = "b"\ndown_revision = ("a", "z")\n', encoding="utf-8")
    with pytest.raises(cos.MigrationGraphError):
        cos.load_migration_graph(d)


def test_recusa_revisao_duplicada(tmp_path):
    d = tmp_path / "versions"
    d.mkdir()
    (d / "a.py").write_text('revision = "x"\ndown_revision = None\n', encoding="utf-8")
    (d / "b.py").write_text('revision = "x"\ndown_revision = "x"\n', encoding="utf-8")
    with pytest.raises(cos.MigrationGraphError):
        cos.load_migration_graph(d)


def test_recusa_arquivo_de_revisao_ilegivel(tmp_path):
    d = tmp_path / "versions"
    d.mkdir()
    (d / "quebrado.py").write_text("revision = (((", encoding="utf-8")
    with pytest.raises(cos.MigrationGraphError):
        cos.load_migration_graph(d)


def test_detecta_ciclo_em_vez_de_girar_para_sempre():
    with pytest.raises(cos.MigrationGraphError):
        cos.migration_is_ancestor("017", "a", {"a": "b", "b": "a"})


def test_mensagens_da_barreira_nao_vazam_dsn_host_usuario_nem_sql():
    proibidos = ("postgres://", "postgresql://", "neon.tech", "amazonaws.com",
                 "password", "SELECT ", "to_regclass", "@", "alembic_version")
    mensagens = []
    for conn in (Conexao(revisoes=[]), Conexao(revisoes=["016"]),
                 Conexao(revisoes=["019"], relacao=None),
                 Conexao(revisoes=["019", "018"]),
                 Conexao(revisoes=[], tem_controle=False)):
        with pytest.raises(cos.ChannelSyncError) as exc:
            cos.assert_apply_authorized(conn)
        mensagens.append(str(exc.value))
    for m in mensagens:
        for p in proibidos:
            assert p not in m, f"mensagem vazou {p!r}: {m}"


# ---------------------------------------------------------------------------
# `--observed-date`
# ---------------------------------------------------------------------------

def test_argparse_entrega_datetime_date_e_nao_texto():
    args = pub.build_cli().parse_args(
        ["--marketplace", "tiktok", "--observed-date", "2026-09-17"])
    assert isinstance(args.observed_date, date)
    assert not isinstance(args.observed_date, str)
    assert args.observed_date == date(2026, 9, 17)


def test_a_data_normalizada_compara_igual_ao_date_do_driver():
    """O defeito de 2C4D1 em uma linha: a comparacao precisa dar True."""
    class ConexaoDatas:
        def cursor(self, *a, **k):
            return self

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [{"snapshot_date": date(2026, 9, 17)}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    normalizada = pub.data_observada("2026-09-17")
    assert cos.tiktok_snapshot_exists(ConexaoDatas(), normalizada) is True
    # E a forma antiga, em texto, continuaria falhando — por isso a correcao.
    assert cos.tiktok_snapshot_exists(ConexaoDatas(), "2026-09-17") is False


def test_data_inexistente_continua_recusada():
    class ConexaoDatas:
        def cursor(self, *a, **k):
            return self

        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return [{"snapshot_date": date(2026, 9, 16)}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    assert cos.tiktok_snapshot_exists(ConexaoDatas(),
                                      pub.data_observada("2026-09-17")) is False


@pytest.mark.parametrize("ruim", ["20260917", "2026-09-17T00:00", "17/09/2026",
                                  "2026-13-01", "2026-09-17 00:00:00", "", "hoje"])
def test_formato_invalido_recusado_na_fronteira_da_cli(ruim):
    """Recusa no `parse_args`: antes de conexao, lock, auditoria ou escrita."""
    with pytest.raises(SystemExit):
        pub.build_cli().parse_args(
            ["--marketplace", "tiktok", "--observed-date", ruim])
    with pytest.raises(argparse.ArgumentTypeError):
        pub.data_observada(ruim)


def test_erro_de_data_nao_ecoa_o_valor_digitado():
    with pytest.raises(argparse.ArgumentTypeError) as exc:
        pub.data_observada("/etc/passwd-e-um-valor-bem-comprido")
    assert "passwd" not in str(exc.value)
    assert str(exc.value) == pub.MSG_DATA_INVALIDA


def test_sem_a_flag_o_comportamento_canonico_e_preservado():
    args = pub.build_cli().parse_args(["--marketplace", "tiktok"])
    assert args.observed_date is None


def test_data_ja_normalizada_passa_intacta():
    """Idempotencia: chamar duas vezes nao quebra nem re-parseia."""
    d = date(2026, 9, 17)
    assert pub.data_observada(d) is d


def test_shopee_ignora_observed_date_como_antes():
    """A Shopee nao ganhou filtro nem regra nova: o parametro segue inerte.

    `collect_snapshot` retorna no ramo da Shopee ANTES de olhar a data — o
    contrato dela e' `snapshot_current`, sem serie por dia.
    """
    fonte = FONTE_SYNC.read_text(encoding="utf-8")
    pubsrc = FONTE_PUB.read_text(encoding="utf-8")
    ramo = pubsrc.split('if marketplace == "shopee":')[1].split("return registros")[0]
    assert "observed_date" not in ramo, "a Shopee passou a olhar observed_date"
    assert "observed_date" not in fonte.split("SQL_SHOPEE_SIMPLE_PARENTS")[1].split('"""')[1]


# ---------------------------------------------------------------------------
# Anti-regressao ESTRUTURAL — as formas defeituosas nao podem voltar
# ---------------------------------------------------------------------------

def test_a_barreira_nao_volta_a_exigir_pertencimento_a_alembic_version():
    src = FONTE_SYNC.read_text(encoding="utf-8")
    trecho = src.split("def assert_apply_authorized")[1].split("\ndef ")[0]
    assert "REQUIRED_MIGRATION not in" not in trecho
    assert "REQUIRED_MIGRATION in " not in trecho
    assert "not in carimbadas" not in trecho


def test_a_barreira_nao_volta_a_comparar_igualdade_com_o_head():
    src = FONTE_SYNC.read_text(encoding="utf-8")
    trecho = src.split("def assert_apply_authorized")[1].split("\ndef ")[0]
    for forma in ("REQUIRED_MIGRATION ==", "== REQUIRED_MIGRATION",
                  "REQUIRED_MIGRATION !=", "!= REQUIRED_MIGRATION"):
        assert forma not in trecho, forma


def test_a_decisao_nao_usa_comparacao_lexical_nem_numerica_de_revisao():
    src = FONTE_SYNC.read_text(encoding="utf-8")
    alvo = ("def load_migration_graph" + src.split("def load_migration_graph")[1]
            .split("def assert_apply_authorized")[0]
            + src.split("def migration_is_ancestor")[1].split("\ndef ")[0])
    for forma in ("int(", "float(", " >= ", " <= ", " > ", " < ", ".zfill(", "sorted(g"):
        assert forma not in alvo, f"comparacao de ordem em revisao: {forma}"


def test_a_decisao_vem_do_grafo_de_down_revision():
    src = FONTE_SYNC.read_text(encoding="utf-8")
    assert "down_revision" in src
    assert "migration_is_ancestor" in src.split("def assert_apply_authorized")[1]


def test_o_caminho_das_migrations_e_constante_e_nao_vem_de_cli_ou_ambiente():
    src = FONTE_SYNC.read_text(encoding="utf-8")
    bloco = src.split("MIGRATIONS_DIR = ")[1].split("\n\n")[0]
    for proibido in ("environ", "getenv", "argv", "input(", "sys.argv"):
        assert proibido not in bloco, proibido


def test_a_tabela_fisica_continua_sendo_checada_por_identificador_constante():
    src = FONTE_SYNC.read_text(encoding="utf-8")
    trecho = src.split("def assert_apply_authorized")[1].split("\ndef ")[0]
    assert "to_regclass(%s)" in trecho, "a checagem precisa ser parametrizada"
    assert "TARGET_TABLE" in trecho
    assert 'f"SELECT' not in trecho and ".format(" not in trecho


def test_a_data_da_cli_e_normalizada_com_type_no_argparse():
    src = FONTE_PUB.read_text(encoding="utf-8")
    trecho = src.split('"--observed-date"')[1].split(")")[0]
    assert "type=data_observada" in trecho, "a normalizacao precisa estar no parser"


def test_a_data_nao_volta_a_ser_reconvertida_para_texto():
    src = FONTE_PUB.read_text(encoding="utf-8")
    corpo = src.split("def collect_snapshot")[1].split("\ndef ")[0]
    for forma in ("str(observed_date", "observed_date.isoformat(",
                  "f\"{observed_date"):
        assert forma not in corpo, forma


# ---------------------------------------------------------------------------
# Forma das declaracoes: `Assign` E `AnnAssign`
#
# As 19 revisoes de hoje usam `revision = "017"`. O template do Alembic 1.18,
# que gera a PROXIMA, usa `revision: str = "020"`. Um parser que so' lesse
# `Assign` perderia a revisao nova, o head deixaria de existir no grafo e a
# barreira recusaria — exatamente o apagao que este modulo corrige.
# ---------------------------------------------------------------------------

def _escreve(d: Path, nome: str, corpo: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / nome).write_text(corpo, encoding="utf-8")


def test_le_revisao_no_formato_anotado_do_template_atual(tmp_path):
    d = tmp_path / "versions"
    _escreve(d, "a.py", 'revision: str = "a"\ndown_revision: str | None = None\n')
    _escreve(d, "b.py", 'revision: str = "b"\ndown_revision: str | None = "a"\n')
    g = cos.load_migration_graph(d)
    assert g == {"a": None, "b": "a"}
    assert cos.migration_is_ancestor("a", "b", g) is True


def test_cadeia_mista_assign_e_annassign(tmp_path):
    """O caso real do proximo deploy: 19 antigas + uma nova pelo template."""
    d = tmp_path / "versions"
    _escreve(d, "017.py", 'revision = "017"\ndown_revision = "016"\n')
    _escreve(d, "016.py", 'revision = "016"\ndown_revision = None\n')
    _escreve(d, "020.py",
             'from typing import Sequence, Union\n'
             'revision: str = "020"\n'
             'down_revision: Union[str, Sequence[str], None] = "017"\n')
    g = cos.load_migration_graph(d)
    assert cos.migration_is_ancestor("017", "020", g) is True
    assert cos.migration_is_ancestor("017", "016", g) is False


def test_anotacao_sem_valor_e_recusada(tmp_path):
    d = tmp_path / "versions"
    _escreve(d, "a.py", "revision: str\ndown_revision: str | None = None\n")
    with pytest.raises(cos.MigrationGraphError):
        cos.load_migration_graph(d)


def test_o_parser_acompanha_o_template_do_alembic_instalado():
    """Ancora no template REAL: se ele mudar de forma, este teste avisa.

    Gera um par de revisoes com a mesma sintaxe que `alembic revision`
    produziria hoje e exige que o grafo as enxergue.
    """
    import re
    import sys

    # O pacote `alembic` pode estar sombreado por `apps/api/alembic` conforme o
    # sys.path; localizar o template pelo alembic REAL, no disco.
    spec = None
    for caminho in sys.path:
        cand = Path(caminho) / "alembic" / "templates" / "generic" / "script.py.mako"
        if cand.exists():
            spec = cand
            break
    if spec is None:
        import importlib.metadata as md
        for f in md.files("alembic") or []:
            if f.name == "script.py.mako" and "generic" in str(f):
                spec = Path(md.distribution("alembic").locate_file(f))
                break
    if spec is None or not spec.exists():
        pytest.skip("template do alembic nao localizavel neste ambiente")

    texto = spec.read_text(encoding="utf-8")
    linha_rev = next(l for l in texto.splitlines() if l.startswith("revision"))
    linha_down = next(l for l in texto.splitlines() if l.startswith("down_revision"))
    # Substitui a interpolacao mako por literais.
    corpo = (re.sub(r"\$\{[^}]+\}", '"NOVA"', linha_rev) + "\n"
             + re.sub(r"\$\{[^}]+\}", '"RAIZ"', linha_down) + "\n")
    raiz = 'revision = "RAIZ"\ndown_revision = None\n'

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "versions"
        _escreve(d, "raiz.py", raiz)
        _escreve(d, "nova.py", "from typing import Sequence, Union\n" + corpo)
        g = cos.load_migration_graph(d)
        assert "NOVA" in g, (
            "o template do Alembic instalado gera uma forma que o parser nao "
            f"enxerga: {linha_rev!r}"
        )
        assert cos.migration_is_ancestor("RAIZ", "NOVA", g) is True


# ---------------------------------------------------------------------------
# O caminho das migrations nao depende do diretorio de trabalho
# ---------------------------------------------------------------------------

def test_o_grafo_e_encontrado_de_qualquer_cwd(tmp_path, monkeypatch):
    """Ancorado em `__file__`, nao no CWD: o operador roda de onde quiser."""
    esperado = cos.load_migration_graph()
    monkeypatch.chdir(tmp_path)
    assert cos.load_migration_graph() == esperado
    monkeypatch.chdir(Path(cos.__file__).resolve().parents[2])
    assert cos.load_migration_graph() == esperado


def test_migrations_dir_aponta_para_dentro_do_repositorio():
    esperado = Path(cos.__file__).resolve().parents[1] / "apps" / "api" / "alembic" / "versions"
    assert cos.MIGRATIONS_DIR == esperado
    assert cos.MIGRATIONS_DIR.is_dir(), "o diretorio precisa existir no checkout"
