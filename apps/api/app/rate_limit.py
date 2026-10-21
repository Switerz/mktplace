"""Rate limit por janela deslizante, em memoria — sem dependencia nova.

Porte direto do mecanismo que o repositorio ja' usa no MCP do Oraculo
(`apps/web/src/server/oracle/rate-limit.ts`): mesma janela deslizante, mesmo
relogio injetavel, mesma honestidade sobre o limite ser APROXIMADO — cada
processo/instancia tem o proprio contador.

Acrescimo em relacao ao original: teto de CHAVES, para que um cliente variando
o identificador (ex.: `X-Forwarded-For` forjado) nao faca a memoria crescer sem
limite. Estourou, sai a chave menos recente.
"""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Resultado:
    permitido: bool
    retry_after_s: int = 0


class JanelaDeslizante:
    def __init__(
        self,
        limite: int,
        janela_s: float,
        *,
        max_chaves: int = 10_000,
        relogio: Callable[[], float] = time.monotonic,
    ):
        self.limite = limite
        self.janela_s = janela_s
        self._max_chaves = max_chaves
        self._relogio = relogio
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    def verificar(self, chave: str) -> Resultado:
        agora = self._relogio()
        inicio = agora - self.janela_s
        with self._lock:
            atuais = [t for t in self._hits.pop(chave, []) if t > inicio]
            if len(atuais) >= self.limite:
                self._hits[chave] = atuais  # reinsere no fim (mais recente)
                espera = max(0.0, atuais[0] + self.janela_s - agora)
                return Resultado(False, max(1, math.ceil(espera)))
            atuais.append(agora)
            self._hits[chave] = atuais
            while len(self._hits) > self._max_chaves:
                self._hits.popitem(last=False)
            return Resultado(True)

    def limpar(self) -> None:
        with self._lock:
            self._hits.clear()
