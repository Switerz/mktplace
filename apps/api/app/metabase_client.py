"""
Cliente HTTP para a API REST do Metabase.
Usado como fonte de dados quando o banco local não está disponível.
"""
from __future__ import annotations

import httpx

from app.config import settings


def query(sql: str) -> list[dict]:
    """
    Executa uma query SQL nativa no Data Mart via Metabase e retorna list[dict].
    Lança httpx.HTTPError se a requisição falhar.
    """
    payload = {
        "database": settings.metabase_database_id,
        "type": "native",
        "native": {"query": sql},
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{settings.metabase_url}/api/dataset",
            headers={"x-api-key": settings.metabase_api_key},
            json=payload,
        )
        resp.raise_for_status()

    raw = resp.json()

    # Metabase pode retornar erro na resposta mesmo com HTTP 200
    if "error" in raw:
        raise RuntimeError(f"Metabase query error: {raw['error']}")

    data = raw["data"]
    cols = [c["name"] for c in data["cols"]]
    return [dict(zip(cols, row)) for row in data["rows"]]
