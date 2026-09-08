"""Gate AVH-4A / AVH-4A-R — contraprovas do contrato e do importador da Avoe.

Sem fixture e sem `parametrize`, para permitir execucao por runner de stdlib.
Cada bloco cita o FINDING do AVH-4A-R que cobre.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import shutil
import tempfile
import tokenize
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from pipelines.avoe import snapshot_contract as sc
from pipelines.avoe import snapshot_import as si

CAPTURED = "2026-09-01T15:32:34.320Z"
DDL_PATH = Path("db/sql/marts/avoe_proxy_snapshot_ddl.sql")
MIG_PATH = Path("apps/api/alembic/versions/015_create_avoe_proxy_snapshots.py")


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------

def _codigo_executavel(caminho: Path) -> str:
    """Codigo sem comentario e sem literal de string: docstring nao e' codigo."""
    fonte = caminho.read_text(encoding="utf-8")
    saida = []
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        saida.append(tok.string)
    return " ".join(saida)


def _jsonl(linhas: list[dict]) -> str:
    return "".join(json.dumps(x) + "\n" for x in linhas)


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _meta(marca: str, mes: str, meta_valor, faturamento=None,
          criado="2026-08-26T13:05:36+00:00", atualizado="2026-08-26T13:05:36+00:00"):
    linha = {"id": f"id-{marca}-{mes}", "marca": marca, "mes_referencia": mes,
             "meta": meta_valor, "faturamento": faturamento}
    if criado is not None:
        linha["criado_em"] = criado
    if atualizado is not None:
        linha["atualizado_em"] = atualizado
    return linha


def _fat(marca: str, plataforma: str, dia: str, valor, ads=None, criado=None):
    return {"id": f"id-{marca}-{plataforma}-{dia}", "marca": marca,
            "plataforma": plataforma, "data_referencia": dia,
            "faturamento": valor, "ads": ads,
            "criado_em": criado if criado is not None else dia + "T10:00:00+00:00"}


def _escreve(p: Path, texto: str) -> None:
    p.write_text(texto, encoding="utf-8", newline="\n")


def _make_snapshot(dirpath: Path, metas: list[dict], fats: list[dict],
                   captured_at: str | None = CAPTURED,
                   corromper: str | None = None,
                   omitir: str | None = None,
                   status: str = "OK",
                   row_count_metas=None,
                   sha_file: str | None = "auto",
                   duplicar_tabela: bool = False) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    txt_metas = _jsonl(metas)
    txt_fats = _jsonl(fats)

    if omitir != sc.FILE_TARGETS:
        _escreve(dirpath / sc.FILE_TARGETS, txt_metas)
    if omitir != sc.FILE_CHANNELS:
        _escreve(dirpath / sc.FILE_CHANNELS, txt_fats)

    tabelas = [
        {"table": "resumo_marca_mes", "file": sc.FILE_TARGETS,
         "row_count": row_count_metas if row_count_metas is not None else len(metas),
         "sha256": _sha(txt_metas)},
        {"table": "faturamento_diario_marca", "file": sc.FILE_CHANNELS,
         "row_count": len(fats), "sha256": _sha(txt_fats)},
    ]
    if row_count_metas == "REMOVER":
        tabelas[0].pop("row_count")
    if duplicar_tabela:
        tabelas.append(dict(tabelas[0]))

    manifest = {"gate": "AVH-3A", "source_system": "avoe_hub", "status": status,
                "captured_at": captured_at, "page_size": 1000, "tables": tabelas}
    if captured_at is None:
        manifest.pop("captured_at")
    _escreve(dirpath / "MANIFEST.json", json.dumps(manifest, indent=1))

    if sha_file == "auto":
        h = hashlib.sha256((dirpath / "MANIFEST.json").read_bytes()).hexdigest()
        _escreve(dirpath / "MANIFEST.sha256", f"{h}  MANIFEST.json\n")
    elif sha_file is not None:
        _escreve(dirpath / "MANIFEST.sha256", sha_file)

    if corromper:
        alvo = dirpath / corromper
        _escreve(alvo, alvo.read_text(encoding="utf-8")
                 + json.dumps(_meta("Intruso", "2026-08-01", 1)) + "\n")
    return dirpath


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="avh4a-"))


def _snapshot_padrao(base: Path) -> Path:
    metas = [
        _meta("Apice", "2026-08-01", 5000000),
        _meta("Barbours", "2026-08-01", 10000000),
        _meta("Denavita", "2026-08-01", 5000000),
        _meta("GoCase", "2026-08-01", 5000000),
        _meta("Kokeshi", "2026-08-01", 10000000, faturamento=9557070.49),
        _meta("Bloom", "2026-07-01", 0),
        _meta("Apice", "2026-06-01", 50000),
    ]
    fats = [
        _fat("Kokeshi", "SHEIN", "2026-08-01", 10000),
        _fat("Kokeshi", "SHEIN", "2026-08-02", 12000),
        _fat("Barbours", "MAGALU", "2026-08-01", 5000),
        _fat("Kokeshi", "TIKTOK", "2026-08-01", 900000),
        _fat("Kokeshi", "MELI", "2026-08-01", 800000),
        _fat("Kokeshi", "SHOPEE", "2026-08-01", 700000),
        _fat("Kokeshi", "SHEIN", "2026-05-01", 99999),
        _fat("Kokeshi", "KWAI", "2026-08-03", None),
    ]
    return _make_snapshot(base / "snap", metas, fats)


# ===========================================================================
# FINDING 5 — manifesto, MANIFEST.sha256 obrigatorio, row_count
# ===========================================================================

