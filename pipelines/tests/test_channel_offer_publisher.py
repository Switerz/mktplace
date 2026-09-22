"""Gate PMA-2C3A — executor auditavel: maquina de estados e ordem operacional.

Os fakes reproduzem o que o driver REALMENTE faz: `cursor.rowcount` depois de
cada comando, `commit()` que pode levantar, e o ciclo do advisory lock com
`pg_try_advisory_lock` devolvendo booleano. Um fake que apenas registrasse
chamadas deixaria passar erro de reconciliacao e de rowcount.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from pipelines import channel_offer_publisher as pub
from pipelines import channel_offer_sync as cos

HOJE = date(2026, 9, 16)
CEDO = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
TARDE = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)

APICE = cos.PublicationScope("shopee", HOJE, "apice")
BARBOURS = cos.PublicationScope("shopee", HOJE, "barbours")
LESCENT = cos.PublicationScope("shopee", HOJE, "lescent")
TIKTOK = cos.PublicationScope("tiktok", HOJE, "tiktok")


def registro(conta="apice", chave="900", canal="shopee"):
    """Registro completo: todas as colunas de `RECORD_COLUMNS`."""
    base = {c: None for c in cos.RECORD_COLUMNS}
    base.update({
        "observed_date": HOJE, "observed_at": TARDE, "marketplace": canal,
        "brand": conta, "offer_key": chave, "parent_item_id": chave,
        "shop_account": conta, "observation_mode": "snapshot_current",
        "snapshot_status": "current", "account_watermark_at": TARDE,
        "is_active": True, "product_type": "no_kit_signal",
        "product_type_source": "channel_flag",
        "observed_price_source": "current_price",
        "promo_context": "available", "business_scope": "in_scope",
    })
    return base


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class CursorFake:
    """Modela `rowcount` e `fetchone` por tipo de comando."""

    def __init__(self, conn):
        self._conn = conn
        self.rowcount = None

    def execute(self, sql, params=None):
        texto = " ".join(str(sql).split())
        self._conn.comandos.append((texto[:40], params))
        alto = texto.upper()
        if "PG_TRY_ADVISORY_LOCK" in alto:
            self._resposta = (self._conn.lock_disponivel,)
            self.rowcount = 1
        elif "PG_ADVISORY_UNLOCK" in alto:
            self._conn.lock_liberado = True
            self._resposta = (True,)
            self.rowcount = 1
        elif alto.startswith("DELETE"):
            # O escopo apagado vem do SQL, NAO dos params. Ler os params faria
            # o fake se comportar como se o filtro existisse mesmo quando ele
            # foi removido da consulta — e um DELETE sem `shop_account`
            # passaria despercebido. Este fake apaga o que a clausula WHERE
            # realmente restringe.
            filtros = {}
            if "MARKETPLACE" in alto:
                filtros["marketplace"] = params["marketplace"]
            if "OBSERVED_DATE" in alto:
                filtros["observed_date"] = params["observed_date"]
            if "SHOP_ACCOUNT" in alto:
                filtros["shop_account"] = params["shop_account"]
            atingidos = [
                chave for chave in list(self._conn.existentes)
                if all((
                    filtros.get("marketplace", chave[0]) == chave[0],
                    filtros.get("observed_date", chave[1]) == chave[1],
                    filtros.get("shop_account", chave[2]) == chave[2],
                ))
            ]
            self.rowcount = sum(self._conn.existentes.pop(k) for k in atingidos)
            # `alvo_do_delete` e' o ESCOPO que a clausula WHERE restringe —
            # registrado mesmo quando nenhuma linha existia, porque o comando
            # foi emitido de qualquer forma. `apagados` guarda so' o que tinha
            # linha. Confundir os dois esconderia um DELETE largo demais numa
            # tabela vazia.
            self._conn.alvo_do_delete.append(tuple(
                filtros.get(campo) for campo in
                ("marketplace", "observed_date", "shop_account")))
            self._conn.apagados.extend(atingidos)
        elif alto.startswith("SELECT COUNT"):
            escopo = (params["marketplace"], params["observed_date"],
                      params["shop_account"])
            self._resposta = (self._conn.inseridos_por_escopo.get(escopo, 0),)
            self.rowcount = 1
        elif alto.startswith("INSERT INTO AUDIT"):
            self._conn.audit_rows.append(params)
            self._resposta = (self._conn.proximo_sync_run_id,)
            self.rowcount = 1
        elif alto.startswith("UPDATE AUDIT"):
            self._conn.audit_updates.append(params)
            self.rowcount = self._conn.audit_update_rowcount
        else:
            self._resposta = (None,)
            self.rowcount = 0

    def fetchone(self):
        return self._resposta

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


class ConexaoFake:
    def __init__(self, lock_disponivel=True, commit_levanta=None,
                 existentes=None, audit_update_rowcount=1):
        self.lock_disponivel = lock_disponivel
        self.lock_liberado = False
        self.commit_levanta = commit_levanta
        self.commits = 0
        self.rollbacks = 0
        self.comandos = []
        self.apagados = []
        self.alvo_do_delete = []
        self.existentes = dict(existentes or {})
        self.inseridos_por_escopo = {}
        self.audit_rows = []
        self.audit_updates = []
        self.audit_update_rowcount = audit_update_rowcount
        self.proximo_sync_run_id = 4242

    def cursor(self, **kw):
        return CursorFake(self)

    def commit(self):
        self.commits += 1
        if self.commit_levanta is not None:
            raise self.commit_levanta

    def rollback(self):
        self.rollbacks += 1


def _monkey_execute_values(monkeypatch, conn_destino):
    """`execute_values` real e' do psycopg2; aqui ele alimenta a reconciliacao."""
    def falso(cur, sql, argslist, *a, **kw):
        cur.rowcount = len(argslist)
        for tupla in argslist:
            d = dict(zip(cos.RECORD_COLUMNS, tupla))
            chave = (d["marketplace"], d["observed_date"], d["shop_account"])
            conn_destino.inseridos_por_escopo[chave] = (
                conn_destino.inseridos_por_escopo.get(chave, 0) + 1)
    monkeypatch.setattr(pub, "execute_values", falso)


