# Decisões de arquitetura

## Pendente — Reavaliar o Neon como camada de serving na Fase 2

**Registrada em:** 21/07/2026  
**Status:** decisão futura importante; não executar durante a estabilização atual.

O Neon permanece como banco consumido pela API no curto prazo. A API, as dimensões e os contratos canônicos ainda dependem de `marts.*` no Neon.

Por outro lado, a cópia Data Mart → Neon introduz atraso, lacunas quando o sync para, divergências após revisões históricas da fonte e custo operacional de reconciliação. Quando ML, TikTok e Shopee estiverem automatizados e padronizados no Data Mart, será necessário decidir formalmente entre:

1. manter o Neon e tornar a sincronização automática e observável; ou
2. criar uma camada `serving` estável no Data Mart e fazer a API consultá-la diretamente, eliminando a cópia analítica no Neon.

### Critérios obrigatórios para a decisão

- contrato canônico dos três canais no Data Mart;
- conectividade segura e read-only entre Render e RDS;
- desempenho, índices e concorrência com as cargas do warehouse;
- comportamento da read replica sob `conflict with recovery`;
- disponibilidade, custo, rollback e plano de migração da API;
- reconciliação em paralelo entre a resposta atual do Neon e a nova camada de serving.

Até essa avaliação, o Data Mart continua como fonte analítica e o Neon como camada de serving da Torre. Esta decisão é prioritária para a Fase 2, mas não bloqueia a correção atual de consistência e completude dos dados.

## Login Google na Torre (AUTH-GOOGLE)

**Registrada em:** 04/11/2026
**Status:** decidido; implementação na branch `feat/auth-google`. Virada em produção conforme `docs/AUTH_GOOGLE_RUNBOOK.md`.

Decisões travadas (plano `docs/superpowers/plans/2026-11-03-login-google.md`):

| Tema | Decisão | Por quê |
|---|---|---|
| Credencial Google | Mesmo OAuth Client do GoHunter (`AUTH_GOOGLE_ID`, `398963590019-….apps.googleusercontent.com`). `AUTH_GOOGLE_SECRET` não é usado (fluxo de ID token). | Pedido do usuário; o client ID é público. |
| Segredo da sessão | `AUTH_SECRET` novo para a Torre (≥ 32 caracteres), não reaproveitar o do GoHunter. | Com o mesmo segredo, um cookie `gh_session` do GoHunter seria um JWT válido aqui. |
| Onde mora o gate | No FastAPI (Render), como middleware. O `LoginGate` do front só esconde a interface. | A defesa é o gate do servidor; antes, `PUT /api/v1/metas/{mes}` era aberto. |
| Como o cookie chega ao Render | Mesma origem: `NEXT_PUBLIC_API_URL=/torre-api` + rewrite `/torre-api/:path*` → `TORRE_API_ORIGIN`. | `vercel.app` e `onrender.com` são sites diferentes; cookie de terceiro é bloqueado no Safari e no Firefox. |
| Liga/desliga | Front: `AUTH_GOOGLE_ID` vazio desliga o login (`dev@local`). API: `TORRE_AUTH_MODE=off\|report\|enforce`, padrão `off`; valor desconhecido conta como `enforce`. | Subir sem quebrar a produção e observar (`report`) antes de bloquear. |
| Client ID no front | Vem pela resposta 401 de `/api/auth/me` (`clientId`). Não existe `NEXT_PUBLIC_AUTH_GOOGLE_ID`. | Uma variável a menos. |
| Middleware do Next | Não criar. As únicas `/api/*` do Next são `/api/mcp` (OAuth próprio) e `/api/auth/*` (públicas). | Os dados passam pelo Render, que tem o gate. |
| Auditoria / admin | Fora de escopo. O login só gera o log `auth_login email=<email>` na Vercel. | YAGNI. |
| Domínio de produção | O endereço canônico da Torre passa a ser `https://mktplace-ten.vercel.app` (antes, `mktplace-gobeaute.vercel.app`). Origens JS autorizadas no Google: esse domínio e `http://localhost:3000`. | Mudança de domínio decidida durante a implementação. |

Restrições: domínios permitidos por match exato após o último `@` (padrão `gocase.com,gobeaute.com.br`); sessão JWT HS256 de 7 dias no cookie `torre_session` (HttpOnly, SameSite=Lax); chave de serviço no header `x-torre-service-key` (`TORRE_SERVICE_KEY` no Render = `MCP_BACKEND_SERVICE_KEY` na Vercel). Rotas públicas sem sessão: `/health`, `/ready`, `/api/v1/alertas/*`, `/api/v1/b2b-revenda/*`.