def test_manifesto_valido_e_lido():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        assert r.captured_at == datetime(2026, 9, 1, 15, 32, 34, 320000, tzinfo=timezone.utc)
        assert len(r.snapshot_id) == 32
        assert set(r.files) == set(sc.ALLOWED_FILES)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_manifesto_ausente_falha():
    base = _tmp()
    try:
        (base / "vazio").mkdir()
        try:
            sc.read_snapshot(base / "vazio")
        except sc.SnapshotContractError as exc:
            assert "MANIFEST.json ausente" in str(exc)
        else:
            raise AssertionError("deveria falhar sem manifesto")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_manifest_sha256_ausente_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           sha_file=None)
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "MANIFEST.sha256 ausente" in str(exc)
        else:
            raise AssertionError("MANIFEST.sha256 e' obrigatorio")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_manifest_sha256_vazio_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           sha_file="   \n")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "vazio" in str(exc)
        else:
            raise AssertionError("MANIFEST.sha256 vazio deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_manifest_sha256_malformado_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           sha_file="nao-e-um-hash  MANIFEST.json\n")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "malformado" in str(exc)
        else:
            raise AssertionError("hash malformado deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_manifest_sha256_com_multiplas_entradas_falha():
    base = _tmp()
    try:
        h = "0" * 64
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           sha_file=f"{h}  MANIFEST.json\n{h}  OUTRO.json\n")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "ambiguo" in str(exc)
        else:
            raise AssertionError("multiplas entradas deveriam falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_manifest_sha256_divergente_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           sha_file="a" * 64 + "  MANIFEST.json\n")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "divergente" in str(exc)
        else:
            raise AssertionError("hash divergente do manifesto deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_row_count_ausente_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           row_count_metas="REMOVER")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "row_count ausente" in str(exc)
        else:
            raise AssertionError("row_count ausente deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_row_count_nao_inteiro_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           row_count_metas="1")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "deve ser inteiro" in str(exc)
        else:
            raise AssertionError("row_count string deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_row_count_negativo_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           row_count_metas=-1)
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "negativo" in str(exc)
        else:
            raise AssertionError("row_count negativo deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_row_count_zero_com_arquivo_de_uma_linha_falha():
    """FINDING 5 — comparacao vale inclusive quando declarado zero."""
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           row_count_metas=0)
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "declara 0 linhas" in str(exc)
        else:
            raise AssertionError("row_count=0 com 1 linha deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_tabela_declarada_duas_vezes_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           duplicar_tabela=True)
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "mais de uma vez" in str(exc)
        else:
            raise AssertionError("tabela duplicada no manifesto deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_arquivo_alterado_depois_do_hash_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           corromper=sc.FILE_TARGETS)
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "hash divergente" in str(exc)
        else:
            raise AssertionError("deveria detectar arquivo alterado")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_arquivo_declarado_e_ausente_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           omitir=sc.FILE_CHANNELS)
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "ausente" in str(exc)
        else:
            raise AssertionError("deveria falhar com arquivo ausente")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_status_diferente_de_ok_e_recusado():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           status="TIMEOUT")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "somente OK" in str(exc)
        else:
            raise AssertionError("status != OK deveria ser recusado")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_captured_at_ausente_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           captured_at=None)
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "captured_at ausente" in str(exc)
        else:
            raise AssertionError("captured_at ausente deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_captured_at_sem_timezone_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 1)], [],
                           captured_at="2026-09-01T15:32:34")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "timezone" in str(exc)
        else:
            raise AssertionError("captured_at ingenuo deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ===========================================================================
# FINDING 6 — schema de origem: obrigatoria / opcional / proibida
# ===========================================================================

def test_coluna_obrigatoria_ausente_falha():
    base = _tmp()
    try:
        linha = _meta("Apice", "2026-08-01", 1)
        del linha["meta"]
        d = _make_snapshot(base / "s", [linha], [])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "coluna obrigatoria ausente" in str(exc)
            assert "meta" in str(exc)
        else:
            raise AssertionError("coluna obrigatoria ausente deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_coluna_opcional_ausente_e_aceita():
    base = _tmp()
    try:
        linha = _meta("Apice", "2026-08-01", 1000, criado=None, atualizado=None)
        del linha["id"]
        del linha["faturamento"]
        d = _make_snapshot(base / "s", [linha], [])
        r = sc.read_snapshot(d)
        assert len(r.target_rows) == 1
        assert r.target_rows[0]["source_recorded_at"] is None
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_coluna_proibida_falha():
    base = _tmp()
    try:
        linha = _meta("Apice", "2026-08-01", 1)
        linha["gmv"] = 123
        d = _make_snapshot(base / "s", [linha], [])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "coluna PROIBIDA" in str(exc)
        else:
            raise AssertionError("coluna proibida deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_coluna_extra_fora_do_schema_falha():
    base = _tmp()
    try:
        linha = _meta("Apice", "2026-08-01", 1)
        linha["campo_novo_da_avoe"] = 123
        d = _make_snapshot(base / "s", [linha], [])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "fora do schema declarado" in str(exc)
        else:
            raise AssertionError("coluna nova deveria falhar, nao ser ignorada")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_faturamento_permanece_declarado_como_opcional_lido_e_descartado():
    """FINDING 6 — nao pode desaparecer do schema sem revisao."""
    assert "faturamento" in sc.OPTIONAL_TARGETS
    assert "descartado" in sc.OPTIONAL_TARGETS["faturamento"]
    assert "faturamento" not in sc.REQUIRED_TARGETS


def test_toda_coluna_opcional_tem_razao_documentada():
    for mapa in (sc.OPTIONAL_TARGETS, sc.OPTIONAL_CHANNELS):
        for coluna, razao in mapa.items():
            assert isinstance(razao, str) and len(razao) > 10, coluna


# ===========================================================================
# FINDING 3 — duplicidade na chave de origem
# ===========================================================================

def test_chave_de_origem_declarada():
    assert sc.SOURCE_KEY_TARGETS == ("mes_referencia", "marca")
    assert sc.SOURCE_KEY_CHANNELS == ("data_referencia", "marca", "plataforma")


def test_duplicata_diaria_identica_falha():
    base = _tmp()
    try:
        linha = _fat("Kokeshi", "SHEIN", "2026-08-01", 10000)
        outra = dict(linha)
        outra["id"] = "outro-id"
        d = _make_snapshot(base / "s", [], [linha, outra])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "duplicata IDENTICA" in str(exc)
            assert "nao soma nem" in str(exc)
        else:
            raise AssertionError("duplicata identica deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_duplicata_diaria_conflitante_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [], [
            _fat("Kokeshi", "SHEIN", "2026-08-01", 10000),
            _fat("Kokeshi", "SHEIN", "2026-08-01", 99999),
        ])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "duplicata CONFLITANTE" in str(exc)
        else:
            raise AssertionError("duplicata conflitante deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_duplicata_de_meta_falha():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [
            _meta("Apice", "2026-08-01", 5000000),
            _meta("Apice", "2026-08-01", 7000000),
        ], [])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "duplicata" in str(exc)
        else:
            raise AssertionError("duplicata de meta deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_duplicidade_e_checada_antes_da_agregacao():
    """A checagem precede `_build_channels` no fluxo de `read_snapshot`."""
    fonte = _codigo_executavel(Path("pipelines/avoe/snapshot_contract.py"))
    pos_check = fonte.find("_assert_chave_unica ( brutos_channels")
    pos_build = fonte.find("channel_rows = _build_channels")
    assert pos_check != -1, "chamada de checagem nao encontrada"
    assert pos_build != -1, "chamada de agregacao nao encontrada"
    assert pos_check < pos_build


# ===========================================================================
# FINDING 7 — canal desconhecido bloqueia o snapshot inteiro
# ===========================================================================

def test_canal_nao_oficial_desconhecido_bloqueia_o_snapshot():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", 5000000)], [
            _fat("Kokeshi", "SHEIN", "2026-08-01", 10000),
            _fat("Kokeshi", "TEMU", "2026-08-02", 500),
        ])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "canal desconhecido" in str(exc)
            assert "TEMU" in str(exc)
            assert "snapshot inteiro" in str(exc)
        else:
            raise AssertionError("canal desconhecido deveria bloquear tudo")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_canal_oficial_conhecido_e_descarte_contabilizado():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        canais = {x["channel"] for x in r.channel_rows}
        assert canais & {"tiktok", "mercado_livre", "shopee", "meli"} == set()
        resumo = [x for x in r.rejections if x.get("motivo") == "resumo de descartes"][0]
        assert resumo["canais_oficiais_ignorados"] == 3
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_main_devolve_exit_de_falha_com_canal_desconhecido():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [], [_fat("Kokeshi", "TEMU", "2026-08-02", 1)])
        assert si.main(["--snapshot-dir", str(d)]) == 2
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ===========================================================================
# FINDING 8 — timestamps de proveniencia
# ===========================================================================

def test_timestamp_invalido_falha_e_nao_vira_nulo():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s",
                           [_meta("Apice", "2026-08-01", 1, atualizado="ontem")], [])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "timestamp invalido" in str(exc)
            assert "nulo" in str(exc)
        else:
            raise AssertionError("timestamp invalido deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_timestamp_naive_falha():
    base = _tmp()
    try:
        d = _make_snapshot(
            base / "s", [_meta("Apice", "2026-08-01", 1,
                               atualizado="2026-08-26T13:05:36")], [])
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            assert "sem timezone" in str(exc)
            assert "nao assume UTC" in str(exc)
        else:
            raise AssertionError("timestamp naive deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_timestamp_com_offset_e_normalizado_para_utc():
    base = _tmp()
    try:
        d = _make_snapshot(
            base / "s", [_meta("Apice", "2026-08-01", 1,
                               atualizado="2026-08-26T10:05:36-03:00")], [])
        r = sc.read_snapshot(d)
        gravado = r.target_rows[0]["source_recorded_at"]
        assert gravado == datetime(2026, 8, 26, 13, 5, 36, tzinfo=timezone.utc)
        assert gravado.utcoffset() == timedelta(0)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_source_recorded_at_prefere_atualizado_em():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [
            _meta("Apice", "2026-08-01", 1,
                  criado="2026-08-01T00:00:00+00:00",
                  atualizado="2026-08-26T13:05:36+00:00"),
        ], [])
        r = sc.read_snapshot(d)
        assert r.target_rows[0]["source_recorded_at"].day == 26
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_source_recorded_at_faz_fallback_para_criado_em():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [
            _meta("Apice", "2026-08-01", 1,
                  criado="2026-08-01T00:00:00+00:00", atualizado=None),
        ], [])
        r = sc.read_snapshot(d)
        assert r.target_rows[0]["source_recorded_at"].day == 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ===========================================================================
# FINDING 1 — idempotencia independente do run id
# ===========================================================================

def test_import_run_id_fica_fora_das_colunas_de_negocio():
    for op in si.OPERATIONAL_COLUMNS:
        assert op not in si.TARGET_BUSINESS_COLUMNS
        assert op not in si.CHANNEL_BUSINESS_COLUMNS
    assert "import_run_id" in si.TARGET_INSERT_COLUMNS
    assert "import_run_id" in si.CHANNEL_INSERT_COLUMNS
    assert "imported_at" not in si.TARGET_INSERT_COLUMNS
    assert "imported_at" not in si.CHANNEL_INSERT_COLUMNS


def test_snapshot_id_esta_nas_colunas_de_negocio():
    """Proveniencia, nao operacional: divergencia dele e' conflito."""
    assert "snapshot_id" in si.TARGET_BUSINESS_COLUMNS
    assert "snapshot_id" in si.CHANNEL_BUSINESS_COLUMNS


def test_nenhuma_coluna_de_realizado_no_destino():
    proibidas = {"faturamento", "realizado", "gmv", "official_realized_amount"}
    assert not (proibidas & set(si.TARGET_INSERT_COLUMNS))
    assert not (proibidas & set(si.CHANNEL_INSERT_COLUMNS))


def test_faturamento_da_origem_e_descartado_nas_metas():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        kokeshi = [x for x in r.target_rows if x["brand"] == "Kokeshi"]
        assert len(kokeshi) == 1
        assert "faturamento" not in kokeshi[0]
        assert kokeshi[0]["target_amount"] == Decimal("10000000.00")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_ddl_nao_declara_coluna_de_realizado():
    declaracoes = []
    for linha in DDL_PATH.read_text(encoding="utf-8").splitlines():
        nua = linha.strip()
        if not nua or nua.startswith("--"):
            continue
        primeira = nua.split()[0].lower()
        if primeira.isidentifier():
            declaracoes.append(primeira)
    for proibida in ("realizado", "official_realized_amount", "gmv", "gmv_oficial",
                     "receita", "faturamento"):
        assert proibida not in declaracoes, f"coluna proibida declarada: {proibida}"


# ===========================================================================
# FINDING 2 — snapshot corrente ATOMICO, sem forward-fill
# ===========================================================================

def _linha_meta(mes: date, marca: str, captured: datetime, valor: str, snap="s1"):
    return {"source": "avoe_hub", "captured_at": captured, "ref_month": mes,
            "brand": marca, "target_amount": Decimal(valor),
            "snapshot_id": snap, "import_run_id": "x"}


def _linha_canal(mes: date, marca: str, canal: str, captured: datetime, snap="s1"):
    return {"source": "avoe_hub", "captured_at": captured, "ref_month": mes,
            "brand": marca, "channel": canal, "snapshot_id": snap,
            "import_run_id": "x"}


def test_versao_corrente_e_o_maior_captured_at():
    t1 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 5, tzinfo=timezone.utc)
    rows = [_linha_meta(date(2026, 8, 1), "Apice", t1, "5000000", "s1"),
            _linha_meta(date(2026, 8, 1), "Apice", t2, "6000000", "s2")]
    corrente = sc.select_current_version(rows)
    assert len(corrente) == 1
    assert corrente[0]["captured_at"] == t2
    assert corrente[0]["target_amount"] == Decimal("6000000")


def test_marca_ausente_na_captura_nova_nao_e_ressuscitada():
    """FINDING 2 — a unidade e' a CAPTURA, nao a marca."""
    t1 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 5, tzinfo=timezone.utc)
    rows = [
        _linha_meta(date(2026, 8, 1), "A", t1, "100", "s1"),
        _linha_meta(date(2026, 8, 1), "B", t1, "200", "s1"),
        _linha_meta(date(2026, 8, 1), "A", t2, "150", "s2"),
    ]
    corrente = sc.select_current_version(rows)
    assert {r["brand"] for r in corrente} == {"A"}, "B nao pode voltar da captura anterior"
    assert all(r["captured_at"] == t2 for r in corrente)
    assert len(corrente) == 1


