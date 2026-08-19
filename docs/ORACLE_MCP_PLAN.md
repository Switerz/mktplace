# Oráculo × Torre Marketplace — Plano e Implementação do Conector MCP

> **Status:** OM0 e **OM1 CONCLUÍDOS** — Task 1/2 (implementação) e Task 2/2
> (integração com o V3-1A + QA local real).
> **Nenhum deploy, nenhum segredo, nenhuma publicação.**
>
> **Todo deployment DESABILITADO por construção** — a rota responde 404 em
> Production, Preview e custom environments enquanto não houver autenticação
> real (§13.4.1, §25.4, **§26.1**).
> **OAuth: ainda pendente.** **OM2: NÃO iniciado.**
> **`/api/mcp` NÃO está habilitado na Vercel; o Claude.ai NÃO foi conectado.**
>
> **Gate:** OM0 → OM1 · **Base integrada:** `origin/main` @ `e675948` (V3-1A)
> **Branch/worktree:** `oracle-mcp`
> **Auditoria:** 2026-08-18 · **Checkpoint OM0-F:** 2026-08-18
> **Implementação local (OM1 Task 1/2):** 2026-08-18 — ver **§25**
> **Correção consolidada pré-QA:** 2026-08-19 — ver **§26**
> **Correção terminal pré-QA:** 2026-08-19 — ver **§27**
> **Task 2/2 — integração e QA local:** 2026-08-19 — ver **§28**

---

## 1. Resumo executivo

O objetivo é expor os dados **governados** da Torre de Controle de Marketplaces
ao Oráculo (skill de roteamento do Claude) através de um servidor MCP hospedado
na própria Vercel, em `/api/mcp`, que fala **apenas** com a API FastAPI pública
no Render — nunca com PostgreSQL, nunca com o Data Mart via TCP, nunca como
proxy REST genérico.

A auditoria confirma que **a frente é viável em termos de dados**. A
autenticação **não bloqueia escrever o servidor localmente** — bloqueia
**expô-lo**.

| Dimensão | Veredito |
|---|---|
| Fontes de dados | **Viável.** 13 famílias de endpoints em produção, servidas pelo Neon (`marts.*`), respondendo 200 com `refreshed_at` real. |
| Stack MCP | **Implementado.** `@modelcontextprotocol/server@2.0.0` + `zod@4.4.3`, Node ≥ 20, no Next.js 15.5.19. **`mcp-handler` não foi usado** — o SDK v2 já traz `createMcpHandler` e `WebStandardStreamableHTTPServerTransport` (§25.2). |
| Catálogo de tools | **Definido.** 5 tools no MVP, nenhuma de escrita, nenhuma genérica. |
| Autenticação | **OAuth adiado** (decisão do proprietário). Não existe infraestrutura de identidade — nem código, nem issuer, nem JWKS, nem tabela (§13.1.1). Bloqueia **publicação**, não implementação local. |
| Risco herdado | **Aberto.** A API do Render é **pública hoje**. Proteger `/api/mcp` não protege o backend. |
| Isolamento na Vercel | **Não existe** exceção de Deployment Protection por path para GET/POST. O único bypass viável destrava o projeto inteiro e **não está autorizado** (§16.1). |

**Recomendação:** OM1 pode começar **local, dev/test e read-only**, desde que a
rota nasça **fail-closed** e permaneça **desabilitada em produção** até haver
auth real. O desenho A (OAuth) está adiado; o desenho B (bearer compartilhado)
**não está aprovado**. Nenhum bypass de plataforma conta como autenticação.

### 1.1 Achados que mudam o desenho

Cinco achados desta auditoria alteram o que seria construído ingenuamente:

1. **`/tempo-real` está quebrado em produção (HTTP 500).** Depende do Data Mart
   (RDS), inacessível a partir do Render. Fica fora do MVP — não é escolha de
   escopo, é indisponibilidade medida.
2. **O dia corrente é sistematicamente parcial.** Em 18/08/2026 o GMV do próprio
   dia era R$ 3.681 contra ~R$ 600 mil nos dias anteriores. Uma tool que inclua
   o dia corrente sem rótulo fará o Oráculo relatar uma queda catastrófica falsa.
3. **`/operacoes` devolve nomes de creators — PII.** Fica fora do MVP na forma
   crua; só entra com projeção server-side que elimine a dimensão creator.
4. **Existe um WAF à frente do Render que responde HTML 403** (e expõe o IP do
   chamador na página). O adapter precisa tratar corpo não-JSON como falha
   categorizada, jamais repassá-lo ao modelo.
5. **O frontend tem fallback mock** em Canais, Financeiro e Qualidade. A tela
   pode exibir números plausíveis com a API fora do ar. O MCP **nunca** deve
   replicar esse comportamento — silêncio é preferível a número inventado.

---

## 2. Arquitetura atual confirmada

Tudo nesta seção foi verificado nesta task (código lido e/ou requisição
read-only executada), não assumido.

### 2.1 Frontend

| Fato | Evidência |
|---|---|
| Next.js **15.1**, React 19 | `apps/web/package.json` → `"next": "^15.1.0"` |
| **Não** existe `app/api/mcp` | `apps/web/app/api` não existe; zero `route.ts` no projeto |
| **Nenhum** runtime declarado | zero ocorrências de `export const runtime` / `maxDuration` em `apps/web` |
| **Nenhuma** autenticação | zero ocorrências de `next-auth`/`clerk`/`auth0`/`getServerSession`; **não existe** `middleware.ts` |
| Deploy na Vercel | `apps/web/vercel.json` → `"framework": "nextjs"`, `installCommand: npm ci` |
| `next.config.ts` **vazio** | `const nextConfig: NextConfig = {}` |
| Sem `zod`, sem SDK MCP | ausentes de `dependencies` e `devDependencies` |
| Domínio canônico responde | `GET https://mktplace-gobeaute.vercel.app/` → **200** |
| `/api/mcp` ainda não existe | `GET .../api/mcp` → **404** |

> **Observação relevante para a Vercel (§16):** a raiz respondeu **200 sem
> autenticação alguma**. Isso indica que a Deployment Protection **não** está
> ativa no domínio de produção. Consequência: hoje não há bypass a configurar,
> mas também não há proteção de plataforma — toda a segurança do MCP terá de
> vir da auth da própria rota.

### 2.2 Backend

| Fato | Evidência |
|---|---|
| FastAPI no Render | `GET https://mktplace-api.onrender.com/health` → **200** |
| **Sem autenticação** | `apps/api/app/main.py` não registra dependência de auth; nenhuma rota exige credencial |
| CORS permissivo | `allow_methods=["*"]`, `allow_headers=["*"]`, `allow_credentials=True` |
| Dois engines distintos | `apps/api/app/database.py`: `DATABASE_URL` → Neon; `DATAMART_DATABASE_URL` → RDS |
| Fonte servida é o **Neon** | `GET /api/v1/performance/health-datasource` → `{"active_source":"neon_marts","db_connected":true}` |
| Data Mart inacessível ao Render | `apps/api/app/config.py` documenta a causa raiz (Gate G4); confirmado em runtime por `/tempo-real` → **500** |
| Timezone de negócio | `apps/api/app/deps/period.py` → `APP_TIMEZONE = ZoneInfo("America/Sao_Paulo")` |
| Intervalo máximo | `MAX_RANGE_DAYS = 366`, validado com 422 em runtime |
| Há um WAF à frente | string de injeção → **403** com corpo **HTML** (não JSON) |

### 2.3 O mecanismo que separa READY de BLOCKED

`apps/api/app/services/gold_service.py` roteia a query pelo **texto do SQL**:

```python
def _uses_datamart(sql: str) -> bool:
    lowered = sql.lower()
    return any(token in lowered for token in (" gold.", "from gold.", "join gold.", " raw.", ...))
```

Se o SQL mencionar `gold.` ou `raw.`, a execução vai para o `datamart_engine`
(RDS) — **inalcançável a partir do Render**. Se mencionar apenas `marts.`, roda
no Neon e funciona.

Mapeamento por função de serviço (extraído do código):

| Função de serviço | Tabelas | Engine | Produção |
|---|---|---|---|
| `performance_service.*` (todas) | somente `marts.*` | Neon | ✅ 200 |
| `regioes_service.*` | `marts.dim_loja`, `marts.fact_marketplace_region_daily` | Neon | ✅ 200 |
| `executive_summary_service` | reusa os anteriores | Neon | ✅ 200 |
| `gold_service.get_inteligencia` | `marts.fact_ml_*`, `marts.fact_tiktok_product_daily` | Neon | ✅ 200 |
| `gold_service.get_operacoes` | `marts.fact_ml_gestao_diaria`, `marts.fact_tiktok_*` | Neon | ✅ 200 |
| `gold_service.get_brand_detail` | `marts.fact_tiktok_*` | Neon | ✅ 200 |
| `gold_service.get_tempo_real` | **`gold.tiktok_shop_hourly`, `raw.tiktok_shop_orders`** | **RDS** | ❌ **500** |
| `gold_service.diagnose_raw_tempo_real` | **`gold.`/`raw.`** | **RDS** | ❌ rota de debug |

> `gold.ml_produto_pnl` aparece em `performance_service.py`, mas **apenas dentro
> de uma string de aviso** (`_ML_COST_MISSING_WARNING`), nunca em SQL executado.
> Não contamina o roteamento.

---

## 3. Arquitetura proposta

```
Oráculo / Claude.ai
        │  Streamable HTTP (POST JSON-RPC) + Authorization: Bearer
        ▼
https://mktplace-gobeaute.vercel.app/api/mcp        ← Next.js Route Handler
        │                                             runtime = "nodejs" (obrigatório)
        │  ├─ withMcpAuth (obrigatório, required: true)
        │  ├─ validação Zod do input (allowlists estritas)
        │  └─ 5 tools registradas via registerTool
        ▼
  adapter server-side (torre-client)               ← caminhos fixos no código
        │  fetch + AbortController + limite de corpo + validação da resposta
        ▼
https://mktplace-api.onrender.com/api/v1/...        ← FastAPI (público hoje)
        ▼
  Neon — marts.* (fatos governados)
```

**Invariantes do desenho:**

- o MCP **não** abre conexão PostgreSQL;
- o MCP **não** consulta o Data Mart (RDS);
- o MCP **não** expõe proxy de URL/path/SQL;
- nenhuma tool escreve, em nenhuma circunstância, no primeiro release;
- o caminho HTTP chamado é **constante literal no código**, nunca derivado de
  input do modelo;
- falha vira erro categorizado — **nunca** mock, nunca lista vazia silenciosa.

---

## 4. Fatos confirmados × hipóteses

### 4.1 Confirmado nesta task

- Next.js 15.1 instalado; sem `middleware.ts`; sem auth; sem `app/api`.
- `/api/mcp` não existe (404); domínio de produção responde 200 **sem** proteção.
- FastAPI público no Render, sem auth, com CORS permissivo, servindo Neon.
- `/tempo-real` retorna **500** em produção.
- `refreshed_at` é real e resolvido por período: agosto → `2026-08-18T18:56Z`;
  julho → `2026-08-05T18:53Z` (mês fechado, coerente).
- O dia corrente é parcial (18/08 = R$ 3.681 vs ~R$ 600 mil/dia).
- Validações de borda funcionam: intervalo > 366 dias → 422; marca inválida →
  422 com a allowlist na mensagem.
- Injeção SQL via querystring → **403 HTML** do WAF (não chega à aplicação).
- `/operacoes` retorna chaves `creator` e `creators` → **PII**.
- `/pedidos` é **agregado** (marca/dia) — nenhum identificador de comprador.
- `mcp-handler@2.1.1`; peer `@modelcontextprotocol/server@^2.0.0` (**não**
  opcional), `next` opcional; `engines.node >= 20`.
- `@modelcontextprotocol/server@2.0.0` existe e exige `zod@^4.2.0`.
- Em v2 o auth é lido em **`ctx.http?.authInfo`** (ver §5).
- A spec 2026-07-28 define `outputSchema` e `structuredContent`.

### 4.2 Hipótese / não confirmado

| Item | Situação |
|---|---|
| Root Directory da Vercel | **Não confirmado.** Existe `apps/web/vercel.json`, o que sugere `apps/web`, mas as configurações do projeto na Vercel não foram lidas (CLI proibida nesta task). |
| Versão do Node na Vercel | **Não confirmado.** Nada no repositório fixa a versão (sem `engines`, sem `.nvmrc`). |
| Fluid Compute | **Não confirmado.** Configuração de plataforma, não versionada. |
| Deployment Protection | **Indício forte de que está desligada** (200 sem auth), mas o painel não foi lido. |
| `mcp_oauth_*` | **Resolvido em 2026-08-18 (§13.1.1):** inexistente neste repositório **e** não localizado nos projetos Supabase acessíveis. Não existe projeto `torre_de_performance_b2b` acessível. Um projeto (INACTIVE) ficou inconclusivo — não bloqueante, pois nenhuma reutilização está autorizada. |
| Skill do Oráculo | **Não localizada.** Busca read-only em `~/.claude/skills`, `~/.claude/plugins` e `~/Desktop` não encontrou nenhum arquivo `*oracul*`. O §20 é, portanto, proposta a validar contra a skill real. |

---

## 5. Auditoria oficial do stack MCP

Fontes primárias consultadas em 2026-08-18.

### 5.1 Versões e dependências

Do registry oficial (`registry.npmjs.org/mcp-handler/latest`):

```json
{
  "version": "2.1.1",
  "engines": { "node": ">=20" },
  "dependencies": { "chalk": "^5.3.0", "commander": "^11.1.0" },
  "peerDependencies": {
    "next": ">=13.0.0",
    "@modelcontextprotocol/server": "^2.0.0"
  },
  "peerDependenciesMeta": {
    "next": { "optional": true },
    "@modelcontextprotocol/server": { "optional": false }
  }
}
```

E de `registry.npmjs.org/@modelcontextprotocol/server/latest`: versão `2.0.0`,
`engines.node >= 20`, dependências `zod@^4.2.0` e `@modelcontextprotocol/core@2.0.0`.

**Conclusões:**

- O peer correto chama-se **`@modelcontextprotocol/server`** (o pacote existe e
  é o SDK v2). Não é `@modelcontextprotocol/sdk`.
- É **`zod@^4`**, não v3 — diferença material, pois v4 muda a geração de JSON
  Schema e parte da API de refinamento.
- Node ≥ 20 é exigido pelos dois pacotes → a rota **não pode** rodar em Edge.
- Instalação futura (OM1, não executar agora):
  `mcp-handler@^2 @modelcontextprotocol/server@^2 zod@^4`.

### 5.2 Mudanças da v2 (CHANGELOG oficial)

| Item | Situação na 2.x |
|---|---|
| Transporte HTTP+SSE (2024-11-05) | **Removido.** Atenção à evolução dentro da própria 2.x: o CHANGELOG **2.0.0** diz que `/sse` e `/message` respondem **410 Gone**, mas a **2.0.1** removeu o roteamento de endpoints de transporte legado ("Mount the MCP handler directly at a framework route and remove legacy transport endpoint routing"). Na **2.1.1**, que é a versão que instalaremos, vale o README atual: "unmounted `/sse` and `/message` paths are handled by your framework" — ou seja, **404 do Next.js**, não 410. O teste do §18 deve aceitar "não é transporte ativo", não cravar um código. |
| Redis | **Removido.** "Redis is no longer needed or used." |
| Sessão | **Stateless.** Operações de sessão GET/DELETE respondem 405. |
| Registro de tool | `registerTool` com `inputSchema` em `z.object(...)`. |
| **`authInfo`** | **`extra.authInfo` → `ctx.http?.authInfo`** |
| Assinatura do handler | 2.1.0: `createMcpHandler(initialize, options)` (objeto único). |
| Shims 1.x | 2.1.0 removeu `basePath`, `streamableHttpEndpoint`, `redisUrl`. |
| Spec implementada | 2026-07-28, stateless, com `_meta` por request e `server/discover`. |

### 5.3 Resolução da divergência do handoff

O handoff registrou divergência entre exemplos antigos (`extra.authInfo`) e o
changelog v2 (`ctx.http?.authInfo`).

**Resolvido pela documentação atual: usa-se `ctx.http?.authInfo`.**

Evidência textual, citada literalmente de três fontes primárias independentes:

**1. README oficial de `vercel/mcp-handler@main`, seção "Migrating from 1.x":**

> "In handler callbacks, `extra.authInfo` is now `ctx.http?.authInfo`."

**2. CHANGELOG oficial, entrada 2.0.0 (Major Changes):**

> "**Breaking**: tool/prompt/resource registration follows SDK v2 (`registerTool`
> with `z.object(...)` Standard Schemas; variadic `server.tool(...)` is gone;
> `extra.authInfo` is now `ctx.http?.authInfo`)."

**3. `docs/AUTHORIZATION.md`, exemplo executável:**

```ts
async ({ message }, ctx) => {
  const authInfo = ctx.http?.authInfo;   // <- forma correta na v2
}
```

Qualquer exemplo que use `extra.authInfo` é pré-2.0 e **não deve ser copiado**.
Em OM1 isto deve ser reconferido contra os *tipos* do pacote instalado, que são a
autoridade final.

### 5.4 Auth: helpers oficiais

- **`withMcpAuth`** — verifica bearer tokens e devolve o desafio
  `WWW-Authenticate` no formato RFC 9728 em caso de falha.
- **`protectedResourceHandler`** — serve o documento de Protected Resource
  Metadata (RFC 9728), listando os authorization servers, em
  `/.well-known/oauth-protected-resource`.

