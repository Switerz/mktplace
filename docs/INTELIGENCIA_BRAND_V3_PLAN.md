# Gate V3 — Inteligência e Marca 360 (desenho)

**Status:** **V3-0 CONCLUÍDO — DESENHO APROVADO.** Próximo marco: **V3-1A**, que
**ainda não foi iniciado**. **Nenhuma implementação foi realizada neste gate** — nem
frontend, nem backend, nem migration, nem pipeline.
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
etapas porque a matriz definitiva **não é implementável** com o payload atual — a
divisão reflete uma dependência real, não um subgate para inflar processo.

| Fase | Conteúdo | Depende de | Critério de saída |
|---|---|---|---|
| **V3-0** | Este desenho, corrigido nos sete findings | — | **CONCLUÍDO — DESENHO APROVADO** pelo proprietário em 18/08/2026 |
| **V3-1A** — fundação da Inteligência | Cabeçalho de regimes (ML fotografia × TikTok 30 dias); **rituária** derivada do payload; remoção das duas falsas affordances (`<span>` de botão e hover de linha); concentração Pareto com diálogo **só de agregados** + CTA para `/produtos`; **wiring da querystring canônica `/produtos?channels=ml&brands=…&pareto_bucket=…` (§9.2)** e de `lens` em `/inteligencia`; listas e fila honestamente truncadas ("amostra capada"); fila com lentes substituindo as três tabelas; LTV filtrável; os 13 estados; tipografia ≥12px; bloco 3 **como listas/faixas**, sem matriz | **payload atual** | 5 tabelas → 1; zero falsa affordance; rituária visível; nenhuma contagem apresentada como total; `/produtos` reproduzível por URL na forma canônica; `lens` com allowlist e sem propagação pela sidebar; suíte + typecheck + build verdes |
| **contrato backend** | **BE6** `opportunity_map` — universo + `total_count` + as duas referências com origem + quadrantes com fronteiras + agregados completos + destaques com `item_id` e `returned_count` + **as duas faixas** (§15.1); **BE3** contagens verdadeiras; **BE4** frescor do snapshot; **BE5** `available_months` (§15.2). **BE1 e BE2 saíram** (§15.0) | — | contratos aceitos e servidos; nenhum campo entregue sem consumidor |
| **V3-1B** — conclusão da Inteligência | **Matriz 2×2 definitiva**: quatro quadrantes com fronteiras `>=` explícitas, as duas referências exibidas com origem, **agregados do universo separados dos pontos de destaque**, e as **duas faixas** (`sem_ads` e `roas_indisponivel_com_investimento`) como blocos distintos; contagens verdadeiras substituindo "ao menos N"; etiqueta de frescor com timestamp | **BE6** (matriz), BE3, BE4 | nenhuma afirmação de cobertura sem `total_count`; nenhum eixo derivado de `product_status`; pontos truncados **nunca** rotulados como "todos os produtos"; desperdício com Ads visível na sua própria faixa |
| **V3-2** — Marca 360 | Contêiner de regime mensal cercado + nota de não-sobreposição; `ctx_from=inteligencia` com `ctx_focus` próprio (§9.1); remoção de Demographics; **competências reais** via `available_months`; sinalização de mock no cabeçalho do bloco; guarda de identidade do `brandDetail`; drill-downs de canal e produto **sem benchmark entre superfícies** | V3-1A/B (produtor do contexto) **e obrigatoriamente BE5** | dois regimes inequívocos; nenhuma seção sem etiqueta de período; **zero import de módulo mock**; volta à evidência reconstruindo marca + foco, sem repropagar `ctx_*` |
| **V3-3** — QA integrado | a11y (foco, nome acessível, contraste, ≥12px), responsivo em desktop/tablet/mobile, os 13 estados, matriz de drill-down acionamento a acionamento, regressões | V3-1, V3-2 | zero scroll horizontal de página; zero elemento clicável sem affordance e sem nome acessível; zero par de blocos com vazio desproporcional |

**Dependências que não podem ser contornadas:** a matriz sem BE6 não existe (degrada
para listas, §7.3.1); a Marca sem BE5 não pode ser aceita como "competências sem mock".

**V3-1A não foi iniciado.** O desenho está aprovado e versionado; a implementação começa
numa task própria. Nada além deste documento foi produzido no V3-0.
