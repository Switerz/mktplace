# Gate V3 — Inteligência e Marca 360 (desenho)

**Status:** **V3-0, V3-1A, PF1, V3-BE e V3-1B VERSIONADOS** (`309b6bf`,
`e675948`, `45fa3f8`, `26434c8`, `2d7ecdf`), com o contrato do V3-BE
**confirmado em produção**. **V3-2 (Marca 360) e V3-3 (QA visual integrado)
TECNICAMENTE CONCLUÍDOS E VERSIONADOS**. O smoke de produção fechou como
**`PASS WITH ISSUE`**, com três achados **cosméticos** de formatação — todos
**corrigidos no patch terminal do §29**. **Publicação do patch e smoke terminal
seguem pendentes**, e o `PASS` final não é declarado antes dele: o QA integrado
rodou em navegador real nos três viewports, com **248 de 260 verificações
aprovadas**, e as 12 restantes são quatro dívidas preexistentes de componentes
compartilhados, demonstradas em outras rotas (§28.6). Base integrada até
`76f361b`. **Todas as seis fases do Gate V3 estão fechadas tecnicamente.**
Nenhum pipeline ou migration foi tocado em nenhum momento; o V3-BE foi a única
etapa que alterou backend, e o fez de forma aditiva.
**Data:** 18/08/2026. **Base:** `origin/main` = `3998af5`.
**Escopo:** frontend de `/inteligencia` e `/brand/[brand]`, e o fluxo de drill-down
entre Inteligência, Marca, Canais e Produtos.

Este documento é **especificação**. Nenhuma linha de UI, API, pipeline ou banco foi
escrita. Nenhum endpoint, rota ou dependência foi criado.

Revisado em 18/08/2026 numa rodada de correção terminal que reescreveu sete contratos:
matriz de oportunidades (agora dependente de BE6), drill-down do Pareto, contexto
`ctx_*` discriminado por origem, competências reais como bloqueio do V3-2, nomenclatura
temporal do bloco de prioridades, comparação entre canais e o próprio estado do gate.

---

## 1. Objetivo e personas

Duas superfícies hoje carregam o mesmo defeito estrutural em formas diferentes:
elas **mostram** o portfólio, mas não **conduzem** a uma decisão. Inteligência é
uma pilha de sete blocos independentes; Marca é uma sequência longa em que duas
seções vizinhas podem estar em períodos diferentes sem que nada avise.

### Personas

| Persona | Pergunta que traz | O que precisa da tela |
|---|---|---|
| **Gestor de marketplace / brand manager** | "onde eu ajo hoje?" | Poucos itens, ordenados por impacto, com o motivo e o próximo passo. Não quer navegar para descobrir a prioridade. |
| **Analista operacional** | "por que isso está assim?" | Evidência densa, definição da métrica, limitação do dado, e caminho até a linha que explica. |

A ordem de leitura que as duas superfícies devem servir, nesta sequência:

1. O que merece atenção?
2. Onde está a oportunidade ou o desperdício?
3. Qual marca / produto / canal explica?
4. Qual é a evidência?
5. Qual limitação existe no dado?
6. Qual é o próximo destino ou ação?

Hoje Inteligência responde 1 e 2 de forma fragmentada, responde 4 por excesso
(cinco tabelas), **não responde 3, 5 nem 6**. Marca responde 3 e 4 bem, responde 5
parcialmente e **não responde 1, 2 nem 6**.

---

## 2. Decisões suportadas

O desenho é avaliado por decisões concretas, não por beleza.

| # | Decisão | Superfície | Evidência exigida |
|---|---|---|---|
| D1 | Pausar mídia de um produto que gasta sem vender | Inteligência → produto | ad spend, dias com ads, bucket Pareto, velocity |
| D2 | Aumentar budget de um produto com ROAS alto | Inteligência → produto | ROAS, ACOS, participação no GMV, velocity |
| D3 | Testar ads num produto orgânico forte | Inteligência → produto | GMV orgânico, bucket, cancelamento |
| D4 | Priorizar uma marca para atenção da semana | Inteligência → Marca | concentração, LTV/recorrência, desperdício |
| D5 | Reequilibrar mix de canal de uma marca | Marca → Canais | GMV por canal, eficiência por canal |
| D6 | Investir em conteúdo/creator de uma marca | Marca | vídeos ativos, views, GMV por vídeo |
| D7 | Escolher produto de uma marca para empurrar no TikTok | Marca → Produtos | GMV, pedidos, vídeos, GMV/1k views |
| D8 | Aceitar ou recusar um número antes de reportá-lo | ambas | período, fonte, frescor, limitação |

D8 não é decorativa: é a decisão que sustenta as outras sete.

---

## 3. Auditoria da referência (`torre_b2b`)

Leitura ponta a ponta do frontend relevante, em clone temporário fora do repositório
da Torre, **removido ao final**. Nenhuma linha de código foi copiada.

**Forma geral.** 61 rotas de página, 133 client components co-localizados,
26 componentes compartilhados, 86 módulos em `lib/`. O padrão é
`page.tsx` fino (0,5–3,6 KB, server, resolve permissão) → `*-client.tsx` grande
(`gerencial-client` 138 KB / 2.342 linhas, `canais-client` 110 KB,
`financeiro-client` 514 KB). Tokens semânticos por CSS vars + rampa `brand`
indigo + paleta `sidebar` escura em `tailwind.config.ts`. 43 `dynamic()` para
carregamento sob demanda.

### 3.1 Padrão a padrão

**(a) Ritmo esperado — `lib/ritmo.ts`**
1. *Pergunta:* "estou adiantado ou atrasado no mês?"
2. *Organização:* converte % atingido + peso esperado da curva histórica em quatro
   classes (`atingido`/`no_ritmo`/`atencao`/`atrasado`), cada uma com badge, cor de
   barra e hex.
3. *Interação:* nenhuma — é anotação.
4. *Após clicar:* nada.
5. *Contexto:* n/a.
6. *Funciona bem:* devolve **julgamento** junto com o número, e `desvioRitmoLabel`
   produz texto humano curto ("12% atrás", "no esperado"). É o padrão mais forte da
   referência.
7. *Funciona mal:* quando a curva é nula, cai em linear **silenciosamente** — o
   usuário não sabe qual regra produziu a classificação.
8. *Análogo na Torre:* `channel-signal-reasons.ts` e `classificaRitmo` não têm par
   direto; a Torre classifica sinais de canal, não ritmo temporal.
9. *Como superar:* a Torre **não tem meta comercial**, então importar "ritmo vs
   meta" seria inventar. Adaptar para **ritmo contra a própria história** (ver 6.2) e
   **rotular o modo degradado** em vez de silenciá-lo.

**(b) Oportunidade de fechamento — `oportunidade-fechamento.tsx`**
1. *Pergunta:* "quem está abaixo do próprio potencial?"
2. *Organização:* três abas (vendedor/região/cliente) com a mesma lente; colunas
   mês atual · média 12m · pico 12m · três meses anteriores; contagem no rótulo da aba.
3. *Interação:* troca de aba, refresh, exportar.
4. *Após clicar:* **nada** — a linha não é clicável.
5. *Contexto:* recebe os filtros globais como props e os repassa à query.
6. *Funciona bem:* comparar contra **média e pico próprios** dispensa meta externa;
   `GapBadge` põe o delta embaixo do valor; `overflow-auto max-h-[600px]` com
   `thead sticky` mantém o cabeçalho **dentro** do card.
7. *Funciona mal:* `fmt(v)` devolve `"—"` para **zero**, colapsando zero com
   ausente; nenhum tipo admite `null`; tipografia em 8/9/10px; um `error` derruba as
   três abas; a regra de inclusão nunca é declarada; `hover:bg-slate-50` sugere clique
   que não existe; refresh sem `refreshed_at` visível.
8. *Análogo na Torre:* nenhum — a Torre não tem "gap vs pico próprio" em nenhuma tela.
9. *Como superar:* adotar a lente (atual vs média vs pico) **com `null` preservado**,
   com a regra de inclusão escrita no bloco, e com a linha levando a algum lugar.

**(c) Detalhe de insight — `analise-carteira/components/insight-detail.tsx`**
1. *Pergunta:* "o que esse alerta significa e o que eu faço?"
2. *Organização:* título + prioridade (alta/média/baixa com cor) + **ação sugerida**
   em prosa + score classificado + **comparação com pares** + timeline + log de ações.
3. *Interação:* registrar ação com motivo; despachar mensagem por template.
4. *Após clicar:* grava o log; **nunca navega**.
5. *Contexto:* CNPJ na query de cada sub-fetch.
6. *Funciona bem:* é o único lugar da referência que fecha o laço
   *achado → ação → registro do motivo*; **cada um dos quatro fetches degrada
   sozinho** — cada promessa tem seu próprio tratamento de falha, devolvendo dado nulo
   em vez de propagar o erro, então uma falha parcial não mata o card;
   comparação com pares dá referência sem exigir meta.
7. *Funciona mal:* zero `router.push`/`<Link>` — o insight explica e morre ali;
   ações de escrita dentro do detalhe; ação sugerida em 13px.
8. *Análogo na Torre:* `InsightDrilldownContent` (Pulso) — mesma família, sem
   prioridade explícita, sem comparação com pares, e **com** CTA de destino.
9. *Como superar:* juntar as duas metades que nenhum dos dois tem inteiras:
   prioridade + ação sugerida + referência nomeada (do `insight-detail`) **e** CTA
   com filtros preservados (da Torre).

**(d) Drill-down do funil — `funil-drilldown.tsx`**
1. *Pergunta:* "quais pedidos estão nesta etapa?"
2. *Organização:* modal cheio com tabela transacional e filtros locais.
3. *Interação:* abre por clique na etapa do funil.
4. *Após clicar:* lista pedido a pedido, com sub-modal de correção.
5. *Contexto:* recebe os 7 filtros globais por props.
6. *Funciona bem:* evidência no nível da transação; contexto global chega íntegro.
7. *Funciona mal:* estado **só em memória** (`onClose`), fora da URL — não é
   compartilhável nem sobrevive a reload; **modal sobre modal** (`z-[60]`);
   seis filtros locais (`marca_local`, `vendedor_local`, `filial_local`, …) empilhados
   sobre os globais **sem reconciliação visível**; zero `role="dialog"`,
   zero `aria-modal`, sem foco preso.
8. *Análogo na Torre:* `KpiDrilldownDialog` — que **já tem** `role="dialog"`,
   `aria-modal`, `aria-labelledby`, foco preso em Tab/Shift+Tab, Escape e restauração
   de foco.
9. *Como superar:* nada a importar. A Torre já ganha; o cuidado é **não regredir** ao
   criar novos acionamentos.

**(e) Canais — `canais-client.tsx`**
1. *Pergunta:* "como cada canal performa e quem está dentro dele?"
2. *Organização:* KPIs + série + matriz marca×canal + modal de drill por canal com
   paginação interna.
3. *Interação:* multi-selects de filial/marca/canal/UF/tipo; clique no canal abre modal.
4. *Após clicar:* lista paginada + **ações de escrita em lote** (bulk segmento).
5. *Contexto:* só estado local — nada na URL.
6. *Funciona bem:* paginação dentro do modal evita tabela infinita; `/api/canais/refresh`
   expõe `last_data` como fonte dedicada de recência.
7. *Funciona mal:* endpoint composto `/api/canais/all` devolve `kpis+canais+serie+matriz`
   num só payload — **uma falha derruba tudo**; escrita dentro de superfície de leitura.
8. *Análogo na Torre:* `/canais` com `KpiDrilldownDialog` e detalhe marca×canal,
   já com CTA para `/brand/[brand]`.
9. *Como superar:* manter o fetch por seção da Torre (U4) e trazer só a ideia de
   **paginação dentro do detalhe** quando a evidência passar de ~50 linhas.

**(f) Mapa — `components/charts/brasil-map.tsx`**
Só `onMouseEnter` + tooltip. **Não é clicável** e não tem nome acessível, então não
serve de origem de drill-down. A Torre tem `RegioesBrazilMap` lazy; oportunidade real
de superar é tornar a UF um acionamento com teclado — **fora do escopo do V3**
(pertence a `/regioes`), registrado como dívida.

**(g) Shell e navegação — `nav-config.tsx` / `sidebar.tsx`**
Navegação agrupada ("Cockpits", "Cadastros") com `description` por item e
`badge: "beta"` de maturidade, mais `aba_id`/`strictAba`/`hardGate` de permissão.
*Superar:* a Torre pode usar o mesmo slot de badge para **maturidade de dado**
("TikTok-only", "sem CMV") em vez de maturidade de software.

**(h) Estados e frescor**
`isLoading` 93×, `isValidating` 10×, `stale` 37×, `parcial`/`partial` 100×,
`fallback` 125× — a referência tem noção de parcial e stale. Mas `refreshed_at`
aparece **0 vezes** (usa `last_data`/"atualizado", 102×).

**(i) URL e compartilhabilidade — achado decisivo**
`router.push` **0**, `router.replace` **0**, `history.replaceState` **0**,
`useSearchParams` **2** em 133 componentes. Filtros, abas, drill-downs e paginação
vivem **só em memória**. Nada na referência é compartilhável ou restaurável.

**(j) Acessibilidade — achado decisivo**
Em 159 componentes: `aria-label` 21, `onKeyDown` 36, `Escape` 15, `focus-visible` 14,
`sr-only` 5, `tabIndex` 3, e **`aria-live` 0, `role="dialog"` 0, `aria-modal` 0**.

**(k) Tipografia**
No cluster da Gerencial: 4× `text-[8px]`, 24× `text-[9px]`, 112× `text-[10px]`,
16× `text-[11px]`.

**(l) Exportação**
`loadXlsx` 64×, `writeFile` 44× — dependência `xlsx`. A Torre **removeu** `xlsx` no
U4 por vulnerabilidades altas sem correção e exporta CSV sem biblioteca.
**Não reintroduzir.**

**(m) Responsividade**
Em `gerencial-client.tsx`: `sm:` 18×, **`md:` 2×**, `lg:` 9×, `xl:` 1×, `2xl:` 0.
A escada de breakpoints é rasa e tablet fica praticamente sem tratamento.

### 3.2 Conceitos B2B sem equivalente — rejeitados

`clientes`, `vendedores`, `comissão`, `inadimplência`, `carteira`,
`matriz SKU × cliente`, `pipeline comercial`, `multi-tenant de torres`.
A Torre vende para consumidor final via marketplace: não há carteira nomeada, não há
vendedor com meta, não há título a receber. Trazer qualquer um desses seria inventar
entidade.

---

## 4. Auditoria atual das duas páginas

### 4.1 `/inteligencia` — 890 linhas, client component único

Sete seções na ordem: KPIs de status (4 cards) · Urgente · Escalar · Testar Ads ·
Pareto (barras empilhadas) · LTV · Top Produtos TikTok. **Cinco das sete são tabelas
largas.** A caracterização "sete tabelas" é factual.

**O que está certo e deve ser preservado**

- `computeRequestStatus` + `requestKey`/`resolvedKey` e `displayData` protegido por
  frescor — nenhuma resposta obsoleta sobrescreve a atual, e `resolvedKey` é marcado
  **também na falha**.
- Estado vazio que diz explicitamente **"Não é modo demonstração — a fonte não
  retornou dados"**. É a melhor frase de honestidade de dado do repositório.
- `aria-live="polite"` com `aria-atomic`, `aria-busy` por seção, `focus-visible` em
  todos os controles, navegação interna com `scroll-mt-24`, `TableScrollHint`,
  `SortableHeader`, `tabular-nums`.
- Nota de escopo declarando que a tela **não herda filtros globais** e que o filtro de
  marca é local e só afeta ML.

**Defeitos**

| # | Defeito | Gravidade |
|---|---|---|
| B1 | **Nenhum drill-down.** Zero `KpiDrilldownDialog`, zero `<Link>`, `KpiCard` sem `onClick`. A tela é terminal: identifica a ação e não leva a lugar algum. | alta |
| B2 | **Coluna "Ação" é um `<span>` estilizado de botão** (`bg-rose-600 text-white rounded`) em três tabelas — "Pausar Ads", "Aumentar Budget", "Testar Ads". Falsa affordance clássica. | alta |
| B3 | **`hover:bg-slate-50` em `<tr>` de cinco tabelas** sem nenhuma linha clicável. Segunda falsa affordance. | média |
| B4 | **`ML_BRANDS = ["barbours","kokeshi","lescent"]` hardcoded no frontend, sem `rituaria`.** A API tem 4 marcas ML (rituária incluída em 01/07/2026) e a fonte tem 4. Resultado: o filtro não oferece rituária e a seção Pareto itera só 3 marcas — **dado real de uma marca fica invisível**. | alta |
| B5 | **Contagens exibidas são as contagens truncadas.** `urgent` LIMIT 30, `scale` LIMIT 20, `organic` LIMIT 20 + `.slice(0,10)`. O subtítulo diz "N produto(s)" usando o array já cortado. Se houver 200 produtos gastando sem vender, a tela diz 30. | alta |
| B6 | **Período ausente em seis das sete seções.** Blocos 1–5 leem `marts.fact_ml_produto_ranking`, que **não tem coluna de data**; bloco 6 lê a cross-company, também sem dimensão temporal. Só o bloco 7 declara período ("Últimos 30 dias"). A página nunca diz "fotografia de quando". | alta |
| B7 | **Duas escalas de percentual na mesma página.** `revenue_share_pct` é renderizado `× 100`; `cancel_rate_pct` e `ad_acos_pct` não. Sem definição visível, o leitor não sabe qual é fração e qual é percentual. | média |
| B8 | **Limiares sem referência.** "ROAS >= 8x" está no subtítulo (vem do SQL) e "bom GMV" não está definido em lugar nenhum. Nenhum tooltip, nenhuma fonte. | média |
| B9 | **Filtro de marca aplicado de forma inconsistente.** Filtra `urgent`/`scale`/`organic`; **não** filtra `ltv`, que é ML e tem coluna `brand`. | média |
| B10 | **`avg_pct_video/live/card` é média simples de percentuais diários**, não a participação do período. O cabeçalho "% Vídeo" sugere participação. | média |
| B11 | Dez ocorrências de `text-[10px]` e uma de `text-[11px]`. | baixa |
| B12 | Cinco tabelas de 7–9 colunas empilhadas verticalmente: sem hierarquia, tudo com o mesmo peso visual. | alta |

### 4.2 `/brand/[brand]` — 828 linhas

Seções: identidade + pills de marca · KPIs · Mix de Canal (GMV diário) · Ecossistema
de Conteúdo · Atratividade · Freshness de Conteúdo · Demographics · Funil por Canal ·
Top 5 Creators · Top 5 Produtos.

**O que está certo e deve ser preservado**

- **Contexto de chegada já implementado e bem desenhado** (`BrandArrivalBanner`,
  `parseBrandArrivalContext`): só enums allowlisted na URL, nunca dinheiro/percentual/
  JSON/texto livre; parâmetro ausente, repetido ou incompatível ⇒ contexto ignorado
  sem erro; `ctx_*` fora de `FILTER_QUERY_KEYS`, então a sidebar descarta o contexto.
- `mergeFilteredHref` preservando filtros, com a marca de destino sobrescrevendo
  `brands=`.
- `dailyRequestKey`/`resolvedDailyKey` e guarda `ignore` nos dois efeitos.
- `isOrdersReliable` + nota explícita quando o fallback não separa pedidos por canal.
- Volta explícita para Canais (`backToCanais`).

**Defeitos**

| # | Defeito | Gravidade |
|---|---|---|
| M1 | **Dois regimes temporais independentes na mesma página.** `daily` vem dos **filtros globais** (`dateFrom`/`dateTo`, preset `mes_anterior`) via `/performance/daily`; `brandDetail` vem de **`period`**, um mês calendário em estado local, via `/brand-detail?ref_month=`. Nada acopla os dois. A página pode mostrar Mix de Canal de julho e Análise Mensal de agosto lado a lado. | crítica |
| M2 | **A lista de competências vem de um módulo mock.** `period` é inicializado com `AVAILABLE_MONTHS[0]` de `lib/mock-daily` e alimenta uma chamada **real**. Meses reais fora dessa lista são inalcançáveis; meses da lista sem dado real produzem seções vazias. | alta |
| M3 | **Fallback sintético no gráfico principal.** Se `/performance/daily` falhar, `generateDailyData(brand, days)` gera a série e `isLive=false`. O gráfico renderiza números inventados; a defesa é um badge. | alta |
| M4 | **Seção "Demographics" nunca tem dado.** As sete colunas `viewers_pct_*` de `marts.fact_tiktok_brand_content_daily` são **100% NULL** (1.581 linhas, zero não-nulos, medido). A seção existe e exibe sete traços. | alta |
| M5 | **A página é TikTok-only, mas se chama "Marca".** Os cinco blocos de `/brand-detail` leem só fatos TikTok. ML e Shopee entram apenas pelo gráfico diário de outro endpoint. Não é uma visão 360. | alta |
| M6 | **`brandDetail` não tem guarda de identidade equivalente à do `daily`.** Tem `ignore`, mas não há `detailIsFresh`/`displayBrandDetail` protegido como em `dailyIsFresh`. Assimetria com o padrão U4. | média |
| M7 | Onze `text-[10px]` e dois `text-[11px]`. | baixa |
| M8 | Dez seções em coluna única longa, com blocos pareados de alturas muito diferentes (Demographics vazio ao lado de Funil cheio). | alta |
| M9 | **Nenhum drill-down próprio.** Registrado no G2 §1.5 como aceitável naquela fase; com Inteligência passando a apontar para cá, deixa de ser aceitável. | média |

### 4.3 Referências internas consultadas

`PRODUCT.md` (47 linhas), `DESIGN.md` (258), `docs/DRILLDOWN_ARCHITECTURE.md` (436),
`docs/GERENCIAL_V2_SPEC.md` (1.051), `docs/UI_REVAMP_V2_PLAN.md` (1.067),
`docs/PROJECT_STATUS.md` (1.735), seções S3 de `docs/SERVING_AIRFLOW_PLAN.md`.

Contexto lido sem incorporar decisão aberta: `tiktok_gmv_com_frete_decisao.md`,
`tiktok_marts_grain_extension_handoff.md`, `gold_vs_marts_matrix.md`,
`cobertura_canais_avoe.md`, `docs/octaprice/`. **GMV TikTok com frete continua
decisão aberta** — todo número de GMV neste plano é a métrica vigente, sem frete.

Componentes de composição do G2 §3.1 que **já existem**: `DrilldownContextLine`,
`EvidenceRow`, `DataQualityNote`, `DrilldownCta`. **Não existe** `DrilldownMetricPair`
(previsto e nunca construído) — é a única peça nova de composição que o V3 precisa.

---

## 5. Verdade dos dados

### 5.1 Inteligência — sete blocos

Fonte medida por AST em `get_inteligencia` (última definição vence) e pelo SQL real.
**Zero `gold.*`, zero `raw.*`, zero `datamart_engine`** nos dois métodos.

| Bloco | Pergunta | Fonte | Grão | Chave | Métricas | Período | Limite | Limitação | Destino natural |
|---|---|---|---|---|---|---|---|---|---|
| `signals` | Como o portfólio ML se distribui entre vender/anunciar? | `marts.fact_ml_produto_ranking` | agregado por `product_status` | `product_status` | `n_products`, `SUM(gross_revenue)`, `SUM(ad_spend)`, `AVG(ad_roas) WHERE >0` | **nenhum** (snapshot) | — | `avg_roas` ignora ROAS ≤ 0; 4 status fixos | fila de evidência filtrada por status |
| `urgent` | Quem gasta em ads sem vender? | idem | produto | `(brand, title)` no payload; a tabela tem `(brand, item_id)` | `ad_spend`, `days_advertised`, `pareto_bucket`, `revenue_velocity` | **nenhum** | **30** | payload **sem `item_id`**; contagem exibida = capada | produto → Marca |
| `scale` | Quem tem ROAS alto e merece budget? | idem | produto | idem | `gmv`, `ad_roas`, `ad_acos_pct`, `revenue_share_pct` | **nenhum** | **20** | `ad_roas >= 8` fixo no SQL; `revenue_share_pct` é fração | produto → Marca |
| `organic` | Quem vende sem ads? | idem | produto | idem | `gmv`, `units_sold`, `cancel_rate_pct` | **nenhum** | **20** + `.slice(0,10)` no cliente | corte duplo silencioso | produto → Marca |
| `pareto` | Quão concentrado é o GMV? | idem | `(brand, pareto_bucket)` | `(brand, pareto_bucket)` | `n_products`, `SUM(gmv)`, `SUM(ad_spend)` | **nenhum** | — | frontend itera 3 marcas (B4) | marca → Marca |
| `ltv` | Os compradores voltam? | `marts.fact_ml_cross_company_summary` | marca | `brand` | `total_buyers`, `repeat_buyers`, `repeat_rate_pct`, `avg_customer_ltv`, `vip_buyers`, `one_and_done_buyers`, `at_risk_or_churned`, `overall_roas` | **nenhum**; `date_from`/`date_to` são `NULL` por contrato | 4 linhas | não filtrado pelo filtro local (B9) | marca → Marca |
| `tk_products` | Quais produtos TikTok puxam GMV? | `marts.fact_tiktok_product_daily` | `(brand, product_name)` | `(brand, product_name)` | `SUM(gmv)`, `SUM(orders)`, `AVG(pct_gmv_*)`, `avg_rating` | **últimos 30 dias** | **25** | agrupa por **nome**, não por `product_id` — nomes repetidos somam IDs distintos; `AVG` de % diário ≠ participação do período | produto → Marca |

**Consequência central:** seis dos sete blocos **não têm período**. Isso não é lacuna
de UI, é a natureza da fonte — `fact_ml_produto_ranking` é substituída por inteiro a
cada carga e carrega `refreshed_at`, não janela. Qualquer desenho que sugira "período"
nesses blocos mente.

### 5.2 Brand Detail — cinco blocos

Todos com `date BETWEEN start AND end`, derivado de `ref_month`. **Todos TikTok.**

