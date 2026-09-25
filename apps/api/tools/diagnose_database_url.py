"""Diagnostico SEGURO da inicializacao do banco da API.

    python tools/diagnose_database_url.py

Feito para rodar no Shell do servico no Render, onde o problema vive. Nao
escreve nada, nao altera variavel e nao abre transacao alem de `SELECT 1`.

O QUE ESTE SCRIPT NUNCA IMPRIME
--------------------------------
URL, DSN, host, porta, usuario, banco, senha, valores de query string, ou a
MENSAGEM de qualquer excecao.

O ultimo item nao e' zelo excessivo: `create_engine` levanta `ArgumentError`
cujo texto e' "Could not parse SQLAlchemy URL from string '<a URL inteira>'".
Imprimir `str(exc)` publicaria a senha no log. Por isso so' sai
`type(exc).__name__`.

O QUE ELE IMPRIME
-----------------
Presenca, comprimento, formato e resultado de cada etapa — o suficiente para
decidir a correcao sem revelar o segredo.

Saida em blocos ROTULADOS, feita para ser colada de volta num chat sem
revisao linha a linha.
"""

from __future__ import annotations

import os
import sys

VARIAVEL = "DATABASE_URL"

#: Prefixos que denunciam valor colado errado no painel.
PREFIXOS_SUSPEITOS = (
    "DATABASE_URL=", "database_url=", "psql ", "psql:", "export ",
)

#: VOCABULARIO FECHADO do esquema. Nada fora desta lista e' impresso.
#:
#: Derivar o rotulo do valor — `valor.split("://")[0]` — publica texto
#: arbitrario quando o valor esta malformado: um wrapper de shell, um prefixo
#: colado ou uma senha antes do `@` sairiam no relatorio (finding F2).
#: Qualquer coisa que nao case EXATAMENTE vira `nao_reconhecido`.
ESQUEMAS_CONHECIDOS = ("postgresql", "postgresql+psycopg2", "postgresql+psycopg")
ESQUEMA_AUSENTE = "ausente"
ESQUEMA_NAO_RECONHECIDO = "nao_reconhecido"

#: Destes, so' os dois primeiros funcionam com o driver que o projeto instala.
ESQUEMAS_SERVIVEIS = ("postgresql", "postgresql+psycopg2")

#: Chaves de query que a ferramenta pode NOMEAR. Qualquer outra e' contada, nao
#: nomeada: num valor malformado, "a chave" pode ser texto arbitrario escolhido
#: por quem escreveu o valor.
QUERY_CONHECIDAS = ("sslmode", "sslrootcert", "application_name",
                    "connect_timeout", "options", "channel_binding")

#: MESMO vocabulario fechado, aplicado ao `drivername` que o SQLAlchemy extrai.
#:
#: Fechar so' o esquema nao bastava. A regex de URL do SQLAlchemy aceita
#: QUALQUER nome de esquema formado por `[\w+]`, entao `make_url` atravessa um
#: valor hostil sem levantar e devolve o texto inteiro em `drivername`. Medido:
#: `SEGREDO://u@h/d` -> drivername `'SEGREDO'`, e
#: `postgresql+SEGREDO://u@h/d` -> drivername `'postgresql+SEGREDO'`. Imprimir
#: esse campo cru republicava exatamente o que o rotulo do esquema recusava
#: (finding F2, segunda rodada).
DRIVERS_CONHECIDOS = ESQUEMAS_CONHECIDOS
DRIVER_AUSENTE = ESQUEMA_AUSENTE
DRIVER_NAO_RECONHECIDO = ESQUEMA_NAO_RECONHECIDO


def _rotulo_fechado(candidato: str | None, conhecidos: tuple[str, ...],
                    ausente: str, nao_reconhecido: str) -> str:
    """Devolve `candidato` somente se ele estiver em `conhecidos`.

    Unico ponto do programa autorizado a decidir um rotulo. Ou o valor casa
    EXATAMENTE com um item da lista — e entao o que sai e' o item da lista, nao
    o valor — ou sai um rotulo constante. Nao ha caminho que devolva texto
    derivado da entrada.
    """
    if candidato is None or candidato.strip() == "":
        return ausente
    return candidato if candidato in conhecidos else nao_reconhecido


