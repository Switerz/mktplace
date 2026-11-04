# Login Google da Torre — Plano de Implementação

> **Para agentes:** SUB-SKILL OBRIGATÓRIA: use superpowers:subagent-driven-development (recomendado) ou superpowers:executing-plans para executar este plano tarefa por tarefa. Os passos usam checkbox (`- [ ]`).

**Objetivo:** só quem tem conta Google `@gocase.com` ou `@gobeaute.com.br` consegue abrir a Torre e ler dados da API, usando o mesmo OAuth Client e o mesmo desenho de autenticação do GoHunter.

**Arquitetura:** é uma porta do login do GoHunter (`C:\Users\User\Documents\GoHunter`: `src/lib/session.ts`, `src/app/api/auth/google/route.ts`, `src/app/api/auth/logout/route.ts`, `src/components/LoginGate.tsx`, `worker/auth.ts`). O front usa o Google Identity Services e manda o ID token para `POST /api/auth/google` (route handler do Next, na Vercel). Ali o token é validado com `jose` e vira o cookie `torre_session` (JWT HS256 assinado por nós). A diferença em relação ao GoHunter: aqui os dados vêm de outra origem (FastAPI no Render). Por isso o navegador passa a chamar a API pela mesma origem (`/torre-api/*`, um rewrite do Next para o Render), e o cookie chega até o FastAPI. Lá, um middleware ASGI (`GateDeSessao`) é a defesa real: sem sessão válida, 401. O Oráculo (MCP) e outras máquinas usam uma chave de serviço.

**Stack:** Next.js 15 (App Router) + `jose` 6.2.9 (já instalado) · FastAPI + `pyjwt` (já instalado) · Google Identity Services (script `https://accounts.google.com/gsi/client`).

**Spec:** não há documento de spec separado. O pedido foi "login Google igual ao do GoHunter, com as mesmas credenciais". A referência é o código do GoHunter citado acima e a regra `GoHunter/.claude/rules/auth.md`.

## Decisões travadas (o executor não reabre)

| Tema | Decisão | Por quê |
|---|---|---|
| Credencial Google | **Mesmo OAuth Client do GoHunter.** `AUTH_GOOGLE_ID` = valor de `C:\Users\User\Documents\GoHunter\.env.local` (`398963590019-….apps.googleusercontent.com`). `AUTH_GOOGLE_SECRET` não é usado (o fluxo de ID token não precisa dele). | Pedido do usuário; o client ID é público. |
| Segredo da sessão | **`AUTH_SECRET` novo para a Torre** (`openssl rand -base64 32`). Não reaproveitar o do GoHunter. | Com o mesmo segredo, um cookie `gh_session` do GoHunter seria um JWT válido aqui. |
| Onde mora o gate | **No FastAPI (Render)**, como middleware. O `LoginGate` do front só esconde a interface. | É o mesmo princípio do GoHunter ("a defesa é o gate do servidor"). Hoje `PUT /api/v1/metas/{mes}` é aberto para qualquer um. |
| Como o cookie chega ao Render | **Mesma origem:** `NEXT_PUBLIC_API_URL=/torre-api` + rewrite `/torre-api/:path*` → `TORRE_API_ORIGIN`. | `vercel.app` e `onrender.com` são sites diferentes (Public Suffix List). Um cookie de terceiro cruzando os dois é bloqueado no Safari e no Firefox. |
| Liga/desliga | Front: `AUTH_GOOGLE_ID` vazio desliga o login (igual ao GoHunter, `dev@local`). API: `TORRE_AUTH_MODE=off\|report\|enforce`, padrão `off`; valor desconhecido conta como `enforce`. | Permite subir sem quebrar a produção e observar (`report`) antes de bloquear. |
| Client ID no front | O front recebe o client ID pela resposta 401 de `/api/auth/me` (`clientId`). Não existe `NEXT_PUBLIC_AUTH_GOOGLE_ID`. | Uma variável a menos. O ID é público. |
| Middleware do Next | **Não criar.** As únicas `/api/*` do Next são `/api/mcp` (OAuth próprio) e `/api/auth/*` (públicas). | YAGNI: os dados passam pelo Render, que tem o gate. |
| Auditoria / admin | Fora de escopo (o GoHunter tem `logAudit` e `ADMIN_EMAILS`; a Torre não tem tabela de auditoria). O login só gera um log `auth_login email=<email>` na Vercel. | YAGNI. |

## Restrições globais