| Bloco | Fonte | Grão | Métricas relevantes | Limitação |
|---|---|---|---|---|
| KPIs | `marts.fact_tiktok_brand_content_daily` | marca × mês | `gmv`, `orders`, `customers` (`CASE WHEN visitors > 0`), `cvr_pct`, `cos_pct`, `gpm`, `active_videos`, `total_views`, `active_video_creators`, `videos_per_creator`, mix `pct_video/live/card`, `evergreen/fresh_videos` | `active_videos`/`total_views` vêm da tabela **de marca**, não da soma de produtos — não conferem com o produto e não devem ser comparados |
| `daily` | idem | dia | `gmv`, `gmv_video`, `gmv_live`, `gmv_card`, `new_videos_posted` | é uma **segunda** série diária, distinta da série global da página |
| `top_creators` | `marts.fact_tiktok_creator_daily` | creator | `gmv`, `videos`, `lives` | LIMIT 5; nomes de creator são dado sensível |
| `top_produtos` | `marts.fact_tiktok_product_daily` | `(product_id, product_name)` | `gmv`, `orders`, `videos` = `SUM(active_videos)`, `gpm` = `SUM(gmv)/SUM(video_views)*1000` | LIMIT 5; **usa as colunas da migration 011** |
| `channels` | `marts.fact_tiktok_channel_efficiency_daily` | canal | `impressions`, `page_views`, `items_sold`, `gmv`, `ctr_pct`, `cvr_pct` | 3 canais; tabela criada no S3 |
| Demografia | `fact_tiktok_brand_content_daily` | marca × mês | 7 × `viewers_pct_*` | **100% NULL**, medido |

Ads: **não existe** em `/brand-detail`. Não há coluna de ad spend TikTok no contrato.
Qualquer bloco de Ads na página de Marca hoje seria invenção.

### 5.3 Classificação do que se deseja

| Item desejado | Classificação |
|---|---|
| Prioridade/severidade por linha de oportunidade | **calculável** com o payload atual, **sobre a amostra capada** |
| Matriz 2×2 de oportunidade sobre o portfólio | **exige endpoint/query nova** — campo aditivo `opportunity_map` (BE6, §15.1). O payload atual **não** sustenta: `urgent ∪ scale ∪ organic` é amostra capada, deixa de fora `sells+advertised` com ROAS < 8 e todo `inactive`, e **não existe corte de GMV** |
| Referência de GMV para o eixo vertical | **exige endpoint/query nova** — mediana do GMV positivo do universo do snapshot. **Não** derivável de `product_status`, que distingue venda/Ads e não é limiar de volume |
| Produtos de um bucket Pareto | **exige endpoint/query nova** — o payload `pareto` só traz agregados. A consulta já existe em `/produtos/ml?pareto_bucket=`; falta o **wiring de URL** no frontend |
| Lista de competências reais da marca | **novo campo aditivo** `available_months` (BE5, §15.2) — **não derivável** de `brandDetail.daily`, que só traz o mês pedido |
| Concentração Pareto em barra | **já disponível** |
| LTV/recorrência por marca | **já disponível** |
| Mix de canal por marca | **já disponível** (`channels`) |
| GMV por 1k views por produto | **já disponível** (`gpm` em `top_produtos`) |
| Headroom "atual vs melhor mês próprio" da marca | **calculável** só se a série mensal existir; hoje `/brand-detail` traz um mês por chamada ⇒ **exige endpoint/query nova** ou N chamadas |
| `item_id` para os destaques do mapa | **exige endpoint/query nova** — entra **dentro do contrato BE6** (§15.1-D), não como campo solto em `ProductSignalRow` |
| `product_id` em `tk_products` | **fora do escopo do V3** — **não é aditivo**: `tk_products` agrupa por `(brand, product_name)`, e adicionar `product_id` mudaria `GROUP BY`, grão, contagens e valores. Registrado como **decisão futura separada** (§15.0) |
| Contagem verdadeira ao lado da lista capada | **novo campo aditivo** (`*_total_count`) |
| `refreshed_at` do snapshot ML no payload de Inteligência | **novo campo aditivo** (a tabela tem a coluna) |
| Demografia de audiência | **indisponível — não prometer** |
| Ad spend/ROAS de TikTok | **indisponível — não prometer** |
| Margem real / CMV | **indisponível — não prometer** |
| Ritmo vs meta comercial | **indisponível** — não existe meta no domínio |
| Comparação com pares no estilo `insight-detail` | **calculável** entre marcas do próprio portfólio (mediana das 4/5 marcas) |

---

## 6. Padrões: copiar / adaptar / superar / rejeitar

| Padrão da referência | Ação | Justificativa |
|---|---|---|
| Scroll interno com `thead sticky` dentro do card | **copiar** | resolve o cabeçalho flutuando fora do card |
| Abas com contagem no rótulo | **copiar** | densidade sem esconder volume |
| `dynamic()` por bloco pesado | **copiar** | a Torre já faz no mapa; estender |
| Atual vs média vs pico próprios (`oportunidade-fechamento`) | **adaptar** | dispensa meta, que a Torre não tem |
| Classificação com rótulo humano (`lib/ritmo`) | **adaptar** | vs história própria, e **rotulando** o modo degradado |
| Prioridade + ação sugerida + comparação com pares (`insight-detail`) | **adaptar** | pares = outras marcas do portfólio |
| Degradação independente por fetch | **adaptar** | a Torre já tem parcial por seção (U4); estender ao detalhe |
| Nav agrupada com badge de maturidade | **adaptar** | badge de **maturidade de dado**, não de software |
| Paginação dentro do detalhe | **adaptar** | só acima de ~50 linhas de evidência |
| `fmt(0) → "—"` | **rejeitar** | colapsa zero com ausente; viola null ≠ zero |
| Biblioteca `xlsx` | **rejeitar** | removida no U4 por vulnerabilidade alta |
| Tipografia 8/9/10px | **rejeitar** | piso de 12px |
| Drill-down só em memória | **rejeitar** | a Torre já tem URL allowlisted |
| Modal sobre modal | **rejeitar** | um nível de diálogo |
| Escrita dentro de superfície de leitura | **rejeitar** | Inteligência e Marca são leitura |
| Endpoint composto único | **rejeitar** | acopla falhas |
| Linha com hover e sem clique | **rejeitar** | falsa affordance (e a Torre também comete, B3) |
| Conceitos B2B (§3.2) | **rejeitar** | sem entidade equivalente |

**Onde a Torre já supera e não pode regredir:** filtros e contexto na URL com
allowlist; `role="dialog"`/`aria-modal`/foco preso/Escape/restauração de foco;
`refreshedAt: null` que **nunca inventa timestamp**; `null` distinto de zero;
identidade de requisição (`requestKey`/`resolvedKey`) marcada também na falha;
exportação CSV sem dependência; alvo de toque ≥44px.

**Como superamos em capacidade explicativa e navegabilidade**, concretamente:
a referência tem *ou* explicação sem destino (`insight-detail`) *ou* destino sem
explicação (CTAs da Gerencial). O V3 exige **explicação, evidência, limitação e
destino no mesmo acionamento** — é o contrato §3 do G2 aplicado a uma superfície que
hoje não tem acionamento nenhum.

---

## 7. Arquitetura de Inteligência

**Tese:** deixar de ser sete tabelas e passar a ser um **radar decisório de
portfólio** — o que atenção pede hoje, quanto vale, quem explica, e para onde ir.

Sete blocos, no máximo. Nenhum gráfico decorativo. Duas tabelas densas, não cinco.

### 7.1 Bloco 1 — Cabeçalho de escopo e frescor

- **Pergunta:** de quando é isso que estou lendo?
- **Visual:** cabeçalho + duas etiquetas de regime temporal explícitas.
- **Métrica:** nenhuma. `refreshed_at` do snapshot ML e a janela de 30 dias do TikTok.
- **Estado:** `loading` mostra "Atualizando"; `stale` mostra a etiqueta em âmbar.
- **Interação:** nenhuma.
- **Limitação:** enquanto `refreshed_at` não vier no payload (§11 backend), a etiqueta
  ML diz "fotografia do último carregamento" **sem** timestamp — nunca um inventado.
- **Resolve:** B6.

Duas etiquetas, sempre visíveis, porque a página tem de fato dois regimes:
`Portfólio ML · fotografia` e `TikTok · últimos 30 dias`. Cada bloco abaixo herda
uma delas e a repete no próprio cabeçalho.

### 7.2 Bloco 2 — Prioridades da fotografia ML atual

O título é literal de propósito. A fonte destes cartões é
`marts.fact_ml_produto_ranking`, uma **fotografia acumulada sem janela temporal** —
chamar isso de "prioridades do período" sugeriria que o bloco responde ao intervalo
global de datas, e ele não responde a intervalo nenhum.

- **Pergunta:** o que merece atenção na fotografia atual do portfólio ML?
- **Escopo declarado no cabeçalho do bloco:** `Mercado Livre · fotografia`, mais a
  data de atualização **quando BE4 existir**. Sem BE4, o bloco diz
  "fotografia do último carregamento" **sem** timestamp — nunca um inventado.
- **Visual:** três a quatro **cartões de prioridade** (não KPI cards): cada um com
  título curto, contagem, valor em risco/oportunidade, e uma frase de motivo.
- **Métrica:** derivada de `signals` + agregação de `urgent`/`scale`/`organic`.
- **Dimensão:** classe de ação.
- **Ordenação:** por valor absoluto em jogo (desperdício primeiro, depois oportunidade).
- **Estado:** skeleton por cartão; cartão vazio some (não exibe "0" como prioridade).
- **Interação:** o cartão inteiro é botão, com nome acessível próprio.
- **Drill-down:** abre `KpiDrilldownDialog` com o conteúdo do bloco 6 filtrado pela
  classe.
- **CTA:** "Ver evidências" → rola/filtra o bloco 6.
- **Contexto preservado:** filtro local de marca.
- **Regime único:** **`tk_products` não entra neste bloco.** TikTok tem janela de 30
  dias e ML não tem janela; misturar os dois numa mesma prioridade produziria um
  número sem período definível. A leitura TikTok fica no bloco 5, sob a sua própria
  etiqueta.
- **Limitação:** o valor é a soma **do que veio na lista capada** até `*_total_count`
  (BE3) existir; a frase de motivo declara isso.
- **Resolve:** B1, B12 (hierarquia), parte de B5, B6.

### 7.3 Bloco 3 — Mapa de oportunidades (matriz) — **depende de BE6**

A matriz permanece o elemento central desejado, e **não é implementável com o payload
atual**. O desenho anterior propunha derivá-la de `urgent ∪ scale ∪ organic`; a
revisão mostrou que essa união **não é o portfólio**:

| Motivo | Consequência |
|---|---|
| `urgent` LIMIT 30, `scale` LIMIT 20, `organic` LIMIT 20 | a união é amostra capada, nunca universo |
| `scale` exige `sells+advertised` **e** `ad_roas >= 8` | produto **`sells+advertised` com ROAS < 8 não entra em lista nenhuma** |
| `inactive` não tem lista | quarto status do portfólio ausente |
| `signals` é agregado por `product_status` | não oferece coordenada por produto |
| existe corte de ROAS em 8, **não existe corte de GMV** | os quatro quadrantes não têm eixo vertical defensável |
| `product_status` distingue venda/Ads | **não é limiar de volume**; usá-lo como eixo de GMV seria inventar semântica |

Portanto o plano **não** afirma que o payload atual sustenta um mapa do portfólio, nem
que os quatro quadrantes são completos.

- **Pergunta:** onde está a oportunidade e onde está o desperdício, no universo inteiro?
- **Visual (com BE6):** **matriz 2×2**, eixo X = retorno (`roas` vs `roas_reference`),
  eixo Y = volume (`gmv` vs `gmv_reference`), tamanho do ponto = investimento, cor =
  quadrante. Quadrantes com fronteira inclusiva no lado alto (§15.1-B): `escalar`
  (alto/alto), `testar_investimento` (ROAS alto, GMV baixo), `monitorar` (ROAS baixo,
  GMV alto), `reduzir_parar` (baixo/baixo). Fora dos quadrantes, **duas faixas
  distintas**: `sem_ads` e `roas_indisponivel_com_investimento` — a segunda é onde o
  desperdício não-mensurável fica visível, em vez de desaparecer dentro de "sem Ads".
- **Métrica:** exclusivamente os campos de `opportunity_map` (BE6, §15).
- **Dimensão:** produto, com identificador estável.
- **Ordenação:** posicional; rótulo apenas nos maiores contribuintes de cada quadrante.
- **Estado:** `empty` quando o universo elegível for zero. Quando
  `returned_count < total_count`, a UI declara que **os pontos são destaques** e que
  **os agregados dos quadrantes cobrem o universo completo** — os pontos nunca são
  apresentados como "todos os produtos".
- **Interação:** clique no quadrante filtra o bloco 6; clique no ponto abre o detalhe.
- **Drill-down:** `KpiDrilldownDialog` "Produto · quadrante", com as duas referências
  e a origem de cada uma escritas no diálogo.
- **CTA:** "Abrir marca deste produto".
- **Filtros preservados:** marca local.
- **Limitação:** escopo `ml_snapshot` — **sem janela temporal**; TikTok não entra
  (não há ROAS TikTok); as duas referências são **descritivas do portfólio, não meta**.
  `roas = 0` é valor numérico baixo e ocupa quadrante; `roas = null` é
  indisponibilidade e vai para faixa.
- **Por que gráfico:** a decisão é comparar duas grandezas contínuas em quatro regimes
  — é o que uma tabela não mostra. Não é decoração. Mas só vale se o universo for real.
- **Resolve:** B12, B2.

#### 7.3.1 Degradação enquanto BE6 não existir

A degradação aceita **não é uma matriz 2×2 incompleta**. Sem BE6 o bloco 3 **não é uma
matriz**: exibe **listas e faixas de evidência priorizadas**, cada uma rotulada como
**amostra capada** com o limite explícito ("30 de ao menos 30", e "ao menos N" até BE3),
sem eixo de GMV, sem quadrante e sem afirmação de cobertura. Nenhum ponto é plotado num
plano cartesiano que insinue universo.

### 7.4 Bloco 4 — Concentração e composição

- **Pergunta:** o portfólio depende de poucos produtos?
- **Visual:** barra empilhada A/B/C/D por marca (**mantém** o visual atual, que já é
  bom) + uma linha por marca com "N produtos = X% do GMV".
- **Métrica:** `pareto`.
- **Dimensão:** marca × bucket.
- **Ordenação:** marcas por GMV total desc.
- **Estado:** marca sem bucket some da lista, com nota de contagem.
- **Interação:** clique no segmento abre um diálogo **explicativo** do bucket.
- **Drill-down:** **somente os agregados que o payload realmente traz** — `n_products`,
  `gmv` e `ad_spend` do par `(brand, pareto_bucket)`, mais o share do bucket no GMV da
  marca. O payload `pareto` **não contém os produtos do bucket**, e `urgent`/`scale`/
  `organic` **não recompõem** o bucket (são capadas e filtradas por `product_status`).
  O diálogo, portanto, **não lista produtos** e não afirma tê-los.
- **CTA:** "Ver produtos deste bucket em /produtos", com a querystring canônica
  `/produtos?channels=ml&brands=<marca>&pareto_bucket=<bucket>` — ver §9.2.
- **Escopo declarado:** `Mercado Livre · fotografia` (snapshot ML, sem janela).
- **Wiring necessário (frontend, V3-1):** a API já aceita `pareto_bucket` em
  `/api/v1/performance/produtos/ml` e valida contra `VALID_PARETO_BUCKETS`, mas a página
  `/produtos` guarda o bucket em **estado local** e **não lê `searchParams`** — hoje o
  filtro não é reproduzível por URL. É tarefa de frontend do V3-1 fazer `/produtos` ler
  `brand` e `pareto_bucket` da URL. **Nenhum endpoint novo** é criado para isso.
- **Limitação:** **as 4 marcas ML**, incluindo rituária — corrige B4 lendo as marcas
  presentes no payload em vez de uma constante local.
- **Resolve:** B4.

### 7.5 Bloco 5 — Produtos e mídia (leitura, não tabela)

- **Pergunta:** a mídia está paga onde o produto responde?
- **Visual:** duas **listas compactas** lado a lado (não tabelas de 8 colunas):
  "Maior desperdício" e "Maior retorno", 5 linhas cada, com barra proporcional.
- **Métrica:** `ad_spend` (desperdício), `ad_roas` × `gmv` (retorno).
- **Ordenação:** desc pela métrica do lado.
- **Estado:** lado vazio exibe frase própria; os dois vazios colapsam o bloco.
- **Interação:** linha abre detalhe do produto.
- **CTA:** "Ver todos na fila" (bloco 6).
- **Limitação:** ML apenas; cada lista declara "5 de N" com N verdadeiro quando
  `*_total_count` existir, e "5 de ao menos 30" antes disso.
- **Resolve:** B5, B12.

### 7.6 Bloco 6 — Fila priorizada de evidências

- **Pergunta:** qual é a evidência linha a linha?
- **Visual:** **uma** tabela densa, com seletor de lente (Parar · Escalar · Testar ·
  Todos) e contagem por lente na aba — substitui as três tabelas de hoje.
- **Métrica:** colunas por lente, sem repetir as 9 colunas em todas.
- **Dimensão:** produto.
- **Ordenação:** `SortableHeader` (preservado), padrão = métrica da lente.
- **Estado:** skeleton; vazio por lente; parcial quando uma lente falhar.
- **Interação:** **botão explícito "Detalhe"** por linha, com nome acessível
  ("Detalhe de <produto>"). A linha inteira **não** é clicável e perde o
  `hover:bg-slate-50` enganoso — o hover passa a marcar só o botão.
- **Drill-down:** `KpiDrilldownDialog` no contrato §3.
- **CTA:** "Abrir marca deste produto".
- **Filtros preservados:** lente e marca local viajam por URL allowlisted.
- **Limitação:** aviso único de truncamento por lente.
- **Resolve:** B2, B3, B5, B12.

### 7.7 Bloco 7 — LTV e próximos destinos

- **Pergunta:** os compradores voltam, e para onde eu vou agora?
- **Visual:** tabela de 4 linhas (uma por marca ML — **filtrável**, corrigindo B9) +
  faixa de destinos.
- **Métrica:** `ltv`.
- **Interação:** linha → detalhe da marca; destinos → `/canais`, `/produtos`,
  `/brand/<marca>` com filtros preservados.
- **Limitação:** ML apenas; sem dimensão temporal — a etiqueta de regime repete isso.
- **Resolve:** B9, B1.

### 7.8 O que sai

`urgent`, `scale` e `organic` **deixam de ser três tabelas** e viram lentes do bloco 6.
`tk_products` sai da lista plana e entra no bloco 5 como terceira lente TikTok, com a
etiqueta de 30 dias. As colunas "Ação" com `<span>` de botão são **removidas** — a ação
passa a ser o quadrante (bloco 3) e o botão "Detalhe" (bloco 6).

---

## 8. Arquitetura de Marca 360

**Tese:** receber contexto de chegada e explicar a marca em quatro camadas —
**situação → canal → produto/conteúdo → próximo passo** — com o regime temporal de
cada seção sempre explícito.

### 8.1 Sequência

| # | Bloco | Pergunta | Visual | Período | Limitação declarada |
|---|---|---|---|---|---|
| 1 | **Contexto de chegada** | por que estou aqui? | `BrandArrivalBanner` (existente) estendido para `ctx_from=inteligencia` | herda | banner nunca exibe número |
| 2 | **Identidade + situação** | como a marca está? | cabeçalho + 3 sinais classificados com motivo textual | **global** | — |
| 3 | **KPIs** | quanto? | `KpiCard` (existente) | **competência mensal** | etiqueta mensal |
| 4 | **Evolução e mix por canal** | o dinheiro vem de onde? | `DailyChart` + `ChannelMixChart` (existentes) | **global** | fallback sintético sinalizado |
| 5 | **O que mudou e por quê** | o que explica a variação? | 3–5 linhas de decomposição com delta nomeado | **global vs período anterior** | só quando `compare` estiver ativo |
| 6 | **Funil / conversão** | onde perde? | tabela de canal (impressões → views → itens → GMV) | **competência mensal** | 3 canais TikTok |
| 7 | **Conteúdo e creators** | conteúdo sustenta venda? | ecossistema + freshness + top 5 creators | **competência mensal** | nomes de creator ficam fora da URL |
| 8 | **Produtos** | quais produtos puxam? | top 5 com `gpm` e vídeos | **competência mensal** | usa colunas da migration 011 |
| 9 | **Oportunidades / riscos** | o que fazer nesta marca? | 2–4 cartões com motivo | mista, etiquetada por cartão | nunca inventa ação sem dado |
| 10 | **Próximos passos** | para onde vou? | destinos com filtros preservados | — | — |

**A seção "Demographics" é removida** enquanto as sete colunas forem 100% NULL.
Em seu lugar, uma linha em "limitações do dado" dizendo que audiência não é coberta.
Reintroduzir só quando houver valor não-nulo. (M4)

**Ads não entra.** Não há ad spend TikTok no contrato. (§5.2)

### 8.2 Convivência de períodos — a decisão central

O problema (M1) é que hoje dois regimes coexistem **sem marcação**. Regra do V3:

1. **Um seletor por regime, cada um adjacente ao seu bloco.** O `DateRangeFilter`
   global governa os blocos 2, 4 e 5. O `PeriodSelector` de competência governa 3, 6,
   7 e 8, e fica **dentro** do cabeçalho de um contêiner rotulado
   "TikTok Shop · análise mensal".
2. **Etiqueta de período obrigatória** no cabeçalho de **todo** bloco, com duas
   formas visuais distintas: intervalo (`01–31/07`) para o regime global e competência
   (`jul/2026`) para o mensal. Nunca a mesma formatação para os dois.
3. **Quando os dois regimes não se sobrepõem**, uma nota neutra e única acima do
   contêiner mensal: "a análise mensal abaixo cobre jul/2026; os blocos acima cobem
   01–31/08". Sem cor de erro — não é erro, é escopo.
4. **A lista de competências passa a vir da API**, não de `lib/mock-daily` (M2). Até
   existir, a lista é derivada das datas realmente presentes em `brandDetail.daily`,
   e nunca de um módulo de mock.
5. **Troca de marca** pelos pills: `brands=` é sobrescrito (já ocorre), `ctx_*` é
   **descartado** (já ocorre por não estar em `FILTER_QUERY_KEYS`), e a competência
   mensal é **preservada** — é escolha do analista, não contexto de chegada.
6. **URL compartilhável:** filtros globais + competência + `ctx_*` allowlisted. Abrir
   a URL noutro navegador reproduz a mesma tela.
7. **Retorno à evidência:** o banner mantém o link de volta à origem; com
   `ctx_from=inteligencia`, volta para a lente correta do bloco 6.

---

## 9. Matriz de drill-down

Contrato: **um único `KpiDrilldownDialog`**, primitives do G2, explicação antes da
navegação, identificadores allowlisted na URL, **zero** dinheiro/percentual/JSON/texto
livre em querystring, filtros preservados, contexto descartado ao trocar marca/canal,
`null` distinto de zero, qualidade separada do diagnóstico comercial.

**Anti-registry mantido.** O G2 §3.1 rejeitou registry e definiu revisão "quando um 4º
conteúdo de detalhe nascer **e repetir fluxo**". O V3 adiciona acionamentos do **mesmo
gênero** (fila de evidência de produto/marca) e os resolve por **composição** com as
peças que já existem — `DrilldownContextLine`, `EvidenceRow`, `DataQualityNote`,
`DrilldownCta` — mais a única peça prevista e nunca construída,
`DrilldownMetricPair`. Nenhum registro central. A revisão só se justifica se um gênero
novo aparecer com fluxo próprio repetido.

| Origem | Elemento | Pergunta | Conteúdo do detalhe | Evidência | Limitação | CTA | Destino | Contexto preservado |
|---|---|---|---|---|---|---|---|---|
| Inteligência B2 | cartão de prioridade (botão) | o que compõe esta prioridade na fotografia ML? | diagnóstico + total da classe + top 5 contribuintes | linhas produto → ad_spend/ROAS | escopo `ml_snapshot` sem janela; "5 de ao menos N (amostra capada)" | Ver evidências | bloco 6 na lente | lente + marca local |
| Inteligência B3 | quadrante da matriz (**só com BE6**) | o que há neste regime? | regra do quadrante (fronteiras `>=`) + **as duas referências com a origem de cada** + `count`/`gmv`/`ad_spend` **do universo** ao lado do `returned_count` de destaques | agregados completos do quadrante + pontos de destaque com critério de ordenação declarado | escopo `ml_snapshot`; referências descritivas, **não meta**; destaques ≠ universo | Ver evidências | bloco 6 filtrado | lente + marca |
| Inteligência B3 | **ponto de destaque** | por que este produto está aqui? | diagnóstico + métrica principal + as **duas referências** com a origem de cada + o lado da fronteira que o classificou | `gmv`, `ad_spend`, `roas`, quadrante | escopo `ml_snapshot`, sem janela; sem CMV; é **destaque**, não amostra do universo | Abrir marca | `/brand/<marca>?ctx_from=inteligencia&ctx_focus=escala_ads|desperdicio_ads&ctx_channel=ml&ctx_brand=<marca>` | filtros + `ctx_*` |
| Inteligência B3 | faixa `roas_indisponivel_com_investimento` | há gasto sem retorno apurável? | `count`, `gmv` e `ad_spend` da faixa + explicação de que é **falha de mensuração**, não retorno baixo | agregados da faixa | não confundir com `sem_ads` (sem investimento) nem com `roas = 0` (retorno baixo, que fica em quadrante) | Ver evidências | bloco 6, lente Parar | lente + marca |
| Inteligência B4 | segmento da barra Pareto | o que há no bucket? | **apenas os agregados do payload**: `n_products`, `gmv`, `ad_spend` e share do bucket no GMV da marca | os próprios agregados — **o payload não traz os produtos do bucket, e o diálogo não os lista** | escopo `ml_snapshot`; 4 marcas ML | Ver produtos deste bucket | `/produtos?channels=ml&brands=<marca>&pareto_bucket=<bucket>` (§9.2; **exige o wiring de URL do V3-1A**) | filtros |
| Inteligência B5 | linha da lista compacta | por que está no topo? | diagnóstico + valor + referência | métricas da lente | "5 de N" | Ver na fila | bloco 6 | lente |
| Inteligência B6 | **botão "Detalhe"** | qual é a evidência completa? | contrato §3 completo | todas as colunas da lente | truncamento declarado + ausência de período | Abrir marca | `/brand/<marca>` com `ctx_focus` da lente (§9.1) | filtros + `ctx_*` |
| Inteligência B7 | linha de LTV | os compradores voltam? | recorrência + LTV + em risco | 8 métricas da cross-company | sem dimensão temporal | Abrir marca | `/brand/<marca>` | filtros |
| Inteligência B7 | destino | — | — | — | — | Canais / Produtos | `/canais`, `/produtos` | filtros |
| Marca B2 | sinal classificado | por que este sinal? | motivo textual (`channel-signal-reasons`) | métrica que disparou | aplicabilidade por canal | Ver canal | `/canais` | filtros |
| Marca B6 | linha de canal | onde perde conversão **neste canal**? | funil do próprio canal + taxas calculáveis dele (`ctr_pct`, `cvr_pct`) + contribuição da superfície no GMV da marca. **Sem benchmark entre canais** | impressões→views→itens→GMV | TikTok-only; vídeo, live e product card são **superfícies heterogêneas** — sem regra de negócio documentada que demonstre comparabilidade, a mediana das três não é referência de performance | Comparar canais | `/canais` | filtros |
| Marca B8 | linha de produto | este produto sustenta? | GMV, pedidos, vídeos, GMV/1k views | `top_produtos` | competência mensal | Ver em Produtos | `/produtos` | filtros |
| Marca B1 | banner de chegada | por que cheguei aqui? | motivo da navegação, **sem número** | — | contexto ignorado se incompatível | Voltar à origem | Canais **ou** Inteligência | — |

