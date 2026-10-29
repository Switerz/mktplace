"""Checagem SEGURA da publicacao das metas (METAS-1, migration 032).

    python tools/metas_deploy_check.py antes    # antes de `alembic upgrade 032`
    python tools/metas_deploy_check.py depois   # depois do upgrade

Usa a `DATABASE_URL` do ambiente (ou do `.env` de `apps/api`), a MESMA do
Render: assim o que se verifica e' o papel que a API vai usar.

O QUE ESTE SCRIPT NUNCA FAZ
---------------------------
Nao imprime URL, host, usuario ou senha, nem a mensagem de excecao (o texto
das excecoes do SQLAlchemy pode carregar o DSN): so' o nome da classe. Nao
aplica migration. O teste de escrita do modo `depois` roda dentro de uma
transacao que termina em ROLLBACK — nada fica gravado, nem no historico.

SAIDA
-----
Uma linha `OK`/`FALHA` por verificacao e codigo de saida 0 so' quando tudo
passou.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402

TABELA = "marts.meta_gmv_mensal"
HISTORICO = "marts.meta_gmv_mensal_historico"
SEQUENCIA = "marts.meta_gmv_mensal_historico_id_seq"
VERSAO_ANTES = "031"
VERSAO_DEPOIS = "032"


def main(modo: str) -> int:
    falhas = 0

    def checa(rotulo: str, ok: bool, detalhe: str = "") -> None:
        nonlocal falhas
        print(f"{'OK   ' if ok else 'FALHA'}  {rotulo}{f' ({detalhe})' if detalhe else ''}")
        if not ok:
            falhas += 1

    try:
        eng = create_engine(settings.database_url, pool_pre_ping=True)
        con = eng.connect()
    except Exception as exc:  # noqa: BLE001 — fronteira; so' o nome da classe
        checa("conexao com o banco", False, type(exc).__name__)
        return 1
    checa("conexao com o banco", True)

    with con:
        versao = con.execute(text("SELECT version_num FROM alembic_version")).scalar()
        esperada = VERSAO_ANTES if modo == "antes" else VERSAO_DEPOIS
        checa(f"alembic_version = {esperada}", versao == esperada, f"encontrada {versao}")
        if modo == "antes" and versao != VERSAO_ANTES:
            print(
                "       PARE: o upgrade so' e' seguro a partir da 031. Abaixo dela, "
                "`alembic upgrade 032` aplicaria tambem migrations de outras frentes."
            )

        existe = {t: con.execute(text("SELECT to_regclass(:t)"), {"t": t}).scalar() is not None for t in (TABELA, HISTORICO)}

        if modo == "antes":
            checa("tabelas de metas ainda nao existem", not any(existe.values()))
            pode_criar = con.execute(
                text("SELECT has_schema_privilege(current_user, 'marts', 'CREATE')")
            ).scalar()
            checa("papel atual pode criar tabela em marts (para a migration)", bool(pode_criar))
            return 1 if falhas else 0

        for t, ok in existe.items():
            checa(f"tabela {t} existe", ok)
        if not all(existe.values()):
            return 1

        privilegios = [
            (TABELA, "TABLE", "SELECT"), (TABELA, "TABLE", "INSERT"),
            (TABELA, "TABLE", "UPDATE"), (TABELA, "TABLE", "DELETE"),
            (HISTORICO, "TABLE", "SELECT"), (HISTORICO, "TABLE", "INSERT"),
            (SEQUENCIA, "SEQUENCE", "USAGE"),
        ]
        for obj, tipo, priv in privilegios:
            fn = "has_table_privilege" if tipo == "TABLE" else "has_sequence_privilege"
            ok = con.execute(text(f"SELECT {fn}(current_user, :o, :p)"), {"o": obj, "p": priv}).scalar()
            checa(f"papel da API tem {priv} em {obj}", bool(ok))

        # Escrita real, desfeita no fim: prova o caminho que a API usa (upsert,
        # historico e lock consultivo) sem deixar rastro.
        # As consultas acima abriram transacao implicita (autobegin): fecha-la
        # antes, para o teste ter uma transacao propria que termina em ROLLBACK.
        con.rollback()
        trans = con.begin()
        try:
            con.execute(text("SELECT pg_advisory_xact_lock(31999999)"))
            con.execute(text(
                f"INSERT INTO {TABELA} (mes, brand, channel, meta_gmv) VALUES ('2030-12-01', 'apice', 'tiktok', 1)"
                " ON CONFLICT (mes, brand, channel) DO UPDATE SET meta_gmv = EXCLUDED.meta_gmv"
            ))
            con.execute(text(
                f"INSERT INTO {HISTORICO} (mes, brand, channel, meta_anterior, meta_nova)"
                " VALUES ('2030-12-01', 'apice', 'tiktok', NULL, 1)"
            ))
            lido = con.execute(text(f"SELECT meta_gmv FROM {TABELA} WHERE mes = '2030-12-01' AND brand = 'apice' AND channel = 'tiktok'")).scalar()
            checa("escrita de teste (desfeita) funciona", lido is not None)
        except Exception as exc:  # noqa: BLE001
            checa("escrita de teste (desfeita) funciona", False, type(exc).__name__)
        finally:
            trans.rollback()
        sobrou = con.execute(text(f"SELECT count(*) FROM {TABELA} WHERE mes = '2030-12-01'")).scalar()
        checa("nada ficou gravado pelo teste", sobrou == 0, f"{sobrou} linha(s)")

    return 1 if falhas else 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("antes", "depois"):
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