def _plano(records=(), scopes=(APICE,), permitido=True, motivo=None):
    decisao = (cos.PublishDecision(cos.PUBLISH_ALLOW) if permitido
               else cos.PublishDecision(cos.PUBLISH_REFUSE, motivo))
    return cos.PublicationPlan("shopee", tuple(scopes), tuple(records), decisao)


def _roda(destino, auditoria, plano, monkeypatch, gate_reader=None):
    _monkey_execute_values(monkeypatch, destino)

    def build(gate):
        gate.read("fonte", lambda: None)
        if gate_reader:
            gate_reader(gate)
        return plano

    return pub.run_publication(
        target_conn=destino, audit_conn=auditoria,
        build_plan=build, rows_extracted_of=lambda p: len(p.records) + 7,
    )


# ---------------------------------------------------------------------------
# Lock e ordem operacional
# ---------------------------------------------------------------------------


def test_lock_ocupado_encerra_sem_ler_e_sem_escrever(monkeypatch):
    destino = ConexaoFake(lock_disponivel=False)
    auditoria = ConexaoFake()
    leu = []

    def build(gate):
        leu.append(True)
        return _plano()

    r = pub.run_publication(target_conn=destino, audit_conn=auditoria,
                            build_plan=build, rows_extracted_of=lambda p: 0)
    assert r.state == pub.STATE_LOCK_UNAVAILABLE
    assert leu == []
    assert destino.commits == 0 and destino.apagados == []
    assert auditoria.audit_rows == []


def test_a_fonte_so_e_lida_depois_do_lock(monkeypatch):
    destino = ConexaoFake()
    auditoria = ConexaoFake()
    ordem = []
    original = cos.try_acquire_publication_lock

    def espia(conn):
        ordem.append("lock")
        return original(conn)

    monkeypatch.setattr(cos, "try_acquire_publication_lock", espia)
    _monkey_execute_values(monkeypatch, destino)

    def build(gate):
        gate.read("fonte", lambda: ordem.append("leitura"))
        return _plano([registro()])

    pub.run_publication(target_conn=destino, audit_conn=auditoria,
                        build_plan=build, rows_extracted_of=lambda p: 1)
    assert ordem == ["lock", "leitura"]


def test_ler_a_fonte_antes_do_lock_falha_alto():
    gate = pub.SourceGate()
    with pytest.raises(pub.SourceReadBeforeLockError):
        gate.read("fonte", lambda: None)
    gate.mark_locked()
    assert gate.read("fonte", lambda: 7) == 7


def test_o_lock_e_liberado_em_todos_os_desfechos(monkeypatch):
    cenarios = [
        ConexaoFake(),
        ConexaoFake(commit_levanta=RuntimeError("queda")),
    ]
    for destino in cenarios:
        _roda(destino, ConexaoFake(), _plano([registro()]), monkeypatch)
        assert destino.lock_liberado is True


