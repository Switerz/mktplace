# Login Google: configuração e virada em produção

Runbook para quem opera a Torre. Cada passo é verificado antes do próximo. Plano de origem: `docs/superpowers/plans/2026-11-03-login-google.md`. Domínio canônico: `https://mktplace-ten.vercel.app` (o anterior, `mktplace-gobeaute.vercel.app`, não vale mais).

## Variáveis

**Render (API)**

| Variável | Valor |
|---|---|
| `TORRE_AUTH_MODE` | `off` (padrão), `report` ou `enforce`. Vazio = `off`. Valor desconhecido = `enforce`. |
| `AUTH_SECRET` | ≥ 32 caracteres, o mesmo valor da Vercel. Novo, não é o do GoHunter. `openssl rand -base64 32`. |
| `ALLOWED_EMAIL_DOMAINS` | Padrão `gocase.com,gobeaute.com.br`. |
| `TORRE_SERVICE_KEY` | ≥ 32 caracteres. Novo. `openssl rand -base64 32`. |

**Vercel (web, Production)**

| Variável | Valor |
|---|---|
| `AUTH_GOOGLE_ID` | Mesmo OAuth client do GoHunter (`398963590019-….apps.googleusercontent.com`). Vazio desliga o login (`dev@local`). |
| `AUTH_SECRET` | O mesmo valor do Render. |
| `ALLOWED_EMAIL_DOMAINS` | Igual ao do Render. |
| `TORRE_API_ORIGIN` | `https://mktplace-api.onrender.com` |
| `NEXT_PUBLIC_API_URL` | `/torre-api` |
| `MCP_BACKEND_SERVICE_KEY` | O mesmo valor de `TORRE_SERVICE_KEY` do Render (usado pelo Oráculo MCP). |

Não existe `NEXT_PUBLIC_AUTH_GOOGLE_ID`: o front recebe o client ID no corpo do 401 de `/api/auth/me`.

**Guarda de build.** Se `NEXT_PUBLIC_API_URL` for relativo (`/torre-api`), `TORRE_API_ORIGIN` precisa estar definido, senão o `next build` falha (`assertTorreApiConfig`). Confira as duas variáveis na Vercel antes do redeploy.

**Rotas e cookie.** `POST /api/auth/google`, `POST /api/auth/logout`, `GET /api/auth/me`. Cookie `torre_session`, validade de 7 dias. Rotas da API públicas (sem sessão): `/health`, `/ready`, `/api/v1/alertas/*`, `/api/v1/b2b-revenda/*`. Sem sessão, a API responde 401 com o header `X-Torre-Auth: sessao-ausente`.

## Virada

- [ ] **1. API com o gate desligado.** Merge das Tarefas 1–2 na `main`; deploy pelo hook `RENDER_DEPLOY_HOOK_MKTPLACE_API` (variável de ambiente do usuário; um POST publica a `main`). No Render, configurar `AUTH_SECRET` (novo), `ALLOWED_EMAIL_DOMAINS=gocase.com,gobeaute.com.br`, `TORRE_SERVICE_KEY` (novo) e `TORRE_AUTH_MODE=off`.
  - Verificar: `curl -s -o /dev/null -w "%{http_code}" https://mktplace-api.onrender.com/ready` → `200`, e a Torre em produção segue igual.
- [ ] **2. Google Cloud Console** (manual, por quem administra o OAuth Client do GoHunter). Em *APIs & Services → Credentials →* client `398963590019-…`, adicionar as *Authorized JavaScript origins* `https://mktplace-ten.vercel.app` e `http://localhost:3000`. Não mexer nas origens do GoHunter. URLs de preview da Vercel ficam de fora de propósito.
  - Verificar: as duas origens aparecem na lista, e as do GoHunter continuam lá.
- [ ] **3. Front com login.** Merge das Tarefas 3–7. Na Vercel (Production), configurar `AUTH_GOOGLE_ID`, `AUTH_SECRET` (igual ao do Render), `ALLOWED_EMAIL_DOMAINS`, `TORRE_API_ORIGIN=https://mktplace-api.onrender.com`, `NEXT_PUBLIC_API_URL=/torre-api` e `MCP_BACKEND_SERVICE_KEY` (igual ao `TORRE_SERVICE_KEY`). Redeploy.
  - Verificar: login com conta `@gocase.com` funciona; conta pessoal `@gmail.com` recebe "Acesso restrito…"; no DevTools, as chamadas vão para `/torre-api/...` com status 200.
- [ ] **4. Observar (`report`).** No Render, `TORRE_AUTH_MODE=report`. Por 1–2 dias úteis, filtrar os logs por `auth_report negaria` (formato: `auth_report negaria metodo=… caminho=… codigo=… ua=…`).
  - UA de navegador logo após o deploy é esperado (abas antigas).
  - Qualquer UA de máquina (python-requests, Airflow, n8n, curl) é consumidor desconhecido: identificar e dar a ele `TORRE_SERVICE_KEY`, ou incluir na allowlist da Tarefa 2 com teste.
  - Confirmar que o cookie chega pelo rewrite: com a sessão aberta, as rotas da Torre **não** devem aparecer no `auth_report`. Se aparecerem, aplicar o Plano B da Tarefa 5.
  - **Verificar:** o Oráculo MCP (`ORACLE_API_AUDIENCE` e o recurso MCP canônico) pode ainda referenciar o domínio antigo `mktplace-gobeaute.vercel.app`. Conferir antes de ligar o `enforce`; este runbook não altera o código do Oráculo.
- [ ] **5. Ligar (`enforce`).** `TORRE_AUTH_MODE=enforce` no Render.
  - Verificar: `curl -s -o /dev/null -w "%{http_code}" https://mktplace-api.onrender.com/api/v1/performance/overview` → `401`; `/ready` → `200`; uma ferramenta do Oráculo MCP responde com dado; a Torre logada funciona em todas as páginas do menu.
- [ ] **Rollback.** `TORRE_AUTH_MODE=off` no Render reabre a API sem deploy de código. Para tirar a tela de login, esvaziar `AUTH_GOOGLE_ID` na Vercel e fazer redeploy.

## Segredos

Nunca registrar em log, issue, PR ou documento os valores de `AUTH_SECRET`, `TORRE_SERVICE_KEY`, `MCP_BACKEND_SERVICE_KEY` nem o client ID completo.
