"""Gate AVH-4A / AVH-4A-R — contraprovas do contrato e do importador da Avoe.

Sem fixture e sem `parametrize`, para permitir execucao por runner de stdlib.
Cada bloco cita o FINDING do AVH-4A-R que cobre.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import tokenize
import traceback
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
    """Codigo sem comentario e sem literal de texto: docstring nao e' codigo.

    A partir do 3.12 uma f-string nao e' um unico token STRING — o miolo vem em
    `FSTRING_MIDDLE`. Sem descartar esse tipo tambem, prosa dentro de f-string
    passaria por codigo e faria estes testes casarem com a propria mensagem.
    """
    descartar = {tokenize.COMMENT, tokenize.STRING}
    for nome in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        if hasattr(tokenize, nome):
            descartar.add(getattr(tokenize, nome))
    fonte = caminho.read_text(encoding="utf-8")
    saida = []
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type in descartar:
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
                 rowcount_padrao=1, falhar_no_commit=False,
                 falhar_no_rollback=False, falhar_no_close=False):
        self.existentes = existentes or []
        self.count_para_retornar = count_para_retornar
        self.falhar_em = falhar_em
        self.rowcount_padrao = rowcount_padrao
        self.falhar_no_commit = falhar_no_commit
        self.falhar_no_rollback = falhar_no_rollback
        self.falhar_no_close = falhar_no_close
        self.fechada = False
        self.fechamentos_tentados = 0
        self.sqls: list[str] = []
        self.cursores_pedidos = 0
        self.commits = 0
        self.commits_tentados = 0
        self.rollbacks = 0
        self.rollbacks_tentados = 0
        self.inseridas: list[int] = []

    def cursor(self):
        self.cursores_pedidos += 1
        return _FakeCursor(self)

    def commit(self):
        self.commits_tentados += 1
        if self.falhar_no_commit:
            raise RuntimeError("conexao caiu durante o commit")
        self.commits += 1

    def rollback(self):
        self.rollbacks_tentados += 1
        if self.falhar_no_rollback:
            raise RuntimeError("conexao caiu durante o rollback")
        self.rollbacks += 1

    def close(self):
        self.fechamentos_tentados += 1
        if self.falhar_no_close:
            raise RuntimeError("conexao nao pode ser encerrada")
        self.fechada = True


class _AuditFake(_FakeConn):
    """Conexao de auditoria que registra os status efetivamente escritos."""

    def __init__(self, falhar_no_finish=False, falhar_no_start=False,
                 falhar_no_indeterminate=False, commit_perdido=False, **kw):
        super().__init__(**kw)
        self.falhar_no_finish = falhar_no_finish
        self.falhar_no_start = falhar_no_start
        self.falhar_no_indeterminate = falhar_no_indeterminate
        # `commit_perdido`: o SQL foi aplicado, o commit tambem, e a
        # CONFIRMACAO se perdeu no caminho de volta. O servidor gravou; o
        # cliente nao sabe. Um rollback posterior seria no-op e retornaria.
        self.commit_perdido = commit_perdido
        self.mutacoes_aplicadas: list[str] = []
        self.status_escritos: list[str] = []
        self.indeterminados = 0
        self.indeterminados_tentados = 0

    def commit(self):
        if self.commit_perdido:
            self.commits_tentados += 1
            self.mutacoes_aplicadas.append("commit aplicado no servidor")
            raise RuntimeError("conexao caiu apos o commit; confirmacao perdida")
        return super().commit()

    def cursor(self):
        self.cursores_pedidos += 1
        return _AuditCursor(self)


class _AuditCursor(_FakeCursor):
    def execute(self, sql, params=None):
        b = " ".join(sql.lower().split())
        if "insert into audit.source_sync_run" in b and self.conn.falhar_no_start:
            self.conn.sqls.append("INSERT INTO audit.source_sync_run (FALHOU)")
            raise RuntimeError("auditoria indisponivel no start")
        if "update audit.source_sync_run" in b and "set status" in b:
            if self.conn.falhar_no_finish:
                self.conn.sqls.append("UPDATE audit.source_sync_run SET status (FALHOU)")
                raise RuntimeError("auditoria indisponivel")
            self.conn.status_escritos.append(params[0] if params else "?")
        if "update audit.source_sync_run" in b and "set error_message" in b:
            self.conn.indeterminados_tentados += 1
            if self.conn.falhar_no_indeterminate:
                self.conn.sqls.append(
                    "UPDATE audit.source_sync_run SET error_message (FALHOU)")
                raise RuntimeError("auditoria indisponivel na marcacao")
            self.conn.indeterminados += 1
        super().execute(sql, params)


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


# ===========================================================================
# AVH-4A-H1 — maquina de estados da publicacao
#
# Tres desfechos distintos, cada um com sua contraprova:
#   1. falha ANTES do commit  -> rollback + auditoria `failed`;
#   2. excecao NO commit      -> INDETERMINADO, auditoria fica `running`;
#   3. commit confirmado com auditoria falhando -> AuditoriaIncompleta.
# ===========================================================================

def _resultado_padrao(base: Path):
    return sc.read_snapshot(_snapshot_padrao(base))


def _existentes_de(r):
    """Espelha no destino exatamente o que a leitura produziu (caminho no-op)."""
    return {
        si.TARGET_TABLE_TARGETS: [
            {c: x[c] for c in si.TARGET_BUSINESS_COLUMNS} for x in r.target_rows],
        si.TARGET_TABLE_CHANNELS: [
            {c: x[c] for c in si.CHANNEL_BUSINESS_COLUMNS} for x in r.channel_rows],
    }


# Frases que so' podem ser ditas quando a transacao de DADOS foi observada.
# A palavra "revertido" sozinha nao serve de criterio: ela e' legitima quando
# descreve a conexao de auditoria, que e' outro recurso.
_AFIRMACOES_SOBRE_OS_DADOS = (
    "rollback aplicado", "publicacao revertida", "dados revertidos",
    "nada gravado", "nada foi gravado", "nada foi publicado",
    "dados estao seguros", "dados seguros",
)


def _proibe_afirmacao_sobre_os_dados(msg: str) -> None:
    baixo = msg.lower()
    for frase in _AFIRMACOES_SOBRE_OS_DADOS:
        assert frase not in baixo, f"afirmacao indevida sobre os dados: {frase!r}"


def _revert_so_fala_da_auditoria(msg: str) -> None:
    """Se a mensagem menciona reversao, tem de ser a da conexao de auditoria."""
    baixo = msg.lower()
    assert "rollback" not in baixo, "esta mensagem nao pode falar em rollback"
    inicio = 0
    while True:
        i = baixo.find("revert", inicio)
        if i < 0:
            return
        trecho = baixo[i:i + 90]
        assert "auditoria" in trecho, f"reversao sem dono explicito: {trecho!r}"
        inicio = i + 1


class _CursorContagemPorTabela(_FakeCursor):
    """`SELECT count(*)` responde por tabela, para o caminho de INSERT fechar."""

    def execute(self, sql, params=None):
        baixo = " ".join(sql.lower().split())
        super().execute(sql, params)
        if "select count(*) as n" in baixo:
            alvo = next((t for t in self.conn.contagens if t in baixo), None)
            self._resultado = [{"n": self.conn.contagens.get(alvo, -1)}]


class _ConnComContagem(_FakeConn):
    def __init__(self, contagens, **kw):
        super().__init__(**kw)
        self.contagens = contagens

    def cursor(self):
        return _CursorContagemPorTabela(self)


class _StatsFake:
    """So' o que `rows_extracted_de` consome."""

    def __init__(self, targets_lidos, channels_lidos):
        self.stats = {"targets_lidos": targets_lidos, "channels_lidos": channels_lidos}


def test_h1_estados_declarados():
    assert si.PUBLICACAO_NAO_CONFIRMADA == "nao_confirmada"
    assert si.PUBLICACAO_CONFIRMADA == "confirmada"
    assert si.PUBLICACAO_INDETERMINADA == "indeterminada"
    assert issubclass(si.PublicacaoNaoConfirmada, si.SnapshotImportError)
    assert issubclass(si.PublicacaoIndeterminada, si.SnapshotImportError)
    assert issubclass(si.AuditoriaIncompleta, si.SnapshotImportError)


# --- 1. Falha ANTES de tentar commit ---------------------------------------
def test_h1_falha_pre_commit_faz_rollback_e_auditoria_failed():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(falhar_em="insert into marts.proxy_avoe")
        aud = _AuditFake()
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.PublicacaoNaoConfirmada:
            pass
        else:
            raise AssertionError("deveria ser PublicacaoNaoConfirmada")
        assert dados.rollbacks == 1
        assert dados.commits_tentados == 0, "nao pode ter tentado commit"
        assert aud.status_escritos == ["failed"]
        assert "success" not in aud.status_escritos
        assert aud.indeterminados == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- 2. Excecao no PROPRIO commit ------------------------------------------
def test_h1_excecao_no_commit_e_indeterminada_nunca_failed():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _ConnComContagem(
            {si.TARGET_TABLE_TARGETS: len(r.target_rows),
             si.TARGET_TABLE_CHANNELS: len(r.channel_rows)},
            falhar_no_commit=True)
        aud = _AuditFake()
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.PublicacaoIndeterminada as exc:
            msg = str(exc)
        else:
            raise AssertionError("deveria ser PublicacaoIndeterminada")
        assert dados.inseridas, "o caminho exercitado precisa ser o de INSERT"
        assert dados.commits_tentados == 1
        assert dados.commits == 0
        assert dados.rollbacks == 0, "nao se faz rollback de commit indeterminado"
        assert aud.status_escritos == [], "nunca failed, nunca success"
        assert aud.indeterminados == 1, "audit_mark_indeterminate deve ser chamado"
        assert "nenhum rollback foi tentado" in msg
        assert "nenhum retry" in msg
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- 3. Commit confirmado + falha em audit_finish(success) ------------------
def test_h1_auditoria_incompleta_apos_commit_confirmado():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _ConnComContagem(
            {si.TARGET_TABLE_TARGETS: len(r.target_rows),
             si.TARGET_TABLE_CHANNELS: len(r.channel_rows)})
        aud = _AuditFake(falhar_no_finish=True)
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.AuditoriaIncompleta as exc:
            msg = str(exc)
        else:
            raise AssertionError("deveria ser AuditoriaIncompleta")
        assert dados.commits == 1, "exatamente um commit de dados"
        assert dados.rollbacks == 0, "zero rollback depois de commit confirmado"
        assert aud.status_escritos == [], "nunca chamou audit_finish(failed)"
        assert "dados publicados" in msg and "running" in msg
        assert "NAO repita automaticamente" in msg
        _proibe_afirmacao_sobre_os_dados(msg)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- 4. No-op com excecao no commit ----------------------------------------
