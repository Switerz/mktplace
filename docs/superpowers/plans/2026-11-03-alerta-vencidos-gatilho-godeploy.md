# Alerta de vencidos da Shopee: gatilho no GoDeploy, lógica na API — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** o alerta de vencidos da Shopee (> 3% da fila) passa a rodar sozinho a cada 15 min, sem Airflow: um cron do GoDeploy chama uma rota da API no Render, e a API decide e manda a DM no Slack.

**Architecture:** a regra que hoje é CLI em `pipelines/expedicao/alerta_vencidos.py` muda para `apps/api` (a API do Render sobe só `apps/api` e não importa `pipelines`). Uma rota `POST` protegida por segredo roda a regra. Um app mínimo no GoDeploy só confere a assinatura do cron e repassa a chamada. A garantia de "uma mensagem por fotografia" fica no próprio Slack: cada DM leva `metadata` com a chave da fotografia, e antes de postar a API lê o histórico da DM daquela pessoa. Não há tabela nova nem migration.

**Tech Stack:** FastAPI + SQLAlchemy (apps/api), Slack Web API (urllib, sem dependência nova), GoDeploy (worker JS + `createCronJob`), pytest, `node --test`.

**Spec:** a conversa de 03/11/2026 e a seção "Alerta de vencidos da Shopee no Slack" de `docs/expedicao_contrato.md` (regra de negócio inalterada: limiar, lembretes 9h/13h/17h, aviso de saída).

## Global Constraints

- Limiar: `overdue_count / backlog_count` da Shopee **estritamente acima de 3%** (`LIMIAR_VENCIDOS = 0.03`). Inalterado.
- Lembretes quando o relógio de Brasília atravessa **9h, 13h ou 17h** (`HORAS_LEMBRETE = (9, 13, 17)`), `BRT = UTC-3` fixo. Inalterado.
- Fotografia mais velha que **60 min** não é avaliada (`MAX_IDADE_MIN = 60`; antes, 45). Motivo: a idade máxima precisa ser maior ou igual ao atraso da publicação somado à cadência de 15 min, senão uma fotografia pode passar inteira entre dois disparos.
- Cadência do cron: `*/15 * * * *` (UTC).
- **Sem migration nova.** A cadeia do PMA no Airflow recusa um head do Neon que a imagem não conheça (pin `4617c15`, conhece até a `035`). Uma `036` exigiria outro PR no `goca-se/airflow`, que é o que este desenho evita.
- A sessão da API que lê o alerta é **READ ONLY + REPEATABLE READ**. A API não grava nada no Neon.
- Segredos nunca entram em código, log, resposta HTTP ou mensagem de erro: `SLACK_BOT_TOKEN`, `ALERTA_WEBHOOK_SECRET`.
- Mensagens de erro HTTP são **fixas**, sem ecoar entrada. Esse é o padrão da API (ver `app/routers/expedicao.py`).
- O router `app/routers/expedicao.py` continua **só GET**: `test_somente_metodos_get_disponiveis` precisa seguir passando. A rota nova fica num router próprio.
- O app do GoDeploy fica `authenticated` (padrão). Nunca `setAppPublic`.
- Testes da API rodam de dentro de `apps/api` (os imports são `from app...`). Numa worktree sem `.venv`, use o Python da `.venv` do checkout principal (`C:/Users/User/Documents/mktplace/apps/api/.venv/Scripts/python.exe -m pytest ...`) ou `uv run --extra dev python -m pytest ...`.
- Destinatários vêm de `EXPEDICAO_ALERTA_DESTINATARIOS` (e-mails do Slack separados por vírgula). E-mail só aparece em `app/services/slack_dm.py` e na config, nunca no pacote de serviço da Expedição.

## Review Focus