def test_canal_ausente_na_captura_nova_nao_e_ressuscitado():
    t1 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 5, tzinfo=timezone.utc)
    rows = [
        _linha_canal(date(2026, 8, 1), "Kokeshi", "shein", t1, "s1"),
        _linha_canal(date(2026, 8, 1), "Kokeshi", "kwai", t1, "s1"),
        _linha_canal(date(2026, 8, 1), "Kokeshi", "shein", t2, "s2"),
    ]
    corrente = sc.select_current_version(rows)
    assert {r["channel"] for r in corrente} == {"shein"}


def test_snapshot_antigo_permanece_consultavel():
    t1 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 5, tzinfo=timezone.utc)
    rows = [_linha_meta(date(2026, 8, 1), "Apice", t1, "5000000", "s1"),
            _linha_meta(date(2026, 8, 1), "Apice", t2, "6000000", "s2")]
    sc.select_current_version(rows)
    assert len(rows) == 2


def test_competencias_distintas_escolhem_capturas_distintas():
    t1 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 5, tzinfo=timezone.utc)
    rows = [_linha_meta(date(2026, 8, 1), "Apice", t1, "5000000", "s1"),
            _linha_meta(date(2026, 9, 1), "Apice", t2, "7000000", "s2")]
    corrente = sc.select_current_version(rows)
    assert len(corrente) == 2
    por_mes = {r["ref_month"]: r["captured_at"] for r in corrente}
    assert por_mes[date(2026, 8, 1)] == t1
    assert por_mes[date(2026, 9, 1)] == t2