Ambos existem na v2 sob a spec 2026-07-28.

### 5.5 `structuredContent` / `outputSchema`

Confirmado na spec oficial 2026-07-28 (§ Tools):

> "**Structured** content is returned as a JSON value in the `structuredContent`
> field of a result. This can be any JSON value ... that conforms to the tool's
> `outputSchema` if one is defined."

E, decisivo para a interoperabilidade:

> "For backwards compatibility, a tool that returns structured content SHOULD
> also return the serialized JSON in a TextContent block."

**Decisão:** as tools devolverão `structuredContent` **e** o mesmo JSON
serializado em um bloco `text`. É exatamente o que a spec recomenda.

Sobre nomes de tool, a spec pede 1–128 caracteres em `[A-Za-z0-9_.-]`. O padrão
exigido pelo handoff (`^[a-zA-Z0-9_-]{1,64}$`) é um subconjunto estrito — todos
os nomes propostos no §8 o respeitam.

---

## 6. Fontes de verdade

| Camada | Onde | O que é |
|---|---|---|
| `marts.*` (Neon) | `DATABASE_URL` | **Fonte servida.** Fatos governados que alimentam a Torre. Única camada que o MCP alcança. |
| `gold.*`, `raw.*` (RDS) | `DATAMART_DATABASE_URL` | Camada anterior. **Inacessível ao Render** → fora do escopo do MCP. |

Fatos relevantes ao MVP:

| Tabela | Grão | Serve |
|---|---|---|
| `marts.fact_marketplace_daily_performance` | dia × marca × marketplace | Gerencial, Canais, Financeiro, Qualidade, Pedidos, Trend |
| `marts.fact_marketplace_region_daily` | dia × marca × marketplace × UF | Regiões |
| `marts.fact_ml_produto_ranking` | produto ML (**cumulativo, sem competência mensal**) | Produtos ML, Inteligência |
| `marts.fact_tiktok_product_daily` | dia × produto TikTok | Produtos TikTok |
| `marts.fact_shopee_product_monthly` | mês × produto Shopee | Produtos Shopee |
| `marts.dim_loja` | marca | Allowlist de marcas (`ativo`) |

**Allowlists canônicas** (confirmadas em runtime pelo 422 do backend):

- marcas: `apice`, `barbours`, `kokeshi`, `lescent`, `rituaria`
- canais: `tiktok`, `ml`, `shopee` (+ `all`)
- buckets de Pareto: `A_top50`, `B_next30`, `C_next15`, `D_tail`
- granularidades de trend: `auto`, `day`, `week`, `month`

---

## 7. Matriz de superfícies

Classificação de cada superfície da Torre como fonte candidata a tool.

### 7.1 Gerencial — `READY`

- **Endpoints:** `GET /api/v1/performance/overview`, `/brands`, `/trend`,
  `/monthly`, `/executive-summary`
- **Serviço:** `performance_service.get_overview/get_brands/get_trend/get_monthly`,
  `executive_summary_service.get_executive_summary`
- **Fato:** `marts.fact_marketplace_daily_performance` × `marts.dim_loja`
- **Grão:** dia × marca × marketplace, agregado ao período
- **Chaves:** `brand_key`, `marketplace_id`, `date`
- **Métricas:** `gmv`, `orders`, `avg_ticket`, `ad_spend`, `ml_roas`,
  `ml_cancel_rate_pct`, `gmv_mom_pct`, compradores únicos por canal
- **Período:** default = **mês calendário anterior completo**; `date_from`/`date_to`
  inclusivos; máx. 366 dias
- **Timezone:** `America/Sao_Paulo` (`today_brt()`)
- **Moeda:** BRL, em **reais** (float), não centavos
- **Comparação:** `compare=true` → mês anterior completo se o período for mês
  fechado; senão janela anterior de mesma duração
- **`refreshed_at`:** presente, `MAX()` sobre as linhas do período
- **null vs zero:** `Optional[float]` preservado; `_ratio()` devolve `None` (não 0)
  quando o denominador é ≤ 0
- **Restrições:** o **dia corrente é parcial**; GMV não inclui frete, cancelados
  nem devoluções (definição ratificada)
- **Produção:** 200 ✅ · **Mock no frontend:** sim (Overview/Brands)

### 7.2 Canais — `READY_WITH_RESTRICTION`

- **Endpoint:** `GET /api/v1/performance/canais` · **Serviço:** `get_canais`
- **Grão:** marca × canal
- **Métricas:** GMV por canal, split TikTok video/live/card, visitantes,
  conversão, compradores novos/recorrentes, `roas`, `acos_pct`,
  `marketplace_cost_pct`, `seller_shipping_pct`, medianas e p75 por canal
- **Restrições (do próprio código):**
  - **comissão do ML indisponível** no mart (`total_fees` nulo para ML) —
    `_ML_COST_MISSING_WARNING`;
  - **custo TikTok é direcional**: a base de repasse difere do GMV comercial em
    ~5,5% — `_TIKTOK_COST_WARNING`;
  - `conversion_rate` tem **escala inconsistente** entre canais (TikTok em ratio,
    Shopee em percentual, ML nulo);
  - a resposta distingue `applicable` (o canal opera esse custo) de `available`
    (o mart tem o dado) — **N/A ≠ Sem dado**, e o MCP precisa preservar isso.
- **Produção:** 200 ✅ (~14 KB) · **Mock no frontend:** **sim** (`CANAIS_MOCK_BRANDS`)

### 7.3 Produtos — `READY_WITH_RESTRICTION`

- **Endpoints:** `/produtos/ml`, `/produtos/tiktok`, `/produtos/shopee` (+`/summary`)
- **Grão:** produto (ML cumulativo; TikTok diário agregado ao mês; Shopee mensal)
- **Paginação real:** devolve `total`, `limit`, `offset` → `total_count` é
  **verdadeiro** (ex.: ML `total: 1648`, TikTok `523`, Shopee `472`)
- **Restrições:**
  - `fact_ml_produto_ranking` **não tem competência mensal** — é cumulativo; o
    campo `scope` explicita isso e **não** deve ser filtrado por mês;
  - `estimated_margin` (ML) **não deve ser exposto**: não há CMV, logo não é
    margem real;
  - ROAS/ACOS por produto só existem para ML.
- **PII:** não — `item_id`, `seller_sku`, `title` são dados de catálogo
- **Produção:** 200 ✅ · **Mock:** não

### 7.4 Financeiro — `READY_WITH_RESTRICTION`

- **Endpoint:** `/financeiro` · **Métricas:** GMV, settlement, fees, `avg_fee_pct`,
  `avg_settlement_pct`, ad spend/revenue, ROAS, ACOS, CPC, custo de frete
- **Restrições:** comissão do ML ausente; settlement Shopee com caso conhecido
  de razão > 100%; base de repasse TikTok ≠ GMV comercial (~5,5%)
- **Produção:** 200 ✅ · **Mock:** **sim** (`FINANCEIRO_MOCK_BRANDS`)

### 7.5 Qualidade — `READY_WITH_RESTRICTION`

- **Endpoint:** `/quality` · **Métricas:** taxas de cancelamento, devolução,
  não-entrega, prazo médio de entrega, por canal e marca
- **Restrição grave:** **TikTok não registra cancelamento** — `canceled_orders`
  é **0 em todos os meses** (dez/25–jun/26) conforme `docs/gold_vs_marts_matrix.md`.
  Zero aqui significa *ausência de medição*, não ausência de cancelamento. Uma
  tool que devolva "TikTok: 0% de cancelamento" está enganando o leitor.
- **Produção:** 200 ✅ · **Mock:** **sim** (`QUALITY_MOCK_BRANDS`)

### 7.6 Regiões — `READY_WITH_RESTRICTION`

- **Endpoints:** `/api/v1/regioes/summary`, `/by-uf`, `/by-brand`, `/trend`
- **Fato:** `marts.fact_marketplace_region_daily` · **Grão:** dia × marca × canal × UF
- **Allowlist:** 27 UFs + `XX` (desconhecida)
- **Restrição:** a cobertura regional é **materialmente menor** que a de Canais
  (medição de ~43,8% a menos, registrada no checkpoint de qualidade). Totais de
  Regiões **não reconciliam** com os de Gerencial e isso precisa ser dito na
  resposta, não escondido.
- **Produção:** 200 ✅ · **Mock:** não

### 7.7 Pedidos — `READY`

- **Endpoint:** `/pedidos` · default **30 dias** (não mês anterior)
- **Grão:** agregado por marca e por dia — `kpis`, `by_brand`, `daily`
- **PII:** **não** — chaves verificadas em runtime; não há `order_id`, comprador,
  endereço ou documento
- **Produção:** 200 ✅ · **Mock:** não

### 7.8 Inteligência — `READY_WITH_RESTRICTION`

- **Endpoint:** `/inteligencia` (migrado para Neon — commit `868177d`)
- **Conteúdo:** sinais de portfólio, Pareto, LTV, recompra, `at_risk_or_churned`
- **Restrições:** payload **grande (~29 KB)** — precisa de projeção/limite
  server-side antes de virar tool; base ML é **cumulativa**, sem competência
  mensal, logo **não aceita filtro de período**
- **Produção:** 200 ✅ · **Mock:** não

### 7.9 Operações — `BLOCKED` (para o MVP)

- **Endpoint:** `/operacoes` — responde 200 (~11 KB)
- **Motivo do bloqueio:** devolve as chaves **`creator`** e **`creators`** —
  identificação de pessoas. Enquadra-se na proibição explícita do handoff
  ("não use fonte que exponha PII").
- **Caminho de desbloqueio:** projeção server-side que agregue lives/vídeos por
  marca e **descarte a dimensão creator**. Fica para depois do MVP.
- Observação: no momento da auditoria `alertas` estava **vazio** (`[]`), o que
  reduz ainda mais o valor da superfície hoje.

### 7.10 Marca — `READY_WITH_RESTRICTION`

- **Endpoint:** `/brand-detail` (migrado para Neon)
- **Restrição estrutural:** a fonte é **TikTok-only**; o backend rejeita com 422
  qualquer `channels` que exclua TikTok. Não serve para "desempenho da marca X"
  em geral — apenas para "marca X no TikTok".
- **Produção:** 200 ✅ · **Mock:** não

### 7.11 Tempo real — `BLOCKED`

- **Endpoint:** `/tempo-real` → **HTTP 500 em produção**
- **Causa:** depende de `gold.tiktok_shop_hourly` + `raw.tiktok_shop_orders`
  (RDS), inalcançável do Render (Gate G4)
- **Não é candidata a tool.** Também exclui `/debug/raw-tempo-real`.

### 7.12 Consolidado

| Superfície | Classificação | Produção | Mock no front | PII |
|---|---|---|---|---|
| Gerencial | `READY` | 200 | sim | não |
| Pedidos | `READY` | 200 | não | não |
| Canais | `READY_WITH_RESTRICTION` | 200 | **sim** | não |
| Produtos | `READY_WITH_RESTRICTION` | 200 | não | não |
| Financeiro | `READY_WITH_RESTRICTION` | 200 | **sim** | não |
| Qualidade | `READY_WITH_RESTRICTION` | 200 | **sim** | não |
| Regiões | `READY_WITH_RESTRICTION` | 200 | não | não |
| Inteligência | `READY_WITH_RESTRICTION` | 200 | não | não |
| Marca | `READY_WITH_RESTRICTION` | 200 | não | não |
| Operações | `BLOCKED` (PII) | 200 | não | **sim** |
| Tempo real | `BLOCKED` (500) | **500** | não | n/d |
| `/debug/raw-tempo-real` | `OUT_OF_SCOPE` | — | — | — |
| `/health-datasource` | `OUT_OF_SCOPE` (uso interno do adapter) | 200 | — | — |

---

## 8. Catálogo das tools (MVP)

Cinco tools. Nenhuma espelha um endpoint 1:1; cada uma responde uma pergunta
completa de negócio. Nenhuma escreve.

### 8.1 `torre_desempenho_marketplaces`

1. **name:** `torre_desempenho_marketplaces`
2. **title:** Desempenho consolidado dos marketplaces
3. **description (para roteamento):**
   > Use para responder "quanto vendemos", "como foi o mês", "GMV/pedidos/ticket
   > médio", "crescimento vs período anterior" por canal (TikTok Shop, Mercado
   > Livre, Shopee) e por marca. Grão: agregado do período, com opcional quebra
   > diária/semanal/mensal. Métrica principal: **GMV = soma do valor bruto dos
   > pedidos, sem frete, sem cancelados e sem devoluções**. Retorna o universo
   > completo dos canais e marcas no escopo (não é top-N). Não use para produto
   > individual, região/UF, qualidade de entrega ou dados intradiários — nem
   > para o dia corrente, que é sempre parcial.
4. **Responde:** GMV/pedidos/ticket do período; comparação com período anterior;
   composição por canal e por marca; série temporal.
5. **Não responde:** produto específico; UF; taxa de cancelamento; tempo real;
   margem/lucro (não há CMV).
6. **inputSchema:** ver §9.1
7. **Defaults:** `periodo = "mes_anterior"`; `canais = ["all"]`; `marcas = null`
   (todas); `granularidade = "none"`; `comparar = false`
8. **Allowlists:** canais `tiktok|ml|shopee|all`; marcas `apice|barbours|kokeshi|lescent|rituaria`;
   granularidade `none|day|week|month`
9. **Limite padrão:** série limitada a 120 buckets
10. **Limite máximo:** 366 buckets (day) — acima disso força `week`/`month`
11. **Intervalo máximo:** **366 dias** (espelha `MAX_RANGE_DAYS`)
12. **Endpoints internos:** `/api/v1/performance/overview`, `/brands`, e
    `/trend` somente quando `granularidade != "none"`
13. **Transformação server-side:** funde overview + brands em um único envelope;
    projeta apenas os campos do `outputSchema`; anexa warning de dia parcial
    quando `fim >= hoje(BRT)`; nunca converte `null` em `0`.
14. **outputSchema:** ver §10.1
15. **Exemplo:** §10.4
16. **Proveniência:** `marts.fact_marketplace_daily_performance` (Neon)
17. **Definição das métricas:** §12
18. **Restrições:** dia corrente parcial; comissão ML ausente; custo TikTok direcional
19. **Erros:** `PERIODO_INVALIDO`, `INTERVALO_EXCEDIDO`, `MARCA_INVALIDA`,
    `CANAL_INVALIDO`, `FONTE_INDISPONIVEL`, `TIMEOUT_FONTE`
20. **Escopo:** `torre:read`

### 8.2 `torre_comparar_canais_marcas`

1. **name:** `torre_comparar_canais_marcas`
2. **title:** Comparação de eficiência entre canais e marcas
3. **description:**
   > Use para comparar **eficiência** entre canais e marcas: ROAS, ACOS,
   > investimento em ads sobre GMV, custo de marketplace e frete do vendedor,
   > com medianas e p75 por canal. Grão: uma linha por marca × canal. Retorna o
   > universo completo (não é top-N). **Cada métrica traz `aplicavel` e
   > `disponivel`: "não aplicável" (o canal não opera esse custo) é diferente de
   > "sem dado".** Limitações que devem ser ditas ao usuário: a comissão do
   > Mercado Livre **não existe** no mart; o custo do TikTok é direcional
   > (base de repasse difere do GMV em ~5,5%). Não use para totais de GMV
   > (use `torre_desempenho_marketplaces`) nem para produtos.
4. **Responde:** qual canal/marca é mais eficiente em mídia; quem está acima da
   mediana; onde o custo de marketplace é atípico.
5. **Não responde:** margem/lucro real; comissão ML; ranking de produto.
6. **inputSchema:** §9.2 · **Defaults:** `periodo = "mes_anterior"`, todos os canais/marcas
7. **Limite padrão/máximo:** 30 / 60 linhas (5 marcas × 3 canais ⇒ nunca satura)
8. **Intervalo máximo:** 366 dias
9. **Endpoint interno:** `/api/v1/performance/canais`
10. **Transformação:** projeta a matriz marca×canal + medianas; **propaga
    `data_warning` do backend** para `meta.warnings`; descarta `signals` textuais
    não normalizados.
11. **outputSchema:** §10.2 · **Proveniência:** `marts.fact_marketplace_daily_performance`
12. **Restrições:** comissão ML `null`; `conversion_rate` com escala inconsistente
    entre canais — **não exposta** por esta tool
13. **Erros:** iguais aos da §8.1 · **Escopo:** `torre:read`

### 8.3 `torre_produtos_prioritarios`

1. **name:** `torre_produtos_prioritarios`
2. **title:** Produtos prioritários por canal
3. **description:**
   > Use para "quais produtos vendem mais", "onde investir", "curva ABC/Pareto"
   > em um canal específico (Mercado Livre, TikTok Shop ou Shopee). Grão:
   > produto. **Retorna top-N ordenado (padrão 20, máximo 50), não o universo**
   > — o total real vem em `meta.total_count`. Métrica principal: receita bruta
   > do produto no escopo. Só o Mercado Livre tem ROAS/ACOS por produto.
   > **Não existe margem real** (sem CMV) e a base do ML é **cumulativa, sem
   > competência mensal** — não aceita filtro de período. Não use para totais
   > por canal nem para comparar marcas.
4. **Responde:** top produtos por receita; bucket de Pareto; velocidade de
   receita; sinal de ação (ML).
5. **Não responde:** margem/lucro; estoque; produto por UF; período no ML.
6. **inputSchema:** §9.3 · **Defaults:** `limite = 20`, `ordenar_por = "receita"`
7. **Limite padrão/máximo:** **20 / 50** (o backend aceita até 100; o MCP é mais
   estrito para manter a resposta pequena)