### 9.1 Contrato de contexto discriminado por origem (Finding 3)

O contrato atual de `brand-arrival-context.ts` foi desenhado **para Canais**: os cinco
sinais (`custo_alto`, `frete_alto`, `ads_subutilizado`, `sem_dado`, `roas_forte`) são
sinais da matriz marca × canal, validados por `isSignalCompatibleWithChannel`. As
categorias reais da Inteligência — desperdício, escala, venda orgânica, concentração,
LTV, produto TikTok — **não são esses sinais**. Reaproveitar `ctx_signal` com outro
significado seria mapeamento semanticamente falso, e o plano não faz isso.

**Extensão retrocompatível e discriminada:**

| Aspecto | `ctx_from=canais` (existente) | `ctx_from=inteligencia` (novo) |
|---|---|---|
| Parâmetro de motivo | `ctx_signal` ∈ `ARRIVAL_SIGNALS` | **`ctx_focus`**, enum próprio |
| Canal | `ctx_channel` **obrigatório**, validado contra os canais filtrados e contra a emissibilidade do sinal | **só quando semanticamente necessário** — ver abaixo |
| Marca | `ctx_brand` obrigatório, validado contra a marca da rota | idem, sem mudança |
| Identificador de produto | não usa | **só se a Marca consumir** — ver abaixo |
| Rótulo de retorno | `RETURN_CTA_LABEL` atual | rótulo próprio ("Voltar à evidência em Inteligência") |

**Retrocompatibilidade é estrutural, não uma promessa:** `parseBrandArrivalContext`
hoje faz `if (from !== CTX_FROM_CANAIS) return null` e exige os quatro parâmetros
presentes e únicos. Uma URL de Canais continua sendo interpretada exatamente como
antes; uma URL com `ctx_from=inteligencia` é hoje **ignorada em silêncio**, que é o
comportamento correto até o wiring existir.

**Focos allowlisted de Inteligência** — refletem **apenas** categorias que existem no
payload de `/inteligencia`, uma a uma:

| `ctx_focus` | Origem real | Canal precisa viajar? |
|---|---|---|
| `desperdicio_ads` | `urgent` (`product_status = 'ad_spend_no_sales'`) | sim, `ml` |
| `escala_ads` | `scale` (`sells+advertised` e `ad_roas >= 8`) | sim, `ml` |
| `venda_organica` | `organic` (`sells_organic_only`) | sim, `ml` |
| `concentracao` | `pareto` | sim, `ml` |
| `ltv` | `ltv` (cross-company) | sim, `ml` |
| `produto_tiktok` | `tk_products` (janela de 30 dias) | sim, `tiktok` |

Os seis focos são **seis focos navegáveis derivados dos blocos do payload** — o
payload tem **sete** campos, e `signals` é agregado de suporte, sem produto para
navegar, logo não gera foco. Nenhum foco é criado
sem bloco que o produza. Como cada foco nasce de uma fonte de **um** marketplace, o
canal **é** semanticamente necessário nos seis casos e continua obrigatório — o que
preserva a validação existente contra os canais filtrados: chegar com foco de ML numa
página filtrada sem ML descarta o contexto, e isso é correto.

**Identificador de produto na URL.** `item_id`/`product_id` **não viajam nesta fase**.
A regra é: identificador só entra quando o consumidor realmente o usa, e a página de
Marca **não tem hoje nenhum consumidor** de um `item_id` de ML — os cinco blocos dela
são TikTok e agregados por marca ou por `product_id` próprio. Transportar o
identificador antes de existir uso verificável seria dívida sem retorno. Quando um
consumidor nascer (por exemplo, destacar o produto de chegada na lista de produtos),
valem duas regras adicionais, porque **identificador opaco não é enum**:

- validação explícita de **tamanho** (limite máximo declarado) e de **charset**
  (allowlist de caracteres, sem espaço, sem acento, sem separador de querystring);
- ausência, formato inválido ou identificador não encontrado ⇒ contexto **ignorado**,
  sem erro, exatamente como os enums já se comportam.

**Invariantes preservadas:** `ctx_*` continua fora de `FILTER_QUERY_KEYS`
(`["channels","brands","date_from","date_to","compare"]`), então a sidebar e os links
da página descartam o contexto; nenhum valor monetário, percentual, título, mensagem
ou JSON entra na URL; parâmetro ausente, repetido ou fora do enum descarta o contexto;
troca de marca descarta o contexto; **o retorno nunca repropaga `ctx_*`**.

**Retorno à Inteligência** reconstrói **marca + lente/foco** — mapeando o `ctx_focus`
para a lente correspondente do bloco 6 e para o filtro local de marca — e chega
**frio**: sem `ctx_*` na URL de volta, porque voltar não é chegar quente.

### 9.2 Querystring canônica dos destinos

A convenção global de filtros usa **`brands`** (plural), não `brand`:
`FILTER_QUERY_KEYS = ["channels", "brands", "date_from", "date_to", "compare"]`. O
desenho anterior escrevia "`brand` + `pareto_bucket`" e estava fora da convenção.

#### Destino do Pareto → Produtos

```
/produtos?channels=ml&brands=<marca>&pareto_bucket=<bucket>
```

| Parâmetro | Regra |
|---|---|
| `channels=ml` | seleciona a **aba Mercado Livre** da página Produtos |
| `brands` | **uma única** marca ML válida (allowlist das marcas ML do payload) |
| `pareto_bucket` | somente bucket allowlisted: `A_top50`, `B_next30`, `C_next15`, `D_tail` |
| ausente, repetido ou inválido | **ignorado com segurança** — a página abre no estado padrão, sem erro |
| estado local | **inicializado pela URL** na primeira renderização |
| alteração posterior de filtro | **atualiza a URL** sem perder os parâmetros globais compatíveis |
| métrica | **nenhuma viaja** — nem valor, nem percentual, nem contagem |

**Distinção que precisa ficar explícita:** a **query da página** usa `brands` (plural,
convenção de filtros globais); o **parâmetro do endpoint** continua sendo `brand`
(singular) — `/api/v1/performance/produtos/ml?brand=…&pareto_bucket=…`. A página traduz
`brands` → `brand` ao montar a chamada. Nenhum dos dois nomes muda por causa do outro.

**Estado hoje:** a API já aceita e valida `pareto_bucket` contra `VALID_PARETO_BUCKETS`,
mas a página `/produtos` guarda o bucket em **estado local** e **não lê `searchParams`**.
O wiring é tarefa de frontend do V3-1A. **Nenhum endpoint novo.**

#### Retorno frio de `ctx_from=inteligencia`

O retorno **nunca** carrega `ctx_*` — voltar não é chegar quente. Cada foco reconstrói
marca **e** lente/âncora:

| `ctx_focus` | Retorno |
|---|---|
| `desperdicio_ads` | `/inteligencia?brands=<marca>&lens=parar#fila-evidencias` |
| `escala_ads` | `/inteligencia?brands=<marca>&lens=escalar#fila-evidencias` |
| `venda_organica` | `/inteligencia?brands=<marca>&lens=testar#fila-evidencias` |
| `concentracao` | `/inteligencia?brands=<marca>#concentracao` |
| `ltv` | `/inteligencia?brands=<marca>#ltv` |
| `produto_tiktok` | `/inteligencia?brands=<marca>#produtos-tiktok` |

Os três primeiros focos apontam para uma **lente** da fila de evidências; os três
últimos para a **âncora** do bloco correspondente, porque não há lente para eles.

#### Contrato de `lens`

| Regra | Valor |
|---|---|
| Allowlist | `parar`, `escalar`, `testar`, `todos` |
| Repetido ou inválido | resolve para **`todos`**, sem erro |
| Natureza | **estado local reproduzível da rota** — faz a URL restaurar a lente aberta |
| Não é `ctx_*` | não expira, não depende de origem, não é contexto quente |
| Propagação | **não entra em `FILTER_QUERY_KEYS`**, logo a sidebar **não** o propaga para outras páginas |
| Retorno | a URL de retorno contém `brands` e `lens`/âncora, e **nenhum `ctx_*`** |

**Regras que valem para toda a matriz**

- Todo elemento interativo responde algo material; nenhum abre um detalhe que só
  repete a linha.
- **Nenhuma linha inteira é clicável.** O acionamento é um botão ou link com nome
  acessível e alvo ≥44px (`DrilldownCta` já garante `min-h-11`).
- Elemento não interativo não recebe estilo de botão nem `hover` de linha.
- Inteligência usa **`ctx_focus`** próprio, nunca `ctx_signal` de Canais (§9.1);
  `ctx_from` ganha um único valor novo, `inteligencia`, **e só com wiring real** — a
  regra "não se cria enum sem wiring" do G3 §8 é respeitada.
- `product_id`/`item_id` **não viajam nesta fase** (§9.1): sem consumidor verificável
  na Marca, transportá-los seria antecipação.
- Qualidade de dado sempre em `DataQualityNote` separado do diagnóstico comercial.

---

## 10. Wireframes