def test_snapshot_id_divergente_no_mesmo_captured_at_falha():
    t = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [_linha_meta(date(2026, 8, 1), "A", t, "100", "s1"),
            _linha_meta(date(2026, 8, 1), "B", t, "200", "s2")]
    try:
        sc.select_current_version(rows)
    except sc.SnapshotContractError as exc:
        assert "snapshot_id divergente" in str(exc)
    else:
        raise AssertionError("snapshot_id divergente na mesma captura deveria falhar")


def test_selecao_nunca_usa_imported_at():
    arvore = ast.parse(Path("pipelines/avoe/snapshot_contract.py").read_text(encoding="utf-8"))
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef) and n.name == "select_current_version"]
    assert len(alvo) == 1
    corpo = [n for n in alvo[0].body
             if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    fonte = " ; ".join(ast.unparse(n) for n in corpo)
    assert "captured_at" in fonte
    assert "imported_at" not in fonte


def test_selecao_nao_agrupa_por_marca_nem_canal():
    arvore = ast.parse(Path("pipelines/avoe/snapshot_contract.py").read_text(encoding="utf-8"))
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef) and n.name == "select_current_version"][0]
    corpo = [n for n in alvo.body
             if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    fonte = " ; ".join(ast.unparse(n) for n in corpo)
    assert "ref_month" in fonte
    assert "'brand'" not in fonte and '"brand"' not in fonte
    assert "'channel'" not in fonte and '"channel"' not in fonte


def test_linha_sem_captured_at_falha_na_selecao():
    rows = [{"source": "avoe_hub", "captured_at": None,
             "ref_month": date(2026, 8, 1), "brand": "Apice"}]
    try:
        sc.select_current_version(rows)
    except sc.SnapshotContractError as exc:
        assert "captured_at" in str(exc)
    else:
        raise AssertionError("linha sem captured_at deveria falhar")


# ===========================================================================
# Cobertura: Denavita, GoCase, ausencia que nao vira zero
# ===========================================================================

def test_denavita_e_gocase_preservadas_sem_realizado():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        marcas = {x["brand"] for x in r.target_rows}
        assert "Denavita" in marcas and "GoCase" in marcas
        for x in r.target_rows:
            if x["brand"] in ("Denavita", "GoCase"):
                assert x["target_amount"] > 0
                assert "faturamento" not in x
                assert x["brand_key"] in ("denavita", "gocase")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_marca_sem_regra_de_crosswalk_fica_com_brand_key_nulo():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Bloom", "2026-08-01", 1000)], [])
        r = sc.read_snapshot(d)
        assert len(r.target_rows) == 1
        assert r.target_rows[0]["brand_key"] is None
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_null_e_distinto_de_zero_em_canais():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [], [_fat("Kokeshi", "KWAI", "2026-08-03", None)])
        r = sc.read_snapshot(d)
        assert len(r.channel_rows) == 1
        assert r.channel_rows[0]["reported_amount"] is None
        assert r.channel_rows[0]["days_covered"] == 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_zero_informado_permanece_zero():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [], [_fat("Kokeshi", "KWAI", "2026-08-03", 0)])
        r = sc.read_snapshot(d)
        assert r.channel_rows[0]["reported_amount"] == Decimal("0.00")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_meta_nula_e_rejeitada_e_nao_virou_zero():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [_meta("Apice", "2026-08-01", None)], [])
        r = sc.read_snapshot(d)
        assert r.target_rows == []
        assert any("nula" in str(x.get("motivo")) for x in r.rejections)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_competencia_anterior_a_agosto_e_rejeitada():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        assert {x["ref_month"] for x in r.target_rows} == {date(2026, 8, 1)}
        motivos = [x for x in r.rejections
                   if x.get("dataset") == "targets" and "anterior a" in str(x.get("motivo"))]
        assert len(motivos) == 2
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_regime_diario_corta_antes_de_10_06():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        assert all(x["ref_month"] >= date(2026, 6, 1) for x in r.channel_rows)
        resumo = [x for x in r.rejections if x.get("motivo") == "resumo de descartes"][0]
        assert resumo["linhas_fora_do_regime_diario"] == 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_agregacao_mensal_soma_dias_e_registra_cobertura():
    base = _tmp()
    try:
        d = _make_snapshot(base / "s", [], [
            _fat("Kokeshi", "SHEIN", "2026-08-01", 10000),
            _fat("Kokeshi", "SHEIN", "2026-08-02", 12000),
        ])
        r = sc.read_snapshot(d)
        assert len(r.channel_rows) == 1
        linha = r.channel_rows[0]
        assert linha["reported_amount"] == Decimal("22000.00")
        assert linha["days_covered"] == 2
        assert linha["coverage_status"] == "partial_month"
        assert linha["first_business_date"] == date(2026, 8, 1)
        assert linha["last_business_date"] == date(2026, 8, 2)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ===========================================================================