- Domínios permitidos: padrão `gocase.com,gobeaute.com.br`, sobrescrito por `ALLOWED_EMAIL_DOMAINS` (csv). O match é **exato** sobre o trecho depois do último `@`, sem diferenciar maiúsculas. Nunca usar `includes`/`endswith`.
- Sessão: JWT `HS256`, `iss="torre-mktplace"`, `aud="torre-mktplace-api"`, claims `email`, `name`, `picture`, `iat`, `exp`. Validade de 7 dias (`604800` s). Tolerância de relógio de 30 s no Python.
- Cookie: nome `torre_session`, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` quando `NODE_ENV=production`, `Max-Age=604800`. Logout: o mesmo cookie com `Max-Age=0`.
- `AUTH_SECRET` precisa ter ≥ 32 caracteres e o **mesmo valor** na Vercel e no Render. Mais curto conta como não configurado: assinar lança erro, verificar devolve `null`/nega.
- Chave de serviço: header `x-torre-service-key`. Variável `TORRE_SERVICE_KEY` no Render e `MCP_BACKEND_SERVICE_KEY` na Vercel, ≥ 32 caracteres. Comparar com `hmac.compare_digest`.
- Nenhum token, cookie, segredo ou chave aparece em log, em corpo de resposta ou em mensagem de exceção. Log de negação leva só método, path (sem query), código e user-agent cortado em 80 caracteres.
- Sem dependência nova em nenhum dos dois apps.
- Toda resposta de `/api/auth/*` leva `Cache-Control: no-store`.
- Texto de interface em pt-BR com acentos. A tela de login segue `DESIGN.md` §2 (cores), §4 (vidro) e §7 (tokens semânticos, dois temas).
- Testes web novos entram no script `"test"` de `apps/web/package.json`. Testes Python rodam com `cd apps/api && .venv/Scripts/python -m pytest <arquivo> -v`.
- Executar numa branch nova a partir da `main` (`feat/auth-google`), não na `feat/ui-vidro-intensidade`.
- Commits no padrão `feat(auth): … (AUTH-GOOGLE-n)`.

## Pontos de atenção na revisão

1. **A sessão expira com a tela aberta.** A API responde 401 e o `api-client` devolve `null`, o que hoje cai em **dados de demonstração**. O esperado é voltar para a tela de login, nunca mostrar mock como se fosse dado real. Coberto pelo teste do header `X-Torre-Auth` (Tarefa 2) e pelo `torreFetch` (Tarefa 5).
2. **E-mail parecido com domínio permitido** (`x@gocase.com.evil.com`, `x@mail.gocase.com`, `x@gocase.com@evil.com`): precisa ser negado. `JOAO@GOCASE.COM` precisa entrar. Testes nas Tarefas 1 e 3.
3. **Chamadores que não são navegador** (health check do Render em `/ready`, webhook de alerta com `X-Alerta-Token`, B2B com Auth0, Oráculo MCP, algum cron desconhecido): precisam seguir funcionando em `enforce`. A allowlist e a chave de serviço estão nas Tarefas 2 e 7. Antes de ligar `enforce`, o modo `report` mostra quem mais chama a API (Tarefa 8).
4. **Configuração errada em produção** (`TORRE_AUTH_MODE=enforced` digitado errado, `AUTH_SECRET` curto ou vazio): a resposta é 503, nunca liberar. Teste na Tarefa 2.
5. **Preflight CORS (`OPTIONS`) e exports grandes** (`/api/v1/expedicao/.../export`): passam pelo gate sem buffer e sem 401 no preflight. O middleware é ASGI puro. Testes na Tarefa 2.

---

### Tarefa 1: Validação da sessão no Python

**Arquivos:**
- Criar: `apps/api/app/sessao.py`
- Testes: `apps/api/tests/test_sessao.py`

**Interfaces:**
- Produz:
  - `SESSION_COOKIE = "torre_session"`, `SESSION_ISSUER = "torre-mktplace"`, `SESSION_AUDIENCE = "torre-mktplace-api"`, `SEGREDO_MINIMO = 32`, `LEEWAY_SEGUNDOS = 30`
  - `@dataclass(frozen=True) class Sessao: email: str; nome: Optional[str]`
  - `dominios_permitidos(csv: str) -> tuple[str, ...]` (csv vazio → `("gocase.com", "gobeaute.com.br")`; normaliza com strip e lower)
  - `email_permitido(email: Optional[str], dominios: tuple[str, ...]) -> bool`
  - `validar_sessao(token: Optional[str], segredo: str, dominios: tuple[str, ...]) -> Optional[Sessao]`: devolve `None` para qualquer falha e nunca lança exceção.

- [ ] **Passo 1: escrever os testes que falham**

```python
# tests/test_sessao.py — tokens sinteticos, sem rede
import time, jwt
from app.sessao import (Sessao, dominios_permitidos, email_permitido, validar_sessao,
                        SESSION_ISSUER, SESSION_AUDIENCE)

SEGREDO = "s" * 32
DOM = ("gocase.com", "gobeaute.com.br")

def _tok(email="joao@gocase.com", segredo=SEGREDO, iss=SESSION_ISSUER, aud=SESSION_AUDIENCE,
         exp_em=300, alg="HS256", **extra):
    agora = int(time.time())
    claims = {"email": email, "name": "Joao", "iss": iss, "aud": aud, "iat": agora, "exp": agora + exp_em, **extra}
    return jwt.encode(claims, segredo, algorithm=alg)

def test_dominios_padrao_quando_vazio():
    assert dominios_permitidos("") == DOM
    assert dominios_permitidos(" GoCase.com , ") == ("gocase.com",)

def test_email_permitido_match_exato():
    assert email_permitido("JOAO@GOCASE.COM", DOM)
    for ruim in ["x@gocase.com.evil.com", "x@mail.gocase.com", "x@evil.com", "gocase.com", "", None]:
        assert not email_permitido(ruim, DOM), ruim

def test_sessao_valida():
    assert validar_sessao(_tok(), SEGREDO, DOM) == Sessao(email="joao@gocase.com", nome="Joao")

def test_sessao_invalida_vira_none():
    casos = [
        None, "", "lixo",
        _tok(segredo="o" * 32),                 # outro segredo (ex.: o do GoHunter)
        _tok(iss="gohunter"), _tok(aud="outro"), # outro emissor/publico
        _tok(exp_em=-120),                       # expirado alem do leeway
        _tok(email="x@evil.com"),                # dominio barrado na leitura
    ]
    for t in casos:
        assert validar_sessao(t, SEGREDO, DOM) is None

def test_segredo_curto_nega_tudo():
    assert validar_sessao(_tok(segredo="curto"), "curto", DOM) is None

def test_alg_none_recusado():
    t = jwt.encode({"email": "joao@gocase.com", "iss": SESSION_ISSUER, "aud": SESSION_AUDIENCE,
                    "iat": int(time.time()), "exp": int(time.time()) + 300}, None, algorithm="none")
    assert validar_sessao(t, SEGREDO, DOM) is None
```

- [ ] **Passo 2: rodar e ver falhar.** `cd apps/api && .venv/Scripts/python -m pytest tests/test_sessao.py -v` → FAIL com `ModuleNotFoundError: app.sessao`.
- [ ] **Passo 3: implementar `app/sessao.py`.** Usar `jwt.decode(token, segredo, algorithms=["HS256"], audience=SESSION_AUDIENCE, issuer=SESSION_ISSUER, leeway=LEEWAY_SEGUNDOS, options={"require": ["exp", "iat", "iss", "aud"]})` dentro de `try/except jwt.PyJWTError`. Depois do decode, checar o domínio de novo com `email_permitido` (é a mesma defesa em profundidade do `verifySessionToken` do GoHunter). O e-mail sai em minúsculas.
- [ ] **Passo 4: rodar e ver passar.** Mesmo comando → PASS (6 testes).
- [ ] **Passo 5: commit.** `git add apps/api/app/sessao.py apps/api/tests/test_sessao.py && git commit -m "feat(auth): validacao da sessao Google no FastAPI (AUTH-GOOGLE-1)"`

---

### Tarefa 2: Gate de sessão no FastAPI

**Arquivos:**
- Criar: `apps/api/app/gate_sessao.py`
- Alterar: `apps/api/app/config.py` (novos campos em `Settings`), `apps/api/app/main.py` (registrar o middleware **antes** do `CORSMiddleware`, para o CORS ficar por fora)
- Testes: `apps/api/tests/test_gate_sessao.py`

**Interfaces:**
- Consome: `validar_sessao`, `dominios_permitidos`, `SESSION_COOKIE` (Tarefa 1).
- Produz:
  - Em `Settings`: `torre_auth_mode: str = "off"`, `auth_secret: SecretStr = SecretStr("")`, `allowed_email_domains: str = "gocase.com,gobeaute.com.br"`, `torre_service_key: SecretStr = SecretStr("")`
  - `CAMINHOS_PUBLICOS = ("/health", "/ready", "/api/v1/alertas/", "/api/v1/b2b-revenda/")`: os dois primeiros casam por igualdade, os terminados em `/` por prefixo.
  - `SERVICE_KEY_HEADER = "x-torre-service-key"`, `ATOR_SERVICO = "servico:oraculo-mcp"`, `HEADER_AUTH_NEGADA = "X-Torre-Auth"` (valor `sessao-ausente`)
  - `@dataclass(frozen=True) class Decisao: liberar: bool; status: int = 200; codigo: str = ""; ator: Optional[str] = None`
  - `decidir(*, metodo: str, caminho: str, cookie_sessao: Optional[str], chave_servico: Optional[str], modo: str, segredo: str, dominios: tuple[str, ...], chave_servico_esperada: str) -> Decisao`: função pura.
  - `class GateDeSessao` (ASGI puro: `__init__(self, app)`, `async __call__(self, scope, receive, send)`). Lê `settings` a cada request e grava `scope["state"]["ator"]` quando houver ator.

**Regras de `decidir` (nesta ordem):**
1. `modo == "off"` → libera, sem ator.
2. `metodo == "OPTIONS"` ou caminho público → libera, sem ator.
3. Modo fora de `{"off", "report", "enforce"}` conta como `"enforce"`.
4. `segredo` com menos de 32 caracteres → `Decisao(False, 503, "auth_nao_configurada")`.
5. Chave de serviço esperada com ≥ 32 caracteres e `compare_digest` igual → libera com `ator=ATOR_SERVICO`.
6. `validar_sessao` OK → libera com `ator=sessao.email`.
7. Senão → `Decisao(False, 401, "sessao_ausente")`.

**Wrapper ASGI:** quando `decidir` nega e o modo é `report`, registra `logger.warning("auth_report negaria metodo=%s caminho=%s codigo=%s ua=%s", …)` e deixa passar. Em `enforce`, responde JSON `{"detail": "Autenticação obrigatória."}` (401) ou `{"detail": "Autenticação da Torre não configurada."}` (503), com `Cache-Control: no-store` e, só no 401, `X-Torre-Auth: sessao-ausente`. Escopos que não são `http` passam direto.

- [ ] **Passo 1: escrever os testes que falham.** Montar um `FastAPI()` mínimo com `GateDeSessao` e as rotas `/api/v1/x` (GET), `/health`, `/api/v1/alertas/y` (POST), `/api/v1/b2b-revenda/z` e `/api/v1/stream` (um `StreamingResponse` de 3 pedaços). A rota `/api/v1/x` devolve `request.state.ator`. Fazer `monkeypatch` em `settings`. Casos:

```python
def test_off_libera_sem_cookie(cli_off): assert cli_off.get("/api/v1/x").status_code == 200
def test_enforce_sem_cookie_401_com_header(cli):
    r = cli.get("/api/v1/x"); assert r.status_code == 401
    assert r.headers["X-Torre-Auth"] == "sessao-ausente"; assert r.headers["Cache-Control"] == "no-store"
def test_enforce_cookie_valido_injeta_ator(cli):
    r = cli.get("/api/v1/x", cookies={"torre_session": tok("Joao@Gocase.com")}); assert r.json() == {"ator": "joao@gocase.com"}
def test_publicos_passam(cli):
    assert cli.get("/health").status_code == 200
    assert cli.post("/api/v1/alertas/y").status_code == 200
    assert cli.get("/api/v1/b2b-revenda/z").status_code == 200
    assert cli.get("/healthz").status_code in (401, 404)   # prefixo de /health nao vale
def test_options_passa(cli): assert cli.options("/api/v1/x", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}).status_code != 401
def test_chave_servico(cli):
    assert cli.get("/api/v1/x", headers={"x-torre-service-key": "k" * 32}).json() == {"ator": "servico:oraculo-mcp"}
    assert cli.get("/api/v1/x", headers={"x-torre-service-key": "errada"}).status_code == 401
def test_chave_servico_curta_no_servidor_nao_vale(cli_chave_curta):  # TORRE_SERVICE_KEY="curta" e header "curta"
    assert cli_chave_curta.get("/api/v1/x", headers={"x-torre-service-key": "curta"}).status_code == 401
def test_modo_desconhecido_vira_enforce(cli_modo_typo): assert cli_modo_typo.get("/api/v1/x").status_code == 401
def test_segredo_ausente_503(cli_sem_segredo): assert cli_sem_segredo.get("/api/v1/x").status_code == 503
def test_report_loga_e_libera(cli_report, caplog):
    assert cli_report.get("/api/v1/x?token=abc").status_code == 200
    assert "auth_report negaria" in caplog.text and "token=abc" not in caplog.text
def test_stream_passa_inteiro(cli): assert cli.get("/api/v1/stream", cookies={"torre_session": tok()}).text == "abc"
def test_nada_sensivel_no_log(cli, caplog):
    t = tok(); cli.get("/api/v1/x", cookies={"torre_session": t}); assert t not in caplog.text
def test_main_registra_gate_dentro_do_cors():
    from app.main import app
    nomes = [m.cls.__name__ for m in app.user_middleware]
    assert nomes.index("CORSMiddleware") < nomes.index("GateDeSessao")  # user_middleware[0] = mais externo
```

- [ ] **Passo 2: rodar e ver falhar.** `cd apps/api && .venv/Scripts/python -m pytest tests/test_gate_sessao.py -v` → FAIL (falta o módulo).
- [ ] **Passo 3: implementar** `gate_sessao.py`, os campos em `config.py` (comentário curto em cada um, no estilo do arquivo) e o registro em `main.py`.
- [ ] **Passo 4: rodar e ver passar**, e rodar a suíte inteira: `.venv/Scripts/python -m pytest -q` → tudo verde. O modo padrão `off` não pode quebrar nenhum teste existente.
- [ ] **Passo 5: commit.** `git commit -m "feat(auth): gate de sessao no FastAPI com modos off/report/enforce (AUTH-GOOGLE-2)"`

---

### Tarefa 3: Módulo de sessão no Next (porta do `session.ts` do GoHunter)

**Arquivos:**
- Criar: `apps/web/src/server/auth/session.ts`
- Testes: `apps/web/tests/auth-session.test.ts` (e adicionar ao script `test`)

**Interfaces:**
- Produz (módulo puro: recebe `env`, não lê `process.env`, igual ao padrão de `src/server/oracle/access.ts`):
  - `SESSION_COOKIE = "torre_session"`, `SESSION_ISSUER = "torre-mktplace"`, `SESSION_AUDIENCE = "torre-mktplace-api"`, `SESSION_TTL_SECONDS = 604800`
  - `interface AuthEnv { AUTH_GOOGLE_ID?: string; AUTH_SECRET?: string; ALLOWED_EMAIL_DOMAINS?: string; NODE_ENV?: string }`
  - `interface SessionData { email: string; name?: string; picture?: string }`
  - `interface GoogleIdentity { email: string; emailVerified: boolean; name?: string; picture?: string }`
  - `authEnabled(env: AuthEnv): boolean`: `AUTH_GOOGLE_ID` não vazio.
  - `allowedDomains(env): string[]`, `isAllowedEmail(email: string | null | undefined, env): boolean`
  - `createSessionToken(data: SessionData, env): Promise<string>`: lança erro se o segredo tiver menos de 32 caracteres.
  - `verifySessionToken(token: string | null | undefined, env): Promise<SessionData | null>`
  - `verifyGoogleIdToken(idToken: string, env, keys?: JWTVerifyGetKey): Promise<GoogleIdentity>`: `keys` padrão = `createRemoteJWKSet(new URL("https://www.googleapis.com/oauth2/v3/certs"))` criado uma vez no módulo. Issuers aceitos: `["https://accounts.google.com", "accounts.google.com"]`, audience = `AUTH_GOOGLE_ID`. Aceita `email_verified` `true` ou `"true"`.
  - `sessionCookieHeader(token: string, env, maxAge = SESSION_TTL_SECONDS): string`: o valor do `Set-Cookie`, mesmo formato do `setCookie` do GoHunter.
  - `readCookie(req: Request, name: string): string | undefined`

- [ ] **Passo 1: escrever os testes que falham.** Chaves sintéticas com `generateKeyPair("RS256")` + `createLocalJWKSet`, no estilo de `tests/oracle-oauth.test.ts`:
  - `isAllowedEmail`: os mesmos casos da Tarefa 1 (`JOAO@GOCASE.COM` entra; `x@gocase.com.evil.com`, `x@mail.gocase.com` e `x@gocase.com@evil.com` não entram).
  - Ida e volta: `verifySessionToken(await createSessionToken({email:"joao@gocase.com"}, env), env)` devolve `{ email: "joao@gocase.com" }`. O payload decodificado tem `iss === "torre-mktplace"` e `aud === "torre-mktplace-api"`.
  - Com `ALLOWED_EMAIL_DOMAINS="gobeaute.com.br"`, a mesma sessão de `@gocase.com` passa a dar `null`.
  - Segredo curto: `createSessionToken` rejeita e `verifySessionToken` devolve `null`.
  - `verifyGoogleIdToken`: token com aud certo → identidade com `emailVerified === true` e e-mail em minúsculas. aud errado, issuer errado, expirado ou outra chave → rejeita.
  - `sessionCookieHeader`: em produção contém `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/` e `Max-Age=604800`; em desenvolvimento não contém `Secure`; com `maxAge` 0 contém `Max-Age=0`.
- [ ] **Passo 2: rodar e ver falhar.** `cd apps/web && node --test tests/auth-session.test.ts` → FAIL (módulo inexistente).
- [ ] **Passo 3: implementar** portando `GoHunter/src/lib/session.ts`, com estas diferenças: `env` injetado, `iss`/`aud` próprios, piso de 32 caracteres no segredo e `keys` injetável.
- [ ] **Passo 4: rodar e ver passar.** Mesmo comando → PASS. Depois `npm run typecheck`.
- [ ] **Passo 5: commit.** `git commit -m "feat(auth): sessao propria com jose, porta do GoHunter (AUTH-GOOGLE-3)"`

---

### Tarefa 4: Rotas `/api/auth/google`, `/api/auth/logout` e `/api/auth/me`

**Arquivos:**
- Criar: `apps/web/src/server/auth/handlers.ts` (lógica testável)
- Criar: `apps/web/app/api/auth/google/route.ts`, `apps/web/app/api/auth/logout/route.ts`, `apps/web/app/api/auth/me/route.ts`. São cascas finas: `export async function POST(req: Request) { return handleGoogleLogin(req, process.env); }`, com `export const dynamic = "force-dynamic"`.
- Testes: `apps/web/tests/auth-routes.test.ts`

**Interfaces:**
- Consome: tudo da Tarefa 3.
- Produz:
  - `handleGoogleLogin(req: Request, env: AuthEnv, keys?: JWTVerifyGetKey): Promise<Response>`
  - `handleLogout(req: Request, env: AuthEnv): Promise<Response>`
  - `handleMe(req: Request, env: AuthEnv): Promise<Response>`
  - Contrato de `/api/auth/me`, usado pela Tarefa 6:
    - auth desligada → `200 { authEnabled: false, email: "dev@local" }`
    - sessão válida → `200 { authEnabled: true, email, name, picture }`
    - sem sessão → `401 { error: "nao_autenticado", clientId: <AUTH_GOOGLE_ID> }`

**Respostas de `handleGoogleLogin`** (todas com `Cache-Control: no-store`):

| Situação | Status | Corpo |
|---|---|---|
| auth desligada | 404 | `{ error: "auth_desligada" }` |
| sem `credential` (string) no JSON | 400 | `{ error: "credential_ausente" }` |
| `verifyGoogleIdToken` lança | 401 | `{ error: "credencial_invalida" }` |
| `emailVerified` falso | 403 | `{ error: "email_nao_verificado" }` |
| domínio barrado | 403 | `{ error: "dominio_nao_permitido", message: "Acesso restrito a contas @gocase.com / @gobeaute.com.br." }` (lista vinda de `allowedDomains`) |
| ok | 200 | `{ ok: true, email, name, picture }` + `Set-Cookie` + `console.info("auth_login email=%s", email)` |

`handleLogout` sempre responde `200 { ok: true }` com o cookie `Max-Age=0`.

- [ ] **Passo 1: escrever os testes que falham.** Um teste para cada linha da tabela, mais os três estados de `/me`. Também conferir que nenhum corpo ou log contém o `credential` enviado.
- [ ] **Passo 2: rodar e ver falhar.** `node --test tests/auth-routes.test.ts` → FAIL.
- [ ] **Passo 3: implementar** `handlers.ts` e as três `route.ts`.
- [ ] **Passo 4: rodar e ver passar.** Depois `npm run typecheck` e `npm run build`. O build precisa listar `/api/auth/google`, `/api/auth/logout` e `/api/auth/me` como dinâmicas (`ƒ`).
- [ ] **Passo 5: commit.** `git commit -m "feat(auth): rotas de login, logout e me (AUTH-GOOGLE-4)"`

---

### Tarefa 5: API pela mesma origem (`/torre-api`) e `torreFetch`

**Arquivos:**
- Criar: `apps/web/src/lib/torre-api-proxy.ts`, `apps/web/src/lib/torre-fetch.ts`
- Alterar: `apps/web/next.config.ts` (`rewrites()` usando `torreApiRewrites(process.env.TORRE_API_ORIGIN)`)
- Alterar: os 13 pontos que chamam a API pelo navegador para usar `torreFetch` no lugar de `fetch`: `src/lib/api-client.ts` (linhas 158, 2299, 2340, 2386, 2436, 2490, 2536), `src/lib/b2b-revenda.ts:378`, `src/lib/dre-api.ts:35`, `src/lib/metas-api.ts:31` e `:88`, `src/lib/expedicao-demo.ts:45` (a Expedição e o painel TikTok passam por ele, via `buscarExpedicao`) e `app/brand/[brand]/page.tsx:277`. Links de download (`<a href>` com a base da API) não precisam de mudança: com a base relativa, o navegador já manda o cookie. Antes de começar, confirmar a lista com `grep -rn "fetch(" src app | grep -v src/server`.
- Testes: `apps/web/tests/torre-api-proxy.test.ts`, `apps/web/tests/torre-fetch.test.ts`

**Interfaces:**
- Produz:
  - `TORRE_API_PREFIX = "/torre-api"`
  - `torreApiRewrites(origin: string | undefined): { source: string; destination: string }[]`: vazio ou `undefined` → `[]`; `https://<host>` ou `http://localhost:<porta>` / `http://127.0.0.1:<porta>` → `[{ source: "/torre-api/:path*", destination: "<origin sem barra final>/:path*" }]`; qualquer outra coisa → lança `Error("TORRE_API_ORIGIN invalida")` (o build falha alto).
  - `EVENTO_SESSAO_EXPIRADA = "torre:sessao-expirada"`
  - `torreFetch(input: string, init?: RequestInit): Promise<Response>`: chama `fetch` com `credentials: "same-origin"` (o `init` do chamador prevalece em `signal` e `headers`). Se `res.status === 401` **e** `res.headers.get("X-Torre-Auth") === "sessao-ausente"` **e** `window` existe → `window.dispatchEvent(new Event(EVENTO_SESSAO_EXPIRADA))`. Devolve o `res` intacto.

- [ ] **Passo 1: escrever os testes que falham.**
  - `torreApiRewrites`: os casos acima, mais `"http://mktplace-api.onrender.com"` (http fora de localhost) e `"https://x.com/path"` (com path) lançam erro.
  - `torreFetch`, com `globalThis.fetch` e `globalThis.window` falsos: 401 com o header dispara o evento uma vez; 401 **sem** o header (é o caso do B2B com Auth0) não dispara; 200 não dispara; sem `window` não lança.
  - Teste de guarda (estilo dos `*-wiring.test.ts`): ler os arquivos fonte da lista acima e garantir que nenhum ainda tem `await fetch(` com `API_URL`/`API`/`apiUrl`/`BASE`. Todos passam por `torreFetch`.
- [ ] **Passo 2: rodar e ver falhar.**
- [ ] **Passo 3: implementar** os dois módulos, o `rewrites()` no `next.config.ts` e trocar `fetch` por `torreFetch` nos pontos listados. É uma troca mecânica: não mexer em parse, cache nem fallback.
- [ ] **Passo 4: verificar.** `npm test` e `npm run typecheck` passam. Depois um teste manual local: subir a API (`uvicorn app.main:app --port 8080` com `TORRE_AUTH_MODE=report` e `AUTH_SECRET` de 32 caracteres) e o web com `NEXT_PUBLIC_API_URL=/torre-api` e `TORRE_API_ORIGIN=http://localhost:8080`. Abrir `/`: os dados carregam e o log da API mostra `auth_report negaria … codigo=sessao_ausente` (prova de que a chamada passou pelo rewrite).
- [ ] **Passo 5: commit.** `git commit -m "feat(auth): API pela mesma origem e torreFetch (AUTH-GOOGLE-5)"`

> Plano B, só se a Tarefa 8 mostrar que a Vercel não repassa o `Cookie` no rewrite externo: trocar o rewrite por um route handler `app/torre-api/[...path]/route.ts` que valida a sessão e repassa com `x-torre-service-key`. Não fazer isso por antecipação.

---

### Tarefa 6: Tela de login, sessão no shell e botão Sair

**Arquivos:**
- Criar: `apps/web/src/components/auth/LoginGate.tsx` (porta do `GoHunter/src/components/LoginGate.tsx`), `apps/web/src/components/auth/ContaUsuario.tsx`, `apps/web/src/lib/login-mensagens.ts`
- Alterar: `apps/web/app/layout.tsx` (`<ThemeProvider><LoginGate><AppShell>…`), `apps/web/src/components/shell/Sidebar.tsx` (`<ContaUsuario />` junto do `ThemeToggle`, linha ~174), `apps/web/src/components/shell/Topbar.tsx` (perto da linha 65)
- Testes: `apps/web/tests/login-gate.test.ts`

**Interfaces:**
- Consome: contrato de `/api/auth/me` e `/api/auth/google` (Tarefa 4), `EVENTO_SESSAO_EXPIRADA` (Tarefa 5).
- Produz:
  - `interface SessaoUsuario { authEnabled: boolean; email: string; name?: string; picture?: string }`
  - `useSessao(): SessaoUsuario | null` (contexto fornecido pelo `LoginGate`)
  - `mensagemDeErroLogin(status: number, corpo: unknown): string`:
    - 403 `dominio_nao_permitido` → usa o `message` do corpo
    - 403 `email_nao_verificado` → "Seu e-mail Google não está verificado."
    - 401 → "Não foi possível validar sua conta Google. Tente de novo."
    - qualquer outro / falha de rede (status 0) → "Falha no login. Tente de novo."
  - `MENSAGEM_SESSAO_EXPIRADA = "Sua sessão expirou. Entre de novo para continuar."`

**Comportamento do `LoginGate`:** os estados são `checking | authed | anon`. Ao montar, faz `GET /api/auth/me`: 200 → `authed` (guarda a sessão no contexto); 401 → `anon` com o `clientId` do corpo. Em `anon`, carrega o GIS uma vez (`id="gis-script"`), chama `initialize({ client_id, callback })` e `renderButton(…, { theme: "outline", size: "large", text: "signin_with", shape: "pill", locale: "pt-BR" })`. O callback faz `POST /api/auth/google`; com OK, `window.location.reload()`, e com erro mostra `mensagemDeErroLogin`. Também escuta `EVENTO_SESSAO_EXPIRADA` → `anon` + `MENSAGEM_SESSAO_EXPIRADA`. Se o `/me` falhar por rede, fica em `anon` com "Falha no login. Tente de novo.", sem bloquear para sempre.

**Visual:** um cartão de vidro centralizado sobre o canvas (DESIGN.md §4), com a marca "Torre de Controle · Gobeaute", a linha "Acesso restrito a contas GoGroup." e o botão do Google. Usar só tokens semânticos (§7) para funcionar nos dois temas. O `checking` usa o `Skeleton` ou o spinner que já existem. `ContaUsuario` só aparece quando `authEnabled`: mostra e-mail (ou iniciais no shell compacto) e um botão "Sair" com alvo ≥ 44 px (piso de acessibilidade do projeto). O "Sair" faz `POST /api/auth/logout`, depois `window.google?.accounts?.id?.disableAutoSelect?.()`, depois `reload`.

- [ ] **Passo 1: escrever os testes que falham.** Unitários de `mensagemDeErroLogin` (os 4 ramos). Testes de ligação, lendo o código fonte: `app/layout.tsx` envolve `<AppShell>` com `<LoginGate>`; `Sidebar.tsx` e `Topbar.tsx` renderizam `<ContaUsuario`; `LoginGate.tsx` referencia `EVENTO_SESSAO_EXPIRADA` e `/api/auth/me`; `ContaUsuario.tsx` chama `/api/auth/logout`; nenhum arquivo novo usa cor literal (`#`, `bg-white`, `text-gray-`).
- [ ] **Passo 2: rodar e ver falhar.**
- [ ] **Passo 3: implementar.** Ler `DESIGN.md` antes.
- [ ] **Passo 4: verificar.** `npm test`, `npm run typecheck`, `npm run build`. Teste manual no navegador (skill `run`): com `AUTH_GOOGLE_ID` vazio, a Torre abre direto, sem botão Sair. Com `AUTH_GOOGLE_ID` preenchido e `http://localhost:3000` cadastrado como origem (Tarefa 8, passo 2), a tela de login aparece e entrar com `@gocase.com` abre a Torre. Apagar o cookie `torre_session` no DevTools e mudar de filtro volta para o login com a mensagem de sessão expirada, sem mostrar dados de demonstração.
- [ ] **Passo 5: commit.** `git commit -m "feat(auth): tela de login Google e botao Sair (AUTH-GOOGLE-6)"`

---

### Tarefa 7: Oráculo (MCP) com chave de serviço

**Arquivos:**
- Alterar: `apps/web/src/server/oracle/access.ts` (`AccessEnv.MCP_BACKEND_SERVICE_KEY?: string`; a decisão de acesso ganha `backendServiceKey?: string`, que fica `undefined` quando vazia), `apps/web/src/server/oracle/handler.ts:231` (repassar), `apps/web/src/server/oracle/server.ts:39` (repassar), `apps/web/src/server/oracle/torre-client.ts` (opção `serviceKey?: string`; quando definida, envia o header `x-torre-service-key`; atualizar o comentário "Sem credenciais" da linha ~198), `apps/web/src/server/oracle/metadata-route.ts` só se ele listar o env completo.
- Testes: um caso novo em `apps/web/tests/oracle-data.test.ts` (o arquivo que já cobre o `TorreClient`)

**Interfaces:**
- Consome: header `x-torre-service-key` e `ATOR_SERVICO` (Tarefa 2).
- Produz: `new TorreClient({ baseUrl, serviceKey?, fetchImpl?, … })`.

- [ ] **Passo 1: escrever os testes que falham.** Com `fetchImpl` falso que captura os headers: com `serviceKey: "k".repeat(32)` o header vai igual; sem `serviceKey` o header não existe. Em nenhum erro devolvido pelo client aparece a chave. `evaluateAccess` com `MCP_BACKEND_SERVICE_KEY` preenchido devolve `backendServiceKey` igual; vazio devolve `undefined`.
- [ ] **Passo 2: rodar e ver falhar.** `npm run test:mcp` → FAIL nos casos novos.
- [ ] **Passo 3: implementar.**
- [ ] **Passo 4: verificar.** `npm run test:mcp` e `npm test` → PASS.
- [ ] **Passo 5: commit.** `git commit -m "feat(auth): oraculo MCP chama a API com chave de servico (AUTH-GOOGLE-7)"`

---

### Tarefa 8: Configuração, documentação e virada em produção

**Arquivos:**
- Alterar: `.env.example` (bloco "Login Google" com `AUTH_GOOGLE_ID`, `AUTH_SECRET`, `ALLOWED_EMAIL_DOMAINS`, `TORRE_AUTH_MODE`, `TORRE_SERVICE_KEY`, `TORRE_API_ORIGIN`, `MCP_BACKEND_SERVICE_KEY`, cada um com uma linha dizendo onde vai, se Vercel, Render ou os dois). Também `NEXT_PUBLIC_API_URL`, com o comentário de que em produção o valor é `/torre-api`.
- Criar: `docs/AUTH_GOOGLE_RUNBOOK.md` (os passos abaixo, como checklist)
- Alterar: `README.md` (seção "Publicação atual": o front chama a API por `/torre-api`, e o CORS deixa de ser o caminho principal), `docs/DECISIONS.md` (entrada AUTH-GOOGLE com as decisões travadas do topo deste plano)

**Virada (cada passo verificado antes do próximo):**

- [ ] **1. API com o gate desligado.** Merge das Tarefas 1–2 na `main`, depois deploy pelo hook `RENDER_DEPLOY_HOOK_MKTPLACE_API`. No Render, configurar `AUTH_SECRET` (novo), `ALLOWED_EMAIL_DOMAINS=gocase.com,gobeaute.com.br`, `TORRE_SERVICE_KEY` (novo, `openssl rand -base64 32`) e `TORRE_AUTH_MODE=off`. Verificar: `curl -s https://mktplace-api.onrender.com/ready` → 200, e a Torre em produção segue igual.
- [ ] **2. Google Cloud Console** (manual, quem administra o OAuth Client do GoHunter): em *APIs & Services → Credentials →* client `398963590019-…`, adicionar as *Authorized JavaScript origins* `https://mktplace-ten.vercel.app` e `http://localhost:3000`. Não mexer nas origens do GoHunter. As URLs de preview da Vercel ficam de fora de propósito (o README já diz que não são o endereço da Torre).
- [ ] **3. Front com login.** Merge das Tarefas 3–7. Na Vercel (Production), configurar `AUTH_GOOGLE_ID` (o mesmo do GoHunter), `AUTH_SECRET` (o mesmo valor do Render), `ALLOWED_EMAIL_DOMAINS`, `TORRE_API_ORIGIN=https://mktplace-api.onrender.com`, `NEXT_PUBLIC_API_URL=/torre-api` e `MCP_BACKEND_SERVICE_KEY` (o mesmo valor de `TORRE_SERVICE_KEY`). Redeploy. Verificar: o login com conta `@gocase.com` funciona; uma conta pessoal `@gmail.com` recebe "Acesso restrito…"; no DevTools, as chamadas vão para `/torre-api/...` com status 200.
- [ ] **4. Observar.** No Render, `TORRE_AUTH_MODE=report`. Por 1–2 dias úteis, filtrar os logs por `auth_report negaria`. As linhas com UA de navegador logo depois do deploy são esperadas (abas antigas). Qualquer UA de máquina (python-requests, Airflow, n8n, curl) é um consumidor desconhecido: identificar e dar a ele `TORRE_SERVICE_KEY` ou incluir na allowlist da Tarefa 2 com teste. Também confirmar que o cookie chega pelo rewrite: com a sessão aberta, as rotas da Torre **não** aparecem no `auth_report`. Se aparecerem, aplicar o Plano B da Tarefa 5.
- [ ] **5. Ligar.** `TORRE_AUTH_MODE=enforce`. Verificar: `curl -s -o /dev/null -w "%{http_code}" https://mktplace-api.onrender.com/api/v1/performance/overview` → `401`; `/ready` → 200; uma ferramenta do Oráculo MCP responde com dado; a Torre logada funciona em todas as páginas do menu.
- [ ] **Rollback:** `TORRE_AUTH_MODE=off` no Render reabre a API sem deploy de código. Para tirar a tela de login, esvaziar `AUTH_GOOGLE_ID` na Vercel e fazer redeploy.
- [ ] **Commit da documentação.** `git commit -m "docs(auth): runbook e variaveis do login Google (AUTH-GOOGLE-8)"`

## Riscos aceitos

- **Mesmo client ID do GoHunter:** um ID token emitido para o GoHunter (validade de cerca de 1 h) também passaria no `/api/auth/google` da Torre, porque o `aud` é o mesmo. O público e os domínios são os mesmos, então o risco é baixo. Se um dia isso incomodar, criar um client ID próprio da Torre. É só trocar o `AUTH_GOOGLE_ID`, sem mudar código.
- **Sessão de 7 dias sem revogação individual:** para derrubar todo mundo, basta trocar o `AUTH_SECRET` (nos dois lados). Para barrar um domínio, `ALLOWED_EMAIL_DOMAINS` vale na hora, porque o domínio é checado de novo em toda leitura.