def test_h1_no_op_com_excecao_no_commit_tambem_e_indeterminado():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(existentes=_existentes_de(r), falhar_no_commit=True)
        aud = _AuditFake()
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.PublicacaoIndeterminada as exc:
            assert "no-op" in str(exc)
        else:
            raise AssertionError("no-op com commit falho deveria ser indeterminado")
        assert dados.inseridas == [], "no-op nao insere"
        assert dados.rollbacks == 0
        assert aud.status_escritos == []
        assert aud.indeterminados == 1
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- 5. No-op confirmado + falha na auditoria -------------------------------
def test_h1_no_op_confirmado_com_auditoria_incompleta():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(existentes=_existentes_de(r))
        aud = _AuditFake(falhar_no_finish=True)
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.AuditoriaIncompleta as exc:
            msg = str(exc)
        else:
            raise AssertionError("deveria ser AuditoriaIncompleta")
        assert dados.commits == 1
        assert dados.rollbacks == 0
        assert dados.inseridas == []
        assert aud.status_escritos == []
        assert "no-op concluido" in msg
        _proibe_afirmacao_sobre_os_dados(msg)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- 6. Metricas: extraidas = lidas, carregadas = gravadas ------------------
def test_h1_rows_extracted_conta_linhas_lidas_nao_agregados():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        assert si.rows_extracted_de(r) == (r.stats["targets_lidos"]
                                           + r.stats["channels_lidos"])
        assert r.stats["channels_lidos"] > len(r.channel_rows), \
            "a fixture precisa exercitar a agregacao diaria -> mensal"
        assert si.rows_extracted_de(r) != len(r.target_rows) + len(r.channel_rows)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_h1_metricas_do_snapshot_de_referencia():
    """Snapshot 2026-09-01: 19 metas + 4.818 diarias = 4.837 extraidas.

    As 31 saidas (7 metas + 24 canais) sao `rows_loaded`, nao `rows_extracted`.
    O run 285 ja publicado tem 31 nos dois campos e nao sera reescrito; uma
    execucao equivalente hoje registraria 4.837 e 31.
    """
    assert si.rows_extracted_de(_StatsFake(19, 4818)) == 4837
    assert 7 + 24 == 31


def test_h1_no_op_registra_extraidas_e_zero_carregadas():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(existentes=_existentes_de(r))
        aud = _AuditFake()
        aplicado = si.apply_with_audit(dados, aud, r,
                                       execute_values=_fake_execute_values)
        assert aplicado["no_op"] is True
        assert aplicado["targets_inserted"] == 0 and aplicado["channels_inserted"] == 0
        assert aplicado["estado"] == si.PUBLICACAO_CONFIRMADA
        assert aud.status_escritos == ["success"]
        # extraidas foram registradas no audit_start, com as LINHAS LIDAS
        assert any("insert into audit.source_sync_run" in " ".join(s.lower().split())
                   for s in aud.sqls)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- 7. Prova ESTRUTURAL: o helper e' alcancavel pelo fluxo real ------------
def test_h1_mark_indeterminate_e_alcancavel_por_apply_with_audit():
    """Chamar o helper isolado nao e' prova; o fluxo real precisa alcanca-lo."""
    arvore = ast.parse(Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8"))
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef) and n.name == "apply_with_audit"]
    assert len(alvo) == 1
    fonte = ast.unparse(alvo[0])
    assert "audit_mark_indeterminate" in fonte, "o fluxo real precisa chamar o helper"
    assert "PublicacaoIndeterminada" in fonte
    vistos = 0
    for no in ast.walk(alvo[0]):
        if isinstance(no, ast.ExceptHandler) and no.type is not None:
            tipo = ast.unparse(no.type)
            corpo = " ".join(ast.unparse(x) for x in no.body)
            if "PublicacaoIndeterminada" in tipo:
                vistos += 1
                assert "audit_finish" not in corpo, "indeterminado nao finaliza"
                assert "audit_mark_indeterminate" in corpo
    assert vistos == 1


def test_h1_commit_fica_fora_do_bloco_que_faz_rollback():
    arvore = ast.parse(Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8"))
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef) and n.name == "publish"][0]
    for no in ast.walk(alvo):
        if not isinstance(no, ast.Try):
            continue
        if any("rollback" in ast.unparse(h) for h in no.handlers):
            corpo = " ".join(ast.unparse(x) for x in no.body)
            assert "neon_conn.commit" not in corpo, \
                "commit nao pode viver no try que faz rollback"


def test_h1_cli_distingue_os_tres_desfechos():
    """Cada desfecho tem exit code proprio e rotulo proprio."""
    codigos = {k: c for k, c, _ in si.DESFECHOS}
    assert codigos[si.PublicacaoIndeterminada] == 5
    assert codigos[si.AuditoriaIncompleta] == 6
    assert codigos[si.PublicacaoNaoConfirmada] == 4
    rotulos = {k: r for k, _, r in si.DESFECHOS}
    assert rotulos[si.PublicacaoIndeterminada] == "PUBLICACAO INDETERMINADA"
    assert rotulos[si.AuditoriaIncompleta] == "AUDITORIA INCOMPLETA (publicacao confirmada)"


# ===========================================================================
# AVH-4A-H1-R — fechamento exaustivo dos estados de auditoria
#
# A auditoria e' um eixo independente do estado dos dados. Ela pode falhar em
# quatro momentos, e nenhuma dessas falhas pode terminar numa mensagem que
# afirme mais do que o processo sabe.
# ===========================================================================

def _conn_de_insercao(r):
    """Conexao de dados que faz o caminho completo de INSERT fechar."""
    return _ConnComContagem({si.TARGET_TABLE_TARGETS: len(r.target_rows),
                             si.TARGET_TABLE_CHANNELS: len(r.channel_rows)})


# --- FINDING 2: audit_start falhou -----------------------------------------
def test_hr_audit_start_falho_nao_tenta_publicar():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn()
        aud = _AuditFake(falhar_no_start=True)
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.AuditoriaInicialIncompleta as exc:
            msg = str(exc)
            erro_capturado = exc
        else:
            raise AssertionError("deveria ser AuditoriaInicialIncompleta")
        # publish() nunca foi chamado: nem cursor a conexao de dados abriu.
        assert dados.cursores_pedidos == 0, "publish() nao pode ter sido chamado"
        assert dados.sqls == []
        assert dados.inseridas == []
        assert dados.commits_tentados == 0 and dados.commits == 0
        assert dados.rollbacks_tentados == 0 and dados.rollbacks == 0
        assert aud.status_escritos == [] and aud.indeterminados == 0
        # A mensagem nao pode sugerir reversao dos DADOS. A unica reversao
        # mencionavel e' a da propria conexao de auditoria.
        _revert_so_fala_da_auditoria(msg)
        assert "PUBLICACAO NAO FOI TENTADA" in msg
        assert "Nenhum retry" in msg
        # Rollback confirmado na auditoria: da' para afirmar que a linha nao
        # foi criada.
        assert erro_capturado.resultado_auditoria.estado == si.AUDIT_REVERTIDA
        assert "nenhuma linha de run foi criada" in msg
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_hr_audit_start_falho_nao_e_confundido_com_os_outros_desfechos():
    assert not issubclass(si.AuditoriaInicialIncompleta, si.PublicacaoNaoConfirmada)
    assert not issubclass(si.AuditoriaInicialIncompleta, si.PublicacaoIndeterminada)
    assert not issubclass(si.AuditoriaInicialIncompleta, si.AuditoriaIncompleta)
    assert si._despacha_desfecho(si.AuditoriaInicialIncompleta("x"))[0] == 7