# Moeda e proxy
# ===========================================================================

def test_moeda_assumida_com_warning_obrigatorio():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        for x in r.target_rows + r.channel_rows:
            assert x["currency_code"] == "BRL"
            assert x["currency_status"] == "assumed_unconfirmed"
        for x in r.target_rows:
            assert "inferido" in x["currency_warning"]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_moeda_nunca_marcada_como_confirmada():
    assert sc.CURRENCY_STATUS_ASSUMED == "assumed_unconfirmed"
    assert "ck_pabmts_currency_warning_obrigatorio" in DDL_PATH.read_text(encoding="utf-8")


def test_is_proxy_e_definition_status_obrigatorios():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        assert r.channel_rows
        for x in r.channel_rows:
            assert x["is_proxy"] is True
            assert x["definition_status"] == "unconfirmed"
            assert "GMV oficial" in x["definition_warning"]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_allowlist_de_canal_espelha_a_migration():
    ddl = DDL_PATH.read_text(encoding="utf-8")
    for canal in sc.CHANNEL_MAP.values():
        assert f"'{canal}'" in ddl
    for oficial in ("'tiktok'", "'mercado_livre'", "'shopee'"):
        assert oficial not in ddl


# ===========================================================================
# FINDING 4 — migration fail-closed e equivalencia com o DDL
# ===========================================================================

def test_migration_e_fail_closed():
    mig = MIG_PATH.read_text(encoding="utf-8")
    for tolerante in ("IF NOT EXISTS", "IF EXISTS", "CASCADE"):
        assert tolerante not in mig, f"migration nao pode tolerar: {tolerante}"


def test_ddl_e_fail_closed():
    ddl = DDL_PATH.read_text(encoding="utf-8")
    for tolerante in ("IF NOT EXISTS", "IF EXISTS", "CASCADE"):
        assert tolerante not in ddl