### 10.1 Inteligência — desktop (≥1024px)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Inteligência                          [Portfólio ML · fotografia]    │  B1
│ Radar de portfólio e mídia            [TikTok · últimos 30 dias]     │  h=auto
├──────────────────────────────────────────────────────────────────────┤
│ [Todas as marcas][BARBOURS][KOKESHI][LESCENT][RITUÁRIA]  ← 4 marcas  │  h=44
├──────────────────────────────────────────────────────────────────────┤
│ PRIORIDADES                                                          │  B2
│ ┌────────────┐┌────────────┐┌────────────┐                           │  h=132
│ │ Desperdício││ Escalar    ││ Testar     │   ← 3-4 cartões-botão      │
│ │ R$ X · N p ││ N produtos ││ N produtos │      (grid 3 col)          │
│ │ "motivo…"  ││ "motivo…"  ││ "motivo…"  │                            │
│ └────────────┘└────────────┘└────────────┘                           │
├────────────────────────────────────┬─────────────────────────────────┤
│ MAPA DE OPORTUNIDADES        7/12  │ CONCENTRAÇÃO           5/12      │  B3+B4
│  GMV↑                              │ BARBOURS ▓▓▓▓▒▒░░  A 62%        │  h=380
│    │ Monitorar │ Escalar           │ KOKESHI  ▓▓▓▒▒▒░░  A 48%        │  (par
│    │───────────┼──────────         │ LESCENT  ▓▓▓▓▓▒░░  A 71%        │   alinhado)
│    │ Reduzir   │ Testar            │ RITUÁRIA ▓▓▒▒▒░░░  A 39%        │
│    └───────────────────→ ROAS      │ [sem ads: N produtos]           │
│  ⌐ sem_ads  ⌐ roas indisp. c/ Ads  │ [duas faixas, nunca fundidas]   │
├────────────────────────────────────┴─────────────────────────────────┤
│ PRODUTOS E MÍDIA          [ML · fotografia]                          │  B5
│ ┌── Maior desperdício ────────┐┌── Maior retorno ─────────────┐      │  h=260
│ │ produto ▓▓▓▓▓▓ R$ x  [Det.] ││ produto ▓▓▓▓▓ 14,2x  [Det.]  │      │
│ │ … 5 linhas · "5 de ao menos ││ … 5 linhas                   │      │
│ └─────────────────────────────┘└──────────────────────────────┘      │
├──────────────────────────────────────────────────────────────────────┤
│ FILA DE EVIDÊNCIAS                                                   │  B6
│ [Parar 30][Escalar 20][Testar 20][Todos]        scroll interno 520px │  h=560
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Marca │ Produto │ métricas da lente │ Pareto │ Vel. │ [Detalhe] │ │
│ │ … thead sticky dentro do card, sem hover na linha                │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ⚠ lista limitada a 30 desta lente                                    │
├──────────────────────────────────────────────────────────────────────┤
│ LTV & FIDELIZAÇÃO  [ML · fotografia]   │  PRÓXIMOS DESTINOS          │  B7
│ 4 linhas × 8 colunas         [Detalhe] │  → Canais → Produtos → Marca│  h=220
└──────────────────────────────────────────────────────────────────────┘
```

Grid 12 colunas. Blocos pareados (B3+B4, B7+destinos) usam `items-stretch` com
**altura mínima comum** e conteúdo alinhado ao topo — o par nunca fica com um lado
vazio e outro cheio. Só o bloco 6 tem scroll interno; a página nunca rola na horizontal.

### 10.2 Inteligência — mobile (<640px)

```
┌────────────────────────────┐
│ Inteligência               │
│ [ML · fotografia]          │  etiquetas empilhadas
│ [TikTok · 30 dias]         │
├────────────────────────────┤
│ marcas → scroll horizontal │  só a faixa de chips rola
│ dentro da própria faixa    │
├────────────────────────────┤
│ PRIORIDADES  1 col         │  cartões full-width
│ [Desperdício]              │
│ [Escalar] [Testar]         │
├────────────────────────────┤
│ CONCENTRAÇÃO  (antes)      │  ← a matriz DESCE no mobile:
│ barras full-width          │    quadrantes em 340px não são
├────────────────────────────┤    legíveis; barras são
│ MAPA (colapsado)           │    a leitura mobile útil
│ "Abrir mapa" → dialog      │
├────────────────────────────┤
│ PRODUTOS E MÍDIA  1 col    │  listas empilhadas, 3 linhas cada
├────────────────────────────┤
│ FILA — cartões, não tabela │  cada evidência = cartão com
│ ┌────────────────────────┐ │  2 métricas + [Detalhe]
│ │ produto                │ │
│ │ R$ x · 12 dias         │ │
│ │             [Detalhe]  │ │
│ └────────────────────────┘ │
├────────────────────────────┤
│ LTV → cartão por marca     │
│ DESTINOS → lista           │
└────────────────────────────┘
```

A tabela de 9 colunas **não** é comprimida no mobile: vira cartão por linha.

### 10.3 Marca — desktop

```
┌──────────────────────────────────────────────────────────────────────┐
│ ← Voltar para <origem>                                               │
│ ⓘ Você chegou por <motivo>  (banner, sem número)                     │  B1
├──────────────────────────────────────────────────────────────────────┤
│ ⬤ BARBOURS          [3 sinais com motivo]        [01–31/08 global]   │  B2
├──────────────────────────────────────────────────────────────────────┤
│ ══ TikTok Shop · análise mensal ═════════ [PeriodSelector: jul/2026] │
│ ⓘ a análise mensal cobre jul/2026; os blocos globais cobrem 01–31/08 │  nota
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ KPIs  [jul/2026]   6 cards em 2 linhas de 3                      │ │  B3
│ ├──────────────────────────────┬───────────────────────────────────┤ │
│ │ FUNIL POR CANAL   [jul/2026] │ PRODUTOS         [jul/2026]       │ │  B6+B8
│ │ 3 linhas × 6 col             │ top 5 · GMV/1k views  [Detalhe]   │ │  par alinhado
│ ├──────────────────────────────┴───────────────────────────────────┤ │
│ │ CONTEÚDO E CREATORS  [jul/2026]  ecossistema + freshness + top5  │ │  B7
│ └──────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────┤
│ EVOLUÇÃO E MIX   [01–31/08 global]                                   │  B4
│ DailyChart (8/12)                    │ ChannelMixChart (4/12)        │  h=320
├──────────────────────────────────────────────────────────────────────┤
│ O QUE MUDOU E POR QUÊ  [01–31/08 vs 01–31/07]                        │  B5
│ 3–5 linhas de decomposição com delta nomeado                         │  h=200
├──────────────────────────────────────────────────────────────────────┤
│ OPORTUNIDADES / RISCOS (2–4 cartões)  │  PRÓXIMOS PASSOS             │  B9+B10
└──────────────────────────────────────────────────────────────────────┘
```

O contêiner mensal é **visualmente cercado** (borda + faixa de título), para que o
regime temporal seja uma propriedade do contêiner, não de cada card.

### 10.4 Marca — mobile

```
┌────────────────────────────┐
│ ← Voltar                   │
│ ⓘ chegada (banner)         │
│ ⬤ BARBOURS                 │
│ sinais → empilhados        │
├────────────────────────────┤
│ ══ mensal · jul/2026 ═══   │  faixa sticky APENAS enquanto
│ ⓘ nota de escopo           │  o contêiner mensal está na tela
│ KPIs 2 col                 │
│ Funil → cartão por canal   │
│ Produtos → cartões         │
│ Conteúdo → 1 col           │
│ ═══════════════════════    │
├────────────────────────────┤
│ EVOLUÇÃO [global] gráfico  │  altura 220px, nunca 400
│ MIX → barras horizontais   │
│ O QUE MUDOU → lista        │
│ OPORTUNIDADES → cartões    │
│ PRÓXIMOS PASSOS → lista    │
└────────────────────────────┘
```

**Sticky:** só a faixa do regime mensal, e só dentro do próprio contêiner — é o único
lugar onde perder a referência temporal ao rolar causa erro de leitura. Nada mais é
sticky.

**Conteúdo mínimo / típico / máximo**

| Bloco | Mínimo | Típico | Máximo |
|---|---|---|---|
| Prioridades | 1 cartão | 3 | 4 (nunca mais) |
| Bloco 3 sem BE6 (listas) | 1 linha | 5–10 por faixa | limite da lista, sempre rotulado |
| Bloco 3 com BE6 (matriz) | 1 ponto → vira lista | definido por `opportunity_map` | `returned_count`, com truncamento declarado |
| Fila | 0 → estado vazio por lente | 20–30 | 30 + aviso |
| Funil | 1 canal | 3 | 3 |
| Produtos | 1 | 5 | 5 |
| Creators | 1 | 5 | 5 |
| Daily | 1 ponto → exibe valor, não gráfico | 28–31 | 92 (trimestre) |

Série de 1–2 pontos **não** vira gráfico grande: exibe os valores. Isso evita
"gráfico enorme com poucos pontos".

---

## 11. Estados

Válido para as duas páginas. Nenhum estado reutiliza dado antigo com filtro novo — a
regra `requestKey`/`resolvedKey` já existente é a garantia, e passa a valer **por
bloco**, não só por página.

| Estado | Gatilho | Comportamento |
|---|---|---|
| **loading** | `resolvedKey !== requestKey` | skeleton com a forma final; `aria-busy`; sem número antigo visível |
| **fresh/live** | chave resolvida e igual | dado + etiqueta de período + `refreshed_at` quando existir |
| **mock** | fallback sintético (Marca B4) | badge permanente **e** faixa no cabeçalho do bloco: "dados de exemplo — não usar para decisão". O gráfico recebe hachura de fundo. Nunca só um badge pequeno |
| **partial** | um bloco falha, outros não | aviso âmbar único no bloco que falhou, com "tentar novamente" próprio; os demais seguem frescos |
| **empty** | sucesso com 0 linhas | frase específica do bloco + a frase de honestidade existente ("não é modo demonstração") |
| **error** | falha definitiva | banner com retry por bloco; nenhum dado parcial anterior é mantido |
| **stale** | `refreshed_at` acima do limite da fonte | etiqueta de período em âmbar + "atualizado há Xh"; dado continua visível |
| **timeout** | resposta não chega no limite | tratado como `error`, com texto próprio ("a fonte não respondeu") |
| **null** | campo nulo | `—` com `title="sem dado"`. **Nunca 0, nunca vazio.** Zero é renderizado como `0` |
| **canal sem cobertura** | canal filtrado que a fonte não cobre | indicador neutro (não "erro", não "offline"), como já feito em Pedidos no U5 |
| **período sem dados** | competência sem linha | "sem dado para jul/2026" + competências que têm dado |
| **bloco parcial com endpoint OK** | payload chega com seção vazia | a seção declara vazio; o resto não é contaminado |

**Regra de ouro:** `null ≠ 0 ≠ vazio ≠ erro`. Quatro renderizações distintas.

---

## 12. Acessibilidade

- Piso tipográfico **12px** (`text-xs`). As 10 ocorrências de `text-[10px]`/`[11px]`
  em Inteligência e 13 em Marca são substituídas; nenhuma nova abaixo de 12px.
- Todo acionamento é `<button>` ou `<a>`, com nome acessível que identifica o objeto
  ("Detalhe de <produto>", não "Detalhe"), alvo ≥44×44px.
- `KpiDrilldownDialog` mantém `role="dialog"`, `aria-modal`, `aria-labelledby`, foco
  preso, Escape e restauração de foco. **Um nível de diálogo** — sem modal sobre modal.
- Região `aria-live="polite"` por página anunciando loading/erro/carregado (existente),
  estendida para anunciar troca de lente e de competência.
- `aria-busy` por bloco durante `loading`.
- Cor nunca é o único portador: quadrante tem rótulo; sinal tem texto; severidade tem
  palavra.
- Matriz e barras têm alternativa textual: tabela equivalente acessível por
  "ver como tabela" (a matriz é decisória, então precisa de par textual).
- Contraste mínimo AA em todo texto sobre preenchimento colorido (os badges de Pareto
  com fundo violeta claro são reavaliados).
- Navegação interna por âncora mantém `scroll-mt-24` e ordem de foco previsível.

---

## 13. Responsividade

| Faixa | Inteligência | Marca |
|---|---|---|
| <640 | 1 coluna; matriz colapsada em diálogo; fila em cartões | 1 coluna; funil/produtos em cartões; gráfico 220px |
| 640–1023 | 2 colunas nos pares; fila mantém tabela com scroll interno | KPIs 2 col; pares empilham |
| ≥1024 | grid 12; pares 7/12 + 5/12 | grid 12; 8/12 + 4/12 |
| ≥1536 | largura máxima de leitura fixa; não estica tabelas | idem |

Regras: a **página** nunca rola na horizontal; só tabelas e a faixa de chips rolam,
dentro do próprio contêiner (`TableScrollHint` existente). Blocos pareados alinham
altura por `items-stretch` + mínimo comum. Tablet (`md:`) é tratado explicitamente —
foi a lacuna clara da referência.

---

## 14. Performance

- `dynamic()` para a matriz de oportunidades, os dois gráficos de Marca e o conteúdo
  do diálogo — o padrão já usado em `RegioesBrazilMap`.
- Zero fetch novo em V3-1: os sete blocos de Inteligência vêm do **mesmo** payload de
  hoje. Marca mantém os dois fetches atuais.
- Cálculos derivados (prioridade, share de bucket, mediana **entre marcas** — que são
  entidades comparáveis, ao contrário das três superfícies TikTok) em `useMemo` sobre
  o payload já carregado — nenhum vai para o servidor.
- Nenhuma dependência nova. Exportação, se houver, é CSV sem biblioteca (padrão U4).
- O volume de pontos da matriz passa a ser o `returned_count` de `opportunity_map`, que
  o próprio contrato limita e declara. Nenhuma contagem é apresentada como universo
  completo sem `total_count` ao lado.

---

## 15. Tarefas de backend — só as realmente necessárias

Todas **aditivas**, nenhuma obrigatória para V3-1 começar. Um bloco degrada com
elegância enquanto o campo não existe.

| # | Mudança | Por que é necessária | Degradação sem ela |
|---|---|---|---|
| BE3 | `*_total_count` ao lado de `urgent`/`scale`/`organic` | as contagens exibidas hoje são as capadas (B5) | aviso "ao menos N" em vez do número verdadeiro |
| BE4 | `refreshed_at` no payload de Inteligência | a página não tem como declarar a idade da fotografia ML (B6) | etiqueta sem timestamp, nunca inventado |
| **BE5** | **`available_months` na resposta de `/brand-detail`** — **BLOQUEANTE do V3-2** | ver §15.2 | **não há degradação aceitável**: sem esta fonte, a página continua importando `AVAILABLE_MONTHS` do mock |
| **BE6** | **`opportunity_map` no payload de `/inteligencia`** — **BLOQUEANTE da matriz** | ver §15.1 | bloco 3 vira listas/faixas rotuladas como amostra capada (§7.3.1); **nenhuma matriz 2×2** |

### 15.0 Por que BE1 e BE2 saíram das entregas do V3

O desenho corrigido estabeleceu que **`item_id`/`product_id` não viajam pela URL nesta
fase**, porque a página de Marca não os consome (§9.1). Manter BE1 e BE2 como entregas
seria antecipar campos sem uso atual — exatamente o que o plano proíbe em outros pontos.

- **BE1 (`item_id` em `ProductSignalRow`) foi absorvida pelo BE6.** O identificador ML
  é necessário **dentro** do mapa de oportunidades, para os pontos de destaque, e por
  isso passa a fazer parte do contrato de `opportunity_map` (§15.1-D). Fora do mapa não
  há consumidor.
- **BE2 (`product_id` em `TkProductRow`) foi retirada e não entra neste gate**, e por um
  motivo mais forte que a ausência de consumidor: **ela não é puramente aditiva**.
  `tk_products` é agrupado por `(brand, product_name)`; acrescentar `product_id`
  exigiria mudar o `GROUP BY`, e com ele **o grão, as contagens e os valores** — dois
  `product_id` que hoje somam sob um mesmo nome passariam a ser linhas distintas.
  Migrar `tk_products` de agrupamento por **nome** para agrupamento por **`product_id`**
  é, portanto, **decisão futura separada**, com mudança de contrato e de grão, e não uma
  tarefa de suporte ao V3.

**Consequência para os diálogos das listas atuais.** O diálogo de uma linha de
`urgent`/`scale`/`organic`/`tk_products` opera sobre **a linha já carregada em memória**
— ele explica, mostra evidência e limitação, e oferece o CTA para a marca. O que ele
**não** é: compartilhável por identificador de produto. Abrir a URL noutro navegador
reproduz a página e a lente, não o diálogo de um produto específico. Isso é aceito nesta
fase e declarado, em vez de resolvido com um campo sem uso.

**Regra preservada:** nenhum título ou texto livre viaja na URL, em nenhuma hipótese.

### 15.1 BE6 — contrato conceitual de `opportunity_map`

Campo aditivo no payload de `/inteligencia`. Nenhum endpoint novo, nenhuma rota nova.
Conceitual: nomes finais são decisão da implementação.

O contrato tem **quatro partes deliberadamente separadas** — universo, quadrantes,
destaques e faixas — porque a versão anterior misturava agregado completo com ponto
exibido e deixava ambíguo se o gráfico mostrava todos os produtos ou uma amostra.

#### A. Universo

| Elemento | Exigência |
|---|---|
| Escopo | explicitamente **`ml_snapshot`** — declara que não há janela temporal |
| Universo elegível | regra escrita: produtos ML das marcas em escopo com `product_status` não nulo, **incluindo** `sells+advertised` de qualquer ROAS, `sells_organic_only`, `ad_spend_no_sales` e `inactive` |
| `total_count` | tamanho do **universo completo**, nunca dos pontos retornados |
| `roas_reference` | **8**, com **origem declarada**: é o corte que já existe no SQL de `scale` (`ad_roas >= 8`) — reaproveitado, não inventado |
| `gmv_reference` | **mediana do GMV positivo calculada sobre o universo completo**, jamais sobre pontos truncados. **Não** derivada de `product_status`, que distingue venda/Ads e não é limiar de volume |
| Natureza das referências | **estatística descritiva do portfólio, não meta comercial** — e o diálogo do quadrante exibe as duas com a origem escrita |

#### B. Mapeamento dos quadrantes e fronteiras

Sem decisão deixada para o implementador:

| ROAS | GMV | Quadrante |
|---|---|---|
| alto | alto | **`escalar`** |
| alto | baixo | **`testar_investimento`** |
| baixo | alto | **`monitorar`** |
| baixo | baixo | **`reduzir_parar`** |

Fronteiras **inclusivas no lado alto**:

- **ROAS alto** quando `roas >= roas_reference`;
- **GMV alto** quando `gmv >= gmv_reference`;
- qualquer outro valor numérico fica no **lado baixo**.

**Zero numérico continua sendo valor** e participa do quadrante correspondente:
`roas = 0` é retorno numérico baixo, não indisponibilidade. **`null` continua
indisponibilidade** e nunca entra num quadrante — vai para a faixa da parte E.

#### C. Agregados completos por quadrante

Calculados sobre **todo o universo classificado**, e **permanecem completos mesmo quando
os pontos visuais são limitados**:

| Campo | Significado |
|---|---|
| `count` | quantos produtos do universo caem no quadrante |
| `gmv` | GMV agregado do quadrante, universo inteiro |
| `ad_spend` | investimento agregado do quadrante, universo inteiro |

#### D. Pontos de destaque

Coleção **explicitamente separada** dos agregados:

| Elemento | Exigência |
|---|---|
| Natureza | **destaques**, não "todos os produtos" |
| Seleção | **determinística** (critério de ordenação declarado, sem empate resolvido por acaso) |
| Identificador | **`item_id`** ML estável — é aqui que o identificador é necessário, e é por isso que BE1 foi absorvida (§15.0) |
| Marca | presente |
| Título | **apenas no payload, nunca na URL** |
| `gmv`, `ad_spend`, `roas` | do produto |
| `quadrant` | chave allowlisted a que o ponto pertence |
| Critério de ordenação | declarado no payload, para a UI poder explicá-lo |
| Limite por quadrante | declarado |
| `returned_count` | quantos pontos vieram no total |

**Regra de interface:** quando `returned_count < total_count`, a UI diz que **os pontos
são destaques**, enquanto **os agregados dos quadrantes representam o universo
completo**. Os pontos truncados **nunca** são chamados de "todos os produtos".

#### E. Faixas fora dos quadrantes — duas, não uma

São diagnósticos diferentes e **não podem ser fundidos**:

| Faixa | Definição | Diagnóstico |
|---|---|---|
| **`sem_ads`** | `ad_spend = 0`, ou ausência de investimento conforme o contrato da fonte | não há mídia para julgar; candidato a teste |
| **`roas_indisponivel_com_investimento`** | investimento **positivo** e `roas` **`null`** | **há gasto e o retorno não é apurável** — é problema de mensuração, e potencialmente desperdício |

O motivo de separá-las é direto: **desperdício com Ads não pode desaparecer dentro de
uma faixa chamada "sem Ads"**. Cada faixa carrega `count`, `gmv` e `ad_spend` próprios.

E de novo, porque é a confusão mais fácil de cometer: **`roas` numérico igual a zero não
entra na faixa de indisponibilidade** — é retorno baixo e participa do quadrante
correspondente pela regra da parte B.

### 15.2 BE5 — contrato conceitual de `available_months`

Campo aditivo na resposta de `/brand-detail`. **Endpoint separado não se justifica**:
a informação pertence à marca que já está sendo consultada.

Por que é bloqueante e não conveniência: `fetchBrandDetail(brand, period)` devolve
**apenas o mês pedido**, e a coleção `daily` dessa resposta contém somente dias desse
mês. **Não existe como derivar dela os outros meses disponíveis.** Sem uma fonte de
disponibilidade, o seletor de competência continua dependendo de `AVAILABLE_MONTHS` do
mock — que é exatamente o defeito M2.

| Elemento | Exigência |
|---|---|
| Conteúdo | meses com **dado real para a marca consultada**, não uma lista global |
| Ordenação | **decrescente** (mais recente primeiro) |
| Formato | canônico **`YYYY-MM`** |
| Competência selecionada | ecoada na resposta, para a UI confirmar o que foi servido |
| Competência pedida indisponível | resposta explícita — a UI mostra "sem dado para `<mês>`" **e** as competências que têm dado; nunca cai silenciosamente noutro mês |
| Vazio | marca sem nenhum mês ⇒ lista vazia e estado `empty` próprio, sem seletor |
| Frontend | **zero import de `AVAILABLE_MONTHS`** ou de qualquer módulo mock na página final |

Facilitador já existente: `PeriodSelector` **já aceita uma prop `months`** (o default é
a constante do mock, e o próprio componente documenta que páginas com meses dinâmicos
passam a sua lista). Assim que `available_months` existir, a mudança de frontend é
passar a prop — não há refatoração de componente.

**Fora de escopo, registrado:** agrupar `tk_products` por `product_id` em vez de nome
altera contrato de payload e contagem — decisão separada. Série mensal multi-mês da
marca (para headroom vs melhor mês próprio) exigiria endpoint novo.

---

## 16. Escopo e anti-escopo

**No escopo:** `/inteligencia`, `/brand/[brand]`, os acionamentos da matriz §9, o
contêiner de regime temporal, os estados §11, tipografia e a11y das duas páginas,
extensão de `ctx_from` com um valor e wiring real.

**Fora do escopo:** `/tempo-real`; `/canais` e `/produtos` além de receber navegação;
`/regioes` e o mapa clicável; GMV TikTok com frete (decisão aberta); qualquer bloco de
Ads em Marca; demografia; margem/CMV; registry de drill-down; endpoint novo; rota
nova; dependência nova; alteração de `DESIGN.md`; deploy; Scheduler; pipeline.

---

## 17. Riscos

| # | Risco | Mitigação |
|---|---|---|
| R1 | A matriz vira decoração — ou pior, mente sobre cobertura | resolvido por dependência: sem **BE6** não há matriz, só listas rotuladas como amostra capada (§7.3.1). Com BE6, o eixo de ROAS reaproveita o corte existente (`ad_roas >= 8`) e o de GMV usa a **mediana do GMV positivo do universo**, nunca `product_status`; as duas referências aparecem no diálogo com a origem escrita |
| R9 | BE6 ou BE5 não serem priorizados, travando V3-1B e V3-2 | V3-1A entrega valor sozinho (rituária, falsas affordances, fila, estados, tipografia) e a degradação da matriz está especificada; a Marca **não** é aceita sem BE5, e isso está no critério de saída |
| R10 | `ctx_focus` divergir dos blocos reais se o payload mudar | os seis focos derivam dos blocos **navegáveis** do payload (`signals` é agregado de suporte e não gera foco); bloco novo exige foco novo **com wiring**, e bloco removido invalida o foco |
| R11 | Destaques do mapa serem lidos como o universo | `total_count` e `returned_count` viajam juntos, e a UI é obrigada a rotular os pontos como **destaques** quando truncados (§15.1-D) |
| R12 | Desperdício com Ads desaparecer numa faixa "sem Ads" | as duas faixas são obrigatoriamente separadas no contrato (§15.1-E), com agregados próprios |
| R2 | O contêiner mensal ainda pode ser lido como o período da página | borda + faixa + etiqueta por bloco + nota de não-sobreposição; e a formatação de intervalo é visualmente distinta da de competência |
| R3 | Corrigir B4 (rituária) pode mudar números que alguém já reportou | é correção de cobertura, não de cálculo; registrar no release que a marca passou a aparecer |
| R4 | `*_total_count` revelar volumes muito maiores que os 30 exibidos | é o objetivo; o aviso de truncamento passa a ser honesto |
| R5 | Remover "Demographics" pode parecer perda de recurso | a seção nunca teve dado; a limitação passa a ser declarada em texto |
| R6 | Fallback sintético continuar em produção | a sinalização de `mock` fica no cabeçalho do bloco, não só num badge |
| R7 | Aumentar acionamentos aumenta superfície de a11y | todo acionamento nasce com nome acessível e ≥44px, verificado no V3-3 |
| R8 | Novo `ctx_from` propagar contexto onde não deve | `ctx_*` segue fora de `FILTER_QUERY_KEYS`; teste dedicado |

---

## 18. Critérios de aceite do desenho

1. Cada um dos sete blocos de Inteligência e dos dez de Marca responde **uma**
   pergunta declarada — §7 e §8.1. ✔
2. Cada acionamento da matriz §9 tem explicação, evidência, limitação e próximo passo. ✔
3. Nenhuma métrica inventada: toda métrica de §7/§8 sai da §5, e o que não existe está
   marcado "indisponível — não prometer". ✔
4. Períodos distintos são visualmente inequívocos: duas formatações, contêiner cercado,
   etiqueta por bloco, nota de não-sobreposição. ✔
5. Inteligência deixa de ser coleção de tabelas: de 5 tabelas para **1** tabela densa
   + 1 matriz + 1 barra + 2 listas compactas + 1 tabela de 4 linhas. ✔
6. Marca ganha hierarquia em quatro camadas com contêiner de regime. ✔
7. Supera a referência em rastreabilidade e confiança: URL allowlisted (a referência
   tem zero), diálogo acessível (zero na referência), `null` preservado (a referência
   colapsa zero e nulo), limitação declarada por bloco. ✔
8. Reaproveita G2/G3/V2: `KpiDrilldownDialog`, `DrilldownContextLine`, `EvidenceRow`,
   `DataQualityNote`, `DrilldownCta`, `BrandArrivalBanner`, `KpiCard`, `DailyChart`,
   `ChannelMixChart`, `SortableHeader`, `TableScrollHint`, `PeriodSelector`,
   `DateRangeFilter`, `computeRequestStatus`, `mergeFilteredHref`. Um componente novo
   de composição: `DrilldownMetricPair` (já previsto no G2 §3.1). ✔
9. Nenhuma dependência, endpoint ou rota criada antecipadamente. ✔
10. Implementável em fatias, **com as dependências declaradas**: a fundação da
    Inteligência (V3-1A) usa o payload atual; a **matriz definitiva depende de BE6** e a
    **Marca depende de BE5**. O critério anterior — "V3-1 não depende de backend" — era
    falso e foi removido. ✔
11. Nenhuma visualização afirma cobrir o portfólio sem um contrato que entregue universo
    e `total_count`. ✔
12. Nenhum corte de GMV é atribuído a `product_status`. ✔
13. Nenhum drill-down promete lista que o payload não carrega. ✔
14. `ctx_signal` de Canais não é reutilizado com outro significado. ✔
15. Nenhum campo de backend é pedido sem consumidor: **BE1 e BE2 foram retiradas**, e o
    `item_id` necessário vive dentro do contrato BE6. ✔
16. Mudança de grão não é disfarçada de campo aditivo: `product_id` em `tk_products` é
    **decisão futura separada**. ✔
17. Os quatro quadrantes e as fronteiras `>=` estão definidos sem deixar escolha ao
    implementador; **`0` é valor e `null` é indisponibilidade**. ✔
18. **Agregados do universo** e **pontos de destaque** são coleções distintas, e a UI é
    obrigada a dizer qual está mostrando. ✔
19. `sem_ads` e `roas_indisponivel_com_investimento` são faixas separadas — desperdício
    com Ads não desaparece. ✔
20. Toda navegação usa a querystring canônica (`channels`, `brands`, `pareto_bucket`,
    `lens`), com a distinção `brands` (página) × `brand` (endpoint) declarada. ✔

---

## 19. Roadmap

Três tasks operacionais, com as dependências internas explícitas. O V3-1 tem duas
etapas porque a matriz definitiva **não era implementável** com o payload de
18/08 — a divisão refletiu uma dependência real, não um subgate para inflar
processo. A dependência foi resolvida pelo **BE6**, entregue no V3-BE.

| Fase | Conteúdo | Depende de | Critério de saída |
|---|---|---|---|
| **V3-0** | Este desenho, corrigido nos sete findings | — | **CONCLUÍDO — DESENHO APROVADO** pelo proprietário em 18/08/2026 |
| **V3-1A** — fundação da Inteligência ✅ **VERSIONADO E PUBLICADO (`e675948`)** | Cabeçalho de regimes (ML fotografia × TikTok 30 dias); **rituária** derivada do payload; remoção das duas falsas affordances (`<span>` de botão e hover de linha); concentração Pareto com diálogo **só de agregados** + CTA para `/produtos`; **wiring da querystring canônica `/produtos?channels=ml&brands=…&pareto_bucket=…` (§9.2)** e de `lens` em `/inteligencia`; listas e fila honestamente truncadas ("amostra capada"); fila com lentes substituindo as três tabelas; LTV filtrável; os 13 estados; tipografia ≥12px; bloco 3 **como listas/faixas**, sem matriz | **payload atual** | 5 tabelas → 1; zero falsa affordance; rituária visível; nenhuma contagem apresentada como total; `/produtos` reproduzível por URL na forma canônica; `lens` com allowlist e sem propagação pela sidebar; suíte + typecheck + build verdes |
| **contrato backend** ✅ **VERSIONADO NO V3-BE (`26434c8`) E CONFIRMADO EM PRODUÇÃO** | **BE6** `opportunity_map` — universo + `total_count` + as duas referências com origem + quadrantes com fronteiras + agregados completos + destaques com `item_id` e `returned_count` + **as duas faixas** (§15.1); **BE3** contagens verdadeiras; **BE4** frescor do snapshot; **BE5** `available_months` (§15.2). **BE1 e BE2 saíram** (§15.0) | — | contratos aceitos e servidos; nenhum campo entregue sem consumidor |
| **V3-1B** — conclusão da Inteligência ✅ **VERSIONADO (`2d7ecdf`), QA VISUAL FINAL 96/96** | **Matriz 2×2 definitiva**: quatro quadrantes com fronteiras `>=` explícitas, as duas referências exibidas com origem, **agregados do universo separados dos pontos de destaque**, e as **duas faixas** (`sem_ads` e `roas_indisponivel_com_investimento`) como blocos distintos; contagens verdadeiras substituindo "ao menos N"; etiqueta de frescor com timestamp | **BE6** (matriz), BE3, BE4 | nenhuma afirmação de cobertura sem `total_count`; nenhum eixo derivado de `product_status`; pontos truncados **nunca** rotulados como "todos os produtos"; desperdício com Ads visível na sua própria faixa |
| **V3-2** — Marca 360 ✅ **IMPLEMENTADO — AGUARDANDO REVISÃO (§27)** | Contêiner de regime mensal cercado + nota de não-sobreposição; `ctx_from=inteligencia` com `ctx_focus` próprio (§9.1); remoção de Demographics; **competências reais** via `available_months`; sinalização de mock no cabeçalho do bloco; guarda de identidade do `brandDetail`; drill-downs de canal e produto **sem benchmark entre superfícies** | V3-1A/B (produtor do contexto) **e obrigatoriamente BE5** | dois regimes inequívocos; nenhuma seção sem etiqueta de período; **zero import de módulo mock**; volta à evidência reconstruindo marca + foco, sem repropagar `ctx_*` |
| **V3-3** — QA integrado ✅ **EXECUTADO EM NAVEGADOR REAL — 248/260, AGUARDANDO REVISÃO (§28)** | a11y (foco, nome acessível, contraste, ≥12px), responsivo em desktop/tablet/mobile, os 13 estados, matriz de drill-down acionamento a acionamento, regressões | V3-1, V3-2 | zero scroll horizontal de página; zero elemento clicável sem affordance e sem nome acessível; zero par de blocos com vazio desproporcional |

**Dependências que não podem ser contornadas:** a matriz sem BE6 não existe (degrada
para listas, §7.3.1); a Marca sem BE5 não pode ser aceita como "competências sem
mock". **As duas foram entregues e versionadas no V3-BE (`26434c8`)**, então nenhuma
delas bloqueia mais nada.

**V3-1A, PF1, V3-BE e V3-1B estão versionados** (registro do V3-1A em §20; QA em
§20.9; patch de acessibilidade em §22; V3-BE em §23; V3-1B em §24, §25 e §26,
versionado em `2d7ecdf`). **O V3-2 (§27) e o V3-3 (§28) foram tecnicamente
concluídos e versionados no commit de fechamento do Gate V3** — nenhuma fase
permanece aberta do ponto de vista técnico. O que resta é **publicação e smoke
pós-deploy**, que não são deste gate.

---

## 20. Registro do V3-1A — Task 1/2 (implementada em 18/08/2026)

**Estado em 18/08/2026, quando este registro foi escrito: `IMPLEMENTADO, QA VISUAL
EXECUTADO E A11Y FECHADA — AGUARDANDO REVISÃO`. Hoje o V3-1A está VERSIONADO E
PUBLICADO em `e675948`.** Este
registro descreve a **Task 1/2**, durante a qual nenhum navegador foi usado, nem como
apoio de implementação. O QA visual formal foi executado depois, na **Task 2/2**
(18/08/2026), e está registrado no §20.9.

### 20.1 Arquivos

**Criados** — módulos puros (sem React, testáveis com `node:test`):

| Arquivo | Papel |
|---|---|
| `src/lib/inteligencia/brands.ts` | marcas ML **derivadas do payload**, seleção local, filtro por marca |
| `src/lib/inteligencia/lens.ts` | contrato de `lens` (allowlist, parse, construção de href, âncoras) |
| `src/lib/inteligencia/queue.ts` | união discriminada das três listas, ordenação e notas de amostra |
| `src/lib/inteligencia/priorities.ts` | cartões de prioridade e contagem por origem |
| `src/lib/inteligencia/pareto.ts` | concentração por marca, share do bucket, href canônico |
| `src/lib/produtos-url.ts` | contrato canônico de querystring de `/produtos` |

**Componentes criados:** `components/inteligencia/PriorityCards.tsx`,
`ConcentrationBars.tsx`, `EvidenceQueue.tsx`, e
`components/drilldown/DrilldownMetricPair.tsx` — a única peça de composição prevista
no G2 §3.1 que nunca havia sido construída, agora com o primeiro consumidor real.

**Reescritos:** `app/inteligencia/page.tsx` (sete blocos).
**Tocado só para o wiring de URL:** `app/produtos/page.tsx`.

### 20.2 O que usa o payload atual

**Tudo.** Nenhum fetch novo, nenhum endpoint novo, nenhuma dependência nova: a página
continua com **uma única** chamada a `fetchInteligencia()`, e prioridades, faixas,
concentração, listas compactas, fila e LTV são derivações puras (`useMemo`) sobre esse
mesmo payload.

### 20.3 O que permanece bloqueado

- **A matriz 2×2 definitiva continua bloqueada por BE6.** O bloco 3 foi entregue como
  **faixas de amostra priorizada** — sem scatter, sem eixo, sem quadrante, sem mediana
  de subconjunto, e sem a faixa `roas_indisponivel_com_investimento`. Cada faixa declara
  a regra que a formou e o limite conhecido da lista. Verificado por teste sobre o
  código, não sobre a prosa.
- **Marca 360 continua bloqueada por BE5 e é escopo do V3-2.** Nenhum `ctx_*` é
  produzido: os destinos de marca são **frios** (`/brand/<marca>?brands=<marca>`).
- **Contagens verdadeiras dependem de BE3**; **frescor com timestamp depende de BE4**.
  Enquanto não existirem, a etiqueta ML diz literalmente "fotografia do último
  carregamento" e as contagens são sempre rotuladas como amostra.

### 20.4 Contratos de URL implementados

`/inteligencia?brands=<marca>&lens=parar|escalar|testar|todos` — `lens` ausente,
repetido ou inválido resolve para `todos`; a lente padrão é omitida da URL; `ctx_*` é
descartado na construção do href.

`/produtos?channels=ml&brands=<marca>&pareto_bucket=<bucket>` — parsing e construção
centralizados em `produtos-url.ts`, com allowlist de canal, marca **validada contra as
marcas da aba** e bucket allowlisted. Parâmetro ausente, repetido, vazio, inválido ou
incompatível com o canal é ignorado com segurança. A página fala `brands` (plural); o
endpoint continua recebendo `brand` (singular), e a tradução é explícita em
`brandParamForEndpoint`. `lens` e `pareto_bucket` ficam fora de `FILTER_QUERY_KEYS`, e
nenhuma das duas páginas é filter-aware — a sidebar não os propaga.

### 20.4b Correção consolidada pré-QA (18/08/2026)

Cinco findings da revisão, todos fechados. Os dois primeiros eram defeitos reais de
comportamento, não de estilo.

**1 — `buildQueue` apagava linhas reais (bloqueador).** O módulo deduplicava por
`(brand, title)` "como guarda". Só que o grão da tabela-fonte é `(brand, item_id)` e o
payload **não** entrega `item_id`: duas linhas com o mesmo título podem ser dois
produtos, e o frontend não tem como provar o contrário. Contraprova reproduzida antes
da correção — duas linhas de `urgent` com o mesmo título e ad spend 100 e 200 viravam
**uma** linha e uma soma de **R$ 100 em vez de 300**. Perda silenciosa numa cifra
monetária, propagada para fila, contagens por lente, cartões de prioridade, somas e
listas compactas. Correção: concatenação pura na ordem de `KIND_ORDER`, com `kind` como
única adição, **zero deduplicação**; `evidenceKey` foi **removida** por não ter uso
honesto. Quatro testes novos provam preservação: mesma lista, origens diferentes,
contagens/somas exatas em todas as lentes.

**2 — loading estava sendo apresentado como vazio.** `status.loading` caía no ramo do
conteúdo normal; com `displayData` nulo, a página dizia "Nenhuma prioridade…", "Sem
dados TikTok…" e "Sem dados de LTV…" **antes de a requisição terminar** — e o texto de
vazio afirma inclusive "não é modo demonstração", o que agrava a falsidade. Agora existe
`InteligenciaSkeleton` com `role="status"`/`aria-busy`, sem valor, sem contagem zero, sem
texto de vazio e sem controle acionável, num ramo que **precede** error e
indisponibilidade. Chips e cartões ficam `disabled` durante o carregamento.

**3 — bloco 5 não cumpria o contrato drill-down-driven do §7.5.** Cada linha das duas
listas ML ganhou botão "Detalhe" com nome acessível (produto + marca, ≥44px) abrindo o
**diálogo único**; cada lista ganhou "Ver todos na fila →" apontando para a lente certa
com `#fila-evidencias` e a marca preservada, sem `ctx_*` e sem métrica na URL. A redação
de truncamento passou a `listSampleNote`: **"5 de ao menos 30"** quando a lista bateu o
LIMIT, "5 de N registros recebidos" quando ficou abaixo. O card TikTok declara o próprio
teto (`TK_PRODUCTS_LIMIT = 25`) e diz que "3 no mobile é apresentação, não cobertura".

**4 — suíte inteira verde.** As duas falhas pré-existentes foram corrigidas **nos
testes**, sem tocar produção: `gerencial-v2.test.ts` F5 deixou de exigir que o
`PROJECT_STATUS.md` repetisse uma frase histórica (o SPEC é a fonte durável dos 16
acionamentos; a contraprova de que "doze caminhos" não voltou permanece), e
`pedidos-shopee-only-semantics.test.ts` normaliza a fonte para LF uma única vez —
`app/pedidos/page.tsx` **não** foi alterado.

**5 — semântica da tabela LTV.** A coluna de ação tinha `<th>` rotulado "Marca",
duplicando a primeira coluna. Passou a `<th scope="col">Detalhe</th>`. Métricas e
navegação inalteradas.

**Coerência do wiring de Produtos:** os **seis** pontos de request passaram a usar
`brandParamForEndpoint(brand)`. A afirmação de centralização deixou de ser retórica —
zero tradução inline sobrou, e um teste trava os dois números.

### 20.5 Testes e validações

Três arquivos novos, **65 testes**: `inteligencia-v3.test.ts` (43),
`produtos-url.test.ts` (parsing e construção), `inteligencia-v3-wiring.test.ts` (22
contratos de JSX que o harness sem DOM não consegue renderizar).