8. **Endpoints internos:** `/produtos/ml` · `/produtos/tiktok` · `/produtos/shopee`
   (escolhido pelo enum `canal`, **caminho fixo no código**)
9. **Transformação:** projeta somente os campos do schema; **remove
   `estimated_margin`**; marca `escopo_temporal` como `cumulativo` (ML) ou
   `mensal` (TikTok/Shopee).
10. **outputSchema:** §10.3 · **`total_count`:** verdadeiro (campo `total` da API)
11. **Restrições:** `estimated_margin` nunca exposto; ML sem período
12. **Erros:** `CANAL_INVALIDO`, `LIMITE_EXCEDIDO`, `PARAMETRO_INCOMPATIVEL`
    (ex.: período informado para ML), `FONTE_INDISPONIVEL`
13. **Escopo:** `torre:read`

### 8.4 `torre_qualidade_dados`

1. **name:** `torre_qualidade_dados`
2. **title:** Frescor e confiabilidade dos dados da Torre
3. **description:**
   > Use **antes de confiar em qualquer número**, e para responder "os dados
   > estão atualizados?", "até quando temos dado?", "posso confiar nisso?".
   > Devolve `refreshed_at` por superfície, o último dia com dado consolidado,
   > se o dia corrente é parcial, e a lista de limitações conhecidas
   > (cancelamento TikTok não medido, comissão ML ausente, cobertura regional
   > menor que a de canais). Não devolve GMV nem ranking — é uma tool de
   > metadados. Use-a quando o usuário questionar uma divergência.
4. **Responde:** frescor; cobertura; ressalvas conhecidas; disponibilidade das fontes.
5. **Não responde:** valores de negócio.
6. **inputSchema:** `{}` (`{ "type": "object", "additionalProperties": false }`)
7. **Endpoints internos:** `/health-datasource`, `/overview` (sonda barata do
   mês corrente para extrair `refreshed_at`), `/quality`
8. **Transformação:** compara `date_to` efetivo com `today_brt()`; deriva
   `dia_corrente_parcial`; monta a lista estática de limitações auditadas.
9. **Restrições:** as limitações são **curadas no código do adapter** e precisam
   ser revistas quando o checkpoint de qualidade mudar — risco de desatualização
   registrado no §21.
10. **Erros:** `FONTE_INDISPONIVEL` · **Escopo:** `torre:read`

### 8.5 `torre_regioes_vendas`

1. **name:** `torre_regioes_vendas`
2. **title:** Vendas por região (UF)
3. **description:**
   > Use para "onde vendemos", "quais estados", "concentração geográfica".
   > Grão: UF (27 + `XX` desconhecida), agregado no período. Retorna o universo
   > das UFs com venda, ordenado por GMV. **Aviso obrigatório: a cobertura
   > regional é materialmente menor que a de canais — os totais aqui NÃO
   > reconciliam com `torre_desempenho_marketplaces` e a diferença não é erro de
   > cálculo, é cobertura.** Não use para totais oficiais da empresa nem para
   > qualquer coisa em grão de pedido ou cliente.
4. **Responde:** GMV/pedidos/unidades por UF; concentração; ranking de estados.
5. **Não responde:** cidade, CEP, endereço, cliente; totais oficiais.
6. **inputSchema:** §9.4 · **Defaults:** `periodo = "mes_anterior"`, `limite = 27`
7. **Limite máximo:** 28 (27 UFs + `XX`) · **Intervalo máximo:** 366 dias
8. **Endpoints internos:** `/api/v1/regioes/summary` e `/by-uf`
9. **Transformação:** injeta **sempre** o warning de sub-cobertura em
   `meta.warnings`, mesmo quando não solicitado.
10. **PII:** nenhuma — a UF é a menor granularidade geográfica exposta
11. **Erros:** `UF_INVALIDA`, `PERIODO_INVALIDO`, `FONTE_INDISPONIVEL`
12. **Escopo:** `torre:read`

### 8.6 Fora do MVP (e por quê)

| Candidata | Motivo |
|---|---|
| `torre_operacao_alertas` | PII (creators). Só após projeção que remova a dimensão. |
| `torre_inteligencia_portfolio` | Payload ~29 KB e base cumulativa sem período; precisa de recorte definido pelo proprietário. |
| `torre_tempo_real` | Fonte em **500** (Data Mart inacessível). |
| `torre_marca_detalhe` | É TikTok-only; alto risco de o Oráculo ler como "a marca toda". |

### 8.7 Proibições respeitadas

Nenhuma tool de `query_database`, `run_sql`, `call_api`, `fetch_endpoint`,
`get_any_data`, proxy de URL, execução de código ou escrita. O modelo **não
escolhe caminho HTTP** — só valores dentro de enums fechados.

---

## 9. Schemas de entrada

Zod v4. Sem `.passthrough()`; objetos **estritos**.

### 9.1 `torre_desempenho_marketplaces`

```ts
z.object({
  periodo: z.enum(["mes_anterior", "mes_atual", "ultimos_7_dias",
                   "ultimos_30_dias", "personalizado"]).default("mes_anterior"),
  data_inicio: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  data_fim:    z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  canais: z.array(z.enum(["tiktok", "ml", "shopee"])).min(1).max(3).optional(),
  marcas: z.array(z.enum(["apice","barbours","kokeshi","lescent","rituaria"]))
           .min(1).max(5).optional(),
  granularidade: z.enum(["none", "day", "week", "month"]).default("none"),
  comparar: z.boolean().default(false),
}).strict()
```

Regras adicionais (via `.superRefine`, para gerar erro legível):

- `periodo = "personalizado"` ⇒ `data_inicio` **e** `data_fim` obrigatórios;
- fora de `"personalizado"`, ambos devem estar **ausentes** (nunca misturar);
- `data_inicio <= data_fim`; `data_fim` não pode ser futura;
- intervalo ≤ **366 dias**;
- `granularidade = "day"` com intervalo > 366 ⇒ rejeitar (não degradar em silêncio).

### 9.2 `torre_comparar_canais_marcas`

```ts
z.object({
  periodo: z.enum(["mes_anterior", "mes_atual", "personalizado"]).default("mes_anterior"),
  data_inicio: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  data_fim:    z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  canais: z.array(z.enum(["tiktok", "ml", "shopee"])).min(1).max(3).optional(),
  marcas: z.array(z.enum(["apice","barbours","kokeshi","lescent","rituaria"]))
           .min(1).max(5).optional(),
}).strict()
```

### 9.3 `torre_produtos_prioritarios`

```ts
z.object({
  canal: z.enum(["ml", "tiktok", "shopee"]),              // obrigatório
  marca: z.enum(["apice","barbours","kokeshi","lescent","rituaria"]).optional(),
  mes: z.string().regex(/^\d{4}-\d{2}$/).optional(),      // ignorado para ml
  pareto_bucket: z.enum(["A_top50","B_next30","C_next15","D_tail"]).optional(),
  limite: z.number().int().min(1).max(50).default(20),
  ordenar_por: z.enum(["receita", "unidades", "roas"]).default("receita"),
}).strict()
```

- `ordenar_por = "roas"` só é válido para `canal = "ml"` → senão
  `PARAMETRO_INCOMPATIVEL`;
- `mes` com `canal = "ml"` → erro explícito (a base é cumulativa), **nunca**
  aceitar e ignorar em silêncio.

### 9.4 `torre_regioes_vendas`

```ts
z.object({
  periodo: z.enum(["mes_anterior", "mes_atual", "ultimos_30_dias",
                   "personalizado"]).default("mes_anterior"),
  data_inicio: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  data_fim:    z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  canais: z.array(z.enum(["tiktok", "ml", "shopee"])).min(1).max(3).optional(),
  marcas: z.array(z.enum(["apice","barbours","kokeshi","lescent","rituaria"]))
           .min(1).max(5).optional(),
  ufs: z.array(z.string().regex(/^([A-Z]{2})$/)).min(1).max(28).optional(),
  limite: z.number().int().min(1).max(28).default(27),
}).strict()
```

`ufs` é validado contra a lista fechada das 27 UFs + `XX` — o regex sozinho não
basta.

### 9.5 `torre_qualidade_dados`

```ts
z.object({}).strict()
```

Serializado como `{ "type": "object", "additionalProperties": false }`, forma
recomendada pela spec para tools sem parâmetros.

---

## 10. Schemas de saída

### 10.1 `torre_desempenho_marketplaces`

```ts
z.object({
  meta: MetaEnvelope,
  data: z.object({
    total: z.object({
      gmv: z.number(), pedidos: z.number().int(),
      ticket_medio: z.number().nullable(),
      gmv_periodo_anterior: z.number().nullable(),
      variacao_pct: z.number().nullable(),
    }),
    por_canal: z.array(z.object({
      canal: z.enum(["tiktok","ml","shopee"]),
      canal_label: z.string(),
      gmv: z.number().nullable(), pedidos: z.number().int().nullable(),
      participacao_pct: z.number().nullable(),
    })),
    por_marca: z.array(z.object({
      marca: z.string(), label: z.string(),
      gmv: z.number(), pedidos: z.number().int(),
      variacao_pct: z.number().nullable(),
    })),
    serie: z.array(z.object({
      data: z.string(), label: z.string(),
      gmv: z.number(), pedidos: z.number().int(),
    })).nullable(),   // null = não solicitada; [] = solicitada e vazia
  }),
})
```

> A distinção `null` vs `[]` em `serie` é deliberada e espelha o contrato de
> `TrendResponse.comparison` do backend.

### 10.2 `torre_comparar_canais_marcas`

```ts
z.object({
  meta: MetaEnvelope,
  data: z.object({
    linhas: z.array(z.object({
      marca: z.string(), canal: z.string(), canal_label: z.string(),
      gmv: z.number(), pedidos: z.number().int(),
      ads_investimento: z.number().nullable(),
      ads_sobre_gmv_pct: z.number().nullable(),
      roas: z.number().nullable(),
      acos_pct: z.number().nullable(),
      custo_marketplace_pct: z.number().nullable(),
      frete_vendedor_pct: z.number().nullable(),
      ads_aplicavel: z.boolean(),   ads_disponivel: z.boolean(),
      custo_aplicavel: z.boolean(), custo_disponivel: z.boolean(),
      frete_aplicavel: z.boolean(), frete_disponivel: z.boolean(),
      aviso: z.string().nullable(),
    })),
    medianas_por_canal: z.array(z.object({
      canal: z.string(),
      gmv_mediana: z.number().nullable(),
      roas_mediana: z.number().nullable(),
      custo_marketplace_pct_mediana: z.number().nullable(),
      custo_marketplace_pct_p75: z.number().nullable(),
      marcas_com_dado: z.number().int(),
    })),
  }),
})
```

### 10.3 `torre_produtos_prioritarios`

```ts
z.object({
  meta: MetaEnvelope,                    // meta.cobertura = "top-N por receita"
  data: z.object({
    escopo_temporal: z.enum(["cumulativo", "mensal"]),
    itens: z.array(z.object({
      posicao: z.number().int(),
      identificador: z.string(),         // item_id / product_id / sku_ref
      titulo: z.string().nullable(),
      marca: z.string(),
      receita: z.number(),
      unidades: z.number().int().nullable(),
      participacao_receita_pct: z.number().nullable(),
      pareto_bucket: z.string().nullable(),
      roas: z.number().nullable(),       // somente ml
      acos_pct: z.number().nullable(),   // somente ml
      sinal_acao: z.string().nullable(), // somente ml
    })),
  }),
})
```

### 10.4 Exemplo pequeno de saída (`torre_desempenho_marketplaces`)

```json
{
  "meta": {
    "fonte": "Torre de Controle de Marketplaces — GoBeauté",
    "camada": "marts (Neon)",
    "periodo": { "inicio": "2026-07-01", "fim": "2026-07-31", "inclusivo": true },
    "timezone": "America/Sao_Paulo",
    "moeda": "BRL",
    "unidade_monetaria": "reais",
    "filtros_aplicados": { "canais": "all", "marcas": null },
    "definicao_metrica": "GMV = valor bruto dos pedidos, sem frete, sem cancelados, sem devoluções",
    "refreshed_at": "2026-08-05T18:53:53.827542+00:00",
    "cobertura": "universo completo dos canais e marcas no escopo",
    "limit": null,
    "returned_count": 3,
    "total_count": null,
    "warnings": []
  },
  "data": {
    "total": {
      "gmv": 22544297.31, "pedidos": 331544,
      "ticket_medio": 67.99,
      "gmv_periodo_anterior": null, "variacao_pct": null
    },
    "por_canal": [
      { "canal": "tiktok", "canal_label": "TikTok Shop",   "gmv": 13112450.10, "pedidos": 190233, "participacao_pct": 58.16 },
      { "canal": "ml",     "canal_label": "Mercado Livre", "gmv":  5203118.44, "pedidos":  71880, "participacao_pct": 23.08 },
      { "canal": "shopee", "canal_label": "Shopee",        "gmv":  4228728.77, "pedidos":  69431, "participacao_pct": 18.76 }
    ],
    "por_marca": [],
    "serie": null
  }
}
```

> Os valores de `por_canal` acima são **ilustrativos do formato**. Os únicos
> números reais medidos nesta auditoria e citados neste documento são o GMV
> total de jul/2026 (R$ 22.544.297,31), o de 01–18/08/2026 (R$ 11.512.531,49) e
> os pontos diários usados para demonstrar a parcialidade do dia corrente.

---

## 11. Envelope e proveniência

Envelope canônico, idêntico em todas as tools:

```jsonc
{
  "meta": {
    "fonte": "Torre de Controle de Marketplaces — GoBeauté",
    "camada": "marts (Neon)",
    "periodo": { "inicio": "YYYY-MM-DD", "fim": "YYYY-MM-DD", "inclusivo": true },
    "timezone": "America/Sao_Paulo",
    "moeda": "BRL",
    "unidade_monetaria": "reais",   // explícito: NÃO são centavos
    "filtros_aplicados": {},        // eco do que o backend realmente aplicou
    "definicao_metrica": "...",
    "refreshed_at": null,           // ISO-8601 UTC do mart, ou null
    "cobertura": "universo completo" | "top-N por <critério>",
    "limit": 20,
    "returned_count": 10,
    "total_count": null,            // só quando verdadeiro
    "warnings": []
  },
  "data": {}
}
```

**Regras (todas verificáveis nos testes do §18):**

- `null` **nunca** vira `0`. Ausência de medição e valor zero são coisas
  diferentes — o backend já respeita isso (`_ratio()` devolve `None` com
  denominador ≤ 0) e o adapter não pode desfazer.
- `total_count` só é preenchido quando a fonte devolve um total verdadeiro
  (produtos: campo `total`). Nas demais tools é `null` — **nunca** `returned_count`.
- Resultado top-N **sempre** rotulado em `meta.cobertura`.
- Datas inclusivas, no fuso declarado; `refreshed_at` fica em UTC (é o que o
  backend devolve) e isso é dito explicitamente.
- Dinheiro em **reais**, declarado em `unidade_monetaria`.
- `filtros_aplicados` reflete o eco do backend (`FiltersEcho`), não o input do
  modelo — se o backend normalizar diferente, prevalece o backend.
- **Warnings obrigatórios** (injetados pelo adapter, não opcionais):
  - `fim >= hoje(BRT)` → `"O período inclui o dia corrente, que é parcial: a carga do dia ainda não fechou e o valor está subestimado."`
  - tool de regiões → aviso permanente de sub-cobertura;
  - canais/financeiro com ML → aviso de comissão ausente;
  - qualidade com TikTok → aviso de cancelamento não medido.
- **Nunca** devolver: HTML, stack trace, host interno, DSN, SQL, segredo,
  milhares de linhas.
- Tamanho-alvo por resposta: **≤ ~8 KB**; teto duro de 256 KB no adapter.
- Entrega dupla: `structuredContent` (conforme `outputSchema`) **e** o mesmo JSON
  serializado em um bloco `text` — recomendação explícita da spec 2026-07-28.

---

## 12. Definições de negócio

| Termo | Definição operacional | Fonte |
|---|---|---|
| **GMV** | Valor bruto dos pedidos, **sem frete**, **sem cancelados**, **sem devoluções**. Definição ratificada do projeto. | `marts.fact_marketplace_daily_performance.gmv` |
| **Pedidos** | Contagem de pedidos pagos no período. | `.orders` |
| **Ticket médio** | `gmv / pedidos`. `null` se pedidos = 0. | derivado |
| **ROAS** | `ad_revenue / ad_spend`. `null` se `ad_spend <= 0` (nunca 0, nunca infinito). | `_ratio()` |
| **ACOS %** | `ad_spend / ad_revenue × 100`. | `_pct()` |
| **Custo de marketplace %** | `total_fees / gmv × 100`. **Nulo para ML** — o mart não tem o dado. | `_channel_row()` |
| **Frete do vendedor %** | `seller_shipping_cost / gmv × 100`. Não aplicável ao TikTok. | `_SHIPPING_APPLICABLE` |
| **Variação vs anterior** | Mês fechado → mês calendário anterior completo. Período custom → janela anterior de mesma duração. | `resolve_compare_period()` |
| **Período default** | Gerencial/Canais/Financeiro/Qualidade → **mês anterior completo**. Pedidos → **últimos 30 dias**. | `deps/filters.py` |
| **Dia operacional** | `America/Sao_Paulo`, lido uma única vez por request. | `today_brt()` |
| **Aplicável × Disponível** | *Aplicável* = o canal opera esse custo por modelo de negócio. *Disponível* = o mart tem o dado no período. **N/A ≠ Sem dado.** | contrato do Gate 1 |
| **Margem** | **Não existe.** Não há CMV nos três marketplaces. `estimated_margin` do ML não é margem real e não deve ser exposto. | auditoria de Produtos |