def test_migration_e_linear_a_partir_do_head_real():
    arvore = ast.parse(MIG_PATH.read_text(encoding="utf-8"))
    valores = {}
    for no in arvore.body:
        if isinstance(no, ast.Assign) and isinstance(no.targets[0], ast.Name):
            valores[no.targets[0].id] = getattr(no.value, "value", None)
    assert valores.get("revision") == "015"
    assert valores.get("down_revision") == "014"
    assert valores.get("branch_labels") is None
    assert valores.get("depends_on") is None


def test_migration_nao_toca_objeto_oficial():
    mig = MIG_PATH.read_text(encoding="utf-8")
    for oficial in ("fact_marketplace_daily_performance", "dim_loja", "dim_marketplace",
                    "fact_goal_monthly"):
        assert f"ALTER TABLE marts.{oficial}" not in mig
        assert f"DROP TABLE marts.{oficial}" not in mig


def test_migration_cria_exatamente_dois_objetos():
    mig = MIG_PATH.read_text(encoding="utf-8")
    assert mig.count("CREATE TABLE marts.") == 2
    assert "proxy_avoe_brand_monthly_target_snapshot" in mig
    assert "proxy_avoe_extra_channel_monthly_snapshot" in mig


def test_downgrade_remove_apenas_os_dois_objetos():
    down = MIG_PATH.read_text(encoding="utf-8").split("def downgrade")[1]
    assert down.count("DROP TABLE marts.proxy_avoe") == 2
    assert "fact_marketplace_daily_performance" not in down


def _colunas_declaradas(texto: str, tabela: str) -> list[tuple[str, str, str]]:
    """(coluna, tipo, nullability) na ordem de declaracao."""
    inicio = texto.index(f"CREATE TABLE marts.{tabela}")
    corpo = texto[inicio:]
    corpo = corpo[:corpo.index("CONSTRAINT pk_")]
    saida = []
    for linha in corpo.splitlines():
        nua = linha.strip().rstrip(",")
        if not nua or nua.startswith("--") or nua.startswith("CREATE TABLE"):
            continue
        partes = nua.split()
        if len(partes) < 2 or not partes[0].isidentifier():
            continue
        tipo = partes[1]
        if len(partes) > 2 and partes[2].startswith("("):
            tipo += partes[2]
        nulo = "NOT NULL" if "NOT NULL" in nua else "NULL"
        saida.append((partes[0].lower(), tipo.upper().replace(" ", ""), nulo))
    return saida


def test_equivalencia_colunas_tipos_e_nullability():
    ddl = DDL_PATH.read_text(encoding="utf-8")
    mig = MIG_PATH.read_text(encoding="utf-8")
    for tabela in ("proxy_avoe_brand_monthly_target_snapshot",
                   "proxy_avoe_extra_channel_monthly_snapshot"):
        a = _colunas_declaradas(ddl, tabela)
        b = _colunas_declaradas(mig, tabela)
        assert a, f"nenhuma coluna extraida do DDL para {tabela}"
        assert a == b, f"divergencia DDL x migration em {tabela}: {set(a) ^ set(b)}"


def test_equivalencia_pks():
    ddl = DDL_PATH.read_text(encoding="utf-8")
    mig = MIG_PATH.read_text(encoding="utf-8")
    for pk in ("PRIMARY KEY (source, captured_at, ref_month, brand)",
               "PRIMARY KEY (source, captured_at, ref_month, brand, channel)"):
        assert pk in ddl and pk in mig


def test_equivalencia_checks():
    padrao = re.compile(r"CONSTRAINT (ck_[a-z0-9_]+)")
    a = sorted(set(padrao.findall(DDL_PATH.read_text(encoding="utf-8"))))
    b = sorted(set(padrao.findall(MIG_PATH.read_text(encoding="utf-8"))))
    assert a == b, f"CHECKs divergentes: {set(a) ^ set(b)}"
    assert len(a) >= 30, f"esperados >= 30 CHECKs nomeados, encontrados {len(a)}"


def test_equivalencia_indices():
    padrao = re.compile(r"CREATE INDEX (idx_[a-z0-9_]+)")
    a = sorted(set(padrao.findall(DDL_PATH.read_text(encoding="utf-8"))))
    b = sorted(set(padrao.findall(MIG_PATH.read_text(encoding="utf-8"))))
    assert a == b, f"indices divergentes: {set(a) ^ set(b)}"
    assert len(a) == 6


def test_equivalencia_comentarios_essenciais():
    ddl = DDL_PATH.read_text(encoding="utf-8")
    mig = MIG_PATH.read_text(encoding="utf-8")
    for alvo in ("COMMENT ON TABLE marts.proxy_avoe_brand_monthly_target_snapshot",
                 "COMMENT ON TABLE marts.proxy_avoe_extra_channel_monthly_snapshot",
                 "proxy_avoe_brand_monthly_target_snapshot.captured_at",
                 "proxy_avoe_extra_channel_monthly_snapshot.reported_amount"):
        assert alvo in ddl, f"ausente no DDL: {alvo}"
        assert alvo in mig, f"ausente na migration: {alvo}"


# ===========================================================================
# Importador: dry-run, idempotencia com run ids distintos, rollback
# ===========================================================================

def _fake_execute_values(cur, sql, argslist, page_size=None):
    cur.execute(sql)
    cur.conn.inseridas.append(len(argslist))
    cur.rowcount = len(argslist)
    return None


