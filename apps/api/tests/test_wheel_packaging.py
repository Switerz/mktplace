"""A wheel de `mktplace-api` nao pode publicar pacote de topo alheio.

INCIDENTE QUE ESTE ARQUIVO EXISTE PARA IMPEDIR
-----------------------------------------------
Em 2026-09-25 a imagem do Airflow passou a instalar `mktplace-api` para obter
`app.services.pma_kit_bom`. A distribuicao declarava `packages.find` sem
`include`, entao o `top_level.txt` publicava CINCO pacotes: `alembic`, `app`,
`etl`, `tests` e `tools`.

`alembic/` em `apps/api` e' a pasta de MIGRATIONS, com `__init__.py` de 0
bytes. O pip gravou esse arquivo POR CIMA do `alembic/__init__.py` do pacote
real — `--no-deps` protege contra resolver dependencia, nao contra colisao de
arquivo entre distribuicoes. De 25/09 22:14 a 28/09 todo servico do staging
morreu no boot com:

    ImportError: cannot import name '__version__' from 'alembic'

E o build passou VERDE o tempo todo, porque nada no build importava `alembic`.

POR QUE ESTE TESTE LE' O `.whl`, E NAO O `pyproject.toml`
----------------------------------------------------------
Assertar sobre a declaracao provaria que alguem escreveu `include`, nao que a
wheel resultante obedece. Entre os dois ha' o backend de build, o
`MANIFEST.in`, `package-data`, diretorios novos e o comportamento de
`packages.find` — qualquer um deles pode reintroduzir o pacote alheio com o
`include` intacto. O teste CONSTROI a distribuicao e le' o `top_level.txt` e o
`RECORD` de dentro do arquivo.

E' lento (dezenas de segundos) e por isso e' um teste so'. O que ele cobre nao
tem substituto barato.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pytest

RAIZ_API = pathlib.Path(__file__).resolve().parents[1]

#: Pacotes de topo que esta distribuicao PODE publicar. Allowlist por
#: inclusao: pacote novo entra aqui de proposito, e a pessoa que o acrescentar
#: tem de decidir se o nome e' seguro num ambiente compartilhado.
TOPO_PERMITIDO = {"app"}

#: Nomes que, se publicados, colidem com pacote de terceiro ou com nome generico
#: que qualquer projeto pode ter. `alembic` e' o que causou o incidente; `tests`
#: e `tools` sao os proximos da fila pela mesma razao.
TOPO_PROIBIDO = {"alembic", "etl", "tests", "tools"}

#: O que a imagem do Airflow importa. Se a wheel deixar de traze-los, a DAG
#: `marketplace_tower_kit_reference_publish` quebra no preflight.
CONTRATO = (
    "app/__init__.py",
    "app/services/__init__.py",
    "app/services/pma_kit_bom.py",
    "app/services/pma_match.py",
    "app/services/pma_domain.py",
)


def _construir_wheel(destino: pathlib.Path) -> pathlib.Path:
    """Constroi a wheel REAL de `apps/api`. Pula se o backend nao estiver la'."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(destino),
             str(RAIZ_API)],
            capture_output=True, text=True, timeout=900,
        )
    except FileNotFoundError:  # pragma: no cover - ambiente sem build
        pytest.skip("modulo `build` indisponivel")
    if proc.returncode != 0:
        if "No module named build" in (proc.stderr or ""):
            pytest.skip("modulo `build` indisponivel")
        pytest.fail(f"a wheel nao construiu:\n{proc.stdout[-2000:]}\n"
                    f"{proc.stderr[-2000:]}")
    wheels = sorted(destino.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


@pytest.fixture(scope="module")
def wheel():
    """Uma construcao por modulo: o custo e' o motivo de nao haver dez testes."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mktplace-api-wheel-"))
    # `build` deixa residuo em `apps/api/build/`; remove-lo antes evita que uma
    # execucao anterior contamine o conteudo medido.
    shutil.rmtree(RAIZ_API / "build", ignore_errors=True)
    try:
        caminho = _construir_wheel(tmp)
        with zipfile.ZipFile(caminho) as z:
            nomes = z.namelist()
            tl = next(n for n in nomes if n.endswith("dist-info/top_level.txt"))
            top_level = z.read(tl).decode().split()
        yield {"caminho": caminho, "nomes": nomes, "top_level": top_level}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(RAIZ_API / "build", ignore_errors=True)


def test_top_level_publica_somente_app(wheel):
    """`top_level.txt` e' o que o pip usa para saber o que a wheel ocupa."""
    assert set(wheel["top_level"]) == TOPO_PERMITIDO, wheel["top_level"]


def test_nenhum_pacote_proibido_no_top_level(wheel):
    invasores = set(wheel["top_level"]) & TOPO_PROIBIDO
    assert not invasores, (
        f"{sorted(invasores)} no top_level.txt: instalar esta wheel grava por "
        f"cima de pacote alheio. Foi assim que o alembic do Airflow morreu.")


def test_o_conteudo_do_zip_tambem_so_tem_app(wheel):
    """O `top_level.txt` pode mentir; os ARQUIVOS nao.

    Uma wheel pode declarar um `top_level` curto e ainda assim carregar
    arquivos de outro pacote — `package-data` e `MANIFEST.in` fazem isso. Por
    isso a verificacao e' sobre os dois.
    """
    topo = {n.split("/")[0] for n in wheel["nomes"] if "/" in n}
    topo -= {n for n in topo if n.endswith(".dist-info")}
    assert topo == TOPO_PERMITIDO, sorted(topo)


def test_nenhum_arquivo_de_migration_viaja_na_wheel(wheel):
    """O `alembic/__init__.py` de 0 bytes e' o arquivo que causou o incidente."""
    migrations = [n for n in wheel["nomes"]
                  if n.startswith("alembic/") or "/versions/" in n]
    assert not migrations, migrations[:10]


def test_o_contrato_de_kit_continua_na_wheel(wheel):
    """A correcao nao pode ter tirado o que a DAG importa."""
    faltando = [c for c in CONTRATO if c not in wheel["nomes"]]
    assert not faltando, faltando


def test_o_record_nao_declara_arquivo_fora_de_app(wheel):
    """`RECORD` e' a lista que o pip usa para DESINSTALAR.

    Um `RECORD` com caminho fora de `app/` significa que `pip uninstall
    mktplace-api` apagaria arquivo de outra distribuicao — o estrago simetrico
    ao da instalacao, e mais silencioso.
    """
    with zipfile.ZipFile(wheel["caminho"]) as z:
        nome = next(n for n in z.namelist() if n.endswith("dist-info/RECORD"))
        linhas = z.read(nome).decode().splitlines()
    caminhos = [l.split(",")[0] for l in linhas if l.strip()]
    intrusos = [c for c in caminhos
                if not c.startswith("app/") and ".dist-info/" not in c]
    assert not intrusos, intrusos[:10]
