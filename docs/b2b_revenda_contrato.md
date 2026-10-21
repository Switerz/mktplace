# B2B / Revendedores — contrato de leitura da Torre B2B

**Gate:** TORRE-B2B-OCTAPRICE · **Estado:** código atrás de flags
**desligadas** em produção; nenhuma variável configurada no Render ou na Vercel.

## O que é

Nova visão do Monitoramento de Preços: `Lojas próprias | B2B / Revendedores`.
Mostra se revendedores B2B anunciam abaixo do **PMA informado pelo OctaPrice**,
em que canal, marca e SKU, com que gravidade e de quando é o dado.

A Torre B2B é **fonte externa e somente leitura**. Ela já recebe o relatório
diário do OctaPrice e expõe as RPCs `fn_monit_*`; nós chamamos as **mesmas**
RPCs que alimentam a tela de lá, para os números baterem por construção.
Nada é escrito do lado de lá, e nada no projeto da Torre B2B é alterado por
este repositório.

```
navegador ──► nossa FastAPI (/api/v1/b2b-revenda) ──► Data API do Supabase da Torre B2B
                                   (chave só no backend)        (6 RPCs, allowlist fixa)
```

O navegador **nunca** fala com a Torre B2B e nunca recebe a chave.

## Regras de produto

| Regra | Onde está garantida |
|---|---|
| Piso = PMA do OctaPrice; não se mistura com o PDV de Lojas próprias | rota e schema separados; `Escopo.aviso_piso` |
| Amazon fora dos KPIs (a dedup da fonte descarta vendedores do mesmo anúncio) | allowlist `B2B_REVENDA_CANAIS_KPI`; sem filtro, a API envia a allowlist, **nunca NULL**; allowlist vazia ⇒ `unavailable` |
| Cobertura de identificação explícita | `cobertura_identificacao` (só contagens) |
| Frescor visível | `Frescor` em toda resposta (dia do relatório, situação, horário da consulta) |
| Fonte indisponível nunca vira zero | `status: "unavailable"` com `motivo_tecnico`; dia sem relatório não chama RPC |
| Cache de 15–30 min | `B2B_REVENDA_CACHE_TTL_SECONDS` (900–1800, padrão 1200); falha não é cacheada |
| Sem dado de cliente enquanto a Torre de Marketplaces não tiver autenticação | nenhuma rota devolve cliente, vendedor, seller, CNPJ, e-mail ou telefone; nenhuma aceita filtro por cliente/vendedor (testes de contrato) |

## Endpoints (todos agregados, só GET)

| Rota | Conteúdo |
|---|---|
| `GET /api/v1/b2b-revenda/frescor` | dia mais recente, dias disponíveis, situação, canais fora dos KPIs |
| `GET /api/v1/b2b-revenda/resumo` | anúncios, violação, sellers violando, desvio médio, buy box, severidade, cobertura de identificação |
| `GET /api/v1/b2b-revenda/canais-marcas` | marca × canal |
| `GET /api/v1/b2b-revenda/skus?limite=` | ranking de SKUs com violação (1–50) |

Filtros (parâmetros repetidos): `data`, `marcas` (`AP`/`BB`/`KS`), `canais`
(só os da allowlist), `severidades` (`grave`/`media`/`leve`), `skus`, `ufs`.
Recorte inválido ⇒ 422 com mensagem constante (o valor recebido não volta).
Todas exigem autenticação (seção "Autenticação das rotas"). Autenticado e
com a flag desligada, respondem 200 com `status: "unavailable"` /
`feature_flag_desligada` sem tocar a rede; a rota web responde 404.

Frescor: depois de 13h BRT o relatório do dia já é esperado (o import do
e-mail roda 08h40/10h40/12h40 BRT); antes disso, o de ontem ainda conta como
em dia. Os horários de coleta e de importação **não** saem das RPCs: a tela
diz isso em vez de mostrar um horário inventado.

## O que é lido na fonte — allowlist fixa

