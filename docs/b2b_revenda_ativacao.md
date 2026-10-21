# B2B / Revendedores — passo a passo do Auth0 e gate único de ativação

**Gates:** OCTAPRICE-2 (API protegida) e OCTAPRICE-3 (login web + BFF).
Contrato de dados: [`b2b_revenda_contrato.md`](b2b_revenda_contrato.md).

Nada aqui foi executado. Este documento é a ordem exata para ligar a aba, com
uma verificação em cada passo e o rollback.

> **Nunca** cole Client Secret, chave da Torre B2B, segredo de sessão, token ou
> cookie em chat, e-mail, issue, documento ou commit. Cada valor vai **direto**
> na variável do painel (Auth0 → Vercel/Render).

## Como funciona

```
navegador ──cookie HttpOnly──► Next (BFF, /api/b2b/*) ──Bearer──► FastAPI (/api/v1/b2b-revenda) ──apikey──► Torre B2B
                                │                                 │
                                └─ login/callback/logout ◄─► Auth0 (tenant gobeaute-oraculo)
```

- Login por **Authorization Code + PKCE**, cliente confidencial. A troca do
  code acontece no servidor da Vercel.
- O access token fica **só** dentro do cookie `__Host-b2b_sessao`
  (`HttpOnly; Secure; SameSite=Lax`), criptografado (A256GCM) com
  `B2B_AUTH_SESSION_SECRET`. O navegador não lê o token, e nada vai para
  localStorage ou sessionStorage.
- Só a aba B2B exige login. Não há middleware global; o resto da Torre não muda.
- Com `NEXT_PUBLIC_B2B_REVENDA_MODE` diferente de `api`, todas as rotas
  `/api/b2b/*` respondem 404.

| Situação | O que a pessoa vê |
|---|---|
| sem sessão | "Entre com a conta corporativa" + botão de login |
| sessão expirada | "Sua sessão expirou" + botão de login |
| logada sem `b2b_revenda:read` | "Sua conta não tem acesso" + sair/trocar de conta (nenhuma chamada à API) |
| auth não configurada (Vercel ou Render) | "Login corporativo ainda não configurado" |
| logada com permissão | dados (ou "indisponível" com o motivo, nunca zero) |

---

## Parte A — Auth0 (dono do tenant `gobeaute-oraculo`)

### A1. API (audience)

1. **Applications → APIs → + Create API**.
2. **Name:** `Torre de Marketplaces — B2B Revenda`.
3. **Identifier:** `https://mktplace-api.onrender.com/api/v1/b2b-revenda`.
   É a audience. Não pode ser alterada depois.
4. **JSON Web Token (JWT) Signing Algorithm:** `RS256`.
5. Na aba **Settings** da API:
   - **Maximum Access Token Lifetime / Token Expiration:** `28800` (8 h).
     A sessão da Torre nunca passa de 12 h nem do vencimento do token.
   - **Allow Skipping User Consent:** ligado (aplicação first-party).
   - **Allow Offline Access:** desligado (não usamos refresh token).
6. Ainda em **Settings**, seção **RBAC Settings**:
   - **Enable RBAC:** ligado.
   - **Add Permissions in the Access Token:** ligado.
7. Aba **Permissions** → adicionar:
   - **Permission:** `b2b_revenda:read`
   - **Description:** `Ler agregados da aba B2B / Revendedores`
8. **Save**.

### A2. Aplicação (login da web)

1. **Applications → Applications → + Create Application**.
2. **Name:** `Torre de Marketplaces — Web (B2B)`. **Type:** `Regular Web Applications`.
3. Aba **Settings**:
   - **Allowed Callback URLs:**
     `https://mktplace-gobeaute.vercel.app/api/b2b/auth/callback`
   - **Allowed Logout URLs:**
     `https://mktplace-gobeaute.vercel.app/monitoramento-preco`
   - **Allowed Web Origins:** vazio (o fluxo é todo no servidor).
   - **Application Login URI** (opcional):
     `https://mktplace-gobeaute.vercel.app/api/b2b/auth/login`
   - Para testar localmente, acrescente `http://localhost:3000/api/b2b/auth/callback`
     e `http://localhost:3000/monitoramento-preco` nas listas acima, separados por vírgula.
4. Aba **Credentials** → **Authentication Method:** `Client Secret (Post)`.
   O BFF envia o segredo no corpo do POST ao `/oauth/token`.
5. **Settings → Advanced Settings → Grant Types:** só `Authorization Code`.
   Desmarque Implicit, Refresh Token e Client Credentials.
6. Aba **Connections:** só a conexão **Google** corporativa (`google-oauth2`).
   Desligue `Username-Password-Authentication` e as demais.
7. **Save Changes**.
8. Copie **Client ID** e **Client Secret** direto para as variáveis da Vercel
   (Parte C). Não passe por nenhum outro lugar.

> **A Action do Oráculo não restringe esta aplicação.** Ela só age sobre os
> clients do Oráculo; para os demais, retorna sem negar nem conceder nada.
> Por isso o controle de acesso aqui é o **role** (A3): qualquer conta Google
> consegue logar, mas sem o role não recebe `b2b_revenda:read`, vê "Sua conta
> não tem acesso" e nenhum dado sai. Para barrar já no login quem não é
> `gocase.com`/`gobeaute.com.br`, crie uma Action própria (fora deste gate).

### A3. Role e usuários autorizados