def test_lock_liberado_mesmo_quando_o_plano_recusa(monkeypatch):
    destino = ConexaoFake()
    _roda(destino, ConexaoFake(), _plano(permitido=False, motivo="x"), monkeypatch)
    assert destino.lock_liberado is True


def test_lock_liberado_na_mesma_conexao(monkeypatch):
    destino = ConexaoFake()
    _roda(destino, ConexaoFake(), _plano([registro()]), monkeypatch)
    comandos = [c for c, _ in destino.comandos]
    assert any("pg_try_advisory_lock" in c for c in comandos)
    assert any("pg_advisory_unlock" in c for c in comandos)


# ---------------------------------------------------------------------------
# Publicacao e escopo
# ---------------------------------------------------------------------------


def test_publicacao_feliz_comita_uma_vez(monkeypatch):
    destino = ConexaoFake(existentes={("shopee", HOJE, "apice"): 5})
    auditoria = ConexaoFake()
    r = _roda(destino, auditoria, _plano([registro()]), monkeypatch)
    assert r.state == pub.STATE_PUBLISHED
    assert r.rows_loaded == 1
    assert destino.commits == 1 and destino.rollbacks == 0
    assert r.audit_complete is True


def test_conta_saudavel_vazia_apaga_somente_a_propria_fotografia(monkeypatch):
    destino = ConexaoFake(existentes={("shopee", HOJE, "apice"): 12,
                                      ("shopee", HOJE, "barbours"): 30})
    r = _roda(destino, ConexaoFake(), _plano([], scopes=(APICE,)), monkeypatch)
    assert r.state == pub.STATE_PUBLISHED
    assert r.rows_loaded == 0
    assert destino.apagados == [("shopee", HOJE, "apice")]
    # barbours nao foi tocada
    assert ("shopee", HOJE, "barbours") in destino.existentes


def test_uma_conta_nao_apaga_a_outra(monkeypatch):
    destino = ConexaoFake(existentes={("shopee", HOJE, "apice"): 10,
                                      ("shopee", HOJE, "barbours"): 20,
                                      ("shopee", HOJE, "lescent"): 30})
    _roda(destino, ConexaoFake(),
          _plano([registro("barbours", "901")], scopes=(BARBOURS,)), monkeypatch)
    assert destino.apagados == [("shopee", HOJE, "barbours")]
    assert ("shopee", HOJE, "apice") in destino.existentes
    assert ("shopee", HOJE, "lescent") in destino.existentes


def test_conta_indisponivel_nao_entra_no_plano_e_preserva_a_fotografia(monkeypatch):
    """Fonte indisponivel vira recusa: zero escopos, zero DELETE."""
    destino = ConexaoFake(existentes={("shopee", HOJE, "apice"): 99})
    plano = cos.build_publication_plan(
        marketplace="shopee", records=[], healthy_scopes={APICE},
        incoming_watermarks={APICE: TARDE}, published_watermarks={APICE: CEDO},
        channel_enabled=True, source_available=False)
    r = _roda(destino, ConexaoFake(), plano, monkeypatch)
    assert r.state == pub.STATE_REFUSED
    assert destino.apagados == []
    assert destino.existentes[("shopee", HOJE, "apice")] == 99


def test_conta_que_nao_executou_nao_e_apagada(monkeypatch):
    destino = ConexaoFake(existentes={("shopee", HOJE, "lescent"): 42})
    _roda(destino, ConexaoFake(),
          _plano([registro("apice")], scopes=(APICE,)), monkeypatch)
    assert ("shopee", HOJE, "lescent") in destino.existentes


def test_oferta_removida_da_origem_nao_sobrevive(monkeypatch):
    """O escopo inteiro e' apagado antes do insert."""
    destino = ConexaoFake(existentes={("shopee", HOJE, "apice"): 3})
    r = _roda(destino, ConexaoFake(),
              _plano([registro("apice", "900")], scopes=(APICE,)), monkeypatch)
    assert destino.alvo_do_delete == [("shopee", HOJE, "apice")]
    assert r.rows_loaded == 1


def test_multiplas_contas_em_uma_transacao(monkeypatch):
    destino = ConexaoFake()
    r = _roda(destino, ConexaoFake(),
              _plano([registro("apice", "900"), registro("barbours", "901")],
                     scopes=(APICE, BARBOURS)), monkeypatch)
    assert r.state == pub.STATE_PUBLISHED
    assert r.rows_loaded == 2
    assert destino.commits == 1
    assert set(destino.alvo_do_delete) == {("shopee", HOJE, "apice"),
                                           ("shopee", HOJE, "barbours")}