# --- FINDING 1: commit indeterminado + marcacao falhou ----------------------
def test_hr_indeterminado_com_marcacao_falha_continua_indeterminado():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _ConnComContagem(
            {si.TARGET_TABLE_TARGETS: len(r.target_rows),
             si.TARGET_TABLE_CHANNELS: len(r.channel_rows)},
            falhar_no_commit=True)
        aud = _AuditFake(falhar_no_indeterminate=True)
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.PublicacaoIndeterminadaAuditoriaNaoConfirmada as exc:
            msg = str(exc)
            capturada = exc
        else:
            raise AssertionError("deveria ser PublicacaoIndeterminadaAuditoriaNaoConfirmada")
        # O desfecho EXTERNO continua sendo indeterminado.
        assert isinstance(capturada, si.PublicacaoIndeterminada)
        assert not isinstance(capturada, si.PublicacaoNaoConfirmada)
        # Nunca failed, nunca success; a marcacao foi TENTADA e nao confirmada.
        assert aud.status_escritos == []
        assert aud.indeterminados_tentados == 1
        assert aud.indeterminados == 0
        # Zero rollback na conexao de dados.
        assert dados.rollbacks_tentados == 0 and dados.rollbacks == 0
        assert dados.commits == 0 and dados.commits_tentados == 1
        # A mensagem preserva as DUAS causas e manda reconciliar sem retry.
        assert "levantou excecao" in msg, "causa 1: o commit"
        assert "TAMBEM falhou" in msg, "causa 2: a marcacao da auditoria"
        assert "reconcilie" in msg.lower()
        assert "nao repita a importacao automaticamente" in msg.lower()
        assert "rollback aplicado" not in msg
        # Finding 3 do H1-R2: nenhuma cadeia crua sobrevive.
        assert capturada.__cause__ is None
        assert capturada.__context__ is None
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_hr_indeterminado_com_marcacao_falha_nao_vaza_segredo():
    """A segunda causa passa pelo mesmo sanitizador da primeira."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)

        class _AuditVazando(_AuditFake):
            def cursor(self):
                self.cursores_pedidos += 1
                return _CursorVazando(self)

        class _CursorVazando(_AuditCursor):
            def execute(self, sql, params=None):
                b = " ".join(sql.lower().split())
                if "set error_message" in b:
                    self.conn.indeterminados_tentados += 1
                    # DSN sintetico montado em partes, para nao parecer
                    # credencial de verdade para um scanner de segredos.
                    dsn = "postgres" + "://u:" + "senha" + "@h:5432/d"
                    raise RuntimeError(f"FATAL: {dsn} recusou a conexao")
                super().execute(sql, params)

        dados = _ConnComContagem(
            {si.TARGET_TABLE_TARGETS: len(r.target_rows),
             si.TARGET_TABLE_CHANNELS: len(r.channel_rows)},
            falhar_no_commit=True)
        try:
            si.apply_with_audit(dados, _AuditVazando(), r,
                                execute_values=_fake_execute_values)
        except si.PublicacaoIndeterminadaAuditoriaNaoConfirmada as exc:
            msg = str(exc)
        else:
            raise AssertionError("deveria ser indeterminada com auditoria nao confirmada")
        assert "postgres://" not in msg
        assert "senha" not in msg
        assert "suprimida por conter segredo" in msg
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- Falha pre-commit + falha ao gravar `failed` ---------------------------
def test_hr_pre_commit_com_auditoria_failed_falha():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(falhar_em="insert into marts.proxy_avoe")
        aud = _AuditFake(falhar_no_finish=True)
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.AuditoriaIncompletaSemPublicacao as exc:
            msg = str(exc)
            capturada = exc
        else:
            raise AssertionError("deveria ser AuditoriaIncompletaSemPublicacao")
        # Rollback de dados CONFIRMADO.
        assert dados.rollbacks == 1 and dados.rollbacks_tentados == 1
        assert capturada.rollback_confirmado is True
        assert dados.commits_tentados == 0
        # Continua sendo "nada publicado", nao commit indeterminado.
        assert isinstance(capturada, si.PublicacaoNaoConfirmada)
        assert not isinstance(capturada, si.PublicacaoIndeterminada)
        assert aud.status_escritos == [], "o failed nao chegou a ser gravado"
        assert aud.indeterminados == 0, "isto NAO e' commit indeterminado"
        # A mensagem separa a transacao de dados (revertida e confirmada) da
        # auditoria (incompleta), sem misturar as duas.
        assert "transacao de dados foi REVERTIDA e confirmada" in msg
        assert "commit nunca foi tentado" in msg
        assert "NAO e' commit indeterminado" in msg
        assert "permanece 'running'" in msg, "rollback da auditoria confirmado"
        assert capturada.resultado_auditoria.estado == si.AUDIT_REVERTIDA
        assert si._despacha_desfecho(capturada)[0] == 8
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_hr_rollback_que_levanta_vira_estado_proprio():
    """H1-R2 finding 1 — nem `PublicacaoNaoConfirmada`, nem indeterminado."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(falhar_em="insert into marts.proxy_avoe",
                          falhar_no_rollback=True)
        try:
            si.publish(dados, r, execute_values=_fake_execute_values)
        except si.ReversaoNaoConfirmada as exc:
            msg = str(exc)
            capturada = exc
        else:
            raise AssertionError("deveria ser ReversaoNaoConfirmada")
        assert not isinstance(capturada, si.PublicacaoNaoConfirmada)
        assert not isinstance(capturada, si.PublicacaoIndeterminada)
        assert dados.rollbacks_tentados == 1 and dados.rollbacks == 0
        assert dados.commits_tentados == 0, "o commit nunca foi tentado"
        # A conexao foi encerrada para forcar o fim da transacao.
        assert capturada.conexao_encerrada is True
        assert dados.fechada is True
        # E o que a mensagem NAO pode dizer.
        _proibe_afirmacao_sobre_os_dados(msg)
        assert "REVERSAO NAO FOI CONFIRMADA" in msg
        assert "commit NUNCA foi tentado" in msg
        assert "nao foi observado" in msg
        assert "Reconcilie em leitura" in msg
        assert "Nenhum retry" in msg
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_hr_reversao_nao_confirmada_com_close_que_tambem_levanta():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(falhar_em="insert into marts.proxy_avoe",
                          falhar_no_rollback=True, falhar_no_close=True)
        try:
            si.publish(dados, r, execute_values=_fake_execute_values)
        except si.ReversaoNaoConfirmada as exc:
            assert exc.conexao_encerrada is False
            assert "nem o fim da transacao foi observado" in str(exc)
        else:
            raise AssertionError("deveria ser ReversaoNaoConfirmada")
        assert dados.fechada is False
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_hr_reversao_nao_confirmada_nunca_marca_failed():
    """`failed` diria que a transacao terminou sem gravar; nao se observou."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(falhar_em="insert into marts.proxy_avoe",
                          falhar_no_rollback=True)
        aud = _AuditFake()
        try:
            si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        except si.ReversaoNaoConfirmada as exc:
            msg = str(exc)
            capturada = exc
        else:
            raise AssertionError("deveria ser ReversaoNaoConfirmada")
        assert aud.status_escritos == [], "nunca failed, nunca success"
        assert aud.indeterminados == 1, "so' a nota de indeterminacao"
        assert capturada.resultado_auditoria.estado == si.AUDIT_CONFIRMADA
        assert "permanece 'running'" in msg
        assert si._despacha_desfecho(capturada)[0] == 11
        _proibe_afirmacao_sobre_os_dados(msg)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_hr_rollback_confirmado_diz_rollback_aplicado():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(falhar_em="insert into marts.proxy_avoe")
        try:
            si.publish(dados, r, execute_values=_fake_execute_values)
        except si.PublicacaoNaoConfirmada as exc:
            assert exc.rollback_confirmado is True
            assert "rollback aplicado" in str(exc)
        else:
            raise AssertionError("deveria ser PublicacaoNaoConfirmada")
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- FINDING 3: varredura de TODAS as saidas da CLI ------------------------
def test_hr_tabela_de_desfechos_e_completa_e_ordenada():
    """Subclasses antes das bases, exit codes unicos, um por estado."""
    classes = [k for k, _, _ in si.DESFECHOS]
    codigos = [c for _, c, _ in si.DESFECHOS]
    assert len(set(codigos)) == len(codigos), "exit codes precisam ser unicos"
    assert set(codigos) == {4, 5, 6, 7, 8, 9, 11}
    esperadas = {
        si.AuditoriaInicialIncompleta, si.PublicacaoNaoConfirmada,
        si.AuditoriaIncompletaSemPublicacao, si.PublicacaoIndeterminada,
        si.PublicacaoIndeterminadaAuditoriaNaoConfirmada, si.AuditoriaIncompleta,
        si.ReversaoNaoConfirmada,
    }
    assert set(classes) == esperadas
    for i, filha in enumerate(classes):
        for j, mae in enumerate(classes):
            if filha is not mae and issubclass(filha, mae):
                assert i < j, f"{filha.__name__} precisa vir antes de {mae.__name__}"


def test_hr_despacho_por_isinstance_bate_com_cada_estado():
    casos = {
        si.AuditoriaInicialIncompleta: 7,
        si.PublicacaoNaoConfirmada: 4,
        si.AuditoriaIncompletaSemPublicacao: 8,
        si.ReversaoNaoConfirmada: 11,
        si.PublicacaoIndeterminada: 5,
        si.PublicacaoIndeterminadaAuditoriaNaoConfirmada: 9,
        si.AuditoriaIncompleta: 6,
    }
    for classe, codigo in casos.items():
        assert si._despacha_desfecho(classe("x"))[0] == codigo, classe.__name__
    # Qualquer coisa fora da maquina de estados cai no nao-classificado.
    assert si._despacha_desfecho(RuntimeError("x")) == si.DESFECHO_NAO_CLASSIFICADO
    assert si.DESFECHO_NAO_CLASSIFICADO[0] == 10


def test_hr_nenhum_rotulo_da_cli_afirma_rollback():
    """So' `publish()` pode dizer 'rollback aplicado', e so' quando executou."""
    for _, _, rotulo in si.DESFECHOS:
        baixo = rotulo.lower()
        assert "rollback" not in baixo, rotulo
        assert "revert" not in baixo, rotulo
    assert "rollback" not in si.DESFECHO_NAO_CLASSIFICADO[1].lower()
    assert "revert" not in si.DESFECHO_NAO_CLASSIFICADO[1].lower()


def test_hr_a_frase_rollback_aplicado_so_existe_no_ramo_que_a_comprova():
    """Prova estrutural: a frase vive so' dentro do guard de rollback ok."""
    fonte = Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    donos = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.FunctionDef) and "rollback aplicado" in ast.unparse(no):
            donos.append(no.name)
    assert donos == ["publish"], f"a frase vazou para: {donos}"
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef) and n.name == "publish"][0]
    # O `try` cujo corpo chama `neon_conn.rollback()`. O handler dele e' o ramo
    # em que a reversao NAO foi confirmada, e ali a frase e' proibida — o
    # handler termina levantando `ReversaoNaoConfirmada`, entao o codigo que
    # usa a frase e' inalcancavel quando o rollback falha.
    guardas = [n for n in ast.walk(alvo)
               if isinstance(n, ast.Try)
               and "neon_conn.rollback()" in ast.unparse(n.body)]
    assert len(guardas) == 1, "o rollback precisa de exatamente um guard"
    guarda = guardas[0]
    assert "rollback aplicado" not in ast.unparse(guarda)
    for h in guarda.handlers:
        corpo = ast.unparse(h)
        assert "rollback aplicado" not in corpo, \
            "a frase nao pode estar no ramo em que o rollback falhou"
        assert "ReversaoNaoConfirmada" in corpo, \
            "o ramo de rollback falho tem de sair pelo estado proprio"
        assert "_levanta(" in corpo, "e tem de sair levantando, nao caindo adiante"


def test_hr_main_nao_tem_handler_generico_mentiroso():
    fonte = Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    principal = [n for n in ast.walk(arvore)
                 if isinstance(n, ast.FunctionDef) and n.name == "main"][0]
    # Nenhum handler de main, em lugar nenhum, pode afirmar reversao.
    for no in ast.walk(principal):
        if isinstance(no, ast.ExceptHandler):
            corpo = ast.unparse(no.body)
            assert "rollback" not in corpo.lower()
            assert "revert" not in corpo.lower()
    # E o generico do `try` que chama apply_with_audit precisa se declarar
    # nao-classificado, em vez de escolher um estado por conta propria.
    blocos = [n for n in ast.walk(principal)
              if isinstance(n, ast.Try) and "apply_with_audit" in ast.unparse(n.body)]
    assert len(blocos) == 1
    # O despacho tem de vir da tabela, nao de handlers escritos a mao.
    tipados = [h for h in blocos[0].handlers
               if h.type is not None and ast.unparse(h.type) == "SnapshotImportError"]
    assert len(tipados) == 1
    assert "_despacha_desfecho" in ast.unparse(tipados[0].body)
    genericos = [h for h in blocos[0].handlers
                 if h.type is not None and ast.unparse(h.type) == "Exception"]
    assert len(genericos) == 1
    corpo = ast.unparse(genericos[0].body)
    assert "DESFECHO_NAO_CLASSIFICADO" in corpo, \
        "o handler generico precisa declarar ignorancia, nao rollback"
    assert "return 4" not in corpo and "nada gravado" not in corpo