---

## 13. Autenticação e autorização

### 13.1 Estado atual — verificado, não presumido

| Requisito | Situação |
|---|---|
| Autenticação própria na Torre | ❌ **Não existe.** Sem `middleware.ts`, sem lib de auth, sem sessão. |
| Provedor OAuth/OIDC | ❌ Nenhum. |
| Issuer | ❌ Nenhum. |
| JWKS | ❌ Zero ocorrências de `jwks` no repositório. |
| Introspection endpoint | ❌ Nenhum. |
| Claims / roles / scopes | ❌ Não existem — não há conceito de usuário no projeto. |
| Audience para o MCP | ❌ Não definida. |
| Tabelas `mcp_oauth_*` | ❌ **Zero ocorrências** em `docs/` e `apps/` — e **não localizadas** nos projetos Supabase acessíveis (§13.1.1). |

Sobre `mcp_oauth_*`: não foi encontrada nenhuma referência neste repositório.
Caso apareçam em documentação de **outro** sistema, isso **não** constitui prova
de infraestrutura reutilizável aqui. A proveniência foi investigada
diretamente — ver §13.1.1.

#### 13.1.1 Proveniência de `mcp_oauth_*` — auditoria Supabase (2026-08-18)

**Veredito: `NÃO LOCALIZADO nos projetos acessíveis`** (com um projeto
inconclusivo).

Método: **metadata-only** via Supabase MCP — `list_projects` e `list_tables`.
Nenhum SQL, nenhuma leitura de linhas, nenhuma escrita, nenhum acesso a conteúdo
de `auth.users`, sessões ou tokens. Apenas nomes de projeto, schema e tabela.

| Projeto acessível | Status | Varredura | `mcp_oauth_*` |
|---|---|---|---|
| GTI | ACTIVE_HEALTHY | **todos os schemas** | ❌ ausente |
| FormsTransp | ACTIVE_HEALTHY | **todos os schemas** | ❌ ausente |
| Planner Financeiro | **INACTIVE** | ⚠️ falhou (timeout de conexão) | **inconclusivo** |

**Achados factuais:**

1. **Não existe projeto chamado `torre_de_performance_b2b`** entre os projetos
   acessíveis. A atribuição que circulava — de que as tabelas `mcp_oauth_*`
   pertenceriam a esse projeto — **não se sustenta** com o acesso disponível.
2. **Nenhuma tabela `mcp_oauth_*` foi localizada** em nenhum schema dos dois
   projetos que responderam.
3. Existe, no projeto **GTI**, a tabela `public.mcp_personal_access_tokens`.
   É infraestrutura de auth **de MCP**, mas baseada em **PAT**, não em OAuth, e
   pertence a uma aplicação distinta (gestão de tarefas/OKR/KPI). **Não** é
   `mcp_oauth_*` e não foi inspecionada em conteúdo.
4. **Cuidado com falso positivo:** ambos os projetos possuem `auth.oauth_clients`,
   `auth.oauth_authorizations`, `auth.oauth_consents`,
   `auth.oauth_client_states` e `auth.custom_oauth_providers`. Essas tabelas são
   **built-ins do GoTrue/Supabase**, criadas em qualquer projeto, todas com **0
   linhas**. Não são infraestrutura MCP e **não devem** ser confundidas com
   `mcp_oauth_*`.

**Consequências — válidas mesmo se as tabelas existirem em algum lugar:**

- pertencem a **outro sistema/projeto** enquanto não houver prova de
  compatibilidade (issuer, audience, contrato operacional);
- **não foram copiadas, consultadas por conteúdo, nem adotadas**;
- **não autorizam reutilização** automática, por decisão do proprietário;
- **não fazem parte** do MVP local atual;
- a ausência ou indisponibilidade delas **não bloqueia** o MVP local (§14, D1).

O único item que permanece formalmente em aberto é o projeto **Planner
Financeiro**, inacessível por estar INACTIVE. Como nenhuma reutilização está
autorizada, isso **não é bloqueante** — é apenas uma lacuna registrada.

### 13.2 Desenho A — OAuth/OIDC definitivo

Requisitos mínimos e situação:

| # | Requisito | Situação |
|---|---|---|
| 1 | Issuer real e acessível | ❌ ausente |
| 2 | Metadata RFC 8414 | ❌ ausente |
| 3 | JWKS ou introspection | ❌ ausente |
| 4 | `audience`/`resource` do MCP | ❌ ausente |
| 5 | Scopes (mínimo `torre:read`) | ❌ ausente |
| 6 | Identidade por pessoa | ❌ ausente |
| 7 | Expiração e revogação | ❌ ausente |
| 8 | `/.well-known/oauth-protected-resource` via `protectedResourceHandler` | ⚙️ implementável — **depende de (1)** |
| 9 | `withMcpAuth(..., { required: true })` | ⚙️ implementável — **depende de (1)** |
| 10 | Auditoria sem token | ⚙️ implementável |

**Veredito: `BLOCKED_BY_IDENTITY_PROVIDER`.** Os itens 8–10 são só fiação; sem os
itens 1–7 não há o que ligar. Nenhum issuer é inventado neste documento.

### 13.3 Desenho B — bearer compartilhado (piloto)

Descrito **apenas como alternativa temporária**, sujeita à aprovação explícita do
proprietário. **Esta task não o aprova.**

- Segredo `ORACLE_MCP_BEARER_TOKEN`, **server-side** na Vercel (Production e
  Preview separados), ≥ 32 bytes aleatórios.
- Autenticação **obrigatória** — sem modo anônimo, sem "se o token existir".
- Comparação em **tempo constante** (`crypto.timingSafeEqual` sobre digests de
  mesmo tamanho); comparar o hash, nunca a string crua, para não vazar tamanho.
- Escopo **exclusivamente read-only** — reforçado pela ausência de tool de escrita.
- Rotação trimestral e imediata a qualquer suspeita; procedimento documentado
  antes do go-live.
- **Nunca** versionado, **nunca** logado, **nunca** enviado ao browser, **nunca**
  com prefixo `NEXT_PUBLIC_`.
- Falha → **401** com `WWW-Authenticate`, sem revelar se o token existe.

**Risco explícito e inegociável:** com bearer compartilhado **não há identidade
por pessoa**. Todos os portadores são indistinguíveis; a auditoria registra
"alguém com o token", não "quem". Revogar afeta todos simultaneamente. Se o token
vazar, o vazamento é indetectável por identidade — só por padrão de uso.

### 13.4 Regra de decisão

**Decisão do proprietário (2026-08-18): OAuth está ADIADO.** A regra vigente
passa a distinguir *implementar* de *publicar*:

1. **OAuth** — **adiado**. Continua sendo o destino para acesso por pessoa, mas
   não é pré-requisito do trabalho local. Requisitos do §13.2 permanecem válidos
   para quando for retomado.
2. **Bearer compartilhado** — **não aprovado**. Permanece descrito apenas como
   alternativa possível para um piloto futuro, sujeito a aprovação explícita.
3. **Publicação pública do MCP: BLOQUEADA** enquanto não houver autenticação
   real decidida e implementada.
4. **Implementação local/dev/test: PERMITIDA**, sob as condições do §13.4.1.

#### 13.4.1 Fronteira exata entre implementar e publicar

O que a decisão de auth bloqueia — e o que não bloqueia:

| Atividade | Situação |
|---|---|
| Escrever a rota, o adapter e as tools em ambiente **local/dev/test** | ✅ **permitido** |
| Rodar o servidor MCP em `localhost`, **read-only** | ✅ permitido |
| Testes automatizados com auth **fake/stub** | ✅ permitido — **somente em teste** |
| Deploy de `/api/mcp` habilitado em **produção** sem auth real | ❌ **proibido** |
| Habilitar a rota em Preview público sem auth real | ❌ proibido |
| Tratar bypass da Vercel como autenticação da rota | ❌ **proibido** (§16.1) |
| Reutilizar `mcp_oauth_*` de outro projeto | ❌ proibido (§13.1.1) |

**Regras inegociáveis para qualquer implementação futura:**

- **Fail-closed em produção.** Sem credencial válida, a rota nega. A ausência de
  configuração de auth deve **derrubar a requisição**, nunca liberá-la. O modo
  anônimo não pode existir como *fallback* silencioso de configuração faltante.
- **Auth fake vive apenas em teste.** Nunca em código de produção, nunca atrás de
  uma variável de ambiente que possa ser ligada por engano em produção.
- **Nenhum bypass de plataforma conta como auth.** O Automation Bypass da Vercel
  destrava o projeto inteiro (§16.1) e **não está autorizado**.
- Enquanto a auth real não existir, o valor default de habilitação da rota em
  produção é **desligado**.

> Publicar `/api/mcp` sem autenticação equivaleria a tornar os agregados
> comerciais da empresa legíveis por qualquer pessoa na internet — e, dado que a
> Deployment Protection aparenta estar desligada, sem nenhuma barreira de
> plataforma por trás. Por isso a fronteira acima é sobre *exposição*, não sobre
> *escrita de código*.

### 13.5 Risco herdado — frente separada

**Proteger `/api/mcp` NÃO protege os endpoints REST do Render.**

Estado verificado: `https://mktplace-api.onrender.com/api/v1/performance/*`
responde **200 sem credencial alguma**. Qualquer pessoa que conheça a URL obtém
GMV, marcas, canais e produtos — independentemente do que for feito no MCP.

O MCP **reduz** a superfície (allowlists, limites, sem PII), mas **não fecha** a
porta existente. Fechar exige trabalho no FastAPI (autenticação de serviço,
restrição de origem ou rede) e é uma **frente própria**, fora de OM0–OM2. Está
registrado como risco R1 (§21).

---

## 14. Decisões e requisitos pendentes do proprietário

| # | Decisão | Bloqueia | Opções |
|---|---|---|---|
| **D1** | **Modelo de autenticação** — **DECIDIDO em 2026-08-18: OAuth adiado** | **publicação pública** do MCP (não bloqueia OM1 local/dev/test — §13.4.1) | retomar quando houver issuer (desenho A) ou aprovar bearer de piloto (desenho B). Até lá, a rota permanece **desabilitada em produção**, fail-closed |
| **D2** | Proteger a API do Render | não bloqueia OM1, mas é risco aberto | (a) frente separada agora; (b) aceitar formalmente o risco |
| **D3** | Escopo de exposição | catálogo final | confirmar que GMV/ROAS/produtos por marca podem ser lidos via Claude.ai |
| **D4** | Operações (PII de creators) | tool futura | (a) financiar projeção sem creator; (b) manter fora |
| **D5** | Inteligência | tool futura | definir o recorte útil dos ~29 KB |
| **D6** | Ambiente do piloto | config Vercel | somente Production, ou Preview também |
| **D7** | Skill do Oráculo | OM2 | informar onde vive a skill (não localizada nesta máquina) |
| **D8** | Tratamento do dia corrente | contrato das tools | (a) avisar e incluir *(recomendado)*; (b) truncar em D-1 |

**Recomendações:** D1 → (b) apenas se o piloto for restrito e houver plano de
migração para OAuth; D2 → (a); D8 → (a).

---

## 15. Adapter server-side

Camada dedicada (`apps/web/src/server/torre-client.ts` em OM1) — **não** reutiliza
`apps/web/src/lib/api-client.ts`, que é código de browser e tem fallback mock.

| Característica | Decisão |
|---|---|
| Execução | **somente servidor**; nunca importado por componente cliente |
| URL base | `MCP_BACKEND_API_URL`, server-side. **Nunca** `NEXT_PUBLIC_*` |
| Validação da base | `https:` + hostname **exatamente** `mktplace-api.onrender.com` (constante server-only), porta padrão, sem credencial/query/fragment, path apenas raiz. Configuração divergente **falha fechada** — ver §27.1 |
| Caminhos | **constantes literais** no código; o modelo nunca fornece path, host ou query crua |
| Querystring | montada por `URLSearchParams` a partir de valores **já validados** por Zod |
| Timeout | `AbortController`, **8 s** por chamada; teto de **25 s** por tool |
| Limite de corpo | leitura em stream, aborta acima de **256 KB** |
| Content-Type | **exige `application/json`**; corpo HTML (ex.: 403 do WAF) → `FONTE_INDISPONIVEL`, corpo descartado |
| Validação da resposta | Zod sobre o payload do backend antes de projetar |
| Retry | **ZERO**. A falha é explícita e sanitizada; quem decide repetir é o modelo/cliente. Nenhum backoff interno, nenhuma tentativa oculta |
| Concorrência | no máximo 3 chamadas paralelas por invocação de tool |
| Erros | categorias fechadas: `FONTE_INDISPONIVEL`, `TIMEOUT_FONTE`, `PARAMETRO_INVALIDO`, `INTERVALO_EXCEDIDO`, `LIMITE_EXCEDIDO`, `SEM_DADO_NO_PERIODO`, `NAO_AUTORIZADO` |
| Falha | **sempre** erro explícito. Nunca lista vazia silenciosa, nunca mock, nunca dado parcial sem aviso |
| Logs | método, caminho lógico, status, duração, categoria. **Nunca** token, header `Authorization`, querystring crua ou corpo |

**Runtime:** a rota **deve** declarar `export const runtime = "nodejs"`.
`mcp-handler@2.1.1` e `@modelcontextprotocol/server@2.0.0` exigem `node >= 20`;
Edge não satisfaz esse contrato. Também vale declarar `export const maxDuration`
compatível com o teto de 25 s.

**Erro do backend vs erro da tool:** um 422 do FastAPI significa que o adapter
montou um pedido inválido — é **defeito do adapter**, pois o Zod deveria ter
barrado antes. Deve ser logado como anomalia, e não repassado como se fosse
escolha do usuário.

---

## 16. Vercel

Mapeado sem alterar nada. Nenhuma configuração foi tocada; a CLI não foi usada.

| Item | Situação | Ação futura (OM2) |
|---|---|---|
| Root Directory | não confirmado (provável `apps/web`) | confirmar no painel antes do deploy |
| Node version | não fixada no repositório | garantir **≥ 20** (idealmente 22) |
| Domínio canônico | `mktplace-gobeaute.vercel.app` → **200** | manter; **não** alterar alias |
| Deployment Protection | aparenta **desligada** (200 sem auth) | **não desativar nada**. Se for ligada depois, ver §16.1: **não existe** exceção por path para GET/POST |
| Fluid Compute | não confirmado | verificar; útil para I/O-bound |
| Timeout da função | default | alinhar ao teto de 25 s do adapter |
| Variáveis server-side | nenhuma existe hoje | `MCP_BACKEND_API_URL` e, se D1 = bearer, `ORACLE_MCP_BEARER_TOKEN` — **sem** `NEXT_PUBLIC_` |
| Preview × Production | — | segredos **distintos** por ambiente; piloto começa só em Production |
| Logs | — | garantir que não registrem `Authorization` |

**Regras respeitadas:** não desativar Deployment Protection globalmente; não
expor o site inteiro; não alterar domínio/alias/variável; não usar a CLI;
não fazer deploy.

### 16.1 Correção: a Vercel **não** isenta um path da Deployment Protection

Uma premissa comum — e que constava de uma versão anterior desta seção — é que
seria possível criar uma exceção "apenas para `/api/mcp`". **Isso não existe.**
Verificado na documentação oficial da Vercel (2026-08-18):

| Mecanismo | Escopo real | Serve para isentar `/api/mcp` em GET/POST? |
|---|---|---|
| **OPTIONS Allowlist** | path, **mas só método `OPTIONS`** | ❌ — só preflight CORS |
| **Deployment Protection Exceptions** | **domínios de preview** | ❌ — não é path |
| **Protection Bypass for Automation** | **projeto inteiro**, via secret | ⚠️ funciona, mas não é por rota |
| **Shareable Links** | branch deployment | ❌ |

Citação da doc oficial sobre a OPTIONS Allowlist:

> "If a request path **starts with** one of the specified paths **and has the
> method `OPTIONS`**, it bypasses Deployment Protection."

E sobre as Exceptions:

> "Specify **preview domains** that should be exempt from deployment protection."

**Consequências práticas:**

1. Hoje isso é **inócuo**: a produção já responde 200 sem proteção, então não há
   nada a contornar.
2. Se a Deployment Protection for ligada no futuro, a única forma de o Oráculo
   alcançar `/api/mcp` é o **Protection Bypass for Automation**, cujo secret
   destrava **o projeto inteiro** — não apenas a rota MCP. Isso é um *trade-off*
   de segurança a decidir conscientemente, não um detalhe de configuração.
   Registrado como risco **R15** (§21).
3. Portanto o desenho **não pode** contar com isolamento de rota pela
   plataforma. A auth própria do `/api/mcp` (§13) é a **única** camada específica
   da rota, o que reforça que ela é obrigatória, e não redundante.

> Nota: um eventual bypass de Deployment Protection **nunca** substitui a auth da
> rota. São camadas independentes, e o MCP não pode depender da plataforma.

---

## 17. Segurança e privacidade

**Privacidade — o que nunca sai pelo MCP:**

- nomes de compradores, clientes ou creators;
- `order_id`, CPF/CNPJ, e-mail, telefone, endereço, CEP;
- qualquer granularidade transacional (pedido, item de pedido);
- geografia abaixo de **UF**.

`/operacoes` está fora do MVP exatamente por conter `creator`/`creators`.
`/pedidos`, apesar do nome, é agregado por marca/dia — verificado chave a chave.