1. **Nova tentativa depois de envio parcial** (o Slack falhou no 2º destinatário e o GoDeploy repete após um 5xx): quem já recebeu não recebe de novo. Teste na Task 2.
2. **Falha ao ler o histórico da DM** (escopo `im:history` ausente, `channel_not_found`): não posta às cegas. Postar sem conferir viraria spam a cada 15 min. Teste na Task 2.
3. **Disparo repetido sobre a mesma fotografia** (todo disparo dentro dos 60 min dela): só o primeiro manda. Teste na Task 3.
4. **Fila vazia** (`backlog = 0`, fração `None`) com a fotografia anterior acima de 3%: sai o aviso "voltaram" com 0,0%, sem dividir por zero. Teste na Task 1.
5. **Chamada sem segredo, com segredo errado ou com a config vazia**: 401 ou 503, sem nenhuma consulta ao banco nem ao Slack. Teste na Task 3.

Risco aceito, sem teste: dois disparos simultâneos (a nova tentativa de um e o tick seguinte) podem ler o histórico antes de qualquer um postar. A janela é de segundos, e o GoDeploy executa os jobs de um app em sequência.

---

### Task 1: regra do alerta dentro da API

**Files:**
- Create: `apps/api/app/services/alerta_vencidos.py`
- Test: `apps/api/tests/test_alerta_vencidos_servico.py`
- Fonte do porte: `pipelines/expedicao/alerta_vencidos.py` (funções puras e `ler_canal`) e `pipelines/tests/test_expedicao_alerta_vencidos.py` (casos de `test_limite_*` até `test_mensagem_de_normalizacao`, linhas 107–228)

**Interfaces:**
- Consumes: `FILA`, `RUN` de `app/services/expedicao_service.py` (`"marts.expedicao_fila_atual"`, `"marts.expedicao_refresh_run"`).
- Produces:
  - constantes `LIMIAR_VENCIDOS`, `HORAS_LEMBRETE`, `MAX_IDADE_MIN = 60`, `CANAL = "shopee"`, `URL_DA_EXPEDICAO`, `ENTROU`/`LEMBRETE`/`SAIU`;
  - `Medida`, `Leitura` (iguais às do pipeline, com `canal: str` no lugar do enum `Channel`);
  - `ler_canal(db: Session, canal: str) -> Leitura | None`;
  - `decidir(leitura: Leitura) -> str | None`, `passou_hora_de_lembrete(desde, ate) -> bool`, `fotografia_velha(leitura, agora, max_idade_min) -> bool`, `montar_mensagem(leitura, evento) -> str` (cópia fiel);
  - `chave_da_fotografia(leitura: Leitura) -> str`, que devolve `f"{leitura.canal}:{leitura.effective_at.astimezone(timezone.utc).isoformat()}"`;
  - `@dataclass(frozen=True) Avaliacao(canal: str, situacao: str, evento: str | None, mensagem: str | None, chave: str | None, fracao: float | None)`, com `situacao` em `{"sem_fotografia", "fotografia_velha", "sem_evento", "evento"}`;
  - `avaliar(db: Session, agora: datetime) -> Avaliacao`.

- [ ] **Step 1: Portar os testes puros e escrever os novos.** Copie os casos das linhas 107–228 do teste do pipeline, trocando `Channel.SHOPEE` por `"shopee"` e o cursor psycopg2 falso por uma `Session` falsa cujo `execute(text, params)` devolve objetos com `.mappings().all()` / `.mappings().first()`. Acrescente:

```python
def test_fila_vazia_com_anterior_acima_sai_com_zero():
    l = leitura(total=Medida(0, 0), anterior=Medida(100, 5))
    assert decidir(l) == SAIU
    assert "voltaram a 0,0%" in montar_mensagem(l, SAIU)

def test_chave_da_fotografia_e_canal_mais_instante_utc():
    l = leitura(effective_at=datetime(2026, 11, 3, 10, 2, tzinfo=BRT))
    assert chave_da_fotografia(l) == "shopee:2026-11-03T13:02:00+00:00"

def test_avaliar_fotografia_de_61_min_nao_avalia(db_em_alerta):
    a = avaliar(db_em_alerta, agora=EFFECTIVE + timedelta(minutes=61))
    assert a.situacao == "fotografia_velha" and a.evento is None

def test_avaliar_em_alerta_devolve_evento_mensagem_e_chave(db_em_alerta):
    a = avaliar(db_em_alerta, agora=EFFECTIVE + timedelta(minutes=10))
    assert (a.situacao, a.evento) == ("evento", ENTROU)
    assert a.chave.startswith("shopee:") and "Abrir a Expedição" in a.mensagem

def test_ler_canal_abre_transacao_read_only_repeatable_read(db_em_alerta):
    ler_canal(db_em_alerta, "shopee")
    assert "REPEATABLE READ" in db_em_alerta.sql[0] and "READ ONLY" in db_em_alerta.sql[0]
```

- [ ] **Step 2: Rodar e ver falhar.** `cd apps/api && python -m pytest tests/test_alerta_vencidos_servico.py -q`. Esperado: `ModuleNotFoundError: app.services.alerta_vencidos`.
- [ ] **Step 3: Implementar `app/services/alerta_vencidos.py`.** Copie as funções puras sem mudar a regra. `ler_canal` emite primeiro `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY` e depois as três consultas do pipeline via `db.execute(text(...), {...})`. `avaliar` faz `ler_canal`, depois `db.rollback()` em `finally`, `fotografia_velha` com `MAX_IDADE_MIN`, `decidir` e `montar_mensagem`.
- [ ] **Step 4: Rodar e ver passar.** Mesmo comando. Esperado: todos verdes.
- [ ] **Step 5: Commit.** `git add apps/api/app/services/alerta_vencidos.py apps/api/tests/test_alerta_vencidos_servico.py && git commit -m "feat(alerta): regra de vencidos da Shopee dentro da API"`

### Task 2: DM no Slack que não se repete

**Files:**
- Create: `apps/api/app/services/slack_dm.py` (baseado em `pipelines/common/slack_dm.py`)
- Test: `apps/api/tests/test_slack_dm.py`

**Interfaces:**
- Produces:
  - `class SlackFalhou(RuntimeError)`;
  - `EVENTO_METADATA = "torre_alerta_vencidos"`;
  - `Chamador = Callable[[str, str, dict[str, str]], dict]` (`token, metodo, campos -> corpo`); o padrão é `_slack`, que vem do pipeline sem mudança;
  - `@dataclass(frozen=True) ResultadoEnvio(enviados: int, ja_tinham: int)`;
  - `enviar_dm_sem_repetir(token: str, destinatarios: list[str], texto: str, chave: str, *, chamar: Chamador = _slack) -> ResultadoEnvio`.
- Sequência por destinatário:
  1. `users.lookupByEmail(email)` → `user.id`;
  2. `conversations.open(users=id)` → `channel.id`;
  3. `conversations.history(channel, limit="20", include_all_metadata="true")`. Se alguma mensagem tiver `metadata.event_type == EVENTO_METADATA` e `metadata.event_payload.chave == chave`, conta em `ja_tinham` e segue para o próximo;
  4. senão, `chat.postMessage(channel, text, metadata=json.dumps({"event_type": EVENTO_METADATA, "event_payload": {"chave": chave}}))` e conta em `enviados`.
- Escopos usados, todos já presentes em `infra/slack/torre-bot/manifest.json`: `users:read.email`, `im:write`, `im:history`, `chat:write`.

- [ ] **Step 1: Escrever os testes**, com um `chamar` falso que registra `(metodo, campos)` e responde por método:

```python
def test_posta_com_metadata_da_chave(): ...
    # assert ultimo == ("chat.postMessage", ...) e json.loads(campos["metadata"])["event_payload"]["chave"] == "shopee:X"
def test_quem_ja_recebeu_esta_chave_nao_recebe_de_novo(): ...
    # history devolve metadata com a mesma chave -> ResultadoEnvio(enviados=0, ja_tinham=1), nenhum chat.postMessage
def test_chave_diferente_no_historico_nao_bloqueia(): ...
    # history com chave "shopee:outra" -> enviados == 1
def test_reenvio_apos_falha_parcial_so_manda_para_quem_faltou(): ...
    # 2 destinatarios; history do 1o ja tem a chave -> so 1 chat.postMessage, para o 2o
def test_falha_ao_ler_historico_nao_posta(): ...
    # history responde {"ok": False, "error": "missing_scope"} -> SlackFalhou, zero chat.postMessage
def test_erro_nunca_carrega_o_token(): ...
    # token "xoxb-segredo"; str(SlackFalhou) nao contem "xoxb-segredo"
```

- [ ] **Step 2: Rodar e ver falhar.** `cd apps/api && python -m pytest tests/test_slack_dm.py -q`. Esperado: `ModuleNotFoundError`.
- [ ] **Step 3: Implementar `app/services/slack_dm.py`.** `_slack` e `SlackFalhou` vêm do pipeline sem mudança. `enviar_dm_sem_repetir` segue a sequência acima. Uma falha em qualquer chamada levanta `SlackFalhou` e interrompe o restante.
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit.** `git commit -m "feat(alerta): DM do bot da Torre com chave da fotografia, sem repetir"`

### Task 3: rota protegida que dispara o alerta

**Files:**
- Create: `apps/api/app/routers/alertas.py`
- Modify: `apps/api/app/config.py` (três campos novos), `apps/api/app/main.py` (`app.include_router(alertas.router)`)
- Test: `apps/api/tests/test_alertas_rota.py` (padrão de `TestClient` + `app.dependency_overrides[get_db]` de `tests/test_metas.py:157-177`)

**Interfaces:**
- Consumes: `avaliar` (Task 1), `enviar_dm_sem_repetir`, `SlackFalhou`, `ResultadoEnvio` (Task 2).
- Produces:
  - em `Settings`: `alerta_webhook_secret: SecretStr = SecretStr("")`, `slack_bot_token: SecretStr = SecretStr("")`, `expedicao_alerta_destinatarios: str = ""`. As envs são `ALERTA_WEBHOOK_SECRET`, `SLACK_BOT_TOKEN` e `EXPEDICAO_ALERTA_DESTINATARIOS`;
  - `POST /api/v1/alertas/expedicao-vencidos`, header `X-Alerta-Token`, query `simular: bool = False`.
- Contrato da rota, na ordem de checagem (nada é lido antes de passar pela checagem anterior):

| Condição | Status | Corpo `detail` |
|---|---|---|
| `alerta_webhook_secret` vazio | 503 | `"Alerta nao configurado."` |
| header ausente ou diferente (`hmac.compare_digest`) | 401 | `"Nao autorizado."` |
| `db is None` | 503 | `"Banco de dados indisponivel."` |
| `not simular` e token/destinatários vazios | 503 | `"Envio nao configurado."` |
| `SlackFalhou` | 502 | `"Slack recusou o envio."` (o GoDeploy tenta de novo) |
| ok | 200 | `{"canal", "situacao", "evento", "enviados", "ja_tinham"}`, e `"mensagem"` só quando `simular` |

  Com `simular=true`, roda `avaliar` e não chama o Slack (`enviados = ja_tinham = 0`). Com `situacao != "evento"`, nunca chama o Slack.

- [ ] **Step 1: Escrever os testes.** Use monkeypatch em `settings` e em `alertas.avaliar` / `alertas.enviar_dm_sem_repetir`; ambos são espiões que falham o teste se chamados indevidamente.