1. **User Management → Roles → + Create Role**.
   - **Name:** `Torre B2B Revenda — Leitura`
   - **Description:** `Acesso à aba B2B / Revendedores (agregados do OctaPrice)`
2. No role, aba **Permissions → Add Permissions** → API
   `Torre de Marketplaces — B2B Revenda` → marque `b2b_revenda:read` → **Add Permissions**.
3. Aba **Users → Assign Users** → selecione as pessoas autorizadas (comercial,
   operação, admin).
   - Usuário Google só existe no Auth0 depois do **primeiro login**. Se a
     pessoa não aparecer, peça que ela entre uma vez pela aba (verá "Sua conta
     não tem acesso"), atribua o role e peça que ela saia e entre de novo.
4. Para revogar: remova o usuário do role. O acesso cai no próximo login, ou
   em até 8 h (vencimento do token).

---

## Parte B — Credencial da Torre B2B (dono da Torre B2B)

Uma **secret exclusiva** para a Torre de Marketplaces (curto prazo), ou o
acesso somente leitura às seis RPCs (definitivo). Ver
[`b2b_revenda_contrato.md`](b2b_revenda_contrato.md), seção "Credencial". A
chave usada na sondagem **não** é reaproveitada.

---

## Parte C — Variáveis (só no gate, direto nos painéis)

**Render — serviço da API:**

| Variável | Valor |
|---|---|
| `API_AUTH_ISSUER` | `https://gobeaute-oraculo.us.auth0.com/` |
| `API_AUTH_AUDIENCE` | `https://mktplace-api.onrender.com/api/v1/b2b-revenda` |
| `B2B_REVENDA_REQUIRED_SCOPE` | `b2b_revenda:read` (é o padrão) |
| `B2B_REVENDA_SUPABASE_URL` | raiz do projeto da Torre B2B |
| `B2B_REVENDA_SUPABASE_KEY` | a credencial **exclusiva** (Parte B) |
| `B2B_REVENDA_ENABLED` | `true` (só no passo 4 do gate) |

**Vercel — projeto `mktplace`, só no ambiente Production** (Preview não está
nas Callback URLs e o login falharia):

| Variável | Valor |
|---|---|
| `B2B_AUTH_ISSUER` | `https://gobeaute-oraculo.us.auth0.com/` |
| `B2B_AUTH_AUDIENCE` | `https://mktplace-api.onrender.com/api/v1/b2b-revenda` |
| `B2B_AUTH_CLIENT_ID` | Client ID da aplicação A2 |
| `B2B_AUTH_CLIENT_SECRET` | Client Secret da aplicação A2 (Sensitive) |
| `B2B_AUTH_SESSION_SECRET` | 48+ bytes aleatórios, ex.: `openssl rand -base64 48` (Sensitive) |
| `B2B_AUTH_APP_BASE_URL` | `https://mktplace-gobeaute.vercel.app` |
| `B2B_API_BASE_URL` | `https://mktplace-api.onrender.com` |
| `NEXT_PUBLIC_B2B_REVENDA_MODE` | `api` (só no passo 6 do gate; exige novo build) |

Nenhuma dessas variáveis, exceto `NEXT_PUBLIC_B2B_REVENDA_MODE`, chega ao
navegador. Há teste que trava isso, e o bundle é varrido no QA.

---

## Gate único de ativação

Cada passo só começa com o anterior verificado.

| # | Passo | Verificação | Rollback |
|---|---|---|---|
| 1 | Parte A feita no Auth0 | API com permission, aplicação com URLs exatas, role com ≥ 1 usuário | apagar app/role |
| 2 | Credencial exclusiva da Torre B2B emitida (Parte B) | dono confirma o nome e que a chave é só nossa | revogar a chave |
| 3 | Render: `API_AUTH_*`, `B2B_REVENDA_SUPABASE_*` + **redeploy manual** | `/api/v1/b2b-revenda/resumo` sem token = **401**; com token sem permission = **403**; `/openapi.json` lista as 4 rotas | remover as variáveis |
| 4 | Render: `B2B_REVENDA_ENABLED=true` | com token válido: `/frescor` 200 `ok`, dia de hoje ou ontem; `/resumo` reconcilia com as RPCs (mesmo dia, sem Amazon) | `B2B_REVENDA_ENABLED=false` |
| 5 | Vercel (Production): `B2B_AUTH_*` e `B2B_API_BASE_URL` | nada muda na tela (flag ainda off): `/monitoramento-preco` sem seletor, `/revendedores` 404 | remover as variáveis |
| 6 | Vercel: `NEXT_PUBLIC_B2B_REVENDA_MODE=api` + redeploy | smoke abaixo | remover a variável + redeploy (volta ao 404) |

**Smoke do passo 6** (desktop e celular):
- sem login: aba mostra "Entre com a conta corporativa"; nenhuma requisição
  sai para a FastAPI;
- login com conta **com** role: volta para a aba com dados, "Conectado como …";
  o frescor bate com o dia;
- login com conta **sem** role: "Sua conta não tem acesso"; 0 chamadas à API;
- **Sair**: volta para `/monitoramento-preco` e a aba pede login de novo;
- `document.cookie` no console **não** mostra `__Host-b2b_sessao` (é HttpOnly);
- bundle e respostas sem token, sem `B2B_AUTH_*` e sem a chave da Torre B2B;
- lojas próprias e o restante da Torre continuam abrindo **sem login**.

Fora do gate (depois): rotas com dado de cliente (seller, cliente, vendedor,
reincidência), com permission própria; refresh token, se a sessão de 8 h for
curta.