**Segurança:**

- **hoje (OM1):** não existe autenticação real. Contexto não autorizado ou
  desabilitado → **404 genérico** (§25.6, §27.3-C). O par
  "sem credencial → 401" só passa a valer **quando** houver auth real;
- input só por enums e regex; nenhum campo livre chega a URL, path ou SQL;
- proteção natural contra injeção: nada do input vira caminho, cabeçalho ou SQL —
  além disso, o backend já valida com allowlist e há um WAF à frente;
- **nunca** repassar corpo HTML — o 403 do WAF inclui o IP do chamador e não pode
  chegar ao modelo;
- nada de host interno, DSN, nome de tabela interna ou SQL nas mensagens de erro;
- segredos apenas em variáveis server-side, fora do bundle do cliente;
- respostas pequenas, limitando exfiltração em massa;
- superfície mínima: 5 tools read-only com contrato fechado.

**Nota de prompt injection:** conteúdo textual vindo do mart (títulos de produto,
por exemplo) é **dado**, não instrução. Deve ser transportado como valor JSON,
sem interpolação em texto livre de instrução.

**Rate limiting — requisito normativo ainda não coberto.** A spec 2026-07-28
(§ Tools, *Security Considerations*) lista, entre as obrigações do servidor:

> "Servers **MUST**: Validate all tool inputs; Implement proper access controls;
> **Rate limit tool invocations**; Sanitize tool outputs."

Os três primeiros estão desenhados (§9, §13, §15). O quarto **não estava** e passa
a ser requisito de OM1. Desenho mínimo proposto, sem dependência nova:

- limite por token e por janela deslizante (sugestão inicial: 60 chamadas / 5 min),
  aplicado no handler antes do adapter;
- excedente → erro de execução de tool com `isError: true` e texto acionável
  ("limite de chamadas atingido, tente novamente em N s"), **não** erro de protocolo,
  conforme a distinção da própria spec;
- contador em memória do processo é aceitável no piloto — com Fluid Compute várias
  invocações compartilham instância, então o limite é aproximado, e isso deve ser
  declarado no runbook em vez de fingido como exato;
- o limite protege o backend do Render (que não tem rate limit próprio) tanto quanto
  o MCP, e é a única barreira contra varredura em massa via `granularidade='diaria'`
  em 366 dias repetidamente.

---

## 18. Testes

Matriz para OM1/OM2. **Nada disso foi executado nesta task.**

### 18.1 Protocolo

| # | Teste | Esperado |
|---|---|---|
| P1 | `initialize` / `server/discover` | responde conforme a spec 2026-07-28 |
| P2 | `tools/list` | exatamente **5** tools, ordem determinística |
| P3 | Cada tool presente com `name`/`title`/`description`/`inputSchema` | ✅ |
| P4 | Chamada válida por tool | `isError: false` + `structuredContent` |
| P5 | Input inválido (enum fora da lista) | erro de execução legível, `isError: true` |
| P6 | Método não suportado | erro JSON-RPC adequado |
| P7 | `GET /sse` e `POST /message` | **não são transporte ativo**. Na 2.1.1 os paths ficam desmontados e caem no 404 do Next.js; o `410 Gone` do CHANGELOG vale para a 2.0.0. Asserir "não estabelece sessão SSE", não um código fixo (ver §5.2) |
| P8 | Sessão (GET/DELETE) | 405 — stateless |
| P9 | Duas chamadas idênticas em conexões distintas | mesmo resultado (sem estado) |
| P10 | `structuredContent` + bloco `text` com o mesmo JSON | ambos presentes |

### 18.2 Segurança

> **S1–S3 pertencem à matriz FUTURA de autenticação (OM2).** No OM1 não há
> bearer nem OAuth: a negação é **404 genérico**, coberta pelos testes de
> fronteira (§25.6, §26.1). Não implemente S1–S3 antes de existir auth real.

| # | Teste | Esperado |
|---|---|---|
| S1 | Sem bearer *(futuro, OM2)* | **401** + `WWW-Authenticate` |
| S2 | Bearer inválido *(futuro, OM2)* | **401**, sem revelar se o token existe |
| S3 | Scope insuficiente *(futuro, OM2, se OAuth)* | **403** |
| S4 | Token em log ou mensagem de erro | **nunca aparece** |
| S5 | Path arbitrário via input | impossível — não há campo de path |
| S6 | `limite` acima do teto (ex.: 500) | rejeitado pelo Zod |
| S7 | Intervalo > 366 dias | rejeitado **antes** da chamada HTTP |
| S8 | `marcas: ["x' OR 1=1--"]` | valor inválido no enum, nunca sai requisição |
| S9 | Backend em erro | categoria legível, **sem** stack trace e **sem** HTML |
| S10 | Resposta > 256 KB | abortada, erro claro |
| S11 | Exceder o rate limit (§17) | erro de execução com `isError: true` e texto acionável, **não** erro de protocolo; backend do Render preservado |
| S12 | Varredura em massa (`granularidade='diaria'`, 366 dias, repetida) | barrada pelo rate limit antes de sobrecarregar o Render |

### 18.3 Dados — por tool

Para cada uma das 5 tools:

| # | Teste | Esperado |
|---|---|---|
| D1 | Período com dado real | valores conferem com o REST |
| D2 | Período verdadeiramente vazio (ex.: 2020) | `data` vazio + `warnings`, **não** erro |
| D3 | `null` preservado (ROAS sem ad spend; comissão ML) | `null`, **nunca** `0` |
| D4 | Zero real preservado (GMV zerado num dia sem venda) | `0`, **nunca** `null` |
| D5 | Período ecoado | bate com `date_from`/`date_to` do backend |
| D6 | Filtro de marca/canal aplicado | `filtros_aplicados` ecoa o backend |
| D7 | `limite` respeitado | `returned_count <= limit` |
| D8 | Proveniência completa | `fonte`, `camada`, `refreshed_at`, `definicao_metrica` |
| D9 | Período incluindo hoje | warning de dia parcial **presente** |
| D10 | `total_count` | verdadeiro em produtos; `null` nas demais |

### 18.4 Produção (somente após autorização — **não executar em OM0**)

Endpoint externo acessível; chamada sem token rejeitada; `tools/list`
autenticado; uma chamada real por tool; nenhum mock; nenhum host local; nenhum
CORS ou Deployment Protection mascarando a auth; payload pequeno; nenhuma PII.

---

## 19. Reconciliação

Objetivo: provar que o número do MCP é **o mesmo** que o da Torre — e explicar
toda diferença que restar.

| # | Reconciliação | Critério |
|---|---|---|
| R1 | `torre_desempenho_marketplaces` (jul/2026, all) × `GET /overview?ref_month=2026-07` | **idênticos** ao centavo |
| R2 | Mesma tool × tela Gerencial, mesmo filtro | idênticos; qualquer diferença é bug de projeção |
| R3 | Soma de `por_canal` × `total.gmv` | idênticos (mesma cláusula WHERE no backend) |
| R4 | Soma da `serie` × `total.gmv` | idênticos — contrato explícito de `/trend` |
| R5 | `torre_produtos_prioritarios` × `/produtos/*` | `total_count` = campo `total`; top-N na mesma ordem |
| R6 | `torre_regioes_vendas` × `/regioes/summary` | idênticos **entre si** |
| R7 | Regiões × Gerencial | **divergem por construção** (sub-cobertura). Documentar a diferença medida; **nunca** ajustar número para "bater" |
| R8 | Qualidade TikTok | cancelamento = 0 é **não medido**; a tool precisa dizê-lo |
| R9 | Financeiro ML | comissão `null`, jamais `0` |

**Princípio:** diferença conhecida é **documentada**, nunca escondida e nunca
"corrigida" no adapter. O MCP não é lugar de regra de negócio nova — se um
número precisa mudar, muda no mart.

Valores de referência medidos em 2026-08-18 (para conferência futura):
jul/2026 GMV total = **R$ 22.544.297,31**; 01–18/08/2026 = **R$ 11.512.531,49**.

---

## 20. Registro no Oráculo

> **Ressalva:** a skill do Oráculo **não foi localizada** nesta máquina (busca
> read-only em `~/.claude/skills`, `~/.claude/plugins` e `~/Desktop` — nenhum
> arquivo `*oracul*`). Nada foi editado. A proposta abaixo precisa ser conferida
> contra a skill real (decisão **D7**).

**Registrar somente após deploy + smoke autenticado.**

1. **Nome do conector:** `Torre Marketplace`
2. **URL:** `https://mktplace-gobeaute.vercel.app/api/mcp`
3. **Roteamento intenção → tool:**

| Intenção do usuário | Tool |
|---|---|
| "quanto vendemos", "GMV do mês", "como foi julho" | `torre_desempenho_marketplaces` |
| "qual canal é mais eficiente", "ROAS por marca" | `torre_comparar_canais_marcas` |
| "produtos que mais vendem", "curva ABC" | `torre_produtos_prioritarios` |
| "os dados estão atualizados?", "posso confiar?" | `torre_qualidade_dados` |
| "quais estados vendem mais" | `torre_regioes_vendas` |

4. **Bullet de propriedade:**

> **Torre Marketplace é fonte de:** GMV, pedidos, ticket médio, participação por
> canal (TikTok Shop, Mercado Livre, Shopee) e por marca (Ápice, Barbours,
> Kokeshi, Lescent, Rituária); eficiência de mídia (ROAS/ACOS); ranking de
> produtos; vendas por UF; e frescor dos próprios dados. Grão mínimo: dia ×
> marca × canal (e UF em Regiões).
>
> **Torre Marketplace NÃO é fonte de:** margem ou lucro (não há CMV); dados em
> tempo real ou intradiários; pedidos individuais, clientes ou creators;
> estoque; logística e prazos de transportadora; dados de e-commerce próprio
> fora dos três marketplaces; qualquer informação abaixo do grão de UF.

5. **Perguntas de avaliação de roteamento:**

| Pergunta | Tool esperada |
|---|---|
| "Quanto a Barbours vendeu no Mercado Livre em julho?" | `torre_desempenho_marketplaces` |
| "Qual marca tem o melhor ROAS no ML?" | `torre_comparar_canais_marcas` |
| "Top 10 produtos da Kokeshi no TikTok" | `torre_produtos_prioritarios` |
| "Esse número está atualizado?" | `torre_qualidade_dados` |
| "Vendemos mais em SP ou RJ?" | `torre_regioes_vendas` |
| "Qual foi o GMV de hoje até agora?" | `torre_qualidade_dados` **ou** recusa explicando que o dia corrente é parcial |
| "Qual a margem do produto X?" | **nenhuma** — deve responder que a Torre não tem CMV |

6. **Quando preferir outra fonte:**

- prazo de entrega, transportadora, frete, ocorrência logística → fonte de logística;
- dados de Google Ads fora dos marketplaces → fonte de marketing;
- cadastro/ficha de produto, imagens, atributos → fonte de cadastro;
- pedido individual ou atendimento a cliente → sistema transacional (**nunca** o MCP);
- qualquer coisa em tempo real → **nenhuma fonte disponível hoje**.

---

## 21. Riscos

| # | Risco | Sev. | Mitigação |
|---|---|---|---|
| **R1** | **API do Render pública** — proteger o MCP não a protege | **Alta** | Frente separada (D2). Registrar aceite formal se não for tratada |
| **R2** | **Bearer compartilhado sem identidade por pessoa** | **Alta** | Só com aprovação consciente; piloto restrito; rotação; migrar para OAuth |
| **R3** | **Dia corrente parcial lido como queda real** | **Alta** | Warning obrigatório no envelope; description da tool avisa |
| **R4** | PII de creators em `/operacoes` | **Alta** | Fora do MVP |
| **R5** | Frontend com fallback mock cria expectativa de "sempre responder" | Média | MCP **nunca** faz fallback: erro explícito |
| **R6** | Regiões não reconcilia com Gerencial | Média | Warning permanente; R7 do §19 |
| **R7** | Cancelamento TikTok = 0 lido como excelência operacional | Média | Warning em Qualidade; nunca apresentar 0 sem ressalva |
| **R8** | Comissão ML ausente subestima custo total | Média | `null` + aviso; jamais `0` |
| **R9** | Corpo HTML do WAF vazando para o modelo (inclui IP) | Média | Exigir `application/json`; descartar corpo |
| **R10** | Lista de limitações do §8.4 desatualizar | Média | Revisar a cada mudança do checkpoint de qualidade |
| **R11** | Data Mart continua inacessível ao Render | Média | Tempo real fora de escopo até a conectividade mudar |
| **R12** | `origin/main` avançar e conflitar | Baixa | Rebase antes de OM1; este documento é arquivo novo, sem sobreposição com o V3 |
| **R13** | Node < 20 na Vercel quebra o build | Baixa | Confirmar antes do deploy |
| **R14** | Custo/latência de cold start no Render | Baixa | Timeout de 8 s cobrindo resposta+corpo+parse; **zero retry** |
| **R15** | **Se a Deployment Protection for ligada, o único bypass viável (Automation) destrava o projeto inteiro, não só `/api/mcp`** | Média | §16.1. Decidir conscientemente; nunca tratar como detalhe de config. A auth da rota continua sendo a única camada específica |

---

## 22. Anti-escopo

**Não faz parte desta frente:**

- qualquer tool de escrita, em qualquer release;
- acesso direto a PostgreSQL, Neon ou RDS pelo MCP;
- proxy REST/SQL genérico ou execução de código;
- exposição de PII (compradores, creators, pedidos individuais);
- dados em tempo real ou intradiários;
- criação de **endpoints novos** no FastAPI — se uma tool precisar de endpoint
  novo, isso é **stop-loss** (§13 do handoff), não trabalho silencioso;
- alterações no frontend, nas telas ou no `api-client.ts` do browser;
- mudanças em `apps/`, `pipelines/`, `db/`, manifests ou lockfiles em OM0;
- alteração de `docs/PROJECT_STATUS.md` (pertence à instância V3);
- edição da skill do Oráculo antes de OM2;
- desativar Deployment Protection globalmente;
- qualquer deploy ou uso da Vercel CLI em OM0/OM1.

---

## 23. Roadmap OM0 → OM2

### OM0 — auditoria e blueprint *(esta task)*

Critérios de saída — todos atendidos:

- [x] fontes mapeadas e classificadas (§7)
- [x] catálogo MVP definido (§8)
- [x] envelope canônico (§11)
- [x] auth **decidível** — dois desenhos, requisitos e regra de decisão (§13)
- [x] plano de teste (§18) e reconciliação (§19)
- [x] riscos e bloqueios explícitos (§21)

**Entrega:** este documento. Nenhum código.

### OM1 — implementação local

**Status: Task 1/2 implementada (2026-08-18) e corrigida (2026-08-19).
Aguardando a Task 2/2 de QA local — OM1 NÃO está concluído.**
Registro de execução no §25; correções no §26 e §27.

**Como efetivamente ocorreu (correção factual):** o proprietário autorizou as
duas instâncias em **paralelo**. A implementação foi feita em **worktree
isolado** (`oracle-mcp`) diretamente sobre `origin/main` @ `309b6bf`, sem
esperar o V3-1A. A versão antiga deste plano listava "V3-1A versionado" e
"rebase" como pré-requisitos de OM1 — isso **não** foi o caminho seguido, e o
registro é ajustado aqui em vez de mantido como se tivesse sido cumprido.

**Estado da integração com o V3:**

- **V3-1A ainda NÃO está integrado nesta branch.**
- Antes do **versionamento final** do OM1 será **obrigatório**
  incorporar/rebasear sobre o commit final do V3-1A.
- `apps/web/package.json` e `apps/web/package-lock.json` exigirão **integração
  coordenada** (as duas instâncias alteram os mesmos arquivos — ver §26.10).
- Na base final integrada, a **suíte completa deverá ficar 100% verde**.

**Pré-requisitos remanescentes para versionar:**

1. revisão e aprovação deste documento;
2. conclusão da **Task 2/2** (QA local);
3. integração/rebase sobre o commit final do V3-1A;
4. suíte completa 100% verde nessa base.

> A decisão de auth (D1) **deixou de ser pré-requisito de OM1** após a decisão de
> 2026-08-18 (OAuth adiado). Ela passa a ser pré-requisito de **OM2**, porque o
> que ela governa é a *exposição*, não a escrita do código (§13.4.1).

**Escopo permitido:** **local / dev / test, read-only.** Dependências
(`mcp-handler@^2`, `@modelcontextprotocol/server@^2`, `zod@^4`); rota
`app/api/mcp/route.ts` com `runtime = "nodejs"`; adapter `torre-client`; as 5
tools; testes locais (auth **stub apenas em teste**); build; smoke com cliente
MCP local.

**Condições de segurança que valem já em OM1:**

- a rota nasce **fail-closed**: sem credencial válida configurada, nega;
- **desabilitada por padrão em produção** — habilitação exige auth real;
- nenhum stub de auth alcançável fora do ambiente de teste;
- nenhuma tool de escrita.

**Sem deploy. Sem alteração de configuração na Vercel. Sem publicação.**

### OM2 — publicação e registro

**Somente após aprovação de OM1 _e_ decisão de auth (D1) com autenticação real
implementada.** Este é o gate onde a auth passa a ser bloqueante — é aqui que
existe exposição.

Configuração segura da Vercel (variáveis server-side, Node ≥ 20); deploy; smoke
**autenticado**; reconciliação dos números (§19); conexão no Claude.ai;
atualização da skill do Oráculo (§20); avaliação de roteamento.