def rotulo_do_esquema(valor: str | None) -> str:
    """Rotulo do esquema, SEMPRE tirado de `ESQUEMAS_CONHECIDOS`.

    Nao devolve pedaco nenhum do valor: ou casa exatamente com um esquema
    conhecido, ou e' `nao_reconhecido`.
    """
    if valor is None or valor.strip() == "":
        return ESQUEMA_AUSENTE
    if "://" not in valor:
        return ESQUEMA_NAO_RECONHECIDO
    return _rotulo_fechado(
        valor.split("://", 1)[0], ESQUEMAS_CONHECIDOS,
        ESQUEMA_AUSENTE, ESQUEMA_NAO_RECONHECIDO,
    )


def rotulo_do_driver(drivername: str | None) -> str:
    """Rotulo do driver, SEMPRE tirado de `DRIVERS_CONHECIDOS`.

    Recebe o `url.drivername` ja' parseado pelo SQLAlchemy — que pode ser texto
    arbitrario, porque a regex de URL nao valida o nome do esquema.
    """
    return _rotulo_fechado(
        drivername, DRIVERS_CONHECIDOS, DRIVER_AUSENTE, DRIVER_NAO_RECONHECIDO,
    )


def sim_nao(v: bool) -> str:
    return "sim" if v else "nao"


def bloco(titulo: str) -> None:
    print("")
    print("=== " + titulo + " ===")


