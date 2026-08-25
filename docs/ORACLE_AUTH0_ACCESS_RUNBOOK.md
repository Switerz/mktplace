# Oráculo — acesso corporativo, rollout multi-organização e offboarding

**Estado deste documento (25/08/2026):** a política corporativa do OM3 foi
**versionada no commit de fechamento deste gate** —
[infra/auth0/actions/oracle-corporate-access.js](../infra/auth0/actions/oracle-corporate-access.js),
coberta por 34 testes puros. **A Action NÃO foi publicada nem aplicada no
Auth0**, e nenhuma validação multiusuário foi executada: versionar o código não
o coloca em vigor. Não há ferramenta autenticada de Auth0 nesta sessão, e nenhuma
foi instalada. **Enquanto a Action não for publicada, o acesso continua
dependendo de role manual.** Os passos abaixo são para o proprietário executar no
dashboard.

> **Nunca cole neste repositório, em issue, log, chat ou documento:** Client
> Secret, access token, refresh token, authorization code, cookie de sessão ou
> JWT bruto. Client IDs completos também não — eles entram apenas como *secret*
> da Action.

---

## 1. Modelo de autorização

### Antes

Cada pessoa precisava existir no Auth0 **e** receber manualmente uma role com a
permission `oracle:read`. O claim `permissions` do token era a única fonte de
autorização na prática. Onboarding era um passo manual por pessoa; esquecer o
passo significava um 403 no primeiro uso.

### Depois

Uma **Post-Login Action (trigger v3)** concede `oracle:read` automaticamente a
quem satisfaz, simultaneamente:

1. o login veio por um **OAuth Client do Oráculo** (allowlist em secret);
2. a conexão é **Google** (`google-oauth2`);
3. `email_verified === true`;
4. o domínio do e-mail é **exatamente** `gocase.com` ou `gobeaute.com.br`;
5. a audience pedida, quando o runtime a expõe, é a API do Oráculo.

Falhou qualquer condição → `api.access.deny()`. Nenhum acesso por omissão.

### Por que `addScope` e não role automática

O resource server une os claims `permissions` **e** `scope` ao decidir
(`effectiveScopes` em [oauth.ts](../apps/web/src/server/oracle/oauth.ts)), então
um scope adicionado pela Action autoriza sem tocar em RBAC. Atribuir role pela
Management API exigiria credencial M2M, teria rate limit e **não afetaria o
primeiro token** — só o login seguinte. A Action vale imediatamente.

A role manual do proprietário pode **permanecer como legado/admin**; ela deixa de
ser requisito para usuários corporativos.

---

## 2. Publicar a Action (proprietário)

1. **Auth0 Dashboard → Actions → Library → Build Custom**.
2. Nome: `Oraculo — acesso corporativo`.
3. **Trigger: `Login / Post Login`, versão `v3`.**
   `api.accessToken.addScope` **não existe** em versões anteriores — publicar em
   v2 falha em silêncio, concedendo nada.
4. Cole o conteúdo de
   [`infra/auth0/actions/oracle-corporate-access.js`](../infra/auth0/actions/oracle-corporate-access.js)
   **na íntegra**. Os `exports` extras no fim são para teste; o runtime os ignora.
5. Em **Secrets** (ícone de chave), adicione:

   | Chave | Valor | Obrigatório |
   |---|---|---|
   | `ORACLE_CLIENT_IDS` | Client IDs do Oráculo separados por vírgula, um por organização Claude | sim |
   | `ORACLE_API_AUDIENCE` | `https://mktplace-gobeaute.vercel.app/api/mcp` | sim |
   | `ORACLE_CONNECTIONS` | nome da conexão Google, se quiser restringir por nome | opcional |

   Sem `ORACLE_CLIENT_IDS`, nenhum cliente casa e a Action **não faz nada** — o
   MCP então nega por falta de scope. Fail-closed, não fail-open.
6. **Deploy**.
7. **Actions → Flows → Login** → arraste a Action para o fluxo, **antes da
   emissão final do token**. Só depois do Deploy ela aparece na lista.
8. **Applications → cada client do Oráculo → Connections**: confirme que apenas
   a conexão Google esperada está habilitada.
9. **APIs → Oráculo**: mantenha RBAC e *Add Permissions in the Access Token*
   como já estão — a Action soma ao `scope`, não substitui o contrato vigente.

### Verificação obrigatória logo após publicar

- **Auth0 → Monitoring → Logs**: um login corporativo deve aparecer como
  `Success Login`; um externo, como falha com a mensagem genérica de deny.
- **Não confirme sucesso pelo dashboard apenas.** A prova é o `tools/list`
  retornar 5 tools no Claude, com uma conta que **nunca teve role manual**.

### Login inicial × refresh token exchange

**Um único handler cobre os dois fluxos.** O Post-Login executa tanto no login
interativo quanto na troca por refresh token, e o fluxo é identificável por
`event.transaction.protocol === "oauth2-refresh-token"` — valor documentado pelo
Auth0 para esse campo, e usado pela própria documentação em exemplos que pulam
MFA na renovação.

**Não existe `onExecuteCredentialsExchange` nesta Action, e não deve existir.**
Aquele é o trigger de Client Credentials/M2M, cujo evento sequer carrega
`event.user` — exportar o handler de Post-Login sob aquele nome misturaria
contratos e ainda assim não cobriria refresh.