```python
def test_sem_segredo_configurado_503_sem_ler_nada(...): assert r.status_code == 503 and espiao_avaliar.chamadas == 0
def test_header_ausente_401(...): ...
def test_header_errado_401_e_nao_ecoa(...): assert "errado" not in r.text
def test_simular_devolve_mensagem_sem_chamar_slack(...): assert r.json()["mensagem"] and espiao_slack.chamadas == 0
def test_evento_envia_com_a_chave_da_fotografia(...): assert espiao_slack.args[3] == "shopee:2026-11-03T13:02:00+00:00"
def test_sem_evento_nao_chama_slack(...): ...
def test_mesma_fotografia_duas_vezes_so_envia_uma(...):
    # enviar falso guarda as chaves ja' vistas e devolve ja_tinham=1 na 2a; 2a resposta: enviados == 0
def test_slack_falhou_502_sem_token_no_corpo(...): assert r.status_code == 502 and "xoxb" not in r.text
def test_envio_sem_token_ou_destinatarios_503(...): ...
def test_router_de_expedicao_continua_so_get(): ...  # reexecuta a checagem de tests/test_expedicao_api.py:620
```

- [ ] **Step 2: Rodar e ver falhar.** `cd apps/api && python -m pytest tests/test_alertas_rota.py -q`.
- [ ] **Step 3: Implementar a config, o router e o registro em `main.py`.** Destinatários: `[e.strip() for e in valor.split(",") if e.strip()]`. `agora = datetime.now(timezone.utc)`.
- [ ] **Step 4: Rodar a suíte da API inteira.** `cd apps/api && python -m pytest tests -q`. Esperado: verde, incluindo `test_somente_metodos_get_disponiveis`.
- [ ] **Step 5: Commit.** `git commit -m "feat(alerta): rota protegida que dispara o alerta de vencidos"`

### Task 4: tirar o CLI antigo e atualizar o contrato

**Files:**
- Delete: `pipelines/expedicao/alerta_vencidos.py`, `pipelines/tests/test_expedicao_alerta_vencidos.py`, `pipelines/common/slack_dm.py`. Nenhum outro arquivo os importa (conferido em 03/11/2026 com `git grep slack_dm`).
- Modify: `docs/expedicao_contrato.md`. A linha do quadro de estado ("falta o agendamento no Airflow") passa a dizer "gatilho no GoDeploy a cada 15 min". A seção "Alerta de vencidos da Shopee no Slack" deve:
  - trocar o comando pela rota e pelo contrato da tabela da Task 3;
  - registrar o `MAX_IDADE_MIN = 60` e o motivo;
  - explicar a não repetição pela `metadata` da DM;
  - listar as envs no Render;
  - descrever o app do GoDeploy (Task 5);
  - substituir o parágrafo "Agendamento: uma task na DAG...".

- [ ] **Step 1: Apagar os três arquivos e rodar `python -m pytest pipelines/tests -q`.** Esperado: o mesmo resultado de antes, menos os testes removidos.
- [ ] **Step 2: Editar o contrato.**
- [ ] **Step 3: Commit.** `git commit -m "refactor(alerta): CLI do pipeline sai, a regra vive na API"`

### Task 5: app gatilho no GoDeploy

**Files:**
- Create: `infra/godeploy/torre-alerta-gatilho/src/index.js`, `infra/godeploy/torre-alerta-gatilho/src/repasse.js`, `infra/godeploy/torre-alerta-gatilho/test/repasse.test.mjs`

**Interfaces:**
- `repasse.js` exporta:
  - `URL_DA_API = "https://mktplace-api.onrender.com/api/v1/alertas/expedicao-vencidos"`;
  - `statusParaGoDeploy(upstream)`: `number | null -> number`. Devolve 200 para 2xx; 502 para `null` (falha de rede) ou 5xx, que o GoDeploy tenta de novo; 424 para 4xx, que é terminal (segredo errado não se resolve tentando de novo).