| Validação | Resultado |
|---|---|
| `npm test` | **690 pass / 0 fail** — suíte inteira verde, incluindo as duas falhas antes pré-existentes |
| `npm run typecheck` | limpo |
| `npm run build` | sucesso; `/inteligencia` 11,8 kB e `/produtos` 11,2 kB, ambas estáticas |
| `git diff --check` | limpo |
| Scan de secrets/DSN/token/IP/PII/caminho pessoal | 0 em 2.718 linhas novas |
| Diff em `apps/api`, `pipelines`, `db`, migrations, `package-lock.json` | **zero** |

**Dois testes preexistentes foram atualizados**, os dois porque codificavam
comportamento que o desenho V3-0 rejeitou ou copy que virou contrato:

- `u5-resolvedkey-wiring.test.ts` exigia que o filtro de marca alcançasse **apenas**
  `urgent/scale/organic` e "nunca pareto/ltv" — exatamente o defeito **B9** da
  auditoria. Reescrito para o contrato aprovado: a seleção alcança **todos** os blocos
  ML, e nunca o bloco TikTok. A inversão está documentada no próprio teste.
- `v22-propagacao-visual.test.ts` (P20) casava com uma **frase literal** da nota de
  escopo. Passou a verificar a **garantia**: ausência de `useGlobalFilters` mais uma
  declaração explícita ao leitor, em qualquer redação.

### 20.6 As duas falhas que eram pré-existentes (agora corrigidas nos testes)

| Teste | Causa | Prova |
|---|---|---|
| `gerencial-v2.test.ts` F5 | exige `dezesseis tipos de acionamento` em `PROJECT_STATUS.md`; a frase saiu no Revamp V2 (`04d0d17`) e o teste nunca foi atualizado | 0 ocorrências em `a1c5ffe`, `d04306e`, `3998af5`, `a5bbbdd` e `309b6bf` — todos anteriores a esta task |
| `pedidos-shopee-only-semantics.test.ts` | marcador multilinha com `\n` contra arquivo em **CRLF** (`core.autocrlf=true`, sem `.gitattributes`) | `app/pedidos/page.tsx` e o teste são **byte-idênticos ao HEAD**; nenhum dos dois foi tocado |

As duas foram corrigidas **na camada de teste**, sem tocar código de produção:
`app/pedidos/page.tsx` permanece byte-idêntico ao HEAD, e o SPEC da Gerencial passou a
ser a fonte durável da contagem em vez de uma frase repetida num documento volátil. A
sensibilidade a fim de linha do repositório (`core.autocrlf=true` sem `.gitattributes`)
continua sendo uma dívida própria: aqui ela foi neutralizada no ponto de leitura, não na
raiz.

### 20.7 Findings corrigidos durante a implementação

- O `evidenceKey` foi escrito com um **byte NUL literal** como separador, o que tornava
  o módulo um arquivo binário para o git e o grep. Trocado por `\u0000` escapado — o
  separador NUL é mantido de propósito, porque evita colisão entre `("a b","c")` e
  `("a","b c")`.
- Quatro asserções minhas casavam com a **prosa que explica a proibição** em vez de com
  o código (a página diz "não há matriz, eixo, quadrante nem mediana"). Passaram a usar
  um `codeOnly` que remove comentários, e a verificar identificadores de
  implementação — não palavras.
- O piso de 12px foi escopado corretamente: **estrito** nos cinco arquivos autorais, e
  em `/produtos` a asserção garante que o wiring **não acrescentou** nenhuma
  ocorrência (as três existentes são anteriores e ficam como dívida registrada).

### 20.8 Riscos remanescentes

1. **QA visual EXECUTADO** em 18/08/2026 (§20.9), em navegador real. Os **4 achados
   de alvo/tipografia (A1–A4) foram FECHADOS** no patch terminal de 19/08/2026
   (§20.10), com medição em navegador nos três viewports. Resta **1 achado de
   plataforma fora de escopo**: `withCache` memoiza a falha por 5 min, encaminhado
   para o gate próprio **PF1**, antes do V3-1B.
2. **Três `text-[10px]` em `/produtos`** permanecem, anteriores a esta task; o redesenho
   visual daquela página não está no escopo do V3-1A.
3. **`EvidenceRow` usa `text-[10px]`** na sub-linha de referência. O componente é do G2
   e é compartilhado por Canais e Gerencial; para não regredir aquelas telas, ele não
   foi tocado, e os diálogos desta página **não usam** a prop `reference`, de modo que o
   caminho de 10px não é renderizado aqui.
4. **Visitar `/produtos` sem querystring passa a reescrever a URL** para
   `?channels=ml` na primeira renderização. É o preço de tornar o estado reproduzível;
   estável, sem laço de render.
5. As duas falhas de teste pré-existentes seguem vermelhas na suíte.

### 20.9 QA visual do V3-1A — Task 2/2 (executada em 18/08/2026)

**Estado: `QA VISUAL EXECUTADO — 1 CORREÇÃO APLICADA, 4 ACHADOS ABERTOS`.**

O QA foi executado **em navegador real**, não simulado. Playwright 1.62.1 veio do
cache do `npx` já presente na máquina e dirigiu o **Chromium 149.0.7827.55** do cache
`ms-playwright` via `executablePath` — **nenhuma dependência foi instalada** e
`package-lock.json` permanece intocado. Todo artefato (scripts, screenshots, JSON de
resultados, log do servidor) ficou em `%TEMP%`; nada entrou no repositório.

#### Método

O backend real não estava alcançável, então a matriz de estados foi provada por
**interceptação de rede determinística** (`page.route`) sobre a build de produção
(`next build` + `next start`, porta local). Isso é o oposto de fabricar dado para
encobrir ausência de backend: o payload injetado é um **fixture declarado**, cada
número esperado foi calculado à mão antes de olhar a tela, e o relatório distingue o
que foi provado por renderização do que foi provado por leitura de código.

Viewports: **1440×900**, **1024×768** e **390×844**, com screenshot inspecionado em
cada um.

#### O que foi provado por comportamento renderizado

- **Preservação da fila (o bug que a rodada pré-QA corrigiu).** O fixture tem duas
  linhas com o mesmo par `(marca, título)` dentro de `urgent` e uma colisão
  `barbours/"COLIDE"` entre `urgent` e `scale` — exatamente o que o dedup removido
  destruía. Renderizou **6 linhas para 6 linhas de fonte**, duas "DUPLICADO" e duas
  "COLIDE", na ordem `parar→parar→parar→escalar→escalar→testar`; abas
  `{Parar 3, Escalar 2, Testar 1, Todos 6}`; somas dos cartões R$ 700 / R$ 4K / R$ 800
  contra 700 / 4.000 / 800 calculados à mão. Nenhuma linha perdida, nenhuma soma
  corrompida.
- **`null` distinto de zero.** A linha `kokeshi` do LTV tem `repeat_rate_pct: 0` real e
  os demais campos `null`: renderizou `0,0%` para o zero e `—` para os nulos, nunca
  `0` para ausência.
- **Estados.** *loading* mostra esqueleto com `role="status"`/`aria-busy`, sem valor e
  sem controle habilitado; *fresh sem dado* mostra vazio explícito; a **precedência**
  entre esqueleto, erro e vazio foi verificada na renderização, não no código.
- **Querystring.** `lens` nos quatro valores, `reload`, `back`/`forward`, valor inválido
  (`matriz`) e repetido caindo em `todos`, e **zero link de navegação carregando
  `lens`** — a sidebar não propaga estado de tela.
- **Rota canônica para `/produtos`.** O CTA do bucket gera
  `/produtos?channels=ml&brands=barbours&pareto_bucket=A_top50`, sem métrica e sem
  `ctx_*`; o request ao endpoint sai com **`brand=barbours` no singular** e
  `pareto_bucket=A_top50`; `reload` reproduz o estado; bucket inválido (`E_outro`) e
  marca não-ML (`apice`) são descartados da URL.
- **Drill-downs.** Cartões de prioridade, listas compactas ML, Pareto, fila e LTV:
  nome acessível específico, um único diálogo, foco inicial no fechar, *focus trap*,
  `Escape` e clique fora fechando, e **foco devolvido ao elemento de origem**. O bloco
  TikTok tem **zero acionáveis** — o payload não traz identificador de produto, e a
  tela não finge que traz.
- **Truncamento honesto.** As quatro redações de amostra apareceram na tela, e os
  limites foram provados no *off-by-one* (29/31).
- **Console e rede.** 849 requests, **somente** para o host local, **zero** request de
  escrita, **zero** erro de console ou de página em toda a bateria.

#### Correção aplicada — rodada única consolidada

**Separador decimal errado para pt-BR.** Todo percentual, ROAS e nota renderizava com
**ponto**: `12.0x`, `0.0%`, `8.0%`, `4.5`. A causa é `Number.toFixed()`, insensível a
locale. Na mesma tela `fmtBrl`/`fmtNumber` já usavam pt-BR, então a página misturava
duas convenções numéricas numa interface brasileira — e não havia um único número com
vírgula. Corrigido com `src/lib/inteligencia/format.ts`
(`decBr`/`pctBr`/`roasBr`/`fractionAsPctBr`) aplicado nos **14 pontos** de
`page.tsx` (9), `EvidenceQueue.tsx` (4) e `ConcentrationBars.tsx` (1). A correção é
**só de apresentação**: mesma quantidade de casas decimais, nenhum cálculo, métrica,
threshold ou arredondamento de negócio alterado. `src/lib/formatters.ts` **não** foi
tocado — o `fmtPct` de lá é formatador de *delta* (prefixa `+`) e é compartilhado por
outras telas. Cinco testes focais travam a convenção, incluindo um que reprova qualquer
`toFixed` que volte aos quatro arquivos visuais do V3-1A.

#### Achados abertos — não corrigidos, por disciplina de escopo

A rodada consolidada única já havia sido gasta na correção acima quando a fase de
responsividade rodou. Abrir uma segunda onda contrariaria o contrato da task, então os
achados abaixo ficaram **classificados e com a correção pronta**. O proprietário autorizou explicitamente uma segunda correção estreita, e **os quatro foram fechados no §20.10**.
Nenhum é bloqueante: todos passam o mínimo AA de alvo (WCAG 2.5.8, 24×24 CSS px).

| # | Achado | Origem | Local | Correção proposta |
|---|---|---|---|---|
| A1 ✅ **FECHADO (§20.10)** | Navegação interna de seções com alvo de **24px** de altura (`px-2.5 py-1 text-xs`) — abaixo dos 44px do §12, e é a navegação principal da tela no mobile de 390px | **introduzido pelo V3-1A** | `app/inteligencia/page.tsx`, `<nav>` de âncoras | subir para `py-2.5` ou `min-h-11` |
| A2 | Glifo de ordenação a `text-[10px]` | **atribuição corrigida no §20.10: é do `SortableHeader` COMPARTILHADO, preexistente ao V3-1A**, e não de `EvidenceQueue.tsx` | `src/components/SortableHeader.tsx` | `text-[10px]` para `text-xs` — **FECHADO no §20.10** |
| A3 ✅ **FECHADO (§20.10)** | Segmentos da barra Pareto com **30px** de altura (`h-8`) | introduzido pelo V3-1A, por desenho | `ConcentrationBars.tsx` | aceitável: barra empilhada é faixa fina, os alvos têm 330–1100px de largura e passam o AA |
| A4 ✅ **FECHADO (§20.10)** | Cabeçalhos ordenáveis com **40px** de altura | introduzido pelo V3-1A | `EvidenceQueue.tsx`, `#ltv` | aceitável: controle secundário, 97–252px de largura, passa o AA |

#### Achado real fora do escopo — não corrigido

**`withCache` memoiza a falha por 5 minutos.** `apiFetch` captura todo erro e devolve
`null`; `withCache` guarda esse `null` sob `CACHE_TTL = 5 * 60 * 1000`. Consequência
observada: depois de uma falha de rede, o botão **"Tentar novamente" é inerte por cinco
minutos** — a tela repete a indisponibilidade sem tocar a rede. O defeito está em
`src/lib/api-client.ts`, **fora da lista de arquivos permitidos** desta task, e é
**anterior ao V3-1A** (afeta todas as telas que usam o cache). Fica registrado como
dívida de plataforma, não como regressão deste gate.

#### Dívidas preexistentes — apenas classificadas

As três ocorrências de `text-[10px]` em `/produtos` e a sub-linha de `EvidenceRow`
seguem como no §20.8: **não são renderizadas nem introduzidas pelo V3-1A** e não foram
tocadas. Os três avisos `gray-on-color` do detector nas linhas de classes condicionais
dos chips permanecem **falsos positivos** — o `text-slate-600` é o ramo do ternário que
só se aplica a fundo claro; nenhum ternário foi alterado para silenciar o detector.

#### Validações reexecutadas depois da correção

`npm test` **695/695** (5 novos), `npm run typecheck` limpo, `npm run build`
compilando, `git diff --check` limpo, detector Impeccable com **apenas** os 3 falsos
positivos já pré-classificados, e o scan de segredos/DSN/token/IP/PII/caminhos
pessoais em 27 arquivos **sem ocorrência**. As jornadas afetadas foram reexecutadas
nos três viewports.

### 20.10 Patch terminal de acessibilidade — A1 a A4 (19/08/2026)

**Estado: `A1–A4 FECHADOS — CONTRATO DE ALVO E TIPOGRAFIA VERDE NOS TRÊS VIEWPORTS`.**

Segunda correção estreita, **explicitamente autorizada pelo proprietário** depois do
stop-loss registrado no §20.9. Não é V3-1A.1, não amplia o redesenho e não
carrega refatoração.

O contrato autoritativo do projeto foi aplicado sem desconto: **todo controle
interativo com área renderizada mínima de 44×44px** e **nenhum texto ou glifo abaixo
de 12px**. Os 24px, 30px e 40px do §20.9 deixaram de ser dívida aceitável, e o critério
AA de 24×24px (WCAG 2.5.8) **não** foi usado como justificativa.

#### Correção de classificação do §20.9

O achado **A2 estava atribuído ao arquivo errado**. O glifo de 10px não vive em
`EvidenceQueue.tsx`: ele está em `src/components/SortableHeader.tsx`, componente
**compartilhado por 14 arquivos e 156 usos**. Portanto A2 é **dívida preexistente**
que o V3-1A passou a renderizar nesta tela, e não um defeito introduzido pelo gate.
A1 e A3 seguem corretamente atribuídos ao V3-1A; A4 é do mesmo componente
compartilhado, também preexistente.

#### Diff funcional

| # | Arquivo | De | Para |
|---|---|---|---|
| A1 | `app/inteligencia/page.tsx` | `px-2.5 py-1` | `inline-flex items-center justify-center min-h-11 min-w-11 px-2.5` |
| A2 | `src/components/SortableHeader.tsx` (2 ramos do `SortIcon`) | `text-[10px] leading-none` | `text-xs leading-none` |
| A4 | `src/components/SortableHeader.tsx` (botão) | `w-full h-full flex` | `w-full h-full min-h-11 flex` |
| A3 | `src/components/inteligencia/ConcentrationBars.tsx` (contêiner) | `flex h-8` | `flex min-h-11` |
| A3 | `src/components/inteligencia/ConcentrationBars.tsx` (segmento) | `h-full min-w-[2.5rem]` | `self-stretch min-h-11 min-w-11` |

Duas decisões merecem registro, porque a primeira tentativa **não** fechou o contrato
e a medição em navegador provou isso:

1. **`h-11` no contêider do Pareto rendia 42px, não 44.** O contêiner tem
   `border border-slate-100`; no `border-box`, a altura de conteúdo fica em 42px e o
   filho `h-full` herdava 42px. A correção move a garantia para o próprio acionável:
   `min-h-11` no contêiner e `self-stretch min-h-11` no botão. A altura passou a ser
   propriedade do controle, não consequência aritmética do pai.
2. **O rótulo curto "LTV" media 39,8px de largura.** `min-h-11` resolvia a altura e
   deixava a largura em 39,8px. `min-w-11` estabelece o piso de 44px sem travar nada:
   rótulos longos seguem a largura natural (81,6px a 115,2px), como o contrato pede.

O tamanho do glifo é **explícito** (`text-xs`), e não herdado, de propósito:
`/pedidos` passa `!text-[10px]` no `className` do `<th>` em 7 usos, então um glifo que
herdasse o tamanho voltaria a 10px naquela tela sem que este componente fosse tocado.

#### Medições em navegador — antes e depois

Playwright 1.62.1 + Chromium 149 do cache local, `getBoundingClientRect()` e
`getComputedStyle()`, com a build de produção servida localmente.

| Alvo | Antes | 1ª tentativa | Depois | Critério |
|---|---|---|---|---|
| Links da navegação interna (6) | 24px de altura; "LTV" 39,8px de largura | 44px de altura; 39,8px de largura | **44×44px** (mín.), largura natural até 115,2px | altura ≥44 obrigatória |
| Segmentos do Pareto (3) | ~30px | **42px** | **44px** de altura; largura 96,6–1100px | ≥44×44 |
| Cabeçalhos ordenáveis | ~40px | 44px | **44px** (mín.; 56px e 73px onde o rótulo quebra) | ≥44 de altura |
| Glifo de ordenação | 10px | 12px | **12px** em todos os 12 glifos | ≥12px |

Repetido nos três viewports, com o mesmo resultado: **1440×900**, **1024×768** e
**390×844**. Zero overflow horizontal em todos (`scrollWidth == clientWidth`:
1440/1440, 1024/1024, 390/390) e nenhum controle excedendo a viewport.

#### Contratos funcionais verificados na renderização

- **Navegação interna** leva às seções corretas: `#fila-evidencias` e `#concentracao`
  em `top=96px`; `#ltv`, última seção da página, em `top=533px` — a página já está no
  fim do scroll, e a seção fica visível. Rótulos, destinos e ordem inalterados.
- **Ordenação** asc/desc funcionando: `aria-sort` alterna `descending`→`ascending`, os
  valores realmente invertem (`R$ 3K, R$ 1K, R$ 800, R$ 0, R$ 0, R$ 0` ⇄ o inverso) e
  as 6 linhas sobrevivem a todos os estados.
- **Pareto** abre o drill-down correto: `aria-label="Detalhe do bucket A — top 50% do
  GMV de BARBOURS"` abre o diálogo do bucket A daquela marca.
- **Diálogo** com *focus trap*, `Escape` fechando e foco devolvido ao segmento.
- **Foco visível** por teclado no link interno e no cabeçalho ordenável.
- **Zero erro de aplicação e zero erro de hidratação** no console.

#### Regressão do componente compartilhado

`SortableHeader` é usado por **14 arquivos, 156 vezes**. Duas verificações
independentes:

1. **Medição em navegador**, desktop e mobile: `/canais` (4 tabelas, 34 cabeçalhos),
   `/produtos` (1 tabela, 6), `/qualidade` (2 tabelas, 14) e `/inteligencia` (12) —
   **66 cabeçalhos**, todos com altura ≥44px, todos os glifos a 12px, ordenação
   funcional (`aria-sort` `none`→`ascending`), estrutura de tabela sem regressão e zero
   overflow novo em nenhuma das duas larguras.
2. **Auditoria estática dos 156 usos.** Os únicos tokens que os consumidores passam via
   `className` são `!py-2.5` (11×), `!py-3` (7×), `!text-[10px]` (7×), `!px-4` (5×),
   `!px-5` (3×) e `!px-3` (3×): **nenhum token de altura**. E `className` é aplicado ao
   `<th>`, nunca ao `<button>` — então `min-h-11` não é sobrescrevível por consumidor
   algum. A API pública, o `aria-sort`, o `scope="col"`, o alinhamento e a lógica de
   `onSort` ficaram idênticos.

**Escopo real da medição em `/regioes`:** a rota **foi aberta em desktop e mobile** e
verificada quanto a viewport, layout e overflow — **não houve overflow horizontal** em
nenhuma das duas larguras. Mas **a tabela não montou**, nem com payload injetado (os
quatro endpoints da rota têm contratos que o fixture desta rodada não reproduz, e
reconstruí-los estava fora do escopo de um patch de três regras CSS). Portanto
**nenhum `SortableHeader` dessa rota foi medido em runtime**, e nada é afirmado sobre a
altura dos cabeçalhos da tabela de Regiões. A garantia ali é **indireta**: é o mesmo
componente compartilhado, agora com `min-h-11`, e a auditoria estática dos 156 usos não
encontrou nenhum override de altura. Fica registrado como **risco não bloqueante**.

#### Dívida preexistente registrada, não corrigida

`/pedidos` rebaixa o rótulo do `<th>` a 10px com `!text-[10px]` em 7 usos. É
**anterior ao V3-1A**, está fora de A1–A4 e exigiria editar uma página que não faz
parte deste patch. Depois desta rodada o glifo daquela tela renderiza a 12px e o
rótulo continua a 10px — a inconsistência ficou mais visível, e é assim que deve ser
até que a dívida seja tratada em gate próprio.

#### Testes

`tests/a11y-target-44.test.ts`, **12 testes**, registrado no script `test`. Travam o
contrato de classe por **token** (e não por substring: `min-w-11` como substring
casaria com `min-w-110`), a API pública e a semântica do componente compartilhado, e o
contrato funcional puro do Pareto — `concentrationByBrand` continua devolvendo
`50/30/15/5` e `null` distinto de zero quando não há GMV total. Dois testes de
regressão impedem que qualquer arquivo visual do V3-1A volte a declarar texto abaixo de
12px ou altura fixa abaixo de 44px perto de um acionável.

O cabeçalho do arquivo declara explicitamente o que estes testes **não** são: `node
--test` não tem DOM, então eles não medem pixel. A medição é a do navegador, acima.

#### Validações

`npm test` **707/707** (12 novos), `npm run typecheck` limpo, `npm run build`
compilando, `git diff --check` limpo, `package-lock.json` **não modificado**, **zero
dependência nova**, nenhum arquivo fora de `apps/web/` e `docs/`, detector Impeccable
com **apenas** os 3 falsos positivos `gray-on-color` já pré-classificados, e scan de
secrets/token/DSN/IP privado/PII/caminhos pessoais nas linhas novas **sem ocorrência**.

**Nenhum commit, push ou deploy nesta rodada.** Ao ser escrito, este registro
deixava o Gate V3-1A aguardando revisão e versionamento; o versionamento ocorreu
depois, em `e675948`.

#### Dívida prioritária de plataforma — fora deste patch

Dívida prioritária de plataforma: `withCache` armazena respostas degradadas/falhas por
até cinco minutos, tornando "Tentar novamente" ineficaz em algumas telas. Correção
deve ocorrer em gate próprio **PF1**, antes do V3-1B. **Esse gate foi executado e
versionado em `45fa3f8`, antes do V3-1B, como previsto aqui.**

Este defeito **não foi causado pelo V3-1A**: está em `src/lib/api-client.ts`, é
anterior ao gate e afeta todas as telas que usam o cache.

---

## 23. Gate V3-BE — BE3, BE4, BE5 e BE6 implementados (21/08/2026)

**Estado: `VERSIONADO EM 26434c8 — CONTRATO CONFIRMADO EM PRODUÇÃO pelo smoke
read-only do V3-1B (§24.1). Nenhum frontend nesta task.`** Uma rodada de correção
consolidada pré-versionamento
(21/08/2026) fechou o `returned_count` de topo exigido pelo §15.1-D e corrigiu
duas imprecisões factuais deste registro: a tabela de escopo descrevia o helper
puro como se fosse o contrato da rota, e o orçamento de consultas media só o
serviço. Ver §23.2, §23.3 e §23.8. Base: `417be72` (o UE-F1A já integrado). Zero commit,
push ou deploy.

### 23.1 Universo ML — quatro marcas, não cinco

`ML_BRANDS = ("barbours", "kokeshi", "lescent", "rituaria")`. **Ápice não
pertence ao universo ML**: ela existe em `BRANDS_IN_SCOPE` (TikTok/Shopee) e tem
**zero linhas** em `marts.fact_ml_produto_ranking`, porque não vende no Mercado
Livre. O plano dizia "cinco marcas" em algumas passagens; o fato medido é quatro.

### 23.2 Parâmetro opcional de escopo

`/inteligencia` passou a aceitar o **mesmo filtro canônico das outras rotas**,
`brands=<marca>[,<marca>]`. Não há segundo contrato de querystring, e a chamada
antiga sem querystring continua idêntica.

Há **duas camadas** de validação, com contratos diferentes, e é preciso não
confundi-las.

**Camada 1 — a rota HTTP**, via `resolve_brands`, o helper *canônico* que as
demais rotas já usam. Ele é **case-sensitive** e valida contra
`marts.dim_loja`, o que custa **uma consulta read-only** quando o parâmetro
existe. O contrato público é **lowercase**, o mesmo que o frontend já emite.

**Camada 2 — `resolve_ml_scope`**, helper *puro*, sem banco, que projeta o
pedido sobre a allowlist `ML_BRANDS`. É a **segunda defesa**, não a primeira.

| entrada em `?brands=` | rota HTTP | `ml_scope_brands` |
|---|---|---|
| ausente, vazio | 200, **zero consulta de validação** | as quatro marcas ML |
| `barbours,kokeshi` | 200 | `["barbours","kokeshi"]` |
| `kokeshi,barbours` ou com repetição | 200 | idêntico ao anterior — saída determinística |
| `apice` | 200 — é marca válida do grupo | `[]`: universo ML vazio, **nada fabricado** |
| `BARBOURS` | **422** — `resolve_brands` não normaliza caixa | n/a |
| marca inexistente | **422** | n/a |
| string injetável | **422** | n/a |

**Onde exatamente o 422 acontece.** *Depois* do `SELECT` read-only em
`marts.dim_loja`, e *antes* de qualquer consulta comercial de
`get_inteligencia`. Ou seja: **não** é "antes de qualquer SQL" — é antes de
qualquer SQL **de negócio**. A string do usuário nunca é interpolada: ela só
existe como valor comparado em memória contra as chaves vindas de `dim_loja`.

**O que `resolve_ml_scope` faz e a rota não.** O helper puro normaliza caixa
(`["BARBOURS"]` → `("barbours",)`) e descarta silenciosamente o que está fora
da allowlist (`["'; DROP TABLE --"]` → `()`). Isso vale para chamadas diretas
ao serviço, **não** para o endpoint — que rejeita esses dois casos com 422
antes de chegar lá. A rota **não aceita maiúsculas**, e o contrato global de
`resolve_brands` não foi alterado para que ela aceitasse: mudá-lo mexeria em
todas as outras rotas.