def main() -> int:
    bloco("1. A VARIAVEL")
    bruto = os.environ.get(VARIAVEL)
    presente = bruto is not None
    print("  presente no processo      : " + sim_nao(presente))
    if not presente:
        # Importante: ausencia PURA nao produz o 503. O Settings tem default
        # sintaticamente valido, entao o engine sobe e a falha vira erro de
        # CONEXAO. Se chegou aqui com "nao", o 503 tem outra causa.
        print("  -> AUSENTE. O default do Settings e' sintaticamente valido,")
        print("     entao isto NAO explica um 503 'Banco de dados indisponivel'.")
        print("     Procure outra causa (driver ausente, por exemplo).")
        return 2

    print("  comprimento (caracteres)  : " + str(len(bruto)))
    print("  vazia                     : " + sim_nao(bruto == ""))
    print("  so' espacos               : " + sim_nao(bruto != "" and bruto.strip() == ""))

    bloco("2. FORMATO DO VALOR")
    tem_esquerda = bruto != bruto.lstrip()
    tem_direita = bruto != bruto.rstrip()
    print("  espaco/quebra a ESQUERDA  : " + sim_nao(tem_esquerda)
          + ("   <- QUEBRA o parse" if tem_esquerda else ""))
    print("  espaco/quebra a DIREITA   : " + sim_nao(tem_direita)
          + ("   (tolerado pelo SQLAlchemy)" if tem_direita else ""))
    print("  quebra de linha interna   : " + sim_nao("\n" in bruto.strip()
                                                     or "\r" in bruto.strip()))
    aspas = bruto.startswith(('"', "'")) or bruto.endswith(('"', "'"))
    print("  aspas nas pontas          : " + sim_nao(aspas)
          + ("   <- QUEBRA o parse" if aspas else ""))
    pref = next((p for p in PREFIXOS_SUSPEITOS if bruto.lstrip().startswith(p)), None)
    print("  prefixo indevido          : " + (repr(pref) if pref else "nao")
          + ("   <- QUEBRA o parse" if pref else ""))

    # Vocabulario FECHADO: o rotulo sai de `ESQUEMAS_CONHECIDOS` ou e'
    # `nao_reconhecido`. Nenhum pedaco do valor e' impresso.
    limpo = bruto.strip().strip('"').strip("'")
    esquema = rotulo_do_esquema(limpo)
    print("  esquema                   : " + esquema)
    print("  esquema servivel hoje     : " + sim_nao(esquema in ESQUEMAS_SERVIVEIS))
    if esquema == ESQUEMA_NAO_RECONHECIDO:
        print("     -> Nao casa com nenhum esquema conhecido. As causas mais")
        print("        comuns sao `postgres://` (alias REMOVIDO no SQLAlchemy")
        print("        2.x), aspas, prefixo colado ou wrapper de shell.")
    elif esquema == "postgresql+psycopg":
        print("     -> Exige o driver `psycopg` (v3), que o projeto NAO instala.")

    bloco("3. PARSE PELO SQLALCHEMY")
    try:
        from sqlalchemy.engine import make_url
    except Exception as exc:  # noqa: BLE001
        print("  sqlalchemy indisponivel   : " + type(exc).__name__)
        return 1
    url = None
    try:
        url = make_url(bruto)
        print("  parse                     : OK")
    except Exception as exc:  # noqa: BLE001
        print("  parse                     : FALHOU (" + type(exc).__name__ + ")")

    if url is not None:
        # Vocabulario FECHADO, pelo mesmo motivo do esquema: `drivername` e'
        # texto que veio do valor, nao uma constante do SQLAlchemy.
        driver = rotulo_do_driver(url.drivername)
        print("  driver                    : " + driver)
        if driver == DRIVER_NAO_RECONHECIDO:
            print("     -> O SQLAlchemy parseou, mas o driver nao esta' na lista")
            print("        conhecida. `create_engine` vai levantar.")
        print("  usuario presente          : " + sim_nao(bool(url.username)))
        print("  senha presente            : " + sim_nao(bool(url.password)))
        print("  host presente             : " + sim_nao(bool(url.host)))
        print("  porta presente            : " + sim_nao(url.port is not None))
        print("  banco presente            : " + sim_nao(bool(url.database)))
        # Tambem vocabulario FECHADO. Imprimir o nome cru da chave publicaria
        # texto escolhido por quem escreveu o valor — e num valor malformado
        # "a chave" pode ser qualquer coisa. Reporta-se quais chaves CONHECIDAS
        # estao presentes, e quantas outras existem, sem nomea-las.
        chaves = set(url.query.keys())
        for conhecida in QUERY_CONHECIDAS:
            print("  query `%s`%s: %s"
                  % (conhecida, " " * max(1, 18 - len(conhecida)),
                     sim_nao(conhecida in chaves)))
        print("  outras chaves de query    : " + str(len(chaves - set(QUERY_CONHECIDAS))))

    bloco("4. DRIVER NO ARTEFATO")
    try:
        import psycopg2
        print("  psycopg2 importavel       : sim")
        print("  psycopg2.__version__      : " + str(getattr(psycopg2, "__version__", "?")))
    except Exception as exc:  # noqa: BLE001
        print("  psycopg2 importavel       : NAO (" + type(exc).__name__ + ")")
        print("     -> `create_engine` levanta e o engine nunca nasce.")

    bloco("5. CRIACAO DO ENGINE")
    eng = None
    try:
        from sqlalchemy import create_engine
        eng = create_engine(bruto, pool_pre_ping=True)
        print("  create_engine             : OK")
    except Exception as exc:  # noqa: BLE001
        print("  create_engine             : FALHOU (" + type(exc).__name__ + ")")
        print("     -> e' EXATAMENTE esta falha que zera o SessionLocal e")
        print("        produz 503 'Banco de dados indisponivel'.")

    bloco("6. SELECT 1")
    if eng is None:
        print("  nao executado             : sem engine")
    else:
        try:
            from sqlalchemy import text
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            print("  SELECT 1                  : OK")
        except Exception as exc:  # noqa: BLE001
            print("  SELECT 1                  : FALHOU (" + type(exc).__name__ + ")")
            print("     -> engine nasceu, banco nao respondeu. Isto NAO produz")
            print("        o 503 observado; produz erro de conexao.")

    bloco("7. COMO O APP ENXERGA")
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app.database import FALHA_DO_ENGINE, SessionLocal, engine  # noqa: PLC0415
        print("  engine do app             : " + ("criado" if engine is not None else "None"))
        print("  SessionLocal              : " + ("criado" if SessionLocal is not None else "None"))
        print("  categoria registrada      : " + str(FALHA_DO_ENGINE or "(nenhuma)"))
    except Exception as exc:  # noqa: BLE001
        print("  app nao importavel daqui  : " + type(exc).__name__)

    bloco("8. CONTEXTO DO SERVICO")
    print("  cwd                       : " + os.getcwd())
    print("  python                    : " + sys.version.split()[0])
    # Nomes, nunca valores. Serve para achar colisao com Environment Group.
    relacionadas = sorted(
        k for k in os.environ
        if "DATABASE" in k.upper() or k.upper().startswith("PG")
    )
    print("  variaveis relacionadas    : " + (", ".join(relacionadas) or "(nenhuma)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