# --- Prova estrutural: nenhuma falha de auditoria escapa --------------------
def test_hr_falha_de_mark_indeterminate_nao_escapa_para_o_handler_generico():
    """O `try` em volta da marcacao precisa existir DENTRO do handler."""
    arvore = ast.parse(Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8"))
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef) and n.name == "apply_with_audit"][0]
    handlers = [h for h in ast.walk(alvo)
                if isinstance(h, ast.ExceptHandler) and h.type is not None
                and ast.unparse(h.type) == "PublicacaoIndeterminada"]
    assert len(handlers) == 1
    corpo = handlers[0].body
    # A chamada nao pode ser uma expressao solta: precisa estar protegida.
    tentativas = [n for n in corpo if isinstance(n, ast.Try)]
    assert tentativas, "audit_mark_indeterminate precisa estar dentro de um try"
    protegido = ast.unparse(tentativas[0])
    assert "audit_mark_indeterminate" in protegido
    assert "PublicacaoIndeterminadaAuditoriaNaoConfirmada" in protegido
    solta = [n for n in corpo
             if isinstance(n, ast.Expr) and "audit_mark_indeterminate" in ast.unparse(n)]
    assert not solta, "chamada desprotegida de audit_mark_indeterminate"


def test_hr_audit_start_e_audit_finish_failed_tambem_estao_protegidos():
    arvore = ast.parse(Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8"))
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef) and n.name == "apply_with_audit"][0]
    # audit_start dentro de um try que levanta AuditoriaInicialIncompleta.
    protegidos = [ast.unparse(n) for n in ast.walk(alvo) if isinstance(n, ast.Try)]
    assert any("audit_start" in t and "AuditoriaInicialIncompleta" in t
               for t in protegidos), "audit_start precisa estar protegido"
    assert any("audit_finish" in t and "AuditoriaIncompletaSemPublicacao" in t
               for t in protegidos), "audit_finish(failed) precisa estar protegido"
    assert any("audit_finish" in t and "AuditoriaIncompleta(" in t
               for t in protegidos), "audit_finish(success) precisa estar protegido"
    # Nenhuma chamada de auditoria solta no corpo da funcao.
    for no in alvo.body:
        if isinstance(no, ast.Expr):
            texto = ast.unparse(no)
            assert "audit_" not in texto, f"chamada de auditoria desprotegida: {texto}"


def test_hr_apply_with_audit_so_levanta_excecoes_tipadas():
    """Todo `raise` explicito da funcao e' da maquina de estados (ou re-raise)."""
    arvore = ast.parse(Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8"))
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef) and n.name == "apply_with_audit"][0]
    tipadas = {"AuditoriaInicialIncompleta", "AuditoriaIncompletaSemPublicacao",
               "PublicacaoIndeterminadaAuditoriaNaoConfirmada", "AuditoriaIncompleta",
               "ReversaoNaoConfirmada", "_enriquece_reversao"}
    # Nomes locais ligados a um construtor tipado tambem valem (`erro = X(...)`).
    for no in ast.walk(alvo):
        if isinstance(no, ast.Assign) and isinstance(no.value, ast.Call):
            classe = ast.unparse(no.value.func)
            if classe in tipadas:
                tipadas.update(ast.unparse(t) for t in no.targets)
    # Nada de `raise X(...)` direto: tudo sai por `_levanta`, que zera a cadeia.
    for no in ast.walk(alvo):
        if isinstance(no, ast.Raise) and no.exc is not None:
            raise AssertionError(f"raise direto (vaza cadeia): {ast.unparse(no)}")
    vistos = set()
    for no in ast.walk(alvo):
        if not isinstance(no, ast.Call) or ast.unparse(no.func) != "_levanta":
            continue
        arg = ast.unparse(no.args[0]).split("(")[0].strip()
        assert arg in tipadas, f"_levanta com argumento nao tipado: {arg}"
        vistos.add(arg)
    # Um `_levanta` por desfecho: os quatro construidos aqui mais o de reversao.
    assert vistos == {"erro", "_enriquece_reversao"}, vistos
    construidas = {ast.unparse(n.value.func) for n in ast.walk(alvo)
                   if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                   and ast.unparse(n.value.func) in tipadas}
    assert construidas == {
        "AuditoriaInicialIncompleta", "PublicacaoIndeterminadaAuditoriaNaoConfirmada",
        "AuditoriaIncompletaSemPublicacao", "AuditoriaIncompleta"}, construidas


# --- Matriz completa, exercitada de ponta a ponta --------------------------
def test_hr_matriz_completa_dos_sete_estados():
    """Os sete estados da matriz, cada um com seu exit code e sua evidencia."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        existentes = _existentes_de(r)
        vistos = {}

        # 1. audit_start falhou -> publicacao nao tentada
        d, a = _FakeConn(), _AuditFake(falhar_no_start=True)
        try:
            si.apply_with_audit(d, a, r, execute_values=_fake_execute_values)
        except Exception as e:
            vistos["audit_start"] = (si._despacha_desfecho(e)[0], d.rollbacks,
                                     a.status_escritos)
        # 2. falha pre-commit -> rollback + failed
        d, a = _FakeConn(falhar_em="insert into marts.proxy_avoe"), _AuditFake()
        try:
            si.apply_with_audit(d, a, r, execute_values=_fake_execute_values)
        except Exception as e:
            vistos["pre_commit"] = (si._despacha_desfecho(e)[0], d.rollbacks,
                                    a.status_escritos)
        # 3. falha pre-commit + failed nao gravou
        d = _FakeConn(falhar_em="insert into marts.proxy_avoe")
        a = _AuditFake(falhar_no_finish=True)
        try:
            si.apply_with_audit(d, a, r, execute_values=_fake_execute_values)
        except Exception as e:
            vistos["pre_commit_sem_auditoria"] = (si._despacha_desfecho(e)[0],
                                                  d.rollbacks, a.status_escritos)
        # 4. commit indeterminado + marcacao ok
        d = _ConnComContagem({si.TARGET_TABLE_TARGETS: len(r.target_rows),
                              si.TARGET_TABLE_CHANNELS: len(r.channel_rows)},
                             falhar_no_commit=True)
        a = _AuditFake()
        try:
            si.apply_with_audit(d, a, r, execute_values=_fake_execute_values)
        except Exception as e:
            vistos["indeterminado"] = (si._despacha_desfecho(e)[0], d.rollbacks,
                                       a.status_escritos)
        # 5. commit indeterminado + marcacao falhou
        d = _ConnComContagem({si.TARGET_TABLE_TARGETS: len(r.target_rows),
                              si.TARGET_TABLE_CHANNELS: len(r.channel_rows)},
                             falhar_no_commit=True)
        a = _AuditFake(falhar_no_indeterminate=True)
        try:
            si.apply_with_audit(d, a, r, execute_values=_fake_execute_values)
        except Exception as e:
            vistos["indeterminado_sem_auditoria"] = (si._despacha_desfecho(e)[0],
                                                     d.rollbacks, a.status_escritos)
        # 6. commit confirmado + success gravado
        d, a = _FakeConn(existentes=existentes), _AuditFake()
        si.apply_with_audit(d, a, r, execute_values=_fake_execute_values)
        vistos["sucesso"] = (0, d.rollbacks, a.status_escritos)
        # 7. commit confirmado + success falhou
        d, a = _FakeConn(existentes=existentes), _AuditFake(falhar_no_finish=True)
        try:
            si.apply_with_audit(d, a, r, execute_values=_fake_execute_values)
        except Exception as e:
            vistos["auditoria_incompleta"] = (si._despacha_desfecho(e)[0],
                                              d.rollbacks, a.status_escritos)

        assert vistos == {
            "audit_start":                 (7, 0, []),
            "pre_commit":                  (4, 1, ["failed"]),
            "pre_commit_sem_auditoria":    (8, 1, []),
            "indeterminado":               (5, 0, []),
            "indeterminado_sem_auditoria": (9, 0, []),
            "sucesso":                     (0, 0, ["success"]),
            "auditoria_incompleta":        (6, 0, []),
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_hr_nenhum_estado_grava_failed_depois_do_commit():
    """Invariante transversal: `failed` so' existe no ramo pre-commit."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        for dados, aud in (
            (_ConnComContagem({si.TARGET_TABLE_TARGETS: len(r.target_rows),
                               si.TARGET_TABLE_CHANNELS: len(r.channel_rows)},
                              falhar_no_commit=True), _AuditFake()),
            (_ConnComContagem({si.TARGET_TABLE_TARGETS: len(r.target_rows),
                               si.TARGET_TABLE_CHANNELS: len(r.channel_rows)},
                              falhar_no_commit=True),
             _AuditFake(falhar_no_indeterminate=True)),
            (_FakeConn(existentes=_existentes_de(r)), _AuditFake(falhar_no_finish=True)),
        ):
            try:
                si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
            except si.SnapshotImportError:
                pass
            assert "failed" not in aud.status_escritos
            assert dados.rollbacks == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ===========================================================================
# AVH-4A-H1-R2 — fechamento conservador
#
# Tres eixos novos:
#   finding 2  cada mutacao de auditoria tem quatro resultados possiveis;
#   finding 3  nenhuma excecao crua de driver sobrevive em lugar nenhum;
#   finding 4  spies diretos com contagem exata, em vez de proxies estruturais.
# ===========================================================================

# Marcadores unicos do erro de driver injetado. Sao especificos de proposito:
# termos genericos como "INSERT INTO" apareceriam nas linhas de codigo DESTE
# arquivo de teste dentro do traceback, e o que esta sob teste e' o produto.
_SEGREDOS = ("postgres://", "postgresql://", "s3nh4-secreta", "usuario_tecnico",
             "10.0.0.7", "sslmode=require", "params=(", "eyJ")


def _traceback_do_produto(exc: BaseException) -> str:
    """Traceback formatado, sem os quadros deste arquivo de teste."""
    quadros = [q for q in traceback.extract_tb(exc.__traceback__)
               if "test_avoe_snapshot_import" not in q.filename]
    return "".join(traceback.format_list(quadros))