**Não autorizado neste gate:** Automation Bypass da Vercel como substituto de
auth (§16.1); publicação da rota sem credencial real; qualquer tool de escrita.

> A frente **não** será subdividida em OM0.1/OM0.2.

---

## 24. Critérios de aceite

### 24.1 De OM0 (esta entrega)

1. Toda superfície da Torre classificada com evidência de código **e** de runtime.
2. Toda afirmação sobre o stack MCP sustentada por fonte primária citada.
3. Divergência `extra.authInfo` × `ctx.http?.authInfo` **resolvida com evidência
   textual** (§5.3).
4. Catálogo de 4–6 tools, nenhuma genérica, nenhuma de escrita.
5. Envelope definido com regras de `null`, `total_count` e top-N.
6. Auth documentada nos dois desenhos, com veredito e requisitos faltantes.
7. Riscos, bloqueadores e decisões do proprietário explícitos.
8. Nenhum código, dependência, segredo, deploy ou alteração de configuração.
9. `docs/PROJECT_STATUS.md` intocado; nenhum arquivo do V3 tocado.

### 24.2 De OM1 (futuro)

Escopo de OM1 é **local/dev/test, read-only** — a validação abaixo roda em
`localhost`, nunca em produção.

`tools/list` devolve exatamente as 5 tools; toda tool valida input por Zod e
rejeita fora da allowlist; nenhum caminho HTTP derivável de input; **rate
limiting implementado conforme §17** (requisito normativo `MUST` da spec);
nenhum fallback mock; envelope completo em toda resposta; `null` e zero
preservados; build e typecheck limpos; testes do §18.1–18.3 passando
localmente.

**Critérios específicos de auth em OM1** (ver §13.4.1):

- a rota é **fail-closed**: contexto não autorizado ou desabilitado responde
  **404 genérico**, incluindo o caso de configuração ausente. Não existe 401,
  `WWW-Authenticate`, bearer ou OAuth no OM1;
- o stub de auth usado nos testes é **inalcançável** fora do ambiente de teste;
- a rota está **desabilitada por padrão em produção**, e habilitá-la exige auth
  real (não é alcançável por variável de ambiente isolada);
- nenhum caminho de código trata bypass de plataforma como autenticação.

### 24.3 De OM2 (futuro)

Endpoint autenticado acessível externamente; negação sem credencial; as 5 tools
respondendo com dado real; **R1–R6 do §19 reconciliados**; nenhuma PII; payloads
pequenos; skill do Oráculo atualizada; avaliação de roteamento aprovada.

---

## Apêndice A — Endpoints verificados em produção (2026-08-18)

Somente `GET` read-only, contra hosts públicos. Nenhum payload integral, nome
próprio, identificador individual ou credencial foi registrado.

| Endpoint | HTTP | Observação |
|---|---|---|
| `mktplace-gobeaute.vercel.app/` | 200 | sem autenticação |
| `mktplace-gobeaute.vercel.app/api/mcp` | 404 | ainda não existe |
| `/health` | 200 | — |
| `/api/v1/performance/health-datasource` | 200 | `neon_marts`, conectado |
| `/api/v1/performance/overview` | 200 | ~0,8 KB |
| `/api/v1/performance/brands` | 200 | ~1,9 KB |
| `/api/v1/performance/canais` | 200 | ~14 KB |
| `/api/v1/performance/financeiro` | 200 | ~4,4 KB |
| `/api/v1/performance/quality` | 200 | ~3,9 KB |
| `/api/v1/performance/trend` | 200 | ~2,3 KB |
| `/api/v1/performance/executive-summary` | 200 | ~5,3 KB |
| `/api/v1/performance/pedidos` | 200 | agregado, sem PII |
| `/api/v1/performance/monthly` | 200 | ~0,6 KB |
| `/api/v1/performance/daily` | 200 | ~1,4 KB |
| `/api/v1/performance/produtos/ml` | 200 | `total: 1648` |
| `/api/v1/performance/produtos/tiktok` | 200 | `total: 523` |
| `/api/v1/performance/produtos/shopee` | 200 | `total: 472` |
| `/api/v1/performance/produtos/ml/summary` | 200 | — |
| `/api/v1/performance/inteligencia` | 200 | ~29 KB |
| `/api/v1/performance/operacoes` | 200 | ~11 KB — **contém creators (PII)** |
| `/api/v1/performance/brand-detail` | 200 | TikTok-only |
| `/api/v1/performance/tempo-real` | **500** | Data Mart inacessível |
| `/api/v1/regioes/summary` | 200 | — |
| `/api/v1/regioes/by-uf` | 200 | ~10 KB |
| `/api/v1/regioes/by-brand` | 200 | ~3,3 KB |

Validações de borda observadas: intervalo > 366 dias → **422**; marca inválida →
**422** com allowlist; string de injeção → **403 HTML** do WAF.

## Apêndice B — Fontes primárias consultadas

| Fonte | Uso |
|---|---|
| `registry.npmjs.org/mcp-handler/latest` | versão 2.1.1, engines, peers |
| `registry.npmjs.org/@modelcontextprotocol/server/latest` | existência do pacote, `zod@^4.2.0`, node ≥ 20 |
| README de `vercel/mcp-handler@main` | `registerTool`, `ctx.http?.authInfo`, `withMcpAuth`, `protectedResourceHandler`, stateless |
| CHANGELOG de `vercel/mcp-handler` | mudanças 2.0.0–2.1.1, remoção de SSE/Redis, mudança do `authInfo` |
| Spec MCP 2026-07-28, seção Tools | `outputSchema`, `structuredContent`, fallback textual, nomes de tool, erros |
| `vercel.com/docs/deployment-protection` | métodos e escopos de proteção |
| `vercel.com/docs/deployment-protection/methods-to-bypass-deployment-protection` | escopo real de cada bypass — base do §16.1 |

---

## 25. OM1 Task 1/2 — implementação local (registro de execução)

> **Executado em 2026-08-18**, no worktree `oracle-mcp` sobre `origin/main` @
> `309b6bf`. **Sem deploy, sem publicação, sem segredo, sem commit.**

### 25.1 Veredito

O servidor MCP read-only está implementado, compila, e passa **107 testes
focais** cobrindo protocolo, segurança e semântica de dados. A rota existe em
`/api/mcp` e **nega em produção por construção**. OM2 não foi iniciado.

### 25.2 Divergência aprovada: SDK direto, sem `mcp-handler`

O §5 do blueprint (OM0) previa `mcp-handler@2.1.1` como wrapper. **Na
implementação isso se mostrou desnecessário**, e a decisão foi usar o SDK
oficial diretamente. Evidência coletada do pacote publicado:

- `@modelcontextprotocol/server@2.0.0` **já exporta** `createMcpHandler`,
  `WebStandardStreamableHTTPServerTransport`, `McpServer` e `InMemoryTransport`;
- `mcp-handler` declara esse mesmo pacote como peer **não opcional** — ou seja,
  seria uma camada a mais sobre a dependência que já precisaríamos ter;
- `createMcpHandler` devolve `{ fetch(request) => Promise<Response>, close() }`,
  que encaixa direto num Route Handler do App Router.

**Resultado:** uma dependência a menos, sem perda de capacidade. O blueprint foi
corrigido no §1 e aqui.

### 25.3 Versões efetivamente usadas

| Pacote | Versão | Tipo | Justificativa |
|---|---|---|---|
| `@modelcontextprotocol/server` | `2.0.0` (exata) | prod | SDK oficial, **estável** (`dist-tags.latest`, pós-beta). Traz transporte, servidor e handler HTTP. |
| `zod` | `4.4.3` (exata) | prod | Exigido pelo SDK (`^4.2.0`); `4.4.3` é o último estável v4. Schemas de entrada e saída. |
| `@modelcontextprotocol/client` | `2.0.0` (exata) | **dev** | Cliente MCP real para o teste de protocolo in-process. Não vai para o bundle. |

Ambiente: Node **v24.16.0** (SDK exige ≥ 20), npm 11.13.0, Next **15.5.19**.

**Lockfile:** 14 pacotes adicionados, **0 removidos**, **nenhuma versão
preexistente alterada**. As transitivas (`jose`, `pkce-challenge`, `eventsource`,
`cross-spawn`, `which`…) vêm majoritariamente do cliente de teste.

> **Stop-loss não disparado:** o SDK v2 **não** é prerelease. As versões
> `2.0.0-alpha.*` e `2.0.0-beta.*` existem no histórico, mas `latest` aponta para
> a `2.0.0` estável.

### 25.4 Arquitetura final

```
POST /api/mcp  (App Router, runtime = "nodejs", force-dynamic)
  └─ app/api/mcp/route.ts            casca fina: só lê process.env
       └─ handler.ts                 FAIL-CLOSED + createMcpHandler
            ├─ access.ts             decisão pura (env injetado)
            └─ server.ts             McpServer por requisição
                 ├─ rate-limit.ts    janela deslizante (spec: MUST)
                 └─ tools.ts         as 5 tools
                      ├─ schemas.ts       Zod v4 + resolução de período
                      ├─ envelope.ts      envelope canônico + fallback textual
                      ├─ limitations.ts   limitações curadas (ponto único)
                      ├─ errors.ts        categorias sanitizadas
                      └─ torre-client.ts  GET allowlisted -> FastAPI
```

**Decisão de desenho relevante:** a rota é uma casca; toda a lógica e a decisão
de acesso vivem em `handler.ts`, que **recebe o ambiente por parâmetro**. Isso
permitiu testar a matriz fail-closed de forma determinista, sem mutar
`process.env` entre testes — que na primeira tentativa produziu exatamente o
teste intermitente que se esperaria.

### 25.5 Achados de protocolo (2026-07-28)

Três comportamentos reais, verificados contra o SDK instalado, que **corrigem
suposições do blueprint**:

1. **Não existe handshake `initialize`.** Chamá-lo devolve `-32601 Method not
   found`. A era 2026-07-28 é stateless: cada requisição carrega o envelope
   `_meta` (`protocolVersion`, `clientInfo`, `clientCapabilities`) e a descoberta
   é feita por **`server/discover`**, que responde `supportedVersions` e
   `capabilities`.
2. **O header `Mcp-Method` é obrigatório** e precisa concordar com o `method` do
   corpo; a divergência é rejeitada com `-32020`.
3. **`GET` e `DELETE` respondem `405`** (operações de sessão do transporte 2025,
   inaplicáveis ao modo stateless).

Sobre `/sse`: o blueprint previa **410 Gone**, que era o comportamento do
`mcp-handler`. Com o SDK direto **nenhuma rota `/sse` ou `/message` foi criada** —
logo o Next devolve **404**. Isso é mais restritivo e satisfaz "nenhum transporte
SSE paralelo"; há teste garantindo que esses diretórios não existem.

### 25.6 Fronteira de acesso — matriz provada

| Ambiente | `ORACLE_MCP_ENABLED` | Backend | Stub | Resultado |
|---|---|---|---|---|
| `NODE_ENV=production` | — | ok | — | **404** (`production_disabled`) |
| `NODE_ENV=production` | `1` | ok | — | **404** — flag local não destrava produção |
| `NODE_ENV=production` | `1` | ok | presente | **404** — stub não destrava produção |
| `VERCEL_ENV=production` | `1` | ok | — | **404** |
| `development` | ausente | ok | — | **404** (`not_enabled`) |
| `development` | `1` | **ausente** | — | **404** (`missing_backend_config`) |
| `development` | `1` | ok | — | **permitido** |
| `development` | `1` | ok | presente | **404** (`stub_not_allowed`) |
| `test` | `1` | ok | presente | **permitido** (modo `test`) |

Garantias adicionais, cobertas por teste:

- a decisão ocorre **antes** de instanciar o MCP e **antes** de qualquer chamada
  HTTP — negação produz **zero** requisição upstream (provado com `fetch` espião);
- a resposta de negação é `404` com corpo genérico `{"error":"not_found"}`, que
  **não revela** se a rota está desabilitada, mal configurada ou protegida;
- nenhum nome de variável de ambiente, host ou configuração aparece na resposta;
- só `ORACLE_MCP_ENABLED=1` habilita (`"true"`, `"yes"`, `"0"`, `""` não);
- a base do backend precisa ser **https absoluta**.

> **404 em vez de 401** é deliberado: enquanto não existe autenticação real, um
> `401` anunciaria uma porta fechada. Quando houver auth, `401` com
> `WWW-Authenticate` passa a ser o correto para "credencial ausente".

### 25.7 Endpoints upstream por tool

Todos allowlisted como **constantes literais** no código; o modelo nunca fornece
path, host ou query.

| Tool | Endpoints |
|---|---|
| `torre_desempenho_marketplaces` | `/performance/overview`, `/performance/brands`, `/performance/trend` (só com granularidade) |
| `torre_comparar_canais_marcas` | `/performance/canais` |
| `torre_produtos_prioritarios` | `/performance/produtos/{ml,tiktok,shopee}` |
| `torre_qualidade_dados` | `/performance/health-datasource`, `/performance/overview`, `/performance/quality` |
| `torre_regioes_vendas` | `/regioes/summary`, `/regioes/by-uf` |

> **Correção ao handoff:** os endpoints de região são `/api/v1/regioes/*`, **não**
> `/api/v1/performance/regioes/*`. Confirmado em produção no OM0.

Superfícies fora do MVP (`/operacoes`, `/inteligencia`, `/brand-detail`,
`/tempo-real`, `/debug/*`) **não estão na allowlist** — há teste que falha se
alguma delas aparecer.

### 25.8 Matriz de testes — resultados

Suíte focal: **107 testes, 107 passando**, sem rede (todo upstream é `fetch`
falso; o relógio é injetado).

| Grupo | Arquivo | Testes | Cobre |
|---|---|---|---|
| Unidades | `oracle-units.test.ts` | 28 | período/fuso, validação, envelope, rate limit |
| Acesso | `oracle-access.test.ts` | 13 | matriz fail-closed completa |
| Segurança | `oracle-security.test.ts` | 17 | S5–S12, WAF, timeout, injeção, limites |
| Dados | `oracle-data.test.ts` | 28 | D1–D10, reconciliações R3/R4/R7, PII |
| Protocolo | `oracle-protocol.test.ts` | 5 | P2–P6, P9, P10 com cliente MCP real |
| Rota | `oracle-route.test.ts` | 16 | P1, P8, fail-closed no handler real, `/sse` |

Destaques do que está **provado**, e não apenas afirmado:

- **`null` nunca vira zero:** Shopee sem dado permanece `null`; comissão do ML
  permanece `null` com `applicable=true, available=false`.
- **Zero permanece zero:** marca com GMV `0` real não vira `null`.
- **TikTok:** cancelamento `0` é reportado como `measured: false` com
  `value_pct: null` — nunca "0% de cancelamento".
- **`estimated_margin` nunca aparece** na saída (nem o campo, nem o valor).
- **`total_count`** só existe onde o backend dá total verdadeiro (produtos:
  1648), com `truncated: true`; as demais tools devolvem `null`.
- **Regiões** sempre emite o aviso de cobertura, e o teste **exige** que o GMV
  regional **divirja** do gerencial — nenhuma reconciliação artificial.
- **`channels_sem_cobertura_regional`** é lido da fonte: mudando o payload, a
  saída muda (prova de que não é lista fixa).
- **Sanitização:** HTML do WAF, IP do solicitante, IP interno, porta de banco,
  host upstream, path interno e stack trace — nenhum aparece na resposta.
- **Rate limit:** excedente vira `isError` (não erro de protocolo) e **não toca o
  Render**.

### 25.9 Qualidade

| Verificação | Resultado |
|---|---|
| `npm run test:mcp` | **107/107** |
| `npm test` (suíte web completa) | **717/719** — 2 falhas **preexistentes**, ver abaixo |
| `npm run typecheck` | **limpo** |
| `npm run build` | **sucesso**; `/api/mcp` registrada como rota dinâmica (ƒ) |
| `git diff --check` | limpo |
| Trailing whitespace nos arquivos novos | 0 |
| Scan de segredos/DSN/e-mail/caminho pessoal | 0 ocorrências reais |

**As 2 falhas da suíte completa são anteriores a esta task** e não têm relação
com o MCP:

1. `F5. a documentacao usa contagem de drill-down nao ambigua` — lê
   `docs/PROJECT_STATUS.md`, que em `309b6bf` **não contém** o texto exigido
   (`dezesseis tipos de acionamento`: 0 ocorrências).
2. `Shopee isolada continua sem chamar fetchPedidos` — lê
   `apps/web/app/pedidos/page.tsx`.

Ambos os arquivos estão **byte-idênticos ao HEAD** (não foram tocados aqui), e
ambos são justamente arquivos em que a instância de QA/Revamp está trabalhando.
São falhas do baseline, não regressões.

**Vulnerabilidades:** `npm audit` reporta **4 high**, todas **preexistentes** —
`next` e `postcss` (diretas, versões inalteradas por esta task) e `nanoid` e
`sharp` (transitivas). **Nenhuma vulnerabilidade nova** foi introduzida pelos três
pacotes adicionados. Nenhuma correção automática foi executada.

### 25.10 Conflito futuro conhecido

`apps/web/package.json` foi alterado aqui (3 dependências + script `test:mcp` +
os 6 arquivos de teste no script `test`). A instância de QA/Revamp altera o
**mesmo arquivo** no worktree `ui-v3`. **Haverá conflito ao integrar com o
V3-1A.**

Resolução esperada, quando chegar a hora: manter **as duas** listas de
dependências e **concatenar** os arquivos no script `test`. Não é conflito
semântico — são adições disjuntas. **Não foi resolvido nesta task**, por decisão
explícita do handoff.