def test_tiktok_usa_o_escopo_canonico(monkeypatch):
    destino = ConexaoFake()
    plano = cos.PublicationPlan(
        "tiktok", (TIKTOK,), (registro("tiktok", "1731", "tiktok"),),
        cos.PublishDecision(cos.PUBLISH_ALLOW))
    r = _roda(destino, ConexaoFake(), plano, monkeypatch)
    assert r.state == pub.STATE_PUBLISHED
    assert destino.alvo_do_delete == [("tiktok", HOJE, "tiktok")]


# ---------------------------------------------------------------------------
# Atomicidade
# ---------------------------------------------------------------------------


def test_delete_e_insert_nao_podem_ser_commitados_separadamente(monkeypatch):
    """Um unico `commit()` cobre os dois; nao ha commit entre eles."""
    destino = ConexaoFake()
    _roda(destino, ConexaoFake(),
          _plano([registro()], scopes=(APICE,)), monkeypatch)
    assert destino.commits == 1
    # o DELETE acontece, o INSERT acontece, e so' depois vem o unico commit
    tipos = [c.split()[0].upper() for c, _ in destino.comandos]
    assert "DELETE" in tipos
    assert destino.commits == 1


def test_execute_plan_nao_comita(monkeypatch):
    destino = ConexaoFake()
    _monkey_execute_values(monkeypatch, destino)
    pub.execute_plan(destino, _plano([registro()]))
    assert destino.commits == 0


def test_falha_no_insert_reverte_o_delete(monkeypatch):
    destino = ConexaoFake(existentes={("shopee", HOJE, "apice"): 7})

    def explode(cur, sql, argslist, *a, **kw):
        raise RuntimeError("insert falhou")

    monkeypatch.setattr(pub, "execute_values", explode)

    def build(gate):
        gate.mark_locked()
        return _plano([registro()])

    r = pub.run_publication(target_conn=destino, audit_conn=ConexaoFake(),
                            build_plan=build, rows_extracted_of=lambda p: 1)
    assert r.state == pub.STATE_ROLLED_BACK
    assert destino.rollbacks == 1
    assert destino.commits == 0
    assert r.rows_loaded == 0


def test_reconciliacao_pre_commit_pega_divergencia(monkeypatch):
    destino = ConexaoFake()

    def insere_de_menos(cur, sql, argslist, *a, **kw):
        cur.rowcount = len(argslist)
        # nao alimenta `inseridos_por_escopo`: a contagem vera' zero

    monkeypatch.setattr(pub, "execute_values", insere_de_menos)
    with pytest.raises(pub.PublisherError):
        pub.execute_plan(destino, _plano([registro()]))


def test_plano_recusado_nunca_e_executado():
    with pytest.raises(pub.PublisherError):
        pub.execute_plan(ConexaoFake(), _plano(permitido=False, motivo="x"))


# ---------------------------------------------------------------------------
# Commit indeterminado e auditoria
# ---------------------------------------------------------------------------


def test_commit_indeterminado_nao_vira_failed_e_nao_faz_rollback(monkeypatch):
    destino = ConexaoFake(commit_levanta=RuntimeError("conexao caiu no commit"))
    auditoria = ConexaoFake()
    r = _roda(destino, auditoria, _plano([registro()]), monkeypatch)
    assert r.state == pub.STATE_INDETERMINATE
    assert r.data_state_known is False
    # rollback NAO e' tentado: ele nao desfaria um commit possivelmente aplicado
    assert destino.rollbacks == 0
    # a auditoria mantem `running` e escreve a nota
    nota = auditoria.audit_updates[-1]
    assert "INDETERMINADO:" in nota[0]
    assert pub.AUDIT_FAILED not in [p for p in nota if isinstance(p, str)]


def test_commit_indeterminado_nao_gera_retry(monkeypatch):
    destino = ConexaoFake(commit_levanta=RuntimeError("queda"))
    _roda(destino, ConexaoFake(), _plano([registro()]), monkeypatch)
    assert destino.commits == 1  # tentado UMA vez, nunca repetido


def test_commit_confirmado_nunca_vira_failed(monkeypatch):
    destino = ConexaoFake()
    auditoria = ConexaoFake()
    r = _roda(destino, auditoria, _plano([registro()]), monkeypatch)
    assert r.state == pub.STATE_PUBLISHED
    status = [p[0] for p in auditoria.audit_updates]
    assert pub.AUDIT_SUCCESS in status
    assert pub.AUDIT_FAILED not in status