class _FakeCursor:
    """Devolve DICTS, como RealDictCursor em producao. Nunca tuplas."""

    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self._resultado: list[dict] = []

    def execute(self, sql, params=None):
        self.conn.sqls.append(" ".join(sql.split())[:90])
        self.rowcount = self.conn.rowcount_padrao
        baixo = " ".join(sql.lower().split())
        if "returning sync_run_id" in baixo:
            self._resultado = [{"sync_run_id": 4242}]
            self.rowcount = 1
        elif "select count(*) as n" in baixo:
            self._resultado = [{"n": self.conn.count_para_retornar}]
        elif baixo.startswith("select source"):
            # O fake responde POR TABELA: um dict de existentes por nome de
            # tabela, para nao devolver linha de metas quando se le' canais.
            if isinstance(self.conn.existentes, dict):
                alvo = next((t for t in self.conn.existentes if t in baixo), None)
                self._resultado = self.conn.existentes.get(alvo, []) if alvo else []
            else:
                self._resultado = self.conn.existentes
        else:
            self._resultado = []
        if self.conn.falhar_em and self.conn.falhar_em in baixo:
            raise RuntimeError("falha simulada no destino")

    def fetchone(self):
        return self._resultado[0] if self._resultado else None

    def fetchall(self):
        return list(self._resultado)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, existentes=None, count_para_retornar=0, falhar_em=None,
                 rowcount_padrao=1):
        self.existentes = existentes or []
        self.count_para_retornar = count_para_retornar
        self.falhar_em = falhar_em
        self.rowcount_padrao = rowcount_padrao
        self.sqls: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.inseridas: list[int] = []

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_dry_run_nao_escreve_nada():
    base = _tmp()
    try:
        assert si.main(["--snapshot-dir", str(_snapshot_padrao(base))]) == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_relatorio_declara_zero_escrita_no_dry_run():
    base = _tmp()
    try:
        texto = si.build_report(sc.read_snapshot(_snapshot_padrao(base)), None)
        assert "ESCRITA: nenhuma (sem --apply)" in texto
        assert "NAO e' GMV oficial" in texto
        assert "BRL ASSUMIDA" in texto
        assert "unidade = CAPTURA" in texto
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_idempotencia_ignora_run_id_diferente():
    """FINDING 1 — mesma captura, run id novo, sem reutilizacao artificial."""
    base = _tmp()
    try:
        d = _snapshot_padrao(base)
        primeira = sc.read_snapshot(d, run_id="execucao-A")
        segunda = sc.read_snapshot(d, run_id="execucao-B-diferente")
        assert primeira.stats["run_id"] != segunda.stats["run_id"]

        # O destino guarda exatamente o que a PRIMEIRA execucao gravou (run id A).
        existentes = {
            si.TARGET_TABLE_TARGETS: [
                {c: r[c] for c in si.TARGET_BUSINESS_COLUMNS}
                for r in primeira.target_rows],
            si.TARGET_TABLE_CHANNELS: [
                {c: r[c] for c in si.CHANNEL_BUSINESS_COLUMNS}
                for r in primeira.channel_rows],
        }
        conn = _FakeConn(existentes=existentes)
        resultado = si.publish(conn, segunda, execute_values=_fake_execute_values)
        assert resultado["no_op"] is True
        assert resultado["targets_inserted"] == 0
        assert resultado["channels_inserted"] == 0
        assert conn.inseridas == []
        assert conn.rollbacks == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_conteudo_de_negocio_divergente_na_mesma_captura_e_recusado():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        existente = {c: None for c in si.TARGET_BUSINESS_COLUMNS}
        existente["source"] = "avoe_hub"
        conn = _FakeConn(existentes=[existente])
        try:
            si.publish(conn, r, execute_values=_fake_execute_values)
        except si.SnapshotImportError as exc:
            assert "conteudo de negocio DIVERGENTE" in str(exc)
            assert "append-only" in str(exc)
        else:
            raise AssertionError("conteudo divergente deveria ser recusado")
        assert conn.rollbacks == 1
        assert conn.commits == 0
        assert conn.inseridas == []
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_apply_usa_advisory_lock_proprio_e_timeouts():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        conn = _FakeConn(count_para_retornar=len(r.target_rows))
        try:
            si.publish(conn, r, execute_values=_fake_execute_values)
        except si.SnapshotImportError:
            pass  # divergencia de contagem entre as duas tabelas e' esperada no fake
        assert any("pg_advisory_xact_lock" in s for s in conn.sqls)
        assert any("statement_timeout" in s for s in conn.sqls)
        assert any("lock_timeout" in s for s in conn.sqls)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_advisory_lock_e_distinto_do_pma():
    assert si.ADVISORY_LOCK_KEY == 913_120_041
    pma = Path("pipelines/pma/reference_import.py").read_text(encoding="utf-8")
    assert "913_120_013" in pma


def test_rollback_integral_em_falha():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        conn = _FakeConn(falhar_em="insert into marts.proxy_avoe")
        try:
            si.publish(conn, r, execute_values=_fake_execute_values)
        except Exception:
            pass
        assert conn.rollbacks == 1
        assert conn.commits == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_importador_nao_tem_update_delete_nem_on_conflict_nas_tabelas_de_snapshot():
    codigo = Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8").lower()
    for proibido in ("delete from marts.proxy_avoe", "update marts.proxy_avoe",
                     "truncate", "on conflict"):
        assert proibido not in codigo
    assert "append-only" in codigo


def test_importador_nao_le_credencial_da_avoe():
    """Nenhum termo de credencial ou endpoint da Avoe em ponto algum."""
    for arq in ("pipelines/avoe/snapshot_import.py", "pipelines/avoe/snapshot_contract.py"):
        codigo = Path(arq).read_text(encoding="utf-8")
        for proibido in ("AV_USER", "AV_PASS", "avoehub", "login-usuario",
                         "Authorization", "supabase"):
            assert proibido not in codigo, f"{arq}: {proibido}"


def test_apikey_aparece_somente_na_denylist_do_sanitizador():
    """`apikey` existe no codigo, mas SO' como termo que o sanitizador suprime."""
    arvore = ast.parse(Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8"))
    fora_do_sanitizador = []
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef) and no.name == "_sanitize_erro":
            continue
        if "apikey" in ast.unparse(no):
            fora_do_sanitizador.append(getattr(no, "name", type(no).__name__))
    assert not fora_do_sanitizador, f"apikey fora do sanitizador: {fora_do_sanitizador}"