def _texto_de_um_erro(exc: BaseException) -> str:
    """Tudo o que um operador consegue extrair do objeto de excecao."""
    partes = [str(exc), repr(exc), _traceback_do_produto(exc)]
    atual = exc
    for _ in range(10):
        proximo = atual.__cause__ or atual.__context__
        if proximo is None:
            break
        partes.append(str(proximo))
        partes.append(repr(proximo))
        partes.append(_traceback_do_produto(proximo))
        atual = proximo
    return "\n".join(partes)


def _sem_segredo(texto: str, rotulo: str) -> None:
    baixo = texto.lower()
    for s in _SEGREDOS:
        assert s.lower() not in baixo, f"{rotulo} vazou {s!r}"


class _ExcecaoDeDriver(RuntimeError):
    """Imita psycopg2: DSN, host, usuario, parametros e SQL no proprio texto."""

    def __init__(self, onde: str):
        dsn = "postgres" + "://usuario_tecnico:" + "s3nh4-secreta" + "@10.0.0.7:5432/db"
        super().__init__(
            f"FATAL em {onde}: {dsn}?sslmode=require recusou; "
            f"SQL: INSERT INTO marts.proxy_avoe_brand_monthly_target_snapshot "
            f"VALUES (...); params=('avoe_hub', 's3nh4-secreta')")


# ---------------------------------------------------------------------------
# FINDING 3 — vazamento por str / repr / traceback / stderr / logger / cadeia
# ---------------------------------------------------------------------------

def _erro_de(fabrica):
    """Roda `fabrica()` e devolve a excecao levantada."""
    try:
        fabrica()
    except BaseException as exc:  # noqa: BLE001 — e' exatamente o objeto sob teste
        return exc
    raise AssertionError("nada foi levantado")


def test_r2_vazamento_em_todas_as_superficies_de_cada_estado():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        existentes = _existentes_de(r)
        contagens = {si.TARGET_TABLE_TARGETS: len(r.target_rows),
                     si.TARGET_TABLE_CHANNELS: len(r.channel_rows)}
        cenarios = {
            "audit_start": lambda: si.apply_with_audit(
                _FakeConn(), _AuditFake(falhar_no_start=True), r,
                execute_values=_fake_execute_values),
            "pre_commit": lambda: si.apply_with_audit(
                _FakeConn(falhar_em="insert into marts.proxy_avoe"),
                _AuditFake(), r, execute_values=_fake_execute_values),
            "pre_commit_sem_auditoria": lambda: si.apply_with_audit(
                _FakeConn(falhar_em="insert into marts.proxy_avoe"),
                _AuditFake(falhar_no_finish=True), r,
                execute_values=_fake_execute_values),
            "reversao": lambda: si.apply_with_audit(
                _FakeConn(falhar_em="insert into marts.proxy_avoe",
                          falhar_no_rollback=True),
                _AuditFake(), r, execute_values=_fake_execute_values),
            "indeterminado": lambda: si.apply_with_audit(
                _ConnComContagem(contagens, falhar_no_commit=True),
                _AuditFake(), r, execute_values=_fake_execute_values),
            "indeterminado_sem_auditoria": lambda: si.apply_with_audit(
                _ConnComContagem(contagens, falhar_no_commit=True),
                _AuditFake(falhar_no_indeterminate=True), r,
                execute_values=_fake_execute_values),
            "auditoria_incompleta": lambda: si.apply_with_audit(
                _FakeConn(existentes=existentes),
                _AuditFake(falhar_no_finish=True), r,
                execute_values=_fake_execute_values),
        }
        for nome, fabrica in cenarios.items():
            exc = _erro_de(fabrica)
            assert isinstance(exc, si.SnapshotImportError), nome
            assert exc.__cause__ is None, f"{nome}: __cause__ preservado"
            assert exc.__context__ is None, f"{nome}: __context__ preservado"
            assert exc.__suppress_context__ is True, nome
            _sem_segredo(_texto_de_um_erro(exc), nome)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_excecao_realista_de_driver_nao_vaza_por_nenhuma_superficie():
    """Injeta um erro com DSN, host, usuario, params e SQL no texto."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)

        class _ConnDriver(_FakeConn):
            def commit(self):
                self.commits_tentados += 1
                raise _ExcecaoDeDriver("commit")

        class _AuditDriver(_AuditFake):
            def cursor(self):
                self.cursores_pedidos += 1
                return _CursorDriver(self)

        class _CursorDriver(_AuditCursor):
            def execute(self, sql, params=None):
                b = " ".join(sql.lower().split())
                if "set error_message" in b:
                    self.conn.indeterminados_tentados += 1
                    raise _ExcecaoDeDriver("mark_indeterminate")
                super().execute(sql, params)

        dados = _ConnDriver(existentes=_existentes_de(r))
        exc = _erro_de(lambda: si.apply_with_audit(
            dados, _AuditDriver(), r, execute_values=_fake_execute_values))
        assert isinstance(exc, si.PublicacaoIndeterminadaAuditoriaNaoConfirmada)
        _sem_segredo(_texto_de_um_erro(exc), "cadeia completa")
        assert "suprimida por conter segredo" in str(exc)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_stderr_real_da_cli_nao_vaza_e_devolve_o_exit_code():
    """Executa `main(--apply)` de ponta a ponta, com conexoes falsas."""
    base = _tmp()
    d = _snapshot_padrao(base)
    r = sc.read_snapshot(d)
    contagens = {si.TARGET_TABLE_TARGETS: len(r.target_rows),
                 si.TARGET_TABLE_CHANNELS: len(r.channel_rows)}

    casos = [
        (7, lambda: (_FakeConn(), _AuditFake(falhar_no_start=True))),
        (4, lambda: (_FakeConn(falhar_em="insert into marts.proxy_avoe"),
                     _AuditFake())),
        (8, lambda: (_FakeConn(falhar_em="insert into marts.proxy_avoe"),
                     _AuditFake(falhar_no_finish=True))),
        (11, lambda: (_FakeConn(falhar_em="insert into marts.proxy_avoe",
                                falhar_no_rollback=True), _AuditFake())),
        (5, lambda: (_ConnComContagem(contagens, falhar_no_commit=True),
                     _AuditFake())),
        (9, lambda: (_ConnComContagem(contagens, falhar_no_commit=True),
                     _AuditFake(falhar_no_indeterminate=True))),
        (6, lambda: (_FakeConn(existentes=_existentes_de(r)),
                     _AuditFake(falhar_no_finish=True))),
        (0, lambda: (_FakeConn(existentes=_existentes_de(r)), _AuditFake())),
    ]

    publish_real = si.publish
    conectar_real = si._neon_writable
    url_antes = os.environ.get("DATABASE_URL")
    stderr_antes = sys.stderr
    try:
        # DSN falso: `_neon_writable` esta trocado, entao psycopg2 nem e' tocado.
        os.environ["DATABASE_URL"] = "dsn-de-teste-nao-usado"
        for esperado, fabrica in casos:
            conexoes = list(fabrica())
            si._neon_writable = lambda _url, _c=conexoes: _c.pop(0)
            si.publish = lambda conn, res, execute_values=None: publish_real(
                conn, res, execute_values=_fake_execute_values)
            capturado = io.StringIO()
            sys.stderr = capturado
            try:
                codigo = si.main(["--snapshot-dir", str(d), "--apply"])
            finally:
                sys.stderr = stderr_antes
            texto = capturado.getvalue()
            assert codigo == esperado, f"esperava {esperado}, veio {codigo}: {texto}"
            _sem_segredo(texto, f"stderr do exit {esperado}")
            if esperado == 0:
                assert texto == "", "sucesso nao escreve em stderr"
                continue
            rotulo = texto.split(":", 1)[0]
            assert "rollback" not in rotulo.lower(), rotulo
            assert "revert" not in rotulo.lower() or esperado == 11, rotulo
            # So' o exit 4 pode dizer "nada gravado" no rotulo.
            if esperado != 4:
                assert "nada gravado" not in rotulo.lower(), rotulo
            # "rollback aplicado" so' nos dois estados com reversao CONFIRMADA.
            if esperado not in (4, 8):
                assert "rollback aplicado" not in texto, rotulo
    finally:
        si.publish = publish_real
        si._neon_writable = conectar_real
        if url_antes is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_antes
        sys.stderr = stderr_antes
        shutil.rmtree(base, ignore_errors=True)


def test_r2_o_importador_nao_emite_log():
    """Nao ha logger: nada a vazar por ele, e o teste trava essa escolha."""
    fonte = Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8")
    assert "import logging" not in fonte
    assert "logging.getLogger" not in fonte
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        registros = []

        class _Coletor(logging.Handler):
            def emit(self, record):
                registros.append(record)

        coletor = _Coletor()
        raiz = logging.getLogger()
        raiz.addHandler(coletor)
        nivel = raiz.level
        raiz.setLevel(logging.DEBUG)
        try:
            _erro_de(lambda: si.apply_with_audit(
                _FakeConn(falhar_em="insert into marts.proxy_avoe"),
                _AuditFake(falhar_no_finish=True), r,
                execute_values=_fake_execute_values))
        finally:
            raiz.removeHandler(coletor)
            raiz.setLevel(nivel)
        assert registros == [], f"o importador emitiu log: {registros}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_levanta_zera_cadeia_mesmo_dentro_de_except():
    original = _ExcecaoDeDriver("teste")
    try:
        try:
            raise original
        except RuntimeError:
            si._levanta(si.SnapshotImportError("mensagem limpa"))
    except si.SnapshotImportError as exc:
        assert exc.__cause__ is None
        assert exc.__context__ is None
        assert exc.__suppress_context__ is True
        _sem_segredo(_texto_de_um_erro(exc), "_levanta")
    else:
        raise AssertionError("deveria ter levantado")


# ---------------------------------------------------------------------------
# FINDING 2 — quatro resultados por mutacao de auditoria
# ---------------------------------------------------------------------------

def test_r2_estados_de_mutacao_de_auditoria_declarados():
    assert si.AUDIT_NAO_TENTADA == "nao_tentada"
    assert si.AUDIT_CONFIRMADA == "confirmada"
    assert si.AUDIT_REVERTIDA == "revertida"
    assert si.AUDIT_INDETERMINADA == "indeterminada"
    assert si.AUDITORIA_NAO_TENTADA.estado == si.AUDIT_NAO_TENTADA
    assert si.SnapshotImportError("x").resultado_auditoria is si.AUDITORIA_NAO_TENTADA
    assert si.ResultadoAuditoria("audit_start", si.AUDIT_CONFIRMADA).certo
    assert si.ResultadoAuditoria("audit_start", si.AUDIT_REVERTIDA).certo
    assert si.ResultadoAuditoria("audit_start", si.AUDIT_NAO_TENTADA).certo
    assert not si.ResultadoAuditoria("audit_start", si.AUDIT_INDETERMINADA).certo


def test_r2_mutacao_classifica_revertida_quando_o_rollback_retorna():
    for mutacao, chamada in (
        ("audit_start", lambda c: si.audit_start(c, 4837)),
        ("audit_finish", lambda c: si.audit_finish(c, 1, "failed", rows_loaded=0)),
        ("audit_mark_indeterminate",
         lambda c: si.audit_mark_indeterminate(c, 1, "x")),
    ):
        conn = _AuditFake(falhar_no_start=True, falhar_no_finish=True,
                          falhar_no_indeterminate=True)
        exc = _erro_de(lambda c=conn, f=chamada: f(c))
        assert isinstance(exc, si.MutacaoAuditoriaFalhou), mutacao
        assert exc.resultado.mutacao == mutacao
        assert exc.resultado.estado == si.AUDIT_REVERTIDA
        assert exc.resultado.certo is True
        assert conn.rollbacks == 1 and conn.commits == 0
        assert "revertida na propria conexao de auditoria" in str(exc)


def test_r2_mutacao_classifica_indeterminada_quando_o_rollback_tambem_falha():
    conn = _AuditFake(falhar_no_start=True, falhar_no_rollback=True)
    exc = _erro_de(lambda: si.audit_start(conn, 4837))
    assert isinstance(exc, si.MutacaoAuditoriaFalhou)
    assert exc.resultado.estado == si.AUDIT_INDETERMINADA
    assert exc.resultado.certo is False
    assert conn.rollbacks_tentados == 1 and conn.rollbacks == 0
    assert "nao pode ser afirmado" in str(exc)


def test_r2_mutacao_classifica_indeterminada_quando_o_commit_da_auditoria_falha():
    conn = _AuditFake(falhar_no_commit=True)
    exc = _erro_de(lambda: si.audit_start(conn, 4837))
    assert isinstance(exc, si.MutacaoAuditoriaFalhou)
    assert exc.resultado.estado == si.AUDIT_INDETERMINADA
    assert conn.rollbacks_tentados == 0, "commit indeterminado nao chama rollback"
    assert "o commit da auditoria foi TENTADO e levantou" in str(exc)


def test_r2_audit_start_indeterminado_nao_afirma_a_linha():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn()
        aud = _AuditFake(falhar_no_start=True, falhar_no_rollback=True)
        exc = _erro_de(lambda: si.apply_with_audit(
            dados, aud, r, execute_values=_fake_execute_values))
        assert isinstance(exc, si.AuditoriaInicialIncompleta)
        msg = str(exc)
        assert exc.resultado_auditoria.estado == si.AUDIT_INDETERMINADA
        assert "NAO se afirma se a linha de run existe" in msg
        assert "nenhuma linha de run foi criada" not in msg
        assert "reconcilie audit.source_sync_run em leitura" in msg
        assert dados.cursores_pedidos == 0, "a publicacao nao pode comecar"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_audit_finish_indeterminado_nao_diz_que_ficou_running():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(existentes=_existentes_de(r))
        aud = _AuditFake(falhar_no_finish=True, falhar_no_rollback=True)
        exc = _erro_de(lambda: si.apply_with_audit(
            dados, aud, r, execute_values=_fake_execute_values))
        assert isinstance(exc, si.AuditoriaIncompleta)
        msg = str(exc)
        assert exc.resultado_auditoria.estado == si.AUDIT_INDETERMINADA
        assert "permanece 'running'" not in msg
        assert "NAO se afirma em que estado ficou o registro" in msg
        assert "no-op concluido (commit confirmado)" in msg
        _proibe_afirmacao_sobre_os_dados(msg)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_mark_indeterminate_indeterminado_nao_afirma_a_nota():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        contagens = {si.TARGET_TABLE_TARGETS: len(r.target_rows),
                     si.TARGET_TABLE_CHANNELS: len(r.channel_rows)}
        dados = _ConnComContagem(contagens, falhar_no_commit=True)
        aud = _AuditFake(falhar_no_indeterminate=True, falhar_no_rollback=True)
        exc = _erro_de(lambda: si.apply_with_audit(
            dados, aud, r, execute_values=_fake_execute_values))
        assert isinstance(exc, si.PublicacaoIndeterminadaAuditoriaNaoConfirmada)
        msg = str(exc)
        assert exc.resultado_auditoria.estado == si.AUDIT_INDETERMINADA
        assert "NAO se afirma se a nota INDETERMINADO ficou ou nao na linha" in msg
        assert "a nota INDETERMINADO foi revertida" not in msg
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_mark_indeterminate_revertida_pode_afirmar_a_ausencia_da_nota():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        contagens = {si.TARGET_TABLE_TARGETS: len(r.target_rows),
                     si.TARGET_TABLE_CHANNELS: len(r.channel_rows)}
        dados = _ConnComContagem(contagens, falhar_no_commit=True)
        aud = _AuditFake(falhar_no_indeterminate=True)
        exc = _erro_de(lambda: si.apply_with_audit(
            dados, aud, r, execute_values=_fake_execute_values))
        assert exc.resultado_auditoria.estado == si.AUDIT_REVERTIDA
        assert "A nota INDETERMINADO foi revertida e NAO esta na linha" in str(exc)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_nao_ha_transacao_distribuida():
    """A auditoria nunca e' comitada junto com os dados, nem vice-versa."""
    fonte = Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8")
    assert "two-phase" not in fonte.lower()
    assert "PREPARE TRANSACTION" not in fonte.upper()
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        dados = _FakeConn(existentes=_existentes_de(r))
        aud = _AuditFake()
        si.apply_with_audit(dados, aud, r, execute_values=_fake_execute_values)
        # Dois recursos, dois commits proprios: 1 nos dados, 2 na auditoria
        # (o start e o finish).
        assert dados.commits == 1
        assert aud.commits == 2
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# FINDING 4 — spies diretos, contagem exata por estado
# ---------------------------------------------------------------------------

