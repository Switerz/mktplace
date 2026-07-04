"""Hashing e serialização JSON segura para a Fase Raw Shopee 1."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """SHA-256 do conteúdo bruto do arquivo (idempotência técnica por arquivo)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_safe(value: Any) -> Any:
    """Converte valores de célula (openpyxl/csv) em algo serializável em JSON.

    NaN/Infinity viram None (JSON estrito não aceita). Datas/horas viram ISO 8601.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def payload_to_json(payload: dict) -> str:
    """JSON estável (chaves ordenadas) para hash de linha e para raw_payload."""
    safe = json_safe(payload)
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, allow_nan=False)


def row_sha256(payload: dict) -> str:
    return sha256_text(payload_to_json(payload))