### 25.11 O que continua fora

- **Nenhuma publicação**: sem deploy, sem configuração na Vercel, sem CLI.
- **Nenhuma autenticação real**: OAuth segue adiado; nenhum bearer, PAT ou bypass
  foi criado. O único "stub" existente só é aceito sob `NODE_ENV=test`.
- **Nenhum Supabase**, nenhum banco, nenhum pipeline, nenhuma alteração no
  FastAPI, nenhum endpoint novo.
- **Nenhuma tool de escrita.**
- **OM2 não iniciado.**

---

## 26. OM1 — rodada de correção consolidada (pré-QA)

> **Executada em 2026-08-19**, no worktree `oracle-mcp` sobre `origin/main` @
> `309b6bf`. **Sem deploy, sem dependência nova, sem commit.**
> **A Task 2/2 e o OM2 continuam NÃO iniciados.**

Quatro findings materiais e dois endurecimentos, corrigidos em uma rodada.
Suíte focal passou de **107** para **163 testes**, todos verdes.

### 26.1 F1 — negar todo deployment, não apenas produção

**Problema:** `isProductionEnv` cobria só `NODE_ENV=production` e
`VERCEL_ENV=production`. Preview e custom environments recebem URL pública, e
ficariam habilitados se a flag local estivesse ligada — proteger Preview por
efeito colateral de `NODE_ENV` não é proteção.

**Correção:** `AccessEnv` ganhou `VERCEL`, `VERCEL_ENV` e `VERCEL_TARGET_ENV`, e
a decisão passou a separar três conceitos:

| Conceito | Função | Resultado |
|---|---|---|
| Produção | `isProductionEnv` — `NODE_ENV`, `VERCEL_ENV` ou `VERCEL_TARGET_ENV` = `production` | `production_disabled` |
| Hospedado | `isHostedEnv` — `VERCEL=1`, ou `VERCEL_ENV`/`VERCEL_TARGET_ENV` com qualquer valor | `hosted_deployment_disabled` |
| Local | nenhum sinal de deployment | permitido **com** habilitação explícita |

Matriz efetiva:

| Ambiente | Sinais | Resultado |
|---|---|---|
| Local (`npm run dev`) | nenhum + `ORACLE_MCP_ENABLED=1` | **permitido** |
| Produção | `NODE_ENV=production` | **404** `production_disabled` |
| Produção | `VERCEL_ENV=production` | **404** `production_disabled` |
| Custom publicado como produção | `VERCEL_TARGET_ENV=production` | **404** `production_disabled` |
| **Preview** | `VERCEL_ENV=preview` | **404** `hosted_deployment_disabled` |
| **Custom environment** | `VERCEL_TARGET_ENV=staging-qa` | **404** `hosted_deployment_disabled` |
| Qualquer execução na Vercel | `VERCEL=1` | **404** `hosted_deployment_disabled` |
| `vercel dev` | `VERCEL=1` / `VERCEL_ENV=development` | **404** `hosted_deployment_disabled` |

`currentEnv()` na rota foi atualizada para ler os três sinais novos.

> **Consequência aceita e documentada:** `vercel dev` deixa de funcionar para o
> piloto. O uso local é por `npm run dev`. Isso é preferível a manter uma brecha
> por conveniência.

### 26.2 F2 — payload malformado nunca vira sucesso vazio

**Problema:** `arrayOrEmpty()` e `String(row.brand ?? "")` convertiam contrato
quebrado em resposta válida vazia — indistinguível de "não houve venda".

**Correção:** novo módulo `upstream.ts` com contratos Zod mínimos por endpoint.
`arrayOrEmpty` e `asRecord` foram **removidos do código**.

| Endpoint | Obrigatório | Identidade que não pode ser vazia |
|---|---|---|
| `overview` | `current` (com `gmv`, `orders`) | — |
| `brands` | `brands` array | `brand` |
| `trend` | `data` array (quando solicitado) | `date` |
| `canais` | `channel_rows` e `channel_medians` arrays; **os 6 booleanos** de applicable/available | `brand`, `channel` |
| `produtos/ml` | `items` array, `total` inteiro ≥ 0 | `item_id` |
| `produtos/tiktok` | `items`, `total`, **`ref_month` YYYY-MM** | `product_id` |
| `produtos/shopee` | `items`, `total`, **`ref_month` YYYY-MM** | `sku_ref` ou `product_name` |
| `health-datasource` | `active_source` string, `db_connected` boolean | — |
| `quality` | `kpis` objeto | — |
| `regioes/summary` | estrutura esperada; `channels_sem_cobertura_regional` array quando presente | — |
| `regioes/by-uf` | `data` array | `uf` |

Semântica resultante:

| Situação | Resultado |
|---|---|
| Array **presente e vazio** | **sucesso vazio** (`returned_count: 0`) |
| Array **ausente** | `INVALID_UPSTREAM_RESPONSE` |
| Array com **tipo errado** | `INVALID_UPSTREAM_RESPONSE` |
| Linha **sem identificador** | `INVALID_UPSTREAM_RESPONSE` |
| Métrica opcional `null` | preservada como `null` |

Os booleanos de Canais entraram como obrigatórios porque **sem eles a distinção
entre "N/A" e "sem dado" se perde** — e essa distinção é a razão de a tool existir.

O detalhe do Zod **nunca** vai ao modelo: só a categoria sanitizada.

### 26.3 F3 — competência efetiva de Produtos

**Problema:** TikTok/Shopee usam o mês anterior quando `mes` é omitido, mas o
envelope dizia `ref_month: null` e `period: null` — a resposta descrevia um
período sem dizer qual.

**Correção:**

- `ref_month` é **obrigatório** no contrato de TikTok/Shopee;
- a competência usada é a **devolvida pela fonte**, nunca o eco do input;
- se o usuário pediu um mês e a fonte respondeu outro → `INVALID_UPSTREAM_RESPONSE`
  (a resposta não descreveria o que foi pedido);
- `meta.period` recebe primeiro e último dia dessa competência, `inclusive: true`;
- `filters_applied.ref_month` recebe a competência real;
- `data.ref_month` explicita a competência no corpo;
- **ML permanece cumulativo**: `period: null`, `ref_month: null`,
  `temporal_scope: "cumulativo"`, com a limitação declarada. Nada é inventado.

`monthBoundsOf()` deriva os limites via `Date.UTC(y, m, 0)`, o que resolve
fevereiro comum e bissexto sem tabela própria (testado: `2024-02` → 29,
`2026-02` → 28).

### 26.4 F4 — timeout cobrindo o corpo inteiro

**Problema:** `clearTimeout` acontecia assim que `fetch()` devolvia os headers;
a leitura do corpo ficava sem deadline. Um corpo pendurado penduraria a
invocação.

**Correção:**

- **um** `AbortController` cobre resposta + leitura do corpo + parse;
- `clearTimeout` roda **exatamente uma vez**, no `finally` externo, após o parse;
- cada `reader.read()` (e o `res.text()` do fallback) corre contra o abort via
  `withDeadline()`;
- abort durante a leitura → **`SOURCE_TIMEOUT`**;
- erro não relacionado a abort → `SOURCE_UNAVAILABLE` ou
  `INVALID_UPSTREAM_RESPONSE`, conforme o caso;
- teto de bytes, cancelamento do reader, zero retry e zero vazamento de mensagem
  preservados;
- o fallback sem stream passou a medir **bytes UTF-8** (`TextEncoder`), não
  caracteres.

Prova: fixture entrega headers na hora e deixa o corpo pendente; com
`timeoutMs: 60` a chamada termina em `SOURCE_TIMEOUT` em **menos de 2 s** (não
espera os 8 s do default), e `process.getActiveResourcesInfo()` mostra que
nenhum timer vazou.

### 26.5 F5 — parâmetro interno desconhecido falha

**Problema:** `TorreClient.get()` descartava em silêncio chaves fora de
`ALLOWED_PARAMS`. Um typo como `brandss` removeria o filtro de marca e
**ampliaria** a consulta sem ninguém perceber.

**Correção:** chave desconhecida lança `MISSING_CONFIGURATION` **antes do fetch**
(zero chamada upstream). A checagem da allowlist acontece **antes** do descarte
de `undefined` — um typo cujo valor por acaso seja `undefined` também falha, para
o erro não ficar latente até o dia em que aquele parâmetro passa a ter valor.

A validação da base URL também endureceu (`isUsableBackendUrl`, agora
compartilhada com a fronteira de acesso). Passou a rejeitar:

| Caso | Motivo |
|---|---|
| `http://…` | só https |
| `https://user:pass@host` | credencial embutida na URL |
| `https://host/api/v1` | path significativo (seria ignorado adiante) |
| `https://host/?x=1` | querystring |
| `https://host/#frag` | fragment |

Aceita apenas origem limpa. **Na correção terminal (§27.1) foi adicionada a
allowlist EXATA de hostname**, que faltava nesta rodada: hoje a validação exige
`mktplace-api.onrender.com` exatamente, com porta padrão.

### 26.6 F6 — janelas compostas da tool de qualidade

**Problema:** a tool consulta o **mês corrente** para frescor e o **mês fechado
anterior** para indicadores, mas o envelope tinha um único `meta.period` — o que
sugeria que tudo pertencia à mesma janela.

**Correção:**

- `meta.period` = **`null`**: não existe janela única nesta resposta;
- `data.freshness.checked_period` — janela do frescor (mês corrente);
- `data.quality_indicators_checked_period` — janela dos indicadores (mês fechado);
- `filters_applied` declara as duas com **nomes distintos**
  (`freshness_period`, `quality_indicators_period`);
- `refreshed_at` continua sendo o carimbo da carga, **nunca** a competência dos
  indicadores;
- `current_day_partial`, TikTok não mensurado, `null` ≠ zero e saúde técnica sem
  período inventado — todos preservados;
- a `description` da tool avisa explicitamente que a resposta é composta.

Testado inclusive na virada de ano: em 15/01/2026 o frescor é `2026-01-01..15` e
os indicadores são `2025-12-01..31`.

### 26.7 Endurecimento — duplicatas em filtros são rejeitadas

`canais`, `marcas` e `ufs` passaram a **rejeitar** valores duplicados em vez de
normalizar em silêncio. `["ml","ml"]` quase sempre indica chamada malformada;
deduplicar sem avisar esconderia o defeito de quem chamou. Nenhuma requisição
upstream sai, e o resultado não tem linha duplicada.

### 26.8 Revisão adversarial — as seis perguntas

| Pergunta | Resposta |
|---|---|
| Payload estrutural inválido ainda vira sucesso vazio? | **Não.** `arrayOrEmpty`/`asRecord` removidos; contratos obrigam arrays e identidades. |
| Competência efetiva continua aparecendo como `null`? | **Não** — exceto no ML, onde `null` é o fato correto (base cumulativa). |
| Algum deployment Vercel ≠ Production habilita a rota? | **Não** — `VERCEL=1`, `VERCEL_ENV` e `VERCEL_TARGET_ENV` cobrem Preview e custom. |
| Algum corpo continua lendo após o timeout? | **Não** — cada leitura corre contra o abort; reader é cancelado; timer limpo uma vez. |
| Typo interno remove filtro em silêncio? | **Não** — falha antes do fetch, inclusive com valor `undefined`. |
| Resposta composta afirma janela única? | **Não** — Qualidade usa `period: null` e nomeia as duas janelas. |

**Duas frestas foram encontradas nesta revisão e fechadas na mesma rodada:**
a ordem allowlist × `undefined` (§26.5) e `filters` em forma de array, que agora
é contrato quebrado, com guarda extra em `echoFilters` como defesa em profundidade.

### 26.9 Testes e qualidade

| Verificação | Resultado |
|---|---|
| `npm run test:mcp` | **163/163** (era 107) |
| `npm test` (suíte completa) | **772/774** — as **mesmas 2** falhas preexistentes |
| `npm run typecheck` | limpo |
| `npm run build` | sucesso; `/api/mcp` como rota dinâmica (ƒ) |
| `git diff --check` | limpo |
| Timers/recursos pendurados | nenhum (`getActiveResourcesInfo`) |
| Scan de segredos/PII/caminhos | 0 ocorrências reais |
| Dependências novas **nesta rodada** | **nenhuma** (as dependências MCP/Zod foram introduzidas pelo OM1 como um todo — ver §25.3) |
| `package-lock.json` | **não alterado nesta rodada** |

Novo arquivo de teste: `tests/oracle-findings.test.ts` (**56 testes**), registrado
em `test` e `test:mcp`.

As 2 falhas da suíte completa seguem sendo `F5. a documentacao usa contagem de
drill-down nao ambigua` e `Shopee isolada continua sem chamar fetchPedidos` — os
mesmos nomes de antes, lendo arquivos **byte-idênticos ao HEAD**
(`docs/PROJECT_STATUS.md` e `apps/web/app/pedidos/page.tsx`), nenhum deles tocado
aqui. **Não são regressão do OM1** e o conflito com o V3 não foi resolvido nesta
task.

### 26.10 Risco remanescente

A detecção de "hospedado" usa os **sinais oficiais da Vercel**. Um deployment em
outro provedor, rodando com `NODE_ENV=development` **e** `ORACLE_MCP_ENABLED=1`
explicitamente definido, não seria detectado como hospedado. Hoje isso não existe
no projeto (o frontend está na Vercel), e exigiria alguém definir a flag de
piloto num servidor — mas está registrado como limite conhecido do desenho, não
como cobertura total.

---

## 27. OM1 Task 1/2 — correção terminal pré-QA

> **Executada em 2026-08-19.** Veredito da revisão externa anterior:
> `REQUEST CHANGES`. Dois findings técnicos materiais e um conjunto de
> inconsistências factuais no plano. Escopo estrito, sem deploy, sem commit.
>
> **A Task 2/2 continua NÃO autorizada. OM1 NÃO está concluído.**

Suíte focal MCP: **168 testes, 168 verdes** (era 163).

### 27.1 F1 — hostname do backend agora é allowlisted

**Problema confirmado:** `isUsableBackendUrl` exigia HTTPS, ausência de
credencial e ausência de path/query/fragment, mas **aceitava qualquer hostname
HTTPS**. Isso contradizia o próprio §15 ("pertencer ao host allowlisted") e
permitia que uma configuração errada apontasse o servidor para qualquer origem.

**Correção:** constante server-only, fechada e testável:

```ts
export const ALLOWED_BACKEND_HOSTNAME = "mktplace-api.onrender.com";
```

Contrato final da validação — **todas** as condições são obrigatórias:

| Condição | Regra |
|---|---|
| Protocolo | `protocol === "https:"` |
| Hostname | **igualdade exata** com a constante |
| Porta | `port === ""` (apenas a porta padrão de HTTPS) |
| Credencial | `username` e `password` vazios |
| Path | apenas raiz (`"/"` ou vazio) |
| Query | `search === ""` |
| Fragment | `hash === ""` |

**Nenhum `endsWith`.** A correspondência é por igualdade, o que fecha a família
de ataques em que o host canônico aparece como prefixo ou sufixo.

Matriz verificada:

| URL | Resultado |
|---|---|
| `https://mktplace-api.onrender.com` | ✅ aceita |
| `https://mktplace-api.onrender.com/` | ✅ aceita |
| `https://mktplace-api.onrender.com:443/` | ✅ aceita (porta padrão é normalizada para vazio) |
| `https://MKTPLACE-API.ONRENDER.COM/` | ✅ aceita (DNS é case-insensitive; a `URL` normaliza) |
| `https://mktplace-api.onrender.com.evil.invalid` | ❌ sufixo malicioso |
| `https://sub.mktplace-api.onrender.com` | ❌ subdomínio prefixado |
| `https://mktplace-api.onrender.com.br` | ❌ sufixo adicional |
| `https://evil-mktplace-api.onrender.com` | ❌ prefixo colado |
| `https://mktplace-api.onrender.com.` | ❌ trailing dot |
| `https://qualquer-coisa.onrender.com` | ❌ passaria num `endsWith` ingênuo |
| `https://mktplace-api-fake.onrender.com` | ❌ idem |
| `https://onrender.com` | ❌ domínio pai |
| `https://outro-host.onrender.com` | ❌ outro host |
| `https://example.com` | ❌ host arbitrário |
| `https://mktplace-api.onrender.com:8443` | ❌ porta customizada |
| `https://user:pass@mktplace-api.onrender.com` | ❌ credencial embutida |
| `http://mktplace-api.onrender.com` | ❌ HTTP |
| `https://mktplace-api.onrender.com/api/v1` | ❌ path |
| `https://mktplace-api.onrender.com/?x=1` | ❌ query |
| `https://mktplace-api.onrender.com/#frag` | ❌ fragment |
| `/api/v1`, `""`, `"   "`, ausente, `javascript:` | ❌ forma inválida |

Consequências:

- o **modelo nunca escolhe host nem path** — o host é constante e o path é
  literal no código;
- configuração divergente **falha fechada**: `MCP_BACKEND_API_URL` apontando
  para outro host devolve `missing_backend_config` na fronteira, e a rota
  responde 404;
- a mensagem de erro permanece **sanitizada**: não revela qual host era esperado
  nem qual foi recebido;
- **nenhuma segunda variável de ambiente** foi introduzida — o backend é único e
  conhecido, e uma variável de allowlist só moveria o problema.

Os testes migraram de `backend.invalid` para o hostname canônico com `fetchImpl`
mockado. **Nenhum teste toca a rede real.**

### 27.2 F2 — o cleanup do reader não prolonga mais o deadline