Escopo ML vazio **não monta `IN ()`**: as consultas ML simplesmente não são
emitidas.

O escopo alcança `signals`, `urgent`, `scale`, `organic`, `pareto`, `ltv`, os três
totais, o frescor e o `opportunity_map`. **`tk_products` permanece global** — é
TikTok, e o contrato do V3-1A não muda de grão nem de escopo.

### 23.3 Orçamento de consultas — 9 / 8 / 6

BE3 e BE4 **não abriram round-trip**: as três contagens e `MAX(refreshed_at)`
viajam como colunas auxiliares na consulta de `signals`, que já agrupava por
`product_status`. Cada item de `signals` continua com os mesmos cinco campos; as
colunas auxiliares só alimentam os metadados de topo.

**No serviço** — é o que o número 9/8/6 mede:

| caminho | antes | agora |
|---|---|---|
| `get_inteligencia` normal | 7 | **9** (+2, do `opportunity_map`) |
| `get_inteligencia` com falha de LTV | 6 | **8** |
| `get_brand_detail` | 5 | **6** (+1, `available_months`) |

**Na chamada HTTP** — o filtro novo cobra uma consulta de validação, e
**somente quando o parâmetro existe**:

| chamada | consultas | composição |
|---|---|---|
| `GET /inteligencia` | **9** (8 com falha de LTV) | só o serviço |
| `GET /inteligencia?brands=…` | **10** (9 com falha de LTV) | 1 `SELECT` read-only em `marts.dim_loja` + as 9 do serviço |
| `GET /brand-detail` | **6** | só o serviço |

A distinção que importa: **BE3 e BE4 não adicionaram round-trip** — eles
viajam em `signals`. A consulta extra é da **validação do filtro novo**, ela
não existe quando `brands` é omitido, e não deve ser reclassificada como
"zero round-trip".

### 23.4 As treze chaves do payload

As sete originais permanecem nas **sete primeiras posições, na mesma ordem** —
é isso que prova que nada foi removido nem reordenado. Depois delas, seis
aditivas:

```
signals, urgent, scale, organic, pareto, ltv, tk_products,
urgent_total_count, scale_total_count, organic_total_count,
ml_snapshot_refreshed_at, ml_scope_brands, opportunity_map
```

### 23.5 BE3 — totais verdadeiros

`urgent_total_count`, `scale_total_count`, `organic_total_count`: inteiros, mesmo
escopo e mesmo filtro comercial da lista respectiva, contados **antes do LIMIT**,
zero quando não há linha, e sempre `>= len(lista)`. `scale_total_count` carrega o
corte extra `ad_roas >= 8` — não é o `n_products` do status.

As três listas ganharam **desempate determinístico** `chave DESC, brand ASC,
item_id ASC`, sem mudar filtro nem limite. Antes, `scale` tinha 92 grupos
empatados em `ad_roas` cobrindo 204 linhas com `LIMIT 20`: quais 20 apareciam era
indefinido. `item_id` **não** foi acrescentado ao payload das três listas — ele
entra só na cláusula de ordenação.

### 23.6 BE4 — frescor real

`ml_snapshot_refreshed_at`: `MAX(refreshed_at)` do universo filtrado, em ISO-8601
UTC com sufixo `Z` e segundos inteiros; `null` quando a tabela ou o escopo está
vazio. Não usa `now()`, `ingested_at`, `last_sale` nem o relógio da resposta, e
**não assume timestamp uniforme** — toma o máximo real.

### 23.7 BE5 — `available_months`

`BrandDetailResponse.available_months: list[str]`, de
`marts.fact_tiktok_brand_content_daily`, por marca consultada, `YYYY-MM`
decrescente, sem duplicidade, `[]` para marca sem histórico. **Não cria endpoint
novo.** `ref_month` continua ecoando a competência **pedida**: um mês bem
formatado mas inexistente segue devolvendo **200 com dados vazios e a lista real
de competências**, sem troca silenciosa; formato inválido segue **422**.

A consulta é a **única** de `/brand-detail` sem a janela do mês — de propósito,
porque ela existe justamente para descobrir *outras* competências. O teste de
contrato passou a afirmar isso explicitamente, em vez de exigir a janela em todas.

### 23.8 BE6 — `opportunity_map`

Universo: o snapshot **inteiro** no escopo (`product_status IS NOT NULL`),
incluindo `inactive` e os `sells+advertised` com ROAS < 8 — **nunca**
`urgent ∪ scale ∪ organic`. `total_count` é o universo, antes de qualquer limite.

Referências **descritivas, nunca metas**: `roas_reference = 8.0` (o corte que
`scale` já usava) e `gmv_reference` pela **mediana `PERCENTILE_DISC`** do GMV
estritamente positivo, **recalculada dentro do escopo**. `DISC` e não `CONT`
porque devolve o próprio `NUMERIC` da coluna: a fronteira alta inclusiva fica
exata, sem cast de float, e a referência é um GMV efetivamente observado.

Precedência: `sem_ads` → `roas_indisponivel_com_investimento` → quadrantes.
`ad_roas = 0` é **valor numérico baixo**, nunca indisponibilidade. Fronteiras
altas inclusivas (`ROAS >= 8`, `GMV >= mediana`).

As **quatro** chaves de quadrante e as **duas** faixas estão sempre no payload,
mesmo zeradas. Destaques: **10 por quadrante**, ordenados por
`ad_spend DESC, gmv DESC, brand ASC, item_id ASC` — declarado no payload como
`highlight_order`. Vêm **somente dos quadrantes**, nunca das faixas, com a mesma
classificação dos agregados. O LIMIT não toca os agregados. `title` é **só
exibição**; a URL usa `item_id`.

#### Os dois níveis de `returned_count` — e por que não se confundem

O §15.1-D pede `returned_count` como "quantos pontos vieram **no total**", e a
regra de interface compara `returned_count < total_count` para dizer que os
pontos são **destaques**, não o universo. O diálogo por quadrante precisa do
mesmo número recortado. São **quatro** grandezas distintas:

| campo | significado |
|---|---|
| `opportunity_map.total_count` | universo completo no escopo |
| `opportunity_map.returned_count` | **total** de pontos de destaque retornados = `len(highlights)` |
| `quadrants[*].count` | universo **daquele** quadrante |
| `quadrants[*].returned_count` | destaques **daquele** quadrante |

As faixas **não** têm `returned_count`, porque nunca produzem destaque.
Invariantes garantidos e testados:

- `returned_count == len(highlights)`;
- `returned_count == sum(q.returned_count for q in quadrants)`;
- `q.returned_count <= q.count` para todo quadrante;
- `returned_count <= sum(q.count for q in quadrants)`;
- `q.returned_count <= 10`;
- nenhum destaque vem das duas faixas;
- os agregados são idênticos com ou sem destaques — o LIMIT não os alcança.

Nos estados `empty` e `unavailable_no_positive_gmv`, `returned_count` é **0** e
`highlights` é `[]`.

#### Sem GMV positivo — sem quadrante fabricado

`classification_status = "unavailable_no_positive_gmv"`, `gmv_reference: null`,
os quatro quadrantes zerados, `highlights: []`, e os produtos com Ads e ROAS
numérico contados em `unclassified_count`. O invariante nesse estado é
`sem_ads + roas_indisponivel_com_investimento + unclassified_count = total_count`.
A UI futura mostra indisponibilidade da matriz, nunca quatro quadrantes falsos.
Universo vazio → `classification_status = "empty"`, tudo zerado e referências
`null`. Com GMV positivo → `"available"` e `unclassified_count = 0`.

### 23.9 Zero não é null

O serializador usava `if r.get(campo)`, e **zero é falsy**: um ROAS zero real
chegava à tela como indisponibilidade. Trocado por `is not None` nos **30 pontos**
de `get_inteligencia` — `ad_roas`, `ad_acos_pct`, `cancel_rate_pct`,
`revenue_share_pct`, `units_sold`, `days_advertised` nas três listas, os seis
campos opcionais de `ltv`, os percentuais e o rating de `tk_products`, e
`signals.avg_roas`.

A **definição** de `signals.avg_roas` não mudou: segue
`AVG(CASE WHEN ad_roas > 0 THEN ad_roas END)`, que por construção devolve `NULL`
ou positivo. Essa média de positivos não se confunde com o ROAS individual do
produto.

