"""OPS-AIRFLOW-MIGRATION-1 — a wheel `mktplace-pipelines` carrega o fecho da cadeia.

O Airflow instala esta wheel (`--no-deps`) e chama cada CLI da `daily_chain`
como subprocesso. Um modulo do fecho fora da wheel so' apareceria em runtime,
como `ModuleNotFoundError` no meio do ciclo. Por isso:

1. o fecho e' RECONSTRUIDO a partir de `PIPELINES["daily_chain"]`, nao de uma
   lista copiada — step novo na cadeia sem empacotamento reprova aqui. Somam-se
   os CLIs que o Airflow chama FORA da cadeia (`CLIS_DO_AIRFLOW_FORA_DA_CADEIA`);
2. a wheel e' CONSTRUIDA e lida (como em `apps/api/tests/test_wheel_packaging`):
   provar o `include` do pyproject provaria so' a declaracao.
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pytest

from pipelines.ops import orchestrate as o

RAIZ = pathlib.Path(__file__).resolve().parents[2]

#: Steps da cadeia que NAO vao para o Airflow: exigem credencial de escrita no
#: Data Mart (`DATAMART_GOLD_WRITE_URL`) e o artefato de consentimento local.
NAO_PORTAVEIS = {"gold_regional_incremental", "sync_region_if_needed"}

TOPO_PROIBIDO = {"alembic", "app", "etl", "apps", "tests", "tools"}
SUBARVORES_PROIBIDAS = ("pipelines/ingestion/gold_regional/", "pipelines/ingestion/shopee_raw/",
                        "pipelines/tests/")


def _arquivo(mod: str) -> pathlib.Path | None:
    p = RAIZ.joinpath(*mod.split("."))
    if p.with_suffix(".py").exists():
        return p.with_suffix(".py")
    if (p / "__init__.py").exists():
        return p / "__init__.py"
    return None


def _importados(caminho: pathlib.Path, mod: str) -> set[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8-sig"))
    pacote = mod if caminho.name == "__init__.py" else mod.rsplit(".", 1)[0]
    saida: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            saida |= {a.name for a in no.names}
        elif isinstance(no, ast.ImportFrom):
            base = no.module or ""
            if no.level:
                partes = pacote.split(".")
                base = ".".join(partes[: len(partes) - no.level + 1]
                                + ([no.module] if no.module else []))
            saida.add(base)
            saida |= {f"{base}.{a.name}" for a in no.names}
    return saida


def fecho_interno(raizes) -> set[str]:
    vistos: set[str] = set()
    fila = list(raizes)
    while fila:
        mod = fila.pop()
        if mod in vistos or _arquivo(mod) is None:
            continue
        vistos.add(mod)
        partes = mod.split(".")
        fila += [".".join(partes[:i]) for i in range(1, len(partes))]
        fila += [m for m in _importados(_arquivo(mod), mod)
                 if m.split(".")[0] == "pipelines" and _arquivo(m)]
    return vistos


#: CLIs que DAGs do Airflow chamam fora da `daily_chain`. A Expedicao TikTok
#: saiu da cadeia (PR #87) e o fecho reconstruido dela deixaria de cobri-la.
CLIS_DO_AIRFLOW_FORA_DA_CADEIA = {
    "pipelines.expedicao.cli",                  # marketplace_tower_expedicao_refresh (Shopee/ML)
    "pipelines.expedicao.tiktok_cli",           # marketplace_tower_expedicao_refresh (TikTok)
    "pipelines.sync_shopee_daily_api_shadow",   # marketplace_tower_shopee_daily_shadow
    "pipelines.dre.insumo_publisher",           # marketplace_tower_dre_insumo_publish
}


def _clis_portaveis() -> set[str]:
    cadeia = {s.module for s in o.PIPELINES["daily_chain"] if s.name not in NAO_PORTAVEIS}
    return cadeia | CLIS_DO_AIRFLOW_FORA_DA_CADEIA


def test_clis_do_airflow_fora_da_cadeia_existem_e_nao_estao_na_cadeia():
    for mod in CLIS_DO_AIRFLOW_FORA_DA_CADEIA:
        assert _arquivo(mod) is not None, mod
    assert not CLIS_DO_AIRFLOW_FORA_DA_CADEIA & {s.module for s in o.PIPELINES["daily_chain"]}


def test_os_dois_steps_nao_portaveis_ainda_existem_na_cadeia():
    """Se sumirem da cadeia, a exclusao aqui deixa de fazer sentido."""
    assert NAO_PORTAVEIS <= {s.name for s in o.PIPELINES["daily_chain"]}


#: Modulos de `app.services` (distribuicao `mktplace-api`) que o fecho importa
#: NO TOPO. A imagem do Airflow nao instala `mktplace-api`: copia so' um fecho
#: fixo dele (Dockerfile do repositorio do Airflow). Esta lista e' o contrato
#: com aquela copia — modulo novo aqui sem entrar la' quebra no primeiro import.
APP_SERVICES_NO_TOPO = {
    "pma_domain", "pma_match",            # pipelines.channel_offer_sync (PMA)
    "avoe_snapshot_service",              # pipelines.ops.health_check
}


def _imports_no_topo(mod: str) -> list[tuple[str, list[str]]]:
    arvore = ast.parse(_arquivo(mod).read_text(encoding="utf-8-sig"))
    saida = []
    for no in arvore.body:
        if isinstance(no, ast.Import):
            saida += [(a.name, []) for a in no.names]
        elif isinstance(no, ast.ImportFrom) and not no.level and no.module:
            saida.append((no.module, [a.name for a in no.names]))
    return saida


def test_fecho_nao_importa_alembic_nem_etl_no_topo():
    for mod in fecho_interno(_clis_portaveis()):
        for nome, _ in _imports_no_topo(mod):
            assert nome.split(".")[0] not in {"alembic", "etl"}, (mod, nome)


def test_imports_de_app_no_topo_sao_exatamente_os_do_contrato():
    vistos = set()
    for mod in fecho_interno(_clis_portaveis()):
        for nome, itens in _imports_no_topo(mod):
            if nome.split(".")[0] != "app":
                continue
            assert nome == "app.services", (mod, nome)
            vistos |= set(itens)
    assert vistos == APP_SERVICES_NO_TOPO


@pytest.mark.parametrize("servico", sorted(APP_SERVICES_NO_TOPO))
def test_servicos_do_contrato_so_dependem_de_stdlib_sqlalchemy_ou_do_proprio_contrato(servico):
    """A copia na imagem nao arrasta dependencia: cada servico importa so'
    stdlib, sqlalchemy ou outro servico da propria lista."""
    caminho = RAIZ / "apps" / "api" / "app" / "services" / f"{servico}.py"
    for no in ast.walk(ast.parse(caminho.read_text(encoding="utf-8-sig"))):
        if isinstance(no, ast.Import):
            nomes = [(a.name, []) for a in no.names]
        elif isinstance(no, ast.ImportFrom) and not no.level and no.module:
            nomes = [(no.module, [a.name for a in no.names])]
        else:
            continue
        for nome, itens in nomes:
            topo = nome.split(".")[0]
            if topo == "app":
                assert nome == "app.services" and set(itens) <= APP_SERVICES_NO_TOPO, (servico, nome, itens)
            else:
                assert topo in sys.stdlib_module_names or topo in {"sqlalchemy", "__future__"}, (servico, nome)


def _construir_wheel(destino: pathlib.Path) -> pathlib.Path:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(destino), str(RAIZ)],
            capture_output=True, text=True, timeout=900)
    except FileNotFoundError:  # pragma: no cover
        pytest.skip("modulo `build` indisponivel")
    if proc.returncode != 0:
        if "No module named build" in (proc.stderr or ""):
            pytest.skip("modulo `build` indisponivel")
        pytest.fail(f"a wheel nao construiu:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    wheels = sorted(destino.glob("mktplace_pipelines-*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


@pytest.fixture(scope="module")
def wheel():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mktplace-pipelines-wheel-"))
    shutil.rmtree(RAIZ / "build", ignore_errors=True)
    try:
        caminho = _construir_wheel(tmp)
        with zipfile.ZipFile(caminho) as z:
            nomes = z.namelist()
            tl = next(n for n in nomes if n.endswith("dist-info/top_level.txt"))
            top_level = z.read(tl).decode().split()
        yield {"nomes": set(nomes), "top_level": top_level}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(RAIZ / "build", ignore_errors=True)


def test_top_level_e_so_pipelines(wheel):
    assert wheel["top_level"] == ["pipelines"]
    assert not any(n.split("/")[0] in TOPO_PROIBIDO for n in wheel["nomes"])


def test_todo_modulo_do_fecho_da_cadeia_esta_na_wheel(wheel):
    faltando = []
    for mod in sorted(fecho_interno(_clis_portaveis())):
        rel = _arquivo(mod).relative_to(RAIZ).as_posix()
        if rel not in wheel["nomes"]:
            faltando.append(rel)
    assert not faltando, f"modulos do fecho fora da wheel: {faltando}"


def test_subarvores_nao_portaveis_ficam_fora(wheel):
    for proibida in SUBARVORES_PROIBIDAS:
        assert not any(n.startswith(proibida) for n in wheel["nomes"]), proibida
