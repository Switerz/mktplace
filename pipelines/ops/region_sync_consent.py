"""
Gate B6.1b — consentimento PERSISTENTE e gitignored para o sync regional
agendado (`pipelines.ops.sync_region_if_needed` / `pipelines.sync_region_daily`).

Motivo de existir: `sync_region_if_needed` e' `critical=True` em
`pipelines.ops.orchestrate.PIPELINES["full_daily"]` (Gate B2). Ate' aqui, o
consentimento de escrita (`I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1`) so'
existia como variavel de ambiente definida manualmente para UMA UNICA
invocacao interativa (Gates B2-B5). O Task Scheduler dispara um processo
NOVO, sem essa sessao interativa — sem um mecanismo persistente, o preflight
de `sync_region_daily` (`check_sync_region_consent`,
ver `pipelines/ops/preflight.py`) sempre bloquearia, e como o step e'
critico, `full_daily` reportaria FAILED todo dia assim que fosse agendado
(achado do Gate B6.1).

Arquivo: `.env.region-sync.local` na raiz do repo — mesmo padrao de
`.env.gold-write.local` (`pipelines/ingestion/gold_regional/write_conn.py`):
fora do `.env` principal, coberto pela regra generica `.env.*` do
`.gitignore`, nunca commitado. Contem SOMENTE:

    I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1

Isso NAO e' uma credencial (nenhum host/usuario/senha aqui) — e' um
consentimento explicito e persistente de que a escrita automatizada em
`marts.fact_marketplace_region_daily` foi autorizada previamente por um
humano, para a execucao agendada nao depender de ninguem estar logado
setando a variavel manualmente.

Regras de seguranca:
  - a variavel de ambiente, se ja estiver definida no processo (ex.: setada
    manualmente para uma unica invocacao, como em todos os Gates B2-B5),
    SEMPRE vence — o arquivo so' e' consultado quando ela ainda nao esta
    presente;
  - o arquivo NUNCA e' criado automaticamente por este modulo (so' leitura);
  - o conteudo do arquivo NUNCA e' impresso/logado — no maximo, o NOME do
    arquivo (fixo, publico, sem informacao sensivel);
  - chaves extras no arquivo sao ignoradas silenciosamente (este arquivo nao
    e' um secret de conexao como `.env.gold-write.local` — nao ha DSN aqui —
    entao nao exige a validacao estrita de "exatamente estas chaves" daquele
    modulo);
  - nunca escreve nada em disco, nunca persiste nada em `.env`.

Uso:
    from pipelines.ops.region_sync_consent import ensure_region_sync_consent
    if ensure_region_sync_consent():
        ...  # I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1 esta' em os.environ
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGION_SYNC_CONSENT_PATH = REPO_ROOT / ".env.region-sync.local"

CONSENT_KEY = "I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY"


def ensure_region_sync_consent(env_path: Path | None = None) -> bool:
    """Garante que `I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY` esteja
    disponivel em `os.environ` para o RESTANTE deste processo.

    Prioridade: variavel de ambiente ja definida > arquivo local. Nunca
    escreve em disco (nem no arquivo de consentimento, nem em `.env`); a
    unica escrita e' em `os.environ`, em memoria, so' deste processo.

    `env_path=None` (padrao) resolve `DEFAULT_REGION_SYNC_CONSENT_PATH` NO
    MOMENTO DA CHAMADA (nao como valor de default fixado na definicao da
    funcao) — isso permite que testes monkeypatchem
    `DEFAULT_REGION_SYNC_CONSENT_PATH` sem precisar passar `env_path`
    explicitamente toda vez."""
    if os.environ.get(CONSENT_KEY) == "1":
        return True

    path = env_path if env_path is not None else DEFAULT_REGION_SYNC_CONSENT_PATH
    if not path.is_file():
        return False

    values = dotenv_values(path)
    if values.get(CONSENT_KEY) == "1":
        os.environ[CONSENT_KEY] = "1"
        return True
    return False