def test_auditoria_incompleta_apos_commit_preserva_a_verdade(monkeypatch):
    """Os dados estao publicados; falhar ao auditar nao os desfaz."""
    destino = ConexaoFake()
    auditoria = ConexaoFake(audit_update_rowcount=0)  # UPDATE nao acha a linha
    r = _roda(destino, auditoria, _plano([registro()]), monkeypatch)
    assert r.state == pub.STATE_PUBLISHED
    assert r.data_published is True
    assert r.audit_complete is False
    assert destino.commits == 1


def test_rows_extracted_e_rows_loaded_sao_grandezas_distintas(monkeypatch):
    destino = ConexaoFake()
    auditoria = ConexaoFake()
    r = _roda(destino, auditoria, _plano([registro()]), monkeypatch)
    assert r.rows_extracted == 1 + 7   # o que foi LIDO
    assert r.rows_loaded == 1          # o que foi PUBLICADO
    assert r.rows_extracted != r.rows_loaded


def test_escopo_saudavel_vazio_registra_rows_loaded_zero(monkeypatch):
    destino = ConexaoFake()
    auditoria = ConexaoFake()
    r = _roda(destino, auditoria, _plano([], scopes=(APICE,)), monkeypatch)
    assert r.rows_loaded == 0
    assert auditoria.audit_updates[-1][1] == 0  # rows_loaded do UPDATE


def test_a_recusa_deixa_rastro_auditavel(monkeypatch):
    auditoria = ConexaoFake()
    r = _roda(ConexaoFake(), auditoria,
              _plano(permitido=False, motivo=cos.REFUSE_WATERMARK_REGRESSION),
              monkeypatch)
    assert r.state == pub.STATE_REFUSED
    assert auditoria.audit_rows, "a tentativa recusada precisa deixar rastro"
    assert cos.REFUSE_WATERMARK_REGRESSION in auditoria.audit_updates[-1][2]


def test_auditoria_usa_conexao_independente(monkeypatch):
    """Se fosse a mesma, o rollback dos dados apagaria o rastro."""
    destino = ConexaoFake()
    auditoria = ConexaoFake()
    _roda(destino, auditoria, _plano([registro()]), monkeypatch)
    assert auditoria.commits >= 2  # start + finish, com commit proprio
    assert destino.commits == 1


def test_status_de_auditoria_fora_do_dominio_falha_alto():
    with pytest.raises(pub.PublisherError):
        pub.audit_finish(ConexaoFake(), 1, "indeterminate")


def test_update_de_auditoria_que_nao_acha_a_linha_falha_alto():
    with pytest.raises(pub.PublisherError):
        pub.audit_finish(ConexaoFake(audit_update_rowcount=0), 1,
                         pub.AUDIT_SUCCESS)


def test_batch_id_nao_e_inventado(monkeypatch):
    destino = ConexaoFake()
    r = registro()
    assert r["batch_id"] is None
    _roda(destino, ConexaoFake(), _plano([r]), monkeypatch)
    # a coluna viaja nula, nao preenchida
    assert r["batch_id"] is None


def test_nenhuma_linha_sentinela_para_escopo_vazio(monkeypatch):
    destino = ConexaoFake()
    _roda(destino, ConexaoFake(), _plano([], scopes=(APICE,)), monkeypatch)
    assert destino.inseridos_por_escopo == {}


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------


def test_detalhe_de_excecao_externa_nao_vaza_nada():
    msg = pub._detalhe(RuntimeError("host=10.0.0.1 user=admin senha=xyz"))
    for vazamento in ("10.0.0.1", "admin", "xyz", "senha"):
        assert vazamento not in msg


def test_detalhe_preserva_mensagem_propria_do_modulo():
    msg = pub._detalhe(cos.ChannelSyncError("colisao de chave de oferta"))
    assert "colisao de chave de oferta" in msg


def test_estados_sao_exatamente_os_cinco():
    assert set(pub.PUBLICATION_STATES) == {
        "lock_unavailable", "refused", "published", "rolled_back",
        "indeterminate"}


def test_o_insert_usa_a_ordem_de_record_columns():
    for coluna in cos.RECORD_COLUMNS:
        assert coluna in pub.SQL_INSERT_OFFERS
    assert pub.SQL_INSERT_OFFERS.startswith(f"INSERT INTO {cos.TARGET_TABLE}")
    assert "SELECT" not in pub.SQL_INSERT_OFFERS.upper()


