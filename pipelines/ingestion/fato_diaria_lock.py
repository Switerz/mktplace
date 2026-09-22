"""Exclusao mutua dos escritores de `marts.fact_marketplace_daily_performance`.

Gate SH-AUTO-1. Este modulo e' o CONTRATO — o valor da chave, a forma da
chamada e a semantica da falha moram aqui, e nao no chamador.

POR QUE ESTE LOCK EXISTE
------------------------
Cinco caminhos escrevem na MESMA tabela e nenhum deles adquiria lock algum:

    --source shopee        PATCH_SHOPEE_ORDERS_SQL   (marketplace_id 3)
    --source shopee-stats  PATCH_SHOP_STATS_SQL      (marketplace_id 3)
    --source shopee-ads    PATCH_ADS_SQL             (marketplace_id 3)
    --source ml            UPSERT_SQL                (marketplace_id 2)
    --source tiktok        UPSERT_SQL                (marketplace_id 1)

Os tres primeiros sao PARCIAIS por desenho: cada um escreve apenas as colunas
de que e' fonte, e conta com o que os outros ja publicaram na MESMA LINHA
(Gates SD2-C e R2.1). Dois deles rodando em paralelo sobre a mesma chave
`(date, loja_id, marketplace_id)` produzem uma linha montada com metades de
instantes diferentes — e ninguem fica vermelho, porque cada `ON CONFLICT DO
UPDATE` individual e' valido. E' exatamente essa a falha que o lock impede.

O problema deixa de ser hipotetico quando o runner Shopee do Airflow entrar:
duas esteiras — o Task Scheduler do notebook e o worker — passam a escrever na
mesma tabela sem nenhuma coordenacao entre elas. O lock e' a condicao que o
documento de serving exige antes dessa convivencia existir.

🔑 A CHAVE E' POR MARKETPLACE, NAO POR SOURCE
---------------------------------------------
Os tres writers Shopee usam EXATAMENTE a mesma chave, porque disputam a mesma
linha. Chavear por `source` daria tres locks distintos e nenhuma exclusao —
seria cerimonia sem efeito.

Marketplaces diferentes NAO se excluem, e isso e' deliberado: a unica chave da
tabela e' `(date, loja_id, marketplace_id)` e uma loja pertence a um unico
marketplace (`marts.dim_loja`), entao ML e Shopee nunca disputam a mesma linha.
Serializar canais independentes so' alongaria o `full_daily` sem proteger nada.

🔴 LOCK DE SESSAO, NAO DE TRANSACAO
-----------------------------------
`pg_try_advisory_xact_lock` morreria no primeiro `commit()` — e este fluxo
commita varias vezes antes do UPSERT (`_start_sync_run` commita na hora,
`_log_quality_checks` tambem). O lock precisa cobrir a LEITURA que determina a
fotografia, e essa leitura acontece entre esses commits. Um lock transacional
so cobriria o ultimo trecho, deixando de fora justamente a extracao.

Entao: `pg_try_advisory_lock` em conexao DEDICADA e em autocommit, adquirido
antes de qualquer leitura ou auditoria, liberado no `finally`. A transacao de
escrita nasce depois, no ciclo normal de `local_session()`.

A conexao dedicada e' a garantia dura da liberacao: lock de sessao morre com a
sessao. Se o processo cair entre o UPSERT e o `pg_advisory_unlock`, o PostgreSQL
libera ao encerrar a conexao — nao existe lock preso a limpar na mao. Por isso o
`finally` fecha a conexao mesmo quando o unlock explicito falha.

🔴 `try` E NUNCA A VARIANTE QUE ESPERA
--------------------------------------
`pg_advisory_lock` (bloqueante) transformaria concorrencia em fila silenciosa: o
segundo processo ficaria pendurado ate o `execution_timeout` do step e so entao
apareceria como timeout — diagnostico errado para o que e' disputa. `try` falha
no ato, com nome proprio e exit code proprio.

E' tambem por isso que NAO ha retry: lock ocupado significa que o outro escritor
esta com a fotografia na mao. Repetir sem saber o resultado dele e' o caminho
para publicar por cima de um snapshot em construcao.

CONTRATO PARA O RUNNER DO AIRFLOW (gate SH-AUTO-4)
--------------------------------------------------
O runner Shopee NAO deve importar este modulo (repositorios distintos, imagens
distintas) nem recalcular a chave a partir da formula. Ele deve usar o LITERAL:

    SELECT pg_try_advisory_lock(918130003)   -- Shopee

na forma de UM argumento `bigint`. A forma de dois argumentos
(`pg_try_advisory_lock(int, int)`) ocupa um espaco de chaves DIFERENTE no
PostgreSQL e nao se excluiria com esta — usa-la seria um lock que nao tranca
nada, e passaria em qualquer teste que nao coloque as duas esteiras frente a
frente.

Os valores estao congelados na tabela abaixo e travados por teste. A formula
existe para explicar de onde vieram, nao para ser reexecutada em outro
repositorio.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from sqlalchemy import text

from pipelines.common.logging import get_logger

logger = get_logger(__name__)

#: Base da serie. As chaves ja em uso no repositorio ocupam 906..917; esta
#: serie comeca em 918 para nao colidir com nenhuma delas nem com as chaves
#: avulsas (564738291056, 987654321123). Ver `KNOWN_FOREIGN_LOCK_KEYS` em
#: `pipelines/expedicao/contract.py`.
FATO_DIARIA_LOCK_BASE = 918_130_000

#: 🔒 VALORES CONGELADOS. Derivacao: `FATO_DIARIA_LOCK_BASE + marketplace_id`,
#: com `marketplace_id` conforme `marts.dim_marketplace` (1 TikTok, 2 Mercado
#: Livre, 3 Shopee). Os literais estao escritos por extenso de proposito: quem
#: for reusar a chave em outro repositorio le o NUMERO, nao a soma.
FATO_DIARIA_ADVISORY_LOCK_KEYS: dict[int, int] = {
    1: 918_130_001,  # TikTok Shop
    2: 918_130_002,  # Mercado Livre
    3: 918_130_003,  # Shopee — usada pelos TRES writers (orders, stats, ads)
}

#: Exit code de `python -m pipelines.ingestion.daily_performance` quando o lock
#: esta ocupado. 75 e' `EX_TEMPFAIL` do `sysexits.h`: "falha temporaria, o
#: pedido deve ser repetido MAIS TARDE" — que e' exatamente o caso, e se
#: distingue de 1 (erro generico) na leitura do log do orquestrador. O
#: orquestrador nao reexecuta step nenhum; o codigo e' sinal para o humano.
EXIT_CODE_LOCK_UNAVAILABLE = 75


class FatoDiariaLockUnavailable(RuntimeError):
    """Outro escritor ja detem o lock da fato diaria deste marketplace.

    Excecao propria, e nao `RuntimeError` generico, para que o chamador
    distinga "havia outra esteira publicando" de qualquer outro erro — a
    diferenca entre uma execucao que nao devia ter comecado e uma que comecou e
    quebrou. So a primeira e' segura de repetir depois.
    """

    def __init__(self, marketplace_id: int, chave: int) -> None:
        self.marketplace_id = marketplace_id
        self.chave = chave
        super().__init__(
            f"advisory lock {chave} (marketplace_id={marketplace_id}) ja esta "
            f"em uso: outro escritor de marts.fact_marketplace_daily_performance "
            f"esta publicando agora. Nada foi lido, nada foi escrito e nenhuma "
            f"linha de auditoria foi aberta. NAO reexecute em paralelo — espere "
            f"o outro terminar."
        )


def lock_key_for(marketplace_id: int) -> int:
    """Chave de advisory lock do marketplace. Levanta para id desconhecido.

    Fail-closed de proposito: um `marketplace_id` novo que caisse num
    `dict.get(...)` devolvendo `None` seguiria sem lock nenhum, e a ausencia de
    protecao apareceria como corrupcao de linha semanas depois, nao como erro.
    """
    if isinstance(marketplace_id, bool) or not isinstance(marketplace_id, int):
        raise ValueError(
            f"marketplace_id precisa ser int, recebido {type(marketplace_id).__name__}"
        )
    try:
        return FATO_DIARIA_ADVISORY_LOCK_KEYS[marketplace_id]
    except KeyError:
        conhecidos = ", ".join(str(k) for k in sorted(FATO_DIARIA_ADVISORY_LOCK_KEYS))
        raise ValueError(
            f"marketplace_id={marketplace_id} nao tem chave de advisory lock "
            f"declarada em FATO_DIARIA_ADVISORY_LOCK_KEYS (conhecidos: "
            f"{conhecidos}). Marketplace novo exige chave nova no contrato — "
            f"escrever na fato diaria sem lock nao e' uma opcao."
        ) from None


def _default_connect() -> Any:
    """Conexao dedicada em AUTOCOMMIT no banco local (Neon).

    Import tardio: `pipelines.common.db` cria o engine no import do modulo e
    levanta se `DATABASE_URL` nao estiver configurado. Importar no topo faria
    qualquer teste que so' inspeciona o contrato (chaves, excecao) depender de
    configuracao de banco.
    """
    from pipelines.common.db import local_engine

    return local_engine().connect().execution_options(isolation_level="AUTOCOMMIT")


def _acquired(row: Any) -> bool:
    """Le o booleano de `pg_try_advisory_lock` de uma linha de resultado.

    Aceita tupla/`Row` (o que o SQLAlchemy entrega aqui) e mapping (o que um
    cursor configurado com `RealDictCursor` entregaria). Um dublê que devolva
    so' uma das duas formas passa nos testes e falha no primeiro contato real —
    foi assim que o piloto dos runners de ML caiu.
    """
    if row is None:
        raise RuntimeError(
            "pg_try_advisory_lock nao devolveu linha. Sem resposta do banco nao "
            "ha como afirmar que o lock foi adquirido, e assumir que sim "
            "publicaria sem exclusao."
        )
    try:
        valor = row["pg_try_advisory_lock"]
    except (TypeError, KeyError, IndexError):
        valor = row[0]
    return bool(valor)


@contextmanager
def fato_diaria_lock(
    marketplace_id: int,
    *,
    connect: Callable[[], Any] | None = None,
) -> Iterator[int]:
    """Segura o lock da fato diaria durante todo o bloco.

    Levanta `FatoDiariaLockUnavailable` ANTES de ceder o controle quando outro
    escritor ja o detem — o chamador nunca chega a ler a fonte.

    `connect` existe para os testes injetarem uma conexao; em producao e'
    sempre a conexao dedicada em autocommit do banco local.
    """
    chave = lock_key_for(marketplace_id)
    conn = (connect or _default_connect)()
    adquirido = False
    try:
        adquirido = _acquired(
            conn.execute(text("SELECT pg_try_advisory_lock(:chave)"), {"chave": chave}).first()
        )
        if not adquirido:
            raise FatoDiariaLockUnavailable(marketplace_id, chave)
        logger.info(
            "advisory lock %d adquirido (marketplace_id=%d) — escritor exclusivo "
            "de marts.fact_marketplace_daily_performance",
            chave, marketplace_id,
        )
        yield chave
    finally:
        # 🔴 A liberacao e' CLEANUP e nunca pode SUBSTITUIR a excecao que trouxe
        # o fluxo ate aqui. Um commit indeterminado que saia daqui como
        # "erro ao liberar lock" seria lido no runbook como "nada foi
        # publicado" — afirmacao falsa e cara. Por isso os dois `except` mudos.
        #
        # Perder o unlock explicito NAO deixa lock preso: ele e' de sessao e
        # morre quando a conexao fecha, o que o `close()` abaixo garante e o
        # proprio PostgreSQL garante se o processo morrer antes disso.
        if adquirido:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:chave)"), {"chave": chave})
            except Exception:  # noqa: BLE001, S110 — ver comentario acima
                logger.warning(
                    "pg_advisory_unlock(%d) falhou; o lock sera liberado no "
                    "fechamento da conexao", chave,
                )
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110 — idem
            pass