| RPC | Colunas pedidas (`select=`) | Parâmetros enviados (por nome) |
|---|---|---|
| `fn_monit_datas` | `data_ref` | — |
| `fn_monit_resumo` | `data_ref, total, violando, pct, sellers_violando, produtos, desvio_medio` | `p_data, p_sevs, p_marcas, p_canais, p_skus, p_ufs` |
| `fn_monit_severidade` | `leve, media, grave, com_buybox, viol_com_buybox` | `p_data, p_marcas, p_canais, p_skus, p_ufs` |
| `fn_monit_marca_canal` | `marca_id, canal, anuncios, violando, desvio_medio, preco_medio` | idem `resumo` |
| `fn_monit_top_produtos` | `sku, marca_id, exemplo, anuncios, violando, pct, desvio_medio` | idem + `p_limit` |
| `fn_monit_sellers` | **só** `identificado, violando` — contados e descartados no backend | idem + `p_limit` |

- Nenhum nome de RPC vem da requisição; não existe endpoint genérico.
- `p_vendedores` e `p_cnpjs` **nunca** são enviados.
- Parâmetros vão por nome no corpo JSON: se a fonte renomear um parâmetro ou
  coluna, a leitura falha alto (`contrato_da_fonte_quebrado`), não desloca valores.
- `fn_monit_sellers` e `fn_monit_marca_canal` são paginadas (`Range`) para não
  depender do `max-rows` da Data API.
- Nenhuma tabela, view, storage, auth ou outra RPC é consultada.

## Credencial

Chave da Data API do Supabase da Torre B2B, **só no backend**:

- vai no header `apikey` (o gateway sintetiza o `Authorization`);
- é `SecretStr` nas settings: não aparece em `repr`, log, resposta nem traceback;
- erro da fonte é classificado só pelo status HTTP e pelo campo `code` —
  mensagem, detalhe e hint do corpo não são lidos;
- o frontend tem teste que falha se qualquer variável da integração for citada.

A escolha da chave segue a regra **menos privilegiada que execute as seis
RPCs**. A chave usada na sondagem **não** vai para produção. Duas alternativas:

| | Curto prazo | Definitiva |
|---|---|---|
| O quê | chave **secret exclusiva** da Torre de Marketplaces, emitida pelo dono da Torre B2B só para este consumidor | acesso **somente leitura limitado às seis RPCs** |
| Como | nova secret key no projeto Supabase da Torre B2B, nomeada para este uso; vai direto para `B2B_REVENDA_SUPABASE_KEY` no Render | role Postgres dedicada, sem login próprio de aplicação, com `EXECUTE` só nas seis funções (de preferência invólucros `SECURITY DEFINER` que devolvam apenas agregados), emitida para a Data API como papel próprio ou exposta por um endpoint mínimo do lado da Torre B2B |
| Privilégio | **ignora RLS**: o acesso efetivo é o da `service_role`. O que limita o uso é o nosso lado (allowlist das seis RPCs, só backend, rota autenticada) | só o que as seis funções devolvem; nada de tabela, auth ou storage |
| Revogação | revogável sozinha, sem afetar outros consumidores da Torre B2B | revogável sozinha; mudar a role não afeta ninguém |
| Mudança aqui | nenhuma | pequena no adapter (como a credencial é apresentada); a allowlist e o contrato não mudam |

Em qualquer das duas, a chave nunca passa por chat, e-mail, documento ou commit.

## Autenticação das rotas (Gate OCTAPRICE-2)

Todas as rotas `/api/v1/b2b-revenda/*` exigem **access token do Auth0
corporativo** — o mesmo tenant do Oráculo, sem sistema de usuários novo
([`app/auth.py`](../apps/api/app/auth.py)):

- issuer: só `https://gobeaute-oraculo.us.auth0.com/` (allowlist no código, a mesma de `oauth.ts`);
- assinatura: só RS256, chave do JWKS derivado do issuer (cache de 10 min);
- audience: exatamente `API_AUTH_AUDIENCE`;
- permission `b2b_revenda:read`, como elemento completo de `permissions` ∪ `scope`;
- `exp`, `iat`, `iss`, `aud` e `sub` obrigatórios.

| Situação | Resposta |
|---|---|
| autenticação não configurada (ou inválida) | 503, sem dado |
| sem token, token malformado, forjado, expirado, de outro issuer ou audience | 401 + `WWW-Authenticate: Bearer` |
| token válido sem `b2b_revenda:read` | 403 (`insufficient_scope`) |
| token válido + flag desligada | 200 `unavailable` / `feature_flag_desligada` |
| token válido + flag ligada | contrato normal (dado ou `unavailable` com motivo) |