O teste antigo congelava o defeito de propósito
(`test_i11_zero_vira_none_em_todo_campo_com_guarda_falsy`, "Congelado de
propósito"). Ele foi **invertido**, e cada família ganhou a contraprova de que
`None` continua `None` — zero e ausência seguem distinguíveis.

### 23.10 Atualização do teste legado do Gate S3

`apps/api/tests/test_s3_source_swap.py` congelava o contrato anterior. **Sete**
pontos foram atualizados, e **somente** onde a mudança aditiva exige:

| ponto | de | para |
|---|---|---|
| chaves de topo | 7 | 13, com asserção extra de que as 7 originais são as 7 primeiras |
| consultas de `/inteligencia` | `== 7` | `== 9` |
| consultas com falha de LTV | `== 6` | `== 8` |
| nome/docstring "outras seis" | — | "outras oito" |
| `BrandDetailResponse` | `== 37` | `== 38` |
| assinatura | `["db"]` | `["db", "ml_brands"]`, + `default is None` |

**Nenhuma contraprova foi afrouxada.** Continuam exatas e verdes: zero `gold.` e
zero `raw.` por função, toda consulta passando pela `Session`, conjunto de tabelas
autorizado por igualdade de conjunto, ausência de Data Mart, `TempoRealResponse`
em 5 campos, nenhum campo de frescor em `BrandDetailResponse`, e as contagens de
tabela por função. Nada virou `>=`, presença parcial ou subconjunto.

### 23.11 Validação

**Suíte completa: 43 failed, 796 passed, 8 skipped** — os **mesmos 43 node IDs**
ambientais do baseline (`43 failed, 727 passed, 8 skipped`), **zero falha nova** e
**zero falha antiga desaparecida**. Os +69 aprovados são os testes novos.
`compileall`, import/startup, OpenAPI e `git diff --check` limpos.

**Integração read-only contra o Neon** (transação read-only, `statement_timeout`
15s, só `SELECT`, sem `EXPLAIN ANALYZE`), nos cinco escopos:

| escopo | `total_count` | `gmv_reference` | invariante |
|---|---|---|---|
| global (4 marcas) | 1.650 | 2.207,05 | ✅ |
| barbours | 721 | 1.816,73 | ✅ |
| kokeshi | 487 | 1.833,81 | ✅ |
| lescent | 218 | 3.973,90 | ✅ |
| rituaria | 224 | 2.718,40 | ✅ |
| só ápice | 0 | `null` | `empty`, `ml_scope_brands: []` |

A fonte **avançou de 1.648 para 1.650** entre a auditoria (19/08) e esta
implementação (21/08), com `refreshed_at = 2026-08-21T09:02:43Z`. É exatamente por
isso que **nenhuma contagem viva foi congelada em teste unitário**: os testes
fixam invariantes e contratos, não números da fotografia.

Também provado nos dados reais: zero duplicidade de `(brand, item_id)`;
`total_count` igual ao universo real; a mediana **muda por marca** e nenhuma
herda a global; `available_months` com 11 competências (`2025-10`…`2026-08`) nas
cinco marcas, decrescente e sem duplicidade; mês inexistente devolvendo 200 com
dados vazios e a lista real; marca sem histórico com `[]`; e **18 produtos com
`ad_roas = 0` chegando às listas servidas como zero** — antes todos viravam
`null`.

### 23.12 Risco remanescente

`_query(db, sql)` continua **sem** bind params, por decisão de escopo: alterá-la
globalmente exigiria tocar `test_operacoes_contract.py`, fora da allowlist. A
segurança vem da **derivação por allowlist**, que é mais forte aqui do que
parametrizar, porque nenhuma string do usuário existe no caminho. Se algum dia um
valor livre precisar entrar numa cláusula, a parametrização passa a ser
obrigatória e vira gate próprio.

**O V3-2 não foi iniciado.** O frontend não foi tocado **nesta task** — o V3-1B, que
consome este contrato, veio depois (§24 e §25).

---

## 24. Gate V3-1B — smoke do V3-BE e matriz definitiva (21/08/2026)

**Estado ao fim desta task: `IMPLEMENTADO LOCALMENTE — QA VISUAL PENDENTE (Task
2/2). V3-2 e V3-3 não iniciados.`** Base: `6a5c957`. Zero commit, push ou deploy.
O QA visual foi executado depois, na Task 2/2 (§25), e fechado em 96/96 na rodada
terminal (§26).

### 24.1 Fase A — smoke de produção do V3-BE: `PASS`

Somente leituras HTTP `GET`, sem header de autenticação, sem imprimir secret nem
PII. **75 verificações, 0 falhas.** O contrato do V3-BE **está servido em
produção**: `/openapi.json` declara `brands` em `/inteligencia` e
`available_months` em `BrandDetailResponse`; o payload tem as 13 chaves na ordem,
com as 7 originais nas 7 primeiras posições.

Observado no escopo global: `ml_scope_brands` = as quatro marcas ML;
`ml_snapshot_refreshed_at = 2026-08-21T09:02:43Z`; `total_count = 1650`;
`returned_count = 40 = len(highlights) =` soma dos `returned_count` por
quadrante; invariante das seis classes fechando em 1650; zero duplicidade de
`(brand, item_id)` nos destaques; nenhum destaque vindo das faixas.

`brands=barbours`: escopo respeitado em todas as superfícies ML, sem vazamento
de outra marca, e `gmv_reference` **recalculada** (1816,73 contra 2207,05 do
global). `brands=apice`: escopo ML vazio, listas e totais zerados,
`classification_status = "empty"`, referências `null`, frescor `null` — nada
fabricado.

**Entradas inválidas.** Quatro delas — marca inexistente, `BARBOURS`,
`barbours'/*` e `barbours' UNION SELECT 1` — devolvem **422 do app**, em JSON,
sem SQL, DSN ou stack. Duas com sintaxe de injeção mais agressiva
(`'; DROP TABLE …`, `') OR 1=1 --`) são barradas **antes** por WAF de borda com
**403 `text/html`**: a requisição nem chega ao FastAPI. É camada adicional, mais
restritiva que o contrato, nunca menos. Nota não-bloqueante: a mensagem de 422
**ecoa o valor inválido** e lista as marcas válidas — comportamento
pré-existente do `resolve_brands` compartilhado, em JSON, sem vetor de HTML.

`/brand-detail`: `available_months` com 11 competências `YYYY-MM`, decrescente,
sem duplicidade, competência pedida ecoada. Com `1999-01`: **200**, conteúdo
mensal vazio, `ref_month` continua `1999-01`, a lista real inalterada — nenhuma
troca silenciosa.

**O SHA não é demonstrável por HTTP:** nem `/health` nem
`/health-datasource` expõem versão ou commit. O que este smoke prova é o
**contrato**; não afirmo qual SHA está rodando.

### 24.2 Fase B — a matriz

O bloco 3 deixou de ser a degradação em faixas e passou a ser a matriz 2×2 do
§7.3: eixo X `roas` × `roas_reference`, eixo Y `gmv` × `gmv_reference`, tamanho
do ponto pelo investimento, cor consistente por quadrante, rótulo só no maior
contribuinte de cada quadrante.

**Campos consumidos:** os seis aditivos. `opportunity_map` inteiro,
`urgent_total_count`/`scale_total_count` (bloco 5), `ml_snapshot_refreshed_at`
(frescor do bloco ML) e `ml_scope_brands` (escopo e estado).

**A regra que organiza tudo é negativa:** o frontend **não decide nada** do mapa.
Mediana, referência de ROAS, classificação, agregados, faixas e seleção de
destaques vêm do contrato. `axisPosition` posiciona o ponto **dentro da metade
que o backend já atribuiu** — nunca escolhe a metade —, então o desenho não pode
contradizer a classificação, nem por erro de ponto flutuante na fronteira
inclusiva, nem se a regra do contrato mudar. Há teste estático caçando
recomputação.

**Três grandezas separadas na tela**, porque são coisas diferentes: universo
(`total_count`), agregados por quadrante (que cobrem o universo) e destaques
plotados (`returned_count`). Quando `returned_count < total_count`, a seção
declara em texto que os agregados cobrem o universo e os pontos são só destaques.

**Escopo de marca virou parâmetro de fetch.** Era recorte só no cliente; agora
vai para a API, porque `gmv_reference` é a mediana **do escopo** e recalculá-la
no cliente seria refazer contrato. O escopo entra na **chave do cache** (antes
`withCache("inteligencia")` era constante — global e filtrado colidiriam) e na
**identidade da requisição**. O universo de marcas dos chips é aprendido apenas
de resposta **sem** escopo: uma resposta filtrada não conhece as outras marcas e
não pode encolher o seletor.

**Estados tratados:** `available`, `empty`, `unavailable_no_positive_gmv`,
loading, erro, resposta obsoleta e **escopo ML vazio** — este último é estado
próprio, distinto de universo zero, porque as duas coisas exigem frases
diferentes. Sem `gmv_reference` **nenhum ponto é plotado** e a matriz é declarada
indisponível, com os produtos de Ads e ROAS medido contados em
`unclassified_count` — zero quadrante fabricado.

**As duas faixas nunca se fundem.** `sem_ads` explica ausência de investimento e
diz que **não** é retorno indisponível; `roas_indisponivel_com_investimento`
explica falha de mensuração e diz que **não** é ROAS baixo, registrando que
`ROAS = 0` é retorno baixo **medido** e ocupa quadrante.

**Drill-downs**, todos no `KpiDrilldownDialog` único (nenhum shell novo):
quadrante (regra com as fronteiras, origem declarada de cada referência,
agregados do universo, quantidade de destaques, limitação de fotografia sem
janela, CTA para a fila); ponto (produto, marca, GMV, Ads, ROAS, as duas
comparações, o porquê do quadrante, CTA para a marca); e uma faixa cada, com as
explicações distintas. No mobile (<640) a **matriz inteira** abre no mesmo
diálogo, conforme o §13.

**URLs:** nenhum título, dinheiro, percentual, JSON ou texto livre viaja.
`item_id` **não** viaja — o §9.1 é explícito, e a página de Marca não tem
consumidor.

### 24.3 Uma divergência deliberada da instrução da Task

A Task pedia, no drill-down do ponto, "CTA para `/brand/[brand]` com os
identificadores `ctx_*` allowlisted pelo plano". **Implementei o CTA frio**, sem
nenhum `ctx_*`, por três razões: o §9.1 allowlista **seis** focos derivados dos
blocos do payload e **nenhum** para o `opportunity_map`; o plano condiciona o
valor novo `ctx_from=inteligencia` a existir "**só com wiring real**"; e a página
de Marca **não tem hoje consumidor** de foco vindo da Inteligência — o contexto
quente é explicitamente do V3-2. Emitir contexto que ninguém lê seria dívida sem
retorno, e criar um sétimo foco seria estender o allowlist sem consumidor. O
filtro de marca, sim, viaja. **Decisão fechada na Task 2/2 (FINDING 2):** o CTA do ponto **permanece frio** no
V3-1B. Não se cria `ctx_focus` novo, enum novo, `ctx_from=inteligencia` parcial,
`item_id` na URL nem consumidor especulativo na Marca. O ponto abre
`/brand/<marca>` preservando apenas os filtros compatíveis, e a **chegada quente**
**do `opportunity_map` será decidida e implementada de ponta a ponta no V3-2**. Os
seis focos já allowlistados para os outros blocos e o contrato de
`ctx_from=canais` seguem intocados.

### 24.4 Testes

`tests/inteligencia-v31b.test.ts`, **38 testes**: a regra negativa (nada
recalculado), universo × agregados × destaques, os dois níveis de
`returned_count`, os quatro estados, escopo vazio como estado próprio, sem
`gmv_reference` nenhum ponto, as duas faixas distintas com o que cada uma **não**
é, ROAS zero em quadrante e ROAS null em faixa, fronteira inclusiva e posição
nunca contradizendo o quadrante, raio por área com piso clicável, rótulo só nos
maiores, origem das referências, frescor sem `new Date()`, totais verdadeiros,
escopo na chave de cache e na identidade, URLs sem valor livre, CTA frio, um
único shell, alvos de 44px com nome acessível e foco visível, colapso mobile,
SVG com nome acessível, e nenhum texto abaixo de 12px.

**Três congelamentos do V3-1A foram invertidos, porque BE6 chegou:** o que
proibia matriz/quadrante/eixo antes de BE6 agora exige que a matriz **leia** o
contrato e proíbe **derivar** referência ou classificação na página; a contagem
de `refreshedAt={null}` foi de 3 para 6 (os três diálogos novos), preservando a
contraprova de que nenhum `refreshedAt` recebe valor diferente de `null`; e a
lista de chaves de cache do PF1 ganhou o escopo em `inteligencia:`.

### 24.5 Validações

**989 testes, 989 aprovados** na suíte web — não havia falha preexistente aqui, e
não há nenhuma agora. `npm run typecheck` limpo, `npm run build` compilando,
`git diff --check` limpo. Detector Impeccable nos arquivos visuais: **os 3
`gray-on-color` já pré-classificados como falsos positivos no V3-1A**, e **zero
finding novo** — `OpportunityMatrix.tsx` saiu limpo.

**QA visual formal em navegador: PENDENTE, é a Task 2/2.** Nada foi simulado.

Nenhum backend, endpoint, migration, SQL, pipeline, banco, dependência ou deploy
nesta task. As 43 falhas preexistentes da suíte da API não foram tocadas.

---

## 25. Gate V3-1B Task 2/2 — integração, dois findings e QA visual (21/08/2026)

**Estado: `V3-1B TECNICAMENTE CONCLUÍDO — AGUARDANDO REVISÃO E VERSIONAMENTO.
V3-2 e V3-3 não iniciados.`** Zero commit, push ou deploy.

### 25.1 Integração com `7b1b451`

`origin/main` avançou para **`7b1b451`** (*feat(web): protege mcp do oraculo com
oauth*) durante a Task 1/2, tocando três caminhos que o V3-1B também alterava.
Integrei com backup externo em `%TEMP%` (patch binário + cópia dos três arquivos
novos + SHA-256 dos dez), stash **nomeado** `V3-1B-task1-rastreados`,
`git merge --ff-only`, reaplicação e só então descarte do stash. Nenhum `reset`,
`restore`, `checkout` destrutivo ou `clean`.

**Os três sobrepostos, resolvidos por conteúdo e não por lado:**

| arquivo | resolução |
|---|---|
| `package.json` | único conflito real. Base = **main** (dependências corretas do OAuth) + `tests/inteligencia-v31b.test.ts` acrescentado **uma vez**. **52 testes**: `oracle-oauth`, `tiktok-content-mix` e todos os do Oráculo preservados; **zero dependência nova**; `package-lock.json` intocado pelo V3 |
| `tests/inteligencia-v3-wiring.test.ts` | mesclou sozinho. **As 8 linhas que `7b1b451` acrescentou estão todas presentes.** 29 testes antes e depois — o teste 27 foi **renomeado**, nenhum apagado |
| `docs/PROJECT_STATUS.md` | mesclou sozinho. `OM2`, `OAuth`, `Oráculo` e `MCP` preservados, 1844 linhas antes e depois; só a linha da frente V3 mudou |

**Prova de que nada se perdeu:** 7 dos 10 arquivos ficaram **byte-idênticos** ao
backup, e os 3 que mudaram são exatamente os sobrepostos. Os três arquivos novos
do V3-1B conferem byte a byte com a cópia externa.

### 25.2 FINDING 1 — frescor real nos drill-downs

Os três diálogos novos usavam `refreshedAt={null}`, embora o BE4 entregue
`ml_snapshot_refreshed_at`. Corrigido com **um único timestamp de exibição**:

```
const mlRefreshedAt = displayData?.ml_snapshot_refreshed_at ?? null;
```

A proteção inteira está em sair de `displayData`, não de `data`:
`displayData = status.fresh ? data : null`, então loading, erro e resposta
obsoleta derrubam o timestamp para `null` junto com os dados. **Nenhum diálogo
pode exibir o frescor de uma requisição anterior.** `null` continua significando
frescor indisponível — nunca "agora", e nunca `new Date()`.

Recebem o valor: quadrante, ponto e faixa. O congelamento herdado que exigia
`refreshedAt={null}` foi atualizado: ele valia antes do BE4; agora protegeria a
**ausência** em vez do contrato. Passou a afirmar 3 `null` (blocos anteriores ao
V3-1B) + 3 com o valor do contrato, mais a proibição explícita de
`new Date()`/`Date.now()`.

### 25.3 FINDING 2 — CTA frio, decisão fechada

O CTA do ponto **permanece frio**. O ponto abre `/brand/<marca>` preservando
apenas o filtro compatível, e **nada mais**: sem `ctx_*`, sem `item_id`, sem
métrica, sem texto livre. A chegada quente do `opportunity_map` será decidida e
implementada **de ponta a ponta no V3-2**. Os seis focos já allowlistados para os
outros blocos e o contrato de `ctx_from=canais` seguem intocados.

### 25.4 QA visual — executado em navegador real

Chromium 149 via Playwright do cache local, build de produção servida
localmente, artefatos somente em `%TEMP%`. **96 verificações; 88 aprovadas nesta
rodada.** O resultado final, depois das correções e da reconciliação da contagem,
é **96/96** e está em §26.

Por viewport (**1440×900**, **1024×768**, **390×844**): chamada global emitida,
universo 1650 declarado, destaques nomeados como destaques, declaração de
truncamento presente, frescor do contrato renderizado, total verdadeiro do BE3
(674) no bloco 5, **zero overflow horizontal**, nenhum alvo interativo abaixo de
44×44px. Desktop e tablet mostram a matriz 2×2; **mobile a colapsa** e oferece o
acionamento.

`brands=barbours`: a chamada filtrada realmente ocorre (`["all","barbours"]`),
universo do escopo (721), **pontos só da marca**, nenhuma outra marca nas
superfícies ML. `brands=apice`: estado próprio de "sem escopo Mercado Livre",
distinto de universo zero, zero ponto, **zero resíduo** da requisição anterior e
frescor rotulado como indisponível. Transições rápidas global → barbours → apice
→ global terminam no estado global sem resíduo do escopo, e escopos distintos
geram chaves de cache distintas.

Diálogos: quadrante abre com a regra, a **fronteira inclusiva** (`ROAS ≥ 8,0x`), a
origem das duas referências, os agregados rotulados como universo, os destaques
separados e o **frescor real**; fecha por Escape e por backdrop, com foco
retornando ao acionador. Ponto: nome acessível com produto, marca, quadrante e
métricas; abre o detalhe do ponto e **não** o do quadrante; teclado abre o mesmo
que o mouse; CTA frio verificado como `/brand/barbours?brands=barbours`. Faixas:
as duas presentes, **inclusive a de `count = 0`**, com explicações distintas e
cada uma declarando o que **não** é; `ROAS = 0` plotado em quadrante, nunca em
faixa. Mobile: a matriz abre **no shell único**, sem aninhar diálogo, e navegar
para o quadrante troca o conteúdo do mesmo shell.

Erro: bloco explícito, **zero dado ou timestamp antigo**, retry consultando a
rede e o frescor aparecendo só depois do sucesso. Resto da página sem regressão:
fila, Pareto, LTV, seis âncoras e a ordenação da fila (PF1/V3-1A) funcionando.

Higiene: **zero erro de aplicação, zero erro de hidratação, zero request de
escrita, nenhum host além do local**, e nenhuma resposta 4xx/5xx além do único
500 que o próprio QA injeta.

### 25.5 Correção consolidada — uma rodada, um finding real

O QA encontrou um **fallback silencioso**: exigir que a marca estivesse no
universo ML aprendido fazia `?brands=apice` cair no escopo **global**, e a tela
mostrava o portfólio inteiro como se o filtro não existisse. Corrigido: o
parâmetro é encaminhado sempre que tiver forma de `brand_key`, e **quem decide o
escopo ML é a API** — `apice` devolve `ml_scope_brands: []`, que é o estado
correto. A allowlist de **forma** fica no cliente só para não encaminhar lixo de
URL; a allowlist de **valor** é do backend. Após a correção, os cinco checks de
J3 passaram.

**Dois findings classificados nesta rodada como fora do V3-1B — o primeiro foi
depois corrigido na rodada terminal (§26.2), o segundo segue aceito como dívida:**

1. `TableScrollHint.tsx:82` renderiza `text-[11px]` — abaixo do piso de 12px, em
   tablet e mobile. É `aria-hidden="true"` (dica decorativa de rolagem),
   **preexistente** desde `2f302c0` e compartilhado por **nove** componentes.
   Corrigi-lo aqui mexeria em telas fora deste gate.
2. A mensagem de 422 do `resolve_brands` **ecoa o valor inválido** e lista as
   marcas válidas — comportamento preexistente do helper compartilhado, em JSON,
   sem vetor de HTML.

Uma terceira reprovação foi classificada nesta rodada como **asserção minha**, não
defeito, porque `fmtBrl` abrevia (`R$ 1,8K`) e o check procurava `1.816,73` literal.
**Essa classificação estava errada e foi revertida na rodada terminal:** o defeito
era da aplicação — as duas medianas apareciam como `R$ 2K`, visualmente idênticas.
Ver §26.3.

### 25.6 Validações

**1067 testes, 1067 aprovados** — maior que os 989 da base `6a5c957`, porque
`7b1b451` trouxe as suítes de Oráculo/OAuth; nenhuma suíte registrada foi
removida. `typecheck` limpo, `build` compilando, `git diff --check` limpo.
Detector Impeccable nos arquivos visuais: **os 3 `gray-on-color` já
pré-classificados como falsos positivos, zero finding novo** —
`OpportunityMatrix.tsx` limpo.

Nenhum backend, API, SQL, migration, pipeline, banco, endpoint ou dependência.
Nada do OAuth/Oráculo além de preservar o que já estava em `main`.

---

## 26. Gate V3-1B — rodada terminal pré-versionamento (21/08/2026)

**Estado ao fim desta rodada: `V3-1B TECNICAMENTE CONCLUÍDO — QA VISUAL FINAL
96/96 APROVADAS, AGUARDANDO VERSIONAMENTO`.** Esta seção descreve o estado
imediatamente anterior ao commit; o **versionamento ocorreu depois, em
`2d7ecdf`** (*feat(web): adiciona matriz de oportunidades na inteligencia*, 12
arquivos). O V3-3 segue não iniciado.

### 26.1 Índice normalizado sem perder trabalho

O índice estava parcialmente preparado, misturando arquivos de rodadas diferentes.
Foi esvaziado com `git restore --staged` nos **sete caminhos rastreados
explicitamente** — nunca por glob, `reset --hard`, `checkout`, `clean` ou `restore`
de conteúdo, que apagariam a árvore de trabalho em vez do índice.

**Prova de que nada mudou:** SHA-256 dos dez arquivos do V3-1B **idênticos** antes e
depois; `git diff --cached` vazio; os mesmos sete arquivos seguem `M` e os três novos
seguem `??`; **zero stash residual**; os 13 módulos e 9 testes do Oráculo/OAuth
intactos.

### 26.2 Piso tipográfico de 12px na dica de rolagem

O `TableScrollHint` renderizava a dica em `text-[11px]`, abaixo do piso de 12px que o
V3 declara. Na rodada anterior isso foi classificado como dívida preexistente **fora**
do gate — o componente é compartilhado por nove telas. A revisão determinou o
contrário: **o piso é contrato, e o componente aparece dentro do V3-1B**. Corrigido
com **uma linha**, `text-[11px]` → `text-xs`, sem tocar texto, `aria-hidden`, cor,
espaçamento, lógica ou API.

Medido em navegador real, nos três viewports:

| rota | mobile 390×844 | tablet 1024×768 | desktop 1440×900 |
|---|---|---|---|
| `/inteligencia` | 1 dica **visível a 12px** | 1 dica no DOM a 12px, oculta por `sm:hidden` | dica ausente (tabela não rola) |
| `/canais` | 4 dicas **visíveis a 12px** | 4 no DOM a 12px, ocultas | ausentes |
| `/financeiro` | 3 dicas **visíveis a 12px** | 1 no DOM a 12px, oculta | ausentes |

Em todos os casos: `text-align: center`, `rgb(148,163,184)` (slate-400),
`padding-top: 4px`, **uma única linha**, zero estouro do contêiner, zero overflow
horizontal de página e zero erro de aplicação. Alinhamento, cor e espaçamento são os
mesmos de antes — só o tamanho subiu.

O teste `scroll-hint.test.ts` passou a provar isso estaticamente: ausência de
`text-[11px]`, **nenhum** `text-[Npx]` abaixo de 12 em ponto algum do componente,
presença da classe de 12px, e a semântica preservada (`aria-hidden`, texto idêntico,
API pública inalterada, dica ainda condicionada a `edges.canScrollRight`).

### 26.3 Referência é limiar, não manchete

A rodada anterior classificou a reprovação de `J2.mediana-muda` como asserção errada
do harness. **A classificação estava errada.** Uma sonda dirigida em navegador provou
o defeito na aplicação:

```
GLOBAL   Referências: ROAS 8,0x · GMV R$ 2K
BARBOURS Referências: ROAS 8,0x · GMV R$ 2K     ← mediana 1.816,73
universo barbours: 721 · escopos pedidos: ["all","barbours"]
```

A requisição estava corretamente escopada — universo e pontos mudavam. O que não
mudava era **a referência exibida**: `fmtBrl` abrevia, e 2.207,05 e 1.816,73 caem os
dois em `R$ 2K`. O diálogo afirma que a mediana "muda quando o escopo muda", e a tela
mostrava o contrário.

Corrigido com `moedaExata`, moeda pt-BR com duas casas, aplicada **somente às
referências** — cabeçalho da matriz, `figcaption`, regra do quadrante, origem das
referências e leitura do ponto. Manchete continua abreviada: perder casas decimais num
KPI é economia de espaço, mas perder casas decimais num **limiar** apaga a informação
que ele existe para dar. Nada de mediana, classificação ou agregado é recalculado no
frontend — a correção é de formatação.

Depois dela: `global=GMV R$ 2.207,05` × `escopo=GMV R$ 1.816,73`.

### 26.4 Reconciliação da contagem do QA

A rodada anterior reportou 88 de 96. As oito não-aprovações se decompõem assim:

| origem | qtd. | o que era | destino |
|---|---|---|---|
| **defeito de aplicação, corrigido na própria rodada** | 5 | os cinco checks de `J3` antes da correção do escopo `brands=apice` (fallback silencioso para global) | corrigido; §25.5 |
| **defeito de aplicação, corrigido na rodada terminal** | 2 | tipografia a 11px em tablet e mobile (`J1.tablet.fonte-12`, `J1.mobile.fonte-12`) | corrigido; §26.2 |
| **classificação errada, revertida** | 1 | `J2.mediana-muda` foi chamada de asserção do harness; era defeito real de aplicação | corrigido; §26.3 |

O harness também tinha **dois defeitos próprios**, encontrados ao verificar a
contagem, e nenhum dos dois mascarava problema da aplicação:

1. `refGlobal` lia `innerText` **sem normalizar** e o regex exigia espaço comum, mas
   `toLocaleString` emite **espaço não-quebrável** entre `R$` e o número — o mesmo
   check no escopo passava porque ali o texto era normalizado. Corrigido com `\s` e
   normalização nas duas pontas.
2. `H.total-requests` era telemetria, não verificação, e contava a própria URL da API
   como navegação, o que tornava a guarda circular. Virou
   `H.sem-tempestade-de-requisicoes`, com as navegações restritas ao host da app.

As outras quatro entradas informativas (`J2.ref-global`, `J3.chip-apice`,
`J4.requisicoes`, `J7.titulo`) também deixaram de ser registro e passaram a ter
veredito, porque todas tinham expectativa determinada. **Nenhum número vivo foi
congelado:** `J2.mediana-muda` compara a referência global com a do escopo e exige
centavos, em vez de fixar o valor.

**Resultado final: 96 verificações, 96 aprovadas.** Nenhuma não-aprovação remanescente.

Duas baterias **complementares**, contadas separadamente e não somadas às 96, cobriram
vãos que o harness deixava: a **dica de rolagem** em três rotas × três viewports
(§26.2) e os **drill-downs em tablet 1024×768** — quadrante, ponto e faixa vazia, com
`Escape`, devolução de foco, abertura por teclado, referência com centavos, zero
overflow e zero erro: **13 verificações, 13 aprovadas**. J6/J7/J8 rodavam só em
desktop e J9 só em mobile; o tablet não tinha drill-down exercitado.

Amostras do fechamento: `J2.ref-global` = `GMV R$ 2.207,05`; `J2.mediana-muda` =
`global=GMV R$ 2.207,05 escopo=GMV R$ 1.816,73`; `J4.requisicoes` =
`["all","barbours","apice","all"]`, uma por escopo, na ordem, sem duplicata;
`H.sem-tempestade-de-requisicoes` = 14 requisições de API em 13 navegações, pico 10×
contra limite 14; `J1.*.fonte-12` = menor fonte **12px** nos três viewports.

### 26.5 Dívida preexistente registrada, não corrigida

A mensagem de **422** do `resolve_brands` **ecoa o valor inválido** recebido e lista as
marcas válidas. É comportamento preexistente do helper compartilhado, **não é falha do
V3-1B** e não conta como não-aprovação do QA. Aceita nesta rodada porque a resposta é
**JSON**, não executa HTML, e não expõe stack, SQL nem DSN. Fica registrada aqui como
dívida separada, para tratamento em gate próprio de backend.

---

## 27. Gate V3-2 Task 1/2 — Marca 360 implementada (21/08/2026)

**Estado: `IMPLEMENTADO — AGUARDANDO REVISÃO`.** Base: `2d7ecdf` (V3-1B já
versionado). Zero commit, push ou deploy. **V3-3 não iniciado**, e o QA visual
integrado é dele — o que houve aqui foi inspeção local durante a implementação,
não fechamento de QA.

### 27.1 A história que a página passou a contar

Dez seções em coluna única viraram uma sequência com regime temporal explícito
em cada bloco: **chegada → situação → evolução → o que mudou → competência
mensal → conteúdo/produtos → evidência → próxima ação**.

| # | Bloco | Regime | O que mudou |
|---|---|---|---|
| 1 | Cabeçalho + `BrandArrivalBanner` | herda | banner passa a nomear a origem ("chegou de Inteligência por…") |
| 2 | Situação da marca | **intervalo global** | os quatro KPIs, com a ressalva de que a classificação de sinais por canal é de Canais e não é recalculada aqui |
| 3 | Evolução e mix | **intervalo global** | GMV diário + **mix por marketplace** (barras), com borda âmbar quando a série é de exemplo |
| 4 | O que mudou | **global vs anterior** | decomposição do GMV por canal contra a janela equivalente |
| 5 | `TikTok Shop · análise mensal` | **competência** | contêiner cercado, com seletor, KPIs, mix de superfície, funil, conteúdo, produtos e limitações |
| 6 | Últimos 7 dias | **intervalo global** | etiqueta de intervalo; `Sem dado` em vez de travessão mudo |
| 7 | Próximos passos | — | três destinos frios, com filtros compatíveis preservados |

### 27.2 A fronteira entre os dois regimes (defeito M1)

O regime passou a ser propriedade **do contêiner**, não de cada card. Três
mecanismos, e nenhum deles é decorativo:

1. **Formatação distinta e não intercambiável.** Intervalo é
   `01/08/2026 – 31/08/2026` (`fmtPeriodo`); competência é `ago/2026`
   (`fmtCompetencia`). Um teste afirma que a competência **não** casa com o
   padrão `dd/mm/aaaa`, para que as duas nunca convirjam por descuido.
2. **Etiqueta em todo bloco analítico.** Os blocos globais carregam
   `intervalo global <período>`; os de dentro do contêiner carregam
   `competência <mês>`. Optei por isso em vez da faixa *sticky* do wireframe
   §10.4: a etiqueta por bloco cumpre o mesmo objetivo — nunca perder a
   referência temporal ao rolar — e é estritamente mais informativa, porque
   sobrevive a captura de tela e a leitor de tela. O contêiner mensal **não
   contém** a expressão "intervalo global" em nenhum ponto do markup.
3. **Nota neutra de não-sobreposição.** `periodRegimeRelation` compara início e
   fim do mês com o intervalo (comparação lexicográfica de ISO, que é
   cronológica) e devolve a nota só quando não há interseção. Sobreposição
   parcial conta como sobreposição. Não é erro, não tem cor de erro, e não
   bloqueia nada.

### 27.3 Competência real e URL compartilhável (defeito M2, BE5)

`available_months` entrou no contrato TypeScript de `BrandDetail` como campo
aditivo com default `[]`. A página **não importa mais `mock-daily`** e não
conhece `AVAILABLE_MONTHS`.

| Situação | Comportamento |
|---|---|
| `ref_month` ausente | competência **mais recente realmente disponível** |
| válida e na lista | selecionada |
| válida e **fora** da lista | **preservada**, com vazio explícito nomeando o mês e listando as competências que têm dado |
| inválida (`2026-13`), vazia ou repetida | ignorada com segurança, cai na mais recente |
| troca de marca pelos pills | competência **preservada**, `ctx_*` **descartado** |
| navegação pela sidebar | nada de quente viaja: `ref_month` está fora de `FILTER_QUERY_KEYS` |

**Ordem deliberada: a URL manda.** Uma competência bem formada nunca é trocada
em silêncio, mesmo sem dado — trocar esconderia do analista que o mês pedido não
existe para aquela marca. Só a ausência de pedido cai na mais recente.

`available_months` só existe **depois** da resposta, então a primeira leitura sai
sem competência e o endpoint serve o próprio padrão; se a mais recente com dado
for outra, ela é adotada e a chave muda. É a mesma leitura com outra chave —
**nenhum endpoint novo, nenhum fetch novo**. A adoção é **por derivação**:
`available_months` entra no estado, `resolveRefMonth` passa a devolver
`available[0]`, e a mudança de identidade dispara a segunda leitura — no máximo
uma, porque na rodada seguinte a competência já consta na lista e a resolução
converge. Não há helper de adoção (§27.10).

**Fallback sintético.** Continua existindo, encapsulado em
`lib/brand/demo-series.ts` como `buildDemoSeries` + `DEMO_SERIES_WARNING`
("dados de exemplo — não usar para decisão"), e diferenciado por borda âmbar nos
dois cartões do bloco global. O nome do símbolo importado é a primeira coisa que
alguém lê ao auditar a página; `generateDailyData` de um módulo `mock-daily`
parecia detalhe de implementação.

### 27.4 Frescor mensal com identidade própria (defeito M6)

`brandDetailRequestKey(brand, month)` — e **nada mais**. A chave global
(`brand|channels|from|to|compare`) ficou **intocada**: os dois fetches têm
gatilhos diferentes, e misturá-los faria a troca de intervalo global invalidar
dado mensal que continua válido.

- `computeRequestStatus` (o mesmo do PF1/U4) separa `loading`, `error` e `fresh`;
- `displayDetail = detailStatus.fresh ? brandDetail : null` — a proteção inteira
  está em sair daqui, e é isso que o painel recebe;
- os **três** desfechos registram a chave resolvida: sucesso, resposta nula e
  rejeição. Sem o terceiro, uma rejeição deixaria o painel em skeleton para
  sempre;
- guarda `ignore` nos dois efeitos, com `cleanup` nos dois;
- **o diálogo mensal fecha quando `detailRequestKey` muda** — um detalhe que
  sobrevivesse à troca mostraria evidência de outra identidade.

**Quatro estados, mutuamente exclusivos**, na ordem de decisão:
`loading` → `error` → `empty` → `ready`. A rodada terminal (§27.10) reduziu
cinco para quatro e mudou de lado o significado de `null`; a redação anterior
desta seção afirmava que uma falha de `apiFetch` era "indisponibilidade
concluída sem payload", e isso era falso.

### 27.5 Contexto quente da Inteligência (§9.1)

`brand-arrival-context.ts` virou **união discriminada por origem**:

| | `ctx_from=canais` | `ctx_from=inteligencia` |
|---|---|---|
| motivo | `ctx_signal` ∈ 5 sinais | **`ctx_focus`** ∈ 6 focos |
| canal | validado contra o filtro **e** contra o sinal | validado contra o filtro **e derivado do foco** |
| retorno | `/canais?brands&channels` | `/inteligencia?brands[&lens]#âncora` |
| rótulo | "Voltar à evidência em Canais" | "Voltar à evidência em Inteligência" |

O canal **não é parâmetro do produtor** de Inteligência: é derivado do foco,
porque no contrato cada foco nasce de uma fonte de um único marketplace. Deixá-lo
aberto permitiria montar um par impossível. Uma URL que traz **as duas** chaves
de motivo é ambígua sobre a própria origem e é descartada nas duas direções, por
uma guarda de **presença** e não de "valor único válido" (§27.10).

**Produtores realmente conectados** — e só onde o dado demonstra o foco:

| Produtor | Foco | Por que é demonstrável |
|---|---|---|
| CTA do diálogo da fila de evidências | `desperdicio_ads` / `escala_ads` / `venda_organica` | mapeamento **exato**: as três lentes SÃO as três listas do payload |
| Linha da tabela de LTV | `ltv` | a própria linha é a evidência de recorrência da marca |
| Ponto de destaque da matriz, quadrante `escalar` | `escala_ads` | identidade de população: o quadrante exige ROAS ≥ referência, a mesma da lista `scale` |

**Ficaram FRIOS, de propósito:** os quadrantes `reduzir_parar`, `monitorar` e
`testar_investimento` — `desperdicio_ads` é `ad_spend_no_sales`, e um ponto do
quadrante inferior tem ROAS medido, podendo ter venda. Mapeá-lo seria inventar
classificação para produzir contexto. "Próximos destinos" também segue frio: o
próprio bloco se anuncia como navegação limpa.

`concentracao` e `produto_tiktok` estão **allowlistados no consumidor sem
produtor nesta rodada** — o Pareto abre `/produtos` e o painel TikTok não tem
drill-down. A URL é entrada compartilhável e editável à mão, então o consumidor
tem de tratá-los; criar um CTA novo só para ter produtor seria inverter a regra.

**Evidência na Marca, auditada foco a foco:** os três focos de Ads e
`produto_tiktok` têm âncora real (`marca-periodo` e `marca-produtos-tiktok`);
`concentracao` e `ltv` **não existem nesta tela** e declaram a limitação em vez
de prometer navegação. Nenhuma descrição contém dígito.

**Achado corrigido na implementação:** o retorno à Inteligência **não pode**
passar por `mergeFilteredHref`. Dois motivos independentes, cada um suficiente:
`/inteligencia` não é filter-aware, e o `split("?")` daquela função jogaria
`#fila-evidencias` dentro do valor de `lens`
(`lens=parar%23fila-evidencias`) — quebrando a lente **e** perdendo a âncora. A
decisão ficou isolada e testada em `returnPreservesGlobalFilters`, com um teste
que reproduz o dano evitado.

A âncora `produtos-tiktok` foi acrescentada ao painel TikTok do bloco 5 da
Inteligência, para que o retorno mandado pelo contrato aterrisse de fato.

### 27.6 Drill-downs da Marca (§9, linhas B6 e B8)

Dois acionamentos novos, no **shell único** `KpiDrilldownDialog`, compostos com
as primitives do G2 — nenhum shell, registry ou modal novo.

- **Funil da superfície:** impressões → CTR → visitas → CVR → itens → GMV, só da
  própria superfície e da própria marca. **Zero mediana, p75 ou benchmark entre
  superfícies**: vídeo, live e product card são superfícies heterogêneas, e sem
  regra de negócio documentada que demonstre comparabilidade, a mediana das três
  não é referência. CTA: **"Abrir TikTok Shop em Canais"**, com a ressalva de que
  a superfície específica **não viaja** (§27.10).
- **Produto TikTok:** GMV, pedidos, vídeos e GMV/1k views. `gpm` vem **pronto**
  do backend; quando é `null`, a linha diz ausência — a divisão não é
  reconstruída aqui. CTA: Produtos na aba TikTok, com a ressalva explícita de que
  **a competência não viaja**, porque `/produtos` não consome `ref_month`.

`monthly-drilldown.ts` devolve dados **semânticos** (`{kind:"value"}` ×
`{kind:"missing"}`), e é o componente que formata. Assim `null ≠ zero` fica
testável no nível certo: um formatador que recebesse `number` já teria apagado a
diferença antes do teste. Zero medido continua zero (CVR 0%, GMV R$ 0,00);
`null` vira "Sem dado".

Nenhuma linha inteira é clicável, e o `hover` de linha foi **removido** de todas
as tabelas — linha sem ação não deve parecer clicável. O acionamento é botão com
nome acessível (`Detalhe do funil da superfície Vídeo em ago/2026`) e alvo
`min-h-11 min-w-11`.

### 27.7 O que saiu, e o que não entrou

**Demographics foi removido**: as sete colunas `viewers_pct_*`/`followers_pct_*`
são 100% nulas na fonte, e a seção exibia sete traços. No lugar, uma nota
compacta dentro do contêiner mensal — o card grande e vazio não sobrou. A palavra
"Demographics" continua existindo **uma vez**, dentro da nota que explica a
remoção: apagar o card não é apagar a informação.

**Não entrou:** Ads na Marca (o contrato mensal não traz investimento de mídia),
margem, CMV, afiliados, share de atribuição, benchmark competitivo, ranking novo,
previsão e recomendação por threshold. Nenhuma métrica comercial é recalculada no
frontend; a única divisão é ticket médio = GMV/pedidos, guardada por `> 0`.

**Dívida preexistente que este gate FECHOU** na sua própria superfície: os onze
`text-[10px]` e dois `text-[11px]` da página (defeito M7) — a Marca agora tem
piso de 12px, afirmado por teste nos dois arquivos do gate.

### 27.8 Arquivos

**Criados** — quatro módulos puros e um componente:

| Arquivo | Papel |
|---|---|
| `src/lib/brand/ref-month.ts` | contrato da competência: parse, resolução, rótulo, chave de requisição, estado do painel, relação entre regimes |
| `src/lib/brand/period-changes.ts` | decomposição por canal e mix por marketplace, com `null ≠ zero` |
| `src/lib/brand/monthly-drilldown.ts` | conteúdo semântico dos dois detalhes mensais |
| `src/lib/brand/demo-series.ts` | encapsula e rotula o fallback sintético |
| `src/components/brand/TikTokMonthlyPanel.tsx` | o contêiner do regime mensal |

**Alterados:** `app/brand/[brand]/page.tsx` (reescrita),
`src/lib/brand-arrival-context.ts` (união discriminada),
`src/components/BrandArrivalBanner.tsx` (copy e CTA por origem),
`app/inteligencia/page.tsx` (três produtores + âncora),
`src/lib/inteligencia/lens.ts` (âncora `produtos-tiktok`),
`src/lib/api-client.ts` (`available_months`),
`tests/brand-arrival-context.test.ts` (o teste de "único produtor" passou a
descrever os dois), `package.json` (registro do teste novo).

### 27.9 Validações

**1155 testes, 1155 aprovados** (1069 antes + 86 do V3-2), `typecheck` limpo,
`build` compilando, `git diff --check` limpo, `package-lock.json` intocado,
**zero dependência nova**, nada fora de `apps/web/` e `docs/`, e scan de
secrets/token/DSN/IP privado/PII/caminho pessoal em 2.722 linhas **sem
ocorrência**.

Detector Impeccable: os três `gray-on-color` da Inteligência seguem sendo os
falsos positivos já pré-classificados (ternários), e o painel mensal recebeu um
`ai-color-palette` por usar violeta em cabeçalho — **também falso positivo**,
porque violeta é o mundo visual incumbente do projeto (89 usos de `bg-violet-50`,
79 de `text-violet-700`). O tom foi alinhado ao token estabelecido
(`text-violet-800`) em vez de inventar um novo.

**Zero backend, endpoint, SQL, migration, pipeline, banco ou deploy.** O único
arquivo de API lido foi `schemas/performance.py`, e apenas para provar que
`available_months` existe no contrato real.
### 27.10 Rodada de correção terminal — cinco findings (22/08/2026)

Revisão do próprio V3-2 antes do V3-3. Base `2d7ecdf`, zero commit.

**FINDING 1 (bloqueador) — `available_months = []` fabricava `ready` com zeros.**
O wiring usava `monthAvailable: resolucao.available || !resolucao.hasAvailable`.
A intenção era não declarar "sem dado" antes da primeira resposta; o efeito real
era outro: numa resposta **fresca** de marca sem histórico, o backend devolve 200
com `available_months = []` **e agregados zerados**, e `hasAvailable === false`
fazia `monthAvailable === true` → `ready`. A tela exibia GMV R$ 0,00, zero pedido
e zero cliente como se fossem medidas. Corrigido para `monthAvailable:
resolucao.available` — a pergunta é só uma, e o caso "ainda não se sabe" já era
coberto por `loading`, que tem precedência. O vazio ganhou **duas copies**: sem
histórico ("esta marca não tem histórico no TikTok Shop", sem seletor, porque não
há mês para escolher) e mês sem dado (competência preservada + lista das que têm
dado). Quando a URL não pediu competência e a lista veio vazia, `refMonth` é
`null` e o nome vem do `ref_month` **ecoado pela resposta**, não de um travessão.

**FINDING 2 (bloqueador) — falha era rotulada como `unavailable`, sem retry.**
`apiFetch` devolve `null` para HTTP não-2xx, falha de rede, JSON inválido e
qualquer exceção capturada. Logo `null` é **falha de leitura**, e não existe hoje
caso demonstrado de "concluiu corretamente sem payload". A copy "a consulta
concluiu sem payload" era semanticamente falsa e o estado não oferecia
recuperação. Agora, no `.then`, `d === null` registra a chave, encerra o loading
e marca `error = true`; o `.catch` segue fazendo o mesmo para uma rejeição
futura. O estado de erro oferece **"Tentar novamente"**, e o retry volta à rede
porque o PF1 não cacheia `null` (`isCacheableApiResult`). **`unavailable` foi
removido do contrato**, em vez de mantido como distinção inventada: não havia
gatilho demonstrável, e a distinção custava o retry ao usuário. Nenhuma segunda
requisição automática, nenhum endpoint novo, e nenhuma afirmação sobre timeout —
`apiFetch` não o distingue.

**FINDING 3 (alto) — `available_months` sem identidade de marca.** A lista era um
`useState<string[]>` solto. Ao trocar de marca pelos pills, o seletor seguia
oferecendo os meses da marca anterior durante o loading, e se a leitura da marca
nova falhasse a lista antiga **permanecia** — um furo na mesma proteção de
frescor que o payload mensal já tinha. Agora a disponibilidade é
`{ brand, months, servedMonth }`, e o consumo passa por
`availabilityForBrand(availability, brand)`: sem coincidência de marca, não há
lista. Uma falha **não** sobrescreve a disponibilidade, então um retry da mesma
marca continua enxergando a própria lista, e uma marca nova sem resposta
simplesmente não tem nenhuma. `ref_month` escolhido explicitamente continua no
href dos pills; `ctx_*` continua descartado.

**FINDING 4 (médio) — chave estrangeira aceita em uma origem.** `ctx_from=canais`
não rejeitava `ctx_focus`, e no ramo de Inteligência o teste era
`readSingle(ctx_signal) != null` — que devolve `null` para parâmetro
**repetido**, deixando passar exatamente o caso mais suspeito
(`?ctx_signal=a&ctx_signal=b`). Nasceu `hasParam`, que responde **presença** e
não "valor único válido": uma ocorrência, várias, valor vazio ou inválido contam
todas. Canais rejeita qualquer `ctx_focus`; Inteligência rejeita qualquer
`ctx_signal`. Parâmetro próprio repetido segue inválido, e os dois caminhos
válidos seguem idênticos.

**FINDING 5 (médio) — superfície chamada de canal, e CTA sem a dimensão.**
`channel_funnel` entrega VIDEO, LIVE e PRODUCT_CARD, que são **superfícies do
TikTok Shop**, não marketplaces. O detalhe dizia "benchmark entre canais" e o CTA
"Comparar canais em Canais", mas `/canais?channels=tiktok` abre a visão do
marketplace e não preserva a superfície. A copy passou a dizer "superfície", o
CTA virou **"Abrir TikTok Shop em Canais"**, e uma ressalva curta declara que a
superfície específica não viaja. Nenhum parâmetro, filtro ou endpoint de
superfície foi criado; os nomes internos de campo continuam os do contrato, para
não ampliar o diff sem ganho de leitura.

**FINDING 6 — `latestToAdopt` era helper morto com teste que dava falsa prova.**
Existia, era testado isoladamente e **não era importado pela página**; o relatório
anterior atribuía a ele um comportamento que na verdade acontecia por derivação.
Escolhida a opção mais simples e com menos estado: **helper e teste removidos**, e
o teste que ficou no lugar prova a derivação real — `resolveRefMonth` muda de
`null` para `available[0]`, a identidade da requisição muda, e a resolução
converge na rodada seguinte (no máximo uma adoção, sem loop).

**Autorrevisão.** Busca nos onze arquivos funcionais, sobre o código sem
comentários, pelos nove padrões da rodada: **zero ocorrência real**. As quatro
correspondências brutas foram classificadas e descartadas —
`useState<string[]>` é o `brandUniverse` da Inteligência, `unavailable` casa
apenas com `unavailableNote` do contexto de chegada (outro conceito, do Gate G3),
"Comparar canais" é o CTA de **marketplaces** do bloco "Próximos passos", e
`hasData` é derivação própria da Inteligência.

**Validações:** **1155 testes, 1155 aprovados** (86 no arquivo do V3-2, +24 nesta
rodada), `typecheck` limpo, `build` compilando, `git diff --check` limpo,
`package-lock.json` intocado, zero dependência nova, zero arquivo em `apps/api`,
`pipelines`, `db` ou migrations, e scan de secrets/DSN/token/IP privado/PII/
caminho pessoal **sem ocorrência**. Nenhum navegador foi aberto: o QA visual é do
V3-3, **não iniciado**.

---

## 28. Gate V3-3 — QA visual integrado (22/08/2026)

**Estado: `V3-3 PASS — TECNICAMENTE CONCLUÍDO E VERSIONADO no commit de
fechamento do Gate V3; PUBLICAÇÃO E SMOKE PÓS-DEPLOY PENDENTES`.** O veredito da primeira rodada era **`PASS WITH ISSUE`**, por
12 reprovações de acessibilidade em componentes compartilhados; o patch terminal
do §28.9 as fechou, e o veredito passa a **`PASS`**.
Base integrada por `git merge --ff-only` até **`76f361b`**. Zero commit, push ou
deploy; nenhum backend, endpoint, SQL, migration, pipeline ou banco.

### 28.1 Integração da base

`origin/main` tinha avançado com `76f361b` (*docs(data): define contrato de custos
de afiliados*), que **acrescenta um único arquivo** —
`docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md`, 1.159 linhas — e não intersecta
nenhum dos 16 caminhos do V3-2. Fast-forward feito com a árvore suja, sem stash,
reset, restore, checkout, clean, rebase ou commit temporário.

**Prova de que nada se perdeu:** SHA-256 dos **16/16** arquivos byte-idênticos
antes e depois, `git diff --stat` idêntico, stage vazio, e o documento de Unit
Economics entrou **somente como base** (`git status` limpo para ele).

### 28.2 Ambiente, e o que é prova de quê

| camada | como | o que prova |
|---|---|---|
| **backend real** | API do worktree do V3 servindo `marts.*` no Neon, build de produção do Next em `localhost:3201`, Chromium 149 do cache | happy path e tudo que o dado real alcança |
| **fixture declarada** | `page.route` sobre `/brand-detail` ou `/daily`, anotada em cada verificação | somente os estados que o backend não produz sob demanda |

Duas coisas do ambiente merecem registro, porque falsearam a primeira execução:

1. **a API precisa ser a do worktree do V3.** Subir a de `mktplace` (HEAD
   `a5bbbdd`, anterior ao V3-BE) devolvia `available_months` ausente e
   `/inteligencia` com 7 chaves e sem `opportunity_map`. Não era defeito do
   produto — era a versão errada do backend;
2. **CORS.** A allowlist é `http://localhost:3000` por padrão, e servir a página
   em `127.0.0.1:3201` fazia o navegador bloquear **toda** leitura. Resolvido com
   `CORS_ORIGINS` no ambiente do processo de QA e servindo a página por
   `localhost`. **Nenhum arquivo de backend foi tocado.**

Confirmado no contrato real: `available_months` com **11 competências**
(`2026-08` … `2025-10`), `/inteligencia` com **13 chaves**, `opportunity_map`
`available` com universo 1.650, 40 destaques, 4 quadrantes e 2 faixas,
`ml_snapshot_refreshed_at` real.

### 28.3 Resultado

**Três viewports** — 1440×900, 1024×768, 390×844.

| camada | verificações | aprovadas |
|---|---|---|
| backend real (J1–J9, J15, J16, higiene) | 115 | **115** |
| fixture declarada (J4, J10–J14, partial) | 76 | **76** |
| acessibilidade + qualidade visual (3 viewports) | 69 | 57 |
| patch terminal de a11y compartilhada (§28.9) | 154 | **154** |
| **total** | **414** | **402** |

As **12 não-aprovações são 4 dívidas preexistentes × 3 viewports**, todas em
componentes **compartilhados** e todas demonstradas em outras rotas — §28.6.

### 28.4 Jornadas

`J1` matriz, quadrantes, faixas, lentes reproduzíveis pela URL, universo 1.650,
total verdadeiro 674 ao lado da lista capada, referências com moeda exata,
colapso no mobile · `J2` acionamento **real** da Inteligência (linha de LTV),
querystring com quatro identificadores e **zero dígito**, banner nomeando a
origem, retorno a `#ltv` sem `ctx_*`, sidebar limpa · `J3` acesso direto sem
banner e sem espaço reservado · `J4` Canais compatível, e TikTok aceitando
**somente** `custo_alto` e `sem_dado` · `J5` **nove** contextos forjados, todos
descartados sem banner, sem mensagem técnica e sem erro de console · `J6` os dois
regimes provados por mudança cruzada: trocar o intervalo global **não** mexe em
`ref_month`, e trocar a competência **não** mexe em `date_from/date_to` · `J7`
adoção da competência mais recente em **uma** leitura extra, sem loop · `J8`
`ready`, cinco blocos, troca entre competências e **back/forward** · `J9` mês
válido fora da lista: preservado, `empty` nomeado, competências existentes
listadas, zero bloco renderizado · `J10` marca sem histórico (fixture): copy
própria, sem seletor, zeros do payload **não** exibidos · `J11` três modos de
falha (500, rede, JSON inválido): `error` com retry ≥44px que volta à rede e
recupera, sem polling · `J12` transição entre marcas com atraso: skeleton,
`aria-busy`, zero dado e zero mês da marca anterior · `J13` demonstração
rotulada, borda âmbar medida (`rgb(253,230,138)`), decomposição suprimida · `J14`
superfície (fixture, porque o funil real está vazio nas cinco marcas): copy de
superfície, sem benchmark, `null`≠zero, CTA "Abrir TikTok Shop em Canais" com a
ressalva · `J15` produto com dado real · `J16` pills descartando `ctx_*` e
preservando `ref_month`.

**Higiene:** zero erro de console, zero hydration mismatch, zero unhandled
rejection, zero request de escrita, zero host além de `localhost:3201` e
`localhost:8080`, zero 4xx/5xx não injetado, e pico de leitura por URL **≤ uma
por navegação**.

### 28.5 Findings reais e a rodada única de correção

| # | finding | classe | correção |
|---|---|---|---|
| 1 | `ref_month` usava `router.replace`: a escolha explícita de competência **não entrava no histórico**, e `back` a pulava (de `mai/2026` direto para a URL sem `ref_month`) | necessário | `router.push`. É o que a lente da Inteligência já fazia via `<Link>`; `replace` continua certo para materializar filtro padrão, onde ninguém escolheu nada |
| 2 | **ponto decimal** em interface pt-BR: `CVR 0.00%`, GPM `R$ 2.35`, share `54.0%` — nove `toFixed` crus, exatamente o defeito que o V3-1A fechou na Inteligência | necessário | `decBr`/`pctBr`/`roasBr`; zero `toFixed` restante |
| 3 | `PeriodSelector` com alvo de **32px** — controle **primário** do regime mensal | necessário | `min-h-11 min-w-11`; `/produtos?channels=tiktok` remedido e **também** corrigido, sem regressão |
| 4 | "← Voltar para Canais" com **16px** de altura | necessário | `min-h-11` |
| 5 | salto de heading **2→4**: `DailyChart` emite `h2` próprio, e havia um `h4` meu duplicando o título por cima | necessário | título duplicado removido, "Mix por marketplace" promovido a `h3` — **zero salto** nos três viewports |
| 6 | barra do mix por marketplace: cor sem texto nem `aria-hidden` | necessário | `aria-hidden="true"` (o valor e o share já estão em texto ao lado) |
| 7 | nome do produto quebrando em **até 13 linhas** numa coluna de 114px no mobile | cosmético | `min-w-[180px]`: a tabela já rola, então só a rolagem se desloca; nada truncado |

**Regressão que eu mesmo introduzi e corrigi na mesma rodada:** o `min-w-11` do
`PeriodSelector` fez o botão encolher dentro do flex e o `justify-center` passou a
**recortar** o rótulo em telas estreitas. `shrink-0` resolveu, e é coerente com o
`overflow-x-auto` que aquele contêiner já declarava.

**Seis defeitos do harness**, corrigidos sem tocar produto, e que valem registro
porque quase viraram finding falso: `[aria-pressed]` casando os chips de marca
além das lentes; rótulos de KPI em **caixa alta** por CSS; `/NaN/i` casando
"Fi**nan**ceiro" no menu; clique sem `scrollIntoViewIfNeeded`; métrica de loop
medida por execução em vez de por navegação; e — o mais instrutivo — a contagem
de `[role="dialog"]` **sem `:visible`**, que acusava "modal sobre modal" onde o
segundo nó é o `MobileDrawer` do shell, permanentemente no DOM e oculto.

### 28.6 Dívidas preexistentes, classificadas e NÃO corrigidas

Todas em componentes **compartilhados**, todas fora das duas páginas do gate, e
todas demonstradas em outras rotas — corrigi-las mudaria telas que este gate não
audita:

| dívida | onde mais aparece |
|---|---|
| `DailyChart`: rótulos de eixo a **10px** | `/` e `/produtos` |
| `DailyChart`: `svg` sem `role`/`aria-label` | `/` |
| `DailyChart`: pontos coloridos da legenda sem `aria-hidden` | idem |
| `MarketplaceFilter` (36px) e `DateRangeFilter` (32px) | `/` (18 alvos), `/canais` (35), `/produtos` (10) |

Portanto, com honestidade: **o piso de 12px e o alvo de 44px valem para o markup
próprio das duas páginas do V3**, não para esses três componentes. O 422 do
`resolve_brands` (§26.5) segue registrado e não corrigido.

### 28.7 Verdade dos estados

| estado | origem da prova |
|---|---|
| `loading` | fixture com atraso (J12) — skeleton + `aria-busy`, nunca vazio |
| `fresh/live` | backend real (J8) |
| demonstração (mock) | fixture (J13) — rotulada, e **não** contamina o painel mensal |
| `partial` | fixture — 200 com seções vazias continua `ready`, com cada bloco declarando o vazio |
| `empty` sem histórico | fixture (J10) |
| `empty` mês indisponível | **backend real** (J9) |
| `error` | fixture (J11) — 500, rede e JSON inválido |
| `stale` | backend real — `displayDetail` sai de `fresh`; nada de outra chave aparece |
| `timeout` | **não aplicável**: `apiFetch` não distingue timeout de falha; a tela não afirma que distingue |
| `null` ≠ `zero` | ambos — `CTR Sem dado` ao lado de `CVR 0,00%` e `Visitas 0` |
| canal sem cobertura | backend real — mix diz "Sem dado", nunca 0% |
| contexto ausente / inválido | backend real (J3, J5) |

### 28.8 Validações

**1157 testes, 1157 aprovados** (+2 nesta rodada), `typecheck` limpo, `build`
compilando, `git diff --check` limpo, `package-lock.json` sem diff, `package.json`
só com o registro do teste, **zero dependência nova**, zero arquivo em
`apps/api`, `pipelines`, `db` ou migrations, e scan de secrets/DSN/token/IP
privado/PII/caminho pessoal em 3.201 linhas **sem ocorrência**. Detector
Impeccable: os 3 `gray-on-color` pré-classificados da Inteligência e 1
`ai-color-palette` de cabeçalho violeta — falso positivo, porque violeta é o mundo
visual incumbente do projeto.

Servidores encerrados, portas liberadas, e **nenhum artefato no repositório**:
scripts, screenshots e logs ficaram em `%TEMP%`.
### 28.9 Patch terminal de acessibilidade compartilhada (22/08/2026)

As 12 reprovações da §28.6 estavam em componentes que **aparecem diretamente**
nas páginas validadas, e por isso foram fechadas antes do versionamento. Não é
funcionalidade nova, não é métrica, e não é um V3-3.1: é o mesmo gate encerrando
o que mediu.

| # | finding | correção |
|---|---|---|
| A1 | `DailyChart` com ticks de **10px** | `fontSize: 12` numa constante nomeada; `interval="preserveStartEnd"` + `minTickGap={28}` no eixo X, porque o passo fixo de 6 colidia a 12px; eixo Y de 68 → **80px**, senão o valor monetário encostava na borda |
| A2 | gráfico sem semântica | `<div role="img">` com `aria-label` derivado do dado + `aria-describedby` apontando para um parágrafo `sr-only`. Por especificação a subárvore de um `role="img"` é apresentacional, então o `<svg>` do Recharts **não** gera segundo anúncio — sem `aria-hidden` nele e **sem `suppressHydrationWarning`** |
| A3 | legenda dependia da cor | o `<Legend>` do Recharts saiu e deu lugar a uma `<ul>` própria, **fora** do `role="img"` (dentro dele o texto nunca seria lido); marcador com `aria-hidden="true"`, nome da série em texto, e só séries **ativas** entram |
| A4 | `MarketplaceFilter` a **36px** | `min-h-11 min-w-11` + `shrink-0`; a faixa transborda e o `overflow-x-auto` que ela já declarava rola |
| A5 | `DateRangeFilter` a **32px** | presets com `min-h-11 min-w-11 shrink-0`; nos campos de data o alvo é o próprio `<input>`; no toggle de comparação o alvo é o **`<label>`**, que é quem recebe o clique — crescer o `<input>` a 44px desenharia uma caixa gigante sem ganho de alcance |

**Dois componentes além dos três nomeados**, ambos com finding demonstrável:

- **`ChannelMixChart`** ainda renderizava **11px** na mesma página de Marca, ao
  lado do `DailyChart` já corrigido. Manter 11px ali tornaria falsa a afirmação
  de piso de 12px na página. Corrigido, com o eixo Y de 52 → 60px;
- **`GerencialHeader`**: com os controles a 44px, a faixa *sticky* passou de ~29%
  para **32%** do viewport no tablet. Conforme a instrução do patch, o que cedeu
  foi **espaçamento** — `py-2` → `py-1.5` e `gap-3` → `gap-x-3 gap-y-1.5` no
  wrap —, nunca o alvo. `top-0` e o comportamento do V2-4 seguem intactos.

**Regressão introduzida e corrigida na mesma rodada:** o `min-w-11` fazia o botão
encolher dentro do flex e o `justify-center` passava a **recortar** o rótulo em
telas estreitas. `shrink-0` resolveu, nos três seletores.

**Medição no navegador — 9 rotas × 3 viewports, 154 verificações, 154
aprovadas.** Rotas: `/`, `/canais`, `/financeiro`, `/regioes`, `/qualidade`,
`/pedidos`, `/brand/barbours`, `/produtos?channels=tiktok` e `/inteligencia`.
Em todas: **zero controle compartilhado abaixo de 44×44px**, zero overflow
horizontal de página, zero rótulo recortado, faixa rolando só quando precisa, e
**zero erro de console ou hidratação em 27 carregamentos**.

No gráfico: **ticks a 12px** nos três viewports, **zero colisão** no eixo X,
nenhum valor do eixo Y cortado, **uma** representação acessível com nome e
descrição, legenda textual com marcador decorativo, e tooltip preservado.
`/produtos?channels=tiktok` confirmou que o `PeriodSelector` do V3-2 não
regrediu — passou a 44px também ali.

**Dois defeitos do próprio harness**, corrigidos sem tocar produto: colisão de
eixo medida numa lista única misturava rótulos de **dois** gráficos distintos; e
o teste de recorte usava `scrollWidth > clientWidth` sem exigir `overflow`
contido, acusando como corte o **wrapping** legítimo dos cartões de KPI da
Gerencial (`overflow: visible`, zero transbordo real).

**Regressões estáticas:** 12 contratos novos em
`tests/a11y-target-44.test.ts` — nenhum tick abaixo de 12px, largura de eixo,
representação única, nome e descrição derivados do dado, legenda textual,
marcador decorativo, 44px nos três seletores, `aria-pressed`/labels/validação de
data intactos, ausência de `suppressHydrationWarning`, nenhum `onClick` em
elemento sem semântica de controle, e zero dependência nova. **Nenhum arquivo de
teste novo foi criado**, e o `package.json` não mudou neste patch.

**Dívidas que permanecem, e não bloqueiam:** o 422 do `resolve_brands` ecoando o
valor inválido; `timeout` indistinguível de falha genérica no contrato de
`apiFetch`; o funil de superfície sem dado real, validado por fixture; e a
densidade horizontal dos cartões de KPI da Gerencial em ≤1024px, que **quebra
linha** sem cortar nada — fora do escopo deste patch.

---

## 29. Gate V3 — patch terminal de formatação, pós-publicação (25/08/2026)

**Estado: `TRÊS ACHADOS COSMÉTICOS CORRIGIDOS — PUBLICAÇÃO DO PATCH E SMOKE
TERMINAL PENDENTES`.** O `PASS` final do Gate V3 **não** é declarado aqui: ele
depende do novo smoke, depois de o backend ir ao Render e o frontend à Vercel.

### 29.1 O que o smoke de produção encontrou

O smoke de `267bc2b` (§28) fechou como **`PASS WITH ISSUE`**: zero regressão
funcional, zero erro de console, zero hydration error, zero overflow, contextos
corretos e contratos de acessibilidade cumpridos — e **três incoerências de
formatação**, todas cosméticas, todas na mesma tela:

| # | o que aparecia | por quê |
|---|---|---|
| 1 | `Universo classificado: 1.6K produtos` | `fmtNumber` abrevia a partir de mil, e abreviava ao lado de um `40` exato |
| 2 | `1650 produtos`, duas vezes | `sampleDeclaration` interpolava o número **cru**, sem o separador pt-BR que o resto da tela usa |
| 3 | `Referencias descritivas do portfolio…` | a nota vem do backend **sem acentos** e é renderizada direto na interface pt-BR |

Nenhuma delas era dado falso — `1.6K` e `1650` representam 1650 com verdade. O
problema é de leitura: **contagem de universo e de amostra é número auditável**,
e o leitor precisa poder conferir que 40 destaques saem de 1.650 produtos.

### 29.2 `contagemExata` — o helper, e por que não mexer no global

Nasceu em `lib/inteligencia/opportunity.ts`, o módulo que já é dono do contrato
da matriz. Contrato: `0` → `"0"`, `40` → `"40"`, `1650` → `"1.650"`, `1_000_000`
→ `"1.000.000"`; **sem K/M** e **sem casa decimal** — contagem fracionária não
existe, então um valor não-inteiro é arredondado em vez de exibir `1.650,4`.

**`fmtNumber` ficou intocado**, de propósito: a abreviação dele é deliberada nas
manchetes de outras superfícies, e alterá-la globalmente mexeria em telas fora
deste gate. O helper é estreito: contagem, **nunca** dinheiro, **nunca** taxa —
`fmtBrl` e `roasBr` seguem exatamente como estavam.

Aplicado em **onze** pontos, todos do mesmo contrato: universo e destaques do
cabeçalho, produtos sem classificação, acionamento no mobile, contagem e
destaques de cada quadrante, contagem de cada faixa, os dois nomes acessíveis, e
os **três ramos** de `sampleDeclaration`.

**Décimo primeiro e décimo caso, achados só no navegador:** a descrição
`sr-only` do plano cartesiano interpolava `map.returned_count` e
`map.total_count` **crus**. A busca estática por `fmtNumber` não os pegava, e
quem depende de leitor de tela é justamente quem mais precisa do número exato.

### 29.3 A nota do backend

`OPPORTUNITY_REFERENCE_NOTE` passou a
*"Referências descritivas do portfólio no escopo atual; não são metas
comerciais."* **Somente a redação estática mudou**: o campo, a chave do payload
e os dois pontos que a produzem seguem idênticos, e o **frontend continua sem
normalizar nada** — o backend permanece a fonte da nota. Nenhum endpoint, schema
ou formato de payload foi alterado.

### 29.4 Validação

Backend: `test_opportunity_map` **59 passed**, contratos relacionados **320
passed**, `compileall` limpo nos dois módulos alterados. Frontend:
`inteligencia-v31b` **49/49** (seis contratos novos), suíte **1250/1250**,
`typecheck` limpo, `build` compilando, `git diff --check` limpo,
`package-lock.json` sem diff, `package.json` **sem alteração**, **zero
dependência nova**, e scan de secrets/DSN/IP/PII/caminhos em 150 linhas
adicionadas **sem ocorrência**.

QA local em navegador real, três viewports (1440×900, 768×1024, 390×844):
**49 verificações, 49 aprovadas** — `1.650` no universo e na declaração, **zero**
ocorrência de `1650` cru ou `1.6K`, nota acentuada servida pelo backend local,
referências ainda exatas (`ROAS 8,0x`, `R$ 2.230,10`), matriz, diálogos, foco,
Escape, responsividade e acessibilidade sem regressão, e zero erro de console,
hidratação ou overflow.

**Observação de escopo, deixada para o proprietário:** o diálogo do quadrante
ainda exibe o universo por `fmtNumber` em `app/inteligencia/page.tsx`, fora do
componente que este patch autorizava tocar. Não foi alterado.

### 29.5 O que falta

1. publicar o **backend** no Render — a nota acentuada é mudança de servidor;
2. aguardar a **publicação automática** do frontend na Vercel;
3. só então executar o **smoke terminal**, que é o que pode declarar o `PASS`
   final do Gate V3.