class _Espia:
    """Troca as funcoes do modulo por wrappers que contam chamadas."""

    NOMES = ("publish", "audit_start", "audit_finish", "audit_mark_indeterminate")

    def __init__(self):
        self.contagem = {n: 0 for n in self.NOMES}
        self._originais = {}

    def __enter__(self):
        for nome in self.NOMES:
            original = getattr(si, nome)
            self._originais[nome] = original

            def _wrapper(*a, __nome=nome, __orig=original, **kw):
                self.contagem[__nome] += 1
                return __orig(*a, **kw)

            setattr(si, nome, _wrapper)
        return self

    def __exit__(self, *_):
        for nome, original in self._originais.items():
            setattr(si, nome, original)
        return False


def _cenarios_da_matriz(r):
    contagens = {si.TARGET_TABLE_TARGETS: len(r.target_rows),
                 si.TARGET_TABLE_CHANNELS: len(r.channel_rows)}
    existentes = _existentes_de(r)
    return {
        "audit_start_revertido": (
            lambda: _FakeConn(), lambda: _AuditFake(falhar_no_start=True), 7),
        "audit_start_indeterminado": (
            lambda: _FakeConn(),
            lambda: _AuditFake(falhar_no_start=True, falhar_no_rollback=True), 7),
        "pre_commit": (
            lambda: _FakeConn(falhar_em="insert into marts.proxy_avoe"),
            lambda: _AuditFake(), 4),
        "pre_commit_sem_auditoria": (
            lambda: _FakeConn(falhar_em="insert into marts.proxy_avoe"),
            lambda: _AuditFake(falhar_no_finish=True), 8),
        "reversao_nao_confirmada": (
            lambda: _FakeConn(falhar_em="insert into marts.proxy_avoe",
                              falhar_no_rollback=True),
            lambda: _AuditFake(), 11),
        "indeterminado": (
            lambda: _ConnComContagem(contagens, falhar_no_commit=True),
            lambda: _AuditFake(), 5),
        "indeterminado_sem_auditoria": (
            lambda: _ConnComContagem(contagens, falhar_no_commit=True),
            lambda: _AuditFake(falhar_no_indeterminate=True), 9),
        "sucesso": (
            lambda: _FakeConn(existentes=existentes), lambda: _AuditFake(), 0),
        "auditoria_incompleta": (
            lambda: _FakeConn(existentes=existentes),
            lambda: _AuditFake(falhar_no_finish=True), 6),
    }


# Contagem EXATA esperada por estado:
#   publish, commit(dados), rollback(dados), audit_start, audit_finish,
#   audit_mark_indeterminate
_CONTAGENS_ESPERADAS = {
    "audit_start_revertido":       (0, 0, 0, 1, 0, 0),
    "audit_start_indeterminado":   (0, 0, 0, 1, 0, 0),
    "pre_commit":                  (1, 0, 1, 1, 1, 0),
    "pre_commit_sem_auditoria":    (1, 0, 1, 1, 1, 0),
    "reversao_nao_confirmada":     (1, 0, 1, 1, 0, 1),
    "indeterminado":               (1, 0, 0, 1, 0, 1),
    "indeterminado_sem_auditoria": (1, 0, 0, 1, 0, 1),
    "sucesso":                     (1, 1, 0, 1, 1, 0),
    "auditoria_incompleta":        (1, 1, 0, 1, 1, 0),
}