A política **não ramifica por protocolo**: a mesma decisão de domínio vale nos
dois fluxos, de propósito. Um refresh não pode virar caminho para escapar da
regra que o login inicial aplicou — há teste dedicado a isso.

**Por que o offboarding ainda exige revogação.** Não é por causa do refresh: é
porque um access token **já emitido** vive até `exp`, independentemente de a
política ter mudado ou de o usuário ter sido bloqueado. Revogar o grant é o que
encerra a cadeia. Ver §5.

---

## 3. Rollout por organização Claude

Cada organização Claude é uma **fronteira administrativa independente**. Não
existe herança entre elas, e o MCP **não recebe a identidade da organização
Claude** — a autorização usa exclusivamente a identidade Auth0/Google.

> "Organização Claude" e "Auth0 Organization" são conceitos diferentes. Este
> gate **não** introduz Auth0 Organizations: os dois domínios corporativos
> resolvem o problema sem essa complexidade.

### Recomendação: um OAuth Client Auth0 por organização Claude

Todos os clients compartilham a mesma API Audience, o mesmo callback oficial do
Claude e o mesmo backend. O que muda é só o Client ID na allowlist da Action.

Ganho concreto: **revogar ou rotacionar uma organização não derruba as demais** —
basta remover aquele Client ID do secret `ORACLE_CLIENT_IDS`. Há teste cobrindo
exatamente isso.

### Passos por organização

1. No Auth0: **Applications → Create Application → Regular Web Application**.
   Callbacks: `https://claude.ai/api/mcp/auth_callback` e
   `https://claude.com/api/mcp/auth_callback`.
2. Habilite **apenas** a conexão Google esperada.
3. Acrescente o novo Client ID ao secret `ORACLE_CLIENT_IDS` (vírgula) e
   **Deploy** a Action de novo — secret alterado só vale após redeploy.
4. No Claude, um **Owner** da organização adiciona o custom connector apontando
   para `https://mktplace-gobeaute.vercel.app/api/mcp`, informando Client ID e
   Client Secret **nas configurações avançadas do conector**.
5. Cada membro clica em **Vincular** e autentica com a própria conta Google.
6. Distribua a skill `/oraculo` naquela organização — ela **não** se propaga
   sozinha entre organizações.

O backend, as cinco tools e o banco **não são duplicados** em nenhum momento.

---

## 4. Onboarding

Para alguém de `gocase.com` ou `gobeaute.com.br`:

1. A pessoa abre o conector do Oráculo no Claude e clica em **Vincular**.
2. Autentica no Google com o e-mail corporativo.
3. A Action valida e concede `oracle:read` **no primeiro token**.
4. Pronto — **nenhuma role, convite ou cadastro manual**.

Se der "acesso restrito a contas corporativas autorizadas", verifique nesta
ordem: (a) usou a conta corporativa, e não pessoal? (b) o e-mail está verificado
no Google Workspace? (c) o Client ID daquela organização está na allowlist? (d) a
Action está no Login Flow e foi feito Deploy após a última mudança de secret?

---

## 5. Offboarding

**A ordem importa.** Suspender no Google Workspace impede *novos* logins, mas
**não invalida** um access token já emitido nem um refresh token vivo.

1. **Google Workspace**: suspenda ou remova a conta. Bloqueia autenticação nova.
2. **Auth0 → User Management → Users**: localize o usuário e **Block**.
3. **Auth0 → o usuário → Authorized Applications**: **revogue os grants** do(s)
   client(s) do Oráculo. É isto que mata o refresh token.
4. Confirme que uma nova tentativa de conexão é negada.

**Autorização explícita necessária:** revogar grants derruba as sessões ativas
daquele usuário. Para uma revogação **em massa** (por exemplo, ao mudar a
política), a interrupção atinge todo mundo e exige decisão sua — este agente não
executa revogação.

**Janela de exposição:** entre a suspensão e a revogação do grant, um access
token já emitido continua válido até `exp`. Trate essa janela como o SLA real de
offboarding.

---

## 6. Rotação

| O que | Quando | Como |
|---|---|---|
| Client Secret de uma organização | suspeita de vazamento, ou rotina | Auth0 → Application → Rotate; atualize nas configurações avançadas do conector daquela organização. As demais não são afetadas. |
| Client ID | descomissionar uma organização | remova do `ORACLE_CLIENT_IDS` e faça **Deploy**; opcionalmente apague a Application. |
| Chave de assinatura (JWKS) | rotação do tenant | automática — o resource server resolve por `kid` via JWKS remoto, sem chave fixada em código. |
| Domínios aprovados | mudança societária/marca | edite `ALLOWED_DOMAINS` na Action, ajuste os testes, versione, e só então Deploy. |

---

## 7. O que o MCP continua exigindo — inalterado por este gate

`issuer` do Auth0 exato · `audience` exatamente igual ao resource canônico ·
RS256 apenas · `exp`/`sub`/`iss`/`aud` obrigatórios · `oracle:read` como
**elemento completo** (`oracle:reader` e `oracle:read:all` continuam negados) ·
Production da Vercel só elegível com configuração completa · Preview e custom
environments sempre 404 · **cinco tools read-only**, nenhuma sexta ·
bearer nunca repassado ao FastAPI.

A Action **soma** uma forma de conceder `oracle:read`. Ela não afrouxa nenhuma
dessas verificações, e não tem como fazê-lo: `api.accessToken.addScope` só
acrescenta scope, e o único scope no arquivo é `oracle:read`.