**Problema confirmado:** o deadline abortava `reader.read()`, mas o `finally`
executava `await reader.cancel()` **sem deadline**. Um stream ou adaptador cujo
`cancel()` nunca resolvesse manteria a tool pendurada **depois** de o
`SOURCE_TIMEOUT` já ter sido decidido — anulando o próprio timeout.

**Correção:** o cancelamento passou a ser disparado sem ser aguardado.

```ts
void reader.cancel().catch(() => {
  /* best-effort: o objetivo e' apenas nao vazar o stream */
});
```

Preservado: um único deadline para headers + corpo + parse; cancelamento do fetch
pelo `AbortController`; teto de 256 KB em bytes UTF-8; zero retry; nenhum
timer/recurso pendurado; erro sanitizado.

**Prova (pior caso):** fixture entrega headers imediatamente, `read()` nunca
resolve **e** `cancel()` também nunca resolve. Com `timeoutMs: 50` a chamada
termina em `SOURCE_TIMEOUT` em **~58 ms** (limite do teste: 1500 ms), e o
contador confirma que o cancelamento **foi iniciado** — apenas não aguardado.

**Caso normal:** um segundo teste confirma que o caminho feliz continua liberando
o reader (`cancel()` chamado uma vez) **sem nenhuma `unhandledRejection`** — o
teste instala um listener de `unhandledRejection` e falha se algo escapar.

Um terceiro teste confirma, via `process.getActiveResourcesInfo()`, que nenhum
timer sobra depois do cancelamento hostil.

### 27.3 Correções factuais no plano

| # | Estava | Passou a ser |
|---|---|---|
| **A** | "OM0 e OM1 concluídos" **e** "Task 2/2 não iniciada" (contradição) | "OM0 concluído; **OM1 Task 1/2 implementada e corrigida, aguardando Task 2/2 de QA local**". OM1 só é declarado concluído após a Task 2/2 |
| **B** | "V3-1A versionado" e "rebase" como pré-requisitos de OM1 | registro factual: instâncias autorizadas **em paralelo**, implementação em worktree isolado sobre `309b6bf`; **V3-1A ainda não integrado**; integração/rebase e suíte 100% verde passam a ser pré-requisitos do **versionamento final** |
| **C** | aceite "sem credencial válida responde **401**" | **404 genérico** para contexto não autorizado/desabilitado; nenhuma informação sobre rota ou configuração; sem 401, `WWW-Authenticate`, bearer ou OAuth no OM1 |
| **D** | §15: "Retry: **no máximo 1**, com backoff de 500 ms" | **zero retry**; falha explícita e sanitizada; quem decide repetir é o modelo/cliente; nenhum backoff interno. R14 ajustado também |
| **E** | §15 dizia "host allowlisted" sem implementação; §26.5 afirmava "nenhuma allowlist fixa de hostname foi criada" | §15 descreve o contrato real (hostname exato, porta padrão, root); a afirmação de §26.5 foi **removida** e aponta para §27.1 |
| **F** | "zero dependência nova" de forma genérica | distinção explícita: **o OM1 como um todo** introduziu `@modelcontextprotocol/server`, `zod` e `@modelcontextprotocol/client` (§25.3); **esta rodada terminal** não introduziu nenhuma |

Os itens de 401 que **permanecem** no documento são apenas os do **desenho
futuro de OAuth** (§13.2/§13.3) e da matriz de testes de OM2 — descrevem o que
existirá *quando* houver autenticação real, e estão marcados como tal. O
comportamento implementado hoje é 404, documentado em §25.6 e §27.

### 27.4 As duas falhas da suíte completa — classificação

A suíte completa executa **780 testes**, com **778 passando** e **2 falhas**:

1. `F5. a documentacao usa contagem de drill-down nao ambigua` — lê
   `docs/PROJECT_STATUS.md` e `docs/GERENCIAL_V2_SPEC.md`;
2. `Shopee isolada continua sem chamar fetchPedidos (branch de skip permanece
   antes do fetch real)` — lê `apps/web/app/pedidos/page.tsx`.

Classificação:

- os arquivos que originam as falhas são **byte-idênticos à base** `309b6bf` e
  **não foram alterados pelo OM1**;
- portanto **não são regressão do OM1**, e **não devem ser corrigidos nesta
  branch** — são justamente arquivos em que a instância de QA/Revamp trabalha;
- **zero** falhas em testes `oracle-*`.

Redação correta, a ser usada em qualquer relatório:

> **Suíte completa executada com 2 falhas externas/preexistentes; conjunto MCP
> integralmente verde. A integração final ainda exige suíte completa 100%
> verde.**

Não é correto dizer "778/780 = suíte completa aprovada". A **Task 1/2 pode ser
avaliada pelas suítes MCP**; o **versionamento final** fica condicionado ao
rebase sobre o V3-1A e à suíte integral 100% verde depois da integração.

### 27.5 Revisão adversarial

| Pergunta | Resposta |
|---|---|
| 1. URL HTTPS para qualquer host ainda é aceita? | **Não.** Igualdade exata de hostname. |
| 2. Hostname parecido com o canônico passa? | **Não.** Prefixo, sufixo, subdomínio, `.evil.invalid` e trailing dot rejeitados. |
| 3. Porta customizada passa? | **Não.** `port !== ""` rejeita; `:443` é normalizada para vazio. |
| 4. `cancel()` que nunca resolve ainda segura a tool? | **Não.** Disparado com `void` + `.catch()`; provado em ~58 ms. |
| 5. Alguma seção afirma 401 sem existir autenticação? | **Não.** Os 401 restantes são do desenho futuro de OAuth e da matriz de OM2, marcados como tal. |
| 6. Alguma seção promete retry? | **Não.** §15 e R14 dizem zero retry. |
| 7. Alguma seção chama OM1 de concluído antes da Task 2/2? | **Não.** Cabeçalho, §23, §25 e §26 dizem "Task 1/2 … aguardando Task 2/2". |
| 8. Alguma seção afirma que V3-1A já foi integrado? | **Não.** §23 registra explicitamente que **não** está integrado. |
| 9. O documento distingue dependências do OM1 das desta rodada? | **Sim.** §25.3 (OM1) × §26.9/§27 (esta rodada: nenhuma). |
| 10. Alguma correção tocou V3, backend, banco ou pipeline? | **Não.** Apenas `access.ts`, `torre-client.ts`, testes `oracle-*`, fixtures e este documento. |

### 27.6 Arquivos alterados nesta rodada

| Arquivo | Mudança |
|---|---|
| `apps/web/src/server/oracle/access.ts` | `ALLOWED_BACKEND_HOSTNAME` + validação exata de host/porta |
| `apps/web/src/server/oracle/torre-client.ts` | cleanup do reader sem `await` |
| `apps/web/tests/oracle-access.test.ts` | matriz de URLs (aceitas/rejeitadas), falha fechada |
| `apps/web/tests/oracle-findings.test.ts` | 3 testes de `cancel()` hostil |
| `apps/web/tests/oracle-fixtures.ts` | `hangingCancelFetch`, `countingCancelFetch` |
| `apps/web/tests/oracle-{data,protocol,route,security}.test.ts` | migração para o hostname canônico |
| `docs/ORACLE_MCP_PLAN.md` | correções A–F e esta seção |

`package.json` e `package-lock.json` **não receberam alteração nova**: nenhum
arquivo de teste novo foi criado (os casos entraram em arquivos já registrados) e
nenhuma dependência mudou.

---

## 28. OM1 Task 2/2 — integração com o V3-1A, QA local e fechamento

> **Executada em 2026-08-19.** Integração sobre `origin/main` @ `e675948`
> (V3-1A), suíte completa verde, QA MCP local real contra o backend público em
> somente leitura. **OM1 concluído. OM2 não iniciado. Nenhum deploy.**

### 28.1 Integração

O commit local do OM1 (23 arquivos) foi rebaseado sobre `e675948`. Resultado:
**exatamente um commit à frente** de `origin/main`, zero atrás.

Conflito único e esperado: `apps/web/package.json`. Resolvido por **união
programática** (script fora do repositório, com checagens duras antes de
escrever), não à mão:

| Item | Resultado |
|---|---|
| Testes do V3 preservados | **42** (inclui os 4 novos do V3-1A: `a11y-target-44`, `inteligencia-v3-wiring`, `inteligencia-v3`, `produtos-url`) |
| Testes do Oracle preservados | **7** |
| Total no script `test` | **49**, sem duplicatas |
| Script `test:mcp` | os mesmos 7 arquivos Oracle |
| `oracle-fixtures.ts` | **fora** dos scripts (é helper, não executável) |
| Dependências | união sem alterar **nenhuma** versão existente |
| JSON | válido |

Os 21 arquivos exclusivos do OM1 saíram do rebase com **hash idêntico** ao de
antes da integração (verificado com `git hash-object` contra o inventário
pré-integração). Nenhum arquivo funcional do V3 foi modificado — o diff do
commit contra `origin/main` contém apenas `package.json`, `package-lock.json` e
os 21 arquivos novos do OM1.

**Lockfile:** `npm install --package-lock-only` não produziu diferença de
conteúdo (hash idêntico antes e depois), ou seja, o lockfile do OM1 já
representava exatamente o `package.json` integrado. `npm ci` reinstalou a árvore
sem erro.

### 28.2 Uma correção necessária num teste do V3 — declarada

A suíte integrada acusou **uma** falha, que não existia em nenhum dos dois gates
isolados: `32. nenhuma dependencia nova no package.json`, em
`tests/inteligencia-v3-wiring.test.ts`.

O teste faz `deepEqual` sobre o conjunto **inteiro** de `dependencies` e a sua
própria mensagem diz *"o conjunto de dependencias de runtime nao pode mudar **no
V3-1A**"*. É uma guarda **escopada ao gate do V3** — correta sobre o V3-1A e
factualmente desatualizada sobre a árvore integrada, porque o OM1 adiciona duas
dependências de runtime legítimas.

Não havia como satisfazer os dois lados sem editá-lo:
`@modelcontextprotocol/server` e `zod` são dependências de **runtime** da rota
server-side; movê-las para `devDependencies` quebraria o build da Vercel.

**Correção aplicada — a guarda não foi enfraquecida:**

- continua sendo `deepEqual` sobre uma allowlist **exata** (qualquer sétimo
  pacote ainda quebra o teste);
- a lista passou de 4 para 6 nomes, com comentário registrando **qual gate**
  introduziu cada adição e por que ela é de runtime;
- a mensagem de asserção passou a dizer "só muda por gate declarado (V3-1A:
  nenhuma; OM1: server+zod)";
- a guarda do `xlsx` foi mantida intacta.

> **Este é o único arquivo do V3 tocado nesta task**, e merece revisão do
> proprietário do V3-1A. Nenhum contrato de comportamento do V3 foi alterado;
> nenhum teste foi removido da lista.

### 28.3 Validação integrada

| Verificação | Resultado |
|---|---|
| `npm run test:mcp` | **168/168** |
| `npm test` (suíte completa) | **875/875 — 100% verde** |
| `npm run typecheck` | limpo |
| `npm run build` | sucesso; **`/api/mcp` como `ƒ` (dinâmica, server-side)** |
| `git diff --check` | limpo |

As duas falhas que persistiam na base anterior (`F5. contagem de drill-down` e
`Shopee isolada … fetchPedidos`) **desapareceram** com a integração do V3-1A,
exatamente como previsto: elas liam arquivos que o V3-1A corrigiu.

### 28.4 QA MCP local real — 52/52 PASS

Servidor local em `next dev`, porta livre, variáveis **apenas no processo**
(`ORACLE_MCP_ENABLED=1`, `MCP_BACKEND_API_URL` para o host canônico) — **`.env`
não foi editado**. Cliente MCP **oficial** (`@modelcontextprotocol/client`) por
**Streamable HTTP** real, não chamada direta de função. Harness executado
**fora** do repositório: nenhum artefato de QA foi versionado.

**E1 — descoberta:** `tools/list` devolveu **exatamente as 5** tools esperadas,
todas com `description` substantiva e `inputSchema` do tipo objeto; nenhuma
sexta tool; nenhum verbo de escrita no catálogo.

**E2 — caminho felizcontra o backend público:**

| Tool | período | `refreshed_at` | retornados | `total_count` | tamanho |
|---|---|---|---|---|---|
| `torre_desempenho_marketplaces` | 2026-07-01..31 | presente | 8 | `null` | 1,4 KB |
| `torre_comparar_canais_marcas` | 2026-07-01..31 | presente | 14 | `null` | 9,9 KB |
| `torre_produtos_prioritarios` | `null` (ML cumulativo) | presente | 5 | **1648** | 2,4 KB |
| `torre_qualidade_dados` | `null` (composta) | presente | 4 | `null` | 2,4 KB |
| `torre_regioes_vendas` | 2026-07-01..31 | presente | 5 | `null` | 2,0 KB |

Em todas: envelope canônico completo, `BRL`/`reais`/`America/Sao_Paulo`,
definição da métrica, **zero PII**, **zero `estimated_margin`**, payload pequeno.
`total_count` verdadeiro só em Produtos; `null` nas demais. TikTok chegou com
`measured: false` e `value_pct: null` — **não** como "0% de cancelamento".
Qualidade veio com `meta.period: null` e as duas janelas nomeadas. Regiões emitiu
o aviso de cobertura parcial.

**E3 — reconciliação REST × MCP (leitura direta do backend, sem imprimir payload):**

| Comparação | Resultado |
|---|---|
| GMV de `/overview` × `data.total.gmv` | **idêntico** |
| Pedidos | **idêntico** |
| `refreshed_at` | **idêntico** |
| Soma dos canais × total | **idêntica** |
| Linhas marca × canal de `/canais` | **14 = 14** |
| ML: custo de marketplace e ROAS | **idênticos** (custo `null`, ROAS 11,29) |
| `total` de `/produtos/ml` × `meta.total_count` | **1648 = 1648** |
| Identidade e ordenação dos 5 primeiros produtos | **preservadas** |
| Receita item a item | **idêntica** |
| GMV de `/regioes/summary` × `data.summary.gmv` | **idêntico** |
| `active_source` de `/health-datasource` | **idêntico** (`neon_marts`) |

**Divergência zero** em tudo que deveria bater. A única diferença é a
**esperada e contratual**: o GMV regional **não** é igual ao gerencial — e o QA
**exige** essa divergência, provando que nenhum ajuste artificial é aplicado. Os
canais sem cobertura regional vieram **da fonte** (`["tiktok"]`), não de lista
fixa.

**E4 — validação e segurança:** os **7** inputs inválidos testados (canal fora
da allowlist, marca fora da allowlist, intervalo > 366 dias, duplicata,
limite acima do teto, parâmetro desconhecido, string de injeção) foram
**todos rejeitados**. Nenhuma resposta de erro vazou host, path, DSN, token ou
stack trace. Nenhum `inputSchema` expõe campo de URL/path/host/query/SQL.

**E5 — timeout e recursos** (suíte dedicada, 87 testes verdes): corpo pendurado
após headers → `SOURCE_TIMEOUT`; `cancel()` que nunca resolve **não** segura a
chamada (~60 ms com deadline de 50 ms); teto em bytes UTF-8 no streaming e no
fallback; zero timer pendente; zero `unhandledRejection`; zero retry/backoff.

**E6 — fail-closed:** produção por `NODE_ENV`, `VERCEL_ENV` e
`VERCEL_TARGET_ENV`; Preview; custom environment; `VERCEL=1` — **todos 404**, e
a flag local **nunca** destrava ambiente hospedado. Host divergente → 404
`missing_backend_config`, sem revelar o host esperado.

**E7 — operação:** console limpo; zero `unhandledRejection`; nenhum host externo
inesperado (apenas o loopback/LAN do próprio dev server e o backend canônico);
servidor encerrado e portas liberadas ao final; **nenhum artefato de QA dentro do
repositório**.

### 28.5 Auditoria de dependências

`npm audit --omit=dev`: **4 high, 0 critical** — `next` (direta) e `nanoid`,
`postcss`, `sharp` (transitivas, todas pela árvore do `next`).

**Nenhuma é atribuível ao OM1.** Verificado explicitamente: `npm ls` dos três
pacotes transitivos não mostra nenhum caminho por `@modelcontextprotocol/*` nem
`zod`, e a árvore MCP é mínima (`server` → `core` + `zod`; `client` → `core` +
`zod`, deduplicados). O audit incluindo dev reporta os **mesmos 4** — o cliente
de teste também não adiciona nada.

Todas são **baseline**, herdadas do `next`/`postcss` que o V3 já usava, com as
versões inalteradas por esta frente. Nenhuma correção automática foi executada.

### 28.6 O que continua fora

- **Nenhum deploy foi feito por esta task.** Publicar o commit em `main` faz a
  Vercel construir o código, e a rota `/api/mcp` passa a **existir** — mas
  responde **404 por construção** em Production, Preview e custom environments,
  porque não há autenticação real. Isso é comportamento desenhado e testado, não
  efeito colateral.
- **`/api/mcp` não está habilitado na Vercel.** Nenhuma variável de ambiente foi
  criada lá, e `ORACLE_MCP_ENABLED` não existe naquele ambiente.
- **O Claude.ai não foi conectado.** Nenhum conector externo foi registrado, e a
  skill do Oráculo não foi alterada.
- **OAuth segue adiado.** **Bearer compartilhado não foi implementado.**
- **OM2 não iniciado.**
- Zero alteração em `apps/api`, pipelines, banco, Supabase ou `.env`.

**Requisito para publicar de verdade:** autenticação real decidida e
implementada (§13.4.1). Nenhum bypass da plataforma conta como autenticação
(§16.1).