def test_unica_variavel_de_ambiente_lida_e_database_url():
    """FINDING: nenhuma credencial da Avoe vem do ambiente."""
    lidas: set[str] = set()
    for arq in ("pipelines/avoe/snapshot_import.py", "pipelines/avoe/snapshot_contract.py"):
        arvore = ast.parse(Path(arq).read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            texto = ast.unparse(no.func)
            if texto in ("os.environ.get", "os.getenv") and no.args:
                if isinstance(no.args[0], ast.Constant):
                    lidas.add(no.args[0].value)
            elif texto.startswith("os.environ["):
                lidas.add("<subscript>")
    assert lidas == {"DATABASE_URL"}, f"variaveis lidas: {sorted(lidas)}"


def test_zero_retry_automatico():
    codigo = _codigo_executavel(Path("pipelines/avoe/snapshot_import.py")).lower()
    for proibido in ("retry", "tenacity", "backoff", "sleep"):
        assert proibido not in codigo


# ===========================================================================
# FINDING 9 — auditoria duravel em conexao independente
# ===========================================================================

def test_audit_start_commita_na_conexao_de_auditoria():
    conn = _FakeConn()
    assert si.audit_start(conn, 31) == 4242
    assert conn.commits == 1
    assert any("insert into audit.source_sync_run" in s.lower() for s in conn.sqls)


def test_audit_finish_exige_rowcount_um():
    conn = _FakeConn(rowcount_padrao=0)
    try:
        si.audit_finish(conn, 4242, "success", rows_loaded=31)
    except si.SnapshotImportError as exc:
        assert "afetou 0 linhas" in str(exc)
    else:
        raise AssertionError("rowcount != 1 deveria falhar")
    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_audit_finish_valida_status():
    conn = _FakeConn()
    try:
        si.audit_finish(conn, 4242, "quase", rows_loaded=0)
    except si.SnapshotImportError as exc:
        assert "status de auditoria invalido" in str(exc)
    else:
        raise AssertionError("status invalido deveria falhar")
    assert conn.commits == 0


def test_status_validos_sao_os_do_check_da_tabela():
    assert si.STATUS_VALIDOS == frozenset({"running", "success", "failed"})


def test_commit_indeterminado_nao_e_marcado_failed():
    conn = _FakeConn()
    si.audit_mark_indeterminate(conn, 4242, "excecao no commit dos dados")
    sqls = " ".join(conn.sqls).lower()
    assert "set error_message" in sqls
    assert "status =" not in sqls
    assert conn.commits == 1


def test_tentativa_revertida_deixa_rastro_failed():
    base = _tmp()
    try:
        r = sc.read_snapshot(_snapshot_padrao(base))
        dados = _FakeConn(falhar_em="insert into marts.proxy_avoe")
        auditoria = _FakeConn()
        try:
            si.apply_with_audit(dados, auditoria, r, execute_values=_fake_execute_values)
        except Exception:
            pass
        sqls = " ".join(auditoria.sqls).lower()
        assert "insert into audit.source_sync_run" in sqls
        assert "update audit.source_sync_run" in sqls
        assert auditoria.commits == 2   # running + failed
        assert dados.rollbacks == 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_erro_de_auditoria_e_sanitizado():
    msg = si._sanitize_erro(RuntimeError("falha em postgresql://user:senha@host/db"))
    assert "senha" not in msg
    assert "postgresql://" not in msg
    assert "suprimida" in msg


# ===========================================================================
# Sanitizacao geral
# ===========================================================================

def test_sanitize_path_nao_expoe_caminho():
    p = Path("C:/Users/alguem/segredo/snap/resumo_marca_mes.jsonl")
    assert sc.sanitize_path(p) == "resumo_marca_mes.jsonl"
    assert "Users" not in sc.sanitize_path(p)


def test_sanitize_run_id_remove_caractere_perigoso():
    assert sc.sanitize_run_id("a b/c\\d;e") == "a-b-c-d-e"
    try:
        sc.sanitize_run_id("///")
    except sc.SnapshotContractError:
        pass
    else:
        raise AssertionError("run id vazio deveria falhar")


def test_erro_de_leitura_cita_so_o_nome_do_arquivo():
    base = _tmp()
    try:
        d = base / "s"
        d.mkdir(parents=True)
        _escreve(d / sc.FILE_TARGETS, "{nao json}\n")
        _escreve(d / sc.FILE_CHANNELS, "")
        manifest = {"source_system": "avoe_hub", "status": "OK", "captured_at": CAPTURED,
                    "tables": [
                        {"file": sc.FILE_TARGETS, "row_count": 1,
                         "sha256": _sha("{nao json}\n")},
                        {"file": sc.FILE_CHANNELS, "row_count": 0, "sha256": _sha("")},
                    ]}
        _escreve(d / "MANIFEST.json", json.dumps(manifest))
        h = hashlib.sha256((d / "MANIFEST.json").read_bytes()).hexdigest()
        _escreve(d / "MANIFEST.sha256", f"{h}  MANIFEST.json\n")
        try:
            sc.read_snapshot(d)
        except sc.SnapshotContractError as exc:
            msg = str(exc)
            assert "resumo_marca_mes.jsonl" in msg
            assert "Users" not in msg and str(base) not in msg
        else:
            raise AssertionError("JSON invalido deveria falhar")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_sem_pii_no_ddl_e_no_contrato():
    ddl = DDL_PATH.read_text(encoding="utf-8").lower()
    contrato = Path("pipelines/avoe/snapshot_contract.py").read_text(encoding="utf-8").lower()
    for termo in ("telefone", "endereco_cliente", "buyer_name", "customer_name"):
        assert termo not in ddl
        assert termo not in contrato
