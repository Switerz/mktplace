"""Gate PMA-2C3A-R — fiacao operacional: a CLI alcanca o executor.

O gate anterior entregou o executor mas deixou `--apply` recusando
incondicionalmente. Estes testes provam que existe caminho produtivo, e travam
cada propriedade que a fiacao poderia quebrar em silencio.

Os fakes sao ESTRITOS: cada conexao registra o que recebeu, e nenhuma aceita
ser usada no papel de outra. Um fake permissivo deixaria passar exatamente o
defeito que mais importa aqui — a fonte cair para o destino, ou a auditoria
compartilhar a transacao dos dados.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

import pytest

from pipelines import channel_offer_publisher as pub
from pipelines import channel_offer_sync as cos

HOJE = date(2026, 9, 16)
CEDO = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
TARDE = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)

APICE = cos.PublicationScope("shopee", HOJE, "apice")
BARBOURS = cos.PublicationScope("shopee", HOJE, "barbours")


def _args(**kw):
    base = dict(marketplace="shopee", apply=True, operator_override=True,
                observed_date=None)
    base.update(kw)
    return argparse.Namespace(**base)


class ConexaoEstrita:
    """Recusa ser usada fora do proprio papel."""

    def __init__(self, papel, respostas=None):
        self.papel = papel
        self.fechada = False
        self.comandos = []
        self.respostas = respostas or {}

    def cursor(self, **kw):
        return CursorEstrito(self)

    def commit(self):
        self.comandos.append(("commit", None))

    def rollback(self):
        self.comandos.append(("rollback", None))

    def close(self):
        self.fechada = True


class CursorEstrito:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 1
        self._resposta = (None,)

    def execute(self, sql, params=None):
        texto = " ".join(str(sql).split())
        self._conn.comandos.append((texto[:48], params))
        alto = texto.upper()
        if "PG_TRY_ADVISORY_LOCK" in alto:
            self._resposta = (self._conn.respostas.get("lock", True),)
        elif "PG_ADVISORY_UNLOCK" in alto:
            self._resposta = (True,)
        elif "ALEMBIC_VERSION" in alto:
            self._resposta = [(self._conn.respostas.get("revisao", "017"),)]
        elif "TO_REGCLASS" in alto:
            self._resposta = (self._conn.respostas.get("relacao",
                                                       cos.TARGET_TABLE),)
        elif "DISTINCT SHOP_ACCOUNT" in alto:
            self._resposta = [(c,) for c in
                              self._conn.respostas.get("contas_publicadas", ())]
        elif "MAX(ACCOUNT_WATERMARK_AT)" in alto:
            self._resposta = list(self._conn.respostas.get("watermarks", ()))
        elif alto.startswith("INSERT INTO AUDIT"):
            self._resposta = (99,)
        else:
            self._resposta = (0,)

    def fetchone(self):
        if isinstance(self._resposta, list):
            return self._resposta[0] if self._resposta else None
        return self._resposta

    def fetchall(self):
        return self._resposta if isinstance(self._resposta, list) else []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


def _fabricas(monkeypatch, *, lock=True, revisao="017",
              relacao=cos.TARGET_TABLE, contas_publicadas=(), watermarks=()):
    destino = ConexaoEstrita("destino", {
        "lock": lock, "revisao": revisao, "relacao": relacao,
        "contas_publicadas": contas_publicadas, "watermarks": watermarks,
    })
    auditoria = ConexaoEstrita("auditoria")
    fonte = ConexaoEstrita("fonte")
    return destino, auditoria, fonte


def _roda_apply(monkeypatch, *, registros=None, contas_fonte=("apice",),
                relogios=None, **kw):
    destino, auditoria, fonte = _fabricas(monkeypatch, **kw)
    chamadas = {"run_publication": 0, "collect": 0}
    registros = registros if registros is not None else []
    relogios = relogios or {
        "apice": cos.AccountClock("shopee", "apice", TARDE, rows_seen=1)}

    def falso_collect(gate, source_conn, marketplace, observed_date=None):
        chamadas["collect"] += 1
        assert source_conn is fonte, "a fonte nao pode ser outra conexao"
        gate.read("fonte", lambda: None)
        return registros, relogios, frozenset(contas_fonte)

    original = pub.run_publication

    def espia(**kwargs):
        chamadas["run_publication"] += 1
        assert kwargs["target_conn"] is destino
        assert kwargs["audit_conn"] is auditoria
        assert kwargs["audit_conn"] is not kwargs["target_conn"], (
            "auditoria precisa de conexao PROPRIA")
        return original(**kwargs)

    monkeypatch.setattr(pub, "collect_snapshot", falso_collect)
    monkeypatch.setattr(pub, "run_publication", espia)
    monkeypatch.setattr(pub, "execute_values",
                        lambda cur, sql, args, *a, **k: setattr(
                            cur, "rowcount", len(args)))

    desfecho = pub.run_apply(
        _args(**{k: v for k, v in kw.items() if k in ("observed_date",)}),
        connect_target=lambda: destino,
        connect_audit=lambda: auditoria,
        connect_source=lambda: fonte,
    )
    return desfecho, chamadas, destino, auditoria, fonte


# ---------------------------------------------------------------------------
# A CLI alcanca o executor
# ---------------------------------------------------------------------------


def test_apply_alcanca_run_publication_exatamente_uma_vez(monkeypatch):
    _, chamadas, _, _, _ = _roda_apply(monkeypatch)
    assert chamadas["run_publication"] == 1


def test_o_main_nao_tem_mais_recusa_incondicional():
    """Teste ESTRUTURAL: nenhum `return EXIT_REFUSED` antes do executor."""
    import ast
    import pathlib

    for modulo, alvo in (
        ("pipelines/channel_offer_publisher.py", "run_apply"),
        ("pipelines/channel_offer_sync.py", "publisher.main"),
    ):
        fonte = pathlib.Path(modulo).read_text(encoding="utf-8")
        arvore = ast.parse(fonte)
        main = next(n for n in arvore.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        corpo = ast.unparse(main)
        assert alvo in corpo, modulo
        # o caminho de `--apply` chega ao executor; a recusa que sobra vem de
        # excecao tratada, nunca de um `return` incondicional
        antes_do_alvo = corpo.split(alvo)[0]
        assert "APPLY BLOQUEADO" not in antes_do_alvo
        assert "nao esta autorizado nesta rodada" not in antes_do_alvo


def test_modo_diagnostico_nunca_alcanca_o_executor(monkeypatch, capsys):
    """O ensaio agora le a fonte — e continua sem tocar o executor."""
    tocou = []
    monkeypatch.setattr(pub, "run_apply",
                        lambda *a, **k: tocou.append(True))
    monkeypatch.setattr(pub, "run_diagnose",
                        lambda args, **k: {
                            "mode": "dry_run", "marketplace": args.marketplace,
                            "observed_dates": ["2026-09-22"], "rows": 1,
                            "accounts": ["apice"], "brands": ["apice"],
                            "statuses": {"current": 1}, "prices_absent": 0,
                            "prices_zero": 0, "fingerprint": "f" * 32,
                            "scopes": [], "accounts_seen": ["apice"]})
    assert pub.main(["--marketplace", "shopee"]) == pub.EXIT_OK
    assert tocou == [], "o executor de publicacao nao pode ser alcancado"
    saida = capsys.readouterr().out
    assert "mode=dry_run" in saida
    assert "nada foi escrito" in saida


class _FonteDuble:
    """Conexao de FONTE falsa. Nao tem cursor, commit nem caminho de escrita."""

    def close(self):
        pass


def test_diagnostico_nao_abre_conexao_de_destino(monkeypatch):
    """Gate PMA-2C5C-H1 — o ensaio percorre a leitura e mesmo assim nao abre
    conexao de destino.

    A fonte e' injetada no lugar de `_read_only`: sem isso o teste alcancaria o
    Data Mart do ambiente, e e' a guarda do `conftest` que o impede. Um teste
    que precisa de credencial para provar que nao escreve prova o contrario do
    que promete.
    """
    destinos, fontes = [], []
    monkeypatch.setattr(pub, "_writable",
                        lambda url: destinos.append(url))
    monkeypatch.setattr(pub, "audit_start",
                        lambda *a, **k: destinos.append("audit_start"))
    monkeypatch.setattr(cos, "_read_only",
                        lambda url: (fontes.append(url), _FonteDuble())[1])
    monkeypatch.setattr(cos, "load_internal_catalog", lambda c: {})
    monkeypatch.setattr(cos, "latest_tiktok_snapshot",
                        lambda c: date(2026, 9, 22))
    monkeypatch.setattr(cos, "fetch_tiktok_offers", lambda c, d, cat: [])
    monkeypatch.setenv("DATAMART_DATABASE_URL", "postgresql://fonte/injetada")

    assert pub.main(["--marketplace", "tiktok"]) == pub.EXIT_OK
    assert destinos == [], (
        "nem conexao de destino nem abertura de auditoria sao permitidas")
    assert fontes == ["postgresql://fonte/injetada"], (
        "o ensaio precisa ter REALMENTE aberto a fonte somente-leitura")


def test_o_sync_delega_apply_ao_publisher(monkeypatch):
    recebidos = {}

    def falso_main(argv):
        recebidos["argv"] = argv
        return pub.EXIT_OK

    monkeypatch.setattr(pub, "main", falso_main)
    assert cos.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_OK
    assert recebidos["argv"] == ["--marketplace", "shopee", "--apply"]


def test_o_sync_preserva_o_diagnostico(monkeypatch, capsys):
    tocou = []
    monkeypatch.setattr(pub, "main", lambda argv: tocou.append(argv))
    assert cos.main(["--marketplace", "shopee"]) == cos.EXIT_OK
    assert tocou == []
    assert "diagnose" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Precondicao e lock
# ---------------------------------------------------------------------------


def test_tabela_ausente_recusa_antes_de_ler_a_fonte(monkeypatch):
    destino, auditoria, fonte = _fabricas(monkeypatch, relacao=None)
    leu = []
    monkeypatch.setattr(pub, "collect_snapshot",
                        lambda *a, **k: leu.append(True))
    with pytest.raises(cos.ApplyNotAuthorizedError):
        pub.run_apply(_args(), connect_target=lambda: destino,
                      connect_audit=lambda: auditoria,
                      connect_source=lambda: fonte)
    assert leu == []
    # e nem sequer disputou o lock
    assert not any("advisory" in c for c, _ in destino.comandos)


def test_migration_017_ausente_recusa(monkeypatch):
    destino, auditoria, fonte = _fabricas(monkeypatch, revisao="016")
    with pytest.raises(cos.ApplyNotAuthorizedError):
        pub.run_apply(_args(), connect_target=lambda: destino,
                      connect_audit=lambda: auditoria,
                      connect_source=lambda: fonte)


def test_lock_ocupado_nao_le_a_fonte(monkeypatch):
    destino, auditoria, fonte = _fabricas(monkeypatch, lock=False)
    leu = []
    monkeypatch.setattr(pub, "collect_snapshot",
                        lambda *a, **k: leu.append(True))
    desfecho = pub.run_apply(_args(), connect_target=lambda: destino,
                             connect_audit=lambda: auditoria,
                             connect_source=lambda: fonte)
    assert desfecho.state == pub.STATE_LOCK_UNAVAILABLE
    assert leu == []


def test_a_fonte_so_e_lida_depois_do_lock(monkeypatch):
    ordem = []
    destino, auditoria, fonte = _fabricas(monkeypatch)
    original = cos.try_acquire_publication_lock

    def espia(conn):
        ordem.append("lock")
        return original(conn)

    def collect(gate, source_conn, marketplace, observed_date=None):
        gate.read("fonte", lambda: ordem.append("leitura"))
        return [], {"apice": cos.AccountClock("shopee", "apice", TARDE)}, \
            frozenset(["apice"])

    monkeypatch.setattr(cos, "try_acquire_publication_lock", espia)
    monkeypatch.setattr(pub, "collect_snapshot", collect)
    monkeypatch.setattr(pub, "execute_values",
                        lambda cur, sql, a, *b, **k: None)
    pub.run_apply(_args(), connect_target=lambda: destino,
                  connect_audit=lambda: auditoria, connect_source=lambda: fonte)
    assert ordem == ["lock", "leitura"]


# ---------------------------------------------------------------------------
# Estados das contas
# ---------------------------------------------------------------------------


def test_saudavel_com_ofertas():
    e = pub.classify_accounts(accounts_in_source={"apice"},
                              accounts_with_offers={"apice"},
                              accounts_published=set(), source_available=True)
    assert e.healthy_with_offers == {"apice"} and not e.healthy_empty


def test_saudavel_vazio_e_alcancavel():
    """A conta tem relogio mas nenhuma oferta: apaga a PROPRIA fotografia."""
    e = pub.classify_accounts(accounts_in_source={"apice", "lescent"},
                              accounts_with_offers={"apice"},
                              accounts_published=set(), source_available=True)
    assert e.healthy_empty == {"lescent"}
    assert "lescent" in e.healthy  # entra nos escopos que sofrem DELETE


def test_indisponivel_nao_vira_vazio():
    e = pub.classify_accounts(accounts_in_source={"apice"},
                              accounts_with_offers=set(),
                              accounts_published={"apice"},
                              source_available=False)
    assert e.unavailable == {"apice"}
    assert e.healthy == frozenset()  # NADA e' apagado
    assert e.healthy_empty == frozenset()


def test_conta_ausente_nao_e_tratada_como_executada():
    e = pub.classify_accounts(accounts_in_source={"apice"},
                              accounts_with_offers={"apice"},
                              accounts_published={"apice", "barbours"},
                              source_available=True)
    assert e.did_not_run == {"barbours"}
    assert "barbours" not in e.healthy


def test_oferta_de_conta_fora_do_relogio_falha_alto():
    with pytest.raises(pub.PublisherError):
        pub.classify_accounts(accounts_in_source={"apice"},
                              accounts_with_offers={"apice", "fantasma"},
                              accounts_published=set(), source_available=True)


def test_saudavel_vazio_produz_escopo_com_a_data_do_proprio_relogio():
    relogios = {"lescent": cos.AccountClock("shopee", "lescent", TARDE)}
    escopos, wm = pub._scopes_for_empty_accounts(
        {"lescent"}, "shopee", relogios, None)
    assert len(escopos) == 1
    escopo = next(iter(escopos))
    assert escopo.shop_account == "lescent"
    assert escopo.observed_date == HOJE
    assert wm[escopo] == TARDE


def test_saudavel_vazio_sem_relogio_falha_alto():
    with pytest.raises(pub.PublisherError):
        pub._scopes_for_empty_accounts({"x"}, "shopee", {}, None)


def test_tiktok_usa_a_conta_canonica():
    assert cos.canonical_account("tiktok", None) == "tiktok"


def test_shopee_sem_conta_falha_alto():
    with pytest.raises(cos.ChannelSyncError):
        cos.canonical_account("shopee", None)


# ---------------------------------------------------------------------------
# Escopo, isolamento e desfechos
# ---------------------------------------------------------------------------


def _registro(conta="apice", chave="900"):
    base = {c: None for c in cos.RECORD_COLUMNS}
    base.update({"observed_date": HOJE, "marketplace": "shopee",
                 "brand": conta, "offer_key": chave, "parent_item_id": chave,
                 "shop_account": conta, "account_watermark_at": TARDE,
                 "is_active": True})
    return base


def test_saudavel_vazio_apaga_somente_seu_escopo(monkeypatch):
    desfecho, _, destino, _, _ = _roda_apply(
        monkeypatch, registros=[], contas_fonte=("lescent",),
        relogios={"lescent": cos.AccountClock("shopee", "lescent", TARDE)})
    assert desfecho.state == pub.STATE_PUBLISHED
    assert desfecho.rows_loaded == 0
    deletes = [p for c, p in destino.comandos
               if c.upper().startswith("DELETE") and p]
    assert len(deletes) == 1
    assert deletes[0]["shop_account"] == "lescent"


def test_multiplas_contas_permanecem_isoladas(monkeypatch):
    desfecho, _, destino, _, _ = _roda_apply(
        monkeypatch,
        registros=[_registro("apice", "900"), _registro("barbours", "901")],
        contas_fonte=("apice", "barbours"),
        relogios={"apice": cos.AccountClock("shopee", "apice", TARDE),
                  "barbours": cos.AccountClock("shopee", "barbours", TARDE)},
        contas_publicadas=("apice", "barbours", "lescent"))
    contas = {p["shop_account"] for c, p in destino.comandos
              if c.upper().startswith("DELETE") and p}
    assert contas == {"apice", "barbours"}
    assert "lescent" not in contas  # nao executou: preservada


def test_published_retorna_sucesso(monkeypatch, capsys):
    monkeypatch.setattr(pub, "run_apply", lambda a, **k: pub.PublicationOutcome(
        pub.STATE_PUBLISHED, cos.PublishDecision(cos.PUBLISH_ALLOW),
        rows_extracted=10, rows_loaded=8, scopes_replaced=(APICE,)))
    assert pub.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_OK
    assert "PUBLICADO" in capsys.readouterr().out


def test_refused_retorna_recusa(monkeypatch, capsys):
    monkeypatch.setattr(pub, "run_apply", lambda a, **k: pub.PublicationOutcome(
        pub.STATE_REFUSED,
        cos.PublishDecision(cos.PUBLISH_REFUSE, cos.REFUSE_WATERMARK_REGRESSION)))
    assert pub.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_REFUSED
    erro = capsys.readouterr().err
    assert "RECUSADO" in erro and cos.REFUSE_WATERMARK_REGRESSION in erro


def test_rolled_back_retorna_falha_segura(monkeypatch, capsys):
    monkeypatch.setattr(pub, "run_apply", lambda a, **k: pub.PublicationOutcome(
        pub.STATE_ROLLED_BACK, cos.PublishDecision(cos.PUBLISH_ALLOW),
        detail="insert falhou"))
    assert pub.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_FAILED
    assert "desfeita" in capsys.readouterr().err


def test_lock_ocupado_tem_codigo_proprio(monkeypatch, capsys):
    monkeypatch.setattr(pub, "run_apply", lambda a, **k: pub.PublicationOutcome(
        pub.STATE_LOCK_UNAVAILABLE,
        cos.PublishDecision(cos.PUBLISH_REFUSE, "lock_unavailable")))
    assert pub.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_LOCKED
    assert "OCUPADO" in capsys.readouterr().err


def test_indeterminate_tem_codigo_distinto_e_nao_manda_repetir(monkeypatch,
                                                               capsys):
    monkeypatch.setattr(pub, "run_apply", lambda a, **k: pub.PublicationOutcome(
        pub.STATE_INDETERMINATE, cos.PublishDecision(cos.PUBLISH_ALLOW),
        sync_run_id=77, detail="o commit foi TENTADO e levantou"))
    codigo = pub.main(["--marketplace", "shopee", "--apply"])
    assert codigo == pub.EXIT_INDETERMINATE
    assert codigo not in (pub.EXIT_OK, pub.EXIT_REFUSED, pub.EXIT_FAILED,
                          pub.EXIT_LOCKED)
    erro = capsys.readouterr().err
    assert "INDETERMINADO" in erro
    assert "NAO reexecute" in erro


def test_auditoria_incompleta_nao_rebaixa_publicacao(monkeypatch, capsys):
    monkeypatch.setattr(pub, "run_apply", lambda a, **k: pub.PublicationOutcome(
        pub.STATE_PUBLISHED, cos.PublishDecision(cos.PUBLISH_ALLOW),
        rows_loaded=5, sync_run_id=12, audit_complete=False))
    assert pub.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_OK
    capturado = capsys.readouterr()
    assert "PUBLICADO" in capturado.out
    assert "INCOMPLETA" in capturado.err


def test_a_matriz_de_exit_codes_cobre_todos_os_estados():
    assert set(pub.OUTCOME_EXIT) == set(pub.PUBLICATION_STATES)
    assert len(set(pub.OUTCOME_EXIT.values())) == len(pub.PUBLICATION_STATES)


# ---------------------------------------------------------------------------
# Recursos, interrupcao e vazamento
# ---------------------------------------------------------------------------


def test_todas_as_conexoes_sao_fechadas(monkeypatch):
    _, _, destino, auditoria, fonte = _roda_apply(monkeypatch)
    assert destino.fechada and auditoria.fechada and fonte.fechada


def test_conexoes_sao_fechadas_mesmo_com_falha(monkeypatch):
    destino, auditoria, fonte = _fabricas(monkeypatch)
    monkeypatch.setattr(pub, "collect_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("fonte caiu")))
    with pytest.raises(RuntimeError):
        pub.run_apply(_args(), connect_target=lambda: destino,
                      connect_audit=lambda: auditoria,
                      connect_source=lambda: fonte)
    assert destino.fechada and auditoria.fechada and fonte.fechada


def test_keyboard_interrupt_propaga_apos_cleanup(monkeypatch):
    destino, auditoria, fonte = _fabricas(monkeypatch)
    monkeypatch.setattr(pub, "collect_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(
                            KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        pub.run_apply(_args(), connect_target=lambda: destino,
                      connect_audit=lambda: auditoria,
                      connect_source=lambda: fonte)
    assert destino.fechada and fonte.fechada


def test_system_exit_propaga_pelo_main(monkeypatch):
    def explode(*a, **k):
        raise SystemExit(9)

    monkeypatch.setattr(pub, "run_apply", explode)
    with pytest.raises(SystemExit):
        pub.main(["--marketplace", "shopee", "--apply"])


def test_falha_de_credencial_nao_vaza_dsn(monkeypatch, capsys):
    def explode(*a, **k):
        raise RuntimeError(
            "could not connect to postgresql://u:senha@10.0.0.7:5432/db")

    monkeypatch.setattr(pub, "run_apply", explode)
    assert pub.main(["--marketplace", "shopee", "--apply"]) == pub.EXIT_FAILED
    erro = capsys.readouterr().err
    for vazamento in ("postgresql://", "senha", "10.0.0.7", "5432", "Traceback"):
        assert vazamento not in erro


def test_nenhuma_conexao_cai_para_outra(monkeypatch):
    """Fonte, destino e auditoria sao TRES objetos distintos."""
    _, _, destino, auditoria, fonte = _roda_apply(monkeypatch)
    assert destino is not auditoria
    assert destino is not fonte
    assert auditoria is not fonte


def test_nao_existe_fallback_entre_as_fabricas():
    """Cada papel tem sua propria fabrica; nenhuma cobre a outra."""
    import inspect
    fonte = inspect.getsource(pub.run_apply)
    assert "connect_target or" in fonte
    assert "connect_audit or" in fonte
    assert "connect_source or" in fonte
    # destino e auditoria vao ao Neon; a fonte, ao Data Mart
    assert "DATAMART_DATABASE_URL" in fonte
    assert fonte.count("DATABASE_URL") >= 3


def test_nenhuma_variavel_de_ambiente_nova_foi_inventada():
    import inspect
    import re
    fonte = inspect.getsource(pub.run_apply)
    usadas = set(re.findall(r'environ\["([A-Z_]+)"\]', fonte))
    assert usadas <= {"DATABASE_URL", "DATAMART_DATABASE_URL"}