A autenticação vem **antes** da flag, da fonte e do cache: quem não se
autentica não recebe nem o `unavailable`. O token nunca é logado nem ecoado.

**Configuração no Auth0 (dono do tenant, não feita aqui):**
1. APIs → criar API com *Identifier* = valor de `API_AUTH_AUDIENCE`
   (sugestão: `https://mktplace-api.onrender.com/api/v1/b2b-revenda`), RS256.
2. Na API: habilitar RBAC e *Add Permissions in the Access Token*; criar a
   permission `b2b_revenda:read`.
3. Atribuir a permission por role às pessoas autorizadas (comercial,
   operação, admin). Não usar a Action do Oráculo para isto: ela concede
   `oracle:read` por domínio, e este dado tem público menor.

**Bloqueio conhecido para a tela:** o navegador da Torre ainda não tem login
humano. Com `NEXT_PUBLIC_B2B_REVENDA_MODE=api`, a aba recebe 401 e mostra
"Acesso restrito" — nunca zero. Ligar a aba no navegador exige o fluxo de
login do Auth0 na web (gate próprio).

## Variáveis de ambiente

API (Render) — **documentadas, nenhuma configurada**:

| Variável | Padrão | Papel |
|---|---|---|
| `API_AUTH_ISSUER` | vazio | `https://gobeaute-oraculo.us.auth0.com/`; vazio ou outro host ⇒ 503 |
| `API_AUTH_AUDIENCE` | vazio | Identifier da API no Auth0 (HTTPS); vazio ⇒ 503 |
| `B2B_REVENDA_REQUIRED_SCOPE` | `b2b_revenda:read` | permission exigida (um único valor) |
| `B2B_REVENDA_ENABLED` | `false` | liga os endpoints; desligado ⇒ `unavailable` sem rede |
| `B2B_REVENDA_SUPABASE_URL` | vazio | raiz do projeto (`https://<ref>.supabase.co`); só HTTPS, sem caminho |
| `B2B_REVENDA_SUPABASE_KEY` | vazio | chave da Data API (segredo) |
| `B2B_REVENDA_TIMEOUT_SECONDS` | `10` | 1–30 (conexão limitada a 5 s) |
| `B2B_REVENDA_CACHE_TTL_SECONDS` | `1200` | 900–1800 |
| `B2B_REVENDA_CANAIS_KPI` | `Mercado Livre,Shopee` | allowlist dos KPIs, com a grafia da fonte |

Web (Vercel, em build):

| Variável | Valores |
|---|---|
| `NEXT_PUBLIC_B2B_REVENDA_MODE` | ausente = desligada (404 e sem seletor) · `fixture` = dados fictícios com faixa de aviso, sem requisição · `api` = lê a nossa API |

## Ativação (gate próprio, fora deste PR)

1. Auth0: API, permission e roles criadas (seção acima).
2. Render: `API_AUTH_ISSUER`, `API_AUTH_AUDIENCE` e a credencial **exclusiva**
   (nunca a da sondagem) colocadas direto nas variáveis; redeploy manual.
3. Conferir 401 sem token, 403 sem permission e 200 `unavailable` com token
   válido e flag desligada.
4. `B2B_REVENDA_ENABLED=true` e reconciliação de `/frescor`, `/resumo`,
   `/canais-marcas` e `/skus` contra a fonte no mesmo dia e recorte.
5. Login humano na web; só então `NEXT_PUBLIC_B2B_REVENDA_MODE=api`.
6. Rotas com dado de cliente (seller, cliente, vendedor, reincidência) ficam
   para depois, sob a mesma autenticação e com permission própria.

## Limitações conhecidas

- Violação, desvio e PMA vêm prontos do OctaPrice; a Torre não aplica tolerância.
- Seller é identificado pelo nome normalizado; nomes iguais em canais diferentes contam como um.
- Cliente B2B depende da identificação manual feita na Torre B2B.
- Histórico limitado à retenção da fonte (~90 dias).
- Horários de coleta e importação não expostos pelas RPCs.
- `% violação` por SKU é recalculado aqui com 1 casa (a RPC arredonda a 0); as contagens são as da fonte.