- `index.js`: `POST /cron/alerta-vencidos`. Passos:
  1. chama `verifyCron(request, env)`, copiado sem mudança de `references/cron.md` da skill `godeploy`; se falhar, responde 403;
  2. faz `fetch(URL_DA_API, { method: "POST", headers: { "X-Alerta-Token": env.ALERTA_WEBHOOK_SECRET } })` dentro de `try/catch` (`catch` = `null`);
  3. faz `console.log` só do status de cima e do `situacao` / `enviados` do JSON;
  4. responde `statusParaGoDeploy(status)`.

  Qualquer outra rota responde 404. Sem `package.json`: não há dependência.

- [ ] **Step 1: Escrever `test/repasse.test.mjs`** (`node:test`) com `[200→200, 204→200, 401→424, 422→424, 500→502, 503→502, null→502]`.
- [ ] **Step 2: Rodar e ver falhar.** `node --test infra/godeploy/torre-alerta-gatilho/test/`.
- [ ] **Step 3: Implementar `repasse.js` e `index.js`.**
- [ ] **Step 4: Rodar e ver passar.**
- [ ] **Step 5: Commit e abrir o PR** com as Tasks 1–5 (título `feat(alerta): vencidos da Shopee disparado pelo GoDeploy, sem Airflow`).

### Task 6: pôr no ar (depois do merge)

Estes passos dependem do usuário onde está indicado.

- [ ] **Step 1: Gerar o segredo compartilhado** em arquivo, sem imprimir: `python -c "import secrets;print(secrets.token_urlsafe(32))" > "<scratchpad>/alerta_webhook_secret.txt"`.
- [ ] **Step 2 (usuário): configurar o Render.** No serviço `mktplace-api`, cadastrar `ALERTA_WEBHOOK_SECRET` (conteúdo do arquivo do Step 1), `SLACK_BOT_TOKEN` (`xoxb-`, em OAuth & Permissions do app "Torre de Marketplaces") e `EXPEDICAO_ALERTA_DESTINATARIOS`.
- [ ] **Step 3: Publicar a API.** Fazer o merge e o `POST` em `RENDER_DEPLOY_HOOK_MKTPLACE_API`. Esperado: `GET https://mktplace-api.onrender.com/health` devolve 200.
- [ ] **Step 4: Teste de fumaça da rota.** Sem header, `curl -s -o /dev/null -w "%{http_code}" -X POST .../api/v1/alertas/expedicao-vencidos` devolve `401`. Com header e `?simular=true` (header lido do arquivo), devolve `200` com `situacao` preenchida.
- [ ] **Step 5: Teste real do Slack (usuário confere a DM).** Só se `situacao == "evento"` agora. Se não, rodar o teste do Slack num shell Python local, chamando `enviar_dm_sem_repetir` com a chave `"teste:<agora>"` para um destinatário só. Rodar **duas vezes**. Esperado: na 1ª, `enviados=1`; na 2ª, `ja_tinham=1`. Isso prova que o `metadata` volta no `conversations.history`. Se a 2ª não acusar `ja_tinham`, parar: a não repetição não funciona, e o desenho volta para a conversa.
- [ ] **Step 6: Criar o app no GoDeploy.** Seguir `getUploadToken`, depois o upload de `src/index.js` e `src/repasse.js`, depois `createApp` (`name: "torre-alerta-gatilho"`, `entrypoint: "src/index.js"`). Em seguida, `setAppSecret(ALERTA_WEBHOOK_SECRET)` com o valor do arquivo. Não tornar público.
- [ ] **Step 7: Agendar.** `createCronJob({ appId, cron: "*/15 * * * *", route: "/cron/alerta-vencidos" })`. Após o primeiro tick, `listCronJobs` deve mostrar o último disparo com `200`, e `getAppLogs` deve mostrar o status da API.
- [ ] **Step 8: Apagar o arquivo do segredo** do scratchpad e registrar no contrato a data de ativação.
