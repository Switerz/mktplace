"""Dublê de conexão para o advisory lock da fato diária.

🔴 DEVOLVE TUPLA POR PADRÃO, e isso não é detalhe de implementação do dublê: é
o que o runtime entrega. `Connection.execute(...).first()` do SQLAlchemy
devolve um `Row`, que se lê por posição; um dublê que devolvesse dicionário
deixaria passar um `row["pg_try_advisory_lock"]` sem fallback posicional e a
falha só apareceria no primeiro contato com o banco. Foi exatamente assim que
o piloto dos runners de ML caiu no Airflow (PR goca-se/airflow#1610): 596
testes verdes, `ValueError` na primeira execução real.

`modo="mapping"` existe para provar o outro ramo — o de quem configurar um
cursor que devolve mapping — sem que ele vire o padrão.
"""
from __future__ import annotations

from typing import Any


class _Resultado:
    def __init__(self, linha: Any) -> None:
        self._linha = linha

    def first(self) -> Any:
        return self._linha


class ConexaoDeLock:
    """Conexão fake que registra cada SQL executado, na ordem.

    Parâmetros:
      `livre`     — resposta de `pg_try_advisory_lock` (True = adquiriu).
      `modo`      — "tupla" (padrão, igual ao runtime) ou "mapping".
      `erro_unlock` — exceção a levantar no `pg_advisory_unlock`, para provar
                      que o cleanup não mascara a exceção original.
      `unlock_false` — `pg_advisory_unlock` devolve false (o PostgreSQL faz isso
                      quando a sessão não detém a chave), para provar que o
                      cleanup INVALIDA a conexão em vez de confiar no `close()`.
      `sem_linha` — `first()` devolve None, simulando banco que não respondeu.
    """

    def __init__(
        self,
        *,
        livre: bool = True,
        modo: str = "tupla",
        erro_unlock: Exception | None = None,
        unlock_false: bool = False,
        sem_linha: bool = False,
    ) -> None:
        self.livre = livre
        self.modo = modo
        self.erro_unlock = erro_unlock
        self.unlock_false = unlock_false
        self.sem_linha = sem_linha
        self.sqls: list[str] = []
        self.parametros: list[dict] = []
        self.fechada = False
        self.invalidada = False

    # -- API mínima de sqlalchemy.Connection usada pelo lock -----------------
    def execute(self, clause: Any, params: dict | None = None) -> _Resultado:
        sql = str(clause)
        self.sqls.append(sql)
        self.parametros.append(dict(params or {}))
        if "pg_advisory_unlock" in sql:
            if self.erro_unlock is not None:
                raise self.erro_unlock
            return _Resultado(self._linha(not self.unlock_false, "pg_advisory_unlock"))
        if "pg_try_advisory_lock" in sql:
            if self.sem_linha:
                return _Resultado(None)
            return _Resultado(self._linha(self.livre, "pg_try_advisory_lock"))
        return _Resultado(None)

    def close(self) -> None:
        self.fechada = True

    def invalidate(self) -> None:
        """Descarta a conexão do pool e fecha o socket.

        🔴 É ela, e não `close()`, que garante a liberação de um lock de sessão:
        `close()` numa conexão pooled devolve ao pool com a sessão viva, e o
        lock sobrevive. Medido contra PostgreSQL 16 em 22/09/2026.
        """
        self.invalidada = True

    # -- helpers -------------------------------------------------------------
    def _linha(self, valor: bool, coluna: str) -> Any:
        """No modo mapping a chave é o NOME REAL da função chamada.

        Um mapping que devolvesse sempre `pg_try_advisory_lock` faria a leitura
        do unlock cair no ramo posicional por acidente — e o teste passaria sem
        exercitar o caminho que existe no runtime.
        """
        if self.modo == "mapping":
            return {coluna: valor}
        return (valor,)

    @property
    def chaves_usadas(self) -> list[int]:
        return [p["chave"] for p in self.parametros if "chave" in p]

    def executou(self, fragmento: str) -> bool:
        return any(fragmento in s for s in self.sqls)