def test_a_cli_nao_publica_sem_apply(monkeypatch, capsys):
    """Gate PMA-2C5C-H1 — o dry-run deixou de ser `print` e `return 0`.

    Agora ele LE a fonte e monta a candidata, entao o teste precisa dar-lhe uma
    fonte. O que continua garantido, e e' o ponto: nenhuma conexao de destino,
    nenhum lock, nenhuma auditoria — e a saida nunca diz "publicado".
    """
    monkeypatch.setattr(pub, "run_diagnose",
                        lambda args, **k: {
                            "mode": "dry_run", "marketplace": args.marketplace,
                            "observed_dates": ["2026-09-22"], "rows": 2,
                            "accounts": ["apice"], "brands": ["apice"],
                            "statuses": {"current": 2}, "prices_absent": 0,
                            "prices_zero": 0, "fingerprint": "f" * 32,
                            "scopes": [], "accounts_seen": ["apice"]})
    assert pub.main(["--marketplace", "shopee"]) == pub.EXIT_OK
    saida = capsys.readouterr().out
    assert "mode=dry_run" in saida
    assert "CANDIDATA VALIDADA" in saida
    assert "publicad" not in saida.replace("CANDIDATA VALIDADA", "").lower()


def test_a_cli_sem_apply_falha_quando_a_fonte_nao_responde(monkeypatch):
    """Fail-closed: ensaio sem fonte NAO pode devolver 0 fingindo sucesso."""
    monkeypatch.delenv("DATAMART_DATABASE_URL", raising=False)
    assert pub.main(["--marketplace", "shopee"]) == pub.EXIT_FAILED


def test_a_cli_apply_alcanca_o_executor(monkeypatch, capsys):
    """Gate PMA-2C3A-R: `--apply` deixou de ser recusa incondicional.

    Antes, este teste afirmava que a CLI sempre recusava — e por isso
    `run_publication` era inalcancavel por caminho produtivo. Agora ele afirma
    o contrario: `--apply` chega ao executor. A recusa que sobra vem da
    PRECONDICAO real (migration 017 e relacao), verificada contra o banco.
    """
    alcancou = []

    def falso_run_apply(args, **kwargs):
        alcancou.append(args.marketplace)
        return pub.PublicationOutcome(
            pub.STATE_PUBLISHED, cos.PublishDecision(cos.PUBLISH_ALLOW),
            rows_extracted=3, rows_loaded=3)

    monkeypatch.setattr(pub, "run_apply", falso_run_apply)
    assert pub.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_OK
    assert alcancou == ["shopee"]
    assert "PUBLICADO" in capsys.readouterr().out


def test_a_cli_recusa_quando_a_precondicao_do_banco_falha(monkeypatch, capsys):
    """A recusa continua existindo — mas vinda do banco, nao de um `return`."""
    def explode(args, **kwargs):
        raise cos.ApplyNotAuthorizedError(
            "publicacao nao autorizada: a relacao de destino nao existe")

    monkeypatch.setattr(pub, "run_apply", explode)
    assert pub.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_REFUSED
    assert "RECUSADO" in capsys.readouterr().err


def test_o_fake_de_delete_honra_a_clausula_where(monkeypatch):
    """Contraprova do proprio fake: sem `shop_account` no SQL, ele apaga tudo.

    Sem esta propriedade, o teste `test_uma_conta_nao_apaga_a_outra` passaria
    mesmo com o filtro de conta removido da consulta.
    """
    destino = ConexaoFake(existentes={("shopee", HOJE, "apice"): 1,
                                      ("shopee", HOJE, "barbours"): 1})
    cur = CursorFake(destino)
    cur.execute(cos.SQL_DELETE_SCOPE, {"marketplace": "shopee",
                                       "observed_date": HOJE,
                                       "shop_account": "apice"})
    assert destino.apagados == [("shopee", HOJE, "apice")]

    destino2 = ConexaoFake(existentes={("shopee", HOJE, "apice"): 1,
                                       ("shopee", HOJE, "barbours"): 1})
    sem_conta = cos.SQL_DELETE_SCOPE.replace(
        "   AND shop_account  = %(shop_account)s", "")
    cur2 = CursorFake(destino2)
    cur2.execute(sem_conta, {"marketplace": "shopee", "observed_date": HOJE,
                             "shop_account": "apice"})
    assert set(destino2.apagados) == {("shopee", HOJE, "apice"),
                                      ("shopee", HOJE, "barbours")}