def test_r2_contagem_exata_de_chamadas_por_estado():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        observado = {}
        for nome, (fab_dados, fab_aud, codigo) in _cenarios_da_matriz(r).items():
            dados, aud = fab_dados(), fab_aud()
            with _Espia() as espia:
                try:
                    si.apply_with_audit(dados, aud, r,
                                        execute_values=_fake_execute_values)
                    saida = 0
                except si.SnapshotImportError as exc:
                    saida = si._despacha_desfecho(exc)[0]
            assert saida == codigo, f"{nome}: exit {saida}, esperado {codigo}"
            observado[nome] = (
                espia.contagem["publish"],
                dados.commits_tentados if nome != "sucesso" else dados.commits,
                dados.rollbacks_tentados,
                espia.contagem["audit_start"],
                espia.contagem["audit_finish"],
                espia.contagem["audit_mark_indeterminate"],
            )
        # `commits_tentados` no lugar de `commits` deixa o indeterminado visivel:
        # 1 tentativa, 0 confirmacoes.
        esperado = dict(_CONTAGENS_ESPERADAS)
        for nome in ("indeterminado", "indeterminado_sem_auditoria"):
            p, _c, rb, s, f, m = esperado[nome]
            esperado[nome] = (p, 1, rb, s, f, m)
        assert observado == esperado
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_zero_retry_e_zero_segunda_finalizacao():
    """Nenhuma mutacao de auditoria e' emitida duas vezes, em estado nenhum."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        for nome, (fab_dados, fab_aud, _codigo) in _cenarios_da_matriz(r).items():
            dados, aud = fab_dados(), fab_aud()
            with _Espia() as espia:
                try:
                    si.apply_with_audit(dados, aud, r,
                                        execute_values=_fake_execute_values)
                except si.SnapshotImportError:
                    pass
            for chamada, n in espia.contagem.items():
                assert n <= 1, f"{nome}: {chamada} chamada {n}x (retry)"
            finalizacoes = (espia.contagem["audit_finish"]
                            + espia.contagem["audit_mark_indeterminate"])
            assert finalizacoes <= 1, f"{nome}: {finalizacoes} finalizacoes"
            assert dados.commits_tentados <= 1, f"{nome}: commit repetido"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_nunca_publica_sem_audit_start_confirmado():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        for aud in (_AuditFake(falhar_no_start=True),
                    _AuditFake(falhar_no_start=True, falhar_no_rollback=True),
                    _AuditFake(falhar_no_commit=True)):
            dados = _FakeConn()
            with _Espia() as espia:
                try:
                    si.apply_with_audit(dados, aud, r,
                                        execute_values=_fake_execute_values)
                except si.AuditoriaInicialIncompleta:
                    pass
                else:
                    raise AssertionError("deveria ser AuditoriaInicialIncompleta")
            assert espia.contagem["publish"] == 0, "publish nao pode ser chamado"
            assert dados.cursores_pedidos == 0
            assert dados.commits_tentados == 0
            assert dados.rollbacks_tentados == 0
            assert dados.inseridas == []
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_nunca_tenta_rollback_depois_de_commit_confirmado_ou_indeterminado():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        contagens = {si.TARGET_TABLE_TARGETS: len(r.target_rows),
                     si.TARGET_TABLE_CHANNELS: len(r.channel_rows)}
        posteriores = {
            "confirmado_sucesso": (_FakeConn(existentes=_existentes_de(r)),
                                   _AuditFake()),
            "confirmado_auditoria_falha": (_FakeConn(existentes=_existentes_de(r)),
                                           _AuditFake(falhar_no_finish=True)),
            "indeterminado": (_ConnComContagem(contagens, falhar_no_commit=True),
                              _AuditFake()),
            "indeterminado_auditoria_falha": (
                _ConnComContagem(contagens, falhar_no_commit=True),
                _AuditFake(falhar_no_indeterminate=True)),
        }
        for nome, (dados, aud) in posteriores.items():
            try:
                si.apply_with_audit(dados, aud, r,
                                    execute_values=_fake_execute_values)
            except si.SnapshotImportError:
                pass
            assert dados.rollbacks_tentados == 0, f"{nome}: tentou rollback"
            assert dados.commits_tentados == 1, f"{nome}: commits_tentados"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_keyboard_interrupt_e_system_exit_propagam_crus():
    base = _tmp()
    try:
        r = _resultado_padrao(base)

        for classe in (KeyboardInterrupt, SystemExit):
            # (a) interrupcao dentro da transacao de dados
            class _ConnInterrompida(_FakeConn):
                def cursor(self):
                    self.cursores_pedidos += 1
                    raise classe("interrompido pelo operador")

            dados, aud = _ConnInterrompida(), _AuditFake()
            with _Espia() as espia:
                try:
                    si.apply_with_audit(dados, aud, r,
                                        execute_values=_fake_execute_values)
                except classe:
                    pass
                except si.SnapshotImportError as exc:
                    raise AssertionError(
                        f"{classe.__name__} virou estado operacional: {exc}")
                else:
                    raise AssertionError(f"{classe.__name__} nao propagou")
            assert espia.contagem["audit_finish"] == 0
            assert espia.contagem["audit_mark_indeterminate"] == 0
            assert dados.rollbacks_tentados == 0

            # (b) interrupcao durante a auditoria inicial
            class _AuditInterrompida(_AuditFake):
                def cursor(self):
                    self.cursores_pedidos += 1
                    raise classe("interrompido pelo operador")

            dados = _FakeConn()
            with _Espia() as espia:
                try:
                    si.apply_with_audit(dados, _AuditInterrompida(), r,
                                        execute_values=_fake_execute_values)
                except classe:
                    pass
                except si.SnapshotImportError as exc:
                    raise AssertionError(
                        f"{classe.__name__} virou estado operacional: {exc}")
                else:
                    raise AssertionError(f"{classe.__name__} nao propagou")
            assert espia.contagem["publish"] == 0
            assert dados.cursores_pedidos == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_o_publish_real_tambem_deixa_o_keyboard_interrupt_passar():
    base = _tmp()
    try:
        r = _resultado_padrao(base)

        def _interrompe(cur, sql, argslist, page_size=None):
            raise KeyboardInterrupt("ctrl-c no meio do INSERT")

        dados = _FakeConn()
        try:
            si.publish(dados, r, execute_values=_interrompe)
        except KeyboardInterrupt:
            pass
        except si.SnapshotImportError as exc:
            raise AssertionError(f"virou estado operacional: {exc}")
        else:
            raise AssertionError("KeyboardInterrupt nao propagou")
        assert dados.rollbacks_tentados == 0, "nada de rollback silencioso"
        assert dados.commits_tentados == 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# Matriz exaustiva final
# ---------------------------------------------------------------------------

def test_r2_matriz_exaustiva_de_estados():
    """Nove desfechos alcancaveis, com exit code e evidencia de auditoria."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        visto = {}
        for nome, (fab_dados, fab_aud, codigo) in _cenarios_da_matriz(r).items():
            dados, aud = fab_dados(), fab_aud()
            try:
                si.apply_with_audit(dados, aud, r,
                                    execute_values=_fake_execute_values)
                saida, estado_aud = 0, si.AUDIT_CONFIRMADA
            except si.SnapshotImportError as exc:
                saida = si._despacha_desfecho(exc)[0]
                # Toda excecao da maquina carrega o resultado da auditoria.
                estado_aud = exc.resultado_auditoria.estado
            visto[nome] = (saida, dados.rollbacks_tentados, dados.commits_tentados,
                           tuple(aud.status_escritos), estado_aud)

        assert visto == {
            # nome:  (exit, rollbacks_tentados, commits_tentados, status, auditoria)
            "audit_start_revertido":       (7, 0, 0, (), si.AUDIT_REVERTIDA),
            "audit_start_indeterminado":   (7, 0, 0, (), si.AUDIT_INDETERMINADA),
            "pre_commit":                  (4, 1, 0, ("failed",), si.AUDIT_CONFIRMADA),
            "pre_commit_sem_auditoria":    (8, 1, 0, (), si.AUDIT_REVERTIDA),
            "reversao_nao_confirmada":     (11, 1, 0, (), si.AUDIT_CONFIRMADA),
            "indeterminado":               (5, 0, 1, (), si.AUDIT_CONFIRMADA),
            "indeterminado_sem_auditoria": (9, 0, 1, (), si.AUDIT_REVERTIDA),
            "sucesso":                     (0, 0, 1, ("success",), si.AUDIT_CONFIRMADA),
            "auditoria_incompleta":        (6, 0, 1, (), si.AUDIT_REVERTIDA),
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r2_nenhum_estado_afirma_o_que_nao_observou():
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        for nome, (fab_dados, fab_aud, codigo) in _cenarios_da_matriz(r).items():
            if codigo == 0:
                continue
            dados, aud = fab_dados(), fab_aud()
            exc = _erro_de(lambda: si.apply_with_audit(
                dados, aud, r, execute_values=_fake_execute_values))
            msg = str(exc)
            rotulo = si._despacha_desfecho(exc)[1]
            assert "rollback" not in rotulo.lower(), nome
            # "rollback aplicado" so' nos estados com reversao CONFIRMADA.
            if nome in ("pre_commit", "pre_commit_sem_auditoria"):
                assert "rollback aplicado" in msg, nome
                assert exc.rollback_confirmado is True
            else:
                assert "rollback aplicado" not in msg, nome
                assert getattr(exc, "rollback_confirmado", False) is False, nome
            # Nenhum estado pos-commit pode alegar que nada foi gravado.
            if codigo in (5, 6, 9, 11):
                _proibe_afirmacao_sobre_os_dados(msg)
            assert "retry" in msg.lower() or "repita" in msg.lower(), nome
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ===========================================================================
# AVH-4A-H1-R3 — a fase do commit da auditoria
#
# O bloqueio: classificar pelo que o `rollback()` devolveu e' insuficiente.
# Se o commit da auditoria FOI TENTADO e levantou, o resultado e'
# `indeterminada` para sempre — um rollback posterior que retorne nao pode
# rebaixar isso para `revertida`, porque ele nao desfaz um commit aplicado.
# ===========================================================================

_MUTACOES = (
    ("audit_start", lambda c: si.audit_start(c, 4837)),
    ("audit_finish", lambda c: si.audit_finish(c, 1, "failed", rows_loaded=0)),
    ("audit_mark_indeterminate", lambda c: si.audit_mark_indeterminate(c, 1, "x")),
)


def test_r3_commit_perdido_e_sempre_indeterminado_nas_tres_mutacoes():
    """SQL aplicado, commit aplicado, confirmacao perdida, rollback seria no-op."""
    for mutacao, chamada in _MUTACOES:
        conn = _AuditFake(commit_perdido=True)
        exc = _erro_de(lambda c=conn, f=chamada: f(c))
        assert isinstance(exc, si.MutacaoAuditoriaFalhou), mutacao
        ra = exc.resultado
        assert ra.mutacao == mutacao
        assert ra.estado == si.AUDIT_INDETERMINADA, f"{mutacao}: {ra.estado}"
        assert ra.estado != si.AUDIT_REVERTIDA
        assert ra.certo is False
        # A mutacao chegou a ser emitida e o servidor a aplicou.
        assert conn.mutacoes_aplicadas == ["commit aplicado no servidor"]
        assert conn.commits_tentados == 1 and conn.commits == 0
        # E nenhum rollback foi tentado depois do commit.
        assert conn.rollbacks_tentados == 0, f"{mutacao}: tentou rollback pos-commit"
        assert conn.rollbacks == 0
        msg = str(exc)
        assert "commit da auditoria foi TENTADO e levantou" in msg
        assert "nenhum rollback foi tentado depois disso" in msg
        # Nenhuma afirmacao sobre o que ficou persistido.
        assert "nao pode ser afirmado" in msg
        for proibida in ("a linha nao existe", "permanece 'running'",
                         "nenhuma linha de run foi criada", "revertida"):
            assert proibida not in msg, f"{mutacao} afirmou: {proibida!r}"
        # Reconciliacao read-only e zero retry.
        assert "reconcilie audit.source_sync_run em leitura" in msg
        assert "antes de qualquer nova execucao" in msg


def test_r3_rollback_que_retorna_depois_do_commit_nao_rebaixa_para_revertida():
    """Mesmo com um rollback perfeitamente saudavel, continua indeterminada."""
    for mutacao, chamada in _MUTACOES:
        conn = _AuditFake(commit_perdido=True)
        # rollback saudavel: se fosse chamado, retornaria como no-op.
        assert conn.falhar_no_rollback is False
        exc = _erro_de(lambda c=conn, f=chamada: f(c))
        assert exc.resultado.estado == si.AUDIT_INDETERMINADA, mutacao
        # Prova de que a saude do rollback e' irrelevante: nem foi consultado.
        assert conn.rollbacks_tentados == 0, mutacao
        # E se alguem o chamasse manualmente agora, ele retornaria — o que
        # continua nao provando nada sobre a linha.
        conn.rollback()
        assert conn.rollbacks == 1
        assert exc.resultado.estado == si.AUDIT_INDETERMINADA, mutacao


def test_r3_as_quatro_faces_por_mutacao():
    """A matriz completa de resultados, para cada uma das tres mutacoes."""
    observado = {}
    for mutacao, chamada in _MUTACOES:
        # (a) falha antes do commit, rollback confirmado -> revertida
        c = _AuditFake(falhar_no_start=True, falhar_no_finish=True,
                       falhar_no_indeterminate=True)
        e = _erro_de(lambda cc=c, f=chamada: f(cc))
        observado[(mutacao, "pre_commit_rollback_ok")] = (
            e.resultado.estado, c.rollbacks_tentados, c.commits_tentados)

        # (b) falha antes do commit, rollback falhando -> indeterminada
        c = _AuditFake(falhar_no_start=True, falhar_no_finish=True,
                       falhar_no_indeterminate=True, falhar_no_rollback=True)
        e = _erro_de(lambda cc=c, f=chamada: f(cc))
        observado[(mutacao, "pre_commit_rollback_falho")] = (
            e.resultado.estado, c.rollbacks_tentados, c.commits_tentados)

        # (c) commit levanta -> indeterminada, sem rollback
        c = _AuditFake(commit_perdido=True)
        e = _erro_de(lambda cc=c, f=chamada: f(cc))
        observado[(mutacao, "commit_levanta")] = (
            e.resultado.estado, c.rollbacks_tentados, c.commits_tentados)

        # (d) commit retorna -> confirmada
        c = _AuditFake()
        chamada(c)
        observado[(mutacao, "commit_ok")] = (
            si.AUDIT_CONFIRMADA, c.rollbacks_tentados, c.commits_tentados)

    esperado = {}
    for mutacao, _ in _MUTACOES:
        esperado[(mutacao, "pre_commit_rollback_ok")] = (si.AUDIT_REVERTIDA, 1, 0)
        esperado[(mutacao, "pre_commit_rollback_falho")] = (si.AUDIT_INDETERMINADA, 1, 0)
        esperado[(mutacao, "commit_levanta")] = (si.AUDIT_INDETERMINADA, 0, 1)
        esperado[(mutacao, "commit_ok")] = (si.AUDIT_CONFIRMADA, 0, 1)
    assert observado == esperado


def test_r3_commit_perdido_da_auditoria_sobe_pelos_desfechos_certos():
    """De ponta a ponta: o estado indeterminado da auditoria nao vira `failed`."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        contagens = {si.TARGET_TABLE_TARGETS: len(r.target_rows),
                     si.TARGET_TABLE_CHANNELS: len(r.channel_rows)}

        # (a) commit perdido ja no audit_start -> publicacao nao tentada
        dados, aud = _FakeConn(), _AuditFake(commit_perdido=True)
        exc = _erro_de(lambda: si.apply_with_audit(
            dados, aud, r, execute_values=_fake_execute_values))
        assert isinstance(exc, si.AuditoriaInicialIncompleta)
        assert exc.resultado_auditoria.estado == si.AUDIT_INDETERMINADA
        assert si._despacha_desfecho(exc)[0] == 7
        assert dados.cursores_pedidos == 0, "publish nao pode ter sido chamado"
        assert "NAO se afirma se a linha de run existe" in str(exc)

        # (b) commit perdido no audit_finish(success) -> auditoria incompleta,
        #     mas os dados continuam publicados
        class _AuditPerdeNoFinish(_AuditFake):
            def commit(self):
                # o start confirma; o finish e' que perde a confirmacao
                if self.commits >= 1:
                    self.commits_tentados += 1
                    self.mutacoes_aplicadas.append("commit aplicado no servidor")
                    raise RuntimeError("confirmacao perdida no finish")
                return _FakeConn.commit(self)

        dados, aud = _FakeConn(existentes=_existentes_de(r)), _AuditPerdeNoFinish()
        exc = _erro_de(lambda: si.apply_with_audit(
            dados, aud, r, execute_values=_fake_execute_values))
        assert isinstance(exc, si.AuditoriaIncompleta)
        assert exc.resultado_auditoria.estado == si.AUDIT_INDETERMINADA
        assert si._despacha_desfecho(exc)[0] == 6
        assert dados.commits == 1 and dados.rollbacks_tentados == 0
        assert aud.status_escritos == ["success"], "o UPDATE saiu, a confirmacao nao"
        msg = str(exc)
        assert "permanece 'running'" not in msg, "nao se sabe o estado da linha"
        assert "NAO se afirma em que estado ficou o registro" in msg
        _proibe_afirmacao_sobre_os_dados(msg)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r3_falha_de_close_nao_esconde_a_classificacao_principal():
    """O `close()` e' acessorio: sua falha nao troca o estado nem o exit code."""
    base = _tmp()
    try:
        r = _resultado_padrao(base)
        for falhar_close, encerrada, trecho in (
            (False, True, "a conexao de dados foi encerrada"),
            (True, False, "nem o fim da transacao foi observado"),
        ):
            dados = _FakeConn(falhar_em="insert into marts.proxy_avoe",
                              falhar_no_rollback=True,
                              falhar_no_close=falhar_close)
            aud = _AuditFake()
            exc = _erro_de(lambda: si.apply_with_audit(
                dados, aud, r, execute_values=_fake_execute_values))
            # A classificacao principal e' a mesma nos dois casos.
            assert isinstance(exc, si.ReversaoNaoConfirmada)
            assert si._despacha_desfecho(exc)[0] == 11
            assert exc.conexao_encerrada is encerrada
            assert dados.fechamentos_tentados == 1
            msg = str(exc)
            assert trecho in msg
            # A causa principal (a falha original) continua visivel.
            assert "falha simulada no destino" in msg
            assert "REVERSAO NAO FOI CONFIRMADA" in msg
            if falhar_close:
                assert "a conexao de dados foi encerrada para forcar" not in msg
            _proibe_afirmacao_sobre_os_dados(msg)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_r3_nenhuma_captura_de_base_exception_no_importador():
    """Prova estrutural complementar: nada de `except BaseException`."""
    fonte = Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    for no in ast.walk(arvore):
        if isinstance(no, ast.ExceptHandler) and no.type is not None:
            tipo = ast.unparse(no.type)
            assert "BaseException" not in tipo, f"captura larga demais: {tipo}"
            assert "KeyboardInterrupt" not in tipo, tipo
            assert "SystemExit" not in tipo, tipo
        if isinstance(no, ast.ExceptHandler) and no.type is None:
            raise AssertionError("except nu (captura BaseException por tabela)")


def test_r3_a_fase_e_explicita_no_executor_de_mutacao():
    """Complemento em AST: a classificacao le a FASE, nao so' o rollback."""
    arvore = ast.parse(Path("pipelines/avoe/snapshot_import.py").read_text(encoding="utf-8"))
    alvo = [n for n in ast.walk(arvore)
            if isinstance(n, ast.FunctionDef)
            and n.name == "_executa_mutacao_auditoria"][0]
    fonte = ast.unparse(alvo)
    assert "fase = 'mutacao'" in fonte and "fase = 'commit'" in fonte
    assert "if fase == 'commit'" in fonte
    # O ramo de commit tem de levantar ANTES de qualquer rollback.
    corpo_commit = fonte.split("if fase == 'commit'")[1].split("try:")[0]
    assert "AUDIT_INDETERMINADA" in corpo_commit
    assert "rollback" not in corpo_commit.replace("nenhum rollback foi tentado", "")


def test_r3_keyboard_interrupt_atravessa_o_executor_de_mutacao():
    for classe in (KeyboardInterrupt, SystemExit):
        class _AuditInterrompe(_AuditFake):
            def commit(self):
                self.commits_tentados += 1
                raise classe("ctrl-c no commit da auditoria")

        conn = _AuditInterrompe()
        try:
            si.audit_start(conn, 4837)
        except classe:
            pass
        except si.SnapshotImportError as exc:
            raise AssertionError(f"{classe.__name__} virou estado: {exc}")
        else:
            raise AssertionError(f"{classe.__name__} nao propagou")
        assert conn.rollbacks_tentados == 0


def test_r3_commit_perdido_nao_vaza_segredo_em_nenhuma_superficie():
    class _AuditDriverNoCommit(_AuditFake):
        def commit(self):
            self.commits_tentados += 1
            raise _ExcecaoDeDriver("commit da auditoria")

    conn = _AuditDriverNoCommit()
    exc = _erro_de(lambda: si.audit_start(conn, 4837))
    assert exc.resultado.estado == si.AUDIT_INDETERMINADA
    assert exc.__cause__ is None and exc.__context__ is None
    _sem_segredo(_texto_de_um_erro(exc), "commit perdido da auditoria")
    assert "suprimida por conter segredo" in str(exc)
